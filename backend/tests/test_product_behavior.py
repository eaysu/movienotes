import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.auth import Account
from app.enrich import EnrichedFilm
from starlette.requests import Request
from app.main import (
    _add_random_reasons,
    _diary_recent_rows,
    _check_profile_watchlist_freshness,
    _community_random_pool,
    _community_reason,
    _personality_refresh_needed,
    _response_locale,
    _pick_random_films,
    _refresh_profile_favorites,
    _refresh_profile_watchlist,
)
from app.scraper import ScrapedFilm, ScrapedProfile


class ProductBehaviorTests(unittest.TestCase):
    def test_personality_stays_stable_when_fav4_is_unchanged(self):
        stored = {
            "taste": {"analysis": ["existing"], "personality": "keep me"},
            "favorite_films": [{"slug": "a"}, {"slug": "b"}],
        }
        current = [
            EnrichedFilm(title="A", slug="a"),
            EnrichedFilm(title="B", slug="b"),
        ]

        self.assertFalse(_personality_refresh_needed(stored, current))

    def test_personality_refreshes_when_fav4_changes(self):
        stored = {
            "taste": {"analysis": ["existing"], "personality": "old"},
            "favorite_films": [{"slug": "a"}, {"slug": "b"}],
        }
        current = [
            EnrichedFilm(title="A", slug="a"),
            EnrichedFilm(title="C", slug="c"),
        ]

        self.assertTrue(_personality_refresh_needed(stored, current))

    def test_favorite_refresh_reads_the_profile_page_and_persists_new_fav4(self):
        account = Account(
            id=7, auth_user_id="auth-7", username="cinephile", display_name="Old name"
        )
        stored = {
            "account": account.__dict__.copy(),
            "taste": {"analysis": ["existing"]},
            "favorite_films": [{"slug": "old", "title": "Old"}],
        }
        saved = []
        service = SimpleNamespace(
            get_profile=lambda _account: stored,
            save_profile_identity_and_favorites=lambda *_args: saved.append(_args),
        )
        settings = SimpleNamespace(scrape_max_retries=1, has_tmdb=False)
        profile = ScrapedProfile(
            username="cinephile",
            display_name="New name",
            favorite_films=[ScrapedFilm("New", 2024, "new")],
            stats={"films": 12},
        )

        with (
            patch("app.main.scrape_profile", new=AsyncMock(return_value=profile)),
            patch("app.main._make_cache", return_value=(None, None)),
            patch("app.main._resolve_favorite_posters", new=AsyncMock()),
            patch("app.main._refresh_favorite_taste", new=AsyncMock()),
        ):
            result = asyncio.run(
                _refresh_profile_favorites(account, settings, service)
            )

        self.assertTrue(result["changed"])
        self.assertEqual(saved[0][2][0].slug, "new")
        self.assertEqual(result["profile"]["account"]["display_name"], "New name")

    def test_random_pick_has_a_short_reason(self):
        film = EnrichedFilm(
            title="Surprise", slug="surprise", director="A Director"
        )

        _add_random_reasons([film], source="community")

        self.assertIn("listende olmayan", film.reason)
        self.assertIn("A Director", film.reason)

    def test_community_pick_keeps_the_lead_it_arrived_with(self):
        film = EnrichedFilm(title="Surprise", slug="surprise")
        film.reason = _community_reason(4, 4.25)

        _add_random_reasons([film], source="community")

        self.assertIn("4 sinefil", film.reason)
        self.assertIn("4.2", film.reason)

    def test_discover_fallback_says_where_the_film_came_from(self):
        film = EnrichedFilm(title="Surprise", slug="surprise")

        _add_random_reasons([film], source="discover")

        self.assertIn("TMDb", film.reason)

    def test_random_pool_comes_from_other_members_not_the_watchlist(self):
        rows = [
            {
                "film_slug": "stalker",
                "title": "Stalker",
                "release_year": 1979,
                "tmdb_id": 1398,
                "director": "Andrei Tarkovsky",
                "genres": ["Science Fiction"],
                "keywords": [],
                "poster_url": "https://example.com/stalker.jpg",
                "overview": "A guide leads two men into the Zone.",
                "watcher_count": 3,
                "avg_rating": 4.5,
            },
            # Rows without a usable title cannot be rendered as a card.
            {"film_slug": "unknown", "title": "  ", "watcher_count": 1},
        ]
        service = SimpleNamespace(community_random_films=lambda user_id, limit: rows)
        account = SimpleNamespace(id=7)

        films = asyncio.run(_community_random_pool(service, account))

        self.assertEqual([film.slug for film in films], ["stalker"])
        self.assertIn("3 sinefil", films[0].reason)

    def test_random_pool_is_empty_without_an_account(self):
        self.assertEqual(asyncio.run(_community_random_pool(None, None)), [])

    def test_random_picks_are_not_stable_across_calls(self):
        pool = [EnrichedFilm(title=f"F{i}", slug=f"f{i}") for i in range(40)]

        draws = {
            tuple(film.slug for film in _pick_random_films(pool, 3))
            for _ in range(12)
        }

        # A daily-seeded pick would collapse to one combination.
        self.assertGreater(len(draws), 1)

    def test_explicit_profile_refresh_bypasses_the_watchlist_cache(self):
        account = Account(
            id=1,
            auth_user_id="auth-1",
            username="film_fan",
            display_name="Film Fan",
        )
        settings = SimpleNamespace(
            has_tmdb=False,
            scrape_delay=0,
            scrape_max_pages=20,
            watchlist_film_limit=1000,
            scrape_max_retries=3,
        )
        load = AsyncMock(return_value=([EnrichedFilm(title="New", slug="new")], False))
        with (
            patch("app.main._make_cache", return_value=(None, object())),
            patch("app.main._make_persistent_cache", return_value=object()),
            patch("app.main._load_user_films", new=load),
        ):
            count = asyncio.run(
                _refresh_profile_watchlist(account, settings, SimpleNamespace())
            )

        self.assertEqual(count, 1)
        self.assertEqual(load.await_args.args[:2], ("film_fan", "watchlist"))
        self.assertTrue(load.await_args.kwargs["force"])

    def test_entry_watchlist_check_starts_full_refresh_when_head_changed(self):
        account = Account(
            id=1, auth_user_id="auth-1", username="film_fan", display_name="Film Fan"
        )
        settings = SimpleNamespace(
            has_tmdb=False,
            scrape_delay=0,
            scrape_max_pages=8,
            watchlist_film_limit=150,
            scrape_max_retries=3,
        )

        class FakeCache:
            def __init__(self):
                self.sets = []

            def get(self, namespace, _key, ttl=None):
                return None

            def get_with_freshness(self, namespace, _key, ttl=None):
                if namespace == "films_watchlist":
                    return ([{"slug": "old-film"}], True)
                return ({"complete": True}, True)

            def set(self, namespace, key, value):
                self.sets.append((namespace, key, value))

            def touch(self, *_args):
                raise AssertionError("changed watchlist must not be touched as current")

        start = AsyncMock(return_value=(object(), False))
        with (
            patch("app.main._make_cache", return_value=(None, object())),
            patch("app.main._make_persistent_cache", return_value=FakeCache()),
            patch(
                "app.main.scrape_watchlist",
                new=AsyncMock(return_value=([ScrapedFilm(title="New", year=None, slug="new-film")], True)),
            ),
            patch("app.main._get_or_create_film_flight", new=start),
        ):
            result = asyncio.run(
                _check_profile_watchlist_freshness(account, settings, SimpleNamespace())
            )

        self.assertEqual(result, {"status": "refreshing", "changed": True})
        self.assertEqual(start.await_args.args[:2], ("film_fan", "watchlist"))

    def test_entry_watchlist_check_touches_unchanged_cache(self):
        account = Account(
            id=1, auth_user_id="auth-1", username="film_fan", display_name="Film Fan"
        )
        settings = SimpleNamespace(scrape_max_retries=3)

        class FakeCache:
            def __init__(self):
                self.touches = []
                self.sets = []

            def get(self, namespace, _key, ttl=None):
                return None

            def get_with_freshness(self, namespace, _key, ttl=None):
                if namespace == "films_watchlist":
                    return ([{"slug": "same-film"}], True)
                return ({"complete": True}, True)

            def touch(self, namespace, key):
                self.touches.append((namespace, key))

            def set(self, namespace, key, value):
                self.sets.append((namespace, key, value))

        cache = FakeCache()
        with (
            patch("app.main._make_cache", return_value=(None, object())),
            patch("app.main._make_persistent_cache", return_value=cache),
            patch(
                "app.main.scrape_watchlist",
                new=AsyncMock(return_value=([ScrapedFilm(title="Same", year=None, slug="same-film")], True)),
            ),
            patch("app.main._get_or_create_film_flight", new=AsyncMock()) as start,
        ):
            result = asyncio.run(
                _check_profile_watchlist_freshness(account, settings, SimpleNamespace())
            )

        self.assertEqual(result, {"status": "current", "changed": False})
        self.assertEqual(cache.touches, [("films_watchlist", "film_fan")])
        self.assertEqual(
            cache.sets,
            [("watchlist_head_check", "film_fan", {"checked": True})],
        )
        start.assert_not_awaited()

    def test_entry_watchlist_check_is_deferred_during_persistent_cooldown(self):
        account = Account(
            id=1, auth_user_id="auth-1", username="film_fan", display_name="Film Fan"
        )
        settings = SimpleNamespace(scrape_max_retries=3)

        class FakeCache:
            def get(self, namespace, key, ttl=None):
                self.last_get = (namespace, key, ttl)
                return {"checked": True}

        cache = FakeCache()
        scrape = AsyncMock()
        heavy_limit = AsyncMock()
        request = Request({"type": "http", "method": "POST", "path": "/api/profile/watchlist/check"})
        with (
            patch("app.main._make_cache", return_value=(None, object())),
            patch("app.main._make_persistent_cache", return_value=cache),
            patch("app.main.scrape_watchlist", new=scrape),
            patch("app.main._enforce_heavy_rate_limit", new=heavy_limit),
        ):
            result = asyncio.run(
                _check_profile_watchlist_freshness(
                    account, settings, SimpleNamespace(), request=request
                )
            )

        self.assertEqual(result, {"status": "deferred", "changed": False})
        self.assertEqual(cache.last_get[:2], ("watchlist_head_check", "film_fan"))
        self.assertEqual(cache.last_get[2], 30 * 60)
        scrape.assert_not_awaited()
        heavy_limit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class RecentFilmsOrderTests(unittest.TestCase):
    """The "Son filmler" card must show watch order, not release order.

    `user_watched_films.watched_rank` follows Letterboxd's /films/ listing,
    which is ordered by release date. Caching that as if it were the diary is
    what made the card show the newest films instead of the last ones watched.
    """

    def _account(self):
        return Account(id=1, auth_user_id="auth-1", username="film_fan", display_name="Fan")

    def _settings(self):
        return SimpleNamespace(scrape_max_retries=1)

    def test_diary_order_is_reported_as_watch_order(self):
        diary = [ScrapedFilm(title="Son İzlenen", year=1959, slug="son-izlenen")]
        service = SimpleNamespace(
            watched_films_by_slugs=lambda _id, _slugs: {},
            list_recent_watched=lambda _id, _limit: [],
        )

        with patch("app.main.scrape_diary", new=AsyncMock(return_value=(diary, True))):
            rows, watch_order = asyncio.run(
                _diary_recent_rows(self._account(), service, self._settings(), 10)
            )

        self.assertTrue(watch_order)
        self.assertEqual(rows[0]["title"], "Son İzlenen")

    def test_rss_is_tried_before_falling_back_to_stored_order(self):
        rss = [ScrapedFilm(title="RSS Filmi", year=2001, slug="rss-filmi")]
        service = SimpleNamespace(
            watched_films_by_slugs=lambda _id, _slugs: {},
            list_recent_watched=lambda _id, _limit: [{"title": "Yanlış Sıra"}],
        )

        with (
            patch("app.main.scrape_diary", new=AsyncMock(return_value=([], True))),
            patch("app.main._scrape_watched_rss", new=AsyncMock(return_value=rss)),
        ):
            rows, watch_order = asyncio.run(
                _diary_recent_rows(self._account(), service, self._settings(), 10)
            )

        self.assertTrue(watch_order)
        self.assertEqual(rows[0]["title"], "RSS Filmi")

    def test_stored_fallback_is_flagged_as_not_watch_order(self):
        service = SimpleNamespace(
            watched_films_by_slugs=lambda _id, _slugs: {},
            list_recent_watched=lambda _id, _limit: [{"title": "Çıkış Sırası"}],
        )

        with (
            patch("app.main.scrape_diary", new=AsyncMock(return_value=([], True))),
            patch("app.main._scrape_watched_rss", new=AsyncMock(return_value=[])),
        ):
            rows, watch_order = asyncio.run(
                _diary_recent_rows(self._account(), service, self._settings(), 10)
            )

        self.assertFalse(watch_order)
        self.assertEqual(rows[0]["title"], "Çıkış Sırası")

    def test_only_real_watch_order_is_cached(self):
        main = (Path(__file__).parents[1] / "app" / "main.py").read_text()
        endpoint = main.split('@app.get("/api/profile/recent")', 1)[1].split("@app.", 1)[0]

        self.assertIn("if watch_order:", endpoint)
        self.assertLess(
            endpoint.index("if watch_order:"),
            endpoint.index('pcache.set, "films_diary_recent"'),
        )


