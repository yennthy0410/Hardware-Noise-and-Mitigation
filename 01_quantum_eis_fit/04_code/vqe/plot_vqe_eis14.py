"""EIS14-only publication figures. Offline plotting; never connects to IBM.
Run from anywhere: python 04_code/vqe/plot_vqe_eis14.py
Optional: --classical path/to/classical_fitted_spectrum.csv
"""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
STUDY = HERE.parents[1]  # 01_quantum_eis_fit
PROJECT = STUDY.parent    # MXene QPU Hardware
VQE = STUDY / '01_vqe'

def locate_data():
    candidates = [PROJECT/'data'/'EIS14'/'EIS14.csv', PROJECT/'data'/'EIS14.csv',
                  PROJECT/'data'/'EIS14'/'EIS14', STUDY/'01_vqe'/'data'/'EIS14.csv']
    for p in candidates:
        if p.is_file(): return p
    raise FileNotFoundError('Cannot locate EIS14 experimental CSV. Checked: '+str(candidates))

def spectrum(path, kind):
    d = pd.read_csv(path)
    pairs = [('frequency_hz','z_real_fit_ohm','minus_z_imag_fit_ohm'),
             ('Frequency (Hz)',"Z' (Ohms)",'-Z" (Ohms)'),
             ('frequency_hz','z_real_ohm','z_imag_ohm')]
    for fc,rc,ic in pairs:
        if all(c in d for c in (fc,rc,ic)):
            f=d[fc].to_numpy(float); r=d[rc].to_numpy(float); im=d[ic].to_numpy(float)
            z=r+(1j*im if ic=='z_imag_ohm' else -1j*im)
            return f,z
    raise ValueError(f'{kind}: expected frequency and real/imaginary impedance columns; got {list(d.columns)}')

def align(freq, f2, z2, label):
    if len(np.unique(f2)) != len(f2): raise ValueError(f'{label}: duplicate frequencies')
    order=np.argsort(f2); f2=f2[order]; z2=z2[order]
    if len(freq)!=len(f2) or not np.allclose(freq,f2,rtol=1e-5,atol=1e-8):
        raise ValueError(f'{label}: frequencies do not match EIS14 data; no interpolation performed')
    return z2

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--data',type=Path,default=None)
    ap.add_argument('--vqe',type=Path,default=VQE/'output'/'quantum_fitted_spectrum.csv')
    ap.add_argument('--classical',type=Path,default=VQE/'output'/'classical_fitted_spectrum.csv')
    args=ap.parse_args()
    data=args.data or locate_data()
    figdir=VQE/'figure'; outdir=VQE/'output'
    figdir.mkdir(parents=True,exist_ok=True);outdir.mkdir(parents=True,exist_ok=True)
    raw=pd.read_csv(data)
    f=raw['Frequency (Hz)'].to_numpy(float)
    z=raw["Z' (Ohms)"].to_numpy(float)-1j*raw['-Z" (Ohms)'].to_numpy(float)
    order=np.argsort(f);f=f[order];z=z[order]
    if np.any(f<=0) or not np.all(np.isfinite(z)) or len(np.unique(f))!=len(f):
        raise ValueError('Invalid experimental frequency/impedance values')
    fv,zv=spectrum(args.vqe,'VQE'); zv=align(f,fv,zv,'VQE')
    methods={'VQE/VQA':zv}
    if args.classical.exists():
        fc,zc=spectrum(args.classical,'Classical');methods={'Classical':align(f,fc,zc,'Classical'),**methods}
    else:
        print('WARNING: Classical fit not found:',args.classical)
        print('No classical curve will be fabricated. Add classical_fitted_spectrum.csv and rerun.')
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelsize':11,
        'axes.titlesize':11,'axes.titleweight':'bold','axes.linewidth':1.7,
        'xtick.direction':'in','ytick.direction':'in','xtick.top':True,'ytick.right':True,
        'xtick.major.width':1.4,'ytick.major.width':1.4,'pdf.fonttype':42})
    colors={'Classical':'#e46c18','VQE/VQA':'#15934b'}
    def save(key,title,xlabel,ylabel,kind,transform=None,zero=False):
        fig,ax=plt.subplots(figsize=(5.1,5.1))
        if zero:ax.axhline(0,color='#555555',lw=1,alpha=.8)
        if kind=='nyquist':
            ax.plot(z.real,-z.imag,'o',color='#1d70b2',ms=3.2,label='Experimental data',zorder=4)
            for name,zz in methods.items():ax.plot(zz.real,-zz.imag,lw=1.9,color=colors[name],label=name)
        elif kind=='bode':
            ax.semilogx(f,transform(z),'o',color='#1d70b2',ms=3.2,label='Experimental data',zorder=4)
            for name,zz in methods.items():ax.semilogx(f,transform(zz),lw=1.9,color=colors[name],label=name)
        else:
            for name,zz in methods.items():
                d=zz-z
                y=transform(d,z)
                ax.semilogx(f,y,lw=1.6,marker='o',ms=2.1,color=colors[name],label=name)
        ax.set(xlabel=xlabel,ylabel=ylabel,title=title)
        ax.set_box_aspect(1)
        for spine in ax.spines.values():spine.set_linewidth(1.7)
        ax.legend(frameon=False,fontsize=9,loc='best')
        fig.savefig(figdir/f'EIS14_VQE_{key}.png',dpi=600,bbox_inches='tight',pad_inches=.15)
        plt.close(fig)
    save('nyquist','EIS14 | Nyquist',r"$Z'$ ($\Omega$)",r"$-Z''$ ($\Omega$)",'nyquist')
    save('bode_magnitude','EIS14 | Bode magnitude','Frequency (Hz)',r'$|Z|$ ($\Omega$)','bode',abs)
    save('bode_phase','EIS14 | Bode phase','Frequency (Hz)','Phase (deg)','bode',lambda a:np.angle(a,deg=True))
    eps=np.finfo(float).eps
    residuals=[('residual_real','Real residual',r'$\mathrm{Re}(\Delta Z)$ ($\Omega$)',lambda d,z:d.real),
      ('residual_imag','Imaginary residual',r'$\mathrm{Im}(\Delta Z)$ ($\Omega$)',lambda d,z:d.imag),
      ('residual_real_normalized','Normalized real residual',r'$\mathrm{Re}(\Delta Z)/|Z_{data}|$',lambda d,z:d.real/np.maximum(abs(z),eps)),
      ('residual_imag_normalized','Normalized imaginary residual',r'$\mathrm{Im}(\Delta Z)/|Z_{data}|$',lambda d,z:d.imag/np.maximum(abs(z),eps))]
    for key,title,ylab,fn in residuals:save(key,'EIS14 | '+title,'Frequency (Hz)',ylab,'residual',fn,True)
    metrics=[]
    for name,zz in methods.items():
        err=zz-z
        metrics.append({'dataset':'EIS14','method':name,'N':len(f),'SSE_ohm2':float(np.sum(abs(err)**2)),
          'RMSE_ohm':float(np.sqrt(np.mean(abs(err)**2))),
          'mean_relative_absolute_error_pct':float(100*np.mean(abs(err)/np.maximum(abs(z),eps)))})
    pd.DataFrame(metrics).to_csv(outdir/'EIS14_VQE_metrics.csv',index=False)
    with open(outdir/'EIS14_VQE_metrics.json','w',encoding='utf8') as file:json.dump(metrics,file,indent=2)
    print('Experimental data:',data,'\nFigure folder:',figdir,'\nMetrics folder:',outdir)
    print(pd.DataFrame(metrics).to_string(index=False))
if __name__=='__main__':main()
