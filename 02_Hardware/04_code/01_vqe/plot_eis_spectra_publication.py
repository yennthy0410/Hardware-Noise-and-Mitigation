#!/usr/bin/env python3
"""Offline Study B: individual 600-DPI Nyquist, Bode, residual, SSE and RMSE figures.
Reads archived 5-run EIS parameters; does not contact IBM or refit data.
"""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

PARAMS=['Rs','L','Rct','Q1','n1','Q2','n2']
QPUS=['ibm_fez','ibm_kingston','ibm_marrakesh']
RUN_COLORS=['#4C78A8','#F58518','#54A24B','#B279A2','#E45756']
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelsize':11,
'axes.titlesize':12,'axes.titleweight':'bold','axes.labelweight':'bold','axes.linewidth':1.7,
'xtick.direction':'in','ytick.direction':'in','xtick.major.width':1.2,'ytick.major.width':1.2,
'legend.frameon':False,'figure.facecolor':'white','savefig.facecolor':'white'})

def impedance(f, p):
    rs,L,rct,q1,n1,q2,n2=p
    w=2*np.pi*f
    return rs+1j*w*L+1/(1/rct+q1*(1j*w)**n1)+1/(q2*(1j*w)**n2)

def canvas(title,x,y):
    fig,ax=plt.subplots(figsize=(5.25,5.25))
    ax.set_box_aspect(1)
    ax.set(title=title,xlabel=x,ylabel=y)
    ax.tick_params(top=True,right=True)
    for spine in ax.spines.values():spine.set_linewidth(1.7)
    ax.grid(alpha=.13,linewidth=.6)
    return fig,ax

