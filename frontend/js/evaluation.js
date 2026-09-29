/* Evaluation workspace: the guided 10-step type-evaluation workflow.

   Steps 14-23 of the PRD (15.2): application data, parties, instrument,
   metrology, laboratory conditions, the generated test plan, test execution,
   validation summary, technical review and approval/report. The backend owns
   every rule; this page renders the workflow, captures observations and shows
   exactly how each result was produced (PRD 13.4, 11.3). */

import { api, requireSession, formatApiError, can, canAny, getUser } from './api.js';
import {
  renderShell, caseStatusPill, resultPill, fmt, fmtDate, escapeHtml,
  empty, loading, toast, openModal, promptReason, debounce,
} from './ui.js';

const MASS_UNITS = ['g', 'kg', 'mg', 't', 'lb', 'oz'];
const ATTACHMENT_CATEGORIES = [
  'nameplate_photograph', 'test_setup_photograph', 'calibration_certificate',
  'technical_manual', 'drawing', 'correspondence', 'other',
];
const DEDICATED = [
  'position_label', 'load', 'indication', 'additional_load',
  'elapsed_seconds', 'temperature_c', 'value', 'unit',
];
const CASE_EDITABLE = ['DRAFT', 'ASSIGNED', 'IN_PROGRESS', 'CORRECTION_REQUIRED'];
const STEP_DEFS = [
  { key: 'application', n: 14, label: 'Application', title: 'Application information' },
  { key: 'parties', n: 15, label: 'Parties & assignments', title: 'Applicant, manufacturer and assignments' },
  { key: 'instrument', n: 16, label: 'Instrument', title: 'Instrument model and characteristics' },
  { key: 'metrology', n: 17, label: 'Metrology', title: 'Metrological characteristics and MPE' },
  { key: 'conditions', n: 18, label: 'Conditions', title: 'Laboratory and environmental conditions' },
  { key: 'plan', n: 19, label: 'Test plan', title: 'Applicable test-plan generation' },
  { key: 'execution', n: 20, label: 'Execution', title: 'Test execution and observations' },
  { key: 'summary', n: 21, label: 'Validation', title: 'Validation and summary' },
  { key: 'review', n: 22, label: 'Review', title: 'Technical review and correction' },
  { key: 'approval', n: 23, label: 'Approval', title: 'Approval, finalization and report' },
];

const params = new URLSearchParams(window.location.search);
let caseId = params.get('case');
if (!caseId && params.get('new') === '1') window.location.replace('/evaluations.html#create');
const requestedStep = params.get('step');
const initialStep = STEP_DEFS.some((step) => step.key === requestedStep) ? requestedStep : 'application';

const content = renderShell({
  active: 'evaluations',
  crumb: 'Evaluation',
  title: caseId ? 'Loading\u2026' : 'Evaluation',
  actionsHtml: '<button class="btn-sm" id="refresh-case">Refresh</button>',
});

const state = {
  case: null,
  tests: [],
  plan: null,
  conditions: [],
  audit: [],
  workflow: [],
  attachments: [],
  evidenceReq: null,
  reports: [],
  step: initialStep,
  validation: {},
  aiExtraction: null,
  draftRows: {},
  labUsers: null,
};
let selectedTestId = null;

/* ------------------------------------------------------------- utilities */

function isEditable() {
  const c = state.case;
  if (!c) return false;
  const user = getUser() || {};
  if (['FINALIZED', 'CANCELLED'].includes(c.status)) return false;
  if (['SUPER_ADMIN', 'LAB_ADMIN'].includes(user.role_code)) return true;
  if (user.role_code === 'ENGINEER' && c.engineer_id === user.id) return CASE_EDITABLE.includes(c.status);
  return false;
}
function canEditTests() { return isEditable() && canAny('tests.edit', 'tests.edit.own'); }

function definitionFor(test) { return test.definition || {}; }
function inputSchema(test) { return definitionFor(test).input_schema || {}; }
function columns(test) { return inputSchema(test).columns || []; }

function testValue(obs, key) {
  if (DEDICATED.includes(key)) return obs[key];
  const payload = obs.input_payload || {};
  return payload[key];
}

function rowObject(test, obs) {
  const row = { observation_no: obs ? obs.observation_no : 1 };
  columns(test).forEach((col) => {
    const value = obs ? testValue(obs, col.key) : null;
    row[col.key] = value === null || value === undefined ? '' : String(value);
  });
  return row;
}

function selectedTest() { return state.tests.find((item) => item.id === selectedTestId) || null; }
function statusLabel(value) { return String(value || '').replace(/_/g, ' '); }

function caseStepStatus(stepKey) {
  const c = state.case;
  if (!c) return '';
  if (['application', 'parties', 'instrument', 'metrology'].includes(stepKey)) return c.instrument ? 'done' : '';
  if (stepKey === 'conditions') return state.conditions.length ? 'done' : '';
  if (stepKey === 'plan') return state.plan && (state.plan.items || []).length ? 'done' : '';
  if (stepKey === 'execution') {
    return state.tests.length && state.tests.every((t) => t.status === 'COMPLETED' || t.applicability_status === 'NOT_APPLICABLE') ? 'done' : '';
  }
  if (stepKey === 'summary') return c.overall_result ? 'done' : '';
  if (stepKey === 'review') return ['VERIFIED', 'APPROVED', 'FINALIZED', 'UNDER_APPROVAL'].includes(c.status) ? 'done' : '';
  if (stepKey === 'approval') return ['APPROVED', 'FINALIZED'].includes(c.status) ? 'done' : '';
  return '';
}

/* ---------------------------------------------------------------- loading */

async function loadAll() {
  const [kase, tests, plan, conditions, audit, workflow, attachments, evidenceReq, reports] = await Promise.all([
    api.get('/cases/' + caseId),
    api.get('/cases/' + caseId + '/tests'),
    api.get('/cases/' + caseId + '/test-plan'),
    api.get('/cases/' + caseId + '/conditions'),
    api.get('/cases/' + caseId + '/audit-logs'),
    api.get('/cases/' + caseId + '/workflow-actions'),
    api.get('/cases/' + caseId + '/attachments'),
    api.get('/cases/' + caseId + '/evidence-requirements').catch(() => null),
    api.get('/cases/' + caseId + '/reports').catch(() => []),
  ]);
  state.case = kase;
  state.tests = tests;
  state.plan = plan;
  state.conditions = conditions;
  state.audit = audit;
  state.workflow = workflow;
  state.attachments = attachments;
  state.evidenceReq = evidenceReq;
  state.reports = reports || [];
  if (!selectedTestId || !tests.some((item) => item.id === selectedTestId)) {
    selectedTestId = tests.length ? tests[0].id : null;
  }
}

async function refreshTest(testId) {
  const updated = await api.get('/tests/' + testId);
  const index = state.tests.findIndex((item) => item.id === testId);
  if (index >= 0) state.tests[index] = updated;
  return updated;
}

async function refreshCase() {
  state.case = await api.get('/cases/' + caseId);
  state.tests = await api.get('/cases/' + caseId + '/tests');
  render();
}

/* --------------------------------------------------------------- shell bits */

function railHtml() {
  return '<div class="card tight rail"><ol>' + STEP_DEFS.map((step) => {
    const cls = [step.key === state.step ? 'current' : '', caseStepStatus(step.key)].filter(Boolean).join(' ');
    return '<li><button data-step="' + step.key + '" class="' + cls + '">' +
      escapeHtml(step.label) + '</button></li>';
  }).join('') + '</ol>' +
    '<div class="hint mt-2">Steps 14\u201323 of the evaluation workflow.</div></div>';
}

function nextAction() {
  const c = state.case;
  const unresolved = state.tests.filter((t) =>
    t.applicability_status !== 'NOT_APPLICABLE' && t.status !== 'COMPLETED');
  switch (c.status) {
    case 'DRAFT': return 'Assign an engineer, reviewer and approver to start the evaluation.';
    case 'ASSIGNED':
    case 'IN_PROGRESS':
      return unresolved.length
        ? unresolved.length + ' test(s) still need observations, calculation or completion.'
        : 'All applicable tests are complete \u2014 submit for technical review.';
    case 'CORRECTION_REQUIRED': return 'Corrections requested: ' + (c.last_correction_reason || 'see the review reason.');
    case 'TESTING_COMPLETED': return 'Submitted. Awaiting independent technical review.';
    case 'UNDER_REVIEW': return 'Under review by the technical reviewer.';
    case 'VERIFIED': return 'Verified. Awaiting the approving authority.';
    case 'UNDER_APPROVAL': return 'Awaiting the approval decision.';
    case 'APPROVED': return 'Approved. Generate and finalize the report to lock the record.';
    case 'FINALIZED': return 'Finalized and locked. The report is immutable.';
    case 'REJECTED': return 'Rejected at approval. Create a corrected revision.';
    case 'CANCELLED': return 'Cancelled.';
    default: return '';
  }
}

function asideHtml() {
  const c = state.case;
  const resolved = state.tests.filter((t) => ['PASS', 'FAIL', 'WAIVED'].includes(t.result_status)).length;
  const failed = state.tests.filter((t) => t.result_status === 'FAIL').length;
  const overall = c.overall_result ? resultPill(c.overall_result) : '<span class="faint">not yet determined</span>';
  return '<div class="aside">' +
    '<div class="card tight"><div class="card-title"><h3>Status</h3>' + caseStatusPill(c.status) + '</div>' +
      '<div class="inline" style="justify-content:space-between"><span class="faint small">Overall result</span>' + overall + '</div>' +
      '<div class="inline mt-2" style="justify-content:space-between"><span class="faint small">Tests resolved</span>' +
        '<span class="mono">' + resolved + ' / ' + state.tests.length + '</span></div>' +
      (failed ? '<div class="banner fail mt-2" style="margin-bottom:0"><div>' + failed + ' failing test(s)</div></div>' : '') +
      '<div class="hint mt-2">' + escapeHtml(nextAction()) + '</div></div>' +
    '<div class="card tight"><div class="card-title"><h3>Record</h3></div>' +
      '<div class="small faint">Application</div><div class="mono">' + escapeHtml(c.application_no) + '</div>' +
      '<div class="small faint mt-2">Ruleset</div><div class="mono small">' + escapeHtml(c.standard_version_label || '\u2014') + '</div>' +
      '<div class="small faint mt-2">Report template</div><div class="mono small">' + escapeHtml(c.template_version_label || '\u2014') + '</div>' +
      '<div class="small faint mt-2">Revision</div><div class="mono">' + c.revision_no + '</div>' +
      '<div class="small faint mt-2">Updated</div><div class="small">' + fmtDate(c.updated_at) + '</div></div>' +
    '</div>';
}

