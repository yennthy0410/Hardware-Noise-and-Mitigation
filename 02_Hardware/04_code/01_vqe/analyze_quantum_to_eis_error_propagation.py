#!/usr/bin/env python3
"""Study B 1.3 — Quantum-to-EIS error propagation, offline.

Uses outputs of Part 1.2 and the repository's exact parameter_mapping.py and
impedance_model.py. No QPU jobs, no Aer simulation, no synthetic measurements.

For each backend/scenario, reports: observed nonlinear changes; numerical
Jacobians d(parameter)/d(expectation), d(Re/Im impedance)/d(parameter);
first-order propagation; and EXACT Shapley allocation of delta SSE among the
seven parameter changes (all 2^7 counterfactual subsets).
"""
from __future__ import annotations
import argparse
import importlib
import itertools
import json
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

PARAMS=('Rs','L','Rct','Q1','n1','Q2','n2')
QPUS=('ibm_fez','ibm_kingston','ibm_marrakesh')
SCENARIOS=('measurement','gate','decoherence','combined_calibrated')
COLORS={'ibm_fez':'#277DA1','ibm_kingston':'#F28E2B','ibm_marrakesh':'#6B8E65'}

def find_root():
    candidates=[Path(__file__).resolve().parent,*Path(__file__).resolve().parents,Path.cwd(),*Path.cwd().parents]
    for p in candidates:
        if (p/'01_quantum_eis_fit/04_code/vqe/parameter_mapping.py').exists():return p
    raise FileNotFoundError('Project root containing Study A VQE modules not found; use --root')

def get_functions(root):
    for folder in ('01_quantum_eis_fit/04_code/vqe','shared_tools'):
        p=root/folder
        if p.is_dir() and str(p) not in sys.path: sys.path.insert(0,str(p))
    mapping=importlib.import_module('parameter_mapping')
    impedance=importlib.import_module('impedance_model')
    return mapping.expectations_to_parameters, impedance.impedance_model, impedance.read_eis_file

def load_dataset(reader,path):
    f,z=reader(str(path));f=np.asarray(f,float).ravel();z=np.asarray(z,complex).ravel()
    good=np.isfinite(f)&(f>0)&np.isfinite(z.real)&np.isfinite(z.imag)
    f,z=f[good],z[good]
    if len(f)<3:raise ValueError(f'Insufficient valid EIS data in {path}')
    ix=np.argsort(f);return f[ix],z[ix]

def map_values(mapper,e):
    result=mapper(np.asarray(e,float))
    d=result[0] if isinstance(result,tuple) else result
    if not isinstance(d,dict):raise TypeError('expectations_to_parameters must return dict or (dict, metadata)')
    vals=np.asarray([float(d[k]) for k in PARAMS])
    if not np.isfinite(vals).all(): raise ValueError('Non-finite values from original parameter mapper')
    return vals

def impedance_values(model,f,p):
    z=np.asarray(model(f,*np.asarray(p,float)),complex).ravel()
    if z.shape!=f.shape or not np.isfinite(z.real).all() or not np.isfinite(z.imag).all():
        raise ValueError('Invalid EIS prediction; check physical bounds and parameter mapping')
    return z

def read_expectations(path):
    d=pd.read_csv(path)
    needed={'scenario','logical_qubit','expectation_z'}
    if not needed.issubset(d.columns):raise ValueError(f'{path}: required {needed}')
    result={}
    for sc,g in d.groupby('scenario'):
        qubits=pd.to_numeric(g.logical_qubit,errors='raise').astype(int)
        if len(g)!=7 or set(qubits)!=set(range(7)):raise ValueError(f'{path}: {sc} requires exactly Z0-Z6')
        arr=np.array([float(g.loc[qubits==i,'expectation_z'].iloc[0]) for i in range(7)])
        if not np.isfinite(arr).all() or (np.abs(arr)>1+1e-6).any():raise ValueError(f'{path}: {sc} invalid expectations')
        result[str(sc)]=arr
    if 'ideal' not in result:raise ValueError(f'{path}: missing ideal baseline')
    return result

def numerical_mapping_jacobian(mapper,e,epsilon=1e-5):
    result=np.zeros((7,7))
    for j in range(7):
        h=min(epsilon,max(1e-7,1e-3*(1-abs(e[j]))))
        left=e.copy();right=e.copy()
        left[j]=max(-1.,e[j]-h);right[j]=min(1.,e[j]+h)
        if right[j]<=left[j]:raise ValueError('Cannot differentiate at expectation boundary')
        result[:,j]=(map_values(mapper,right)-map_values(mapper,left))/(right[j]-left[j])
    return result

