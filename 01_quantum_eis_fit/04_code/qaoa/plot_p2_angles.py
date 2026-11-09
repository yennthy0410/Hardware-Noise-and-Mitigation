"""Plot measured p=2 optimization samples as 2D angle slices (not a recomputed energy grid)."""
from pathlib import Path
import numpy as np,pandas as pd,matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
BASE=Path(__file__).resolve().parents[2]/'02_qaoa';O=BASE/'output';F=BASE/'figure';F.mkdir(exist_ok=True,parents=True)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.linewidth':1.5,'xtick.direction':'in','ytick.direction':'in','xtick.top':True,'ytick.right':True})
d=pd.read_csv(O/'p2_optimizer_trace.csv').dropna(subset=['gamma_0','beta_0','gamma_1','beta_1','expectation_energy'])
d=d[np.isfinite(d.expectation_energy)]
# p=2 has 4 angles. Show measured samples only; no false regular-grid energy landscape.
for k in (0,1):
 fig,ax=plt.subplots(figsize=(5.5,5.5),layout='constrained');ax.set_box_aspect(1)
 sc=ax.scatter(d[f'gamma_{k}'],d[f'beta_{k}'],c=d.expectation_energy,cmap='viridis',s=13,alpha=.8,rasterized=True)
 best=d.loc[d.expectation_energy.idxmin()];ax.scatter([best[f'gamma_{k}']],[best[f'beta_{k}']],marker='*',s=140,c='red',edgecolors='black',label='Best sampled state')
 ax.set_xlabel(r'$\gamma_{%d}$' % (k+1));ax.set_ylabel(r'$\beta_{%d}$' % (k+1))
 ax.set_title(f'EIS14 | p=2 sampled angle projection (layer {k+1})')
 fig.colorbar(sc,ax=ax,label='Sampled expected Ising energy',fraction=.046,pad=.04);ax.legend(frameon=False,fontsize=8)
 fig.savefig(F/f'EIS14_QAOA_p2_sampled_angles_layer{k+1}.png',dpi=600,bbox_inches='tight',facecolor='white')
 plt.close(fig)
print('These are sampled projections, NOT controlled 2D landscape slices.')
