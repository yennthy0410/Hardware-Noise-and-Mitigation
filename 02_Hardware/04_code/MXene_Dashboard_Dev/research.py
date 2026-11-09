"""Bounded research tools over publication tables. No scientific engine imports.

Reuse canonical per-PUB parameters/SSE/RMSE; descriptive summaries are not
independence-adjusted inference. Internal evidence never enters ordinary UI.
"""
import csv, json, math, re, statistics, os, uuid
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal
from reader import HERE

LEVELS=(5,10,15,20,25,30)
METRICS=('SSE','RMSE','Rs','L','Rct','Q1','n1','Q2','n2')
LABELS={'M0_Baseline':'None','M1_DD':'DD','M2_TREX':'TREX','M3_ZNE':'ZNE','M4_DD_TREX':'DD+TREX','M5_DD_ZNE':'DD+ZNE','M6_TREX_ZNE':'TREX+ZNE','M7_ALL':'ALL'}
UNITS={'SSE':'ohm²','RMSE':'ohm','Rs':'ohm','Rct':'ohm','L':'H','Q1':'S s^n1','Q2':'S s^n2','n1':'dimensionless','n2':'dimensionless'}
class ToolRequest(BaseModel):
    tool:Literal['compare_treatments','calculate_eis_metrics','analyze_parameter_drift','calculate_uncertainty','compare_n_levels','generate_derived_chart']='compare_treatments'
    levels:list[Literal[5,10,15,20,25,30]]=Field(default_factory=lambda:[5],max_length=6)
    metric:Literal['SSE','RMSE','Rs','L','Rct','Q1','n1','Q2','n2']='SSE'
    treatments:list[str]=Field(default_factory=list,max_length=8)

def select_question(message, history=(), default_n=None):
    text=message.lower()
    if re.search(r'residual|nyquist|bode',text):
        raise ValueError('For frequency-resolved analysis, use the matching Results figure and its deterministic interpretation. Conversational comparison currently supports SSE, RMSE and the seven EIS parameters.')
    explicit=re.findall(r'\bn\s*[=:\-]?\s*(5|10|15|20|25|30)\b',text)
    all_levels=bool(re.search(r'all.*(?:levels|n\b)|across n|versus n|vs\.? n|increasing n',text))
    listed=re.search(r'\bn\s*[=:\-]?\s*((?:5|10|15|20|25|30)(?:\s*,\s*(?:5|10|15|20|25|30))+)',text)
    if listed:explicit=re.findall(r'\d+',listed.group(1))
    if all_levels: levels=list(LEVELS)
    elif explicit: levels=list(dict.fromkeys(map(int,explicit)))
    else:
        prior=next((getattr(m,'content','') for m in reversed(history) if getattr(m,'role','')=='user'), '')
        levels=select_question(prior,(),default_n).levels if prior else [default_n] if default_n in LEVELS else list(LEVELS)
    metric=next((m for m in METRICS if re.search(r'\b'+m.lower()+r'\b',text)),None)
    if metric is None:
        prior=next((getattr(m,'content','') for m in reversed(history) if getattr(m,'role','')=='user'), '')
        metric=select_question(prior,(),default_n).metric if prior else 'SSE'
    treatments=[]
    # Longest combined labels first; do not confuse DD+ZNE with DD and ZNE.
    remaining=text.replace(' ','')
    for tid,label in sorted(LABELS.items(),key=lambda p:-len(p[1])):
        if label!='None' and label!='ALL' and label.lower() in remaining:
            treatments.append(tid);remaining=remaining.replace(label.lower(),'')
    if re.search(r'\bnone\b|\bbaseline\b',text):treatments.append('M0_Baseline')
    if not treatments and not re.search(r'all|compare treatments',text):
        prior=next((getattr(m,'content','') for m in reversed(history) if getattr(m,'role','')=='user'), '')
        if prior:treatments=select_question(prior,(),default_n).treatments
    tool='generate_derived_chart' if re.search(r'\b(plot|chart|graph|show)\b',text) else 'compare_n_levels' if len(levels)>1 else 'compare_treatments'
    if re.search(r'uncertainty|confidence|interval',text):tool='calculate_uncertainty'
    if re.search(r'drift|propagation|sensitivity',text):tool='analyze_parameter_drift'
    if re.search(r'\b(plot|chart|graph|show)\b',text) and not re.search(r'uncertainty|confidence|interval',text):tool='generate_derived_chart'
    return ToolRequest(tool=tool,levels=levels,metric=metric,treatments=treatments)

