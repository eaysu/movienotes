"""Application settings, loaded from the environment / a .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Values come from .env or the environment."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API keys ---
    tmdb_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini-2025-08-07"
    # Zevk/kişilik analizi için ayrı model; boşsa openai_model kullanılır.
    openai_analysis_model: str = "gpt-5.6-terra"
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_anon_key: str = ""
    auth_identity_secret: str = ""
    auth_cookie_secure: bool = True
    # "Beni hatırla" işaretliyse oturum tarayıcı sınırına kadar yaşar
    # (Chrome çerezleri 400 günde keser); işaretlenmezse bir gün.
    auth_session_max_age: int = 60 * 60 * 24 * 400
    auth_session_short_max_age: int = 60 * 60 * 24
    web_push_vapid_public_key: str = ""
    web_push_vapid_private_key: str = ""
    # Marka adı değişse de bunlar adres: yayında olan konağı gösterirler.
    # Kendi alan adı alındığında PUBLIC_SITE_HOST'u ayarlamak yeterli.
    public_site_host: str = "movie-boxd.onrender.com"
    web_push_vapid_subject: str = "mailto:hello@movie-boxd.onrender.com"

    # --- Recommender tuning ---
    num_recommendations: int = 5
    recommendation_history_limit: int = 100
    favorite_director_boost: float = 0.08

    # --- Local development ---
    # Opens a password-free login route, so onboarding can be replayed without
    # re-registering. Never set this in a deployed environment: it is a login
    # bypass. The route also refuses any caller that is not on this machine.
    dev_login_enabled: bool = False
    dev_login_password: str = "movienotes-dev-only"
    # Runs the app against an in-memory stand-in for the database: any public
    # Letterboxd name signs straight in, nothing is written anywhere, and the
    # session dies with the process. Started by `python -m scripts.sandbox`,
    # which also points DATA_DIR at a temporary folder it removes on exit.
    # Never set this in a deployed environment: it has no passwords at all.
    sandbox_mode: bool = False

    # --- Sinema gündemi (bülten) ---
    # Ships dark: the release layer and the venue framework are inert until this
    # is turned on for an account cohort.
    bulletin_enabled: bool = False
    bulletin_region: str = "TR"
    # Kart üzerindeki şehir seçicisi; boş seçim ülke geneli vizyon demektir.
    bulletin_cities: str = "İstanbul,Ankara,İzmir"
    # A venue is re-fetched at most this often, whoever triggers it.
    bulletin_ingest_interval_hours: int = 12
    # A card is cheap to rebuild; keeping it short prevents a morning refresh
    # from being hidden behind a week-old personalised digest.
    bulletin_digest_ttl_hours: int = 6

    # --- Scraper ---
    # Letterboxd sayfaları doğrudan, curl-cffi ile okunur. Ücretli veya harici
    # scraping proxy/API servisi kullanılmaz.
    # Agresif mod: hız öncelikli — daha kısa gecikme, daha düşük film tavanı.
    scrape_delay: float = 0.6       # sayfalar arası temel gecikme (jitter eklenir)
    scrape_max_pages: int = 8       # watchlist için max sayfa
    watched_max_pages: int = 8      # izlenen filmler için max sayfa
    watched_film_limit: int = 100   # hard limit — en son izlenen N film (taste profili)
    # Kept for backwards-compatible env parsing; onboarding no longer performs
    # a duplicate watched-list scrape before the checkpointed full sweep.
    provisional_watched_film_limit: int = 0
    watchlist_film_limit: int = 150 # candidate havuzu — en son eklenen N film (varsayılan sıra: en yeni önce)
    scrape_max_retries: int = 2     # 403/429'da sayfa başına tekrar deneme

    # --- Günce taraması ---
    # Saatlik koşu, üye başına tek istek. Maliyeti belirleyen şey kaydın sayısı
    # değil, koştaki üye sayısı: bir sayfadan üç kayıt okumakla on iki kayıt
    # okumak aynı isteği harcıyor.
    diary_scan_enabled: bool = True
    diary_scan_members_per_run: int = 20   # saatlik bütçe — üye sayısından bağımsız
    diary_scan_entries: int = 3            # üye başına en yeni kaç yorumlu kayıt
    # Yazan üye sık, yazmayan üye seyrek taranıyor. Ardışık boş taramada eşik
    # ikiye katlanıp tavana kadar çıkıyor; ilk yeni kayıtta tabana dönüyor.
    # Üye sayısı büyüdükçe bütçeyi asıl koruyan mekanizma bu.
    diary_scan_min_hours: int = 1
    diary_scan_max_hours: int = 24
    # Yeni üyenin arşivi: uygulamaya girdikten sonra, koş başına birkaç sayfa.
    # Sayfa başına on iki kayıt, yani bir koşta ~36 kayıt yeniden eskiye doğru.
    diary_backfill_members_per_run: int = 2
    diary_backfill_pages_per_run: int = 3

    # --- Storage ---
    data_dir: str = "data"

    @property
    def data_path(self) -> Path:
        """Önbellek dizini.

        Göreli bir ad verilirse depo köküne (backend/app/config.py → ../..)
        bağlanıyor; çalışma dizinine değil. Aksi hâlde uygulamayı `backend/`
        içinden başlatmak ikinci ve boş bir cache.sqlite3 açar, kazınmış
        her şey yeniden çekilir.
        """
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[2] / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_db_path(self) -> Path:
        return self.data_path / "cache.sqlite3"

    @property
    def has_tmdb(self) -> bool:
        return bool(self.tmdb_api_key.strip())

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url.strip() and self.supabase_key.strip())

    @property
    def bulletin_city_list(self) -> list[str]:
        return [city.strip() for city in self.bulletin_cities.split(",") if city.strip()]

    @property
    def has_auth(self) -> bool:
        # The sandbox has accounts, just nowhere to keep them. The shell reads
        # this to decide whether to show the sign-in screen at all.
        return bool(self.sandbox_mode) or bool(
            self.has_supabase
            and self.supabase_anon_key.strip()
            and self.auth_identity_secret.strip()
        )

    @property
    def has_web_push(self) -> bool:
        return bool(self.web_push_vapid_public_key.strip() and self.web_push_vapid_private_key.strip())


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
