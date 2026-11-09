// Offline render contract: load the real canonical definitions, never a server.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const contract=JSON.parse(fs.readFileSync(0,'utf8').trim());
const state={figure_contract:contract,levels:[{target_n:30,valid:200,expected:240,missing:40,invalid:0}],artifacts:contract.required.map((name,i)=>({id:'fixture'+i,name,level:25,version:'direct'}))};
const ctx=vm.createContext({state,level:25,esc:x=>String(x),console});
vm.runInContext(fs.readFileSync('static/gallery.js','utf8'),ctx);
const run=s=>vm.runInContext(s,ctx);
assert.equal(run('galleryRows().flatMap(r=>r[1]).length'),contract.required.length);
let html=run('galleryMarkup()');
assert.match(html,/06_mitigation_three_tier_positioning-png/);
assert.equal((html.match(/<img /g)||[]).length,contract.required.length);
assert.match(html,/id="gallery-version"/);
assert.doesNotMatch(html,/Accuracy and cost/);
ctx.level=30;html=run('galleryMarkup()');
assert.match(html,/BLOCKED for n=30: 200\/240/);assert.equal((html.match(/<img /g)||[]).length,0);
console.log('PASS: shared contract renders positioning, respects optional figure/version scope, and explains n=30 block');
