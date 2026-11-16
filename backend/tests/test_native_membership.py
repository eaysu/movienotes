"""Independent account + catalog + notes, with the real exported ZIP as a canary."""

import asyncio
import csv
import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import AccountExistsError, AuthService
from app.catalog import Catalog
from app.config import Settings
from app.library import Library
from app.letterboxd_import import parse_letterboxd_export, InvalidLetterboxdExport
from app.sandbox import SandboxAuthService
from .test_letterboxd_import import export_zip

ROOT = Path(__file__).resolve().parents[2]
REAL_ZIP = ROOT / "letterboxd-enesaysu-2026-09-02-21-21-utc.zip"


@pytest.fixture
def native(tmp_path):
    settings = Settings(
        _env_file=None,
        sandbox_mode=True,
        tmdb_api_key="",
        openai_api_key="",
        data_dir=str(tmp_path),
        bulletin_enabled=False,
        diary_scan_enabled=False,
        nightly_pick_enabled=False,
    )
    service = SandboxAuthService()
    catalog = Catalog(service, settings)
    films = [
        catalog.store(
            dict(
                catalog_key=f"tmdb:{i}",
                tmdb_id=i,
                title=f"Film {i}",
                release_year=2000 + i,
                director="Director",
                genres=["Drama"],
                overview="A person finds a new life.",
                details_loaded=True,
            )
        )
        for i in range(1, 7)
    ]
    with patch("app.main.get_settings", return_value=settings), patch(
        "app.main._auth_service", return_value=service
    ), patch(
        "app.main._auth_rate_limiter",
        main.SlidingWindowRateLimiter(limit=40, window_seconds=900, burst=12, burst_seconds=30),
    ), patch(
        "app.main._auth_failure_limiter",
        main.SlidingWindowRateLimiter(limit=8, window_seconds=900, burst=5, burst_seconds=30),
    ):
        client = TestClient(main.app, base_url="https://testserver")
        yield settings, service, films, client


def register(client, username="new_member"):
    return client.post(
        "/api/auth/register",
        json=dict(
            username=username,
            password="native-test-password",
            password_confirm="native-test-password",
        ),
    )


def headers(client):
    return {"X-CSRF-Token": client.cookies.get("mb_csrf")}


def choose(client, films):
    return client.put(
        "/api/profile/favorites",
        json={"film_ids": [f["id"] for f in films[:4]]},
        headers=headers(client),
    )


def test_unique_username_password_and_session_without_letterboxd(native):
    _, service, films, client = native
    with patch(
        "app.main.scrape_profile",
        new=AsyncMock(side_effect=AssertionError("No HTML requests")),
    ) as scraper:
        first = register(client, "New_Member")
        duplicate = register(client, "new_member")
    assert first.status_code == 200
    assert first.json()["account"]["username"] == "new_member"
    assert first.json()["account"]["profile_source"] == "native"
    assert client.cookies.get("mb_access")
    assert duplicate.status_code == 409
    scraper.assert_not_called()
    assert choose(client, films).status_code == 200
    with patch.object(service, "refresh", return_value=None):
        assert client.post("/api/auth/refresh", headers=headers(client)).status_code == 401
    assert (
        client.post(
            "/api/profile/onboarding-complete", headers=headers(client)
        ).status_code
        == 200
    )
    assert client.post("/api/auth/logout", headers=headers(client)).status_code == 200
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "new_member", "password": "wrong-password"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "NEW_MEMBER", "password": "native-test-password"},
        ).status_code
        == 200
    )


def test_confirmation_mismatch_does_not_create_an_account(native):
    _, service, _, client = native
    response = client.post(
        "/api/auth/register",
        json=dict(
            username="new_member",
            password="native-test-password",
            password_confirm="different-password",
        ),
    )
    assert response.status_code == 422
    assert service.account is None


def test_four_distinct_favorites_are_durable_and_required(native):
    _, service, films, client = native
    register(client)
    assert client.post(
        "/api/posts", json={"film_id": films[0]["id"], "body": "Too early"},
        headers=headers(client),
    ).status_code == 409
    assert (
        client.post(
            "/api/profile/onboarding-complete", headers=headers(client)
        ).status_code
        == 409
    )
    response = client.put(
        "/api/profile/favorites",
        json={"film_ids": [films[0]["id"]] * 4},
        headers=headers(client),
    )
    assert response.status_code == 422
    result = choose(client, films)
    assert result.status_code == 200, result.text
    assert len(result.json()["favorite_films"]) == 4
    assert (
        client.get("/api/profile/me").json()["account"]["profile_sync_status"]
        == "ready"
    )


