# Movienotes

Kullanıcı adı ve parolayla açılan bağımsız bir sinefil hesabı. Dört favori filmle
başlayan profil, GPT ile kısa bir zevk okuması ve uygulama tanıtımı sunar. Akışta
film hakkında 420 karakterlik not yazılır; isteğe bağlı puan 0.5–5 yıldızdır.
Notun tarihi sunucunun yayınladığı andır; geçmiş bir izleme tarihi girilmez.

Letterboxd hesabı gerekmez. İsteyen kendi ZIP dışa aktarımını yükleyerek izleme
geçmişini, puanlarını, izleme listesini ve yorumlarını özel arşivine taşıyabilir.
Bu yorumlar otomatik olarak topluluk akışına yayımlanmaz. Scraping varsayılan
olarak kapalıdır ve kayıt, onboarding, ZIP aktarımı veya öneriler için gerekmez.

## Çalıştırma

Python 3.12 ve frontend derlemesi için Node.js gerekir.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
npm install
npm run build:css
PYTHONPATH=backend uvicorn app.main:app --reload
```

Supabase hesabı için `SUPABASE_URL`, service-role `SUPABASE_KEY`,
`SUPABASE_ANON_KEY` ve kalıcı `AUTH_IDENTITY_SECRET` ayarlanmalı. Parolaları
Supabase Auth tutar; uygulama tablolarına parola veya parola özeti yazılmaz.
E-posta toplanmadığı için yeni hesaplarda otomatik parola kurtarma yoktur.

TMDb anahtarı katalog aramasını ve yeni film bilgilerini sağlar. Kayıtlı katalog,
TMDb geçici olarak erişilemiyorsa kullanılmaya devam eder. OpenAI anahtarı yoksa
veya model yanıt vermezse ilk yerel zevk özeti korunur; analiz tekrar denenebilir.

## Veritabanı ve geçiş

Yeni bir kurulumda önce `backend/supabase/schema.sql`, ardından
`backend/supabase/native_membership.sql` uygulanır. Var olan yayında yeni
migration **sunucu sürümü değiştirilmeden önce** uygulanmalı.

Yeni migration ek tablolardır: film UUID'leri, harici kimlik eşlemeleri, kişisel
film rafı ve özel ZIP arşivi. Eski kullanıcı kimlikleri, film sluggları, notlar,
takipler, Blendler ve mektuplar korunur. Mevcut film verileri ortak kataloğa
aktarılır. Tekrar çalıştırılabilir; kullanıcı verisi temizleyen bir reset yoktur.

`AUTH_IDENTITY_SECRET`, sentetik auth e-posta alan adı
`@users.movieboxd.invalid` ve `/static` varlık öneki değiştirilmemeli. Bunlar
mevcut hesap ve tarayıcılarla geriye uyumluluk sağlar. Yeni üyelik aynı auth
kimliklerini kullanır. Daha önce parolasız açılmış hesaplar, mevcut oturumlarından
Ayarlar → Hesap güvenliği ile parola belirlemeli; yeni giriş ekranı parola ister.

Kataloğu ek filmlerle önceden doldurmak için, migration sonrasında:

```sh
PYTHONPATH=backend python -m scripts.warm_catalog --pages 5
```

Bu komut TMDb popüler ve yüksek puanlı listelerini sınırlı hızla okur. Aramada
bulunan yeni filmler de ortak kataloğa eklenir. Bütün kataloğu tek seferde indirmek
kullanıcının kayıt veya film seçimi için bekleme koşulu değildir.

## Kod düzeni

- `backend/app/native_api.py`: bağımsız kayıt, Fav 4, analiz, katalog ve raf uçları.
- `backend/app/catalog.py`: ortak UUID, TMDb araması, ayrıntılar ve eski slug eşlemeleri.
- `backend/app/library.py`: kişisel raf, puanlı notlar ve kalıcı ZIP eşleştirme işleri.
- `backend/app/letterboxd_import.py`: ZIP/CSV ayrıştırma; Letterboxd ağ isteği yapmaz.
- `frontend/js/onboarding.js`: dört film seçimi, ürün tanıtımı ve GPT okuması.
- `frontend/js/library.js`: izlenenler, izleme listesi, özel arşiv ve eşleşme düzeltme.
- `backend/app/main.py` ve `frontend/js/app.js`: mevcut sosyal uygulamanın kabuğu.

Ayrıntılı davranış sözleşmesi: [docs/native-membership.md](docs/native-membership.md).
Önceki sürümün scraping belgeleri: [docs/legacy-scraping.md](docs/legacy-scraping.md).

## Testler

```sh
pytest
npm run check:js
npm run build:css
```

Kişisel Letterboxd ZIP dosyaları git'e alınmaz. Proje kökündeki
`letterboxd-enesaysu-2026-09-02-21-21-utc.zip` varsa gerçek dışa aktarım testi de
çalışır. Bu dosyada 563 izlenen film, 256 puan, 202 watchlist satırı, 149 günce
satırı, 86 yorum ve dört favori bulunur. Tekrar aktarım ve yerel puanın korunması
ayrıca test edilir.

`backend/tests/test_native_postgres.py`, yalnızca localhost'taki ayrı PostgreSQL /
PostgREST test ortamı için `NATIVE_TEST_POSTGREST_URL` verilirse çalışır.
`backend/tests/native_membership.sql`, izole veritabanında RPC atomikliği ve
RLS erişim izinlerini kontrol eder; çalışması sonunda test yazımlarını geri alır.

TMDb ve OpenAI bağımlılık simülasyonu:

```sh
postman simulation run postman/simulations/native-membership/native-membership.sim.yaml --port 4517 --no-history
```

Bu yerel testte `TMDB_API_BASE=http://127.0.0.1:4517/tmdb/3` ve
`OPENAI_BASE_URL=http://127.0.0.1:4517/openai/v1` kullanılır; gerçek anahtar
kullanılmamalı. Simülasyon, API sağlayıcılarının canlı davranışını doğrulamaz.
