// Real served JS + HTTP with simulated DOM, not browser rendering.
const vm=require('vm'),assert=require('assert');const base='http://127.0.0.1:8766';
class E{constructor(){this.children=[];this.value='';this.textContent='';}set innerHTML(s){for(const m of s.matchAll(/\bid="([^"]+)"/g))this.ids[m[1]]=new E();}appendChild(e){this.children.push(e);}replaceChildren(...c){this.children=c;}focus(){}setAttribute(){}scrollIntoView(){}}
const text=e=>e.textContent+e.children.map(text).join(' ');const chips=e=>[...(e.onclick?[e]:[]),...e.children.flatMap(chips)];
async function request(path,method='GET',body){const r=await fetch(base+path,{method,headers:{'Content-Type':'application/json','X-MXene-AI':'local-only'},...(body?{body:JSON.stringify(body)}:{})});assert(r.ok,await r.clone().text());return r.json();}
async function session(storage){const ids={};let confirmations=0,renamed='Workspace UI acceptance '+Date.now(),renderPromise;
 const ctx=vm.createContext({console,empty:x=>x,active:'AI Research Workspace',level:5,$:id=>ids[id],document:{createElement:()=>new E(),querySelectorAll:()=>[]},window:{addEventListener(){},prompt:()=>renamed,confirm(){confirmations++;return true;}},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},fetch:(url,opts)=>fetch(base+url,opts),render(){return renderPromise=paint();}});
 const run=s=>vm.runInContext(s,ctx);
 for(const file of ['chat.js','manual_workspace.js','research_workspace.js'])run(await (await fetch(base+'/static/'+file,{cache:'no-store'})).text());
 async function paint(){const host=new E();host.ids=ids;host.innerHTML=run('workspaceMarkup()');await run('bindResearchWorkspace()');}
 run("workspaceView='manager';managerView='auto'");await paint();return {run,ids,renamed,confirmations:()=>confirmations,finish:()=>renderPromise};}
(async()=>{const storage=new Map(),created=[];try{const css=await(await fetch(base+'/static/research_workspace.css')).text();assert(css.includes('#workspace-messages{overflow:auto;flex:1'));assert(css.includes('#workspace-command-panel{flex:0 0 auto'));assert(css.includes('.command-group')); 
 const ui=await session(storage);await ui.run('workspaceNew()');const a=ui.run('workspaceId');created.push(a);
 const prompts=chips(ui.ids['workspace-suggestions']);assert(prompts.length>0);assert(!prompts.some(e=>e.textContent.includes('Run Batch')));assert(text(ui.ids['workspace-suggestions']).includes('AUTO Control'));assert(ui.run('workspaceMarkup()').includes('Python Auto Manager Commands'));assert(ui.run('workspaceMarkup()').indexOf('workspace-command-panel')>ui.run('workspaceMarkup()').indexOf('workspace-manager-pane'));assert.equal((ui.run('workspaceMarkup()').match(/id="workspace-command-panel"/g)||[]).length,1);assert(ui.run('workspaceMarkup()').includes('Single-PUB Execution'));
 const operational=prompts.find(e=>e.textContent.startsWith('Run Auto Manager - '));assert(operational);await operational.onclick();assert.equal(ui.ids['workspace-input'].value,'Run Auto Manager');assert.equal((await request('/api/conversations/'+a)).turns.length,0);await prompts[0].onclick();assert.equal(ui.ids['workspace-input'].value,prompts[0].textContent.split(' - ')[0]);assert.equal((await request('/api/conversations/'+a)).turns.length,0);
 ui.ids['workspace-input'].value='Analyze n=5 and compare DD with TREX.';await ui.run('workspaceSend()');assert(text(ui.ids['workspace-messages']).includes('median SSE'));
 assert(chips(ui.ids['workspace-suggestions']).length>=3);
 await ui.ids['workspace-rename'].onclick();assert.equal((await request('/api/conversations/'+a)).title,ui.renamed);
 await ui.run('workspaceNew()');const b=ui.run('workspaceId');created.push(b);assert(!text(ui.ids['workspace-messages']).includes('median SSE'));
 ui.ids['workspace-input'].value='Analyze Rct at n=10.';await ui.run('workspaceSend()');
 const button=ui.ids['workspace-list'].children.find(x=>x.textContent===ui.renamed);assert(button);await button.onclick();
 ui.ids['workspace-input'].value='What about RMSE?';await ui.run('workspaceSend()');const saved=await request('/api/conversations/'+a);assert.equal(saved.turns.length,2);assert.deepEqual(saved.turns[1].context.research_scope.levels,[5]);
 const reloaded=await session(storage);assert.equal(reloaded.run('workspaceId'),a);assert(chips(reloaded.ids['workspace-suggestions']).length>=13);assert(text(reloaded.ids['workspace-messages']).includes('median RMSE'));assert(!text(reloaded.ids['workspace-messages']).includes('audit_ref'));
 await reloaded.ids['workspace-delete'].onclick();await reloaded.finish();assert.equal(reloaded.confirmations(),1);assert.equal((await request('/api/conversations/'+b)).turns.length,1);
 console.log('PASS: served workspace create/send/sidebar/open/rename/refresh/follow-up/confirmed delete over real HTTP; separate chat scopes verified. DOM harness only.');
 }finally{for(const id of created)await request('/api/conversations/'+id,'DELETE',{});}
})().catch(e=>{console.error(e);process.exitCode=1;});
