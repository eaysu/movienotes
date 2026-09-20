import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]        # backend/
FRONTEND = ROOT.parent / "frontend"               # depo kökü/frontend


def test_profile_uses_two_swipe_carousels_without_director_accordion():
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    for element_id in (
        "profile-directors",
        "profile-recent-films",
    ):
        marker = f'id="{element_id}" class="'
        assert marker in html
        classes = html.split(marker, 1)[1].split('"', 1)[0]
        assert "profile-carousel" in classes

    assert "profile-directors-more" not in html
    assert "profile-directors-panel" not in html
    assert "PROFILE_CAROUSEL_MS = 10000" in js
    assert "touchmove" in js
    assert "data-carousel-frame" in js
    assert "touch-action: pan-y" in css


def test_phone_layout_prevents_film_grid_and_inbox_overflow():
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    for element_id, variant in (
        ("alt-grid", "film-grid-4"),
    ):
        marker = f'id="{element_id}" class="'
        classes = html.split(marker, 1)[1].split('"', 1)[0]
        assert "mobile-film-grid" in classes
        assert variant in classes

    assert "@media (max-width: 339px)" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "overflow-x: clip" in css
    assert "#br-svg { width: 7.5rem; }" in css
    assert "inbox-card-actions" in css
    assert 'flex flex-col sm:flex-row sm:items-center' in js
    assert 'class="safe-footer ' in html


def test_mobile_profile_uses_the_compact_card_flow_and_expandable_lists():
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "js" / "app.js").read_text()

    for element_id in (
        "mobile-profile",
        "m-profile-favorites",
        "m-profile-summary",
        "m-profile-bulletin",
        "m-profile-collections",
        "view-profile-list",
        "m-profile-list",
    ):
        assert f'id="{element_id}"' in html
    assert "mobileCollectionCard('directors'" in js
    assert "function openMobileProfileList(kind)" in js
    assert "data-mobile-film-details" in js
    assert "showView('profile-list')" in js


def test_logo_button_accessible_name_contains_its_visible_label():
    html = (FRONTEND / "index.html").read_text()

    assert 'id="btn-home"' in html
    assert 'aria-label="Movienotes ana sayfa"' in html


def test_three_png_share_modules_are_wired_to_native_share_and_download():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    share_js = (FRONTEND / "js" / "share-cards.js").read_text()

    for element_id in (
        "btn-share-common",
        "btn-share-watchlist",
        "btn-share-personality",
        "dialog-png-share",
        "png-share-download",
        "png-share-native",
    ):
        assert f'id="{element_id}"' in html

    assert "shareCards.renderBlendShareCard(_currentBlendResult, 'watched')" in app_js
    assert "shareCards.renderBlendShareCard(_currentBlendResult, 'watchlist')" in app_js
    assert "shareCards.renderProfileShareCard(_persistedProfile)" in app_js
    assert "function renderProfileShareCard(profile)" in share_js
    assert "function makeCanvas(width = WIDTH, height = HEIGHT)" in share_js
    assert "const WIDTH = 1080" in share_js
    assert "const HEIGHT = 1350" in share_js
    # The recent-films card is a 16:9 frame, so the shared helpers take a size.
    assert "const WIDE_WIDTH = 1920" in share_js
    assert "const WIDE_HEIGHT = 1080" in share_js
    assert "drawFooter(ctx, label, width = WIDTH, height = HEIGHT)" in share_js
    assert "/api/share/image?${query}" in share_js
    assert "fitWrappedBlock" in share_js
    assert "data.avatar_url1" in share_js
    assert "data.avatar_url2" in share_js
    assert 'id="br-avatar1"' in html
    assert 'id="br-avatar2"' in html
    assert "navigator.share" in share_js
    assert "new File([card.blob]" in share_js
    assert "anchor.download = filename" in share_js


def test_blend_history_supports_refresh_and_confirmed_shared_delete():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert 'data-blend-action="refresh-result"' in app_js
    assert 'data-blend-action="delete-result"' in app_js
    assert "/api/blends/${encodeURIComponent(requestId)}/refresh" in app_js
    assert "Bu işlem Blend'i iki tarafın geçmişinden de kaldırır." in app_js


def test_blend_history_lazy_loads_full_result_payload_only_when_opened():
    app_js = (FRONTEND / "js" / "app.js").read_text()
    auth_py = (ROOT / "app" / "auth.py").read_text()

    list_blends = auth_py.split("def list_blends(self, account: Account)", 1)[1]
    list_blends = list_blends.split("def count_pending_blend_requests", 1)[0]
    assert '"id,request_id,score,confidence,algorithm_version,created_at"' in list_blends
    assert 'score,confidence,result,algorithm_version' not in list_blends
    assert "if (action === 'view')" in app_js
    assert "let stored = await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}/result`)" in app_js
    assert "await renderBlendResult(blendResultWithPeerAvatars(stored.result, item.peer))" in app_js
    assert "done.filter(item => !!item.blend_result).length" in app_js


def test_common_blend_cards_show_both_ratings_and_favorite_signals():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "film.rating1" in app_js
    assert "film.rating2" in app_js
    assert "film.favorite1" in app_js
    assert "film.favorite2" in app_js
    assert "Fav 10" not in app_js


def test_profile_decks_are_fixed_height_with_scrollable_overviews():
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    assert html.count("profile-dashboard-card") == 2
    assert 'class="film-overview-scroll mt-3 pr-2 pb-1"' in js
    assert 'data-deck-controls class="profile-carousel-controls' in js
    assert 'data-carousel-frame class="profile-carousel-frame' in js
    assert ".profile-dashboard-card" in css
    assert "grid-template-rows: minmax(0, 1fr) auto" in css
    assert ".profile-carousel-controls" in css
    assert ".director-card-scroll::-webkit-scrollbar { display: none; }" in css
    assert ".director-card-scroll [data-director-loading] { display: none; }" in css
    assert ".film-overview-scroll" in css
    assert "flex: 1 1 0%" in css
    assert "-webkit-overflow-scrolling: touch" in css
    assert "overflow-y: auto" in css


def test_blend_films_link_out_and_blend_library_opens_its_full_hub():
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "js" / "app.js").read_text()

    assert 'id="btn-blends-create"' not in html
    assert 'id="blend-tools-host"' in html
    assert "const href = letterboxdFilmURL(film.slug)" in js
    assert 'target="_blank" rel="noopener"' in js
    assert "case 'blends': openQuickTool('blend'); break;" in js
    assert "openQuickTool('blend')" in js


def test_new_inline_recommendation_keeps_the_result_panel_open():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "startInlineReco('taste', { preserveViewport: true })" in app_js
    assert "if (!preserveViewport)" in app_js


def test_manual_profile_refresh_also_forces_a_watchlist_check():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "refresh_watchlist=${refreshWatchlist ? 'true' : 'false'}" in app_js
    assert "syncProfile(false, true)" in app_js


def test_profile_entry_checks_watchlist_head_without_blocking_profile_render():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "queueEntrySync();" in app_js
    assert "apiJSON('/api/profile/entry-sync'" in app_js
    assert "checkWatchlistFreshness();" in app_js
    assert "'/api/profile/watchlist/check'" in app_js
    assert "mb_watchlist_check:" in app_js


def test_png_posters_are_fetched_with_credentials_into_local_blobs():
    share_js = (FRONTEND / "js" / "share-cards.js").read_text()

    assert "credentials: 'include'" in share_js
    assert "const blob = await response.blob()" in share_js
    assert "URL.createObjectURL(blob)" in share_js
    assert "releaseShareImage(image)" in share_js


def test_existing_or_pending_blend_routes_to_its_current_location():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "async function routeToExistingBlend(data)" in app_js
    assert "if (data.status === 'accepted')" in app_js
    assert "await renderBlendResult(stored.result)" in app_js
    assert "await loadBlendInbox(true)" in app_js
    assert "Bu kullanıcı sana zaten bir Blend isteği göndermiş" in app_js


def test_profile_boot_avoids_eager_blend_history_and_repeated_empty_aux_calls():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    enter_app = app_js.split("function enterApp(account, opts = {})", 1)[1]
    enter_app = enter_app.split("// ── Onboarding reveal", 1)[0]
    assert "refreshBlendBadge();" in enter_app
    assert "loadBlendInbox(false);" not in enter_app
    assert "const BLEND_BADGE_POLL_MS = 60000" in app_js
    assert "top-films" not in app_js
    assert "_recentLoaded = true;\n    renderRecentFilms(films);" in app_js
    assert "_statsLoaded = true;" in app_js


def test_health_and_session_boot_requests_start_in_parallel():
    app_js = (FRONTEND / "js" / "app.js").read_text()
    boot = app_js.split("async function boot()", 1)[1].split(
        "// ── Loading steps", 1
    )[0]

    assert "await Promise.all([" in boot
    assert "loadHealth()," in boot
    assert "apiJSON('/api/auth/me', { cache: 'no-store' }).catch(() => null)" in boot
    assert "setAuthMode('register');" in boot


def test_public_registration_count_is_rendered_without_exposing_user_records():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert html.count('data-public-user-count class=') == 2
    assert 'data-public-user-count-value' in html
    assert 'Letterboxd parolanı burada kullanma' in html
    assert 'directed by:' in html
    assert 'href="https://twitter.com/caddebogasi"' in html
    assert 'id="auth-title"' in html
    assert "title.textContent = t(login ? 'Movienotes’a giriş yap' : 'Movienotes’da hesap oluştur')" in (FRONTEND / "js" / "auth.js").read_text()
    auth = html.split('id="view-auth"', 1)[1].split('id="view-idle"', 1)[0]
    assert auth.index('data-public-user-count') < auth.index('<main')
    assert "apiJSON('/api/public/stats')" in app_js
    assert "data?.registered_users" in app_js
    assert "count.toLocaleString(uiLocale())" in app_js