def test_note_can_reference_a_film_outside_history_and_only_uses_server_date(native):
    settings, service, films, client = native
    register(client)
    choose(client, films)
    film = films[-1]
    assert not service.watched_film_by_slug(1, film["slug"])
    response = client.post(
        "/api/posts",
        json={
            "film_id": film["id"],
            "body": "I watched this years ago.",
            "user_rating": 4.5,
        },
        headers=headers(client),
    )
    assert response.status_code == 200, response.text
    post = response.json()["post"]
    assert post["user_rating"] == 4.5
    assert (
        datetime.fromisoformat(post["created_at"]).date()
        == datetime.now(timezone.utc).date()
    )
    assert len(client.get("/api/feed?scope=mine").json()["posts"]) == 1
    assert (
        client.post(
            "/api/posts",
            json={"film_id": film["id"], "body": "Another thought", "user_rating": 2.3},
            headers=headers(client),
        ).status_code
        == 422
    )
    backdated = client.post(
        "/api/posts",
        json={"film_id": film["id"], "body": "Backdate", "created_at": "2000-01-01"},
        headers=headers(client),
    )
    assert backdated.status_code == 422
    missing_csrf = client.post(
        "/api/posts", json={"film_id": film["id"], "body": "Missing CSRF"}
    )
    assert missing_csrf.status_code == 403


def test_watchlist_is_independent_of_watched_history(native):
    _, _, films, client = native
    register(client)
    response = client.put(
        "/api/library/" + films[0]["id"],
        json={"watchlist": True},
        headers=headers(client),
    )
    assert response.status_code == 200
    rows = client.get("/api/library?scope=watchlist").json()["films"]
    assert len(rows) == 1 and not rows[0]["watched"]


def test_gpt_analysis_receives_four_favorites_and_has_a_retryable_fallback(native):
    _, _, films, client = native
    register(client)
    choose(client, films)
    with patch(
        "app.library.analyze_taste",
        new=AsyncMock(
            return_value={
                "analysis": ["A short taste reading."],
                "personality": "A thoughtful viewer.",
            }
        ),
    ) as model:
        response = client.post("/api/profile/analysis", headers=headers(client))
    assert response.status_code == 200
    assert response.json()["analysis_used"] is True
    assert len(model.await_args.args[2]) == 4
    with patch("app.library.analyze_taste", new=AsyncMock(return_value={})):
        fallback = client.post("/api/profile/analysis", headers=headers(client))
    assert fallback.status_code == 200
    assert fallback.json()["analysis_used"] is False
    assert fallback.json()["taste"]["analysis"] == ["A short taste reading."]


def test_registration_race_cleans_up_only_its_new_identity():
    settings = SimpleNamespace(
        has_auth=True,
        supabase_url="https://test.invalid",
        supabase_key="service",
        supabase_anon_key="anon",
        auth_identity_secret="test",
    )
    client = MagicMock()
    client.auth.admin.create_user.return_value = SimpleNamespace(
        user=SimpleNamespace(id="new-identity")
    )
    client.table.return_value.insert.return_value.execute.side_effect = ValueError(
        "23505 duplicate key username"
    )
    service = AuthService(settings, client_factory=lambda *_: client)
    with patch.object(service, "_account_row_by_username", return_value=None):
        with pytest.raises(AccountExistsError):
            service.register_native("member", "native-test-password")
    client.auth.admin.delete_user.assert_called_once_with("new-identity")
    client.auth.admin.update_user_by_id.assert_not_called()


