-- Run only against an isolated PostgreSQL test database after both schemas.
BEGIN;
DO $test$
DECLARE uid BIGINT; movie UUID; favs JSONB; result JSONB;
BEGIN
 INSERT INTO users(username,account_status) VALUES('NativeCase','anonymous'),('nativecase','anonymous'),('NATIVECASE','active');
 BEGIN
  INSERT INTO users(username,account_status) VALUES('NaTiVeCaSe','active');
  RAISE EXCEPTION 'case-insensitive account duplicate accepted';
 EXCEPTION WHEN unique_violation THEN NULL;
 END;
 INSERT INTO users(username,account_status,profile_source) VALUES('native_db_review','active','native') RETURNING id INTO uid;
 INSERT INTO films(catalog_key,title,release_year) VALUES('test:one','One',2001),('test:two','Two',2002),('test:three','Three',2003),('test:four','Four',2004);
 SELECT jsonb_agg(jsonb_build_object('id',id) ORDER BY catalog_key) INTO favs FROM films WHERE catalog_key IN ('test:one','test:two','test:three','test:four');
 PERFORM save_native_favorites(uid,favs);
 IF (SELECT count(*) FROM profile_favorites WHERE user_id=uid)<>4 THEN RAISE EXCEPTION 'four favourites not saved'; END IF;
 BEGIN
  PERFORM save_native_favorites(uid,jsonb_build_array(favs->0,favs->0,favs->1,favs->2));
  RAISE EXCEPTION 'duplicate favourites accepted';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM='duplicate favourites accepted' THEN RAISE; END IF;
 END;
 SELECT id INTO movie FROM films WHERE catalog_key='test:one';
 result:=publish_film_note(uid,movie,'A note about a film I watched years ago.',4.5,FALSE);
 IF (result->>'created_at')::date<>CURRENT_DATE OR (result->>'user_rating')::real<>4.5 THEN RAISE EXCEPTION 'publication date or rating incorrect'; END IF;
 PERFORM publish_film_note(uid,movie,'A second thought, with no new rating.',NULL,FALSE);
 IF (SELECT count(*) FROM posts WHERE author_id=uid)<>2 THEN RAISE EXCEPTION 'several notes about one film were not preserved'; END IF;
 IF (SELECT count(*) FROM user_watched_films WHERE user_id=uid AND film_id=movie)<>1 THEN RAISE EXCEPTION 'watched aggregate duplicated'; END IF;
 PERFORM merge_import_library(uid,movie,TRUE,TRUE,2.0);
 IF (SELECT rating FROM user_films WHERE user_id=uid AND film_id=movie)<>4.5 THEN RAISE EXCEPTION 'ZIP overwrote native rating'; END IF;
 BEGIN
  PERFORM publish_film_note(uid,movie,'Invalid rating',6,FALSE);
  RAISE EXCEPTION 'invalid rating accepted';
 EXCEPTION WHEN check_violation THEN NULL;
 END;
 IF (SELECT count(*) FROM posts WHERE author_id=uid)<>2 THEN RAISE EXCEPTION 'failed write partially committed'; END IF;
 IF has_table_privilege('anon','public.user_films','SELECT') OR has_function_privilege('anon','public.publish_film_note(bigint,uuid,text,real,boolean)','EXECUTE') THEN RAISE EXCEPTION 'private data or RPC publicly accessible'; END IF;
 RAISE NOTICE 'Native SQL checks passed: distinct favourites, dated notes, rating validation, atomic writes, import precedence and RLS';
END $test$;
ROLLBACK;
