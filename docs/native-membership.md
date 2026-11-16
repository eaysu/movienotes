# Independent membership and film library

Implementation contract (16 November 2026):

- Register using a case-insensitive unique username and a password entered twice.
  Account creation and a session do not require Letterboxd or email. Passwords
  remain in Supabase Auth. Existing IDs and synthetic auth identities are retained. Case-only duplicates
  among old anonymous archive profiles are preserved; the unique account-name
  index covers registered accounts.
- Onboarding saves exactly four different catalog film IDs, then generates a
  short taste analysis with the configured GPT model and introduces the product.
  Missing model/provider availability must leave the account usable and allow retry.
- Catalog films have their own UUID. TMDb IDs are unique external references;
  old Letterboxd slugs remain aliases. Search checks saved metadata and fills
  gaps from TMDb. Poster paths use the TMDb image CDN.
- A new note requires a catalog film and 1–420 characters. An optional rating is
  0.5–5 in half-star increments. The server supplies the publication timestamp;
  no watched date or client-created timestamp is accepted. A film need not
  already be in the member's archive. Several notes about a film are allowed.
- ZIP imports preserve watched films, ratings, watchlists, repeated diary entries
  and reviews, without fetching Letterboxd pages. Reimports are idempotent;
  native ratings and notes win over imported data. Uncertain matches require
  confirmation and retain their original metadata. Imported reviews are private
  archive entries, not newly published public notes.
- Background metadata matching resumes after restarts. Letterboxd crawls are
  disabled by default. No production migration or deployment is automatic.

New routes: POST /api/auth/register; GET /api/catalog/search;
GET /api/catalog/films/{film_id}; PUT /api/profile/favorites;
POST /api/profile/analysis; GET /api/library; PUT /api/library/{film_id};
GET /api/library/archive; GET /api/library/imports; POST /api/library/matches/{film_id}.
Existing /api/posts and /api/profile/import-letterboxd adopt the above contract.
All personal writes use the existing session and CSRF guards. Catalog selection
is resolved on the server; client titles, posters and ratings never establish
canonical metadata.

Apply backend/supabase/native_membership.sql before starting this version.


## Release and verification

Before deploying to Render, apply the
additive `backend/supabase/native_membership.sql` to the existing Supabase
project before deploying this application version. Do not replace the existing
auth identity secret or domain. Render must keep
`LETTERBOXD_SCRAPING_ENABLED=false`. Warm the catalog with
`python -m scripts.warm_catalog --pages 5` after migration if desired; users can
also populate it through search. Existing passwordless members need to set a
password from their current session before switching to password-only login.

Verified on 16 November 2026 (548 tests passed; the two optional PostgreSQL
tests skipped in that run were also executed separately and both passed;
JavaScript syntax and the production CSS build passed):

- The real personal ZIP parsed all 563 watched rows, 256 ratings, 202 watchlist
  rows, 149 diary rows, 86 full reviews and four favorite URLs. The 149 diary
  rows and 86 reviews form 150 distinct archive events. Duplicate title/year
  films and repeated viewings remain distinct. Full review text, ratings,
  source dates and favorite order were compared with the CSV contents.
- Reimporting that ZIP does not duplicate records or publish private reviews.
  Native ratings and watchlist edits survive reimports. A later metadata match
  moves existing notes to the resolved film without changing their dates.
- A disposable local PostgreSQL/PostgREST database exercised atomic note
  publication, canonical favorites, native edit precedence, private archive
  matching and repeat imports. The migration ran twice without growing the
  film catalog. SQL transaction checks cover rating constraints and RLS.
- The real application ran locally against the committed TMDb/OpenAI stand-ins.
  Desktop and 390px mobile browser checks covered registration, four-film
  selection, the three introduction pages, analysis, two notes on one film,
  optional 4.5 rating, and watchlist changes. A representative ZIP was also
  uploaded through the library UI: its multiline review remained private,
  the existing native 4.5 rating survived the imported 3 rating, and the
  public feed stayed at two user-authored notes. The exercised browser flow had
  no console errors, horizontal overflow or Letterboxd fetches.

The simulation establishes caller behavior against these stand-ins; it does
not establish current Render connectivity, real Supabase Auth configuration,
TMDb availability or the configured GPT model's production response. Perform a
new-account/import smoke check on Render after migration and deployment.
Personal ZIPs and request logs are intentionally excluded from the repository.
See `postman/simulations/native-membership/native-membership.context.yaml`.
