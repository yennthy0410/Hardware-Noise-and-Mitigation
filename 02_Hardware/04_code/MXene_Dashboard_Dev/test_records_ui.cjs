const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const ctx=vm.createContext({console,Set,Date,Number,String,Math,esc:x=>String(x??'N/A'),jobValue:x=>String(x??'N/A'),jobTime:x=>x||'N/A',table:r=>JSON.stringify(r)});
vm.runInContext(fs.readFileSync('static/records.js','utf8'),ctx);
const run=s=>vm.runInContext(s,ctx);
run(`recordsData={rows:[5,10,15,20,25,30].flatMap(n=>[{key:n+'a',target_n:n,batch_id:'b'+n,treatment:'DD',job_id:'job'+n,recovery:'Original'},{key:n+'b',target_n:n,batch_id:'b'+n,treatment:'TREX',job_id:'replacement'+n,recovery:'Replacement'}])}`);
for(const n of [5,10,15,20,25,30]){run(`recordsN='${n}'`);assert.equal(run('recordsSelected().length'),2);}
run("recordsN='';recordsQuery='replacement30'");assert.equal(run('recordsSelected().length'),1);
run("recordsQuery='';recordsBatch='b5';recordsTreatment='TREX'");assert.equal(run('recordsSelected()[0].job_id'),'replacement5');
assert.ok(run('recordsMarkup()').includes('Entire campaign'));assert.ok(run('recordsMarkup()').includes('Reload saved records'));
assert.ok(run("recordsDetails({saved_runtime:[],events:[],provenance:[],validation:[],ledger_states:[]})").includes('Original / replacement provenance'));
assert.ok(!run('recordsMarkup()').includes('Run Auto Manager'));
console.log('PASS: six n-level filters, combined filters, originals/replacements, historical layout, expandable detail markup; simulated JS harness, not browser rendering.');

run("recordsBatch='';recordsTreatment='';recordsAccount='labK';recordsKind='Replacement';recordsData.rows.forEach(r=>r.account='labK')");
assert.equal(run('recordsSelected().length'),6);
run("recordsKind='';recordsSort='target_n';recordsDir=-1");assert.equal(run('recordsSelected()[0].target_n'),30);
assert.ok(run('recordsMarkup()').includes('records-account'));assert.ok(run('recordsMarkup()').includes('records-kind'));
assert.ok(run('recordsMarkup()').includes('Started At (UTC)'));
const nodes={};ctx.$=id=>nodes[id]||(nodes[id]={});ctx.document={querySelectorAll:()=>[]};
run("recordsData.rows=Array.from({length:60},(_,i)=>({key:String(i),job_id:String(i),target_n:5,account:'labK',recovery:'Original'}));recordsPage=1;updateRecords()");
assert.equal((nodes['records-body'].innerHTML.match(/data-record-open=/g)||[]).length,25);
run("recordsOpen.add(recordBatchKey(recordsData.rows[25]));updateRecords()");assert.ok(nodes['records-body'].innerHTML.includes('data-record-copy='));assert.equal(nodes['records-next'].disabled,false);
run('recordsPage=2;updateRecords()');assert.equal((nodes['records-body'].innerHTML.match(/data-record-open=/g)||[]).length,10);assert.equal(nodes['records-next'].disabled,true);
console.log('PASS: account/classification filters, numeric sorting, 25-row pagination, full-ID copy button markup (simulated frontend).');

run("recordsN='';recordsBatch='';recordsTreatment='';recordsAccount='';recordsKind='';recordsQuery='';recordsData={rows:[{key:'original',campaign_id:'c',target_n:30,block:'registered-block',batch_sequence:6,job_id:'original-id',batch_id:'original-uuid',recovery:'Original',treatment:'ZNE',account:'labA',status:'DONE',expected_pubs:5,accepted_pubs:4},{key:'replacement',campaign_id:'c',target_n:30,block:'registered-block',batch_sequence:6,job_id:'replacement-id',batch_id:'replacement-uuid',recovery:'Replacement',treatment:'ZNE',account:'labB',status:'DONE',expected_pubs:1,accepted_pubs:1}]}");
assert.equal(run('recordGroups(recordsSelected()).length'),1);
assert.equal(run('recordGroups(recordsSelected())[0].label'),'Batch 006');
assert.equal(run('batchCells(recordGroups(recordsSelected())[0])[4]'),5);
const before=run('JSON.stringify(recordsData.rows)');
assert.ok(run('batchDetails(recordGroups(recordsSelected())[0])').includes('replacement-uuid'));
assert.ok(run('batchDetails(recordGroups(recordsSelected())[0])').includes('original-id'));
assert.equal(run('JSON.stringify(recordsData.rows)'),before);
run("recordsStatus='RUNNING'");assert.equal(run('recordsSelected().length'),0);
run("recordsStatus='DONE';recordsAccount='labB'");assert.equal(run('recordsSelected()[0].job_id'),'replacement-id');
assert.equal(run("recordBatchLabel({target_n:30,block:'arbitrary'})"),'Unnumbered execution');
console.log('PASS: stable registered Batch grouping, original denominator, identifier preservation, status/account filtering, unnumbered fallback.');
