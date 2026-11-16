import { $, escapeHTML, safeImageURL } from './dom.js?v=20261116.2';
import { apiJSON } from './api.js?v=20261116.2';
import { csrfHeaders } from './auth.js?v=20261116.2';
import { t } from './i18n.js?v=20261116.2';
import { runLetterboxdImport } from './import-progress.js?v=20261116.2';

const icon = (name) => `<span class="material-symbols-outlined" aria-hidden="true">${name}</span>`;
const poster = film => safeImageURL(film.poster_url)
  ? `<img src="${safeImageURL(film.poster_url)}" alt="" loading="lazy"/>`
  : `<span class="native-poster-placeholder">${icon('movie')}</span>`;

export function createNativeOnboarding({ showView, onProfile, onComplete }) {
  let active = false, selected = [], results = [], phase = 'pick', tour = 0;
  let analysis = null, analyzing = false, error = '', searchId = 0, timer;
  let saving = false, imported = '', account, generation = 0;
  const stage = $('ob-stage');
  const panels = [
    { icon: 'chat_bubble', eyebrow: '01 / FİLM NOTLARI', title: 'Film biter. Sohbet devam eder.', body: 'Bugün izlediğin bir film ya da yıllardır aklından çıkmayan bir sahne. Bir film seç, düşünceni yaz; istersen beş üzerinden puanını ekle.', preview: '<div class="native-note-preview"><span class="native-preview-avatar">M</span><div><strong>Senin sinema köşen <small>· bugün</small></strong><p>Bazı filmler bittikten sonra başlıyor.</p><span class="native-stars">★★★★½</span><small>420 karakter. İstersen spoiler etiketiyle.</small></div></div>' },
    { icon: 'auto_awesome', eyebrow: '02 / KEŞİF', title: 'Bir sonraki filmin, sana yakın.', body: 'İzleme listeni oluştur, zevkine yakın filmleri keşfet. Sinefil Sineması’nda ortak noktaların olan insanlarla tanış; Blend ile film zevklerinizi karşılaştır.', preview: '<div class="native-feature-row"><span>'+icon('bookmark')+'İzleme listesi</span><span>'+icon('join_inner')+'Blend</span><span>'+icon('groups')+'Sinefil Sineması</span></div>' },
    { icon: 'local_activity', eyebrow: '03 / TOPLULUK', title: 'Sinema, bir araya gelme meselesi.', body: 'Notlara cevap ver, sinefilleri takip et, bir filmle birlikte mektup gönder. Sinema bülteninden perdedeki filmleri keşfet. Eski Letterboxd arşivini de istediğin zaman ayarlardan getirebilirsin.', preview: '<div class="native-feature-row"><span>'+icon('forum')+'Film sohbetleri</span><span>'+icon('mail')+'Mektuplar</span><span>'+icon('theaters')+'Sinema bülteni</span></div>' },
  ];
  function buttons() {
    $('ob-prev').classList.add('hidden'); $('ob-next').classList.add('hidden');
    $('ob-skip').classList.add('hidden'); $('ob-dots').innerHTML = '';
    $('ob-bg-note').textContent = phase === 'pick'
      ? t('Dört filmle başlar. Sen ekledikçe sana daha çok benzer.')
      : t('Bu ilk okuma dört favorine dayanıyor; film ekledikçe gelişir.');
  }
  function slots() {
    return Array.from({ length: 4 }, (_, index) => {
      const film = selected[index];
      const attributes = film
        ? 'data-native-remove="' + escapeHTML(film.id) + '" aria-label="' + escapeHTML(film.title) + ' · ' + t('Kaldır') + '"'
        : 'disabled aria-label="' + t('Boş favori yuvası') + '"';
      return `<button type="button" class="native-fav-slot ${film ? 'is-filled' : ''}" ${attributes}>${film ? poster(film) : `<span class="native-slot-number">0${index + 1}</span>${icon('add')}`}<span class="native-slot-label">${film ? escapeHTML(film.title) : t('Senin seçimin')}</span>${film ? '<span class="native-remove">×</span>' : ''}</button>`;
    }).join('');
  }
  function paintResults() {
    const el = $('native-film-results'); if (!el) return;
    el.innerHTML = results.length ? results.map((film, index) => {
      const chosen = selected.some(f => f.id === film.id);
      return `<button type="button" data-native-film="${index}" class="native-search-film ${chosen ? 'is-selected' : ''}" aria-pressed="${chosen}" ${!chosen && selected.length === 4 ? 'disabled' : ''}>${poster(film)}<span class="native-film-caption"><strong>${escapeHTML(film.title)}</strong><small>${escapeHTML(film.year || '')}</small></span><span class="native-film-check">${icon(chosen ? 'check' : 'add')}</span></button>`;
    }).join('') : `<p class="native-empty">${t('Film bulunamadı. Başka bir adla arayabilirsin.')}</p>`;
  }
  function paintSelection() {
    $('native-fav-slots').innerHTML = slots();
    $('native-fav-count').textContent = `${selected.length} / 4`;
    const button = $('native-favorites-save');
    button.disabled = selected.length !== 4 || saving;
    button.textContent = saving ? t('Favorilerin kaydediliyor…') : t('Sinema profilimi oluştur');
    paintResults();
  }
  async function search(query = '') {
    const id = ++searchId;
    const status = $('native-search-status');
    if (status) status.textContent = t('Filmler aranıyor…');
    try {
      const data = await apiJSON(`/api/catalog/search?q=${encodeURIComponent(query)}`);
      if (!active || phase !== 'pick' || id !== searchId) return;
      results = data.films || []; paintResults();
      if (status) status.textContent = data.upstream_unavailable ? t('Kayıtlı filmler gösteriliyor. Film servisini biraz sonra yeniden deneyebilirsin.') : '';
    } catch (err) {
      if (status && id === searchId) status.textContent = err.message || t('Filmler yüklenemedi. Yeniden dene.');
    }
  }
  function pick() {
    buttons(); phase = 'pick';
    stage.innerHTML = `<section class="native-onboard native-in"><div class="native-eyebrow"><span class="native-live-dot"></span>${t('SENİN SİNEMA DÜNYAN')}</div><h1>${t('Seni anlatan')}<br/><em>${t('dört film.')}</em></h1><p class="native-lede">${t('Kusursuz bir liste aramıyoruz. Sende iz bırakan dört filmi seç; hikâyen buradan başlasın.')}</p><div class="native-selection-head"><span>${t('FAVORİ DÖRTLÜN')}</span><strong id="native-fav-count">${selected.length} / 4</strong></div><div id="native-fav-slots" class="native-fav-slots">${slots()}</div><label class="native-search-label" for="native-film-search">${t('Bir film bul')}</label><div class="native-search-box">${icon('search')}<input id="native-film-search" type="search" placeholder="${t('Film adıyla ara…')}" autocomplete="off" maxlength="120"/></div><p id="native-search-status" class="native-status" role="status" aria-live="polite"></p><div id="native-film-results" class="native-film-results" aria-label="${t('Film sonuçları')}"></div><p id="native-onboard-error" class="native-error" role="alert"></p><button id="native-favorites-save" type="button" class="native-primary" ${selected.length !== 4 ? 'disabled' : ''}>${t('Sinema profilimi oluştur')}${icon('arrow_forward')}</button><details class="native-import"><summary>${icon('inventory_2')}<span>${t('Letterboxd arşivin mi var?')}</span>${icon('expand_more')}</summary><p>${t('ZIP dosyanı getir; filmlerin, puanların ve yorumların özel arşivine aktarılsın. Dört favorini yine burada seçebilirsin.')}</p><a href="https://letterboxd.com/user/exportdata/" target="_blank" rel="noopener noreferrer">${t('Letterboxd’dan arşivimi indir')}${icon('open_in_new')}</a><label class="native-import-upload" for="native-archive">${icon('upload_file')}${t('ZIP dosyası seç')}<input id="native-archive" type="file" accept=".zip,application/zip"/></label><p id="native-import-status" role="status">${escapeHTML(imported)}</p></details></section>`;
    $('native-film-search').addEventListener('input', event => { clearTimeout(timer); timer = setTimeout(() => search(event.target.value.trim()), 280); });
    $('native-favorites-save').addEventListener('click', save);
    $('native-archive').addEventListener('change', event => upload(event.target.files?.[0]));
    search();
  }
  async function upload(file) {
    if (!file) return;
    const status = $('native-import-status');
    if (file.size > 16 * 1024 * 1024 || !/\.zip$/i.test(file.name)) { status.textContent = t('Lütfen 16 MB altında bir Letterboxd ZIP dosyası seç.'); return; }
    $('native-archive').disabled = true; status.textContent = t('Arşivin alınıyor…');
    try {
      const data = await runLetterboxdImport(file);
      imported = t('{films} film, {ratings} puan ve {reviews} yorum alındı. Eşleştirmeler arka planda tamamlanıyor.', { films: data.watched_count, ratings: data.import?.counts?.ratings || 0, reviews: data.review_count });
      if (status.isConnected) status.textContent = imported;
    } catch (err) { if (status.isConnected) status.textContent = err.message; }
    finally { if ($('native-archive')) $('native-archive').disabled = false; }
  }
  async function save() {
    if (selected.length !== 4 || saving) return;
    saving = true; paintSelection(); const token = generation;
    try {
      const profile = await apiJSON('/api/profile/favorites', { method: 'PUT', headers: csrfHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ film_ids: selected.map(f => f.id) }) });
      if (!active || token !== generation) return;
      generation++; onProfile(profile); phase = 'tour'; tour = 0; paintTour(); runAnalysis();
    } catch (err) { if ($('native-onboard-error')) $('native-onboard-error').textContent = err.message; }
    finally { saving = false; if ($('native-favorites-save')) paintSelection(); }
  }
  async function runAnalysis() {
    analyzing = true; error = ''; const token = generation;
    if (phase === 'ready') paintReady();
    try {
      analysis = await apiJSON('/api/profile/analysis', { method: 'POST', headers: csrfHeaders() });
      if (!active || token !== generation) return;
      onProfile(analysis);
      if (!analysis.analysis_used) error = t('GPT okuması şu an hazır değil. İlk zevk özetin kaydedildi; istersen yeniden deneyebilirsin.');
    } catch (err) { error = err.message; }
    finally { if (token === generation) { analyzing = false; if (active && phase === 'ready') paintReady(); } }
  }
  function paintTour() {
    buttons(); const slide = panels[tour];
    stage.innerHTML = `<section class="native-onboard native-in"><div class="native-eyebrow">${t(slide.eyebrow)}</div><div class="native-tour-icon">${icon(slide.icon)}</div><h1 class="native-tour-title">${t(slide.title)}</h1><p class="native-lede">${t(slide.body)}</p>${slide.preview}<div class="native-tour-progress">${panels.map((_, i) => `<span class="${i === tour ? 'is-active' : ''}"></span>`).join('')}</div><button id="native-tour-next" type="button" class="native-primary">${t(tour === panels.length - 1 ? 'İlk zevk okumamı gör' : 'Devam et')}${icon('arrow_forward')}</button><button id="native-tour-back" type="button" class="native-back">${t('Geri')}</button><div class="native-analysis-status">${icon(analyzing ? 'hourglass_top' : 'check_circle')}${t(analyzing ? 'Bu sırada GPT dört favorini okuyor…' : 'İlk zevk okuman hazır.')}</div></section>`;
    $('native-tour-next').addEventListener('click', () => { if (tour === panels.length - 1) { phase = 'ready'; paintReady(); } else { tour++; paintTour(); } });
    $('native-tour-back').addEventListener('click', () => { if (tour) { tour--; paintTour(); } else pick(); });
  }
  function paintReady() {
    buttons();
    const taste = analysis?.taste || {};
    const prose = (taste.analysis || []).join(' ') || taste.summary || t('Seçtiğin dört film, ilk sinema profilinin başlangıcı. Yeni film ve notlarınla bu okuma gelişecek.');
    stage.innerHTML = `<section class="native-onboard native-in"><div class="native-eyebrow">${icon('auto_awesome')}${t('İLK ZEVK OKUMAN')}</div><h1>${t('Sinemada')}<br/><em>${t('senin izin.')}</em></h1><div class="native-ready-posters">${selected.map(f => `<div>${poster(f)}</div>`).join('')}</div><div class="native-reading" role="status" aria-live="polite">${analyzing ? `<div class="native-reading-loader"></div><p>${t('Dört film arasındaki ortak hikâye aranıyor…')}</p>` : `<span class="native-reading-mark">“</span><p>${escapeHTML(prose)}</p>${taste.personality ? `<p class="native-reading-personality">${escapeHTML(taste.personality)}</p>` : ''}`}</div><p class="native-status">${escapeHTML(error)}</p>${error && !analyzing ? `<button id="native-analysis-retry" type="button" class="native-back">${t('Analizi yeniden dene')}</button>` : ''}<button id="native-onboard-finish" type="button" class="native-primary">${t('Movienotes’a gir')}${icon('arrow_forward')}</button><button id="native-ready-back" type="button" class="native-back">${t('Tanıtıma dön')}</button></section>`;
    $('native-onboard-finish').addEventListener('click', async () => {
      const button = $('native-onboard-finish'); button.disabled = true;
      try { await onComplete(); active = false; } catch (err) { error = err.message; paintReady(); }
    });
    $('native-analysis-retry')?.addEventListener('click', runAnalysis);
    $('native-ready-back').addEventListener('click', () => { phase = 'tour'; paintTour(); });
  }
  stage.addEventListener('click', event => {
    if (!active || phase !== 'pick' || saving) return;
    const remove = event.target.closest('[data-native-remove]');
    if (remove) { selected = selected.filter(f => f.id !== remove.dataset.nativeRemove); paintSelection(); return; }
    const pickButton = event.target.closest('[data-native-film]');
    if (!pickButton) return;
    const film = results[Number(pickButton.dataset.nativeFilm)];
    if (!film) return;
    const exists = selected.some(f => f.id === film.id);
    if (exists) selected = selected.filter(f => f.id !== film.id);
    else if (selected.length < 4) selected.push(film);
    paintSelection();
  });
  return {
    get active() { return active; },
    stop() { active = false; generation++; clearTimeout(timer); },
    async start(nextAccount) {
      account = nextAccount; generation++; active = true; phase = 'pick'; selected = []; analysis = null; error = ''; imported = ''; analyzing = false;
      showView('onboarding'); pick();
      try {
        const data = await apiJSON('/api/profile/me');
        if (!active || phase !== 'pick') return;
        const previous = data.favorite_films || [];
        if (!selected.length && previous.length === 4 && previous.every(f => f.film_id || (f.slug || '').startsWith('movie-'))) {
          selected = previous.map(f => ({ ...f, id: f.film_id || f.slug.slice(6), year: f.release_year })); paintSelection();
        }
      } catch (_) { /* The selection page remains usable on a fresh account. */ }
    },
  };
}
