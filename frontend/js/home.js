/* Landing page. The only script index.html needs; it used to be an inline
 * module, which the application's Content-Security-Policy does not allow
 * (audit item 13). */

import { getSession } from './api.js';
import { initTheme } from './theme.js';
import { startClocks } from './clock.js';

initTheme();
startClocks();

if (getSession() && getSession().access_token) {
  const enter = document.getElementById('enter');
  enter.textContent = 'Continue as ' + (getSession().user.full_name || 'user');
  enter.href = '/dashboard.html';
}
