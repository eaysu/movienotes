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
  'Tür sinyali arşiv taraması sürerken oluşuyor…': 'Your genre signal is forming while the archive is read…',
  'Arşivin taranıyor; bu bölüm tarama ilerledikçe dolacak.': 'Your archive is being read; this section fills in as the scan progresses.',
  'Arşivin taranıyor; yönetmen sıralaman tarama ilerledikçe oluşacak.': 'Your archive is being read; your director ranking forms as the scan progresses.',
  'Arşivin taranıyor; yönetmen listen tarama ilerledikçe oluşacak.': 'Your archive is being read; your director list forms as the scan progresses.',
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
  'Son filmler': 'Recent films', 'Bu yıl izlenen': 'Watched this year',
  'Puanlanan film': 'Rated films', 'İzlenen film': 'Watched films', 'En sevilen türler': 'Favourite genres',
  'Takipçi': 'Followers', 'izlenen': 'watched', 'puanlanan': 'rated', 'bu yıl': 'this year',
  'Arşivin taranıyor': 'Your archive is being scanned', 'Zevk analizi': 'Taste analysis',
  'Favori filmlerin okunuyor…': 'Your favourite films are being read…',
  'Favori filmler hazırlanıyor…': 'Your favourite films are being prepared…',
  'Fav 4 analizini şimdi kullanabilirsin; tam geçmişin arka planda ekleniyor.': 'You can use your Fav 4 analysis now; your full history is being added in the background.',
  'Bu hafta vizyonda': 'In theatres this week', 'Sinema filtresi': 'Cinema filter', 'Tüm filmleri gör': 'See all films', 'Bu filtreye uyan gösterim yok.': 'No showings match this filter.', 'Listeyi aç': 'Open list',
  'İzlediğin son 10 film': 'Your last 10 watched films',
  '{rank}. sırada': '{rank}th place',
  'Konu bilgisi yükleniyor…': 'Loading plot details…',
  'Konu bilgisi henüz bulunmuyor.': 'Plot details are not available yet.',
  'Konu bilgisi şu anda alınamadı.': 'Plot details could not be loaded right now.',
  'Filmleri arşiv taraması tamamlandıkça eklenecek.': 'Films will appear as the archive scan completes.',
  'Yönetmen listesi hazırlanıyor…': 'Your director list is being prepared…',
  'Liste arşiv taraması tamamlandıkça eklenecek.': 'This list will appear as the archive scan completes.',
  'Favori dörtlünden ilk okuma': 'A first read from your Fav 4',
  'Favori dörtlün hazırlanıyor…': 'Your Fav 4 is being prepared…',
  'Profil bağlantısı yeniden kuruluyor.': 'Reconnecting your profile.',
  'Tüm geçmişin arka planda taranıyor. Profilin tarama ilerledikçe kendiliğinden zenginleşecek; şimdi uygulamaya girebilirsin.': 'Your full history is being scanned in the background. Your profile will fill itself in as it progresses; you can enter the app now.',
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
  'ORTAK İZLEME LİSTESİ': 'SHARED WATCHLIST',
  'Sıradaki filmlerimiz': 'Our next films', '% UYUM': '% MATCH',
  'Taranan film': 'Films scanned', 'Listede buluşan': 'Shared list', 'Ortak film': 'Shared films',
  'İKİMİZİN DE İZLEMEK İSTEDİĞİ': 'WE BOTH WANT TO WATCH', 'İKİ ZEVKİ BULUŞTURACAK': 'BRINGING TWO TASTES TOGETHER',
  'ÖNE ÇIKAN ORTAK FİLMLER': 'FEATURED SHARED FILMS',
  'Ortak izleme listemiz': 'Our shared watchlist', 'Ortak izlediğimiz filmler': 'Films we watched in common',
  '{user1} ve {user2} · %{score} uyum · {count} film tarandı': '{user1} and {user2} · {score}% match · {count} films scanned',
  'Profil kartı için Fav 4 ve hesap özeti henüz hazır değil.': 'Your Fav 4 and account summary are not ready for a profile card yet.',
  'SİNEFİL HESAP ÖZETİ': 'CINEPHILE ACCOUNT SUMMARY', 'EN ÇOK DÖNDÜĞÜ TÜR': 'MOST-WATCHED GENRE',
  'Tür sinyali oluşuyor': 'Genre signal is forming', 'FAVORİ YÖNETMEN': 'FAVOURITE DIRECTOR',
  'HESABININ SÖYLEDİĞİ': 'WHAT YOUR ACCOUNT SAYS', '@{username} · Movienotes profil kartı': '@{username} · Movienotes profile card',
  'Sinefil profil kartım': 'My cinephile profile card',
  'Puanlamamış': 'No rating', 'Letterboxd’de aç': 'Open on Letterboxd',
  'Profil fotoğrafı': 'Profile photo',
  'İkinizin de izleme listesinde.': 'On both of your watchlists.', '@{username} · izleme geçmişim, Fav 4’üm ve hesap özetim': '@{username} · my viewing history, Fav 4 and account summary',
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
  'Movienotes ana sayfa': 'Movienotes home', 'Keşfet': 'Explore',
  'kayıtlı sinefil burada film keşfediyor': 'registered cinephiles are discovering films here',
  'Letterboxd kullanıcı adı': 'Letterboxd username', 'letterboxd kullanıcı adı': 'Letterboxd username',
  'ikinci letterboxd kullanıcı adı': 'second Letterboxd username',
  'Parola · en az 10 karakter': 'Password · at least 10 characters', 'Parolayı tekrar yaz': 'Repeat password',
  'Yeni parolayı tekrar yaz': 'Repeat new password',
  'İzlediğin filmlerde ara…': 'Search your watched films…', 'İzlediğin filmlerde ara': 'Search your watched films',
  'İzlediğin filmlerde ara (isteğe bağlı)': 'Search your watched films (optional)',
  'Bu sinefile bugün bir mektup yazdıysan, yeniden yazmak için 24 saat beklemen gerekir. Başka bir sinefile ise hemen yazabilirsin. Bir film, bir sahne ya da sende bıraktığı bir düşünce…': 'If you wrote to this cinephile today, wait 24 hours before writing again. You can write to another cinephile right away. A film, a scene, or a thought it left with you…',
  'Kopyalamak için tıkla': 'Click to copy', 'Letterboxd güncesindeki yeni filmleri tarar': 'Scans new films in your Letterboxd diary',
  'Geri kaydır': 'Scroll back', 'İleri kaydır': 'Scroll forward', 'Bölümü aç/kapat': 'Open or close section',
  'Listeyi düzenle': 'Edit list', 'Önceki': 'Previous', 'Akışı filtrele': 'Filter feed',
  'Bir oyunculuk, bir plan ya da final… paylaş': 'Share a performance, a shot, or an ending…',
  'Mektuplar nasıl çalışır?': 'How do letters work?', 'Sinefil sayfaları': 'Cinephile pages',
  'Letterboxd kullanıcı adını girer, bio alanına tek seferlik bir kod ekleyerek profilin senin olduğunu doğrularsın.': 'Enter your Letterboxd username and verify the profile is yours by adding a one-time code to your bio.',
  'Herkese açık izleme geçmişin ve watchlist’in doğrudan Letterboxd’dan okunur — hiçbir dosya yüklemen gerekmez.': 'Your public viewing history and watchlist are read directly from Letterboxd — no files to upload.',
  'Tüm geçmişin taranıp sinefil profilin çıkarılır: favori yönetmenlerin, sevdiğin türler ve kısa bir kişilik okuması.': 'Your complete history is scanned to build your cinephile profile: favourite directors, genres you enjoy, and a short personality read.',
  '“Bu gece ne izlesem?” dediğinde watchlist’inden ya da tüm kütüphanenden zevkine en uygun filmler önerilir.': 'When you ask “What should I watch tonight?”, we recommend films from your watchlist or the wider catalogue that fit your taste.',
  'Blend ile başka bir kullanıcının zevkiyle uyumunu ölçebilirsin — yalnızca o kişi isteğini kabul ederse.': 'With Blend, you can measure your compatibility with another member’s taste — only after they accept your request.',
  '1080 × 1350 PNG hazır': '1080 × 1350 PNG ready', 'Veri yaklaşımı': 'Data approach',
  'Veriyi kendi sunucumuz doğrudan okur; üçüncü taraf bir scraping servisi ya da ücretli API kullanılmaz.': 'Our server reads the data directly; no third-party scraping service or paid API is used.',
  'Movienotes parolan Supabase Auth tarafından saklanır; uygulama tablolarına parola ya da parola özeti yazılmaz.': 'Your Movienotes password is stored by Supabase Auth; no password or password hash is written to the app tables.',
  'Film verileri (afiş, tür, yönetmen) TMDb’den zenginleştirilir ve tekrar kullanılmak üzere ortak bir havuzda tutulur.': 'Film data (poster, genre, director) is enriched from TMDb and kept in a shared pool for reuse.',
  '“Ayarlar › Verimi sil” ile profilini, zevk analizini ve ilişkili tüm verini kalıcı olarak silebilirsin.': 'Use “Settings › Delete my data” to permanently delete your profile, taste analysis, and related data.',
  'Zevkine göre film önerileri ve Blend uyum skoru için arkadaşını çağır.': 'Invite a friend for taste-based film picks and a Blend compatibility score.',
  'Instagram ve TikTok: linki kopyalayıp DM\'den gönderebilirsin.': 'Instagram and TikTok: copy the link and send it in a DM.',
  'Sinefil Mektupları nasıl çalışır?': 'How do Cinephile Letters work?',
  'Mektup kutun varsayılan olarak açıktır; izole kalmak istersen profilinden kapatabilirsin.': 'Your letterbox is open by default; you can close it from your profile if you prefer to stay private.',
  'Mektup yollayabilmek için kendi mektup kutunun da açık olması gerekir; kapalıyken karşı taraf sana cevap veremez.': 'To send a letter, your own letterbox must be open; when it is closed, the other person cannot reply.',
  'Mektuplarını yalnızca sen ve yazıştığın kişi görebilir. Movienotes bunları uçtan uca şifrelemez; gizliliği erişim kuralları sağlar.': 'Only you and the person you correspond with can see your letters. Movienotes does not end-to-end encrypt them; access rules protect their privacy.',
  'Her sinefile 24 saatte bir mektup yazabilirsin. Başka bir sinefile ise beklemeden yazabilirsin. Gelen bir mektup açıldığında otomatik olarak okundu görünür.': 'You can write to each cinephile once every 24 hours. You can write to another cinephile without waiting. An incoming letter is marked read when opened.',
  'Bir kullanıcıyı engellediğinde aranızdaki mektuplar iki taraftan da kaldırılır. Bildirim, içeriği otomatik paylaşmaz.': 'When you block a user, letters between you are removed for both sides. Notifications never reveal the content automatically.',
  'Not yazmadan önce': 'Before writing a note', '@kullanıcı': '@user', 'adlı sinefile mektup yollayabilmen için kendi mektup kutunun da açık olması gerekiyor — kapalıyken karşı taraf sana cevap veremez.': 'your own letterbox must be open to send a letter to this cinephile — when it is closed, they cannot reply.',
  'Kutunu açtığında Sinefil Sineması’ndaki üyeler sana günde bir mektup gönderebilir. İstediğin an profilinden kapatabilirsin.': 'When you open your letterbox, members in Cinephile Cinema can send you one letter a day. You can close it from your profile at any time.',
  'Kutumu aç ve yazmaya başla': 'Open my letterbox and start writing', 'İstersen izlediğin bir filmi de hediye edebilirsin.': 'If you like, you can also gift a film you watched.',
  '600 karakter kaldı': '600 characters remaining', 'Yalnızca seni takip eden ve mektup kutusu açık sinefiller burada görünür.': 'Only cinephiles who follow you and have an open letterbox appear here.',
  'Movienotes uygulaması': 'Movienotes app', 'Ana ekranından tek dokunuşla aç; daha uygulama gibi, daha hızlı kullan.': 'Open it from your home screen in one tap for a faster, app-like experience.',
  'düğmesine dokun': 'tap the button', '’yi seç': 'then choose', 'Sağ üstten': 'From the top right',
  'Engeli kaldırdığında takip, Blend veya mektup ilişkisi otomatik olarak geri gelmez.': 'Unblocking does not automatically restore following, Blend, or letter relationships.',
  'Beni hatırla': 'Remember me', 'Aşağıdaki kodu Letterboxd profilindeki': 'Add the code below to the',
  'alanına ekle. Profilin herkese açık olmalı.': 'field on your Letterboxd profile. Your profile must be public.',
  'Notlarım ve takipçilerim': 'My notes and followers', 'Favori yönetmen': 'Favourite director',
  'Şu an geçici bir analiz gösteriliyor; tüm geçmiş tamamlanınca otomatik güncellenir.': 'A provisional analysis is showing now; it updates automatically once your full history is complete.',
  'Watchlist\'ten öner': 'Recommend from watchlist', 'Zevk analizine göre en uygun film.': 'The best fit according to your taste analysis.',
  'Rastgele öneri': 'Random recommendation', 'Blend isteği gönder': 'Send Blend request',
  'Günce': 'Diary',
  'Zevkine göre bir film bul ya da takip ettiğin biriyle ortak bir liste çıkar.': 'Find a film for your taste or create a shared list with someone you follow.',
  'İzleme listende': 'In your watchlist', 'İzleme geçmişine ve film zevkine bakarak bu akşam sana iyi gelecek filmi bulur.': 'Finds a film that will suit your evening using your viewing history and taste.',
  'Sıraya alındınız — önünüzde': 'You are in the queue — ahead of you:', 'kişi var': 'people',
  'Ne izlesem? bölümüne dön': 'Back to What should I watch?', 'En İyi Öneri': 'Top recommendation',
  'Diğer Seçenekler': 'Other options', 'ortak yönetmen': 'shared director',
  'Ortak İzledikleriniz': 'Films you both watched', 'Ama zevk analizi yapıldı!': 'But the taste analysis is complete!',
  'Ortak watchlist taranıyor…': 'Scanning shared watchlist…', 'Birlikte İzlemek İstedikleriniz': 'Films you both want to watch',
  'Farklı Film': 'Different film', 'Zevk Analizi ile Öner': 'Recommend by taste analysis',
  'Belirli bir film hakkındaki notları gör.': 'See notes about a specific film.',
  'Takip ettiklerinin notlarını kişi bazında daralt.': 'Narrow notes from people you follow by person.',
  'Topluluk notları etkileşime göre sıralanır; eşit notlarda takip ettiklerin öne çıkar.': 'Community notes are ranked by engagement; people you follow come first when scores are tied.',
  'Henüz not yazmamış.': 'They have not written a note yet.', 'Yükleniyor…': 'Loading…',
  'Profil açılamadı.': 'Profile could not be opened.', 'Gelen': 'Received', 'Gönderilen': 'Sent',
  'Bildir': 'Report', 'Engelle': 'Block', 'İki taraftan sil': 'Delete for both', 'Görüldü': 'Seen',
  'Film hediyesi': 'Film gift', 'Puanladıkların': 'Your ratings',
  '❝ Söz': '❝ Quote', '🎬 Bilgi': '🎬 Film fact', 'Anladım': 'Got it',
  'Öner': 'Recommend', 'Rastgele Seç': 'Random pick', 'Blend Oluştur': 'Create Blend',
  'Blend İsteği Gönder': 'Send Blend request', 'İzleme listende': 'In your watchlist',
  'ne izlemelisin?': 'what should you watch?', 'Watchlist’inden': 'From your watchlist',
  'sürpriz bir film.': 'a surprise film.', 'İki sinefilin': 'Two cinephiles’', 'uyum skoru.': 'compatibility score.',
  'Son izlediklerine ve film zevkine bakarak bu akşam sana iyi gelecek filmi bulur.': 'Finds a film for tonight using your recent watches and film taste.',
  'Watchlist’inden rastgele bir film seç — maksimum 3 kez şansını dene.': 'Pick a random film from your watchlist — try your luck up to three times.',
  'Kayıtlı bir kullanıcıya istek gönder. Zevkleriniz yalnızca o kabul ederse karşılaştırılır.': 'Send a request to a registered member. Your tastes are compared only if they accept.',
  'İki Letterboxd kullanıcısının film zevkini karşılaştır, ortak favorileri keşfet.': 'Compare two Letterboxd users’ film tastes and discover shared favourites.',
  'Araçlara dön': 'Back to tools', 'İki zevk, tek liste': 'Two tastes, one list', 'Blend yap': 'Make a Blend',
  'Rastgele seç': 'Choose randomly', 'Sunucu yanıtı tamamlanmadan bağlantı kapandı. Lütfen tekrar deneyin.': 'The connection closed before the server finished responding. Please try again.',
  'Sunucuya ulaşılamadı.': 'Could not reach the server.', 'İzleme listen okunuyor': 'Reading your watchlist',
  'Film bilgileri toplanıyor': 'Collecting film details', 'Zevkinle eşleştiriliyor': 'Matching to your taste',
  'En iyi seçim yapılıyor': 'Making the best pick', 'Topluluk havuzu karıştırılıyor': 'Shuffling the community pool',
  'Yeni öneri': 'New recommendation', 'Hazırlanıyor': 'Preparing',
  'Watchlist\'inde öneri için yeterli film yoktu — eksikleri TMDb\'den, daha önce izlemediğin filmlerden tamamladık.': 'Your watchlist did not have enough films for a recommendation — we completed it with films you have not watched from TMDb.',
  'Sana uygun bir öneri çıkaramadık.': 'We could not find a suitable recommendation.', 'Kartı sağa sola kaydır': 'Swipe the card left or right',
  'filmi de beğenmedin mi? Rastgeleye geç': 'films and still not convinced? Switch to random.',
  'Film bulunamadı.': 'No film found.', '🎬 Beğenmezsen çevirmeye devam et — hak sınırı yok.': '🎬 Keep spinning if you do not like it — there is no limit.',
  'Başka bir tane': 'Another one', 'Zevkime göre öner': 'Recommend for my taste',
  'Topluluk havuzu henüz yeterli değil — bunu TMDb\'den, izlemediğin filmler arasından seçtik.': 'The community pool is not large enough yet — we chose this from unwatched TMDb films.',
  'Diğer Movienotes üyelerinin izlediği, senin izlemediğin filmler arasından.': 'From films watched by other Movienotes members that you have not watched.',
  'Bir arkadaşını davet et': 'Invite a friend', 'Davet etmek ister misin? Linki kopyala ya da bir uygulamadan gönder.': 'Would you like to invite them? Copy the link or send it with an app.',
  'Kopyalandı ✓': 'Copied ✓', 'PNG oluşturulamadı.': 'PNG could not be created.',
  'Görünüm: Açık': 'Appearance: Light',
  'Hesabın kilitlenecek: notların, profil ayrıntıların ve takip listelerin yalnız kabul ettiğin takipçilere açılır, Sinefil Sineması listesinde de çıkmazsın. Uygulamayı sosyal alandan izole kullanmak istiyorsan bu yeterli. Devam edilsin mi?': 'Your account will be private: your notes, profile details, and follows will only be available to followers you accept, and you will not appear in Cinephile Cinema. Continue?',
  'Hesabın herkese açılacak: notların ve profil ayrıntıların tüm kayıtlı sinefillere görünür, Sinefil Sineması listesinde çıkarsın. Devam edilsin mi?': 'Your account will become public: your notes and profile details will be visible to all registered cinephiles, and you will appear in Cinephile Cinema. Continue?',
  'Hesabın kilitlendi; Sinefil Sineması listesinde de çıkmayacaksın.': 'Your account is private; you will not appear in Cinephile Cinema.',
  'Hesabın herkese açıldı.': 'Your account is public.', 'Bu tarayıcı kalıcı push bildirimi desteği sunmuyor.': 'This browser does not support persistent push notifications.',
  'Tarayıcı bildirimi için izin verilmedi.': 'Browser notification permission was not granted.', 'Servis çalışanı kaydedilemedi.': 'Service worker could not be registered.',
  'Tarayıcı bildirimleri açık. Uygulama arka plandayken de yeni hareketleri haber vereceğiz.': 'Browser notifications are on. We will let you know about new activity even while the app is in the background.',
  'Tarayıcı bildirimi açılamadı.': 'Browser notifications could not be enabled.',
  'İzleme geçmişin tamamlandıkça bu alan sinema alışkanlıklarını daha ayrıntılı anlatacak.': 'As your viewing history grows, this space will describe your cinema habits in more detail.',
  'Letterboxd Fav 4 henüz alınamadı.': 'Your Letterboxd Fav 4 could not be retrieved yet.',
  'Bir film hakkında düşüncelerini paylaş': 'Share your thoughts about a film', 'Perde kapandıktan sonra aklında ne kaldı?': 'What stayed with you after the credits?',
  'Bu sahne sende nasıl bir iz bıraktı?': 'How did this scene stay with you?', 'Bugün izlediğin filmle iki cümle kur': 'Write two sentences about the film you watched today',
  'Sinefillerin arasında bir not bırak': 'Leave a note among cinephiles', 'az önce': 'just now', 'devamını oku': 'read more',
  'Spoiler — göstermek için dokun': 'Spoiler — tap to reveal', 'Bu hafta en çok konuşulanlar': 'Most discussed this week',
  'İzlediklerin arasında bulunamadı.': 'Not found among your watched films.', 'Bu film hakkında not yok.': 'There are no notes about this film.',
  'Henüz not yok. İlkini sen yaz.': 'There are no notes yet. Write the first one.', 'Akış yüklenemedi.': 'Feed could not be loaded.',
  'Takip ettiklerin henüz not yazmamış.': 'People you follow have not written notes yet.', 'Künye yükleniyor…': 'Film details are loading…',
  'üye izlemiş': 'members watched', 'topluluk ortalaması': 'community average', 'İzledin': 'Watched', 'Tümü': 'All',
  'Puanladığın filmler tarandıkça en sevdiğin 10 film burada. Kalemle kendin de seçebilirsin.': 'As your rated films are scanned, your 10 favourite films appear here. You can also choose them yourself with the pencil.',
  'İzleme geçmişin tarandıkça son izlediğin filmler burada görünür.': 'Your recent films appear here as your viewing history is scanned.',
  'Eşleşen film yok.': 'No matching film.', 'Filmler alınamadı.': 'Films could not be retrieved.', 'En fazla 10 film seçebilirsin.': 'You can choose at most 10 films.',
  'İzleme geçmişin taranıyor': 'Scanning your viewing history', 'Film verileri zenginleştiriliyor': 'Enriching film data', 'Zevk analizi hesaplanıyor': 'Calculating taste analysis',
  'Gelen kutusu': 'Inbox', 'Mektuplara açığım': 'My letterbox is open', 'Mektuplara kapalı': 'My letterbox is closed',
  'Bu mektup eski cihaz-anahtarlı biçimde yazılmıştı ve artık açılamıyor.': 'This letter was written using the old device-key format and can no longer be opened.',
  'Mektup içeriği okunamadı.': 'Letter content could not be read.', 'Bir mektup seç': 'Choose a letter',
  'Konuştuğun sinefiller solda. Birini seçtiğinde tüm mektuplarınız burada açılır.': 'The cinephiles you correspond with are on the left. Choose one to open all your letters here.',
  'Mektuplara dön': 'Back to letters', 'Henüz mektubun yok.': 'You do not have any letters yet.', 'Mektuplar yüklenemedi.': 'Letters could not be loaded.',
  'Mektupları açarsan Sinefil Sineması’ndaki kullanıcılar sana 24 saatte bir mektup gönderebilir. Açmak istiyor musun?': 'If you open letters, people in Cinephile Cinema can send you one letter every 24 hours. Would you like to open them?',
  'Mektupları kapatırsan yeni mektup alamazsın. Mevcut mektupların korunur. Kapatmak istiyor musun?': 'If you close letters, you will not receive new ones. Your existing letters will remain. Close them?',
  'Mektuplara açıksın. İstediğin an buradan kapatabilirsin.': 'Your letterbox is open. You can close it here at any time.', 'Mektuplar kapatıldı.': 'Letters are closed.',
  'Gönderim hakkı kontrol edilemedi.': 'Could not check sending permission.', 'Kaldır': 'Remove',
  'Bu mektup gönderildikten sonra geri alınamaz. Göndermek istiyor musun?': 'This letter cannot be recalled after it is sent. Send it?', 'Mektup gönderilemedi.': 'Letter could not be sent.',
  'Bilinmeyen kullanıcı': 'Unknown user', 'Kullanıcıyı bildir': 'Report user', 'Kullanıcıyı engelle': 'Block user',
  'İptal et': 'Cancel request', 'hazır değil': 'not ready', 'Aç': 'Open', 'Sonucu hazırla': 'Prepare result',
  'Henüz tamamlanmış bir Blend yok. Bir arkadaşına Blend isteği gönder.': 'There is no completed Blend yet. Send a Blend request to a friend.',
  'Blendler yüklenemedi.': 'Blends could not be loaded.', 'Engeli kaldır': 'Unblock', 'Engellediğin kullanıcı yok.': 'You have not blocked anyone.',
  'Engellenen kullanıcılar yüklenemedi.': 'Blocked users could not be loaded.', 'Bekleyen gelen isteğin yok.': 'You have no pending incoming requests.',
  'Bekleyen gönderilmiş isteğin yok.': 'You have no pending sent requests.',
  'Takipçilerin yükleniyor…': 'Your followers are loading…', 'Mektup kutusu açık bir takipçin henüz yok.': 'You do not have a follower with an open letterbox yet.',
  'Takipçilerin şu an yüklenemedi.': 'Your followers could not be loaded right now.', 'Mektup yazmak için mektuplara açık bir sinefilin Mektup düğmesine dokun.': 'To write a letter, tap Letter for a cinephile whose letterbox is open.',
  'Bu kullanıcı sana zaten bir Blend isteği göndermiş. İstek burada.': 'This user has already sent you a Blend request. It is here.',
  'Bu kullanıcıya gönderdiğin Blend isteği hâlâ yanıt bekliyor.': 'Your Blend request to this user is still awaiting a response.',
  'İstek gönderildi': 'Request sent', 'Blend isteği gönderilemedi.': 'Blend request could not be sent.',
  'Sinefiller aranıyor…': 'Searching cinephiles…', 'Henüz gösterilecek sinefil yok. Yeni kayıtlar burada belirecek.': 'There are no cinephiles to show yet. New members will appear here.',
  'Sinefil Sineması yüklenemedi.': 'Cinephile Cinema could not be loaded.',
  'Bu tur bitti — beğenmediysen yeni bir tur çekelim.': 'This round is done — start a new one if you did not like it.',
  'Şu an önerecek film bulamadık; biraz sonra tekrar dene.': 'We could not find a film to recommend right now; please try again shortly.',
  'Silmek istediğin Letterboxd kullanıcı adını önce yukarıya yaz.': 'Enter the Letterboxd username you want to delete above first.',
  'Yeni bir analiz başlatırsan public veriler tekrar oluşturulur.': 'If you start a new analysis, public data will be created again.',
  'Önceki yönetmen': 'Previous director', 'Sonraki yönetmen': 'Next director', 'Önceki film': 'Previous film',
  'Konu bilgisi hazırlanıyor…': 'Plot details are being prepared…', 'Tüm geçmiş analiz edildi': 'Full history analysed',
  'Mektup ayarı güncellenemedi.': 'Letterbox setting could not be updated.', 'Mektubun @': 'Your letter to @',
  'Hesap oluşturulamadı.': 'Account could not be created.', 'Giriş yapılamadı.': 'Could not sign in.',
  'Letterboxd yanıtı gecikti, hâlâ deniyoruz…': 'Letterboxd is taking longer to respond; we are still trying…',
  'Bu normalden uzun sürüyor ama vazgeçmedik, birkaç saniye daha bekle…': 'This is taking longer than usual, but we have not given up — please wait a few more seconds…',
  'Doğrulama kodu hazırlanıyor…': 'Preparing verification code…', 'Kod hazırlanıyor, birkaç saniye daha…': 'Preparing your code, a few more seconds…',
  'Hesap bağlantısı gecikti ama hâlâ çalışıyor…': 'Account connection is delayed, but still working…',
  'Kod 15 dakika geçerli. Bio’yu kaydettikten sonra kontrol et.': 'The code is valid for 15 minutes. Check after saving your bio.',
  'Letterboxd bio alanı kontrol ediliyor…': 'Checking your Letterboxd bio…',
  'Hesap doğrulandı. Şimdi parolanla giriş yapabilirsin.': 'Account verified. You can now sign in with your password.',
  'Bio doğrulanamadı.': 'Bio could not be verified.', 'Kodu Letterboxd bio alanına ekle, sonra yeni parolanı kaydet.': 'Add the code to your Letterboxd bio, then save your new password.',
  'Sıfırlama başlatılamadı.': 'Reset could not be started.', 'Parolan değiştirildi. Yeni parolanla giriş yapabilirsin.': 'Your password was changed. You can sign in with your new password.',
  'Parola değiştirilemedi.': 'Password could not be changed.', 'Kod panoya kopyalandı.': 'Code copied to clipboard.',
  'Topluluk notları': 'Community notes', 'Bir filmde ara': 'Search for a film', 'Toplulukta notu olan filmlerde ara': 'Search films with community notes',
  'Takip isteği güncellenemedi.': 'Follow request could not be updated.', 'Mektup kutusu açılamadı.': 'Letterbox could not be opened.',
  'Filmler yükleniyor…': 'Films are loading…', 'Filmler yüklenemedi; tekrar dene.': 'Films could not be loaded; try again.',
  'İzleme geçmişin ve Fav 4 filmlerin analiz ediliyor…': 'Your viewing history and Fav 4 films are being analysed…',
  'Kısa ve tatlı bir geçmişin var. Analizin birazdan hazır, daha esnemeye fırsat bulamadan döneriz.': 'You have a short and sweet history. Your analysis will be ready soon.',
  'Dolu dolu bir arşiv! Filmleri tek tek okuyoruz, yalnızca bir-iki dakika. Sen keyfine bak.': 'That is a rich archive! We are reading the films one by one; it will only take a minute or two.',
  'Bu ciddi bir koleksiyon. Yüzlerce filmi tarıyoruz, birkaç dakika sürebilir; bu arada aşağıdaki sinema bilgileriyle vakit geçir.': 'This is a serious collection. We are scanning hundreds of films; it may take a few minutes. Enjoy the cinema facts below meanwhile.',
  'Kocaman bir sinema geçmişin var ve hepsini hakkıyla analiz etmek istiyoruz. Kahveni tazele, birkaç dakikaya buradayız.': 'You have a substantial cinema history and we want to analyse it properly. Refresh your coffee; we will be back in a few minutes.',
  'Binden fazla film… Sen gerçek bir sinefilsin. Bu arşivi satır satır okumak birkaç dakika alacak ama sonucu görünce ‘iyi ki beklemişim’ diyeceksin.': 'More than a thousand films… You are a true cinephile. Reading this archive line by line will take a few minutes, but the result will be worth the wait.',
  '250 filmi geride bıraktık…': 'We are past 250 films…', '1000 filmi de devirdik — amma izlemişsin!': 'We passed 1,000 films — that is a lot of watching!',
  'Son düzlükteyiz, analiz derleniyor…': 'We are in the final stretch; analysis is coming together…',
  'Onboarding tamamlanamadı. Lütfen tekrar dene.': 'Onboarding could not finish. Please try again.',
  'Letterboxd profilin bağlandı': 'Your Letterboxd profile is connected', 'Zevk profilini çıkardık — birlikte bakalım.': 'We built your taste profile — let’s take a look.',
  'Letterboxd geçmişin': 'Your Letterboxd history', 'Favori dörtlün': 'Your Fav 4',
  'Son izlediğin film': 'The last film you watched', 'Listelerin': 'Your lists',
  '{rating}★ verdin': 'You rated it {rating}★',
  'Sinefil kişiliğin': 'Your cinephile personality', 'Zevkini paylaşmak ister misin?': 'Would you like to share your taste?',
  'Profilin varsayılan olarak diğer kayıtlı sinefillere görünür. İstediğin an profilinden gizleyebilirsin.': 'Your profile is visible to other registered cinephiles by default. You can hide it from your profile at any time.',
  'Görünür kal': 'Stay visible', 'Profilin Sinefil Sineması’nda görünür.': 'Your profile is visible in Cinephile Cinema.',
  'Profilin gizli kalacak.': 'Your profile will stay private.', 'Profilin görünür durumda.': 'Your profile is visible.',
  'Tüm izleme geçmişin arka planda taranmaya devam ediyor. Uygulamaya şimdi geçebilirsin; profilin tarama ilerledikçe kendiliğinden zenginleşir.': 'Your full viewing history keeps being read in the background. You can open the app now; your profile grows richer as the scan progresses.',
  'Arşiv taraman arka planda sürüyor.': 'Your archive scan is running in the background.', 'İleri / geri gezinebilirsin.': 'You can move forward or back.',
  'Bağlantı yeniden kuruluyor': 'Reconnecting',
  'İzlediğin filmler': 'Films you watched', 'Bu yıl': 'This year',
  'Yüksek': 'High', 'Orta': 'Medium', 'Düşük': 'Low', 'veri kapsamı': 'data coverage',
  'Birlikte İzlemek İstedikleriniz': 'Films you both want to watch', 'Fav 4 henüz hazır değil.': 'Fav 4 is not ready yet.',
  'Zevk haritalarınız yakın': 'Your taste maps are close',
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
      if (record.type === 'attributes') translateElement(record.target);
      record.addedNodes.forEach(translateTree);
    }
  });
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ['placeholder', 'title', 'aria-label'],
  });
}