def test_sinefil_area_opens_a_profile_page_from_the_card():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    auth_py = (ROOT / "app" / "auth.py").read_text()
    schema = (ROOT / "supabase" / "schema.sql").read_text()

    assert 'id="profile-sinefil-area"' in html
    # Görünürlük ayrı bir anahtar değil artık: kilitli hesap ikisini birlikte
    # çeviriyor, kullanıcı tek bir karar veriyor.
    assert 'id="profile-discovery-toggle"' not in html
    assert 'id="profile-private-toggle"' in html
    assert 'id="view-sinefil"' in html
    assert "Sinefil Sineması" in html
    assert "ALTER TABLE public.users ADD COLUMN IF NOT EXISTS discoverable BOOLEAN NOT NULL DEFAULT TRUE;" in schema
    assert "idx_users_sinefil_directory" in schema
    assert "apiJSON('/api/profile/privacy-settings'" in app_js
    assert "apiJSON(`/api/sinefil-alani?q=${encodeURIComponent(query)}&page=${_sinefilPage}&per_page=${_sinefilPerPage}`)" in app_js
    assert 'id="sinefil-pagination"' in html
    assert 'data-sinefil-page' in app_js
    assert 'data-sinefil-profile=' in app_js
    # Avatarına dokunmak profili bir sayfa olarak açar; kart görünümü kalktı.
    assert "openUserPage(profile.dataset.sinefilProfile" in app_js
    assert 'id="dialog-sinefil-profile"' not in html
    assert "openSinefilProfile" not in app_js
    # Kartın altındaki tek satır sunucudan gelir, arayüzde sabit değil.
    card = app_js.split("function sinefilCard", 1)[1].split("\n}", 1)[0]
    assert "profile.match_note" in card
    assert "Profili görüntüle" not in card
    assert "Film zevkiniz benziyor" in auth_py
    assert "def list_sinefil_cards" in auth_py
    assert "def sinefil_personality" in auth_py


def test_recommendation_and_blend_tools_live_outside_the_profile_screen():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert 'data-nav-action="watch"' in html
    assert 'data-nav-action="blend"' not in html
    assert 'data-nav="blends" class="nav-item"><span class="material-symbols-outlined">join_inner</span>Blend</button>' in html
    assert 'id="view-tools"' in html
    assert 'id="quick-tools-host"' in html
    assert 'data-rail-action=' not in html
    assert "showView('tools');" in app_js
    assert "function mountQuickTools(hostId = 'quick-tools-host')" in app_js
    assert "mountQuickTools('blend-tools-host');" in app_js
    assert "showView('blends');" in app_js
    assert "$('profile-quick-tools')?.classList.remove('hidden');" not in app_js.split('function openProfilePanel', 1)[1].split('function mountQuickTools', 1)[0]


def test_sinefil_letters_are_account_bound_and_keep_inbox_private():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    schema = (ROOT / "supabase" / "schema.sql").read_text()

    assert 'id="inbox-letters-panel"' in html
    assert 'id="dialog-letter-help"' in html
    assert 'function letterThreadCard' in app_js
    assert 'data-letter-thread=' in app_js
    assert "apiJSON('/api/letters')" in app_js
    assert "cinephile_letters" in schema
    assert "send_cinephile_letter" in schema
    assert 'id="profile-letter-toggle"' in html
    assert 'id="letters-conversation"' in html
    assert "/api/letters/send-status" in app_js
    assert "recipient_username" in app_js
    assert "recipient_user_id = v_recipient_user_id" in schema
    assert "Görüldü" in app_js

    # The device-key design is gone: letters follow the account, not a browser.
    assert not (FRONTEND / "js" / "letters-crypto.js").exists()
    assert "loadOrCreateDeviceIdentity" not in app_js
    assert "user_letter_keys" not in app_js
    assert "/api/letters/key-material" not in app_js
    assert "DROP TABLE IF EXISTS public.user_letter_keys" in schema
    # And the UI must not promise encryption it no longer performs.
    assert "cihazında şifrelenir" not in html
    assert "yalnızca alıcısının cihazında" not in html
    # Broken rows from the retired browser-key system are hidden and then
    # deleted only through the signed-in, CSRF-protected repair route.
    assert "legacyLetters" in app_js
    assert "apiJSON('/api/letters/legacy'" in app_js
    assert "mobilde ve webde" in html
    # A newly restored session hydrates the live letterbox preference before
    # choosing whether to show the "open your box" dialog.
    assert "refreshLiveLetterSettings" in app_js
    assert "'/api/letters/settings'" in app_js
    # The film picker itself must stay inside the letter composer; otherwise
    # clicking a result navigates to Letterboxd before the letter is sent.
    picker = app_js.split("async function searchLetterFilms", 1)[1].split(
        "async function sendLetter", 1
    )[0]
    assert "letterFilmMarkup(film, { link: false })" in picker


def test_letter_workspace_is_a_mobile_thread_view_and_sent_letters_can_be_recalled():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert 'id="letters-sidebar"' in html
    assert 'id="letters-workspace"' in html
    assert "function renderLetterWorkspace" in app_js
    # An open correspondence is its own page: the inbox heading steps aside and
    # the page's single back link walks to the list, so the panel carries no
    # second back arrow of its own.
    assert "data-letter-mobile-back" not in app_js
    assert "function closeLetterThread" in app_js
    assert "$('inbox-header').classList.toggle('hidden', open);" in app_js
    assert "if (_openLetterThread) closeLetterThread();" in app_js
    assert 'id="inbox-back-label"' in html
    assert "Sosyal alan" not in html
    assert 'data-letter-action="delete"' in app_js
    assert "Bu gönderilmiş mektup iki tarafın konuşmasından da silinsin mi?" in app_js
    assert "`/api/letters/${encodeURIComponent(letterId)}`" in app_js


def test_the_phone_profile_header_puts_follows_by_the_name_and_stats_on_their_own_row():
    """Asked for: follows beside the username, the three counts full width."""
    html = (FRONTEND / "index.html").read_text()

    card = html.split('id="mobile-profile"', 1)[1].split("</section>", 1)[0]
    name_block = card.split('id="m-profile-display-name"', 1)[1]
    follows, stats = name_block.split('id="m-profile-sample-size"', 1)
    # Both follow buttons sit in the column that holds the name…
    assert 'data-profile-follows="followers"' in follows
    assert 'data-profile-follows="following"' in follows
    # …and the watched/rated/this-year row comes after them, spanning the card.
    assert 'id="m-profile-rated-count"' in stats
    assert 'id="m-profile-year-count"' in stats
    assert 'class="mt-4 grid grid-cols-3' in card


def test_the_cinema_guide_card_teases_five_and_leaves_filtering_to_the_list():
    """Asked for: no filter on the dashboard card, five most relevant picks."""
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    card = html.split('id="m-profile-bulletin"', 1)[1].split("</section>", 1)[0]
    assert "m-bulletin-venue" not in card
    assert 'data-mobile-profile-list="bulletin"' in card
    # The select now belongs to the full-programme page, top right of its head.
    list_page = html.split('id="view-profile-list"', 1)[1].split("</div>\n</div>", 1)[0]
    assert 'id="m-profile-list-filter"' in list_page
    assert 'id="m-bulletin-venue"' in list_page

    teaser = app_js.split("function renderMobileBulletin(data)", 1)[1].split(
        "function renderMobileBulletinList", 1
    )[0]
    assert "const films = (data.films || []).slice(0, 5);" in teaser
    assert "m-bulletin-venue" not in teaser
    assert "$('m-bulletin-venue').addEventListener('change', () => renderMobileBulletinList());" in app_js


def test_the_two_collection_cards_split_the_phone_width_evenly():
    """Asked for: the last two cards side by side, flush with the card above."""
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    marker = 'id="m-profile-collections" class="'
    classes = html.split(marker, 1)[1].split('"', 1)[0]
    assert "grid grid-cols-2" in classes
    # No horizontal strip left over, and nothing padding the row from below.
    assert "overflow-x-auto" not in classes
    assert "pb-1" not in classes
    card = app_js.split("function mobileCollectionCard", 1)[1].split(
        "function renderMobileCollections", 1
    )[0]
    assert "w-full" in card
    assert "shrink-0" not in card
    assert "snap-start" not in card


def test_one_recommendation_fits_a_screen_without_scrolling():
    """Asked for: a thumbnail beside the title, not a full-bleed poster."""
    tools = (FRONTEND / "index.html").read_text().split('id="view-tools"', 1)[1]
    reco_js = (FRONTEND / "js" / "recommendations.js").read_text()

    # The tools page owns its top bar, so it no longer reserves a header's gap.
    assert "pt-20" not in tools.split("</div>", 1)[0]
    assert "Sinema araçları</span>" not in tools
    assert "whitespace-nowrap" in tools.split('id="tools-directory"', 1)[1]

    assert "function buildPickCard" in reco_js
    card = reco_js.split("function buildPickCard", 1)[1].split("function buildHeroCard", 1)[0]
    assert 'class="relative w-[92px] shrink-0 aspect-[2/3]' in card
    # Identity on the top row; genres and the reasoning below it.
    title_row = card.split('<div class="flex items-start gap-3.5">', 1)[1]
    assert title_row.index("${title}") < title_row.index("${director}")
    assert card.index("${genres}") > card.index("${director}")
    assert card.index("whyBlock(film)") > card.index("${genres}")
    # Both single-pick surfaces share it, so neither can drift back to a hero.
    assert "md:w-[260px]" not in reco_js
    assert "return buildPickCard(film" in reco_js
    # The average belongs to every pick, not just the random one, so it is read
    # inside the shared builder rather than injected by one caller. It is
    # Letterboxd's five-star community score, never TMDb's ten-point vote.
    assert "film.letterboxd_rating" in card
    assert "vote_average" not in reco_js
    assert "extraMeta" not in reco_js


