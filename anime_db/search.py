"""Self-contained lookup by ADB, AMDB, source ID and title."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote


def search(path: Path, query: str, *, limit: int = 20) -> list[dict]:
    """Open the standalone anime.db read-only; never depend on mappings.db."""
    if not (1 <= limit <= 100):
        raise ValueError("limit out of range")
    query = query.strip()
    if not query:
        return []
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        base = """
            SELECT a.adb_id, a.amdb_id,
              (SELECT t.value FROM anime_titles AS t WHERE t.anime_id=a.anime_id
               ORDER BY CASE t.locale WHEN 'ru' THEN 0 WHEN 'en' THEN 1
                 WHEN 'ja-Latn' THEN 2 WHEN 'ja' THEN 3 ELSE 4 END,
                 t.source_id, t.title_type LIMIT 1) AS title
            FROM anime AS a
        """
        params: tuple = ()
        if query.startswith("adb:") and query[4:].isdigit():
            sql = base + (
                " WHERE a.adb_id=? OR a.adb_id=("
                "SELECT new_adb_id FROM id_redirects WHERE old_adb_id=?) LIMIT ?"
            )
            params = (int(query[4:]), int(query[4:]), limit)
        elif query.startswith("amdb:") and query[5:].isdigit():
            sql = base + (
                " WHERE a.amdb_id=? OR a.amdb_id=("
                "SELECT new_amdb_id FROM amdb_redirects WHERE old_amdb_id=?) LIMIT ?"
            )
            params = (int(query[5:]), int(query[5:]), limit)
        elif (
            ":" in query
            and conn.execute(
                "SELECT 1 FROM anime_external_ids WHERE provider=? LIMIT 1",
                (query.split(":", 1)[0].strip(),),
            ).fetchone()
        ):
            provider, native = query.split(":", 1)
            sql = (
                base
                + """
                JOIN anime_external_ids AS e ON e.anime_id=a.anime_id
                WHERE e.provider=? AND e.external_id=? LIMIT ?
            """
            )
            params = (provider, native, limit)
        else:
            # FTS5 query as a quoted phrase prevents accidental user syntax.
            phrase = '"' + query.replace('"', '""') + '"'
            sql = (
                base
                + """
                JOIN anime_title_fts AS fts ON fts.adb_id=a.adb_id
                WHERE anime_title_fts MATCH ? GROUP BY a.anime_id LIMIT ?
            """
            )
            params = (phrase, limit)
            # SQL LIKE wildcards are literal user text, not search syntax.
            # FTS5 strips these punctuation marks and can overmatch, so
            # do not use its results for names containing % or _.
            records = (
                conn.execute(sql, params).fetchall()
                if "%" not in query and "_" not in query
                else []
            )
            if not records:
                sql = (
                    base
                    + """
                    JOIN anime_titles AS t2 ON t2.anime_id=a.anime_id
                    WHERE t2.value LIKE ? ESCAPE '\\' GROUP BY a.anime_id LIMIT ?
                """
                )
                params = (
                    "%" + query.replace("%", "\\%").replace("_", "\\_") + "%",
                    limit,
                )
                records = conn.execute(sql, params).fetchall()
            return [dict(r) for r in records]
        return [dict(row) for row in conn.execute(sql, params)]
