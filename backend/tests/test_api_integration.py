import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main
from app.cache import Cache
from app.enrich import EnrichedFilm
from app.rate_limit import SlidingWindowRateLimiter
from app.scraper import ProfileNotFoundError


class _MemoryCache:
    def __init__(self, watchlist=None):
        self.watchlist = watchlist
        self.values = {}

    def get(self, namespace, key, ttl=None):
        return self.values.get((namespace, str(key)))

    def get_with_freshness(self, namespace, key, ttl=None):
        if namespace == "films_watchlist" and self.watchlist is not None:
            return self.watchlist, True
        return None

    def set(self, namespace, key, value):
        self.values[(namespace, str(key))] = value


class StaticAssetCacheTests(unittest.TestCase):
    def test_versioned_assets_are_immutable_but_html_and_bare_urls_are_not(self):
        with TestClient(main.app) as client:
            html = client.get("/")
            versioned = client.get("/static/js/dom.js?v=cache-contract")
            bare = client.get("/static/js/dom.js")

        self.assertEqual(html.headers["cache-control"], "no-cache")
        self.assertEqual(
            versioned.headers["cache-control"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            bare.headers["cache-control"],
            "public, max-age=3600, must-revalidate",
        )


def _settings():
    return SimpleNamespace(
        has_tmdb=False,
        has_openai=False,
        has_supabase=False,
        tmdb_api_key="",
        openai_model="local",
        num_recommendations=5,
        scrape_delay=0,
        watched_max_pages=1,
        watched_film_limit=2,
        scrape_max_retries=1,
        scrape_max_pages=1,
        watchlist_film_limit=2,
    )


def _events(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


class PublicStatsTests(unittest.TestCase):
    def test_public_stats_returns_cached_active_account_count(self):
        settings = SimpleNamespace(has_supabase=True)
        original_cache = main._public_stats_cache
        main._public_stats_cache = {"checked_at": 0.0, "registered_users": 0}
        try:
            with (
                patch("app.main.get_settings", return_value=settings),
                patch("app.main._count_registered_users", return_value=37) as counter,
                TestClient(main.app) as client,
            ):
                first = client.get("/api/public/stats")
                second = client.get("/api/public/stats")

            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json(), {"registered_users": 37})
            self.assertEqual(second.json(), {"registered_users": 37})
            counter.assert_called_once_with(settings)
        finally:
            main._public_stats_cache = original_cache


class SseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.original_limiter = main._heavy_rate_limiter
        self.original_delete_limiter = main._delete_rate_limiter
        main._heavy_rate_limiter = SlidingWindowRateLimiter(
            limit=5, window_seconds=600, burst=2, burst_seconds=15
        )
        main._delete_rate_limiter = SlidingWindowRateLimiter(
            limit=3, window_seconds=3600, burst=1, burst_seconds=15
        )

    def tearDown(self):
        main._heavy_rate_limiter = self.original_limiter
        main._delete_rate_limiter = self.original_delete_limiter

    def test_recommend_success_stream_has_five_results_contract(self):
        watched = [EnrichedFilm(title="Watched", slug="watched")]
        watchlist = [EnrichedFilm(title=f"Film {i}", slug=f"film-{i}") for i in range(5)]

        async def load(_username, list_type, **_kwargs):
            return (watched if list_type == "watched" else watchlist), True

        result = {
            "taste_summary": "Taste summary",
            "recommendations": [film.to_dict() for film in watchlist],
            "llm_used": False,
        }
        cache = _MemoryCache()
        with (
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._make_cache", return_value=(None, cache)),
            patch("app.main._make_persistent_cache", return_value=cache),
            patch("app.main._load_user_films", side_effect=load),
            patch("app.main.rank_watchlist", autospec=True, return_value=watchlist),
            patch("app.main.rank_candidates", new=AsyncMock(return_value=result)),
            TestClient(main.app) as client,
        ):
            response = client.post(
                "/api/recommend",
                json={"username": "film_fan"},
                headers={"CF-Connecting-IP": "203.0.113.10"},
            )

        self.assertEqual(response.status_code, 200)
        final = [event for event in _events(response) if event["type"] == "result"][-1]
        self.assertEqual(len(final["recommendations"]), 5)
        self.assertEqual(final["username"], "film_fan")

    def test_scrape_error_stream_exposes_machine_readable_code(self):
        cache = _MemoryCache()
        with (
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._make_cache", return_value=(None, cache)),
            patch("app.main._make_persistent_cache", return_value=cache),
            patch(
                "app.main._load_user_films",
                new=AsyncMock(side_effect=ProfileNotFoundError("Kullanıcı bulunamadı")),
            ),
            TestClient(main.app) as client,
        ):
            response = client.post(
                "/api/recommend",
                json={"username": "missing_user"},
                headers={"CF-Connecting-IP": "203.0.113.11"},
            )

        error = [event for event in _events(response) if event["type"] == "error"][-1]
        self.assertEqual(error["code"], "profile_not_found")

    def test_blend_score_precedes_lazy_common_watchlist(self):
        watched = [
            EnrichedFilm(
                title="Shared Film",
                slug="shared-film",
                genres=["Drama"],
                keywords=["family"],
            )
        ]
        watchlist = [EnrichedFilm(title="Next Film", slug="next-film")]

        async def load(_username, list_type, **_kwargs):
            if list_type == "watchlist":
                await asyncio.sleep(0.01)
                return watchlist, True
            return watched, True

        cache = _MemoryCache()
        with (
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._make_cache", return_value=(None, cache)),
            patch("app.main._make_persistent_cache", return_value=cache),
            patch("app.main._load_user_films", side_effect=load),
            TestClient(main.app) as client,
        ):
            response = client.post(
                "/api/blend",
                json={"username1": "film_fan", "username2": "other_user"},
                headers={"CF-Connecting-IP": "203.0.113.13"},
            )

        events = _events(response)
        event_types = [event["type"] for event in events]
        self.assertLess(event_types.index("result"), event_types.index("watchlist_result"))
        result = next(event for event in events if event["type"] == "result")
        lazy = next(event for event in events if event["type"] == "watchlist_result")
        self.assertTrue(result["watchlist_pending"])
        self.assertEqual(lazy["common_watchlist_films"][0]["slug"], "next-film")

    def test_delete_data_removes_only_username_scoped_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "cache.sqlite3"
            cache = Cache(db_path)
            cache.set("films_watched", "film_fan", [{"slug": "watched"}])
            cache.set("films_watchlist", "film_fan", [{"slug": "watchlist"}])
            cache.set("films_watched", "other_user", [{"slug": "other"}])
            cache.set("films_full_refresh", "watched:film_fan", {"complete": True})
            cache.set("recommendations:film_fan", "hash", {"recommendations": []})
            cache.set("recommendations:other_user", "hash", {"recommendations": []})
            cache.set("recommendations", "legacy", {"recommendations": []})
            cache.set("tmdb", "shared-film", {"title": "Shared Film"})
            settings = SimpleNamespace(has_supabase=False, cache_db_path=db_path)

            with (
                patch("app.main.get_settings", return_value=settings),
                TestClient(main.app) as client,
            ):
                response = client.request(
                    "DELETE",
                    "/api/data",
                    json={"username": "@Film_Fan"},
                    headers={"CF-Connecting-IP": "203.0.113.14"},
                )
                limited = client.request(
                    "DELETE",
                    "/api/data",
                    json={"username": "film_fan"},
                    headers={"CF-Connecting-IP": "203.0.113.14"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(limited.status_code, 429)
            self.assertEqual(limited.headers["Retry-After"], "15")
            self.assertEqual(response.json()["username"], "film_fan")
            self.assertIsNone(cache.get("films_watched", "film_fan"))
            self.assertIsNone(cache.get("films_watchlist", "film_fan"))
            self.assertIsNone(cache.get("films_full_refresh", "watched:film_fan"))
            self.assertIsNone(cache.get("recommendations:film_fan", "hash"))
            self.assertIsNone(cache.get("recommendations", "legacy"))
            self.assertIsNotNone(cache.get("films_watched", "other_user"))
            self.assertIsNotNone(cache.get("recommendations:other_user", "hash"))
            self.assertIsNotNone(cache.get("tmdb", "shared-film"))

    def test_random_has_its_own_budget_while_heavy_modes_share_one(self):
        film = EnrichedFilm(title="Film", slug="film", poster_url="https://example.com/p.jpg")
        cache = _MemoryCache(watchlist=[film.to_dict()])

        async def load(_username, list_type, **_kwargs):
            return [film], True

        result = {
            "taste_summary": "Taste",
            "recommendations": [film.to_dict()],
            "llm_used": False,
        }
        headers = {"CF-Connecting-IP": "203.0.113.12"}
        with (
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._make_cache", return_value=(None, cache)),
            patch("app.main._make_persistent_cache", return_value=cache),
            patch("app.main._load_user_films", side_effect=load),
            patch("app.main.rank_watchlist", autospec=True, return_value=[film]),
            patch("app.main.rank_candidates", new=AsyncMock(return_value=result)),
            # This checks endpoint quotas, not Letterboxd's film-page service.
            # Keep the clock-sensitive burst assertion independent of the
            # network and of the scraper's intentional pacing policy.
            patch("app.main.scrape_film_rating", new=AsyncMock(return_value=None)),
            TestClient(main.app) as client,
        ):
            first = client.post("/api/recommend", json={"username": "film_fan"}, headers=headers)
            # Random is meant to be spun repeatedly, so it must not eat the
            # shared analysis budget.
            spins = [
                client.post("/api/random", json={"username": "film_fan"}, headers=headers)
                for _ in range(3)
            ]
            second = client.post("/api/recommend", json={"username": "film_fan"}, headers=headers)
            third = client.post(
                "/api/blend",
                json={"username1": "film_fan", "username2": "other_user"},
                headers=headers,
            )

        self.assertEqual([spin.status_code for spin in spins], [200, 200, 200])
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(third.status_code, 429)
        self.assertEqual(third.headers["Retry-After"], "15")


if __name__ == "__main__":
    unittest.main()
