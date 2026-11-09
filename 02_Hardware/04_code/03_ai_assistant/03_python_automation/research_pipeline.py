"""Thin command-line entry point for the existing VQE independent-n backend.

Routine and processing commands are local Python operations. Live Batch
submission is reachable only through ``mxene run --live`` with two explicit
operator confirmations, and remains subject to final_config.json, fresh
preflight, quota, and runtime-evidence gates.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from account_config import ACCOUNT_LABELS


AI_DIR=Path(__file__).resolve().parents[1]
VQE_DIR=AI_DIR.parent/"01_vqe"
if str(VQE_DIR) not in sys.path:
    sys.path.insert(0,str(VQE_DIR))
if str(AI_DIR) not in sys.path:
    sys.path.insert(0,str(AI_DIR))

N_LEVELS=(5,10,15,20,25,30)


def _registry_path(project: str | Path, registry_path: str | Path | None=None) -> Path:
    if registry_path is not None:
        return Path(registry_path).resolve()
    from job_registry import JobRegistry
    return JobRegistry.default_database(project)


def _typed_target(level: dict[str,Any], project=None, registry=None):
    from independent_n_campaign import _target_from_level
    return _target_from_level(project or Path.cwd(),level,registry=registry)


def verify_campaign(project, campaign_id):
    """Write-free SQLite, checkpoint, frozen-manifest and accepted-coverage audit."""
    from job_registry import JobRegistry
    from batch_progression import evaluate_batch_progression
    registry=JobRegistry(_registry_path(project),readonly=True)
    status=registry.independent_campaign_status(campaign_id)
    if not status.get('exists'):raise KeyError(campaign_id)
    with registry._connect() as db:
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    levels=[];failures=[]
    if integrity!='ok':failures.append('SQLite integrity check failed')
    for level in status['levels']:
        target=_typed_target(level,project,registry)
        progress=registry.target_status(target.target_id)
        blocks=[evaluate_batch_progression(registry,target.target_id,b['logical_block_id'])
                for b in progress.get('blocks',[])]
        complete=(len(blocks)==target.blocks_required and all(b['eligible'] for b in blocks))
        if int(level['level_index'])<int(status['current_level_index']) and not complete:
            failures.append(f"Checkpoint advanced past incomplete target {target.target_id}")
        levels.append({'target_id':target.target_id,'target_n':target.target_n,
            'accepted_logical_pubs':len(registry.repaired_pub_values(target.target_id)),
            'required_logical_pubs':target.target_n*len(target.treatment_ids),
            'frozen_manifest_verified':True,'complete':complete,'blocks':blocks})
    targets={level['target_id'] for level in levels}
    unknown=[{'row_id':r['id'],'state':r['submission_state']} for r in registry.rows()
             if r.get('target_id') in targets and r['submission_state'] in ('UNKNOWN','SUBMITTING')]
    unknown += [{'replacement_id':r['replacement_id'],'state':r['state']}
                for target in targets for r in registry.replacement_jobs(target)
                if r['state'] in ('UNKNOWN','SUBMITTING','RESERVED')]
    return {'campaign_id':campaign_id,'verification_passed':not failures and not unknown,
        'sqlite_integrity':integrity,'levels':levels,'unresolved_identities':unknown,'failures':failures,
        'provider_checked':False,'submission_performed':False,'read_only':True}


def run_pipeline(project: str | Path, campaign_id: str, *, mode: str="offline",
                 confirm_live_submission: bool=False, authorize_live_campaign: bool=False,
                 registry_path: str | Path | None=None, account_label: str | None=None, ai_mode: str="python",
                 ask_ai: bool=False, once: bool=False, resume: bool=False, poll_seconds: int=60) -> dict[str,Any]:
    """Run a read-only dry-run by default, or explicitly invoke the live backend."""
    if mode not in ("offline","live"):
        raise ValueError("mode must be offline or live")
    database=_registry_path(project,registry_path)
    if mode=="offline":
        if not database.is_file():
            return {"campaign_id":campaign_id,"state":"NOT_PLANNED","mode":"offline",
                "submission_performed":False,"batch_created":False,
                "reason":"Campaign is not registered in SQLite; restore or explicitly plan it before running."}
        from independent_n_campaign import dry_run_independent_campaign
        from job_registry import JobRegistry
        report=dry_run_independent_campaign(project,campaign_id,JobRegistry(database,readonly=True))
        return {"campaign_id":campaign_id,"mode":"offline","state":"OFFLINE_DRY_RUN",
            "submission_performed":False,"batch_created":False,"dry_run":report}
    if not confirm_live_submission or not authorize_live_campaign:
        raise RuntimeError("Live mode requires --authorize-live-campaign and --confirm-live-submission")
    from final_configuration import load_final_config
    final_config=load_final_config(project)
    if not final_config["live_submission_enabled"]:
        return {'campaign_id':campaign_id,'mode':'live','pipeline':{
            'state':'BLOCKED_AUTHORIZATION','pause_class':'PAUSED_OPERATOR_ACTION',
            'reason':'Live submission is disabled by final_config.json','submission_performed':False}}
    if not database.is_file():
        raise FileNotFoundError("Campaign registry is missing; restore or explicitly plan it before live execution")
    from job_registry import JobRegistry
    snapshot=JobRegistry(database,readonly=True).independent_campaign_status(campaign_id)
    if not snapshot.get("exists"):
        raise KeyError(f"Unknown independent-n campaign: {campaign_id}")
    level_index=int(snapshot.get("current_level_index",0))
    if level_index < 0 or level_index > len(snapshot["manifest"]["levels"]):
        raise RuntimeError("Campaign has no pending n level")
    level=snapshot["manifest"]["levels"][min(level_index,len(snapshot['manifest']['levels'])-1)]
    # In automatic mode, the immutable manifest account is historical only.
    # The executor selects a fresh eligible account before each new Batch.
    if final_config.get("account_selection") != "highest_available_quota":
        selected_account = final_config.get("account_by_n", {}).get(str(level["target_n"]))
        if selected_account not in final_config["enabled_account_labels"]:
            raise RuntimeError("Configured n-level account is not enabled in final_config.json")
    if snapshot["manifest"].get("backend") != final_config["allowed_backend"]:
        raise RuntimeError("Campaign backend is not allowed by final_config.json")
    from independent_n_campaign import run_independent_campaign,run_campaign_auto_manager
    if once:
        result=run_independent_campaign(project,campaign_id,JobRegistry(database),
            authorize_live_campaign=True,ai_mode=ai_mode,explicit_ai=ask_ai,account_label=account_label,
            once=True,resume=resume,final_config=final_config,poll_seconds=poll_seconds)
    else:
        result=run_campaign_auto_manager(project,campaign_id,JobRegistry(database),
            authorize_live_campaign=True,ai_mode=ai_mode,explicit_ai=ask_ai,account_label=account_label,
            resume=resume,final_config=final_config,poll_seconds=poll_seconds)
    return {"campaign_id":campaign_id,"mode":"live","pipeline":result}


def _paper_artifacts(output: str | Path) -> dict[str,Any]:
    """Inventory generated paper data/figures without recreating them."""
    import math
    directory=Path(output)
    csv_files=sorted(path for path in directory.rglob("*.csv") if path.is_file())
    png_files=sorted(path for path in directory.rglob("*.png") if path.is_file())
    if not csv_files or not png_files:
        raise RuntimeError("Analysis output must contain both CSV tables and PNG figures")
    from PIL import Image
    figures=[]
    for path in png_files:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            dpi=image.info.get("dpi")
            figures.append({"path":str(path),"width":image.width,"height":image.height,
                "dpi":list(dpi[:2]) if dpi else None,
                "sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    tables=[{"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()} for path in csv_files]
    return {"csv_count":len(tables),"png_count":len(figures),"csv":tables,"png":figures,
        "files_verified":all(math.isfinite(item["width"]) and item["width"]>0 and item["height"]>0 for item in figures)}


from step_artifacts import artifact_hashes as _step_artifact_hashes


class CampaignStepBlocked(RuntimeError):
    """Known failed safety gate. Earlier verified COLLECT writes may already be durable."""


def _collect_local_scope(project, registry, target, authorization, batch, *, job_id=None):
    """Shared write-free ledger/submission/archive checks for preview and execution."""
    from vqe_adapter import record_directory
    if type(batch) is not int or not 1<=batch<=target.target_n//5:
        raise CampaignStepBlocked('Explicit existing Batch sequence required')
    blocks=registry.target_status(target.target_id).get('blocks',[])
    matches=[b for b in blocks if b['sequence_no']==batch]
    if len(matches)!=1:raise CampaignStepBlocked('Batch sequence is not uniquely registered')
    block=matches[0]['logical_block_id'];rows=registry.rows(block)
    if job_id is not None:
        rows=[row for row in rows if row.get('job_id')==job_id]
        if len(rows)!=1:raise CampaignStepBlocked('Registered original Job ID does not resolve to exactly one attempt')
    elif (len(rows)!=8 or len({r['treatment_id'] for r in rows})!=8
            or {r['treatment_id'] for r in rows}!=set(target.treatment_ids)
            or len({r.get('job_id') for r in rows})!=8 or any(not r.get('job_id') for r in rows)
            or len({r.get('batch_id') for r in rows})!=1 or not rows[0].get('batch_id')):
        raise CampaignStepBlocked('Exactly eight distinct original jobs in one Batch required')
    if job_id is None and any(r['logical_block_id']==block for r in registry.replacement_jobs(target.target_id)):
        raise CampaignStepBlocked('Replacement-linked Batch requires a separately verified collection contract')
    root=record_directory(project,block).resolve();identities=[]
    def file_hash(path):
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        return digest.hexdigest()
    def archive_evidence(row):
        records=[p for p in registry.pub_validation_records(target.target_id) if p['job_row_id']==row['id']]
        if not records:return None
        raw=Path(row['metadata'].get('per_pub_source_path','')).resolve()
        digest=row['metadata'].get('per_pub_source_sha256')
        if (not raw.is_relative_to(root/row['treatment_id']/'raw_retrievals') or not raw.is_file()
                or file_hash(raw)!=digest
                or {p['pub_index'] for p in records}!=set(range(1,6)) or len(records)!=5
                or any(p['source_sha256']!=digest for p in records)):
            raise CampaignStepBlocked('Existing PUB validation/archive identity is incomplete or inconsistent')
        missing=sum('MISSING_PUB_RESULT' in json.loads(p['error_codes_json']) for p in records)
        valid=sum(p['expectation_valid']==1 and p['uncertainty_valid']==1 for p in records)
        if (row.get('submission_state')!='COMPLETED' or row.get('validated_pub_count')!=valid
                or row.get('collected_pub_count')!=5-missing):
            raise CampaignStepBlocked('Archive/PUB records exist but canonical job progress is not fully committed')
        return dict(valid=valid,invalid=5-valid-missing,missing=missing)
    for row in rows:
        if (row['target_id']!=target.target_id or row['attempt']!=1 or row['chunk_n']!=5
                or row['input_hashes']!=target.input_hashes
                or row['account_label'] not in authorization['allowed_accounts']
                or row['backend']!=target.backend
                or row['submission_state'] not in ('SUBMITTED','COMPLETED','FAILED','CANCELLED')):
            raise CampaignStepBlocked('Original attempt, authorization, frozen inputs or job state mismatch')
        folder=root/row['treatment_id'];files=[];docs=[]
        for name in ('job_submitted.json','submission_state.json'):
            path=folder/name
            if not path.is_file() or path.stat().st_size>1024*1024:raise CampaignStepBlocked('Required submission identity file missing or oversized')
            payload=path.read_bytes()
            try:d=json.loads(payload)
            except ValueError:raise CampaignStepBlocked('Invalid submission identity JSON') from None
            if not isinstance(d,dict):raise CampaignStepBlocked('Invalid submission identity schema')
            for k in ('job_id','logical_block_id','treatment_id','account_label','backend','input_hashes'):
                if d.get(k)!=row[k]:raise CampaignStepBlocked('Submission file conflicts with registered job identity: '+k)
            if d.get('instance')!=row['metadata'].get('instance') or not d.get('instance'):
                raise CampaignStepBlocked('Submission instance does not match registered attempt')
            if d.get('n')!=5 or d.get('pubs_per_treatment')!=5:raise CampaignStepBlocked('Original PUB mapping mismatch')
            docs.append(d);files.append((path,hashlib.sha256(payload).hexdigest()))
        submitted,intent=docs
        if (submitted.get('batch_id')!=row['batch_id'] or intent.get('batch_id',row['batch_id'])!=row['batch_id'] or not intent.get('attempt_id')
                or intent.get('status')!='SUBMITTED'
                or intent.get('submit_started_utc')!=row['metadata'].get('submit_started_utc')
                or not intent.get('submit_started_utc')
                or submitted.get('shots_requested_per_pub')!=4096 or intent.get('requested_shots_per_pub')!=4096):
            raise CampaignStepBlocked('Batch, attempt intent or shot identity cannot be verified')
        prior=archive_evidence(row)
        if prior is None and any((folder/name).exists() for name in ('result.json','expectations.csv','job_retrieved_metadata.json')):
            raise CampaignStepBlocked('Interrupted legacy publication requires archive reconciliation; no overwrite')
        identities.append((row,files,prior))
    return block,identities,archive_evidence


def _collect_scope_receipt(target, batch, block, identities):
    from independent_n_campaign import _sha
    jobs=[{k:row[k] for k in ('job_id','treatment_id','account_label','batch_id','attempt')}
          for row,_,_ in identities]
    jobs.sort(key=lambda row:row['treatment_id'])
    proof={'target_id':target.target_id,'n':target.target_n,'batch':batch,'block':block,'jobs':jobs,
           'submission_files':sorted((str(path),digest) for _,files,_ in identities for path,digest in files)}
    return {'campaign_id':'vqe-independent-n-01','n':target.target_n,'batch':batch,
            'operation':'COLLECT-only','jobs':jobs,'scope_id':_sha(proof)}


def preview_collect_step(project, campaign_id, n, batch):
    """Canonical local-only scope preview; no provider, locks, or writes."""
    from job_registry import JobRegistry
    from independent_n_campaign import _sha,_campaign_has_unknown
    from final_configuration import load_final_config,campaign_authorization_from_final_config
    if campaign_id!='vqe-independent-n-01' or type(n) is not int or n not in N_LEVELS:
        raise CampaignStepBlocked('One supported campaign and explicit n-level required')
    registry=JobRegistry(_registry_path(project),readonly=True)
    status=registry.independent_campaign_status(campaign_id)
    if not status.get('exists'):raise CampaignStepBlocked('Existing campaign is not registered')
    manifest=status['manifest']
    if _sha({k:v for k,v in manifest.items() if k!='manifest_sha256'})!=status['manifest_sha256'] or manifest.get('manifest_sha256')!=status['manifest_sha256']:
        raise CampaignStepBlocked('Immutable campaign manifest hash mismatch')
    config=load_final_config(project);authorization=campaign_authorization_from_final_config(config)
    if manifest.get('backend')!=config['allowed_backend'] or not authorization['allowed_accounts'] or 'collect' not in authorization['allowed_actions']:
        raise CampaignStepBlocked('Canonical backend/account/action authorization mismatch')
    if _campaign_has_unknown(registry,status):raise CampaignStepBlocked('UNKNOWN submission reconciliation required; no STEP mutation permitted')
    level=next((item for item in manifest['levels'] if item['target_n']==n),None)
    if level is None:raise CampaignStepBlocked('Requested n-level is not registered')
    target=_typed_target(level,project,registry)
    block,identities,_=_collect_local_scope(project,registry,target,authorization,batch)
    return _collect_scope_receipt(target,batch,block,identities)


def _collect_campaign_step(project, registry, target, authorization, batch, *, expected_scope=None, job_id=None, monitor_only=False):
    """Under caller's campaign lock; one original Batch, no scheduling or repair."""
    from vqe_adapter import collect_block, ArchivedInvalidPubs
    from batch_scheduler import verify_reconciliation_job
    from account_config import load_saved_account_profiles, make_saved_profile_service
    from read_only_preflight import _instance_ref
    block,identities,archive_evidence=_collect_local_scope(project,registry,target,authorization,batch,job_id=job_id)
    if expected_scope is not None and _collect_scope_receipt(target,batch,block,identities)['scope_id']!=expected_scope:
        raise CampaignStepBlocked('Confirmed COLLECT job/submission scope changed; request a fresh preview')
    # Validate every identity/status before opening any registry write transaction.
    services={};jobs={};statuses={}
    try:
        for row,files,prior in identities:
            account=row['account_label']
            if account not in services:
                service=make_saved_profile_service(load_saved_account_profiles((account,))[account])
                if _instance_ref(service.active_instance()) not in authorization['allowed_instance_refs'].get(account,[]):
                    raise CampaignStepBlocked('Active provider instance is not authorized')
                services[account]=service
            service=services[account];job=service.job(row['job_id'])
            verify_reconciliation_job(row,job,service,root=project)
            remote=str(job.status()).upper().split('.')[-1]
            if remote not in ('DONE','COMPLETED','QUEUED','PENDING','RUNNING','INITIALIZING','VALIDATING','FAILED','ERROR','CANCELLED','CANCELED'):
                raise CampaignStepBlocked('Provider job status is unknown; no collection permitted')
            statuses[row['job_id']]=remote;jobs[row['job_id']]=job
    except CampaignStepBlocked:raise
    except Exception as exc:
        raise CampaignStepBlocked('Provider identity/status verification unavailable: '+type(exc).__name__+'; no results collected') from None
    from datetime import datetime,timezone
    observation={'job_ids_inspected':list(statuses),'provider_statuses':statuses,
        'provider_checked_at':datetime.now(timezone.utc).isoformat(),
        'status_counts':{label:sum(('DONE' if s=='COMPLETED' else 'PENDING' if s in ('QUEUED','INITIALIZING','VALIDATING') else 'FAILED' if s in ('ERROR','CANCELLED','CANCELED') else s)==label for s in statuses.values()) for label in ('DONE','PENDING','RUNNING','FAILED','UNKNOWN')}}
    if monitor_only:
        return observation|{'state':'OBSERVED','collected_jobs':0,
            'reason':'Provider status checked only; no result retrieval or archive/registry write'}
    # Existing registry only: do not run schema initialization/migration for STEP.
    registry.readonly=False
    completed=sum(s in ('DONE','COMPLETED') for s in statuses.values())
    collected=already=valid=invalid=missing=0;failures=[]
    try:
        for row,files,prior in identities:
            remote=statuses[row['job_id']]
            if prior is not None:
                already+=1;valid+=prior['valid'];invalid+=prior['invalid'];missing+=prior['missing'];continue
            if remote not in ('DONE','COMPLETED'):continue
            for path,digest in files:
                if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise CampaignStepBlocked('Submission identity changed during collection')
            job=jobs[row['job_id']]
            class CompletedJob:
                def __getattr__(self,name):return getattr(job,name)
                def result(self,*args,**kwargs):
                    if str(job.status()).upper().split('.')[-1] not in ('DONE','COMPLETED'):
                        raise RuntimeError('Job is no longer verified completed')
                    return job.result(*args,**kwargs)
            class CollectionReads:
                def job(self,jid):
                    if jid!=row['job_id']:raise RuntimeError('Collector requested an unverified job')
                    return CompletedJob()
            failure=None
            try:
                collect_block(project,block,shots=4096,n=5,account_label=row['account_label'],ibm_account=row['account_label'],
                    instance=row['metadata']['instance'],registry=registry,service_override=CollectionReads(),ai_mode='python',
                    treatments=(row['treatment_id'],),job_row_id=row['id'])
            except Exception as exc:
                chain=exc;scientific_failure=False
                for _ in range(8):
                    if isinstance(chain,ArchivedInvalidPubs):scientific_failure=True;break
                    chain=getattr(chain,'__cause__',None)
                    if chain is None:break
                if not scientific_failure:failure=type(exc).__name__
            finally:
                jobs.pop(row['job_id'],None)
                job=None
            updated=next(r for r in registry.rows(block) if r['id']==row['id'])
            evidence=archive_evidence(updated)
            if evidence is None:
                failures.append({'treatment':row['treatment_id'],'error':failure or 'ArchiveOrLedgerNotVerified'})
                # No blind retry or automatic progression after incomplete writes.
                break
            collected+=1;valid+=evidence['valid'];invalid+=evidence['invalid'];missing+=evidence['missing']
            if failure:
                failures.append({'treatment':row['treatment_id'],'error':failure,'raw_and_pub_evidence_verified':True})
                break
    finally:registry.readonly=True
    pending=sum(s in ('QUEUED','PENDING','RUNNING','INITIALIZING','VALIDATING') for s in statuses.values())
    failed=sum(s in ('FAILED','ERROR','CANCELLED','CANCELED') for s in statuses.values())
    return {'state':'COLLECTION_INCOMPLETE' if failures else 'COLLECT_CHECKED','completed_jobs':completed,'collected_jobs':collected,
            'already_archived_jobs':already,'valid_pubs':valid,'invalid_pubs':invalid,'missing_pubs':missing,
            'pending_jobs':pending,'failed_jobs':failed,'unvalidated_pubs':5*(len(identities)-collected-already),
            'recovery_required':bool(invalid or missing),'archives':[r.get('metadata',{}).get('per_pub_source_path') for r in registry.rows(block) if r.get('job_id') in statuses],
            'failures':failures,'batch_sequence':batch,'archive_and_ledger_verified':not failures,**observation}


