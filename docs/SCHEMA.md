# Anime DB schema v1

The runtime package uses normalized SQLite categories, not a raw JSON column for the entire title. The main anime table contains only the build-local row key and the two public identities. All source values remain queryable without any adjacent .db package.

## Identity rules

1. Persistent adb_id values are allocated via the state/registry.json registry (not row order). Old IDs are not recycled.
2. amdb_id links are optional and refer to public anchor IDs, **not** mapping build-local entity IDs.
3. Source IDs are unique per provider in anime_external_ids. Conflicts fail closed.
4. No guesses based on overlapping titles or partial strings. Splits, merges and redirects will be explicit migrations, not silent graph merges.

## Semantic tables

- anime_titles / anime_descriptions have locale **and** source_field columns and preserve source identity.
- anime_statuses, anime_formats, anime_episode_counts, anime_dates, anime_durations, anime_scores, anime_statistics capture multiple source candidates.
- anime_genres, anime_tags, anime_studios, anime_characters, anime_staff, anime_relations, anime_episodes capture structured lists.
- anime_images, anime_videos, anime_links capture metadata and URL references.
- anime_source_attributes preserves unmapped source-specific JSON attributes.
- aggregation_summary flags conflicts and never manufactures consensus.
- anime.technical.db holds complete compressed API JSON snapshots, HTTP events and provenance.
- anime.images.db contains a BLOB store with SHA-256 unique bytes for future image imports.

## Distribution

All .db files are SQLite, read-only after download, each shipped together with .db.zst. Language packages have stable public IDs and localized text, but are not the only home for a language; **anime.db has everything**. Source packages filter to records actually fetched from that source.

Releases remain disabled until the schema and external semantics are verified with real fixtures.
