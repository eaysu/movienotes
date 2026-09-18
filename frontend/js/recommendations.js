import { escapeHTML, safeImageURL, letterboxdFilmURL } from './dom.js?v=20260902.15';
import { t } from './i18n.js?v=20260918.2';

export function createRecommendationCards() {
// Make a poster clickable through to its Letterboxd page.
function posterLink(inner, film) {
  const href = letterboxdFilmURL(film && film.slug);
  return href
    ? `<a href="${href}" target="_blank" rel="noopener" title="${escapeHTML(film.title || '')} — Letterboxd" class="block w-full h-full">${inner}</a>`
    : inner;
}

// Konu özeti kartta yer almıyor: okunması gereken tek paragraf "sana neden
// önerdik" — filmin kendi tanıtımı Letterboxd bağlantısının ardında.
function overviewBlock() {
  return '';
}

// "Sana neden önerdik?" — the LLM's reasoning for this pick.
function whyBlock(film) {
  if (!film.reason) return '';
  return `<div class="mobile-flat mobile-flat--tight rounded-xl border border-primary-container/25 bg-primary-container/[0.07] p-4">
      <p class="flex items-center gap-2 font-label-sm text-label-sm uppercase tracking-[.18em] text-primary-container mb-1.5">
        <span class="material-symbols-outlined text-[15px]" style="font-variation-settings:'FILL' 1">auto_awesome</span>${t('Sana neden önerdik?')}
      </p>
      <p class="font-body-md text-body-md text-on-surface leading-relaxed">${escapeHTML(film.reason)}</p>
    </div>`;
}
function buildHeroCard(film) {
  const title = escapeHTML(film.title);
  const director = escapeHTML(film.director);
  const year = escapeHTML(film.year);
  const posterURL = safeImageURL(film.poster_url);
  const genres = (film.genres || []).slice(0, 4).map(g =>
    `<span class="px-3 py-1 rounded-full bg-surface-variant text-on-surface-variant font-label-sm text-label-sm border border-outline-variant/20">${escapeHTML(g)}</span>`
  ).join('');

  const poster = posterURL
    ? `<img alt="${title}" draggable="false"
          class="w-full h-full object-cover object-center transition-transform duration-700 group-hover:scale-[1.03]"
          src="${posterURL}" loading="lazy"/>`
    : `<div class="w-full h-full flex items-center justify-center bg-surface-container">
          <span class="material-symbols-outlined text-[64px] text-on-surface-variant/20">movie</span>
       </div>`;

  return `
    <article class="tilt-card glass-panel rounded-xl overflow-hidden group flex flex-col md:flex-row gap-0">
      <div class="md:w-[260px] shrink-0 aspect-[2/3] md:aspect-auto md:h-auto overflow-hidden relative bg-surface-container">
        ${posterLink(poster, film)}
        <div class="absolute inset-0 bg-gradient-to-t from-surface-container-lowest/60 via-transparent to-transparent md:bg-gradient-to-r md:from-transparent md:to-surface-container-lowest/30 pointer-events-none"></div>
        <div class="absolute top-3 left-3 px-3 py-1 rounded-full bg-primary-container/90 backdrop-blur-sm font-label-md text-label-md text-on-primary-container font-bold">#1</div>
      </div>
      <div class="p-stack-lg flex flex-col gap-stack-md flex-grow justify-center">
        <div>
          <h3 class="font-headline-lg text-headline-lg text-on-surface leading-tight">
            ${title}
            ${film.year ? `<span class="font-body-lg text-body-lg text-on-surface-variant/60 ml-2">${year}</span>` : ''}
          </h3>
          ${film.director ? `<div class="font-label-md text-label-md text-tertiary-container mt-unit">${director}</div>` : ''}
        </div>
        ${genres ? `<div class="flex flex-wrap gap-stack-sm">${genres}</div>` : ''}
        ${overviewBlock(film)}
        ${whyBlock(film)}
      </div>
    </article>`;
}

// ── Alt card builder (portrait grid) ──────────────────────────────────────
function buildAltCard(film, idx) {
  const title = escapeHTML(film.title);
  const director = escapeHTML(film.director);
  const year = escapeHTML(film.year);
  const posterURL = safeImageURL(film.poster_url);
  const accentColors = ['text-primary-container', 'text-secondary-container', 'text-tertiary-container', 'text-primary-container'];
  const ac = accentColors[idx % accentColors.length];
  const poster = posterURL
    ? `<img alt="${title}" draggable="false"
          class="w-full h-full object-cover group-hover:scale-[1.04] transition-transform duration-500"
          src="${posterURL}" loading="lazy"/>`
    : `<div class="w-full h-full flex items-center justify-center bg-surface-container">
          <span class="material-symbols-outlined text-[40px] text-on-surface-variant/20">movie</span>
       </div>`;
  // Yan kartlarda da konu özeti yok; sebep paragrafı kalıyor.
  const shortOverview = '';
  const shortReason = film.reason
    ? `<div class="mt-1 rounded-lg border border-primary-container/20 bg-primary-container/[0.06] p-2.5">
         <p class="font-label-sm text-[9px] uppercase tracking-[.14em] text-primary-container mb-1">${t('Sana neden önerdik?')}</p>
         <p class="font-label-sm text-label-sm text-on-surface-variant leading-relaxed line-clamp-4">${escapeHTML(film.reason)}</p>
       </div>`
    : '';
  return `
    <article class="tilt-card glass-panel rounded-xl overflow-hidden group flex flex-col overflow-safe">
      <div class="w-full aspect-[2/3] overflow-hidden relative bg-surface-container shrink-0">
        ${posterLink(poster, film)}
        <div class="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-surface-container-lowest/80 to-transparent pointer-events-none"></div>
        <div class="absolute top-2 left-2 w-7 h-7 rounded-full bg-surface-container/80 backdrop-blur-sm flex items-center justify-center font-bold text-xs ${ac}">#${idx + 2}</div>
      </div>
      <div class="p-stack-sm flex flex-col gap-unit flex-grow">
        <h4 class="font-label-md text-label-md text-on-surface line-clamp-2 leading-snug">${title}${film.year ? ` <span class="text-on-surface-variant/60">(${year})</span>` : ''}</h4>
        ${film.director ? `<span class="font-label-sm text-label-sm text-on-surface-variant/70">${director}</span>` : ''}
        ${shortOverview}
        ${shortReason}
      </div>
    </article>`;
}

// ── Random card builder ────────────────────────────────────────────────────
function buildRandomCard(film) {
  const title = escapeHTML(film.title);
  const director = escapeHTML(film.director);
  const year = escapeHTML(film.year);
  const posterURL = safeImageURL(film.poster_url);
  const genres = (film.genres || []).slice(0, 4).map(g =>
    `<span class="px-3 py-1 rounded-full bg-surface-variant text-on-surface-variant font-label-sm text-label-sm border border-outline-variant/20">${escapeHTML(g)}</span>`
  ).join('');

  const poster = posterURL
    ? `<img alt="${title}" draggable="false"
          class="w-full h-full object-cover object-center transition-transform duration-700 group-hover:scale-[1.03]"
          src="${posterURL}" loading="lazy"/>`
    : `<div class="w-full h-full flex items-center justify-center bg-surface-container">
          <span class="material-symbols-outlined text-[64px] text-on-surface-variant/20">movie</span>
       </div>`;

  const rating = film.vote_average && film.vote_average > 0
    ? `<div class="flex items-center gap-1 text-on-surface-variant/60 mt-unit">
         <span class="material-symbols-outlined text-[14px]" style="font-variation-settings:'FILL' 1">star</span>
         <span class="font-label-md text-label-md">${film.vote_average.toFixed(1)}</span>
       </div>`
    : '';

  return `
    <article class="reco-card glass-panel rounded-xl overflow-hidden group flex flex-col md:flex-row gap-0">
      <div class="md:w-[260px] shrink-0 aspect-[2/3] md:aspect-auto md:h-auto overflow-hidden relative bg-surface-container">
        ${posterLink(poster, film)}
        <div class="absolute inset-0 bg-gradient-to-t from-surface-container-lowest/60 via-transparent to-transparent md:bg-gradient-to-r md:from-transparent md:to-surface-container-lowest/30 pointer-events-none"></div>
        <div class="absolute top-3 left-3 px-2 py-1 rounded-full bg-tertiary-container/90 backdrop-blur-sm">
          <span class="material-symbols-outlined text-on-tertiary-container" style="font-size:16px;font-variation-settings:'FILL' 1">shuffle</span>
        </div>
      </div>
      <div class="p-stack-lg flex flex-col gap-stack-md flex-grow justify-center">
        <div>
          <h3 class="font-headline-lg text-headline-lg text-on-surface leading-tight">
            ${title}
            ${film.year ? `<span class="font-body-lg text-body-lg text-on-surface-variant/60 ml-2">${year}</span>` : ''}
          </h3>
          ${film.director ? `<div class="font-label-md text-label-md text-tertiary-container mt-unit">${director}</div>` : ''}
          ${rating}
        </div>
        ${genres ? `<div class="flex flex-wrap gap-stack-sm">${genres}</div>` : ''}
        ${overviewBlock(film)}
        ${whyBlock(film)}
      </div>
    </article>`;
}

return { buildHeroCard, buildAltCard, buildRandomCard };
}