class ResponseLocaleTests(unittest.TestCase):
    """Reported: recommendation reasons came back in English in a Turkish app.

    The shell resolves its language from a per-device preference the account
    may never have been told about, so the server was falling through to
    Accept-Language — which a browser can order differently from the UI.
    """

    @staticmethod
    def _request(headers: dict) -> Request:
        return Request({
            "type": "http",
            "method": "POST",
            "path": "/api/recommend",
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in headers.items()
            ],
        })

    def test_the_shells_own_locale_beats_accept_language(self):
        account = SimpleNamespace(preferred_locale="auto")
        request = self._request({
            "accept-language": "en-US,en;q=0.9,tr;q=0.8",
            "x-movienotes-locale": "tr",
        })

        self.assertEqual(_response_locale(account, request), "tr")

    def test_an_explicit_account_preference_still_wins(self):
        account = SimpleNamespace(preferred_locale="en")
        request = self._request({"x-movienotes-locale": "tr"})

        self.assertEqual(_response_locale(account, request), "en")

    def test_accept_language_remains_the_last_resort(self):
        account = SimpleNamespace(preferred_locale="auto")

        self.assertEqual(
            _response_locale(account, self._request({"accept-language": "en-GB,en"})),
            "en",
        )
        self.assertEqual(
            _response_locale(account, self._request({"accept-language": "tr-TR,tr"})),
            "tr",
        )

    def test_a_junk_header_does_not_override_anything(self):
        account = SimpleNamespace(preferred_locale="auto")
        request = self._request({
            "accept-language": "tr-TR,tr",
            "x-movienotes-locale": "klingon",
        })

        self.assertEqual(_response_locale(account, request), "tr")


