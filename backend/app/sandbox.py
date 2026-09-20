"""An in-memory stand-in for AuthService, for throwaway sessions.

`python -m scripts.sandbox` runs the whole app against this: any public
Letterboxd name signs straight in, the real scraper reads that profile, the
real onboarding plays, and none of it is written anywhere. Everything lives in
the dictionaries below, so closing the process is the delete.

It answers the same calls the Supabase-backed service does. The ones that carry
the session — the account, its profile snapshot, its watched archive and its
sync job — are implemented; the social surfaces a solo visitor has nothing in
(feed, letters, Blends, notifications) answer empty rather than fail, which is
what an account with no history would see anyway.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from typing import Any

from .auth import Account, InvalidCredentialsError

# Anything not implemented below is answered from the shape its name implies,
# so a surface this session never populates stays empty instead of raising.
_EMPTY_BY_PREFIX: tuple[tuple[tuple[str, ...], Any], ...] = (
    (("list_", "search_", "get_rated_", "get_watched_films"), []),
    (("count_", "unread_"), 0),
)
# The scheduled sweeps ask for a cohort to work through; a solo session has
# none, and returning ``None`` there reads as a broken query, not an empty one.
_EMPTY_BY_SUFFIX: tuple[tuple[tuple[str, ...], Any], ...] = (
    (("_candidates", "_films", "_users", "_rows"), []),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SandboxAuthService:
    """One account, held in memory, discarded with the process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.account: Account | None = None
        self.token: str = ""
        self._taste: dict | None = None
        self._favorites: list[dict] = []
        self._films: dict[str, dict] = {}
        self._job: dict | None = None
        self._posters: dict[str, dict] = {}

    # ── session ───────────────────────────────────────────────────────────
    def open_session(self, profile) -> tuple[Account, str]:
        """Adopt a scraped public profile as this session's whole identity."""
        with self._lock:
            self.account = Account(
                id=1,
                auth_user_id="sandbox",
                username=profile.username,
                display_name=profile.display_name or profile.username,
                avatar_url=profile.avatar_url or "",
                account_status="active",
                profile_sync_status="pending",
                letterboxd_stats=profile.stats or {},
            )
            self._favorites = [
                {
                    "position": position,
                    "slug": film.slug,
                    "title": film.title,
                    "release_year": film.year,
                    "tmdb_id": None,
                    "poster_url": film.poster_url,
                }
                for position, film in enumerate(profile.favorite_films[:4], start=1)
                if film.slug and film.title
            ]
            self.token = secrets.token_urlsafe(24)
            return self.account, self.token

    def current_account(self, access_token: str) -> Account:
        with self._lock:
            if not self.account or not self.token or access_token != self.token:
                raise InvalidCredentialsError("Sandbox oturumu bulunamadı.")
            return self.account

    def revoke(self, *_args, **_kwargs) -> None:
        with self._lock:
            self.account = None
            self.token = ""
            self._films.clear()
            self._favorites.clear()
            self._taste = None
            self._job = None

    # ── schema probes ─────────────────────────────────────────────────────
    def check_schema(self) -> bool:
        return True

    def check_sync_schema(self) -> bool:
        return True

    # ── profile snapshot ──────────────────────────────────────────────────
    def get_profile(self, account: Account) -> dict:
        with self._lock:
            data = dict((self.account or account).__dict__)
            data.setdefault("discoverable", False)
            data.setdefault("letter_receiving_enabled", False)
            data.setdefault("private_account", False)
            data.setdefault("preferred_locale", "auto")
            return {
                "account": data,
                "taste": dict(self._taste) if self._taste else None,
                "favorite_films": list(self._favorites),
            }

    def save_profile_snapshot(self, account, profile, favorites, taste) -> None:
        with self._lock:
            self._taste = taste.to_dict()
            self._apply_identity(profile, favorites)

    def save_profile_identity_and_favorites(self, account, profile, favorites) -> None:
        with self._lock:
            self._apply_identity(profile, favorites)

    def _apply_identity(self, profile, favorites) -> None:
        if self.account is not None:
            self.account.display_name = profile.display_name or self.account.display_name
            self.account.avatar_url = profile.avatar_url or self.account.avatar_url
            self.account.letterboxd_stats = profile.stats or self.account.letterboxd_stats
        rows = [
            {
                "position": position,
                "slug": film.slug,
                "title": film.title,
                "release_year": film.year,
                "tmdb_id": getattr(film, "tmdb_id", None),
                "poster_url": film.poster_url,
            }
            for position, film in enumerate(favorites[:4], start=1)
            if film.slug and film.title
        ]
        if rows:
            self._favorites = rows

    def clear_taste_narrative(self, _account) -> None:
        with self._lock:
            if self._taste:
                self._taste.pop("analysis", None)

    def complete_onboarding(self, account: Account) -> str:
        with self._lock:
            stamp = _now()
            if self.account is not None:
                self.account.onboarding_completed_at = stamp
            return stamp

    def mark_sync_status(self, _user_id: int, status: str) -> None:
        with self._lock:
            if self.account is not None:
                self.account.profile_sync_status = status

    def set_discoverable(self, account: Account, visible: bool) -> bool:
        account.discoverable = bool(visible)
        return bool(visible)

    def set_private_account(self, account: Account, private: bool) -> bool:
        account.private_account = bool(private)
        return bool(private)

    def set_letter_receiving(self, account: Account, enabled: bool) -> bool:
        return bool(enabled)

    def letter_receiving_status(self, _account) -> bool:
        return False

    def set_preferred_locale(self, account: Account, locale: str) -> str:
        account.preferred_locale = locale
        return locale

    def social_stats(self, _account) -> dict:
        return {"followers": 0, "following": 0, "posts": 0}

    def list_blends(self, _account) -> dict:
        return {"blends": [], "requests": []}

    def list_feed(self, _account, **_kwargs) -> dict:
        return {"posts": [], "next_cursor": ""}

    def letter_send_status(self, _account, _username: str = "") -> dict:
        return {"can_send": False, "reason": "sandbox"}

    def record_activity_event(self, *_args, **_kwargs) -> None:
        return None

    # ── watched archive ───────────────────────────────────────────────────
    def save_watched_films(self, _user_id: int, films: list[dict]) -> int:
        written = 0
        with self._lock:
            for film in films:
                slug = (film.get("slug") or film.get("film_slug") or "").strip()
                if not slug:
                    continue
                row = dict(self._films.get(slug) or {})
                row.update({key: value for key, value in film.items() if value is not None})
                # The store reads back the way Supabase does, so everything
                # downstream of it is exercised unchanged.
                row["film_slug"] = slug
                row.setdefault("release_year", film.get("year"))
                row.setdefault("is_active", True)
                self._films[slug] = row
                written += 1
        return written

    @staticmethod
    def _film_row(row: dict) -> dict:
        return {
            "slug": row.get("film_slug") or "",
            "title": row.get("title") or "",
            "director": row.get("director") or "",
            "year": row.get("release_year") or row.get("year"),
            "user_rating": row.get("user_rating"),
            "poster_url": row.get("poster_url") or "",
            "tmdb_id": row.get("tmdb_id"),
        }

    def _ordered(self) -> list[dict]:
        return sorted(
            (row for row in self._films.values() if row.get("is_active", True)),
            key=lambda row: (row.get("watched_rank") is None, row.get("watched_rank") or 0),
        )

    def get_watched_films(self, _user_id: int) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._ordered()]

    def get_watched_slugs(self, _user_id: int) -> set[str]:
        with self._lock:
            return {slug for slug, row in self._films.items() if row.get("is_active", True)}

    def count_watched_films(self, user_id: int) -> int:
        return len(self.get_watched_slugs(user_id))

    def list_recent_watched(self, _user_id: int, limit: int = 10) -> list[dict]:
        with self._lock:
            return [self._film_row(row) for row in self._ordered()[:limit]]

    def list_watched_for_picker(self, _user_id: int, query: str = "", limit: int = 60) -> list[dict]:
        needle = query.strip().casefold()
        with self._lock:
            rows = [
                self._film_row(row) for row in self._ordered()
                if not needle or needle in str(row.get("title", "")).casefold()
            ]
        return rows[:limit]

    def watched_films_by_slugs(self, _user_id: int, slugs: list[str]) -> dict[str, dict]:
        with self._lock:
            return {
                slug: self._film_row(self._films[slug])
                for slug in slugs if slug in self._films
            }

    def watched_film_by_slug(self, _user_id: int, slug: str) -> dict | None:
        with self._lock:
            row = self._films.get(slug)
            return self._film_row(row) if row else None

    def get_rated_watched_films(self, _user_id: int, *_args, **_kwargs) -> list[dict]:
        with self._lock:
            return [
                self._film_row(row) for row in self._ordered()
                if row.get("user_rating") is not None
            ]

    # ── background sync job ───────────────────────────────────────────────
    def get_sync_job(self, _user_id: int) -> dict | None:
        with self._lock:
            return dict(self._job) if self._job else None

    def upsert_sync_job(self, user_id: int, **fields) -> dict:
        with self._lock:
            job = dict(self._job or {"user_id": user_id})
            job.update(fields)
            job["updated_at"] = _now()
            self._job = job
            return dict(job)

    def touch_sync_job(self, user_id: int, *, owned_by: str | None = None, **fields) -> bool:
        with self._lock:
            if self._job is None:
                return False
            if owned_by and self._job.get("lease_token") != owned_by:
                return False
            self._job.update(fields)
            self._job["heartbeat_at"] = _now()
            return True

    def claim_sync_job(self, user_id: int, lease_token: str, lease_seconds: int = 360) -> bool:
        with self._lock:
            job = dict(self._job or {"user_id": user_id})
            job.update({"lease_token": lease_token, "heartbeat_at": _now()})
            self._job = job
            return True

    def finalize_sync_run(self, _user_id: int, sync_run_id: str) -> int:
        """Retire rows the latest crawl did not see, exactly as the SQL does."""
        dropped = 0
        with self._lock:
            for row in self._films.values():
                if row.get("last_seen_run_id") not in (None, sync_run_id) and row.get("is_active", True):
                    row["is_active"] = False
                    dropped += 1
        return dropped

    # ── shared film catalog ───────────────────────────────────────────────
    def save_film_posters(self, rows: list[dict]) -> None:
        with self._lock:
            for row in rows or []:
                slug = row.get("slug")
                if slug:
                    self._posters[slug] = row

    def get_film_posters(self, slugs) -> dict[str, str]:
        with self._lock:
            return {
                slug: self._posters[slug].get("poster_url") or ""
                for slug in (slugs or []) if slug in self._posters
            }

    def get_film_assets(self, slugs) -> dict[str, dict]:
        with self._lock:
            return {slug: self._posters[slug] for slug in (slugs or []) if slug in self._posters}

    def get_film_posters_by_tmdb_ids(self, _ids) -> dict:
        return {}

    def film_catalog_entry(self, slug: str) -> dict:
        with self._lock:
            return dict(self._posters.get(slug) or {})

    # ── everything this session cannot populate ───────────────────────────
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        for names, empty in _EMPTY_BY_PREFIX:
            if name.startswith(names):
                return lambda *args, **kwargs: (
                    list(empty) if isinstance(empty, list) else empty
                )
        for names, empty in _EMPTY_BY_SUFFIX:
            if name.endswith(names):
                return lambda *args, **kwargs: (
                    list(empty) if isinstance(empty, list) else empty
                )
        return lambda *args, **kwargs: None
