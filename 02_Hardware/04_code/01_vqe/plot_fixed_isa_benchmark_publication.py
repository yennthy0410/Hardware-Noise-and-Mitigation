#!/usr/bin/env python3
"""Rebuild fixed-ISA benchmark figures from saved CSV only; no IBM access."""
from pathlib import Path
import argparse, shutil
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
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.titlesize':12,
'axes.titleweight':'bold','axes.labelsize':11,'axes.labelweight':'bold','axes.linewidth':1.7,
'xtick.direction':'in','ytick.direction':'in','xtick.major.width':1.2,
'ytick.major.width':1.2,'legend.frameon':False,'savefig.facecolor':'white',
'figure.facecolor':'white'})

def save(fig,path):
 path.parent.mkdir(parents=True,exist_ok=True)
 fig.savefig(path,dpi=600,bbox_inches='tight',pad_inches=.20,facecolor='white')
 plt.close(fig)

def axes(title,x,y,figsize=(5.5,5.5)):
 fig,ax=plt.subplots(figsize=figsize)
 ax.set(title=title,xlabel=x,ylabel=y)
 ax.tick_params(top=True,right=True)
 for sp in ax.spines.values():sp.set_linewidth(1.7)
 return fig,ax

def metric_box(df,metric,path):
 fig,ax=axes(f'Fixed ISA | Cross-QPU {metric}','IBM QPU',f'{metric} ({"Ω²" if metric=="SSE" else "Ω"})',(6.4,5.6))
 groups=[df.loc[df.backend==q,metric].dropna().to_numpy(float) for q in QPUS]
 bp=ax.boxplot(groups,positions=np.arange(3),widths=.43,patch_artist=True,showfliers=False,
 medianprops={'color':'#222222','linewidth':2},boxprops={'linewidth':1.5},
 whiskerprops={'linewidth':1.4},capprops={'linewidth':1.4})
 for patch,color in zip(bp['boxes'],COLORS):patch.set_facecolor(color);patch.set_alpha(.18);patch.set_edgecolor(color)
 for i,(arr,color) in enumerate(zip(groups,COLORS)):
  jitter=np.linspace(-.10,.10,len(arr))
  ax.scatter(i+jitter,arr,s=34,facecolor=color,edgecolor='white',linewidth=.6,zorder=4,label='Individual runs' if i==0 else None)
  ax.text(i,1.02,f'n={len(arr)}',transform=ax.get_xaxis_transform(),ha='center',fontsize=9,color='#555555')
 ax.set_xticks(range(3),['Fez','Kingston','Marrakesh'])
 ax.grid(axis='y',alpha=.14)
 fig.tight_layout(pad=1.4);save(fig,path)

def combined_heatmap(df,value,row,col,path,title,unit,percent=False):
 p=df.pivot_table(index=row,columns=col,values=value,aggfunc='mean')
 order=[f'{q.replace("ibm_", "").capitalize()} | R{r}' for q in QPUS for r in range(1,6)]
 p=p.reindex(order)
 if percent:p=p.reindex(columns=PARAMS)
 else:p=p.reindex(columns=[f'Z{i}' for i in range(7)])
 vals=p.to_numpy(float)
 if not np.isfinite(vals).any():raise ValueError('No finite heatmap data')
 bound=max(np.nanmax(np.abs(vals)),1e-12)
 fig,ax=axes(title,'EIS parameter' if percent else 'Observable','Backend and run',(7.9,7.0))
 im=ax.imshow(vals,cmap='RdBu_r',norm=TwoSlopeNorm(vmin=-bound,vcenter=0,vmax=bound),aspect='auto')
 ax.set_xticks(range(7),p.columns,fontsize=10)
 ax.set_yticks(range(len(p)),p.index,fontsize=9)
 ax.set_xticks(np.arange(-.5,7,1),minor=True)
 ax.set_yticks(np.arange(-.5,len(p),1),minor=True)
 ax.grid(which='minor',color='white',linewidth=1.1)
 ax.tick_params(which='minor',bottom=False,left=False)
 for i in range(vals.shape[0]):
  for j in range(vals.shape[1]):
   v=vals[i,j]
   if np.isfinite(v):
    s=f'{v:+.1f}' if percent else f'{v:+.3f}'
    ax.text(j,i,s,ha='center',va='center',fontsize=7.2,color='white' if abs(v)>.62*bound else '#20252b')
 for cut in [4.5,9.5]:ax.axhline(cut,color='#1d2939',linewidth=1.9)
 cb=fig.colorbar(im,ax=ax,fraction=.036,pad=.03,shrink=.85)
 cb.set_label(unit)
 fig.tight_layout(pad=1.0);save(fig,path)

