// Run: node tests/task-controls.test.cjs
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../portal/app.js'),'utf8');
const context=vm.createContext({
  node:(tag,text)=>({tag,text,dataset:{},children:[],append(...items){this.children.push(...items);}}),
});
vm.runInContext(source.match(/const seedStates=.+;/)[0]+'\n'+source.slice(source.indexOf('function taskButtons('),source.indexOf('function openTaskDialog(')),context);
const controls=(target,status)=>context.taskButtons(target,status).children;
const labels=(target,status)=>controls(target,status).filter(n=>n.tag==='button').map(n=>n.text).join(',');
const done={id:'torrent:one',progress:1,state:'stalledUP',uploads:[{state:'published'}]};
assert.equal(labels(done),'删除项目');
assert.equal(controls(done)[0].text,'等待做种连接');
assert.equal(labels({...done,state:'stoppedUP'}),'删除项目');
assert.equal(controls({...done,state:'stoppedUP'})[0].text,'做种已暂停');
assert.equal(labels({...done,uploads:[{state:'queued'}]}),'取消归档,删除项目');
assert.equal(labels({...done,uploads:[{state:'cancelled'}]},{action:'cancel',state:'done'}),'继续归档,删除项目');
assert.equal(labels({...done,progress:.5,state:'downloading'}),'暂停下载,取消下载,删除项目');
assert.equal(labels({...done,progress:0,state:'metaDL',uploads:[]}),'暂停下载,取消下载,删除项目');
assert.equal(labels({...done,progress:.5,state:'stoppedDL'}),'继续下载,取消下载,删除项目');
assert.equal(labels({...done,progress:.5,state:'downloading'},{action:'resume',state:'done',error:'task_control_failed'}),'重试继续,取消下载,删除项目');
assert.equal(labels({resources:[done,{...done,progress:.5,state:'stoppedDL'}],uploads:[],tvdb_id:42}),'继续下载,取消下载,删除追剧项目');
assert.equal(labels({resources:[done,done],uploads:done.uploads,tvdb_id:42}),'删除追剧项目');
assert.equal(labels({resources:[],uploads:done.uploads,tvdb_id:42}),'删除追剧项目');
assert.equal(labels({resources:[],uploads:[{state:'failed'}]}),'取消归档,删除项目');
assert(controls(done,{state:'pending',action:'delete'}).find(n=>n.tag==='button').disabled);
console.log('Task controls passed: downloading, paused, mixed, archiving, cancelled and published resources.');
