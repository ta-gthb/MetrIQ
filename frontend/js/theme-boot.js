/* Apply the stored theme before the first paint.
 *
 * This used to be an inline <script> in all eight pages. It is a file now so the
 * Content-Security-Policy can say `script-src 'self'` with no inline exception
 * (audit item 13). It is loaded synchronously from <head>, so it still runs
 * before the first paint and a page opened in light mode never flashes the dark
 * palette first.
 */
(function () {
  try {
    var stored = localStorage.getItem('metriq-theme');
    var prefersLight = window.matchMedia
      && window.matchMedia('(prefers-color-scheme: light)').matches;
    document.documentElement.setAttribute('data-theme',
      stored === 'light' || stored === 'dark' ? stored : (prefersLight ? 'light' : 'dark'));
  } catch (error) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
