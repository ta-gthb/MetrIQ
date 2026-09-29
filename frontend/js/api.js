/* MetrIQ API client.
 *
 * By default the browser talks to the same origin that serves these files: the
 * bundled backend mounts this folder, and on Vercel a rewrite proxies /api to
 * the Render service. Authorization is carried as a bearer token; the backend
 * is the security boundary and re-checks every permission and record scope on
 * each request (PRD 16.3).
 *
 * To call the API directly instead (bypassing the proxy), set an absolute base
 * before this module loads, e.g. in the page head:
 *
 *   <script>window.METRIQ_API_BASE = 'https://metriq-api.onrender.com';</script>
 *
 * Either form is accepted, with or without a trailing /api/v1.
 */

function resolveApiBase() {
  const configured = typeof window !== 'undefined' ? window.METRIQ_API_BASE : '';
  const base = String(configured || '').trim().replace(/\/+$/, '');
  if (!base) return '/api/v1';
  return base.endsWith('/api/v1') ? base : base + '/api/v1';
}

const API_BASE = resolveApiBase();
const SESSION_KEY = 'metriq.session';

function readSession() {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null');
  } catch (error) {
    return null;
  }
}

let session = readSession();

export class ApiError extends Error {
  constructor(status, detail, payload) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail));
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.payload = payload;
  }
}

export function getSession() { return session; }
export function getUser() { return session ? session.user : null; }
export function getPermissions() { return session ? session.permissions || [] : []; }
export function getRoleName() { return session ? session.role_name : null; }
export function can(code) { return getPermissions().includes(code); }
export function canAny(...codes) { return codes.some((code) => can(code)); }

export function setSession(next) {
  session = next;
  if (next) localStorage.setItem(SESSION_KEY, JSON.stringify(next));
  else localStorage.removeItem(SESSION_KEY);
}

export function clearSession() { setSession(null); }

function buildUrl(path, query) {
  const url = new URL(API_BASE + path, window.location.origin);
  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
    });
  }
  return url.toString();
}

async function request(method, path, options = {}) {
  const { body, formData, query, blob, redirectOn401 = true, skipRefresh = false } = options;
  const headers = {};
  if (session && session.access_token) headers.Authorization = `Bearer ${session.access_token}`;

  let payload;
  if (formData) {
    payload = formData;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: payload,
      // The refresh token is an HttpOnly cookie, so it has to travel with every
      // call - including the cross-origin one when a page points directly at
      // the API instead of using the host's rewrite (audit item 13).
      credentials: 'include',
    });
  } catch (error) {
    throw new ApiError(0, 'The MetrIQ service cannot be reached. Check your connection and retry.');
  }

  // Access tokens are short-lived on purpose. A 401 from an expired token is
  // transparent: refresh once and replay the request, so an hour-long token
  // feels like a session and a stolen one is worth an hour at most.
  if (response.status === 401 && !skipRefresh && session && session.access_token) {
    if (await refreshSession()) return request(method, path, { ...options, skipRefresh: true });
  }

  if (response.status === 401) {
    clearSession();
    if (redirectOn401 && !window.location.pathname.endsWith('/login.html')) {
      window.location.href = '/login.html?expired=1';
    }
    throw new ApiError(401, 'Your session has expired. Please sign in again.');
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const parsed = await response.json();
      detail = parsed.detail || parsed;
    } catch (error) { /* non-JSON error body */ }
    throw new ApiError(response.status, detail);
  }

  if (blob) return response.blob();
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

export const api = {
  get: (path, options) => request('GET', path, options),
  post: (path, body, options) => request('POST', path, { ...options, body }),
  put: (path, body, options) => request('PUT', path, { ...options, body }),
  patch: (path, body, options) => request('PATCH', path, { ...options, body }),
  delete: (path, options) => request('DELETE', path, options),
  upload: (path, formData, options) => request('POST', path, { ...options, formData }),
  download: (path, options) => request('GET', path, { ...options, blob: true }),
};

/* --------------------------------------------------------------- auth ---- */

/* The refresh token must never be kept in page storage: it arrives as an
 * HttpOnly cookie, and a copy here would put it back within reach of any script
 * on the page, which is the exposure the cookie exists to remove (item 13). */
function withoutRefreshToken(tokens) {
  if (!tokens) return tokens;
  const { refresh_token: _discarded, ...rest } = tokens;
  return rest;
}

/* One refresh at a time: a page that fires six requests after the token expired
 * must rotate once, not six times - the second rotation would look like a reused
 * token and sign the user out. */
let refreshInFlight = null;

async function refreshSession() {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const tokens = await request('POST', '/auth/refresh', {
          redirectOn401: false,
          skipRefresh: true,
        });
        setSession({ ...session, ...withoutRefreshToken(tokens) });
        if (!session.permissions || !session.permissions.length) await refreshProfile();
        return true;
      } catch (error) {
        clearSession();
        return false;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

export async function login(email, password) {
  const tokens = await api.post('/auth/login', { email, password }, { redirectOn401: false });
  setSession({ ...withoutRefreshToken(tokens), permissions: [] });
  await refreshProfile();
  return session;
}

export async function refreshProfile() {
  const me = await api.get('/me');
  setSession({
    ...session,
    user: me.user,
    role_name: me.role_name,
    permissions: me.permissions,
    laboratory: me.laboratory,
  });
  return session;
}

export async function logout() {
  try {
    // Revoke the session on the server, not only in this browser. Without this
    // a captured refresh token would keep working after "sign out".
    await api.post('/auth/logout', undefined, { redirectOn401: false, skipRefresh: true });
  } catch (error) {
    // An unreachable API must not trap the user on a signed-in page.
  }
  clearSession();
  window.location.href = '/login.html';
}

/** Guard a page: requires a session and, optionally, at least one permission. */
export async function requireSession(...permissions) {
  if (!session || !session.access_token) {
    window.location.href = `/login.html?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    throw new ApiError(401, 'Sign-in required.');
  }
  if (!session.permissions || !session.permissions.length) {
    try {
      await refreshProfile();
    } catch (error) {
      // A backend that is briefly unavailable must not lock the user out of a
      // page they can already see; the API calls themselves will surface it.
    }
  }
  if (permissions.length && !permissions.some((code) => can(code))) {
    throw new ApiError(403, 'Your role does not have access to this page.');
  }
  return session;
}

export function formatApiError(error) {
  if (!(error instanceof ApiError)) return String(error && error.message ? error.message : error);
  const detail = error.detail;
  if (typeof detail === 'string') return detail;
  if (detail && detail.message) {
    const extra = detail.blocking_tests || detail.failed_tests || [];
    return extra.length ? `${detail.message} (${extra.join(', ')})` : detail.message;
  }
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join('; ');
  if (detail) return JSON.stringify(detail);
  return error.message;
}
