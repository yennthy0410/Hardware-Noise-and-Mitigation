"""Offline Study A comparison: EIS14 Classical / VQE-VQA / QAOA p=2.
Only independent 600-dpi PNG figures. Does not run optimizers or IBM hardware.
"""
from pathlib import Path
import argparse, json, re
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Locate Study A reliably whether this script is in 04_code/ or a nested folder.
def find_study_dir(script_path):
 for parent in script_path.resolve().parents:
  if (parent / '01_vqe' / 'output').is_dir() and (parent / '02_qaoa' / 'output').is_dir():
   return parent
 raise FileNotFoundError(
  'Cannot locate 01_quantum_eis_fit containing 01_vqe/output and 02_qaoa/output. '
  'Place this script in 01_quantum_eis_fit/04_code/.')

ROOT=find_study_dir(Path(__file__))
V=ROOT/'01_vqe'/'output'; Q=ROOT/'02_qaoa'/'output'
DEST=ROOT/'03_compare_vqe_qaoa'; FIG=DEST/'figure'; OUT=DEST/'output'
for p in (FIG,OUT):p.mkdir(parents=True,exist_ok=True)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelsize':11,'axes.titlesize':12,'axes.titleweight':'bold','axes.linewidth':1.7,'xtick.direction':'in','ytick.direction':'in','xtick.major.width':1.3,'ytick.major.width':1.3,'legend.framealpha':.95})
COL={'Classical':'#ed6b22','VQE/VQA':'#198b43','QAOA p=2':'#d62728'}
LEGEND={'Classical':'classical','VQE/VQA':'VQE/VQA (cont.)','QAOA p=2':'QAOA (disc.)'}

def canvas(title,xlabel,ylabel):
 f,a=plt.subplots(figsize=(5.25,5.25));a.set_box_aspect(1);a.tick_params(top=True,right=True,width=1.3)
 for s in a.spines.values():s.set_linewidth(1.7)
 a.set(title=title,xlabel=xlabel,ylabel=ylabel)
 return f,a

def save(f,name):
 f.tight_layout(pad=1.5);f.savefig(FIG/(name+'.png'),dpi=600,bbox_inches='tight',facecolor='white',pad_inches=.16);plt.close(f)

def check_frequency(a,b,tag):
 if len(a)!=len(b) or not np.allclose(a,b,rtol=1e-8,atol=1e-8):raise ValueError('Frequency mismatch: '+tag)

def read():
 v=pd.read_csv(V/'quantum_fitted_spectrum.csv');c=pd.read_csv(V/'classical_fitted_spectrum.csv');q=pd.read_csv(Q/'qaoa_simulator_fitted_spectrum.csv');q=q.set_index('frequency_hz').reindex(v.frequency_hz).reset_index()
 f=v.frequency_hz.to_numpy(float)
 check_frequency(f,c.frequency_hz.to_numpy(float),'Classical');check_frequency(f,q.frequency_hz.to_numpy(float),'QAOA')
 data=v.z_real_data_ohm.to_numpy(float)-1j*v.minus_z_imag_data_ohm.to_numpy(float)
 for d,tag in [(c,'Classical'),(q,'QAOA')]:
  zz=d.z_real_data_ohm.to_numpy(float)-1j*d.minus_z_imag_data_ohm.to_numpy(float)
  if not np.allclose(zz,data,rtol=1e-7,atol=1e-7):raise ValueError('Experimental data mismatch: '+tag)
 models={'Classical':c.z_real_fit_ohm.to_numpy(float)-1j*c.minus_z_imag_fit_ohm.to_numpy(float),'VQE/VQA':v.z_real_fit_ohm.to_numpy(float)-1j*v.minus_z_imag_fit_ohm.to_numpy(float),'QAOA p=2':q.z_real_qaoa_simulator_ohm.to_numpy(float)+1j*q.z_imag_qaoa_simulator_ohm.to_numpy(float)}
 return f,data,models

