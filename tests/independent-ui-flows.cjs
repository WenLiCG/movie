// Independent browser checks for the imported portal fixes.
// Local synthetic API only. Run: NODE_PATH=<playwright dir> node tests/independent-ui-flows.cjs
const assert = require('node:assert/strict');
const {createServer} = require('node:http');
const {readFile} = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const checks = Object.fromEntries(['cloud','downloader','indexers','television','retention','uploader','metadata'].map(k => [k, {complete: true, message: '配置正常。'}]));
  let loggedIn = false;
  let sessionMode = 'html-503';
  let retentionDays = 7;
  let searchFails = false;
  let holdNextTaskPoll;
  let cloudMount;
  const mountRequests=[];
  const mounts=[{path:'/aliyun',provider:'AliyundriveOpen',eligible:true},
    {path:'/existing',provider:'AliyundriveOpen',eligible:true},{path:'/local',provider:'Local',eligible:false}];
  const state = {
    session: {csrf: 'synthetic-csrf'},
    status: () => ({cloud_mount:cloudMount, settings: {retention_days: retentionDays}, credentials: {openlist: false, qb: false}, checks, cleanup: {done: 0, pending: 0, deferred: 0, reasons: []}}),
    metrics: {disk: {used: 1024, total: 2048}, memory: {used: 1024, total: 2048}, cpu: 1},
    recommendations: {items: []},
    search: {items: [{id: 'tv-1', title: '示例剧.S01.1080p.WEB-DL.H.264-HHWEB', size: 1024, resolution: '1080p', codec: 'H.264', seeders: 3, site: 'local', free: true}, {id: 'tv-2', title: '示例剧.S01.2160p.WEB-DL.H.265-HHWEB', size: 4096, resolution: '4K', codec: 'H.265', seeders: 2, site: 'local', free: false}], warnings: []},
    series: {items: [{id: 1, tvdbId: 42, title: '示例剧', year: 2026, monitored: true, archived: 1, version: {valid: true, fields: {group: 'HHWEB', resolution: '1080p', codec: 'H.264', source: 'WEB-DL', platform: '', audio: '', subtitles: ''}}, seasons: [{seasonNumber: 0, name: 'Specials'}, {seasonNumber: 1}, {seasonNumber: 2}]}]},
    tasks: {items: [{id: 'movie:1', name: '示例电影', file_count: 1, uploads: [{id: 'job1', dest: '/aliyun/Movies/A/A.mkv', state: 'failed', error: 'upload_unconfirmed'}], resources: [{hash: 'abc', name: '示例电影', size: 2048, dlspeed: 512, progress: 0.5, state: 'downloading', files: [], uploads: []}]}]},
    playbacks: {items: []},
    'preview-version': {available: true, title: '示例剧.S01.1080p.WEB-DL.H.264-HHWEB', fields: {group: 'HHWEB', resolution: '1080p', codec: 'H.264', source: 'WEB-DL', platform: '', audio: '', subtitles: ''}, episodes: 'S01', missing: ['platform', 'audio', 'subtitles'], candidates: [{id: '42', title: '示例剧', year: 2026, seasons: [1, 2], version: {group: 'HHWEB', resolution: '1080p', codec: 'H.264', source: 'WEB-DL', platform: '', audio: '', subtitles: ''}}, {id: '43', title: '示例剧特别篇', year: 2026, seasons: [1], version: null}]},
    episodes: {series_id: 1, version_ready: true, items: [{id: 101, seasonNumber: 1, episodeNumber: 1, title: '第一集', hasFile: false, archived: true, downloading: false}, {id: 201, seasonNumber: 2, episodeNumber: 1, title: '第二季首集', hasFile: false, archived: false, downloading: false}]},
  };
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/discover/api/')) {
      const name = url.pathname.split('/').pop();
      response.setHeader('Content-Type', 'application/json');
      if (name === 'tasks' && holdNextTaskPoll) {
        holdNextTaskPoll(response);
        holdNextTaskPoll = null;
        return;
      }
      if (name === 'session' && sessionMode === 'html-503') {
        response.writeHead(503, {'Content-Type': 'text/html'});
        response.end('<html>gateway down</html>');
        return;
      }
      if (name === 'session' && !loggedIn) {response.writeHead(401); response.end('{}'); return;}
      if (name === 'login') {loggedIn = true; response.end(JSON.stringify(state.session)); return;}
      if (name === 'logout') {loggedIn = false; response.end('{}'); return;}
      if (['cloud-mounts','cloud-mount','configure'].includes(name)) {
        let raw='';
        for await (const chunk of request) raw+=chunk;
        const body=JSON.parse(raw);
        mountRequests.push({name,body});
        if (name==='cloud-mounts') response.end(JSON.stringify({mounts}));
        else if (body.expected_mount!==(cloudMount || '/aliyun')) {
          response.writeHead(409);
          response.end(JSON.stringify({error:'影院当前挂载已更改，请刷新设置后重试。'}));
        } else if (name==='configure') response.end(JSON.stringify({message:'接线完成'}));
        else {
          cloudMount=body.mount_path;
          response.end(JSON.stringify({path:cloudMount,provider:'AliyundriveOpen',message:'已启用 '+cloudMount}));
        }
        return;
      }
      if (name === 'search' && searchFails) {response.writeHead(500); response.end('not-json'); return;}
      if (name === 'settings') {retentionDays = 14; response.end(JSON.stringify({message: '已保存'})); return;}
      const payload = state[name];
      response.end(JSON.stringify(typeof payload === 'function' ? payload() : (payload || {})));
      return;
    }
    const file = url.pathname === '/discover/' ? 'index.html' : url.pathname.split('/').pop();
    if (!['index.html', 'style.css', 'app.js', 'paint.js', 'theme.js'].includes(file)) {response.writeHead(404); response.end(); return;}
    response.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    response.end(await readFile(path.join(__dirname, '../portal', file)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    const holdNext = endpoint => new Promise((resolve, reject) => {
      page.route('**/discover/api/' + endpoint, resolve, {times:1}).catch(reject);
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/discover/');
    await page.locator('#login').waitFor({state: 'visible'});
    assert.match(await page.locator('#notice').innerText(), /服务暂时不可用/);
    assert.equal(await page.locator('#workspace').isHidden(), true);

    sessionMode = 'signed-out';
    // Wrong-password login surfaces inline right inside the login form.
    await page.route('**/discover/api/login', route => route.fulfill({status: 400, json: {error: '登录失败：Emby 用户名或密码不正确。'}}));
    await page.locator('#username').fill('demo');
    await page.locator('#password').fill('wrong');
    await page.getByRole('button', {name: '登录', exact: true}).click();
    await page.locator('#login-error').waitFor({state: 'visible'});
    assert.match(await page.locator('#login-error').innerText(), /用户名或密码不正确/);
    await page.unroute('**/discover/api/login');
    await page.locator('#password').fill('synthetic-only');
    assert.equal(await page.locator('#login-error').isHidden(), true);
    await page.getByRole('button', {name: '登录', exact: true}).click();
    await page.locator('#workspace').waitFor({state: 'visible'});

    await page.locator('[name=kind][value=tv]').check();
    await page.locator('#query').fill('示例剧');
    await page.locator('#search-form button').click();
    await page.locator('.result-row').first().waitFor();
    assert.equal(await page.locator('#results .result-row').count(), 2);
    assert(await page.locator('.row-meta').first().getByText('1 KiB', {exact: true}).isVisible());
    const selectBox = await page.locator('#resolution').boundingBox();
    assert(selectBox.height <= 56, 'filter select keeps single-line height instead of wrapping caret: ' + selectBox.height);
    // Filtering shows filtered/total count and one-click reset.
    await page.locator('#codec').selectOption('AV1');
    assert.match(await page.locator('#result-count').innerText(), /显示 0 \/ 共 2 个结果/);
    await page.getByRole('button', {name: /显示全部资源/}).click();
    assert.equal(await page.locator('#results .result-row').count(), 2);

    // Follow dialog keeps user edits across candidate switches and gates replace confirmation on real changes.
    await page.locator('.result-row').first().getByRole('button', {name: '按此版本追更', exact: true}).click();
    await page.locator('#version-content').waitFor({state: 'visible'});
    await page.locator('#series-match').selectOption('42');
    assert.equal(await page.locator('#replace-version-row').isHidden(), true);
    await page.locator('#version-extra summary').click();
    await page.locator('#version-subtitles').fill('CHS');
    assert.equal(await page.locator('#replace-version-row').isHidden(), false);
    await page.locator('#series-match').selectOption('43');
    assert.equal(await page.locator('#version-subtitles').inputValue(), 'CHS');
    // A slow save for the first release must not close the second release's dialog or open the old episode picker.
    const savedFollow = holdNext('follow');
    await page.locator('#version-confirm').check();
    await page.locator('#mode').selectOption('manual');
    await page.locator('#submit-version').click();
    const oldFollow = await savedFollow;
    assert(await page.locator('#series-match').isDisabled());
    await page.locator('#follow-dialog').getByRole('button', {name:'关闭', exact:true}).click();
    await page.locator('.result-row').nth(1).getByRole('button', {name:'按此版本追更'}).click();
    await page.locator('#version-content').waitFor();
    await oldFollow.fulfill({json:{message:'旧剧已保存'}});
    await page.waitForLoadState('networkidle');
    assert(await page.locator('#follow-dialog').isVisible());
    assert.match(await page.locator('#version-resource').innerText(), /2160p/);
    assert(await page.locator('#episodes-dialog').isHidden());
    await page.locator('#cancel-follow').click();
    const stalePreview = holdNext('preview-version');
    await page.locator('.result-row').first().getByRole('button', {name:'按此版本追更'}).click();
    const oldPreview = await stalePreview;
    await page.locator('#cancel-follow').click();
    await page.locator('.result-row').nth(1).getByRole('button', {name:'按此版本追更'}).click();
    await page.locator('#version-content').waitFor();
    await oldPreview.fulfill({status:500,json:{error:'旧预览失败'}});
    await page.waitForLoadState('networkidle');
    assert(await page.locator('#follow-notice').isHidden(), 'an obsolete preview error must not appear in this release');
    await page.locator('#cancel-follow').click();

    let downloadRoute;
    await page.route('**/discover/api/download-release', route => { downloadRoute = route; });
    await page.locator('.result-row').first().getByRole('button', {name: '下载此资源', exact: true}).click();
    await page.locator('#free').check();
    assert.match(await page.locator('#result-count').innerText(), /显示 1 \/ 共 2 个结果/);
    const pending = page.getByRole('button', {name: '正在提交…', exact: true});
    assert(await pending.isDisabled(), 'filter changes must preserve a pending submission');
    await downloadRoute.fulfill({json: {message: '已提交此资源'}});
    await page.getByRole('button', {name: '已提交', exact: true}).waitFor();
    assert(await page.locator('#notice-action').isVisible(), 'download notice offers direct jump to tasks');
    await page.locator('#free').uncheck();
    assert(await page.getByRole('button', {name: '已提交', exact: true}).isDisabled());
    await page.locator('#dismiss-notice').click();

    await page.locator('#query').fill('');
    assert(await page.locator('#recommendations').isVisible());
    assert.equal(await page.locator('.result-row').count(), 0);
    await page.locator('#query').fill('示例剧');
    searchFails = true;
    await page.locator('#search-form button').click();
    await page.locator('#notice').waitFor({state: 'visible'});
    assert.match(await page.locator('#notice').innerText(), /服务暂时不可用|无法连接影院服务/);
    searchFails = false;

    await page.locator('nav [data-view=settings]').click();
    assert.equal(await page.locator('#active-mount').innerText(),'/aliyun');
    await page.locator('#openlist-credentials summary').click();
    await page.locator('#openlist-user').fill('fixture-admin');
    await page.locator('#openlist-password').fill('fixture-secret');
    await page.locator('#mount-token').fill('fixture-new-token');
    await page.locator('#list-mounts').click();
    await page.locator('#mount-choice').waitFor();
    assert.deepEqual(mountRequests.at(-1),{name:'cloud-mounts',body:{openlist_username:'fixture-admin',openlist_password:'fixture-secret'}});
    await page.locator('#mount-choice').selectOption('/local');
    assert(await page.locator('#save-mount').isDisabled());
    assert.match(await page.locator('#mount-hint').innerText(),/已占用/);
    await page.locator('#mount-choice').selectOption('/existing');
    assert(await page.locator('#mount-token').isHidden());
    await page.getByRole('button',{name:'核验并启用挂载'}).click();
    await page.waitForFunction(()=>document.getElementById('active-mount').textContent==='/existing');
    assert.equal(mountRequests.at(-1).name,'cloud-mount');
    assert.equal(mountRequests.at(-1).body.mount_path,'/existing');
    assert.equal(mountRequests.at(-1).body.expected_mount,'/aliyun');
    assert(!('refresh_token' in mountRequests.at(-1).body));
    await page.locator('#mount-choice').selectOption('');
    await page.locator('#mount-path').fill('/new-cinema');
    await page.locator('#mount-token').fill('fixture-new-token');
    cloudMount='/other-tab';
    await page.getByRole('button',{name:'新建并启用挂载'}).click();
    await page.getByText('影院当前挂载已更改，请刷新设置后重试。',{exact:true}).waitFor();
    await page.waitForFunction(()=>document.getElementById('active-mount').textContent==='/other-tab');
    assert.equal(mountRequests.at(-1).body.expected_mount,'/existing');
    await page.getByRole('button',{name:'新建并启用挂载'}).click();
    await page.waitForFunction(()=>document.getElementById('active-mount').textContent==='/new-cinema');
    assert.deepEqual(mountRequests.at(-1),{name:'cloud-mount',body:{openlist_username:'fixture-admin',openlist_password:'fixture-secret',expected_mount:'/other-tab',mount_path:'/new-cinema',refresh_token:'fixture-new-token'}});
    assert(!mountRequests.some(r=>r.name==='configure'));
    await page.locator('#mount-path').fill('/another');
    await page.locator('#mount-token').fill('fixture-draft');
    await page.locator('#connect-services').click();
    await page.getByText('接线完成',{exact:true}).waitFor();
    assert.equal(mountRequests.at(-1).body.expected_mount,'/new-cinema');
    assert(!('refresh_token' in mountRequests.at(-1).body));
    assert(!JSON.stringify(mountRequests.at(-1)).includes('fixture-draft'));
    await page.locator('#days').fill('123');
    const delayedPoll = new Promise(resolve => { holdNextTaskPoll = resolve; });
    await page.evaluate(() => { window.priorAccountPoll = tasks().catch(() => {}); });
    const oldReply = await delayedPoll;
    await page.locator('#logout').click();
    await page.locator('#login').waitFor({state: 'visible'});
    assert.equal(await page.locator('#results').innerText(), '');
    assert.equal(await page.locator('#workspace').isHidden(), true);
    assert.equal(await page.locator('#task-list').innerText(), '');
    assert.equal(await page.locator('#mount-token').inputValue(), '');
    assert.equal(await page.locator('#mount-choice option').count(),0);
    assert.equal(await page.locator('#query').inputValue(), '');

    await page.locator('#username').fill('demo');
    await page.locator('#password').fill('synthetic-only');
    await page.getByRole('button', {name: '登录', exact: true}).click();
    await page.locator('#workspace').waitFor({state: 'visible'});
    // A delayed 401 from the previous account must not sign out the current one.
    oldReply.writeHead(401);
    oldReply.end('{}');
    await page.evaluate(() => window.priorAccountPoll);
    assert(await page.locator('#workspace').isVisible());
    await page.locator('nav [data-view=settings]').click();
    await page.locator('#days').waitFor();
    assert.equal(await page.locator('#days').inputValue(), '7');
    await page.locator('#days').fill('21');
    retentionDays = 3;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.waitForTimeout(200);
    assert.equal(await page.locator('#days').inputValue(), '21');
    await page.locator('#days').blur();
    await page.evaluate(() => {delete document.getElementById('days').dataset.edited;});
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.waitForFunction(() => document.getElementById('days').value === '3');

    await page.route('**/discover/api/metrics', route => route.fulfill({status: 503, json: {}}));
    await page.evaluate(() => metrics());
    assert.equal(await page.locator('#disk-label').innerText(), '暂不可用');
    assert(await page.locator('#disk-meter').isHidden());
    await page.unroute('**/discover/api/metrics');
    await page.evaluate(() => metrics());
    assert(await page.locator('#disk-meter').isVisible());

    await page.locator('nav [data-view=tasks]').click();
    await page.locator('.task-row').first().waitFor();
    assert(await page.locator('.task-row .row-meta').first().getByText('2 KiB', {exact: true}).isVisible());
    assert(await page.locator('.task-row .row-meta').first().getByText('512 B/s', {exact: true}).isVisible());
    const retry = page.getByRole('button', {name: '重新上传', exact: true});
    await retry.focus();
    await page.evaluate(() => tasks());
    await page.waitForFunction(() => document.activeElement?.dataset.taskControl === 'job1:retry');
    Object.assign(state.tasks.items[0].uploads[0], {state: 'queued', progress: 100, terminal_proven: true, error: 'remote_hash_mismatch'});
    await page.locator('#refresh-tasks').click();
    const check = page.getByRole('button', {name: '重新检查', exact: true});
    await check.waitFor();
    assert.equal(await page.getByText('等待归档复核', {exact: true}).count(), 2);
    assert.equal(await page.locator('.upload-row').getByText('100.0%', {exact: true}).count(), 0);
    const recheck = page.waitForRequest(req => req.url().endsWith('/api/retry-upload'));
    await check.click();
    assert.deepEqual((await recheck).postDataJSON(), {id: 'job1'});

    // A series with one torrent still has two independent operation scopes.
    const single = {...state.tasks.items[0], id: 'series:42', tvdb_id: 42};
    state.tasks.items = [single];
    await page.setViewportSize({width: 390, height: 844});
    await page.locator('#refresh-tasks').click();
    const project = page.locator('[data-id="series:42"]');
    await project.getByRole('button', {name: '删除追剧项目'}).waitFor();
    await project.getByText('资源详情与操作', {exact: true}).click();
    await project.getByRole('button', {name: '删除资源', exact: true}).click();
    assert.equal(await page.evaluate(() => taskRequest().id), 'torrent:abc');
    assert.equal(await page.locator('#task-dialog-title').innerText(), '删除资源');
    await page.locator('#close-task-dialog').click();
    single.resource_actions = {'torrent:abc': {id: 'torrent:abc', action: 'delete', state: 'blocked', error: 'task_changed', can_keep: true}};
    await page.locator('#refresh-tasks').click();
    await project.getByText('保留剩余文件并结束', {exact: true}).waitFor();
    assert(await project.getByRole('button', {name: '删除追剧项目'}).isDisabled());
    assert.equal(await project.getByText('保留剩余文件并结束', {exact: true}).count(), 1);
    // qB may no longer return the torrent that owns a blocked child operation.
    single.resources = [];
    single.resource_actions['torrent:abc'].action = 'cancel';
    await page.locator('#refresh-tasks').click();
    await project.locator('.task-source').waitFor({state: 'detached'});
    const finish = page.waitForRequest(req => req.url().endsWith('/api/task-action'));
    await project.getByRole('button', {name: '保留剩余文件并结束'}).click();
    assert.deepEqual((await finish).postDataJSON(), {id: 'torrent:abc', action: 'keep-remaining'});
    await page.setViewportSize({width: 1280, height: 900});

    await page.locator('nav [data-view=playbacks]').click();
    await page.getByText('暂无播放记录。', {exact: false}).waitFor();
    state.playbacks.items = [{title: '长片测试', started_at: 1793700000, position_seconds: 5025, route: 'cloud', state: 'playing', http_status: 307, host: 'dl1.aliyundrive.cloud', has_progress: true}];
    await page.locator('#refresh-playbacks').click();
    await page.getByText('客户端已报告播放进度 1:23:45', {exact: true}).waitFor();
    await page.route('**/discover/api/playbacks', route => route.fulfill({status: 503, json: {error: '稍后再试'}}));
    await page.locator('#refresh-playbacks').click();
    await page.getByText('刷新失败，仍显示上次结果。', {exact: true}).waitFor();
    await page.unroute('**/discover/api/playbacks');

    // Extra torrent files (NFO/poster) show 不自动归档 and uploads with future retry_at show 下次重试.
    state.tasks.items = [{
      id: 'torrent:m1', name: '示例电影多文件', file_count: 2,
      uploads: [{id: 'jm1', dest: '/aliyun/Movies/M/M.mkv', state: 'queued', progress: 25, retry_at: Math.floor(Date.now() / 1000) + 300, error: 'network_or_protocol', error_message: '传输连接中断，等待重试；不代表云盘授权失效。'}],
      resources: [{hash: 'm1', name: '示例电影多文件', size: 2048, progress: 1, state: 'stalledUP',
        uploads: [{id: 'jm1', dest: '/aliyun/Movies/M/M.mkv', state: 'queued', progress: 25, retry_at: Math.floor(Date.now() / 1000) + 300, error: 'network_or_protocol', error_message: '传输连接中断，等待重试；不代表云盘授权失效。'}],
        files: [{index: 0, name: 'M.mkv', size: 2000, progress: 1, priority: 1, job_id: 'jm1', role: 'video'},
                {index: 1, name: 'M.nfo', size: 48, progress: 1, priority: 1, role: 'extra'}]}],
    }];
    await page.locator('nav [data-view=tasks]').click();
    await page.locator('[data-id="torrent:m1"] summary').first().click();
    await page.getByText('不自动归档', {exact: true}).waitFor();
    await page.getByText(/下次重试 \d{2}:\d{2}:\d{2}/).waitFor();

    await page.locator('nav [data-view=series]').click();
    await page.getByRole('button', {name: '选集下载', exact: true}).click();
    await page.locator('#episode-list label').first().waitFor();
    assert.equal(await page.locator('#episode-season').inputValue(), '2', 'defaults to first season with selectable episodes');
    const selectBtn = await page.locator('#select-season').boundingBox();
    const refreshBtn = await page.locator('#refresh-episodes').boundingBox();
    assert(refreshBtn.x - (selectBtn.x + selectBtn.width) >= 6, 'episode toolbar buttons have clear spacing');
    await page.locator('#select-season').click();
    assert.equal(await page.locator('#select-season').innerText(), '取消本季全选');
    const sentEpisodes = holdNext('download-episodes');
    await page.locator('#download-episodes').click();
    const oldEpisodes = await sentEpisodes;
    assert(await page.locator('#select-season').isDisabled());
    await page.keyboard.press('Escape');
    await page.getByRole('button', {name: '选集下载', exact: true}).click();
    await page.locator('#episode-list label').first().waitFor();
    await oldEpisodes.fulfill({json:{message:'之前选择的集数已提交'}});
    await page.waitForLoadState('networkidle');
    assert(await page.locator('#episodes-dialog').isVisible());
    assert(await page.locator('#download-episodes').isDisabled(), 'the new selection is empty');
    const staleRefresh = holdNext('episodes');
    await page.locator('#refresh-episodes').click();
    const oldRefresh = await staleRefresh;
    await page.keyboard.press('Escape');
    await page.getByRole('button', {name:'选集下载',exact:true}).click();
    await page.locator('#episode-list label').first().waitFor();
    await oldRefresh.fulfill({status:500,json:{error:'旧选集刷新失败'}});
    await page.waitForLoadState('networkidle');
    assert(await page.locator('#episode-notice').isHidden(), 'an obsolete refresh error must not replace the new episode state');
    await page.locator('#cancel-episodes').click();

    await page.setViewportSize({width: 320, height: 800});
    await page.getByText('更多', {exact: true}).click();
    const moreBox = await page.locator('.more-actions > div').boundingBox();
    assert(moreBox.x >= 0 && moreBox.x + moreBox.width <= 320, 'more-actions menu stays within 320px viewport');
    await page.setViewportSize({width: 1280, height: 900});
    await page.getByRole('button', {name: '设置追更范围', exact: true}).click();
    assert.equal(await page.locator('.more-actions').evaluate(el => el.open), false);
    await page.locator('#follow-dialog').waitFor({state: 'visible'});
    const seasons = await page.locator('#season option').evaluateAll(options => options.map(option => option.value));
    assert.deepEqual(seasons, ['1', '2']);
    // Saving refreshes 我的追剧 at once; a series without a chosen rule offers 选择版本 directly.
    state.series.items.push({id: 2, tvdbId: 43, title: '待选剧', year: 2026, monitored: false, archived: 0, version: {valid: false, reason: 'missing'}, seasons: [{seasonNumber: 1}]});
    const saved = page.waitForRequest(req => req.url().endsWith('/api/follow'));
    await page.locator('#submit-version').click();
    assert.deepEqual((await saved).postDataJSON(), {id: '42', mode: 'all', season: 1});
    const unchosen = page.locator('.series-row').filter({hasText: '待选剧'});
    await unchosen.waitFor();
    assert.equal(await unchosen.locator('.status-label').innerText(), '待选版本');
    const choose = unchosen.getByRole('button', {name: '选择版本', exact: true});
    assert(await choose.isVisible(), 'the fix for a missing rule is not hidden in the 更多 menu');
    assert.match(await choose.getAttribute('class'), /\bprimary\b/);
    assert.equal(await unchosen.getByRole('button', {name: '选集下载'}).count(), 0);
    assert.equal(await unchosen.getByRole('button', {name: '恢复追更'}).count(), 0);
    // 删除追剧 uses the styled native confirm dialog instead of window.confirm.
    await unchosen.getByText('更多', {exact: true}).click();
    await unchosen.getByRole('button', {name: '删除追剧', exact: true}).click();
    await page.locator('#confirm-dialog').waitFor({state: 'visible'});
    await page.locator('#cancel-confirm').click();
    await page.locator('#confirm-dialog').waitFor({state: 'hidden'});

    // Config warning banner stays hidden while already on Settings, and reappears on other views.
    checks.cloud = {complete: false, message: '尚未连接阿里云盘。'};
    await page.locator('nav [data-view=settings]').click();
    assert(await page.locator('#refresh-settings').isVisible());
    assert.equal(await page.locator('#config-warning').isHidden(), true, 'warning banner is redundant on settings');
    // Settings columns stay balanced (2 tall cards left, 4 compact cards right) and header nav stays single-row at 1280px.
    assert.equal(await page.locator('.settings-column').nth(0).locator('.setting-block').count(), 2);
    assert.equal(await page.locator('.settings-column').nth(1).locator('.setting-block').count(), 4);
    const brandBox = await page.locator('.brand').boundingBox();
    const settingsNavBox = await page.locator('nav [data-view=settings]').boundingBox();
    assert(Math.abs(brandBox.y - settingsNavBox.y) < 20, 'desktop header nav stays on one row at 1280px');
    // Blocked watch popup offers direct fallback action to open Emby.
    await page.evaluate(() => { window.open = () => null; });
    await page.locator('#watch').click();
    await page.locator('#notice-action').waitFor({state: 'visible'});
    assert.equal(await page.locator('#notice-action').innerText(), '直接前往电影墙');
    await page.locator('#dismiss-notice').click();
    await page.locator('nav [data-view=search]').click();
    await page.locator('#config-warning').waitFor({state: 'visible'});

    assert.deepEqual(errors, []);
    console.log('Independent UI flows passed: login and late account responses, search and submissions, settings, metrics, single-resource controls and recovery, retry focus, playback and season selection.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exit(1);});
