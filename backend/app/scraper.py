"""Layer 1 — scrape Letterboxd film lists (watchlist and watched films).

Letterboxd has no public API, so we fetch HTML and parse the poster grid.
The CSS selectors are best-effort; Letterboxd can change its markup at any time.
"""

import asyncio
import html as _html
import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import urljoin

try:
    from curl_cffi.requests import AsyncSession
except ImportError:  # pragma: no cover - only for minimal/local runtimes
    import httpx

    class AsyncSession:  # type: ignore[no-redef]
        """Compatibility client when curl-cffi's native library is unavailable.

        Production uses curl-cffi for its browser TLS fingerprint. Keeping this
        tiny fallback means a broken optional native wheel cannot take the app
        (or its scraper tests) down before a request is even attempted.
        """

        def __init__(self, **_kwargs):
            self._client = httpx.AsyncClient(follow_redirects=True)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            await self._client.aclose()

        async def get(self, url: str, **kwargs):
            kwargs.pop("impersonate", None)
            return await self._client.get(url, **kwargs)
from bs4 import BeautifulSoup

log = logging.getLogger("moviebox")

BASE_URL = "https://letterboxd.com"

_scrape_flights: dict[tuple, asyncio.Task] = {}
_scrape_flight_lock = asyncio.Lock()


class LetterboxdCircuitOpenError(RuntimeError):
    """Raised locally while the shared, upstream-protection circuit is open."""

    def __init__(self, retry_after: float):
        super().__init__("Letterboxd request circuit is temporarily open")
        self.retry_after = retry_after


class _LetterboxdRequestBudget:
    """Process-wide adaptive budget for every request to Letterboxd.

    Requests are deliberately serialized. A 403/429 opens a process-wide
    circuit for long enough that a queued sync can checkpoint and retry later,
    instead of turning one upstream block into many near-identical requests.
    This protects profile syncs, entry syncs, and recommendation jobs from
    independently overwhelming the same upstream host.
    """

    def __init__(
        self,
        max_concurrency: int = 1,
        min_interval: float = 2.5,
        block_seconds: float = 120.0,
    ):
        self.max_concurrency = max_concurrency
        self.current_limit = max_concurrency
        self.min_interval = min_interval
        self.block_seconds = block_seconds
        self._active = 0
        self._next_allowed = 0.0
        self._blocked_until = 0.0
        self._penalties = 0
        self._success_streak = 0
        self._condition = asyncio.Condition()

    async def request(self, factory):
        while True:
            delay = 0.0
            async with self._condition:
                now = time.monotonic()
                if now < self._blocked_until:
                    raise LetterboxdCircuitOpenError(self._blocked_until - now)
                delay = max(self._blocked_until - now, self._next_allowed - now, 0.0)
                if delay <= 0 and self._active < self.current_limit:
                    self._active += 1
                    self._next_allowed = now + self.min_interval
                    break
                if delay <= 0:
                    await self._condition.wait()
                    continue
            await asyncio.sleep(min(delay, 60.0))

        response = None
        try:
            response = await factory()
            return response
        finally:
            status = getattr(response, "status_code", None)
            async with self._condition:
                self._active = max(0, self._active - 1)
                if status in (403, 429):
                    self._penalties = min(self._penalties + 1, 5)
                    self._success_streak = 0
                    self.current_limit = 1
                    # A block is a signal to checkpoint every crawl, not to
                    # retry through it.  This intentionally matches the durable
                    # sync retry window so a job resumes from its saved page.
                    cooldown = self.block_seconds
                    self._blocked_until = max(
                        self._blocked_until, time.monotonic() + cooldown
                    )
                    log.warning(
                        "letterboxd circuit OPEN status=%s cooldown=%.1fs limit=1",
                        status,
                        cooldown,
                    )
                elif status is not None and status < 400:
                    self._success_streak += 1
                    if self._success_streak >= 20 and self.current_limit < self.max_concurrency:
                        self.current_limit += 1
                        self._success_streak = 0
                        self._penalties = max(0, self._penalties - 1)
                        log.warning(
                            "letterboxd circuit RECOVER limit=%d", self.current_limit
                        )
                self._condition.notify_all()


_letterboxd_budget = _LetterboxdRequestBudget()


async def _budgeted_get(session, url: str, **kwargs):
    return await _letterboxd_budget.request(lambda: session.get(url, **kwargs))


async def _coalesce_scrape(key: tuple, factory):
    """Coalesce identical direct scrape calls, including across API modes."""
    async with _scrape_flight_lock:
        task = _scrape_flights.get(key)
        if task is None:
            task = asyncio.create_task(factory())
            _scrape_flights[key] = task

            def _clear_finished(done_task, flight_key=key):
                if _scrape_flights.get(flight_key) is done_task:
                    _scrape_flights.pop(flight_key, None)
                try:
                    done_task.exception()
                except asyncio.CancelledError:
                    pass

            task.add_done_callback(_clear_finished)
        else:
            log.warning("scraper single-flight JOIN %s", key[:2])

    return await asyncio.shield(task)


# ── HTTP istemcisi ayarları ─────────────────────────────────────────────────
# Tüm taramalar tek, sabit bir istemci profili kullanır. Bir erişim engelinden
# sonra profil değiştirmek veya art arda tekrar denemek yerine ortak devre
# kesiciye saygı duyulur.
_DEFAULT_IMPERSONATE = "chrome"

# Gerçek tarayıcı navigasyon başlıkları — TLS parmak izini davranışsal olarak tamamlar.
_NAV_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


async def _human_pause(base: float) -> None:
    """Ardışık sayfalar için küçük, değişken bir tempo koru.

    İstek bütçesi zaten süreç genelinde eşzamanlılık ve hız sınırı uyguluyor.
    Buradaki bekleme yalnızca bir sonraki sayfaya geçmeden önceki kısa jitter;
    eskiden eklenen 1,5--4 saniyelik rastlantısal "okuma molası" ise kullanıcı
    akışını yavaşlatıyor ve özellikle küçük listelerde hiçbir değer katmıyordu.
    """
    if base <= 0:
        return
    await asyncio.sleep(base * random.uniform(0.7, 1.2))


async def _warmup(session, username: str) -> int | None:
    """Probe the profile only when a list endpoint needs classification.

    List crawls used to always request the homepage and profile before the first
    film page.  On Render that added two more opportunities for a 403 before any
    useful data was read.  A direct list request is both faster and closer to the
    request a user makes from a profile; the profile probe is now a rare fallback
    used to distinguish an absent account from a private list.
    """
    try:
        response = await _budgeted_get(
            session,
            f"{BASE_URL}/{username}/",
            headers=_NAV_HEADERS,
            timeout=10,
        )
        return response.status_code
    except Exception:
        return None


