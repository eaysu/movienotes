import { $, escapeHTML, safeImageURL, letterboxdFilmURL } from './dom.js?v=20260902.15';
import {
  API_BASE,
  apiJSON,
  assertStreamResponse,
  beginApiRequest,
  cancelActiveApiRequest,
  finishApiRequest,
  scrapeErrorMessage,
  streamErrorMessage,
} from './api.js?v=20260919.2';
import {
  cookieValue,
  csrfHeaders,
  setAuthMessage,
  setAuthMode,
  setPasswordVisibility,
} from './auth.js?v=20260919.2';
import { directorAvatar, directorFilmGrid, directorFilmTile } from './profile.js?v=20260919.2';
import { animateScore, getScoreInfo } from './blend.js?v=20260902.15';
import { createRecommendationCards } from './recommendations.js?v=20260919.2';
import {
  getLocale,
  initI18n,
  localePreference,
  setLocalePreference,
  t,
} from './i18n.js?v=20260919.3';

initI18n();

const uiLocale = () => (getLocale() === 'en' ? 'en-US' : 'tr-TR');

let _shareCardsModule;
function loadShareCardsModule() {
  if (!_shareCardsModule) {
    _shareCardsModule = import('./share-cards.js?v=20260919.2');
  }
  return _shareCardsModule;
}

// Chrome exposes this one-shot event only for a valid, installable PWA. We
// keep it until the signed-in shell is ready, then invite the user ourselves
// instead of relying on Chrome to choose a moment for the browser infobar.
let _deferredInstallPrompt = null;

function isInstalledApp() {
  return window.matchMedia?.('(display-mode: standalone)').matches || Boolean(window.navigator.standalone);
}

// iPhone/iPad. Safari, Chrome, Edge — hepsi iOS'ta WebKit üzerinde çalışıyor,
// dolayısıyla hiçbiri `beforeinstallprompt` göndermiyor ve hiçbiri kurulumu
// kendisi başlatamıyor. Tarayıcıya değil platforma bakmak bu yüzden doğru.
// iPadOS 13+ kendini "Macintosh" diye tanıtıyor; dokunma noktası sayısı ayırıyor.
function isIOS() {
  const ua = navigator.userAgent || '';
  return /iPhone|iPad|iPod/i.test(ua)
    || (/Macintosh/.test(ua) && (navigator.maxTouchPoints || 0) > 1);
}

// Kurulum çağrısının ne zaman kapatıldığı. iOS'ta "kuruldu" sinyali yok:
// kullanıcı ana ekrana eklese bile Safari'de açtığında `standalone` false,
// yani hatırlamazsak çağrı her girişte yeniden çıkardı.
const IOS_INSTALL_HINT_KEY = 'mb_ios_install_hint';
const IOS_INSTALL_HINT_DAYS = 30;

function iosHintSilenced() {
  try {
    const at = Number(localStorage.getItem(IOS_INSTALL_HINT_KEY) || 0);
    return at > 0 && Date.now() - at < IOS_INSTALL_HINT_DAYS * 86400000;
  } catch (_) {
    return false;
  }
}

function showInstallAppDialog() {
  const dialog = $('dialog-install-app');
  if (!_account || isInstalledApp() || dialog.open) return;
  // Onboarding kilitli bir tam ekran akış: Chrome `beforeinstallprompt`'u geç
  // gönderirse bu modal onun üstüne açılıyordu. Kurulum çağrısı bekleyebilir.
  if (_shownView === 'onboarding') return;
  const ios = isIOS();
  if (!ios && !_deferredInstallPrompt) return;
  if (ios && iosHintSilenced()) return;
  // iOS'ta kurulumu tarayıcı başlatamıyor: düğme yerine paylaş menüsü adımları.
  $('install-ios-steps').classList.toggle('hidden', !ios);
  $('btn-install-app').classList.toggle('hidden', ios);
  $('btn-install-dismiss').textContent = ios ? 'Anladım' : 'Şimdi değil';
  $('btn-install-dismiss').classList.toggle('flex-1', ios);
  dialog.showModal();
}

// Kapatıldığı an damgalanıyor: iOS'ta kurulumun gerçekleştiğini anlamanın bir
// yolu yok, o yüzden "gördüm" bilgisini kullanıcının kapatması veriyor.
$('dialog-install-app').addEventListener('close', () => {
  if (!isIOS()) return;
  try {
    localStorage.setItem(IOS_INSTALL_HINT_KEY, String(Date.now()));
  } catch (_) {
    /* özel sekmede depolama kapalı olabilir; çağrı yine çıkar, sorun değil */
  }
});

async function registerMovienotesServiceWorker() {
  if (!('serviceWorker' in navigator)) return null;
  try {
    return await navigator.serviceWorker.register('/push-sw.js');
  } catch (_) {
    return null;
  }
}

async function requestMovienotesInstall() {
  if (!_deferredInstallPrompt) return;
  const button = $('btn-install-app');
  button.disabled = true;
  try {
    await _deferredInstallPrompt.prompt();
    await _deferredInstallPrompt.userChoice;
  } finally {
    _deferredInstallPrompt = null;
    button.disabled = false;
    $('dialog-install-app').close();
  }
}

window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  _deferredInstallPrompt = event;
  showInstallAppDialog();
});

window.addEventListener('appinstalled', () => {
  _deferredInstallPrompt = null;
  $('dialog-install-app')?.close();
});

registerMovienotesServiceWorker();

// ── Cinema facts & quotes ──────────────────────────────────────────────────
const CINEMA_ITEMS = [
  // Yönetmen sözleri
  { type: 'quote', text: 'Bir hikayenin başı, ortası ve sonu olmalıdır — ama bu sırayla olmak zorunda değil.', author: 'Jean-Luc Godard' },
  { type: 'quote', text: 'Şimdiye kadar çekilmiş her filmden çalarım.', author: 'Quentin Tarantino' },
  { type: 'quote', text: 'Sinema, kadraja neyin girip neyin dışarıda kaldığıdır.', author: 'Martin Scorsese' },
  { type: 'quote', text: 'Film bir rüya şeridedir.', author: 'Orson Welles' },
  { type: 'quote', text: 'Her sinemaya gittiğimde sihir yaşanır — film ne hakkında olursa olsun.', author: 'Steven Spielberg' },
  { type: 'quote', text: 'Yazılabilecek ya da düşünülebilecek her şey filme alınabilir.', author: 'Stanley Kubrick' },
  { type: 'quote', text: 'Film yapmak madene inmek gibidir. Bir kez girdin mi, çıkamazsın.', author: 'Federico Fellini' },
  { type: 'quote', text: 'Umudu olmayanlar için güzel filmler yapmak istiyorum.', author: 'Werner Herzog' },
  { type: 'quote', text: 'Filmcilikte kural yoktur. Yalnızca günahlar vardır.', author: 'Frank Capra' },
  { type: 'quote', text: 'Film bir yanılsamadır. Şöhret geçicidir. İnanç yok edilemez.', author: 'Ingmar Bergman' },
  { type: 'quote', text: 'Sinema, dünyanın en güzel sahtekârlığıdır.', author: 'Jean-Luc Godard' },
  { type: 'quote', text: 'İyi bir film sizi durdurup düşündürür. Harika bir film sizi değiştirir.', author: 'Roger Ebert' },
  { type: 'quote', text: 'Zaman, sinemanın hammaddesidir.', author: 'Andrei Tarkovsky' },
  { type: 'quote', text: 'Sessiz filmler hiçbir zaman gerçekten ölmedi — sadece konuşmayı öğrendi.', author: 'Alfred Hitchcock' },
  { type: 'quote', text: 'Sinema gerçeği saniyede yirmi dört kare hızında yansıtır.', author: 'Jean-Luc Godard' },
  { type: 'quote', text: 'Hayat bir filmdir; ölüm ise yönetmendir.', author: 'Luis Buñuel' },
  { type: 'quote', text: 'İzleyici sinema salonuna girdiğinde gerçeklikten kaçmak için gelir. Biz ona başka bir gerçeklik sunarız.', author: 'Pedro Almodóvar' },
  { type: 'quote', text: 'Benim için sinemada en önemli şey sessizliktir.', author: 'David Lynch' },
  { type: 'quote', text: 'Bir sahneyi çekebilmek için önce onu hissedebilmem gerekir.', author: 'Wong Kar-wai' },
  { type: 'quote', text: 'Korku en iyi motivasyondur. Ben sürekli korku içinde çalışırım.', author: 'James Cameron' },
  { type: 'quote', text: 'Kurgu olmadan sinema sadece fotoğraftır.', author: 'Sergei Eisenstein' },
  { type: 'quote', text: 'En iyi kamera açısı, izleyicinin fark etmediği açıdır.', author: 'Akira Kurosawa' },
  { type: 'quote', text: 'Bir film, izledikten sonra sizi değiştirmiyorsa neden var olsun?', author: 'Abbas Kiarostami' },
  { type: 'quote', text: 'Aktörler karakterleri oynamaz; karakterler aktörlerin içinden konuşur.', author: 'Stanislavski' },
  { type: 'quote', text: 'Renk, sinemada müzik gibidir — bilinçaltına ulaşır.', author: 'Vittorio Storaro' },
  // Sinema bilgileri
  { type: 'fact', text: '1951\'de kaydedilen "Wilhelm Çığlığı" adlı ses efekti, 400\'den fazla film ve dizide kullanıldı.' },
  { type: 'fact', text: '2001: A Space Odyssey, gerçek Ay yürüyüşünden tam 15 ay önce vizyona girdi.' },
  { type: 'fact', text: 'Psycho\'nun duş sahnesinde kan olarak çikolatalı şurup kullanıldı — siyah beyaz görüntüde çok daha iyi duruyordu.' },
  { type: 'fact', text: 'Inception\'ın sonunda topaç hiçbir zaman durmadan önce kesilir. Nolan belirsizliği kasıtlı bıraktı.' },
  { type: 'fact', text: 'Parasite (2019), Oscar tarihinde En İyi Film ödülünü kazanan ilk İngilizce olmayan film oldu.' },
  { type: 'fact', text: 'The Lord of the Rings üçlemesi için 48.000\'den fazla parça özel zırh üretildi.' },
  { type: 'fact', text: 'Jaws (1975) "yaz gişe bombası" kavramını icat etti — stüdyolar daha önce bu şekilde sezon odaklı dağıtım yapmamıştı.' },
  { type: 'fact', text: '"Jedi" kelimesi, Japonca\'da samuray dönemi dramalarını ifade eden "Jidaigeki"den geliyor.' },
  { type: 'fact', text: 'Mad Max: Fury Road\'un tüm senaryosu 3,5 sayfaya sığıyor. Filmin yaklaşık %90\'ı aksiyon.' },
  { type: 'fact', text: 'Akira Kurosawa\'nın renk körü olduğu söylenir — oysa renk filmleri cesur ve titiz paletleriyle övülür.' },
  { type: 'fact', text: 'Metropolis (1927), döneminin herhangi bir Alman filminden iki kat pahalıya mal oldu ve stüdyoyu neredeyse iflas ettirdi.' },
  { type: 'fact', text: 'Heath Ledger, The Dark Knight\'taki Joker makyajını kendisi tasarlayıp kendisi uyguladı.' },
  { type: 'fact', text: 'Stanley Kubrick, Dr. Strangelove\'ın finalinde büyük bir pasta dövüşü sahnesi çekti; ama filmi fazla ciddiye alarak sahneden vazgeçti.' },
  { type: 'fact', text: 'Schindler\'s List çekimleri sırasında Spielberg\'in moralini tazelemek için arkadaşı Robin Williams her gün arayıp onu güldürürdü.' },
  { type: 'fact', text: 'Casablanca\'nın sonu çekim sırasında henüz yazılmamıştı — oyuncular hangi karakterin gideceğini bilmiyordu.' },
  { type: 'fact', text: 'Jurassic Park\'taki dinozor kükremeleri fil, kaplan ve yavaşlatılmış bebek seslerinin karışımından oluşuyor.' },
  { type: 'fact', text: 'Buster Keaton tüm tehlikeli sahneleri kaskadör kullanmadan kendisi yapardı ve hayatını defalarca riske attı.' },
  { type: 'fact', text: 'Her yıl dünyada yaklaşık 11.000 uzun metrajlı film üretiliyor; Bollywood tek başına Hollywood\'dan daha fazlasını çekiyor.' },
  { type: 'fact', text: 'Sinema tarihinin ilk "jump cut"ını Georges Méliès kaza sonucu keşfetti: kamera durdu, hayat devam etti.' },
  { type: 'fact', text: 'No Country for Old Men\'de arka planda hiç film müziği yok — Coen kardeşler gerilimi yalnızca sessizlik ve ses efektleriyle inşa etti.' },
  { type: 'fact', text: 'The Shining, Steadicam teknolojisinin sinemadaki ilk büyük kullanımlarından biridir.' },
  { type: 'fact', text: 'Türk sinemasının ilk filmi, 1914\'te çekilen Ayastefanos\'taki Rus Abidesinin Yıkılışı\'dır.' },
  { type: 'fact', text: 'Gone with the Wind (1939), enflasyona göre ayarlandığında tüm zamanların en yüksek hasılatlı filmi olmaya devam ediyor.' },
  { type: 'fact', text: 'The Dark Knight\'taki Joker\'in "kalem numarası" sahnesi senaryoda yoktu — Heath Ledger o an doğaçlama yaptı.' },
  { type: 'fact', text: 'Pixar\'ın ilk tam uzun metrajlı animasyonu Toy Story (1995), tamamıyla bilgisayarla üretilen ilk feature film olma özelliğini taşıyor.' },
  { type: 'fact', text: 'Alfred Hitchcock, Psycho\'da kendi filminin fragmanını bizzat anlatarak tanıttı — bu tanıtım biçimi döneminde benzersizdi.' },
  { type: 'fact', text: 'Bilinen en eski film "Roundhay Garden Scene" (1888) yalnızca ~2 saniye sürer.' },
  { type: 'fact', text: 'Boyhood, aynı oyuncularla 12 yıl boyunca, yılda birkaç gün çekilerek tamamlandı.' },
  { type: 'fact', text: 'Whiplash\'in tamamı yalnızca 19 günde çekildi.' },
  { type: 'fact', text: 'İlk Godzilla (1954) kostümü yaklaşık 100 kilogramdı.' },
  { type: 'fact', text: 'Yüzüklerin Efendisi üçlemesi tek seferde, yaklaşık 438 gün süren bir çekimle tamamlandı.' },
  { type: 'fact', text: 'Toy Story 2 yanlış bir silme komutuyla sunuculardan neredeyse yok oldu; bir çalışanın evindeki yedek kopya kurtardı.' },
  { type: 'fact', text: 'Citizen Kane\'de tavanlar görünsün diye Orson Welles kamerayı yere gömdürdü.' },
  { type: 'fact', text: 'Alien\'ın meşhur sahnesinde oyuncular o kadar kan geleceğini bilmiyordu; şaşkınlıkları gerçek.' },
];

// These are written as English editorial copy, rather than machine-translated
// quotations, so an English onboarding never rotates back into Turkish.
const CINEMA_ITEMS_EN = [
  { type: 'quote', text: 'Cinema is a way of looking closely at the world.', author: 'Movienotes' },
  { type: 'quote', text: 'A great film gives you a new way to notice the familiar.', author: 'Movienotes' },
  { type: 'quote', text: 'The best watchlist is the one you actually keep returning to.', author: 'Movienotes' },
  { type: 'fact', text: 'A film’s mood is built from image, sound, rhythm, and the spaces between them.' },
  { type: 'fact', text: 'Your ratings, favourites, and recent watches each reveal a different side of your taste.' },
  { type: 'fact', text: 'Film posters were one of cinema’s first shared languages: a promise of tone before the lights go down.' },
  { type: 'fact', text: 'A rewatch can change a favourite because you bring a different version of yourself to the film.' },
  { type: 'fact', text: 'Directors, genres, decades, and pacing all leave useful signals in a viewing history.' },
  { type: 'fact', text: 'The most useful recommendation is not the most popular film; it is the one that meets you at the right moment.' },
  { type: 'fact', text: 'A shared watchlist is often the fastest way to discover a new corner of someone else’s taste.' },
];

let _factTimer = null;
let _factPool = [];

function _nextFact() {
  if (!_factPool.length) {
    _factPool = [...(getLocale() === 'en' ? CINEMA_ITEMS_EN : CINEMA_ITEMS)]
      .sort(() => Math.random() - 0.5);
  }
  return _factPool.pop();
}

function _showFact(item) {
  const badge  = $('fact-badge');
  const text   = $('fact-text');
  const author = $('fact-author');
  badge.textContent  = item.type === 'quote' ? t('❝ Söz') : t('🎬 Bilgi');
  text.textContent   = item.text;
  author.textContent = item.author ? `— ${item.author}` : '';
}

function startFactRotation() {
  const card = $('cinema-fact');
  _factPool = [];
  _showFact(_nextFact());
  card.classList.remove('fading');
  _factTimer = setInterval(() => {
    card.classList.add('fading');
    setTimeout(() => {
      _showFact(_nextFact());
      card.classList.remove('fading');
    }, 500);
  }, 5500);
}

function stopFactRotation() {
  clearInterval(_factTimer);
  _factTimer = null;
}

const RANK_COLORS = [
  'text-primary-container',
  'text-secondary-container',
  'text-tertiary-container',
];

// ── Mode (taste | random) ──────────────────────────────────────────────────
let currentMode = 'taste';
let _randomFilms = [];
let _randomAttempt = 0;

const _MODE_BTN_BASE = 'flex-1 flex items-center justify-center gap-1.5 py-3 rounded-lg font-label-md text-label-md uppercase tracking-wider transition-all duration-200 border';
const _MODE_BTN_OFF  = `${_MODE_BTN_BASE} border-outline-variant/30 bg-surface-variant text-on-surface-variant`;

const _BTN_ACTIVE_TASTE  = `${_MODE_BTN_BASE} border-transparent bg-primary-container text-black`;
const _BTN_ACTIVE_RANDOM = `${_MODE_BTN_BASE} border-transparent bg-tertiary-container text-on-tertiary-container`;
const _BTN_ACTIVE_BLEND  = `${_MODE_BTN_BASE} border-transparent bg-secondary-container text-on-secondary-container`;

const _RECOMMEND_LABELS = {
  taste:  { icon: 'auto_awesome',  label: 'Öner' },
  random: { icon: 'shuffle',       label: 'Rastgele Seç' },
  blend:  { icon: 'join_inner',    label: 'Blend Oluştur' },
};

function setMode(mode) {
  currentMode = mode;
  $('btn-mode-taste').className  = mode === 'taste'  ? _BTN_ACTIVE_TASTE  : _MODE_BTN_OFF;
  $('btn-mode-random').className = mode === 'random' ? _BTN_ACTIVE_RANDOM : _MODE_BTN_OFF;
  $('btn-mode-blend').className  = mode === 'blend'  ? _BTN_ACTIVE_BLEND  : _MODE_BTN_OFF;

  // Blend'e özel: ikinci input + buton rengi/yazısı
  const isBlend = mode === 'blend';
  $('blend-second-input').classList.toggle('hidden', !isBlend);

  const selected = _RECOMMEND_LABELS[mode] || _RECOMMEND_LABELS.taste;
  const icon = selected.icon;
  const label = mode === 'blend' && _authEnabled ? 'Blend İsteği Gönder' : selected.label;
  $('btn-recommend-icon').textContent = icon;
  $('btn-recommend-label').textContent = label;

  // Buton rengi moda göre değişir
  const btnColors = {
    taste:  'bg-primary-container text-black',
    random: 'bg-tertiary-container text-on-tertiary-container',
    blend:  'bg-secondary-container text-on-secondary-container',
  };
  $('btn-recommend').className = `w-full ${btnColors[mode] || btnColors.taste} py-4 rounded-lg font-label-md text-label-md uppercase tracking-wider hover:opacity-90 active:scale-[0.98] transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed`;

  // Headline & description
  const headlines = {
    taste:  { line1: 'İzleme listende',        accent: 'ne izlemelisin?',       accentColor: 'text-gradient-green',  desc: 'Son izlediklerine ve film zevkine bakarak bu akşam sana iyi gelecek filmi bulur.' },
    random: { line1: 'Watchlist\'inden',        accent: 'sürpriz bir film.',     accentColor: 'text-gradient-blue', desc: 'Watchlist\'inden rastgele bir film seç — maksimum 3 kez şansını dene.' },
    blend:  { line1: 'İki sinefilin',          accent: 'uyum skoru.',           accentColor: 'text-gradient-orange', desc: _authEnabled ? 'Kayıtlı bir kullanıcıya istek gönder. Zevkleriniz yalnızca o kabul ederse karşılaştırılır.' : 'İki Letterboxd kullanıcısının film zevkini karşılaştır, ortak favorileri keşfet.' },
  };
  const h = headlines[mode] || headlines.taste;
  $('idle-headline').childNodes[0].textContent = h.line1 + '\n';
  $('idle-headline-accent').textContent = h.accent;
  $('idle-headline-accent').className = h.accentColor;
  $('idle-description').textContent = h.desc;
}

// ── Profile as home screen: "Bu gece" actions ────────────────────────────
let _profileWatchMode = 'taste';
let _profileBlendTimer = null;

// Uygulama hiyerarşisi: Akış kök sayfa; araç kataloğu mobilde aradaki
// sayfadır. Araç sonucu hiçbir zaman varsayılan olarak Profile'a dönmez.
// Kaynağı burada tutmak, aynı aracı hem sol menüden hem mobil katalogdan
// açtığımızda "Geri"nin doğal üst sayfaya dönmesini sağlar.
const _toolParents = { watch: 'feed', blend: 'feed' };

function _setToolBackCopy(kind, parent) {
  const destination = parent === 'tools' ? 'Araçlara dön' : 'Akışa dön';
  if (kind === 'blend') {
    const label = $('blend-result-back-label');
    if (label) label.textContent = destination;
    const pageBack = $('blends-back-label');
    if (pageBack) pageBack.textContent = destination;
    return;
  }
  const label = $('tools-back-label');
  if (label) label.textContent = destination;
}

function returnToToolParent(kind) {
  const parent = _toolParents[kind] || 'feed';
  if (parent === 'tools') {
    openToolsDirectory();
    return;
  }
  openFeed();
}

function returnToWatchTool() {
  openQuickTool('watch', { parent: _toolParents.watch || 'feed' });
}

function homeView() {
  // The feed is the home screen, the way a timeline is on Twitter. The
  // recommender dashboard is now reached as "Profil".
  return (_authEnabled && _account) ? 'feed' : 'idle';
}

// Screens that belong to the recommender return to the dashboard, not the feed:
// that is where the buttons they came from live.
function dashboardView() {
  return (_authEnabled && _account) ? 'profile' : 'idle';
}

function _toggleMsg(el, msg) {
  if (msg) { el.textContent = msg; el.classList.remove('hidden'); }
  else { el.textContent = ''; el.classList.add('hidden'); }
}
function profileActionNotice(msg) {
  $('profile-action-error').classList.add('hidden');
  _toggleMsg($('profile-action-notice'), msg);
}
function profileActionError(msg) {
  $('profile-action-notice').classList.add('hidden');
  _toggleMsg($('profile-action-error'), msg);
}
function showActionError(msg) {
  if (_authEnabled && _account) {
    const target = !$('view-tools').classList.contains('hidden') ? 'tools' : 'profile';
    showView(target);
    profileActionError(msg);
    if (msg) $('profile-action-error').scrollIntoView({ block: 'center', behavior: 'smooth' });
  } else {
    setIdleError(msg);
  }
}

function openProfilePanel(which) {
  const watch = which === 'watch';
  const blend = which === 'blend';
  $('profile-watch-panel').classList.toggle('open', watch);
  $('profile-blend-panel').classList.toggle('open', blend);
  if (watch || blend) resetRecoPanel();
  profileActionNotice(null);
  profileActionError(null);
  if (watch) {
    setProfileWatchMode(_profileWatchMode);
    // Bugünün zevk önerisi hâlâ geçerliyse paneli kapatma, geri yükle.
    const cached = _loadTasteReco();
    if (cached) _showTasteReco(cached.at || 0);
  }
  if (blend) setTimeout(() => $('profile-blend-username').focus(), 40);
}

function mountQuickTools(hostId = 'quick-tools-host') {
  const host = $(hostId);
  const tools = $('profile-quick-tools');
  if (!host || !tools) return;
  if (tools.parentElement !== host) host.appendChild(tools);
  // Blend sayfasında araç kendi başlığını göstermiyor; üstteki boşluğu da
  // bırakmalı ki kutucuk "Blendler" başlığının hemen altına gelsin.
  tools.classList.toggle('quick-tools--embedded', hostId === 'blend-tools-host');
}

function openToolsDirectory() {
  mountQuickTools();
  _toolParents.watch = 'feed';
  _toolParents.blend = 'feed';
  _setToolBackCopy('watch', 'feed');
  showView('tools');
  $('tools-directory')?.classList.remove('hidden');
  $('profile-quick-tools')?.classList.add('hidden');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function openQuickTool(which, { parent = 'feed' } = {}) {
  _toolParents[which] = parent;
  _setToolBackCopy(which, parent);
  if (which === 'blend') {
    mountQuickTools('blend-tools-host');
    showView('blends');
    $('profile-quick-tools')?.classList.remove('hidden');
    $('quick-tools-head')?.classList.add('hidden');
    $('quick-tools-kicker').textContent = 'İki zevk, tek liste';
    $('quick-tools-title').textContent = 'Blend yap';
    $('quick-tools-icon').textContent = 'join_inner';
    openProfilePanel('blend');
    loadMyBlends(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
    return;
  }
  mountQuickTools();
  showView('tools');
  $('tools-directory')?.classList.add('hidden');
  $('profile-quick-tools')?.classList.remove('hidden');
  $('quick-tools-head')?.classList.remove('hidden');
  $('quick-tools-kicker').textContent = 'Bu gece';
  $('quick-tools-title').textContent = 'Ne izlesem?';
  $('quick-tools-icon').textContent = 'local_movies';
  openProfilePanel(which);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function setProfileWatchMode(mode) {
  _profileWatchMode = mode;
  document.querySelectorAll('#profile-watch-panel [data-watch-mode]').forEach(btn => {
    const on = btn.dataset.watchMode === mode;
    btn.classList.toggle('bg-surface-container/70', on);
    btn.classList.toggle('text-on-surface', on);
    btn.classList.toggle('text-on-surface-variant', !on);
    btn.classList.toggle('border-primary-container/50', on && mode === 'taste');
    btn.classList.toggle('border-tertiary-container/50', on && mode === 'random');
    btn.classList.toggle('border-outline-variant/25', !on);
  });
  $('profile-watch-go').className = `mt-4 w-full ${mode === 'random' ? 'bg-tertiary-container text-on-tertiary-container' : 'bg-primary-container text-black'} py-3.5 rounded-xl font-label-md text-label-md uppercase tracking-wider hover:opacity-90 active:scale-[0.98] transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-40`;
  $('profile-watch-go-icon').textContent = mode === 'random' ? 'shuffle' : 'auto_awesome';
  $('profile-watch-go-label').textContent = mode === 'random' ? 'Rastgele seç' : 'Öner';
}

// ── Shared SSE consumer for /api/recommend & /api/random ────────────────
async function consumeRecommendationStream(path, body, handlers) {
  const { onQueued, onStep, onResult, onError, timeoutMs = 180000 } = handlers || {};
  const apiRequest = beginApiRequest(timeoutMs);
  let terminal = false;
  try {
    const resp = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
      signal: apiRequest.controller.signal,
    });
    await assertStreamResponse(resp);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done: streamDone, value } = await reader.read();
      if (streamDone) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let event;
        try { event = JSON.parse(line.slice(6)); } catch { continue; }
        if (event.type === 'queued') onQueued && onQueued(event.ahead);
        else if (event.type === 'step') onStep && onStep(event.step);
        else if (event.type === 'result') { terminal = true; onResult && onResult(event); }
        else if (event.type === 'error') { terminal = true; onError && onError(scrapeErrorMessage(event)); }
      }
    }
    if (!terminal) onError && onError('Sunucu yanıtı tamamlanmadan bağlantı kapandı. Lütfen tekrar deneyin.');
  } catch (error) {
    if (apiRequest.replaced || apiRequest.cancelled) return;
    const message = streamErrorMessage(error, apiRequest, 'Sunucuya ulaşılamadı.');
    if (message) onError && onError(message);
  } finally {
    finishApiRequest(apiRequest);
  }
}

// ── Streaming text reveal ──────────────────────────────────────────────
const _reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function streamText(el, text) {
  if (!el) return;
  text = String(text || '');
  if (el._streamKey === text) return;
  el._streamKey = text;
  if (el._streamTimer) { clearInterval(el._streamTimer); el._streamTimer = null; }
  if (_reduceMotion || text.length < 3) { el.textContent = text; return; }
  // Soft wash: words are laid out at once, then faded in a few at a time.
  const parts = text.split(/(\s+)/);
  const words = [];
  const frag = document.createDocumentFragment();
  for (const part of parts) {
    if (part === '') continue;
    if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); continue; }
    const span = document.createElement('span');
    span.className = 'stream-word';
    span.textContent = part;
    frag.appendChild(span);
    words.push(span);
  }
  el.textContent = '';
  el.appendChild(frag);
  let i = 0;
  el._streamTimer = setInterval(() => {
    for (let k = 0; k < 2 && i < words.length; k++, i++) words[i].classList.add('on');
    if (i >= words.length) { clearInterval(el._streamTimer); el._streamTimer = null; }
  }, 60);
}

// ── Inline "Bu gece" recommendation (no page transition) ────────────────
const _RECO_STEP_LABEL = {
  scraping: 'İzleme listen okunuyor',
  enriching: 'Film bilgileri toplanıyor',
  ranking: 'Zevkinle eşleştiriliyor',
  llm: 'En iyi seçim yapılıyor',
};
let _recoBusy = false;

function resetRecoPanel() {
  // A running recommendation is never interrupted by navigating around —
  // only leaving the site stops it. Panel switches just leave it be.
  if (_recoBusy) return;
  $('profile-reco-panel').classList.remove('open');
  $('profile-reco-body').innerHTML = '';
}

function _recoLoadingHTML(mode) {
  return `
    <div class="flex flex-col items-center gap-4 py-6 text-center">
      <div class="spinner" style="width:44px;height:44px"></div>
      <p id="profile-reco-status" class="font-label-md text-label-md text-on-surface-variant uppercase tracking-wide">${mode === 'random' ? 'Topluluk havuzu karıştırılıyor' : 'İzleme listen okunuyor'}</p>
    </div>`;
}

function _recoResetBtn() {
  return `<button type="button" id="profile-reco-again" class="mt-4 w-full flex items-center justify-center gap-2 rounded-xl border border-outline-variant/25 bg-surface-container/40 py-3 font-label-md text-label-md uppercase tracking-wide text-on-surface-variant hover:text-on-surface hover:bg-surface-container/70 transition-colors"><span class="material-symbols-outlined text-[18px]">refresh</span>Yeni öneri</button>`;
}

async function startInlineReco(mode, { preserveViewport = false } = {}) {
  if (_recoBusy) return;
  const username = ($('username-input').value || (_account && _account.username) || '').trim();
  if (!username) return;
  _recoBusy = true;
  profileActionNotice(null);
  profileActionError(null);
  $('profile-watch-panel').classList.remove('open');
  $('profile-reco-body').innerHTML = _recoLoadingHTML(mode);
  $('profile-reco-panel').classList.add('open');
  if (!preserveViewport) {
    setTimeout(() => $('profile-reco-panel').scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 60);
  }

  await consumeRecommendationStream(
    mode === 'random' ? '/api/random' : '/api/recommend',
    { username },
    {
      timeoutMs: 300000,
      onStep: (step) => {
        const el = $('profile-reco-status');
        if (el) el.textContent = _RECO_STEP_LABEL[step] || 'Hazırlanıyor';
      },
      onResult: (event) => {
        _recoBusy = false;
        // Surface the result even if the user wandered off mid-request.
        $('profile-reco-panel').classList.add('open');
        if (mode === 'random') renderInlineRandom(event);
        else renderInlineTaste(event);
      },
      onError: (msg) => {
        _recoBusy = false;
        $('profile-reco-panel').classList.add('open');
        $('profile-reco-body').innerHTML =
          `<div class="rounded-xl px-4 py-3 bg-error-container/30 text-error font-body-md text-body-md">${escapeHTML(msg)}</div>${_recoResetBtn()}`;
      },
    },
  );
}

function _discoverNote(on) {
  return on
    ? `<div class="mb-4 rounded-xl border border-tertiary-container/30 bg-tertiary-container/10 px-4 py-3 font-body-md text-body-md text-tertiary-container">Watchlist'inde öneri için yeterli film yoktu — eksikleri TMDb'den, daha önce izlemediğin filmlerden tamamladık.</div>`
    : '';
}

// ── Zevk profili önerisi — gün boyu sabit, 3 film arası gezilebilir ──────
const _TASTE_RECO_KEY = 'mb_taste_reco';
// Bumped whenever the stored shape or the pool size changes, so yesterday's
// three-film pool is not replayed all of today from localStorage.
const _TASTE_RECO_VERSION = 2;
function _todayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function _loadTasteReco() {
  try {
    const o = JSON.parse(localStorage.getItem(_TASTE_RECO_KEY) || 'null');
    if (!o || o.v !== _TASTE_RECO_VERSION) return null;
    if (o.day !== _todayKey() || !Array.isArray(o.pool) || !o.pool.length) return null;
    if (_account && o.username && o.username !== _account.username) return null;
    return o;
  } catch (_) { return null; }
}
function _saveTasteReco(pool, summary, discover) {
  try {
    localStorage.setItem(_TASTE_RECO_KEY, JSON.stringify({
      v: _TASTE_RECO_VERSION,
      day: _todayKey(),
      username: (_account && _account.username) || '',
      pool, summary, discover: !!discover, at: 0,
    }));
  } catch (_) {}
}
function _clearTasteReco() { try { localStorage.removeItem(_TASTE_RECO_KEY); } catch (_) {} }

function renderInlineTaste(data) {
  const pool = (data.recommendations || []).slice(0, 5);
  if (!pool.length) {
    $('profile-reco-body').innerHTML = `<div class="rounded-xl px-4 py-3 bg-error-container/30 text-error font-body-md text-body-md">Sana uygun bir öneri çıkaramadık.</div>${_recoResetBtn()}`;
    return;
  }
  _saveTasteReco(pool, data.taste_summary || '', data.discover_fallback);
  _showTasteReco(0);
}

function _showTasteReco(index) {
  const o = _loadTasteReco();
  if (!o) return;
  const i = Math.max(0, Math.min(index, o.pool.length - 1));
  o.at = i;
  try { localStorage.setItem(_TASTE_RECO_KEY, JSON.stringify(o)); } catch (_) {}
  $('profile-reco-panel').classList.add('open');
  $('profile-reco-body').innerHTML = `
    ${_discoverNote(o.discover)}
    <div class="line-rise reco-swipe" data-reco-swipe>${buildHeroCard(o.pool[i])}</div>
    <p class="mt-2 text-center font-label-sm text-label-sm text-on-surface-variant/45 sm:hidden">Kartı sağa sola kaydır</p>
    <div class="mt-4 flex items-center justify-between gap-3">
      <button type="button" data-taste-nav="-1" ${i === 0 ? 'disabled' : ''} class="w-10 h-10 rounded-full border border-outline-variant/30 text-on-surface-variant hover:text-on-surface disabled:opacity-25 flex items-center justify-center transition-colors"><span class="material-symbols-outlined text-[20px]">chevron_left</span></button>
      <span class="font-label-sm text-label-sm uppercase tracking-wide text-on-surface-variant/60">${i + 1} / ${o.pool.length}</span>
      <button type="button" data-taste-nav="1" ${i === o.pool.length - 1 ? 'disabled' : ''} class="w-10 h-10 rounded-full border border-outline-variant/30 text-on-surface-variant hover:text-on-surface disabled:opacity-25 flex items-center justify-center transition-colors"><span class="material-symbols-outlined text-[20px]">chevron_right</span></button>
    </div>
    ${i === o.pool.length - 1 ? _toRandomBtn(o.pool.length) : ''}
    ${_recoResetBtn()}`;
  bindRecoSwipe($('profile-reco-body'));
}

