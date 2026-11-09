"""Unified historical view; exact identities, bounded reads, no provider calls/writes."""
from pathlib import Path, PureWindowsPath
import json, math, hashlib, re
from datetime import datetime, timezone
from collections import defaultdict
from reader import OUT, CAMPAIGN

# Last projection's diagnostic lineage stays server-side, not in ordinary UI.
AUDIT={}
def load(path,root,content_only=False):
    path=Path(path).resolve()
    if not path.is_relative_to(root.resolve()) or path.stat().st_size>8*1024*1024:raise ValueError('Outside bounded source')
    payload=path.read_bytes()
    value=json.loads(payload.decode('utf-8-sig'))
    if not isinstance(value,dict):
        if content_only:return {}
        raise ValueError('Unsupported record')
    value['_record_file_sha256']=hashlib.sha256(payload).hexdigest()
    return value

def utc(value):
    try:
        d=datetime.fromisoformat(value.replace('Z','+00:00'))
        return d.astimezone(timezone.utc).isoformat().replace('+00:00','Z') if d.utcoffset() is not None else None
    except (ValueError,TypeError,AttributeError):return None

def errors(record):
    try:value=json.loads(record.get('error_codes_json') or '[]')
    except (ValueError,TypeError):return []
    return value if isinstance(value,list) else []

