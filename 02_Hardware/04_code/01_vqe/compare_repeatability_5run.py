#!/usr/bin/env python3
"""Fixed-ISA HEA EIS repeatability: 3 QPUs x 5 runs. READ ONLY. No IBM API calls.
Run from anywhere. Requires collected parameters/expectations/result JSON and project EIS model.
"""
from __future__ import annotations
import argparse, json, re, sys, hashlib, tempfile, shutil, os
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from repository_paths import scratch_root

PARAMS=['Rs','L','Rct','Q1','n1','Q2','n2']
QPUS=['ibm_fez','ibm_kingston','ibm_marrakesh']
UNITS=['Ω','H','Ω','S·s^n1','—','S·s^n2','—']

def root_find():
    here=Path(__file__).resolve()
    for p in [here.parent,*here.parents,Path.cwd(),*Path.cwd().parents]:
        if (p/'01_quantum_eis_fit'/'04_code'/'vqe'/'parameter_mapping.py').exists():return p
    raise FileNotFoundError('Cannot locate project root containing Study A VQE modules')

def js(p):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def flat(v,prefix=''):
    out={}
    if isinstance(v,dict):
        for k,x in v.items():out.update(flat(x, f'{prefix}.{k}' if prefix else str(k)))
    elif isinstance(v,list):
        for i,x in enumerate(v):out.update(flat(x,f'{prefix}.{i}'))
    else:out[prefix]=v
    return out

def search(meta, *patterns):
    f=flat(meta)
    for pattern in patterns:
        for k,v in f.items():
            if k.lower().endswith(pattern.lower()) and v not in ('',None):return v
    return None

def scalar(v):
    try:return float(v)
    except (TypeError,ValueError):return np.nan

def read_params(p):
    df=pd.read_csv(p)
    nc=next((x for x in ['parameter','parameter_name','name'] if x in df),None)
    vc=next((x for x in ['value_qpu','value','hardware_fitted_value','fitted_value'] if x in df),None)
    if not nc or not vc:raise ValueError(f'Invalid parameters schema: {p}')
    d=dict(zip(df[nc].astype(str),pd.to_numeric(df[vc],errors='coerce')))
    a=np.array([d.get(x,np.nan) for x in PARAMS],float)
    if not np.isfinite(a).all():raise ValueError(f'Invalid 7 parameters in {p}')
    return a

def read_evs(p):
    df=pd.read_csv(p)
    c=next((x for x in ['expectation_raw','expectation_z','expectation_clipped','ev','expectation'] if x in df),None)
    if not c:raise ValueError(f'No expectations in {p}')
    for key in ['qubit','logical_qubit']:
        if key in df:df=df.sort_values(key);break
    a=pd.to_numeric(df[c],errors='coerce').to_numpy(float)
    if len(a)!=7 or not np.isfinite(a).all():raise ValueError(f'Invalid 7 expectations in {p}')
    return a

def funcs(root):
    for relative in ['01_quantum_eis_fit/04_code/vqe','shared_tools']:
        p=root/relative
        if p.is_dir() and str(p) not in sys.path:sys.path.insert(0,str(p))
    from impedance_model import impedance_model,read_eis_file
    from parameter_mapping import expectations_to_parameters
    return impedance_model,read_eis_file,expectations_to_parameters

def ideal_refs(qpu_root,theta,mapper):
    p=qpu_root/'references'/'EIS14'/'ideal_expectations.csv'
    if p.is_file():
        d=pd.read_csv(p)
        c=next((x for x in ['expectation_ideal','expectation_z','expectation'] if x in d),None)
        if c and len(d)==7:
            if 'logical_qubit' in d:d=d.sort_values('logical_qubit')
            e=pd.to_numeric(d[c],errors='raise').to_numpy(float)
            if np.isfinite(e).all():
                a,_=mapper(e)
                return e,np.array([a[k] for k in PARAMS],float),str(p)
    if theta:
        from qiskit.quantum_info import Statevector,SparsePauliOp
        from hea_ansatz import build_hea_7qubit_3layer,assign_angles
        d=pd.read_csv(theta)
        c=next((x for x in ['optimal_angle_rad','theta_rad'] if x in d),None)
        if not c:raise ValueError('Theta CSV missing angles column')
        angles=d[c].to_numpy(float)
        if len(angles)!=21:raise ValueError('Expected 21 angles')
        circuit,parameters=build_hea_7qubit_3layer(measure=False)
        state=Statevector.from_instruction(assign_angles(circuit,parameters,angles))
        e=np.array([float(np.real(state.expectation_value(SparsePauliOp(''.join('Z' if j==6-i else 'I' for j in range(7)))))) for i in range(7)])
        a,_=mapper(e)
        return e,np.array([a[k] for k in PARAMS],float),str(theta)
    raise FileNotFoundError('Ideal expectations not found; provide --theta')