@pytest.mark.skipif(
    not REAL_ZIP.exists(), reason="Personal ZIP is intentionally not committed"
)
def test_real_export_all_counts_ratings_reviews_and_favorite_order_are_preserved():
    import zipfile

    raw = REAL_ZIP.read_bytes()
    export = parse_letterboxd_export(raw)
    assert (
        hashlib.sha256(raw).hexdigest()
        == "10a640d6dc8362a08c12b56f12e93db0f0b540d697043464a1b192cd58a44fb2"
    )
    assert len(export.watched) == 563
    assert sum(r["rating_observed"] for r in export.watched) == 256
    assert len(export.watchlist) == 202
    assert sum(r["has_diary"] for r in export.entries) == 149
    assert sum(r["has_review"] for r in export.entries) == 86
    assert len(export.entries) == 150  # one review has no diary counterpart
    assert len(export.favorites) == 4
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        read = lambda name: list(
            csv.DictReader(
                io.StringIO(archive.read(name).decode("utf-8-sig"), newline="")
            )
        )
        expected = read("profile.csv")[0]["Favorite Films"].split(", ")
        assert [f["source_uri"] for f in export.favorites] == expected
        ratings = {r["Letterboxd URI"]: float(r["Rating"]) for r in read("ratings.csv")}
        observed = {r["slug"]: r.get("user_rating") for r in export.watched}
        from app.letterboxd_import import _film_slug

        assert all(
            observed[_film_slug(uri)] == rating for uri, rating in ratings.items()
        )
        source_reviews = {r["Letterboxd URI"]: r for r in read("reviews.csv")}
        assert all(
            r["source_uri"] in source_reviews for r in export.entries if r["has_review"]
        )
        assert all(
            r["review"]
            for r in export.entries
            if source_reviews.get(r["source_uri"], {}).get("Review")
        )
        from bs4 import BeautifulSoup
        import html

        # Compare every complete review, including multiline and HTML content.
        for entry in export.entries:
            source = source_reviews.get(entry["source_uri"])
            if source is None:
                continue
            expected_body = BeautifulSoup(html.unescape(source["Review"]), "html.parser").get_text(" ", strip=True)
            assert " ".join(entry["review"].split()) == " ".join(expected_body.split())
            assert entry.get("watched_on") == (source.get("Watched Date") or None)
    # A title/year collision is kept as two distinct film URIs.
    assert len({(r["title"], r.get("release_year")) for r in export.watched}) == 562
    assert sum(bool(r.get("identity_ambiguous")) for r in export.watched) == 2


@pytest.mark.skipif(
    not REAL_ZIP.exists(), reason="Personal ZIP is intentionally not committed"
)
def test_real_zip_reimport_does_not_duplicate_or_publish_reviews_and_native_rating_wins(
    native,
):
    settings, service, _, client = native
    register(client)
    library = Library(service, settings)
    raw = REAL_ZIP.read_bytes()
    export = parse_letterboxd_export(raw)
    job = library.prepare_import(
        service.account, export, hashlib.sha256(raw).hexdigest()
    )
    asyncio.run(library.process_import(service.account, job))
    assert job["state"] == "done", job
    before = library.list(1)
    entries_before = len(service._import_entries)
    assert len([r for r in before if r["watched"]]) == 563
    assert len([r for r in before if r["watchlist"]]) == 202
    assert not service._posts  # importing a review is not publishing a new note
    assert (
        len([r for r in service._import_entries.values() if r["kind"] == "entry"])
        == 150
    )
    film = before[0]["film"]
    library.update(1, film, watched=True, rating=4.5)
    same = library.prepare_import(
        service.account, export, hashlib.sha256(raw).hexdigest()
    )
    assert same["state"] == "done"
    later = library.prepare_import(
        service.account, export, "a-new-export-with-the-same-records"
    )
    asyncio.run(library.process_import(service.account, later))
    assert len(library.list(1)) == len(before)
    assert len(service._import_entries) == entries_before
    assert service._library[(1, film["id"])]["rating"] == 4.5


