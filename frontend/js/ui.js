/* Shared UI primitives: shell, navigation, status pills, toasts and modals. */

import { can, canAny, getUser, getRoleName, logout } from './api.js';
import { initTheme } from './theme.js';
import { startClocks } from './clock.js';

export function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** Render a metrological value for display: no scientific notation, and no
 *  meaningless trailing zeros from the fixed-scale database columns. */
export function fmt(value, { unit = null, dash = '\u2014' } = {}) {
  if (value === null || value === undefined || value === '') return dash;
  let text = String(value);
  if (/^-?\d+(\.\d+)?$/.test(text)) {
    if (text.includes('.')) text = text.replace(/0+$/, '').replace(/\.$/, '');
    if (text === '-0') text = '0';
  }
  return unit ? `${text} ${unit}` : text;
}

export function fmtDate(value, { withTime = true } = {}) {
  if (!value) return '\u2014';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '\u2014';
  const options = { year: 'numeric', month: 'short', day: '2-digit' };
  if (withTime) { options.hour = '2-digit'; options.minute = '2-digit'; }
  return date.toLocaleString(undefined, options);
}

const RESULT_KINDS = {
  PASS: 'pass', FAIL: 'fail', INCOMPLETE: 'warn', INVALID: 'fail',
  NOT_APPLICABLE: 'na', WAIVED: 'info', PENDING: 'na', COMPLETED: 'pass',
};

const CASE_KINDS = {
  DRAFT: 'na', ASSIGNED: 'info', IN_PROGRESS: 'info', TESTING_COMPLETED: 'accent',
  UNDER_REVIEW: 'accent', CORRECTION_REQUIRED: 'warn', VERIFIED: 'accent',
  UNDER_APPROVAL: 'accent', APPROVED: 'pass', FINALIZED: 'pass',
  REJECTED: 'fail', CANCELLED: 'na',
};

export function resultPill(status) {
  const value = status || 'PENDING';
  return `<span class="pill pill-${RESULT_KINDS[value] || 'na'}">${escapeHtml(value.replace(/_/g, ' '))}</span>`;
}

export function caseStatusPill(status) {
  const value = status || 'DRAFT';
  return `<span class="pill pill-${CASE_KINDS[value] || 'na'}">${escapeHtml(value.replace(/_/g, ' '))}</span>`;
}

export function severityPill(kind) {
  const map = { high: 'fail', medium: 'warn', low: 'info', info: 'info', warning: 'warn' };
  return `<span class="pill pill-${map[kind] || 'info'}">${escapeHtml(kind)}</span>`;
}

export function toast(message, kind = 'info', timeout = 5200) {
  let host = document.querySelector('.toast-host');
  if (!host) {
    host = document.createElement('div');
    host.className = 'toast-host';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
  }
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => node.remove(), timeout);
}

export function debounce(fn, wait = 500) {
  let handle;
  return (...args) => {
    clearTimeout(handle);
    handle = setTimeout(() => fn(...args), wait);
  };
}

export function empty(message, hint = '') {
  return `<div class="empty"><div>${escapeHtml(message)}</div>${
    hint ? `<div class="small faint mt-2">${escapeHtml(hint)}</div>` : ''}</div>`;
}

export function loading(message = 'Loading\u2026') {
  return `<div class="empty">${escapeHtml(message)}</div>`;
}

/* --------------------------------------------------------------- modal --- */

