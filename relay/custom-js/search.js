(() => {
  if (window.__cinemaSearchInstalled) return;
  window.__cinemaSearchInstalled = true;

  const link = document.createElement('a');
  link.id = 'cinema-search';
  link.className = 'headerButton headerSectionItem cinema-search';
  link.href = ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname)
    ? `${location.protocol}//${location.hostname}:8097/discover/`
    : '/discover/';
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.title = '找片与追剧（新标签页，使用 Emby 管理员账号）';
  link.setAttribute('aria-label', link.title);
  link.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><span>找片 / 追剧</span>';

  let adminUserId = null, isAdmin = null;

  function checkAdmin(userId) {
    if (!userId) {
      adminUserId = null;
      isAdmin = null;
      return false;
    }
    if (typeof ApiClient === 'undefined') return false;
    if (typeof ApiClient.getCurrentUser !== 'function') {
      const policy = ApiClient._currentUser && ApiClient._currentUser.Policy;
      return policy && typeof policy.IsAdministrator === 'boolean' ? policy.IsAdministrator : true;
    }
    if (adminUserId !== userId) {
      adminUserId = userId;
      const cached = ApiClient._currentUser && ApiClient._currentUser.Id === userId ? ApiClient._currentUser : null;
      isAdmin = cached && cached.Policy ? cached.Policy.IsAdministrator === true : null;
      Promise.resolve(ApiClient.getCurrentUser()).then(user => {
        if (adminUserId === userId) {
          isAdmin = Boolean(user && user.Policy && user.Policy.IsAdministrator === true);
          update();
        }
      }).catch(() => {
        if (adminUserId === userId) {
          isAdmin = false;
          update();
        }
      });
    }
    return isAdmin === true;
  }

  function update() {
    const header = document.querySelector('.headerRight');
    let userId = null, signedIn = false;
    try {
      // Emby's own session determines visibility; no credential crosses to Prowlarr.
      userId = typeof ApiClient !== 'undefined' ? ApiClient.getCurrentUserId() : null;
      signedIn = Boolean(userId && ApiClient.accessToken());
    } catch {
      userId = null;
      signedIn = false;
    }

    const canManage = signedIn && checkAdmin(userId);
    if (!signedIn) {
      adminUserId = null;
      isAdmin = null;
    }
    const onLoginPage = /login|selectserver|startup|welcome/i.test(location.hash);
    const videoVisible = Array.from(document.querySelectorAll('video')).some(
      video => video.getClientRects().length > 0
    );
    link.hidden = !canManage || onLoginPage || Boolean(document.fullscreenElement) || videoVisible;

    if (!header) {
      link.remove();
    } else if (link.parentElement !== header) {
      header.prepend(link);
    }
  }

  document.addEventListener('viewshow', update);
  document.addEventListener('fullscreenchange', update);
  document.addEventListener('play', update, true);
  window.addEventListener('hashchange', update);
  window.addEventListener('pageshow', update);
  // Emby replaces its navigation during SPA transitions.
  new MutationObserver(update).observe(document.body, { childList: true, subtree: true });
  update();
})();


// User-session handoff stays in postMessage, never in a URL or a server log.
(() => {
  // Emby's SPA router may rewrite location.hash before custom.js loads, so check query too.
  const handoff = (location.search || '').includes('cinemaHandoff=') || (location.hash || '').includes('cinemaHandoff=1');
  if(!window.opener || !handoff)return;
  const portal=['127.0.0.1','localhost','[::1]'].includes(location.hostname)?location.protocol+'//'+location.hostname+':8097':location.origin;
  let accepted=false;
  window.addEventListener('message',event=>{
    if(accepted||event.source!==window.opener||event.origin!==portal||event.data?.type!=='cinema-watch-login')return;
    const auth=event.data.auth;
    if(!auth||!['server_id','user_id','token','name'].every(k=>typeof auth[k]==='string'&&auth[k].length>0&&auth[k].length<4096))return;
    try {
      let credentials=JSON.parse(localStorage.getItem('servercredentials3')||'{}');
      if(!Array.isArray(credentials.Servers))credentials.Servers=[];
      let server=credentials.Servers.find(s=>s.Id===auth.server_id);
      if(!server){server={Id:auth.server_id};credentials.Servers.push(server);}
      Object.assign(server,{Name:auth.name,ManualAddress:location.origin,ManualAddressOnly:true,LocalAddress:location.origin,RemoteAddress:location.origin,LastConnectionMode:2,DateLastAccessed:Date.now(),UserId:auth.user_id});
      server.Users=(server.Users||[]).filter(u=>u.UserId!==auth.user_id);server.Users.push({UserId:auth.user_id,AccessToken:auth.token});delete server.AccessToken;
      localStorage.setItem('servercredentials3',JSON.stringify(credentials));localStorage.setItem('autoLogin','lastuser');
      accepted=true;window.opener.postMessage({type:'cinema-watch-complete'},portal);
      location.replace('/web/index.html#!/home');
    }catch{window.opener.postMessage({type:'cinema-watch-error'},portal);}
  });
  window.opener.postMessage({type:'cinema-watch-ready'},portal);
})();
