"""Incremental, resumable bounded metadata ingestion and durable snapshots."""

from __future__ import annotations

import hashlib
import json
import time
import zlib
from datetime import datetime, timezone, timedelta
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


def is_stale(record: dict, days: int) -> bool:
    """Malformed or timezone-less timestamps must be revalidated."""
    try:
        seen = datetime.fromisoformat(str(record["fetched_at"]))
        return seen.tzinfo is None or seen <= datetime.now(timezone.utc) - timedelta(
            days=days
        )
    except (KeyError, ValueError, TypeError):
        return True


def crawl(
    candidates: list[Candidate],
    state_dir: Path,
    *,
    registry: IdentityRegistry | None = None,
    client: httpx.Client | None = None,
    max_requests: int | None = None,
) -> dict:
    if max_requests is not None and not 1 <= max_requests <= 10:
        raise ValueError("Test phase permits only 1–10 total HTTP requests per source")
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

    def identity_keys(external_ids: dict[str, str]) -> dict[str, str]:
        # Do not inflate the permanent identity registry with all 55+
        # provider namespaces; only register the actively collected sources.
        return {
            key: val for key, val in external_ids.items() if key in (*SOURCES, "mal")
        }

    try:
        for source in SOURCES:
            policy = policies[source]
            cap = min(policy.max_requests, max_requests or policy.max_requests)
            if not policy.enabled:
                report["sources"][source] = {"status": "disabled", "attempted": 0}
                continue
            # Reserve a small slice of the fixed quota for aging records so
            # 30,000+ new titles do not starve existing anime of updates.
            stale: list[tuple[str, Candidate, str]] = []
            for c in candidates:
                ext = c.external_ids.get(source)
                if source == "shikimori":
                    ext = ext or c.external_ids.get("mal")
                if ext and ext.isdecimal():
                    cached_path = snapshot_path(state_dir, source, ext)
                    if cached_path.is_file():
                        cached = read_snapshot(cached_path)
                        if cached.get("amdb_id") == c.amdb_id and is_stale(
                            cached, policy.refresh_after_days
                        ):
                            stale.append((str(cached.get("fetched_at", "")), c, ext))
            stale.sort(key=lambda row: row[0])
            refresh_quota = min(policy.max_refresh_requests, len(stale), cap)
            budget = [cap - refresh_quota]
            gate = RateGate(policy)
            found = skipped = missing = errors = refreshed_ids = 0
            blocked = False
            for candidate in candidates:
                external_id = candidate.external_ids.get(source)
                if source == "shikimori":
                    external_id = external_id or candidate.external_ids.get("mal")
                if not external_id or not external_id.isdecimal():
                    continue
                path = snapshot_path(state_dir, source, external_id)
                if path.is_file():
                    # Attach newly available AMDB provider identifiers to old
                    # snapshots without sending another HTTP request.
                    cached = read_snapshot(path)
                    if cached.get("amdb_id") != candidate.amdb_id:
                        errors += 1
                        continue
                    external_ids = {
                        **cached.get("external_ids", {}),
                        **candidate.external_ids,
                        source: external_id,
                    }
                    try:
                        stable_id = registry.assign(
                            candidate.amdb_id, identity_keys(external_ids)
                        )
                    except ValueError:
                        errors += 1
                        continue
                    if stable_id != cached["adb_id"]:
                        errors += 1
                        continue
                    if external_ids != cached.get("external_ids"):
                        cached["external_ids"] = external_ids
                        write_snapshot(path, cached)
                        registry.save(state_dir / "registry.json")
                        refreshed_ids += 1
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
                            candidate.amdb_id, identity_keys(candidate.external_ids)
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
                        # Stop all traffic, including stale refreshes, when
                        # the service denies access or enforces a rate block.
                        blocked = True
                        break
            new_requests = cap - refresh_quota - budget[0]
            refresh_budget = [cap - new_requests]
            refreshed = 0
            for _, candidate, external_id in stale:
                if blocked or refresh_budget[0] <= 0:
                    break
                path = snapshot_path(state_dir, source, external_id)
                snapshot = read_snapshot(path)
                initial_budget = refresh_budget[0]
                start = time.monotonic()
                data, status, error = fetch_one(
                    source, external_id, policy, refresh_budget, gate, client
                )
                report["requests"].append(
                    {
                        "source": source,
                        "id": external_id,
                        "status": status,
                        "error": error,
                        "kind": "refresh",
                        "elapsed_ms": int((time.monotonic() - start) * 1000),
                        "used_requests": initial_budget - refresh_budget[0],
                        "at": stamp(),
                    }
                )
                if data is not None:
                    try:
                        stable_id = registry.assign(
                            candidate.amdb_id,
                            identity_keys(snapshot.get("external_ids") or {}),
                        )
                    except ValueError:
                        stable_id = None
                    if (
                        snapshot.get("amdb_id") != candidate.amdb_id
                        or snapshot.get("adb_id") != stable_id
                    ):
                        errors += 1
                        report["requests"][-1]["error"] = "ambiguous_identity"
                        continue
                    snapshot["payload"] = data
                    snapshot["fetched_at"] = stamp()
                    write_snapshot(path, snapshot)
                    refreshed += 1
                elif status in (404, 410):
                    # Preserve the last successful payload. A missing page may
                    # be temporary and should not destroy confirmed metadata.
                    missing += 1
                else:
                    errors += 1
                    if status in (401, 403, 429):
                        break
            total_requests = new_requests + (cap - new_requests - refresh_budget[0])
            report["sources"][source] = {
                "status": "complete" if errors == 0 else "partial",
                "attempted": total_requests,
                "found": found,
                "refreshed": refreshed,
                "cached": skipped,
                "cached_ids_refreshed": refreshed_ids,
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
