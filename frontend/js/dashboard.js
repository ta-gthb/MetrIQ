/* Dashboard: KPI cards, role-specific queue, status chart, recent activity,
   test metrics and the AI review queue (PRD 15.4). */

import { api, requireSession, formatApiError, can, getUser } from './api.js';
import { renderShell, caseStatusPill, resultPill, fmt, fmtDate, escapeHtml, empty, loading, toast } from './ui.js';

const content = renderShell({
  active: 'dashboard',
  crumb: 'Overview',
  title: 'Dashboard',
  actionsHtml: '<button class="btn-sm" id="refresh">Refresh</button>',
});

document.getElementById('refresh').addEventListener('click', () => load());

function kpiCard(label, value, kind = '') {
  return `<div class="card tight kpi ${kind}">
    <div class="value">${escapeHtml(String(value))}</div>
    <div class="label">${escapeHtml(label)}</div>
  </div>`;
}

function caseRow(item) {
  const role = getUser()?.role_code;
  const href = role === 'SUPER_ADMIN'
    ? '/reports.html'
    : `/evaluation.html?case=${item.id}`;
  return `<tr>
    <td><a href="${href}">${escapeHtml(item.application_no)}</a>
      <div class="faint small truncate">${escapeHtml(item.title || '')}</div></td>
    <td>${escapeHtml(item.instrument_model || '\u2014')}</td>
    <td>${caseStatusPill(item.status)}</td>
    <td>${item.overall_result ? resultPill(item.overall_result) : '<span class="faint">\u2014</span>'}</td>
    <td class="small faint nowrap">${fmtDate(item.updated_at)}</td>
  </tr>`;
}

function queueCard(title, description, items) {
  const body = items.length
    ? `<div class="table-wrap"><table><tbody>${items.slice(0, 6).map(caseRow).join('')}</tbody></table></div>`
    : `<div class="faint small">Nothing waiting.</div>`;
  return `<div class="card">
    <div class="card-title"><h3>${escapeHtml(title)}</h3>
      <span class="pill pill-info">${items.length}</span></div>
    <div class="faint small" style="margin-bottom:8px">${escapeHtml(description)}</div>
    ${body}</div>`;
}

const ROLE_PROFILES = {
  SUPER_ADMIN: {
    title: 'Operations overview',
    description: 'See every laboratory queue, unblock decisions and keep the evaluation service moving.',
    action: ['/reports.html', 'Browse reports'],
    queueOrder: ['my_testing', 'awaiting_review', 'awaiting_approval'],
  },
  LAB_ADMIN: {
    title: 'Laboratory control room',
    description: 'Coordinate your laboratory\'s active evaluations, assignments and review hand-offs.',
    action: ['/evaluation.html?new=1', 'Start an evaluation'],
    queueOrder: ['my_testing', 'awaiting_review'],
  },
  ENGINEER: {
    title: 'My test bench',
    description: 'Record observations, resolve returned work and move assigned instruments toward review.',
    action: ['/evaluations.html', 'Open my evaluations'],
    queueOrder: ['my_testing', 'corrections_requested'],
  },
  REVIEWER: {
    title: 'Technical review desk',
    description: 'Validate evidence, calculations and rule references before a case moves to approval.',
    action: ['/evaluations.html', 'Open review queue'],
    queueOrder: ['awaiting_review'],
  },
  APPROVER: {
    title: 'Approval desk',
    description: 'Make accountable approval decisions on verified type-evaluation records.',
    action: ['/evaluations.html', 'Open approval queue'],
    queueOrder: ['awaiting_approval'],
  },
};

const QUEUE_LABELS = {
  my_testing: ['Testing workspace', 'Assigned cases that still need observations or calculations.'],
  awaiting_review: ['Technical review queue', 'Submitted cases that need independent verification.'],
  awaiting_approval: ['Approval queue', 'Verified cases that need an approval decision.'],
  corrections_requested: ['Corrections to resolve', 'Cases returned with a reviewer\'s reason.'],
};

function roleProfile() {
  const role = getUser()?.role_code;
  return ROLE_PROFILES[role] || {
    title: 'Evaluation overview',
    description: 'Monitor the evaluation records available to your account.',
    action: ['/evaluations.html', 'Open evaluations'],
    queueOrder: [],
  };
}

function statusChart(rows) {
  const data = rows.filter((row) => row.count > 0);
  if (!data.length) return '<div class="faint small">No cases recorded yet.</div>';
  const max = Math.max(...data.map((row) => row.count));
  return data.map((row) => `
    <div class="inline" style="gap:8px;margin-bottom:5px">
      <span class="small" style="width:150px">${escapeHtml(row.status.replace(/_/g, ' '))}</span>
      <div style="flex:1;background:var(--bg-inset);border-radius:4px;height:14px;overflow:hidden">
        <div style="width:${Math.round((row.count / max) * 100)}%;height:100%;background:linear-gradient(90deg,var(--accent),var(--info))"></div>
      </div>
      <span class="mono small" style="width:26px;text-align:right">${row.count}</span>
    </div>`).join('');
}

