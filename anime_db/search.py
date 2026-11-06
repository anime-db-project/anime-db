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
            sql = base + " WHERE a.adb_id=? LIMIT ?"
            params = (int(query[4:]), limit)
        elif query.startswith("amdb:") and query[5:].isdigit():
            sql = base + " WHERE a.amdb_id=? LIMIT ?"
            params = (int(query[5:]), limit)
        elif ":" in query and query.split(":", 1)[0] in ("shikimori", "kitsu", "anilist", "mal"):
            provider, native = query.split(":", 1)
            sql = base + """
                JOIN anime_external_ids AS e ON e.anime_id=a.anime_id
                JOIN source_catalog AS s ON s.source_id=e.source_id
                WHERE s.source_key=? AND e.external_id=? LIMIT ?
            """
            params = (provider, native, limit)
        else:
            # FTS5 query as a quoted phrase prevents accidental user syntax.
            phrase = '"' + query.replace('"', '""') + '"'
            sql = base + """
                JOIN anime_title_fts AS fts ON fts.adb_id=a.adb_id
                WHERE anime_title_fts MATCH ? GROUP BY a.anime_id LIMIT ?
            """
            params = (phrase, limit)
            records = conn.execute(sql, params).fetchall()
            if not records:
                sql = base + """
                    JOIN anime_titles AS t2 ON t2.anime_id=a.anime_id
                    WHERE t2.value LIKE ? GROUP BY a.anime_id LIMIT ?
                """
                params = ("%" + query.replace("%", "\\%").replace("_", "\\_") + "%", limit)
                records = conn.execute(sql, params).fetchall()
            return [dict(r) for r in records]
        return [dict(row) for row in conn.execute(sql, params)]
