"""Concurrency and load behaviour across every area of the app.

Supabase, Letterboxd and TMDb are all blocking clients, so every handler has to
push its work onto a thread and leave the event loop free. That is invisible in
a single-request test: a handler that calls Supabase directly on the loop
returns exactly the same JSON, just one request at a time. These tests give the
stand-in service a realistic round-trip latency and then ask for many things at
once, so the difference shows up as wall-clock time.

They are deliberately measured against a generous ceiling. The point is to
catch serialisation — a handler that lost its ``to_thread``, a lock held across
an await, a shared cache that turns concurrent readers into a queue — not to
benchmark this machine.
"""

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app import main
from app.auth import Account

# One stand-in Supabase round trip. Long enough that serialising 40 of them is
# unmistakable (2s), short enough to keep the suite quick.
ROUND_TRIP = 0.05


def _settings(**overrides):
    values = {
        "has_auth": True,
        "has_supabase": True,
        "has_tmdb": False,
        "has_openai": False,
        "has_web_push": False,
        "supabase_url": "https://project.supabase.co",
        "supabase_key": "service-key",
        "supabase_anon_key": "anon-key",
        "auth_identity_secret": "load-test-secret",
        "auth_cookie_secure": True,
        "auth_session_max_age": 604800,
        "auth_session_short_max_age": 86400,
        "dev_login_enabled": False,
        "openai_model": "local",
        "num_recommendations": 5,
        "scrape_delay": 0,
        "scrape_max_retries": 1,
        "scrape_max_pages": 1,
        "watched_max_pages": 1,
        "watched_film_limit": 2,
        "watchlist_film_limit": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


_ACCOUNT = Account(
    id=42,
    auth_user_id="00000000-0000-0000-0000-00000000002a",
    username="load_fan",
    display_name="Load Fan",
    letterboxd_stats={"films": 563, "this_year": 25},
)

# What each read path expects back, so the handler does real work rather than
# tripping over a missing key and returning early.
_PAYLOADS = {
    "list_feed": {"posts": [], "next_cursor": ""},
    "list_letters": [],
    "list_blends": {"blends": [], "requests": []},
    "list_notifications": [],
    "list_sinefil_cards": [],
    "search_accounts": [],
    "trending_films": [],
    "social_stats": {"followers": 3, "following": 5},
    "get_profile": {"taste": {"algorithm_version": main.TASTE_PROFILE_VERSION}, "favorite_films": []},
    "list_letterable_followers": [],
    "letter_receiving_status": True,
    "count_unread_letters": 0,
    "count_pending_blend_requests": 0,
    "unread_notification_count": 0,
    "list_recent_watched": [],
    "get_sync_job": None,
}


class _SlowService:
    """Every call costs one round trip, exactly like the real client."""

    def __init__(self, latency: float = ROUND_TRIP):
        self.latency = latency
        self.calls: list[str] = []
        self._lock = __import__("threading").Lock()

    def current_account(self, _token):
        return self._respond("current_account")

    def _respond(self, name):
        time.sleep(self.latency)
        with self._lock:
            self.calls.append(name)
        if name == "current_account":
            return _ACCOUNT
        return _PAYLOADS.get(name, {})

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self._respond(name)


# The areas a member actually moves through, as GET paths. Each one is a real
# route with its own handler; together they are the whole read surface.
AREAS = {
    "feed": "/api/feed?scope=community",
    "feed-following": "/api/feed?scope=following",
    "trending": "/api/feed/trending",
    "profile": "/api/profile/me",
    "profile-stats": "/api/profile/stats",
    "social-stats": "/api/profile/social-stats",
    "sync-status": "/api/profile/sync-status",
    "letters": "/api/letters",
    "letters-unread": "/api/letters/unread-count",
    "letter-settings": "/api/letters/settings",
    "letter-followers": "/api/letters/followers",
    "blends": "/api/blends",
    "blends-pending": "/api/blends/pending-count",
    "notifications": "/api/notifications",
    "notifications-unread": "/api/notifications/unread-count",
    "sinefil": "/api/sinefil-alani",
    "search": "/api/users/search?q=someone",
}

_COOKIES = {"mb_access": "load-test-token", "mb_csrf": "csrf-token"}


class _LoadCase(unittest.IsolatedAsyncioTestCase):
    """Shared harness: a slow service behind the real ASGI app."""

    async def asyncSetUp(self):
        main._account_cache.clear()
        for limiter in (
            main._auth_rate_limiter,
            main._auth_failure_limiter,
            main._member_action_limiter,
            main._heavy_rate_limiter,
            main._random_rate_limiter,
        ):
            limiter._buckets.clear()
        self.service = _SlowService()
        self._patches = [
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._auth_service", return_value=self.service),
        ]
        for item in self._patches:
            item.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="https://testserver",
            cookies=_COOKIES,
            timeout=30.0,
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in self._patches:
            item.stop()
        main._account_cache.clear()

    async def _burst(self, paths):
        started = time.perf_counter()
        responses = await asyncio.gather(*(self.client.get(path) for path in paths))
        return responses, time.perf_counter() - started


class EveryAreaUnderLoadTests(_LoadCase):
    async def test_the_whole_read_surface_survives_a_simultaneous_sweep(self):
        responses, elapsed = await self._burst(list(AREAS.values()))

        failures = {
            name: response.status_code
            for name, response in zip(AREAS, responses)
            if response.status_code != 200
        }
        self.assertEqual(failures, {}, f"areas that failed under load: {failures}")

        # Serialised, seventeen areas averaging two round trips each would take
        # well over a second. Threaded, they overlap.
        self.assertLess(
            elapsed, 1.0,
            f"the read surface serialised: {elapsed:.2f}s for {len(AREAS)} areas",
        )

    async def test_forty_readers_do_not_queue_behind_each_other(self):
        """The clearest signal that a handler lost its thread hand-off."""
        paths = [AREAS["feed"], AREAS["letters"], AREAS["blends"], AREAS["notifications"]] * 10

        responses, elapsed = await self._burst(paths)

        self.assertTrue(all(r.status_code == 200 for r in responses))
        serial = len(paths) * ROUND_TRIP
        self.assertLess(
            elapsed, serial / 3,
            f"{len(paths)} concurrent reads took {elapsed:.2f}s; "
            f"serial would be {serial:.2f}s — a handler is blocking the loop",
        )

    async def test_the_shell_and_health_stay_instant_while_the_api_is_loaded(self):
        """What a visitor sees first must not wait behind other people's work."""
        background = [
            asyncio.create_task(self.client.get(AREAS["feed"])) for _ in range(40)
        ]
        await asyncio.sleep(0)  # let the burst reach the app

        started = time.perf_counter()
        shell = await self.client.get("/")
        health = await self.client.get("/api/health")
        elapsed = time.perf_counter() - started

        await asyncio.gather(*background)
        self.assertEqual(shell.status_code, 200)
        self.assertEqual(health.status_code, 200)
        self.assertLess(
            elapsed, 0.5,
            f"the shell waited {elapsed:.2f}s behind 40 API calls",
        )


class ThreadPoolCapacityTests(unittest.TestCase):
    """The app's real concurrency ceiling is the pool behind ``to_thread``."""

    def test_the_worker_pool_is_sized_for_round_trips_not_for_cores(self):
        """Measured: throughput plateaued at cpu_count + 4 concurrent calls.

        Every handler waits on Supabase in a thread. The interpreter's default
        executor is sized for computation, so a one-core host would have given
        the whole application five threads — five members in flight, and the
        sixth queued behind them for no reason but the size of the box.
        """
        import os

        from fastapi.testclient import TestClient

        with TestClient(main.app) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            pool = main._worker_pool

        self.assertIsNotNone(pool, "no explicit worker pool was installed")
        self.assertGreaterEqual(
            pool._max_workers, 8,
            "a one-core host must not shrink the app to five threads",
        )
        self.assertGreater(
            pool._max_workers, (os.cpu_count() or 1) + 4,
            "the pool is still sized for cores rather than for round trips",
        )


class SessionCostTests(_LoadCase):
    async def test_a_session_is_validated_once_not_once_per_request(self):
        """Every handler needs the account; only the first may pay for it."""
        await self.client.get(AREAS["profile"])
        self.service.calls.clear()

        responses, _ = await self._burst([AREAS["feed"]] * 20)

        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(
            self.service.calls.count("current_account"), 0,
            "the account cache stopped collapsing repeat session lookups",
        )

    async def test_twenty_different_members_each_pay_once(self):
        """And the cache must key by session, not collapse people together."""
        main._account_cache.clear()
        clients = [
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app),
                base_url="https://testserver",
                cookies={"mb_access": f"member-{index}", "mb_csrf": "csrf-token"},
                timeout=30.0,
            )
            for index in range(20)
        ]
        try:
            responses = await asyncio.gather(
                *(client.get(AREAS["feed"]) for client in clients)
            )
        finally:
            await asyncio.gather(*(client.aclose() for client in clients))

        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(self.service.calls.count("current_account"), 20)


