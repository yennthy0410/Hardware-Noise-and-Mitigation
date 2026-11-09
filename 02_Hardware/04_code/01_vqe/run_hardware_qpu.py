"""Part I: repeatable multi-QPU HEA-EIS RAW benchmark.

DRY RUN by default. Needs the existing project modules hea_ansatz.py and
parameter_mapping.py, IBM Quantum credentials and optimal_angles.csv files.
The script does NOT measure crosstalk or causally identify a noise mechanism.
"""
import os
for env in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS'):
    os.environ.setdefault(env,'1')
import argparse, csv, hashlib, json, platform, sys, time, traceback, shutil
from itertools import product
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import qiskit
from qiskit import qpy
from qiskit.quantum_info import SparsePauliOp, Statevector
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_ibm_runtime import EstimatorV2, QiskitRuntimeService, RuntimeEncoder

def _study_a_root():
    """Locate canonical Study A modules without depending on 02_Hardware/src."""
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents, Path.cwd(), *Path.cwd().parents):
        candidate = parent / '01_quantum_eis_fit' / '04_code' / 'vqe'
        if (candidate / 'parameter_mapping.py').is_file() and (candidate / 'impedance_model.py').is_file():
            return parent
    raise FileNotFoundError('Study A modules missing: 01_quantum_eis_fit/04_code/vqe')

def _register_study_a():
    import sys
    directory = _study_a_root() / '01_quantum_eis_fit' / '04_code' / 'vqe'
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return directory

PROJECT_ROOT = _study_a_root()
_register_study_a()
from hea_ansatz import N_QUBITS, build_hea_7qubit_3layer, assign_angles
from parameter_mapping import PARAMETER_NAMES, PARAMETER_UNITS, expectations_to_parameters

SEEDS = {'EIS13':13,'EIS14':1013}
DEFAULT_BACKENDS = 'ibm_fez,ibm_kingston,ibm_marrakesh'

def utc(): return datetime.now(timezone.utc).isoformat()
def save_json(obj,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,default=str,ensure_ascii=False),encoding='utf-8')
def digest(data): return hashlib.sha256(data).hexdigest()
def hash_theta(a):return digest(np.asarray(a,dtype=np.float64).tobytes())
def safe(f):
    try:return f()
    except Exception:return None

def theta_file(project,dataset,seed,explicit):
    if explicit:
        p=Path(explicit)
        if not p.exists():raise FileNotFoundError(p)
        return p
    roots=[project/'results'/'quantum_cobyla_5seed',project/'results'/'seed_sensitivity_real',project/'results'/'quantum_cobyla', project/'experiments'/'HEA_VQE'/'00_initial_pipeline_validation'/'results'/'quantum_cobyla_5seed', project/'experiments'/'HEA_VQE'/'02_optimizer_seed_sensitivity'/'results'/'quantum_cobyla_5seed']
    attempts=[r/dataset/f'seed_{seed}'/'optimal_angles.csv' for r in roots]
    attempts += [r/dataset/'optimal_angles.csv' for r in roots]
    for p in attempts:
        if p.exists():return p
    raise FileNotFoundError('Missing theta CSV for '+dataset+'; tried: '+', '.join(map(str,attempts)))

def read_theta(p):
    df=pd.read_csv(p)
    cols=[c for c in ('optimal_angle_rad','theta_rad') if c in df]
    if not cols: raise ValueError(f'{p}: require optimal_angle_rad or theta_rad')
    v=df[cols[0]].to_numpy(dtype=float)
    if len(v)!=3*N_QUBITS or not np.isfinite(v).all():
        raise ValueError(f'{p}: expected {3*N_QUBITS} finite angles, got {len(v)}')
    return v

def build_model(theta):
    qc, parameters = build_hea_7qubit_3layer(measure=False)
    return assign_angles(qc,parameters,theta)

