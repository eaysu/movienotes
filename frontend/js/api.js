import { t } from './i18n.js?v=20260918.2';

export const API_BASE = window.__API_BASE__ || '';

let activeRequest = null;
let sessionRefreshPromise = null;

function csrfToken() {
  const prefix = 'mb_csrf=';
  const item = document.cookie.split('; ').find(part => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : '';
}

// Most endpoints raise HTTPException with a plain-string detail, but a
// request-shape error (e.g. a bad username) never reaches our code — FastAPI's
// own Pydantic validation rejects it first, as a `detail` array of
// {msg, loc, ...} objects. Without this, `new Error(payload.detail)` on that
// array silently stringifies to "[object Object]" and the real message —
// e.g. "Letterboxd kullanıcı adı 2–15 karakter olmalı" — never reaches the user.
function errorDetailMessage(payload) {
  const detail = payload?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0];
    const msg = typeof first === 'string' ? first : first?.msg;
    if (msg) return String(msg).replace(/^Value error,\s*/, '');
  }
  return '';
}

function apiError(response, payload) {
  const error = new Error(t(errorDetailMessage(payload) || `HTTP ${response.status}`));
  error.status = response.status;
  error.code = response.headers.get('X-Error-Code') || payload.code || '';
  return error;
}

function canRecoverSession(path) {
  // A refresh request must never recursively refresh itself.  All normal API
  // calls — including /auth/me — may have met an access cookie that expired
  // while an installed app sat in the background.
  return path !== '/api/auth/refresh' && path !== '/api/auth/login';
}

function withCurrentCsrf(options) {
  const headers = new Headers(options.headers || {});
  if (headers.has('X-CSRF-Token')) {
    const token = csrfToken();
    if (token) headers.set('X-CSRF-Token', token);
  }
  return { ...options, headers };
}

async function refreshExpiredSession() {
  if (sessionRefreshPromise) return sessionRefreshPromise;
  const token = csrfToken();
  if (!token) throw new Error('Oturum yenilenemedi.');
  sessionRefreshPromise = (async () => {
    const response = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'X-CSRF-Token': token },
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw apiError(response, payload);
    return payload;
  })();
  try {
    return await sessionRefreshPromise;
  } finally {
    sessionRefreshPromise = null;
  }
}

function isSafeRetry(options) {
  return String(options.method || 'GET').toUpperCase() === 'GET';
}

export async function apiJSON(path, options = {}, attempt = 0, recovered = false) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      credentials: 'same-origin',
      // Auth answers must never be revived from an HTTP cache after a login,
      // logout or a refresh-token rotation.
      cache: path.startsWith('/api/auth/') ? 'no-store' : options.cache,
      ...options,
    });
  } catch (error) {
    // A waking PWA can beat Render's first live connection by a few hundred
    // milliseconds. Retrying only safe reads avoids duplicating writes.
    if (attempt === 0 && isSafeRetry(options)) {
      await new Promise(resolve => setTimeout(resolve, 350));
      return apiJSON(path, options, 1, recovered);
    }
    throw error;
  }
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) {
    // One expired access token used to make every background poll surface
    // "Oturum geçersiz" until the member manually reloaded the installed app.
    // Share one refresh across that whole burst, then replay the original
    // request with the CSRF cookie that the refresh just rotated.
    if (response.status === 401 && !recovered && canRecoverSession(path)) {
      await refreshExpiredSession();
      return apiJSON(path, withCurrentCsrf(options), attempt, true);
    }
    if (attempt === 0 && isSafeRetry(options) && response.status >= 500) {
      await new Promise(resolve => setTimeout(resolve, 350));
      return apiJSON(path, options, 1, recovered);
    }
    throw apiError(response, payload);
  }
  return payload;
}

export function beginApiRequest(timeoutMs) {
  if (activeRequest) {
    activeRequest.replaced = true;
    activeRequest.controller.abort();
    clearTimeout(activeRequest.timer);
  }
  const request = {
    controller: new AbortController(),
    replaced: false,
    cancelled: false,
    timedOut: false,
    timer: null,
  };
  request.timer = setTimeout(() => {
    request.timedOut = true;
    request.controller.abort();
  }, timeoutMs);
  activeRequest = request;
  return request;
}

export function finishApiRequest(request) {
  if (!request) return;
  clearTimeout(request.timer);
  if (activeRequest === request) activeRequest = null;
}

export function cancelActiveApiRequest() {
  if (!activeRequest) return;
  activeRequest.cancelled = true;
  activeRequest.controller.abort();
  clearTimeout(activeRequest.timer);
  activeRequest = null;
}

export async function assertStreamResponse(response) {
  if (!response.ok) {
    let detail = '';
    try { detail = errorDetailMessage(await response.json()); } catch (_) {}
    if (response.status === 429) {
      const retry = Number(response.headers.get('Retry-After') || 0);
      const suffix = retry > 0 ? ` Yaklaşık ${retry} saniye sonra tekrar dene.` : '';
      throw new Error((detail || 'İstek sınırına ulaşıldı.') + suffix);
    }
    throw new Error(detail || `Sunucu HTTP ${response.status} hatası döndürdü.`);
  }
  if (!response.body) throw new Error('Yanıt akışı başlatılamadı.');
}

export function streamErrorMessage(error, request, fallback) {
  if (request?.timedOut) return 'İstek zaman aşımına uğradı. Lütfen tekrar deneyin.';
  if (error?.name === 'AbortError') return null;
  return error?.message || fallback;
}

export function scrapeErrorMessage(event) {
  const messages = {
    profile_not_found: 'Letterboxd kullanıcısı bulunamadı. Kullanıcı adını kontrol edip tekrar dene.',
    profile_or_list_private: 'Profil veya liste gizli. Movienotes yalnızca herkese açık Letterboxd verilerini okuyabilir.',
    list_empty: 'Bu kullanıcının ilgili film listesi boş. Dolu bir watchlist ile tekrar dene.',
    letterboxd_blocked: 'Letterboxd erişimi geçici olarak sınırladı. Birkaç dakika sonra tekrar dene.',
    markup_changed: 'Letterboxd sayfa yapısı değişmiş olabilir. Bu hata teknik inceleme için kaydedildi.',
    network_error: 'Letterboxd ağına ulaşılamadı. Bağlantıyı kontrol edip tekrar dene.',
  };
  return messages[event?.code] || event?.detail || 'Film verileri alınamadı.';
}
