"""Independent membership, shared catalog and private library API."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import AuthError, validate_password
from .catalog import Catalog
from .library import Library, enriched
from .letterboxd_import import (
    MAX_UPLOAD_BYTES,
    InvalidLetterboxdExport,
    parse_letterboxd_export,
)
from .recommender import rank_watchlist
from .llm import rank_candidates

router = APIRouter()


def context():
    from . import main

    settings = main.get_settings()
    return main, main._auth_service(), settings


class Registration(BaseModel):
    username: str
    password: str
    password_confirm: str

    @field_validator("username", mode="before")
    @classmethod
    def username_policy(cls, value):
        from .main import _normalize_username

        return _normalize_username(value)


class Favorites(BaseModel):
    film_ids: list[UUID] = Field(min_length=4, max_length=4)

    @field_validator("film_ids")
    @classmethod
    def distinct(cls, value):
        if len(set(value)) != 4:
            raise ValueError("Dört farklı film seç.")
        return value


class FilmState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    watched: bool | None = None
    watchlist: bool | None = None
    rating: float | None = None

    @field_validator("rating")
    @classmethod
    def rating_policy(cls, value):
        if value is not None and (not 0.5 <= value <= 5 or value * 2 != int(value * 2)):
            raise ValueError("Puan 0.5–5 arasında ve yarım yıldız adımlarında olmalı.")
        return value


class Match(BaseModel):
    target_film_id: UUID


@router.post("/api/auth/register")
async def register(req: Registration, request: Request, response: Response):
    main, service, _settings = context()
    await main._enforce_auth_rate_limit(request)
    try:
        validate_password(req.password, req.password_confirm)
        session = await asyncio.to_thread(
            service.register_native,
            req.username,
            req.password,
            ip_hash=main._ip_hash(request),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except AuthError as exc:
        main._raise_auth_http(exc)
    main._set_session_cookies(response, session)
    return {"ok": True, "logged_in": True, "account": session.account.__dict__}


@router.get("/api/catalog/search")
async def search(request: Request, q: str = "", limit: int = 24):
    main, service, settings = context()
    account = await main._require_account(request)
    if len(q) > 120:
        raise HTTPException(422, "Arama en fazla 120 karakter olabilir.")
    films, unavailable = await Catalog(service, settings).search(
        q.strip(), main._response_locale(account, request)
    )
    return {
        "films": films[: max(1, min(limit, 24))],
        "upstream_unavailable": unavailable,
    }


@router.get("/api/catalog/films/{film_id}")
async def film_detail(film_id: UUID, request: Request):
    main, service, settings = context()
    await main._require_account(request)
    try:
        return {"film": await Catalog(service, settings).details(str(film_id))}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/api/profile/favorites")
async def favorites(req: Favorites, request: Request):
    main, service, settings = context()
    main._require_csrf(request)
    await main._enforce_member_action_limit(request)
    account = await main._require_account(request)
    library = Library(service, settings)
    try:
        films = [
            await library.catalog.details(str(identity)) for identity in req.film_ids
        ]
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    await asyncio.to_thread(library.save_favorites, account, films)
    # Save an immediate deterministic snapshot; the separate analysis call
    # can fail/retry without losing these durable selections.
    return await library.rebuild(account, main._response_locale(account, request))


@router.post("/api/profile/analysis")
async def analysis(request: Request):
    main, service, settings = context()
    main._require_csrf(request)
    await main._enforce_member_action_limit(request)
    await main._enforce_heavy_rate_limit(request)
    account = await main._require_account(request)
    profile = await asyncio.to_thread(service.get_profile, account)
    if len(profile.get("favorite_films") or []) != 4:
        raise HTTPException(409, "Analiz için önce dört favori filmini seç.")
    return await Library(service, settings).rebuild(
        account, main._response_locale(account, request), use_llm=True
    )


@router.get("/api/library")
async def library_read(
    request: Request, scope: str = "all", q: str = "", page: int = 1
):
    main, service, settings = context()
    account = await main._require_account(request)
    library = Library(service, settings)
    rows = await asyncio.to_thread(library.list, account.id)
    if scope in ("watched", "watchlist"):
        rows = [r for r in rows if r.get(scope)]
    if q:
        rows = [r for r in rows if q.casefold() in r["film"]["title"].casefold()]
    page = max(1, page)
    return {
        "films": rows[(page - 1) * 40 : page * 40],
        "total": len(rows),
        "page": page,
    }


@router.put("/api/library/{film_id}")
async def library_update(film_id: UUID, req: FilmState, request: Request):
    main, service, settings = context()
    main._require_csrf(request)
    await main._enforce_member_action_limit(request)
    account = await main._require_account(request)
    library = Library(service, settings)
    try:
        film = await library.catalog.details(str(film_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    row = await asyncio.to_thread(library.update, account.id, film, **req.model_dump())
    with contextlib.suppress(Exception):
        await library.rebuild(account, main._response_locale(account, request))
    return {"ok": True, "entry": row, "film": film}


@router.get("/api/library/archive")
async def archive(request: Request, page: int = 1):
    main, service, settings = context()
    account = await main._require_account(request)
    rows = await asyncio.to_thread(Library(service, settings).archive_rows, account.id)
    entries = [
        {
            "source_key": r["source_key"],
            "film_id": r.get("film_id"),
            **{k: v for k, v in r["data"].items() if not k.startswith("_")},
        }
        for r in rows
        if r["kind"] == "entry"
    ]
    entries.sort(key=lambda r: r.get("watched_on") or "", reverse=True)
    page = max(1, page)
    return {
        "entries": entries[(page - 1) * 30 : page * 30],
        "total": len(entries),
        "page": page,
    }


@router.get("/api/library/imports")
async def imports(request: Request, progress: bool = False):
    main, service, settings = context()
    account = await main._require_account(request)
    library = Library(service, settings)
    jobs = await asyncio.to_thread(library.imports, account.id)
    if progress:
        # The progress screen polls often and needs only the job rows.
        return {"imports": jobs}
    unresolved = await asyncio.to_thread(library.unresolved, account.id)
    return {"imports": jobs, "unresolved": unresolved}


@router.post("/api/library/matches/{film_id}")
async def match(film_id: UUID, req: Match, request: Request):
    main, service, settings = context()
    main._require_csrf(request)
    account = await main._require_account(request)
    library = Library(service, settings)
    try:
        target = await library.catalog.details(str(req.target_film_id))
        await asyncio.to_thread(library.merge, account.id, str(film_id), target)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "film": target}


async def import_export(request: Request):
    main, service, settings = context()
    main._require_csrf(request)
    await main._enforce_member_action_limit(request)
    account = await main._require_account(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "ZIP dosyası 16 MB sınırını aşıyor.")
    try:
        export = await asyncio.to_thread(parse_letterboxd_export, bytes(body))
    except InvalidLetterboxdExport as exc:
        raise HTTPException(400, str(exc)) from exc
    library = Library(service, settings)
    fingerprint = hashlib.sha256(body).hexdigest()
    job = await asyncio.to_thread(library.prepare_import, account, export, fingerprint)
    if job["state"] != "done":
        library.start_import(account, job)
    return {
        "watched_count": len(export.watched),
        "watchlist_count": len(export.watchlist),
        "review_count": sum(r["has_review"] for r in export.entries),
        "entry_count": len(export.entries),
        "favorite_count": len(export.favorites),
        "import": job,
    }


async def native_recommendations(account, request):
    main, service, settings = context()
    library = Library(service, settings)
    rows = await asyncio.to_thread(library.list, account.id)
    watched = [enriched(r["film"], r.get("rating")) for r in rows if r.get("watched")]
    selected = [r["film"] for r in rows if r.get("watchlist") and not r.get("watched")]
    if not selected:
        selected = await asyncio.to_thread(library.catalog.list, "", 300)
        excluded = {r["film_id"] for r in rows if r.get("watched")}
        selected = [f for f in selected if f["id"] not in excluded]
    if not selected:
        selected, _ = await library.catalog.search()
        excluded = {r["film_id"] for r in rows if r.get("watched")}
        selected = [f for f in selected if f["id"] not in excluded]
    profile = await asyncio.to_thread(service.get_profile, account)
    slugs = [f["slug"] for f in profile.get("favorite_films") or []]
    locale = main._response_locale(account, request)
    candidates = await asyncio.to_thread(
        rank_watchlist,
        watched,
        [enriched(f) for f in selected],
        n=12,
        favorite_four_slugs=slugs,
        locale=locale,
    )
    result = await rank_candidates(
        settings, watched, candidates, favorite_four_slugs=slugs, locale=locale
    )
    return {
        **result,
        "username": account.username,
        "watched_count": len(watched),
        "watchlist_count": len(selected),
        "cached": True,
    }


async def native_random_pool(account, source):
    main, service, settings = context()
    if account is None:
        return []
    library = Library(service, settings)
    rows = await asyncio.to_thread(library.list, account.id)
    watched = {r["film_id"] for r in rows if r.get("watched")}
    if source == "watchlist":
        films = [r["film"] for r in rows if r.get("watchlist")]
    else:
        if source == "community":
            community = await main._community_random_pool(service, account)
            if community:
                return community
        films = await asyncio.to_thread(library.catalog.list, "", 300)
        if len(films) < 12:
            popular, _ = await library.catalog.search()
            films = list({f["id"]: f for f in films + popular}.values())
    return [enriched(f) for f in films if f["id"] not in watched]


async def resume_imports():
    """Only durable ZIP metadata jobs; there are no Letterboxd crawl calls."""
    from . import main
    from .auth import Account
    from .sandbox import SandboxAuthService

    while True:
        try:
            service = main._auth_service()
            if not isinstance(service, SandboxAuthService):
                rows = await asyncio.to_thread(
                    lambda: service._service_client()
                    .table("library_imports")
                    .select("*")
                    .in_("state", ["queued", "running", "failed"])
                    .or_("lease_until.is.null,lease_until.lt." + datetime_now())
                    .limit(3)
                    .execute()
                    .data
                    or []
                )
                for job in rows:
                    accounts = await asyncio.to_thread(
                        lambda: service._service_client()
                        .table("users")
                        .select("*")
                        .eq("id", job["user_id"])
                        .execute()
                        .data
                        or []
                    )
                    if accounts:
                        Library(service, main.get_settings()).start_import(
                            service._account(accounts[0]), job
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            main.log.warning("library import retry scan failed", exc_info=True)
        await asyncio.sleep(30)


def datetime_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
