#!/usr/bin/env python3
"""Study B 1.2: controlled Aer noise scenarios -> seven expectations -> EIS.
Offline; no IBM connection. Three calibrated noise mechanisms
(measurement, gate error, decoherence) and their combined effect.
Uses the existing project HEA and EIS model.
"""
from __future__ import annotations
import argparse, json, sys, re, tempfile, shutil
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

QPUS=('ibm_fez','ibm_kingston','ibm_marrakesh')
PARAMS=('Rs','L','Rct','Q1','n1','Q2','n2')
COLORS={'ibm_fez':'#277DA1','ibm_kingston':'#F28E2B','ibm_marrakesh':'#6B8E65'}
SCENARIOS=('ideal','measurement','decoherence','gate','combined_calibrated')

def repo_root():
    here=Path(__file__).resolve()
    for p in [here.parent,*here.parents,Path.cwd(),*Path.cwd().parents]:
        if (p/'01_quantum_eis_fit'/'04_code'/'vqe'/'parameter_mapping.py').exists():return p
    raise FileNotFoundError('Project root not found (Study A VQE modules missing).')

def read_t(df, candidates):
    for c in candidates:
        if c in df.columns:return pd.to_numeric(df[c],errors='coerce')
    return pd.Series(np.nan,index=df.index,dtype=float)

def robust_median(values):
    a=np.asarray(values,dtype=float);a=a[np.isfinite(a)]
    return float(np.median(a)) if len(a) else np.nan

def calibration(root,backend,source):
    """Prefer already processed calibration exports. Value units MUST be explicit.
    1.1 qubit export has t1_s/t2_s if properly parsed; gate export duration_s.
    """
    folder=source or root/'experiments/HEA_VQE/01_simulator_to_qpu_hardware/results/EIS14/04_noise_characterization'
    qpath=folder/'01_qubit_calibration_raw.csv'
    gpath=folder/'01_gate_calibration_raw.csv'
    if not qpath.exists() or not gpath.exists():
        raise FileNotFoundError(f'Calibration CSVs absent from {folder}. Run Part 1.1 first.')
    q=pd.read_csv(qpath);g=pd.read_csv(gpath)
    if 'backend' not in q or 'backend' not in g:raise ValueError('CSV requires backend field')
    q=q.loc[q.backend.astype(str)==backend].copy();g=g.loc[g.backend.astype(str)==backend].copy()
    if q.empty or g.empty:raise ValueError(f'No calibration rows for {backend}')
    # Normalize from 1.1 export: t1/t2 in seconds. Some legacy exports also provide *_us
    for name in ('t1','t2'):
        if name in q.columns:
            q[name+'_seconds']=pd.to_numeric(q[name],errors='coerce')
        elif name+'_s' in q.columns:
            q[name+'_seconds']=pd.to_numeric(q[name+'_s'],errors='coerce')
        elif name+'_us' in q.columns:
            q[name+'_seconds']=pd.to_numeric(q[name+'_us'],errors='coerce')*1e-6
        else:raise ValueError(f'Missing {name} in {qpath}')
    # Refuse ambiguous/mixed units instead of accepting fantastically long lifetimes.
    for name in ('t1','t2'):
        s=q[name+'_seconds'].dropna()
        s=s[s>0]
        if s.empty:raise ValueError(f'Missing valid {name} for {backend}')
        if (s>0.01).any():raise ValueError(f'{backend}: {name} values look like microseconds stored as seconds. Fix calibration units in Part 1.1 before running.')
    rerr=read_t(q,['readout_error']); p10=read_t(q,['prob_meas1_prep0']);p01=read_t(q,['prob_meas0_prep1'])
    # Prefer direct asymmetric assignment errors; symmetric inference is labeled approximation.
    readout_kind='asymmetric_calibration' if p10.notna().any() and p01.notna().any() else 'symmetric_from_readout_error'
    a=robust_median(p10) if readout_kind.startswith('asymmetric') else robust_median(rerr)
    b=robust_median(p01) if readout_kind.startswith('asymmetric') else robust_median(rerr)
    gate=read_t(g,['error','gate_error'])
    arity=read_t(g,['arity'])
    if arity.notna().any():
        e1=robust_median(gate[arity==1]);e2=robust_median(gate[arity==2]);
        t1=robust_median(read_t(g.loc[arity==1],['duration_s','gate_length_s']));
        t2=robust_median(read_t(g.loc[arity==2],['duration_s','gate_length_s']))
    else:raise ValueError('Gate CSV requires arity for 1Q/2Q separation')
    vals={'T1_s':robust_median(q['t1_seconds']), 'T2_s':robust_median(q['t2_seconds']),
          'readout_p10':a,'readout_p01':b,'readout_model':readout_kind,
          'oneq_error':e1,'twoq_error':e2,'oneq_duration_s':t1,'twoq_duration_s':t2,
          'calibration_source':str(folder),'n_qubit_rows':len(q),'n_gate_rows':len(g)}
    for key in ['readout_p10','readout_p01','oneq_error','twoq_error']:
        val=vals[key]
        if not np.isfinite(val) or not 0<=val<1:raise ValueError(f'{backend}: invalid/missing {key}={val}; no guessed replacement')
    for key in ['oneq_duration_s','twoq_duration_s']:
        val=vals[key]
        if not np.isfinite(val) or not 0<val<.01:raise ValueError(f'{backend}: invalid duration {key}={val}; no guessed replacement')
    return vals