// Son öneriyi de beğenmediyse çıkmaz sokak olmasın: rastgeleye devam.
function _toRandomBtn(total) {
  return `<button type="button" id="profile-reco-torandom" class="mt-4 w-full flex items-center justify-center gap-2 rounded-xl border border-secondary-container/30 bg-secondary-container/10 py-3 font-label-md text-label-md uppercase tracking-wide text-secondary-container hover:bg-secondary-container/20 transition-colors"><span class="material-symbols-outlined text-[18px]">casino</span>${total} filmi de beğenmedin mi? Rastgeleye geç</button>`;
}

// Rastgele: sınırsız. Havuz, topluluğun izlediği ama senin izlemediğin filmler.
function renderInlineRandom(data) {
  const films = data.films || [];
  if (!films.length) {
    $('profile-reco-body').innerHTML = `<div class="rounded-xl px-4 py-3 bg-error-container/30 text-error font-body-md text-body-md">Film bulunamadı.</div>${_recoResetBtn()}`;
    return;
  }
  $('profile-reco-body').innerHTML = `
    ${_randomPoolNote(data)}
    <p class="mb-4 font-body-md text-body-md text-on-surface-variant">🎬 Beğenmezsen çevirmeye devam et — hak sınırı yok.</p>
    <div class="line-rise">${buildRandomCard(films[0])}</div>
    <div class="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
      <button type="button" id="profile-reco-reroll" class="flex items-center justify-center gap-2 rounded-xl border border-secondary-container/30 bg-secondary-container/10 py-3 font-label-md text-label-md uppercase tracking-wide text-secondary-container hover:bg-secondary-container/20 transition-colors"><span class="material-symbols-outlined text-[18px]">casino</span>Başka bir tane</button>
      <button type="button" id="profile-reco-totaste" class="flex items-center justify-center gap-2 rounded-xl border border-primary-container/30 bg-primary-container/10 py-3 font-label-md text-label-md uppercase tracking-wide text-primary-container hover:bg-primary-container/20 transition-colors"><span class="material-symbols-outlined text-[18px]">psychology</span>Zevkime göre öner</button>
    </div>`;
}

function _randomPoolNote(data) {
  return data.discover_fallback
    ? `<div class="mb-4 rounded-xl border border-tertiary-container/30 bg-tertiary-container/10 px-4 py-3 font-body-md text-body-md text-tertiary-container">Topluluk havuzu henüz yeterli değil — bunu TMDb'den, izlemediğin filmler arasından seçtik.</div>`
    : `<div class="mb-4 rounded-xl border border-outline-variant/25 bg-surface-variant/40 px-4 py-3 font-body-md text-body-md text-on-surface-variant">Diğer Movienotes üyelerinin izlediği, senin izlemediğin filmler arasından.</div>`;
}

function runProfileWatch() {
  profileActionNotice(null);
  profileActionError(null);
  startInlineReco(_profileWatchMode);
}

function runProfileBlend() {
  blendRequestFlow({
    inputId: 'profile-blend-username',
    suggestionsId: 'profile-blend-suggestions',
    button: 'profile-blend-go',
    onNotice: profileActionNotice,
    onError: profileActionError,
    onNotFound: (username) => openShareSheet({ notFoundUsername: username }),
  });
}

// ── Invite / share sheet ───────────────────────────────────────────────
const SITE_URL = 'https://movie-boxd.onrender.com';

function openShareSheet(opts = {}) {
  const dialog = $('dialog-share');
  const notFound = (opts.notFoundUsername || '').replace(/^@/, '');
  const url = SITE_URL;
  const message = notFound
    ? `Movienotes'da film zevkimizi karşılaştıralım (Blend) — kaydol: ${url}`
    : `Letterboxd zevkine göre film öneren Movienotes'u dene: ${url}`;

  $('share-title').textContent = notFound
    ? `@${notFound} sitemize kaydolmamış 😔`
    : 'Bir arkadaşını davet et';
  $('share-subtitle').textContent = notFound
    ? 'Davet etmek ister misin? Linki kopyala ya da bir uygulamadan gönder.'
    : 'Zevkine göre film önerileri ve Blend uyum skoru için arkadaşını çağır.';
  $('share-link').value = url;

  const enc = encodeURIComponent;
  $('share-whatsapp').href = `https://wa.me/?text=${enc(message)}`;
  $('share-x').href = `https://twitter.com/intent/tweet?text=${enc(message)}`;

  const nativeShare = () => {
    if (navigator.share) {
      navigator.share({ title: 'Movienotes', text: message, url }).catch(() => {});
    } else {
      copyShareLink();
    }
  };
  $('share-native').onclick = nativeShare;
  $('share-instagram').onclick = nativeShare;
  $('share-tiktok').onclick = nativeShare;
  $('share-copy').onclick = copyShareLink;

  if (!dialog.open) dialog.showModal();
}

function copyShareLink() {
  const value = $('share-link').value;
  const done = () => {
    const btn = $('share-copy');
    const original = btn.textContent;
    btn.textContent = 'Kopyalandı ✓';
    setTimeout(() => { btn.textContent = original; }, 1600);
  };
  if (navigator.clipboard) {
    navigator.clipboard.writeText(value).then(done).catch(() => {
      $('share-link').select();
      document.execCommand && document.execCommand('copy');
      done();
    });
  } else {
    $('share-link').select();
    document.execCommand && document.execCommand('copy');
    done();
  }
}

async function buildAndOpenShareCard(button, factory) {
  if (!button || button.disabled) return;
  const label = button.querySelector('[data-share-label]');
  const icon = button.querySelector('.material-symbols-outlined');
  const originalLabel = label?.textContent || '';
  const originalIcon = icon?.textContent || 'ios_share';
  button.disabled = true;
  button.classList.add('opacity-60');
  if (label) label.textContent = 'Hazırlanıyor';
  if (icon) {
    icon.textContent = 'progress_activity';
    icon.classList.add('animate-spin');
  }
  await new Promise(resolve => requestAnimationFrame(resolve));
  let succeeded = false;
  try {
    const shareCards = await loadShareCardsModule();
    const card = await factory(shareCards);
    shareCards.openShareCardPreview(card);
    succeeded = true;
  } catch (error) {
    button.title = error?.message || 'PNG oluşturulamadı.';
    if (label) label.textContent = 'Tekrar dene';
  } finally {
    button.disabled = false;
    button.classList.remove('opacity-60');
    if (icon) {
      icon.textContent = originalIcon;
      icon.classList.remove('animate-spin');
    }
    if (succeeded && label) label.textContent = originalLabel;
    if (!succeeded) setTimeout(() => {
      if (label) label.textContent = originalLabel;
      button.removeAttribute('title');
    }, 2400);
  }
}

function setAuthHeaderLinks(visible) {
  $('header-how-it-works').classList.toggle('hidden', !visible);
  $('header-privacy').classList.toggle('hidden', !visible);
}

async function refreshFeedBadge() {
  if (!_account) return;
  try {
    const data = await apiJSON('/api/notifications/unread-count');
    const count = Math.max(0, Number(data.count) || 0);
    const label = count > 9 ? '9+' : String(count);
    // Header pill outside the feed, bell inside it — the same tally.
    [$('feed-notification-badge'), $('feed-bell-badge')].forEach(badge => {
      if (!badge) return;
      badge.textContent = label;
      badge.classList.toggle('hidden', count === 0);
    });
    paintNavBadge('notifications', count);
    if (_lastUnreadNotificationCount !== null
      && count > _lastUnreadNotificationCount
      && document.hidden
      && 'Notification' in window
      && Notification.permission === 'granted') {
      new Notification('Movienotes', {
        body: `${count - _lastUnreadNotificationCount} yeni bildirimin var.`,
        icon: '/static/movienotes-notify-192.png?v=20260910.6',
      });
    }
    _lastUnreadNotificationCount = count;
  } catch (_) {}
}

function startFeedNotificationPolling() {
  if (_feedNotificationPollTimer) return;
  _feedNotificationPollTimer = setInterval(refreshFeedBadge, 45000);
}

function stopFeedNotificationPolling() {
  if (_feedNotificationPollTimer) clearInterval(_feedNotificationPollTimer);
  _feedNotificationPollTimer = null;
  _lastUnreadNotificationCount = null;
}

let _shownView = '';

function showView(name) {
  // İlk gerçek ekran çizildiği anda açılış perdesi kalkar.
  document.body.classList.remove('is-booting');
  // Yeni bir sayfa her zaman başından açılır; önceki sayfanın kaydırma
  // konumu devralınınca sayfa boşluktan başlıyormuş gibi görünüyordu.
  if (name !== _shownView) {
    _shownView = name;
    window.scrollTo(0, 0);
  }
  ['auth', 'onboarding', 'profile', 'tools', 'idle', 'loading', 'results', 'random-result', 'blend-loading', 'blend-result', 'inbox', 'blends', 'sinefil', 'feed', 'thread', 'user', 'follows', 'notifications'].forEach(v => {
    $(`view-${v}`).classList.toggle('hidden', v !== name);
  });
  $('main-footer').classList.toggle('hidden', NO_FOOTER_VIEWS.includes(name));
  // Onboarding is a locked, full-screen takeover — no header to click away with.
  $('app-header').classList.toggle(
    'hidden', name === 'onboarding' || (Boolean(_account) && SHELL_VIEWS.includes(name)),
  );
  setAuthHeaderLinks(name === 'auth' && _authEnabled && !_account);
  const feedButton = $('btn-open-feed');
  const showFeed = Boolean(_account) && !SHELL_VIEWS.includes(name);
  feedButton.classList.toggle('hidden', !showFeed);
  feedButton.classList.toggle('flex', showFeed);
  const headerProfile = $('btn-header-profile');
  // Her ekranda: kabuk dışındaki sonuç/öneri ekranlarında da profile tek
  // dokunuşla dönülebilmeli. Akış ailesi kendi başlığındaki avatarı taşıyor.
  const showHeaderProfile = Boolean(_account)
    && !['auth', 'onboarding', 'loading', 'blend-loading'].includes(name)
    && !OWN_HEADER_VIEWS.includes(name);
  headerProfile.classList.toggle('hidden', !showHeaderProfile);
  headerProfile.classList.toggle('flex', showHeaderProfile);
  const letterComposeFab = $('btn-letter-compose-fab');
  if (letterComposeFab) {
    // Bir mektuplaşma açıkken zaten "…kişisine yaz" düğmesi var; kayan
    // düğme onun üstüne biniyordu.
    const showLetterComposeFab = name === 'inbox' && !_openLetterThread;
    letterComposeFab.classList.toggle('hidden', !showLetterComposeFab);
    letterComposeFab.classList.toggle('flex', showLetterComposeFab);
  }
  paintShell(name);
  rememberRoute(name);
  applyProfileTheme();
}

// ── Adres yönlendirmesi ──────────────────────────────────────────────────
// Yenilediğinde bulunduğun sayfa açılsın diye ekran adı adres çubuğunda
// tutuluyor. `replaceState` kullanılıyor: geçmişe kayıt eklenmiyor, yani
// tarayıcının geri tuşu bugünkü davranışını koruyor.
const ROUTE_OF_VIEW = {
  feed: 'akis', notifications: 'bildirimler', inbox: 'mektuplar',
  blends: 'blend', sinefil: 'kesfet', profile: 'profil', tools: 'araclar',
};
const VIEW_OF_ROUTE = Object.fromEntries(
  Object.entries(ROUTE_OF_VIEW).map(([view, route]) => [route, view])
);
let _routeRestoring = false;

function currentRoute(name) {
  if (name === 'user') return _userPage.username ? `u/${_userPage.username}` : 'akis';
  if (name === 'thread') return _threadId ? `not/${_threadId}` : 'akis';
  if (name === 'follows') return _followsFrom ? `takip/${_followsFrom}` : 'akis';
  return ROUTE_OF_VIEW[name] || '';
}

function rememberRoute(name) {
  if (_routeRestoring || !_account) return;
  const route = currentRoute(name);
  if (!route) return;
  const next = `#/${route}`;
  if (location.hash !== next) history.replaceState(null, '', next);
}

// Açılışta adresteki ekranı geri getirir. Bilinmeyen adres akışa düşer.
async function restoreRoute() {
  const raw = (location.hash || '').replace(/^#\/?/, '');
  const [head, ...rest] = raw.split('/').filter(Boolean);
  if (!head) return false;
  _routeRestoring = true;
  try {
    if (head === 'u' && rest[0]) { await openUserPage(rest[0], { from: 'feed' }); return true; }
    if (head === 'not' && rest[0]) { await openThread(rest[0], { from: 'feed' }); return true; }
    if (head === 'takip' && rest[0]) { await openFollows(rest[0], 'followers'); return true; }
    const view = VIEW_OF_ROUTE[head];
    if (!view) return false;
    goNav(NAV_OF_VIEW[view] || 'feed');
    return true;
  } finally {
    _routeRestoring = false;
  }
}

// ── Uygulama kabuğu: sol gezinme, alt sekme çubuğu, sağ raf ─────────────
// Twitter'ın üç sütunu: solda gezinme, ortada akış, sağda gündem. Mobilde sol
// sütun alta iner, sağ raf kaybolur — aynı bilgi mimarisi, dar ekran hâli.
const SHELL_VIEWS = [
  'feed', 'thread', 'user', 'follows', 'notifications',
  'profile', 'tools', 'inbox', 'blends', 'sinefil',
  // Sonuç ekranları da kabuğun içinde: tepesinde MOVIENOTES bandı ve "Akış"
  // düğmesi yerine kendi geri bağlantısı ve profil avatarı var.
  'results', 'random-result', 'blend-result',
];
// The feed family carries its own column header, so the global logo bar would
// be a second, redundant band above it — Twitter has one.
const OWN_HEADER_VIEWS = ['feed', 'thread', 'user', 'follows', 'notifications'];
// Kabuk açıkken alt çubuk zaten ekranın dibinde duruyor; altına bir de site
// altbilgisi koymak her sayfaya birkaç yüz piksel gereksiz kaydırma ekliyordu.
const NO_FOOTER_VIEWS = [
  'auth', 'onboarding', 'loading', 'blend-loading',
  'feed', 'thread', 'user', 'follows', 'notifications',
  'profile', 'tools', 'inbox', 'blends', 'sinefil',
];
// Which nav item lights up for a given view.
const NAV_OF_VIEW = {
  feed: 'feed', thread: 'feed', user: 'feed', follows: 'feed',
  notifications: 'notifications', inbox: 'inbox', blends: 'blends',
  sinefil: 'sinefil', profile: 'profile', tools: '',
  results: '', 'random-result': '', 'blend-result': '',
};

function paintShell(name) {
  const on = Boolean(_account) && SHELL_VIEWS.includes(name);
  document.body.classList.toggle('has-shell', on);
  document.body.classList.toggle('has-rail', on && OWN_HEADER_VIEWS.includes(name));
  const active = NAV_OF_VIEW[name] || '';
  document.querySelectorAll('[data-nav]').forEach(button => {
    button.classList.toggle('is-active', button.dataset.nav === active);
  });
  const fab = $('btn-compose-fab');
  const showFab = on && ['feed', 'thread'].includes(name);
  fab.classList.toggle('hidden', !showFab);
  fab.classList.toggle('flex', showFab);
  if (on) paintNavAccount();
  paintAvatarButtons();
  if (name === 'profile') applyFolds();
}

// Profil düğmesi her ekranda aynı görünsün: ikon değil, kişinin kendi avatarı.
// ── Öneri kartını kaydırma ──────────────────────────────────────────────
// Telefonda kartı parmakla çekmek, ok düğmelerine basmaktan daha doğal.
// Sürüklerken kart parmağı izler ve hafifçe eğilir; eşiği geçtiyse o yöne
// savrulup sıradaki filme geçilir, geçmediyse yerine oturur.
const SWIPE_THRESHOLD = 64;

function bindRecoSwipe(root) {
  const card = root?.querySelector('[data-reco-swipe]');
  if (!card || card.dataset.swipeBound) return;
  card.dataset.swipeBound = '1';

  let startX = 0;
  let startY = 0;
  let dx = 0;
  let dragging = false;

  const paint = (offset, settling = false) => {
    card.classList.toggle('reco-swipe--settling', settling);
    // Eğim kaydırma miktarıyla artar ama 8 dereceyi geçmez.
    const tilt = Math.max(-8, Math.min(8, offset / 14));
    card.style.transform = offset
      ? `translateX(${offset}px) rotate(${tilt}deg)`
      : '';
  };

  const release = () => {
    if (!dragging) return;
    dragging = false;
    const step = dx <= -SWIPE_THRESHOLD ? 1 : (dx >= SWIPE_THRESHOLD ? -1 : 0);
    const target = _loadTasteReco();
    const at = target ? (target.at || 0) : 0;
    const last = target ? target.pool.length - 1 : 0;
    // Uçtaki kart savrulmaz: gidecek yer yoksa yerine döner.
    const allowed = step === 1 ? at < last : (step === -1 ? at > 0 : false);
    if (!allowed) { paint(0, true); dx = 0; return; }
    card.classList.add('reco-swipe--settling', 'reco-swipe--gone');
    paint(step === 1 ? -window.innerWidth : window.innerWidth, true);
    const next = at + step;
    dx = 0;
    setTimeout(() => _showTasteReco(next), 190);
  };

  card.addEventListener('touchstart', event => {
    if (event.touches.length !== 1) return;
    startX = event.touches[0].clientX;
    startY = event.touches[0].clientY;
    dx = 0;
    dragging = true;
    card.classList.remove('reco-swipe--settling');
  }, { passive: true });

  card.addEventListener('touchmove', event => {
    if (!dragging) return;
    const moveX = event.touches[0].clientX - startX;
    const moveY = event.touches[0].clientY - startY;
    // Dikey niyet yatay niyetten baskınsa sayfa kaydırmasına karışmıyoruz.
    if (Math.abs(moveY) > Math.abs(moveX) && Math.abs(moveY) > 12) {
      dragging = false;
      paint(0, true);
      return;
    }
    dx = moveX;
    paint(dx);
  }, { passive: true });

  card.addEventListener('touchend', release, { passive: true });
  card.addEventListener('touchcancel', () => { dragging = false; paint(0, true); dx = 0; }, { passive: true });
}

// ── Katlanır bölümler ────────────────────────────────────────────────────
// Telefonda profil panosu on bir kart uzunluğundaydı. Ağır bölümler dar
// ekranda kapalı başlar; hangisinin açık kaldığını kullanıcı seçer ve seçim
// cihazda saklanır.
// Kısayollar zaten alt çubukta ve kenar çubuğunda var; panoda kapalı başlar.
const FOLDED_ON_PHONE = ['kisayollar', 'turler', 'yonetmen', 'ozet', 'auteur', 'basucu', 'gunce'];

function _foldPrefs() {
  try { return JSON.parse(localStorage.getItem('mb_folds') || '{}'); }
  catch (_) { return {}; }
}

function applyFolds() {
  const phone = window.matchMedia('(max-width: 1023px)').matches;
  const prefs = _foldPrefs();
  document.querySelectorAll('[data-fold]').forEach(section => {
    const key = section.dataset.fold;
    const folded = !phone ? false : (key in prefs
      ? Boolean(prefs[key])
      : FOLDED_ON_PHONE.includes(key));
    section.classList.toggle('is-folded', folded);
    const toggle = section.querySelector('.fold-head, .fold-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', String(!folded));
  });
}

function toggleFold(section) {
  const folded = !section.classList.contains('is-folded');
  section.classList.toggle('is-folded', folded);
  const toggle = section.querySelector('.fold-head, .fold-toggle');
  if (toggle) toggle.setAttribute('aria-expanded', String(!folded));
  try {
    const prefs = _foldPrefs();
    prefs[section.dataset.fold] = folded;
    localStorage.setItem('mb_folds', JSON.stringify(prefs));
  } catch (_) {}
}

function paintAvatarButtons() {
  const avatar = safeImageURL(_account?.avatar_url);
  const name = _account?.display_name || _account?.username || '';
  const inner = avatar
    ? `<img src="${avatar}" alt="" class="h-full w-full object-cover"/>`
    : `<span class="font-bold text-primary-container">${escapeHTML((name[0] || '?').toUpperCase())}</span>`;
  document.querySelectorAll('.avatar-button').forEach(button => {
    button.innerHTML = inner;
  });
}

function paintNavAccount() {
  if (!_account) return;
  const avatar = safeImageURL(_account.avatar_url);
  const name = _account.display_name || _account.username || '';
  $('nav-account-avatar').innerHTML = avatar
    ? `<img src="${avatar}" alt="" class="h-full w-full object-cover"/>`
    : escapeHTML((name[0] || '?').toUpperCase());
  $('nav-account-name').textContent = name;
  $('nav-account-handle').textContent = `@${_account.username || ''}`;
  const composer = $('feed-compose-avatar');
  composer.innerHTML = avatar
    ? `<img src="${avatar}" alt="" class="h-full w-full object-cover"/>`
    : escapeHTML((name[0] || '?').toUpperCase());
}

function goNav(target) {
  switch (target) {
    case 'feed': openFeed(); break;
    case 'notifications': openNotifications(); break;
    case 'inbox': openLetterInbox(); break;
    case 'blends': openQuickTool('blend'); break;
    case 'sinefil': showView('sinefil'); loadSinefilArea(); break;
    case 'profile': showView('profile'); break;
    default: openFeed();
  }
}

// ── App-wide light/dark theme (opt-in, per viewer) ─────────────────────
function _profileThemePref() {
  try {
    const value = localStorage.getItem('mb_theme') || localStorage.getItem('mb_profile_theme');
    if (value === 'light') localStorage.setItem('mb_theme', 'light');
    return value === 'light' ? 'light' : 'dark';
  }
  catch (_) { return 'dark'; }
}

function applyProfileTheme() {
  const light = _profileThemePref() === 'light';
  document.body.classList.toggle('theme-light', light);
  const label = $('profile-theme-label');
  if (label) label.textContent = light ? 'Görünüm: Açık' : 'Görünüm: Koyu';
}

function toggleProfileTheme() {
  const next = _profileThemePref() === 'light' ? 'dark' : 'light';
  try { localStorage.setItem('mb_theme', next); } catch (_) {}
  applyProfileTheme();
}

function setIdleError(msg) {
  if (msg) {
    $('idle-error-text').textContent = msg;
    $('idle-error').classList.remove('hidden');
    $('idle-error').classList.add('flex');
  } else {
    $('idle-error').classList.add('hidden');
    $('idle-error').classList.remove('flex');
  }
}

function setIdleNotice(msg) {
  if (msg) {
    $('idle-notice-text').textContent = msg;
    $('idle-notice').classList.remove('hidden');
    $('idle-notice').classList.add('flex');
  } else {
    $('idle-notice').classList.add('hidden');
    $('idle-notice').classList.remove('flex');
  }
}

// ── Health check ───────────────────────────────────────────────────────────
async function loadHealth() {
  try {
    const r = await fetch(`${API_BASE}/api/health`);
    if (!r.ok) return null;
    const h = await r.json();

    $('dot-catalog').className = `w-2 h-2 rounded-full transition-all duration-500 ${h.tmdb_enabled  ? 'dot-orange' : 'dot-off'}`;
    $('dot-tmdb').className    = `w-2 h-2 rounded-full transition-all duration-500 ${h.tmdb_enabled  ? 'dot-green'  : 'dot-off'}`;
    $('dot-llm').className     = `w-2 h-2 rounded-full transition-all duration-500 ${h.llm_enabled   ? 'dot-blue'   : 'dot-off'}`;
    return h;
  } catch (_) { return null; }
}

let _publicStatsPromise = null;

async function loadPublicStats() {
  // Coalesce duplicate boot/logout calls so a single page never makes two
  // identical anonymous requests.
  if (_publicStatsPromise) return _publicStatsPromise;
  _publicStatsPromise = apiJSON('/api/public/stats')
    .then((data) => {
      const count = Number(data?.registered_users || 0);
      document.querySelectorAll('[data-public-user-count]').forEach((el) => {
        const value = el.querySelector('[data-public-user-count-value]');
        if (!value || count < 1) {
          el.classList.add('hidden');
          return;
        }
        value.textContent = count.toLocaleString(uiLocale());
        el.classList.remove('hidden');
      });
      return data;
    })
    .catch(() => {
      // Social proof is optional; never show an error state for it.
      _publicStatsPromise = null;
      return null;
    });
  return _publicStatsPromise;
}

// ── Account & persisted profile ───────────────────────────────────────────
let _authEnabled = false;
let _account = null;
let _persistedProfile = null;
let _lastUnreadNotificationCount = null;
let _feedNotificationPollTimer = null;
let _verification = null;
let _resetChallenge = null;
// Parola, kayıt sırasında girildiği haliyle bio doğrulaması bitene kadar
// bellekte tutulur; doğrulama başarılıysa oturum otomatik açılır, sonra silinir.
let _pendingRegPassword = null;
let _registrationAccount = null;

function syncLanguageControl() {
  const select = $('profile-language-select');
  if (select) select.value = localePreference();
}

function applyStoredAccountLocale(account) {
  const saved = account?.preferred_locale;
  if (saved !== 'tr' && saved !== 'en') return false;
  if (localePreference() === saved && getLocale() === saved) return false;
  setLocalePreference(saved);
  window.location.reload();
  return true;
}

async function changeProfileLanguage(event) {
  const preference = event.currentTarget.value;
  setLocalePreference(preference);
  if (_account) {
    try {
      await apiJSON('/api/profile/locale', {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ locale: preference }),
      });
      _account.preferred_locale = preference;
    } catch (_) {
      // The selected device remains localized even if an older database has
      // not yet received the optional preference column.
    }
  }
  window.location.reload();
}

function setImage(img, fallback, value, alt) {
  try {
    const url = new URL(String(value || ''));
    if (url.protocol !== 'https:') throw new Error('unsafe image');
    img.src = url.href;
    img.alt = alt || '';
    img.classList.remove('hidden');
    fallback?.classList.add('hidden');
  } catch (_) {
    img.removeAttribute('src');
    img.classList.add('hidden');
    fallback?.classList.remove('hidden');
  }
}

function applyAccount(account) {
  _account = account;
  syncLanguageControl();
  paintAvatarButtons();
  $('username-input').value = account.username;
  $('primary-username-field').classList.add('hidden');
  $('profile-display-name').textContent = account.display_name || account.username;
  $('profile-username').textContent = '@' + account.username;
  $('profile-avatar-fallback').textContent = (account.display_name || account.username)[0].toUpperCase();
  setImage($('profile-avatar'), $('profile-avatar-fallback'), account.avatar_url, account.display_name);
  $('btn-delete-data').classList.add('hidden');
  $('btn-mode-blend').disabled = false;
  $('btn-mode-blend').classList.remove('opacity-40', 'cursor-not-allowed');
  $('btn-mode-blend').title = t('Kayıtlı bir kullanıcıya onay isteği gönder.');
  renderProfileLetterSettings(Boolean(account.letter_receiving_enabled));
  renderPrivateAccount(Boolean(account.private_account));
  loadProfileSocialStats();
}

function renderProfileSocialStats(stats = {}) {
  const followers = Math.max(0, Number(stats.followers) || 0);
  const following = Math.max(0, Number(stats.following) || 0);
  $('profile-followers-count').textContent = String(followers);
  $('profile-following-count').textContent = String(following);
}

async function loadProfileSocialStats() {
  if (!_account) return;
  try {
    renderProfileSocialStats(await apiJSON('/api/profile/social-stats'));
  } catch (_) {
    // The static zero state remains visible even if a transient request fails.
    renderProfileSocialStats();
  }
}

function renderPrivateAccount(privateAccount) {
  const label = $('profile-private-label');
  if (label) label.textContent = privateAccount ? 'Kilitli hesap: Açık' : 'Kilitli hesap: Kapalı';
}

async function togglePrivateAccount() {
  const next = !_account?.private_account;
  // Tek anahtar iki şeyi birlikte çeviriyor: hem ayrıntıların kimlere açık
  // olduğunu hem de Sinefil Sineması listesinde çıkıp çıkmadığını.
  const message = next
    ? 'Hesabın kilitlenecek: notların, profil ayrıntıların ve takip listelerin yalnız kabul ettiğin takipçilere açılır, Sinefil Sineması listesinde de çıkmazsın. Uygulamayı sosyal alandan izole kullanmak istiyorsan bu yeterli. Devam edilsin mi?'
    : 'Hesabın herkese açılacak: notların ve profil ayrıntıların tüm kayıtlı sinefillere görünür, Sinefil Sineması listesinde çıkarsın. Devam edilsin mi?';
  if (!window.confirm(message)) return;
  try {
    const data = await apiJSON('/api/profile/privacy-settings', {
      method: 'POST', headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ private: next }),
    });
    if (_account) {
      _account.private_account = Boolean(data.private_account);
      _account.discoverable = !data.private_account;
    }
    renderPrivateAccount(Boolean(data.private_account));
    profileActionNotice(data.private_account
      ? 'Hesabın kilitlendi; Sinefil Sineması listesinde de çıkmayacaksın.'
      : 'Hesabın herkese açıldı.');
  } catch (error) { profileActionError(error.message || 'Hesap gizliliği değiştirilemedi.'); }
}

async function enableBrowserNotifications() {
  if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) { profileActionError('Bu tarayıcı kalıcı push bildirimi desteği sunmuyor.'); return; }
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') { profileActionNotice('Tarayıcı bildirimi için izin verilmedi.'); return; }
  try {
    const key = await apiJSON('/api/push/public-key');
    const registration = await registerMovienotesServiceWorker();
    if (!registration) throw new Error('Servis çalışanı kaydedilemedi.');
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      const raw = String(key.public_key || '').replace(/-/g, '+').replace(/_/g, '/');
      const padded = raw + '='.repeat((4 - raw.length % 4) % 4);
      const bytes = Uint8Array.from(atob(padded), char => char.charCodeAt(0));
      subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes });
    }
    await apiJSON('/api/push/subscriptions', {
      method: 'POST', headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(subscription.toJSON()),
    });
    profileActionNotice('Tarayıcı bildirimleri açık. Uygulama arka plandayken de yeni hareketleri haber vereceğiz.');
  } catch (error) {
    profileActionError(error.message || 'Tarayıcı bildirimi açılamadı.');
  }
}

function renderProfileLetterSettings(open) {
  const toggle = $('profile-letter-toggle');
  if (!toggle) return;
  toggle.setAttribute('aria-pressed', String(Boolean(open)));
  $('profile-letter-icon').textContent = open ? 'mark_email_read' : 'mail_lock';
  $('profile-letter-label').textContent = open ? 'Açık' : 'Kapalı';
  toggle.classList.toggle('bg-[#ff8000]/15', Boolean(open));
}




function accountSummaryFromTaste(taste) {
  const pieces = [taste?.summary, ...(taste?.analysis || [])]
    .filter(piece => typeof piece === 'string' && piece.trim())
    .map(piece => piece.trim());
  return pieces.length
    ? pieces.slice(0, 4).join(' ')
    : 'İzleme geçmişin tamamlandıkça bu alan sinema alışkanlıklarını daha ayrıntılı anlatacak.';
}

function renderPersistedProfile(data) {
  if (!data) return;
  if (applyStoredAccountLocale(data.account)) return;
  _persistedProfile = data;
  if (data.account) applyAccount(data.account);
  const taste = data.taste;
  if (taste) {
    streamText($('profile-account-summary'), accountSummaryFromTaste(taste));
    const sweptTotal = (data.sync_job && data.sync_job.total) || 0;
    $('profile-sample-size').textContent = String(Math.max(taste.sample_size || 0, sweptTotal));
    $('profile-rated-count').textContent = String(taste.rated_count || 0);
    const genres = taste.top_genres || [];
    $('profile-genres').innerHTML = genres.length
      ? genres.slice(0, 4).map(genre => `<span class="inline-flex items-center gap-2 px-3.5 py-2 rounded-full border border-primary-container/20 bg-primary-container/5 text-on-surface font-label-md text-label-md"><span class="w-1.5 h-1.5 rounded-full bg-primary-container"></span>${escapeHTML(genre)}</span>`).join('')
      : '<span class="text-on-surface-variant/60 text-sm">Tür sinyali henüz yeterli değil.</span>';
    // ── Auteur radar: top-10 director carousel ──────────────────────────
    const dirDetail = (taste.top_directors_detail || []).filter(d => d && d.name).slice(0, 10);
    const dirFallback = (taste.top_directors?.length ? taste.top_directors : [taste.favorite_director])
      .filter(Boolean).slice(0, 10).map(name => ({ name, films: [], count: 0 }));
    const dirRows = dirDetail.length ? dirDetail : dirFallback;
    const favoriteDirector = dirRows[0] || null;
    $('profile-favorite-director-name').textContent = favoriteDirector?.name || 'Henüz belirleniyor';
    $('profile-favorite-director-note').textContent = favoriteDirector?.count
      ? `${favoriteDirector.count} film ve puanlarınla öne çıkan yönetmen.`
      : favoriteDirector ? 'İzleme sıklığın ve verdiğin puanlarla öne çıkıyor.' : 'Yeterli yönetmen verisi oluştuğunda burada görünecek.';
    $('profile-favorite-director-avatar').innerHTML = favoriteDirector
      ? directorAvatar(favoriteDirector, 'w-14 h-14 text-[18px]')
      : '<span class="material-symbols-outlined">person</span>';

    if (dirRows.length) {
      renderDirectorDeck(dirRows);
    } else {
      unregisterProfileCarousel('profile-directors');
      _directorDeck = null;
      $('profile-directors').innerHTML = '<div class="rounded-2xl border border-dashed border-outline-variant/30 p-5 text-on-surface-variant">Yönetmen sıralaması için birkaç film bilgisinin daha tamamlanması gerekiyor.</div>';
    }

    const syncedAt = taste.updated_at || taste.generated_at;
    if (syncedAt) {
      $('profile-last-sync').innerHTML = `<span class="material-symbols-outlined text-[16px]">schedule</span>${t('Son güncelleme')} · ${escapeHTML(new Date(syncedAt).toLocaleString(uiLocale()))}`;
    }
  } else {
    unregisterProfileCarousel('profile-directors');
    _directorDeck = null;
    $('profile-directors').innerHTML = '<div class="rounded-2xl border border-dashed border-outline-variant/30 p-5 text-on-surface-variant">Zevk profili hazırlanıyor…</div>';
    $('profile-account-summary').textContent = 'İzleme geçmişin ve Fav 4 filmlerin analiz ediliyor…';
    $('profile-favorite-director-name').textContent = 'Henüz belirleniyor';
    $('profile-favorite-director-note').textContent = 'Yönetmen bilgileri tamamlandıkça burada görünecek.';
    $('profile-favorite-director-avatar').innerHTML = '<span class="material-symbols-outlined">person</span>';
  }
  const deferAuxiliary = !$('view-onboarding').classList.contains('hidden');
  if (!deferAuxiliary && !_statsLoaded) loadProfileStats();

  const accountSummary = accountSummaryFromTaste(taste);

  const favorites = data.favorite_films || [];
  $('profile-favorites').innerHTML = favorites.length
    ? favorites.slice(0, 4).map((film, index) => {
      const title = escapeHTML(film.title);
      const year = film.release_year ? escapeHTML(String(film.release_year)) : '';
      const poster = safeImageURL(film.poster_url);
      const badge = `<span class="absolute top-2.5 left-2.5 w-7 h-7 rounded-full bg-black/65 backdrop-blur flex items-center justify-center text-xs font-bold text-primary-container border border-white/10">${index + 1}</span>`;
      const emptyCard = `<div class="absolute inset-0 flex flex-col items-center justify-center p-4 text-center">
             <span class="material-symbols-outlined text-on-surface-variant/25 text-[44px]">movie</span>
             <strong class="mt-3 font-label-md text-label-md text-on-surface-variant line-clamp-3">${title}</strong>
             ${year ? `<span class="mt-1 font-label-sm text-label-sm text-on-surface-variant/50">${year}</span>` : ''}
           </div>`;
      const href = letterboxdFilmURL(film.slug);
      const openTag = href
        ? `<a href="${href}" target="_blank" rel="noopener" title="${title} — Letterboxd" class="group block relative aspect-[2/3] rounded-2xl overflow-hidden bg-surface-container ring-1 ring-outline-variant/25 shadow-[0_26px_60px_-20px_rgba(0,0,0,.65)] transition-transform duration-300 hover:-translate-y-1.5 hover:ring-primary-container/40">`
        : `<article class="group relative aspect-[2/3] rounded-2xl overflow-hidden bg-surface-container ring-1 ring-outline-variant/25 shadow-[0_26px_60px_-20px_rgba(0,0,0,.65)] transition-transform duration-300 hover:-translate-y-1.5">`;
      const closeTag = href ? '</a>' : '</article>';
      return poster
        ? `${openTag}
             <img src="${poster}" alt="${title}" onerror="posterErr(this)" class="absolute inset-0 w-full h-full object-cover transition-transform duration-500 group-hover:scale-[1.06]" loading="lazy"/>
             <div hidden>${emptyCard}</div>
             <div class="absolute inset-0 bg-gradient-to-t from-black/95 via-black/25 to-transparent"></div>
             ${badge}
             <div class="absolute inset-x-0 bottom-0 p-3.5">
               <strong class="block font-headline-md text-[14px] md:text-[15px] text-white leading-tight line-clamp-2">${title}</strong>
               ${year ? `<span class="mt-0.5 block font-label-sm text-label-sm text-white/55">${year}</span>` : ''}
             </div>
           ${closeTag}`
        : `${href ? openTag : '<article class="relative aspect-[2/3] rounded-2xl overflow-hidden bg-surface-container ring-1 ring-outline-variant/25">'}
             ${badge}${emptyCard}
           ${href ? closeTag : '</article>'}`;
    }).join('')
    : '<div class="col-span-full rounded-2xl border border-dashed border-outline-variant/30 p-10 text-center text-on-surface-variant">Letterboxd Fav 4 henüz alınamadı.</div>';

  const profileShareReady = favorites.length >= 4 && Boolean(accountSummary);
  $('btn-share-personality').classList.toggle('hidden', !profileShareReady);
  $('btn-share-personality').classList.toggle('flex', profileShareReady);

  if (!deferAuxiliary && !_topFilmsLoaded) loadTopFilms();
  if (!deferAuxiliary && !_recentLoaded) loadRecentFilms();
  if (!deferAuxiliary && !_bulletinLoaded) loadBulletin();
  applySyncJob(data.sync_job);
}