def read_eis(root,path,read_func):
    p=Path(path).resolve() if path else root/'data'/'EIS14.csv'
    if not p.is_file():raise FileNotFoundError(p)
    freq,z=read_func(str(p));freq=np.asarray(freq,float);z=np.asarray(z,complex)
    keep=np.isfinite(freq)&(freq>0)&np.isfinite(z.real)&np.isfinite(z.imag)
    if not keep.any():raise ValueError('No valid EIS points')
    ix=np.argsort(freq[keep]);return freq[keep][ix],z[keep][ix],p

def scores(v,z):
    r=v-z;sse=float(np.sum(np.abs(r)**2));return {'SSE':sse,'RMSE':float(np.sqrt(sse/(2*len(z))))}

def savefig(fig, p):
    """Render PNG to a short local temporary path before copying to OneDrive.

    Avoids Matplotlib/Pillow Windows OSError [Errno 22] when writing directly to
    longer synced paths. Results are saved to the fixed output location.
    """
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        fig.tight_layout()
        with tempfile.NamedTemporaryFile(prefix="fig_", suffix=".png", delete=False, dir=scratch_root(root_find())) as temp:
            temp_path = Path(temp.name)
        fig.savefig(str(temp_path), dpi=300, bbox_inches="tight", format="png")
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise OSError("Matplotlib did not produce a nonempty PNG")
        shutil.copyfile(temp_path, p)
    finally:
        plt.close(fig)
        if temp_path is not None:
            try: temp_path.unlink(missing_ok=True)
            except OSError: pass

def tablefig(df,path,title,fontsize=9):
    fig,ax=plt.subplots(figsize=(max(10,2+1.6*len(df.columns)), max(2.6,.53*(len(df)+2))))
    ax.axis('off');t=ax.table(cellText=df.astype(str).values,colLabels=df.columns,loc='center',cellLoc='center');t.auto_set_font_size(False);t.set_fontsize(fontsize);t.scale(1,1.5)
    for (r,c),cell in t.get_celld().items():
        cell.set_edgecolor('#aab5c2');cell.set_linewidth(.45)
        if r==0:cell.set_facecolor('#243e59');cell.set_text_props(color='white',weight='bold')
        elif r%2==0:cell.set_facecolor('#f1f5fa')
    ax.set_title(title,pad=14);savefig(fig,path)