def model_functions(root):
    for relative in ('01_quantum_eis_fit/04_code/vqe','shared_tools'):
        p=root/relative
        if p.is_dir() and str(p) not in sys.path:sys.path.insert(0,str(p))
    from impedance_model import impedance_model,read_eis_file
    from parameter_mapping import expectations_to_parameters
    from hea_ansatz import build_hea_7qubit_3layer,assign_angles
    return impedance_model,read_eis_file,expectations_to_parameters,build_hea_7qubit_3layer,assign_angles

def angles_file(root,chosen):
    paths=[chosen] if chosen else [root/'experiments/HEA_VQE/02_optimizer_seed_sensitivity/results/seed_sensitivity_real/EIS14/seed_1013/optimal_angles.csv']
    for path in paths:
        if path is not None and Path(path).is_file():
            d=pd.read_csv(path)
            for name in ('optimal_angle_rad','theta_rad','angle_rad','theta','value'):
                if name in d:
                    a=pd.to_numeric(d[name],errors='coerce').dropna().to_numpy(float)
                    if len(a)==21:return a,str(path)
            raise ValueError(f'{path}: 21 theta values required')
    raise FileNotFoundError('Optimal angles CSV not found. Supply --theta.')

def eis_data(root,reader,path):
    p=path or root/'data/EIS14.csv'
    freq,z=reader(str(p));freq=np.asarray(freq,float);z=np.asarray(z,complex)
    idx=np.isfinite(freq)&(freq>0)&np.isfinite(z.real)&np.isfinite(z.imag)
    if not idx.any():raise ValueError('No valid impedance points')
    order=np.argsort(freq[idx]);return freq[idx][order],z[idx][order],str(p)

def logical_circuit(build,assign,theta):
    c,parameters=build(measure=False)
    c=assign(c,parameters,theta)
    if c.num_qubits!=7:raise ValueError('Expected 7 logical qubits')
    return c