def metrics(f,data,models):
 rows=[];points=[]
 for label,z in models.items():
  e=z-data;sse=float(np.sum(np.abs(e)**2));rmse=float(np.sqrt(sse/len(e)));mae=float(np.mean(np.abs(e)));rel=float(np.mean(np.abs(e)/np.maximum(np.abs(data),1e-12))*100)
  rows.append({'method':label,'N':len(e),'SSE_ohm2':sse,'RMSE_ohm':rmse,'MAE_ohm':mae,'mean_relative_absolute_error_pct':rel})
  for fi,ei,zi in zip(f,e,z):points.append({'method':label,'frequency_hz':fi,'residual_real_ohm':ei.real,'residual_imag_ohm':ei.imag,'residual_abs_ohm':abs(ei),'fit_real_ohm':zi.real,'fit_imag_ohm':zi.imag})
 pd.DataFrame(rows).to_csv(OUT/'comparison_metrics.csv',index=False)
 pd.DataFrame(points).to_csv(OUT/'comparison_residuals.csv',index=False)
 return pd.DataFrame(rows)

def main_plots(f,data,models):
 for typ in ('nyquist','bode_magnitude','bode_phase'):
  titles={'nyquist':('Nyquist comparison',"Z′ (Ω)","−Z″ (Ω)"),'bode_magnitude':('Bode magnitude comparison','Frequency (Hz)','|Z| (Ω)'),'bode_phase':('Bode phase comparison','Frequency (Hz)','Phase (deg)')}
  title,x,y=titles[typ];fig,ax=canvas(title,x,y)
  if typ=='nyquist':ax.scatter(data.real,-data.imag,s=13,color='#1976b5',label='Data',zorder=5)
  elif typ=='bode_magnitude':ax.scatter(f,np.abs(data),s=13,color='#1976b5',label='Data',zorder=5)
  else:ax.scatter(f,np.angle(data,deg=True),s=13,color='#1976b5',label='Data',zorder=5)
  for label,z in models.items():
   if typ=='nyquist':xx,yy=z.real,-z.imag
   elif typ=='bode_magnitude':xx,yy=f,np.abs(z)
   else:xx,yy=f,np.angle(z,deg=True)
   order=np.argsort(xx) if typ!='nyquist' else np.arange(len(xx))
   ax.plot(np.asarray(xx)[order],np.asarray(yy)[order],lw=1.5,color=COL[label],label=LEGEND.get(label,label))
  if typ!='nyquist':ax.set_xscale('log')
  ax.legend(loc='best',fontsize=8,frameon=False);save(fig,'compare_'+typ)

 for key,title,ylabel in [('real','Real residual','Re(Zfit − Zdata) (Ω)'),('imag','Imaginary residual','Im(Zfit − Zdata) (Ω)'),('real_normalized','Normalized real residual','Re(ΔZ)/|Zdata|'),('imag_normalized','Normalized imaginary residual','Im(ΔZ)/|Zdata|')]:
  fig,ax=canvas(title,'Frequency (Hz)',ylabel);ax.axhline(0,color='#777777',lw=1)
  for label,z in models.items():
   err=z-data
   if key.startswith('real'): yy=err.real
   else:yy=err.imag
   if key.endswith('normalized'):yy=yy/np.maximum(np.abs(data),1e-12)
   order=np.argsort(f);ax.plot(f[order],yy[order],lw=1.55,color=COL[label],label=LEGEND.get(label,label))
  ax.set_xscale('log');ax.legend(frameon=False,fontsize=8);save(fig,'compare_residual_'+key)