def test_the_random_result_is_one_card_and_two_next_steps():
    """Asked for: no explanatory cards above the pick, nothing below the two
    buttons, and no page scroll on either single-pick screen."""
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    render = app_js.split("function renderInlineRandom", 1)[1].split(
        "function runProfileWatch", 1
    )[0]
    assert "Beğenmezsen çevirmeye devam et" not in app_js
    # The pool note is gone: where the pick came from is not the reader's job.
    assert "_randomPoolNote" not in app_js
    assert "Diğer Movienotes üyelerinin" not in app_js
    # One row, so neither next step drops off the bottom on a phone.
    assert 'class="mt-3 grid grid-cols-2 gap-2"' in render
    assert "grid-cols-1 sm:grid-cols-2" not in render
    assert 'id="profile-reco-reroll"' in render
    assert 'id="profile-reco-totaste"' in render

    # The page itself is pinned to the viewport, but the result area keeps an
    # internal scroll so an overflow can never trap a button off-screen.
    assert "body.tools-fixed { overflow: hidden; }" in css
    assert "body.tools-fixed #view-tools" in css
    assert "body.tools-fixed #quick-tools-host" in css
    assert "overflow-y: auto" in css.split("body.tools-fixed #quick-tools-host", 1)[1]
    assert "classList.toggle('tools-fixed', name === 'tools')" in app_js


def test_the_taste_pick_panel_opens_on_its_chooser_not_an_old_answer():
    """Reported: entering "Ne izlesem?" showed the previous run's cards below
    the buttons, with a reset button nobody needed under them."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    panel = app_js.split("function openProfilePanel", 1)[1].split(
        "function mountQuickTools", 1
    )[0]
    assert "_showTasteReco" not in panel
    assert "_loadTasteReco" not in panel

    show = app_js.split("function _showTasteReco", 1)[1].split(
        "function _toRandomBtn", 1
    )[0]
    assert "_recoResetBtn()" not in show
    # It still backs paging and swiping inside the run that produced it.
    assert "_loadTasteReco" in show
    # Errors keep a way out, so the button itself must not disappear entirely.
    assert "function _recoResetBtn" in app_js


def test_an_attached_letter_film_can_always_be_detached():
    """Reported: a long title pushed "Kaldır" off the card, so an attached film
    could not be removed. A flex child defaults to min-width:auto, so the title
    won the row unless it is told it may shrink."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    picked = app_js.split("data-letter-film-clear", 1)[0].rsplit(
        "target.innerHTML", 1
    )[1]
    assert 'class="min-w-0 flex-1"' in picked
    markup = app_js.split("function letterFilmMarkup", 1)[1].split(
        "function letterCard", 1
    )[0]
    assert "truncate" in markup
    assert "min-w-0 flex-1" in markup
    assert "shrink-0 rounded object-cover" in markup
    assert "shrink-0 rounded-md" in app_js


def test_every_page_wears_the_same_title():
    """Asked for: one size, one weight, one height for every page heading.

    Each page used to spell its own class list, so the sizes ranged from 22px
    to 36px and decorative kickers pushed some titles a line further down than
    others.
    """
    html = (FRONTEND / "index.html").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    rule = css.split(".page-title {", 1)[1].split("}", 1)[0]
    assert "font-size: 22px" in rule          # the notifications heading's size
    assert "font-weight: 700" in rule

    for heading in ("Bildirimler", "Mektuplar", "Blendler", "Sinefil Sineması",
                    "Bu akşam ne yapalım?"):
        marker = f'class="page-title'
        assert marker in html, heading
        assert heading in html, heading
    assert 'id="m-profile-list-title" class="page-title"' in html
    # No page-level heading keeps a bespoke size any more.
    assert "font-headline-lg text-headline-lg text-on-surface\">" not in html

    # The kickers that pushed titles out of line are gone, markup and script.
    app_js = (FRONTEND / "js" / "app.js").read_text()
    for kicker in ("Topluluk</span>", "Blend alanı", "m-profile-list-kicker"):
        assert kicker not in html, kicker
    assert "m-profile-list-kicker" not in app_js

    # And every shell page starts its content at the same inset.
    inset = css.split("body.has-shell #view-profile,", 1)[1].split("}", 1)[0]
    for view in ("#view-profile-list", "#view-notifications", "#view-tools",
                 "#view-inbox", "#view-blends", "#view-sinefil"):
        assert view in inset, view
    assert "padding-top: .75rem" in inset


def test_blend_films_are_rows_that_open_on_the_two_ratings():
    """Asked for: a list with an arrow per film, opening to each side's score.

    Five posters at grid size pushed the ratings — the thing two people came to
    compare — below the fold.
    """
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    for element_id in ("br-grid", "br-wishlist-grid"):
        classes = html.split(f'id="{element_id}" class="', 1)[1].split('"', 1)[0]
        assert "mobile-film-grid" not in classes, element_id

    card = app_js.split("function buildBlendFilmCard", 1)[1].split(
        "async function renderBlendResult", 1
    )[0]
    assert "<details" in card and "<summary" in card
    assert "group-open:rotate-90" in card          # the arrow turns when open
    assert "film.rating1" in card and "film.rating2" in card
    assert "film.favorite1" in card and "film.favorite2" in card


def test_the_blend_stats_row_stays_out_of_the_way():
    """Asked for: the same three figures, taking far less of the screen."""
    html = (FRONTEND / "index.html").read_text()
    stats = html.split('id="br-stats"', 1)[1].split("</section>", 1)[0]

    # All three are still reported…
    for element_id in ("br-common-count", "br-scan-count", "br-director-card"):
        assert element_id in stats, element_id
    # …at a size that no longer dominates the result.
    assert "text-headline-lg" not in stats
    assert "px-6 py-4" not in stats
    assert "min-w-[120px]" not in stats


def test_the_blend_score_does_not_pulse():
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    assert "ring-glow" not in app_js
    assert "ring-glow" not in css


def test_the_blend_share_card_is_titled_as_a_blend():
    share_js = (FRONTEND / "js" / "share-cards.js").read_text()

    assert "Movienotes Blend" in share_js
    assert "Aynı filmlerde buluştuk" not in share_js
    assert "ORTAK İZLENENLER" not in share_js
    # The corner label rode on every card, not just the profile one.
    assert "SİNEFİL PROFİL KARTI" not in share_js
    # The watchlist variant keeps its own heading.
    assert "ORTAK İZLEME LİSTESİ" in share_js


def test_a_working_blend_button_spins_instead_of_rewording_itself():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "'Hazırlanıyor…'" not in app_js
    assert "animate-spin" in app_js.split("const busyLabel", 1)[1][:600]
    # innerHTML both ways, or the spinner markup would be restored as text.
    assert "const oldText = button.innerHTML;" in app_js
    assert "button.innerHTML = oldText;" in app_js


def test_the_profile_avatar_reaches_the_top_right_corner():
    """Reported: on the result screens it sat beside the back link.

    The bar was nested in another flex row, so it shrank to its content and
    space-between had nothing left to push apart.
    """
    html = (FRONTEND / "index.html").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    bar = css.split(".page-topbar {", 1)[1].split("}", 1)[0]
    assert "justify-content: space-between" in bar
    # A block-level flex container already fills its parent. Forcing width:100%
    # instead pushed the bars that carry a horizontal margin off-screen, taking
    # the avatar with them.
    assert "width: 100%" not in bar
    assert "page-topbar mx-4" in html
    # …and no bar is nested in another flex row, which is what made
    # space-between have nothing to push apart in the first place.
    assert '<div class="flex items-center justify-between mt-stack-md">' not in html


def test_the_feed_header_gave_back_its_height():
    html = (FRONTEND / "index.html").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    header = html.split('id="view-feed"', 1)[1].split('id="feed-list"', 1)[0]
    assert 'class="flex items-center justify-between gap-3 px-4 py-2.5"' in header
    assert "py-4" not in header.split("role=\"tablist\"", 1)[0]
    tab = css.split(".feed-tab {", 1)[1].split("}", 1)[0]
    assert "padding: .57rem" in tab


def test_a_profile_portrait_can_be_opened_full_size():
    """Asked for: tapping a member's photo should show the photo.

    Portraits render between 44px and 118px everywhere, which is too small to
    actually look at.
    """
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    assert 'id="dialog-avatar"' in html
    assert 'id="avatar-zoom-image"' in html
    assert ".avatar-zoom-dialog" in css
    assert "[data-avatar-zoom] { cursor: zoom-in; }" in css

    # Both own-profile portraits and both Blend portraits opt in.
    for element_id in ("profile-avatar", "m-profile-avatar", "br-avatar1", "br-avatar2"):
        assert f'id="{element_id}" data-avatar-zoom' in html, element_id
    # …as does another member's page, and a correspondence header.
    assert app_js.count("data-avatar-zoom") >= 2
    assert "peerAvatar(peer, { zoom: true })" in app_js

    handler = app_js.split("const avatar = event.target.closest('[data-avatar-zoom]')", 1)[1]
    # Resolved property, not the escaping markup helper: an escaped "&" would
    # break an avatar URL's query string.
    assert "avatar.currentSrc || avatar.src" in handler
    assert "safeImageURL(avatar" not in app_js
    assert "$('dialog-avatar').showModal();" in handler


