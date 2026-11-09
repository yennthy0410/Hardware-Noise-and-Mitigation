#!/usr/bin/env python3
"""Offline publication figures for Study B VQE sections 03–05; no IBM calls."""
from pathlib import Path
import argparse, shutil, json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib.ticker import MaxNLocator

QPUS=['ibm_fez','ibm_kingston','ibm_marrakesh']
COLORS=['#277DA1','#43AA8B','#F3722C']
PARAMS=['Rs','L','Rct','Q1','n1','Q2','n2']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.titlesize':12,'axes.titleweight':'bold',
 'axes.labelsize':11,'axes.labelweight':'bold','axes.linewidth':1.7,'xtick.direction':'in',
 'ytick.direction':'in','xtick.major.width':1.2,'ytick.major.width':1.2,
 'legend.frameon':False,'figure.facecolor':'white','savefig.facecolor':'white'})

def save(fig,p):
 p.parent.mkdir(parents=True,exist_ok=True);fig.savefig(p,dpi=600,bbox_inches='tight',pad_inches=.22);plt.close(fig)

def axnew(title,x='',y='',size=(5.6,5.6)):
 fig,ax=plt.subplots(figsize=size);ax.set(title=title,xlabel=x,ylabel=y)
 ax.tick_params(top=True,right=True)
 for s in ax.spines.values():s.set_linewidth(1.7)
 return fig,ax

def archive(folder,keep):
 if keep:return
 for p in folder.rglob('*.png'):
  rel=p.relative_to(folder); dest=folder.parent/'legacy_figure'/rel;dest.parent.mkdir(parents=True,exist_ok=True)
  if not dest.exists():shutil.move(p,dest)
  else:p.unlink()

def heat(df,row,col,value,path,title,unit,rows=None,cols=None,annot=True,percent=False):
 p=df.pivot_table(index=row,columns=col,values=value,aggfunc='mean',sort=False)
 if rows is not None:p=p.reindex(index=rows)
 if cols is not None:p=p.reindex(columns=cols)
 vals=p.to_numpy(float)
 if not np.isfinite(vals).any():return False
 bound=max(float(np.nanmax(np.abs(vals))),1e-12)
 h=max(4.5,min(8.0,2.0+.33*len(p)));w=max(5.7,min(9.5,2.0+.85*len(p.columns)))
 # Scenario names are long: allocate room and wrap labels instead of clipping/overlap.
 if str(col)=='scenario':w=max(w,8.4)
 fig,ax=axnew(title,'Noise scenario' if str(col)=='scenario' else str(col).replace('_',' ').title(),str(row).replace('_',' ').title(),(w,h))
 im=ax.imshow(vals,cmap='RdBu_r',norm=TwoSlopeNorm(vmin=-bound,vcenter=0,vmax=bound),aspect='auto')
 labels=[('Combined\ncalibrated' if str(x).lower()=='combined_calibrated' else str(x).replace('_','\n')) if str(col)=='scenario' else str(x) for x in p.columns]
 ax.set_xticks(range(len(p.columns)),labels,rotation=35 if len(p.columns)>7 else 0,ha='right' if len(p.columns)>7 else 'center',fontsize=9 if str(col)=='scenario' else 8)
 ax.set_yticks(range(len(p)),[str(x) for x in p.index],fontsize=8)
 ax.set_xticks(np.arange(-.5,len(p.columns),1),minor=True);ax.set_yticks(np.arange(-.5,len(p),1),minor=True)
 ax.grid(which='minor',color='white',linewidth=1.1);ax.tick_params(which='minor',bottom=False,left=False)
 if annot and vals.size<=110:
  for i in range(vals.shape[0]):
   for j in range(vals.shape[1]):
    v=vals[i,j]
    if np.isfinite(v):ax.text(j,i,f'{v:+.1f}' if percent else (f'{v:+.2e}' if abs(v)>=100 or (0<abs(v)<.001) else f'{v:+.3f}'),ha='center',va='center',fontsize=6.6,color='white' if abs(v)>.63*bound else '#20252b')
 cb=fig.colorbar(im,ax=ax,fraction=.035,pad=.03,shrink=.86);cb.set_label(unit)
 fig.tight_layout(pad=1.1);save(fig,path);return True

