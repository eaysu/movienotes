"""One-time full watched-history crawl + incremental refresh runner.

Started in-process from ``POST /api/profile/sync``. A run walks the Letterboxd
diary window by window, checkpointing progress to ``profile_sync_jobs`` so a
Render free-tier restart mid-crawl is resumed on the user's next visit — the
``/api/profile/me`` poll re-spawns a job whose heartbeat has gone stale. No
external scheduler is required.

The runner is dependency-injected via a ``pipeline`` object (see
``main._SyncPipeline``) so it can be unit-tested without Supabase or the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .scraper import AccessBlockedError

log = logging.getLogger("uvicorn.error")

# `/films/` grid pages fetched per crawl step (~72 posters per page).
WATCHED_WINDOW_PAGES = 4
# Runaway-markup safety ceiling, intentionally above any realistic Letterboxd
# library. The crawler otherwise walks until Letterboxd returns an empty page.
FULL_MAX_FILMS = 50_000
# Director/keyword detail calls per checkpointed batch.
ENRICH_BATCH = 150
# A running job whose heartbeat is older than this is treated as abandoned.
HEARTBEAT_STALE_SECONDS = 180
# Process-wide cap on concurrent full crawls (the free tier is a single, small box).
MAX_CONCURRENT_JOBS = 1
LEASE_SECONDS = 360
# Cooldown after a hard failure before the job is retried.
FAILURE_BACKOFF = timedelta(minutes=30)
# A Letterboxd block is expected to be temporary.  Keep the durable checkpoint
# and retry it soon, instead of treating it like a data/configuration failure.
SCRAPE_RETRY_BACKOFF = timedelta(minutes=2)
# Opening the app should be cheap. A completed profile gets at most one
# opportunistic Letterboxd check per day; explicit refresh remains available.
INCREMENTAL_MIN_INTERVAL = timedelta(hours=24)

_job_sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_tasks: dict[int, asyncio.Task] = {}


# Letterboxd publishes the member's own film count on their profile header,
# and the grid the sweep walks lists that same set. A crawl that declares
# itself finished far below it did not reach the end. The margin is loose on
# purpose: the published figure lags its own diary slightly, and a sweep that
# retried a complete archive forever would be its own kind of failure.
SWEEP_COMPLETE_RATIO = 0.9


def _reached_the_end(processed: int, expected_total: int) -> bool:
    """Is an "empty page" the end of the grid, or a page without one?"""
    if expected_total <= 0:
        return True          # nothing to check it against; take it at its word
    return processed >= expected_total * SWEEP_COMPLETE_RATIO


@dataclass
class ScrapeWindow:
    """One persisted crawl window and the exact page that follows it."""

    films: list[dict]
    next_page: int
    exhausted: bool = False
    complete: bool = True


class IncompleteScrapeError(RuntimeError):
    """The upstream blocked a crawl after a safe page-level checkpoint."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_running(user_id: int) -> bool:
    task = _tasks.get(user_id)
    return bool(task and not task.done())


def job_needs_full_sweep(job: dict | None) -> bool:
    """True when no full sweep has ever completed for this user."""
    if not job:
        return True
    if job.get("scope") != "full":
        return True
    return job.get("state") != "done"


def job_is_resumable(job: dict | None, *, now: datetime | None = None) -> bool:
    """True when a job should be (re)started in this process now."""
    if not job:
        return False
    state = job.get("state")
    if state not in ("queued", "running", "failed"):
        return False
    now = now or _now()
    backoff = _parse_ts(job.get("backoff_until"))
    if backoff and backoff > now:
        return False
    lease_expires = _parse_ts(job.get("lease_expires_at"))
    if lease_expires and lease_expires > now:
        return False
    if state in ("queued", "failed"):
        # `failed` only reaches here once its 30-minute cooldown has elapsed.
        return True
    heartbeat = _parse_ts(job.get("heartbeat_at"))
    return (
        heartbeat is None
        or (now - heartbeat).total_seconds() > HEARTBEAT_STALE_SECONDS
    )