def simulate(c,scenario,cal):
    """Density matrix provides exact Pauli-Z EVs under noise; no shot sampling.
    Thermal relaxation on 2Q is a tensor product channel; depolarizing rates are
    calibration-derived approximations, NOT precise native-gate reconstruction.
    """
    from qiskit_aer import AerSimulator
    from qiskit_aer.noise import NoiseModel,thermal_relaxation_error,depolarizing_error
    from qiskit.quantum_info import DensityMatrix,Statevector,SparsePauliOp
    if scenario=='ideal':
        state=Statevector.from_instruction(c)
        rho=DensityMatrix(state)
    else:
        nm=NoiseModel()
        deco=scenario in ('decoherence','combined_calibrated')
        gates=scenario in ('gate','combined_calibrated')
        if deco:
            t1,t2=cal['T1_s'],cal['T2_s']
            # Aer requires T2 <= 2*T1; reject invalid aggregated medians.
            if t2>2*t1:raise ValueError('Aggregated T2 exceeds 2*T1; use per-qubit snapshot calibration')
            e1=thermal_relaxation_error(t1,t2,cal['oneq_duration_s'])
            er=thermal_relaxation_error(t1,t2,cal['twoq_duration_s'])
            e2=er.tensor(er)
            nm.add_all_qubit_quantum_error(e1,['ry'])
            nm.add_all_qubit_quantum_error(e2,['cx'])
        if gates:
            g1=depolarizing_error(cal['oneq_error'],1)
            g2=depolarizing_error(cal['twoq_error'],2)
            if deco:
                # compose both physical channels rather than replacing one
                nm=NoiseModel()
                nm.add_all_qubit_quantum_error(g1.compose(e1),['ry'])
                nm.add_all_qubit_quantum_error(g2.compose(e2),['cx'])
            else:
                nm.add_all_qubit_quantum_error(g1,['ry']);nm.add_all_qubit_quantum_error(g2,['cx'])
        simulator=AerSimulator(method='density_matrix',noise_model=nm)
        from qiskit import transpile
        qc=c.copy();qc.save_density_matrix()
        # Basis includes the modeled logical operations; do not compile them away.
        compiled=transpile(qc,simulator,optimization_level=0)
        result=simulator.run(compiled,shots=1).result()
        rho=DensityMatrix(result.data(0)['density_matrix'])
    expectation=[]
    for i in range(7):
        observable=SparsePauliOp(''.join('Z' if j==6-i else 'I' for j in range(7)))
        expectation.append(float(np.real(rho.expectation_value(observable))))
    ev=np.asarray(expectation,float)
    if scenario in ('measurement','combined_calibrated'):
        a,b=cal['readout_p10'],cal['readout_p01']
        ev=(1-a-b)*ev+(b-a)
    return np.clip(ev,-1,1)

def ensure_supported(c):
    supported={'ry','cx','barrier','id'}
    gates={inst.operation.name for inst in c.data}
    bad=gates-supported
    if bad:raise ValueError(f'HEA gate set {gates} contains unsupported {bad}; adapt noise channels to exact ansatz before running')

def savefig(fig,p):
    from repository_paths import scratch_root
    p.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='en_',dir=scratch_root(repo_root())) as d:
        src=Path(d)/'p.png';fig.savefig(src,dpi=220,bbox_inches='tight',facecolor='white')
        shutil.copyfile(src,p)
    plt.close(fig)

def save_table(df, out_png, title, formats=None):
    """Render compact publication-style table, retaining the complete CSV separately."""
    view=df.copy().fillna('N/A')
    formats=formats or {}
    for col, fmt in formats.items():
        if col in view:
            view[col]=view[col].map(lambda v: fmt.format(v) if isinstance(v,(float,int,np.floating,np.integer)) and np.isfinite(v) else ('N/A' if pd.isna(v) else str(v)))
    view=view.astype(str)
    nrows,ncols=view.shape
    figw=max(11, min(23, 2.0*ncols+2.5))
    figh=max(3.2, 1.2+0.43*(nrows+1))
    fig,ax=plt.subplots(figsize=(figw,figh))
    ax.axis('off');fig.suptitle(title,fontsize=15,fontweight='bold',y=.985)
    tab=ax.table(cellText=view.values,colLabels=[str(c) for c in view.columns],cellLoc='center',loc='center',bbox=[0,.02,1,.91])
    tab.auto_set_font_size(False);tab.set_fontsize(9)
    for (r,c),cell in tab.get_celld().items():
        cell.set_linewidth(.45);cell.set_edgecolor('#CAD3DD')
        if r==0:cell.set_facecolor('#18324B');cell.set_text_props(color='white',weight='bold')
        else:cell.set_facecolor('#EFF5F9' if r%2 else 'white')
    savefig(fig,out_png)

def drift_heatmap(matrix, scenario_names, labels, out_png, title, unit, symmetric=True):
    mat=np.asarray(matrix,float)
    finite=mat[np.isfinite(mat)]
    limit=max(1e-12,float(np.max(np.abs(finite)))) if len(finite) else 1.0
    fig,ax=plt.subplots(figsize=(max(10,1.15*len(labels)+4),max(4,0.72*len(scenario_names)+2)))
    if symmetric:im=ax.imshow(mat,cmap='RdBu_r',vmin=-limit,vmax=limit,aspect='auto')
    else:im=ax.imshow(mat,cmap='YlOrRd',vmin=0,vmax=limit,aspect='auto')
    ax.set_xticks(range(len(labels)),labels);ax.set_yticks(range(len(scenario_names)),[str(s).replace('_',' ').title() for s in scenario_names]);ax.set_xlabel('Observable' if unit=='Δ⟨Z⟩' else 'EIS parameter')
    ax.set_title(title,fontsize=13,pad=14)
    fig.colorbar(im,ax=ax,label=unit,fraction=.035,pad=.03)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v=mat[i,j];lab='N/A' if not np.isfinite(v) else f'{v:+.4f}' if unit=='Δ⟨Z⟩' else f'{v:+.2f}'
            ax.text(j,i,lab,ha='center',va='center',fontsize=8,color='white' if np.isfinite(v) and abs(v)>.57*limit else '#192D3E')
    savefig(fig,out_png)

