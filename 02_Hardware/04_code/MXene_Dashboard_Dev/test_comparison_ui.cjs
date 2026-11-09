const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const host={isConnected:true,innerHTML:''};
let payload={state:'BLOCKED_REFERENCE',version:1,published_at:'2026-11-09T00:00:00Z',
  observations:840,n_levels:[5,10,15,20,25,30],treatment_labels:{M0_Baseline:'None'},
  warning:'Descriptive variability only',blockers:{D:'M0 mismatch',E:'Missing method',F:'Missing method'},
  figures:[{name:'Figure_C_105_PUB_convergence.png',filename:'Figure_C_105_PUB_convergence.png'}],
  tables:[{name:'comparison_summary.csv',filename:'comparison_summary.csv'}],
  summary:[{scope:'pooled',treatment:'M0_Baseline',median_SSE:'570.1580954461737',median_RMSE:'1.46958',sample_SD_SSE:'12',median_SSE_reduction_percent:'0'}]};
const ctx=vm.createContext({document:{getElementById:()=>host},fetch:async()=>({ok:true,json:async()=>payload}),
  esc:x=>String(x??''),encodeURIComponent,Number,String,Object,Error});
vm.runInContext(fs.readFileSync('static/comparison.js','utf8'),ctx);
(async()=>{
  await vm.runInContext('loadCanonicalComparison()',ctx);
  assert.match(host.innerHTML,/v001/);assert.match(host.innerHTML,/840 accepted/);
  assert.match(host.innerHTML,/570\.15810/);assert.match(host.innerHTML,/M0 mismatch/);
  assert.equal((host.innerHTML.match(/<img /g)||[]).length,1);
  assert.match(host.innerHTML,/comparison_summary.csv\?download=true/);
  payload={...payload,state:'COMPLETE',version:2,figure_methods:{C:{label:'Ordinary pooled-prefix bootstrap, 3,000 replicates',seed:1013,caveat:'No job dependence adjustment'},E:{label:'Matched n-level cluster bootstrap, 10,000 replicates',seed:1013,caveat:'Conditional sensitivity'}},treatment_colors:{M1_DD:'#0072B2'},method_summary:'Matched n-level sensitivity, not inferential CIs',
    figures:['Figure_C_105_PUB_convergence.png','Figure_D_105_PUB_triple_positioning.png',
      'Figure_E_M0_cluster_bootstrap_delta_SSE.png','Figure_F_M0_cluster_bootstrap_reduction.png'].map(name=>({name,filename:name})),
    contrasts:[{treatment:'M1_DD',label:'DD',delta_SSE:'-109.617925368067',delta_sensitivity_low:'-245.2',delta_sensitivity_high:'-8.1',
                reduction_percent:'19.2258824778',reduction_sensitivity_low:'1.8',reduction_sensitivity_high:'37.4'}]};
  await vm.runInContext('loadCanonicalComparison()',ctx);
  assert.equal((host.innerHTML.match(/<img /g)||[]).length,4);
  assert.match(host.innerHTML,/Ordinary pooled-prefix bootstrap, 3,000 replicates/);assert.match(host.innerHTML,/Matched n-level cluster bootstrap, 10,000 replicates/);assert.match(host.innerHTML,/seed 1013/);assert.match(host.innerHTML,/color:#0072B2/);assert.match(host.innerHTML,/v002/);assert.match(host.innerHTML,/not inferential CIs/);
  assert.match(host.innerHTML,/-109\.6179/);assert.match(host.innerHTML,/19\.2259/);
  assert.doesNotMatch(host.innerHTML,/unavailable until its original method/);
  payload={state:'MISSING_ARTIFACT',reason:'No canonical publication marker',figures:[],tables:[]};
  await vm.runInContext('loadCanonicalComparison()',ctx);
  assert.match(host.innerHTML,/MISSING_ARTIFACT/);assert.equal((host.innerHTML.match(/<img /g)||[]).length,0);
  assert.doesNotMatch(host.innerHTML,/04_code\/results/);
  console.log('PASS: canonical PNG, exact CSV-derived display, version/time, download and explicit missing states (DOM harness).');
})().catch(error=>{console.error(error);process.exitCode=1;});
