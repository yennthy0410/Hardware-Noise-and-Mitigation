#!/usr/bin/env python3
"""Offline, non-destructive migration of Study B from original 1.zip."""
from pathlib import Path
import zipfile, shutil, io, csv, json, argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

SRC='MXene-Quantum-EIS-Hardware/experiments/HEA_VQE/'
BASE=SRC+'01_simulator_to_qpu_hardware/results/EIS14/'
MAP={
 '04_noise_characterization':'01_hardware_noise_characterization',
 '02_hardware_repeatability':'02_fixed_isa_repeatability',
 '03_transpilation_layout_sensitivity':'03_repeated_transpilation_reproducibility',
 '05_controlled_noise_scenarios':'04_controlled_noise_scenarios',
 '06_quantum_to_eis_error_propagation':'05_noise_to_eis_error_propagation',
 '01_run_qpu':'00_raw_qpu_jobs',
}
SECTIONS=['01_hardware_noise_characterization','02_fixed_isa_repeatability','03_repeated_transpilation_reproducibility','04_controlled_noise_scenarios','05_noise_to_eis_error_propagation','06_mitigation_benchmark_M0_M7','07_accuracy_cost_tradeoff','08_summary']
COMPARE=['01_hardware_repeatability','02_transpilation_reproducibility','03_noise_sensitivity','04_error_propagation','05_mitigation_M0_M7','06_accuracy_cost','07_cross_qpu_consistency','08_summary']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.linewidth':1.6,'axes.labelweight':'bold','axes.titleweight':'bold','savefig.dpi':600})
COL={'ibm_fez':'#0072B2','ibm_kingston':'#009E73','ibm_marrakesh':'#D55E00'}
def save(fig,path):
 path.parent.mkdir(parents=True,exist_ok=True); fig.savefig(path,dpi=600,bbox_inches='tight',facecolor='white');plt.close(fig)
def axes(title,xlabel,ylabel):
 fig,ax=plt.subplots(figsize=(6.5,6.5));ax.set_title(title,pad=12);ax.set_xlabel(xlabel);ax.set_ylabel(ylabel);ax.grid(alpha=.22);return fig,ax
def numeric(df,candidates):
 for c in candidates:
  if c in df.columns:
   v=pd.to_numeric(df[c],errors='coerce')
   if v.notna().any():return c,v
 return None,None
