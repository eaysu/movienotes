"""Username-first account service backed by Supabase Auth.

Passwords and password hashes never enter application tables. A deterministic,
non-routable synthetic email maps each Letterboxd username to Supabase Auth while
the user-facing product remains username-only.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from .enrich import EnrichedFilm
from .scraper import ScrapedProfile
from .semantic import taste_document_scores
from .taste_profile import TasteProfileSnapshot


logger = logging.getLogger(__name__)

# Uygulamada yazılan not 420 karakter — kısa kalması ürün kararı, sınırı API
# katmanı uyguluyor. Letterboxd'dan gelen yorum ise olduğu gibi geliyor; bazıları
# birkaç bin karakterlik deneme yazısı. Akış kartı zaten 220 karakterde kırpıp
# "devamını oku" gösterdiği için uzun gövde arayüzü bozmuyor.
DIARY_BODY_MAX = 10_000

# Akışta günce kaydının görünür kaldığı süre. Bütün arşiv içeri alınıyor ama
# keşif akışlarının penceresi var; yoksa yıllar öncesinin kayıtları akışı
# doldurur. Üyenin kendi notları ve profil sayfası penceresiz.
#
# İki pencere farklı çünkü iki akış farklı iş yapıyor: topluluk 130 kişilik bir
# havuzdan "bu hafta ne konuşuluyor"u gösteriyor, takip ettiklerin ise seçilmiş
# birkaç kişiyi — orada bir hafta çoğu zaman boş bir sayfa demek.
FEED_DIARY_WINDOW_DAYS = 7
FEED_FOLLOWING_WINDOW_DAYS = 30


# Push bildirimi, uygulama içi bildirimin kısa ve ekranda bağımsız okunabilen
# karşılığıdır. Metinler olayın kendisini söyler; "yeni bildirimin var" gibi
# boş bir uyarı üyeyi uygulamayı açmaya zorlamaz.
_PUSH_COPY: dict[str, dict[str, tuple[str, str]]] = {
    "follow": {
        "tr": ("Yeni bir takipçi", "Bir sinefil seni takibe aldı. Ortak filmlerinizin izini sürmek için profiline göz at."),
        "en": ("A new follower", "A cinephile has started following you. Take a look at the films you may have in common."),
    },
    "follow_request": {
        "tr": ("Yeni takip isteği", "Bir sinefil seni takip etmek istiyor. İsteğini bildirimlerden değerlendirebilirsin."),
        "en": ("New follow request", "A cinephile would like to follow you. You can review the request in Notifications."),
    },
    "follow_accepted": {
        "tr": ("Takip isteğin kabul edildi", "Artık birbirinizin sinema dünyasını daha yakından keşfedebilirsiniz."),
        "en": ("Your follow request was accepted", "You can now explore each other’s cinema worlds more closely."),
    },
    "blend_request": {
        "tr": ("Yeni bir Blend isteği", "Bir sinefil zevklerinizi karşılaştırmak istiyor. Kesişiminizde hangi filmler var, birlikte görün."),
        "en": ("A new Blend request", "A cinephile wants to compare your tastes. See which films meet in the middle."),
    },
    "blend_accepted": {
        "tr": ("Blend’in hazır", "İsteğin kabul edildi. Ortak film haritanız şimdi seni bekliyor."),
        "en": ("Your Blend is ready", "Your request was accepted. Your shared film map is ready to explore."),
    },
    "blend_rejected": {
        "tr": ("Blend isteğin yanıtlandı", "Bu kez eşleşemediniz; sinema başka bir ortak noktada yeniden buluşturabilir."),
        "en": ("Your Blend request was answered", "Not a match this time — cinema may bring you together elsewhere."),
    },
    "bulletin": {
        "tr": ("Perdede sana göre bir film var", "Bu hafta vizyonda zevkine yakın bir film seni bekliyor. Seanslara göz at."),
        "en": ("A film for you is on screen", "A film close to your taste is in theatres this week. Have a look at the showtimes."),
    },
    "nightly_pick": {
        "tr": ("Bu gece ne izlesen?", "{title} seni bekliyor. Işıkları kıs, ilk sahneyi aç."),
        "en": ("What should you watch tonight?", "{title} is waiting for you. Dim the lights and press play."),
    },
}


def _clip_review(body: str) -> str:
    """Çok uzun yorumu sınırda keser; kesildiğini görünür kılar."""
    body = body.strip()
    if len(body) <= DIARY_BODY_MAX:
        return body
    return body[:DIARY_BODY_MAX - 1].rstrip() + "…"


try:  # Optional locally; Render installs the pinned package for real delivery.
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - keeps local schema/unit tooling light
    WebPushException = Exception
    webpush = None


class AuthError(Exception):
    code = "auth_failed"


class AccountExistsError(AuthError):
    code = "account_exists"


class InvalidCredentialsError(AuthError):
    code = "invalid_credentials"


class VerificationError(AuthError):
    code = "verification_failed"


class VerificationExpiredError(VerificationError):
    code = "verification_expired"


class OwnershipPendingError(VerificationError):
    """The code is the right one; it just is not on the Letterboxd page yet.

    This is the ordinary signup moment, not a failed attempt: the member holds
    a code only this server issued, and the bio edit has simply not landed.
    Counting it as an attempt burned through the challenge — five impatient
    checks killed a registration that was never wrong about anything.
    """


class OwnershipProofError(VerificationError):
    code = "ownership_proof_missing"


class BlendServiceError(AuthError):
    code = "blend_failed"


class TransientStorageError(AuthError):
    """A safe retryable Supabase edge failure surfaced to the HTTP layer."""

    code = "storage_temporarily_unavailable"


@dataclass
class Account:
    id: int
    auth_user_id: str
    username: str
    display_name: str = ""
    avatar_url: str = ""
    account_status: str = "active"
    profile_sync_status: str = "pending"
    onboarding_completed_at: str | None = None
    letterboxd_stats: dict = field(default_factory=dict)
    discoverable: bool = True
    letter_receiving_enabled: bool = False
    private_account: bool = False
    # `auto` follows the device locale; explicit values travel with the account.
    preferred_locale: str = "auto"


@dataclass
class AuthSession:
    account: Account
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass
class RegistrationChallenge:
    username: str
    verification_code: str
    expires_at: str


def validate_password(password: str, confirmation: str | None = None) -> str:
    if not isinstance(password, str) or len(password) < 10:
        raise ValueError("Parola en az 10 karakter olmalı.")
    if len(password) > 128:
        raise ValueError("Parola en fazla 128 karakter olabilir.")
    if confirmation is not None and password != confirmation:
        raise ValueError("Parolalar eşleşmiyor.")
    return password


# ── Sinefil Sineması eşleşme skoru ──────────────────────────────────────────
# Her sinyalin kendi tavanı var. Önceden tek bir ortak Fav 4 filmi 42 puandı,
# beş ortak yönetmen ise 30: bir rastlantı, baştan aşağı örtüşen bir rafın
# önüne geçiyordu. Sıralı on yönetmen ve tür listesi artık gerçek ağırlık
# taşıyor — aynı yönetmenleri izleyen iki kişi, dört favorileri tutmasa da
# eşleşir.
SINEFIL_WEIGHTS = {
    "fav4": (30, 60),
    "directors": (7, 35),
    "genres": (6, 24),
    "keywords": (2, 12),
}
SINEFIL_SEMANTIC_WEIGHT = 12
SINEFIL_SEMANTIC_MIN_COVERAGE = 0.30


def sinefil_match_score(
    *,
    shared_fav4: int,
    shared_directors: int,
    shared_genres: int,
    shared_keywords: int,
    semantic_score: float = 0.0,
    semantic_coverage: float = 0.0,
) -> int:
    """How closely two members' stated tastes line up, out of 100."""
    def capped(kind: str, count: int) -> int:
        weight, ceiling = SINEFIL_WEIGHTS[kind]
        return min(max(0, int(count)) * weight, ceiling)

    semantic = (
        round(float(semantic_score) * SINEFIL_SEMANTIC_WEIGHT)
        if semantic_coverage >= SINEFIL_SEMANTIC_MIN_COVERAGE
        else 0
    )
    return min(100, (
        capped("fav4", shared_fav4)
        + capped("directors", shared_directors)
        + capped("genres", shared_genres)
        + capped("keywords", shared_keywords)
        + semantic
    ))


