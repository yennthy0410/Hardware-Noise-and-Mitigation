"""Read-only identity audit. No submission, reconciliation writes, or result retrieval."""
import os
for name in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'
import sys, json, logging, hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'02_Hardware/04_code/03_ai_assistant'))
logging.disable(logging.CRITICAL)
from job_registry import JobRegistry
from worker_runtime import WorkerLock
from independent_n_campaign import campaign_root
from account_config import load_saved_account_profiles,make_saved_profile_service
from batch_scheduler import verify_reconciliation_job,_check_reconciliation_records
from repository_paths import frozen_source,source_hashes
from vqe_adapter import verify_frozen_inputs
cid='vqe-independent-n-01';block=cid+'-n25-block-003'
batch='d9e6cd69-775b-4597-9267-7e27a864879d'
r=JobRegistry(root/'02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/03_IBM_execution_records/ai_assistant/job_registry.sqlite3',readonly=True)
def digest():
    with r._connection() as db:return hashlib.sha256('\n'.join(db.iterdump()).encode()).hexdigest()
before=digest()
try:
    with WorkerLock(campaign_root(root,cid)/'worker.lock'):
        print('CAMPAIGN_LOCK_ACQUIRED',flush=True)
        rows=r.rows(block)
        assert len(rows)==8 and all(x['account_label']=='labK' and x['batch_id']==batch and x['backend']=='ibm_fez' for x in rows)
        _check_reconciliation_records(r,block,root)
        hashes=source_hashes(frozen_source(root))
        assert all(x['input_hashes']==hashes for x in rows)
        _,obs,theta,physical,_,_=verify_frozen_inputs(root)
        print('FROZEN_VERIFIED',len(theta),len(obs),list(physical),flush=True)
        for row in rows:
            print('LOCAL',json.dumps({k:row.get(k) for k in ('treatment_id','job_id','submission_state','validated_pub_count')}),flush=True)
        service=make_saved_profile_service(load_saved_account_profiles(('labK',))['labK'])
        instance=rows[0]['metadata']['instance']
        if service.active_instance()!=instance:raise RuntimeError('Active instance mismatch')
        jobs=service.jobs(limit=None,backend_name='ibm_fez',instance=instance,session_id=batch,descending=False)
        if len({j.job_id() for j in jobs})!=len(jobs):raise RuntimeError('Duplicate provider listing')
        for job in jobs:
            matches=[];rejections={}
            exact=[row for row in rows if row['job_id']==job.job_id()]
            candidates=exact or [row for row in rows if not row['job_id']]
            for row in candidates:
                try:
                    verify_reconciliation_job(row,job,service,root=root)
                    matches.append(row['treatment_id'])
                except (RuntimeError,ValueError,TypeError,KeyError) as exc:
                    rejections[row['treatment_id']]=str(exc)
            print('PROVIDER',json.dumps({'job_id':job.job_id(),'status':str(job.status()),
                'batch_id':str(job.session_id),'created':str(job.creation_date),'matches':matches,'rejections':rejections}),flush=True)
        print('READ_ONLY_AUDIT_FINISHED; no identity updates or result retrieval performed',flush=True)
except Exception as exc:
    seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc));message=str(exc)
        print('AUDIT_BLOCKED',type(exc).__name__,'proxy_failure=', 'proxy' in message.lower(),
              'connection_refused=', '10061' in message or 'refused' in message.lower(),flush=True)
        exc=exc.__cause__ or exc.__context__
    sys.exit(2)
finally:
    print('DATABASE_UNCHANGED',before==digest(),flush=True)