// ── Sinefil Akışı ────────────────────────────────────────────────────────
let _feedScope = 'community';
let _feedCursor = '';
let _feedPickedFilm = null;
let _feedFilmPickerMode = 'compose';
let _feedAuthor = '';
let _feedFollowFilterOpen = false;
let _feedFollowingUsers = [];
// When set, the feed is narrowed to one film — the trend behaves like a
// destination rather than a decoration.
let _feedFilm = { slug: '', title: '' };
let _threadId = '';
let _threadFrom = '';
const FEED_COMPOSER_PROMPTS = [
  'Bir film hakkında düşüncelerini paylaş',
  'Perde kapandıktan sonra aklında ne kaldı?',
  'Bu sahne sende nasıl bir iz bıraktı?',
  'Bugün izlediğin filmle iki cümle kur',
  'Bir oyunculuk, bir plan ya da final… paylaş',
  'Sinefillerin arasında bir not bırak',
];
const FEED_COMPOSER_PROMPTS_EN = [
  'Share your thoughts about a film',
  'What stayed with you after the credits?',
  'How did this scene stay with you?',
  'Write two sentences about the film you watched today',
  'Share a performance, a shot, or an ending…',
  'Leave a note among cinephiles',
];
let _feedComposerPromptIndex = 0;
let _feedComposerPromptTimer = null;

function startFeedComposerPromptFlow() {
  const input = $('feed-compose-text');
  if (!input || _feedComposerPromptTimer) return;
  const next = () => {
    if (input.value || document.activeElement === input) {
      _feedComposerPromptTimer = setTimeout(next, 1400);
      return;
    }
    const prompts = getLocale() === 'en' ? FEED_COMPOSER_PROMPTS_EN : FEED_COMPOSER_PROMPTS;
    const phrase = prompts[_feedComposerPromptIndex % prompts.length];
    _feedComposerPromptIndex += 1;
    let length = 0;
    const stream = () => {
      if (input.value || document.activeElement === input) {
        _feedComposerPromptTimer = setTimeout(next, 1000);
        return;
      }
      input.placeholder = phrase.slice(0, length) || ' ';
      length += 1;
      if (length <= phrase.length) _feedComposerPromptTimer = setTimeout(stream, 34);
      else _feedComposerPromptTimer = setTimeout(next, 2800);
    };
    stream();
  };
  _feedComposerPromptTimer = setTimeout(next, 700);
}

function feedRelativeTime(value) {
  const then = new Date(value).getTime();
  if (!then) return '';
  const minutes = Math.floor((Date.now() - then) / 60000);
  if (minutes < 1) return t('az önce');
  if (minutes < 60) return getLocale() === 'en' ? `${minutes} min` : `${minutes} dk`;
  if (minutes < 1440) return getLocale() === 'en' ? `${Math.floor(minutes / 60)} h` : `${Math.floor(minutes / 60)} sa`;
  const days = Math.floor(minutes / 1440);
  return days < 7 ? `${days} ${t('g')}` : new Date(then).toLocaleDateString(uiLocale(), { day: '2-digit', month: 'short' });
}

// Uzun bir yorum kartı kilitliyor: 220 karakterden sonrası "devamını oku"nun
// arkasında duruyor. Açmak metni yerinde büyütüyor, altındaki notlar da doğal
// olarak aşağı kayıyor.
const FEED_BODY_CLAMP = 220;

function feedBodyMarkup(raw) {
  const text = String(raw || '');
  if (!text.trim()) return '';
  const cls = 'mt-2 whitespace-pre-wrap break-words text-[15px] leading-relaxed text-on-surface';
  if (text.length <= FEED_BODY_CLAMP) {
    return `<p class="${cls}">${escapeHTML(text)}</p>`;
  }
  // Kesme noktası kelimenin ortasına düşmesin.
  let cut = text.lastIndexOf(' ', FEED_BODY_CLAMP);
  if (cut < FEED_BODY_CLAMP * 0.6) cut = FEED_BODY_CLAMP;
  return `<p class="${cls}" data-post-body>
    <span data-body-short>${escapeHTML(text.slice(0, cut).trimEnd())}…
      <button type="button" data-body-more class="ml-1 align-baseline text-primary-container hover:underline">devamını oku</button>
    </span>
    <span data-body-full class="hidden">${escapeHTML(text)}</span>
  </p>`;
}

function feedPostCard(post, { compact = false } = {}) {
  const author = post.author || {};
  const film = post.film;
  const name = escapeHTML(author.display_name || author.username || 'sinefil');
  const username = escapeHTML(author.username || '');
  const body = escapeHTML(post.body || '');
  const poster = film ? safeImageURL(film.poster_url) : '';
  const filmRail = film
    ? `<a href="${letterboxdFilmURL(film.slug) || '#'}" target="_blank" rel="noopener" title="${escapeHTML(film.title || 'Film')} — Letterboxd" class="group order-3 ml-auto w-[68px] shrink-0 self-start text-left sm:w-[78px]">
        <span class="block overflow-hidden rounded-xl border border-outline-variant/30 bg-surface-container shadow-[0_16px_34px_-22px_rgba(0,0,0,.95)] transition-transform duration-300 group-hover:-translate-y-1 group-hover:border-primary-container/50">
          ${poster
            ? `<img src="${poster}" alt="${escapeHTML(film.title || '')}" onerror="posterErr(this)" loading="lazy" class="aspect-[2/3] w-full object-cover bg-surface-container"/>`
            : `<span class="flex aspect-[2/3] items-center justify-center bg-surface-container text-on-surface-variant/35"><span class="material-symbols-outlined text-[28px]">movie</span></span>`}
        </span>
        <strong class="mt-2 block line-clamp-2 text-[11px] leading-snug text-on-surface group-hover:text-primary-container sm:text-[12px]">${escapeHTML(film.title || 'Film')}</strong>
        <span class="mt-1 block text-[10px] text-on-surface-variant/60">${escapeHTML(String(film.year || ''))}</span>
        <span class="mt-0.5 block line-clamp-2 text-[10px] leading-snug text-on-surface-variant">${escapeHTML(film.director || '')}</span>
      </a>`
    : '';
  // Günce kaydı da nottan farksız görünür: kartta okunacak şey yorumun
  // kendisi. Künye ve puan, Letterboxd'un verisini tekrar etmekten başka bir
  // şey yapmıyordu.
  // A spoiler stays covered until the reader asks for it — in a film community
  // that is a bigger trust question than profanity.
  const text = post.spoiler
    ? `<p class="mt-2 text-[15px] leading-relaxed"><button type="button" data-reveal-spoiler class="w-full rounded-lg bg-surface-variant/70 px-3 py-2 text-left text-sm text-on-surface-variant">Spoiler — göstermek için dokun</button><span class="hidden">${body}</span></p>`
    : feedBodyMarkup(post.body || '');
  return `<article class="border-b border-outline-variant/20 px-4 py-4 transition-colors hover:bg-surface-container/30" data-post-id="${escapeHTML(post.id)}">
    <div class="flex min-h-[128px] items-stretch gap-3">
      <div class="flex min-h-[132px] min-w-0 flex-1 flex-col pt-0.5">
        <div class="flex min-w-0 items-center gap-2.5">
          <button type="button" data-post-author="${username}" class="shrink-0" aria-label="@${username} profili">${peerAvatar(author)}</button>
          <div class="min-w-0 flex-1 leading-tight">
            <button type="button" data-post-author="${username}" class="block max-w-full truncate text-left text-sm font-bold text-on-surface hover:underline">${name}</button>
            <span class="mt-0.5 block truncate text-xs text-on-surface-variant/60">@${username}</span>
          </div>
        </div>
        ${text}
        <div class="feed-actions mt-auto flex h-9 shrink-0 items-center gap-4 text-sm text-on-surface-variant">
          <button type="button" data-post-like class="flex items-center gap-1.5 hover:text-primary-container transition-colors ${post.liked ? 'text-primary-container' : ''}">
            <span class="material-symbols-outlined text-[18px]" style="${post.liked ? "font-variation-settings:'FILL' 1" : ''}">favorite</span>
            <span data-like-count>${post.like_count || 0}</span>
          </button>
          ${compact ? '' : `<button type="button" data-post-open class="flex items-center gap-1.5 hover:text-primary-container transition-colors">
            <span class="material-symbols-outlined text-[18px]">chat_bubble</span>
            <span>${post.reply_count || 0}</span>
          </button>`}
          ${post.mine ? '<button type="button" data-post-delete class="flex items-center gap-1 text-on-surface-variant/60 hover:text-error transition-colors"><span class="material-symbols-outlined text-[18px]">delete</span></button>' : ''}
          ${post.mine ? '' : '<button type="button" data-post-report class="ml-auto flex items-center gap-1 text-on-surface-variant/55 hover:text-error transition-colors" title="Notu bildir"><span class="material-symbols-outlined text-[18px]">flag</span></button>'}
        </div>
      </div>
      <span class="order-2 shrink-0 self-start pt-1 font-label-sm text-label-sm text-on-surface-variant/55">${escapeHTML(feedRelativeTime(post.created_at))}</span>
      ${filmRail}
    </div>
  </article>`;
}

function renderFeedTrending(films) {
  const strip = $('feed-trending');
  const rail = $('rail-trending');
  if (!films.length) { strip.innerHTML = ''; rail.innerHTML = ''; return; }
  renderRailTrending(films);
  strip.innerHTML = `
    <p class="mb-2 font-label-sm text-label-sm uppercase tracking-wide text-on-surface-variant/60">Bu hafta en çok konuşulanlar</p>
    <div class="flex gap-3 overflow-x-auto pb-1">
      ${films.map(film => `<button type="button" data-trend-film="${escapeHTML(film.slug)}" data-trend-title="${escapeHTML(film.title)}" class="shrink-0 w-[86px] text-left">
        ${safeImageURL(film.poster_url)
          ? `<img src="${safeImageURL(film.poster_url)}" alt="" onerror="posterErr(this)" class="w-full aspect-[2/3] rounded-lg object-cover bg-surface-container"/>`
          : '<div class="w-full aspect-[2/3] rounded-lg bg-surface-container"></div>'}
        <p class="mt-1 truncate text-[11px] text-on-surface">${escapeHTML(film.title)}</p>
        <p class="text-[11px] text-on-surface-variant/50">${film.count} not</p>
      </button>`).join('')}
    </div>`;
}

// The rail is Twitter's right column: trends as a vertical list, then people.
function renderRailTrending(films) {
  $('rail-trending').innerHTML = `<div class="rounded-2xl border border-outline-variant/25 bg-surface-container/40 p-4">
    <p class="font-label-sm text-label-sm uppercase tracking-[.18em] text-primary-container">Bu hafta en çok konuşulanlar</p>
    <div class="mt-3 flex flex-col">${films.map((film, index) => `<button type="button" data-trend-film="${escapeHTML(film.slug)}" data-trend-title="${escapeHTML(film.title)}" class="flex items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-surface-container/70 transition-colors">
      <span class="w-4 shrink-0 text-sm text-on-surface-variant/40">${index + 1}</span>
      ${safeImageURL(film.poster_url)
        ? `<img src="${safeImageURL(film.poster_url)}" alt="" onerror="posterErr(this)" class="h-12 w-8 shrink-0 rounded object-cover bg-surface-container"/>`
        : '<div class="h-12 w-8 shrink-0 rounded bg-surface-container"></div>'}
      <span class="min-w-0 flex-1">
        <strong class="block truncate text-sm text-on-surface">${escapeHTML(film.title)}</strong>
        <span class="block text-xs text-on-surface-variant/50">${film.count} not</span>
      </span>
    </button>`).join('')}</div>
  </div>`;
}

async function searchFeedFilms() {
  const query = $('feed-film-search').value.trim();
  if (query.length < 2) { $('feed-film-results').innerHTML = ''; return; }
  try {
    const endpoint = _feedFilmPickerMode === 'filter'
      ? `/api/feed/films?q=${encodeURIComponent(query)}`
      : `/api/profile/watched?q=${encodeURIComponent(query)}`;
    const data = await apiJSON(endpoint);
    const films = (data.films || []).slice(0, 8).map(film => ({
      ...film, slug: film.slug || film.film_slug || '',
    })).filter(film => film.slug);
    $('feed-film-results').innerHTML = films.length
      ? films.map((film, index) => `<button type="button" data-feed-film-index="${index}" class="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm hover:bg-surface-variant">${letterFilmMarkup(film)}</button>`).join('')
      : '<p class="px-3 py-2 text-sm text-on-surface-variant/60">İzlediklerin arasında bulunamadı.</p>';
    $('feed-film-results')._feedFilms = films;
  } catch (_) { $('feed-film-results').innerHTML = ''; }
}

async function loadFeed({ append = false } = {}) {
  if (!_account) return;
  if (!append) { _feedCursor = ''; $('feed-list').innerHTML = '<p class="px-4 py-10 text-center text-sm text-on-surface-variant/60">Yükleniyor…</p>'; }
  try {
    const sort = _feedScope === 'community' ? 'engagement' : 'recent';
    const data = await apiJSON(`/api/feed?scope=${_feedScope}&sort=${sort}&cursor=${encodeURIComponent(_feedCursor)}&film=${encodeURIComponent(_feedFilm.slug || '')}&author=${encodeURIComponent(_feedAuthor)}`);
    const cards = (data.posts || []).map(post => feedPostCard(post)).join('');
    if (append) $('feed-list').insertAdjacentHTML('beforeend', cards);
    else if (cards) $('feed-list').innerHTML = cards;
    else if (_feedFilm.slug) $('feed-list').innerHTML = '<p class="px-4 py-12 text-center text-sm text-on-surface-variant">Bu film hakkında not yok.</p>';
    else if (_feedScope === 'following') await renderFollowSuggestions();
    else $('feed-list').innerHTML = '<p class="px-4 py-12 text-center text-sm text-on-surface-variant">Henüz not yok. İlkini sen yaz.</p>';
    _feedCursor = data.next_cursor || '';
    $('btn-feed-more').classList.toggle('hidden', !_feedCursor);
  } catch (error) {
    $('feed-list').innerHTML = `<div class="px-4 py-10 text-center">
      <p class="text-sm text-error">${escapeHTML(error.message || 'Akış yüklenemedi.')}</p>
      <button type="button" id="btn-feed-retry" class="mt-3 rounded-full border border-outline-variant/40 px-4 py-2 text-sm text-on-surface-variant hover:text-on-surface transition-colors">Tekrar dene</button>
    </div>`;
  }
}

// An empty "takip ettiklerin" is a dead end unless it says who to follow. The
// names come from the same taste matching the Sinefil Sineması page uses.
async function renderFollowSuggestions() {
  const list = $('feed-list');
  list.innerHTML = '<p class="px-4 py-10 text-center text-sm text-on-surface-variant">Takip ettiklerin henüz not yazmamış.</p>';
  try {
    const [data, mine] = await Promise.all([
      apiJSON('/api/sinefil-alani?q=&page=1&per_page=8'),
      apiJSON(`/api/users/${encodeURIComponent(_account.username)}/following`).catch(() => ({ users: [] })),
    ]);
    // Someone already followed is not a suggestion — they simply have not written yet.
    const already = new Set((mine.users || []).map(user => user.username));
    const people = (data.profiles || [])
      .filter(person => person.username && !already.has(person.username))
      .slice(0, 5);
    if (!people.length) return;
    list.insertAdjacentHTML('beforeend', `<div class="mx-4 rounded-2xl border border-outline-variant/25 bg-surface-container/60 p-4">
      <p class="font-label-sm text-label-sm uppercase tracking-[.18em] text-primary-container">Kimi takip etmeli?</p>
      <div class="mt-3 flex flex-col gap-2">${people.map(person => `<div class="flex items-center gap-3">
        <button type="button" data-post-author="${escapeHTML(person.username)}" class="shrink-0">${peerAvatar(person)}</button>
        <button type="button" data-post-author="${escapeHTML(person.username)}" class="min-w-0 flex-1 text-left">
          <strong class="block truncate text-sm text-on-surface">${escapeHTML(person.display_name || person.username)}</strong>
          <span class="block truncate text-xs text-on-surface-variant/60">${escapeHTML(person.match_note || `@${person.username}`)}</span>
        </button>
        ${followButton({ username: person.username, following: false, is_me: false })}
      </div>`).join('')}</div>
    </div>`);
  } catch (_) {}
}