async def _fetch_with_retry(
    session, url: str, referer: str, *, max_retries: int = 3, timeout: float = 14.0
):
    """Bir sayfayı getir; ağ hatalarını sınırlı tekrar dene, blokları hemen dön.

    403/429 bir yeniden deneme sinyali değildir. İlk yanıt ortak devre kesiciyi
    açar; çağıran işi kalıcı checkpoint'inden daha sonra sürdürür.
    """
    headers = {**_NAV_HEADERS, "Referer": referer}
    resp = None
    last_status = 0
    for attempt in range(max_retries):
        try:
            resp = await _budgeted_get(
                session,
                url, headers=headers, timeout=timeout, impersonate=_DEFAULT_IMPERSONATE
            )
        except LetterboxdCircuitOpenError as exc:
            log.info(
                "scraper: shared circuit open; skipped %s retry_after=%.1fs",
                url,
                exc.retry_after,
            )
            return None, 403
        except Exception as exc:
            last_status = -1
            log.warning("scraper: network error (attempt %d) %s: %s", attempt + 1, url, exc)
            if attempt == max_retries - 1:
                return None, last_status
            await _human_pause(1.0 * (attempt + 1))
            continue

        last_status = resp.status_code
        if resp.status_code not in (403, 429):
            return resp, resp.status_code
        return resp, last_status

    return resp, last_status


async def _fetch_profile_with_fresh_sessions(
    username: str, *, max_retries: int = 3
):
    """Fetch the small public profile document once.

    Account creation never waits through a block. The caller can admit the
    user in deferred-verification mode, and the durable profile sync resumes
    once the shared request budget permits another attempt.
    """
    profile_url = f"{BASE_URL}/{username}/"
    try:
        async with AsyncSession(impersonate=_DEFAULT_IMPERSONATE) as session:
            response = await _budgeted_get(
                session,
                profile_url,
                headers={**_NAV_HEADERS, "Referer": f"{BASE_URL}/"},
                timeout=14,
                impersonate=_DEFAULT_IMPERSONATE,
            )
            return response, response.status_code
    except LetterboxdCircuitOpenError as exc:
        log.info(
            "profile scraper: shared circuit open user=%s retry_after=%.1fs",
            username,
            exc.retry_after,
        )
        return None, 403
    except Exception as exc:
        log.warning("profile scraper: network error user=%s: %s", username, exc)
        return None, -1


class ScrapeError(Exception):
    """Raised when a Letterboxd page cannot be retrieved."""

    code = "scrape_failed"

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class ProfileNotFoundError(ScrapeError):
    code = "profile_not_found"


class PrivateListError(ScrapeError):
    code = "profile_or_list_private"


class EmptyListError(ScrapeError):
    code = "list_empty"


class AccessBlockedError(ScrapeError):
    code = "letterboxd_blocked"


class MarkupChangedError(ScrapeError):
    code = "markup_changed"


class ScrapeNetworkError(ScrapeError):
    code = "network_error"


def _empty_page_error(username: str, list_path: str, html: str) -> ScrapeError:
    """Classify a 200 response whose expected film grid could not be parsed."""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True).lower()
    raw = html.lower()
    if any(marker in text for marker in (
        "profile is private",
        "member's profile is private",
        "member’s profile is private",
        "private account",
        "watchlist is private",
    )):
        return PrivateListError(
            f"@{username} profili veya bu liste gizli; yalnızca herkese açık veriler okunabilir."
        )
    empty_markers = (
        "watchlist is empty",
        "no films in this watchlist",
        "no films yet",
        "no diary entries",
        "hasn't logged any films",
        "hasn’t logged any films",
        "0 films",
    )
    if any(marker in text for marker in empty_markers):
        label = "Watchlist" if list_path == "watchlist" else "Film listesi"
        return EmptyListError(f"@{username} için {label.lower()} boş.")
    # Normal Letterboxd templates can include challenge-related JavaScript, so
    # check clear empty-list copy first.  Otherwise an actually empty public
    # watchlist is misreported as a temporary Cloudflare block.
    if any(marker in raw for marker in ("cf-chl-", "challenge-platform")) or any(
        marker in text for marker in ("just a moment", "attention required")
    ):
        return AccessBlockedError(
            "Letterboxd erişimi geçici olarak engelledi. Birkaç dakika sonra tekrar dene."
        )
    return MarkupChangedError(
        "Letterboxd sayfası açıldı ancak film kartları okunamadı; sayfa yapısı değişmiş olabilir."
    )


@dataclass
class ScrapedFilm:
    title: str
    year: Optional[int]
    slug: str
    poster_url: Optional[str] = None
    user_rating: Optional[float] = None  # Letterboxd 0.5-5.0 arası
    # Public Letterboxd lazy-poster endpoint. It is called only after the shared
    # asset catalog and TMDb both fail to provide a poster.
    poster_resolver_url: Optional[str] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScrapeListResult:
    """A page-window result with enough state to resume without skipping data.

    It remains unpackable as ``(films, complete)`` for the older call sites,
    while the full-history sync uses ``next_page`` and ``exhausted`` to persist a
    precise checkpoint after an upstream block.
    """

    films: list[ScrapedFilm]
    complete: bool
    next_page: int
    exhausted: bool
    pages_fetched: int

    def __iter__(self):
        yield self.films
        yield self.complete


@dataclass
class ScrapedProfile:
    username: str
    display_name: str
    avatar_url: Optional[str] = None
    bio: str = ""
    favorite_films: list[ScrapedFilm] = field(default_factory=list)
    stats: dict = field(default_factory=dict)  # {"films": 563, "this_year": 25, ...}

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "bio": self.bio,
            "favorite_films": [film.to_dict() for film in self.favorite_films],
            "stats": self.stats,
        }


def _slug_to_title(slug: str) -> str:
    return slug.replace("-", " ").strip().title()


def _parse_year_from_name(name: str) -> tuple[str, Optional[int]]:
    m = re.search(r"^(.*?)\s*\((\d{4})\)\s*$", name.strip())
    if m:
        return m.group(1).strip(), int(m.group(2))
    return name.strip(), None


_FILM_PATH_RE = re.compile(r"(?:https?://letterboxd\.com)?/film/([^/?#]+)/?")


def _slug_from_film_link(value: object) -> str:
    """Return a film slug only from an actual Letterboxd ``/film/`` URL."""
    match = _FILM_PATH_RE.search(str(value or "").strip())
    return match.group(1).strip().lower() if match else ""


