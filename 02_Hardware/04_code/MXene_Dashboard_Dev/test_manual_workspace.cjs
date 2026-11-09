// Offline UI contracts: no HTTP server, credentials or provider calls.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},calls=[];
const context=vm.createContext({console,sessionStorage:{getItem:()=>null},window:{},
 document:{querySelectorAll:()=>[]},$:id=>nodes[id],esc:x=>String(x??'Unavailable'),empty:x=>x,
 card:(title,body)=>title+body,table:rows=>JSON.stringify(rows),singlePubMarkup:()=>'<p>Advanced fixture</p>',
 render:()=>{},workspaceView:'manager',workspaceVisible:()=>true,workspaceId:'fixture-chat',
 renderAssistantResponse:async(host,r)=>host.result=r,
 assistantRequest:async(path,body,method)=>{calls.push({path,body,method});return {control_state:'AWAITING_CONFIRMATION'};}});
vm.runInContext(fs.readFileSync('static/manual_workspace.js','utf8'),context);
const run=code=>vm.runInContext(code,context);
run(`managerData={campaign_id:'vqe-independent-n-01',campaign_state:'PAUSED',accepted:5,expected:240,campaigns:[{campaign_id:'vqe-independent-n-01'}],levels:[{n:30,accepted:5,expected:240,batch_count:1,collections:{Collected:1},execution:{DONE:1},batches:[{block_id:'registered-block-004',sequence:4,chunk_n:5,batch_id:'registered-runtime-batch',jobs:[{job_id:'registered-job',block:'registered-block-004',expected_pubs:5,treatment:'None',treatment_ids:['M0_Baseline'],recovery:'Original',provenance:[]}]}]}]};managerLevel=30;`);
assert.equal(run('managerView'),'progress');
let html=run('managerMarkup()');
assert.match(html,/Campaign Progress/);assert.match(html,/AUTO Control/);assert.match(html,/STEP-by-STEP/);
assert.match(html,/registered-runtime-batch/);assert.match(html,/Refresh local evidence/);
assert.equal(calls.length,0);
run(`manualOperation='analyze'`);html=run('manualMarkup()');
assert.doesNotMatch(html,/id="manual-kind"/);assert.match(html,/Complete n-level science/);
run(`manualOperation='collect';manualKind='pub'`);html=run('manualMarkup()');
assert.match(html,/value="16"/);assert.match(html,/value="20"/);assert.match(html,/full parent Job/);
for(const id of ['manual-prepare','manual-result','manual-latest'])nodes[id]={replaceChildren(){},disabled:false};
run('bindManualWorkspace()');
(async()=>{
 await nodes['manual-prepare'].onclick();
 assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/conversations/fixture-chat/manual');
 assert.equal(calls[0].body.manual_scope.job_id,'registered-job');assert.equal(calls[0].body.manual_scope.slot,16);
 assert.equal(calls[0].body.manual_scope.action,'collect');assert.equal(nodes['manual-result'].result.control_state,'AWAITING_CONFIRMATION');
 await nodes['manual-latest'].onclick();assert.equal(calls[1].path,'/api/manual/result');
 assert.ok(calls.every(c=>!c.path.includes('/confirm')&&!c.path.includes('/dispatch')));
 const index=fs.readFileSync('static/index.html','utf8');
 assert.ok(index.indexOf('manual_workspace.js')<index.indexOf('research_workspace.js'));
 run(fs.readFileSync('static/research_workspace.js','utf8'));
 run("workspaceView='manager';managerView='progress'");
 await run('workspaceSuggestions()');assert.equal(calls.length,2);
 console.log('PASS: default Progress, separate controls, registry selectors, scoped PUB preview, n-level science, explicit status, no auto-dispatch');
})().catch(error=>{console.error(error);process.exitCode=1;});
