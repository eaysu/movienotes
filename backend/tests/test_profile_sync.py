import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import profile_sync
from app.scraper import AccessBlockedError


def _iso(dt):
    return dt.isoformat()


class JobHelperTests(unittest.TestCase):
    def test_job_needs_full_sweep(self):
        self.assertTrue(profile_sync.job_needs_full_sweep(None))
        self.assertTrue(profile_sync.job_needs_full_sweep({"state": "running", "scope": "full"}))
        self.assertTrue(
            profile_sync.job_needs_full_sweep({"state": "done", "scope": "incremental"})
        )
        self.assertFalse(
            profile_sync.job_needs_full_sweep({"state": "done", "scope": "full"})
        )

    def test_job_is_resumable_states(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(profile_sync.job_is_resumable({"state": "queued"}, now=now))
        self.assertFalse(
            profile_sync.job_is_resumable({"state": "done", "scope": "full"}, now=now)
        )
        # Fresh heartbeat → another worker owns it.
        self.assertFalse(
            profile_sync.job_is_resumable(
                {"state": "running", "heartbeat_at": _iso(now - timedelta(seconds=30))},
                now=now,
            )
        )
        # Stale heartbeat → abandoned, resume it.
        self.assertTrue(
            profile_sync.job_is_resumable(
                {"state": "running", "heartbeat_at": _iso(now - timedelta(minutes=10))},
                now=now,
            )
        )
        # Backoff window still open.
        self.assertFalse(
            profile_sync.job_is_resumable(
                {"state": "queued", "backoff_until": _iso(now + timedelta(minutes=5))},
                now=now,
            )
        )
        # A failed job retries once its cooldown has elapsed, not before.
        self.assertFalse(
            profile_sync.job_is_resumable(
                {"state": "failed", "backoff_until": _iso(now + timedelta(minutes=10))},
                now=now,
            )
        )
        self.assertTrue(
            profile_sync.job_is_resumable(
                {"state": "failed", "backoff_until": _iso(now - timedelta(minutes=1))},
                now=now,
            )
        )

    def test_incremental_due(self):
        now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(profile_sync.incremental_due(None, now=now))
        self.assertFalse(
            profile_sync.incremental_due(
                {"state": "running", "scope": "full"}, now=now
            )
        )
        self.assertFalse(
            profile_sync.incremental_due(
                {"state": "done", "scope": "full",
                 "updated_at": (now - timedelta(hours=23)).isoformat()},
                now=now,
            )
        )
        self.assertTrue(
            profile_sync.incremental_due(
                {"state": "done", "scope": "full",
                 "updated_at": (now - timedelta(hours=25)).isoformat()},
                now=now,
            )
        )

    def test_progress_of(self):
        self.assertIsNone(profile_sync.progress_of(None))
        mid = profile_sync.progress_of(
            {"state": "running", "phase": "diary", "films_processed": 40, "films_total": 200}
        )
        self.assertEqual(mid["percent"], 20)
        self.assertEqual(mid["phase"], "diary")
        self.assertFalse(mid["onboarding_ready"])
        reveal = profile_sync.progress_of(
            {"state": "running", "phase": "enrich", "films_processed": 200, "films_total": 200}
        )
        self.assertFalse(reveal["onboarding_ready"])
        done = profile_sync.progress_of(
            {"state": "done", "scope": "full", "films_processed": 812, "films_total": 812}
        )
        self.assertEqual(done["percent"], 100)
        self.assertTrue(done["onboarding_ready"])
        # Never report 100 before the job is actually done.
        almost = profile_sync.progress_of(
            {"state": "running", "films_processed": 999, "films_total": 1000}
        )
        self.assertEqual(almost["percent"], 99)


class FakeService:
    def __init__(self):
        self.job = None
        self.films = {}

    def get_sync_job(self, uid):
        return dict(self.job) if self.job else None

    def upsert_sync_job(self, uid, **fields):
        base = dict(self.job) if self.job else {"user_id": uid}
        base.update(fields)
        self.job = base
        return dict(base)

    def touch_sync_job(self, uid, **fields):
        fields.pop("owned_by", None)
        base = dict(self.job) if self.job else {"user_id": uid, "state": "running"}
        base.update(fields)
        now = datetime.now(timezone.utc).isoformat()
        base["heartbeat_at"] = now
        base["updated_at"] = now
        self.job = base
        return True

    def claim_sync_job(self, uid, lease_token, lease_seconds):
        self.touch_sync_job(uid, lease_token=lease_token)
        return True

    def finalize_sync_run(self, uid, sync_run_id):
        for row in self.films.values():
            row["is_active"] = row.get("last_seen_run_id") == sync_run_id
        return len(self.films)

    def get_watched_slugs(self, uid):
        return set(self.films)

    def get_watched_films(self, uid):
        return [dict(row) for row in self.films.values()]

    def save_watched_films(self, uid, rows):
        written = 0
        for row in rows:
            slug = row.get("slug") or row.get("film_slug")
            if not slug:
                continue
            cur = self.films.get(slug, {"film_slug": slug, "details_loaded": False})
            for key, value in row.items():
                if key == "slug":
                    continue
                if key in ("director", "genres", "keywords") and not value:
                    continue  # mirror the RPC: don't clobber with empties
                cur[key] = value
            cur["film_slug"] = slug
            if row.get("details_loaded"):
                cur["details_loaded"] = True
            self.films[slug] = cur
            written += 1
        return written


class FakePipeline:
    def __init__(self, service, pages):
        self.service = service
        self.pages = pages
        self.window_calls = []
        self.rebuilt_with = None
        self.search_calls = 0

    async def scrape_watched_window(self, username, start_page):
        self.window_calls.append(start_page)
        return [dict(f) for f in self.pages.get(start_page, [])]

    async def scrape_recent(self, username):
        return [dict(f) for f in getattr(self, "recent", [])]

    async def enrich_search(self, films):
        self.search_calls += 1
        out = []
        for i, film in enumerate(films):
            out.append(
                {
                    "slug": film["slug"],
                    "title": film.get("title") or "",
                    "release_year": film.get("year"),
                    "tmdb_id": 500 + i,
                    "genres": ["Drama"],
                    "user_rating": film.get("user_rating"),
                    "watched_rank": film.get("watched_rank"),
                    "details_loaded": False,
                }
            )
        return out

    async def enrich_details(self, rows):
        return [
            {
                "slug": row.get("film_slug") or row.get("slug"),
                "director": "Some Director",
                "genres": ["Drama"],
                "keywords": ["kw"],
                "details_loaded": True,
            }
            for row in rows
        ]

    async def rebuild_snapshot(self, account, *, use_llm=True, repair_all=True):
        self.rebuilt_with = account.id
        self.rebuilt_llm = use_llm
        self.rebuilt_repair_all = repair_all
        return len(self.service.films)


def _account(uid=7, username="film_fan"):
    return SimpleNamespace(id=uid, username=username)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        task = profile_sync._tasks.pop(7, None)
        if task and not task.done():
            task.cancel()

    async def test_full_sweep_walks_windows_then_enriches_and_aggregates(self):
        service = FakeService()
        service.job = {
            "user_id": 7,
            "state": "queued",
            "phase": "diary",
            "scope": "full",
            "cursor_page": 1,
            "films_processed": 0,
        }
        pages = {
            1: [
                {"slug": "a", "title": "A", "year": 2020, "user_rating": 4.5},
                {"slug": "b", "title": "B", "year": 2019, "user_rating": None},
                {"slug": "c", "title": "C", "year": 2018, "user_rating": 3.0},
            ],
            1 + profile_sync.WATCHED_WINDOW_PAGES: [
                {"slug": "d", "title": "D", "year": 2001, "user_rating": 5.0}
            ],
            1 + 2 * profile_sync.WATCHED_WINDOW_PAGES: [],
        }
        pipeline = FakePipeline(service, pages)

        await profile_sync._crawl(pipeline, service, _account())

        self.assertEqual(service.job["state"], "done")
        self.assertEqual(service.job["phase"], "done")
        self.assertEqual(service.job["films_total"], 4)
        self.assertEqual(set(service.films), {"a", "b", "c", "d"})
        # watched_rank is a running position across windows: 0,1,2 then 3.
        self.assertEqual(service.films["a"]["watched_rank"], 0)
        self.assertEqual(service.films["c"]["watched_rank"], 2)
        self.assertEqual(service.films["d"]["watched_rank"], 3)
        # Details were filled in for every row.
        self.assertTrue(all(row["details_loaded"] for row in service.films.values()))
        self.assertEqual(service.films["b"]["director"], "Some Director")
        self.assertEqual(pipeline.rebuilt_with, 7)
        self.assertEqual(pipeline.search_calls, 0)

    async def test_resume_starts_from_checkpoint_without_refetching_page_one(self):
        service = FakeService()
        service.films = {
            "a": {"film_slug": "a", "tmdb_id": 1, "details_loaded": True, "watched_rank": 0, "last_seen_run_id": "run-1"},
            "b": {"film_slug": "b", "tmdb_id": 2, "details_loaded": True, "watched_rank": 1, "last_seen_run_id": "run-1"},
            "c": {"film_slug": "c", "tmdb_id": 3, "details_loaded": True, "watched_rank": 2, "last_seen_run_id": "run-1"},
        }
        resume_cursor = 1 + profile_sync.WATCHED_WINDOW_PAGES
        service.job = {
            "user_id": 7,
            "state": "running",
            "phase": "diary",
            "scope": "full",
            "cursor_page": resume_cursor,
            "films_processed": 3,
            "sync_run_id": "run-1",
            "heartbeat_at": (
                datetime.now(timezone.utc) - timedelta(minutes=20)
            ).isoformat(),
        }
        pages = {
            resume_cursor: [{"slug": "d", "title": "D", "year": 2001, "user_rating": 5.0}],
            resume_cursor + profile_sync.WATCHED_WINDOW_PAGES: [],
        }
        pipeline = FakePipeline(service, pages)

        await profile_sync._crawl(pipeline, service, _account())

        self.assertNotIn(1, pipeline.window_calls)
        self.assertEqual(pipeline.window_calls[0], resume_cursor)
        self.assertEqual(set(service.films), {"a", "b", "c", "d"})
        self.assertEqual(service.job["state"], "done")

    async def test_incremental_adds_new_films_and_patches_changed_ratings(self):
        service = FakeService()
        service.films = {
            "a": {"film_slug": "a", "tmdb_id": 1, "details_loaded": True, "watched_rank": 0, "user_rating": 3.5},
            "b": {"film_slug": "b", "tmdb_id": 2, "details_loaded": True, "watched_rank": 1, "user_rating": None},
        }
        service.job = {"user_id": 7, "state": "done", "phase": "done", "scope": "incremental"}
        pipeline = FakePipeline(service, {})
        pipeline.recent = [
            {"slug": "d", "title": "D", "year": 2024, "user_rating": 5.0},
            {"slug": "a", "title": "A", "year": 2020, "user_rating": 4.5},
        ]

        await profile_sync._crawl(pipeline, service, _account())

        self.assertEqual(set(service.films), {"a", "b", "d"})
        self.assertLess(service.films["d"]["watched_rank"], 0)  # ahead of the history
        self.assertTrue(service.films["d"]["details_loaded"])
        self.assertEqual(service.films["a"]["user_rating"], 4.5)  # patched
        self.assertEqual(pipeline.rebuilt_with, 7)
        self.assertEqual(service.job["state"], "done")
        self.assertEqual(service.job["scope"], "full")

    async def test_incremental_reaggregates_without_llm_when_nothing_changed(self):
        service = FakeService()
        service.films = {
            "a": {"film_slug": "a", "tmdb_id": 1, "details_loaded": True, "user_rating": 4.5},
        }
        service.job = {"user_id": 7, "state": "done", "phase": "done", "scope": "incremental"}
        pipeline = FakePipeline(service, {})
        pipeline.recent = [{"slug": "a", "title": "A", "year": 2020, "user_rating": 4.5}]

        await profile_sync._crawl(pipeline, service, _account())

        # The snapshot is refreshed every run so it can't drift behind the store,
        # but an unchanged run must not spend an LLM call.
        self.assertEqual(pipeline.rebuilt_with, 7)
        self.assertFalse(pipeline.rebuilt_llm)
        self.assertEqual(service.job["state"], "done")
        self.assertEqual(service.job["scope"], "full")

    async def test_incremental_repairs_existing_poster_only_metadata(self):
        service = FakeService()
        service.films = {
            "a": {
                "film_slug": "a",
                "title": "A",
                "tmdb_id": None,
                "poster_url": "https://letterboxd.example/a.jpg",
                "details_loaded": False,
                "user_rating": 4.0,
            },
        }
        service.job = {
            "user_id": 7,
            "state": "done",
            "phase": "done",
            "scope": "incremental",
        }
        pipeline = FakePipeline(service, {})
        pipeline.recent = [
            {"slug": "a", "title": "A", "year": 2020, "user_rating": 4.0}
        ]

        await profile_sync._crawl(pipeline, service, _account())

        self.assertTrue(service.films["a"]["details_loaded"])
        self.assertEqual(service.films["a"]["director"], "Some Director")
        self.assertTrue(pipeline.rebuilt_llm)

    async def test_incremental_can_clear_a_removed_rating(self):
        service = FakeService()
        service.films = {
            "a": {
                "film_slug": "a",
                "tmdb_id": 1,
                "details_loaded": True,
                "user_rating": 4.5,
            },
        }
        service.job = {
            "user_id": 7,
            "state": "done",
            "phase": "done",
            "scope": "incremental",
        }
        pipeline = FakePipeline(service, {})
        pipeline.recent = [
            {"slug": "a", "title": "A", "year": 2020, "user_rating": None}
        ]

        await profile_sync._crawl(pipeline, service, _account())

        self.assertIsNone(service.films["a"]["user_rating"])
        self.assertTrue(pipeline.rebuilt_llm)

    async def test_full_refresh_does_not_stop_on_an_all_known_window(self):
        service = FakeService()
        service.films = {
            "known": {
                "film_slug": "known",
                "tmdb_id": 1,
                "details_loaded": True,
                "watched_rank": 0,
            }
        }
        service.job = {
            "user_id": 7,
            "state": "queued",
            "phase": "diary",
            "scope": "full",
            "cursor_page": 1,
            "films_processed": 0,
            "sync_run_id": "force-run",
        }
        pipeline = FakePipeline(
            service,
            {
                1: [{"slug": "known", "title": "Known", "year": 2020}],
                1 + profile_sync.WATCHED_WINDOW_PAGES: [
                    {"slug": "older", "title": "Older", "year": 1990}
                ],
                1 + 2 * profile_sync.WATCHED_WINDOW_PAGES: [],
            },
        )

        await profile_sync._crawl(pipeline, service, _account())

        self.assertEqual(
            pipeline.window_calls,
            [1, 1 + profile_sync.WATCHED_WINDOW_PAGES, 1 + 2 * profile_sync.WATCHED_WINDOW_PAGES],
        )
        self.assertIn("older", service.films)

    async def test_partial_window_checkpoints_before_the_blocked_page(self):
        service = FakeService()
        service.job = {
            "user_id": 7,
            "state": "queued",
            "phase": "diary",
            "scope": "full",
            "cursor_page": 1,
            "films_processed": 0,
            "sync_run_id": "partial-run",
        }

        class PartialPipeline(FakePipeline):
            async def scrape_watched_window(self, username, start_page):
                self.window_calls.append(start_page)
                return profile_sync.ScrapeWindow(
                    films=[{"slug": "saved-page-one", "title": "Saved", "year": 2024}],
                    next_page=2,
                    complete=False,
                )

        await profile_sync.run_job(PartialPipeline(service, {}), service, _account())

        self.assertEqual(service.job["state"], "failed")
        self.assertEqual(service.job["cursor_page"], 2)
        self.assertIn("saved-page-one", service.films)
        self.assertIn("sayfa 2", service.job["last_error"])
        self.assertTrue(service.job["backoff_until"])

    async def test_hard_failure_sets_failed_state_with_backoff(self):
        service = FakeService()
        service.job = {
            "user_id": 7,
            "state": "queued",
            "phase": "diary",
            "scope": "full",
            "cursor_page": 1,
        }

        class Boom(FakePipeline):
            async def scrape_watched_window(self, username, start_page):
                raise RuntimeError("letterboxd blocked")

        await profile_sync.run_job(Boom(service, {}), service, _account())

        self.assertEqual(service.job["state"], "failed")
        self.assertIn("blocked", service.job["last_error"])
        self.assertTrue(service.job["backoff_until"])
        self.assertFalse(profile_sync.is_running(7))

    async def test_upstream_access_block_uses_the_short_retry_backoff(self):
        service = FakeService()
        service.job = {
            "user_id": 7,
            "state": "queued",
            "phase": "diary",
            "scope": "full",
            "cursor_page": 1,
            "films_processed": 0,
            "films_total": 0,
        }

        class Blocked(FakePipeline):
            async def scrape_watched_window(self, username, start_page):
                raise AccessBlockedError("Letterboxd HTTP 403", status=403)

        started = datetime.now(timezone.utc)
        await profile_sync.run_job(Blocked(service, {}), service, _account())
        retry_at = datetime.fromisoformat(service.job["backoff_until"])

        self.assertLessEqual(retry_at - started, timedelta(minutes=3))


class TruncatedCrawlTests(unittest.IsolatedAsyncioTestCase):
    """Reported: "it could not scrape all the films."

    A rate-limited Letterboxd request answers 200 with a page that has no film
    grid on it. That parses as an empty list, which is exactly what the real
    last page looks like, so the sweep called itself finished — and because the
    run then counted as authoritative, `finalize_sync_run` retired every film
    it never reached. A short archive, reported as complete.
    """

    def _pipeline(self, pages):
        class _Pipeline:
            def __init__(self):
                self.calls = []

            async def scrape_watched_window(self, _username, cursor):
                self.calls.append(cursor)
                return pages[cursor]

            async def hydrate_catalog(self, films):
                return films

            async def enrich_details(self, batch):
                return batch

            async def rebuild_snapshot(self, _account):
                return 0

        return _Pipeline()

    def _service(self):
        store = {"films": {}, "job": {}, "finalized": []}

        def save(_uid, films):
            for film in films:
                store["films"][film["slug"]] = film
            return len(films)

        return SimpleNamespace(
            get_sync_job=lambda _uid: dict(store["job"]),
            upsert_sync_job=lambda _uid, **f: store["job"].update(f) or dict(store["job"]),
            touch_sync_job=lambda _uid, **f: store["job"].update(
                {k: v for k, v in f.items() if not k.startswith("_")}) or True,
            claim_sync_job=lambda *_a, **_k: True,
            get_watched_slugs=lambda _uid: set(store["films"]),
            get_watched_films=lambda _uid: list(store["films"].values()),
            save_watched_films=save,
            finalize_sync_run=lambda _uid, run: store["finalized"].append(run) or 0,
            record_activity_event=lambda *_a, **_k: None,
        ), store

    @staticmethod
    def _window(films, next_page, *, exhausted=False):
        return profile_sync.ScrapeWindow(
            films=films, next_page=next_page, exhausted=exhausted, complete=True
        )

    def _films(self, start, count):
        return [{"slug": f"film-{n}", "title": f"Film {n}"} for n in range(start, start + count)]

    async def test_an_empty_page_far_below_the_known_total_is_not_the_end(self):
        account = SimpleNamespace(
            id=1, username="gokcnen", letterboxd_stats={"films": 934}
        )
        pages = {
            1: self._window(self._films(0, 288), 5),
            5: self._window([], 6, exhausted=True),   # served without a grid
        }
        pipeline = self._pipeline(pages)
        service, store = self._service()

        with self.assertRaises(profile_sync.IncompleteScrapeError) as caught:
            await profile_sync._crawl(pipeline, service, account)

        # It says where it stopped and against what, so the log is diagnosable.
        self.assertIn("288/934", str(caught.exception))
        # And nothing was retired on the strength of a page that never loaded.
        self.assertEqual(store["finalized"], [])

    async def test_a_genuine_last_page_still_ends_the_crawl(self):
        account = SimpleNamespace(
            id=1, username="gokcnen", letterboxd_stats={"films": 300}
        )
        pages = {
            1: self._window(self._films(0, 288), 5),
            5: self._window([], 6, exhausted=True),
        }
        pipeline = self._pipeline(pages)
        service, store = self._service()

        await profile_sync._crawl(pipeline, service, account)

        # 288 of a published 300 is the end of the grid, not a blocked page.
        self.assertEqual(store["job"]["state"], "done")
        self.assertEqual(len(store["finalized"]), 1)

    async def test_a_profile_with_no_published_count_is_taken_at_its_word(self):
        """Nothing to check against is not a reason to refuse to finish."""
        account = SimpleNamespace(id=1, username="gokcnen", letterboxd_stats={})
        pages = {
            1: self._window(self._films(0, 40), 5),
            5: self._window([], 6, exhausted=True),
        }
        pipeline = self._pipeline(pages)
        service, store = self._service()

        await profile_sync._crawl(pipeline, service, account)

        self.assertEqual(store["job"]["state"], "done")


if __name__ == "__main__":
    unittest.main()