def _stars_to_value(value: object) -> Optional[float]:
    """Parse Letterboxd's accessible star text (for example ``★★★½``)."""
    text = str(value or "").strip()
    if not text or not any(char in text for char in _STAR_VALUES):
        return None
    total = sum(_STAR_VALUES.get(char, 0.0) for char in text)
    return total if 0.5 <= total <= 5.0 else None


def _rating_from_node(node) -> Optional[float]:
    """Read either legacy CSS ratings or the current accessible SVG rating."""
    classes = " ".join(node.get("class", []))
    match = re.search(r"rated-(?:large-)?(\d{1,2})", classes)
    if match and 1 <= int(match.group(1)) <= 10:
        return int(match.group(1)) / 2.0

    # Letterboxd's current markup exposes the rating to assistive technology
    # through `aria-label` and also retains it in `<title>`.  Read both so the
    # parser survives either minified or accessibility-first renderings.
    return _stars_to_value(node.get("aria-label")) or _stars_to_value(
        node.get_text("", strip=True)
    )


def _extract_rating(el) -> Optional[float]:
    """Member rating from legacy grids and current SVG-based activity cards."""
    scopes = [el]
    if el.parent is not None:
        scopes.append(el.parent)
    for scope in scopes:
        ratings = scope.select(
            'span.rating, .rating, svg.glyph.-rating, svg[aria-label*="★"]'
        )
        if len(ratings) == 1:
            # Do not borrow a rating from a multi-poster parent/list container.
            rating = _rating_from_node(ratings[0])
            if rating is not None:
                return rating
    return None


def _parse_page(html: str) -> list[ScrapedFilm]:
    soup = BeautifulSoup(html, "lxml")
    films: list[ScrapedFilm] = []

    def _extract_poster(el) -> ScrapedFilm | None:
        slug = (
            el.get("data-item-slug")
            or el.get("data-film-slug")
            or _slug_from_film_link(el.get("data-item-link"))
            or _slug_from_film_link(el.get("data-target-link"))
        ).strip()
        if not slug or slug in ("", "/"):
            return None
        display_name = _html.unescape((
            el.get("data-item-full-display-name", "")
            or el.get("data-item-name", "")
            or el.get("data-film-name", "")
        ).strip())
        title, year = _parse_year_from_name(display_name) if display_name else ("", None)
        if not year:
            raw_year = el.get("data-film-release-year", "")
            if raw_year.isdigit():
                year = int(raw_year)
            else:
                m = re.search(r"-(\d{4})$", slug)
                if m:
                    year = int(m.group(1))
        poster_url: Optional[str] = None
        img = el.find("img")
        if img:
            if img.get("alt") and not title:
                title = img["alt"].strip()
            # Letterboxd has used eager images, lazy image attributes and
            # picture/source elements across its recent grid renderers.
            # srcset örneği: "https://a.ltrbxd.com/.../0-70-0-105-crop.jpg 1x, ...2x"
            src = (
                img.get("src", "")
                or img.get("data-src", "")
                or img.get("data-original", "")
            )
            if not src:
                srcset = img.get("srcset", "") or img.get("data-srcset", "")
                if not srcset:
                    source = el.select_one("picture source[srcset], source[srcset]")
                    srcset = source.get("srcset", "") if source else ""
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
            src = urljoin(BASE_URL, src) if src else ""
            if src and "empty-poster" not in src and src.startswith("https://"):
                poster_url = src
        else:
            source = el.select_one("picture source[srcset], source[srcset]")
            srcset = source.get("srcset", "") if source else ""
            src = srcset.split(",")[0].strip().split(" ")[0] if srcset else ""
            src = urljoin(BASE_URL, src) if src else ""
            if src and "empty-poster" not in src and src.startswith("https://"):
                poster_url = src
        if not title:
            title = _slug_to_title(slug)
        film = ScrapedFilm(
            title=title,
            year=year,
            slug=slug,
            poster_url=poster_url,
            user_rating=_extract_rating(el),
        )
        if not poster_url:
            # Current Letterboxd grids render an empty placeholder server-side
            # and expose their own poster resolver recipe as JSON. Keep it as a
            # private parser attribute so only actual misses trigger a request.
            raw_resolver = el.get("data-resolvable-poster-path", "")
            try:
                resolver = json.loads(raw_resolver) if raw_resolver else {}
            except (TypeError, ValueError):
                resolver = {}
            base_link = str(resolver.get("posteredBaseLink") or "")
            cache_key = str(resolver.get("cacheBustingKey") or "")
            if (
                base_link.startswith("/film/")
                and base_link.endswith("/")
                and resolver.get("hasDefaultPoster")
            ):
                path = f"{base_link}poster/std/230/"
                if re.fullmatch(r"[A-Za-z0-9_-]+", cache_key):
                    path += f"?k={cache_key}"
                film.poster_resolver_url = f"{BASE_URL}{path}"
        return film

    # 2024+ LazyPoster: data-item-slug
    candidates = soup.select("div[data-item-slug]")
    # Legacy: data-film-slug
    if not candidates:
        candidates = soup.select("div[data-film-slug]")
    # Newer list markup: li[data-film-slug] or li[data-item-slug]
    if not candidates:
        candidates = soup.select("li[data-film-slug], li[data-item-slug]")
    # Current LazyPoster cards expose both data-item-link and data-target-link.
    # Accept either so a partial rollout of the newer markup cannot blank a
    # whole collection. Restrict the fallback to real film links, not user/list
    # links that happen to be rendered in the same page.
    if not candidates:
        candidates = [
            el for el in soup.select("[data-item-link], [data-target-link]")
            if _slug_from_film_link(
                el.get("data-item-link") or el.get("data-target-link")
            )
        ]

    seen: set[str] = set()
    for el in candidates:
        film = _extract_poster(el)
        if film and film.slug not in seen:
            seen.add(film.slug)
            films.append(film)

    # Diary is a table, not a poster grid.  Letterboxd moved it to
    # `/<user>/diary/films/`; keep this fallback in the shared parser so the
    # pagination/checkpoint machinery remains identical for both list shapes.
    if not films:
        for row in soup.select("tr.diary-entry-row"):
            link = row.select_one(
                ".td-film-details a[href^='/film/'], a[href^='/film/']"
            )
            href = link.get("href", "") if link else ""
            slug_match = re.match(r"^/film/([^/]+)/", href)
            if not slug_match:
                continue
            slug = slug_match.group(1)
            if slug in seen:
                continue
            title = _html.unescape(link.get_text(" ", strip=True)) or _slug_to_title(slug)
            year = None
            released = row.select_one(".td-released")
            if released:
                year_match = re.search(r"\b(18|19|20)\d{2}\b", released.get_text(" ", strip=True))
                if year_match:
                    year = int(year_match.group(0))
            seen.add(slug)
            films.append(
                ScrapedFilm(
                    title=title,
                    year=year,
                    slug=slug,
                    user_rating=_extract_rating(row),
                )
            )

    return films


