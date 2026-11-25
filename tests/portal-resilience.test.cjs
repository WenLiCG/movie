// Run: node tests/portal-resilience.test.cjs
// Slices individual functions out of portal/app.js (like task-controls.test.cjs) and runs them with stubs.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../portal/app.js'),'utf8');
const slice=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));

(async()=>{
  // api(): network failures and non-JSON 401 responses still produce a usable state.
  {
    let signedOut=0;
    const context=vm.createContext({csrf:'',signedIn:v=>{if(!v)signedOut++;},JSON,Object,Error});
    vm.runInContext('var csrf="";'+slice('async function api(','async function action('),context);
    context.fetch=async()=>{throw new TypeError('Failed to fetch');};
    await assert.rejects(context.api('session'),e=>e.message.includes('无法连接影院服务'));
    context.fetch=async()=>({status:401,ok:false,json:async()=>{throw new SyntaxError('html');}});
    await assert.rejects(context.api('session'),e=>e.status===401);
    assert.equal(signedOut,1,'401 without a JSON body must still sign the page out');
  }
  // uploadRow(): retry/reconnect buttons carry a stable focus key so 10 s polling keeps keyboard focus.
  {
    const made=[];
    const element=()=>({dataset:{},children:[],firstChild:{firstChild:{}},append(...v){this.children.push(...v);}});
    const context=vm.createContext({
      node:(tag,text)=>{const el={...element(),tag,text};made.push(el);return el;},
      row:()=>element(),episodeTitle:v=>v,uploadStates:{},
    });
    vm.runInContext(slice('function awaitingArchiveCheck(','let tasksAgain='),context);
    const failed=context.uploadRow({id:'job1',dest:'/aliyun/Movies/A/A.mkv',state:'failed',error:'openlist_code_401'});
    const keys=failed.children.map(b=>b.dataset.taskControl);
    assert.deepEqual(keys,['job1:retry','job1:reconnect']);
  }
  // Follow scope never offers season 0 (Sonarr specials), which the server rejects.
  assert.match(source,/\$\('season'\)\.replaceChildren\(\.\.\.\(item\.seasons\|\|\[\]\)\.filter\(v=>v\.seasonNumber>0\)/);
  // Whole result list is not a live region; the result count announces changes.
  const html=fs.readFileSync(require('node:path').join(__dirname,'../portal/index.html'),'utf8');
  assert.match(html,/<div id="results"><\/div>/);
  assert.match(html,/id="result-count"[^>]*aria-live="polite"/);
  console.log('Portal resilience passed: network/401 errors, focus keys, season scope and live regions.');
})().catch(error=>{console.error(error);process.exit(1);});