/* -------------------------------------------------------- step 14: application */

function stepApplication() {
  const c = state.case;
  const editable = isEditable() && can('cases.edit');
  return '<div class="card"><div class="step-head"><span class="step-no">14</span>' +
    '<div><h2>Application information</h2><div class="faint small">Registration data for this evaluation case.</div></div></div>' +
    '<div class="field-row">' +
      '<div class="field"><label>Application number</label><input value="' + escapeHtml(c.application_no) + '" disabled /></div>' +
      '<div class="field"><label>Status</label><div>' + caseStatusPill(c.status) + '</div></div>' +
      '<div class="field"><label>Priority</label>' +
        (editable
          ? '<select id="f-priority">' + ['low', 'normal', 'high', 'urgent'].map((p) =>
              '<option value="' + p + '"' + (c.priority === p ? ' selected' : '') + '>' + p + '</option>').join('') + '</select>'
          : '<input value="' + escapeHtml(c.priority) + '" disabled />') + '</div>' +
    '</div>' +
    '<div class="field"><label>Title</label>' +
      (editable ? '<input id="f-title" value="' + escapeHtml(c.title || '') + '" />'
                : '<input value="' + escapeHtml(c.title || '') + '" disabled />') + '</div>' +
    '<div class="field"><label>Purpose</label>' +
      (editable ? '<textarea id="f-purpose" rows="2">' + escapeHtml(c.purpose || '') + '</textarea>'
                : '<textarea rows="2" disabled>' + escapeHtml(c.purpose || '') + '</textarea>') + '</div>' +
    '<div class="field"><label>Scope notes</label>' +
      (editable ? '<textarea id="f-scope" rows="2">' + escapeHtml(c.scope_notes || '') + '</textarea>'
                : '<textarea rows="2" disabled>' + escapeHtml(c.scope_notes || '') + '</textarea>') + '</div>' +
    (editable ? '<div class="inline"><button class="btn-primary btn-sm" id="save-application">Save application data</button>' +
      '<span class="faint small">Every edit is written to the audit trail.</span></div>'
      : '<div class="hint">This case is ' + escapeHtml(statusLabel(c.status)) + '; it is read-only for your role.</div>') +
    '</div>';
}

function bindApplication() {
  const button = document.getElementById('save-application');
  if (!button) return;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const payload = {
        title: document.getElementById('f-title').value.trim() || null,
        purpose: document.getElementById('f-purpose').value.trim() || null,
        scope_notes: document.getElementById('f-scope').value.trim() || null,
        priority: document.getElementById('f-priority').value,
      };
      state.case = await api.patch('/cases/' + caseId, payload);
      toast('Application data saved.', 'success');
      render();
    } catch (error) {
      toast(formatApiError(error), 'error');
      button.disabled = false;
    }
  });
}

/* ------------------------------------------------------------ step 15: parties */

function personFields(person) {
  return [
    ['Name', person && person.full_name],
    ['User ID', person && person.user_code],
    ['Email', person && person.email],
  ];
}

function partyCard(title, party, fields) {
  if (!party) return '<div class="card"><div class="card-title"><h3>' + escapeHtml(title) + '</h3></div>' +
    '<div class="faint small">Not recorded.</div></div>';
  return '<div class="card"><div class="card-title"><h3>' + escapeHtml(title) + '</h3></div>' +
    '<div class="stack">' + fields.map((pair) =>
      '<div><div class="small faint">' + escapeHtml(pair[0]) + '</div><div>' + escapeHtml(pair[1] || '\u2014') + '</div></div>'
    ).join('') + '</div></div>';
}

function stepParties() {
  const c = state.case;
  const instrument = c.instrument || {};
  const manufacturer = c.manufacturer || instrument.manufacturer || {};
  const canAssign = can('cases.assign') && !['FINALIZED', 'CANCELLED'].includes(c.status);
  const assignForm = canAssign
    ? '<div class="card mt-3"><div class="card-title"><h3>Assignments</h3>' +
        '<span class="faint small">Engineer \u2192 reviewer \u2192 approver separation of duties</span></div>' +
        '<div class="field-row">' +
          '<div class="field"><label>Engineer</label><select id="a-engineer">' + userOptions(c.engineer_id) + '</select></div>' +
          '<div class="field"><label>Reviewer</label><select id="a-reviewer">' + userOptions(c.reviewer_id) + '</select></div>' +
          '<div class="field"><label>Approver</label><select id="a-approver">' + userOptions(c.approver_id) + '</select></div>' +
        '</div><button class="btn-primary btn-sm" id="save-assignments">Save assignments</button></div>'
    : '';
  return '<div class="card"><div class="step-head"><span class="step-no">15</span>' +
    '<div><h2>Applicant, manufacturer and assignments</h2>' +
    '<div class="faint small">Parties are master records; assignments decide who may act at each stage.</div></div></div>' +
    '<div class="grid cols-2">' +
      partyCard('Applicant', c.applicant, [
        ['Name', c.applicant && c.applicant.name], ['Organisation type', c.applicant && c.applicant.organisation_type],
        ['Contact', c.applicant && c.applicant.contact_person], ['Email', c.applicant && c.applicant.email],
        ['City', c.applicant && c.applicant.city]]) +
      partyCard('Manufacturer', manufacturer, [
        ['Name', manufacturer.name], ['Contact', manufacturer.contact_person],
        ['Email', manufacturer.email], ['City', manufacturer.city]]) +
    '</div>' +
    '<div class="grid cols-3 mt-3">' +
      partyCard('Assigned engineer', c.engineer, personFields(c.engineer)) +
      partyCard('Reviewer', c.reviewer, personFields(c.reviewer)) +
      partyCard('Approver', c.approver, personFields(c.approver)) +
    '</div>' + assignForm + '</div>';
}

function userOptions(selected) {
  const users = state.labUsers || [];
  return '<option value="">\u2014 unassigned \u2014</option>' + users.map((user) =>
    '<option value="' + user.id + '"' + (user.id === selected ? ' selected' : '') + '>' +
    escapeHtml(user.full_name) + ' (' + escapeHtml(user.role_code) + ')</option>').join('');
}

function bindParties() {
  const button = document.getElementById('save-assignments');
  if (!button) return;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const payload = {};
      ['engineer', 'reviewer', 'approver'].forEach((field) => {
        const value = document.getElementById('a-' + field).value;
        if (value) payload[field + '_id'] = value;
      });
      state.case = await api.post('/cases/' + caseId + '/assignments', payload);
      toast('Assignments saved.', 'success');
      render();
    } catch (error) {
      toast(formatApiError(error), 'error');
      button.disabled = false;
    }
  });
}

async function loadLabUsers() {
  if (state.labUsers) return;
  try {
    const data = await api.get('/users', { query: { page_size: 200 } });
    state.labUsers = data.items;
  } catch (error) {
    state.labUsers = [];
  }
}
/* --------------------------------------------------------- step 16: instrument */

function stepInstrument() {
  const instrument = (state.case.instrument || {});
  const ranges = instrument.ranges || [];
  const rows = ranges.length
    ? ranges.map((range) => '<tr><td class="mono">' + range.range_no + '</td>' +
        '<td class="num">' + fmt(range.min_capacity, { unit: range.unit }) + '</td>' +
        '<td class="num">' + fmt(range.max_capacity, { unit: range.unit }) + '</td>' +
        '<td class="num">' + fmt(range.verification_scale_interval, { unit: range.unit }) + '</td>' +
        '<td class="num">' + fmt(range.actual_scale_interval, { unit: range.unit }) + '</td></tr>').join('')
    : '<tr><td colspan="5" class="faint small">Single-range instrument.</td></tr>';
  return '<div class="card"><div class="step-head"><span class="step-no">16</span>' +
    '<div><h2>Instrument model and characteristics</h2>' +
    '<div class="faint small">Recorded when the case was registered; changes to these values require a new master record.</div></div></div>' +
    '<div class="field-row">' +
      '<div class="field"><label>Model</label><input value="' + escapeHtml(instrument.model || '') + '" disabled /></div>' +
      '<div class="field"><label>Type designation</label><input value="' + escapeHtml(instrument.type_designation || '') + '" disabled /></div>' +
      '<div class="field"><label>Serial number</label><input value="' + escapeHtml(instrument.serial_number || '') + '" disabled /></div>' +
      '<div class="field"><label>Accuracy class</label><input value="' + escapeHtml(instrument.instrument_class || '') + '" disabled /></div>' +
    '</div>' +
    '<div class="grid cols-3 mt-3">' +
      infoCard('Capacity', [
        ['Max', fmt(instrument.max_capacity, { unit: instrument.unit })],
        ['Min', fmt(instrument.min_capacity, { unit: instrument.unit })],
      ]) +
      infoCard('Scale intervals', [
        ['Verification interval e', fmt(instrument.verification_scale_interval, { unit: instrument.unit })],
        ['Actual interval d', fmt(instrument.actual_scale_interval, { unit: instrument.unit })],
      ]) +
      infoCard('Construction', [
        ['Electronic', instrument.is_electronic ? 'Yes' : 'No'],
        ['Zero device', instrument.has_zero_device ? 'Yes' : 'No'],
        ['Tare device', instrument.has_tare_device ? 'Yes' : 'No'],
        ['Level indicator', instrument.has_level_indicator ? 'Yes' : 'No'],
      ]) +
    '</div>' +
    '<div class="card-title mt-3"><h3>Ranges</h3></div>' +
    '<div class="table-wrap"><table><thead><tr><th>#</th><th class="num">Min</th><th class="num">Max</th>' +
      '<th class="num">e</th><th class="num">d</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
    nameplatePanel() +
    requiredEvidencePanel() +
    '</div>';
}

function infoCard(title, pairs) {
  return '<div class="card tight"><div class="card-title"><h3>' + escapeHtml(title) + '</h3></div>' +
    pairs.map((pair) => '<div class="inline" style="justify-content:space-between">' +
      '<span class="faint small">' + escapeHtml(pair[0]) + '</span><span class="mono">' + escapeHtml(pair[1]) + '</span></div>').join('') +
    '</div>';
}

