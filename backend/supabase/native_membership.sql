-- Additive migration. Existing auth IDs, usernames, posts and relationships stay.
BEGIN;
-- Anonymous archive profiles predate accounts and can differ only by case.
-- Keep their identities and relationships; reserve names uniquely for accounts.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_casefold ON public.users(lower(username)) WHERE account_status <> 'anonymous';
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS profile_source TEXT NOT NULL DEFAULT 'letterboxd';
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS letterboxd_username TEXT;
UPDATE public.users SET letterboxd_username = username WHERE profile_source = 'letterboxd' AND letterboxd_username IS NULL;
CREATE TABLE IF NOT EXISTS public.films (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 catalog_key TEXT NOT NULL UNIQUE,
 tmdb_id INTEGER UNIQUE,
 title TEXT NOT NULL,
 original_title TEXT NOT NULL DEFAULT '',
 release_year INTEGER,
 overview TEXT NOT NULL DEFAULT '',
 director TEXT NOT NULL DEFAULT '',
 genres JSONB NOT NULL DEFAULT '[]', keywords JSONB NOT NULL DEFAULT '[]',
 poster_url TEXT, vote_average REAL NOT NULL DEFAULT 0,
 details_loaded BOOLEAN NOT NULL DEFAULT FALSE,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS films_title_search ON public.films (lower(title));
CREATE TABLE IF NOT EXISTS public.film_aliases (
 provider TEXT NOT NULL, external_id TEXT NOT NULL,
 film_id UUID NOT NULL REFERENCES public.films(id),
 PRIMARY KEY (provider, external_id)
);
CREATE INDEX IF NOT EXISTS film_aliases_film ON public.film_aliases(film_id);
-- Consolidate metadata by external TMDb ID; every historic slug remains an alias.
INSERT INTO public.films(catalog_key, tmdb_id, title, release_year, overview, director, genres, keywords, poster_url, vote_average, details_loaded)
 SELECT DISTINCT ON (COALESCE('tmdb:' || tmdb_id::text, 'letterboxd:' || film_slug))
 COALESCE('tmdb:' || tmdb_id::text, 'letterboxd:' || film_slug), tmdb_id, title, release_year, overview, director, genres, keywords, poster_url, vote_average, details_loaded
 FROM public.film_posters p WHERE NOT EXISTS (SELECT 1 FROM public.films existing WHERE p.film_slug='movie-'||existing.id::text)
 ORDER BY COALESCE('tmdb:' || tmdb_id::text, 'letterboxd:' || film_slug), details_loaded DESC, updated_at DESC
 ON CONFLICT (catalog_key) DO NOTHING;
-- Older profiles may contain films that never reached the shared poster pool.
INSERT INTO public.films(catalog_key,tmdb_id,title,release_year,director,genres,keywords,poster_url,details_loaded)
 SELECT DISTINCT ON (COALESCE('tmdb:'||tmdb_id::text,'letterboxd:'||film_slug))
 COALESCE('tmdb:'||tmdb_id::text,'letterboxd:'||film_slug),tmdb_id,title,release_year,director,genres,keywords,poster_url,details_loaded
 FROM public.user_watched_films p WHERE NOT EXISTS (SELECT 1 FROM public.films existing WHERE p.film_slug='movie-'||existing.id::text)
 ORDER BY COALESCE('tmdb:'||tmdb_id::text,'letterboxd:'||film_slug),details_loaded DESC,updated_at DESC ON CONFLICT(catalog_key) DO NOTHING;
INSERT INTO public.films(catalog_key,tmdb_id,title,release_year,poster_url)
 SELECT DISTINCT ON (COALESCE('tmdb:'||tmdb_id::text,'letterboxd:'||slug))
 COALESCE('tmdb:'||tmdb_id::text,'letterboxd:'||slug),tmdb_id,title,release_year,poster_url
 FROM public.profile_favorites p WHERE NOT EXISTS (SELECT 1 FROM public.films existing WHERE p.slug='movie-'||existing.id::text)
 ORDER BY COALESCE('tmdb:'||tmdb_id::text,'letterboxd:'||slug) ON CONFLICT(catalog_key) DO NOTHING;
INSERT INTO public.film_aliases(provider,external_id,film_id)
 SELECT 'letterboxd', p.film_slug, f.id FROM public.film_posters p JOIN public.films f
 ON f.catalog_key = COALESCE('tmdb:' || p.tmdb_id::text, 'letterboxd:' || p.film_slug)
 ON CONFLICT DO NOTHING;
INSERT INTO public.film_aliases(provider,external_id,film_id)
 SELECT 'letterboxd', p.film_slug, f.id FROM public.user_watched_films p JOIN public.films f
 ON f.catalog_key=COALESCE('tmdb:'||p.tmdb_id::text,'letterboxd:'||p.film_slug) ON CONFLICT DO NOTHING;
INSERT INTO public.film_aliases(provider,external_id,film_id)
 SELECT 'letterboxd', p.slug, f.id FROM public.profile_favorites p JOIN public.films f
 ON f.catalog_key=COALESCE('tmdb:'||p.tmdb_id::text,'letterboxd:'||p.slug) ON CONFLICT DO NOTHING;
ALTER TABLE public.profile_favorites ADD COLUMN IF NOT EXISTS film_id UUID REFERENCES public.films(id);
ALTER TABLE public.user_watched_films ADD COLUMN IF NOT EXISTS film_id UUID REFERENCES public.films(id);
ALTER TABLE public.posts ADD COLUMN IF NOT EXISTS film_id UUID REFERENCES public.films(id);
ALTER TABLE public.posts ADD COLUMN IF NOT EXISTS user_rating REAL;
DO $$ BEGIN
 ALTER TABLE public.posts ADD CONSTRAINT posts_user_rating_check CHECK(user_rating IS NULL OR (user_rating BETWEEN 0.5 AND 5 AND user_rating * 2 = trunc(user_rating * 2)));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
UPDATE public.profile_favorites p SET film_id=a.film_id FROM public.film_aliases a WHERE a.provider='letterboxd' AND a.external_id=p.slug AND p.film_id IS NULL;
UPDATE public.user_watched_films p SET film_id=a.film_id FROM public.film_aliases a WHERE a.provider='letterboxd' AND a.external_id=p.film_slug AND p.film_id IS NULL;
UPDATE public.posts p SET film_id=a.film_id FROM public.film_aliases a WHERE a.provider='letterboxd' AND a.external_id=p.film_slug AND p.film_id IS NULL;
-- Existing profile snapshot RPCs keep their interface and automatically attach
-- canonical IDs when they rewrite a set of favourites.
CREATE OR REPLACE FUNCTION public.attach_catalog_identity() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=public AS $$
DECLARE slug_value TEXT;
BEGIN
 slug_value:=COALESCE(to_jsonb(NEW)->>'film_slug',to_jsonb(NEW)->>'slug');
 IF slug_value ~ '^movie-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
  NEW.film_id:=substring(slug_value FROM 7)::uuid;
 ELSIF NEW.film_id IS NULL THEN
  SELECT film_id INTO NEW.film_id FROM film_aliases WHERE provider='letterboxd' AND external_id=slug_value;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS native_favorite_identity ON public.profile_favorites;
CREATE TRIGGER native_favorite_identity BEFORE INSERT OR UPDATE OF slug ON public.profile_favorites FOR EACH ROW EXECUTE FUNCTION public.attach_catalog_identity();
DROP TRIGGER IF EXISTS native_watched_identity ON public.user_watched_films;
CREATE TRIGGER native_watched_identity BEFORE INSERT OR UPDATE OF film_slug ON public.user_watched_films FOR EACH ROW EXECUTE FUNCTION public.attach_catalog_identity();
DROP TRIGGER IF EXISTS native_post_identity ON public.posts;
CREATE TRIGGER native_post_identity BEFORE INSERT OR UPDATE OF film_slug ON public.posts FOR EACH ROW EXECUTE FUNCTION public.attach_catalog_identity();
CREATE TABLE IF NOT EXISTS public.user_films (
 user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 film_id UUID NOT NULL REFERENCES public.films(id),
 watched BOOLEAN NOT NULL DEFAULT FALSE, watchlist BOOLEAN NOT NULL DEFAULT FALSE,
 rating REAL CHECK(rating IS NULL OR (rating BETWEEN 0.5 AND 5 AND rating * 2 = trunc(rating * 2))),
 rating_source TEXT NOT NULL DEFAULT 'letterboxd', updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(user_id,film_id)
);
ALTER TABLE public.user_films ADD COLUMN IF NOT EXISTS watched_source TEXT NOT NULL DEFAULT 'letterboxd';
ALTER TABLE public.user_films ADD COLUMN IF NOT EXISTS watchlist_source TEXT NOT NULL DEFAULT 'letterboxd';
INSERT INTO public.user_films(user_id,film_id,watched,rating)
 SELECT DISTINCT ON(user_id,film_id) user_id,film_id,TRUE,user_rating FROM public.user_watched_films
 WHERE film_id IS NOT NULL AND is_active ORDER BY user_id,film_id,updated_at DESC ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS public.library_imports (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 fingerprint TEXT NOT NULL, source_username TEXT,
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('staging','queued','running','done','failed')),
 counts JSONB NOT NULL DEFAULT '{}', last_error TEXT NOT NULL DEFAULT '',
 lease_until TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(user_id,fingerprint)
);
CREATE TABLE IF NOT EXISTS public.imported_film_entries (
 user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 source_key TEXT NOT NULL, import_id UUID NOT NULL REFERENCES public.library_imports(id) ON DELETE CASCADE,
 film_id UUID REFERENCES public.films(id), kind TEXT NOT NULL,
 data JSONB NOT NULL, match_status TEXT NOT NULL DEFAULT 'pending',
 PRIMARY KEY(user_id,source_key)
);
CREATE INDEX IF NOT EXISTS imported_entries_pending ON public.imported_film_entries(import_id,match_status);

