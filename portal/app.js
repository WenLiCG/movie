const $ = id => document.getElementById(id);
let csrf = '', items = [], kind = 'movie', following = '', currentView = 'search', metricsBusy = false;
let episodeTarget = null, episodeSeries = null, episodeItems = [], selectedEpisodes = new Set();
let episodeGeneration = 0, episodeTimer = null, episodeDeadline = 0, episodeVersionReady = false;
let selectedRelease = null, versionPreview = null, versionGeneration = 0;
const versionLabels = {group:'发布组',resolution:'清晰度',codec:'编码',source:'片源类型',platform:'片源平台',audio:'音轨',subtitles:'字幕'};
const versionInputs = {group:'version-group',resolution:'quality',codec:'version-codec',source:'version-source',platform:'version-platform',audio:'version-audio',subtitles:'version-subtitles'};
function size(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '大小未知';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB'];
  let unit = 0;
  while (bytes >= 1024 && unit < units.length - 1) {
    bytes /= 1024;
    unit++;
  }
  return `${Number(bytes.toFixed(1))} ${units[unit]}`;
}
if (['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname)) {
  const ports = {'/': [8095, '/'], '/search/': [9696, '/search/'], '/sonarr/': [8989, '/sonarr/'], '/qb/': [8080, '/'], '/openlist/': [5244, '/openlist/']};
  document.querySelectorAll('a[href]').forEach(a => { const target = ports[a.getAttribute('href')]; if (target) a.href = `${location.protocol}//${location.hostname}:${target[0]}${target[1]}`; });
}
function node(tag, value, cls, symbol) {
  const el = document.createElement(tag);
  if (value !== undefined) el.textContent = value;
  if (cls) el.className = cls;
  if (symbol) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    svg.setAttribute('class', 'icon'); svg.setAttribute('aria-hidden', 'true');
    use.setAttribute('href', '#i-' + symbol); svg.append(use); el.prepend(svg);
  }
  return el;
}
function notice(message, error = false) {
  if ($('episodes-dialog').open) { $('episode-notice').textContent = message; $('episode-notice').hidden = false; return; }
  if ($('follow-dialog').open) { $('follow-notice').textContent = message; $('follow-notice').hidden = false; return; }
  $('notice-text').textContent = message; $('notice').className = error ? 'error' : ''; $('notice').hidden = false;
}
$('dismiss-notice').onclick = () => $('notice').hidden = true;
async function api(path, body) {
  let response;
  try { response = await fetch('/discover/api/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: body === undefined ? undefined : JSON.stringify(body)}); }
  catch { throw new Error('无法连接影院服务，请检查网络后重试。'); }
  if (response.status === 401) signedIn(false);
  let data;
  try { data = await response.json(); } catch { throw Object.assign(new Error(response.status === 401 ? '会话已失效，请使用 Emby 管理员账号登录。' : '服务暂时不可用，请稍后重试。'), {status: response.status}); }
  if (!response.ok) throw Object.assign(new Error(data.error || '操作失败，请稍后重试。'), {status: response.status});
  return data;
}
async function action(button, work) { button.disabled = true; try { await work(); } catch (error) { notice(error.message, true); } finally { button.disabled = false; } }
function signedIn(value) {
  $('notice').hidden = true;
  if (value) {
    loadRecommendations();
  } else {
    recommendationGeneration++;
    csrf = '';
    $('cloud-form').reset();
    $('retention-form').reset();
    delete $('days').dataset.edited;
    $('query').value = '';
    document.querySelector('[name=kind][value=movie]').checked = true;
    $('recommendation-groups').replaceChildren();
    resetSearch();
    ['series-list', 'task-list', 'playback-list'].forEach(id => $(id).replaceChildren());
    $('task-refresh-status').textContent = $('playback-refresh-status').textContent = '';
    show('search');
    ['follow-dialog', 'episodes-dialog', 'task-dialog'].forEach(id => $(id).close());
  }
  $('login').hidden = value;
  for (const id of ['workspace', 'metrics', 'logout', 'watch']) $(id).hidden = !value;
  document.querySelector('nav').hidden = !value;
}
let recommendationGeneration=0;
async function loadRecommendations(){
  const generation=++recommendationGeneration;
  $('recommendation-status').textContent='正在读取站点近期列表…';$('recommendation-groups').replaceChildren();
  try{
    const result=await api('recommendations');
    if(generation!==recommendationGeneration)return;
    const values=result.items||[];
    $('recommendation-status').textContent=result.message||'优先显示站点中文片名，点击直接搜索。近期列表按做种人数排序，每 30 分钟更新；非全站完整热榜。';
    for(const [type,label,symbol] of [['movie','热门电影','film'],['tv','热门剧集／综艺','tv']]){
      const list=values.filter(v=>v.kind===type);if(!list.length)continue;
      const section=node('section',undefined,'recommendation-column');section.append(node('h3',label,undefined,symbol));
      const links=node('ol');
      list.forEach((v,index)=>{
        const item=node('li'),button=node('button',undefined,'recommendation-link');button.type='button';button.title=v.release;
        const body=node('span',undefined,'recommendation-body');body.append(node('strong',v.title),node('small',`${v.site} · 做种 ${v.seeders}`));
        button.append(node('span',String(index+1).padStart(2,'0'),'recommendation-rank'),body,node('span','搜索','recommendation-action','arrow'));
        button.onclick=()=>{document.querySelector(`[name=kind][value=${type}]`).checked=true;$('query').value=v.query;$('search-form').requestSubmit();};
        item.append(button);links.append(item);
      });section.append(links);$('recommendation-groups').append(section);
    }
    $('welcome').hidden=values.length>0||$('search').classList.contains('has-results');
  }catch(error){if(generation===recommendationGeneration)$('recommendation-status').textContent='站点近期列表暂时不可用；仍可直接搜索。';}
}
const metaTones = {'免费':'success','追更中':'success','已发布':'success','播放中':'success','网盘直连跳转':'success','下载中':'info','归档中':'info','同步集数中':'info','已暂停':'warning','下载已暂停':'warning','规则异常':'danger','VPS 中转':'danger','请求失败':'danger'};
function row(title, meta, cls = 'result-row') {
  const el = node('article', undefined, cls), body = node('div', undefined, 'row-body');
  body.append(node('h3', title, 'row-title', cls === 'series-row' ? 'tv' : cls === 'task-row' ? 'download' : cls === 'playback-row' ? 'play' : cls === 'upload-row' ? undefined : kind === 'tv' ? 'tv' : 'film'));
  const details = node('div', undefined, 'row-meta');
  meta.filter(v => v !== undefined && v !== null && v !== '').forEach(v => details.append(node('span', String(v), Object.hasOwn(metaTones,v) ? 'status-label ' + metaTones[v] : undefined)));
  body.append(details); el.append(body); return el;
}
async function show(view) { currentView = view; document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== view); document.querySelectorAll('[data-view]').forEach(el => { if (el.dataset.view === view) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current'); }); try { if (view === 'settings') await status(); if (view === 'series') await series(); if (view === 'tasks') await tasks(); if(view==='playbacks')await playbacks(); } catch (e) { notice(e.message, true); } }
document.querySelectorAll('[data-view]').forEach(el => el.onclick = () => show(el.dataset.view));
$('login-form').onsubmit = event => { event.preventDefault(); action(event.submitter, async () => { const result = await api('login', {username: $('username').value, password: $('password').value}); csrf = result.csrf; $('password').value = ''; signedIn(true); await status(); await metrics(); }); };
$('logout').onclick = () => action($('logout'), async () => { await api('logout', {}); csrf = ''; signedIn(false); });
let searchGeneration=0, searchWarnings=[];
function resetSearch() {
  searchGeneration++;
  items = [];
  $('results').replaceChildren();
  $('result-count').textContent = '';
  for (const id of ['filters', 'search-warning', 'tv-download-note']) $(id).hidden = true;
  $('search').classList.remove('has-results');
  $('welcome').hidden = $('recommendation-groups').childElementCount > 0;
}
$('query').addEventListener('input', () => {
  if (!$('query').value.trim()) resetSearch();
});
async function search() {
  const generation=++searchGeneration;
  kind=document.querySelector('[name=kind]:checked').value;
  const requestedKind=kind, query=$('query').value;
  $('result-count').textContent='正在搜索，请稍候…'; $('results').replaceChildren();
  $('filters').hidden=true; $('search-warning').hidden=true; $('welcome').hidden=true; $('search').classList.add('has-results');
  try {
    const result=await api('search?'+new URLSearchParams({q:query,kind:requestedKind}));
    if(generation!==searchGeneration)return;
    items=result.items; searchWarnings=[...(result.warnings||[])];
    const fallback=kind==='movie'&&!items.some(i=>i.resolution==='1080p');
    if(fallback&&items.length)searchWarnings.push('没有找到 1080p 资源，已显示全部清晰度。');
    $('resolution').value=kind==='movie'&&!fallback?'1080p':'';
    $('codec').value=''; $('free').checked=false;
    $('site').replaceChildren(new Option('全部站点',''),...[...new Set(items.map(v=>v.site).filter(Boolean))].map(v=>new Option(v,v)));
    $('filters').hidden=false; render();
  } catch(e) { if(generation===searchGeneration){$('result-count').textContent='搜索未完成，请检查站点连接后重试。';throw e;} }
}
$('search-form').onsubmit=event=>{event.preventDefault();action(event.submitter||$('search-form').querySelector('button'),search);};
document.querySelectorAll('[name=kind]').forEach(input=>input.onchange=()=>{if($('query').value.trim())search().catch(e=>notice(e.message,true));});
function versionSummary(fields) { return Object.entries(fields || {}).filter(([,v])=>v).map(([k,v])=>`${versionLabels[k]}：${v}`).join(' · '); }
function fillVersion(fields, editable) {
  Object.entries(versionInputs).forEach(([key,id])=>{ $(id).value=fields?.[key] || ''; $(id).disabled=!editable; });
  $('version-extra').open=false;
  updateVersionSummary();
  const missing=Object.keys(versionInputs).filter(k=>!fields?.[k]);
  $('version-missing').textContent=missing.length ? `尚未限定：${missing.map(k=>versionLabels[k]).join('、')}。请补充标题中的标记，或确认这些条件不限。` : '识别结果需确认；只保证标题同时符合这些条件。';
}
function updateVersionSummary(){
  $('version-summary').textContent='将同时要求：'+Object.entries(versionInputs).map(([key,id])=>`${versionLabels[key]} ${$(id).value.trim()||'不限'}`).join(' · ');
}
Object.values(versionInputs).forEach(id=>$(id).addEventListener('input',updateVersionSummary));
function chooseSeries() {
  following=$('series-match').value;
  const candidate=versionPreview?.candidates.find(c=>c.id===following);
  const fields=versionPreview?.fields;
  fillVersion(fields,true);
  $('season').replaceChildren(...(candidate?.seasons || []).map(v=>new Option(`第 ${v} 季`,v)));
  $('existing-version').hidden=!candidate?.version;
  $('existing-version').textContent=candidate?.version ? '当前版本：'+versionSummary(candidate.version)+'。换版不会删除已有内容。' : '';
  $('replace-version-row').hidden=!candidate?.version;
  $('replace-version').checked=false;
}
$('series-match').onchange=chooseSeries;
async function openResource(item) {
  const generation=++versionGeneration;
  selectedRelease=item;versionPreview=null;following='';
  $('follow-title').textContent='按此版本追更';
  $('version-resource').textContent=item.title;
  $('follow-notice').hidden=true;$('version-content').hidden=true;$('version-loading').hidden=false;
  $('submit-version').disabled=true;$('version-confirm').checked=false;$('version-confirm').required=true;
  $('version-confirm-row').hidden=false;$('series-match').disabled=false;
  $('follow-scope').hidden=false;
  $('mode').value='all';$('season-label').hidden=true;
  $('submit-version').textContent='保存规则并追更';
  $('follow-dialog').showModal();
  try {
    const preview=await api('preview-version',{release_id:item.id});
    if(generation!==versionGeneration || !$('follow-dialog').open)return;
    if(!preview.available){$('follow-notice').textContent=preview.message;$('follow-notice').hidden=false;return;}
    versionPreview=preview;
    const options=preview.candidates.map(c=>new Option(c.title+(c.year?` · ${c.year}`:''),c.id));
    if(options.length>1)options.unshift(new Option('请选择正确的剧集／综艺',''));
    $('series-match').replaceChildren(...options);chooseSeries();
    $('version-content').hidden=false;$('submit-version').disabled=false;
    $('series-match').focus({preventScroll:true});$('follow-dialog').scrollTop=0;
  } finally {if(generation===versionGeneration)$('version-loading').hidden=true;}
}
function openFollow(item) {
  versionGeneration++;selectedRelease=null;versionPreview=null;following=String(item.tvdbId);
  $('follow-title').textContent='设置追更范围';$('version-resource').textContent=item.title;
  $('follow-notice').hidden=true;$('version-loading').hidden=true;$('version-content').hidden=false;
  $('series-match').replaceChildren(new Option(item.title,following));$('series-match').disabled=true;
  fillVersion(item.version.fields,false);
  $('version-confirm-row').hidden=true;$('version-confirm').required=false;$('replace-version-row').hidden=true;$('existing-version').hidden=true;
  $('follow-scope').hidden=false;
  $('mode').value='all';$('season-label').hidden=true;
  $('season').replaceChildren(...(item.seasons||[]).filter(v=>v.seasonNumber>0).map(v=>new Option(`第 ${v.seasonNumber} 季`,v.seasonNumber)));
  $('submit-version').disabled=false;$('submit-version').textContent='保存追更范围';$('follow-dialog').showModal();
}
$('follow-dialog').addEventListener('close',()=>{versionGeneration++;});
function releaseDownload(item, tv) {
  // Reuse the control while filters redraw this result, including during a submission.
  if (item.downloadButton) return item.downloadButton;
  const title = tv ? '下载此资源' : '下载';
  const button = node('button', undefined, tv ? undefined : 'primary', 'download');
  const label = node('span', title);
  button.append(label);
  button.onclick = async () => {
    button.disabled = true;
    label.textContent = '正在提交…';
    try {
      const result = tv ? await api('download-release', {release_id: item.id}) : await api('download', {id: item.id});
      label.textContent = '已提交';
      notice(result.message);
    } catch (error) {
      label.textContent = title;
      button.disabled = false;
      notice(error.message, true);
    }
  };
  item.downloadButton = button;
  return button;
}
function render() {
  const values=items.filter(i=>(!$('resolution').value||i.resolution===$('resolution').value)&&(!$('codec').value||i.codec===$('codec').value)&&(!$('site').value||i.site===$('site').value)&&(!$('free').checked||i.free));
  $('search-warning').textContent=searchWarnings.join(' '); $('search-warning').hidden=!searchWarnings.length;
  $('tv-download-note').hidden=kind!=='tv';
  $('results').replaceChildren();$('result-count').textContent=values.length?`${values.length} 个结果`:'没有匹配结果。可调整筛选，或在设置中检查搜索站点。';
  values.forEach(item=>{
    const el=row(item.title,[item.resolution,item.codec,size(item.size),`做种 ${item.seeders??'未知'}`,item.site,item.free?'免费':'',kind==='tv'?(item.group?`发布组 ${item.group}`:'发布组待识别'):'',item.episodes,item.categories]);
    if(item.seeders===0)el.classList.add('no-seeders');
    if(item.subtitle)el.firstChild.insertBefore(node('p',item.subtitle,'row-description'),el.firstChild.lastChild);
    if(item.labels?.length){const labels=node('div',undefined,'release-labels');item.labels.forEach(v=>labels.append(node('span',v,'release-label')));el.firstChild.append(labels);}
    if(kind==='tv'){
      const follow=node('button','按此版本追更','primary','follow');follow.onclick=()=>action(follow,()=>openResource(item));
      const actions=node('div',undefined,'row-actions');actions.append(releaseDownload(item,true),follow);el.append(actions);
    }else{
      el.append(releaseDownload(item,false));
    }
    $('results').append(el);
  });
}
async function openEpisodes(item) {
  clearTimeout(episodeTimer); episodeGeneration++; episodeDeadline = Date.now() + 120000;
  episodeTarget = item; episodeSeries = null; episodeItems = []; episodeVersionReady=false; selectedEpisodes.clear();
  $('episodes-title').textContent = item.title + ' · 选集下载'; $('episode-notice').hidden = true;
  $('episode-list').replaceChildren(); $('download-episodes').disabled = true;
  $('episode-season').replaceChildren(); $('episodes-dialog').showModal(); await loadEpisodes();
}
async function loadEpisodes(generation = episodeGeneration) {
  clearTimeout(episodeTimer);
  const target = episodeTarget;
  $('episode-loading').hidden = false; $('episode-list').setAttribute('aria-busy', 'true');
  try {
    const result = await api('episodes', {id: target.id, quality: '1080p'});
    if (generation !== episodeGeneration || !$('episodes-dialog').open) return;
    episodeSeries = result.series_id; episodeItems = result.items; episodeVersionReady=result.version_ready===true; if(!episodeVersionReady){$('episode-notice').textContent='该剧尚无有效版本规则，请先在剧集／综艺搜索中选择资源并保存版本。';$('episode-notice').hidden=false;}else if($('episode-notice').textContent.startsWith('该剧尚无有效版本规则'))$('episode-notice').hidden=true;
    const season = $('episode-season').value;
    const seasons = [...new Set(episodeItems.map(e => e.seasonNumber))].sort((a, b) => a - b);
    $('episode-season').replaceChildren(...seasons.map(v => new Option(`第 ${v} 季`, v)));
    if (seasons.includes(Number(season))) $('episode-season').value = season;
    selectedEpisodes = new Set([...selectedEpisodes].filter(id => episodeItems.some(e => e.id === id && !e.hasFile && !e.archived && !e.downloading)));
    renderEpisodes();
    if (!episodeItems.length && Date.now() < episodeDeadline) {
      episodeTimer = setTimeout(() => loadEpisodes(generation).catch(e => notice(e.message, true)), 1500);
    } else {
      $('episode-loading').hidden = true; $('episode-list').setAttribute('aria-busy', 'false');
    }
  } catch (error) {
    if (generation === episodeGeneration) { $('episode-loading').hidden = true; $('episode-list').setAttribute('aria-busy', 'false'); }
    throw error;
  }
}
$('episodes-dialog').addEventListener('close', () => { episodeGeneration++; clearTimeout(episodeTimer); $('episode-loading').hidden = true; });
function renderEpisodes() {
  $('episode-list').replaceChildren();
  const values = episodeItems.filter(e => e.seasonNumber === Number($('episode-season').value)).sort((a,b) => a.episodeNumber - b.episodeNumber);
  if (!values.length) $('episode-list').append(node('p', Date.now() < episodeDeadline ? '正在同步剧集信息，完成后会自动显示。' : '元数据获取较慢，请稍后重试。', 'muted'));
  values.forEach(e => { const label = node('label', undefined, 'episode-option'), input = node('input'); input.type = 'checkbox'; input.value = e.id; input.disabled = e.hasFile || e.archived || e.downloading || !episodeVersionReady; input.checked = selectedEpisodes.has(e.id); input.onchange = () => { if (input.checked) selectedEpisodes.add(e.id); else selectedEpisodes.delete(e.id); updateEpisodeButton(); }; label.append(input, node('span', `第 ${e.episodeNumber} 集 · ${e.title || '未命名'}` + (e.archived ? '（云端已有）' : e.hasFile ? '（本地已有）' : e.downloading ? '（下载中）' : ''))); $('episode-list').append(label); });
  updateEpisodeButton();
}
function updateEpisodeButton() { $('download-episodes').disabled = !selectedEpisodes.size || !episodeVersionReady; $('download-episodes').textContent = selectedEpisodes.size ? `下载所选 ${selectedEpisodes.size} 集` : '下载所选集数'; }
$('episode-season').onchange = renderEpisodes;
$('refresh-episodes').onclick = () => { episodeDeadline = Date.now() + 120000; return action($('refresh-episodes'), () => loadEpisodes()); };
$('cancel-episodes').onclick = () => $('episodes-dialog').close();
$('episodes-form').onsubmit = event => { event.preventDefault(); action(event.submitter, async () => { const result = await api('download-episodes', {series_id: episodeSeries, episode_ids: [...selectedEpisodes]}); $('episodes-dialog').close(); notice(result.message); }); };
['resolution', 'codec', 'site', 'free'].forEach(id => $(id).onchange = render);
$('mode').onchange = () => $('season-label').hidden = $('mode').value !== 'season';
$('cancel-follow').onclick = () => $('follow-dialog').close();
$('follow-form').onsubmit=event=>{event.preventDefault();action(event.submitter,async()=>{
  const mode=$('mode').value,id=following,title=versionPreview?.candidates.find(c=>c.id===id)?.title || $('version-resource').textContent;
  const body={id,mode,season:Number($('season').value)};
  if(selectedRelease)Object.assign(body,{release_id:selectedRelease.id,fields:Object.fromEntries(Object.entries(versionInputs).map(([key,input])=>[key,$(input).value.trim()])),confirmed:$('version-confirm').checked,replace:$('replace-version').checked});
  const result=await api('follow',body);
  $('follow-dialog').close();notice(result.message||'版本规则已保存。');
  if(mode==='manual')await openEpisodes({id,title});
});};
let statusBusy=false, qbCredentialsNeeded=null;
$('days').addEventListener('input',()=>{$('days').dataset.edited='1';});
async function status() {
  if(statusBusy)return;statusBusy=true;
  try{await loadStatus();}finally{statusBusy=false;}
}
async function loadStatus() {
  const s = await api('status'); if (!$('days').dataset.edited && document.activeElement !== $('days')) $('days').value = s.settings.retention_days;
  $('openlist-credentials').hidden=!s.credentials.openlist; if (s.credentials.qb !== qbCredentialsNeeded) { $('download-credentials').open=s.credentials.qb; qbCredentialsNeeded=s.credentials.qb; }
  const names = {metadata:'Emby 刮削', uploader:'上传服务', cloud: '阿里云盘', downloader: '下载器', indexers: '搜索站点', television: '追剧连接', retention: '保留时间'};
  const missing = Object.entries(s.checks).filter(([,v]) => !v.complete);
  $('nav-warning').hidden = !missing.length; $('config-warning').hidden = !missing.length;
  $('config-warning-text').textContent = `有 ${missing.length} 项配置未完成或连接异常：${missing.map(([k]) => names[k]).join('、')}。点击设置处理。`;
  document.querySelectorAll('[data-check]').forEach(el => { const check = s.checks[el.dataset.check]; el.classList.toggle('incomplete', !check.complete); el.classList.toggle('complete', check.complete); el.replaceChildren(); if (!check.complete) { const icon = $('nav-warning').cloneNode(true); icon.removeAttribute('id'); icon.hidden = false; el.append(icon); } el.append(node('span', check.message)); });
  $('cleanup-state').textContent = `已清理 ${s.cleanup.done} 个任务，${(s.cleanup.pending||0)+(s.cleanup.deferred||0)} 个等待复核。无法确认云端文件或本地关联的任务会保留。` + ((s.cleanup.reasons||[]).length?' '+s.cleanup.reasons.join('；')+'。':'') + (s.cleanup.last ? ' 上次清理：' + new Date(s.cleanup.last * 1000).toLocaleString() : '');
}
$('config-warning').onclick = () => show('settings');
$('metadata-defaults').onclick = () => action($('metadata-defaults'), async () => {notice((await api('metadata-defaults', {})).message);await status();});
$('cloud-form').onsubmit = event => { event.preventDefault(); action(event.submitter, async () => { const r = await api('configure', {openlist_username:$('openlist-user').value,openlist_password:$('openlist-password').value, refresh_token: $('refresh-token').value, qb_username: $('qb-user').value, qb_password: $('qb-password').value}); $('refresh-token').value = ''; $('openlist-password').value=''; $('qb-password').value = ''; notice(r.message); await status(); }); };
$('retention-form').onsubmit = event => { event.preventDefault(); action(event.submitter, async () => { notice((await api('settings', {retention_days: Number($('days').value)})).message); delete $('days').dataset.edited; await status(); }); };
function chooseVersionForSeries(series){
  show('search');document.querySelector('[name=kind][value=tv]').checked=true;$('query').value=series.title;$('search-form').requestSubmit();
}
async function series(){
  const r=await api('series');$('series-list').replaceChildren();
  if(!r.items.length)$('series-list').append(node('p','还没有追剧。搜索剧集／综艺，选择资源版本后开始追更。','empty','tv'));
  r.items.forEach(s=>{
    const el=row(s.title,[s.original_title,s.year,`已归档 ${s.archived||0} 集`,!s.version?.valid?'规则异常':s.preparing?'同步集数中':s.monitored?'追更中':'已暂停',s.nextAiring?'下次更新 '+new Date(s.nextAiring).toLocaleDateString():'暂无下次播出时间'],'series-row');
    el.firstChild.append(node('p',s.version?.valid?'版本条件：'+versionSummary(s.version.fields):'尚无有效版本规则；请先选择资源版本。',s.version?.valid?'row-description':'warning-text'));
    const change=node('button',s.version?.valid?'更换版本':'选择版本');change.onclick=()=>chooseVersionForSeries(s);
    const select=node('button','选集下载',undefined,'download');select.disabled=!s.version?.valid;select.onclick=()=>action(select,()=>openEpisodes({id:String(s.tvdbId),title:s.title}));
    const scope=node('button','设置追更范围');scope.disabled=!s.version?.valid;scope.onclick=()=>openFollow(s);
    const actions=node('div',undefined,'row-actions');actions.append(select);
    const more=node('details',undefined,'more-actions'), summary=node('summary','更多'), menu=node('div'); more.append(summary,menu);menu.append(scope,change);actions.append(more);
    const pause=node('button',s.monitored?'暂停追更':'恢复追更',undefined,s.monitored?'pause':'play');pause.disabled=!s.monitored&&(!s.version?.valid||s.preparing);pause.onclick=()=>action(pause,async()=>{notice((await api('monitor',{id:s.id,monitored:!s.monitored})).message);await series();});actions.prepend(pause);
    const remove=node('button','删除追剧','quiet');remove.onclick=()=>{if(confirm(`删除《${s.title}》的追剧？停止自动追更并从列表移除；已有文件和正在下载的任务保留。`))return action(remove,async()=>{const r=await api('remove-series',{id:s.id});await series();notice(r.message);$('refresh-series').focus();});};menu.append(remove);
    el.append(actions);$('series-list').append(el);
  });
}
const uploadStates={pending:'等待上传',uploading:'提交上传中',queued:'上传中',uploaded:'校验完成，等待发布',moving:'发布确认中',published:'已发布 · 约 10 分钟后可在影院看到',failed:'上传未完成',cancelled:'已取消，文件保留'};
const qbStates={downloading:'下载中',stalledDL:'等待连接',metaDL:'获取资源信息',queuedDL:'排队下载',stoppedDL:'下载已暂停',pausedDL:'下载已暂停',checkingDL:'下载校验中',error:'下载出错',missingFiles:'源文件缺失'};
const seedStates={uploading:'做种中',stalledUP:'等待做种连接',queuedUP:'排队做种',forcedUP:'做种中',stoppedUP:'做种已暂停',pausedUP:'做种已暂停',checkingUP:'做种文件校验中',error:'下载器出错',missingFiles:'本地文件缺失'};
const episodeTitle=name=>name.match(/S\d{1,2}[ ._-]*E\d{1,4}(?:-E?\d{1,4}|E\d{1,4})*/i)?.[0].toUpperCase()||name;
function uploadRow(u, compact=false, short=false) {
  const name=u.dest.split('/').pop();
  const el=row(short?episodeTitle(name):name,[u.dest.startsWith('/aliyun/TV/')?'剧集／综艺':'电影',uploadStates[u.state]||'处理中',u.state==='queued'?`${Number(u.progress||0).toFixed(1)}%`:'',u.queue_position?`排队第 ${u.queue_position} 个文件`:'',u.error_message],'upload-row');
  el.firstChild.firstChild.title=name;
  el.firstChild.firstChild.hidden=compact;
  if(u.state==='failed'||u.error==='upload_unconfirmed'||u.error==='move_pending'){
    const b=node('button',u.state==='failed'?'重新上传':'重新检查');b.dataset.taskControl=u.id+':retry';
    b.onclick=()=>action(b,async()=>{notice((await api('retry-upload',{id:u.id})).message);await tasks();});el.append(b);
  }
  if(['openlist_code_401','openlist_code_403'].includes(u.error)){const b=node('button','重新连接云盘');b.dataset.taskControl=u.id+':reconnect';b.onclick=()=>show('settings');el.append(b);}
  return el;
}
let tasksAgain=false, taskTarget=null, tasksBusy=false, taskScope='', taskPreview=0, taskSubmitting=false;
const actionErrors={task_control_failed:'下载器尚未确认，请刷新后重试',task_waiting_for_upload:'等待云盘任务结束后继续处理',upload_unconfirmed:'旧上传状态尚未确认，请检查云盘任务',task_waiting_for_import:'等待站点搜索或剧集入库结束',cleanup_waiting_for_stop:'等待下载器停止',cleanup_waiting_for_removal:'等待下载器移除任务',cleanup_waiting_for_files:'等待本地文件删除完成',cleanup_shared_file:'文件与其他任务共用，已保留；请在高级管理核对',task_changed:'资源已变化，请核对新清单后重新确认',delete_scope_required:'旧删除请求缺少确认清单，请重新预览并确认',cleanup_local_unverified:'无法确认剩余文件的归属，已保留',cleanup_episode_changed:'Sonarr 文件与确认记录不符，已暂停删除',delete_all_not_configured:'Emby 维护连接或索引目录未就绪，尚未完成删除',delete_all_unverified:'缺少云端归属校验记录，保留文件等待核对',delete_all_unsafe_index:'索引内容或备份存在变化，保留索引等待核对',delete_all_waiting_cloud:'等待云盘确认原件已移除',delete_all_waiting_scan:'云端和索引已清理，等待 Emby 扫描完成',delete_all_local_changed:'本地文件内容已变化，已保留，等待核对',remote_hash_mismatch:'云端文件已被替换，已停止以免误删新版本',remote_size_mismatch:'云端文件与归档记录不符，已停止以免误删',remote_hash_unavailable:'云盘校验信息不完整，保留文件等待核对'};
function keepRemaining(id){
  const keep=node('button','保留剩余文件并结束','quiet');
  keep.dataset.taskControl=id+':keep';
  keep.onclick=()=>action(keep,async()=>{
    notice((await api('task-action',{id,action:'keep-remaining'})).message);
    await tasks();
  });
  return keep;
}
function taskButtons(target, status, disabled=false) {
  const box=node('div',undefined,'task-actions');
  const resources=target.resources||[target],unfinished=resources.filter(r=>r.progress<1),downloading=unfinished.length>0;
  const archiving=(target.uploads||[]).some(u=>u.state!=='published');
  const cancelled=status?.action==='cancel'||(target.uploads||[]).some(u=>u.state==='cancelled');
  const stopped=value=>['stoppedDL','pausedDL','stoppedUP','pausedUP'].includes(value);
  const paused=downloading&&unfinished.every(r=>stopped(r.state));
  const retryResume=status?.action==='resume'&&!!status?.error;
  const pause=downloading&&!paused&&!retryResume;
  const resume=node('button',retryResume?'重试继续':downloading?(paused?'继续下载':'暂停下载'):'继续归档',undefined,pause?'pause':'play');
  resume.disabled=disabled||['pending','blocked'].includes(status?.state);
  resume.dataset.taskControl=target.id+':resume';
  resume.onclick=()=>action(resume,async()=>{const r=await api('task-action',{id:target.id,action:pause?'pause':'resume'});notice(downloading?r.message:'已继续归档。');await tasks();});
  const cancel=node('button',downloading?'取消下载':'取消归档');cancel.disabled=disabled||['pending','blocked'].includes(status?.state)||status?.action==='cancel';cancel.onclick=()=>openTaskDialog({...target,archiveOnly:!downloading},'cancel');
  cancel.dataset.taskControl=target.id+':cancel';
  const remove=node('button',target.tvdb_id?'删除追剧项目':'删除项目','quiet danger','trash');remove.disabled=status?.state==='pending';remove.onclick=()=>openTaskDialog(target,'delete');
  remove.dataset.taskControl=target.id+':delete';
  if(status?.can_keep)box.append(keepRemaining(status.id||target.id));
  if(!downloading&&resources.length)box.append(node('span',resources.length===1?(seedStates[resources[0].state]||'下载已完成'):'下载已完成','task-seed-state muted'));
  if(downloading)box.append(resume,cancel);
  else if(retryResume||archiving)box.append(retryResume||cancelled?resume:cancel);
  box.append(remove);return box;
}
function openTaskDialog(target, operation){
  taskTarget={...target,operation};
  $('task-dialog-title').textContent=operation==='delete'?(target.tvdb_id?'删除整个追剧项目':'删除项目'):(target.archiveOnly?'取消归档':'取消下载');
  $('task-dialog-name').textContent=target.name;
  $('task-dialog-description').textContent=operation==='delete'?(target.tvdb_id?'停止该剧追更，移除全部相关下载与归档条目。':'移除此资源的下载与归档条目。'):target.archiveOnly?'停止尚未发布的归档，保留本地文件和已发布的云端影片。'+(target.tvdb_id?'该剧也会暂停追更。':''):'终止当前下载和后续上传，保留本地文件；之后可选择继续下载。';
  $('task-delete-options').hidden=operation!=='delete';document.querySelector('[name=delete-files][value=false]').checked=true;
  $('confirm-task').textContent=operation==='delete'?'确认删除':target.archiveOnly?'确认取消归档':'确认取消下载';updateTaskDeleteMode();$('task-dialog-error').hidden=true;$('task-dialog').showModal();
}
function taskRequest(){
  const mode=document.querySelector('[name=delete-files]:checked').value;
  return {id:taskTarget.id,action:taskTarget.operation,delete_files:taskTarget.operation==='delete'&&mode!=='false',
    delete_all:taskTarget.operation==='delete'&&mode==='all'};
}
async function updateTaskDeleteMode(){
  const request=taskRequest(), all=request.delete_all;
  const generation=++taskPreview, confirm=$('confirm-task');
  taskScope='';
  $('task-all-details').hidden=!request.delete_files;
  $('task-cloud-files').hidden=!all;
  $('task-dialog-cloud-note').textContent=all?'将停止做种，删除所选范围的 VPS 文件、云端原件及已归档字幕，清理 Emby 索引并扫描。云端删除无法在此撤销；未选中的项目／资源保留。':'云端已归档影片保留。合集或资源包的操作会影响包内全部文件。';
  if(request.action==='delete')confirm.textContent=all?'确认删除所有':'确认删除';
  confirm.disabled=taskSubmitting||request.delete_files;
  if(!request.delete_files)return;
  $('task-preview-status').textContent='正在核对文件清单…';
  $('task-local-files').replaceChildren();
  $('task-all-files').replaceChildren();
  try {
    const result=await api('task-action',{...request,preview:true});
    if(generation!==taskPreview)return;
    $('task-local-files').replaceChildren(...(result.local.length ? result.local : ['没有本地文件记录']).map(v=>node('li',v)));
    $('task-all-files').replaceChildren(...(result.cloud.length ? result.cloud : ['没有已归档的云端文件']).map(v=>node('li',v)));
    $('task-preview-status').textContent='请核对以下文件。';
    taskScope=result.scope;
    confirm.disabled=taskSubmitting;
  } catch(error) {
    if(generation===taskPreview)$('task-preview-status').textContent=error.message+'。可重新选择删除范围以重试。';
  }
}
document.querySelectorAll('[name=delete-files]').forEach(input=>input.onchange=updateTaskDeleteMode);
$('close-task-dialog').onclick=()=>$('task-dialog').close();
$('task-form').onsubmit=async event=>{
  event.preventDefault();
  const request=taskRequest(), target=taskTarget, confirm=$('confirm-task');
  if(taskSubmitting||request.delete_files&&!taskScope)return;
  if(request.delete_files)request.scope=taskScope;
  taskSubmitting=confirm.disabled=true;
  $('task-dialog-error').hidden=true;
  try {
    const result=await api('task-action',request);
    if(taskTarget===target)$('task-dialog').close();
    notice(target.operation==='cancel'&&target.archiveOnly?'已请求取消尚未发布的归档；文件和已发布的云端影片保留。':result.message);
    await tasks();
  } catch(error) {
    if(taskTarget!==target)return;
    $('task-dialog-error').textContent=error.message;
    $('task-dialog-error').hidden=false;
    if(error.status===409)await updateTaskDeleteMode();
  } finally {
    taskSubmitting=false;
    confirm.disabled=taskRequest().delete_files&&!taskScope;
  }
};
async function tasks() {
  if(tasksBusy){tasksAgain=true;return;}tasksBusy=true;tasksAgain=false;
  const list=$('task-list'), refresh=$('task-refresh-status');
  try {
    const r=await api('tasks');
    const expanded=new Set([...list.querySelectorAll('details[open]')].map(e=>e.dataset.key));
    const control=document.activeElement?.dataset.taskControl;
    const detailKey=document.activeElement?.matches('summary')&&document.activeElement.parentElement.dataset.key;
    list.replaceChildren();
    if(r.discovery_issue)list.append(node('p',r.discovery_issue.message+' '+r.discovery_issue.dest,'warning-text'));
    if(!r.items.length)$('task-list').append(node('p','当前没有下载或归档任务。','empty','download'));
    r.items.forEach(g=>{
      const completed=g.uploads.filter(u=>!u.parent_id&&u.state==='published').length;
      const downloading=g.resources.filter(t=>t.progress<1), total=g.resources.reduce((n,t)=>n+(t.size||0),0);
      const progress=total?g.resources.reduce((n,t)=>n+(t.size||0)*t.progress,0)/total:g.resources.length?g.resources.reduce((n,t)=>n+t.progress,0)/g.resources.length:1;
      const progressKnown=g.resources.length||g.uploads.length;
      const multiple=g.file_count>1;
      const allStopped=downloading.length&&downloading.every(t=>['stoppedDL','pausedDL','stoppedUP','pausedUP'].includes(t.state));
      const allPublished=g.uploads.length&&g.uploads.every(u=>u.state==='published');
      const state=g.action?.state==='pending'?(g.action.action==='delete'?'删除处理中':'取消处理中'):g.action?.action==='cancel'&&!allPublished?'已取消':g.action?.action==='pause'&&allStopped&&downloading.length?'下载已暂停':downloading.length?'下载中':g.uploads.some(u=>!['published','cancelled'].includes(u.state))?'归档中':completed?'已发布':'等待导入';
      const el=row(g.name,[g.tvdb_id?'追剧项目':'独立下载',state,g.file_count+' 个文件',completed?'已发布 '+completed+' 个视频':'',progressKnown?`下载 ${(progress*100).toFixed(1)}%`:''],'task-row');
      el.dataset.id=g.id;
      if(progressKnown){const bar=node('progress',undefined,'task-download-progress');bar.max=1;bar.value=progress;bar.setAttribute('aria-label','当前资源下载进度');el.firstChild.append(bar);}
      if(Object.values(g.resource_actions||{}).some(a=>a.state==='pending'))el.firstChild.append(node('p','部分资源正在处理，展开查看进度。','muted'));
      for(const status of Object.values(g.resource_actions||{})){
        if(status.can_keep&&(g.resources.length<=1||!g.resources.some(t=>'torrent:'+t.hash===status.id))){
          el.firstChild.append(node('p',actionErrors[status.error]||'资源删除暂未完成。','warning-text'),keepRemaining(status.id));
        }
      }
      if(g.action?.error)el.firstChild.append(node('p',actionErrors[g.action.error]||'暂未完成，保留数据并自动重试；可在高级管理检查。','warning-text'));
      el.append(taskButtons(g,g.action,!g.resources.length&&!g.uploads.length));
      const detail=node(multiple?'details':'div',undefined,multiple?'task-files':'task-inline');
      if(multiple){detail.dataset.key=g.id;detail.open=expanded.has(g.id);detail.append(node('summary',`查看文件（${g.file_count}）`));}
      const linked=new Set();
      g.resources.forEach(t=>{
        const resource=node('section',undefined,'task-resource');
        const resourceAction=g.resource_actions?.['torrent:'+t.hash];
        if(resourceAction?.error&&resourceAction.state!=='pending')resource.append(node('p',actionErrors[resourceAction.error]||'操作尚未确认，请刷新后重试。','warning-text'));
        if(resourceAction?.state==='pending')resource.append(node('p',(resourceAction.action==='delete'?'删除处理中':'取消处理中')+' · '+(actionErrors[resourceAction.error]||'正在确认任务状态'),'warning-text'));
        t.files.filter(f=>f.priority).forEach(f=>{
          const job=t.uploads.find(j=>j.id===f.job_id);
          if(job){resource.append(uploadRow(job,!multiple&&!g.tvdb_id,!!g.tvdb_id));linked.add(job.id);}
          else {const fileRow=row(g.tvdb_id?episodeTitle(f.name):f.name,[t.progress<1?(qbStates[t.state]||'下载中'):'等待导入或上传',`${(f.progress*100).toFixed(1)}%`,size(f.size)],'upload-row');fileRow.firstChild.firstChild.title=f.name;fileRow.firstChild.firstChild.hidden=!multiple&&!g.tvdb_id;resource.append(fileRow);}
        });
        t.uploads.filter(j=>!linked.has(j.id)).forEach(j=>{resource.append(uploadRow(j,!multiple&&!g.tvdb_id,!!g.tvdb_id));linked.add(j.id);});
        if(g.resources.length>1){
          const source=node('details',undefined,'task-source');source.dataset.key=g.id+':'+t.hash;source.open=expanded.has(source.dataset.key);
          source.append(node('summary','资源详情与操作'),node('p',t.name,'row-description'));
          t.files.filter(f=>f.priority).forEach(f=>source.append(node('p',f.name,'row-description')));
          source.append(taskButtons({id:'torrent:'+t.hash,name:t.name,state:t.state,progress:t.progress,uploads:t.uploads},resourceAction||(g.action?.state==='pending'?g.action:null)));
          resource.append(source);
        }
        detail.append(resource);
      });
      g.uploads.filter(j=>!linked.has(j.id)).forEach(j=>detail.append(uploadRow(j,!multiple&&!g.tvdb_id)));
      el.firstChild.append(detail);$('task-list').append(el);
    });
    if(control)list.querySelector(`[data-task-control="${CSS.escape(control)}"]`)?.focus({preventScroll:true});
    else if(detailKey)list.querySelector(`details[data-key="${CSS.escape(detailKey)}"] > summary`)?.focus({preventScroll:true});
    refresh.textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  } catch(error) {
    refresh.textContent=list.childElementCount?'刷新失败，仍显示上次结果。':'暂时无法读取任务，请刷新重试。';
    throw error;
  } finally {tasksBusy=false;if(tasksAgain){tasksAgain=false;tasks().catch(()=>{});}}
}
$('watch').onclick=()=>{
  const address=['127.0.0.1','localhost','[::1]'].includes(location.hostname)?location.protocol+'//'+location.hostname+':8095':location.origin;
  const target=window.open('about:blank','cinema-watch');
  if(!target){notice('浏览器阻止了看片窗口，请允许本站弹出窗口后重试。',true);return;}
  action($('watch'),async()=>{
    const auth=api('watch-session',{});
    try {
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>finish(new Error('看片页面连接超时，请稍后重试。')),25000);
        function finish(error){clearTimeout(timer);window.removeEventListener('message',receive);error?reject(error):resolve();}
        async function receive(event){
          if(event.source!==target||event.origin!==address)return;
          if(event.data?.type==='cinema-watch-ready'){
            try{target.postMessage({type:'cinema-watch-login',auth:await auth},address);}catch(e){finish(e);}
          }else if(event.data?.type==='cinema-watch-complete'){finish();target.focus();}
          else if(event.data?.type==='cinema-watch-error')finish(new Error('无法保存看片会话，请允许本站使用本地存储后重试。'));
        }
        window.addEventListener('message',receive);
        target.location=address+'/web/index.html?cinemaHandoff='+Date.now()+'#!/login?cinemaHandoff=1';
        auth.catch(finish);
      });
    }catch(e){notice(e.message,true);}
  });
};
$('refresh-series').onclick = () => action($('refresh-series'), series);
$('refresh-tasks').onclick = () => action($('refresh-tasks'), tasks);
let playbacksBusy=false;
async function playbacks(){
  if(playbacksBusy)return;playbacksBusy=true;
  try{
    const result=await api('playbacks');$('playback-list').replaceChildren();
    if(!result.items.length)$('playback-list').append(node('p','暂无播放记录。开始播放后会自动记录；升级前的播放和绕过影院入口的播放不在此列。','empty','history'));
    const routes={cloud:'网盘直连跳转',vps:'VPS 中转',unknown:'直连尚未确认'},states={playing:'播放中',paused:'已暂停',stopped:'已停止',inactive:'暂无近期上报',requested:'已发起请求',failed:'请求失败'};
    result.items.forEach(item=>{
      const seconds=Math.floor(item.position_seconds),position=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
      const el=row(item.title,[new Date(item.started_at*1000).toLocaleString('zh-CN'),routes[item.route],states[item.state],item.client,item.device],'playback-row');
      el.classList.toggle('playback-alert',item.route==='vps'||!!item.error_status);
      el.firstChild.append(node('p',item.route==='cloud'?`HTTP ${item.http_status} → ${item.host}。视频请求已交给网盘。`:item.route==='vps'?'已观测到影院服务器返回视频数据，请检查播放链路。':item.host?`HTTP ${item.http_status} → ${item.host}，尚未确认是网盘直连。`:'尚未观测到网盘视频跳转响应。','row-description'));
      el.firstChild.append(node('p',item.has_progress?`客户端已报告播放进度 ${position}`:'尚未收到有效播放进度；跳转成功不等于客户端已成功播放。','muted'));
      if(item.error_status)el.firstChild.append(node('p',`期间出现 HTTP ${item.error_status} 错误；请检查登录、云盘连接或更换云端播放版本。`,'warning-text'));
      $('playback-list').append(el);
    });
    $('playback-refresh-status').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  } catch(error) {
    $('playback-refresh-status').textContent=$('playback-list').childElementCount?'刷新失败，仍显示上次结果。':'暂时无法读取播放记录，请刷新重试。';
    throw error;
  } finally {playbacksBusy=false;}
}
$('refresh-playbacks').onclick=()=>action($('refresh-playbacks'),playbacks);
async function metrics() {
  if (metricsBusy || !csrf) return;
  metricsBusy = true;
  try {
    const r = await api('metrics');
    $('metrics-time').textContent = '刚刚更新 · 每 10 秒刷新';
    for (const name of ['disk', 'memory']) {
      const value = r[name], meter = $(name + '-meter');
      $(name + '-label').textContent = value ? `${size(value.used)} / ${size(value.total)}` : '暂不可用';
      meter.hidden = !value;
      if (value) {
        meter.value = 100 * value.used / value.total;
        meter.classList.toggle('high', name === 'disk' && meter.value > 85);
      }
    }
    $('cpu-label').textContent = r.cpu === null ? '采样中' : `${r.cpu.toFixed(1)}%`;
    $('cpu-meter').hidden = r.cpu === null;
    if (r.cpu !== null) $('cpu-meter').value = r.cpu;
  } catch {
    $('metrics-time').textContent = '状态暂不可用，稍后自动重试';
    for (const name of ['disk', 'memory', 'cpu']) {
      $(name + '-label').textContent = '暂不可用';
      $(name + '-meter').hidden = true;
    }
  } finally {
    metricsBusy = false;
  }
}
setInterval(() => { if (!document.hidden && csrf) { metrics(); if (currentView === 'tasks') tasks().catch(() => {}); if(currentView==='playbacks')playbacks().catch(()=>{}); } }, 10000);
window.addEventListener('focus', () => { if (csrf && currentView === 'settings') status().catch(e => notice(e.message, true)); });
(async () => {
  try {
    const r=await api('session');
    csrf=r.csrf;
    signedIn(true);
    await status();
    await metrics();
  } catch(error) {
    if(!csrf)signedIn(false);
    if(error.status!==401)notice(error.message,true);
  }
})();

$('select-season').onclick=()=>{const values=episodeItems.filter(e=>e.seasonNumber===Number($('episode-season').value)&&!e.hasFile&&!e.archived&&!e.downloading&&episodeVersionReady);const clear=values.length&&values.every(e=>selectedEpisodes.has(e.id));values.forEach(e=>clear?selectedEpisodes.delete(e.id):selectedEpisodes.add(e.id));renderEpisodes();};