function nameplatePanel() {
  const extraction = state.aiExtraction;
  let result = '<div class="hint">Upload a nameplate photograph to compare an AI reading with the recorded instrument data. The extractor is advisory: it never edits the record.</div>';
  if (extraction) {
    if (!extraction.available) {
      result = '<div class="banner warn"><div>' + escapeHtml(extraction.message || 'The AI feature is unavailable.') + '</div></div>';
    } else if (!extraction.fields.length) {
      result = '<div class="banner warn"><div>' + escapeHtml(extraction.message || 'No fields could be read.') + '</div></div>';
    } else {
      const instrument = state.case.instrument || {};
      result = '<div class="table-wrap"><table><thead><tr><th>Field</th><th>AI reading</th><th>Recorded</th><th>Confidence</th></tr></thead><tbody>' +
        extraction.fields.map((field) => '<tr' + (field.low_confidence ? ' class="row-invalid"' : '') + '>' +
          '<td>' + escapeHtml(field.label || field.key) + '</td>' +
          '<td class="mono">' + escapeHtml(field.value || '\u2014') + '</td>' +
          '<td class="mono faint">' + escapeHtml(instrumentValue(field.key, instrument)) + '</td>' +
          '<td class="num">' + (field.confidence === null || field.confidence === undefined ? '\u2014' : fmt(field.confidence)) +
            (field.low_confidence ? ' <span class="pill pill-warn">low</span>' : '') + '</td></tr>').join('') +
        '</tbody></table></div>' +
        '<div class="inline mt-2"><button class="btn-sm" id="dismiss-extraction">Dismiss</button>' +
        '<span class="faint small">' + escapeHtml(extraction.message || '') + '</span></div>';
    }
  }
  return '<div class="card tight mt-3"><div class="card-title"><h3>AI nameplate extraction</h3>' +
    '<span class="pill pill-accent">advisory only</span></div>' +
    '<div class="inline" style="margin-bottom:10px">' +
      '<input type="file" id="nameplate-file" accept="image/*,.pdf" style="max-width:280px" />' +
      '<button class="btn-sm" id="run-nameplate">Extract fields</button></div>' + result + '</div>';
}

function instrumentValue(key, instrument) {
  const map = {
    model: instrument.model, type_designation: instrument.type_designation,
    serial_number: instrument.serial_number, instrument_class: instrument.instrument_class,
    max_capacity: instrument.max_capacity, min_capacity: instrument.min_capacity,
    verification_scale_interval: instrument.verification_scale_interval,
    actual_scale_interval: instrument.actual_scale_interval, unit: instrument.unit,
  };
  return fmt(map[key]);
}

function bindInstrument() {
  bindRequiredEvidence();
  const button = document.getElementById('run-nameplate');
  const dismiss = document.getElementById('dismiss-extraction');
  if (dismiss) dismiss.addEventListener('click', () => { state.aiExtraction = null; render(); });
  if (!button) return;
  button.addEventListener('click', async () => {
    const input = document.getElementById('nameplate-file');
    if (!input.files.length) { toast('Choose a nameplate image or PDF first.', 'warn'); return; }
    const form = new FormData();
    form.append('file', input.files[0]);
    form.append('case_id', caseId);
    button.disabled = true;
    try {
      state.aiExtraction = await api.upload('/ai/nameplate-extract', form);
      toast('Extraction complete \u2014 review the fields before relying on them.', 'success');
    } catch (error) {
      toast(formatApiError(error), 'error');
    } finally {
      render();
    }
  });
}

/* ---------------------------------------------------------- step 17: metrology */

function stepMetrology() {
  const instrument = state.case.instrument || {};
  return '<div class="card"><div class="step-head"><span class="step-no">17</span>' +
    '<div><h2>Metrological characteristics</h2>' +
    '<div class="faint small">Maximum permissible errors are configuration data from the active rule set ' +
      '(<span class="mono">' + escapeHtml(state.case.standard_version_label || '') + '</span>).</div></div></div>' +
    '<div class="grid cols-2">' +
      infoCard('Declared characteristics', [
        ['Accuracy class', instrument.instrument_class || '\u2014'],
        ['Max capacity', fmt(instrument.max_capacity, { unit: instrument.unit })],
        ['Min capacity', fmt(instrument.min_capacity, { unit: instrument.unit })],
        ['Verification scale interval e', fmt(instrument.verification_scale_interval, { unit: instrument.unit })],
        ['Actual scale interval d', fmt(instrument.actual_scale_interval, { unit: instrument.unit })],
      ]) +
      '<div class="card tight"><div class="card-title"><h3>MPE lookup</h3>' +
        '<span class="pill pill-accent">rule engine</span></div>' +
        '<div class="hint">Ask the deterministic engine which MPE band applies to a given load. ' +
          'This is the same resolution used during compliance evaluation.</div>' +
        '<div class="field-row mt-2">' +
          '<div class="field"><label>Applied load</label><div class="input-unit"><input id="mpe-load" class="numeric" value="' + escapeHtml(instrument.max_capacity || '') + '" />' +
            '<span class="unit">' + escapeHtml(instrument.unit || 'g') + '</span></div></div>' +
        '</div>' +
        '<button class="btn-sm btn-primary" id="mpe-lookup">Resolve MPE</button>' +
        '<div id="mpe-result" class="mt-3"></div></div>' +
    '</div></div>';
}

function bindMetrology() {
  const button = document.getElementById('mpe-lookup');
  if (!button) return;
  button.addEventListener('click', async () => {
    const instrument = state.case.instrument || {};
    const load = document.getElementById('mpe-load').value.trim();
    if (!load) { toast('Enter a load to resolve the MPE.', 'warn'); return; }
    button.disabled = true;
    try {
      const result = await api.post('/calculations/mpe', {
        instrument_class: instrument.instrument_class,
        load,
        e: instrument.verification_scale_interval,
        stage: 'verification',
      });
      document.getElementById('mpe-result').innerHTML =
        '<div class="calc-panel"><div class="formula">' + escapeHtml(result.rule_id || '') + '</div><dl>' +
        '<dt>Band</dt><dd>' + escapeHtml(result.band || '\u2014') + '</dd>' +
        '<dt>MPE (\u00b1)</dt><dd>' + escapeHtml(fmt(result.mpe, { unit: instrument.unit })) + '</dd>' +
        '<dt>m / e</dt><dd>' + escapeHtml(fmt(result.m_over_e)) + '</dd>' +
        '<dt>Clause</dt><dd>' + escapeHtml(result.clause_reference || '\u2014') + '</dd></dl></div>';
    } catch (error) {
      toast(formatApiError(error), 'error');
    } finally {
      button.disabled = false;
    }
  });
}

/* --------------------------------------------------------- step 18: conditions */

function stepConditions() {
  const editable = isEditable() && canAny('tests.edit', 'tests.edit.own', 'cases.edit');
  const rows = state.conditions.length
    ? state.conditions.map((condition) => '<tr>' +
        '<td>' + escapeHtml(condition.label) + '</td>' +
        '<td class="num">' + fmt(condition.temperature_c, { unit: '\u00b0C' }) + '</td>' +
        '<td class="num">' + fmt(condition.relative_humidity_pct, { unit: '%' }) + '</td>' +
        '<td class="num">' + fmt(condition.barometric_pressure_kpa, { unit: 'kPa' }) + '</td>' +
        '<td class="small faint">' + fmtDate(condition.started_at) + '</td>' +
        '<td class="small">' + escapeHtml(condition.notes || '\u2014') + '</td></tr>').join('')
    : '<tr><td colspan="6" class="faint small">No environmental conditions recorded.</td></tr>';
  const form = editable
    ? '<div class="card tight mt-3"><div class="card-title"><h3>Record conditions</h3></div>' +
        '<div class="field-row">' +
          '<div class="field"><label>Label</label><input id="c-label" value="Ambient" /></div>' +
          '<div class="field"><label>Temperature (\u00b0C)</label><input id="c-temp" class="numeric" /></div>' +
          '<div class="field"><label>Relative humidity (%)</label><input id="c-humidity" class="numeric" /></div>' +
          '<div class="field"><label>Pressure (kPa)</label><input id="c-pressure" class="numeric" /></div>' +
        '</div>' +
        '<div class="field"><label>Notes</label><input id="c-notes" /></div>' +
        '<button class="btn-primary btn-sm" id="save-condition">Add condition record</button></div>'
    : '';
  return '<div class="card"><div class="step-head"><span class="step-no">18</span>' +
    '<div><h2>Laboratory and environmental conditions</h2>' +
    '<div class="faint small">Conditions under which the observations were taken; they form part of the evidence.</div></div></div>' +
    '<div class="table-wrap"><table><thead><tr><th>Label</th><th class="num">Temp</th><th class="num">RH</th>' +
      '<th class="num">Pressure</th><th>Started</th><th>Notes</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
    form + '</div>';
}

function bindConditions() {
  const button = document.getElementById('save-condition');
  if (!button) return;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const payload = {
        label: document.getElementById('c-label').value.trim() || 'Ambient',
        temperature_c: document.getElementById('c-temp').value.trim() || null,
        relative_humidity_pct: document.getElementById('c-humidity').value.trim() || null,
        barometric_pressure_kpa: document.getElementById('c-pressure').value.trim() || null,
        notes: document.getElementById('c-notes').value.trim() || null,
      };
      await api.post('/cases/' + caseId + '/conditions', payload);
      state.conditions = await api.get('/cases/' + caseId + '/conditions');
      toast('Condition record added.', 'success');
      render();
    } catch (error) {
      toast(formatApiError(error), 'error');
      button.disabled = false;
    }
  });
}

/* --------------------------------------------------------------- step 19: plan */

