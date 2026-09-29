/* Theme controller.
 *
 * The palette lives entirely in css/app.css: :root is the dark control surface
 * and [data-theme="light"] restates it, so no component rule knows which theme
 * is active. This module owns the switch between them:
 *
 *   - the choice is stored in localStorage under "metriq-theme";
 *   - with no stored choice the operating system preference wins, and keeps
 *     winning until the operator chooses for themselves;
 *   - <html data-theme> is set by a small inline bootstrap in each page head,
 *     before first paint, so a light-mode operator never sees a dark flash.
 *
 * Any element carrying data-theme-toggle becomes a switch. Clicks are handled
 * by one delegated listener, so chrome rendered later by renderShell() is
 * wired without extra bookkeeping.
 */

const STORAGE_KEY = 'metriq-theme';
const MODES = ['light', 'dark'];
/* Glyph of the theme the switch would move to. */
const ICONS = { dark: '\u2600', light: '\u263e' };
const NAMES = { dark: 'Light', light: 'Dark' };

let wired = false;

function prefersLight() {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-color-scheme: light)').matches;
}

/** The operator's saved choice, or null when they have never made one. */
export function storedTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return MODES.includes(value) ? value : null;
  } catch (error) {
    return null; // Storage disabled (private browsing): fall back to the OS.
  }
}

/** The theme in force right now, falling back to the system preference. */
export function currentTheme() {
  const applied = document.documentElement.getAttribute('data-theme');
  if (MODES.includes(applied)) return applied;
  return storedTheme() || (prefersLight() ? 'light' : 'dark');
}

function syncSwitches(theme) {
  const dark = theme === 'dark';
  document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
    const label = dark ? 'Switch to light theme' : 'Switch to dark theme';
    button.setAttribute('aria-pressed', String(dark));
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
    const icon = button.querySelector('[data-theme-icon]');
    const text = button.querySelector('[data-theme-label]');
    if (icon) icon.textContent = ICONS[theme];
    if (text) text.textContent = NAMES[theme];
  });
}

export function applyTheme(mode, { persist = false } = {}) {
  const theme = MODES.includes(mode) ? mode : (prefersLight() ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', theme);
  /* The switch's word is written below, so the stylesheet holds it back
     until now rather than showing a word that may be wrong for a moment. */
  document.documentElement.setAttribute('data-theme-ready', 'true');
  if (persist) {
    try { localStorage.setItem(STORAGE_KEY, theme); } catch (error) { /* ignore */ }
  }
  syncSwitches(theme);
  document.dispatchEvent(new CustomEvent('metriq:theme', { detail: { theme } }));
  return theme;
}

export function setTheme(mode) { return applyTheme(mode, { persist: true }); }

export function toggleTheme() { return setTheme(currentTheme() === 'dark' ? 'light' : 'dark'); }

/** Apply the current theme and wire the switches. Idempotent, so page scripts
 *  and renderShell() can both call it. */
export function initTheme() {
  const theme = applyTheme(currentTheme());
  if (wired) return theme;
  wired = true;

  document.addEventListener('click', (event) => {
    const target = event.target;
    const button = target && target.closest ? target.closest('[data-theme-toggle]') : null;
    if (!button) return;
    event.preventDefault();
    toggleTheme();
  });

  if (typeof window.matchMedia === 'function') {
    const media = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => { if (!storedTheme()) applyTheme(prefersLight() ? 'light' : 'dark'); };
    if (typeof media.addEventListener === 'function') media.addEventListener('change', onChange);
    else if (typeof media.addListener === 'function') media.addListener(onChange);
  }
  return theme;
}