def test_a_member_header_gives_the_name_and_the_actions_their_own_rows():
    """Reported: the display name was cut to a few characters and the username
    ran under the Blend and letter buttons on a phone."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    header = app_js.split("function userHeaderMarkup", 1)[1].split(
        "function userFavoritesMarkup", 1
    )[0]
    identity, actions = header.split("Actions get their own row", 1)
    # Name and username own the row beside the portrait…
    assert "${name}" in identity and "@${escapeHTML(profile.username)}" in identity
    assert identity.count("truncate") >= 2
    assert "followButton(profile)" not in identity
    # …and the three actions sit on the next one, free to wrap.
    assert "flex flex-wrap items-center gap-2" in actions
    assert "${blendAction}${letterAction}${followButton(profile)}" in actions


def test_blend_is_orange_and_letters_are_blue_on_a_member_header():
    """Asked for: one colour per feature, everywhere. This header had them
    swapped — Blend green, letters orange — against the rest of the app."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    blend = app_js.split('aria-label="Blend yap"', 1)[1].split(">", 1)[0]
    letter = app_js.split('aria-label="Mektup yaz"', 1)[1].split(">", 1)[0]
    assert "secondary-container" in blend and "primary-container" not in blend
    assert "tertiary-container" in letter and "secondary-container" not in letter


