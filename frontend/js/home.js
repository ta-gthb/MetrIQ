/* Home page: the platform's aggregate statistics, kept current while the page
 * is open, and the sign-in shortcut for an existing session.
 *
 * The figures come from the one endpoint that answers without a token
 * (GET /api/v1/platform/statistics). It publishes counts only, and this module
 * writes nothing into the page that it has not escaped first.
 */

import { api, getSession } from './api.js';
import { escapeHtml } from './ui.js';
import { initTheme } from './theme.js';
import { startClocks } from './clock.js';

initTheme();
startClocks();

const grid = document.querySelector('[data-statistics]');
const status = document.querySelector('[data-stats-status]');
const detail = document.querySelector('[data-statistics-detail]');
const note = document.querySelector('[data-stats-note]');
const heroRuleset = document.querySelector('[data-hero-ruleset]');

const FALLBACK_REFRESH_MS = 15000;

/* The headline figures, in the order they are shown. Each is a path into the
 * payload, so the markup here and the contract there stay visibly in step. */
const HEADLINES = [
  { label: 'Evaluations recorded', path: 'evaluations.total', hint: 'Type evaluations on this deployment' },
  { label: 'In progress', path: 'evaluations.open', hint: 'Moving through the workflow' },
  { label: 'Approved', path: 'evaluations.approved', hint: 'Approved or finalised' },
  { label: 'Reports issued', path: 'reports.issued', hint: 'Immutable, hash-verifiable' },
  { label: 'Tests concluded', path: 'testing.completed', hint: 'Executed to a recorded result' },
  { label: 'Non-conformities', path: 'testing.non_compliant', hint: 'Tests failed against their limit' },
  { label: 'Measurements recorded', path: 'testing.measurements', hint: 'Observations held as data' },
  { label: 'Instruments registered', path: 'network.instruments', hint: 'Under evaluation' },
];

function readPath(object, path) {
  return path.split('.').reduce((node, key) => (
    node === null || node === undefined ? undefined : node[key]
  ), object);
}

function count(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '\u2014';
}

function text(value) {
  return value === null || value === undefined || value === '' ? '\u2014' : String(value);
}

function rulesetLabel(ruleset) {
  if (!ruleset || !ruleset.version_label) return 'Not activated';
  const state = ruleset.provisional ? `${ruleset.state} (provisional)` : ruleset.state;
  return `${ruleset.version_label} \u00b7 ${state}`;
}

function renderStatistics(payload) {
  const standings = payload.standards || {};
  const network = payload.network || {};
  const activity = payload.activity || {};

  grid.innerHTML = HEADLINES.map((item) => `
    <article class="stat">
      <div class="stat-value">${escapeHtml(count(readPath(payload, item.path)))}</div>
      <div class="stat-label">${escapeHtml(item.label)}</div>
      <div class="stat-hint">${escapeHtml(item.hint)}</div>
    </article>`).join('');

  const rows = [
    ['Rule set', rulesetLabel(standings.ruleset)],
    ['Catalogue entries', count(standings.test_definitions)],
    ['Evaluations awaiting review', count(readPath(payload, 'evaluations.awaiting_review'))],
    ['Evaluations awaiting approval', count(readPath(payload, 'evaluations.awaiting_approval'))],
    ['Reports in preparation', count(readPath(payload, 'reports.draft'))],
    ['Laboratories', count(network.laboratories)],
    ['Manufacturers', count(network.manufacturers)],
    ['Applicants', count(network.applicants)],
    ['New evaluations, 7 days', count(activity.evaluations_last_7_days)],
    ['Measurements, 24 hours', count(activity.measurements_last_24_hours)],
    ['Reports, 30 days', count(activity.reports_last_30_days)],
    ['Audit events, 24 hours', count(activity.events_last_24_hours)],
  ];
  detail.innerHTML = rows.map(([label, value]) => `
    <div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');

  if (heroRuleset) heroRuleset.textContent = rulesetLabel(standings.ruleset);
  grid.dataset.rendered = 'true';
}

/* The first read failed: say so in place of the figures rather than leaving the
 * placeholders on screen. Figures already shown are left alone. */
function markUnavailable() {
  if (!grid.dataset.rendered) {
    grid.innerHTML = '<div class="empty" style="grid-column:1/-1">'
      + 'Statistics are unavailable at the moment.</div>';
  }
  if (heroRuleset && heroRuleset.textContent.indexOf('Reading') === 0) {
    heroRuleset.textContent = 'Unavailable';
  }
}

function mark(state, message) {
  status.dataset.state = state;
  status.textContent = message;
}

let timer = null;

function schedule(delay) {
  window.clearTimeout(timer);
  if (document.hidden) return;
  timer = window.setTimeout(load, delay);
}

async function load() {
  try {
    const payload = await api.get('/platform/statistics', { redirectOn401: false, skipRefresh: true });
    renderStatistics(payload);

    const readAt = new Date(payload.generated_at);
    const seconds = Number(payload.refresh_seconds) > 0 ? Number(payload.refresh_seconds) : 15;
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';
    mark('ok', `Live \u00b7 ${readAt.toLocaleTimeString()}`);
    note.textContent = `Figures read at ${readAt.toLocaleString()} (${zone}); refreshed every `
      + `${seconds} seconds while this page is open.`;
    schedule(seconds * 1000);
  } catch (error) {
    mark('error', 'Statistics unavailable');
    markUnavailable();
    note.textContent = 'The statistics could not be read at this moment. The rest of this page, '
      + 'and the sign-in page, are unaffected.';
    schedule(FALLBACK_REFRESH_MS);
  }
}

/* A backgrounded tab is not asked for figures it is not showing, and is
 * refreshed immediately when it comes back. */
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    window.clearTimeout(timer);
    timer = null;
  } else {
    load();
  }
});

const session = getSession();
if (session && session.access_token) {
  const enter = document.getElementById('enter');
  enter.textContent = `Continue as ${session.user.full_name || 'user'}`;
  enter.href = '/dashboard.html';
}

load();
