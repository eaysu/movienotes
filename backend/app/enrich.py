"""Layer 2 — enrich scraped films with TMDb metadata.

Scraping gives us only titles and slugs. To recommend well we need overviews,
genres, directors and keywords. TMDb has a real, free API for exactly this.
Results are cached so a film looked up once is never fetched again.
"""

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx

from .cache import Cache

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
SEARCH_TTL = 60 * 60 * 24 * 30  # 30 days
NEGATIVE_LOOKUP_TTL = 60 * 60 * 24  # retry genuine "not found" results daily
log = logging.getLogger("moviebox")

_tmdb_client: Optional[httpx.AsyncClient] = None
_tmdb_client_lock = asyncio.Lock()
_tmdb_request_sem = asyncio.Semaphore(16)


async def _get_tmdb_client() -> httpx.AsyncClient:
    """Return the process-wide pooled TMDb client."""
    global _tmdb_client
    async with _tmdb_client_lock:
        if _tmdb_client is None or _tmdb_client.is_closed:
            _tmdb_client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=16),
            )
        return _tmdb_client


async def close_tmdb_client() -> None:
    """Close the shared connection pool during application shutdown."""
    global _tmdb_client
    if _tmdb_client is not None and not _tmdb_client.is_closed:
        await _tmdb_client.aclose()
    _tmdb_client = None


@dataclass
class EnrichedFilm:
    title: str
    year: Optional[int] = None
    slug: str = ""
    tmdb_id: Optional[int] = None
    overview: str = ""
    genres: list[str] = field(default_factory=list)
    director: str = ""
    keywords: list[str] = field(default_factory=list)
    vote_average: float = 0.0
    poster_url: Optional[str] = None
    matched: bool = False  # True once TMDb data was found
    details_loaded: bool = False  # director/keywords detail call completed
    similarity: float = 0.0
    reason: str = ""
    user_rating: Optional[float] = None  # Letterboxd kullanıcı puanı (0.5-5.0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["text_blob"] = self.text_blob
        return d

    @property
    def text_blob(self) -> str:
        """Everything an embedding model should 'read' about this film."""
        parts = [
            self.title,
            f"Directed by {self.director}." if self.director else "",
            f"Genres: {', '.join(self.genres)}." if self.genres else "",
            self.overview,
            f"Themes: {', '.join(self.keywords)}." if self.keywords else "",
        ]
        return " ".join(p for p in parts if p).strip()