def test_the_cinema_list_separates_the_film_from_the_cinema():
    """Reported: the link icon opened Letterboxd, so the cinema's own page for
    the film was unreachable from the programme."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    render = app_js.split("function renderMobileBulletinList", 1)[1].split(
        "function paintBulletin", 1
    )[0]
    # Poster and title go to the film…
    assert "letterboxdFilmURL(film.slug)" in render
    # …and the icon beside them goes to the venue, straight through when there
    # is only one and via the picker when there are several.
    assert "venues[0].url" in render
    assert 'data-bulletin-venues="${key}"' in render
    assert "openBulletinVenues(venueButton.dataset.bulletinVenues)" in app_js.split(
        "$('m-profile-list').addEventListener('click'", 1
    )[1]


def test_the_blend_hero_reports_three_figures_and_nothing_else():
    """Asked for: no data-coverage line, and the shared director with a face."""
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    main_py = (ROOT / "app" / "main.py").read_text()

    assert "br-confidence" not in html
    assert "br-confidence" not in app_js
    assert "veri kapsamı" not in app_js

    stats = html.split('id="br-stats"', 1)[1].split("</section>", 1)[0]
    for element_id in ("br-common-count", "br-scan-count", "br-director"):
        assert element_id in stats, element_id
    assert 'id="br-director-avatar"' in stats
    assert "$('br-director-avatar').innerHTML" in app_js
    # The portrait comes from the same cached TMDb person lookup the profile
    # deck uses, on both the stored and the freshly computed payload.
    assert "async def _director_photo" in main_py
    assert main_py.count('"top_director_photo"') == 2


def test_the_phones_back_gesture_walks_the_app_instead_of_closing_it():
    """Reported: in the installed app the phone's own back button quit from
    wherever the member was.

    The shell only ever called replaceState, so a standalone window held a
    single history entry and the first back popped straight out of it.
    """
    app_js = (FRONTEND / "js" / "app.js").read_text()
    manifest = json.loads((FRONTEND / "site.webmanifest").read_text())

    assert manifest["display"] == "standalone"

    remember = app_js.split("function rememberRoute", 1)[1].split(
        "async function restoreRoute", 1
    )[0]
    # Every later screen pushes; only the first one replaces, so back from the
    # home screen still closes the app.
    assert "history.pushState({ view: name }" in remember
    assert "history.replaceState({ view: name }" in remember
    assert "if (previous === undefined)" in remember

    popstate = app_js.split("window.addEventListener('popstate'", 1)[1].split(
        "// ── Uygulama kabuğu", 1
    )[0]
    assert "await restoreRoute()" in popstate
    assert "showView(view)" in popstate
    # The restoring guard is saved and restored, not cleared, or the screen a
    # back gesture lands on would push a fresh entry of its own.
    assert "const wasRestoring = _routeRestoring;" in popstate
    assert "_routeRestoring = wasRestoring;" in popstate
    restore = app_js.split("async function restoreRoute", 1)[1].split(
        "window.addEventListener('popstate'", 1
    )[0]
    assert "_routeRestoring = wasRestoring;" in restore
    assert "_routeRestoring = false;" not in restore


def test_the_blend_field_waits_to_be_tapped():
    """Asked for: arriving at Blends should not raise the keyboard."""
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "$('profile-blend-username').focus()" not in app_js
    field = html.split('id="profile-blend-username"', 1)[1].split(">", 1)[0]
    assert 'placeholder="Blend yapacağın kullanıcının adını gir"' in field


def test_safari_is_told_how_to_install_since_it_cannot_be_asked():
    """Reported: Safari never offered the app.

    Only iOS got the manual steps, so desktop Safari — which also never fires
    beforeinstallprompt — fell through the guard and showed nothing.
    """
    app_js = (FRONTEND / "js" / "app.js").read_text()
    html = (FRONTEND / "index.html").read_text()

    assert "function isSafari" in app_js
    assert "function needsManualInstall" in app_js
    prompt = app_js.split("function showInstallAppDialog", 1)[1].split("}\n\n", 1)[0]
    assert "const manual = needsManualInstall();" in prompt
    assert "if (!manual && !_deferredInstallPrompt) return;" in prompt
    # Wording follows the platform: a phone shares, a desktop adds to the Dock.
    assert "MANUAL_INSTALL_STEPS" in app_js
    assert "Dock’a Ekle" in app_js
    assert html.count("data-install-step") == 3


def test_the_shell_reserves_the_notch_and_the_home_indicator():
    """Asked for: it has to look right on iOS as well as Android.

    Every env(safe-area-inset-*) in the stylesheet was dead: without
    viewport-fit=cover iOS reports them all as zero.
    """
    html = (FRONTEND / "index.html").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    assert "viewport-fit=cover" in html
    assert "#app-header { padding-top: env(safe-area-inset-top); }" in css
    # Anything measured against the tab bar grows with the home indicator.
    assert "padding-bottom: calc(4.5rem + env(safe-area-inset-bottom))" in css
    assert "min-height: calc(100dvh - 4.5rem - env(safe-area-inset-bottom))" in css
    fab = css.split("#btn-letter-compose-fab {", 1)[1].split("}", 1)[0]
    assert "env(safe-area-inset-bottom)" in fab
    # A hard pixel floor made every short screen scroll into empty space.
    assert "max(884px, 100dvh)" not in css
    assert "min-height: 100dvh;" in css


def test_the_tools_card_does_not_also_sit_on_the_profile_page():
    """Reported: the "Ne izlesem?" card was still on the desktop profile.

    It is moved into the tools page on demand, but md:block showed it on the
    dashboard until that first visit re-parented it away.
    """
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    classes = html.split('id="profile-quick-tools" class="', 1)[1].split('"', 1)[0]
    assert "hidden" in classes
    assert "md:block" not in classes
    # Visibility is decided by the tools flow, which owns the card.
    assert "$('profile-quick-tools')?.classList.remove('hidden')" in app_js


def test_profile_follow_lists_are_dialogs_and_stay_out_of_the_share_card():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    share_js = (FRONTEND / "js" / "share-cards.js").read_text()

    assert 'id="dialog-profile-follows"' in html
    assert 'data-profile-follows="followers"' in html
    assert 'data-profile-follows="following"' in html
    assert "function openProfileFollows" in app_js
    assert "profile-follows-list" in app_js
    assert "profile-follows-list" not in share_js


def test_public_profiles_show_follow_counts_but_never_link_to_member_lists():
    app_js = (FRONTEND / "js" / "app.js").read_text()
    header = app_js.split("function userHeader", 1)[1].split("function renderUserPage", 1)[0]

    assert 'data-follows="followers"' not in header
    assert 'data-follows="following"' not in header
    assert "takipçi</span>" in header


def test_chrome_install_prompt_uses_a_real_pwa_event_and_registered_worker():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    manifest = (FRONTEND / "site.webmanifest").read_text()

    assert 'id="dialog-install-app"' in html
    assert 'href="/static/site.webmanifest?v=20260910.6"' in html
    assert 'href="/static/movienotes-mark.png?v=20260910.6"' in html
    assert 'src="/static/movienotes-mark.png"' in html
    assert "beforeinstallprompt" in app_js
    assert "requestMovienotesInstall" in app_js
    assert "register('/push-sw.js')" in app_js
    assert '"192x192"' in manifest
    assert '"512x512"' in manifest


def test_a_notification_carries_the_brand_mark():
    """Asked for: the Chrome notification should show our own logo.

    The launcher icon is the dark-ground artwork; a notification lands on the
    system's own surface, light or dark, so it uses the transparent-ground mark
    instead. The badge is masked to a silhouette from that alpha channel, which
    is why it is a separate, smaller file.
    """
    import struct

    sw = (FRONTEND / "push-sw.js").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "icon: '/static/movienotes-notify-192.png'" in sw
    assert "badge: '/static/movienotes-badge-96.png'" in sw
    assert "/static/movienotes-notify-192.png" in app_js
    # Tapping the notification has to land on the route the app knows.
    assert "data: { url: '/#/bildirimler' }" in sw

    for name, size in (("movienotes-notify-192.png", 192), ("movienotes-badge-96.png", 96)):
        raw = (FRONTEND / name).read_bytes()
        width, height, _, colour = struct.unpack(">IIBB", raw[16:26])
        assert (width, height) == (size, size), name
        # Colour type 6 is RGBA: the ground must stay transparent or the badge
        # mask turns into a solid block.
        assert colour == 6, name


def test_the_installed_app_opens_on_the_chosen_icon():
    """The launcher, splash and favicon are all cut from movienotes-icon.png."""
    import hashlib

    mark = (FRONTEND / "movienotes-mark.png").read_bytes()
    manifest = (FRONTEND / "site.webmanifest").read_text()
    html = (FRONTEND / "index.html").read_text()

    # Both launcher sizes are cut from the same artwork as the favicon.
    for name in ("movienotes-icon-192.png", "movienotes-icon-512.png",
                 "movienotes-icon-maskable.png"):
        icon = (FRONTEND / name).read_bytes()
        assert icon, name
        # A resize keeps the mark's white ground: the flat dark icon is 1254px
        # of near-black, so a corner pixel tells them apart.
        assert icon[:8] == mark[:8], name
        assert f'"/static/{name}?v=' in manifest, name
    # The splash paints `background_color` behind the icon, so it matches the
    # icon's own dark ground.
    assert '"background_color": "#171e27"' in manifest
    # Android 12+ kırpma maskesi için markanın güvenli alana çekilmiş sürümü.
    assert '"purpose": "maskable"' in manifest
    assert 'apple-touch-icon" href="/static/movienotes-icon-maskable.png' in html


def test_the_people_you_follow_live_behind_the_filter_button():
    """Reported: the follow chips sat under the tabs on every visit.

    They belong to the filter menu, so the timeline header stays quiet until
    somebody actually asks to narrow it.
    """
    app_js = (FRONTEND / "js" / "app.js").read_text()

    block = app_js.split("function renderFeedFollowingFilter", 1)[1].split("\nasync function ", 1)[0]
    assert "_feedFollowFilterOpen || Boolean(_feedAuthor)" in block
    # Choosing "kullanıcıya göre" from the filter menu is what opens it.
    assert "setFeedScope('following', { openFollowFilter: true })" in app_js
    # Changing tabs closes it again.
    assert "_feedFollowFilterOpen = openFollowFilter;" in app_js


def test_a_shell_page_starts_at_its_top_and_does_not_scroll_for_nothing():
    """Reported: pages opened with a header-sized gap and a stray scroll."""
    css = (FRONTEND / "css" / "source.css").read_text()
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    # The fixed header is hidden inside the shell, so its reserved space goes.
    assert "body.has-shell #view-profile" in css
    assert "body.has-shell #view-blend-result { padding-top: .75rem; }" in css
    # A full-height column plus the tab bar was always 4.5rem too tall — and
    # taller still on a phone with a home indicator.
    assert ".shell-column { min-height: calc(100dvh - 4.5rem - env(safe-area-inset-bottom)); }" in css
    assert "min-h-screen border-outline-variant/20" not in html
    # And the site footer no longer stacks under the tab bar.
    footer = app_js.split("const NO_FOOTER_VIEWS = [", 1)[1].split("]", 1)[0]
    for view in ("'profile'", "'tools'", "'inbox'", "'blends'", "'sinefil'"):
        assert view in footer, view
    assert "window.scrollTo(0, 0);" in app_js.split("function showView", 1)[1][:400]


def test_the_profile_dashboard_folds_its_heavy_sections_on_a_phone():
    """Reported: eleven stacked cards made the dashboard endless on mobile.

    Folded, the page measured 1970px instead of 4288px on a 390px screen.
    """
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    for section in ("kisayollar", "turler", "yonetmen", "ozet", "auteur", "gunce"):
        assert f'data-fold="{section}"' in html, section
        assert f"'{section}'" in app_js.split("FOLDED_ON_PHONE = [", 1)[1].split("]", 1)[0], section
    # Only phones start folded, and the reader's own choice outlives that.
    assert "matchMedia('(max-width: 1023px)')" in app_js
    assert "mb_folds" in app_js
    assert "[data-fold].is-folded .fold-body { display: none; }" in css
    # The dashboard cards are pinned to 42rem; folded they have to let go.
    assert ".profile-dashboard-card.is-folded" in css


def test_the_profile_button_is_the_members_own_avatar_on_every_screen():
    """Reported: the button showed a generic person icon and went missing."""
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    # One painter fills every avatar button, header and column headers alike.
    assert "function paintAvatarButtons" in app_js
    assert html.count("avatar-button") >= 2
    # Her ekranın kendi üst satırı bir tane taşır; sonuç ekranları da dahil.
    assert html.count("data-open-profile") == 11
    # Hidden only where there is no account yet.
    block = app_js.split("const showHeaderProfile", 1)[1].split(";", 1)[0]
    for view in ("'auth'", "'onboarding'", "'loading'", "'blend-loading'"):
        assert view in block, view
    assert "OWN_HEADER_VIEWS.includes(name)" in block


def test_the_deck_arrows_flank_the_poster_on_a_phone():
    """Asked for: the arrows belong either side of the film poster."""
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    # One pair sits in the poster row, the other stays in the row below; CSS
    # decides which is visible, so both use the same data attribute.
    assert 'class="deck-poster-row' in app_js
    assert 'class="deck-arrow"' in app_js
    assert "deck-row-arrow" in app_js
    assert ".deck-poster-row > .deck-arrow { display: flex; }" in css
    assert ".deck-row-arrow { visibility: hidden; }" in css


def test_the_desktop_dashboard_has_no_collapsibles():
    """Asked for: on the web every card is open, with no dropdown at all."""
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    # A phone-only preference never reaches the desktop layout.
    folds = app_js.split("function applyFolds", 1)[1].split("\n}", 1)[0]
    assert "const folded = !phone ? false :" in folds
    wide = css.split("@media (min-width: 1024px) {", 1)
    assert any(".fold-toggle, [data-fold] .fold-chevron { display: none !important; }" in part
               for part in css.split("@media"))
    assert "[data-fold].is-folded .fold-body { display: revert; }" in css


def test_the_phone_background_is_the_apps_own_tone():
    css = (FRONTEND / "css" / "source.css").read_text()

    block = css.split("@media (max-width: 767px) {", 1)
    assert "background-color: rgb(23 30 39)" in css
    # The opt-in light profile theme still wins on a phone.
    assert "body:not(.theme-light) { --color-surface-container-lowest: 23 30 39; }" in css


def test_the_phone_feed_shows_notes_until_the_pencil_is_tapped():
    """Asked for: the home feed is notes; writing opens from the pencil."""
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    # Hidden on a phone, always open from sm up.
    assert 'id="feed-composer" class="hidden border-b' in html
    assert "sm:block" in html.split('id="feed-composer"', 1)[1].split(">", 1)[0]
    assert "function toggleComposer" in app_js
    assert "function closeComposerOnPhone" in app_js
    # The pencil is a toggle: a second tap closes an empty box, but never
    # throws away something half-written.
    toggle = app_js.split("function toggleComposer", 1)[1].split("\n}", 1)[0]
    assert "composer.classList.add('hidden')" in toggle
    assert "$('feed-compose-text').value.trim() || _feedPickedFilm" in toggle
    # Reopening the feed would close it, so the order matters when already there.
    assert "if ($('view-feed').classList.contains('hidden'))" in app_js
    # Opening the feed and posting both leave it closed again on a phone.
    assert app_js.count("closeComposerOnPhone();") >= 2
    # One row for the three controls, all 40px tall.
    row = html.split('id="feed-compose-film"', 1)[1].split("</div>", 1)[0]
    assert "h-10" in row
    for control in ('id="btn-feed-post"', "spoiler-check"):
        assert control in html
    # The counter hangs in the text area's bottom-right, above Paylaş.
    counter = html.split('id="feed-compose-count"', 1)[1].split(">", 1)[0]
    assert "absolute bottom-1 right-0" in counter
    # And the spoiler box is round.
    assert ".spoiler-check {" in css
    assert "border-radius: 9999px" in css.split(".spoiler-check {", 1)[1].split("}", 1)[0]


def test_the_dashboard_sections_hide_their_actions_until_opened():
    """Asked for: no pencil, clapper or image icon on a closed section."""
    html = (FRONTEND / "index.html").read_text()

    for fold in ("auteur", "gunce"):
        head = html.split(f'data-fold="{fold}"', 1)[1].split('<div class="fold-body">', 1)[0]
        icons = [line for line in head.splitlines() if "material-symbols-outlined" in line]
        # Only the chevron survives in a collapsed header.
        assert len(icons) == 1, (fold, icons)
        assert "fold-chevron" in icons[0], fold
    # The edit and PNG buttons moved inside, so they appear with the content.
    for section, button in (("gunce", "profile-recent-share"),):
        body = html.split(f'data-fold="{section}"', 1)[1].split('<div class="fold-body">', 1)[1]
        assert f'id="{button}"' in body.split("</section>", 1)[0], section


def test_the_blend_page_opens_on_its_name_box():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    blends = html.split('id="view-blends"', 1)[1].split('id="view-sinefil"', 1)[0]
    # Heading, then the tool host — no prose in between.
    assert blends.index("Blendler</h1>") < blends.index('id="blend-tools-host"')
    assert "İstekler, tamamlanmış blendler" not in blends
    # The embedded tool drops its own title and top spacing.
    assert "$('quick-tools-head')?.classList.add('hidden');" in app_js
    assert "quick-tools--embedded" in app_js
    assert ".quick-tools--embedded" in css


def test_the_letter_button_sits_beside_follow_at_the_same_height():
    """Reported: the letter icon hung below the stats, out of line with follow."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    header = app_js.split("function userHeaderMarkup", 1)[1].split("\n}", 1)[0]
    # One row: blend, then letter, then follow — measured at 36px each.
    assert "${blendAction}${letterAction}${followButton(profile)}" in header
    assert "h-9 w-9" in header.split("const iconAction", 1)[1].split("\n", 1)[0]
    follow = app_js.split("function followButton", 1)[1].split("\n}", 1)[0]
    assert "h-9" in follow