// Filme daraltılmış akışın başı artık filmin künyesi: poster, kaç üye
// izlemiş, topluluk ortalaması ve bu hafta perdede olup olmadığı. Notların
// kendisi hemen altında akıyor.
function renderFeedFilmChip() {
  const box = $('feed-film-filter');
  box.classList.toggle('hidden', !_feedFilm.slug);
  if (!_feedFilm.slug) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="flex items-start gap-3">
      <div class="h-[96px] w-16 shrink-0 rounded-lg bg-surface-container"></div>
      <div class="min-w-0 flex-1">
        <strong class="block truncate font-headline-md text-[18px] text-on-surface">${escapeHTML(_feedFilm.title)}</strong>
        <span class="mt-1 block text-sm text-on-surface-variant/60">Künye yükleniyor…</span>
      </div>
      <button type="button" id="feed-film-clear" class="shrink-0 rounded-full border border-outline-variant/40 px-3 py-1 text-xs text-on-surface-variant hover:text-on-surface">Kapat</button>
    </div>`;
  loadFilmOverview(_feedFilm.slug);
}

async function loadFilmOverview(slug) {
  let data;
  try {
    data = await apiJSON(`/api/films/${encodeURIComponent(slug)}`);
  } catch (_) { return; }
  if (_feedFilm.slug !== slug) return;   // arada başka bir filme geçilmiş
  const film = data.film || {};
  const community = data.community || {};
  const poster = safeImageURL(film.poster_url);
  const href = letterboxdFilmURL(slug);
  const art = poster
    ? `<img src="${poster}" alt="" onerror="posterErr(this)" class="h-full w-full object-cover"/>`
    : '<span class="flex h-full w-full items-center justify-center text-on-surface-variant/30"><span class="material-symbols-outlined">movie</span></span>';
  const facts = [
    community.watched_count
      ? `<span><strong class="text-on-surface">${community.watched_count}</strong> üye izlemiş</span>`
      : '',
    community.average
      ? `<span class="inline-flex items-center gap-1"><span class="material-symbols-outlined text-[15px] text-primary-container" style="font-variation-settings:'FILL' 1">star</span><strong class="text-on-surface">${community.average}</strong> topluluk ortalaması</span>`
      : '',
    data.mine?.watched ? '<span class="text-primary-container">İzledin</span>' : '',
  ].filter(Boolean).join('<span class="text-on-surface-variant/30">·</span>');
  const venues = (data.venues || []).filter(venue => venue.name);
  const screen = venues.length
    ? `<p class="mt-2 flex flex-wrap items-center gap-2 font-label-sm text-label-sm text-tertiary-container">
        <span class="inline-flex items-center gap-1"><span class="material-symbols-outlined text-[15px]">theaters</span>Bu hafta perdede</span>
        ${venues.slice(0, 3).map(venue => venue.url
          ? `<a href="${escapeHTML(venue.url)}" target="_blank" rel="noopener" class="underline decoration-tertiary-container/40">${escapeHTML(venue.name)}</a>`
          : `<span>${escapeHTML(venue.name)}</span>`).join('')}
      </p>`
    : '';
  $('feed-film-filter').innerHTML = `<div class="flex items-start gap-3">
      ${href ? `<a href="${href}" target="_blank" rel="noopener" class="block h-[96px] w-16 shrink-0 overflow-hidden rounded-lg bg-surface-container">${art}</a>` : `<span class="block h-[96px] w-16 shrink-0 overflow-hidden rounded-lg bg-surface-container">${art}</span>`}
      <div class="min-w-0 flex-1">
        <strong class="block font-headline-md text-[18px] leading-tight text-on-surface">${escapeHTML(film.title || _feedFilm.title)}${film.year ? ` <span class="text-on-surface-variant/50">${escapeHTML(String(film.year))}</span>` : ''}</strong>
        ${film.director ? `<span class="mt-0.5 block truncate text-sm text-tertiary-container">${escapeHTML(film.director)}</span>` : ''}
        ${facts ? `<p class="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-on-surface-variant">${facts}</p>` : ''}
        ${screen}
      </div>
      <button type="button" id="feed-film-clear" class="shrink-0 rounded-full border border-outline-variant/40 px-3 py-1 text-xs text-on-surface-variant hover:text-on-surface">Kapat</button>
    </div>`;
}

function openFilmFeed(slug, title) {
  _feedFilm = { slug, title };
  renderFeedFilmChip();
  loadFeed();
}

function renderFeedFollowingFilter() {
  const filter = $('feed-follow-filter');
  // Kişi şeridi sekmenin altında hep durmaz: filtre ikonundan açılır. Seçili
  // bir kişi varken açık kalır, yoksa kullanıcı neye baktığını göremezdi.
  const show = _feedScope === 'following' && (_feedFollowFilterOpen || Boolean(_feedAuthor));
  filter.classList.toggle('hidden', !show);
  if (!show) return;
  const pill = (label, username = '') => `<button type="button" data-feed-author="${escapeHTML(username)}" class="shrink-0 rounded-full border px-3 py-1.5 text-xs transition-colors ${_feedAuthor === username ? 'border-primary-container/50 bg-primary-container/15 text-primary-container' : 'border-outline-variant/30 text-on-surface-variant hover:border-outline-variant/60 hover:text-on-surface'}">${label}</button>`;
  filter.innerHTML = `<div class="flex gap-2 overflow-x-auto pb-1">${pill('Tümü')}${_feedFollowingUsers.map(person => pill(`@${escapeHTML(person.username)}`, person.username)).join('')}</div>`;
}

async function loadFeedFollowingUsers() {
  if (!_account?.username) return;
  try {
    const data = await apiJSON(`/api/users/${encodeURIComponent(_account.username)}/following`);
    _feedFollowingUsers = (data.users || []).filter(user => user.username);
  } catch (_) { _feedFollowingUsers = []; }
  renderFeedFollowingFilter();
}

async function setFeedScope(scope, { openFollowFilter = false } = {}) {
  _feedScope = scope;
  _feedAuthor = '';
  _feedFollowFilterOpen = openFollowFilter;
  $('feed-sort-note').classList.toggle('hidden', scope !== 'community');
  document.querySelectorAll('[data-feed-scope]').forEach(button => {
    button.classList.toggle('is-active', button.dataset.feedScope === scope);
  });
  if (scope === 'following') await loadFeedFollowingUsers();
  else renderFeedFollowingFilter();
  return loadFeed();
}

async function openFeed() {
  showView('feed');
  closeComposerOnPhone();
  _feedFilm = { slug: '', title: '' };
  renderFeedFilmChip();
  $('feed-sort-note').classList.toggle('hidden', _feedScope !== 'community');
  // Serial, not parallel: a burst of six calls at boot is what made a
  // transient read failure look like an empty timeline.
  await setFeedScope(_feedScope);
  try { renderFeedTrending((await apiJSON('/api/feed/trending')).films || []); } catch (_) {}
}

// A picked film is shown as a card with an ×; changing your mind has to be
// one click, not a page reload.
function renderComposerFilm() {
  const chip = $('feed-compose-film-chip');
  const picker = $('feed-compose-film');
  const film = _feedPickedFilm;
  picker.classList.toggle('hidden', Boolean(film));
  chip.classList.toggle('hidden', !film);
  chip.classList.toggle('flex', Boolean(film));
  chip.classList.toggle('mt-3', Boolean(film));
  if (!film) { chip.innerHTML = ''; return; }
  const poster = safeImageURL(film.poster_url);
  chip.innerHTML = `${poster
      ? `<img src="${poster}" alt="" onerror="posterErr(this)" class="h-14 w-10 shrink-0 rounded-md object-cover bg-surface-container"/>`
      : '<div class="h-14 w-10 shrink-0 rounded-md bg-surface-container"></div>'}
    <span class="min-w-0 flex-1">
      <strong class="block truncate text-sm text-on-surface">${escapeHTML(film.title || '')}</strong>
      <span class="block truncate text-xs text-on-surface-variant/60">${[film.year, film.director].filter(Boolean).map(escapeHTML).join(' · ')}</span>
    </span>
    <button type="button" id="feed-compose-film-change" class="shrink-0 rounded-lg px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface">Değiştir</button>
    <button type="button" id="feed-compose-film-clear" class="shrink-0 rounded-lg p-1.5 text-on-surface-variant hover:text-error" aria-label="Filmi kaldır"><span class="material-symbols-outlined text-[18px]">close</span></button>`;
}

function clearComposerFilm() {
  _feedPickedFilm = null;
  $('feed-compose-film-label').textContent = 'Hangi film hakkında yazacaksın?';
  renderComposerFilm();
}

async function submitPost() {
  const button = $('btn-feed-post');
  const error = $('feed-compose-error');
  const body = $('feed-compose-text').value.trim();
  if (!_feedPickedFilm) {
    error.textContent = 'Önce hakkında yazacağın filmi seç.';
    error.classList.remove('hidden');
    return;
  }
  if (!body) { error.textContent = 'Birkaç kelime yaz.'; error.classList.remove('hidden'); return; }
  button.disabled = true;
  error.classList.add('hidden');
  try {
    await apiJSON('/api/posts', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        body,
        film_slug: _feedPickedFilm.slug,
        tmdb_id: _feedPickedFilm.tmdb_id || null,
        film_title: _feedPickedFilm.title || '',
        film_year: _feedPickedFilm.year || null,
        spoiler: $('feed-compose-spoiler').checked,
      }),
    });
    $('feed-compose-text').value = '';
    $('feed-compose-spoiler').checked = false;
    $('feed-compose-count').textContent = '420';
    clearComposerFilm();
    closeComposerOnPhone();
    await loadFeed();
  } catch (err) {
    error.textContent = err.message || 'Not paylaşılamadı.';
    error.classList.remove('hidden');
  } finally { button.disabled = false; }
}

async function openThread(postId, { from = '' } = {}) {
  _threadId = postId;
  // Remember where the reader came from so "geri" is not always the feed.
  _threadFrom = from || _currentFeedOrigin();
  showView('thread');
  $('thread-root').innerHTML = '<p class="py-8 text-center text-sm text-on-surface-variant/60">Yükleniyor…</p>';
  $('thread-replies').innerHTML = '';
  try {
    const data = await apiJSON(`/api/posts/${encodeURIComponent(postId)}`);
    $('thread-root').innerHTML = feedPostCard(data.post, { compact: true });
    $('thread-replies').innerHTML = (data.replies || []).map(reply => feedPostCard(reply, { compact: true })).join('');
  } catch (error) {
    $('thread-root').innerHTML = `<p class="rounded-xl bg-error-container/30 px-4 py-3 text-sm text-error">${escapeHTML(error.message || 'Not yüklenemedi.')}</p>`;
  }
}

async function submitReply() {
  const button = $('btn-thread-reply');
  const error = $('thread-reply-error');
  const body = $('thread-reply-text').value.trim();
  if (!body || !_threadId) return;
  button.disabled = true;
  error.classList.add('hidden');
  try {
    await apiJSON(`/api/posts/${encodeURIComponent(_threadId)}/replies`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ body }),
    });
    $('thread-reply-text').value = '';
    await openThread(_threadId);
  } catch (err) {
    error.textContent = err.message || 'Cevap gönderilemedi.';
    error.classList.remove('hidden');
  } finally { button.disabled = false; }
}

async function togglePostLike(card) {
  const postId = card.dataset.postId;
  const button = card.querySelector('[data-post-like]');
  const liked = button.classList.contains('text-primary-container');
  try {
    const data = await apiJSON(`/api/posts/${encodeURIComponent(postId)}/like`, {
      method: liked ? 'DELETE' : 'POST',
      headers: csrfHeaders(),
    });
    button.classList.toggle('text-primary-container', data.liked);
    button.querySelector('.material-symbols-outlined').style.fontVariationSettings = data.liked ? "'FILL' 1" : '';
    button.querySelector('[data-like-count]').textContent = data.like_count;
  } catch (_) {}
}

async function reportPost(card) {
  const postId = card.dataset.postId;
  const detail = window.prompt('Bu not için kısa bir bildirim nedeni yazabilirsin (isteğe bağlı):', '');
  if (detail === null) return;
  try {
    await apiJSON(`/api/posts/${encodeURIComponent(postId)}/report`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ category: 'other', detail: detail.slice(0, 500) }),
    });
    window.alert('Bildirim alındı. İncelenecek.');
  } catch (error) {
    window.alert(error.message || 'Bu not bildirilemedi.');
  }
}

// ── Sinefil profili, takip listeleri ve bildirimler ─────────────────────
let _userPage = { username: '', cursor: '', from: 'feed' };
let _followsFrom = '';

function followButton(profile) {
  if (profile.is_me) return '';
  const status = profile.follow_status || (profile.following ? 'accepted' : 'none');
  const active = status === 'accepted';
  const pending = status === 'pending';
  const label = pending ? 'İstek gönderildi' : (active ? 'Takiptesin' : 'Takip et');
  const style = active || pending
    ? 'border border-outline-variant/40 text-on-surface-variant hover:border-error/40 hover:text-error'
    : 'bg-primary-container text-on-primary-container';
  return `<button type="button" data-follow="${escapeHTML(profile.username)}" data-follow-status="${status}" class="inline-flex h-9 shrink-0 items-center rounded-full px-4 font-label-sm text-label-sm uppercase tracking-wide transition-colors ${style}">${label}</button>`;
}

function userHeaderMarkup(profile) {
  const name = escapeHTML(profile.display_name || profile.username);
  const avatar = safeImageURL(profile.avatar_url);
  const stats = profile.letterboxd_stats || {};
  const watched = Number(stats.films || stats.watched || 0);
  const locked = Boolean(profile.private_account) && !profile.can_view && !profile.is_me;
  const canLetter = !profile.is_me && !locked && Boolean(profile.letter_receiving_enabled);
  const canBlend = !profile.is_me && !locked
    && profile.follow_status === 'accepted' && Boolean(profile.follows_you);
  // Mektup ve blend, takip düğmesiyle aynı satırda ve aynı boyda: hepsi 36px
  // yüksekliğinde yuvarlak düğmeler, mektup takibin hemen solunda.
  const iconAction = 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-colors';
  const letterAction = canLetter
    ? `<button type="button" data-user-letter="${escapeHTML(profile.username)}" aria-label="Mektup yaz" title="Mektup yaz" class="${iconAction} border-secondary-container/45 bg-secondary-container/10 text-secondary-container hover:bg-secondary-container/20"><span class="material-symbols-outlined text-[19px]">mail</span></button>`
    : '';
  const blendAction = canBlend
    ? `<button type="button" data-user-blend="${escapeHTML(profile.username)}" aria-label="Blend yap" title="Blend yap" class="${iconAction} border-primary-container/45 bg-primary-container/10 text-primary-container hover:bg-primary-container/20"><span class="material-symbols-outlined text-[19px]">join_inner</span></button>`
    : '';
  return `<div class="rounded-2xl border border-outline-variant/25 bg-surface-container/60 p-5">
    <div class="flex items-start gap-4">
      ${avatar
        ? `<img src="${avatar}" alt="" class="h-20 w-20 shrink-0 rounded-full object-cover border border-outline-variant/30"/>`
        : `<div class="flex h-20 w-20 shrink-0 items-center justify-center rounded-full bg-surface-container text-2xl font-bold text-primary-container">${name[0] || '?'}</div>`}
      <div class="min-w-0 flex-1">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h1 class="truncate font-headline-md text-headline-md text-on-surface">${name}</h1>
            <p class="text-sm text-on-surface-variant/70">@${escapeHTML(profile.username)}</p>
          </div>
          <div class="flex shrink-0 items-center gap-2">${blendAction}${letterAction}${followButton(profile)}</div>
        </div>
        ${locked ? '<span class="mt-2 inline-flex items-center gap-1 rounded-full bg-surface-variant/60 px-2 py-0.5 text-[11px] text-on-surface-variant"><span class="material-symbols-outlined text-[13px]">lock</span>Kilitli hesap</span>' : (profile.follows_you ? '<span class="mt-2 inline-block rounded-full bg-surface-variant/60 px-2 py-0.5 text-[11px] text-on-surface-variant">Seni takip ediyor</span>' : '')}
      </div>
    </div>
    ${locked ? '<p class="mt-4 text-sm text-on-surface-variant">Bu hesabın notları, zevk profili ve takip listeleri kilitli.</p>' : `<div class="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
      <span class="text-on-surface-variant"><strong class="text-on-surface">${profile.note_count}</strong> not</span>
      <span class="text-on-surface-variant"><strong class="text-on-surface">${profile.follower_count || 0}</strong> takipçi</span>
      <span class="text-on-surface-variant"><strong class="text-on-surface">${profile.following_count || 0}</strong> takip</span>
      ${watched ? `<span class="text-on-surface-variant"><strong class="text-on-surface">${watched}</strong> film izlemiş</span>` : ''}
    </div>`}
  </div>`;
}

function userFavoritesMarkup(favorites) {
  const films = (favorites || []).filter(Boolean);
  if (!films.length) return '';
  return `<p class="font-label-sm text-label-sm uppercase tracking-[.18em] text-tertiary-container">Fav 4</p>
    <div class="mt-2 grid grid-cols-4 gap-2">${films.map(film => {
      const poster = safeImageURL(film.poster_url);
      const href = letterboxdFilmURL(film.slug);
      const art = poster
        ? `<img src="${poster}" alt="${escapeHTML(film.title || '')}" onerror="posterErr(this)" loading="lazy" class="w-full aspect-[2/3] rounded-lg object-cover bg-surface-container"/>`
        : `<div class="w-full aspect-[2/3] rounded-lg bg-surface-container"></div>`;
      return href ? `<a href="${href}" target="_blank" rel="noopener">${art}</a>` : art;
    }).join('')}</div>`;
}

async function openUserPage(username, { from = 'feed' } = {}) {
  if (!username) return;
  if (_account?.username && username.toLowerCase() === _account.username.toLowerCase()) {
    showView('profile');
    if (_persistedProfile) renderPersistedProfile(_persistedProfile);
    else loadProfile();
    return;
  }
  _userPage = { username, cursor: '', from };
  showView('user');
  $('user-header').innerHTML = '<p class="py-8 text-center text-sm text-on-surface-variant/60">Yükleniyor…</p>';
  $('user-favorites').innerHTML = '';
  $('user-posts').innerHTML = '';
  $('btn-user-more').classList.add('hidden');
  try {
    const data = await apiJSON(`/api/users/${encodeURIComponent(username)}`);
    $('user-header').innerHTML = userHeaderMarkup(data.profile);
    const locked = Boolean(data.profile.private_account) && !data.profile.can_view && !data.profile.is_me;
    $('user-favorites').innerHTML = locked ? '' : userFavoritesMarkup(data.profile.favorites);
    $('user-posts').innerHTML = locked
      ? '<p class="py-8 text-center text-sm text-on-surface-variant/60">Bu hesap kilitli. Notlarını ve zevk profilini görmek için takip isteğinin kabul edilmesini bekle.</p>'
      : (data.posts || []).length
      ? data.posts.map(post => feedPostCard(post)).join('')
      : '<p class="py-8 text-center text-sm text-on-surface-variant/60">Henüz not yazmamış.</p>';
    _userPage.cursor = data.next_cursor || '';
    $('btn-user-more').classList.toggle('hidden', !_userPage.cursor);
  } catch (error) {
    $('user-header').innerHTML = `<p class="rounded-xl bg-error-container/30 px-4 py-3 text-sm text-error">${escapeHTML(error.message || 'Profil açılamadı.')}</p>`;
  }
}

async function loadMoreUserPosts() {
  if (!_userPage.cursor) return;
  const button = $('btn-user-more');
  button.disabled = true;
  try {
    const data = await apiJSON(`/api/users/${encodeURIComponent(_userPage.username)}?cursor=${encodeURIComponent(_userPage.cursor)}`);
    $('user-posts').insertAdjacentHTML('beforeend', (data.posts || []).map(post => feedPostCard(post)).join(''));
    _userPage.cursor = data.next_cursor || '';
    button.classList.toggle('hidden', !_userPage.cursor);
  } catch (_) {} finally { button.disabled = false; }
}

async function toggleFollow(button) {
  const username = button.dataset.follow;
  const status = button.dataset.followStatus || 'none';
  const following = status !== 'none';
  button.disabled = true;
  try {
    const response = await apiJSON(`/api/users/${encodeURIComponent(username)}/follow`, {
      method: following ? 'DELETE' : 'POST',
      headers: csrfHeaders(),
    });
    if ($('view-user').classList.contains('hidden')) {
      // In a list the row flips in place; the page stays where it was.
      button.outerHTML = followButton({ username, follow_status: response.follow_status || 'none', is_me: false });
    } else {
      await openUserPage(username, { from: _userPage.from });
    }
  } catch (_) {} finally { button.disabled = false; }
}

async function openFollows(username, kind) {
  _followsFrom = username;
  showView('follows');
  $('follows-title').textContent = kind === 'followers' ? 'Takipçiler' : 'Takip edilenler';
  $('follows-list').innerHTML = '<p class="py-8 text-center text-sm text-on-surface-variant/60">Yükleniyor…</p>';
  try {
    const data = await apiJSON(`/api/users/${encodeURIComponent(username)}/${kind}`);
    const users = data.users || [];
    $('follows-list').innerHTML = users.length
      ? users.map(user => `<div class="flex items-center gap-3 rounded-xl border border-outline-variant/25 bg-surface-container/50 p-3">
          <button type="button" data-post-author="${escapeHTML(user.username)}" class="shrink-0">${peerAvatar(user)}</button>
          <button type="button" data-post-author="${escapeHTML(user.username)}" class="min-w-0 flex-1 text-left">
            <strong class="block truncate text-sm text-on-surface">${escapeHTML(user.display_name || user.username)}</strong>
            <span class="block truncate text-xs text-on-surface-variant/60">@${escapeHTML(user.username)}</span>
          </button>
          ${followButton({ ...user, is_me: user.is_me })}
        </div>`).join('')
      : '<p class="py-8 text-center text-sm text-on-surface-variant/60">Burada kimse yok.</p>';
  } catch (error) {
    $('follows-list').innerHTML = `<p class="rounded-xl bg-error-container/30 px-4 py-3 text-sm text-error">${escapeHTML(error.message || 'Liste açılamadı.')}</p>`;
  }
}

async function openProfileFollows(kind) {
  if (!_account?.username) return;
  const dialog = $('dialog-profile-follows');
  $('profile-follows-title').textContent = kind === 'followers' ? 'Takipçilerin' : 'Takip ettiklerin';
  $('profile-follows-list').innerHTML = '<p class="py-8 text-center text-sm text-on-surface-variant/60">Yükleniyor…</p>';
  if (!dialog.open) dialog.showModal();
  try {
    const data = await apiJSON(`/api/users/${encodeURIComponent(_account.username)}/${kind}`);
    const users = data.users || [];
    $('profile-follows-list').innerHTML = users.length
      ? users.map(user => `<button type="button" data-profile-follow-user="${escapeHTML(user.username)}" class="flex w-full items-center gap-3 rounded-xl border border-outline-variant/20 bg-surface-container/45 p-3 text-left hover:bg-surface-container transition-colors"><span class="shrink-0">${peerAvatar(user)}</span><span class="min-w-0 flex-1"><strong class="block truncate text-sm text-on-surface">${escapeHTML(user.display_name || user.username)}</strong><span class="block truncate text-xs text-on-surface-variant/60">@${escapeHTML(user.username)}</span></span><span class="material-symbols-outlined text-on-surface-variant/50">chevron_right</span></button>`).join('')
      : '<p class="py-8 text-center text-sm text-on-surface-variant/60">Burada henüz kimse yok.</p>';
  } catch (error) {
    $('profile-follows-list').innerHTML = `<p class="rounded-xl bg-error-container/30 px-4 py-3 text-sm text-error">${escapeHTML(error.message || 'Liste açılamadı.')}</p>`;
  }
}

function notificationRow(item) {
  const actor = item.actor || {};
  const who = escapeHTML(actor.display_name || actor.username || 'Bir sinefil');
  const post = item.post;
  const excerpt = post ? escapeHTML((post.body || '').slice(0, 90)) : '';
  const film = post && post.film_title ? escapeHTML(post.film_title) : '';
  const what = {
    like: 'notunu beğendi',
    reply: 'notuna cevap yazdı',
    follow: 'seni takip etmeye başladı',
    follow_request: 'sana takip isteği gönderdi',
    follow_accepted: 'takip isteğini kabul etti',
    letter: 'sana bir mektup gönderdi',
    blend_request: 'sana Blend isteği gönderdi',
    blend_accepted: 'Blend isteğini kabul etti',
    blend_rejected: 'Blend isteğini reddetti',
    // Aktörü olmayan tek tür: sistemden gelir.
    bulletin: 'İzleme listendeki bir film bu hafta perdede',
  }[item.kind] || 'bir şey yaptı';
  const icon = { bulletin: 'theaters', like: 'favorite', reply: 'chat_bubble', follow: 'person_add', follow_request: 'person_add', follow_accepted: 'how_to_reg', letter: 'mail', blend_request: 'join_inner', blend_accepted: 'handshake', blend_rejected: 'close' }[item.kind] || 'notifications';
  const destination = item.kind === 'letter'
    ? 'inbox'
    : (item.kind.startsWith('blend_') ? 'blends' : (item.kind === 'bulletin' ? 'profile' : ''));
  const unread = !item.read_at;
  return `<div class="flex items-start gap-3 rounded-xl border p-3 transition-colors ${unread ? 'border-primary-container/35 bg-primary-container/10' : 'border-outline-variant/25 bg-surface-container/40'}"
      ${post ? `data-notification-thread="${escapeHTML(post.thread_id)}"` : ''} ${destination ? `data-notification-destination="${destination}"` : ''} role="button" tabindex="0">
    <span class="material-symbols-outlined mt-0.5 text-[18px] text-primary-container">${icon}</span>
    <div class="min-w-0 flex-1">
      <p class="text-sm text-on-surface">${actor.username
        ? `<button type="button" data-post-author="${escapeHTML(actor.username)}" class="font-bold hover:underline">${who}</button> ${what}`
        : what}</p>
      ${film ? `<p class="mt-0.5 text-xs text-on-surface-variant/60">${film}</p>` : ''}
      ${excerpt ? `<p class="mt-1 truncate text-sm text-on-surface-variant">“${excerpt}”</p>` : ''}
      ${item.kind === 'follow_request' && actor.username ? `<div class="mt-3 flex gap-2"><button type="button" data-follow-request="${escapeHTML(actor.username)}" data-follow-decision="accepted" class="rounded-lg bg-primary-container px-3 py-2 text-xs font-bold text-on-primary-container">Kabul et</button><button type="button" data-follow-request="${escapeHTML(actor.username)}" data-follow-decision="rejected" class="rounded-lg border border-outline-variant/30 px-3 py-2 text-xs text-on-surface-variant">Reddet</button></div>` : ''}
      <p class="mt-1 text-xs text-on-surface-variant/50">${escapeHTML(feedRelativeTime(item.created_at))}</p>
    </div>
  </div>`;
}

async function openNotifications() {
  showView('notifications');
  $('notifications-list').innerHTML = '<p class="py-8 text-center text-sm text-on-surface-variant/60">Yükleniyor…</p>';
  try {
    const data = await apiJSON('/api/notifications');
    const items = data.notifications || [];
    $('notifications-list').innerHTML = items.length
      ? items.map(notificationRow).join('')
      : '<p class="py-8 text-center text-sm text-on-surface-variant/60">Henüz bildirim yok.</p>';
  } catch (error) {
    $('notifications-list').innerHTML = `<p class="rounded-xl bg-error-container/30 px-4 py-3 text-sm text-error">${escapeHTML(error.message || 'Bildirimler açılamadı.')}</p>`;
  }
  // Reading the list marks it read server-side; the badges follow.
  refreshFeedBadge();
}

// ── Sinema gündemi — yatay kart şeridi, profil boyandıktan sonra ─────────
let _bulletinLoaded = false;
let _bulletinData = null;
let _bulletinExpanded = false;

function bulletinFilmCard(film) {
  const title = escapeHTML(film.title || 'Film');
  const poster = safeImageURL(film.poster_url);
  const year = film.year ? escapeHTML(String(film.year)) : '';
  const note = film.note ? escapeHTML(film.note) : '';
  const venues = (film.venues || []).filter(venue => venue && venue.name);
  const href = letterboxdFilmURL(film.slug);
  const art = poster
    ? `<img src="${poster}" alt="" onerror="posterErr(this)" loading="lazy" class="w-full aspect-[2/3] rounded-xl object-cover bg-surface-container"/>`
    : `<div class="w-full aspect-[2/3] rounded-xl bg-surface-container flex items-center justify-center"><span class="material-symbols-outlined text-on-surface-variant/25 text-[34px]">movie</span></div>`;
  const cover = href
    ? `<a href="${href}" target="_blank" rel="noopener" title="${title} — Letterboxd" class="block">${art}</a>`
    : art;
  // The strip clips overflow, so a dropdown would be cut off; the venue list
  // opens in a dialog instead, which also works better on a phone.
  const action = venues.length > 1
    ? `<button type="button" data-bulletin-venues="${escapeHTML(String(film.tmdb_id || film.slug || film.title))}" class="mt-2 w-full rounded-lg border border-tertiary-container/30 px-2 py-1.5 text-[11px] uppercase tracking-wide text-tertiary-container hover:bg-tertiary-container/10 transition-colors">${venues.length} sinema</button>`
    : (venues[0]?.url
      ? `<a href="${escapeHTML(venues[0].url)}" target="_blank" rel="noopener" class="mt-2 block w-full rounded-lg border border-tertiary-container/30 px-2 py-1.5 text-center text-[11px] uppercase tracking-wide text-tertiary-container hover:bg-tertiary-container/10 transition-colors">${venues[0].film_page ? 'Filmin sayfası' : 'Sinema programı'}</a>`
      : '');
  const highlight = film.highlight ? 'ring-1 ring-tertiary-container/40' : '';
  return `<article class="shrink-0 w-[150px] sm:w-[168px] rounded-2xl ${highlight} p-2">
    ${cover}
    <strong class="mt-2 block font-headline-md text-[14px] leading-tight text-on-surface line-clamp-2">${title}</strong>
    ${year ? `<span class="block text-[11px] text-on-surface-variant/50">${year}</span>` : ''}
    ${note ? `<span class="mt-1 block text-[11px] leading-snug text-tertiary-container line-clamp-2">${note}</span>` : ''}
    ${action}
  </article>`;
}

function bulletinMoreCard(remaining) {
  return `<article class="shrink-0 w-[150px] sm:w-[168px] p-2 flex">
    <button type="button" id="bulletin-more" class="flex w-full flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-outline-variant/35 text-on-surface-variant hover:text-tertiary-container hover:border-tertiary-container/45 transition-colors">
      <span class="material-symbols-outlined text-[26px]">more_horiz</span>
      <span class="px-2 text-center text-[12px] leading-tight">Dahasını göster<br/><span class="text-on-surface-variant/50">+${remaining} film</span></span>
    </button>
  </article>`;
}

function renderBulletin(data) {
  const section = $('profile-bulletin');
  if (!data.enabled) { section.classList.add('hidden'); return; }
  section.classList.remove('hidden');
  _bulletinData = data;
  _bulletinExpanded = false;

  const select = $('bulletin-venue');
  const venues = data.venues || [];
  if (select.options.length <= 1 && venues.length) {
    select.innerHTML = ['<option value="">Tüm sinemalar</option>']
      .concat(venues.map(venue =>
        `<option value="${escapeHTML(venue.slug)}">${escapeHTML(venue.name)} (${venue.count})</option>`))
      .join('');
  }
  paintBulletin();
}

function paintBulletin() {
  const data = _bulletinData;
  if (!data) return;
  const chosen = $('bulletin-venue').value || '';
  const films = (data.films || []).filter(film =>
    !chosen || (film.venues || []).some(venue => venue.slug === chosen));

  if (!films.length) {
    $('bulletin-nav').classList.add('hidden');
    $('bulletin-nav').classList.remove('flex');
    $('bulletin-body').innerHTML = `<p class="rounded-2xl border border-dashed border-outline-variant/30 p-8 text-center text-sm text-on-surface-variant">${
      data.preparing
        ? 'Bu haftanın programı hazırlanıyor; birazdan tekrar bak.'
        : 'Bu filtreye uyan gösterim yok.'
    }</p>`;
    return;
  }

  // Priority films lead; the rest stay one tap away instead of making the
  // strip endless on first paint.
  // Eşik sunucudan geliyor: kademe sayısı değişince burası yanlış kalmasın.
  const highlighted = films.filter(film => film.highlight);
  const rest = films.filter(film => !film.highlight);
  const lead = highlighted.length ? highlighted : rest.slice(0, 12);
  const remainder = highlighted.length ? rest : rest.slice(12);
  const shown = _bulletinExpanded ? films : lead;
  const hidden = _bulletinExpanded ? 0 : remainder.length;

  $('bulletin-body').innerHTML = `
    <p class="mb-3 text-xs text-on-surface-variant/60">${films.length} film${
      highlighted.length ? ` · ${highlighted.length} tanesi seninle ilgili, önde` : ''
    }</p>
    <div id="bulletin-strip" class="bulletin-strip">
      ${shown.map(bulletinFilmCard).join('')}
      ${hidden ? bulletinMoreCard(hidden) : ''}
    </div>`;
  const nav = $('bulletin-nav');
  nav.classList.toggle('hidden', shown.length <= 2);
  nav.classList.toggle('flex', shown.length > 2);
}

function openBulletinVenues(key) {
  const film = (_bulletinData?.films || []).find(
    item => String(item.tmdb_id || item.slug || item.title) === key);
  if (!film) return;
  $('bulletin-venues-title').textContent = film.title || 'Film';
  $('bulletin-venues-list').innerHTML = (film.venues || []).map(venue => {
    const when = venue.starts_at
      ? ` · ${escapeHTML(new Date(venue.starts_at).toLocaleString(uiLocale(), {
        weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
      }))}`
      : '';
    // Kadıköy gibi bazı sitelerde filme özel adres yok; satır neyi açacağını
    // söylemezse kullanıcı sinemanın programına düşünce şaşırıyor.
    const kind = venue.film_page ? 'Filmin sayfası' : 'Sinema programı';
    const label = `<span class="min-w-0"><strong class="block truncate">${escapeHTML(venue.name)}${when}</strong><small class="text-on-surface-variant/60">${kind}</small></span>`;
    return venue.url
      ? `<a href="${escapeHTML(venue.url)}" target="_blank" rel="noopener" class="flex items-center justify-between gap-3 rounded-xl border border-outline-variant/25 px-4 py-3 text-sm text-on-surface hover:border-tertiary-container/45 hover:text-tertiary-container transition-colors">${label}<span class="material-symbols-outlined text-[18px]">open_in_new</span></a>`
      : `<span class="rounded-xl border border-outline-variant/20 px-4 py-3 text-sm text-on-surface-variant/60">${label}</span>`;
  }).join('');
  $('dialog-bulletin-venues').showModal();
}

async function loadBulletin() {
  if (!_account) return;
  _bulletinLoaded = true;
  try {
    renderBulletin(await apiJSON('/api/bulletin'));
  } catch (_) {
    $('profile-bulletin').classList.add('hidden');
  }
}

// ── Profil carouselleri — görünür ve kullanıcı boşta iken 10 sn'de ilerler ─
const PROFILE_CAROUSEL_MS = 10000;
const _profileCarouselTimers = new Map();
const _profileCarouselVisible = new Map();
const _profileCarouselPaused = new Set();
const _profileCarouselAdvance = new Map();
let _profileCarouselObserver = null;

function _clearProfileCarouselTimer(boxId) {
  const timer = _profileCarouselTimers.get(boxId);
  if (timer) clearTimeout(timer);
  _profileCarouselTimers.delete(boxId);
}

function _scheduleProfileCarousel(boxId) {
  _clearProfileCarouselTimer(boxId);
  const advance = _profileCarouselAdvance.get(boxId);
  if (!advance || document.hidden || _profileCarouselPaused.has(boxId)
      || _profileCarouselVisible.get(boxId) === false) return;
  _profileCarouselTimers.set(boxId, setTimeout(() => {
    _profileCarouselTimers.delete(boxId);
    advance();
  }, PROFILE_CAROUSEL_MS));
}

function registerProfileCarousel(boxId, advance) {
  _profileCarouselAdvance.set(boxId, advance);
  if (!_profileCarouselVisible.has(boxId)) _profileCarouselVisible.set(boxId, true);
  const box = $(boxId);
  if ('IntersectionObserver' in window && box) {
    if (!_profileCarouselObserver) {
      _profileCarouselObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          const id = entry.target.id;
          _profileCarouselVisible.set(id, entry.isIntersecting && entry.intersectionRatio >= 0.3);
          if (_profileCarouselVisible.get(id)) _scheduleProfileCarousel(id);
          else _clearProfileCarouselTimer(id);
        });
      }, { threshold: [0, 0.3] });
    }
    _profileCarouselObserver.observe(box);
  }
  _scheduleProfileCarousel(boxId);
}

function unregisterProfileCarousel(boxId) {
  _clearProfileCarouselTimer(boxId);
  _profileCarouselAdvance.delete(boxId);
  _profileCarouselVisible.delete(boxId);
  _profileCarouselPaused.delete(boxId);
  const box = $(boxId);
  if (_profileCarouselObserver && box) _profileCarouselObserver.unobserve(box);
}

function _pauseProfileCarousel(boxId) {
  _profileCarouselPaused.add(boxId);
  _clearProfileCarouselTimer(boxId);
}

function _resumeProfileCarousel(boxId) {
  _profileCarouselPaused.delete(boxId);
  _scheduleProfileCarousel(boxId);
}

function attachProfileCarousel(box, navigate) {
  let x0 = null;
  let y0 = null;
  let horizontal = false;

  const resetDragFrame = () => {
    const frame = box.querySelector('[data-carousel-frame]');
    if (!frame) return;
    frame.style.transition = 'transform .2s ease, opacity .2s ease';
    frame.style.transform = 'translateX(0)';
    frame.style.opacity = '1';
    setTimeout(() => {
      if (!frame.isConnected) return;
      frame.style.transition = '';
      frame.style.transform = '';
      frame.style.opacity = '';
    }, 220);
  };

  box.addEventListener('mouseenter', () => _pauseProfileCarousel(box.id));
  box.addEventListener('mouseleave', () => _resumeProfileCarousel(box.id));
  box.addEventListener('focusin', () => _pauseProfileCarousel(box.id));
  box.addEventListener('focusout', () => setTimeout(() => {
    if (!box.contains(document.activeElement)) _resumeProfileCarousel(box.id);
  }, 0));
  box.addEventListener('touchstart', event => {
    x0 = event.touches[0].clientX;
    y0 = event.touches[0].clientY;
    horizontal = false;
    _pauseProfileCarousel(box.id);
  }, { passive: true });
  box.addEventListener('touchmove', event => {
    if (x0 == null || y0 == null) return;
    const dx = event.touches[0].clientX - x0;
    const dy = event.touches[0].clientY - y0;
    if (!horizontal && Math.abs(dx) > Math.abs(dy) + 6) horizontal = true;
    if (!horizontal) return;
    const frame = box.querySelector('[data-carousel-frame]');
    if (!frame) return;
    const drag = Math.max(-72, Math.min(72, dx * 0.55));
    frame.style.transition = 'none';
    frame.style.transform = `translateX(${drag}px)`;
    frame.style.opacity = String(Math.max(0.72, 1 - Math.abs(drag) / 260));
  }, { passive: true });
  box.addEventListener('touchend', event => {
    let navigated = false;
    if (x0 != null && horizontal) {
      const dx = event.changedTouches[0].clientX - x0;
      if (Math.abs(dx) > 45) {
        navigate(dx < 0 ? 1 : -1);
        navigated = true;
      }
    }
    if (!navigated) resetDragFrame();
    x0 = null;
    y0 = null;
    horizontal = false;
    _resumeProfileCarousel(box.id);
  }, { passive: true });
  box.addEventListener('touchcancel', () => {
    resetDragFrame();
    x0 = null;
    y0 = null;
    horizontal = false;
    _resumeProfileCarousel(box.id);
  }, { passive: true });
}

document.addEventListener('visibilitychange', () => {
  _profileCarouselAdvance.forEach((_advance, id) => {
    if (document.hidden) _clearProfileCarouselTimer(id);
    else _scheduleProfileCarousel(id);
  });
});

// ── İlk 10 yönetmen ───────────────────────────────────────────────────────
let _directorDeck = null;

function renderDirectorDeck(directors) {
  const rows = Array.isArray(directors) ? directors.slice(0, 10) : [];
  if (!rows.length) {
    _directorDeck = null;
    unregisterProfileCarousel('profile-directors');
    return;
  }
  const currentName = _directorDeck?.directors?.[_directorDeck.index]?.name;
  const preserved = rows.findIndex(d => d.name === currentName);
  _directorDeck = { directors: rows, index: preserved >= 0 ? preserved : 0 };
  _paintDirectorDeck(0);
}

function _paintDirectorDeck(direction = 0) {
  if (!_directorDeck) return;
  const { directors, index } = _directorDeck;
  const director = directors[index];
  const rank = index + 1;
  const meta = director.count
    ? `${director.count} film${director.avg_rating ? ` · senin ortalaman ${Number(director.avg_rating).toFixed(1)}★` : ''}`
    : 'İzleme sıklığı ve puanlarına göre';
  const gridId = `profile-dir-hero-films-${index}`;
  const motion = direction < 0 ? 'carousel-from-left' : direction > 0 ? 'carousel-from-right' : '';
  $('profile-directors').innerHTML = `
    <div data-carousel-frame class="profile-carousel-frame ${motion}">
      <div class="director-card-scroll relative rounded-2xl border border-primary-container/25 bg-surface-container/45 p-4 md:p-5">
        <span class="pointer-events-none absolute -right-3 -bottom-9 font-display-lg text-[120px] leading-none select-none" style="color:rgba(0,224,84,0.09)">${String(rank).padStart(2, '0')}</span>
        <div class="relative flex items-center gap-4">
          ${directorAvatar(director, 'w-14 h-14 text-[18px]')}
          <div class="min-w-0">
            <span class="block font-label-sm text-label-sm uppercase tracking-wide text-primary-container">${rank}. favori yönetmen</span>
            <strong class="block font-headline-md text-[20px] md:text-[22px] text-on-surface truncate">${escapeHTML(director.name)}</strong>
            <span class="font-label-sm text-label-sm text-on-surface-variant/60">${escapeHTML(meta)}</span>
          </div>
        </div>
        ${(director.films || []).length ? directorFilmGrid(director.films, gridId, false, Boolean(director.has_more)) : ''}
        ${director.has_more ? `<button type="button" data-dir-load-rank="${rank}" data-dir-grid="${gridId}" class="mt-3 w-full rounded-xl border border-primary-container/25 py-2.5 font-label-md text-label-md uppercase tracking-wide text-primary-container hover:bg-primary-container/10 transition-colors">Tüm filmlerini göster</button>` : ''}
      </div>
      <div data-deck-controls class="profile-carousel-controls flex items-center justify-between gap-3">
        <button type="button" data-director-nav="-1" class="w-10 h-10 shrink-0 rounded-full border border-outline-variant/30 text-on-surface-variant hover:text-on-surface flex items-center justify-center transition-colors" aria-label="Önceki yönetmen"><span class="material-symbols-outlined text-[20px]">chevron_left</span></button>
        <span class="font-label-sm text-label-sm uppercase tracking-wide text-on-surface-variant/60">${rank} / ${directors.length}</span>
        <button type="button" data-director-nav="1" class="w-10 h-10 shrink-0 rounded-full border border-outline-variant/30 text-on-surface-variant hover:text-on-surface flex items-center justify-center transition-colors" aria-label="Sonraki yönetmen"><span class="material-symbols-outlined text-[20px]">chevron_right</span></button>
      </div>
    </div>`;
  if (directors.length > 1) {
    registerProfileCarousel('profile-directors', () => _directorNav(1));
  } else {
    unregisterProfileCarousel('profile-directors');
  }
}

function _directorNav(delta) {
  if (!_directorDeck || _directorDeck.directors.length < 2) return;
  const length = _directorDeck.directors.length;
  _directorDeck.index = (_directorDeck.index + delta + length) % length;
  _paintDirectorDeck(delta);
}

// ── "Başucu filmleri" & "Son filmler" — tek odak film ──────────────────
let _recentFilms = [];
let _recentLoaded = false;
let _topFilms = [];
let _topFilmsLoaded = false;
let _topFilmsSel = new Set();
let _topFilmsPool = new Map();
let _topFilmsSearchTimer = null;

function _filmHero(f, sideArrows = '') {
  const poster = safeImageURL(f.poster_url);
  const title = escapeHTML(f.title || '');
  const year = f.year ? escapeHTML(String(f.year)) : '';
  const director = escapeHTML(f.director || '');
  const rating = f.user_rating ? Number(f.user_rating).toFixed(1) : '';
  const href = letterboxdFilmURL(f.slug);
  const art = poster
    ? `<img src="${poster}" alt="" onerror="posterErr(this)" class="w-full max-w-[168px] mx-auto aspect-[2/3] rounded-xl object-cover bg-surface-container"/>`
    : `<div class="w-full max-w-[168px] mx-auto aspect-[2/3] rounded-xl bg-surface-container flex items-center justify-center"><span class="material-symbols-outlined text-on-surface-variant/25 text-[40px]">movie</span></div>`;
  const cover = href
    ? `<a href="${href}" target="_blank" rel="noopener" class="block min-w-0 flex-1" title="${title} — Letterboxd">${art}</a>`
    : `<div class="min-w-0 flex-1">${art}</div>`;
  return `
    <div class="flex h-full min-h-0 flex-col overflow-hidden">
      <div class="deck-poster-row flex shrink-0 items-center gap-1">${sideArrows ? sideArrows.replace('%SLOT%', cover) : cover}</div>
      <div class="mt-4 shrink-0 text-center">
        <h3 class="font-headline-md text-[18px] md:text-[20px] text-on-surface leading-tight">${title}${year ? ` <span class="font-body-md text-body-md text-on-surface-variant/50">${year}</span>` : ''}</h3>
        ${director ? `<p class="mt-1 font-label-sm text-label-sm text-tertiary-container">${director}</p>` : ''}
        ${rating ? `<p class="mt-2 inline-flex items-center gap-1 font-label-md text-label-md text-primary-container"><span class="material-symbols-outlined text-[15px]" style="font-variation-settings:'FILL' 1">star</span>${rating}</p>` : ''}
      </div>
      <div class="film-overview-scroll mt-3 pr-2 pb-1" tabindex="0" aria-label="${title} film konusu">
        ${f.overview
          ? `<p class="font-body-md text-body-md text-on-surface-variant leading-relaxed">${escapeHTML(f.overview)}</p>`
          : '<p class="font-label-sm text-label-sm text-on-surface-variant/40">Konu bilgisi hazırlanıyor…</p>'}
      </div>
    </div>
  `;
}

// Deste: kartta tek film, ‹ › / kaydırma ile 10 film arası gezinilir.
const _filmDecks = {};

function renderFilmDeck(boxId, list, emptyText) {
  const films = Array.isArray(list) ? list.slice(0, 10) : [];
  if (!films.length) {
    $(boxId).innerHTML = `<p class="py-8 text-center font-body-md text-body-md text-on-surface-variant/60">${emptyText}</p>`;
    _filmDecks[boxId] = null;
    unregisterProfileCarousel(boxId);
    return;
  }
  const previous = _filmDecks[boxId];
  const currentSlug = previous?.films?.[previous.index]?.slug;
  const preserved = films.findIndex(film => film.slug === currentSlug);
  _filmDecks[boxId] = { films, index: preserved >= 0 ? preserved : 0 };
  _paintFilmDeck(boxId, 0);
}

function _paintFilmDeck(boxId, direction = 0) {
  const deck = _filmDecks[boxId];
  if (!deck) return;
  const { films, index } = deck;
  const motion = direction < 0 ? 'carousel-from-left' : direction > 0 ? 'carousel-from-right' : '';
  // Telefonda oklar posterin iki yanında durur; geniş ekranda alttaki satırda
  // kalır. İki takım da aynı `data-deck-nav` düğmesi, biri CSS ile gizli.
  const sideArrows = films.length > 1
    ? `<button type="button" data-deck-nav="-1" class="deck-arrow" aria-label="Önceki film"><span class="material-symbols-outlined text-[22px]">chevron_left</span></button>
       %SLOT%
       <button type="button" data-deck-nav="1" class="deck-arrow" aria-label="Sonraki film"><span class="material-symbols-outlined text-[22px]">chevron_right</span></button>`
    : '';
  $(boxId).innerHTML = `
    <div data-carousel-frame class="profile-carousel-frame ${motion}">
      <div class="profile-carousel-body" data-deck-body>${_filmHero(films[index], sideArrows)}</div>
      <div data-deck-controls class="profile-carousel-controls flex items-center justify-between gap-3">
        <button type="button" data-deck-nav="-1" class="deck-row-arrow w-10 h-10 shrink-0 rounded-full border border-outline-variant/30 text-on-surface-variant hover:text-on-surface flex items-center justify-center transition-colors" aria-label="Önceki film"><span class="material-symbols-outlined text-[20px]">chevron_left</span></button>
        <span class="font-label-sm text-label-sm uppercase tracking-wide text-on-surface-variant/60">${index + 1} / ${films.length}</span>
        <button type="button" data-deck-nav="1" class="deck-row-arrow w-10 h-10 shrink-0 rounded-full border border-outline-variant/30 text-on-surface-variant hover:text-on-surface flex items-center justify-center transition-colors" aria-label="Sonraki film"><span class="material-symbols-outlined text-[20px]">chevron_right</span></button>
      </div>
    </div>`;
  const f = films[index];
  if (!f.overview && !f._noOverview) _loadDeckOverview(boxId, index);
  if (films.length > 1) {
    registerProfileCarousel(boxId, () => _deckNav(boxId, 1));
  } else {
    unregisterProfileCarousel(boxId);
  }
}

function _deckNav(boxId, delta) {
  const deck = _filmDecks[boxId];
  if (!deck || deck.films.length < 2) return;
  const length = deck.films.length;
  deck.index = (deck.index + delta + length) % length;
  _paintFilmDeck(boxId, delta);
}

async function _loadDeckOverview(boxId, index) {
  const deck = _filmDecks[boxId];
  if (!deck) return;
  const f = deck.films[index];
  try {
    const qs = `slug=${encodeURIComponent(f.slug || '')}`
      + `&title=${encodeURIComponent(f.title || '')}`
      + (f.year ? `&year=${encodeURIComponent(f.year)}` : '');
    const data = await apiJSON(`/api/profile/film-overview?${qs}`);
    f.overview = data.overview || '';
    f._noOverview = !data.overview;
  } catch (_) { f._noOverview = true; }
  if (_filmDecks[boxId] && _filmDecks[boxId].index === index) _paintFilmDeck(boxId);
}

function handleFilmDeck(event) {
  const nav = event.target.closest('[data-deck-nav]');
  if (nav) _deckNav(event.currentTarget.id, Number(nav.dataset.deckNav));
}

function renderTopFilms(list) {
  _topFilms = Array.isArray(list) ? list : [];
  renderFilmDeck('profile-top-films', _topFilms,
    'Puanladığın filmler tarandıkça en sevdiğin 10 film burada. Kalemle kendin de seçebilirsin.');
}

function renderRecentFilms(list) {
  _recentFilms = Array.isArray(list) ? list : [];
  renderFilmDeck('profile-recent-films', _recentFilms,
    'İzleme geçmişin tarandıkça son izlediğin filmler burada görünür.');
}

async function loadTopFilms() {
  try {
    const data = await apiJSON('/api/profile/top-films');
    const films = data.films || [];
    _topFilmsLoaded = true;
    renderTopFilms(films);
  } catch (_) { /* sonraki render tekrar dener */ }
}

async function loadRecentFilms(fresh) {
  try {
    const data = await apiJSON(`/api/profile/recent${fresh ? '?fresh=1' : ''}`);
    const films = data.films || [];
    _recentLoaded = true;
    renderRecentFilms(films);
  } catch (_) { /* sonraki render tekrar dener */ }
}

let _statsLoaded = false;
async function loadProfileStats() {
  try {
    const data = await apiJSON('/api/profile/stats');
    _statsLoaded = true;
    if (typeof data.this_year === 'number') {
      $('profile-year-count').textContent = data.this_year.toLocaleString(uiLocale());
      if (_persistedProfile) {
        _persistedProfile.stats = { ...(_persistedProfile.stats || {}), this_year: data.this_year };
      }
    }
  } catch (_) { /* sonraki render tekrar dener */ }
}

function _topFilmsPickRow(film) {
  const on = _topFilmsSel.has(film.slug);
  const poster = safeImageURL(film.poster_url);
  const title = escapeHTML(film.title || '');
  const meta = [escapeHTML(film.director || ''), film.year ? String(film.year) : '']
    .filter(Boolean).join(' · ');
  const rating = film.user_rating ? `★ ${Number(film.user_rating).toFixed(1)}` : '';
  return `<button type="button" data-top-slug="${escapeHTML(film.slug)}" class="w-full flex items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors ${on ? 'bg-primary-container/15' : 'hover:bg-surface-variant/60'}">
    <span class="w-5 h-5 shrink-0 rounded border flex items-center justify-center ${on ? 'bg-primary-container border-primary-container text-black' : 'border-outline-variant/50 text-transparent'}"><span class="material-symbols-outlined text-[14px]">check</span></span>
    ${poster
      ? `<img src="${poster}" alt="" onerror="posterErr(this)" class="w-8 h-12 rounded object-cover bg-surface-container shrink-0"/>`
      : '<div class="w-8 h-12 rounded bg-surface-container shrink-0"></div>'}
    <span class="min-w-0 flex-grow">
      <span class="block font-label-md text-label-md text-on-surface truncate">${title}</span>
      ${meta ? `<span class="block font-label-sm text-label-sm text-on-surface-variant/60 truncate">${escapeHTML(meta)}</span>` : ''}
    </span>
    ${rating ? `<span class="shrink-0 font-label-sm text-label-sm text-on-surface-variant/60">${rating}</span>` : ''}
  </button>`;
}

function _renderTopFilmsPicker(films) {
  films.forEach(f => { if (f.slug) _topFilmsPool.set(f.slug, f); });
  // Selected films first (from the running pool, so they stay visible while
  // searching), then the rest of the current result set.
  const ordered = [
    ...[..._topFilmsSel].map(s => _topFilmsPool.get(s)).filter(Boolean),
    ...films.filter(f => !_topFilmsSel.has(f.slug)),
  ];
  $('top-films-list').innerHTML = ordered.length
    ? ordered.map(_topFilmsPickRow).join('')
    : '<p class="py-8 text-center font-body-md text-body-md text-on-surface-variant/60">Eşleşen film yok.</p>';
  $('top-films-count').textContent = `${_topFilmsSel.size} / 10 seçili`;
}

async function openTopFilmsEditor() {
  _topFilmsSel = new Set(_topFilms.map(f => f.slug).filter(Boolean));
  _topFilmsPool = new Map(_topFilms.filter(f => f.slug).map(f => [f.slug, f]));
  $('top-films-search').value = '';
  $('top-films-list').innerHTML = '<p class="py-8 text-center font-body-md text-body-md text-on-surface-variant/50">Yükleniyor…</p>';
  $('dialog-top-films').showModal();
  try {
    const data = await apiJSON('/api/profile/watched?limit=120');
    _renderTopFilmsPicker(data.films || []);
  } catch (error) {
    $('top-films-list').innerHTML = `<p class="py-8 text-center font-body-md text-body-md text-error">${escapeHTML(error.message || 'Filmler alınamadı.')}</p>`;
  }
}

async function _searchTopFilms() {
  const q = $('top-films-search').value.trim();
  try {
    const data = await apiJSON(`/api/profile/watched?limit=80&q=${encodeURIComponent(q)}`);
    _renderTopFilmsPicker(data.films || []);
  } catch (_) { /* keep current list */ }
}

function handleTopFilmsPick(event) {
  const button = event.target.closest('[data-top-slug]');
  if (!button) return;
  const slug = button.dataset.topSlug;
  if (_topFilmsSel.has(slug)) _topFilmsSel.delete(slug);
  else if (_topFilmsSel.size < 10) _topFilmsSel.add(slug);
  else { _topFilmsNote('En fazla 10 film seçebilirsin.'); return; }
  _searchTopFilms();
}

async function saveTopFilms() {
  const button = $('top-films-save');
  button.disabled = true;
  try {
    const data = await apiJSON('/api/profile/top-films', {
      method: 'PUT',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ slugs: [..._topFilmsSel] }),
    });
    renderTopFilms(data.top_films);
    _topFilmsLoaded = false;
    loadTopFilms();                       // re-pull so the new focus film gets its plot
    $('dialog-top-films').close();
  } catch (error) {
    _topFilmsNote(error.message || 'Liste kaydedilemedi.');
  } finally { button.disabled = false; }
}

function _topFilmsNote(msg) {
  const el = $('top-films-count');
  if (el) el.textContent = msg;
}

// ── Full watched-history sweep progress ──────────────────────────────────
let _sweepPollTimer = null;
const _SWEEP_PHASE_LABEL = {
  diary: 'İzleme geçmişin taranıyor',
  enrich: 'Film verileri zenginleştiriliyor',
  aggregate: 'Zevk analizi hesaplanıyor',
};

function applySyncJob(job) {
  const badge = $('profile-scope-badge');
  const strip = $('profile-sweep');
  const active = job && (job.state === 'queued' || job.state === 'running');

  if (job && job.state === 'done' && job.scope === 'full') {
    $('profile-scope-badge-text').textContent = job.total
      ? `${t('Tüm geçmiş')} · ${job.total.toLocaleString(uiLocale())} ${t('film')}`
      : 'Tüm geçmiş analiz edildi';
    badge.classList.remove('hidden');
    badge.classList.add('inline-flex');
  } else {
    badge.classList.add('hidden');
    badge.classList.remove('inline-flex');
  }

  if (active) {
    strip.classList.remove('hidden');
    $('profile-sweep-label').textContent = _SWEEP_PHASE_LABEL[job.phase] || 'Tüm izleme geçmişin analiz ediliyor';
    $('profile-sweep-count').textContent = job.total
      ? `${job.processed.toLocaleString(uiLocale())} / ${job.total.toLocaleString(uiLocale())} ${t('film')}`
      : `${(job.processed || 0).toLocaleString(uiLocale())} ${t('film')}`;
    const progressBar = $('profile-sweep-bar');
    progressBar.classList.toggle('is-indeterminate', !job.total);
    progressBar.style.width = job.total ? `${Math.max(4, job.percent || 0)}%` : '38%';
    startSweepPoll();
  } else {
    strip.classList.add('hidden');
    stopSweepPoll();
  }
}

function startSweepPoll() {
  if (_sweepPollTimer) return;
  _sweepPollTimer = setInterval(async () => {
    if ($('view-profile').classList.contains('hidden')) { stopSweepPoll(); return; }
    try {
      const data = await apiJSON('/api/profile/sync-status');
      const job = data.sync_job;
      const active = job && (job.state === 'queued' || job.state === 'running');
      applySyncJob(job);
      if (!active) await loadProfile();
    } catch (_) { /* transient; keep polling */ }
  }, 7000);
}

function stopSweepPoll() {
  if (_sweepPollTimer) { clearInterval(_sweepPollTimer); _sweepPollTimer = null; }
}

let _blendInbox = { incoming: [], outgoing: [], history: [], blocked: [] };
let _currentBlendResult = null;
const BLEND_BADGE_POLL_MS = 60000;
let _blendBadgePollTimer = null;

function renderBlendBadge(rawCount) {
  const count = Math.max(0, Number(rawCount) || 0);
  const badge = $('profile-inbox-badge');
  const button = $('profile-inbox');
  badge.textContent = count > 9 ? '9+' : String(count);
  badge.classList.toggle('hidden', count === 0);
  badge.classList.toggle('flex', count > 0);
  badge.classList.toggle('inbox-badge-pulse', count > 0);
  button.classList.toggle('border-secondary-container/60', count > 0);
  button.classList.toggle('text-secondary-container', count > 0);
  button.setAttribute(
    'aria-label',
    count ? `Gelen kutusu, ${count} yeni öğe` : 'Gelen kutusu',
  );
  paintNavBadge('inbox', count);
}

// The same tally in three places: sidebar pill, tab-bar dot, old header badge.
function paintNavBadge(kind, count) {
  const pill = $(`nav-badge-${kind}`);
  const dot = $(`tab-badge-${kind}`);
  if (pill) {
    pill.textContent = count > 9 ? '9+' : String(count);
    pill.classList.toggle('hidden', count === 0);
  }
  if (dot) dot.classList.toggle('hidden', count === 0);
}

async function refreshBlendBadge() {
  if (!_account || document.hidden) return;
  try {
    const data = await apiJSON('/api/blends/pending-count');
    renderBlendBadge(data.count);
  } catch (_) { /* mevcut sayıyı koru; sonraki poll tekrar dener */ }
}

function startBlendBadgePolling() {
  if (_blendBadgePollTimer) return;
  _blendBadgePollTimer = setInterval(refreshBlendBadge, BLEND_BADGE_POLL_MS);
}

function stopBlendBadgePolling() {
  if (_blendBadgePollTimer) clearInterval(_blendBadgePollTimer);
  _blendBadgePollTimer = null;
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && _account) refreshBlendBadge();
});

let _letterEnablePendingRecipient = null;
let _letterRecipient = null;
let _letterPickedFilm = null;
let _letterSearchTimer = null;
let _letterSendStatus = { can_send: true, seconds_remaining: 0 };
let _letterCooldownTimer = null;

async function refreshLiveLetterSettings() {
  if (!_account) return false;
  const data = await apiJSON('/api/letters/settings');
  const enabled = Boolean(data.letter_receiving_enabled);
  _account.letter_receiving_enabled = enabled;
  renderLetterSettings();
  renderProfileLetterSettings(enabled);
  return enabled;
}

function letterMessage(kind, message = '') {
  const node = $(`letters-${kind}`);
  if (!node) return;
  node.textContent = message;
  node.classList.toggle('hidden', !message);
}

function formatLetterCooldown(seconds) {
  const total = Math.max(0, Math.ceil(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours ? `${hours} sa ${minutes} dk` : `${Math.max(1, minutes)} dk`;
}

function renderLetterCooldown() {
  const remaining = Math.max(0, Number(_letterSendStatus.seconds_remaining) || 0);
  const blocked = !_letterSendStatus.can_send && remaining > 0;
  const notice = $('letter-compose-cooldown');
  const send = $('btn-letter-send');
  const recipient = _letterSendStatus.recipient_username ? `@${_letterSendStatus.recipient_username} için ` : '';
  notice.textContent = blocked ? `${recipient}yeni mektup hakkın ${formatLetterCooldown(remaining)} sonra açılacak. Bu arada başka bir sinefile yazabilirsin.` : '';
  notice.classList.toggle('hidden', !blocked);
  send.disabled = blocked;
  if (_letterCooldownTimer) clearInterval(_letterCooldownTimer);
  if (blocked) {
    _letterCooldownTimer = setInterval(() => {
      _letterSendStatus.seconds_remaining = Math.max(0, _letterSendStatus.seconds_remaining - 60);
      _letterSendStatus.can_send = _letterSendStatus.seconds_remaining === 0;
      renderLetterCooldown();
    }, 60000);
  }
}

async function loadLetterSendStatus(username = _letterRecipient?.username || '') {
  const query = username ? `?recipient_username=${encodeURIComponent(username)}` : '';
  _letterSendStatus = await apiJSON(`/api/letters/send-status${query}`);
  renderLetterCooldown();
  return _letterSendStatus;
}

function renderLetterSettings() {
  const open = Boolean(_account?.letter_receiving_enabled);
  renderProfileLetterSettings(open);
  const receiving = $('btn-letter-receiving');
  if (receiving) {
    receiving.setAttribute('aria-pressed', String(open));
    receiving.textContent = open ? 'Mektuplara açığım' : 'Mektuplara kapalı';
  }
}

function letterFilmMarkup(film, { link = true } = {}) {
  if (!film) return '';
  const title = escapeHTML(film.title || 'Film');
  const year = film.release_year || film.year || '';
  const director = escapeHTML(film.director || '');
  const poster = safeImageURL(film.poster_url);
  const href = letterboxdFilmURL(film.slug || film.film_slug);
  const text = `<span class="min-w-0"><strong class="block truncate">${title}${year ? ` <span class="text-on-surface-variant">(${escapeHTML(year)})</span>` : ''}</strong>${director ? `<span class="mt-0.5 block truncate text-xs text-on-surface-variant">${director}</span>` : ''}</span>`;
  return `<div class="flex min-w-0 items-center gap-3">${poster ? `<img src="${poster}" alt="" class="h-12 w-9 rounded object-cover"/>` : ''}${link && href ? `<a href="${href}" target="_blank" rel="noopener" class="min-w-0 hover:underline">${text}</a>` : text}</div>`;
}

function letterCard(item, payload) {
  const peer = item.peer || {};
  const incoming = item.direction === 'received';
  const author = incoming ? peer : (_account || {});
  const title = escapeHTML(author.display_name || author.username || 'Bilinmeyen sinefil');
  const username = escapeHTML(author.username || '');
  const peerUsername = escapeHTML(peer.username || '');
  const body = escapeHTML(
    payload?.body
      || (item.legacy_encrypted
        ? 'Bu mektup eski cihaz-anahtarlı biçimde yazılmıştı ve artık açılamıyor.'
        : 'Mektup içeriği okunamadı.'),
  );
  const date = new Date(item.created_at).toLocaleDateString(uiLocale(), { day: '2-digit', month: 'short', year: 'numeric' });
  const seen = item.read_at ? new Date(item.read_at).toLocaleString(uiLocale(), { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  const attachment = payload?.film ? `<div class="mt-3 rounded-xl border border-tertiary-container/25 bg-tertiary-container/10 p-3 text-sm text-tertiary-container"><span class="mb-2 block text-[10px] font-bold uppercase tracking-wide">Film hediyesi</span>${letterFilmMarkup(payload.film)}</div>` : '';
  return `<article class="rounded-2xl border border-outline-variant/25 bg-surface-container p-4 shadow-lg"><div class="flex items-center gap-3">${peerAvatar(author)}<div class="min-w-0 flex-1"><strong class="block truncate text-on-surface">${title}</strong><span class="text-xs text-on-surface-variant">@${username} · ${date}</span></div><span class="rounded-full border border-tertiary-container/25 px-2 py-1 text-[10px] uppercase tracking-wide text-tertiary-container">${incoming ? 'Gelen' : 'Gönderilen'}</span></div><p class="mt-4 whitespace-pre-wrap break-words text-sm leading-relaxed text-on-surface-variant">${body}</p>${attachment}<div class="mt-4 flex flex-wrap gap-2"><button data-letter-action="report" data-peer-username="${peerUsername}" class="rounded-lg border border-outline-variant/25 px-3 py-2 text-xs text-on-surface-variant">Bildir</button><button data-letter-action="block" data-peer-username="${peerUsername}" class="rounded-lg border border-outline-variant/25 px-3 py-2 text-xs text-on-surface-variant hover:text-error">Engelle</button>${!incoming ? `<button data-letter-action="delete" data-letter-id="${escapeHTML(item.id)}" class="rounded-lg border border-outline-variant/25 px-3 py-2 text-xs text-on-surface-variant hover:text-error">İki taraftan sil</button>` : ''}${!incoming && seen ? `<span class="self-center text-xs text-primary-container">Görüldü · ${seen}</span>` : ''}</div></article>`;
}

function letterReplyBar(peer) {
  const username = escapeHTML(peer?.username || '');
  const name = escapeHTML(peer?.display_name || peer?.username || 'bu sinefile');
  if (!username) return '';
  // A correspondence continues where it lives. Before this, replying meant
  // finding the person again in Sinefil Sineması and starting over.
  return `<button type="button" data-letter-reply="${username}" class="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-tertiary-container/30 bg-tertiary-container/10 py-3 font-label-md text-label-md uppercase tracking-wide text-tertiary-container hover:bg-tertiary-container/20 transition-colors">
    <span class="material-symbols-outlined text-[18px]">mail</span>${name} kişisine yaz
  </button>`;
}

let _openLetterThread = '';
let _letterThreads = [];

function letterThreadCard(group) {
  const peer = group.peer || {};
  const username = escapeHTML(peer.username || '');
  const name = escapeHTML(peer.display_name || peer.username || 'Sinefil');
  const unread = group.items.filter(({ item }) => item.direction === 'received' && !item.read_at).length;
  const latest = group.items[0]?.payload?.body || 'Filmli bir mektup';
  const selected = _openLetterThread === peer.username;
  return `<button type="button" data-letter-thread="${username}" class="flex w-full items-center gap-3 rounded-xl p-3 text-left transition-colors ${selected ? 'bg-tertiary-container/15 text-on-surface ring-1 ring-tertiary-container/25' : 'text-on-surface hover:bg-surface-variant/45'}"><span class="shrink-0">${peerAvatar(peer)}</span><span class="min-w-0 flex-1"><strong class="block truncate text-sm">${name}</strong><span class="mt-0.5 block truncate text-xs text-on-surface-variant">${escapeHTML(latest)}</span></span>${unread ? `<span class="rounded-full bg-secondary-container px-2 py-1 text-[10px] font-bold text-on-secondary-container">${unread}</span>` : ''}</button>`;
}

function renderLetterConversation(username = _openLetterThread) {
  const panel = $('letters-conversation');
  const group = _letterThreads.find(thread => thread.peer?.username === username);
  if (!group) {
    panel.innerHTML = '<div class="flex flex-col items-center justify-center px-6 py-10 text-center text-on-surface-variant"><span class="material-symbols-outlined text-[36px] text-tertiary-container/45">mail</span><strong class="mt-4 text-on-surface">Bir mektup seç</strong><p class="mt-2 max-w-xs text-sm leading-relaxed">Konuştuğun sinefiller solda. Birini seçtiğinde tüm mektuplarınız burada açılır.</p></div>';
    return;
  }
  _openLetterThread = group.peer.username;
  const peer = group.peer || {};
  const name = escapeHTML(peer.display_name || peer.username || 'Sinefil');
  const usernameLabel = escapeHTML(peer.username || '');
  const details = group.items.map(({ item, payload }) => letterCard(item, payload)).join('');
  panel.innerHTML = `<div class="flex flex-col"><header class="flex items-center gap-3 border-b border-outline-variant/20 px-5 py-4"><button type="button" data-letter-mobile-back class="-ml-2 rounded-full p-2 text-on-surface-variant hover:text-on-surface" aria-label="Mektuplara dön"><span class="material-symbols-outlined text-[20px]">arrow_back</span></button><span class="shrink-0">${peerAvatar(peer)}</span><span class="min-w-0 flex-1"><strong class="block truncate text-on-surface">${name}</strong><span class="block truncate text-xs text-on-surface-variant">@${usernameLabel} · ${group.items.length} mektup</span></span></header><div class="flex-1 space-y-3 p-4">${details}</div><div class="border-t border-outline-variant/20 p-4">${letterReplyBar(peer)}</div></div>`;
  $('letters-list').innerHTML = _letterThreads.map(letterThreadCard).join('');
  renderLetterWorkspace();
}

function renderLetterWorkspace() {
  const fab = $('btn-letter-compose-fab');
  if (fab) {
    const show = !_openLetterThread && !$('view-inbox').classList.contains('hidden');
    fab.classList.toggle('hidden', !show);
    fab.classList.toggle('flex', show);
  }
  // Bir kişiye dokunulduğunda ekranın tamamı o mektuplaşmaya ayrılır —
  // masaüstünde de. Yan yana iki sütun, dar ekranda da geniş ekranda da
  // mektubun kendisine kalan yeri daraltıyordu.
  const open = Boolean(_openLetterThread);
  $('letters-sidebar').classList.toggle('hidden', open);
  $('letters-conversation').classList.toggle('hidden', !open);
}

async function loadLetters() {
  if (!_account) return;
  letterMessage('error');
  try {
    // The reply bar states the daily limit, so the inbox needs the allowance
    // before it renders rather than only when the composer opens. Not knowing
    // it is survivable: the composer says so on open.
    const unreadData = await apiJSON('/api/letters/unread-count');
    let unreadCount = Math.max(0, Number(unreadData.count) || 0);
    const data = await apiJSON('/api/letters');
    const legacyLetters = (data.letters || []).filter(item => item.legacy_encrypted);
    if (legacyLetters.length) {
      try {
        const purge = await apiJSON('/api/letters/legacy', {
          method: 'DELETE', headers: csrfHeaders(),
        });
        if (purge.deleted) {
          unreadCount = Math.max(0, unreadCount - legacyLetters.filter(
            item => item.direction === 'received' && !item.read_at,
          ).length);
          letterMessage('notice', `${purge.deleted} eski, cihaz-anahtarlı mektup kaldırıldı. Yeni mektupların mobilde ve webde açılır.`);
        }
      } catch (_) {
        // A retry on the next inbox visit is safer than letting an old broken
        // row hide every current conversation.
      }
    }
    $('inbox-letters-badge').textContent = unreadCount > 9 ? '9+' : String(unreadCount);
    $('inbox-letters-badge').classList.toggle('hidden', unreadCount === 0);
    const decoded = (data.letters || []).filter(item => !item.legacy_encrypted).map(item => ({
      item,
      // New rows are account-bound, so a user can continue the same letter
      // thread on mobile and web without carrying a device-specific key.
      payload: { body: item.body || '', film: item.film || null },
    }));
    const unread = decoded.filter(({ item, payload }) => item.direction === 'received' && !item.read_at && payload).map(({ item }) => item.id);
    await Promise.all(unread.map(async id => {
      const response = await apiJSON(`/api/letters/${encodeURIComponent(id)}/read`, { method: 'POST', headers: csrfHeaders() });
      if (response.read) {
        const target = decoded.find(({ item }) => item.id === id);
        if (target) target.item.read_at = new Date().toISOString();
      }
    }));
    const threads = new Map();
    decoded.forEach(entry => {
      const key = entry.item.peer?.username || 'unknown';
      if (!threads.has(key)) threads.set(key, { peer: entry.item.peer, items: [] });
      threads.get(key).items.push(entry);
    });
    threads.forEach(thread => thread.items.sort((a, b) => new Date(b.item.created_at) - new Date(a.item.created_at)));
    const sortedThreads = [...threads.values()].sort((a, b) => {
      const latestA = a.items[0]?.item?.created_at || '';
      const latestB = b.items[0]?.item?.created_at || '';
      return new Date(latestB) - new Date(latestA);
    });
    _letterThreads = sortedThreads;
    if (!_openLetterThread || !sortedThreads.some(thread => thread.peer?.username === _openLetterThread)) {
      _openLetterThread = '';
    }
    $('letters-list').innerHTML = sortedThreads.length
      ? sortedThreads.map(letterThreadCard).join('')
      : '<div class="p-5 text-center text-sm text-on-surface-variant">Henüz mektubun yok.</div>';
    renderLetterConversation();
    renderLetterWorkspace();
    if (unread.length) refreshBlendBadge();
  } catch (error) {
    $('letters-list').innerHTML = '';
    _letterThreads = [];
    renderLetterConversation();
    letterMessage('error', error.message || 'Mektuplar yüklenemedi.');
  }
}

async function toggleLetterReceiving(trigger = null) {
  const next = !_account?.letter_receiving_enabled;
  const prompt = next
    ? 'Mektupları açarsan Sinefil Sineması’ndaki kullanıcılar sana 24 saatte bir mektup gönderebilir. Açmak istiyor musun?'
    : 'Mektupları kapatırsan yeni mektup alamazsın. Mevcut mektupların korunur. Kapatmak istiyor musun?';
  if (!window.confirm(prompt)) return;
  const button = trigger || $('btn-letter-receiving');
  if (button) button.disabled = true;
  try {
    const data = await apiJSON('/api/letters/receiving', { method: 'POST', headers: csrfHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ enabled: next }) });
    _account.letter_receiving_enabled = data.letter_receiving_enabled;
    renderLetterSettings();
    letterMessage('notice', next ? 'Mektuplara açıksın. İstediğin an buradan kapatabilirsin.' : 'Mektuplar kapatıldı.');
  } catch (error) { letterMessage('error', error.message || 'Mektup ayarı güncellenemedi.'); }
  finally { if (button) button.disabled = false; }
}

async function openLetterCompose(username) {
  // Writing requires an open letterbox of your own, so the recipient can answer.
  let receivingEnabled = Boolean(_account?.letter_receiving_enabled);
  if (!receivingEnabled) {
    try { receivingEnabled = await refreshLiveLetterSettings(); } catch (_) {}
  }
  if (!receivingEnabled) {
    _letterEnablePendingRecipient = username;
    $('letter-enable-username').textContent = `@${username}`;
    $('letter-enable-error').classList.add('hidden');
    $('dialog-letter-enable').showModal();
    return;
  }
  _letterRecipient = { username };
  _letterPickedFilm = null;
  $('letter-compose-title').textContent = `@${username} için mektup`;
  $('letter-compose-body').value = '';
  $('letter-compose-count').textContent = '600 karakter kaldı';
  $('letter-film-search').value = '';
  $('letter-film-results').innerHTML = '';
  $('letter-film-picked').classList.add('hidden');
  $('letter-compose-error').classList.add('hidden');
  $('dialog-letter-compose').showModal();
  loadLetterSendStatus(username).catch(error => {
    $('letter-compose-error').textContent = error.message || 'Gönderim hakkı kontrol edilemedi.';
    $('letter-compose-error').classList.remove('hidden');
  });
}

function renderPickedLetterFilm() {
  const target = $('letter-film-picked');
  if (!_letterPickedFilm) { target.classList.add('hidden'); target.innerHTML = ''; return; }
  target.innerHTML = `<div class="flex items-center justify-between gap-3"><div>${letterFilmMarkup(_letterPickedFilm, { link: false })}</div><button type="button" data-letter-film-clear class="rounded-md px-2 py-1 text-xs text-tertiary-container">Kaldır</button></div>`;
  target.classList.remove('hidden');
}

async function searchLetterFilms() {
  const query = $('letter-film-search').value.trim();
  if (query.length < 2) { $('letter-film-results').innerHTML = ''; return; }
  try {
    const data = await apiJSON(`/api/profile/watched?q=${encodeURIComponent(query)}`);
    const films = (data.films || []).slice(0, 6).map(film => ({ ...film, slug: film.slug || film.film_slug || '' }));
    $('letter-film-results').innerHTML = films.map((film, index) => `<button type="button" data-letter-film-index="${index}" class="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm hover:bg-surface-variant">${letterFilmMarkup(film, { link: false })}</button>`).join('');
    $('letter-film-results')._letterFilms = films;
  } catch (_) { $('letter-film-results').innerHTML = ''; }
}

async function sendLetter(event) {
  event.preventDefault();
  if (!_letterRecipient) return;
  if (!_letterSendStatus.can_send) return;
  if (!window.confirm('Bu mektup gönderildikten sonra geri alınamaz. Göndermek istiyor musun?')) return;
  const button = $('btn-letter-send'); button.disabled = true;
  try {
    const recipientData = await apiJSON(`/api/letters/recipients/${encodeURIComponent(_letterRecipient.username)}`);
    const recipient = recipientData.recipient;
    await apiJSON('/api/letters', {
      method: 'POST', headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        recipient_username: recipient.username,
        body: $('letter-compose-body').value,
        film: _letterPickedFilm,
      }),
    });
    _letterSendStatus = { can_send: false, seconds_remaining: 24 * 60 * 60, recipient_username: recipient.username };
    $('dialog-letter-compose').close();
    letterMessage('notice', `Mektubun @${recipient.username} adresine gönderildi.`);
    await loadLetters();
  } catch (error) {
    $('letter-compose-error').textContent = error.message || 'Mektup gönderilemedi.';
    $('letter-compose-error').classList.remove('hidden');
  } finally { button.disabled = false; }
}

function peerAvatar(peer) {
  const poster = safeImageURL(peer?.avatar_url);
  const name = escapeHTML(peer?.display_name || peer?.username || '?');
  return poster
    ? `<img src="${poster}" alt="${name}" class="w-11 h-11 shrink-0 rounded-full object-cover border border-outline-variant/30"/>`
    : `<div class="w-11 h-11 shrink-0 rounded-full bg-surface-container flex items-center justify-center text-primary-container font-bold">${name[0] || '?'}</div>`;
}

function blendRequestCard(item, kind) {
  const peer = item.peer || {};
  const username = escapeHTML(peer.username || 'bilinmeyen');
  const displayName = escapeHTML(peer.display_name || peer.username || 'Bilinmeyen kullanıcı');
  const id = escapeHTML(item.id);
  let actions = '';
  const safety = `<button data-blend-action="report" data-peer-username="${username}" class="min-h-[42px] px-3 py-2 rounded-lg border border-outline-variant/20 text-on-surface-variant hover:text-secondary-container text-xs" title="Kullanıcıyı bildir">Bildir</button>
    <button data-blend-action="block" data-peer-username="${username}" class="min-h-[42px] px-3 py-2 rounded-lg border border-outline-variant/20 text-on-surface-variant hover:text-error text-xs" title="Kullanıcıyı engelle">Engelle</button>`;
  if (kind === 'incoming') {
    actions = `<div class="inbox-card-actions">
      ${safety}
      <button data-blend-action="rejected" data-request-id="${id}" class="min-h-[42px] px-3 py-2 rounded-lg border border-outline-variant/30 text-on-surface-variant hover:text-error text-xs uppercase">Reddet</button>
      <button data-blend-action="accepted" data-request-id="${id}" class="min-h-[42px] px-3 py-2 rounded-lg bg-primary-container text-black text-xs uppercase font-bold">Kabul et</button>
    </div>`;
  } else if (kind === 'outgoing') {
    actions = `<div class="inbox-card-actions">${safety}<button data-blend-action="cancel" data-request-id="${id}" class="min-h-[42px] px-3 py-2 rounded-lg border border-outline-variant/30 text-on-surface-variant hover:text-error text-xs uppercase">İptal et</button></div>`;
  }
  return `<article class="glass-panel rounded-xl p-4 flex flex-col sm:flex-row sm:items-center gap-4 overflow-safe">
    <div class="flex items-center gap-3 min-w-0 flex-1">
      ${peerAvatar(peer)}
      <div class="min-w-0"><strong class="text-on-surface block truncate">${displayName}</strong><span class="text-on-surface-variant text-sm block truncate">@${username}</span></div>
    </div>
    ${actions}
  </article>`;
}

function blendResultWithPeerAvatars(result, peer = {}) {
  if (!result) return result;
  const ownUsername = _account?.username || '';
  const ownAvatar = _account?.avatar_url || '';
  const peerUsername = peer?.username || '';
  const peerAvatarURL = peer?.avatar_url || '';
  const avatarFor = username => {
    if (username === ownUsername) return ownAvatar;
    if (username === peerUsername) return peerAvatarURL;
    return '';
  };
  return {
    ...result,
    avatar_url1: result.avatar_url1 || avatarFor(result.username1),
    avatar_url2: result.avatar_url2 || avatarFor(result.username2),
  };
}

function emptyInbox(text) {
  return `<div class="rounded-xl border border-dashed border-outline-variant/30 p-5 text-center text-on-surface-variant text-sm">${escapeHTML(text)}</div>`;
}

function blendMyCard(item) {
  const peer = item.peer || {};
  const result = item.blend_result;
  const score = Number(result?.score);
  const hasResult = Number.isFinite(score) && !!result;
  const name = escapeHTML(peer.display_name || peer.username || 'Bilinmeyen');
  const username = escapeHTML(peer.username || '');
  const poster = safeImageURL(peer.avatar_url);
  const avatar = poster
    ? `<img src="${poster}" alt="${name}" class="w-14 h-14 rounded-full object-cover border border-outline-variant/30"/>`
    : `<div class="w-14 h-14 rounded-full bg-surface-container flex items-center justify-center text-primary-container text-xl font-bold">${name[0] || '?'}</div>`;
  const dateValue = result?.result?.generated_at || result?.created_at || item.decided_at || item.created_at;
  const dateLabel = dateValue ? new Date(dateValue).toLocaleDateString(uiLocale()) : '';
  const scoreBlock = hasResult
    ? `<div class="text-right leading-none shrink-0"><div class="text-3xl font-bold text-primary-container">${score}</div><div class="font-label-sm text-label-sm uppercase tracking-wide text-on-surface-variant/60 mt-1">% uyum</div></div>`
    : `<span class="shrink-0 font-label-sm text-label-sm uppercase tracking-wide text-on-surface-variant/50">hazır değil</span>`;
  const head = `<div class="flex items-center gap-3">
      ${avatar}
      <div class="min-w-0 flex-grow">
        <strong class="text-on-surface block truncate">${name}</strong>
        <span class="text-on-surface-variant text-sm block truncate">@${username}${dateLabel ? ` · ${escapeHTML(dateLabel)}` : ''}</span>
      </div>
      ${scoreBlock}
    </div>`;
  if (hasResult) {
    return `<article class="glass-panel rounded-2xl p-5 flex flex-col hover:border-primary-container/40 transition-colors">
      ${head}
      <div class="mt-4 grid grid-cols-3 gap-2">
        <button data-blend-action="view" data-request-id="${escapeHTML(item.id)}" class="min-h-[42px] rounded-lg bg-surface-variant px-2 text-xs uppercase text-on-surface hover:text-primary">Aç</button>
        <button data-blend-action="refresh-result" data-request-id="${escapeHTML(item.id)}" class="min-h-[42px] rounded-lg border border-outline-variant/30 px-2 text-xs uppercase text-on-surface-variant hover:text-primary">Yenile</button>
        <button data-blend-action="delete-result" data-request-id="${escapeHTML(item.id)}" data-peer-username="${username}" class="min-h-[42px] rounded-lg border border-error/30 px-2 text-xs uppercase text-error hover:bg-error/10">Sil</button>
      </div>
    </article>`;
  }
  return `<article class="glass-panel rounded-2xl p-5 flex flex-col">
    ${head}
    <button data-blend-action="retry" data-request-id="${escapeHTML(item.id)}" class="mt-4 w-full px-3 py-2.5 rounded-lg border border-outline-variant/30 text-on-surface-variant hover:text-primary text-sm uppercase tracking-wide">Sonucu hazırla</button>
  </article>`;
}

function renderMyBlends(data) {
  const done = (data.history || []).filter(item => item.status === 'accepted');
  $('blends-list').innerHTML = done.length
    ? done.map(blendMyCard).join('')
    : emptyInbox('Henüz tamamlanmış bir Blend yok. Bir arkadaşına Blend isteği gönder.');
  const ready = done.filter(item => !!item.blend_result).length;
  $('profile-blends-badge').textContent = ready > 9 ? '9+' : String(ready);
  $('profile-blends-badge').classList.toggle('hidden', ready === 0);
  $('profile-blends-badge').classList.toggle('flex', ready > 0);
}

async function loadMyBlends(show = true) {
  if (!_account) return;
  if (show) showView('blends');
  $('blends-error').classList.add('hidden');
  try {
    renderBlendInbox(await apiJSON('/api/blends'));
  } catch (error) {
    if (show) {
      $('blends-error').textContent = error.message || 'Blendler yüklenemedi.';
      $('blends-error').classList.remove('hidden');
    }
  }
}

function blockedUserCard(item) {
  const user = item.user || {};
  const username = escapeHTML(user.username || '');
  return `<article class="glass-panel rounded-xl p-4 flex flex-col sm:flex-row sm:items-center gap-4 overflow-safe">
    <div class="flex items-center gap-3 min-w-0 flex-1">
      ${peerAvatar(user)}
      <div class="min-w-0 flex-grow"><strong class="text-on-surface block truncate">${escapeHTML(user.display_name || user.username || 'Bilinmeyen')}</strong><span class="text-on-surface-variant text-sm block truncate">@${username}</span></div>
    </div>
    <button data-blend-action="unblock" data-peer-username="${username}" class="w-full sm:w-auto min-h-[42px] px-3 py-2 rounded-lg border border-outline-variant/30 text-on-surface-variant hover:text-primary text-xs uppercase">Engeli kaldır</button>
  </article>`;
}

async function loadBlockedUsers() {
  const list = $('blocked-users-list');
  const notice = $('blocked-users-notice');
  const error = $('blocked-users-error');
  if (!list) return;
  notice.classList.add('hidden');
  error.classList.add('hidden');
  list.innerHTML = '<p class="py-6 text-center text-sm text-on-surface-variant/60">Yükleniyor…</p>';
  try {
    const data = await apiJSON('/api/blends');
    _blendInbox = data;
    list.innerHTML = data.blocked?.length
      ? data.blocked.map(blockedUserCard).join('')
      : emptyInbox('Engellediğin kullanıcı yok.');
  } catch (requestError) {
    list.innerHTML = '';
    error.textContent = requestError.message || 'Engellenen kullanıcılar yüklenemedi.';
    error.classList.remove('hidden');
  }
}

async function openBlockedUsers() {
  toggleProfileMenu(false);
  const dialog = $('dialog-blocked-users');
  if (!dialog.open) dialog.showModal();
  await loadBlockedUsers();
}

function renderBlendInbox(data) {
  _blendInbox = data;
  $('blend-incoming').innerHTML = data.incoming?.length
    ? data.incoming.map(item => blendRequestCard(item, 'incoming')).join('')
    : emptyInbox('Bekleyen gelen isteğin yok.');
  $('blend-outgoing').innerHTML = data.outgoing?.length
    ? data.outgoing.map(item => blendRequestCard(item, 'outgoing')).join('')
    : emptyInbox('Bekleyen gönderilmiş isteğin yok.');
  // The small polling endpoint owns the combined Blend + letter badge; do not
  // overwrite it here with only the incoming Blend count.
  refreshBlendBadge();
  renderMyBlends(data);
}

async function loadBlendInbox(show = true) {
  if (!_account) return;
  if (show) showView('blends');
  $('blends-error').classList.add('hidden');
  try {
    const data = await apiJSON('/api/blends');
    renderBlendInbox(data);
    return data;
  } catch (error) {
    if (show) {
      $('blends-error').textContent = error.message || 'Blendler yüklenemedi.';
      $('blends-error').classList.remove('hidden');
    }
  }
  return null;
}

// Gelen kutusu artık yalnız mektuplar; Blend tarafı kendi sekmesinde.
async function openLetterInbox() {
  showView('inbox');
  _openLetterThread = '';
  await loadLetters();
}

async function openFollowerLetterPicker() {
  const dialog = $('dialog-letter-followers');
  const list = $('letter-followers-list');
  if (!dialog || !list) return;
  list.innerHTML = '<p class="py-8 text-center text-sm text-on-surface-variant/60">Takipçilerin yükleniyor…</p>';
  if (!dialog.open) dialog.showModal();
  try {
    const data = await apiJSON('/api/letters/followers');
    const followers = data.followers || [];
    list.innerHTML = followers.length
      ? followers.map(person => `<button type="button" data-letter-follower="${escapeHTML(person.username)}" class="flex w-full items-center gap-3 rounded-xl border border-outline-variant/20 bg-surface-container/45 p-3 text-left transition-colors hover:border-tertiary-container/40 hover:bg-tertiary-container/10"><span class="shrink-0">${peerAvatar(person)}</span><span class="min-w-0 flex-1"><strong class="block truncate text-sm text-on-surface">${escapeHTML(person.display_name || person.username)}</strong><span class="block truncate text-xs text-on-surface-variant/60">@${escapeHTML(person.username)}</span></span><span class="material-symbols-outlined text-tertiary-container">edit</span></button>`).join('')
      : '<div class="rounded-xl border border-outline-variant/20 bg-surface-container/35 px-4 py-7 text-center text-sm leading-relaxed text-on-surface-variant">Mektup kutusu açık bir takipçin henüz yok.</div>';
  } catch (error) {
    list.innerHTML = `<p class="rounded-xl bg-error-container/30 px-4 py-3 text-sm text-error">${escapeHTML(error.message || 'Takipçilerin şu an yüklenemedi.')}</p>`;
  }
}

async function openLetterSendingArea() {
  showView('sinefil');
  await loadSinefilArea();
  sinefilMessage('notice', 'Mektup yazmak için mektuplara açık bir sinefilin Mektup düğmesine dokun.');
}

async function routeToExistingBlend(data) {
  const requestId = data.request_id;
  if (data.status === 'accepted') {
    let stored = await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}/result`);
    if (!stored.result) {
      stored = await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}/result`, {
        method: 'POST', headers: csrfHeaders(),
      });
    }
    if (stored.result) {
      await renderBlendResult(stored.result);
      return;
    }
    await loadMyBlends(true);
    return;
  }

  await loadBlendInbox(true);
  await new Promise(resolve => requestAnimationFrame(resolve));
  const trigger = [...document.querySelectorAll('[data-request-id]')]
    .find(node => node.dataset.requestId === requestId);
  const card = trigger?.closest('article');
  if (card) {
    card.classList.add('ring-2', 'ring-secondary-container/70');
    card.scrollIntoView({ block: 'center', behavior: 'smooth' });
    setTimeout(() => card.classList.remove('ring-2', 'ring-secondary-container/70'), 2600);
  }
  $('blends-notice').textContent = data.direction === 'incoming'
    ? 'Bu kullanıcı sana zaten bir Blend isteği göndermiş. İstek burada.'
    : 'Bu kullanıcıya gönderdiğin Blend isteği hâlâ yanıt bekliyor.';
  $('blends-notice').classList.remove('hidden');
}

async function handleBlendInboxAction(event) {
  if (event.target.closest('[data-letter-mobile-back]')) {
    _openLetterThread = '';
    renderLetterConversation();
    renderLetterWorkspace();
    return;
  }
  const thread = event.target.closest('[data-letter-thread]');
  if (thread) {
    renderLetterConversation(thread.dataset.letterThread);
    return;
  }
  const reply = event.target.closest('[data-letter-reply]');
  if (reply) {
    _openLetterThread = reply.dataset.letterReply;
    openLetterCompose(reply.dataset.letterReply);
    return;
  }
  const letterButton = event.target.closest('[data-letter-action]');
  if (letterButton) {
    const action = letterButton.dataset.letterAction;
    const username = letterButton.dataset.peerUsername;
    if (action === 'delete') {
      const letterId = letterButton.dataset.letterId;
      if (!letterId || !window.confirm('Bu gönderilmiş mektup iki tarafın konuşmasından da silinsin mi?')) return;
      letterButton.disabled = true;
      try {
        await apiJSON(`/api/letters/${encodeURIComponent(letterId)}`, { method: 'DELETE', headers: csrfHeaders() });
        letterMessage('notice', 'Mektup iki tarafın konuşmasından kaldırıldı.');
        await loadLetters();
      } catch (error) { letterMessage('error', error.message || 'Mektup silinemedi.'); }
      return;
    }
    if (!username) return;
    if (action === 'block') {
      if (!window.confirm(`@${username} engellensin mi? Aranızdaki mektuplar iki taraftan da silinir.`)) return;
      try {
        await apiJSON(`/api/users/${encodeURIComponent(username)}/block`, { method: 'POST', headers: csrfHeaders() });
        letterMessage('notice', `@${username} engellendi; mektuplar kaldırıldı.`);
        await loadLetters();
      } catch (error) { letterMessage('error', error.message || 'Kullanıcı engellenemedi.'); }
      return;
    }
    if (action === 'report') {
      const category = window.prompt('Bildirim kategorisi: spam, harassment, impersonation veya other', 'harassment');
      if (category === null) return;
      try {
        await apiJSON(`/api/users/${encodeURIComponent(username)}/report`, { method: 'POST', headers: csrfHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ category: category.trim().toLowerCase(), detail: 'Şifreli mektup üzerinden bildirildi; içerik otomatik paylaşılmadı.' }) });
        letterMessage('notice', 'Bildirimin alındı. Mektup içeriği paylaşılmadı.');
      } catch (error) { letterMessage('error', error.message || 'Bildirim gönderilemedi.'); }
      return;
    }
  }
  const button = event.target.closest('[data-blend-action]');
  if (!button) return;
  const action = button.dataset.blendAction;
  const requestId = button.dataset.requestId;
  const peerUsername = button.dataset.peerUsername;
  const inBlendLibrary = !!button.closest('#view-blends');
  const actionError = inBlendLibrary ? $('blends-error') : $('blends-error');
  const actionNotice = inBlendLibrary ? $('blends-notice') : $('blends-notice');
  actionError.classList.add('hidden');
  actionNotice?.classList.add('hidden');
  $('blends-notice').classList.add('hidden');
  if (action === 'block') {
    if (!peerUsername || !window.confirm(`@${peerUsername} engellensin mi? Bekleyen Blend istekleri de iptal edilir.`)) return;
    button.disabled = true;
    try {
      await apiJSON(`/api/users/${encodeURIComponent(peerUsername)}/block`, {
        method: 'POST', headers: csrfHeaders(),
      });
      await loadBlendInbox(false);
      $('blends-notice').textContent = `@${peerUsername} engellendi.`;
      $('blends-notice').classList.remove('hidden');
    } catch (error) {
      $('blends-error').textContent = error.message || 'Kullanıcı engellenemedi.';
      $('blends-error').classList.remove('hidden');
    } finally { button.disabled = false; }
    return;
  }
  if (action === 'unblock') {
    if (!peerUsername) return;
    button.disabled = true;
    try {
      await apiJSON(`/api/users/${encodeURIComponent(peerUsername)}/block`, {
        method: 'DELETE', headers: csrfHeaders(),
      });
      await loadBlockedUsers();
      $('blocked-users-notice').textContent = `@${peerUsername} engeli kaldırıldı.`;
      $('blocked-users-notice').classList.remove('hidden');
    } catch (error) {
      $('blocked-users-error').textContent = error.message || 'Engel kaldırılamadı.';
      $('blocked-users-error').classList.remove('hidden');
    } finally { button.disabled = false; }
    return;
  }
  if (action === 'report') {
    if (!peerUsername) return;
    const category = window.prompt('Bildirim kategorisi: spam, harassment, impersonation veya other', 'spam');
    if (category === null) return;
    const normalizedCategory = category.trim().toLowerCase();
    if (!['spam', 'harassment', 'impersonation', 'other'].includes(normalizedCategory)) {
      $('blends-error').textContent = 'Geçersiz bildirim kategorisi.';
      $('blends-error').classList.remove('hidden');
      return;
    }
    const detail = window.prompt('Kısa açıklama (isteğe bağlı, en fazla 500 karakter)', '') ?? '';
    button.disabled = true;
    try {
      await apiJSON(`/api/users/${encodeURIComponent(peerUsername)}/report`, {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ category: normalizedCategory, detail: detail.slice(0, 500) }),
      });
      $('blends-notice').textContent = 'Bildirimin alındı.';
      $('blends-notice').classList.remove('hidden');
    } catch (error) {
      $('blends-error').textContent = error.message || 'Bildirim gönderilemedi.';
      $('blends-error').classList.remove('hidden');
    } finally { button.disabled = false; }
    return;
  }
  if (action === 'view') {
    const item = _blendInbox.history.find(entry => entry.id === requestId);
    if (!item) return;
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = 'Açılıyor…';
    try {
      let stored = await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}/result`);
      if (!stored.result) {
        stored = await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}/result`, {
          method: 'POST', headers: csrfHeaders(),
        });
      }
      if (stored.result) {
        await renderBlendResult(blendResultWithPeerAvatars(stored.result, item.peer));
      }
    } catch (error) {
      actionError.textContent = error.message || 'Blend sonucu açılamadı.';
      actionError.classList.remove('hidden');
    } finally {
      button.disabled = false;
      button.textContent = oldText;
    }
    return;
  }
  if (action === 'delete-result') {
    const who = peerUsername ? `@${peerUsername} ile olan ` : '';
    const confirmed = window.confirm(
      `${who}Blend kalıcı olarak silinsin mi? Bu işlem Blend'i iki tarafın geçmişinden de kaldırır.`
    );
    if (!confirmed) return;
  }
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = ['accepted', 'retry', 'refresh-result'].includes(action) ? 'Hazırlanıyor…' : 'İşleniyor…';
  try {
    if (action === 'delete-result') {
      await apiJSON(`/api/blends/${encodeURIComponent(requestId)}`, {
        method: 'DELETE', headers: csrfHeaders(),
      });
      await loadBlendInbox(false);
      if (actionNotice) {
        actionNotice.textContent = 'Blend iki tarafın geçmişinden silindi.';
        actionNotice.classList.remove('hidden');
      }
      return;
    }
    if (action === 'refresh-result') {
      const data = await apiJSON(`/api/blends/${encodeURIComponent(requestId)}/refresh`, {
        method: 'POST', headers: csrfHeaders(),
      });
      await loadBlendInbox(false);
      if (data.result) await renderBlendResult(data.result);
      return;
    }
    if (action === 'cancel') {
      await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}`, {
        method: 'DELETE', headers: csrfHeaders(),
      });
      await loadBlendInbox(false);
      return;
    }
    if (action === 'retry') {
      const data = await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}/result`, {
        method: 'POST', headers: csrfHeaders(),
      });
      await loadBlendInbox(false);
      if (data.result) await renderBlendResult(data.result);
      return;
    }
    const data = await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}/decision`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ decision: action }),
    });
    await loadBlendInbox(false);
    if (action === 'accepted' && data.result) await renderBlendResult(data.result);
  } catch (error) {
    actionError.textContent = error.message || 'İşlem tamamlanamadı.';
    actionError.classList.remove('hidden');
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function syncProfile(force = false, refreshWatchlist = false) {
  if (!_account) return;
  const button = $('btn-profile-sync');
  button.disabled = true;
  button.querySelector('span').classList.add('animate-spin');
  $('profile-sync-error').classList.add('hidden');
  $('profile-account-summary').textContent = 'İzleme geçmişin ve Fav 4 filmlerin analiz ediliyor…';
  try {
    const data = await apiJSON(`/api/profile/sync?force=${force ? 'true' : 'false'}&refresh_watchlist=${refreshWatchlist ? 'true' : 'false'}`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
    });
    if (data.taste && !data.taste.updated_at) data.taste.updated_at = new Date().toISOString();
    renderPersistedProfile(data);
    if ($('view-onboarding').classList.contains('hidden')) {
      _topFilmsLoaded = false; loadTopFilms();
      _recentLoaded = false; loadRecentFilms(true);
      _statsLoaded = false; loadProfileStats();
    }
    return data;
  } catch (error) {
    $('profile-account-summary').textContent = 'Profil senkronu tamamlanamadı. Yenile düğmesiyle tekrar deneyebilirsin.';
    $('profile-sync-error').textContent = error.message || 'Profil senkronu tamamlanamadı.';
    $('profile-sync-error').classList.remove('hidden');
    return null;
  } finally {
    button.disabled = false;
    button.querySelector('span').classList.remove('animate-spin');
  }
}

async function loadProfile() {
  try {
    const profile = await apiJSON('/api/profile/me');
    renderPersistedProfile(profile);
    if (_account?.profile_sync_status === 'pending' || profile.needs_refresh) syncProfile();
    else {
      checkFavoriteFreshness();
      checkWatchlistFreshness();
    }
  } catch (_) {
    if (_account?.profile_sync_status === 'pending') syncProfile();
  }
}

async function checkFavoriteFreshness() {
  if (!_account) return;
  // A profile page is tiny, but do not make repeated view switches scrape it.
  const key = `mb_fav4_check:${_account.username || _account.id}`;
  const lastCheck = Number(sessionStorage.getItem(key) || 0);
  if (Date.now() - lastCheck < 5 * 60 * 1000) return;
  sessionStorage.setItem(key, String(Date.now()));
  try {
    const result = await apiJSON('/api/profile/favorites/check', {
      method: 'POST',
      headers: csrfHeaders(),
    });
    if (result.changed && result.profile) {
      renderPersistedProfile(result.profile);
      _topFilmsLoaded = false;
      _recentLoaded = false;
    }
  } catch (_) {
    // A temporary Letterboxd block must never hide an otherwise usable profile.
  }
}

async function checkWatchlistFreshness() {
  if (!_account) return;
  const key = `mb_watchlist_check:${_account.username || _account.id}`;
  const lastCheck = Number(sessionStorage.getItem(key) || 0);
  if (Date.now() - lastCheck < 5 * 60 * 1000) return;
  sessionStorage.setItem(key, String(Date.now()));
  try {
    await apiJSON('/api/profile/watchlist/check', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
    });
  } catch (_) {
    // The last known-good watchlist remains usable when Letterboxd is unavailable.
    sessionStorage.removeItem(key);
  }
}

function _onboardKey(account) {
  return 'mb_onboarded:' + (account?.username || account?.id || '');
}

function enterApp(account, opts = {}) {
  applyAccount(account);
  // Authentication intentionally keeps social preference columns optional for
  // migration safety. Hydrate the live letter setting after the session is in.
  refreshLiveLetterSettings().catch(() => {});
  // İlk ekranda yalnız küçük badge sayısını al. Büyük Blend geçmişi ve kayıtlı
  // sonuç payload'ları inbox / Blendlerim gerçekten açıldığında lazy-load edilir.
  refreshBlendBadge();
  refreshFeedBadge();
  startBlendBadgePolling();
  startFeedNotificationPolling();
  // Onboarding always plays right after a fresh registration. Otherwise it
  // plays only while the first sync is still pending and it hasn't already
  // been shown for this account in this tab.
  const key = _onboardKey(account);
  if (opts.fromRegistration) sessionStorage.removeItem(key);
  if (
    opts.fromRegistration ||
    !account.onboarding_completed_at ||
    (['pending', 'syncing'].includes(account.profile_sync_status) && !sessionStorage.getItem(key))
  ) {
    startOnboarding();
    return;
  }
  // Yenilendiğinde aynı ekrana dönülür; adres yoksa ev akıştır. Pano arka
  // planda hazırlanmaya devam eder, "Profil"e geçiş anlık olsun diye.
  restoreRoute()
    .then(restored => (restored ? null : openFeed()))
    .then(loadProfile);
  showInstallAppDialog();
}

// ── Onboarding reveal ──────────────────────────────────────────────────
// Tüm izleme geçmişi taraması bitene kadar tek bir bekleme ekranı çalışır;
// tam analiz hazır olduğunda slaytlar sırayla sunulur.
let _obToken = 0;             // her yeni çalışma bu sayacı artırır — async iptal kontrolü
let _obSlideTimer = null;     // slayt otomatik ilerleme
let _obFactTimer = null;      // bilgi kartı rotasyonu
let _obPollTimer = null;      // tam tarama job yoklaması
let _obReveal = null;         // { slides:[fn], index, token } — tarama sonrası sunum
const OB_SLIDE_MS = 15000;
const OB_FACT_MS = 7000;
const OB_POLL_MS = 5000;

const OB_BUCKETS = [
  { max: 250,      text: 'Kısa ve tatlı bir geçmişin var. Analizin birazdan hazır, daha esnemeye fırsat bulamadan döneriz.' },
  { max: 500,      text: 'Dolu dolu bir arşiv! Filmleri tek tek okuyoruz, yalnızca bir-iki dakika. Sen keyfine bak.' },
  { max: 750,      text: 'Bu ciddi bir koleksiyon. Yüzlerce filmi tarıyoruz, birkaç dakika sürebilir; bu arada aşağıdaki sinema bilgileriyle vakit geçir.' },
  { max: 1000,     text: 'Kocaman bir sinema geçmişin var ve hepsini hakkıyla analiz etmek istiyoruz. Kahveni tazele, birkaç dakikaya buradayız.' },
  { max: Infinity, text: 'Binden fazla film… Sen gerçek bir sinefilsin. Bu arşivi satır satır okumak birkaç dakika alacak ama sonucu görünce ‘iyi ki beklemişim’ diyeceksin.' },
];
function _obBucketText(total) {
  return (OB_BUCKETS.find(b => (total || 0) <= b.max) || OB_BUCKETS[OB_BUCKETS.length - 1]).text;
}

const OB_MILESTONES = [
  { at: 250,  text: '250 filmi geride bıraktık…' },
  { at: 500,  text: '500 film tamam, tempo yerinde.' },
  { at: 750,  text: '750 film oldu, hâlâ okuyoruz.' },
  { at: 1000, text: '1000 filmi de devirdik — amma izlemişsin!' },
  { at: 1500, text: 'Son düzlükteyiz, analiz derleniyor…' },
];
function _obMilestoneText(processed) {
  let hit = '';
  for (const m of OB_MILESTONES) if ((processed || 0) >= m.at) hit = m.text;
  return hit;
}

function _obClearTimers() {
  if (_obSlideTimer) { clearTimeout(_obSlideTimer); _obSlideTimer = null; }
  if (_obFactTimer)  { clearInterval(_obFactTimer); _obFactTimer = null; }
  if (_obPollTimer)  { clearInterval(_obPollTimer); _obPollTimer = null; }
  _obReveal = null;
}

// Bu onboarding çalışması hâlâ geçerli mi? Değilse timer'ları da temizler.
function _obLive(token) {
  const ok = token === _obToken && !$('view-onboarding').classList.contains('hidden');
  if (!ok) _obClearTimers();
  return ok;
}

function finishOnboarding() {
  _obToken += 1;
  _obClearTimers();
  if (_account) sessionStorage.setItem(_onboardKey(_account), '1');
  $('ob-skip').classList.add('hidden');
  $('ob-prev').classList.add('hidden');
  $('ob-next').classList.add('hidden');
  showView('profile');
  if (_persistedProfile) renderPersistedProfile(_persistedProfile);
  else loadProfile();
  // Yeni kaydolan da uygulamayı kurma çağrısını görsün. `enterApp` onboarding
  // yoluna sapıp geri döndüğü için buraya kadar hiç çağrılmıyordu, yani yeni
  // üye çağrıyı ancak bir sonraki girişinde görüyordu. Kısa gecikme profilin
  // ilk kez boyanmasına izin veriyor; modal onun üstüne aniden binmiyor.
  setTimeout(showInstallAppDialog, 1500);
}

async function completeOnboarding() {
  const button = $('ob-skip');
  button.disabled = true;
  try {
    const data = await apiJSON('/api/profile/onboarding-complete', {
      method: 'POST',
      headers: csrfHeaders(),
    });
    if (_account) _account.onboarding_completed_at = data.completed_at;
    finishOnboarding();
  } catch (error) {
    $('ob-bg-note').textContent = error.message || 'Onboarding tamamlanamadı. Lütfen tekrar dene.';
  } finally {
    button.disabled = false;
  }
}

function _obStage(html) {
  $('ob-stage').innerHTML = `<div class="ob-in">${html}</div>`;
}

function _obDots(active, total) {
  $('ob-dots').innerHTML = Array.from({ length: total }, (_, i) =>
    `<i class="${i === active ? 'on' : ''}"></i>`).join('');
}

function _countUp(el, target, ms = 1100) {
  if (_reduceMotion) { el.textContent = target.toLocaleString(uiLocale()); return; }
  const start = performance.now();
  const step = (now) => {
    const p = Math.min((now - start) / ms, 1);
    el.textContent = Math.round((1 - Math.pow(1 - p, 3)) * target).toLocaleString(uiLocale());
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// Bekleme ekranındaki bilgi/alıntı kartı — 4 sn'de bir yumuşak geçişle akar.
function _obPaintFact() {
  const item = _nextFact();
  const t = $('ob-fact-text');
  if (!t) return;
  const a = $('ob-fact-author');
  const b = $('ob-fact-badge');
  const swap = () => {
    if (b) b.textContent = item.type === 'quote' ? '❝ Söz' : '🎬 Bilgi';
    t.textContent = item.text;
    if (a) a.textContent = item.author ? `— ${item.author}` : '';
  };
  const card = $('ob-fact-card');
  if (card && !_reduceMotion) {
    card.style.opacity = '0';
    setTimeout(() => { swap(); card.style.opacity = '1'; }, 220);
  } else {
    swap();
  }
}

function _obRenderWaiting(heading) {
  _obStage(`
    <p class="font-label-sm text-label-sm uppercase tracking-[.24em] text-primary-container">${escapeHTML(heading)}</p>
    <p id="ob-bucket-line" class="mt-3 font-body-md text-body-md text-on-surface/90 leading-relaxed min-h-[1.5em]"></p>
    <p id="ob-milestone-line" class="mt-3 font-label-sm text-label-sm text-primary-container/90 min-h-[1.25em]"></p>
    <div id="ob-fact-card" style="transition:opacity .3s ease" class="mt-6 rounded-2xl border border-outline-variant/20 bg-surface-container/50 p-5 text-left">
      <span id="ob-fact-badge" class="font-label-sm text-label-sm text-on-surface-variant/60"></span>
      <p id="ob-fact-text" class="mt-2 font-body-md text-body-md text-on-surface/90 leading-relaxed"></p>
      <p id="ob-fact-author" class="mt-2 font-label-sm text-label-sm text-on-surface-variant/60"></p>
    </div>`);
  _obPaintFact();
  if (_obFactTimer) clearInterval(_obFactTimer);
  _obFactTimer = setInterval(_obPaintFact, OB_FACT_MS);
}

function _obStopFacts() {
  if (_obFactTimer) { clearInterval(_obFactTimer); _obFactTimer = null; }
}

function _obRenderWelcome() {
  const name = escapeHTML(((_account.display_name || _account.username || '').split(' ')[0]) || _account.username || '');
  const avatar = safeImageURL(_account.avatar_url);
  _obStage(`
    <div class="flex flex-col items-center gap-5">
      ${avatar
        ? `<img src="${avatar}" alt="" class="w-32 h-32 rounded-full object-cover ring-2 ring-primary-container/40 shadow-2xl"/>`
        : `<div class="w-32 h-32 rounded-full bg-surface-container ring-2 ring-primary-container/40 flex items-center justify-center font-display-lg text-[44px] text-primary-container">${name ? name[0].toUpperCase() : '?'}</div>`}
      <div>
        <p class="font-label-sm text-label-sm uppercase tracking-[.24em] text-primary-container">Letterboxd profilin bağlandı</p>
        <h2 class="mt-2 font-headline-lg text-[30px] text-on-surface">Merhaba ${name}</h2>
        <p class="mt-3 font-body-md text-body-md text-on-surface-variant/70">Zevk profilini çıkardık — birlikte bakalım.</p>
      </div>
    </div>`);
}

function _obRenderNumbers(items) {
  _obStage(`
    <p class="font-label-sm text-label-sm uppercase tracking-[.24em] text-primary-container">Letterboxd geçmişin</p>
    <div class="mt-6 grid grid-cols-3 gap-3">
      ${items.map(x => `
        <div class="rounded-2xl border border-outline-variant/20 bg-surface-container/50 p-4">
          <strong data-ob-count="${x.value}" class="block font-display-lg text-[26px] md:text-[30px] leading-none text-on-surface">0</strong>
          <span class="mt-2 block font-label-sm text-[9px] md:text-label-sm uppercase tracking-wide text-on-surface-variant">${escapeHTML(x.label)}</span>
        </div>`).join('')}
    </div>`);
  $('ob-stage').querySelectorAll('[data-ob-count]').forEach(el =>
    _countUp(el, Number(el.dataset.obCount)));
}

function _obRenderFavs(favs) {
  _obStage(`
    <p class="font-label-sm text-label-sm uppercase tracking-[.24em] text-secondary-container">Favori dörtlün</p>
    <div class="mt-6 grid grid-cols-4 gap-2.5">
      ${favs.map((f, i) => {
        const poster = safeImageURL(f.poster_url);
        const title = escapeHTML(f.title || '');
        return `<div class="line-rise" style="animation-delay:${i * 140}ms">
          <div class="relative aspect-[2/3] rounded-xl overflow-hidden bg-surface-container ring-1 ring-outline-variant/25">
            ${poster ? `<img src="${poster}" alt="${title}" class="absolute inset-0 w-full h-full object-cover"/>` : `<div class="absolute inset-0 flex items-center justify-center p-2 text-center text-[9px] text-on-surface-variant/70">${title}</div>`}
          </div>
        </div>`;
      }).join('')}
    </div>`);
}

function _obRenderDirector(d) {
  _obStage(`
    <p class="font-label-sm text-label-sm uppercase tracking-[.24em] text-tertiary-container">Favori yönetmenin</p>
    <div class="mt-6 flex flex-col items-center gap-4">
      ${directorAvatar(d, 'w-28 h-28 text-[36px]')}
      <div>
        <h2 class="font-headline-lg text-[26px] text-on-surface">${escapeHTML(d.name)}</h2>
        <p class="mt-1 font-body-md text-body-md text-on-surface-variant">${Number(d.count) || 0} filmini izledin${d.avg_rating ? ` · ortalaman ${Number(d.avg_rating).toFixed(1)}★` : ''}</p>
      </div>
    </div>`);
}

function _obRenderPersonality(text) {
  _obStage(`
    <p class="font-label-sm text-label-sm uppercase tracking-[.24em] text-primary-container">Sinefil kişiliğin</p>
    <p id="ob-personality" class="mt-5 font-body-lg text-body-lg leading-[1.7] text-on-surface"></p>`);
  streamText($('ob-personality'), text);
}

function _obRenderSinefilConsent() {
  const visible = _account?.discoverable !== false;
  _obStage(`
    <p class="font-label-sm text-label-sm uppercase tracking-[.24em] text-tertiary-container">Sinefil Sineması</p>
    <h2 class="mt-3 font-headline-lg text-[26px] text-on-surface">Zevkini paylaşmak ister misin?</h2>
    <p class="mt-3 font-body-md text-body-md leading-relaxed text-on-surface-variant/80">Profilin varsayılan olarak diğer kayıtlı sinefillere görünür. İstediğin an profilinden gizleyebilirsin.</p>
    <div class="mt-6 grid gap-3 sm:grid-cols-2">
      <button type="button" data-ob-discoverable="true" class="rounded-xl bg-tertiary-container px-4 py-3 font-label-md text-label-md uppercase tracking-wide text-on-tertiary-container">Görünür kal</button>
      <button type="button" data-ob-discoverable="false" class="rounded-xl border border-outline-variant/30 bg-surface-container px-4 py-3 font-label-md text-label-md uppercase tracking-wide text-on-surface-variant">Gizli yap</button>
    </div>
    <p id="ob-discovery-note" class="mt-4 font-label-sm text-label-sm ${visible ? 'text-tertiary-container' : 'text-on-surface-variant/60'}">${visible ? 'Profilin Sinefil Sineması’nda görünür.' : 'Profilin gizli kalacak.'}</p>`);
  $('ob-stage').querySelectorAll('[data-ob-discoverable]').forEach(button => {
    button.addEventListener('click', async () => {
      const next = button.dataset.obDiscoverable === 'true';
      button.disabled = true;
      try {
        const saved = await saveDiscoveryVisibility(next);
        const note = $('ob-discovery-note');
        note.textContent = saved ? 'Profilin görünür durumda.' : 'Profilin gizli kalacak.';
        note.className = `mt-4 font-label-sm text-label-sm ${saved ? 'text-tertiary-container' : 'text-on-surface-variant/60'}`;
      } catch (error) {
        $('ob-discovery-note').textContent = error.message || 'Tercih kaydedilemedi; profilin gizli kalacak.';
      } finally { button.disabled = false; }
    });
  });
}

function _obRenderOutro(full) {
  _obStage(`
    <p class="font-label-sm text-label-sm uppercase tracking-[.24em] text-primary-container">Hazır</p>
    <h2 class="mt-3 font-headline-lg text-[26px] text-on-surface">Zevk profilin hazır</h2>
    <p class="mt-3 font-body-md text-body-md text-on-surface-variant/80">${full
      ? 'Tüm izleme geçmişin ve yönetmen verilerin analiz edildi. İçeri girip bu geceye bir film seçelim.'
      : 'Tam analiz doğrulanıyor; tamamlanmadan profile geçilmeyecek.'}</p>`);
}

// Tarama sonrası sunum: slaytları sırayla gösterir, OB_SLIDE_MS'de bir
// otomatik ilerler; kullanıcı ileri/geri gezinebilir (timer sıfırlanır).
function _obShowRevealSlide(i) {
  const r = _obReveal;
  if (!r || r.token !== _obToken) return;
  r.index = Math.max(0, Math.min(i, r.slides.length - 1));
  const last = r.index === r.slides.length - 1;
  _obDots(r.index, r.slides.length);
  r.slides[r.index]();
  $('ob-prev').classList.toggle('hidden', r.index === 0);
  $('ob-next').classList.toggle('hidden', last);
  $('ob-skip').classList.toggle('hidden', !last);
  $('ob-bg-note').textContent = last
    ? 'Hazır olduğunda uygulamaya geçebilirsin.'
    : 'İleri / geri gezinebilirsin.';
  if (_obSlideTimer) { clearTimeout(_obSlideTimer); _obSlideTimer = null; }
  if (!last) {
    _obSlideTimer = setTimeout(() => _obShowRevealSlide(r.index + 1), OB_SLIDE_MS);
  }
}

function _obRevealNav(delta) {
  if (_obReveal) _obShowRevealSlide(_obReveal.index + delta);
}

// Tüm Letterboxd sayfaları, tüm film metadata pass'i ve final zevk snapshot'ı
// tamamlanana kadar bekler. Ham crawl'un bitmesi tek başına yeterli değildir.
function _obAwaitFullSweep(token, provisional) {
  return new Promise(resolve => {
    const job0 = provisional && provisional.sync_job;
    if (job0 && job0.onboarding_ready) {
      apiJSON('/api/profile/me')
        .then(p => { if (p) _persistedProfile = p; resolve(p); })
        .catch(() => resolve(provisional));
      return;
    }
    let lastMilestone = '';

    const tick = async () => {
      if (!_obLive(token)) { resolve(null); return; }
      let status = null;
      try { status = await apiJSON('/api/profile/sync-status'); } catch (_) { /* geçici; yoklamaya devam */ }
      if (!_obLive(token)) { resolve(null); return; }
      const job = status && status.sync_job;
      if (job) {
        const mt = _obMilestoneText(job.processed || 0);
        const ml = $('ob-milestone-line');
        if (ml && mt && mt !== lastMilestone) { ml.textContent = mt; lastMilestone = mt; }
      }
      if (job && job.onboarding_ready) {
        if (_obPollTimer) { clearInterval(_obPollTimer); _obPollTimer = null; }
        _obStopFacts();
        try {
          const profile = await apiJSON('/api/profile/me');
          if (profile) _persistedProfile = profile;
          resolve(profile || provisional);
        } catch (_) {
          resolve(provisional);
        }
        return;
      }
    };

    tick();
    if (_obPollTimer) clearInterval(_obPollTimer);
    if (_obLive(token)) _obPollTimer = setInterval(tick, OB_POLL_MS);
  });
}

async function startOnboarding() {
  const token = ++_obToken;
  _obClearTimers();
  showView('onboarding');
  $('ob-skip').classList.add('hidden');
  $('ob-prev').classList.add('hidden');
  $('ob-next').classList.add('hidden');
  $('ob-skip-label').textContent = 'Uygulamaya geç';
  $('ob-bg-note').textContent = 'Tüm geçmişin ve yönetmen verilerin hazırlanıyor…';
  $('ob-dots').innerHTML = '';

  // ── Bekleme: tüm Letterboxd geçmişi taranana ve reveal verisi hazır olana kadar.
  //    Slaytlar (Merhaba, rakamlar, favori 4, kişilik, yönetmen) tarama
  //    tamamlandıktan sonra sırayla sunulur.
  _obRenderWaiting('Zevk profilin hazırlanıyor');

  const data = await syncProfile();       // bootstrap: kimlik bilgileri + tam sweep'i başlatır
  if (!_obLive(token)) return;
  if (!data) {
    _obRenderWaiting('Bağlantı yeniden kuruluyor');
    $('ob-bg-note').textContent = 'Geçmiş taraması tamamlanmadan devam edilmeyecek.';
    _obSlideTimer = setTimeout(() => {
      if (_obLive(token)) startOnboarding();
    }, 8000);
    return;
  }

  const total0 = Math.max(
    (data.letterboxd_stats || {}).films || 0,
    (data.taste || {}).sample_size || 0,
    (data.sync_job && data.sync_job.total) || 0,
  );
  const bl = $('ob-bucket-line');
  if (bl) bl.textContent = _obBucketText(total0);

  const readyProfile = await _obAwaitFullSweep(token, data);
  if (!_obLive(token)) return;
  _obStopFacts();

  // ── Tarama bitti — slaytları sırayla sun ──
  const profile = readyProfile || _persistedProfile || data;
  const taste = profile.taste || data.taste || {};
  const stats = data.letterboxd_stats || {};
  const favs = (profile.favorite_films || data.favorite_films || []).slice(0, 4);
  const dir = (taste.top_directors_detail || [])[0];
  const total = Math.max(
    stats.films || 0, taste.sample_size || 0,
    (profile.sync_job && profile.sync_job.total)
      || (data.sync_job && data.sync_job.total) || 0,
  );
  const numbers = [
    { label: 'İzlediğin filmler', value: total },
    { label: 'Puanladıkların', value: taste.rated_count || 0 },
    { label: 'Bu yıl', value: stats.this_year || 0 },
  ].filter(x => x.value > 0);
  const personality = (taste.personality || '').trim();

  const slides = [
    () => _obRenderWelcome(),
    numbers.length ? () => _obRenderNumbers(numbers) : null,
    favs.length ? () => _obRenderFavs(favs) : null,
    personality ? () => _obRenderPersonality(personality) : null,
    (dir && dir.name) ? () => _obRenderDirector(dir) : null,
    () => _obRenderSinefilConsent(),
    () => _obRenderOutro(profile?.sync_job?.state === 'done'),
  ].filter(Boolean);

  _obReveal = { slides, index: 0, token };
  _obShowRevealSlide(0);
}

async function boot() {
  // Auth ekranını ağ isteklerine bağlama; mobil ağda session/health yanıtı
  // gecikse bile ziyaretçi formu hemen görsün.
  setAuthMode('register');
  showView('auth');
  // Health ve session birbirinden bağımsızdır. Mobil ağda iki round-trip'i
  // sıraya koymak yerine aynı anda başlatarak giriş/profil açılışını hızlandır.
  const [health, me] = await Promise.all([
    loadHealth(),
    apiJSON('/api/auth/me', { cache: 'no-store' }).catch(() => null),
  ]);
  _authEnabled = Boolean(health?.auth_enabled);
  if (!_authEnabled) { showView('idle'); loadPublicStats(); return; }
  if (me?.account) {
    enterApp(me.account);
    return;
  }
  // apiJSON normally repairs an expired access token itself. Keep this small
  // fallback for a browser that loaded an older shell just before it updated.
  if (cookieValue('mb_csrf')) {
    try {
      const refreshed = await apiJSON('/api/auth/refresh', {
        method: 'POST', headers: csrfHeaders(),
      });
      enterApp(refreshed.account);
      return;
    } catch (_) {}
  }
  // İlk kez gelen ziyaretçiyi kayıt olmaya yönlendir; giriş yapan hesaplar
  // zaten yukarıdaki session dalında doğrudan uygulamaya alınır.
  setAuthMode('register');
  showView('auth');
  loadPublicStats();
}

// ── Loading steps ──────────────────────────────────────────────────────────
const STEP_MAP = { scraping: 0, enriching: 1, ranking: 2, llm: 3 };
let _currentStep = -1;

function resetSteps() {
  _currentStep = -1;
  document.querySelectorAll('#loading-steps .step-item').forEach(li => {
    li.className = 'step-item flex items-center gap-3';
    const icon = li.querySelector('.material-symbols-outlined');
    icon.textContent = li.dataset.icon;
    icon.style.color = '';
  });
}

function completeStep(idx) {
  const li = document.querySelectorAll('#loading-steps .step-item')[idx];
  if (!li) return;
  li.classList.remove('active');
  li.classList.add('done');
  const icon = li.querySelector('.material-symbols-outlined');
  icon.textContent = 'check_circle';
  icon.style.color = '#43fe6d';
}

function activateStep(idx) {
  const li = document.querySelectorAll('#loading-steps .step-item')[idx];
  if (li) li.classList.add('active');
}

function onStep(stepName) {
  const idx = STEP_MAP[stepName] ?? 0;
  if (_currentStep >= 0) completeStep(_currentStep);
  activateStep(idx);
  _currentStep = idx;
}

function finishSteps() {
  if (_currentStep >= 0) completeStep(_currentStep);
}

// ── Queue UI ───────────────────────────────────────────────────────────────
function showQueueInfo(ahead) {
  $('queue-ahead').textContent = ahead;
  $('queue-info').classList.remove('hidden');
  $('queue-info').classList.add('flex');
}

function hideQueueInfo() {
  $('queue-info').classList.add('hidden');
  $('queue-info').classList.remove('flex');
}

// ── Recommendation card builders ──────────────────────────────────────────
const { buildHeroCard, buildAltCard, buildRandomCard } =
  createRecommendationCards();

// ── Render results ─────────────────────────────────────────────────────────
function renderResults(data) {
  $('result-username').textContent = '@' + data.username;

  $('taste-summary').textContent =
    (data.discover_fallback ? 'Watchlist’inde yeterli film yoktu; eksikleri TMDb’den, izlemediklerinden tamamladık. ' : '')
    + (data.taste_summary || 'Film zevkin analiz edildi.');

  const all = data.recommendations;
  const hero = all[0];
  const alts = all.slice(1, 5);

  $('hero-card').innerHTML = hero ? buildHeroCard(hero) : '';

  const altSection = $('alt-section');
  if (alts.length > 0) {
    $('alt-grid').innerHTML = alts.map(buildAltCard).join('');
    altSection.classList.remove('hidden');
  } else {
    altSection.classList.add('hidden');
  }

  showView('results');
}

// ── Blend helpers ──────────────────────────────────────────────────────────
const BLEND_STEP_MAP = { scraping: 0, enriching: 1, ranking: 2 };
let _blendStep = -1;

function resetBlendSteps() {
  _blendStep = -1;
  document.querySelectorAll('#blend-loading-steps .step-item').forEach(li => {
    li.className = 'step-item flex items-center gap-3';
    const icon = li.querySelector('.material-symbols-outlined');
    icon.textContent = li.dataset.icon;
    icon.style.color = '';
  });
}

function onBlendStep(stepName) {
  const idx = BLEND_STEP_MAP[stepName] ?? 0;
  if (_blendStep >= 0) {
    const prev = document.querySelectorAll('#blend-loading-steps .step-item')[_blendStep];
    if (prev) { prev.classList.remove('active'); prev.classList.add('done'); prev.querySelector('.material-symbols-outlined').textContent = 'check_circle'; prev.querySelector('.material-symbols-outlined').style.color = '#43fe6d'; }
  }
  const li = document.querySelectorAll('#blend-loading-steps .step-item')[idx];
  if (li) li.classList.add('active');
  _blendStep = idx;
}

function finishBlendSteps() {
  if (_blendStep >= 0) {
    const li = document.querySelectorAll('#blend-loading-steps .step-item')[_blendStep];
    if (li) { li.classList.remove('active'); li.classList.add('done'); li.querySelector('.material-symbols-outlined').textContent = 'check_circle'; li.querySelector('.material-symbols-outlined').style.color = '#43fe6d'; }
  }
}

function _startBlendFact() {
  const item = _nextFact();
  $('bfact-badge').textContent  = item.type === 'quote' ? '❝ Söz' : '🎬 Bilgi';
  $('bfact-text').textContent   = item.text;
  $('bfact-author').textContent = item.author ? `— ${item.author}` : '';
}

function buildBlendFilmCard(film, idx, username1 = '', username2 = '') {
  const title = escapeHTML(film.title);
  const director = escapeHTML(film.director);
  const year = escapeHTML(film.year);
  const posterURL = safeImageURL(film.poster_url);
  const href = letterboxdFilmURL(film.slug);
  const poster = posterURL
    ? `<img alt="${title}" draggable="false" loading="lazy"
          class="w-full h-full object-cover group-hover:scale-[1.04] transition-transform duration-500"
          src="${posterURL}"/>`
    : `<div class="w-full h-full flex items-center justify-center bg-surface-container"><span class="material-symbols-outlined text-[40px] text-on-surface-variant/20">movie</span></div>`;
  const preferenceLine = (username, rating, favorite) => {
    const hasRating = rating !== null && rating !== undefined;
    if (!hasRating && !favorite) return '';
    const favoriteLabel = favorite === 'fav4' ? 'Fav 4' : favorite === 'top10' ? 'Fav 10' : '';
    return `<span class="flex min-w-0 items-center justify-between gap-1 text-[10px] leading-tight text-on-surface-variant/75">
      <span class="truncate" title="@${escapeHTML(username)}">@${escapeHTML(username)}</span>
      <strong class="shrink-0 text-primary-container">${hasRating ? `${Number(rating).toFixed(1)}★` : ''}${hasRating && favoriteLabel ? ' · ' : ''}${favoriteLabel}</strong>
    </span>`;
  };
  const preferences = [
    preferenceLine(username1, film.rating1, film.favorite1),
    preferenceLine(username2, film.rating2, film.favorite2),
  ].filter(Boolean).join('');
  const card = `
    <article class="tilt-card glass-panel h-full rounded-xl overflow-hidden group flex flex-col overflow-safe"
      style="opacity:0;animation:blend-card-in .5s cubic-bezier(.22,1,.36,1) both;animation-delay:${idx * 80}ms">
      <div class="w-full aspect-[2/3] overflow-hidden relative bg-surface-container shrink-0">
        ${poster}
        <div class="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-surface-container-lowest/80 to-transparent pointer-events-none"></div>
      </div>
      <div class="p-stack-sm flex flex-col gap-unit flex-grow">
        <h4 class="font-label-md text-label-md text-on-surface line-clamp-2 leading-snug">${title}${film.year ? ` <span class="text-on-surface-variant/60">(${year})</span>` : ''}</h4>
        ${film.director ? `<span class="font-label-sm text-label-sm text-on-surface-variant/70">${director}</span>` : ''}
        ${preferences ? `<div class="mt-1 flex flex-col gap-1 border-t border-outline-variant/15 pt-2">${preferences}</div>` : ''}
      </div>
    </article>`;
  return href
    ? `<a href="${href}" target="_blank" rel="noopener" class="block h-full" title="${title} — Letterboxd">${card}</a>`
    : card;
}

async function renderBlendResult(data) {
  _currentBlendResult = {
    ...data,
    films: data.films || [],
    common_watchlist_films: data.common_watchlist_films || [],
    bridge_films: data.bridge_films || [],
  };
  const { username1, username2, score, watched_count1, watched_count2,
          common_count, top_director,
          top_director_count1, top_director_count2, films,
          common_watchlist_films = [], bridge_films = [], watchlist_public = false, watchlist_pending = false,
          confidence = { level: 'low', score: 0, sample_size: 0, rating_pairs: 0 } } = data;

  const info = getScoreInfo(score);

  // User bubbles
  $('br-init1').textContent = username1[0].toUpperCase();
  $('br-name1').textContent = '@' + username1;
  $('br-init2').textContent = username2[0].toUpperCase();
  $('br-name2').textContent = '@' + username2;
  setImage($('br-avatar1'), $('br-init1'), data.avatar_url1, username1);
  setImage($('br-avatar2'), $('br-init2'), data.avatar_url2, username2);

  // Ring color
  $('br-ring').setAttribute('stroke', info.color);
  $('br-svg').style.setProperty('--ring-color', info.color);

  // Stats
  $('br-common-count').textContent = common_count;
  $('br-scan-count').textContent = watched_count1 + watched_count2;
  if (top_director) {
    $('br-director').textContent = top_director;
    $('br-director-counts').textContent = `${username1}: ${top_director_count1} · ${username2}: ${top_director_count2}`;
    $('br-director-card').classList.remove('hidden');
    $('br-director-card').classList.add('flex');
  }

  // Background gradient
  $('blend-bg').style.background = [
    `radial-gradient(circle at 15% 25%, ${info.bg}0.06) 0%, transparent 45%)`,
    `radial-gradient(circle at 85% 75%, ${info.bg}0.04) 0%, transparent 40%)`,
  ].join(',');

  // Ortak izlenen filmler
  if (films && films.length > 0) {
    $('br-grid').innerHTML = films.map((film, idx) => buildBlendFilmCard(film, idx, username1, username2)).join('');
    $('br-films-section').classList.remove('hidden');
    $('br-films-section').classList.add('flex');
    $('br-no-common').classList.add('hidden');
  } else {
    $('br-films-section').classList.add('hidden');
    $('br-no-common').classList.remove('hidden');
  }

  // Ortak watchlist ana skordan bağımsız yüklenir.
  if (watchlist_pending) {
    $('br-wishlist-loading').classList.remove('hidden');
    $('br-wishlist-section').classList.add('hidden');
    $('br-no-wishlist').classList.add('hidden');
    if (data.request_id) pollBlendWatchlist(data.request_id);
  } else {
    renderBlendWatchlist({ common_watchlist_films, bridge_films, watchlist_public });
  }

  showView('blend-result');

  // Animation sequence
  // Phase 1 (0ms): User bubbles fly in
  $('br-user1').classList.remove('opacity-0');
  $('br-user1').classList.add('blend-bubble-l');
  $('br-user2').classList.remove('opacity-0');
  $('br-user2').classList.add('blend-bubble-r');

  // Phase 2 (700ms): Score ring draws + number counts
  await new Promise(r => setTimeout(r, 700));
  animateScore(score, $('br-ring'), $('br-score'));
  $('br-ring').classList.add('ring-glow');

  // Phase 3 (1200ms): Score label + stats
  await new Promise(r => setTimeout(r, 500));
  $('br-label').textContent = info.label;
  $('br-label').classList.remove('opacity-0');
  $('br-label').classList.add('blend-fade-up');
  const confidenceLabels = { high: 'Yüksek', medium: 'Orta', low: 'Düşük' };
  $('br-confidence').textContent = `${confidenceLabels[confidence.level] || 'Düşük'} veri kapsamı · %${confidence.score} · ${confidence.sample_size} film/kişi`;
  $('br-confidence').classList.remove('opacity-0');
  $('br-confidence').classList.add('blend-fade-up');
  await new Promise(r => setTimeout(r, 150));
  $('br-stats').classList.remove('opacity-0');
  $('br-stats').classList.add('blend-fade-up');
}

let _blendWatchlistPollToken = 0;
async function pollBlendWatchlist(requestId) {
  const token = ++_blendWatchlistPollToken;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 4000));
    if (token !== _blendWatchlistPollToken || $('view-blend-result').classList.contains('hidden')) return;
    try {
      const data = await apiJSON(`/api/blends/requests/${encodeURIComponent(requestId)}/result`);
      if (data.result && !data.result.watchlist_pending) {
        renderBlendWatchlist(data.result);
        return;
      }
    } catch (_) { /* transient; the persisted task is safely retryable */ }
  }
}

function renderBlendWatchlist(payload = {}) {
  const { common_watchlist_films = [], bridge_films = [], watchlist_public = false } = payload;
  if (_currentBlendResult) {
    _currentBlendResult = {
      ..._currentBlendResult,
      common_watchlist_films,
      bridge_films,
      watchlist_public,
      watchlist_pending: false,
    };
  }
  $('br-wishlist-loading').classList.add('hidden');
  const title = $('br-wishlist-title');
  const sub = $('br-wishlist-sub');
  const show = (films) => {
    $('br-wishlist-grid').innerHTML = films.map((film, idx) => buildBlendFilmCard(film, idx)).join('');
    $('br-wishlist-section').classList.remove('hidden');
    $('br-wishlist-section').classList.add('flex');
    $('br-no-wishlist').classList.add('hidden');
  };
  // The backend fills missing common-watchlist slots with taste-matched bridge
  // picks. Render both pools together; otherwise a four-film common list would
  // hide the fifth recommendation that was already computed and persisted.
  const common = Array.isArray(common_watchlist_films) ? common_watchlist_films : [];
  const bridge = Array.isArray(bridge_films) ? bridge_films : [];
  const combined = [...common, ...bridge].slice(0, 5);
  if (combined.length > 0 && common.length > 0) {
    title.textContent = 'Birlikte İzlemek İstedikleriniz';
    if (bridge.length > 0) {
      sub.textContent = `${common.length} ortak film ve zevklerinizi buluşturan ${combined.length - common.length} öneri.`;
      sub.classList.remove('hidden');
    } else {
      sub.classList.add('hidden');
      sub.textContent = '';
    }
    show(combined);
  } else if (combined.length > 0) {
    title.textContent = `Sizi Birleştirecek ${combined.length} Film`;
    sub.textContent = 'Watchlist’lerinizde ortak film çıkmadı — ikinizin zevkini buluşturacak, henüz kimsenin izlemediği filmler.';
    sub.classList.remove('hidden');
    show(combined);
  } else {
    $('br-wishlist-section').classList.add('hidden');
    $('br-no-wishlist').classList.remove('hidden');
  }
}

// ── Sinefil Sineması ──────────────────────────────────────────────────────
let _sinefilSearchTimer = null;
let _activeSinefilProfile = null;
let _sinefilProfiles = [];
let _sinefilPage = 1;
const _sinefilPerPage = 12;

function sinefilMessage(kind, message = '') {
  const el = $(`sinefil-${kind}`);
  if (!message) { el.textContent = ''; el.classList.add('hidden'); return; }
  el.textContent = message;
  el.classList.remove('hidden');
}

function sinefilCard(profile) {
  const username = escapeHTML(profile.username || '');
  const name = escapeHTML(profile.display_name || profile.username || '');
  const avatar = safeImageURL(profile.avatar_url);
  const initial = escapeHTML((profile.display_name || profile.username || '?')[0].toUpperCase());
  const favorites = (profile.favorites || []).slice(0, 4);
  const posters = favorites.map(film => {
    const poster = safeImageURL(film.poster_url);
    const title = escapeHTML(film.title || 'Film');
    return poster
      ? `<img src="${poster}" alt="${title}" class="aspect-[2/3] w-full rounded-lg object-cover bg-surface-variant" loading="lazy"/>`
      : `<div class="flex aspect-[2/3] items-center justify-center rounded-lg bg-surface-variant p-2 text-center font-label-sm text-[9px] text-on-surface-variant">${title}</div>`;
  }).join('') || '<div class="col-span-4 py-5 text-center text-sm text-on-surface-variant">Fav 4 henüz hazır değil.</div>';
  // Profil fotoğrafı ve ad, profilin kendisine götürür: ayrı bir "görüntüle"
  // düğmesine gerek yok.
  const head = `<button type="button" data-sinefil-profile="${username}" class="flex min-w-0 items-center gap-3 text-left">
      ${avatar
        ? `<img src="${avatar}" alt="${name}" class="h-12 w-12 shrink-0 rounded-full object-cover ring-1 ring-tertiary-container/35"/>`
        : `<span class="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-tertiary-container/15 font-headline-md text-tertiary-container">${initial}</span>`}
      <span class="min-w-0">
        <strong class="block truncate font-headline-md text-[18px] text-on-surface">@${username}</strong>
        <span class="mt-0.5 flex items-center gap-1 truncate text-sm text-on-surface-variant">${name}${profile.private_account ? '<span class="material-symbols-outlined text-[15px]" title="Kilitli hesap">lock</span>' : ''}</span>
      </span>
    </button>`;
  return `<article data-sinefil-profile="${username}" role="link" tabindex="0" class="cursor-pointer rounded-2xl border border-outline-variant/25 bg-surface-container p-4 shadow-xl transition-colors hover:border-tertiary-container/40">
    <div class="flex items-start justify-between gap-3">${head}${followButton(profile)}</div>
    <div class="mt-4 grid grid-cols-4 gap-2">${posters}</div>
    <p class="mt-3 text-left font-label-sm text-label-sm text-tertiary-container">${escapeHTML(profile.match_note || 'Zevk haritalarınız yakın')}</p>
  </article>`;
}


// Page numbers are windowed so the row always fits: five on a phone, ten on a
// wider screen. The window slides with the current page (page 4 of many shows
// 2 3 4 5 6) and clamps at both ends.
const _SINEFIL_WIDE_MQ = window.matchMedia('(min-width: 640px)');
const _SINEFIL_PAGE_BUTTONS_NARROW = 5;
const _SINEFIL_PAGE_BUTTONS_WIDE = 10;
let _sinefilPagination = null;

function _sinefilPageWindow(page, pages) {
  const size = Math.min(
    _SINEFIL_WIDE_MQ.matches ? _SINEFIL_PAGE_BUTTONS_WIDE : _SINEFIL_PAGE_BUTTONS_NARROW,
    pages,
  );
  const start = Math.min(
    Math.max(1, page - Math.floor((size - 1) / 2)),
    pages - size + 1,
  );
  return Array.from({ length: size }, (_, index) => start + index);
}

function renderSinefilPagination(pagination = {}) {
  _sinefilPagination = pagination;
  const nav = $('sinefil-pagination');
  const pages = Math.max(1, Number(pagination.pages) || 1);
  const page = Math.min(pages, Math.max(1, Number(pagination.page) || 1));
  const hasResults = Number(pagination.total) > 0;
  nav.innerHTML = '';
  nav.classList.toggle('hidden', !hasResults);
  nav.classList.toggle('flex', hasResults);
  if (!hasResults) return;
  const button = (label, target, disabled = false, current = false) => `<button type="button" data-sinefil-page="${target}"${disabled ? ' disabled' : ''}${current ? ' aria-current="page"' : ''} class="inline-flex min-h-[44px] min-w-[38px] sm:min-w-[44px] items-center justify-center rounded-xl border px-2 sm:px-3 text-sm transition-colors ${current ? 'border-tertiary-container/60 bg-tertiary-container/15 text-tertiary-container' : 'border-outline-variant/25 text-on-surface-variant hover:border-tertiary-container/45 hover:text-tertiary-container'} disabled:cursor-not-allowed disabled:opacity-35">${label}</button>`;
  nav.insertAdjacentHTML('beforeend', button('<span class="material-symbols-outlined text-[18px]">chevron_left</span>', page - 1, page === 1));
  for (const number of _sinefilPageWindow(page, pages)) {
    nav.insertAdjacentHTML('beforeend', button(String(number), number, false, number === page));
  }
  nav.insertAdjacentHTML('beforeend', button('<span class="material-symbols-outlined text-[18px]">chevron_right</span>', page + 1, page === pages));
}

// Rotating the phone (or resizing a window) changes how many fit.
_SINEFIL_WIDE_MQ.addEventListener('change', () => {
  if (_sinefilPagination) renderSinefilPagination(_sinefilPagination);
});

async function loadSinefilArea(page = 1) {
  _sinefilPage = Math.max(1, Number(page) || 1);
  sinefilMessage('error');
  const query = $('sinefil-search').value.trim();
  $('sinefil-grid').innerHTML = '<div class="col-span-full py-12 text-center text-on-surface-variant">Sinefiller aranıyor…</div>';
  $('sinefil-pagination').classList.add('hidden');
  $('sinefil-pagination').classList.remove('flex');
  try {
    const data = await apiJSON(`/api/sinefil-alani?q=${encodeURIComponent(query)}&page=${_sinefilPage}&per_page=${_sinefilPerPage}`);
    const profiles = data.profiles || [];
    _sinefilProfiles = profiles;
    const pagination = data.pagination || { page: _sinefilPage, pages: profiles.length === _sinefilPerPage ? _sinefilPage + 1 : 1, per_page: _sinefilPerPage, total: profiles.length };
    _sinefilPage = Number(pagination.page) || _sinefilPage;
    if (!profiles.length) {
      $('sinefil-grid').innerHTML = '<div class="col-span-full rounded-2xl border border-dashed border-outline-variant/30 p-10 text-center text-on-surface-variant">Henüz gösterilecek sinefil yok. Yeni kayıtlar burada belirecek.</div>';
      renderSinefilPagination({ pages: 1, page: 1, total: 0 });
      return;
    }
    sinefilMessage('notice');
    $('sinefil-grid').innerHTML = profiles.map(sinefilCard).join('');
    renderSinefilPagination(pagination);
  } catch (error) {
    $('sinefil-grid').innerHTML = '';
    renderSinefilPagination({ pages: 1, page: 1 });
    sinefilMessage('error', error.message || 'Sinefil Sineması yüklenemedi.');
  }
}

async function requestSinefilBlend(username, button) {
  button.disabled = true;
  try {
    const data = await apiJSON('/api/blends/requests', {
      method: 'POST', headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ recipient_username: username }),
    });
    if (data.existing) { await routeToExistingBlend(data); return; }
    button.textContent = 'İstek gönderildi';
    sinefilMessage('notice', `@${data.recipient_username} kullanıcısına Blend isteği gönderildi.`);
  } catch (error) {
    sinefilMessage('error', error.message || 'Blend isteği gönderilemedi.');
  } finally { button.disabled = false; }
}

// ── Blend SSE flow ──────────────────────────────────────────────────────────
let _blendSearchTimer = null;

async function searchBlendUsers(inputId = 'username2-input', panelId = 'blend-user-suggestions') {
  if (!_authEnabled) return;
  const query = $(inputId).value.trim().replace(/^@/, '').toLowerCase();
  if (query.length < 2) {
    $(panelId).classList.add('hidden');
    return;
  }
  try {
    const data = await apiJSON(`/api/users/search?q=${encodeURIComponent(query)}`);
    const users = data.users || [];
    if (!users.length) { $(panelId).classList.add('hidden'); return; }
    $(panelId).innerHTML = users.map(user => `<button type="button" data-blend-user="${escapeHTML(user.username)}" class="w-full px-4 py-3 flex items-center gap-3 hover:bg-surface-variant text-left border-b border-outline-variant/20 last:border-0"><strong class="text-on-surface">${escapeHTML(user.display_name || user.username)}</strong><span class="text-on-surface-variant text-sm">@${escapeHTML(user.username)}</span></button>`).join('');
    $(panelId).classList.remove('hidden');
  } catch (_) {
    $(panelId).classList.add('hidden');
  }
}

async function blendRequestFlow(opts = {}) {
  const inputId = opts.inputId || 'username2-input';
  const panelId = opts.suggestionsId || 'blend-user-suggestions';
  const notify = opts.onNotice || setIdleNotice;
  const fail = opts.onError || setIdleError;
  const recipient = $(inputId).value.trim();
  if (!recipient) { $(inputId).focus(); return; }
  notify(null);
  fail(null);
  const button = opts.button ? $(opts.button) : $('btn-recommend');
  if (button) button.disabled = true;
  try {
    const data = await apiJSON('/api/blends/requests', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ recipient_username: recipient }),
    });
    $(inputId).value = '';
    $(panelId).classList.add('hidden');
    if (data.existing) {
      await routeToExistingBlend(data);
      return;
    }
    notify(`@${data.recipient_username} kullanıcısına Blend isteği gönderildi.`);
    loadBlendInbox(false);
  } catch (error) {
    if ((error.code === 'recipient_not_found' || error.status === 404) && opts.onNotFound) {
      opts.onNotFound(recipient);
    } else {
      fail(error.message || 'Blend isteği gönderilemedi.');
    }
  } finally { if (button) button.disabled = false; }
}

async function blendFlow() {
  const u1 = $('username-input').value.trim();
  const u2 = $('username2-input').value.trim();
  if (!u1) { $('username-input').focus(); return; }
  if (!u2) { $('username2-input').focus(); return; }

  setIdleError(null);
  setIdleNotice(null);
  $('btn-recommend').disabled = true;

  // Set loading bubble initials
  $('bl-init1').textContent = u1[0].toUpperCase();
  $('bl-name1').textContent = u1;
  $('bl-init2').textContent = u2[0].toUpperCase();
  $('bl-name2').textContent = u2;

  resetBlendSteps();
  _startBlendFact();
  showView('blend-loading');

  // Reset blend result state for re-runs
  $('br-user1').className = 'opacity-0 flex flex-col items-center gap-2 min-w-[80px] md:min-w-[110px]';
  $('br-user2').className = 'opacity-0 flex flex-col items-center gap-2 min-w-[80px] md:min-w-[110px]';
  $('br-label').className = 'opacity-0 font-headline-md text-headline-md text-on-surface text-center';
  $('br-confidence').className = 'opacity-0 -mt-4 font-label-md text-label-md text-on-surface-variant text-center';
  $('br-stats').className = 'opacity-0 flex flex-wrap items-center justify-center gap-gutter';
  $('br-score').textContent = '0';
  $('br-ring').style.strokeDashoffset = '503';
  $('br-ring').classList.remove('ring-glow');
  $('br-director-card').classList.add('hidden');
  $('br-director-card').classList.remove('flex');

  const done = (errMsg) => {
    finishBlendSteps();
    if (errMsg) { showView('idle'); setIdleError(errMsg); }
    $('btn-recommend').disabled = false;
  };

  const attemptBlend = async () => {
    const apiRequest = beginApiRequest(240000);
    let receivedTerminalEvent = false;
    try {
      const resp = await fetch(`${API_BASE}/api/blend`, {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ username1: u1, username2: u2 }),
        signal: apiRequest.controller.signal,
      });
      await assertStreamResponse(resp);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      while (true) {
        const { done: streamDone, value } = await reader.read();
        if (streamDone) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let event;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }

          if (event.type === 'step') {
            onBlendStep(event.step);
          } else if (event.type === 'result') {
            receivedTerminalEvent = true;
            finishBlendSteps();
            $('btn-recommend').disabled = false;
            await renderBlendResult(event);
          } else if (event.type === 'watchlist_result') {
            renderBlendWatchlist(event);
          } else if (event.type === 'error') {
            receivedTerminalEvent = true;
            done(scrapeErrorMessage(event));
          }
        }
      }
      if (!receivedTerminalEvent) {
        done('Sunucu yanıtı tamamlanmadan bağlantı kapandı. Lütfen tekrar deneyin.');
      }
    } catch (error) {
      if (apiRequest.replaced || apiRequest.cancelled) return;
      const message = streamErrorMessage(
        error, apiRequest, 'Sunucuya ulaşılamadı. Birkaç dakika sonra tekrar deneyin.'
      );
      if (!message) return;
      done(message);
    } finally {
      finishApiRequest(apiRequest);
    }
  };
  await attemptBlend();
}

// ── Render random result ───────────────────────────────────────────────────
function renderRandomResult() {
  const film = _randomFilms[_randomAttempt];
  const total = _randomFilms.length;
  const current = _randomAttempt + 1;
  const remaining = total - current;

  $('random-result-username').textContent = '@' + $('username-input').value.trim();
  $('random-attempt-badge').textContent = `${current} / ${total}`;
  $('random-hero-card').innerHTML = buildRandomCard(film);

  const tryAgainBtn = $('btn-try-again');
  const infoEl = $('random-attempts-info');

  tryAgainBtn.disabled = false;
  infoEl.textContent = remaining > 0
    ? `Bu turdan ${remaining} film daha var.`
    : 'Bu tur bitti — beğenmediysen yeni bir tur çekelim.';

  showView('random-result');
}

// ── Random flow (SSE) ──────────────────────────────────────────────────────
async function randomFlow() {
  const username = $('username-input').value.trim();
  if (!username) { $('username-input').focus(); return; }

  setIdleError(null);
  setIdleNotice(null);
  $('btn-recommend').disabled = true;
  showView('loading');
  resetSteps();
  hideQueueInfo();
  startFactRotation();

  const done = (errMsg) => {
    stopFactRotation();
    finishSteps();
    if (errMsg) {
      returnToWatchTool();
      showActionError(errMsg);
    }
    $('btn-recommend').disabled = false;
  };

  const apiRequest = beginApiRequest(180000);
  let receivedTerminalEvent = false;
  try {
    const resp = await fetch(`${API_BASE}/api/random`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ username }),
      signal: apiRequest.controller.signal,
    });
    await assertStreamResponse(resp);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done: streamDone, value } = await reader.read();
      if (streamDone) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let event;
        try { event = JSON.parse(line.slice(6)); } catch { continue; }

        if (event.type === 'step') {
          hideQueueInfo();
          onStep(event.step);
        } else if (event.type === 'result') {
          receivedTerminalEvent = true;
          finishSteps();
          stopFactRotation();
          _randomFilms = event.films || [];
          _randomAttempt = 0;
          $('btn-recommend').disabled = false;
          if (_randomFilms.length === 0) {
            done('Şu an önerecek film bulamadık; biraz sonra tekrar dene.');
          } else {
            renderRandomResult();
          }
        } else if (event.type === 'error') {
          receivedTerminalEvent = true;
          done(scrapeErrorMessage(event));
        }
      }
    }
    if (!receivedTerminalEvent) {
      done('Sunucu yanıtı tamamlanmadan bağlantı kapandı. Lütfen tekrar deneyin.');
    }
  } catch (error) {
    if (apiRequest.replaced || apiRequest.cancelled) return;
    const message = streamErrorMessage(error, apiRequest, 'Sunucuya ulaşılamadı.');
    if (message) done(message);
  } finally {
    finishApiRequest(apiRequest);
  }
}

// ── Main recommend flow (SSE) ──────────────────────────────────────────────
async function tasteFlow() {
  const username = $('username-input').value.trim();
  if (!username) { $('username-input').focus(); return; }

  setIdleError(null);
  setIdleNotice(null);
  $('btn-recommend').disabled = true;
  showView('loading');
  resetSteps();
  hideQueueInfo();
  startFactRotation();

  const done = (errMsg) => {
    stopFactRotation();
    finishSteps();
    if (errMsg) {
      returnToWatchTool();
      showActionError(errMsg);
    }
    $('btn-recommend').disabled = false;
  };

  const apiRequest = beginApiRequest(180000);
  let receivedTerminalEvent = false;
  try {
    const resp = await fetch(`${API_BASE}/api/recommend`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ username }),
      signal: apiRequest.controller.signal,
    });
    await assertStreamResponse(resp);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done: streamDone, value } = await reader.read();
      if (streamDone) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let event;
        try { event = JSON.parse(line.slice(6)); } catch { continue; }

        if (event.type === 'queued') {
          showQueueInfo(event.ahead);
        } else if (event.type === 'step') {
          hideQueueInfo();
          onStep(event.step);
        } else if (event.type === 'result') {
          receivedTerminalEvent = true;
          finishSteps();
          stopFactRotation();
          renderResults(event);
          $('btn-recommend').disabled = false;
        } else if (event.type === 'error') {
          receivedTerminalEvent = true;
          done(scrapeErrorMessage(event));
        }
      }
    }
    if (!receivedTerminalEvent) {
      done('Sunucu yanıtı tamamlanmadan bağlantı kapandı. Lütfen tekrar deneyin.');
    }
  } catch (error) {
    if (apiRequest.replaced || apiRequest.cancelled) return;
    const message = streamErrorMessage(error, apiRequest, 'Sunucuya ulaşılamadı.');
    if (message) done(message);
  } finally {
    finishApiRequest(apiRequest);
  }
}

// ── Recommend dispatcher ───────────────────────────────────────────────────
function recommend() {
  if (currentMode === 'random') randomFlow();
  else if (currentMode === 'blend') {
    if (_authEnabled) blendRequestFlow();
    else blendFlow();
  }
  else tasteFlow();
}

async function deleteMyData() {
  const username = $('username-input').value.trim();
  showView(dashboardView());
  setIdleNotice(null);
  setIdleError(null);
  profileActionNotice(null);
  profileActionError(null);
  if (!username) {
    showActionError('Silmek istediğin Letterboxd kullanıcı adını önce yukarıya yaz.');
    $('username-input').focus();
    return;
  }
  const confirmed = window.confirm(
    _authEnabled
      ? `@${username.replace(/^@/, '')} hesabı ve saklanan tüm Movienotes verileri kalıcı olarak silinsin mi?`
      : `@${username.replace(/^@/, '')} için saklanan profil ve öneri cache'i silinsin mi? ` +
        'Yeni bir analiz başlatırsan public veriler tekrar oluşturulur.'
  );
  if (!confirmed) return;

  cancelActiveApiRequest();
  const button = $('btn-delete-data');
  button.disabled = true;
  try {
    const response = await fetch(`${API_BASE}/api/data`, {
      method: 'DELETE',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ username }),
    });
    let payload = {};
    try { payload = await response.json(); } catch { /* empty error response */ }
    if (!response.ok) {
      const retryAfter = response.headers.get('Retry-After');
      const suffix = retryAfter ? ` (${retryAfter} saniye sonra tekrar dene.)` : '';
      throw new Error((payload.detail || 'Veri silinemedi.') + suffix);
    }
    $('username-input').value = '';
    $('username2-input').value = '';
    if (_authEnabled) {
      _account = null;
      setAuthMode('login');
      showView('auth');
      setAuthMessage(`@${payload.username} hesabı ve saklanan veriler silindi.`);
    } else {
      setIdleNotice(`@${payload.username} için saklanan veriler silindi.`);
    }
  } catch (error) {
    showActionError(error.message || 'Veri silinemedi. Lütfen tekrar dene.');
  } finally {
    button.disabled = false;
  }
}

