from __future__ import annotations

import sqlite3
import unittest

from anime_db.schema import LOCALES, SOURCES, create_catalog, create_technical, create_images


class SchemaTests(unittest.TestCase):
    def test_catalog_is_normalized_and_multivalued(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            create_catalog(conn, role="full")
            self.assertIn("ru", LOCALES)
            self.assertIn("anilist", SOURCES)
            conn.execute("INSERT INTO anime(adb_id,amdb_id) VALUES(12,34)")
            anime_id = conn.execute("SELECT anime_id FROM anime").fetchone()[0]
            conn.execute(
                "INSERT INTO anime_statuses(anime_id,source_id,status) VALUES(?,?,?)",
                (anime_id, 1, "released"),
            )
            conn.execute(
                "INSERT INTO anime_statuses(anime_id,source_id,status) VALUES(?,?,?)",
                (anime_id, 2, "current"),
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM anime_statuses").fetchone()[0], 2
            )
            conn.execute(
                "INSERT INTO anime_episode_counts(anime_id,source_id,count_type,value) VALUES(?,?,?,?)",
                (anime_id, 1, "total", 12),
            )
            conn.execute(
                "INSERT INTO anime_episode_counts(anime_id,source_id,count_type,value) VALUES(?,?,?,?)",
                (anime_id, 1, "aired", 11),
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM anime_episode_counts").fetchone()[0], 2
            )
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_technical_and_image_blobs(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            create_technical(conn)
            self.assertTrue(
                conn.execute("SELECT 1 FROM sqlite_master WHERE name='raw_snapshots'").fetchone()
            )
        with sqlite3.connect(":memory:") as conn:
            create_images(conn)
            conn.execute(
                "INSERT INTO image_blobs(sha256,mime_type,data) VALUES(?,?,?)",
                (b"1"*32, "image/png", b"\x89PNG"),
            )
            self.assertEqual(
                conn.execute("SELECT data FROM image_blobs").fetchone()[0], b"\x89PNG"
            )