def test_a_whole_member_card_opens_the_profile():
    """Reported: only the avatar was clickable."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    card = app_js.split("function sinefilCard", 1)[1].split("\n}", 1)[0]
    assert '<article data-sinefil-profile=' in card
    assert 'role="link"' in card
    # The follow button inside it still does its own job.
    handler = app_js.split("$('sinefil-grid').addEventListener('click'", 1)[1].split("});", 1)[0]
    assert handler.index("data-follow") < handler.index("data-sinefil-profile")


def test_the_recommendation_card_can_be_swiped_on_a_phone():
    """Asked for: drag the card sideways, with a slight tilt."""
    app_js = (FRONTEND / "js" / "app.js").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    block = app_js.split("function bindRecoSwipe", 1)[1].split("\n}\n", 1)[0]
    for event in ("touchstart", "touchmove", "touchend", "touchcancel"):
        assert f"'{event}'" in block, event
    assert "rotate(${tilt}deg)" in block
    # A vertical drag belongs to the page, not the card.
    assert "Math.abs(moveY) > Math.abs(moveX)" in block
    # The end of the pool does not fling into nothing.
    assert "const allowed" in block
    assert ".reco-swipe { touch-action: pan-y" in css
    assert "prefers-reduced-motion" in css


def test_the_recommendation_card_keeps_only_the_reason():
    """Reported: the taste paragraph and the plot summary were noise."""
    app_js = (FRONTEND / "js" / "app.js").read_text()
    reco_js = (FRONTEND / "js" / "recommendations.js").read_text()

    # Poster, title, director, genres and "sana neden önerdik" — nothing else.
    assert "function overviewBlock() {\n  return '';\n}" in reco_js
    assert "const shortOverview = '';" in reco_js
    assert "whyBlock" in reco_js
    # And the summary paragraph above the card is gone.
    assert "escapeHTML(o.summary)" not in app_js


def test_a_refresh_reopens_the_page_you_were_on():
    """Reported: refreshing anywhere landed back on the feed, after a flash of
    the Movienotes-headed shell."""
    app_js = (FRONTEND / "js" / "app.js").read_text()
    html = (FRONTEND / "index.html").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()

    # The open screen is written to the address bar. It now also leaves a
    # history entry, so the phone's back gesture walks the app — see
    # test_the_phones_back_gesture_walks_the_app_instead_of_closing_it.
    assert "history.replaceState({ view: name }, '', next)" in app_js
    assert "async function restoreRoute" in app_js
    assert "restoreRoute()" in app_js
    for route in ("akis", "bildirimler", "mektuplar", "blend", "kesfet", "profil", "araclar"):
        assert f"'{route}'" in app_js.split("const ROUTE_OF_VIEW", 1)[1].split("}", 1)[0], route
    # And the shell stays invisible until the first real screen is painted.
    assert 'class="is-booting' in html
    assert "body.is-booting #app-header" in css
    assert "classList.remove('is-booting')" in app_js


def test_mobile_drops_a_layer_of_card_chrome():
    """Reported: cards inside cards inside cards, three deep on a phone."""
    html = (FRONTEND / "index.html").read_text()
    css = (FRONTEND / "css" / "source.css").read_text()
    reco_js = (FRONTEND / "js" / "recommendations.js").read_text()

    assert "@media (max-width: 767px)" in css
    flat = css.split(".mobile-flat {", 1)[1].split("}", 1)[0]
    for rule in ("border: 0", "background: transparent", "box-shadow: none"):
        assert rule in flat, rule
    # The outer wrappers give up their frame; the film card itself keeps one.
    assert html.count("mobile-flat") >= 8
    assert "mobile-flat" in reco_js


def test_the_letter_screen_is_one_pane_on_every_size():
    """Reported: tapping a person should hand the whole screen to that letter."""
    app_js = (FRONTEND / "js" / "app.js").read_text()
    html = (FRONTEND / "index.html").read_text()

    block = app_js.split("function renderLetterWorkspace", 1)[1].split("\n}", 1)[0]
    assert "const open = Boolean(_openLetterThread)" in block
    assert "'hidden', open" in block
    # No leftover two-column grid or capped heights to scroll past.
    assert "md:grid-cols-[minmax(220px,0.72fr)_minmax(0,1.55fr)]" not in html
    assert "max-h-[42vh]" not in html
    assert "isCompactLetterWorkspace" not in app_js


def test_the_compose_button_only_shows_where_there_is_something_to_write():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "const showFab = on && ['feed', 'thread'].includes(name);" in app_js
    # And the letter button steps aside once a conversation is open.
    assert "name === 'inbox' && !_openLetterThread" in app_js


def test_feed_can_filter_to_visible_film_notes_and_orders_community_by_engagement():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert 'id="btn-feed-film-filter"' in html
    # The paragraph that narrated the ordering is gone from the header, which
    # had to earn back its height; the ordering itself is unchanged.
    assert "feed-sort-note" not in html
    assert "feed-sort-note" not in app_js
    assert "_feedFilmPickerMode === 'filter'" in app_js
    assert '`/api/feed/films?q=${encodeURIComponent(query)}`' in app_js
    assert "sort = _feedScope === 'community' ? 'engagement' : 'recent'" in app_js


def test_feed_has_own_notes_and_a_following_person_filter_without_mobile_trends():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert 'data-feed-scope="mine"' in html
    assert 'id="feed-follow-filter"' in html
    assert "function renderFeedFollowingFilter" in app_js
    assert "author=${encodeURIComponent(_feedAuthor)}" in app_js
    # The inline trend rail is desktop/tablet-only; phone has the compact feed.
    trend = html.split('id="feed-trending" class="', 1)[1].split('"', 1)[0]
    assert "hidden" in trend
    assert "lg:block" in trend
    card = app_js.split("function feedPostCard", 1)[1].split("\nfunction ", 1)[0]
    assert "feed-actions mt-auto flex h-9 shrink-0 items-center" in card


def test_a_correspondence_continues_from_the_inbox():
    """Replying used to mean finding the person again in Sinefil Sineması."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "function letterReplyBar" in app_js
    assert "data-letter-reply=" in app_js
    assert "letterReplyBar(peer)" in app_js
    # The reply opens the same composer the directory uses.
    reply = app_js.split("data-letter-reply]", 1)[1].split("}", 1)[0]
    assert "openLetterCompose" in reply
    # A cooldown only applies to the same correspondence, not every recipient.
    assert "loadLetterSendStatus(username)" in app_js
    # And answering does not collapse the conversation you were reading.
    assert "_openLetterThread" in app_js


def test_writing_a_letter_requires_your_own_letterbox_to_be_open():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    schema = (ROOT / "supabase" / "schema.sql").read_text()

    assert 'id="dialog-letter-enable"' in html
    assert 'id="btn-letter-enable-confirm"' in html
    assert "_account?.letter_receiving_enabled" in app_js
    assert "dialog-letter-enable').showModal()" in app_js
    # Enforced server-side too, not just hidden in the UI.
    assert "letter_sender_closed" in schema
    assert "letter_sender_closed" in (ROOT / "app" / "main.py").read_text()


def test_cinema_bulletin_scrolls_horizontally_only():
    """The card is a strip, not a scrolling page inside a page."""
    css = (FRONTEND / "css" / "source.css").read_text()
    js = (FRONTEND / "js" / "app.js").read_text()
    html = (FRONTEND / "index.html").read_text()

    strip = css.split(".bulletin-strip {", 1)[1].split("}", 1)[0]
    assert "overflow-x: auto" in strip
    assert "overflow-y: hidden" in strip
    assert "scroll-snap-type: x mandatory" in strip
    # Vertical padding keeps the highlight ring off the clipping edge.
    padding = [line for line in strip.splitlines() if line.strip().startswith("padding:")]
    assert padding, "the strip needs vertical padding or the card rings clip"
    top = padding[0].split("padding:", 1)[1].strip().rstrip(";").split()[0]
    assert top not in ("0", "0px"), "a zero top padding clips the ring again"
    # A vertically scrolling grid was what this replaced.
    assert "max-h-[70vh] overflow-y-auto" not in js

    # Priority films lead, the rest arrive on demand.
    assert "bulletinMoreCard" in js
    assert "_bulletinExpanded" in js
    # Venue choice opens a dialog because a dropdown would be clipped by the strip.
    assert 'id="dialog-bulletin-venues"' in html
    assert "openBulletinVenues" in js


def test_a_sidebar_label_that_wraps_still_lines_up_with_the_others():
    """Reported with a screenshot: "What should I watch?" broke the column.

    A button centres its own text, so the one item long enough to wrap sat
    centred while every other label started at the same left edge. The English
    label is now short enough to fit, and the rule holds whatever the label.
    """
    css = (FRONTEND / "css" / "source.css").read_text()
    en = (FRONTEND / "js" / "i18n.js").read_text()

    block = css.split(".nav-item {", 1)[1].split("}", 1)[0]
    assert "text-align: left" in block
    # And the icon keeps its size instead of being squeezed by a long label.
    assert ".nav-item > .material-symbols-outlined { flex: 0 0 auto; }" in css

    assert "'Ne izlesem?': 'What to watch?'" in en
    assert "What should I watch?" not in en.split("'Ne izlesem?'", 1)[1][:80]


