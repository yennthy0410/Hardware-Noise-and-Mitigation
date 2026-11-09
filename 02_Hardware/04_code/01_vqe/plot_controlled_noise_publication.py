#!/usr/bin/env python3
"""Offline, publication-focused controlled-noise figures. No QPU calls."""
from pathlib import Path
import argparse, shutil, json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

BACKENDS=['ibm_fez','ibm_kingston','ibm_marrakesh']
NAMES=['Fez','Kingston','Marrakesh']
COLORS=['#277DA1','#43AA8B','#F3722C']
PARAMS=['Rs','L','Rct','Q1','n1','Q2','n2']
SCENARIOS=['measurement','gate','decoherence','combined_calibrated']
LABELS=['Measurement','Gate','Decoherence','Combined\ncalibrated']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.titlesize':12,'axes.titleweight':'bold','axes.labelsize':11,'axes.labelweight':'bold','axes.linewidth':1.7,'xtick.direction':'in','ytick.direction':'in','savefig.facecolor':'white'})

def save(fig,path):
 path.parent.mkdir(parents=True,exist_ok=True)
 fig.savefig(path,dpi=600,bbox_inches='tight',pad_inches=.28,facecolor='white')
 plt.close(fig)

def axes(title,x='',y='',size=(7.4,5.6)):
 fig,ax=plt.subplots(figsize=size,layout='constrained');ax.set_title(title,pad=14);ax.set_xlabel(x,labelpad=9);ax.set_ylabel(y,labelpad=9)
 ax.tick_params(top=True,right=True)
 for sp in ax.spines.values():sp.set_linewidth(1.7)
 return fig,ax

def bars(df,metric,path,ylabel,title):
 fig,ax=axes(title,'Noise scenario',ylabel)
 x=np.arange(4);w=.24
 for j,(b,n,c) in enumerate(zip(BACKENDS,NAMES,COLORS)):
  d=df[df.backend.eq(b)].set_index('scenario');values=pd.to_numeric(d[metric],errors='coerce').reindex(SCENARIOS).to_numpy(float)
  ax.bar(x+(j-1)*w,values,width=w,color=c,label=n)
 ax.set_xticks(x,LABELS,fontsize=9);ax.set_xlim(-.55,3.55);ax.grid(axis='y',alpha=.14);ax.set_axisbelow(True)
 ax.legend(ncol=3,loc='upper center',bbox_to_anchor=(.5,1.0),fontsize=9)
 save(fig,path)

def drift_heatmap(df,path,title):
 vals=df.to_numpy(float);fig,ax=axes(title,'Noise scenario','EIS parameter',(8.0,5.5))
 vmax=max(np.nanmax(np.abs(vals)),1e-8)
 im=ax.imshow(vals,aspect='auto',cmap='YlOrRd',norm=Normalize(vmin=0,vmax=vmax))
 ax.set_xticks(range(4),LABELS,fontsize=9);ax.set_yticks(range(7),PARAMS)
 ax.set_xticks(np.arange(-.5,4,1),minor=True);ax.set_yticks(np.arange(-.5,7,1),minor=True)
 ax.grid(which='minor',color='white',linewidth=1.3);ax.tick_params(which='minor',bottom=False,left=False)
 for i in range(7):
  for j in range(4):
   v=vals[i,j]
   if np.isfinite(v):ax.text(j,i,f'{v:.2f}',ha='center',va='center',fontsize=9,color='white' if v>vmax*.67 else '#20252b')
 cb=fig.colorbar(im,ax=ax,shrink=.85,pad=.03);cb.set_label('Absolute parameter deviation (%)')
 save(fig,path)

def table(df,path,title):
 # Use a wide rectangular layout; wrap long headings; never truncate fields.
 df=df.copy();nrows=len(df);ncols=len(df.columns)
 formatted=[]
 for row in df.itertuples(index=False,name=None):
  formatted.append([f'{v:.4g}' if isinstance(v,(float,np.floating)) else str(v) for v in row])
 headers=[str(c).replace('_','\n') for c in df.columns]
 fig,ax=plt.subplots(figsize=(max(10,2.5*ncols),max(3.0,.58*(nrows+2))),layout='constrained');ax.axis('off')
 tab=ax.table(cellText=formatted,colLabels=headers,cellLoc='center',colLoc='center',bbox=[0,.02,1,.90])
 tab.auto_set_font_size(False);tab.set_fontsize(9)
 for (i,j),cell in tab.get_celld().items():
  cell.set_edgecolor('#D7DEE5');cell.set_linewidth(.6);cell.set_facecolor('#E7EDF3' if i==0 else ('#F5F7FA' if i%2==0 else 'white'))
  if i==0:cell.set_text_props(weight='bold',fontsize=8)
 ax.set_title(title,fontsize=12,fontweight='bold',pad=10)
 save(fig,path)

def findroot():
 for p in Path(__file__).resolve().parents:
  if (p/'01_vqe'/'04_controlled_noise_scenarios'/'output').is_dir():return p
 raise FileNotFoundError('Could not locate 02_Hardware/01_vqe/04_controlled_noise_scenarios/output')