def summary_table(df,path):
 # Full labels; intentionally rectangular rather than square.
 cols=['Metric']+QPUS
 d=df.loc[:,cols].copy()
 d.columns=['Metric','Fez','Kingston','Marrakesh']
 labels={'Mean absolute expectation drift':'Mean |expectation drift|',
 'Mean absolute parameter drift':'Mean |parameter drift|'}
 d['Metric']=d['Metric'].replace(labels)
 fig,ax=plt.subplots(figsize=(11,1.15+.53*(len(d)+1)));ax.axis('off')
 data=[]
 for _,r in d.iterrows():
  data.append([str(r.iloc[0])]+[f'{float(v):.4g}' if pd.notna(v) else '—' for v in r.iloc[1:]])
 t=ax.table(cellText=data,colLabels=list(d.columns),colWidths=[.43,.19,.19,.19],
 loc='center',cellLoc='center',bbox=[0,.02,1,.92]);t.auto_set_font_size(False);t.set_fontsize(10)
 for (i,j),cell in t.get_celld().items():
  cell.set_edgecolor('#D7DEE5');cell.set_linewidth(.7)
  cell.set_facecolor('#E7EDF3' if i==0 else ('#F5F7FA' if i%2==0 else '#FFFFFF'))
  if i==0:cell.set_text_props(weight='bold')
  if j==0:cell.set_text_props(ha='left')
 ax.set_title('Fixed ISA | Cross-QPU consistency',fontweight='bold',fontsize=14,pad=13)
 save(fig,path)

def job_table(df,folder):
 # No truncation: all 15 runs, split into one table per backend, landscape canvas.
 fields=['run','job_id','submitted_at_utc','completed_at_utc','execution_time_s','qpu_usage_s']
 for q in QPUS:
  d=df[df.backend==q].sort_values('run').copy()
  if d.empty:continue
  fig,ax=plt.subplots(figsize=(16,3.65));ax.axis('off')
  data=[]
  for _,r in d.iterrows():
   data.append([str(int(r.run)),str(r.job_id),str(r.submitted_at_utc).replace('T',' ')[:19],
    str(r.completed_at_utc).replace('T',' ')[:19],f'{r.execution_time_s:.3f}',f'{r.qpu_usage_s:.3f}'])
  t=ax.table(cellText=data,colLabels=['Run','IBM job ID','Submitted (UTC)','Completed (UTC)','Execution (s)','QPU usage (s)'],
   colWidths=[.06,.24,.23,.23,.13,.11],cellLoc='center',bbox=[0,.04,1,.86])
  t.auto_set_font_size(False);t.set_fontsize(9.3)
  for (i,j),c in t.get_celld().items():
   c.set_edgecolor('#D8E0E8');c.set_linewidth(.65)
   c.set_facecolor('#E7EDF3' if i==0 else ('#F5F7FA' if i%2==0 else '#FFFFFF'))
   if i==0:c.set_text_props(weight='bold')
  ax.set_title(f'Fixed ISA | {q} job benchmark',fontweight='bold',fontsize=14,pad=13)
  save(fig,folder/f'job_benchmark_{q}.png')

def drift_summary(params,exps,out):
 # Compact cross-QPU effect-size comparisons, one chart for all 7 parameters.
 for df,value,category,name in [(params,'relative_drift_pct','parameter','parameter'),(exps,'drift','observable','expectation')]:
  d=df.copy();d['abs_drift']=pd.to_numeric(d[value],errors='coerce').abs()
  p=d.groupby(['backend',category])['abs_drift'].median().unstack(category)
  cols=PARAMS if name=='parameter' else [f'Z{i}' for i in range(7)]
  p=p.reindex(index=QPUS,columns=cols)
  fig,ax=axes(f'Fixed ISA | Cross-QPU {name} drift',name.capitalize(),
   'Median absolute drift (%)' if name=='parameter' else 'Median |Δ⟨Z⟩|',(7.0,5.5))
  x=np.arange(len(cols));width=.24
  for j,q in enumerate(QPUS):
   ax.bar(x+(j-1)*width,p.loc[q].values,width,color=COLORS[j],label=q.replace('ibm_','').capitalize())
  ax.set_xticks(x,cols);ax.legend(ncol=3,loc='upper center',bbox_to_anchor=(.5,1.0),fontsize=8)
  ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
  fig.tight_layout(pad=1.1);save(fig,out/f'cross_qpu_{name}_drift_median.png')

