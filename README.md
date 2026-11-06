> **AI-assisted project:** Developed with the assistance of AI.

# anime-db

A standalone, provenance-aware **full anime metadata SQLite database**. Builds identity-stable anime metadata from independently collected Shikimori, Kitsu and AniList records. The identity reference is obtained from [anime-mapping-db](https://github.com/anime-db-project/anime-mapping-db), but a released database does not depend on that database at runtime. [anime-timestamp-db](https://github.com/anime-db-project/anime-timestamp-db) remains a separate project.

**Status: experimental schema v1. No Releases are enabled.** Automated test runs save database packages as [GitHub Actions artifacts](../../actions/workflows/build.yml), not GitHub Releases.

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
- Native IDs are available in anime_external_ids. Search and joins work without installing mappings.db. The identity registry is persisted in the state branch and is never regenerated from the position of a source response.

AMDB anchors are read from the latest mapping reference only during a build. We never copy mapping evidence, guess links from title similarity, or modify the mapping project's data.

## Structure

Each kind of fact has a semantic table: anime_titles (locale, source_field, title_type), anime_descriptions, anime_statuses, anime_formats, anime_dates, anime_episode_counts, anime_durations, anime_scores, anime_statistics, anime_genres, anime_tags, anime_studios, anime_characters, anime_staff, anime_relations, anime_episodes, anime_images, anime_videos, anime_links and anime_source_attributes.

Facts may have multiple candidates. **Aggregation never discards source values.** If candidates disagree, aggregation_summary marks a conflict and leaves selected_json NULL instead of choosing an unverified answer. A source's localized field stays in its own typed locale column, not in an unstructured aliases array. Unmodeled fields stay in anime_source_attributes and full raw source responses are retained in anime.technical.db.

## Test workflow

The per-provider configuration is at config/sources.json. Test builds use a **hard cap of 10 HTTP requests per source per workflow**, independent per-second/per-minute/per-hour limits, jitter, once-only 429 retry, source-aware checkpoints and a transparent project User-Agent. A 403 stops that provider without CAPTCHA workarounds.

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