def incremental_due(job: dict | None, *, now: datetime | None = None) -> bool:
    """True when a completed full sweep is stale enough for a cheap refresh."""
    if not job or job.get("state") != "done" or job.get("scope") != "full":
        return False
    now = now or _now()
    last = _parse_ts(job.get("updated_at"))
    return last is None or (now - last) >= INCREMENTAL_MIN_INTERVAL


def progress_of(job: dict | None) -> dict | None:
    if not job:
        return None
    state = job.get("state") or "queued"
    total = int(job.get("films_total") or 0)
    processed = int(job.get("films_processed") or 0)
    if state == "done":
        percent = 100
    elif total > 0:
        percent = min(99, round(processed / total * 100))
    else:
        percent = 0
    # The reveal is intentionally gated on the final full-history snapshot.
    # A completed raw crawl is not enough: every row must first pass through
    # director/genre/keyword enrichment, then the taste aggregates are rebuilt.
    onboarding_ready = bool(state == "done" and job.get("scope") == "full")
    return {
        "state": state,
        "phase": job.get("phase") or "diary",
        "scope": job.get("scope") or "full",
        "processed": processed,
        "total": total,
        "percent": percent,
        "onboarding_ready": onboarding_ready,
        "error": job.get("last_error") or "",
    }


async def ensure_started(
    pipeline, service, account, *, scope: str = "full", force: bool = False
) -> dict | None:
    """Create/queue the job row when needed and spawn the runner if idle.

    ``force`` re-queues a completed sweep from scratch (explicit "refresh").
    Returns the current (possibly freshly created) job row.
    """
    job = await asyncio.to_thread(service.get_sync_job, account.id)
    if (
        not force
        and scope == "full"
        and job
        and job.get("state") == "done"
        and job.get("scope") == "full"
    ):
        return job  # a full sweep already finished

    if force or job is None or job.get("state") == "done":
        sync_run_id = str(uuid.uuid4()) if scope == "full" else None
        job = await asyncio.to_thread(
            service.upsert_sync_job,
            account.id,
            state="queued",
            phase="diary",
            scope=scope,
            cursor_page=1,
            films_processed=0,
            films_total=0,
            attempts=0,
            last_error="",
            backoff_until=None,
            sync_run_id=sync_run_id,
            lease_token=None,
            lease_expires_at=None,
        )
    elif job.get("state") == "failed":
        # Resume from the checkpoint rather than recrawling from page 1.
        job = await asyncio.to_thread(
            service.upsert_sync_job,
            account.id,
            state="queued",
            last_error="",
            backoff_until=None,
        )

    if not is_running(account.id) and job_is_resumable(job):
        start(pipeline, service, account)
    return job


def start(pipeline, service, account) -> None:
    if is_running(account.id):
        return
    task = asyncio.create_task(run_job(pipeline, service, account))
    _tasks[account.id] = task

    def _clear(done_task, user_id=account.id):
        if _tasks.get(user_id) is done_task:
            _tasks.pop(user_id, None)
        with contextlib.suppress(asyncio.CancelledError):
            done_task.exception()

    task.add_done_callback(_clear)


