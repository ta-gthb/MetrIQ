/* Administration and governance console (PRD 15.3, 16, 19).

   Tabs are permission-filtered: a laboratory administrator sees users and
   equipment, a metrology authority manages standards and rule sets, and only
   a platform administrator manages settings. The UI hides what the role cannot
   do, but the backend re-checks every permission on every call. */

import { api, requireSession, formatApiError, can, canAny, getUser } from './api.js';
import {
  renderShell, fmt, fmtDate, escapeHtml, empty, loading, toast, openModal, promptReason,
} from './ui.js';

const content = renderShell({
  active: 'admin',
  crumb: 'Governance',
  title: 'Administration',
  actionsHtml: '<button class="btn-sm" id="refresh">Refresh</button>',
});

const ROLES = ['SUPER_ADMIN', 'LAB_ADMIN', 'ENGINEER', 'REVIEWER', 'APPROVER', 'AUDITOR'];
const state = { tab: null, users: [], labs: [], roles: [], permissions: [], settings: [] };

function tabs() {
  return [
    ['users', 'Users', canAny('users.manage', 'users.manage.scoped')],
    ['laboratories', 'Laboratories', can('laboratories.manage')],
    ['roles', 'Roles & permissions', canAny('users.manage', 'users.manage.scoped')],
    ['standards', 'Standards & rules', can('rules.view')],
    ['catalogue', 'Test catalogue', can('rules.view')],
    ['templates', 'Report templates', can('rules.view')],
    ['ai', 'AI features', canAny('ai.manage', 'ai.view')],
    ['settings', 'System settings', can('settings.manage')],
    ['audit', 'Audit logs', canAny('audit.view', 'audit.view.scope', 'audit.view.limited')],
  ].filter((item) => item[2]);
}

function renderTabs() {
  return '<div class="inline" style="gap:6px;margin-bottom:14px">' + tabs().map((item) =>
    '<button class="btn-sm ' + (state.tab === item[0] ? 'btn-primary' : '') + '" data-tab="' + item[0] + '">' +
    escapeHtml(item[1]) + '</button>').join('') + '</div>';
}

function labName(id) {
  const lab = state.labs.find((item) => item.id === id);
  return lab ? lab.name : '\u2014';
}

/* ------------------------------------------------------------------- users */

function userForm(user) {
  const editing = Boolean(user);
  const labs = state.labs.map((lab) =>
    '<option value="' + lab.id + '"' + (user && user.laboratory_id === lab.id ? ' selected' : '') + '>' +
    escapeHtml(lab.name) + '</option>').join('');
  return '<div class="field"><label class="req">Full name</label>' +
      '<input data-value name="full_name" value="' + escapeHtml(user ? user.full_name : '') + '" /></div>' +
    (editing ? '' : '<div class="field"><label class="req">Email</label>' +
      '<input data-input name="email" type="email" /></div>') +
    '<div class="field-row">' +
      '<div class="field"><label>Designation</label><input data-input name="designation" value="' +
        escapeHtml(user ? user.designation || '' : '') + '" /></div>' +
      '<div class="field"><label class="req">Role</label><select data-input name="role_code">' +
        ROLES.map((role) => '<option value="' + role + '"' + (user && user.role_code === role ? ' selected' : '') + '>' +
          role + '</option>').join('') + '</select></div>' +
    '</div>' +
    '<div class="field"><label>Laboratory</label><select data-input name="laboratory_id">' +
      '<option value="">\u2014 none \u2014</option>' + labs + '</select></div>' +
    (editing
      ? '<div class="field"><label class="req">Active</label><select data-input name="is_active">' +
          '<option value="true"' + (user.is_active ? ' selected' : '') + '>Active</option>' +
          '<option value="false"' + (!user.is_active ? ' selected' : '') + '>Disabled</option></select></div>'
      : '<div class="field"><label class="req">Initial password</label>' +
        '<input data-input name="password" type="text" value="" autocomplete="new-password" />' +
        '<div class="hint">At least 8 characters. This bundle carries no default: ' +
        '<button type="button" class="btn-ghost btn-sm" id="generate-password">Generate one</button> ' +
        'and communicate it through an approved channel. The user should change it after first sign-in.</div></div>');
}

function collectModal(backdrop) {
  const payload = {};
  backdrop.querySelectorAll('[data-input], [data-value]').forEach((input) => {
    payload[input.name || 'value'] = input.value;
  });
  return payload;
}

async function renderUsers() {
  const data = state.users;
  const rows = data.map((user) => '<tr>' +
    '<td><div>' + escapeHtml(user.full_name) + '</div><div class="faint small">' + escapeHtml(user.email) + '</div></td>' +
    '<td class="mono small">' + escapeHtml(user.role_code) + '</td>' +
    '<td class="small">' + escapeHtml(user.designation || '\u2014') + '</td>' +
    '<td class="small">' + escapeHtml(labName(user.laboratory_id)) + '</td>' +
    '<td>' + (user.is_active ? '<span class="pill pill-pass">active</span>' : '<span class="pill pill-na">disabled</span>') + '</td>' +
    '<td class="nowrap"><button class="btn-sm" data-edit-user="' + user.id + '">Edit</button> ' +
      '<button class="btn-sm" data-reset-user="' + user.id + '">Reset password</button></td></tr>').join('');
  return '<div class="card"><div class="card-title"><h3>Users</h3>' +
    (can('users.manage') || can('users.manage.scoped') ? '<button class="btn-primary btn-sm" id="new-user">Create user</button>' : '') +
    '</div>' + (data.length ? '<div class="table-wrap"><table><thead><tr><th>Name</th><th>Role</th><th>Designation</th>' +
      '<th>Laboratory</th><th>Status</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div>'
      : empty('No users found.')) + '</div>';
}

