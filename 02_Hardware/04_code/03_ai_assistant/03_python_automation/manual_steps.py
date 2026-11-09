"""Scope resolution for execute_campaign_step; collection stays in canonical delegates."""
import re


def resolve(project, registry, status, authorization, n, scope):
    from research_pipeline import (CampaignStepBlocked, _typed_target, _collect_local_scope,
        _collect_scope_receipt, preview_single_pub)
    from independent_n_campaign import _sha
    fields={'kind','action','block_id','job_id','treatment','slot','account','scope_id'}
    if set(scope)-fields or scope.get('kind') not in ('batch','job','pub','external') or scope.get('action') not in ('monitor','collect'):
        raise CampaignStepBlocked('Unsupported manual scope; no operation authorized')
    selection={k:v for k,v in scope.items() if k!='scope_id' and v is not None}
    if scope['action'] not in authorization['allowed_actions']:raise CampaignStepBlocked('Manual action is not authorized')
    level=next((l for l in status['manifest']['levels'] if l['target_n']==n),None)
    if level is None:raise CampaignStepBlocked('Requested n-level is not registered')
    target=_typed_target(level,project,registry)
    jobs=[];route={};proof={}
    if scope['kind']=='external':
        if scope['action']!='monitor':raise CampaignStepBlocked('External import BLOCKED: input, circuit, observables, theta/ISA, treatment, slot mapping and attempt provenance are not verified. Read-only inspection only.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',scope.get('job_id') or '') or scope.get('account') not in authorization['allowed_accounts']:
            raise CampaignStepBlocked('External inspection requires an explicit Job ID and authorized saved account')
        jobs=[{'job_id':scope['job_id'],'account_label':scope['account'],'backend':target.backend}]
        route={'external':True};proof=jobs
    else:
        blocks=registry.target_status(target.target_id).get('blocks',[])
        originals=[r for r in registry.rows() if r.get('target_id')==target.target_id and r.get('job_id')==scope.get('job_id')]
        replacements=[r for r in registry.replacement_jobs(target.target_id) if r.get('job_id')==scope.get('job_id')]
        if scope['kind'] in ('job','pub') and replacements:
            if originals or len(replacements)!=1:raise CampaignStepBlocked('Grouped or ambiguous recovery job is outside the verified single-replacement contract')
            item=replacements[0]
            if scope.get('slot') not in (None,item['logical_pub_index']) or scope.get('treatment') not in (None,item['treatment_id']):
                raise CampaignStepBlocked('Selected logical slot/treatment differs from replacement lineage')
            preview=preview_single_pub(project,status['campaign_id'],n,'RECOVERY',item['treatment_id'],item['logical_pub_index'],item['account_label'],registry_path=registry.database)
            if preview['state']!='RECORDED' or preview.get('job_id')!=item['job_id']:
                raise CampaignStepBlocked(preview.get('reason','Recovery attempt is not eligible'))
            route={'replacement':{'purpose':'RECOVERY','action':scope['action'],'treatment':item['treatment_id'],
                'slot':item['logical_pub_index'],'account':item['account_label'],'scope_id':preview['scope_id']}}
            proof=preview['scope_id'];jobs=[{'job_id':item['job_id'],'treatment_id':item['treatment_id'],'attempt':item['attempt'],'account_label':item['account_label']}]
        else:
            if scope['kind']=='batch':
                matches=[b for b in blocks if b['logical_block_id']==scope.get('block_id')]
                job_id=None
            else:
                if len(originals)!=1:raise CampaignStepBlocked('Job ID is not one unambiguous registered original or supported recovery attempt')
                original=originals[0];job_id=original['job_id']
                matches=[b for b in blocks if b['logical_block_id']==original['logical_block_id']]
            if len(matches)!=1:raise CampaignStepBlocked('Registered Batch identity is missing or ambiguous')
            batch=matches[0]['sequence_no']
            block,identities,_=_collect_local_scope(project,registry,target,authorization,batch,job_id=job_id)
            if scope['kind']=='pub':
                first=(batch-1)*target.chunk_n+1
                if type(scope.get('slot')) is not int or not first<=scope['slot']<first+target.chunk_n or scope.get('treatment')!=original['treatment_id']:
                    raise CampaignStepBlocked('Logical PUB slot is not mapped to the selected original Job')
            proof=_collect_scope_receipt(target,batch,block,identities)
            jobs=proof['jobs'];route={'batch':batch,'job_id':job_id,'block':block}
    receipt={'campaign_id':status['campaign_id'],'n':n,'scope':selection,'jobs':jobs,
        'scope_id':_sha({'campaign':status['campaign_id'],'n':n,'selection':selection,'proof':proof,'inputs':target.input_hashes}),
        'state':'READY','reason':'Existing executions only. No submission, cancellation or automatic recovery.',
        'collection_unit':'Full parent Job result is archived and all its PUBs validated once; selected logical slot is reported separately.' if scope['kind']=='pub' else 'Registered execution'}
    return target,route,receipt


