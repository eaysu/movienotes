const LOCALE_KEY = 'mb_locale_preference';
const SUPPORTED = new Set(['tr', 'en']);

// Turkish remains the source language of the markup.  Keeping a single source
// document prevents the two shells from drifting; this dictionary translates
// both server-rendered nodes and nodes created later by the app.
const EN = Object.freeze({
  'Akış': 'Feed', 'Akışa dön': 'Back to feed', 'Ayarlar': 'Settings',
  'Bildirimler': 'Notifications', 'Mektuplar': 'Letters', 'Ne izlesem?': 'What should I watch?',
  'Blend': 'Blend', 'Profil': 'Profile', 'Profilim': 'My profile',
  'Not yaz': 'Write a note', 'Gizlilik': 'Privacy', 'Program nasıl çalışır?': 'How does it work?',
  'Giriş': 'Sign in', 'Giriş yap': 'Sign in', 'Girişe dön': 'Back to sign in',
  'Hesap oluştur': 'Create account', 'Hesabı oluştur': 'Create account',
  'Parolamı unuttum': 'Forgot password?', 'Parolayı değiştir': 'Change password',
  'Kod oluştur': 'Create code', 'Bio’yu kontrol et': 'Check bio', 'Geri dön': 'Back',
  'Kopyala': 'Copy', 'Kaydet': 'Save', 'Ekle': 'Add', 'Düzenle': 'Edit',
  'Vazgeç': 'Cancel', 'Paylaş': 'Share', 'Kapat': 'Close', 'Çıkış yap': 'Sign out',
  'Verimi sil': 'Delete my data', 'Verimi Sil': 'Delete my data',
  'Tarayıcı bildirimleri': 'Browser notifications', 'Engellediğin kullanıcılar': 'Blocked users',
  'Görünüm: Koyu': 'Appearance: Dark', 'Kilitli hesap: Kapalı': 'Private account: Off',
  'Kilitli hesap: Açık': 'Private account: On', 'Kapalı': 'Off', 'Açık': 'On',
  'Mektuplar:': 'Letters:', 'Mektup yaz': 'Write a letter', 'Mektubu gönder': 'Send letter',
  'Mektup kutun kapalı': 'Your letterbox is closed', 'Mektup kutunu açalım mı?': 'Open your letterbox?',
  'Sinefil Sineması': 'Cinephile Cinema', 'Sinefil ara': 'Search cinephiles',
  'Kullanıcı ara': 'Search users', 'Topluluk': 'Community', 'Takipçiler': 'Followers',
  'Takip': 'Following', 'Takip ettiklerin': 'Following', 'Seni takip edenler': 'People following you',
  'Takipleştiğin bir sinefille ortak izleme listenizi keşfedin.': 'Explore a shared watchlist with a cinephile you follow.',
  'Takipçine mektup yaz': 'Write to a follower', 'Gelen istekler': 'Incoming requests',
  'Gönderilenler': 'Sent', 'Blendlerim': 'My Blends', 'Blendler': 'Blends',
  'Blend alanı': 'Blend space', 'Rastgele': 'Random', 'Rastgele Öneri': 'Random pick',
  'Watchlist’inden zevkine uygun ya da rastgele bir seçim yap.': 'Pick a fitting or random film from your watchlist.',
  'Watchlist\'inden sürpriz bir film.': 'A surprise from your watchlist.',
  'Watchlist\'ler gizli veya ortak film yok.': 'Watchlists are private or have no shared films.',
  'Öner': 'Recommend', 'Öneri': 'Recommendation', 'Film ekle': 'Add a film',
  'Filme göre filtrele': 'Filter by film', 'Kullanıcıya göre filtrele': 'Filter by user',
  'Hangi film?': 'Which film?', 'Notlar': 'Notes', 'Notların': 'Your notes',
  'Cevapla': 'Reply', 'Daha fazla': 'More', 'Daha fazla göster': 'Show more',
  'Bu gece': 'Tonight', 'Bu akşam ne yapalım?': 'What should we do tonight?',
  'Bu hafta perdede': 'In theatres this week', 'Bu film nerede oynuyor': 'Where is this film showing?',
  'Tüm sinemalar': 'All cinemas', 'Sinema gündemi': 'Cinema guide', 'Sinema araçları': 'Cinema tools',
  'Profil kartını paylaş': 'Share profile card', 'PNG paylaş': 'Share PNG', 'PNG indir': 'Download PNG',
  'PNG hazır': 'PNG ready', 'Paylaşım kartı': 'Share card',
  'Profil ve watchlist taranıyor...': 'Scanning profile and watchlist...',
  'Film bilgileri zenginleştiriliyor...': 'Enriching film details...',
  'Zevk profili oluşturuluyor...': 'Building your taste profile...',
  'En iyi filmler seçiliyor...': 'Selecting the best films...',
  'Tüm izleme geçmişin analiz ediliyor': 'Your full viewing history is being analysed',
  'Son güncelleme': 'Last updated', 'film': 'films', 'g': 'd',
  'Tür sinyali henüz yeterli değil.': 'There is not enough genre signal yet.',
  'Yönetmen sıralaması için birkaç film bilgisinin daha tamamlanması gerekiyor.': 'A few more film details are needed to rank directors.',
  'Zevk profili hazırlanıyor…': 'Your taste profile is being prepared…',
  'İzleme geçmişin ve Fav 4 filmlerin analiz ediliyor…': 'Your viewing history and Fav 4 films are being analysed…',
  'Yönetmen bilgileri tamamlandıkça burada görünecek.': 'This will appear as director details are completed.',
  'Profil senkronu tamamlanamadı. Yenile düğmesiyle tekrar deneyebilirsin.': 'Profile sync could not finish. Try again with the refresh button.',
  'Profil senkronu tamamlanamadı.': 'Profile sync could not finish.',
  'Kayıtlı bir kullanıcıya onay isteği gönder.': 'Send a request to a registered user for approval.',
  'İzleme geçmişin tamamlandıkça bu alan sinema alışkanlıklarını daha ayrıntılı anlatacak.': 'As your viewing history grows, this space will describe your cinema habits in more detail.',
  'İzleme sıklığın ve verdiğin puanlarla öne çıkıyor.': 'This director stands out through how often you watch them and the ratings you give.',
  'Yeterli yönetmen verisi oluştuğunda burada görünecek.': 'This will appear once there is enough director data.',
  'Film zevkiniz benziyor': 'Your film tastes are similar',
  'Henüz ortak izlenmiş film bulunamadı.': 'No shared watched film has been found yet.',
  'Ortak izlenmek istenen film bulunamadı.': 'No shared watchlist film has been found.',
  'Ortak filmler ve uyum hesaplanıyor...': 'Calculating shared films and compatibility…',
  'İki profil taranıyor...': 'Scanning two profiles…',
  'Tüm geçmiş analiz edildi': 'Full history analysed',
  'Geçmiş taraması tamamlanmadan devam edilmeyecek.': 'We will continue once the history scan finishes.',
  'Bu hesap kilitli. Notlarını ve zevk profilini görmek için takip isteğinin kabul edilmesini bekle.': 'This account is private. Wait for your follow request to be accepted to see its notes and taste profile.',
  'Kullanıcı adı veya parola hatalı.': 'Incorrect username or password.',
  'Oturum geçersiz.': 'Your session is no longer valid.',
  'Oturum yenilenemedi.': 'Your session could not be renewed.',
  'Parola en az 10 karakter olmalı.': 'Password must be at least 10 characters.',
  'Parolalar eşleşmiyor.': 'Passwords do not match.',
  'Letterboxd erişimi geçici olarak engelledi. Birkaç dakika sonra tekrar dene.': 'Letterboxd temporarily blocked access. Please try again in a few minutes.',
  'Zevk analizin arka planda hazırlanıyor…': 'Your taste analysis is being prepared in the background…',
  'İzleme geçmişin analiz ediliyor…': 'Your viewing history is being analysed…',
  'Tüm geçmiş': 'Full history', 'Hesap özeti': 'Account summary', 'Analiz': 'Analysis',
  'Zevk Analizi': 'Taste analysis', 'Auteur radar': 'Auteur radar', 'Fav 4': 'Fav 4',
  'Başucu filmleri': 'Go-to films', 'Son filmler': 'Recent films', 'Bu yıl izlenen': 'Watched this year',
  'Puanlanan film': 'Rated films', 'İzlenen film': 'Watched films', 'En sevilen türler': 'Favourite genres',
  'Favori yönetmenlerin': 'Your favourite directors', 'Henüz belirleniyor': 'Still figuring it out',
  'Profili güncelle': 'Refresh profile', 'Profile dön': 'Back to profile',
  'Letterboxd bağlı': 'Letterboxd connected', 'Sosyal alan': 'Social space',
  'Kısayollar': 'Shortcuts', 'Kullanım Koşulları': 'Terms of use', 'API Durumu': 'API status',
  'Uygulamayı yükle': 'Install app', 'Movienotes’u yükle': 'Install Movienotes',
  'Uygulamaya geç': 'Open app', 'Şimdi değil': 'Not now', 'Ana Ekrana Ekle': 'Add to Home Screen',
  'Dil': 'Language', 'Otomatik (telefon dili)': 'Automatic (phone language)',
  'Türkçe': 'Turkish', 'İngilizce': 'English',
  'Şifreyi gizle': 'Hide password', 'Şifreyi göster': 'Show password',
  'Movienotes’a giriş yap': 'Sign in to Movienotes', 'Movienotes’da hesap oluştur': 'Create a Movienotes account',
  'Açıldığında tüm filmler yüklenir': 'All films load when opened',
  'Sana neden önerdik?': 'Why we recommended it',
  'kullanıcı': 'user', 'SİNEFİL PROFİL KARTI': 'CINEPHILE PROFILE CARD',
  'Blend sonucu bulunamadı.': 'Blend result was not found.', 'Paylaşılacak film bulunamadı.': 'No film is available to share.',
  'ORTAK İZLEME LİSTESİ': 'SHARED WATCHLIST', 'ORTAK İZLENENLER': 'SHARED WATCHED FILMS',
  'Sıradaki filmlerimiz': 'Our next films', 'Aynı filmlerde buluştuk': 'Films we both loved', '% UYUM': '% MATCH',
  'Taranan film': 'Films scanned', 'Listede buluşan': 'Shared list', 'Ortak film': 'Shared films',
  'İKİMİZİN DE İZLEMEK İSTEDİĞİ': 'WE BOTH WANT TO WATCH', 'İKİ ZEVKİ BULUŞTURACAK': 'BRINGING TWO TASTES TOGETHER',
  'ÖNE ÇIKAN ORTAK FİLMLER': 'FEATURED SHARED FILMS',
  'Ortak izleme listemiz': 'Our shared watchlist', 'Ortak izlediğimiz filmler': 'Films we watched in common',
  '{user1} ve {user2} · %{score} uyum · {count} film tarandı': '{user1} and {user2} · {score}% match · {count} films scanned',
  'Profil kartı için Fav 4 ve hesap özeti henüz hazır değil.': 'Your Fav 4 and account summary are not ready for a profile card yet.',
  'SİNEFİL HESAP ÖZETİ': 'CINEPHILE ACCOUNT SUMMARY', 'EN ÇOK DÖNDÜĞÜ TÜR': 'MOST-WATCHED GENRE',
  'Tür sinyali oluşuyor': 'Genre signal is forming', 'FAVORİ YÖNETMEN': 'FAVOURITE DIRECTOR',
  'HESABININ SÖYLEDİĞİ': 'WHAT YOUR ACCOUNT SAYS', '@{username} · Movienotes profil kartı': '@{username} · Movienotes profile card',
  'Sinefil profil kartım': 'My cinephile profile card', '@{username} · izleme geçmişim, Fav 4’üm ve hesap özetim': '@{username} · my viewing history, Fav 4 and account summary',
  'Son izlenen film listesi henüz hazır değil.': 'Your recent-films list is not ready yet.', 'GÜNCE · SON İZLENENLER': 'DIARY · RECENT FILMS',
  'Son {count} film': 'Last {count} films', '@{username} · Letterboxd güncesi': '@{username} · Letterboxd diary',
  'Son izlediğim filmler': 'My recent films', '@{username} · son izlediğim {count} film': '@{username} · my last {count} films',
  'Paylaşım önizlemesi açılamadı.': 'Share preview could not be opened.', '{width} × {height} PNG hazır': '{width} × {height} PNG ready',
  'Sistem paylaşımı açılamadı; PNG olarak indirebilirsin.': 'System sharing could not open; you can download the PNG instead.',
  'Önemli:': 'Important:',
  'Bu parola yalnızca Movienotes hesabın için. Letterboxd parolanı burada kullanma; biz onu hiçbir zaman istemeyiz.': 'This password is only for your Movienotes account. Never use your Letterboxd password here; we will never ask for it.',
  'Sahipliği Letterboxd bio koduyla doğrulayarak yeni parola belirle.': 'Verify ownership with a Letterboxd bio code and choose a new password.',
  'Yalnızca Letterboxd kullanıcı adın yeterli. Verilerini kendi doğrudan scraper’ımızla okuyoruz.': 'Your Letterboxd username is all you need. We read your data with our own direct scraper.',
  'Yalnızca herkese açık Letterboxd profil verileri okunur. Letterboxd parolan hiçbir zaman istenmez, hiçbir yere kaydedilmez.': 'Only public Letterboxd profile data is read. Your Letterboxd password is never requested or stored.',
  'Program': 'How the app', 'nasıl çalışır?': 'works',
  'Kişisel sinema profilin': 'Your personal cinema profile', 'Bir arkadaşını davet et': 'Invite a friend',
  'Bir arkadaşına öner': 'Recommend to a friend', 'Sana özel bir mektup': 'A letter just for you',
  'Mektupların hesabına bağlıdır: giriş yaptığın her cihazda aynı mektupları görürsün.': 'Your letters are tied to your account: you see the same letters on every signed-in device.',
  'Yeni mektupların hesabına bağlıdır: mobilde ve webde aynı konuşmayı açabilirsin. Bu mektubu yalnızca sen ve alıcısı görür.': 'New letters are tied to your account: you can open the same conversation on mobile and web. Only you and the recipient can see this letter.',
  '© 2025 Movienotes · Film Motoru': '© 2025 Movienotes · Film engine',
});

