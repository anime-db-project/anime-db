"""Bounded provider clients: no browser impersonation or CAPTCHA workarounds."""

from __future__ import annotations

import json
import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

PROJECT_USER_AGENT = "anime-db/0.1 (+https://github.com/anime-db-project/anime-db)"
CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "sources.json"
SOURCES = ("shikimori", "kitsu", "anilist")

ANILIST_QUERY = """
query AnimeDB($id: Int!) {
  Media(id: $id, type: ANIME) {
    id idMal siteUrl title { romaji english native userPreferred }
    synonyms format status description(asHtml: false)
    startDate { year month day } endDate { year month day }
    season seasonYear episodes duration countryOfOrigin source
    hashtag isAdult isLicensed updatedAt averageScore meanScore
    popularity trending favourites genres
    tags { id name description rank isMediaSpoiler isGeneralSpoiler }
    studios(isMain: false) {
      pageInfo { hasNextPage }
      edges { isMain node { id name siteUrl } }
    }
    relations {
      pageInfo { hasNextPage }
      edges { relationType node { id type title { romaji } } }
    }
    characters(page: 1, perPage: 25) {
      pageInfo { hasNextPage }
      edges {
        role
        voiceActors { id name { full native } }
        node { id name { full native } image { large } }
      }
    }
    staff(page: 1, perPage: 25) {
      pageInfo { hasNextPage }
      edges { role node { id name { full native } image { large } } }
    }
    coverImage { extraLarge large medium color }
    bannerImage trailer { id site thumbnail }
    externalLinks { id url site type language }
    streamingEpisodes { title thumbnail url }
    stats { scoreDistribution { score amount } statusDistribution { status amount } }
    nextAiringEpisode { id airingAt timeUntilAiring episode }
    rankings { rank type format year season allTime context }
  }
}
"""


@dataclass(frozen=True)
class SourcePolicy:
    source: str
    max_requests: int
    per_second: int
    per_minute: int
    per_hour: int
    delay_seconds: tuple[float, float]
    retry_429_wait_seconds: int
    max_429_wait_seconds: int
    timeout_seconds: float
    enabled: bool
    refresh_after_days: int
    max_refresh_requests: int

    @classmethod
    def from_config(cls, source: str, row: dict, cap: int) -> "SourcePolicy":
        lo, hi = row["delay_seconds"]
        policy = cls(
            source=source,
            max_requests=int(row["max_requests"]),
            per_second=int(row["per_second"]),
            per_minute=int(row["per_minute"]),
            per_hour=int(row["per_hour"]),
            delay_seconds=(float(lo), float(hi)),
            retry_429_wait_seconds=int(row["retry_429_wait_seconds"]),
            max_429_wait_seconds=int(row["max_429_wait_seconds"]),
            timeout_seconds=float(row["timeout_seconds"]),
            enabled=row["enabled"],
            refresh_after_days=int(row.get("refresh_after_days", 14)),
            max_refresh_requests=int(row.get("max_refresh_requests", 2)),
        )
        if not isinstance(policy.enabled, bool):
            raise ValueError(f"{source}: enabled must be boolean")
        if not (1 <= policy.max_requests <= cap <= 10):
            raise ValueError(f"{source}: test-phase hard cap is 10 HTTP calls")
        if not all(
            v >= 1 for v in (policy.per_second, policy.per_minute, policy.per_hour)
        ):
            raise ValueError(f"{source}: rate windows must be positive")
        if not (0 <= lo <= hi <= 10):
            raise ValueError(f"{source}: invalid jitter")
        if not (
            0 <= policy.retry_429_wait_seconds <= policy.max_429_wait_seconds <= 60
        ):
            raise ValueError(f"{source}: retry window invalid")
        if not 1 <= policy.timeout_seconds <= 60:
            raise ValueError(f"{source}: invalid timeout")
        if not 1 <= policy.refresh_after_days <= 365:
            raise ValueError(f"{source}: invalid cache TTL")
        if not 0 <= policy.max_refresh_requests <= policy.max_requests:
            raise ValueError(f"{source}: invalid refresh quota")
        return policy


