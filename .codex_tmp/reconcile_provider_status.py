import sys,json
from pathlib import Path
root=Path.cwd();sys.path.insert(0,str(root/'02_Hardware/04_code/03_ai_assistant'))
from account_config import load_saved_account_profiles,make_saved_profile_service
from job_registry import JobRegistry
registry=JobRegistry(JobRegistry.default_database(root),readonly=True)
rows=[r for r in registry.rows('vqe-independent-n-01-n25-block-003') if r['attempt']==1 and r['job_id']]
report={'read_only':True,'submission_performed':False,'jobs':[],'provider_verified':False}
try:
    service=make_saved_profile_service(load_saved_account_profiles(('labK',))['labK'])
    for row in rows:
        job=service.job(row['job_id'])
        bid=job.session_id() if callable(job.session_id) else job.session_id
        if job.job_id()!=row['job_id'] or str(bid)!=row['batch_id']:raise RuntimeError('Provider identity mismatch')
        report['jobs'].append({'job_id':job.job_id(),'treatment':row['treatment_id'],'status':str(job.status())})
    report['provider_verified']=len(report['jobs'])==7
except Exception as exc:
    types=[];seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc));types.append(type(exc).__name__);exc=exc.__cause__ or exc.__context__
    report['error_types']=types
(root/'.codex_tmp/campaign_reconcile_provider.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