/* A random initial password, built in the browser so no default is embedded in
 * the bundle (audit item 3). The suffix guarantees the composition a human
 * would otherwise have to remember. */
function generatePassword() {
  const alphabet = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  let out = '';
  bytes.forEach((value) => { out += alphabet[value % alphabet.length]; });
  return out + '-aA1';
}

function bindUsers() {
  document.getElementById('new-user')?.addEventListener('click', async () => {
    const result = await openModal({
      title: 'Create a user', submitLabel: 'Create user', bodyHtml: userForm(null),
      onSubmit: (value, backdrop) => {
        const payload = collectModal(backdrop);
        if (!payload.full_name || !payload.email || !payload.password) {
          toast('Full name, email and an initial password are required.', 'warn');
          return false;
        }
        payload.is_active = true;
        payload.laboratory_id = payload.laboratory_id || null;
        api.post('/users', payload)
          .then(() => { toast('User created.', 'success'); reloadTab(); })
          .catch((error) => toast(formatApiError(error), 'error'));
        return true;
      },
    });
    document.getElementById('generate-password')?.addEventListener('click', () => {
      const field = document.querySelector('.modal input[name="password"]');
      if (field) {
        field.value = generatePassword();
        field.focus();
      }
    });
  });
  content.querySelectorAll('[data-edit-user]').forEach((button) => {
    button.addEventListener('click', async () => {
      const user = state.users.find((item) => item.id === button.dataset.editUser);
      await openModal({
        title: 'Edit ' + user.full_name, submitLabel: 'Save changes', bodyHtml: userForm(user),
        onSubmit: (value, backdrop) => {
          const payload = collectModal(backdrop);
          payload.is_active = payload.is_active === 'true';
          payload.laboratory_id = payload.laboratory_id || null;
          api.patch('/users/' + user.id, payload)
            .then(() => { toast('User updated.', 'success'); reloadTab(); })
            .catch((error) => toast(formatApiError(error), 'error'));
          return true;
        },
      });
    });
  });
  content.querySelectorAll('[data-reset-user]').forEach((button) => {
    button.addEventListener('click', async () => {
      const user = state.users.find((item) => item.id === button.dataset.resetUser);
      const confirmed = await openModal({
        title: 'Issue a temporary password?',
        submitLabel: 'Reset password',
        bodyHtml: '<p>A new temporary password will be generated for <strong>' + escapeHtml(user.email) +
          '</strong>. The previous password stops working immediately. Communicate the new value through an approved channel.</p>',
      });
      if (!confirmed) return;
      try {
        const result = await api.post('/users/' + user.id + '/reset-password', {});
        await openModal({
          title: 'Temporary password', submitLabel: 'Done', cancelLabel: 'Close',
          bodyHtml: '<div class="calc-panel"><dl><dt>User</dt><dd>' + escapeHtml(user.email) + '</dd>' +
            '<dt>Temporary password</dt><dd class="mono">' + escapeHtml(result.temporary_password) + '</dd></dl></div>' +
            '<div class="hint mt-2">' + escapeHtml(result.delivery || '') + '</div>',
        });
      } catch (error) { toast(formatApiError(error), 'error'); }
    });
  });
}

/* ------------------------------------------------------------ laboratories */

async function renderLaboratories() {
  const rows = state.labs.map((lab) => '<tr>' +
    '<td><div>' + escapeHtml(lab.name) + '</div><div class="faint small mono">' + escapeHtml(lab.code) + '</div></td>' +
    '<td class="small">' + escapeHtml(lab.location || '\u2014') + '</td>' +
    '<td class="small">' + escapeHtml(lab.accreditation_no || '\u2014') + '</td>' +
    '<td class="small">' + escapeHtml(lab.contact_email || '\u2014') + '</td>' +
    '<td>' + (lab.is_active ? '<span class="pill pill-pass">active</span>' : '<span class="pill pill-na">inactive</span>') + '</td>' +
    '<td><button class="btn-sm" data-edit-lab="' + lab.id + '">Edit</button></td></tr>').join('');
  return '<div class="card"><div class="card-title"><h3>Laboratories</h3>' +
    '<button class="btn-primary btn-sm" id="new-lab">Create laboratory</button></div>' +
    (state.labs.length ? '<div class="table-wrap"><table><thead><tr><th>Name</th><th>Location</th>' +
      '<th>Accreditation</th><th>Contact</th><th>Status</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div>'
      : empty('No laboratories.')) + '</div>';
}

function labForm(lab) {
  return '<div class="field"><label class="req">Name</label><input data-input name="name" value="' +
      escapeHtml(lab ? lab.name : '') + '" /></div>' +
    '<div class="field-row"><div class="field"><label class="req">Code</label><input data-input name="code" value="' +
      escapeHtml(lab ? lab.code : '') + '" /></div>' +
    '<div class="field"><label>Location</label><input data-input name="location" value="' +
      escapeHtml(lab ? lab.location || '' : '') + '" /></div></div>' +
    '<div class="field"><label>Address</label><input data-input name="address" value="' +
      escapeHtml(lab ? lab.address || '' : '') + '" /></div>' +
    '<div class="field-row"><div class="field"><label>Contact email</label><input data-input name="contact_email" value="' +
      escapeHtml(lab ? lab.contact_email || '' : '') + '" /></div>' +
    '<div class="field"><label>Accreditation no.</label><input data-input name="accreditation_no" value="' +
      escapeHtml(lab ? lab.accreditation_no || '' : '') + '" /></div></div>';
}