def get_classical(root,explicit,f,z,model):
    """Prefer classical parameters; otherwise verify and use fitted spectrum on SAME frequency grid."""
    paths=[]
    if explicit:
        p=Path(explicit).resolve()
        if p.is_file():paths=[p]
        elif p.is_dir():paths=list(p.rglob('*.csv'))
        else:raise FileNotFoundError(p)
    else:
        dirs=[root/'reference_results'/'classical'/'EIS14',root/'experiments'/'HEA_VQE'/'00_initial_pipeline_validation'/'results'/'classical'/'EIS14',root/'results'/'classical'/'EIS14']
        for d in dirs:
            if d.exists():paths.extend(d.rglob('*.csv'))
    # Favor spectrum named fitted_spectrum; other formats supported only if schema matches.
    paths=sorted(set(paths),key=lambda p:(0 if 'parameter' in p.name.lower() else 1 if 'fitted_spectrum' in p.name.lower() else 2,str(p)))
    for p in paths:
        try:d=pd.read_csv(p)
        except Exception:continue
        name=next((x for x in ['parameter','parameter_name','name'] if x in d),None)
        value=next((x for x in ['value','fitted_value','best_value','classical_value'] if x in d),None)
        if name and value:
            lookup=dict(zip(d[name].astype(str),pd.to_numeric(d[value],errors='coerce')))
            a=np.array([lookup.get(x,np.nan) for x in PARAMS],float)
            if np.isfinite(a).all():return np.asarray(model(f,*a),complex),str(p)
        fc=next((x for x in ['frequency_hz','frequency','freq_hz','Frequency_Hz'] if x in d),None)
        rc=next((x for x in ['z_real_fit_ohm','z_real_fit','z_real_model_ohm','z_real_classical_ohm'] if x in d),None)
        ic=next((x for x in ['z_imag_fit_ohm','z_imag_fit','z_imag_model_ohm'] if x in d),None)
        mc=next((x for x in ['minus_z_imag_fit_ohm','minus_z_imag_fit','minus_z_imag_model_ohm'] if x in d),None)
        if fc and rc and (ic or mc):
            ff=pd.to_numeric(d[fc],errors='coerce').to_numpy(float);order=np.argsort(ff)
            if len(ff)!=len(f) or not np.allclose(ff[order],f,rtol=1e-8,atol=1e-12):continue
            real=pd.to_numeric(d[rc],errors='coerce').to_numpy(float)[order]
            imag=pd.to_numeric(d[ic or mc],errors='coerce').to_numpy(float)[order]
            zz=real+1j*(imag if ic else -imag)
            if np.isfinite(zz).all():return zz,str(p)
    return None,None

def save_qpu_plots(backend,rows,curves,f,z,sim,classical,out):
    all_lines=[('Experimental',z,'black'),('Classical',classical,'#254b80'),('Ideal Simulator',sim,'#de7f16')]+[(f'QPU Run {r["run"]}',curves[r['run']],plt.cm.tab10(i)) for i,r in enumerate(rows)]
    fig,ax=plt.subplots(figsize=(7.7,6));
    for name,zz,c in all_lines:
        if zz is None:continue
        if name=='Experimental':ax.scatter(zz.real,-zz.imag,s=35,facecolors='none',edgecolors='black',label=name,zorder=10)
        else:ax.plot(zz.real,-zz.imag,label=name,color=c,linewidth=2.2 if name in ['Classical','Ideal Simulator'] else 1.5)
    ax.set(xlabel="Z' (Ω)",ylabel="−Z'' (Ω)",title=f'{backend} — Nyquist (Fixed ISA)');ax.grid(alpha=.25);ax.legend(fontsize=8,ncol=2);savefig(fig,out/'01_nyquist_5runs.png')
    fig,axes=plt.subplots(2,1,figsize=(8,8),sharex=True)
    for name,zz,c in all_lines:
        if zz is None:continue
        for ax,yy in [(axes[0],np.abs(zz)),(axes[1],np.angle(zz,deg=True))]:
            if name=='Experimental':ax.scatter(f,yy,s=25,facecolors='none',edgecolors='black',label=name,zorder=10)
            else:ax.plot(f,yy,label=name,color=c,linewidth=2.2 if name in ['Classical','Ideal Simulator'] else 1.5)
    axes[0].set_ylabel('|Z| (Ω)');axes[0].set_yscale('log');axes[1].set_ylabel('Phase (deg)');axes[1].set_xlabel('Frequency (Hz)')
    for ax in axes:ax.set_xscale('log');ax.grid(alpha=.25);ax.legend(fontsize=8,ncol=2)
    fig.suptitle(f'{backend} — Bode (Fixed ISA)');savefig(fig,out/'02_bode_5runs.png')
    fig,axes=plt.subplots(2,1,figsize=(8,8),sharex=True)
    for name,zz,c in all_lines:
        if zz is None or name=='Experimental':continue
        for ax,val in [(axes[0],(zz-z).real),(axes[1],(zz-z).imag)]:ax.plot(f,val,label=name,color=c,linewidth=2.2 if name in ['Classical','Ideal Simulator'] else 1.5)
    axes[0].set_ylabel('Re(Zfit − Zexp) (Ω)');axes[1].set_ylabel('Im(Zfit − Zexp) (Ω)');axes[1].set_xlabel('Frequency (Hz)')
    for ax in axes:ax.set_xscale('log');ax.axhline(0,color='gray',lw=.8);ax.grid(alpha=.25);ax.legend(fontsize=8,ncol=2)
    fig.suptitle(f'{backend} — Residuals (Fixed ISA)');savefig(fig,out/'03_residuals_5runs.png')
    metric_rows=[{'Method':name,**scores(zz,z)} for name,zz,c in all_lines if zz is not None and name!='Experimental']
    md=pd.DataFrame(metric_rows);md.to_csv(out/'04_fit_metrics_all_methods.csv',index=False)
    for key in ['SSE','RMSE']:
        fig,ax=plt.subplots(figsize=(9,4.7));bar=ax.bar(md.Method,md[key]);ax.bar_label(bar,labels=[f'{v:.3g}' for v in md[key]],padding=3,fontsize=9);ax.tick_params(axis='x',labelrotation=25);ax.set(ylabel=f'{key} ({"Ω²" if key=="SSE" else "Ω"})',title=f'{backend} — {key}: Classical, Simulator and 5 QPU Runs');ax.grid(axis='y',alpha=.2);savefig(fig,out/f'04_{key.lower()}_comparison.png')
    return md

