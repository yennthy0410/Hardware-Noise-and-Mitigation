"""EIS14 QAOA p=2 diagnostics, offline; requires numpy/pandas/matplotlib/scipy.
Run from anywhere. --landscape evaluates exact 21-qubit statevector (slow).
SSE parity is a NEW auxiliary SSE surrogate, not the original weighted-loss QUBO.
"""
from pathlib import Path
import argparse,json
import numpy as np,pandas as pd,matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.linalg import solve
P=Path(__file__).resolve().parents[2]/'02_qaoa'; O=P/'output'; F=P/'figure';F.mkdir(parents=True,exist_ok=True)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelsize':11,'axes.titlesize':11,'axes.titleweight':'bold','axes.linewidth':1.7,'xtick.major.width':1.3,'ytick.major.width':1.3,'xtick.direction':'in','ytick.direction':'in','savefig.dpi':600,'pdf.fonttype':42})
def save(fig,name):
 fig.tight_layout(pad=1.5)
 fig.savefig(F/(name+'.png'),dpi=600,bbox_inches='tight',pad_inches=.15)
 plt.close(fig)
def figax():
 f,a=plt.subplots(figsize=(5.1,5.1));a.tick_params(top=True,right=True);a.set_box_aspect(1);
 for spine in a.spines.values():spine.set_linewidth(1.7)
 return f,a
def bits(df):
    """Validate fixed-width 21-bit QUBO strings; never infer bits from integers."""
    if 'bitstring' not in df.columns:
        raise ValueError('Missing bitstring column in surrogate samples')
    series = df['bitstring'].astype('string').str.strip().str.zfill(21)
    valid = series.str.fullmatch(r'[01]{21}').fillna(False)
    if not bool(valid.all()):
        bad = df.loc[~valid, ['bitstring']].head(5).to_dict('records')
        raise ValueError(f'Expected 21 binary digits per row; {int((~valid).sum())} invalid: {bad}')
    # frombuffer makes a guaranteed rectangular array, avoids ragged arrays.
    raw = ''.join(series.tolist()).encode('ascii')
    return (np.frombuffer(raw, dtype=np.uint8).reshape(len(series), 21) - 48).astype(np.float64)

def design(b):
 ii,jj=np.triu_indices(b.shape[1],1)
 return np.column_stack([np.ones(len(b)),b,b[:,ii]*b[:,jj]])
def fit_sse():
 tr=pd.read_csv(O/'surrogate_training_samples.csv',dtype={'bitstring':'string'});va=pd.read_csv(O/'surrogate_validation_samples.csv',dtype={'bitstring':'string'})
 x=design(bits(tr));y=np.log10(np.maximum(tr.true_SSE.to_numpy(float),1e-15)); xv=design(bits(va));yv=va.true_SSE.to_numpy(float)
 # Ridge on log10 SSE, intercept unregularized; this is a distinct diagnostic surrogate.
 lam=0.2; a=x.T@x; a.flat[::len(a)+1]+=lam;a[0,0]-=lam
 coef=solve(a,x.T@y,assume_a='pos');pred=10**np.clip(xv@coef,-10,12)
 r2=1-np.sum((yv-pred)**2)/np.sum((yv-yv.mean())**2)
 out=pd.DataFrame({'true_SSE':yv,'predicted_SSE_auxiliary':pred,'bitstring':va.bitstring});out.to_csv(O/'auxiliary_sse_validation_predictions.csv',index=False)
 np.save(O/'auxiliary_sse_quadratic_coefficients.npy',coef)
 f,ax=figax();ax.scatter(yv,pred,s=15,alpha=.7,color='#2879aa');m=max(yv.max(),pred.max());ax.plot([0,m],[0,m],color='#ed6d24',lw=1.6);ax.set(xlabel='True SSE (Ω²)',ylabel='Auxiliary quadratic predicted SSE (Ω²)',title='EIS14 | Auxiliary SSE surrogate validation');ax.text(.04,.96,f'Holdout R² = {r2:.4f}\nN = {len(yv)}',transform=ax.transAxes,va='top',bbox=dict(facecolor='white',alpha=.8,edgecolor='none'));save(f,'EIS14_QAOA_auxiliary_SSE_parity')
 print('Auxiliary SSE parity R²:',r2)
 return coef