def build(root):
 root=Path(root)
 sec=root/'01_vqe'/'02_fixed_isa_repeatability';output=sec/'output';fig=sec/'figure'
 d=pd.read_csv(output/'00_all_fixed_QPU_runs.csv')
 params=pd.read_csv(output/'01_parameters_all_QPU.csv')
 exps=pd.read_csv(output/'02_expectations_all_QPU.csv')
 # Remove only older benchmark files; keep the individual Nyquist/Bode/residual plots.
 for f in fig.glob('*.png'):
  dest=sec/'legacy_figure'/f.name;dest.parent.mkdir(parents=True,exist_ok=True)
  if not dest.exists():shutil.move(str(f),str(dest))
  else:f.unlink()
 for metric in ['SSE','RMSE']:metric_box(d,metric,fig/f'cross_qpu_{metric.lower()}_boxplot.png')
 for source,value,field,title,unit,percent,name in [
  (params,'relative_drift_pct','parameter','Fixed ISA | Parameter drift vs ideal','Relative deviation (%)',True,'parameter'),
  (exps,'drift','observable','Fixed ISA | Expectation drift vs ideal','Measured − ideal',False,'expectation')]:
  source=source.copy();source['backend_run']=source.backend.map(lambda q:q.replace('ibm_','').capitalize())+' | R'+source.run.astype(str)
  combined_heatmap(source,value,'backend_run',field,fig/f'cross_qpu_{name}_drift_heatmap.png',title,unit,percent)
  for q in QPUS:
   sub=source[source.backend==q].copy();sub['run_name']='Run '+sub.run.astype(str)
   # A single 5x7 heatmap per QPU, not seven separate charts.
   sub['backend_run']=sub['run_name']
   p=fig/q; p.mkdir(exist_ok=True,parents=True)
   combined_heatmap_backend(sub,value,field,p/f'{name}_drift_heatmap.png',q,unit,percent)
 drift_summary(params,exps,fig)
 summary_table(pd.read_csv(output/'07_cross_QPU_consistency_table.csv'),fig/'cross_qpu_consistency_table.png')
 job_table(pd.read_csv(output/'11_cross_QPU_job_benchmark.csv'),fig)
 print('Fixed ISA publication benchmarks rebuilt (PNG 600 DPI).')

def combined_heatmap_backend(d,value,col,path,q,unit,percent):
 p=d.pivot_table(index='run_name',columns=col,values=value,aggfunc='mean').reindex(
  index=[f'Run {i}' for i in range(1,6)],columns=PARAMS if percent else [f'Z{i}' for i in range(7)])
 vals=p.to_numpy(float);bound=max(np.nanmax(np.abs(vals)),1e-12)
 fig,ax=axes(f'{q} | {"Parameter" if percent else "Expectation"} drift',
 'Parameter' if percent else 'Observable','Run',(7.5,5.6))
 im=ax.imshow(vals,aspect='auto',cmap='RdBu_r',norm=TwoSlopeNorm(vmin=-bound,vcenter=0,vmax=bound))
 ax.set_xticks(range(7),p.columns);ax.set_yticks(range(5),p.index)
 ax.set_xticks(np.arange(-.5,7,1),minor=True);ax.set_yticks(np.arange(-.5,5,1),minor=True)
 ax.grid(which='minor',color='white',linewidth=1.1);ax.tick_params(which='minor',bottom=False,left=False)
 for i in range(5):
  for j in range(7):
   v=vals[i,j]
   if np.isfinite(v):ax.text(j,i,f'{v:+.2f}' if percent else f'{v:+.3f}',ha='center',va='center',fontsize=8,color='white' if abs(v)>.62*bound else '#222222')
 cb=fig.colorbar(im,ax=ax,fraction=.04,pad=.035);cb.set_label(unit)
 fig.tight_layout(pad=1);save(fig,path)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path)
 args=parser.parse_args()
 if args.root:root=args.root
 else:
  root=next(p for p in Path(__file__).resolve().parents if (p/'01_vqe'/'02_fixed_isa_repeatability'/'output').is_dir())
 build(root)