def load_policies(path: Path = CONFIG_FILE) -> dict[str, SourcePolicy]:
    raw = json.loads(path.read_text("utf-8"))
    if raw.get("phase") != "test" or raw.get("hard_cap_per_source") != 10:
        raise ValueError("Only explicit test phase with cap 10 is enabled")
    if set(raw) != set(SOURCES) | {"phase", "hard_cap_per_source"}:
        raise ValueError("Unknown or missing source policies")
    return {name: SourcePolicy.from_config(name, raw[name], 10) for name in SOURCES}


class RateGate:
    """Three sliding-window rate limits with an independent random gap."""

    def __init__(self, policy: SourcePolicy):
        self.p = policy
        self.windows = [
            (1.0, policy.per_second, deque()),
            (60.0, policy.per_minute, deque()),
            (3600.0, policy.per_hour, deque()),
        ]
        self.previous: float | None = None

    def wait(self) -> None:
        if self.previous is not None:
            delay = random.uniform(*self.p.delay_seconds)
            remaining = self.previous + delay - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
        while True:
            now = time.monotonic()
            wait = 0.0
            for interval, count, timestamps in self.windows:
                while timestamps and now - timestamps[0] >= interval:
                    timestamps.popleft()
                if len(timestamps) >= count:
                    wait = max(wait, timestamps[0] + interval - now)
            if wait <= 0:
                break
            time.sleep(wait + 0.01)
        now = time.monotonic()
        for _, _, timestamps in self.windows:
            timestamps.append(now)
        self.previous = now


def provider_request(
    source: str, external_id: str
) -> tuple[str, str, dict | None, dict]:
    """Only fixed, allowlisted HTTPS endpoints. No untrusted URLs."""
    if not external_id.isdecimal() or int(external_id) < 1:
        raise ValueError("Provider IDs must be positive integers")
    if source == "shikimori":
        return (
            "GET",
            f"https://shikimori.io/api/animes/{quote(external_id)}",
            None,
            {"Accept": "application/json"},
        )
    if source == "kitsu":
        return (
            "GET",
            f"https://kitsu.io/api/edge/anime/{quote(external_id)}",
            None,
            {"Accept": "application/vnd.api+json"},
        )
    if source == "anilist":
        return (
            "POST",
            "https://graphql.anilist.co",
            {"query": ANILIST_QUERY, "variables": {"id": int(external_id)}},
            {"Accept": "application/json"},
        )
    raise ValueError("Unknown provider")


def fetch_one(
    source: str,
    external_id: str,
    policy: SourcePolicy,
    budget: list[int],
    gate: RateGate,
    client: httpx.Client,
) -> tuple[dict | None, int, str]:
    """Returns payload, HTTP status and safe error (no credentials in reports)."""
    method, url, payload, headers = provider_request(source, external_id)
    retried_429 = False
    while budget[0] > 0:
        gate.wait()
        budget[0] -= 1
        try:
            response = client.request(
                method,
                url,
                json=payload,
                headers=headers,
                timeout=policy.timeout_seconds,
            )
        except httpx.TransportError as exc:
            return None, 0, type(exc).__name__
        status = response.status_code
        if status == 429:
            retry_after = response.headers.get("retry-after", "").strip()
            try:
                wait = (
                    float(retry_after) if retry_after else policy.retry_429_wait_seconds
                )
            except ValueError:
                wait = policy.retry_429_wait_seconds
            if retried_429 or wait > policy.max_429_wait_seconds or budget[0] == 0:
                return None, status, "rate_limited"
            retried_429 = True
            time.sleep(max(0.1, wait))
            continue
        if status in (403, 401):
            return None, status, "access_denied"
        if status in (404, 410):
            return None, status, "not_found"
        if status != 200:
            return None, status, f"http_{status}"
        if "html" in response.headers.get("content-type", "").lower():
            return None, status, "unexpected_html_response"
        try:
            data = response.json()
        except ValueError:
            return None, status, "invalid_json"
        if not isinstance(data, dict):
            return None, status, "unexpected_json"
        if source == "anilist":
            if data.get("errors"):
                return None, status, "graphql_errors"
            data = data.get("data", {}).get("Media")
        elif source == "kitsu":
            data = data.get("data")
        if not isinstance(data, dict):
            return None, status, "empty_media"
        return data, status, ""
    return None, 0, "budget_exhausted"
