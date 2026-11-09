"""Explicit opt-in AUTO broker and fixed canonical-worker wrapper.

No scheduler, IBM SDK calls, account selection, or campaign retry loop. Run this
separately from the restricted web server. Never starts a campaign at startup.
"""
import os, sys
for _key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[_key]='1'
sys.dont_write_bytecode=True
import errno, hashlib, hmac, json, re, secrets, sqlite3, subprocess, time
from contextlib import contextmanager, closing
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[2]
PRODUCTION=HERE.parent/'03_ai_assistant'
CAMPAIGN='vqe-independent-n-01'
STATE=HERE/'state'
PYTHON=PROJECT/'.venv/Scripts/python.exe'
SCRIPT=Path(__file__).resolve()
READ_STEPS=('preflight','monitor005')
CANONICAL_STEPS=('prepare','collect','single_pub','analyze','export','manual')
STEPS=READ_STEPS+CANONICAL_STEPS

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
def key(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('Invalid execution identity')
    return value
def atomic(path,value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump(value,f,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)
def modules():
    location=str(PRODUCTION/'03_python_automation')
    if location not in sys.path:sys.path.insert(0,location)
    import research_pipeline, independent_n_campaign, worker_runtime, final_configuration, job_registry
    return research_pipeline,independent_n_campaign,worker_runtime,final_configuration,job_registry
def psutil_module():
    site=str(Path(sys.base_prefix)/'Lib/site-packages')
    if site not in sys.path:sys.path.append(site)
    import psutil
    return psutil

def preflight_step(project):
    """One canonical provider-read operation, no registry or report writer.

    Called only inside the broker-owned canonical campaign locks. Restrict the
    account/instance scope to canonical authorization; never choose an account.
    Keep provider exception text, credentials and instance identifiers out of UI.
    """
    import math
    _,_,_,config,_=modules()
    value=config.load_final_config(project)
    authorization=config.campaign_authorization_from_final_config(value)
    labels=authorization['allowed_accounts']
    if not labels or value['allowed_backend']!='ibm_fez':
        raise ValueError('No authorized accounts or unsupported canonical preflight backend')
    from read_only_preflight import preflight_all
    reports=preflight_all(tuple(labels),auth_mode='saved-profile',root=project,
                          allowed_instance_refs=authorization['allowed_instance_refs'])
    rows=[]
    for label in labels:
        report=reports.get(label,{})
        remaining=report.get('remaining_provider_quota_seconds')
        if type(remaining) not in (float,int) or not math.isfinite(remaining):remaining=None
        rows.append({'account':label,'status':report.get('status') if report.get('status') in ('PASS','WARN','FAIL') else 'UNAVAILABLE',
                     'provider_remaining_seconds':remaining,
                     'checks':', '.join(k+': '+v for k,v in report.get('checks',{}).items()
                         if isinstance(k,str) and re.fullmatch('[a-z_]{1,64}',k) and v in ('PASS','WARN','FAIL'))})
    return {'answer':'Preflight completed once. PASS/WARN/FAIL are diagnostic checks, not LIVE authorization. No campaign advancement or submission occurred.',
            'rows':rows,'observed_at':time.time(),'control_state':'STEP_COMPLETED'}

def monitor005_step(project):
    """Reuse the existing fixed-scope diagnostic; no SDK or execution loop here."""
    import importlib.util, io
    from contextlib import redirect_stdout
    path=project/'manager_diagnostics/ibm_batch_005_readonly.py'
    spec=importlib.util.spec_from_file_location('mxene_registered_batch_diagnostic',path)
    diagnostic=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    output=io.StringIO()
    with redirect_stdout(output):code=diagnostic.main(['--project',str(project)])
    answer=output.getvalue()
    if len(answer)>32768:raise ValueError('Diagnostic exceeded bounded report size')
    return {'answer':answer,'rows':[],'observed_at':time.time(),
            'control_state':'STEP_COMPLETED' if code==0 else 'STEP_FAILED'}

def probe(path):
    """Probe existing lock bytes without creating/deleting/writing lock files."""
    try:f=path.open('rb')
    except FileNotFoundError:return 'FREE'
    except OSError:return 'UNKNOWN'
    with f:
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
                msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                fcntl.flock(f.fileno(),fcntl.LOCK_UN)
            return 'FREE'
        except OSError as exc:
            return 'BUSY' if exc.errno in (errno.EACCES,errno.EAGAIN) else 'UNKNOWN'

def ownership(receipt,process,locks,foreign=False):
    """Pure fail-closed classification; liveness alone never proves lock ownership."""
    if foreign or 'UNKNOWN' in locks:return 'UNKNOWN'
    if not receipt:
        return 'STOPPED_VERIFIED' if process is None and locks==['FREE','FREE'] else 'UNKNOWN'
    if process:
        identity=all(process.get(k)==receipt.get(k) for k in ('pid','created','exe','argv','campaign'))
        return 'RUNNING_VERIFIED' if identity and receipt.get('state')=='LOCKED' and locks==['BUSY','BUSY'] else 'UNKNOWN'
    if locks!=['FREE','FREE']:return 'UNKNOWN'
    return 'STOPPED_VERIFIED' if receipt.get('state')=='EXITED' else 'STALE_UNRESOLVED'

class Broker:
    def __init__(self,state=STATE,project=PROJECT):
        self.state=Path(state);self.project=Path(project)
        self.receipt_path=self.state/'auto_worker.json'
        self.launch_path=self.state/'auto_launch.json'
        self.children={}
    def completed_step_result(self,receipt):
        """Read bounded durable evidence; missing/corrupt output is not success."""
        if not receipt or receipt.get('operation') not in STEPS or receipt.get('state') not in ('EXITED','CRASHED'):
            return None
        path=self.state/('step-'+key(receipt['request'])+'.json')
        if path.stat().st_size>65536:raise ValueError('Oversized STEP report')
        result=json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(result,dict) or not isinstance(result.get('answer'),str)
                or not isinstance(result.get('rows'),list)
                or any(not isinstance(row,dict) for row in result['rows'])
                or result.get('control_state') not in ('STEP_COMPLETED','STEP_FAILED')
                or result['control_state']!=receipt.get('outcome')):
            raise ValueError('Inconsistent STEP completion evidence')
        return result
    def step_observation(self, receipt, launch, worker):
        """Report only the latest launch, never a previous operation's success."""
        latest=launch if launch and (not receipt or launch.get('request')!=receipt.get('request')) else receipt
        if not latest or latest.get('operation') not in STEPS:return {}
        summary={k:latest.get(k) for k in ('request','operation','n','batch','state','outcome')}
        if launch and launch.get('request')==latest.get('request'):
            summary.update({k:launch.get(k) for k in ('n','batch')})
        result=None;error=False;reason=None
        if latest is not receipt:
            status='BLOCKED';reason='Latest launch has no matching worker receipt yet; do not repeat the operation.'
        elif worker=='RUNNING_VERIFIED':status='RUNNING'
        elif receipt.get('state') in ('EXITED','CRASHED'):
            try:result=self.completed_step_result(receipt)
            except (OSError,ValueError,KeyError):error=True
            if error:
                status='BLOCKED';reason=receipt.get('failure_reason') or 'STEP report is missing, unreadable or inconsistent; reconcile its durable result.'
            elif receipt.get('state')=='CRASHED':
                status='FAILED';reason=result.get('failure_reason') or result['answer']
            else:
                status=('SUCCEEDED' if result['control_state']=='STEP_COMPLETED' else
                        'BLOCKED' if result.get('operation_status')=='BLOCKED' else 'FAILED')
                if status!='SUCCEEDED':reason=result.get('failure_reason') or result['answer']
        else:
            status='BLOCKED';reason='Worker completion or ownership is unverified; no retry is authorized.'
        return {'operation_status':status,'operation_receipt':summary,'step_result':result,
                'failure_reason':reason,'step_report_error':error}
    def policy(self):
        pipeline,campaign,_,config,registry=modules()
        value=config.load_final_config(self.project)
        authorization=config.campaign_authorization_from_final_config(value)
        ledger=registry.JobRegistry(registry.JobRegistry.default_database(self.project),readonly=True)
        current=ledger.independent_campaign_status(CAMPAIGN)
        # Keep status bounded: canonical AUTO performs full frozen-input and
        # accepted-result validation before deciding any submission. Do not scan
        # every raw archive merely to refresh process status.
        manifest=current.get('manifest') or {}
        manifest_ok=(current.get('exists') and campaign._sha({k:v for k,v in manifest.items() if k!='manifest_sha256'})
                     ==current.get('manifest_sha256')==manifest.get('manifest_sha256'))
        # Include code identity; changed implementation requires a fresh approval.
        files=[SCRIPT,HERE/'worker_supervisor.py',PRODUCTION/'final_config.json']
        diagnostic=self.project/'manager_diagnostics/ibm_batch_005_readonly.py'
        if diagnostic.is_file():files.append(diagnostic)
        files+=list((PRODUCTION/'03_python_automation').glob('*.py'))
        hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        return {'backend':value['allowed_backend'],'accounts':authorization['allowed_accounts'],
                'campaign_state':current.get('state'),
                'authorization_identity':digest({'authorization':authorization,'hashes':hashes,'frozen_manifest':current.get('manifest_sha256')}),
                'checkpoint':digest({'state':current.get('state'),'index':current.get('current_level_index'),'checkpoint':current.get('checkpoint')}),
                'unknown_submission':campaign._campaign_has_unknown(ledger,current),
                'ready':bool(value['live_submission_enabled'] and authorization['allowed_accounts'] and manifest_ok
                             and manifest.get('backend')==value['allowed_backend']),
                'complete':current.get('state')=='COMPLETE' or int(current.get('current_level_index',0))>=6,
                'quota_implications':f"Original Batch threshold 150 s; replacement 40 s. Fresh provider quota, reservations and per-account cap {value['runtime_budget_seconds_per_account']:g} s remain canonical gates. Estimates are diagnostic only."}
    def inspect(self):
        for request,process in list(self.children.items()):
            if process.poll() is not None:self.children.pop(request,None)
        _,campaign,_,_,_=modules()
        directory=campaign.campaign_root(self.project,CAMPAIGN)
        locks=[probe(directory.parent/'auto_manager.lock'),probe(directory/'worker.lock')]
        try:
            receipt=json.loads(self.receipt_path.read_text()) if self.receipt_path.exists() else None
            launch=json.loads(self.launch_path.read_text()) if self.launch_path.exists() else None
            if any(value is not None and not isinstance(value,dict) for value in (receipt,launch)):
                raise ValueError('Invalid receipt')
        except (OSError,ValueError):
            # Windows atomic replacement/AV may briefly deny sharing. An unreadable
            # receipt is not evidence of a stopped worker; do not clear or retry it.
            return {'worker':'UNKNOWN','ownership_verified':False,'can_execute':False,
                    'unknown_submission':None,'reason':'Worker receipt is unreadable or invalid; no operation authorized. Refresh status after the file is readable.'}
        foreign=False;process=None
        psutil=psutil_module()
        for p in psutil.process_iter():
            try:
                if 'python' not in p.name().lower():continue
                argv=p.cmdline();cwd=Path(p.cwd()).resolve()
                relevant=any(str(PRODUCTION).casefold() in arg.casefold() for arg in argv) or cwd.is_relative_to(PRODUCTION)
                relevant=relevant or any((cwd/arg).resolve().is_relative_to(PRODUCTION) for arg in argv[1:] if arg.lower().endswith('.py'))
                managed=len(argv)==5 and argv[1:3]==['-B',str(SCRIPT)] and argv[3]=='--worker'
                if managed and receipt and argv[4]==receipt.get('request'):
                    if p.pid==receipt.get('pid'):
                        process={'pid':p.pid,'created':p.create_time(),'exe':str(Path(p.exe()).resolve()),'argv':argv,'campaign':CAMPAIGN}
                        # A receipt cannot authorize a different executable. venv
                        # launchers may use the base interpreter recorded by pyvenv.cfg.
                        allowed={str(PYTHON.resolve()).casefold()}
                        cfg=PYTHON.parent.parent/'pyvenv.cfg'
                        for line in cfg.read_text().splitlines():
                            if line.lower().startswith('home = '):allowed.add(str((Path(line.split('=',1)[1].strip())/'python.exe').resolve()).casefold())
                        if process['exe'].casefold() not in allowed:foreign=True
                    elif p.pid not in receipt.get('ancestors',[]):foreign=True
                elif managed or relevant:foreign=True
            except psutil.NoSuchProcess:continue
            except (psutil.AccessDenied,OSError):foreign=True
        worker=ownership(receipt,process,locks,foreign)
        if launch and (not receipt or receipt.get('request')!=launch.get('request')):worker='STALE_UNRESOLVED'
        policy=self.policy()
        verified=worker in ('RUNNING_VERIFIED','STOPPED_VERIFIED')
        observation=self.step_observation(receipt,launch,worker)
        report_error=observation.get('step_report_error',False)
        return {'worker':worker,'ownership_verified':verified,
                'supported_operations':['start','resume','pause',*STEPS],
                'operation':(launch or receipt or {}).get('operation'),**observation,
                'can_execute':verified and not report_error,'unknown_submission':policy['unknown_submission'],
                'policy':policy,'owner':receipt.get('request') if receipt else None,
                'reason':('Completed STEP report is missing, unreadable or inconsistent. Reconcile its durable result before further control; no report or lock was cleared.' if report_error else
                          'Canonical lock/process identity verified.' if verified else 'Lock, process or interrupted launch identity requires operator reconciliation; nothing will be cleared automatically.')}
    def grant(self,request_id):
        with closing(sqlite3.connect(self.state/'worker_control.sqlite3')) as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM requests WHERE id=?',(key(request_id),)).fetchone()
        if row is None or row['state']!='DISPATCHING' or row['expires']<time.time():raise ValueError('Explicit unexpired Dashboard authorization required')
        command=json.loads(row['request'])
        if command.get('campaign')!=CAMPAIGN or command.get('operation') not in ('start','resume','pause',*STEPS):
            raise ValueError('Unsupported fixed campaign operation')
        if command['operation'] in ('collect','single_pub','analyze','export','manual'):
            if type(command.get('n')) is not int or command['n'] not in (5,10,15,20,25,30):raise ValueError('One explicit n-level required')
        elif command.get('n') is not None:raise ValueError('Unexpected n-level scope')
        if command['operation']=='collect':
            if type(command.get('batch')) is not int or not 1<=command['batch']<=command['n']//5:raise ValueError('Explicit registered Batch scope required')
            if not re.fullmatch('[a-f0-9]{64}',command.get('collect_scope_id') or ''):raise ValueError('Confirmed eight-job COLLECT preview required')
        elif command.get('batch') is not None:raise ValueError('Unexpected Batch scope')
        if command['operation']!='collect' and command.get('collect_scope_id') is not None:raise ValueError('Unexpected COLLECT scope')
        if command['operation']=='single_pub':
            from worker_supervisor import SinglePubScope
            spec=SinglePubScope.model_validate(command.get('single_pub'))
            if spec.purpose!='RECOVERY' or spec.slot>command['n']:raise ValueError('Invalid single-PUB read scope')
        elif command.get('single_pub') is not None:raise ValueError('Unexpected single-PUB scope')
        if command['operation']=='manual':
            from worker_supervisor import ManualScope
            manual=ManualScope.model_validate(command.get('manual_scope'))
            if not manual.scope_id:raise ValueError('Confirmed manual scope required')
        elif command.get('manual_scope') is not None:raise ValueError('Unexpected manual scope')
        return dict(row),command
    def dispatch(self,request_id):
        row,command=self.grant(request_id)
        evidence=self.inspect()
        if digest(evidence)!=row['evidence']:raise ValueError('Ownership or campaign authorization changed; request fresh approval')
        if row['expires']<time.time():raise ValueError('Authorization expired during verification')
        op=command['operation']
        if not evidence['ownership_verified'] or not evidence.get('can_execute'):
            raise ValueError('Unverified ownership or incomplete STEP evidence')
        if evidence['worker']=='RUNNING_VERIFIED' and (op in STEPS or evidence.get('operation') in STEPS):
            raise ValueError('An exclusive worker is active; no concurrent STEP, AUTO attach or pause allowed')
        with closing(sqlite3.connect(self.state/'broker_dispatch.sqlite3')) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY,operation TEXT NOT NULL)')
            db.execute('INSERT INTO grants VALUES(?,?)',(request_id,op))
        if op=='pause':
            if evidence['worker']!='RUNNING_VERIFIED':raise ValueError('No verified worker to pause')
            _,campaign,_,_,registry=modules()
            result=campaign.control_independent_campaign(self.project,CAMPAIGN,
                    registry.JobRegistry(registry.JobRegistry.default_database(self.project)),action='pause')
            return {'answer':'Cooperative pause requested. Wait for canonical checkpoint acknowledgement; remote jobs are not cancelled.',
                    'control_state':result['state']}
        if evidence['worker']=='RUNNING_VERIFIED':return {'answer':'Attached to existing verified worker.','control_state':'ATTACHED'}
        if evidence['worker']!='STOPPED_VERIFIED' or (op not in STEPS and (not evidence['policy']['ready'] or evidence['policy']['complete'] or evidence['unknown_submission'])):
            raise ValueError('Canonical authorization, completeness or UNKNOWN gate blocks launch')
        if op in CANONICAL_STEPS and evidence['unknown_submission']:raise ValueError('UNKNOWN reconciliation required before canonical STEP')
        if self.launch_path.exists() and json.loads(self.launch_path.read_text()).get('request')==request_id:
            raise ValueError('Execution grant already dispatched')
        atomic(self.launch_path,{'request':request_id,'operation':op,'n':command.get('n'),'batch':command.get('batch'),'collect_scope_id':command.get('collect_scope_id'),'single_pub':command.get('single_pub'),'manual_scope':command.get('manual_scope'),'policy':evidence['policy'],'created':time.time()})
        argv=[str(PYTHON),'-B',str(SCRIPT),'--worker',key(request_id)]
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',OMP_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
        with (self.state/('auto-'+request_id+'.log')).open('ab') as log:
            self.children[request_id]=subprocess.Popen(argv,cwd=self.project,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                shell=False,close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        return {'answer':('One canonical STEP requested asynchronously. Use Show step result to read completion. No automatic advancement.' if op in STEPS else 'Canonical AUTO launch requested asynchronously. Check worker status for lock acquisition; no job submission is assumed.'), 'control_state':'START_REQUESTED'}

def worker(request_id):
    """The only worker command; wraps canonical locks with ownership observation."""
    broker=Broker();launch=json.loads(broker.launch_path.read_text())
    if launch['request']!=key(request_id) or time.time()-launch['created']>120:raise ValueError('Stale or mismatched execution grant')
    with closing(sqlite3.connect(STATE/'worker_control.sqlite3')) as db:
        row=db.execute('SELECT state,request FROM requests WHERE id=?',(request_id,)).fetchone()
    if not row or row[0] not in ('DISPATCHING','START_REQUESTED') or json.loads(row[1]).get('operation')!=launch['operation'] or json.loads(row[1]).get('n')!=launch.get('n') or json.loads(row[1]).get('batch')!=launch.get('batch') or json.loads(row[1]).get('collect_scope_id')!=launch.get('collect_scope_id') or json.loads(row[1]).get('single_pub')!=launch.get('single_pub') or json.loads(row[1]).get('manual_scope')!=launch.get('manual_scope'):
        raise ValueError('Worker has no corresponding explicit authorization')
    # Replayed child commands serialize on a Dashboard-only grant lock; only one
    # can consume it, even if the canonical worker has not reached its lock yet.
    with closing(sqlite3.connect(STATE/'broker_dispatch.sqlite3')) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO workers VALUES(?)',(request_id,))
    # The web request may already have recorded START_REQUESTED. The durable
    # launch receipt can only be created by the broker after checking DISPATCHING.
    pipeline,campaign,runtime,_,_=modules()
    policy=broker.policy()
    if policy!=launch['policy'] or (launch['operation'] not in STEPS and (not policy['ready'] or policy['complete'] or policy['unknown_submission'])):
        raise ValueError('Campaign gates changed before worker startup')
    psutil=psutil_module();process=psutil.Process()
    identity={'request':request_id,'operation':launch['operation'],'n':launch.get('n'),'batch':launch.get('batch'),'manual_scope':launch.get('manual_scope'),'pid':process.pid,'created':process.create_time(),
              'exe':str(Path(process.exe()).resolve()),'argv':process.cmdline(),'campaign':CAMPAIGN,
              'ancestors':[p.pid for p in process.parents()]}
    original=runtime.campaign_worker_lock
    @contextmanager
    def observed_lock(directory):
        if Path(directory).resolve()!=campaign.campaign_root(PROJECT,CAMPAIGN).resolve():raise ValueError('Unexpected campaign lock')
        with original(directory):
            if launch['operation'] in CANONICAL_STEPS and broker.policy()!=launch['policy']:
                raise ValueError('Canonical STEP evidence changed before exclusive lock acquisition')
            # Canonical OS locks acquired first; this adds observation only.
            atomic(broker.receipt_path,dict(identity,state='LOCKED'))
            try:yield
            finally:atomic(broker.receipt_path,dict(identity,state='RELEASING'))
    runtime.campaign_worker_lock=observed_lock
    try:
        if launch['operation'] in CANONICAL_STEPS:
            try:
                result=pipeline.execute_campaign_step(PROJECT,CAMPAIGN,launch['operation'],n=launch.get('n'),**({'batch':launch.get('batch'),**({'collect_scope_id':launch['collect_scope_id']} if launch.get('collect_scope_id') else {})} if launch['operation']=='collect' else {'single_pub':launch.get('single_pub')} if launch['operation']=='single_pub' else {'manual_scope':launch.get('manual_scope')} if launch['operation']=='manual' else {}),
                    authorize_step=True,authorize_output_write=launch['operation'] in ('collect','single_pub','analyze','export') or (launch['operation']=='manual' and launch['manual_scope']['action']=='collect'))
                verified=result['result'].get('archive_and_ledger_verified',True)
                destination=(f" {result['result']['png_count']} PNGs verified in {result['result']['figure_directory']}."
                             if result['operation']=='export' and result['result'].get('figure_directory') else '')
                result={'answer':f"Canonical {result['operation']} returned: {result['result']['state']}. Campaign state: {result['campaign_state']}; no advancement or submission."+destination,
                        'rows':[result['result']],'control_state':'STEP_COMPLETED' if verified else 'STEP_FAILED'}
            except pipeline.CampaignStepBlocked as exc:
                result={'answer':'Canonical STEP blocked: '+str(exc),'rows':[],'control_state':'STEP_FAILED',
                        'operation_status':'BLOCKED','failure_reason':str(exc)}
            atomic(STATE/('step-'+request_id+'.json'),result)
            state=result['control_state']
        elif launch['operation'] in READ_STEPS:
            with runtime.campaign_worker_lock(campaign.campaign_root(PROJECT,CAMPAIGN)):
                if broker.policy()!=launch['policy']:raise ValueError('Policy changed before exclusive STEP')
                result=(preflight_step if launch['operation']=='preflight' else monitor005_step)(PROJECT)
                atomic(STATE/('step-'+request_id+'.json'),result)
            state=result['control_state']
        else:
            result=pipeline.run_pipeline(PROJECT,CAMPAIGN,mode='live',authorize_live_campaign=True,
                confirm_live_submission=True,ai_mode='python',once=False,resume=launch['operation']=='resume',poll_seconds=60)
            state=result.get('pipeline',{}).get('state','UNKNOWN')
        atomic(broker.receipt_path,dict(identity,state='EXITED',outcome=state))
    except BaseException as exc:
        # An abnormal exit must never be automatically restarted.
        # Provider exceptions may contain account/instance credentials. Local
        # analysis/export failures can retain their actionable filesystem reason.
        reason=(f'{type(exc).__name__}: {str(exc)[:2000]}' if launch['operation'] in ('prepare','analyze','export')
                else f'{type(exc).__name__}: operation failed; inspect the local worker log for details')
        if launch['operation'] in STEPS:
            atomic(STATE/('step-'+request_id+'.json'),{'answer':'Canonical STEP failed: '+reason,
                'rows':[],'control_state':'STEP_FAILED','operation_status':'FAILED','failure_reason':reason})
        atomic(broker.receipt_path,dict(identity,state='CRASHED',outcome='STEP_FAILED',failure_reason=reason))
        raise
    finally:runtime.campaign_worker_lock=original

def serve():
    _,_,runtime,_,_=modules()
    STATE.mkdir(exist_ok=True)
    with runtime.WorkerLock(STATE/'auto_broker.lock'):
        token_path=STATE/'auto_broker.token'
        from broker_client import save_token,read_token
        if not token_path.exists() or not token_path.read_text(encoding='ascii').startswith('dpapi:'):
            save_token(token_path,secrets.token_hex(32))
        token=read_token(token_path)
        broker=Broker()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def handle_request(self):
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Origin') or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                    self.send_error(403);return
                try:
                    if self.command=='GET' and self.path=='/health':result={'service':'mxene-broker','provider_queried':False,'automation_started':False}
                    elif self.command=='GET' and self.path=='/status':result=broker.inspect()
                    elif self.command=='POST' and self.path=='/manual-operation':
                        size=int(self.headers.get('Content-Length','0'))
                        if not 1<=size<=16000:raise ValueError('Invalid manual operation size')
                        result=manual_operation(json.loads(self.rfile.read(size)))
                    elif self.command=='POST' and self.path=='/manual-preview':
                        size=int(self.headers.get('Content-Length','0'))
                        if not 1<=size<=4096:raise ValueError('Invalid preview size')
                        body=json.loads(self.rfile.read(size))
                        if set(body)!={'campaign','n','scope'}:raise ValueError('Invalid manual preview request')
                        from worker_supervisor import ManualScope
                        scope=ManualScope.model_validate(body['scope']).model_dump(exclude_none=True)
                        modules()
                        from manual_steps import preview_manual_scope
                        try:result=preview_manual_scope(PROJECT,body['campaign'],body['n'],scope)
                        except (RuntimeError,ValueError,OSError) as exc:result={'state':'BLOCKED','reason':str(exc)}
                    elif self.command=='POST' and self.path=='/collect-preview':
                        size=int(self.headers.get('Content-Length','0'))
                        if not 1<=size<=256:raise ValueError('Invalid preview size')
                        body=json.loads(self.rfile.read(size))
                        if set(body)!={'n','batch'}:raise ValueError('Invalid COLLECT preview scope')
                        pipeline,*_=modules()
                        try:result=pipeline.preview_collect_step(PROJECT,CAMPAIGN,**body)
                        except (RuntimeError,ValueError,OSError) as exc:
                            result={'answer':str(exc),'control_state':'BLOCKED'}
                    elif self.command=='POST' and self.path=='/single-pub-preview':
                        size=int(self.headers.get('Content-Length','0'))
                        if not 1<=size<=2048:raise ValueError('Invalid preview size')
                        body=json.loads(self.rfile.read(size))
                        if set(body)!={'n','purpose','treatment','slot','account'}:raise ValueError('Invalid preview scope')
                        pipeline,*_=modules()
                        result=pipeline.preview_single_pub(PROJECT,CAMPAIGN,**body)
                    elif self.command=='POST' and self.path=='/dispatch':
                        size=int(self.headers.get('Content-Length','0'))
                        if not 1<=size<=256:raise ValueError('Invalid request size')
                        body=json.loads(self.rfile.read(size))
                        if set(body)!={'request_id'}:raise ValueError('Invalid request fields')
                        result=broker.dispatch(body['request_id'])
                    else:raise ValueError('Unsupported AUTO operation')
                    status=200
                except ValueError as exc:
                    status=200 if self.path=='/dispatch' else 409
                    result={'answer':str(exc),'control_state':'BLOCKED'}
                except Exception:
                    status=409;result={'error':'Broker safety check failed; no automatic retry. Inspect local ownership/configuration before requesting fresh authorization.'}
                data=json.dumps(result,allow_nan=False).encode()
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            do_GET=handle_request
            do_POST=handle_request
        HTTPServer(('127.0.0.1',8767),Handler).serve_forever()

def manual_operation(body):
    """Independent operator transport; does not consult AUTO worker state/policy."""
    from manual_execution import ManualExecution,parse_console
    if not isinstance(body,dict) or set(body)-{'action','request','request_id','scope_id','approved','command'}:raise ValueError('Invalid manual operation envelope')
    manager=ManualExecution();action=body.get('action')
    if action in ('config','parse','preview','preflight') and sys.argv[-1:]!=['--manual-query']:
        # The web/broker venv intentionally contains no IBM SDK. Use the same
        # scientific interpreter as canonical workers, with a fixed entry point.
        completed=subprocess.run([str(PYTHON),'-B',str(SCRIPT),'--manual-query'],
            input=json.dumps(body),text=True,encoding='utf-8',capture_output=True,
            timeout=80,cwd=PROJECT,shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if completed.returncode:raise ValueError('Manual preflight worker failed; no submission was dispatched')
        result=json.loads(completed.stdout)
        if result.get('error'):raise ValueError(result['error'])
        return result
    if action=='config':return manager.configuration()
    if action=='parse':return parse_console(body.get('command'),body.get('request'))
    if action=='preview':return manager.preview(body.get('request'))
    if action=='result':
        row=manager.get(body.get('request_id'))
        return {key:row[key] for key in ('id','state','request','preview','result')}
    if action=='preflight':return manager.preflight(body.get('request_id'),body.get('scope_id'))
    if action=='confirm':
        result=manager.authorize(body.get('request_id'),body.get('scope_id'),body.get('approved'))
        identity=result['request_id']
        try:
            with (STATE/('manual-'+identity+'.log')).open('ab') as log:
                subprocess.Popen([str(PYTHON),'-B',str(SCRIPT),'--manual-worker',identity],cwd=PROJECT,
                    stdin=subprocess.DEVNULL,stdout=log,stderr=log,shell=False,close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except Exception:
            manager.update(identity,'UNKNOWN',{'message':'Worker dispatch could not be verified. Inspect the durable request; no automatic retry.'})
            raise
        return result
    raise ValueError('Unsupported manual operation action')


def manual_worker(identity):
    from manual_execution import ManualExecution
    manager=ManualExecution()
    try:manager.execute(key(identity))
    except Exception as error:
        row=manager.get(identity)
        if row['state']=='QUEUED':manager.update(identity,'BLOCKED',{'message':'Exclusive execution lock unavailable or authorization expired. No IBM operation performed.'})
        print('Manual operation stopped:',type(error).__name__)


def manual_query():
    import io
    from contextlib import redirect_stdout,redirect_stderr
    from manual_execution import clean
    try:
        body=json.loads(sys.stdin.read(16001))
        if body.get('action') not in ('config','parse','preview','preflight'):raise ValueError('Query worker cannot execute operations')
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):result=manual_operation(body)
    except Exception as exc:
        result={'error':clean(str(exc)) if isinstance(exc,(ValueError,RuntimeError,FileNotFoundError)) else 'Manual query failed: '+type(exc).__name__}
    print(json.dumps(clean(result),allow_nan=False,ensure_ascii=True))


if __name__=='__main__':
    os.chdir(PROJECT)
    if sys.argv[1:]==['--serve']:serve()
    elif len(sys.argv)==3 and sys.argv[1]=='--worker':worker(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=='--manual-worker':manual_worker(sys.argv[2])
    elif sys.argv[1:]==['--manual-query']:manual_query()
    else:raise SystemExit('Use --serve; worker invocations are broker-owned.')
