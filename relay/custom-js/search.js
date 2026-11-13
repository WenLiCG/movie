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

  function update() {
    const header = document.querySelector('.headerRight');
    let signedIn = false;
    try {
      // Emby's own session determines visibility; no credential crosses to Prowlarr.
      signedIn = typeof ApiClient !== 'undefined' && Boolean(
        ApiClient.getCurrentUserId() && ApiClient.accessToken()
      );
    } catch {
      signedIn = false;
    }

    const onLoginPage = /login|selectserver|startup|welcome/i.test(location.hash);
    const videoVisible = Array.from(document.querySelectorAll('video')).some(
      video => video.getClientRects().length > 0
    );
    link.hidden = !signedIn || onLoginPage || Boolean(document.fullscreenElement) || videoVisible;

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
  if(!window.opener || !location.hash.includes('cinemaHandoff=1'))return;
  // Under /movie (movie3 public wall) keep the prefix; movie2 path /web/... is unchanged.
  const cinemaPathPrefix=(typeof location.pathname==='string'&&/^\/movie(\/|$)/i.test(location.pathname))?'/movie':'';
  const cinemaServerAddress=location.origin+cinemaPathPrefix;
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
      Object.assign(server,{Name:auth.name,ManualAddress:cinemaServerAddress,ManualAddressOnly:true,LocalAddress:cinemaServerAddress,RemoteAddress:cinemaServerAddress,LastConnectionMode:2,DateLastAccessed:Date.now(),UserId:auth.user_id});
      server.Users=(server.Users||[]).filter(u=>u.UserId!==auth.user_id);server.Users.push({UserId:auth.user_id,AccessToken:auth.token});delete server.AccessToken;
      localStorage.setItem('servercredentials3',JSON.stringify(credentials));localStorage.setItem('autoLogin','lastuser');
      accepted=true;window.opener.postMessage({type:'cinema-watch-complete'},portal);
      location.replace(cinemaPathPrefix+'/web/index.html#!/home');
    }catch{window.opener.postMessage({type:'cinema-watch-error'},portal);}
  });
  window.opener.postMessage({type:'cinema-watch-ready'},portal);
})();
