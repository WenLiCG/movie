// Run with Playwright on NODE_PATH and a local Chrome installation: node tests/check_ui.cjs
// All API responses and Emby markup below are synthetic; no external service is contacted.
const assert = require('node:assert/strict');
const {createServer} = require('node:http');
const {readFile} = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const checks = Object.fromEntries(['cloud','downloader','indexers','television','retention','uploader','metadata'].map(k => [k,{complete:k !== 'cloud',message:k === 'cloud' ? '尚未连接，请完成设置。' : '配置正常。'}]));
  const sample = {
    session:{csrf:'synthetic-csrf'},
    status:{settings:{retention_days:7},credentials:{openlist:true,qb:true},checks,cleanup:{done:0,pending:0}},
    metrics:{disk:{used:18*1024**3,total:60*1024**3},memory:{used:1024**3,total:4*1024**3},cpu:8.4},
    recommendations:{items:[{title:'<img src=x onerror=alert(1)> 热门电影',query:'示例电影',release:'完整资源名.2026.1080p',kind:'movie',site:'HDSky',seeders:200},{title:'主角',query:'The Lead',release:'The.Lead.S01.2026.1080p',kind:'tv',site:'SpringSunday',seeders:150}]},
    search:{items:[{id:'sample',title:'<img src=x onerror=alert(1)>.电影.1080p.WEB-DL.H264.'.repeat(5),size:1024**3,resolution:'1080p',codec:'H.264',seeders:28,site:'constructor',free:true,labels:['中字']}],warnings:[]},
    series:{items:[{id:1,tvdbId:42,title:'示例电视剧',original_title:'Example Series',year:2026,monitored:true,archived:3,version:{valid:true,fields:{group:'EXAMPLE',resolution:'1080p'}}}]},
    tasks:{items:[{id:'sample',name:'示例电影',resources:[{hash:'sample',name:'示例电影',size:100,progress:.45,state:'downloading',files:[],uploads:[]}],uploads:[],file_count:1}]},
    playbacks:{items:[{title:'示例电影',started_at:1793700000,position_seconds:79,route:'cloud',state:'playing',http_status:307,host:'example.invalid',has_progress:true}]},
  };
  const pack='同名剧集.S01.2026.1080p.WEB-DL';
  const jobs=[16,18].map(id=>({id:String(id),dest:`/aliyun/TV/同名剧集.S01E${id}.mkv`,state:'published'}));
  sample.tasks.items.push({id:'series:42',name:'同名剧集',tvdb_id:42,file_count:2,uploads:jobs,resources:jobs.map(j=>({hash:'hash'+j.id,name:pack,size:100,progress:1,state:'stalledUP',uploads:[j],files:[{name:j.dest.split('/').pop(),job_id:j.id,priority:1,progress:1,size:100}]}))});
  let loggedIn = false;
  const searches=[];
  const taskRequests=[];
  let previewVersion=1;
  const nativeThemeRequests = new Map();
  const server = createServer(async (request,response) => {
    const url = new URL(request.url,'http://localhost');
    if (url.pathname.startsWith('/discover/api/')) {
      const name = url.pathname.split('/').pop();
      response.setHeader('Content-Type','application/json');
      if (name === 'session' && !loggedIn) {response.writeHead(401);response.end('{}');return;}
      if (name === 'login') {loggedIn=true;response.end(JSON.stringify(sample.session));return;}
      if (name === 'search') searches.push(Object.fromEntries(url.searchParams));
      if (name === 'task-action') {
        let raw='';for await(const chunk of request)raw+=chunk;
        const body=JSON.parse(raw);
        taskRequests.push(body);
        if(body.preview){
          const files=previewVersion===1?[jobs[1].dest]:[jobs[1].dest,'/aliyun/TV/同名剧集.S01E19.mkv'];
          response.end(JSON.stringify({scope:'preview-'+previewVersion,local:['/media/18.mkv'],cloud:files}));
        }else if(body.scope==='preview-1'){
          previewVersion++;
          response.writeHead(409);
          response.end(JSON.stringify({error:'文件清单已变化，请重新确认。'}));
        }else response.end(JSON.stringify({message:'已提交删除'}));
        return;
      }
      response.end(JSON.stringify(sample[name] || {}));return;
    }
    if (url.pathname === '/fake-emby') {
      response.setHeader('Content-Type','text/html');
      response.end('<!doctype html><html class="theme-dark"><head><link rel="stylesheet" href="/web/modules/themes/dark/theme.css"><script src="/discover/theme.js" defer></script></head><body><div class="headerRight"></div></body></html>');return;
    }
    if (url.pathname.startsWith('/web/modules/themes/')) {
      nativeThemeRequests.set(url.pathname,(nativeThemeRequests.get(url.pathname)||0)+1);
      response.setHeader('Content-Type','text/css');response.setHeader('Cache-Control','no-store');
      response.end(url.pathname.endsWith('/theme.css') ? 'body{color:'+(url.pathname.includes('/light/')?'rgb(20,40,20)':'rgb(230,240,230)')+'}' : '');return;
    }
    const file = url.pathname === '/discover/' ? 'index.html' : url.pathname.split('/').pop();
    if (!['index.html','style.css','app.js','paint.js','theme.js'].includes(file)) {response.writeHead(404);response.end();return;}
    response.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html');
    response.end(await readFile(path.join(__dirname,'../portal',file)));
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser = await chromium.launch({channel:'chrome',headless:true});
    const context = await browser.newContext({viewport:{width:1440,height:1000}}), page = await context.newPage(), errors=[];
    context.on('page',p => p.on('pageerror',e => errors.push(e.message)));
    page.on('pageerror',e => errors.push(e.message));
    const origin='http://127.0.0.1:'+server.address().port;
    await page.goto(origin+'/discover/');
    await page.locator('#login').waitFor({state:'visible'});
    assert.equal(await page.locator('html').getAttribute('data-cinema-theme'),'light');
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.className),'skip-link');
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement.id),'main-content');
    await page.locator('#username').fill('demo');await page.locator('#password').fill('synthetic-only');
    await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('.recommendation-link').first().waitFor();
    assert.equal(await page.locator('#recommendations img').count(),0);
    assert.equal(await page.locator('.recommendation-link strong').last().innerText(),'主角');
    assert.equal(await page.locator('.recommendation-link').last().getAttribute('title'),'The.Lead.S01.2026.1080p');
    for(const width of [1440,768,390,320]){
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'recommendation overflow '+width);
    }
    await page.locator('.recommendation-link').last().click();await page.locator('.result-row').waitFor();
    assert.deepEqual(searches.at(-1),{q:'The Lead',kind:'tv'});
    assert.equal(await page.locator('#query').inputValue(),'The Lead');
    assert(await page.locator('[name=kind][value=tv]').isChecked());
    assert(!(await page.locator('#recommendations').isVisible()));
    await page.locator('[name=kind][value=movie]').check();
    await page.locator('#query').fill('示例电影');await page.locator('#search-form button').click();await page.locator('.result-row').waitFor();
    assert.equal(await page.locator('.result-row img').count(),0);
    assert.equal(await page.locator('.status-label.success').innerText(),'免费');
    assert(await page.locator('.result-row button svg').isVisible());
    assert.equal(await page.locator('.row-meta .status-label').count(),1);
    assert(await page.evaluate(() => [...document.querySelectorAll('use')].every(el => document.querySelector(el.getAttribute('href')))));
    await page.locator('nav [data-view=tasks]').click();
    const show=page.locator('[data-id="series:42"]');
    await show.locator('summary').first().click();
    assert(await show.getByRole('heading',{name:'S01E16',exact:true}).isVisible());
    assert(await show.getByRole('heading',{name:'S01E18',exact:true}).isVisible());
    assert.equal(await show.getByRole('heading',{name:pack,exact:true}).count(),0);
    assert.equal(await show.locator('.task-source').count(),2);
    await show.locator('.task-source summary').last().click();
    await show.locator('.task-source').last().getByRole('button',{name:'删除项目',exact:true}).click();
    assert.equal(await page.evaluate(()=>taskTarget.id),'torrent:hash18');
    assert(await page.locator('[name=delete-files][value=false]').isChecked());
    await page.locator('[name=delete-files][value=all]').check();
    assert(await page.locator('#task-all-details').isVisible());
    await page.locator('#task-all-files li').first().waitFor();
    assert.equal(await page.locator('#task-all-files li').count(),1);
    assert((await page.locator('#task-all-files').innerText()).includes('S01E18'));
    assert((await page.locator('#task-dialog-cloud-note').innerText()).includes('云端删除无法在此撤销'));
    for(const width of [1440,390,320]){
      await page.setViewportSize({width,height:844});
      assert(await page.locator('#task-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth),'delete-all dialog overflow '+width);
    }
    await page.locator('#confirm-task').click();
    await page.locator('#task-all-files li').nth(1).waitFor();
    assert(await page.locator('#task-dialog-error').isVisible());
    assert.equal(taskRequests.filter(r=>!r.preview).length,1);
    await page.locator('#confirm-task').click();
    await page.locator('#task-dialog').waitFor({state:'hidden'});
    assert.deepEqual(taskRequests.at(-1),{id:'torrent:hash18',action:'delete',delete_files:true,delete_all:true,scope:'preview-2'});
    await page.locator('#refresh-tasks').click();
    await page.waitForFunction(()=>[...document.querySelectorAll('[data-id="series:42"] .task-source')].at(-1).open);
    for (const width of [1440,1024,768,390,320]) {
      await page.setViewportSize({width,height:1000});
      for (const view of ['search','series','tasks','playbacks','settings']) {
        await page.locator('nav [data-view='+view+']').click();
        await page.waitForFunction(id => !document.getElementById(id).hidden,view);
        assert(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth),'overflow: '+view+' at '+width);
        const watch=await page.locator('#watch').boundingBox(),find=await page.locator('nav [data-view=search]').boundingBox();
        assert(watch.x+watch.width<=find.x+1 && Math.abs(watch.y-find.y)<2,'navigation order at '+width);
      }
    }
    await page.locator('#theme-select').selectOption('dark');await page.reload();
    assert.equal(await page.locator('html').getAttribute('data-cinema-theme'),'dark');
    const emby=await context.newPage();await emby.goto(origin+'/fake-emby');
    await emby.waitForFunction(() => document.documentElement.classList.contains('theme-dark'));
    await page.locator('#theme-select').selectOption('light');
    await emby.waitForFunction(() => document.documentElement.classList.contains('theme-light'));
    assert(await emby.locator('link[href="/web/modules/themes/dark/theme.css"]').evaluate(el => el.disabled));
    await emby.waitForFunction(() => getComputedStyle(document.body).color==='rgb(20, 40, 20)');
    const lightRequests=nativeThemeRequests.get('/web/modules/themes/light/theme.css');
    await emby.evaluate(() => {const style=document.createElement('style');style.textContent='.native-late-component{padding:1px}';document.head.append(style);});
    await emby.waitForTimeout(250);
    assert.equal(nativeThemeRequests.get('/web/modules/themes/light/theme.css'),lightRequests,'late native components must preserve loaded theme stylesheets');
    await emby.evaluate(() => document.documentElement.className='theme-dark');
    await emby.waitForFunction(() => document.documentElement.classList.contains('theme-light'));
    await emby.locator('#cinema-theme-toggle').click();
    await page.waitForFunction(() => document.getElementById('theme-select').value==='dark');
    for (const theme of ['light','dark']) {
      await page.locator('#theme-select').selectOption(theme);
      const colors=await page.evaluate(() => Object.fromEntries(['bg','surface','text','muted','accent','accent-soft','ink','warning','warning-soft','info','info-soft','error','error-soft','control-line'].map(key => [key,getComputedStyle(document.documentElement).getPropertyValue('--'+key).trim()])));
      const luminance=hex => {
        if(hex.length===4)hex='#'+[...hex.slice(1)].map(c=>c+c).join('');
        return hex.slice(1).match(/../g).map(c=>parseInt(c,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
      };
      for (const [foreground,background,minimum] of [['text','bg',4.5],['muted','bg',4.5],['muted','surface',4.5],['ink','accent',4.5],['accent','accent-soft',4.5],['warning','warning-soft',4.5],['info','info-soft',4.5],['error','error-soft',4.5],['control-line','surface',3]]) {
        const values=[luminance(colors[foreground]),luminance(colors[background])].sort((a,b)=>a-b),ratio=(values[1]+.05)/(values[0]+.05);
        assert(ratio>=minimum,`${theme} ${foreground}/${background}: ${ratio.toFixed(2)}`);
      }
    }
    const blocked=await context.newPage();
    await blocked.addInitScript(() => Object.defineProperty(window,'localStorage',{get(){throw new Error('Storage blocked');}}));
    await blocked.goto(origin+'/discover/');
    assert.equal(await blocked.locator('html').getAttribute('data-cinema-theme'),'light');
    await blocked.locator('#theme-select').selectOption('dark');
    assert.equal(await blocked.locator('html').getAttribute('data-cinema-theme'),'dark');
    assert.deepEqual(errors,[]);
    console.log('UI check passed: five views at 320–1440px, scoped delete-all confirmation/payload, recommendations, grouped versions, SVGs, escaping, theme persistence/Emby sync, blocked storage and contrast.');
  } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