async def _resolve_missing_posters(session, films: list) -> int:
    """Use Letterboxd's own public lazy-poster resolver for true HTML misses."""
    targets = [
        film for film in films
        if not (film.get("poster_url") if isinstance(film, dict) else film.poster_url)
        and (
            film.get("poster_resolver_url", "")
            if isinstance(film, dict)
            else film.poster_resolver_url
        )
    ]
    if not targets:
        return 0
    resolved = 0

    async def worker(film: ScrapedFilm) -> None:
        nonlocal resolved
        try:
            response = await _budgeted_get(
                session,
                (
                    film.get("poster_resolver_url", "")
                    if isinstance(film, dict)
                    else film.poster_resolver_url
                ),
                headers={**_NAV_HEADERS, "Accept": "application/json"},
                timeout=12,
            )
            if response.status_code != 200:
                return
            payload = response.json()
            poster_url = payload.get("url2x") or payload.get("url") or ""
            if isinstance(poster_url, str) and poster_url.startswith("https://"):
                if isinstance(film, dict):
                    film["poster_url"] = poster_url
                else:
                    film.poster_url = poster_url
                resolved += 1
        except Exception:
            return

    await asyncio.gather(*(worker(film) for film in targets))
    return resolved


async def resolve_missing_posters(films: list) -> int:
    """Resolve only the final poster misses, in one shared-budgeted session."""
    async with AsyncSession(impersonate=_DEFAULT_IMPERSONATE) as session:
        return await _resolve_missing_posters(session, films)


# ── Letterboxd's own average rating ─────────────────────────────────────────
# A film page publishes its community average as schema.org JSON-LD, on the
# five-star scale members actually rate in. TMDb's ten-point vote is a different
# crowd on a different scale, so it cannot stand in for it.
def _parse_film_rating(html: str) -> Optional[float]:
    """Read schema.org ratings from either current JSON-LD script shape."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.select('script[type="application/ld+json"]'):
        payload = script.get_text().strip()
        # Letterboxd wraps the JSON in a CDATA comment.
        payload = payload.removeprefix("/* <![CDATA[ */").removesuffix("/* ]]> */")
        try:
            data = json.loads(payload.strip())
        except ValueError:
            continue

        # JSON-LD may be a single Movie object, a list, or an @graph as the
        # site changes how it combines structured metadata on a film page.
        roots = data if isinstance(data, list) else [data]
        for root in roots:
            nodes = root.get("@graph", []) if isinstance(root, dict) else []
            if isinstance(root, dict):
                nodes = [root, *nodes] if isinstance(nodes, list) else [root]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                rating = (node.get("aggregateRating") or {}).get("ratingValue")
                try:
                    value = float(rating)
                except (TypeError, ValueError):
                    continue
                if 0.0 < value <= 5.0:
                    return round(value, 2)
    return None


async def _fetch_film_rating(slug: str) -> Optional[float]:
    async with AsyncSession(impersonate=_DEFAULT_IMPERSONATE) as session:
        response, status = await _fetch_with_retry(
            session,
            f"{BASE_URL}/film/{slug}/",
            referer=f"{BASE_URL}/",
            max_retries=2,
            timeout=12.0,
        )
    if response is None or status != 200:
        return None
    return _parse_film_rating(response.text)


async def scrape_film_rating(slug: str) -> Optional[float]:
    """Letterboxd's community average for one film, out of 5, or None."""
    normalized = str(slug or "").strip().strip("/").lower()
    if not normalized:
        return None
    return await _coalesce_scrape(
        (normalized, "film-rating"), lambda: _fetch_film_rating(normalized)
    )


def _parse_profile_page(username: str, html: str) -> ScrapedProfile:
    """Parse public profile identity and ordered Favorite films metadata."""
    soup = BeautifulSoup(html, "lxml")
    summary = soup.select_one(".profile-summary, [data-profile-summary]")
    if summary is None:
        raise _empty_page_error(username, "profile", html)

    display_el = summary.select_one(
        ".person-display-name .label, .person-display-name, [data-profile-name]"
    )
    display_name = (
        _html.unescape(display_el.get_text(" ", strip=True))
        if display_el is not None
        else username
    )
    avatar = (
        summary.select_one("#avatar-large img")
        or summary.select_one(".profile-avatar img, [data-profile-avatar] img")
    )
    avatar_url = urljoin(BASE_URL, avatar.get("src", "").strip()) if avatar else ""
    if not avatar_url.startswith("https://"):
        avatar_url = ""

    bio_el = summary.select_one(
        ".js-bio-content, .js-bio, .profile-bio, [data-profile-bio]"
    )
    bio = _html.unescape(bio_el.get_text(" ", strip=True)) if bio_el else ""

    favorites_section = soup.select_one("#favourites, #favorites, [data-favourites]")
    favorite_films = (
        _parse_page(str(favorites_section))[:4] if favorites_section is not None else []
    )

    # Public profile stat row: Films / This year / Lists / Following / Followers.
    _STAT_KEYS = {
        "films": "films",
        "this year": "this_year",
        "lists": "lists",
        "following": "following",
        "followers": "followers",
    }
    stats: dict = {}
    for stat in soup.select(".profile-statistic"):
        value_el = stat.select_one(".value")
        label_el = stat.select_one(".definition")
        if value_el is None or label_el is None:
            continue
        key = _STAT_KEYS.get(label_el.get_text(" ", strip=True).lower())
        digits = re.sub(r"[^\d]", "", value_el.get_text("", strip=True))
        if key and digits:
            stats[key] = int(digits)

    return ScrapedProfile(
        username=username,
        display_name=display_name or username,
        avatar_url=avatar_url or None,
        bio=bio[:1000],
        favorite_films=favorite_films,
        stats=stats,
    )


