const visibleText=e=>String(e.textContent||'')+(e.children||[]).map(visibleText).join(' ');
// Served frontend -> real localhost HTTP, with a lightweight DOM harness.
// This is NOT Chrome rendering certification.
const vm=require('vm'),assert=require('assert'),fs=require('fs');
const base='http://127.0.0.1:8766',storage=new Map();
class E{constructor(){this.children=[];this.value='';this.hidden=false;}set innerHTML(s){for(const m of s.matchAll(/\bid="([^"]+)"/g))this.ids[m[1]]=new E();}appendChild(e){this.children.push(e);}replaceChildren(...c){this.children=c;}focus(){}scrollIntoView(){}}
async function session(){
 const ids={},body=new E(),responses=[];let confirms=0;
 const response=await fetch(base+'/static/chat.js?v=bridge2',{cache:'no-store'}),code=await response.text();
 assert.equal(response.headers.get('cache-control'),'no-store');assert.equal(code,fs.readFileSync('static/chat.js','utf8'));
 const ctx=vm.createContext({console,active:'Overview',level:5,$:id=>ids[id],document:{createElement(){const e=new E();e.ids=ids;return e;},body,querySelectorAll:()=>[]},window:{addEventListener(){},confirm(){confirms++;throw Error('Unexpected browser confirmation');}},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},fetch:async(url,options)=>{const r=await fetch(base+url,options);responses.push({status:r.status,calls:r.headers.get('X-MXene-Ollama-Calls'),data:await r.clone().json()});return r;}});
 vm.runInContext(code,ctx);vm.runInContext('initChat()',ctx);
 for(const message of ['Show campaign progress.','Analyze n=5 and compare DD with TREX.']){
  ids['chat-input'].value=message;await vm.runInContext('sendChat()',ctx);
  const r=responses.at(-1);assert.equal(r.status,200);assert.equal(r.calls,'0');assert(r.data.answer.length>30);
  if(message.startsWith('Analyze'))assert.deepEqual(new Set(r.data.rows.map(x=>x.treatment)),new Set(['DD','TREX']));
 }
 assert.equal(confirms,0);assert(!ids['chat-history'].children.some(x=>x.textContent==='Allow local AI context for this session'));
 ctx.fetch=async()=>({status:403,ok:false,headers:{get:()=>null},json:async()=>({detail:'Explicit local-only consent required'})});ids['chat-input'].value='Show campaign progress.';await vm.runInContext('sendChat()',ctx);assert.equal(confirms,0);assert(visibleText(ids['chat-history']).includes('backend is outdated'));assert(!ids['chat-history'].children.some(x=>x.textContent==='Allow local AI context for this session'));
 return responses.map(x=>({status:x.status,ollama_calls:x.calls}));
}
(async()=>{console.log('Served JS -> real HTTP:',await session());console.log('Reloaded served JS -> real HTTP:',await session());console.log('No browser confirm; current asset verified. Actual browser rendering not tested.');})().catch(e=>{console.error(e);process.exitCode=1;});