class ResponsePathCostTests(_LoadCase):
    """What a member waits for should only be what they asked for."""

    async def _cost(self, path) -> float:
        started = time.perf_counter()
        response = await self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return (time.perf_counter() - started) / ROUND_TRIP

    async def test_analytics_do_not_sit_between_a_member_and_their_page(self):
        """Measured: opening the directory cost as much in telemetry as content."""
        await self.client.get(AREAS["trending"])  # warm the session cache
        cost = await self._cost(AREAS["sinefil"])

        self.assertLess(
            cost, 1.8,
            f"the directory waited {cost:.1f} round trips; the activity write "
            "belongs off the response path",
        )
        # The event is still recorded, just not in front of the member.
        await asyncio.sleep(ROUND_TRIP * 3)
        self.assertIn("record_activity_event", self.service.calls)

    async def test_the_profile_reads_its_two_sources_at_once(self):
        await self.client.get(AREAS["trending"])  # warm the session cache
        cost = await self._cost(AREAS["profile"])

        self.assertLess(cost, 1.8, f"the profile cost {cost:.1f} sequential round trips")

    async def test_clearing_the_badge_does_not_delay_the_notifications(self):
        await self.client.get(AREAS["trending"])  # warm the session cache
        cost = await self._cost(AREAS["notifications"])

        self.assertLess(cost, 1.8, f"notifications cost {cost:.1f} sequential round trips")