def summarize_backend(backend, rows, ev_records, param_records, dest):
    """Three full raw CSVs are saved by caller; build three compact summary tables and two heatmaps."""
    order=[s for s in SCENARIOS if s in {r['scenario'] for r in rows}]
    by_s={r['scenario']:r for r in rows}; ideal=by_s['ideal']
    ev_df=pd.DataFrame(ev_records); par_df=pd.DataFrame(param_records)
    ev_mat=np.array([[float(ev_df.loc[(ev_df.scenario==sc)&(ev_df.logical_qubit==j),'expectation_z'].iloc[0]) for j in range(7)] for sc in order])
    par_mat=np.array([[float(par_df.loc[(par_df.scenario==sc)&(par_df.parameter==name),'value'].iloc[0]) for name in PARAMS] for sc in order])
    ev_delta=ev_mat-ev_mat[0]
    # Protect near-zero ideal parameters; do not create enormous false percentages.
    par_floor=np.maximum(np.abs(par_mat[0])*1e-10,1e-12)
    valid=np.abs(par_mat[0])>par_floor
    par_delta=np.full_like(par_mat,np.nan)
    par_delta[:,valid]=100*(par_mat[:,valid]-par_mat[0,valid])/np.abs(par_mat[0,valid])
    par_delta[0,:]=np.where(valid,0,np.nan)
    fit_rows=[];expect_rows=[];param_rows=[]
    for i,sc in enumerate(order):
        r=by_s[sc]; ds=float(r['SSE']-ideal['SSE']);dr=float(r['RMSE']-ideal['RMSE'])
        fit_rows.append({'scenario':sc,'SSE':float(r['SSE']),'RMSE':float(r['RMSE']),'delta_SSE_vs_ideal':ds,'delta_RMSE_vs_ideal':dr})
        ae=np.abs(ev_delta[i]);j=int(np.argmax(ae))
        expect_rows.append({'scenario':sc,'mean_absolute_expectation_drift':float(ae.mean()),'max_absolute_expectation_drift':float(ae[j]),'most_affected_observable':f'Z{j}'})
        ap=np.abs(par_delta[i]);valid_indices=np.where(np.isfinite(ap))[0]
        best_idx=int(valid_indices[np.argmax(ap[valid_indices])]) if len(valid_indices) else None
        param_rows.append({'scenario':sc,'mean_absolute_parameter_drift_pct':float(np.nanmean(ap)) if len(valid_indices) else np.nan,'max_absolute_parameter_drift_pct':float(ap[best_idx]) if best_idx is not None else np.nan,'most_affected_parameter':PARAMS[best_idx] if best_idx is not None else 'N/A'})
    fit=pd.DataFrame(fit_rows);ex=pd.DataFrame(expect_rows);pa=pd.DataFrame(param_rows)
    for name,frame,title,format_spec in [
        ('noise_scenario_fitting_summary_table',fit,'Noise Scenario Fitting Performance',{'SSE':'{:.5g}','RMSE':'{:.5g}','delta_SSE_vs_ideal':'{:+.5g}','delta_RMSE_vs_ideal':'{:+.5g}'}),
        ('expectation_drift_summary_table',ex,'Quantum Observable Sensitivity to Controlled Noise',{'mean_absolute_expectation_drift':'{:.5f}','max_absolute_expectation_drift':'{:.5f}'}),
        ('parameter_drift_summary_table',pa,'EIS Parameter Sensitivity to Controlled Noise',{'mean_absolute_parameter_drift_pct':'{:.3f}','max_absolute_parameter_drift_pct':'{:.3f}'})]:
        frame.to_csv(dest/(name+'.csv'),index=False)
        save_table(frame,dest/(name+'.png'),f'{backend} — {title}',format_spec)
    # Ideal row retained in CSV but omitted from heatmaps (identically zero by definition).
    drift_heatmap(ev_delta[1:],order[1:],[f'Z{i}' for i in range(7)],dest/'scenario_expectation_drift_heatmap.png',f'{backend} — Pauli-Z Expectation Drift Relative to Ideal','Δ⟨Z⟩')
    drift_heatmap(par_delta[1:],order[1:],list(PARAMS),dest/'scenario_parameter_drift_heatmap.png',f'{backend} — Relative EIS Parameter Drift','Relative drift (%)')
    return fit,ex,pa


