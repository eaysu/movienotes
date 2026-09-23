import asyncio
from types import SimpleNamespace

import pytest

from app import main
from app.scraper import ScrapeListResult, ScrapedFilm


class _Account:
    id = 42


class _Service:
    def __init__(self, watched):
        self.watched = watched

    def get_watched_slugs(self, _user_id):
        return self.watched


class _CatalogService(_Service):
    def get_film_assets(self, _slugs):
        return {
            "unseen": {
                "tmdb_id": 99,
                "poster_url": "https://image.tmdb.org/t/p/w500/unseen.jpg",
                "overview": "A restored overview.",
                "genres": ["Drama"],
                "director": "A Director",
                "keywords": ["memory"],
                "vote_average": 8.1,
                "matched": True,
                "details_loaded": True,
            }
        }


class _Cache:
    def __init__(self, value=None):
        self.value = value
        self.writes = []

    def get(self, _namespace, _key, _ttl):
        return self.value

    def set(self, namespace, key, value):
        self.writes.append((namespace, key, value))


def test_official_list_pool_never_returns_a_watched_film(monkeypatch):
    cache = _Cache()

    async def fake_list(*_args, **_kwargs):
        return ScrapeListResult(
            films=[
                ScrapedFilm("Already watched", 2001, "seen"),
                ScrapedFilm("New pick", 2002, "new-pick"),
            ],
            complete=True,
            next_page=2,
            exhausted=False,
            pages_fetched=1,
        )

    monkeypatch.setattr(main, "scrape_official_list", fake_list)
    monkeypatch.setattr(main._random, "sample", lambda pages, _count: [pages[0]])

    films = asyncio.run(main._official_list_pool(
        "official_top_500", _Service({"seen"}), _Account(), cache
    ))

    assert [film.slug for film in films] == ["new-pick"]
    assert cache.writes


def test_official_list_pool_uses_a_cached_page_without_scraping(monkeypatch):
    cache = _Cache([
        {"title": "Seen", "year": 1999, "slug": "seen"},
        {"title": "Unseen", "year": 2000, "slug": "unseen", "poster_url": "https://poster"},
    ])

    async def should_not_scrape(*_args, **_kwargs):
        raise AssertionError("a warm official-list page must not fetch Letterboxd")

    monkeypatch.setattr(main, "scrape_official_list", should_not_scrape)
    monkeypatch.setattr(main._random, "sample", lambda pages, _count: [pages[0]])

    films = asyncio.run(main._official_list_pool(
        "official_most_fans", _Service({"seen"}), _Account(), cache
    ))

    assert [film.slug for film in films] == ["unseen"]
    assert films[0].poster_url == "https://poster"


def test_official_list_pool_reuses_catalog_metadata(monkeypatch):
    cache = _Cache([
        {"title": "Unseen", "year": 2000, "slug": "unseen"},
    ])
    monkeypatch.setattr(main._random, "sample", lambda pages, _count: [pages[0]])

    films = asyncio.run(main._official_list_pool(
        "official_top_500", _CatalogService(set()), _Account(), cache
    ))

    assert films[0].poster_url == "https://image.tmdb.org/t/p/w500/unseen.jpg"
    assert films[0].overview == "A restored overview."
    assert films[0].director == "A Director"


def test_random_pick_hydration_uses_full_tmdb_match_for_official_rows(monkeypatch):
    class _Enricher:
        def __init__(self, *_args, **_kwargs):
            pass

        async def enrich(self, _films, *, include_details):
            assert include_details is True
            return [main.EnrichedFilm(
                title="Matched", slug="unseen", tmdb_id=99,
                poster_url="https://poster", overview="A real overview.",
                details_loaded=True,
            )]

    monkeypatch.setattr(main, "Enricher", _Enricher)
    chosen = asyncio.run(main._hydrate_random_picks(
        [main.EnrichedFilm(title="Unseen", slug="unseen")],
        SimpleNamespace(has_tmdb=True, tmdb_api_key="key"),
        object(),
        object(),
    ))

    assert chosen[0].tmdb_id == 99
    assert chosen[0].poster_url == "https://poster"
    assert chosen[0].overview == "A real overview."


def test_official_list_sources_are_the_only_extra_random_sources():
    assert main.RandomRequest(username="filmfan", source="community").source == "community"
    assert main.RandomRequest(username="filmfan", source="official_top_500").source == "official_top_500"
    with pytest.raises(ValueError):
        main.RandomRequest(username="filmfan", source="not-a-list")
