#!/usr/bin/env python3
"""Rebuild THREE multi-panel hardware characterization figures from stored CSV.
No IBM connection, no file moves, no raw-data modifications.
"""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

BACKENDS=['ibm_fez','ibm_kingston','ibm_marrakesh']
NAMES=['Fez','Kingston','Marrakesh']
COLORS=['#2B7FA2','#F28E2B','#709269']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.titlesize':12,
 'axes.titleweight':'bold','axes.labelsize':11,'axes.labelcolor':'#263b58',
 'axes.edgecolor':'#cbd5e1','axes.linewidth':1.0,'xtick.color':'#43536d',
 'ytick.color':'#43536d','figure.facecolor':'white','savefig.facecolor':'white'})

def find_hw():
    script=Path(__file__).resolve()
    for p in [Path.cwd(),*Path.cwd().parents,*script.parents]:
        hw=p/'02_Hardware' if (p/'02_Hardware').is_dir() else p
        csv=hw/'01_vqe/01_hardware_noise_characterization/output/05_cross_QPU_noise_benchmark.csv'
        if csv.is_file():return hw
    raise FileNotFoundError('Cannot find 05_cross_QPU_noise_benchmark.csv; use --csv to specify it.')

def panel(ax,df,key,title,ylabel,fmt):
    vals=pd.to_numeric(df[key],errors='coerce').to_numpy(float)
    x=np.arange(3)
    for i,(v,c) in enumerate(zip(vals,COLORS)):
        if np.isfinite(v):ax.bar(i,v,width=.61,color=c,zorder=3)
    maxv=np.nanmax(vals) if np.any(np.isfinite(vals)) else 1
    ax.set_ylim(0,maxv*1.25 if maxv>0 else 1)
    ax.set_xticks(x,NAMES)
    ax.set_ylabel(ylabel,labelpad=8)
    ax.set_title(title,pad=12)
    ax.grid(axis='y',color='#e5eaf0',linewidth=.8,zorder=0)
    ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False);ax.spines['right'].set_visible(False)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
    for i,v in enumerate(vals):
        if np.isfinite(v):
            ax.annotate(fmt.format(v),(i,v),xytext=(0,6),textcoords='offset points',
                        ha='center',va='bottom',fontsize=10.5,fontweight='bold',color='#1c2f4c')
    ax.set_xlim(-.55,2.55)

def make(df,out,name,title,panels,ncols,figsize):
    nrows=int(np.ceil(len(panels)/ncols))
    fig,axes=plt.subplots(nrows,ncols,figsize=figsize,layout='constrained',squeeze=False)
    for ax,spec in zip(axes.flat,panels):panel(ax,df,*spec)
    for ax in list(axes.flat)[len(panels):]:ax.axis('off')
    fig.suptitle(title,fontsize=19,fontweight='bold',color='#1c2f4c')
    path=out/name
    fig.savefig(path,dpi=600,bbox_inches='tight',pad_inches=.18)
    plt.close(fig)
    print('Saved:',path)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,help='Project root or 02_Hardware directory')
    ap.add_argument('--csv',type=Path,help='Explicit input benchmark CSV')
    ap.add_argument('--out',type=Path,help='Explicit output folder')
    a=ap.parse_args()
    hw=(a.root/'02_Hardware' if a.root and (a.root/'02_Hardware').is_dir() else a.root) if a.root else find_hw()
    section=hw/'01_vqe/01_hardware_noise_characterization'
    csv=a.csv or section/'output/05_cross_QPU_noise_benchmark.csv'
    if not csv.is_file():raise FileNotFoundError(csv)
    raw=pd.read_csv(csv)
    required=['backend','median_readout_error_pct','median_readout_asymmetry_pct',
      'median_T1_us','median_T2_us','median_circuit_over_T2',
      'median_1Q_gate_error_pct','median_2Q_gate_error_pct',
      'median_T2_over_1Q_gate','median_T2_over_2Q_gate']
    missing=[c for c in required if c not in raw.columns]
    if missing:raise ValueError('Missing columns: '+', '.join(missing))
    df=raw.drop_duplicates('backend').set_index('backend').reindex(BACKENDS)
    if df.isna().all(axis=1).any():raise ValueError('Missing at least one IBM backend in CSV')
    out=a.out or section/'figure'
    out.mkdir(parents=True,exist_ok=True)
    make(df,out,'01_measurement_noise.png','Measurement Noise Characterization',[
      ('median_readout_error_pct','Readout Assignment Error','Median readout error (%)','{:.4f}'),
      ('median_readout_asymmetry_pct','Readout Asymmetry','Median asymmetry (%)','{:.4f}')],2,(13.8,5.3))
    make(df,out,'02_gate_error_and_coherence_budget.png','Native Gate Error and Coherence Budget',[
      ('median_1Q_gate_error_pct','Single-Qubit Gate Error','Median calibration error (%)','{:.5f}'),
      ('median_2Q_gate_error_pct','Two-Qubit Gate Error','Median calibration error (%)','{:.4f}'),
      ('median_T2_over_1Q_gate','Single-Qubit Coherence-to-Gate Ratio','Median T2 / 1Q gate duration','{:.1f}'),
      ('median_T2_over_2Q_gate','Two-Qubit Coherence-to-Gate Ratio','Median T2 / 2Q gate duration','{:.1f}')],2,(13.8,9.2))
    make(df,out,'03_decoherence_and_circuit_exposure.png','Decoherence and Circuit Exposure',[
      ('median_T1_us','Energy Relaxation','Median T1 (µs)','{:.1f}'),
      ('median_T2_us','Transverse Coherence','Median T2 (µs)','{:.1f}'),
      ('median_circuit_over_T2','Circuit Coherence Exposure','Circuit duration / T2','{:.6f}')],3,(16.2,5.4))
    print('Done: 3 multi-panel PNGs at 600 DPI; original files unchanged.')
if __name__=='__main__':main()