def test_the_archive_scan_warns_before_the_areas_that_depend_on_it():
    """Asked for: say the scan is still running, and say how far along it is.

    Recommendations, Blend and the Cinephile ranking all read the full
    archive, so during the sweep they answer from a partial one. The warning
    is not a lock: it reports progress as scanned/total, points at what works
    today, and lets the member go in anyway.
    """
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "js" / "app.js").read_text()

    assert 'id="dialog-sweep-gate"' in html
    assert 'id="sweep-gate-count"' in html
    assert 'id="sweep-gate-continue"' in html

    gated = js.split("const SWEEP_GATED = {", 1)[1].split("\n};", 1)[0]
    for area in ("watch:", "blend:", "sinefil:"):
        assert area in gated, area

    # Every gated area routes through the same check, and the check yields.
    assert "withSweepGate('sinefil'" in js
    assert "withSweepGate(which, () => openQuickTool(which, { ...options, gated: true }))" in js
    gate = js.split("function withSweepGate", 1)[1].split("\n}", 1)[0]
    assert "_isSweepActive(_sweepJob)" in gate
    # Warned once per area, not on every visit.
    assert "_sweepGateShown.has(area)" in gate


def test_the_progress_reading_is_scanned_over_total():
    js = (FRONTEND / "js" / "app.js").read_text()
    sync = (ROOT / "app" / "profile_sync.py").read_text()

    block = js.split("function _sweepCountText", 1)[1].split("\n}", 1)[0]
    assert "job?.processed" in block and "job?.total" in block

    # The diary crawl cannot know a total until it runs out of pages, so the
    # member's own published film count stands in for it; without that the
    # reading was a number that only climbed.
    crawl = sync.split("async def _crawl", 1)[1].split("\nasync def ", 1)[0]
    assert 'letterboxd_stats' in crawl
    assert "films_total=max(expected_total, processed)" in crawl


def test_finishing_the_scan_announces_itself_and_offers_the_profile():
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "js" / "app.js").read_text()

    assert 'id="dialog-sweep-done"' in html
    assert 'id="sweep-done-profile"' in html

    block = js.split("function announceSweepComplete", 1)[1].split("\n}\n", 1)[0]
    assert "goNav('profile')" in block
    assert "dialog.showModal()" in block

    # The watcher is not tied to the profile page, or the announcement would
    # only ever reach someone already looking at it.
    poll = js.split("async function pollSweepOnce", 1)[1].split("\n}\n", 1)[0]
    assert "announceSweepComplete(job)" in poll
    assert "_sweepWasActive" in poll
    # The single check made at entry has to schedule the next one, or someone
    # who never opens their profile is never told the scan finished.
    assert "startSweepPoll(); return;" in poll


def test_shell_asset_content_changes_force_a_version_bump():
    """Guard against shipping edits that browsers never fetch.

    Versioned assets are served ``immutable`` for a year, so a change that keeps
    the old ``?v=`` is invisible to every returning visitor. Pinning the digests
    here makes that a failing test instead of a silent no-op: when this fails,
    bump the version in index.html (and the expectation above), then paste the
    new digest.

    The manifest and the icons are in this list because leaving them out cost a
    release: the rename changed the app's name and icon paths but kept
    ``?v=20260910.5``, so every browser that had already installed the app kept
    serving itself a year-old manifest calling it Movieboxd and pointing at icon
    files that no longer existed.
    """
    expected = {
        "js/app.js": "94945523092fe8979b6434f5ab8841a15c268988bc9837804f59f7b7c688325d",
        "app.css": "e0d4d0a3da619f189f862d86a9fbd32e2ebdf6799b83b0ad8a7f4cfd7fce38d7",
        "js/share-cards.js": "a94e9ee8aa8fa4a15a7f6e8ad75136de87314cedf28431084350d504c5c32863",
        "js/i18n.js": "419a0b95cc249f5319ceb2765e5bdd2f10bb87e081544d9dcb7ed1916f5d934a",
        "site.webmanifest": "7a7de349179ed9f226d38632dfde5a8478edd10305972ea52641b0dc6aa7f405",
        "movienotes-mark.png": "850aa9117aa52768952843f8e2c410c0c17868877d81b2058274290373b4ee1e",
        "movienotes-icon-192.png": "3b04c52ffd23799ce424f1acefd0a1d7c386b8c968b09be9bd5c87b623c6ac12",
        "movienotes-icon-512.png": "850aa9117aa52768952843f8e2c410c0c17868877d81b2058274290373b4ee1e",
        "movienotes-icon-maskable.png": "64c553f91ab5aec3cff9a5921213a658b42a1229bcc9495ebda75cb784280615",
    }
    for path, digest in expected.items():
        actual = hashlib.sha256((FRONTEND / path).read_bytes()).hexdigest()
        assert actual == digest, (
            f"{path} changed but its ?v= may not have. Bump the version in "
            f"frontend/index.html, then set the digest here to {actual}."
        )


def test_blend_watchlist_renders_common_and_bridge_picks_as_one_five_film_list():
    app_js = (FRONTEND / "js" / "app.js").read_text()
    render = app_js.split("function renderBlendWatchlist", 1)[1].split(
        "// ── Blend SSE flow", 1
    )[0]

    assert "const combined = [...common, ...bridge].slice(0, 5);" in render
    assert "show(combined);" in render
    assert "common.length - 1" not in render


def test_every_app_shell_asset_has_an_explicit_immutable_version():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    auth_js = (FRONTEND / "js" / "auth.js").read_text()
    profile_js = (FRONTEND / "js" / "profile.js").read_text()
    recommendations_js = (FRONTEND / "js" / "recommendations.js").read_text()
    share_js = (FRONTEND / "js" / "share-cards.js").read_text()
    source_css = (FRONTEND / "css" / "source.css").read_text()

    dependency_version = "v=20260902.15"
    api_version = "v=20260920.20"
    css_version = "v=20260920.20"
    assert f"/static/app.css?{css_version}" in html
    assert "/static/js/app.js?v=20260920.20" in html
    assert "./i18n.js?v=20260920.20" in app_js
    assert app_js.count(f"?{dependency_version}") == 2
    assert f"./api.js?{api_version}" in app_js
    assert "./recommendations.js?v=20260920.20" in app_js
    assert "./share-cards.js?v=20260920.20" in app_js
    assert "./auth.js?v=20260920.20" in app_js
    assert f"./dom.js?{dependency_version}" in auth_js
    assert "./i18n.js?v=20260920.20" in auth_js
    assert f"./dom.js?{dependency_version}" in profile_js
    assert "./i18n.js?v=20260920.20" in profile_js
    assert f"./dom.js?{dependency_version}" in recommendations_js
    assert "./i18n.js?v=20260920.20" in recommendations_js
    assert f"./api.js?{api_version}" in share_js
    assert "./i18n.js?v=20260920.20" in share_js
    assert f"criterion-closet-bg.jpg?{dependency_version}" in source_css


def test_body_font_uses_the_original_geist_app_stack():
    html = (FRONTEND / "index.html").read_text()
    config = (ROOT.parent / "tailwind.config.cjs").read_text()

    assert "family=Lora" not in html
    assert "'body-md': ['Geist']" in config
    assert "'body-lg': ['Geist']" in config
    assert "'label-md': ['Space Grotesk', 'Geist']" in config


def test_mobile_navigation_keeps_recommendations_and_profile_discoverable():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()

    tabbar = html.split('id="app-tabbar"', 1)[1].split("</nav>", 1)[0]
    assert 'id="tab-tools-toggle"' in tabbar
    assert 'id="tools-directory"' in html
    assert 'data-nav="profile"' not in tabbar
    assert 'id="btn-header-profile"' in html
    assert "headerProfile.classList.toggle('flex', showHeaderProfile);" in app_js
    assert "function openToolsDirectory()" in app_js
    assert "data-tools-page=\"blend\"" in html
    assert "data-tools-page=\"watch\"" in html
    assert "openQuickTool(button.dataset.toolsPage, { parent: 'tools' });" in app_js
    assert 'id="btn-tools-back"' in html
    assert "function returnToToolParent(kind)" in app_js
    assert 'id="blend-tools-host"' in html
    assert "if (_account?.username && username.toLowerCase() === _account.username.toLowerCase())" in app_js


def test_blend_page_prioritizes_saved_blends_and_moves_blocks_to_settings():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    blends = html.split('id="view-blends"', 1)[1].split('id="view-sinefil"', 1)[0]

    assert blends.index("Blendlerim") < blends.index("Gelen istekler")
    assert "Tamamlanmış blendler" not in blends
    assert "Sonuçlanan istekler" not in blends
    assert 'id="blend-history"' not in html
    assert 'id="blend-blocked"' not in html
    assert 'id="menu-blocked-users"' in html
    assert "async function openBlockedUsers()" in app_js
    assert "function blendHistoryCard" not in app_js


def test_profile_hero_combines_favorites_with_a_full_account_reading():
    html = (FRONTEND / "index.html").read_text()
    app_js = (FRONTEND / "js" / "app.js").read_text()
    profile = html.split('id="view-profile"', 1)[1].split('id="view-tools"', 1)[0]

    assert profile.index('id="profile-favorites"') < profile.index('id="profile-sample-size"')
    assert 'id="profile-favorites" class="grid grid-cols-2' in profile
    assert 'id="profile-favorite-director-name"' in profile
    assert 'id="profile-account-summary"' in profile
    assert 'Fav 4 kişilik okuması' not in profile
    assert 'function accountSummaryFromTaste' in app_js
    assert "streamText($('profile-account-summary'), accountSummaryFromTaste(taste));" in app_js
    assert "Profil kartını paylaş" in profile
    assert profile.index('id="profile-settings-btn"') < profile.index('profile-rise')


