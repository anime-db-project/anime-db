"""Typed enrichment of nested provider data without discarding source payloads.

These transformers work strictly on already-fetched source snapshots: no
additional API requests and no dependence on a live provider during builds.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from urllib.parse import urljoin, urlsplit

from anime_db.providers import SOURCES


def _obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _seq(value: Any) -> list:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
        return result if result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _url(source: str, value: Any) -> str:
    raw = _text(value)
    if source == "shikimori" and raw.startswith("/") and not raw.startswith("//"):
        raw = urljoin("https://shikimori.io", raw)
    parsed = urlsplit(raw)
    return raw if parsed.scheme == "https" and bool(parsed.netloc) else ""


def enrich_record(conn: sqlite3.Connection, item: dict, anime_id: int) -> None:
    """Write source-specific structured categories for one normalized title."""
    source = item["source"]
    source_id = SOURCES.index(source) + 1
    obj = _obj(item["payload"])
    attrs = _obj(obj.get("attributes")) if source == "kitsu" else obj

    def add(table: str, **columns: Any) -> None:
        keys = ("anime_id", "source_id", *columns)
        values = (anime_id, source_id, *columns.values())
        placeholders = ",".join("?" for _ in keys)
        conn.execute(
            f"INSERT OR IGNORE INTO {table}({','.join(keys)}) VALUES({placeholders})",
            values,
        )

    def origin(kind: str, raw: Any) -> None:
        text = _text(raw)
        if text:
            add("anime_origins", origin_type=kind, value=text)

    def rating(raw: Any, guide: Any = None) -> None:
        text = _text(raw)
        if text:
            add("anime_age_ratings", rating=text, guide=_text(guide))

    def flag(name: str, raw: Any) -> None:
        if isinstance(raw, bool):
            add("anime_flags", flag=name, value=int(raw))

    def distribution(group: str, pairs: Any) -> None:
        if isinstance(pairs, dict):
            pairs = [{"name": k, "value": v} for k, v in pairs.items()]
        for node in _seq(pairs):
            data = _obj(node)
            bucket = _text(data.get("name"))
            count = _number(data.get("value"))
            if bucket and count is not None:
                add(
                    "anime_rating_distributions",
                    distribution_type=group,
                    bucket=bucket,
                    sample_count=count,
                )

    def image(kind: str, path: Any, width: Any = None, height: Any = None) -> None:
        full_url = _url(source, path)
        if full_url:
            add(
                "anime_images",
                image_type=kind,
                url=full_url,
                width=_number(width),
                height=_number(height),
            )

    def named_person(table: str, external_id: str, node: dict, *, char: bool) -> None:
        names = _obj(node.get("name"))
        key = "character_external_id" if char else "person_external_id"
        for name_type, language in (("full", "und"), ("native", "und")):
            value = _text(names.get(name_type))
            if value:
                add(
                    table,
                    **{key: external_id},
                    locale=language,
                    name_type=name_type,
                    value=value,
                )

    if source == "shikimori":
        rating(attrs.get("rating"))
        origin("franchise", attrs.get("franchise"))
        origin("description_source", attrs.get("description_source"))
        for name in ("anons", "ongoing", "favoured"):
            flag(name, attrs.get(name))
        for group, key in (
            ("score", "rates_scores_stats"),
            ("watch_status", "rates_statuses_stats"),
        ):
            distribution(group, attrs.get(key))
        for kind in ("fandubbers", "fansubbers", "licensors"):
            for member in _seq(attrs.get(kind)):
                name = (
                    _text(member.get("name"))
                    if isinstance(member, dict)
                    else _text(member)
                )
                if name:
                    add("anime_credits", credit_type=kind, name=name)
        licensing = _text(attrs.get("license_name_ru"))
        if licensing:
            add("anime_credits", credit_type="license_name_ru", name=licensing)
        airing = _text(attrs.get("next_episode_at"))
        if airing:
            add(
                "anime_airing",
                event_type="next_episode",
                episode_number=0,
                airs_at=airing,
            )
        for screenshot in _seq(attrs.get("screenshots")):
            for size, path in _obj(screenshot).items():
                image("screenshot." + str(size), path)
        for category, entry in (
            ("image", attrs.get("image")),
            ("poster", attrs.get("poster")),
        ):
            for size, path in _obj(entry).items():
                if isinstance(path, str):
                    image(category + "." + str(size), path)

    elif source == "kitsu":
        rating(attrs.get("ageRating"), attrs.get("ageRatingGuide"))
        origin("show_type", attrs.get("showType"))
        origin("slug", attrs.get("slug"))
        flag("nsfw", attrs.get("nsfw"))
        distribution("rating_frequency", attrs.get("ratingFrequencies"))
        airing = _text(attrs.get("nextRelease"))
        if airing:
            add(
                "anime_airing",
                event_type="next_release",
                episode_number=0,
                airs_at=airing,
            )
        # Preserve the exact dimensions supplied by the Kitsu media asset.
        for kind in ("posterImage", "coverImage"):
            media = _obj(attrs.get(kind))
            dims = _obj(_obj(media.get("meta")).get("dimensions"))
            for size, link in media.items():
                if isinstance(link, str):
                    resolved = _url(source, link)
                    if not resolved:
                        continue
                    values = _obj(dims.get(size))
                    base_type = (
                        ("poster" if kind == "posterImage" else "cover") + "." + size
                    )
                    conn.execute(
                        "UPDATE anime_images SET width=?,height=? "
                        "WHERE anime_id=? AND source_id=? AND image_type=? AND url=?",
                        (
                            _number(values.get("width")),
                            _number(values.get("height")),
                            anime_id,
                            source_id,
                            base_type,
                            resolved,
                        ),
                    )
        for relationship_type, relation in _obj(obj.get("relationships")).items():
            data = _obj(relation)
            links = _obj(data.get("links"))
            related_url = _url(source, links.get("related"))
            linked = data.get("data")
            linked_nodes = linked if isinstance(linked, list) else [linked]
            valid = False
            for node in linked_nodes:
                ref = _obj(node)
                if ref.get("id") is not None and ref.get("type"):
                    add(
                        "anime_related_resources",
                        relationship_type=str(relationship_type),
                        target_type=_text(ref["type"]),
                        target_external_id=_text(ref["id"]),
                        related_url=related_url,
                    )
                    valid = True
            if related_url and not valid:
                add(
                    "anime_related_resources",
                    relationship_type=str(relationship_type),
                    target_type="",
                    target_external_id="",
                    related_url=related_url,
                )

            if related_url:
                add(
                    "anime_collection_coverage",
                    collection_type="kitsu." + str(relationship_type),
                    fetch_state="linked_only",
                    fetched_count=len(
                        [node for node in linked_nodes if _obj(node).get("id")]
                    ),
                    has_next_page=None,
                )

    elif source == "anilist":
        origin("country_of_origin", attrs.get("countryOfOrigin"))
        origin("adaptation_source", attrs.get("source"))
        season = _text(attrs.get("season"))
        year = _number(attrs.get("seasonYear"))
        if season or year:
            add("anime_release_seasons", season=season, year=year or 0)
        for name in ("isAdult", "isLicensed"):
            flag(name, attrs.get(name))

        for row in _seq(attrs.get("rankings")):
            rank = _obj(row)
            position = _number(rank.get("rank"))
            if position is not None:
                add(
                    "anime_rankings",
                    ranking_type=_text(rank.get("type")) or "unknown",
                    ranking_format=_text(rank.get("format")),
                    ranking_context=_text(rank.get("context")),
                    season=_text(rank.get("season")),
                    year=_number(rank.get("year")) or 0,
                    all_time=int(bool(rank.get("allTime"))),
                    position=position,
                )
        upcoming = _obj(attrs.get("nextAiringEpisode"))
        if upcoming.get("airingAt") is not None:
            add(
                "anime_airing",
                event_type="next_episode",
                episode_number=_number(upcoming.get("episode")) or 0,
                airs_at=str(upcoming["airingAt"]),
            )
        for collection_name in ("characters", "staff", "relations", "studios"):
            connection = _obj(attrs.get(collection_name))
            page = _obj(connection.get("pageInfo"))
            has_next = page.get("hasNextPage")
            if isinstance(has_next, bool):
                add(
                    "anime_collection_coverage",
                    collection_type=collection_name,
                    fetch_state="partial" if has_next else "complete",
                    fetched_count=len(_seq(connection.get("edges"))),
                    has_next_page=int(has_next),
                )
            elif connection:
                add(
                    "anime_collection_coverage",
                    collection_type=collection_name,
                    fetch_state="unknown",
                    fetched_count=len(_seq(connection.get("edges"))),
                    has_next_page=None,
                )

        for kind, table in (
            ("characters", "anime_character_names"),
            ("staff", "anime_staff_names"),
        ):
            for edge in _seq(_obj(attrs.get(kind)).get("edges")):
                row = _obj(edge)
                node = _obj(row.get("node"))
                ext = _text(node.get("id"))
                if not ext:
                    continue
                named_person(table, ext, node, char=kind == "characters")
                for size, link in _obj(node.get("image")).items():
                    image(f"{kind}.{ext}.{size}", link)
                if kind == "characters":
                    for actor in _seq(row.get("voiceActors")):
                        person = _obj(actor)
                        actor_id = _text(person.get("id"))
                        if not actor_id:
                            continue
                        person_name = _text(_obj(person.get("name")).get("full"))
                        add(
                            "anime_voice_actors",
                            character_external_id=ext,
                            person_external_id=actor_id,
                            person_name=person_name,
                            language=_text(person.get("languageV2")) or "und",
                        )

        stats = _obj(attrs.get("stats"))
        for list_name, bucket_name in (
            ("scoreDistribution", "score"),
            ("statusDistribution", "status"),
        ):
            for row in _seq(stats.get(list_name)):
                data = _obj(row)
                key = _text(data.get(bucket_name))
                amount = _number(data.get("amount"))
                if key and amount is not None:
                    add(
                        "anime_rating_distributions",
                        distribution_type="anilist_" + list_name,
                        bucket=key,
                        sample_count=amount,
                    )

        for episode in _seq(attrs.get("streamingEpisodes")):
            entry = _obj(episode)
            link = _url(source, entry.get("url"))
            if link:
                add(
                    "anime_streaming_episodes",
                    title=_text(entry.get("title")) or "untitled",
                    url=link,
                    thumbnail_url=_url(source, entry.get("thumbnail")),
                )