async def _scrape_profile(
    username: str, *, max_retries: int, resolve_posters: bool = True
) -> ScrapedProfile:
    started = time.perf_counter()
    response, status = await _fetch_profile_with_fresh_sessions(
        username, max_retries=max_retries
    )

    if status == 404:
        raise ProfileNotFoundError(
            f"Letterboxd kullanıcısı '@{username}' bulunamadı.", status=404
        )
    if status in (403, 429):
        raise AccessBlockedError(
            f"Letterboxd erişimi engelledi (HTTP {status}).", status=status
        )
    if response is None:
        raise ScrapeNetworkError(
            "Letterboxd'a ağ üzerinden ulaşılamadı. Lütfen tekrar dene."
        )
    if status != 200:
        raise ScrapeError(
            f"Letterboxd HTTP {status} döndürdü: {BASE_URL}/{username}/",
            status=status,
        )

    profile = _parse_profile_page(username, response.text)
    poster_count = 0
    if resolve_posters:
        async with AsyncSession(impersonate=_DEFAULT_IMPERSONATE) as poster_session:
            poster_count = await _resolve_missing_posters(
                poster_session, profile.favorite_films
            )
    log.warning(
        "scrape_metrics list=profile duration_ms=%d favorites=%d avatar=%s poster_resolved=%d",
        round((time.perf_counter() - started) * 1000),
        len(profile.favorite_films),
        bool(profile.avatar_url),
        poster_count,
    )
    return profile


async def scrape_profile(
    username: str, *, max_retries: int = 3, resolve_posters: bool = True
) -> ScrapedProfile:
    """Fetch public profile identity, avatar, bio and ordered Favorite films.

    Auth flows only need identity and bio. They can disable poster resolution to
    avoid extra Letterboxd requests before the user even reaches the app.
    """
    normalized = username.strip().lstrip("@").lower()
    if not normalized:
        raise ScrapeError("Empty username.")
    return await _coalesce_scrape(
        (normalized, "profile", max_retries, bool(resolve_posters)),
        lambda: _scrape_profile(
            normalized,
            max_retries=max_retries,
            resolve_posters=resolve_posters,
        ),
    )


async def _scrape_list(
    username: str,
    list_path: str,
    *,
    delay: float = 1.0,
    max_pages: int = 40,
    start_page: int = 1,
    film_limit: int | None = None,
    max_retries: int = 3,
) -> ScrapeListResult:
    """Generic paginated scraper for any Letterboxd film grid.

    Strateji (ücretsiz ve doğrudan):
      1. İlk film sayfasına doğrudan git; normal akışta gereksiz warm-up yapma.
      2. Her sayfayı curl-cffi ile getir; humanize edilmiş jitter'lı gecikmeler.
      3. 403/429 gelirse kısa backoff + parmak izi rotasyonu ile tekrar dene.

    start_page: bu sayfadan başlar (resume edilebilir pencereli crawl için);
    en fazla `max_pages` sayfa daha çeker.
    film_limit: toplam bu sayıya ulaşınca durur (None = sınırsız).
    ``complete=False`` means the crawl stopped at ``next_page`` because of an
    upstream block or network error.  Callers that persist a full history must
    resume exactly there rather than advancing to the next fixed-size window.
    """
    username = username.strip().lstrip("@").lower()
    if not username:
        raise ScrapeError("Empty username.")

    films: list[ScrapedFilm] = []
    seen_slugs: set[str] = set()
    complete = True  # blok/hata ile yarıda kalırsa False'a çekilir
    exhausted = False
    started = time.perf_counter()
    pages_fetched = 0
    next_page = start_page

    async with AsyncSession(impersonate=_DEFAULT_IMPERSONATE) as session:
        for page in range(start_page, start_page + max_pages):
            direct_url = (
                f"{BASE_URL}/{username}/{list_path}/"
                if page == 1
                else f"{BASE_URL}/{username}/{list_path}/page/{page}/"
            )
            if page == 1:
                referer = f"{BASE_URL}/{username}/"
            elif page == 2:
                referer = f"{BASE_URL}/{username}/{list_path}/"
            else:
                referer = f"{BASE_URL}/{username}/{list_path}/page/{page - 1}/"

            # curl-cffi — retry + parmak izi rotasyonu
            resp, status = await _fetch_with_retry(
                session, direct_url, referer, max_retries=max_retries
            )

            # ── Durum kodu değerlendirmesi ─────────────────────────────────────
            if status in (403, 429):
                if page == start_page:
                    raise AccessBlockedError(
                        f"Letterboxd erişimi engelledi (HTTP {status}). "
                        "Sunucu IP'si geçici olarak bloklu olabilir.",
                        status=status,
                    )
                complete = False  # bloklandı → kalan sayfalar eksik
                next_page = page
                break
            if resp is None:
                if page == start_page:
                    raise ScrapeNetworkError(
                        "Letterboxd'a ağ üzerinden ulaşılamadı. Lütfen tekrar dene."
                    )
                complete = False  # ağ hatası ile yarıda kaldı
                next_page = page
                break
            if status == 404:
                # 404: bu sayfa yok → liste doğal olarak bitti (eksik değil).
                if page == start_page:
                    # A later window can legitimately begin after the final
                    # page.  Only the first overall page needs account/list
                    # classification.
                    if start_page > 1:
                        exhausted = True
                        break
                    profile_status = await _warmup(session, username)
                    if profile_status == 200:
                        raise PrivateListError(
                            f"@{username} profili bulundu ancak bu liste gizli veya erişilemiyor.",
                            status=404,
                        )
                    raise ProfileNotFoundError(
                        f"Letterboxd kullanıcısı '@{username}' bulunamadı.", status=404
                    )
                exhausted = True
                break
            if status != 200:
                if page == start_page:
                    raise ScrapeError(f"Letterboxd HTTP {status} döndürdü: {direct_url}")
                complete = False
                next_page = page
                break

            pages_fetched += 1
            next_page = page + 1
            page_films = _parse_page(resp.text)
            if not page_films:
                if page == start_page and start_page == 1:
                    preview = resp.text[:300].replace("\n", " ")
                    log.warning("scraper: page 1 empty (status=%s). HTML preview: %s", status, preview)
                    raise _empty_page_error(username, list_path, resp.text)
                exhausted = True
                break  # boş sayfa = pagination doğal sonu

            new_count = 0
            for film in page_films:
                if film.slug not in seen_slugs:
                    seen_slugs.add(film.slug)
                    films.append(film)
                    new_count += 1

            # Sayfa tamamen tekrar (yeni film yok) → pagination bitti, dur.
            if new_count == 0:
                exhausted = True
                break

            if film_limit and len(films) >= film_limit:
                films = films[:film_limit]
                break

            # Never sleep after the last requested page.  This is especially
            # important for the common one-page incremental/fingerprint reads:
            # the response is already complete, so a tail delay only extends
            # request latency.  Between pages we retain jittered pacing.
            has_next_requested_page = page < start_page + max_pages - 1
            if has_next_requested_page:
                await _human_pause(delay)

    log.warning(
        "scrape_metrics list=%s duration_ms=%d pages=%d films=%d complete=%s next_page=%d exhausted=%s",
        list_path,
        round((time.perf_counter() - started) * 1000),
        pages_fetched,
        len(films),
        complete,
        next_page,
        exhausted,
    )
    return ScrapeListResult(
        films=films,
        complete=complete,
        next_page=next_page,
        exhausted=exhausted,
        pages_fetched=pages_fetched,
    )