def test_png_share_renderer_is_lazy_loaded_on_first_share_action():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    imports = app_js.split("// ── Cinema facts", 1)[0]
    assert "from './share-cards.js" not in imports
    assert "import('./share-cards.js?v=20260920.20')" in imports
    assert "const shareCards = await loadShareCardsModule();" in app_js


def test_pwa_requests_share_one_silent_session_refresh_after_access_expiry():
    api_js = (FRONTEND / "js" / "api.js").read_text()

    assert "let sessionRefreshPromise = null;" in api_js
    assert "response.status === 401 && !recovered && canRecoverSession(path)" in api_js
    assert "await refreshExpiredSession();" in api_js
    assert "credentials: 'same-origin'" in api_js
    assert "cache: 'no-store'" in api_js


def test_sync_progress_polling_does_not_reload_the_full_profile_snapshot():
    app_js = (FRONTEND / "js" / "app.js").read_text()

    sweep_poll = app_js.split("function startSweepPoll()", 1)[1].split(
        "function stopSweepPoll()", 1
    )[0]
    assert "apiJSON('/api/profile/sync-status')" in sweep_poll
    assert "apiJSON('/api/profile/me')" not in sweep_poll
    # The full snapshot is read once the sweep is over, never on every tick.
    assert "if (active) { _sweepWasActive = true; startSweepPoll(); return; }" in sweep_poll
    assert sweep_poll.count("loadProfile()") == 1
    # The progress strip is the only thing that polls the sweep. Onboarding
    # gave up waiting for the archive, so it must not open a second poller.
    onboarding = app_js.split("async function startOnboarding", 1)[1].split(
        "async function boot()", 1
    )[0]
    assert "/api/profile/sync-status" not in onboarding


def test_onboarding_slides_only_use_data_the_archive_sweep_is_not_needed_for():
    """Asked for: onboarding must not promise anything the sweep still owes.

    The director ranking needs the whole watched history, so its slide is gone.
    Everything left comes from the one profile-page read (Letterboxd's own stat
    row, the Fav 4, the reading built on them) plus a single diary page for the
    newest film.
    """
    app_js = (FRONTEND / "js" / "app.js").read_text()

    build = app_js.split("async function startOnboarding", 1)[1].split(
        "async function boot()", 1
    )[0]
    slides = build.split("const slides = [", 1)[1].split("].filter(Boolean)", 1)[0]
    order = [
        "_obRenderWelcome",
        "_obRenderNumbers",
        "_obRenderLastWatched",
        "_obRenderFavs",
        "_obRenderPersonality",
        "_obRenderSinefilConsent",
    ]
    positions = [slides.index(name) for name in order]
    assert positions == sorted(positions), slides
    # The retired full-sweep surfaces must not come back.
    for gone in ("_obRenderDirector", "_obRenderOutro", "_obAwaitFullSweep"):
        assert gone not in app_js, gone

    # Numbers come off the scraped profile header, never the sweep's counters.
    assert "stats.films" in build and "stats.this_year" in build
    assert "stats.lists" in build
    assert "apiJSON('/api/profile/recent?preview=1')" in build
    # Consent is the last slide, so its button is the one that opens the app.
    assert slides.rstrip().rstrip(",").endswith("_obRenderSinefilConsent()")


def test_the_profile_says_which_cards_the_running_sweep_still_owes():
    """Asked for: unfinished sections read as loading, not as broken."""
    app_js = (FRONTEND / "js" / "app.js").read_text()

    assert "function _isSweepActive" in app_js
    assert "function _profilePendingCard" in app_js
    # The snapshot renderer decides per card, so it needs the job up front.
    render = app_js.split("function renderPersistedProfile", 1)[1].split(
        "// ── Sinefil Akışı", 1
    )[0]
    assert "const sweeping = _isSweepActive(data.sync_job);" in render
    assert render.count("_profilePendingCard(") == 2
    assert "Arşivin taranıyor" in render
    # Progress polling keeps the stored job fresh for those same decisions.
    poll = app_js.split("function startSweepPoll()", 1)[1].split(
        "function stopSweepPoll()", 1
    )[0]
    assert "_persistedProfile.sync_job = job;" in poll


def test_profile_entry_sync_checks_fav4_before_any_full_crawl():
    app_js = (FRONTEND / "js" / "app.js").read_text()
    main_py = (ROOT / "app" / "main.py").read_text()

    assert "function checkFavoriteFreshness" in app_js
    assert "apiJSON('/api/profile/favorites/check'" in app_js
    assert "function queueEntrySync" in app_js
    assert "apiJSON('/api/profile/entry-sync'" in app_js
    assert '@app.post("/api/profile/favorites/check")' in main_py
    assert '@app.post("/api/profile/entry-sync")' in main_py
    assert "resolve_posters=False" in main_py
    assert "save_profile_identity_and_favorites" in main_py


def test_a_new_member_is_invited_to_install_the_app_too():
    """Kaydolan üye kurulum çağrısını ilk oturumunda görmeli.

    `enterApp` onboarding gerekiyorsa erken dönüyor, dolayısıyla kurulum
    diyaloğunu çağıran satıra hiç ulaşılmıyordu: yeni üye çağrıyı ancak bir
    sonraki girişinde görüyordu. Onboarding bittiğinde de çağrılıyor artık.
    """
    app_js = (FRONTEND / "js" / "app.js").read_text()

    finish = app_js.split("function finishOnboarding()", 1)[1].split("\nasync function ", 1)[0]
    assert "setTimeout(showInstallAppDialog, 1500)" in finish
    # Onboarding kilitli tam ekran: modal onun üstüne binmemeli.
    guard = app_js.split("function showInstallAppDialog()", 1)[1].split("\n}", 1)[0]
    assert "_shownView === 'onboarding'" in guard
    # Zaten kurulu bir uygulamada veya Chrome çağrıyı göndermediyse açılmıyor.
    assert "isInstalledApp()" in guard
    assert "!_deferredInstallPrompt" in guard


def test_iphone_gets_instructions_because_ios_cannot_install_by_itself():
    """iOS'ta `beforeinstallprompt` hiçbir tarayıcıda gönderilmiyor.

    Safari, Chrome, Edge — hepsi WebKit üzerinde çalışıyor, dolayısıyla çağrıyı
    tarayıcıya soramıyoruz ve kurulumu programatik başlatamıyoruz. Tek yol
    paylaş menüsü, o yüzden düğme yerine adımlar gösteriliyor.
    """
    app_js = (FRONTEND / "js" / "app.js").read_text()
    html = (FRONTEND / "index.html").read_text()

    # Tarayıcıya değil platforma bakılıyor; iPadOS 13+ kendini Mac sanıyor.
    detect = app_js.split("function isIOS()", 1)[1].split("\n}", 1)[0]
    assert "/iPhone|iPad|iPod/i.test(ua)" in detect
    assert "navigator.maxTouchPoints" in detect

    # Desktop Safari cannot be asked either, so the guard is about the
    # capability now rather than the platform — see
    # test_safari_is_told_how_to_install_since_it_cannot_be_asked.
    show = app_js.split("function showInstallAppDialog()", 1)[1].split("\n}", 1)[0]
    assert "if (!manual && !_deferredInstallPrompt) return;" in show
    assert "$('install-ios-steps').classList.toggle('hidden', !manual)" in show
    assert "$('btn-install-app').classList.toggle('hidden', manual)" in show

    # Ana ekrana ekleme adımları, sırasıyla.
    steps = html.split('id="install-ios-steps"', 1)[1].split("</ol>", 1)[0]
    assert "Paylaş" in steps and "Ana Ekrana Ekle" in steps and "Ekle</strong>’ye bas" in steps
    # Her adımın metni tek bir span: flex gap'i cümlenin ortasına girmesin.
    assert steps.count("data-install-step=") == 3

    # iOS'ta "kuruldu" sinyali yok; kapatma hatırlanmazsa çağrı her girişte çıkar.
    assert "IOS_INSTALL_HINT_DAYS = 30" in app_js
    assert "localStorage.setItem(IOS_INSTALL_HINT_KEY" in app_js
    assert "if (manual && iosHintSilenced()) return;" in show


def test_a_pydantic_field_validation_error_surfaces_its_real_message():
    """A bad username (e.g. register/start) fails FastAPI's own request
    validation before our endpoint code runs, so `detail` comes back as a
    list of {msg, loc, ...} objects instead of the plain string every other
    error uses. `apiJSON` used to do `new Error(payload.detail)` on that
    list, which silently stringifies to "[object Object]" — the real
    message (e.g. "Letterboxd kullanıcı adı 2–15 karakter olmalı") never
    reached the register screen, so submitting looked like it did nothing.
    Runs the actual frontend module under Node with a mocked `fetch`.
    """
    api_js = FRONTEND / "js" / "api.js"
    script = """
    globalThis.window = { __API_BASE__: '' };
    globalThis.fetch = async () => ({
      ok: false,
      status: 422,
      headers: { get: () => '' },
      json: async () => ({
        detail: [{
          type: 'value_error',
          loc: ['body', 'username'],
          msg: 'Value error, Letterboxd kullan\\u0131c\\u0131 ad\\u0131 2\\u201315 karakter olmal\\u0131',
        }],
      }),
    });
    const { apiJSON } = await import(%s);
    try {
      await apiJSON('/api/auth/register/start', { method: 'POST' });
      console.log(JSON.stringify({ ok: true }));
    } catch (error) {
      console.log(JSON.stringify({ ok: false, message: error.message }));
    }
    """ % json.dumps(api_js.resolve().as_uri())

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["message"] == "Letterboxd kullanıcı adı 2–15 karakter olmalı"
    assert "[object Object]" not in payload["message"]
