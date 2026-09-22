-- Supabase şeması — Supabase Studio > SQL Editor'da çalıştırın.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Kullanıcı takibi: her Letterboxd kullanıcı adı için bir satır.
CREATE TABLE IF NOT EXISTS public.users (
  id            BIGSERIAL    PRIMARY KEY,
  username      TEXT         UNIQUE NOT NULL,
  created_at    TIMESTAMPTZ  DEFAULT now(),
  last_seen_at  TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON public.users (username);

-- Account migration: existing anonymous tracking rows remain valid and can be
-- claimed after Letterboxd ownership verification.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS auth_user_id UUID UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS avatar_url TEXT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS account_status TEXT NOT NULL DEFAULT 'anonymous';
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS profile_sync_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS ownership_verified_at TIMESTAMPTZ;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS profile_synced_at TIMESTAMPTZ;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS letterboxd_stats JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
-- User-curated "top 10 films" (ordered list of watched film slugs). Empty =
-- fall back to the highest-rated watched films.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS top_films JSONB NOT NULL DEFAULT '[]'::jsonb;
-- Sinefil Sineması varsayılan olarak açıktır; kullanıcı profilinden kapatabilir.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS discoverable BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE public.users SET discoverable = TRUE
WHERE account_status = 'active' AND (discoverable IS NULL OR discoverable = FALSE);
-- Mektup kutusu, Sinefil Sineması gibi varsayılan olarak açıktır; izole
-- kalmak isteyen profilinden kapatır. Aynı tercih gönderme hakkını da belirler:
-- kutusu kapalı bir hesap mektup yollayamaz.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS letter_receiving_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.users ALTER COLUMN letter_receiving_enabled SET DEFAULT TRUE;
-- Kilitli hesaplar Sinefil Sineması'nda kart olarak görünür; notları ve
-- ayrıntılı profilleri ancak kabul edilmiş takipçilerine açılır.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS private_account BOOLEAN NOT NULL DEFAULT FALSE;
-- UI language: `auto` follows the device language; an explicit value follows
-- the account across devices.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS preferred_locale TEXT NOT NULL DEFAULT 'auto';
-- Var olan hesaplar bir kereliğine `scripts.open_letterboxes` ile açıldı.
-- Toplu UPDATE bilerek burada değil: şema her uygulandığında çalışır ve
-- kutusunu kapatanların tercihini sessizce geri alırdı.

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_auth_user_id
  ON public.users (auth_user_id) WHERE auth_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_sinefil_directory
  ON public.users (username)
  WHERE account_status = 'active' AND profile_sync_status = 'ready' AND discoverable = TRUE;

CREATE INDEX IF NOT EXISTS idx_users_letter_receivers
  ON public.users (username)
  WHERE account_status = 'active' AND letter_receiving_enabled = TRUE;

-- This constraint is deliberately replaced rather than only created when
-- absent: existing installations may have the earlier version without
-- `verification_deferred`.  That status lets a confirmed user continue to
-- onboarding when Letterboxd temporarily blocks the bio check.
ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_account_status_check;
ALTER TABLE public.users ADD CONSTRAINT users_account_status_check
  CHECK (account_status IN (
    'anonymous', 'pending_verification', 'verification_deferred', 'active', 'disabled'
  ));

DO $$ BEGIN
  ALTER TABLE public.users ADD CONSTRAINT users_profile_sync_status_check
    CHECK (profile_sync_status IN ('pending', 'syncing', 'ready', 'stale', 'failed'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE public.users ADD CONSTRAINT users_preferred_locale_check
    CHECK (preferred_locale IN ('auto', 'tr', 'en'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS public.taste_profiles (
  user_id             BIGINT PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  summary             TEXT NOT NULL DEFAULT '',
  favorite_director   TEXT NOT NULL DEFAULT '',
  top_directors       JSONB NOT NULL DEFAULT '[]'::jsonb,
  top_directors_detail JSONB NOT NULL DEFAULT '[]'::jsonb,
  top_genres          JSONB NOT NULL DEFAULT '[]'::jsonb,
  top_keywords        JSONB NOT NULL DEFAULT '[]'::jsonb,
  analysis            JSONB NOT NULL DEFAULT '[]'::jsonb,
  personality         TEXT NOT NULL DEFAULT '',
  sample_size         INTEGER NOT NULL DEFAULT 0,
  rated_count         INTEGER NOT NULL DEFAULT 0,
  metadata_coverage   INTEGER NOT NULL DEFAULT 0 CHECK (metadata_coverage BETWEEN 0 AND 100),
  confidence_level    TEXT NOT NULL DEFAULT 'low' CHECK (confidence_level IN ('low', 'medium', 'high')),
  confidence_score    INTEGER NOT NULL DEFAULT 0 CHECK (confidence_score BETWEEN 0 AND 100),
  algorithm_version   TEXT NOT NULL,
  source_fingerprint  TEXT NOT NULL DEFAULT '',
  generated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.taste_profiles
  ADD COLUMN IF NOT EXISTS source_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE public.taste_profiles
  ADD COLUMN IF NOT EXISTS top_directors JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE public.taste_profiles
  ADD COLUMN IF NOT EXISTS top_directors_detail JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE public.taste_profiles
  ADD COLUMN IF NOT EXISTS analysis JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE public.taste_profiles
  ADD COLUMN IF NOT EXISTS personality TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS public.profile_favorites (
  user_id       BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  position      SMALLINT NOT NULL CHECK (position BETWEEN 1 AND 4),
  slug          TEXT NOT NULL,
  title         TEXT NOT NULL,
  release_year  INTEGER,
  tmdb_id       INTEGER,
  poster_url    TEXT,
  PRIMARY KEY (user_id, position)
);

-- Full watched-history store: one row per film the user has logged. This is the
-- source of truth the taste analysis aggregates over, so an incremental sync
-- only has to upsert the delta instead of re-scraping the whole history.
CREATE TABLE IF NOT EXISTS public.user_watched_films (
  user_id        BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  film_slug      TEXT NOT NULL,
  title          TEXT NOT NULL DEFAULT '',
  release_year   INTEGER,
  tmdb_id        INTEGER,
  director       TEXT NOT NULL DEFAULT '',
  genres         JSONB NOT NULL DEFAULT '[]'::jsonb,
  keywords       JSONB NOT NULL DEFAULT '[]'::jsonb,
  user_rating    REAL,
  rating_observed BOOLEAN NOT NULL DEFAULT FALSE,
  poster_url     TEXT,
  poster_resolver_url TEXT,
  watched_rank   INTEGER,          -- /films/ listing position, which Letterboxd
                                   -- orders by release date. NOT watch order:
                                   -- diary pages or the RSS feed give that.
  details_loaded BOOLEAN NOT NULL DEFAULT FALSE,
  last_seen_run_id UUID,
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, film_slug)
);

ALTER TABLE public.user_watched_films
  ADD COLUMN IF NOT EXISTS poster_url TEXT;
ALTER TABLE public.user_watched_films
  ADD COLUMN IF NOT EXISTS poster_resolver_url TEXT;
ALTER TABLE public.user_watched_films
  ADD COLUMN IF NOT EXISTS rating_observed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.user_watched_films
  ADD COLUMN IF NOT EXISTS last_seen_run_id UUID;
ALTER TABLE public.user_watched_films
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_user_watched_films_rank
  ON public.user_watched_films (user_id, watched_rank);
CREATE INDEX IF NOT EXISTS idx_user_watched_films_director
  ON public.user_watched_films (user_id, director);
-- The random pick aggregates the whole membership by film, not by user.
CREATE INDEX IF NOT EXISTS idx_user_watched_films_slug_active
  ON public.user_watched_films (film_slug) WHERE is_active;

-- Shared, account-independent film catalog. Filled by every scrape/enrichment
-- path across all users. Public film metadata deliberately survives account
-- deletion so the application gets faster as its catalog grows.
CREATE TABLE IF NOT EXISTS public.film_posters (
  film_slug     TEXT PRIMARY KEY,
  poster_url    TEXT,
  poster_resolver_url TEXT,
  tmdb_id       INTEGER,
  title         TEXT NOT NULL DEFAULT '',
  release_year  INTEGER,
  overview      TEXT NOT NULL DEFAULT '',
  director      TEXT NOT NULL DEFAULT '',
  genres        JSONB NOT NULL DEFAULT '[]'::jsonb,
  keywords      JSONB NOT NULL DEFAULT '[]'::jsonb,
  vote_average  REAL NOT NULL DEFAULT 0,
  matched       BOOLEAN NOT NULL DEFAULT FALSE,
  details_loaded BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent upgrade from the former poster-only pool.
ALTER TABLE public.film_posters ALTER COLUMN poster_url DROP NOT NULL;
ALTER TABLE public.film_posters ADD COLUMN IF NOT EXISTS poster_resolver_url TEXT;
ALTER TABLE public.film_posters ADD COLUMN IF NOT EXISTS overview TEXT NOT NULL DEFAULT '';
ALTER TABLE public.film_posters ADD COLUMN IF NOT EXISTS director TEXT NOT NULL DEFAULT '';
ALTER TABLE public.film_posters ADD COLUMN IF NOT EXISTS genres JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE public.film_posters ADD COLUMN IF NOT EXISTS keywords JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE public.film_posters ADD COLUMN IF NOT EXISTS vote_average REAL NOT NULL DEFAULT 0;
ALTER TABLE public.film_posters ADD COLUMN IF NOT EXISTS matched BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.film_posters ADD COLUMN IF NOT EXISTS details_loaded BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_film_posters_tmdb_id
  ON public.film_posters (tmdb_id)
  WHERE tmdb_id IS NOT NULL;

-- Successful director portraits are durable shared assets. Empty/failed lookups
-- are intentionally not stored here, so a transient TMDb miss can heal later.
CREATE TABLE IF NOT EXISTS public.director_images (
  normalized_name TEXT PRIMARY KEY,
  display_name    TEXT NOT NULL,
  photo_url       TEXT NOT NULL,
  tmdb_person_id  INTEGER,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.upsert_director_images(p_directors JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_count INTEGER;
BEGIN
  INSERT INTO public.director_images (
    normalized_name, display_name, photo_url, tmdb_person_id, updated_at
  )
  SELECT
    lower(trim(item->>'name')),
    trim(item->>'name'),
    item->>'photo_url',
    NULLIF(item->>'tmdb_person_id', '')::INTEGER,
    now()
  FROM jsonb_array_elements(COALESCE(p_directors, '[]'::jsonb)) AS item
  WHERE COALESCE(trim(item->>'name'), '') <> ''
    AND COALESCE(item->>'photo_url', '') <> ''
  ON CONFLICT (normalized_name) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    photo_url = EXCLUDED.photo_url,
    tmdb_person_id = COALESCE(EXCLUDED.tmdb_person_id, public.director_images.tmdb_person_id),
    updated_at = now();
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION public.upsert_director_images(JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.upsert_director_images(JSONB) TO service_role;

CREATE OR REPLACE FUNCTION public.upsert_film_posters(p_films JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_count INTEGER;
BEGIN
  INSERT INTO public.film_posters (
    film_slug, poster_url, poster_resolver_url, tmdb_id, title, release_year, overview, director,
    genres, keywords, vote_average, matched, details_loaded, updated_at
  )
  SELECT
    item->>'slug',
    NULLIF(item->>'poster_url', ''),
    NULLIF(item->>'poster_resolver_url', ''),
    NULLIF(item->>'tmdb_id', '')::INTEGER,
    COALESCE(item->>'title', ''),
    NULLIF(item->>'release_year', '')::INTEGER,
    COALESCE(item->>'overview', ''),
    COALESCE(item->>'director', ''),
    COALESCE(item->'genres', '[]'::jsonb),
    COALESCE(item->'keywords', '[]'::jsonb),
    COALESCE(NULLIF(item->>'vote_average', '')::REAL, 0),
    COALESCE((item->>'matched')::BOOLEAN, FALSE),
    COALESCE((item->>'details_loaded')::BOOLEAN, FALSE),
    now()
  FROM jsonb_array_elements(COALESCE(p_films, '[]'::jsonb)) AS item
  WHERE COALESCE(item->>'slug', '') <> ''
  ON CONFLICT (film_slug) DO UPDATE SET
    poster_url = COALESCE(EXCLUDED.poster_url, public.film_posters.poster_url),
    poster_resolver_url = COALESCE(EXCLUDED.poster_resolver_url, public.film_posters.poster_resolver_url),
    tmdb_id = COALESCE(EXCLUDED.tmdb_id, public.film_posters.tmdb_id),
    title = CASE WHEN EXCLUDED.title <> '' THEN EXCLUDED.title ELSE public.film_posters.title END,
    release_year = COALESCE(EXCLUDED.release_year, public.film_posters.release_year),
    overview = CASE WHEN EXCLUDED.overview <> '' THEN EXCLUDED.overview ELSE public.film_posters.overview END,
    director = CASE WHEN EXCLUDED.director <> '' THEN EXCLUDED.director ELSE public.film_posters.director END,
    genres = CASE WHEN EXCLUDED.genres <> '[]'::jsonb THEN EXCLUDED.genres ELSE public.film_posters.genres END,
    keywords = CASE WHEN EXCLUDED.keywords <> '[]'::jsonb THEN EXCLUDED.keywords ELSE public.film_posters.keywords END,
    vote_average = CASE WHEN EXCLUDED.vote_average > 0 THEN EXCLUDED.vote_average ELSE public.film_posters.vote_average END,
    matched = public.film_posters.matched OR EXCLUDED.matched,
    details_loaded = public.film_posters.details_loaded OR EXCLUDED.details_loaded,
    updated_at = now();
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION public.upsert_film_posters(JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.upsert_film_posters(JSONB) TO service_role;

-- Random pick pool: films the wider membership has watched and this account has
-- not. Deliberately independent of the caller's watchlist, so "rastgele" can
-- surface films the user never listed. Each call returns a fresh random sample;
-- the caller picks the final few from it.
CREATE OR REPLACE FUNCTION public.community_random_films(
  p_user_id BIGINT,
  p_limit INTEGER DEFAULT 24
) RETURNS TABLE (
  film_slug TEXT,
  title TEXT,
  release_year INTEGER,
  tmdb_id INTEGER,
  director TEXT,
  genres JSONB,
  keywords JSONB,
  poster_url TEXT,
  overview TEXT,
  watcher_count BIGINT,
  avg_rating REAL
)
LANGUAGE SQL
SECURITY DEFINER
SET search_path = public
AS $$
WITH mine AS (
  SELECT w.film_slug
  FROM public.user_watched_films w
  WHERE w.user_id = p_user_id AND w.is_active
),
community AS (
  SELECT
    w.film_slug AS slug,
    COUNT(DISTINCT w.user_id) AS watchers,
    AVG(w.user_rating) FILTER (
      WHERE w.rating_observed AND w.user_rating IS NOT NULL
    ) AS rating,
    MAX(w.title) AS title,
    MAX(w.release_year) AS release_year,
    MAX(w.tmdb_id) AS tmdb_id
  FROM public.user_watched_films w
  WHERE w.is_active
    AND w.user_id <> p_user_id
    AND NOT EXISTS (SELECT 1 FROM mine WHERE mine.film_slug = w.film_slug)
  GROUP BY w.film_slug
)
SELECT
  c.slug,
  COALESCE(NULLIF(fp.title, ''), c.title, ''),
  COALESCE(fp.release_year, c.release_year),
  COALESCE(fp.tmdb_id, c.tmdb_id),
  COALESCE(fp.director, ''),
  COALESCE(fp.genres, '[]'::jsonb),
  COALESCE(fp.keywords, '[]'::jsonb),
  fp.poster_url,
  COALESCE(fp.overview, ''),
  c.watchers,
  c.rating::REAL
FROM community c
LEFT JOIN public.film_posters fp ON fp.film_slug = c.slug
-- Films the membership actively disliked stay out; unrated ones remain eligible.
WHERE COALESCE(c.rating, 5) >= 3.0
ORDER BY random()
LIMIT GREATEST(1, LEAST(COALESCE(p_limit, 24), 200));
$$;

REVOKE ALL ON FUNCTION public.community_random_films(BIGINT, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.community_random_films(BIGINT, INTEGER)
  TO service_role;

-- ── Sinema gündemi (bülten) ────────────────────────────────────────────────
-- Venues are data, not code: a venue's parser lives in `config` (selectors and
-- date format) so a site redesign is a row edit. `kind='release'` is the
-- synthetic nationwide layer fed by TMDb, which has no showtimes.
CREATE TABLE IF NOT EXISTS public.venues (
  id            BIGSERIAL PRIMARY KEY,
  slug          TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  city          TEXT NOT NULL DEFAULT '',
  kind          TEXT NOT NULL DEFAULT 'repertory'
                CHECK (kind IN ('release', 'repertory', 'festival')),
  source_url    TEXT NOT NULL DEFAULT '',
  config        JSONB NOT NULL DEFAULT '{}'::jsonb,
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  last_ok_at    TIMESTAMPTZ,
  last_error    TEXT NOT NULL DEFAULT '',
  lease_token   UUID,
  lease_expires_at TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.venues (slug, name, city, kind, source_url)
VALUES ('tr-vizyon', 'Türkiye vizyonu', '', 'release', 'https://www.themoviedb.org/movie/now-playing')
ON CONFLICT (slug) DO NOTHING;

-- Seeded venues. `config.robots` records the permission check that was made
-- before enabling each one, so the decision is auditable later. Selectors are
-- deliberately the sturdiest thing each site offers: a data attribute where one
-- exists, then a stable container class, then a URL pattern.
INSERT INTO public.venues (slug, name, city, kind, source_url, config) VALUES
  ('paribu-cineverse', 'Paribu Cineverse', '', 'repertory',
   'https://www.paribucineverse.com/vizyondakiler',
   '{"strategy":"attr","item_selector":"div.movie-list-banner-item",
     "title_attr":"data-movie-title","link_selector":"a[href*=\'-filmi-izle\']","limit":60,
     "robots":{"checked":"2026-09-04","allowed":true,
               "note":"Allow: / ; only /biletleme/ is disallowed and we never fetch it."}}'::jsonb),
  ('baska-sinema', 'Başka Sinema', '', 'repertory',
   'https://www.baskasinema.com/filmler/',
   '{"strategy":"css","item_selector":"div.movie_box","title_selector":"h3.movie_title",
     "link_selector":"div.movie_cover a[href]","limit":60,
     "robots":{"checked":"2026-09-04","allowed":true,"note":"Only /wp-admin/ disallowed."}}'::jsonb),
  ('atlas-1948', 'Atlas 1948 Sineması', 'İstanbul', 'repertory',
   'https://www.atlas1948.com/',
   '{"strategy":"link","href_pattern":"/film/[^/]+/?$",
     "skip_titles":["BİLETİNİ AL","Detaylar","İncele","Seanslar"],"limit":60,
     "robots":{"checked":"2026-09-04","allowed":true,"note":"No robots restrictions on /film/."}}'::jsonb),
  ('kadikoy-sinemasi', 'Kadıköy Sineması', 'İstanbul', 'repertory',
   'https://biletinial.com/tr-tr/mekan/kadikoy-sinemasi',
   '{"strategy":"css","item_selector":"div.yeniMekan__sayfalar__vizyondakiler li",
     "title_selector":"h3 a","limit":60,
     "robots":{"checked":"2026-09-04","allowed":true,
               "note":"kadikoysinemasi.com redirects here. /kino/ and /WebLogin disallowed; /mekan/ is not."}}'::jsonb)
ON CONFLICT (slug) DO UPDATE SET
  name = EXCLUDED.name,
  city = EXCLUDED.city,
  source_url = EXCLUDED.source_url,
  config = EXCLUDED.config,
  updated_at = now();

CREATE TABLE IF NOT EXISTS public.screenings (
  id             BIGSERIAL PRIMARY KEY,
  venue_id       BIGINT NOT NULL REFERENCES public.venues(id) ON DELETE CASCADE,
  title_raw      TEXT NOT NULL,
  year           INTEGER,
  tmdb_id        INTEGER,
  film_slug      TEXT,
  poster_url     TEXT,
  starts_at      TIMESTAMPTZ,
  url            TEXT NOT NULL DEFAULT '',
  match_status   TEXT NOT NULL DEFAULT 'unresolved'
                 CHECK (match_status IN ('matched', 'ambiguous', 'unresolved')),
  source_run_id  UUID,
  first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per (venue, title, showtime). A release-layer row has no showtime, so
-- the uniqueness key coalesces it to epoch instead of letting NULLs duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS idx_screenings_identity
  ON public.screenings (venue_id, title_raw, COALESCE(starts_at, 'epoch'::timestamptz));
CREATE INDEX IF NOT EXISTS idx_screenings_window
  ON public.screenings (starts_at);
CREATE INDEX IF NOT EXISTS idx_screenings_tmdb
  ON public.screenings (tmdb_id) WHERE tmdb_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_screenings_unmatched
  ON public.screenings (match_status, updated_at DESC) WHERE match_status <> 'matched';

CREATE TABLE IF NOT EXISTS public.bulletin_digests (
  user_id     BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  week_start  DATE NOT NULL,
  city        TEXT NOT NULL DEFAULT '',
  payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, week_start, city)
);

-- Only one process ingests a venue at a time; a stale lease may be reclaimed.
CREATE OR REPLACE FUNCTION public.claim_venue_ingest(
  p_slug TEXT,
  p_lease_token UUID,
  p_lease_seconds INTEGER,
  p_min_age_seconds INTEGER DEFAULT 43200
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_claimed BIGINT;
BEGIN
  UPDATE public.venues
  SET lease_token = p_lease_token,
      lease_expires_at = now() + make_interval(secs => GREATEST(60, p_lease_seconds)),
      updated_at = now()
  WHERE slug = p_slug
    AND active
    AND (last_ok_at IS NULL
         OR last_ok_at <= now() - make_interval(secs => GREATEST(60, p_min_age_seconds)))
    AND (lease_token = p_lease_token
         OR lease_expires_at IS NULL
         OR lease_expires_at <= now())
  RETURNING id INTO v_claimed;
  RETURN v_claimed IS NOT NULL;
END;
$$;

-- Batch upsert for one ingest run. A pass that could not resolve a title must
-- never blank an id an earlier pass (or a human) already resolved.
CREATE OR REPLACE FUNCTION public.upsert_screenings(
  p_venue_slug TEXT,
  p_rows JSONB,
  p_run_id UUID
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_venue_id BIGINT;
  v_count INTEGER;
BEGIN
  SELECT id INTO v_venue_id FROM public.venues WHERE slug = p_venue_slug;
  IF v_venue_id IS NULL THEN RAISE EXCEPTION 'venue_not_found'; END IF;

  INSERT INTO public.screenings (
    venue_id, title_raw, year, tmdb_id, film_slug, poster_url, starts_at, url,
    match_status, source_run_id, updated_at
  )
  SELECT
    v_venue_id,
    trim(item->>'title_raw'),
    NULLIF(item->>'year', '')::INTEGER,
    NULLIF(item->>'tmdb_id', '')::INTEGER,
    NULLIF(item->>'film_slug', ''),
    NULLIF(item->>'poster_url', ''),
    NULLIF(item->>'starts_at', '')::TIMESTAMPTZ,
    COALESCE(item->>'url', ''),
    COALESCE(NULLIF(item->>'match_status', ''), 'unresolved'),
    p_run_id,
    now()
  FROM jsonb_array_elements(COALESCE(p_rows, '[]'::jsonb)) AS item
  WHERE COALESCE(trim(item->>'title_raw'), '') <> ''
  ON CONFLICT (venue_id, title_raw, COALESCE(starts_at, 'epoch'::timestamptz)) DO UPDATE SET
    year = COALESCE(EXCLUDED.year, public.screenings.year),
    tmdb_id = COALESCE(EXCLUDED.tmdb_id, public.screenings.tmdb_id),
    film_slug = COALESCE(EXCLUDED.film_slug, public.screenings.film_slug),
    poster_url = COALESCE(EXCLUDED.poster_url, public.screenings.poster_url),
    url = CASE WHEN EXCLUDED.url <> '' THEN EXCLUDED.url ELSE public.screenings.url END,
    match_status = CASE
      WHEN EXCLUDED.match_status = 'matched' THEN 'matched'
      WHEN public.screenings.match_status = 'matched' THEN 'matched'
      ELSE EXCLUDED.match_status
    END,
    source_run_id = p_run_id,
    updated_at = now();
  GET DIAGNOSTICS v_count = ROW_COUNT;

  -- Rows the venue no longer lists are dropped, so a bulletin never advertises
  -- a screening that has left the programme.
  DELETE FROM public.screenings
  WHERE venue_id = v_venue_id
    AND source_run_id IS DISTINCT FROM p_run_id;

  UPDATE public.venues
  SET last_ok_at = now(), last_error = '', updated_at = now()
  WHERE id = v_venue_id;
  RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.record_venue_failure(
  p_venue_slug TEXT,
  p_error TEXT
) RETURNS VOID
LANGUAGE SQL
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE public.venues
  SET last_error = left(COALESCE(p_error, ''), 500), updated_at = now()
  WHERE slug = p_venue_slug;
$$;

REVOKE ALL ON FUNCTION public.claim_venue_ingest(TEXT, UUID, INTEGER, INTEGER)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.upsert_screenings(TEXT, JSONB, UUID)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.record_venue_failure(TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_venue_ingest(TEXT, UUID, INTEGER, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.upsert_screenings(TEXT, JSONB, UUID) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_venue_failure(TEXT, TEXT) TO service_role;

-- Checkpointed background crawl for the one-time full history sweep. One row per
-- user; a run advances cursor_page under a per-run time budget and can be resumed
-- (in-process on the next visit) if the instance restarts mid-crawl.
CREATE TABLE IF NOT EXISTS public.profile_sync_jobs (
  user_id         BIGINT PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
  state           TEXT NOT NULL DEFAULT 'queued'
                  CHECK (state IN ('queued', 'running', 'done', 'failed')),
  phase           TEXT NOT NULL DEFAULT 'diary'
                  CHECK (phase IN ('diary', 'enrich', 'aggregate', 'done')),
  scope           TEXT NOT NULL DEFAULT 'full'
                  CHECK (scope IN ('full', 'incremental')),
  cursor_page     INTEGER NOT NULL DEFAULT 1,
  films_total     INTEGER NOT NULL DEFAULT 0,
  films_processed INTEGER NOT NULL DEFAULT 0,
  attempts        SMALLINT NOT NULL DEFAULT 0,
  heartbeat_at    TIMESTAMPTZ,
  backoff_until   TIMESTAMPTZ,
  last_error      TEXT NOT NULL DEFAULT '',
  sync_run_id     UUID,
  lease_token     UUID,
  lease_expires_at TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.profile_sync_jobs ADD COLUMN IF NOT EXISTS sync_run_id UUID;
ALTER TABLE public.profile_sync_jobs ADD COLUMN IF NOT EXISTS lease_token UUID;
ALTER TABLE public.profile_sync_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_profile_sync_jobs_resumable
  ON public.profile_sync_jobs (state, heartbeat_at)
  WHERE state IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS public.auth_challenges (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN ('register', 'password_reset')),
  code_hash     TEXT NOT NULL,
  attempts      SMALLINT NOT NULL DEFAULT 0,
  expires_at    TIMESTAMPTZ NOT NULL,
  consumed_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_active
  ON public.auth_challenges (user_id, kind, created_at DESC)
  WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS public.blend_requests (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  requester_user_id   BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  recipient_user_id   BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  status              TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'accepted', 'rejected', 'cancelled', 'expired')),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at          TIMESTAMPTZ,
  expires_at          TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '14 days'),
  CHECK (requester_user_id <> recipient_user_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_blend_requests_pending_pair
  ON public.blend_requests (requester_user_id, recipient_user_id)
  WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS idx_blend_requests_pending_unordered_pair
  ON public.blend_requests (
    LEAST(requester_user_id, recipient_user_id),
    GREATEST(requester_user_id, recipient_user_id)
  ) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_blend_inbox
  ON public.blend_requests (recipient_user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_blend_sent
  ON public.blend_requests (requester_user_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS public.blend_results (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id          UUID NOT NULL UNIQUE REFERENCES public.blend_requests(id) ON DELETE CASCADE,
  score               INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
  confidence          JSONB NOT NULL DEFAULT '{}'::jsonb,
  result              JSONB NOT NULL DEFAULT '{}'::jsonb,
  algorithm_version   TEXT NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_blocks (
  blocker_user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  blocked_user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (blocker_user_id, blocked_user_id),
  CHECK (blocker_user_id <> blocked_user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_blocks_blocked
  ON public.user_blocks (blocked_user_id, blocker_user_id);

CREATE TABLE IF NOT EXISTS public.user_reports (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reporter_user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  reported_user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  category         TEXT NOT NULL CHECK (category IN ('spam', 'harassment', 'impersonation', 'other')),
  detail           TEXT NOT NULL DEFAULT '' CHECK (char_length(detail) <= 500),
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'reviewed', 'dismissed')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (reporter_user_id <> reported_user_id)
);


CREATE INDEX IF NOT EXISTS idx_user_reports_status_time
  ON public.user_reports (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_reports_reporter_time
  ON public.user_reports (reporter_user_id, created_at DESC);

-- Sinefil Mektupları. The device-key E2EE design was dropped: a key that only
-- ever lived in one browser meant a member could not read their own mail after
-- switching devices, and a second login silently rotated the public key and
-- broke the first device too. Letters are now ordinary rows, readable by the
-- service, and the privacy guarantee is access control plus the block rule
-- below — not cryptography. The old envelope columns stay for the rows that
-- were written under the previous design.
DROP TABLE IF EXISTS public.user_letter_keys;

CREATE TABLE IF NOT EXISTS public.cinephile_letters (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sender_user_id         BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  recipient_user_id      BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  sender_public_key      TEXT NOT NULL CHECK (char_length(sender_public_key) BETWEEN 40 AND 512),
  recipient_public_key   TEXT NOT NULL CHECK (char_length(recipient_public_key) BETWEEN 40 AND 512),
  sender_key_version     SMALLINT NOT NULL DEFAULT 1,
  recipient_key_version  SMALLINT NOT NULL DEFAULT 1,
  ciphertext             TEXT NOT NULL CHECK (char_length(ciphertext) BETWEEN 16 AND 12000),
  iv                     TEXT NOT NULL CHECK (char_length(iv) BETWEEN 12 AND 128),
  salt                   TEXT NOT NULL CHECK (char_length(salt) BETWEEN 16 AND 256),
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_at                TIMESTAMPTZ,
  CHECK (sender_user_id <> recipient_user_id)
);

-- Plaintext body and optional film gift. The encrypted columns become optional
-- so historical envelopes survive while new letters are written as text.
ALTER TABLE public.cinephile_letters ADD COLUMN IF NOT EXISTS body TEXT NOT NULL DEFAULT '';
ALTER TABLE public.cinephile_letters ADD COLUMN IF NOT EXISTS film JSONB;
ALTER TABLE public.cinephile_letters ALTER COLUMN ciphertext DROP NOT NULL;
ALTER TABLE public.cinephile_letters ALTER COLUMN iv DROP NOT NULL;
ALTER TABLE public.cinephile_letters ALTER COLUMN salt DROP NOT NULL;
ALTER TABLE public.cinephile_letters ALTER COLUMN sender_public_key DROP NOT NULL;
ALTER TABLE public.cinephile_letters ALTER COLUMN recipient_public_key DROP NOT NULL;

DO $$ BEGIN
  ALTER TABLE public.cinephile_letters ADD CONSTRAINT cinephile_letters_body_length
    CHECK (char_length(body) <= 600);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_cinephile_letters_recipient_unread
  ON public.cinephile_letters (recipient_user_id, created_at DESC) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_cinephile_letters_sender_time
  ON public.cinephile_letters (sender_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.auth_audit_log (
  id            BIGSERIAL PRIMARY KEY,
  user_id       BIGINT REFERENCES public.users(id) ON DELETE SET NULL,
  event         TEXT NOT NULL,
  ip_hash       TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auth_audit_user_time
  ON public.auth_audit_log (user_id, created_at DESC);

-- Product activity telemetry. This is deliberately separate from the security
-- audit log: it stores bounded event metadata (counts/status flags only), and
-- cascades on account deletion so user data can be fully removed on request.
CREATE TABLE IF NOT EXISTS public.user_activity_events (
  id          BIGSERIAL PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  event_type  TEXT NOT NULL,
  metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_events_user_time
  ON public.user_activity_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activity_events_type_time
  ON public.user_activity_events (event_type, created_at DESC);

ALTER TABLE public.user_activity_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_all_user_activity_events" ON public.user_activity_events;
REVOKE ALL ON TABLE public.user_activity_events FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.user_activity_events_id_seq FROM anon, authenticated;
GRANT ALL ON TABLE public.user_activity_events TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.user_activity_events_id_seq TO service_role;

-- One call for the local admin report. It returns aggregates only; raw film
-- rows, passwords, tokens and event metadata are never exposed by the report.
DROP FUNCTION IF EXISTS public.admin_user_activity_report(BOOLEAN);
CREATE OR REPLACE FUNCTION public.admin_user_activity_report(
  p_include_non_active BOOLEAN DEFAULT FALSE
)
RETURNS TABLE (
  user_id BIGINT,
  username TEXT,
  account_status TEXT,
  profile_sync_status TEXT,
  created_at TIMESTAMPTZ,
  profile_synced_at TIMESTAMPTZ,
  onboarding_completed_at TIMESTAMPTZ,
  scan_total BIGINT,
  scan_processed BIGINT,
  watched_count BIGINT,
  watchlist_count BIGINT,
  blend_requests_sent BIGINT,
  blend_requests_received BIGINT,
  completed_blends BIGINT,
  discoverable BOOLEAN,
  letter_receiving_enabled BOOLEAN,
  letters_sent BIGINT,
  letters_received BIGINT,
  letters_unread BIGINT,
  last_letter_at TIMESTAMPTZ,
  profile_sync_requests BIGINT,
  watchlist_checks BIGINT,
  recommendation_attempts BIGINT,
  recommendation_successes BIGINT,
  random_attempts BIGINT,
  login_count BIGINT,
  last_activity_at TIMESTAMPTZ
)
LANGUAGE SQL
SECURITY DEFINER
SET search_path = public
AS $$
WITH watched AS (
  SELECT f.user_id, COUNT(*) FILTER (WHERE f.is_active) AS watched_count
  FROM public.user_watched_films f
  GROUP BY f.user_id
),
blend_activity AS (
  SELECT
    b.user_id,
    COUNT(*) FILTER (WHERE b.direction = 'sent') AS blend_requests_sent,
    COUNT(*) FILTER (WHERE b.direction = 'received') AS blend_requests_received
  FROM (
    SELECT br.requester_user_id AS user_id, 'sent' AS direction
    FROM public.blend_requests br
    UNION ALL
    SELECT br.recipient_user_id AS user_id, 'received' AS direction
    FROM public.blend_requests br
  ) b
  GROUP BY b.user_id
),
blend_completed AS (
  SELECT b.user_id, COUNT(*) AS completed_blends
  FROM (
    SELECT br.requester_user_id AS user_id
    FROM public.blend_requests br
    JOIN public.blend_results result ON result.request_id = br.id
    UNION ALL
    SELECT br.recipient_user_id AS user_id
    FROM public.blend_requests br
    JOIN public.blend_results result ON result.request_id = br.id
  ) b
  GROUP BY b.user_id
),
letters AS (
  -- Volume only: the report counts letters and never selects a body, a film
  -- gift or a recipient.
  SELECT
    l.user_id,
    COUNT(*) FILTER (WHERE l.direction = 'sent') AS letters_sent,
    COUNT(*) FILTER (WHERE l.direction = 'received') AS letters_received,
    COUNT(*) FILTER (WHERE l.direction = 'received' AND l.unread) AS letters_unread,
    MAX(l.created_at) FILTER (WHERE l.direction = 'sent') AS last_letter_at
  FROM (
    SELECT cl.sender_user_id AS user_id, 'sent' AS direction, cl.created_at,
           FALSE AS unread
    FROM public.cinephile_letters cl
    UNION ALL
    SELECT cl.recipient_user_id AS user_id, 'received' AS direction, cl.created_at,
           cl.read_at IS NULL AS unread
    FROM public.cinephile_letters cl
  ) l
  GROUP BY l.user_id
),
events AS (
  SELECT
    e.user_id,
    COUNT(*) FILTER (
      WHERE e.event_type IN ('recommendation_completed', 'recommendation_failed')
    ) AS recommendation_attempts,
    COUNT(*) FILTER (
      WHERE e.event_type = 'recommendation_completed'
        AND e.metadata->>'success' = 'true'
    ) AS recommendation_successes,
    COUNT(*) FILTER (
      WHERE e.event_type IN ('random_completed', 'random_failed')
    ) AS random_attempts,
    COUNT(*) FILTER (WHERE e.event_type = 'profile_sync_requested')
      AS profile_sync_requests,
    COUNT(*) FILTER (WHERE e.event_type = 'watchlist_checked')
      AS watchlist_checks,
    COUNT(*) FILTER (WHERE e.event_type = 'login_succeeded') AS login_count,
    MAX(e.created_at) AS last_activity_at
  FROM public.user_activity_events e
  GROUP BY e.user_id
),
watchlist_cache AS (
  SELECT
    c.key AS username,
    CASE
      WHEN jsonb_typeof(c.value) = 'array' THEN jsonb_array_length(c.value)
      ELSE 0
    END::BIGINT AS watchlist_count
  FROM public.tmdb_cache c
  WHERE c.namespace = 'films_watchlist'
)
SELECT
  u.id,
  u.username,
  u.account_status,
  u.profile_sync_status,
  u.created_at,
  u.profile_synced_at,
  u.onboarding_completed_at,
  COALESCE(NULLIF(job.films_total, 0), watched.watched_count, 0)::BIGINT,
  COALESCE(job.films_processed, 0)::BIGINT,
  COALESCE(watched.watched_count, 0)::BIGINT,
  COALESCE(watchlist_cache.watchlist_count, 0)::BIGINT,
  COALESCE(blend_activity.blend_requests_sent, 0)::BIGINT,
  COALESCE(blend_activity.blend_requests_received, 0)::BIGINT,
  COALESCE(blend_completed.completed_blends, 0)::BIGINT,
  COALESCE(u.discoverable, FALSE),
  COALESCE(u.letter_receiving_enabled, FALSE),
  COALESCE(letters.letters_sent, 0)::BIGINT,
  COALESCE(letters.letters_received, 0)::BIGINT,
  COALESCE(letters.letters_unread, 0)::BIGINT,
  letters.last_letter_at,
  COALESCE(events.profile_sync_requests, 0)::BIGINT,
  COALESCE(events.watchlist_checks, 0)::BIGINT,
  COALESCE(events.recommendation_attempts, 0)::BIGINT,
  COALESCE(events.recommendation_successes, 0)::BIGINT,
  COALESCE(events.random_attempts, 0)::BIGINT,
  COALESCE(events.login_count, 0)::BIGINT,
  events.last_activity_at
FROM public.users u
LEFT JOIN public.profile_sync_jobs job ON job.user_id = u.id
LEFT JOIN watched ON watched.user_id = u.id
LEFT JOIN blend_activity ON blend_activity.user_id = u.id
LEFT JOIN blend_completed ON blend_completed.user_id = u.id
LEFT JOIN letters ON letters.user_id = u.id
LEFT JOIN events ON events.user_id = u.id
LEFT JOIN watchlist_cache ON watchlist_cache.username = u.username
WHERE p_include_non_active OR u.account_status = 'active'
-- Most recently active account first. An account with no recorded event yet
-- falls back to its last sync, then to its registration time, so a fresh
-- registration never sinks below a long-dormant one.
ORDER BY COALESCE(events.last_activity_at, u.profile_synced_at, u.created_at) DESC NULLS LAST,
         u.created_at DESC;
$$;

REVOKE ALL ON FUNCTION public.admin_user_activity_report(BOOLEAN)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_user_activity_report(BOOLEAN)
  TO service_role;

-- A profile refresh must become visible as one coherent snapshot. The backend
-- invokes this function only with the service_role key; browser roles cannot
-- execute it directly.
CREATE OR REPLACE FUNCTION public.save_profile_snapshot(
  p_user_id BIGINT,
  p_profile JSONB,
  p_taste JSONB,
  p_favorites JSONB
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.users
    WHERE id = p_user_id
      AND account_status IN ('active', 'verification_deferred')
  ) THEN
    RAISE EXCEPTION 'active account not found';
  END IF;

  INSERT INTO public.taste_profiles (
    user_id, summary, favorite_director, top_directors, top_directors_detail,
    top_genres, top_keywords, analysis, personality,
    sample_size, rated_count, metadata_coverage, confidence_level,
    confidence_score, algorithm_version, source_fingerprint, generated_at, updated_at
  ) VALUES (
    p_user_id,
    COALESCE(p_taste->>'summary', ''),
    COALESCE(p_taste->>'favorite_director', ''),
    COALESCE(p_taste->'top_directors', '[]'::jsonb),
    COALESCE(p_taste->'top_directors_detail', '[]'::jsonb),
    COALESCE(p_taste->'top_genres', '[]'::jsonb),
    COALESCE(p_taste->'top_keywords', '[]'::jsonb),
    COALESCE(p_taste->'analysis', '[]'::jsonb),
    COALESCE(p_taste->>'personality', ''),
    COALESCE((p_taste->>'sample_size')::INTEGER, 0),
    COALESCE((p_taste->>'rated_count')::INTEGER, 0),
    COALESCE((p_taste->>'metadata_coverage')::INTEGER, 0),
    COALESCE(p_taste->>'confidence_level', 'low'),
    COALESCE((p_taste->>'confidence_score')::INTEGER, 0),
    COALESCE(p_taste->>'algorithm_version', 'taste-v3'),
    COALESCE(p_taste->>'source_fingerprint', ''),
    now(),
    now()
  )
  ON CONFLICT (user_id) DO UPDATE SET
    summary = EXCLUDED.summary,
    favorite_director = EXCLUDED.favorite_director,
    top_directors = EXCLUDED.top_directors,
    top_directors_detail = EXCLUDED.top_directors_detail,
    top_genres = EXCLUDED.top_genres,
    top_keywords = EXCLUDED.top_keywords,
    analysis = EXCLUDED.analysis,
    personality = EXCLUDED.personality,
    sample_size = EXCLUDED.sample_size,
    rated_count = EXCLUDED.rated_count,
    metadata_coverage = EXCLUDED.metadata_coverage,
    confidence_level = EXCLUDED.confidence_level,
    confidence_score = EXCLUDED.confidence_score,
    algorithm_version = EXCLUDED.algorithm_version,
    source_fingerprint = EXCLUDED.source_fingerprint,
    generated_at = EXCLUDED.generated_at,
    updated_at = EXCLUDED.updated_at;

  DELETE FROM public.profile_favorites WHERE user_id = p_user_id;
  INSERT INTO public.profile_favorites (
    user_id, position, slug, title, release_year, tmdb_id, poster_url
  )
  SELECT
    p_user_id,
    (item->>'position')::SMALLINT,
    item->>'slug',
    item->>'title',
    NULLIF(item->>'release_year', '')::INTEGER,
    NULLIF(item->>'tmdb_id', '')::INTEGER,
    NULLIF(item->>'poster_url', '')
  FROM jsonb_array_elements(COALESCE(p_favorites, '[]'::jsonb)) AS item
  WHERE (item->>'position')::INTEGER BETWEEN 1 AND 4
    AND COALESCE(item->>'slug', '') <> ''
    AND COALESCE(item->>'title', '') <> '';

  UPDATE public.users SET
    display_name = COALESCE(NULLIF(p_profile->>'display_name', ''), display_name),
    avatar_url = COALESCE(NULLIF(p_profile->>'avatar_url', ''), avatar_url),
    letterboxd_stats = CASE
      WHEN p_profile ? 'stats' THEN COALESCE(p_profile->'stats', '{}'::jsonb)
      ELSE letterboxd_stats
    END,
    profile_sync_status = 'ready',
    profile_synced_at = now(),
    updated_at = now(),
    last_seen_at = now()
  WHERE id = p_user_id;
END;
$$;

REVOKE ALL ON FUNCTION public.save_profile_snapshot(BIGINT, JSONB, JSONB, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.save_profile_snapshot(BIGINT, JSONB, JSONB, JSONB)
  TO service_role;

-- Atomic batch upsert for the crawl/enrich pipeline. Metadata (director, genres,
-- keywords, tmdb_id) is only overwritten when the incoming batch actually carries
-- it, so a search-only pass never clobbers details a later enrich pass filled in.
CREATE OR REPLACE FUNCTION public.upsert_watched_films(
  p_user_id BIGINT,
  p_films JSONB
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_count INTEGER;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.users
    WHERE id = p_user_id
      AND account_status IN ('active', 'verification_deferred')
  ) THEN
    RAISE EXCEPTION 'active account not found';
  END IF;

  INSERT INTO public.user_watched_films (
    user_id, film_slug, title, release_year, tmdb_id, director, genres, keywords,
    user_rating, rating_observed, poster_url, poster_resolver_url, watched_rank, details_loaded,
    last_seen_run_id, is_active, updated_at
  )
  SELECT
    p_user_id,
    item->>'slug',
    COALESCE(item->>'title', ''),
    NULLIF(item->>'release_year', '')::INTEGER,
    NULLIF(item->>'tmdb_id', '')::INTEGER,
    COALESCE(item->>'director', ''),
    COALESCE(item->'genres', '[]'::jsonb),
    COALESCE(item->'keywords', '[]'::jsonb),
    NULLIF(item->>'user_rating', '')::REAL,
    COALESCE((item->>'rating_observed')::BOOLEAN, FALSE),
    NULLIF(item->>'poster_url', ''),
    NULLIF(item->>'poster_resolver_url', ''),
    NULLIF(item->>'watched_rank', '')::INTEGER,
    COALESCE((item->>'details_loaded')::BOOLEAN, FALSE),
    NULLIF(item->>'last_seen_run_id', '')::UUID,
    COALESCE((item->>'is_active')::BOOLEAN, TRUE),
    now()
  FROM jsonb_array_elements(COALESCE(p_films, '[]'::jsonb)) AS item
  WHERE COALESCE(item->>'slug', '') <> ''
  ON CONFLICT (user_id, film_slug) DO UPDATE SET
    title = CASE WHEN EXCLUDED.title <> '' THEN EXCLUDED.title ELSE public.user_watched_films.title END,
    release_year = COALESCE(EXCLUDED.release_year, public.user_watched_films.release_year),
    tmdb_id = COALESCE(EXCLUDED.tmdb_id, public.user_watched_films.tmdb_id),
    director = CASE WHEN EXCLUDED.director <> '' THEN EXCLUDED.director ELSE public.user_watched_films.director END,
    genres = CASE WHEN EXCLUDED.genres <> '[]'::jsonb THEN EXCLUDED.genres ELSE public.user_watched_films.genres END,
    keywords = CASE WHEN EXCLUDED.keywords <> '[]'::jsonb THEN EXCLUDED.keywords ELSE public.user_watched_films.keywords END,
    user_rating = CASE
      WHEN EXCLUDED.rating_observed THEN EXCLUDED.user_rating
      ELSE public.user_watched_films.user_rating
    END,
    rating_observed = public.user_watched_films.rating_observed OR EXCLUDED.rating_observed,
    poster_url = COALESCE(EXCLUDED.poster_url, public.user_watched_films.poster_url),
    poster_resolver_url = COALESCE(EXCLUDED.poster_resolver_url, public.user_watched_films.poster_resolver_url),
    watched_rank = COALESCE(EXCLUDED.watched_rank, public.user_watched_films.watched_rank),
    details_loaded = public.user_watched_films.details_loaded OR EXCLUDED.details_loaded,
    last_seen_run_id = COALESCE(EXCLUDED.last_seen_run_id, public.user_watched_films.last_seen_run_id),
    is_active = CASE
      WHEN EXCLUDED.last_seen_run_id IS NOT NULL THEN TRUE
      ELSE public.user_watched_films.is_active
    END,
    updated_at = now();

  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION public.upsert_watched_films(BIGINT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.upsert_watched_films(BIGINT, JSONB)
  TO service_role;

-- Cross-process ownership for Render/background workers. Only one valid lease
-- can own a user's sync job at a time; stale leases may be reclaimed.
CREATE OR REPLACE FUNCTION public.claim_profile_sync_job(
  p_user_id BIGINT,
  p_lease_token UUID,
  p_lease_seconds INTEGER
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_claimed BIGINT;
BEGIN
  UPDATE public.profile_sync_jobs
  SET lease_token = p_lease_token,
      lease_expires_at = now() + make_interval(secs => GREATEST(60, p_lease_seconds)),
      heartbeat_at = now(),
      updated_at = now()
  WHERE user_id = p_user_id
    AND state IN ('queued', 'running', 'failed')
    AND (
      lease_token = p_lease_token
      OR lease_expires_at IS NULL
      OR lease_expires_at <= now()
    )
  RETURNING user_id INTO v_claimed;
  RETURN v_claimed IS NOT NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.finalize_profile_sync_run(
  p_user_id BIGINT,
  p_sync_run_id UUID
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_count INTEGER;
BEGIN
  UPDATE public.user_watched_films
  SET is_active = COALESCE(last_seen_run_id = p_sync_run_id, FALSE),
      updated_at = CASE
        WHEN is_active IS DISTINCT FROM COALESCE(last_seen_run_id = p_sync_run_id, FALSE) THEN now()
        ELSE updated_at
      END
  WHERE user_id = p_user_id;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_profile_sync_job(BIGINT, UUID, INTEGER)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finalize_profile_sync_run(BIGINT, UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_profile_sync_job(BIGINT, UUID, INTEGER)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.finalize_profile_sync_run(BIGINT, UUID)
  TO service_role;

CREATE OR REPLACE FUNCTION public.create_blend_request(
  p_requester_user_id BIGINT,
  p_recipient_username TEXT
) RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_recipient_user_id BIGINT;
  v_request_id UUID;
BEGIN
  SELECT id INTO v_recipient_user_id
  FROM public.users
  WHERE username = lower(trim(leading '@' FROM p_recipient_username))
    AND account_status = 'active';

  IF v_recipient_user_id IS NULL THEN
    RAISE EXCEPTION 'recipient_not_found';
  END IF;
  IF v_recipient_user_id = p_requester_user_id THEN
    RAISE EXCEPTION 'self_request';
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.user_blocks
    WHERE (blocker_user_id = p_requester_user_id AND blocked_user_id = v_recipient_user_id)
       OR (blocker_user_id = v_recipient_user_id AND blocked_user_id = p_requester_user_id)
  ) THEN
    RAISE EXCEPTION 'blend_user_blocked';
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended(
      LEAST(p_requester_user_id, v_recipient_user_id)::TEXT || ':' ||
      GREATEST(p_requester_user_id, v_recipient_user_id)::TEXT,
      0
    )
  );

  UPDATE public.blend_requests
  SET status = 'expired', decided_at = now()
  WHERE status = 'pending' AND expires_at <= now()
    AND (
      (requester_user_id = p_requester_user_id AND recipient_user_id = v_recipient_user_id)
      OR
      (requester_user_id = v_recipient_user_id AND recipient_user_id = p_requester_user_id)
    );

  IF EXISTS (
    SELECT 1 FROM public.blend_requests
    WHERE status = 'accepted'
      AND (
        (requester_user_id = p_requester_user_id AND recipient_user_id = v_recipient_user_id)
        OR
        (requester_user_id = v_recipient_user_id AND recipient_user_id = p_requester_user_id)
      )
  ) THEN
    RAISE EXCEPTION 'blend_already_accepted';
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.blend_requests
    WHERE status = 'pending'
      AND (
        (requester_user_id = p_requester_user_id AND recipient_user_id = v_recipient_user_id)
        OR
        (requester_user_id = v_recipient_user_id AND recipient_user_id = p_requester_user_id)
      )
  ) THEN
    RAISE EXCEPTION 'blend_request_exists';
  END IF;

  IF (
    SELECT count(*) FROM public.blend_requests
    WHERE requester_user_id = p_requester_user_id
      AND status = 'pending' AND expires_at > now()
  ) >= 10 THEN
    RAISE EXCEPTION 'pending_quota_reached';
  END IF;

  INSERT INTO public.blend_requests (requester_user_id, recipient_user_id)
  VALUES (p_requester_user_id, v_recipient_user_id)
  RETURNING id INTO v_request_id;
  RETURN v_request_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.decide_blend_request(
  p_request_id UUID,
  p_recipient_user_id BIGINT,
  p_decision TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_request public.blend_requests%ROWTYPE;
BEGIN
  IF p_decision NOT IN ('accepted', 'rejected') THEN
    RAISE EXCEPTION 'invalid_decision';
  END IF;

  SELECT * INTO v_request FROM public.blend_requests
  WHERE id = p_request_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'request_not_found';
  END IF;
  IF v_request.recipient_user_id <> p_recipient_user_id THEN
    RAISE EXCEPTION 'forbidden';
  END IF;
  IF v_request.status = 'pending' AND v_request.expires_at <= now() THEN
    UPDATE public.blend_requests
    SET status = 'expired', decided_at = now()
    WHERE id = p_request_id;
    RETURN jsonb_build_object('id', p_request_id, 'status', 'expired');
  END IF;
  IF v_request.status = p_decision THEN
    RETURN to_jsonb(v_request);
  END IF;
  IF v_request.status <> 'pending' THEN
    RAISE EXCEPTION 'request_already_decided';
  END IF;

  UPDATE public.blend_requests
  SET status = p_decision, decided_at = now()
  WHERE id = p_request_id
  RETURNING * INTO v_request;
  RETURN to_jsonb(v_request);
END;
$$;

CREATE OR REPLACE FUNCTION public.cancel_blend_request(
  p_request_id UUID,
  p_requester_user_id BIGINT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  UPDATE public.blend_requests
  SET status = 'cancelled', decided_at = now()
  WHERE id = p_request_id
    AND requester_user_id = p_requester_user_id
    AND status = 'pending';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'request_not_cancellable';
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.save_blend_result(
  p_request_id UUID,
  p_actor_user_id BIGINT,
  p_score INTEGER,
  p_confidence JSONB,
  p_result JSONB,
  p_algorithm_version TEXT
) RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_result_id UUID;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.blend_requests
    WHERE id = p_request_id AND status = 'accepted'
      AND p_actor_user_id IN (requester_user_id, recipient_user_id)
  ) THEN
    RAISE EXCEPTION 'accepted_request_not_found';
  END IF;

  INSERT INTO public.blend_results (
    request_id, score, confidence, result, algorithm_version
  ) VALUES (
    p_request_id, p_score, p_confidence, p_result, p_algorithm_version
  )
  ON CONFLICT (request_id) DO UPDATE SET
    score = EXCLUDED.score,
    confidence = EXCLUDED.confidence,
    result = EXCLUDED.result,
    algorithm_version = EXCLUDED.algorithm_version
  RETURNING id INTO v_result_id;
  RETURN v_result_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.block_user(
  p_blocker_user_id BIGINT,
  p_blocked_username TEXT
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_blocked_user_id BIGINT;
BEGIN
  SELECT id INTO v_blocked_user_id FROM public.users
  WHERE username = lower(trim(leading '@' FROM p_blocked_username))
    AND account_status = 'active';
  IF v_blocked_user_id IS NULL THEN RAISE EXCEPTION 'user_not_found'; END IF;
  IF v_blocked_user_id = p_blocker_user_id THEN RAISE EXCEPTION 'self_block'; END IF;

  INSERT INTO public.user_blocks (blocker_user_id, blocked_user_id)
  VALUES (p_blocker_user_id, v_blocked_user_id)
  ON CONFLICT DO NOTHING;

  UPDATE public.blend_requests
  SET status = 'cancelled', decided_at = now()
  WHERE status = 'pending'
    AND (
      (requester_user_id = p_blocker_user_id AND recipient_user_id = v_blocked_user_id)
      OR
      (requester_user_id = v_blocked_user_id AND recipient_user_id = p_blocker_user_id)
    );
  -- Blocking immediately removes the encrypted envelopes from both inboxes.
  -- Even though they are unreadable to the service, keeping them visible would
  -- undermine the safety promise of the block action.
  DELETE FROM public.cinephile_letters
  WHERE (sender_user_id = p_blocker_user_id AND recipient_user_id = v_blocked_user_id)
     OR (sender_user_id = v_blocked_user_id AND recipient_user_id = p_blocker_user_id);
  -- A block severs the social edge as well: no pending request, accepted
  -- follow, or old alert can become a side door back to the profile.
  DELETE FROM public.follows
  WHERE (follower_id = p_blocker_user_id AND followee_id = v_blocked_user_id)
     OR (follower_id = v_blocked_user_id AND followee_id = p_blocker_user_id);
  DELETE FROM public.notifications
  WHERE (user_id = p_blocker_user_id AND actor_id = v_blocked_user_id)
     OR (user_id = v_blocked_user_id AND actor_id = p_blocker_user_id);
  RETURN v_blocked_user_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.unblock_user(
  p_blocker_user_id BIGINT,
  p_blocked_username TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  DELETE FROM public.user_blocks b
  USING public.users u
  WHERE b.blocker_user_id = p_blocker_user_id
    AND b.blocked_user_id = u.id
    AND u.username = lower(trim(leading '@' FROM p_blocked_username));
END;
$$;

DROP FUNCTION IF EXISTS public.send_cinephile_letter(BIGINT, TEXT, JSONB);
CREATE OR REPLACE FUNCTION public.send_cinephile_letter(
  p_sender_user_id BIGINT,
  p_recipient_username TEXT,
  p_body TEXT,
  p_film JSONB DEFAULT NULL
) RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_recipient_user_id BIGINT;
  v_body TEXT;
  v_id UUID;
BEGIN
  -- Writing requires an open letterbox of your own: a letter sent from a closed
  -- account is a channel the recipient cannot answer.
  IF NOT EXISTS (
    SELECT 1 FROM public.users u
    WHERE u.id = p_sender_user_id
      AND u.account_status = 'active'
      AND u.letter_receiving_enabled = TRUE
  ) THEN RAISE EXCEPTION 'letter_sender_closed'; END IF;

  SELECT u.id INTO v_recipient_user_id
  FROM public.users u
  WHERE u.username = lower(trim(leading '@' FROM p_recipient_username))
    AND u.account_status = 'active'
    AND u.letter_receiving_enabled = TRUE;
  IF v_recipient_user_id IS NULL THEN RAISE EXCEPTION 'letter_recipient_unavailable'; END IF;
  IF v_recipient_user_id = p_sender_user_id THEN RAISE EXCEPTION 'letter_recipient_unavailable'; END IF;

  -- The quota belongs to this exact pair, so a user can write to another
  -- sinefil without waiting. PostgreSQL's two-key lock only accepts INTEGER
  -- values; lock this sender through the supported BIGINT overload instead.
  PERFORM pg_advisory_xact_lock(p_sender_user_id);

  IF EXISTS (
    SELECT 1 FROM public.user_blocks b
    WHERE (b.blocker_user_id = p_sender_user_id AND b.blocked_user_id = v_recipient_user_id)
       OR (b.blocker_user_id = v_recipient_user_id AND b.blocked_user_id = p_sender_user_id)
  ) THEN RAISE EXCEPTION 'letter_blocked'; END IF;

  v_body := trim(COALESCE(p_body, ''));
  IF char_length(v_body) < 1 OR char_length(v_body) > 600 THEN
    RAISE EXCEPTION 'invalid_letter_body';
  END IF;

  IF EXISTS (
    SELECT 1 FROM public.cinephile_letters
    WHERE sender_user_id = p_sender_user_id
      AND recipient_user_id = v_recipient_user_id
      AND created_at >= now() - interval '24 hours'
  ) THEN RAISE EXCEPTION 'letter_send_cooldown'; END IF;

  INSERT INTO public.cinephile_letters (
    sender_user_id, recipient_user_id, body, film
  ) VALUES (
    p_sender_user_id, v_recipient_user_id, v_body,
    CASE WHEN jsonb_typeof(p_film) = 'object' THEN p_film ELSE NULL END
  ) RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.report_user(
  p_reporter_user_id BIGINT,
  p_reported_username TEXT,
  p_category TEXT,
  p_detail TEXT
) RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_reported_user_id BIGINT;
  v_report_id UUID;
BEGIN
  IF p_category NOT IN ('spam', 'harassment', 'impersonation', 'other') THEN
    RAISE EXCEPTION 'invalid_report_category';
  END IF;
  SELECT id INTO v_reported_user_id FROM public.users
  WHERE username = lower(trim(leading '@' FROM p_reported_username))
    AND account_status = 'active';
  IF v_reported_user_id IS NULL THEN RAISE EXCEPTION 'user_not_found'; END IF;
  IF v_reported_user_id = p_reporter_user_id THEN RAISE EXCEPTION 'self_report'; END IF;
  IF (
    SELECT count(*) FROM public.user_reports
    WHERE reporter_user_id = p_reporter_user_id
      AND created_at >= now() - interval '24 hours'
  ) >= 5 THEN
    RAISE EXCEPTION 'report_quota_reached';
  END IF;

  INSERT INTO public.user_reports (
    reporter_user_id, reported_user_id, category, detail
  ) VALUES (
    p_reporter_user_id, v_reported_user_id, p_category, left(COALESCE(p_detail, ''), 500)
  ) RETURNING id INTO v_report_id;
  RETURN v_report_id;
END;
$$;

REVOKE ALL ON FUNCTION public.create_blend_request(BIGINT, TEXT)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.decide_blend_request(UUID, BIGINT, TEXT)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.cancel_blend_request(UUID, BIGINT)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.save_blend_result(UUID, BIGINT, INTEGER, JSONB, JSONB, TEXT)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.block_user(BIGINT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.unblock_user(BIGINT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.report_user(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.send_cinephile_letter(BIGINT, TEXT, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_blend_request(BIGINT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.decide_blend_request(UUID, BIGINT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.cancel_blend_request(UUID, BIGINT) TO service_role;
GRANT EXECUTE ON FUNCTION public.save_blend_result(UUID, BIGINT, INTEGER, JSONB, JSONB, TEXT)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.block_user(BIGINT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.unblock_user(BIGINT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.report_user(BIGINT, TEXT, TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.send_cinephile_letter(BIGINT, TEXT, TEXT, JSONB) TO service_role;

-- ── Sinefil Akışı ──────────────────────────────────────────────────────────
-- Her üst not bir filme bağlıdır: film seçmeden gönderi atılamaz. Bu, akışın
-- konudan sapmasını tasarımla engeller ve "en çok konuşulan filmler" trendini
-- ek bir veri yapısı olmadan mümkün kılar. Cevaplar filmi üstteki nottan
-- devraldığı için kendi film alanlarını taşımaz.
CREATE TABLE IF NOT EXISTS public.posts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  author_id     BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL DEFAULT 'note' CHECK (kind IN ('note', 'log')),
  body          TEXT NOT NULL DEFAULT '' CHECK (char_length(body) <= 420),
  film_slug     TEXT,
  tmdb_id       INTEGER,
  film_title    TEXT NOT NULL DEFAULT '',
  film_year     INTEGER,
  payload       JSONB,
  spoiler       BOOLEAN NOT NULL DEFAULT FALSE,
  reply_to      UUID REFERENCES public.posts(id) ON DELETE CASCADE,
  like_count    INTEGER NOT NULL DEFAULT 0,
  reply_count   INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at    TIMESTAMPTZ,
  CHECK ((reply_to IS NULL AND film_slug IS NOT NULL) OR reply_to IS NOT NULL),
  CHECK (kind = 'log' OR char_length(trim(body)) > 0)
);

-- Existing installations started with a 280-character check. Rebuild the
-- named check so applying this idempotent schema upgrades them to 420 too.
-- (Sınır aşağıda, `source` sütunu eklendikten sonra kaynağa göre ayrılıyor.)
ALTER TABLE public.posts DROP CONSTRAINT IF EXISTS posts_body_check;
ALTER TABLE public.posts ADD CONSTRAINT posts_body_check CHECK (char_length(body) <= 420);

-- Bir notu doğrudan bildirmek, sosyal akışın moderasyon bağlamını korur.
-- Not silinse bile rapor satırı kalsın; post_id yalnızca NULL olur.
ALTER TABLE public.user_reports
  ADD COLUMN IF NOT EXISTS post_id UUID REFERENCES public.posts(id) ON DELETE SET NULL;

-- Günce kayıtları. Letterboxd'dan gelen bir izleme kaydı akışta nottan farklı
-- görünür ve `source_key` ile bir kez düşer: RSS guid'i benzersiz olduğu için
-- aynı kayıt ikinci kez eklenemez. Kullanıcı silerse satır durur (yumuşak
-- silme) ve anahtar dolu kaldığı için bir daha asla geri gelmez — istenen
-- davranış tam olarak bu.
ALTER TABLE public.posts ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'app';
ALTER TABLE public.posts ADD COLUMN IF NOT EXISTS source_key TEXT;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'posts_source_check'
  ) THEN
    ALTER TABLE public.posts
      ADD CONSTRAINT posts_source_check CHECK (source IN ('app', 'letterboxd'));
  END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_source_key
  ON public.posts (author_id, source_key) WHERE source_key IS NOT NULL;

-- Gövde sınırı kaynağa göre: uygulamada yazılan not kısa kalıyor (420, ürün
-- kararı ve API katmanı da bunu uyguluyor), Letterboxd'dan içe aktarılan yorum
-- olduğu gibi giriyor. Tek bir 420 sınırı birkaç bin karakterlik yorumları
-- sessizce kırpıyordu. `source` sütunu bu satırın üstünde eklendiği için kısıt
-- burada tanımlanmak zorunda.
ALTER TABLE public.posts DROP CONSTRAINT IF EXISTS posts_body_check;
ALTER TABLE public.posts ADD CONSTRAINT posts_body_check
  CHECK (char_length(body) <= CASE WHEN source = 'letterboxd' THEN 10000 ELSE 420 END);

-- Günce taramasının kişi başına son çalıştığı an. Saatlik zamanlayıcı sırayı
-- buradan kuruyor: en uzun süredir taranmayan üye önce.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS diary_synced_at TIMESTAMPTZ;

-- Ardışık kaç taramanın boş geçtiği. Eşiği ikiye katlayarak yazmayan üyeyi
-- seyrekleştiriyor (tavan bir gün), ilk yeni kayıtta sıfırlanıyor. Koş başına
-- istek sayısı sabit olduğu için üyelik büyüdükçe bütçeyi bu koruyor.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS diary_idle_streak SMALLINT NOT NULL DEFAULT 0;

-- Yeni üyenin arşivi kaydolur kaydolmaz değil, uygulamaya girdikten sonra
-- arka planda taranıyor: koş başına birkaç sayfa, yeniden eskiye doğru, profil
-- doldukça görünerek. `diary_backfill_page` nereye kadar okunduğunu tutuyor
-- (0 = hiç başlanmadı), `diary_backfilled_at` arşivin bittiği an. Onboarding'i
-- bekletmemek için ayrı: kayıt akışı yüzlerce sayfalık bir taramaya bağlanamaz.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS diary_backfill_page SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS diary_backfilled_at TIMESTAMPTZ;

-- Var olan üyelerin arşivi toplu betikle zaten tarandı; onları kuyruğa sokmanın
-- anlamı yok. Yalnız bu satır bir kez çalışır, sonrakiler hiçbir şeyi değiştirmez.
UPDATE public.users SET diary_backfilled_at = NOW()
 WHERE diary_backfilled_at IS NULL AND diary_synced_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_posts_feed
  ON public.posts (created_at DESC, id DESC)
  WHERE deleted_at IS NULL AND reply_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_posts_author
  ON public.posts (author_id, created_at DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_posts_film
  ON public.posts (film_slug, created_at DESC)
  WHERE deleted_at IS NULL AND film_slug IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_posts_thread
  ON public.posts (reply_to, created_at)
  WHERE deleted_at IS NULL AND reply_to IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.post_likes (
  post_id    UUID NOT NULL REFERENCES public.posts(id) ON DELETE CASCADE,
  user_id    BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.follows (
  follower_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  followee_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  source      TEXT NOT NULL DEFAULT 'app' CHECK (source IN ('app', 'letterboxd')),
  status      TEXT NOT NULL DEFAULT 'accepted' CHECK (status IN ('pending', 'accepted')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (follower_id, followee_id),
  CHECK (follower_id <> followee_id)
);

-- Eski takipler, geçmişte zaten kurulmuş ilişkiler olduğu için kabul edilmiş
-- sayılır. Yeni kilitli hesaplarda aynı satır bekleyen istek olarak kullanılır.
ALTER TABLE public.follows ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'accepted';
UPDATE public.follows SET status = 'accepted' WHERE status IS NULL;
DO $$ BEGIN
  ALTER TABLE public.follows ADD CONSTRAINT follows_status_check
    CHECK (status IN ('pending', 'accepted'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_follows_followee
  ON public.follows (followee_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_follows_follower_status
  ON public.follows (follower_id, status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_reports_one_post_per_reporter
  ON public.user_reports (reporter_user_id, post_id) WHERE post_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.notifications (
  id         BIGSERIAL PRIMARY KEY,
  user_id    BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('reply', 'like', 'follow', 'follow_request', 'follow_accepted', 'letter', 'blend_request', 'blend_accepted', 'blend_rejected', 'bulletin')),
  actor_id   BIGINT REFERENCES public.users(id) ON DELETE CASCADE,
  post_id    UUID REFERENCES public.posts(id) ON DELETE CASCADE,
  event_key  TEXT,
  read_at    TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.notifications ADD COLUMN IF NOT EXISTS event_key TEXT;
-- 'bulletin': izleme listesindeki bir film perdeye geldiğinde haftada bir kez.
ALTER TABLE public.notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE public.notifications ADD CONSTRAINT notifications_kind_check
  CHECK (kind IN ('reply', 'like', 'follow', 'follow_request', 'follow_accepted',
                  'letter', 'blend_request', 'blend_accepted', 'blend_rejected',
                  'bulletin'));

CREATE INDEX IF NOT EXISTS idx_notifications_unread
  ON public.notifications (user_id, created_at DESC) WHERE read_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_event_key
  ON public.notifications (user_id, event_key) WHERE event_key IS NOT NULL;

-- One browser/device can be replaced without duplicating delivery. Push payloads
-- never contain a letter or note body; they only wake the recipient's browser.
CREATE TABLE IF NOT EXISTS public.web_push_subscriptions (
  id          BIGSERIAL PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  endpoint    TEXT NOT NULL UNIQUE,
  p256dh      TEXT NOT NULL,
  auth        TEXT NOT NULL,
  user_agent  TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_web_push_subscriptions_user
  ON public.web_push_subscriptions (user_id);

-- Counters are denormalized because every feed card would otherwise run its own
-- COUNT(*). Triggers keep them true no matter which path writes.
CREATE OR REPLACE FUNCTION public.posts_like_counter() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    UPDATE public.posts SET like_count = like_count + 1 WHERE id = NEW.post_id;
  ELSE
    UPDATE public.posts SET like_count = GREATEST(0, like_count - 1) WHERE id = OLD.post_id;
  END IF;
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_post_likes_counter ON public.post_likes;
CREATE TRIGGER trg_post_likes_counter
  AFTER INSERT OR DELETE ON public.post_likes
  FOR EACH ROW EXECUTE FUNCTION public.posts_like_counter();

CREATE OR REPLACE FUNCTION public.posts_reply_counter() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF TG_OP = 'INSERT' AND NEW.reply_to IS NOT NULL THEN
    UPDATE public.posts SET reply_count = reply_count + 1 WHERE id = NEW.reply_to;
  ELSIF TG_OP = 'DELETE' AND OLD.reply_to IS NOT NULL THEN
    UPDATE public.posts SET reply_count = GREATEST(0, reply_count - 1) WHERE id = OLD.reply_to;
  ELSIF TG_OP = 'UPDATE' AND NEW.reply_to IS NOT NULL THEN
    -- Silme yumuşak: satır kalır, deleted_at damgalanır. Sayaç bunu görmezse
    -- başlıkta "3 cevap" yazar ama tek cevap görünür. Geri alma da simetrik.
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
      UPDATE public.posts SET reply_count = GREATEST(0, reply_count - 1) WHERE id = NEW.reply_to;
    ELSIF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
      UPDATE public.posts SET reply_count = reply_count + 1 WHERE id = NEW.reply_to;
    END IF;
  END IF;
  RETURN NULL;
END;
$$;

-- UPDATE OF deleted_at: sayaç güncellemesi bu sütuna dokunmadığı için tetikleyici
-- kendini yeniden çağırmaz.
DROP TRIGGER IF EXISTS trg_posts_reply_counter ON public.posts;
CREATE TRIGGER trg_posts_reply_counter
  AFTER INSERT OR UPDATE OF deleted_at OR DELETE ON public.posts
  FOR EACH ROW EXECUTE FUNCTION public.posts_reply_counter();

-- TMDb API önbelleği: SQLite'ın üretim ortamındaki yedeği.
CREATE TABLE IF NOT EXISTS public.tmdb_cache (
  namespace   TEXT         NOT NULL,
  key         TEXT         NOT NULL,
  value       JSONB        NOT NULL,
  created_at  TIMESTAMPTZ  DEFAULT now(),
  PRIMARY KEY (namespace, key)
);

-- Row Level Security — backend yalnızca service_role secret ile bağlanır.
-- anon/authenticated rolleri browser veya ele geçirilmiş public key üzerinden bu
-- tablolardaki kullanıcı adlarını ve cache verisini okuyamaz/değiştiremez.
ALTER TABLE public.users     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tmdb_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.film_posters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.director_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.taste_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profile_favorites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_watched_films ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profile_sync_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_challenges ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.blend_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.blend_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cinephile_letters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.post_likes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.follows ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_push_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.venues ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.screenings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bulletin_digests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_audit_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service_all_users" ON public.users;
DROP POLICY IF EXISTS "service_all_tmdb_cache" ON public.tmdb_cache;

REVOKE ALL ON TABLE public.users FROM anon, authenticated;
REVOKE ALL ON TABLE public.tmdb_cache FROM anon, authenticated;
REVOKE ALL ON TABLE public.film_posters FROM anon, authenticated;
REVOKE ALL ON TABLE public.director_images FROM anon, authenticated;
REVOKE ALL ON TABLE public.taste_profiles FROM anon, authenticated;
REVOKE ALL ON TABLE public.profile_favorites FROM anon, authenticated;
REVOKE ALL ON TABLE public.user_watched_films FROM anon, authenticated;
REVOKE ALL ON TABLE public.profile_sync_jobs FROM anon, authenticated;
REVOKE ALL ON TABLE public.auth_challenges FROM anon, authenticated;
REVOKE ALL ON TABLE public.blend_requests FROM anon, authenticated;
REVOKE ALL ON TABLE public.blend_results FROM anon, authenticated;
REVOKE ALL ON TABLE public.user_blocks FROM anon, authenticated;
REVOKE ALL ON TABLE public.user_reports FROM anon, authenticated;
REVOKE ALL ON TABLE public.cinephile_letters FROM anon, authenticated;
REVOKE ALL ON TABLE public.posts FROM anon, authenticated;
REVOKE ALL ON TABLE public.post_likes FROM anon, authenticated;
REVOKE ALL ON TABLE public.follows FROM anon, authenticated;
REVOKE ALL ON TABLE public.notifications FROM anon, authenticated;
REVOKE ALL ON TABLE public.web_push_subscriptions FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.notifications_id_seq FROM anon, authenticated;
REVOKE ALL ON TABLE public.venues FROM anon, authenticated;
REVOKE ALL ON TABLE public.screenings FROM anon, authenticated;
REVOKE ALL ON TABLE public.bulletin_digests FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.venues_id_seq FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.screenings_id_seq FROM anon, authenticated;
REVOKE ALL ON TABLE public.auth_audit_log FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.users_id_seq FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.auth_audit_log_id_seq FROM anon, authenticated;

GRANT ALL ON TABLE public.users TO service_role;
GRANT ALL ON TABLE public.tmdb_cache TO service_role;
GRANT ALL ON TABLE public.film_posters TO service_role;
GRANT ALL ON TABLE public.director_images TO service_role;
GRANT ALL ON TABLE public.taste_profiles TO service_role;
GRANT ALL ON TABLE public.profile_favorites TO service_role;
GRANT ALL ON TABLE public.user_watched_films TO service_role;
GRANT ALL ON TABLE public.profile_sync_jobs TO service_role;
GRANT ALL ON TABLE public.auth_challenges TO service_role;
GRANT ALL ON TABLE public.blend_requests TO service_role;
GRANT ALL ON TABLE public.blend_results TO service_role;
GRANT ALL ON TABLE public.user_blocks TO service_role;
GRANT ALL ON TABLE public.user_reports TO service_role;
GRANT ALL ON TABLE public.cinephile_letters TO service_role;
GRANT ALL ON TABLE public.posts TO service_role;
GRANT ALL ON TABLE public.post_likes TO service_role;
GRANT ALL ON TABLE public.follows TO service_role;
GRANT ALL ON TABLE public.notifications TO service_role;
GRANT ALL ON TABLE public.web_push_subscriptions TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.notifications_id_seq TO service_role;
GRANT ALL ON TABLE public.venues TO service_role;
GRANT ALL ON TABLE public.screenings TO service_role;
GRANT ALL ON TABLE public.bulletin_digests TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.venues_id_seq TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.screenings_id_seq TO service_role;
GRANT ALL ON TABLE public.auth_audit_log TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.users_id_seq TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.auth_audit_log_id_seq TO service_role;
