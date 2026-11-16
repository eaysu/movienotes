"""Seed additional shared films from TMDb after native_membership.sql is applied.

Run on the deployment host: python -m scripts.warm_catalog --pages 5
No member data, Letterboxd scraping, or LLM requests are involved.
"""

import argparse
import asyncio
import json

from app.auth import AuthService
from app.catalog import Catalog
from app.config import get_settings
from app.enrich import Enricher, _get_tmdb_client, close_tmdb_client


async def warm(pages: int):
    settings = get_settings()
    if not settings.has_auth or not settings.has_tmdb:
        raise RuntimeError("Supabase ve TMDb yapılandırması gerekiyor.")
    catalog = Catalog(AuthService(settings), settings)
    enricher = Enricher(settings.tmdb_api_key, catalog.cache)
    saved = set()
    try:
        client = await _get_tmdb_client()
        for endpoint in ("/movie/popular", "/movie/top_rated"):
            for page in range(1, pages + 1):
                response = await enricher._get(
                    client, endpoint, page=page, language="tr-TR"
                )
                for movie in response.get("results", []):
                    if movie.get("adult") or not movie.get("id"):
                        continue
                    film = await asyncio.to_thread(catalog.from_tmdb, movie)
                    saved.add(film["id"])
                await asyncio.sleep(0.35)
        print(json.dumps({"saved_films": len(saved), "pages_per_source": pages}))
    finally:
        await close_tmdb_client()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, choices=range(1, 21), default=5)
    asyncio.run(warm(parser.parse_args().pages))
