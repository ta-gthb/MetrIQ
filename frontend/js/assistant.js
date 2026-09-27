/* Retrieval-grounded OIML R 76 assistant (PRD 12, Appendix C).

   The assistant answers only from the configured, versioned rule set, test
   catalogue and report template. It never decides compliance and every answer
   carries its citations. */

import { api, requireSession, formatApiError, can } from './api.js';
import { renderShell, escapeHtml, fmtDate, loading, toast } from './ui.js';

const content = renderShell({
  active: 'assistant',
  crumb: 'Assistance',
  title: 'OIML R 76 assistant',
  actionsHtml: '<a class="btn-sm" href="/evaluations.html">Evaluations</a>',
});

const SAMPLES = [
  'What is the maximum permissible error for a class III instrument above 2000 e?',
  'How is the error of indication corrected for the additional load to changeover?',
  'Which tests apply to a non-electronic instrument?',
  'What report sections does the R 76-2 template require?',
];

const state = { history: [], features: null };

function governanceCard() {
  const info = state.features;
  if (!info) return '';
  return '<div class="card tight"><div class="card-title"><h3>Governance</h3>' +
    '<span class="pill ' + (info.enabled ? 'pill-pass' : 'pill-na') + '">' + (info.enabled ? 'enabled' : 'disabled') + '</span></div>' +
    '<div class="inline" style="justify-content:space-between"><span class="faint small">Provider</span><span class="mono">' + escapeHtml(info.provider) + '</span></div>' +
    '<div class="inline mt-2" style="justify-content:space-between"><span class="faint small">Human confirmation</span><span>' +
      (info.human_confirmation_required ? 'required' : 'not required') + '</span></div>' +
    '<div class="inline mt-2" style="justify-content:space-between"><span class="faint small">May decide compliance</span>' +
      '<span>' + (info.can_decide_compliance ? 'yes' : '<strong>no</strong>') + '</span></div>' +
    '<div class="hint mt-2">' + escapeHtml((info.governance || {}).traceability || '') + '</div>' +
    '<div class="hint">' + escapeHtml((info.governance || {}).failure_behaviour || '') + '</div></div>';
}

function historyHtml() {
  if (!state.history.length) {
    return '<div class="faint small">No questions asked yet. Ask about a tolerance, a test procedure or the report structure.</div>';
  }
  return state.history.map((entry, index) => '<div class="card tight" style="margin-bottom:10px">' +
    '<div class="inline" style="justify-content:space-between"><strong>' + escapeHtml(entry.question) + '</strong>' +
      '<span class="faint small">' + fmtDate(entry.at) + '</span></div>' +
    (entry.error
      ? '<div class="banner fail mt-2" style="margin-bottom:0"><div>' + escapeHtml(entry.error) + '</div></div>'
      : '<p class="small mt-2" style="white-space:pre-wrap">' + escapeHtml(entry.answer || '') + '</p>' +
        (entry.citations && entry.citations.length
          ? '<div class="table-wrap"><table><thead><tr><th>Source</th><th>Clause</th><th class="num">Score</th></tr></thead><tbody>' +
            entry.citations.map((citation) => '<tr><td>' + escapeHtml(citation.title || citation.code) +
              '<div class="faint small mono">' + escapeHtml(citation.code || '') + '</div></td>' +
              '<td class="small">' + escapeHtml(citation.clause_reference || '\u2014') + '</td>' +
              '<td class="num">' + (citation.score === undefined ? '\u2014' : citation.score) + '</td></tr>').join('') +
            '</tbody></table></div>' : '') +
        '<div class="hint mt-2">' + escapeHtml(entry.message || '') +
          (entry.grounded === false ? ' <span class="pill pill-warn">ungrounded</span>' : '') + '</div>') +
    '</div>').join('');
}

function render() {
  content.innerHTML = '<div class="grid cols-3">' +
    '<div style="grid-column:span 2"><div class="card">' +
      '<div class="field"><label for="question">Ask about the configured OIML R 76 rule set</label>' +
      '<textarea id="question" rows="3" placeholder="e.g. Which MPE band applies at 2500 e for a class III instrument?"></textarea></div>' +
      '<div class="inline"><button class="btn-primary btn-sm" id="ask">Ask</button>' +
        '<span class="faint small">Answers cite the rule or template section they came from.</span></div>' +
      '<div class="inline mt-2" style="gap:6px">' + SAMPLES.map((sample, index) =>
        '<button class="btn-sm" data-sample="' + index + '">' + escapeHtml(sample.length > 46 ? sample.slice(0, 46) + '\u2026' : sample) + '</button>').join('') +
      '</div>' +
      '<div class="mt-4">' + historyHtml() + '</div>' +
    '</div></div>' +
    '<div>' + governanceCard() + '</div>' +
    '</div>';
  const input = document.getElementById('question');
  document.getElementById('ask')?.addEventListener('click', () => ask(input.value));
  input?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); ask(input.value); }
  });
  content.querySelectorAll('[data-sample]').forEach((button) => {
    button.addEventListener('click', () => { input.value = SAMPLES[Number(button.dataset.sample)]; input.focus(); });
  });
}

async function ask(question) {
  const text = (question || '').trim();
  if (text.length < 3) { toast('Enter a question of at least three characters.', 'warn'); return; }
  const entry = { question: text, at: new Date().toISOString() };
  state.history.unshift(entry);
  render();
  try {
    const result = await api.post('/ai/knowledge', { question: text });
    entry.answer = result.answer;
    entry.citations = result.citations;
    entry.message = result.message;
    entry.grounded = result.grounded;
  } catch (error) {
    entry.error = formatApiError(error);
  }
  render();
}

try {
  await requireSession('ai.view');
  try { state.features = await api.get('/ai/features'); } catch (error) { state.features = null; }
  render();
} catch (error) {
  if (error.status !== 401) {
    content.innerHTML = '<div class="banner fail"><div>' + escapeHtml(formatApiError(error)) + '</div></div>';
  }
}