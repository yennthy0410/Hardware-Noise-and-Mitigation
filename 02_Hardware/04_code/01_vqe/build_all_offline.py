#!/usr/bin/env python3
"""Study B VQE publication-style figures/tables from stored CSV only (no IBM)."""
from pathlib import Path
import argparse, shutil, json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

BACKENDS=['ibm_fez','ibm_kingston','ibm_marrakesh']
COLORS={'ibm_fez':'#1976B5','ibm_kingston':'#198B43','ibm_marrakesh':'#D65A31'}
PARAMS=['Rs','L','Rct','Q1','n1','Q2','n2']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelsize':11,
 'axes.titlesize':12,'axes.titleweight':'bold','axes.labelweight':'bold',
 'axes.linewidth':1.7,'xtick.direction':'in','ytick.direction':'in',
 'xtick.major.width':1.25,'ytick.major.width':1.25,'legend.frameon':False,
 'savefig.facecolor':'white','figure.facecolor':'white','image.interpolation':'nearest'})

def canvas(title,x='',y=''):
 f,a=plt.subplots(figsize=(5.25,5.25));a.set_box_aspect(1)
 a.set(title=title,xlabel=x,ylabel=y);a.tick_params(top=True,right=True,width=1.25)
 for s in a.spines.values():s.set_linewidth(1.7)
 return f,a

def save(fig,path):
 path.parent.mkdir(parents=True,exist_ok=True)
 fig.tight_layout(pad=1.45)
 fig.savefig(path,dpi=600,bbox_inches='tight',pad_inches=.17,facecolor='white')
 plt.close(fig)

def read(path):
 return pd.read_csv(path) if path.is_file() else None

def num(d,key):return pd.to_numeric(d[key],errors='coerce')

def label(b):return str(b).replace('ibm_','').capitalize()

def style_legend(ax):ax.legend(fontsize=8,loc='best',frameon=False)

def lines(df,x,y,out,title,xlab,ylab,group='backend'):
 if df is None or not {x,y}.issubset(df.columns):return False
 d=df.copy();d[y]=num(d,y);d=d.dropna(subset=[y]);
 if d.empty:return False
 fig,ax=canvas(title,xlab,ylab)
 if group in d.columns:
  for b in BACKENDS:
   sub=d[d[group]==b]
   if sub.empty:continue
   sub=sub.sort_values(x) if pd.api.types.is_numeric_dtype(sub[x]) else sub
   ax.plot(sub[x],sub[y],'-o',markersize=4,lw=1.65,color=COLORS[b],label=label(b))
  style_legend(ax)
 else:ax.plot(d[x],d[y],'-o',lw=1.7,color='#1976B5',markersize=4)
 if not pd.api.types.is_numeric_dtype(d[x]):ax.tick_params(axis='x',labelrotation=35)
 save(fig,out);return True

def grouped(df,metric,out,title,ylabel,group='backend'):
 if df is None or metric not in df:return False
 d=df.copy();d[metric]=num(d,metric);d=d.dropna(subset=[metric]);
 if d.empty:return False
 fig,ax=canvas(title,'IBM QPU',ylabel)
 names=[];values=[];colors=[]
 for b in BACKENDS:
  q=d[d[group]==b][metric].to_numpy(float) if group in d else []
  if len(q):names.append(label(b));values.append(q);colors.append(COLORS[b])
 if not values:plt.close(fig);return False
 centers=np.arange(len(names));
 for i,(vals,c) in enumerate(zip(values,colors)):
  ax.scatter(np.full(len(vals),i)+np.linspace(-.12,.12,len(vals)),vals,s=25,color=c,zorder=3)
  ax.plot([i-.18,i+.18],[np.median(vals)]*2,lw=2.2,color='#303030')
 ax.set_xticks(centers,names,fontsize=9);ax.margins(x=.2)
 save(fig,out);return True

