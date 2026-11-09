"""Presentation projection of canonical ledgers on the dashboard's private snapshot.

No provider calls, writes, scientific calculations or acceptance decisions.
"""
import json
from collections import defaultdict

TREATMENTS=('M0_Baseline','M1_DD','M2_TREX','M3_ZNE','M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL')
LABELS=('None','DD','TREX','ZNE','DD+TREX','DD+ZNE','TREX+ZNE','ALL')
def decode(value,default=None):
    try:return json.loads(value) if isinstance(value,str) else (value or default)
    except (ValueError,TypeError):return default
def unique_value(values):
    values={v for v in values if v is not None}
    return next(iter(values)) if len(values)==1 else None
def project_jobs(db,campaign,accepted):
    def rows(sql,args=()):return [dict(r) for r in db.execute(sql,args)]
    levels={r['target_id']:r['target_n'] for r in rows('SELECT target_id,target_n FROM independent_n_campaign_levels WHERE campaign_id=?',(campaign,))}
    block_sequences={r['logical_block_id']:r['sequence_no'] for r in rows('SELECT logical_block_id,sequence_no FROM experiment_blocks')} if rows("SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_blocks'") else {}
    originals=rows('SELECT j.* FROM jobs j JOIN independent_n_campaign_levels l ON l.target_id=j.target_id WHERE l.campaign_id=?',(campaign,))
    replacements=rows('SELECT j.* FROM pub_replacement_jobs j JOIN independent_n_campaign_levels l ON l.target_id=j.target_id WHERE l.campaign_id=?',(campaign,))
    observations=defaultdict(list)
    for m in rows('SELECT checked_at,snapshot_json FROM batch_monitor_state'):
        for j in decode(m['snapshot_json'],{}).get('jobs',[]):
            if j.get('job_id'):observations[j['job_id']].append(dict(j,observed_utc=j.get('observed_utc') or m['checked_at']))
    validation=defaultdict(list)
    for v in rows('SELECT job_row_id,pub_index,expectation_valid,uncertainty_valid,uncertainty_state,error_codes_json,source_sha256,created_at FROM pub_validation_records'):
        validation[v.pop('job_row_id')].append(v)
    events=defaultdict(list)
    for table,idcol,kind in [('recovery_events','job_row_id','original'),('batch_monitor_events','job_row_id','original'),('pub_replacement_events','replacement_id','replacement')]:
        for e in rows(f'SELECT {idcol},event_type,created_at FROM {table} ORDER BY created_at'):
            events[kind,e.pop(idcol)].append(e)
    groups=defaultdict(list)
    for kind,source in [('original',originals),('replacement',replacements)]:
        for r in source:
            # Unsubmitted plans are not executions. Ambiguous durable intents remain visible.
            state=r.get('submission_state',r.get('state'))
            if not r.get('job_id') and state not in ('UNKNOWN','SUBMITTING'):continue
            identity=r.get('job_id') or f"unconfirmed-{kind}-{r.get('id',r.get('replacement_id'))}"
            groups[(r['target_id'],r['account_label'],r['backend'],identity)].append((kind,r))
    result=[]
    for key,members in groups.items():
        target,account,backend,identity=key; job_id=members[0][1].get('job_id')
        obs=list(observations.get(job_id,[])); details=[]; history=[]; counts=[]; provenance=[]; expected=[]; reservations=[]; usage=[]; recorded_evidence=[]
        for kind,r in members:
            meta=decode(r['metadata_json'],{}); rid=r.get('id',r.get('replacement_id'))
            remote=meta.get('last_remote_monitor')
            if isinstance(remote,dict):obs.append(remote)
            if meta.get('provider_status') or meta.get('provider_job_status'):
                metrics=meta.get('provider_metrics') or {}
                obs.append({'status':meta.get('provider_status') or meta.get('provider_job_status'),'timestamps':metrics.get('timestamps'), 'observed_utc':None})
            recorded_evidence.append({'source':f'{kind}:{rid}','job_id':r.get('job_id'),
                'batch_id':r.get('batch_id'),'account_label':r.get('account_label'),'backend':r.get('backend'),
                'logical_block_id':r.get('logical_block_id'),'target_id':target,'attempt':r.get('attempt'),
                'submitted_client_utc':meta.get('submitted_client_utc') or meta.get('submitted_utc'),
                'provider_metrics':meta.get('provider_metrics'), 'source_timestamps':meta.get('source_timestamps'),
                'raw_archive_path':r.get('raw_archive_path') or meta.get('raw_archive_path') or meta.get('per_pub_source_path'),
                'raw_archive_sha256':r.get('raw_archive_sha256') or meta.get('raw_archive_sha256') or meta.get('per_pub_source_sha256'),
                'collected_metadata_path':meta.get('collected_metadata_path'),
                'historical_source_campaign':meta.get('source_campaign_id') if meta.get('historical_adoption') else None,
                'source_job_id':meta.get('source_job_id')})
            history.extend(events[kind,rid]);reservations.append(r.get('quota_reserved_seconds'));usage.append(r.get('qpu_usage_seconds'))
            provenance.append({'kind':kind,'ledger_id':rid,'attempt':r.get('attempt'),'logical_block_id':r['logical_block_id'],'logical_pub_index':r.get('logical_pub_index'),'source_job_id':meta.get('source_job_id'),'source_account':r.get('source_account_label') or meta.get('source_account_label'),'archive_sha256':r.get('raw_archive_sha256') or meta.get('raw_archive_sha256'),'input_hashes':decode(r.get('input_hashes_json'),{}),'ledger_state':r.get('submission_state',r.get('state'))})
            if kind=='original':
                records=validation[rid];details.extend(records)
                # Persisted validation facts, not inferred from remote completion.
                if records:
                    valid=sum(v['expectation_valid']==1 and v['uncertainty_valid']==1 for v in records)
                    invalid=sum(v['expectation_valid']==0 or v['uncertainty_valid']==0 for v in records)
                    counts.append((valid,invalid))
                elif r.get('validated_at'):counts.append((r.get('validated_pub_count'),r.get('invalid_pub_count')))
                else:counts.append((None,None))
                expected.append(r.get('submitted_pub_count') or r.get('chunk_n'))
            else:
                errors=decode(r.get('error_codes_json'),[])
                valid=1 if r['state']=='VALIDATED' else None
                invalid=1 if errors and r.get('expectation_values_json') else (0 if valid else None)
                counts.append((valid,invalid));expected.append(1)
                details.append({'pub_index':r['source_pub_index'],'logical_pub_index':r['logical_pub_index'],'uncertainty_state':r['uncertainty_state'],'error_codes_json':r.get('error_codes_json'),'source_sha256':r.get('raw_archive_sha256'),'created_at':r['updated_at']})
        obs.sort(key=lambda o:o.get('observed_utc') or '')
        observed=obs[-1] if obs else {}; times={}
        for o in obs:
            if isinstance(o.get('timestamps'),dict):times.update({k:v for k,v in o['timestamps'].items() if v})
        treatment_ids=sorted({r['treatment_id'] for _,r in members},key=lambda t:TREATMENTS.index(t) if t in TREATMENTS else 99)
        kinds={k for k,_ in members}
        # Grouped replacements share one provider execution; usage/reservation are not summed.
        replacement_only=kinds=={'replacement'}
        def total(index):
            vals=[c[index] for c in counts]
            if any(v is None for v in vals):return None
            return sum(vals) if replacement_only else unique_value(vals)
        accepted_count=len({(v['treatment_id'],v['logical_pub_index']) for v in accepted.get(target,[]) if v['job_id']==job_id and v.get('account_label')==account})
        expected_count=sum(expected) if replacement_only else unique_value(expected)
        valid=total(0); invalid=total(1)
        validation_state='Not fully validated' if valid is None or invalid is None else ('Complete validated' if valid==expected_count and invalid==0 else 'Partial / invalid')
        submitted=times.get('created') or next((decode(r['metadata_json'],{}).get('submitted_utc') for _,r in members if decode(r['metadata_json'],{}).get('submitted_utc')),None)
        event_keys={(e['event_type'],e['created_at']) for e in history}
        result.append({'key':'|'.join(str(v) for v in key),'target_n':levels[target],'treatment_ids':treatment_ids,'treatment':', '.join(LABELS[TREATMENTS.index(t)] if t in TREATMENTS else t for t in treatment_ids),'treatment_order':min((TREATMENTS.index(t) for t in treatment_ids if t in TREATMENTS),default=99),'job_id':job_id,'account':account,'backend':backend,'batch_id':unique_value([r['batch_id'] for _,r in members]),'block':min(r['logical_block_id'] for _,r in members),'batch_sequence':unique_value([block_sequences.get(r['logical_block_id']) for _,r in members]),'status':observed.get('status'),'observed_at':observed.get('observed_utc'),'submitted':submitted,'queued':times.get('queued'),'started':times.get('running'),'finished':times.get('finished'),'runtime_seconds':unique_value(usage),'reserved_seconds':unique_value(reservations),'expected_pubs':expected_count,'valid_pubs':valid,'invalid_pubs':invalid,'accepted_pubs':accepted_count,'validation_state':validation_state,'collected_pubs':(sum(bool(r.get('raw_archive_sha256')) for _,r in members) if replacement_only and all(r.get('raw_archive_sha256') for _,r in members) else unique_value([r.get('collected_pub_count') for _,r in members]) if not replacement_only else None),'recovery':'Replacement' if replacement_only else ('Treatment replacement' if any(decode(r['metadata_json'],{}).get('combined_treatment_recovery') for _,r in members) else 'Original'),'updated_at':max(r['updated_at'] for _,r in members),'ledger_states':sorted({r.get('submission_state',r.get('state')) for _,r in members}),'validation':details,'provenance':provenance,'recorded_evidence':recorded_evidence,'events':[{'event':e,'timestamp':t} for e,t in sorted(event_keys,key=lambda k:k[1])]})
    return sorted(result,key=lambda r:(r['target_n'],r['treatment_order'],r['block'],r['submitted'] or '',r['key']))
