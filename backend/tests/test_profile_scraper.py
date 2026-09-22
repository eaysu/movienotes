import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.scraper import (
    EmptyListError,
    LetterboxdCircuitOpenError,
    MarkupChangedError,
    _LetterboxdRequestBudget,
    _empty_page_error,
    _fetch_with_retry,
    _fetch_profile_with_fresh_sessions,
    _parse_film_rating,
    _parse_page,
    _parse_profile_page,
    _scrape_list,
    _resolve_missing_posters,
    scrape_reviewed_diary,
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

    def test_parses_current_diary_table_rows(self):
        films = _parse_page(
            """
            <table class="diary-table"><tbody>
              <tr class="diary-entry-row">
                <td class="td-film-details"><h3><a href="/film/perfect-days/">Perfect Days</a></h3></td>
                <td class="td-released"><a>2023</a></td>
                <td><span class="rating rated-8"></span></td>
              </tr>
            </tbody></table>
            """
        )

        self.assertEqual(len(films), 1)
        self.assertEqual(films[0].slug, "perfect-days")
        self.assertEqual(films[0].title, "Perfect Days")
        self.assertEqual(films[0].year, 2023)
        self.assertEqual(films[0].user_rating, 4.0)

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

    def test_empty_list_copy_wins_over_template_challenge_script(self):
        error = _empty_page_error(
            "sample_user",
            "watchlist",
            "<script src='/challenge-platform.js'></script><main>No films yet</main>",
        )
        self.assertIsInstance(error, EmptyListError)

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
    async def test_list_does_not_pause_after_its_final_requested_page(self):
        page_one = """
        <div data-item-slug="perfect-days" data-item-name="Perfect Days (2023)">
          <img src="https://a.ltrbxd.com/perfect-days.jpg" />
        </div>
        """

        class FakeSession:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        pause = AsyncMock()
        with (
            patch("app.scraper.AsyncSession", FakeSession),
            patch(
                "app.scraper._fetch_with_retry",
                return_value=(SimpleNamespace(status_code=200, text=page_one), 200),
            ),
            patch("app.scraper._human_pause", new=pause),
        ):
            result = await _scrape_list(
                "sample_user", "films", max_pages=1, delay=0.6
            )

        self.assertEqual(len(result.films), 1)
        pause.assert_not_awaited()

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

    async def test_open_circuit_skips_the_next_outbound_request(self):
        budget = _LetterboxdRequestBudget(min_interval=0, block_seconds=60)
        calls = 0

        async def blocked():
            nonlocal calls
            calls += 1
            return SimpleNamespace(status_code=403)

        await budget.request(blocked)
        with self.assertRaises(LetterboxdCircuitOpenError):
            await budget.request(blocked)
        self.assertEqual(calls, 1)

    async def test_blocked_profile_returns_after_one_request(self):
        sessions = []

        class FakeSession:
            def __init__(self, *, impersonate):
                self.impersonate = impersonate
                self.urls = []
                sessions.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url, **_kwargs):
                self.urls.append(url)
                return SimpleNamespace(status_code=403, text="profile")

        with (
            patch("app.scraper.AsyncSession", FakeSession),
            patch(
                "app.scraper._letterboxd_budget",
                _LetterboxdRequestBudget(min_interval=0, block_seconds=0),
            ),
        ):
            response, status = await _fetch_profile_with_fresh_sessions(
                "sample_user", max_retries=2
            )

        self.assertEqual(status, 403)
        self.assertEqual(response.text, "profile")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(
            [url for session in sessions for url in session.urls],
            ["https://letterboxd.com/sample_user/"],
        )

    async def test_blocked_list_request_does_not_retry_within_one_job(self):
        class FakeSession:
            def __init__(self):
                self.urls = []

            async def get(self, url, **_kwargs):
                self.urls.append(url)
                return SimpleNamespace(status_code=403, text="blocked")

        session = FakeSession()
        with patch(
            "app.scraper._letterboxd_budget",
            _LetterboxdRequestBudget(min_interval=0, block_seconds=0),
        ):
            response, status = await _fetch_with_retry(
                session,
                "https://letterboxd.com/sample_user/films/page/2/",
                "https://letterboxd.com/sample_user/films/",
                max_retries=3,
            )

        self.assertEqual(status, 403)
        self.assertEqual(response.text, "blocked")
        self.assertEqual(len(session.urls), 1)

    async def test_review_full_text_requests_are_bounded_and_parallel(self):
        reviews = """
        <article class="production-viewing" data-object-id="viewing:101">
          <time class="timestamp" datetime="2026-09-15"></time>
          <div data-item-slug="first-film" data-item-name="First Film (2024)"></div>
          <div class="js-review-body">First truncated review…</div>
        </article>
        <article class="production-viewing" data-object-id="viewing:102">
          <time class="timestamp" datetime="2026-09-14"></time>
          <div data-item-slug="second-film" data-item-name="Second Film (2023)"></div>
          <div class="js-review-body">Second truncated review…</div>
        </article>
        """

        class FakeSession:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        active = 0
        peak = 0

        async def full_text(_session, key, _fallback):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return f"complete {key}"

        with (
            patch("app.scraper.AsyncSession", FakeSession),
            patch(
                "app.scraper._budgeted_get",
                return_value=SimpleNamespace(status_code=200, text=reviews),
            ),
            patch("app.scraper._full_review_text", side_effect=full_text),
        ):
            entries = await scrape_reviewed_diary("sample_user", max_pages=1)

        self.assertEqual(peak, 2)
        self.assertEqual(
            [entry.review for entry in entries],
            ["complete letterboxd-review-101", "complete letterboxd-review-102"],
        )


class FilmRatingParsingTests(unittest.TestCase):
    """Asked for: the Letterboxd average, out of five, not TMDb's ten-point vote."""

    CDATA_PAGE = """
    <script type="application/ld+json">
    /* <![CDATA[ */
    {"@type":"Movie","name":"Parasite",
     "aggregateRating":{"@type":"AggregateRating","ratingValue":4.55,
     "bestRating":5,"ratingCount":1200000}}
    /* ]]> */
    </script>
    """

    def test_reads_the_average_out_of_the_cdata_wrapped_json_ld(self):
        self.assertEqual(_parse_film_rating(self.CDATA_PAGE), 4.55)

    def test_a_page_without_json_ld_has_no_average(self):
        self.assertIsNone(_parse_film_rating("<html><body>no ld here</body></html>"))

    def test_unrated_and_out_of_scale_values_are_rejected(self):
        # A ten-point number here would mean the markup changed meaning; showing
        # it as "8.1/5" would be worse than showing nothing.
        for payload in ('{"aggregateRating":{"ratingValue":8.1}}',
                        '{"aggregateRating":{"ratingValue":0}}',
                        '{"aggregateRating":{"ratingValue":"n/a"}}',
                        '{"name":"No rating yet"}'):
            with self.subTest(payload=payload):
                html = f'<script type="application/ld+json">{payload}</script>'
                self.assertIsNone(_parse_film_rating(html))

    def test_malformed_json_does_not_raise(self):
        html = '<script type="application/ld+json">{not json</script>'
        self.assertIsNone(_parse_film_rating(html))


if __name__ == "__main__":
    unittest.main()
