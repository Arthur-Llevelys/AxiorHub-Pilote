/* Local preferences only. No request or personal data is sent to a service. */
(() => {
  'use strict';
  const root = document.documentElement;
  const themeButton = document.getElementById('ws-theme-toggle');
  const sidebarButton = document.getElementById('ws-sidebar-toggle');
  const sidebar = document.getElementById('ws-sidebar');
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const save = (key, value) => { try { localStorage.setItem(key, value); } catch { /* Private mode. */ } };
  const keyTheme = 'axiorhub-theme-v320';
  const keySidebar = 'axiorhub-sidebar-v320';
  const systemDark = window.matchMedia('(prefers-color-scheme: dark)');
  let chosen = read(keyTheme);
  let collapsed = read(keySidebar) === 'collapsed';

  function theme() {
    const dark = (chosen === 'dark') || (chosen !== 'light' && systemDark.matches);
    root.dataset.theme = dark ? 'dark' : 'light';
    if (themeButton) {
      const label = dark ? 'Activer le thème clair' : 'Activer le thème sombre';
      themeButton.setAttribute('aria-label', label);
      themeButton.title = label;
      themeButton.querySelector('span').textContent = dark ? '☀' : '☾';
    }
  }
  function menu() {
    root.classList.toggle('ws-collapsed', collapsed);
    if (sidebarButton) {
      const label = collapsed ? 'Déplier le menu' : 'Replier le menu';
      sidebarButton.setAttribute('aria-expanded', String(!collapsed));
      sidebarButton.setAttribute('aria-label', label);
      sidebarButton.title = label;
    }
    if (sidebar) sidebar.querySelectorAll('.ws-secondary details').forEach(details => {
      if (collapsed) details.open = false;
      details.inert = collapsed;
    });
  }
  theme();
  menu();
  themeButton?.addEventListener('click', () => {
    chosen = root.dataset.theme === 'dark' ? 'light' : 'dark';
    save(keyTheme, chosen);
    theme();
  });
  sidebarButton?.addEventListener('click', () => {
    collapsed = !collapsed;
    save(keySidebar, collapsed ? 'collapsed' : 'expanded');
    menu();
  });
  systemDark.addEventListener?.('change', () => { if (chosen !== 'dark' && chosen !== 'light') theme(); });
})();
