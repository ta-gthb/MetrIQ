/* Sign-in page. */

import {
  api, login, formatApiError, getSession, loadAuthConfig, requestPasswordReset,
} from './api.js';
import { escapeHtml, toast } from './ui.js';
import { initTheme } from './theme.js';
import { startClocks } from './clock.js';

/* The sign-in page has no shell, so it wires its own theme switch and clock. */
initTheme();
startClocks();

/* Demonstration mode.
 *
 * This file used to carry the demonstration password as a literal, which put it
 * in every browser that loaded the page and in the deployed bundle. It now asks
 * the API for the account list, and the API answers only while the operator has
 * DEMO_MODE on - so a real deployment exposes nothing at all, and the panel says
 * plainly that these are demonstration accounts (audit item 3).
 */
async function renderDemoAccounts() {
  const panel = document.getElementById('demo-panel');
  const label = document.getElementById('demo-label');
  const list = document.getElementById('demo-accounts');
  const superAdminCredential = document.getElementById('demo-system-administrator-credential');

  let payload;
  try {
    payload = await api.get('/auth/demo-accounts', { redirectOn401: false });
  } catch (error) {
    panel.hidden = true;
    return;
  }

  const accounts = payload.accounts || [];
  const password = payload.password || '';
  const superAdmin = accounts.find((account) => account.role_code === 'SUPER_ADMIN');
  panel.hidden = false;
  label.innerHTML = '<span class="pill pill-warn">Demo mode</span> Demonstration accounts' +
    (password ? '.' : ' - the password is supplied separately.') +
    ' Selecting one fills the form; each role sees a different slice of the workflow.';
  if (superAdmin && password) {
    superAdminCredential.textContent = `System Administrator demo sign-in: ${superAdmin.email} / ${password}`;
    superAdminCredential.hidden = false;
  }

  list.innerHTML = accounts.map((account) => `
    <button type="button" class="demo-account" data-user-id="${escapeHtml(account.user_id)}">
      <span><strong>${escapeHtml(account.role_name)}</strong><br />
        <span class="faint">${escapeHtml(account.designation || account.full_name)}</span></span>
      <span class="mono small">${escapeHtml(account.user_id)}</span>
    </button>`).join('');

  list.querySelectorAll('[data-user-id]').forEach((button) => {
    button.addEventListener('click', () => {
      document.getElementById('user-id').value = button.dataset.userId;
      if (password) document.getElementById('password').value = password;
      document.getElementById('submit').focus();
    });
  });
}

/* Which identity provider this deployment uses (audit item 4).
 *
 * The backend decides, and enforces it: this only reflects the decision in the
 * page, so the reset link appears only where the API accepts it. Nothing about
 * the provider, or about how a session is minted, is printed on the page.
 */
let authConfig = { supabase: null, local_login: true, password_reset: null };

async function applyAuthConfig() {
  authConfig = await loadAuthConfig();

  if (authConfig.password_reset) document.getElementById('reset-link').hidden = false;

  // The demonstration panel is only meaningful where MetrIQ checks the password
  // itself; the endpoint answers 404 everywhere else.
  if (authConfig.local_login) await renderDemoAccounts();
  else document.getElementById('demo-panel').hidden = true;
}

applyAuthConfig();

const params = new URLSearchParams(window.location.search);
const notice = document.getElementById('notice');

if (getSession() && getSession().access_token) {
  window.location.href = params.get('next') || '/dashboard.html';
}

if (params.get('expired')) {
  notice.innerHTML = '<div class="banner warn"><div>Your session expired. Please sign in again.</div></div>';
}

if (params.get('reset')) {
  notice.innerHTML = '<div class="banner pass"><div>Your password was changed. Sign in with the new one.</div></div>';
}

/* Password reset. The request goes to the identity provider, which issues the
 * link the user follows (audit item 4). */
const resetForm = document.getElementById('reset-form');
const resetEmail = document.getElementById('reset-email');
const resetSubmit = document.getElementById('reset-submit');

document.getElementById('forgot').addEventListener('click', (event) => {
  event.preventDefault();
  resetForm.hidden = !resetForm.hidden;
  if (!resetForm.hidden) {
    // The reset is the provider's and it addresses a mailbox, so only an
    // address typed above is carried over.
    const typed = document.getElementById('user-id').value.trim();
    resetEmail.value = typed.includes('@') ? typed : '';
    resetEmail.focus();
  }
});

resetForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  resetEmail.classList.remove('invalid');
  if (!resetEmail.value.trim()) { resetEmail.classList.add('invalid'); return; }

  resetSubmit.disabled = true;
  resetSubmit.textContent = 'Sending\u2026';
  try {
    await requestPasswordReset(resetEmail.value.trim());
    // The provider answers the same way whether or not the account exists, so
    // this wording must not either.
    notice.innerHTML = '<div class="banner pass"><div>If that address has an account, a' +
      ' reset link is on its way. Open it to choose a new password.</div></div>';
    resetForm.hidden = true;
  } catch (error) {
    notice.innerHTML = '<div class="banner fail"><div><strong>Could not send the reset link' +
      '</strong>' + escapeHtml(formatApiError(error)) + '</div></div>';
  } finally {
    resetSubmit.disabled = false;
    resetSubmit.textContent = 'Email me a reset link';
  }
});

document.getElementById('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const identifier = document.getElementById('user-id');
  const password = document.getElementById('password');
  const submit = document.getElementById('submit');

  [identifier, password].forEach((field) => field.classList.remove('invalid'));
  if (!identifier.value.trim()) { identifier.classList.add('invalid'); return; }
  if (!password.value) { password.classList.add('invalid'); return; }

  submit.disabled = true;
  submit.textContent = 'Signing in\u2026';
  try {
    await login(identifier.value.trim(), password.value);
    window.location.href = params.get('next') || '/dashboard.html';
  } catch (error) {
    const message = formatApiError(error);
    notice.innerHTML = `<div class="banner fail"><div><strong>Sign-in failed</strong>${escapeHtml(message)}</div></div>`;
    password.value = '';
    password.classList.add('invalid');
    toast(message, 'error');
  } finally {
    submit.disabled = false;
    submit.textContent = 'Sign in';
  }
});
