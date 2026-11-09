"""Operator-only orchestration of canonical IBM adapters; never imported by chat tools.

No scheduler or SDK submit loop lives here. Durable approvals are consumed once;
the existing scheduler/recovery modules own Runtime submission and reservations.
"""
import csv
import hashlib
import json
import math
import os
import re
import shlex
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import replace
from pathlib import Path

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[2]
CAMPAIGN='vqe-independent-n-01'
TREATMENTS=('M0_Baseline','M1_DD','M2_TREX','M3_ZNE','M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL')
OPERATIONS=('status','inspect','retrieve','list_jobs','batch_submit','pub_submit','recover','analyze','export')


def api():
    from auto_broker import modules
    return modules()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,default=str).encode()).hexdigest()


def frozen_preview(manifest):
    bundle=Path(manifest.source_bundle)
    for name in ('lock.json','theta.csv','observables.json'):
        if (bundle/name).stat().st_size>64000:raise ValueError('Frozen preview metadata exceeds the bounded size')
    lock=json.loads((bundle/'lock.json').read_text(encoding='utf-8'))
    with (bundle/'theta.csv').open(encoding='utf-8',newline='') as stream:theta=list(csv.DictReader(stream))
    return {'circuit':{k:lock.get(k) for k in ('source_run','circuit_qubits','unbound_parameter_count','isa_depth','isa_size','physical_layout_logical_order')},
        'theta_vector':theta,'mapped_observables':json.loads((bundle/'observables.json').read_text(encoding='utf-8'))}


