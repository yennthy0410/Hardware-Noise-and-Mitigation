// Offline DOM contract harness. Executes actual frontend scripts, not a real browser.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const guide=JSON.parse(fs.readFileSync('static/guide.json','utf8'));
let elements=[],copied='',requests=[],checks=0;
class Element{
 constructor(tag,attrs=''){this.tag=tag;this.dataset={};this.hidden=false;this.open=false;this.value='';this.textContent='';this.attrs={};for(const m of attrs.matchAll(/([\w-]+)="([^"]*)"/g)){this.attrs[m[1]]=m[2];if(m[1]==='id')this.id=m[2];if(m[1]==='value')this.value=m[2];if(m[1].startsWith('data-'))this.dataset[m[1].slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=m[2];}this.open=/\bopen\b/.test(attrs);}
 set innerHTML(html){this.html=html;if(this.id==='content')elements=elements.filter(e=>!e.dynamicContent);if(this.id==='nav')elements=elements.filter(e=>!e.dynamicNav);for(const m of html.matchAll(/<(\w+)\s+([^>]+)>/g)){const e=new Element(m[1],m[2]);e.dynamicContent=this.id==='content';e.dynamicNav=this.id==='nav';elements.push(e);}}
 get innerHTML(){return this.html||'';}setAttribute(k,v){this.attrs[k]=v;}focus(){this.focused=true;}scrollIntoView(){this.scrolled=true;}
}
for(const id of ['title','nav','context-help','content','connection','updated'])elements.push(new Element('div',`id="${id}"`));
const document={getElementById:id=>elements.find(e=>e.id===id),querySelectorAll(selector){
 if(selector.startsWith('.guide-section'))return elements.filter(e=>e.dataset.section&&(!selector.includes(':not')||!e.hidden));
 const m=selector.match(/\[data-([\w-]+)(?:="([^"]+)")?\]/);if(m){const k=m[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase());return elements.filter(e=>k in e.dataset&&(!m[2]||e.dataset[k]===m[2]));}return [];
},querySelector(selector){return this.querySelectorAll(selector)[0];}};
const context=vm.createContext({synchronizeDashboard:async()=>{},document,console,location:{hash:'',pathname:'/'},history:{replaceState(_,__,path){context.location.hash=path.includes('#')?path.slice(path.indexOf('#')):'';}},window:{scrollY:100,scrollTo(){},addEventListener(){}},navigator:{clipboard:{async writeText(s){copied=s;}}},setInterval(){},fetch:async url=>{requests.push(url);return {ok:true,json:async()=>url.includes('guide.json')?guide:{levels:[],jobs:[],recovery:[],artifacts:[],events:[]}};}});
function run(code){return vm.runInContext(code,context);}function check(value,message){assert(value,message);checks++;}
(async()=>{
 vm.runInContext(fs.readFileSync('static/guide.js','utf8'),context);
 for(const file of ['jobs','records','gallery','diagrams','workspace','chat','manual_workspace','manual_execution','research_workspace'])vm.runInContext(fs.readFileSync('static/'+file+'.js','utf8'),context);
 run('bindAgentSettings=async()=>{}'); // Provider UI has its own offline contract suite.
 vm.runInContext(fs.readFileSync('static/app.js','utf8'),context);
 await new Promise(r=>setImmediate(r));
 await run('loadGuide()');
 check(document.querySelectorAll('[data-tab]').length===7,'seven research navigation buttons');
 for(const [tab,target] of Object.entries(run('guideHelp'))){
  run(`active=${JSON.stringify(tab)};render()`);
  check(document.getElementById('title').textContent===tab,tab+' renders');
  document.getElementById('context-help').onclick();
  check(run('active')==='User Guide'&&context.location.hash==='#guide-'+target,tab+' correct help anchor');
  check(document.getElementById('guide-'+target).scrolled,'anchor opens and scrolls');
  document.getElementById('guide-back').onclick();check(run('active')===tab,'back restores '+tab);
 }
 run("active='Results';level=25;page=2;openGuide('figures')");document.getElementById('guide-back').onclick();check(run('level===25&&page===2'),'Results selection retained');
 run("openGuide('getting-started')");
 document.getElementById('guide-search').oninput({target:{value:'Nyquist'}});check(!document.getElementById('guide-figures').hidden&&document.getElementById('guide-jobs').hidden,'search filters');
 document.getElementById('guide-search').oninput({target:{value:'zzz-no-matches'}});check(!document.getElementById('guide-empty').hidden,'search empty state');
 document.getElementById('guide-clear').onclick();check(document.getElementById('guide-empty').hidden,'clear search');
 document.getElementById('guide-expand').onclick();check(document.querySelectorAll('.guide-section').every(e=>e.open),'expand all');
 document.getElementById('guide-collapse').onclick();check(document.querySelectorAll('.guide-section').every(e=>!e.open),'collapse all');
 for(const b of document.querySelectorAll('[data-copy]')){await b.onclick();check(copied===guide.sections.flatMap(s=>s.commands||[]).find(c=>c.id===b.dataset.copy).code,'exact clipboard command '+b.dataset.copy);}
 if(document.querySelectorAll('[data-copy]').length){context.navigator.clipboard.writeText=async()=>{throw Error('denied')};await document.querySelectorAll('[data-copy]')[0].onclick();check(document.getElementById('guide-copy-status').textContent.includes('manually'),'clipboard fallback');}
 run("guideQuery='quota';render()");await run('refresh()');check(document.getElementById('guide-search').value==='quota','polling preserves guide search');
 run('guideData=null;guideError=true;render()');check(document.getElementById('content').innerHTML.includes('Documentation is unavailable'),'missing document error state');
 check(requests.every(u=>u.startsWith('/api/status')||u==='/static/guide.json'||u.startsWith('/api/conversations')),'only local GET fetches');
 await run('loadGuide()');run("openGuide('mitigation-fitting')");check(document.getElementById('guide-mitigation-fitting').scrolled,'9.2 anchor renders');check(document.getElementById('content').innerHTML.includes('https://arxiv.org/abs/2012.09738'),'primary references rendered');check(run("guideReferences([{url:'javascript:alert(1)',title:'unsafe'}])")==='','unsafe reference refused');
 check(run("esc('<script>')")==='&lt;script&gt;','HTML escaped');

 // Jobs view fixture: every level, all treatments, two Batches, plus a replacement.
 const jobFixtures=[];
 for(const n of [5,10,15,20,25,30])for(let t=0;t<8;t++)for(let b=1;b<=2;b++)jobFixtures.push({key:`${n}-${t}-${b}`,target_n:n,treatment:run('jobsLabels')[t],treatment_ids:[`M${t}_fixture`],treatment_order:t,job_id:`job-${n}-${t}-${b}`,batch_id:`batch-${b}`,block:`block-${b}`,account:'labA',backend:'backend',status:'DONE',observed_at:'2026-01-01T00:00:00Z',submitted:null,queued:null,started:null,finished:null,runtime_seconds:12,reserved_seconds:150,expected_pubs:5,valid_pubs:4,invalid_pubs:1,accepted_pubs:4,validation_state:'Partial / invalid',recovery:'Original',updated_at:'2026-01-02T00:00:00Z',ledger_states:['COMPLETED'],validation:[],provenance:[],events:[]});
 jobFixtures.push({...jobFixtures.at(-1),key:'replacement',job_id:'replacement',recovery:'Replacement'});
 context.jobFixtures=jobFixtures;run("state.execution_jobs=jobFixtures;active='IBM Jobs';render()");
 check(document.querySelectorAll('[data-jobs-level]').length===6,'six Jobs n-tabs');
 for(const n of [5,10,15,20,25,30]){run(`jobsLevel=${n};render()`);check(run('selectedJobs().length')===(n===30?17:16),'all executions for n='+n);}
 run("jobsTreatment='3';updateJobsTable()");check(run('selectedJobs().length')===2,'treatment filter preserves batches');
 run("jobsTreatment='';jobsKind='Replacement';updateJobsTable()");check(run('selectedJobs().length')===1,'replacement filter');
 run("jobsKind='';jobsQuery='job-30-0';updateJobsTable()");check(run('selectedJobs().length')===2,'job ID search');
 run("jobsQuery='';jobsStatus='RUNNING';updateJobsTable()");check(document.getElementById('jobs-body').innerHTML.includes('No matching'),'empty result');
 run("jobsStatus='';jobsSort='job_id';jobsDirection=-1;updateJobsTable()");check(run("selectedJobs()[0].job_id")==='replacement','descending sort');
 check(run('jobTime(null)')==='N/A'&&run('jobValue(null)')==='N/A','missing fields N/A');
 check(!run('jobCell(jobFixtures[0],"valid_pubs")').includes('FINAL'),'validation not FINAL');
 check(run('jobDetail(jobFixtures[0])').includes('Reserved quota (s), not usage'),'reserved usage separate');
 const idButton=document.querySelectorAll('[data-job-copy]').at(-1);context.navigator.clipboard.writeText=async s=>{copied=s;};await idButton.onclick();check(copied===idButton.dataset.jobCopy,'copy exact job/batch ID');
 check(run('jobColumns.length')===16,'sixteen requested columns');
 check(document.getElementById('content').innerHTML.includes('jobs-scroll'),'scrollable white table container');

 check(run("workflowDiagram('System Architecture')").includes('UNKNOWN'),'diagram reconciliation branch');
 console.log(`${checks} frontend DOM contract checks passed (not browser rendering).`);
})().catch(e=>{console.error(e);process.exitCode=1;});