class AuthService:
    CHALLENGE_TTL_MINUTES = 15
    MAX_CHALLENGE_ATTEMPTS = 5
    STORAGE_READ_ATTEMPTS = 3

    def __init__(
        self,
        settings,
        *,
        client_factory: Callable[[str, str], Any] | None = None,
    ):
        if not getattr(settings, "has_auth", False):
            raise RuntimeError("Auth yapılandırılmamış.")
        if client_factory is None:
            from supabase import create_client

            client_factory = create_client
        self.settings = settings
        self._client_factory = client_factory
        # Service-role requests do not carry a user's mutable auth session, so
        # the underlying HTTP connection pool can safely be reused. Auth clients
        # remain per-operation below to avoid sharing session state across users.
        self._service_client_instance = None
        self._service_client_ready = False
        self._service_client_lock = threading.Lock()
        self._activity_disabled_until = 0.0

    def _service_client(self):
        if self._service_client_ready:
            return self._service_client_instance
        with self._service_client_lock:
            if not self._service_client_ready:
                self._service_client_instance = self._client_factory(
                    self.settings.supabase_url, self.settings.supabase_key
                )
                self._service_client_ready = True
        return self._service_client_instance

    def _auth_client(self):
        return self._client_factory(
            self.settings.supabase_url, self.settings.supabase_anon_key
        )

    @staticmethod
    def _is_transient_storage_error(exc: Exception) -> bool:
        """Recognise transient Supabase edge failures, not real API 4xx errors."""
        detail = f"{type(exc).__name__} {exc}".casefold()
        return (
            "cloudflare" in detail
            or "json could not be generated" in detail
            or "json_invalid" in detail
            or "server disconnected" in detail
            or "connectionterminated" in detail
            or "remoteprotocolerror" in detail
            or "eof occurred in violation of protocol" in detail
        )

    def _retry_storage_operation(self, operation: Callable[[], Any]) -> Any:
        """Retry a caller-confirmed idempotent Supabase operation after edge errors."""
        for attempt in range(self.STORAGE_READ_ATTEMPTS):
            try:
                return operation()
            except Exception as exc:
                if not self._is_transient_storage_error(exc):
                    raise
                if attempt + 1 >= self.STORAGE_READ_ATTEMPTS:
                    raise TransientStorageError(
                        "Veri bağlantısı kısa süreli yanıt vermedi. Lütfen tekrar dene."
                    ) from exc
                # This function is called from asyncio.to_thread, so the small
                # backoff does not block FastAPI's event loop.
                time.sleep(0.25 * (attempt + 1))

    def _retry_storage_read(self, operation: Callable[[], Any]) -> Any:
        """Backward-compatible name for retrying idempotent storage reads."""
        return self._retry_storage_operation(operation)

    def check_schema(self) -> bool:
        """Verify the account schema without exposing or reading user records."""
        service = self._service_client()
        required = {
            "users": "id,auth_user_id,account_status,onboarding_completed_at,letterboxd_stats,discoverable,private_account",
            "taste_profiles": "user_id,source_fingerprint,top_directors",
            "profile_favorites": "user_id,position",
            "blend_requests": "id,status",
            "blend_results": "id,request_id",
            "user_blocks": "blocker_user_id,blocked_user_id",
            "user_reports": "id,status",
            "cinephile_letters": "id,sender_user_id,recipient_user_id,body,read_at",
        }
        for table, columns in required.items():
            service.table(table).select(columns).limit(0).execute()
        return True

    def check_sync_schema(self) -> bool:
        """Verify the full-history sync tables exist, without reading records.

        Kept separate from check_schema so the core profile flow still works on a
        deployment where the background-sync migration has not been run yet.
        """
        service = self._service_client()
        service.table("user_watched_films").select(
            "user_id,film_slug,details_loaded,watched_rank,poster_resolver_url"
        ).limit(0).execute()
        service.table("profile_sync_jobs").select(
            "user_id,state,phase,cursor_page,scope,sync_run_id,lease_token,lease_expires_at"
        ).limit(0).execute()
        service.table("director_images").select(
            "normalized_name,photo_url,tmdb_person_id"
        ).limit(0).execute()
        service.table("film_posters").select(
            "film_slug,poster_url,poster_resolver_url,tmdb_id,overview,director,genres,keywords,details_loaded"
        ).limit(0).execute()
        return True

    def _digest(self, value: str, *, purpose: str) -> str:
        return hmac.new(
            self.settings.auth_identity_secret.encode("utf-8"),
            f"{purpose}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def identity_email(self, username: str) -> str:
        """Supabase Auth'a verilen sentetik e-posta.

        DİKKAT: Bu alan adı marka değişse bile sabit kalmalı. E-posta her
        girişte kullanıcı adından yeniden üretiliyor; alan adını değiştirmek
        var olan bütün hesapların Auth kaydını bulunamaz hâle getirir, yani
        herkesi kapı dışında bırakır. Marka adıyla ilgisi olmayan, kalıcı bir
        kimlik alanı olarak okunmalı.
        """
        identity = self._digest(username, purpose="identity")[:40]
        return f"lb-{identity}@users.movieboxd.invalid"

    def challenge_hash(self, code: str) -> str:
        return self._digest(code.upper(), purpose="challenge")

    @staticmethod
    def _account(row: dict) -> Account:
        return Account(
            id=int(row["id"]),
            auth_user_id=str(row["auth_user_id"]),
            username=row["username"],
            display_name=row.get("display_name") or row["username"],
            avatar_url=row.get("avatar_url") or "",
            account_status=row.get("account_status") or "anonymous",
            profile_sync_status=row.get("profile_sync_status") or "pending",
            onboarding_completed_at=row.get("onboarding_completed_at"),
            letterboxd_stats=row.get("letterboxd_stats") or {},
            discoverable=bool(row.get("discoverable", True)),
            letter_receiving_enabled=bool(row.get("letter_receiving_enabled", False)),
            private_account=bool(row.get("private_account", False)),
            preferred_locale=(row.get("preferred_locale") or "auto").lower(),
        )

    @staticmethod
    def _first(result) -> dict | None:
        return (result.data or [None])[0]

    def _account_row_by_username(self, client, username: str) -> dict | None:
        # Authentication only needs durable identity fields. Social-preference
        # migrations must never turn a valid password into a server error.
        identity_columns = (
            "id,auth_user_id,username,display_name,avatar_url,"
            "account_status,profile_sync_status,onboarding_completed_at,letterboxd_stats"
        )

        def read():
            return (
                client.table("users")
                .select(identity_columns)
                .eq("username", username)
                .limit(1)
                .execute()
            )
        result = self._retry_storage_read(read)
        return self._first(result)

    def _audit(self, client, user_id: int | None, event: str, ip_hash: str = "") -> None:
        try:
            client.table("auth_audit_log").insert(
                {"user_id": user_id, "event": event, "ip_hash": ip_hash or None}
            ).execute()
        except Exception:
            pass
        # Keep product-usage telemetry separate from the security audit log.
        # The table is deployed independently, so an older schema must never
        # make authentication or Blend actions fail.
        self.record_activity_event(
            user_id,
            event,
            {"source": "auth_audit"},
        )

    def record_activity_event(
        self,
        user_id: int | None,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort, non-sensitive product activity telemetry.

        This intentionally swallows schema/network errors: analytics must not
        block a user action. Callers should only pass counts, booleans, status
        codes and other bounded metadata—never tokens, passwords or raw URLs.
        """
        if not event_type or user_id is None:
            return
        # A deployment may receive traffic before the optional analytics
        # migration has been applied. Back off after the first schema failure
        # instead of adding a failed Supabase round-trip to every request.
        if time.monotonic() < self._activity_disabled_until:
            return
        payload = metadata if isinstance(metadata, dict) else {}
        try:
            # Prevent an accidental large response/request from becoming an
            # unbounded analytics row.
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if len(encoded) > 4000:
                payload = {"truncated": True}
        except (TypeError, ValueError):
            payload = {"metadata_invalid": True}
        try:
            self._service_client().table("user_activity_events").insert(
                {
                    "user_id": int(user_id) if user_id is not None else None,
                    "event_type": str(event_type)[:80],
                    "metadata": payload,
                }
            ).execute()
        except Exception:
            self._activity_disabled_until = time.monotonic() + 300.0

    def start_registration(
        self,
        username: str,
        password: str,
        profile: ScrapedProfile | None = None,
        *,
        ip_hash: str = "",
    ) -> RegistrationChallenge:
        validate_password(password)
        service = self._service_client()
        existing = self._account_row_by_username(service, username)
        if existing and existing.get("account_status") == "active":
            raise AccountExistsError("Bu Letterboxd kullanıcı adı zaten kayıtlı.")

        auth_user_id = ""
        created_new_auth_user = False
        try:
            if existing and existing.get("auth_user_id"):
                auth_user_id = str(existing["auth_user_id"])
                service.auth.admin.update_user_by_id(
                    auth_user_id,
                    {
                        "password": password,
                        "user_metadata": {"letterboxd_username": username},
                    },
                )
            else:
                created = service.auth.admin.create_user(
                    {
                        "email": self.identity_email(username),
                        "password": password,
                        "email_confirm": True,
                        "user_metadata": {"letterboxd_username": username},
                    }
                )
                auth_user_id = str(created.user.id)
                created_new_auth_user = True
            display_name = (
                profile.display_name
                if profile is not None
                else (existing.get("display_name") if existing else None)
            ) or username
            avatar_url = (
                profile.avatar_url
                if profile is not None
                else (existing.get("avatar_url") if existing else None)
            ) or None
            account_result = service.table("users").upsert(
                {
                    "username": username,
                    "auth_user_id": auth_user_id,
                    "display_name": display_name,
                    "avatar_url": avatar_url,
                    "account_status": "pending_verification",
                    "profile_sync_status": "pending",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="username",
            ).execute()
            account_row = self._first(account_result)
            if account_row is None:
                account_row = self._account_row_by_username(service, username)
            if account_row is None:
                raise RuntimeError("Account row could not be created")

            code = f"MOVIENOTES-{secrets.token_hex(3).upper()}"
            expires = datetime.now(timezone.utc) + timedelta(
                minutes=self.CHALLENGE_TTL_MINUTES
            )
            service.table("auth_challenges").insert(
                {
                    "user_id": account_row["id"],
                    "kind": "register",
                    "code_hash": self.challenge_hash(code),
                    "expires_at": expires.isoformat(),
                }
            ).execute()
            event = "register_restarted" if existing else "register_started"
            self._audit(service, int(account_row["id"]), event, ip_hash)
            return RegistrationChallenge(username, code, expires.isoformat())
        except AccountExistsError:
            raise
        except TransientStorageError:
            # A retryable read can also occur after creating the Supabase Auth
            # identity. Preserve the original cleanup guarantee before the
            # HTTP layer asks the user to retry.
            if auth_user_id and created_new_auth_user:
                try:
                    service.auth.admin.delete_user(auth_user_id)
                except Exception:
                    pass
            raise
        except Exception as exc:
            if auth_user_id and created_new_auth_user:
                try:
                    service.auth.admin.delete_user(auth_user_id)
                except Exception:
                    pass
            raise AuthError("Hesap oluşturulamadı.") from exc

    def _active_challenge(self, client, user_id: int, kind: str) -> dict | None:
        result = (
            client.table("auth_challenges")
            .select("id,code_hash,attempts,expires_at")
            .eq("user_id", user_id)
            .eq("kind", kind)
            .is_("consumed_at", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return self._first(result)

    def verify_ownership(
        self,
        username: str,
        code: str,
        profile: ScrapedProfile,
        *,
        kind: str = "register",
        ip_hash: str = "",
    ) -> Account:
        service = self._service_client()
        row = self._account_row_by_username(service, username)
        if row is None or not row.get("auth_user_id"):
            raise VerificationError("Doğrulama başarısız.")
        challenge = self._active_challenge(service, int(row["id"]), kind)
        if challenge is None:
            raise VerificationError("Doğrulama başarısız.")
        if int(challenge.get("attempts") or 0) >= self.MAX_CHALLENGE_ATTEMPTS:
            raise VerificationError("Doğrulama deneme sınırına ulaşıldı.")
        expires = datetime.fromisoformat(challenge["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise VerificationExpiredError("Doğrulama kodunun süresi doldu.")
        valid_code = hmac.compare_digest(
            challenge["code_hash"], self.challenge_hash(code)
        )
        if not valid_code:
            service.table("auth_challenges").update(
                {"attempts": int(challenge.get("attempts") or 0) + 1}
            ).eq("id", challenge["id"]).execute()
            raise OwnershipProofError("Doğrulama kodu geçersiz.")
        if code.upper() not in profile.bio.upper():
            # Right code, bio not updated yet. Nothing was attempted and
            # nothing failed, so the attempt budget stays untouched — only the
            # message changes, to say which half of the step is missing.
            raise OwnershipPendingError(
                "Kodu Letterboxd biyografinde henüz göremedik. Bio’yu "
                "kaydettikten sonra tekrar dene."
            )

        now = datetime.now(timezone.utc).isoformat()
        service.table("auth_challenges").update({"consumed_at": now}).eq(
            "id", challenge["id"]
        ).execute()
        account_result = service.table("users").update(
            {
                "display_name": profile.display_name,
                "avatar_url": profile.avatar_url,
                "account_status": "active",
                "profile_sync_status": "pending",
                "letterboxd_stats": profile.stats or {},
                "ownership_verified_at": now,
                "updated_at": now,
            }
        ).eq("id", row["id"]).execute()
        updated = self._first(account_result) or self._account_row_by_username(
            service, username
        )
        try:
            service.table("profile_favorites").delete().eq(
                "user_id", row["id"]
            ).execute()
            favorites = [
                {
                    "user_id": int(row["id"]),
                    "position": position,
                    "slug": film.slug,
                    "title": film.title,
                    "release_year": film.year,
                    "poster_url": film.poster_url,
                }
                for position, film in enumerate(profile.favorite_films[:4], start=1)
                if film.slug and film.title
            ]
            if favorites:
                service.table("profile_favorites").insert(favorites).execute()
        except Exception:
            # Ownership is already proven. A transient snapshot write must not
            # turn a valid account into an unrecoverable half-registration;
            # the background profile rebuild will repair favorites.
            pass
        self._audit(service, int(row["id"]), f"{kind}_verified", ip_hash)
        return self._account(updated)

    def defer_ownership_verification(
        self, username: str, code: str, *, ip_hash: str = ""
    ) -> Account:
        """Open a private, provisional session when Letterboxd blocks a bio read.

        The user must still possess the one-time code we issued. The account is
        deliberately kept out of every active/social query until a later bio
        check can promote it, so a temporary upstream block never strands a
        legitimate signup or turns into an ownership bypass.
        """
        service = self._service_client()
        row = self._account_row_by_username(service, username)
        if row is None or not row.get("auth_user_id"):
            raise VerificationError("Doğrulama başarısız.")
        challenge = self._active_challenge(service, int(row["id"]), "register")
        if challenge is None:
            raise VerificationError("Doğrulama başarısız.")
        if int(challenge.get("attempts") or 0) >= self.MAX_CHALLENGE_ATTEMPTS:
            raise VerificationError("Doğrulama deneme sınırına ulaşıldı.")
        expires = datetime.fromisoformat(challenge["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise VerificationExpiredError("Doğrulama kodunun süresi doldu.")
        if not hmac.compare_digest(challenge["code_hash"], self.challenge_hash(code)):
            service.table("auth_challenges").update(
                {"attempts": int(challenge.get("attempts") or 0) + 1}
            ).eq("id", challenge["id"]).execute()
            raise OwnershipProofError("Doğrulama kodu geçersiz.")

        now = datetime.now(timezone.utc).isoformat()
        updated = self._first(
            service.table("users").update(
                {
                    "account_status": "verification_deferred",
                    "profile_sync_status": "pending",
                    "updated_at": now,
                }
            ).eq("id", row["id"]).execute()
        ) or self._account_row_by_username(service, username)
        if updated is None:
            raise TransientStorageError("Doğrulama durumu kaydedilemedi.")
        self._audit(service, int(row["id"]), "register_verification_deferred", ip_hash)
        return self._account(updated)

    def start_password_reset(
        self, username: str, *, ip_hash: str = ""
    ) -> RegistrationChallenge:
        service = self._service_client()
        row = self._account_row_by_username(service, username)
        if row is None or row.get("account_status") != "active":
            raise InvalidCredentialsError("Hesap bulunamadı.")
        code = f"MOVIENOTES-{secrets.token_hex(3).upper()}"
        expires = datetime.now(timezone.utc) + timedelta(
            minutes=self.CHALLENGE_TTL_MINUTES
        )
        service.table("auth_challenges").insert(
            {
                "user_id": row["id"],
                "kind": "password_reset",
                "code_hash": self.challenge_hash(code),
                "expires_at": expires.isoformat(),
            }
        ).execute()
        self._audit(service, int(row["id"]), "password_reset_started", ip_hash)
        return RegistrationChallenge(username, code, expires.isoformat())

    def finish_password_reset(
        self,
        username: str,
        code: str,
        new_password: str,
        profile: ScrapedProfile,
        *,
        ip_hash: str = "",
    ) -> None:
        validate_password(new_password)
        account = self.verify_ownership(
            username, code, profile, kind="password_reset", ip_hash=ip_hash
        )
        service = self._service_client()
        service.auth.admin.update_user_by_id(
            account.auth_user_id, {"password": new_password}
        )
        self._audit(service, account.id, "password_reset_completed", ip_hash)

    def login(self, username: str, password: str, *, ip_hash: str = "") -> AuthSession:
        service = self._service_client()
        row = self._account_row_by_username(service, username)
        try:
            response = self._auth_client().auth.sign_in_with_password(
                {"email": self.identity_email(username), "password": password}
            )
            if (
                response.session is None
                or row is None
                or row.get("account_status") not in {"active", "verification_deferred"}
            ):
                raise InvalidCredentialsError("Kullanıcı adı veya parola hatalı.")
        except InvalidCredentialsError:
            self._audit(
                service, int(row["id"]) if row else None, "login_failed", ip_hash
            )
            raise
        except Exception as exc:
            self._audit(
                service, int(row["id"]) if row else None, "login_failed", ip_hash
            )
            raise InvalidCredentialsError("Kullanıcı adı veya parola hatalı.") from exc
        self._audit(service, int(row["id"]), "login_succeeded", ip_hash)
        return AuthSession(
            account=self._account(row),
            access_token=response.session.access_token,
            refresh_token=response.session.refresh_token,
            expires_in=int(response.session.expires_in or 3600),
        )

    def current_account(self, access_token: str) -> Account:
        def resolve() -> Account:
            response = self._auth_client().auth.get_user(access_token)
            auth_user_id = str(response.user.id)
            service = self._service_client()
            columns = (
                "id,auth_user_id,username,display_name,avatar_url,"
                "account_status,profile_sync_status,onboarding_completed_at,letterboxd_stats"
            )
            result = service.table("users").select(columns).eq(
                "auth_user_id", auth_user_id
            ).in_("account_status", ["active", "verification_deferred"]).limit(1).execute()
            row = self._first(result)
            if row is None:
                raise InvalidCredentialsError("Oturum geçersiz.")
            account = self._account(row)
            # Keep the new preference optional during the schema rollout.
            try:
                locale_row = self._first(
                    service.table("users").select("preferred_locale").eq(
                        "id", account.id
                    ).limit(1).execute()
                ) or {}
                locale = str(locale_row.get("preferred_locale") or "auto").lower()
                account.preferred_locale = locale if locale in {"auto", "tr", "en"} else "auto"
            except Exception:
                pass
            return account

        try:
            # Both Supabase Auth and the account-row lookup are read-only. A
            # short Render/Supabase transport hiccup is not proof that the
            # member's token was revoked, so retry and preserve the session.
            return self._retry_storage_read(resolve)
        except (InvalidCredentialsError, TransientStorageError):
            raise
        except Exception as exc:
            if self._is_transient_storage_error(exc):
                raise TransientStorageError(
                    "Oturum bağlantısı kısa süreli yanıt vermedi."
                ) from exc
            raise InvalidCredentialsError("Oturum geçersiz.") from exc

    def refresh(self, refresh_token: str) -> AuthSession:
        try:
            response = self._auth_client().auth.refresh_session(refresh_token)
            if response.session is None:
                raise InvalidCredentialsError("Oturum yenilenemedi.")
            account = self.current_account(response.session.access_token)
            return AuthSession(
                account=account,
                access_token=response.session.access_token,
                refresh_token=response.session.refresh_token,
                expires_in=int(response.session.expires_in or 3600),
            )
        except (InvalidCredentialsError, TransientStorageError):
            raise
        except Exception as exc:
            if self._is_transient_storage_error(exc):
                raise TransientStorageError(
                    "Oturum yenileme bağlantısı kısa süreli yanıt vermedi."
                ) from exc
            raise InvalidCredentialsError("Oturum yenilenemedi.") from exc

    def revoke(self, access_token: str, refresh_token: str) -> None:
        try:
            auth = self._auth_client().auth
            auth.set_session(access_token, refresh_token)
            auth.sign_out()
        except Exception:
            pass

    def mark_sync_status(self, user_id: int, status: str) -> None:
        self._service_client().table("users").update(
            {
                "profile_sync_status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", user_id).execute()

    def complete_onboarding(self, account: Account) -> str:
        completed_at = datetime.now(timezone.utc).isoformat()
        self._service_client().table("users").update(
            {
                "onboarding_completed_at": completed_at,
                "updated_at": completed_at,
            }
        ).eq("id", account.id).execute()
        return completed_at

    def delete_account(self, account: Account) -> None:
        """Delete the Supabase Auth identity and its cascading profile rows."""
        service = self._service_client()
        service.auth.admin.delete_user(account.auth_user_id)

    def save_profile_snapshot(
        self,
        account: Account,
        profile: ScrapedProfile,
        favorites: list[EnrichedFilm],
        taste: TasteProfileSnapshot,
    ) -> None:
        service = self._service_client()
        service.rpc(
            "save_profile_snapshot",
            {
                "p_user_id": account.id,
                "p_profile": {
                    "display_name": profile.display_name,
                    "avatar_url": profile.avatar_url,
                    "stats": profile.stats or {},
                },
                "p_taste": taste.to_dict(),
                "p_favorites": [
                    {
                        "position": position,
                        "slug": film.slug,
                        "title": film.title,
                        "release_year": film.year,
                        "tmdb_id": film.tmdb_id,
                        "poster_url": film.poster_url,
                    }
                    for position, film in enumerate(favorites[:4], start=1)
                ],
            },
        ).execute()
        # Old installations can add this one column independently of the
        # snapshot RPC rollout. Keep the prose payload separate so a deployed
        # server and a just-migrated database immediately support both
        # languages without requiring the user to replace a long SQL function.
        with contextlib.suppress(Exception):
            service.table("taste_profiles").update({
                "localized_narratives": taste.localized_narratives or {},
            }).eq("user_id", account.id).execute()

    def save_profile_identity_and_favorites(
        self, account: Account, profile: ScrapedProfile, favorites: list[EnrichedFilm]
    ) -> None:
        """Persist a just-scraped profile header and Fav 4 without replacing taste data.

        A member can change Letterboxd favourites independently of their diary.
        Rebuilding the whole snapshot just to reflect that edit used to leave the
        app showing stale favourites until the next long history crawl.
        """
        service = self._service_client()
        now = datetime.now(timezone.utc).isoformat()
        service.table("profile_favorites").delete().eq("user_id", account.id).execute()
        rows = [
            {
                "user_id": account.id,
                "position": position,
                "slug": film.slug,
                "title": film.title,
                "release_year": film.year,
                "tmdb_id": film.tmdb_id,
                "poster_url": film.poster_url,
            }
            for position, film in enumerate(favorites[:4], start=1)
            if film.slug and film.title
        ]
        if rows:
            service.table("profile_favorites").insert(rows).execute()
        service.table("users").update(
            {
                "display_name": profile.display_name,
                "avatar_url": profile.avatar_url,
                "letterboxd_stats": profile.stats or {},
                "profile_synced_at": now,
                "updated_at": now,
                "last_seen_at": now,
            }
        ).eq("id", account.id).execute()

    def get_profile(self, account: Account) -> dict:
        service = self._service_client()
        taste = self._first(
            service.table("taste_profiles")
            .select("*")
            .eq("user_id", account.id)
            .limit(1)
            .execute()
        )
        favorites = (
            service.table("profile_favorites")
            .select("position,slug,title,release_year,tmdb_id,poster_url")
            .eq("user_id", account.id)
            .order("position")
            .execute()
        ).data or []
        account_data = dict(account.__dict__)
        # Keşfet alanı migration'ı henüz uygulanmadıysa mevcut profil akışını
        # bozma; kullanıcı yalnızca varsayılan olarak gizli kalır.
        try:
            visibility = self._first(
                service.table("users").select("discoverable,letter_receiving_enabled,private_account").eq("id", account.id).limit(1).execute()
            ) or {}
            account_data["discoverable"] = bool(visibility.get("discoverable", True))
            account_data["letter_receiving_enabled"] = bool(visibility.get("letter_receiving_enabled", False))
            account_data["private_account"] = bool(visibility.get("private_account", False))
        except Exception:
            pass
        # The language column was introduced after authentication.  Keep this
        # separate from the social-preference query so a not-yet-migrated
        # database never prevents a member from opening their profile.
        try:
            language = self._first(
                service.table("users").select("preferred_locale").eq("id", account.id).limit(1).execute()
            ) or {}
            locale = str(language.get("preferred_locale") or "auto").lower()
            account_data["preferred_locale"] = locale if locale in {"auto", "tr", "en"} else "auto"
        except Exception:
            pass
        return {
            "account": account_data,
            "taste": taste,
            "favorite_films": favorites,
        }

    def set_discoverable(self, account: Account, visible: bool) -> bool:
        """Opt a user in/out of the authenticated Sinefil Sineması directory."""
        self._service_client().table("users").update(
            {
                "discoverable": bool(visible),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", account.id).execute()
        return bool(visible)

    def set_preferred_locale(self, account: Account, locale: str) -> str:
        """Persist an explicit UI language or the device-driven `auto` mode."""
        if locale not in {"auto", "tr", "en"}:
            raise ValueError("Unsupported locale.")
        self._service_client().table("users").update(
            {"preferred_locale": locale, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", account.id).execute()
        return locale

    def clear_taste_narrative(self, account: Account) -> None:
        """Remove prose written in the former UI language before regenerating it."""
        self._service_client().table("taste_profiles").update(
            {"analysis": [], "personality": ""}
        ).eq("user_id", account.id).execute()

    def set_private_account(self, account: Account, private: bool) -> bool:
        """Tek anahtar: "kilitli hesap".

        İki ayrı kavram vardı — `private_account` (ayrıntıları yalnız kabul
        edilen takipçiler görür) ve `discoverable` (Sinefil Sineması listesinde
        çıkar). Kullanıcı hangisinin ne yaptığını bilemiyordu. Artık tek bir
        anahtar ikisini birlikte çeviriyor: kilitli hesap listede de çıkmaz,
        yani sosyal alandan tamamen izole kullanılabilir.
        """
        self._service_client().table("users").update(
            {
                "private_account": bool(private),
                "discoverable": not bool(private),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", account.id).execute()
        return bool(private)

    # ── Sinefil Mektupları ─────────────────────────────────────────────
    # Letters are stored as ordinary rows the service can read. The privacy
    # boundary is access control: only the two participants can fetch a letter,
    # blocking deletes the thread, and the admin report counts letters without
    # ever selecting a body.
    def set_letter_receiving(self, account: Account, enabled: bool) -> bool:
        self._service_client().table("users").update(
            {"letter_receiving_enabled": bool(enabled), "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", account.id).execute()
        return bool(enabled)

    def letter_receiving_status(self, account: Account) -> bool:
        """Read the live setting without making session identity depend on a migration."""
        try:
            row = self._first(
                self._service_client().table("users").select(
                    "letter_receiving_enabled"
                ).eq("id", account.id).limit(1).execute()
            ) or {}
            return bool(row.get("letter_receiving_enabled", False))
        except Exception:
            # The caller still has the account snapshot as a safe fallback.
            return bool(account.letter_receiving_enabled)

    def get_letter_recipient(self, account: Account, username: str) -> dict:
        service = self._service_client()
        recipient = self._first(
            service.table("users").select(
                "id,username,display_name,avatar_url,letter_receiving_enabled,account_status"
            ).eq("username", username).limit(1).execute()
        )
        if not recipient or recipient.get("account_status") != "active" or not recipient.get("letter_receiving_enabled"):
            raise BlendServiceError("letter_recipient_unavailable")
        blocks = service.table("user_blocks").select("blocker_user_id").or_(
            f"and(blocker_user_id.eq.{account.id},blocked_user_id.eq.{recipient['id']}),"
            f"and(blocker_user_id.eq.{recipient['id']},blocked_user_id.eq.{account.id})"
        ).limit(1).execute().data or []
        if blocks:
            raise BlendServiceError("letter_recipient_unavailable")
        return {"username": recipient["username"], "display_name": recipient.get("display_name") or recipient["username"],
                "avatar_url": recipient.get("avatar_url") or ""}

    def list_letterable_followers(self, account: Account, *, limit: int = 100) -> list[dict]:
        """Return followers who currently accept letters.

        This is deliberately a small, relationship-scoped directory rather
        than a general people search: the mobile compose shortcut should not
        turn the letter inbox into another public discovery surface.
        """
        service = self._service_client()
        try:
            rows = service.table("follows").select("follower_id").eq(
                "followee_id", account.id
            ).eq("status", "accepted").order("created_at", desc=True).limit(limit).execute().data or []
        except Exception:
            return []
        ids = {int(row["follower_id"]) for row in rows} - self._blocked_ids(account.id)
        if not ids:
            return []
        try:
            users = service.table("users").select(
                "id,username,display_name,avatar_url,letter_receiving_enabled,account_status"
            ).in_("id", list(ids)).eq("account_status", "active").eq(
                "letter_receiving_enabled", True
            ).execute().data or []
        except Exception:
            return []
        by_id = {int(user["id"]): user for user in users}
        return [
            {
                "username": user["username"],
                "display_name": user.get("display_name") or user["username"],
                "avatar_url": user.get("avatar_url") or "",
            }
            for row in rows
            if (user := by_id.get(int(row["follower_id"])))
        ]

    def social_stats(self, account: Account) -> dict:
        """Small own-profile payload used by the profile's social counters."""
        return {
            "followers": self._accepted_follow_count("followee_id", account.id),
            "following": self._accepted_follow_count("follower_id", account.id),
        }

    def send_letter(
        self, account: Account, recipient_username: str, body: str, film: dict | None = None
    ) -> str:
        text = str(body or "").strip()
        if not 1 <= len(text) <= 600:
            raise BlendServiceError("invalid_letter_body")
        gift = self._letter_film_payload(film)
        service = self._service_client()
        try:
            result = service.rpc("send_cinephile_letter", {
                "p_sender_user_id": account.id,
                "p_recipient_username": recipient_username,
                "p_body": text,
                # Keep the durable letter write independent from optional film
                # metadata. Some deployed PostgREST versions are fragile when
                # JSONB RPC input carries a poster URL; a gift must never make
                # the actual letter unsendable.
                "p_film": None,
            }).execute()
            letter_id = str(self._rpc_value(result))
        except Exception as exc:
            message = str(exc)
            known = (
                "letter_sender_closed",
                "letter_recipient_unavailable",
                "letter_send_cooldown",
                "letter_blocked",
                "invalid_letter_body",
            )
            code = next((item for item in known if item in message), "")
            if code:
                raise BlendServiceError(code) from exc
            # A pre-existing database function used the unsupported PostgreSQL
            # overload ``pg_advisory_xact_lock(bigint, bigint)``. Keep letters
            # working while that database migration rolls out, but retain all
            # of the same recipient, block and per-pair 24-hour checks.
            try:
                letter_id = self._send_letter_write_fallback(
                    service, account, recipient_username, text, gift
                )
            except BlendServiceError:
                raise
            except Exception as fallback_exc:
                logger.exception(
                    "Letter send failed for sender=%s recipient=%s; RPC error: %s",
                    account.id, recipient_username, message,
                )
                raise BlendServiceError("letter_send_failed") from fallback_exc
        try:
            if gift:
                # The letter has already passed all pair/cooldown/block checks.
                # Attachment storage is best-effort so a malformed old schema
                # cannot turn a successfully written private letter into 503.
                with contextlib.suppress(Exception):
                    service.table("cinephile_letters").update({"film": gift}).eq(
                        "id", letter_id
                    ).eq("sender_user_id", account.id).execute()
            recipient = self._first(service.table("users").select("id").eq(
                "username", recipient_username
            ).limit(1).execute()) or {}
            if recipient.get("id"):
                self.notify(
                    int(recipient["id"]), "letter", account.id,
                    event_key=f"letter:{letter_id}",
                )
            return letter_id
        except Exception as exc:
            # The letter itself was already stored. A follow-up metadata or
            # notification problem must not tell its sender that it failed.
            logger.exception("Letter follow-up failed for letter=%s", letter_id)
            return letter_id

    def _send_letter_write_fallback(
        self,
        service,
        account: Account,
        recipient_username: str,
        body: str,
        film: dict | None,
    ) -> str:
        """Compatibility write for an unavailable legacy RPC.

        This path is deliberately narrow: it is used only after an unexpected
        RPC infrastructure error, and mirrors the policy checks in the SQL
        function. The corrected SQL function remains the race-safe primary.
        """
        sender = self._first(
            service.table("users").select("id,account_status,letter_receiving_enabled").eq(
                "id", account.id
            ).limit(1).execute()
        ) or {}
        if sender.get("account_status") != "active" or not sender.get("letter_receiving_enabled"):
            raise BlendServiceError("letter_sender_closed")

        username = str(recipient_username or "").strip().lstrip("@").lower()
        recipient = self._first(
            service.table("users").select("id,account_status,letter_receiving_enabled").eq(
                "username", username
            ).limit(1).execute()
        ) or {}
        recipient_id = recipient.get("id")
        if (
            not recipient_id
            or int(recipient_id) == account.id
            or recipient.get("account_status") != "active"
            or not recipient.get("letter_receiving_enabled")
        ):
            raise BlendServiceError("letter_recipient_unavailable")

        blocks = service.table("user_blocks").select("blocker_user_id").or_(
            f"and(blocker_user_id.eq.{account.id},blocked_user_id.eq.{recipient_id}),"
            f"and(blocker_user_id.eq.{recipient_id},blocked_user_id.eq.{account.id})"
        ).limit(1).execute().data or []
        if blocks:
            raise BlendServiceError("letter_blocked")

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        recent = service.table("cinephile_letters").select("id").eq(
            "sender_user_id", account.id
        ).eq("recipient_user_id", recipient_id).gte("created_at", cutoff).limit(1).execute().data or []
        if recent:
            raise BlendServiceError("letter_send_cooldown")

        created = self._first(service.table("cinephile_letters").insert({
            "sender_user_id": account.id,
            "recipient_user_id": recipient_id,
            "body": body,
            "film": film,
        }).execute()) or {}
        if not created.get("id"):
            raise RuntimeError("letter fallback insert did not return an id")
        return str(created["id"])

    @staticmethod
    def _letter_film_payload(film: dict | None) -> dict | None:
        """Keep only the fields the letter card renders, bounded in size."""
        if not isinstance(film, dict):
            return None
        slug = str(film.get("slug") or film.get("film_slug") or "")[:200]
        title = str(film.get("title") or "")[:200]
        if not slug or not title:
            return None
        year = film.get("year") or film.get("release_year")
        try:
            year = int(year) if year is not None else None
        except (TypeError, ValueError):
            year = None
        return {
            "slug": slug,
            "title": title,
            "year": year,
            "poster_url": str(film.get("poster_url") or "")[:500] or None,
        }

    def list_letters(self, account: Account) -> list[dict]:
        service = self._service_client()
        rows = service.table("cinephile_letters").select(
            "id,sender_user_id,recipient_user_id,body,film,ciphertext,created_at,read_at"
        ).or_(f"sender_user_id.eq.{account.id},recipient_user_id.eq.{account.id}").order("created_at", desc=True).limit(100).execute().data or []
        peers = self._accounts_by_id(service, {
            int(row["recipient_user_id"] if int(row["sender_user_id"]) == account.id else row["sender_user_id"])
            for row in rows
        })
        return [{
            "id": row["id"], "direction": "sent" if int(row["sender_user_id"]) == account.id else "received",
            "peer": peers.get(int(row["recipient_user_id"] if int(row["sender_user_id"]) == account.id else row["sender_user_id"])),
            "body": row.get("body") or "",
            "film": row.get("film") or None,
            # Rows written under the old device-key design can no longer be
            # opened by anyone; the UI says so instead of showing an empty card.
            "legacy_encrypted": not (row.get("body") or "") and bool(row.get("ciphertext")),
            "created_at": row["created_at"], "read_at": row.get("read_at"),
        } for row in rows]

    def delete_sent_letter(self, account: Account, letter_id: str) -> bool:
        """Recall one sent letter for both participants.

        A letter is one shared row, not a per-device copy. Deleting that row
        keeps the two inboxes in sync while preventing recipients from
        removing messages they did not send.
        """
        removed = self._service_client().table("cinephile_letters").delete().eq(
            "id", letter_id
        ).eq("sender_user_id", account.id).execute().data or []
        return bool(removed)

    def purge_legacy_letters(self, account: Account) -> int:
        """Delete only unreadable device-key rows involving this account.

        Modern letters have a server-side body and are intentionally left alone;
        this is a narrow repair for rows that no device can decrypt anymore.
        """
        service = self._service_client()
        rows = service.table("cinephile_letters").select("id,body,ciphertext").or_(
            f"sender_user_id.eq.{account.id},recipient_user_id.eq.{account.id}"
        ).execute().data or []
        legacy_ids = [
            str(row["id"]) for row in rows
            if not (row.get("body") or "") and row.get("ciphertext")
        ]
        if not legacy_ids:
            return 0
        result = service.table("cinephile_letters").delete().in_("id", legacy_ids).execute()
        # PostgREST may be configured to return a minimal delete response.
        # The target IDs were already ownership-scoped above, so that is still
        # the reliable count to report to the signed-in person.
        return len(result.data or legacy_ids)

    def mark_letter_read(self, account: Account, letter_id: str) -> bool:
        result = self._service_client().table("cinephile_letters").update(
            {"read_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", letter_id).eq("recipient_user_id", account.id).is_("read_at", "null").execute()
        return bool(result.data)

    def count_unread_letters(self, account: Account) -> int:
        rows = self._service_client().table("cinephile_letters").select("id").eq(
            "recipient_user_id", account.id).is_("read_at", "null").execute().data or []
        return len(rows)

    def letter_send_status(self, account: Account, recipient_username: str = "") -> dict:
        """Return the 24h cooldown for one recipient, never letter content."""
        username = str(recipient_username or "").strip().lstrip("@").lower()
        query = (
            self._service_client().table("cinephile_letters").select("created_at")
            .eq("sender_user_id", account.id)
        )
        if username:
            recipient = self._first(
                self._service_client().table("users").select("id").eq("username", username).limit(1).execute()
            )
            if not recipient:
                return {"can_send": True, "seconds_remaining": 0, "next_send_at": None, "recipient_username": username}
            query = query.eq("recipient_user_id", recipient["id"])
        row = self._first(query.order("created_at", desc=True).limit(1).execute())
        if not row or not row.get("created_at"):
            return {"can_send": True, "seconds_remaining": 0, "next_send_at": None, "recipient_username": username}
        try:
            sent_at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            next_at = sent_at + timedelta(hours=24)
            remaining = max(0, int((next_at - datetime.now(timezone.utc)).total_seconds()))
            return {"can_send": remaining == 0, "seconds_remaining": remaining, "next_send_at": next_at.isoformat(), "recipient_username": username}
        except ValueError:
            return {"can_send": False, "seconds_remaining": 60, "next_send_at": None, "recipient_username": username}

    @staticmethod
    def _overlap(left, right) -> set[str]:
        return {
            str(value).strip().casefold()
            for value in (left or [])
            if isinstance(value, str) and value.strip()
        } & {
            str(value).strip().casefold()
            for value in (right or [])
            if isinstance(value, str) and value.strip()
        }

    def list_sinefil_cards(self, account: Account, query: str = "") -> list[dict]:
        """Build safe, lightweight discovery cards without scraping or LLM calls."""
        service = self._service_client()
        viewer = self.get_profile(account)
        viewer_favorites = viewer.get("favorite_films") or []
        viewer_fav_titles = {
            str(row.get("slug") or "").lower(): str(row.get("title") or "")
            for row in viewer_favorites if row.get("slug")
        }
        viewer_fav = set(viewer_fav_titles)
        viewer_taste = viewer.get("taste") or {}

        candidates_query = (
            service.table("users")
            .select("id,username,display_name,avatar_url,letter_receiving_enabled,private_account")
            .eq("account_status", "active")
            .eq("profile_sync_status", "ready")
            .neq("id", account.id)
            .order("username")
            .limit(250)
        )
        if query:
            candidates_query = candidates_query.ilike("username", f"%{query}%")
        candidates = candidates_query.execute().data or []
        if not candidates:
            return []

        block_rows = (
            service.table("user_blocks")
            .select("blocker_user_id,blocked_user_id")
            .or_(f"blocker_user_id.eq.{account.id},blocked_user_id.eq.{account.id}")
            .execute()
        ).data or []
        blocked = {
            int(row["blocked_user_id"])
            if int(row["blocker_user_id"]) == account.id
            else int(row["blocker_user_id"])
            for row in block_rows
        }
        candidates = [row for row in candidates if int(row["id"]) not in blocked]
        ids = [int(row["id"]) for row in candidates]
        if not ids:
            return []
        follow_states = self.follow_state(account, set(ids))
        favorites_rows = (
            service.table("profile_favorites")
            .select("user_id,position,slug,title,release_year,poster_url")
            .in_("user_id", ids)
            .order("position")
            .execute()
        ).data or []
        taste_rows = (
            service.table("taste_profiles")
            .select("user_id,top_directors,top_genres,top_keywords")
            .in_("user_id", ids)
            .execute()
        ).data or []
        favorites_by_user: dict[int, list[dict]] = {}
        for row in favorites_rows:
            favorites_by_user.setdefault(int(row["user_id"]), []).append(row)
        taste_by_user = {int(row["user_id"]): row for row in taste_rows}
        semantic_scores, semantic_coverage = taste_document_scores(
            viewer_taste,
            [taste_by_user.get(int(candidate["id"]), {}) for candidate in candidates],
        )
        semantic_by_user = {
            int(candidate["id"]): semantic_scores[index]
            for index, candidate in enumerate(candidates)
        }

        cards: list[dict] = []
        for candidate in candidates:
            user_id = int(candidate["id"])
            favorites = favorites_by_user.get(user_id, [])[:4]
            candidate_fav_titles = {
                str(row.get("slug") or "").lower(): str(row.get("title") or "")
                for row in favorites if row.get("slug")
            }
            candidate_fav = set(candidate_fav_titles)
            same_fav4 = viewer_fav & candidate_fav
            taste = taste_by_user.get(user_id, {})
            directors = self._overlap(viewer_taste.get("top_directors"), taste.get("top_directors"))
            genres = self._overlap(viewer_taste.get("top_genres"), taste.get("top_genres"))
            keywords = self._overlap(viewer_taste.get("top_keywords"), taste.get("top_keywords"))
            semantic_score = semantic_by_user.get(user_id, 0.0)
            semantic_match = semantic_coverage >= 0.30 and semantic_score >= 0.62
            score = sinefil_match_score(
                shared_fav4=len(same_fav4),
                shared_directors=len(directors),
                shared_genres=len(genres),
                shared_keywords=len(keywords),
                semantic_score=semantic_score,
                semantic_coverage=semantic_coverage,
            )
            shared_titles: list[str] = []
            for slug in list(same_fav4):
                title = viewer_fav_titles.get(slug)
                if title and title not in shared_titles:
                    shared_titles.append(title)
            has_favorite_match = bool(same_fav4)
            cards.append({
                "username": candidate["username"],
                "display_name": candidate.get("display_name") or candidate["username"],
                "avatar_url": candidate.get("avatar_url") or "",
                "favorites": [
                    {
                        "slug": row.get("slug") or "",
                        "title": row.get("title") or "",
                        "release_year": row.get("release_year"),
                        "poster_url": row.get("poster_url") or "",
                    }
                    for row in favorites
                ],
                "match_score": score,
                "has_favorite_match": has_favorite_match,
                "semantic_match": semantic_match,
                "shared_titles": shared_titles[:3],
                # Kartın altındaki tek satır: neden bu kişi çıktı. Ortak favori
                # yoksa yönetmen, o da yoksa "birbirinize önerecek çok film".
                "match_note": (
                    "Film zevkiniz benziyor"
                    if has_favorite_match
                    else ("Benzer yönetmenleri beğeniyorsunuz" if directors else (
                        "Benzer film dillerine dönüyorsunuz" if semantic_match
                        else "Birbirinize önerecek çok filminiz var"
                    ))
                ),
                "letters_open": bool(candidate.get("letter_receiving_enabled", False)),
                "private_account": bool(candidate.get("private_account", False)),
                "follow_status": follow_states.get(user_id, "none"),
            })
        return sorted(cards, key=lambda card: (-int(card["has_favorite_match"]), -card["match_score"], card["username"]))

    def sinefil_personality(self, account: Account, username: str) -> str:
        """Return only the opted-in profile's saved Fav-4 read."""
        row = self._first(
            self._service_client().table("users").select("id,private_account,account_status").eq(
                "username", username
            ).limit(1).execute()
        ) or {}
        if (
            not row
            or row.get("account_status") != "active"
            or int(row["id"]) not in self._visible_author_ids(account, {int(row["id"])})
        ):
            raise BlendServiceError("recipient_not_found")
        taste = self._first(
            self._service_client().table("taste_profiles").select("personality").eq("user_id", row.get("id")).limit(1).execute()
        ) or {}
        return str(taste.get("personality") or "")

    # ── Watched-film lookups ───────────────────────────────────────────────
    _WATCHED_PICK_COLS = (
        "film_slug,title,release_year,director,user_rating,poster_url,tmdb_id"
    )

    @staticmethod
    def _film_row(row: dict) -> dict:
        return {
            "slug": row.get("film_slug") or "",
            "title": row.get("title") or "",
            "director": row.get("director") or "",
            "year": row.get("release_year"),
            "user_rating": row.get("user_rating"),
            "poster_url": row.get("poster_url") or "",
            "tmdb_id": row.get("tmdb_id"),
        }

    def watched_films_by_slugs(self, user_id: int, slugs: list[str]) -> dict[str, dict]:
        if not slugs:
            return {}
        rows = (
            self._service_client()
            .table("user_watched_films")
            .select(self._WATCHED_PICK_COLS)
            .eq("user_id", user_id)
            .in_("film_slug", list(slugs)[:50])
            .execute()
        ).data or []
        return {r["film_slug"]: self._film_row(r) for r in rows if r.get("film_slug")}

    def watched_film_by_slug(self, user_id: int, slug: str) -> dict | None:
        def read():
            return (
                self._service_client()
                .table("user_watched_films")
                .select(self._WATCHED_PICK_COLS)
                .eq("user_id", user_id)
                .eq("film_slug", slug)
                .eq("is_active", True)
                .limit(1)
                .execute()
            )

        rows = self._retry_storage_read(read).data or []
        return self._film_row(rows[0]) if rows else None

    def list_recent_watched(self, user_id: int, limit: int = 10) -> list[dict]:
        rows = (
            self._service_client()
            .table("user_watched_films")
            .select(self._WATCHED_PICK_COLS)
            .eq("user_id", user_id)
            .eq("is_active", True)
            .order("watched_rank", nullsfirst=False)
            .limit(limit)
            .execute()
        ).data or []
        return [self._film_row(r) for r in rows]

    def list_watched_for_picker(
        self, user_id: int, query: str = "", limit: int = 60
    ) -> list[dict]:
        builder = (
            self._service_client()
            .table("user_watched_films")
            .select(self._WATCHED_PICK_COLS)
            .eq("user_id", user_id)
            .eq("is_active", True)
        )
        if query:
            builder = builder.ilike("title", f"%{query}%")
        rows = (
            builder.order("user_rating", desc=True, nullsfirst=False)
            .order("watched_rank")
            .limit(limit)
            .execute()
        ).data or []
        return [self._film_row(r) for r in rows]

    # ── Full-history background sync ──────────────────────────────────────
    def get_sync_job(self, user_id: int) -> dict | None:
        def read():
            return (
                self._service_client()
                .table("profile_sync_jobs")
                .select("*")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

        return self._first(self._retry_storage_read(read))

    def resumable_sync_accounts(self, limit: int = 3) -> list[tuple[Account, dict]]:
        """Return a small, service-only batch of unfinished profile imports.

        The web worker owns the retry loop, so a profile import must not depend
        on the member reopening the PWA after a temporary Letterboxd block.
        ``profile_sync.job_is_resumable`` remains the authority for leases and
        backoff; this method only supplies candidate rows.
        """
        service = self._service_client()

        def read_jobs():
            return (
                service.table("profile_sync_jobs")
                .select("user_id,state,phase,scope,heartbeat_at,backoff_until,lease_expires_at")
                .in_("state", ["queued", "running", "failed"])
                .order("updated_at")
                .limit(max(1, min(int(limit), 10)))
                .execute()
            )

        jobs = self._retry_storage_read(read_jobs).data or []
        user_ids = [int(job["user_id"]) for job in jobs if job.get("user_id")]
        if not user_ids:
            return []
        columns = (
            "id,auth_user_id,username,display_name,avatar_url,account_status,"
            "profile_sync_status,onboarding_completed_at,letterboxd_stats"
        )

        def read_accounts():
            return (
                service.table("users")
                .select(columns)
                .in_("id", user_ids)
                .in_("account_status", ["active", "verification_deferred"])
                .execute()
            )

        accounts = {
            int(row["id"]): self._account(row)
            for row in (self._retry_storage_read(read_accounts).data or [])
        }
        return [
            (accounts[int(job["user_id"])], job)
            for job in jobs
            if int(job["user_id"]) in accounts
        ]

    def upsert_sync_job(self, user_id: int, **fields) -> dict:
        payload = {
            "user_id": user_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        def write():
            return (
                self._service_client()
                .table("profile_sync_jobs")
                .upsert(payload, on_conflict="user_id")
                .execute()
            )

        row = self._first(self._retry_storage_operation(write))
        return row or payload

    def touch_sync_job(
        self, user_id: int, *, owned_by: str | None = None, **fields
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        def write():
            query = self._service_client().table("profile_sync_jobs").update(
                {"heartbeat_at": now, "updated_at": now, **fields}
            ).eq("user_id", user_id)
            if owned_by:
                query = query.eq("lease_token", owned_by)
            return query.execute()

        result = self._retry_storage_operation(write)
        return bool(result.data)

    def claim_sync_job(
        self, user_id: int, lease_token: str, lease_seconds: int = 360
    ) -> bool:
        result = self._service_client().rpc(
            "claim_profile_sync_job",
            {
                "p_user_id": user_id,
                "p_lease_token": lease_token,
                "p_lease_seconds": lease_seconds,
            },
        ).execute()
        return bool(self._rpc_value(result))

    def finalize_sync_run(self, user_id: int, sync_run_id: str) -> int:
        result = self._service_client().rpc(
            "finalize_profile_sync_run",
            {"p_user_id": user_id, "p_sync_run_id": sync_run_id},
        ).execute()
        try:
            return int(self._rpc_value(result) or 0)
        except (TypeError, ValueError):
            return 0

    def save_watched_films(self, user_id: int, films: list[dict]) -> int:
        """Atomic batch upsert into user_watched_films via the SQL guard."""
        if not films:
            return 0
        result = self._retry_storage_operation(
            lambda: self._service_client().rpc(
                "upsert_watched_films",
                {"p_user_id": user_id, "p_films": films},
            ).execute()
        )
        # Every public film observation feeds the account-independent catalog.
        # Keeping promotion at this boundary means account deletion can remove
        # personal ratings/history without discarding reusable film metadata.
        self.save_film_posters(
            [
                {
                    "slug": row.get("slug") or row.get("film_slug"),
                    "poster_url": row.get("poster_url"),
                    "poster_resolver_url": row.get("poster_resolver_url"),
                    "tmdb_id": row.get("tmdb_id"),
                    "title": row.get("title") or "",
                    "release_year": row.get("release_year") or row.get("year"),
                    "overview": row.get("overview") or "",
                    "director": row.get("director") or "",
                    "genres": row.get("genres") or [],
                    "keywords": row.get("keywords") or [],
                    "vote_average": row.get("vote_average") or 0,
                    "matched": bool(row.get("tmdb_id") or row.get("matched")),
                    "details_loaded": bool(row.get("details_loaded")),
                }
                for row in films
                if row.get("slug") or row.get("film_slug")
            ]
        )
        try:
            return int(result.data)
        except (TypeError, ValueError):
            return 0

    def _paginate(self, table: str, columns: str, user_id: int, *, order: str | None = None):
        service = self._service_client()
        page = 1000
        offset = 0
        out: list[dict] = []
        while True:
            query = service.table(table).select(columns).eq("user_id", user_id)
            if order:
                query = query.order(order)
            rows = (query.range(offset, offset + page - 1).execute()).data or []
            out.extend(rows)
            if len(rows) < page:
                return out
            offset += page

    def get_watched_films(self, user_id: int) -> list[dict]:
        service = self._service_client()
        page = 1000
        offset = 0
        out: list[dict] = []
        while True:
            def read():
                return (
                    service.table("user_watched_films")
                    .select(
                        "film_slug,title,release_year,tmdb_id,director,genres,keywords,"
                        "user_rating,poster_url,poster_resolver_url,watched_rank,details_loaded"
                    )
                    .eq("user_id", user_id)
                    .eq("is_active", True)
                    .order("watched_rank")
                    .range(offset, offset + page - 1)
                    .execute()
                )

            rows = self._retry_storage_read(read).data or []
            out.extend(rows)
            if len(rows) < page:
                return out
            offset += page

    def get_watched_slugs(self, user_id: int) -> set[str]:
        service = self._service_client()
        page = 1000
        offset = 0
        slugs: set[str] = set()
        while True:
            def read():
                return (
                    service.table("user_watched_films")
                    .select("film_slug")
                    .eq("user_id", user_id)
                    .eq("is_active", True)
                    .range(offset, offset + page - 1)
                    .execute()
                )

            rows = self._retry_storage_read(read).data or []
            slugs.update(row["film_slug"] for row in rows if row.get("film_slug"))
            if len(rows) < page:
                return slugs
            offset += page

    def count_watched_films(self, user_id: int) -> int:
        return len(self.get_watched_slugs(user_id))

    # ── Sinefil Akışı ───────────────────────────────────────────────────
    _POST_COLS = (
        "id,author_id,kind,body,film_slug,tmdb_id,film_title,film_year,payload,"
        "spoiler,reply_to,like_count,reply_count,created_at,source,source_key"
    )

    def import_diary_entries(self, user_id: int, entries: list[dict]) -> int:
        """Günce kayıtlarını akışa düşürür. Döner: eklenen satır sayısı.

        Dört kural:

        * **Bir kez düşer.** `source_key` (RSS guid) üzerindeki tekil indeks
          aynı kaydın ikinci kez eklenmesini engelliyor.
        * **Silinen geri gelmez.** Silme yumuşak: satır `deleted_at` ile durur,
          anahtar dolu kalır, dolayısıyla sonraki taramada da eklenemez.
          Bu yüzden çakışmada *güncelleme değil, atlama* yapıyoruz.
        * **Tarih kaydın kendi tarihi.** `created_at` izlenme günü oluyor, yoksa
          eski bir kayıt akışın tepesine düşerdi.
        * **Yalnız yorumlu kayıt.** Cümlesi olmayan izleme kaydı akışa girmiyor.
        """
        # Yalnız yorum yazılmış kayıtlar akışa giriyor. Puanı olup cümlesi
        # olmayan bir izleme kaydı akışta okunacak bir şey taşımıyor; 130 üyeyle
        # bunlar akışı doldurup gerçek yazıyı görünmez kılıyordu.
        entries = [entry for entry in entries if (entry.get("body") or "").strip()]
        if not entries:
            return 0
        service = self._service_client()
        keys = [entry["source_key"] for entry in entries if entry.get("source_key")]
        seen: set[str] = set()
        for start in range(0, len(keys), 100):
            chunk = keys[start:start + 100]
            rows = service.table("posts").select("source_key").eq(
                "author_id", user_id
            ).in_("source_key", chunk).execute().data or []
            seen.update(row["source_key"] for row in rows)
        fresh = [entry for entry in entries if entry.get("source_key") not in seen]
        if not fresh:
            return 0
        written = 0
        for start in range(0, len(fresh), 50):
            batch = [
                {
                    "author_id": user_id,
                    "kind": "log",
                    "source": "letterboxd",
                    "source_key": entry["source_key"],
                    "body": _clip_review((entry.get("body") or "")),
                    "film_slug": entry["film_slug"],
                    "tmdb_id": entry.get("tmdb_id"),
                    "film_title": entry.get("film_title") or "",
                    "film_year": entry.get("film_year"),
                    "payload": entry.get("payload") or {},
                    "created_at": entry["created_at"],
                }
                for entry in fresh[start:start + 50]
            ]
            try:
                result = service.table("posts").insert(batch).execute()
                written += len(result.data or [])
            except Exception as exc:  # noqa: BLE001
                # Yarış hâlinde tekil indeks tetiklenebilir; tek tek deneyip
                # çakışanları atlıyoruz.
                logger.warning("diary import batch failed, retrying one by one: %s", exc)
                for row in batch:
                    with contextlib.suppress(Exception):
                        service.table("posts").insert(row).execute()
                        written += 1
        return written

    def film_overview_for_community(self, film_slug: str) -> dict:
        """Bir filmin topluluk künyesi: kaç üye izlemiş, ortalama kaç puan.

        Puan ortalaması yalnız gerçekten puanlanmış satırlardan hesaplanıyor
        (`rating_observed`), yoksa puansız izlemeler ortalamayı aşağı çekerdi.
        """
        service = self._service_client()
        blank = {"watched_count": 0, "average": None, "rated_count": 0}
        if not film_slug:
            return blank
        try:
            rows = service.table("user_watched_films").select(
                "user_rating,rating_observed"
            ).eq("film_slug", film_slug).limit(2000).execute().data or []
        except Exception:
            return blank
        rated = [
            float(row["user_rating"]) for row in rows
            if row.get("rating_observed") and row.get("user_rating")
        ]
        return {
            "watched_count": len(rows),
            "rated_count": len(rated),
            "average": round(sum(rated) / len(rated), 2) if rated else None,
        }

    def watched_film_for(self, account: Account, film_slug: str) -> dict:
        """Bu üyenin o filme dair kendi kaydı: izlemiş mi, kaç puan vermiş."""
        if not film_slug:
            return {}
        row = self._first(
            self._service_client().table("user_watched_films").select(
                "user_rating,rating_observed"
            ).eq("user_id", account.id).eq("film_slug", film_slug).limit(1).execute()
        )
        if not row:
            return {"watched": False}
        return {
            "watched": True,
            "rating": row.get("user_rating") if row.get("rating_observed") else None,
        }

    def film_catalog_entry(self, film_slug: str) -> dict:
        if not film_slug:
            return {}
        row = self._first(
            self._service_client().table("film_posters").select(
                "film_slug,title,release_year,poster_url,director"
            ).eq("film_slug", film_slug).limit(1).execute()
        )
        return row or {}

    def diary_sync_candidates(
        self, *, limit: int = 20, min_hours: int = 1, max_hours: int = 24,
    ) -> list[dict]:
        """Sırası gelen üyeler. Hiç taranmamışlar önce.

        Eşik üyeye göre değişiyor: `diary_idle_streak` ardışık kaç taramanın
        boş geçtiğini sayıyor ve eşiği ikiye katlıyor (tavan `max_hours`).
        Yazan üye her saat, yıllardır yazmayan üye günde bir taranıyor.

        Üyelik büyüdükçe önemli olan da bu: koş başına istek sayısı `limit` ile
        sabit, geri çekilme ise o sabit bütçeyi gerçekten yazan üyelere ayırıyor.
        Bütçe yetmediğinde tek sonuç kaydın biraz geç düşmesi — akış penceresi
        yedi gün olduğu için görünürlüğü etkilemiyor.
        """
        service = self._service_client()
        now = datetime.now(timezone.utc)
        try:
            never = service.table("users").select("id,username").eq(
                "account_status", "active"
            ).is_("diary_synced_at", "null").limit(limit).execute().data or []
        except Exception:
            never = []
        if len(never) >= limit:
            return never[:limit]
        try:
            # Havuzu bütçenin birkaç katı tutuyoruz: geri çekilmiş üyeler
            # elendikten sonra da bütçeyi dolduracak kadar aday kalsın.
            pool = service.table("users").select(
                "id,username,diary_synced_at,diary_idle_streak"
            ).eq("account_status", "active").not_.is_(
                "diary_synced_at", "null"
            ).order("diary_synced_at").limit(max(limit * 4, 40)).execute().data or []
        except Exception:
            # İkinci sorgunun hatası birincinin sonucunu götürmesin: hiç
            # taranmamış üye, sıranın en başındaki iş.
            return never

        due: list[dict] = []
        for row in pool:
            streak = int(row.get("diary_idle_streak") or 0)
            threshold = min(max(1, min_hours) * (2 ** min(streak, 10)), max(1, max_hours))
            try:
                last = datetime.fromisoformat(str(row["diary_synced_at"]))
            except (TypeError, ValueError):
                due.append(row)
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last >= timedelta(hours=threshold):
                due.append(row)
            if len(due) >= limit - len(never):
                break
        return never + due

    def ready_notification_accounts(self, *, limit: int = 1000) -> list[Account]:
        """Members with enough saved film data for a useful system message.

        This is deliberately a database-only cohort. Scheduled notifications
        must never cause Letterboxd requests or wake a stalled profile import.
        """
        fields = (
            "id,auth_user_id,username,display_name,avatar_url,account_status,"
            "profile_sync_status,onboarding_completed_at,letterboxd_stats,"
            "discoverable,letter_receiving_enabled,private_account"
        )
        try:
            rows = self._service_client().table("users").select(fields).eq(
                "account_status", "active"
            ).eq("profile_sync_status", "ready").not_.is_(
                "auth_user_id", "null"
            ).order("id").limit(max(1, min(int(limit), 2000))).execute().data or []
        except Exception:
            return []
        return [self._account(row) for row in rows]

    def diary_backfill_candidates(self, *, limit: int = 2) -> list[dict]:
        """Arşivi henüz taranmamış üyeler; en erken kaydolan önce.

        Yeni üye kaydolur kaydolmaz değil, uygulamaya girdikten sonra kuyruğa
        giriyor — onboarding yüzlerce sayfalık bir taramayı beklememeli.
        """
        try:
            return self._service_client().table("users").select(
                "id,username,diary_backfill_page"
            ).eq("account_status", "active").is_(
                "diary_backfilled_at", "null"
            ).order("id").limit(max(1, limit)).execute().data or []
        except Exception:
            return []

    def mark_diary_backfill(self, user_id: int, *, page: int, done: bool) -> None:
        """Arşiv taramasının nereye kadar geldiğini saklar.

        Sayfa numarası ilerlemiyorsa (sayfa okunamadı) damga da atılmıyor:
        sonraki koşu aynı yerden yeniden deniyor.
        """
        patch: dict[str, Any] = {}
        if page > 0:
            patch["diary_backfill_page"] = min(page, 32000)
        if done:
            patch["diary_backfilled_at"] = datetime.now(timezone.utc).isoformat()
        if not patch:
            return
        with contextlib.suppress(Exception):
            self._service_client().table("users").update(patch).eq("id", user_id).execute()

    def mark_diary_synced(self, user_id: int, *, wrote: int = 0) -> None:
        """Taramayı damgalar ve boş geçen tarama sayısını günceller.

        Yeni kayıt geldiyse sayaç sıfırlanıyor — üye yazmaya başladıysa sık
        taranmaya geri dönüyor.
        """
        patch: dict[str, Any] = {"diary_synced_at": datetime.now(timezone.utc).isoformat()}
        if wrote:
            patch["diary_idle_streak"] = 0
        with contextlib.suppress(Exception):
            service = self._service_client()
            if not wrote:
                current = self._first(
                    service.table("users").select("diary_idle_streak").eq(
                        "id", user_id
                    ).limit(1).execute()
                )
                patch["diary_idle_streak"] = min(
                    int((current or {}).get("diary_idle_streak") or 0) + 1, 10
                )
            service.table("users").update(patch).eq("id", user_id).execute()

    def create_post(self, account: Account, payload: dict) -> dict:
        row = {
            "author_id": account.id,
            "kind": payload.get("kind") or "note",
            "body": (payload.get("body") or "").strip(),
            "spoiler": bool(payload.get("spoiler")),
        }
        if payload.get("reply_to"):
            row["reply_to"] = payload["reply_to"]
        else:
            # A top-level note without a film is not a thing this product has.
            slug = str(payload.get("film_slug") or "").strip().lower()
            if not slug:
                raise BlendServiceError("post_film_required")
            # The picker is a convenience, never the security boundary. A raw
            # HTTP request must not be able to manufacture a film and poison
            # the community timeline or its trends.
            film = self.watched_film_by_slug(account.id, slug)
            if not film:
                raise BlendServiceError("post_film_not_owned")
            row.update({
                "film_slug": film["slug"],
                "tmdb_id": film.get("tmdb_id"),
                "film_title": film.get("title") or "",
                "film_year": film.get("year"),
            })
        try:
            created = self._first(self._service_client().table("posts").insert(row).execute())
        except Exception as exc:
            # Surfaced as a clear 503 rather than a bare 500: the member must
            # know the note was not saved.
            raise BlendServiceError("post_failed") from exc
        if not created:
            raise BlendServiceError("post_failed")
        return created

    def _hydrate_posts(self, rows: list[dict], viewer_id: int) -> list[dict]:
        """Attach author, poster and the viewer's own like state in one pass."""
        if not rows:
            return []
        service = self._service_client()
        authors = self._accounts_by_id(service, {int(r["author_id"]) for r in rows})
        slugs = [r["film_slug"] for r in rows if r.get("film_slug")]
        posters: dict[str, dict] = {}
        if slugs:
            found = service.table("film_posters").select(
                "film_slug,poster_url,title,release_year,director"
            ).in_("film_slug", list(dict.fromkeys(slugs))).execute().data or []
            posters = {row["film_slug"]: row for row in found}
        liked: set[str] = set()
        ids = [r["id"] for r in rows]
        if ids:
            mine = service.table("post_likes").select("post_id").eq(
                "user_id", viewer_id
            ).in_("post_id", ids).execute().data or []
            liked = {row["post_id"] for row in mine}
        out = []
        for row in rows:
            poster = posters.get(row.get("film_slug") or "", {})
            out.append({
                **row,
                "author": authors.get(int(row["author_id"])),
                "film": {
                    "slug": row.get("film_slug") or "",
                    "title": poster.get("title") or row.get("film_title") or "",
                    "year": poster.get("release_year") or row.get("film_year"),
                    "poster_url": poster.get("poster_url") or "",
                    "director": poster.get("director") or "",
                } if row.get("film_slug") else None,
                "liked": row["id"] in liked,
                "mine": int(row["author_id"]) == viewer_id,
            })
        return out

    def _blocked_ids(self, viewer_id: int) -> set[int]:
        """Both directions: someone I blocked and someone who blocked me."""
        service = self._service_client()
        try:
                rows = service.table("user_blocks").select(
                "blocker_user_id,blocked_user_id"
            ).or_(
                f"blocker_user_id.eq.{viewer_id},blocked_user_id.eq.{viewer_id}"
            ).execute().data or []
        except Exception:
            return set()
        out: set[int] = set()
        for row in rows:
            out.add(int(row["blocker_user_id"]))
            out.add(int(row["blocked_user_id"]))
        out.discard(viewer_id)
        return out

    @staticmethod
    def _cursor_parts(cursor: str, *, sort: str = "recent") -> tuple | None:
        """Validate a composite keyset cursor before it reaches PostgREST."""
        if not cursor:
            return None
        try:
            if sort == "engagement":
                likes, replies, created_at, post_id = cursor.split("|", 3)
                if int(likes) < 0 or int(replies) < 0:
                    raise ValueError
                values = (int(likes), int(replies), created_at, post_id)
            else:
                created_at, post_id = cursor.rsplit("|", 1)
                values = (created_at, post_id)
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            UUID(post_id)
        except (TypeError, ValueError):
            raise BlendServiceError("invalid_cursor") from None
        return values

    @staticmethod
    def _post_cursor(row: dict, *, sort: str = "recent") -> str:
        if sort == "engagement":
            return f"{int(row.get('like_count') or 0)}|{int(row.get('reply_count') or 0)}|{row['created_at']}|{row['id']}"
        return f"{row['created_at']}|{row['id']}"

    def _apply_post_cursor(self, query, cursor: str, *, sort: str = "recent"):
        parsed = self._cursor_parts(cursor, sort=sort)
        if not parsed:
            return query
        if sort == "engagement":
            likes, replies, created_at, post_id = parsed
            return query.or_(
                f"like_count.lt.{likes},"
                f"and(like_count.eq.{likes},reply_count.lt.{replies}),"
                f"and(like_count.eq.{likes},reply_count.eq.{replies},created_at.lt.{created_at}),"
                f"and(like_count.eq.{likes},reply_count.eq.{replies},created_at.eq.{created_at},id.lt.{post_id})"
            )
        created_at, post_id = parsed
        # created_at alone loses or repeats notes written in the same tick.
        return query.or_(
            f"created_at.lt.{created_at},and(created_at.eq.{created_at},id.lt.{post_id})"
        )

    def _visible_author_ids(self, account: Account, author_ids: set[int]) -> set[int]:
        """Apply both block directions and private-account access everywhere."""
        if not author_ids:
            return set()
        service = self._service_client()
        blocked = self._blocked_ids(account.id)
        candidates = author_ids - blocked
        if not candidates:
            return set()
        rows = service.table("users").select("id,account_status,private_account").in_(
            "id", list(candidates)
        ).execute().data or []
        active = {
            int(row["id"]): bool(row.get("private_account"))
            for row in rows if row.get("account_status") == "active"
        }
        visible = {user_id for user_id, private in active.items() if not private or user_id == account.id}
        locked = [user_id for user_id, private in active.items() if private and user_id != account.id]
        if locked:
            accepted = service.table("follows").select("followee_id").eq(
                "follower_id", account.id
            ).eq("status", "accepted").in_("followee_id", locked).execute().data or []
            visible.update(int(row["followee_id"]) for row in accepted)
        return visible

    def _can_view_user(self, account: Account, user_id: int, private: bool) -> bool:
        return user_id in self._visible_author_ids(account, {user_id}) if private or user_id != account.id else True

    def _visible_rows(self, account: Account, rows: list[dict]) -> list[dict]:
        visible = self._visible_author_ids(account, {int(row["author_id"]) for row in rows})
        return [row for row in rows if int(row["author_id"]) in visible]

    def list_feed(
        self, account: Account, *, scope: str = "community",
        cursor: str = "", limit: int = 20, film_slug: str = "", author_username: str = "",
        sort: str = "recent",
    ) -> dict:
        service = self._service_client()
        engagement_sorted = scope == "community" and sort == "engagement"
        followed: set[int] = set()
        if engagement_sorted:
            followed = {
                int(row["followee_id"]) for row in service.table("follows").select("followee_id").eq(
                    "follower_id", account.id
                ).eq("status", "accepted").execute().data or []
            }
        following: list[int] | None = None
        if not film_slug and scope == "following":
            following = [row["followee_id"] for row in (
                service.table("follows").select("followee_id").eq(
                    "follower_id", account.id
                ).eq("status", "accepted").execute().data or [])]
            following.append(account.id)
        elif not film_slug and scope == "mine":
            following = [account.id]
        if not film_slug and author_username:
            username = str(author_username).strip().lstrip("@").lower()
            candidate = self._first(
                service.table("users").select("id").eq("username", username).eq(
                    "account_status", "active"
                ).limit(1).execute()
            )
            candidate_id = int(candidate["id"]) if candidate else None
            if scope == "following" and candidate_id is not None and candidate_id in (following or []):
                following = [candidate_id]
            elif scope == "mine" and candidate_id == account.id:
                following = [account.id]
            else:
                # Never let a UI filter bypass the selected feed scope.
                following = []
        # Pencere yalnızca keşif akışlarında: topluluk ve takip ettiklerin.
        # "Notların" sekmesi, bir üyenin profili ve film sayfası kendi arşivini
        # eksiksiz göstermek zorunda — kullanıcı kendi yazdığını orada arıyor.
        windowed = scope in ("community", "following") and not film_slug
        window_days = (
            FEED_FOLLOWING_WINDOW_DAYS if scope == "following" else FEED_DIARY_WINDOW_DAYS
        )
        window_start = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()
        scanned_cursor = cursor
        visible: list[dict] = []
        exhausted = False
        # A private/blocked cluster must not make the next visible post
        # unreachable. Scan a few bounded batches, then hand the cursor of the
        # last returned visible card back to the caller.
        for _ in range(6):
            query = service.table("posts").select(self._POST_COLS).is_(
                "deleted_at", "null"
            ).is_("reply_to", "null")
            if engagement_sorted:
                query = query.order("like_count", desc=True).order("reply_count", desc=True).order("created_at", desc=True).order("id", desc=True)
            else:
                query = query.order("created_at", desc=True).order("id", desc=True)
            query = query.limit(60)
            query = self._apply_post_cursor(
                query, scanned_cursor, sort="engagement" if engagement_sorted else "recent"
            )
            if film_slug:
                query = query.eq("film_slug", film_slug)
            elif following is not None:
                query = query.in_("author_id", following)
            if windowed:
                # Günce kaydı bu hafta izlenmişse akışta; uygulamada yazılan not
                # her zaman. Pencere `created_at` üzerinden çünkü içe aktarımda
                # o alan izlenme günü oluyor — kaydın kendi tarihi, çekildiği an
                # değil. Filtre veritabanında: sayfa dolusu eski kaydı çekip
                # Python'da elemek, tarama döngüsünü boşa çevirirdi.
                #
                # İmleç de `or_` kullanıyor. İkisinin AND'lendiğini canlı
                # veritabanında ölçtüm (postgrest 2.30): pencere 109, imleç 289,
                # kesişim 102, birlikte 102. Biri diğerini ezseydi akış sessizce
                # yanlış sayfalanırdı.
                query = query.or_(f"source.eq.app,created_at.gte.{window_start}")
            try:
                rows = self._retry_storage_read(query.execute).data or []
            except BlendServiceError:
                raise
            except Exception as exc:
                raise TransientStorageError("feed_unavailable") from exc
            if not rows:
                exhausted = True
                break
            visible.extend(self._visible_rows(account, rows))
            if engagement_sorted:
                # Within equally engaged notes, put the people the viewer chose
                # to follow first. The database order remains deterministic for
                # all other ties and therefore keeps the keyset cursor stable.
                visible.sort(key=lambda row: (
                    -int(row.get("like_count") or 0),
                    -int(row.get("reply_count") or 0),
                    0 if int(row["author_id"]) in followed else 1,
                ))
            if len(visible) > limit:
                break
            if len(rows) < 60:
                exhausted = True
                break
            scanned_cursor = self._post_cursor(
                rows[-1], sort="engagement" if engagement_sorted else "recent"
            )
        page = visible[:limit]
        return {
            "posts": self._hydrate_posts(page, account.id),
            "next_cursor": self._post_cursor(
                page[-1], sort="engagement" if engagement_sorted else "recent"
            ) if page and (len(visible) > limit or not exhausted) else "",
        }

    def search_feed_films(self, account: Account, query_text: str, limit: int = 16) -> list[dict]:
        """Find films that actually have visible community notes."""
        query_text = str(query_text or "").strip()
        if len(query_text) < 2:
            return []
        service = self._service_client()
        try:
            query = service.table("posts").select(
                "author_id,film_slug,film_title,film_year,created_at"
            ).is_("deleted_at", "null").is_("reply_to", "null").not_.is_(
                "film_slug", "null"
            ).ilike("film_title", f"%{query_text}%").order("created_at", desc=True).limit(120)
            rows = self._visible_rows(account, query.execute().data or [])
        except Exception:
            return []
        slugs: list[str] = []
        by_slug: dict[str, dict] = {}
        for row in rows:
            slug = str(row.get("film_slug") or "")
            if slug and slug not in by_slug:
                by_slug[slug] = row
                slugs.append(slug)
            if len(slugs) >= limit:
                break
        posters = self.get_film_assets(slugs)
        return [{
            "slug": slug,
            "title": (posters.get(slug) or {}).get("title") or row.get("film_title") or slug,
            "year": (posters.get(slug) or {}).get("release_year") or row.get("film_year"),
            "poster_url": (posters.get(slug) or {}).get("poster_url") or "",
            "director": (posters.get(slug) or {}).get("director") or "",
        } for slug, row in by_slug.items()]

    def get_post_thread(self, account: Account, post_id: str) -> dict | None:
        service = self._service_client()
        root = self._first(
            service.table("posts").select(self._POST_COLS).eq("id", post_id)
            .is_("deleted_at", "null").limit(1).execute()
        )
        if not root:
            return None
        if not self._visible_rows(account, [root]):
            return None
        replies = service.table("posts").select(self._POST_COLS).eq(
            "reply_to", post_id
        ).is_("deleted_at", "null").order("created_at").limit(100).execute().data or []
        replies = self._visible_rows(account, replies)
        hydrated = self._hydrate_posts([root] + replies, account.id)
        return {"post": hydrated[0], "replies": hydrated[1:]}

    def _count(self, table: str, column: str, value) -> int:
        try:
            result = self._service_client().table(table).select(
                "*", count="exact"
            ).eq(column, value).limit(1).execute()
        except Exception:
            return 0
        return int(result.count or 0)

    def _accepted_follow_count(self, column: str, user_id: int) -> int:
        try:
            result = self._service_client().table("follows").select(
                "*", count="exact"
            ).eq(column, user_id).eq("status", "accepted").limit(1).execute()
        except Exception:
            return 0
        return int(result.count or 0)

    def public_profile(self, account: Account, username: str) -> dict | None:
        """Someone's page: who they are, their tallies, and where the viewer stands.

        Blocks and unknown accounts return None. A locked account still returns
        its small directory identity (so a member can send a follow request),
        but not its personal viewing data.
        """
        service = self._service_client()
        row = self._first(
            service.table("users").select(
                "id,username,display_name,avatar_url,account_status,letterboxd_stats,"
                "letter_receiving_enabled,private_account,created_at"
            ).eq("username", username).limit(1).execute()
        )
        if not row or (row.get("account_status") or "") != "active":
            return None
        user_id = int(row["id"])
        if user_id != account.id and user_id in self._blocked_ids(account.id):
            return None
        can_view = self._can_view_user(account, user_id, bool(row.get("private_account")))

        try:
            notes = service.table("posts").select("*", count="exact").eq(
                "author_id", user_id
            ).is_("deleted_at", "null").is_("reply_to", "null").limit(1).execute()
            note_count = int(notes.count or 0) if can_view else None
        except Exception:
            note_count = 0 if can_view else None
        favorites = []
        if can_view:
            favorites = service.table("profile_favorites").select(
                "position,slug,title,release_year,poster_url"
            ).eq("user_id", user_id).order("position").limit(4).execute().data or []

        follow_state = self.follow_state(account, {user_id}) if user_id != account.id else {}
        follows_you = bool(
            can_view and user_id != account.id
            and (service.table("follows").select("follower_id").eq(
                "follower_id", user_id
            ).eq("followee_id", account.id).eq("status", "accepted").limit(1).execute().data or [])
        )
        return {
            "id": user_id,
            "username": row["username"],
            "display_name": row.get("display_name") or row["username"],
            "avatar_url": row.get("avatar_url") or "",
            "joined_at": row.get("created_at"),
            "letterboxd_stats": (row.get("letterboxd_stats") or {}) if can_view else {},
            "favorites": favorites,
            "note_count": note_count,
            # Counts are a small public profile signal; the underlying member
            # lists are deliberately owner-only (see ``_follow_list`` route).
            "follower_count": self._accepted_follow_count("followee_id", user_id),
            "following_count": self._accepted_follow_count("follower_id", user_id),
            "follow_status": follow_state.get(user_id, "none") if user_id != account.id else "self",
            "following": follow_state.get(user_id) == "accepted",
            "follows_you": follows_you,
            "is_me": user_id == account.id,
            "letter_receiving_enabled": bool(row.get("letter_receiving_enabled")) if can_view else False,
            "private_account": bool(row.get("private_account")),
            "can_view": can_view,
        }

    def list_user_posts(
        self, account: Account, user_id: int, *, cursor: str = "", limit: int = 20,
    ) -> dict:
        service = self._service_client()
        if user_id not in self._visible_author_ids(account, {user_id}):
            return {"posts": [], "next_cursor": ""}
        query = service.table("posts").select(self._POST_COLS).eq(
            "author_id", user_id
        ).is_("deleted_at", "null").is_("reply_to", "null").order(
            "created_at", desc=True
        ).order("id", desc=True).limit(limit + 1)
        query = self._apply_post_cursor(query, cursor)
        try:
            rows = self._retry_storage_read(query.execute).data or []
        except BlendServiceError:
            raise
        except Exception:
            return {"posts": [], "next_cursor": ""}
        page = rows[:limit]
        return {
            "posts": self._hydrate_posts(page, account.id),
            "next_cursor": self._post_cursor(page[-1]) if len(rows) > limit and page else "",
        }

    def list_follow_list(
        self, account: Account, user_id: int, *, kind: str = "followers", limit: int = 100,
    ) -> list[dict]:
        """Who follows this member, or who they follow, with the viewer's own state."""
        service = self._service_client()
        column, other = (
            ("followee_id", "follower_id") if kind == "followers"
            else ("follower_id", "followee_id")
        )
        try:
            rows = service.table("follows").select(other).eq(
                column, user_id
            ).eq("status", "accepted").order("created_at", desc=True).limit(limit).execute().data or []
        except Exception:
            return []
        ids = {int(row[other]) for row in rows} - self._blocked_ids(account.id)
        if not ids:
            return []
        accounts = self._accounts_by_id(service, ids)
        mine = self.follow_state(account, ids)
        return [
            {
                **accounts[int(row[other])],
                "following": mine.get(int(row[other])) == "accepted",
                "is_me": int(row[other]) == account.id,
            }
            for row in rows
            if int(row[other]) in accounts
        ]

    def delete_post(self, account: Account, post_id: str) -> bool:
        updated = self._service_client().table("posts").update(
            {"deleted_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", post_id).eq("author_id", account.id).is_(
            "deleted_at", "null"
        ).execute().data or []
        return bool(updated)

    def set_post_like(self, account: Account, post_id: str, liked: bool) -> int:
        service = self._service_client()
        post = self._first(
            service.table("posts").select("id,author_id,like_count").eq("id", post_id)
            .is_("deleted_at", "null").limit(1).execute()
        )
        if not post:
            raise BlendServiceError("post_not_found")
        if not self._visible_rows(account, [post]):
            raise BlendServiceError("post_not_found")
        if liked:
            existing = service.table("post_likes").select("post_id").eq(
                "post_id", post_id
            ).eq("user_id", account.id).limit(1).execute().data or []
            created = False
            if not existing:
                service.table("post_likes").insert(
                    {"post_id": post_id, "user_id": account.id}
                ).execute()
                created = True
            if created:
                if int(post["author_id"]) != account.id:
                    self.notify(int(post["author_id"]), "like", account.id, post_id,
                                event_key=f"like:{post_id}:{account.id}")
        else:
            service.table("post_likes").delete().eq("post_id", post_id).eq(
                "user_id", account.id
            ).execute()
        fresh = self._first(
            service.table("posts").select("like_count").eq("id", post_id).limit(1).execute()
        )
        return int((fresh or {}).get("like_count") or 0)

    def notify(
        self, user_id: int, kind: str, actor_id: int | None = None,
        post_id: str | None = None, *, event_key: str | None = None,
        push_kind: str | None = None, push_context: dict | None = None,
        push_url: str = "/#/bildirimler",
    ) -> bool:
        """Bir bildirim yazar. `actor_id` yoksa bildirim sistemdendir
        ("bu hafta perdede" gibi). `event_key` verilirse tekil indeks aynı
        olayın ikinci kez bildirilmesini engelliyor."""
        try:
            self._service_client().table("notifications").insert({
                "user_id": user_id, "kind": kind,
                "actor_id": actor_id, "post_id": post_id,
                "event_key": event_key,
            }).execute()
        except Exception:
            # `event_key` aynı olayın ikinci kez teslim edilmesini engeller.
            # Satır yazılmadıysa push da atılmaz; zamanlayıcılar böylece
            # güvenle tekrar çalışabilir.
            return False
        self._send_web_push(
            user_id, push_kind or kind, context=push_context, url=push_url,
        )
        return True

    def upsert_push_subscription(self, account: Account, subscription: dict, user_agent: str = "") -> None:
        endpoint = str(subscription.get("endpoint") or "")
        keys = subscription.get("keys") or {}
        p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
        if not endpoint.startswith("https://") or not p256dh or not auth:
            raise BlendServiceError("invalid_push_subscription")
        self._service_client().table("web_push_subscriptions").upsert({
            "user_id": account.id, "endpoint": endpoint, "p256dh": p256dh, "auth": auth,
            "user_agent": user_agent[:500], "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="endpoint").execute()

    def _push_locale(self, user_id: int) -> str:
        """Background jobs have no device headers; explicit English wins.

        `auto` follows the device in the web UI. A scheduled delivery cannot
        know that device language, so Turkish remains the product default.
        """
        try:
            row = self._first(self._service_client().table("users").select(
                "preferred_locale"
            ).eq("id", user_id).limit(1).execute()) or {}
            return "en" if row.get("preferred_locale") == "en" else "tr"
        except Exception:
            return "tr"

    def _push_message(self, user_id: int, kind: str, context: dict | None = None) -> tuple[str, str]:
        locale = self._push_locale(user_id)
        title, body = _PUSH_COPY.get(kind, {}).get(
            locale, ("Movienotes", "Yeni bir bildirimin var.")
        )
        values = {"title": str((context or {}).get("film_title") or "Bu film")[:120]}
        return title.format(**values), body.format(**values)

    def _send_web_push(
        self, user_id: int, kind: str, *, context: dict | None = None,
        url: str = "/#/bildirimler",
    ) -> None:
        if not self.settings.has_web_push or webpush is None:
            return
        try:
            rows = self._service_client().table("web_push_subscriptions").select(
                "endpoint,p256dh,auth"
            ).eq("user_id", user_id).execute().data or []
        except Exception:
            return
        title, body = self._push_message(user_id, kind, context)
        payload = json.dumps({"title": title, "body": body, "kind": kind, "url": url})
        for row in rows:
            try:
                webpush(
                    subscription_info={"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}},
                    data=payload, vapid_private_key=self.settings.web_push_vapid_private_key,
                    vapid_claims={"sub": self.settings.web_push_vapid_subject}, ttl=120,
                )
            except WebPushException as exc:
                if getattr(exc, "status_code", 0) in (404, 410):
                    with contextlib.suppress(Exception):
                        self._service_client().table("web_push_subscriptions").delete().eq("endpoint", row["endpoint"]).execute()
            except Exception:
                pass

    def list_notifications(self, account: Account, limit: int = 30) -> list[dict]:
        service = self._service_client()
        try:
            rows = service.table("notifications").select(
                "id,kind,actor_id,post_id,event_key,read_at,created_at"
            ).eq("user_id", account.id).order("created_at", desc=True).limit(limit).execute().data or []
        except Exception:
            return []
        blocked = self._blocked_ids(account.id)
        rows = [row for row in rows if not row.get("actor_id") or int(row["actor_id"]) not in blocked]
        actors = self._accounts_by_id(service, {int(r["actor_id"]) for r in rows if r.get("actor_id")})
        # Without the note itself a notification reads "biri notunu beğendi" and
        # the reader has no idea which one.
        post_ids = [r["post_id"] for r in rows if r.get("post_id")]
        posts: dict[str, dict] = {}
        if post_ids:
            found = service.table("posts").select(
                "id,body,film_title,film_slug,reply_to,deleted_at"
            ).in_("id", list(dict.fromkeys(post_ids))).execute().data or []
            posts = {
                row["id"]: {
                    "id": row["id"],
                    "body": row.get("body") or "",
                    "film_title": row.get("film_title") or "",
                    "thread_id": row.get("reply_to") or row["id"],
                }
                for row in found if not row.get("deleted_at")
            }
        return [
            {
                **row,
                "actor": actors.get(int(row["actor_id"]) if row.get("actor_id") else 0),
                "post": posts.get(row.get("post_id") or ""),
            }
            for row in rows
        ]

    def unread_notification_count(self, account: Account) -> int:
        try:
            result = self._service_client().table("notifications").select(
                "id", count="exact"
            ).eq("user_id", account.id).is_("read_at", "null").limit(1).execute()
        except Exception:
            return 0
        return int(result.count or 0)

    def mark_notifications_read(self, account: Account) -> None:
        with contextlib.suppress(Exception):
            self._service_client().table("notifications").update(
                {"read_at": datetime.now(timezone.utc).isoformat()}
            ).eq("user_id", account.id).is_("read_at", "null").execute()

    def set_follow(self, account: Account, username: str, following: bool) -> dict:
        service = self._service_client()
        target = self._first(
            service.table("users").select("id,username,display_name,avatar_url,private_account")
            .eq("username", username).eq("account_status", "active").limit(1).execute()
        )
        if not target:
            raise BlendServiceError("user_not_found")
        if int(target["id"]) == account.id:
            raise BlendServiceError("self_follow")
        if int(target["id"]) in self._blocked_ids(account.id):
            raise BlendServiceError("user_not_found")
        if following:
            existing = self._first(service.table("follows").select("status").eq(
                "follower_id", account.id
            ).eq("followee_id", target["id"]).limit(1).execute())
            if existing:
                status = existing.get("status") or "accepted"
            else:
                status = "pending" if target.get("private_account") else "accepted"
                service.table("follows").insert(
                    {"follower_id": account.id, "followee_id": target["id"], "status": status}
                ).execute()
                self.notify(
                    int(target["id"]), "follow_request" if status == "pending" else "follow",
                    account.id, event_key=f"follow:{account.id}:{target['id']}",
                )
        else:
            service.table("follows").delete().eq("follower_id", account.id).eq(
                "followee_id", target["id"]
            ).execute()
            status = "none"
        return {
            "username": target["username"],
            "following": status == "accepted", "follow_status": status,
        }

    def decide_follow_request(self, account: Account, username: str, accept: bool) -> dict:
        service = self._service_client()
        requester = self._first(service.table("users").select("id,username").eq(
            "username", username
        ).eq("account_status", "active").limit(1).execute())
        if not requester or int(requester["id"]) in self._blocked_ids(account.id):
            raise BlendServiceError("user_not_found")
        pending = self._first(service.table("follows").select("follower_id").eq(
            "follower_id", requester["id"]
        ).eq("followee_id", account.id).eq("status", "pending").limit(1).execute())
        if not pending:
            raise BlendServiceError("follow_request_not_found")
        if accept:
            service.table("follows").update({"status": "accepted"}).eq(
                "follower_id", requester["id"]
            ).eq("followee_id", account.id).eq("status", "pending").execute()
            self.notify(int(requester["id"]), "follow_accepted", account.id,
                        event_key=f"follow-accepted:{requester['id']}:{account.id}")
        else:
            service.table("follows").delete().eq("follower_id", requester["id"]).eq(
                "followee_id", account.id).eq("status", "pending").execute()
        return {"username": requester["username"], "follow_status": "accepted" if accept else "none"}

    def follow_state(self, account: Account, user_ids: set[int]) -> dict[int, str]:
        if not user_ids:
            return {}
        rows = self._service_client().table("follows").select("followee_id,status").eq(
            "follower_id", account.id
        ).in_("followee_id", list(user_ids)).execute().data or []
        return {int(row["followee_id"]): row.get("status") or "accepted" for row in rows}

    def trending_films(self, days: int = 7, limit: int = 5) -> list[dict]:
        """Most talked-about films. The mandatory film anchor makes this free."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            rows = self._service_client().table("posts").select("film_slug").is_(
            "deleted_at", "null"
            ).is_("reply_to", "null").gte("created_at", since).limit(2000).execute().data or []
        except Exception:
            return []
        counts: dict[str, int] = {}
        for row in rows:
            slug = row.get("film_slug")
            if slug:
                counts[slug] = counts.get(slug, 0) + 1
        top = sorted(counts.items(), key=lambda item: -item[1])[:limit]
        if not top:
            return []
        posters = self._service_client().table("film_posters").select(
            "film_slug,title,release_year,poster_url"
        ).in_("film_slug", [slug for slug, _ in top]).execute().data or []
        by_slug = {row["film_slug"]: row for row in posters}
        return [{
            "slug": slug,
            "count": count,
            "title": (by_slug.get(slug) or {}).get("title") or slug,
            "year": (by_slug.get(slug) or {}).get("release_year"),
            "poster_url": (by_slug.get(slug) or {}).get("poster_url") or "",
        } for slug, count in top]

    # ── Sinema gündemi ──────────────────────────────────────────────────
    def claim_venue_ingest(
        self, slug: str, token: str, lease_seconds: int, min_age_seconds: int
    ) -> bool:
        try:
            result = self._service_client().rpc("claim_venue_ingest", {
                "p_slug": slug,
                "p_lease_token": token,
                "p_lease_seconds": int(lease_seconds),
                "p_min_age_seconds": int(min_age_seconds),
            }).execute()
            return bool(self._rpc_value(result))
        except Exception:
            return False

    def upsert_screenings(self, slug: str, rows: list[dict], run_id: str) -> int:
        try:
            result = self._service_client().rpc("upsert_screenings", {
                "p_venue_slug": slug,
                "p_rows": rows,
                "p_run_id": run_id,
            }).execute()
            return int(self._rpc_value(result) or 0)
        except Exception:
            return 0

    def record_venue_failure(self, slug: str, error: str) -> None:
        with contextlib.suppress(Exception):
            self._service_client().rpc("record_venue_failure", {
                "p_venue_slug": slug,
                "p_error": error,
            }).execute()

    def list_active_venues(self, kind: str | None = None) -> list[dict]:
        try:
            query = self._service_client().table("venues").select(
                "id,slug,name,city,kind,source_url,config,last_ok_at,last_error"
            ).eq("active", True)
            if kind:
                query = query.eq("kind", kind)
            return [dict(row) for row in (self._retry_storage_read(query.execute).data or [])]
        except Exception:
            return []

    def list_screenings(self, *, city: str = "", limit: int = 400) -> list[dict]:
        """Current, recently verified programme rows with venue attribution.

        A failed source must make a film disappear rather than leaving it
        advertised as still in cinemas indefinitely. Two missed scheduled
        refreshes plus a small buffer gives venue sites enough room to recover.
        """
        try:
            max_age_hours = max(
                30, int(self.settings.bulletin_ingest_interval_hours) * 2 + 6
            )
            fresh_after = (
                datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
            ).isoformat()
            rows = self._retry_storage_read(
                lambda: self._service_client().table("screenings").select(
                    "title_raw,year,tmdb_id,film_slug,poster_url,starts_at,url,match_status,updated_at,"
                    "venues!inner(slug,name,city,kind,active,source_url)"
                ).eq("match_status", "matched").gte(
                    "updated_at", fresh_after
                ).limit(limit).execute()
            ).data or []
        except Exception:
            return []
        out = []
        for row in rows:
            venue = row.get("venues") or {}
            if not venue.get("active", True):
                continue
            if city and venue.get("kind") != "release" and venue.get("city") != city:
                continue
            out.append({
                **{key: value for key, value in row.items() if key != "venues"},
                "venue_slug": venue.get("slug") or "",
                "venue_name": venue.get("name") or "",
                "venue_city": venue.get("city") or "",
                "venue_kind": venue.get("kind") or "",
                "venue_source_url": venue.get("source_url") or "",
            })
        return self._hydrate_screenings(out)

    def _hydrate_screenings(self, rows: list[dict]) -> list[dict]:
        """Fill in the film identity a programme page cannot give us.

        A cinema lists a title; TMDb resolves it to an id. Everything the digest
        needs beyond that — the Letterboxd slug that watchlists are keyed by,
        plus genres and director for taste matching — lives in the shared
        catalog, so one lookup by tmdb_id makes the rows usable.
        """
        wanted = sorted({int(row["tmdb_id"]) for row in rows if row.get("tmdb_id")})
        if not wanted:
            return rows
        catalog: dict[int, dict] = {}
        service = self._service_client()
        for index in range(0, len(wanted), 100):
            chunk = wanted[index : index + 100]
            try:
                found = self._retry_storage_read(
                    lambda chunk=chunk: service.table("film_posters").select(
                        "film_slug,tmdb_id,title,release_year,genres,director,poster_url"
                    ).in_("tmdb_id", chunk).execute()
                ).data or []
            except Exception:
                continue
            for item in found:
                if item.get("tmdb_id"):
                    catalog.setdefault(int(item["tmdb_id"]), item)
        for row in rows:
            entry = catalog.get(int(row["tmdb_id"])) if row.get("tmdb_id") else None
            if not entry:
                continue
            row["title"] = entry.get("title") or row.get("title_raw") or ""
            row["film_slug"] = row.get("film_slug") or entry.get("film_slug") or ""
            row["genres"] = entry.get("genres") or []
            row["director"] = entry.get("director") or ""
            row["poster_url"] = row.get("poster_url") or entry.get("poster_url") or ""
            row["year"] = row.get("year") or entry.get("release_year")
        return rows

    def get_bulletin_digest(self, user_id: int, week_start: str, city: str) -> dict | None:
        try:
            row = self._first(
                self._service_client().table("bulletin_digests")
                .select("payload,created_at")
                .eq("user_id", user_id).eq("week_start", week_start).eq("city", city)
                .limit(1).execute()
            )
        except Exception:
            return None
        return (row or {}).get("payload") or None

    def save_bulletin_digest(self, user_id: int, week_start: str, city: str, payload: dict) -> None:
        with contextlib.suppress(Exception):
            self._service_client().table("bulletin_digests").upsert({
                "user_id": user_id,
                "week_start": week_start,
                "city": city,
                "payload": payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="user_id,week_start,city").execute()

    def clear_bulletin_digests(self, week_start: str) -> None:
        """Drop one week's cards so the next visit rebuilds them."""
        with contextlib.suppress(Exception):
            self._service_client().table("bulletin_digests").delete().eq(
                "week_start", week_start
            ).execute()

    def get_rated_watched_films(self, user_id: int) -> list[dict]:
        """Only the fields the bulletin needs, so this stays a cheap read."""
        try:
            return [dict(row) for row in (self._retry_storage_read(
                lambda: self._service_client().table("user_watched_films").select(
                    "film_slug,title,tmdb_id,user_rating,rating_observed,first_seen_at"
                ).eq("user_id", user_id).eq("is_active", True).limit(5000).execute()
            ).data or [])]
        except Exception:
            return []

    def community_random_films(self, user_id: int, limit: int = 24) -> list[dict]:
        """A fresh random sample of films the membership watched and this user did not.

        Independent of the caller's watchlist: the random mode is meant to reach
        outside the list the user already curated for themselves.
        """
        try:
            result = self._retry_storage_read(
                lambda: self._service_client().rpc(
                    "community_random_films",
                    {"p_user_id": user_id, "p_limit": int(limit)},
                ).execute()
            )
        except Exception:
            return []
        return [dict(row) for row in (result.data or [])]

    # ── Shared account-independent film catalog ─────────────────────────
    def save_film_posters(self, films: list[dict]) -> int:
        rows = [f for f in films if f.get("slug")]
        if not rows:
            return 0
        try:
            result = self._retry_storage_operation(
                lambda: self._service_client().rpc(
                    "upsert_film_posters", {"p_films": rows}
                ).execute()
            )
            return int(result.data)
        except Exception:
            return 0

    def get_film_posters(self, slugs) -> dict[str, str]:
        wanted = [s for s in dict.fromkeys(slugs) if s]
        if not wanted:
            return {}
        service = self._service_client()
        out: dict[str, str] = {}
        for i in range(0, len(wanted), 200):
            chunk = wanted[i : i + 200]
            rows = (
                service.table("film_posters")
                .select("film_slug,poster_url")
                .in_("film_slug", chunk)
                .execute()
            ).data or []
            for row in rows:
                if row.get("poster_url"):
                    out[row["film_slug"]] = row["poster_url"]
        return out

    def get_film_assets(self, slugs) -> dict[str, dict]:
        wanted = [s for s in dict.fromkeys(slugs) if s]
        if not wanted:
            return {}
        service = self._service_client()
        out: dict[str, dict] = {}
        for i in range(0, len(wanted), 200):
            rows = (
                service.table("film_posters")
                .select(
                    "film_slug,poster_url,poster_resolver_url,tmdb_id,title,release_year,overview,"
                    "director,genres,keywords,vote_average,matched,details_loaded"
                )
                .in_("film_slug", wanted[i : i + 200])
                .execute()
            ).data or []
            out.update({row["film_slug"]: row for row in rows if row.get("film_slug")})
        return out

    def get_film_posters_by_tmdb_ids(self, tmdb_ids) -> dict[int, str]:
        wanted = [int(value) for value in dict.fromkeys(tmdb_ids) if value]
        if not wanted:
            return {}
        out: dict[int, str] = {}
        service = self._service_client()
        for i in range(0, len(wanted), 200):
            rows = (
                service.table("film_posters")
                .select("tmdb_id,poster_url")
                .in_("tmdb_id", wanted[i : i + 200])
                .execute()
            ).data or []
            for row in rows:
                if row.get("tmdb_id") and row.get("poster_url"):
                    out[int(row["tmdb_id"])] = row["poster_url"]
        return out

    def get_director_images(self, names) -> dict[str, str]:
        assets = self.get_director_assets(names)
        return {
            name: row["photo_url"]
            for name, row in assets.items()
            if row.get("photo_url")
        }

    def get_director_assets(self, names) -> dict[str, dict]:
        requested = [str(name).strip() for name in dict.fromkeys(names) if str(name).strip()]
        normalized = [name.lower() for name in requested]
        if not normalized:
            return {}
        rows = (
            self._service_client()
            .table("director_images")
            .select("normalized_name,display_name,photo_url,tmdb_person_id")
            .in_("normalized_name", normalized)
            .execute()
        ).data or []
        by_normalized = {
            row["normalized_name"]: row
            for row in rows
            if row.get("normalized_name")
        }
        return {
            name: by_normalized[name.lower()]
            for name in requested
            if name.lower() in by_normalized
        }

    def save_director_images(self, directors: list[dict]) -> int:
        rows = [
            row for row in directors
            if row.get("name") and row.get("photo_url")
        ]
        if not rows:
            return 0
        result = self._service_client().rpc(
            "upsert_director_images", {"p_directors": rows}
        ).execute()
        try:
            return int(self._rpc_value(result) or 0)
        except (TypeError, ValueError):
            return 0

    def list_director_films(
        self, user_id: int, director: str, *, limit: int = 60, offset: int = 0
    ) -> list[dict]:
        return (
            self._service_client()
            .table("user_watched_films")
            .select("film_slug,title,release_year,poster_url,user_rating,watched_rank")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .eq("director", director)
            .order("user_rating", desc=True, nullsfirst=False)
            .order("watched_rank")
            .range(offset, offset + limit - 1)
            .execute()
        ).data or []

    def search_accounts(self, account: Account, query: str, limit: int = 8) -> list[dict]:
        service = self._service_client()
        result = (
            service
            .table("users")
            .select("id,username,display_name,avatar_url")
            .eq("account_status", "active")
            .neq("id", account.id)
            .ilike("username", f"%{query}%")
            .order("username")
            .limit(24)
            .execute()
        )
        blocks = (
            service.table("user_blocks")
            .select("blocker_user_id,blocked_user_id")
            .or_(f"blocker_user_id.eq.{account.id},blocked_user_id.eq.{account.id}")
            .execute()
        ).data or []
        blocked_ids = {
            int(row["blocked_user_id"])
            if int(row["blocker_user_id"]) == account.id
            else int(row["blocker_user_id"])
            for row in blocks
        }
        safe_limit = max(1, min(limit, 12))
        return [
            {key: value for key, value in row.items() if key != "id"}
            for row in (result.data or [])
            if int(row["id"]) not in blocked_ids
        ][:safe_limit]

    @staticmethod
    def _rpc_value(result):
        data = result.data
        if isinstance(data, list) and len(data) == 1:
            return data[0]
        return data

    def create_blend_request(
        self, account: Account, recipient_username: str, *, ip_hash: str = ""
    ) -> str:
        service = self._service_client()
        try:
            recipient = self._first(service.table("users").select("id").eq(
                "username", recipient_username
            ).eq("account_status", "active").limit(1).execute())
            if not recipient:
                raise BlendServiceError("recipient_not_found")
            recipient_id = int(recipient["id"])
            if recipient_id in self._blocked_ids(account.id):
                raise BlendServiceError("blend_user_blocked")
            mutual = service.table("follows").select("follower_id,followee_id").eq(
                "status", "accepted"
            ).or_(
                f"and(follower_id.eq.{account.id},followee_id.eq.{recipient_id}),"
                f"and(follower_id.eq.{recipient_id},followee_id.eq.{account.id})"
            ).execute().data or []
            if len({(int(row["follower_id"]), int(row["followee_id"])) for row in mutual}) != 2:
                raise BlendServiceError("blend_follow_required")
            result = service.rpc(
                "create_blend_request",
                {
                    "p_requester_user_id": account.id,
                    "p_recipient_username": recipient_username,
                },
            ).execute()
            request_id = str(self._rpc_value(result))
            self.notify(
                recipient_id, "blend_request", account.id,
                event_key=f"blend-request:{request_id}",
            )
            self._audit(service, account.id, "blend_request_created", ip_hash)
            return request_id
        except Exception as exc:
            message = str(exc)
            known = (
                "recipient_not_found",
                "self_request",
                "blend_request_exists",
                "blend_already_accepted",
                "pending_quota_reached",
                "blend_user_blocked",
                "blend_follow_required",
            )
            code = next((item for item in known if item in message), "blend_request_failed")
            raise BlendServiceError(code) from exc

    def find_blend_relation(
        self, account: Account, recipient_username: str
    ) -> dict | None:
        """Find an accepted or still-live pending Blend for one unordered pair."""
        service = self._service_client()
        peer = self._first(
            service.table("users")
            .select("id,username")
            .eq("username", recipient_username.strip().lstrip("@").lower())
            .eq("account_status", "active")
            .limit(1)
            .execute()
        )
        if not peer:
            return None
        peer_id = int(peer["id"])
        rows = (
            service.table("blend_requests")
            .select(
                "id,requester_user_id,recipient_user_id,status,created_at,expires_at"
            )
            .or_(
                f"and(requester_user_id.eq.{account.id},recipient_user_id.eq.{peer_id}),"
                f"and(requester_user_id.eq.{peer_id},recipient_user_id.eq.{account.id})"
            )
            .in_("status", ["accepted", "pending"])
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        ).data or []
        now = datetime.now(timezone.utc)
        accepted = next((row for row in rows if row.get("status") == "accepted"), None)
        if accepted:
            return {
                "request_id": str(accepted["id"]),
                "status": "accepted",
                "direction": (
                    "incoming"
                    if int(accepted["recipient_user_id"]) == account.id
                    else "outgoing"
                ),
            }
        for row in rows:
            if row.get("status") != "pending":
                continue
            expires = row.get("expires_at")
            if isinstance(expires, str):
                try:
                    expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                except ValueError:
                    expires = None
            if isinstance(expires, datetime) and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires and expires <= now:
                continue
            return {
                "request_id": str(row["id"]),
                "status": "pending",
                "direction": (
                    "incoming"
                    if int(row["recipient_user_id"]) == account.id
                    else "outgoing"
                ),
            }
        return None

    def decide_blend_request(
        self,
        account: Account,
        request_id: str,
        decision: str,
        *,
        ip_hash: str = "",
    ) -> dict:
        service = self._service_client()
        try:
            request = self._first(service.table("blend_requests").select(
                "requester_user_id,recipient_user_id"
            ).eq("id", request_id).limit(1).execute()) or {}
            result = service.rpc(
                "decide_blend_request",
                {
                    "p_request_id": request_id,
                    "p_recipient_user_id": account.id,
                    "p_decision": decision,
                },
            ).execute()
            row = self._rpc_value(result) or {}
            requester_id = int(request.get("requester_user_id") or 0)
            if requester_id:
                kind = "blend_accepted" if decision == "accepted" else "blend_rejected"
                self.notify(
                    requester_id, kind, account.id,
                    event_key=f"blend-{decision}:{request_id}",
                )
            self._audit(service, account.id, f"blend_request_{decision}", ip_hash)
            return row
        except Exception as exc:
            message = str(exc)
            known = (
                "invalid_decision",
                "request_not_found",
                "forbidden",
                "request_already_decided",
            )
            code = next((item for item in known if item in message), "blend_decision_failed")
            raise BlendServiceError(code) from exc

    def cancel_blend_request(self, account: Account, request_id: str) -> None:
        try:
            self._service_client().rpc(
                "cancel_blend_request",
                {
                    "p_request_id": request_id,
                    "p_requester_user_id": account.id,
                },
            ).execute()
            self._audit(self._service_client(), account.id, "blend_request_cancelled")
        except Exception as exc:
            raise BlendServiceError("request_not_cancellable") from exc

    def _accounts_by_id(self, service, user_ids: set[int]) -> dict[int, dict]:
        if not user_ids:
            return {}
        users = (
            service.table("users")
            .select("id,username,display_name,avatar_url")
            .in_("id", list(user_ids))
            .execute()
        ).data or []
        return {
            int(row["id"]): {
                "username": row["username"],
                "display_name": row.get("display_name") or row["username"],
                "avatar_url": row.get("avatar_url") or "",
            }
            for row in users
        }

    def list_blends(self, account: Account) -> dict:
        service = self._service_client()
        now = datetime.now(timezone.utc).isoformat()
        try:
            (
                service.table("blend_requests")
                .update({"status": "expired", "decided_at": now})
                .eq("status", "pending")
                .lt("expires_at", now)
                .execute()
            )
        except Exception:
            pass
        requests = (
            service.table("blend_requests")
            .select(
                "id,requester_user_id,recipient_user_id,status,created_at,"
                "decided_at,expires_at"
            )
            .or_(
                f"requester_user_id.eq.{account.id},recipient_user_id.eq.{account.id}"
            )
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        ).data or []
        blocked_rows = (
            service.table("user_blocks")
            .select("blocked_user_id,created_at")
            .eq("blocker_user_id", account.id)
            .order("created_at", desc=True)
            .execute()
        ).data or []
        user_ids = {
            int(row["requester_user_id"])
            if int(row["requester_user_id"]) != account.id
            else int(row["recipient_user_id"])
            for row in requests
        }
        user_ids.update(int(row["blocked_user_id"]) for row in blocked_rows)
        accounts = self._accounts_by_id(service, user_ids)
        request_ids = [row["id"] for row in requests]
        results = []
        if request_ids:
            results = (
                service.table("blend_results")
                .select(
                    "id,request_id,score,confidence,algorithm_version,created_at"
                )
                .in_("request_id", request_ids)
                .execute()
            ).data or []
        result_by_request = {row["request_id"]: row for row in results}

        incoming, outgoing, history = [], [], []
        for row in requests:
            is_incoming = int(row["recipient_user_id"]) == account.id
            peer_id = int(
                row["requester_user_id"] if is_incoming else row["recipient_user_id"]
            )
            item = {
                **row,
                "direction": "incoming" if is_incoming else "outgoing",
                "peer": accounts.get(peer_id),
                "blend_result": result_by_request.get(row["id"]),
            }
            if row["status"] == "pending":
                (incoming if is_incoming else outgoing).append(item)
            else:
                history.append(item)
        blocked = [
            {
                "created_at": row["created_at"],
                "user": accounts.get(int(row["blocked_user_id"])),
            }
            for row in blocked_rows
        ]
        return {
            "incoming": incoming,
            "outgoing": outgoing,
            "history": history,
            "blocked": blocked,
        }

    def count_pending_blend_requests(self, account: Account) -> int:
        """Lightweight inbox badge count; no peer/result payload is loaded."""
        rows = (
            self._service_client()
            .table("blend_requests")
            .select("id")
            .eq("recipient_user_id", account.id)
            .eq("status", "pending")
            .execute()
        ).data or []
        return len(rows)

    def get_blend_participants(
        self, account: Account, request_id: str
    ) -> tuple[dict, Account, Account]:
        service = self._service_client()
        request = self._first(
            service.table("blend_requests")
            .select("id,requester_user_id,recipient_user_id,status")
            .eq("id", request_id)
            .limit(1)
            .execute()
        )
        if request is None or request.get("status") != "accepted":
            raise BlendServiceError("accepted_request_not_found")
        if account.id not in (
            int(request["requester_user_id"]),
            int(request["recipient_user_id"]),
        ):
            raise BlendServiceError("forbidden")
        rows = (
            service.table("users")
            .select(
                "id,auth_user_id,username,display_name,avatar_url,"
                "account_status,profile_sync_status,onboarding_completed_at,letterboxd_stats"
            )
            .in_(
                "id",
                [request["requester_user_id"], request["recipient_user_id"]],
            )
            .execute()
        ).data or []
        by_id = {int(row["id"]): self._account(row) for row in rows}
        try:
            requester = by_id[int(request["requester_user_id"])]
            recipient = by_id[int(request["recipient_user_id"])]
        except KeyError as exc:
            raise BlendServiceError("participant_not_found") from exc
        return request, requester, recipient

    def get_blend_result(self, request_id: str) -> dict | None:
        return self._first(
            self._service_client()
            .table("blend_results")
            .select(
                "id,request_id,score,confidence,result,algorithm_version,created_at"
            )
            .eq("request_id", request_id)
            .limit(1)
            .execute()
        )

    def save_blend_result(
        self,
        account: Account,
        request_id: str,
        result: dict,
        *,
        algorithm_version: str,
    ) -> str:
        try:
            response = self._service_client().rpc(
                "save_blend_result",
                {
                    "p_request_id": request_id,
                    "p_actor_user_id": account.id,
                    "p_score": result["score"],
                    "p_confidence": result["confidence"],
                    "p_result": result,
                    "p_algorithm_version": algorithm_version,
                },
            ).execute()
            return str(self._rpc_value(response))
        except Exception as exc:
            raise BlendServiceError("blend_result_save_failed") from exc

    def delete_blend(self, account: Account, request_id: str) -> None:
        """Delete the shared request; its result cascades for both participants."""
        service = self._service_client()
        # Reuse the accepted-participant guard before the service-role delete.
        self.get_blend_participants(account, request_id)
        try:
            service.table("blend_requests").delete().eq("id", request_id).execute()
        except Exception as exc:
            raise BlendServiceError("blend_delete_failed") from exc
        try:
            self._audit(service, account.id, "blend_deleted", "")
        except Exception:
            pass

    def block_user(self, account: Account, username: str) -> None:
        try:
            self._service_client().rpc(
                "block_user",
                {
                    "p_blocker_user_id": account.id,
                    "p_blocked_username": username,
                },
            ).execute()
            self._audit(self._service_client(), account.id, "user_blocked")
        except Exception as exc:
            message = str(exc)
            code = next(
                (item for item in ("user_not_found", "self_block") if item in message),
                "block_failed",
            )
            raise BlendServiceError(code) from exc

    def unblock_user(self, account: Account, username: str) -> None:
        try:
            self._service_client().rpc(
                "unblock_user",
                {
                    "p_blocker_user_id": account.id,
                    "p_blocked_username": username,
                },
            ).execute()
            self._audit(self._service_client(), account.id, "user_unblocked")
        except Exception as exc:
            raise BlendServiceError("unblock_failed") from exc

    def report_user(
        self, account: Account, username: str, category: str, detail: str
    ) -> str:
        try:
            response = self._service_client().rpc(
                "report_user",
                {
                    "p_reporter_user_id": account.id,
                    "p_reported_username": username,
                    "p_category": category,
                    "p_detail": detail[:500],
                },
            ).execute()
            self._audit(self._service_client(), account.id, "user_reported")
            return str(self._rpc_value(response))
        except Exception as exc:
            message = str(exc)
            known = (
                "invalid_report_category",
                "user_not_found",
                "self_report",
                "report_quota_reached",
            )
            code = next((item for item in known if item in message), "report_failed")
            raise BlendServiceError(code) from exc

    def report_post(
        self, account: Account, post_id: str, category: str, detail: str
    ) -> str:
        service = self._service_client()
        post = self._first(service.table("posts").select("id,author_id").eq(
            "id", post_id
        ).is_("deleted_at", "null").limit(1).execute())
        if not post or not self._visible_rows(account, [post]):
            raise BlendServiceError("post_not_found")
        if int(post["author_id"]) == account.id:
            raise BlendServiceError("self_report")
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        count = service.table("user_reports").select("id", count="exact").eq(
            "reporter_user_id", account.id
        ).gte("created_at", since).limit(1).execute()
        if int(count.count or 0) >= 10:
            raise BlendServiceError("report_quota_reached")
        try:
            created = self._first(service.table("user_reports").insert({
                "reporter_user_id": account.id,
                "reported_user_id": int(post["author_id"]),
                "post_id": post_id,
                "category": category,
                "detail": detail[:500],
            }).execute())
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise BlendServiceError("post_already_reported") from exc
            raise BlendServiceError("report_failed") from exc
        self._audit(service, account.id, "post_reported")
        return str((created or {}).get("id") or "")
