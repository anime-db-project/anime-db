"""Two-phase crawler: bounded unseen ingest plus explicit stale-data revalidation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from anime_db.crawler import crawl, read_snapshot, snapshot_path, write_snapshot
from anime_db.mapping import Candidate


class RefreshTests(unittest.TestCase):
    def test_one_new_title_and_one_stale_title_share_two_request_budget(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = Candidate(100, {"shikimori": "100"})
            new = Candidate(200, {"shikimori": "200"})
            requests: list[str] = []

            def handler(req: httpx.Request) -> httpx.Response:
                media_id = req.url.path.rsplit("/", 1)[-1]
                requests.append(media_id)
                return httpx.Response(
                    200,
                    json={"id": int(media_id), "name": f"New version {media_id}"},
                )

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                first = crawl([old], root, client=client, max_requests=1)
            self.assertEqual(first["sources"]["shikimori"]["found"], 1)
            path = snapshot_path(root, "shikimori", "100")
            snapshot = read_snapshot(path)
            stable_id = snapshot["adb_id"]
            snapshot["fetched_at"] = "2000-01-01T00:00:00+00:00"
            write_snapshot(path, snapshot)
            requests.clear()

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                second = crawl([old, new], root, client=client, max_requests=2)
            src = second["sources"]["shikimori"]
            self.assertEqual(requests, ["200", "100"])
            self.assertEqual(src["attempted"], 2)
            self.assertEqual(src["found"], 1)
            self.assertEqual(src["refreshed"], 1)
            self.assertEqual(src["cached"], 1)
            self.assertEqual(read_snapshot(path)["adb_id"], stable_id)
            self.assertNotEqual(
                read_snapshot(path)["fetched_at"], "2000-01-01T00:00:00+00:00"
            )

            requests.clear()
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                third = crawl([old, new], root, client=client, max_requests=2)
            self.assertEqual(requests, [])
            self.assertEqual(third["sources"]["shikimori"]["attempted"], 0)

    def test_access_denial_aborts_reserved_refresh_requests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = Candidate(101, {"shikimori": "101"})
            next_item = Candidate(102, {"shikimori": "102"})
            with httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200, json={"id": 101, "name": "First version"}
                    )
                )
            ) as client:
                crawl([first], root, client=client, max_requests=1)
            path = snapshot_path(root, "shikimori", "101")
            item = read_snapshot(path)
            item["fetched_at"] = "2000-01-01T00:00:00+00:00"
            write_snapshot(path, item)

            requests = []

            def refuse(req: httpx.Request) -> httpx.Response:
                requests.append(req.url.path)
                return httpx.Response(403, text="<html>access blocked</html>")

            with httpx.Client(transport=httpx.MockTransport(refuse)) as client:
                result = crawl([first, next_item], root, client=client, max_requests=2)
            self.assertEqual(requests, ["/api/animes/102"])
            self.assertEqual(result["sources"]["shikimori"]["attempted"], 1)
            self.assertEqual(result["sources"]["shikimori"]["refreshed"], 0)
            self.assertEqual(read_snapshot(path)["fetched_at"], item["fetched_at"])

    def test_direct_crawler_cannot_override_test_quota(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                crawl([], Path(td), max_requests=11)
            with self.assertRaises(ValueError):
                crawl([], Path(td), max_requests=0)


if __name__ == "__main__":
    unittest.main()
