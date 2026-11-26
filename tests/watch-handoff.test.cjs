// Run: node tests/watch-handoff.test.cjs
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../relay/custom-js/search.js'),'utf8');
function page(origin,portal,hash='#!/login?cinemaHandoff=1',search=''){
  const values=new Map(),messages=[],handlers={},opener={postMessage:(data,to)=>messages.push({data,to})};
  let redirect='';
  const window={opener,__cinemaSearchInstalled:true,addEventListener:(name,fn)=>handlers[name]=fn};
  const location={origin,hostname:new URL(origin).hostname,protocol:new URL(origin).protocol,search,hash,replace:path=>redirect=path};
  vm.runInNewContext(code,{window,location,localStorage:{getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)},Date});
  return {values,messages,receive:data=>handlers.message(data),opener,redirect:()=>redirect,portal};
}
for(const [origin,portal,hash,search] of [
  ['https://movie.example.com','https://movie.example.com','#!/login?cinemaHandoff=1',''],
  ['https://movie.example.com','https://movie.example.com','#!/startup/manuallogin.html','?cinemaHandoff=1793700000'],
  ['http://127.0.0.1:8095','http://127.0.0.1:8097','#!/login?cinemaHandoff=1',''],
]){
  const p=page(origin,portal,hash,search),auth={server_id:'server',user_id:'admin',token:'user-session-only',name:'影院'};
  assert.deepEqual(JSON.parse(JSON.stringify(p.messages[0].data)),{type:'cinema-watch-ready'});assert.equal(p.messages[0].to,portal);
  p.receive({origin:'https://evil.example',source:p.opener,data:{type:'cinema-watch-login',auth}});
  p.receive({origin:portal,source:{},data:{type:'cinema-watch-login',auth}});
  assert.equal(p.values.size,0);
  p.values.set('servercredentials3',JSON.stringify({Servers:[{Id:'another',Users:[{UserId:'other',AccessToken:'other-session'}]}]}));
  p.receive({origin:portal,source:p.opener,data:{type:'cinema-watch-login',auth}});
  const saved=JSON.parse(p.values.get('servercredentials3')),s=saved.Servers.find(s=>s.Id==='server');
  assert.equal(saved.Servers.length,2);assert.equal(s.ManualAddress,origin);assert.equal(s.UserId,'admin');
  assert.deepEqual(s.Users,[{UserId:'admin',AccessToken:'user-session-only'}]);assert(!s.AccessToken);
  assert.equal(p.values.get('autoLogin'),'lastuser');assert.equal(p.redirect(),'/web/index.html#!/home');
  assert(!p.redirect().includes(auth.token));assert.equal(p.messages.at(-1).data.type,'cinema-watch-complete');
}
console.log('watch handoff: exact origin/source, same host across tunnel ports, preserved accounts, user token outside URLs');