async function loginAccount(event) {
  event.preventDefault();
  const button = $('btn-login');
  button.disabled = true;
  setAuthMessage(null);
  try {
    const data = await apiJSON('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: $('login-username').value.trim(),
        password: $('login-password').value,
        // İşaretliyse oturum çıkış yapılana kadar sürer, değilse bir gün.
        remember: $('login-remember').checked,
      }),
    });
    $('login-password').value = '';
    enterApp(data.account);
  } catch (error) {
    setAuthMessage(error.message || 'Giriş yapılamadı.', true);
  } finally { button.disabled = false; }
}

// Letterboxd taraması Render'ın barındırma IP'sinden Cloudflare'e sık takılıyor;
// kod bunu backoff'lu tekrar denemeyle telafi ediyor ama bu bazen 20-30 saniye
// sürüyor. O süre boyunca ekranda tek bir statik mesaj kalırsa kullanıcı
// isteğin koptuğunu/donduğunu düşünüp vazgeçiyor. Bekleme uzarsa mesajı
// güncelleyip hâlâ çalıştığımızı gösteriyoruz.
function _scrapeWaitReassurance(startText) {
  setAuthMessage(startText);
  const timers = [
    setTimeout(() => setAuthMessage('Letterboxd yanıtı gecikti, hâlâ deniyoruz…'), 6000),
    setTimeout(() => setAuthMessage('Bu normalden uzun sürüyor ama vazgeçmedik, birkaç saniye daha bekle…'), 16000),
  ];
  return () => timers.forEach(clearTimeout);
}