def heatmap(df,row,col,val,out,title,cbar_label='Value',symmetric=False):
 if df is None or not {row,col,val}.issubset(df.columns):return False
 d=df.copy();d[val]=num(d,val)
 p=d.pivot_table(index=row,columns=col,values=val,aggfunc='mean',sort=False)
 if p.empty or not np.isfinite(p.to_numpy(float)).any():return False
 fig,ax=canvas(title,str(col).replace('_',' ').title(),str(row).replace('_',' ').title())
 values=p.to_numpy(float)
 if symmetric:
  vmax=float(np.nanmax(np.abs(values))) or 1
  im=ax.imshow(values,cmap='RdBu_r',norm=TwoSlopeNorm(vcenter=0,vmin=-vmax,vmax=vmax),aspect='auto',interpolation='nearest')
 else:im=ax.imshow(values,cmap='Blues',aspect='auto',interpolation='nearest')
 ax.set_xticks(np.arange(len(p.columns)),[str(c) for c in p.columns],rotation=0,ha='center',fontsize=9)
 ax.set_yticks(np.arange(len(p.index)),[str(c) for c in p.index],fontsize=8)
 ax.set_xticks(np.arange(-.5,len(p.columns),1),minor=True)
 ax.set_yticks(np.arange(-.5,len(p.index),1),minor=True)
 ax.grid(which='minor',color='#ffffff',lw=1.2);ax.tick_params(which='minor',bottom=False,left=False)
 ax.set_aspect('auto');ax.set_box_aspect(1)
 for i in range(values.shape[0]):
  for j in range(values.shape[1]):
   z=values[i,j]
   if np.isfinite(z):
    txt=f'{z:+.2f}' if symmetric else f'{z:.2g}'
    color='white' if symmetric and abs(z)>.58*vmax else '#202020'
    ax.text(j,i,txt,ha='center',va='center',fontsize=7.2,color=color)
 cb=fig.colorbar(im,ax=ax,fraction=.043,pad=.035,shrink=.83);cb.set_label(cbar_label,fontsize=9)
 save(fig,out);return True

def table(df,out,title,max_rows=14,max_cols=7):
 if df is None or df.empty:return False
 d=df.iloc[:max_rows,:max_cols].copy()
 def fmt(v):
  if pd.isna(v):return '—'
  if isinstance(v,(float,np.floating)):
   if abs(v)>0 and (abs(v)<.001 or abs(v)>=10000):return f'{v:.2e}'
   return f'{v:.4g}'
  s=str(v);return s if len(s)<22 else s[:19]+'…'
 vals=[[fmt(v) for v in row] for row in d.itertuples(index=False,name=None)]
 fig,ax=canvas(title);ax.axis('off')
 t=ax.table(cellText=vals,colLabels=[str(c).replace('_',' ')[:20] for c in d.columns],loc='center',cellLoc='center',colLoc='center')
 t.auto_set_font_size(False);t.set_fontsize(7.3);t.scale(1,1.55)
 for (r,c),cell in t.get_celld().items():
  cell.set_edgecolor('#d5dce3');cell.set_linewidth(.55)
  if r==0:cell.set_facecolor('#e8eef5');cell.set_text_props(weight='bold',color='#17283b')
  else:cell.set_facecolor('#ffffff' if r%2 else '#f5f7fa')
 save(fig,out);return True

def bar_scenarios(d,metric,out):
 if d is None or not {'backend','scenario',metric}.issubset(d.columns):return False
 d=d.copy();d[metric]=num(d,metric)
 p=d.pivot_table(index='scenario',columns='backend',values=metric,aggfunc='mean',sort=False)
 if p.empty:return False
 fig,ax=canvas(f'Controlled noise: {metric}','Noise scenario',f'{metric} (Ω²)' if metric=='SSE' else f'{metric} (Ω)')
 x=np.arange(len(p));w=.23
 for j,b in enumerate(BACKENDS):
  if b in p:ax.plot(x,p[b],marker='o',lw=1.6,markersize=4,color=COLORS[b],label=label(b))
 ax.set_xticks(x,[str(v).replace('_',' ') for v in p.index],rotation=42,ha='right',fontsize=8)
 style_legend(ax);save(fig,out);return True

