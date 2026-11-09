"""EIS14 QAOA simulator p=2 figures, with p=2 landscape diagnostics.
No IBM calls, no optimization; reads saved CSV results only.
Run from anywhere: python 01_quantum_eis_fit/04_code/qaoa/plot_qaoa_eis14.py
"""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

SCRIPT=Path(__file__).resolve()
ROOT=SCRIPT.parents[2]  # 01_quantum_eis_fit
OUT=ROOT/'02_qaoa'/'output'
FIG=ROOT/'02_qaoa'/'figure'
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelsize':11,
 'axes.titlesize':11,'axes.titleweight':'bold','axes.linewidth':1.5,
 'xtick.major.width':1.2,'ytick.major.width':1.2,'xtick.direction':'in',
 'ytick.direction':'in','xtick.top':True,'ytick.right':True,'savefig.facecolor':'white'})
COL={'Data':'#1f77b4','Classical':'#e76f00','QAOA (p=2)':'#c82e35'}

def panel(title,xlabel,ylabel,logx=False,logy=False):
 fig,ax=plt.subplots(figsize=(5.2,5.2),layout='constrained')
 ax.set_box_aspect(1);ax.set_title(title,pad=12);ax.set_xlabel(xlabel);ax.set_ylabel(ylabel)
 if logx:ax.set_xscale('log')
 if logy:ax.set_yscale('log')
 for sp in ax.spines.values():sp.set_linewidth(1.5)
 return fig,ax

def save(fig,name):
 FIG.mkdir(parents=True,exist_ok=True)
 fig.savefig(FIG/f'{name}.png',dpi=600,bbox_inches='tight',facecolor='white')
 plt.close(fig)

def main():
 global OUT,FIG
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=OUT);p.add_argument('--figures',type=Path,default=FIG)
 args=p.parse_args()
 # Parser overrides handled below (global declared above assignments)
 OUT=args.output.resolve();FIG=args.figures.resolve()
 f=OUT/'qaoa_simulator_fitted_spectrum.csv'
 if not f.exists():raise FileNotFoundError(f'Historical p=2 simulator spectrum missing: {f}')
 d=pd.read_csv(f).sort_values('frequency_hz');freq=d.frequency_hz.to_numpy(float)
 if (freq<=0).any():raise ValueError('Frequency must be positive')
 z={
 'Data':d.z_real_data_ohm.to_numpy(float)+1j*d.z_imag_data_ohm.to_numpy(float),
 'Classical':d.z_real_classical_ohm.to_numpy(float)+1j*d.z_imag_classical_ohm.to_numpy(float),
 'QAOA (p=2)':d.z_real_qaoa_simulator_ohm.to_numpy(float)+1j*d.z_imag_qaoa_simulator_ohm.to_numpy(float)}
 # Optional independent original experiment file: compare rather than overwrite.
 original=ROOT.parent/'data'/'EIS14'/'EIS14.csv'
 if original.exists():print(f'Original EIS14 available at {original}; saved spectrum remains the authoritative matched source for these figures.')
 for suffix,title,xx,yy,logx,logy in [
 ('nyquist','EIS14 | Nyquist',None,None,False,False),
 ('bode_magnitude','EIS14 | Bode magnitude','Frequency (Hz)','|Z| (Ω)',True,True),
 ('bode_phase','EIS14 | Bode phase','Frequency (Hz)','Phase (°)',True,False)]:
  fig,ax=panel(title,xx or "Z′ (Ω)",yy or "−Z″ (Ω)",logx,logy)
  for label,v in z.items():
   if suffix=='nyquist':x,y=v.real,-v.imag
   elif suffix=='bode_magnitude':x,y=freq,np.abs(v)
   else:x,y=freq,np.degrees(np.angle(v))
   if label=='Data':ax.plot(x,y,'o',ms=2.8,color=COL[label],label='Experimental data',zorder=3)
   else:ax.plot(x,y,lw=1.8,color=COL[label],label=label)
  ax.legend(frameon=False,fontsize=8,loc='best');save(fig,'EIS14_QAOA_'+suffix)
 rows=[]
 for label in ('Classical','QAOA (p=2)'):
  r=z[label]-z['Data'];rows.append({'method':label,'N':len(r),'SSE_ohm2':float(np.sum(abs(r)**2)),'RMSE_ohm':float(np.sqrt(np.mean(abs(r)**2)))})
 metrics = pd.DataFrame(rows)
 metrics.to_csv(OUT/'EIS14_QAOA_metrics.csv',index=False)
 # Two separate square PNG figures, consistent with VQE journal template.
 for key, ylabel, title, filename in [
  ('SSE_ohm2', 'SSE (Ω²)', 'EIS14 | Complex SSE', 'EIS14_QAOA_SSE'),
  ('RMSE_ohm', 'RMSE (Ω)', 'EIS14 | Complex RMSE', 'EIS14_QAOA_RMSE')]:
  fig,ax=panel(title,'Fitting method',ylabel)
  methods=metrics['method'].tolist()
  values=metrics[key].to_numpy(float)
  xpos=np.arange(len(methods))
  ax.bar(xpos,values,width=.56,color=[COL[m] for m in methods],edgecolor='black',linewidth=1.1)
  ax.set_xticks(xpos,methods)
  ax.set_ylim(0,max(values)*1.24 if max(values)>0 else 1)
  for x,value in zip(xpos,values):
   ax.text(x,value+max(values)*.025,f'{value:.3f}',ha='center',va='bottom',fontsize=10,fontweight='bold')
  save(fig,filename)

 for name,component,norm in [('real',lambda r:r.real,False),('imag',lambda r:r.imag,False),('real_normalized',lambda r:r.real,True),('imag_normalized',lambda r:r.imag,True)]:
  fig,ax=panel('EIS14 | '+name.replace('_',' ').capitalize()+' residual','Frequency (Hz)',('Normalized residual' if norm else 'Residual (Ω)'),True)
  for label in ('Classical','QAOA (p=2)'):
   r=z[label]-z['Data']; y=component(r)
   if norm:y=y/np.maximum(np.abs(z['Data']),1e-12)
   ax.plot(freq,y,color=COL[label],lw=1.7,label=label)
  ax.axhline(0,color='black',lw=.8);ax.legend(frameon=False,fontsize=8);save(fig,'EIS14_QAOA_residual_'+name)
 # Surrogate fidelity: the surrogate was fitted to transformed weighted loss,
 # not raw SSE. Plot in its OWN target space; do not mislabel as SSE.
 val=OUT/'surrogate_validation_samples.csv'
 if val.exists():
  a=pd.read_csv(val);x=a.surrogate_target.to_numpy(float);y=a.surrogate_prediction.to_numpy(float)
  fig,ax=panel('EIS14 | Surrogate fidelity','True transformed target','Predicted transformed target')
  ax.scatter(x,y,s=10,alpha=.55,color=COL['Data']);lo=min(x.min(),y.min());hi=max(x.max(),y.max());ax.plot([lo,hi],[lo,hi],color=COL['Classical'],lw=1.4)
  r2=1-np.sum((x-y)**2)/np.sum((x-x.mean())**2)
  ax.text(.04,.96,f'R² = {r2:.4f}\nN = {len(x)}',transform=ax.transAxes,va='top');save(fig,'EIS14_QAOA_surrogate_fidelity')
  print('Surrogate R2 in transformed target space:',r2)
 print('Figures:',FIG,'\nMetrics:',OUT/'EIS14_QAOA_metrics.csv')
if __name__=='__main__':main()
