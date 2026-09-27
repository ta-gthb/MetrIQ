/* Sign-in page. */

import { login, formatApiError, getSession } from './api.js';
import { escapeHtml, toast } from './ui.js';

const DEMO_ACCOUNTS = [
  ['engineer@metriq.local', 'Test Engineer', 'records observations and runs calculations'],
  ['reviewer@metriq.local', 'Technical Reviewer', 'verifies results or requests a correction'],
  ['approver@metriq.local', 'Approving Authority', 'approves, finalizes and releases the report'],
  ['labadmin@metriq.local', 'Laboratory Admin', 'assigns personnel and manages the laboratory'],
  ['auditor@metriq.local', 'Auditor', 'read-only access to records and audit trails'],
  ['admin@metriq.local', 'Super Admin', 'platform configuration and rule sets'],
];

const params = new URLSearchParams(window.location.search);
const notice = document.getElementById('notice');

if (getSession() && getSession().access_token) {
  window.location.href = params.get('next') || '/dashboard.html';
}

if (params.get('expired')) {
  notice.innerHTML = '<div class="banner warn"><div>Your session expired. Please sign in again.</div></div>';
}

document.getElementById('demo-accounts').innerHTML = DEMO_ACCOUNTS.map(([email, role, blurb]) => `
  <button type="button" class="demo-account" data-email="${escapeHtml(email)}">
    <span><strong>${escapeHtml(role)}</strong><br /><span class="faint">${escapeHtml(blurb)}</span></span>
    <span class="mono small">${escapeHtml(email.split('@')[0])}</span>
  </button>`).join('');

document.querySelectorAll('[data-email]').forEach((button) => {
  button.addEventListener('click', () => {
    document.getElementById('email').value = button.dataset.email;
    document.getElementById('password').value = 'MetrIQ@2026';
    document.getElementById('submit').focus();
  });
});

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
