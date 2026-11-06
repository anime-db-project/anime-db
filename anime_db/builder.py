"""Deterministic, audited SQLite distribution build from source snapshots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zlib
from pathlib import Path
from contextlib import closing
from typing import Any

import zstandard

from anime_db.crawler import read_snapshot
from anime_db.providers import SOURCES
from anime_db.normalizer import compact, ingest_record
from anime_db.schema import LOCALES, SCALAR_FIELDS, SCHEMA_VERSION, create_catalog
from anime_db.schema import create_images, create_technical

CATALOG_TABLES = (
    "anime_external_ids", "anime_sources", "anime_titles", "anime_descriptions",
    "anime_genres", "anime_tags", "anime_studios", "anime_characters",
    "anime_staff", "anime_relations", "anime_episodes", "anime_images",
    "anime_videos", "anime_links", "anime_source_attributes",
    *SCALAR_FIELDS,
)


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def audit_db(conn: sqlite3.Connection) -> None:
    check = conn.execute("PRAGMA integrity_check").fetchone()
    if check != ("ok",):
        raise RuntimeError("SQLite integrity check failed")
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError(f"SQLite foreign key error: {problems[:3]}")


def finalize_db(conn: sqlite3.Connection) -> None:
    conn.execute("ANALYZE")
    conn.commit()
    conn.execute("VACUUM")
    audit_db(conn)
    conn.close()


def populate_search(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO anime_title_fts(value,locale,adb_id) "
        "SELECT t.value,t.locale,a.adb_id FROM anime_titles AS t "
        "JOIN anime AS a ON a.anime_id=t.anime_id"
    )


def aggregate(full: sqlite3.Connection, tech: sqlite3.Connection) -> None:
    """Keep all candidate values; only select when a field is unambiguous."""
    for table in ("anime_statuses", "anime_formats", "anime_episode_counts",
                  "anime_durations", "anime_dates", "anime_scores", "anime_statistics"):
        columns = [entry[1] for entry in full.execute(f"PRAGMA table_info({table})")]
        payload_cols = [x for x in columns if x not in ("anime_id", "source_id")]
        selected = ", ".join(payload_cols)
        rows = full.execute(
            f"SELECT anime_id, source_id, {selected} FROM {table} ORDER BY anime_id, source_id"
        )
        groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in rows:
            anime_id, source_id, *values = row
            kind = str(values[0]) if table not in ("anime_statuses", "anime_formats") else ""
            field_name = f"{table}.{kind}" if kind else table
            obj = dict(zip(payload_cols, values))
            groups.setdefault((int(anime_id), field_name), []).append({
                "source_id": source_id, "value": obj
            })
        for (anime_id, field_name), candidates in groups.items():
            values = {compact(c["value"]): c["value"] for c in candidates}
            conflict = len(values) > 1
            selected_value = compact(next(iter(values.values()))) if not conflict else None
            full.execute(
                "INSERT INTO aggregation_summary(anime_id,field_name,distinct_values,selected_json,has_conflict) "
                "VALUES(?,?,?,?,?)",
                (anime_id, field_name, len(values), selected_value, int(conflict)),
            )
            adb_id = full.execute("SELECT adb_id FROM anime WHERE anime_id=?", (anime_id,)).fetchone()[0]
            tech.execute(
                "INSERT INTO aggregation_decisions(adb_id,field_name,value_json,conflict,candidates_json) "
                "VALUES(?,?,?,?,?)",
                (adb_id, field_name, selected_value, int(conflict), compact(candidates)),
            )


def _clone_catalog(
    src_path: Path,
    dst_path: Path,
    role: str,
    *,
    source: str | None = None,
    locale: str | None = None,
) -> None:
    with closing(open_db(dst_path)) as target:
        create_catalog(target, role=role)
        target.execute("ATTACH DATABASE ? AS original", (str(src_path.resolve()),))
        sid = SOURCES.index(source) + 1 if source else None
        if sid is not None:
            # Only titles actually fetched from this particular provider.
            target.execute(
                "INSERT INTO anime SELECT a.* FROM original.anime AS a "
                "WHERE EXISTS (SELECT 1 FROM original.anime_sources AS s "
                "WHERE s.anime_id=a.anime_id AND s.source_id=?)", (sid,),
            )
        else:
            target.execute("INSERT INTO anime SELECT * FROM original.anime")
        target.execute("INSERT OR IGNORE INTO id_redirects SELECT * FROM original.id_redirects")
        target.execute("INSERT OR IGNORE INTO amdb_redirects SELECT * FROM original.amdb_redirects")
        target.execute(
            "INSERT INTO anime_external_ids SELECT e.* FROM original.anime_external_ids AS e "
            "WHERE e.anime_id IN (SELECT anime_id FROM anime)"
        )
        for table in CATALOG_TABLES:
            if table == "anime_external_ids":
                continue
            query = (
                f"INSERT INTO {table} SELECT item.* FROM original.{table} AS item "
                "WHERE item.anime_id IN (SELECT anime_id FROM anime)"
            )
            params: tuple = ()
            if sid is not None:
                query += " AND item.source_id=?"
                params = (sid,)
            if locale is not None:
                # Language packages contain complete identity & source reference,
                # but no arbitrary-language descriptions or unrelated metadata.
                if table in ("anime_titles", "anime_descriptions"):
                    query += " AND item.locale IN (?, 'und')"
                    params = (locale,)
                elif table not in ("anime_sources",):
                    continue
            target.execute(query, params)
        if locale is None and sid is None:
            target.execute(
                "INSERT INTO aggregation_summary SELECT * FROM original.aggregation_summary"
            )
        target.commit()
        target.execute("DETACH DATABASE original")
        populate_search(target)
        finalize_db(target)


def build_from_state(state_dir: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    full_path = output_dir / "anime.db"
    tech_path = output_dir / "anime.technical.db"
    images_path = output_dir / "anime.images.db"
    total = 0
    with closing(open_db(full_path)) as full, closing(open_db(tech_path)) as tech:
        create_catalog(full, role="full")
        create_technical(tech)
        snapshots = sorted((state_dir / "snapshots").glob("*/*.json.zlib"))
        for path in snapshots:
            item = read_snapshot(path)
            if item.get("source") not in SOURCES:
                continue
            anime_id = ingest_record(full, item)
            sid = SOURCES.index(item["source"]) + 1
            full.execute(
                "INSERT OR REPLACE INTO anime_sources(anime_id,source_id,source_external_id,fetched_at) "
                "VALUES(?,?,?,?)",
                (anime_id, sid, item["source_external_id"], item["fetched_at"]),
            )
            raw = compact(item["payload"]).encode("utf-8")
            tech.execute(
                "INSERT INTO raw_snapshots(source_key,external_id,adb_id,sha256,fetched_at,raw_zlib) "
                "VALUES(?,?,?,?,?,?)",
                (item["source"], item["source_external_id"], item["adb_id"],
                 hashlib.sha256(raw).hexdigest(), item["fetched_at"], zlib.compress(raw, 9)),
            )
            total += 1
        crawl_report = state_dir / "last-crawl-report.json"
        if crawl_report.is_file():
            report = json.loads(crawl_report.read_text("utf-8"))
            for event in report.get("requests", []):
                tech.execute(
                    "INSERT INTO request_log(source_key,external_id,status_code,elapsed_ms,detail,recorded_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (event["source"], str(event["id"]), int(event["status"]),
                     int(event["elapsed_ms"]), str(event.get("error", "")), event["at"]),
                )
        aggregate(full, tech)
        populate_search(full)
        full.commit()
        tech.commit()
        titles = full.execute("SELECT COUNT(*) FROM anime").fetchone()[0]
        conflicts = full.execute(
            "SELECT COUNT(*) FROM aggregation_summary WHERE has_conflict=1"
        ).fetchone()[0]
        finalize_db(full)
        finalize_db(tech)
    with closing(open_db(images_path)) as images:
        create_images(images)
        finalize_db(images)

    for lang in LOCALES:
        _clone_catalog(
            full_path, output_dir / f"anime.{lang}.db",
            role=f"language:{lang}", locale=lang,
        )
    for source in SOURCES:
        _clone_catalog(
            full_path, output_dir / f"anime.{source}.db",
            role=f"source:{source}", source=source,
        )

    entries = {}
    database_paths = sorted(output_dir.glob("*.db"))
    compressor = zstandard.ZstdCompressor(level=8, threads=1)
    for path in database_paths:
        compressed = path.with_suffix(".db.zst")
        with path.open("rb") as fin, compressed.open("wb") as fout:
            compressor.copy_stream(fin, fout)
        entries[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": hash_file(path),
            "zst_bytes": compressed.stat().st_size,
            "zst_sha256": hash_file(compressed),
        }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "release_enabled": False,
        "snapshots": total,
        "anime": titles,
        "conflicts": conflicts,
        "sources": list(SOURCES),
        "languages": list(LOCALES),
        "build_seconds": round(time.perf_counter() - started, 3),
        "files": entries,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        "utf-8",
    )
    sums = [
        f"{hash_file(path)}  {path.name}"
        for path in sorted(output_dir.iterdir()) if path.is_file() and path.name != "SHA256SUMS"
    ]
    (output_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n", "utf-8")
    return manifest