export function openModal({ title, bodyHtml, submitLabel = 'Save', cancelLabel = 'Cancel', onSubmit, destructive = false }) {
  /* submitLabel = null renders a read-only panel: close is the only action. */
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
        <header><h2>${escapeHtml(title)}</h2>
          <button class="btn-ghost btn-sm" data-close aria-label="Close">\u2715</button></header>
        <div class="modal-body">${bodyHtml}</div>
        <footer>
          <button data-close>${escapeHtml(cancelLabel)}</button>
          ${submitLabel ? `<button class="${destructive ? 'btn-danger' : 'btn-primary'}" data-submit>${escapeHtml(submitLabel)}</button>` : ''}
        </footer>
      </div>`;
    document.body.appendChild(backdrop);

    const close = (result) => { backdrop.remove(); resolve(result); };
    const submit = backdrop.querySelector('[data-submit]');
    const input = backdrop.querySelector('textarea, input, select');
    if (input) setTimeout(() => input.focus(), 30);

    backdrop.querySelectorAll('[data-close]').forEach((button) => {
      button.addEventListener('click', () => close(null));
    });
    backdrop.addEventListener('mousedown', (event) => { if (event.target === backdrop) close(null); });
    document.addEventListener('keydown', function onKey(event) {
      if (event.key === 'Escape') { document.removeEventListener('keydown', onKey); close(null); }
    });
    submit?.addEventListener('click', async () => {
      const value = readModalValue(backdrop);
      if (onSubmit) {
        submit.disabled = true;
        try {
          const outcome = await onSubmit(value, backdrop);
          if (outcome !== false) close(value);
        } catch (error) {
          toast(error.message || String(error), 'error');
        } finally {
          submit.disabled = false;
        }
      } else {
        close(value);
      }
    });
  });
}

function readModalValue(backdrop) {
  const field = backdrop.querySelector('[data-value]');
  /* A confirmation panel has no input to read: submitting it means "yes".
     Cancel and Escape still resolve null. */
  return field ? field.value : true;
}

export function promptReason(title, { label = 'Reason', hint = '', minLength = 10, submitLabel = 'Confirm' } = {}) {
  return openModal({
    title,
    submitLabel,
    bodyHtml: `
      <div class="field">
        <label for="modal-reason">${escapeHtml(label)}</label>
        <textarea id="modal-reason" data-value rows="4" placeholder="At least ${minLength} characters"></textarea>
        <div class="hint">${escapeHtml(hint || `A reason of at least ${minLength} characters is recorded in the audit trail.`)}</div>
      </div>`,
    onSubmit: (value) => {
      if (!value || value.trim().length < minLength) {
        toast(`${label} must be at least ${minLength} characters.`, 'warn');
        return false;
      }
      return true;
    },
  });
}

/* --------------------------------------------------------------- shell --- */

const ICONS = {
  home: '\u2302', dashboard: '\u25a6', evaluations: '\u2637', reports: '\u25a4',
  admin: '\u2699', audit: '\u2691', standards: '\u00a7', instruments: '\u2696',
  assistant: '\u2726',
};

function navItems() {
  const role = getUser()?.role_code;
  const is = (...roles) => roles.includes(role);
  const items = [
    /* The public home page: the way back to the platform's own description and
       figures from anywhere inside the application. */
    { href: '/', label: 'Home', icon: ICONS.home, show: true, key: 'home' },
    { href: '/dashboard.html', label: 'Dashboard', icon: ICONS.dashboard, show: can('dashboard.view'), key: 'dashboard' },
    { href: '/evaluations.html', label: is('REVIEWER') ? 'Review queue' : is('APPROVER') ? 'Approval queue' : is('ENGINEER') ? 'My evaluations' : 'Evaluations', icon: ICONS.evaluations, show: canAny('cases.view', 'cases.view.scope') && !is('SUPER_ADMIN'), key: 'evaluations' },
    { href: '/evaluation.html?new=1', label: 'New evaluation', icon: '\uff0b', show: can('cases.create') && !is('SUPER_ADMIN'), key: 'new' },
    { href: '/evaluations.html#instruments', label: 'Instruments', icon: ICONS.instruments, show: canAny('cases.view', 'cases.view.scope') && !is('SUPER_ADMIN'), key: 'instruments' },
    { href: '/reports.html', label: 'Reports', icon: ICONS.reports, show: can('reports.download'), key: 'reports' },
    { href: '/assistant.html', label: 'R 76 assistant', icon: ICONS.assistant, show: can('ai.view') && !is('AUDITOR', 'APPROVER'), key: 'assistant' },
  ];
  return items.filter((item) => item.show);
}

function adminItems() {
  return [
    { href: '/admin.html', label: 'Administration', icon: ICONS.admin, show: canAny('users.manage', 'users.manage.scoped', 'laboratories.manage', 'settings.manage'), key: 'admin' },
    { href: '/admin.html#standards', label: 'Standards & rules', icon: ICONS.standards, show: can('rules.view'), key: 'standards' },
    { href: '/admin.html#audit', label: 'Audit logs', icon: ICONS.audit, show: canAny('audit.view', 'audit.view.scope', 'audit.view.limited'), key: 'audit' },
  ].filter((item) => item.show);
}

/** Build the application chrome and return the content container. */
export function renderShell({ active, title, crumb = 'MetrIQ', actionsHtml = '' }) {
  const user = getUser() || { full_name: 'Unknown user', role_code: '\u2014' };
  const shell = document.getElementById('shell');
  if (!shell) throw new Error('renderShell requires a #shell element');

  shell.innerHTML = `
    <a class="skip-link" href="#page-content">Skip to content</a>
    <aside class="sidebar">
      <div class="brand">
        <img src="/assets/logo-emblem.png" alt="" width="320" height="241" />
        <div>
          <div class="brand-name">MetrIQ</div>
          <div class="brand-sub">OIML R 76</div>
        </div>
      </div>
      <nav class="nav" aria-label="Main">
        ${navItems().map((item) => navLink(item, active)).join('')}
        ${adminItems().length ? '<div class="nav-label">Governance</div>' : ''}
        ${adminItems().map((item) => navLink(item, active)).join('')}
      </nav>
    </aside>
    <div class="main">
      <header class="topbar">
        <div class="topbar-title">
          <span class="crumb">${escapeHtml(crumb)}</span>
          <h1>${escapeHtml(title)}</h1>
        </div>
        <div class="inline">
          ${actionsHtml}
          <div class="inline" style="gap:6px">
            <div class="clock" data-clock role="group" aria-label="Current local date and time">
              <span class="clock-time" data-clock-time>--:--:--</span>
              <span class="clock-date" data-clock-date>&nbsp;</span>
            </div>
            <div style="text-align:right">
              <div class="small">${escapeHtml(user.full_name)}</div>
              <div class="faint mono" style="font-size:0.72rem">${escapeHtml(user.user_code || '')}</div>
              <div class="faint" style="font-size:0.72rem">${escapeHtml(getRoleName() || user.role_code)}</div>
            </div>
            <button class="btn-sm theme-toggle" id="theme-toggle" type="button" data-theme-toggle aria-pressed="false">
              <span class="theme-icon" data-theme-icon aria-hidden="true">☀</span>
              <span class="theme-toggle-label" data-theme-label>Light</span>
            </button>
            <button class="btn-sm" id="sign-out">Sign out</button>
          </div>
        </div>
      </header>
      <main class="content" id="page-content" tabindex="-1"></main>
    </div>`;

  shell.querySelector('#sign-out').addEventListener('click', logout);
  /* The theme switch and the live clock belong to every page's chrome, so
     they are wired here rather than in each page module. Both calls are
     idempotent, so a page may also call them before the shell is drawn. */
  initTheme();
  startClocks();
  return shell.querySelector('#page-content');
}

function navLink(item, active) {
  return `<a href="${item.href}" class="${item.key === active ? 'active' : ''}">
    <span class="icon" aria-hidden="true">${item.icon || ''}</span>${escapeHtml(item.label)}</a>`;
}

/** Render an inline "not permitted" panel instead of a broken page. */
export function permissionDenied(message = 'Your role does not have access to this page.') {
  return `<div class="banner fail"><div><strong>Not permitted</strong>${escapeHtml(message)}</div></div>`;
}
