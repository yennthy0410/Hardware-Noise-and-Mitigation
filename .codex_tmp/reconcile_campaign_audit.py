import sys,json,hashlib,sqlite3,shutil
from pathlib import Path
from datetime import datetime,timezone
root=Path.cwd();sys.path.insert(0,str(root/'02_Hardware/04_code/03_ai_assistant'))
from job_registry import JobRegistry
from worker_runtime import WorkerLock
from independent_n_campaign import campaign_root,_target_from_level
from batch_progression import evaluate_batch_progression
from targeted_recovery import _verified_raw_source
cid='vqe-independent-n-01'
registry=JobRegistry(JobRegistry.default_database(root),readonly=True)
with WorkerLock(campaign_root(root,cid)/'worker.lock'):
    status=registry.independent_campaign_status(cid)
    target=_target_from_level(root,status['levels'][4],registry=registry)
    report={'state':status['state'],'level_index':status['current_level_index'],'target':target.target_id,'blocks':[]}
    for block in registry.target_status(target.target_id)['blocks']:
        bid=block['logical_block_id'];rows=registry.rows(bid)
        entry={'block':bid,'execution':block.get('status'),'review':registry.latest_batch_recovery_review(bid),
               'decision':evaluate_batch_progression(registry,target,bid),'jobs':[]}
        for row in rows:
            item={k:row.get(k) for k in ('id','attempt','treatment_id','job_id','batch_id','submission_state','validated_pub_count')}
            item['metadata_keys']=sorted(row['metadata'])
            try:
                path,digest,payload,kind,count=_verified_raw_source(root,row,registry,expected_pubs=5,expected_shots=4096,expected_hashes=target.input_hashes)
                item['archive']={'path':str(path),'sha256':digest,'count':count,'keys':list(payload)}
            except Exception as exc:item['archive_error']=str(exc)
            entry['jobs'].append(item)
        report['blocks'].append(entry)
    report['replacements']=[{k:r.get(k) for k in ('replacement_id','logical_block_id','treatment_id','logical_pub_index','job_id','state','raw_archive_path','raw_archive_sha256')} for r in registry.replacement_jobs(target.target_id)]
    out=root/'.codex_tmp/campaign_reconcile_inspection.json';out.write_text(json.dumps(report,indent=2,default=str))
    print(json.dumps(report,indent=2,default=str))