def preview_single_pub(project, campaign_id, n, purpose, treatment, slot=None, account=None, *, registry_path=None):
    """Read-only preparation; never plan, reserve, instantiate a provider or authorize LIVE."""
    from job_registry import JobRegistry
    from final_configuration import load_final_config,MIN_RECOVERY_QUOTA_SECONDS
    from independent_n_campaign import _campaign_has_unknown, _sha
    if campaign_id!='vqe-independent-n-01' or type(n) is not int or n not in N_LEVELS or purpose not in ('TEST','RECOVERY'):
        raise ValueError('Unsupported single-PUB scope')
    registry=JobRegistry(_registry_path(project,registry_path),readonly=True)
    status=registry.independent_campaign_status(campaign_id)
    if not status.get('exists'):raise ValueError('Campaign is not registered')
    manifest=status['manifest']
    if _sha({k:v for k,v in manifest.items() if k!='manifest_sha256'})!=status.get('manifest_sha256'):
        raise ValueError('Frozen campaign manifest mismatch')
    level=next(l for l in manifest['levels'] if l['target_n']==n)
    target=_typed_target(level,project,registry);cfg=load_final_config(project)
    if treatment not in target.treatment_ids:raise ValueError('Treatment is not frozen M0-M7')
    accounts=cfg['enabled_account_labels']
    if account is not None and account not in accounts:raise ValueError('Account is not authorized')
    result={'purpose':purpose,'n':n,'treatment':treatment,'slot':slot,'account':account,'accounts':accounts,
        'backend':target.backend,'pub_count':1,'campaign_id':campaign_id,'input_reference':target.sha256,
        'provider_checked_at':None,'quota_seconds':None,'submission_enabled':False,
        'submission_blocker':'Recovery LIVE requires a fresh source-job/attempt reconciliation and quota quote bound to one-time authorization. The existing submission CLI does not supply that Dashboard confirmation contract.',
        'scope_id':None,'replacement_id':None,'job_id':None,'recorded_state':None,'slot_options':[]}
    if purpose=='TEST':
        result.update(state='BLOCKED',reason='No separately approved isolated-test quota policy or canonical test storage contract. No test job, namespace or production record is created.',submission_blocker='Isolated TEST quota policy and storage contract are not approved.')
        return result
    accepted={(r['treatment_id'],r['logical_pub_index']) for r in registry.repaired_pub_values(target.target_id)}
    plans=[r for r in registry.replacement_jobs(target.target_id) if r['treatment_id']==treatment]
    for index in range(1,n+1):
        candidates=[r for r in plans if r['logical_pub_index']==index]
        if candidates and len([r for r in candidates if r['attempt']==max(x['attempt'] for x in candidates)])==1:
            latest=max(candidates,key=lambda r:r['attempt'])
            result['slot_options'].append({'slot':index,'state':latest['state'],'replacement_id':latest['replacement_id'],'accepted':(treatment,index) in accepted})
    if slot is None:
        result.update(state='PREVIEW',reason='Select a registered logical slot. Listed candidates are recorded plans, not LIVE authorization.');return result
    if type(slot) is not int or not 1<=slot<=n:raise ValueError('Logical slot is out of range')
    selected=[r for r in plans if r['logical_pub_index']==slot]
    if not selected:
        result.update(state='BLOCKED',reason='No canonical durable replacement plan exists. Pending or uncollected originals are not eligible missing PUBs.');return result
    latest=max(selected,key=lambda r:r['attempt'])
    if sum(r['attempt']==latest['attempt'] for r in selected)!=1:
        result.update(state='BLOCKED',reason='Ambiguous replacement attempt identity.');return result
    result.update(replacement_id=latest['replacement_id'],job_id=latest['job_id'],recorded_state=latest['state'],account=latest['account_label'])
    result['scope_id']=_sha({'campaign':campaign_id,'n':n,'treatment':treatment,'slot':slot,'replacement_id':latest['replacement_id'],'attempt':latest['attempt'],'job_id':latest['job_id'],'account':latest['account_label'],'inputs':target.input_hashes})
    if latest['account_label'] not in accounts:
        result.update(state='BLOCKED',reason='Recorded account is no longer authorized.');return result
    if account is not None and latest['account_label']!=account:
        result.update(state='BLOCKED',reason='Selected account differs from the durable plan; no account switching permitted.');return result
    if _campaign_has_unknown(registry,status) or latest['state'] in ('UNKNOWN','SUBMITTING','RESERVED'):
        result.update(state='BLOCKED',reason='UNKNOWN or unresolved intent requires canonical reconciliation.');return result
    if latest.get('metadata',{}).get('replacement_pub_map') or (latest['job_id'] and sum(r['job_id']==latest['job_id'] for r in registry.replacement_jobs(target.target_id))!=1):
        result.update(state='BLOCKED',reason='Grouped replacement job is outside the exactly-one-PUB contract.');return result
    result.update(state='RECORDED',reason='Existing replacement record only; no new submission authorized.',accepted=(treatment,slot) in accepted,
        minimum_quota_seconds=MIN_RECOVERY_QUOTA_SECONDS,operations=['monitor','collect'] if latest['job_id'] else [])
    return result