async def run_job(pipeline, service, account) -> None:
    uid = account.id
    lease_token = str(uuid.uuid4())
    claimed = await asyncio.to_thread(
        service.claim_sync_job, uid, lease_token, LEASE_SECONDS
    )
    if not claimed:
        log.warning("profile_sync lease BUSY user=%s", uid)
        return
    job = {}
    with contextlib.suppress(Exception):
        job = await asyncio.to_thread(service.get_sync_job, uid) or {}
    await _record_event(
        service,
        uid,
        "profile_sync_started",
        {"scope": job.get("scope") or "full"},
    )
    try:
        async with _job_sem:
            await _crawl(pipeline, service, account, lease_token=lease_token)
        finished = await asyncio.to_thread(service.get_sync_job, uid) or {}
        await _record_event(
            service,
            uid,
            "profile_sync_completed",
            {
                "scope": finished.get("scope") or job.get("scope") or "full",
                "films": int(finished.get("films_processed") or 0),
            },
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # keep the last good snapshot; retry after a cooldown
        log.warning("profile_sync job FAILED user=%s: %s", uid, exc)
        await _record_event(
            service,
            uid,
            "profile_sync_failed",
            {"error": type(exc).__name__},
        )
        with contextlib.suppress(Exception):
            backoff = (
                SCRAPE_RETRY_BACKOFF
                if isinstance(exc, (IncompleteScrapeError, AccessBlockedError))
                else FAILURE_BACKOFF
            )
            await asyncio.to_thread(
                service.touch_sync_job,
                uid,
                owned_by=lease_token,
                state="failed",
                last_error=str(exc)[:400],
                backoff_until=(_now() + backoff).isoformat(),
                lease_token=None,
                lease_expires_at=None,
            )


async def _touch(service, user_id: int, owner_token: str | None, **fields) -> None:
    release_lease = bool(fields.pop("_release_lease", False))
    if release_lease:
        lease_expires_at = None
    elif owner_token:
        lease_expires_at = (_now() + timedelta(seconds=LEASE_SECONDS)).isoformat()
    else:
        lease_expires_at = fields.pop("lease_expires_at", None)
    ok = await asyncio.to_thread(
        service.touch_sync_job,
        user_id,
        owned_by=owner_token,
        lease_expires_at=lease_expires_at,
        **fields,
    )
    if ok is False:
        raise RuntimeError("profile sync lease lost")


async def _record_event(
    service, user_id: int, event_type: str, metadata: dict | None = None
) -> None:
    """Record sync lifecycle telemetry without coupling the runner to schema."""
    recorder = getattr(service, "record_activity_event", None)
    if recorder is None:
        return
    with contextlib.suppress(Exception):
        await asyncio.to_thread(recorder, user_id, event_type, metadata or {})


async def _crawl(pipeline, service, account, *, lease_token: str | None = None) -> None:
    uid = account.id
    job = await asyncio.to_thread(service.get_sync_job, uid) or {}
    if job.get("scope") == "incremental":
        await _incremental(pipeline, service, account, lease_token=lease_token)
        return
    phase = job.get("phase") or "diary"
    cursor = int(job.get("cursor_page") or 1)
    processed = int(job.get("films_processed") or 0)
    attempts = int(job.get("attempts") or 0) + 1
    existing_run_id = job.get("sync_run_id")
    if not existing_run_id and phase == "diary" and cursor > 1:
        # One-time migration safety: old checkpoints did not mark rows with a
        # run id, so resuming them could falsely deactivate earlier pages.
        cursor = 1
        processed = 0
    sync_run_id = existing_run_id or str(uuid.uuid4())
    authoritative_run = bool(existing_run_id or (phase == "diary" and cursor == 1))
    await _touch(
        service,
        uid,
        lease_token,
        state="running",
        attempts=attempts,
        last_error="",
        sync_run_id=sync_run_id,
    )

    known: set[str] = set(await asyncio.to_thread(service.get_watched_slugs, uid))
    natural_end = phase != "diary"
    # Letterboxd publishes the member's own film count on their profile header,
    # and the diary crawl cannot know a total until it runs out of pages. Using
    # that figure as the expected total is what turns the progress reading from
    # a number that only climbs into "247 / 3,454" — the shape people read as
    # progress. It is an estimate, so the percentage is still capped below 100
    # and the real total replaces it at the enrichment phase.
    expected_total = int((getattr(account, "letterboxd_stats", None) or {}).get("films") or 0)

    # ── Phase 1 · walk the /films/ grid and persist each window ───────────
    # Do not put one TMDb search per film on the critical crawl path. Known
    # catalog metadata is hydrated locally; genuine misses are enriched after
    # all Letterboxd pages have been collected.
    while phase == "diary":
        raw_window = await pipeline.scrape_watched_window(account.username, cursor)
        # Keep lightweight test/extension pipelines that return a plain list
        # compatible, while production uses the resumable page checkpoint.
        if isinstance(raw_window, ScrapeWindow):
            window = raw_window.films
            next_cursor = max(cursor, int(raw_window.next_page or cursor))
            exhausted = raw_window.exhausted
            complete = raw_window.complete
        else:
            window = raw_window
            next_cursor = cursor + WATCHED_WINDOW_PAGES
            exhausted = not window
            complete = True
        if not window:
            if exhausted and _reached_the_end(processed, expected_total):
                phase = "enrich"
                natural_end = True
                break
            if exhausted:
                # The grid said it had ended, but the member's own film count
                # says otherwise. A rate-limited Letterboxd request answers 200
                # with a page that has no film grid on it, which parses as an
                # empty list and is indistinguishable from the real last page.
                # Believing it truncates the archive and — because the run is
                # then treated as authoritative — retires every film the crawl
                # never reached. Retry from this cursor instead.
                raise IncompleteScrapeError(
                    f"Letterboxd taraması @{account.username} için sayfa {cursor}'de "
                    f"bitti göründü ama {processed}/{expected_total} filmde duruyor; "
                    "kaldığı yerden yeniden denenecek."
                )
            raise IncompleteScrapeError(
                f"Letterboxd taraması @{account.username} için sayfa {cursor}'de veri döndürmedi."
            )
        fresh: list[dict] = []
        known_updates: list[dict] = []
        for index, film in enumerate(window):
            slug = (film.get("slug") or "").strip()
            if not slug:
                continue
            observed = {
                **film,
                "watched_rank": processed + index,
                "rating_observed": True,
                "last_seen_run_id": sync_run_id,
                "is_active": True,
            }
            if slug in known:
                known_updates.append(observed)
                continue
            # watched_rank is a running position: page order is "recently added"
            # first, so lower rank == more recent.
            fresh.append(observed)
            known.add(slug)
        if known_updates:
            await asyncio.to_thread(service.save_watched_films, uid, known_updates)
        if fresh:
            hydrate = getattr(pipeline, "hydrate_catalog", None)
            enriched = await hydrate(fresh) if hydrate else fresh
            source_by_slug = {film["slug"]: film for film in fresh}
            for row in enriched:
                source = source_by_slug.get(row.get("slug"), {})
                row.update(
                    {
                        "rating_observed": True,
                        "last_seen_run_id": sync_run_id,
                        "is_active": True,
                        "watched_rank": source.get("watched_rank"),
                    }
                )
            await asyncio.to_thread(service.save_watched_films, uid, enriched)
        processed += len(window)
        cursor = next_cursor
        await _touch(
            service,
            uid,
            lease_token,
            cursor_page=cursor,
            films_processed=processed,
            films_total=max(expected_total, processed),
        )
        if not complete:
            # The successful pages above are already durable.  Stop here so the
            # failed page is retried from this exact cursor; never skip a block
            # by blindly advancing to the next four-page window.
            raise IncompleteScrapeError(
                f"Letterboxd taraması @{account.username} için sayfa {cursor}'de engellendi; kaldığı yerden yeniden denenecek."
            )
        if processed >= FULL_MAX_FILMS:
            phase = "enrich"

    # ── Phase 2 · director/keyword details for rows still missing them ─────
    rows = await asyncio.to_thread(service.get_watched_films, uid)
    # Rows without a TMDb id still need the background search step; restricting
    # this list to known ids would permanently strand every cold-catalog film.
    pending = [r for r in rows if not r.get("details_loaded")]
    completed = len(rows) - len(pending)
    await _touch(
        service,
        uid,
        lease_token,
        phase="enrich",
        films_total=len(rows),
        films_processed=completed,
    )
    for offset in range(0, len(pending), ENRICH_BATCH):
        batch = pending[offset : offset + ENRICH_BATCH]
        detailed = await pipeline.enrich_details(batch)
        if detailed:
            await asyncio.to_thread(service.save_watched_films, uid, detailed)
        await _touch(
            service,
            uid,
            lease_token,
            films_processed=completed + offset + len(batch),
        )

    # ── Phase 3 · aggregate the full history into the snapshot ────────────
    await _touch(service, uid, lease_token, phase="aggregate")
    if natural_end and authoritative_run:
        await asyncio.to_thread(service.finalize_sync_run, uid, sync_run_id)
    total = await pipeline.rebuild_snapshot(account)
    await _touch(
        service,
        uid,
        lease_token,
        state="done",
        phase="done",
        scope="full",
        films_total=total,
        films_processed=total,
        last_error="",
        lease_token=None,
        _release_lease=True,
    )
    log.warning("profile_sync job DONE user=%s films=%d", uid, total)


async def _incremental(
    pipeline, service, account, *, lease_token: str | None = None
) -> None:
    """Cheap refresh of a completed sweep: only the last ~50 diary entries.

    New films are enriched and prepended (negative watched_rank keeps them
    ahead of the existing history); changed ratings are patched in place. The
    snapshot is re-aggregated every run (LLM prose only when something changed).
    """
    uid = account.id
    await _touch(
        service, uid, lease_token, state="running", phase="diary", last_error=""
    )
    known: set[str] = set(await asyncio.to_thread(service.get_watched_slugs, uid))
    existing = {
        row.get("film_slug"): row
        for row in await asyncio.to_thread(service.get_watched_films, uid)
    }
    recent = await pipeline.scrape_recent(account.username)

    new_films = [f for f in recent if f.get("slug") and f["slug"] not in known]
    rating_updates = [
        {
            "slug": f["slug"],
            "user_rating": f.get("user_rating"),
            "rating_observed": True,
        }
        for f in recent
        if f.get("slug") in existing
        and existing[f["slug"]].get("user_rating") != f["user_rating"]
    ]

    if new_films:
        for offset, film in enumerate(new_films):
            film["watched_rank"] = -(len(new_films) - offset)
            film["rating_observed"] = True
        enriched = await pipeline.enrich_search(new_films)
        await asyncio.to_thread(service.save_watched_films, uid, enriched)
        detailed = await pipeline.enrich_details(enriched)
        if detailed:
            await asyncio.to_thread(service.save_watched_films, uid, detailed)
    if rating_updates:
        await asyncio.to_thread(service.save_watched_films, uid, rating_updates)

    # Heal incomplete legacy/catalog rows too. Earlier builds could treat a
    # poster-only cache entry as a complete film and never retry its director
    # metadata; every incremental visit now repairs those rows in bounded batches.
    incomplete = [row for row in existing.values() if not row.get("details_loaded")]
    for offset in range(0, len(incomplete), ENRICH_BATCH):
        detailed = await pipeline.enrich_details(
            incomplete[offset : offset + ENRICH_BATCH]
        )
        if detailed:
            await asyncio.to_thread(service.save_watched_films, uid, detailed)

    # Always re-aggregate so the snapshot can never drift behind
    # user_watched_films; only spend an LLM call when something changed.
    await _touch(service, uid, lease_token, phase="aggregate")
    total = await pipeline.rebuild_snapshot(
        account, use_llm=bool(new_films or rating_updates or incomplete)
    )

    await _touch(
        service,
        uid,
        lease_token,
        state="done",
        phase="done",
        scope="full",
        films_total=total,
        films_processed=total,
        last_error="",
        lease_token=None,
        _release_lease=True,
    )
    log.warning(
        "profile_sync incremental user=%s new=%d rating_changes=%d",
        uid,
        len(new_films),
        len(rating_updates),
    )