function _challengeWaitReassurance() {
  setAuthMessage('Doğrulama kodu hazırlanıyor…');
  const timers = [
    setTimeout(() => setAuthMessage('Kod hazırlanıyor, birkaç saniye daha…'), 4000),
    setTimeout(() => setAuthMessage('Hesap bağlantısı gecikti ama hâlâ çalışıyor…'), 10000),
  ];
  return () => timers.forEach(clearTimeout);
}

async function startRegistration(event) {
  event.preventDefault();
  const password = $('register-password').value;
  if (password !== $('register-password-confirm').value) {
    setAuthMessage('Parolalar eşleşmiyor.', true);
    return;
  }
  const button = $('btn-register');
  button.disabled = true;
  const clearReassurance = _challengeWaitReassurance();
  try {
    _verification = await apiJSON('/api/auth/register/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: $('register-username').value.trim(),
        password,
        password_confirm: $('register-password-confirm').value,
      }),
    });
    // Bio doğrulaması bitince oturumu otomatik açmak için parolayı sakla.
    _pendingRegPassword = password;
    $('register-password').value = '';
    $('register-password-confirm').value = '';
    $('register-form').classList.add('hidden');
    $('register-form').classList.remove('flex');
    $('auth-tabs').classList.add('hidden');
    $('verification-code').textContent = _verification.verification_code;
    $('verify-panel').classList.remove('hidden');
    $('verify-panel').classList.add('flex');
    setAuthMessage('Kod 15 dakika geçerli. Bio’yu kaydettikten sonra kontrol et.');
  } catch (error) {
    setAuthMessage(error.message || 'Hesap oluşturulamadı.', true);
  } finally {
    clearReassurance();
    button.disabled = false;
  }
}