def test_repeated_viewings_and_review_only_entries_are_not_collapsed():
    data = export_zip(
        **{
            "watched.csv": "Date,Name,Year,Letterboxd URI\n2025-01-01,One,2001,https://boxd.it/Ab2\n",
            "diary.csv": "Date,Name,Year,Letterboxd URI,Rating,Rewatch,Watched Date\n2025-02-01,One,2001,https://boxd.it/view1,4,,2025-02-01\n2025-03-01,One,2001,https://boxd.it/view2,4.5,Yes,2025-03-01\n",
            "reviews.csv": "Date,Name,Year,Letterboxd URI,Review,Watched Date\n2025-03-01,One,2001,https://boxd.it/view2,Still great,2025-03-01\n2025-04-01,One,2001,https://boxd.it/view3,Another thought,2025-04-01\n",
        }
    )
    parsed = parse_letterboxd_export(data)
    assert len(parsed.watched) == 1
    assert len(parsed.entries) == 3
    assert sum(r["has_review"] for r in parsed.entries) == 2
    assert sum(r["rewatch"] for r in parsed.entries) == 1
    assert len({r["source_key"] for r in parsed.entries}) == 3


def test_ambiguous_tmdb_match_never_selects_a_remake_arbitrarily(native):
    settings, service, _, _ = native
    catalog = Catalog(service, settings)
    film = {"title": "One", "year": 2001}
    with patch.object(
        catalog,
        "search",
        new=AsyncMock(
            return_value=(
                [
                    {"id": "a", "tmdb_id": 1, "title": "One", "year": 2001},
                    {"id": "b", "tmdb_id": 2, "title": "One", "year": 2001},
                ],
                False,
            )
        ),
    ):
        assert asyncio.run(catalog.match(film)) is None


def test_random_catalog_and_watchlist_never_fetch_letterboxd(native):
    settings, service, films, client = native
    register(client)
    choose(client, films)
    library = Library(service, settings)
    library.update(1, films[-1], watchlist=True)
    from app.native_api import native_random_pool

    with patch("app.main.scrape_official_list", new=AsyncMock(side_effect=AssertionError("No HTML"))) as scraper:
        catalog = asyncio.run(native_random_pool(service.account, "catalog"))
        watchlist = asyncio.run(native_random_pool(service.account, "watchlist"))
        assert asyncio.run(main._official_list_pool("official_top_500", service, service.account, None)) == []
    assert {f.slug for f in catalog} == {f["slug"] for f in films[4:]}
    assert [f.slug for f in watchlist] == [films[-1]["slug"]]
    scraper.assert_not_called()


def test_later_metadata_match_merges_an_unresolved_film_without_losing_native_edits(native):
    settings, service, films, client = native
    register(client)
    library = Library(service, settings)
    data = export_zip(**{"watched.csv": "Date,Name,Year,Letterboxd URI\n2025-01-01,One,2001,https://boxd.it/Ab2\n"})
    parsed = parse_letterboxd_export(data)
    first = library.prepare_import(service.account, parsed, "before-metadata")
    asyncio.run(library.process_import(service.account, first))
    source = library.list(1)[0]["film"]
    note = library.publish(service.account, source, "Remember this film.", 4.5, False)
    library.update(1, source, watchlist=False)
    settings.tmdb_api_key = "simulation-only"
    later = library.prepare_import(service.account, parsed, "after-metadata")
    with patch.object(Catalog, "match", new=AsyncMock(return_value=films[0])):
        asyncio.run(library.process_import(service.account, later))
    assert later["state"] == "done"
    rows = library.list(1)
    assert len(rows) == 1 and rows[0]["film_id"] == films[0]["id"]
    assert rows[0]["rating"] == 4.5 and rows[0]["rating_source"] == "native"
    assert service._posts[note["id"]]["film_id"] == films[0]["id"]


def test_distinct_source_films_with_the_same_title_and_year_require_manual_matching(native):
    settings, service, _, client = native
    response = register(client)
    assert response.status_code == 200, response.text
    settings.tmdb_api_key = "simulation-only"
    parsed = parse_letterboxd_export(export_zip(**{
        "watched.csv": "Date,Name,Year,Letterboxd URI\n2025-01-01,One,2001,https://boxd.it/Ab2\n2025-01-02,One,2001,https://boxd.it/Ac2\n",
    }))
    library = Library(service, settings)
    job = library.prepare_import(service.account, parsed, "title-collision")
    with patch.object(Catalog, "match", new=AsyncMock(side_effect=AssertionError("Ambiguous source identity"))) as matcher:
        asyncio.run(library.process_import(service.account, job))
    assert job["state"] == "done"
    assert len(library.list(1)) == 2 and len(library.unresolved(1)) == 2
    matcher.assert_not_called()
