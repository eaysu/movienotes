import asyncio
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.main import (
    BlendRequest,
    LocalePreferenceRequest,
    RandomRequest,
    RecommendRequest,
    _load_user_films,
)
from app.scraper import (
    AccessBlockedError,
    EmptyListError,
    MarkupChangedError,
    PrivateListError,
    ScrapedFilm,
    _empty_page_error,
    _parse_page,
    scrape_watched,
    scrape_watchlist,
)


class UsernameValidationTests(unittest.TestCase):
    def test_normalizes_username(self):
        self.assertEqual(RecommendRequest(username="  @Film_Fan_7 ").username, "film_fan_7")
        self.assertEqual(RandomRequest(username="MOVIELOVER").username, "movielover")

    def test_normalizes_both_blend_usernames(self):
        request = BlendRequest(username1=" @First_User", username2="SECOND_USER ")
        self.assertEqual(request.username1, "first_user")
        self.assertEqual(request.username2, "second_user")

    def test_rejects_path_and_query_injection(self):
        for username in (
            "../films",
            "user/name",
            "user?sort=popular",
            "has-a-hyphen",
            "x",
            "sixteen_chars_xx",
            "",
            "@",
        ):
            with self.subTest(username=username), self.assertRaises(ValidationError):
                RecommendRequest(username=username)


class LocaleValidationTests(unittest.TestCase):
    def test_accepts_supported_locale_preferences_only(self):
        self.assertEqual(LocalePreferenceRequest(locale="EN").locale, "en")
        self.assertEqual(LocalePreferenceRequest(locale="auto").locale, "auto")
        with self.assertRaises(ValidationError):
            LocalePreferenceRequest(locale="de")


class ScraperParserTests(unittest.TestCase):
    def test_parses_current_and_legacy_poster_attributes(self):
        current = """
        <div data-item-slug="perfect-days"
             data-item-full-display-name="Perfect Days (2023)">
          <img src="https://a.ltrbxd.com/perfect-days.jpg">
        </div>
        <div data-item-slug="perfect-days" data-item-name="Duplicate"></div>
        """
        legacy = """
        <div data-film-slug="in-the-mood-for-love-2000"
             data-film-name="In the Mood for Love"
             data-film-release-year="2000"></div>
        """

        films = _parse_page(current)
        legacy_films = _parse_page(legacy)

        self.assertEqual(len(films), 1)
        self.assertEqual((films[0].title, films[0].year), ("Perfect Days", 2023))
        self.assertEqual(films[0].poster_url, "https://a.ltrbxd.com/perfect-days.jpg")
        self.assertEqual(
            (legacy_films[0].title, legacy_films[0].year),
            ("In the Mood for Love", 2000),
        )

    def test_films_grid_rating_is_parsed_from_rated_class(self):
        html = """
        <li class="poster-container">
          <div data-film-slug="stalker" data-film-name="Stalker"
               data-film-release-year="1979"></div>
          <p class="poster-viewingdata"><span class="rating rated-9">★★★★½</span></p>
        </li>
        <li class="poster-container">
          <div data-film-slug="solaris" data-film-name="Solaris"></div>
        </li>
        """
        films = {f.slug: f for f in _parse_page(html)}
        self.assertEqual(films["stalker"].user_rating, 4.5)
        self.assertIsNone(films["solaris"].user_rating)

    def test_empty_page_failures_are_classified(self):
        cases = (
            ("<main>This member's profile is private</main>", PrivateListError),
            ("<main>Your watchlist is empty</main>", EmptyListError),
            ("<title>Just a moment...</title><div id='cf-chl-test'></div>", AccessBlockedError),
            ("<main>Unexpected redesigned page</main>", MarkupChangedError),
        )
        for html, expected_type in cases:
            with self.subTest(expected_type=expected_type):
                self.assertIsInstance(
                    _empty_page_error("film_fan", "watchlist", html),
                    expected_type,
                )


class WatchedRatingMergeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rss_rating_is_merged_into_diary_duplicate(self):
        diary = [
            ScrapedFilm(title="Perfect Days", year=2023, slug="perfect-days"),
            ScrapedFilm(title="Past Lives", year=2023, slug="past-lives"),
        ]
        rss = [
            ScrapedFilm(
                title="Perfect Days",
                year=2023,
                slug="perfect-days",
                user_rating=4.5,
            )
        ]
        with (
            patch("app.scraper.scrape_diary", return_value=(diary, True)),
            patch("app.scraper._scrape_watched_rss", return_value=rss),
        ):
            films, complete = await scrape_watched("film_fan", film_limit=2)

        self.assertTrue(complete)
        self.assertEqual(films[0].user_rating, 4.5)


class SingleFlightTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_list_scrapes_only_once(self):
        calls = 0

        async def fake_scrape(_username, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return [ScrapedFilm(title="Perfect Days", year=2023, slug="perfect-days")], True

        class EmptyCache:
            def get_with_freshness(self, *_args, **_kwargs):
                return None

            def set(self, *_args, **_kwargs):
                return None

        kwargs = dict(
            username="film_fan",
            list_type="watched",
            settings=None,
            enricher=None,
            pcache=EmptyCache(),
            scrape_kwargs={},
        )
        with patch("app.main.scrape_watched", side_effect=fake_scrape):
            first, second = await asyncio.gather(
                _load_user_films(**kwargs),
                _load_user_films(**kwargs),
            )

        self.assertEqual(calls, 1)
        self.assertEqual(first[0][0].slug, "perfect-days")
        self.assertEqual(second[0][0].slug, "perfect-days")

    async def test_random_and_recommend_can_share_same_watchlist_scrape(self):
        calls = 0

        async def fake_list(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return [ScrapedFilm(title="Perfect Days", year=2023, slug="perfect-days")], True

        with patch("app.scraper._scrape_list", side_effect=fake_list):
            first, second = await asyncio.gather(
                scrape_watchlist("film_fan", delay=0, max_pages=1, film_limit=1),
                scrape_watchlist("film_fan", delay=0, max_pages=1, film_limit=1),
            )

        self.assertEqual(calls, 1)
        self.assertEqual(first[0][0].slug, second[0][0].slug)


if __name__ == "__main__":
    unittest.main()