def read_real_expectations(path):
    """Read saved Runtime EstimatorV2 outputs, never infer missing values."""
    df=pd.read_csv(path)
    name=next((c for c in ('expectation_z','qpu_expectation_z','expectation_z_clipped','expectation_clipped','expectation_raw','value') if c in df),None)
    if name is None:raise ValueError(f'{path}: expectation column not found')
    if 'qubit' in df:
        df=df.assign(_q=pd.to_numeric(df['qubit'],errors='coerce'))
    elif 'logical_qubit' in df:
        df=df.assign(_q=pd.to_numeric(df['logical_qubit'],errors='coerce'))
    elif 'observable' in df:
        df=df.assign(_q=df['observable'].astype(str).str.extract(r'(\d+)')[0].astype(float))
    else:raise ValueError(f'{path}: cannot identify Z0-Z6 order')
    df=df.dropna(subset=['_q']).sort_values('_q')
    q=df['_q'].to_numpy(int)
    if len(df)!=7 or not np.array_equal(q,np.arange(7)):
        raise ValueError(f'{path}: expected exactly Z0..Z6, got {q.tolist()}')
    ev=pd.to_numeric(df[name],errors='coerce').to_numpy(float)
    if not np.all(np.isfinite(ev)):raise ValueError(f'{path}: nonfinite expectation')
    return ev