async function load() {
  content.innerHTML = loading('Loading dashboard\u2026');
  try {
    const [summary, pending, aiReview] = await Promise.all([
      api.get('/dashboard/summary'),
      api.get('/dashboard/pending'),
      api.get('/dashboard/ai-review'),
    ]);

    const k = summary.kpis;
    const queues = pending.queues || {};
    const profile = roleProfile();
    const queueEntries = profile.queueOrder
      .filter((key) => Object.prototype.hasOwnProperty.call(queues, key))
      .map((key) => [key, queues[key]]);
    const queueTotal = queueEntries.reduce((total, [, items]) => total + items.length, 0);
    const [actionHref, actionLabel] = profile.action;

    content.innerHTML = `
      <div class="dashboard-intro">
        <div>
          <div class="eyebrow">${escapeHtml(getUser()?.role_code || 'WORKSPACE')}</div>
          <h2>${escapeHtml(profile.title)}</h2>
          <p class="muted">${escapeHtml(profile.description)}</p>
        </div>
        <div class="inline dashboard-intro-actions">
          <span class="pill pill-info">${queueTotal} action${queueTotal === 1 ? '' : 's'} waiting</span>
          <a class="btn btn-primary" href="${actionHref}">${escapeHtml(actionLabel)}</a>
        </div>
      </div>

      <div class="grid cols-4">
        ${kpiCard('Total cases', k.total, 'accent')}
        ${kpiCard('In progress', k.in_progress)}
        ${kpiCard('Awaiting review', k.awaiting_review)}
        ${kpiCard('Awaiting approval', k.awaiting_approval)}
        ${kpiCard('Finalized', k.finalized, 'pass')}
        ${kpiCard('Failed tests', k.failed, k.failed ? 'fail' : '')}
      </div>

      <div class="grid cols-3 mt-4">
        ${queueEntries.map(([key, items]) =>
          queueCard(QUEUE_LABELS[key][0], QUEUE_LABELS[key][1], items)).join('')
          || '<div class="card faint">This role has no action queue. Use the register or reports to inspect records.</div>'}
      </div>

      <div class="grid cols-2 mt-4">
        <div class="card">
          <div class="card-title"><h3>Cases by workflow state</h3></div>
          ${statusChart(summary.status_chart || [])}
        </div>
        <div class="card">
          <div class="card-title"><h3>Test metrics</h3></div>
          <div class="grid cols-3">
            ${kpiCard('Resolved', summary.test_metrics.completed, 'pass')}
            ${kpiCard('Pending', summary.test_metrics.pending, 'warn')}
            ${kpiCard('Not applicable', summary.test_metrics.by_status.NOT_APPLICABLE || 0)}
          </div>
          <div class="mt-3 small muted">Resolved counts PASS, FAIL, waived and not-applicable
            results; pending counts unresolved tests.</div>
        </div>
      </div>

      ${can('ai.view') ? `<div class="card mt-4">
        <div class="card-title"><h3>Recent activity</h3>
          <a href="/evaluations.html" class="small">View all evaluations</a></div>
        ${(summary.recent_cases || []).length
          ? `<div class="table-wrap"><table>
              <thead><tr><th>Application</th><th>Instrument</th><th>Status</th><th>Result</th><th>Updated</th></tr></thead>
              <tbody>${summary.recent_cases.map(caseRow).join('')}</tbody></table></div>`
          : empty('No cases yet.', 'Create an evaluation to get started.')}
      </div>

      <div class="card mt-4">
        <div class="card-title"><h3>AI review queue</h3>
          <span class="pill pill-accent">advisory only</span></div>
        <div class="faint small" style="margin-bottom:8px">AI outputs that still need a human
          disposition: low-confidence extractions, anomaly warnings and report-consistency findings.
          These never change a compliance result.</div>
        ${(aiReview.items || []).length ? `
          <div class="table-wrap"><table>
            <thead><tr><th>Feature</th><th>Summary</th><th>Confidence</th><th>Case</th><th>Raised</th></tr></thead>
            <tbody>${aiReview.items.map((item) => `<tr>
              <td class="mono small">${escapeHtml(item.feature_code)}</td>
              <td>${escapeHtml(item.summary || '\u2014')}</td>
              <td class="num">${item.confidence === null || item.confidence === undefined ? '\u2014' : fmt(item.confidence)}</td>
              <td>${item.case_id ? `<a href="/evaluation.html?case=${item.case_id}">open</a>` : '\u2014'}</td>
              <td class="small faint nowrap">${fmtDate(item.created_at)}</td>
            </tr>`).join('')}</tbody></table></div>`
          : '<div class="faint small">No AI outputs are awaiting disposition.</div>'}
      </div>` : ''}`;
  } catch (error) {
    content.innerHTML = `<div class="banner fail"><div><strong>Could not load the dashboard</strong>${escapeHtml(formatApiError(error))}</div></div>`;
    toast(formatApiError(error), 'error');
  }
}

try {
  await requireSession();
  await load();
} catch (error) {
  if (error.status !== 401) {
    content.innerHTML = `<div class="banner fail"><div>${escapeHtml(formatApiError(error))}</div></div>`;
  }
}