def per_qpu_diagnostics(backend,rows,ideal_e,ideal_p,out):
    rows=sorted(rows,key=lambda r:r['run']);n=len(rows)
    pdata=[]
    for i,p in enumerate(PARAMS):
        values=np.array([r[p] for r in rows]);mean=float(values.mean());sd=float(values.std(ddof=1)) if n>1 else np.nan
        pdata.append([p,UNITS[i],*[f'{r[p]:.6g}' for r in rows],f'{ideal_p[i]:.6g}',f'{mean:.6g}',f'{sd:.4g}'])
    cols=['Parameter','Unit']+[f'Run {r["run"]}' for r in rows]+['Ideal Simulator','Mean','SD']
    table=pd.DataFrame(pdata,columns=cols);table.to_csv(out/'05_parameter_table_vs_ideal.csv',index=False);tablefig(table,out/'05_parameter_table_vs_ideal.png',f'{backend} — Seven EIS Parameters vs Ideal Simulator',7.6)
    expectations=np.array([[r[f'Z{i}'] for i in range(7)] for r in rows]);expect_delta=expectations-ideal_e
    delta_pct=np.array([[r[f'{p}_drift_pct'] for p in PARAMS] for r in rows])
    for arr,labels,title,filename in [(expect_delta,[f'Z{i}' for i in range(7)],'Pauli-Z expectation drift (QPU − ideal)','06_expectation_heatmap.png'),(delta_pct,PARAMS,'Parameter relative drift vs ideal (%)','07_parameter_relative_change_heatmap.png')]:
        fig,ax=plt.subplots(figsize=(9.5,4.8));vmax=max(np.nanmax(abs(arr)),1e-12);vmin=-vmax
        im=ax.imshow(arr,aspect='auto',cmap='coolwarm',vmin=vmin,vmax=vmax)
        ax.set_xticks(range(7),labels);ax.set_yticks(range(n),[f'Run {r["run"]}' for r in rows]);ax.set_title(f'{backend} — {title}')
        for i in range(n):
            for j in range(7):ax.text(j,i,f'{arr[i,j]:+.3g}',ha='center',va='center',fontsize=8,color='white' if abs(arr[i,j])>.6*vmax else 'black')
        fig.colorbar(im,ax=ax,label='Δ⟨Z⟩' if filename.startswith('06') else 'Relative drift (%)');savefig(fig,out/filename)
    pd.DataFrame(expect_delta,columns=[f'Z{i}' for i in range(7)],index=[f'run_{r["run"]:02d}' for r in rows]).to_csv(out/'06_expectation_drift.csv')
    pd.DataFrame(delta_pct,columns=PARAMS,index=[f'run_{r["run"]:02d}' for r in rows]).to_csv(out/'07_parameter_relative_change.csv')