def impedance_jacobian(model,f,p):
    """Complex Jacobian: derivatives per physical parameter; one-sided fallback."""
    base=impedance_values(model,f,p);jac=np.zeros((len(f),7),complex)
    for j in range(7):
        step=max(abs(p[j])*1e-5,1e-12)
        success=False
        for sign in [1,-1]:
            candidate=p.copy();candidate[j]+=sign*step
            try:
                val=impedance_values(model,f,candidate)
                jac[:,j]=(val-base)/(sign*step);success=True;break
            except (ValueError,OverflowError,FloatingPointError):pass
        if not success:raise ValueError(f'Cannot differentiate impedance w.r.t. {PARAMS[j]}')
    return jac

def sse_value(model,f,z,p):
    r=impedance_values(model,f,p)-z
    return float(np.vdot(r,r).real)

def shapley_sse(model,f,z,ideal,other):
    """Exact 7-player Shapley attribution, includes all interactions (signed)."""
    n=7;values={}
    for mask in range(1<<n):
        p=ideal.copy()
        for i in range(n):
            if mask & (1<<i):p[i]=other[i]
        values[mask]=sse_value(model,f,z,p)
    scores=np.zeros(n)
    for i in range(n):
        bit=1<<i
        for mask in range(1<<n):
            if mask & bit:continue
            size=mask.bit_count()
            w=1/(n*math.comb(n-1,size))
            scores[i]+=w*(values[mask|bit]-values[mask])
    delta=values[(1<<n)-1]-values[0]
    if not np.isclose(scores.sum(),delta,atol=1e-6,rtol=1e-7):
        raise ArithmeticError('Shapley contributions do not add up to observed delta SSE')
    return scores,delta