class Enricher:
    """Wraps the TMDb API with caching and best-match selection."""

    def __init__(self, api_key: str, cache: Cache, asset_store=None):
        self.api_key = api_key
        self.cache = cache
        self.asset_store = asset_store
        self._genre_map: dict[int, str] = {}
        self._genre_lock = asyncio.Lock()
        self._cache_hits = 0
        self._api_calls = 0
        self._rate_limits = 0
        self._l2_hydrated = 0
        self._l2_flushed = 0
        self._asset_hits = 0
        self._asset_writes = 0

    async def _film_assets(self, slugs: list[str]) -> dict[str, dict]:
        getter = getattr(self.asset_store, "get_film_assets", None)
        if not getter or not slugs:
            return {}
        try:
            assets = await asyncio.to_thread(getter, slugs)
            self._asset_hits += len(assets)
            return assets
        except Exception:
            return {}

    async def save_film_assets(self, films: list[EnrichedFilm]) -> None:
        saver = getattr(self.asset_store, "save_film_posters", None)
        rows = [
            {
                "slug": film.slug,
                "poster_url": film.poster_url,
                "tmdb_id": film.tmdb_id,
                "title": film.title,
                "release_year": film.year,
                "overview": film.overview,
                "director": film.director,
                "genres": film.genres,
                "keywords": film.keywords,
                "vote_average": film.vote_average,
                "matched": film.matched,
                "details_loaded": film.details_loaded,
            }
            for film in films
            if film.slug
        ]
        if not saver or not rows:
            return
        try:
            self._asset_writes += await asyncio.to_thread(saver, rows)
        except Exception:
            pass

    async def _prefetch_cache(
        self,
        keys: list[str],
        ttl: Optional[float] = None,
        *,
        namespace: str = "tmdb",
    ) -> None:
        """Batch-hydrate the local cache when the backend supports an L2."""
        prefetch = getattr(self.cache, "prefetch", None)
        if prefetch and keys:
            self._l2_hydrated += await asyncio.to_thread(
                prefetch, namespace, keys, ttl
            )

    async def _flush_cache(self) -> None:
        """Persist queued local mutations without blocking the event loop."""
        flush = getattr(self.cache, "flush", None)
        if flush:
            self._l2_flushed += await asyncio.to_thread(flush)

    async def _get(self, client: httpx.AsyncClient, path: str, **params) -> dict:
        params["api_key"] = self.api_key
        for attempt in range(3):
            async with _tmdb_request_sem:
                resp = await client.get(f"{TMDB_BASE}{path}", params=params)
                self._api_calls += 1

            if resp.status_code == 429:
                self._rate_limits += 1
                retry_after = resp.headers.get("Retry-After", "")
                try:
                    delay = max(0.0, min(float(retry_after), 10.0))
                except ValueError:
                    delay = 0.5 * (2 ** attempt)
                log.warning("tmdb 429 path=%s retry=%d delay=%.2fs", path, attempt + 1, delay)
                if attempt < 2:
                    await asyncio.sleep(delay)
                    continue

            if resp.status_code >= 500 and attempt < 2:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue

            resp.raise_for_status()
            return resp.json()

        raise RuntimeError("TMDb retry loop exhausted")

    async def _load_genre_map(self, client: httpx.AsyncClient) -> None:
        if self._genre_map:
            return
        async with self._genre_lock:
            if self._genre_map:
                return
            cached = await asyncio.to_thread(self.cache.get, "tmdb", "genre_map")
            if cached:
                self._genre_map = {int(k): v for k, v in cached.items()}
                return
            data = await self._get(client, "/genre/movie/list")
            self._genre_map = {g["id"]: g["name"] for g in data.get("genres", [])}
            await asyncio.to_thread(self.cache.set, "tmdb", "genre_map", self._genre_map)

    async def fetch_now_playing(
        self, *, region: str = "TR", pages: int = 2, language: str = "tr-TR"
    ) -> list[dict]:
        """Films currently in cinemas in `region`, as raw rows for the bulletin.

        Deliberately not EnrichedFilm: the screening ingest wants the local
        distribution title next to the original one, so it can match a poster we
        already hold and still show the name printed on the ticket.
        """
        client = await _get_tmdb_client()
        cache_key = f"now_playing:{region}:{language}:{pages}"
        cached = await asyncio.to_thread(self.cache.get, "tmdb", cache_key)
        if cached:
            return list(cached)

        rows: list[dict] = []
        seen: set[int] = set()
        for page in range(1, max(1, pages) + 1):
            try:
                data = await self._get(
                    client,
                    "/movie/now_playing",
                    region=region,
                    language=language,
                    page=page,
                )
            except Exception:
                break
            results = data.get("results") or []
            if not results:
                break
            for item in results:
                tmdb_id = item.get("id")
                title = (item.get("title") or "").strip()
                if not tmdb_id or not title or tmdb_id in seen:
                    continue
                seen.add(int(tmdb_id))
                release = item.get("release_date") or ""
                poster = item.get("poster_path") or ""
                rows.append({
                    "tmdb_id": int(tmdb_id),
                    "title": title,
                    "original_title": (item.get("original_title") or "").strip(),
                    "year": int(release[:4]) if release[:4].isdigit() else None,
                    "poster_url": f"{TMDB_IMAGE_BASE}{poster}" if poster else "",
                    "overview": (item.get("overview") or "").strip(),
                    "vote_average": float(item.get("vote_average") or 0.0),
                })
            if page >= int(data.get("total_pages") or 1):
                break

        if rows:
            # Half a day: cinema programmes change daily, not hourly.
            await asyncio.to_thread(self.cache.set, "tmdb", cache_key, rows)
        return rows

    async def search_movie_candidates(
        self, title: str, *, year: int | None = None, language: str = "tr-TR"
    ) -> list[dict]:
        """Search candidates for a cinema programme line, local title first.

        Turkish distribution titles differ from the original ones, so the
        bulletin searches ``tr-TR`` before falling back to English, and keeps
        both titles on each candidate for the caller to compare.
        """
        query = (title or "").strip()
        if not query:
            return []
        cache_key = f"search:{language}:{year or ''}:{query.casefold()}"
        cached = await asyncio.to_thread(self.cache.get, "tmdb", cache_key)
        if cached is not None:
            return list(cached)
        client = await _get_tmdb_client()
        params = {"query": query, "language": language, "include_adult": "false"}
        if year:
            params["year"] = year
        try:
            data = await self._get(client, "/search/movie", **params)
        except Exception:
            return []
        out = []
        for item in (data.get("results") or [])[:8]:
            if not item.get("id"):
                continue
            release = item.get("release_date") or ""
            poster = item.get("poster_path") or ""
            out.append({
                "tmdb_id": int(item["id"]),
                "title": (item.get("title") or "").strip(),
                "original_title": (item.get("original_title") or "").strip(),
                "year": int(release[:4]) if release[:4].isdigit() else None,
                "poster_url": f"{TMDB_IMAGE_BASE}{poster}" if poster else "",
            })
        await asyncio.to_thread(self.cache.set, "tmdb", cache_key, out)
        return out

    async def discover_pool(
        self,
        *,
        genre_names: Optional[list[str]] = None,
        limit: int = 50,
        min_vote_average: float = 6.0,
    ) -> list["EnrichedFilm"]:
        """Well-known films from TMDb Discover, for cold-start fallbacks.

        Discover results already carry overview / genres / poster / vote, so no
        per-film detail call is needed. Optionally biased toward `genre_names`.
        """
        client = await _get_tmdb_client()
        await self._load_genre_map(client)
        name_to_id = {v.lower(): k for k, v in self._genre_map.items()}
        with_genres = ",".join(
            str(name_to_id[n.lower()])
            for n in (genre_names or [])
            if n and n.lower() in name_to_id
        )

        out: list[EnrichedFilm] = []
        for page in (1, 2, 3):
            params = {
                "sort_by": "popularity.desc",
                "vote_count.gte": 400,
                "vote_average.gte": float(min_vote_average),
                "include_adult": "false",
                "language": "en-US",
                "page": page,
            }
            if with_genres:
                params["with_genres"] = with_genres
            try:
                data = await self._get(client, "/discover/movie", **params)
            except Exception:
                break
            for r in data.get("results", []):
                if not r.get("id") or not r.get("title"):
                    continue
                rel = r.get("release_date") or ""
                year = int(rel[:4]) if rel[:4].isdigit() else None
                genres = [
                    self._genre_map[g]
                    for g in r.get("genre_ids", [])
                    if g in self._genre_map
                ]
                poster_path = r.get("poster_path")
                out.append(EnrichedFilm(
                    title=r["title"],
                    year=year,
                    tmdb_id=int(r["id"]),
                    overview=r.get("overview") or "",
                    genres=genres,
                    vote_average=float(r.get("vote_average") or 0.0),
                    poster_url=(
                        f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
                    ),
                    matched=True,
                    details_loaded=bool(genres),
                ))
            if len(out) >= limit:
                break
        return out[:limit]

    @staticmethod
    def _score_match(result: dict, title: str, year: Optional[int]) -> float:
        """Rough relevance score for picking the right search hit."""
        score = float(result.get("popularity", 0.0))
        if result.get("title", "").lower() == title.lower():
            score += 1000.0
        rel = result.get("release_date", "")
        if year and rel[:4].isdigit():
            if int(rel[:4]) == year:
                score += 500.0
            elif abs(int(rel[:4]) - year) <= 1:
                score += 100.0
        return score

    async def _load_details(
        self, client: httpx.AsyncClient, film: EnrichedFilm, cache_key: str
    ) -> EnrichedFilm:
        if not film.tmdb_id or film.details_loaded:
            return film
        try:
            details = await self._get(
                client,
                f"/movie/{film.tmdb_id}",
                append_to_response="keywords,credits",
            )
        except httpx.HTTPError:
            return film

        film.keywords = [
            k["name"] for k in details.get("keywords", {}).get("keywords", [])
        ][:12]
        film.overview = details.get("overview", "") or film.overview
        film.vote_average = float(details.get("vote_average", 0.0) or film.vote_average)
        film.genres = [
            genre.get("name", "")
            for genre in details.get("genres", [])
            if genre.get("name")
        ] or film.genres
        release_date = details.get("release_date", "") or ""
        if not film.year and release_date[:4].isdigit():
            film.year = int(release_date[:4])
        for crew in details.get("credits", {}).get("crew", []):
            if crew.get("job") == "Director":
                film.director = crew.get("name", "")
                break
        poster_path = details.get("poster_path") or ""
        if not film.poster_url and poster_path:
            film.poster_url = f"{TMDB_IMAGE_BASE}{poster_path}"
        film.details_loaded = True
        await asyncio.to_thread(self.cache.set, "tmdb", cache_key, film.to_dict())
        return film

    async def _enrich_one(
        self,
        client: httpx.AsyncClient,
        title: str,
        year: Optional[int],
        slug: str,
        *,
        include_details: bool,
        asset: Optional[dict] = None,
    ) -> EnrichedFilm:
        cache_key = slug or f"{title}:{year}"
        fallback_poster: Optional[str] = None
        # Successful metadata is durable. TMDb poster/credit data changes rarely,
        # and re-fetching a known film defeats the shared-cache speedup.
        cached = await asyncio.to_thread(self.cache.get, "tmdb", cache_key)
        if cached and (
            cached.get("matched") or cached.get("tmdb_id") or cached.get("poster_url")
        ):
            cached.pop("text_blob", None)
            cached.pop("similarity", None)
            cached.pop("reason", None)
            if "details_loaded" not in cached:
                cached["details_loaded"] = bool(
                    cached.get("director") or cached.get("keywords")
                )
            self._cache_hits = getattr(self, "_cache_hits", 0) + 1
            film = EnrichedFilm(**cached)
            if include_details and not film.details_loaded:
                if film.tmdb_id:
                    return await self._load_details(client, film, cache_key)
                # A Letterboxd-only poster is useful, but it is not a complete
                # metadata hit. Continue to title/year search so this record can
                # acquire a stable TMDb id, overview and credits.
                fallback_poster = film.poster_url
            else:
                return film

        # The shared catalog is a durable identity + metadata cache. Detail-less
        # paths can return any known catalog row immediately. Detailed paths
        # make no request when another user has already completed the record.
        if asset and (
            asset.get("tmdb_id")
            or asset.get("poster_url")
            or asset.get("details_loaded")
        ):
            film = EnrichedFilm(
                title=asset.get("title") or title,
                year=year or asset.get("release_year"),
                slug=slug,
                tmdb_id=int(asset["tmdb_id"]) if asset.get("tmdb_id") else None,
                overview=asset.get("overview") or "",
                genres=asset.get("genres") or [],
                director=asset.get("director") or "",
                keywords=asset.get("keywords") or [],
                vote_average=float(asset.get("vote_average") or 0.0),
                poster_url=asset.get("poster_url") or None,
                matched=bool(asset.get("matched") or asset.get("tmdb_id")),
                details_loaded=bool(asset.get("details_loaded")),
            )
            self._asset_hits += 1
            if include_details and not film.details_loaded and film.tmdb_id:
                return await self._load_details(client, film, cache_key)
            if not include_details or film.details_loaded:
                return film
            fallback_poster = film.poster_url or fallback_poster

        negative = await asyncio.to_thread(
            self.cache.get, "tmdb_negative", cache_key, ttl=NEGATIVE_LOOKUP_TTL
        )
        if negative:
            return EnrichedFilm(
                title=title, year=year, slug=slug, poster_url=fallback_poster
            )

        film = EnrichedFilm(
            title=title, year=year, slug=slug, poster_url=fallback_poster
        )

        try:
            await self._load_genre_map(client)
            search = await self._get(
                client, "/search/movie", query=title, **({"year": year} if year else {})
            )
            results = search.get("results", [])
            if not results:
                await asyncio.to_thread(
                    self.cache.set, "tmdb_negative", cache_key, {"not_found": True}
                )
                return film

            best = max(results, key=lambda r: self._score_match(r, title, year))
            film.tmdb_id = best.get("id")
            film.overview = best.get("overview", "") or ""
            film.vote_average = float(best.get("vote_average", 0.0) or 0.0)
            film.genres = [
                self._genre_map.get(gid, "")
                for gid in best.get("genre_ids", [])
                if gid in self._genre_map
            ]
            rel = best.get("release_date", "")
            if rel[:4].isdigit() and not film.year:
                film.year = int(rel[:4])
            poster_path = best.get("poster_path", "")
            backdrop_path = best.get("backdrop_path", "")
            if poster_path:
                film.poster_url = f"{TMDB_IMAGE_BASE}{poster_path}"
            elif backdrop_path:
                # Backdrop fallback: portrait poster yoksa landscape backdrop kullan.
                # Frontend card'ları object-fit: cover ile bu durumu handle eder.
                film.poster_url = f"https://image.tmdb.org/t/p/w780{backdrop_path}"

            film.matched = True
            if include_details:
                await self._load_details(client, film, cache_key)
        except httpx.HTTPError:
            # HTTP hatası (rate limit, timeout): cache'e yazma — bir sonraki istekte tekrar denensin.
            return film

        await asyncio.to_thread(self.cache.set, "tmdb", cache_key, film.to_dict())
        return film

    async def enrich(self, films: list, *, include_details: bool = True) -> list[EnrichedFilm]:
        """Search-enrich films; optionally fetch director/keyword details."""
        client = await _get_tmdb_client()
        cache_keys = [
            (f.get("slug", "") or f"{f['title']}:{f.get('year')}")
            if isinstance(f, dict)
            else (f.slug or f"{f.title}:{f.year}")
            for f in films
        ]
        await self._prefetch_cache(["genre_map"])
        await self._prefetch_cache(cache_keys)
        await self._prefetch_cache(
            cache_keys, NEGATIVE_LOOKUP_TTL, namespace="tmdb_negative"
        )
        assets = await self._film_assets(
            [
                (f.get("slug", "") if isinstance(f, dict) else f.slug) or ""
                for f in films
            ]
        )

        async def worker(f) -> EnrichedFilm:
            title = f["title"] if isinstance(f, dict) else f.title
            year = f.get("year") if isinstance(f, dict) else f.year
            slug = (f.get("slug", "") if isinstance(f, dict) else f.slug) or ""
            scraped_poster = (
                f.get("poster_url") if isinstance(f, dict) else getattr(f, "poster_url", None)
            )
            user_rating = (
                f.get("user_rating") if isinstance(f, dict) else getattr(f, "user_rating", None)
            )
            asset = assets.get(slug) or {}
            enriched = await self._enrich_one(
                client,
                title,
                year,
                slug,
                include_details=include_details,
                asset=asset,
            )
            if not enriched.tmdb_id and asset.get("tmdb_id"):
                enriched.tmdb_id = int(asset["tmdb_id"])
            if not enriched.poster_url and asset.get("poster_url"):
                enriched.poster_url = asset["poster_url"]
            if not enriched.poster_url and scraped_poster:
                enriched.poster_url = scraped_poster
            # user_rating TMDb cache'ine girmemeli (kişiye özel).
            enriched.user_rating = user_rating
            return enriched

        try:
            result = await asyncio.gather(*(worker(f) for f in films))
        finally:
            await self._flush_cache()
        await self.save_film_assets(result)
        log.warning(
            "tmdb enrich films=%d details=%s cache_hits=%d api_calls=%d "
            "rate_limits=%d l2_hydrated=%d l2_flushed=%d asset_hits=%d asset_writes=%d",
            len(films),
            include_details,
            self._cache_hits,
            self._api_calls,
            self._rate_limits,
            self._l2_hydrated,
            self._l2_flushed,
            self._asset_hits,
            self._asset_writes,
        )
        return result

    async def ensure_details(self, films: list[EnrichedFilm]) -> list[EnrichedFilm]:
        """Fetch director/keywords only for the selected, already searched films."""
        if not films:
            return films
        client = await _get_tmdb_client()
        cache_keys = [
            film.slug or f"{film.title}:{film.year}"
            for film in films
            if film.tmdb_id and not film.details_loaded
        ]
        await self._prefetch_cache(cache_keys)

        async def worker(film: EnrichedFilm) -> EnrichedFilm:
            if film.details_loaded or not film.tmdb_id:
                return film
            cache_key = film.slug or f"{film.title}:{film.year}"
            cached = await asyncio.to_thread(
                self.cache.get, "tmdb", cache_key
            )
            if cached and cached.get("details_loaded"):
                film.director = cached.get("director", "") or film.director
                film.keywords = cached.get("keywords", []) or film.keywords
                film.overview = cached.get("overview", "") or film.overview
                film.genres = cached.get("genres", []) or film.genres
                film.vote_average = float(
                    cached.get("vote_average", 0.0) or film.vote_average
                )
                if not film.year and cached.get("year"):
                    film.year = cached.get("year")
                if not film.poster_url and cached.get("poster_url"):
                    film.poster_url = cached.get("poster_url")
                film.details_loaded = True
                self._cache_hits += 1
                return film
            return await self._load_details(client, film, cache_key)

        before_calls = self._api_calls
        try:
            result = await asyncio.gather(*(worker(film) for film in films))
        finally:
            await self._flush_cache()
        await self.save_film_assets(result)
        log.warning(
            "tmdb details films=%d api_calls=%d",
            len(films),
            self._api_calls - before_calls,
        )
        return result

    async def posters_by_id(self, tmdb_ids: list[int]) -> dict[int, str]:
        """Fetch posters straight from /movie/{id} — no search ambiguity."""
        ids = [i for i in dict.fromkeys(tmdb_ids) if i]
        if not ids:
            return {}
        client = await _get_tmdb_client()
        keys = [f"poster_id:{i}" for i in ids]
        await self._prefetch_cache(keys)
        out: dict[int, str] = {}
        asset_getter = getattr(self.asset_store, "get_film_posters_by_tmdb_ids", None)
        if asset_getter:
            try:
                out.update(await asyncio.to_thread(asset_getter, ids))
                self._asset_hits += len(out)
            except Exception:
                pass
        missing_ids = [tmdb_id for tmdb_id in ids if tmdb_id not in out]

        async def worker(tmdb_id: int) -> None:
            key = f"poster_id:{tmdb_id}"
            cached = await asyncio.to_thread(self.cache.get, "tmdb", key)
            if cached and cached.get("poster_url"):
                self._cache_hits += 1
                out[tmdb_id] = cached["poster_url"]
                return
            url = ""
            try:
                data = await self._get(client, f"/movie/{tmdb_id}")
                path = data.get("poster_path") or data.get("backdrop_path") or ""
                if data.get("poster_path"):
                    url = f"{TMDB_IMAGE_BASE}{data['poster_path']}"
                elif path:
                    url = f"https://image.tmdb.org/t/p/w780{path}"
            except httpx.HTTPError:
                return
            await asyncio.to_thread(self.cache.set, "tmdb", key, {"poster_url": url})
            if url:
                out[tmdb_id] = url

        try:
            await asyncio.gather(*(worker(i) for i in missing_ids))
        finally:
            await self._flush_cache()
        return out

    async def movie_meta_by_id(self, tmdb_ids: list[int]) -> dict[int, dict]:
        """overview / poster / director / genres straight from /movie/{id}.

        Cached under a dedicated key so it never inherits an incomplete
        detail row left by an older enrichment pass.
        """
        ids = [i for i in dict.fromkeys(tmdb_ids) if i]
        if not ids:
            return {}
        client = await _get_tmdb_client()
        out: dict[int, dict] = {}

        async def worker(tmdb_id: int) -> None:
            key = f"meta_id:{tmdb_id}"
            cached = await asyncio.to_thread(self.cache.get, "tmdb", key)
            if isinstance(cached, dict) and "overview" in cached:
                self._cache_hits += 1
                out[tmdb_id] = cached
                return
            try:
                data = await self._get(
                    client, f"/movie/{tmdb_id}", append_to_response="credits"
                )
            except httpx.HTTPError:
                return
            director = ""
            for crew in data.get("credits", {}).get("crew", []):
                if crew.get("job") == "Director":
                    director = crew.get("name", "") or ""
                    break
            poster_path = data.get("poster_path") or ""
            meta = {
                "overview": data.get("overview") or "",
                "poster_url": (
                    f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else ""
                ),
                "director": director,
                "genres": [
                    g.get("name", "") for g in data.get("genres", []) if g.get("name")
                ],
                "vote_average": float(data.get("vote_average") or 0.0),
            }
            await asyncio.to_thread(self.cache.set, "tmdb", key, meta)
            out[tmdb_id] = meta

        try:
            await asyncio.gather(*(worker(i) for i in ids))
        finally:
            await self._flush_cache()
        return out

    async def person_photos(self, names: list[str]) -> dict[str, str]:
        """Map a director name → a portrait URL via TMDb person search (cached)."""
        wanted = [n.strip() for n in names if n and n.strip()]
        if not wanted:
            return {}
        client = await _get_tmdb_client()
        keys = [f"person:{n.lower()}" for n in wanted]
        await self._prefetch_cache(keys)
        out: dict[str, str] = {}
        image_getter = getattr(self.asset_store, "get_director_images", None)
        if image_getter:
            try:
                out.update(await asyncio.to_thread(image_getter, wanted))
                self._asset_hits += len(out)
            except Exception:
                pass
        missing_names = [name for name in wanted if name not in out]
        resolved_rows: list[dict] = []

        async def worker(name: str) -> None:
            key = f"person:{name.lower()}"
            cached = await asyncio.to_thread(self.cache.get, "tmdb", key)
            if cached and cached.get("photo_url"):
                self._cache_hits += 1
                out[name] = cached["photo_url"]
                resolved_rows.append(
                    {
                        "name": name,
                        "photo_url": cached["photo_url"],
                        "tmdb_person_id": cached.get("tmdb_person_id"),
                    }
                )
                return
            photo = ""
            try:
                data = await self._get(client, "/search/person", query=name)
                results = data.get("results", []) or []
                best = max(
                    results,
                    key=lambda r: (
                        1000.0 if r.get("name", "").lower() == name.lower() else 0.0
                    )
                    + float(r.get("popularity", 0.0) or 0.0),
                    default=None,
                )
                if best and best.get("profile_path"):
                    photo = f"https://image.tmdb.org/t/p/w185{best['profile_path']}"
                    resolved_rows.append(
                        {
                            "name": name,
                            "photo_url": photo,
                            "tmdb_person_id": best.get("id"),
                        }
                    )
            except httpx.HTTPError:
                return  # transient — don't cache, retry next time
            await asyncio.to_thread(
                self.cache.set,
                "tmdb",
                key,
                {
                    "photo_url": photo,
                    "tmdb_person_id": best.get("id") if best else None,
                },
            )
            if photo:
                out[name] = photo

        try:
            await asyncio.gather(*(worker(n) for n in missing_names))
        finally:
            await self._flush_cache()
        image_saver = getattr(self.asset_store, "save_director_images", None)
        if image_saver and resolved_rows:
            try:
                self._asset_writes += await asyncio.to_thread(image_saver, resolved_rows)
            except Exception:
                pass
        return out

    async def director_movie_ids(self, names: list[str]) -> dict[str, set[int]]:
        """Resolve each favorite director's filmography with at most two cold calls.

        This avoids fetching credits for every watchlist candidate. Person ids and
        filmographies live in the durable TMDb cache, while portrait-bearing person
        records are also promoted to the shared director image table.
        """
        wanted = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
        if not wanted:
            return {}
        client = await _get_tmdb_client()
        person_keys = [f"person:{name.lower()}" for name in wanted]
        await self._prefetch_cache(person_keys)

        shared_assets: dict[str, dict] = {}
        asset_getter = getattr(self.asset_store, "get_director_assets", None)
        if asset_getter:
            try:
                shared_assets = await asyncio.to_thread(asset_getter, wanted)
            except Exception:
                shared_assets = {}

        identities: dict[str, dict] = {}

        async def resolve_person(name: str) -> None:
            key = f"person:{name.lower()}"
            cached = await asyncio.to_thread(self.cache.get, "tmdb", key)
            shared = shared_assets.get(name) or {}
            person_id = shared.get("tmdb_person_id") or (cached or {}).get(
                "tmdb_person_id"
            )
            photo_url = shared.get("photo_url") or (cached or {}).get("photo_url") or ""
            if not person_id:
                try:
                    data = await self._get(client, "/search/person", query=name)
                except httpx.HTTPError:
                    return
                best = max(
                    data.get("results", []) or [],
                    key=lambda row: (
                        1000.0
                        if row.get("name", "").lower() == name.lower()
                        else 0.0
                    )
                    + float(row.get("popularity", 0.0) or 0.0),
                    default=None,
                )
                if not best:
                    return
                person_id = best.get("id")
                if best.get("profile_path"):
                    photo_url = f"https://image.tmdb.org/t/p/w185{best['profile_path']}"
            if not person_id:
                return
            identity = {
                "tmdb_person_id": int(person_id),
                "photo_url": photo_url,
            }
            identities[name] = identity
            await asyncio.to_thread(self.cache.set, "tmdb", key, identity)

        await asyncio.gather(*(resolve_person(name) for name in wanted))
        movie_keys = [
            f"director_movies:{identity['tmdb_person_id']}"
            for identity in identities.values()
        ]
        await self._prefetch_cache(movie_keys)
        out: dict[str, set[int]] = {}

        async def resolve_movies(name: str, identity: dict) -> None:
            person_id = identity["tmdb_person_id"]
            key = f"director_movies:{person_id}"
            cached = await asyncio.to_thread(self.cache.get, "tmdb", key)
            if cached and isinstance(cached.get("movie_ids"), list):
                self._cache_hits += 1
                out[name] = {int(value) for value in cached["movie_ids"] if value}
                return
            try:
                data = await self._get(client, f"/person/{person_id}/movie_credits")
            except httpx.HTTPError:
                return
            movie_ids = {
                int(row["id"])
                for row in data.get("crew", []) or []
                if row.get("id") and row.get("job") == "Director"
            }
            out[name] = movie_ids
            await asyncio.to_thread(
                self.cache.set, "tmdb", key, {"movie_ids": sorted(movie_ids)}
            )

        try:
            await asyncio.gather(
                *(resolve_movies(name, identity) for name, identity in identities.items())
            )
        finally:
            await self._flush_cache()

        image_saver = getattr(self.asset_store, "save_director_images", None)
        rows = [
            {
                "name": name,
                "photo_url": identity.get("photo_url"),
                "tmdb_person_id": identity.get("tmdb_person_id"),
            }
            for name, identity in identities.items()
            if identity.get("photo_url")
        ]
        if image_saver and rows:
            try:
                self._asset_writes += await asyncio.to_thread(image_saver, rows)
            except Exception:
                pass
        return out
