try { document.documentElement.dataset.cinemaTheme = localStorage.getItem('cinema-theme') === 'dark' ? 'dark' : 'light'; } catch { document.documentElement.dataset.cinemaTheme = 'light'; }