def build(root,archive=True):
 sec=Path(root)/'01_vqe'/'04_controlled_noise_scenarios';o=sec/'output';f=sec/'figure';main=f/'01_main';supp=f/'02_supplementary';count=0
 if archive and f.exists():
  for old in list(f.rglob('*.png')):
   if 'legacy_figure' in old.parts:continue
   dest=sec/'legacy_figure'/old.relative_to(f);dest.parent.mkdir(parents=True,exist_ok=True)
   if not dest.exists():shutil.move(old,dest)
   else:old.unlink()
 d=pd.read_csv(o/'cross_QPU_noise_scenario_comparison.csv')
 for metric,unit in [('SSE','SSE (Ω²)'),('RMSE','RMSE (Ω)')]:
  bars(d,metric,main/f'0{count+1}_cross_qpu_{metric.lower()}.png',unit,f'Controlled noise | Cross-QPU {metric}');count+=1
 # Absolute drift against ideal for all seven physical parameters, not just a scalar mean.
 rows=[];expect=[]
 for b in BACKENDS:
  p=pd.read_csv(o/b/'scenario_eis_parameters.csv').pivot_table(index='scenario',columns='parameter',values='value').reindex(columns=PARAMS)
  ref=p.loc['ideal'];rel=(p-ref).abs().div(ref.abs().replace(0,np.nan))*100
  for s in SCENARIOS:
   for k in PARAMS:rows.append({'backend':b,'scenario':s,'parameter':k,'abs_drift_pct':rel.loc[s,k]})
  e=pd.read_csv(o/b/'scenario_expectations.csv').pivot_table(index='scenario',columns='logical_qubit',values='expectation_z')
  for s in SCENARIOS:
   for q in range(7):expect.append({'backend':b,'scenario':s,'observable':f'Z{q}','abs_expectation_drift':abs(e.loc[s,q]-e.loc['ideal',q])})
 rel=pd.DataFrame(rows);ex=pd.DataFrame(expect)
 for b,n in zip(BACKENDS,NAMES):
  pivot=rel[rel.backend.eq(b)].pivot(index='parameter',columns='scenario',values='abs_drift_pct').reindex(index=PARAMS,columns=SCENARIOS)
  drift_heatmap(pivot,supp/f'{b}_parameter_drift_heatmap.png',f'{n} | Parameter drift under noise');count+=1
 # Main: 7x12 heatmap, all backends and scenarios, one consolidated figure.
 big=rel.copy();big['group']=big.backend.str.replace('ibm_','',regex=False).str.capitalize()+'\n'+big.scenario.str.replace('combined_calibrated','Combined',regex=False).str.capitalize()
 mat=big.pivot(index='parameter',columns='group',values='abs_drift_pct')
 ordered=[n+'\n'+s for n in NAMES for s in ['Measurement','Gate','Decoherence','Combined']]
 mat=mat.reindex(index=PARAMS,columns=ordered)
 fig,ax=axes('Controlled noise | Cross-QPU parameter deviation','Backend and noise scenario','EIS parameter',(12.8,5.7))
 values=mat.to_numpy(float);vmax=max(np.nanmax(values),1e-8)
 im=ax.imshow(values,aspect='auto',cmap='YlOrRd',vmin=0,vmax=vmax)
 ax.set_xticks(range(12),ordered,fontsize=8);ax.set_yticks(range(7),PARAMS)
 ax.set_xticks(np.arange(-.5,12,1),minor=True);ax.set_yticks(np.arange(-.5,7,1),minor=True);ax.grid(which='minor',color='white',lw=1)
 ax.tick_params(which='minor',bottom=False,left=False)
 for i in range(7):
  for j in range(12):
   v=values[i,j]
   if np.isfinite(v):ax.text(j,i,f'{v:.1f}',ha='center',va='center',fontsize=7.5,color='white' if v>.67*vmax else '#20252b')
 cb=fig.colorbar(im,ax=ax,shrink=.84,pad=.015);cb.set_label('Absolute parameter deviation (%)')
 save(fig,main/'03_cross_qpu_parameter_drift_heatmap.png');count+=1
 # Additional main metric: relative parameter drift averaged over 7 parameters.
 g=rel.groupby(['backend','scenario'],as_index=False).abs_drift_pct.mean()
 bars(g,'abs_drift_pct',main/'04_cross_qpu_mean_parameter_drift.png','Mean absolute parameter deviation (%)','Controlled noise | Parameter drift');count+=1
 # Observable-level evidence in supplement; one compact cross-backend table for paper.
 e=ex.groupby(['backend','scenario'],as_index=False).abs_expectation_drift.mean()
 bars(e,'abs_expectation_drift',main/'05_cross_qpu_expectation_drift.png','Mean absolute Δ⟨Z⟩','Controlled noise | Expectation drift');count+=1
 summary=d[d.scenario.isin(SCENARIOS)][['backend','scenario','SSE','RMSE']].merge(g,on=['backend','scenario']).merge(e,on=['backend','scenario'])
 summary.to_csv(o/'publication_cross_qpu_summary.csv',index=False)
 rel.to_csv(o/'publication_parameter_drift_7parameters.csv',index=False)
 ex.to_csv(o/'publication_expectation_drift_7observables.csv',index=False)
 table(summary,supp/'cross_qpu_summary_table.png','Controlled noise | Cross-QPU numerical summary');count+=1
 report={'png_generated':count,'main_figures':5,'source':'stored Aer CSV, no IBM calls','dpi':600,'notes':'per-backend diagnostics retained in legacy_figure; main plots use actual seven-parameter drift'}
 (sec/'controlled_noise_publication_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print('Controlled-noise publication figures:',count,'PNG at 600 DPI')
 return count

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path);p.add_argument('--keep-existing',action='store_true');a=p.parse_args();build(a.root or findroot(),not a.keep_existing)
