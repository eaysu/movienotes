"""The throwaway session: sign in as any public profile, keep nothing.

`python -m scripts.sandbox` runs the real app against an in-memory store. These
tests hold it to the two promises that make it useful: a name is all it asks
for, and nothing it learns outlives the process. They also check the guard that
keeps the door shut anywhere but this machine.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from app.sandbox import SandboxAuthService
from app.scraper import ScrapedFilm, ScrapedProfile

ROOT = Path(__file__).resolve().parents[1]


def _profile(username="tarkovskyfan"):
    return ScrapedProfile(
        username=username,
        display_name="Tarkovsky Fan",
        avatar_url="https://a.ltrbxd.com/avatar.jpg",
        bio="just films",
        favorite_films=[
            ScrapedFilm(title="Stalker", slug="stalker", year=1979),
            ScrapedFilm(title="Solaris", slug="solaris", year=1972),
        ],
        stats={"films": 563, "this_year": 25, "lists": 4},
    )


class SandboxStoreTests(unittest.TestCase):
    """The store stands in for Supabase, so it has to answer like Supabase."""

    def setUp(self):
        self.service = SandboxAuthService()
        self.account, self.token = self.service.open_session(_profile())

    def test_a_scraped_profile_becomes_the_whole_identity(self):
        self.assertEqual(self.account.username, "tarkovskyfan")
        self.assertEqual(self.account.letterboxd_stats["films"], 563)

        profile = self.service.get_profile(self.account)
        self.assertEqual(
            [film["title"] for film in profile["favorite_films"]],
            ["Stalker", "Solaris"],
        )

    def test_only_this_session_s_token_opens_it(self):
        self.assertEqual(self.service.current_account(self.token), self.account)
        with self.assertRaises(Exception):
            self.service.current_account("some-other-token")

    def test_films_are_written_as_the_sweep_writes_and_read_as_it_reads(self):
        """The pipeline writes `slug` and reads `film_slug`; both must work."""
        self.service.save_watched_films(1, [
            {"slug": "stalker", "title": "Stalker", "year": 1979, "watched_rank": 1,
             "user_rating": 5.0, "director": "Andrei Tarkovsky", "last_seen_run_id": "r1"},
            {"slug": "solaris", "title": "Solaris", "year": 1972, "watched_rank": 0,
             "last_seen_run_id": "r1"},
        ])

        self.assertEqual(self.service.count_watched_films(1), 2)
        self.assertEqual(self.service.get_watched_slugs(1), {"stalker", "solaris"})
        # Recency order is the sweep's running rank, lowest first.
        self.assertEqual(
            [row["title"] for row in self.service.list_recent_watched(1)],
            ["Solaris", "Stalker"],
        )
        self.assertEqual(
            self.service.watched_film_by_slug(1, "stalker")["director"],
            "Andrei Tarkovsky",
        )

    def test_a_film_the_newest_crawl_did_not_see_is_retired(self):
        """The same rule the SQL enforces: a removed diary entry goes inactive."""
        self.service.save_watched_films(1, [
            {"slug": "stalker", "title": "Stalker", "last_seen_run_id": "old-run"},
            {"slug": "solaris", "title": "Solaris", "last_seen_run_id": "new-run"},
        ])

        dropped = self.service.finalize_sync_run(1, "new-run")

        self.assertEqual(dropped, 1)
        self.assertEqual(self.service.get_watched_slugs(1), {"solaris"})

    def test_the_director_dropdown_is_filled_from_the_archive(self):
        """Reported: the scan finished and the profile's lists stayed empty."""
        self.service.save_watched_films(1, [
            {"slug": "poor-things", "title": "Poor Things",
             "director": "Yorgos Lanthimos", "user_rating": 4.5, "watched_rank": 3},
            {"slug": "the-lobster", "title": "The Lobster",
             "director": "Yorgos Lanthimos", "user_rating": 5.0, "watched_rank": 9},
            {"slug": "stalker", "title": "Stalker", "director": "Andrei Tarkovsky"},
        ])

        films = self.service.list_director_films(1, "Yorgos Lanthimos")

        # Highest rating first, exactly as the production query orders it.
        self.assertEqual([film["title"] for film in films], ["The Lobster", "Poor Things"])
        # And the name comes back from a taste snapshot, so case must not matter.
        self.assertEqual(len(self.service.list_director_films(1, "yorgos lanthimos")), 2)

    def test_a_surface_this_session_cannot_fill_answers_empty(self):
        """A solo visitor has no feed; that is empty, not broken."""
        self.assertEqual(self.service.list_feed(self.account)["posts"], [])
        self.assertEqual(self.service.list_letters(self.account), [])
        self.assertEqual(self.service.count_unread_letters(self.account), 0)
        self.assertEqual(self.service.diary_sync_candidates(), [])

    def test_closing_the_session_leaves_nothing_behind(self):
        self.service.save_watched_films(1, [{"slug": "stalker", "title": "Stalker"}])

        self.service.revoke()

        self.assertEqual(self.service.get_watched_slugs(1), set())
        self.assertIsNone(self.service.account)