def plots(root,log):
 v=root/'01_vqe'
 for section,filename in [('02_fixed_isa_repeatability','00_all_fixed_QPU_runs.csv'),('03_repeated_transpilation_reproducibility','00_all_unfixed_QPU_runs.csv')]:
  f=v/section/'output'/filename
  if not f.exists():continue
  d=pd.read_csv(f);log.append(f'{section} columns: {list(d.columns)}')
  bc=next((c for c in ['backend','qpu','backend_name','QPU'] if c in d),None)
  metric=next((c for c in d if 'rmse' in c.lower()),None)
  if metric is None:metric=next((c for c in d if 'sse' in c.lower()),None)
  if bc is None or metric is None:continue
  d[metric]=pd.to_numeric(d[metric],errors='coerce'); d=d.dropna(subset=[metric])
  if d.empty:continue
  fig,ax=axes('Hardware execution variability', 'Run index','RMSE (Ohm)' if 'rmse' in metric.lower() else 'SSE (Ohm²)')
  for b,g in d.groupby(bc):
   vals=g[metric].to_numpy(); ax.plot(range(1,len(vals)+1),vals,marker='o',linewidth=1.8,label=b,color=COL.get(str(b)))
  ax.legend(frameon=False);save(fig,v/section/'figure'/'hardware_run_variability.png');log.append(f'PLOT {section}: {len(d)} rows, {metric}')
  fig,ax=axes('Cross-QPU execution variability','IBM QPU','RMSE (Ohm)' if 'rmse' in metric.lower() else 'SSE (Ohm²)')
  groups=[g[metric].to_numpy() for _,g in d.groupby(bc)]; names=[str(k) for k,_ in d.groupby(bc)]
  ax.boxplot(groups,tick_labels=[s.replace('ibm_','') for s in names],patch_artist=False);save(fig,v/section/'figure'/'cross_qpu_variability.png')
 # plot controlled noise only when real numeric fields present
 s=v/'04_controlled_noise_scenarios'/'output'/'cross_QPU_noise_scenario_comparison.csv'
 if s.exists():
  d=pd.read_csv(s);log.append(f'controlled_noise columns: {list(d.columns)}')
  x=next((c for c in d if 'scenario' in c.lower()),None)
  y=next((c for c in d if 'rmse' in c.lower()),None)
  if y is None:y=next((c for c in d if 'sse' in c.lower()),None)
  if x and y:
   d[y]=pd.to_numeric(d[y],errors='coerce');d=d.dropna(subset=[y]);
   if not d.empty:
    b=next((c for c in ['backend','qpu','backend_name'] if c in d),None)
    fig,ax=axes('Controlled noise scenarios','Noise scenario','RMSE (Ohm)' if 'rmse' in y.lower() else 'SSE (Ohm²)')
    if b:
     for name,g in d.groupby(b):ax.plot(g[x].astype(str),g[y],marker='o',label=name,color=COL.get(str(name)))
     ax.legend(frameon=False)
    else:ax.plot(d[x].astype(str),d[y],marker='o')
    ax.tick_params(axis='x',labelrotation=35);save(fig,v/'04_controlled_noise_scenarios'/'figure'/'controlled_noise_comparison.png')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True,help='Original 1.zip');ap.add_argument('--destination',type=Path,default=Path('02_Hardware'));ap.add_argument('--include-raw',action='store_true',help='Include all original EIS14 QPU raw artifacts');a=ap.parse_args()
 if not a.source.is_file():ap.error(f'Missing archive: {a.source}')
 root=a.destination.resolve();root.mkdir(parents=True,exist_ok=True)
 for alg in ('01_vqe','02_qaoa'):
  for section in SECTIONS:
   for sub in ('output','figure'):(root/alg/section/sub).mkdir(parents=True,exist_ok=True)
 for section in COMPARE:
  for sub in ('output','figure'):(root/'03_compare_vqe_qaoa'/section/sub).mkdir(parents=True,exist_ok=True)
 (root/'04_code'/'legacy_vqe').mkdir(parents=True,exist_ok=True)
 manifest=[]; skipped=[]
 with zipfile.ZipFile(a.source) as z:
  for item in z.infolist():
   name=item.filename
   if item.is_dir():continue
   dest=None
   if name.startswith(BASE):
    rel=name[len(BASE):];first,_,tail=rel.partition('/')
    if first in MAP:
     target=MAP[first]
     if first=='01_run_qpu':
      if a.include_raw:dest=root/'01_vqe'/'00_raw_qpu_jobs'/tail
      else:skipped.append(name)
     else:
      bucket='figure' if Path(tail).suffix.lower()=='.png' else 'output'
      dest=root/'01_vqe'/target/bucket/tail
   elif name.startswith(SRC+'01_simulator_to_qpu_hardware/scripts/') and name.endswith('.py'):
    dest=root/'04_code'/'legacy_vqe'/Path(name).name
   elif name.startswith(SRC+'03_DD_TREX_ZNE_mitigation/scripts/') and name.endswith('.py'):
    dest=root/'04_code'/'legacy_vqe'/('mitigation_'+Path(name).name)
   if dest is not None:
    dest.parent.mkdir(parents=True,exist_ok=True)
    with z.open(item) as inp,open(dest,'wb') as out:shutil.copyfileobj(inp,out)
    manifest.append({'source':name,'destination':str(dest.relative_to(root)),'bytes':item.file_size})
 pd.DataFrame(manifest).to_csv(root/'migration_manifest.csv',index=False)
 logs=[];plots(root,logs)
 (root/'migration_log.txt').write_text('\n'.join(logs)+'\n',encoding='utf-8')
 (root/'README.md').write_text('''# Study B: offline reorganization from original 1.zip\n\n- `01_vqe`: original EIS14 VQE results reorganized by research question.\n- `02_qaoa`: empty, intentional; QAOA hardware runs have not been completed.\n- `03_compare_vqe_qaoa`: empty until matched QAOA hardware data exist.\n- `04_code/legacy_vqe`: original scripts retained for audit; these are **legacy scripts** and may require further path adaptations before re-execution. Do not run IBM submission scripts merely to generate figures.\n- `migration_manifest.csv`: exact provenance of every copied file.\n- `migration_log.txt`: plot data column checks.\n\nThe migration preserves original CSV/JSON values. `figure/` contains newly regenerated 600-DPI PNGs where a suitable source table exists.\n\n`00_raw_qpu_jobs` is populated only with `--include-raw`. The original 1.zip remains the immutable provenance archive otherwise.\n\n`03_repeated_transpilation_reproducibility` is deliberately named to avoid claiming physical-layout sensitivity from identical layouts.\n\nMitigation M0-M7 is a separate hardware benchmark from Aer controlled-noise scenarios. No QAOA or mitigation data are fabricated.\n''',encoding='utf-8')
 print(f'Completed: {len(manifest)} files; {len(list(root.rglob("*.png")))} PNG figures; {root}')
if __name__=='__main__':main()
