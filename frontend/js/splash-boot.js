/* Play the startup animation of the public home page once per session.
 *
 * The overlay itself is markup and stylesheet (see .splash in css/app.css), so
 * the page is never left covered even if this file never runs. What this adds
 * is the decision, taken before the first paint - it is loaded synchronously
 * from <head> - that a repeat visit within the same session goes straight to
 * the page. It is a file rather than an inline script so the
 * Content-Security-Policy needs no inline exception.
 */
(function () {
  var KEY = 'metriq-splash-shown';
  try {
    if (window.sessionStorage.getItem(KEY) === '1') {
      document.documentElement.setAttribute('data-splash', 'off');
      return;
    }
    window.sessionStorage.setItem(KEY, '1');
  } catch (error) {
    /* Storage is unavailable: the animation simply plays. */
  }
})();
