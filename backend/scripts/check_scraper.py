#!/usr/bin/env python3
"""Direct Letterboxd scraper canary; no proxy or paid service is used.

Usage:
    python -m scripts.check_scraper <letterboxd_username>
    python -m scripts.check_scraper <letterboxd_username> --list films --pages 2

Exit code 0 means the requested window returned parseable films without a
block. It does not mean the entire archive was imported. Run on the actual
deployment host: a successful GitHub/local probe cannot establish Render's
outbound access. Intended for low-frequency checks, not frequent uptime probes.
"""

import argparse
import asyncio
import json
import time

from app.scraper import ScrapeError, scrape_films, scrape_watchlist


async def check(username: str, *, list_type: str = "watchlist", pages: int = 1) -> int:
    started = time.perf_counter()
    try:
        scrape = scrape_films if list_type == "films" else scrape_watchlist
        result = await scrape(
            username,
            max_pages=pages,
            max_retries=2,
        )
        films, complete = result
    except ScrapeError as exc:
        print(json.dumps({
            "ok": False,
            "username": username,
            "list": list_type,
            "error_code": exc.code,
            "error": str(exc),
        }))
        return 1

    payload = {
        "ok": bool(films and complete),
        "username": username,
        "list": list_type,
        "pages_requested": pages,
        "next_page": getattr(result, "next_page", None),
        "film_count": len(films),
        "complete": complete,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "sample_slug": films[0].slug if films else None,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Check current Letterboxd parser health")
    parser.add_argument("username", help="Public Letterboxd username with a non-empty watchlist")
    parser.add_argument("--list", choices=("watchlist", "films"), default="watchlist")
    parser.add_argument("--pages", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    return asyncio.run(check(
        args.username.strip().lstrip("@").lower(), list_type=args.list, pages=args.pages
    ))


if __name__ == "__main__":
    raise SystemExit(main())
