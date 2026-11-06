"""Versioned normalized SQLite schema. Build outputs have independent roles."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1
LOCALES = ("ru", "en", "ja", "de", "es", "fr", "it")
SOURCES = ("shikimori", "kitsu", "anilist")

# Every semantic category has its own typed relation; extra upstream fields
# remain source-specific instead of being dropped or mislabelled as aliases.
SCALAR_FIELDS = {
    "anime_statuses": ("status TEXT NOT NULL", "status"),
    "anime_formats": ("format TEXT NOT NULL", "format"),
    "anime_episode_counts": ("count_type TEXT NOT NULL, value INTEGER NOT NULL", "count_type, value"),
    "anime_durations": ("duration_type TEXT NOT NULL, seconds INTEGER NOT NULL", "duration_type, seconds"),
    "anime_dates": ("date_type TEXT NOT NULL, value TEXT NOT NULL, precision TEXT NOT NULL", "date_type, value, precision"),
    "anime_scores": ("score_type TEXT NOT NULL, value REAL NOT NULL, scale REAL NOT NULL", "score_type, value, scale"),
    "anime_statistics": ("metric TEXT NOT NULL, value REAL NOT NULL", "metric, value"),
}

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE anime (
    anime_id INTEGER PRIMARY KEY,
    adb_id INTEGER NOT NULL UNIQUE CHECK(adb_id > 0),
    amdb_id INTEGER UNIQUE CHECK(amdb_id > 0)
);
CREATE INDEX anime_amdb_idx ON anime(amdb_id) WHERE amdb_id IS NOT NULL;

CREATE TABLE id_redirects (
    old_adb_id INTEGER PRIMARY KEY,
    new_adb_id INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE amdb_redirects (
    old_amdb_id INTEGER PRIMARY KEY,
    new_amdb_id INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE source_catalog (
    source_id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE
);
CREATE TABLE anime_external_ids (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    external_id TEXT NOT NULL,
    PRIMARY KEY (source_id, external_id),
    UNIQUE(anime_id, source_id, external_id)
) WITHOUT ROWID;
CREATE INDEX ext_by_title ON anime_external_ids(anime_id, source_id);

CREATE TABLE anime_sources (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    source_external_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(anime_id, source_id)
) WITHOUT ROWID;

CREATE TABLE anime_titles (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    locale TEXT NOT NULL,
    title_type TEXT NOT NULL,
    source_field TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(anime_id, source_id, locale, title_type, source_field, value)
) WITHOUT ROWID;
CREATE INDEX title_by_locale ON anime_titles(locale, value);
CREATE VIRTUAL TABLE anime_title_fts USING fts5(
    value, locale UNINDEXED, adb_id UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE anime_descriptions (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    locale TEXT NOT NULL,
    description_type TEXT NOT NULL,
    source_field TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(anime_id, source_id, locale, description_type, source_field)
) WITHOUT ROWID;

CREATE TABLE anime_genres (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    genre TEXT NOT NULL,
    PRIMARY KEY(anime_id, source_id, genre)
) WITHOUT ROWID;

CREATE TABLE anime_tags (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    tag TEXT NOT NULL,
    rank INTEGER,
    is_spoiler INTEGER,
    PRIMARY KEY(anime_id, source_id, tag)
) WITHOUT ROWID;

CREATE TABLE anime_studios (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    studio_external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    studio_role TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(anime_id, source_id, studio_external_id, studio_role)
) WITHOUT ROWID;

CREATE TABLE anime_characters (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    character_external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    details_json TEXT,
    PRIMARY KEY(anime_id, source_id, character_external_id, role)
) WITHOUT ROWID;

CREATE TABLE anime_staff (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    person_external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    details_json TEXT,
    PRIMARY KEY(anime_id, source_id, person_external_id, role)
) WITHOUT ROWID;

CREATE TABLE anime_relations (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    relation_type TEXT NOT NULL,
    target_external_id TEXT NOT NULL,
    target_adb_id INTEGER,
    PRIMARY KEY(anime_id, source_id, relation_type, target_external_id)
) WITHOUT ROWID;

CREATE TABLE anime_episodes (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    episode_external_id TEXT NOT NULL,
    season INTEGER,
    episode_number INTEGER,
    title TEXT,
    locale TEXT NOT NULL DEFAULT 'und',
    aired_at TEXT,
    details_json TEXT,
    PRIMARY KEY(anime_id, source_id, episode_external_id)
) WITHOUT ROWID;

CREATE TABLE anime_images (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    image_type TEXT NOT NULL,
    url TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    PRIMARY KEY(anime_id, source_id, image_type, url)
) WITHOUT ROWID;

CREATE TABLE anime_videos (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    video_type TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    PRIMARY KEY(anime_id, source_id, video_type, url)
) WITHOUT ROWID;

CREATE TABLE anime_links (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    site TEXT NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY(anime_id, source_id, site, url)
) WITHOUT ROWID;

CREATE TABLE anime_source_attributes (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
    field_path TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY(anime_id, source_id, field_path)
) WITHOUT ROWID;

CREATE TABLE aggregation_summary (
    anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
    field_name TEXT NOT NULL,
    distinct_values INTEGER NOT NULL,
    selected_json TEXT,
    has_conflict INTEGER NOT NULL CHECK(has_conflict IN (0,1)),
    PRIMARY KEY(anime_id, field_name)
) WITHOUT ROWID;
"""


