import { escapeHTML, safeImageURL } from './dom.js?v=20261117.3';
import { apiJSON } from './api.js?v=20261117.3';
import { csrfHeaders } from './auth.js?v=20261117.3';
import { t } from './i18n.js?v=20261117.3';

// Full-screen progress for a Letterboxd ZIP import. The upload returns at once
// and matching continues on the server; this screen polls the job, shows the
// films as they are matched and can be dismissed while the work continues.
const POLL_MS = 1500;
const ROTATE_MS = 2600;
const icon = name => `<span class="material-symbols-outlined" aria-hidden="true">${name}</span>`;

export function runLetterboxdImport(file, { onDone } = {}) {
  // A modal <dialog> joins the top layer, above an already open library dialog.
  const root = document.createElement('dialog');
  root.className = 'import-progress';
  root.setAttribute('aria-labelledby', 'import-progress-title');
  root.innerHTML = `<div class="import-progress-card import-in">
    <h2 id="import-progress-title" class="import-progress-title">${t('Arşivin yükleniyor…')}</h2>
    <div class="import-stage" aria-hidden="true">
      <div class="import-poster import-poster-back is-left"></div>
      <div class="import-poster import-poster-back is-right"></div>
      <div class="import-poster import-poster-front"><div class="import-reel">${icon('movie')}</div></div>
    </div>
    <p class="import-caption" aria-live="polite"><strong>${t('Filmlerin hazırlanıyor')}</strong><small>${t('ZIP dosyan okunuyor')}</small></p>
    <div class="import-bar is-indeterminate" role="progressbar" aria-label="${t('Aktarım ilerlemesi')}" aria-valuemin="0" aria-valuemax="100"><span></span></div>
    <div class="import-meta"><span class="import-percent"></span><span class="import-eta"></span></div>
    <div class="import-stats"></div>
    <p class="import-note" role="status"></p>
    <button type="button" class="native-primary import-close" hidden>${t('Harika, kapat')}${icon('check')}</button>
  </div>`;
  document.body.appendChild(root);
  root.showModal();
  document.body.classList.add('import-progress-open');
  const q = selector => root.querySelector(selector);
  // The screen stays until the import ends: the member is not sent anywhere
  // else while the archive is still being matched.
  const closeButton = q('.import-close');

  let timer = 0, rotation = 0, recent = [], shown = -1, closed = false, finished = false, samples = [];

  function close() {
    if (closed) return;
    closed = true; clearTimeout(timer); clearInterval(rotation);
    root.classList.add('is-leaving');
    document.body.classList.remove('import-progress-open');
    setTimeout(() => { root.close(); root.remove(); }, 240);
  }
  // Chrome honours a prevented `cancel` only once per user activation, so the
  // Escape key is stopped first and an unexpected close reopens the dialog.
  root.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); if (!closeButton.hidden) close(); } });
  root.addEventListener('cancel', event => { event.preventDefault(); if (!closeButton.hidden) close(); });
  root.addEventListener('close', () => { if (!closed && root.isConnected) root.showModal(); });
  closeButton.addEventListener('click', close);

  function poster(film) {
    const src = safeImageURL(film?.poster_url);
    return src ? `<img src="${src}" alt="" loading="eager"/>` : `<div class="import-reel">${icon('movie')}</div>`;
  }
  function rotate() {
    if (!recent.length) return;
    shown = (shown + 1) % recent.length;
    const film = recent[recent.length - 1 - shown];
    const front = q('.import-poster-front');
    front.classList.remove('is-swapping'); void front.offsetWidth; front.classList.add('is-swapping');
    front.innerHTML = poster(film);
    q('.is-left').innerHTML = poster(recent[(recent.length - 2 - shown + recent.length * 2) % recent.length]);
    q('.is-right').innerHTML = poster(recent[(recent.length - shown + recent.length * 2) % recent.length]);
    const meta = [film.year, film.director].filter(Boolean).map(escapeHTML).join(' · ');
    q('.import-caption').innerHTML = `<strong>${escapeHTML(film.title)}</strong><small>${meta || t('Eşleştirildi')}</small>`;
  }
  function eta(done, total) {
    const now = Date.now();
    samples.push([now, done]); samples = samples.filter(([at]) => now - at < 30000);
    if (samples.length < 3 || done >= total) return '';
    const [[firstAt, firstDone]] = samples;
    const rate = (done - firstDone) / ((now - firstAt) / 1000);
    if (rate <= 0) return '';
    const seconds = Math.round((total - done) / rate);
    return seconds < 60 ? t('bir dakikadan az kaldı') : t('yaklaşık {minutes} dk kaldı', { minutes: Math.ceil(seconds / 60) });
  }
  function paint(job, upload) {
    const counts = job?.counts || {};
    const total = Number(counts.records || 0), done = Math.min(Number(counts.processed || 0), total);
    const percent = total ? Math.floor((done / total) * 100) : 0;
    const bar = q('.import-bar');
    bar.classList.remove('is-indeterminate');
    bar.setAttribute('aria-valuenow', String(percent));
    bar.querySelector('span').style.width = `${Math.max(percent, 2)}%`;
    q('.import-percent').textContent = total ? `%${percent} · ${t('{done} / {total} kayıt', { done, total })}` : '';
    q('.import-eta').textContent = job?.state === 'running' ? eta(done, total) : '';
    const stats = [
      [upload?.watched_count ?? counts.watched ?? 0, 'film', 'movie'],
      [counts.ratings ?? 0, 'puan', 'star'],
      [upload?.review_count ?? counts.reviews ?? 0, 'yorum', 'rate_review'],
      [counts.unresolved ?? 0, 'kontrol edilecek', 'help'],
    ];
    q('.import-stats').innerHTML = stats.map(([n, label, name]) => `<span>${icon(name)}<strong>${escapeHTML(n)}</strong>${t(label)}</span>`).join('');
    if (Array.isArray(counts.recent) && counts.recent.length) {
      const first = !recent.length;
      recent = counts.recent;
      if (first) { rotate(); rotation = setInterval(rotate, ROTATE_MS); }
    }
    const title = q('.import-progress-title'), note = q('.import-note');
    if (job?.state === 'done') {
      finished = true;
      title.textContent = t('Aktarım tamamlandı');
      root.classList.add('is-done');
      note.textContent = counts.unresolved ? t('{count} filmin eşleşmesini Filmlerim → Arşiv aktarımı bölümünden seçebilirsin.', { count: counts.unresolved }) : t('Bütün filmlerin rafında.');
      closeButton.hidden = false; closeButton.focus();
    } else if (job?.state === 'failed') {
      title.textContent = t('Filmlerin eşleştiriliyor');
      note.textContent = t('Film servisi kısa bir süre yanıt vermedi. Aktarım kaldığı yerden kendiliğinden devam edecek.');
    } else {
      title.textContent = t(job?.state === 'running' ? 'Filmlerin eşleştiriliyor' : 'Aktarım sırada');
      note.textContent = t('Filmlerin rafına yerleşiyor; bu ekran aktarım bitince kapanabilir.');
    }
  }
  async function poll(jobId, upload) {
    if (closed && finished) return;
    try {
      const data = await apiJSON('/api/library/imports?progress=true');
      const job = (data.imports || []).find(item => item.id === jobId) || (data.imports || [])[0];
      if (!closed) paint(job, upload);
      if (job?.state === 'done') { onDone?.(job); return; }
    } catch (_) { /* A missed poll is retried; the job itself is durable. */ }
    timer = setTimeout(() => poll(jobId, upload), POLL_MS);
  }

  const done = (async () => {
    try {
      const upload = await apiJSON('/api/profile/import-letterboxd', { method: 'POST', headers: csrfHeaders({ 'Content-Type': 'application/zip' }), body: file });
      paint(upload.import, upload);
      if (upload.import?.state === 'done') onDone?.(upload.import);
      else poll(upload.import?.id, upload);
      return upload;
    } catch (err) {
      q('.import-progress-title').textContent = t('Arşiv aktarılamadı');
      q('.import-bar').classList.remove('is-indeterminate');
      root.classList.add('is-error');
      q('.import-note').textContent = err.message;
      closeButton.textContent = t('Kapat'); closeButton.hidden = false; closeButton.focus();
      throw err;
    }
  })();
  return done;
}