def read_rows(reader, artifact):
    path=reader.artifact(artifact['id'])
    if path.stat().st_size>4_000_000:raise ValueError('Research table exceeds bounded workload')
    with path.open(encoding='utf-8-sig',newline='') as f:
        rows=[]
        for row in csv.DictReader(f):
            if len(rows)>=1000:raise ValueError('Too many research rows')
            rows.append(row)
    return rows

def execute(reader, request):
    if any(t not in LABELS for t in request.treatments):raise ValueError('Unknown treatment')
    s=reader.status();results=[];evidence=[];missing=[];lineage=[]
    for n in request.levels:
        assets=[a for a in s['artifacts'] if a['level']==n and a['name']=='params_table.csv']
        if not assets:missing.append(n);continue
        a=max(assets,key=lambda a:a['version']);rows=read_rows(reader,a);evidence.append(a)
        validation=next((v for v in s['artifacts'] if v['level']==n and v['version']==a['version'] and v['name']=='per_pub_validation.csv'),None)
        if not validation:missing.append(n);continue
        vrows=read_rows(reader,validation);evidence.append(validation)
        accepted={(r['treatment'],int(slot['logical_pub_slot'])):slot.get('accepted_job_id') for r in s.get('recovery_summary',[]) if r['n']==n for slot in r.get('slots',[]) if slot.get('accepted_job_id')}
        mapped={(v.get('treatment'),v.get('IBM_job_id'),v.get('PUB_index')):int(v['logical_pub_index']) for v in vrows if v.get('logical_pub_index') and v.get('selected_for_repaired_dataset','').lower()=='true'}
        groups={};identities=set();logical_identities=set()
        for row in rows:
            tid=row.get('treatment');identity=(tid,row.get('IBM_job_id'),row.get('PUB_index'))
            if int(row['n'])!=n or tid not in LABELS or identity in identities:raise ValueError('Ambiguous or duplicate research rows')
            identities.add(identity)
            slot=mapped.get(identity)
            if not slot or accepted.get((LABELS[tid],slot))!=row.get('IBM_job_id'):continue
            if (tid,slot) in logical_identities:raise ValueError('Duplicate logical observation')
            logical_identities.add((tid,slot))
            value=float(row[request.metric])
            if not math.isfinite(value):raise ValueError('Nonfinite scientific value')
            groups.setdefault(tid,[]).append(value)
            lineage.append(dict(n=n,treatment=tid,logical_slot=slot,job_id=row['IBM_job_id'],pub_index=row['PUB_index']))
        if not groups:missing.append(n);continue
        if any(len(v)>n for v in groups.values()):raise ValueError('Too many accepted observations')
        baseline=statistics.median(groups['M0_Baseline']) if groups.get('M0_Baseline') else None
        for tid,values in groups.items():
            if request.treatments and tid not in request.treatments:continue
            median=statistics.median(values)
            results.append(dict(n=n,treatment=LABELS[tid],count=len(values),metric=request.metric,unit=UNITS[request.metric],median=median,mean=statistics.mean(values),sd=statistics.stdev(values) if len(values)>1 else None,min=min(values),max=max(values),difference_from_none=median-baseline if baseline is not None else None,improvement_pct=(100*(baseline-median)/baseline if baseline and request.metric in ('SSE','RMSE') else None)))
    sentences=[]
    for r in results:
        sd_text=f"{r['sd']:.6g}" if r['sd'] is not None else 'unavailable'
        line=f"n={r['n']}, {r['treatment']}: median {r['metric']} {r['median']:.6g} {r['unit']}; mean {r['mean']:.6g}, sample SD {sd_text}; {r['count']} accepted observations from a planned {r['n']}; incomplete subsets remain preliminary."
        if r['improvement_pct'] is not None:line+=f" Median reduction versus None: {r['improvement_pct']:.6g}% (negative means worse)."
        sentences.append(line)
    if request.metric in ('SSE','RMSE'):
        for n in request.levels:
            group=[r for r in results if r['n']==n]
            if group:
                best=min(group,key=lambda r:r['median'])
                sentences.append(f"Within the selected treatments at n={n}, {best['treatment']} has the lowest median {request.metric}: {best['median']:.6g} {best['unit']}. This is a descriptive ranking, not a significance test.")
    if len(request.levels)>1:
        for treatment in LABELS.values():
            group=sorted([r for r in results if r['treatment']==treatment],key=lambda r:r['n'])
            if len(group)>1:
                first,last=group[0],group[-1];delta=last['median']-first['median']
                sentences.append(f"{treatment}: median {request.metric} changes by {delta:.6g} {first['unit']} from n={first['n']} to n={last['n']}. Independent experiments with different sample sizes do not establish a causal effect of increasing n.")
    sentences.append('Physical interpretation: the canonical circuit combines series resistance and inductance, a charge-transfer resistance in parallel with a constant-phase element, and a second series constant-phase element. Resistance shifts, inductive high-frequency response and distributed capacitive response can influence the spectrum. Parameter changes alone cannot distinguish quantum noise from fitting sensitivity or experimental drift.')
    warning='Descriptive variability is not uncertainty of the mean. Shared execution dependence is not removed by separate PUB rows; confidence intervals and significance are unavailable without verified independent groups.'
    if request.metric in ('Q1','Q2'):warning+=' Constant-phase coefficients have exponent-dependent units; comparisons across varying exponents are descriptive and not direct capacitance comparisons.'
    if request.tool=='analyze_parameter_drift':warning+=' Parameter shifts do not establish measurement-noise causality. Error propagation requires a verified joint covariance/Jacobian and is not computed here.'
    if missing:sentences.append('Scientific analysis is not yet available for n='+', '.join(map(str,missing))+'.')
    chart=None
    if request.tool=='generate_derived_chart' and results:
        chart={'series':[{'name':label,'x':[r['n'] for r in results if r['treatment']==label],'y':[r['median'] for r in results if r['treatment']==label],'mode':'markers','type':'scatter'} for label in LABELS.values() if any(r['treatment']==label for r in results)],'x_label':'n (planned PUBs per treatment)','y_label':'Median '+request.metric+' ('+UNITS[request.metric]+')'}
    return {'rows':results,'sentences':sentences,'warning':warning,'chart':chart,'evidence':evidence,'request':request.model_dump(),'captured_at':s.get('captured_at'),'method':'Canonical per-PUB exports; descriptive sample statistics only','missing':missing,'accepted_lineage':lineage,'calculation_version':'research-descriptive-v1'}

