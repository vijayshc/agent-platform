/* Early theme application to prevent initial flicker.
 * Runs before CSS fully parses and before ThemeManager loads.
 * Chooses theme from the persisted (localStorage) choice, falling back to the
 * per-tab session value, then to 'light'. The preload never writes storage.
 */
(function() {
  try {
    var t = localStorage.getItem('selectedTheme') || sessionStorage.getItem('selectedTheme') || 'light';
    // attach classes ASAP
    document.documentElement.classList.add('js', 'theme-' + t);
    var addBody = function(){
      document.body.classList.add('theme-' + t, 'theme-preload');
    };
    if (document.body) addBody(); else document.addEventListener('DOMContentLoaded', addBody);
  } catch (e) { /* no-op */ }
})();
