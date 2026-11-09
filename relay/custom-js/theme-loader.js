(() => {
  if (window.__cinemaThemeLoader) return;
  window.__cinemaThemeLoader = true;
  const script = document.createElement('script');
  const local = ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname);
  script.src = local ? `${location.protocol}//${location.hostname}:8097/discover/theme.js` : '/discover/theme.js';
  document.head.append(script);
})();
