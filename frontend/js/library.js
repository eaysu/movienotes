import { $, escapeHTML, safeImageURL } from './dom.js?v=20261117.3';
import { apiJSON } from './api.js?v=20261117.3';
import { csrfHeaders } from './auth.js?v=20261117.3';
import { t } from './i18n.js?v=20261117.3';
import { runLetterboxdImport } from './import-progress.js?v=20261117.3';

export function createLibraryPanel({ onCompose, onProfileChanged }) {
  let scope = 'watched', films = [], page = 1, timer, polling, epoch = 0, matching = null;
  const content = $('library-content'), status = $('library-status');
  function card(film, index, state = {}) {
    return `<article class="library-film"><div class="library-film-poster">${safeImageURL(film.poster_url) ? `<img src="${safeImageURL(film.poster_url)}" alt="" loading="lazy"/>` : '<span class="material-symbols-outlined">movie</span>'}</div><div class="library-film-info"><strong>${escapeHTML(film.title)}</strong><small>${escapeHTML(film.year || '')}${film.director ? ' · '+escapeHTML(film.director) : ''}</small>${state.rating != null ? `<span class="native-stars">★ ${escapeHTML(state.rating)} / 5</span>` : ''}<div class="library-film-actions">${matching ? `<button type="button" data-library-action="match" data-film-index="${index}">${t('Bu filmle eşleştir')}</button>` : `<button type="button" data-library-action="note" data-film-index="${index}">${t('Not yaz')}</button><button type="button" data-library-action="watchlist" data-film-index="${index}" aria-pressed="${Boolean(state.watchlist)}">${t(state.watchlist ? 'Listeden çıkar' : 'Listeme ekle')}</button><button type="button" data-library-action="watched" data-film-index="${index}" aria-pressed="${Boolean(state.watched)}">${t(state.watched ? 'İzledim ✓' : 'İzledim')}</button>`}</div></div></article>`;
  }
  async function load(append = false) {
    clearTimeout(polling);
    const id = ++epoch;
    status.textContent = t('Yükleniyor…');
    $('library-more').classList.add('hidden');
    document.querySelectorAll('[data-library-tab]').forEach(el => el.classList.toggle('is-active',el.dataset.libraryTab === scope));
    $('library-search').classList.toggle('hidden',scope === 'imports' || scope === 'archive');
    try {
      if (scope === 'archive') {
        const data = await apiJSON(`/api/library/archive?page=${page}`);
        if (id !== epoch) return;
        const html = (data.entries || []).map(entry => `<article class="library-import-job"><strong>${escapeHTML(entry.title)} (${escapeHTML(entry.year || '')})</strong><p class="native-status">${escapeHTML(entry.watched_on || '')}${entry.rewatch ? ' · '+t('Tekrar izleme') : ''}</p>${entry.user_rating != null ? `<span class="native-stars">★ ${escapeHTML(entry.user_rating)} / 5</span>` : ''}<p class="whitespace-pre-wrap">${escapeHTML(entry.review || t('Yorumsuz izleme kaydı'))}</p></article>`).join('');
        if (append) content.insertAdjacentHTML('beforeend',html); else content.innerHTML = html;
        status.textContent = t('Letterboxd’dan aktarılan özel arşivin. Buradaki tarihler kaynak kayıtların tarihidir.');
        $('library-more').classList.toggle('hidden',page*30 >= data.total);
        return;
      }
      if (scope === 'imports') {
        const data = await apiJSON('/api/library/imports');
        if (id !== epoch) return;
        films = data.unresolved || [];
        content.innerHTML = `<label class="native-import-upload" for="library-import-file"><span class="material-symbols-outlined">upload_file</span>${t('Letterboxd ZIP dosyası yükle')}<input id="library-import-file" type="file" accept=".zip,application/zip" class="sr-only"/></label><p class="native-status">${t('Aktarılan yorumlar özel arşivinde kalır; topluluk akışında otomatik yayımlanmaz.')}</p>${(data.imports || []).map(job => `<div class="library-import-job"><strong>${t(({queued:'Sırada',staging:'Alınıyor',running:'Filmler eşleştiriliyor',done:'Aktarım tamamlandı',failed:'Aktarım bekliyor'})[job.state] || job.state)}</strong><p>${escapeHTML(job.counts?.watched || 0)} ${t('film')} · ${escapeHTML(job.counts?.ratings || 0)} ${t('puan')} · ${escapeHTML(job.counts?.reviews || 0)} ${t('yorum')}</p><small>${escapeHTML(job.counts?.processed || 0)} / ${escapeHTML(job.counts?.records || 0)}</small></div>`).join('')}<h3 class="mt-5 text-sm">${t('Eşleşmesi kontrol edilecek filmler')}</h3>${films.map((film,i) => `<div class="library-match-row"><span>${escapeHTML(film.title)} (${escapeHTML(film.year || '')})</span><button type="button" data-library-action="resolve" data-film-index="${i}">${t('Film seç')}</button></div>`).join('')}`;
        $('library-import-file').addEventListener('change', event => upload(event.target.files?.[0]));
        status.textContent = films.length ? t('Belirsiz eşleşmeleri film seçerek tamamlayabilirsin.') : t('Yeni bir ZIP’i tekrar yüklemek kayıtlarını çoğaltmaz.');
        if ((data.imports || []).some(job => ['staging','queued','running','failed'].includes(job.state))) {
          polling = setTimeout(() => { if ($('dialog-library').open && scope === 'imports') load(); }, 4000);
        }
        return;
      }
      const query = $('library-search').value.trim();
      const data = await apiJSON(scope === 'search' ? `/api/catalog/search?q=${encodeURIComponent(query)}` : `/api/library?scope=${scope}&q=${encodeURIComponent(query)}&page=${page}`);
      if (id !== epoch) return;
      const rows = scope === 'search' ? (data.films || []).map(f => ({film:f})) : data.films || [];
      const offset = append ? films.length : 0;
      films = append ? films.concat(rows.map(r => r.film)) : rows.map(r => r.film);
      const html = rows.map((row,index) => card(row.film,index+offset,row)).join('');
      if (append) content.insertAdjacentHTML('beforeend',html); else content.innerHTML = html;
      status.textContent = matching ? t('Arşivdeki film için doğru sonucu seç.') : (films.length ? '' : t('Burada henüz film yok. Film bul sekmesinden başlayabilirsin.'));
      if (data.upstream_unavailable) status.textContent = t('Film servisi geçici olarak yanıt vermedi. Kayıtlı sonuçlar gösteriliyor.');
      $('library-more').classList.toggle('hidden',scope === 'search' || page*40 >= (data.total || 0));
    } catch (err) { if (id === epoch) status.textContent = err.message; }
  }
  async function upload(file) {
    if (!file) return;
    if (file.size > 16*1024*1024 || !/\.zip$/i.test(file.name)) { status.textContent = t('Lütfen 16 MB altında bir Letterboxd ZIP dosyası seç.'); return; }
    status.textContent = t('Arşiv yükleniyor…'); $('library-import-file').disabled = true;
    try {
      await runLetterboxdImport(file, { onDone: () => { onProfileChanged(); if ($('dialog-library').open && scope === 'imports') load(); } });
      await load();
    } catch (err) { status.textContent = err.message; if ($('library-import-file')) $('library-import-file').disabled = false; }
  }
  content.addEventListener('click', async event => {
    const button = event.target.closest('[data-library-action]'); if (!button) return;
    const film = films[Number(button.dataset.filmIndex)]; if (!film) return;
    const action = button.dataset.libraryAction;
    if (action === 'note') { $('dialog-library').close(); onCompose(film); return; }
    if (action === 'resolve') { matching = film.id; scope = 'search'; $('library-search').value = film.title; page = 1; load(); return; }
    button.disabled = true;
    try {
      if (action === 'match') {
        await apiJSON(`/api/library/matches/${matching}`,{method:'POST',headers:csrfHeaders({'Content-Type':'application/json'}),body:JSON.stringify({target_film_id:film.id})});
        matching = null; scope = 'imports'; load(); onProfileChanged(); return;
      }
      const next = action === 'watched' ? true : button.getAttribute('aria-pressed') !== 'true';
      await apiJSON(`/api/library/${film.id}`,{method:'PUT',headers:csrfHeaders({'Content-Type':'application/json'}),body:JSON.stringify({[action]:next})});
      status.textContent = t('Kaydedildi.'); button.setAttribute('aria-pressed',String(next));
      button.textContent = t(action === 'watched' ? 'İzledim ✓' : (next ? 'Listeden çıkar' : 'Listeme ekle'));
      onProfileChanged();
    } catch (err) { status.textContent = err.message; }
    finally { button.disabled = false; }
  });
  $('library-tabs').addEventListener('click',event => { const tab = event.target.closest('[data-library-tab]'); if (tab) { scope = tab.dataset.libraryTab; matching = null; page = 1; load(); } });
  $('library-search').addEventListener('input',() => { clearTimeout(timer); timer = setTimeout(() => { page = 1; load(); },280); });
  $('library-more').addEventListener('click',() => { page++; load(true); });
  $('dialog-library').addEventListener('close',() => { clearTimeout(polling); epoch++; });
  return { open() { matching = null; page = 1; $('dialog-library').showModal(); load(); } };
}
