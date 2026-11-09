"""Surrogate validation: true SSE vs predicted weighted loss, clearly labeled different objectives."""
from pathlib import Path
import pandas as pd,numpy as np,matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
P=Path(__file__).resolve().parents[2]/'02_qaoa';d=pd.read_csv(P/'output/surrogate_validation_samples.csv');f=P/'figure';f.mkdir(exist_ok=True)
fig,ax=plt.subplots(figsize=(5.5,5.5),layout='constrained');ax.set_box_aspect(1)
ax.scatter(d.true_SSE,d.predicted_weighted_complex_loss,s=10,alpha=.6)
ax.set(xlabel='True unweighted SSE (Ω²)',ylabel='Surrogate-predicted weighted loss',title='EIS14 | Surrogate objective comparison')
ax.text(.04,.96,'Different objective definitions; not parity',transform=ax.transAxes,va='top',fontsize=9)
fig.savefig(f/'EIS14_QAOA_surrogate_vs_true_SSE_different_objectives.png',dpi=600,bbox_inches='tight',facecolor='white')
plt.close(fig)
