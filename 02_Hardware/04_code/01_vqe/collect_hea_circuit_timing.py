#!/usr/bin/env python3
"""Offline ISA duration estimation and per-run coherence exposure.

Uses IBM Target *instruction durations* (current backend by default), not job/QPU usage.
No quantum jobs are submitted. Appends only timing-specific outputs in each run.
"""
from __future__ import annotations
import argparse, csv, json, math, re, sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

QPU_NAMES = ('ibm_fez', 'ibm_kingston', 'ibm_marrakesh')
MODES = ('fixed', 'unfixed')


def utc_now(): return datetime.now(timezone.utc).isoformat()
def read_json(path):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError, UnicodeError): return None

def good(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError): return None

def extract_value(entry):
    if isinstance(entry, dict):return good(entry.get('value', entry.get('val')))
    return good(entry)

def read_used_qubits(run):
    p=run/'selected_physical_qubits.csv'; out=set()
    if p.is_file():
        with p.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                for key in ('physical_qubit','physical_index','physical_qubit_index','physical','qubit'):
                    if key in row:
                        value=good(row[key])
                        if value is not None and value >= 0:out.add(int(value));break
    if out:return sorted(out)
    j=read_json(run/'transpiler_info.json') or {}
    for key in ('physical_qubits','selected_physical_qubits','final_layout'):
        if isinstance(j.get(key),list):
            out={int(q) for q in j[key] if isinstance(q,int) and q>=0}
            if out:return sorted(out)
    return []

def parse_qubit_calibrations(run,used):
    """Prefer BackendProperties JSON with explicit seconds; CSV 't2_us' may have mixed units.

    Return qubit->T2_seconds, notes documenting any units inference.
    """
    folder=run/'calibrations'; items={}; notes=[]
    if not folder.is_dir():return items,['calibrations directory not found']
    files=sorted(folder.rglob('*'))
    # JSON IBM BackendProperties: t2.value is in seconds.
    for p in files:
        if p.suffix.lower()!='.json':continue
        raw=read_json(p)
        def explore(obj):
            if isinstance(obj,dict):
                if isinstance(obj.get('qubits'),list):yield obj
                for v in obj.values():yield from explore(v)
            elif isinstance(obj,list):
                for v in obj:yield from explore(v)
        for props in explore(raw):
            for q,entry in enumerate(props['qubits']):
                if q not in used:continue
                if isinstance(entry,list):
                    for metric in entry:
                        if isinstance(metric,dict) and str(metric.get('name','')).lower()=='t2':
                            val=extract_value(metric)
                            unit=str(metric.get('unit','s')).lower()
                            scale={'s':1,'sec':1,'us':1e-6,'µs':1e-6,'ns':1e-9}.get(unit)
                            if val is not None and val>0 and scale is not None:
                                items[q]=(val*scale,str(p),f'json:{unit}')
                elif isinstance(entry,dict):
                    v=extract_value(entry.get('t2'))
                    if v is not None and v>0:items[q]=(v,str(p),'json:assumed_seconds')
    # CSV only if JSON not available; unit is inferred and disclosed when mislabeled.
    for p in files:
        if p.suffix.lower()!='.csv':continue
        try:
            with p.open(encoding='utf-8-sig',newline='') as f:
                for row in csv.DictReader(f):
                    lowered={str(k).lower().strip():v for k,v in row.items() if k is not None}
                    q=next((good(lowered[k]) for k in ('physical_qubit','qubit','qubit_index','index') if k in lowered),None)
                    if q is None or int(q) not in used or int(q) in items:continue
                    val=None;unit=None
                    if 't2' in lowered and good(lowered['t2']) is not None:
                        val=good(lowered['t2']);unit='seconds_from_t2'
                    elif 't2_us' in lowered and good(lowered['t2_us']) is not None:
                        rawval=good(lowered['t2_us'])
                        if rawval is not None:
                            if rawval<0.01:
                                # Source in supplied calibration CSV saves seconds in a t2_us column.
                                val=rawval;unit='inferred_seconds_from_mislabeled_t2_us'
                                notes.append(f'{p.name}: t2_us={rawval} interpreted as seconds (<0.01); verify source calibration')
                            else:val=rawval*1e-6;unit='microseconds_from_t2_us'
                    if val is not None and val>0:items[int(q)]=(val,str(p),unit)
        except (OSError,UnicodeError,csv.Error) as exc:notes.append(f'CSV parse failed {p}: {exc}')
    return items,notes

def duration_from_circuit(circ,backend):
    """Estimate longest-path ISA circuit instruction duration without rerouting/transpiling."""
    target=backend.target
    if not hasattr(circ,'estimate_duration'):
        raise RuntimeError('Qiskit does not support QuantumCircuit.estimate_duration; upgrade Qiskit')
    # Current Qiskit 2.x accepts keyword or positional target.
    try:d=circ.estimate_duration(target=target,unit='s')
    except TypeError:d=circ.estimate_duration(target,unit='s')
    dur=good(d)
    if dur is None or dur<=0:raise RuntimeError(f'Unusable duration: {d!r}')
    return dur

def write_json(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False,default=str),encoding='utf8')

