/* Document verification page.
 *
 * Reads the code from ?code= (which is what the QR mark carries) or from the
 * form, asks GET /api/v1/verify/{code} and renders the answer. The endpoint is
 * public: a printed report can be checked without an account, so this page
 * needs no session and never touches stored credentials.
 */

import { api, ApiError } from './api.js';
import { initTheme } from './theme.js';
import { startClocks } from './clock.js';
import { escapeHtml, fmtDate, resultPill } from './ui.js';

initTheme();
startClocks();

const form = document.getElementById('verify-form');
const input = document.getElementById('code');
const output = document.getElementById('result');

if (output) output.innerHTML = '';

function row(label, value) {
  return `<tr><th style="text-align:left;width:38%">${escapeHtml(label)}</th><td>${value}</td></tr>`;
}

function renderReport(payload) {
  const match = payload.hash_matches
    ? '<span class="pill pill-pass">Content hash matches</span>'
    : '<span class="pill pill-fail">Content hash does not match</span>';
  const banner = payload.hash_matches ? 'pass' : 'fail';
  return `
    <div class="banner ${banner}" role="note" style="margin-bottom:12px">
      <div>${escapeHtml(payload.statement || '')}</div>
    </div>
    <div class="table-wrap">
      <table>
        <tbody>
          ${row('Report number', escapeHtml(payload.report_no || '\u2014'))}
          ${row('Revision', escapeHtml(String(payload.revision_no ?? '\u2014')))}
          ${row('Application number', escapeHtml(payload.application_no || '\u2014'))}
          ${row('Laboratory', escapeHtml(payload.laboratory || '\u2014'))}
          ${row('Standard', escapeHtml(payload.standard || '\u2014'))}
          ${row('Ruleset version', escapeHtml(payload.ruleset_label || '\u2014'))}
          ${row('Report template', escapeHtml(payload.template_label || '\u2014'))}
          ${row('Overall result', resultPill(payload.overall_result))}
          ${row('Generated', escapeHtml(fmtDate(payload.generated_at)))}
          ${row('Finalized', payload.is_immutable ? escapeHtml(fmtDate(payload.locked_at)) : 'Not finalized')}
          ${row('Record status', escapeHtml(payload.report_status || '\u2014'))}
          ${row('Integrity', match)}
          ${row('Recorded hash', `<span class="mono small">${escapeHtml(payload.content_hash || '\u2014')}</span>`)}
        </tbody>
      </table>
    </div>`;
}

function renderMissing(code) {
  return `
    <div class="banner fail" role="note">
      <div><strong>No record was found for this code.</strong>
      Check that <span class="mono">${escapeHtml(code)}</span> was read correctly,
      and that the report was issued by this platform.</div>
    </div>`;
}

function renderError(message) {
  return `<div class="banner warn" role="note"><div>${escapeHtml(message)}</div></div>`;
}

async function verify(rawCode) {
  const code = String(rawCode || '').trim().toUpperCase();
  if (!code) return;
  if (output) output.innerHTML = '';
  try {
    const payload = await api.get(`/verify/${encodeURIComponent(code)}`, {
      redirectOn401: false,
      skipRefresh: true,
    });
    if (output) output.innerHTML = renderReport(payload);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      if (output) output.innerHTML = renderMissing(code);
      return;
    }
    if (error instanceof ApiError && error.status === 429) {
      if (output) output.innerHTML = renderError('Too many checks from this network. Wait a moment and try again.');
      return;
    }
    if (output) output.innerHTML = renderError('The verification service cannot be reached. Check the connection and retry.');
  }
}

if (form) {
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const code = input ? input.value : '';
    const url = new URL(window.location.href);
    if (code) url.searchParams.set('code', code.trim().toUpperCase());
    window.history.replaceState({}, '', url);
    verify(code);
  });
}

const initial = new URL(window.location.href).searchParams.get('code');
if (initial) {
  if (input) input.value = initial.trim().toUpperCase();
  verify(initial);
}