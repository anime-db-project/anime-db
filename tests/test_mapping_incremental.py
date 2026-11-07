"""Full mapping namespace import and durable, bounded cached source updates."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

from anime_db.crawler import crawl, read_snapshot, snapshot_path
from anime_db.mapping import Candidate, read_candidates, read_anchor_redirects


class MappingIncrementalTests(unittest.TestCase):
    def test_all_external_id_namespaces_survive_candidate_selection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mappings.db"
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE entities(entity_id INTEGER PRIMARY KEY,anchor_id INTEGER UNIQUE);
                    CREATE TABLE nodes(id INTEGER PRIMARY KEY,provider TEXT,external_id TEXT);
                    CREATE TABLE entity_nodes(entity_id INTEGER,node_id INTEGER);
                """)
                conn.executemany(
                    "INSERT INTO entities(entity_id,anchor_id) VALUES (?,?)",
                    [(1, 100), (2, 200), (3, 300)],
                )
                rows = [
                    (1, "shikimori", "100"),
                    (1, "anilist", "200"),
                    (1, "kitsu", "300"),
                    (1, "anidb", "custom-database-key"),
                    (1, "animeplanet", "anime-example"),
                    (2, "shikimori", "101"),
                    (2, "anilist", "201"),
                    (2, "tvdb", "TVDB-999"),
                    (3, "mal", "104"),
                    (3, "anidb", "111"),
                ]
                for i, (entity, provider, external) in enumerate(rows, 1):
                    conn.execute(
                        "INSERT INTO nodes(id,provider,external_id) VALUES (?,?,?)",
                        (i, provider, external),
                    )
                    conn.execute(
                        "INSERT INTO entity_nodes(entity_id,node_id) VALUES (?,?)",
                        (entity, i),
                    )
            all_candidates = read_candidates(path)
            self.assertEqual([x.amdb_id for x in all_candidates], [100, 200, 300])
            self.assertEqual(
                all_candidates[0].external_ids["animeplanet"],
                "anime-example",
            )
            self.assertEqual(all_candidates[1].external_ids["tvdb"], "TVDB-999")
            self.assertEqual(read_candidates(path, 1), all_candidates[:1])
            with self.assertRaises(ValueError):
                read_candidates(path, 0)

    def test_redirect_chain_is_resolved_and_cycle_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mapping.db"
            with sqlite3.connect(path) as conn:
                conn.execute(
                    "CREATE TABLE anchor_redirects("
                    "old_anchor_id INTEGER PRIMARY KEY, anchor_id INTEGER)"
                )
                conn.executemany(
                    "INSERT INTO anchor_redirects(old_anchor_id,anchor_id) VALUES(?,?)",
                    [(90, 100), (80, 90)],
                )
                conn.commit()
            self.assertEqual(read_anchor_redirects(path), {90: 100, 80: 100})
            with sqlite3.connect(path) as conn:
                conn.execute(
                    "UPDATE anchor_redirects SET anchor_id=80 WHERE old_anchor_id=90"
                )
                conn.commit()
            with self.assertRaisesRegex(ValueError, "Cyclic AMDB redirect"):
                read_anchor_redirects(path)

    def test_cached_snapshot_backfills_new_provider_refs_without_http(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            first = Candidate(
                amdb_id=10,
                external_ids={"shikimori": "30", "mal": "30"},
            )
            calls = []

            def handler(req: httpx.Request) -> httpx.Response:
                calls.append(str(req.url))
                return httpx.Response(200, json={"id": 30, "name": "Test"})

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = crawl([first], state, client=client, max_requests=1)
            self.assertEqual(result["sources"]["shikimori"]["found"], 1)
            self.assertEqual(len(calls), 1)

            second = Candidate(
                amdb_id=10,
                external_ids={
                    "shikimori": "30",
                    "mal": "30",
                    "anidb": "114",
                    "animeplanet": "test-entry",
                    "trakt": "movie:30",
                },
            )

            def forbidden(req: httpx.Request) -> httpx.Response:
                self.fail(f"No API calls expected for cached record: {req.url}")

            with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
                result = crawl([second], state, client=client, max_requests=1)
            self.assertEqual(result["sources"]["shikimori"]["attempted"], 0)
            self.assertEqual(result["sources"]["shikimori"]["cached_ids_refreshed"], 1)
            record = read_snapshot(snapshot_path(state, "shikimori", "30"))
            self.assertEqual(record["adb_id"], 1)
            self.assertEqual(record["external_ids"]["trakt"], "movie:30")
            # No redundant mutation on a subsequent identical build.
            with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
                again = crawl([second], state, client=client, max_requests=1)
            self.assertEqual(again["sources"]["shikimori"]["cached_ids_refreshed"], 0)


if __name__ == "__main__":
    unittest.main()