def bars(m):
 for col,unit,title,file in [('SSE_ohm2','SSE (Ω²)','Sum of squared errors','compare_SSE'),('RMSE_ohm','RMSE (Ω)','Root mean squared error','compare_RMSE'),('mean_relative_absolute_error_pct','Mean relative error (%)','Mean relative absolute error','compare_relative_error')]:
  fig,ax=canvas(title,'Method',unit);labels=m.method.tolist();vv=m[col].to_numpy(float)
  xx=np.arange(len(vv));ax.bar(xx,vv,color=[COL[x] for x in labels],width=.58)
  ax.set_xticks(xx,[LEGEND[x] for x in labels],fontsize=8);ax.set_ylim(0,max(vv)*1.23)
  for x,v in zip(xx,vv):ax.text(x,v+max(vv)*.025,f'{v:.3f}',ha='center',fontsize=9)
  save(fig,file)

def _parameter_table(path, value_column):
    """Read the seven physical parameters; fail loudly on malformed input."""
    if not path.is_file():
        raise FileNotFoundError(f"Missing parameter file: {path}\nCopy the saved fitting parameter CSV into the indicated output folder.")
    df = pd.read_csv(path)
    required = {'parameter', value_column}
    if not required.issubset(df.columns):
        raise ValueError(f"{path.name} requires columns {sorted(required)}; found {list(df.columns)}")
    df['parameter'] = df['parameter'].astype(str).str.strip()
    if df['parameter'].duplicated().any():
        raise ValueError(f"Duplicate parameter names in {path}")
    values = pd.to_numeric(df.set_index('parameter')[value_column], errors='coerce')
    names = ['Rs','L','Rct','Q1','n1','Q2','n2']
    if not set(names).issubset(values.index):
        raise ValueError(f"{path.name}: missing {sorted(set(names)-set(values.index))}")
    values = values.reindex(names).astype(float)
    if not np.isfinite(values).all():
        raise ValueError(f"{path.name}: invalid or missing numeric parameter values")
    return values


def parameters():
    names = ['Rs','L','Rct','Q1','n1','Q2','n2']
    classical = _parameter_table(V/'classical_fit_parameters.csv', 'value')
    vqe = _parameter_table(V/'vqe_fit_parameters.csv', 'value')
    qaoa = _parameter_table(Q/'qaoa_p2_fit_parameters.csv', 'qaoa_simulator_value')
    if np.any(classical.to_numpy() == 0):
        raise ValueError('Classical parameter contains zero: relative deviation undefined')
    table = pd.DataFrame({'Classical':classical, 'VQE/VQA':vqe, 'QAOA p=2':qaoa})
    table.index.name = 'parameter'
    table.to_csv(OUT/'seven_parameter_comparison.csv', float_format='%.12g')
    ratios = table.div(table['Classical'], axis=0)
    deviations = (ratios - 1.0)*100.0
    deviations.to_csv(OUT/'seven_parameter_relative_deviation_pct.csv', float_format='%.9g')
    print('Seven parameter values:')
    print(table.to_string(float_format=lambda x: f'{x:.6g}'))

    x=np.arange(7)
    fig,ax=canvas('Parameter deviation from Classical','Parameter','Relative difference (%)')
    w=.35
    for shift,label in [(-w/2,'VQE/VQA'),(w/2,'QAOA p=2')]:
        vals=deviations[label].to_numpy(float)
        ax.bar(x+shift,vals,width=w,color=COL[label],label=LEGEND[label],zorder=3)
    ax.axhline(0,color='gray',lw=1)
    ax.set_xticks(x,names)
    ax.set_xlim(-.65,6.65)
    ax.legend(frameon=False,fontsize=9)
    save(fig,'compare_parameter_relative_difference')

    # Physical parameters have incompatible units and span many orders of magnitude.
    # Use ratios to the classical baseline, not a misleading raw-value common axis.
    fig,ax=canvas('Seven-parameter comparison','Parameter','Parameter / Classical value')
    w=.25
    for shift,label in [(-w,'Classical'),(0,'VQE/VQA'),(w,'QAOA p=2')]:
        vals=ratios[label].to_numpy(float)
        ax.bar(x+shift,vals,width=w,color=COL[label],label=LEGEND[label],zorder=3)
    ax.axhline(1,color='gray',lw=1,ls='--')
    ax.set_xticks(x,names)
    ax.set_xlim(-.65,6.65)
    ax.set_ylim(0,max(1.15,float(np.max(ratios.to_numpy()))*1.15))
    ax.legend(frameon=False,fontsize=8,loc='best')
    save(fig,'compare_seven_parameters_normalized')

    # Heatmap: helps locate parameters with the largest deviation, without
    # confusing the physical units of Rs, L, Rct, Q1, n1, Q2 and n2.
    fig,ax=canvas('Parameter deviation heatmap','Parameter','Optimization method')
    matrix=deviations[['VQE/VQA','QAOA p=2']].to_numpy(float).T
    vmax=max(1.,float(np.max(np.abs(matrix))))
    im=ax.imshow(matrix,aspect='auto',cmap='RdBu_r',vmin=-vmax,vmax=vmax)
    ax.set_xticks(x,names)
    ax.set_yticks([0,1],[LEGEND['VQE/VQA'],LEGEND['QAOA p=2']])
    for i in range(2):
        for j in range(7):
            val=matrix[i,j]
            ax.text(j,i,f'{val:+.1f}%',ha='center',va='center',fontsize=7,
                    color='white' if abs(val)>vmax*.55 else 'black')
    fig.colorbar(im,ax=ax,shrink=.72,label='Relative difference (%)')
    save(fig,'compare_parameter_deviation_heatmap')
    return table

