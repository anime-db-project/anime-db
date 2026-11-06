"""Incremental, resumable bounded metadata ingestion and durable snapshots."""

from __future__ import annotations

import hashlib
import json
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from anime_db.identity import IdentityRegistry
from anime_db.mapping import Candidate
from anime_db.providers import SOURCES, RateGate, fetch_one, load_policies


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_path(state_dir: Path, source: str, external_id: str) -> Path:
    if source not in SOURCES or not external_id.isdecimal():
        raise ValueError("Invalid snapshot path")
    return state_dir / "snapshots" / source / f"{external_id}.json.zlib"


def write_snapshot(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        record, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    temp = path.with_suffix(".tmp")
    temp.write_bytes(zlib.compress(payload.encode("utf-8"), level=9))
    temp.replace(path)


def read_snapshot(path: Path) -> dict:
    return json.loads(zlib.decompress(path.read_bytes()))


def crawl(
    candidates: list[Candidate],
    state_dir: Path,
    *,
    registry: IdentityRegistry | None = None,
    client: httpx.Client | None = None,
    max_requests: int | None = None,
) -> dict:
    policies = load_policies()
    registry = registry or IdentityRegistry.load(state_dir / "registry.json")
    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={
                "User-Agent": "anime-db/0.1 (+https://github.com/anime-db-project/anime-db)"
            },
            follow_redirects=False,
        )
    report = {"started_at": stamp(), "sources": {}, "requests": [], "phase": "test"}
    try:
        for source in SOURCES:
            policy = policies[source]
            cap = min(policy.max_requests, max_requests or policy.max_requests)
            if not policy.enabled:
                report["sources"][source] = {"status": "disabled", "attempted": 0}
                continue
            budget = [cap]
            gate = RateGate(policy)
            found = skipped = missing = errors = 0
            for candidate in candidates:
                external_id = candidate.external_ids.get(source)
                if source == "shikimori":
                    external_id = external_id or candidate.external_ids.get("mal")
                if not external_id or not external_id.isdecimal():
                    continue
                path = snapshot_path(state_dir, source, external_id)
                if path.is_file():
                    skipped += 1
                    continue
                if budget[0] <= 0:
                    break
                initial_budget = budget[0]
                start = time.monotonic()
                data, status, error = fetch_one(
                    source, external_id, policy, budget, gate, client
                )
                report["requests"].append(
                    {
                        "source": source,
                        "id": external_id,
                        "status": status,
                        "error": error,
                        "elapsed_ms": int((time.monotonic() - start) * 1000),
                        "used_requests": initial_budget - budget[0],
                        "at": stamp(),
                    }
                )
                if data is not None:
                    try:
                        adb_id = registry.assign(
                            candidate.amdb_id, candidate.external_ids
                        )
                    except ValueError as exc:
                        errors += 1
                        report["requests"][-1]["error"] = "ambiguous_identity"
                        continue
                    write_snapshot(
                        path,
                        {
                            "source": source,
                            "source_external_id": external_id,
                            "amdb_id": candidate.amdb_id,
                            "adb_id": adb_id,
                            "external_ids": candidate.external_ids,
                            "payload": data,
                            "fetched_at": stamp(),
                        },
                    )
                    registry.save(state_dir / "registry.json")
                    found += 1
                elif status in (404, 410):
                    missing += 1
                else:
                    errors += 1
                    if status in (401, 403, 429):
                        # Stop upon access refusal rather than probing further.
                        break
            report["sources"][source] = {
                "status": "complete" if errors == 0 else "partial",
                "attempted": cap - budget[0],
                "found": found,
                "cached": skipped,
                "missing": missing,
                "errors": errors,
            }
    finally:
        if owns_client:
            client.close()
    report["completed_at"] = stamp()
    path = state_dir / "last-crawl-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf-8")
    registry.save(state_dir / "registry.json")
    return report
