import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.scraper import ScrapeListResult
from scripts.check_scraper import check


def test_canary_fails_when_second_watched_page_is_blocked(capsys):
    partial = ScrapeListResult(
        films=[SimpleNamespace(slug="first-film")], complete=False,
        next_page=2, exhausted=False, pages_fetched=1,
    )
    scrape = AsyncMock(return_value=partial)
    with patch("scripts.check_scraper.scrape_films", scrape):
        assert asyncio.run(check("sample_user", list_type="films", pages=2)) == 1
    assert scrape.call_args.kwargs["max_pages"] == 2
    assert '"complete": false' in capsys.readouterr().out