CREATE OR REPLACE FUNCTION public.save_native_favorites(p_user_id BIGINT,p_films JSONB) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE item JSONB; slot INTEGER := 0;
BEGIN
 PERFORM 1 FROM users WHERE id=p_user_id FOR UPDATE;
 IF jsonb_array_length(p_films) <> 4 OR (SELECT count(DISTINCT x->>'id') FROM jsonb_array_elements(p_films) x) <> 4 THEN RAISE EXCEPTION 'four_distinct_films_required'; END IF;
 DELETE FROM profile_favorites WHERE user_id=p_user_id;
 FOR item IN SELECT * FROM jsonb_array_elements(p_films) LOOP
  slot := slot+1;
  INSERT INTO profile_favorites(user_id,position,film_id,slug,title,release_year,tmdb_id,poster_url)
   SELECT p_user_id,slot,id,'movie-'||id::text,title,release_year,tmdb_id,poster_url FROM films WHERE id=(item->>'id')::uuid;
  IF NOT FOUND THEN RAISE EXCEPTION 'film_not_found'; END IF;
 END LOOP;
 UPDATE users SET profile_source='native', profile_sync_status='ready',updated_at=now() WHERE id=p_user_id;
END $$;

-- A private library aggregate is updated transactionally with a public note.
-- No caller-supplied publication or watched date enters this function.
CREATE OR REPLACE FUNCTION public.publish_film_note(p_user_id BIGINT,p_film_id UUID,p_body TEXT,p_rating REAL,p_spoiler BOOLEAN) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE movie films; note posts;
BEGIN
 SELECT * INTO STRICT movie FROM films WHERE id=p_film_id;
 INSERT INTO user_films(user_id,film_id,watched,rating,rating_source,watched_source)
 VALUES(p_user_id,p_film_id,TRUE,p_rating,CASE WHEN p_rating IS NULL THEN 'letterboxd' ELSE 'native' END,'native')
 ON CONFLICT(user_id,film_id) DO UPDATE SET watched=TRUE,watched_source='native',
 rating=COALESCE(EXCLUDED.rating,user_films.rating),
 rating_source=CASE WHEN EXCLUDED.rating IS NOT NULL THEN 'native' ELSE user_films.rating_source END,updated_at=now();
 INSERT INTO posts(author_id,kind,film_id,film_slug,tmdb_id,film_title,film_year,body,user_rating,spoiler,source,created_at)
 VALUES(p_user_id,'note',movie.id,'movie-'||movie.id::text,movie.tmdb_id,movie.title,movie.release_year,p_body,p_rating,p_spoiler,'app',now()) RETURNING * INTO note;
 UPDATE user_watched_films SET user_rating=COALESCE(p_rating,user_rating),rating_observed=rating_observed OR p_rating IS NOT NULL,is_active=TRUE,updated_at=now() WHERE user_id=p_user_id AND film_id=movie.id;
 IF NOT FOUND THEN
 INSERT INTO user_watched_films(user_id,film_id,film_slug,title,release_year,tmdb_id,director,genres,keywords,poster_url,user_rating,rating_observed,details_loaded)
 VALUES(p_user_id,movie.id,'movie-'||movie.id::text,movie.title,movie.release_year,movie.tmdb_id,movie.director,movie.genres,movie.keywords,movie.poster_url,p_rating,p_rating IS NOT NULL,movie.details_loaded)
 ON CONFLICT(user_id,film_slug) DO UPDATE SET user_rating=COALESCE(EXCLUDED.user_rating,user_watched_films.user_rating),rating_observed=user_watched_films.rating_observed OR EXCLUDED.rating_observed,is_active=TRUE,updated_at=now();
 END IF;
 RETURN to_jsonb(note);
