// Apply the saved theme before the page paints, including when the server is offline.
(() => {
  let theme = 'dark';
  try {
    const saved = localStorage.getItem('stork-theme');
    if (saved === 'dark' || saved === 'light') theme = saved;
  } catch {
    // Private browsing can deny storage; theme switching still works for this page.
  }

  function applyTheme(value) {
    theme = value;
    document.documentElement.dataset.theme = theme;
    const button = document.getElementById('theme-toggle');
    if (button) {
      const label = `Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`;
      button.textContent = theme === 'dark' ? '☀' : '☾';
      button.setAttribute('aria-label', label);
      button.title = label;
    }
    window.dispatchEvent(new Event('stork-theme-change'));
  }

  applyTheme(theme);
  document.addEventListener('DOMContentLoaded', () => {
    applyTheme(theme);
    document.getElementById('theme-toggle').addEventListener('click', () => {
      applyTheme(theme === 'dark' ? 'light' : 'dark');
      try { localStorage.setItem('stork-theme', theme); } catch { /* Storage is optional. */ }
    });
  });
  window.addEventListener('storage', event => {
    if (event.key === 'stork-theme') applyTheme(event.newValue === 'light' ? 'light' : 'dark');
  });
})();
