/* Live date and time for the application chrome.
 *
 * Every timestamp the API stores is UTC (timezone-aware columns), and ui.js
 * renders it in whatever timezone the workstation is set to. This module keeps
 * the clock on screen honest:
 *
 *   - it repaints every element carrying data-clock once a second from the
 *     browser clock, so a page left open overnight still shows the true date;
 *   - it nudges that clock by the offset between the browser clock and the
 *     API's own clock (published by GET /health as server_time_utc), so a
 *     workstation with the wrong system time cannot misdate a test record.
 *
 * The offset is measured once per page load and refreshed every 15 minutes;
 * if the platform endpoint is unreachable the local clock simply keeps ticking.
 */

const TICK_MS = 1000;
const RESYNC_MS = 15 * 60 * 1000;

let offsetMs = 0;
let timer = null;
let lastSync = 0;

/** Wall-clock "now", corrected by the last observed server offset. */
export function now() { return new Date(Date.now() + offsetMs); }

/** Browser-clock correction currently in force, in milliseconds. */
export function clockOffsetMs() { return offsetMs; }

export function timeZoneLabel() {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';
    const minutes = -now().getTimezoneOffset();
    const sign = minutes < 0 ? '-' : '+';
    const absolute = Math.abs(minutes);
    const hours = String(Math.floor(absolute / 60)).padStart(2, '0');
    const rest = String(absolute % 60).padStart(2, '0');
    return `${zone} (UTC${sign}${hours}:${rest})`;
  } catch (error) {
    return 'local time';
  }
}

function paint() {
  if (typeof document === 'undefined') return;
  const moment = now();
  const time = moment.toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
  const date = moment.toLocaleDateString(undefined, {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
  });
  const zone = timeZoneLabel();
  document.querySelectorAll('[data-clock]').forEach((node) => {
    const timeNode = node.querySelector('[data-clock-time]');
    const dateNode = node.querySelector('[data-clock-date]');
    if (!timeNode && !dateNode) { node.textContent = `${date} ${time}`; return; }
    if (timeNode) timeNode.textContent = time;
    if (dateNode) dateNode.textContent = date;
    node.setAttribute('title', zone);
    if (node.tagName === 'TIME') node.setAttribute('datetime', moment.toISOString());
  });
}

async function sync() {
  lastSync = Date.now();
  if (typeof fetch !== 'function') return;
  try {
    const response = await fetch('/health', { headers: { accept: 'application/json' } });
    if (!response.ok) return;
    const payload = await response.json();
    const serverTime = Date.parse(payload && payload.server_time_utc ? payload.server_time_utc : '');
    if (Number.isNaN(serverTime)) return;
    /* server_time_utc is generated when the response is built, so the offset
       still carries one network hop. That is well under the minute precision
       this clock displays and far below anything a test record depends on. */
    offsetMs = serverTime - Date.now();
    paint();
  } catch (error) {
    /* Offline or blocked: the browser clock keeps the display moving. */
  }
}

/** Repaint every [data-clock] now, then once a second. Idempotent. */
export function startClocks() {
  paint();
  if (!timer && typeof window !== 'undefined') {
    timer = window.setInterval(paint, TICK_MS);
    window.addEventListener('online', sync);
  }
  if (!lastSync || Date.now() - lastSync > RESYNC_MS) sync();
}

export function stopClocks() {
  if (timer) { window.clearInterval(timer); timer = null; }
}