"""Loss-preserving normalization of API payloads into semantic SQLite tables."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import urlparse

from anime_db.providers import SOURCES


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plain(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def url(value: Any) -> str:
    value = plain(value)
    return value if urlparse(value).scheme in ("http", "https") else ""


def normalized_locale(value: str) -> str:
    lang = plain(value).replace("_", "-").lower()
    return {"en-jp": "ja-Latn", "ja-latn": "ja-Latn", "ja-jp": "ja", "en-us": "en"}.get(
        lang, lang or "und"
    )


def fuzzy_date(obj: dict[str, Any] | None) -> tuple[str, str] | None:
    if not isinstance(obj, dict) or not obj.get("year"):
        return None
    parts = [str(obj["year"]).zfill(4)]
    if obj.get("month"):
        parts.append(str(obj["month"]).zfill(2))
    if obj.get("day") and len(parts) == 2:
        parts.append(str(obj["day"]).zfill(2))
    return "-".join(parts), (
        "day" if len(parts) == 3 else "month" if len(parts) == 2 else "year"
    )


def ingest_record(conn: sqlite3.Connection, item: dict) -> int:
    source = item["source"]
    if source not in SOURCES:
        raise ValueError(f"Unknown metadata provider: {source}")
    source_id = SOURCES.index(source) + 1
    adb_id = int(item["adb_id"])
    amdb_id = int(item["amdb_id"]) if item.get("amdb_id") else None
    conn.execute(
        "INSERT OR IGNORE INTO anime(adb_id,amdb_id) VALUES (?,?)",
        (adb_id, amdb_id),
    )
    conn.execute(
        "UPDATE anime SET amdb_id=COALESCE(amdb_id, ?) WHERE adb_id=?",
        (amdb_id, adb_id),
    )
    row = conn.execute(
        "SELECT anime_id FROM anime WHERE adb_id=?", (adb_id,)
    ).fetchone()
    if row is None:
        raise ValueError("ADB ID insertion failed")
    anime_id = int(row[0])

    def insert(table: str, **cols: Any) -> None:
        keys = ("anime_id", "source_id", *cols.keys())
        values = (anime_id, source_id, *cols.values())
        placeholders = ",".join("?" for _ in values)
        conn.execute(
            f"INSERT OR IGNORE INTO {table}({','.join(keys)}) VALUES({placeholders})",
            values,
        )

    external_refs = dict(item.get("external_ids") or {})
    external_refs[source] = str(item["source_external_id"])
    for provider, external_id in external_refs.items():
        if not provider or not str(external_id).strip():
            continue
        # Every provider in the AMDB anchor's mapping is copied as a compact
        # (provider, external_id) reference, never as a mapping graph.
        conn.execute(
            "INSERT OR IGNORE INTO anime_external_ids(anime_id,provider,external_id) "
            "VALUES(?,?,?)",
            (anime_id, str(provider), str(external_id)),
        )
        owner = conn.execute(
            "SELECT anime_id FROM anime_external_ids "
            "WHERE provider=? AND external_id=?",
            (str(provider), str(external_id)),
        ).fetchone()
        if owner is None or int(owner[0]) != anime_id:
            raise ValueError(
                f"Conflicting external identity {provider}:{external_id} "
                "belongs to another ADB record"
            )

    def title(locale: str, role: str, field: str, value: Any) -> None:
        # Several Shikimori localized fields are arrays, not scalar strings.
        # Never stringify the whole list as one title.
        options = value if isinstance(value, list) else [value]
        for option in options:
            if isinstance(option, str) and option.strip():
                insert(
                    "anime_titles",
                    locale=normalized_locale(locale),
                    title_type=role,
                    source_field=field,
                    value=option.strip(),
                )

    def description(locale: str, role: str, field: str, value: Any) -> None:
        val = plain(value)
        if val:
            insert(
                "anime_descriptions",
                locale=normalized_locale(locale),
                description_type=role,
                source_field=field,
                value=val,
            )

    def enum(table: str, field: str, value: Any) -> None:
        val = plain(value)
        if val:
            insert(table, **{field: val})

    def episodes(kind: str, value: Any) -> None:
        if (
            value is not None
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            if int(value) >= 0:
                insert("anime_episode_counts", count_type=kind, value=int(value))

    def duration(kind: str, value: Any, unit: int) -> None:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            insert("anime_durations", duration_type=kind, seconds=int(value * unit))

    def score(kind: str, value: Any, scale: float) -> None:
        if isinstance(value, (float, int, str)) and not isinstance(value, bool):
            try:
                number = float(value)
            except ValueError:
                return
            if 0 <= number <= scale:
                insert("anime_scores", score_type=kind, value=number, scale=scale)

    def metric(kind: str, value: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            insert("anime_statistics", metric=kind, value=float(value))

    def date(kind: str, value: Any) -> None:
        if isinstance(value, dict):
            parts = fuzzy_date(value)
            if parts:
                insert(
                    "anime_dates", date_type=kind, value=parts[0], precision=parts[1]
                )
        elif plain(value):
            text = plain(value)
            insert(
                "anime_dates",
                date_type=kind,
                value=text,
                precision="day" if len(text) >= 10 else "unknown",
            )

    def genres(items: Any) -> None:
        for node in items if isinstance(items, list) else []:
            if isinstance(node, dict):
                name = plain(node.get("name"))
                term_kind = plain(node.get("kind")) or "genre"
                identifier = plain(node.get("id")) or name
                if name:
                    if term_kind == "genre":
                        insert("anime_genres", genre=name)
                    else:
                        insert(
                            "anime_tags",
                            tag=name,
                            rank=None,
                            is_spoiler=None,
                        )
                    insert(
                        "anime_taxonomy_names",
                        source_term_id=identifier,
                        term_kind=term_kind,
                        locale="en",
                        source_field="name",
                        name=name,
                    )
                translated = plain(node.get("russian"))
                if translated:
                    insert(
                        "anime_taxonomy_names",
                        source_term_id=identifier,
                        term_kind=term_kind,
                        locale="ru",
                        source_field="russian",
                        name=translated,
                    )
            else:
                name = plain(node)
                if name:
                    insert("anime_genres", genre=name)

    def image(image_type: str, value: Any) -> None:
        if isinstance(value, dict):
            for size, link in value.items():
                if url(link):
                    insert(
                        "anime_images",
                        image_type=f"{image_type}.{size}",
                        url=url(link),
                        width=None,
                        height=None,
                    )
        elif url(value):
            insert(
                "anime_images",
                image_type=image_type,
                url=url(value),
                width=None,
                height=None,
            )

    def studio(name: str, ext: str, role: str) -> None:
        if name:
            insert(
                "anime_studios",
                name=name,
                studio_external_id=ext or name,
                studio_role=role,
            )

    def person(table: str, ext: str, name: str, role: str, node: Any) -> None:
        if not ext and not name:
            return
        fields = (
            {"character_external_id": ext or name}
            if table == "anime_characters"
            else {"person_external_id": ext or name}
        )
        insert(
            table,
            **fields,
            name=name,
            role=role,
            details_json=compact(node) if node else None,
        )

    def link(site: str, value: Any) -> None:
        if url(value):
            insert("anime_links", site=site, url=url(value))

    data = item["payload"]
    if not isinstance(data, dict):
        raise ValueError("Expected source object")
    attrs = data.get("attributes", {}) if source == "kitsu" else data
    if not isinstance(attrs, dict):
        attrs = {}

    used = set()

    def get(field: str) -> Any:
        used.add(field)
        return attrs.get(field)

    if source == "shikimori":
        title("ja-Latn", "romaji", "name", get("name"))
        title("ru", "localized", "russian", get("russian"))
        title("ja", "native", "japanese", get("japanese"))
        title("en", "localized", "english", get("english"))
        for alias in get("synonyms") or []:
            title("und", "alias", "synonyms", alias)
        description("ru", "synopsis", "description", get("description"))
        description("ru", "html", "description_html", get("description_html"))
        enum("anime_statuses", "status", get("status"))
        enum("anime_formats", "format", get("kind"))
        episodes("total", get("episodes"))
        episodes("aired", get("episodes_aired"))
        duration("episode", get("duration"), 60)
        date("aired_start", get("aired_on"))
        date("aired_end", get("released_on"))
        score("average", get("score"), 10)
        metric("scored_by", get("scored_by"))
        genres(get("genres"))
        for node in get("studios") or []:
            if isinstance(node, dict):
                studio(plain(node.get("name")), plain(node.get("id")), "studio")
        image("poster", get("poster"))
        image("image", get("image"))
        for node in get("videos") or []:
            if isinstance(node, dict) and url(node.get("url")):
                insert(
                    "anime_videos",
                    video_type=plain(node.get("kind")) or "video",
                    url=url(node["url"]),
                    title=plain(node.get("name")),
                )
        link("shikimori", get("url"))
    elif source == "kitsu":
        titles = get("titles") or {}
        if isinstance(titles, dict):
            for lang, val in titles.items():
                title(lang, "localized", f"titles.{lang}", val)
        title("und", "canonical", "canonicalTitle", get("canonicalTitle"))
        for alias in get("abbreviatedTitles") or []:
            title("und", "alias", "abbreviatedTitles", alias)
        description("en", "synopsis", "synopsis", get("synopsis"))
        description("en", "description", "description", get("description"))
        enum("anime_statuses", "status", get("status"))
        enum("anime_formats", "format", get("subtype"))
        episodes("total", get("episodeCount"))
        duration("episode", get("episodeLength"), 60)
        date("aired_start", get("startDate"))
        date("aired_end", get("endDate"))
        score("average", get("averageRating"), 100)
        for k in (
            "userCount",
            "favoritesCount",
            "popularityRank",
            "ratingRank",
            "trendingRank",
        ):
            metric(k, get(k))
        image("poster", get("posterImage"))
        image("cover", get("coverImage"))
        if get("youtubeVideoId"):
            token = plain(attrs["youtubeVideoId"])
            if token and all(c.isalnum() or c in "_-" for c in token):
                insert(
                    "anime_videos",
                    video_type="trailer",
                    url="https://www.youtube.com/watch?v=" + token,
                    title="",
                )
        link("kitsu", f"https://kitsu.io/anime/{plain(data.get('id'))}")
    elif source == "anilist":
        names = get("title") or {}
        if isinstance(names, dict):
            for locale, field in (
                ("ja-Latn", "romaji"),
                ("en", "english"),
                ("ja", "native"),
                ("und", "userPreferred"),
            ):
                title(
                    locale,
                    "native" if field == "native" else "localized",
                    f"title.{field}",
                    names.get(field),
                )
        for alias in get("synonyms") or []:
            title("und", "alias", "synonyms", alias)
        description("en", "synopsis", "description", get("description"))
        enum("anime_statuses", "status", get("status"))
        enum("anime_formats", "format", get("format"))
        episodes("total", get("episodes"))
        duration("episode", get("duration"), 60)
        date("aired_start", get("startDate"))
        date("aired_end", get("endDate"))
        score("average", get("averageScore"), 100)
        score("mean", get("meanScore"), 100)
        for k in ("popularity", "trending", "favourites", "seasonYear"):
            metric(k, get(k))
        genres(get("genres"))
        for tag in get("tags") or []:
            if isinstance(tag, dict) and tag.get("name"):
                insert(
                    "anime_tags",
                    tag=str(tag["name"]),
                    rank=tag.get("rank"),
                    is_spoiler=int(bool(tag.get("isMediaSpoiler"))),
                )
        studies = get("studios") or {}
        for edge in studies.get("edges") or [] if isinstance(studies, dict) else []:
            node = (edge or {}).get("node") or {}
            if node.get("name"):
                studio(
                    str(node["name"]),
                    str(node.get("id") or ""),
                    "main" if edge.get("isMain") else "other",
                )
        for kind, table in (
            ("characters", "anime_characters"),
            ("staff", "anime_staff"),
        ):
            group = get(kind) or {}
            for edge in group.get("edges") or [] if isinstance(group, dict) else []:
                node = (edge or {}).get("node") or {}
                names = node.get("name") or {}
                person(
                    table,
                    str(node.get("id") or ""),
                    plain(names.get("full") if isinstance(names, dict) else names),
                    plain(edge.get("role")),
                    node,
                )
        rel = get("relations") or {}
        for edge in rel.get("edges") or [] if isinstance(rel, dict) else []:
            node = (edge or {}).get("node") or {}
            if node.get("id"):
                insert(
                    "anime_relations",
                    relation_type=plain(edge.get("relationType")) or "unknown",
                    target_external_id=str(node["id"]),
                    target_adb_id=None,
                )
        image("poster", get("coverImage"))
        image("banner", get("bannerImage"))
        trailer = get("trailer") or {}
        if isinstance(trailer, dict) and trailer.get("id") and trailer.get("site"):
            insert(
                "anime_videos",
                video_type="trailer",
                url=f"https://{plain(trailer['site']).lower()}.com/watch?v={plain(trailer['id'])}",
                title="",
            )
        link("anilist", get("siteUrl"))
        for x in get("externalLinks") or []:
            if isinstance(x, dict):
                link(plain(x.get("site")) or "external", x.get("url"))

    # Keep the provider's remaining attributes in the full main database,
    # even when no semantic adapter for them exists yet.
    for field, value in attrs.items():
        if field not in used or isinstance(value, (list, dict)):
            insert(
                "anime_source_attributes", field_path=field, value_json=compact(value)
            )
    if source == "kitsu":
        for field, value in data.items():
            if field != "attributes":
                insert(
                    "anime_source_attributes",
                    field_path="resource." + field,
                    value_json=compact(value),
                )
    # Enrich nested source-specific data without discarding original fields.
    from anime_db.enrichment import enrich_record

    enrich_record(conn, item, anime_id)
    return anime_id