async function verifyRegistration() {
  if (!_verification) return;
  const button = $('btn-verify');
  button.disabled = true;
  const clearReassurance = _scrapeWaitReassurance('Letterboxd bio alanı kontrol ediliyor…');
  const username = _verification.username;
  try {
    const data = await apiJSON('/api/auth/register/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username,
        code: _verification.verification_code,
        ...(_pendingRegPassword ? { password: _pendingRegPassword } : {}),
      }),
    });
    clearReassurance();
    _verification = null;

    // Doğrulama ve oturum açma artık aynı istekte tamamlanıyor. Eski bir
    // sunucu yanıtı logged_in döndürmezse manuel giriş ekranına düş.
    if (data.logged_in) {
      _pendingRegPassword = null;
      setAuthMessage(null);
      enterApp(data.account, { fromRegistration: true });
      return;
    }
    _pendingRegPassword = null;
    setAuthMode('login');
    $('login-username').value = username;
    setAuthMessage('Hesap doğrulandı. Şimdi parolanla giriş yapabilirsin.');
  } catch (error) {
    setAuthMessage(error.message || 'Bio doğrulanamadı.', true);
  } finally {
    clearReassurance();
    button.disabled = false;
  }
}

async function startPasswordReset() {
  const button = $('btn-reset-start');
  button.disabled = true;
  setAuthMessage(null);
  try {
    _resetChallenge = await apiJSON('/api/auth/password-reset/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('reset-username').value.trim() }),
    });
    $('reset-code-display').textContent = _resetChallenge.verification_code;
    $('reset-finish-fields').classList.remove('hidden');
    $('reset-finish-fields').classList.add('flex');
    setAuthMessage('Kodu Letterboxd bio alanına ekle, sonra yeni parolanı kaydet.');
  } catch (error) {
    setAuthMessage(error.message || 'Sıfırlama başlatılamadı.', true);
  } finally { button.disabled = false; }
}