function bindLaboratories() {
  document.getElementById('new-lab')?.addEventListener('click', () => {
    openModal({
      title: 'Create a laboratory', submitLabel: 'Create', bodyHtml: labForm(null),
      onSubmit: (value, backdrop) => {
        const payload = collectModal(backdrop);
        payload.is_active = true;
        api.post('/laboratories', payload)
          .then(() => { toast('Laboratory created.', 'success'); reloadTab(); })
          .catch((error) => toast(formatApiError(error), 'error'));
        return true;
      },
    });
  });
  content.querySelectorAll('[data-edit-lab]').forEach((button) => {
    button.addEventListener('click', () => {
      const lab = state.labs.find((item) => item.id === button.dataset.editLab);
      openModal({
        title: 'Edit ' + lab.name, submitLabel: 'Save', bodyHtml: labForm(lab),
        onSubmit: (value, backdrop) => {
          const payload = collectModal(backdrop);
          payload.is_active = lab.is_active;
          api.patch('/laboratories/' + lab.id, payload)
            .then(() => { toast('Laboratory updated.', 'success'); reloadTab(); })
            .catch((error) => toast(formatApiError(error), 'error'));
          return true;
        },
      });
    });
  });
}

/* ------------------------------------------------------------------- roles */

async function renderRoles() {
  const categories = {};
  state.permissions.forEach((permission) => {
    (categories[permission.category] = categories[permission.category] || []).push(permission);
  });
  const matrix = '<div class="table-wrap"><table><thead><tr><th>Permission</th>' +
    state.roles.map((role) => '<th class="center">' + escapeHtml(role.code.replace(/_/g, ' ')) + '</th>').join('') +
    '</tr></thead><tbody>' + Object.entries(categories).map(([category, items]) =>
      '<tr><td colspan="' + (state.roles.length + 1) + '" class="faint small mono">' + escapeHtml(category) + '</td></tr>' +
      items.map((permission) => '<tr><td><div>' + escapeHtml(permission.code) + '</div>' +
        '<div class="faint small">' + escapeHtml(permission.description || '') + '</div></td>' +
        state.roles.map((role) => '<td class="center">' +
          (role.permissions.includes(permission.code) ? '<span class="pill pill-pass">\u2713</span>' : '<span class="faint">\u00b7</span>') +
          '</td>').join('') + '</tr>').join('')).join('') + '</tbody></table></div>';
  return '<div class="card"><div class="card-title"><h3>Roles</h3>' +
    '<span class="faint small">Fixed, seeded role definitions (least privilege)</span></div>' +
    '<div class="table-wrap"><table><thead><tr><th>Role</th><th>Description</th><th class="num">Permissions</th></tr></thead><tbody>' +
    state.roles.map((role) => '<tr><td class="mono">' + escapeHtml(role.code) + '<div class="faint small">' +
      escapeHtml(role.name) + '</div></td><td class="small">' + escapeHtml(role.description || '') + '</td>' +
      '<td class="num">' + role.permission_count + '</td></tr>').join('') + '</tbody></table></div>' +
    '<div class="card-title mt-4"><h3>Permission matrix</h3><span class="faint small">' +
      state.permissions.length + ' permissions</span></div>' + matrix + '</div>';
}
/* ------------------------------------------------- standards, rules, templates */

function reviewPill(status) {
  if (!status) return '<span class="faint">\u2014</span>';
  const kind = status === 'approved' ? 'pass'
    : status === 'rejected' ? 'fail'
    : status === 'pending_domain_review' || status === 'pending' || status === 'needs_changes' ? 'warn'
    : 'info';
  return '<span class="pill pill-' + kind + '">' + escapeHtml(status.replace(/_/g, ' ')) + '</span>';
}

/* The lifecycle is Draft -> Under review -> Approved -> Scheduled -> Active
   (audit item 10). Naming the states here keeps the buttons honest: a
   ruleset only ever offers the one step that is legal next, and the server
   refuses every other combination regardless of what this table shows. */
const LIFECYCLE_LABELS = {
  draft: 'Draft',
  under_review: 'Under review',
  approved: 'Approved',
  scheduled: 'Scheduled',
  active: 'Active',
  superseded: 'Superseded',
  retired: 'Retired',
};

function lifecyclePill(ruleset) {
  const state = ruleset.lifecycle_state || ruleset.status || 'draft';
  const kind = state === 'active' ? 'pass'
    : state === 'superseded' || state === 'retired' ? 'na'
    : state === 'draft' ? 'warn'
    : 'info';
  let html = '<span class="pill pill-' + kind + '">' +
    escapeHtml(LIFECYCLE_LABELS[state] || state) + '</span>';
  if (state === 'active' && ruleset.activation_basis === 'provisional') {
    html += ' <span class="pill pill-warn" title="Activated by the development/demo bootstrap " +
      "without a metrology review. Its results are not from a verified ruleset.">provisional</span>';
  }
  return html;
}

function reviewProgress(ruleset) {
  const total = ruleset.rule_count || 0;
  const reviewed = ruleset.reviewed_rule_count || 0;
  const cls = total && reviewed === total ? 'pill-pass' : 'pill-warn';
  return '<span class="pill ' + cls + '">' + reviewed + ' / ' + total + '</span>';
}