function stepPlan() {
  const items = (state.plan && state.plan.items) || [];
  const canRegenerate = isEditable() && canAny('cases.edit', 'tests.edit.own');
  const rows = items.length
    ? items.map((item) => '<tr>' +
        '<td class="mono">' + escapeHtml(item.test_code) + '</td>' +
        '<td>' + escapeHtml(item.name) + '<div class="faint small mono">' + escapeHtml(item.clause_reference || '') + '</div></td>' +
        '<td>' + escapeHtml(item.category) + '</td>' +
        '<td class="mono">' + item.sequence_no + '</td>' +
        '<td>' + (item.applicable ? '<span class="pill pill-info">APPLICABLE</span>' : '<span class="pill pill-na">NOT APPLICABLE</span>') +
          (item.manual_override ? ' <span class="pill pill-warn">manual</span>' : '') + '</td>' +
        '<td>' + implementationCell(item) + '</td>' +
        '<td class="small">' + escapeHtml(item.reason) +
          ((item.trace || []).length ? '<details class="faint small"><summary>trace</summary><div class="mono">' +
            item.trace.map((t) => escapeHtml(t.check) + ' \u2192 ' + (t.result ? 'true' : 'false')).join('<br/>') +
            '</div></details>' : '') + '</td></tr>').join('')
    : '<tr><td colspan="7" class="faint small">No plan items.</td></tr>';
  return '<div class="card"><div class="step-head"><span class="step-no">19</span>' +
    '<div><h2>Applicable test-plan generation</h2>' +
    '<div class="faint small">Applicability was evaluated against the instrument characteristics and the active rule set. ' +
      'The plan is preserved with the case and is never silently changed.</div></div></div>' +
    '<div class="inline" style="margin-bottom:10px">' +
      '<span class="faint small">Ruleset <span class="mono">' + escapeHtml((state.plan && state.plan.ruleset_label) || '') + '</span></span>' +
      '<span class="faint small">Generated ' + fmtDate(state.plan && state.plan.generated_at) + '</span>' +
      '<span class="right"></span>' +
      (state.plan && state.plan.preserved ? '<span class="pill pill-info">preserved</span>' : '') +
      (canRegenerate ? '<button class="btn-sm" id="regenerate-plan">Regenerate plan</button>' : '') +
    '</div>' +
    '<div class="table-wrap"><table><thead><tr><th>Code</th><th>Test</th><th>Category</th><th>Seq</th>' +
      '<th>Applicability</th><th>Implementation</th><th>Reason</th></tr></thead><tbody>' + rows +
      '</tbody></table></div>' +
    (unsupportedNotice(items)) + '</div>';
}

function implementationCell(item) {
  // A catalogue entry can be complete and still have no deterministic
  // calculator. That is reported as "not implemented" with the reason the
  // catalogue gives, never hidden (audit item 7).
  if (item.supported === false || item.implementation_status === 'not_implemented') {
    const reason = item.unsupported_reason || 'no deterministic calculator is available';
    return '<span class="pill pill-warn" title="' + escapeHtml(reason) + '">NOT IMPLEMENTED</span>' +
      '<div class="faint small">' + escapeHtml(reason) + '</div>';
  }
  return '<span class="pill pill-pass">IMPLEMENTED</span>';
}

function unsupportedNotice(items) {
  const unsupported = (items || []).filter((item) =>
    item.applicable && (item.supported === false || item.implementation_status === 'not_implemented'));
  if (!unsupported.length) return '';
  return '<div class="banner warn small" style="margin-top:10px">' +
    '<strong>' + unsupported.length + ' applicable test(s) cannot be executed yet:</strong> ' +
    unsupported.map((item) => escapeHtml(item.test_code)).join(', ') +
    '. They are shown here rather than removed from the plan; their limits must be reviewed and added ' +
    'to the rule set before the engine can decide them.</div>';
}

function bindPlan() {
  const button = document.getElementById('regenerate-plan');
  if (!button) return;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await api.post('/cases/' + caseId + '/test-plan', {});
      state.plan = await api.get('/cases/' + caseId + '/test-plan');
      state.tests = await api.get('/cases/' + caseId + '/tests');
      toast('Test plan regenerated.', 'success');
      render();
    } catch (error) {
      toast(formatApiError(error), 'error');
      button.disabled = false;
    }
  });
}
/* ----------------------------------------------------------- step 20: execution */

const savers = {};
const dirtyTests = new Set();

function setSaveState(kind, text) {
  const node = document.getElementById('test-save-state');
  if (!node) return;
  node.className = 'save-state ' + kind;
  node.textContent = text;
}

function testListHtml() {
  return '<div class="card tight"><div class="card-title"><h3>Tests</h3>' +
    '<span class="faint small">' + state.tests.length + ' in plan</span></div>' +
    state.tests.map((test) => {
      const definition = test.definition || {};
      const na = test.applicability_status === 'NOT_APPLICABLE';
      const unsupported = definition.implementation_status === 'not_implemented';
      const label = (definition.test_code || '') + ' \u00b7 ' + (definition.name || '') +
        (unsupported ? ' \u2014 not implemented: ' + (definition.unsupported_reason || '') : '');
      return '<button class="test-item ' + (test.id === selectedTestId ? 'active' : '') + '" data-test="' + test.id + '" title="' + escapeHtml(label) + '"' + (na || unsupported ? ' disabled' : '') + '>' +
        '<span class="code">' + escapeHtml(definition.test_code || '') + '</span>' +
        '<span class="name">' + escapeHtml(definition.name || '') + '</span>' +
        '<span style="flex:0 0 6px"></span>' + (unsupported ? '<span class="pill pill-warn">unsupported</span>' : '') + resultPill(test.result_status) + '</button>';
    }).join('') + '</div>';
}

function hasLabelColumn(test) {
  return columns(test).some((col) => col.key === 'position_label' || col.key === 'label');
}

function cellInput(test, col, value, rowIndex, editable) {
  const disabled = editable ? '' : ' disabled';
  const invalid = ' data-cell="' + escapeHtml(col.key) + '"';
  if (col.type === 'boolean') {
    const checked = value === true || value === 'true' || value === 1 || value === '1';
    return '<input type="checkbox" data-col="' + escapeHtml(col.key) + '"' + (checked ? ' checked' : '') + disabled + ' />';
  }
  if (col.type === 'unit') {
    const current = value || ((state.case.instrument || {}).unit) || 'g';
    return '<select data-col="' + escapeHtml(col.key) + '"' + disabled + '>' + MASS_UNITS.map((unit) =>
      '<option value="' + unit + '"' + (unit === current ? ' selected' : '') + '>' + unit + '</option>').join('') + '</select>';
  }
  const type = col.type === 'decimal' ? 'text' : 'text';
  const cls = col.type === 'decimal' ? ' class="numeric"' : '';
  const mode = col.type === 'decimal' ? ' inputmode="decimal" autocomplete="off"' : '';
  return '<input type="' + type + '"' + cls + mode + invalid + ' data-col="' + escapeHtml(col.key) + '" value="' +
    escapeHtml(value === null || value === undefined ? '' : value) + '" placeholder="' + escapeHtml(col.label) + '"' + disabled + ' />';
}

function tableForm(test, editable) {
  const cols = columns(test);
  const labelColumn = hasLabelColumn(test);
  const rows = currentRows(test).map((values, index) => ({ index: index + 1, values }));
  const header = '<tr>' + (labelColumn ? '' : '<th>' + escapeHtml(inputSchema(test).row_label || 'Row') + '</th>') +
    cols.map((col) => '<th' + (col.type === 'decimal' ? ' class="num"' : '') + '>' + escapeHtml(col.label) +
      (col.required ? ' <span class="req"></span>' : '') + '</th>').join('') +
    (editable ? '<th></th>' : '') + '</tr>';
  const body = rows.map((row) => '<tr data-row="' + row.index + '">' +
    (labelColumn ? '' : '<td><input data-col="position_label" value="' +
      escapeHtml(row.values.position_label || '') + '" placeholder="' + escapeHtml((inputSchema(test).row_label || 'Row')) + '"' +
      (editable ? '' : ' disabled') + ' /></td>') +
    cols.map((col) => '<td' + (col.type === 'decimal' ? ' class="num"' : '') + '>' +
      cellInput(test, col, row.values[col.key], row.index, editable) + '</td>').join('') +
    (editable ? '<td><button class="btn-ghost btn-sm" data-remove-row title="Remove row">\u2715</button></td>' : '') +
    '</tr>').join('');
  return '<div class="table-wrap"><table data-obs-table="' + test.id + '"><thead>' + header + '</thead>' +
    '<tbody data-obs-body="' + test.id + '">' + body + '</tbody></table></div>' +
    (editable ? '<button class="btn-sm mt-2" data-add-row>+ Add row</button>' : '');
}

function checklistForm(test, editable) {
  const rows = currentRows(test);
  const body = rows.map((values, index) => '<div class="checklist-row" data-row="' + (index + 1) + '">' +
    '<input data-col="item_code" value="' + escapeHtml(values.item_code || '') + '" placeholder="Checklist item"' + (editable ? '' : ' disabled') + ' />' +
    '<label class="inline" style="gap:6px;margin:0"><input type="checkbox" data-col="conforms"' +
      (isTruthy(values.conforms) ? ' checked' : '') + (editable ? '' : ' disabled') + ' /> <span class="small">Conforms</span></label>' +
    '<input data-col="remarks" value="' + escapeHtml(values.remarks || '') + '" placeholder="Remarks"' + (editable ? '' : ' disabled') + ' />' +
    (editable ? '<button class="btn-ghost btn-sm" data-remove-row title="Remove item">\u2715</button>' : '<span></span>') +
    '</div>').join('');
  return '<div data-obs-table="' + test.id + '" data-obschk="1">' +
    '<div class="checklist-row faint small" data-head><span>Item</span><span>Conforms</span><span>Remarks</span><span></span></div>' +
    '<div data-obs-body="' + test.id + '">' + body + '</div></div>' +
    (editable ? '<button class="btn-sm mt-2" data-add-row>+ Add checklist item</button>' : '');
}

function isTruthy(value) {
  return value === true || value === 'true' || value === 1 || value === '1';
}

