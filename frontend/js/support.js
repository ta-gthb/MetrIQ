/* Support channel: every role can contact the Super Admin, and the Super Admin
   answers the threads from the same page (PRD 18.3). */

import { api, requireSession, formatApiError, getUser } from './api.js';
import {
  renderShell, fmtDate, escapeHtml, loading, toast,
} from './ui.js';

const isSuperAdmin = (getUser() || {}).role_code === 'SUPER_ADMIN';

const content = renderShell({
  active: 'support',
  crumb: 'Governance',
  title: isSuperAdmin ? 'Support inbox' : 'Contact Super Admin',
  actionsHtml: '<button class="btn-sm" id="refresh-support">Refresh</button>',
});

const state = { threads: [], threadId: null, messages: [] };

function messageRows(messages) {
  if (!messages.length) return '<div class="faint small">No messages yet.</div>';
  return messages.map((message) => '<div class="msg">' +
    '<div class="inline" style="justify-content:space-between">' +
      '<span class="small"><strong>' + escapeHtml(message.sender_name || 'User') + '</strong> ' +
        '<span class="faint">' + escapeHtml(message.sender_role || '') + '</span></span>' +
      '<span class="faint" style="font-size:0.68rem">' + fmtDate(message.created_at) + '</span></div>' +
    '<div class="small">' + escapeHtml(message.body) + '</div></div>').join('');
}

function threadList() {
  if (!state.threads.length) return '<div class="faint small">No user has contacted the Super Admin yet.</div>';
  return '<div class="thread-list">' + state.threads.map((thread) => '<button class="btn-sm thread-item ' +
    (state.threadId === thread.user_id ? 'btn-primary' : '') + '" data-thread="' + thread.user_id + '">' +
    '<strong>' + escapeHtml(thread.user_name) + '</strong>' +
    '<div class="faint small">' + escapeHtml(thread.role_code) +
      (thread.laboratory_name ? ' \u00b7 ' + escapeHtml(thread.laboratory_name) : '') + '</div>' +
    (thread.last_message ? '<div class="faint small truncate">' + escapeHtml(thread.last_message) + '</div>' : '') +
    '</button>').join('') + '</div>';
}

function composer() {
  const disabled = isSuperAdmin && !state.threadId;
  return '<div class="field mt-3"><label for="support-body">' +
    (isSuperAdmin ? 'Reply to this thread' : 'Message the Super Admin') + '</label>' +
    '<textarea id="support-body" rows="3" placeholder="' +
    (isSuperAdmin ? 'Write your reply\u2026' : 'Describe what you need help with\u2026') + '"' +
    (disabled ? ' disabled' : '') + '></textarea></div>' +
    '<button class="btn-sm btn-primary" id="support-send"' + (disabled ? ' disabled' : '') + '>Send</button>';
}

function render() {
  if (isSuperAdmin) {
    content.innerHTML = '<div class="grid cols-2">' +
      '<div class="card"><div class="card-title"><h3>Users</h3><span class="pill pill-info">' +
        state.threads.length + '</span></div>' +
        '<div class="hint">Every user role may open a thread with the platform administrator.</div>' +
        threadList() + '</div>' +
      '<div class="card"><div class="card-title"><h3>Conversation</h3></div>' +
        '<div class="hint">Select a user to read and answer their thread.</div>' +
        '<div class="msg-list">' + messageRows(state.messages) + '</div>' +
        composer() + '</div></div>';
  } else {
    content.innerHTML = '<div class="card" style="max-width:760px">' +
      '<div class="card-title"><h3>Your conversation with the Super Admin</h3></div>' +
      '<div class="hint">Use this channel for account, registration or process questions. ' +
      'Messages are kept with your account.</div>' +
      '<div class="msg-list">' + messageRows(state.messages) + '</div>' +
      composer() + '</div>';
  }
  bind();
}

function bind() {
  document.getElementById('support-send')?.addEventListener('click', send);
  content.querySelectorAll('[data-thread]').forEach((button) => {
    button.addEventListener('click', async () => {
      state.threadId = button.dataset.thread;
      window.history.replaceState(null, '', '/support.html?thread=' + state.threadId);
      await loadMessages();
      render();
    });
  });
}

async function send() {
  const input = document.getElementById('support-body');
  const body = (input && input.value ? input.value : '').trim();
  if (!body) { toast('Write a message first.', 'warn'); return; }
  const button = document.getElementById('support-send');
  button.disabled = true;
  try {
    if (isSuperAdmin) {
      await api.post('/support/threads/' + state.threadId, { body });
    } else {
      await api.post('/support/messages', { body });
    }
    await loadMessages();
    if (isSuperAdmin) await loadThreads();
    render();
    toast('Message sent.', 'success');
  } catch (error) {
    toast(formatApiError(error), 'error');
    button.disabled = false;
  }
}

async function loadMessages() {
  try {
    if (isSuperAdmin) {
      state.messages = state.threadId
        ? await api.get('/support/threads/' + state.threadId)
        : [];
    } else {
      state.messages = await api.get('/support/messages');
    }
  } catch (error) {
    state.messages = [];
    toast(formatApiError(error), 'error');
  }
}

async function loadThreads() {
  try {
    state.threads = await api.get('/support/threads');
  } catch (error) {
    state.threads = [];
    toast(formatApiError(error), 'error');
  }
  const requested = new URLSearchParams(window.location.search).get('thread');
  if (!state.threadId && requested) state.threadId = requested;
  if (state.threadId && !state.threads.some((thread) => thread.user_id === state.threadId)) {
    state.threadId = null;
  }
}

async function load() {
  content.innerHTML = loading('Loading\u2026');
  if (isSuperAdmin) {
    await loadThreads();
    if (!state.threadId && state.threads.length) state.threadId = state.threads[0].user_id;
    await loadMessages();
  } else {
    await loadMessages();
  }
  render();
}

document.getElementById('refresh-support').addEventListener('click', load);

try {
  await requireSession('dashboard.view');
  await load();
} catch (error) {
  if (error.status !== 401) {
    content.innerHTML = '<div class="banner fail"><div>' + escapeHtml(formatApiError(error)) + '</div></div>';
  }
}
