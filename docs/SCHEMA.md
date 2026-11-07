# anime-db schema v2

anime.db is the only full metadata catalog. It is self-contained, with
source candidate values and public ADB/AMDB/native lookup; the language,
source, technical and image databases are supplementary distributions.

## Stable identifiers

- anime.anime_id: private SQLite row number, never an external identity.
- anime.adb_id: durable public title ID (adb:<number>) allocated in the
  registry persisted on the GitHub state branch.
- anime.amdb_id: optional public AMDB anchor (amdb:<number>).
- anime_external_ids: every supported mapping namespace is represented as
  a unique pair (provider, external_id), not limited to crawler sources.
- The mapping database is a reference only at build time. The runtime
  does not bundle its nodes, evidence records or graph.
- Conflicting associations must fail closed, not silently merge two titles.

## Typed tables

Every source-specific field includes a source_id; every localized title,
description, person name or taxonomy term has an explicit locale column.

| Category | Tables |
| --- | --- |
| Text and translations | anime_titles, anime_descriptions, anime_character_names, anime_staff_names, anime_taxonomy_names |
| Classification | anime_statuses, anime_formats, anime_age_ratings, anime_origins, anime_release_seasons, anime_flags |
| Seasons and viewing | anime_episode_counts, anime_episodes, anime_durations, anime_dates, anime_airing |
| Statistics | anime_scores, anime_statistics, anime_rankings, anime_rating_distributions |
| People and relations | anime_studios, anime_characters, anime_staff, anime_voice_actors, anime_relations, anime_related_resources, anime_credits |
| Themes and media | anime_genres, anime_tags, anime_images, anime_videos, anime_links, anime_streaming_episodes |
| Provenance and coverage | anime_sources, anime_source_attributes, anime_collection_coverage, aggregation_summary |

Shikimori english and japanese title arrays become multiple localized
records, never a Python list rendered as one title. Its Russian theme and
genre translations use explicit (source_term_id, locale) associations.

Independent source values are never overwritten by aggregation. A clear,
unambiguous value may be selected; otherwise aggregation_summary marks
has_conflict=1 and leaves selected_json empty.

## Nested collection completeness

anime_collection_coverage.fetch_state is one of:
- complete: upstream explicitly reports no next page;
- partial: the first fetched page has more data;
- unknown: no definitive pagination result was returned;
- linked_only: Kitsu returned a relationship URL without its full contents.

The AniList request currently retrieves up to 25 first-page character and
staff edges. Neither those pages nor Kitsu linked resources should be treated
as exhaustively collected unless the provider explicitly confirms it.

## Data preservation and export

The normalized main database includes source-specific unmodeled fields in
anime_source_attributes. Original compressed source JSON plus crawl-request
diagnostics live in anime.technical.db. This allows new typed fields to be
derived from existing snapshots without another API request.

Binary images are planned for anime.images.db with SHA-256 deduplication.
This phase stores image URLs and image dimensions, not the actual bytes.
Each distributed SQLite package is available in two forms: .db and .db.zst.

## Incremental test crawling

- The complete read-only mapping reference currently yields 30,003 eligible
  AMDB identities (not all have records from all three APIs).
- Every enabled source is capped at 10 actual HTTP requests per workflow,
  including any revalidation of old cached records.
- Separate rate limits per second, minute and hour, randomized gaps,
  bounded retry handling for 429, and no Cloudflare CAPTCHA workaround.
- Snapshots are reused for new builds, and newly mapped external IDs can
  be backfilled with zero API calls.
- Per-source refresh TTL: 14 days; up to 2 of the 10 requests may be
  reserved for stale titles in a mixed new-and-old crawl.
- A 401/403/429 response stops any additional traffic to that provider.

## Distribution status

Experimental schema v2. GitHub Releases are intentionally disabled. The
inactive template remains .github/workflows/release.yml.disabled; runnable
test database packages are available only as GitHub Actions artifacts.