def benchmark_info(folder,backend,run):
    submitted=js(folder/'job_submitted.json');config=js(folder/'config.json');runconf=js(folder/'run_config.json');retrieved=js(folder/'job_retrieved_metadata.json');info=js(folder/'job_info.json');trans=js(folder/'transpiler_info.json')
    # Prioritize retrieved IBM metrics; no invented timestamps/usage.
    data={'backend':backend,'run':run,'job_id':search(submitted,'job_id') or search(info,'Job ID') or search(config,'job_id'),
        'qpu_usage_s':search(retrieved,'qpu_usage_s','usage.quantum_seconds','quantum_seconds'),
        'execution_time_s':search(retrieved,'execution_time_s','running_time'),
        'submitted_at_utc':search(retrieved,'job_created_or_submitted_at_utc','submitted_at_utc','created_utc') or search(submitted,'submitted_at_utc','created_utc') or search(info,'Submitted'),
        'completed_at_utc':search(retrieved,'job_completed_at_utc','completed_at_utc') or search(info,'Completed'),
        'isa_depth':search(trans,'isa_depth','isa_circuit_depth'),
        'isa_size':search(trans,'isa_size','isa_circuit_size'),
        'shots':search(config,'shots') or search(runconf,'shots'),
        'qiskit_version':search(config,'qiskit_version') or search(runconf,'qiskit_version'),
        'qiskit_runtime_version':search(config,'qiskit_runtime_version') or search(runconf,'qiskit_runtime_version'),
        'gate_counts':search(trans,'isa_gate_counts','gate_counts'),
        'run_directory':str(folder)}
    return data