async def scrape_watchlist(
    username: str,
    *,
    delay: float = 1.0,
    max_pages: int = 40,
    film_limit: int | None = None,
    max_retries: int = 3,
) -> ScrapeListResult:
    """Kullanıcının izlemek istediği film listesini çeker. Döner: (films, complete)."""
    normalized = username.strip().lstrip("@").lower()
    key = (normalized, "watchlist", delay, max_pages, film_limit, max_retries)
    return await _coalesce_scrape(
        key,
        lambda: _scrape_list(
            normalized,
            "watchlist",
            delay=delay,
            max_pages=max_pages,
            film_limit=film_limit,
            max_retries=max_retries,
        ),
    )


async def scrape_official_list(
    list_slug: str,
    *,
    start_page: int = 1,
    max_pages: int = 1,
    max_retries: int = 1,
) -> ScrapeListResult:
    """Read one small page window from a public Letterboxd official list.

    Official lists are a catalogue source, not a member archive: callers cache
    their pages and sample one at a time instead of downloading hundreds of
    posters for every recommendation request.
    """
    normalized = list_slug.strip().strip("/").lower()
    if not re.fullmatch(r"[a-z0-9-]+", normalized):
        raise ScrapeError("Geçersiz Letterboxd resmi liste adı.")
    page = max(1, int(start_page or 1))
    pages = max(1, int(max_pages or 1))
    key = ("official", normalized, page, pages, max_retries)
    return await _coalesce_scrape(
        key,
        lambda: _scrape_list(
            "official",
            f"list/{normalized}",
            delay=0,
            max_pages=pages,
            start_page=page,
            max_retries=max_retries,
        ),
    )


async def scrape_diary(
    username: str,
    *,
    max_pages: int = 5,
    start_page: int = 1,
    film_limit: int = 250,
    max_retries: int = 3,
) -> ScrapeListResult:
    """Diary HTML sayfalarından ek film listesi çeker. Döner: (films, complete)."""
    normalized = username.strip().lstrip("@").lower()
    key = (normalized, "diary", max_pages, start_page, film_limit, max_retries)
    return await _coalesce_scrape(
        key,
        lambda: _scrape_list(
            normalized, "diary/films",
            delay=0.6, max_pages=max_pages, start_page=start_page,
            film_limit=film_limit, max_retries=max_retries,
        ),
    )


@dataclass
class DiaryEntry:
    """Bir günce kaydı: hangi film, hangi gün, kaç puan, varsa yorumu.

    `key` RSS guid'i (`letterboxd-review-<id>`) — kaydın kalıcı kimliği. Akışa
    bir kez düşmesini ve kullanıcı sildiğinde geri gelmemesini bu sağlıyor.
    """
    key: str
    slug: str
    title: str
    year: Optional[int]
    watched_on: str            # YYYY-MM-DD
    rating: Optional[float] = None
    rewatch: bool = False
    review: str = ""
    tmdb_id: Optional[int] = None


_STAR_VALUES = {"★": 1.0, "½": 0.5}
_VIEWING_ID = re.compile(r"^viewing:(\d+)$")
_TRAILING_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")
_LEADING_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _entry_date(stamp) -> str:
    """`<time datetime>` iki biçimde geliyor; ikisinden de günü alır.

    İzleme günü girilmiş kayıtta değer düz tarih (`2026-09-09`); girilmemişse
    yorumun yayımlanma anı (`2026-07-22T07:42:36.842Z`). İkincisini olduğu gibi
    kullanmak `created_at`'i `...ZT12:00:00+00:00` yapıyor ve Postgres satırı
    "time zone not recognized" ile reddediyordu — kayıt sessizce düşüyordu.
    """
    if stamp is None:
        return ""
    found = _LEADING_DATE.match(str(stamp.get("datetime") or "").strip())
    return found.group(1) if found else ""


def _stars_to_rating(article) -> Optional[float]:
    """`★★★½` → 3.5. Puanı olmayan kayıtta yıldız düğümü hiç yok."""
    rating = article.select_one("svg.glyph.-rating, svg[aria-label]")
    return _rating_from_node(rating) if rating is not None else None


def _parse_review_page(page_html: str) -> list[DiaryEntry]:
    """Bir `/films/reviews/` sayfasındaki kayıtları çıkarır.

    Kaydın kimliği `data-object-id="viewing:<id>"`; RSS'in
    `letterboxd-review-<id>` guid'iyle aynı sayı. İkisi aynı anahtarı ürettiği
    için toplu tarama ile günlük RSS taraması aynı kaydı iki kez düşürmüyor.
    """
    soup = BeautifulSoup(page_html, "lxml")
    out: list[DiaryEntry] = []
    for article in soup.select("article.production-viewing"):
        match = _VIEWING_ID.match(article.get("data-object-id") or "")
        # The current page retains `.js-review-body`; keep the semantic/body
        # fallbacks for a class-name-only front-end refactor.
        body = article.select_one(".js-review-body")
        if body is None:
            body = article.select_one("[data-full-text-url], .js-review .body-text.-prose")
        if not match or not body:
            continue
        poster = article.select_one("[data-item-slug]")
        watched_on = _entry_date(article.select_one("time.timestamp"))
        if not watched_on:
            # Tarihsiz kaydın akışta yeri yok: sıra izlenme gününe göre.
            continue
        name = (poster.get("data-item-name") if poster else "") or ""
        year = _TRAILING_YEAR.search(name)
        out.append(DiaryEntry(
            key=f"letterboxd-review-{match.group(1)}",
            slug=(poster.get("data-item-slug") if poster else "") or "",
            title=_TRAILING_YEAR.sub("", name).strip(),
            year=int(year.group(1)) if year else None,
            watched_on=watched_on,
            rating=_stars_to_rating(article),
            rewatch=False,
            review=body.get_text("\n", strip=True),
        ))
    return out