def build(root,archive_legacy=True):
 v=root/'01_vqe';made=[];errors=[]
 sections=['01_hardware_noise_characterization','02_fixed_isa_repeatability','03_repeated_transpilation_reproducibility',
 '04_controlled_noise_scenarios','05_noise_to_eis_error_propagation']
 if archive_legacy:
  for sec in sections:
   figure=v/sec/'figure';legacy=v/sec/'legacy_figure'
   if figure.exists():
    for png in figure.rglob('*.png'):
     rel=png.relative_to(figure);dest=legacy/rel;dest.parent.mkdir(parents=True,exist_ok=True)
     if not dest.exists():shutil.move(str(png),str(dest))
     else:png.unlink()
 def emit(path,fn,*args,**kwargs):
  try:
   idx={lines:3,grouped:2,heatmap:4,table:1,bar_scenarios:2}[fn]
   if fn(*(list(args[:idx])+[path]+list(args[idx:])),**kwargs):made.append(str(path.relative_to(root)))
  except Exception as e:
   plt.close('all')
   errors.append(f'{path}: {type(e).__name__}: {e}')
 # 01 Hardware noise: compare actual recorded calibrations
 sec=v/sections[0];out=sec/'output';fig=sec/'figure'
 d=read(out/'05_cross_QPU_noise_benchmark.csv')
 if d is not None:
  for metric,title,unit in [
   ('median_readout_error_pct','Readout error','Readout error (%)'),
   ('median_1Q_gate_error_pct','Single-qubit gate error','1Q gate error (%)'),
   ('median_2Q_gate_error_pct','Two-qubit gate error','2Q gate error (%)'),
   ('median_T1_us','T1 coherence time','T1 (μs)'),('median_T2_us','T2 coherence time','T2 (μs)')]:
   emit(fig/f'{metric}.png',grouped,d,metric,title,unit)
  emit(fig/'hardware_calibration_summary_table.png',table,d,'Hardware calibration summary')
 # 02, 03 repeated hardware data
 for secname,csv in [(sections[1],'00_all_fixed_QPU_runs.csv'),(sections[2],'00_all_unfixed_QPU_runs.csv')]:
  sec=v/secname;out=sec/'output';fig=sec/'figure';d=read(out/csv)
  if d is None:continue
  for metric,unit in [('SSE','SSE (Ω²)'),('RMSE','RMSE (Ω)')]:
   emit(fig/f'{metric.lower()}_by_run.png',lines,d,'run',metric,f'{secname.replace("_"," ").title()}: {metric}','Run index',unit)
   emit(fig/f'{metric.lower()}_cross_qpu.png',grouped,d,metric,f'Cross-QPU {metric}',unit)
  for param in PARAMS:
   key=param+'_drift_pct'
   if key in d:emit(fig/f'parameter_drift_{param}.png',grouped,d,key,f'{param} deviation from ideal',f'{param} deviation (%)')
  drift=read(out/'01_parameters_all_QPU.csv')
  if drift is not None:
   for b in BACKENDS:
    s=drift[drift.backend==b]
    emit(fig/b/'parameter_deviation_heatmap.png',heatmap,s,'run','parameter','relative_drift_pct',f'{label(b)}: parameter deviation','Deviation (%)',symmetric=True)
  exp=read(out/'02_expectations_all_QPU.csv')
  if exp is not None:
   for b in BACKENDS:
    s=exp[exp.backend==b]
    emit(fig/b/'expectation_drift_heatmap.png',heatmap,s,'run','observable','drift',f'{label(b)}: expectation drift','Δ⟨Z⟩',symmetric=True)
  for f in ['07_cross_QPU_consistency_table.csv','11_cross_QPU_job_benchmark.csv']:
   emit(fig/f.replace('.csv','.png'),table,read(out/f),f.replace('.csv','').replace('_',' ').title())
 # transpilation specific
 sec=v/sections[2];out=sec/'output';fig=sec/'figure'
 for f in ['isa_layout_diversity_summary.csv','isa_circuit_complexity_table.csv']:
  emit(fig/f.replace('.csv','.png'),table,read(out/f),f.replace('.csv','').replace('_',' ').title())
 d=read(out/'isa_circuit_complexity_table.csv')
 if d is not None:
  for metric in ['isa_depth','twoq_gate_count']:
   emit(fig/f'{metric}_by_run.png',lines,d,'run',metric,f'ISA {metric.replace("_"," ")}','Run index',metric)
 # 04 controlled noise, per backend
 sec=v/sections[3];out=sec/'output';fig=sec/'figure'
 d=read(out/'cross_QPU_noise_scenario_comparison.csv')
 for metric in ['SSE','RMSE']:
  emit(fig/f'noise_{metric.lower()}_cross_qpu.png',bar_scenarios,d,metric)
 for b in BACKENDS:
  o=out/b;f=fig/b
  for filename in ['scenario_fitting_metrics.csv','scenario_eis_parameters.csv','scenario_expectations.csv',
   'parameter_drift_summary_table.csv','expectation_drift_summary_table.csv','noise_scenario_fitting_summary_table.csv']:
   d=read(o/filename)
   if filename.endswith('summary_table.csv'):emit(f/filename.replace('.csv','.png'),table,d,filename.replace('_',' ').replace('.csv','').title())
   if d is None:continue
   if filename=='scenario_eis_parameters.csv':
    for key in ['Rs','Rct','Q1','n1','Q2','n2']:
     if key in d:emit(f/f'{key}_by_noise_scenario.png',lines,d,'scenario',key,f'{label(b)}: {key} under noise','Noise scenario',key,group='__none__')
   if filename=='scenario_expectations.csv':
    x=next((x for x in ['logical_qubit','qubit','observable'] if x in d),None)
    y=next((x for x in ['expectation','value','measured_expectation'] if x in d),None)
    if x and y:emit(f/'expectation_by_scenario.png',heatmap,d,'scenario',x,y,f'{label(b)}: expectations','⟨Z⟩',symmetric=True)
 # 05 error propagation
 sec=v/sections[4];out=sec/'output';fig=sec/'figure'
 d=read(out/'quantum_to_eis_propagation_summary.csv')
 if d is not None:
  for metric,unit in [('delta_SSE','ΔSSE (Ω²)'),('impedance_RMSE_vs_ideal','Impedance RMSE (Ω)'),
   ('expectation_MAE','Expectation MAE'),('parameter_normalized_MAE','Parameter normalized MAE')]:
   emit(fig/f'{metric}_by_scenario.png',lines,d,'scenario',metric,metric.replace('_',' ').title(),'Noise scenario',unit)
  emit(fig/'error_propagation_summary_table.png',table,d,'Noise-to-EIS propagation summary')
 d=read(out/'parameter_shapley_delta_SSE.csv')
 if d is not None:
  for b in BACKENDS:
   emit(fig/b/'parameter_sse_attribution.png',heatmap,d[d.backend==b],'scenario','parameter','shapley_delta_SSE',f'{label(b)}: parameter SSE attribution','ΔSSE (Ω²)',symmetric=True)
 log={'generated_png_count':len(made),'generated_png':made,'errors':errors,
      'notes':'All new charts/tables use a square 5.25-inch canvas, 600 DPI, consistent borders and typography. Previous PNG preserved in legacy_figure. No QPU calls.'}
 (root/'publication_rebuild_report.json').write_text(json.dumps(log,indent=2),encoding='utf-8')
 print('Generated:',len(made),'PNG; errors:',len(errors))
 for e in errors[:15]:print('ERROR:',e)
 return len(made),errors

def findroot():
 for p in Path(__file__).resolve().parents:
  if (p/'01_vqe'/'02_fixed_isa_repeatability'/'output').is_dir() and (p/'04_code').is_dir():return p
 raise FileNotFoundError('02_Hardware root not found')

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path);parser.add_argument('--keep-legacy-in-figure',action='store_true')
 a=parser.parse_args();project_root=a.root.resolve() if a.root else findroot();build(project_root,not a.keep_legacy_in_figure)
 from plot_eis_spectra_publication import build as build_spectra
 build_spectra(project_root)
 from plot_fixed_isa_benchmark_publication import build as build_fixed_benchmarks
 build_fixed_benchmarks(project_root)
 from plot_unfixed_noise_propagation_publication import build as build_other_benchmarks
 build_other_benchmarks(project_root)
 print('All figures regenerated. See publication_rebuild_report.json and the figure folders.')
