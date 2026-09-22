import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.auth import Account, AuthService, AuthSession, TransientStorageError, validate_password
from app.scraper import AccessBlockedError


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
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _account(username="film_fan"):
    return Account(
        id=7,
        auth_user_id="00000000-0000-0000-0000-000000000007",
        username=username,
        display_name="Film Fan",
    )


def test_password_confirmation_and_policy():
    assert validate_password("long-enough-password", "long-enough-password")
    with pytest.raises(ValueError, match="en az 10"):
        validate_password("short")
    with pytest.raises(ValueError, match="eşleşmiyor"):
        validate_password("long-enough-password", "different-password")


def test_synthetic_identity_is_stable_and_does_not_expose_username():
    service = AuthService(_settings(), client_factory=lambda *_args: None)
    first = service.identity_email("film_fan")
    assert first == service.identity_email("film_fan")
    assert first != service.identity_email("other_user")
    assert "film_fan" not in first
    # Marka MOVIENOTES oldu ama bu alan adı sabit kalmak zorunda: e-posta her
    # girişte kullanıcı adından üretiliyor, değişirse var olan hesapların Auth
    # kaydı bulunamaz. Testin işi bu kararı kilitlemek.
    assert first.endswith("@users.movieboxd.invalid")


def test_service_role_client_reuses_connection_pool_but_auth_client_does_not():
    created = []

    def factory(_url, key):
        client = SimpleNamespace(key=key, number=len(created) + 1)
        created.append(client)
        return client

    service = AuthService(_settings(), client_factory=factory)
    assert service._service_client() is service._service_client()
    assert len(created) == 1
    assert service._auth_client() is not service._auth_client()
    assert len(created) == 3


def test_short_account_cache_avoids_repeat_validation_and_can_be_invalidated():
    token = "unique-account-cache-token"
    account = _account("cached_fan")
    calls = []
    fake_service = SimpleNamespace(
        current_account=lambda value: calls.append(value) or account
    )

    def request_for(value):
        return Request({
            "type": "http",
            "method": "GET",
            "path": "/api/auth/me",
            "headers": [(b"cookie", f"mb_access={value}".encode())],
        })

    main._invalidate_account_cache(token)
    with patch("app.main._auth_service", return_value=fake_service):
        assert asyncio.run(main._require_account(request_for(token))) == account
        assert asyncio.run(main._require_account(request_for(token))) == account
        assert calls == [token]
        assert token not in repr(main._account_cache)

        main._invalidate_account_cache(token)
        assert asyncio.run(main._require_account(request_for(token))) == account
        assert calls == [token, token]
    main._invalidate_account_cache(token)