def graph(fig,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(path,dpi=220,bbox_inches='tight',facecolor='white');plt.close(fig)

def summary_table(df,path):
    cols=['backend','scenario','expectation_MAE','parameter_normalized_MAE','impedance_RMSE_vs_ideal','delta_SSE','shapley_residual_abs']
    view=df[cols].copy()
    view.columns=['QPU','Scenario','Mean |ΔZ|','Mean |Δp| / scale','Z spectrum RMSE','ΔSSE','Shapley check']
    for c in view.columns[2:]:view[c]=view[c].map(lambda x:f'{x:.4g}' if np.isfinite(x) else 'N/A')
    fig,ax=plt.subplots(figsize=(14,max(3.2,.39*len(view)+1.65)));ax.axis('off')
    ax.set_title('Quantum-to-EIS Error Propagation — Scenario Summary',weight='bold',fontsize=13,pad=20)
    table=ax.table(cellText=view.values,colLabels=view.columns,loc='center',cellLoc='center',colWidths=[.13,.20,.12,.14,.14,.12,.12]);table.auto_set_font_size(False);table.set_fontsize(8.5);table.scale(1,1.55)
    for (r,c),cell in table.get_celld().items():
        cell.set_edgecolor('#DDE4ED');cell.set_linewidth(.6)
        cell.set_facecolor('#19324B' if r==0 else ('#F0F5FA' if r%2==0 else '#FFFFFF'))
        if r==0:cell.get_text().set_color('white');cell.get_text().set_weight('bold')
    graph(fig,path)

def plot_mapping_heatmap(jac,path):
    # Normalize each parameter row to eliminate incompatible physical units.
    mat=np.abs(jac);denom=np.max(mat,axis=1,keepdims=True)
    norm=np.divide(mat,denom,out=np.zeros_like(mat),where=denom>0)
    fig,ax=plt.subplots(figsize=(9,5));im=ax.imshow(norm,cmap='Blues',vmin=0,vmax=1,aspect='auto')
    ax.set_xticks(range(7),[f'Z{i}' for i in range(7)]);ax.set_yticks(range(7),PARAMS)
    for i in range(7):
        for j in range(7):ax.text(j,i,f'{norm[i,j]:.2f}',ha='center',va='center',fontsize=9,color='white' if norm[i,j]>.55 else '#183149')
    ax.set_title('Expectation-to-Parameter Sensitivity',weight='bold')
    ax.set_xlabel('Pauli-Z observable');ax.set_ylabel('EIS parameter')
    fig.colorbar(im,ax=ax,label='Row-normalized absolute Jacobian')
    graph(fig,path)

def plot_impedance_sensitivity(f,impjac,p,path):
    # 1% relative perturbation; absolute scale for p~=0 is reported as N/A.
    scale=np.abs(p)*.01
    response=np.abs(impjac)*scale[None,:]
    response[:,np.abs(p)<1e-12]=np.nan
    fig,ax=plt.subplots(figsize=(10,5.4))
    for j,name in enumerate(PARAMS):
        if np.any(np.isfinite(response[:,j])):
            ax.semilogx(f,response[:,j],label=name,lw=1.7)
    ax.set_title('Impedance Sensitivity to a 1% Parameter Perturbation',weight='bold')
    ax.set_xlabel('Frequency (Hz)');ax.set_ylabel('First-order |ΔZ| (Ω)')
    ax.grid(alpha=.2);ax.legend(ncol=4,fontsize=9)
    graph(fig,path)

def plot_shapley(df,path):
    scenarios=[s for s in SCENARIOS if s in set(df.scenario)]
    backends=[b for b in QPUS if b in set(df.backend)]
    fig,axes=plt.subplots(len(backends),1,figsize=(12,3.35*len(backends)),sharex=True,squeeze=False)
    for ax,b in zip(axes[:,0],backends):
        d=df[df.backend==b];x=np.arange(len(scenarios));bottom_pos=np.zeros(len(x));bottom_neg=np.zeros(len(x))
        cmap=plt.get_cmap('tab10')
        for j,p in enumerate(PARAMS):
            vals=np.array([float(d.loc[(d.scenario==s)&(d.parameter==p),'shapley_delta_SSE'].iloc[0]) if ((d.scenario==s)&(d.parameter==p)).any() else 0 for s in scenarios])
            pos=np.maximum(vals,0);neg=np.minimum(vals,0)
            ax.bar(x,pos,bottom=bottom_pos,color=cmap(j),label=p if ax==axes[0,0] else None)
            ax.bar(x,neg,bottom=bottom_neg,color=cmap(j))
            bottom_pos+=pos;bottom_neg+=neg
        ax.set_title(b,loc='left',weight='bold',color=COLORS[b]);ax.axhline(0,color='gray',lw=.8);ax.set_ylabel('Attributed ΔSSE (Ω²)');ax.grid(axis='y',alpha=.16)
    axes[-1,0].set_xticks(range(len(scenarios)),[s.replace('_',' ').title() for s in scenarios],rotation=12)
    fig.suptitle('EIS Fitting Error Attribution Across Physical Parameters',weight='bold',fontsize=13,y=.995)
    fig.legend(*axes[0,0].get_legend_handles_labels(),loc='lower center',bbox_to_anchor=(.5,-.02),ncol=7,frameon=False)
    fig.subplots_adjust(bottom=.13,hspace=.37)
    graph(fig,path)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--simulation-root',type=Path,help='Study B Part 1.2 output directory')
    parser.add_argument('--data',type=Path,help='Original EIS14.csv')
    parser.add_argument('--out',type=Path,help='Part 1.3 output directory')
    parser.add_argument('--backends',nargs='+',choices=QPUS,default=list(QPUS))
    args=parser.parse_args()
    root=args.root or find_root()
    results=root/'experiments/HEA_VQE/01_simulator_to_qpu_hardware/results/EIS14'
    sim=args.simulation_root or results/'05_controlled_noise_scenarios'
    out=args.out or results/'06_quantum_to_eis_error_propagation'
    source=args.data or root/'data/EIS14.csv'
    mapper,model,reader=get_functions(root)
    f,z=load_dataset(reader,source)
    out.mkdir(parents=True,exist_ok=True)
    result=[];expect_rows=[];par_rows=[];spectrum_rows=[];impedance_jac_rows=[];shapley_rows=[]
    for backend in args.backends:
        evpath=sim/backend/'scenario_expectations.csv'
        if not evpath.is_file():raise FileNotFoundError(f'Missing {evpath}; run Part 1.2 first')
        d=read_expectations(evpath);e0=d['ideal'];p0=map_values(mapper,e0);z0=impedance_values(model,f,p0);sse0=sse_value(model,f,z,p0)
        Jpe=numerical_mapping_jacobian(mapper,e0);Jzp=impedance_jacobian(model,f,p0)
        for i,pname in enumerate(PARAMS):
            for j in range(7):par_rows.append(dict(backend=backend,parameter=pname,logical_qubit=j,d_parameter_d_expectation=float(Jpe[i,j])))
        for k,hz in enumerate(f):
            for i,pname in enumerate(PARAMS):impedance_jac_rows.append(dict(backend=backend,frequency_hz=float(hz),parameter=pname,dZreal_dp=float(Jzp[k,i].real),dZimag_dp=float(Jzp[k,i].imag)))
        plot_mapping_heatmap(Jpe,out/f'{backend}_expectation_parameter_jacobian.png')
        plot_impedance_sensitivity(f,Jzp,p0,out/f'{backend}_impedance_sensitivity.png')
        for sc in [name for name in SCENARIOS if name in d]:
            e=d[sc];p=map_values(mapper,e);de=e-e0;dp=p-p0
            z1=impedance_values(model,f,p);dz=z1-z0;linear_dp=Jpe@de;linear_dz=Jzp@linear_dp
            shap,delta_sse=shapley_sse(model,f,z,p0,p)
            mae=float(np.mean(np.abs(de)))
            # Use parameter physical scale from mapping's ideal values, not arbitrary percentages near zero.
            scale=np.maximum(np.abs(p0),np.abs(dp));valid=scale>1e-12
            norm_mae=float(np.mean(np.abs(dp[valid])/scale[valid])) if np.any(valid) else 0.
            rmse_spec=float(np.sqrt(np.mean(np.abs(dz)**2)))
            approx_rmse=float(np.sqrt(np.mean(np.abs(dz-linear_dz)**2)))
            result.append(dict(backend=backend,scenario=sc,expectation_MAE=mae,parameter_normalized_MAE=norm_mae,impedance_RMSE_vs_ideal=rmse_spec,delta_SSE=delta_sse,ideal_SSE=sse0,scenario_SSE=sse0+delta_sse,first_order_impedance_RMSE_error=approx_rmse,shapley_residual_abs=abs(shap.sum()-delta_sse)))
            for j in range(7):
                expect_rows.append(dict(backend=backend,scenario=sc,logical_qubit=j,ideal_expectation=float(e0[j]),scenario_expectation=float(e[j]),delta_expectation=float(de[j]),mapped_parameter=PARAMS[j],ideal_parameter=float(p0[j]),scenario_parameter=float(p[j]),delta_parameter=float(dp[j]),linear_delta_parameter=float(linear_dp[j])))
                shapley_rows.append(dict(backend=backend,scenario=sc,parameter=PARAMS[j],shapley_delta_SSE=float(shap[j]),observed_delta_SSE=float(delta_sse)))
            for k,hz in enumerate(f):
                spectrum_rows.append(dict(backend=backend,scenario=sc,frequency_hz=float(hz),ideal_Z_real=float(z0[k].real),ideal_Z_imag=float(z0[k].imag),scenario_Z_real=float(z1[k].real),scenario_Z_imag=float(z1[k].imag),delta_Z_real=float(dz[k].real),delta_Z_imag=float(dz[k].imag),linear_delta_Z_real=float(linear_dz[k].real),linear_delta_Z_imag=float(linear_dz[k].imag)))
        print(f'[OK] {backend}: mapping Jacobian, impedance sensitivity, nonlinear Shapley SSE')
    summary=pd.DataFrame(result);summary.to_csv(out/'quantum_to_eis_propagation_summary.csv',index=False)
    pd.DataFrame(expect_rows).to_csv(out/'expectation_to_parameter_changes.csv',index=False)
    pd.DataFrame(par_rows).to_csv(out/'mapping_jacobian.csv',index=False)
    pd.DataFrame(impedance_jac_rows).to_csv(out/'impedance_sensitivity_jacobian.csv',index=False)
    pd.DataFrame(shapley_rows).to_csv(out/'parameter_shapley_delta_SSE.csv',index=False)
    pd.DataFrame(spectrum_rows).to_csv(out/'impedance_error_propagation.csv',index=False)
    summary_table(summary,out/'quantum_to_eis_propagation_summary.png')
    plot_shapley(pd.DataFrame(shapley_rows),out/'cross_QPU_parameter_SSE_attribution.png')
    with (out/'analysis_metadata.json').open('w',encoding='utf-8') as g:
        json.dump({'part':'1.3 Quantum-to-EIS Error Propagation','source_simulation':str(sim),'data_source':str(source),'backends':args.backends,'scenarios':[sc for sc in SCENARIOS if sc in set(summary.scenario)],'data_points':len(f),'mapper':'01_quantum_eis_fit/04_code/vqe/parameter_mapping.py','impedance':'repository impedance_model.py','jacobians':'finite-difference at ideal expectations / parameters','shapley':'exact over seven EIS parameter substitutions; signed ΔSSE contributions; nonlinear interaction effects shared','limitations':['The Shapley decomposition attributes fit-error changes to EIS parameters, not causally to physical noise sources.','First-order Jacobian is local and can be inaccurate for large perturbations.','Normalizations of different physical parameters are not equivalent to uncertainties.','Results inherit Part 1.2 model assumptions and calibration limitations.']},g,indent=2)
    print(f'[DONE] {len(summary)} scenario/backend analyses; output: {out}')

if __name__=='__main__':main()