def obs(logical_index,layout=None):
    paulis=['I']*N_QUBITS;paulis[N_QUBITS-1-logical_index]='Z'
    op=SparsePauliOp(''.join(paulis))
    return op.apply_layout(layout) if layout is not None else op

def ideal_values(logical_qc):
    st=Statevector.from_instruction(logical_qc)
    return np.array([float(np.real(st.expectation_value(obs(i)))) for i in range(N_QUBITS)])

def get_backends(service,names,min_count,auto_fill):
    available={b.name:b for b in service.backends(operational=True,simulator=False,min_num_qubits=N_QUBITS)}
    selected=[];missing=[]
    for name in names:
        if name in available and name not in selected:selected.append(name)
        else:missing.append(name)
    if missing: print('UNAVAILABLE:',', '.join(missing))
    if auto_fill:
        # Fallback is visible, deterministic; NEVER silently claim the requested machines ran.
        for name in sorted(available):
            if len(selected)>=min_count:break
            if name not in selected:selected.append(name)
    if len(selected)!=min_count:
        raise RuntimeError(f'Only {len(selected)} QPUs selected; need >= {min_count}. Available: {sorted(available)}. Choose --backends or --auto-fill.')
    return [available[name] for name in selected]

def get_properties(backend):
    return safe(lambda:backend.properties(refresh=True)) or safe(lambda:backend.properties())

def calib_tables(backend,properties):
    qrows=[];grows=[]
    if properties is None:return qrows,grows
    for q in range(backend.num_qubits):
        qrows.append({'physical_qubit':q,'T1_us':safe(lambda:properties.t1(q)*1e6),
                      'T2_us':safe(lambda:properties.t2(q)*1e6),
                      'readout_error':safe(lambda:properties.readout_error(q)),
                      'p01':safe(lambda:properties.prob_meas1_prep0(q)),
                      'p10':safe(lambda:properties.prob_meas0_prep1(q))})
    for gate in getattr(properties,'gates',[]):
        gate_name=getattr(gate,'gate',None);qs=list(getattr(gate,'qubits',[]))
        grows.append({'gate':gate_name,'physical_qubits':json.dumps(qs),
                     'error':safe(lambda:properties.gate_error(gate_name,qs)),
                     'duration_s':safe(lambda:properties.gate_length(gate_name,qs))})
    return qrows,grows

def save_calibration(backend,directory,stage):
    p=get_properties(backend);now=utc();meta={'captured_utc':now,'backend':backend.name,
      'last_update_date':str(getattr(p,'last_update_date',None)), 'available':p is not None}
    folder=directory/'calibrations';folder.mkdir(parents=True,exist_ok=True)
    rows,gates=calib_tables(backend,p)
    pd.DataFrame(rows).to_csv(folder/(stage+'_qubits.csv'),index=False)
    pd.DataFrame(gates).to_csv(folder/(stage+'_gates.csv'),index=False)
    save_json(meta,folder/(stage+'_metadata.json'))
    if p is not None:
        save_json(safe(lambda:p.to_dict()),folder/(stage+'_raw_properties.json'))
    return meta,rows,gates

