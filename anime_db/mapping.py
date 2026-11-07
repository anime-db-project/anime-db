"""Consume stable AMDB anchors and all provider IDs without copying its graph.

Uses only the mapping database as a build-time reference. A released
anime.db remains fully usable without a local mapping database.
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


def read_candidates(mapping_path: Path, limit: int | None = None) -> list[Candidate]:
    """Prioritize cross-matched titles; retain every known external namespace.

    A limit of None reads every eligible catalog entity and is preferred for a
    checkpointed crawler. HTTP request budgets remain independently bounded
    by source policies. A positive limit is useful for local smoke tests.
    """
    if not mapping_path.is_file():
        raise FileNotFoundError(f"Mapping reference missing: {mapping_path}")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be a positive integer or None")

    uri = mapping_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        # Phase one: only IDs that can drive our three supported adapters.
        rows = conn.execute("""
            SELECT e.anchor_id, n.provider, n.external_id
            FROM entities AS e
            JOIN entity_nodes AS en ON en.entity_id = e.entity_id
            JOIN nodes AS n ON n.id = en.node_id
            WHERE n.provider IN ('shikimori','kitsu','anilist','mal')
            ORDER BY e.anchor_id, n.provider, n.external_id
        """)
        groups: dict[int, dict[str, str]] = defaultdict(dict)
        for anchor, provider, ext in rows:
            groups[int(anchor)][str(provider)] = str(ext)

        available = sorted(
            groups,
            key=lambda aid: (
                -sum(s in groups[aid] for s in ("shikimori", "kitsu", "anilist")),
                aid,
            ),
        )
        if limit is not None:
            available = available[:limit]
        # Phase two: include *all* external namespaces for selected anchors,
        # not only the provider keys we currently know how to query.
        result: dict[int, dict[str, str]] = {anchor: {} for anchor in available}
        for offset in range(0, len(available), 400):
            batch = available[offset : offset + 400]
            placeholders = ",".join("?" for _ in batch)
            for anchor, provider, external in conn.execute(
                f"""
                SELECT e.anchor_id, n.provider, n.external_id
                FROM entities AS e
                JOIN entity_nodes AS en ON en.entity_id=e.entity_id
                JOIN nodes AS n ON n.id=en.node_id
                WHERE e.anchor_id IN ({placeholders})
                ORDER BY e.anchor_id, n.provider, n.external_id
                """,
                batch,
            ):
                bucket = result[int(anchor)]
                key = str(provider)
                value = str(external)
                # Keep the first sorted value if a provider appears twice.
                bucket.setdefault(key, value)
        return [Candidate(anchor, result[anchor]) for anchor in available]


def read_anchor_redirects(mapping_path: Path) -> dict[int, int]:
    """Import only public AMDB anchor redirects, not mapping graph internals.

    Flatten any chains, and reject loops instead of inventing an identity.
    """
    if not mapping_path.is_file():
        raise FileNotFoundError(mapping_path)
    uri = mapping_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        try:
            rows = conn.execute(
                "SELECT old_anchor_id,anchor_id FROM anchor_redirects"
            ).fetchall()
        except sqlite3.OperationalError as error:
            raise ValueError(
                "Mapping reference has no anchor_redirects table"
            ) from error
    raw = {int(old): int(new) for old, new in rows}
    result = {}
    for old in raw:
        current = old
        seen = set()
        while current in raw:
            if current in seen:
                raise ValueError(f"Cyclic AMDB redirect at {current}")
            seen.add(current)
            current = raw[current]
        if current <= 0 or old <= 0:
            raise ValueError("AMDB anchors must be positive")
        result[old] = current
    return result
