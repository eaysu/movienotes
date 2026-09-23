"""FastAPI app — yeni mimari.

Akış:
  1. İzlenen filmler scrape edilir  (/films/)   → zevk profili kaynağı
  2. Watchlist scrape edilir         (/watchlist/) → aday havuzu
  3. Her iki liste TMDb ile zenginleştirilir
  4. Watchlist filmleri zevk profiline cosine benzerliğine göre sıralanır
  5. LLM en iyi N filmi seçer ve Türkçe gerekçe yazar

Çalıştırmak için:
  uvicorn app.main:app --reload --port 8001 --log-level warning
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import random as _random
import re
import secrets
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from .config import get_settings
from .auth import (
    Account,
    AccountExistsError,
    AuthError,
    AuthService,
    AuthSession,
    BlendServiceError,
    InvalidCredentialsError,
    OwnershipPendingError,
    TransientStorageError,
    VerificationError,
    validate_password,
)
from .cache import Cache, LayeredCache
from .database import delete_user, upsert_user, SupabaseCache
from .enrich import Enricher, EnrichedFilm, close_tmdb_client
from .llm import analyze_taste, rank_candidates
from .recommender import rank_watchlist
from .rate_limit import SlidingWindowRateLimiter
from .sandbox import SandboxAuthService
from .scraper import (
    AccessBlockedError,
    _scrape_watched_rss,
    EmptyListError,
    ScrapedFilm,
    ScrapedProfile,
    ScrapeError,
    scrape_diary,
    scrape_film_rating,
    scrape_reviewed_diary,
    scrape_films,
    scrape_profile,
    scrape_recent_watched,
    scrape_official_list,
    resolve_missing_posters,
    scrape_watchlist,
    scrape_watched,
)
from . import screenings
from .taste_profile import (
    TASTE_PROFILE_VERSION,
    build_taste_profile,
    personality_from_favorites,
    taste_analysis_signal,
    taste_source_fingerprint,
)
from . import profile_sync


# Every handler reaches Supabase through ``asyncio.to_thread``, and the default
# executor is sized for CPU work: ``cpu_count + 4``. On a one-core host that is
# five threads for the entire application, so five members waiting on Supabase
# leave the sixth queued behind them — a ceiling set by the size of the box
# rather than by anything the work needs. These threads are asleep on a socket,
# not computing, so they are sized for round trips in flight.
WORKER_THREADS = int(os.environ.get("WORKER_THREADS", "48"))
_worker_pool: ThreadPoolExecutor | None = None


@contextlib.asynccontextmanager
async def _lifespan(_app):
    global _worker_pool
    schedulers: list[asyncio.Task] = []
    settings = get_settings()
    _worker_pool = ThreadPoolExecutor(
        max_workers=max(8, WORKER_THREADS), thread_name_prefix="movienotes"
    )
    asyncio.get_running_loop().set_default_executor(_worker_pool)
    if getattr(settings, "bulletin_enabled", False) and getattr(settings, "has_tmdb", False):
        schedulers.append(asyncio.create_task(_bulletin_refresh_loop()))
    # Günce taraması hesaplardan bağımsız çalışamaz: üye listesi Supabase'de.
    if getattr(settings, "diary_scan_enabled", True) and getattr(settings, "has_auth", False):
        schedulers.append(asyncio.create_task(_diary_refresh_loop()))
    # Full profile imports are durable jobs.  The worker picks them up even if
    # the member closes the PWA while Letterboxd is temporarily rate-limiting
    # us, rather than requiring another visit to resume the crawl.
    if getattr(settings, "has_auth", False):
        schedulers.append(asyncio.create_task(_profile_sync_retry_loop()))
    try:
        yield
    finally:
        for scheduler in schedulers:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
        await close_tmdb_client()


app = FastAPI(title="Movienotes", version="0.4.0", lifespan=_lifespan)
log = logging.getLogger("moviebox")

_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    allow_credentials="*" not in _allowed_origins,
)

# Depo kökünden çözülüyor: backend/app/main.py → ../../frontend. Çalışma
# dizinine bağlı olmadığı için uvicorn nereden başlatılırsa başlatılsın bulur.
# URL öneki /static olarak kalıyor; değiştirmek her sürümlenmiş varlığın
# adresini bozar ve bir yıllık immutable önbelleği ıskartaya çıkarır.
STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend"


class VersionedStaticFiles(StaticFiles):
    """Cache versioned assets forever, but keep bare URLs safely revalidatable."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        query = scope.get("query_string", b"")
        versioned = any(part.startswith(b"v=") for part in query.split(b"&"))
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if versioned
            else "public, max-age=3600, must-revalidate"
        )
        return response


app.mount(
    "/static",
    VersionedStaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

# ── Concurrency queue ──────────────────────────────────────────────────────
_sem = asyncio.Semaphore(4)   # max 4 eşzamanlı analiz
_q_lock = asyncio.Lock()
_q_waiting = 0   # sırada bekleyenler
_q_active  = 0   # şu an işlenenler

_heavy_rate_limiter = SlidingWindowRateLimiter(
    limit=5,
    window_seconds=10 * 60,
    burst=2,
    burst_seconds=15,
)
_delete_rate_limiter = SlidingWindowRateLimiter(
    limit=3,
    window_seconds=60 * 60,
    burst=1,
    burst_seconds=15,
)
# Random picks are meant to be spun until something clicks, and they cost one
# indexed query instead of a scrape, so the ceiling only exists to stop scripts.
# A card lands in about a second, so someone flipping through taps about that
# often; a burst of twelve per fifteen seconds refused one spin in five of an
# ordinary sitting. The ten-minute ceiling is what actually bounds the cost.
_random_rate_limiter = SlidingWindowRateLimiter(
    limit=180,
    window_seconds=10 * 60,
    burst=20,
    burst_seconds=15,
)

# Public social-proof stats are intentionally tiny and cached.  The hero is
# reachable without a session, so querying the users table on every page load
# would turn a harmless UI flourish into avoidable Supabase traffic.
_PUBLIC_STATS_TTL_SECONDS = 5 * 60
_public_stats_lock = asyncio.Lock()
_public_stats_cache = {"checked_at": 0.0, "registered_users": 0}


def _count_registered_users(settings) -> int:
    """Return the number of active accounts without exposing user records."""
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_key)
    result = (
        client.table("users")
        .select("id", count="exact", head=True)
        .eq("account_status", "active")
        .execute()
    )
    return max(0, int(getattr(result, "count", 0) or 0))


# Signing up costs requests, and a first-timer spends more of them than anyone:
# one to start, then a verify for every time they come back before the bio edit
# has saved on Letterboxd's side. Charging all of that against a small quota
# stranded people mid-registration — password chosen, code issued, and nothing
# to do but wait. So the ceiling on *attempts* is only here to stop a flood,
# while the tight quota is spent by *failures* alone, which is what brute force
# is made of. An honest signup, however clumsy, never pays it.
_auth_rate_limiter = SlidingWindowRateLimiter(
    limit=40,
    window_seconds=15 * 60,
    burst=12,
    burst_seconds=30,
)
_auth_failure_limiter = SlidingWindowRateLimiter(
    limit=8,
    window_seconds=15 * 60,
    burst=5,
    burst_seconds=30,
)
# Letters, Blend invites, blocks and reports used to draw on the signup quota,
# so five letters could leave a member unable to block anyone — and, being keyed
# by address, one person on a shared connection spent everyone else's budget.
# These are ordinary member actions: bounded per session, generously.
_member_action_limiter = SlidingWindowRateLimiter(
    limit=30,
    window_seconds=10 * 60,
    burst=8,
    burst_seconds=30,
)
_readiness_lock = asyncio.Lock()
_readiness_cache = {"checked_at": 0.0, "ready": False}

ACCESS_COOKIE = "mb_access"
REFRESH_COOKIE = "mb_refresh"
CSRF_COOKIE = "mb_csrf"
# Yenileme çerezi httponly olduğu için tercihi ayrı tutuyoruz: token her
# yenilendiğinde ömrünü yeniden aynı seçime göre kurmak gerekiyor.
REMEMBER_COOKIE = "mb_remember"

SHARE_IMAGE_ALLOWED_HOSTS = frozenset({
    "image.tmdb.org",
    "a.ltrbxd.com",
    "s.ltrbxd.com",
    "letterboxd.com",
    "www.letterboxd.com",
})
SHARE_IMAGE_MEDIA_TYPES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/avif",
})
SHARE_IMAGE_MAX_BYTES = 8 * 1024 * 1024


def _client_ip(request: Request) -> str:
    """Resolve the client IP behind Render/Cloudflare without trusting left XFF."""
    cf_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # Cloudflare appends the actual visitor address; a caller can spoof the
        # left side of the chain, so use the rightmost non-empty address.
        addresses = [part.strip() for part in forwarded.split(",") if part.strip()]
        if addresses:
            return addresses[-1]
    return request.client.host if request.client else "unknown"


async def _enforce_heavy_rate_limit(request: Request) -> None:
    allowed, retry_after = await _heavy_rate_limiter.check(_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Çok fazla analiz isteği gönderildi. Lütfen biraz sonra tekrar dene.",
            headers={"Retry-After": str(retry_after)},
        )


async def _enforce_random_rate_limit(request: Request) -> None:
    allowed, retry_after = await _random_rate_limiter.check(_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Çok hızlı çeviriyorsun; birkaç saniye sonra tekrar dene.",
            headers={"Retry-After": str(retry_after)},
        )


async def _enforce_delete_rate_limit(request: Request) -> None:
    allowed, retry_after = await _delete_rate_limiter.check(_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Çok fazla veri silme isteği gönderildi. Lütfen daha sonra tekrar dene.",
            headers={"Retry-After": str(retry_after)},
        )


def _session_key(request: Request) -> str:
    """Identify the caller by their session when they have one, else by address.

    Carrier NAT and campus Wi-Fi put strangers behind a single address, so an
    address-keyed quota lets one busy member spend everybody else's. A signed-in
    caller carries their own credential; anonymous traffic has nothing but the
    address, which is also where brute force has to be counted.
    """
    token = request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE)
    if token:
        return "s:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    return "ip:" + _client_ip(request)


def _raise_rate_limited(detail: str, retry_after: int) -> None:
    raise HTTPException(
        status_code=429, detail=detail, headers={"Retry-After": str(retry_after)}
    )


_AUTH_LIMIT_DETAIL = "Çok fazla hesap isteği gönderildi. Lütfen biraz sonra tekrar dene."


async def _enforce_auth_rate_limit(request: Request) -> None:
    """Allow an honest signup to be clumsy; stop a caller who keeps failing."""
    key = _client_ip(request)
    has_room, retry_after = await _auth_failure_limiter.peek(key)
    if not has_room:
        _raise_rate_limited(
            "Çok fazla başarısız deneme yapıldı. Lütfen biraz sonra tekrar dene.",
            retry_after,
        )
    allowed, retry_after = await _auth_rate_limiter.check(key)
    if not allowed:
        _raise_rate_limited(_AUTH_LIMIT_DETAIL, retry_after)


async def _record_auth_failure(request: Request) -> None:
    """Charge one failed credential or ownership attempt to the strict quota."""
    await _auth_failure_limiter.check(_client_ip(request))


async def _enforce_member_action_limit(request: Request) -> None:
    allowed, retry_after = await _member_action_limiter.check(_session_key(request))
    if not allowed:
        _raise_rate_limited(
            "Çok hızlı ilerliyorsun; birkaç dakika sonra tekrar dene.", retry_after
        )


_auth_service_instance: AuthService | None = None
_auth_service_settings_id: int | None = None
_auth_service_lock = threading.Lock()


def _auth_service():
    global _auth_service_instance, _auth_service_settings_id
    settings = get_settings()
    if not getattr(settings, "has_auth", False):
        raise HTTPException(status_code=503, detail="Hesap sistemi yapılandırılmamış.")
    settings_id = id(settings)
    if _auth_service_instance is not None and _auth_service_settings_id == settings_id:
        return _auth_service_instance
    with _auth_service_lock:
        if _auth_service_instance is None or _auth_service_settings_id != settings_id:
            # The sandbox swaps the whole storage layer for dictionaries. Doing
            # it here means every route, the sync pipeline and the recommender
            # run their real code against a store that dies with the process.
            _auth_service_instance = (
                SandboxAuthService()
                if getattr(settings, "sandbox_mode", False)
                else AuthService(settings)
            )
            _auth_service_settings_id = settings_id
    return _auth_service_instance


# A profile page fans out into several protected API calls. Validating the same
# Supabase access token remotely for every call multiplied latency and upstream
# traffic. Cache only the token digest (never the raw credential) for a very short
# period; logout/account deletion explicitly invalidate it.
ACCOUNT_CACHE_TTL_SECONDS = 30.0
ACCOUNT_CACHE_MAX_ENTRIES = 2048
_account_cache_lock = threading.Lock()
_account_cache: dict[tuple[int, str], tuple[float, Account]] = {}


def _account_cache_digest(access_token: str) -> str:
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def _cached_account(service, access_token: str) -> Account | None:
    now = time.monotonic()
    key = (id(service), _account_cache_digest(access_token))
    with _account_cache_lock:
        entry = _account_cache.get(key)
        if entry is None:
            return None
        expires_at, account = entry
        if expires_at <= now:
            _account_cache.pop(key, None)
            return None
        return account


def _cache_account(service, access_token: str, account: Account) -> None:
    now = time.monotonic()
    key = (id(service), _account_cache_digest(access_token))
    with _account_cache_lock:
        expired = [cache_key for cache_key, value in _account_cache.items() if value[0] <= now]
        for cache_key in expired:
            _account_cache.pop(cache_key, None)
        if len(_account_cache) >= ACCOUNT_CACHE_MAX_ENTRIES and key not in _account_cache:
            oldest = min(_account_cache, key=lambda cache_key: _account_cache[cache_key][0])
            _account_cache.pop(oldest, None)
        _account_cache[key] = (now + ACCOUNT_CACHE_TTL_SECONDS, account)


def _invalidate_account_cache(access_token: str) -> None:
    if not access_token:
        return
    digest = _account_cache_digest(access_token)
    with _account_cache_lock:
        for key in [key for key in _account_cache if key[1] == digest]:
            _account_cache.pop(key, None)


def _ip_hash(request: Request) -> str:
    settings = get_settings()
    return hashlib.sha256(
        f"{settings.auth_identity_secret}:{_client_ip(request)}".encode("utf-8")
    ).hexdigest()


# Scheduled telemetry writes, held so the garbage collector cannot drop a task
# that is still in flight.
_activity_tasks: set[asyncio.Task] = set()


async def _record_activity_event(
    service: AuthService | None,
    account: Account | None,
    event_type: str,
    metadata: dict | None = None,
) -> None:
    """Best-effort product telemetry, kept off the response path.

    Twenty-five endpoints record an event, and awaiting the write put a whole
    Supabase round trip between the member and their page — measurably, opening
    the Sinefil directory cost as much in analytics as it did in content. No
    response depends on the result, so the write is scheduled and left to
    finish on its own.
    """
    if service is None or account is None:
        return

    async def write() -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                service.record_activity_event,
                account.id,
                event_type,
                metadata or {},
            )

    task = asyncio.create_task(write())
    _activity_tasks.add(task)
    task.add_done_callback(_activity_tasks.discard)


def _set_session_cookies(response: Response, session, *, remember: bool = True) -> str:
    """Oturum çerezlerini yazar.

    "Beni hatırla" işaretliyse oturum kullanıcı çıkış yapana kadar durur;
    işaretlenmezse bir gün sonra kendiliğinden düşer. Seçim ayrı bir çerezde
    saklanır, çünkü yenileme uçları oturumu tazelerken aynı ömrü kurmalı.
    """
    settings = get_settings()
    session_age = (
        settings.auth_session_max_age if remember
        else settings.auth_session_short_max_age
    )
    access_age = max(60, min(int(session.expires_in), session_age))
    shared = {
        "secure": settings.auth_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(
        ACCESS_COOKIE,
        session.access_token,
        max_age=access_age,
        httponly=True,
        **shared,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        max_age=session_age,
        httponly=True,
        **shared,
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=session_age,
        httponly=False,
        **shared,
    )
    response.set_cookie(
        REMEMBER_COOKIE,
        "1" if remember else "0",
        max_age=session_age,
        httponly=True,
        **shared,
    )
    # Authentication is personal state, never an HTTP-cache candidate. This
    # matters most in standalone PWAs which resume an old page after a token
    # rotation while the browser is offline/backgrounded.
    response.headers["Cache-Control"] = "no-store, private"
    return csrf_token


def _remembered(request: Request) -> bool:
    """Yenilerken ilk girişteki kalıcılık seçimini izler.

    ``mb_remember`` daha sonra eklendi. Eski, hâlâ geçerli yenileme
    çerezleri bu işareti taşımaz; onları kısa oturuma indirgemek bir iOS/PWA
    kullanıcısının cihazının her açılışta unutulmasına yol açıyordu. Açıkça
    "0" seçilmedikçe cihazı hatırla; çıkış işlemi dört çerezi de siler.
    """
    return request.cookies.get(REMEMBER_COOKIE, "") != "0"


def _clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    for key in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE, REMEMBER_COOKIE):
        response.delete_cookie(
            key,
            path="/",
            secure=settings.auth_cookie_secure,
            samesite="lax",
        )


def _require_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="Güvenlik doğrulaması başarısız.")


async def _require_account(request: Request) -> Account:
    access_token = request.cookies.get(ACCESS_COOKIE, "")
    if not access_token:
        raise HTTPException(status_code=401, detail="Oturum açman gerekiyor.")
    service = _auth_service()
    cached = _cached_account(service, access_token)
    if cached is not None:
        return cached
    try:
        account = await asyncio.to_thread(service.current_account, access_token)
    except TransientStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail="Oturum bağlantısı kısa süreli yanıt vermedi. Lütfen tekrar dene.",
        ) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail="Oturum geçersiz.") from exc
    _cache_account(service, access_token, account)
    return account


async def _restore_account_from_device(request: Request, response: Response) -> Account:
    """Recover a returning browser/PWA from its httpOnly refresh cookie.

    An access token normally lasts about an hour. A standalone mobile web app
    is often resumed only after that, and relying on a readable CSRF cookie to
    start recovery made a valid remembered device look signed out. This helper
    is deliberately used only by the read-only bootstrap endpoint below. The
    refresh credential is httpOnly, Secure and SameSite=Lax; a cross-site
    fetch is rejected before it can rotate the credential.
    """
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(status_code=401, detail="Oturum açman gerekiyor.")

    refresh_token = request.cookies.get(REFRESH_COOKIE, "")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Oturum açman gerekiyor.")
    try:
        session = await asyncio.to_thread(_auth_service().refresh, refresh_token)
    except TransientStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail="Oturum bağlantısı kısa süreli yanıt vermedi. Lütfen tekrar dene.",
        ) from exc
    except AuthError as exc:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Oturum geçersiz.") from exc

    if session is None:
        # A refresh that declines by answering nothing is still a decline. It
        # used to reach the cookie writer and fail there, so a stale cookie
        # produced a 500 on the bootstrap instead of a plain "sign in again".
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Oturum geçersiz.")

    _set_session_cookies(response, session, remember=_remembered(request))
    _cache_account(_auth_service(), session.access_token, session.account)
    return session.account


