"""Read only the stable anime identity reference from anime-mapping-db.

Never persist or exchange its build-local entity_id or node_id.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Candidate:
    amdb_id: int
    external_ids: dict[str, str]


def read_candidates(mapping_path: Path, limit: int = 90) -> list[Candidate]:
    if not mapping_path.exists():
        raise FileNotFoundError(f"Mapping reference missing: {mapping_path}")
    uri = mapping_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute("""
            SELECT e.anchor_id, n.provider, n.external_id
            FROM entities AS e
            JOIN entity_nodes AS en ON en.entity_id = e.entity_id
            JOIN nodes AS n ON n.id = en.node_id
            WHERE n.provider IN ('shikimori','kitsu','anilist','mal')
            ORDER BY e.anchor_id, n.provider, n.external_id
        """)
        groups: dict[int, dict[str, str]] = defaultdict(dict)
        for amdb, provider, external in rows:
            groups[int(amdb)][str(provider)] = str(external)
        # Prefer items carrying many independent identifiers so 10 requests
        # per source provide a useful, cross-provider comparison sample.
        candidates = [
            Candidate(anchor, ids)
            for anchor, ids in groups.items()
            if any(s in ids for s in ("shikimori", "kitsu", "anilist", "mal"))
        ]
        return sorted(
            candidates,
            key=lambda item: (
                -sum(s in item.external_ids for s in ("shikimori", "kitsu", "anilist")),
                item.amdb_id,
            ),
        )[:limit]