async function finishPasswordReset() {
  if (!_resetChallenge) return;
  const password = $('reset-password').value;
  if (password !== $('reset-password-confirm').value) {
    setAuthMessage('Parolalar eşleşmiyor.', true);
    return;
  }
  const button = $('btn-reset-finish');
  button.disabled = true;
  try {
    await apiJSON('/api/auth/password-reset/finish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: _resetChallenge.username,
        code: _resetChallenge.verification_code,
        new_password: password,
        new_password_confirm: $('reset-password-confirm').value,
      }),
    });
    const username = _resetChallenge.username;
    _resetChallenge = null;
    setAuthMode('login');
    $('login-username').value = username;
    setAuthMessage('Parolan değiştirildi. Yeni parolanla giriş yapabilirsin.');
  } catch (error) {
    setAuthMessage(error.message || 'Parola değiştirilemedi.', true);
  } finally { button.disabled = false; }
}

async function logoutAccount() {
  cancelActiveApiRequest();
  stopBlendBadgePolling();
  stopFeedNotificationPolling();
  try {
    await apiJSON('/api/auth/logout', { method: 'POST', headers: csrfHeaders() });
  } catch (_) {}
  _account = null;
  _persistedProfile = null;
  _pendingRegPassword = null;
  _recentLoaded = false;
  _topFilmsLoaded = false;
  _statsLoaded = false;
  _obToken += 1;
  _obClearTimers();
  $('ob-skip').classList.add('hidden');
  $('primary-username-field').classList.remove('hidden');
  $('username-input').value = '';
  renderBlendBadge(0);
  $('profile-settings-menu').classList.add('hidden');
  setAuthMode('login');
  showView('auth');
  loadPublicStats();
}

function toggleProfileMenu(force) {
  const menu = $('profile-settings-menu');
  const shouldOpen = typeof force === 'boolean' ? force : menu.classList.contains('hidden');
  menu.classList.toggle('hidden', !shouldOpen);
  $('profile-settings-btn').setAttribute('aria-expanded', String(shouldOpen));
}

function copyCode(element) {
  const code = element.textContent.trim();
  if (!code) return;
  navigator.clipboard?.writeText(code);
  setAuthMessage('Kod panoya kopyalandı.');
}

function openInfoDialog(id) {
  const dialog = $(id);
  if (dialog && !dialog.open) dialog.showModal();
}

// ── Event listeners ────────────────────────────────────────────────────────
$('login-form').addEventListener('submit', loginAccount);
$('register-form').addEventListener('submit', startRegistration);
$('auth-tab-login').addEventListener('click', () => setAuthMode('login'));
$('auth-tab-register').addEventListener('click', () => setAuthMode('register'));
$('btn-verify').addEventListener('click', verifyRegistration);
$('btn-verify-back').addEventListener('click', () => { _pendingRegPassword = null; setAuthMode('register'); });
$('verification-code').addEventListener('click', () => copyCode($('verification-code')));
$('reset-code-display').addEventListener('click', () => copyCode($('reset-code-display')));
$('btn-show-reset').addEventListener('click', () => {
  $('login-form').classList.add('hidden');
  $('auth-tabs').classList.add('hidden');
  $('reset-panel').classList.remove('hidden');
  $('reset-panel').classList.add('flex');
  $('reset-username').value = $('login-username').value;
  setAuthMessage(null);
});
$('btn-reset-back').addEventListener('click', () => setAuthMode('login'));
$('btn-reset-start').addEventListener('click', startPasswordReset);
$('btn-reset-finish').addEventListener('click', finishPasswordReset);
$('btn-logout').addEventListener('click', logoutAccount);
document.querySelectorAll('[data-password-toggle]').forEach(button => {
  button.addEventListener('click', () => {
    setPasswordVisibility(button, button.getAttribute('aria-pressed') !== 'true');
  });
});
$('header-how-it-works').addEventListener('click', () => openInfoDialog('dialog-how-it-works'));
$('header-privacy').addEventListener('click', () => openInfoDialog('dialog-privacy'));
document.querySelectorAll('[data-close-dialog]').forEach(button => {
  button.addEventListener('click', () => $(button.dataset.closeDialog)?.close());
});
[$('dialog-how-it-works'), $('dialog-privacy'), $('dialog-share'), $('dialog-top-films'), $('dialog-png-share'), $('dialog-letter-help'), $('dialog-letter-compose'), $('dialog-letter-followers'), $('dialog-install-app'), $('dialog-blocked-users'), $('dialog-profile-follows')].forEach(dialog => {
  dialog.addEventListener('click', event => {
    if (event.target === dialog) dialog.close();
  });
});
$('profile-my-notes').addEventListener('click', () => {
  showView('profile');
});
document.querySelectorAll('[data-profile-follows]').forEach(button => {
  button.addEventListener('click', () => openProfileFollows(button.dataset.profileFollows));
});
$('profile-follows-list').addEventListener('click', event => {
  const user = event.target.closest('[data-profile-follow-user]');
  if (!user) return;
  $('dialog-profile-follows').close();
  openUserPage(user.dataset.profileFollowUser, { from: 'profile' });
});
$('btn-install-app').addEventListener('click', requestMovienotesInstall);
$('btn-letter-compose-fab').addEventListener('click', openFollowerLetterPicker);
$('letter-followers-list').addEventListener('click', event => {
  const button = event.target.closest('[data-letter-follower]');
  if (!button) return;
  $('dialog-letter-followers').close();
  openLetterCompose(button.dataset.letterFollower);
});
$('profile-invite-friend').addEventListener('click', () => openShareSheet());
$('profile-sinefil-area').addEventListener('click', () => {
  showView('sinefil');
  loadSinefilArea();
});
$('profile-private-toggle').addEventListener('click', togglePrivateAccount);
$('profile-browser-notifications').addEventListener('click', enableBrowserNotifications);
$('menu-blocked-users').addEventListener('click', openBlockedUsers);
$('btn-sinefil-back').addEventListener('click', () => showView(homeView()));
$('sinefil-search').addEventListener('input', () => {
  clearTimeout(_sinefilSearchTimer);
  _sinefilSearchTimer = setTimeout(() => loadSinefilArea(1), 280);
});
$('sinefil-pagination').addEventListener('click', event => {
  const button = event.target.closest('[data-sinefil-page]');
  if (!button || button.disabled) return;
  loadSinefilArea(Number(button.dataset.sinefilPage));
});
$('sinefil-grid').addEventListener('click', event => {
  const follow = event.target.closest('[data-follow]');
  if (follow) { toggleFollow(follow); return; }
  const profile = event.target.closest('[data-sinefil-profile]');
  if (profile) {
    // Profil bir kart değil, bir sayfa: notlar, takipçiler ve Fav 4 orada.
    openUserPage(profile.dataset.sinefilProfile, { from: 'sinefil' });
    return;
  }
  const blend = event.target.closest('[data-sinefil-blend]');
  if (blend) { requestSinefilBlend(blend.dataset.sinefilBlend, blend); return; }
  const letter = event.target.closest('[data-sinefil-letter]');
  if (letter) openLetterCompose(letter.dataset.sinefilLetter);
});
$('btn-share-personality').addEventListener('click', event => {
  buildAndOpenShareCard(event.currentTarget, shareCards => shareCards.renderProfileShareCard(_persistedProfile));
});
// Akış olayları. Film seçici mektuplardaki diyaloğun aynısı: yeni bileşen yok.
$('btn-open-feed').addEventListener('click', openFeed);
$('btn-thread-back').addEventListener('click', () => {
  if (_threadFrom === 'notifications') { openNotifications(); return; }
  if (_threadFrom === 'user' && _userPage.username) {
    openUserPage(_userPage.username, { from: _userPage.from });
    return;
  }
  if (_threadFrom === 'sinefil') { showView('sinefil'); return; }
  openFeed();
});
$('btn-feed-more').addEventListener('click', () => loadFeed({ append: true }));
$('btn-feed-post').addEventListener('click', submitPost);
$('btn-thread-reply').addEventListener('click', submitReply);
$('feed-compose-text').addEventListener('input', event => {
  $('feed-compose-count').textContent = 420 - event.target.value.length;
});
startFeedComposerPromptFlow();
document.querySelectorAll('[data-feed-scope]').forEach(button => {
  button.addEventListener('click', () => setFeedScope(button.dataset.feedScope));
});
$('feed-follow-filter').addEventListener('click', event => {
  const button = event.target.closest('[data-feed-author]');
  if (!button) return;
  _feedAuthor = button.dataset.feedAuthor || '';
  // "Tümü" filtreyi kaldırır, dolayısıyla şeridi de kapatır.
  if (!_feedAuthor) _feedFollowFilterOpen = false;
  renderFeedFollowingFilter();
  loadFeed();
});
function openFilmPicker(mode = 'compose') {
  _feedFilmPickerMode = mode;
  $('feed-film-search').value = '';
  $('feed-film-results').innerHTML = '';
  $('feed-film-dialog-kicker').textContent = mode === 'filter' ? 'Topluluk notları' : 'Not yazmadan önce';
  $('feed-film-dialog-title').textContent = mode === 'filter' ? 'Bir filmde ara' : 'Hangi film?';
  $('feed-film-search').placeholder = mode === 'filter'
    ? 'Toplulukta notu olan filmlerde ara'
    : 'İzlediğin filmlerde ara';
  $('dialog-feed-film').showModal();
  setTimeout(() => $('feed-film-search').focus(), 50);
}
$('feed-compose-film').addEventListener('click', openFilmPicker);
$('feed-compose-film-chip').addEventListener('click', event => {
  if (event.target.closest('#feed-compose-film-clear')) { clearComposerFilm(); return; }
  if (event.target.closest('#feed-compose-film-change')) openFilmPicker();
});

let _feedSearchTimer = null;
$('feed-film-search').addEventListener('input', () => {
  clearTimeout(_feedSearchTimer);
  _feedSearchTimer = setTimeout(searchFeedFilms, 250);
});
$('feed-film-results').addEventListener('click', event => {
  const button = event.target.closest('[data-feed-film-index]');
  if (!button) return;
  const films = $('feed-film-results')._feedFilms || [];
  const film = films[Number(button.dataset.feedFilmIndex)];
  if (!film) return;
  if (_feedFilmPickerMode === 'filter') {
    _feedScope = 'community';
    _feedAuthor = '';
    _feedFollowFilterOpen = false;
    document.querySelectorAll('[data-feed-scope]').forEach(tab => {
      tab.classList.toggle('is-active', tab.dataset.feedScope === 'community');
    });
    renderFeedFollowingFilter();
    $('feed-sort-note').classList.remove('hidden');
    openFilmFeed(film.slug, film.title);
  } else {
    _feedPickedFilm = film;
    renderComposerFilm();
  }
  $('dialog-feed-film').close();
  if (_feedFilmPickerMode !== 'filter') $('feed-compose-text').focus();
});

function handleFeedCardClick(event) {
  // An author's name or avatar opens their page from wherever it was clicked.
  const author = event.target.closest('[data-post-author]');
  if (author && author.dataset.postAuthor) {
    openUserPage(author.dataset.postAuthor, { from: _currentFeedOrigin() });
    return;
  }
  const follow = event.target.closest('[data-follow]');
  if (follow) { toggleFollow(follow); return; }
  const card = event.target.closest('[data-post-id]');
  if (!card) return;
  if (event.target.closest('[data-body-more]')) {
    const holder = event.target.closest('[data-post-body]');
    if (holder) {
      holder.querySelector('[data-body-short]')?.classList.add('hidden');
      holder.querySelector('[data-body-full]')?.classList.remove('hidden');
    }
    return;
  }
  if (event.target.closest('[data-reveal-spoiler]')) {
    const hidden = card.querySelector('[data-reveal-spoiler] + span');
    if (hidden) {
      hidden.classList.remove('hidden');
      event.target.closest('[data-reveal-spoiler]').remove();
    }
    return;
  }
  if (event.target.closest('[data-post-like]')) { togglePostLike(card); return; }
  if (event.target.closest('[data-post-report]')) { reportPost(card); return; }
  if (event.target.closest('[data-post-open]')) { openThread(card.dataset.postId); return; }
  if (event.target.closest('[data-post-delete]')) {
    if (!window.confirm('Bu not silinsin mi?')) return;
    apiJSON(`/api/posts/${encodeURIComponent(card.dataset.postId)}`, {
      method: 'DELETE', headers: csrfHeaders(),
    }).then(() => (showView('feed'), loadFeed())).catch(() => {});
    return;
  }
  // Anywhere else on the card opens the note, the way a tweet does. Links keep
  // their own behaviour, and a thread card does not re-open itself.
  if (!event.target.closest('a, button') && $('view-thread').classList.contains('hidden')) {
    openThread(card.dataset.postId);
  }
}

$('feed-list').addEventListener('click', event => {
  if (event.target.closest('#btn-feed-retry')) { loadFeed(); return; }
  handleFeedCardClick(event);
});
$('thread-root').addEventListener('click', handleFeedCardClick);
$('thread-replies').addEventListener('click', handleFeedCardClick);
$('user-posts').addEventListener('click', handleFeedCardClick);

// Where "geri" should land: the view the reader was actually looking at.
function _currentFeedOrigin() {
  const open = ['feed', 'user', 'notifications', 'sinefil', 'thread'].find(
    view => !$(`view-${view}`).classList.contains('hidden')
  );
  // A thread was itself opened from somewhere; inherit that rather than loop.
  return open === 'thread' ? _threadFrom || 'feed' : (open || 'feed');
}

$('feed-trending').addEventListener('click', event => {
  const trend = event.target.closest('[data-trend-film]');
  if (trend) openFilmFeed(trend.dataset.trendFilm, trend.dataset.trendTitle);
});
$('feed-film-filter').addEventListener('click', event => {
  if (event.target.closest('#feed-film-clear')) openFilmFeed('', '');
});
$('btn-feed-film-filter').addEventListener('click', () => {
  const menu = $('feed-filter-menu');
  const open = menu.classList.contains('hidden');
  menu.classList.toggle('hidden', !open);
  $('btn-feed-film-filter').setAttribute('aria-expanded', String(open));
});
$('feed-filter-menu').addEventListener('click', event => {
  const option = event.target.closest('[data-feed-filter-kind]');
  if (!option) return;
  $('feed-filter-menu').classList.add('hidden');
  $('btn-feed-film-filter').setAttribute('aria-expanded', 'false');
  if (option.dataset.feedFilterKind === 'film') { openFilmPicker('filter'); return; }
  setFeedScope('following', { openFollowFilter: true });
});

document.querySelectorAll('[data-nav]').forEach(button => {
  button.addEventListener('click', () => goNav(button.dataset.nav));
});
document.querySelectorAll('[data-nav-action]').forEach(button => {
  button.addEventListener('click', () => openQuickTool(button.dataset.navAction));
});
$('tab-tools-toggle').addEventListener('click', () => {
  openToolsDirectory();
});
$('tools-directory').addEventListener('click', event => {
  const button = event.target.closest('[data-tools-page]');
  if (!button) return;
  openQuickTool(button.dataset.toolsPage, { parent: 'tools' });
});
$('btn-tools-back').addEventListener('click', () => returnToToolParent('watch'));
$('nav-logo').addEventListener('click', () => goNav('feed'));
$('app-rail').addEventListener('click', event => {
  const trend = event.target.closest('[data-trend-film]');
  if (trend) { openFeed(); openFilmFeed(trend.dataset.trendFilm, trend.dataset.trendTitle); return; }
  const follow = event.target.closest('[data-follow]');
  if (follow) { toggleFollow(follow); return; }
  const person = event.target.closest('[data-post-author]');
  if (person && person.dataset.postAuthor) openUserPage(person.dataset.postAuthor, { from: 'feed' });
});
$('nav-account').addEventListener('click', () => {
  showView('profile');
  if (!_persistedProfile) loadProfile();
});
// Kalem bir açma-kapama düğmesi: ikinci dokunuş kutuyu geri kapatır. Yazılmış
// bir metin varsa kapatmak onu silmek olur; o yüzden dolu kutu açık kalır.
function toggleComposer() {
  const composer = $('feed-composer');
  const open = !composer.classList.contains('hidden');
  if (open) {
    if ($('feed-compose-text').value.trim() || _feedPickedFilm) {
      $('feed-compose-text').focus();
      return;
    }
    composer.classList.add('hidden');
    return;
  }
  composer.classList.remove('hidden');
  composer.scrollIntoView({ block: 'start', behavior: 'smooth' });
  setTimeout(() => $('feed-compose-text').focus(), 120);
}

// Telefonda ana ekran yalnız paylaşılmış notları gösterir; yazma kutusu kalem
// düğmesiyle açılır ve paylaştıktan sonra tekrar kapanır.
function closeComposerOnPhone() {
  if (window.matchMedia('(min-width: 640px)').matches) return;
  $('feed-composer').classList.add('hidden');
}

// Akışta değilsek önce akışa geçilir ve kutu açılır; akıştaysak kalem
// doğrudan açar/kapatır. (openFeed kutuyu kapattığı için sıra önemli.)
async function composeFromPencil() {
  if ($('view-feed').classList.contains('hidden')) {
    await openFeed();
    toggleComposer();
    return;
  }
  toggleComposer();
}

$('nav-compose').addEventListener('click', composeFromPencil);
$('btn-compose-fab').addEventListener('click', composeFromPencil);

$('btn-notifications-back').addEventListener('click', () => (showView('feed'), loadFeed()));
$('notifications-list').addEventListener('click', event => {
  const request = event.target.closest('[data-follow-request]');
  if (request) {
    request.disabled = true;
    apiJSON(`/api/users/${encodeURIComponent(request.dataset.followRequest)}/follow-request`, {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ decision: request.dataset.followDecision }),
    }).then(openNotifications).catch(error => window.alert(error.message || 'Takip isteği güncellenemedi.'));
    return;
  }
  const author = event.target.closest('[data-post-author]');
  if (author && author.dataset.postAuthor) {
    openUserPage(author.dataset.postAuthor, { from: 'notifications' });
    return;
  }
  const row = event.target.closest('[data-notification-thread]');
  if (row) openThread(row.dataset.notificationThread);
  const destination = event.target.closest('[data-notification-destination]');
  if (destination) goNav(destination.dataset.notificationDestination);
});

$('btn-feed-mine').addEventListener('click', () => {
  showView('profile');
  if (!_persistedProfile) loadProfile();
});
document.addEventListener('click', event => {
  const fold = event.target.closest('.fold-head, .fold-toggle');
  if (fold) {
    const section = fold.closest('[data-fold]');
    if (section) { toggleFold(section); return; }
  }
  // Her ekrandaki avatar aynı yere gider: kenar çubuğundaki "Profil" ile
  // başlıktaki düğmenin ikisi de panoyu açıyor.
  const button = event.target.closest('[data-open-profile]');
  if (button && _account) goNav('profile');
});
$('btn-header-profile').addEventListener('click', () => {
  showView('profile');
  if (!_persistedProfile) loadProfile();
});
$('btn-user-back').addEventListener('click', () => {
  const from = _userPage.from;
  if (from === 'notifications') { openNotifications(); return; }
  if (from === 'sinefil') { showView('sinefil'); return; }
  if (from === 'profile') { showView('profile'); return; }
  showView('feed');
  loadFeed();
});
$('btn-user-more').addEventListener('click', loadMoreUserPosts);
$('user-header').addEventListener('click', event => {
  const follow = event.target.closest('[data-follow]');
  if (follow) { toggleFollow(follow); return; }
  const letter = event.target.closest('[data-user-letter]');
  if (letter) { openLetterCompose(letter.dataset.userLetter); return; }
  const blend = event.target.closest('[data-user-blend]');
  if (blend) { requestSinefilBlend(blend.dataset.userBlend, blend); return; }
  const list = event.target.closest('[data-follows]');
  if (list) openFollows(_userPage.username, list.dataset.follows);
});
$('btn-follows-back').addEventListener('click', () => {
  openUserPage(_followsFrom || _userPage.username, { from: _userPage.from });
});
$('follows-list').addEventListener('click', event => {
  const follow = event.target.closest('[data-follow]');
  if (follow) { toggleFollow(follow); return; }
  const person = event.target.closest('[data-post-author]');
  if (person && person.dataset.postAuthor) openUserPage(person.dataset.postAuthor, { from: 'feed' });
});

$('bulletin-venue').addEventListener('change', () => { _bulletinExpanded = false; paintBulletin(); });
$('profile-bulletin').addEventListener('click', event => {
  if (event.target.closest('#bulletin-more')) {
    _bulletinExpanded = true;
    paintBulletin();
    return;
  }
  const venueButton = event.target.closest('[data-bulletin-venues]');
  if (venueButton) {
    openBulletinVenues(venueButton.dataset.bulletinVenues);
    return;
  }
  const nav = event.target.closest('[data-bulletin-nav]');
  if (nav) {
    const strip = $('bulletin-strip');
    // One screenful at a time, so the arrows track what is actually visible.
    if (strip) strip.scrollBy({ left: Number(nav.dataset.bulletinNav) * strip.clientWidth * 0.85, behavior: 'smooth' });
  }
});
$('profile-recent-share').addEventListener('click', event => {
  buildAndOpenShareCard(
    event.currentTarget,
    shareCards => shareCards.renderRecentFilmsShareCard(_recentFilms, _persistedProfile),
  );
});
$('btn-share-common').addEventListener('click', event => {
  buildAndOpenShareCard(event.currentTarget, shareCards => shareCards.renderBlendShareCard(_currentBlendResult, 'watched'));
});
$('btn-share-watchlist').addEventListener('click', event => {
  buildAndOpenShareCard(event.currentTarget, shareCards => shareCards.renderBlendShareCard(_currentBlendResult, 'watchlist'));
});
$('profile-top-films-edit').addEventListener('click', openTopFilmsEditor);
$('top-films-list').addEventListener('click', handleTopFilmsPick);
$('top-films-search').addEventListener('input', () => {
  clearTimeout(_topFilmsSearchTimer);
  _topFilmsSearchTimer = setTimeout(_searchTopFilms, 250);
});
$('top-films-save').addEventListener('click', saveTopFilms);
$('ob-skip').addEventListener('click', completeOnboarding);
$('ob-prev').addEventListener('click', () => _obRevealNav(-1));
$('ob-next').addEventListener('click', () => _obRevealNav(1));
document.addEventListener('keydown', event => {
  if (_obReveal && !$('view-onboarding').classList.contains('hidden')) {
    if (event.key === 'ArrowLeft') _obRevealNav(-1);
    else if (event.key === 'ArrowRight') _obRevealNav(1);
  }
});
$('profile-inbox').addEventListener('click', () => openLetterInbox());
$('profile-letter-toggle').addEventListener('click', event => toggleLetterReceiving(event.currentTarget));
$('profile-blends').addEventListener('click', () => loadMyBlends(true));
$('profile-settings-btn').addEventListener('click', event => {
  event.stopPropagation();
  toggleProfileMenu();
});
$('profile-settings-menu').addEventListener('click', event => event.stopPropagation());
$('profile-language-select').addEventListener('change', changeProfileLanguage);
$('profile-theme-toggle').addEventListener('click', () => {
  toggleProfileMenu(false);
  toggleProfileTheme();
});
$('menu-delete-data').addEventListener('click', () => {
  toggleProfileMenu(false);
  deleteMyData();
});
document.addEventListener('click', event => {
  toggleProfileMenu(false);
});
$('btn-profile-sync').addEventListener('click', () => syncProfile(false, true));
$('btn-profile-back').addEventListener('click', () => showView(homeView()));
$('btn-inbox-back').addEventListener('click', () => showView(homeView()));
window.addEventListener('resize', () => {
  if (!$('view-inbox').classList.contains('hidden')) renderLetterWorkspace();
});
$('btn-letter-help').addEventListener('click', () => $('dialog-letter-help').showModal());
if ($('btn-letter-receiving')) $('btn-letter-receiving').addEventListener('click', toggleLetterReceiving);

// Opening the letterbox straight from the prompt, then continuing to the letter
// the member came to write.
$('btn-letter-enable-confirm').addEventListener('click', async () => {
  const username = _letterEnablePendingRecipient;
  const button = $('btn-letter-enable-confirm');
  button.disabled = true;
  try {
    const data = await apiJSON('/api/letters/receiving', {
      method: 'POST', headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ enabled: true }),
    });
    if (_account) _account.letter_receiving_enabled = data.letter_receiving_enabled;
    renderLetterSettings();
    _letterEnablePendingRecipient = null;
    $('dialog-letter-enable').close();
    if (username) openLetterCompose(username);
  } catch (error) {
    $('letter-enable-error').textContent = error.message || 'Mektup kutusu açılamadı.';
    $('letter-enable-error').classList.remove('hidden');
  } finally { button.disabled = false; }
});
$('letter-compose-form').addEventListener('submit', sendLetter);
$('letter-compose-body').addEventListener('input', () => { $('letter-compose-count').textContent = `${600 - $('letter-compose-body').value.length} karakter kaldı`; });
$('letter-film-search').addEventListener('input', () => { clearTimeout(_letterSearchTimer); _letterSearchTimer = setTimeout(searchLetterFilms, 250); });
$('letter-film-results').addEventListener('click', event => {
  const pick = event.target.closest('[data-letter-film-index]');
  if (!pick) return;
  _letterPickedFilm = $('letter-film-results')._letterFilms?.[Number(pick.dataset.letterFilmIndex)] || null;
  $('letter-film-results').innerHTML = '';
  $('letter-film-search').value = '';
  renderPickedLetterFilm();
});
$('letter-film-picked').addEventListener('click', event => { if (event.target.closest('[data-letter-film-clear]')) { _letterPickedFilm = null; renderPickedLetterFilm(); } });
$('btn-blends-refresh').addEventListener('click', () => loadMyBlends(false));
$('btn-blends-back').addEventListener('click', () => returnToToolParent('blend'));

$('profile-top-films').addEventListener('click', handleFilmDeck);
$('profile-recent-films').addEventListener('click', handleFilmDeck);
attachProfileCarousel($('profile-directors'), delta => _directorNav(delta));
attachProfileCarousel($('profile-top-films'), delta => _deckNav('profile-top-films', delta));
attachProfileCarousel($('profile-recent-films'), delta => _deckNav('profile-recent-films', delta));

async function loadAllDirectorFilms(rank, films, trigger) {
  if (!films || films.dataset.fullLoaded === 'true') return;
  const loading = films.querySelector('[data-director-loading]');
  if (loading) loading.textContent = 'Filmler yükleniyor…';
  if (trigger) trigger.disabled = true;
  try {
    const allFilms = [];
    let offset = 0;
    let hasMore = true;
    while (hasMore) {
      const data = await apiJSON(`/api/profile/directors/${rank}/films?limit=100&offset=${offset}`);
      const batch = data.films || [];
      allFilms.push(...batch);
      offset += batch.length;
      hasMore = Boolean(data.has_more) && batch.length > 0;
    }
    const grid = films.querySelector('.grid');
    if (grid) grid.innerHTML = allFilms.map(directorFilmTile).join('');
    films.dataset.fullLoaded = 'true';
    if (loading) loading.remove();
    if (trigger?.dataset.dirLoadRank) trigger.remove();
  } catch (error) {
    if (loading) loading.textContent = error.message || 'Filmler yüklenemedi; tekrar dene.';
  } finally {
    if (trigger?.isConnected) trigger.disabled = false;
  }
}

$('profile-directors').addEventListener('click', event => {
  const nav = event.target.closest('[data-director-nav]');
  if (nav) {
    _directorNav(Number(nav.dataset.directorNav));
    return;
  }
  const trigger = event.target.closest('[data-dir-load-rank]');
  if (!trigger) return;
  loadAllDirectorFilms(
    Number(trigger.dataset.dirLoadRank),
    $(trigger.dataset.dirGrid),
    trigger,
  );
});
$('profile-reco-body').addEventListener('click', event => {
  if (event.target.closest('#profile-reco-again')) {
    _clearTasteReco();
    startInlineReco('taste', { preserveViewport: true });
    return;
  }
  const nav = event.target.closest('[data-taste-nav]');
  if (nav) {
    const cur = _loadTasteReco();
    if (cur) _showTasteReco((cur.at || 0) + Number(nav.dataset.tasteNav));
    return;
  }
  if (event.target.closest('#profile-reco-totaste')) {
    setProfileWatchMode('taste');
    startInlineReco('taste');
    return;
  }
  if (event.target.closest('#profile-reco-torandom')) {
    setProfileWatchMode('random');
    startInlineReco('random');
    return;
  }
  if (event.target.closest('#profile-reco-reroll')) {
    setProfileWatchMode('random');
    startInlineReco('random', { preserveViewport: true });
    return;
  }
});

$('profile-watch-panel').addEventListener('click', event => {
  const button = event.target.closest('[data-watch-mode]');
  if (button) setProfileWatchMode(button.dataset.watchMode);
});
$('profile-watch-go').addEventListener('click', runProfileWatch);
$('profile-blend-go').addEventListener('click', runProfileBlend);
$('profile-blend-username').addEventListener('keydown', e => { if (e.key === 'Enter') runProfileBlend(); });
$('profile-blend-username').addEventListener('input', () => {
  clearTimeout(_profileBlendTimer);
  _profileBlendTimer = setTimeout(() => searchBlendUsers('profile-blend-username', 'profile-blend-suggestions'), 250);
});
$('profile-blend-suggestions').addEventListener('click', event => {
  const button = event.target.closest('[data-blend-user]');
  if (!button) return;
  $('profile-blend-username').value = button.dataset.blendUser;
  $('profile-blend-suggestions').classList.add('hidden');
});
$('view-inbox').addEventListener('click', handleBlendInboxAction);
$('view-blends').addEventListener('click', handleBlendInboxAction);
$('dialog-blocked-users').addEventListener('click', handleBlendInboxAction);
$('view-blends').addEventListener('keydown', event => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target.closest('[data-blend-action]')) {
    event.preventDefault();
    handleBlendInboxAction(event);
  }
});
$('btn-recommend').addEventListener('click', recommend);
$('btn-delete-data').addEventListener('click', deleteMyData);
$('username-input').addEventListener('keydown', e => { if (e.key === 'Enter') recommend(); });

$('btn-mode-taste').addEventListener('click', () => setMode('taste'));
$('btn-mode-random').addEventListener('click', () => setMode('random'));
$('btn-mode-blend').addEventListener('click', () => setMode('blend'));
$('username2-input').addEventListener('keydown', e => { if (e.key === 'Enter') recommend(); });
$('username2-input').addEventListener('input', () => {
  clearTimeout(_blendSearchTimer);
  _blendSearchTimer = setTimeout(searchBlendUsers, 250);
});
$('blend-user-suggestions').addEventListener('click', event => {
  const button = event.target.closest('[data-blend-user]');
  if (!button) return;
  $('username2-input').value = button.dataset.blendUser;
  $('blend-user-suggestions').classList.add('hidden');
});

$('btn-home').addEventListener('click', () => {
  cancelActiveApiRequest();
  showView(_authEnabled && !_account ? 'auth' : homeView());
  setIdleError(null);
  setIdleNotice(null);
});
$('btn-new-search').addEventListener('click', () => {
  cancelActiveApiRequest();
  returnToWatchTool();
  setIdleError(null);
  setIdleNotice(null);
});
$('btn-random-new-search').addEventListener('click', () => {
  cancelActiveApiRequest();
  returnToWatchTool();
  setIdleError(null);
  setIdleNotice(null);
});
$('btn-loading-back').addEventListener('click', () => {
  cancelActiveApiRequest();
  stopFactRotation();
  returnToWatchTool();
});
$('btn-try-again').addEventListener('click', () => {
  if (_randomAttempt < _randomFilms.length - 1) {
    _randomAttempt++;
    renderRandomResult();
    return;
  }
  // Batch exhausted — the pool is unlimited, so pull a fresh one.
  randomFlow();
});
$('btn-switch-to-taste').addEventListener('click', () => {
  setMode('taste');
  tasteFlow();
});
$('btn-blend-back').addEventListener('click', () => {
  cancelActiveApiRequest();
  returnToToolParent('blend');
  setIdleError(null);
  setIdleNotice(null);
});
$('btn-blend-loading-back').addEventListener('click', () => {
  cancelActiveApiRequest();
  returnToToolParent('blend');
});

// ── Boot ───────────────────────────────────────────────────────────────────
boot();