END $$;

CREATE OR REPLACE FUNCTION public.merge_imported_film(p_user_id BIGINT,p_from UUID,p_to UUID) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE old_row user_films;
BEGIN
 IF p_from=p_to THEN RETURN; END IF;
 PERFORM 1 FROM users WHERE id=p_user_id FOR UPDATE;
 SELECT * INTO old_row FROM user_films WHERE user_id=p_user_id AND film_id=p_from FOR UPDATE;
 IF FOUND THEN
  INSERT INTO user_films(user_id,film_id,watched,watchlist,rating,rating_source,watched_source,watchlist_source)
  VALUES(p_user_id,p_to,old_row.watched,old_row.watchlist,old_row.rating,old_row.rating_source,old_row.watched_source,old_row.watchlist_source)
  ON CONFLICT(user_id,film_id) DO UPDATE SET watched=CASE WHEN user_films.watched_source='native' THEN user_films.watched ELSE user_films.watched OR EXCLUDED.watched END,watchlist=CASE WHEN user_films.watchlist_source='native' THEN user_films.watchlist ELSE user_films.watchlist OR EXCLUDED.watchlist END,
   rating=CASE WHEN user_films.rating_source='native' THEN user_films.rating ELSE COALESCE(EXCLUDED.rating,user_films.rating) END,
   rating_source=CASE WHEN user_films.rating_source='native' OR EXCLUDED.rating_source='native' THEN 'native' ELSE 'letterboxd' END,
   watched_source=CASE WHEN user_films.watched_source='native' OR EXCLUDED.watched_source='native' THEN 'native' ELSE 'letterboxd' END,
   watchlist_source=CASE WHEN user_films.watchlist_source='native' OR EXCLUDED.watchlist_source='native' THEN 'native' ELSE 'letterboxd' END;
  DELETE FROM user_films WHERE user_id=p_user_id AND film_id=p_from;
 END IF;
 UPDATE imported_film_entries SET film_id=p_to,match_status='confirmed',data=jsonb_set(data,'{_confirmed_film_id}',to_jsonb(p_to::text)) WHERE user_id=p_user_id AND film_id=p_from;
 UPDATE posts SET film_id=p_to,film_slug='movie-'||p_to::text,film_title=f.title,film_year=f.release_year,tmdb_id=f.tmdb_id FROM films f WHERE author_id=p_user_id AND film_id=p_from AND f.id=p_to;
 UPDATE profile_favorites SET film_id=p_to,slug='movie-'||p_to::text,title=f.title,release_year=f.release_year,tmdb_id=f.tmdb_id,poster_url=f.poster_url FROM films f WHERE user_id=p_user_id AND film_id=p_from AND f.id=p_to;
