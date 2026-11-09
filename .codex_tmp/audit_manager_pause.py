import sys, json, hashlib
from pathlib import Path
root=Path.cwd()
sys.path.insert(0,str(root/'02_Hardware/04_code/03_ai_assistant'))
from job_registry import JobRegistry
from final_configuration import load_final_config,campaign_authorization_from_final_config
from independent_n_campaign import _campaign_usage,_target_from_level
from targeted_recovery import plan_invalid_pub_slots
from account_config import load_saved_account_profiles,make_saved_profile_service
from read_only_preflight import preflight_account,frozen_bundle_summary
from quota import account_quota_report
from runtime_estimator import historical_batch_estimate
from experiment_manifest import build_manifest
r=JobRegistry(JobRegistry.default_database(root),readonly=True)
c=r.independent_campaign_status('vqe-independent-n-01')
config=load_final_config(root);auth=campaign_authorization_from_final_config(config)
target=_target_from_level(root,c['manifest']['levels'][5])
rows=r.rows('vqe-independent-n-01-n30-block-001')
out={'checkpoint':c['checkpoint'],'events':[dict(id=e['id'],type=e['event_type'],utc=e['created_at']) for e in c['events']],
     'jobs':[{**{k:x.get(k) for k in ('job_id','batch_id','treatment_id','account_label','submission_state')},
              'monitor':{k:x.get('metadata',{}).get('last_remote_monitor',{}).get(k) for k in ('status','failure_class','validation_error')}} for x in rows],
     'authorization':{k:auth.get(k) for k in ('live_auto_submit','total_runtime_budget_seconds','account_runtime_budget_seconds','allowed_actions')},
     'usage':_campaign_usage(r,c['manifest'],auth)}
try: out['recovery_dry_run']=plan_invalid_pub_slots(root,target,r,final_config=config,dry_run=True)
except Exception as e: out['recovery_error']=type(e).__name__+': '+str(e)
path=root/'.codex_tmp/manager_pause_audit.json'
path.write_text(json.dumps(out,indent=2),encoding='utf-8')
print('Local audit saved',flush=True)
profiles=load_saved_account_profiles(tuple(auth['allowed_accounts']))
reports={};out['provider_inventory']={}
frozen=frozen_bundle_summary(root)
for label,profile in profiles.items():
    try:
        svc=make_saved_profile_service(profile)
        reports[label]=preflight_account(profile,service_factory=lambda _,svc=svc:svc,
            allowed_instance_refs=auth['allowed_instance_refs'][label],frozen_validator=lambda:frozen)
        if label=='labI':
            jobs=svc.jobs(limit=None,session_id=rows[0]['batch_id'],instance=svc.active_instance())
            out['provider_inventory'][label]=[{'job_id':j.job_id(),'status':str(j.status())} for j in jobs]
        print(label,reports[label]['status'],flush=True)
    except Exception as e:
        reports[label]={'status':'FAIL','error_type':type(e).__name__}
        print(label,type(e).__name__,flush=True)
    out['fresh_preflight']=reports
    path.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8')
estimate=historical_batch_estimate(root,build_manifest(root,n=5,replicate_blocks=1,shots=4096,experiment_id=target.experiment_id),require_defensible=True)
out['account_decisions']=account_quota_report(reports,runtime_estimate=estimate,usage=out['usage'],authorization=auth,expected_hashes=target.input_hashes)
path.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8')
