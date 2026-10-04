/* Evaluation register and case creation (PRD 15.1, 15.2 steps 14-18). */

import { api, requireSession, formatApiError, can } from './api.js';
import {
  renderShell, caseStatusPill, resultPill, fmt, fmtDate, escapeHtml,
  empty, loading, toast, openModal,
} from './ui.js';

const MASS_UNITS = ['g', 'kg', 'mg', 't', 'lb', 'oz'];
const CLASSES = ['I', 'II', 'III', 'IIII'];

const content = renderShell({
  active: 'evaluations',
  crumb: 'Workflow',
  title: 'Evaluations',
  actionsHtml: can('cases.create') ? '<a class="btn btn-primary btn-sm" href="#create">New evaluation</a>' : '',
});

let manufacturers = [];
let applicants = [];
let tab = (window.location.hash || '').replace('#', '') || 'all';

/* ------------------------------------------------------------------ tabs */

function tabs() {
  const items = [
    ['all', 'All evaluations'],
    ['mine', 'My evaluations'],
  ];
  if (can('cases.create')) items.push(['create', 'Create']);
  items.push(['instruments', 'Instruments']);
  return `<div class="inline" style="gap:6px;margin-bottom:14px">
    ${items.map(([key, label]) =>
      `<button class="btn-sm ${tab === key ? 'btn-primary' : ''}" data-tab="${key}">${escapeHtml(label)}</button>`).join('')}
  </div>`;
}

function bindTabs() {
  document.querySelectorAll('[data-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      tab = button.dataset.tab;
      window.location.hash = tab;
      render();
    });
  });
}

/* ---------------------------------------------------------------- register */