END $$;

CREATE OR REPLACE FUNCTION public.set_library_state(p_user_id BIGINT,p_film_id UUID,p_patch JSONB) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE result user_films;
BEGIN
 INSERT INTO user_films(user_id,film_id,watched,watchlist,rating,rating_source,watched_source,watchlist_source)
 VALUES(p_user_id,p_film_id,COALESCE((p_patch->>'watched')::boolean,FALSE),COALESCE((p_patch->>'watchlist')::boolean,FALSE),(p_patch->>'rating')::real,
 CASE WHEN p_patch ? 'rating' THEN 'native' ELSE 'letterboxd' END,CASE WHEN p_patch ? 'watched' THEN 'native' ELSE 'letterboxd' END,CASE WHEN p_patch ? 'watchlist' THEN 'native' ELSE 'letterboxd' END)
 ON CONFLICT(user_id,film_id) DO UPDATE SET
 watched=CASE WHEN p_patch ? 'watched' THEN EXCLUDED.watched ELSE user_films.watched END,
 watchlist=CASE WHEN p_patch ? 'watchlist' THEN EXCLUDED.watchlist ELSE user_films.watchlist END,
 rating=CASE WHEN p_patch ? 'rating' THEN EXCLUDED.rating ELSE user_films.rating END,
 watched_source=CASE WHEN p_patch ? 'watched' THEN 'native' ELSE user_films.watched_source END,
 watchlist_source=CASE WHEN p_patch ? 'watchlist' THEN 'native' ELSE user_films.watchlist_source END,
 rating_source=CASE WHEN p_patch ? 'rating' THEN 'native' ELSE user_films.rating_source END,updated_at=now()
 RETURNING * INTO result;
 RETURN to_jsonb(result);
