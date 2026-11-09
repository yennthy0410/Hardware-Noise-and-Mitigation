/* Canonical server-rendered scientific PNGs; no frontend statistical engine. */
async function loadCanonicalComparison(){
  const host=document.getElementById('canonical-comparison');if(!host)return;
  try{
    const response=await fetch('/api/comparison');if(!response.ok)throw Error('Comparison service unavailable');
    const data=await response.json();if(!host.isConnected)return;
    const treatment=(key,label)=>{const color=(data.treatment_colors||{})[key];return /^#[0-9a-f]{6}$/i.test(color||'')?'<span style="color:'+color+'">'+esc(label)+'</span>':esc(label);};
    const url=(name,download=false)=>'/api/comparison/artifacts/'+encodeURIComponent(name)+(download?'?download=true':'');
    const names=['Figure_C_105_PUB_convergence.png','Figure_D_105_PUB_triple_positioning.png','Figure_E_M0_cluster_bootstrap_delta_SSE.png','Figure_F_M0_cluster_bootstrap_reduction.png'];
    let html='<p><strong>'+esc(data.state)+'</strong> · '+esc(data.version?'v'+String(data.version).padStart(3,'0'):'No publication')+' · '+esc(data.published_at||'')+'</p>';
    if(data.reason)html+='<p>'+esc(data.reason)+'</p>';
    if(data.n_levels)html+='<p>'+esc(data.observations)+' accepted observations; n = '+esc(data.n_levels.join(', '))+'. Treatments: '+esc(Object.values(data.treatment_labels).join(', '))+'.</p>';
    html+='<p>'+esc(data.warning||'No verified canonical comparison is available.')+'</p>';
    html+='<p>'+esc(data.method_summary||'See the active publication manifest for the supported statistical method.')+'</p>';
    for(const name of names){
      const item=(data.figures||[]).find(f=>f.name===name);const letter=name.split('_')[1];
      html+='<section><h3>Figure '+letter+'</h3>';
      const method=(data.figure_methods||{})[letter];
      if(method)html+='<p>'+esc(method.label)+'; seed '+esc(method.seed)+'. '+esc(method.caveat)+'</p>';
      html+=item?'<a href="'+url(item.filename,true)+'">Download full-resolution PNG</a><img style="display:block;width:100%;height:auto" src="'+url(item.filename)+'" alt="'+esc(name)+'">':'<p><strong>Missing / blocked artifact:</strong> '+esc((data.blockers||{})[letter]||'No verified canonical PNG')+'</p>';
      html+='</section>';
    }
    const pooled=(data.summary||[]).filter(r=>r.scope==='pooled').sort((a,b)=>Number(a.median_SSE)-Number(b.median_SSE));
    if(pooled.length){
      html+='<h3>Validated pooled descriptive statistics</h3><p>Lowest pooled median SSE: '+esc(data.treatment_labels[pooled[0].treatment])+', '+Number(pooled[0].median_SSE).toPrecision(8)+' Ω². This ranking is descriptive.</p><table><thead><tr><th>Treatment</th><th>Median SSE (Ω²)</th><th>Median RMSE (Ω)</th><th>Sample SD SSE</th><th>Median reduction vs None</th></tr></thead><tbody>';
      for(const r of pooled)html+='<tr><td>'+treatment(r.treatment,data.treatment_labels[r.treatment])+'</td><td>'+Number(r.median_SSE).toPrecision(8)+'</td><td>'+Number(r.median_RMSE).toPrecision(8)+'</td><td>'+Number(r.sample_SD_SSE).toPrecision(8)+'</td><td>'+Number(r.median_SSE_reduction_percent).toFixed(2)+'%</td></tr>';
      html+='</tbody></table>';
    }
    if((data.contrasts||[]).length){
      html+='<h3>M0 contrasts and sensitivity intervals</h3><p>Values and bounds below are read from the same canonical CSV used by E/F. Intervals describe design reweighting sensitivity, not statistical significance.</p><table><thead><tr><th>Treatment</th><th>SSE difference (&Omega;&sup2;)</th><th>95% sensitivity range</th><th>Reduction (%)</th><th>95% sensitivity range (%)</th></tr></thead><tbody>';
      for(const r of data.contrasts)html+='<tr><td>'+treatment(r.treatment,r.label)+'</td><td>'+Number(r.delta_SSE).toFixed(4)+'</td><td>['+Number(r.delta_sensitivity_low).toFixed(4)+', '+Number(r.delta_sensitivity_high).toFixed(4)+']</td><td>'+Number(r.reduction_percent).toFixed(4)+'</td><td>['+Number(r.reduction_sensitivity_low).toFixed(4)+', '+Number(r.reduction_sensitivity_high).toFixed(4)+']</td></tr>';
      html+='</tbody></table>';
    }
    html+='<h3>Supporting canonical CSV / JSON</h3><ul>';
    for(const item of data.tables||[])html+='<li><a href="'+url(item.filename,true)+'">'+esc(item.name)+'</a></li>';
    host.innerHTML=html+'</ul>';
  }catch(error){if(host.isConnected)host.textContent='Missing / unavailable canonical comparison: '+error.message;}
}