async function renderRegister(mine) {
  const status = document.getElementById('status-filter')?.value || '';
  const search = document.getElementById('search')?.value || '';
  let data;
  try {
    data = await api.get('/cases', { query: { mine: mine ? 'true' : '', status, search, page_size: 50 } });
  } catch (error) {
    return `<div class="banner fail"><div>${escapeHtml(formatApiError(error))}</div></div>`;
  }

  const rows = data.items.map((item) => `<tr>
    <td><a href="/evaluation.html?case=${item.id}" class="mono">${escapeHtml(item.application_no)}</a></td>
    <td><div>${escapeHtml(item.title || '\u2014')}</div>
      <div class="faint small">${escapeHtml(item.instrument_model || '')}</div></td>
    <td>${escapeHtml(item.applicant_name || '\u2014')}</td>
    <td>${caseStatusPill(item.status)}</td>
    <td>${item.overall_result ? resultPill(item.overall_result) : '<span class="faint">\u2014</span>'}</td>
    <td class="small faint nowrap">${fmtDate(item.updated_at)}</td>
    <td><a class="btn-sm" href="/evaluation.html?case=${item.id}">Open</a></td>
  </tr>`).join('');

  return `
    <div class="card">
      <div class="inline" style="margin-bottom:12px">
        <div style="min-width:240px"><label for="search">Search</label>
          <input id="search" placeholder="Application number or title" value="${escapeHtml(search)}" /></div>
        <div style="min-width:190px"><label for="status-filter">Status</label>
          <select id="status-filter">
            <option value="">All statuses</option>
            ${['DRAFT', 'ASSIGNED', 'IN_PROGRESS', 'TESTING_COMPLETED', 'UNDER_REVIEW',
               'CORRECTION_REQUIRED', 'VERIFIED', 'APPROVED', 'FINALIZED', 'REJECTED', 'CANCELLED']
              .map((value) => `<option value="${value}" ${status === value ? 'selected' : ''}>${value.replace(/_/g, ' ')}</option>`).join('')}
          </select></div>
        <div class="right"><button id="apply-filter" class="btn-sm">Apply</button></div>
      </div>
      ${data.items.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Application</th><th>Title</th><th>Applicant</th><th>Status</th>
          <th>Result</th><th>Updated</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div>
        <div class="small faint mt-3">${data.meta.total} case(s)</div>`
      : empty(mine ? 'No evaluations are assigned to you.' : 'No evaluations yet.',
              can('cases.create') ? 'Use Create to open a new one.' : '')}
    </div>`;
}

/* ------------------------------------------------------------- instruments */

async function renderInstruments() {
  let data;
  try {
    data = await api.get('/instruments', { query: { page_size: 50 } });
  } catch (error) {
    return `<div class="banner fail"><div>${escapeHtml(formatApiError(error))}</div></div>`;
  }
  if (!data.items.length) return `<div class="card">${empty('No instruments registered yet.')}</div>`;
  return `<div class="card">
    <div class="card-title"><h3>Instrument register</h3>
      <span class="faint small">${data.meta.total} record(s)</span></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Model</th><th>Serial</th><th>Class</th><th class="num">Max</th>
        <th class="num">Min</th><th class="num">e</th><th class="num">d</th><th>Manufacturer</th></tr></thead>
      <tbody>${data.items.map((item) => `<tr>
        <td>${escapeHtml(item.model)}<div class="faint small">${escapeHtml(item.type_designation || '')}</div></td>
        <td class="mono small">${escapeHtml(item.serial_number || '\u2014')}</td>
        <td>${escapeHtml(item.instrument_class)}</td>
        <td class="num">${fmt(item.max_capacity)}</td>
        <td class="num">${fmt(item.min_capacity)}</td>
        <td class="num">${fmt(item.verification_scale_interval)}</td>
        <td class="num">${fmt(item.actual_scale_interval)}</td>
        <td>${escapeHtml(item.manufacturer ? item.manufacturer.name : '\u2014')}</td>
      </tr>`).join('')}</tbody></table></div>
  </div>`;
}

/* ----------------------------------------------------------------- create */

function createForm() {
  return `
    <form id="create-form" novalidate>
      <div class="grid cols-2">
        <div class="card">
          <h3>Application</h3>
          <div class="field"><label class="req" for="title">Title</label>
            <input id="title" required placeholder="e.g. SIW-30K type evaluation" /></div>
          <div class="field"><label for="purpose">Purpose</label>
            <textarea id="purpose" rows="2" placeholder="Type evaluation for national approval"></textarea></div>
          <div class="field-row">
            <div class="field"><label for="priority">Priority</label>
              <select id="priority"><option value="normal">Normal</option>
                <option value="high">High</option><option value="low">Low</option></select></div>
            <div class="field"><label for="scope">Scope notes</label>
              <input id="scope" placeholder="Optional" /></div>
          </div>
          <div class="field"><label for="applicant">Applicant</label>
            <select id="applicant"><option value="">- none -</option>
              ${applicants.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('')}
            </select>
            <div class="hint">Not listed? Type a new applicant name below.</div></div>
          <div class="field"><label for="new-applicant">Create applicant</label>
            <input id="new-applicant" placeholder="Organisation name" /></div>
        </div>

        <div class="card">
          <h3>Instrument</h3>
          <div class="field-row">
            <div class="field"><label class="req" for="model">Model</label><input id="model" required /></div>
            <div class="field"><label for="type-designation">Type designation</label><input id="type-designation" /></div>
          </div>
          <div class="field-row">
            <div class="field"><label for="serial">Serial number</label><input id="serial" /></div>
            <div class="field"><label class="req" for="instrument-class">Accuracy class</label>
              <select id="instrument-class" required>${CLASSES.map((value) => `<option value="${value}">${value}</option>`).join('')}</select></div>
          </div>
          <div class="field"><label for="manufacturer">Manufacturer</label>
            <select id="manufacturer"><option value="">- none -</option>
              ${manufacturers.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('')}
            </select>
            <div class="hint">
              ${can('masters.manage')
                ? '<a href="#" id="new-manufacturer-link">New Manufacturer? Click here to register</a>'
                : 'Manufacturers are maintained by the laboratory administration.'}
            </div></div>
          <fieldset>
            <legend>Metrological characteristics</legend>
            <div class="field-row">
              <div class="field"><label class="req" for="max-capacity">Maximum capacity (Max)</label>
                <div class="input-unit"><input id="max-capacity" class="numeric" required /><span class="unit" id="unit-max">g</span></div></div>
              <div class="field"><label for="min-capacity">Minimum capacity (Min)</label>
                <div class="input-unit"><input id="min-capacity" class="numeric" /><span class="unit" id="unit-min">g</span></div></div>
            </div>
            <div class="field-row">
              <div class="field"><label class="req" for="e">Verification scale interval (e)</label>
                <div class="input-unit"><input id="e" class="numeric" required /><span class="unit" id="unit-e">g</span></div></div>
              <div class="field"><label for="d">Actual scale interval (d)</label>
                <div class="input-unit"><input id="d" class="numeric" /><span class="unit" id="unit-d">g</span></div></div>
              <div class="field"><label for="unit">Unit</label>
                <select id="unit">${MASS_UNITS.map((value) => `<option value="${value}">${value}</option>`).join('')}</select></div>
            </div>
            <div class="inline">
              <label class="inline"><input type="checkbox" id="is-electronic" checked /> Electronic</label>
              <label class="inline"><input type="checkbox" id="has-zero" checked /> Zero device</label>
              <label class="inline"><input type="checkbox" id="has-level" checked /> Level indicator</label>
              <label class="inline"><input type="checkbox" id="has-tare" /> Tare device</label>
            </div>
          </fieldset>
          <div class="field">
            <label class="inline"><input type="checkbox" id="multi-range" /> Multi-range instrument</label>
            <div id="ranges" class="mt-2"></div>
          </div>
        </div>
      </div>
      <div class="card mt-3">
        <h3>Mandatory photographs</h3>
        <div class="hint">Two clear photographs are required before this evaluation can be
          submitted for technical review: the instrument nameplate and the test setup. Attach them
          here, or later in the Instrument or Execution step of the evaluation.</div>
        <div class="grid cols-2">
          <div class="field"><label for="create-nameplate">Instrument nameplate</label>
            <input type="file" id="create-nameplate" accept="image/*" /></div>
          <div class="field"><label for="create-test-setup">Test setup</label>
            <input type="file" id="create-test-setup" accept="image/*" /></div>
        </div>
      </div>
      <div class="inline mt-3">
        <button class="btn-primary" type="submit">Create evaluation and generate test plan</button>
        <span class="save-state faint">The applicable R 76 test plan is generated from the
          standard version rules the moment the case is created.</span>
      </div>
    </form>`;
}

function rangeEditor() {
  return `<div class="checklist-row" data-range>
    <div class="input-unit"><input class="numeric" data-r-min placeholder="Min" /><span class="unit">g</span></div>
    <div class="input-unit"><input class="numeric" data-r-max placeholder="Max" /><span class="unit">g</span></div>
    <div class="input-unit"><input class="numeric" data-r-e placeholder="e" /><span class="unit">g</span></div>
    <button type="button" class="btn-sm btn-ghost" data-remove-range>Remove</button>
  </div>`;
}

/* Registers a manufacturer from the case-creation page and selects it in the
   list, without discarding anything already typed into the form. */
function registerManufacturerInline() {
  openModal({
    title: 'Register a new manufacturer',
    submitLabel: 'Register manufacturer',
    bodyHtml: '<div class="hint">The manufacturer is added to the register and selected for this evaluation.</div>' +
      '<div class="field"><label class="req">Name</label><input data-value name="name" /></div>' +
      '<div class="field-row">' +
        '<div class="field"><label>Code</label><input data-input name="code" placeholder="MFR-0001" /></div>' +
        '<div class="field"><label>Contact person</label><input data-input name="contact_person" /></div>' +
      '</div>' +
      '<div class="field-row">' +
        '<div class="field"><label>Email</label><input data-input type="email" name="email" /></div>' +
        '<div class="field"><label>Phone</label><input data-input name="phone" /></div>' +
      '</div>' +
      '<div class="field"><label>Address</label><input data-input name="address" /></div>' +
      '<div class="field-row">' +
        '<div class="field"><label>City</label><input data-input name="city" /></div>' +
        '<div class="field"><label>Country</label><input data-input name="country" value="India" /></div>' +
      '</div>',
    onSubmit: async (value, backdrop) => {
      const read = (name) => ((backdrop.querySelector('[name="' + name + '"]') || {}).value || '').trim();
      const payload = { name: read('name'), is_active: true };
      if (!payload.name) {
        toast('A manufacturer name is required.', 'warn');
        return false;
      }
      ['code', 'contact_person', 'email', 'phone', 'address', 'city', 'country'].forEach((key) => {
        const entry = read(key);
        if (entry) payload[key] = entry;
      });
      try {
        const created = await api.post('/manufacturers', payload);
        manufacturers.push(created);
        manufacturers.sort((left, right) => String(left.name).localeCompare(String(right.name)));
        const select = document.getElementById('manufacturer');
        if (select) {
          select.innerHTML = '<option value="">- none -</option>' + manufacturers.map((item) =>
            '<option value="' + item.id + '">' + escapeHtml(item.name) + '</option>').join('');
          select.value = created.id;
        }
        toast('Manufacturer registered and selected.', 'success');
      } catch (error) {
        toast(formatApiError(error), 'error');
        return false;
      }
      return true;
    },
  });
}

function bindCreate() {
  document.getElementById('new-manufacturer-link')?.addEventListener('click', (event) => {
    event.preventDefault();
    registerManufacturerInline();
  });
  const multi = document.getElementById('multi-range');
  const host = document.getElementById('ranges');
  const sync = () => {
    host.innerHTML = multi.checked ? `${rangeEditor()}${rangeEditor()}
      <button type="button" class="btn-sm" id="add-range">Add range</button>` : '';
    if (!multi.checked) return;
    host.querySelector('#add-range').addEventListener('click', () => {
      host.insertAdjacentHTML('beforeend', rangeEditor());
    });
    host.addEventListener('click', (event) => {
      if (event.target.matches('[data-remove-range]')) event.target.closest('[data-range]').remove();
    });
  };
  multi.addEventListener('change', sync);
  sync();

  const unit = document.getElementById('unit');
  unit.addEventListener('change', () => {
    ['unit-max', 'unit-min', 'unit-e', 'unit-d'].forEach((id) => { document.getElementById(id).textContent = unit.value; });
  });

  document.getElementById('create-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const required = ['title', 'model', 'max-capacity', 'e'];
    let invalid = false;
    required.forEach((id) => {
      const field = document.getElementById(id);
      const bad = !field.value.trim();
      field.classList.toggle('invalid', bad);
      if (bad) { invalid = true; field.setAttribute('aria-invalid', 'true'); }
      else field.removeAttribute('aria-invalid');
    });
    if (invalid) { toast('Complete the highlighted required fields.', 'warn'); return; }

    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true; submit.textContent = 'Creating\u2026';
    try {
      const manufacturerId = document.getElementById('manufacturer').value || null;
      const applicantName = document.getElementById('new-applicant').value.trim();
      let applicantId = document.getElementById('applicant').value || null;
      if (!applicantId && applicantName) {
        applicantId = (await api.post('/applicants', { name: applicantName })).id;
      }

      const ranges = [...document.querySelectorAll('[data-range]')].map((row, index) => ({
        range_no: index + 1,
        min_capacity: row.querySelector('[data-r-min]').value,
        max_capacity: row.querySelector('[data-r-max]').value,
        verification_scale_interval: row.querySelector('[data-r-e]').value,
        unit: unit.value,
      })).filter((range) => range.min_capacity && range.max_capacity && range.verification_scale_interval);

      const payload = {
        title: document.getElementById('title').value.trim(),
        purpose: document.getElementById('purpose').value.trim() || null,
        scope_notes: document.getElementById('scope').value.trim() || null,
        priority: document.getElementById('priority').value,
        manufacturer_id: manufacturerId,
        applicant_id: applicantId,
        instrument: {
          model: document.getElementById('model').value.trim(),
          type_designation: document.getElementById('type-designation').value.trim() || null,
          serial_number: document.getElementById('serial').value.trim() || null,
          instrument_class: document.getElementById('instrument-class').value,
          max_capacity: document.getElementById('max-capacity').value,
          min_capacity: document.getElementById('min-capacity').value || null,
          verification_scale_interval: document.getElementById('e').value,
          actual_scale_interval: document.getElementById('d').value || null,
          unit: unit.value,
          is_electronic: document.getElementById('is-electronic').checked,
          has_zero_device: document.getElementById('has-zero').checked,
          has_level_indicator: document.getElementById('has-level').checked,
          has_tare_device: document.getElementById('has-tare').checked,
          is_multi_range: document.getElementById('multi-range').checked,
          ranges,
        },
      };
      const created = await api.post('/cases', payload);

      const photographs = [
        ['nameplate_photograph', 'Instrument nameplate', document.getElementById('create-nameplate').files[0]],
        ['test_setup_photograph', 'Test setup', document.getElementById('create-test-setup').files[0]],
      ].filter(([, , file]) => file);
      let evidenceWarning = '';
      if (photographs.length) submit.textContent = 'Attaching photographs\u2026';
      for (const [category, caption, file] of photographs) {
        const attachment = new FormData();
        attachment.append('file', file);
        attachment.append('category', category);
        attachment.append('caption', caption);
        attachment.append('auto_classify', 'false');
        try {
          await api.upload('/cases/' + created.id + '/attachments', attachment);
        } catch (error) {
          evidenceWarning = formatApiError(error);
        }
      }

      const outstanding = photographs.length < 2 || Boolean(evidenceWarning);
      toast(
        outstanding
          ? 'Evaluation created. Two clear photographs are still required before submission.'
          : 'Evaluation created, test plan generated and photographs attached.',
        outstanding ? 'warn' : 'success',
      );
      // Land on the step that carries the mandatory-photograph panel whenever the
      // evidence is still outstanding, so the next action is in front of the user.
      window.location.href = outstanding
        ? `/evaluation.html?case=${created.id}&step=instrument`
        : `/evaluation.html?case=${created.id}`;
    } catch (error) {
      toast(formatApiError(error), 'error');
      submit.disabled = false; submit.textContent = 'Create evaluation and generate test plan';
    }
  });
}

/* ------------------------------------------------------------------ render */

async function render() {
  content.innerHTML = `${tabs()}<div id="panel">${loading()}</div>`;
  bindTabs();
  const panel = document.getElementById('panel');

  if (tab === 'create') {
    if (!can('cases.create')) { panel.innerHTML = '<div class="banner fail"><div>Not permitted.</div></div>'; return; }
    panel.innerHTML = createForm();
    bindCreate();
    return;
  }
  if (tab === 'instruments') {
    panel.innerHTML = await renderInstruments();
    return;
  }

  panel.innerHTML = await renderRegister(tab === 'mine');
  const search = document.getElementById('search');
  const statusFilter = document.getElementById('status-filter');
  document.getElementById('apply-filter')?.addEventListener('click', async () => {
    panel.innerHTML = await renderRegister(tab === 'mine');
  });
  search?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); panel.innerHTML = ''; render(); }
  });
  statusFilter?.addEventListener('change', async () => { panel.innerHTML = await renderRegister(tab === 'mine'); });
}

try {
  await requireSession('cases.view', 'cases.view.scope', 'cases.create');
  [manufacturers, applicants] = await Promise.all([
    api.get('/manufacturers', { query: { page_size: 100 } }).then((data) => data.items).catch(() => []),
    api.get('/applicants', { query: { page_size: 100 } }).then((data) => data.items).catch(() => []),
  ]);
  await render();
} catch (error) {
  if (error.status !== 401) {
    content.innerHTML = `<div class="banner fail"><div>${escapeHtml(formatApiError(error))}</div></div>`;
  }
}
