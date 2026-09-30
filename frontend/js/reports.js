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

const state = {
  search: '', onlyFinal: false, instrumentId: '', result: '', ruleset: '', from: '', to: '',
};
let compareSelection = null;

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
    const numbers = [...new Set(revisions.map((revision) => revision.revision_no))].sort((a, b) => a - b);
    const compare = numbers.length > 1
      ? '<div class="inline mt-3"><span class="small">Compare</span>' +
        '<select id="cmp-left">' + numbers.map((number) =>
          '<option value="' + number + '">revision ' + number + '</option>').join('') + '</select>' +
        '<span class="faint small">with</span>' +
        '<select id="cmp-right">' + numbers.map((number) =>
          '<option value="' + number + '"' + (number === numbers[numbers.length - 1] ? ' selected' : '') +
          '>revision ' + number + '</option>').join('') + '</select>' +
        '<span class="faint small">Each revision keeps the snapshot it printed.</span></div>'
      : '';
    compareSelection = null;
    await openModal({
      title: 'Revisions \u00b7 ' + reportNo,
      bodyHtml: '<div class="table-wrap"><table><thead><tr><th>Rev</th><th>Format</th><th class="num">Size</th>' +
        '<th>SHA-256</th><th>Created</th></tr></thead><tbody>' + rows + '</tbody></table></div>' + compare,
      submitLabel: numbers.length > 1 ? 'Compare' : null, cancelLabel: 'Close',
      onSubmit: (value, backdrop) => {
        const left = Number(backdrop.querySelector('#cmp-left').value);
        const right = Number(backdrop.querySelector('#cmp-right').value);
        if (left === right) { toast('Choose two different revisions to compare.', 'warn'); return false; }
        compareSelection = { left, right };
        return true;
      },
    });
    if (compareSelection) await showComparison(reportId, reportNo, compareSelection.left, compareSelection.right);
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

function repositoryQuery({ paged = true } = {}) {
  const query = {};
  if (state.search) query.search = state.search;
  if (state.onlyFinal) query.only_final = 'true';
  if (state.instrumentId) query.instrument_id = state.instrumentId;
  if (state.result) query.result = state.result;
  if (state.ruleset) query.ruleset = state.ruleset;
  if (state.from) query.generated_from = state.from + 'T00:00:00';
  if (state.to) query.generated_to = state.to + 'T23:59:59';
  if (paged) query.page_size = 100;
  return query;
}