def save(fig,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    fig.tight_layout(pad=1.1)
    fig.savefig(path,dpi=600,bbox_inches='tight',pad_inches=.16)
    plt.close(fig)

def find_reference_file(root, filename, candidates):
    """Find existing archived reference data without changing original CSVs."""
    root=Path(root).resolve()
    project=root.parent
    paths=[base / rel for base,rel in candidates for _ in [None]]
    for path in paths:
        if path.is_file():
            print(f"Reference {filename}: {path}")
            return path
    tried="\n  ".join(str(p) for p in paths)
    raise FileNotFoundError(f"Cannot locate {filename}. Checked:\n  {tried}")

def read_reference(root):
    root=Path(root).resolve()
    project=root.parent
    eis=find_reference_file(root,'EIS14.csv',[
        (project,Path('data/EIS14/EIS14.csv')),
        (project,Path('data/EIS14.csv')),
        (root,Path('00_reference_data/EIS14.csv')),
    ])
    d=pd.read_csv(eis)
    f=d['Frequency (Hz)'].to_numpy(float)
    z=d["Z' (Ohms)"].to_numpy(float)-1j*d['-Z" (Ohms)'].to_numpy(float)
    mask=np.isfinite(f)&(f>0)&np.isfinite(z.real)&np.isfinite(z.imag)
    order=np.argsort(f[mask]);f=f[mask][order];z=z[mask][order]
    classical=find_reference_file(root,'classical_fit_parameters.csv',[
        (project,Path('01_quantum_eis_fit/01_vqe/output/classical_fit_parameters.csv')),
        (project,Path('01_quantum_eis_fit/03_compare_vqe_qaoa/output/classical_fit_parameters.csv')),
        (root,Path('00_reference_data/classical_fit_parameters.csv')),
    ])
    c=pd.read_csv(classical)
    key=next((k for k in ['parameter','parameter_name','name'] if k in c.columns),None)
    value=next((k for k in ['value','fitted_value','classical_value'] if k in c.columns),None)
    if key is None or value is None:
        raise ValueError(f'Unsupported parameter columns in {classical}: {list(c.columns)}')
    params=dict(zip(c[key].astype(str),pd.to_numeric(c[value],errors='coerce')))
    p=np.array([params[k] for k in PARAMS],float)
    if not np.isfinite(p).all():raise ValueError('Classical parameters invalid')
    return f,z,impedance(f,p)

def plot_group(root, section, backend, f,z,classic):
    input_csv=root/'01_vqe'/section/'output'/backend/'00_QPU_run_values.csv'
    if not input_csv.is_file():return 0
    d=pd.read_csv(input_csv).sort_values('run')
    if d.empty:return 0
    assert len(d)==5, f'Expected five runs: {input_csv}, found {len(d)}'
    ideal=np.array([d.iloc[0][k+'_ideal'] for k in PARAMS],float)
    spectra=[('Data',z,'#111111'),('classical',classic,'#ED6B22'),('VQE/VQA (cont.) — ideal',impedance(f,ideal),'#198B43')]
    for i,(_,r) in enumerate(d.iterrows()):
        p=np.array([r[k] for k in PARAMS],float)
        spectra.append((f'QPU run {int(r.run)}',impedance(f,p),RUN_COLORS[i]))
    output=root/'01_vqe'/section/'figure'/backend
    mode='Fixed ISA' if 'fixed_isa' in section else 'Repeated transpilation'
    specs=[('01_nyquist.png','Nyquist',"Z′ (Ω)","−Z″ (Ω)",lambda zz:(zz.real,-zz.imag)),
           ('02_bode_magnitude.png','Bode magnitude','Frequency (Hz)','|Z| (Ω)',lambda zz:(f,np.abs(zz))),
           ('03_bode_phase.png','Bode phase','Frequency (Hz)','Phase (°)',lambda zz:(f,np.angle(zz,deg=True))),
           ('04_residual_real.png','Real residual','Frequency (Hz)','Re(Zfit − Zdata) (Ω)',lambda zz:(f,(zz-z).real)),
           ('05_residual_imaginary.png','Imaginary residual','Frequency (Hz)','Im(Zfit − Zdata) (Ω)',lambda zz:(f,(zz-z).imag))]
    made=0
    for filename,kind,xlabel,ylabel,func in specs:
        fig,ax=canvas(f'{backend} — {kind} ({mode})',xlabel,ylabel)
        for name,zz,color in spectra:
            if 'residual' in filename and name=='Data':continue
            x,y=func(zz)
            if name=='Data':ax.scatter(x,y,s=21,facecolors='none',edgecolors=color,linewidths=.8,label=name,zorder=8)
            else:ax.plot(x,y,color=color,lw=2 if name in ('classical','VQE/VQA (cont.) — ideal') else 1.2,alpha=.9,label=name)
        if 'bode' in filename or 'residual' in filename:ax.set_xscale('log')
        if 'magnitude' in filename:ax.set_yscale('log')
        if 'residual' in filename:ax.axhline(0,color='#777777',ls=':',lw=.9)
        ax.legend(fontsize=7,loc='best',ncol=2)
        save(fig,output/filename);made+=1
    methods=[(name,zz) for name,zz,_ in spectra if name!='Data']
    for metric in ['SSE','RMSE']:
        vals=[float(np.sum(np.abs(zz-z)**2)) for _,zz in methods]
        if metric=='RMSE':vals=[np.sqrt(v/(2*len(z))) for v in vals]
        fig,ax=canvas(f'{backend} — {metric} ({mode})','Method',metric+(' (Ω²)' if metric=='SSE' else ' (Ω)'))
        x=np.arange(len(methods));ax.bar(x,vals,color=['#ED6B22','#198B43',*RUN_COLORS],width=.7)
        ax.set_xticks(x,['Classical','Ideal',*[f'Run {i}' for i in range(1,6)]],rotation=45,ha='right',fontsize=8)
        ax.grid(axis='y',alpha=.13);save(fig,output/f'06_{metric.lower()}.png');made+=1
    return made

def build(root):
    f,z,classic=read_reference(root)
    total=0
    for section in ['02_fixed_isa_repeatability','03_repeated_transpilation_reproducibility']:
        for backend in QPUS:
            n=plot_group(root,section,backend,f,z,classic)
            print(f'{section}/{backend}: {n} PNG')
            total+=n
    print(f'Created {total} individual PNG at 600 DPI')
    return total

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path)
    a=p.parse_args()
    root=a.root if a.root else Path(__file__).resolve().parents[2]
    build(root)