function calcPanelHtml(test) {
  const result = state.validation[test.id];
  if (!result) return '<div class="faint small">Run <strong>Validate</strong> to see the rule, the formula and the intermediate arithmetic behind this result.</div>';
  const rows = (result.rows || []).map((row) => '<tr' + (row.within === false ? ' class="row-invalid"' : '') + '>' +
    '<td class="mono">' + row.observation_no + '</td>' +
    '<td>' + escapeHtml(row.label || '\u2014') + '</td>' +
    '<td class="num">' + fmt(row.load) + '</td>' +
    '<td class="num">' + fmt(row.indication) + '</td>' +
    '<td class="num">' + fmt(row.error) + '</td>' +
    '<td class="num">' + fmt(row.mpe) + '</td>' +
    '<td class="num">' + fmt(row.margin) + '</td>' +
    '<td>' + (row.within === false ? '<span class="pill pill-fail">outside</span>' : '<span class="pill pill-pass">within</span>') + '</td></tr>').join('');
  const intermediates = Object.entries(result.intermediates || {}).map((pair) =>
    '<dt>' + escapeHtml(pair[0].replace(/_/g, ' ')) + '</dt><dd>' + escapeHtml(typeof pair[1] === 'object' ? JSON.stringify(pair[1]) : String(pair[1])) + '</dd>').join('');
  return '<div class="calc-panel">' +
    '<div class="inline"><span class="pill ' + (result.status === 'PASS' ? 'pill-pass' : result.status === 'FAIL' ? 'pill-fail' : 'pill-warn') + '">' +
      escapeHtml(statusLabel(result.status)) + '</span>' +
      '<span class="mono small">' + escapeHtml(result.rule_id || '') + '</span>' +
      '<span class="faint small">' + escapeHtml(result.clause_reference || '') + '</span></div>' +
    (result.explanation ? '<p class="small mt-2">' + escapeHtml(result.explanation) + '</p>' : '') +
    (intermediates ? '<div class="formula">' + escapeHtml((result.intermediates || {}).method || 'intermediate values') + '</div><dl>' + intermediates + '</dl>' : '') +
    (rows ? '<div class="table-wrap mt-3"><table><thead><tr><th>#</th><th>Row</th><th class="num">L</th><th class="num">I</th>' +
      '<th class="num">Error</th><th class="num">MPE</th><th class="num">Margin</th><th>Verdict</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '') +
    (result.errors && result.errors.length ? '<div class="banner fail mt-3"><div><strong>Validation errors</strong>' +
      result.errors.map(escapeHtml).join('<br/>') + '</div></div>' : '') +
    (result.warnings && result.warnings.length ? '<div class="banner warn mt-3"><div><strong>Warnings</strong>' +
      result.warnings.map(escapeHtml).join('<br/>') + '</div></div>' : '') +
    '<div class="hint mt-2">Rounding policy: <span class="mono">' + escapeHtml(result.rounding_policy || '') +
      '</span> \u00b7 engine <span class="mono">' + escapeHtml(result.engine_version || '') + '</span></div>' +
    '</div>';
}

function stepExecution() {
  const test = selectedTest();
  const list = testListHtml();
  if (!test) {
    return '<div class="card">' + empty('No tests in the plan.') + '</div>';
  }
  const definition = definitionFor(test);
  const editable = canEditTests() && test.applicability_status !== 'NOT_APPLICABLE';
  const layout = inputSchema(test).layout || 'table';
  const form = layout === 'checklist' ? checklistForm(test, editable) : tableForm(test, editable);
  const compliance = test.compliance_result;
  const actions = editable
    ? '<div class="inline mt-3">' +
        '<button class="btn-sm" id="btn-validate">Validate (no save)</button>' +
        '<button class="btn-primary btn-sm" id="btn-calculate">Calculate &amp; store</button>' +
        '<button class="btn-sm" id="btn-complete">Mark complete</button>' +
        '<span class="right"></span>' +
        (can('ai.use') ? '<button class="btn-sm" id="btn-anomaly">AI anomaly check</button>' : '') +
        (can('override.request') ? '<button class="btn-sm" id="btn-na">Not applicable\u2026</button>' +
          '<button class="btn-sm btn-danger" id="btn-override">Override\u2026</button>' : '') +
      '</div>'
    : '<div class="banner mt-3" style="margin-bottom:0"><div>' +
        (test.applicability_status === 'NOT_APPLICABLE'
          ? 'This test is marked not applicable: ' + escapeHtml(test.applicability_reason || '')
          : 'Read-only for your role or for the current case status.') + '</div></div>';
  return '<div class="exec-grid">' +
    '<div>' + list + '</div>' +
    '<div><div class="card">' +
      '<div class="step-head"><span class="step-no">20</span>' +
        '<div style="flex:1"><h2>' + escapeHtml(definition.test_code || '') + ' \u00b7 ' + escapeHtml(definition.name || '') + '</h2>' +
        '<div class="faint small">' + escapeHtml(definition.description || '') + '</div>' +
        '<div class="faint small mono">' + escapeHtml(definition.clause_reference || '') + '</div></div>' +
        '<div>' + resultPill(test.result_status) + '</div></div>' +
      '<div class="inline" style="margin-bottom:10px">' +
        '<span class="pill pill-na">' + escapeHtml(statusLabel(test.status)) + '</span>' +
        (test.applicability_reason ? '<span class="faint small">' + escapeHtml(test.applicability_reason) + '</span>' : '') +
        '<span class="right"></span><span class="save-state" id="test-save-state"></span></div>' +
      form + actions +
      '<div class="table-wrap mt-3"><table><tbody>' +
        '<tr><td class="faint small">Measured value</td><td class="mono">' + fmt(compliance && compliance.measured_value) + '</td>' +
        '<td class="faint small">Limit</td><td class="mono">' + fmt(compliance && compliance.limit_value) + '</td></tr>' +
        '<tr><td class="faint small">Margin</td><td class="mono">' + fmt(compliance && compliance.margin) + '</td>' +
        '<td class="faint small">Rule</td><td class="mono small">' + escapeHtml((compliance && compliance.rule_id) || '\u2014') + '</td></tr>' +
      '</tbody></table></div>' +
      '</div>' +
      anomalyPanel(test) +
      '<div class="card mt-3"><div class="card-title"><h3>How calculated</h3>' +
        '<span class="pill pill-accent">deterministic</span></div>' +
        '<div id="calc-panel">' + calcPanelHtml(test) + '</div></div>' +
      (editable ? evidencePanel() : '') +
    '</div>' +
    '</div>';
}

function anomalyPanel(test) {
  const findings = (test.observations || []).filter((obs) => obs.anomaly_flag);
  if (!findings.length) {
    return '<div class="card tight"><div class="card-title"><h3>AI review</h3></div>' +
      '<div class="hint">Anomaly warnings appear here. They never change a compliance result; each one needs a human disposition.</div></div>';
  }
  return '<div class="card tight"><div class="card-title"><h3>AI review</h3><span class="pill pill-warn">' + findings.length + '</span></div>' +
    findings.map((obs) => '<div class="card tight" style="margin-bottom:8px">' +
      '<div class="inline" style="justify-content:space-between"><span class="mono small">row ' + obs.observation_no + '</span>' +
      (obs.anomaly_disposition ? '<span class="pill pill-na">' + escapeHtml(obs.anomaly_disposition) + '</span>' : '<span class="pill pill-warn">open</span>') + '</div>' +
      '<div class="small mt-2">' + escapeHtml(obs.anomaly_note || 'Statistical outlier') + '</div>' +
      (obs.anomaly_disposition ? '' : '<div class="inline mt-2"><button class="btn-sm" data-disposition="confirmed" data-obs="' + obs.observation_no + '">Confirm</button>' +
        '<button class="btn-sm" data-disposition="dismissed" data-obs="' + obs.observation_no + '">Dismiss</button></div>') +
      '</div>').join('') + '</div>';
}

function requiredEvidencePanel() {
  const req = state.evidenceReq;
  if (!req || !req.required.length) return '';
  const editable = canEditTests();
  const rows = req.required.map((category) => {
    const present = req.present.indexOf(category) >= 0;
    const slot = present || !editable
      ? ''
      : '<span class="inline"><input type="file" id="req-file-' + category + '" accept="image/*" style="max-width:190px" />' +
        '<button class="btn-sm" data-upload-required="' + category + '">Upload</button></span>';
    return '<div class="inline" style="justify-content:space-between;margin-bottom:6px">' +
      '<span>' + (present ? '<span class="pill pill-pass">attached</span>' : '<span class="pill pill-warn">required</span>') +
      ' <span class="small">' + escapeHtml(statusLabel(category)) + '</span>' +
      (present ? '<span class="faint small" style="margin-left:8px">' + escapeHtml(req.present.length + '/' + req.required.length) + ' attachments</span>' : '') + '</span>' +
      slot + '</div>';
  }).join('');
  return '<div class="card tight mt-3"><div class="card-title"><h3>Mandatory photographs</h3>' +
    (req.satisfied
      ? '<span class="pill pill-pass">complete</span>'
      : '<span class="pill pill-warn">' + req.missing.length + ' outstanding</span>') + '</div>' +
    '<div class="hint">Two clear photographs are required before this evaluation can be submitted ' +
    'for technical review: the instrument nameplate and the test setup.</div>' +
    rows + '</div>';
}

async function uploadRequiredEvidence(category) {
  const input = document.getElementById('req-file-' + category);
  if (!input || !input.files.length) { toast('Choose an image to upload.', 'warn'); return; }
  const form = new FormData();
  form.append('file', input.files[0]);
  form.append('category', category);
  form.append('caption', statusLabel(category));
  form.append('auto_classify', 'false');
  if (selectedTestId) form.append('test_instance_id', selectedTestId);
  try {
    await api.upload('/cases/' + caseId + '/attachments', form);
    state.attachments = await api.get('/cases/' + caseId + '/attachments');
    state.evidenceReq = await api.get('/cases/' + caseId + '/evidence-requirements');
    toast(statusLabel(category) + ' attached.', 'success');
    render();
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

function bindRequiredEvidence() {
  document.querySelectorAll('[data-upload-required]').forEach((button) => {
    button.addEventListener('click', () => uploadRequiredEvidence(button.dataset.uploadRequired));
  });
}

async function downloadEvidence(attachmentId) {
  const item = (state.attachments || []).find((row) => row.id === attachmentId);
  if (!item || !item.download_url) return;
  try {
    // The server's download_url is absolute and already carries the short-lived
    // signed token; the route also requires the bearer token, which only a fetch
    // can send - a plain <a href> would answer 401.
    const blob = await api.download(item.download_url.replace(/^\/api\/v1/, ''));
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = item.original_filename || 'evidence';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

function evidencePanel() {
  const rows = state.attachments.length
    ? '<div class="table-wrap mt-2"><table><tbody>' + state.attachments.map((item) =>
      '<tr><td>' + escapeHtml(item.original_filename) +
        ' <button class="btn-sm" data-download-attachment="' + escapeHtml(item.id) + '">Download</button>' +
        '<div class="faint small">' + escapeHtml(item.caption || '') + '</div></td>' +
      '<td><span class="pill pill-info">' + escapeHtml(statusLabel(item.category)) + '</span>' +
        (item.advisory_category ? ' <span class="pill pill-accent">AI suggested</span>' : '') + '</td>' +
      '<td class="small faint nowrap">' + fmtDate(item.created_at) + '</td></tr>').join('') + '</tbody></table></div>'
    : '<div class="faint small mt-2">No evidence uploaded yet.</div>';
  return '<div class="card tight mt-3"><div class="card-title"><h3>Evidence</h3>' +
    '<span class="faint small">Recommended: ' + escapeHtml(((definitionFor(selectedTest()).evidence_requirements || {}).recommended || []).join(', ') || 'none') + '</span></div>' +
    '<div class="inline">' +
      '<input type="file" id="evidence-file" style="max-width:260px" />' +
      '<input id="evidence-caption" placeholder="Caption" style="max-width:220px" />' +
      '<select id="evidence-category" style="max-width:180px"><option value="">Auto-classify</option>' +
        ATTACHMENT_CATEGORIES.map((c) => '<option value="' + c + '">' + statusLabel(c) + '</option>').join('') + '</select>' +
      '<button class="btn-sm" id="upload-evidence">Upload</button></div>' + rows + '</div>' +
    requiredEvidencePanel();
}
/* ------------------------------------------------------ step 20 interactions */

function currentRows(test) {
  if (state.draftRows && state.draftRows[test.id]) return state.draftRows[test.id];
  const observations = test.observations || [];
  if (observations.length) return observations.map((obs) => rowObject(test, obs));
  const layout = inputSchema(test).layout || 'table';
  if (layout === 'checklist') return [rowObject(test, null)];
  return [rowObject(test, null), rowObject(test, null), rowObject(test, null)];
}

function rowContainers(test) {
  const body = document.querySelector('[data-obs-body="' + test.id + '"]');
  if (!body) return [];
  return Array.from(body.querySelectorAll('tr, .checklist-row')).filter((node) => !node.hasAttribute('data-head'));
}

function collectRows(test) {
  const containers = rowContainers(test);
  if (!containers.length) return null;
  return containers.map((container, index) => {
    const row = { observation_no: index + 1 };
    container.querySelectorAll('[data-col]').forEach((input) => {
      const key = input.dataset.col;
      if (input.type === 'checkbox') row[key] = input.checked;
      else {
        const value = input.value.trim();
        row[key] = value === '' ? null : value;
      }
    });
    return row;
  });
}

function mergeTest(updated) {
  const index = state.tests.findIndex((item) => item.id === updated.id);
  if (index >= 0) state.tests[index] = updated;
}

async function saveObservations(test, { quiet = false } = {}) {
  const rows = collectRows(test);
  if (rows === null) return null;
  state.draftRows[test.id] = rows;
  if (!canEditTests()) return null;
  setSaveState('saving', 'Saving\u2026');
  try {
    const updated = await api.put('/tests/' + test.id + '/observations', { observations: rows, replace: true });
    mergeTest(updated);
    dirtyTests.delete(test.id);
    setSaveState('saved', 'Saved ' + new Date().toLocaleTimeString());
    return updated;
  } catch (error) {
    setSaveState('error', 'Not saved \u2014 ' + formatApiError(error));
    if (!quiet) toast(formatApiError(error), 'error');
    throw error;
  }
}

function scheduleSave(test) {
  if (!savers[test.id]) savers[test.id] = debounce(() => { saveObservations(test, { quiet: true }).catch(() => {}); }, 900);
  savers[test.id]();
}

async function flushDirty() {
  const pending = Array.from(dirtyTests);
  for (const id of pending) {
    const test = state.tests.find((item) => item.id === id);
    if (test) await saveObservations(test, { quiet: true }).catch(() => {});
  }
}

function markRequired(test) {
  let missing = false;
  columns(test).forEach((col) => {
    if (!col.required || col.type === 'boolean') return;
    document.querySelectorAll('[data-obs-body="' + test.id + '"] [data-col="' + col.key + '"]').forEach((input) => {
      if (!input.value.trim()) { input.classList.add('invalid'); missing = true; }
      else input.classList.remove('invalid');
    });
  });
  return missing;
}

function highlightResult(test, result) {
  document.querySelectorAll('[data-obs-body="' + test.id + '"] [data-col]').forEach((input) => input.classList.remove('invalid'));
  const containers = rowContainers(test);
  containers.forEach((node) => node.classList.remove('row-invalid'));
  const bad = new Set();
  (result.rows || []).forEach((row) => { if (row.within === false) bad.add(Number(row.observation_no)); });
  (result.errors || []).forEach((message) => {
    const match = /row\s+(\d+)/i.exec(String(message));
    if (match) bad.add(Number(match[1]));
  });
  bad.forEach((n) => { if (containers[n - 1]) containers[n - 1].classList.add('row-invalid'); });
}

async function runValidate(test) {
  const button = document.getElementById('btn-validate');
  if (button) button.disabled = true;
  try {
    if (markRequired(test)) toast('Some required cells are empty; the engine will report exactly what is missing.', 'warn');
    await saveObservations(test, { quiet: true }).catch(() => {});
    const result = await api.post('/tests/' + test.id + '/validate', {});
    state.validation[test.id] = result;
    const panel = document.getElementById('calc-panel');
    if (panel) panel.innerHTML = calcPanelHtml(test);
    highlightResult(test, result);
    await refreshTest(test.id);
  } catch (error) {
    toast(formatApiError(error), 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function runCalculate(test) {
  const button = document.getElementById('btn-calculate');
  if (button) button.disabled = true;
  try {
    if (markRequired(test)) { toast('Complete the highlighted required cells first.', 'warn'); return; }
    await saveObservations(test, { quiet: true }).catch(() => {});
    const result = await api.post('/tests/' + test.id + '/calculate', {});
    state.validation[test.id] = result;
    toast(statusLabel(result.status) + ' \u2014 ' + (result.explanation || result.errors.join(' ')),
      result.status === 'PASS' ? 'success' : result.status === 'FAIL' ? 'error' : 'warn');
    await refreshTest(test.id);
    render();
  } catch (error) {
    toast(formatApiError(error), 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function runComplete(test) {
  const button = document.getElementById('btn-complete');
  if (button) button.disabled = true;
  try {
    await saveObservations(test, { quiet: true }).catch(() => {});
    const updated = await api.patch('/tests/' + test.id, { mark_complete: true });
    mergeTest(updated);
    toast('Test marked complete.', 'success');
    render();
  } catch (error) {
    toast(formatApiError(error), 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function runAnomalyCheck(test) {
  const button = document.getElementById('btn-anomaly');
  if (button) button.disabled = true;
  try {
    await saveObservations(test, { quiet: true }).catch(() => {});
    const report = await api.post('/tests/' + test.id + '/anomaly-check', {});
    const count = (report.findings || []).length;
    toast(report.available
      ? (count ? count + ' potential anomaly finding(s) flagged for human review.' : 'No anomalies detected across ' + report.checked_rows + ' row(s).')
      : (report.message || 'AI is unavailable.'), count ? 'warn' : 'success');
    await refreshTest(test.id);
    render();
  } catch (error) {
    toast(formatApiError(error), 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function runDisposition(test, observationNo, disposition) {
  try {
    await api.post('/tests/' + test.id + '/anomaly-disposition?observation_no=' + observationNo + '&disposition=' + disposition, {});
    toast('Disposition recorded: ' + disposition + '.', 'success');
    await refreshTest(test.id);
    render();
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function runNotApplicable(test) {
  const reason = await promptReason('Mark test not applicable', {
    label: 'Justification',
    hint: 'The reviewer and approver see this justification; it is stored in the audit trail.',
  });
  if (!reason) return;
  try {
    const updated = await api.patch('/tests/' + test.id, {
      applicability_status: 'NOT_APPLICABLE', applicability_reason: reason,
    });
    mergeTest(updated);
    toast('Test marked not applicable.', 'success');
    render();
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function runOverride(test) {
  const target = await openModal({
    title: 'Record a manual override',
    submitLabel: 'Continue',
    bodyHtml: '<div class="field"><label>The deterministic engine produced this result. ' +
      'PASS and FAIL cannot be overridden (PRD 27.2). Choose the exceptional outcome to record:</label>' +
      '<select data-value><option value="WAIVED">WAIVED \u2014 accept with a documented justification</option>' +
      '<option value="NOT_APPLICABLE">NOT_APPLICABLE \u2014 outside the scope of this evaluation</option></select></div>' +
      '<div class="hint">The automated result stays stored and recoverable in the calculation run history.</div>',
  });
  if (!target) return;
  const reason = await promptReason('Override justification', {
    label: 'Justification', minLength: 20,
    hint: 'At least 20 characters. This is a governed exception and notifies the approver.',
  });
  if (!reason) return;
  try {
    const result = await api.post('/tests/' + test.id + '/override', { status: target, reason });
    toast('Override recorded (' + result.previous_status + ' \u2192 ' + result.new_status + ').', 'success');
    await refreshTest(test.id);
    render();
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

async function uploadEvidence() {
  const input = document.getElementById('evidence-file');
  if (!input || !input.files.length) { toast('Choose a file to upload.', 'warn'); return; }
  const form = new FormData();
  form.append('file', input.files[0]);
  form.append('caption', document.getElementById('evidence-caption').value.trim());
  const category = document.getElementById('evidence-category').value;
  if (category) form.append('category', category);
  // An empty string is not a valid UUID, so only attach the test when one is selected.
  if (selectedTestId) form.append('test_instance_id', selectedTestId);
  try {
    const created = await api.upload('/cases/' + caseId + '/attachments', form);
    state.attachments = await api.get('/cases/' + caseId + '/attachments');
    state.evidenceReq = await api.get('/cases/' + caseId + '/evidence-requirements').catch(() => state.evidenceReq);
    toast('Evidence uploaded' + (created.classification && created.classification.category
      ? ' and classified as ' + statusLabel(created.classification.category) + ' (advisory).' : '.'), 'success');
    render();
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

function bindExecution() {
  document.querySelectorAll('[data-test]').forEach((button) => {
    button.addEventListener('click', async () => {
      await flushDirty();
      selectedTestId = button.dataset.test;
      render();
    });
  });
  const test = selectedTest();
  if (!test) return;

  const body = document.querySelector('[data-obs-body="' + test.id + '"]');
  if (body && canEditTests()) {
    const touch = () => { dirtyTests.add(test.id); setSaveState('saving', 'Unsaved changes\u2026'); scheduleSave(test); };
    body.addEventListener('input', touch);
    body.addEventListener('change', touch);
    body.querySelectorAll('[data-col]').forEach((input) => {
      input.addEventListener('input', () => input.classList.remove('invalid'), { once: true });
    });
  }
  const addRow = document.querySelector('[data-add-row]');
  if (addRow) addRow.addEventListener('click', () => {
    const rows = collectRows(test) || currentRows(test);
    rows.push(rowObject(test, null));
    state.draftRows[test.id] = rows;
    render();
  });
  document.querySelectorAll('[data-remove-row]').forEach((button) => {
    button.addEventListener('click', () => {
      const rows = collectRows(test) || currentRows(test);
      if (rows.length <= 1) { toast('At least one row is required.', 'warn'); return; }
      rows.splice(Number(button.closest('[data-row]').dataset.row) - 1, 1);
      state.draftRows[test.id] = rows;
      render();
    });
  });
  document.getElementById('btn-validate')?.addEventListener('click', () => runValidate(test));
  document.getElementById('btn-calculate')?.addEventListener('click', () => runCalculate(test));
  document.getElementById('btn-complete')?.addEventListener('click', () => runComplete(test));
  document.getElementById('btn-anomaly')?.addEventListener('click', () => runAnomalyCheck(test));
  document.getElementById('btn-na')?.addEventListener('click', () => runNotApplicable(test));
  document.getElementById('btn-override')?.addEventListener('click', () => runOverride(test));
  document.querySelectorAll('[data-disposition]').forEach((button) => {
    button.addEventListener('click', () => runDisposition(test, button.dataset.obs, button.dataset.disposition));
  });
  document.getElementById('upload-evidence')?.addEventListener('click', uploadEvidence);
  document.querySelectorAll('[data-download-attachment]').forEach((button) => {
    button.addEventListener('click', () => downloadEvidence(button.dataset.downloadAttachment));
  });
  bindRequiredEvidence();
}

/* ----------------------------------------------------------- step 21: summary */

function stepSummary() {
  const rows = state.tests.map((test) => {
    const result = test.compliance_result || {};
    return '<tr>' +
      '<td class="mono">' + escapeHtml(test.definition.test_code) + '</td>' +
      '<td>' + escapeHtml(test.definition.name) + '</td>' +
      '<td>' + (test.applicability_status === 'NOT_APPLICABLE'
        ? '<span class="pill pill-na">N/A</span>'
        : '<span class="pill pill-info">' + escapeHtml(test.applicability_status) + '</span>') + '</td>' +
      '<td>' + escapeHtml(statusLabel(test.status)) + '</td>' +
      '<td>' + resultPill(test.result_status) + '</td>' +
      '<td class="num">' + fmt(result.measured_value) + '</td>' +
      '<td class="num">' + fmt(result.limit_value) + '</td>' +
      '<td class="mono small">' + escapeHtml(result.rule_id || '\u2014') + '</td>' +
      '<td class="small faint">' + escapeHtml(result.clause_reference || '\u2014') + '</td></tr>';
  }).join('');
  const unresolved = state.tests.filter((t) => t.applicability_status !== 'NOT_APPLICABLE' && t.status !== 'COMPLETED');
  const failed = state.tests.filter((t) => t.result_status === 'FAIL');
  const canSubmit = can('cases.submit') && ['DRAFT', 'ASSIGNED', 'IN_PROGRESS', 'CORRECTION_REQUIRED', 'TESTING_COMPLETED'].includes(state.case.status);
  const evidenceReady = !state.evidenceReq || state.evidenceReq.satisfied;
  let banner = '';
  if (failed.length) {
    banner = '<div class="banner fail"><div><strong>' + failed.length + ' test(s) failed</strong>' +
      'A failed type evaluation cannot be submitted as a conforming report. Waive with a recorded justification or correct the instrument.</div></div>';
  } else if (unresolved.length) {
    banner = '<div class="banner warn"><div><strong>' + unresolved.length + ' test(s) unresolved</strong>' +
      unresolved.map((t) => escapeHtml(t.definition.test_code)).join(', ') + '</div></div>';
  } else if (state.tests.length) {
    banner = '<div class="banner pass"><div><strong>All applicable tests are resolved</strong>' +
      'The case is ready for technical review.</div></div>';
  }
  // Shown alongside the test banner: the two blockers are independent and the
  // engineer can clear the photographs while the tests are still running.
  if (!evidenceReady) {
    banner += '<div class="banner warn"><div><strong>Mandatory photographs outstanding</strong>' +
      'Attach ' + state.evidenceReq.missing.map((c) => escapeHtml(statusLabel(c))).join(' and ') +
      ' before submitting. They can be uploaded in the instrument or execution step.</div></div>';
  }
  return '<div class="card"><div class="step-head"><span class="step-no">21</span>' +
    '<div style="flex:1"><h2>Validation and summary</h2>' +
    '<div class="faint small">Every result below cites the rule and clause the engine evaluated it against.</div></div>' +
    (state.case.overall_result ? resultPill(state.case.overall_result) : '') + '</div>' +
    banner +
    '<button class="btn-sm" id="print-summary">Print / save as PDF</button>' +
    '<div class="table-wrap mt-3"><table><thead><tr><th>Code</th><th>Test</th><th>Applicability</th><th>Status</th>' +
      '<th>Result</th><th class="num">Measured</th><th class="num">Limit</th><th>Rule</th><th>Clause</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>' +
    (canSubmit ? '<div class="inline mt-3"><button class="btn-primary btn-sm" id="btn-submit"' +
      (evidenceReady ? '' : ' disabled title="Attach the mandatory photographs first"') + '>Submit for technical review</button>' +
      '<span class="faint small">' + (evidenceReady
        ? 'Blocked until every applicable test is complete and no test has failed.'
        : 'Attach the mandatory nameplate and test-setup photographs before submitting.') + '</span></div>' : '') +
    '</div>';
}

function bindSummary() {
  document.getElementById('print-summary')?.addEventListener('click', () => window.print());
  const button = document.getElementById('btn-submit');
  if (!button) return;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await flushDirty();
      const updated = await api.post('/cases/' + caseId + '/submit', {});
      state.case = updated;
      toast('Submitted for technical review.', 'success');
      await loadAll();
      render();
    } catch (error) {
      toast(formatApiError(error), 'error');
      button.disabled = false;
    }
  });
}

/* ------------------------------------------------------------ step 22: review */

function timelineHtml() {
  if (!state.workflow.length && !state.audit.length) return '<div class="faint small">No history yet.</div>';
  const actions = state.workflow.map((action) => '<div class="card tight" style="margin-bottom:8px">' +
    '<div class="inline" style="justify-content:space-between"><span class="mono small">' + escapeHtml(action.action) + '</span>' +
    '<span class="faint small">' + fmtDate(action.acted_at) + '</span></div>' +
    '<div class="small">' + escapeHtml(action.from_status || '\u2014') + ' \u2192 ' + escapeHtml(action.to_status || '\u2014') + '</div>' +
    (action.reason ? '<div class="small mt-2">' + escapeHtml(action.reason) + '</div>' : '') +
    '<div class="faint small">' + escapeHtml(action.actor_role || '') + '</div></div>').join('');
  const entries = state.audit.slice(0, 25).map((entry) => '<tr>' +
    '<td class="small faint nowrap">' + fmtDate(entry.occurred_at) + '</td>' +
    '<td class="mono small">' + escapeHtml(entry.event_type) + '</td>' +
    '<td class="small">' + escapeHtml(entry.actor_email || '\u2014') + '</td>' +
    '<td class="small">' + escapeHtml(entry.field_changed || entry.entity_type || '') + '</td>' +
    '<td class="small faint">' + escapeHtml(entry.reason || '') + '</td></tr>').join('');
  return '<div class="grid cols-2"><div><div class="card-title"><h3>Workflow actions</h3></div>' + (actions || '<div class="faint small">None.</div>') + '</div>' +
    '<div><div class="card-title"><h3>Audit trail</h3><span class="faint small">most recent first</span></div>' +
    '<div class="table-wrap"><table><tbody>' + entries + '</tbody></table></div></div></div>';
}

function stepReview() {
  const c = state.case;
  const canReview = can('cases.review') && ['TESTING_COMPLETED', 'UNDER_REVIEW', 'CORRECTION_REQUIRED'].includes(c.status);
  const actions = canReview
    ? '<div class="inline mt-3"><button class="btn-primary btn-sm" id="btn-verify">Verify (pass review)</button>' +
      '<button class="btn-sm" id="btn-correction">Request correction\u2026</button></div>'
    : '';
  const correction = c.last_correction_reason
    ? '<div class="banner warn"><div><strong>Corrections were requested</strong>' + escapeHtml(c.last_correction_reason) + '</div></div>' : '';
  return '<div class="card"><div class="step-head"><span class="step-no">22</span>' +
    '<div style="flex:1"><h2>Technical review and correction</h2>' +
    '<div class="faint small">Independent verification of calculations, evidence and rule citations. ' +
      'The reviewer cannot be the engineer who recorded the results.</div></div>' + caseStatusPill(c.status) + '</div>' +
    correction +
    '<div class="table-wrap"><table><tbody>' +
      '<tr><td class="small faint">Submitted</td><td>' + fmtDate(c.submitted_at) + '</td>' +
      '<td class="small faint">Verified</td><td>' + fmtDate(c.verified_at) + '</td></tr>' +
      '<tr><td class="small faint">Approved</td><td>' + fmtDate(c.approved_at) + '</td>' +
      '<td class="small faint">Finalized</td><td>' + fmtDate(c.finalized_at) + '</td></tr>' +
    '</tbody></table></div>' + actions + '<div class="mt-4">' + timelineHtml() + '</div></div>';
}

function bindReview() {
  document.getElementById('btn-verify')?.addEventListener('click', async () => {
    try {
      state.case = await api.post('/cases/' + caseId + '/verify', {});
      toast('Technically verified.', 'success');
      await loadAll();
      render();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });
  document.getElementById('btn-correction')?.addEventListener('click', async () => {
    const reason = await promptReason('Request correction', {
      label: 'Correction required', submitLabel: 'Return to engineer',
      hint: 'The engineer sees this reason; the case revision number is incremented.',
    });
    if (!reason) return;
    try {
      state.case = await api.post('/cases/' + caseId + '/request-correction', { reason });
      toast('Corrections requested.', 'success');
      await loadAll();
      render();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });
}
/* ---------------------------------------------------------- step 23: approval */

async function downloadReport(reportId, fmt) {
  try {
    const blob = await api.download('/reports/' + reportId + '/download?fmt=' + fmt);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'report-' + reportId + '.' + fmt;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

function reportPanel() {
  const reports = state.reports || [];
  const canGenerate = can('reports.generate') && !['DRAFT', 'CANCELLED'].includes(state.case.status);
  const finalReport = reports.find((item) => item.is_immutable) || reports[0];
  const rows = reports.length
    ? '<div class="table-wrap mt-2"><table><thead><tr><th>Report</th><th>Rev</th><th>Status</th><th>Generated</th><th></th></tr></thead><tbody>' +
      reports.map((report) => '<tr>' +
        '<td class="mono">' + escapeHtml(report.report_no) + '</td>' +
        '<td class="mono">' + report.revision_no + '</td>' +
        '<td>' + (report.is_immutable ? '<span class="pill pill-pass">locked</span>' : '<span class="pill pill-info">draft</span>') + '</td>' +
        '<td class="small faint">' + fmtDate(report.generated_at) + '</td>' +
        '<td class="nowrap"><button class="btn-sm" data-download="' + report.id + '" data-fmt="pdf">PDF</button> ' +
          '<button class="btn-sm" data-download="' + report.id + '" data-fmt="docx">DOCX</button></td></tr>').join('') +
      '</tbody></table></div>'
    : '<div class="faint small mt-2">No report generated yet.</div>';
  return '<div class="card tight mt-3"><div class="card-title"><h3>Generated report</h3>' +
    (finalReport && finalReport.verification_code ? '<span class="mono small">' + escapeHtml(finalReport.verification_code) + '</span>' : '') + '</div>' +
    (finalReport ? '<div class="calc-panel"><dl>' +
      '<dt>Report number</dt><dd>' + escapeHtml(finalReport.report_no) + '</dd>' +
      '<dt>Revision</dt><dd>' + finalReport.revision_no + '</dd>' +
      '<dt>Content hash</dt><dd class="truncate" title="' + escapeHtml(finalReport.content_hash || '') + '">' +
        escapeHtml((finalReport.content_hash || '').slice(0, 16)) + '\u2026</dd>' +
      '<dt>Verification code</dt><dd>' + escapeHtml(finalReport.verification_code || '\u2014') + '</dd></dl></div>' : '') +
    (canGenerate ? '<div class="inline mt-2"><button class="btn-sm" id="btn-generate">Generate PDF + DOCX</button>' +
      '<span class="faint small">A snapshot of the rule-set version, results and evidence is frozen into the report.</span></div>' : '') +
    rows + '</div>';
}

function stepApproval() {
  const c = state.case;
  const canApprove = can('cases.approve') && ['VERIFIED', 'UNDER_APPROVAL'].includes(c.status);
  const canFinalize = can('cases.finalize') && ['APPROVED', 'FINALIZED'].includes(c.status);
  const canCancel = can('cases.assign') && !['FINALIZED', 'CANCELLED'].includes(c.status);
  const actions = (canApprove || canFinalize || canCancel)
    ? '<div class="inline mt-3">' +
        (canApprove ? '<button class="btn-primary btn-sm" id="btn-approve">Approve report</button>' +
          '<button class="btn-danger btn-sm" id="btn-reject">Reject\u2026</button>' : '') +
        (canFinalize ? '<button class="btn-primary btn-sm" id="btn-finalize">Finalize and lock</button>' : '') +
        '<span class="right"></span>' +
        (canCancel ? '<button class="btn-sm btn-danger" id="btn-cancel">Cancel case\u2026</button>' : '') +
      '</div>'
    : '';
  const finalNotice = c.status === 'FINALIZED'
    ? '<div class="banner pass"><div><strong>Finalized and locked</strong>' +
      'This revision is immutable. Any change requires a new revision with its own audit trail.</div></div>' : '';
  return '<div class="card"><div class="step-head"><span class="step-no">23</span>' +
    '<div style="flex:1"><h2>Approval, finalization and report</h2>' +
    '<div class="faint small">The approving authority approves; finalization freezes the report content and hash - ' +
        'a digital signature is not implemented.</div></div>' +
    caseStatusPill(c.status) + '</div>' + finalNotice +
    '<div class="table-wrap"><table><tbody>' +
      '<tr><td class="small faint">Reviewer</td><td>' + escapeHtml((c.reviewer || {}).full_name || '\u2014') + '</td>' +
      '<td class="small faint">Approver</td><td>' + escapeHtml((c.approver || {}).full_name || '\u2014') + '</td></tr>' +
      '<tr><td class="small faint">Ruleset</td><td class="mono small">' + escapeHtml(c.standard_version_label || '\u2014') + '</td>' +
      '<td class="small faint">Report template</td><td class="mono small">' + escapeHtml(c.template_version_label || '\u2014') + '</td></tr>' +
    '</tbody></table></div>' +
    actions + reportPanel() + '</div>';
}

function bindApproval() {
  document.getElementById('btn-generate')?.addEventListener('click', async () => {
    const button = document.getElementById('btn-generate');
    button.disabled = true;
    try {
      await api.post('/cases/' + caseId + '/reports/generate', { formats: ['pdf', 'docx'], lock: false });
      state.reports = await api.get('/cases/' + caseId + '/reports');
      toast('Report generated in PDF and DOCX.', 'success');
      render();
    } catch (error) {
      toast(formatApiError(error), 'error');
      button.disabled = false;
    }
  });
  document.querySelectorAll('[data-download]').forEach((button) => {
    button.addEventListener('click', () => downloadReport(button.dataset.download, button.dataset.fmt));
  });
  document.getElementById('btn-approve')?.addEventListener('click', async () => {
    const reason = await promptReason('Approve report', {
      label: 'Approval statement', minLength: 10, submitLabel: 'Approve',
      hint: 'Recorded against your name in the audit trail.',
    });
    if (!reason) return;
    try {
      state.case = await api.post('/cases/' + caseId + '/approve', { reason });
      toast('Approved.', 'success');
      await loadAll();
      render();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });
  document.getElementById('btn-reject')?.addEventListener('click', async () => {
    const reason = await promptReason('Reject at approval', { label: 'Rejection reason', submitLabel: 'Reject', destructive: true });
    if (!reason) return;
    try {
      state.case = await api.post('/cases/' + caseId + '/reject', { reason });
      toast('Case rejected.', 'warn');
      await loadAll();
      render();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });
  document.getElementById('btn-finalize')?.addEventListener('click', async () => {
    const confirmed = await openModal({
      title: 'Finalize and lock this revision?',
      submitLabel: 'Finalize',
      bodyHtml: '<p>The case will be locked, the report content hash frozen and a verification code issued. ' +
        'This cannot be undone; further changes require a new revision.</p>',
    });
    if (!confirmed) return;
    try {
      state.case = await api.post('/cases/' + caseId + '/finalize', {});
      toast('Case finalized and locked.', 'success');
      await loadAll();
      render();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });
  document.getElementById('btn-cancel')?.addEventListener('click', async () => {
    const reason = await promptReason('Cancel this case', { label: 'Cancellation reason', submitLabel: 'Cancel case', destructive: true });
    if (!reason) return;
    try {
      state.case = await api.post('/cases/' + caseId + '/cancel', { reason });
      toast('Case cancelled.', 'warn');
      await loadAll();
      render();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });
}

/* ------------------------------------------------------------------- render */

const STEP_RENDERERS = {
  application: stepApplication,
  parties: stepParties,
  instrument: stepInstrument,
  metrology: stepMetrology,
  conditions: stepConditions,
  plan: stepPlan,
  execution: stepExecution,
  summary: stepSummary,
  review: stepReview,
  approval: stepApproval,
};

const STEP_BINDERS = {
  application: bindApplication,
  parties: bindParties,
  instrument: bindInstrument,
  metrology: bindMetrology,
  conditions: bindConditions,
  plan: bindPlan,
  execution: bindExecution,
  summary: bindSummary,
  review: bindReview,
  approval: bindApproval,
};

function render() {
  const c = state.case;
  const definition = STEP_DEFS.find((step) => step.key === state.step) || STEP_DEFS[0];
  const heading = document.querySelector('.topbar-title h1');
  if (heading) heading.textContent = c.application_no + ' \u00b7 ' + definition.title;
  content.innerHTML = '<div class="wizard">' + railHtml() +
    '<div id="step-body">' + (STEP_RENDERERS[state.step] || stepApplication)() + '</div>' +
    asideHtml() + '</div>';
  document.querySelectorAll('[data-step]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (button.dataset.step === state.step) return;
      await flushDirty();
      state.step = button.dataset.step;
      render();
    });
  });
  const footer = document.createElement('div');
  footer.className = 'wizard-footer';
  const index = STEP_DEFS.findIndex((step) => step.key === state.step);
  footer.innerHTML = '<button class="btn-sm" id="step-prev"' + (index === 0 ? ' disabled' : '') + '>\u2190 Previous</button>' +
    '<button class="btn-sm" id="step-next"' + (index === STEP_DEFS.length - 1 ? ' disabled' : '') + '>Next \u2192</button>' +
    '<span class="faint small">Step ' + definition.n + ' of 23 \u00b7 ' + escapeHtml(definition.title) + '</span>' +
    '<span class="right"></span>' + caseStatusPill(c.status);
  document.getElementById('step-body').appendChild(footer);
  document.getElementById('step-prev')?.addEventListener('click', goRelative(-1));
  document.getElementById('step-next')?.addEventListener('click', goRelative(1));
  (STEP_BINDERS[state.step] || (() => {}))();
}

function goRelative(delta) {
  return async () => {
    const index = STEP_DEFS.findIndex((step) => step.key === state.step);
    const target = STEP_DEFS[index + delta];
    if (!target) return;
    await flushDirty();
    state.step = target.key;
    render();
  };
}

document.getElementById('refresh-case')?.addEventListener('click', async () => {
  try { await refreshCase(); toast('Reloaded.', 'success'); }
  catch (error) { toast(formatApiError(error), 'error'); }
});

/* ------------------------------------------------------------------ bootstrap */

try {
  await requireSession('cases.view', 'cases.view.scope', 'cases.create', 'tests.view_results');
  if (!caseId) {
    content.innerHTML = '<div class="banner warn"><div><strong>No case selected</strong>' +
      'Open an evaluation from the register, or create a new one.</div></div>' +
      '<a class="btn btn-primary" href="/evaluations.html">Go to evaluations</a>';
  } else {
    content.innerHTML = loading('Loading evaluation\u2026');
    await loadAll();
    await loadLabUsers();
    render();
  }
} catch (error) {
  if (error.status !== 401) {
    content.innerHTML = '<div class="banner fail"><div>' + escapeHtml(formatApiError(error)) + '</div></div>';
  }
}