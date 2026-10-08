import io
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.letterboxd_import import InvalidLetterboxdExport, parse_letterboxd_export
from app import main


def export_zip(**files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class LetterboxdExportTests(unittest.TestCase):
    def test_merges_watched_ratings_and_diary_without_duplicate_films(self):
        data = export_zip(
            **{
                "profile.csv": "Username\nexample\n",
                "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://boxd.it/Ab2\n2026-02-01,Two,2002,https://boxd.it/Z9\n",
                "ratings.csv": "Date,Name,Year,Letterboxd URI,Rating\n2026-03-01,One,2001,https://boxd.it/Ab2,4.5\n",
                "diary.csv": "Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date\n2026-04-01,One,2001,https://boxd.it/diary,5,No,,2026-04-01\n",
                "watchlist.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,Three,2003,https://boxd.it/Cd3\n",
            }
        )
        parsed = parse_letterboxd_export(data)
        self.assertEqual(parsed.username, "example")
        self.assertEqual(len(parsed.watched), 2)
        one = next(item for item in parsed.watched if item["title"] == "One")
        self.assertEqual(one["slug"], "boxd-416232")
        self.assertEqual(one["user_rating"], 4.5)
        self.assertTrue(one["rating_observed"])
        self.assertEqual(one["watched_rank"], 0)
        self.assertEqual(len(parsed.watchlist), 1)

    def test_rejects_missing_watched_files_and_unsafe_uri(self):
        with self.assertRaises(InvalidLetterboxdExport):
            parse_letterboxd_export(export_zip(**{"watchlist.csv": "Name\nOne\n"}))
        with self.assertRaises(InvalidLetterboxdExport):
            parse_letterboxd_export(export_zip(**{
                "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://evil.example/film/one/\n"
            }))

    def test_import_does_not_treat_missing_rating_as_zero(self):
        parsed = parse_letterboxd_export(export_zip(**{
            "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://boxd.it/Ab2\n"
        }))
        self.assertFalse(parsed.watched[0]["rating_observed"])
        self.assertNotIn("user_rating", parsed.watched[0])

    def test_empty_watchlist_file_is_distinct_from_missing_file(self):
        data = export_zip(**{
            "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://boxd.it/Ab2\n",
            "watchlist.csv": "Date,Name,Year,Letterboxd URI\n",
        })
        parsed = parse_letterboxd_export(data)
        self.assertTrue(parsed.watchlist_present)
        self.assertEqual(parsed.watchlist, [])


class ImportEndpointTests(unittest.TestCase):
    def test_export_backed_profile_checks_do_not_scrape(self):
        job = {"scope": "full", "cursor_page": 0, "state": "done"}
        service = SimpleNamespace(get_sync_job=lambda _uid: job)
        account = SimpleNamespace(id=7, username="example")
        with (
            patch.object(main, "_require_csrf"),
            patch.object(main, "_require_account", new=AsyncMock(return_value=account)),
            patch.object(main, "_auth_service", return_value=service),
            patch.object(main, "_check_profile_watchlist_freshness", new=AsyncMock()) as watchlist_scrape,
            patch.object(main, "_refresh_profile_favorites", new=AsyncMock()) as favorites_scrape,
            TestClient(main.app) as client,
        ):
            watchlist = client.post("/api/profile/watchlist/check")
            favorites = client.post("/api/profile/favorites/check")
        self.assertEqual(watchlist.json(), {"status": "export", "changed": False})
        self.assertEqual(favorites.json(), {"changed": False})
        watchlist_scrape.assert_not_awaited()
        favorites_scrape.assert_not_awaited()

    def test_import_queues_enrichment_without_scraping(self):
        class Service:
            def __init__(self):
                self.rows = []

            def get_sync_job(self, _uid):
                return None

            def check_sync_schema(self):
                pass

            def get_watched_films(self, _uid):
                return []

            def save_watched_films(self, _uid, rows):
                self.rows.extend(rows)

            def upsert_sync_job(self, _uid, **fields):
                self.job = fields
                return fields

            def mark_sync_status(self, _uid, status):
                self.status = status

        class Cache:
            def __init__(self):
                self.values = {}

            def set(self, namespace, key, value):
                self.values[(namespace, key)] = value

        data = export_zip(**{
            "profile.csv": "Username\nexample\n",
            "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://boxd.it/Ab2\n",
            "watchlist.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,Two,2002,https://boxd.it/Z9\n",
        })
        service = Service()
        cache = Cache()
        account = SimpleNamespace(id=7, username="example")
        with (
            patch.object(main, "_require_csrf"),
            patch.object(main, "_require_account", new=AsyncMock(return_value=account)),
            patch.object(main, "_auth_service", return_value=service),
            patch.object(main.profile_sync, "is_running", return_value=False),
            patch.object(main.profile_sync, "start") as start,
            patch.object(main, "_make_cache", return_value=(None, None)),
            patch.object(main, "_make_persistent_cache", return_value=cache),
            TestClient(main.app) as client,
        ):
            response = client.post("/api/profile/import-letterboxd", content=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["watched_count"], 1)
        self.assertEqual(response.json()["watchlist_count"], 1)
        self.assertEqual(service.job["scope"], "full")
        self.assertEqual(service.job["cursor_page"], 0)
        self.assertEqual(service.status, "syncing")
        self.assertEqual(service.rows[0]["user_rating"] if "user_rating" in service.rows[0] else None, None)
        self.assertIn(("films_watchlist", "example"), cache.values)
        start.assert_called_once()

    def test_rejects_someone_elses_export(self):
        data = export_zip(**{
            "profile.csv": "Username\nother\n",
            "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://boxd.it/Ab2\n",
        })
        service = SimpleNamespace(get_sync_job=lambda _uid: None)
        with (
            patch.object(main, "_require_csrf"),
            patch.object(main, "_require_account", new=AsyncMock(return_value=SimpleNamespace(id=7, username="example"))),
            patch.object(main, "_auth_service", return_value=service),
            patch.object(main.profile_sync, "is_running", return_value=False),
            TestClient(main.app) as client,
        ):
            response = client.post("/api/profile/import-letterboxd", content=data)
        self.assertEqual(response.status_code, 400)