def preview_manual_scope(project,campaign_id,n,scope):
    from research_pipeline import _registry_path,CampaignStepBlocked
    from job_registry import JobRegistry
    from independent_n_campaign import _sha,_campaign_has_unknown
    from final_configuration import load_final_config,campaign_authorization_from_final_config
    if campaign_id!='vqe-independent-n-01':raise CampaignStepBlocked('This broker is pinned to vqe-independent-n-01; other campaign execution is not authorized')
    registry=JobRegistry(_registry_path(project),readonly=True);status=registry.independent_campaign_status(campaign_id)
    if not status.get('exists'):raise CampaignStepBlocked('Campaign is not registered')
    manifest=status['manifest']
    if _sha({k:v for k,v in manifest.items() if k!='manifest_sha256'})!=status['manifest_sha256']:raise CampaignStepBlocked('Campaign manifest mismatch')
    if _campaign_has_unknown(registry,status):raise CampaignStepBlocked('UNKNOWN submissions require canonical reconciliation before manual execution')
    cfg=load_final_config(project)
    if manifest['backend']!=cfg['allowed_backend']:raise CampaignStepBlocked('Backend authorization mismatch')
    return resolve(project,registry,status,campaign_authorization_from_final_config(cfg),n,scope)[2]


def execute_manual_scope(project,registry,status,authorization,n,scope):
    from research_pipeline import CampaignStepBlocked,_collect_campaign_step,_single_pub_read_step
    target,route,preview=resolve(project,registry,status,authorization,n,scope)
    if scope.get('scope_id')!=preview['scope_id']:raise CampaignStepBlocked('Manual execution scope changed; request a fresh confirmation')
    if route.get('external'):
        from account_config import load_saved_account_profiles,make_saved_profile_service
        from read_only_preflight import _instance_ref
        service=make_saved_profile_service(load_saved_account_profiles((scope['account'],))[scope['account']])
        if _instance_ref(service.active_instance()) not in authorization['allowed_instance_refs'].get(scope['account'],[]):raise CampaignStepBlocked('External account instance is not authorized')
        job=service.job(scope['job_id'])
        if job.job_id()!=scope['job_id'] or job.backend().name!=target.backend:raise CampaignStepBlocked('External Job ID/backend mismatch')
        from datetime import datetime,timezone
        remote=str(job.status()).upper().split('.')[-1]
        result={'state':'OBSERVED_EXTERNAL','job_ids_inspected':[scope['job_id']],'provider_statuses':{scope['job_id']:remote},
            'provider_checked_at':datetime.now(timezone.utc).isoformat(),'collected_jobs':0,'accepted_pubs':0,
            'import_status':'BLOCKED: scientific provenance and logical mapping unverified; no campaign import or archival'}
    elif route.get('replacement'):
        before=next(r for r in registry.replacement_jobs(target.target_id) if r['job_id']==scope['job_id'])
        result=_single_pub_read_step(project,registry,target,status['campaign_id'],n,route['replacement'])
        latest=next(r for r in registry.replacement_jobs(target.target_id) if r['job_id']==scope['job_id'])
        result.update(job_ids_inspected=[scope['job_id']],collected_jobs=int(scope['action']=='collect' and before['state']!='VALIDATED' and result.get('archive_verified',False)),
            already_archived_jobs=int(before['state']=='VALIDATED' and result.get('archive_verified',False)),valid_pubs=int(result.get('validation')=='VALIDATED'),
            invalid_pubs=int(result.get('archive_verified',False) and result.get('validation')!='VALIDATED'),
            recovery_required=bool(result.get('archive_verified',False) and result.get('validation')!='VALIDATED'),
            archives=[latest['raw_archive_path']] if result.get('archive_verified') else [],
            provider_statuses={scope['job_id']:result['provider_status']},provider_checked_at=result['checked_at'])
    else:
        result=_collect_campaign_step(project,registry,target,authorization,route['batch'],job_id=route['job_id'],monitor_only=scope['action']=='monitor')
        if scope['kind']=='pub' and scope['action']=='collect':
            index=(scope['slot']-1)%target.chunk_n+1
            row=next(r for r in registry.rows(route['block']) if r['job_id']==scope['job_id'])
            values=[r for r in registry.pub_validation_records(target.target_id) if r['job_row_id']==row['id'] and r['pub_index']==index]
            result['selected_pub']={'logical_slot':scope['slot'],'treatment':scope['treatment'],'records':values,
                'parent_result_preserved':True}
    # Unknown scientific counts stay unknown during inspection; DONE never means collected.
    for field in ('valid_pubs','invalid_pubs','missing_pubs','recovery_required','already_archived_jobs'):
        result.setdefault(field,None)
    result.setdefault('archives',[])
    if 'status_counts' not in result:
        normalized={'COMPLETED':'DONE','QUEUED':'PENDING','INITIALIZING':'PENDING','VALIDATING':'PENDING','ERROR':'FAILED','CANCELLED':'FAILED','CANCELED':'FAILED'}
        counts=dict.fromkeys(('DONE','PENDING','RUNNING','FAILED','UNKNOWN'),0)
        for value in result.get('provider_statuses',{}).values():
            label=normalized.get(value,value);counts[label if label in counts else 'UNKNOWN']+=1
        result['status_counts']=counts
    return result|{'scope':preview['scope'],'scope_id':preview['scope_id'],'campaign_id':status['campaign_id'],'n':n}
