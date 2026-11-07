> **AI-assisted project:** Developed with the assistance of AI.

# anime-db

A standalone, provenance-aware **full anime metadata SQLite database**. Builds identity-stable anime metadata from independently collected Shikimori, Kitsu and AniList records. The identity reference is obtained from [anime-mapping-db](https://github.com/anime-db-project/anime-mapping-db), but a released database does not depend on that database at runtime. [anime-timestamp-db](https://github.com/anime-db-project/anime-timestamp-db) remains a separate project.

**Status: experimental schema v2. No Releases are enabled.** Automated test runs save database packages as [GitHub Actions artifacts](../../actions/workflows/build.yml), not GitHub Releases.

## Database files

Every SQLite file is shipped **twice**: a directly usable .db and a compressed .db.zst copy. There is only **one full catalog**, anime.db. There is no anime-full.db and no reduced alternative masquerading as the full database.

| Filename | Contents |
| --- | --- |
| anime.db | Complete normalized metadata: all supported sources, every available language, preserved candidate values, selected / conflicted facts, relations and media URLs |
| anime.ru.db, anime.en.db, anime.ja.db, anime.de.db, anime.es.db, anime.fr.db, anime.it.db | Independent language-focused SQLite packages with localized texts and public IDs |
| anime.shikimori.db, anime.kitsu.db, anime.anilist.db | Source-specific semantic metadata including its native fields |
| anime.technical.db | Full compressed API response snapshots, crawl events and aggregation decisions |
| anime.images.db | Separate binary-image storage prepared for future controlled image imports (empty in the test phase) |
| manifest.json, SHA256SUMS | Counts, schema version, sizes and checksums |

The full database stores **image URLs and image metadata**. Actual image BLOBs belong in anime.images.db; image downloading is intentionally disabled while API ingestion is limited to 10 requests per source.

## Identity

- **adb:<id>** — permanent, public project identity (allocated by anime-db).
- **amdb:<anchor_id>** — independent public matching identity (optionally linked).
- **anime_id** — build-local SQLite key, never persist it between versions.
- Native IDs from all mapped external provider namespaces are indexed in anime_external_ids. Search and joins work without installing mappings.db. The identity registry is persisted in the state branch and is never regenerated from the position of a source response.

AMDB anchors are read from the latest mapping reference only during a build. We never copy mapping evidence, guess links from title similarity, or modify the mapping project's data.

## Structure

Each kind of fact has a semantic table: anime_titles (locale, source_field, title_type), anime_descriptions, anime_statuses, anime_formats, anime_dates, anime_episode_counts, anime_durations, anime_scores, anime_statistics, anime_genres, anime_tags, anime_studios, anime_characters, anime_staff, anime_relations, anime_episodes, anime_images, anime_videos, anime_links and anime_source_attributes.

Facts may have multiple candidates. **Aggregation never discards source values.** If candidates disagree, aggregation_summary marks a conflict and leaves selected_json NULL instead of choosing an unverified answer. A source's localized field stays in its own typed locale column, not in an unstructured aliases array. Shikimori multi-title fields are expanded into individual titles; localized genres/themes are indexed by their source taxonomy IDs. Unmodeled fields stay in anime_source_attributes and full raw source responses are retained in anime.technical.db.

## Test workflow

The per-provider configuration is at config/sources.json. Test builds use a **hard cap of 10 HTTP requests per source per workflow**, independent per-second/per-minute/per-hour limits, jitter, once-only 429 retry, source-aware checkpoints and a transparent project User-Agent. A 403 stops that provider without CAPTCHA workarounds. Every source has a 14-day refresh TTL, with at most 2 stale-record refresh calls sharing the same 10-call limit with new titles. Old snapshots gain new mapping IDs without any network request.

- CI runs offline schema/identity/normalization tests.
- Database test build runs after code changes or manually and saves SQLite artifacts.
- The release workflow is prepared as an **inactive template**. Publishing is disabled until after the architecture is validated.

### Local commands

Python 3.11+:

~~~bash
python -m pip install -e .
anime-db run --mapping-db /path/to/mappings.db --state-dir .state --output-dir artifacts --max-requests 10
anime-db build --state-dir .state --output-dir artifacts
~~~

Every build validates SQLite integrity, runs ANALYZE/VACUUM, and generates both .db and Zstandard compressed copies.

## Data permissions

Provider rights, attribution and access conditions remain source-specific. An anonymous HTTP endpoint is not automatically permission for bulk copying or redistribution. The maintainer reports authorization for the intended source integrations; each adapter stays bounded and can be disabled independently. No user accounts, tokens or image file bytes are downloaded as part of the test-phase catalog crawl.

## Sources

- [Shikimori API](https://shikimori.one/api/doc) — REST anime metadata.
- [Kitsu anime catalog API](https://github.com/hummingbird-me/api-docs) — anime JSON:API (kitsu.io/api/edge, not the unrelated CGWire production app).
- [AniList GraphQL](https://docs.anilist.co/guide/graphql/) — public Media query by anime ID; observe [terms](https://docs.anilist.co/guide/terms-of-use).

### New v2 metadata features

The v2 schema adds semantic tables for age ratings, origins, season/year,
user-rating distributions, tags and taxonomy translations, staff/character
names and voice actors, media rankings, credits and related resources.

Provider pagination is explicitly modeled. AniList character/staff/relation
lists may be partially fetched; the anime_collection_coverage table records
whether the current page is complete. Kitsu relationship URLs are recorded as
linked-only until they are independently fetched. A preserved original API
response is not proof that every linked resource has been retrieved.

The mapping reference now supplies all external ID namespaces for each
candidate, rather than just the three current crawler providers. The current
reference contains 30,003 candidate AMDB anchors with at least one usable
source ID. The hard 10-request limit remains enforced per source per run.

### Standalone search examples

    anime-db search 'adb:1' --db anime.db
    anime-db search 'amdb:1' --db anime.db
    anime-db search 'anidb:114' --db anime.db
    anime-db search 'Здесь и сейчас' --db anime.db

Source IDs and titles above are illustrative of a test snapshot. Stable ADB
identities must be read from this project's registry, not guessed from position.

Remaining work: deeper Kitsu related-record API ingestion, later AniList pages,
episode details and binary images. None are represented as fully collected yet.