def create_catalog(conn: sqlite3.Connection, *, role: str) -> None:
    conn.executescript(SCHEMA)
    for table, (columns, keys) in SCALAR_FIELDS.items():
        conn.execute(
            f"""
            CREATE TABLE {table} (
                anime_id INTEGER NOT NULL REFERENCES anime(anime_id),
                source_id INTEGER NOT NULL REFERENCES source_catalog(source_id),
                {columns},
                PRIMARY KEY(anime_id, source_id, {keys})
            ) WITHOUT ROWID
            """
        )
    conn.executemany(
        "INSERT INTO source_catalog(source_id,source_key) VALUES(?,?)",
        ((n + 1, name) for n, name in enumerate((*SOURCES, "mal"))),
    )
    conn.executemany(
        "INSERT INTO metadata(key,value) VALUES(?,?)",
        (("schema_version", str(SCHEMA_VERSION)), ("database_role", role)),
    )
    conn.commit()


def create_technical(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE raw_snapshots (
            source_key TEXT NOT NULL,
            external_id TEXT NOT NULL,
            adb_id INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            raw_zlib BLOB NOT NULL,
            PRIMARY KEY(source_key,external_id)
        ) WITHOUT ROWID;
        CREATE TABLE request_log (
            id INTEGER PRIMARY KEY,
            source_key TEXT NOT NULL,
            external_id TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            elapsed_ms INTEGER NOT NULL,
            detail TEXT,
            recorded_at TEXT NOT NULL
        );
        CREATE TABLE aggregation_decisions (
            adb_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            value_json TEXT,
            conflict INTEGER NOT NULL,
            candidates_json TEXT NOT NULL,
            PRIMARY KEY(adb_id,field_name)
        ) WITHOUT ROWID;
    """)
    conn.executemany(
        "INSERT INTO metadata(key,value) VALUES(?,?)",
        (("schema_version", str(SCHEMA_VERSION)), ("database_role", "technical")),
    )
    conn.commit()


def create_images(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE image_blobs(
            image_id INTEGER PRIMARY KEY,
            sha256 BLOB NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            data BLOB NOT NULL
        );
        CREATE TABLE image_references(
            adb_id INTEGER NOT NULL,
            image_type TEXT NOT NULL,
            image_sha256 BLOB NOT NULL,
            PRIMARY KEY(adb_id,image_type,image_sha256)
        ) WITHOUT ROWID;
    """)
    conn.executemany(
        "INSERT INTO metadata(key,value) VALUES(?,?)",
        (("schema_version", str(SCHEMA_VERSION)), ("database_role", "images")),
    )
    conn.commit()
