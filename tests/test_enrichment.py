"""Precise offline contracts for the extra typed metadata categories."""

from __future__ import annotations

import sqlite3
import unittest

from anime_db.normalizer import ingest_record
from anime_db.schema import create_catalog


def record(source: str, adb: int, ext: str, payload: dict) -> dict:
    return {
        "source": source,
        "adb_id": adb,
        "amdb_id": adb + 100,
        "external_ids": {
            source: ext,
            "mal": str(adb + 200),
            "anidb": f"a{adb}",
            "animeplanet": f"anime-{adb}",
        },
        "source_external_id": ext,
        "payload": payload,
        "fetched_at": "2026-11-07T01:00:00+00:00",
    }


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class EnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        create_catalog(self.conn, role="test")

    def tearDown(self) -> None:
        self.conn.close()

    def test_anilist_names_voice_actors_and_full_source_fields(self) -> None:
        payload = {
            "id": 12,
            "title": {"romaji": "Umi", "english": "Ocean", "native": "海"},
            "status": "FINISHED",
            "characters": {
                "pageInfo": {"hasNextPage": True},
                "edges": [
                    {
                        "role": "MAIN",
                        "node": {
                            "id": 101,
                            "name": {"full": "Kora", "native": "コラ"},
                            "image": {"large": "https://example.org/c101.png"},
                        },
                        "voiceActors": [
                            {
                                "id": 77,
                                "name": {"full": "Voice Person", "native": "声優"},
                            }
                        ],
                    }
                ],
            },
            "staff": {
                "pageInfo": {"hasNextPage": False},
                "edges": [
                    {
                        "role": "Director",
                        "node": {
                            "id": 55,
                            "name": {"full": "Director Name", "native": "監督"},
                        },
                    }
                ],
            },
            "rankings": [
                {
                    "type": "RATED",
                    "rank": 12,
                    "year": 2021,
                    "season": "SPRING",
                    "format": "TV",
                    "allTime": False,
                    "context": "highest rated",
                },
            ],
            "streamingEpisodes": [
                {
                    "title": "Episode 1",
                    "url": "https://stream.example/ep1",
                    "thumbnail": "https://stream.example/thumb1.jpg",
                },
            ],
            "stats": {
                "scoreDistribution": [{"score": 90, "amount": 200}],
                "statusDistribution": [{"status": "COMPLETED", "amount": 450}],
            },
            "countryOfOrigin": "JP",
            "source": "MANGA",
            "season": "SPRING",
            "seasonYear": 2021,
            "isAdult": False,
            "isLicensed": True,
            "coverImage": {
                "large": "https://example.org/poster.png",
                "color": "#123456",
            },
            "nextAiringEpisode": {"episode": 5, "airingAt": 1790000000},
        }
        ingest_record(self.conn, record("anilist", 1, "12", payload))
        self.assertEqual(count(self.conn, "anime_characters"), 1)
        self.assertEqual(count(self.conn, "anime_character_names"), 2)
        self.assertEqual(count(self.conn, "anime_staff_names"), 2)
        self.assertEqual(count(self.conn, "anime_voice_actors"), 1)
        self.assertEqual(count(self.conn, "anime_rankings"), 1)
        self.assertEqual(count(self.conn, "anime_streaming_episodes"), 1)
        self.assertEqual(count(self.conn, "anime_rating_distributions"), 2)
        self.assertEqual(count(self.conn, "anime_origins"), 2)
        self.assertEqual(count(self.conn, "anime_flags"), 2)
        self.assertEqual(count(self.conn, "anime_release_seasons"), 1)
        self.assertEqual(count(self.conn, "anime_airing"), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT collection_type,fetch_state FROM anime_collection_coverage "
                "ORDER BY collection_type"
            ).fetchall(),
            [("characters", "partial"), ("staff", "complete")],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT locale,name_type,value FROM anime_character_names "
                "WHERE name_type='native'"
            ).fetchone(),
            ("und", "native", "コラ"),
        )
        self.assertEqual(
            self.conn.execute("SELECT person_name FROM anime_voice_actors").fetchone()[
                0
            ],
            "Voice Person",
        )
        # Critical: a parsed child object still preserves *all* source fields,
        # including the cover color and pageInfo.hasNextPage, without a refetch.
        attributes = dict(
            self.conn.execute(
                "SELECT field_path,value_json FROM anime_source_attributes"
            ).fetchall()
        )
        self.assertIn('"color":"#123456"', attributes["coverImage"])
        self.assertIn('"hasNextPage":true', attributes["characters"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM anime_external_ids").fetchone()[0],
            4,
        )
        self.assertEqual(
            count(self.conn, "anime_age_ratings"),
            0,
        )

    def test_shikimori_relative_images_age_rating_and_credits(self) -> None:
        ingest_record(
            self.conn,
            record(
                "shikimori",
                2,
                "31",
                {
                    "id": 31,
                    "name": "Example",
                    "russian": "Пример",
                    "english": ["English title", "Other English title"],
                    "japanese": ["日本語名", "もう一つの名前"],
                    "genres": [
                        {
                            "id": 2,
                            "kind": "genre",
                            "name": "Action",
                            "russian": "Экшен",
                        },
                        {
                            "id": 17,
                            "kind": "theme",
                            "name": "School",
                            "russian": "Школа",
                        },
                    ],
                    "rating": "pg_13",
                    "kind": "tv",
                    "anons": False,
                    "ongoing": True,
                    "fandubbers": ["Dubbing Team"],
                    "fansubbers": ["Subtitle Team"],
                    "rates_scores_stats": [{"name": 9, "value": 18}],
                    "rates_statuses_stats": [{"name": "Просмотрено", "value": 52}],
                    "screenshots": [
                        {
                            "original": "/system/screenshots/original/one.jpg",
                            "preview": "/system/screenshots/x332/one.jpg",
                        }
                    ],
                    "image": {"original": "/system/animes/original/31.jpg"},
                },
            ),
        )
        self.assertEqual(count(self.conn, "anime_age_ratings"), 1)
        self.assertEqual(count(self.conn, "anime_formats"), 1)
        self.assertEqual(
            [
                value
                for (value,) in self.conn.execute(
                    "SELECT value FROM anime_titles WHERE locale='ja' ORDER BY value"
                )
            ],
            ["もう一つの名前", "日本語名"],
        )
        self.assertEqual(
            count(self.conn, "anime_taxonomy_names"),
            4,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT name FROM anime_taxonomy_names "
                "WHERE locale='ru' AND source_term_id='2'"
            ).fetchone()[0],
            "Экшен",
        )
        self.assertEqual(count(self.conn, "anime_genres"), 1)
        self.assertEqual(count(self.conn, "anime_tags"), 1)
        self.assertEqual(count(self.conn, "anime_credits"), 2)
        self.assertEqual(count(self.conn, "anime_rating_distributions"), 2)
        self.assertEqual(count(self.conn, "anime_flags"), 2)
        images = [r[0] for r in self.conn.execute("SELECT url FROM anime_images")]
        self.assertTrue(
            any(
                x.startswith("https://shikimori.io/system/screenshots/") for x in images
            )
        )
        self.assertTrue(
            any(x.startswith("https://shikimori.io/system/animes/") for x in images)
        )

    def test_kitsu_relationship_pointers_and_image_dimensions(self) -> None:
        ingest_record(
            self.conn,
            record(
                "kitsu",
                3,
                "700",
                {
                    "type": "anime",
                    "id": "700",
                    "attributes": {
                        "titles": {"en": "Example", "ja_jp": "試験"},
                        "ageRating": "PG",
                        "ageRatingGuide": "Parental Guidance",
                        "showType": "TV",
                        "nsfw": False,
                        "posterImage": {
                            "large": "https://media.kitsu.app/example.jpg",
                            "meta": {
                                "dimensions": {"large": {"width": 550, "height": 780}}
                            },
                        },
                        "ratingFrequencies": {"10": "8", "20": "10"},
                    },
                    "relationships": {
                        "animeCharacters": {
                            "links": {
                                "related": "https://kitsu.io/api/edge/anime/700/anime-characters"
                            }
                        },
                        "staff": {
                            "data": [{"type": "person", "id": "33"}],
                            "links": {
                                "related": "https://kitsu.io/api/edge/anime/700/staff"
                            },
                        },
                    },
                },
            ),
        )
        self.assertEqual(count(self.conn, "anime_related_resources"), 2)
        self.assertEqual(
            count(self.conn, "anime_collection_coverage"),
            2,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT DISTINCT fetch_state FROM anime_collection_coverage"
            ).fetchone()[0],
            "linked_only",
        )
        self.assertEqual(count(self.conn, "anime_rating_distributions"), 2)
        self.assertEqual(count(self.conn, "anime_age_ratings"), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT width,height FROM anime_images WHERE image_type='poster.large'"
            ).fetchone(),
            (550, 780),
        )
        self.assertEqual(
            self.conn.execute("SELECT rating,guide FROM anime_age_ratings").fetchone(),
            ("PG", "Parental Guidance"),
        )

    def test_conflicting_external_identity_fails_closed(self) -> None:
        ingest_record(self.conn, record("shikimori", 1, "111", {"name": "One"}))
        other = record("kitsu", 2, "222", {"attributes": {"titles": {"en": "Two"}}})
        other["external_ids"]["anidb"] = "a1"
        with self.assertRaisesRegex(ValueError, "Conflicting external identity"):
            ingest_record(self.conn, other)


if __name__ == "__main__":
    unittest.main()
