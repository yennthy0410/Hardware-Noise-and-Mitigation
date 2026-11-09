#!/usr/bin/env python3
"""Restart-safe IBM mitigation collector/usage refresher/analyzer (never submits).

For every selected n: collect all submitted treatments, validate n PUBs x 7
expectations and standard errors for M0..M7, refresh actual IBM QPU usage, and analyze/reanalyze
when results or usage changed. Archives every pilot invocation's stdout/stderr.
"""
from __future__ import annotations
import argparse
import os
import csv
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from repository_paths import repository_root, MITIGATION_REL, pilot_script
REL = MITIGATION_REL
TREATMENTS = ('M0_Baseline','M1_DD','M2_TREX','M3_ZNE',
              'M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL')
OUTPUTS = ('per_repeat.csv','summary_mean_std.csv','params_table.csv',
           'tail_stats.csv','round_summary.json','distribution_pilot.png',
           'sse_plot_data.csv','05_mitigation_median_CI_convergence.png',
           '06_mitigation_three_tier_positioning.png',
           '04_mitigation_accuracy_cost.png','accuracy_cost_data.csv',
           'nyquist_pilot.png','nyquist_curves.csv','bode_pilot.png',
           'bode_curves.csv','residual_pilot.png','residual_curves.csv')

def utc(): return dt.datetime.now(dt.timezone.utc).isoformat()
def load(path):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc: raise RuntimeError(f'Invalid JSON {path}: {exc}') from exc

def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as f:
            json.dump(value,f,indent=2,ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)

def root_path(value):
    return repository_root(value)

def record_dir(base,n): return base/'output'/'03_IBM_execution_records'/f'n={n}_IBM_job_infor_recorded'

def records(base,n):
    result=[]
    for name in TREATMENTS:
        folder=record_dir(base,n)/name
        sub=folder/'job_submitted.json'
        info=load(sub) if sub.exists() else None
        if info and not info.get('job_id'): raise RuntimeError(f'Missing job_id in {sub}')
        state=folder/'submission_state.json'
        ambiguous=(not info and state.exists() and str(load(state).get('status','')).upper() in ('SUBMITTING','UNKNOWN'))
        result.append({'name':name,'folder':folder,'info':info,'ambiguous':ambiguous,
                       'collected':(folder/'result.json').is_file() and (folder/'expectations.csv').is_file()})
    return result

