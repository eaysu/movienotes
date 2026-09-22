import { escapeHTML, safeImageURL, letterboxdFilmURL } from './dom.js?v=20260902.15';
import { t } from './i18n.js?v=20260920.23';

export function directorFilmTile(film) {
  const poster = safeImageURL(film.poster_url);
  const title = escapeHTML(film.title || '');
  const year = film.year ? escapeHTML(String(film.year)) : '';
  const href = letterboxdFilmURL(film.slug);
  const vote = film.user_rating
    ? `<span class="absolute inset-x-1 bottom-1 flex items-center justify-center gap-0.5 rounded bg-black/80 py-0.5 text-[10px] font-bold text-primary-container"><span class="material-symbols-outlined text-[11px]" style="font-variation-settings:'FILL' 1">star</span>${Number(film.user_rating).toFixed(1)}</span>`
    : '';
  const textFallback = `<div class="absolute inset-0 flex items-center justify-center p-1.5 text-center"><span class="text-[9px] leading-tight text-on-surface-variant/70 line-clamp-4">${title}</span></div>`;
  const inner = poster
    ? `<img src="${poster}" alt="${title}" loading="lazy" onerror="posterErr(this)" class="absolute inset-0 w-full h-full object-cover"/><div hidden>${textFallback}</div>`
    : textFallback;
  const art = `<div class="relative aspect-[2/3] rounded-lg overflow-hidden bg-surface-container ring-1 ring-outline-variant/20 transition-transform duration-200 hover:-translate-y-0.5 hover:ring-primary-container/40">${inner}${vote}</div>`;
  return `<div>
    ${href ? `<a href="${href}" target="_blank" rel="noopener" title="${title} — Letterboxd">${art}</a>` : art}
    <p class="mt-1 text-[10px] leading-tight text-on-surface-variant line-clamp-2">${title}</p>
    ${year ? `<span class="text-[9px] text-on-surface-variant/45">${year}</span>` : ''}
  </div>`;
}

export function directorFilmGrid(films, id, hidden, hasMore = false) {
  return `<div id="${id}" data-full-loaded="${hasMore ? 'false' : 'true'}" class="collapse-y${hidden ? '' : ' open'} mt-3"><div class="collapse-inner">
    <div class="grid grid-cols-3 sm:grid-cols-4 gap-2">${films.map(directorFilmTile).join('')}</div>
    ${hasMore ? `<p data-director-loading class="mt-3 text-center font-label-sm text-label-sm text-on-surface-variant/50">${t('Açıldığında tüm filmler yüklenir')}</p>` : ''}
  </div></div>`;
}

export function directorAvatar(director, sizeClass) {
  const photo = safeImageURL(director && director.photo_url);
  const initial = escapeHTML((((director && director.name) || '?').trim()[0] || '?').toUpperCase());
  if (photo) {
    return `<span class="${sizeClass} shrink-0 rounded-full overflow-hidden block bg-surface-container ring-1 ring-primary-container/30">
      <img src="${photo}" alt="${initial}" loading="lazy" onerror="posterErr(this)" class="w-full h-full object-cover"/>
      <span hidden class="w-full h-full flex items-center justify-center font-headline-md text-primary-container">${initial}</span>
    </span>`;
  }
  return `<span class="${sizeClass} shrink-0 rounded-full flex items-center justify-center font-headline-md text-primary-container" style="background:rgba(0,224,84,0.10);border:1px solid rgba(0,224,84,0.30)">${initial}</span>`;
}