class ForcedTasteRebuildTests(unittest.TestCase):
    """Reported: the taste analysis stayed on its old text after a version bump.

    A rebuild inherited the stored prose whenever the watched archive had not
    moved — which is exactly the case a forced rebuild exists to handle.
    """

    def test_a_forced_rebuild_does_not_inherit_the_stored_analysis(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        rebuild = source.split("async def rebuild_snapshot", 1)[1].split(
            "async def _refresh_locale_taste", 1
        )[0]

        assert "if not force_analysis and not source_changed and stored_taste.get(\"analysis\"):" in rebuild
        # A silent LLM failure must be visible rather than looking like old text.
        assert 'taste.analysis_source = "local"' in rebuild
        assert 'taste.analysis_source = "llm"' in rebuild
        assert "taste analysis fell back to local prose" in rebuild


class RandomRatingFloorTests(unittest.TestCase):
    """Asked for: a spin should aim above 3.5 on the five-star scale."""

    @staticmethod
    def _row(slug: str, avg):
        return {
            "film_slug": slug, "title": slug.title(), "release_year": 2000,
            "tmdb_id": 1, "avg_rating": avg, "watcher_count": 5,
        }

    def _pool(self, rows):
        service = SimpleNamespace(community_random_films=lambda *_a, **_k: rows)
        account = SimpleNamespace(id=1)
        return asyncio.run(_community_random_pool(service, account))

    def test_films_the_membership_rated_below_the_floor_are_dropped(self):
        slugs = {f.slug for f in self._pool([
            self._row("loved", 4.4),
            self._row("fine", 3.5),
            self._row("poor", 2.1),
        ])}

        self.assertEqual(slugs, {"loved", "fine"})

    def test_an_unrated_film_is_not_treated_as_a_bad_one(self):
        slugs = {f.slug for f in self._pool([
            self._row("loved", 4.4), self._row("unseen", None),
        ])}

        self.assertEqual(slugs, {"loved", "unseen"})

    def test_an_all_low_pool_still_returns_something(self):
        # An empty random mode is worse than a mediocre film.
        slugs = {f.slug for f in self._pool([
            self._row("meh", 2.0), self._row("worse", 1.2),
        ])}

        self.assertEqual(slugs, {"meh", "worse"})
