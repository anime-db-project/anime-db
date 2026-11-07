"""Public identity lookup and literal/phrase search without mapping.db."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from anime_db.builder import populate_search
from anime_db.schema import create_catalog
from anime_db.search import search


class StandaloneSearchTests(unittest.TestCase):
    def test_all_id_types_and_titles_with_colon_or_special_chars(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "anime.db"
            with sqlite3.connect(path) as conn:
                create_catalog(conn, role="test")
                for adb, amdb, title, native in (
                    (1, 1001, "Star: Blue", "123"),
                    (2, 1002, "Star_Red", "124"),
                    (3, 1003, "Star%Rays", "125"),
                ):
                    conn.execute(
                        "INSERT INTO anime(adb_id,amdb_id) VALUES (?,?)",
                        (adb, amdb),
                    )
                    anime_id = int(
                        conn.execute(
                            "SELECT anime_id FROM anime WHERE adb_id=?", (adb,)
                        ).fetchone()[0]
                    )
                    conn.execute(
                        "INSERT INTO anime_titles("
                        "anime_id,source_id,locale,title_type,source_field,value) "
                        "VALUES (?,1,'en','localized','english',?)",
                        (anime_id, title),
                    )
                    conn.execute(
                        "INSERT INTO anime_external_ids(anime_id,provider,external_id) "
                        "VALUES (?,?,?)",
                        (anime_id, "anidb", native),
                    )
                conn.execute(
                    "INSERT INTO amdb_redirects(old_amdb_id,new_amdb_id) VALUES(31719,1001)"
                )
                conn.execute(
                    "INSERT INTO id_redirects(old_adb_id,new_adb_id) VALUES(999,1)"
                )
                populate_search(conn)
                conn.commit()
            self.assertEqual(search(path, "adb:1")[0]["amdb_id"], 1001)
            self.assertEqual(search(path, "amdb:1001")[0]["adb_id"], 1)
            self.assertEqual(search(path, "amdb:31719")[0]["adb_id"], 1)
            self.assertEqual(search(path, "adb:999")[0]["adb_id"], 1)
            self.assertEqual(search(path, "anidb:123")[0]["adb_id"], 1)
            self.assertEqual(search(path, "Star: Blue")[0]["adb_id"], 1)
            self.assertEqual(search(path, "Star%")[0]["adb_id"], 3)
            self.assertEqual(search(path, "Star_Red")[0]["adb_id"], 2)
            self.assertEqual(search(path, "missing:123"), [])
            self.assertEqual(search(path, "'; DROP TABLE anime;--"), [])
            self.assertEqual(search(path, "adb:1")[0]["adb_id"], 1)


if __name__ == "__main__":
    unittest.main()
