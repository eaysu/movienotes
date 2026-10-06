export const $ = id => document.getElementById(id);

export function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);
}

export function safeImageURL(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === 'https:' ? escapeHTML(url.href) : '';
  } catch (_) {
    return '';
  }
}

const _FILM_SLUG_RE = /^[a-z0-9][a-z0-9-]{0,159}$/;

// Public Letterboxd film page for a slug (used to make posters clickable).
export function letterboxdFilmURL(slug) {
  const clean = String(slug || '').trim().toLowerCase();
  const short = /^boxd-((?:[0-9a-f]{2}){2,24})$/.exec(clean);
  if (short) {
    const id = short[1].match(/.{2}/g).map(hex => String.fromCharCode(parseInt(hex, 16))).join('');
    return /^[A-Za-z0-9]{2,24}$/.test(id) ? `https://boxd.it/${id}` : '';
  }
  return _FILM_SLUG_RE.test(clean) ? `https://letterboxd.com/film/${clean}/` : '';
}

// Failed artwork is replaced by the adjacent accessible text fallback.
export function posterErr(img) {
  img.hidden = true;
  const fallback = img.nextElementSibling;
  if (fallback && fallback.hasAttribute('hidden')) fallback.hidden = false;
}

window.posterErr = posterErr;