# The route is registered at import time behind the flag, so exercising it
# means importing the app with the flag set. That happens in its own process,
# which is also the honest shape of the thing: the sandbox is a separate run.
_JOURNEY = """
import json, os, sys
sys.path.insert(0, %r)
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app import main
from app.scraper import ScrapedFilm, ScrapedProfile

profile = ScrapedProfile(
    username="tarkovskyfan", display_name="Tarkovsky Fan",
    avatar_url="https://a.ltrbxd.com/avatar.jpg", bio="",
    favorite_films=[ScrapedFilm(title="Stalker", slug="stalker", year=1979)],
    stats={"films": 563, "this_year": 25},
)
out = {}
with patch("app.main.scrape_profile", new=AsyncMock(return_value=profile)), \
     TestClient(main.app, base_url="http://127.0.0.1") as client:
    out["health"] = client.get("/api/health").json()
    started = client.post("/api/sandbox/session", json={"username": "TarkovskyFan"})
    out["session"] = started.status_code
    out["account"] = started.json().get("account", {}).get("username")
    out["cookies"] = sorted(client.cookies.keys())
    out["me"] = client.get("/api/auth/me").status_code
    out["favorites"] = [
        film["title"] for film in client.get("/api/profile/me").json()["favorite_films"]
    ]
    out["areas"] = {
        path: client.get(path).status_code
        for path in ("/api/feed?scope=community", "/api/letters", "/api/blends",
                     "/api/notifications", "/api/sinefil-alani",
                     "/api/profile/social-stats", "/api/blends/pending-count")
    }
    out["onboarding"] = client.post(
        "/api/profile/onboarding-complete",
        headers={"X-CSRF-Token": client.cookies.get("mb_csrf")},
    ).status_code
print("RESULT " + json.dumps(out))
"""


