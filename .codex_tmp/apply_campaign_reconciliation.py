import sys,json,hashlib,sqlite3,shutil
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
root=Path.cwd();sys.path.insert(0,str(root/'02_Hardware/04_code/03_ai_assistant'))
from job_registry import JobRegistry
from worker_runtime import WorkerLock
from independent_n_campaign import campaign_root,_target_from_level
from batch_progression import evaluate_batch_progression,replacement_provenance_verified
from targeted_recovery import _verified_raw_source
from vqe_adapter import verify_frozen_inputs,record_directory
cid='vqe-independent-n-01';database=JobRegistry.default_database(root)
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def encode(value):return json.dumps(value,sort_keys=True,default=str)
with WorkerLock(campaign_root(root,cid)/'worker.lock'):
    reg=JobRegistry(database,readonly=True);status=reg.independent_campaign_status(cid)
    assert status['state']=='PAUSED' and status['current_level_index']==4
    target=_target_from_level(root,status['levels'][4],registry=reg)
    qc,obs,theta,physical,_,_=verify_frozen_inputs(root)
    assert len(theta)==21 and len(obs)==7 and tuple(physical)==tuple(range(140,147)) and len(qc.parameters)==0
    directory=Path(sys.argv[1]) if len(sys.argv)>1 else root/'manager_audit_backups'/('reconcile_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    if not directory.exists():
        directory.mkdir(parents=True)
        with reg._connection() as source:
            with sqlite3.connect(directory/'job_registry.sqlite3') as destination:source.backup(destination)
        shutil.copytree(campaign_root(root,cid),directory/'campaign',ignore=shutil.ignore_patterns('worker.lock'))
    before_rows=reg.rows();before_replacements=reg.replacement_jobs(target.target_id)
    selected=reg.repaired_pub_values(target.target_id)
    all_coverage={l['target_id']:reg.repaired_pub_values(l['target_id']) for l in status['levels']}
    assert len(selected)==120
    assert len({(r['treatment_id'],r['logical_pub_index']) for r in selected})==120
    identities=[r['job_id'] for r in before_rows if r['job_id']]
    all_replacements=[r for l in status['levels'] for r in reg.replacement_jobs(l['target_id'])]
    identities += [r['job_id'] for r in all_replacements if r['job_id']]
    assert all(n==1 for n in Counter(identities).values())
    assert not [r for r in before_rows if r['submission_state'] in ('UNKNOWN','SUBMITTING')]
    assert not [r for r in all_replacements if r['state'] in ('UNKNOWN','SUBMITTING','RESERVED')]
    archives={};sources={};blocks=[]
    for sequence in (1,2,3):
        block=f'{cid}-n25-block-{sequence:03d}'
        decision=evaluate_batch_progression(reg,target,block);assert decision['eligible']
        blocks.append({'block':block,**decision})
        for row in reg.rows(block):
            if not row['job_id']:
                assert sequence==3 and row['treatment_id']=='M7_ALL' and row['submission_state']=='CONFIRMED_NOT_SUBMITTED'
                continue
            path,sha,payload,kind,count=_verified_raw_source(root,row,reg,expected_pubs=5,expected_shots=4096,expected_hashes=target.input_hashes)
            archives[str(path)]=sha;sources[row['job_id']]=(path,payload)
            sidecar=path.with_name(path.stem+'.structured.json')
            if sidecar.exists():archives[str(sidecar)]=digest(sidecar)
            for original in path.parent.parent.rglob('*.json'):archives[str(original)]=digest(original)
    for r in selected:
        if r['source']=='REPLACEMENT':
            replacement=next(item for item in before_replacements if item['replacement_id']==r['replacement_id'])
            assert replacement_provenance_verified({**replacement,'metadata_json':encode(replacement['metadata']),
                'options_json':encode(replacement['options'])},r,target.input_hashes)
            continue
        path,payload=sources[r['job_id']]
        record=payload['records'][(r['logical_pub_index']-1)%5]
        assert record['expectation_values']==r['expectation_values'] and record['standard_deviations']==r['standard_deviations']
    for r in before_replacements:
        assert r['state']=='VALIDATED' and digest(r['raw_archive_path'])==r['raw_archive_sha256']
        archives[str(Path(r['raw_archive_path']))]=r['raw_archive_sha256']
    m7=[r for r in selected if r['job_id']=='dbnc6e8ou4as73b493sg']
    m3=[r for r in selected if r['treatment_id']=='M3_ZNE' and r['logical_pub_index']==15]
    assert len(m7)==5 and {r['logical_pub_index'] for r in m7}==set(range(11,16))
    assert len(m3)==1 and m3[0]['source']=='REPLACEMENT' and m3[0]['job_id']=='dbnceo0qtr0c73acpjq0'
    future=[]
    for sequence in (4,5):
        block=f'{cid}-n25-block-{sequence:03d}'
        assert not reg.rows(block) and not any(r['logical_block_id']==block for r in before_replacements)
        folder=record_directory(root,block)
        records=list(folder.rglob('job_submitted.json'))+list(folder.rglob('submission_state.json')) if folder.exists() else []
        assert not records
        future.append({'block':block,'registry_jobs':0,'submission_record_files':0,'record_directory_exists':folder.exists()})
    original3=[]
    for row in reg.rows(f'{cid}-n25-block-003'):
        if row['attempt']!=1 or not row['job_id']:continue
        assert row['submission_state']=='COMPLETED'
        path,payload=sources[row['job_id']];metadata=path.parent.parent/'job_retrieved_metadata.json'
        item={'job_id':row['job_id'],'treatment':row['treatment_id'],'registry_state':'COMPLETED','raw_archive_sha256':digest(path)}
        if metadata.exists():
            data=json.loads(metadata.read_text());assert data['job_id']==row['job_id'] and data['status']=='DONE'
            assert data['batch_id']==row['batch_id'] and data['raw_result_sha256']==digest(path)
            item.update(archived_provider_status='DONE',metadata_sha256=digest(metadata))
        else:item.update(archived_provider_status='No separate retrieval metadata; five-PUB archive and prior terminal registry state verified')
        original3.append(item)
    assert len(original3)==7
    evidence={'blocks':blocks,'original_batch_003_jobs':original3,'future_batches':future,
        'frozen_inputs_verified':True,'manifest_sha256':target.sha256,'input_hashes':target.input_hashes,
        'm7_selected_pub_count':len(m7),'m3_pub15_selected_count':len(m3),'m3_pub15_job_id':m3[0]['job_id'],
        'unknown_or_submitting':0,'duplicate_provider_ids':0,'archive_hashes':archives}
    (directory/'verified_evidence.json').write_text(json.dumps(evidence,indent=2,default=str))
    (directory/'accepted_coverage_before.json').write_text(encode(all_coverage))
    reg=JobRegistry(database)
    block1=f'{cid}-n25-block-001';review=reg.latest_batch_recovery_review(block1)
    if not review or review['recovery_status']!='RECOVERED' or not review['raw_archives_verified'] or not review['provenance_verified']:
        reg.record_batch_recovery_review(block1,batch_execution_status='INCOMPLETE',recovery_status='RECOVERED',
            completeness_policy_version='complete-only-repaired-v1',valid_fraction_by_treatment={name:1.0 for name in target.treatment_ids},
            raw_archives_verified=True,provenance_verified=True,
            details={'reason':'Reconciled stale review against immutable original archives and validated replacement provenance',
                     'evidence_path':str(directory/'verified_evidence.json'),'evidence_sha256':digest(directory/'verified_evidence.json'),
                     'scientific_results_changed':False,'submission_performed':False})
    reg.record_independent_target_event(target.target_id,'ORIGINAL_BATCH_003_ARCHIVE_STATUS_RECONCILED',
        {'original_jobs':original3,'original_m7_state':'CONFIRMED_NOT_SUBMITTED','job_states_changed':False,
         'provider_freshly_checked':False,'scientific_results_changed':False,'submission_performed':False})
    assert reg.rows()==before_rows and reg.replacement_jobs(target.target_id)==before_replacements
    assert all_coverage=={l['target_id']:reg.repaired_pub_values(l['target_id']) for l in status['levels']}
    assert all(digest(p)==sha for p,sha in archives.items())
    after=reg.independent_campaign_status(cid)
    assert after['state']=='PAUSED' and after['current_level_index']==4
    # Derived per-level target_status intentionally reflects the appended review.
    # Persisted campaign/level/checkpoint rows are compared against the backup below.
    changed=[]
    with sqlite3.connect(directory/'job_registry.sqlite3') as saved,reg._connection() as current:
        for (table,) in saved.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if saved.execute('SELECT * FROM "'+table+'"').fetchall()!=[tuple(r) for r in current.execute('SELECT * FROM "'+table+'"')]:changed.append(table)
    assert set(changed)<= {'batch_recovery_reviews','recovery_events','independent_n_campaign_events'}
    result={'backup':str(directory),'campaign_state':'PAUSED','n25_validated':120,'n25_required':200,
            'batch001_recovery':reg.latest_batch_recovery_review(block1)['recovery_status'],
            'batch003_original_jobs_completed':7,'m7_selected':5,'m3_pub15_selected':1,
            'frozen_inputs_verified':True,'unknown_submitting_or_duplicate':0,'future_batches':future,
            'scientific_rows_job_ids_and_archives_unchanged':True,'changed_tables':changed,
            'local_readiness':'READY_FOR_BATCH_004_SUBJECT_TO_FRESH_PROVIDER_GATES','provider_freshly_verified':False,
            'n30_advanced':False,'submission_performed':False}
    (directory/'readiness.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
