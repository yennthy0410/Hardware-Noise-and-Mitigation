// Offline DOM contract checks; no external providers, IBM calls or browser claims.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const ids={},storage=new Map(),requests=[];let copied='',checks=0;
class E{
 constructor(){this.children=[];this.value='';this.textContent='';this.dataset={};this.hidden=false;}
 appendChild(e){this.children.push(e);return e;}replaceChildren(...children){this.children=children;}
 set innerHTML(s){for(const m of s.matchAll(/id="([^"]+)"/g))ids[m[1]]=new E();}
 setAttribute(){} focus(){}
}
const ctx=vm.createContext({console,document:{createElement:()=>new E(),body:new E(),querySelectorAll:()=>[]},
 window:{addEventListener(){}},navigator:{clipboard:{writeText:async text=>copied=text}},
 localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},sessionStorage:{getItem:()=>null,setItem(){}},
 $:id=>ids[id],setTimeout,clearTimeout,active:'AI Research Workspace',
 fetch:async(url,options)=>{requests.push({url,body:JSON.parse(options?.body||'{}')});return {ok:true,headers:{get:()=> '2'},json:async()=>({cloud_models:['cloud-a'],local_default_model:'local-a',context_window:32768,response_tokens:1800})};}});
const run=s=>vm.runInContext(s,ctx),check=(value,label)=>{assert(value,label);checks++;};
const text=e=>e.textContent+e.children.map(text).join(' ');
(async()=>{
 run(fs.readFileSync('static/chat.js','utf8'));
 check(run('agentRequestSettings().provider')==='local','local default');
 check(!run('agentRequestSettings().cloud_consent'),'cloud denied by default');
 const markup=run('agentSettingsMarkup()');new E().innerHTML=markup;
 check(markup.includes('繁體中文')&&markup.includes('简体中文')&&markup.includes('Tiếng Việt'),'language choices');
 await run('bindAgentSettings()');
 check(requests.every(r=>r.url==='/api/ai/config'),'opening settings sends no model context');
 ids['agent-provider'].onchange({target:{value:'cloud'}});
 check(!run('agentRequestSettings().cloud_consent'),'switching provider does not authorize transmission');
 ids['agent-fallback'].onchange({target:{checked:true}});
 check(!run('agentRequestSettings().cloud_fallback'),'fallback needs separate cloud consent');
 ids['agent-cloud-consent'].onchange({target:{checked:true}});
 ids['agent-fallback'].onchange({target:{checked:true}});
 check(run('agentRequestSettings().cloud_consent&&agentRequestSettings().cloud_fallback'),'explicit fallback authorization');
 ids['agent-model'].onchange({target:{value:'cloud-a'}});
 ids['agent-language'].onchange({target:{value:'zh-Hant'}});
 check(run('agentRequestSettings().language')==='zh-Hant','selected language retained');
 check(!storage.get('mxene-agent-preferences').includes('consent'),'cloud consent never persisted');
 ids['agent-cloud-consent'].onchange({target:{checked:false}});
 check(!run('agentRequestSettings().cloud_fallback'),'revoking consent revokes fallback');
 ctx.host=new E();ctx.result={answer:'**Result** <img onerror=alert(1)>',provider:'local',model:'local-a',tool_trace:[{reference:'T1',tool:'science',status:'completed'}],verified_findings:['SSE 3'],tool_evidence:[{reference:'T1',result_excerpt:'<script>unsafe</script>'}]};
 await run('renderAssistantResponse(host,result)');
 check(text(ctx.host).includes('local-a')&&text(ctx.host).includes('science'),'provider and tool status visible');
 check(text(ctx.host).includes('<script>unsafe</script>'),'retrieved text stays literal');
 await ctx.host.children.find(e=>e.textContent==='Copy response').onclick();
 check(copied===ctx.result.answer,'copy response');
 check(run('readableMath("x^20")')==='x^20','math powers are not corrupted');
 check(run('readableMath(String.raw`\\frac{SSE}{N}`)')==='(SSE)/(N)','readable fraction');
 const workspace=fs.readFileSync('static/research_workspace.js','utf8');
 check(workspace.includes('maxlength="32000"')&&workspace.includes('rows="5"'),'expandable long input');
 check(workspace.includes("e.key==='Enter'&&!e.shiftKey"),'Enter/Shift+Enter');
 check(workspace.includes("'/operator'")&&workspace.includes('watchAgentProgress(id)'),'operator separation and progress polling');
 console.log(`${checks} hybrid chat UI contract checks passed (DOM harness).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
