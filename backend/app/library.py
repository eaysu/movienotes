"""Member-owned film library, private ZIP archive and resumable TMDb matching."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from .catalog import Catalog, public_film
from .enrich import EnrichedFilm
from .taste_profile import build_taste_profile, personality_from_favorites
from .scraper import ScrapedProfile
from .llm import analyze_taste

_import_tasks: dict[str, asyncio.Task] = {}


def enriched(film: dict, rating=None) -> EnrichedFilm:
    return EnrichedFilm(
        title=film["title"],
        slug=film["slug"],
        year=film.get("year"),
        tmdb_id=film.get("tmdb_id"),
        director=film.get("director") or "",
        genres=film.get("genres") or [],
        keywords=film.get("keywords") or [],
        overview=film.get("overview") or "",
        poster_url=film.get("poster_url") or None,
        user_rating=rating,
        details_loaded=bool(film.get("details_loaded")),
    )


class Library:
    def __init__(self, service, settings):
        self.service, self.settings = service, settings
        self.catalog = Catalog(service, settings)
        self.memory = self.catalog.memory
        if self.memory and not hasattr(service, "_library"):
            service._library, service._imports, service._import_entries = {}, {}, {}

    def list(self, user_id: int) -> list[dict]:
        if self.memory:
            return [
                {**r, "film": self.catalog.get(r["film_id"])}
                for (uid, _), r in self.service._library.items()
                if uid == user_id
            ]
        all_rows = []
        for offset in range(0, 50000, 1000):
            rows = (
                self.service._service_client()
                .table("user_films")
                .select("*,film:films(*)")
                .eq("user_id", user_id)
                .order("updated_at", desc=True)
                .range(offset, offset + 999)
                .execute()
                .data
                or []
            )
            all_rows.extend(
                {**r, "film": public_film(r["film"])} for r in rows if r.get("film")
            )
            if len(rows) < 1000:
                break
        return all_rows

    def update(
        self,
        user_id: int,
        film: dict,
        *,
        watched=None,
        watchlist=None,
        rating=None,
        native=True,
    ) -> dict:
        if self.memory:
            key = (user_id, film["id"])
            old = self.service._library.get(key, {})
            row = {**old, "user_id": user_id, "film_id": film["id"]}
            row["watched"] = (
                (
                    bool(watched)
                    if native
                    else (
                        old.get("watched", False)
                        if old.get("watched_source") == "native"
                        else bool(watched) or old.get("watched", False)
                    )
                )
                if watched is not None
                else old.get("watched", False)
            )
            if native and watched is not None:
                row["watched_source"] = "native"
            row["watchlist"] = (
                (
                    bool(watchlist)
                    if native
                    else (
                        old.get("watchlist", False)
                        if old.get("watchlist_source") == "native"
                        else bool(watchlist) or old.get("watchlist", False)
                    )
                )
                if watchlist is not None
                else old.get("watchlist", False)
            )
            if native and watchlist is not None:
                row["watchlist_source"] = "native"
            if rating is not None and (native or old.get("rating_source") != "native"):
                row.update(
                    rating=rating, rating_source="native" if native else "letterboxd"
                )
            self.service._library[key] = row
        elif not native:
            self.service._service_client().rpc(
                "merge_import_library",
                dict(
                    p_user_id=user_id,
                    p_film_id=film["id"],
                    p_watched=bool(watched),
                    p_watchlist=bool(watchlist),
                    p_rating=rating,
                ),
            ).execute()
            rows = (
                self.service._service_client()
                .table("user_films")
                .select("*")
                .eq("user_id", user_id)
                .eq("film_id", film["id"])
                .execute()
                .data
                or []
            )
            row = rows[0]
        else:
            patch = {
                name: value
                for name, value in {
                    "watched": watched,
                    "watchlist": watchlist,
                    "rating": rating,
                }.items()
                if value is not None
            }
            row = (
                self.service._service_client()
                .rpc(
                    "set_library_state",
                    dict(p_user_id=user_id, p_film_id=film["id"], p_patch=patch),
                )
                .execute()
                .data
            )
        self.mirror(user_id, film, row)
        return row

    def mirror(self, user_id, film, row):
        if not row.get("watched"):
            if self.memory:
                for value in self.service._films.values():
                    if value.get("film_id") == film["id"]:
                        value["is_active"] = False
            else:
                self.service._service_client().table("user_watched_films").update(
                    {"is_active": False}
                ).eq("user_id", user_id).eq("film_id", film["id"]).execute()
            return
        legacy = dict(
            slug=film["slug"],
            is_active=True,
            film_id=film["id"],
            title=film["title"],
            release_year=film.get("year"),
            tmdb_id=film.get("tmdb_id"),
            director=film.get("director") or "",
            genres=film.get("genres") or [],
            keywords=film.get("keywords") or [],
            poster_url=film.get("poster_url") or "",
            user_rating=row.get("rating"),
            rating_observed=row.get("rating") is not None,
            details_loaded=bool(film.get("details_loaded")),
        )
        if self.memory:
            self.service.save_watched_films(user_id, [legacy])
        else:
            existing = (
                self.service._service_client()
                .table("user_watched_films")
                .select("film_slug")
                .eq("user_id", user_id)
                .eq("film_id", film["id"])
                .limit(1)
                .execute()
                .data
                or []
            )
            legacy["film_slug"] = (
                existing[0]["film_slug"] if existing else legacy["slug"]
            )
            legacy.pop("slug")
            legacy["user_id"] = user_id
            self.service._service_client().table("user_watched_films").upsert(
                legacy, on_conflict="user_id,film_slug"
            ).execute()

    def save_favorites(self, account, films):
        if self.memory:
            self.service._favorites = [
                dict(
                    position=i + 1,
                    film_id=f["id"],
                    slug=f["slug"],
                    title=f["title"],
                    release_year=f.get("year"),
                    tmdb_id=f.get("tmdb_id"),
                    poster_url=f.get("poster_url"),
                )
                for i, f in enumerate(films)
            ]
            self.service.mark_sync_status(account.id, "ready")
        else:
            self.service._service_client().rpc(
                "save_native_favorites",
                dict(p_user_id=account.id, p_films=[{"id": f["id"]} for f in films]),
            ).execute()
        account.profile_source = "native"
        account.profile_sync_status = "ready"
        # Favorite films are explicit positive preference references, and count
        # as known watched films without fabricating a rating or a dated log.
        for film in films:
            self.update(account.id, film, watched=True, native=False)

    async def rebuild(self, account, locale="tr", *, use_llm=False):
        profile = await asyncio.to_thread(self.service.get_profile, account)
        favorites = []
        for row in profile.get("favorite_films") or []:
            identity = row.get("film_id")
            if not identity and (row.get("slug") or "").startswith("movie-"):
                identity = row["slug"][6:]
            if identity:
                film = await self.catalog.details(identity)
            else:
                film = await asyncio.to_thread(
                    self.catalog.by_alias, "letterboxd", row.get("slug") or ""
                )
            if film:
                favorites.append(enriched(film))
        rows = await asyncio.to_thread(self.list, account.id)
        watched = [
            enriched(r["film"], r.get("rating"))
            for r in rows
            if r.get("watched") and r.get("film")
        ]
        taste = build_taste_profile(watched, favorites)
        taste.personality = personality_from_favorites(favorites)
        if len(watched) <= 4 and favorites:
            taste.summary = (
                "Seçtiğin dört film ilk zevk profilini oluşturuyor. Yeni notların ve puanlarınla bu okuma gelişecek."
                if locale == "tr"
                else "Your four favourites form your first taste profile. This reading will grow with your notes and ratings."
            )
            taste.analysis = [taste.summary]

        generated = {}
        if use_llm and favorites:
            try:
                generated = await asyncio.wait_for(
                    analyze_taste(
                        self.settings, watched or favorites, favorites, locale=locale
                    ),
                    timeout=35,
                )
            except asyncio.TimeoutError:
                pass
        if generated.get("analysis"):
            taste.analysis = generated["analysis"]
            taste.personality = generated.get("personality") or taste.personality
            taste.analysis_source = "llm"
        elif (profile.get("taste") or {}).get("analysis"):
            # Updating a note must not erase the previously generated prose.
            taste.analysis = profile["taste"]["analysis"]
            taste.personality = profile["taste"].get("personality") or taste.personality
        taste.localized_narratives = {
            **((profile.get("taste") or {}).get("localized_narratives") or {}),
            locale: {"analysis": taste.analysis, "personality": taste.personality},
        }
        latest = await asyncio.to_thread(self.service.get_profile, account)
        before = [row.get("slug") for row in profile.get("favorite_films") or []]
        after = [row.get("slug") for row in latest.get("favorite_films") or []]
        if before != after:
            return {
                **latest,
                "analysis_used": False,
                "analysis_stale": True,
                "analysis_available": self.settings.has_openai,
            }
        identity = ScrapedProfile(
            username=account.username,
            display_name=account.display_name,
            avatar_url=account.avatar_url,
            favorite_films=[],
            stats=account.letterboxd_stats or {},
        )
        await asyncio.to_thread(
            self.service.save_profile_snapshot, account, identity, favorites, taste
        )
        status = "ready" if len(favorites) == 4 or account.profile_source != "native" else "pending"
        await asyncio.to_thread(self.service.mark_sync_status, account.id, status)
        result = await asyncio.to_thread(self.service.get_profile, account)
        result["analysis_used"] = bool(generated.get("analysis"))
        result["analysis_available"] = self.settings.has_openai
        return result

    def publish(self, account, film, body, rating, spoiler):
        if self.memory:
            self.update(account.id, film, watched=True, rating=rating)
            return self.service.create_post(
                account,
                dict(
                    body=body,
                    film_id=film["id"],
                    film_slug=film["slug"],
                    tmdb_id=film.get("tmdb_id"),
                    film_title=film["title"],
                    film_year=film.get("year"),
                    user_rating=rating,
                    spoiler=spoiler,
                ),
            )
        result = (
            self.service._service_client()
            .rpc(
                "publish_film_note",
                dict(
                    p_user_id=account.id,
                    p_film_id=film["id"],
                    p_body=body,
                    p_rating=rating,
                    p_spoiler=spoiler,
                ),
            )
            .execute()
        )
        return result.data

    def imports(self, user_id):
        if self.memory:
            return [
                dict(j)
                for j in self.service._imports.values()
                if j["user_id"] == user_id
            ]
        return (
            self.service._service_client()
            .table("library_imports")
            .select("id,state,counts,last_error,source_username,created_at,updated_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
            .data
            or []
        )

    def prepare_import(self, account, export, fingerprint):
        old = (
            next(
                (
                    j
                    for j in self.imports(account.id)
                    if j.get("fingerprint") == fingerprint
                ),
                None,
            )
            if self.memory
            else None
        )
        if not self.memory:
            rows = (
                self.service._service_client()
                .table("library_imports")
                .select("*")
                .eq("user_id", account.id)
                .eq("fingerprint", fingerprint)
                .limit(1)
                .execute()
                .data
                or []
            )
            old = rows[0] if rows else None
        if old and old["state"] in ("done", "queued", "running"):
            return old
        job = old or dict(
            id=str(
                uuid5(NAMESPACE_URL, f"movienotes:import:{account.id}:{fingerprint}")
            ),
            user_id=account.id,
            fingerprint=fingerprint,
            source_username=export.username,
            state="staging",
            counts={},
            last_error="",
        )
        previous = {r["source_key"]: r for r in self.archive_rows(account.id)}
        records = []
        for kind, rows in (
            ("watched", export.watched),
            ("watchlist", export.watchlist),
            ("favorite", export.favorites),
        ):
            for item in rows:
                source_key = kind + ":" + item["slug"]
                records.append(
                    dict(
                        user_id=account.id,
                        source_key=source_key,
                        import_id=job["id"],
                        kind=kind,
                        data=item,
                        match_status="pending",
                    )
                )
        for item in export.entries:
            records.append(
                dict(
                    user_id=account.id,
                    source_key=item["source_key"],
                    import_id=job["id"],
                    kind="entry",
                    data=item,
                    match_status="pending",
                )
            )
        for row in records:
            prior = previous.get(row["source_key"], {})
            confirmed = (prior.get("data") or {}).get("_confirmed_film_id")
            if confirmed:
                row["data"] = {**row["data"], "_confirmed_film_id": confirmed}
        job["counts"] = {
            **export.source_counts,
            "entries": len(export.entries),
            "favorites": len(export.favorites),
            "records": len(records),
            "processed": 0,
            "unresolved": 0,
        }
        if self.memory:
            self.service._imports[job["id"]] = job
            for r in records:
                previous = self.service._import_entries.get(
                    (account.id, r["source_key"]), {}
                )
                self.service._import_entries[(account.id, r["source_key"])] = {
                    **previous,
                    **r,
                }
        else:
            self.service._service_client().table("library_imports").upsert(
                job, on_conflict="user_id,fingerprint"
            ).execute()
            for offset in range(0, len(records), 200):
                self.service._service_client().table("imported_film_entries").upsert(
                    records[offset : offset + 200], on_conflict="user_id,source_key"
                ).execute()
        self.patch_job(job["id"], state="queued", lease_until=None)
        job["state"] = "queued"
        return job

    def patch_job(self, job_id, **values):
        values["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self.memory:
            self.service._imports[job_id].update(values)
        else:
            self.service._service_client().table("library_imports").update(values).eq(
                "id", job_id
            ).execute()

    def pending(self, job_id):
        if self.memory:
            return [
                r
                for r in self.service._import_entries.values()
                if r["import_id"] == job_id and r["match_status"] == "pending"
            ][:50]
        return (
            self.service._service_client()
            .table("imported_film_entries")
            .select("*")
            .eq("import_id", job_id)
            .eq("match_status", "pending")
            .order("source_key")
            .limit(50)
            .execute()
            .data
            or []
        )

    def patch_entry(self, row, **values):
        if self.memory:
            self.service._import_entries[(row["user_id"], row["source_key"])].update(
                values
            )
        else:
            self.service._service_client().table("imported_film_entries").update(
                values
            ).eq("user_id", row["user_id"]).eq(
                "source_key", row["source_key"]
            ).execute()

    def archive_rows(self, user_id):
        if self.memory:
            return [
                dict(row)
                for row in self.service._import_entries.values()
                if row["user_id"] == user_id
            ]
        rows = []
        for offset in range(0, 200000, 1000):
            page = (
                self.service._service_client()
                .table("imported_film_entries")
                .select("*")
                .eq("user_id", user_id)
                .order("source_key")
                .range(offset, offset + 999)
                .execute()
                .data
                or []
            )
            rows.extend(page)
            if len(page) < 1000:
                break
        return rows

    def progress(self, job_id):
        if self.memory:
            rows = [
                r
                for r in self.service._import_entries.values()
                if r["import_id"] == job_id
            ]
            return {
                "processed": sum(r["match_status"] != "pending" for r in rows),
                "unresolved": sum(r["match_status"] == "unresolved" for r in rows),
            }
        count = lambda status, neg=False: (
            self.service._service_client()
            .table("imported_film_entries")
            .select("source_key", count="exact")
            .eq("import_id", job_id)
            .neq("match_status", status)
            .limit(0)
            .execute()
            .count
            if neg
            else self.service._service_client()
            .table("imported_film_entries")
            .select("source_key", count="exact")
            .eq("import_id", job_id)
            .eq("match_status", status)
            .limit(0)
            .execute()
            .count
        )
        return {"processed": count("pending", True), "unresolved": count("unresolved")}

    def unresolved(self, user_id):
        if self.memory:
            rows = [
                r
                for r in self.service._import_entries.values()
                if r["user_id"] == user_id and r["match_status"] == "unresolved"
            ]
        else:
            rows = (
                self.service._service_client()
                .table("imported_film_entries")
                .select("film_id,data,kind,match_status")
                .eq("user_id", user_id)
                .eq("match_status", "unresolved")
                .limit(500)
                .execute()
                .data
                or []
            )
        unique = {
            r["film_id"]: self.catalog.get(r["film_id"])
            for r in rows
            if r.get("film_id")
        }
        return [f for f in unique.values() if f]

    def merge(self, user_id, source, target):
        if not any(f["id"] == source for f in self.unresolved(user_id)):
            raise LookupError("Bu eşleşme hesabında bulunamadı.")
        self._move_identity(user_id, source, target)

    def _move_identity(self, user_id, source, target):
        if source == target["id"]:
            return
        if self.memory:
            old = self.service._library.pop((user_id, source), {})
            prior = dict(self.service._library.get((user_id, target["id"]), {}))
            self.update(
                user_id,
                target,
                watched=old.get("watched"),
                watchlist=old.get("watchlist"),
                rating=old.get("rating"),
                native=False,
            )
            merged = self.service._library[(user_id, target["id"])]
            for field in ("rating", "watched", "watchlist"):
                flag = field + "_source"
                if old.get(flag) == "native" and prior.get(flag) != "native":
                    merged[field], merged[flag] = old.get(field), "native"
            self.mirror(user_id, target, merged)
            for post in self.service._posts.values():
                if post.get("author_id") == user_id and post.get("film_id") == source:
                    post.update(film_id=target["id"], film_slug=target["slug"], film_title=target["title"], film_year=target.get("year"), tmdb_id=target.get("tmdb_id"))
            for row in self.service._import_entries.values():
                if row["user_id"] == user_id and row.get("film_id") == source:
                    row.update(
                        film_id=target["id"],
                        match_status="confirmed",
                        data={**row["data"], "_confirmed_film_id": target["id"]},
                    )
        else:
            self.service._service_client().rpc(
                "merge_imported_film",
                dict(p_user_id=user_id, p_from=source, p_to=target["id"]),
            ).execute()
        if self.memory:
            for row in self.service._films.values():
                if row.get("film_id") == source:
                    row["is_active"] = False
        else:
            self.service._service_client().table("user_watched_films").update(
                {"is_active": False}
            ).eq("user_id", user_id).eq("film_id", source).execute()
            current = (
                self.service._service_client()
                .table("user_films")
                .select("*")
                .eq("user_id", user_id)
                .eq("film_id", target["id"])
                .execute()
                .data
                or []
            )
            if current:
                self.mirror(user_id, target, current[0])

    async def process_import(self, account, job):
        if self.memory:
            if job.get("state") == "running":
                return
        else:
            claimed = await asyncio.to_thread(
                lambda: self.service._service_client()
                .rpc("claim_library_import", dict(p_import_id=job["id"]))
                .execute()
                .data
            )
            if not claimed:
                return
        await asyncio.to_thread(self.patch_job, job["id"], state="running")
        counts = dict(job.get("counts") or {})
        matched_cache = {}
        try:
            while True:
                rows = await asyncio.to_thread(self.pending, job["id"])
                if not rows:
                    break
                for row in rows:
                    data = row["data"]
                    key = (
                        data.get("slug") or data.get("source_uri") or row["source_key"]
                    )
                    confirmed = data.get("_confirmed_film_id")
                    film = (
                        await asyncio.to_thread(self.catalog.get, confirmed)
                        if confirmed
                        else matched_cache.get(key)
                    )
                    if not film:
                        film = await asyncio.to_thread(
                            self.catalog.import_placeholder, data
                        )
                        if self.settings.has_tmdb and not data.get("identity_ambiguous"):
                            target = await self.catalog.match(film)
                            if target:
                                original = film
                                film = await self.catalog.details(target["id"])
                                # An earlier export may already have used the
                                # unresolved identity. Consolidate its notes and
                                # native edits before applying the new records.
                                await asyncio.to_thread(self._move_identity, account.id, original["id"], film)
                                if data.get("slug"):
                                    await asyncio.to_thread(
                                        self.catalog.alias,
                                        "letterboxd",
                                        data["slug"],
                                        film["id"],
                                    )
                            await asyncio.sleep(0.3)
                        matched_cache[key] = film
                    if row["kind"] in ("watched", "watchlist"):
                        await asyncio.to_thread(
                            self.update,
                            account.id,
                            film,
                            watched=row["kind"] == "watched",
                            watchlist=row["kind"] == "watchlist",
                            rating=data.get("user_rating"),
                            native=False,
                        )
                    await asyncio.to_thread(
                        self.patch_entry,
                        row,
                        film_id=film["id"],
                        match_status=(
                            "confirmed"
                            if confirmed
                            else ("matched" if film.get("tmdb_id") else "unresolved")
                        ),
                    )
                    # Renew the lease while a large archive is being matched.
                    # Slow provider requests must not let another worker take over.
                    await asyncio.to_thread(
                        self.patch_job,
                        job["id"],
                        lease_until=(
                            datetime.now(timezone.utc) + timedelta(minutes=5)
                        ).isoformat(),
                    )
                counts.update(await asyncio.to_thread(self.progress, job["id"]))
                await asyncio.to_thread(
                    self.patch_job,
                    job["id"],
                    counts=counts,
                    lease_until=(
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                )
            await asyncio.to_thread(
                self.patch_job, job["id"], state="done", counts=counts, lease_until=None
            )
            await self.rebuild(account)
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.patch_job,
                job["id"],
                state="queued",
                counts=counts,
                lease_until=None,
            )
            raise
        except Exception as exc:
            await asyncio.to_thread(
                self.patch_job,
                job["id"],
                state="failed",
                counts=counts,
                last_error=type(exc).__name__,
                lease_until=(
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            )

    def start_import(self, account, job):
        key = job["id"]
        if key not in _import_tasks or _import_tasks[key].done():
            task = asyncio.create_task(self.process_import(account, job))
            _import_tasks[key] = task
            task.add_done_callback(
                lambda done: (
                    _import_tasks.pop(key, None)
                    if _import_tasks.get(key) is done
                    else None
                )
            )
