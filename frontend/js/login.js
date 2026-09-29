/* Sign-in page. */

import { api, login, formatApiError, getSession } from './api.js';
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

  let payload;
  try {
    payload = await api.get('/auth/demo-accounts', { redirectOn401: false });
  } catch (error) {
    panel.hidden = true;
    return;
  }

  const accounts = payload.accounts || [];
  const password = payload.password || '';
  panel.hidden = false;
  label.innerHTML = '<span class="pill pill-warn">Demo mode</span> Demonstration accounts' +
    (password ? '.' : ' - the password is supplied separately.') +
    ' Selecting one fills the form; each role sees a different slice of the workflow.';

  list.innerHTML = accounts.map((account) => `
    <button type="button" class="demo-account" data-email="${escapeHtml(account.email)}">
      <span><strong>${escapeHtml(account.role_name)}</strong><br />
        <span class="faint">${escapeHtml(account.designation || account.full_name)}</span></span>
      <span class="mono small">${escapeHtml(account.email.split('@')[0])}</span>
    </button>`).join('');

  list.querySelectorAll('[data-email]').forEach((button) => {
    button.addEventListener('click', () => {
      document.getElementById('email').value = button.dataset.email;
      if (password) document.getElementById('password').value = password;
      document.getElementById('submit').focus();
    });
  });
}

renderDemoAccounts();

const params = new URLSearchParams(window.location.search);
const notice = document.getElementById('notice');

if (getSession() && getSession().access_token) {
  window.location.href = params.get('next') || '/dashboard.html';
}

if (params.get('expired')) {
  notice.innerHTML = '<div class="banner warn"><div>Your session expired. Please sign in again.</div></div>';
}

document.getElementById('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const email = document.getElementById('email');
  const password = document.getElementById('password');
  const submit = document.getElementById('submit');

  [email, password].forEach((field) => field.classList.remove('invalid'));
  if (!email.value.trim()) { email.classList.add('invalid'); return; }
  if (!password.value) { password.classList.add('invalid'); return; }

  submit.disabled = true;
  submit.textContent = 'Signing in\u2026';
  try {
    await login(email.value.trim(), password.value);
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