async def scrape_reviewed_diary(
    username: str, *, max_pages: int = 40, start_page: int = 1,
    progress: dict | None = None,
) -> list[DiaryEntry] | None:
    """Üyenin yazılı günce kayıtları — RSS'in son ~50 sınırı olmadan.

    `start_page` ile `max_pages` bir dilim veriyor: yeni üyenin arşivi tek
    seferde değil, koş başına birkaç sayfa hâlinde yeniden eskiye doğru
    taranabilsin. `progress` verilirse nereye kadar okunduğu (`last_page`) ve
    arşivin bitip bitmediği (`exhausted`) oraya yazılıyor; çağıran bunu saklayıp
    sonraki koşuda kaldığı yerden devam ediyor.

    RSS tek istekle geliyor ama yalnızca son elli kaydı taşıyor, dolayısıyla
    yıllar öncesinin yorumları oradan hiç görünmüyor. `/films/reviews/` sayfası
    sayfa başına on iki kayıtla bütün arşivi veriyor.

    Uzun yorumlar liste sayfasında kırpılıyor ve sonuna `…` konuyor; ölçtüm,
    603 karakterlik bir gövdenin tamamı 1254 karakterdi. O yüzden yalnızca
    kırpılanlar için tam metin ucu ayrıca çağrılıyor — kırpılmamış kaydın
    fazladan isteğe ihtiyacı yok.

    `None` okunamadı demektir (403/gizli/ağ); boş liste "yazılı kayıt yok"
    demektir. Toplu aktarım bu ikisini ayırmak zorunda, yoksa tek bir hız
    sınırı bütün üyeleri "yorumu yok" diye işaretler.
    """
    out: list[DiaryEntry] = []
    seen: set[str] = set()
    last_page = 0
    exhausted = False
    started = time.perf_counter()
    full_text_count = 0
    try:
        async with AsyncSession(impersonate=_DEFAULT_IMPERSONATE) as session:
            for page in range(start_page, start_page + max_pages):
                url = f"{BASE_URL}/{username}/films/reviews/page/{page}/"
                response = await _budgeted_get(
                    session, url, headers=_NAV_HEADERS, timeout=25,
                )
                if response.status_code != 200:
                    # İlk sayfa okunamadıysa üye hakkında hiçbir şey bilmiyoruz.
                    if page == start_page:
                        return None
                    break
                entries = _parse_review_page(response.text)
                last_page = page
                if not entries:
                    exhausted = True
                    break
                fresh = [entry for entry in entries if entry.key not in seen]
                seen.update(entry.key for entry in fresh)
                truncated = [entry for entry in fresh if entry.review.endswith("…")]
                if truncated:
                    # Review detail endpoints are independent.  Fetch a small
                    # bounded batch in parallel instead of serially turning a
                    # page of long reviews into N round trips.  The process-wide
                    # Letterboxd budget remains the final concurrency guard.
                    gate = asyncio.Semaphore(2)

                    async def hydrate(entry: DiaryEntry) -> None:
                        async with gate:
                            entry.review = await _full_review_text(
                                session, entry.key, entry.review,
                            )

                    await asyncio.gather(*(hydrate(entry) for entry in truncated))
                    full_text_count += len(truncated)
                out.extend(fresh)
                if len(entries) < 12:
                    exhausted = True
                    break
    except Exception as exc:  # noqa: BLE001 - besleme kritik değil
        log.warning("reviewed diary failed user=%s: %s", username, exc)
        if progress is not None:
            progress.update(last_page=last_page, exhausted=exhausted)
        return out or None
    if progress is not None:
        progress.update(last_page=last_page, exhausted=exhausted)
    log.warning(
        "scrape_metrics list=reviews duration_ms=%d pages=%d entries=%d full_text=%d exhausted=%s",
        round((time.perf_counter() - started) * 1000),
        last_page - start_page + 1 if last_page else 0,
        len(out),
        full_text_count,
        exhausted,
    )
    return out


async def _full_review_text(session, key: str, fallback: str) -> str:
    """Kırpılmış yorumun tamamını getirir; başarısız olursa kırpılmışı bırakır."""
    viewing_id = key.rsplit("-", 1)[-1]
    try:
        response = await _budgeted_get(
            session, f"{BASE_URL}/s/full-text/viewing:{viewing_id}/",
            headers=_NAV_HEADERS, timeout=20,
        )
        if response.status_code != 200:
            return fallback
        text = BeautifulSoup(response.text, "lxml").get_text("\n", strip=True)
        return text.strip() or fallback
    except Exception:  # noqa: BLE001 - kırpılmış metin hiç yoktan iyidir
        return fallback


async def scrape_following(username: str, *, max_pages: int = 5) -> list[str] | None:
    """Usernames this member follows on Letterboxd, for seeding the app graph.

    Person cards, not film cards, so the list parser does not apply: each entry
    is `div.person-summary > a.name` with the profile path as its href. Returns
    lowercase usernames.

    `None` means the page could not be read (403, private, network); an empty
    list means it was read and the member follows nobody. Seeding must tell
    those apart, otherwise a rate-limited run silently records "follows no one"
    for everybody and looks like a success.
    """
    out: list[str] = []
    seen: set[str] = set()
    first_page_ok = False
    try:
        async with AsyncSession(impersonate=_DEFAULT_IMPERSONATE) as session:
            for page in range(1, max(1, max_pages) + 1):
                path = f"{username}/following/" if page == 1 else f"{username}/following/page/{page}/"
                response, status = await _fetch_with_retry(
                    session,
                    f"{BASE_URL}/{path}",
                    f"{BASE_URL}/{username}/",
                    max_retries=2,
                )
                if response is None or status != 200:
                    break
                first_page_ok = True
                soup = BeautifulSoup(response.text, "lxml")
                names = [
                    anchor.get("href", "").strip("/").lower()
                    for anchor in soup.select("div.person-summary a.name")
                ]
                names = [name for name in names if name and "/" not in name]
                if not names:
                    break
                for name in names:
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
                if page < max(1, max_pages):
                    await _human_pause(0.6)
    except Exception as exc:  # noqa: BLE001 - seeding must never break a caller
        log.warning("following scrape failed user=%s: %s", username, exc)
        if not first_page_ok:
            return None
    return out