def _validated_share_image_url(value: str) -> str:
    """Allow only known public poster CDNs; never act as a generic proxy."""
    try:
        parsed = urlsplit(str(value or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or host not in SHARE_IMAGE_ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Geçersiz poster adresi.") from exc
    return parsed.geturl()


async def _fetch_share_image(value: str) -> tuple[bytes, str]:
    current = _validated_share_image_url(value)
    headers = {
        "Accept": "image/jpeg,image/png,image/webp;q=0.8,*/*;q=0.1",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://letterboxd.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            for _hop in range(4):
                upstream = await client.get(current, headers=headers)
                if upstream.status_code in {301, 302, 303, 307, 308}:
                    location = upstream.headers.get("location", "")
                    if not location:
                        break
                    current = _validated_share_image_url(urljoin(current, location))
                    continue
                if upstream.status_code != 200:
                    raise HTTPException(status_code=502, detail="Poster alınamadı.")
                media_type = upstream.headers.get("content-type", "").split(";", 1)[0].lower()
                content = upstream.content
                if media_type not in SHARE_IMAGE_MEDIA_TYPES:
                    raise HTTPException(status_code=415, detail="Desteklenmeyen poster biçimi.")
                if not content or len(content) > SHARE_IMAGE_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="Poster dosyası çok büyük.")
                return content, media_type
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Poster kaynağına ulaşılamadı.") from exc
    raise HTTPException(status_code=502, detail="Poster yönlendirmesi tamamlanamadı.")


async def _enforce_account_username(request: Request, username: str) -> Account | None:
    """Protect legacy username payloads once account mode is enabled."""
    if not getattr(get_settings(), "has_auth", False):
        return None
    _require_csrf(request)
    account = await _require_account(request)
    if account.username != username:
        raise HTTPException(status_code=403, detail="Yalnızca kendi profilini kullanabilirsin.")
    return account


def _raise_auth_http(exc: Exception) -> None:
    if isinstance(exc, TransientStorageError):
        raise HTTPException(
            status_code=503,
            detail="Veri bağlantısı kısa süreli yanıt vermedi. Lütfen tekrar dene.",
        ) from exc
    if isinstance(exc, AccountExistsError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, InvalidCredentialsError):
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya parola hatalı.") from exc
    if isinstance(exc, VerificationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, AuthError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


def _raise_blend_http(exc: BlendServiceError) -> None:
    code = str(exc)
    errors = {
        "recipient_not_found": (404, "Kayıtlı Movienotes kullanıcısı bulunamadı."),
        "self_request": (422, "Kendine Blend isteği gönderemezsin."),
        "blend_request_exists": (409, "Bu iki kullanıcı arasında bekleyen bir istek var."),
        "blend_already_accepted": (409, "Bu kullanıcıyla zaten tamamlanmış bir Blend'in var."),
        "pending_quota_reached": (429, "Bekleyen Blend isteği kotasına ulaştın."),
        "blend_user_blocked": (403, "Bu kullanıcıyla Blend isteği oluşturulamaz."),
        "request_not_found": (404, "Blend isteği bulunamadı."),
        "forbidden": (403, "Bu Blend isteği için yetkin yok."),
        "request_already_decided": (409, "Bu Blend isteği daha önce sonuçlandırılmış."),
        "request_not_cancellable": (409, "Bu Blend isteği artık iptal edilemez."),
        "accepted_request_not_found": (409, "Kabul edilmiş Blend isteği bulunamadı."),
        "blend_result_save_failed": (503, "Blend sonucu kaydedilemedi."),
        "blend_delete_failed": (503, "Blend silinemedi. Lütfen tekrar dene."),
        "user_not_found": (404, "Kayıtlı Movienotes kullanıcısı bulunamadı."),
        "self_block": (422, "Kendini engelleyemezsin."),
        "self_report": (422, "Kendini bildiremezsin."),
        "invalid_report_category": (422, "Geçersiz bildirim kategorisi."),
        "report_quota_reached": (429, "Günlük bildirim kotasına ulaştın."),
        "block_failed": (400, "Kullanıcı engellenemedi."),
        "unblock_failed": (400, "Engel kaldırılamadı."),
        "report_failed": (400, "Bildirim gönderilemedi."),
        "letter_sender_closed": (409, "Mektup yollayabilmek için kendi mektup kutunu da açman gerekiyor."),
        "letter_recipient_unavailable": (403, "Bu kullanıcı şu anda mektup almaya açık değil."),
        "letter_send_cooldown": (429, "Bu sinefile bugün zaten bir mektup yazdın. Ona yeniden yazmak için 24 saat beklemelisin."),
        "letter_blocked": (403, "Bu kullanıcıyla mektuplaşamazsın."),
        "invalid_letter_body": (422, "Mektup 1–600 karakter arasında olmalı."),
        "letter_send_failed": (503, "Mektup şu an gönderilemedi. Lütfen tekrar dene."),
        "letter_not_found": (404, "Bu gönderilmiş mektup bulunamadı."),
    }
    status_code, detail = errors.get(code, (400, "Blend işlemi tamamlanamadı."))
    raise HTTPException(
        status_code=status_code, detail=detail, headers={"X-Error-Code": code}
    ) from exc


def _raise_scrape_http(exc: ScrapeError) -> None:
    if isinstance(exc, AccessBlockedError):
        raise HTTPException(
            status_code=503,
            detail=(
                "Letterboxd erişimi geçici olarak sınırladı. Otomatik yeniden "
                "denemeler tamamlandı; lütfen yaklaşık bir dakika sonra tekrar dene."
            ),
            headers={"Retry-After": "60"},
        ) from exc
    raise HTTPException(status_code=exc.status or 503, detail=str(exc)) from exc


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _scrape_error_event(exc: ScrapeError) -> str:
    return _sse({"type": "error", "code": exc.code, "detail": str(exc)})


async def _await_with_heartbeat(coro, holder: dict, *, interval: float = 5.0, max_total: float = 120.0):
    """`coro`'yu çalıştırırken her `interval` saniyede bir 'ping' SSE üretir.

    Humanize edilmiş scraping uzun sürebildiğinden (10-40s), bu süre boyunca
    bağlantıyı canlı tutmak için periyodik ping gönderir — aksi halde araya
    giren proxy'ler idle bağlantıyı kesebilir. Sonuç holder['result']'a,
    istisna holder['error']'a yazılır.

    `max_total` saniyeyi aşarsa görev iptal edilir ve holder['error'] bir
    ScrapeError ile doldurulur — Cloudflare'ın olasılıksal engellemesi retry
    bütçesini beklenenden çok aştığında istek sonsuza kadar asılı kalmasın.
    """
    task = asyncio.ensure_future(coro)
    elapsed = 0.0
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            elapsed += interval
            if elapsed >= max_total:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                holder["error"] = ScrapeError(
                    f"Letterboxd {max_total:.0f} saniye içinde yanıt vermedi — "
                    "sunucu IP'si geçici olarak engellenmiş olabilir. Birkaç dakika sonra tekrar dene."
                )
                return
            yield _sse({"type": "ping"})
        except BaseException:
            break  # task hatası aşağıda exception() ile ele alınır
    exc = task.exception()
    if exc is not None:
        holder["error"] = exc
    else:
        holder["result"] = task.result()


async def _capacity_stream(stream):
    """Run an SSE iterator under the shared endpoint concurrency budget."""
    global _q_waiting, _q_active

    acquired = False
    waiting = True
    active = False
    async with _q_lock:
        _q_waiting += 1
        ahead = _q_active + _q_waiting - 1

    try:
        if ahead > 0:
            yield _sse({"type": "queued", "ahead": ahead})

        await _sem.acquire()
        acquired = True

        async with _q_lock:
            _q_waiting -= 1
            waiting = False
            _q_active += 1
            active = True

        async for event in stream:
            yield event
    finally:
        if acquired:
            _sem.release()
        if waiting or active:
            async with _q_lock:
                if waiting:
                    _q_waiting -= 1
                if active:
                    _q_active -= 1
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await stream.aclose()


def _make_cache(settings):
    sqlite_cache = Cache(settings.cache_db_path)
    if settings.has_supabase:
        from supabase import create_client
        client = create_client(settings.supabase_url, settings.supabase_key)
        return client, LayeredCache(sqlite_cache, SupabaseCache(client))
    return None, sqlite_cache


# ── Kullanıcı profili kalıcı cache'i ─────────────────────────────────────────
# Çekilip zenginleştirilmiş film listeleri kullanıcı başına Supabase'e kaydedilir.
# Sonraki oturumlar scrape + TMDb adımlarını tamamen atlar → ~100s yerine ~5s.
# Supabase yoksa SQLite'a yazılır (lokal kalıcı, Render'da redeploy'da silinir).
TTL_USER_FILMS = 24 * 3600  # 1 gün
TTL_FULL_SCRAPE = 7 * 24 * 3600  # derindeki silme/değişiklikler için haftalık tam crawl
WATCHLIST_HEAD_CHECK_MIN_INTERVAL = 30 * 60  # oturumlar arasında 30 dk
# Installed apps can be opened many times a day. Each entry asks for a sync,
# but the durable gate keeps the actual Letterboxd work bounded per account.
ENTRY_SYNC_MIN_INTERVAL = 15 * 60
FINGERPRINT_FILM_LIMIT = 28
TTL_RECOMMENDATION = 30 * 24 * 3600
RECOMMENDER_VERSION = "v5-last100-fav4-directors"
BLEND_VERSION = "blend-v9-director-photo"


def _make_persistent_cache(settings, client):
    """Kullanıcı film profilleri için kalıcı cache (Supabase tercih edilir)."""
    if client is not None:
        return SupabaseCache(client)
    return Cache(settings.cache_db_path)


def _enriched_from_cache(rows: list[dict]) -> list[EnrichedFilm]:
    """Cache JSON'undan EnrichedFilm listesi kur (text_blob hesaplanan alan, atılır)."""
    out: list[EnrichedFilm] = []
    for d in rows:
        d = {k: v for k, v in d.items() if k != "text_blob"}
        if "details_loaded" not in d:
            d["details_loaded"] = bool(d.get("director") or d.get("keywords"))
        out.append(EnrichedFilm(**d))
    return out


def _enriched_from_watched_rows(
    rows: list[dict], limit: int | None = None
) -> list[EnrichedFilm]:
    """Build a recent-first Blend profile from durable per-user rows."""
    ordered = sorted(
        rows,
        key=lambda row: row.get("watched_rank")
        if row.get("watched_rank") is not None
        else 10**9,
    )
    if limit is not None:
        ordered = ordered[:limit]
    return [
        EnrichedFilm(
            title=row.get("title") or "",
            year=row.get("release_year"),
            slug=row.get("film_slug") or "",
            tmdb_id=row.get("tmdb_id"),
            genres=row.get("genres") or [],
            director=row.get("director") or "",
            keywords=row.get("keywords") or [],
            poster_url=row.get("poster_url") or None,
            matched=bool(row.get("tmdb_id")),
            details_loaded=bool(row.get("details_loaded")),
            user_rating=row.get("user_rating"),
        )
        for row in ordered
        if row.get("film_slug")
    ]


def _detail_sample(films: list[EnrichedFilm], limit: int) -> list[EnrichedFilm]:
    """Prefer explicitly high-rated films, then preserve recent profile order."""
    rated = sorted(
        (film for film in films if film.user_rating is not None),
        key=lambda film: film.user_rating or 0.0,
        reverse=True,
    )
    rated_ids = {id(film) for film in rated}
    return (rated + [film for film in films if id(film) not in rated_ids])[:limit]


def _recommendation_namespace(username: str) -> str:
    """Keep recommendation rows deletable without weakening content-addressed keys."""
    return f"recommendations:{username}"


def _delete_cached_user_data(cache, username: str) -> bool:
    """Delete every username-addressable cache row from one backend."""
    operations = [
        cache.delete("films_watched", username),
        cache.delete("films_watchlist", username),
        cache.delete("films_full_refresh", f"watched:{username}"),
        cache.delete("films_full_refresh", f"watchlist:{username}"),
        cache.delete("watchlist_head_check", username),
        cache.clear(_recommendation_namespace(username)),
        # Pre-v3 recommendation rows were anonymous hashes and cannot be mapped
        # to a username. The obsolete namespace is no longer read.
        cache.clear("recommendations"),
    ]
    return all(result is not False for result in operations)


def _recommendation_cache_key(
    username: str,
    watched: list[EnrichedFilm],
    watchlist: list[EnrichedFilm],
    *,
    model: str,
    count: int,
    favorite_directors: list[str] | None = None,
    favorite_four_slugs: list[str] | None = None,
    locale: str = "tr",
) -> str:
    """Content-address a recommendation so profile changes invalidate it."""
    payload = {
        "version": RECOMMENDER_VERSION,
        "username": username,
        "model": model,
        "count": count,
        "watched": [(film.slug, film.user_rating) for film in watched],
        "watchlist": [film.slug for film in watchlist],
        "favorite_directors": (favorite_directors or [])[:3],
        "favorite_four_slugs": (favorite_four_slugs or [])[:4],
        "locale": locale,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest


_letterboxd_rating_cache: dict[str, tuple[float, float | None]] = {}
_LETTERBOXD_RATING_TTL = 24 * 60 * 60


async def _attach_letterboxd_ratings(rows: list[dict]) -> None:
    """Fill each row's ``letterboxd_rating`` — the community average out of 5.

    TMDb's ``vote_average`` is a different crowd on a ten-point scale, so it is
    not what a Letterboxd member means by "the rating". One page per film, kept
    for a day in-process, and every failure is silent: a missing average simply
    does not render.
    """
    targets = {
        str(row.get("slug") or ""): row
        for row in rows
        if row.get("slug") and not row.get("letterboxd_rating")
    }
    targets.pop("", None)
    if not targets:
        return
    now = time.time()

    async def fill(slug: str, row: dict) -> None:
        hit = _letterboxd_rating_cache.get(slug)
        if hit and now - hit[0] < _LETTERBOXD_RATING_TTL:
            row["letterboxd_rating"] = hit[1]
            return
        rating = None
        with contextlib.suppress(Exception):
            rating = await scrape_film_rating(slug)
        _letterboxd_rating_cache[slug] = (now, rating)
        row["letterboxd_rating"] = rating

    await asyncio.gather(*(fill(slug, row) for slug, row in targets.items()))


def _response_locale(account: Account | None, request: Request) -> str:
    """Resolve account preference, else the language the shell is rendering in.

    The shell's own choice is stored per device and an account may sit at
    "auto" forever, so ``Accept-Language`` is not a stand-in for it: a browser
    that asks for English first while the app renders Turkish used to get
    English recommendation reasons. The client states its resolved locale.
    """
    preferred = getattr(account, "preferred_locale", "auto")
    if preferred in {"tr", "en"}:
        return preferred
    client = request.headers.get("x-movienotes-locale", "").strip().lower()
    if client in {"tr", "en"}:
        return client
    return "en" if request.headers.get("accept-language", "").lower().startswith("en") else "tr"


_film_load_flights: dict[tuple[str, str], asyncio.Task] = {}
_film_load_lock = asyncio.Lock()


async def _scrape_enrich_and_cache(
    username: str,
    list_type: str,
    *,
    enricher,
    pcache,
    scrape_kwargs: dict,
    cached_rows: list[dict] | None = None,
    allow_head_check: bool = False,
):
    """Cache miss sonrası pahalı scrape + enrichment işini bir kez çalıştır."""
    ns = f"films_{list_type}"

    if cached_rows and allow_head_check:
        expected = [row.get("slug", "") for row in cached_rows[:FINGERPRINT_FILM_LIMIT]]
        if expected and all(expected):
            try:
                if list_type == "watched":
                    head, _ = await scrape_diary(
                        username,
                        max_pages=1,
                        film_limit=FINGERPRINT_FILM_LIMIT,
                        max_retries=scrape_kwargs.get("max_retries", 3),
                    )
                else:
                    head, _ = await scrape_watchlist(
                        username,
                        delay=0,
                        max_pages=1,
                        film_limit=FINGERPRINT_FILM_LIMIT,
                        max_retries=scrape_kwargs.get("max_retries", 3),
                    )
                actual = [film.slug for film in head]
                if actual == expected[:len(actual)] and len(actual) == len(expected):
                    await asyncio.to_thread(pcache.touch, ns, username)
                    log.warning(
                        "fingerprint HIT %s/%s (%d head slugs); full crawl skipped",
                        list_type,
                        username,
                        len(actual),
                    )
                    return _enriched_from_cache(cached_rows), True
            except ScrapeError as exc:
                log.warning("fingerprint check failed %s/%s: %s", list_type, username, exc)

    scrape_fn = scrape_watched if list_type == "watched" else scrape_watchlist
    scraped, complete = await scrape_fn(username, **scrape_kwargs)
    if not scraped:
        return [], False

    if enricher is not None:
        # Profile cache stores cheap search metadata. Director/keywords are fetched
        # later only for the small subset that affects ranking or Blend.
        films = await enricher.enrich(scraped, include_details=False)
        final_misses = [
            source
            for source, film in zip(scraped, films)
            if not film.poster_url and source.poster_resolver_url
        ]
        if final_misses:
            await resolve_missing_posters(final_misses)
            repaired = []
            for source, film in zip(scraped, films):
                if not film.poster_url and source.poster_url:
                    film.poster_url = source.poster_url
                    repaired.append(film)
            if repaired:
                await enricher.save_film_assets(repaired)
    else:
        await resolve_missing_posters(scraped)
        films = [
            EnrichedFilm(title=f.title, year=f.year, slug=f.slug, poster_url=f.poster_url)
            for f in scraped
        ]

    # Yalnızca temiz biten taramaları cache'le (eksik profil 24s yapışmasın).
    if pcache is not None and films and complete:
        await asyncio.to_thread(pcache.set, ns, username, [f.to_dict() for f in films])
        await asyncio.to_thread(
            pcache.set,
            "films_full_refresh",
            f"{list_type}:{username}",
            {"complete": True},
        )
        log.warning("cache SET  %s/%s (%d films)", list_type, username, len(films))
    elif not complete:
        log.warning("cache SKIP %s/%s — scrape incomplete (blocked)", list_type, username)

    return films, False


async def _get_or_create_film_flight(
    username: str,
    list_type: str,
    *,
    enricher,
    pcache,
    scrape_kwargs: dict,
    cached_rows: list[dict] | None = None,
    allow_head_check: bool = False,
) -> tuple[asyncio.Task, bool]:
    """Return the shared refresh task and whether this caller joined it."""
    flight_key = (username, list_type)
    async with _film_load_lock:
        task = _film_load_flights.get(flight_key)
        if task is not None:
            return task, True

        task = asyncio.create_task(
            _scrape_enrich_and_cache(
                username,
                list_type,
                enricher=enricher,
                pcache=pcache,
                scrape_kwargs=scrape_kwargs,
                cached_rows=cached_rows,
                allow_head_check=allow_head_check,
            )
        )
        _film_load_flights[flight_key] = task

        def _clear_finished(done_task, key=flight_key):
            if _film_load_flights.get(key) is done_task:
                _film_load_flights.pop(key, None)
            # Background stale refresh failures must be observed, but never replace
            # or invalidate the last known-good cache entry.
            with contextlib.suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc is not None:
                    log.warning("background refresh FAILED %s/%s: %s", key[1], key[0], exc)

        task.add_done_callback(_clear_finished)
        return task, False


async def _load_user_films(
    username: str,
    list_type: str,
    *,
    settings,
    enricher,
    pcache,
    scrape_kwargs: dict,
    force: bool = False,
):
    """Bir kullanıcının bir listesi için zenginleştirilmiş filmleri döndür.

    Sıra: kalıcı cache (Supabase) → yoksa scrape + TMDb enrich → cache'e yaz.
    Sadece TEMIZ biten (complete=True) scrape'ler cache'lenir; bloklu/eksik
    taramalar 24 saat boyunca kötü veri servis etmesin diye yazılmaz.

    force=True → cache okumasını atlar, taze çekip üzerine yazar (cache warmer).
    list_type: 'watched' | 'watchlist'.
    Döner: (list[EnrichedFilm], from_cache: bool).
    """
    ns = f"films_{list_type}"
    if pcache is not None and not force:
        entry = await asyncio.to_thread(
            pcache.get_with_freshness, ns, username, ttl=TTL_USER_FILMS
        )
        if entry is not None:
            cached, fresh = entry
            if fresh:
                log.warning("cache HIT  %s/%s (%d films)", list_type, username, len(cached))
                return _enriched_from_cache(cached), True

            # Stale-while-revalidate: kullanıcı sağlam eski sonucu hemen alır.
            # Yenileme başarısız/eksik olursa _scrape_enrich_and_cache cache'e yazmaz.
            full_entry = await asyncio.to_thread(
                pcache.get_with_freshness,
                "films_full_refresh",
                f"{list_type}:{username}",
                ttl=TTL_FULL_SCRAPE,
            )
            _task, joined = await _get_or_create_film_flight(
                username,
                list_type,
                enricher=enricher,
                pcache=pcache,
                scrape_kwargs=scrape_kwargs,
                cached_rows=cached,
                allow_head_check=bool(full_entry and full_entry[1]),
            )
            log.warning(
                "cache STALE %s/%s (%d films; refresh=%s)",
                list_type,
                username,
                len(cached),
                "joined" if joined else "started",
            )
            return _enriched_from_cache(cached), True

    # Aynı profil/liste eşzamanlı istenirse Letterboxd ve TMDb işini çoğaltma.
    # shield: bir istemci bağlantıyı kapattığında paylaşılan görev diğer bekleyenler
    # ve cache yazımı için çalışmaya devam eder.
    task, joined = await _get_or_create_film_flight(
        username,
        list_type,
        enricher=enricher,
        pcache=pcache,
        scrape_kwargs=scrape_kwargs,
    )
    if joined:
        log.warning("single-flight JOIN %s/%s", list_type, username)

    return await asyncio.shield(task)


_LETTERBOXD_USERNAME_RE = re.compile(r"^[a-z0-9_]{2,15}$")


def _normalize_username(value: str) -> str:
    """Normalize and validate a public Letterboxd profile name.

    Keeping usernames to a small URL-safe alphabet prevents path/query injection
    into scraper URLs and avoids wasting external API budget on malformed input.
    """
    if not isinstance(value, str):
        raise ValueError("Letterboxd kullanıcı adı metin olmalı.")
    username = value.strip().lstrip("@").lower()
    if not _LETTERBOXD_USERNAME_RE.fullmatch(username):
        raise ValueError(
            "Letterboxd kullanıcı adı 2–15 karakter olmalı; yalnızca harf, rakam "
            "veya alt çizgi içerebilir."
        )
    return username


class _UsernameRequest(BaseModel):
    @field_validator("username", mode="before", check_fields=False)
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _normalize_username(value)


class RecommendRequest(_UsernameRequest):
    username: str


class RandomRequest(_UsernameRequest):
    username: str
    source: str = "community"

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        valid = {"community", "official_top_500", "official_most_fans"}
        if value not in valid:
            raise ValueError("Geçersiz öneri kaynağı.")
        return value


class DeleteDataRequest(_UsernameRequest):
    username: str


class BlendRequest(BaseModel):
    username1: str
    username2: str

    @field_validator("username1", "username2", mode="before")
    @classmethod
    def validate_usernames(cls, value: str) -> str:
        return _normalize_username(value)


class RegisterStartRequest(_UsernameRequest):
    username: str
    password: str
    password_confirm: str


class OwnershipVerifyRequest(_UsernameRequest):
    username: str
    code: str
    # Registration keeps this in memory in the browser so the verified account
    # can receive its session in the same request. It is optional to preserve
    # the manual-login fallback for older clients.
    password: str | None = None


class LoginRequest(_UsernameRequest):
    username: str
    password: str
    remember: bool = True


class SandboxSessionRequest(_UsernameRequest):
    """A Letterboxd name and nothing else: the sandbox has no passwords."""

    username: str


class PasswordResetStartRequest(_UsernameRequest):
    username: str


class PasswordResetFinishRequest(_UsernameRequest):
    username: str
    code: str
    new_password: str
    new_password_confirm: str


class CreateBlendRequest(BaseModel):
    recipient_username: str

    @field_validator("recipient_username", mode="before")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        return _normalize_username(value)


class BlendDecisionRequest(BaseModel):
    decision: str

    @field_validator("decision", mode="before")
    @classmethod
    def validate_decision(cls, value: str) -> str:
        decision = str(value).strip().lower()
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Karar accepted veya rejected olmalı.")
        return decision


class ReportUserRequest(BaseModel):
    category: str
    detail: str = ""

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, value: str) -> str:
        category = str(value).strip().lower()
        if category not in {"spam", "harassment", "impersonation", "other"}:
            raise ValueError("Geçersiz bildirim kategorisi.")
        return category

    @field_validator("detail", mode="before")
    @classmethod
    def validate_detail(cls, value: str) -> str:
        detail = str(value or "").strip()
        if len(detail) > 500:
            raise ValueError("Bildirim detayı en fazla 500 karakter olabilir.")
        return detail


class DiscoveryVisibilityRequest(BaseModel):
    visible: bool


class AccountPrivacyRequest(BaseModel):
    private: bool


class LocalePreferenceRequest(BaseModel):
    locale: str

    @field_validator("locale", mode="before")
    @classmethod
    def validate_locale(cls, value: str) -> str:
        locale = str(value or "").strip().lower()
        if locale not in {"auto", "tr", "en"}:
            raise ValueError("Language must be auto, tr, or en.")
        return locale


class FollowRequestDecision(BaseModel):
    decision: str

    @field_validator("decision", mode="before")
    @classmethod
    def validate_decision(cls, value: str) -> str:
        decision = str(value or "").strip().lower()
        if decision not in {"accepted", "rejected"}:
            raise ValueError("Karar accepted veya rejected olmalı.")
        return decision


class PostRequest(BaseModel):
    body: str
    film_slug: str = ""
    tmdb_id: int | None = None
    film_title: str = ""
    film_year: int | None = None
    spoiler: bool = False

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        value = str(value or "").strip()
        if not 1 <= len(value) <= 420:
            raise ValueError("Not 1–420 karakter arasında olmalı.")
        return value


class PushSubscriptionRequest(BaseModel):
    endpoint: str
    keys: dict[str, str]


class ReplyRequest(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        value = str(value or "").strip()
        if not 1 <= len(value) <= 280:
            raise ValueError("Cevap 1–280 karakter arasında olmalı.")
        return value


class LetterReceivingRequest(BaseModel):
    enabled: bool


class SendLetterRequest(BaseModel):
    recipient_username: str
    body: str
    film: dict | None = None

    @field_validator("recipient_username", mode="before")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        return _normalize_username(value)

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        value = str(value or "").strip()
        if not 1 <= len(value) <= 600:
            raise ValueError("Mektup 1–600 karakter arasında olmalı.")
        return value


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/push-sw.js")
def push_service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "push-sw.js", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
@app.head("/api/health", include_in_schema=False)
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "tmdb_enabled": settings.has_tmdb,
        "llm_enabled": settings.has_openai,
        "supabase_enabled": settings.has_supabase,
        "auth_enabled": getattr(settings, "has_auth", False),
        "web_push_enabled": settings.has_web_push,
        # The shell reads this to drop the password fields: there is no account
        # to protect, and asking for a password nobody set would be theatre.
        "sandbox": bool(getattr(settings, "sandbox_mode", False)),
    }


@app.get("/api/public/stats")
async def public_stats() -> dict:
    """Small, anonymous social-proof payload used by the public hero."""
    settings = get_settings()
    if not settings.has_supabase:
        return {"registered_users": 0}

    now = time.monotonic()
    if now - _public_stats_cache["checked_at"] < _PUBLIC_STATS_TTL_SECONDS:
        return {"registered_users": _public_stats_cache["registered_users"]}

    async with _public_stats_lock:
        # Another request may have refreshed the value while this one waited.
        now = time.monotonic()
        if now - _public_stats_cache["checked_at"] >= _PUBLIC_STATS_TTL_SECONDS:
            try:
                count = await asyncio.to_thread(_count_registered_users, settings)
            except Exception:
                # Keep the last known value during a transient Supabase issue;
                # this endpoint should never make the landing page fail.
                log.warning("public user count unavailable", exc_info=True)
                count = _public_stats_cache["registered_users"]
            _public_stats_cache.update(checked_at=now, registered_users=count)

    return {"registered_users": _public_stats_cache["registered_users"]}


@app.get("/api/share/image")
async def share_image(
    request: Request,
    url: str = "",
    slug: str = "",
    tmdb_id: int | None = None,
) -> Response:
    """Authenticated, allowlisted image bridge used by the PNG canvas exporter."""
    await _require_account(request)
    service = _auth_service()
    clean_slug = slug.strip().lower()
    if clean_slug and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,159}", clean_slug):
        raise HTTPException(status_code=400, detail="Geçersiz film kimliği.")

    candidates: list[str] = []
    if url:
        candidates.append(url)
    asset = {}
    if clean_slug:
        try:
            assets = await asyncio.to_thread(service.get_film_assets, [clean_slug])
            asset = assets.get(clean_slug) or {}
        except Exception:
            asset = {}
        if asset.get("poster_url"):
            candidates.append(asset["poster_url"])
        if not tmdb_id and asset.get("tmdb_id"):
            tmdb_id = int(asset["tmdb_id"])
        if asset.get("poster_resolver_url"):
            holder = {
                "poster_url": "",
                "poster_resolver_url": asset["poster_resolver_url"],
            }
            with contextlib.suppress(Exception):
                await resolve_missing_posters([holder])
            if holder.get("poster_url"):
                candidates.append(holder["poster_url"])

    if tmdb_id:
        try:
            pooled = await asyncio.to_thread(
                service.get_film_posters_by_tmdb_ids, [tmdb_id]
            )
        except Exception:
            pooled = {}
        if pooled.get(int(tmdb_id)):
            candidates.append(pooled[int(tmdb_id)])

    last_error: HTTPException | None = None
    for candidate in dict.fromkeys(candidates):
        try:
            content, media_type = await _fetch_share_image(candidate)
            break
        except HTTPException as exc:
            last_error = exc
    else:
        # A stable TMDb id can repair an old catalog row whose poster URL was
        # absent or no longer downloadable. The successful URL is persisted by
        # the shared Enricher/asset-store path for later users.
        settings = get_settings()
        if tmdb_id and settings.has_tmdb:
            _supabase_client, cache = _make_cache(settings)
            enricher = Enricher(settings.tmdb_api_key, cache, asset_store=service)
            meta = await enricher.movie_meta_by_id([int(tmdb_id)])
            repaired_url = (meta.get(int(tmdb_id)) or {}).get("poster_url") or ""
            if repaired_url:
                content, media_type = await _fetch_share_image(repaired_url)
                if clean_slug:
                    await asyncio.to_thread(
                        service.save_film_posters,
                        [{"slug": clean_slug, "tmdb_id": int(tmdb_id), "poster_url": repaired_url}],
                    )
            elif last_error:
                raise last_error
            else:
                raise HTTPException(status_code=404, detail="Poster bulunamadı.")
        elif last_error:
            raise last_error
        else:
            raise HTTPException(status_code=404, detail="Poster bulunamadı.")
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/readiness")
@app.head("/api/readiness", include_in_schema=False)
async def readiness(response: Response) -> dict:
    """Check that auth configuration and the required Supabase schema are usable."""
    settings = get_settings()
    if not settings.has_auth:
        response.status_code = 503
        return {"status": "not_ready", "auth_configured": False, "schema_ready": False}

    now = time.monotonic()
    ttl = 60 if _readiness_cache["ready"] else 15
    if now - _readiness_cache["checked_at"] < ttl:
        ready = bool(_readiness_cache["ready"])
    else:
        async with _readiness_lock:
            now = time.monotonic()
            ttl = 60 if _readiness_cache["ready"] else 15
            if now - _readiness_cache["checked_at"] >= ttl:
                try:
                    ready = await asyncio.to_thread(_auth_service().check_schema)
                except Exception as exc:
                    log.warning("readiness schema check failed: %s", type(exc).__name__)
                    ready = False
                _readiness_cache.update(checked_at=now, ready=bool(ready))
            else:
                ready = bool(_readiness_cache["ready"])
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "auth_configured": True,
        "schema_ready": ready,
    }


@app.post("/api/auth/register/start")
async def register_start(req: RegisterStartRequest, request: Request) -> dict:
    await _enforce_auth_rate_limit(request)
    try:
        validate_password(req.password, req.password_confirm)
        # Do not put Letterboxd in the critical path. The ownership check is
        # performed when the user returns with the bio code; issuing the code
        # now keeps registration responsive even when Letterboxd is slow.
        challenge = await asyncio.to_thread(
            _auth_service().start_registration,
            req.username,
            req.password,
            None,
            ip_hash=_ip_hash(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ScrapeError as exc:
        _raise_scrape_http(exc)
    except AuthError as exc:
        _raise_auth_http(exc)
    return {
        "username": challenge.username,
        "verification_code": challenge.verification_code,
        "expires_at": challenge.expires_at,
        "instruction": "Kodu Letterboxd profil bio alanına ekleyip doğrula.",
    }


@app.post("/api/auth/register/verify")
async def register_verify(
    req: OwnershipVerifyRequest, request: Request, response: Response
) -> dict:
    await _enforce_auth_rate_limit(request)
    session = None
    try:
        if req.password is not None:
            validate_password(req.password)
        profile = await scrape_profile(
            req.username,
            max_retries=get_settings().scrape_max_retries,
            resolve_posters=False,
        )
        account = await asyncio.to_thread(
            _auth_service().verify_ownership,
            req.username,
            req.code.strip(),
            profile,
            ip_hash=_ip_hash(request),
        )
        if req.password is not None:
            # The account is already active at this point. Reuse the password
            # that was supplied at registration so the browser does not need a
            # second round-trip (and a second Supabase auth handshake).
            try:
                session = await asyncio.to_thread(
                    _auth_service().login,
                    req.username,
                    req.password,
                    ip_hash=_ip_hash(request),
                )
            except AuthError:
                # Ownership is already verified. Keep the old manual-login
                # fallback if the combined auth handshake has a transient
                # failure, instead of making the user repeat verification.
                session = None
    except AccessBlockedError:
        # A public bio is still the ownership proof, but Cloudflare blocks a
        # Render IP from time to time. Do not trap a member on this screen:
        # open a private provisional session and keep the account out of every
        # active/social surface until a later successful bio read promotes it.
        try:
            account = await asyncio.to_thread(
                _auth_service().defer_ownership_verification,
                req.username,
                req.code.strip(),
                ip_hash=_ip_hash(request),
            )
            if req.password is not None:
                session = await asyncio.to_thread(
                    _auth_service().login,
                    req.username,
                    req.password,
                    ip_hash=_ip_hash(request),
                )
        except AuthError as exc:
            _raise_auth_http(exc)
        if session is not None:
            _set_session_cookies(response, session, remember=True)
        return {
            "ok": True,
            "account": account.__dict__,
            "logged_in": session is not None,
            "verification_deferred": True,
            "message": "Letterboxd bio kontrolü geçici olarak ertelendi; hesabın yalnızca sana açık şekilde devam ediyor.",
        }
    except ScrapeError as exc:
        _raise_scrape_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OwnershipPendingError as exc:
        # Checking back before the bio saved is the normal way to use this
        # screen, so it costs nothing: no attempt, no quota, just a clearer
        # instruction and an invitation to try again.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VerificationError as exc:
        # A wrong code is the only thing here worth counting against a guesser.
        await _record_auth_failure(request)
        _raise_auth_http(exc)
    except AuthError as exc:
        _raise_auth_http(exc)
    if session is not None:
        _set_session_cookies(response, session, remember=True)
    return {
        "ok": True,
        "account": account.__dict__,
        "logged_in": session is not None,
    }


if get_settings().sandbox_mode:  # pragma: no cover - local tooling only
    log.warning(
        "SANDBOX MODE — /api/sandbox/session signs in as any public Letterboxd "
        "profile with no password, and nothing is written to a database. This "
        "must never be set in a deployed environment."
    )

    @app.post("/api/sandbox/session")
    async def sandbox_session(
        req: SandboxSessionRequest, request: Request, response: Response
    ) -> dict:
        """Adopt any public Letterboxd profile for one throwaway session.

        There is no password because there is no account: the name is scraped,
        the result is held in memory, and closing the process is the delete.
        The route refuses a caller that is not on this machine, so a stray flag
        on a deployed host still cannot be reached from outside it.
        """
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(status_code=404, detail="Not found")
        service = _auth_service()
        if not isinstance(service, SandboxAuthService):
            raise HTTPException(status_code=404, detail="Not found")
        try:
            profile = await scrape_profile(
                req.username,
                max_retries=get_settings().scrape_max_retries,
                resolve_posters=False,
            )
        except ScrapeError as exc:
            _raise_scrape_http(exc)
        account, token = await asyncio.to_thread(service.open_session, profile)
        _set_session_cookies(
            response,
            AuthSession(
                account=account,
                access_token=token,
                refresh_token=token,
                expires_in=60 * 60 * 24,
            ),
            remember=False,
        )
        return {"ok": True, "account": account.__dict__, "sandbox": True}


@app.post("/api/auth/login")
async def login(req: LoginRequest, request: Request, response: Response) -> dict:
    await _enforce_auth_rate_limit(request)
    try:
        session = await asyncio.to_thread(
            _auth_service().login,
            req.username,
            req.password,
            ip_hash=_ip_hash(request),
        )
    except InvalidCredentialsError as exc:
        await _record_auth_failure(request)
        _raise_auth_http(exc)
    except AuthError as exc:
        _raise_auth_http(exc)
    _set_session_cookies(response, session, remember=req.remember)
    return {"ok": True, "account": session.account.__dict__}


if get_settings().dev_login_enabled:  # pragma: no cover - local tooling only
    log.warning(
        "DEV LOGIN ENABLED — /api/dev/login accepts a username without a "
        "password. This must never be set in a deployed environment."
    )

    @app.get("/api/dev/login")
    async def dev_login(request: Request, username: str = "") -> Response:
        """Password-free login for local testing, then straight to the app.

        The route exists only when DEV_LOGIN_ENABLED is set and additionally
        refuses anything but a loopback caller, so a stray flag on a deployed
        host still cannot be reached from outside the machine.
        """
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=404, detail="Not found")
        settings = get_settings()
        try:
            session = await asyncio.to_thread(
                _auth_service().login,
                _normalize_username(username),
                settings.dev_login_password,
                ip_hash="dev",
            )
        except AuthError as exc:
            _raise_auth_http(exc)
        except Exception as exc:  # noqa: BLE001 - this route exists to diagnose
            raise HTTPException(
                status_code=400,
                detail=(
                    f"@{username} için dev girişi yapılamadı: {exc}. "
                    "Hesabı 'python -m scripts.dev_start' ile hazırla."
                ),
            ) from exc
        response = RedirectResponse(url="/", status_code=303)
        _set_session_cookies(response, session)
        return response


@app.get("/api/auth/me")
async def auth_me(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, private"
    try:
        account = await _require_account(request)
    except HTTPException as exc:
        # The bootstrap is the one place a device may silently exchange its
        # durable, httpOnly credential for a new short-lived access token.
        # Other API calls retain the normal CSRF-protected retry in api.js.
        if exc.status_code != 401:
            raise
        account = await _restore_account_from_device(request, response)
    return {"account": account.__dict__}


@app.get("/api/push/public-key")
async def web_push_public_key(request: Request) -> dict:
    await _require_account(request)
    settings = get_settings()
    if not settings.has_web_push:
        raise HTTPException(status_code=503, detail="Tarayıcı push bildirimi henüz yapılandırılmadı.")
    return {"public_key": settings.web_push_vapid_public_key}


@app.post("/api/push/subscriptions")
async def save_web_push_subscription(req: PushSubscriptionRequest, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        await asyncio.to_thread(
            _auth_service().upsert_push_subscription, account, req.model_dump(),
            request.headers.get("user-agent", ""),
        )
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    return {"ok": True}


@app.post("/api/auth/refresh")
async def refresh_session(request: Request, response: Response) -> dict:
    _require_csrf(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE, "")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Oturum yenilenemedi.")
    try:
        session = await asyncio.to_thread(_auth_service().refresh, refresh_token)
    except TransientStorageError as exc:
        # Do not clear a valid refresh cookie merely because an upstream edge
        # briefly dropped the request. The PWA will retry this safely.
        _raise_auth_http(exc)
    except AuthError as exc:
        _clear_session_cookies(response)
        _raise_auth_http(exc)
    _set_session_cookies(response, session, remember=_remembered(request))
    return {"ok": True, "account": session.account.__dict__}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    _require_csrf(request)
    access_token = request.cookies.get(ACCESS_COOKIE, "")
    refresh_token = request.cookies.get(REFRESH_COOKIE, "")
    if access_token and refresh_token:
        await asyncio.to_thread(
            _auth_service().revoke, access_token, refresh_token
        )
    _invalidate_account_cache(access_token)
    _clear_session_cookies(response)
    return {"ok": True}


@app.post("/api/auth/password-reset/start")
async def password_reset_start(
    req: PasswordResetStartRequest, request: Request
) -> dict:
    await _enforce_auth_rate_limit(request)
    try:
        challenge = await asyncio.to_thread(
            _auth_service().start_password_reset,
            req.username,
            ip_hash=_ip_hash(request),
        )
    except AuthError as exc:
        _raise_auth_http(exc)
    return {
        "username": challenge.username,
        "verification_code": challenge.verification_code,
        "expires_at": challenge.expires_at,
        "instruction": "Kodu Letterboxd profil bio alanına ekleyip yeni parolanı belirle.",
    }


@app.post("/api/auth/password-reset/finish")
async def password_reset_finish(
    req: PasswordResetFinishRequest, request: Request
) -> dict:
    await _enforce_auth_rate_limit(request)
    try:
        validate_password(req.new_password, req.new_password_confirm)
        profile = await scrape_profile(
            req.username,
            max_retries=get_settings().scrape_max_retries,
            resolve_posters=False,
        )
        await asyncio.to_thread(
            _auth_service().finish_password_reset,
            req.username,
            req.code.strip(),
            req.new_password,
            profile,
            ip_hash=_ip_hash(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ScrapeError as exc:
        _raise_scrape_http(exc)
    except OwnershipPendingError as exc:
        # Same as registration: waiting for a bio edit is not a failed attempt.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VerificationError as exc:
        await _record_auth_failure(request)
        _raise_auth_http(exc)
    except AuthError as exc:
        _raise_auth_http(exc)
    return {"ok": True}


async def _profile_sync_status(account: Account, service) -> dict | None:
    """Read/resume an unfinished first sync without opening a new crawl.

    A profile page is opened frequently, especially from an installed app.
    It may resume a genuinely interrupted initial full import, but must never
    turn a normal visit into another Letterboxd crawl just because the daily
    incremental check is due.
    """
    job = await asyncio.to_thread(service.get_sync_job, account.id)
    if not profile_sync.is_running(account.id):
        if job and job.get("scope") == "full" and profile_sync.job_is_resumable(job):
            # Resume-on-visit: a job whose lease/heartbeat went stale after a
            # process restart is picked up without losing onboarding progress.
            profile_sync.start(_SyncPipeline(get_settings()), service, account)
    return profile_sync.progress_of(job)


@app.get("/api/profile/me")
async def profile_me(request: Request) -> dict:
    account = await _require_account(request)
    service = _auth_service()
    # The snapshot and the sync job are two independent reads, and this is the
    # first request the app makes after entry — overlapping them takes a full
    # round trip off the profile's time to paint.
    profile, sync_job = await asyncio.gather(
        asyncio.to_thread(service.get_profile, account),
        _profile_sync_status(account, service),
        return_exceptions=True,
    )
    if isinstance(profile, BaseException):
        raise profile
    profile["needs_refresh"] = bool(
        not profile.get("taste")
        or profile["taste"].get("algorithm_version") != TASTE_PROFILE_VERSION
    )
    if not isinstance(sync_job, BaseException):
        profile["sync_job"] = sync_job
    return profile


@app.get("/api/profile/social-stats")
async def profile_social_stats(request: Request) -> dict:
    """Keep profile follow counts cheap; loading the full public page is unnecessary."""
    account = await _require_account(request)
    return await asyncio.to_thread(_auth_service().social_stats, account)


@app.get("/api/profile/sync-status")
async def profile_sync_status(request: Request) -> dict:
    """Small polling payload; the full profile is fetched only after completion."""
    account = await _require_account(request)
    service = _auth_service()
    progress = await _profile_sync_status(account, service)
    return {"sync_job": progress}


@app.post("/api/profile/onboarding-complete")
async def complete_profile_onboarding(request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    service = _auth_service()
    # The first four favourites are enough to make the onboarding useful.
    # A full crawl remains a background task; keeping a new member trapped on
    # this endpoint until that crawl finishes made the signup time depend on
    # the size (and current availability) of Letterboxd's archive.
    #
    # Still read the job here: it deliberately resumes an interrupted full
    # import before the member enters the app, but it is no longer a gate.
    await _profile_sync_status(account, service)
    completed_at = await asyncio.to_thread(service.complete_onboarding, account)
    # Without this, a page reload within the 30s account cache window
    # (_require_account) still serves the pre-completion Account, sending a
    # user who just finished onboarding straight back into it.
    _invalidate_account_cache(request.cookies.get(ACCESS_COOKIE, ""))
    await _record_activity_event(
        service, account, "onboarding_completed", {"source": "profile"}
    )
    return {"ok": True, "completed_at": completed_at}


@app.post("/api/profile/discovery-settings")
async def update_discovery_settings(
    req: DiscoveryVisibilityRequest, request: Request
) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    service = _auth_service()
    try:
        visible = await asyncio.to_thread(service.set_discoverable, account, req.visible)
    except Exception as exc:
        log.warning("discovery visibility update failed account=%s", account.id, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Sinefil Sineması ayarı henüz hazır değil. SQL güncellemesini kontrol et.",
        ) from exc
    account.discoverable = visible
    await _record_activity_event(
        service, account, "sinefil_visibility_changed", {"visible": visible}
    )
    return {"ok": True, "discoverable": visible}


@app.post("/api/profile/privacy-settings")
async def update_account_privacy(
    req: AccountPrivacyRequest, request: Request
) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        private = await asyncio.to_thread(
            _auth_service().set_private_account, account, req.private
        )
    except Exception as exc:
        log.warning("account privacy update failed account=%s", account.id, exc_info=True)
        raise HTTPException(status_code=503, detail="Hesap gizliliği şu an kaydedilemedi.") from exc
    account.private_account = private
    await _record_activity_event(
        _auth_service(), account, "account_privacy_changed", {"private": private}
    )
    return {"ok": True, "private_account": private}


@app.post("/api/profile/locale")
async def update_profile_locale(
    req: LocalePreferenceRequest, request: Request
) -> dict:
    """Save the UI language preference selected from profile settings."""
    _require_csrf(request)
    account = await _require_account(request)
    try:
        locale = await asyncio.to_thread(
            _auth_service().set_preferred_locale, account, req.locale
        )
        await asyncio.to_thread(_auth_service().clear_taste_narrative, account)
    except Exception as exc:
        log.warning("locale preference update failed account=%s", account.id, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Language preference could not be saved.",
        ) from exc
    account.preferred_locale = locale
    # Rebuild prose from the already-saved film catalogue. This avoids a new
    # Letterboxd crawl solely because the member changed app language.
    asyncio.create_task(
        _refresh_locale_taste(account, _response_locale(account, request))
    )
    await _record_activity_event(
        _auth_service(), account, "locale_changed", {"locale": locale}
    )
    return {"ok": True, "locale": locale}


@app.post("/api/letters/receiving")
async def update_letter_receiving(req: LetterReceivingRequest, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        enabled = await asyncio.to_thread(
            _auth_service().set_letter_receiving, account, req.enabled
        )
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    account.letter_receiving_enabled = enabled
    await _record_activity_event(_auth_service(), account, "letter_receiving_changed", {"enabled": enabled})
    return {"ok": True, "letter_receiving_enabled": enabled}


@app.get("/api/letters/settings")
async def letter_settings(request: Request) -> dict:
    """Live letterbox state for a newly restored browser session."""
    account = await _require_account(request)
    enabled = await asyncio.to_thread(
        _auth_service().letter_receiving_status, account
    )
    return {"letter_receiving_enabled": enabled}


@app.get("/api/letters/followers")
async def list_letterable_followers(request: Request) -> dict:
    """Followers with an open letterbox, for the mobile compose shortcut."""
    account = await _require_account(request)
    followers = await asyncio.to_thread(
        _auth_service().list_letterable_followers, account
    )
    return {"followers": followers}


@app.get("/api/letters/recipients/{username}")
async def get_letter_recipient(username: str, request: Request) -> dict:
    account = await _require_account(request)
    try:
        normalized = _normalize_username(username)
        recipient = await asyncio.to_thread(
            _auth_service().get_letter_recipient, account, normalized
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    return {"recipient": recipient}


@app.get("/api/letters")
async def list_letters(request: Request) -> dict:
    account = await _require_account(request)
    letters = await asyncio.to_thread(_auth_service().list_letters, account)
    return {"letters": letters}


@app.delete("/api/letters/legacy")
async def purge_legacy_letters(request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    deleted = await asyncio.to_thread(_auth_service().purge_legacy_letters, account)
    if deleted:
        await _record_activity_event(_auth_service(), account, "legacy_letters_purged", {"count": deleted})
    return {"ok": True, "deleted": deleted}


@app.get("/api/letters/unread-count")
async def unread_letter_count(request: Request) -> dict:
    account = await _require_account(request)
    count = await asyncio.to_thread(_auth_service().count_unread_letters, account)
    return {"count": count}


@app.get("/api/letters/send-status")
async def letter_send_status(request: Request, recipient_username: str = "") -> dict:
    account = await _require_account(request)
    return await asyncio.to_thread(
        _auth_service().letter_send_status, account, recipient_username
    )


@app.post("/api/letters")
async def send_letter(req: SendLetterRequest, request: Request) -> dict:
    _require_csrf(request)
    await _enforce_member_action_limit(request)
    account = await _require_account(request)
    try:
        letter_id = await asyncio.to_thread(
            _auth_service().send_letter,
            account,
            req.recipient_username,
            req.body,
            req.film,
        )
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    await _record_activity_event(_auth_service(), account, "letter_sent", {})
    return {"ok": True, "letter_id": letter_id}


@app.post("/api/letters/{letter_id}/read")
async def mark_letter_read(letter_id: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", letter_id):
        raise HTTPException(status_code=422, detail="Geçersiz mektup kimliği.")
    marked = await asyncio.to_thread(_auth_service().mark_letter_read, account, letter_id)
    if marked:
        await _record_activity_event(_auth_service(), account, "letter_read", {})
    return {"ok": True, "read": marked}


@app.delete("/api/letters/{letter_id}")
async def delete_sent_letter(letter_id: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", letter_id):
        raise HTTPException(status_code=422, detail="Geçersiz mektup kimliği.")
    deleted = await asyncio.to_thread(_auth_service().delete_sent_letter, account, letter_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Bu gönderilmiş mektup bulunamadı.")
    await _record_activity_event(_auth_service(), account, "letter_deleted", {})
    return {"ok": True, "deleted": True}


@app.get("/api/sinefil-alani")
async def list_sinefil_alani(request: Request, q: str = "", page: int = 1, per_page: int = 12) -> dict:
    account = await _require_account(request)
    query = str(q or "").strip().lstrip("@").lower()
    if len(query) > 80:
        raise HTTPException(status_code=422, detail="Arama metni çok uzun.")
    if page < 1 or page > 10000:
        raise HTTPException(status_code=422, detail="Geçersiz sayfa.")
    if per_page < 1 or per_page > 24:
        raise HTTPException(status_code=422, detail="Geçersiz sayfa boyutu.")
    service = _auth_service()
    try:
        cards = await asyncio.to_thread(service.list_sinefil_cards, account, query)
    except Exception as exc:
        log.warning("sinefil directory unavailable account=%s", account.id, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Sinefil Sineması henüz hazır değil. SQL güncellemesini kontrol et.",
        ) from exc
    await _record_activity_event(service, account, "sinefil_area_opened", {"query": bool(query)})
    total = len(cards)
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages and total:
        page = pages
    start = (page - 1) * per_page
    return {
        "profiles": cards[start : start + per_page],
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages},
    }


@app.get("/api/sinefil-alani/{username}/personality")
async def sinefil_personality(username: str, request: Request) -> dict:
    account = await _require_account(request)
    try:
        normalized = _normalize_username(username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    service = _auth_service()
    try:
        personality = await asyncio.to_thread(
            service.sinefil_personality, account, normalized
        )
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    except Exception as exc:
        log.warning("sinefil personality unavailable username=%s", normalized, exc_info=True)
        raise HTTPException(status_code=503, detail="Kişilik okuması yüklenemedi.") from exc
    return {"username": normalized, "personality": personality}


@app.get("/api/profile/directors/{rank}/films")
async def profile_director_films(
    rank: int, request: Request, limit: int = 60, offset: int = 0
) -> dict:
    """Lazy-load one ranked director's watched films for the profile accordion."""
    account = await _require_account(request)
    if rank < 1 or rank > 10 or limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="Geçersiz yönetmen sayfalaması.")
    service = _auth_service()
    profile = await asyncio.to_thread(service.get_profile, account)
    taste = profile.get("taste") or {}
    names = [name for name in taste.get("top_directors", []) if name]
    if rank > len(names):
        raise HTTPException(status_code=404, detail="Yönetmen bulunamadı.")
    director = names[rank - 1]
    rows = await asyncio.to_thread(
        service.list_director_films,
        account.id,
        director,
        limit=limit,
        offset=offset,
    )
    films = [
        {
            "slug": row.get("film_slug") or "",
            "title": row.get("title") or "",
            "year": row.get("release_year"),
            "poster_url": row.get("poster_url") or "",
            "user_rating": row.get("user_rating"),
        }
        for row in rows
    ]
    return {
        "rank": rank,
        "director": director,
        "films": films,
        "offset": offset,
        "has_more": len(films) == limit,
    }


@app.get("/api/profile/watched")
async def profile_watched_films(request: Request, q: str = "", limit: int = 60) -> dict:
    """Search the member's own watched library (note composer, film pickers)."""
    account = await _require_account(request)
    if limit < 1 or limit > 120:
        raise HTTPException(status_code=422, detail="Geçersiz sayfalama.")
    films = await asyncio.to_thread(
        _auth_service().list_watched_for_picker, account.id, q.strip()[:80], limit
    )
    return {"films": films}


async def _fill_overviews(service, rows: list[dict], count: int) -> None:
    """Best-effort: plot / poster / director for the first `count` rows.

    Films already carrying a tmdb_id use the cached detail path; anything else
    (a diary entry the sweep hasn't reached yet) is resolved by a TMDb search.
    """
    settings = get_settings()
    if not (count and settings.has_tmdb and rows):
        return
    with contextlib.suppress(Exception):
        _client, cache = _make_cache(settings)
        enricher = Enricher(settings.tmdb_api_key, cache, asset_store=service)
        targets = rows[:count]
        assets = await asyncio.to_thread(
            service.get_film_assets,
            [row.get("slug") for row in targets if row.get("slug")],
        )
        for row in targets:
            asset = assets.get(row.get("slug")) or {}
            if asset.get("overview"):
                row["overview"] = asset["overview"]
            if not row.get("poster_url") and asset.get("poster_url"):
                row["poster_url"] = asset["poster_url"]
            if not row.get("director") and asset.get("director"):
                row["director"] = asset["director"]
            if not row.get("tmdb_id") and asset.get("tmdb_id"):
                row["tmdb_id"] = asset["tmdb_id"]
        meta = await enricher.movie_meta_by_id(
            [
                r["tmdb_id"]
                for r in targets
                if r.get("tmdb_id") and not r.get("overview")
            ]
        )
        need_search = [
            r
            for r in targets
            if not r.get("overview") and not r.get("tmdb_id") and r.get("title")
        ]
        searched = []
        if need_search:
            searched = await enricher.enrich(
                [
                    {"title": r["title"], "year": r.get("year"), "slug": r["slug"]}
                    for r in need_search
                ],
                include_details=True,
            )
        by_slug = {o.slug: o for o in searched if o.slug}
        for row in targets:
            if row.get("overview"):
                continue
            m = meta.get(row.get("tmdb_id"))
            if m:
                if m.get("overview"):
                    row["overview"] = m["overview"]
                if not row.get("poster_url") and m.get("poster_url"):
                    row["poster_url"] = m["poster_url"]
                if not row.get("director") and m.get("director"):
                    row["director"] = m["director"]
                continue
            o = by_slug.get(row["slug"])
            if not o:
                continue
            if o.overview:
                row["overview"] = o.overview
            if not row.get("poster_url") and o.poster_url:
                row["poster_url"] = o.poster_url
            if not row.get("director") and o.director:
                row["director"] = o.director
            if not row.get("tmdb_id") and o.tmdb_id:
                row["tmdb_id"] = o.tmdb_id
        await asyncio.to_thread(
            service.save_film_posters,
            [
                {
                    "slug": row.get("slug"),
                    "title": row.get("title") or "",
                    "release_year": row.get("year"),
                    "tmdb_id": row.get("tmdb_id"),
                    "poster_url": row.get("poster_url") or "",
                    "overview": row.get("overview") or "",
                    "director": row.get("director") or "",
                }
                for row in targets
                if row.get("slug") and row.get("overview")
            ],
        )


def _guess_lb_slug(title: str) -> str:
    """Best-effort Letterboxd slug from a title (usually just the slugified name)."""
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:160]


async def _discover_fallback_films(
    enricher, service, account, watched_films, *, genre_names=None, limit=40,
    min_vote_average: float = 6.0,
):
    """TMDb Discover films the user hasn't watched — for an empty watchlist."""
    if enricher is None:
        return []
    pool = []
    with contextlib.suppress(Exception):
        pool = await enricher.discover_pool(
            genre_names=genre_names,
            limit=limit + 25,
            min_vote_average=min_vote_average,
        )
    if not pool:
        return []
    watched_slugs: set[str] = set()
    watched_tmdb: set[int] = set()
    if account is not None and service is not None:
        with contextlib.suppress(Exception):
            watched_slugs = await asyncio.to_thread(
                service.get_watched_slugs, account.id
            )
    for f in watched_films or []:
        if getattr(f, "slug", ""):
            watched_slugs.add(f.slug)
        if getattr(f, "tmdb_id", None):
            watched_tmdb.add(int(f.tmdb_id))
    picks = []
    for film in pool:
        film.slug = _guess_lb_slug(film.title)
        if film.slug in watched_slugs:
            continue
        if film.tmdb_id and int(film.tmdb_id) in watched_tmdb:
            continue
        picks.append(film)
        if len(picks) >= limit:
            break
    return picks


RANDOM_POOL_SAMPLE = 40   # rows requested from the community pool per call
# A spin is meant to be worth taking, so it aims above this five-star average.
# It is an aim, not a guarantee: an empty pool is worse than a mediocre film.
RANDOM_MIN_AVERAGE = 3.5
RANDOM_PICK_COUNT = 3
OFFICIAL_LIST_CACHE_TTL = 30 * 24 * 3600
OFFICIAL_LIST_CACHE_NAMESPACE = "letterboxd_official_list_pages"
OFFICIAL_LIST_SOURCES = {
    "official_top_500": {
        "slug": "letterboxds-top-500-films",
        "pages": 5,
    },
    "official_most_fans": {
        "slug": "top-250-films-with-the-most-fans",
        "pages": 3,
    },
}


# The random mode writes its own explanations rather than paying for an LLM
# call per spin, so they have to be translated here like any other copy.
_RANDOM_REASONS: dict[str, dict[str, str]] = {
    "community_rated": {
        "tr": "Movienotes'da {watchers} sinefil izlemiş, ortalama {average} vermişler; senin listende yok.",
        "en": "{watchers} cinephiles on Movienotes have seen it and rated it {average} on average; it is not on your list.",
    },
    "community": {
        "tr": "Movienotes'da {watchers} sinefilin izlediği, senin listende olmayan bir film.",
        "en": "A film {watchers} cinephiles on Movienotes have seen and you have not listed.",
    },
    "community_one": {
        "tr": "Başka bir Movienotes üyesinin izlediği, senin listende olmayan bir film.",
        "en": "A film another Movienotes member has seen and you have not listed.",
    },
    "discover": {
        "tr": "Topluluk havuzu yetmedi; bunu TMDb'den, henüz izlemediklerin arasından seçtik.",
        "en": "The community pool came up short, so this one is from TMDb, among films you have not seen.",
    },
    "unlisted": {
        "tr": "Senin listende olmayan filmler arasından çıktı.",
        "en": "It came out of the films that are not on your list.",
    },
    "director": {
        "tr": " {director} imzalı olması da seçime küçük bir karakter katıyor.",
        "en": " Being a {director} film gives the pick a little character of its own.",
    },
    "genre": {
        "tr": " {genre} tarafında farklı bir ruh hâline alan açabilir.",
        "en": " On the {genre} side it might open room for a different mood.",
    },
    "chance": {
        "tr": " Kararsız kaldığın bir anda şansı bu filme bırakabilirsin.",
        "en": " When you cannot decide, you could leave it to chance and take this one.",
    },
    "official_top_director": {
        "tr": "{title}, en sık döndüğün yönetmenlerden {director}'ın filmi; ayrıca Letterboxd Top 500'de yer alıyor.",
        "en": "{title} is by {director}, one of the directors you return to most, and it also appears in Letterboxd's Top 500.",
    },
    "official_top_genre": {
        "tr": "{title}, sık izlediğin {genre} tarafına yakın olduğu ve Letterboxd Top 500'de yer aldığı için öne çıktı.",
        "en": "{title} stood out because it aligns with the {genre} films you return to and appears in Letterboxd's Top 500.",
    },
    "official_top_detail": {
        "tr": "{title}, {director} imzalı bir {genre}; Letterboxd Top 500'deki yeriyle keşfetmeye değer.",
        "en": "{title} is a {genre} film by {director}, with a place in Letterboxd's Top 500 worth exploring.",
    },
    "official_top_fallback": {
        "tr": "{title}, Letterboxd Top 500'de yer alıyor ve henüz izlediklerinde görünmüyor.",
        "en": "{title} is in Letterboxd's Top 500 and is not yet among your watched films.",
    },
    "official_fans_director": {
        "tr": "{title}, en sık döndüğün yönetmenlerden {director}'ın filmi; Letterboxd'ın en çok hayranı olan 250 filmi arasında.",
        "en": "{title} is by {director}, one of the directors you return to most, and is among Letterboxd's 250 films with the most fans.",
    },
    "official_fans_genre": {
        "tr": "{title}, sık izlediğin {genre} tarafına yakın ve Letterboxd'da güçlü bir hayran kitlesi var.",
        "en": "{title} aligns with the {genre} films you return to and has a strong Letterboxd fan base.",
    },
    "official_fans_detail": {
        "tr": "{title}, {director} imzalı bir {genre}; Letterboxd'da en çok hayranı olan 250 filmden biri.",
        "en": "{title} is a {genre} film by {director}, and one of Letterboxd's 250 films with the most fans.",
    },
    "official_fans_fallback": {
        "tr": "{title}, Letterboxd'da en çok hayranı olan 250 filmden biri ve henüz izlediklerinde görünmüyor.",
        "en": "{title} is one of Letterboxd's 250 films with the most fans and is not yet among your watched films.",
    },
}


def _random_reason(key: str, locale: str, **values) -> str:
    return _RANDOM_REASONS[key]["en" if locale == "en" else "tr"].format(**values)


def _community_reason(watchers: int, rating, locale: str = "tr") -> str:
    """Honest lead for a pick that came from what other members have watched."""
    try:
        average = float(rating) if rating is not None else 0.0
    except (TypeError, ValueError):
        average = 0.0
    if watchers >= 2 and average > 0:
        return _random_reason(
            "community_rated", locale, watchers=watchers, average=f"{average:.1f}"
        )
    if watchers >= 2:
        return _random_reason("community", locale, watchers=watchers)
    return _random_reason("community_one", locale)


def _pick_random_films(
    pool: list, n: int, *, favorite_directors=None, favorite_genres=None
) -> list:
    """A fresh sample on every call — the random mode has no daily quota.

    The draw is weighted rather than uniform: a film by a director the member
    returns to, or in a genre they watch most, is likelier to come up. It stays
    a draw, so the same spin never gives the same answer and nothing outside
    their taste is excluded — "random" that only ever offers the top of a
    ranking is just a slow recommendation.
    """
    if not pool:
        return []
    with_poster = [film for film in pool if getattr(film, "poster_url", "")]
    source = with_poster if len(with_poster) >= n else pool
    directors = {
        str(name).strip().casefold() for name in (favorite_directors or []) if name
    }
    genres = {
        str(name).strip().casefold() for name in (favorite_genres or []) if name
    }
    if not directors and not genres:
        return _random.sample(source, min(n, len(source)))

    def weight(film) -> float:
        value = 1.0
        if directors and str(getattr(film, "director", "") or "").strip().casefold() in directors:
            value += 2.0
        if genres and genres & {
            str(genre).strip().casefold() for genre in (getattr(film, "genres", None) or [])
        }:
            value += 1.0
        return value

    remaining = list(source)
    weights = [weight(film) for film in remaining]
    picked: list = []
    for _ in range(min(n, len(remaining))):
        choice = _random.choices(range(len(remaining)), weights=weights, k=1)[0]
        picked.append(remaining.pop(choice))
        weights.pop(choice)
    return picked


async def _community_random_pool(
    service, account, limit=RANDOM_POOL_SAMPLE, *, locale: str = "tr"
) -> list:
    """Films the membership watched and this account has not, watchlist-independent."""
    if service is None or account is None:
        return []
    rows = await asyncio.to_thread(service.community_random_films, account.id, limit)
    # The membership's own average is already on the five-star scale, so a spin
    # can prefer the films they rated well. Unrated films keep their place —
    # "no one has scored it yet" is not the same as "it scored badly".
    well_rated = [
        row for row in (rows or [])
        if row.get("avg_rating") is None
        or float(row.get("avg_rating") or 0) >= RANDOM_MIN_AVERAGE
    ]
    # Never trade an empty result for a stricter bar.
    rows = well_rated or rows
    films = []
    for row in rows or []:
        slug = row.get("film_slug") or ""
        title = (row.get("title") or "").strip()
        if not slug or not title:
            continue
        film = EnrichedFilm(
            title=title,
            year=row.get("release_year"),
            slug=slug,
            tmdb_id=row.get("tmdb_id"),
            overview=row.get("overview") or "",
            genres=row.get("genres") or [],
            director=row.get("director") or "",
            keywords=row.get("keywords") or [],
            poster_url=row.get("poster_url") or None,
            matched=bool(row.get("tmdb_id")),
        )
        film.reason = _community_reason(
            int(row.get("watcher_count") or 0), row.get("avg_rating"), locale
        )
        films.append(film)
    return films


async def _official_list_pool(source: str, service, account, cache) -> list[EnrichedFilm]:
    """Sample an official list page and remove every film the member watched.

    The list page is retained for a month in the shared cache. A recommendation
    normally reads one cached page and one local database query; it does not
    trigger a profile crawl or download an entire public list.
    """
    config = OFFICIAL_LIST_SOURCES.get(source)
    if not config or service is None or account is None:
        return []

    pages = list(range(1, int(config["pages"]) + 1))
    # A heavily watched cinephile can have every title from a sampled page.
    # Try a few distinct pages, still at most one network request per uncached
    # page and zero Letterboxd requests for warm cache entries.
    for page in _random.sample(pages, min(3, len(pages))):
        cache_key = f"{source}:page:{page}"
        cached = await asyncio.to_thread(
            cache.get, OFFICIAL_LIST_CACHE_NAMESPACE, cache_key, OFFICIAL_LIST_CACHE_TTL
        )
        rows = cached if isinstance(cached, list) else []
        if not rows:
            try:
                window = await scrape_official_list(
                    config["slug"], start_page=page, max_pages=1, max_retries=1
                )
            except ScrapeError:
                continue
            rows = [film.to_dict() for film in window.films if film.slug]
            if not rows:
                continue
            await asyncio.to_thread(
                cache.set, OFFICIAL_LIST_CACHE_NAMESPACE, cache_key, rows
            )
            flush = getattr(cache, "flush", None)
            if flush:
                await asyncio.to_thread(flush)

        slugs = [str(row.get("slug") or "") for row in rows if row.get("slug")]
        if not slugs:
            continue
        watched = await asyncio.to_thread(service.get_watched_slugs, account.id)
        unseen = [row for row in rows if row.get("slug") and row["slug"] not in watched]
        if unseen:
            # Official-list cache rows intentionally start as lightweight
            # Letterboxd data. Merge any TMDb metadata already learned from a
            # previous spin so later recommendations render with their poster,
            # overview and genres without another external lookup.
            assets: dict[str, dict] = {}
            asset_getter = getattr(service, "get_film_assets", None)
            if asset_getter:
                with contextlib.suppress(Exception):
                    assets = await asyncio.to_thread(
                        asset_getter, [str(row.get("slug") or "") for row in unseen]
                    )
            return [
                EnrichedFilm(
                    title=str(
                        (assets.get(str(row.get("slug") or ""), {}).get("title"))
                        or row.get("title")
                        or ""
                    ).strip(),
                    year=(
                        assets.get(str(row.get("slug") or ""), {}).get("release_year")
                        or row.get("year")
                    ),
                    slug=str(row.get("slug") or ""),
                    tmdb_id=assets.get(str(row.get("slug") or ""), {}).get("tmdb_id"),
                    overview=assets.get(str(row.get("slug") or ""), {}).get("overview") or "",
                    genres=assets.get(str(row.get("slug") or ""), {}).get("genres") or [],
                    director=assets.get(str(row.get("slug") or ""), {}).get("director") or "",
                    keywords=assets.get(str(row.get("slug") or ""), {}).get("keywords") or [],
                    vote_average=float(
                        assets.get(str(row.get("slug") or ""), {}).get("vote_average") or 0
                    ),
                    poster_url=(
                        assets.get(str(row.get("slug") or ""), {}).get("poster_url")
                        or row.get("poster_url")
                        or None
                    ),
                    matched=bool(
                        assets.get(str(row.get("slug") or ""), {}).get("matched")
                        or assets.get(str(row.get("slug") or ""), {}).get("tmdb_id")
                    ),
                    details_loaded=bool(
                        assets.get(str(row.get("slug") or ""), {}).get("details_loaded")
                    ),
                )
                for row in unseen
                if str(row.get("title") or "").strip()
            ]
    return []


async def _hydrate_random_picks(chosen: list[EnrichedFilm], settings, cache, service) -> list[EnrichedFilm]:
    """Ensure recommendation cards have the metadata the UI actually renders."""
    missing_details = [
        film for film in chosen if not film.poster_url or not film.overview
    ]
    if missing_details and settings.has_tmdb:
        enricher = Enricher(settings.tmdb_api_key, cache, asset_store=service)
        # `ensure_details` only fills known TMDb ids. Official Letterboxd list
        # rows start without an id, so they must go through the full title/year
        # match once. `enrich` also persists the result in the shared catalog.
        with contextlib.suppress(Exception):
            chosen = await enricher.enrich(chosen, include_details=True)
    still_missing = [film for film in chosen if not film.poster_url and film.slug]
    if still_missing:
        with contextlib.suppress(Exception):
            await resolve_missing_posters(still_missing)
    return chosen


def _official_pick_reason(
    film: EnrichedFilm,
    source: str,
    locale: str,
    favorite_directors: list[str] | None,
    favorite_genres: list[str] | None,
) -> str:
    """Give every official-list card a film-specific, taste-aware rationale."""
    title = film.title or "Bu film"
    director = (film.director or "").strip()
    genres = [str(genre).strip() for genre in (film.genres or []) if str(genre).strip()]
    director_matches = {
        str(name).strip().casefold() for name in (favorite_directors or []) if name
    }
    genre_matches = {
        str(name).strip().casefold() for name in (favorite_genres or []) if name
    }
    is_top = source == "official_top_500"
    prefix = "official_top" if is_top else "official_fans"

    if director and director.casefold() in director_matches:
        return _random_reason(
            f"{prefix}_director", locale, title=title, director=director
        )
    matching_genre = next((genre for genre in genres if genre.casefold() in genre_matches), "")
    if matching_genre:
        return _random_reason(
            f"{prefix}_genre", locale, title=title, genre=matching_genre
        )
    if director and genres:
        return _random_reason(
            f"{prefix}_detail", locale, title=title, director=director, genre=genres[0]
        )
    return _random_reason(f"{prefix}_fallback", locale, title=title)


def _add_random_reasons(
    films: list,
    *,
    source: str,
    locale: str = "tr",
    favorite_directors: list[str] | None = None,
    favorite_genres: list[str] | None = None,
) -> list:
    """Give every pick a concrete source and film/taste-specific explanation."""
    for film in films:
        if source in OFFICIAL_LIST_SOURCES:
            film.reason = _official_pick_reason(
                film, source, locale, favorite_directors, favorite_genres
            )
            continue
        director = getattr(film, "director", "") or ""
        genres = getattr(film, "genres", None) or []
        lead = getattr(film, "reason", "") or _random_reason(
            "discover" if source == "discover" else "unlisted", locale
        )
        if director:
            detail = _random_reason("director", locale, director=director)
        elif genres:
            detail = _random_reason("genre", locale, genre=genres[0])
        else:
            detail = _random_reason("chance", locale)
        film.reason = lead + detail
    return films


async def _random_discover_pool(settings, service, account, cache, limit=RANDOM_POOL_SAMPLE):
    """Unseen TMDb films for the random mode when there is no community history yet.

    Returns the candidate pool rather than a final pick: the caller samples from
    it and enriches only the few films it actually shows.
    """
    if not settings.has_tmdb:
        return []
    enricher = Enricher(settings.tmdb_api_key, cache, asset_store=service)
    return await _discover_fallback_films(
        enricher, service, account, [], genre_names=None, limit=limit,
        # A spin should be worth taking: RANDOM_MIN_AVERAGE on the five-star
        # scale, stated to TMDb in its own ten-point terms.
        min_vote_average=RANDOM_MIN_AVERAGE * 2,
    )


async def _diary_recent_rows(
    account: Account, service, settings, limit: int
) -> tuple[list[dict], bool]:
    """Last `limit` films in real watch order. Returns (rows, is_watch_order).

    Two sources carry the order a member actually watched in: the diary pages
    and the RSS feed. The stored history does not — `watched_rank` follows the
    /films/ listing, which is ordered by release date — so falling back to it
    answers a different question, and the caller is told when that happened.
    """
    try:
        films, _ = await scrape_diary(
            account.username, max_pages=1, film_limit=max(limit + 5, 15),
            max_retries=settings.scrape_max_retries,
        )
    except ScrapeError:
        films = []
    scraped = [f for f in films if f.slug][:limit]
    if not scraped:
        # The RSS feed is the other genuine watch-order source and costs one
        # request, so it is tried before giving up on chronology.
        with contextlib.suppress(Exception):
            rss = await _scrape_watched_rss(account.username)
            scraped = [f for f in rss if f.slug][:limit]
    if not scraped:
        rows = await asyncio.to_thread(service.list_recent_watched, account.id, limit)
        return rows, False
    by_slug = await asyncio.to_thread(
        service.watched_films_by_slugs, account.id, [f.slug for f in scraped]
    )
    out: list[dict] = []
    for f in scraped:
        d = by_slug.get(f.slug, {})
        rating = f.user_rating if f.user_rating is not None else d.get("user_rating")
        out.append({
            "slug": f.slug,
            "title": f.title or d.get("title", ""),
            "year": f.year or d.get("year"),
            "director": d.get("director", ""),
            "poster_url": d.get("poster_url") or f.poster_url or "",
            "user_rating": rating,
            "tmdb_id": d.get("tmdb_id"),
        })
    return out, True


@app.get("/api/profile/recent")
async def profile_recent_films(
    request: Request, preview: int = 10, fresh: bool = False
) -> dict:
    """The user's last 10 films in real watch order, all with plot summaries."""
    account = await _require_account(request)
    service = _auth_service()
    settings = get_settings()
    supabase_client, _ = _make_cache(settings)
    pcache = _make_persistent_cache(settings, supabase_client)
    cached = None
    if not fresh:
        with contextlib.suppress(Exception):
            cached = await asyncio.to_thread(
                pcache.get, "films_diary_recent", account.username, 3600
            )
    if cached:
        rows = cached
    else:
        rows, watch_order = await _diary_recent_rows(account, service, settings, 10)
        # Caching a release-ordered fallback under the diary key is what made
        # this card show the newest films for an hour at a time; only real
        # watch order is worth storing.
        if watch_order:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    pcache.set, "films_diary_recent", account.username, rows
                )
    await _fill_overviews(service, rows, max(0, min(preview, 10)))
    return {"films": rows}


_bulletin_ingest_task: asyncio.Task | None = None
_bulletin_scheduler_task: asyncio.Task | None = None


async def _bulletin_refresh_loop() -> None:
    """Keep the cinema programme current while Render is kept awake.

    UptimeRobot already keeps the free Render web service alive. This light
    hourly tick does no external work unless the DB's per-venue lease says the
    configured refresh interval has elapsed, so restarts and multiple workers
    remain harmless.
    """
    settings = get_settings()
    service = _auth_service()
    while True:
        _kick_bulletin_ingest(settings, service)
        await asyncio.sleep(60 * 60)


def _kick_bulletin_ingest(settings, service) -> None:
    """Refresh the programme in the background, at most one run per process.

    There is no worker dyno, so the first member to open the bulletin nudges the
    ingest. The DB lease makes that safe across processes, and the caller never
    waits: the card renders from whatever rows already exist.
    """
    global _bulletin_ingest_task
    if not settings.has_tmdb:
        return
    if _bulletin_ingest_task is not None and not _bulletin_ingest_task.done():
        return

    async def _run():
        try:
            supabase_client, cache = _make_cache(settings)
            enricher = Enricher(settings.tmdb_api_key, cache, asset_store=service)
            release_written = await screenings.ingest_release_layer(
                service, settings, enricher=enricher
            )
            # Venues run after the release layer so their titles can match
            # against films it has just resolved.
            written = await screenings.ingest_venues(service, enricher=enricher)
            # A refreshed source invalidates every card built from the old one.
            if release_written or written:
                await asyncio.to_thread(
                    service.clear_bulletin_digests, screenings.week_start().isoformat()
                )
        except Exception as exc:  # noqa: BLE001 - never surface to the caller
            log.warning("bulletin ingest failed: %s", exc)

    _bulletin_ingest_task = asyncio.create_task(_run())


async def _notify_bulletin(service, account, week: str, payload: dict) -> None:
    """İzleme listesindeki ya da zevkine uyan bir film perdeye geldiyse haftada
    bir kez haber verir. `event_key` sayesinde aynı hafta ikinci bildirim
    düşmüyor, dolayısıyla her istekte çağırmak zararsız."""
    if not payload.get("highlighted"):
        return
    await asyncio.to_thread(
        service.notify, account.id, "bulletin", None, None,
        event_key=f"bulletin:{week}",
    )


@app.get("/api/bulletin")
async def bulletin(request: Request) -> dict:
    """The whole current cinema programme, ordered for this member."""
    settings = get_settings()
    account = await _require_account(request)
    if not settings.bulletin_enabled:
        return {"enabled": False, "films": [], "venues": []}

    service = _auth_service()
    week = screenings.week_start().isoformat()

    cached = await asyncio.to_thread(service.get_bulletin_digest, account.id, week, "")
    # A payload written by an older shape is stale, not usable.
    cached_at = None
    with contextlib.suppress(TypeError, ValueError):
        cached_at = datetime.fromisoformat(
            str((cached or {}).get("generated_at") or "").replace("Z", "+00:00")
        )
    digest_fresh = bool(
        cached_at and cached_at >= datetime.now(timezone.utc) - timedelta(
            hours=max(1, settings.bulletin_digest_ttl_hours)
        )
    )
    if cached and cached.get("version") == screenings.PAYLOAD_VERSION and digest_fresh:
        await _notify_bulletin(service, account, week, cached)
        _kick_bulletin_ingest(settings, service)
        return {"enabled": True, **cached}

    rows = await asyncio.to_thread(service.list_screenings)
    if not rows:
        _kick_bulletin_ingest(settings, service)
        return {
            "enabled": True,
            "films": [],
            "venues": [],
            "total": 0,
            "preparing": True,
        }

    watched = await asyncio.to_thread(service.get_rated_watched_films, account.id)
    supabase_client, _ = _make_cache(settings)
    pcache = _make_persistent_cache(settings, supabase_client)
    watchlist: list = []
    if pcache is not None:
        with contextlib.suppress(Exception):
            entry = await asyncio.to_thread(
                pcache.get_with_freshness, "films_watchlist", account.username, ttl=None
            )
            watchlist = (entry[0] if entry else []) or []
    stored = await asyncio.to_thread(service.get_profile, account)
    payload = screenings.build_bulletin(
        rows, watched, watchlist, (stored or {}).get("taste") or {}
    )
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    # An empty listing means the ingest is still running; that is not worth a
    # week of cache, and it heals on its own.
    if payload.get("total"):
        await asyncio.to_thread(
            service.save_bulletin_digest, account.id, week, "", payload
        )
        await _notify_bulletin(service, account, week, payload)
    await _record_activity_event(
        service,
        account,
        "bulletin_viewed",
        {"total": payload.get("total", 0), "highlighted": payload.get("highlighted", 0)},
    )
    _kick_bulletin_ingest(settings, service)
    return {"enabled": True, **payload}


@app.get("/api/profile/stats")
async def profile_stats(request: Request) -> dict:
    """Lightweight Letterboxd counters (total watched, films this year)."""
    account = await _require_account(request)
    if account.letterboxd_stats:
        return {
            "films": int(account.letterboxd_stats.get("films", 0) or 0),
            "this_year": int(account.letterboxd_stats.get("this_year", 0) or 0),
        }
    settings = get_settings()
    supabase_client, _ = _make_cache(settings)
    pcache = _make_persistent_cache(settings, supabase_client)
    with contextlib.suppress(Exception):
        cached = await asyncio.to_thread(
            pcache.get, "profile_stats", account.username, 3600
        )
        if cached:
            return cached
    out = {"films": 0, "this_year": 0}
    try:
        profile = await scrape_profile(
            account.username, max_retries=settings.scrape_max_retries
        )
        out = {
            "films": int(profile.stats.get("films", 0) or 0),
            "this_year": int(profile.stats.get("this_year", 0) or 0),
        }
    except ScrapeError:
        pass
    with contextlib.suppress(Exception):
        await asyncio.to_thread(pcache.set, "profile_stats", account.username, out)
    return out


@app.get("/api/profile/film-overview")
async def profile_film_overview(
    request: Request, slug: str, title: str = "", year: int | None = None
) -> dict:
    """One film's plot, resolved lazily when a list row is expanded."""
    account = await _require_account(request)
    clean = slug.strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,159}$", clean):
        raise HTTPException(status_code=422, detail="Geçersiz film.")
    service = _auth_service()
    try:
        row = await asyncio.to_thread(service.watched_film_by_slug, account.id, clean)
    except TransientStorageError:
        # This is a best-effort panel; a temporary database edge error should
        # not turn expanding a film card into a user-visible 500.
        log.warning("film overview storage read unavailable account=%s", account.id)
        return {"overview": ""}
    if row is None:
        row = {"slug": clean, "title": title.strip()[:200], "year": year}
    if not row.get("title"):
        return {"overview": ""}
    await _fill_overviews(service, [row], 1)
    return {"overview": row.get("overview", "")}


async def _provisional_profile_sync(
    account: Account, settings, service, *, force: bool
) -> dict:
    """Fast in-request bootstrap: identity + Fav 4 only.

    The complete watched history is intentionally owned by the checkpointed
    background crawl. Doing a separate 250-film scrape here duplicated the
    first pages and delayed the moment that the exhaustive crawl could start.
    """
    # A failed incremental job records its temporary scope as "incremental".
    # Do not mistake that bookkeeping row for a brand-new account and replace
    # a real, durable film archive with a zero-film bootstrap snapshot.
    stored = await asyncio.to_thread(service.get_profile, account)
    stored_count = await asyncio.to_thread(service.count_watched_films, account.id)
    if stored_count:
        stored["account"] = account.__dict__
        stored.setdefault("letterboxd_stats", account.letterboxd_stats or {})
        return stored

    await asyncio.to_thread(service.mark_sync_status, account.id, "syncing")
    try:
        stored_taste = stored.get("taste") or {}
        # A profile page is a single small request and is the source of truth
        # for Fav 4. Never bootstrap a new account from an old DB copy.
        profile = await scrape_profile(
            account.username,
            max_retries=settings.scrape_max_retries,
            resolve_posters=False,
        )
        _supabase_client, cache = _make_cache(settings)
        enricher = (
            Enricher(settings.tmdb_api_key, cache, asset_store=service)
            if settings.has_tmdb else None
        )
        watched: list[EnrichedFilm] = []
        source_fingerprint = taste_source_fingerprint(profile, watched)
        if enricher is not None:
            favorites = await enricher.enrich(
                profile.favorite_films, include_details=False
            )
        else:
            favorites = [
                EnrichedFilm(
                    title=film.title,
                    year=film.year,
                    slug=film.slug,
                    poster_url=film.poster_url,
                )
                for film in profile.favorite_films
            ]
        await _resolve_favorite_posters(
            favorites,
            [{"film_slug": f.slug, "poster_url": f.poster_url} for f in watched],
            service,
            enricher,
        )
        # The bootstrap analysis is intentionally based on the four explicit
        # favourites, not on an empty watched list.  This makes genre, theme
        # and director signals available in the first onboarding seconds;
        # _SyncPipeline replaces it with the full-history snapshot later.
        taste = build_taste_profile(favorites, favorites)
        taste.source_fingerprint = source_fingerprint
        taste.personality = personality_from_favorites(favorites)
        if stored_taste.get("personality"):
            taste.personality = stored_taste["personality"]
        await _apply_director_photos(taste, enricher, service)
        await asyncio.to_thread(
            service.save_profile_snapshot,
            account,
            profile,
            favorites,
            taste,
        )
        # save_profile_snapshot marks a normal completed refresh as ready. This
        # bootstrap is different: keep the account locked in onboarding until
        # the background job has crawled every Letterboxd history page and
        # committed its interim snapshot.
        await asyncio.to_thread(service.mark_sync_status, account.id, "syncing")
        account.display_name = profile.display_name
        account.avatar_url = profile.avatar_url or ""
        account.profile_sync_status = "syncing"
        return {
            "account": account.__dict__,
            "taste": taste.to_dict(),
            "favorite_films": [film.to_dict() for film in favorites[:4]],
            "letterboxd_stats": profile.stats,
        }
    except ScrapeError as exc:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(service.mark_sync_status, account.id, "failed")
        _raise_scrape_http(exc)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("profile sync failed username=%s: %s", account.username, exc)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(service.mark_sync_status, account.id, "failed")
        raise HTTPException(
            status_code=503,
            detail="Profil senkronu tamamlanamadı. Son sağlam analiz korunuyor.",
        ) from exc


def _favorite_slug_tuple(films: list) -> tuple[str, ...]:
    """Normalize stored dicts and enriched objects for stable Fav 4 comparison."""
    slugs: list[str] = []
    for film in (films or [])[:4]:
        slug = film.get("slug") if isinstance(film, dict) else getattr(film, "slug", "")
        if slug:
            slugs.append(slug)
    return tuple(slugs)


def _personality_refresh_needed(stored_snapshot: dict, favorites: list) -> bool:
    stored_taste = stored_snapshot.get("taste") or {}
    return (
        _favorite_slug_tuple(stored_snapshot.get("favorite_films") or [])
        != _favorite_slug_tuple(favorites)
        or not stored_taste.get("analysis")
    )


async def _refresh_profile_favorites(
    account: Account, settings, service, *, locale: str | None = None
) -> dict:
    """Fetch only the public profile page and immediately expose a changed Fav 4.

    This deliberately does *not* crawl the diary or watchlist. It keeps a
    favourite edit visible in seconds and lets the background taste refresh
    rebuild prose from the saved watched-film catalogue afterwards.
    """
    stored = await asyncio.to_thread(service.get_profile, account)
    profile = await scrape_profile(
        account.username,
        max_retries=settings.scrape_max_retries,
        resolve_posters=False,
    )
    previous = _favorite_slug_tuple(stored.get("favorite_films") or [])
    current = _favorite_slug_tuple(profile.favorite_films)
    identity_changed = (
        str(account.display_name or "").strip() != str(profile.display_name or "").strip()
        or str(account.avatar_url or "").strip() != str(profile.avatar_url or "").strip()
        or (profile.stats or {}) != (account.letterboxd_stats or {})
    )
    changed = current != previous or identity_changed
    if not changed:
        stored["account"] = account.__dict__
        return {"changed": False, "profile": stored}

    _supabase_client, cache = _make_cache(settings)
    enricher = (
        Enricher(settings.tmdb_api_key, cache, asset_store=service)
        if settings.has_tmdb
        else None
    )
    if enricher is not None:
        favorites = await enricher.enrich(profile.favorite_films, include_details=False)
    else:
        favorites = [
            EnrichedFilm(
                title=film.title,
                year=film.year,
                slug=film.slug,
                poster_url=film.poster_url,
            )
            for film in profile.favorite_films
        ]
    await _resolve_favorite_posters(favorites, [], service, enricher)
    await asyncio.to_thread(
        service.save_profile_identity_and_favorites, account, profile, favorites
    )
    account.display_name = profile.display_name or account.display_name
    account.avatar_url = profile.avatar_url or ""
    account.letterboxd_stats = profile.stats or {}
    fresh = await asyncio.to_thread(service.get_profile, account)
    fresh["account"] = account.__dict__
    # New favourites are an explicit taste signal. Build the prose separately
    # so the tap that triggered refresh remains fast.
    _schedule_favorite_taste_refresh(account, force_analysis=True, locale=locale)
    return {"changed": True, "profile": fresh}


async def _sync_recent_history_on_entry(account: Account, settings, service) -> dict:
    """Merge only the newest Letterboxd observations into a stored archive.

    This deliberately avoids the old all-incomplete-film repair pass. Opening
    the installed app should check new watches and rating edits, not begin a
    multi-thousand-film metadata job.
    """
    known = await asyncio.to_thread(service.get_watched_slugs, account.id)
    existing = {
        row.get("film_slug"): row
        for row in await asyncio.to_thread(service.get_watched_films, account.id)
    }
    recent = await scrape_recent_watched(
        account.username, max_retries=settings.scrape_max_retries
    )
    new_rows = [film for film in recent if film.slug and film.slug not in known]
    rating_updates = [
        {
            "slug": film.slug,
            "user_rating": film.user_rating,
            "rating_observed": True,
        }
        for film in recent
        if film.slug in existing
        and existing[film.slug].get("user_rating") != film.user_rating
    ]
    if not new_rows and not rating_updates:
        return {"new_films": 0, "rating_updates": 0}

    if new_rows:
        seeds = []
        for offset, film in enumerate(new_rows):
            seeds.append(
                {
                    "slug": film.slug,
                    "title": film.title,
                    "year": film.year,
                    "user_rating": film.user_rating,
                    "poster_url": film.poster_url,
                    "poster_resolver_url": film.poster_resolver_url,
                    "watched_rank": -(len(new_rows) - offset),
                    "rating_observed": True,
                }
            )
        pipeline = _SyncPipeline(settings, use_stored_profile=True)
        enriched = await pipeline.enrich_search(seeds)
        await asyncio.to_thread(service.save_watched_films, account.id, enriched)
        detailed = await pipeline.enrich_details(enriched)
        if detailed:
            await asyncio.to_thread(service.save_watched_films, account.id, detailed)
    if rating_updates:
        await asyncio.to_thread(service.save_watched_films, account.id, rating_updates)

    # The persisted Fav 4 is reused, so this rebuild makes no second
    # Letterboxd profile request. Metadata repair stays bounded for entry sync.
    total = await _SyncPipeline(settings, use_stored_profile=True).rebuild_snapshot(
        account,
        use_llm=True,
        repair_all=False,
    )
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            service.upsert_sync_job,
            account.id,
            state="done",
            phase="done",
            scope="full",
            films_processed=total,
            films_total=total,
            last_error="",
            backoff_until=None,
            lease_token=None,
            lease_expires_at=None,
        )
        await asyncio.to_thread(service.mark_sync_status, account.id, "ready")
    return {"new_films": len(new_rows), "rating_updates": len(rating_updates)}


async def _sync_recent_diary_on_entry(account: Account, settings, service) -> int:
    """Import the member's newest written Letterboxd logs, if any."""
    entries = await scrape_reviewed_diary(account.username, max_pages=1)
    if entries is None:
        return 0
    keep = max(1, int(getattr(settings, "diary_scan_entries", 3)))
    newest = sorted(
        (entry for entry in entries if entry.slug and entry.review.strip()),
        key=lambda entry: entry.watched_on,
        reverse=True,
    )[:keep]
    rows = [
        {
            "source_key": entry.key,
            "film_slug": entry.slug,
            "film_title": entry.title,
            "film_year": entry.year,
            "tmdb_id": entry.tmdb_id,
            "body": entry.review,
            "payload": {
                "rating": entry.rating,
                "rewatch": entry.rewatch,
                "watched_on": entry.watched_on,
            },
            "created_at": f"{entry.watched_on}T12:00:00+00:00",
        }
        for entry in newest
    ]
    written = await asyncio.to_thread(service.import_diary_entries, account.id, rows)
    await asyncio.to_thread(service.mark_diary_synced, account.id, wrote=written)
    return written


_entry_sync_tasks: dict[int, asyncio.Task] = {}


async def _profile_sync_retry_loop() -> None:
    """Resume a bounded batch of queued/failed full imports every 30 seconds."""
    while True:
        try:
            service = _auth_service()
            if not isinstance(service, SandboxAuthService):
                candidates = await asyncio.to_thread(service.resumable_sync_accounts, 3)
                for account, job in candidates:
                    if profile_sync.is_running(account.id) or not profile_sync.job_is_resumable(job):
                        continue
                    await profile_sync.ensure_started(
                        _SyncPipeline(get_settings()), service, account, scope="full"
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a retry scan must not affect requests
            log.warning("profile sync retry scan failed", exc_info=True)
        await asyncio.sleep(30)


async def _run_entry_sync(account: Account, settings, service) -> None:
    """Best-effort, sequential entry sync; failures retain the last good state."""
    try:
        favorite_result = await _refresh_profile_favorites(account, settings, service)
        history_result = await _sync_recent_history_on_entry(account, settings, service)
        diary_written = await _sync_recent_diary_on_entry(account, settings, service)
        await _record_activity_event(
            service,
            account,
            "entry_sync_completed",
            {
                "favorites_changed": bool(favorite_result.get("changed")),
                **history_result,
                "diary_written": diary_written,
            },
        )
    except ScrapeError as exc:
        log.info("entry sync deferred account=%s: %s", account.id, exc)
    except Exception:  # noqa: BLE001 - opening the app must remain reliable
        log.warning("entry sync failed account=%s", account.id, exc_info=True)
    finally:
        _entry_sync_tasks.pop(account.id, None)


async def _schedule_entry_sync(account: Account, settings, service) -> str:
    """Queue one durable, throttled background refresh for an app entry."""
    if account.id in _entry_sync_tasks:
        return "running"

    # A member who entered through the temporary bio-verification fallback
    # has no bootstrap snapshot yet.  Do not let the lightweight entry pass
    # (which also needs Letterboxd) be their only chance to start importing:
    # create the durable full job first.  It checkpoints progress and retries
    # after an upstream block, whereas the entry pass is intentionally
    # best-effort and may be abandoned with the HTTP request.
    try:
        existing_job = await asyncio.to_thread(service.get_sync_job, account.id)
        if profile_sync.job_needs_full_sweep(existing_job):
            job = await profile_sync.ensure_started(
                _SyncPipeline(settings), service, account, scope="full"
            )
            if job:
                return "full_sync_queued"
    except Exception:  # noqa: BLE001 - app entry must remain available
        log.warning("entry full sync queue failed account=%s", account.id, exc_info=True)

    client, _cache = _make_cache(settings)
    pcache = _make_persistent_cache(settings, client)
    recent = await asyncio.to_thread(
        pcache.get, "entry_sync", account.username, ENTRY_SYNC_MIN_INTERVAL
    )
    if recent is not None:
        return "deferred"
    # Mark before scheduling so simultaneous tabs and PWA resumes share one
    # Letterboxd budget even if the task takes a few seconds to start.
    await asyncio.to_thread(
        pcache.set, "entry_sync", account.username, {"queued": True}
    )
    task = asyncio.create_task(_run_entry_sync(account, settings, service))
    _entry_sync_tasks[account.id] = task
    return "queued"


async def _stash_posters(films: list[dict]) -> None:
    """Promote public film metadata into the shared durable catalog."""
    rows = [
        {
            "slug": f.get("slug"),
            "poster_url": f.get("poster_url"),
            "poster_resolver_url": f.get("poster_resolver_url"),
            "tmdb_id": f.get("tmdb_id"),
            "title": f.get("title"),
            "release_year": f.get("release_year"),
            "overview": f.get("overview") or "",
            "director": f.get("director") or "",
            "genres": f.get("genres") or [],
            "keywords": f.get("keywords") or [],
            "vote_average": f.get("vote_average") or 0,
            "matched": bool(f.get("matched") or f.get("tmdb_id")),
            "details_loaded": bool(f.get("details_loaded")),
        }
        for f in films
        if f.get("slug")
    ]
    if not rows:
        return
    with contextlib.suppress(Exception):
        await asyncio.to_thread(_auth_service().save_film_posters, rows)


async def _resolve_favorite_posters(favorites, watched_rows, service, enricher) -> None:
    """Best-effort Fav 4 posters: watched rows → shared pool → a fresh enrich."""
    row_poster = {
        row.get("film_slug"): row.get("poster_url")
        for row in (watched_rows or [])
        if row.get("poster_url")
    }
    for fav in favorites:
        if not fav.poster_url and row_poster.get(fav.slug):
            fav.poster_url = row_poster[fav.slug]

    missing = [f for f in favorites if not f.poster_url and f.slug]
    if not missing:
        return
    with contextlib.suppress(Exception):
        pool = await asyncio.to_thread(
            service.get_film_posters, [f.slug for f in missing]
        )
        for fav in missing:
            if pool.get(fav.slug):
                fav.poster_url = pool[fav.slug]

    missing = [f for f in favorites if not f.poster_url and f.slug]
    if missing and enricher is not None:
        with contextlib.suppress(Exception):
            got = await enricher.enrich(
                [{"slug": f.slug, "title": f.title, "year": f.year} for f in missing],
                include_details=False,
            )
            by_slug = {g.slug: g for g in got if g.slug}
            for fav in missing:
                hit = by_slug.get(fav.slug)
                if hit:
                    fav.tmdb_id = fav.tmdb_id or hit.tmdb_id
                    if hit.poster_url:
                        fav.poster_url = hit.poster_url

    by_id = [f for f in favorites if not f.poster_url and f.tmdb_id]
    if by_id and enricher is not None:
        with contextlib.suppress(Exception):
            resolved = await enricher.posters_by_id([f.tmdb_id for f in by_id])
            for fav in by_id:
                if resolved.get(fav.tmdb_id):
                    fav.poster_url = resolved[fav.tmdb_id]

    await _stash_posters(
        [
            {
                "slug": f.slug,
                "poster_url": f.poster_url,
                "tmdb_id": f.tmdb_id,
                "title": f.title,
                "release_year": f.year,
            }
            for f in favorites
            if f.slug and f.poster_url
        ]
    )


async def _director_photo(name: str, enricher) -> str:
    """One shared director's portrait for a Blend, or "" when TMDb has none."""
    if not name or enricher is None:
        return ""
    with contextlib.suppress(Exception):
        photos = await enricher.person_photos([name])
        return photos.get(name) or ""
    return ""


async def _apply_director_photos(taste, enricher, service) -> None:
    """Fill top_directors_detail[].photo_url from TMDb person search (cached)."""
    detail = getattr(taste, "top_directors_detail", None) or []
    if not detail or enricher is None:
        return
    with contextlib.suppress(Exception):
        photos = await enricher.person_photos([d.get("name", "") for d in detail])
        for d in detail:
            if photos.get(d.get("name", "")):
                d["photo_url"] = photos[d["name"]]


class _SyncPipeline:
    """Glue the background runner (app.profile_sync) calls for the full sweep."""

    window_pages = profile_sync.WATCHED_WINDOW_PAGES

    def __init__(self, settings, *, use_stored_profile: bool = False):
        self.settings = settings
        self.use_stored_profile = use_stored_profile
        self._enricher_obj = None
        self._enricher_built = False

    def _enricher(self):
        if not self._enricher_built:
            self._enricher_built = True
            if self.settings.has_tmdb:
                _client, cache = _make_cache(self.settings)
                self._enricher_obj = Enricher(
                    self.settings.tmdb_api_key,
                    cache,
                    asset_store=_auth_service(),
                )
        return self._enricher_obj

    async def scrape_watched_window(
        self, username: str, start_page: int
    ) -> profile_sync.ScrapeWindow:
        result = await scrape_films(
            username,
            start_page=start_page,
            max_pages=self.window_pages,
            film_limit=self.window_pages * 80,
            max_retries=self.settings.scrape_max_retries,
        )
        return profile_sync.ScrapeWindow(
            films=[
                {
                    "slug": film.slug,
                    "title": film.title,
                    "year": film.year,
                    "user_rating": film.user_rating,
                    "poster_url": film.poster_url,
                    "poster_resolver_url": film.poster_resolver_url,
                }
                for film in result.films
                if film.slug
            ],
            next_page=result.next_page,
            exhausted=result.exhausted,
            complete=result.complete,
        )

    async def scrape_recent(self, username: str) -> list[dict]:
        films = await scrape_recent_watched(
            username, max_retries=self.settings.scrape_max_retries
        )
        return [
            {
                "slug": film.slug,
                "title": film.title,
                "year": film.year,
                "user_rating": film.user_rating,
                "poster_url": film.poster_url,
                "poster_resolver_url": film.poster_resolver_url,
            }
            for film in films
            if film.slug
        ]

    async def hydrate_catalog(self, films: list[dict]) -> list[dict]:
        """Merge durable shared metadata without making an external request."""
        if not films:
            return []
        service = _auth_service()
        try:
            assets = await asyncio.to_thread(
                service.get_film_assets,
                [film.get("slug") for film in films if film.get("slug")],
            )
        except Exception:
            assets = {}
        out: list[dict] = []
        for film in films:
            asset = assets.get(film.get("slug")) or {}
            out.append(
                {
                    "slug": film.get("slug") or "",
                    "title": asset.get("title") or film.get("title") or "",
                    "release_year": asset.get("release_year") or film.get("year"),
                    "tmdb_id": asset.get("tmdb_id"),
                    "overview": asset.get("overview") or "",
                    "director": asset.get("director") or "",
                    "genres": asset.get("genres") or [],
                    "keywords": asset.get("keywords") or [],
                    "vote_average": asset.get("vote_average") or 0,
                    "poster_url": asset.get("poster_url") or film.get("poster_url") or "",
                    "poster_resolver_url": film.get("poster_resolver_url") or asset.get("poster_resolver_url") or "",
                    "user_rating": film.get("user_rating"),
                    "watched_rank": film.get("watched_rank"),
                    "details_loaded": bool(asset.get("details_loaded")),
                }
            )
        return out

    async def enrich_search(self, films: list[dict]) -> list[dict]:
        enricher = self._enricher()
        if enricher is None:
            await resolve_missing_posters(films)
            return [
                {
                    "slug": film["slug"],
                    "title": film.get("title") or "",
                    "release_year": film.get("year"),
                    "user_rating": film.get("user_rating"),
                    "poster_url": film.get("poster_url") or "",
                    "watched_rank": film.get("watched_rank"),
                    "details_loaded": False,
                }
                for film in films
            ]
        enriched = await enricher.enrich(
            [
                {
                    "slug": film["slug"],
                    "title": film.get("title") or "",
                    "year": film.get("year"),
                    "user_rating": film.get("user_rating"),
                }
                for film in films
            ],
            include_details=False,
        )
        final_misses = [
            source
            for source, film in zip(films, enriched)
            if not film.poster_url and source.get("poster_resolver_url")
        ]
        if final_misses:
            await resolve_missing_posters(final_misses)
        out: list[dict] = []
        for src, ef in zip(films, enriched):
            out.append(
                {
                    "slug": src["slug"],
                    "title": ef.title or src.get("title") or "",
                    "release_year": ef.year or src.get("year"),
                    "tmdb_id": ef.tmdb_id,
                    "genres": ef.genres or [],
                    "overview": ef.overview or "",
                    "vote_average": ef.vote_average or 0,
                    "matched": ef.matched,
                    "poster_url": ef.poster_url or src.get("poster_url") or "",
                    "poster_resolver_url": src.get("poster_resolver_url") or "",
                    "user_rating": src.get("user_rating"),
                    "watched_rank": src.get("watched_rank"),
                    "details_loaded": False,
                }
            )
        await _stash_posters(out)
        return out

    async def enrich_details(self, rows: list[dict]) -> list[dict]:
        enricher = self._enricher()
        if enricher is None:
            return []
        # A full enrich (search + details) so this pass also fills poster_url /
        # tmdb_id for rows the search step missed, not just director/keywords.
        seeds = [
            {
                "slug": row.get("film_slug") or row.get("slug") or "",
                "title": row.get("title") or "",
                "year": row.get("release_year"),
                "poster_resolver_url": row.get("poster_resolver_url") or "",
            }
            for row in rows
            if row.get("film_slug") or row.get("slug")
        ]
        detailed = await enricher.enrich(seeds, include_details=True)
        final_misses = [
            source
            for source, film in zip(seeds, detailed)
            if not film.poster_url and source.get("poster_resolver_url")
        ]
        if final_misses:
            await resolve_missing_posters(final_misses)
            for source, film in zip(seeds, detailed):
                if not film.poster_url and source.get("poster_url"):
                    film.poster_url = source["poster_url"]
        resolver_by_slug = {
            source.get("slug"): source.get("poster_resolver_url") or ""
            for source in seeds
        }
        out = [
            {
                "slug": film.slug,
                "tmdb_id": film.tmdb_id,
                "title": film.title or "",
                "release_year": film.year,
                "director": film.director or "",
                "genres": film.genres or [],
                "keywords": film.keywords or [],
                "overview": film.overview or "",
                "vote_average": film.vote_average or 0,
                "matched": film.matched,
                "poster_url": film.poster_url or "",
                "poster_resolver_url": resolver_by_slug.get(film.slug, ""),
                "details_loaded": bool(film.details_loaded),
            }
            for film in detailed
            if film.slug
        ]
        await _stash_posters(out)
        return out

    async def rebuild_snapshot(
        self,
        account: Account,
        *,
        use_llm: bool = True,
        repair_all: bool = True,
        force_analysis: bool = False,
        locale: str | None = None,
    ) -> int:
        service = _auth_service()
        rows = await asyncio.to_thread(service.get_watched_films, account.id)
        rows.sort(
            key=lambda row: row["watched_rank"]
            if row.get("watched_rank") is not None
            else 10**9
        )
        watched = [
            EnrichedFilm(
                title=row.get("title") or "",
                year=row.get("release_year"),
                slug=row.get("film_slug") or "",
                tmdb_id=row.get("tmdb_id"),
                genres=row.get("genres") or [],
                director=row.get("director") or "",
                keywords=row.get("keywords") or [],
                poster_url=row.get("poster_url") or None,
                details_loaded=bool(row.get("details_loaded")),
                user_rating=row.get("user_rating"),
            )
            for row in rows
        ]
        enricher = self._enricher()
        # ── Poster repair ────────────────────────────────────────────────
        # 1) shared pool (free), 2) TMDb only for unresolved rows. Every
        # successful repair is written back to both the user row and shared pool.
        missing = [f for f in watched if not f.poster_url]
        if not repair_all:
            missing = _detail_sample(missing, 60)
        if missing:
            with contextlib.suppress(Exception):
                pool = await asyncio.to_thread(
                    service.get_film_posters, [f.slug for f in missing]
                )
                pool_patch = []
                for film in missing:
                    if pool.get(film.slug):
                        film.poster_url = pool[film.slug]
                        pool_patch.append(
                            {"slug": film.slug, "poster_url": film.poster_url}
                        )
                if pool_patch:
                    await asyncio.to_thread(
                        service.save_watched_films, account.id, pool_patch
                    )

        # 2) direct /movie/{id} for rows that already have a tmdb_id — no
        # search ambiguity, so this recovers the famous films a rate-limited
        # search pass dropped.
        repair_scope = watched if repair_all else _detail_sample(watched, 60)
        by_id = [f for f in repair_scope if not f.poster_url and f.tmdb_id]
        if by_id and enricher is not None:
            for offset in range(0, len(by_id), 250):
                with contextlib.suppress(Exception):
                    batch = by_id[offset : offset + 250]
                    resolved = await enricher.posters_by_id(
                        [f.tmdb_id for f in batch]
                    )
                    id_patch = []
                    for film in batch:
                        if resolved.get(film.tmdb_id):
                            film.poster_url = resolved[film.tmdb_id]
                            id_patch.append(
                                {
                                    "slug": film.slug,
                                    "poster_url": film.poster_url,
                                    "tmdb_id": film.tmdb_id,
                                    "title": film.title,
                                    "release_year": film.year,
                                }
                            )
                    if id_patch:
                        await asyncio.to_thread(
                            service.save_watched_films, account.id, id_patch
                        )

        if enricher is not None:
            need_poster = [film for film in repair_scope if not film.poster_url]
            for offset in range(0, len(need_poster), 150):
                with contextlib.suppress(Exception):
                    batch = need_poster[offset : offset + 150]
                    refreshed = await enricher.enrich(
                        [
                            {"slug": f.slug, "title": f.title, "year": f.year}
                            for f in batch
                        ],
                        include_details=False,
                    )
                    by_slug = {r.slug: r for r in refreshed if r.slug}
                    patch = []
                    for film in batch:
                        hit = by_slug.get(film.slug)
                        if hit and hit.poster_url:
                            film.poster_url = hit.poster_url
                            patch.append(
                                {
                                    "slug": film.slug,
                                    "poster_url": hit.poster_url,
                                    "tmdb_id": hit.tmdb_id,
                                    "title": hit.title,
                                    "release_year": hit.year,
                                }
                            )
                    if patch:
                        await asyncio.to_thread(
                            service.save_watched_films, account.id, patch
                        )

        stored_snapshot: dict = {}
        if self.use_stored_profile:
            stored_snapshot = await asyncio.to_thread(service.get_profile, account)
            stored_favorites = stored_snapshot.get("favorite_films") or []
            profile = ScrapedProfile(
                username=account.username,
                display_name=account.display_name or account.username,
                avatar_url=account.avatar_url or None,
                favorite_films=[
                    ScrapedFilm(
                        title=row.get("title") or "",
                        year=row.get("release_year"),
                        slug=row.get("slug") or "",
                        poster_url=row.get("poster_url") or None,
                    )
                    for row in stored_favorites
                    if row.get("slug") and row.get("title")
                ],
                stats=account.letterboxd_stats or {},
            )
        else:
            profile = await scrape_profile(
                account.username, max_retries=self.settings.scrape_max_retries
            )
        if enricher is not None:
            favorites = await enricher.enrich(
                profile.favorite_films, include_details=False
            )
        else:
            favorites = [
                EnrichedFilm(
                    title=film.title,
                    year=film.year,
                    slug=film.slug,
                    poster_url=film.poster_url,
                )
                for film in profile.favorite_films
            ]
        await _resolve_favorite_posters(favorites, rows, service, enricher)
        taste = build_taste_profile(watched, favorites)
        if account.preferred_locale == "en":
            genres = ", ".join(taste.top_genres[:2])
            director = taste.favorite_director
            if genres and director:
                taste.summary = f"A taste profile drawn to {genres}, with a clear affinity for {director}."
            elif genres:
                taste.summary = f"A viewing profile with a clear pull toward {genres}."
            elif director:
                taste.summary = f"A viewing profile with a clear affinity for {director}."
            else:
                taste.summary = "Your viewing history is saved; your taste profile will become richer as film details are completed."
        taste.source_fingerprint = taste_source_fingerprint(profile, watched)
        taste.personality = personality_from_favorites(favorites)
        await _apply_director_photos(taste, enricher, service)
        if not stored_snapshot:
            with contextlib.suppress(Exception):
                stored_snapshot = await asyncio.to_thread(service.get_profile, account)
        stored_taste = stored_snapshot.get("taste") or {}
        source_changed = (
            stored_taste.get("source_fingerprint") != taste.source_fingerprint
        )
        # Carrying the stored prose forward saves an LLM call when nothing about
        # the archive moved. A forced rebuild is the one case where it must not
        # happen: the reason for forcing is that the prose itself is stale — a
        # new algorithm version, or an edited Fav 4 — while the watched films
        # behind the fingerprint are unchanged. Inheriting here is what kept
        # members looking at their old analysis after every version bump.
        if not force_analysis and not source_changed and stored_taste.get("analysis"):
            taste.analysis = stored_taste["analysis"]

        # A full first snapshot gets an LLM pass. Later passes only refresh the
        # profile prose when its source changes; merely opening the app does not.
        # A repair/maintenance rebuild can deliberately opt out of external
        # prose generation. In that mode it must never send a member's film
        # history to the LLM merely because the stored fingerprint changed.
        should_analyze = use_llm and (
            force_analysis or source_changed or not stored_taste.get("analysis")
        )
        refresh_personality = _personality_refresh_needed(stored_snapshot, favorites)
        taste.analysis_source = "local"
        if should_analyze:
            with contextlib.suppress(Exception):
                extra = await analyze_taste(
                    self.settings,
                    taste_analysis_signal(watched, favorites),
                    favorites,
                    locale=locale
                    or ("en" if account.preferred_locale == "en" else "tr"),
                )
                if extra.get("analysis"):
                    taste.analysis = extra["analysis"]
                    taste.analysis_source = "llm"
                if (refresh_personality or force_analysis) and extra.get("personality"):
                    taste.personality = extra["personality"]
            if taste.analysis_source != "llm":
                # The deterministic lines are still current for this version, so
                # the member is never stuck on old text — but say so loudly,
                # because silent LLM failures used to be invisible here.
                log.warning(
                    "taste analysis fell back to local prose account=%s has_openai=%s",
                    account.id,
                    self.settings.has_openai,
                )
        if not refresh_personality and not force_analysis and stored_taste.get("personality"):
            taste.personality = stored_taste["personality"]
        await asyncio.to_thread(
            service.save_profile_snapshot, account, profile, favorites, taste
        )
        return len(watched)


async def _refresh_locale_taste(account: Account, locale: str) -> None:
    """Regenerate only account prose after a language change, off-request.

    The locale is resolved at the request, not here: a member who leaves the
    setting on "auto" has no stored preference to read, so the prose used to
    fall back to Turkish while the whole interface around it was English.
    """
    try:
        await _SyncPipeline(get_settings(), use_stored_profile=True).rebuild_snapshot(
            account,
            use_llm=True,
            repair_all=False,
            force_analysis=True,
            locale=locale,
        )
    except Exception:  # noqa: BLE001 - the existing data remains usable
        log.warning("locale taste refresh failed account=%s", account.id, exc_info=True)


async def _refresh_favorite_taste(
    account: Account, *, force_analysis: bool = False, locale: str | None = None
) -> None:
    """Regenerate the stored Fav 4 + director taste read off-request."""
    try:
        await _SyncPipeline(get_settings(), use_stored_profile=True).rebuild_snapshot(
            account,
            use_llm=True,
            repair_all=False,
            force_analysis=force_analysis,
            locale=locale,
        )
    except Exception:  # noqa: BLE001 - favourites are already safely persisted
        log.warning("favorite taste refresh failed account=%s", account.id, exc_info=True)


_favorite_taste_refresh_tasks: dict[int, asyncio.Task] = {}


def _schedule_favorite_taste_refresh(
    account: Account, *, force_analysis: bool = False, locale: str | None = None
) -> None:
    """Coalesce duplicate profile-page refreshes for the same account."""
    active = _favorite_taste_refresh_tasks.get(account.id)
    if active is not None and not active.done():
        return
    task = asyncio.create_task(
        _refresh_favorite_taste(account, force_analysis=force_analysis, locale=locale)
    )
    _favorite_taste_refresh_tasks[account.id] = task

    def _clear(completed: asyncio.Task) -> None:
        if _favorite_taste_refresh_tasks.get(account.id) is completed:
            _favorite_taste_refresh_tasks.pop(account.id, None)

    task.add_done_callback(_clear)


async def _refresh_profile_watchlist(account: Account, settings, service) -> int:
    """Force a complete watchlist read and replace its durable recommendation cache."""
    supabase_client, cache = _make_cache(settings)
    pcache = _make_persistent_cache(settings, supabase_client)
    enricher = (
        Enricher(settings.tmdb_api_key, cache, asset_store=service)
        if settings.has_tmdb else None
    )
    films, _cached = await _load_user_films(
        account.username,
        "watchlist",
        settings=settings,
        enricher=enricher,
        pcache=pcache,
        scrape_kwargs={
            "delay": settings.scrape_delay,
            "max_pages": settings.scrape_max_pages,
            "film_limit": settings.watchlist_film_limit,
            "max_retries": settings.scrape_max_retries,
        },
        force=True,
    )
    return len(films)


async def _refresh_profile_watchlist_background(account: Account, settings, service) -> None:
    """Keep an optional watchlist refresh off the fast Fav 4 response path."""
    try:
        await _refresh_profile_watchlist(account, settings, service)
    except Exception:  # noqa: BLE001 - the durable cache remains available
        log.warning("background watchlist refresh failed account=%s", account.id, exc_info=True)


def _watchlist_head_matches(cached_rows: list[dict], head: list[ScrapedFilm]) -> bool:
    """Compare the newest Letterboxd page without crawling the whole watchlist."""
    expected = [
        row.get("slug", "") for row in cached_rows[:FINGERPRINT_FILM_LIMIT]
    ]
    actual = [film.slug for film in head]
    return bool(expected) and actual == expected[:len(actual)] and len(actual) == len(expected)


async def _check_profile_watchlist_freshness(
    account: Account, settings, service, *, request: Request | None = None
) -> dict:
    """Check one cheap page and start a full refresh only when it is needed."""
    supabase_client, cache = _make_cache(settings)
    pcache = _make_persistent_cache(settings, supabase_client)
    recent_check = await asyncio.to_thread(
        pcache.get,
        "watchlist_head_check",
        account.username,
        WATCHLIST_HEAD_CHECK_MIN_INTERVAL,
    )
    if recent_check is not None:
        return {"status": "deferred", "changed": False}

    entry = await asyncio.to_thread(
        pcache.get_with_freshness,
        "films_watchlist",
        account.username,
        ttl=None,
    )
    cached_rows = (entry[0] if entry is not None else []) or []

    empty = False
    try:
        # A cached head check costs no Letterboxd request and must not consume
        # the member's heavy-request budget on every PWA resume.
        if request is not None:
            await _enforce_heavy_rate_limit(request)
        head, _complete = await scrape_watchlist(
            account.username,
            delay=0,
            max_pages=1,
            film_limit=FINGERPRINT_FILM_LIMIT,
            max_retries=settings.scrape_max_retries,
        )
    except EmptyListError:
        head = []
        empty = True

    # Persist the successful head check across tabs, browser restarts and Render
    # instances. Failed Letterboxd requests never reach here and remain retryable.
    await asyncio.to_thread(
        pcache.set,
        "watchlist_head_check",
        account.username,
        {"checked": True},
    )

    changed = entry is None or not _watchlist_head_matches(cached_rows, head)
    full_entry = await asyncio.to_thread(
        pcache.get_with_freshness,
        "films_full_refresh",
        f"watchlist:{account.username}",
        ttl=TTL_FULL_SCRAPE,
    )
    full_due = not bool(full_entry and full_entry[1])

    # An explicitly empty public watchlist is already a complete result; do not
    # start a second crawl that would raise the same EmptyListError.
    if empty:
        if changed or full_due:
            await asyncio.to_thread(
                pcache.set, "films_watchlist", account.username, []
            )
            await asyncio.to_thread(
                pcache.set,
                "films_full_refresh",
                f"watchlist:{account.username}",
                {"complete": True},
            )
        else:
            await asyncio.to_thread(
                pcache.touch, "films_watchlist", account.username
            )
        return {"status": "updated" if changed else "current", "changed": changed}

    if changed or full_due:
        enricher = (
            Enricher(settings.tmdb_api_key, cache, asset_store=service)
            if settings.has_tmdb else None
        )
        _task, joined = await _get_or_create_film_flight(
            account.username,
            "watchlist",
            enricher=enricher,
            pcache=pcache,
            scrape_kwargs={
                "delay": settings.scrape_delay,
                "max_pages": settings.scrape_max_pages,
                "film_limit": settings.watchlist_film_limit,
                "max_retries": settings.scrape_max_retries,
            },
        )
        log.warning(
            "watchlist head %s %s (changed=%s full_due=%s)",
            "joined" if joined else "refresh started",
            account.username,
            changed,
            full_due,
        )
        return {"status": "refreshing", "changed": changed}

    await asyncio.to_thread(pcache.touch, "films_watchlist", account.username)
    return {"status": "current", "changed": False}


@app.post("/api/profile/watchlist/check")
async def check_my_watchlist(request: Request) -> dict:
    """Non-blocking entry check: one Letterboxd page, full crawl only on change."""
    _require_csrf(request)
    account = await _require_account(request)
    service = _auth_service()
    try:
        result = await _check_profile_watchlist_freshness(
            account, get_settings(), service, request=request
        )
        await _record_activity_event(
            service,
            account,
            "watchlist_checked",
            {"status": result.get("status", ""), "changed": bool(result.get("changed"))},
        )
        return result
    except ScrapeError as exc:
        _raise_scrape_http(exc)


@app.post("/api/profile/favorites/check")
async def check_my_profile_favorites(request: Request) -> dict:
    """Quickly reflect Fav 4 edits without scheduling a full Letterboxd crawl."""
    _require_csrf(request)
    account = await _require_account(request)
    service = _auth_service()
    try:
        result = await _refresh_profile_favorites(account, get_settings(), service)
        await _record_activity_event(
            service, account, "favorites_checked", {"changed": result["changed"]}
        )
        return result
    except ScrapeError as exc:
        _raise_scrape_http(exc)


@app.post("/api/profile/entry-sync")
async def sync_profile_on_entry(request: Request) -> dict:
    """Queue a bounded freshness check without delaying the app shell."""
    _require_csrf(request)
    account = await _require_account(request)
    status = await _schedule_entry_sync(account, get_settings(), _auth_service())
    return {"status": status}


@app.post("/api/profile/sync")
async def sync_my_profile(
    request: Request,
    force: bool = False,
    refresh_watchlist: bool = False,
) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    settings = get_settings()
    service = _auth_service()
    await _record_activity_event(
        service,
        account,
        "profile_sync_requested",
        {"force": bool(force), "refresh_watchlist": bool(refresh_watchlist)},
    )
    try:
        await asyncio.to_thread(service.check_schema)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Profil veri şeması güncelleniyor. Lütfen biraz sonra tekrar dene.",
        ) from exc

    full_sync_available = True
    try:
        await asyncio.to_thread(service.check_sync_schema)
    except Exception:
        full_sync_available = False

    locale = _response_locale(account, request)

    # Once film rows exist, never regress to the empty in-request bootstrap.
    # An incremental job can fail with scope="incremental" after Letterboxd
    # blocks a request, even though the completed archive is safely stored.
    if full_sync_available and not force:
        job = await asyncio.to_thread(service.get_sync_job, account.id)
        stored_count = await asyncio.to_thread(service.count_watched_films, account.id)
        if stored_count:
            # A completed diary crawl must not freeze Fav 4 forever. This is
            # only one profile-page request, not another history scrape.
            refreshed = await _refresh_profile_favorites(
                account, settings, service, locale=locale
            )
            stored = refreshed["profile"]
            # A snapshot written by an older algorithm is stale prose, however
            # unchanged the archive behind it is. Rebuild it asynchronously:
            # opening a profile must remain instant and never trigger a crawl.
            if (stored.get("taste") or {}).get("algorithm_version") != TASTE_PROFILE_VERSION:
                _schedule_favorite_taste_refresh(
                    account, force_analysis=True, locale=locale
                )
                stored["taste_refreshing"] = True
            if refresh_watchlist:
                asyncio.create_task(
                    _refresh_profile_watchlist_background(account, settings, service)
                )
                stored["watchlist_refreshing"] = True
            # Do not launch a daily incremental crawl from a button press or
            # a returning-device bootstrap. The archive remains available if
            # Letterboxd is rate-limiting us; a future explicit refresh can
            # safely schedule work after the upstream recovers.
            stored["sync_job"] = profile_sync.progress_of(job)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(service.mark_sync_status, account.id, "ready")
            return stored

    await _enforce_heavy_rate_limit(request)
    refreshed_watchlist_count: int | None = None
    if refresh_watchlist:
        try:
            refreshed_watchlist_count = await _refresh_profile_watchlist(
                account, settings, service
            )
        except ScrapeError as exc:
            _raise_scrape_http(exc)

    try:
        result = await _provisional_profile_sync(account, settings, service, force=force)
    except AccessBlockedError as exc:
        # The fast Fav 4 bootstrap is allowed to fail without keeping the
        # member out of the app.  It must still leave behind a durable full
        # import job; otherwise a user who chooses "continue" has no later
        # path for their films and favorites to arrive.
        if full_sync_available:
            with contextlib.suppress(Exception):
                await profile_sync.ensure_started(
                    _SyncPipeline(settings), service, account, scope="full", force=force
                )
        _raise_scrape_http(exc)
    if refreshed_watchlist_count is not None:
        result["watchlist_count"] = refreshed_watchlist_count

    if full_sync_available:
        with contextlib.suppress(Exception):
            job = await profile_sync.ensure_started(
                _SyncPipeline(settings), service, account, scope="full", force=force
            )
            result["sync_job"] = profile_sync.progress_of(job)
    return result


# Posting is meant to feel immediate, so the ceiling only exists to stop scripts
# and runaway clients — it is nowhere near what a person types in a day.
_post_rate_limiter = SlidingWindowRateLimiter(
    limit=40, window_seconds=24 * 60 * 60, burst=6, burst_seconds=60,
)


async def _enforce_post_rate_limit(request: Request) -> None:
    allowed, retry_after = await _post_rate_limiter.check(_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Bugünlük not sınırına ulaştın. Yarın devam edebilirsin.",
            headers={"Retry-After": str(retry_after)},
        )


def _raise_post_http(exc: BlendServiceError) -> None:
    mapping = {
        "post_film_required": (422, "Not yazmadan önce bir film seçmelisin."),
        "post_film_not_owned": (422, "Yalnızca izlediğin bir film hakkında not yazabilirsin."),
        "post_not_found": (404, "Bu not bulunamadı."),
        "post_already_reported": (409, "Bu notu zaten bildirdin."),
        "post_failed": (503, "Not kaydedilemedi. Lütfen tekrar dene."),
        "user_not_found": (404, "Bu kullanıcı bulunamadı."),
        "self_follow": (422, "Kendini takip edemezsin."),
        "follow_request_not_found": (404, "Takip isteği bulunamadı."),
        "blend_follow_required": (403, "Blend için karşılıklı takip gerekiyor."),
        "invalid_cursor": (422, "Geçersiz akış sayfalaması."),
    }
    status, detail = mapping.get(str(exc), (400, "İşlem tamamlanamadı."))
    raise HTTPException(status_code=status, detail=detail)


_diary_ingest_task: asyncio.Task | None = None


_diary_backfill_task: asyncio.Task | None = None


def _kick_diary_backfill(settings, service) -> None:
    """Yeni üyenin arşivini arka planda, yeniden eskiye doğru tarar.

    Onboarding'e bağlanmıyor: kayıt akışı yüzlerce sayfalık bir taramayı
    bekleyemez. Üye uygulamaya girdiğinde tetikleniyor ve koş başına birkaç
    sayfa ilerliyor, böylece profil dolarken görünüyor. Nerede kalındığı
    `diary_backfill_page` içinde; süreç yeniden başlasa da kaldığı yerden devam
    ediyor.

    Çağıran beklemiyor — tek bir görev, süreç başına.
    """
    global _diary_backfill_task
    if not getattr(settings, "diary_scan_enabled", True):
        return
    if _diary_backfill_task is not None and not _diary_backfill_task.done():
        return

    async def _run():
        try:
            members = await asyncio.to_thread(
                service.diary_backfill_candidates,
                limit=int(getattr(settings, "diary_backfill_members_per_run", 2)),
            )
            pages = max(1, int(getattr(settings, "diary_backfill_pages_per_run", 3)))
            for member in members:
                username = member.get("username") or ""
                if not username:
                    continue
                done_pages = int(member.get("diary_backfill_page") or 0)
                progress: dict = {}
                entries = await scrape_reviewed_diary(
                    username, start_page=done_pages + 1, max_pages=pages,
                    progress=progress,
                )
                if entries is None and not progress.get("last_page"):
                    # Okunamadı: ilerleme yazmıyoruz, sonraki koş yine dener.
                    continue
                rows = [
                    {
                        "source_key": entry.key,
                        "film_slug": entry.slug,
                        "film_title": entry.title,
                        "film_year": entry.year,
                        "tmdb_id": entry.tmdb_id,
                        "body": entry.review,
                        "payload": {
                            "rating": entry.rating,
                            "rewatch": entry.rewatch,
                            "watched_on": entry.watched_on,
                        },
                        "created_at": f"{entry.watched_on}T12:00:00+00:00",
                    }
                    for entry in (entries or []) if entry.slug and entry.review.strip()
                ]
                if rows:
                    await asyncio.to_thread(
                        service.import_diary_entries, int(member["id"]), rows
                    )
                await asyncio.to_thread(
                    service.mark_diary_backfill, int(member["id"]),
                    page=int(progress.get("last_page") or 0),
                    done=bool(progress.get("exhausted")),
                )
                log.warning(
                    "diary backfill user=%s pages=%s..%s rows=%d done=%s",
                    username, done_pages + 1, progress.get("last_page"),
                    len(rows), bool(progress.get("exhausted")),
                )
                await asyncio.sleep(4)
        except Exception as exc:  # noqa: BLE001 - besleme çağırana sızmamalı
            log.warning("diary backfill failed: %s", exc)

    _diary_backfill_task = asyncio.create_task(_run())


async def _diary_refresh_loop() -> None:
    """Yeni yazılan günce kayıtlarını saatte bir akışa düşürür.

    Ayrı bir işçi süreç yok; UptimeRobot servisi ayakta tuttuğu için bu hafif
    tik de dönmeye devam ediyor. Koş başına iş miktarı `diary_scan_*` ayarlarıyla
    sınırlı, dolayısıyla üye sayısı büyüdüğünde Letterboxd'a giden yük artmıyor.
    """
    settings = get_settings()
    service = _auth_service()
    while True:
        _kick_diary_ingest(settings, service)
        # Arşiv taraması ayrı bir görev: kimse uygulamaya girmese de ilerlesin.
        _kick_diary_backfill(settings, service)
        await asyncio.sleep(60 * 60)


def _kick_diary_ingest(settings, service) -> None:
    """Sırası gelen üyelerin yeni yorumlarını akışa düşürür.

    Üye başına tek istek: `/films/reviews/` ilk sayfası en yeni on iki yorumlu
    kaydı veriyor, ondan en yeni `diary_scan_entries` tanesi alınıyor. Kaydı
    üçe indirmek isteği ucuzlatmıyor — asıl maliyet koştaki üye sayısı, onu da
    `diary_scan_members_per_run` sınırlıyor.

    RSS yerine bu sayfa kullanılıyor çünkü RSS son ~50 *izleme* kaydını
    taşıyor: art arda elli film yorumsuz loglanırsa yeni yazılan yorum oradan
    görünmez oluyor. Sayfa yalnızca yorumluları listeliyor.
    """
    global _diary_ingest_task
    if not getattr(settings, "diary_scan_enabled", True):
        return
    if _diary_ingest_task is not None and not _diary_ingest_task.done():
        return

    async def _run():
        try:
            members = await asyncio.to_thread(
                service.diary_sync_candidates,
                limit=int(getattr(settings, "diary_scan_members_per_run", 20)),
                min_hours=int(getattr(settings, "diary_scan_min_hours", 1)),
                max_hours=int(getattr(settings, "diary_scan_max_hours", 24)),
            )
            keep = max(1, int(getattr(settings, "diary_scan_entries", 3)))
            for member in members:
                username = member.get("username") or ""
                if not username:
                    continue
                entries = await scrape_reviewed_diary(username, max_pages=1)
                # `None` okunamadı demek: damgayı atmıyoruz ki sıradaki koşuda
                # yine denenebilsin ve geri çekilme sayacı bozulmasın.
                if entries is None:
                    continue
                newest = sorted(
                    (entry for entry in entries if entry.slug and entry.review.strip()),
                    key=lambda entry: entry.watched_on,
                    reverse=True,
                )[:keep]
                rows = [
                    {
                        "source_key": entry.key,
                        "film_slug": entry.slug,
                        "film_title": entry.title,
                        "film_year": entry.year,
                        "tmdb_id": entry.tmdb_id,
                        "body": entry.review,
                        "payload": {
                            "rating": entry.rating,
                            "rewatch": entry.rewatch,
                            "watched_on": entry.watched_on,
                        },
                        # Akış sırası izlenme gününe göre.
                        "created_at": f"{entry.watched_on}T12:00:00+00:00",
                    }
                    for entry in newest
                ]
                written = await asyncio.to_thread(
                    service.import_diary_entries, int(member["id"]), rows
                )
                await asyncio.to_thread(
                    service.mark_diary_synced, int(member["id"]), wrote=written
                )
                if written:
                    log.warning("diary import user=%s rows=%d", username, written)
                await asyncio.sleep(4)
        except Exception as exc:  # noqa: BLE001 - besleme çağırana sızmamalı
            log.warning("diary ingest failed: %s", exc)

    _diary_ingest_task = asyncio.create_task(_run())


@app.get("/api/feed")
async def read_feed(
    request: Request, scope: str = "community", cursor: str = "", film: str = "", author: str = "", sort: str = "",
) -> dict:
    account = await _require_account(request)
    service = _auth_service()
    scope = scope if scope in ("community", "following", "mine") else "community"
    try:
        feed_sort = "engagement" if scope == "community" and sort != "recent" else "recent"
        feed = await asyncio.to_thread(
            service.list_feed, account, scope=scope, cursor=cursor, limit=20,
            film_slug=film.strip()[:120], author_username=author.strip()[:80], sort=feed_sort,
        )
    except BlendServiceError as exc:
        _raise_post_http(exc)
    except TransientStorageError as exc:
        # Better an honest "tekrar dene" than an empty timeline that reads as
        # "nobody has written anything".
        raise HTTPException(
            status_code=503, detail="Akış şu an yüklenemedi. Tekrar dene.",
        ) from exc
    # Uygulamaya girmiş bir üye var: arşivi taranmamış birinin sırası
    # ilerlesin. Arka planda, yanıtı bekletmeden.
    _kick_diary_backfill(get_settings(), service)
    return {
        "scope": scope,
        "film": film.strip()[:120],
        "author": author.strip()[:80],
        "sort": feed_sort,
        "posts": feed["posts"],
        # Composite keyset: timestamp ties never duplicate or hide a note.
        "next_cursor": feed["next_cursor"],
    }


@app.get("/api/feed/films")
async def search_feed_films(request: Request, q: str = "") -> dict:
    account = await _require_account(request)
    films = await asyncio.to_thread(_auth_service().search_feed_films, account, q.strip()[:80])
    return {"films": films}


@app.get("/api/films/{film_slug}")
async def film_page(film_slug: str, request: Request) -> dict:
    """Bir filmin topluluk künyesi: kaç üye izlemiş, ortalama puan, perdede mi.

    Notların kendisi `/api/feed?film=` üzerinden geliyor; burada yalnız o
    listenin başına konacak künye var.
    """
    account = await _require_account(request)
    service = _auth_service()
    slug = film_slug.strip().lower()[:120]
    if not slug:
        raise HTTPException(status_code=404, detail="Film bulunamadı.")
    catalog, stats = await asyncio.gather(
        asyncio.to_thread(service.film_catalog_entry, slug),
        asyncio.to_thread(service.film_overview_for_community, slug),
    )
    # "Bu hafta perdede mi" — bülteni zaten tuttuğumuz gösterimlerden okuyoruz.
    venues: list[dict] = []
    if get_settings().bulletin_enabled:
        try:
            rows = await asyncio.to_thread(lambda: service.list_screenings(limit=400))
            venues = [
                {
                    "name": row.get("venue_name") or "",
                    "url": row.get("url") or "",
                    "film_page": bool(row.get("url"))
                    and row.get("url") != row.get("venue_source_url"),
                }
                for row in rows if (row.get("film_slug") or "") == slug
            ]
        except Exception:  # noqa: BLE001 - künye bültensiz de anlamlı
            venues = []
    mine = await asyncio.to_thread(service.watched_film_for, account, slug)
    return {
        "film": {
            "slug": slug,
            "title": catalog.get("title") or "",
            "year": catalog.get("release_year"),
            "poster_url": catalog.get("poster_url") or "",
            "director": catalog.get("director") or "",
        },
        "community": stats,
        "venues": venues,
        "mine": mine or {},
    }


@app.post("/api/posts")
async def create_post(req: PostRequest, request: Request) -> dict:
    _require_csrf(request)
    await _enforce_post_rate_limit(request)
    account = await _require_account(request)
    service = _auth_service()
    if account.profile_sync_status not in ("ready", "stale"):
        # Raising the cost of a throwaway account: a profile has to exist first.
        raise HTTPException(
            status_code=409,
            detail="Profilin hazırlanınca not yazabilirsin.",
        )
    try:
        post = await asyncio.to_thread(service.create_post, account, req.model_dump())
    except BlendServiceError as exc:
        _raise_post_http(exc)
    await _record_activity_event(service, account, "post_created", {"spoiler": req.spoiler})
    return {"ok": True, "post": post}


@app.get("/api/posts/{post_id}")
async def read_post(post_id: str, request: Request) -> dict:
    account = await _require_account(request)
    thread = await asyncio.to_thread(_auth_service().get_post_thread, account, post_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Bu not bulunamadı.")
    return thread


@app.post("/api/posts/{post_id}/replies")
async def reply_to_post(post_id: str, req: ReplyRequest, request: Request) -> dict:
    _require_csrf(request)
    await _enforce_post_rate_limit(request)
    account = await _require_account(request)
    service = _auth_service()
    parent = await asyncio.to_thread(service.get_post_thread, account, post_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Bu not bulunamadı.")
    try:
        reply = await asyncio.to_thread(
            service.create_post, account,
            {"body": req.body, "reply_to": post_id},
        )
    except BlendServiceError as exc:
        _raise_post_http(exc)
    author_id = int(parent["post"]["author_id"])
    if author_id != account.id:
        await asyncio.to_thread(
            service.notify, author_id, "reply", account.id, post_id,
            event_key=f"reply:{reply['id']}",
        )
    return {"ok": True, "reply": reply}


@app.delete("/api/posts/{post_id}")
async def delete_post(post_id: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    removed = await asyncio.to_thread(_auth_service().delete_post, account, post_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Bu not bulunamadı.")
    return {"ok": True}


@app.post("/api/posts/{post_id}/like")
async def like_post(post_id: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        count = await asyncio.to_thread(
            _auth_service().set_post_like, account, post_id, True
        )
    except BlendServiceError as exc:
        _raise_post_http(exc)
    return {"ok": True, "like_count": count, "liked": True}


@app.delete("/api/posts/{post_id}/like")
async def unlike_post(post_id: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        count = await asyncio.to_thread(
            _auth_service().set_post_like, account, post_id, False
        )
    except BlendServiceError as exc:
        _raise_post_http(exc)
    return {"ok": True, "like_count": count, "liked": False}


@app.post("/api/posts/{post_id}/report")
async def report_post(post_id: str, req: ReportUserRequest, request: Request) -> dict:
    _require_csrf(request)
    await _enforce_member_action_limit(request)
    account = await _require_account(request)
    try:
        report_id = await asyncio.to_thread(
            _auth_service().report_post, account, post_id, req.category, req.detail
        )
    except BlendServiceError as exc:
        _raise_post_http(exc)
    return {"ok": True, "report_id": report_id}


@app.get("/api/feed/trending")
async def trending_films(request: Request) -> dict:
    await _require_account(request)
    films = await asyncio.to_thread(_auth_service().trending_films)
    return {"films": films}


@app.post("/api/users/{username}/follow")
async def follow_user(username: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        result = await asyncio.to_thread(
            _auth_service().set_follow, account, _normalize_username(username), True
        )
    except BlendServiceError as exc:
        _raise_post_http(exc)
    return {"ok": True, **result}


@app.delete("/api/users/{username}/follow")
async def unfollow_user(username: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        result = await asyncio.to_thread(
            _auth_service().set_follow, account, _normalize_username(username), False
        )
    except BlendServiceError as exc:
        _raise_post_http(exc)
    return {"ok": True, **result}


@app.post("/api/users/{username}/follow-request")
async def decide_follow_request(
    username: str, req: FollowRequestDecision, request: Request
) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        result = await asyncio.to_thread(
            _auth_service().decide_follow_request,
            account, _normalize_username(username), req.decision == "accepted",
        )
    except BlendServiceError as exc:
        _raise_post_http(exc)
    return {"ok": True, **result}


@app.get("/api/notifications")
async def read_notifications(request: Request) -> dict:
    account = await _require_account(request)
    service = _auth_service()
    # Clearing the badge does not depend on the list, and the member is about
    # to see everything in it either way, so the two round trips overlap
    # instead of queueing.
    items, _ = await asyncio.gather(
        asyncio.to_thread(service.list_notifications, account),
        asyncio.to_thread(service.mark_notifications_read, account),
    )
    return {"notifications": items}


@app.get("/api/notifications/unread-count")
async def unread_notifications(request: Request) -> dict:
    account = await _require_account(request)
    count = await asyncio.to_thread(_auth_service().unread_notification_count, account)
    return {"count": count}


@app.get("/api/users/search")
async def search_registered_users(q: str, request: Request) -> dict:
    account = await _require_account(request)
    try:
        query = _normalize_username(q)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    users = await asyncio.to_thread(
        _auth_service().search_accounts, account, query
    )
    return {"users": users}


def _clean_username(value: str) -> str:
    try:
        return _normalize_username(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Declared after /api/users/search so the literal path keeps winning the match.
@app.get("/api/users/{username}")
async def read_user_page(username: str, request: Request, cursor: str = "") -> dict:
    account = await _require_account(request)
    service = _auth_service()
    profile = await asyncio.to_thread(
        service.public_profile, account, _clean_username(username)
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Bu sinefil bulunamadı.")
    timeline = await asyncio.to_thread(
        service.list_user_posts, account, profile["id"], cursor=cursor, limit=20
    )
    return {
        "profile": profile,
        "posts": timeline["posts"],
        "next_cursor": timeline["next_cursor"],
    }


@app.get("/api/users/{username}/followers")
async def read_followers(username: str, request: Request) -> dict:
    return await _follow_list(username, request, "followers")


@app.get("/api/users/{username}/following")
async def read_following(username: str, request: Request) -> dict:
    return await _follow_list(username, request, "following")


async def _follow_list(username: str, request: Request, kind: str) -> dict:
    account = await _require_account(request)
    service = _auth_service()
    profile = await asyncio.to_thread(
        service.public_profile, account, _clean_username(username)
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Bu sinefil bulunamadı.")
    # Follow counts are public, but member names are private relationship
    # data. Even accepted followers cannot enumerate somebody else's graph.
    if profile["id"] != account.id:
        raise HTTPException(status_code=403, detail="Takip listeleri yalnızca hesap sahibine açık.")
    users = await asyncio.to_thread(
        service.list_follow_list, account, profile["id"], kind=kind
    )
    return {"kind": kind, "profile": profile, "users": users}


@app.post("/api/users/{username}/block")
async def block_registered_user(username: str, request: Request) -> dict:
    _require_csrf(request)
    await _enforce_member_action_limit(request)
    account = await _require_account(request)
    try:
        normalized = _normalize_username(username)
        await asyncio.to_thread(_auth_service().block_user, account, normalized)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    return {"ok": True, "username": normalized, "blocked": True}


@app.delete("/api/users/{username}/block")
async def unblock_registered_user(username: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        normalized = _normalize_username(username)
        await asyncio.to_thread(_auth_service().unblock_user, account, normalized)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    return {"ok": True, "username": normalized, "blocked": False}


@app.post("/api/users/{username}/report")
async def report_registered_user(
    username: str, req: ReportUserRequest, request: Request
) -> dict:
    _require_csrf(request)
    await _enforce_member_action_limit(request)
    account = await _require_account(request)
    try:
        normalized = _normalize_username(username)
        report_id = await asyncio.to_thread(
            _auth_service().report_user,
            account,
            normalized,
            req.category,
            req.detail,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    return {"ok": True, "report_id": report_id}


@app.post("/api/blends/requests")
async def create_blend_invite(req: CreateBlendRequest, request: Request) -> dict:
    _require_csrf(request)
    await _enforce_member_action_limit(request)
    account = await _require_account(request)
    service = _auth_service()
    existing = await asyncio.to_thread(
        service.find_blend_relation, account, req.recipient_username
    )
    if existing:
        return {
            "ok": True,
            "existing": True,
            "recipient_username": req.recipient_username,
            **existing,
        }
    try:
        request_id = await asyncio.to_thread(
            service.create_blend_request,
            account,
            req.recipient_username,
            ip_hash=_ip_hash(request),
        )
    except BlendServiceError as exc:
        if str(exc) in {"blend_request_exists", "blend_already_accepted"}:
            existing = await asyncio.to_thread(
                service.find_blend_relation, account, req.recipient_username
            )
            if existing:
                return {
                    "ok": True,
                    "existing": True,
                    "recipient_username": req.recipient_username,
                    **existing,
                }
        _raise_blend_http(exc)
    return {
        "ok": True,
        "request_id": request_id,
        "recipient_username": req.recipient_username,
        "status": "pending",
        "existing": False,
    }


@app.get("/api/blends")
async def list_my_blends(request: Request) -> dict:
    account = await _require_account(request)
    return await asyncio.to_thread(_auth_service().list_blends, account)


@app.get("/api/blends/pending-count")
async def pending_blend_count(request: Request) -> dict:
    """Small unified inbox badge: pending Blends plus unread encrypted letters."""
    account = await _require_account(request)
    blend_count, letter_count = await asyncio.gather(
        asyncio.to_thread(_auth_service().count_pending_blend_requests, account),
        asyncio.to_thread(_auth_service().count_unread_letters, account),
    )
    return {"count": blend_count + letter_count, "blend_count": blend_count, "letter_count": letter_count}


@app.delete("/api/blends/requests/{request_id}")
async def cancel_blend_invite(request_id: str, request: Request) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    try:
        await asyncio.to_thread(
            _auth_service().cancel_blend_request, account, request_id
        )
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    return {"ok": True, "request_id": request_id, "status": "cancelled"}


async def _compute_accepted_blend(
    account: Account,
    request_id: str,
    service: AuthService,
    *,
    force_recompute: bool = False,
) -> dict:
    _request, first, second = await asyncio.to_thread(
        service.get_blend_participants, account, request_id
    )
    stored = await asyncio.to_thread(service.get_blend_result, request_id)
    if (
        not force_recompute
        and stored is not None
        and stored.get("algorithm_version") == BLEND_VERSION
    ):
        if (stored.get("result") or {}).get("watchlist_pending"):
            _schedule_blend_watchlist_completion(account, request_id, service)
        return {
            **stored["result"],
            "avatar_url1": first.avatar_url or "",
            "avatar_url2": second.avatar_url or "",
            "request_id": request_id,
            "cached": True,
        }
    settings = get_settings()
    _supabase_client, cache = _make_cache(settings)
    enricher = (
        Enricher(settings.tmdb_api_key, cache, asset_store=service)
        if settings.has_tmdb else None
    )
    rows1, rows2 = await asyncio.gather(
        asyncio.to_thread(service.get_watched_films, first.id),
        asyncio.to_thread(service.get_watched_films, second.id),
    )
    watched1 = _enriched_from_watched_rows(rows1)
    watched2 = _enriched_from_watched_rows(rows2)
    if not watched1 or not watched2:
        raise ScrapeError("Blend için iki profil senkronunun da tamamlanması gerekli.")
    if enricher is not None:
        watched1, watched2 = await asyncio.gather(
            _complete_blend_profile_metadata(watched1, first.id, enricher, service),
            _complete_blend_profile_metadata(watched2, second.id, enricher, service),
        )
    async def _preference_slugs(participant: Account) -> list[str]:
        favorite_four: list[str] = []
        if hasattr(service, "get_profile"):
            with contextlib.suppress(Exception):
                profile = await asyncio.to_thread(service.get_profile, participant)
                favorite_four = [
                    film.get("slug") for film in profile.get("favorite_films", [])
                    if film.get("slug")
                ][:4]
        return favorite_four

    favorite_four1, favorite_four2 = (
        await asyncio.gather(_preference_slugs(first), _preference_slugs(second))
    )
    blend_result = _calculate_blend(
        watched1,
        watched2,
        top_n=10,
        favorite_four1=favorite_four1,
        favorite_four2=favorite_four2,
    )
    payload = {
        "username1": first.username,
        "username2": second.username,
        "avatar_url1": first.avatar_url or "",
        "avatar_url2": second.avatar_url or "",
        "score": blend_result["score"],
        "confidence": blend_result["confidence"],
        "watched_count1": len(watched1),
        "watched_count2": len(watched2),
        "common_count": blend_result["common_count"],
        "top_director": blend_result["top_director"],
        "top_director_photo": await _director_photo(
            blend_result["top_director"], enricher
        ),
        "top_director_count1": blend_result["top_director_count1"],
        "top_director_count2": blend_result["top_director_count2"],
        "films": [
            {
                **film.to_dict(),
                **blend_result["film_preferences"].get(
                    film.slug or f"{film.title.lower().strip()}:{film.year}", {}
                ),
            }
            for film in blend_result["films"]
        ],
        "favorite_matches": blend_result["favorite_matches"],
        "common_watchlist_films": [],
        "bridge_films": [],
        "watchlist_public": False,
        "watchlist_pending": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    result_id = await asyncio.to_thread(
        service.save_blend_result,
        account,
        request_id,
        payload,
        algorithm_version=BLEND_VERSION,
    )
    await _record_activity_event(
        service,
        account,
        "blend_result_computed",
        {
            "score": int(payload.get("score") or 0),
            "common_count": int(payload.get("common_count") or 0),
            "watchlist_pending": True,
        },
    )
    response = {
        **payload,
        "request_id": request_id,
        "result_id": result_id,
        "cached": False,
    }
    _schedule_blend_watchlist_completion(account, request_id, service)
    return response


async def _complete_blend_profile_metadata(
    films: list[EnrichedFilm],
    user_id: int,
    enricher: Enricher,
    service: AuthService,
) -> list[EnrichedFilm]:
    """Hydrate every incomplete Blend row, reusing the shared catalog first."""
    missing = [film for film in films if not film.details_loaded]
    if not missing:
        return films
    completed = await enricher.enrich(missing, include_details=True)
    by_slug = {film.slug: film for film in completed if film.slug}
    merged = [by_slug.get(film.slug, film) for film in films]
    rows = [
        {
            "slug": film.slug,
            "title": film.title,
            "release_year": film.year,
            "tmdb_id": film.tmdb_id,
            "director": film.director,
            "genres": film.genres,
            "keywords": film.keywords,
            "poster_url": film.poster_url or "",
            "overview": film.overview,
            "vote_average": film.vote_average,
            "matched": film.matched,
            "details_loaded": film.details_loaded,
        }
        for film in completed
        if film.slug
    ]
    if rows:
        await asyncio.to_thread(service.save_watched_films, user_id, rows)
    return merged


_blend_watchlist_tasks: dict[str, asyncio.Task] = {}


def _schedule_blend_watchlist_completion(
    account: Account, request_id: str, service: AuthService
) -> None:
    current = _blend_watchlist_tasks.get(request_id)
    if current and not current.done():
        return
    task = asyncio.create_task(
        _complete_blend_watchlists(account, request_id, service)
    )
    _blend_watchlist_tasks[request_id] = task

    def _clear(done_task, key=request_id):
        if _blend_watchlist_tasks.get(key) is done_task:
            _blend_watchlist_tasks.pop(key, None)
        with contextlib.suppress(asyncio.CancelledError):
            exc = done_task.exception()
            if exc:
                log.warning("blend watchlist completion failed request=%s: %s", key, exc)

    task.add_done_callback(_clear)


async def _complete_blend_watchlists(
    account: Account, request_id: str, service: AuthService
) -> None:
    stored = await asyncio.to_thread(service.get_blend_result, request_id)
    if not stored or not (stored.get("result") or {}).get("watchlist_pending"):
        return
    _request, first, second = await asyncio.to_thread(
        service.get_blend_participants, account, request_id
    )
    settings = get_settings()
    supabase_client, cache = _make_cache(settings)
    pcache = _make_persistent_cache(settings, supabase_client)
    enricher = (
        Enricher(settings.tmdb_api_key, cache, asset_store=service)
        if settings.has_tmdb else None
    )
    watchlist_kwargs = {
        "delay": settings.scrape_delay,
        "max_pages": settings.scrape_max_pages,
        "film_limit": settings.watchlist_film_limit,
        "max_retries": settings.scrape_max_retries,
    }

    async def load_watchlist(username: str):
        try:
            return await _load_user_films(
                username,
                "watchlist",
                settings=settings,
                enricher=enricher,
                pcache=pcache,
                scrape_kwargs=watchlist_kwargs,
            )
        except ScrapeError:
            return [], False

    rows1, rows2, (watchlist1, _), (watchlist2, _) = await asyncio.gather(
        asyncio.to_thread(service.get_watched_films, first.id),
        asyncio.to_thread(service.get_watched_films, second.id),
        load_watchlist(first.username),
        load_watchlist(second.username),
    )
    watched1 = _enriched_from_watched_rows(rows1)
    watched2 = _enriched_from_watched_rows(rows2)
    common_watchlist = _common_watchlist_films(watchlist1, watchlist2, limit=5)
    bridge_films: list = []
    remaining = max(0, 5 - len(common_watchlist))
    if remaining:
        bridge_films = await _blend_bridge_films(
            watched1,
            watched2,
            watchlist1,
            watchlist2,
            enricher=enricher,
            n=remaining,
            exclude=common_watchlist,
        )
        if enricher is not None and bridge_films:
            with contextlib.suppress(Exception):
                await enricher.ensure_details(bridge_films)
    completed = {
        **stored["result"],
        "common_watchlist_films": [film.to_dict() for film in common_watchlist],
        "bridge_films": [film.to_dict() for film in bridge_films],
        "watchlist_public": bool(watchlist1) and bool(watchlist2),
        "watchlist_pending": False,
    }
    await asyncio.to_thread(
        service.save_blend_result,
        account,
        request_id,
        completed,
        algorithm_version=BLEND_VERSION,
    )
    await _record_activity_event(
        service,
        account,
        "blend_watchlist_completed",
        {
            "common_watchlist_count": len(common_watchlist),
            "bridge_count": len(bridge_films),
        },
    )


_accepted_blend_flights: dict[str, asyncio.Task] = {}
_accepted_blend_lock = asyncio.Lock()


async def _accepted_blend_single_flight(
    account: Account,
    request_id: str,
    service: AuthService,
    *,
    force_recompute: bool = False,
) -> dict:
    async with _accepted_blend_lock:
        task = _accepted_blend_flights.get(request_id)
        if task is None:
            task = asyncio.create_task(
                _compute_accepted_blend(
                    account,
                    request_id,
                    service,
                    force_recompute=force_recompute,
                )
            )
            _accepted_blend_flights[request_id] = task

            def clear(done_task, key=request_id):
                if _accepted_blend_flights.get(key) is done_task:
                    _accepted_blend_flights.pop(key, None)
                with contextlib.suppress(asyncio.CancelledError):
                    done_task.exception()

            task.add_done_callback(clear)
    return await asyncio.shield(task)


async def _cancel_blend_background_tasks(request_id: str) -> None:
    """Stop stale result/watchlist writers before refresh or deletion."""
    tasks = [
        _blend_watchlist_tasks.pop(request_id, None),
        _accepted_blend_flights.pop(request_id, None),
    ]
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@app.post("/api/blends/requests/{request_id}/decision")
async def decide_blend_invite(
    request_id: str, req: BlendDecisionRequest, request: Request
) -> dict:
    _require_csrf(request)
    account = await _require_account(request)
    if req.decision == "accepted":
        await _enforce_heavy_rate_limit(request)
    service = _auth_service()
    try:
        decision = await asyncio.to_thread(
            service.decide_blend_request,
            account,
            request_id,
            req.decision,
            ip_hash=_ip_hash(request),
        )
        if decision.get("status") == "expired":
            return {"request_id": request_id, "status": "expired"}
        if req.decision == "rejected":
            return {"request_id": request_id, "status": "rejected"}
        async with _sem:
            result = await _accepted_blend_single_flight(
                account, request_id, service
            )
        return {"status": "accepted", "result": result}
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    except ScrapeError as exc:
        _raise_scrape_http(exc)


@app.post("/api/blends/requests/{request_id}/result")
async def retry_blend_result(request_id: str, request: Request) -> dict:
    _require_csrf(request)
    await _enforce_heavy_rate_limit(request)
    account = await _require_account(request)
    service = _auth_service()
    try:
        async with _sem:
            result = await _accepted_blend_single_flight(
                account, request_id, service
            )
        return {"status": "accepted", "result": result}
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    except ScrapeError as exc:
        _raise_scrape_http(exc)


@app.post("/api/blends/{request_id}/refresh")
async def refresh_blend_result(request_id: str, request: Request) -> dict:
    """Recompute an accepted Blend from both users' latest durable profiles."""
    _require_csrf(request)
    await _enforce_heavy_rate_limit(request)
    account = await _require_account(request)
    service = _auth_service()
    try:
        await _cancel_blend_background_tasks(request_id)
        async with _sem:
            result = await _accepted_blend_single_flight(
                account,
                request_id,
                service,
                force_recompute=True,
            )
        return {"status": "refreshed", "result": result}
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    except ScrapeError as exc:
        _raise_scrape_http(exc)


@app.delete("/api/blends/{request_id}")
async def delete_blend(request_id: str, request: Request) -> dict:
    """Delete one shared Blend so it disappears for both participants."""
    _require_csrf(request)
    account = await _require_account(request)
    service = _auth_service()
    try:
        await _cancel_blend_background_tasks(request_id)
        await asyncio.to_thread(service.delete_blend, account, request_id)
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    return {"ok": True, "request_id": request_id}


@app.get("/api/blends/requests/{request_id}/result")
async def get_blend_result_status(request_id: str, request: Request) -> dict:
    account = await _require_account(request)
    service = _auth_service()
    try:
        # Consent/participant guard before reading the stored result.
        _blend_request, first, second = await asyncio.to_thread(
            service.get_blend_participants, account, request_id
        )
        stored = await asyncio.to_thread(service.get_blend_result, request_id)
    except BlendServiceError as exc:
        _raise_blend_http(exc)
    if not stored:
        return {"status": "preparing", "result": None}
    result = {
        **stored["result"],
        "avatar_url1": first.avatar_url or "",
        "avatar_url2": second.avatar_url or "",
        "request_id": request_id,
        "cached": True,
    }
    if result.get("watchlist_pending"):
        _schedule_blend_watchlist_completion(account, request_id, service)
    return {"status": "preparing" if result.get("watchlist_pending") else "ready", "result": result}


@app.delete("/api/data")
async def delete_data(req: DeleteDataRequest, request: Request, response: Response) -> dict:
    """Delete a username's regenerable profile and recommendation caches."""
    await _enforce_delete_rate_limit(request)
    settings = get_settings()
    account = await _enforce_account_username(request, req.username)

    # Stop this process from completing a stale profile refresh after deletion.
    async with _film_load_lock:
        flights = [
            _film_load_flights.pop(key)
            for key in list(_film_load_flights)
            if key[0] == req.username
        ]
    for flight in flights:
        flight.cancel()
    if flights:
        await asyncio.gather(*flights, return_exceptions=True)

    local_cache = Cache(settings.cache_db_path)
    local_ok = await asyncio.to_thread(
        _delete_cached_user_data, local_cache, req.username
    )
    remote_ok = True
    account_ok = True
    if settings.has_supabase:
        from supabase import create_client

        client = create_client(settings.supabase_url, settings.supabase_key)
        remote_cache = SupabaseCache(client)
        remote_ok = await asyncio.to_thread(
            _delete_cached_user_data, remote_cache, req.username
        )
        if getattr(settings, "has_auth", False) and account is not None and local_ok and remote_ok:
            try:
                await asyncio.to_thread(_auth_service().delete_account, account)
            except Exception:
                account_ok = False
        elif not getattr(settings, "has_auth", False):
            account_ok = await asyncio.to_thread(delete_user, client, req.username)

    if not (local_ok and remote_ok and account_ok):
        raise HTTPException(
            status_code=503,
            detail="Verinin tamamı silinemedi. Lütfen biraz sonra tekrar dene.",
        )
    log.warning("user data deleted username=%s", req.username)
    if getattr(settings, "has_auth", False):
        _invalidate_account_cache(request.cookies.get(ACCESS_COOKIE, ""))
        _clear_session_cookies(response)
    return {
        "ok": True,
        "username": req.username,
        "detail": "Kullanıcıya bağlı profil ve öneri cache'i silindi.",
    }


@app.post("/api/recommend")
async def recommend(req: RecommendRequest, request: Request):
    """SSE stream: queued? → scraping → enriching → ranking → llm → result."""
    global _q_waiting, _q_active
    account = await _enforce_account_username(request, req.username)
    await _enforce_heavy_rate_limit(request)
    service = _auth_service() if account is not None else None
    response_locale = _response_locale(account, request)

    async def generate():
        global _q_waiting, _q_active
        t0 = time.perf_counter()
        settings = get_settings()
        pipeline_id = secrets.token_hex(6)
        stage = "queue"

        # Sıraya gir
        async with _q_lock:
            _q_waiting += 1
            ahead = _q_active + _q_waiting - 1

        if ahead > 0:
            yield _sse({"type": "queued", "ahead": ahead})

        async with _sem:
            async with _q_lock:
                _q_waiting -= 1
                _q_active  += 1

            try:
                # Cache + enricher hazırlığı
                stage = "setup"
                supabase_client, cache = _make_cache(settings)
                pcache = _make_persistent_cache(settings, supabase_client)
                if supabase_client is not None:
                    await asyncio.to_thread(upsert_user, supabase_client, req.username)
                enricher = (
                    Enricher(settings.tmdb_api_key, cache, asset_store=service)
                    if settings.has_tmdb else None
                )

                watched_kwargs = dict(
                    delay=settings.scrape_delay,
                    max_pages=settings.watched_max_pages,
                    film_limit=settings.watched_film_limit,
                    max_retries=settings.scrape_max_retries,
                )
                watchlist_kwargs = dict(
                    delay=settings.scrape_delay,
                    max_pages=settings.scrape_max_pages,
                    film_limit=settings.watchlist_film_limit,
                    max_retries=settings.scrape_max_retries,
                )

                # 1+2. Cache'ten oku ya da scrape + TMDb enrich (heartbeat'li)
                stage = "load_films"
                yield _sse({"type": "step", "step": "scraping"})
                t1 = time.perf_counter()
                hs: dict = {}
                async for ping in _await_with_heartbeat(
                    asyncio.gather(
                        _load_user_films(req.username, "watched", settings=settings,
                                         enricher=enricher, pcache=pcache, scrape_kwargs=watched_kwargs),
                        _load_user_films(req.username, "watchlist", settings=settings,
                                         enricher=enricher, pcache=pcache, scrape_kwargs=watchlist_kwargs),
                    ),
                    hs,
                ):
                    yield ping
                if "error" in hs:
                    exc = hs["error"]
                    if isinstance(exc, ScrapeError):
                        await _record_activity_event(
                            service,
                            account,
                            "recommendation_failed",
                            {"stage": "scraping", "error_code": exc.code},
                        )
                        yield _scrape_error_event(exc)
                        return
                    raise exc
                (all_watched_films, w_cached), (watchlist_films, wl_cached) = hs["result"]
                watchlist_count = len(watchlist_films)
                load_ms = round((time.perf_counter() - t1) * 1000)
                yield _sse({"type": "step", "step": "enriching"})
                log.warning("⏱ load films      %.2fs  (watched=%d[cache=%s], watchlist=%d[cache=%s])",
                            time.perf_counter() - t1, len(all_watched_films), w_cached,
                            len(watchlist_films), wl_cached)

                favorite_directors: list[str] = []
                top_genres: list[str] = []
                favorite_four_slugs: list[str] = []
                stage = "profile_signals"
                if service is not None and account is not None:
                    with contextlib.suppress(Exception):
                        stored_profile = await asyncio.to_thread(
                            service.get_profile, account
                        )
                        taste = stored_profile.get("taste") or {}
                        favorite_directors = [
                            name for name in taste.get("top_directors", []) if name
                        ][:3]
                        top_genres = [g for g in taste.get("top_genres", []) if g][:3]
                        favorite_four_slugs = [
                            film.get("slug")
                            for film in stored_profile.get("favorite_films", [])
                            if film.get("slug")
                        ][:4]
                # The same source used for the profile read powers discovery:
                # Fav 4 plus films by the directors watched most often. This
                # replaces the retired manual Top 10 signal.
                director_counts = Counter(
                    film.director.strip()
                    for film in all_watched_films
                    if film.director and film.director.strip()
                )
                most_watched_directors = [
                    name for name, _count in director_counts.most_common(3)
                ]
                if most_watched_directors:
                    favorite_directors = most_watched_directors

                history_limit = getattr(settings, "recommendation_history_limit", 100)
                watched_films = all_watched_films[:history_limit]
                included_slugs = {film.slug for film in watched_films if film.slug}
                explicit_slugs = set(favorite_four_slugs)
                watched_films += [
                    film
                    for film in all_watched_films
                    if film.slug in explicit_slugs and film.slug not in included_slugs
                ]
                director_signal = [
                    film for film in all_watched_films
                    if film.director in set(most_watched_directors)
                    and film.slug not in {item.slug for item in watched_films if item.slug}
                ][:90]
                watched_films += director_signal

                discover_fallback = False
                if len(watchlist_films) < settings.num_recommendations:
                    stage = "discover_fallback"
                    # Watchlist empty or too thin to fill a recommendation set →
                    # top up from TMDb Discover, biased by taste, excluding
                    # everything already watched (and the films we already hold).
                    have_slugs = {
                        f.slug for f in watchlist_films if getattr(f, "slug", "")
                    }
                    topups = await _discover_fallback_films(
                        enricher, service, account, watched_films,
                        genre_names=top_genres,
                        limit=max(12, settings.num_recommendations * 6),
                    )
                    watchlist_films = watchlist_films + [
                        f for f in topups if f.slug not in have_slugs
                    ]
                    discover_fallback = True
                    if not watchlist_films:
                        await _record_activity_event(
                            service,
                            account,
                            "recommendation_failed",
                            {"stage": "candidate_pool", "error_code": "empty_watchlist"},
                        )
                        yield _sse({"type": "error", "detail": "Watchlist boş; alternatif öneri de bulunamadı."})
                        return

                recommendation_key = _recommendation_cache_key(
                    req.username,
                    watched_films,
                    watchlist_films,
                    model=settings.openai_model if settings.has_openai else "local",
                    count=settings.num_recommendations,
                    favorite_directors=favorite_directors,
                    favorite_four_slugs=favorite_four_slugs,
                    locale=response_locale,
                )
                stage = "cache_lookup"
                cached_recommendation = await asyncio.to_thread(
                    # Local/Supabase cache lookup happens after all inputs are
                    # known, so a failure here has its own useful stage label.
                    pcache.get,
                    _recommendation_namespace(req.username),
                    recommendation_key,
                    ttl=TTL_RECOMMENDATION,
                ) if pcache is not None else None
                if cached_recommendation:
                    total_ms = round((time.perf_counter() - t0) * 1000)
                    metrics = {
                        "total_ms": total_ms,
                        "load_ms": load_ms,
                        "rank_ms": 0,
                        "llm_ms": 0,
                        "watched_cache_hit": w_cached,
                        "watchlist_cache_hit": wl_cached,
                        "recommendation_cache_hit": True,
                        "tmdb_api_calls": getattr(enricher, "_api_calls", 0),
                        "tmdb_l2_hydrated": getattr(enricher, "_l2_hydrated", 0),
                        "tmdb_l2_flushed": getattr(enricher, "_l2_flushed", 0),
                    }
                    log.warning("pipeline_metrics %s", json.dumps(metrics, sort_keys=True))
                    log.warning("recommendation cache HIT %s", req.username)
                    await _record_activity_event(
                        service,
                        account,
                        "recommendation_completed",
                        {
                            "success": True,
                            "cache_hit": True,
                            "watched_count": len(watched_films),
                            "watchlist_count": watchlist_count,
                            "result_count": len(cached_recommendation.get("recommendations") or []),
                        },
                    )
                    await _attach_letterboxd_ratings(
                        cached_recommendation["recommendations"]
                    )
                    yield _sse({
                        "type": "result",
                        "username": req.username,
                        "watched_count": len(watched_films),
                        "watchlist_count": watchlist_count,
                        "taste_summary": cached_recommendation["taste_summary"],
                        "recommendations": cached_recommendation["recommendations"],
                        "discover_fallback": discover_fallback,
                        "meta": {
                            "tmdb_enabled": settings.has_tmdb,
                            "llm_used": cached_recommendation.get("llm_used", False),
                            "recommendation_cache_hit": True,
                            "metrics": metrics,
                        },
                    })
                    return

                # 3. TF-IDF ranking
                stage = "ranking"
                yield _sse({"type": "step", "step": "ranking"})
                t3 = time.perf_counter()
                if enricher is not None:
                    await enricher.ensure_details(_detail_sample(watched_films, 24))
                    # One cached filmography request per favorite director lets
                    # us identify their watchlist titles before shortlist pruning,
                    # without downloading credits for every candidate.
                    director_movies = await enricher.director_movie_ids(
                        favorite_directors
                    )
                    for film in watchlist_films:
                        if film.director or not film.tmdb_id:
                            continue
                        for director_name, movie_ids in director_movies.items():
                            if film.tmdb_id in movie_ids:
                                film.director = director_name
                                break
                candidate_count = settings.num_recommendations * 2
                director_pool = rank_watchlist(
                    watched_films,
                    watchlist_films,
                    n=min(len(watchlist_films), candidate_count * 4),
                    favorite_directors=favorite_directors,
                    director_boost=getattr(settings, "favorite_director_boost", 0.08),
                    favorite_four_slugs=favorite_four_slugs,
                    favorite_genres=top_genres,
                    genre_boost=getattr(settings, "favorite_genre_boost", 0.06),
                    locale=response_locale,
                )
                if enricher is not None:
                    await enricher.ensure_details(director_pool)
                candidates = rank_watchlist(
                    watched_films,
                    director_pool,
                    n=candidate_count,
                    favorite_directors=favorite_directors,
                    director_boost=getattr(settings, "favorite_director_boost", 0.08),
                    favorite_four_slugs=favorite_four_slugs,
                    favorite_genres=top_genres,
                    genre_boost=getattr(settings, "favorite_genre_boost", 0.06),
                    # This pass writes the reasons the member actually reads
                    # whenever the LLM is unavailable, so it needs the locale too.
                    locale=response_locale,
                )
                rank_ms = round((time.perf_counter() - t3) * 1000)
                log.warning("⏱ tfidf rank      %.2fs  (candidates=%d)", time.perf_counter() - t3, len(candidates))

                # 4. LLM
                stage = "llm"
                yield _sse({"type": "step", "step": "llm"})
                t4 = time.perf_counter()
                result = await rank_candidates(
                    settings,
                    watched_films,
                    candidates,
                    favorite_four_slugs=favorite_four_slugs,
                    locale=response_locale,
                )
                llm_ms = round((time.perf_counter() - t4) * 1000)
                cacheable_result = result.get("llm_used", False) or not settings.has_openai
                if pcache is not None and result.get("recommendations") and cacheable_result:
                    stage = "cache_result"
                    await asyncio.to_thread(
                        pcache.set,
                        _recommendation_namespace(req.username),
                        recommendation_key,
                        result,
                    )
                log.warning("⏱ llm rerank      %.2fs  (llm_used=%s)", time.perf_counter() - t4, result.get("llm_used"))
                log.warning("⏱ TOTAL           %.2fs", time.perf_counter() - t0)

                metrics = {
                    "total_ms": round((time.perf_counter() - t0) * 1000),
                    "load_ms": load_ms,
                    "rank_ms": rank_ms,
                    "llm_ms": llm_ms,
                    "watched_cache_hit": w_cached,
                    "watchlist_cache_hit": wl_cached,
                    "recommendation_cache_hit": False,
                    "tmdb_api_calls": getattr(enricher, "_api_calls", 0),
                    "tmdb_cache_hits": getattr(enricher, "_cache_hits", 0),
                    "tmdb_rate_limits": getattr(enricher, "_rate_limits", 0),
                    "tmdb_l2_hydrated": getattr(enricher, "_l2_hydrated", 0),
                    "tmdb_l2_flushed": getattr(enricher, "_l2_flushed", 0),
                }
                log.warning("pipeline_metrics %s", json.dumps(metrics, sort_keys=True))

                await _record_activity_event(
                    service,
                    account,
                    "recommendation_completed",
                    {
                        "success": True,
                        "cache_hit": False,
                        "llm_used": bool(result.get("llm_used")),
                        "watched_count": len(watched_films),
                        "watchlist_count": watchlist_count,
                        "result_count": len(result.get("recommendations") or []),
                    },
                )

                await _attach_letterboxd_ratings(result["recommendations"])
                yield _sse({
                    "type": "result",
                    "username": req.username,
                    "watched_count": len(watched_films),
                    "watchlist_count": watchlist_count,
                    "taste_summary": result["taste_summary"],
                    "recommendations": result["recommendations"],
                    "discover_fallback": discover_fallback,
                    "meta": {
                        "tmdb_enabled": settings.has_tmdb,
                        "llm_used": result.get("llm_used", False),
                        "recommendation_cache_hit": False,
                        "metrics": metrics,
                    },
                })

            except Exception as exc:
                # `str(exc)` can be empty (notably InvalidStateError's
                # "Exception is not set."), so include the type, current
                # pipeline stage and traceback. The short random id lets a
                # user-visible SSE failure be matched to one log sequence
                # without logging account data or request payloads.
                log.exception(
                    "pipeline failure id=%s stage=%s type=%s detail=%r",
                    pipeline_id,
                    stage,
                    type(exc).__name__,
                    str(exc)[:500],
                )
                await _record_activity_event(
                    service,
                    account,
                    "recommendation_failed",
                    {
                        "stage": stage,
                        "error": type(exc).__name__,
                        "pipeline_id": pipeline_id,
                    },
                )
                yield _sse({
                    "type": "error",
                    "detail": "Beklenmeyen bir hata oluştu.",
                    "error_id": pipeline_id,
                })

            finally:
                async with _q_lock:
                    _q_active -= 1

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/random")
async def random_pick(req: RandomRequest, request: Request):
    """SSE stream: seçilen katalogda, kullanıcının izlemediği filmlerden seç.

    Watchlist'ten bağımsızdır ve günlük bir kota taşımaz: her istek yeni bir
    örnek çeker, böylece kullanıcı beğenene kadar çevirebilir. Letterboxd'a hiç
    istek gitmediği için tekrarlanan çağrılar ucuzdur.
    """
    account = await _enforce_account_username(request, req.username)
    await _enforce_random_rate_limit(request)
    # Resolved out here: the generator runs after the response has started and
    # no longer has a request to read the language off.
    response_locale = _response_locale(account, request)

    async def generate():
        settings = get_settings()
        service = _auth_service() if account is not None else None
        supabase_client, cache = _make_cache(settings)

        yield _sse({"type": "step", "step": "enriching"})
        source = req.source
        if source in OFFICIAL_LIST_SOURCES:
            pool = await _official_list_pool(source, service, account, cache)
        else:
            pool = await _community_random_pool(service, account, locale=response_locale)
        if not pool and source == "community":
            # No community history yet (or no account): fall back to TMDb Discover.
            pool = await _random_discover_pool(settings, service, account, cache)
            source = "discover"
        if not pool:
            await _record_activity_event(
                service,
                account,
                "random_failed",
                {"error_code": "empty_pool", "stage": "pool"},
            )
            yield _sse({
                "type": "error",
                "detail": "Bu listede henüz izlemediğin bir film bulamadık; biraz sonra tekrar dene.",
            })
            return

        # A spin should still feel like this member's shelf, so their stored
        # directors and genres tilt the draw.
        taste_directors: list[str] = []
        taste_genres: list[str] = []
        if service is not None and account is not None:
            with contextlib.suppress(Exception):
                stored_taste = (
                    await asyncio.to_thread(service.get_profile, account)
                ).get("taste") or {}
                taste_directors = [n for n in stored_taste.get("top_directors", []) if n]
                taste_genres = [g for g in stored_taste.get("top_genres", []) if g]
        chosen = _pick_random_films(
            pool,
            RANDOM_PICK_COUNT,
            favorite_directors=taste_directors,
            favorite_genres=taste_genres,
        )
        chosen = await _hydrate_random_picks(chosen, settings, cache, service)

        _add_random_reasons(
            chosen,
            source=source,
            locale=response_locale,
            favorite_directors=taste_directors,
            favorite_genres=taste_genres,
        )
        await _record_activity_event(
            service,
            account,
            "random_completed",
            {
                "success": True,
                "pool_source": source,
                "pool_count": len(pool),
                "result_count": len(chosen),
                "discover_fallback": source == "discover",
            },
        )
        random_rows = [f.to_dict() for f in chosen]
        await _attach_letterboxd_ratings(random_rows)
        yield _sse({
            "type": "result",
            "username": req.username,
            "pool_source": source,
            "pool_count": len(pool),
            "discover_fallback": source == "discover",
            "films": random_rows,
        })

    return StreamingResponse(
        _capacity_stream(generate()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _calculate_blend(
    watched1: list,
    watched2: list,
    top_n: int = 20,
    *,
    favorite_four1: list[str] | set[str] | None = None,
    favorite_four2: list[str] | set[str] | None = None,
) -> dict:
    """Blend skoru, ortak filmler ve ortak yönetmen hesapla."""
    from collections import Counter
    import numpy as np
    from sklearn.metrics.pairwise import cosine_similarity
    from .semantic import profile_similarity

    def _cos(c1: Counter, c2: Counter) -> float:
        """İki Counter arasında cosine similarity."""
        keys = sorted(set(c1) | set(c2))
        if not keys:
            return 0.0
        v1 = np.array([c1.get(k, 0) for k in keys], dtype=float)
        v2 = np.array([c2.get(k, 0) for k in keys], dtype=float)
        if not v1.any() or not v2.any():
            return 0.0
        return float(cosine_similarity(v1.reshape(1, -1), v2.reshape(1, -1))[0][0])

    def _rating_weight(f) -> float:
        """Signed preference: dislikes subtract, loves add, unrated is weak interest."""
        r = f.user_rating
        if r is None:
            return 0.20
        return max(-1.0, min(1.0, (float(r) - 3.0) / 2.0))

    fav4_1 = {slug for slug in (favorite_four1 or []) if slug}
    fav4_2 = {slug for slug in (favorite_four2 or []) if slug}

    # ── Ortak filmler ──────────────────────────────────────────────────────
    # 1. Slug eşleşmesi (birincil)
    slugs2 = {f.slug for f in watched2 if f.slug}
    common_slugs = [f for f in watched1 if f.slug and f.slug in slugs2]

    # 2. Başlık+yıl eşleşmesi (ikincil — slug yoksa veya eksikse)
    seen_slugs = {f.slug for f in common_slugs}
    keys2 = {(f.title.lower().strip(), f.year) for f in watched2}
    common_title = [
        f for f in watched1
        if f.slug not in seen_slugs
        and (f.title.lower().strip(), f.year) in keys2
    ]
    common = common_slugs + common_title
    common_count = len(common)
    # Fav 4 is an explicit 5★ signal, while the other person's dislike can
    # still pull the mutual floor down.
    _w2_by_slug = {f.slug: f for f in watched2 if f.slug}
    _w2_by_key = {(f.title.lower().strip(), f.year): f for f in watched2}

    def _second_film(f):
        return (
            _w2_by_slug.get(f.slug)
            if f.slug
            else _w2_by_key.get((f.title.lower().strip(), f.year))
        ) or _w2_by_key.get((f.title.lower().strip(), f.year))

    def _effective_rating(rating, slug: str, fav4: set[str]):
        value = float(rating) if rating is not None else None
        if slug in fav4:
            return max(value or 0.0, 5.0)
        return value

    def _favorite_label(slug: str, fav4: set[str]) -> str:
        if slug in fav4:
            return "fav4"
        return ""

    def _common_rank(f):
        f2 = _second_film(f)
        r1 = f.user_rating
        r2 = f2.user_rating if f2 else None
        slug1 = f.slug or ""
        slug2 = f2.slug if f2 else slug1
        effective1 = _effective_rating(r1, slug1, fav4_1)
        effective2 = _effective_rating(r2, slug2, fav4_2)
        both_signaled = effective1 is not None and effective2 is not None
        if both_signaled:
            mutual_floor = min(effective1, effective2)
            mutual_average = (effective1 + effective2) / 2.0
            agreement = -abs(effective1 - effective2)
        else:
            known = [v for v in (effective1, effective2) if v is not None]
            mutual_floor = -1.0
            mutual_average = sum(known) / len(known) if known else -1.0
            agreement = -5.0
        explicit_count = sum((slug1 in fav4_1, slug2 in fav4_2))
        return (
            both_signaled,
            mutual_floor,
            mutual_average,
            agreement,
            explicit_count,
            f.poster_url is not None,
            f.vote_average,
        )

    common.sort(key=_common_rank, reverse=True)
    top_common = common[:top_n]
    film_preferences: dict[str, dict] = {}
    for film in top_common:
        second = _second_film(film)
        slug1 = film.slug or ""
        slug2 = second.slug if second else slug1
        identity = slug1 or f"{film.title.lower().strip()}:{film.year}"
        film_preferences[identity] = {
            "rating1": film.user_rating,
            "rating2": second.user_rating if second else None,
            "favorite1": _favorite_label(slug1, fav4_1),
            "favorite2": _favorite_label(slug2, fav4_2),
        }

    # ── Uyum skoru ─────────────────────────────────────────────────────────
    # Sinyal 1-4: Rating-ağırlıklı özellik vektörleri
    # 5 yıldız verilen film, 1 yıldızlı filmden ~5x daha fazla zevk profilini şekillendirir.
    def _weighted_counter(films, key_fn) -> Counter:
        c: Counter = Counter()
        for f in films:
            w = _rating_weight(f)
            for k in key_fn(f):
                c[k] += w
        return c

    genre1 = _weighted_counter(watched1, lambda f: f.genres or [])
    genre2 = _weighted_counter(watched2, lambda f: f.genres or [])
    keyword1 = _weighted_counter(watched1, lambda f: f.keywords or [])
    keyword2 = _weighted_counter(watched2, lambda f: f.keywords or [])
    director1 = _weighted_counter(
        watched1, lambda f: [f.director] if f.director else []
    )
    director2 = _weighted_counter(
        watched2, lambda f: [f.director] if f.director else []
    )
    era1 = _weighted_counter(
        watched1, lambda f: [(f.year // 10) * 10] if f.year else []
    )
    era2 = _weighted_counter(
        watched2, lambda f: [(f.year // 10) * 10] if f.year else []
    )
    genre_sim = _cos(genre1, genre2)
    kw_sim = _cos(keyword1, keyword2)
    dir_sim = _cos(director1, director2)
    era_sim = _cos(era1, era2)

    # A bounded direct-overlap signal prevents metadata-only coincidences from
    # dominating while still recognizing two people who watched the same films.
    identities1 = {
        f.slug or f"{f.title.lower().strip()}:{f.year}" for f in watched1
    }
    identities2 = {
        f.slug or f"{f.title.lower().strip()}:{f.year}" for f in watched2
    }
    overlap_sim = (
        len(identities1 & identities2) / max(len(identities1 | identities2), 1)
    )

    # Sinyal 5: Ortak filmlerde rating korelasyonu (Pearson)
    # Her iki kullanıcının da puan verdiği ortak filmlerde, aynı filmleri
    # benzer şekilde değerlendiriyorlarsa güçlü uyum sinyali.
    rating_corr = 0.0
    w2_by_slug = {f.slug: f for f in watched2 if f.slug}
    w2_by_key  = {(f.title.lower().strip(), f.year): f for f in watched2}
    pairs: list[tuple[float, float]] = []
    for f1 in watched1:
        if f1.user_rating is None:
            continue
        f2 = w2_by_slug.get(f1.slug) if f1.slug else None
        if f2 is None:
            f2 = w2_by_key.get((f1.title.lower().strip(), f1.year))
        if f2 and f2.user_rating is not None:
            pairs.append((f1.user_rating, f2.user_rating))

    rating_signal_available = False
    if len(pairs) >= 3:
        r1 = np.array([p[0] for p in pairs])
        r2 = np.array([p[1] for p in pairs])
        # Pearson correlation: NaN güvenliği için std kontrolü
        if r1.std() > 0 and r2.std() > 0:
            rating_corr = float(np.corrcoef(r1, r2)[0, 1])
            rating_signal_available = True

    # Rating correlation is the strongest signal when enough common ratings
    # exist. Unlike v2, negative correlation is a real incompatibility penalty.
    if rating_signal_available:
        raw = (
            genre_sim * 0.05
            + kw_sim * 0.20
            + dir_sim * 0.20
            + era_sim * 0.05
            + rating_corr * 0.35
            + overlap_sim * 0.15
        )
    else:
        raw = (
            genre_sim * 0.10
            + kw_sim * 0.30
            + dir_sim * 0.30
            + era_sim * 0.10
            + overlap_sim * 0.20
        )

    semantic_sim, semantic_coverage = profile_similarity(
        watched1,
        watched2,
        first_weights=[max(0.0, _rating_weight(film)) for film in watched1],
        second_weights=[max(0.0, _rating_weight(film)) for film in watched2],
    )
    # Semantic proximity can discover shared film language where the exact
    # keyword vocabularies differ. It stays secondary to shared ratings and
    # favorites, so two people who rate common films in opposite ways cannot
    # become a high-match Blend merely through similar synopsis wording.
    if semantic_sim is not None and semantic_coverage >= 0.20:
        raw = raw * 0.85 + semantic_sim * 0.15

    # The displayed score is intentionally warmer than the raw statistical
    # similarity. A nonlinear calibration protects meaningful differences while
    # avoiding demoralizing zeroes for two valid, simply different profiles.
    # The floor and the curve were both raised once real Blends read colder
    # than they felt: raw similarity between two people who share a film
    # language rarely passes 0.5, so the mid range is where the warmth has to
    # land. Ordering is untouched — the curve is still monotonic in `raw`.
    bounded_raw = max(0.0, min(raw, 1.0))
    calibrated_score = 32.0 + 68.0 * (bounded_raw ** 0.72)

    shared_fav4 = sorted(fav4_1 & fav4_2)
    favorite_bonus = min(len(shared_fav4) * 12, 24)
    score = round(min(100.0, calibrated_score + favorite_bonus))

    min_watched = min(len(watched1), len(watched2))

    def _metadata_coverage(films) -> float:
        if not films:
            return 0.0
        quality = [
            (0.25 if f.genres else 0.0)
            + (0.35 if f.keywords else 0.0)
            + (0.30 if f.director else 0.0)
            + (0.10 if f.year else 0.0)
            for f in films
        ]
        return sum(quality) / len(quality)

    sample_confidence = min(min_watched / 100.0, 1.0)
    metadata_confidence = (
        _metadata_coverage(watched1) + _metadata_coverage(watched2)
    ) / 2.0
    overlap_confidence = min(common_count / 10.0, 1.0)
    rating_confidence = min(len(pairs) / 8.0, 1.0)
    confidence_value = (
        sample_confidence * 0.45
        + metadata_confidence * 0.35
        + overlap_confidence * 0.10
        + rating_confidence * 0.10
    )
    confidence_score = round(max(0.0, min(confidence_value, 1.0)) * 100)
    if confidence_score >= 75:
        confidence_level = "high"
    elif confidence_score >= 45:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    # ── Ortak en sevilen yönetmen ───────────────────────────────────────────
    def _loved_directors(films) -> Counter:
        loved: Counter = Counter()
        for film in films:
            if not film.director:
                continue
            weight = _rating_weight(film)
            if weight > 0:
                loved[film.director] += weight
        return loved

    director_love1 = _loved_directors(watched1)
    director_love2 = _loved_directors(watched2)
    director_counts1 = Counter(f.director for f in watched1 if f.director)
    director_counts2 = Counter(f.director for f in watched2 if f.director)
    common_dirs = set(director_love1) & set(director_love2)

    if common_dirs:
        # min(d1, d2): her iki kullanıcının da gerçekten sevdiği yönetmeni öne çıkar.
        # Toplam (d1+d2) yerine min kullanmak tek taraflı baskınlığı engeller.
        # Eşitlikte toplam tiebreaker olarak kullanılır.
        scored = {d: (min(director_love1[d], director_love2[d]), director_love1[d] + director_love2[d])
                  for d in common_dirs}
        top_dir = max(scored, key=scored.get)
    else:
        top_dir = None

    return {
        "score": score,
        "confidence": {
            "level": confidence_level,
            "score": confidence_score,
            "sample_size": min_watched,
            "metadata_coverage": round(metadata_confidence * 100),
            "semantic_coverage": round(semantic_coverage * 100),
            "rating_pairs": len(pairs),
        },
        "common_count": common_count,
        "top_director": top_dir,
        "top_director_count1": director_counts1.get(top_dir, 0) if top_dir else 0,
        "top_director_count2": director_counts2.get(top_dir, 0) if top_dir else 0,
        "films": top_common,
        "film_preferences": film_preferences,
        "favorite_matches": {
            "fav4": shared_fav4,
            "bonus": favorite_bonus,
        },
    }


def _common_watchlist_films(first: list, second: list, limit: int = 3) -> list:
    """Match two watchlists by stable slug, falling back to normalized title/year."""
    second_slugs = {film.slug for film in second if film.slug}
    common = [film for film in first if film.slug and film.slug in second_slugs]
    seen_slugs = {film.slug for film in common}
    second_keys = {(film.title.lower().strip(), film.year) for film in second}
    common += [
        film
        for film in first
        if film.slug not in seen_slugs
        and (film.title.lower().strip(), film.year) in second_keys
    ]
    common.sort(
        key=lambda film: (film.poster_url is not None, film.vote_average),
        reverse=True,
    )
    return common[:limit]


async def _blend_bridge_films(
    watched1: list,
    watched2: list,
    watchlist1: list,
    watchlist2: list,
    enricher=None,
    n: int = 5,
    exclude: list | None = None,
) -> list:
    """Fill up to N remaining slots with unseen films that bridge both tastes.

    Candidates must be unseen by *both* users. The pool is drawn from the two
    watchlists first; if that is too thin it is widened with a popularity
    discover pool biased toward the genres both users watch. The pool is then
    ranked against the combined viewing history so the picks lean toward what
    each person already likes.
    """
    from collections import Counter

    seen_slugs = {f.slug for f in (watched1 + watched2) if f.slug}
    seen_keys = {
        (f.title.lower().strip(), f.year)
        for f in (watched1 + watched2)
        if f.title
    }
    excluded_slugs = {f.slug for f in (exclude or []) if f.slug}
    excluded_keys = {
        (f.title.lower().strip(), f.year)
        for f in (exclude or [])
        if f.title
    }
    unavailable_keys = seen_keys | excluded_keys

    def _unseen(film) -> bool:
        if film.slug and (film.slug in seen_slugs or film.slug in excluded_slugs):
            return False
        if film.title and (
            film.title.lower().strip(), film.year
        ) in unavailable_keys:
            return False
        return True

    pool: list = []
    pool_slugs: set = set()
    pool_keys: set = set()

    def _add(film) -> None:
        if not _unseen(film):
            return
        key = (film.title.lower().strip() if film.title else "", film.year)
        if (film.slug and film.slug in pool_slugs) or key in pool_keys:
            return
        if film.slug:
            pool_slugs.add(film.slug)
        pool_keys.add(key)
        pool.append(film)

    for film in (watchlist1 + watchlist2):
        _add(film)

    if len(pool) < n and enricher is not None:
        g1 = Counter(g for f in watched1 for g in (f.genres or []))
        g2 = Counter(g for f in watched2 for g in (f.genres or []))
        shared = [g for g, _ in (g1 & g2).most_common(4)] or [
            g for g, _ in (g1 + g2).most_common(4)
        ]
        with contextlib.suppress(Exception):
            for film in await enricher.discover_pool(genre_names=shared, limit=40):
                if not film.slug and film.title:
                    film.slug = _guess_lb_slug(film.title)
                _add(film)

    if not pool:
        return []

    ranked = rank_watchlist(watched1 + watched2, pool, n=n)
    for film in ranked:
        film.reason = ""  # similarity note is internal, not shown for bridge picks
    return ranked[:n]


@app.post("/api/blend")
async def blend(req: BlendRequest, request: Request):
    """SSE stream: iki kullanıcının film zevkini harmanlayıp uyum skoru hesapla."""
    await _enforce_account_username(request, req.username1)
    if getattr(get_settings(), "has_auth", False):
        raise HTTPException(
            status_code=409,
            detail="Blend için karşı taraf onayı gerekir. Inbox akışı P1 ile açılacak.",
        )
    await _enforce_heavy_rate_limit(request)
    log.warning("blend request: %s / %s", req.username1, req.username2)

    async def generate():
        t0 = time.perf_counter()
        settings = get_settings()
        try:
            supabase_client, cache = _make_cache(settings)
            pcache = _make_persistent_cache(settings, supabase_client)
            if supabase_client is not None:
                await asyncio.to_thread(upsert_user, supabase_client, req.username1)
                await asyncio.to_thread(upsert_user, supabase_client, req.username2)
            enricher = (
                Enricher(settings.tmdb_api_key, cache, asset_store=_auth_service())
                if settings.has_tmdb else None
            )

            watched_kwargs = dict(
                delay=settings.scrape_delay, max_pages=settings.watched_max_pages,
                film_limit=settings.watched_film_limit, max_retries=settings.scrape_max_retries,
            )
            watchlist_kwargs = dict(
                delay=settings.scrape_delay, max_pages=settings.scrape_max_pages,
                film_limit=settings.watchlist_film_limit, max_retries=settings.scrape_max_retries,
            )

            def _load(username, list_type, kwargs):
                return _load_user_films(username, list_type, settings=settings,
                                        enricher=enricher, pcache=pcache, scrape_kwargs=kwargs)

            async def _safe_load(username, list_type, kwargs):
                try:
                    return await _load(username, list_type, kwargs)
                except ScrapeError:
                    return [], False

            yield _sse({"type": "step", "step": "scraping"})
            t1 = time.perf_counter()

            # ── Faz 1: izlenen filmler (cache→scrape+enrich, paralel, heartbeat'li) ──
            h1: dict = {}
            async for ping in _await_with_heartbeat(
                asyncio.gather(
                    _load(req.username1, "watched", watched_kwargs),
                    _load(req.username2, "watched", watched_kwargs),
                ), h1,
            ):
                yield ping
            if "error" in h1:
                exc = h1["error"]
                if isinstance(exc, ScrapeError):
                    log.warning("blend scrape error (watched): %s", exc)
                    yield _scrape_error_event(exc)
                    return
                raise exc
            (w1_enriched, _c1), (w2_enriched, _c2) = h1["result"]

            if not w1_enriched:
                yield _sse({"type": "error", "detail": f"@{req.username1} profili bulunamadı veya gizli."})
                return
            if not w2_enriched:
                yield _sse({"type": "error", "detail": f"@{req.username2} profili bulunamadı veya gizli."})
                return

            yield _sse({"type": "step", "step": "enriching"})

            # Watchlist'ler skor için gerekli değil. Arka planda başlatılır ve
            # ana Blend sonucu bekletilmeden ayrı bir SSE olayıyla tamamlanır.
            watchlist_future = asyncio.gather(
                _safe_load(req.username1, "watchlist", watchlist_kwargs),
                _safe_load(req.username2, "watchlist", watchlist_kwargs),
            )

            if enricher is not None:
                await asyncio.gather(
                    enricher.ensure_details(_detail_sample(w1_enriched, 40)),
                    enricher.ensure_details(_detail_sample(w2_enriched, 40)),
                )

            log.warning("blend profile load %.2fs  w1=%d w2=%d",
                        time.perf_counter() - t1, len(w1_enriched), len(w2_enriched))
            load_ms = round((time.perf_counter() - t1) * 1000)

            # ── Ranking ───────────────────────────────────────────────────────────
            yield _sse({"type": "step", "step": "ranking"})
            result = _calculate_blend(w1_enriched, w2_enriched, top_n=10)

            metrics = {
                "total_ms": round((time.perf_counter() - t0) * 1000),
                "load_ms": load_ms,
                "tmdb_api_calls": getattr(enricher, "_api_calls", 0),
                "tmdb_cache_hits": getattr(enricher, "_cache_hits", 0),
                "tmdb_rate_limits": getattr(enricher, "_rate_limits", 0),
                "tmdb_l2_hydrated": getattr(enricher, "_l2_hydrated", 0),
                "tmdb_l2_flushed": getattr(enricher, "_l2_flushed", 0),
            }
            log.warning("blend_metrics %s", json.dumps(metrics, sort_keys=True))

            log.warning("blend TOTAL %.2fs  score=%d common=%d",
                        time.perf_counter() - t0, result["score"], result["common_count"])

            yield _sse({
                "type": "result",
                "username1": req.username1,
                "username2": req.username2,
                "score": result["score"],
                "confidence": result["confidence"],
                "watched_count1": len(w1_enriched),
                "watched_count2": len(w2_enriched),
                "common_count": result["common_count"],
                "top_director": result["top_director"],
                "top_director_photo": await _director_photo(
                    result["top_director"], enricher
                ),
                "top_director_count1": result["top_director_count1"],
                "top_director_count2": result["top_director_count2"],
                "films": [f.to_dict() for f in result["films"]],
                "common_watchlist_films": [],
                "bridge_films": [],
                "watchlist_public": None,
                "watchlist_pending": True,
                "meta": {"metrics": metrics},
            })

            # ── Faz 2: ortak watchlist (lazy, ana sonuçtan sonra) ────────────────
            h2: dict = {}
            async for ping in _await_with_heartbeat(watchlist_future, h2):
                yield ping
            if "error" in h2:
                log.warning("blend watchlist load failed: %s", h2["error"])
                wl1e, wl2e = [], []
            else:
                (wl1e, _w1), (wl2e, _w2) = h2["result"]
            common_wl_films = _common_watchlist_films(wl1e, wl2e, limit=5)
            bridge_wl_films: list = []
            remaining = max(0, 5 - len(common_wl_films))
            if remaining:
                bridge_wl_films = await _blend_bridge_films(
                    w1_enriched,
                    w2_enriched,
                    wl1e,
                    wl2e,
                    enricher=enricher,
                    n=remaining,
                    exclude=common_wl_films,
                )
                if enricher is not None and bridge_wl_films:
                    with contextlib.suppress(Exception):
                        await enricher.ensure_details(bridge_wl_films)
            yield _sse({
                "type": "watchlist_result",
                "common_watchlist_films": [film.to_dict() for film in common_wl_films],
                "bridge_films": [film.to_dict() for film in bridge_wl_films],
                "watchlist_public": bool(wl1e) and bool(wl2e),
                "metrics": {
                    "watchlist_ms": round((time.perf_counter() - t0) * 1000),
                    "watchlist_count1": len(wl1e),
                    "watchlist_count2": len(wl2e),
                },
            })

        except Exception as exc:
            log.warning("blend EXCEPTION %s: %s", type(exc).__name__, exc)
            yield _sse({"type": "error", "detail": "Beklenmeyen bir hata oluştu."})

    return StreamingResponse(
        _capacity_stream(generate()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
