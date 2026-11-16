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
            parse_letterboxd_export(
                export_zip(
                    **{
                        "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://evil.example/film/one/\n"
                    }
                )
            )

    def test_import_does_not_treat_missing_rating_as_zero(self):
        parsed = parse_letterboxd_export(
            export_zip(
                **{
                    "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://boxd.it/Ab2\n"
                }
            )
        )
        self.assertFalse(parsed.watched[0]["rating_observed"])
        self.assertNotIn("user_rating", parsed.watched[0])

    def test_empty_watchlist_file_is_distinct_from_missing_file(self):
        data = export_zip(
            **{
                "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://boxd.it/Ab2\n",
                "watchlist.csv": "Date,Name,Year,Letterboxd URI\n",
            }
        )
        parsed = parse_letterboxd_export(data)
        self.assertTrue(parsed.watchlist_present)
        self.assertEqual(parsed.watchlist, [])


class ImportEndpointTests(unittest.TestCase):
    def test_profile_freshness_checks_never_scrape_when_disabled(self):
        account = SimpleNamespace(id=7, username="independent")
        with (
            patch.object(main, "_require_csrf"),
            patch.object(main, "_require_account", new=AsyncMock(return_value=account)),
            patch.object(main, "_auth_service", return_value=object()),
            patch.object(
                main,
                "get_settings",
                return_value=SimpleNamespace(letterboxd_scraping_enabled=False),
            ),
            patch.object(
                main, "_check_profile_watchlist_freshness", new=AsyncMock()
            ) as watchlist,
            patch.object(
                main, "_refresh_profile_favorites", new=AsyncMock()
            ) as favorites,
        ):
            client = TestClient(main.app)
            self.assertEqual(
                client.post("/api/profile/watchlist/check").json(),
                {"status": "native", "changed": False},
            )
            self.assertEqual(
                client.post("/api/profile/favorites/check").json(), {"changed": False}
            )
        watchlist.assert_not_called()
        favorites.assert_not_called()

    def _upload(self, source_username):
        import tempfile
        from app.config import Settings
        from app.sandbox import SandboxAuthService
        from app.library import Library

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        settings = Settings(
            _env_file=None,
            sandbox_mode=True,
            tmdb_api_key="",
            openai_api_key="",
            data_dir=temporary.name,
        )
        service = SandboxAuthService()
        account = service.register_native("independent", "long-enough-password").account
        data = export_zip(
            **{
                "profile.csv": "Username\n" + source_username + "\n",
                "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,One,2001,https://boxd.it/Ab2\n",
                "watchlist.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,Two,2002,https://boxd.it/Z9\n",
            }
        )
        with (
            patch.object(main, "_require_csrf"),
            patch.object(main, "_require_account", new=AsyncMock(return_value=account)),
            patch.object(main, "_auth_service", return_value=service),
            patch.object(main, "get_settings", return_value=settings),
            patch.object(Library, "start_import") as starter,
            patch.object(
                main,
                "scrape_profile",
                new=AsyncMock(side_effect=AssertionError("No Letterboxd HTML")),
            ) as scraper,
        ):
            response = TestClient(main.app).post(
                "/api/profile/import-letterboxd", content=data
            )
        self.assertEqual(response.status_code, 200, response.text)
        starter.assert_called_once()
        scraper.assert_not_called()
        return response.json(), service

    def test_import_is_durably_queued_without_scraping(self):
        result, service = self._upload("independent")
        self.assertEqual(result["watched_count"], 1)
        self.assertEqual(result["watchlist_count"], 1)
        self.assertEqual(result["import"]["state"], "queued")
        self.assertEqual(len(service._import_entries), 2)

    def test_zip_username_is_a_source_label_not_account_ownership(self):
        result, service = self._upload("letterboxd_name")
        self.assertEqual(service.account.username, "independent")
        self.assertEqual(result["import"]["source_username"], "letterboxd_name")