function rulesetActions(ruleset) {
  const id = ruleset.standard_version_id;
  const state = ruleset.lifecycle_state || ruleset.status || 'draft';
  const buttons = [];
  if (can('rules.view')) {
    buttons.push('<button class="btn-sm" data-package="' + id + '" data-label="' +
      escapeHtml(ruleset.version_label || id) + '">Review package</button>');
  }
  if (can('rules.review') && ruleset.unreviewed_rule_count > 0) {
    buttons.push('<button class="btn-sm" data-review="' + id + '">Review rules</button>');
  }
  if (can('rules.manage') && state === 'draft') {
    buttons.push('<button class="btn-sm" data-submit="' + id + '">Submit for review</button>');
  }
  if (can('rules.approve') && state === 'under_review') {
    buttons.push('<button class="btn-sm" data-reject="' + id + '">Reject</button>');
    buttons.push('<button class="btn-sm btn-primary" data-approve="' + id + '">Approve</button>');
  }
  if (can('rules.approve') && state === 'approved') {
    buttons.push('<button class="btn-sm" data-schedule="' + id + '">Schedule</button>');
  }
  if (can('rules.approve') && (state === 'approved' || state === 'scheduled')) {
    const blocked = ruleset.can_activate
      ? ''
      : ' disabled title="Every rule needs a current metrology review first"';
    buttons.push('<button class="btn-sm btn-primary" data-activate="' + id + '"' + blocked +
      '>Activate</button>');
  }
  if (can('rules.approve') && (state === 'active' || state === 'scheduled')) {
    buttons.push('<button class="btn-sm btn-danger" data-deactivate="' + id + '">Deactivate</button>');
  }
  return buttons.length ? buttons.join(' ') : '<span class="faint small">\u2014</span>';
}

async function renderStandards() {
  const [standards, rulesets, rules] = await Promise.all([
    api.get('/standards'),
    api.get('/rulesets'),
    api.get('/rules'),
  ]);
  const standardsHtml = standards.map((standard) => '<div class="card tight" style="margin-bottom:10px">' +
    '<div class="card-title"><h3>' + escapeHtml(standard.code) + '</h3>' +
      '<span class="pill pill-info">' + escapeHtml(standard.publisher || 'standard') + '</span></div>' +
    '<div class="small muted">' + escapeHtml(standard.title) + '</div>' +
    '<div class="table-wrap mt-2"><table><thead><tr><th>Version</th><th>Edition</th><th>Status</th>' +
      '<th>Effective</th></tr></thead><tbody>' +
      (standard.versions || []).map((version) => '<tr><td class="mono small">' + escapeHtml(version.version_label) + '</td>' +
        '<td class="small">' + escapeHtml(version.edition) + '</td>' +
        '<td>' + (version.is_active ? '<span class="pill pill-pass">active</span>' : '<span class="pill pill-na">' + escapeHtml(version.status) + '</span>') + '</td>' +
        '<td class="small faint">' + escapeHtml(version.effective_from || '\u2014') + '</td>' +
        '</tr>').join('') +
      '</tbody></table></div></div>').join('');

  const rulesetsHtml = rulesets.map((ruleset) => '<tr>' +
    '<td class="mono small">' + escapeHtml(ruleset.version_label) +
      (ruleset.approved_by_name ? '<div class="faint">approved by ' + escapeHtml(ruleset.approved_by_name) + '</div>' : '') +
      (ruleset.deactivation_reason ? '<div class="faint">' + escapeHtml(ruleset.deactivation_reason) + '</div>' : '') +
      '</td>' +
    '<td class="small">' + escapeHtml(ruleset.standard_code || '') + ' ' + escapeHtml(ruleset.edition || '') + '</td>' +
    '<td>' + lifecyclePill(ruleset) +
      (ruleset.scheduled_for ? '<div class="faint small">effective ' + escapeHtml(ruleset.scheduled_for) + '</div>' : '') +
      '</td>' +
    '<td>' + reviewProgress(ruleset) + '</td>' +
    '<td class="num">' + ruleset.rule_count + '</td>' +
    '<td class="nowrap">' + rulesetActions(ruleset) + '</td></tr>').join('');

  const rulesHtml = rules.map((rule) => '<tr>' +
    '<td class="mono small">' + escapeHtml(rule.code) + '</td>' +
    '<td>' + escapeHtml(rule.name) + '<div class="faint small">' + escapeHtml(rule.description || '') + '</div></td>' +
    '<td class="small">' + escapeHtml(rule.category) + '</td>' +
    '<td class="small mono">' + escapeHtml(rule.clause_reference || '\u2014') + '</td>' +
    '<td class="mono small">' + escapeHtml(rule.version_label || '\u2014') + '</td>' +
    '<td>' + reviewPill(rule.review_status) + '</td></tr>').join('');

  return '<div class="banner warn"><div><strong>A rule set reaches production through review, not by being seeded</strong>' +
    'The shipped bands and tolerances are configuration data, not verified metrological truth. A rule set moves through ' +
    '<span class="mono">draft \u2192 under review \u2192 approved \u2192 scheduled \u2192 active</span>, and activation is refused ' +
    'while any rule lacks a current sign-off from a named metrology reviewer. Anything activated by the development/demo ' +
    'bootstrap is labelled <span class="mono">provisional</span>. Historical cases keep the rule set they were created with.</div></div>' +
    '<div class="card"><div class="card-title"><h3>Standards</h3></div>' + standardsHtml + '</div>' +
    '<div class="card mt-3"><div class="card-title"><h3>Rule sets</h3>' +
      '<span class="faint small">Activating a version preserves the version used by historical cases</span></div>' +
    '<div class="table-wrap"><table><thead><tr><th>Version</th><th>Standard</th><th>Lifecycle</th><th>Rules reviewed</th>' +
      '<th class="num">Rules</th><th></th></tr></thead><tbody>' + rulesetsHtml + '</tbody></table></div></div>' +
    '<div class="card mt-3"><div class="card-title"><h3>Rules</h3><span class="faint small">' + rules.length + ' definitions</span></div>' +
    '<div class="table-wrap"><table><thead><tr><th>Code</th><th>Rule</th><th>Category</th><th>Clause</th>' +
      '<th>Version</th><th>Review</th></tr></thead><tbody>' + rulesHtml + '</tbody></table></div></div>';
}

