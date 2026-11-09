const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let revision='r1',fail=false,renders=0,timer,calls=[];
const nodes={connection:{},updated:{}};
const ctx=vm.createContext({state:{},managerData:null,recordsData:null,managerError:'',recordsError:'',active:'Results',level:25,
 $:id=>nodes[id],render:()=>renders++,setInterval:(f,ms)=>{timer={f,ms};},
 fetch:async path=>{calls.push(path);if(fail)throw Error('fixture failure');return {ok:true,json:async()=>path==='/api/data-revision'?{revision}:{revision,snapshot:{revision,artifacts:[{name:revision}],captured_at:'fixture'},progress:{revision,accepted:revision==='r1'?0:40},records:{revision,rows:[revision]}}};}});
vm.runInContext(fs.readFileSync('static/data_sync.js','utf8'),ctx);
const run=s=>vm.runInContext(s,ctx);
(async()=>{
 assert.equal(timer.ms,5000);
 await run('synchronizeDashboard()');assert.equal(ctx.state.revision,'r1');assert.equal(ctx.managerData.revision,ctx.recordsData.revision);
 await run('synchronizeDashboard()');assert.equal(renders,1);
 revision='r2';await timer.f();assert.equal(ctx.state.artifacts[0].name,'r2');assert.equal(ctx.managerData.accepted,40);assert.equal(ctx.level,25);
 ctx.level=30;await run('synchronizeDashboard(true)');assert.equal(ctx.level,30);
 fail=true;await timer.f();assert.match(nodes.connection.textContent,/STALE/);assert.equal(ctx.state.revision,'r2');fail=false;
 await Promise.all([run('synchronizeDashboard(true)'),run('synchronizeDashboard(true)')]);
 assert.ok(calls.every(p=>p.startsWith('/api/data-revision')||p.startsWith('/api/dashboard-data')));
 const m=vm.createContext({});vm.runInContext(fs.readFileSync('static/manual_workspace.js','utf8'),m);
 assert.equal(vm.runInContext('statusLabels({DONE:7,RUNNING:1})',m),'Completed: 7; Running: 1');
 assert.match(fs.readFileSync('static/manual_workspace.js','utf8'),/Refresh IBM status \(read-only\)/);
 console.log('PASS: local-only completion refresh, one revision across views, Results update, n-level retention, errors and status labels');
})().catch(e=>{console.error(e);process.exitCode=1;});