def validate_fixed_runs(out, qpu_root, model,mapper,freq,z, simulated, backends):
    """Compare *logical surrogate* Aer predictions to historical Fixed ISA results.

    This is a descriptive validation screening only. Not native-ISA, layout-aware
    noise-model validation: logical homogeneous channels can differ from real backend.
    """
    dst=out/'validation_fixed_ISA'; dst.mkdir(parents=True,exist_ok=True)
    erows=[]; prows=[]; frows=[]; runrows=[]; skips=[]
    for backend in backends:
        folder=qpu_root/backend/'EIS14'/'fixed'
        if not folder.exists():
            skips.append(f'{backend}: missing {folder}');continue
        if backend not in simulated:continue
        predictions=simulated[backend]
        for i in range(1,6):
            run=folder/f'run_{i:02d}'
            ep=run/'expectations.csv'
            if not ep.exists():
                ep=next(iter(run.glob('**/expectations.csv')),None) if run.exists() else None
            if ep is None or not ep.exists():
                skips.append(f'{backend}/run_{i:02d}: missing expectations.csv');continue
            try:
                ev_real=read_real_expectations(ep)
                raw= pd.read_csv(ep)
                jid = str(raw['job_id'].dropna().iloc[0]) if 'job_id' in raw and raw['job_id'].notna().any() else ''
                params_real,_=mapper(ev_real)
                fit_real=np.asarray(model(freq,*[float(params_real[k]) for k in PARAMS]),complex)
                sse_real=float(np.sum(np.abs(fit_real-z)**2))
                for kind in ('ideal','combined_calibrated','real_qpu'):
                    ev=ev_real if kind=='real_qpu' else predictions[kind]['expectations']
                    pdict=params_real if kind=='real_qpu' else predictions[kind]['parameters']
                    sse=sse_real if kind=='real_qpu' else predictions[kind]['SSE']
                    rmse=float(np.sqrt(sse/(2*len(z))))
                    erows.extend(dict(backend=backend,run=i,job_id=jid,model=kind,observable=f'Z{q}',expectation_z=float(v),shots=('from_saved_job' if kind=='real_qpu' else 'exact_density_matrix')) for q,v in enumerate(ev))
                    prows.extend(dict(backend=backend,run=i,job_id=jid,model=kind,parameter=k,value=float(pdict[k])) for k in PARAMS)
                    frows.append(dict(backend=backend,run=i,job_id=jid,model=kind,SSE=sse,RMSE=rmse))
                ideal=predictions['ideal']['expectations']; noisy=predictions['combined_calibrated']['expectations']
                mae_i=float(np.mean(np.abs(ideal-ev_real)))
                mae_n=float(np.mean(np.abs(noisy-ev_real)))
                runrows.append(dict(backend=backend,run=i,job_id=jid,expectation_MAE_ideal=mae_i,expectation_MAE_noisy=mae_n,
                    expectation_MAE_improvement_percent=(100*(mae_i-mae_n)/mae_i if mae_i>1e-12 else np.nan),
                    real_QPU_SSE=sse_real,ideal_Aer_SSE=predictions['ideal']['SSE'],combined_Aer_SSE=predictions['combined_calibrated']['SSE'],
                    absolute_SSE_prediction_error_ideal=abs(predictions['ideal']['SSE']-sse_real),
                    absolute_SSE_prediction_error_noisy=abs(predictions['combined_calibrated']['SSE']-sse_real)))
            except Exception as exc:
                skips.append(f'{backend}/run_{i:02d}: {type(exc).__name__}: {exc}')
    if skips:(dst/'validation_missing_or_invalid.txt').write_text('\n'.join(skips)+'\n',encoding='utf8')
    if not runrows:
        print('[WARN] No complete Fixed ISA run available for validation. See validation_missing_or_invalid.txt')
        return
    datadir=dst/'data'; datadir.mkdir(exist_ok=True)
    pd.DataFrame(erows).to_csv(datadir/'aer_validation_expectations.csv',index=False)
    pd.DataFrame(prows).to_csv(datadir/'aer_validation_parameters.csv',index=False)
    pd.DataFrame(frows).to_csv(datadir/'aer_validation_fitting_metrics.csv',index=False)
    rd=pd.DataFrame(runrows)
    rd.to_csv(datadir/'aer_qpu_validation_per_run.csv',index=False)
    results=[]
    for b,d in rd.groupby('backend',sort=False):
        mi=float(d.expectation_MAE_ideal.mean());mn=float(d.expectation_MAE_noisy.mean())
        vals=simulated[b]
        median=float(d.real_QPU_SSE.median())
        results.append(dict(QPU=b,valid_runs=len(d),expectation_MAE_ideal=mi,expectation_MAE_noisy=mn,
           expectation_MAE_improvement_pct=(100*(mi-mn)/mi if mi>1e-12 else np.nan),
           ideal_Aer_SSE=vals['ideal']['SSE'],combined_Aer_SSE=vals['combined_calibrated']['SSE'],
           median_real_QPU_SSE=median,
           abs_median_SSE_prediction_error_ideal=abs(vals['ideal']['SSE']-median),
           abs_median_SSE_prediction_error_noisy=abs(vals['combined_calibrated']['SSE']-median)))
    summary=pd.DataFrame(results)
    summary.to_csv(dst/'03_aer_qpu_validation_summary.csv',index=False)
    display=summary.copy().rename(columns={'expectation_MAE_ideal':'MAE ideal', 'expectation_MAE_noisy':'MAE noisy',
       'expectation_MAE_improvement_pct':'MAE gain %','median_real_QPU_SSE':'QPU median SSE',
       'abs_median_SSE_prediction_error_ideal':'Ideal |ΔSSE|','abs_median_SSE_prediction_error_noisy':'Noisy |ΔSSE|',
       'ideal_Aer_SSE':'Ideal SSE','combined_Aer_SSE':'Noisy SSE'})
    save_table(display,dst/'03_aer_qpu_validation_summary.png','Noise-Model Validation Against Fixed-ISA Hardware',
      {c:'{:.4g}' for c in display if c not in ('QPU','valid_runs')})
    names=summary.QPU.tolist();fig,axes=plt.subplots(1,len(names),figsize=(6*len(names),4.7),sharey=True,squeeze=False)
    for ax,b in zip(axes.flat,names):
        real=np.array([[x['expectation_z'] for x in erows if x['backend']==b and x['run']==r and x['model']=='real_qpu'] for r in sorted(rd.loc[rd.backend==b,'run'])])
        x=np.arange(7)
        ax.errorbar(x,real.mean(axis=0),yerr=(real.std(axis=0,ddof=1) if len(real)>1 else np.zeros(7)),fmt='o',capsize=3,color=COLORS[b],label='Real QPU mean ± SD')
        ax.plot(x,simulated[b]['ideal']['expectations'],'--',marker='s',color='#616E7C',label='Ideal Aer')
        ax.plot(x,simulated[b]['combined_calibrated']['expectations'],'-',marker='D',color='#B74154',label='Combined Aer')
        ax.set_xticks(x,[f'Z{i}' for i in x]);ax.set_title(b);ax.grid(alpha=.18);ax.set_ylim(-1.05,1.05)
    axes[0,0].set_ylabel('Pauli-Z expectation value')
    axes[0,-1].legend(loc='best',fontsize=8)
    fig.suptitle('Quantum Observable Validation Against Real QPUs',weight='bold');fig.tight_layout()
    savefig(fig,dst/'01_aer_qpu_expectation_validation.png')
    fig,axes=plt.subplots(1,len(names),figsize=(5.7*len(names),4.8),squeeze=False)
    for ax,b in zip(axes.flat,names):
        d=rd[rd.backend==b];x=np.arange(3)
        vals=[simulated[b]['ideal']['SSE'],simulated[b]['combined_calibrated']['SSE'],float(d.real_QPU_SSE.median())]
        ax.bar(x,vals,color=['#616E7C','#B74154',COLORS[b]],alpha=.9)
        ax.scatter(np.full(len(d),2),d.real_QPU_SSE,color='#152A3B',s=25,zorder=3,label='Individual QPU runs')
        ax.set_xticks(x,['Ideal Aer','Combined Aer','Real QPU']);ax.set_title(b);ax.grid(axis='y',alpha=.18)
        for xx,y in zip(x,vals):ax.annotate(f'{y:.3g}',(xx,y),xytext=(0,4),textcoords='offset points',ha='center',fontsize=8)
    axes[0,0].set_ylabel('SSE (Ω²)');fig.suptitle('EIS Fitting Validation Against Real QPUs',weight='bold');fig.tight_layout()
    savefig(fig,dst/'02_aer_qpu_fitting_validation.png')
    (dst/'model_scope.json').write_text(json.dumps({'real_qpu_runs':len(rd),'noise_model':'logical homogeneous surrogate',
       'comparison_level':'screening, not native ISA/layout-matched hardware reproduction',
       'shots':'exact density matrix simulation compared to finite-shot hardware',
       'calibration_limitations':'median calibration model; does not prove contributions of individual mechanisms',
       'missing_runs_report':'validation_missing_or_invalid.txt' if skips else None},indent=2),encoding='utf8')
    print(f'[VALIDATION] {len(rd)} Fixed ISA QPU jobs compared; output {dst}')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=Path,default=None);ap.add_argument('--calibrations',type=Path,default=None)
    ap.add_argument('--theta',type=Path,default=None);ap.add_argument('--data',type=Path,default=None)
    ap.add_argument('--out',type=Path,default=None);ap.add_argument('--backends',nargs='+',choices=QPUS,default=list(QPUS))
    ap.add_argument('--skip-validation',action='store_true',help='Only controlled noise simulations')
    ap.add_argument('--fixed-root',type=Path,default=None,help='Root containing backend/EIS14/fixed/run_XX')
    args=ap.parse_args()
    root=args.root or repo_root()
    out=args.out or root/'experiments/HEA_VQE/01_simulator_to_qpu_hardware/results/EIS14/05_controlled_noise_scenarios'
    out.mkdir(parents=True,exist_ok=True)
    model,reader,mapper,build,assign=model_functions(root)
    theta,theta_src=angles_file(root,args.theta)
    freq,z,data_src=eis_data(root,reader,args.data)
    circuit=logical_circuit(build,assign,theta)
    ensure_supported(circuit)
    with open(out/'experiment_protocol.json','w',encoding='utf-8') as fp:
        json.dump({'theta_source':theta_src,'data_source':data_src,'scenarios':list(SCENARIOS),
                   'method':'Aer density matrix, exact single-Z expectations; no finite-shot noise',
                   'no_reoptimization':True,
                   'noise_mechanisms':['measurement_error','decoherence','gate_error'],
                   'combined_scenario':'measurement + decoherence + gate error',
                   'limitations':['7-qubit logical surrogate; not native ISA/layout-aware',
                                  'median hardware calibration parameters are used as homogeneous channels',
                                  'depolarizing parameters approximately follow calibration gate error; not exact fitted gate channels',
                                  'independent scenario effects need not add linearly']},fp,indent=2)
    allrows=[];simulated={}
    for backend in args.backends:
        cal=calibration(root,backend,args.calibrations)
        dest=out/backend;dest.mkdir(exist_ok=True)
        with open(dest/'scenario_calibration.json','w',encoding='utf-8') as fp:json.dump(cal,fp,indent=2)
        curves={}; rows=[];evs=[];pr=[]; predictions={}
        for sc in SCENARIOS:
            ev=simulate(circuit,sc,cal)
            pdict,_=mapper(ev)
            pars=np.array([float(pdict[k]) for k in PARAMS])
            fit=np.asarray(model(freq,*pars),complex)
            if fit.shape!=z.shape or not np.isfinite(fit).all():raise ValueError(f'Invalid impedance from {backend}/{sc}')
            residual=fit-z;sse=float(np.sum(np.abs(residual)**2));rmse=float(np.sqrt(sse/(2*len(z))))
            row={'backend':backend,'scenario':sc,'SSE':sse,'RMSE':rmse,**{k:float(v) for k,v in zip(PARAMS,pars)}}
            rows.append(row);allrows.append(row);curves[sc]=fit
            predictions[sc]={'expectations':ev,'parameters':{k:float(v) for k,v in zip(PARAMS,pars)},'SSE':sse}
            evs.extend({'backend':backend,'scenario':sc,'logical_qubit':i,'expectation_z':float(v)} for i,v in enumerate(ev))
            pr.extend({'backend':backend,'scenario':sc,'parameter':k,'value':float(v)} for k,v in zip(PARAMS,pars))
        for r in rows:
            baseline=rows[0]
            r['delta_SSE_from_ideal']=r['SSE']-baseline['SSE'];r['delta_RMSE_from_ideal']=r['RMSE']-baseline['RMSE']
        pd.DataFrame(rows).to_csv(dest/'scenario_fitting_metrics.csv',index=False)
        pd.DataFrame(evs).to_csv(dest/'scenario_expectations.csv',index=False)
        pd.DataFrame(pr).to_csv(dest/'scenario_eis_parameters.csv',index=False)
        summarize_backend(backend,rows,evs,pr,dest)
        simulated[backend]=predictions
        print(f'[OK] {backend}: {len(rows)} scenarios, {len(z)} EIS points -> {dest}')
    df=pd.DataFrame(allrows)
    # Add baseline deltas for cross backend too
    for backend in args.backends:
        mask=df.backend==backend;ideal=float(df.loc[mask & (df.scenario=='ideal'),'SSE'].iloc[0]);df.loc[mask,'delta_SSE_from_ideal']=df.loc[mask,'SSE']-ideal
    df.to_csv(out/'cross_QPU_noise_scenario_comparison.csv',index=False)
    fig,ax=plt.subplots(figsize=(10,5));scs=[s for s in SCENARIOS if s!='ideal'];x=np.arange(len(scs));w=.23
    for i,b in enumerate(args.backends):
        d=df[df.backend==b].set_index('scenario');v=[float(d.loc[s,'delta_SSE_from_ideal']) for s in scs]
        ax.bar(x+(i-(len(args.backends)-1)/2)*w,v,w,color=COLORS[b],label=b)
    ax.set_xticks(x,[s.replace('_',' ').title() for s in scs],rotation=15);ax.set_ylabel('ΔSSE relative to ideal (Ω²)')
    ax.set_title('Noise Scenario Sensitivity Across QPUs');ax.legend();ax.axhline(0,lw=.8,color='gray')
    ax.grid(axis='y',alpha=.2);fig.tight_layout()
    savefig(fig,out/'cross_QPU_noise_delta_SSE.png')
    if not args.skip_validation:
        fixed_root=args.fixed_root or root/'experiments/HEA_VQE/01_simulator_to_qpu_hardware/results/EIS14/01_run_qpu'
        validate_fixed_runs(out,fixed_root,model,mapper,freq,z,simulated,args.backends)
    print(f'[DONE] Outputs: {out}. No QPU jobs submitted.')

if __name__=='__main__':main()