async def _fetch_watched_rss(username: str) -> list[ScrapedFilm]:
    """RSS feed'den en son ~50 izlenen filmi çeker (rating dahil).

    HTML scrape ile birleştirilerek kapsam genişletilir.
    Başarısız olursa boş liste döner — kritik değil.
    """
    url = f"{BASE_URL}/{username}/rss/"
    try:
        async with AsyncSession(impersonate=_DEFAULT_IMPERSONATE) as session:
            resp = await _budgeted_get(
                session, url, headers=_NAV_HEADERS, timeout=20
            )
        if resp.status_code != 200:
            return []
    except Exception:
        return []

    films: list[ScrapedFilm] = []
    entries = re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL)
    for entry in entries:
        title_m  = re.search(r"<letterboxd:filmTitle>(.*?)</letterboxd:filmTitle>", entry)
        year_m   = re.search(r"<letterboxd:filmYear>(\d{4})</letterboxd:filmYear>", entry)
        link_m   = re.search(r"<link>(https://letterboxd\.com[^<]+)</link>", entry)
        rating_m = re.search(r"<letterboxd:memberRating>([\d.]+)</letterboxd:memberRating>", entry)
        if not title_m:
            continue
        title  = _html.unescape(title_m.group(1).strip())
        year   = int(year_m.group(1)) if year_m else None
        rating = float(rating_m.group(1)) if rating_m else None
        slug   = ""
        if link_m:
            slug_match = re.search(r"/film/([^/]+)/", link_m.group(1))
            if slug_match:
                slug = slug_match.group(1)
        films.append(ScrapedFilm(title=title, year=year, slug=slug, user_rating=rating))

    return films


async def _scrape_watched_rss(username: str) -> list[ScrapedFilm]:
    """Coalesce simultaneous RSS reads from recent and taste-profile flows."""
    normalized = username.strip().lstrip("@").lower()
    return await _coalesce_scrape(
        (normalized, "watched-rss"), lambda: _fetch_watched_rss(normalized)
    )


async def scrape_films(
    username: str,
    *,
    start_page: int = 1,
    max_pages: int = 10,
    film_limit: int = 5000,
    max_retries: int = 3,
) -> ScrapeListResult:
    """Tüm izlenen filmler grid'i (`/films/`, 'eklenme' sırası, en yeni önce).

    Diary yalnızca tarihli loglanan filmleri kapsar; `/films/` kullanıcının
    izledim işaretlediği her filmi verir ve grid item'larda puanları taşır.
    Döner: (films, complete).
    """
    normalized = username.strip().lstrip("@").lower()
    key = (normalized, "films", start_page, max_pages, film_limit, max_retries)
    return await _coalesce_scrape(
        key,
        lambda: _scrape_list(
            normalized,
            "films",
            delay=0.6,
            max_pages=max_pages,
            start_page=start_page,
            film_limit=film_limit,
            max_retries=max_retries,
        ),
    )


async def scrape_recent_watched(
    username: str, *, max_retries: int = 3
) -> list[ScrapedFilm]:
    """En son eklenen ~72 film (`/films/` sayfa 1) + RSS ratings — ucuz artımlı diff.

    `/films/` sayfa 1 tarihsiz loglanan filmleri de yakalar ve grid'de puan
    taşır; RSS son ~50 diary puanını tamamlar. Blokluysa/boşsa boş liste döner.
    """
    rss_task = asyncio.create_task(_scrape_watched_rss(username))
    try:
        diary_films, _complete = await scrape_films(
            username, start_page=1, max_pages=1, film_limit=72, max_retries=max_retries
        )
    except ScrapeError:
        diary_films = []
    rss_films = await rss_task

    by_slug: dict[str, ScrapedFilm] = {}
    order: list[str] = []
    for film in diary_films:
        if film.slug and film.slug not in by_slug:
            by_slug[film.slug] = film
            order.append(film.slug)
    for film in rss_films:
        if not film.slug:
            continue
        if film.slug in by_slug:
            if film.user_rating is not None:
                by_slug[film.slug].user_rating = film.user_rating
        else:
            by_slug[film.slug] = film
            order.append(film.slug)
    return [by_slug[slug] for slug in order]


async def scrape_watched(
    username: str,
    *,
    delay: float = 1.0,
    max_pages: int = 10,
    film_limit: int = 100,
    max_retries: int = 3,
) -> tuple[list[ScrapedFilm], bool]:
    """Kullanıcının en son izlediği filmleri çeker (zevk profili için).

    Öncelik — hepsi tarihli/kronolojik, en yeni önce:
      1. Diary sayfaları (film_limit'e yetecek kadar, ~50 kayıt/sayfa)
      2. /rss/ — en son ~50 diary kaydı, diary'nin kaçırdığını tamamlar
      3. /films/ HTML (tarihsiz, sıra garantisiz) — sadece 1+2 film_limit'i
         doldurmazsa (az/dağınık diary kaydı olan kullanıcılar için) dolgu.
    film_limit hard cap olarak uygulanır (varsayılan 100) — "en son izlenen N film".
    Döner: (films, complete) — complete, taramanın bir blokla yarıda kalıp kalmadığı.
    """
    diary_pages = max(1, -(-film_limit // 50))
    rss_task = asyncio.create_task(_scrape_watched_rss(username))
    try:
        diary_films, complete = await scrape_diary(
            username,
            max_pages=diary_pages,
            film_limit=film_limit,
            max_retries=max_retries,
        )
    except ScrapeError:
        diary_films, complete = [], True  # diary boş/gizli olabilir, kritik değil

    seen: set[str] = {f.slug for f in diary_films if f.slug}
    by_slug = {f.slug: f for f in diary_films if f.slug}
    combined = list(diary_films)

    # RSS diary ile paralel çekilir ve liste dolmuş olsa bile rating'ler mevcut
    # kayıtlara merge edilir; aksi halde kişisel puan sinyali kaybolur.
    rss_films = await rss_task
    for f in rss_films:
        if f.slug:
            if f.slug in by_slug:
                if f.user_rating is not None:
                    by_slug[f.slug].user_rating = f.user_rating
                continue
            if f.slug not in seen and len(combined) < film_limit:
                seen.add(f.slug)
                by_slug[f.slug] = f
                combined.append(f)
        elif len(combined) < film_limit:
            key = f"{f.title.lower()}:{f.year}"
            if key not in seen:
                seen.add(key)
                combined.append(f)

    if len(combined) < film_limit:
        try:
            html_films, html_complete = await _scrape_list(
                username, "films",
                delay=delay,
                max_pages=max_pages,
                film_limit=film_limit,
                max_retries=max_retries,
            )
        except ScrapeError:
            html_films, html_complete = [], complete
        complete = complete and html_complete
        for f in html_films:
            if len(combined) >= film_limit:
                break
            if f.slug and f.slug not in seen:
                seen.add(f.slug)
                combined.append(f)

    return combined[:film_limit], complete
