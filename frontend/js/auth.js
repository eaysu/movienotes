import { $ } from './dom.js?v=20260902.15';
import { t, getLocale } from './i18n.js?v=20260920.21';

export function cookieValue(name) {
  const prefix = `${name}=`;
  const item = document.cookie.split('; ').find(part => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : '';
}

export function csrfHeaders(extra = {}) {
  const token = cookieValue('mb_csrf');
  // Prose the server writes — recommendation reasons, taste analysis — has to
  // follow the language the app is actually rendering in. The shell resolves
  // that from a stored preference the account may never have been told about,
  // so Accept-Language is not a stand-in for it.
  const headers = { ...extra, 'X-Movienotes-Locale': getLocale() };
  return token ? { ...headers, 'X-CSRF-Token': token } : headers;
}

export function setAuthMessage(message, isError = false) {
  const el = $('auth-message');
  if (!message) { el.classList.add('hidden'); return; }
  el.textContent = message;
  el.className = `mt-4 rounded-lg px-4 py-3 font-body-md text-body-md ${isError ? 'bg-error-container/30 text-error' : 'bg-primary-container/10 text-primary-container'}`;
}

export function setPasswordVisibility(button, visible) {
  const input = $(button.dataset.passwordToggle);
  if (!input) return;
  input.type = visible ? 'text' : 'password';
  button.setAttribute('aria-pressed', String(visible));
  button.setAttribute('aria-label', t(visible ? 'Şifreyi gizle' : 'Şifreyi göster'));
  // Eye state mirrors the field: open eye = password shown, slashed eye = hidden.
  button.querySelector('.material-symbols-outlined').textContent = visible
    ? 'visibility'
    : 'visibility_off';
}

export function resetPasswordVisibility() {
  document.querySelectorAll('[data-password-toggle]').forEach(button => {
    setPasswordVisibility(button, false);
  });
}

export function setAuthMode(mode) {
  resetPasswordVisibility();
  // Deneme oturumunda hesap diye bir şey yok: giriş/kayıt sekmeleri ve parola
  // alanları tamamen kapanır, geriye tek bir kullanıcı adı kutusu kalır.
  if (mode === 'sandbox') {
    const title = $('auth-title');
    if (title) title.textContent = t('Bir Letterboxd profili dene');
    for (const id of ['login-form', 'register-form', 'verify-panel', 'reset-panel', 'auth-tabs']) {
      $(id).classList.add('hidden');
      $(id).classList.remove('flex');
    }
    $('sandbox-form').classList.remove('hidden');
    $('sandbox-form').classList.add('flex');
    setAuthMessage(null);
    return;
  }
  $('sandbox-form').classList.add('hidden');
  $('sandbox-form').classList.remove('flex');
  const login = mode === 'login';
  const title = $('auth-title');
  if (title) title.textContent = t(login ? 'Movienotes’a giriş yap' : 'Movienotes’da hesap oluştur');
  $('login-form').classList.toggle('hidden', !login);
  $('register-form').classList.toggle('hidden', login);
  $('register-form').classList.toggle('flex', !login);
  $('verify-panel').classList.add('hidden');
  $('verify-panel').classList.remove('flex');
  $('reset-panel').classList.add('hidden');
  $('reset-panel').classList.remove('flex');
  $('auth-tabs').classList.remove('hidden');
  $('auth-tab-login').className = `py-2.5 rounded-lg font-label-md text-label-md uppercase ${login ? 'bg-primary-container text-black' : 'bg-surface-variant text-on-surface-variant border border-outline-variant/30'}`;
  $('auth-tab-register').className = `py-2.5 rounded-lg font-label-md text-label-md uppercase ${!login ? 'bg-primary-container text-black' : 'bg-surface-variant text-on-surface-variant border border-outline-variant/30'}`;
  setAuthMessage(null);
}
