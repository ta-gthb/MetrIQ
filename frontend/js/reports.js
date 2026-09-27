/* Report repository: search, snapshot, revisions and download (PRD 15.1, 17).

   A generated report is an immutable snapshot of the case at a rule-set
   version. Downloads are fetched as authenticated blobs so the bearer token is
   never placed in a URL. */

import { api, requireSession, formatApiError, can } from './api.js';
import {
  renderShell, resultPill, caseStatusPill, fmt, fmtDate, escapeHtml,
  empty, loading, toast, openModal,
} from './ui.js';

const content = renderShell({
  active: 'reports',
  crumb: 'Repository',
  title: 'Reports',
  actionsHtml: '<button class="btn-sm" id="refresh">Refresh</button>',
});

const state = { search: '', onlyFinal: false };

async function download(reportId, fmt) {
  try {
    const blob = await api.download('/reports/' + reportId + '/download?fmt=' + fmt);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'metriq-report.' + fmt;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function showSnapshot(reportId, reportNo) {
  try {
    const snapshot = await api.get('/reports/' + reportId + '/snapshot');
    const summary = snapshot.summary || snapshot;
    const html = '<div class="calc-panel" style="max-height:60vh;overflow:auto">' +
      '<pre class="mono small" style="white-space:pre-wrap;margin:0">' +
      escapeHtml(JSON.stringify(summary, null, 1)) + '</pre></div>' +
      '<div class="hint mt-2">The snapshot is the frozen content the report was generated from: ' +
      'rule-set version, results, rules, clauses and evidence.</div>';
    await openModal({ title: 'Snapshot \u00b7 ' + reportNo, bodyHtml: html, submitLabel: 'Close', cancelLabel: 'Dismiss' });
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function showRevisions(reportId, reportNo) {
  try {
    const revisions = await api.get('/reports/' + reportId + '/revisions');
    const rows = revisions.length
      ? revisions.map((revision) => '<tr><td class="mono">' + revision.revision_no + '</td>' +
          '<td>' + escapeHtml(revision.format) + '</td>' +
          '<td class="num">' + (revision.size_bytes ? Math.round(revision.size_bytes / 1024) + ' KB' : '\u2014') + '</td>' +
          '<td class="mono small truncate" title="' + escapeHtml(revision.sha256 || '') + '">' +
            escapeHtml((revision.sha256 || '').slice(0, 16)) + '\u2026</td>' +
          '<td class="small faint">' + fmtDate(revision.created_at) + '</td></tr>').join('')
      : '<tr><td colspan="5" class="faint small">No revisions recorded.</td></tr>';
    await openModal({
      title: 'Revisions \u00b7 ' + reportNo,
      bodyHtml: '<div class="table-wrap"><table><thead><tr><th>Rev</th><th>Format</th><th class="num">Size</th>' +
        '<th>SHA-256</th><th>Created</th></tr></thead><tbody>' + rows + '</tbody></table></div>',
      submitLabel: 'Close', cancelLabel: 'Dismiss',
    });
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function load() {
  content.innerHTML = loading('Loading reports\u2026');
  let data;
  try {
    data = await api.get('/reports', {
      query: { search: state.search, only_final: state.onlyFinal ? 'true' : '', page_size: 100 },
    });
  } catch (error) {
    content.innerHTML = '<div class="banner fail"><div>' + escapeHtml(formatApiError(error)) + '</div></div>';
    return;
  }
  const rows = data.items.map((report) => '<tr>' +
    '<td class="mono"><a href="/evaluation.html?case=' + report.case_id + '">' + escapeHtml(report.report_no) + '</a>' +
      '<div class="faint small">' + escapeHtml(report.application_no || '') + '</div></td>' +
    '<td>' + (report.is_immutable ? '<span class="pill pill-pass">locked</span>' : '<span class="pill pill-info">draft</span>') +
      ' <span class="mono small">rev ' + report.revision_no + '</span></td>' +
    '<td>' + (report.overall_result ? resultPill(report.overall_result) : '<span class="faint">\u2014</span>') + '</td>' +
    '<td class="mono small">' + escapeHtml(report.ruleset_label || '\u2014') + '</td>' +
    '<td class="mono small">' + escapeHtml(report.verification_code || '\u2014') + '</td>' +
    '<td class="small faint nowrap">' + fmtDate(report.generated_at) + '</td>' +
    '<td class="nowrap">' +
      '<button class="btn-sm" data-dl="' + report.id + '" data-fmt="pdf">PDF</button> ' +
      '<button class="btn-sm" data-dl="' + report.id + '" data-fmt="docx">DOCX</button> ' +
      '<button class="btn-sm" data-snapshot="' + report.id + '" data-no="' + escapeHtml(report.report_no) + '">Snapshot</button> ' +
      '<button class="btn-sm" data-revisions="' + report.id + '" data-no="' + escapeHtml(report.report_no) + '">Revisions</button>' +
    '</td></tr>').join('');

  content.innerHTML = '<div class="card">' +
    '<div class="inline" style="margin-bottom:12px">' +
      '<div style="min-width:260px"><label for="search">Search</label>' +
        '<input id="search" placeholder="Report or application number" value="' + escapeHtml(state.search) + '" /></div>' +
      '<label class="inline" style="gap:6px;margin:22px 0 0"><input type="checkbox" id="only-final"' +
        (state.onlyFinal ? ' checked' : '') + ' /> <span class="small">Finalized only</span></label>' +
      '<div class="right"><button class="btn-sm" id="apply">Apply</button></div></div>' +
    (data.items.length ? '<div class="table-wrap"><table><thead><tr><th>Report</th><th>Status</th><th>Result</th>' +
      '<th>Ruleset</th><th>Verification code</th><th>Generated</th><th></th></tr></thead><tbody>' + rows +
      '</tbody></table></div><div class="small faint mt-3">' + data.meta.total + ' report(s)</div>'
      : empty('No reports found.', 'Reports appear here once generated from a submitted case.')) +
    '</div>';

  document.getElementById('apply')?.addEventListener('click', () => {
    state.search = document.getElementById('search').value.trim();
    state.onlyFinal = document.getElementById('only-final').checked;
    load();
  });
  document.getElementById('search')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); state.search = event.target.value.trim(); load(); }
  });
  document.getElementById('only-final')?.addEventListener('change', (event) => {
    state.onlyFinal = event.target.checked; load();
  });
  content.querySelectorAll('[data-dl]').forEach((button) => {
    button.addEventListener('click', () => download(button.dataset.dl, button.dataset.fmt));
  });
  content.querySelectorAll('[data-snapshot]').forEach((button) => {
    button.addEventListener('click', () => showSnapshot(button.dataset.snapshot, button.dataset.no));
  });
  content.querySelectorAll('[data-revisions]').forEach((button) => {
    button.addEventListener('click', () => showRevisions(button.dataset.revisions, button.dataset.no));
  });
}

document.getElementById('refresh').addEventListener('click', load);

try {
  await requireSession('reports.download');
  await load();
} catch (error) {
  if (error.status !== 401) {
    content.innerHTML = '<div class="banner fail"><div>' + escapeHtml(formatApiError(error)) + '</div></div>';
  }
}