function valueLabel(value) {
  if (value === null || value === undefined || value === '') return '\u2014';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

async function exportCsv() {
  try {
    const params = new URLSearchParams(repositoryQuery({ paged: false }));
    const blob = await api.download('/reports/export.csv' + (params.toString() ? '?' + params.toString() : ''));
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'metriq-repository.csv';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast('Repository exported as CSV.', 'success');
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function showComparison(reportId, reportNo, left, right) {
  try {
    const diff = await api.get('/reports/' + reportId + '/compare', { query: { left, right } });
    const rows = diff.changes.length
      ? diff.changes.map((item) => '<tr>' +
          '<td class="small">' + escapeHtml(item.area) +
            (item.code ? ' <span class="mono">' + escapeHtml(item.code) + '</span>' : '') + '</td>' +
          '<td class="mono small">' + escapeHtml(item.field) + '</td>' +
          '<td class="small">' + escapeHtml(valueLabel(item.before)) + '</td>' +
          '<td class="small">' + escapeHtml(valueLabel(item.after)) + '</td></tr>').join('')
      : '<tr><td colspan="4" class="faint small">No content differences between these revisions.</td></tr>';
    await openModal({
      title: 'Compare revisions \u00b7 ' + reportNo,
      submitLabel: null, cancelLabel: 'Close',
      bodyHtml: '<div class="small faint" style="margin-bottom:8px">Revision ' +
        escapeHtml(String(diff.before.revision_no)) + ' \u2192 revision ' + escapeHtml(String(diff.after.revision_no)) +
        ' \u00b7 ' + diff.change_count + ' change(s) \u00b7 tests changed: ' +
        (diff.changed_tests.length ? escapeHtml(diff.changed_tests.join(', ')) : 'none') + '</div>' +
        '<div class="table-wrap" style="max-height:55vh;overflow:auto"><table><thead><tr>' +
        '<th>Area</th><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>' + rows + '</tbody></table></div>',
    });
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function showInstrumentHistory(instrumentId, model) {
  try {
    const history = await api.get('/instruments/' + instrumentId + '/history');
    const rows = history.cases.length
      ? history.cases.map((entry) => '<tr>' +
          '<td class="mono"><a href="/evaluation.html?case=' + entry.case_id + '">' +
            escapeHtml(entry.application_no) + '</a>' +
            '<div class="faint small">' + escapeHtml(entry.title || '') + '</div></td>' +
          '<td class="small">' + escapeHtml(entry.status) + '</td>' +
          '<td>' + (entry.report && entry.report.overall_result
            ? resultPill(entry.report.overall_result)
            : '<span class="faint small">no report</span>') + '</td>' +
          '<td class="mono small">' + escapeHtml(entry.report ? entry.report.report_no : '\u2014') + '</td>' +
          '<td class="small faint nowrap">' + fmtDate(entry.created_at) + '</td></tr>').join('')
      : '<tr><td colspan="5" class="faint small">No evaluations recorded for this instrument.</td></tr>';
    await openModal({
      title: 'Instrument history \u00b7 ' + (history.instrument.model || model || ''),
      submitLabel: null, cancelLabel: 'Close',
      bodyHtml: '<div class="small faint" style="margin-bottom:8px">Serial ' +
        escapeHtml(history.instrument.serial_number || '\u2014') + ' \u00b7 class ' +
        escapeHtml(history.instrument.instrument_class || '\u2014') + ' \u00b7 ' +
        history.cases.length + ' evaluation(s)</div>' +
        '<div class="table-wrap" style="max-height:55vh;overflow:auto"><table><thead><tr>' +
        '<th>Application</th><th>Status</th><th>Result</th><th>Report</th><th>Opened</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table></div>',
    });
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function load() {
  content.innerHTML = loading('Loading reports\u2026');
  let data;
  let instruments = { items: [] };
  try {
    [data, instruments] = await Promise.all([
      api.get('/reports', { query: repositoryQuery() }),
      api.get('/instruments', { query: { page_size: 200 } }).catch(() => ({ items: [] })),
    ]);
  } catch (error) {
    content.innerHTML = '<div class="banner fail"><div>' + escapeHtml(formatApiError(error)) + '</div></div>';
    return;
  }
  const rows = data.items.map((report) => '<tr>' +
    '<td class="mono"><a href="/evaluation.html?case=' + report.case_id + '">' + escapeHtml(report.report_no) + '</a>' +
      '<div class="faint small">' + escapeHtml(report.application_no || '') + '</div></td>' +
    '<td class="small">' + (report.instrument_model
      ? escapeHtml(report.instrument_model) +
        '<div class="faint small mono">' + escapeHtml(report.instrument_serial_number || '\u2014') + '</div>' +
        (report.instrument_id
          ? '<button class="btn-sm" data-instrument-history="' + report.instrument_id +
            '" data-model="' + escapeHtml(report.instrument_model || '') + '">History</button>'
          : '')
      : '\u2014') + '</td>' +
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

  const instrumentOptions = '<option value="">All instruments</option>' + instruments.items.map((item) =>
    '<option value="' + item.id + '"' + (item.id === state.instrumentId ? ' selected' : '') + '>' +
    escapeHtml(item.model + (item.serial_number ? ' \u00b7 ' + item.serial_number : '')) + '</option>').join('');

  content.innerHTML = '<div class="card">' +
    '<div class="inline" style="margin-bottom:12px;flex-wrap:wrap">' +
      '<div style="min-width:220px"><label for="search">Search</label>' +
        '<input id="search" placeholder="Number, title, model or serial" value="' + escapeHtml(state.search) + '" /></div>' +
      '<div style="min-width:200px"><label for="filter-instrument">Instrument</label>' +
        '<select id="filter-instrument">' + instrumentOptions + '</select></div>' +
      '<div style="min-width:130px"><label for="filter-result">Result</label>' +
        '<select id="filter-result"><option value="">Any</option>' +
          '<option value="PASS"' + (state.result === 'PASS' ? ' selected' : '') + '>PASS</option>' +
          '<option value="FAIL"' + (state.result === 'FAIL' ? ' selected' : '') + '>FAIL</option></select></div>' +
      '<div style="min-width:160px"><label for="filter-ruleset">Ruleset</label>' +
        '<input id="filter-ruleset" placeholder="e.g. r76-1-2006-v1" value="' + escapeHtml(state.ruleset) + '" /></div>' +
      '<div style="min-width:150px"><label for="filter-from">Generated from</label>' +
        '<input id="filter-from" type="date" value="' + escapeHtml(state.from) + '" /></div>' +
      '<div style="min-width:150px"><label for="filter-to">Generated to</label>' +
        '<input id="filter-to" type="date" value="' + escapeHtml(state.to) + '" /></div>' +
      '<label class="inline" style="gap:6px;margin:22px 0 0"><input type="checkbox" id="only-final"' +
        (state.onlyFinal ? ' checked' : '') + ' /> <span class="small">Finalized only</span></label>' +
      '<div class="right"><button class="btn-sm" id="export-csv">Export CSV</button> ' +
        '<button class="btn-sm" id="apply">Apply</button></div></div>' +
    (data.items.length ? '<div class="table-wrap"><table><thead><tr><th>Report</th><th>Instrument</th>' +
      '<th>Status</th><th>Result</th>' +
      '<th>Ruleset</th><th>Verification code</th><th>Generated</th><th></th></tr></thead><tbody>' + rows +
      '</tbody></table></div><div class="small faint mt-3">' + data.meta.total + ' report(s)</div>'
      : empty('No reports found.', 'Reports appear here once generated from a submitted case. Widen the filters or generate a report from a submitted case.')) +
    '</div>';

  document.getElementById('apply')?.addEventListener('click', () => {
    state.search = document.getElementById('search').value.trim();
    state.instrumentId = document.getElementById('filter-instrument').value;
    state.result = document.getElementById('filter-result').value;
    state.ruleset = document.getElementById('filter-ruleset').value.trim();
    state.from = document.getElementById('filter-from').value;
    state.to = document.getElementById('filter-to').value;
    state.onlyFinal = document.getElementById('only-final').checked;
    load();
  });
  document.getElementById('export-csv')?.addEventListener('click', exportCsv);
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
  content.querySelectorAll('[data-instrument-history]').forEach((button) => {
    button.addEventListener('click', () => showInstrumentHistory(button.dataset.instrumentHistory, button.dataset.model));
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