def circuit_stats(isa,gate_table,qubit_table):
    lookup={(r['gate'],tuple(json.loads(r['physical_qubits']))):r for r in gate_table}
    two=[];one=[];freq=Counter()
    for inst in isa.data:
        name=inst.operation.name
        qs=tuple(isa.find_bit(q).index for q in inst.qubits)
        freq[name]+=1
        if name in {'barrier','delay','measure','reset'}:continue
        if len(qs) not in (1,2):continue
        rec=lookup.get((name,qs),{})
        row={'gate':name,'physical_qubits':list(qs),'calibrated_error':rec.get('error'),
             'calibrated_duration_s':rec.get('duration_s')}
        (one if len(qs)==1 else two).append(row)
    # ISA can include idle wires: map logical observables to physical indices from layout.
    phys=[]
    for i in range(N_QUBITS):
        physical=(safe(lambda:isa.layout.initial_index_layout(filter_ancillas=True)[i]))
        phys.append(physical)
    q_lookup={r['physical_qubit']:r for r in qubit_table}
    selected_q=[q_lookup[q] for q in phys if q in q_lookup]
    def mean_field(rows,key):
        values=[float(x[key]) for x in rows if x.get(key) is not None and np.isfinite(float(x[key]))]
        return float(np.mean(values)) if values else None
    def exposure(rows):
        vals=[float(x['calibrated_error']) for x in rows if x['calibrated_error'] is not None]
        return float(np.sum(vals)) if len(vals)==len(rows) else None
    stats={'physical_layout_logical_order':phys,'isa_depth':isa.depth(),'isa_size':isa.size(),
       'gate_counts':dict(freq),'oneq_gate_count':len(one),'twoq_gate_count':len(two),
       'oneq_error_exposure_proxy':exposure(one),'twoq_error_exposure_proxy':exposure(two),
       'mean_layout_readout_error':mean_field(selected_q,'readout_error'),
       'min_layout_T1_us':min((r['T1_us'] for r in selected_q if r['T1_us'] is not None),default=None),
       'min_layout_T2_us':min((r['T2_us'] for r in selected_q if r['T2_us'] is not None),default=None),
       'gate_exposure_note':'Sum of calibrated per-operation gate error metrics: proxy, not probability of total circuit error',
       'duration_idle_note':'Not computed; requires a scheduled circuit and backend timing model'}
    return stats,one,two,selected_q

def append_manifest(path,row):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['backend','dataset','run','layout_mode','job_id','status','timestamp_utc','directory','error'])
        if f.tell()==0:w.writeheader()
        w.writerow({k:row.get(k,'') for k in w.fieldnames})

def save_circuit(isa,folder):
    with (folder/'circuit.qpy').open('wb') as f:qpy.dump(isa,f)
    try:
        from qiskit.qasm3 import dumps
        (folder/'circuit.qasm').write_text(dumps(isa),encoding='utf-8')
    except Exception as exc:
        (folder/'circuit_qasm_export_error.txt').write_text(str(exc),encoding='utf-8')
    try:
        fig=isa.draw(output='mpl',fold=35,idle_wires=False)
        fig.savefig(folder/'circuit_diagram.png',dpi=250,bbox_inches='tight')
        import matplotlib.pyplot as plt
        plt.close(fig)
    except Exception as exc:
        (folder/'circuit_diagram.txt').write_text(str(isa.draw(output='text',fold=100)),encoding='utf-8')
        (folder/'circuit_diagram_warning.txt').write_text(str(exc),encoding='utf-8')
    # QPY is the authoritative exact serialization of the native ISA circuit.
    # Executable Python loads it; unlike manual gate-by-gate reconstruction, this
    # preserves instruction definitions, classical registers, and circuit layout.
    (folder/'circuit_qiskit_code.py').write_text(
        'from pathlib import Path\nfrom qiskit import qpy\n'
        'with (Path(__file__).parent / "circuit.qpy").open("rb") as f:\n'
        '    circuit = qpy.load(f)[0]\n'
        'print(circuit)\n',encoding='utf-8')


