// Offline DOM and WCAG color-pair tests; no provider or live browser operations.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const nodes={},calls=[];let approved=false,checks=0;
class E{constructor(){this.value='';this.children=[];this.disabled=false;}set innerHTML(s){this.html=s;for(const m of s.matchAll(/id="([^"]+)"/g))nodes[m[1]]=new E();}appendChild(e){this.children.push(e);}replaceChildren(){this.children=[];}}
const ctx=vm.createContext({console,document:{querySelectorAll:()=>[],createElement:()=>new E()},window:{confirm:()=>approved},navigator:{clipboard:{writeText:async()=>{}}},$:id=>nodes[id],esc:x=>String(x),render:()=>{},assistantRequest:async(path,body)=>{calls.push({path,body});if(body.action==='config')return {accounts:['labA'],treatments:['M0_Baseline']};if(body.action==='preview')return {request_id:'a'.repeat(32),scope_id:'scope'};return {state:body.action==='preflight'?'READY':'QUEUED'};}});
const run=s=>vm.runInContext(s,ctx),check=(v)=>{assert(v);checks++;};
run(fs.readFileSync('static/manual_execution.js','utf8'));
(async()=>{
const html=run('independentManualMarkup()');new E().innerHTML=html;
for(const label of ['Existing IBM Job ID','Registered Campaign','New Manual Execution','Advanced Settings','Advanced Manual Command Console','Preview','Preflight','Confirm / Execute'])check(html.includes(label));
await run('bindIndependentManual()');check(calls.length===1&&calls[0].body.action==='config');
nodes['im-target'].onchange({target:{value:'new'}});check(run('independentDraft.operation')==='batch_submit');
run("independentDraft.account='labA';independentDraft.label='mock';independentDraft.pubs='1';independentDraft.job_id='stale'");
check(run('imRequest().job_id')==='');await run("imAction('preview')");check(!nodes['im-preflight'].disabled&&nodes['im-confirm'].disabled);
await run("imAction('preflight')");check(!nodes['im-confirm'].disabled);
const n=calls.length;await run("imAction('confirm')");check(calls.length===n);
approved=true;await run("imAction('confirm')");check(calls.at(-1).body.approved===true);check(nodes['im-confirm'].disabled);
run('imInvalidate()');check(nodes['im-confirm'].disabled&&nodes['im-preflight'].disabled);
const css=fs.readFileSync('static/research_workspace.css','utf8');
const luminance=h=>{const rgb=h.match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;};
for(const [fg,bg] of [['1F2937','FFFFFF'],['475569','FFFFFF'],['1F2937','E2E8F0'],['475569','E2E8F0'],['FFFFFF','102033'],['047857','FFFFFF'],['B45309','FFFFFF'],['B91C1C','FFFFFF'],['0369A1','FFFFFF']]){check((Math.max(luminance(fg),luminance(bg))+.05)/(Math.min(luminance(fg),luminance(bg))+.05)>=4.5);check(css.includes('#'+fg));}
for(const state of [':disabled',':hover',':focus-visible','details[open]','aria-pressed=true'])check(css.includes(state));
console.log(`${checks} manual UI/contrast assertions passed`);
})().catch(e=>{console.error(e);process.exitCode=1;});