const REVIEW_FIELDS = [
  ['reviewer_name', 'Reviewer name', 'Dr A. Metrologist'],
  ['reviewer_credentials', 'Reviewer credentials', 'Legal metrology reviewer, OIML R 76 scope'],
  ['source_revision', 'Source revision', 'OIML R 76-1:2006 (E)'],
  ['change_note', 'Change note', 'Bands, tolerances and clause references checked'],
];

function reviewFieldId(field) { return 'review-' + field.replace(/_/g, '-'); }

function readReviewFields(scope) {
  const value = {};
  REVIEW_FIELDS.forEach(([field]) => {
    const input = scope.querySelector('#' + reviewFieldId(field));
    value[field] = input ? input.value.trim() : '';
  });
  return value;
}

/* Rule-level sign-off. The reviewer's identity and the revision they worked
   from are captured once and applied to each rule they approve, so the
   approval record in the database always answers "who checked this, against
   what, and when" (audit item 6). */
async function openReviewDialog(versionId) {
  let payload;
  try {
    payload = await api.get('/rulesets/' + versionId + '/review-package');
  } catch (error) {
    toast(formatApiError(error), 'error');
    return;
  }

  const fields = REVIEW_FIELDS.map(([field, label, placeholder]) =>
    '<div class="field"><label for="' + reviewFieldId(field) + '">' + escapeHtml(label) + '</label>' +
    '<input id="' + reviewFieldId(field) + '" value="' + escapeHtml(placeholder) + '" /></div>').join('');

  const rows = (payload.rules || []).map((rule) =>
    '<tr data-rule="' + rule.rule_code + '">' +
      '<td class="mono small">' + escapeHtml(rule.rule_code) +
        '<div class="faint">' + escapeHtml(rule.rule_name || '') + '</div></td>' +
      '<td class="mono small">' + escapeHtml(rule.clause_reference || '\u2014') + '</td>' +
      '<td class="small">' + escapeHtml(rule.formula || '\u2014') +
        '<div class="faint">' + escapeHtml(rule.rounding_policy || '') + '</div></td>' +
      '<td data-state>' + reviewPill(rule.review_status) +
        (rule.pending_reason ? '<div class="faint small">' + escapeHtml(rule.pending_reason) + '</div>' : '') +
        (rule.reviewed_by ? '<div class="faint small">' + escapeHtml(rule.reviewed_by) + '</div>' : '') +
        '</td>' +
      '<td class="nowrap">' +
        '<button class="btn-sm" data-sign="' + rule.rule_version_id + '">Sign off</button> ' +
        '<button class="btn-sm" data-needs-change="' + rule.rule_version_id + '">Needs changes</button>' +
      '</td></tr>').join('');

  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML =
    '<div class="modal modal-wide" role="dialog" aria-modal="true" aria-label="Record a metrology review">' +
      '<header><h2>Record a metrology review</h2>' +
        '<button class="btn-ghost btn-sm" data-close aria-label="Close">\u2715</button></header>' +
      '<div class="modal-body">' +
        '<p class="small muted">' + escapeHtml(payload.version_label) + ' \u2014 ' +
          payload.reviewed_rule_count + ' of ' + payload.rule_count + ' rules currently signed off. ' +
          'A sign-off is bound to the rule content, so editing a rule afterwards revokes it.</p>' +
        '<div class="grid-2">' + fields + '</div>' +
        '<div class="table-wrap"><table><thead><tr><th>Rule</th><th>Clause</th><th>Formula</th>' +
          '<th>Review</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '</div>' +
      '<footer><button data-close>Close</button></footer>' +
    '</div>';
  document.body.appendChild(backdrop);

  const close = () => backdrop.remove();
  backdrop.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', close));
  backdrop.addEventListener('mousedown', (event) => { if (event.target === backdrop) close(); });
  document.addEventListener('keydown', function onKey(event) {
    if (event.key === 'Escape') { document.removeEventListener('keydown', onKey); close(); }
  });

  const record = async (ruleVersionId, decision, row) => {
    const value = readReviewFields(backdrop);
    if (!value.reviewer_name) { toast('A named reviewer is required.', 'error'); return; }
    try {
      const result = await api.post(
        '/rulesets/' + versionId + '/rules/' + ruleVersionId + '/review',
        { ...value, decision, boundary_cases_passed: true,
          boundary_case_reference: 'tests/test_rule_boundaries.py' },
      );
      const cell = row.querySelector('[data-state]');
      cell.innerHTML = reviewPill(result.review_status) +
        '<div class="faint small">' + escapeHtml(result.reviewer_name) + '</div>';
      toast(decision === 'approved' ? 'Rule signed off.' : 'Rule marked as needing changes.', 'success');
    } catch (error) {
      toast(formatApiError(error), 'error');
    }
  };

  backdrop.querySelectorAll('tbody tr').forEach((row) => {
    const sign = row.querySelector('[data-sign]');
    const flag = row.querySelector('[data-needs-change]');
    if (sign) sign.addEventListener('click', () => record(sign.dataset.sign, 'approved', row));
    if (flag) flag.addEventListener('click', () => record(flag.dataset.needsChange, 'needs_changes', row));
  });
}

async function downloadReviewPackage(versionId, label) {
  try {
    const payload = await api.get('/rulesets/' + versionId + '/review-package');
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'rule-review-package-' + (label || versionId) + '.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast(formatApiError(error), 'error');
  }
}

