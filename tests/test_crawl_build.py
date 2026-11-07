from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx
import zstandard

from anime_db.builder import build_from_state
from anime_db.crawler import crawl
from anime_db.mapping import Candidate
from anime_db.providers import provider_request, load_policies
from anime_db.search import search


class CrawlBuildTests(unittest.TestCase):
    def test_config_caps_every_provider_at_ten(self) -> None:
        policies = load_policies()
        self.assertEqual(set(policies), {"shikimori", "kitsu", "anilist"})
        self.assertTrue(all(p.max_requests == 10 for p in policies.values()))
        with self.assertRaises(ValueError):
            provider_request("anilist", "../bad")

    def test_one_bounded_request_per_source_and_all_distributions(self) -> None:
        requests = []

        def handler(req: httpx.Request) -> httpx.Response:
            requests.append(str(req.url))
            if "shikimori.io" in str(req.url):
                return httpx.Response(
                    200,
                    json={
                        "id": 1,
                        "name": "Example",
                        "russian": "Пример",
                        "status": "released",
                        "episodes": 12,
                        "episodes_aired": 12,
                        "description": "Сюжет",
                        "genres": [{"name": "Action"}],
                        "poster": {"originalUrl": "https://example.org/cover.jpg"},
                    },
                )
            if "kitsu.io" in str(req.url):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "type": "anime",
                            "id": "2",
                            "attributes": {
                                "titles": {"en": "Example", "ja_jp": "試験"},
                                "episodeCount": 13,
                                "status": "finished",
                                "subtype": "TV",
                            },
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "Media": {
                            "id": 3,
                            "title": {"romaji": "Example", "english": "Example"},
                            "episodes": 12,
                            "status": "FINISHED",
                            "genres": ["Action"],
                            "startDate": {"year": 2020, "month": 5, "day": 2},
                        }
                    }
                },
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate = Candidate(
                42, {"shikimori": "1", "kitsu": "2", "anilist": "3", "mal": "4"}
            )
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                report = crawl(
                    [candidate], root / "state", client=client, max_requests=1
                )
            self.assertEqual(len(requests), 3)
            self.assertTrue(
                all(v["attempted"] == 1 for v in report["sources"].values())
            )
            manifest = build_from_state(root / "state", root / "output")
            self.assertEqual(manifest["anime"], 1)
            self.assertEqual(manifest["snapshots"], 3)
            self.assertEqual(
                manifest["source_coverage"],
                {
                    "anilist": 1,
                    "kitsu": 1,
                    "shikimori": 1,
                },
            )
            self.assertEqual(manifest["external_provider_namespaces"], 4)
            self.assertGreater(manifest["table_counts"]["anime_titles"], 0)
            self.assertGreaterEqual(manifest["title_locales"]["ru"], 1)
            self.assertGreaterEqual(manifest["conflicts"], 1)
            self.assertEqual(len(manifest["files"]), 13)
            full = root / "output" / "anime.db"
            with sqlite3.connect(full) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM anime").fetchone()[0], 1
                )
                ep_values = [
                    x[0]
                    for x in conn.execute(
                        "SELECT value FROM anime_episode_counts WHERE count_type='total' ORDER BY value"
                    )
                ]
                self.assertEqual(ep_values, [12, 12, 13])
                self.assertEqual(
                    conn.execute(
                        "SELECT has_conflict FROM aggregation_summary "
                        "WHERE field_name='anime_episode_counts.total'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM anime_titles WHERE locale='ru'"
                    ).fetchone()[0],
                    1,
                )
            self.assertEqual(search(full, "adb:1")[0]["amdb_id"], 42)
            self.assertEqual(search(full, "amdb:42")[0]["adb_id"], 1)
            self.assertEqual(search(full, "kitsu:2")[0]["adb_id"], 1)
            self.assertEqual(search(full, "Пример")[0]["adb_id"], 1)
            with sqlite3.connect(root / "output" / "anime.ru.db") as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM anime_titles WHERE locale='ru'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM anime_titles WHERE locale='ja'"
                    ).fetchone()[0],
                    0,
                )
            with sqlite3.connect(root / "output" / "anime.ru.db") as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM anime_episode_counts"
                    ).fetchone()[0],
                    4,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM anime_genres").fetchone()[0],
                    2,
                )
                self.assertGreater(
                    conn.execute("SELECT COUNT(*) FROM aggregation_summary").fetchone()[
                        0
                    ],
                    0,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM anime_source_attributes"
                    ).fetchone()[0],
                    0,
                )
            with sqlite3.connect(root / "output" / "anime.kitsu.db") as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM anime_sources").fetchone()[0], 1
                )
            with sqlite3.connect(root / "output" / "anime.technical.db") as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0], 3
                )
            for name, record in manifest["files"].items():
                source = (root / "output" / name).read_bytes()
                compressed = (root / "output" / (name + ".zst")).read_bytes()
                self.assertEqual(
                    zstandard.ZstdDecompressor().decompress(
                        compressed, max_output_size=len(source) + 1
                    ),
                    source,
                )

    def test_prevent_extra_calls_after_access_denied(self) -> None:
        hits = []

        def handler(req):
            hits.append(req)
            return httpx.Response(
                403,
                text="<html>Cloudflare challenge</html>",
                headers={"content-type": "text/html"},
            )

        with tempfile.TemporaryDirectory() as td:
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                report = crawl(
                    [Candidate(7, {"kitsu": "1"}), Candidate(8, {"kitsu": "2"})],
                    Path(td),
                    client=client,
                    max_requests=10,
                )
            self.assertEqual(len(hits), 1)
            self.assertEqual(report["sources"]["kitsu"]["status"], "partial")