def verify_pubs(rec,n):
    path=rec['folder']/'expectations.csv'
    if not rec['info'] or not rec['collected']: return 'result or expectations missing'
    try:
        with path.open(newline='',encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
        if len(rows)!=n: return f'{len(rows)} PUBs, expected {n}'
        seen=set()
        for row in rows:
            i=int(row['pub'])
            if i in seen: return 'duplicate PUB index'
            seen.add(i)
            for k in range(7):
                for col in (f"Z{k}", f"std_Z{k}"):
                    raw=row.get(col)
                    if raw is None or not str(raw).strip(): return f"missing {col} at PUB {i}"
                    v=float(raw)
                    if not math.isfinite(v): return f"nonfinite {col} at PUB {i}"
                    if col.startswith("std_") and v<0: return f"negative {col} at PUB {i}"
        if seen!=set(range(1,n+1)): return 'PUB indexes incomplete'
        return None
    except (OSError,ValueError,KeyError,TypeError) as exc: return str(exc)

def usage(rec):
    """Read only verified measured usage, never queue duration or estimates."""
    folder=rec['folder']
    for file in ('job_usage_raw.json','job_retrieved_metadata.json','job_metrics_raw.json'):
        p=folder/file
        if not p.is_file(): continue
        obj=load(p)
        candidates=[]
        if isinstance(obj,dict):
            candidates += [obj.get('qpu_usage_s'),obj.get('quantum_seconds'),obj.get('qpu_seconds')]
            for key in ('usage','metrics','metrics_raw'):
                item=obj.get(key)
                if isinstance(item,dict):
                    candidates += [item.get('quantum_seconds'),item.get('qpu_seconds'),item.get('qpu_usage_s')]
                elif key=='usage': candidates.append(item)
        for v in candidates:
            if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>=0:
                return float(v)
    return None

def fingerprint(recs):
    """Content hash of source files that influence analysis; catches late usage."""
    digest=hashlib.sha256()
    for rec in recs:
        for name in ('job_submitted.json','expectations.csv','job_usage_raw.json',
                     'job_retrieved_metadata.json','job_metrics_raw.json'):
            p=rec['folder']/name
            digest.update((rec['name']+'/'+name).encode())
            if p.is_file(): digest.update(hashlib.sha256(p.read_bytes()).digest())
            else: digest.update(b'MISSING')
    return digest.hexdigest()

def run_pilot(args,project,pilot,logs,phase,n,label=None,account=None,instance=None):
    cmd=[sys.executable,str(pilot),phase,'--project',str(project),'--round',f'n={n}','--repeats',str(n)]
    if label is not None: cmd+=['--account-label',label]
    if account: cmd+=['--ibm-account',account]
    if instance: cmd+=['--instance',instance]
    started=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    log=logs/f'n={n}_{phase}_{started}.log'
    print(f'[{utc()}] n={n} {phase} account={account or "legacy-default"} -> {log.name}',flush=True)
    env=os.environ.copy()
    env['PYTHONIOENCODING']='utf-8'
    env['PYTHONUTF8']='1'
    with log.open('w',encoding='utf-8') as f:
        f.write('COMMAND: '+subprocess.list2cmdline(cmd)+'\nSTARTED: '+utc()+'\n\n'); f.flush()
        try:
            completed=subprocess.run(cmd,cwd=project,env=env,stdout=f,stderr=subprocess.STDOUT,
                                     timeout=args.command_timeout,check=False)
            code=completed.returncode
        except subprocess.TimeoutExpired:
            code=124;f.write('\nTIMEOUT: process stopped; jobs on IBM are unchanged.\n')
        f.write(f'\nFINISHED: {utc()} EXIT: {code}\n')
    if code:
        print(f'  WARNING: {phase} exit={code}; see {log}',flush=True)
    return code,log

def identity_groups(recs,args):
    groups={}
    for rec in recs:
        info=rec['info']
        if not info: continue
        label=info.get('account_label')
        instance=info.get('instance')
        groups.setdefault((label,instance),[]).append(rec)
    resolved=[];errors=[]
    for (label,instance),group in groups.items():
        account=args.account_map.get(label or '')
        if not account and not args.allow_legacy_default:
            errors.append(f'No account mapping for provenance {label!r}, instance {instance!r}; use --account-map LABEL=SAVED or --allow-legacy-default')
            continue
        # The IBM account must have permission to read these particular jobs.
        resolved.append((label,instance,account,group))
    return resolved,errors

def process_round(args,project,base,pilot,logs,n,previous):
    state={'updated_utc':utc(),'n':n,'errors':[],'status':'WAITING'}
    recs=records(base,n)
    if any(r['ambiguous'] for r in recs):
        state['errors'].append('Unresolved SUBMITTING state; reconcile manually before any new submission')
    groups,errs=identity_groups(recs,args);state['errors']+=errs
    # Collect is idempotent: pilot skips already-collected result files.
    for label,instance,account,group in groups:
        if any(not r['collected'] for r in group):
            code,log=run_pilot(args,project,pilot,logs,'collect',n,label,account,instance)
            if code: state['errors'].append(f'collect failed ({code}): {log.name}')
    recs=records(base,n)
    checks={r['name']:verify_pubs(r,n) for r in recs}
    state['submitted_jobs']=sum(bool(r['info']) for r in recs)
    state['valid_treatments']=sum(v is None for v in checks.values())
    state['invalid_treatments']={k:v for k,v in checks.items() if v is not None}
    # Refresh usage even before all treatments finish; this preserves partial IBM
    # cost records and retries missing values on subsequent polling cycles.
    # Only run for groups with at least one locally missing usage value.
    for label,instance,account,group in groups:
        if any(usage(r) is None for r in group):
            code,log=run_pilot(args,project,pilot,logs,'refresh-usage',n,label,account,instance)
            if code: state['errors'].append(f'refresh-usage failed ({code}): {log.name}')
    recs=records(base,n)
    measured={r['name']:usage(r) for r in recs if r['info']}
    state['qpu_usage_s']=measured
    state['usage_measured']=sum(v is not None for v in measured.values())
    state['usage_missing']=[r['name'] for r in recs if not r['info'] or measured.get(r['name']) is None]
    state['qpu_usage_status_csv']=str(record_dir(base,n)/'qpu_usage_status.csv')
    if state['valid_treatments']!=8:
        state['status']='INVALID_PUBS_NEED_RECOVERY' if any('nonfinite' in str(x) or 'missing Z' in str(x) or 'missing std_' in str(x) for x in checks.values() if x) else 'WAITING_FOR_RESULTS'
        return state
    out=base/'output'/'02_run_level_data'/f'n={n}'
    missing=[f for f in OUTPUTS if not (out/f).is_file()]
    sig=fingerprint(recs)
    previous_sig=previous.get('analysis_input_sha256')
    needs_analyze=bool(missing or args.reanalyze or previous_sig!=sig)
    # When QPU usage is unavailable, the pilot still generates a placeholder
    # accuracy/cost chart. Mark incomplete, keep retrying usage and reanalyzing
    # when real measurements appear. Never present placeholder as cost data.
    if needs_analyze:
        code,log=run_pilot(args,project,pilot,logs,'analyze',n)
        if code: state['errors'].append(f'analyze failed ({code}): {log.name}')
        else: state['analysis_input_sha256']=sig
    else:
        state['analysis_input_sha256']=sig
    state['missing_outputs']=[f for f in OUTPUTS if not (out/f).is_file()]
    state['analyzed']=not state['missing_outputs'] and bool(state.get('analysis_input_sha256'))
    state['status']='COMPLETE' if state['analyzed'] and state['usage_measured']==8 else ('WAITING_FOR_USAGE' if state['analyzed'] else 'ANALYSIS_INCOMPLETE')
    return state

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project',type=Path)
    ap.add_argument('--pilot-script',type=Path)
    ap.add_argument('--n-values',type=int,nargs='+',default=[5,10,15,20,25,30])
    ap.add_argument('--account-map',action='append',default=[],metavar='LABEL=SAVED_ACCOUNT')
    ap.add_argument('--allow-legacy-default',action='store_true',help='Use default IBM credentials for unmapped job provenance')
    ap.add_argument('--interval',type=int,default=180)
    ap.add_argument('--command-timeout',type=int,default=900)
    ap.add_argument('--once',action='store_true')
    ap.add_argument('--reanalyze',action='store_true')
    ap.add_argument('--stop-when-complete',action='store_true',help='Exit when all selected rounds have all results, usage and outputs')
    args=ap.parse_args()
    if not args.n_values or min(args.n_values)<1 or len(set(args.n_values))!=len(args.n_values): ap.error('n-values must be unique positive integers')
    if args.interval<30 or args.command_timeout<30: ap.error('interval/timeout must be >= 30')
    pairs={}
    for value in args.account_map:
        if '=' not in value: ap.error('Use --account-map LABEL=SAVED_ACCOUNT')
        label,account=value.split('=',1)
        if not label or not account: ap.error('Empty account mapping')
        if label in pairs and pairs[label]!=account: ap.error(f'Conflicting mapping for {label}')
        pairs[label]=account
    args.account_map=pairs
    project=root_path(args.project)
    base=project/REL
    pilot=pilot_script(project,args.pilot_script)
    if not pilot.is_file(): ap.error(f'Pilot script not found: {pilot}')
    logs=base/'output'/'04_runtime_logs';logs.mkdir(parents=True,exist_ok=True)
    checkpoint=logs/'qpu_auto_manager_state.json'
    lock=logs/'qpu_auto_manager.lock'
    try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError: raise SystemExit(f'Lock exists: {lock}. Check whether another manager is running; remove only if stale.')
    with os.fdopen(fd,'w') as f: f.write(f'pid={os.getpid()} started={utc()}\n')
    try:
        while True:
            previous=load(checkpoint).get('rounds',{}) if checkpoint.is_file() else {}
            current={'updated_utc':utc(),'rounds':{},'all_complete':False}
            for n in args.n_values:
                try: current['rounds'][f'n={n}']=process_round(args,project,base,pilot,logs,n,previous.get(f'n={n}',{}))
                except Exception as exc:
                    current['rounds'][f'n={n}']={'status':'ERROR','errors':[f'{type(exc).__name__}: {exc}'],'updated_utc':utc()}
                    print(f'n={n} ERROR: {exc}',file=sys.stderr,flush=True)
                atomic(checkpoint,current)
                s=current['rounds'][f'n={n}']
                print(f'n={n}: {s["status"]}, valid={s.get("valid_treatments",0)}/8, QPU usage={s.get("usage_measured",0)}/8',flush=True)
            current['all_complete']=all(s.get('status')=='COMPLETE' for s in current['rounds'].values())
            current['updated_utc']=utc();atomic(checkpoint,current)
            if args.once or (args.stop_when_complete and current['all_complete']): break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('Stopped. IBM jobs and local results remain intact.',flush=True)
    finally: lock.unlink(missing_ok=True)

if __name__=='__main__': main()