def cross_plots(master,paramrows,ideal_e,sim,z,out,qpus):
    for metric in ['SSE','RMSE']:
        fig,ax=plt.subplots(figsize=(9.5,5));
        ax.axhline(scores(sim,z)[metric],ls='--',color='dimgray',label='Ideal Simulator')
        for j,b in enumerate(qpus):
            grp=master[master.backend==b].sort_values('run');v=grp[metric].to_numpy(float)
            if len(v):
                ax.scatter(np.full(len(v),j)+np.linspace(-.12,.12,len(v)),v,label=b,s=32)
                ax.text(j,float(np.max(v)),f'Median {np.median(v):.3g}',ha='center',va='bottom',fontsize=8)
        ax.set_xticks(range(len(qpus)),qpus);ax.set_ylabel(metric);ax.grid(alpha=.25);ax.legend(fontsize=8);ax.set_title(f'Cross-QPU {metric} repeatability');savefig(fig,out/f'08_cross_QPU_{metric}.png')
    em=np.array([[np.mean([abs(float(r[f'Z{i}'])-ideal_e[i]) for r in master[master.backend==b].to_dict('records')]) if len(master[master.backend==b]) else np.nan for i in range(7)] for b in qpus])
    pm=np.array([[np.mean(abs(master.loc[master.backend==b,f'{p}_drift_pct'].to_numpy(float))) if len(master[master.backend==b]) else np.nan for p in PARAMS] for b in qpus])
    for mat,labels,name,file in [(em,[f'Z{i}' for i in range(7)],'Mean absolute expectation drift','09_cross_QPU_expectation_drift.png'),(pm,PARAMS,'Mean absolute parameter drift (%)','10_cross_QPU_parameter_drift.png')]:
        fig,ax=plt.subplots(figsize=(10,4));im=ax.imshow(mat,aspect='auto',cmap='viridis');ax.set_xticks(range(7),labels);ax.set_yticks(range(len(qpus)),qpus);ax.set_title(name)
        for i in range(len(qpus)):
            for j in range(7):
                if np.isfinite(mat[i,j]):ax.text(j,i,f'{mat[i,j]:.3g}',ha='center',va='center',color='white' if mat[i,j]>np.nanmax(mat)*.6 else 'black',fontsize=9)
        fig.colorbar(im,ax=ax);savefig(fig,out/file)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--qpu-root',type=Path,default=None,help='Folder containing ibm_*/EIS14/fixed/run_XX')
    ap.add_argument('--data',type=Path,default=None)
    ap.add_argument('--theta',type=Path,default=None)
    ap.add_argument('--classical',type=Path,default=None,help='Classical parameter CSV or fitted spectrum CSV (or a containing folder)')
    ap.add_argument('--out',type=Path,default=None,help='Output directory; default is results/EIS14/02_hardware_repeatability')
    ap.add_argument('--backends',default=','.join(QPUS));ap.add_argument('--runs',type=int,default=5)
    ap.add_argument('--allow-partial',action='store_true');ap.add_argument('--allow-missing-classical',action='store_true')
    args=ap.parse_args();root=root_find()
    qpu_root=(args.qpu_root or root/'01_vqe/00_raw_qpu_jobs').resolve()
    if not qpu_root.is_dir():raise FileNotFoundError(f'QPU root not found: {qpu_root}')
    out=(args.out or root/'01_vqe/02_fixed_isa_repeatability').resolve()
    out.mkdir(parents=True,exist_ok=True)
    print('[INFO] Saving results directly to:',out,flush=True)
    model,reader,mapper=funcs(root);f,z,data=read_eis(root,args.data,reader);eideal,pideal,ref=ideal_refs(qpu_root,args.theta,mapper)
    sim=np.asarray(model(f,*pideal),complex);assert sim.shape==z.shape and np.isfinite(sim).all()
    classical,classical_source=get_classical(root,args.classical,f,z,model)
    if classical is None and not args.allow_missing_classical:raise FileNotFoundError('Classical reference not found. Supply --classical PATH (CSV parameters or fitted_spectrum.csv). Or use --allow-missing-classical to continue without classical.')
    if classical is not None and (classical.shape!=z.shape or not np.isfinite(classical).all()):raise ValueError('Invalid classical impedance')
    qpus=[b.strip() for b in args.backends.split(',') if b.strip()];allrows=[];miss=[];longp=[];longe=[];bench=[]
    for b in qpus:
        rows=[];curves={}
        for run in range(1,args.runs+1):
            d=qpu_root/b/'EIS14'/'fixed'/f'run_{run:02d}'
            try:
                if not all((d/n).is_file() for n in ['parameters.csv','expectations.csv','result.json']):raise FileNotFoundError('Need collected parameters.csv expectations.csv result.json')
                ps=read_params(d/'parameters.csv');es=read_evs(d/'expectations.csv');zz=np.asarray(model(f,*ps),complex)
                if zz.shape!=z.shape or not np.isfinite(zz).all():raise ValueError('Invalid impedance')
                record={'backend':b,'run':run,'mode':'fixed',**scores(zz,z)}
                for i,p in enumerate(PARAMS):
                    record[p]=ps[i];record[p+'_ideal']=pideal[i];record[p+'_drift_pct']=100*(ps[i]-pideal[i])/abs(pideal[i]) if abs(pideal[i])>1e-14 else np.nan
                    longp.append({'backend':b,'run':run,'parameter':p,'QPU':ps[i],'Ideal':pideal[i],'relative_drift_pct':record[p+'_drift_pct']})
                for i in range(7):
                    record[f'Z{i}']=es[i];record[f'delta_Z{i}']=es[i]-eideal[i]
                    longe.append({'backend':b,'run':run,'observable':f'Z{i}','QPU':es[i],'Ideal':eideal[i],'drift':es[i]-eideal[i]})
                rows.append(record);allrows.append(record);curves[run]=zz;bench.append(benchmark_info(d,b,run))
            except Exception as ex:miss.append({'backend':b,'run':run,'path':str(d),'issue':str(ex)})
        if not rows:print('[WARN]',b,'no complete runs');continue
        if len(rows)<args.runs and not args.allow_partial:
            print('[WARN]',b,'partial runs, per-QPU plots skipped; use --allow-partial');continue
        dest=out/b;dest.mkdir(exist_ok=True)
        rows.sort(key=lambda r:r['run']);pd.DataFrame(rows).to_csv(dest/'00_QPU_run_values.csv',index=False)
        save_qpu_plots(b,rows,curves,f,z,sim,classical,dest);per_qpu_diagnostics(b,rows,eideal,pideal,dest)
    pd.DataFrame(miss,columns=['backend','run','path','issue']).to_csv(out/'00_missing_runs.csv',index=False)
    if not allrows:raise RuntimeError('No collected QPU data found')
    master=pd.DataFrame(allrows);master.to_csv(out/'00_all_fixed_QPU_runs.csv',index=False)
    pd.DataFrame(longp).to_csv(out/'01_parameters_all_QPU.csv',index=False);pd.DataFrame(longe).to_csv(out/'02_expectations_all_QPU.csv',index=False)
    metrics=['Median SSE','SD(SSE)','Median RMSE','Mean absolute expectation bias','Mean expectation SD','Mean absolute parameter drift (%)']
    sums=pd.DataFrame(index=metrics,columns=qpus,dtype=float)
    for b in qpus:
        g=master[master.backend==b];
        if g.empty:continue
        E=g[[f'Z{i}' for i in range(7)]].to_numpy(float)
        for key,val in {'Median SSE':g.SSE.median(),'SD(SSE)':g.SSE.std(ddof=1),'Median RMSE':g.RMSE.median(),'Mean absolute expectation bias':np.mean(abs(E.mean(axis=0)-eideal)),'Mean expectation SD':np.mean(E.std(axis=0,ddof=1)) if len(E)>1 else np.nan,'Mean absolute parameter drift (%)':np.nanmean(abs(g[[p+'_drift_pct' for p in PARAMS]].to_numpy(float)))}.items():sums.loc[key,b]=val
    sums.index.name='Metric';sums.to_csv(out/'07_cross_QPU_consistency_table.csv')
    show=sums.reset_index();
    for b in qpus:show[b]=show[b].map(lambda v:'N/A' if pd.isna(v) else f'{v:.5g}')
    tablefig(show,out/'07_cross_QPU_consistency_table.png','Cross-QPU Consistency (Fixed ISA)')
    cross_plots(master,longp,eideal,sim,z,out,qpus)
    bdf=pd.DataFrame(bench)
    cols=['backend','run','job_id','submitted_at_utc','completed_at_utc','execution_time_s','qpu_usage_s','isa_depth','isa_size','shots','qiskit_version','qiskit_runtime_version','gate_counts','run_directory']
    bdf=bdf.reindex(columns=cols);bdf.to_csv(out/'11_cross_QPU_job_benchmark.csv',index=False)
    compact=bdf[['backend','run','job_id','submitted_at_utc','completed_at_utc','execution_time_s','qpu_usage_s']].copy()
    for x in ['submitted_at_utc','completed_at_utc']:
        compact[x]=compact[x].map(lambda x: str(x)[:19].replace('T',' ') if pd.notna(x) and x else 'N/A')
    for x in ['execution_time_s','qpu_usage_s']:compact[x]=compact[x].map(lambda v: f'{scalar(v):.3g}' if np.isfinite(scalar(v)) else 'N/A')
    compact.columns=['QPU','Run','Job ID','Submitted (UTC)','Completed (UTC)','Execution (s)','QPU usage (s)']
    tablefig(compact,out/'11_cross_QPU_job_benchmark.png','Cross-QPU Job Benchmark (15 Fixed-ISA Jobs)',7)
    metadata={'dataset':'EIS14','ideal_source':ref,'classical_source':classical_source,'experimental_source':str(data),'n_points':int(len(f)),'formulas':{'residual':'Z_fit - Z_exp','SSE':'sum(abs(residual)**2)','RMSE':'sqrt(SSE/(2*N))'},'limitations':['Read-only Fixed ISA; no physical noise attribution','Missing backend metadata retained as N/A']}
    (out/'00_analysis_metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print(f'[DONE] {len(master)} runs. Results saved in {out}. Missing: {len(miss)}')

if __name__=='__main__':main()
