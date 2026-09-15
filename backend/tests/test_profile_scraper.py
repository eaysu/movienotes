import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.scraper import (
    MarkupChangedError,
    _LetterboxdRequestBudget,
    _fetch_profile_with_fresh_sessions,
    _parse_page,
    _parse_profile_page,
    _scrape_list,
    _resolve_missing_posters,
)


FIXTURE = Path(__file__).parent / "fixtures" / "profile_public.html"


class ProfileParserTests(unittest.TestCase):
    def test_lazy_poster_recipe_is_kept_only_for_missing_image(self):
        films = _parse_page(
            '''
            <div data-item-slug="the-queens-gambit"
                 data-item-name="The Queen's Gambit (2020)"
                 data-resolvable-poster-path='{"posteredBaseLink":"/film/the-queens-gambit/","hasDefaultPoster":true,"cacheBustingKey":"abc123"}'>
              <img src="https://s.ltrbxd.com/static/img/empty-poster-125.png"/>
            </div>
            '''
        )
        self.assertEqual(len(films), 1)
        self.assertIsNone(films[0].poster_url)
        self.assertEqual(
            films[0].poster_resolver_url,
            "https://letterboxd.com/film/the-queens-gambit/poster/std/230/?k=abc123",
        )

    def test_parses_avatar_identity_bio_and_ordered_favorite_four(self):
        profile = _parse_profile_page("sample_user", FIXTURE.read_text())

        self.assertEqual(profile.username, "sample_user")
        self.assertEqual(profile.display_name, "Sample User")
        self.assertEqual(
            profile.avatar_url,
            "https://a.ltrbxd.com/resized/avatar/sample-large.jpg",
        )
        self.assertEqual(profile.bio, "slow cinema & impossible romances")
        self.assertEqual(
            [film.slug for film in profile.favorite_films],
            ["first-film", "second-film", "third-film", "fourth-film"],
        )
        self.assertEqual(profile.favorite_films[0].year, 2001)
        self.assertIsNone(profile.favorite_films[0].poster_url)

    def test_missing_profile_summary_is_classified_as_markup_change(self):
        with self.assertRaises(MarkupChangedError):
            _parse_profile_page("sample_user", "<main>redesigned profile</main>")

    def test_parses_public_profile_statistics(self):
        html = """
        <section class="profile-summary">
          <div class="person-display-name"><span class="label">Sample User</span></div>
          <h4 class="profile-statistic statistic"><a href="/u/films/"><span class="value">1,204</span><span class="definition">Films</span></a></h4>
          <h4 class="profile-statistic statistic"><a href="/u/diary/for/2026/"><span class="value">73</span><span class="definition">This year</span></a></h4>
          <h4 class="profile-statistic statistic"><a href="/u/followers/"><span class="value">5</span><span class="definition">Followers</span></a></h4>
        </section>
        """
        profile = _parse_profile_page("sample_user", html)
        self.assertEqual(profile.stats.get("films"), 1204)
        self.assertEqual(profile.stats.get("this_year"), 73)
        self.assertEqual(profile.stats.get("followers"), 5)
        self.assertIn("stats", profile.to_dict())


class ProfileRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_list_keeps_the_blocked_page_as_next_checkpoint(self):
        page_one = """
        <div data-item-slug="perfect-days" data-item-name="Perfect Days (2023)">
          <img src="https://a.ltrbxd.com/perfect-days.jpg" />
        </div>
        """

        class FakeSession:
            get_calls = 0

            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, *_args, **_kwargs):
                type(self).get_calls += 1
                raise AssertionError("successful list crawls must not warm up first")

        async def fetch(_session, _url, _referer, **_kwargs):
            if not hasattr(fetch, "calls"):
                fetch.calls = 0
            fetch.calls += 1
            if fetch.calls == 1:
                return SimpleNamespace(status_code=200, text=page_one), 200
            return SimpleNamespace(status_code=403, text="blocked"), 403

        with (
            patch("app.scraper.AsyncSession", FakeSession),
            patch("app.scraper._fetch_with_retry", side_effect=fetch),
            patch("app.scraper._human_pause", new=AsyncMock()),
        ):
            result = await _scrape_list(
                "sample_user", "films", max_pages=4, delay=0, max_retries=1
            )

        self.assertEqual([film.slug for film in result.films], ["perfect-days"])
        self.assertFalse(result.complete)
        self.assertFalse(result.exhausted)
        self.assertEqual(result.next_page, 2)
        self.assertEqual(result.pages_fetched, 1)
        self.assertEqual(FakeSession.get_calls, 0)

    async def test_lazy_poster_resolver_prefers_high_resolution_url(self):
        film = _parse_page(
            '''
            <div data-item-slug="the-queens-gambit"
                 data-item-name="The Queen's Gambit (2020)"
                 data-resolvable-poster-path='{"posteredBaseLink":"/film/the-queens-gambit/","hasDefaultPoster":true}'>
              <img src="https://s.ltrbxd.com/static/img/empty-poster-125.png"/>
            </div>
            '''
        )[0]

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "url": "https://a.ltrbxd.com/poster-230.jpg",
                    "url2x": "https://a.ltrbxd.com/poster-460.jpg",
                }

        session = SimpleNamespace(get=AsyncMock(return_value=Response()))
        count = await _resolve_missing_posters(session, [film])

        self.assertEqual(count, 1)
        self.assertEqual(film.poster_url, "https://a.ltrbxd.com/poster-460.jpg")

    async def test_global_budget_serializes_on_block_and_recovers_cautiously(self):
        budget = _LetterboxdRequestBudget(max_concurrency=3, min_interval=0)

        async def response(status):
            return SimpleNamespace(status_code=status)

        await budget.request(lambda: response(429))
        self.assertEqual(budget.current_limit, 1)
        self.assertGreater(budget._blocked_until, 0)

        budget._blocked_until = 0
        for _ in range(20):
            await budget.request(lambda: response(200))
        self.assertEqual(budget.current_limit, 2)

    async def test_blocked_profile_retry_uses_a_fresh_browser_session(self):
        statuses = iter((403, 200))
        sessions = []

        class FakeSession:
            def __init__(self, *, impersonate):
                self.impersonate = impersonate
                self.profile_status = next(statuses)
                sessions.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url, **_kwargs):
                status = 200 if url == "https://letterboxd.com/" else self.profile_status
                return SimpleNamespace(status_code=status, text="profile")

        with (
            patch("app.scraper.AsyncSession", FakeSession),
            patch("app.scraper._human_pause", new=AsyncMock()),
            patch("app.scraper.asyncio.sleep", new=AsyncMock()),
        ):
            response, status = await _fetch_profile_with_fresh_sessions(
                "sample_user", max_retries=2
            )

        self.assertEqual(status, 200)
        self.assertEqual(response.text, "profile")
        self.assertEqual(len(sessions), 2)
        self.assertNotEqual(sessions[0].impersonate, sessions[1].impersonate)


if __name__ == "__main__":
    unittest.main()