def save_evidence(record):
    directory=HERE/'state'/'research';directory.mkdir(exist_ok=True)
    key=uuid.uuid4().hex;path=directory/(key+'.json');tmp=directory/(key+'.tmp')
    payload=json.dumps(record,allow_nan=False,ensure_ascii=False).encode('utf-8')
    with tmp.open('xb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    return key

def validate_selection(raw, sentences):
    """LLM selects evidence sentences; it cannot author unverified numeric prose."""
    obj=json.loads(raw)
    if set(obj)!={'sentence_ids'} or not isinstance(obj['sentence_ids'],list):raise ValueError('Invalid research answer contract')
    ids=obj['sentence_ids']
    if len(ids)>48 or any(type(i) is not int or i<0 or i>=len(sentences) for i in ids):raise ValueError('Unverified scientific claim')
    return [sentences[i] for i in dict.fromkeys(ids)]

# Dashboard bridge: dispatch only audited reads, never manager CLI commands.
class BridgeRequest(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    operation:Literal['get_campaign_status','get_worker_status','get_ibm_job_status','check_ibm_quota','get_validated_results','analyze_existing_results','compare_mitigation_treatments','export_derived_figures']
    n:Literal[5,10,15,20,25,30]|None=None
    pending_only:bool=False
    question:str=Field(default='',max_length=4000)

def route_command(message, n=None):
    text=message.strip().lower()
    # Deny action requests before any read/scientific routing.
    if re.search(r'\b(start|stop|pause|resume|submit|cancel|resubmit|switch account|recover jobs|run worker)\b',text):
        raise ValueError('Manager control and IBM actions are not enabled in this bridge.')
    match=re.search(r'\bn\s*=\s*(5|10|15|20|25|30)\b',text)
    if match:n=int(match.group(1))
    operation=None
    if re.search(r'\bquota\b',text):operation='check_ibm_quota'
    elif re.search(r'\bworker\b',text):operation='get_worker_status'
    elif re.search(r'\bjobs?\b',text):operation='get_ibm_job_status'
    elif re.search(r'campaign (progress|status)|campaign doing',text):operation='get_campaign_status'
    elif re.match(r'(show|get) (validated )?results',text):operation='get_validated_results'
    elif re.match(r'(analy[sz]e|compare|plot|export|what about|and (sse|rmse|rct)|show (sse|rmse|rct))\b',text):
        operation='export_derived_figures' if re.match(r'(plot|export)',text) else 'compare_mitigation_treatments' if text.startswith('compare') else 'analyze_existing_results'
    if operation is None:return None
    return BridgeRequest(operation=operation,n=n,pending_only=bool(re.search(r'pending|queued|running',text)),question=message)

def _manager_modules():
    import sys
    from reader import PROD
    sys.dont_write_bytecode=True
    path=str(PROD/'03_python_automation')
    if path not in sys.path:sys.path.insert(0,path)
    from job_registry import JobRegistry
    import quota
    import final_configuration
    return JobRegistry,quota,final_configuration

def _workflow_skill():
    import sys,hashlib
    from reader import PROD
    path=str(PROD/'01_ai_agent')
    if path not in sys.path:sys.path.insert(0,path)
    from offline_tools import load_skill
    text=load_skill('workflow-manager')
    return {'name':'workflow-manager','sha256':hashlib.sha256(text.encode()).hexdigest()}

def bridge(reader, request, *, history=()):
    from reader import CAMPAIGN,OUT,read_json
    from collections import Counter
    if Path.cwd().resolve()!=HERE.parents[2].resolve():
        raise ValueError('Manager archive paths require the workspace working directory. Launch using start_dashboard.ps1; no manager data were changed.')
    JobRegistry,quota,config=_manager_modules()
    skill=_workflow_skill()
    snapshot=reader.status()
    if snapshot.get('offline'):raise ValueError('The local snapshot is unavailable; current manager evidence cannot be verified.')
    operation=request.operation
    result={'operation':operation,'captured_at':snapshot.get('captured_at'),'source_mode':'persisted local evidence; no IBM lookup','skill':skill}
    sentences=[];rows=[];chart=None
    if operation in ('get_campaign_status','get_worker_status','check_ibm_quota'):
        # Serialize with the existing snapshot writer; never touch the production DB.
        with reader.lock:
            registry=JobRegistry(HERE/'state/snapshot.sqlite3',readonly=True)
            status=registry.independent_campaign_status(CAMPAIGN,event_limit=5)
            result['manager_status']=status
            if operation=='check_ibm_quota':
                reservations=quota.provider_reservations(registry)
        if operation=='get_campaign_status':
            sentences=[f"Recorded campaign state: {status.get('state','Unavailable')}. Accepted logical PUBs in this snapshot: {snapshot.get('valid','Unavailable')} of {snapshot.get('expected','Unavailable')}."]
            sentences.append('This is the persisted manager state, not a new IBM status lookup.')
        elif operation=='get_worker_status':
            directory=OUT/'03_IBM_execution_records/ai_assistant/independent_n_campaigns'/CAMPAIGN
            result['lock_files_present']=[p.is_file() for p in (directory/'worker.lock',directory.parent/'auto_manager.lock')]
            sentences=[f"Recorded campaign state: {status.get('state','Unavailable')}. Active worker ownership is UNKNOWN: lock-file existence and checkpoints cannot establish process liveness. No lock was acquired and no worker was started or interrupted."]
        else:
            directory=OUT/'03_IBM_execution_records/ai_assistant/preflight_reports'
            for label,reservation in reservations.items():
                path=max(directory.glob('preflight_'+label+'_*.json'),default=None)
                record={'account':label,'status':'Unavailable','provider_remaining_seconds':None,'unreserved_seconds':None}
                if path:
                    try:
                        report=read_json(path)
                        record['observed_at']=report.get('usage_checked_utc',report.get('checked_utc'))
                        if report.get('account_label')!=label:raise ValueError('Account mismatch')
                        fields=quota.report_quota_fields(report)
                        record['provider_remaining_seconds']=fields['remaining_seconds']
                        if reservation['ambiguous']:
                            record['status']='Unreconciled reservations; availability unknown'
                        else:
                            available=quota.available_quota_seconds(provider_remaining_seconds=fields['remaining_seconds'],outstanding_reservations_seconds=reservation['reserved_seconds'])
                            record.update(status='Fresh saved provider evidence',unreserved_seconds=available,
                                original_threshold=config.quota_eligibility(available,provider_remaining_seconds=fields['remaining_seconds'])['eligible'],
                                recovery_threshold=config.quota_eligibility(available,provider_remaining_seconds=fields['remaining_seconds'],workload_type='targeted_replacement')['eligible'])
                    except (ValueError,KeyError,TypeError,OSError):record['status']='Saved provider evidence missing, invalid or stale'
                rows.append(record)
                sentences.append(f"{label}: {record['status']}"+(f"; provider remaining {record['provider_remaining_seconds']:.6g} seconds; unreserved {record['unreserved_seconds']:.6g} seconds." if record['unreserved_seconds'] is not None else '.'))
            sentences.append(f"Canonical quota floors: {config.MIN_BATCH_QUOTA_SECONDS:g} seconds for original Batches and {config.MIN_RECOVERY_QUOTA_SECONDS:g} seconds for targeted recovery. This read does not authorize submission or switch accounts. Fresh IBM refresh is not enabled.")
            result['quota_evidence']=rows
            result['reservation_evidence']=reservations
    elif operation=='get_ibm_job_status':
        jobs=[j for j in snapshot.get('execution_jobs',[]) if request.n is None or j['target_n']==request.n]
        if request.pending_only:jobs=[j for j in jobs if j.get('status') in ('SUBMITTED','QUEUED','RUNNING','INITIALIZING') or (j.get('status') is None and any(v in ('SUBMITTED','SUBMITTING','UNKNOWN') for v in j.get('ledger_states',[])))]
        result['job_evidence']=jobs
        from worker_supervisor import observation_freshness
        rows=[{'n':j.get('target_n'),'treatment':j.get('treatment'),'recorded_status':j.get('status') or 'UNKNOWN',
               'account':j.get('account'),'backend':j.get('backend'),
               'provider_observed_at':j.get('observed_at') or 'N/A',
               'freshness':observation_freshness(j.get('observed_at'))} for j in jobs]
        counts=Counter(j.get('status') or 'UNKNOWN' for j in jobs)
        sentences=[f"Last locally recorded jobs: {', '.join(f'{key}: {count}' for key,count in sorted(counts.items())) or 'No matching records'}. These are cached observations, not live IBM responses."]
    else:
        selected=select_question(request.question or f'Analyze n={request.n or 5}',history,default_n=request.n)
        if operation=='export_derived_figures':selected=selected.model_copy(update={'tool':'generate_derived_chart'})
        result.update(execute(reader,selected))
        sentences=result['sentences']+[result['warning']]
        rows=result['rows'];chart=result['chart']
        if chart:sentences.append('Derived chart prepared in the isolated research workspace. Its chart toolbar can download a PNG; original figures are unchanged.')
    result['sentences']=sentences
    audit_ref=save_evidence(result)
    if operation=='check_ibm_quota':
        from worker_supervisor import observation_freshness
        for row in rows:row['freshness']=observation_freshness(row.get('observed_at'))
    return {'research_scope':result.get('request'),'audit_ref':audit_ref,'answer':'\n'.join(sentences),'rows':rows,'chart':chart,'limitations':'Persisted evidence only; no IBM actions.','model':None,'engine':'Python tool bridge'}