def _single_pub_read_step(project,registry,target,campaign_id,n,spec):
    """One canonical replacement read/collect. Submission is intentionally not exposed."""
    allowed={'purpose','treatment','slot','account','scope_id','action'}
    if not isinstance(spec,dict) or set(spec)!=allowed:raise CampaignStepBlocked('Invalid typed single-PUB request')
    if spec['purpose']!='RECOVERY' or spec['action'] not in ('monitor','collect'):
        raise CampaignStepBlocked('TEST and LIVE submission are blocked; only existing replacement monitor/collect is exposed')
    preview=preview_single_pub(project,campaign_id,n,spec['purpose'],spec['treatment'],spec['slot'],spec['account'],registry_path=registry.database)
    if preview['state']!='RECORDED' or not preview.get('job_id') or preview['scope_id']!=spec['scope_id']:
        raise CampaignStepBlocked('Replacement scope changed, is unregistered, or requires reconciliation')
    item=next(r for r in registry.replacement_jobs(target.target_id) if r['replacement_id']==preview['replacement_id'])
    if item['input_hashes']!=target.input_hashes or item['backend']!=target.backend:
        raise CampaignStepBlocked('Replacement frozen input/backend mismatch')
    from account_config import load_saved_account_profiles,make_saved_profile_service
    from final_configuration import load_final_config
    from read_only_preflight import _instance_ref
    cfg=load_final_config(project)
    service=make_saved_profile_service(load_saved_account_profiles((item['account_label'],))[item['account_label']])
    active=_instance_ref(service.active_instance())
    if active!=item['metadata'].get('instance_ref') or active not in cfg['allowed_instance_refs'].get(item['account_label'],[]):
        raise CampaignStepBlocked('Registered provider instance is not authorized')
    job=service.job(item['job_id'])
    session=getattr(job,'session_id',None);session=session() if callable(session) else session
    if job.job_id()!=item['job_id'] or session!=item['batch_id'] or job.backend().name!=item['backend']:
        raise CampaignStepBlocked('Provider job, Batch or backend identity mismatch')
    inputs=job.inputs
    if not isinstance(inputs,dict) or not isinstance(inputs.get('pubs'),(list,tuple)) or len(inputs['pubs'])!=1:
        raise CampaignStepBlocked('Provider does not verify an exactly-one-PUB job')
    remote=str(job.status()).upper().split('.')[-1]
    from datetime import datetime,timezone
    result={'state':'OBSERVED','provider_status':remote,'job_id':item['job_id'],'replacement_id':item['replacement_id'],
        'checked_at':datetime.now(timezone.utc).isoformat(),'purpose':'RECOVERY','pub_count':1,'account':item['account_label'],'backend':item['backend']}
    if spec['action']=='monitor':return result
    if remote not in ('DONE','COMPLETED'):
        return result|{'state':'NOT_COLLECTED','reason':'Only verified completed jobs are collected. No retry or replacement.'}
    class CompletedRead:
        def job(self,jid):
            if jid!=item['job_id']:raise RuntimeError('Unregistered replacement ID requested')
            return self
        def __getattr__(self,name):return getattr(job,name)
        def result(self,*args,**kwargs):
            if str(job.status()).upper().split('.')[-1] not in ('DONE','COMPLETED'):raise RuntimeError('Replacement completion changed')
            return job.result(*args,**kwargs)
    from targeted_recovery import collect_replacement
    registry.readonly=False
    try:collected=collect_replacement(project,target,registry,item['replacement_id'],CompletedRead())
    finally:registry.readonly=True
    latest=next(r for r in registry.replacement_jobs(target.target_id) if r['replacement_id']==item['replacement_id'])
    from repository_paths import repository_root,MITIGATION_REL
    raw=Path(latest.get('raw_archive_path') or '')
    if not raw.is_absolute():raw=repository_root(project)/raw
    expected_root=repository_root(project)/MITIGATION_REL/'output/03_IBM_execution_records'
    if not raw.resolve().is_relative_to(expected_root.resolve()) or not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest()!=latest.get('raw_archive_sha256'):
        raise CampaignStepBlocked('Canonical collection archive is not verified')
    return result|{'state':latest['state'],'archive_verified':True,'validation':collected.get('state',latest['state'])}


