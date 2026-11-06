from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anime_db.identity import IdentityRegistry


class IdentityTests(unittest.TestCase):
    def test_adb_identity_is_durable_and_independent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "registry.json"
            r = IdentityRegistry()
            one = r.assign(120, {"anilist": "99", "kitsu": "100"})
            self.assertEqual(one, 1)
            r.save(path)
            reload = IdentityRegistry.load(path)
            self.assertEqual(reload.assign(120, {"anilist": "99"}), one)
            self.assertEqual(reload.assign(None, {"shikimori": "22"}), 2)
            self.assertEqual(reload.assign(121, {"shikimori": "22"}), 2)
            self.assertEqual(reload.next_id, 3)
            with self.assertRaises(ValueError):
                reload.assign(120, {"shikimori": "22"})
            with self.assertRaises(ValueError):
                reload.assign(122, {"anilist": "99"})
            reload.save(path)
            self.assertEqual(json.loads(path.read_text())["next_id"], 3)

    def test_source_ids_are_not_casefolded_or_guessed(self) -> None:
        registry = IdentityRegistry()
        self.assertNotEqual(
            registry.assign(None, {"kitsu": "1"}),
            registry.assign(None, {"anilist": "1"}),
        )