def write_csv(path,rows,fields):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def find_root(script):
    for path in (Path.cwd(),*Path.cwd().parents,script.parent,*script.parents):
        candidate=path/'experiments'/'HEA_VQE'/'01_simulator_to_qpu_hardware'/'results'/'EIS14'/'01_run_qpu'
        if candidate.is_dir():return candidate.resolve()
    raise SystemExit('Cannot detect QPU runs. Use --qpu-root.')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--qpu-root',type=Path,help='Root containing ibm_*/EIS14/fixed or unfixed/run_XX')
    ap.add_argument('--modes',nargs='+',choices=MODES,default=list(MODES))
    ap.add_argument('--backends',nargs='+',choices=QPU_NAMES,default=list(QPU_NAMES))
    ap.add_argument('--instance',help='IBM Quantum Platform instance CRN (optional)')
    ap.add_argument('--offline',action='store_true',help='Only recalculate exposure from existing circuit_timing.json; do not connect IBM')
    ap.add_argument('--force',action='store_true',help='Recompute existing timing outputs (otherwise reuse them)')
    args=ap.parse_args()
    root=args.qpu_root.resolve() if args.qpu_root else find_root(Path(__file__).resolve())
    print('QPU run root:',root)
    service=None
    if not args.offline:
        from qiskit_ibm_runtime import QiskitRuntimeService
        kwargs={'channel':'ibm_quantum_platform'}
        if args.instance:kwargs['instance']=args.instance
        service=QiskitRuntimeService(**kwargs)
    from qiskit import qpy
    targets={};collected=[];failures=0
    for name in args.backends:
        for mode in args.modes:
            for idx in range(1,6):
                run=root/name/'EIS14'/mode/f'run_{idx:02d}'
                qpy_path=run/'circuit.qpy'
                if not qpy_path.is_file():continue
                path=run/'circuit_timing.json'
                meta=read_json(path) if path.is_file() and not args.force else None
                if not isinstance(meta,dict) or good(meta.get('circuit_duration_s')) is None:
                    meta={'backend':name,'mode':mode,'run':idx,'analyzed_at_utc':utc_now(),
                          'duration_kind':'target_based_ISA_critical_path_estimate','is_observed_qpu_execution_time':False}
                    if args.offline:
                        meta['status']='missing_saved_timing'
                    else:
                        try:
                            if name not in targets:targets[name]=service.backend(name)
                            with qpy_path.open('rb') as f:circ=qpy.load(f)[0]
                            dur=duration_from_circuit(circ,targets[name])
                            meta.update(status='estimated',circuit_duration_s=dur,circuit_duration_us=dur*1e6,
                                        isa_depth=circ.depth(),loaded_circuit_qubits=circ.num_qubits,
                                        timing_source='CURRENT backend.target; not historical calibrated duration',
                                        historical_job_timing_confirmed=False)
                        except Exception as exc:
                            meta.update(status='error',error=f'{type(exc).__name__}: {exc}')
                    write_json(path,meta)
                used=read_used_qubits(run)
                vals,notes=parse_qubit_calibrations(run,used)
                dur=good(meta.get('circuit_duration_s'))
                records=[]
                for q in used:
                    value=vals.get(q)
                    t2=value[0] if value else None
                    ratio=dur/t2 if dur and t2 and t2>0 else None
                    records.append({'backend':name,'mode':mode,'run':idx,'physical_qubit':q,
                                    'T2_us':t2*1e6 if t2 else '',
                                    'circuit_duration_us':dur*1e6 if dur else '',
                                    'circuit_over_T2':ratio if ratio is not None else '',
                                    'T2_source':value[1] if value else '',
                                    'T2_unit_interpretation':value[2] if value else '',
                                    'timing_source':meta.get('timing_source','')})
                write_csv(run/'circuit_coherence_exposure.csv',records,
                          ['backend','mode','run','physical_qubit','T2_us','circuit_duration_us','circuit_over_T2','T2_source','T2_unit_interpretation','timing_source'])
                ratios=[float(r['circuit_over_T2']) for r in records if r['circuit_over_T2']!='']
                summary={'backend':name,'mode':mode,'run':idx,'job_id':None,
                         'circuit_duration_us':dur*1e6 if dur else None,
                         'median_circuit_over_T2':median(ratios) if ratios else None,
                         'max_circuit_over_T2':max(ratios) if ratios else None,
                         'n_used_physical_qubits':len(used),'n_T2_available':len(ratios),
                         'timing_status':meta.get('status'),'T2_notes':notes,
                         'important':'Derived from target timing and saved calibration, NOT job execution time or QPU usage.'}
                for fn in ('job_submitted.json','job_retrieved_metadata.json','job_info.json'):
                    j=read_json(run/fn)
                    if isinstance(j,dict):
                        summary['job_id']=summary['job_id'] or j.get('job_id') or j.get('id')
                write_json(run/'circuit_coherence_summary.json',summary)
                collected.append({k:summary[k] for k in ('backend','mode','run','job_id','circuit_duration_us','median_circuit_over_T2','max_circuit_over_T2','n_used_physical_qubits','n_T2_available','timing_status')})
                print(f'{name:15} {mode:7} run_{idx:02d}: duration={summary["circuit_duration_us"]} us; median exposure={summary["median_circuit_over_T2"]}; qubits={len(used)}; status={meta.get("status")}')
                if not ratios:failures+=1
    if not collected:raise SystemExit('No circuit.qpy found. Check --qpu-root.')
    # Index only; all detailed data goes into each run.
    index=root/'circuit_timing_runs_index.csv'
    write_csv(index,collected,list(collected[0].keys()))
    print(f'\nProcessed {len(collected)} runs; {failures} without exposure. Per-run files saved. Index: {index}')

if __name__=='__main__':main()