function storageGet() {
  try { return localStorage.getItem(LOCALE_KEY) || ''; } catch (_) { return ''; }
}

export function normalizeLocale(value) {
  const locale = String(value || '').toLowerCase().split('-')[0];
  return SUPPORTED.has(locale) ? locale : 'tr';
}

export function localePreference() {
  const saved = storageGet();
  return saved === 'auto' || SUPPORTED.has(saved) ? saved : 'auto';
}

export function getLocale() {
  const preference = localePreference();
  if (preference !== 'auto') return preference;
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language || 'tr'];
  return languages.some(language => String(language).toLowerCase().startsWith('en')) ? 'en' : 'tr';
}

export function setLocalePreference(preference) {
  const value = preference === 'auto' ? 'auto' : normalizeLocale(preference);
  try { localStorage.setItem(LOCALE_KEY, value); } catch (_) {}
  return value;
}

export function t(source, values = {}) {
  let text = getLocale() === 'en' ? (EN[source] || source) : source;
  for (const [name, value] of Object.entries(values)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
}

function translatedText(value) {
  const leading = value.match(/^\s*/)?.[0] || '';
  const trailing = value.match(/\s*$/)?.[0] || '';
  const core = value.slice(leading.length, value.length - trailing.length);
  const translated = t(core);
  return translated === core ? value : `${leading}${translated}${trailing}`;
}

function translateElement(element) {
  for (const attribute of ['placeholder', 'title', 'aria-label']) {
    if (element.hasAttribute?.(attribute)) {
      const value = element.getAttribute(attribute);
      const translated = t(value);
      if (translated !== value) element.setAttribute(attribute, translated);
    }
  }
}

function translateTree(root) {
  if (getLocale() !== 'en' || !root) return;
  if (root.nodeType === Node.TEXT_NODE) {
    const translated = translatedText(root.nodeValue || '');
    if (translated !== root.nodeValue) root.nodeValue = translated;
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
  if (root.nodeType === Node.ELEMENT_NODE) translateElement(root);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    const translated = translatedText(node.nodeValue || '');
    if (translated !== node.nodeValue) node.nodeValue = translated;
  });
  root.querySelectorAll?.('*').forEach(translateElement);
}

export function applyLocale() {
  const locale = getLocale();
  document.documentElement.lang = locale;
  document.documentElement.dataset.locale = locale;
  document.title = 'Movienotes';
  translateTree(document.body);
}

let observer;
export function initI18n() {
  applyLocale();
  if (observer) return;
  observer = new MutationObserver(records => {
    if (getLocale() !== 'en') return;
    for (const record of records) {
      if (record.type === 'characterData') translateTree(record.target);
      record.addedNodes.forEach(translateTree);
    }
  });
  observer.observe(document.body, { childList: true, subtree: true, characterData: true });
}