class SandboxSessionRouteTests(unittest.TestCase):
    """The route only exists when the flag is on, and only from this machine."""

    def test_the_route_does_not_exist_without_the_flag(self):
        from app import main

        paths = {route.path for route in main.app.routes}
        # The suite runs unflagged, so the module-level guard must have held.
        self.assertNotIn("/api/sandbox/session", paths)

    def test_a_name_alone_opens_a_session_that_reaches_every_area(self):
        import json
        import subprocess

        env = {
            **os.environ,
            "SANDBOX_MODE": "true",
            "SUPABASE_URL": "", "SUPABASE_KEY": "", "SUPABASE_ANON_KEY": "",
            "AUTH_COOKIE_SECURE": "false",
            "DIARY_SCAN_ENABLED": "false",
            "BULLETIN_ENABLED": "false",
            "DATA_DIR": self.enterContext(tempfile.TemporaryDirectory()),
        }
        result = subprocess.run(
            [sys.executable, "-c", _JOURNEY % str(ROOT)],
            capture_output=True, text=True, timeout=120, env=env, cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        line = next(l for l in result.stdout.splitlines() if l.startswith("RESULT "))
        out = json.loads(line[len("RESULT "):])

        self.assertTrue(out["health"]["sandbox"])
        self.assertTrue(out["health"]["auth_enabled"])
        self.assertFalse(out["health"]["supabase_enabled"])
        # A name and nothing else. No password was sent, and one arrived signed in.
        self.assertEqual(out["session"], 200)
        self.assertEqual(out["account"], "tarkovskyfan")
        self.assertIn("mb_access", out["cookies"])
        self.assertEqual(out["me"], 200)
        self.assertEqual(out["favorites"], ["Stalker"])
        self.assertEqual(out["onboarding"], 200)
        self.assertEqual(
            {status for status in out["areas"].values()}, {200}, out["areas"]
        )


class SandboxScriptTests(unittest.TestCase):
    """What the script promises on the command line."""

    def setUp(self):
        self.source = (ROOT.parent / "scripts" / "sandbox.py").read_text()

    def test_the_scrape_cache_is_temporary_too(self):
        """Otherwise "closing it deletes everything" is false of every page read."""
        self.assertIn('"DATA_DIR": cache_dir', self.source)
        self.assertIn("tempfile.mkdtemp", self.source)
        self.assertIn("shutil.rmtree(cache_dir", self.source)

    def test_supabase_is_not_merely_unused_but_unreachable(self):
        self.assertIn('"SUPABASE_URL": ""', self.source)
        self.assertIn('"SUPABASE_KEY": ""', self.source)
        self.assertIn('"SANDBOX_MODE": "true"', self.source)

    def test_only_api_keys_are_taken_from_the_env_file(self):
        block = self.source.split("def _load_api_keys", 1)[1].split("\ndef ", 1)[0]

        self.assertIn("TMDB_API_KEY", block)
        self.assertIn("OPENAI_API_KEY", block)
        for secret in ("SUPABASE_KEY", "AUTH_IDENTITY_SECRET", "SUPABASE_ANON_KEY"):
            self.assertNotIn(secret, block, f"{secret} must not be read here")


class SandboxIsNotADeployedModeTests(unittest.TestCase):
    def setUp(self):
        self.main = (ROOT / "app" / "main.py").read_text()
        self.config = (ROOT / "app" / "config.py").read_text()

    def test_the_route_refuses_a_caller_from_off_this_machine(self):
        block = self.main.split('@app.post("/api/sandbox/session")', 1)[1]
        block = block.split("\n@app.", 1)[0]

        self.assertIn('request.client.host', block)
        self.assertIn('raise HTTPException(status_code=404', block)
        # And it refuses to run against a real database even if the flag is on.
        self.assertIn("isinstance(service, SandboxAuthService)", block)

    def test_the_flag_defaults_off_and_says_why(self):
        block = self.config.split("sandbox_mode", 1)[0]

        self.assertIn("sandbox_mode: bool = False", self.config)
        self.assertIn("Never set this in a deployed environment", block)


class SandboxSignInScreenTests(unittest.TestCase):
    """The promise on screen: a name, and no password field anywhere."""

    def setUp(self):
        frontend = ROOT.parent / "frontend"
        self.html = (frontend / "index.html").read_text()
        self.app_js = (frontend / "js" / "app.js").read_text()
        self.auth_js = (frontend / "js" / "auth.js").read_text()

    def test_the_form_asks_for_a_name_and_nothing_else(self):
        form = self.html.split('id="sandbox-form"', 1)[1].split("</form>", 1)[0]

        self.assertIn('id="sandbox-username"', form)
        self.assertNotIn('type="password"', form)
        self.assertIn("hiçbir şey kaydedilmez", form)

    def test_the_shell_switches_only_when_the_server_says_so(self):
        boot = self.app_js.split("async function boot()", 1)[1].split("\n}", 1)[0]

        self.assertIn("_sandbox = Boolean(health?.sandbox)", boot)
        self.assertIn("setAuthMode('sandbox')", boot)

    def test_the_sandbox_mode_hides_every_account_surface(self):
        block = self.auth_js.split("if (mode === 'sandbox')", 1)[1].split("return;", 1)[0]

        for panel in ("login-form", "register-form", "verify-panel", "reset-panel", "auth-tabs"):
            self.assertIn(panel, block, panel)

    def test_signing_in_plays_the_real_onboarding(self):
        """The point of the script is to watch onboarding, so it must run."""
        block = self.app_js.split("async function startSandboxSession", 1)[1]
        block = block.split("\nasync function ", 1)[0]

        self.assertIn("/api/sandbox/session", block)
        self.assertIn("fromRegistration: true", block)


class RunTestScriptTests(unittest.TestCase):
    """./run-test.sh is the one command, so it carries the same promises."""

    def setUp(self):
        self.path = ROOT.parent / "run-test.sh"
        self.source = self.path.read_text()

    def test_it_is_executable_and_parses(self):
        import subprocess

        self.assertTrue(os.access(self.path, os.X_OK), "run-test.sh is not executable")
        parsed = subprocess.run(["bash", "-n", str(self.path)], capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_the_interpreter_is_chosen_by_what_it_can_import(self):
        """Caught live: `python3` on macOS is Xcode's, with no dependencies.

        Picking by name gave a traceback about pydantic_settings, which reads
        as a broken script rather than as the wrong Python.
        """
        self.assertIn("import fastapi, uvicorn, pydantic_settings", self.source)
        self.assertIn("Bağımlılıkları kurulu bir Python bulunamadı", self.source)

    def test_chrome_opens_only_once_the_server_answers(self):
        """A fixed sleep hands the visitor a "site can't be reached" page."""
        block = self.source.split("open_browser()", 1)[1].split("\n}", 1)[0]

        self.assertIn("/api/health", block)
        self.assertIn('open -a "Google Chrome"', block)
        # And a machine without Chrome still gets a browser.
        self.assertIn("google-chrome", block)
        self.assertIn("xdg-open", block)

    def test_the_server_runs_in_the_foreground_so_ctrl_c_cleans_up(self):
        """`exec` or a background server would strand the temporary folder."""
        self.assertNotIn("exec ", self.source)
        self.assertIn("--no-browser", self.source)


class SandboxFeedTests(unittest.TestCase):
    """A solo session still has one real source of notes: their own diary."""

    def setUp(self):
        self.service = SandboxAuthService()
        self.account, _ = self.service.open_session(_profile())

    def test_written_diary_entries_land_in_the_feed(self):
        written = self.service.import_diary_entries(1, [
            {"source_key": "k1", "film_slug": "stalker", "film_title": "Stalker",
             "body": "Still the best thing I have seen all year.",
             "created_at": "2026-09-01T12:00:00+00:00"},
            {"source_key": "k2", "film_slug": "solaris", "film_title": "Solaris",
             "body": "Colder than I remembered.",
             "created_at": "2026-09-05T12:00:00+00:00"},
        ])

        self.assertEqual(written, 2)
        posts = self.service.list_feed(self.account)["posts"]
        # Newest first, and each one carries the author and film the shell draws.
        self.assertEqual([post["film"]["title"] for post in posts], ["Solaris", "Stalker"])
        self.assertEqual(posts[0]["author"]["username"], "tarkovskyfan")
        self.assertTrue(posts[0]["mine"])

    def test_an_entry_without_a_comment_is_not_a_note(self):
        """The same rule as production: a rating alone has nothing to read."""
        written = self.service.import_diary_entries(1, [
            {"source_key": "k3", "film_slug": "mirror", "body": "   "},
        ])

        self.assertEqual(written, 0)
        self.assertEqual(self.service.list_feed(self.account)["posts"], [])

    def test_the_same_entry_does_not_land_twice(self):
        row = {"source_key": "k4", "film_slug": "mirror", "body": "Luminous."}

        self.service.import_diary_entries(1, [row])
        self.service.import_diary_entries(1, [row])

        self.assertEqual(len(self.service.list_feed(self.account)["posts"]), 1)


class RefreshDeclineTests(unittest.TestCase):
    """Caught live: a stale cookie produced a 500 instead of "sign in again"."""

    def test_an_unknown_token_is_declined_rather_than_half_answered(self):
        service = SandboxAuthService()
        service.open_session(_profile())

        self.assertIsNone(service.refresh("some-other-token"))

    def test_the_bootstrap_turns_a_declined_refresh_into_a_plain_401(self):
        main_py = (ROOT / "app" / "main.py").read_text()
        block = main_py.split("def _restore_account_from_device", 1)[1]
        block = block.split("\ndef ", 1)[0]

        # A refresh that declines by answering nothing is still a decline; it
        # used to reach the cookie writer and fail on `session.expires_in`.
        assert "if session is None:" in block
        assert "_clear_session_cookies(response)" in block
        self.assertLess(
            block.index("if session is None:"),
            block.index("_set_session_cookies(response, session"),
        )


class SharedAssetLookupTests(unittest.TestCase):
    """Caught live: "zevkime göre öner" died at the ranking stage."""

    def test_a_shared_store_that_answers_nothing_does_not_take_ranking_down(self):
        enrich = (ROOT / "app" / "enrich.py").read_text()
        block = enrich.split("def director_movie_ids", 1)[1].split("\n    async def ", 1)[0]

        # The shared pool is an optimisation. A store answering `None` rather
        # than raising slipped past the except and crashed on `.get`.
        assert "await asyncio.to_thread(asset_getter, wanted) or {}" in block


if __name__ == "__main__":
    unittest.main()
