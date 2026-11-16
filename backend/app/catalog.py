"""Shared film identities and TMDb metadata. Never requests Letterboxd pages."""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from .enrich import Enricher, _get_tmdb_client
from .cache import Cache


def normalized_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def film_slug(film_id: str) -> str:
    return "movie-" + str(UUID(str(film_id)))


def public_film(row: dict) -> dict:
    return {
        **row,
        "film_id": row["id"],
        "slug": film_slug(row["id"]),
        "year": row.get("release_year"),
        "poster_url": row.get("poster_url") or "",
    }


class Catalog:
    def __init__(self, service, settings):
        self.service, self.settings = service, settings
        # Sandbox stays entirely in process; production uses service-role RLS.
        from .sandbox import SandboxAuthService

        self.memory = isinstance(service, SandboxAuthService)
        if self.memory and not hasattr(service, "_catalog"):
            service._catalog = {}
            service._aliases = {}
        self.cache = Cache(settings.cache_db_path)

    def get(self, film_id: str) -> dict | None:
        film_id = str(UUID(str(film_id)))
        if self.memory:
            row = self.service._catalog.get(film_id)
        else:
            rows = (
                self.service._service_client()
                .table("films")
                .select("*")
                .eq("id", film_id)
                .limit(1)
                .execute()
                .data
                or []
            )
            row = rows[0] if rows else None
        return public_film(row) if row else None

    def by_alias(self, provider: str, external_id: str) -> dict | None:
        if self.memory:
            identity = self.service._aliases.get((provider, external_id))
        else:
            rows = (
                self.service._service_client()
                .table("film_aliases")
                .select("film_id")
                .eq("provider", provider)
                .eq("external_id", external_id)
                .limit(1)
                .execute()
                .data
                or []
            )
            identity = rows[0]["film_id"] if rows else None
        return self.get(identity) if identity else None

    def alias(self, provider: str, external_id: str, film_id: str) -> None:
        if self.memory:
            self.service._aliases[(provider, external_id)] = film_id
        else:
            self.service._service_client().table("film_aliases").upsert(
                dict(provider=provider, external_id=external_id, film_id=film_id),
                on_conflict="provider,external_id",
            ).execute()

    def store(self, values: dict) -> dict:
        key = values["catalog_key"]
        if self.memory:
            old = next(
                (v for v in self.service._catalog.values() if v["catalog_key"] == key),
                {},
            )
        else:
            old_rows = (
                self.service._service_client()
                .table("films")
                .select("*")
                .eq("catalog_key", key)
                .limit(1)
                .execute()
                .data
                or []
            )
            old = old_rows[0] if old_rows else {}
        allowed = {
            "id",
            "catalog_key",
            "tmdb_id",
            "title",
            "original_title",
            "release_year",
            "overview",
            "director",
            "genres",
            "keywords",
            "poster_url",
            "vote_average",
            "details_loaded",
            "updated_at",
        }
        row = {
            **old,
            **{k: v for k, v in values.items() if k in allowed and v is not None},
        }
        row.setdefault("id", str(uuid5(NAMESPACE_URL, "movienotes:film:" + key)))
        row.setdefault("genres", [])
        row.setdefault("keywords", [])
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self.memory:
            self.service._catalog[row["id"]] = row
        else:
            # ID is derived from the external key for concurrent first searches.
            # Existing migrated IDs win; upserts never replace identity.
            saved = (
                self.service._service_client()
                .table("films")
                .upsert(row, on_conflict="catalog_key")
                .execute()
                .data
                or []
            )
            if saved:
                row = saved[0]
        film = public_film(row)
        mirror = {
            k: row.get(k)
            for k in (
                "tmdb_id",
                "title",
                "release_year",
                "overview",
                "director",
                "genres",
                "keywords",
                "poster_url",
                "vote_average",
                "details_loaded",
            )
            if k in row
        }
        mirror.update(film_slug=film["slug"], matched=bool(row.get("tmdb_id")))
        if self.memory:
            self.service._posters[film["slug"]] = mirror
        else:
            self.service._service_client().table("film_posters").upsert(
                mirror, on_conflict="film_slug"
            ).execute()
        return film

    def list(self, query: str = "", limit: int = 24) -> list[dict]:
        if self.memory:
            rows = list(self.service._catalog.values())
            if query:
                q = normalized_title(query)
                rows = [
                    r
                    for r in rows
                    if q in normalized_title(r["title"])
                    or q in normalized_title(r.get("original_title") or "")
                ]
            rows.sort(key=lambda r: float(r.get("vote_average") or 0), reverse=True)
            rows = rows[:limit]
        else:
            q = self.service._service_client().table("films").select("*")
            if query:
                # ilike is a single filter value; never interpolate user input
                # into PostgREST's OR/filter grammar.
                q = q.ilike(
                    "title", "%" + query.replace("%", "").replace("_", "") + "%"
                )
            rows = q.order("vote_average", desc=True).limit(limit).execute().data or []
        return [public_film(r) for r in rows]

    def from_tmdb(self, row: dict, *, details: bool = False) -> dict:
        release = row.get("release_date") or ""
        values = dict(
            catalog_key=f"tmdb:{int(row['id'])}",
            tmdb_id=int(row["id"]),
            title=row.get("title") or row.get("original_title") or "",
            original_title=row.get("original_title") or "",
            release_year=int(release[:4]) if release[:4].isdigit() else None,
            vote_average=float(row.get("vote_average") or 0),
        )
        for name in ("overview",):
            if row.get(name):
                values[name] = row[name]
        if row.get("poster_path"):
            values["poster_url"] = (
                "https://image.tmdb.org/t/p/w500" + row["poster_path"]
            )
        if details:
            values.update(
                director=next(
                    (
                        x["name"]
                        for x in row.get("credits", {}).get("crew", [])
                        if x.get("job") == "Director"
                    ),
                    "",
                ),
                genres=[x["name"] for x in row.get("genres", [])],
                keywords=[
                    x["name"] for x in row.get("keywords", {}).get("keywords", [])
                ],
                details_loaded=True,
            )
        return self.store(values)

    async def search(
        self, query: str = "", locale: str = "tr"
    ) -> tuple[list[dict], bool]:
        local = await asyncio.to_thread(self.list, query)
        if len(local) >= 12 or not self.settings.has_tmdb:
            return local, False
        cache_key = hashlib.sha256(
            (locale + ":" + query.casefold()).encode()
        ).hexdigest()
        cached = await asyncio.to_thread(
            self.cache.get, "catalog_search", cache_key, 3600
        )
        if cached is not None:
            return cached, False
        enricher = Enricher(self.settings.tmdb_api_key, self.cache)
        try:
            client = await _get_tmdb_client()
            params = {"language": "tr-TR" if locale == "tr" else "en-US"}
            if query:
                params.update(query=query, include_adult=False)
            data = await enricher._get(
                client, "/search/movie" if query else "/movie/popular", **params
            )
            remote = []
            for row in data.get("results", [])[:20]:
                if row.get("adult") or not row.get("id"):
                    continue
                remote.append(await asyncio.to_thread(self.from_tmdb, row))
            unique = {r["id"]: r for r in local + remote}
            films = list(unique.values())[:24]
            await asyncio.to_thread(self.cache.set, "catalog_search", cache_key, films)
            return films, False
        except (httpx.HTTPError, ValueError, RuntimeError):
            # Saved search results remain useful during upstream failure.
            return local, True

    async def details(self, film_id: str) -> dict:
        film = await asyncio.to_thread(self.get, film_id)
        if film is None:
            raise LookupError("Film bulunamadı.")
        if (
            film.get("details_loaded")
            or not film.get("tmdb_id")
            or not self.settings.has_tmdb
        ):
            return film
        enricher = Enricher(self.settings.tmdb_api_key, self.cache)
        try:
            data = await enricher._get(
                await _get_tmdb_client(),
                f"/movie/{film['tmdb_id']}",
                append_to_response="credits,keywords",
                language="tr-TR",
            )
            return await asyncio.to_thread(self.from_tmdb, data, details=True)
        except (httpx.HTTPError, ValueError, RuntimeError):
            return film

    def import_placeholder(self, row: dict) -> dict:
        slug = (
            row.get("slug")
            or "unresolved-"
            + hashlib.sha256(
                (row.get("source_uri") or row["title"] + str(row.get("year"))).encode()
            ).hexdigest()[:24]
        )
        known = self.by_alias("letterboxd", slug)
        if known:
            return known
        film = self.store(
            dict(
                catalog_key="letterboxd:" + slug,
                title=row.get("title") or "Eşleşme bekleyen film",
                release_year=row.get("release_year") or row.get("year"),
            )
        )
        self.alias("letterboxd", slug, film["id"])
        return film

    async def candidates(self, film: dict, *, exact_year: bool = True) -> list[dict]:
        """Possible catalog identities for an imported title and year.

        TMDb is asked directly with the release year, so a crowded local
        catalog (``Z``, ``Pi``, ``Casablanca``) never hides the real film, and
        only the single accepted candidate is written to the catalog.
        """
        if not self.settings.has_tmdb:
            return await asyncio.to_thread(self.list, film["title"])
        enricher = Enricher(self.settings.tmdb_api_key, self.cache)
        try:
            data = await enricher._get(
                await _get_tmdb_client(),
                "/search/movie",
                **(
                    {"primary_release_year": int(film["year"])}
                    if exact_year
                    else {}
                ),
                query=film["title"],
                include_adult="false",
                language="en-US",
            )
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            raise RuntimeError(
                "Film eşleştirme servisi geçici olarak yanıt vermedi."
            ) from exc
        rows = []
        for row in data.get("results") or []:
            if row.get("adult") or not row.get("id"):
                continue
            release = row.get("release_date") or ""
            rows.append(
                {
                    "tmdb_id": int(row["id"]),
                    "title": row.get("title") or "",
                    "original_title": row.get("original_title") or "",
                    "year": int(release[:4]) if release[:4].isdigit() else None,
                    "vote_count": int(row.get("vote_count") or 0),
                    "_tmdb": row,
                }
            )
        return rows

    async def match(self, film: dict) -> dict | None:
        if film.get("tmdb_id"):
            return film
        # Titles and years must agree exactly and produce a single candidate.
        # Missing dates and remakes remain explicit unresolved matches.
        if not film.get("year"):
            return None
        names = {normalized_title(film["title"])}

        def pick(candidates, years):
            matches = [
                f
                for f in candidates
                if f.get("tmdb_id")
                and f.get("year") in years
                and names.intersection(
                    {
                        normalized_title(f["title"]),
                        normalized_title(f.get("original_title") or ""),
                    }
                )
            ]
            if not matches:
                return None, False
            # TMDb lists student shorts and home videos under famous titles
            # ("Arrival", 2016). A film with clearly established votes wins
            # over such namesakes; genuinely competing remakes stay unresolved.
            matches.sort(key=lambda f: f.get("vote_count") or 0, reverse=True)
            if len(matches) > 1:
                top = matches[0].get("vote_count") or 0
                runner_up = matches[1].get("vote_count") or 0
                if top < 50 or top < 10 * max(runner_up, 1):
                    return None, True
            return matches[0], True

        year = film["year"]
        found, seen = pick(await self.candidates(film), {year})
        if not seen and self.settings.has_tmdb:
            # Letterboxd and TMDb can date a film by different premieres
            # (Casablanca 1942/1943, Split 2016/2017): allow one year apart.
            found, _ = pick(
                await self.candidates(film, exact_year=False),
                {year - 1, year, year + 1},
            )
        if not found:
            return None
        if "_tmdb" in found:
            return await asyncio.to_thread(self.from_tmdb, found["_tmdb"])
        return found
