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
  const state = {
    session: {csrf: 'synthetic-csrf'},
    status: () => ({settings: {retention_days: retentionDays}, credentials: {openlist: false, qb: false}, checks, cleanup: {done: 0, pending: 0, deferred: 0, reasons: []}}),
    metrics: {disk: {used: 1024, total: 2048}, memory: {used: 1024, total: 2048}, cpu: 1},
    recommendations: {items: []},
    search: {items: [{id: 'tv-1', title: '示例剧.S01.1080p', size: 1024, resolution: '1080p', codec: 'H.264', seeders: 3, site: 'local', free: true}], warnings: []},
    series: {items: [{id: 1, tvdbId: 42, title: '示例剧', year: 2026, monitored: true, archived: 0, version: {valid: true, fields: {resolution: '1080p'}}, seasons: [{seasonNumber: 0, name: 'Specials'}, {seasonNumber: 1}, {seasonNumber: 2}]}]},
    tasks: {items: [{id: 'movie:1', name: '示例电影', file_count: 1, uploads: [{id: 'job1', dest: '/aliyun/Movies/A/A.mkv', state: 'failed', error: 'upload_unconfirmed'}], resources: [{hash: 'abc', name: '示例电影', size: 100, progress: 1, state: 'stalledUP', files: [], uploads: []}]}]},
    playbacks: {items: []},
  };
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/discover/api/')) {
      const name = url.pathname.split('/').pop();
      response.setHeader('Content-Type', 'application/json');
      if (name === 'session' && sessionMode === 'html-503') {
        response.writeHead(503, {'Content-Type': 'text/html'});
        response.end('<html>gateway down</html>');
        return;
      }
      if (name === 'session' && !loggedIn) {response.writeHead(401); response.end('{}'); return;}
      if (name === 'login') {loggedIn = true; response.end(JSON.stringify(state.session)); return;}
      if (name === 'logout') {loggedIn = false; response.end('{}'); return;}
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
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/discover/');
    await page.locator('#login').waitFor({state: 'visible'});
    assert.match(await page.locator('#notice').innerText(), /服务暂时不可用/);
    assert.equal(await page.locator('#workspace').isHidden(), true);

    sessionMode = 'signed-out';
    await page.locator('#username').fill('demo');
    await page.locator('#password').fill('synthetic-only');
    await page.getByRole('button', {name: '登录', exact: true}).click();
    await page.locator('#workspace').waitFor({state: 'visible'});

    await page.locator('[name=kind][value=tv]').check();
    await page.locator('#query').fill('示例剧');
    await page.locator('#search-form button').click();
    await page.locator('.result-row').waitFor();
    assert.equal(await page.locator('#results .result-row').count(), 1);
    let downloadRoute;
    await page.route('**/discover/api/download-release', route => { downloadRoute = route; });
    await page.getByRole('button', {name: '下载此资源', exact: true}).click();
    await page.locator('#free').check();
    const pending = page.getByRole('button', {name: '正在提交…', exact: true});
    assert(await pending.isDisabled(), 'filter changes must preserve a pending submission');
    await downloadRoute.fulfill({json: {message: '已提交此资源'}});
    await page.getByRole('button', {name: '已提交', exact: true}).waitFor();
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
    await page.locator('#refresh-token').fill('synthetic-unused-token');
    await page.locator('#days').fill('123');
    await page.locator('#logout').click();
    await page.locator('#login').waitFor({state: 'visible'});
    assert.equal(await page.locator('#results').innerText(), '');
    assert.equal(await page.locator('#workspace').isHidden(), true);
    assert.equal(await page.locator('#task-list').innerText(), '');
    assert.equal(await page.locator('#refresh-token').inputValue(), '');
    assert.equal(await page.locator('#query').inputValue(), '');

    await page.locator('#username').fill('demo');
    await page.locator('#password').fill('synthetic-only');
    await page.getByRole('button', {name: '登录', exact: true}).click();
    await page.locator('#workspace').waitFor({state: 'visible'});
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
    const retry = page.getByRole('button', {name: '重新上传', exact: true});
    await retry.focus();
    await page.evaluate(() => tasks());
    await page.waitForFunction(() => document.activeElement?.dataset.taskControl === 'job1:retry');

    await page.locator('nav [data-view=playbacks]').click();
    await page.getByText('暂无播放记录。', {exact: false}).waitFor();
    await page.route('**/discover/api/playbacks', route => route.fulfill({status: 503, json: {error: '稍后再试'}}));
    await page.locator('#refresh-playbacks').click();
    await page.getByText('刷新失败，仍显示上次结果。', {exact: true}).waitFor();

    await page.locator('nav [data-view=series]').click();
    await page.getByText('更多', {exact: true}).click();
    await page.getByRole('button', {name: '设置追更范围', exact: true}).click();
    await page.locator('#follow-dialog').waitFor({state: 'visible'});
    const seasons = await page.locator('#season option').evaluateAll(options => options.map(option => option.value));
    assert.deepEqual(seasons, ['1', '2']);
    await page.locator('#follow-dialog').evaluate(dialog => dialog.close());

    assert.deepEqual(errors, []);
    console.log('Independent UI flows passed: startup 503, submission state, search reset, sign-out drafts, settings and retry focus, metrics recovery, stale playback, season 0 hidden.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exit(1);});