def boxplot(df,metric,path,title):
 fig,ax=axnew(title,'IBM QPU',f'{metric} ({"Ω²" if metric=="SSE" else "Ω"})',(6.4,5.6))
 groups=[pd.to_numeric(df.loc[df.backend==q,metric],errors='coerce').dropna().to_numpy(float) for q in QPUS]
 bp=ax.boxplot(groups,positions=np.arange(3),widths=.42,patch_artist=True,showfliers=False,
 medianprops={'color':'#222','linewidth':2},boxprops={'linewidth':1.5})
 for patch,c in zip(bp['boxes'],COLORS):patch.set_facecolor(c);patch.set_alpha(.19);patch.set_edgecolor(c)
 for i,(v,c) in enumerate(zip(groups,COLORS)):
  ax.scatter(i+np.linspace(-.10,.10,len(v)),v,color=c,s=32,edgecolor='white',linewidth=.6,zorder=4)
  ax.text(i,1.015,f'n={len(v)}',transform=ax.get_xaxis_transform(),ha='center',fontsize=8)
 ax.set_xticks(range(3),['Fez','Kingston','Marrakesh']);ax.grid(axis='y',alpha=.15);fig.tight_layout(pad=1.4);save(fig,path)

def table(df,path,title,columns=None,width=12):
 if columns is not None:df=df[columns]
 n=len(df);height=max(2.2,.46*(n+1)+.85)
 fig,ax=plt.subplots(figsize=(width,height));ax.axis('off')
 data=[]
 for row in df.itertuples(index=False,name=None):
  data.append([f'{v:.4g}' if isinstance(v,(float,np.floating)) and pd.notna(v) else ('—' if pd.isna(v) else str(v)) for v in row])
 headers=[str(x).replace('_',' ') for x in df.columns]
 widths=[max(9,len(h)) for h in headers];s=sum(widths);widths=[x/s for x in widths]
 t=ax.table(cellText=data,colLabels=headers,colWidths=widths,cellLoc='center',bbox=[0,.02,1,.90]);t.auto_set_font_size(False);t.set_fontsize(9 if len(headers)<=7 else 7.6)
 for (i,j),cell in t.get_celld().items():
  cell.set_edgecolor('#D7DEE5');cell.set_linewidth(.65);cell.set_facecolor('#E7EDF3' if i==0 else ('#F5F7FA' if i%2==0 else 'white'))
  if i==0:cell.set_text_props(weight='bold')
 ax.set_title(title,fontsize=13,fontweight='bold',pad=14);save(fig,path)

def scenarios_order(values):
 preferred=['ideal','noiseless','readout','measurement','gate','decoherence','combined']
 vals=list(dict.fromkeys(map(str,values)))
 return sorted(vals,key=lambda s:(next((i for i,k in enumerate(preferred) if k in s.lower()),99),s))

def scenario_lines(df,metric,path,title,ylabel):
 """Grouped bars; independent noise categories are not a continuous x-axis."""
 d=df.copy();d[metric]=pd.to_numeric(d[metric],errors='coerce');d=d.dropna(subset=[metric]);order=scenarios_order(d.scenario)
 fig,ax=axnew(title,'Noise scenario',ylabel,(7.2,5.6));x=np.arange(len(order));w=.24
 for j,(q,c) in enumerate(zip(QPUS,COLORS)):
  vals=d[d.backend==q].groupby('scenario')[metric].mean().reindex(order).to_numpy(float)
  ax.bar(x+(j-1)*w,vals,width=w,color=c,label=q.replace('ibm_','').capitalize())
 ax.set_xticks(x,[str(v).replace('_','\n') for v in order],fontsize=9)
 ax.set_xlim(-.55,len(order)-.45);ax.legend(ncol=3,loc='upper center',bbox_to_anchor=(.5,1.0),fontsize=8)
 ax.grid(axis='y',alpha=.14);ax.set_axisbelow(True)
 fig.tight_layout(pad=1.5);save(fig,path)

def bars(df,category,metric,path,title,unit,order=None):
 p=df.pivot_table(index=category,columns='backend',values=metric,aggfunc='mean',sort=False)
 if order is not None:p=p.reindex(order)
 fig,ax=axnew(title,category.replace('_',' ').title(),unit,(max(6.6,.65*len(p)+3),5.6));x=np.arange(len(p));w=.24
 for j,(q,c) in enumerate(zip(QPUS,COLORS)):
  if q in p:ax.bar(x+(j-1)*w,p[q].to_numpy(float),width=w,color=c,label=q.replace('ibm_','').capitalize())
 ax.set_xticks(x,[str(v).replace('_','\n') for v in p.index],fontsize=8);ax.legend(ncol=3,fontsize=8);ax.grid(axis='y',alpha=.14);ax.set_axisbelow(True)
 fig.tight_layout(pad=1.1);save(fig,path)