def surrogate_slice():
 # Original quadratic QUBO: visualize 2-variable bit grid at fixed remaining bits.
 model=json.load(open(O/'quadratic_surrogate_model.json'));co=model['coefficients'];print('surrogate coefficient keys',list(co) if isinstance(co,dict) else type(co))
 tr=pd.read_csv(O/'surrogate_training_samples.csv',dtype={'bitstring':'string'});base=np.array([int(v) for v in tr.loc[tr.true_weighted_complex_loss.idxmin(),'bitstring'].strip().zfill(21)]);
 # bit blocks 0..2 Rs, 3..5 L, 6..8 Rct, 9..11 Q1 (verify encoding in project before publication)
 def evaluate(b):
  c=co
  val=float(c['constant'])
  for k,v in c['linear'].items():val+=float(v)*b[int(k.lstrip('x'))]
  for t in c['pairwise']:val+=float(t['coefficient'])*b[int(t['i'])]*b[int(t['j'])]
  return val
 print('coefficient structure:', {k:(len(v) if isinstance(v,list) else str(v)[:70]) for k,v in co.items()})
 vals=np.zeros((8,8));
 for i in range(8):
  for j in range(8):
   b=base.copy()
   for k in range(3):b[6+k]=(i>>k)&1;b[9+k]=(j>>k)&1
   vals[j,i]=evaluate(b)
 f,ax=figax();im=ax.imshow(vals,origin='lower',cmap='viridis',extent=[-.5,7.5,-.5,7.5],aspect='auto');ax.set(xlabel='Rct discrete level (3-bit)',ylabel='Q1 discrete level (3-bit)',title='EIS14 | QUBO discretization slice');f.colorbar(im,ax=ax,label='Original surrogate target (log10 weighted loss)');save(f,'EIS14_QAOA_surrogate_discretization')

def exact_landscapes(grid):
 # Full statevector: 2^21 amplitudes. Hamiltonian is diagonal in computational basis.
 model=json.load(open(O/'ising_model.json'));n=int(model['number_of_qubits']);assert n==21
 h=np.asarray(model['h'],float);J=pd.read_csv(O/'ising_J_upper.csv').iloc[:,1:].to_numpy(float);C=float(model['ising_constant']);N=1<<n
 indices=np.arange(N,dtype=np.uint32);energy=np.full(N,C,dtype=np.float64)
 zz=[]
 for i in range(n):zz.append(1-2*((indices>>i)&1).astype(np.int8))
 for i in range(n):
  energy+=h[i]*zz[i]
  for j in range(i+1,n):
   if J[i,j]:energy+=J[i,j]*zz[i]*zz[j]
 del zz
 opt=json.load(open(O/'p2_optimal_parameters.json'));g=np.array(opt['optimal_gamma'],float);b=np.array(opt['optimal_beta'],float)
 print('p2 optimal angles',g,b,'energy at optimum expected',opt['optimal_expectation_energy'],flush=True)
 def expectation(gg,bb):
  state=np.full(N,1/np.sqrt(N),dtype=np.complex128)
  for layer in range(2):
   state*=np.exp(-1j*gg[layer]*energy)
   cb=np.cos(bb[layer]);sb=-1j*np.sin(bb[layer])
   for q in range(n):
    # reshape into pairs along the target qubit; avoid copying the whole array twice
    view=state.reshape(-1,2,1<<q)
    a=view[:,0,:].copy();v=view[:,1,:].copy()
    view[:,0,:]=cb*a+sb*v;view[:,1,:]=sb*a+cb*v
  return float(np.dot(energy,np.abs(state)**2))
 test=expectation(g,b);print('Recomputed optimum',test,flush=True)
 if abs(test-opt['optimal_expectation_energy'])>0.02:raise RuntimeError('Energy mismatch: check mixer convention / qubit ordering / Ising scaling before plotting!')
 for layer in (0,1):
  for mode in ('coarse','refined'):
   if mode=='coarse':ga=np.linspace(-np.pi*2,np.pi*2,grid);ba=np.linspace(0,np.pi,grid)
   else:ga=np.linspace(g[layer]-.4,g[layer]+.4,grid);ba=np.linspace(max(0,b[layer]-.4),min(np.pi,b[layer]+.4),grid)
   vals=np.zeros((len(ba),len(ga)))
   for j,beta in enumerate(ba):
    for i,gamma in enumerate(ga):
     gg=g.copy();bb=b.copy();gg[layer]=gamma;bb[layer]=beta;vals[j,i]=expectation(gg,bb)
    print('landscape',layer+1,mode,j+1,'/',len(ba),flush=True)
   pd.DataFrame(vals,index=ba,columns=ga).to_csv(O/f'p2_layer{layer+1}_{mode}_energy_grid.csv')
   f,ax=figax();im=ax.pcolormesh(ga,ba,vals,shading='auto',cmap='viridis');ax.scatter([g[layer]],[b[layer]],marker='*',s=100,c='red',edgecolors='white',zorder=5,label='p=2 optimum');ax.legend(fontsize=8);ax.set(xlabel=fr'$\gamma_{layer+1}$ (rad)',ylabel=fr'$\beta_{layer+1}$ (rad)',title=f'EIS14 | p=2 layer {layer+1} {mode} energy');f.colorbar(im,ax=ax,label=r'$\langle H_C\rangle$');save(f,f'EIS14_QAOA_p2_layer{layer+1}_{mode}_landscape')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--landscape',action='store_true',help='Compute exact 21-qubit energy landscapes; may take considerable CPU/RAM');ap.add_argument('--grid',type=int,default=7);args=ap.parse_args()
 fit_sse();surrogate_slice()
 if args.landscape:exact_landscapes(args.grid)
 else:print('Energy landscapes not generated. Use --landscape --grid 7 (21-qubit statevector, high CPU/RAM).')
if __name__=='__main__':main()
