# Movienotes

Letterboxd hesabına bağlanan bir **sinefil akışı** ve **yapay zekâ destekli film
önerici**. Kullanıcı herkese açık Letterboxd kullanıcı adıyla kaydolur, geçici
bir bio koduyla hesabın kendisine ait olduğunu kanıtlar; ardından izleme
geçmişi, Fav 4'ü ve puan farkındalı zevk analizi kalıcı olarak saklanır.

Uygulamanın ana ekranı akıştır: üyeler film notu paylaşır, birbirini takip eder,
Letterboxd güncesindeki yorumlu kayıtlar otomatik olarak akışa düşer. İzleme
listesi ise **TF-IDF + LLM melez** öneri hattıyla sıralanır:

```
kullanıcı adı → doğrudan scrape → TMDb ile zenginleştirme → benzerlik sıralaması
            → LLM yeniden sıralama → öneriler
```

TMDb ve OpenAI isteğe bağlıdır. TMDb yoksa zenginleştirme atlanır; OpenAI
anahtarı yoksa son adım yerel benzerlik sıralamasına düşer.

## Ürün

| Alan | Ne yapar |
|------|----------|
| **Akış** | Film notları, cevaplar, beğeni, spoiler etiketi, film bazlı filtre. Ana ekran. |
| **Günce aktarımı** | Üyenin Letterboxd'daki **yalnızca yorumlu** izleme kayıtlarının tamamı çekilir; akışta izlendiği günün tarihiyle yer alır. |
| **Film sayfası** | Poster, kaç üyenin izlediği, topluluk ortalaması, o filme dair bütün notlar, "bu hafta perdede mi". |
| **Ne izlesem?** | İzleme listesinden zevke göre sıralı öneri, gerekçesiyle. Sınırsız rastgele mod ayrı havuzdan çalışır. |
| **Sinefil Sineması** | Zevk örtüşmesine göre sıralanmış üye kartları; Fav 4 ve eşleşme notu. |
| **Blend** | İki üyenin karşılıklı onayıyla hesaplanan 0–100 uyum skoru ve ortak izleme listesi. |
| **Mektuplar** | Üyeler arası uzun biçimli yazışma; günde bir gönderim. |
| **Sinema bülteni** | Haftalık vizyon ajandası; perdedeki her film, üyeyle olan bağın gücüne göre sıralı. |
| **Bildirimler** | Takip, takip isteği, not cevabı, beğeni, mektup, Blend ve "bu hafta perdede" olayları. Web push desteklidir. |
| **Kurulum çağrısı** | Ana ekrana ekleme daveti; iOS'ta paylaş menüsü adımlarıyla. |

## Depo düzeni

```
movie-box/
├── backend/
│   ├── app/
│   │   ├── main.py          FastAPI uygulaması, bütün uç noktalar
│   │   ├── config.py        ayarlar / .env okuma
│   │   ├── auth.py          hesaplar, akış, mektup, Blend, bildirim veri katmanı
│   │   ├── database.py      Supabase service-role istemcisi
│   │   ├── cache.py         katmanlı SQLite/Supabase anahtar-değer önbelleği
│   │   ├── rate_limit.py    IP başına bütçeler (auth / ağır uçlar / silme)
│   │   ├── scraper.py       katman 1 — profil, izleme listesi, yorumlu günce
│   │   ├── enrich.py        katman 2 — TMDb zenginleştirme
│   │   ├── recommender.py   katman 3 — puan farkındalı benzerlik sıralaması
│   │   ├── taste_profile.py kalıcı zevk özeti ve güven skoru
│   │   ├── profile_sync.py  kontrol noktalı tam/artımlı geçmiş taraması
│   │   ├── semantic.py      gömme tabanlı benzerlik
│   │   ├── screenings.py    mekân programı ayrıştırma ve bülten
│   │   └── llm.py           katman 4 — LLM yeniden sıralama ve zevk metni
│   ├── scripts/             tek komutluk bakım ve göç araçları
│   ├── supabase/schema.sql  tablolar, RPC'ler, RLS ve grant'lar
│   ├── tests/               pytest takımı
│   ├── requirements.txt
│   └── runtime.txt
├── frontend/                → `/static` altında servis edilir
│   ├── index.html           anlamsal kabuk
│   ├── css/source.css       Tailwind kaynağı
│   ├── app.css              üretilmiş Tailwind çıktısı (commit'lenir)
│   ├── js/                  auth / api / profile / recommendations / blend / app
│   ├── push-sw.js           web push service worker'ı
│   ├── site.webmanifest     PWA manifesti
│   └── *.png, og-image-v6.jpg
├── brand/                   kaynak görseller (ikon master'ı, kapak, OG kaynağı)
├── data/cache.sqlite3       yerel önbellek (git'e girmez)
├── package.json             Tailwind derlemesi ve JS sözdizimi kontrolü
├── tailwind.config.cjs
├── pytest.ini
├── Procfile · render.yaml   dağıtım
└── .env.example
```