def clean(value):
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {str(k):('[REDACTED]' if re.search('token|password|secret|credential|api.?key|crn',str(k),re.I) else clean(v)) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [clean(v) for v in value]
    if isinstance(value,str):return re.sub(r'(?i)(?:crn:v1:[^\s"\']+|bearer\s+\S+|(?:api[_ -]?key|token|password)\s*[:=]\s*\S+)','[REDACTED]',value)[:24000]
    return value if value is None or isinstance(value,(int,float,bool)) else str(value)


def validated(body):
    defaults={'operation':'status','account':'','backend':'ibm_fez','experiment':'VQE','circuit':'run_02',
              'n':5,'treatments':[],'pubs':[],'job_id':'','run_id':'','label':'','output':'manual_runs'}
    if not isinstance(body,dict) or set(body)-set(defaults):raise ValueError('Unknown manual request fields')
    request=defaults|body
    if request['operation'] not in OPERATIONS:raise ValueError('Unsupported manual operation')
    if request['experiment']!='VQE' or request['circuit']!='run_02':raise ValueError('Only the existing frozen VQE run_02 adapter is supported; no QAOA adapter is installed')
    if type(request['n']) is not int or request['n'] not in (5,10,15,20,25,30):raise ValueError('Select n=5,10,15,20,25,30')
    for name in ('treatments','pubs'):
        values=request[name]
        if not isinstance(values,list) or any(type(v) not in (str,int) for v in values) or len(values)!=len(set(values)):raise ValueError('Use unique '+name)
    if set(request['treatments'])-set(TREATMENTS):raise ValueError('Unknown treatment')
    if any(type(v) is not int or not 1<=v<=request['n'] for v in request['pubs']):raise ValueError('PUB repetitions must be within the selected n-level')
    for name,pattern in (('job_id',r'[A-Za-z0-9_-]{1,128}'),('run_id',r'[a-f0-9]{32}'),('account',r'lab[A-IK]'),('label',r'[A-Za-z0-9 _.-]{1,80}')):
        if not isinstance(request[name],str) or (request[name] and not re.fullmatch(pattern,request[name])):raise ValueError('Invalid '+name)
    if request['output']!='manual_runs':raise ValueError('Output must be the isolated manual_runs destination; arbitrary paths and overwrites are forbidden')
    if request['operation'] in ('status','inspect','retrieve','recover') and not request['job_id']:raise ValueError('An explicit IBM Job ID is required')
    if request['operation'] in ('analyze','export') and not request['run_id']:raise ValueError('Select a validated manual run ID')
    if request['operation'] in ('batch_submit','pub_submit'):
        if not request['label']:raise ValueError('Give the new independent execution a unique label')
        if not request['treatments']:raise ValueError('Select at least one treatment')
        if not request['pubs']:request['pubs']=list(range(1,request['n']+1))
        if request['operation']=='pub_submit' and len(request['treatments'])!=1:raise ValueError('An independent Runtime job has one treatment; use Batch for multiple treatments')
    if request['operation']=='recover' and (len(request['treatments'])!=1 or len(request['pubs'])!=1):raise ValueError('Recovery requires one treatment and one exact logical PUB')
    return request


def parse_console(command,fields=None):
    if not isinstance(command,str) or len(command)>1000:raise ValueError('Command is too long')
    words=shlex.split(command,posix=True)
    if words==['help']:return {'help':['help','list jobs','job status <job_id>','job retrieve <job_id>','job inspect <job_id>','batch submit','pub submit','pub recover','analyze <manual_run_id>','export <manual_run_id>']}
    mappings={('list','jobs'):'list_jobs',('batch','submit'):'batch_submit',('pub','submit'):'pub_submit',('pub','recover'):'recover'}
    body=dict(fields or {})
    if tuple(words) in mappings:body['operation']=mappings[tuple(words)]
    elif len(words)==3 and words[0]=='job' and words[1] in ('status','retrieve','inspect'):body.update(operation=words[1],job_id=words[2])
    elif len(words)==2 and words[0] in ('analyze','export'):
        # A validated issued run ID is the path capability; no shell/file evaluation.
        body.update(operation=words[0],run_id=words[1])
    else:raise ValueError('Unknown command. Use help; shell and Python execution are not supported.')
    return validated(body)


class ManualExecution:
    def __init__(self,project=PROJECT,state=None):
        self.project=Path(project);self.state=Path(state or HERE/'state/manual_runs')
        self.state.mkdir(parents=True,exist_ok=True)
        self.database=self.state/'control.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, request TEXT, fingerprint TEXT, state TEXT, preview TEXT, expires REAL, result TEXT, created REAL)')

    def db(self):
        connection=sqlite3.connect(self.database,timeout=5);connection.row_factory=sqlite3.Row
        # sqlite connection context commits but does not close; callers use closing below.
        return _Database(connection)

    def get(self,key):
        if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Invalid manual request ID')
        with self.db() as db:row=db.execute('SELECT * FROM requests WHERE id=?',(key,)).fetchone()
        if row is None:raise ValueError('Manual request not found')
        return {**dict(row),'request':json.loads(row['request']),'preview':json.loads(row['preview']), 'result':json.loads(row['result'] or '{}')}

    def update(self,key,state,result=None):
        with self.db() as db:db.execute('UPDATE requests SET state=?,result=? WHERE id=?',(state,json.dumps(clean(result or {}),allow_nan=False),key))

    def configuration(self):
        _,_,_,config,_=api();cfg=config.load_final_config(self.project)
        with self.db() as db:
            recent=[{'request_id':r['id'],'state':r['state'],'operation':json.loads(r['request'])['operation'],
                'label':json.loads(r['request']).get('label',''),'created':r['created']} for r in db.execute('SELECT * FROM requests ORDER BY created DESC LIMIT 25')]
        return {'accounts':cfg['enabled_account_labels'],'backends':[cfg['allowed_backend']],'experiments':['VQE'],
                'circuit':'Frozen Fixed ISA run_02; 21 angles; physical mapping and mapped observables unchanged',
                'treatments':list(TREATMENTS),'operations':list(OPERATIONS),'output':str(self.state),
                'quota':{'new_submission':150.0,'targeted_recovery':40.0},'auto_required':False,'recent_runs':recent}

    def registry(self,readonly=True):
        *_,registry=api()
        return registry.JobRegistry(registry.JobRegistry.default_database(self.project),readonly=readonly)

    def policy(self,request):
        _,_,_,config,_=api();cfg=config.load_final_config(self.project)
        if request['account'] not in cfg['enabled_account_labels']:raise ValueError('Account is not authorized in final_config.json')
        if request['backend']!=cfg['allowed_backend']:raise ValueError('Backend is outside the configured authorized scope')
        if not cfg['allowed_instance_refs'].get(request['account']):raise ValueError('Account has no approved instance scope')
        if request['operation'] in ('batch_submit','pub_submit','recover') and not cfg['live_submission_enabled']:raise ValueError('Live submission is disabled by existing policy')
        return cfg

    def source(self,request,registry,allow_account_change=False):
        """Resolve canonical provenance; never infer arbitrary job compatibility."""
        originals=[row for row in registry.rows() if row.get('job_id')==request['job_id']]
        with registry._connection() as db:
            targets=[row[0] for row in db.execute('SELECT target_id FROM experiment_targets')]
        replacements=[row for target in targets for row in registry.replacement_jobs(target) if row.get('job_id')==request['job_id']]
        if len(originals)+len(replacements)>1:raise ValueError('Ambiguous/grouped Job ID; use existing canonical advanced collection for this job')
        if originals or replacements:
            row=(originals or replacements)[0]
            if (not allow_account_change and row['account_label']!=request['account']) or row['backend']!=request['backend']:raise ValueError('Job account/backend differs from recorded provenance')
            return ('original' if originals else 'replacement'),row
        return 'external',None

    def target(self,target_id):
        from experiment_manifest import TargetManifest
        if target_id.startswith('manual-'):
            key=target_id.removeprefix('manual-')
            record=self.get(key)
            return TargetManifest(**record['result']['target'])
        pipeline,*_=api();registry=self.registry();status=registry.independent_campaign_status(CAMPAIGN)
        level=next((l for l in status.get('levels',[]) if l['target_id']==target_id),None)
        if level is None:raise ValueError('No supported input manifest for this registered job')
        return pipeline._typed_target(level,self.project,registry)

    def recovery_candidate(self,request,registry):
        from targeted_recovery import plan_invalid_pub_slots
        kind,source=self.source(request,registry,allow_account_change=True)
        if kind!='original' or not source.get('target_id'):raise ValueError('Recovery requires a registered original with verified scientific provenance')
        target=self.target(source['target_id'])
        logical=request['pubs'][0]
        if target.target_id.startswith('manual-'):
            labels=source['metadata'].get('pub_labels',[])
            if logical not in labels:raise ValueError('Selected repetition does not belong to the original manual job')
            logical=labels.index(logical)+1
        elif target.target_n!=request['n']:raise ValueError('Selected n-level differs from original job provenance')
        if source['treatment_id']!=request['treatments'][0]:raise ValueError('Selected treatment does not match the original job')
        if any(v['treatment_id']==request['treatments'][0] and v['logical_pub_index']==logical for v in registry.repaired_pub_values(target.target_id)):
            raise ValueError('This logical PUB already has an accepted result; replacement refused')
        report=plan_invalid_pub_slots(self.project,target,registry,final_config=self.policy(request),replacement_account=None,dry_run=True)
        candidates=[p for p in report['plans'] if p['original_job_id']==request['job_id'] and p['logical_pub_index']==logical and p['treatment_id']==request['treatments'][0]]
        if len(candidates)!=1:raise ValueError('No single verified invalid/missing PUB candidate; retrieve/validate original results first')
        candidate=candidates[0]
        attempts=[r for r in registry.replacement_jobs(target.target_id) if r['treatment_id']==request['treatments'][0] and r['logical_pub_index']==logical]
        if attempts:
            latest=max(attempts,key=lambda r:r['attempt'])
            candidate.update(state=latest['state'],replacement_id=latest['replacement_id'],replacement_job_id=latest.get('job_id'))
        if candidate['state'] not in ('DRY_RUN','PLANNED','FAILED'):raise ValueError('Replacement is pending, UNKNOWN, or already submitted; reconcile it instead of retrying')
        return target,source,candidate

    def preview(self,body):
        request=validated(body);cfg=self.policy(request)
        from experiment_manifest import build_manifest
        manifest=build_manifest(self.project,n=len(request['pubs']) or 1) if request['operation'] in ('batch_submit','pub_submit','recover','analyze','export') else None
        view={'request':request,'input_manifest':manifest.to_dict() if manifest else None,'pub_count':len(request['pubs'])*len(request['treatments']),
              'output_root':str(self.state),'overwrite':False,'provider_checked':False,'quota_verified':False,
              'required_quota_seconds':40.0 if request['operation']=='recover' else 150.0 if request['operation'] in ('batch_submit','pub_submit') else None,
              'authorization_digest':digest(cfg),'notes':'No submission. Preflight and direct confirmation are required. AUTO is not started.'}
        if manifest:
            view['frozen_inputs']=frozen_preview(manifest)
            view['reference_manifest_note']='The reference manifest lists all eight available treatments; execution_scope is the exact authorized selection.'
            view['execution_scope']={'treatments':request['treatments'],'pub_labels':request['pubs'],
                'pub_count':view['pub_count'],'jobs':len(request['treatments']),
                'mode':'standalone Runtime job' if request['operation']=='pub_submit' else request['operation']}
        if request['operation']=='recover':
            target,source,candidate=self.recovery_candidate(request,self.registry())
            view['recovery']=candidate;view['source_input_hashes']=target.input_hashes
        if request['operation'] in ('analyze','export'):
            previous=self.get(request['run_id'])
            if not previous['result'].get('validated_artifact'):raise ValueError('Run has no validated compatible scientific artifact')
            view['source_artifact']=previous['result']['validated_artifact']
        key=uuid.uuid4().hex;view['request_id']=key;view['scope_id']=digest(view)
        fingerprint=digest({'request':request,'recovery':view.get('recovery')})
        with self.db() as db:
            if request['operation'] in ('batch_submit','pub_submit','recover') and db.execute("SELECT 1 FROM requests WHERE fingerprint=? AND (state IN ('QUEUED','RUNNING','SUCCEEDED','UNKNOWN') OR (state='READY' AND expires>?))",(fingerprint,time.time())).fetchone():raise ValueError('Duplicate execution scope exists. Inspect the existing run; do not submit it again.')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',(key,json.dumps(request),fingerprint,'PREVIEW',json.dumps(view),0,'{}',time.time()))
        return view

    def service(self,request):
        from account_config import load_saved_account_profiles,make_saved_profile_service
        from read_only_preflight import _instance_ref
        cfg=self.policy(request);profile=load_saved_account_profiles((request['account'],))[request['account']]
        service=make_saved_profile_service(profile)
        if _instance_ref(service.active_instance()) not in cfg['allowed_instance_refs'][request['account']]:raise ValueError('Saved account active instance is not authorized')
        return service,profile,cfg

    def lock(self):
        _,campaign,runtime,_,_=api()
        return runtime.campaign_worker_lock(campaign.campaign_root(self.project,CAMPAIGN))

    def fresh(self,request):
        from read_only_preflight import preflight_account,_instance_ref
        from vqe_adapter import verify_frozen_inputs
        from quota import report_quota_fields,provider_reservations,available_quota_seconds
        from final_configuration import quota_eligibility
        service,profile,cfg=self.service(request)
        registry=self.registry()
        if any(row['submission_state'] in ('UNKNOWN','SUBMITTING','RESERVED') for row in registry.rows()):raise ValueError('Unresolved submission intents require canonical reconciliation before new execution')
        with registry._connection() as db:
            if db.execute("SELECT 1 FROM pub_replacement_jobs WHERE state IN ('UNKNOWN','SUBMITTING','RESERVED') LIMIT 1").fetchone():raise ValueError('UNKNOWN replacement must be reconciled before submission')
        report=preflight_account(profile,service_factory=lambda _:service,allowed_instance_refs=cfg['allowed_instance_refs'][request['account']],frozen_validator=lambda:{'source_run':'run_02','verified_circuit_parameters':len(verify_frozen_inputs(self.project)[2])})
        if report.get('status')!='PASS' or not report.get('submission_eligible'):raise ValueError('Provider preflight failed: account/backend/input/pending-job checks must all pass')
        if report.get('configured_instance_ref')!=_instance_ref(service.active_instance()):raise ValueError('Instance changed during preflight')
        usage=report_quota_fields(report);reservation=provider_reservations(registry)[request['account']]
        if reservation['ambiguous']:raise ValueError('Quota reservations are ambiguous')
        available=available_quota_seconds(provider_remaining_seconds=usage['remaining_seconds'],outstanding_reservations_seconds=reservation['reserved_seconds'],local_budget_remaining_seconds=max(0,cfg['runtime_budget_seconds_per_account']-usage['consumed_seconds']))
        gate=quota_eligibility(available,workload_type='targeted_replacement' if request['operation']=='recover' else 'original_batch',provider_remaining_seconds=usage['remaining_seconds'])
        if not gate['eligible']:raise ValueError('Insufficient verified quota: '+ '; '.join(gate['reasons']))
        return service,cfg,report,gate

    def check_preview(self,row):
        request=row['request'];cfg=self.policy(request)
        from experiment_manifest import build_manifest
        current=build_manifest(self.project,n=len(request['pubs']) or 1).to_dict() if row['preview']['input_manifest'] else None
        if digest(cfg)!=row['preview']['authorization_digest'] or digest(current)!=digest(row['preview']['input_manifest']):
            raise ValueError('Authorization or frozen inputs changed; create a new preview')
        if request['operation']=='recover':
            _,_,candidate=self.recovery_candidate(request,self.registry())
            if digest(candidate)!=digest(row['preview']['recovery']):raise ValueError('Recovery evidence changed; preview the latest exact replacement scope')

    def preflight(self,key,scope):
        row=self.get(key)
        if row['state'] not in ('PREVIEW','READY') or row['preview']['scope_id']!=scope:raise ValueError('Invalid or consumed preview')
        with self.lock():
            self.check_preview(row)
            if row['request']['operation'] in ('batch_submit','pub_submit','recover'):
                with self.db() as db:
                    if db.execute("SELECT 1 FROM requests WHERE fingerprint=? AND id<>? AND (state IN ('QUEUED','RUNNING','SUCCEEDED','UNKNOWN') OR (state='READY' AND expires>?))",(row['fingerprint'],key,time.time())).fetchone():raise ValueError('Duplicate manual execution already exists')
            if row['request']['operation'] in ('batch_submit','pub_submit','recover'):
                _,_,report,gate=self.fresh(row['request']);result={'quota':gate,'checked_at':report.get('checked_utc')}
            else:result={'quota':'Not applicable: this operation does not submit jobs'}
            with self.db() as db:
                if db.execute("UPDATE requests SET state='READY',expires=?,result=? WHERE id=? AND state IN ('PREVIEW','READY')",(time.time()+300,json.dumps(result),key)).rowcount!=1:
                    raise ValueError('Preview was consumed during preflight; nothing dispatched')
        return {'request_id':key,'scope_id':scope,'state':'READY','preflight':result,'expires_in_seconds':300}

    def authorize(self,key,scope,approved):
        if approved is not True:raise ValueError('Direct operator confirmation is required')
        row=self.get(key)
        if row['preview']['scope_id']!=scope:raise ValueError('Confirmation scope mismatch')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if row['request']['operation'] in ('batch_submit','pub_submit','recover') and db.execute("SELECT 1 FROM requests WHERE fingerprint=? AND id<>? AND state IN ('QUEUED','RUNNING','SUCCEEDED','UNKNOWN')",(row['fingerprint'],key)).fetchone():raise ValueError('Duplicate dispatch is forbidden')
            if db.execute("UPDATE requests SET state='QUEUED' WHERE id=? AND state='READY' AND expires>?",(key,time.time())).rowcount!=1:raise ValueError('Expired, consumed, or failed preflight; nothing dispatched')
        return {'request_id':key,'state':'QUEUED'}

    def execute(self,key):
        row=self.get(key)
        with self.lock():
            with self.db() as db:
                if db.execute("UPDATE requests SET state='RUNNING' WHERE id=? AND state='QUEUED' AND expires>?",(key,time.time())).rowcount!=1:raise ValueError('No unconsumed live authorization')
            try:
                self._submission_attempted=False
                self._active_key=key
                self.check_preview(row)
                result=self.perform(key,row['request'])
                self.update(key,'BLOCKED' if result.get('state')=='PLANNED' else 'SUCCEEDED',result)
            except Exception as exc:
                # Conservatively retain interrupted submissions as UNKNOWN, never replay.
                result={'error_type':type(exc).__name__,'message':clean(str(exc)) if isinstance(exc,(ValueError,FileExistsError)) else 'Operation failed. Inspect recorded job identities before retrying.', 'automatic_retry':False}
                self.update(key,'UNKNOWN' if self._submission_attempted else 'BLOCKED',self.get(key)['result']|result)
                raise
        return result

    def perform(self,key,request):
        operation=request['operation']
        if operation in ('batch_submit','pub_submit'):return self.submit(key,request)
        if operation=='recover':return self.recover(request)
        if operation in ('analyze','export'):return self.analyze(key,request)
        return self.read_job(key,request)

    def live_grant(self):
        row=self.get(self._active_key)
        if row['state']!='RUNNING' or row['expires']<=time.time():raise ValueError('Live authorization expired before submission')

    def submit(self,key,request):
        from experiment_manifest import build_manifest,build_target_manifest
        from runtime_estimator import estimate_runtime
        from vqe_adapter import prepare_block
        from batch_scheduler import submit_runtime_block
        service,cfg,report,gate=self.fresh(request)
        count=len(request['pubs']);block='manual-'+key
        manifest=build_manifest(self.project,n=count,experiment_id=block)
        target=replace(build_target_manifest(self.project,target_n=5),experiment_id=block,target_id=block,
            target_n=count,chunk_n=count,blocks_required=1,treatment_ids=tuple(request['treatments']),
            treatments=tuple(t for t in manifest.treatments if t['id'] in request['treatments']),jobs_per_block=len(request['treatments']),
            pubs_per_block_per_treatment=count,total_jobs=len(request['treatments']),total_requested_pubs=count*len(request['treatments']))
        # No empirical samples are fabricated; estimates are explicitly unavailable.
        estimate=estimate_runtime([],treatments=TREATMENTS,target_n=count)
        registry=self.registry(False)
        from vqe_adapter import record_directory
        if record_directory(self.project,block).exists():raise FileExistsError('Manual destination already exists; refusing overwrite')
        prepare_block(self.project,block,n=count,shots=4096)
        self.update(key,'RUNNING',{'target':target.to_dict(),'block_id':block,'pub_labels':request['pubs']})
        self.live_grant()
        self._submission_attempted=True
        jobs=submit_runtime_block(manifest,registry,service,logical_block_id=block,account_label=request['account'],instance=service.active_instance(),
            reservation_seconds_per_job=gate['reservation_seconds'],runtime_estimate=estimate,authorize_live=True,preflight_verified=True,
            quota_budget_seconds=cfg['runtime_budget_seconds_per_account'],safety_margin_seconds=cfg['safety_margin_seconds'],root=self.project,
            adaptive_target_context={'target_id':block,'target_n':count,'chunk_n':count,'manifest_sha256':target.sha256,'account_label':request['account'],
                'runtime_upper_bound_seconds':None,'safety_margin_seconds':cfg['safety_margin_seconds'],'preflight_checked_at':report['checked_utc'],'quota_scope':'account_wide_verified'},
            manual_selection={'treatments':request['treatments'],'pub_labels':request['pubs'],'mode':'job' if operation_is_job(request) else 'batch'})
        return {'target':target.to_dict(),'block_id':block,'job_ids':jobs,'pub_labels':request['pubs'],'registered_in_campaign':False,'quota':gate,'runtime_estimate':'Unavailable; not a measured runtime bound'}

    def recover(self,request):
        from targeted_recovery import submit_replacement_slot
        from runtime_estimator import targeted_replacement_estimate
        from vqe_adapter import treatment_options
        from read_only_preflight import _instance_ref
        _,_,_,configuration,_=api()
        service,cfg,report,_=self.fresh(request);registry=self.registry(False)
        target,source,candidate=self.recovery_candidate(request,registry)
        job=service.job(request['job_id']) if source['account_label']==request['account'] else self.service(request|{'account':source['account_label']})[0].job(request['job_id'])
        if str(job.status()).upper().split('.')[-1] not in ('DONE','COMPLETED','ERROR','FAILED','CANCELLED','CANCELED'):raise ValueError('Original job is pending or UNKNOWN; replacement refused')
        options=treatment_options(source['treatment_id'],target.shots_per_pub,self.project)
        options=options.model_dump(mode='json') if hasattr(options,'model_dump') else {'canonical_serialized_options':str(options)}
        entry=registry.plan_pub_replacement(target_id=target.target_id,logical_block_id=source['logical_block_id'],treatment_id=source['treatment_id'],
            source_pub_index=candidate['source_pub_index'],logical_pub_index=candidate['logical_pub_index'],options=options,allow_new_attempt=True,
            replacement_account_label=request['account'],metadata={'source_run':'run_02','source_job_id':source['job_id'],'source_account_label':source['account_label'],
                'source_archive_path':candidate['source_archive_path'],'source_archive_sha256':candidate['source_archive_sha256'],
                'shots_per_pub':4096,'physical_qubits':list(target.physical_qubits),'input_hashes':target.input_hashes})
        self.live_grant()
        self._submission_attempted=True
        return submit_replacement_slot(self.project,target,registry,entry['replacement_id'],service,final_config=cfg,
            campaign_authorization=configuration.campaign_authorization_from_final_config(cfg),preflight_report=report,
            runtime_estimate=targeted_replacement_estimate(self.project,target,source['treatment_id'],registry=registry,target_id=target.target_id),
            explicit_authorization=True,active_instance_ref=_instance_ref(service.active_instance()),authorized_instance_refs=cfg['allowed_instance_refs'][request['account']])

    def read_job(self,key,request):
        from vqe_adapter import capture_runtime_result,collect_block_for_recovery
        service,profile,_=self.service(request);registry=self.registry()
        if request['operation']=='list_jobs':
            return {'jobs':[{'job_id':job.job_id(),'status':str(job.status())} for job in service.jobs(limit=25)],'limit':25}
        job=service.job(request['job_id'])
        if job.job_id()!=request['job_id'] or job.backend().name!=request['backend']:raise ValueError('Provider Job ID/backend mismatch')
        status=str(job.status()).upper().split('.')[-1]
        result={'job_id':request['job_id'],'account':request['account'],'backend':request['backend'],'status':status,'observed_at':time.time()}
        for name in ('metrics','error_message','logs'):
            try:result[name]=clean(getattr(job,name)())
            except Exception:result[name]='Unavailable'
        kind,source=self.source(request,registry);result['provenance']=kind
        if kind=='original':
            from batch_scheduler import verify_reconciliation_job
            verify_reconciliation_job(source,job,service,root=self.project)
        if source:result['registry_state']=source.get('submission_state',source.get('state'))
        if request['operation']!='retrieve':return result
        if status not in ('DONE','COMPLETED','ERROR','FAILED','CANCELLED','CANCELED'):raise ValueError('Job is pending or UNKNOWN; retrieval is deferred')
        # One bounded provider result fetch shared by canonical collection and diagnostics.
        job=_CachedJob(job);service=_JobService(service,request['job_id'],job)
        registry.readonly=False  # Existing schema only; no startup migration for a read.
        if kind=='replacement':
            from targeted_recovery import collect_replacement
            result['collection']=collect_replacement(self.project,self.target(source['target_id']),registry,source['replacement_id'],service)
        elif kind=='original':
            result['collection']=collect_block_for_recovery(self.project,source['logical_block_id'],treatments=[source['treatment_id']],shots=4096,
                n=source.get('chunk_n') or source['n'],account_label=request['account'],ibm_account=profile.label,
                instance=service.active_instance(),registry=registry,service_override=service,job_row_id=source['id'])
        else:result['campaign_import']='Not performed; external jobs are not assumed to be MXene experiments'
        # Separate, immutable diagnostic archive for every accessible result.
        captured=capture_runtime_result(job.result(),'Manual retrieval')
        folder=self.state/key;folder.mkdir(exist_ok=False)
        artifact=folder/'pubs.json';payload={'job_id':request['job_id'],'account':request['account'],'backend':request['backend'],'kind':kind,'captured':captured,
            'input_hashes':source.get('input_hashes') if source else None,'source_row':source['id'] if kind=='original' else None}
        encoded=json.dumps(clean(payload),allow_nan=False).encode('utf-8')
        if len(encoded)>16_000_000:raise ValueError('Result exceeds bounded diagnostic archive size')
        artifact.write_bytes(encoded)
        result.update(archive=str(artifact),sha256=hashlib.sha256(encoded).hexdigest(),pub_validation={k:v for k,v in captured.items() if k not in ('records','pub_metadata')},pub_examples=captured['records'][:5])
        expected=(1 if kind=='replacement' else source.get('chunk_n') or source.get('n')) if source else None
        if source and not captured['invalid_expectation_pubs'] and not captured['invalid_uncertainty_pubs'] and captured['pub_count']==expected:
            result['validated_artifact']={'path':str(artifact),'sha256':result['sha256'],'kind':kind}
        return result

    def analyze(self,key,request):
        """Reuse canonical per-PUB EIS calculation; export only to a new run folder."""
        import numpy as np
        from vqe_adapter import load_pilot,verify_frozen_inputs
        from repository_paths import repository_root
        row=self.get(request['run_id']);reference=row['result'].get('validated_artifact')
        if not reference:raise ValueError('No compatible validated artifact')
        path=Path(reference['path']).resolve()
        if not path.is_relative_to(self.state.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=reference['sha256']:raise ValueError('Validated input changed or is outside manual storage')
        payload=json.loads(path.read_text(encoding='utf-8'))
        from experiment_manifest import build_manifest
        if payload['input_hashes']!=build_manifest(self.project,n=1).input_hashes:raise ValueError('Frozen-input provenance mismatch')
        pilot=load_pilot(self.project);pilot.register_science(repository_root(self.project))
        from impedance_model import read_eis_file
        frequency,impedance=read_eis_file(str(self.project/'data/EIS14.csv'))
        rows=[]
        for record in payload['captured']['records']:
            if record['expectation_validation']!='PASS':raise ValueError('Invalid PUB cannot be analyzed')
            evs=record.get('expectation_values',record.get('evs'))
            parameters,curve,sse,rmse=pilot.calculate_pub_eis(evs,np.asarray(frequency),np.asarray(impedance))
            rows.append({'job_id':payload['job_id'],'pub':record['pub_index'],'SSE':float(sse),'RMSE':float(rmse),**dict(parameters)})
        folder=self.state/key;folder.mkdir(exist_ok=False)
        with (folder/'metrics.csv').open('x',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        with (folder/'provenance.json').open('x',encoding='utf-8') as stream:json.dump({'source':reference,'job_id':payload['job_id'],'method':'Existing mitigation_pilot.calculate_pub_eis'},stream)
        if request['operation']=='export':
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            fig,axes=plt.subplots(1,2,figsize=(9,4))
            for axis,metric in zip(axes,('SSE','RMSE')):
                axis.plot([r['pub'] for r in rows],[r[metric] for r in rows],'o');axis.set(xlabel='PUB',ylabel=metric,title='Recorded '+metric)
            fig.tight_layout();fig.savefig(folder/'manual_metrics.png',dpi=300);plt.close(fig)
        return {'rows':rows,'output':str(folder),'source':reference,'scientific_function':'mitigation_pilot.calculate_pub_eis','overwrite':False}


def operation_is_job(request):return request['operation']=='pub_submit'


class _CachedJob:
    def __init__(self,job):self.job=job;self.loaded=False;self.error=None
    def __getattr__(self,name):return getattr(self.job,name)
    def result(self,*args,**kwargs):
        if not self.loaded:
            try:self.value=self.job.result(*args,**({'timeout':120}|kwargs))
            except Exception as exc:self.error=exc
            self.loaded=True
        if self.error:raise self.error
        return self.value


class _JobService:
    def __init__(self,service,key,job):self.service=service;self.key=key;self.cached=job
    def __getattr__(self,name):return getattr(self.service,name)
    def job(self,key):return self.cached if key==self.key else self.service.job(key)


class _Database:
    def __init__(self,connection):self.connection=connection
    def __enter__(self):return self.connection
    def __exit__(self,*args):
        try:self.connection.rollback() if args[0] else self.connection.commit()
        finally:self.connection.close()