def test_temporary_auth_lookup_failure_is_not_reported_as_an_invalid_session():
    """A Render/Supabase disconnect must retain the member's refresh cookie."""
    fake_service = SimpleNamespace(
        current_account=lambda _token: (_ for _ in ()).throw(
            TransientStorageError("Oturum bağlantısı kısa süreli yanıt vermedi.")
        ),
        refresh=lambda _token: (_ for _ in ()).throw(
            TransientStorageError("Oturum yenileme bağlantısı kısa süreli yanıt vermedi.")
        ),
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        me = client.get("/api/auth/me", headers={"Cookie": "mb_access=transient-access-token"})
        refreshed = client.post(
            "/api/auth/refresh",
            headers={
                "Cookie": "mb_refresh=refresh-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert me.status_code == 503
    assert "Oturum bağlantısı" in me.json()["detail"]
    assert refreshed.status_code == 503
    assert not refreshed.headers.get_list("set-cookie")


def test_full_history_sync_schema_is_service_role_only():
    schema = (Path(__file__).parents[1] / "supabase" / "schema.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS public.user_watched_films (" in schema
    assert "CREATE TABLE IF NOT EXISTS public.profile_sync_jobs (" in schema
    assert "CREATE OR REPLACE FUNCTION public.upsert_watched_films(" in schema
    # New tables lock out browser roles like every other user table.
    for line in (
        "ALTER TABLE public.user_watched_films ENABLE ROW LEVEL SECURITY;",
        "ALTER TABLE public.profile_sync_jobs ENABLE ROW LEVEL SECURITY;",
        "REVOKE ALL ON TABLE public.user_watched_films FROM anon, authenticated;",
        "REVOKE ALL ON TABLE public.profile_sync_jobs FROM anon, authenticated;",
        "GRANT ALL ON TABLE public.user_watched_films TO service_role;",
        "GRANT ALL ON TABLE public.profile_sync_jobs TO service_role;",
        "REVOKE ALL ON FUNCTION public.upsert_watched_films(BIGINT, JSONB)",
        "GRANT EXECUTE ON FUNCTION public.upsert_watched_films(BIGINT, JSONB)",
        "CREATE TABLE IF NOT EXISTS public.film_posters (",
        "CREATE OR REPLACE FUNCTION public.upsert_film_posters(",
        "ALTER TABLE public.film_posters ENABLE ROW LEVEL SECURITY;",
        "REVOKE ALL ON TABLE public.film_posters FROM anon, authenticated;",
        "GRANT ALL ON TABLE public.film_posters TO service_role;",
        "GRANT EXECUTE ON FUNCTION public.upsert_film_posters(JSONB) TO service_role;",
        "CREATE TABLE IF NOT EXISTS public.director_images (",
        "CREATE OR REPLACE FUNCTION public.upsert_director_images(",
        "ALTER TABLE public.director_images ENABLE ROW LEVEL SECURITY;",
        "CREATE OR REPLACE FUNCTION public.claim_profile_sync_job(",
        "CREATE OR REPLACE FUNCTION public.finalize_profile_sync_run(",
        "COALESCE(last_seen_run_id = p_sync_run_id, FALSE)",
        "CREATE INDEX IF NOT EXISTS idx_film_posters_tmdb_id",
        "ALTER TABLE public.film_posters ALTER COLUMN poster_url DROP NOT NULL;",
        "ADD COLUMN IF NOT EXISTS overview TEXT NOT NULL DEFAULT '';",
        "details_loaded = public.film_posters.details_loaded OR EXCLUDED.details_loaded",
    ):
        assert line in schema


class _RecordingTable:
    def __init__(self, name, log):
        self.name = name
        self._log = log

    def select(self, columns):
        self._log.append((self.name, columns))
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return SimpleNamespace(data=[], count=0)


class _RecordingClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return _RecordingTable(name, self.calls)


def test_check_sync_schema_probes_only_the_new_tables_with_zero_rows():
    client = _RecordingClient()
    service = AuthService(_settings(), client_factory=lambda *_args: client)
    assert service.check_sync_schema() is True
    probed = {name for name, _cols in client.calls}
    assert probed == {
        "user_watched_films", "profile_sync_jobs", "director_images", "film_posters"
    }


def test_readiness_requires_discovery_visibility_column():
    client = _RecordingClient()
    service = AuthService(_settings(), client_factory=lambda *_args: client)

    assert service.check_schema() is True
    users_query = next(columns for name, columns in client.calls if name == "users")
    assert "discoverable" in users_query


def test_transient_cloudflare_storage_read_retries_then_returns_clear_error():
    service = AuthService(_settings(), client_factory=lambda *_args: None)
    attempts = []

    def unavailable():
        attempts.append(True)
        raise RuntimeError("Cloudflare 400 Bad Request: JSON could not be generated")

    with patch("app.auth.time.sleep") as sleep:
        with pytest.raises(TransientStorageError, match="Veri bağlantısı"):
            service._retry_storage_read(unavailable)

    assert len(attempts) == 3
    assert sleep.call_count == 2


@pytest.mark.parametrize(
    "message",
    [
        "httpx.RemoteProtocolError: Server disconnected",
        "httpx.RemoteProtocolError: <ConnectionTerminated error_code:1>",
        "httpx.WriteError: EOF occurred in violation of protocol (_ssl.c:2417)",
    ],
)
def test_transient_storage_read_retries_transport_disconnects(message):
    service = AuthService(_settings(), client_factory=lambda *_args: None)
    attempts = []

    def unavailable():
        attempts.append(True)
        raise RuntimeError(message)

    with patch("app.auth.time.sleep"):
        with pytest.raises(TransientStorageError, match="Veri bağlantısı"):
            service._retry_storage_read(unavailable)

    assert len(attempts) == 3


def test_login_sets_http_only_session_and_readable_csrf_cookies():
    session = AuthSession(
        account=_account(),
        access_token="access-token",
        refresh_token="refresh-token",
        expires_in=3600,
    )
    fake_service = SimpleNamespace(login=lambda *_args, **_kwargs: session)
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/auth/login",
            json={"username": "film_fan", "password": "long-enough-password"},
        )

    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    access = next(item for item in cookies if item.startswith("mb_access="))
    refresh = next(item for item in cookies if item.startswith("mb_refresh="))
    csrf = next(item for item in cookies if item.startswith("mb_csrf="))
    assert "HttpOnly" in access and "Secure" in access and "SameSite=lax" in access
    assert "HttpOnly" in refresh
    assert "HttpOnly" not in csrf and "Secure" in csrf


def test_existing_film_archive_is_never_replaced_by_an_empty_bootstrap_snapshot():
    """A failed incremental job must not make a mature account look new."""
    account = _account()
    saved = {
        "taste": {"sample_size": 564, "algorithm_version": "taste-v4-fav4-directors"},
        "favorite_films": [{"slug": "a-film", "title": "A Film"}],
    }
    fake_service = SimpleNamespace(
        get_profile=lambda _account: dict(saved),
        count_watched_films=lambda _uid: 564,
    )
    scrape = AsyncMock()
    with patch("app.main.scrape_profile", new=scrape):
        restored = asyncio.run(
            main._provisional_profile_sync(account, _settings(), fake_service, force=False)
        )

    assert restored["taste"]["sample_size"] == 564
    assert restored["account"]["username"] == "film_fan"
    scrape.assert_not_awaited()


def test_existing_archive_sync_skips_heavy_limit_and_does_not_restart_incremental_crawl():
    account = _account()
    stored = {
        "taste": {"sample_size": 564, "algorithm_version": "taste-v4-fav4-directors"},
        "favorite_films": [],
    }
    job = {
        "state": "failed", "phase": "aggregate", "scope": "incremental",
        "films_processed": 0, "films_total": 0,
    }
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        check_schema=lambda: True,
        check_sync_schema=lambda: True,
        get_sync_job=lambda _uid: job,
        count_watched_films=lambda _uid: 564,
        mark_sync_status=lambda *_args: None,
    )
    favorite_refresh = AsyncMock(return_value={"changed": False, "profile": dict(stored)})
    provisional = AsyncMock()
    heavy_limit = AsyncMock()
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._refresh_profile_favorites", new=favorite_refresh),
        patch("app.main._provisional_profile_sync", new=provisional),
        patch("app.main._enforce_heavy_rate_limit", new=heavy_limit),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/profile/sync",
            headers={
                "Cookie": "mb_access=existing-archive-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json()["taste"]["sample_size"] == 564
    favorite_refresh.assert_awaited_once()
    provisional.assert_not_awaited()
    heavy_limit.assert_not_awaited()


def test_profile_visit_does_not_start_a_daily_incremental_scrape():
    account = _account()
    job = {
        "state": "done", "phase": "done", "scope": "full",
        "films_processed": 564, "films_total": 564,
    }
    fake_service = SimpleNamespace(get_sync_job=lambda _uid: job)
    starter = AsyncMock()
    with (
        patch("app.main.profile_sync.is_running", return_value=False),
        patch("app.main.profile_sync.incremental_due", return_value=True),
        patch("app.main.profile_sync.ensure_started", new=starter),
    ):
        status = asyncio.run(main._profile_sync_status(account, fake_service))

    assert status["scope"] == "full"
    starter.assert_not_awaited()


def test_entry_sync_is_queued_without_holding_the_signed_in_shell():
    account = _account()
    fake_service = SimpleNamespace(current_account=lambda _token: account)
    scheduler = AsyncMock(return_value="queued")
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._schedule_entry_sync", new=scheduler),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/profile/entry-sync",
            headers={
                "Cookie": "mb_access=entry-sync-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "queued"}
    scheduler.assert_awaited_once()


def test_auth_me_restores_a_remembered_device_without_a_readable_csrf_cookie():
    """A cold installed PWA must enter the app from its durable cookie alone."""
    session = AuthSession(
        account=_account(),
        access_token="renewed-access-token",
        refresh_token="renewed-refresh-token",
        expires_in=3600,
    )
    fake_service = SimpleNamespace(
        refresh=lambda token: session if token == "device-refresh-token" else None,
    )
    with (
        patch("app.main.get_settings", return_value=_settings(auth_session_max_age=34560000)),
        patch("app.main._auth_service", return_value=fake_service),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        # No access or CSRF cookie: this models an app reopened after its
        # one-hour access token elapsed. The refresh token is httpOnly and
        # remains available to the server.
        response = client.get(
            "/api/auth/me",
            headers={"Cookie": "mb_refresh=device-refresh-token"},
        )

    assert response.status_code == 200
    assert response.json()["account"]["username"] == "film_fan"
    cookies = response.headers.get_list("set-cookie")
    assert any(cookie.startswith("mb_access=renewed-access-token") for cookie in cookies)
    assert any(cookie.startswith("mb_refresh=renewed-refresh-token") for cookie in cookies)
    # A missing marker denotes a pre-marker remembered device, not a short
    # session. 400 days is the browser's persistent-cookie ceiling.
    assert any(cookie.startswith("mb_remember=1") and "Max-Age=34560000" in cookie for cookie in cookies)


def test_auth_me_does_not_restore_a_cross_site_request():
    fake_service = SimpleNamespace(refresh=pytest.fail)
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.get(
            "/api/auth/me",
            headers={
                "Cookie": "mb_refresh=device-refresh-token",
                "Sec-Fetch-Site": "cross-site",
            },
        )

    assert response.status_code == 401


def test_account_mode_rejects_state_change_without_csrf_before_work_starts():
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._enforce_heavy_rate_limit", new=AsyncMock()),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post("/api/random", json={"username": "film_fan"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Güvenlik doğrulaması başarısız."


def test_register_password_mismatch_stops_before_scraping():
    scrape = AsyncMock()
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        patch("app.main.scrape_profile", new=scrape),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/auth/register/start",
            json={
                "username": "film_fan",
                "password": "long-enough-password",
                "password_confirm": "different-password",
            },
        )

    assert response.status_code == 422
    scrape.assert_not_awaited()


def test_registration_start_issues_code_without_waiting_for_letterboxd():
    scrape = AsyncMock(side_effect=AccessBlockedError("Letterboxd HTTP 403", status=403))
    challenge = SimpleNamespace(
        username="film_fan",
        verification_code="MOVIENOTES-ABC123",
        expires_at="2026-09-15T12:00:00+00:00",
    )
    fake_service = SimpleNamespace(start_registration=lambda *_args, **_kwargs: challenge)
    with (
        patch("app.main.get_settings", return_value=_settings(scrape_max_retries=3)),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        patch("app.main.scrape_profile", new=scrape),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/auth/register/start",
            json={
                "username": "film_fan",
                "password": "long-enough-password",
                "password_confirm": "long-enough-password",
            },
        )

    assert response.status_code == 200
    assert response.json()["verification_code"] == "MOVIENOTES-ABC123"
    scrape.assert_not_awaited()


def test_registration_verification_can_issue_session_without_second_login_request():
    account = _account()
    session = AuthSession(
        account=account,
        access_token="access-token",
        refresh_token="refresh-token",
        expires_in=3600,
    )
    fake_service = SimpleNamespace(
        verify_ownership=lambda *_args, **_kwargs: account,
        login=lambda *_args, **_kwargs: session,
    )
    scrape = AsyncMock(return_value=SimpleNamespace(bio="MOVIENOTES-ABC123"))
    with (
        patch("app.main.get_settings", return_value=_settings(scrape_max_retries=1)),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        patch("app.main.scrape_profile", new=scrape),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/auth/register/verify",
            json={
                "username": "film_fan",
                "code": "MOVIENOTES-ABC123",
                "password": "long-enough-password",
            },
        )

    assert response.status_code == 200
    assert response.json()["logged_in"] is True
    assert any(cookie.startswith("mb_access=") for cookie in response.headers.get_list("set-cookie"))
    scrape.assert_awaited_once_with(
        "film_fan", max_retries=1, resolve_posters=False
    )


def test_bio_check_keeps_signup_moving_when_letterboxd_blocks_the_profile_read():
    account = _account()
    session = AuthSession(
        account=account,
        access_token="access-token",
        refresh_token="refresh-token",
        expires_in=3600,
    )
    fake_service = SimpleNamespace(
        defer_ownership_verification=lambda *_args, **_kwargs: account,
        login=lambda *_args, **_kwargs: session,
    )
    scrape = AsyncMock(side_effect=AccessBlockedError("Letterboxd HTTP 403", status=403))
    with (
        patch("app.main.get_settings", return_value=_settings(scrape_max_retries=1)),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        patch("app.main.scrape_profile", new=scrape),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/auth/register/verify",
            json={
                "username": "film_fan",
                "code": "MOVIENOTES-ABC123",
                "password": "long-enough-password",
            },
        )

    assert response.status_code == 200
    assert response.json()["verification_deferred"] is True
    assert response.json()["logged_in"] is True
    assert any(cookie.startswith("mb_access=") for cookie in response.headers.get_list("set-cookie"))


def test_authenticated_user_can_create_consent_based_blend_request():
    account = _account()
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        find_blend_relation=lambda *_args: None,
        create_blend_request=lambda *_args, **_kwargs: "request-123",
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/blends/requests",
            json={"recipient_username": "other_user"},
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "request_id": "request-123",
        "recipient_username": "other_user",
        "status": "pending",
        "existing": False,
    }


@pytest.mark.parametrize(
    ("relation", "expected_status", "expected_direction"),
    [
        (
            {"request_id": "accepted-1", "status": "accepted", "direction": "outgoing"},
            "accepted",
            "outgoing",
        ),
        (
            {"request_id": "pending-1", "status": "pending", "direction": "incoming"},
            "pending",
            "incoming",
        ),
    ],
)
def test_existing_blend_relation_is_returned_instead_of_creating_a_duplicate(
    relation, expected_status, expected_direction
):
    account = _account()
    created = []
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        find_blend_relation=lambda *_args: relation,
        create_blend_request=lambda *_args, **_kwargs: created.append(True),
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/blends/requests",
            json={"recipient_username": "other_user"},
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json()["existing"] is True
    assert response.json()["status"] == expected_status
    assert response.json()["direction"] == expected_direction
    assert created == []


def test_pending_blend_count_returns_numbered_inbox_badge_value():
    account = _account()
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        count_pending_blend_requests=lambda _account: 12,
        count_unread_letters=lambda _account: 3,
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.get(
            "/api/blends/pending-count",
            headers={"Cookie": "mb_access=access-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"count": 15, "blend_count": 12, "letter_count": 3}


def test_rejecting_blend_does_not_run_comparison_engine():
    account = _account()
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        decide_blend_request=lambda *_args, **_kwargs: {
            "id": "request-123",
            "status": "rejected",
        },
    )
    compute = AsyncMock()
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._accepted_blend_single_flight", new=compute),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/blends/requests/request-123/decision",
            json={"decision": "rejected"},
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    compute.assert_not_awaited()


def test_accepting_blend_returns_persisted_comparison_result():
    account = _account()
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        decide_blend_request=lambda *_args, **_kwargs: {
            "id": "request-123",
            "status": "accepted",
        },
    )
    computed = {"request_id": "request-123", "score": 82, "cached": False}
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_heavy_rate_limit", new=AsyncMock()),
        patch(
            "app.main._accepted_blend_single_flight",
            new=AsyncMock(return_value=computed),
        ),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/blends/requests/request-123/decision",
            json={"decision": "accepted"},
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "result": computed}


def test_block_endpoint_is_authenticated_and_csrf_protected():
    account = _account()
    blocked = []
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        block_user=lambda _account, username: blocked.append(username),
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        missing_csrf = client.post("/api/users/other_user/block")
        response = client.post(
            "/api/users/other_user/block",
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert missing_csrf.status_code == 403
    assert response.status_code == 200
    assert blocked == ["other_user"]


def test_report_rejects_unknown_category_before_service_call():
    account = _account()
    fake_service = SimpleNamespace(current_account=lambda _token: account)
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/users/other_user/report",
            json={"category": "not-valid", "detail": "test"},
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 422


def test_password_reset_mismatch_stops_before_profile_scrape():
    scrape = AsyncMock()
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._enforce_auth_rate_limit", new=AsyncMock()),
        patch("app.main.scrape_profile", new=scrape),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/auth/password-reset/finish",
            json={
                "username": "film_fan",
                "code": "MOVIENOTES-ABC123",
                "new_password": "long-enough-password",
                "new_password_confirm": "different-password",
            },
        )

    assert response.status_code == 422
    scrape.assert_not_awaited()


def test_onboarding_completion_allows_the_full_crawl_to_continue_in_background():
    account = _account()
    completed = []
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        get_sync_job=lambda _uid: {
            "state": "running", "phase": "enrich", "scope": "full",
            "films_processed": 250, "films_total": 250,
        },
        complete_onboarding=lambda value: completed.append(value.id),
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/profile/onboarding-complete",
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert completed == [account.id]


def test_sync_status_returns_progress_without_loading_profile_snapshot():
    account = _account()
    loaded_profiles = []
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        get_sync_job=lambda _uid: {
            "state": "running",
            "phase": "enrich",
            "scope": "full",
            "films_processed": 125,
            "films_total": 250,
        },
        get_profile=lambda _account: loaded_profiles.append(True),
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main.profile_sync.is_running", return_value=True),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.get(
            "/api/profile/sync-status",
            headers={"Cookie": "mb_access=sync-status-token"},
        )

    assert response.status_code == 200
    assert response.json()["sync_job"] == {
        "state": "running",
        "phase": "enrich",
        "scope": "full",
        "processed": 125,
        "total": 250,
        "percent": 50,
        "onboarding_ready": False,
        "error": "",
    }
    assert loaded_profiles == []


def test_onboarding_completion_is_persisted_after_reveal_is_ready():
    account = _account()
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        get_sync_job=lambda _uid: {
            "state": "done", "phase": "done", "scope": "full",
            "films_processed": 250, "films_total": 250,
        },
        complete_onboarding=lambda _account: "2026-08-31T12:00:00+00:00",
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/profile/onboarding-complete",
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json()["completed_at"].startswith("2026-08-31")


def test_onboarding_completion_invalidates_the_short_account_cache():
    """A reload right after finishing onboarding must see the fresh account,
    not the pre-completion snapshot _require_account cached for 30s."""
    token = "onboarding-cache-token"
    account = _account("cache_member")
    calls = []
    fake_service = SimpleNamespace(
        current_account=lambda _token: calls.append(_token) or account,
        get_sync_job=lambda _uid: {
            "state": "done", "phase": "done", "scope": "full",
            "films_processed": 812, "films_total": 812,
        },
        complete_onboarding=lambda _account: "2026-08-31T12:00:00+00:00",
    )
    main._invalidate_account_cache(token)
    cookie = {"Cookie": f"mb_access={token}; mb_csrf=csrf-token"}
    try:
        with (
            patch("app.main.get_settings", return_value=_settings()),
            patch("app.main._auth_service", return_value=fake_service),
            TestClient(main.app, base_url="https://testserver") as client,
        ):
            # A normal page load primes the short-lived account cache.
            client.get("/api/auth/me", headers=cookie)
            assert calls == [token]

            response = client.post(
                "/api/profile/onboarding-complete",
                headers={**cookie, "X-CSRF-Token": "csrf-token"},
            )
            assert response.status_code == 200

            # A reload right after finishing onboarding must re-validate
            # instead of returning the cached, pre-completion account.
            client.get("/api/auth/me", headers=cookie)
            assert calls == [token, token]
    finally:
        main._invalidate_account_cache(token)


def test_authenticated_delete_removes_auth_identity_and_clears_session():
    account = _account()
    deleted = []
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        delete_account=lambda value: deleted.append(value.id),
    )
    settings = _settings(cache_db_path="/tmp/movienotes-test-cache.sqlite3")
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_delete_rate_limit", new=AsyncMock()),
        patch("app.main.Cache", return_value=object()),
        patch("app.main.SupabaseCache", return_value=object()),
        patch("app.main._delete_cached_user_data", return_value=True),
        patch("supabase.create_client", return_value=object()),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.request(
            "DELETE",
            "/api/data",
            json={"username": "film_fan"},
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert deleted == [account.id]
    cookies = response.headers.get_list("set-cookie")
    assert any(item.startswith("mb_access=") and "Max-Age=0" in item for item in cookies)


def test_readiness_reports_schema_state_without_exposing_details():
    fake_service = SimpleNamespace(check_schema=lambda: True)
    main._readiness_cache.update(checked_at=0.0, ready=False)
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        TestClient(main.app) as client,
    ):
        response = client.get("/api/readiness")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "auth_configured": True,
        "schema_ready": True,
    }


