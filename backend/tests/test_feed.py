import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]        # backend/
FRONTEND = ROOT.parent / "frontend"               # depo kökü/frontend


class FeedSchemaTests(unittest.TestCase):
    """The schema encodes the product rules, so they are checked here."""

    def setUp(self):
        self.schema = (ROOT / "supabase" / "schema.sql").read_text()

    def test_a_top_level_note_cannot_exist_without_a_film(self):
        posts = self.schema.split("CREATE TABLE IF NOT EXISTS public.posts (", 1)[1]
        posts = posts.split(");", 1)[0]

        # This is the product's central rule: no film, no note. A reply carries
        # no film of its own because it inherits the one above it.
        self.assertIn(
            "CHECK ((reply_to IS NULL AND film_slug IS NOT NULL) OR reply_to IS NOT NULL)",
            posts,
        )

    def test_note_body_is_bounded_and_not_empty(self):
        posts = self.schema.split("CREATE TABLE IF NOT EXISTS public.posts (", 1)[1]
        posts = posts.split(");", 1)[0]

        self.assertIn("char_length(body) <= 420", posts)
        self.assertIn("char_length(trim(body)) > 0", posts)

    def test_counters_are_maintained_by_triggers_not_by_callers(self):
        self.assertIn("CREATE TRIGGER trg_post_likes_counter", self.schema)
        self.assertIn("CREATE TRIGGER trg_posts_reply_counter", self.schema)
        # GREATEST guards against a counter going negative on a double delete.
        self.assertIn("GREATEST(0, like_count - 1)", self.schema)
        self.assertIn("GREATEST(0, reply_count - 1)", self.schema)

    def test_a_deleted_reply_stops_being_counted(self):
        """Deletion is soft, so the counter has to watch UPDATE as well.

        Caught live: three replies written, two deleted, and the thread header
        still said "3 cevap" over a single visible reply.
        """
        self.assertIn(
            "AFTER INSERT OR UPDATE OF deleted_at OR DELETE ON public.posts",
            self.schema,
        )
        counter = self.schema.split("FUNCTION public.posts_reply_counter()", 1)[1]
        counter = counter.split("$$;", 1)[0]
        self.assertIn("OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL", counter)
        # And restoring a note puts its reply back on the tally.
        self.assertIn("OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL", counter)

    def test_feed_tables_are_service_role_only(self):
        for table in ("posts", "post_likes", "follows", "notifications"):
            self.assertIn(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;", self.schema)
            self.assertIn(f"REVOKE ALL ON TABLE public.{table} FROM anon, authenticated;", self.schema)
            self.assertIn(f"GRANT ALL ON TABLE public.{table} TO service_role;", self.schema)

    def test_deleting_an_account_takes_its_posts_with_it(self):
        posts = self.schema.split("CREATE TABLE IF NOT EXISTS public.posts (", 1)[1]
        posts = posts.split(");", 1)[0]
        self.assertIn("author_id     BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE", posts)
        # And a deleted note takes its replies, rather than orphaning them.
        self.assertIn("reply_to      UUID REFERENCES public.posts(id) ON DELETE CASCADE", posts)

    def test_nobody_can_follow_themselves(self):
        follows = self.schema.split("CREATE TABLE IF NOT EXISTS public.follows (", 1)[1]
        follows = follows.split(");", 1)[0]
        self.assertIn("CHECK (follower_id <> followee_id)", follows)

    def test_private_accounts_keep_follow_requests_until_accepted(self):
        follows = self.schema.split("CREATE TABLE IF NOT EXISTS public.follows (", 1)[1]
        follows = follows.split(");", 1)[0]
        self.assertIn("status      TEXT NOT NULL DEFAULT 'accepted'", follows)
        self.assertIn("status IN ('pending', 'accepted')", follows)
        self.assertIn("private_account BOOLEAN NOT NULL DEFAULT FALSE", self.schema)


class FeedApiTests(unittest.TestCase):
    def setUp(self):
        self.main = (ROOT / "app" / "main.py").read_text()
        self.auth = (ROOT / "app" / "auth.py").read_text()

    def test_the_feed_pages_by_keyset_never_by_offset(self):
        feed = self.main.split('@app.get("/api/feed")', 1)[1].split("@app.", 1)[0]

        self.assertIn("next_cursor", feed)
        # The prose may say "offset"; the query must never use one.
        self.assertNotIn(".offset(", feed)
        self.assertNotIn("offset=", feed)
        self.assertIn("cursor", self.auth.split("def list_feed", 1)[1].split("def ", 1)[0])

    def test_blocking_hides_posts_in_both_directions(self):
        blocked = self.auth.split("def _blocked_ids", 1)[1].split("\n    def ", 1)[0]

        # Someone I blocked and someone who blocked me must both disappear.
        self.assertIn("blocker_user_id.eq.", blocked)
        self.assertIn("blocked_user_id.eq.", blocked)
        feed = self.auth.split("def list_feed", 1)[1].split("\n    def ", 1)[0]
        # Feed, thread and reactions all use the same access helper so a
        # direct post URL cannot evade the feed-only block filter.
        self.assertIn("_visible_rows", feed)
        visible = self.auth.split("def _visible_author_ids", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("_blocked_ids", visible)

    def test_a_failed_read_is_not_served_as_an_empty_timeline(self):
        """Caught live: one transient Supabase read turned the feed into
        "Henüz not yok" while two notes sat in the table."""
        block = self.auth.split("def list_feed", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("raise TransientStorageError", block)

        route = self.main.split('@app.get("/api/feed")', 1)[1].split("@app.", 1)[0]
        self.assertIn("except TransientStorageError", route)
        self.assertIn("503", route)

    def test_writing_requires_a_synced_profile(self):
        create = self.main.split('@app.post("/api/posts")', 1)[1].split("@app.", 1)[0]

        self.assertIn('profile_sync_status not in ("ready", "stale")', create)
        self.assertIn("409", create)

    def test_every_write_path_checks_csrf(self):
        for route in (
            '@app.post("/api/posts")',
            '@app.post("/api/posts/{post_id}/replies")',
            '@app.delete("/api/posts/{post_id}")',
            '@app.post("/api/posts/{post_id}/like")',
            '@app.delete("/api/posts/{post_id}/like")',
            '@app.post("/api/users/{username}/follow")',
            '@app.delete("/api/users/{username}/follow")',
        ):
            block = self.main.split(route, 1)[1].split("@app.", 1)[0]
            self.assertIn("_require_csrf(request)", block, route)

    def test_a_deleted_note_is_never_served(self):
        for method in ("def list_feed", "def get_post_thread", "def trending_films"):
            block = self.auth.split(method, 1)[1].split("\n    def ", 1)[0]
            self.assertIn('"deleted_at", "null"', block, method)

    def test_liking_your_own_note_does_not_notify_you(self):
        block = self.auth.split("def set_post_like", 1)[1].split("\n    def ", 1)[0]

        self.assertIn('if int(post["author_id"]) != account.id:', block)

    def test_posts_are_canonicalized_from_the_authenticated_library(self):
        create = self.auth.split("def create_post", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("watched_film_by_slug", create)
        self.assertIn("post_film_not_owned", create)

    def test_feed_scopes_cannot_bypass_visibility_and_follow_filters(self):
        """A named-person filter is a convenience, never an access path."""
        feed = self.auth.split("def list_feed", 1)[1].split("\n    def ", 1)[0]
        route = self.main.split('@app.get("/api/feed")', 1)[1].split("@app.", 1)[0]
        visibility = self.auth.split("def _visible_author_ids", 1)[1].split("\n    def ", 1)[0]

        self.assertIn('scope == "mine"', feed)
        self.assertIn("author_username", feed)
        self.assertIn("candidate_id in (following or [])", feed)
        self.assertIn('("community", "following", "mine")', route)
        # Public accounts always remain in the community timeline; locked
        # accounts only join after an accepted relationship.
        self.assertIn("if not private or user_id == account.id", visibility)
        self.assertIn('eq("status", "accepted")', visibility)

    def test_legacy_letter_cleanup_is_csrf_protected_and_account_scoped(self):
        route = self.main.split('@app.delete("/api/letters/legacy")', 1)[1].split("@app.", 1)[0]
        cleanup = self.auth.split("def purge_legacy_letters", 1)[1].split("\n    def ", 1)[0]

        self.assertIn("_require_csrf(request)", route)
        self.assertIn("_require_account(request)", route)
        self.assertIn("sender_user_id.eq.{account.id}", cleanup)
        self.assertIn("recipient_user_id.eq.{account.id}", cleanup)
        self.assertIn("row.get(\"ciphertext\")", cleanup)

    def test_sent_letter_recall_deletes_the_one_shared_row_for_both_inboxes(self):
        route = self.main.split('@app.delete("/api/letters/{letter_id}")', 1)[1].split("@app.", 1)[0]
        recall = self.auth.split("def delete_sent_letter", 1)[1].split("\n    def ", 1)[0]

        self.assertIn("_require_csrf(request)", route)
        self.assertIn("sender_user_id", recall)
        self.assertIn(".delete()", recall)
        self.assertNotIn("recipient_user_id", recall)

    def test_community_feed_uses_engagement_keyset_and_visible_film_search(self):
        feed = self.auth.split("def list_feed", 1)[1].split("\n    def ", 1)[0]
        films = self.auth.split("def search_feed_films", 1)[1].split("\n    def ", 1)[0]
        route = self.main.split('@app.get("/api/feed")', 1)[1].split("@app.", 1)[0]
        film_route = self.main.split('@app.get("/api/feed/films")', 1)[1].split("@app.", 1)[0]

        self.assertIn('sort == "engagement"', feed)
        self.assertIn('order("like_count", desc=True)', feed)
        self.assertIn('order("reply_count", desc=True)', feed)
        self.assertIn("_visible_rows(account, query.execute().data or [])", films)
        self.assertIn('feed_sort = "engagement"', route)
        self.assertIn("search_feed_films", film_route)

    def test_follow_requests_and_post_reports_have_dedicated_write_paths(self):
        self.assertIn("def decide_follow_request", self.auth)
        self.assertIn("def report_post", self.auth)
        self.assertIn('@app.post("/api/users/{username}/follow-request")', self.main)
        self.assertIn('@app.post("/api/posts/{post_id}/report")', self.main)

    def test_letters_and_blends_notify_their_recipient_once(self):
        schema = (ROOT / "supabase" / "schema.sql").read_text()
        self.assertIn("'blend_request'", schema)
        self.assertIn("'letter'", schema)
        blend = self.auth.split("def create_blend_request", 1)[1].split("\n    def ", 1)[0]
        letters = self.auth.split("def send_letter", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('event_key=f"blend-request:{request_id}"', blend)
        self.assertIn('event_key=f"letter:{letter_id}"', letters)

    def test_optional_letter_film_cannot_block_the_private_letter_write(self):
        letters = self.auth.split("def send_letter", 1)[1].split("\n    def ", 1)[0]

        self.assertIn('"p_film": None', letters)
        self.assertIn('service.table("cinephile_letters").update({"film": gift})', letters)
        self.assertIn('contextlib.suppress(Exception)', letters)

    def test_live_letter_setting_is_read_separately_from_migration_safe_login(self):
        route = self.main.split('@app.get("/api/letters/settings")', 1)[1].split("@app.", 1)[0]
        status = self.auth.split("def letter_receiving_status", 1)[1].split("\n    def ", 1)[0]

        self.assertIn("letter_receiving_status", route)
        self.assertIn('"letter_receiving_enabled"', status)

    def test_background_push_uses_a_subscription_not_notification_content(self):
        schema = (ROOT / "supabase" / "schema.sql").read_text()
        self.assertIn("web_push_subscriptions", schema)
        self.assertIn("def upsert_push_subscription", self.auth)
        self.assertIn("def _send_web_push", self.auth)
        self.assertIn('@app.post("/api/push/subscriptions")', self.main)

    def test_locked_profile_payload_does_not_expose_viewing_totals(self):
        profile = self.auth.split("def public_profile", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('"letterboxd_stats": (row.get("letterboxd_stats") or {}) if can_view else {}', profile)

    def test_login_is_not_coupled_to_social_preference_migrations(self):
        lookup = self.auth.split("def _account_row_by_username", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("identity_columns", lookup)
        self.assertNotIn("private_account", lookup)
        self.assertNotIn("letter_receiving_enabled", lookup)


class DiaryImportTests(unittest.TestCase):
    """Letterboxd güncesi akışa düşerken üç kural korunmalı."""

    def setUp(self):
        self.schema = (ROOT / "supabase" / "schema.sql").read_text()
        self.auth = (ROOT / "app" / "auth.py").read_text()
        self.main = (ROOT / "app" / "main.py").read_text()
        self.scraper = (ROOT / "app" / "scraper.py").read_text()

    def test_a_diary_entry_can_only_land_once(self):
        self.assertIn("ALTER TABLE public.posts ADD COLUMN IF NOT EXISTS source_key TEXT;", self.schema)
        self.assertIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_source_key\n"
            "  ON public.posts (author_id, source_key) WHERE source_key IS NOT NULL;",
            self.schema,
        )

    def test_a_deleted_log_is_never_scraped_back(self):
        """Silme yumuşak: satır kalır, anahtar dolu kalır, tekrar eklenemez.

        Bu yüzden içe aktarma çakışmada *güncelleme değil, atlama* yapmak
        zorunda; upsert `deleted_at`'i temizleyip kaydı geri getirirdi.
        """
        block = self.auth.split("def import_diary_entries", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("fresh = [entry for entry in entries if entry.get(\"source_key\") not in seen]", block)
        self.assertNotIn("upsert", block)
        self.assertNotIn("on_conflict", block)

    def test_only_a_reviewed_entry_reaches_the_feed(self):
        """Puanı olup cümlesi olmayan izleme kaydı akışta okunacak bir şey
        taşımıyordu ve gerçek yazıyı görünmez kılıyordu."""
        block = self.auth.split("def import_diary_entries", 1)[1].split("\n    def ", 1)[0]
        self.assertIn(
            'entries = [entry for entry in entries if (entry.get("body") or "").strip()]',
            block,
        )

    def test_the_feed_date_is_the_day_it_was_watched(self):
        block = self.main.split("def _kick_diary_ingest", 1)[1].split("\n@app", 1)[0]
        self.assertIn('"created_at": f"{entry.watched_on}T12:00:00+00:00"', block)
        # Okunamayan bir günce "kayıt yok" sayılmaz.
        self.assertIn("if entries is None:", block)

    def test_letterboxds_own_filler_is_not_imported_as_a_note(self):
        """Kullanıcının cümlesi olmayan kayıt akışa girmiyor.

        RSS'in "Watched on …" doldurmasını temizleyen ayrıştırıcı kalktı; kaynak
        artık yalnızca yorumluları listeleyen sayfa. Orada yorumsuz kaydın
        `.js-review-body` düğümü hiç yok, dolayısıyla kayıt zaten atlanıyor.
        """
        block = self.scraper.split("def _parse_review_page", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn('body = article.select_one(".js-review-body")', block)
        self.assertIn("if not match or not body:", block)
        self.assertIn("continue", block)

    def test_the_bulk_import_takes_every_reviewed_entry(self):
        """Üyenin bütün yazılı arşivi; sıralama filtrelemeden sonra.

        Kesme varsayılan olarak kapalı: akışı daraltan şey artık üye başına bir
        kota değil, haftalık pencere. `--limit` yalnız deneme için duruyor ve
        ters sırada olsaydı en yeni kayıtlar yorumsuz çıktığında hiçbir şey
        aktarılmazdı.
        """
        script = (ROOT / "scripts" / "import_diary.py").read_text()

        block = script.split("def _rows_from", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn('(entry.review or "").strip()', block)
        self.assertIn("reverse=True", block)
        self.assertIn("reviewed[:limit] if limit > 0 else reviewed", block)
        self.assertLess(block.index("reviewed ="), block.index("reviewed[:limit]"))
        # Yorumlu kaydı olmayan üye zorlanmıyor.
        self.assertIn("yorumlu kayıt yok, atlandı", script)
        self.assertIn('"--limit", type=int, default=0', script)

    def test_the_bulk_import_obeys_the_same_three_rules(self):
        script = (ROOT / "scripts" / "import_diary.py").read_text()

        self.assertIn("service.import_diary_entries", script)
        self.assertIn('f"{entry.watched_on}T12:00:00+00:00"', script)
        # Okunamayan günce "kayıt yok" sayılmaz.
        self.assertIn("if entries is None:", script)
        # --apply olmadan hiçbir şey yazılmaz.
        self.assertIn('"--apply", action="store_true"', script)

    def test_the_weekly_release_notice_lands_once_a_week(self):
        block = self.main.split("async def _notify_bulletin", 1)[1].split("\n@app", 1)[0]
        self.assertIn('event_key=f"bulletin:{week}"', block)
        self.assertIn('if not payload.get("highlighted"):', block)
        # Önbellekten dönen istekte de denenmeli, yoksa özet tazeyken hiç
        # bildirim düşmüyordu.
        bulletin = self.main.split('@app.get("/api/bulletin")', 1)[1].split("\n@app", 1)[0]
        self.assertEqual(bulletin.count("_notify_bulletin(service, account, week"), 2)

    def test_a_log_reads_as_a_note_not_as_metadata(self):
        """Künye ve puan Letterboxd verisini tekrar etmekten başka iş
        yapmıyordu; kartta okunacak şey yorumun kendisi."""
        app_js = (FRONTEND / "js" / "app.js").read_text()
        card = app_js.split("function feedPostCard", 1)[1].split("\nfunction ", 1)[0]

        self.assertNotIn("Güncesine ekledi", card)
        self.assertNotIn("logBadge", card)
        # Beğeni/cevap satırı sabit yükseklikte ve kartın altına yapışık.
        self.assertIn("feed-actions mt-auto flex h-9 shrink-0", card)
        css = (FRONTEND / "css" / "source.css").read_text()
        actions = css.split(".feed-actions {", 1)[1].split("}", 1)[0]
        self.assertIn("height: 2.25rem", actions)
        self.assertIn("margin-top: auto", actions)

    def test_a_long_review_is_folded_behind_a_read_more(self):
        """Uzun bir yorum kartı kilitliyordu; açılınca metin yerinde büyüyor."""
        app_js = (FRONTEND / "js" / "app.js").read_text()

        block = app_js.split("function feedBodyMarkup", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("FEED_BODY_CLAMP", block)
        self.assertIn("devamını oku", block)
        # Kesme kelimenin ortasına düşmemeli.
        self.assertIn("text.lastIndexOf(' ', FEED_BODY_CLAMP)", block)
        # Açmak kartı thread'e götürmemeli: düğme kendi dalında dönüyor.
        handler = app_js.split("function handleFeedCardClick", 1)[1].split("\n}", 1)[0]
        self.assertLess(
            handler.index("data-body-more"), handler.index("data-reveal-spoiler")
        )


class ProfilePageTests(unittest.TestCase):
    """The Twitter-shaped part: a member's page, their people, their alerts."""

    def setUp(self):
        self.main = (ROOT / "app" / "main.py").read_text()
        self.auth = (ROOT / "app" / "auth.py").read_text()
        self.html = (FRONTEND / "index.html").read_text()
        self.js = (FRONTEND / "js" / "app.js").read_text()

    def test_the_username_route_never_shadows_the_search_route(self):
        """/api/users/search must stay reachable next to /api/users/{username}."""
        search = self.main.index('@app.get("/api/users/search")')
        wildcard = self.main.index('@app.get("/api/users/{username}")')
        self.assertLess(search, wildcard)

    def test_a_profile_the_viewer_may_not_see_is_a_flat_404(self):
        block = self.auth.split("def public_profile", 1)[1].split("\n    def ", 1)[0]

        # Blocked in either direction, or not active: no distinction is offered,
        # so the answer cannot be used to probe who exists.
        self.assertIn("_blocked_ids", block)
        self.assertIn('(row.get("account_status") or "") != "active"', block)
        route = self.main.split('@app.get("/api/users/{username}")', 1)[1].split("@app.", 1)[0]
        self.assertIn("404", route)

    def test_a_profile_carries_the_tallies_the_page_shows(self):
        block = self.auth.split("def public_profile", 1)[1].split("\n    def ", 1)[0]
        for field in ("note_count", "follower_count", "following_count", "follows_you", "is_me"):
            self.assertIn(f'"{field}"', block, field)

    def test_follow_member_lists_are_owner_only_while_counts_stay_public(self):
        route = self.main.split("async def _follow_list", 1)[1].split("@app.", 1)[0]
        profile = self.auth.split("def public_profile", 1)[1].split("\n    def ", 1)[0]

        self.assertIn('profile["id"] != account.id', route)
        self.assertIn('Takip listeleri yalnızca hesap sahibine açık.', route)
        self.assertIn('"follower_count": self._accepted_follow_count', profile)
        self.assertIn('"following_count": self._accepted_follow_count', profile)

    def test_a_profile_timeline_shows_notes_not_replies(self):
        block = self.auth.split("def list_user_posts", 1)[1].split("\n    def ", 1)[0]

        self.assertIn('is_("reply_to", "null")', block)
        self.assertIn('is_("deleted_at", "null")', block)
        self.assertIn("cursor", block)

    def test_a_notification_names_the_note_it_is_about(self):
        block = self.auth.split("def list_notifications", 1)[1].split("\n    def ", 1)[0]

        self.assertIn("film_title", block)
        self.assertIn("thread_id", block)
        # A deleted note leaves the notification without a target, never a stub.
        self.assertIn('if not row.get("deleted_at")', block)

    def test_the_new_views_are_registered_and_reachable(self):
        for view in ("view-user", "view-follows", "view-notifications"):
            self.assertIn(f'id="{view}"', self.html, view)
        registry = self.js.split("function showView", 1)[1].split("forEach", 1)[0]
        for view in ("'user'", "'follows'", "'notifications'"):
            self.assertIn(view, registry, view)

    def test_the_shell_keeps_mobile_navigation_compact_and_profile_in_header(self):
        """Desktop keeps the full rail; mobile reserves its bar for four core areas."""
        for element in ("app-sidebar", "app-tabbar", "app-rail", "btn-compose-fab"):
            self.assertIn(f'id="{element}"', self.html, element)
        for target in ("feed", "sinefil", "notifications", "inbox", "blends", "profile"):
            self.assertIn(f'data-nav="{target}"', self.html, target)
        sidebar = self.html.split('id="app-sidebar"', 1)[1].split("</nav>", 1)[0]
        tabbar = self.html.split('id="app-tabbar"', 1)[1].split("</nav>", 1)[0]
        for target in ("feed", "sinefil", "notifications", "inbox"):
            self.assertIn(f'data-nav="{target}"', sidebar, target)
            self.assertIn(f'data-nav="{target}"', tabbar, target)
        self.assertIn('id="tab-tools-toggle"', tabbar)
        self.assertIn('id="tools-directory"', self.html)
        self.assertNotIn('data-nav="profile"', tabbar)
        self.assertIn('id="btn-header-profile"', self.html)

    def test_the_feed_is_the_home_screen(self):
        home = self.js.split("function homeView()", 1)[1].split("}", 1)[0]
        self.assertIn("'feed'", home)
        # The recommender screens still return to the dashboard they belong to.
        dash = self.js.split("function dashboardView()", 1)[1].split("}", 1)[0]
        self.assertIn("'profile'", dash)

    def test_a_picked_film_can_be_taken_back_off(self):
        """Reported: a film could be attached to a note but never removed."""
        self.assertIn("function clearComposerFilm", self.js)
        self.assertIn("feed-compose-film-clear", self.js)
        chip = self.js.split("function renderComposerFilm", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("feed-compose-film-change", chip)

    def test_a_centred_column_is_given_an_explicit_width(self):
        """`mx-auto` inside the body's column flex shrinks to content width;
        the notifications column came out a sliver wide because of it."""
        for view in ("view-feed", "view-user", "view-notifications", "view-follows", "view-thread"):
            markup = self.html.split(f'id="{view}" class="', 1)[1].split('"', 1)[0]
            self.assertIn("w-full", markup, view)
            self.assertIn("max-w-2xl", markup, view)

    def test_letters_and_blends_do_not_share_a_screen(self):
        """Mektuplar is letters only; every Blend surface lives in its own tab."""
        inbox = self.html.split('id="view-inbox"', 1)[1].split('id="view-blends"', 1)[0]
        blends = self.html.split('id="view-blends"', 1)[1].split('id="view-sinefil"', 1)[0]

        for section in ("blend-incoming", "blend-outgoing", "blends-list"):
            self.assertIn(f'id="{section}"', blends, section)
            self.assertNotIn(f'id="{section}"', inbox, section)
        self.assertNotIn('id="blend-history"', self.html)
        self.assertNotIn('id="blend-blocked"', self.html)
        self.assertIn('id="menu-blocked-users"', self.html)
        self.assertIn('id="dialog-blocked-users"', self.html)
        # The tab strip that used to split the inbox is gone with it.
        self.assertNotIn("data-inbox-tab", self.html)
        self.assertNotIn("setInboxTab", self.js)
        # Letters still have their own list and toggle in the inbox.
        self.assertIn('id="letters-list"', inbox)

    def test_an_author_is_clickable_wherever_their_note_appears(self):
        card = self.js.split("function feedPostCard", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("data-post-author", card)
        # Every surface that renders cards routes clicks through one handler,
        # whether it is passed directly or called from a small wrapper.
        for container in ("feed-list", "thread-root", "thread-replies", "user-posts"):
            anchor = f"$('{container}').addEventListener('click'"
            self.assertIn(anchor, self.js, container)
            registration = self.js.split(anchor, 1)[1][:400]
            self.assertIn("handleFeedCardClick", registration, container)


class FeedProductRuleTests(unittest.TestCase):
    def test_the_removed_twitter_features_stay_removed(self):
        """Repost, quote and bookmark were ruled out; nothing should reintroduce them."""
        main = (ROOT / "app" / "main.py").read_text().lower()
        auth = (ROOT / "app" / "auth.py").read_text().lower()

        for banned in ("repost", "bookmark", "quote_post", "retweet"):
            self.assertNotIn(banned, main, banned)
            self.assertNotIn(banned, auth, banned)

    def test_trending_is_computed_from_films_not_hashtags(self):
        auth = (ROOT / "app" / "auth.py").read_text()
        block = auth.split("def trending_films", 1)[1].split("\n    def ", 1)[0]

        self.assertIn("film_slug", block)
        self.assertNotIn("hashtag", block.lower())

    def test_follow_seeding_copies_one_direction_only(self):
        seed = (ROOT / "scripts" / "seed_follows.py").read_text()

        self.assertIn('"source": "letterboxd"', seed)
        # An existing pair is skipped, so unfollowing survives a re-run.
        self.assertIn("not in existing", seed)

    def test_seeding_never_reads_a_blocked_page_as_an_empty_follow_list(self):
        """Letterboxd answers a long run with 403s.

        If that read like "follows nobody", one rate-limited run would mark the
        whole graph as seeded and quietly leave it empty.
        """
        scraper = (ROOT / "app" / "scraper.py").read_text()
        block = scraper.split("async def scrape_following", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn("if not first_page_ok:", block)
        self.assertIn("return None", block)

        seed = (ROOT / "scripts" / "seed_follows.py").read_text()
        self.assertIn("if following is None:", seed)
        # Rows land per member, so a run cut short keeps what it earned.
        self.assertNotIn("return len(rows)", seed)


if __name__ == "__main__":
    unittest.main()


class DiaryArchiveTests(unittest.TestCase):
    """Bütün yazılı arşiv içeri giriyor; akış onu bir haftayla daraltıyor."""

    def setUp(self):
        self.auth = (ROOT / "app" / "auth.py").read_text()
        self.main = (ROOT / "app" / "main.py").read_text()
        self.scraper = (ROOT / "app" / "scraper.py").read_text()
        self.schema = (ROOT / "supabase" / "schema.sql").read_text()
        self.config = (ROOT / "app" / "config.py").read_text()

    def test_both_the_backfill_and_the_hourly_scan_read_the_review_pages(self):
        """RSS'in son ~50 kaydı *izleme* kaydı, yorum değil.

        Art arda elli film yorumsuz loglanırsa yeni yazılan yorum RSS'ten
        görünmez oluyordu. İki iş de yalnızca yorumluları listeleyen sayfayı
        okuyor: toplu aktarım sonuna kadar, saatlik tarama ilk sayfayı.
        """
        self.assertIn("async def scrape_reviewed_diary(", self.scraper)
        self.assertIn("/films/reviews/page/", self.scraper)
        self.assertIn("scrape_reviewed_diary", (ROOT / "scripts" / "import_diary.py").read_text())
        self.assertIn("scrape_reviewed_diary(username, max_pages=1)", self.main)
        # RSS ayrıştırıcısı kaldırıldı; ölü kod olarak kalmasın.
        self.assertNotIn("async def scrape_diary_entries", self.scraper)

    def test_html_and_rss_produce_the_same_key_for_one_entry(self):
        """`viewing:<id>` ile `letterboxd-review-<id>` aynı sayıyı taşıyor.

        Anahtarlar ayrışsaydı toplu tarama ile günlük RSS taraması aynı yorumu
        iki ayrı not olarak düşürürdü.
        """
        from app.scraper import _parse_review_page

        fixture = (ROOT / "tests" / "fixtures" / "reviews_page.html").read_text()
        rows = _parse_review_page(fixture)

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.key.startswith("letterboxd-review-") for row in rows))
        self.assertEqual(rows[0].key, "letterboxd-review-1463572057")
        self.assertEqual(rows[0].slug, "the-second-act")
        self.assertEqual(rows[0].title, "The Second Act")
        self.assertEqual(rows[0].year, 2024)
        self.assertEqual(rows[0].watched_on, "2026-08-23")
        self.assertEqual(rows[0].rating, 3.5)          # ★★★½
        self.assertTrue(rows[0].review)

    def test_a_truncated_review_is_fetched_in_full(self):
        """Liste sayfası uzun yorumu `…` ile kesiyor: ölçüldü, 603/1254."""
        self.assertIn('entry.review.endswith("…")', self.scraper)
        self.assertIn("/s/full-text/viewing:", self.scraper)
        # Tam metin alınamazsa kırpılmış hâli kalıyor, kayıt düşmüyor.
        self.assertIn("return fallback", self.scraper)

    def test_an_imported_review_is_not_squeezed_into_the_note_limit(self):
        """420 uygulamada yazılan notun sınırı, Letterboxd yorumunun değil."""
        self.assertIn("DIARY_BODY_MAX = 10_000", self.auth)
        self.assertIn('"body": _clip_review(', self.auth)
        self.assertNotIn('"body": (entry.get("body") or "")[:420]', self.auth)
        self.assertIn(
            "CHECK (char_length(body) <= CASE WHEN source = 'letterboxd' "
            "THEN 10000 ELSE 420 END)",
            self.schema,
        )
        # Kısıt `source` sütununa bakıyor, dolayısıyla sütun eklendikten sonra
        # tanımlanmalı; şema tepeden aşağı çalışıyor.
        self.assertLess(
            self.schema.index("ADD COLUMN IF NOT EXISTS source TEXT"),
            self.schema.index("CASE WHEN source = 'letterboxd'"),
        )

    def test_the_two_discovery_feeds_get_different_horizons(self):
        """Topluluk bir hafta, takip ettiklerin bir ay.

        İki akış farklı iş yapıyor: topluluk 130 kişilik havuzdan "bu hafta ne
        konuşuluyor"u gösteriyor, takip ettiklerin seçilmiş birkaç kişiyi —
        orada bir hafta çoğu zaman boş bir sayfa demek.
        """
        self.assertIn("FEED_DIARY_WINDOW_DAYS = 7", self.auth)
        self.assertIn("FEED_FOLLOWING_WINDOW_DAYS = 30", self.auth)
        window = self.auth.split("def list_feed(", 1)[1].split("def search_feed_films", 1)[0]
        self.assertIn(
            'FEED_FOLLOWING_WINDOW_DAYS if scope == "following" else FEED_DIARY_WINDOW_DAYS',
            window,
        )

    def test_only_the_discovery_feeds_are_limited_to_a_window(self):
        """Kendi notların ve film sayfası eksiksiz."""
        window = self.auth.split("def list_feed(", 1)[1].split("def search_feed_films", 1)[0]
        self.assertIn(
            'windowed = scope in ("community", "following") and not film_slug', window
        )
        # Uygulamada yazılan not penceresiz: `source.eq.app` her zaman geçiyor.
        self.assertIn('query.or_(f"source.eq.app,created_at.gte.{window_start}")', window)
        # Filtre veritabanında; Python'da elemek tarama döngüsünü boşa çevirirdi.
        self.assertNotIn("for row in rows if row", window.split("windowed", 1)[1][:400])


class DiaryScheduleTests(unittest.TestCase):
    """Saatlik tarama: koş başına iş sabit, sıra yazana ayrılıyor."""

    def setUp(self):
        self.auth = (ROOT / "app" / "auth.py").read_text()
        self.main = (ROOT / "app" / "main.py").read_text()
        self.config = (ROOT / "app" / "config.py").read_text()
        self.schema = (ROOT / "supabase" / "schema.sql").read_text()

    def test_the_scan_actually_runs_instead_of_waiting_to_be_called(self):
        """`_kick_diary_ingest` tanımlıydı ama hiçbir yerden çağrılmıyordu.

        Söz verdiği otomatik tarama bu yüzden hiç çalışmadı; artık kendi saatlik
        döngüsü var ve uygulama açılışında başlıyor.
        """
        self.assertIn("async def _diary_refresh_loop()", self.main)
        loop = self.main.split("async def _diary_refresh_loop()", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_kick_diary_ingest(settings, service)", loop)
        self.assertIn("await asyncio.sleep(60 * 60)", loop)
        lifespan = self.main.split("async def _lifespan(", 1)[1].split("\n@", 1)[0]
        self.assertIn("_diary_refresh_loop()", lifespan)
        # Hesaplar kapalıysa taranacak üye listesi de yok.
        self.assertIn('getattr(settings, "has_auth", False)', lifespan)
        # Kapatılabilir olmalı: iptal edilmeyen döngü kapanışı askıda bırakır.
        self.assertIn("scheduler.cancel()", lifespan)

    def test_one_request_per_member_and_a_capped_number_of_members(self):
        """Kaydı üçe indirmek isteği ucuzlatmıyor; maliyet üye sayısında."""
        block = self.main.split("def _kick_diary_ingest", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn("scrape_reviewed_diary(username, max_pages=1)", block)
        self.assertIn('getattr(settings, "diary_scan_members_per_run", 20)', block)
        self.assertIn('getattr(settings, "diary_scan_entries", 3)', block)
        self.assertIn("[:keep]", block)
        self.assertIn("diary_scan_members_per_run: int = 24", self.config)
        self.assertIn("diary_scan_entries: int = 3", self.config)
        self.assertIn("diary_scan_max_hours: int = 12", self.config)

    def test_a_member_who_never_writes_is_scanned_less_and_less_often(self):
        """Sabit bütçenin üyelik büyüdükçe yetmesini sağlayan mekanizma."""
        self.assertIn(
            "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS diary_idle_streak",
            self.schema,
        )
        block = self.auth.split("def diary_sync_candidates", 1)[1].split(
            "\n    def ", 1
        )[0]
        # Eşik ikiye katlanıyor ama tavanı var, yoksa üye sonsuza dek düşer.
        self.assertIn("2 ** min(streak, 10)", block)
        self.assertIn("max(1, max_hours)", block)
        # Havuz bütçeden geniş: geri çekilmişler elenince bütçe yine dolsun.
        self.assertIn("max(limit * 4, 40)", block)

    def test_a_scan_that_finds_something_puts_the_member_back_in_the_fast_lane(self):
        block = self.auth.split("def mark_diary_synced", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('patch["diary_idle_streak"] = 0', block)
        self.assertIn("+ 1, 10", block)
        # Yazan üyenin sayacı sıfırlanmadan önce fazladan okuma yapılmıyor.
        self.assertIn("if not wrote:", block)
        # Okunamayan günce damgalanmıyor: geri çekilme yanlışlıkla tetiklenmesin.
        ingest = self.main.split("def _kick_diary_ingest", 1)[1].split("\nasync def ", 1)[0]
        self.assertLess(ingest.index("if entries is None:"), ingest.index("mark_diary_synced"))

    def test_a_broken_second_query_does_not_drop_the_never_scanned(self):
        """Ölçüldü: iki üye hiç taranmamışken kuyruk boş dönüyordu.

        İki sorgu tek `try` içindeydi; ikincisi hata verince birincinin sonucu
        da atılıyordu. Hiç taranmamış üye sıranın en başındaki iş.
        """
        block = self.auth.split("def diary_sync_candidates", 1)[1].split("\n    def ", 1)[0]
        head, tail = block.split("pool = service.table", 1)
        self.assertIn("never = []", head)          # ilk sorgunun kendi except'i
        self.assertIn("return never", tail)        # ikinci sorgu düşerse elde kalan
        self.assertNotIn("return []", tail.split("due:", 1)[0])

    def test_a_review_without_a_logged_watch_date_still_gets_a_usable_day(self):
        """Canlı koşuda yakalandı: Postgres satırları
        `time zone "zt12:00:00+00:00" not recognized` ile reddediyordu.

        Letterboxd `<time datetime>` alanını iki biçimde veriyor: izleme günü
        girilmişse düz tarih, girilmemişse yorumun yayımlanma anı. İkincisini
        olduğu gibi kullanmak `created_at`'i bozuyor ve kayıt sessizce düşüyordu.
        """
        from app.scraper import _parse_review_page

        def page(value):
            return f"""
            <article class="production-viewing" data-object-id="viewing:42">
              <div data-item-slug="a-film" data-item-name="A Film (2020)"></div>
              <time class="timestamp" datetime="{value}">x</time>
              <div class="js-review-body"><p>bir cümle</p></div>
            </article>
            """

        plain = _parse_review_page(page("2026-09-09"))
        stamped = _parse_review_page(page("2026-07-22T07:42:36.842Z"))

        self.assertEqual(plain[0].watched_on, "2026-09-09")
        self.assertEqual(stamped[0].watched_on, "2026-07-22")
        # Tarihi hiç olmayan kayıt akışa girmiyor: sıra izlenme gününe göre.
        self.assertEqual(_parse_review_page(page("")), [])


class DiaryBackfillTests(unittest.TestCase):
    """Yeni üyenin arşivi: onboarding'te değil, girdikten sonra, kademeli."""

    def setUp(self):
        self.auth = (ROOT / "app" / "auth.py").read_text()
        self.main = (ROOT / "app" / "main.py").read_text()
        self.scraper = (ROOT / "app" / "scraper.py").read_text()
        self.schema = (ROOT / "supabase" / "schema.sql").read_text()
        self.config = (ROOT / "app" / "config.py").read_text()

    def test_registration_never_waits_for_the_archive(self):
        """Kayıt akışı yüzlerce sayfalık bir taramaya bağlanamaz."""
        for route in ('@app.post("/api/auth/register/verify")',
                      '@app.post("/api/profile/onboarding-complete")'):
            block = self.main.split(route, 1)[1].split("@app.", 1)[0]
            self.assertNotIn("_kick_diary_backfill", block, route)
        # Tetikleyici uygulamaya giriş: akış açıldığında.
        feed = self.main.split('@app.get("/api/feed")', 1)[1].split("@app.", 1)[0]
        self.assertIn("_kick_diary_backfill(get_settings(), service)", feed)
        # Yanıtı bekletmiyor: görev oluşturuluyor, await edilmiyor.
        kick = self.main.split("def _kick_diary_backfill", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn("asyncio.create_task(_run())", kick)

    def test_the_archive_is_read_a_few_pages_at_a_time_newest_first(self):
        """Profil dolarken görünsün: tek seferde değil, dilim dilim."""
        self.assertIn("start_page: int = 1", self.scraper)
        self.assertIn("range(start_page, start_page + max_pages)", self.scraper)
        kick = self.main.split("def _kick_diary_backfill", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn("start_page=done_pages + 1", kick)
        self.assertIn('getattr(settings, "diary_backfill_pages_per_run", 3)', kick)
        self.assertIn("diary_backfill_pages_per_run: int = 3", self.config)

    def test_progress_survives_a_restart_and_a_failed_page(self):
        """Süreç yeniden başlasa da kaldığı yerden devam etmeli."""
        self.assertIn(
            "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS diary_backfill_page",
            self.schema,
        )
        mark = self.auth.split("def mark_diary_backfill", 1)[1].split("\n    def ", 1)[0]
        # Sayfa okunamadıysa ilerleme yazılmıyor; aynı sayfa tekrar denenecek.
        self.assertIn("if page > 0:", mark)
        kick = self.main.split("def _kick_diary_backfill", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn('if entries is None and not progress.get("last_page"):', kick)
        self.assertIn("continue", kick)

    def test_a_finished_archive_leaves_the_queue(self):
        kick = self.main.split("def _kick_diary_backfill", 1)[1].split("\nasync def ", 1)[0]
        self.assertIn('done=bool(progress.get("exhausted"))', kick)
        candidates = self.auth.split("def diary_backfill_candidates", 1)[1].split(
            "\n    def ", 1
        )[0]
        self.assertIn('is_(\n                "diary_backfilled_at", "null"\n            )', candidates)

    def test_members_already_swept_by_the_bulk_script_are_not_queued_again(self):
        """Şema bir kez var olanları bitmiş sayıyor; 130 üye baştan taranmasın."""
        self.assertIn(
            "UPDATE public.users SET diary_backfilled_at = NOW()\n"
            " WHERE diary_backfilled_at IS NULL AND diary_synced_at IS NOT NULL;",
            self.schema,
        )

    def test_a_script_runs_from_either_invocation(self):
        """Depo kökünden `python -m backend.scripts.<ad>` de çalışmalı.

        Yaşandı: komut `ModuleNotFoundError: No module named 'app'` ile öldü.
        O biçimde sys.path'e depo kökü giriyor, `app` ise backend/ altında.
        """
        init = (ROOT / "scripts" / "__init__.py").read_text()
        self.assertIn("sys.path.insert(0, _BACKEND)", init)
        self.assertIn("parents[1]", init)