def discretization():
 fp=Q/'quadratic_surrogate_model.json'
 if not fp.exists():return
 model=json.loads(fp.read_text());coef=model.get('coefficients',{})
 if not all(k in coef for k in ('constant','linear','pairwise')):return
 samples=Q/'surrogate_training_samples.csv'
 if not samples.exists():return
 df=pd.read_csv(samples,dtype={'bitstring':'string'});s=df['bitstring'].str.strip().str.zfill(21)
 if not s.str.fullmatch('[01]{21}').fillna(False).all():raise ValueError('Invalid QAOA 21-bit string')
 target='true_weighted_complex_loss';base=s.iloc[int(df[target].idxmin())] if target in df else s.iloc[0]
 base=np.fromiter(map(int,base),dtype=np.int8)
 def score(b):
  value=float(coef['constant'])
  for k,v in coef['linear'].items():value+=float(v)*b[int(re.search(r'\d+',k).group())]
  for t in coef['pairwise']:value+=float(t['coefficient'])*b[int(t['i'])]*b[int(t['j'])]
  return value
 vals=np.empty((8,8))
 for i in range(8):
  for j in range(8):
   b=base.copy()
   for k in range(3):b[6+k]=(i>>k)&1;b[9+k]=(j>>k)&1
   vals[j,i]=score(b)
 fig,ax=canvas('Quadratic surrogate and 3-bit discretization','Rct level (3-bit)','Q1 level (3-bit)')
 im=ax.imshow(vals,origin='lower',extent=[-.5,7.5,-.5,7.5],aspect='auto',cmap='viridis');ax.set_xticks(range(8));ax.set_yticks(range(8))
 fig.colorbar(im,ax=ax,shrink=.75,label='Predicted log10(weighted loss)')
 save(fig,'compare_surrogate_3bit_grid')
 (OUT/'surrogate_discretization_note.txt').write_text('Original QAOA quadratic surrogate; 3-bit blocks 6..8 and 9..11. Other 15 bits held at lowest-training-loss state. This is NOT a VQE trajectory. Confirm bit-to-parameter mapping in source code before publication.\n')

if __name__=='__main__':
 f,d,m=read();result=metrics(f,d,m);main_plots(f,d,m);bars(result);parameters();discretization()
 print(result.to_string(index=False));print('PNG figures:',FIG);print('Tables:',OUT)