Python paketleri `backend/` kökünden içe aktarılıyor, bu yüzden sunucuyu depo
kökünden çalıştırmak `PYTHONPATH=backend` istiyor. Bakım betikleri kendi kökünü
bulduğu için iki biçimde de çalışıyor: `PYTHONPATH=backend python -m
scripts.<ad>` veya `python -m backend.scripts.<ad>`. `frontend/` dizini `/static`
URL öneki altında servis edilir — **önek değiştirilemez**: sürümlenmiş her
varlığın adresini bozar ve bir yıllık `immutable` önbelleği ıskartaya çıkarır.

## Kurulum

Python 3.12 gerekiyor (`backend/runtime.txt`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env            # anahtarları buraya
```

### Sunucuyu çalıştır

```bash
PYTHONPATH=backend uvicorn app.main:app --reload
```

http://localhost:8000

### Deneme oturumu (sandbox)

Onboarding'i herhangi bir Letterboxd profili üzerinde, hiçbir şey saklamadan
izlemek için:

```bash
./run-test.sh                 # sunucuyu açar, hazır olunca Chrome'u getirir
./run-test.sh --port 9000
./run-test.sh --keep-cache    # taramaları çalıştırmalar arasında koru
```

`run-test.sh` yalnızca bir sarmalayıcı: bağımlılıkları kurulu Python'u kendi
buluyor, sağlık ucu yanıt verene kadar bekliyor ve ancak sonra tarayıcıyı
açıyor. Doğrudan da çalıştırılabilir:

```bash
python -m scripts.sandbox --no-browser --port 9000
```

Giriş ekranı yalnızca bir Letterboxd adı sorar — parola yok, çünkü ortada hesap
yok. Yazılan ad gerçek scraper ile taranır, gerçek onboarding oynar, arka plan
arşiv taraması gerçekten çalışır; tek fark her şeyin bellekte durması.
Supabase'e hiç bağlanılmaz (`.env`'den yalnızca TMDb/OpenAI anahtarları
okunur), `DATA_DIR` geçici bir klasöre bakar ve Ctrl-C o klasörü siler. Yani
depodaki `data/cache.sqlite3` bu oturumdan hiç etkilenmez.

`SANDBOX_MODE` yayında **asla** açılmamalı: parola diye bir şey yok. Uç nokta
ayrıca kendi makinesi dışından gelen çağrıyı 404 ile reddediyor.

### Yerel geliştirme

Kayıt, herkese açık bir Letterboxd bio'suna kod yazmayı gerektiriyor: üretimde
doğru kapı, test edilen şey onboarding olduğunda saf sürtünme. Tek komut hesabı
hazırlar, sunucuyu başlatır ve tarayıcıyı giriş yapılmış hâlde açar:

```bash
PYTHONPATH=backend python -m scripts.dev_start              # enesaysu olarak gir, veriyi koru
PYTHONPATH=backend python -m scripts.dev_start --fresh      # onboarding'i sıfırdan oynat
PYTHONPATH=backend python -m scripts.dev_start --user someone --port 8010
```

`.env.local` içinde `SUPABASE_ANON_KEY` ve `AUTH_IDENTITY_SECRET` gerekiyor;
kimlik sırrı **Render'daki ile aynı olmak zorunda** — sentetik giriş e-postası
ondan türetiliyor, farklı bir değer var olan hesabı bulamaz.

İki sonucu bilmeye değer: betik hesabın parolasını `DEV_LOGIN_PASSWORD` yapar,
yani eski parola uygulamadan sıfırlanana kadar çalışmaz. Ve ayrı bir yerel
veritabanı yok — `--fresh` o kullanıcının gerçek profil satırlarını siler,
bu yüzden önce sorar.

Kullandığı rota (`GET /api/dev/login`) yalnızca `DEV_LOGIN_ENABLED` verildiğinde
var olur ve loopback dışındaki her çağırıcıyı reddeder. `render.yaml` içinde
değildir ve asla olmamalıdır.

### Frontend ve testler

```bash
npm install && npm run build:css   # frontend/app.css'i yeniden üret
npm run check:js                   # sözdizimi kontrolü
pytest                             # pytest.ini PYTHONPATH'i kendisi ayarlıyor
```

Tailwind çıktısı commit'lenir; Render çalışma anında Node'a ihtiyaç duymaz.
`frontend/js/app.js` veya `app.css` değişirse `index.html` içindeki `?v=`
sürümünü de artırın — `test_shell_asset_content_changes_force_a_version_bump`
bunu unutmayı başarısız bir test hâline getiriyor, çünkü sürümlenmiş varlıklar
bir yıl boyunca `immutable` servis ediliyor ve eski `?v=` ile yapılan bir
değişiklik geri dönen hiçbir ziyaretçiye ulaşmaz.

## Değiştirilmesi hesapları bozan üç şey

1. **`AUTH_IDENTITY_SECRET`** — sentetik Supabase e-posta eşlemesi bundan
   türetiliyor. Kimlik göçü yapmadan asla döndürülmez.
2. **`identity_email` içindeki alan adı** (`@users.movieboxd.invalid`) —
   marka MOVIENOTES olarak değişti ama bu dize sabit kalmak zorunda. E-posta
   her girişte kullanıcı adından yeniden üretiliyor; alan adını değiştirmek var
   olan bütün hesapların Auth kaydını bulunamaz hâle getirir.
3. **`/static` URL öneki** — yukarıda anlatıldığı gibi.

`SUPABASE_KEY` (service-role) tarayıcıya asla verilmez; yalnızca backend kullanır.

## Günce aktarımı

Kaynak `/username/films/reviews/` sayfaları: yalnızca yorumlu kayıtları
listeliyor, sayfa başına on iki tane. RSS kullanılmıyor çünkü son ~50 *izleme*
kaydını taşıyor — art arda elli film yorumsuz loglanırsa yeni yazılan yorum
oradan görünmez oluyor.

Uzun yorumlar liste sayfasında `…` ile kırpılıyor (ölçülen bir örnek: 603
karakter görünüyor, tamamı 1254), o yüzden yalnızca kırpılanlar için
`/s/full-text/viewing:<id>/` ucu ayrıca çağrılıyor.

**Saatlik tarama.** Uygulama açılışında bir zamanlayıcı başlıyor
(`_diary_refresh_loop`), saatte bir sırası gelen üyeleri tarıyor: üye başına tek
istek, ilk sayfadaki en yeni `DIARY_SCAN_ENTRIES` (3) yorumlu kayıt. Ayrı bir
işçi süreç yok; UptimeRobot servisi ayakta tuttuğu için tik de dönüyor.

**Ölçek.** Koş başına maliyeti belirleyen şey kaydın sayısı değil üye sayısı:
bir sayfadan üç kayıt okumakla on iki kayıt okumak aynı isteği harcıyor. O
yüzden sınır üyede: `DIARY_SCAN_MEMBERS_PER_RUN` (20) koş başına istek sayısını
üyelik büyüse de sabit tutuyor. Sabit bütçeyi gerçekten yazan üyelere ayıran
şey geri çekilme: `users.diary_idle_streak` ardışık kaç taramanın boş geçtiğini
sayıyor ve eşiği ikiye katlıyor (`DIARY_SCAN_MIN_HOURS` 1 saatten
`DIARY_SCAN_MAX_HOURS` 12 saate kadar), ilk yeni kayıtta tabana dönüyor. Yazan
üye her saat, yıllardır yazmayan üye günde bir taranıyor. Bütçe yetmediğinde tek
sonuç kaydın biraz geç düşmesi; akış penceresi en dar yerde yedi gün olduğu için
görünürlüğü etkilemiyor.

Üye başına yalnız üç kayıt okumanın bir bedeli var: bir üye aynı saat içinde
üçten fazla yorum yazarsa fazlası o taramada atlanıyor ve bir daha bakılmıyor.
Toplu tarama (`scripts.import_diary`) çalıştığında bu boşluklar kapanıyor.
Güncel varsayılan saatte 24 üyedir; bu, 133 üyelik mevcut havuzda en geç birkaç
saat içinde yeniden kontrol anlamına gelir. Sayıyı büyütmek fazladan istek
getirmiyor, `DIARY_SCAN_ENTRIES` yeterli.

## Bildirimler

Tarayıcı bildirimleri profil ayarından izin verildiğinde takip, Blend ve sinema
gündemi olayları cihazda da görünür. Vizyon programı yenilendiğinde, izleme
listesi veya zevk profiliyle eşleşen üyeler haftada en fazla bir kez bildirim
alır. Her gün `NIGHTLY_PICK_HOUR` (varsayılan Türkiye saatiyle 22.00) anında
izlenmemiş yerel katalogdan bir film seçilir; aynı günün teslimi `event_key`
ile tekilleştiği için Render yeniden başlasa bile ikinci bildirim gönderilmez.

**Yeni üyenin arşivi.** Kayıt sırasında değil, üye uygulamaya girdikten sonra
taranıyor: akış açıldığında arka planda bir iş tetikleniyor ve koş başına
`DIARY_BACKFILL_MEMBERS_PER_RUN` (2) üye × `DIARY_BACKFILL_PAGES_PER_RUN` (3)
sayfa ilerliyor — üye başına yaklaşık 36 kayıt, yeniden eskiye doğru, profil
doldukça görünerek. Onboarding'e bağlanmamasının sebebi:
yüzlerce sayfalık bir tarama kayıt akışını bekletemez.

Nerede kalındığı `users.diary_backfill_page` içinde, dolayısıyla süreç yeniden
başlasa da tarama kaldığı yerden devam ediyor; arşiv bittiğinde
`diary_backfilled_at` doluyor ve üye kuyruktan çıkıyor. Bir sayfa okunamazsa
ilerleme yazılmıyor, sonraki koş aynı sayfadan yeniden deniyor. Saatlik döngü de
aynı işi tetikliyor, böylece kimse uygulamaya girmese bile kuyruk ilerliyor.

```bash
PYTHONPATH=backend python -m scripts.import_diary                  # ne olacağını göster
PYTHONPATH=backend python -m scripts.import_diary --apply          # bütün arşiv
PYTHONPATH=backend python -m scripts.import_diary --apply --limit 5 --pause 5
```

Dört kural içe aktarmanın kendisinde:

- **Bir kez düşer.** Kaydın kimliği sayfadaki `viewing:<id>`; `posts.source_key`
  üzerindeki tekil indeks aynı kaydı ikinci kez eklemiyor.
- **Silinen geri gelmez.** Silme yumuşak olduğu için satır ve anahtarı duruyor;
  tekrar çalıştırmak onu diriltmiyor.
- **Sıra izlenme günü.** Kayıt, akışta izlendiği günün tarihiyle yer alıyor.
- **Yalnız yorumlu kayıt.** Cümlesi olmayan izleme kaydı akışa girmiyor; puan
  tek başına okunacak bir şey taşımıyor.

Takip grafiği de aynı şekilde bir kez tohumlanır:
`PYTHONPATH=backend python -m scripts.seed_follows --apply`.

### Akıştaki pencere

Arşivin tamamı içeri giriyor ama keşif akışlarının bir ufku var
(`FEED_DIARY_WINDOW_DAYS`, `FEED_FOLLOWING_WINDOW_DAYS`). Pencere `created_at` üzerinden,
o alan içe aktarımda **izlenme günü** oluyor — kaydın kendi tarihi, çekildiği an
değil.

| Yüzey | Pencere |
|---|---|
| Topluluk | Son 7 gün |
| Takip ettiklerin | Son 30 gün |
| Notların, üye profili, film sayfası | Tamamı |

İki keşif akışının penceresi farklı çünkü iki farklı iş yapıyorlar: topluluk
bütün üyelerden "bu hafta ne konuşuluyor"u gösteriyor, takip ettiklerin ise
seçilmiş birkaç kişiyi — orada bir hafta çoğu zaman boş bir sayfa demek.

Pencere yalnızca `source = 'letterboxd'` satırlarına işliyor; uygulamada
yazılan not eskise de akışta kalıyor. Bir hafta sonra kaybolan bir not, üyenin
uygulamaya yazdığı yazıyı silmek gibi okunurdu.

Pencere **gizliyor, silmiyor**: eski kayıtları temizleyen bir iş yok, satırlar
veritabanında duruyor ve profil ile film sayfalarında görünmeye devam ediyor.

Aynı ayrım gövde sınırında da var: uygulamada yazılan not 420 karakter (ürün
kararı, API katmanı uyguluyor), içe aktarılan yorum 10.000'e kadar. Tek bir 420
sınırı birkaç bin karakterlik yorumları sessizce kırpıyordu.

## Sinema gündemi

`GET /api/bulletin` haftalık bir kart döndürür. Perdedeki her film listede yer
alır — kart bir program, kısa liste değil — ama sıralama üyeyle olan bağın
gücüne göre:

| Sıra | Bağ | Kartın notu |
|---|---|---|
| 0 | İzleme listesinde | "İzleme listende" |
| 1 | İzlemiş ve 4+ vermiş | "Bu filme 4.5 vermiştin" |
| 2 | İzlemiş | "İzlemiştin" |
| 3 | Zevkine uyuyor (yönetmen/tür) | "… filmi" / "… tarafında" |
| 4 | Alakasız | — |

İzlemiş olmak, "yönetmenini seviyor"dan daha kesin bir bağ; o yüzden düşük puan
verilmiş bir film bile zevk tahmininin önünde geliyor, ama notu dürüst kalıyor.
İlk dört kademe "öne çıkan" sayılıyor ve bunu kartın kendisi `highlight` alanıyla
taşıyor — arayüz sabit bir eşik tutmuyor.

Program satırları `screenings` tablosunda, iki katman yazıyor. Vizyon katmanı
`BULLETIN_REGION` için TMDb `now_playing` (sözleşmeli, bozulamaz). Repertuvar
katmanı mekân programlarını `venues.config` içinde saklanan seçicilerle
ayrıştırıyor — yani sitesini yenileyen bir mekân satır düzenlemesi, dağıtım
değil. Başarısız olan mekân `last_error` yazar, bülten kalanla birlikte gider.
Bağlantılar yayına girmeden önce doğrulanıyor: 404 veya yumuşak-404 dönen satır
mekânın program sayfasına düşürülür ve arayüzde "Sinema programı" olarak
etiketlenir.

Servis ayakta olduğu sürece saatlik hafif bir zamanlayıcı program alımını
dürtüyor. Mekân başına veritabanı kirası (`claim_venue_ingest`) dış kaynakların
`BULLETIN_INGEST_INTERVAL_HOURS` başına en fazla bir kez okunmasını garanti
ediyor. Kişiselleştirilmiş özetler `BULLETIN_DIGEST_TTL_HOURS` sonra (varsayılan
altı saat) düşüyor; iki tazelemeyi kaçırmış satırlar "hâlâ vizyonda" diye
gösterilmek yerine saklanıyor.

Türkçe dağıtım başlıkları `tmdb_id` üzerinden eşleşiyor; önce `tr-TR`, sonra
İngilizce aranıyor. Güvenle çözülemeyen hiçbir şey tahmin edilmiyor,
`unresolved` veya `ambiguous` kalıyor:

```bash
PYTHONPATH=backend python -m scripts.resolve_screenings            # eşleşmeyenler kuyruğu
PYTHONPATH=backend python -m scripts.resolve_screenings --venues   # mekân sağlığı
PYTHONPATH=backend python -m scripts.resolve_screenings --map "Sonbahar Sonatı=4174"
```

Özellik kapalı gelir: `BULLETIN_ENABLED=true` açar, `venues.active` tek bir
mekânı kapatır.

## Mektuplar

Mektuplar hesaba bağlıdır: kullanıcı giriş yaptığı her cihazda aynı mektupları
görür. Önceki tasarımda anahtar yalnızca tarayıcının IndexedDB deposunda
durduğu için ikinci bir cihaza girmek hem eski mektupları okunamaz yapıyor hem
de sunucudaki public key'i değiştirerek ilk cihazı bozuyordu; bu yüzden uçtan
uca şifreleme kaldırıldı.

Gizliliği artık erişim kuralları sağlıyor: bir mektubu yalnızca gönderen ve
alıcı listeleyebilir, engelleme iki taraftaki mektupları siler, admin raporu
gövdeyi hiç seçmez. Servis mektupları teknik olarak okuyabilir — bu, ürünün
verdiği sözün sınırıdır. Eski şifreli satırlar veritabanında kalır ama artık
kimse tarafından açılamaz; arayüz bunu açıkça söyler.

Mektup kutusu varsayılan olarak açıktır; izole kalmak isteyen profilinden
kapatır. Var olan hesaplar bir kereliğine `scripts.open_letterboxes --apply` ile
açıldı; toplu güncelleme şemada değil, çünkü şema tekrar tekrar uygulanıyor ve
kapatanların tercihini sessizce geri alırdı.

Mektup yollamak için kullanıcının kendi kutusunun da açık olması gerekiyor
(`letter_sender_closed`); kapalıysa arayüz kutuyu açmayı öneren bir modal
gösterir. Gerekçesi: kapalı bir hesaptan gönderilen mektup, alıcının cevap
veremediği tek yönlü bir kanal olur. Bu kural gelmeden önce yollamış ve kutusu
kapalı kalmış hesapları `scripts.fix_letter_senders` listeler, `--apply` açar.

## Kurulum çağrısı

Uygulamayı ana ekrana ekleme daveti, üye uygulamaya girdiğinde çıkıyor;
onboarding'i yeni bitiren üye de görsün diye `finishOnboarding` sonunda 1,5
saniye gecikmeyle çağrılıyor (profil ilk kez boyansın, modal üstüne binmesin).
Onboarding ekrandayken hiç açılmıyor: orası kilitli bir tam ekran akış.

Platforma göre iki hâli var:

| Platform | Davranış |
|---|---|
| Chrome / Edge (Android, masaüstü) | `beforeinstallprompt` yakalanıyor, diyalogdaki düğme kurulumu başlatıyor. |
| iOS (Safari, Chrome, Edge — hepsi WebKit) | Olay hiç gelmiyor ve sayfadan kurulum başlatılamıyor; düğme yerine paylaş menüsü adımları gösteriliyor. |

iOS'ta "kuruldu" sinyali de yok — üye ana ekrana eklese bile Safari'de açtığında
`navigator.standalone` false. O yüzden kapatma `IOS_INSTALL_HINT_DAYS` (30 gün)
boyunca hatırlanıyor, yoksa davet her girişte yeniden çıkardı.

## Gizlilik

Tek bir anahtar var: **kilitli hesap**. Kapalıyken profil herkese açıktır ve
üye Sinefil Sineması'nda görünür; açıkken profil yalnızca onaylanan takipçilere
görünür ve üye keşif yüzeylerinden çıkar. Görünürlük ile kilit ayrı ayrı
yönetilen iki kavram değildir; ikisi aynı anahtara bağlıdır, böylece kullanıcı
uygulamayı sosyal alandan tamamen izole de kullanabilir.

`DELETE /api/data` giriş yapılmış Supabase Auth kimliğini, profil/zevk/Fav 4
satırlarını ve kullanıcı adına bağlı önbellekleri siler. Paylaşılan TMDb
metadata'sı kişisel değildir ve kalır.

## Yerel yönetim raporu

Depo, toplam hesap kullanımı için yalnızca yerelde çalışan, service-role bir
rapor içerir. Kasten bir HTTP admin uç noktası değildir; parola, token, ham
olay metadata'sı veya film satırı asla yazdırmaz:

```bash
PYTHONPATH=backend python -m scripts.admin_users
PYTHONPATH=backend python -m scripts.admin_users --username enesaysu --json
PYTHONPATH=backend python -m scripts.admin_users --include-non-active
```

Satırlar en son etkinliğe göre sıralı. Her hesap için: Sinefil Sineması
görünürlüğü, mektup kutusu tercihi, gönderilen/alınan/okunmamış mektup sayısı,
son gönderim tarihi, tarama ilerlemesi, izlenen ve izleme listesi sayıları,
Blend gönderilen/alınan/tamamlanan, öneri başarı oranı, rastgele seçimler,
senkron istekleri, girişler ve son etkinlik. Mektuplar yalnızca sayılır: rapor
ne gövde ne film hediyesi ne alıcı seçer.

Komutu kullanmadan önce güncel `backend/supabase/schema.sql` Supabase SQL
Editor'da çalıştırılmalı; eski bir rapor fonksiyonu mektup ve görünürlük
kolonlarını `-` olarak yazdırır.

## API

| Grup | Uç noktalar |
|------|-------------|
| Servis | `GET /api/health`, `/api/readiness`, `/api/public/stats`, `/api/share/image` |
| Auth | `POST /api/auth/register/start`, `register/verify`, `login`, `refresh`, `logout`, `password-reset/start`, `password-reset/finish`; `GET /api/auth/me`; `DELETE /api/data` |
| Push | `GET /api/push/public-key`, `POST /api/push/subscriptions` |
| Profil | `GET /api/profile/me`, `social-stats`, `sync-status`, `stats`, `watched`, `recent`, `film-overview`, `directors/{rank}/films`, `top-films`; `PUT /api/profile/top-films`; `POST /api/profile/sync`, `watchlist/check`, `onboarding-complete`, `discovery-settings`, `privacy-settings` |
| Akış | `GET /api/feed`, `/api/feed/films`, `/api/feed/trending`, `/api/films/{slug}`, `/api/posts/{id}`; `POST /api/posts`, `/api/posts/{id}/replies`, `/like`, `/report`; `DELETE /api/posts/{id}`, `/like` |
| Sosyal | `GET /api/users/search`, `/api/users/{username}`, `/followers`, `/following`, `/api/notifications`, `/api/notifications/unread-count`; `POST /api/users/{username}/follow`, `follow-request`, `block`, `report`; `DELETE .../follow`, `.../block` |
| Öneri | `POST /api/recommend`, `POST /api/random`, `GET /api/bulletin?city=` |
| Sinefil Sineması | `GET /api/sinefil-alani`, `/api/sinefil-alani/{username}/personality` |
| Mektuplar | `GET /api/letters`, `settings`, `followers`, `unread-count`, `send-status`, `recipients/{username}`; `POST /api/letters`, `receiving`, `{id}/read`; `DELETE /api/letters/{id}`, `legacy` |
| Blend | `POST /api/blends/requests`, `{id}/decision`, `{id}/result`, `{id}/refresh`; `GET /api/blends`, `pending-count`, `requests/{id}/result`; `DELETE /api/blends/requests/{id}`, `/api/blends/{id}` |

Auth yapılandırılmışken Taste ve Random giriş yapılmış kullanıcı adı ve
double-submit CSRF token'ı istiyor. Auth ve ağır rotaların ayrı IP bütçeleri var.

## API anahtarları

- **TMDb** — <https://www.themoviedb.org/settings/api> (ücretsiz).
  Zenginleştirme katmanını açar; öneriler belirgin biçimde iyileşir.
- **OpenAI** — <https://platform.openai.com/api-keys>. LLM yeniden sıralama ve
  zevk metinleri. Model `OPENAI_MODEL` ile ayarlanır.
- **Supabase** — hesaplar için zorunlu; profilleri ve önbellekleri dağıtımlar
  arasında kalıcı kılar.

## Notlar ve sınırlar

- **Letterboxd'un resmî API erişimi kısıtlı ve öneri projelerine kapalı.** Bu
  yüzden scraper herkese açık HTML'i ayrıştırıyor; `backend/app/scraper.py`
  içindeki seçiciler Letterboxd işaretlemesini değiştirirse kırılabilir.
  İstekler arasında nazik bir gecikme (`SCRAPE_DELAY`) var, proxy veya ücretli
  scraping servisi kullanılmıyor. Ölçek büyütmeden önce Letterboxd'un kullanım
  şartlarını kontrol edin. Günlük bir canary (`scripts.check_scraper`, GitHub
  Actions) ayrıştırıcının sağlığını izliyor.
- Profiller stale-while-revalidate önbellekle çalışıyor. İlk sayfa parmak izi
  değişmediyse tam tarama atlanıyor; tam tarama en az haftalık yine koşuyor.
- Aynı anda gelen özdeş scrape'ler birleştiriliyor, TMDb paylaşılan sınırlı bir
  havuz kullanıyor.
- TMDb metadata'sı yerel SQLite L1 ve toplu Supabase L2 kullanıyor. Çözülmüş
  posterler ve yönetmen portreleri paylaşılan varlık tablolarına yükseltiliyor;
  bilinen bir slug/TMDb id film aramasını atlıyor.
- Bütün Letterboxd HTML istekleri tek bir uyarlanır süreç bütçesini paylaşıyor.
  403/429 trafiği seri hâle getirip soğuma devresi açıyor; sürekli başarı
  eşzamanlılığı kademeli olarak geri veriyor. Tam profil taramaları ek olarak
  Supabase kirası kullanıyor, böylece iki Render süreci aynı işe sahip olamaz.
- Öneri sıralaması en son 100 izlenen filmi, puan farkındalı negatif sinyalleri
  ve MMR çeşitliliğini kullanıyor. En sevilen üç yönetmen sınırlı bir ikincil
  destek alıyor. LLM bağlamı 3.5+ puanları skorlarıyla içeriyor; puansız geçmiş
  yalnızca hiç puan verisi yoksa kullanılıyor.
- Rastgele mod izleme listesinden bağımsız ve sınırsız. Havuzu, diğer üyelerin
  izleyip bu hesabın izlemediği filmler (`community_random_films`); üyelikte
  kullanılabilir geçmiş yoksa TMDb Discover'a düşüyor. Letterboxd'u hiç
  taramadığı için paylaşılan analiz bütçesi yerine kendi kovasını kullanıyor.
- Blend, 0–100 kalibre edilmiş benzerlik skoruyla birlikte bağımsız bir veri
  kapsamı göstergesi (düşük/orta/yüksek) döndürüyor.
- Supabase olmadan önbellekler `data/cache.sqlite3` içinde yaşıyor ve kalıcı
  diski olmayan sunucularda uçucudur.

## Dağıtım

Render servisinin **Root Directory** ayarı `backend` olmalı. Komutlar o zaman
depo köküne değil `backend/`'e göre koşuyor, `app` paketi `PYTHONPATH`
gerektirmeden içe aktarılıyor ve build/start komutlarının hiçbiri özel bir şey
yapmıyor:

```
Build:  pip install -r requirements.txt
Start:  uvicorn app.main:app --host 0.0.0.0 --port $PORT --log-level warning
```

Depo tamamen klonlanıyor, `frontend/` de `STATIC_DIR` tarafından depo köküne
göre çözüldüğü için yerinde bulunuyor. `render.yaml` aynı yapılandırmayı
taşıyor ama elle oluşturulmuş (blueprint olmayan) bir servis bu dosyayı
okumaz — panel ayarı yine de yapılmalı.

## Hesap yayına alma

1. `backend/supabase/schema.sql` dosyasını Supabase SQL Editor'da çalıştır.
2. Render'da `SUPABASE_URL`, service-role `SUPABASE_KEY`, `SUPABASE_ANON_KEY`
   ve sabit bir `AUTH_IDENTITY_SECRET` (`openssl rand -hex 32`) ayarla.
3. Dağıt. `/api/health` `auth_enabled: true` bildirmeli.
4. `/api/readiness` `status: ready` dönmeli; 503 ya şemanın uygulanmadığını ya
   da Supabase'in erişilemez olduğunu söylüyor.
5. Test kullanıcısı kaydet, kodu herkese açık Letterboxd bio'suna koy, doğrula,
   giriş yap ve ilk profil senkronunu bekle.

## Açık işler

- `watched_rank` öneri hattı ve Blend tarafında *tazelik* gibi kullanılıyor;
  aslında Letterboxd'un liste sırası. Gerçek izleme tarihi günce kayıtlarında
  var, sıralama oraya bağlanmalı.
- Supabase RLS politikaları yalnızca gereken role/operasyona indirilmeli.
- Google fontları self-host/subset edilip kritik olanlar preload edilmeli.
- `criterion-closet-bg.jpg` için AVIF/WebP varyantı üretilmeli
  (`frontend/movienotes-mark.png` de favicon olarak 232 KB — küçültülebilir).
- İki üyeli gerçek Supabase üzerinde RLS/state-machine entegrasyon testi ve
  login → senkron → inbox → Blend kabulü için browser E2E testi.
- Öneri için golden dataset ve offline eval; eval'lerin CI'a eklenmesi.
- Letterboxd hesabı olmayan kullanıcı için 10 filmlik swipe onboarding.
