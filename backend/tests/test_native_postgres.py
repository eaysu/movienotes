"""Optional integration suite for the disposable local PostgreSQL/PostgREST pair.

NATIVE_TEST_POSTGREST_URL=http://127.0.0.1:55480 pytest ...
Never points at a hosted or production database.
"""

import asyncio
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from postgrest import SyncPostgrestClient

from app.auth import AuthService
from app.catalog import Catalog
from app.config import Settings
from app.library import Library
from app.letterboxd_import import parse_letterboxd_export
from .test_letterboxd_import import export_zip

URL = os.environ.get("NATIVE_TEST_POSTGREST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Requires isolated local PostgREST")


@pytest.fixture
def database(tmp_path):
    assert urlsplit(URL).hostname in {
        "localhost",
        "127.0.0.1",
    }, "Only an isolated local test database is permitted"
    client = SyncPostgrestClient(URL)
    username = "db_" + uuid4().hex[:10]
    user = (
        client.table("users")
        .insert(
            {
                "username": username,
                "account_status": "active",
                "profile_source": "native",
            }
        )
        .execute()
        .data[0]
    )
    settings = Settings(
        _env_file=None,
        supabase_url="http://127.0.0.1",
        supabase_key="test-service",
        supabase_anon_key="test-anon",
        auth_identity_secret="test-identity",
        tmdb_api_key="",
        openai_api_key="",
        data_dir=str(tmp_path),
    )
    service = AuthService(settings, client_factory=lambda *_: client)
    account = service._account(user)
    yield client, service, account, settings
    client.table("users").delete().eq("id", user["id"]).execute()
    client.aclose()


def test_database_snapshot_keeps_canonical_favorites_and_native_notes(database):
    client, service, account, settings = database
    library = Library(service, settings)
    suffix = uuid4().hex
    films = [
        library.catalog.store(
            dict(
                catalog_key=f"test:{suffix}:{i}",
                title=f"Test film {i}",
                release_year=2000 + i,
                director="Test director",
                genres=["Drama"],
                details_loaded=True,
            )
        )
        for i in range(4)
    ]
    library.save_favorites(account, films)
    profile = asyncio.run(library.rebuild(account))
    assert {r["film_id"] for r in profile["favorite_films"]} == {f["id"] for f in films}
    post = library.publish(
        account, films[0], "A note about a film from years ago.", 4.5, False
    )
    assert post["film_id"] == films[0]["id"] and post["user_rating"] == 4.5
    library.update(account.id, films[0], watchlist=False, native=True)
    library.update(account.id, films[0], watchlist=True, rating=2, native=False)
    row = next(r for r in library.list(account.id) if r["film_id"] == films[0]["id"])
    assert row["rating"] == 4.5 and row["watchlist"] is False
    feed = service.list_feed(account, scope="mine")
    assert feed["posts"][0]["user_rating"] == 4.5
    assert feed["posts"][0]["film"]["title"] == "Test film 0"
    library.update(account.id, films[0], watched=False)
    library.update(account.id, films[0], watched=True)
    assert service.watched_film_by_slug(account.id, films[0]["slug"])


def test_database_import_is_idempotent_and_confirmed_matches_survive_reimports(
    database,
):
    client, service, account, settings = database
    library = Library(service, settings)
    data = export_zip(
        **{
            "watched.csv": "Date,Name,Year,Letterboxd URI\n2026-01-01,Unknown Film,2001,https://boxd.it/Aa12\n",
            "ratings.csv": "Date,Name,Year,Letterboxd URI,Rating\n2026-01-01,Unknown Film,2001,https://boxd.it/Aa12,3\n",
            "reviews.csv": "Date,Name,Year,Letterboxd URI,Review,Watched Date\n2026-01-01,Unknown Film,2001,https://boxd.it/review1,An imported review,2026-01-01\n",
        }
    )
    parsed = parse_letterboxd_export(data)
    job = library.prepare_import(account, parsed, hashlib.sha256(data).hexdigest())
    asyncio.run(library.process_import(account, job))
    saved = library.imports(account.id)[0]
    assert saved["state"] == "done", saved
    assert len(library.list(account.id)) == 1
    source = library.unresolved(account.id)[0]
    old_note = library.publish(account, source, "A note written before the match was resolved.", 4.5, False)
    target = library.catalog.store(
        dict(
            catalog_key="test:matched:" + uuid4().hex,
            title="Confirmed Film",
            release_year=2001,
            details_loaded=True,
        )
    )
    library.merge(account.id, source["id"], target)
    moved_note = client.table("posts").select("film_id,user_rating").eq("id", old_note["id"]).execute().data[0]
    assert moved_note["film_id"] == target["id"] and moved_note["user_rating"] == 4.5
    assert len(library.list(account.id)) == 1
    library.update(account.id, target, rating=4.5)
    again = library.prepare_import(account, parsed, "different-export-" + uuid4().hex)
    asyncio.run(library.process_import(account, again))
    rows = library.list(account.id)
    assert len(rows) == 1
    assert rows[0]["film_id"] == target["id"]
    assert rows[0]["rating"] == 4.5
    entries = library.archive_rows(account.id)
    assert len(entries) == 2 and all(r["match_status"] == "confirmed" for r in entries)
    assert (
        client.table("posts")
        .select("id")
        .eq("author_id", account.id)
        .execute()
        .data
    ) == [{"id": old_note["id"]}]
