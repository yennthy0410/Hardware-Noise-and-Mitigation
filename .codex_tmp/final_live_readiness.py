"""Read-only preflight/dry-run audit. No submission or registry mutation APIs."""
import sys,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
root=Path.cwd();sys.path.insert(0,str(root/'02_Hardware/04_code/03_ai_assistant'))
from job_registry import JobRegistry
from worker_runtime import WorkerLock,RetryingReadOnlyService
from independent_n_campaign import campaign_root,_target_from_level,_campaign_usage
from research_pipeline import run_pipeline,verify_campaign
from final_configuration import load_final_config,campaign_authorization_from_final_config
from read_only_preflight import preflight_all,frozen_bundle_summary,_instance_ref
from account_config import make_saved_profile_service
from experiment_manifest import build_manifest
from runtime_estimator import historical_batch_estimate
from quota import select_highest_available_account,AccountSelectionUnavailable

cid='vqe-independent-n-01';block=cid+'-n25-block-004'
directory=root/'manager_audit_backups'/('live_readiness_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
directory.mkdir(parents=True)
def save(name,data):(directory/name).write_text(json.dumps(data,indent=2,default=str),encoding='utf-8')
def snapshot(reg):
    with reg._connection() as db:
        return {r[0]:[tuple(v) for v in db.execute('SELECT * FROM "'+r[0]+'"')] for r in
                db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}
def chain(exc):
    seen=set();out=[]
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc));out.append(type(exc).__name__);exc=exc.__cause__ or exc.__context__
    return out
with WorkerLock(campaign_root(root,cid)/'worker.lock'):
    reg=JobRegistry(JobRegistry.default_database(root),readonly=True);before=snapshot(reg)
    status=reg.independent_campaign_status(cid);cfg=load_final_config(root)
    auth=campaign_authorization_from_final_config(cfg)
    local=verify_campaign(root,cid);save('local_verification.json',local)
    dry=run_pipeline(root,cid,mode='offline');save('dry_run.json',dry)
    target=_target_from_level(root,status['levels'][4],registry=reg)
    frozen=frozen_bundle_summary(root)
    manifest=build_manifest(root,n=5,replicate_blocks=1,shots=4096,experiment_id=target.experiment_id)
    estimate=historical_batch_estimate(root,manifest,require_defensible=True)
    services={};errors={}
    def factory(profile):
        try:
            service=RetryingReadOnlyService(make_saved_profile_service(profile),attempts=2)
            services[profile.label]=service;return service
        except Exception as exc:errors[profile.label]=chain(exc);raise
    labels=auth['allowed_accounts']
    print('Running read-only preflight for '+', '.join(labels),flush=True)
    reports=preflight_all(tuple(labels),auth_mode='saved-profile',root=root,
        allowed_instance_refs=auth['allowed_instance_refs'],service_factory=factory)
    save('preflight.json',reports)
    inventories={}
    for label in labels:
        service=services.get(label)
        if service is None:
            inventories[label]={'complete':False,'reason':'Provider connection unavailable','error_types':errors.get(label,[])};continue
        try:
            active=service.active_instance()
            if _instance_ref(active) not in auth['allowed_instance_refs'].get(label,[]):raise RuntimeError('Unauthorized instance')
            jobs=list(service.jobs(limit=None,instance=active))
            inventory=[{'job_id':job.job_id(),'status':str(job.status())} for job in jobs]
            inventories[label]={'complete':True,'job_count':len(inventory),'jobs':inventory}
        except Exception as exc:inventories[label]={'complete':False,'error_types':chain(exc)}
    save('provider_inventory.json',inventories)
    usage=_campaign_usage(reg,status['manifest'],auth)
    try:selection=select_highest_available_account(reports,runtime_estimate=estimate,usage=usage,authorization=auth,expected_hashes=target.input_hashes)
    except AccountSelectionUnavailable as exc:selection={'eligible':False,'account_decisions':exc.decisions}
    save('quota_selection.json',selection)
    selected=reg.repaired_pub_values(target.target_id)
    original=reg.rows();replacements=[r for level in status['levels'] for r in reg.replacement_jobs(level['target_id'])]
    unknown=[r['id'] for r in original if r['submission_state'] in ('UNKNOWN','SUBMITTING')]
    ambiguous=[r['replacement_id'] for r in replacements if r['state'] in ('UNKNOWN','SUBMITTING','RESERVED')]
    ids=[r['job_id'] for r in original+replacements if r['job_id']]
    duplicates=[jid for jid,n in Counter(ids).items() if n>1]
    next_level=next(r for r in dry['dry_run']['levels'] if r['n']==25)
    blockers=[]
    if not local['verification_passed']:blockers.append('Local campaign verification failed')
    if unknown or ambiguous or duplicates:blockers.append('Unresolved or duplicate local submission identities')
    if len(selected)!=120 or next_level['next_block_id']!=block or reg.rows(block):blockers.append('Batch 004 progression or reservation mismatch')
    if not cfg['live_submission_enabled'] or not auth['live_auto_submit']:blockers.append('Live execution disabled in existing authorization')
    for label in labels:
        if reports[label].get('status')!='PASS':blockers.append(label+': preflight failed; IBM authentication/backend/quota eligibility not verified')
        if not inventories[label]['complete']:blockers.append(label+': fresh complete provider inventory unavailable')
    if not selection.get('eligible'):blockers.append('No authorized account has verified provider quota and workload headroom')
    assert before==snapshot(reg),'Read-only audit unexpectedly changed SQLite'
    result={'result':'BLOCKED' if blockers else 'LIVE_READY','checked_utc':datetime.now(timezone.utc).isoformat(),
        'campaign':cid,'block':block,'campaign_state':status['state'],'validated_pubs':len(selected),'required_pubs':200,
        'planned_jobs':next_level['planned_jobs_in_next_batch'],'planned_pubs':next_level['planned_pubs_in_next_batch'],
        'frozen_inputs_verified':local['verification_passed'] and frozen['input_sha256']==target.input_hashes,
        'unknown':unknown,'ambiguous_replacements':ambiguous,'duplicate_ids':duplicates,
        'authorized_accounts':labels,'connection_errors':errors,
        'runtime':{'stress_seconds':estimate.stress_block_seconds,'safety_margin_seconds':auth['safety_margin_seconds'],
            'minimum_provider_seconds':150,'required_workload_headroom_seconds':estimate.stress_block_seconds+auth['safety_margin_seconds'],
            'defensible':estimate.defensible,'operator_approved_provisional':estimate.operator_approved_provisional},
        'blockers':blockers,'database_unchanged':True,'submission_performed':False,'audit_directory':str(directory)}
    save('readiness.json',result);print(json.dumps(result,indent=2))
