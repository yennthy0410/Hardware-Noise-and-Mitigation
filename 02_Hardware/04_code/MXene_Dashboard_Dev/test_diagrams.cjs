// Geometry/data contract tests; not a browser screenshot or typography certification.
const fs=require('fs'),vm=require('vm'),assert=require('assert');let checks=0;
const circuit=JSON.parse(fs.readFileSync('state/frozen_circuit.json','utf8'));
circuit.available=true;
const labels=['None','DD','TREX','ZNE','DD+TREX','DD+ZNE','TREX+ZNE','ALL'];
const tids=['M0_Baseline','M1_DD','M2_TREX','M3_ZNE','M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL'];
const traits=[[0,0,0],[1,0,0],[0,1,0],[0,0,1],[1,1,0],[1,0,1],[0,1,1],[1,1,1]];
const state={valid:1,expected:840,levels:[{target_n:5,valid:1,expected:40}],execution_jobs:[{key:'orig',target_n:5,block:'actual-block',batch_id:'actual-batch',job_id:'original-id',expected_pubs:5,recovery:'Original',treatment_ids:[tids[0]],treatment:'None',account:'labA',backend:'actual-backend',status:'DONE',ledger_states:['COMPLETED']},{key:'rep',target_n:5,block:'actual-block',batch_id:'replacement-batch',job_id:'replacement-id',expected_pubs:1,recovery:'Replacement',treatment_ids:[tids[0]],treatment:'None',account:'labB',backend:'actual-backend',status:'DONE',ledger_states:['VALIDATED']}],recovery_summary:[{n:5,treatment:'None',valid:1,slots:[{logical_pub_slot:1,status:'Recovered',accepted_job_id:'replacement-id',execution_keys:['orig','rep']}]}]};
const ctx=vm.createContext({console,state,jobsLabels:labels,circuitData:circuit,designData:[5,10,15,20,25,30].map(n=>({target_n:n,total_requested_pubs:8*n,backend:'actual-backend',source:`n${n}/experiment_manifest.json`,shots_per_pub:4096,treatments:tids.map((id,i)=>({id,dd:traits[i][0],trex:traits[i][1],zne:traits[i][2],runtime_options:'recorded fixture options'}))})),empty:s=>s});
vm.runInContext(fs.readFileSync('static/diagrams.js','utf8'),ctx);const run=s=>vm.runInContext(s,ctx);const check=(v,m)=>{assert(v,m);checks++;};
function hits(a,b,n){const eps=.01;if(a[0]===b[0])return a[0]>n.x+eps&&a[0]<n.x+n.w-eps&&Math.max(Math.min(a[1],b[1]),n.y+eps)<Math.min(Math.max(a[1],b[1]),n.y+n.h-eps);if(a[1]===b[1])return a[1]>n.y+eps&&a[1]<n.y+n.h-eps&&Math.max(Math.min(a[0],b[0]),n.x+eps)<Math.min(Math.max(a[0],b[0]),n.x+n.w-eps);throw Error('Nonorthogonal edge');}
for(const kind of ['architecture','lifecycle','plan']){
 const g=run(`sciGraph('${kind}')`),m=run(`sciLayout(sciGraph('${kind}'))`);
 check(m.nodes.length===g.nodes.length,kind+' all graph nodes retained');
 for(const edge of m.edges)for(let i=1;i<edge.points.length;i++)for(const node of m.nodes)check(!hits(edge.points[i-1],edge.points[i],node),`${kind} ${edge.from}>${edge.to} intersects ${node.id}`);
 for(const node of m.nodes){check(node.x>=0&&node.y>=0&&node.x+node.w<m.width&&node.y+node.h<m.height,kind+' node within bounds');check(48+node.titleLines.length*21+(node.subLines.length-1)*17<node.h,'text lines fit height');}
 const svg=run(`sciWorkflow('${kind==='architecture'?'System Architecture':kind==='lifecycle'?'Campaign Lifecycle':'Execution Plan'}')`);check(svg.includes('marker-end='),'directed edges');check(svg.includes('Accessible text outline'),'accessible equivalent');
 if(kind==='plan')check(g.edges.every(e=>!e.back),'plan is DAG iteration');
 if(kind==='lifecycle')for(const id of ['authorized','quota','known','status','valid','replace','complete','six'])check(g.nodes.some(n=>n.id===id&&n.decision),'decision '+id);
}
run('sciExecutionTree()');let model=run("sciModels.get('sci-execution')");check(model.nodes.length===7,'initial six branches lazy');
run("sciExpanded.add('exec-n5');sciExecutionTree()");model=run("sciModels.get('sci-execution')");check(model.nodes.some(n=>n.subtitle==='actual-block'),'actual block present');check(!model.nodes.some(n=>n.title==='Original job'),'jobs not rendered before batch expansion');
run("sciExpanded.add('exec-n5|actual-block');sciExecutionTree()");model=run("sciModels.get('sci-execution')");check(model.nodes.filter(n=>n.title==='Runtime Batch').length===2,'original/replacement batches retained');
run("sciExpanded.add('exec-n5|actual-block|actual-batch');sciExpanded.add('exec-n5|actual-block|replacement-batch');sciExecutionTree()");model=run("sciModels.get('sci-execution')");check(model.nodes.filter(n=>/^(Original|Replacement) job$/.test(n.title)).length===2,'two real executions');
run("sciExpanded.add('exec-job|orig');sciExpanded.add('exec-job|rep');sciExecutionTree()");model=run("sciModels.get('sci-execution')");const slots=model.nodes.filter(n=>n.title.includes('slot 1'));check(slots.length===2,'same logical slot linked to both attempts');check(slots.filter(n=>n.kind==='success').length===1,'only accepted replacement highlighted');
run("sciExpanded.delete('exec-n5');sciExecutionTree()");check(run("sciModels.get('sci-execution').nodes.length")===7,'collapse removes lazy children');
run("sciExpanded.add('design-n5');sciDesignTree()");model=run("sciModels.get('sci-design')");check(model.nodes.filter(n=>n.kind==='pub').length===8,'eight treatment branches');check(new Set(model.nodes.filter(n=>n.kind==='pub').map(n=>n.color)).size===8,'consistent distinct treatment colors');
const packed=run('sciCircuitColumns(circuitData)');check(packed.length===circuit.operations.length,'all QPY instructions retained');
for(let i=0;i<packed.length;i++){const p=packed[i],o=circuit.operations[i];check(p.index===i&&p.gate===o.gate&&JSON.stringify(p.qubits)===JSON.stringify(o.qubits)&&JSON.stringify(p.params)===JSON.stringify(o.params)&&JSON.stringify(p.clbits)===JSON.stringify(o.clbits),'exact instruction '+i);}
for(const q of circuit.active_qubits){const wire=packed.filter(o=>o.qubits.includes(q));check(wire.every((o,i)=>!i||o.column>wire[i-1].column),'physical wire ordering '+q);}
const maxcol=Math.max(...packed.map(o=>o.column));let rendered=[];
for(let fold=0;fold<Math.ceil((maxcol+1)/10);fold++){const html=run(`sciCircuitFold=${fold};sciCircuit()`);rendered.push(...[...html.matchAll(/data-gate-index="(\d+)"/g)].map(m=>Number(m[1])));check(html.includes('Fit all')&&html.includes('Full screen'),'circuit controls');}
check(rendered.length===packed.length&&new Set(rendered).size===packed.length,'every gate rendered once across folds');
check(run('sciTreatmentMatrix()').match(/class="sci-activation dd"/g).length===4,'DD enabled four');check(run('sciTreatmentMatrix()').match(/class="sci-activation trex"/g).length===4,'TREX enabled four');check(run('sciTreatmentMatrix()').match(/class="sci-activation zne"/g).length===4,'ZNE enabled four');
const before=JSON.stringify(ctx.designData);run("sciTreatmentFilter='dd';sciTreatmentMatrix()");check(JSON.stringify(ctx.designData)===before,'filter does not mutate configuration');
const controlChecks=async()=>{
 const handlers={},attrs={},feedback={textContent:''};let fullscreen=0,rendered=0;
 const frame={requestFullscreen:async()=>{fullscreen++;ctx.document.fullscreenElement=frame;}};
 const svg={dataset:{sciSvg:'sci-architecture'},getBoundingClientRect:()=>({width:900,height:600}),setAttribute:(k,v)=>attrs[k]=v,addEventListener:(k,f)=>handlers[k]=f,setPointerCapture(){},closest:()=>frame};
 const controls=['fit','read','in','out','full'].map(action=>({dataset:{sciAction:action,sciId:'sci-architecture'}}));
 const node={dataset:{sciOwner:'sci-execution',sciNode:'exec-n5'}};
 ctx.render=()=>rendered++;
 ctx.document={querySelector(){},querySelectorAll:s=>s==='[data-sci-svg]'?[svg]:s==='[data-sci-node]'?[node]:s.includes('data-sci-id=')?controls:[],getElementById:id=>id.startsWith('sci-feedback')?feedback:null,exitFullscreen:async()=>{ctx.document.fullscreenElement=null;}};
 run('sciWorkflow("System Architecture");sciBind()');const width=()=>Number(attrs.viewBox.split(' ')[2]);let initial=width();await controls[2].onclick();check(width()<initial,'zoom in');initial=width();await controls[3].onclick();check(width()>initial,'zoom out');
 await controls[0].onclick();check(width()===run("sciModels.get('sci-architecture').width"),'fit all bounds');await controls[1].onclick();check(width()<=1100,'readable view');
 const beforePan=attrs.viewBox;handlers.pointerdown({clientX:100,clientY:100,pointerId:1,target:{closest:()=>null}});handlers.pointermove({clientX:150,clientY:120});handlers.pointerup();check(attrs.viewBox!==beforePan,'pointer panning');
 initial=width();handlers.wheel({ctrlKey:true,deltaY:-1,preventDefault(){}});check(width()<initial,'modified wheel zoom');
 const oldBox=attrs.viewBox;handlers.keydown({key:'ArrowRight',target:svg,preventDefault(){}});check(attrs.viewBox!==oldBox,'keyboard panning');
 await controls[4].onclick();check(fullscreen===1&&ctx.document.fullscreenElement===frame,'fullscreen requested');await controls[4].onclick();check(ctx.document.fullscreenElement===null,'fullscreen exit');
 frame.requestFullscreen=async()=>{throw Error('blocked')};await controls[4].onclick();check(feedback.textContent.includes('unavailable'),'fullscreen denial honest');
 const expanded=run("sciExpanded.has('exec-n5')");node.onkeydown({key:'Enter',preventDefault(){}});check(run("sciExpanded.has('exec-n5')")!==expanded&&rendered===1,'keyboard lazy branch toggle');
 ctx.window={innerWidth:375};const columns=run('sciCircuitColumns(circuitData)');const folds=Math.ceil((Math.max(...columns.map(o=>o.column))+1)/4);let indices=[];
 for(let fold=0;fold<folds;fold++){const html=run(`sciCircuitFold=${fold};sciCircuit()`);indices.push(...[...html.matchAll(/data-gate-index="(\d+)"/g)].map(m=>Number(m[1])));}
 check(indices.length===circuit.operations.length&&new Set(indices).size===indices.length,'mobile folded views preserve all instructions');
 console.log(`${checks} diagram geometry/data/control assertions passed (not browser rendering).`);
};
controlChecks().catch(e=>{console.error(e);process.exitCode=1;});