def execute_campaign_step(project, campaign_id, operation, *, n=None, batch=None, single_pub=None, collect_scope_id=None, manual_scope=None,
                          authorize_step=False, authorize_output_write=False, registry_path=None, new_analysis_version=False):
    """One canonical non-submitting operation; never advances campaign checkpoints.

    Prepare on an existing campaign verifies its immutable preparation, never
    re-registers/resets it. Analyze includes the canonical numerical/plot artifact
    generation; Export packages that verified analysis in publication locations.
    Both reuse existing create-only/versioned publication behavior. No arbitrary
    output path, account, job, attempt, or service is accepted by this interface.
    Collect supports original, first-attempt eight-treatment Batches only; mixed/replacement scopes fail closed.
    """
    if authorize_step is not True:raise PermissionError('Explicit STEP authorization required')
    if type(new_analysis_version) is not bool or (new_analysis_version and operation!='analyze'):
        raise ValueError('A new analysis version is permitted only for an authorized Analyze STEP')
    if operation not in ('prepare','collect','single_pub','analyze','export','manual'):
        raise ValueError('Unsupported STEP: standalone validation/recovery require scoped canonical contracts')
    if campaign_id!='vqe-independent-n-01':raise ValueError('Unsupported campaign scope')
    if (operation=='prepare' and n is not None) or (operation!='prepare' and (type(n) is not int or n not in N_LEVELS)):
        raise ValueError('Prepare is campaign scoped; collect/analysis/export require one explicit n-level')
    if operation!='single_pub' and single_pub is not None:raise ValueError('Unexpected single-PUB scope')
    if operation!='manual' and manual_scope is not None:raise ValueError('Unexpected manual scope')
    if operation=='manual' and (not isinstance(manual_scope,dict) or manual_scope.get('action') not in ('monitor','collect')):raise ValueError('Explicit manual operation scope required')
    if operation=='manual' and manual_scope['action']=='collect' and authorize_output_write is not True:raise PermissionError('Explicit canonical output/registry write authorization required')
    if operation=='single_pub' and not isinstance(single_pub,dict):raise ValueError('Explicit single-PUB scope required')
    if operation!='collect' and batch is not None:raise ValueError('Batch scope is collect-only')
    if operation!='collect' and collect_scope_id is not None:raise ValueError('COLLECT scope is collect-only')
    if operation=='collect' and (type(batch) is not int or not 1<=batch<=6):raise ValueError('Explicit Batch sequence required')
    if operation in ('collect','single_pub','analyze','export') and authorize_output_write is not True:
        raise PermissionError('Explicit canonical STEP output/registry write authorization required')
    from independent_n_campaign import campaign_root, _sha, _campaign_has_unknown, dry_run_independent_campaign
    from worker_runtime import campaign_worker_lock
    from job_registry import JobRegistry
    from final_configuration import load_final_config,campaign_authorization_from_final_config
    database=_registry_path(project,registry_path)
    if not database.is_file():raise FileNotFoundError('Existing campaign registry required; STEP will not create it')
    with campaign_worker_lock(campaign_root(project,campaign_id)):
        registry=JobRegistry(database,readonly=True)
        status=registry.independent_campaign_status(campaign_id)
        if not status.get('exists'):raise KeyError('Existing campaign is not registered')
        manifest=status.get('manifest') or {}
        if _sha({k:v for k,v in manifest.items() if k!='manifest_sha256'})!=status.get('manifest_sha256') or manifest.get('manifest_sha256')!=status.get('manifest_sha256'):
            raise CampaignStepBlocked('Immutable campaign manifest hash mismatch')
        config=load_final_config(project)
        authorization=campaign_authorization_from_final_config(config)
        action=manual_scope['action'] if operation=='manual' else ('collect' if single_pub.get('action')=='collect' else 'monitor') if operation=='single_pub' else 'plot' if operation=='export' else operation
        if (manifest.get('backend')!=config['allowed_backend'] or not authorization['allowed_accounts']
                or action not in authorization['allowed_actions']):
            raise PermissionError('Canonical backend/account/action authorization mismatch')
        if _campaign_has_unknown(registry,status):raise CampaignStepBlocked('UNKNOWN submission reconciliation required; no STEP mutation permitted')
        checkpoint={k:status.get(k) for k in ('state','current_level_index','checkpoint')}
        levels=manifest.get('levels',[])
        if sorted(item['target_n'] for item in levels)!=list(N_LEVELS):raise CampaignStepBlocked('Campaign must contain exactly six n-levels')
        if operation=='prepare':
            for level in levels:
                target=_typed_target(level,project,registry)
                if registry.target_status(target.target_id).get('exists') is not True:
                    raise CampaignStepBlocked('Prepared target registration is missing; no automatic repair/reset permitted')
            report=dry_run_independent_campaign(project,campaign_id,registry)
            result={'state':'ALREADY_PREPARED','preparation_verified':True,'levels_verified':len(levels),
                    'planned_pubs':report['requested_pubs'],'new_campaign_created':False}
        elif operation=='manual':
            from manual_steps import execute_manual_scope
            result=execute_manual_scope(project,registry,status,authorization,n,manual_scope)
        elif operation=='single_pub':
            level=next(item for item in levels if item['target_n']==n)
            target=_typed_target(level,project,registry)
            result=_single_pub_read_step(project,registry,target,campaign_id,n,single_pub)
        elif operation=='collect':
            level=next(item for item in levels if item['target_n']==n)
            target=_typed_target(level,project,registry)
            result=_collect_campaign_step(project,registry,target,authorization,batch,
                **({'expected_scope':collect_scope_id} if collect_scope_id is not None else {}))
        else:
            level=next(item for item in levels if item['target_n']==n)
            target=_typed_target(level,project,registry)
            from targeted_recovery import repaired_dataset_status
            repaired=repaired_dataset_status(target,registry)
            rows=repaired['rows']
            if (not repaired['analysis_eligible'] or repaired['status'] not in ('COMPLETE','REPAIRED_COMPLETE')
                    or repaired.get('duplicate_slots') or len(rows)!=n*len(target.treatment_ids)
                    or any(repaired['counts_by_treatment'].get(t)!=n for t in target.treatment_ids)
                    or any(row['input_hashes']!=target.input_hashes for row in rows)):
                raise CampaignStepBlocked('Exactly n accepted, provenance-valid logical PUBs per treatment required')
            # Bind the handoff to the selected original/replacement records, not counts alone.
            dataset_digest=_sha(sorted(rows,key=lambda row:(row['treatment_id'],row['logical_pub_index'])))
            from repository_paths import repository_root,MITIGATION_REL,atomic_archive
            output_parent=(repository_root(project)/MITIGATION_REL/'output/02_run_level_data/adaptive_targets').resolve()
            from step_artifacts import latest_analysis_receipt, next_analysis_receipt
            receipt_dir=campaign_root(project,campaign_id)/'steps'
            receipt_path=latest_analysis_receipt(receipt_dir,n,dataset_digest)
            def checked_output(value):
                path=Path(value).resolve()
                if path.parent!=output_parent or not (path.name==target.target_id or path.name.startswith(target.target_id+'_v')):
                    raise RuntimeError('Unexpected canonical analysis output scope')
                return path
            if operation=='analyze':
                from adaptive_targets import analyze_target
                receipt_extra={}
                if new_analysis_version:
                    receipt_path,receipt_extra=next_analysis_receipt(receipt_dir,n,dataset_digest)
                analysis=analyze_target(project,target,registry,**({'new_version':True} if new_analysis_version else {}))
                if analysis.get('artifacts_verified') is not True:raise RuntimeError('Canonical analysis verification failed')
                output=checked_output(analysis['output'])
                receipt={'campaign_id':campaign_id,'n':n,'target_id':target.target_id,'target_manifest_sha256':target.sha256,
                         'input_hashes':target.input_hashes,'dataset_sha256':dataset_digest,'output':str(output),
                         'artifact_sha256':_step_artifact_hashes(output),**receipt_extra}
                # Repeating an ordinary Analyze may reuse the current versioned
                # receipt, but must never drop its immutable history fields.
                if not new_analysis_version and receipt_path.is_file():
                    previous=json.loads(receipt_path.read_text(encoding='utf-8'))
                    for key in ('receipt_version','previous_receipt'):
                        if key in previous:receipt[key]=previous[key]
                payload=json.dumps(receipt,sort_keys=True,allow_nan=False).encode('utf-8')
                atomic_archive(receipt_path,(payload,),hashlib.sha256(payload).hexdigest())
                result={'state':'ANALYZED','artifacts_verified':True,'already_complete':analysis.get('already_complete',False)}
            else:
                if not receipt_path.is_file() or receipt_path.stat().st_size>1024*1024:
                    raise CampaignStepBlocked('Verified STEP analysis receipt required; run Analyze n-level first')
                receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
                expected={'campaign_id':campaign_id,'n':n,'target_id':target.target_id,'target_manifest_sha256':target.sha256,
                          'input_hashes':target.input_hashes,'dataset_sha256':dataset_digest}
                if any(receipt.get(k)!=v for k,v in expected.items()):raise CampaignStepBlocked('Analysis receipt provenance mismatch')
                output=checked_output(receipt['output'])
                if _step_artifact_hashes(output)!=receipt['artifact_sha256']:raise CampaignStepBlocked('Analysis artifact hashes changed; export refused')
                from scientific_exports import export_n_level_results
                export=export_n_level_results(project,target,registry,{'output':str(output),'artifacts_verified':True})
                if export.get('files_verified') is not True:raise RuntimeError('Publication export verification failed')
                result={'state':'EXPORTED','files_verified':True,'already_exists':export.get('already_exists',False),
                        'figure_directory':export['figures'],'data_directory':export['data'],
                        'png_files':export['png_files'],'png_count':len(export['png_files']),
                        'export_manifest':str(Path(export['data'])/'export_manifest.json')}
            result['analysis_receipt']={'path':str(receipt_path),
                'sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest()}
            result['analysis_output']=str(output)
            result['output']=result.get('figure_directory',str(output))
        after=registry.independent_campaign_status(campaign_id)
        if {k:after.get(k) for k in checkpoint}!=checkpoint:raise RuntimeError('Campaign checkpoint changed during STEP')
        return {'operation':operation,'campaign_id':campaign_id,'n':n,'campaign_state':after['state'],
                'result':result,'campaign_progression_changed':False,'submission_performed':False}


def process_pipeline(project: str | Path, campaign_id: str, *, n: int | None=None,
                     registry_path: str | Path | None=None,
                     plan_replacements: bool=False,
                     replacement_account: str | None=None,
                     dry_run_replacements: bool=False,
                     reapprove_replacement_attempt: bool=False) -> dict[str,Any]:
    """Analyze only n-levels with exactly n validated PUBs for every treatment."""
    if n is not None and n not in N_LEVELS:
        raise ValueError(f"n must be one of {N_LEVELS}")
    if dry_run_replacements and not plan_replacements:
        raise ValueError("--dry-run-replacements requires --plan-replacements")
    if replacement_account and not plan_replacements:
        raise ValueError("--replacement-account requires --plan-replacements")
    if reapprove_replacement_attempt and not plan_replacements:
        raise ValueError("--reapprove-replacement-attempt requires --plan-replacements")
    database=_registry_path(project,registry_path)
    if not database.is_file():
        raise FileNotFoundError("No campaign registry exists; plan the campaign first")
    from independent_n_campaign import inspect_independent_campaign
    from job_registry import JobRegistry
    registry=JobRegistry(database,readonly=not plan_replacements or dry_run_replacements)
    campaign=registry.independent_campaign_status(campaign_id)
    if not campaign.get("exists"):
        raise KeyError(f"Unknown independent-n campaign: {campaign_id}")
    status=inspect_independent_campaign(registry,campaign_id,project)
    levels=[item for item in campaign["manifest"]["levels"] if n is None or int(item["target_n"])==n]
    results=[]
    for level in levels:
        target=_typed_target(level,project,registry)
        target_status=registry.target_status(target.target_id)
        from targeted_recovery import repaired_dataset_status
        repaired=repaired_dataset_status(target,registry)
        counts=repaired["counts_by_treatment"]
        blocks=target_status.get("blocks",[])
        complete=repaired["analysis_eligible"] and repaired["status"] in ("COMPLETE","REPAIRED_COMPLETE")
        if complete:
            from adaptive_targets import analyze_target
            report=analyze_target(project,target,registry)
            if report.get("artifacts_verified") is not True:
                raise RuntimeError(f"Paper output verification failed for n={target.target_n}")
            from scientific_exports import export_n_level_results
            export=export_n_level_results(project,target,registry,report)
            artifacts=_paper_artifacts(report["output"])
            analysis_state="COMPLETE_ANALYSIS" if repaired["status"]=="COMPLETE" else "REPAIRED_COMPLETE_ANALYSIS"
            results.append({"n":target.target_n,"experiment_id":target.experiment_id,
                "state":analysis_state,"dataset_status":repaired["status"],
                "output":report["output"],"paper_export":export,"artifacts_verified":True,
                "paper_artifacts":artifacts})
        else:
            item={"n":target.target_n,"experiment_id":target.experiment_id,
                "state":"NOT_READY","validated_pubs_by_treatment":counts,
                "required_pubs_per_treatment":target.target_n,
                "reason":"Complete-only policy: recovery must produce exactly n valid PUBs for every treatment before analysis."}
            if plan_replacements:
                from targeted_recovery import plan_invalid_pub_slots
                from final_configuration import load_final_config
                item["replacement_plan"]=plan_invalid_pub_slots(project,target,registry,
                    final_config=load_final_config(project),replacement_account=replacement_account,
                    dry_run=dry_run_replacements,
                    explicit_retry_authorization=reapprove_replacement_attempt)
            results.append(item)
    return {"campaign_id":campaign_id,"results":results,
        "completed_levels":sum(item["state"] in ("COMPLETE_ANALYSIS","REPAIRED_COMPLETE_ANALYSIS") for item in results),
        "campaign_progression_changed":False}


def status_pipeline(project: str | Path,campaign_id: str,*,registry_path: str | Path | None=None) -> dict[str,Any]:
    """Read campaign state without opening IBM services or mutating SQLite."""
    database=_registry_path(project,registry_path)
    if not database.is_file():
        return {"campaign_id":campaign_id,"exists":False,"state":"NOT_PLANNED"}
    from independent_n_campaign import inspect_independent_campaign
    from job_registry import JobRegistry
    report = inspect_independent_campaign(JobRegistry(database,readonly=True),campaign_id,project)
    if not report.get("exists", True):
        return report
    # Display-only: distinguish immutable historical authorization from active
    # final_config. No IBM access, quota selection, or Registry writes here.
    from final_configuration import load_final_config
    config = load_final_config(project)
    historical = list(report.get("execution_account_labels", []))
    active = list(config["enabled_account_labels"])
    automatic = config.get("account_selection") == "highest_available_quota"
    report["historical_accounts"] = historical
    report["active_accounts"] = active
    report["account_selection"] = config.get("account_selection", "fixed_by_n")
    report["live_submission_enabled"] = config["live_submission_enabled"]
    report["next_batch_account"] = "PENDING_QUOTA_SELECTION" if automatic else None
    for level in report.get("levels", []):
        if level.get("state") in ("PLANNED", "PENDING") and not level.get("blocks"):
            level["selected_account_label"] = None if automatic else config.get("account_by_n", {}).get(str(level["n"]))
            level["account_selection_pending"] = automatic
    # Derived display summary only; campaign state and original IBM Batch status remain unchanged.
    report["repaired_progress_summary"] = [
        {"n": level["n"], "valid": level.get("repaired_valid_pubs", 0),
         "expected": level.get("expected_pubs", 0),
         "complete": level.get("repaired_complete", False),
         "replacement_pubs": level.get("validated_replacement_pubs", 0)}
        for level in report.get("levels", [])
    ]
    return report


def _replacement_context(project: str | Path,campaign_id: str,n: int,treatment: str,pub_index: int,
                         registry_path: str | Path | None=None):
    """Resolve one already-planned logical PUB replacement without guessing IDs."""
    if n not in N_LEVELS or not 1<=pub_index<=n:
        raise ValueError("n and one-based --pub-index must identify a supported logical PUB slot")
    from job_registry import JobRegistry
    database=_registry_path(project,registry_path)
    if not database.is_file():raise FileNotFoundError("No independent-n registry exists")
    registry=JobRegistry(database)
    campaign=registry.independent_campaign_status(campaign_id)
    if not campaign.get("exists"):raise KeyError(f"Unknown independent-n campaign: {campaign_id}")
    level=next((item for item in campaign["manifest"]["levels"] if int(item["target_n"])==n),None)
    if level is None:raise KeyError(f"Campaign has no independent n={n} experiment")
    target=_typed_target(level,project,registry)
    if treatment not in target.treatment_ids:raise ValueError("Treatment must be one of the frozen M0-M7 treatment IDs")
    rows=[row for row in registry.replacement_jobs(target.target_id)
        if row["treatment_id"]==treatment and int(row["logical_pub_index"])==pub_index]
    if not rows:raise RuntimeError("No durable replacement plan exists for this logical PUB; run process --plan-replacements first")
    latest=max(rows,key=lambda row:int(row["attempt"]))
    if sum(int(row["attempt"])==int(latest["attempt"]) for row in rows)!=1:
        raise RuntimeError("Replacement attempts for this logical PUB are ambiguous in the registry")
    return registry,target,latest


def submit_replacement_pipeline(project: str | Path,campaign_id: str,n: int,treatment: str,pub_index: int,
        account_label: str,*,authorize_live_replacement: bool=False,confirm_live_submission: bool=False,
        registry_path: str | Path | None=None) -> dict[str,Any]:
    """Submit exactly one planned invalid PUB after fresh account/runtime gates."""
    from final_configuration import load_final_config,campaign_authorization_from_final_config
    from account_config import load_saved_account_profiles,make_saved_profile_service
    from read_only_preflight import preflight_account,_instance_ref
    from runtime_estimator import targeted_replacement_estimate as _targeted_replacement_runtime_estimate
    from experiment_manifest import build_manifest
    from targeted_recovery import submit_replacement_slot
    config=load_final_config(project)
    if not authorize_live_replacement or not confirm_live_submission:
        raise RuntimeError("Live replacement requires --authorize-live-replacement and --confirm-live-submission")
    if account_label not in config["enabled_account_labels"]:
        raise RuntimeError("Requested replacement account is not enabled in final_config.json")
    registry,target,item=_replacement_context(project,campaign_id,n,treatment,pub_index,registry_path)
    if item["state"]!="PLANNED" or item["job_id"] is not None:
        raise RuntimeError("Only an unsubmitted PLANNED replacement may be submitted; reconcile every other state")
    if item["account_label"]!=account_label:
        raise RuntimeError("Replacement plan account differs; select it with process --plan-replacements --replacement-account first")
    if item["backend"]!=config["allowed_backend"] or item["backend"]!="ibm_fez":
        raise RuntimeError("Replacement backend is not the configured ibm_fez backend")
    if not config["live_submission_enabled"]:
        raise RuntimeError("Live submission is disabled in final_config.json")
    profile=load_saved_account_profiles((account_label,))[account_label]
    service=make_saved_profile_service(profile)
    active=service.active_instance()
    instance_ref=_instance_ref(active)
    authorized_refs=config["allowed_instance_refs"].get(account_label,[])
    if not authorized_refs or instance_ref not in authorized_refs:
        raise RuntimeError("Saved profile active instance is outside this account's approved opaque instance scope")
    from vqe_adapter import verify_frozen_inputs
    fresh=preflight_account(profile,service_factory=lambda _:service,
        frozen_validator=lambda: {"source_run":"run_02","input_sha256":target.input_hashes,
            "verified_circuit_parameters":len(verify_frozen_inputs(project)[2])})
    if fresh.get("status")!="PASS" or not fresh.get("submission_eligible"):
        raise RuntimeError("Fresh read-only preflight did not pass all account, backend, quota, scope and pending-job checks")
    if fresh.get("configured_instance_ref")!=instance_ref:
        raise RuntimeError("Fresh preflight instance differs from the saved profile's active instance")
    runtime_manifest=build_manifest(project,n=5,replicate_blocks=1,shots=4096,experiment_id=target.experiment_id)
    estimate=_targeted_replacement_runtime_estimate(project,runtime_manifest,treatment,registry=registry,target_id=target.target_id)
    authorization=campaign_authorization_from_final_config(config)
    return submit_replacement_slot(project,target,registry,int(item["replacement_id"]),service,
        final_config=config,campaign_authorization=authorization,preflight_report=fresh,
        runtime_estimate=estimate,explicit_authorization=True,active_instance_ref=instance_ref,
        authorized_instance_refs=authorized_refs)


def collect_replacement_pipeline(project: str | Path,campaign_id: str,n: int,treatment: str,pub_index: int,
        account_label: str,*,registry_path: str | Path | None=None) -> dict[str,Any]:
    """Collect one existing replacement ID; never submits or retries a job."""
    from final_configuration import load_final_config
    from account_config import load_saved_account_profiles,make_saved_profile_service
    from read_only_preflight import _instance_ref
    from targeted_recovery import collect_replacement
    config=load_final_config(project)
    registry,target,item=_replacement_context(project,campaign_id,n,treatment,pub_index,registry_path)
    if item["account_label"]!=account_label:
        raise RuntimeError("Collection account must match the registered replacement account")
    service=None
    if not item.get("raw_archive_path"):
        profile=load_saved_account_profiles((account_label,))[account_label]
        service=make_saved_profile_service(profile)
        active_ref=_instance_ref(service.active_instance())
        expected_ref=item["metadata"].get("instance_ref")
        allowed=config["allowed_instance_refs"].get(account_label,[])
        if not expected_ref or active_ref!=expected_ref or active_ref not in allowed:
            raise RuntimeError("Replacement job's saved-profile instance does not match its recorded approved scope")
    return collect_replacement(project,target,registry,int(item["replacement_id"]),service)


def validate_replacement_pipeline(project: str | Path,campaign_id: str,n: int,treatment: str,pub_index: int,
        *,registry_path: str | Path | None=None) -> dict[str,Any]:
    """Revalidate the immutable one-PUB archive offline; no IBM service is opened."""
    from targeted_recovery import revalidate_replacement_archive,repaired_dataset_status
    registry,target,item=_replacement_context(project,campaign_id,n,treatment,pub_index,registry_path)
    validation=revalidate_replacement_archive(project,target,registry,int(item["replacement_id"]))
    return validation|{"repaired_dataset":repaired_dataset_status(target,registry),
        "ibm_connection":False,"submission_performed":False}


def main(argv=None) -> int:
    """Forward the retired process/slot-command CLI to the single mxene CLI."""
    from mxene import main as mxene_main
    return mxene_main(argv)


if __name__=="__main__":
    raise SystemExit(main())
