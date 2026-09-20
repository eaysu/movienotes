"""The signup journey, played end to end against the real guards.

Every other auth test patches ``_enforce_auth_rate_limit`` away, so nothing
ever measured what a first-time member actually spends to get an account. This
file leaves the limiters in place and walks the journey the way a person walks
it: register, paste the code into the Letterboxd bio, come back, get it wrong
once because the bio had not saved yet, try again, and enter the app.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main
from app.auth import (
    Account,
    AuthService,
    AuthSession,
    InvalidCredentialsError,
    OwnershipPendingError,
    OwnershipProofError,
    RegistrationChallenge,
)
from app.scraper import ScrapedFilm, ScrapedProfile


def _settings(**overrides):
    values = {
        "has_auth": True,
        "has_supabase": True,
        "supabase_url": "https://project.supabase.co",
        "supabase_key": "service-key",
        "supabase_anon_key": "anon-key",
        "auth_identity_secret": "test-identity-secret",
        "auth_cookie_secure": True,
        "auth_session_max_age": 604800,
        "auth_session_short_max_age": 86400,
        "scrape_max_retries": 1,
        "dev_login_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Signup:
    """A stand-in for AuthService that can play one registration in memory."""

    CODE = "MOVIENOTES-ABC123"

    def __init__(self, username="new_member"):
        self.username = username
        self.password = ""
        self.account = Account(
            id=11,
            auth_user_id="00000000-0000-0000-0000-00000000000b",
            username=username,
            display_name="New Member",
        )

    # ── the three calls the signup endpoints make ──────────────────────────
    def start_registration(self, username, password, profile=None, *, ip_hash=""):
        self.password = password
        return RegistrationChallenge(username, self.CODE, "2099-01-01T00:00:00+00:00")

    def verify_ownership(self, username, code, profile, *, kind="register", ip_hash=""):
        if code.upper() != self.CODE:
            raise OwnershipProofError("Doğrulama kodu geçersiz.")
        if code.upper() not in (profile.bio or "").upper():
            raise OwnershipPendingError("Kodu Letterboxd biyografinde henüz göremedik.")
        return self.account

    def login(self, username, password, *, ip_hash=""):
        if username != self.username or password != self.password:
            raise InvalidCredentialsError("Kullanıcı adı veya parola hatalı.")
        return AuthSession(
            account=self.account,
            access_token="new-member-access",
            refresh_token="new-member-refresh",
            expires_in=3600,
        )

    def current_account(self, _token):
        return self.account


def _profile(bio=""):
    return ScrapedProfile(
        username="new_member",
        display_name="New Member",
        avatar_url="https://a.ltrbxd.com/avatar.jpg",
        bio=bio,
        favorite_films=[ScrapedFilm(title="Stalker", slug="stalker", year=1979)],
        stats={"films": 563, "this_year": 25, "lists": 4},
    )


class SignupJourneyTests(unittest.TestCase):
    """Friction is measured in requests: how many can a signup afford to spend?"""

    def setUp(self):
        # Module-level limiters outlive a test, so each journey starts clean.
        for limiter in (main._auth_rate_limiter, main._auth_failure_limiter):
            limiter._buckets.clear()
        self.service = _Signup()
        self.bio = {"text": ""}
        self.scrape = AsyncMock(side_effect=lambda *a, **k: _profile(self.bio["text"]))

    def _client(self, ip="203.0.113.5"):
        return TestClient(
            main.app,
            base_url="https://testserver",
            headers={"CF-Connecting-IP": ip},
        )

    def _patched(self):
        return (
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._auth_service", return_value=self.service),
            patch("app.main.scrape_profile", new=self.scrape),
        )

    def _register(self, client, username="new_member"):
        return client.post(
            "/api/auth/register/start",
            json={
                "username": username,
                "password": "a-long-enough-password",
                "password_confirm": "a-long-enough-password",
            },
        )

    def _verify(self, client, username="new_member"):
        return client.post(
            "/api/auth/register/verify",
            json={
                "username": username,
                "code": _Signup.CODE,
                "password": "a-long-enough-password",
            },
        )

    def test_the_verified_member_is_signed_in_by_the_same_request(self):
        """No second trip to the login form once ownership is proven."""
        start_patch, service_patch, scrape_patch = self._patched()
        with start_patch, service_patch, scrape_patch, self._client() as client:
            started = self._register(client)
            self.bio["text"] = f"cinema and {_Signup.CODE} enjoyer"
            verified = self._verify(client)

        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["verification_code"], _Signup.CODE)
        self.assertEqual(verified.status_code, 200)
        self.assertTrue(verified.json()["logged_in"])
        self.assertEqual(verified.json()["account"]["username"], "new_member")

    def test_rechecking_an_unsaved_bio_does_not_lock_the_member_out(self):
        """The most common signup moment: the bio edit has not landed yet.

        Someone tapping "Doğrula" while Letterboxd catches up is the normal
        case, not an attack. Turning that into "Çok fazla hesap isteği
        gönderildi" strands a member who has already chosen a password, with
        nothing to do but wait fifteen minutes.
        """
        start_patch, service_patch, scrape_patch = self._patched()
        with start_patch, service_patch, scrape_patch, self._client() as client:
            self.assertEqual(self._register(client).status_code, 200)

            # Six impatient checks before the bio saves — more than the five
            # the challenge allows as *attempts*, because these are not
            # attempts. Each one says what is missing.
            for _ in range(6):
                pending = self._verify(client)
                self.assertEqual(pending.status_code, 409, pending.text)
                self.assertIn("biyografi", pending.json()["detail"].lower())

            self.bio["text"] = _Signup.CODE
            final = self._verify(client)

        self.assertEqual(final.status_code, 200, final.text)
        self.assertTrue(final.json()["logged_in"])

    def test_a_shared_address_does_not_make_one_signup_block_the_next(self):
        """Carrier NAT and campus Wi-Fi put strangers on one address."""
        start_patch, service_patch, scrape_patch = self._patched()
        codes = []
        with start_patch, service_patch, scrape_patch:
            for name in ("first_fan", "second_fan", "third_fan"):
                with self._client(ip="198.51.100.7") as client:
                    codes.append(self._register(client, name).status_code)

        self.assertEqual(codes, [200, 200, 200])

    def test_repeated_wrong_passwords_still_run_out_of_budget(self):
        """Loosening the honest path must not open the login form to guessing."""
        start_patch, service_patch, scrape_patch = self._patched()
        self.service.password = "the-real-password"
        statuses = []
        with start_patch, service_patch, scrape_patch, self._client(ip="192.0.2.99") as client:
            for _ in range(12):
                statuses.append(
                    client.post(
                        "/api/auth/login",
                        json={"username": "new_member", "password": "guess-guess-guess"},
                    ).status_code
                )

        self.assertIn(401, statuses)
        self.assertIn(429, statuses, "a password guesser must eventually be stopped")
        self.assertLessEqual(
            statuses.index(429), 10, "the guessing budget must stay small"
        )


class _FakeTable:
    """The two auth_challenges/users calls verify_ownership makes, recorded."""

    def __init__(self, store, name):
        self.store, self.name = store, name
        self._update = None

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def update(self, values):
        self._update = values
        return self

    def delete(self):
        return self

    def insert(self, *_a, **_k):
        return self

    def execute(self):
        if self._update is not None:
            self.store["writes"].append((self.name, self._update))
            self._update = None
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=self.store["rows"].get(self.name, []))


class ChallengeAttemptTests(unittest.TestCase):
    """Five re-checks used to kill a registration that was never wrong."""

    def _service(self):
        store = {
            "writes": [],
            "rows": {
                "users": [{"id": 5, "auth_user_id": "u5", "username": "new_member"}],
                "auth_challenges": [{
                    "id": "challenge-1",
                    "code_hash": "",
                    "attempts": 0,
                    "expires_at": "2099-01-01T00:00:00+00:00",
                }],
            },
        }
        client = SimpleNamespace(table=lambda name: _FakeTable(store, name))
        service = AuthService(_settings(), client_factory=lambda *_a: client)
        store["rows"]["auth_challenges"][0]["code_hash"] = service.challenge_hash(
            _Signup.CODE
        )
        return service, store

    def test_waiting_for_the_bio_does_not_spend_an_attempt(self):
        service, store = self._service()

        with self.assertRaises(OwnershipPendingError):
            service.verify_ownership("new_member", _Signup.CODE, _profile(bio="no code here"))

        self.assertEqual(
            [write for write in store["writes"] if "attempts" in write[1]],
            [],
            "a correct code that has not appeared on the page yet is not an attempt",
        )

    def test_a_wrong_code_still_spends_one(self):
        service, store = self._service()

        with self.assertRaises(OwnershipProofError):
            service.verify_ownership("new_member", "MOVIENOTES-WRONG1", _profile(bio=""))

        self.assertEqual(
            [write[1] for write in store["writes"] if "attempts" in write[1]],
            [{"attempts": 1}],
        )


class OnboardingEscapeTests(unittest.TestCase):
    """A new member must never be stuck outside the app they just signed up for."""

    def setUp(self):
        from pathlib import Path

        self.js = (Path(__file__).resolve().parents[2] / "frontend" / "js" / "app.js").read_text()

    def test_a_slow_bootstrap_opens_a_way_into_the_app(self):
        block = self.js.split("async function startOnboarding", 1)[1]
        block = block.split("\nasync function ", 1)[0]

        # The presentation waits on one Letterboxd-backed request. If it is
        # slow, the door opens anyway instead of holding a fact card forever.
        self.assertIn("_obArmEscape()", block)
        self.assertIn("_obOfferEscape(", block)

    def test_the_reconnect_loop_gives_up_instead_of_spinning_forever(self):
        block = self.js.split("async function startOnboarding", 1)[1]
        block = block.split("\nasync function ", 1)[0]

        self.assertIn("retry >= OB_MAX_RETRIES", block)
        self.assertIn("startOnboarding(retry + 1)", block)
        self.assertIn("const OB_MAX_RETRIES", self.js)

    def test_leaving_early_does_not_mark_the_presentation_as_seen(self):
        """Escaping a stuck screen must not cost the member their slides."""
        self.assertIn(
            "_obEscapeOnly ? finishOnboarding() : completeOnboarding()", self.js
        )
        # And once the slides do play, the button completes onboarding again.
        reveal = self.js.split("function _obShowRevealSlide", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("if (last) _obEscapeOnly = false;", reveal)


class MemberActionBudgetTests(unittest.TestCase):
    """Letters, Blends, blocks and reports used to share the signup budget."""

    def setUp(self):
        for limiter in (main._auth_rate_limiter, main._auth_failure_limiter,
                        main._member_action_limiter):
            limiter._buckets.clear()

    def test_sending_letters_does_not_spend_the_signup_budget(self):
        account = Account(id=3, auth_user_id="u3", username="letter_fan")
        service = SimpleNamespace(
            current_account=lambda _token: account,
            send_letter=lambda *a, **k: "letter-id",
        )
        statuses = []
        with (
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._auth_service", return_value=service),
            TestClient(main.app, base_url="https://testserver") as client,
        ):
            for index in range(6):
                statuses.append(
                    client.post(
                        "/api/letters",
                        headers={
                            "Cookie": "mb_access=letter-token; mb_csrf=csrf-token",
                            "X-CSRF-Token": "csrf-token",
                        },
                        json={"recipient_username": "pen_pal", "body": f"merhaba {index}"},
                    ).status_code
                )

        self.assertNotIn(429, statuses, statuses)

    def test_one_member_cannot_spend_another_members_action_budget(self):
        """Two sessions on one address must hold two separate buckets."""
        account = Account(id=4, auth_user_id="u4", username="blocker")
        service = SimpleNamespace(
            current_account=lambda _token: account,
            block_user=lambda *a, **k: None,
        )

        def block(client, token):
            return client.post(
                "/api/users/someone/block",
                headers={
                    "Cookie": f"mb_access={token}; mb_csrf=csrf-token",
                    "X-CSRF-Token": "csrf-token",
                },
            ).status_code

        with (
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._auth_service", return_value=service),
            TestClient(main.app, base_url="https://testserver",
                       headers={"CF-Connecting-IP": "198.51.100.30"}) as client,
        ):
            spent = [block(client, "heavy-session") for _ in range(40)]
            neighbour = block(client, "quiet-session")

        self.assertIn(429, spent, "an unbounded action loop must still be stopped")
        self.assertNotEqual(neighbour, 429, "and it must only cost its own session")


if __name__ == "__main__":
    unittest.main()