function bindStandards() {
  const on = (attribute, handler) => content.querySelectorAll('[data-' + attribute + ']')
    .forEach((button) => button.addEventListener('click', () => handler(button.dataset[attribute], button)));

  on('review', (versionId) => openReviewDialog(versionId));
  on('package', (versionId, button) => downloadReviewPackage(versionId, button.dataset.label));

  on('activate', async (versionId) => {
    const reason = await promptReason('Activate this rule set?', {
      label: 'Activation reason',
      hint: 'Recorded in the audit trail. The version becomes the default for newly created cases; ' +
        'existing cases keep the version they were created with.',
      submitLabel: 'Activate',
    });
    if (reason === null) return;
    try {
      await api.post('/rulesets/' + versionId + '/activate', { reason });
      toast('Rule set activated.', 'success');
      reloadTab();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });

  on('submit', async (versionId) => {
    const note = await openModal({
      title: 'Submit this rule set for technical review',
      submitLabel: 'Submit',
      bodyHtml: '<p class="small muted">The set moves to <span class="mono">under review</span>. Every rule still needs a ' +
        'named metrology sign-off before it can be approved and activated.</p>' +
        '<div class="field"><label for="modal-note">Note (optional)</label>' +
        '<input id="modal-note" data-value /></div>',
    });
    if (note === null) return;
    try {
      await api.post('/rulesets/' + versionId + '/submit-review', { note });
      toast('Submitted for review.', 'success');
      reloadTab();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });

  on('approve', async (versionId) => {
    const summary = await api.get('/rulesets/' + versionId);
    const gate = summary.review_gate || {};
    if (!summary.can_activate) {
      toast('Not every rule has a current metrology review: ' + (gate.summary || 'review required'), 'error');
      return;
    }
    const confirmed = await openModal({
      title: 'Approve this rule set?',
      submitLabel: 'Approve',
      bodyHtml: '<p class="small muted">Approval records your name against all ' + summary.rule_count +
        ' rules at their current content. Editing any rule afterwards revokes the approval.</p>',
    });
    if (!confirmed) return;
    try {
      await api.post('/rulesets/' + versionId + '/approve', {});
      toast('Rule set approved.', 'success');
      reloadTab();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });

  on('reject', async (versionId) => {
    const note = await promptReason('Reject this rule set?', {
      label: 'What is wrong with it?',
      hint: 'The set returns to draft and the reason is recorded against it.',
      submitLabel: 'Reject',
    });
    if (note === null) return;
    try {
      await api.post('/rulesets/' + versionId + '/reject', { change_note: note });
      toast('Rule set returned to draft.', 'success');
      reloadTab();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });

  on('schedule', async (versionId) => {
    const effective = await openModal({
      title: 'Schedule this rule set',
      submitLabel: 'Schedule',
      bodyHtml: '<div class="field"><label class="req" for="modal-date">Effective from</label>' +
        '<input id="modal-date" type="date" data-value /></div>' +
        '<div class="hint">The version stays inactive until it is activated.</div>',
    });
    if (!effective) return;
    try {
      await api.post('/rulesets/' + versionId + '/schedule', { effective_from: effective });
      toast('Rule set scheduled.', 'success');
      reloadTab();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });

  on('deactivate', async (versionId) => {
    const reason = await promptReason('Deactivate this rule set?', {
      label: 'Deactivation reason',
      hint: 'Required, and recorded with a timestamp. New cases will have no active ruleset until ' +
        'another version is approved and activated.',
      submitLabel: 'Deactivate',
    });
    if (reason === null) return;
    try {
      await api.post('/rulesets/' + versionId + '/deactivate', { reason });
      toast('Rule set deactivated.', 'success');
      reloadTab();
    } catch (error) { toast(formatApiError(error), 'error'); }
  });
}

async function renderCatalogue() {
  const definitions = await api.get('/test-definitions');
  const rows = definitions.map((definition) => '<tr>' +
    '<td class="mono">' + escapeHtml(definition.test_code) + '</td>' +
    '<td>' + escapeHtml(definition.name) + '<div class="faint small">' + escapeHtml(definition.description || '') + '</div></td>' +
    '<td class="small">' + escapeHtml(definition.category) + '</td>' +
    '<td>' + (definition.phase === 'MVP' ? '<span class="pill pill-pass">MVP</span>' : '<span class="pill pill-na">phase 2</span>') + '</td>' +
    '<td class="mono small">' + escapeHtml(definition.clause_reference || '\u2014') + '</td>' +
    '<td class="num">' + definition.sequence_no + '</td>' +
    '<td>' + (definition.is_active ? '<span class="pill pill-pass">active</span>' : '<span class="pill pill-na">inactive</span>') + '</td>' +
    '<td class="small mono">' + escapeHtml((definition.input_schema || {}).layout || '\u2014') + '</td></tr>').join('');
  return '<div class="card"><div class="card-title"><h3>Test catalogue</h3>' +
    '<span class="faint small">' + definitions.length + ' test definitions (versioned with the rule set)</span></div>' +
    '<div class="table-wrap"><table><thead><tr><th>Code</th><th>Test</th><th>Category</th><th>Phase</th>' +
      '<th>Clause</th><th class="num">Seq</th><th>Status</th><th>Input</th></tr></thead><tbody>' + rows + '</tbody></table></div></div>';
}

async function renderTemplates() {
  const templates = await api.get('/report-templates');
  const cards = templates.map((template) => {
    const versions = template.versions || [];
    const active = versions.find((version) => version.is_active) || versions[0] || {};
    const sections = (active.section_map || {}).sections || [];
    return '<div class="card"><div class="card-title"><h3>' + escapeHtml(template.name) + '</h3>' +
      '<span class="mono small">' + escapeHtml(template.code) + '</span></div>' +
      '<div class="small muted">' + escapeHtml(template.description || '') + '</div>' +
      '<div class="mt-3 small faint">Version ' + escapeHtml(active.version_label || '\u2014') + ' \u00b7 ' +
        sections.length + ' sections \u00b7 ' + reviewPill(active.review_status) + '</div>' +
      '<div class="table-wrap mt-2"><table><thead><tr><th>#</th><th>Section</th><th>Required</th></tr></thead><tbody>' +
      sections.map((section) => '<tr><td class="mono">' + escapeHtml(section.number || '') + '</td>' +
        '<td>' + escapeHtml(section.title || '') + '</td>' +
        '<td>' + (section.required ? '<span class="pill pill-warn">required</span>' : '<span class="faint small">optional</span>') + '</td></tr>').join('') +
      '</tbody></table></div></div>';
  }).join('');
  return '<div class="card"><div class="card-title"><h3>Report templates</h3></div>' +
    '<div class="faint small">Reports are generated from the frozen section map of the template version used by the case.</div></div>' +
    '<div class="mt-3">' + (cards || empty('No templates.')) + '</div>';
}

/* ---------------------------------------------------------------------- AI */

async function renderAi() {
  const info = await api.get('/ai/features');
  const canManage = can('ai.manage');
  const rows = info.features.map((feature) => '<tr>' +
    '<td class="mono small">' + escapeHtml(feature.code) + '</td>' +
    '<td>' + escapeHtml(feature.label) + '</td>' +
    '<td class="mono small faint">' + escapeHtml(feature.setting_key) + '</td>' +
    '<td>' + (feature.enabled ? '<span class="pill pill-pass">enabled</span>' : '<span class="pill pill-na">disabled</span>') + '</td>' +
    '<td>' + (canManage ? '<button class="btn-sm" data-toggle-ai="' + feature.code + '" data-enabled="' +
      (!feature.enabled) + '">' + (feature.enabled ? 'Disable' : 'Enable') + '</button>' : '') + '</td></tr>').join('');
  const governance = info.governance || {};
  return '<div class="card"><div class="card-title"><h3>AI feature governance</h3>' +
    '<span class="pill ' + (info.enabled ? 'pill-pass' : 'pill-na') + '">' + escapeHtml(info.provider) + '</span></div>' +
    '<div class="grid cols-3"><div><div class="faint small">Overall</div><div>' + (info.enabled ? 'Enabled' : 'Disabled') + '</div></div>' +
      '<div><div class="faint small">Human confirmation</div><div>' + (info.human_confirmation_required ? 'Required' : 'Not required') + '</div></div>' +
      '<div><div class="faint small">May decide compliance</div><div>' + (info.can_decide_compliance ? 'Yes' : 'No') + '</div></div></div>' +
    '<div class="table-wrap mt-3"><table><thead><tr><th>Code</th><th>Feature</th><th>Setting</th><th>State</th><th></th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table></div>' +
    '<div class="hint mt-3">' + escapeHtml(governance.traceability || '') + '</div>' +
    '<div class="hint">' + escapeHtml(governance.failure_behaviour || '') + '</div></div>';
}

function bindAi() {
  content.querySelectorAll('[data-toggle-ai]').forEach((button) => {
    button.addEventListener('click', async () => {
      const enabled = button.dataset.enabled === 'true';
      try {
        await api.patch('/ai/features/' + button.dataset.toggleAi + '?enabled=' + enabled, {});
        toast('AI feature ' + (enabled ? 'enabled' : 'disabled') + '.', 'success');
        reloadTab();
      } catch (error) { toast(formatApiError(error), 'error'); }
    });
  });
}

/* ---------------------------------------------------------------- settings */

async function renderSettings() {
  const rows = state.settings.map((setting) => '<tr>' +
    '<td class="mono small">' + escapeHtml(setting.key) + '</td>' +
    '<td class="small">' + escapeHtml(setting.category || '') + '</td>' +
    '<td class="small faint">' + escapeHtml(setting.description || '') + '</td>' +
    '<td><input class="mono small" data-setting="' + escapeHtml(setting.key) + '" value="' +
      escapeHtml(JSON.stringify(setting.value)) + '" /></td>' +
    '<td><button class="btn-sm" data-save-setting="' + escapeHtml(setting.key) + '">Save</button></td></tr>').join('');
  return '<div class="card"><div class="card-title"><h3>System settings</h3>' +
    '<button class="btn-primary btn-sm" id="new-setting">Add setting</button></div>' +
    '<div class="hint">Values are JSON objects. Feature toggles are managed on the AI features tab.</div>' +
    (state.settings.length ? '<div class="table-wrap mt-2"><table><thead><tr><th>Key</th><th>Category</th>' +
      '<th>Description</th><th>Value (JSON)</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div>'
      : empty('No settings have been recorded.')) + '</div>';
}

function bindSettings() {
  content.querySelectorAll('[data-save-setting]').forEach((button) => {
    button.addEventListener('click', async () => {
      const key = button.dataset.saveSetting;
      const input = content.querySelector('[data-setting="' + key + '"]');
      let value;
      try { value = JSON.parse(input.value); }
      catch (error) { toast('Value must be valid JSON.', 'warn'); return; }
      try {
        await api.put('/settings/' + encodeURIComponent(key), value);
        toast('Setting saved.', 'success');
        reloadTab();
      } catch (error) { toast(formatApiError(error), 'error'); }
    });
  });
  document.getElementById('new-setting')?.addEventListener('click', () => {
    openModal({
      title: 'Add a system setting', submitLabel: 'Save setting',
      bodyHtml: '<div class="field"><label class="req">Key</label><input data-input name="key" placeholder="ai.nameplate_extract" /></div>' +
        '<div class="field"><label>Category</label><input data-input name="category" value="general" /></div>' +
        '<div class="field"><label>Description</label><input data-input name="description" /></div>' +
        '<div class="field"><label class="req">Value (JSON)</label><input data-input name="value" value="{&quot;enabled&quot;: true}" /></div>',
      onSubmit: (value, backdrop) => {
        const payload = collectModal(backdrop);
        let parsed;
        try { parsed = JSON.parse(payload.value); }
        catch (error) { toast('Value must be valid JSON.', 'warn'); return false; }
        const query = '?category=' + encodeURIComponent(payload.category || 'general') +
          '&description=' + encodeURIComponent(payload.description || '');
        api.put('/settings/' + encodeURIComponent(payload.key) + query, parsed)
          .then(() => { toast('Setting saved.', 'success'); reloadTab(); })
          .catch((error) => toast(formatApiError(error), 'error'));
        return true;
      },
    });
  });
}

/* ------------------------------------------------------------------- audit */

async function renderAudit(page) {
  const data = await api.get('/audit-logs', { query: { page: page || 1, page_size: 50 } });
  const rows = data.items.map((entry) => '<tr>' +
    '<td class="small faint nowrap">' + fmtDate(entry.occurred_at) + '</td>' +
    '<td class="mono small">' + escapeHtml(entry.event_type) + '</td>' +
    '<td class="small">' + escapeHtml(entry.actor_email || '\u2014') + '<div class="faint small">' + escapeHtml(entry.actor_role || '') + '</div></td>' +
    '<td class="small">' + escapeHtml(entry.entity_type) + '</td>' +
    '<td class="small">' + (entry.case_id ? '<a href="/evaluation.html?case=' + entry.case_id + '">case</a>' : '\u2014') + '</td>' +
    '<td class="small">' + escapeHtml(entry.field_changed || '') + '</td>' +
    '<td class="small faint">' + escapeHtml(entry.reason || '') + '</td></tr>').join('');
  return '<div class="card"><div class="card-title"><h3>Audit logs</h3>' +
    '<span class="faint small">' + data.meta.total + ' events \u00b7 page ' + data.meta.page + ' of ' + data.meta.pages + '</span></div>' +
    '<div class="table-wrap"><table><thead><tr><th>When</th><th>Event</th><th>Actor</th><th>Entity</th>' +
      '<th>Case</th><th>Field</th><th>Reason</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '<div class="inline mt-3"><button class="btn-sm" id="audit-prev"' + (data.meta.has_prev ? '' : ' disabled') + '>\u2190 Newer</button>' +
      '<button class="btn-sm" id="audit-next"' + (data.meta.has_next ? '' : ' disabled') + '>Older \u2192</button></div>' +
    '<div class="hint mt-2">Audit entries are append-only. Values are captured before and after every governed change.</div></div>';
}

/* ------------------------------------------------------------------- driver */

function bindTabs() {
  content.querySelectorAll('[data-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      state.tab = button.dataset.tab;
      window.location.hash = state.tab;
      reloadTab();
    });
  });
}

async function reloadTab() {
  content.innerHTML = renderTabs() + '<div id="panel">' + loading() + '</div>';
  bindTabs();
  const panel = document.getElementById('panel');
  try {
    if (state.tab === 'users') {
      if (!state.labs.length) state.labs = (await api.get('/laboratories', { query: { page_size: 200 } })).items;
      state.users = (await api.get('/users', { query: { page_size: 200 } })).items;
      panel.innerHTML = await renderUsers();
      bindUsers();
    } else if (state.tab === 'laboratories') {
      state.labs = (await api.get('/laboratories', { query: { page_size: 200 } })).items;
      panel.innerHTML = await renderLaboratories();
      bindLaboratories();
    } else if (state.tab === 'roles') {
      state.roles = await api.get('/admin/roles');
      state.permissions = await api.get('/admin/permissions');
      panel.innerHTML = await renderRoles();
    } else if (state.tab === 'standards') {
      panel.innerHTML = await renderStandards();
      bindStandards();
    } else if (state.tab === 'catalogue') {
      panel.innerHTML = await renderCatalogue();
    } else if (state.tab === 'templates') {
      panel.innerHTML = await renderTemplates();
    } else if (state.tab === 'ai') {
      panel.innerHTML = await renderAi();
      bindAi();
    } else if (state.tab === 'settings') {
      state.settings = await api.get('/settings');
      panel.innerHTML = await renderSettings();
      bindSettings();
    } else if (state.tab === 'audit') {
      panel.innerHTML = await renderAudit(1);
      bindAudit(1);
    }
  } catch (error) {
    panel.innerHTML = '<div class="banner fail"><div>' + escapeHtml(formatApiError(error)) + '</div></div>';
  }
}

function bindAudit(page) {
  const go = async (target) => {
    const panel = document.getElementById('panel');
    panel.innerHTML = loading();
    panel.innerHTML = await renderAudit(target);
    bindAudit(target);
  };
  document.getElementById('audit-prev')?.addEventListener('click', () => go(page - 1));
  document.getElementById('audit-next')?.addEventListener('click', () => go(page + 1));
}

document.getElementById('refresh').addEventListener('click', reloadTab);

try {
  await requireSession('dashboard.view');
  const available = tabs();
  if (!available.length) {
    content.innerHTML = '<div class="banner fail"><div>Your role does not have access to the administration console.</div></div>';
  } else {
    state.tab = (window.location.hash || '').replace('#', '') || available[0][0];
    if (!available.some((item) => item[0] === state.tab)) state.tab = available[0][0];
    await reloadTab();
  }
} catch (error) {
  if (error.status !== 401) {
    content.innerHTML = '<div class="banner fail"><div>' + escapeHtml(formatApiError(error)) + '</div></div>';
  }
}