def number(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0 else None

def historical(snapshot,root=OUT):
    global AUDIT
    execution=root/'03_IBM_execution_records';runtime=root/'04_runtime_logs'
    audit={'jobs':{},'unlinked':[],'unreadable':[]}; indexed=defaultdict(list)
    # Explicit schema filenames only. Never glob raw result payloads, backups or credentials.
    paths=set(execution.glob('replicate_block_*/*/job_submitted.json'))|set(execution.glob('replicate_block_*/*/job_retrieved_metadata.json'))
    runtime_files=[p for p in runtime.rglob('*') if p.is_file()] if runtime.is_dir() else []
    paths.update(p for p in runtime_files if p.name in ('job_submitted.json','job_retrieved_metadata.json'))
    for path in sorted(paths):
        try:
            d=load(path,root);indexed[d.get('job_id')].append((d,str(path)))
        except (OSError,ValueError):audit['unreadable'].append(str(path))
    groups=defaultdict(list);unconfirmed=0
    for item in snapshot.get('execution_jobs',[]):
        if item.get('job_id'):groups[item['job_id']].append(item)
        else:unconfirmed+=1
    rows=[]
    for jid,members in groups.items():
        r=dict(members[0]);r['key']=jid;r['campaign_id']=CAMPAIGN
        for field in ('provenance','events','validation'):
            r[field]=list({json.dumps(v,sort_keys=True):v for item in members for v in item.get(field,[])}.values())
        conflicts=[];lineage=[];file_hashes={};candidates=defaultdict(list);saved=[];linked=[]
        def add(field,value,source):
            if value is not None and value!='':candidates[field].append((value,source))
        def choose(field):
            values={json.dumps(float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) else v,sort_keys=True) for v,_ in candidates[field]}
            if len(values)>1:
                conflicts.append({'field':field,'evidence':candidates[field]});return None
            return candidates[field][0][0] if values else None
        for item in members:
            for field in ('target_n','batch_id','account','backend','block','treatment','expected_pubs','accepted_pubs','valid_pubs','invalid_pubs','recovery','validation_state','collected_pubs'):
                add(field,item.get(field),'registry projection')
            for field in ('started','finished'):add(field,utc(item.get(field)),'saved provider observation')
            add('provider_created',utc(item.get('submitted')),'saved provider creation observation')
            add('runtime_seconds',number(item.get('runtime_seconds')),'registry qpu_usage_seconds')
        # Identity conflicts cannot be hidden by first-row selection.
        for field in ('target_n','batch_id','account','backend','block','treatment','expected_pubs','accepted_pubs','valid_pubs','invalid_pubs','recovery','validation_state','collected_pubs'):
            r[field]=choose(field)
        identities_ok=all(r.get(k) is not None for k in ('target_n','account','backend','block','batch_id'))
        evidence=[e for m in members for e in m.get('recorded_evidence',[])]
        def match(d, historical_campaign=None):
            if not identities_ok:return False
            actual=d.get('job_id') or d.get('replacement_job_id')
            if actual!=jid:return False
            for source,dest in (('account_label','account'),('backend','backend'),('batch_id','batch_id'),('runtime_session_id','batch_id'),('logical_block_id','block')):
                if d.get(source) is not None and d[source]!=r[dest]:return False
            if d.get('campaign_id') is not None and d['campaign_id'] not in (CAMPAIGN,historical_campaign):return False
            if d.get('target_n') is not None and d['target_n']!=r['target_n']:return False
            # Legacy n is the chunk/PUB count, NOT the experiment n-level.
            targets={e.get('target_id') for e in evidence if e.get('target_id')}
            if d.get('target_id') and targets and d['target_id'] not in targets:return False
            if d.get('treatment_id') and r.get('treatment_ids') and d['treatment_id'] not in r['treatment_ids']:return False
            if d.get('attempt') is not None and d['attempt'] not in {e.get('attempt') for e in evidence}:return False
            return True
        for d,source in indexed.get(jid,[]):
            # Submission/retrieval files carry full identity; absent fields do not authorize a join.
            if all(d.get(k) is not None for k in ('account_label','backend','batch_id','logical_block_id')) and match(d):linked.append((d,source))
            else:conflicts.append({'field':'identity','source':source})
        for e in evidence:
            add('submitted',utc(e.get('submitted_client_utc')),e['source'])
            for field in ('raw_archive_path','collected_metadata_path'):
                path=e.get(field)
                if not path:continue
                original_path=path
                recorded=Path(path)
                if not recorded.is_absolute():
                    parts=PureWindowsPath(path).parts
                    if '03_IBM_execution_records' in parts:
                        recorded=execution.joinpath(*parts[parts.index('03_IBM_execution_records')+1:])
                path=str(recorded.resolve())
                try:
                    d=load(recorded,root,content_only=field=='raw_archive_path')
                    expected=e.get('raw_archive_sha256') if field=='raw_archive_path' else None
                    if expected:
                        h=hashlib.sha256()
                        with Path(path).open('rb') as stream:
                            for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
                        if h.hexdigest()!=expected:raise ValueError('Archive hash mismatch')
                    if not (d.get('job_id') or d.get('replacement_job_id')):
                        # Content-only raw results are bound by ledger hash, not a second identity schema.
                        if expected:lineage.append(path)
                        continue
                    if not match(d,e.get('historical_source_campaign')):raise ValueError('Archive identity mismatch')
                    linked.append((d,path))
                except (OSError,ValueError):conflicts.append({'field':'archive_identity_or_hash','source':path})
        seen=set()
        for d,source in linked:
            if source in seen:continue
            seen.add(source);lineage.append(source);file_hashes[source]=d.get('_record_file_sha256')
            metrics=d.get('metrics_raw') or d.get('provider_metrics') or {}
            metrics=metrics if isinstance(metrics,dict) else {}
            times=metrics.get('timestamps') or d.get('timestamps') or {};times=times if isinstance(times,dict) else {}
            usage=metrics.get('usage') or {};usage=usage if isinstance(usage,dict) else {}
            qpu=number(usage.get('qpu_charge_time_seconds'))
            if qpu is None:qpu=number(d.get('qpu_usage_seconds'))
            if qpu is None:qpu=number(usage.get('quantum_seconds',usage.get('qpu_seconds')))
            if qpu is None:qpu=number(d.get('qpu_usage_s'))
            # Keep different recorded seconds definitions; disagreements are explicit.
            add('runtime_seconds',qpu,source)
            submitted=d.get('submitted_client_utc') or d.get('submitted_utc')
            add('submitted',utc(submitted),source)
            add('provider_created',utc(times.get('created') or d.get('job_creation_date_utc') or d.get('job_creation_date')),source)
            started=utc(times.get('running'));finished=utc(times.get('finished'))
            add('started',started,source);add('finished',finished,source)
            elapsed=None
            if started and finished:
                elapsed=number((datetime.fromisoformat(finished.replace('Z','+00:00'))-datetime.fromisoformat(started.replace('Z','+00:00'))).total_seconds())
            saved.append(dict(submitted=submitted,collected=d.get('collected_utc'),status=d.get('status') or d.get('job_status'),started=times.get('running'),finished=times.get('finished'),elapsed_seconds=elapsed,qpu_usage_seconds=qpu))
            add('parent_job_id',d.get('original_source_job_id'),source)
        r['submitted']=choose('submitted');r['submission_time_basis']='Recorded client submission time' if r['submitted'] else None
        if not candidates['submitted']:
            r['submitted']=choose('provider_created');r['submission_time_basis']='Recorded provider creation time' if r['submitted'] else None
        for field in ('started','finished','runtime_seconds'):r[field]=choose(field)
        r['elapsed_seconds']=None
        if r['started'] and r['finished']:r['elapsed_seconds']=number((datetime.fromisoformat(r['finished'].replace('Z','+00:00'))-datetime.fromisoformat(r['started'].replace('Z','+00:00'))).total_seconds())
        # A newer dated observation may supersede history; undated disagreements do not.
        statuses=[(m.get('status'),utc(m.get('observed_at')),'registry observation') for m in members if m.get('status')]
        statuses += [(x['status'],utc(x['collected']),'saved retrieval') for x in saved if x['status']]
        normalize=lambda v: {'COMPLETED':'DONE','ERROR':'FAILED','CANCELED':'CANCELLED'}.get(str(v).upper(),str(v).upper())
        r['status']=None
        if statuses:
            if len({normalize(x[0]) for x in statuses})==1:r['status']=statuses[0][0]
            elif all(x[1] for x in statuses):
                latest=max(x[1] for x in statuses);last=[x for x in statuses if x[1]==latest]
                if len({normalize(x[0]) for x in last})==1:r['status']=last[0][0]
                else:conflicts.append({'field':'status','evidence':statuses})
            else:conflicts.append({'field':'status','evidence':statuses})
        add('status_history',statuses,'recorded observations')
        for e in evidence:add('parent_job_id',e.get('source_job_id'),e['source'])
        r['parent_job_id']=choose('parent_job_id')
        attempts={p.get('attempt') for m in members for p in m.get('provenance',[]) if p.get('attempt') is not None}
        r['recovery_attempt']=next(iter(attempts)) if len(attempts)==1 else None
        r['saved_runtime']=saved;r['record_conflict']=bool(conflicts)
        r['runtime_evidence_conflict']=any(c['field']=='runtime_seconds' for c in conflicts)
        sequences={m.get('batch_sequence') for m in members if m.get('batch_sequence') is not None}
        match=re.search(r'-block-(\d+)$',r.get('block') or '')
        r['batch_sequence']=next(iter(sequences)) if len(sequences)==1 else int(match[1]) if not sequences and match else None
        r['batch_sequence_source']=('Registered experiment_blocks.sequence_no' if len(sequences)==1 else
            'Registered logical block identifier suffix' if not sequences and match else 'Unnumbered / conflicting registration')
        r['batch_label']=f"Batch {r['batch_sequence']:03d}" if isinstance(r['batch_sequence'],int) and r['batch_sequence']>0 else 'Unnumbered execution'
        # Missing results require explicit validation facts; never subtract queued PUBs.
        validation=r.get('validation') or []
        missing=sum('MISSING_PUB_RESULT' in errors(v) for v in validation)
        r['missing_pubs']=missing if validation else None
        if validation and all('expectation_valid' in v for v in validation):
            r['invalid_pubs']=sum(not(v.get('expectation_valid')==1 and v.get('uncertainty_valid')==1) and 'MISSING_PUB_RESULT' not in errors(v) for v in validation)
        elif r['invalid_pubs'] is not None and r['missing_pubs'] is not None:r['invalid_pubs']=max(0,r['invalid_pubs']-missing)
        r['retrieval_status']=('Archived' if r.get('collected_pubs') and r.get('collected_pubs')==r.get('expected_pubs') else 'Partially archived' if r.get('collected_pubs') else 'Not recorded')
        r['validation_status']=r.get('validation_state')
        r.pop('recorded_evidence',None)
        audit['jobs'][jid]={'lineage':lineage,'source_hashes':file_hashes,'registry_evidence':evidence,'field_evidence':dict(candidates),'conflicts':conflicts}
        rows.append(r)
    audit['unlinked']=[source for jid,docs in indexed.items() if jid not in groups for _,source in docs]
    AUDIT=audit
    return dict(rows=rows,captured_at=snapshot.get('captured_at'),offline=snapshot.get('offline',False),runtime_folder_present=runtime.is_dir(),runtime_folder_entries=len(runtime_files),unreadable_metadata=len(audit['unreadable']),unlinked_files=len(audit['unlinked']),unconfirmed_intents=unconfirmed,conflicting_jobs=sum(r['record_conflict'] for r in rows),notice='Recorded history only. No IBM queries. Missing/conflicting values are N/A; unknown schemas are not inferred.')
