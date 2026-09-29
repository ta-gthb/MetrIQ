/* Choosing a new password, after following a reset link.
 *
 * The identity provider owns the credential: the recovery token in the URL is
 * sent to Supabase and nowhere else, and MetrIQ never sees the password (audit
 * item 4). The token is stripped from the address bar as soon as it is read, so
 * it does not survive in the browser history or in a copied URL.
 */

import { loadAuthConfig } from './api.js';
import { escapeHtml, toast } from './ui.js';
import { initTheme } from './theme.js';
import { startClocks } from './clock.js';

initTheme();
startClocks();

const notice = document.getElementById('notice');
const form = document.getElementById('reset-form');
const password = document.getElementById('password');
const confirmPassword = document.getElementById('confirm');
const submit = document.getElementById('submit');

let supabaseConfig = null;

function fail(message) {
  form.hidden = true;
  notice.innerHTML = `<div class="banner fail"><div>${escapeHtml(message)}</div></div>`;
}

/* Supabase returns the recovery token in the URL fragment, and reports an
 * expired or already-used link in the same place. */
function readRecovery() {
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  const query = new URLSearchParams(window.location.search);
  const pick = (key) => fragment.get(key) || query.get(key) || '';
  return { token: pick('access_token'), error: pick('error_description') || pick('error') };
}

const recovery = readRecovery();
if (window.location.hash || window.location.search) {
  window.history.replaceState(null, '', window.location.pathname);
}

(async function start() {
  const config = await loadAuthConfig();

  if (!config.supabase) {
    fail('This deployment does not offer password reset by email. Ask an administrator to set one.');
    return;
  }
  supabaseConfig = config.supabase;

  if (!recovery.token) {
    fail(recovery.error
      ? `That reset link is no longer usable (${recovery.error}). Request a new one from the sign-in page.`
      : 'This page needs the link from the reset email. Request one from the sign-in page.');
    return;
  }
  form.hidden = false;
  password.focus();
})();

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  [password, confirmPassword].forEach((field) => field.classList.remove('invalid'));

  if (password.value.length < 8) {
    password.classList.add('invalid');
    toast('Use at least 8 characters.', 'error');
    return;
  }
  if (password.value !== confirmPassword.value) {
    confirmPassword.classList.add('invalid');
    toast('The two passwords do not match.', 'error');
    return;
  }

  submit.disabled = true;
  submit.textContent = 'Saving\u2026';
  try {
    const response = await fetch(`${supabaseConfig.auth_url}/user`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        apikey: supabaseConfig.anon_key,
        Authorization: `Bearer ${recovery.token}`,
      },
      body: JSON.stringify({ password: password.value }),
    });

    if (!response.ok) {
      let message = `Could not set the password (${response.status}).`;
      try {
        const payload = await response.json();
        message = payload.msg || payload.error_description || payload.message || message;
      } catch (error) { /* not JSON */ }
      throw new Error(message);
    }
    window.location.href = '/login.html?reset=1';
  } catch (error) {
    notice.innerHTML = '<div class="banner fail"><div><strong>Could not set the password' +
      '</strong>' + escapeHtml(error.message) + '</div></div>';
    submit.disabled = false;
    submit.textContent = 'Set the new password';
  }
});