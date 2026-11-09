// Focused DOM harness, not native-browser rendering.
const fs=require('fs'),vm=require('vm'),assert=require('assert');let checks=0;
const ids={},panels=[],requests=[];
class E{constructor(){this.children=[];this.value='';this.hidden=false;this.dataset={};this.textContent='';}set innerHTML(s){for(const m of s.matchAll(/\bid="([^"]+)"/g)){ids[m[1]]=new E();ids[m[1]].hidden=m[0].includes(' hidden');}if(s.includes('id="chat-panel" hidden'))ids['chat-panel'].hidden=true;}appendChild(e){this.children.push(e);return e;}replaceChildren(...x){this.children=x;}focus(){}scrollIntoView(){}setAttribute(){} }
const text=e=>e.textContent+e.children.map(text).join(' ');
const ctx=vm.createContext({console,active:'Results',level:5,render(){},$:id=>ids[id],document:{body:new E(),createElement:()=>new E(),querySelectorAll:q=>q==='[data-interpretation]'?panels:[]},window:{addEventListener(){}},sessionStorage:{getItem(){return null},setItem(){}},fetch:async(url,options)=>{requests.push({url,payload:JSON.parse(options?.body||'{}')});return {ok:true,status:200,headers:{get:()=> '2'},json:async()=>({answer:'Verified short result',rows:[]})};}});
const run=s=>vm.runInContext(s,ctx),check=(value,label)=>{assert(value,label);checks++;};
(async()=>{
 run(fs.readFileSync('static/chat.js','utf8'));run('initChat();openChat()');ids['chat-input'].value='Analyze n=5';await run('sendChat()');check(text(ids['chat-history']).includes('Verified short result'),'current answer');check(requests[0].payload.history.length===0,'quick has no history');
 ids['chat-min'].onclick();check(ids['chat-history'].children.length===0,'close clears answer');run('openChat()');check(ids['chat-input'].value==='','reopen clears input');
 ids['chat-input'].value='Compare DD and TREX';await run('sendChat()');check(!text(ids['chat-history']).includes('Analyze n=5'),'next question replaces previous');check(requests.every(r=>r.url==='/api/ai/analyze'),'quick never calls conversation persistence');
 let release;ctx.fetch=()=>new Promise(resolve=>{release=()=>resolve({ok:true,headers:{get:()=> '2'},json:async()=>({answer:'Late answer'})});});ids['chat-input'].value='Slow question';const pending=run('sendChat()');ids['chat-min'].onclick();run('openChat()');release();await pending;check(!text(ids['chat-history']).includes('Late answer'),'closed request cannot repopulate widget');
 run(fs.readFileSync('static/gallery.js','utf8'));for(const key of ['figure-a','figure-b']){const p=new E();p.dataset={interpretation:key,n:'5'};panels.push(p);}
 ctx.fetch=async()=>({ok:true,json:async()=>({n:5,available:true,compact:['None: 10 ohm. DD: 4 ohm. Descriptive reduction: 60%.'],summaries:[{text:'Detailed quantitative evidence'}],warning:'No significance claim',source:{sha256:'must-not-display'}})});
 await run('loadInterpretations()');const detailA=panels[0].children.find(x=>x.children.some(y=>y.textContent==='Show detailed analysis'));const detailB=panels[1].children.find(x=>x.children.some(y=>y.textContent==='Show detailed analysis'));
 check(!detailA.open&&!detailB.open,'both collapsed by default');detailA.open=true;detailA.ontoggle();check(!detailB.open,'independent expansion');check(text(detailA).includes('Detailed quantitative evidence'),'detail retained');check(!text(panels[0]).includes('must-not-display'),'source metadata hidden');
 await run('loadInterpretations()');check(panels[0].children.some(x=>x.open),'expansion persists through refresh');check(!panels[1].children.some(x=>x.open),'other remains collapsed');
 const container=new E();ctx.safeHost=container;run('scientificText(safeHost,"<img src=x onerror=alert(1)> **bold** `code`")');check(text(container).includes('<img'),'HTML stays text');
 console.log(`${checks} assistant/interpretation UI checks passed (DOM harness only).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
