import { escapeHTML, safeImageURL, letterboxdFilmURL } from './dom.js?v=20260902.15';
import { t } from './i18n.js?v=20260920.14';

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
  // No mobile-flat here: the reasoning now sits inside a padded card, so it
  // keeps its own inset instead of bleeding to the screen edge.
  return `<div class="rounded-xl border border-primary-container/25 bg-primary-container/[0.07] p-3">
      <p class="flex items-center gap-2 font-label-sm text-label-sm uppercase tracking-[.18em] text-primary-container mb-1">
        <span class="material-symbols-outlined text-[15px]" style="font-variation-settings:'FILL' 1">auto_awesome</span>${t('Sana neden önerdik?')}
      </p>
      <p class="font-body-md text-body-md text-on-surface leading-relaxed line-clamp-5">${escapeHTML(film.reason)}</p>
    </div>`;
}
// ── Compact pick card ─────────────────────────────────────────────────────
// One recommendation has to be readable without scrolling, so the poster is a
// thumbnail beside the title rather than a full-bleed image above it: identity
// on the top row, then the genres and the reasoning that earn the scroll-free
// decision.
function buildPickCard(film, { badge } = {}) {
  const title = escapeHTML(film.title);
  const director = escapeHTML(film.director);
  const year = escapeHTML(film.year);
  const posterURL = safeImageURL(film.poster_url);
  // Letterboxd's own community average, on the five-star scale members rate
  // in. TMDb's ten-point vote is a different crowd, so it is not shown as a
  // stand-in when the Letterboxd page could not be read.
  const average = Number(film.letterboxd_rating);
  const rating = average > 0
    ? `<div class="mt-1.5 flex items-center gap-1 text-on-surface-variant/70">
         <span class="material-symbols-outlined text-[14px] text-primary-container" style="font-variation-settings:'FILL' 1">star</span>
         <span class="font-label-md text-label-md">${average.toFixed(1)}<span class="text-on-surface-variant/45">/5</span></span>
         <span class="font-label-sm text-label-sm text-on-surface-variant/45">Letterboxd</span>
       </div>`
    : '';
  const genres = (film.genres || []).slice(0, 4).map(g =>
    `<span class="px-2.5 py-1 rounded-full bg-surface-variant text-on-surface-variant font-label-sm text-label-sm border border-outline-variant/20">${escapeHTML(g)}</span>`
  ).join('');

  const poster = posterURL
    ? `<img alt="${title}" draggable="false"
          class="w-full h-full object-cover object-center transition-transform duration-500 group-hover:scale-[1.04]"
          src="${posterURL}" loading="lazy"/>`
    : `<div class="w-full h-full flex items-center justify-center bg-surface-container">
          <span class="material-symbols-outlined text-[32px] text-on-surface-variant/20">movie</span>
       </div>`;

  return `
    <article class="tilt-card glass-panel rounded-xl overflow-hidden group p-4 flex flex-col gap-3">
      <div class="flex items-start gap-3.5">
        <div class="relative w-[92px] shrink-0 aspect-[2/3] overflow-hidden rounded-lg bg-surface-container">
          ${posterLink(poster, film)}
          ${badge}
        </div>
        <div class="min-w-0 flex-1">
          <h3 class="font-headline-md text-[20px] leading-tight text-on-surface break-words">${title}</h3>
          ${film.director ? `<div class="mt-1.5 font-label-md text-label-md text-tertiary-container break-words">${director}</div>` : ''}
          ${film.year ? `<div class="mt-0.5 font-label-sm text-label-sm text-on-surface-variant/60">${year}</div>` : ''}
          ${rating}
        </div>
      </div>
      ${genres ? `<div class="flex flex-wrap gap-1.5">${genres}</div>` : ''}
      ${overviewBlock(film)}
      ${whyBlock(film)}
    </article>`;
}

function buildHeroCard(film) {
  return buildPickCard(film, {
    badge: '<div class="absolute top-1.5 left-1.5 px-2 py-0.5 rounded-full bg-primary-container/90 backdrop-blur-sm font-label-sm text-label-sm text-on-primary-container font-bold">#1</div>',
  });
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
  return buildPickCard(film, {
    badge: `<div class="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded-full bg-tertiary-container/90 backdrop-blur-sm">
          <span class="material-symbols-outlined text-on-tertiary-container" style="font-size:14px;font-variation-settings:'FILL' 1">shuffle</span>
        </div>`,
  });
}

return { buildHeroCard, buildAltCard, buildRandomCard };
}