def test_readiness_is_503_when_auth_is_not_configured():
    with (
        patch("app.main.get_settings", return_value=SimpleNamespace(has_auth=False)),
        TestClient(main.app) as client,
    ):
        response = client.get("/api/readiness")

    assert response.status_code == 503
    assert response.json()["schema_ready"] is False


def test_refreshing_blend_forces_a_new_comparison():
    account = _account()
    fake_service = SimpleNamespace(current_account=lambda _token: account)
    computed = {"request_id": "request-123", "score": 86, "cached": False}
    compute = AsyncMock(return_value=computed)
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._enforce_heavy_rate_limit", new=AsyncMock()),
        patch("app.main._cancel_blend_background_tasks", new=AsyncMock()),
        patch("app.main._accepted_blend_single_flight", new=compute),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.post(
            "/api/blends/request-123/refresh",
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "refreshed", "result": computed}
    assert compute.await_args.kwargs["force_recompute"] is True


def test_deleting_blend_removes_the_shared_record():
    account = _account()
    deleted = []
    fake_service = SimpleNamespace(
        current_account=lambda _token: account,
        delete_blend=lambda _account, request_id: deleted.append(request_id),
    )
    with (
        patch("app.main.get_settings", return_value=_settings()),
        patch("app.main._auth_service", return_value=fake_service),
        patch("app.main._cancel_blend_background_tasks", new=AsyncMock()),
        TestClient(main.app, base_url="https://testserver") as client,
    ):
        response = client.delete(
            "/api/blends/request-123",
            headers={
                "Cookie": "mb_access=access-token; mb_csrf=csrf-token",
                "X-CSRF-Token": "csrf-token",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "request_id": "request-123"}
    assert deleted == ["request-123"]


def test_dev_login_route_only_exists_behind_the_flag():
    """The password-free route is local tooling and must stay unreachable."""
    main_source = (Path(__file__).parents[1] / "app" / "main.py").read_text()

    # Registered inside the flag check, not registered-then-guarded, so a
    # deployed build does not expose the path at all.
    assert "if get_settings().dev_login_enabled:" in main_source
    block = main_source.split("if get_settings().dev_login_enabled:", 1)[1]
    block = block.split("\n@app.get", 1)[0]
    assert '@app.get("/api/dev/login")' in block
    # And even then only from this machine.
    assert '("127.0.0.1", "::1", "localhost")' in block
    assert "status_code=404" in block


def test_dev_login_defaults_to_off_and_is_never_deployed():
    from app.config import Settings

    assert Settings().dev_login_enabled is False
    render = (Path(__file__).resolve().parents[2] / "render.yaml").read_text()
    assert "DEV_LOGIN_ENABLED" not in render


def test_new_accounts_get_an_open_letterbox():
    schema = (Path(__file__).parents[1] / "supabase" / "schema.sql").read_text()

    assert "ALTER COLUMN letter_receiving_enabled SET DEFAULT TRUE" in schema


def test_bulk_letterbox_open_is_a_script_not_a_schema_line():
    """Re-applying the schema must not undo someone's choice to close it."""
    schema = (Path(__file__).parents[1] / "supabase" / "schema.sql").read_text()

    assert "UPDATE public.users SET letter_receiving_enabled" not in schema
    assert (Path(__file__).parents[1] / "scripts" / "open_letterboxes.py").exists()


def test_remember_me_keeps_the_session_until_logout_and_one_day_without_it():
    """Reported: sessions expired on their own after a week either way.

    Checked, the session should outlive the browser being closed and only end
    at an explicit logout; unchecked, it should be gone the next day.
    """
    from app.config import Settings

    settings = Settings()
    assert settings.auth_session_max_age == 60 * 60 * 24 * 400  # tarayıcı üst sınırı
    assert settings.auth_session_short_max_age == 60 * 60 * 24

    main_py = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    setter = main_py.split("def _set_session_cookies", 1)[1].split("\ndef ", 1)[0]
    assert "remember: bool = True" in setter
    assert "settings.auth_session_short_max_age" in setter
    # The choice rides in its own cookie because the refresh token is httponly:
    # a token rotation has to rebuild the same lifetime.
    assert "REMEMBER_COOKIE" in setter
    refresh = main_py.split('@app.post("/api/auth/refresh")', 1)[1].split("@app.", 1)[0]
    assert "remember=_remembered(request)" in refresh
    # Logging out clears the marker along with the tokens.
    clear = main_py.split("def _clear_session_cookies", 1)[1].split("\ndef ", 1)[0]
    assert "REMEMBER_COOKIE" in clear

    # Existing device sessions pre-date the marker cookie. They must retain
    # the original durable refresh credential rather than falling to one day.
    remembered = main_py.split("def _remembered", 1)[1].split("\ndef ", 1)[0]
    assert 'request.cookies.get(REMEMBER_COOKIE, "") != "0"' in remembered

    app_js = (Path(__file__).resolve().parents[2] / "frontend" / "js" / "app.js").read_text()
    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text()
    assert 'id="login-remember"' in html
    assert "remember: $('login-remember').checked" in app_js


def test_every_screen_shares_one_top_bar():
    """Reported: the back link and avatar sat at a different height per page."""
    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text()
    css = (Path(__file__).resolve().parents[2] / "frontend" / "css" / "source.css").read_text()

    bar = css.split(".page-topbar {", 1)[1].split("}", 1)[0]
    assert "min-height: 3.5rem" in bar
    assert "align-items: center" in bar
    # One shape, used by every screen that has a back link.
    for back in ("btn-profile-back", "btn-tools-back", "btn-inbox-back",
                 "btn-blends-back", "btn-sinefil-back", "btn-thread-back",
                 "btn-user-back", "btn-follows-back", "btn-notifications-back",
                 "btn-new-search", "btn-random-new-search", "btn-blend-back"):
        row = html.split(f'id="{back}"', 1)[0]
        assert row.rsplit("<div", 1)[1].startswith(' class="page-topbar'), back
    # And "Ana sayfa" is gone: home is the feed now.
    assert "Ana sayfa" not in html
