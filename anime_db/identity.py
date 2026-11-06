"""Independent monotonically allocated ADB identities plus optional AMDB links."""

from __future__ import annotations

import json
from pathlib import Path


class IdentityRegistry:
    def __init__(self, data: dict | None = None):
        data = data or {}
        self.next_id = max(1, int(data.get("next_id", 1)))
        self.by_amdb: dict[str, int] = {
            str(k): int(v) for k, v in data.get("by_amdb", {}).items()
        }
        self.by_external: dict[str, int] = {
            str(k): int(v) for k, v in data.get("by_external", {}).items()
        }
        self.redirects: dict[str, int] = {
            str(k): int(v) for k, v in data.get("redirects", {}).items()
        }
        used = set(self.by_amdb.values()) | set(self.by_external.values())
        self.next_id = max(self.next_id, max(used, default=0) + 1)

    @classmethod
    def load(cls, path: Path) -> "IdentityRegistry":
        if path.is_file():
            return cls(json.loads(path.read_text("utf-8")))
        return cls()

    @staticmethod
    def key(source: str, external_id: str) -> str:
        return f"{source}:{external_id}"

    def assign(self, amdb_id: int | None, external_ids: dict[str, str]) -> int:
        """Do not merge conflicting ADB identities from a transitive ID match."""
        candidates = set()
        if amdb_id is not None and str(amdb_id) in self.by_amdb:
            candidates.add(self.by_amdb[str(amdb_id)])
        for source, external_id in external_ids.items():
            key = self.key(source, external_id)
            if key in self.by_external:
                candidates.add(self.by_external[key])
        if len(candidates) > 1:
            raise ValueError(f"Conflicting ADB identities: {sorted(candidates)}")
        if candidates:
            adb_id = next(iter(candidates))
        else:
            adb_id = self.next_id
            self.next_id += 1
        if amdb_id is not None:
            # Distinct public AMDB identities must not silently collapse
            # because a single upstream ID is duplicated or stale.
            for known_anchor, known_adb in self.by_amdb.items():
                if known_adb == adb_id and known_anchor != str(amdb_id):
                    raise ValueError(
                        f"ADB {adb_id} already links to AMDB {known_anchor}; "
                        f"refusing unreviewed AMDB {amdb_id}"
                    )
            self.by_amdb[str(amdb_id)] = adb_id
        for source, external_id in external_ids.items():
            key = self.key(source, external_id)
            other = self.by_external.get(key)
            if other is not None and other != adb_id:
                raise ValueError(f"Conflicting external ID: {key}")
            self.by_external[key] = adb_id
        return adb_id

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "next_id": self.next_id,
            "by_amdb": self.by_amdb,
            "by_external": self.by_external,
            "redirects": self.redirects,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
