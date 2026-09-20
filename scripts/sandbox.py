#!/usr/bin/env python3
"""Run Movienotes as a throwaway session against any public Letterboxd profile.

The app comes up with no accounts and no database. The sign-in screen asks only
for a Letterboxd name — there is no password, because there is nothing to
protect — and typing one scrapes that profile and plays the real onboarding on
it. Everything the session produces lives in memory and in a temporary cache
folder; both are gone when you stop the script with Ctrl-C.

Usage::

    python -m scripts.sandbox                 # http://127.0.0.1:8765
    python -m scripts.sandbox --port 9000
    python -m scripts.sandbox --keep-cache    # reuse scrapes between runs

TMDB_API_KEY and OPENAI_API_KEY are read from your .env as usual, so posters
and the written analysis work exactly as they do in production if they are set.
Nothing else from .env is used: Supabase is not contacted at all.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _load_api_keys() -> None:
    """Take only the two API keys from .env; leave every credential behind."""
    for name in (".env", ".env.local"):
        env_file = ROOT / name
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().upper()
            if key in {"TMDB_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL",
                       "OPENAI_ANALYSIS_MODEL"}:
                os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--keep-cache",
        action="store_true",
        help="keep the scrape cache between runs instead of deleting it",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    _load_api_keys()

    # A temporary DATA_DIR is what makes "close it and it is gone" true of the
    # scrape cache as well: without it, SQLite would quietly keep every page
    # this session read in the repository's own data folder.
    cache_dir = (
        str(ROOT / "data") if args.keep_cache
        else tempfile.mkdtemp(prefix="movienotes-sandbox-")
    )

    os.environ.update({
        "SANDBOX_MODE": "true",
        "DATA_DIR": cache_dir,
        # Supabase is not reachable from here, and must not be.
        "SUPABASE_URL": "", "SUPABASE_KEY": "", "SUPABASE_ANON_KEY": "",
        # Plain http on a loopback address, so the session cookie has to be
        # allowed to travel without TLS.
        "AUTH_COOKIE_SECURE": "false",
        "DEV_LOGIN_ENABLED": "false",
        # One person, one profile: the scheduled sweeps have no cohort to
        # serve and would only add noise. The on-entry diary read still runs,
        # and keeps more than the production three so the feed has something
        # to show in a session that lasts minutes rather than weeks.
        "BULLETIN_ENABLED": "false",
        "DIARY_SCAN_ENABLED": "false",
        "DIARY_SCAN_ENTRIES": "12",
    })

    sys.path.insert(0, str(BACKEND))
    import uvicorn

    url = f"http://{args.host}:{args.port}"
    # Bu satırlar uvicorn açılmadan önce görünmeli; stdout bir terminale
    # bağlı değilken tamponlanıp scriptin donduğu izlenimi veriyordu.
    print(f"\n  Movienotes sandbox → {url}", flush=True)
    print("  Bir Letterboxd kullanıcı adı yaz; parola sorulmaz.", flush=True)
    print(f"  Geçici veri: {cache_dir}", flush=True)
    print("  Ctrl-C ile kapat — her şey silinir.\n", flush=True)
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:  # pragma: no cover - the ordinary way to stop
        pass
    finally:
        if not args.keep_cache:
            shutil.rmtree(cache_dir, ignore_errors=True)
            print(f"\n  Silindi: {cache_dir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