class NormalUseIsNeverRateLimitedTests(_LoadCase):
    async def test_a_busy_browsing_session_never_hits_a_limit(self):
        """Five passes over every area is an ordinary few minutes of use."""
        statuses = []
        for _ in range(5):
            responses, _ = await self._burst(list(AREAS.values()))
            statuses.extend(response.status_code for response in responses)

        self.assertNotIn(429, statuses, "normal browsing hit a rate limit")

    @staticmethod
    async def _replay(limiter, *, every: float, count: int) -> int:
        """Replay a steady tapping rate against a limiter's own configuration."""
        from app.rate_limit import SlidingWindowRateLimiter

        now = [0.0]
        clone = SlidingWindowRateLimiter(
            limit=limiter.limit,
            window_seconds=limiter.window_seconds,
            burst=limiter.burst,
            burst_seconds=limiter.burst_seconds,
            clock=lambda: now[0],
        )
        blocked = 0
        for _ in range(count):
            allowed, _ = await clone.check("member")
            blocked += 0 if allowed else 1
            now[0] += every
        return blocked

    async def test_spinning_the_random_picker_once_a_second_is_not_abuse(self):
        """The picker exists to be spun, and a card lands in about a second."""
        blocked = await self._replay(main._random_rate_limiter, every=1.0, count=60)

        self.assertEqual(blocked, 0, "a steady spin got told it was too fast")

    async def test_a_scripted_spin_loop_is_still_stopped(self):
        blocked = await self._replay(main._random_rate_limiter, every=0.02, count=60)

        self.assertGreater(blocked, 0)

    async def test_writing_several_letters_in_a_sitting_is_not_abuse(self):
        blocked = await self._replay(main._member_action_limiter, every=20.0, count=12)

        self.assertEqual(blocked, 0)


class HeavyWorkQueueTests(_LoadCase):
    async def test_analysis_requests_queue_instead_of_failing(self):
        """Only four analyses run at once; the rest must wait, not error.

        A queued member is told where they are in line. Dropping the request
        instead would make the busiest moment the one that looks broken.
        """
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_pipeline(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {}

        async def hold():
            async with main._sem:
                await slow_pipeline()

        holders = [asyncio.create_task(hold()) for _ in range(4)]
        await started.wait()
        await asyncio.sleep(0)

        self.assertTrue(main._sem.locked(), "the analysis queue lost its ceiling")
        release.set()
        await asyncio.gather(*holders)
        self.assertFalse(main._sem.locked())


class StreamingUnderLoadTests(_LoadCase):
    async def test_ten_simultaneous_random_picks_all_stream_a_result(self):
        """SSE is the delivery path for both pickers; it has to fan out."""
        import tempfile
        from pathlib import Path

        from app.cache import Cache

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cache = Cache(Path(temp.name) / "load.db")

        films = [
            SimpleNamespace(
                title="Stalker", slug="stalker", year=1979, director="Andrei Tarkovsky",
                poster_url="", genres=["Drama"], keywords=[], overview="",
                similarity=0.5, reason="", user_rating=None, letterboxd_rating=4.2,
                to_dict=lambda: {"title": "Stalker", "slug": "stalker"},
            )
        ]

        async def pool(*_args, **_kwargs):
            await asyncio.sleep(0.01)
            return films

        with (
            patch("app.main._community_random_pool", new=AsyncMock(side_effect=pool)),
            patch("app.main._attach_letterboxd_ratings", new=AsyncMock()),
            patch("app.main._make_cache", return_value=(None, cache)),
            patch("app.main._make_persistent_cache", return_value=cache),
        ):
            started = time.perf_counter()
            responses = await asyncio.gather(*(
                self.client.post("/api/random", json={"username": "load_fan"},
                                 headers={"X-CSRF-Token": "csrf-token"})
                for _ in range(10)
            ))
            elapsed = time.perf_counter() - started

        self.assertTrue(all(r.status_code == 200 for r in responses),
                        [r.status_code for r in responses])
        for response in responses:
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            self.assertTrue(events, "a stream carried no events")
            self.assertNotIn(
                "error", {event.get("type") for event in events},
                f"a stream errored under load: {events[-1]}",
            )
        self.assertLess(elapsed, 5.0, f"ten picks took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