END $$;

CREATE OR REPLACE FUNCTION public.merge_import_library(p_user_id BIGINT,p_film_id UUID,p_watched BOOLEAN,p_watchlist BOOLEAN,p_rating REAL) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
BEGIN
 INSERT INTO user_films(user_id,film_id,watched,watchlist,rating,rating_source)
 VALUES(p_user_id,p_film_id,p_watched,p_watchlist,p_rating,'letterboxd')
 ON CONFLICT(user_id,film_id) DO UPDATE SET watched=CASE WHEN user_films.watched_source='native' THEN user_films.watched ELSE user_films.watched OR EXCLUDED.watched END,watchlist=CASE WHEN user_films.watchlist_source='native' THEN user_films.watchlist ELSE user_films.watchlist OR EXCLUDED.watchlist END,
 rating=CASE WHEN user_films.rating_source='native' THEN user_films.rating ELSE COALESCE(EXCLUDED.rating,user_films.rating) END,updated_at=now();
END $$;

CREATE OR REPLACE FUNCTION public.claim_library_import(p_import_id UUID) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
BEGIN
 UPDATE library_imports SET state='running',lease_until=now()+interval '5 minutes',updated_at=now()
 WHERE id=p_import_id AND state IN ('queued','running','failed') AND (lease_until IS NULL OR lease_until<now());
 RETURN FOUND;
END $$;

ALTER TABLE public.films ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.film_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_films ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.library_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.imported_film_entries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.films,public.film_aliases,public.user_films,public.library_imports,public.imported_film_entries FROM anon,authenticated;
GRANT ALL ON TABLE public.films,public.film_aliases,public.user_films,public.library_imports,public.imported_film_entries TO service_role;
REVOKE ALL ON FUNCTION public.save_native_favorites(BIGINT,JSONB),public.publish_film_note(BIGINT,UUID,TEXT,REAL,BOOLEAN),public.merge_imported_film(BIGINT,UUID,UUID),public.merge_import_library(BIGINT,UUID,BOOLEAN,BOOLEAN,REAL),public.claim_library_import(UUID),public.set_library_state(BIGINT,UUID,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.save_native_favorites(BIGINT,JSONB),public.publish_film_note(BIGINT,UUID,TEXT,REAL,BOOLEAN),public.merge_imported_film(BIGINT,UUID,UUID),public.merge_import_library(BIGINT,UUID,BOOLEAN,BOOLEAN,REAL),public.claim_library_import(UUID),public.set_library_state(BIGINT,UUID,JSONB) TO service_role;
COMMIT;
