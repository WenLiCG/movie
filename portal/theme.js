(() => {
  if (window.CinemaTheme) return;
  const key = 'cinema-theme';
  let selected = 'light';
  try { if (localStorage.getItem(key) === 'dark') selected = 'dark'; } catch {}
  const portal = Boolean(document.getElementById('workspace'));
  function applyNative() {
    const root = document.documentElement, theme = 'theme-' + selected;
    for (const value of Array.from(root.classList)) if (value.startsWith('theme-') && value !== theme) root.classList.remove(value);
    if (!root.classList.contains(theme)) root.classList.add(theme);
    const links = ['theme.css', 'theme_nontv.css'].map(name => {
      const id = 'cinema-theme-' + name.replace('.', '-');
      let link = document.getElementById(id);
      if (!link) { link = document.createElement('link'); link.id = id; link.rel = 'stylesheet'; }
      const href = '/web/modules/themes/' + selected + '/' + name;
      if (link.getAttribute('href') !== href) link.setAttribute('href', href);
      return link;
    });
    // Native themes contain unscoped component rules, so both themes cannot stay enabled.
    document.querySelectorAll('head link[rel="stylesheet"]').forEach(link => {
      const match = link.href.match(/\/modules\/themes\/([^/]+)\//);
      if (match && match[1] !== 'common') link.disabled = match[1] !== selected;
    });
    // Preserve loaded stylesheets when SPA components push the theme down the cascade.
    if (document.head.lastElementChild !== links[1] || links[1].previousElementSibling !== links[0]) {
      for (const link of links) {
        if (link.parentElement === document.head && document.head.moveBefore) document.head.moveBefore(link, null);
        // shortcut: older browsers may reload CSS; drop reinsertion once moveBefore is universal.
        else document.head.append(link);
      }
    }
  }
  function apply(value) {
    selected = value === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.cinemaTheme = selected;
    document.querySelectorAll('[data-theme-select]').forEach(el => el.value = selected);
    if (!portal) {
      applyNative();
      const button = document.getElementById('cinema-theme-toggle');
      if (button) { button.textContent = selected === 'light' ? '深色' : '亮色'; button.title = '全局切换至' + button.textContent + '主题'; }
    }
  }
  function set(value) { try { localStorage.setItem(key, value); } catch {} apply(value); }
  window.CinemaTheme = {set, get: () => selected};
  document.querySelectorAll('[data-theme-select]').forEach(el => el.addEventListener('change', () => set(el.value)));
  window.addEventListener('storage', event => { if (event.key === key) apply(event.newValue); });
  window.addEventListener('pageshow', () => { try { apply(localStorage.getItem(key)); } catch { apply(selected); } });
  apply(selected);
  if (!portal) {
    const button = document.createElement('button'); button.id = 'cinema-theme-toggle'; button.type = 'button';
    button.className = 'headerButton headerSectionItem cinema-theme-toggle';
    button.onclick = () => set(selected === 'light' ? 'dark' : 'light');
    function update() {
      const header = document.querySelector('.headerRight');
      const video = Array.from(document.querySelectorAll('video')).some(v => v.getClientRects().length);
      button.hidden = Boolean(document.fullscreenElement) || video;
      if (header && button.parentElement !== header) { header.append(button); apply(selected); }
    }
    new MutationObserver(update).observe(document.body, {childList: true, subtree: true});
    new MutationObserver(applyNative).observe(document.documentElement, {attributes: true, attributeFilter: ['class']});
    new MutationObserver(applyNative).observe(document.head, {childList: true});
    document.addEventListener('viewshow', () => { update(); applyNative(); }); document.addEventListener('fullscreenchange', update);
    document.addEventListener('play', update, true); update();
  }
})();