def job_result(job,folder,ideal,metadata):
    result=job.result()
    with (folder/'result.json').open('w',encoding='utf-8') as f:json.dump(result,f,cls=RuntimeEncoder,indent=2)
    ev=np.asarray(result[0].data.evs,dtype=float).ravel()
    if len(ev)!=N_QUBITS:raise ValueError(f'Expected {N_QUBITS} expectation values, got {len(ev)}')
    std=safe(lambda:np.asarray(result[0].data.stds,dtype=float).ravel())
    if std is None or len(std)!=N_QUBITS:std=np.full(N_QUBITS,np.nan)
    # Keep raw unbounded estimates as evidence; clip only for physical decoding.
    clipped=np.clip(ev,-1,1)
    params,_=expectations_to_parameters(clipped)
    ideal_params,_=expectations_to_parameters(ideal)
    eframe=pd.DataFrame({'logical_qubit':range(N_QUBITS),'parameter':PARAMETER_NAMES,
                         'expectation_raw':ev,'expectation_decoding_clipped':clipped,
                         'expectation_ideal':ideal,'delta_expectation':ev-ideal,'runtime_std':std})
    eframe.to_csv(folder/'expectations.csv',index=False)
    pframe=pd.DataFrame({'parameter':PARAMETER_NAMES,'unit':PARAMETER_UNITS,
                         'value_qpu':[params[n] for n in PARAMETER_NAMES],
                         'value_ideal':[ideal_params[n] for n in PARAMETER_NAMES]})
    pframe['absolute_drift']=(pframe.value_qpu-pframe.value_ideal).abs()
    pframe['relative_drift_percent']=100*pframe.absolute_drift/pframe.value_ideal.abs().replace(0,np.nan)
    pframe.to_csv(folder/'parameters.csv',index=False)
    pd.DataFrame({'backend':[metadata['backend']]*N_QUBITS,
       'dataset':[metadata['dataset']]*N_QUBITS,'run':[metadata['run']]*N_QUBITS,
       'qubit':range(N_QUBITS),'parameter':PARAMETER_NAMES,
       'expectation_z':ev,'expectation_ideal':ideal,
       'value_qpu':[params[n] for n in PARAMETER_NAMES],
       'value_ideal':[ideal_params[n] for n in PARAMETER_NAMES],
       'relative_drift_percent':pframe.relative_drift_percent,
       'std':std}).to_csv(folder/'summary.csv',index=False)
    save_json({'job_id':job.job_id(),'backend':metadata['backend'],'completed_utc':utc(),
               'job_metrics':safe(job.metrics),'result_metadata':str(getattr(result[0],'metadata',{}))},folder/'job_info.json')
    return {'expectation_MAE':float(np.abs(ev-ideal).mean()),
            'parameter_mean_relative_drift_pct':float(pframe.relative_drift_percent.mean()),
            **{f'{n}_qpu':float(params[n]) for n in PARAMETER_NAMES}}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir',type=Path,default=PROJECT_ROOT if PROJECT_ROOT else Path.cwd())
    parser.add_argument('--out',type=Path,default=None)
    parser.add_argument('--datasets',default='EIS14')
    parser.add_argument('--data-dir',type=Path,default=None,help='Directory containing EIS13.csv and EIS14.csv (default: PROJECT/data then PROJECT)')
    parser.add_argument('--theta-eis13',default=None)
    parser.add_argument('--theta-eis14',default=None)
    parser.add_argument('--backends',default=DEFAULT_BACKENDS,
                        help='Exactly three IBM QPUs by default; unavailable machines require explicit replacement')
    parser.add_argument('--min-qpus',type=int,default=3)
    parser.add_argument('--auto-fill',action='store_true',help='Explicitly replace missing backends with another available QPU')
    parser.add_argument('--runs',type=int,default=5)
    parser.add_argument('--shots',type=int,default=4096)
    parser.add_argument('--optimization-level',type=int,default=3,choices=(0,1,2,3))
    parser.add_argument('--layout-mode',choices=('unfixed','fixed','both'),default='both',
                        help='both adds fixed ISA measurement repeats to separate compilation from sampling/noise')
    parser.add_argument('--submit',action='store_true',help='EXPLICIT permission to submit QPU jobs')
    parser.add_argument('--submit-only',action='store_true',help='Submit and save job IDs, do not block waiting for results (use --submit)')
    parser.add_argument('--collect-only',action='store_true',help='Collect previously submitted job results, no new jobs')
    parser.add_argument('--max-jobs',type=int,default=None,help='Optional cap, useful for pilot')
    parser.add_argument('--instance',default=None,help='IBM Quantum instance CRN for the intended account')
    parser.add_argument('--token-env',default=None,help='Environment variable holding the intended API key; never put key in source')
    parser.add_argument('--check-account',action='store_true',help='Check IBM account/instance/backends only; no job submission')
    args=parser.parse_args()
    if args.runs<1 or args.min_qpus!=3 or args.shots<1:parser.error('runs>0, min-qpus=3, shots>0')
    project=args.project_dir.resolve();out=(args.out or (project/'experiments'/'HEA_VQE'/'01_transpilation_layout_sensitivity'/'results'/'part1_multi_qpu')).resolve()
    out.mkdir(parents=True,exist_ok=True)
    datasets=[x.strip().upper() for x in args.datasets.split(',') if x.strip()]
    if not datasets or any(x not in SEEDS for x in datasets):parser.error('datasets must be EIS13 and/or EIS14')
    if args.submit_only and not args.submit:parser.error('--submit-only requires --submit')
    if args.check_account and args.submit:parser.error('--check-account cannot be used with --submit')
    if args.collect_only and args.submit:parser.error('--collect-only must not be paired with --submit')
    # Validate theta files BEFORE connecting and submitting anything.
    dataset_def={}
    data_path_for={}
    for ds in datasets:
        source=theta_file(project,ds,SEEDS[ds],args.theta_eis13 if ds=='EIS13' else args.theta_eis14)
        angles=read_theta(source)
        logical=build_model(angles);ideal=ideal_values(logical)
        data_candidates=([args.data_dir / (ds+'.csv')] if args.data_dir is not None else [project/'data'/(ds+'.csv'),project/(ds+'.csv')])
        data_path=next((p for p in data_candidates if p.exists()),None)
        if data_path is None:raise FileNotFoundError('Cannot locate '+ds+'.csv; supply --data-dir. Searched: '+str(data_candidates))
        dataset_def[ds]=(source,angles,logical,ideal)
        data_path_for[ds]=data_path
        dsdir=out/'references'/ds;dsdir.mkdir(parents=True,exist_ok=True)
        shutil.copy2(data_path,dsdir/(ds+'.csv'))
        pd.DataFrame({'logical_qubit':range(N_QUBITS),'parameter':PARAMETER_NAMES,'expectation_ideal':ideal}).to_csv(dsdir/'ideal_expectations.csv',index=False)
        ip,_=expectations_to_parameters(ideal)
        pd.DataFrame({'parameter':PARAMETER_NAMES,'value':[ip[k] for k in PARAMETER_NAMES],'unit':PARAMETER_UNITS}).to_csv(dsdir/'ideal_parameters.csv',index=False)
        save_json({'theta_source':str(source),'theta_sha256':hash_theta(angles),'optimizer_seed':SEEDS[ds], 'input_eis_source':str(data_path), 'input_eis_sha256':digest(data_path.read_bytes())},dsdir/'reference_metadata.json')
    print('Connecting to IBM Runtime service...',flush=True)
    connection={'channel':'ibm_quantum_platform'}
    if args.instance:connection['instance']=args.instance
    if args.token_env:
        token=os.environ.get(args.token_env)
        if not token:raise RuntimeError(f'Environment variable {args.token_env} is not defined')
        connection['token']=token
    service=QiskitRuntimeService(**connection)
    acct=service.active_account() or {}
    print('CONNECTED channel:',acct.get('channel'),'instance:',acct.get('instance'),flush=True)
    if args.instance and acct.get('instance') and acct.get('instance')!=args.instance:
        raise RuntimeError('Connected instance mismatch: refusing to submit!')
    if args.check_account:
        available=[b.name for b in service.backends(operational=True,simulator=False,min_num_qubits=N_QUBITS)]
        print('AVAILABLE REAL QPUs:',', '.join(available),flush=True)
        print('CHECK COMPLETE. No job submitted.')
        return
    backends=get_backends(service,[x.strip() for x in args.backends.split(',') if x.strip()],args.min_qpus,args.auto_fill) if not args.collect_only else []
    selected=[b.name for b in backends] if backends else [x.strip() for x in args.backends.split(',') if x.strip()]
    modes=['fixed','unfixed'] if args.layout_mode=='both' else [args.layout_mode]
    proposed=len(backends)*len(datasets)*args.runs*len(modes)
    if args.max_jobs is not None:proposed=min(proposed,args.max_jobs)
    config={'created_utc':utc(),'backends':selected,'datasets':datasets,'runs':args.runs,
            'shots':args.shots,'layout_mode':args.layout_mode,'optimizer_seeds':SEEDS,
            'optimization_level':args.optimization_level,'planned_jobs':proposed,
            'run_is_real':bool(args.submit),'qiskit_version':qiskit.__version__,
            'python_version':platform.python_version(),'qiskit_runtime_version':safe(lambda:__import__('importlib.metadata',fromlist=['version']).version('qiskit-ibm-runtime')),
            'methodological_limits':['This is RAW QPU / layout sensitivity; it is not direct crosstalk measurement.',
                                    'Single-qubit gate errors, T1/T2, and readout metrics are calibration estimates.',
                                    'Cross-QPU correlation is not causal noise attribution.']}
    save_json(config,out/'experiment_config.json')
    print(json.dumps(config,indent=2))
    if args.collect_only:
        summaries=[]
        for jobfile in sorted(out.glob('*/EIS*/*/run_*/job_submitted.json')):
            folder=jobfile.parent
            if (folder/'run_summary.json').exists():continue
            info=json.loads(jobfile.read_text(encoding='utf-8'))
            ds=info['dataset']
            if ds not in dataset_def:continue
            try:
                job=service.job(info['job_id'])
                status=str(job.status())
                print('JOB',info['job_id'],status,flush=True)
                if not any(x in status.upper() for x in ('DONE','COMPLETED')):continue
                result_info=job_result(job,folder,dataset_def[ds][3],info)
                backend_for_calib=safe(lambda:service.backend(info['backend']))
                if backend_for_calib is not None:save_calibration(backend_for_calib,folder,'after')
                info.update(result_info);info['status']='DONE';info['timestamp_utc']=utc()
                save_json(info,folder/'run_summary.json')
                append_manifest(out/'jobs_manifest.csv',info)
                summaries.append(info)
            except Exception as exc:
                print('COLLECT ERROR',info['job_id'],exc,file=sys.stderr)
        saved=[json.loads(f.read_text(encoding='utf-8')) for f in out.glob('*/EIS*/*/run_*/run_summary.json')]
        if saved:pd.DataFrame(saved).to_csv(out/'all_runs_summary.csv',index=False)
        print('Collected',len(summaries),'jobs. Saved results:',out)
        return
    if not args.submit:
        print('DRY RUN: no IBM Runtime jobs were submitted. Use --submit when ready.')
        return
    manifest=out/'jobs_manifest.csv';completed=0
    for run,ds,mode,backend in product(range(1,args.runs+1),datasets,modes,backends):
                    source,theta,logical,ideal=dataset_def[ds]
                    if args.max_jobs is not None and completed>=args.max_jobs: print('Max jobs reached');return
                    directory=out/backend.name/ds/mode/f'run_{run:02d}'
                    directory.mkdir(parents=True,exist_ok=True)
                    if (directory/'job_submitted.json').exists():
                        print('SKIP existing submitted job:',directory,flush=True)
                        continue
                    info={'backend':backend.name,'dataset':ds,'run':run,'layout_mode':mode,'directory':str(directory),'timestamp_utc':utc()}
                    try:
                        saved_fixed=out/backend.name/ds/'fixed'/'fixed_isa.qpy'
                        if mode=='fixed' and saved_fixed.exists():
                            with saved_fixed.open('rb') as f:isa=qpy.load(f)[0]
                        else:
                            pm=generate_preset_pass_manager(backend=backend,optimization_level=args.optimization_level)
                            isa=pm.run(logical)
                            if mode=='fixed':
                                saved_fixed.parent.mkdir(parents=True,exist_ok=True)
                                with saved_fixed.open('wb') as f:qpy.dump(isa,f)
                        calmeta,calq,calg=save_calibration(backend,directory,'before')
                        stats,one,two,selq=circuit_stats(isa,calg,calq)
                        obs_isa=[obs(i,isa.layout) for i in range(N_QUBITS)]
                        save_circuit(isa,directory)
                        stats['isa_qpy_sha256']=digest((directory/'circuit.qpy').read_bytes())
                        stats['fixed_isa_reference']=str(saved_fixed) if mode=='fixed' else None
                        pd.DataFrame(one).to_csv(directory/'oneq_operations.csv',index=False)
                        pd.DataFrame(two).to_csv(directory/'twoq_operations.csv',index=False)
                        pd.DataFrame(selq).to_csv(directory/'selected_physical_qubits.csv',index=False)
                        pd.DataFrame({'theta_index':range(len(theta)),'theta_rad':theta}).to_csv(directory/'theta.csv',index=False)
                        shutil.copy2(data_path_for[ds],directory/'input_dataset_backup.csv')
                        save_json({'optimizer_seed':SEEDS[ds],'theta_source':str(source),
                                  'theta_sha256':hash_theta(theta),'dataset':ds,
                                  'shots_requested':args.shots,'layout_mode':mode,'calibration_before':calmeta,
                                  **stats},directory/'run_config.json')
                        save_json({'backend':backend.name,'dataset':ds,'run':run,'shots':args.shots,'optimizer_seed':SEEDS[ds],
                                   'theta_sha256':hash_theta(theta),'layout_mode':mode,'submission_utc':utc(),
                                   'qiskit_version':qiskit.__version__},directory/'config.json')
                        save_json({'backend':backend.name,'dataset':ds,'run':run,**stats},directory/'transpiler_info.json')
                        with (directory/'observables.json').open('w',encoding='utf-8') as f:
                            json.dump([o.to_list() for o in obs_isa],f,indent=2,default=str)
                        estimator=EstimatorV2(mode=backend)
                        estimator.options.default_shots=args.shots
                        estimator.options.resilience_level=0
                        estimator.options.dynamical_decoupling.enable=False
                        estimator.options.twirling.enable_gates=False
                        estimator.options.twirling.enable_measure=False
                        job=estimator.run([(isa,obs_isa)])
                        info['job_id']=job.job_id();info['status']='SUBMITTED'
                        save_json(info,directory/'job_submitted.json')
                        save_json({'job_id':info['job_id'],'backend':backend.name,'status':'SUBMITTED','submitted_utc':utc()},directory/'job_info.json')
                        append_manifest(manifest,info)
                        completed+=1
                        print(f'[{completed}/{proposed}] SUBMITTED {backend.name} {ds} {mode} #{run}: {job.job_id()}',flush=True)
                        if args.submit_only:
                            continue
                        summary=job_result(job,directory,ideal,info)
                        info.update(summary);info['status']='DONE';info['timestamp_utc']=utc()
                        save_json(info,directory/'run_summary.json')
                        save_calibration(backend,directory,'after')
                        append_manifest(manifest,info)
                        print('DONE',backend.name,ds,mode,run,summary['expectation_MAE'],flush=True)
                    except Exception as exc:
                        info['status']='ERROR';info['error']=str(exc);info['timestamp_utc']=utc()
                        save_json({'error':str(exc),'traceback':traceback.format_exc(),**info},directory/'error.json')
                        append_manifest(manifest,info)
                        print('ERROR',backend.name,ds,mode,run,str(exc),file=sys.stderr,flush=True)
    summaries=list(out.glob('*/EIS*/**/run_summary.json'))
    if summaries:
        records=[json.loads(f.read_text(encoding='utf-8')) for f in summaries]
        pd.DataFrame(records).to_csv(out/'all_runs_summary.csv',index=False)
    print('Finished. All artifacts:',out)

if __name__=='__main__':main()