def complexity_boxplot(df,metric,path):
 fig,ax=axnew(f'Repeated transpilation | {metric.replace("_"," ")}', 'IBM QPU',metric.replace('_',' ').title(),(6.4,5.6))
 groups=[pd.to_numeric(df.loc[df.backend==q,metric],errors='coerce').dropna().to_numpy(float) for q in QPUS]
 for i,(vals,color) in enumerate(zip(groups,COLORS)):
  ax.scatter(i+np.linspace(-.10,.10,len(vals)),vals,s=35,color=color,zorder=3)
  if len(vals):ax.hlines(np.median(vals),i-.2,i+.2,color='#20252b',lw=2)
 ax.set_xticks(range(3),['Fez','Kingston','Marrakesh']);ax.grid(axis='y',alpha=.15)
 fig.tight_layout(pad=1.3);save(fig,path)

def build(root,keep=False):
 root=Path(root);base=root/'01_vqe';count=0
 def output(figfn):
  nonlocal count
  figfn();count+=1
 # 03 Unfixed: same format as fixed; no claim of measured layout sensitivity
 sec=base/'03_repeated_transpilation_reproducibility';o=sec/'output';f=sec/'figure';archive(f,keep)
 runs=pd.read_csv(o/'00_all_unfixed_QPU_runs.csv')
 for m in ['SSE','RMSE']:output(lambda m=m:boxplot(runs,m,f/f'cross_qpu_{m.lower()}_boxplot.png',f'Repeated transpilation | Cross-QPU {m}'))
 for filename,value,col,unit,ispercent in [('01_parameters_all_QPU.csv','relative_drift_pct','parameter','Relative deviation (%)',True),('02_expectations_all_QPU.csv','drift','observable','Δ⟨Z⟩',False)]:
  d=pd.read_csv(o/filename);name='parameter' if ispercent else 'expectation';cols=PARAMS if ispercent else [f'Z{i}' for i in range(7)]
  d['backend_run']=d.backend.str.replace('ibm_','',regex=False).str.capitalize()+' | R'+d.run.astype(str)
  rows=[q.replace('ibm_','').capitalize()+f' | R{i}' for q in QPUS for i in range(1,6)]
  if heat(d,'backend_run',col,value,f/f'cross_qpu_{name}_drift_heatmap.png',f'Repeated transpilation | {name.capitalize()} drift',unit,rows,cols,percent=ispercent):count+=1
  for q in QPUS:
   sub=d[d.backend==q].copy();sub['run_name']='Run '+sub.run.astype(str)
   if heat(sub,'run_name',col,value,f/q/f'{name}_drift_heatmap.png',f'{q} | {name.capitalize()} drift',unit,[f'Run {i}' for i in range(1,6)],cols,percent=ispercent):count+=1
  d['abs_drift']=pd.to_numeric(d[value],errors='coerce').abs();g=d.groupby(['backend',col],as_index=False).abs_drift.median()
  output(lambda g=g,col=col,name=name,unit=unit,cols=cols:bars(g,col,'abs_drift',f/f'cross_qpu_{name}_drift_median.png',f'Repeated transpilation | Median absolute {name} drift',unit,cols))
 consistency=pd.read_csv(o/'07_cross_QPU_consistency_table.csv');output(lambda:table(consistency,f/'cross_qpu_consistency_table.png','Repeated transpilation | Cross-QPU consistency',width=11))
 jobs=pd.read_csv(o/'11_cross_QPU_job_benchmark.csv')
 for q in QPUS:
  sub=jobs[jobs.backend==q].copy();fields=['run','job_id','submitted_at_utc','completed_at_utc','execution_time_s','qpu_usage_s']
  output(lambda sub=sub,q=q:table(sub,f/f'job_benchmark_{q}.png',f'Repeated transpilation | {q} job benchmark',fields,width=16))
 diversity=pd.read_csv(o/'isa_layout_diversity_summary.csv')
 output(lambda:table(diversity[['backend','n_runs','n_unique_layouts','n_unique_isa_structures','layout_verified']],f/'isa_layout_reproducibility_table.png','ISA and physical layout reproducibility',width=12))
 complexity=pd.read_csv(o/'isa_circuit_complexity_table.csv')
 for metric in ['isa_depth','twoq_gate_count']:
  output(lambda metric=metric:complexity_boxplot(complexity,metric,f/f'{metric}_cross_qpu.png'))
 # 04 Aer controlled noise: grouped comparison and compact 5x7 heatmaps, no 7 isolated plots
 sec=base/'04_controlled_noise_scenarios';o=sec/'output';f=sec/'figure';archive(f,keep)
 d=pd.read_csv(o/'cross_QPU_noise_scenario_comparison.csv')
 for metric,unit in [('SSE','SSE (Ω²)'),('RMSE','RMSE (Ω)')]:
  output(lambda metric=metric,unit=unit:scenario_lines(d,metric,f/f'cross_qpu_{metric.lower()}_by_noise_scenario.png',f'Controlled noise | Cross-QPU {metric}',unit))
 for q in QPUS:
  sub=o/q;dest=f/q
  ex=pd.read_csv(sub/'scenario_expectations.csv');par=pd.read_csv(sub/'scenario_eis_parameters.csv')
  order=scenarios_order(ex.scenario);ex['observable']='Z'+ex.logical_qubit.astype(str)
  if heat(ex,'scenario','observable','expectation_z',dest/'expectation_by_noise_heatmap.png',f'{q} | Expectation under noise','⟨Z⟩',order,[f'Z{i}' for i in range(7)]):count+=1
  if heat(par,'scenario','parameter','value',dest/'parameter_values_heatmap.png',f'{q} | EIS parameters under noise','Parameter value (mixed units)',order,PARAMS,annot=False):count+=1
  # Normalization is essential: different parameters have incompatible units.
  pivot=par.pivot_table(index='scenario',columns='parameter',values='value',aggfunc='mean').reindex(index=order,columns=PARAMS)
  ideal=next((s for s in order if 'ideal' in s.lower() or 'noiseless' in s.lower()),None)
  if ideal is not None:
   ref=pivot.loc[ideal].replace(0,np.nan)
   normalized=((pivot-ref)/ref.abs()*100).stack().dropna().rename('relative_drift_pct').reset_index()
   if heat(normalized,'scenario','parameter','relative_drift_pct',dest/'parameter_relative_drift_heatmap.png',f'{q} | Parameter deviation from ideal','Deviation (%)',order,PARAMS,percent=True):count+=1
  for name in ['noise_scenario_fitting_summary_table','parameter_drift_summary_table','expectation_drift_summary_table']:
   t=pd.read_csv(sub/f'{name}.csv');output(lambda t=t,name=name:table(t,dest/f'{name}.png',f'{q} | {name.replace("_"," ").capitalize()}',width=11))
 # 05 propagation: single cross-backend comparison for each metric and heatmaps
 sec=base/'05_noise_to_eis_error_propagation';o=sec/'output';f=sec/'figure';archive(f,keep)
 d=pd.read_csv(o/'quantum_to_eis_propagation_summary.csv')
 for metric,unit in [('delta_SSE','ΔSSE (Ω²)'),('impedance_RMSE_vs_ideal','Impedance RMSE (Ω)'),('expectation_MAE','Mean |Δ⟨Z⟩|'),('parameter_normalized_MAE','Normalized parameter MAE')]:
  output(lambda metric=metric,unit=unit:scenario_lines(d,metric,f/f'{metric}_cross_qpu.png',f'Noise-to-EIS propagation | {metric.replace("_"," ")}',unit))
 for metric,unit in [('delta_SSE','ΔSSE (Ω²)'),('expectation_MAE','Mean |Δ⟨Z⟩|'),('parameter_normalized_MAE','Normalized parameter MAE')]:
  if heat(d,'backend','scenario',metric,f/f'{metric}_cross_qpu_heatmap.png',f'Cross-QPU | {metric.replace("_"," ")}',unit,QPUS,scenarios_order(d.scenario),annot=True):count+=1
 shap=pd.read_csv(o/'parameter_shapley_delta_SSE.csv')
 for q in QPUS:
  sub=shap[shap.backend==q]
  if heat(sub,'scenario','parameter','shapley_delta_SSE',f/q/'parameter_sse_attribution_heatmap.png',f'{q} | Parameter contribution to ΔSSE','Shapley ΔSSE (Ω²)',scenarios_order(sub.scenario),PARAMS,annot=True):count+=1
 # Mapping Jacobian: parameter units differ, so display log10 of absolute derivative,
 # never compare raw derivative magnitudes as dimensionless amplification.
 jac_path=o/'mapping_jacobian.csv'
 if jac_path.exists():
  jac=pd.read_csv(jac_path)
  for q in QPUS:
   jj=jac[jac.backend==q].copy()
   jj['logical_qubit_label']='Z'+jj.logical_qubit.astype(str)
   jj['log10_abs_derivative']=np.log10(np.maximum(np.abs(pd.to_numeric(jj.d_parameter_d_expectation,errors='coerce')),1e-14))
   if heat(jj,'parameter','logical_qubit_label','log10_abs_derivative',f/q/'mapping_jacobian_log_magnitude.png',
           f'{q} | Mapping Jacobian magnitude','log10 |∂parameter / ∂⟨Z⟩| (parameter units)',PARAMS,[f'Z{i}' for i in range(7)],annot=True):count+=1
 # Impedance sensitivity: use dimensionless relative sensitivity, NOT raw
 # derivatives of parameters with incompatible physical units.
 sens_path=o/'impedance_sensitivity_jacobian.csv'
 if sens_path.exists():
  sens=pd.read_csv(sens_path)
  sens['dZdp']=pd.to_numeric(sens.dZreal_dp,errors='coerce')+1j*pd.to_numeric(sens.dZimag_dp,errors='coerce')
  for q in QPUS:
   ss=sens[sens.backend==q].copy()
   parfile=base/'04_controlled_noise_scenarios'/'output'/q/'scenario_eis_parameters.csv'
   if not parfile.exists():continue
   par=pd.read_csv(parfile)
   ideal=par[par.scenario.astype(str).str.lower().eq('ideal')].set_index('parameter')['value'].to_dict()
   if not all(k in ideal for k in PARAMS):continue
   fgrid=np.sort(ss.frequency_hz.unique().astype(float))
   omega=2*np.pi*fgrid
   # Same Rs-L-(Rct || CPE1)-CPE2 model as Study A.
   z=ideal['Rs']+1j*omega*ideal['L']+ideal['Rct']/(1+ideal['Rct']*ideal['Q1']*(1j*omega)**ideal['n1'])+1/(ideal['Q2']*(1j*omega)**ideal['n2'])
   # Normalized sensitivity S_i=|p_i/Z * dZ/dp_i| (dimensionless).
   fig,ax=axnew(f'{q} | Normalized impedance sensitivity','Frequency (Hz)','Normalized sensitivity (dimensionless)',(6.4,6.0))
   for param in PARAMS:
    d0=ss[ss.parameter==param].sort_values('frequency_hz')
    if len(d0)!=len(fgrid):continue
    val=np.abs(float(ideal[param])*d0.dZdp.to_numpy()/z)
    ax.plot(fgrid,np.maximum(val,1e-14),label=param,lw=1.65)
   ax.set_xscale('log');ax.set_yscale('log');ax.legend(ncol=4,fontsize=8,loc='upper center',bbox_to_anchor=(.5,1.0))
   ax.grid(alpha=.13,which='major');fig.tight_layout(pad=1.5)
   save(fig,f/q/'normalized_impedance_sensitivity_by_frequency.png');count+=1
   # Noise-induced contribution uses actual Aer parameter drift in each scenario.
   # This is a first-order complex contribution, not an additive SSE attribution.
   for scenario in ['measurement','gate','decoherence','combined_calibrated']:
    dd=par[par.scenario.astype(str).eq(scenario)].set_index('parameter')['value'].to_dict()
    if not all(k in dd for k in PARAMS):continue
    fig,ax=axnew(f'{q} | Noise-induced impedance contribution\n{scenario.replace("_"," ").title()}',
                 'Frequency (Hz)','|∂Z/∂p × Δp| (Ω)',(6.4,6.0))
    for param in PARAMS:
     d0=ss[ss.parameter==param].sort_values('frequency_hz')
     if len(d0)!=len(fgrid):continue
     delta=float(dd[param])-float(ideal[param])
     val=np.abs(d0.dZdp.to_numpy()*delta)
     ax.plot(fgrid,np.maximum(val,1e-14),label=param,lw=1.65)
    ax.set_xscale('log');ax.set_yscale('log');ax.legend(ncol=4,fontsize=8,loc='upper center',bbox_to_anchor=(.5,1.0))
    ax.grid(alpha=.13,which='major');fig.tight_layout(pad=1.5)
    save(fig,f/q/f'noise_induced_impedance_contribution_{scenario}.png');count+=1
 # Compact, reproducible manifest
 report={'generated_png_count':count,'sections':['03_repeated_transpilation_reproducibility','04_controlled_noise_scenarios','05_noise_to_eis_error_propagation'],'dpi':600,'source':'stored CSV only; no IBM jobs'}
 (root/'unfixed_noise_propagation_rebuild_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(f'Rebuilt {count} publication PNGs for unfixed, controlled noise, and noise-to-EIS propagation.')
 return count

def find_root():
 for p in Path(__file__).resolve().parents:
  if (p/'01_vqe'/'03_repeated_transpilation_reproducibility'/'output').is_dir():return p
 raise FileNotFoundError('Cannot find 02_Hardware containing stored VQE outputs')

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path);parser.add_argument('--keep-existing',action='store_true');a=parser.parse_args()
 build(a.root.resolve() if a.root else find_root(),a.keep_existing)
