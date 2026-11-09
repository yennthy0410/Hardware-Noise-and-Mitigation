"""Control-plane state only. The production execution adapter is deliberately gated.

Never interprets Python, shell, SQL or LLM-generated arguments. Does not implement
a campaign loop. Canonical WorkerLock must also be acquired by an execution adapter.
"""
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).resolve().parent
CAMPAIGN = 'vqe-independent-n-01'


class SinglePubScope(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    purpose:Literal['TEST','RECOVERY']
    treatment:Literal['M0_Baseline','M1_DD','M2_TREX','M3_ZNE','M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL']
    slot:int=Field(ge=1,le=30)
    account:str=Field(pattern=r'^lab[A-IK]$')
    scope_id:str=Field(pattern=r'^[a-f0-9]{64}$')
    action:Literal['monitor','collect']


class ManualScope(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['batch','job','pub','external']
    action:Literal['monitor','collect']
    block_id:str|None=Field(default=None,max_length=200)
    job_id:str|None=Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,128}$')
    treatment:str|None=Field(default=None,max_length=40)
    slot:int|None=Field(default=None,ge=1,le=30)
    account:str|None=Field(default=None,pattern=r'^lab[A-IK]$')
    scope_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation: Literal['help', 'campaign_status', 'batch_status', 'job_status', 'quota_status', 'monitor_collect', 'monitor005', 'start', 'pause', 'resume', 'status', 'prepare', 'preflight', 'monitor', 'collect', 'review', 'recover', 'analyze', 'export', 'batch','single_pub','manual']
    campaign: Literal['vqe-independent-n-01'] = CAMPAIGN
    n: Literal[5, 10, 15, 20, 25, 30] | None = None
    batch: Literal[1,2,3,4,5,6] | None = None
    single_pub:SinglePubScope|None=None
    collect_scope_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    manual_scope:ManualScope|None=None


def route_control(message):
    """Full-string matching: ordinary research questions retain the existing router."""
    text = re.sub(r'\s+', ' ', message.strip().lower().rstrip('.!?')).strip()
    text = re.sub(r'\bn\s*=\s*', 'n=', text)
    commands = {'start auto manager': 'start', 'pause auto manager': 'pause',
                'resume auto manager': 'resume', 'show worker status': 'status',
                'run preflight': 'preflight', 'monitor jobs': 'monitor',
                'collect completed jobs': 'collect', 'export figures': 'export',
                'run auto manager':'start','resume campaign':'resume','show auto manager status':'status',
                'monitor auto manager':'status','pause auto manager safely':'pause',
                'prepare campaign':'prepare','collect results':'collect','analyze results':'analyze',
                'review targeted recovery':'review', 'monitor batch 005':'monitor005',
                'show step result':'status','run targeted recovery':'recover',
                'show supported commands':'help','show supported step commands':'help',
                'review recovery':'review','recovery status':'review',
                'campaign status':'campaign_status','worker status':'status',
                'batch status':'batch_status','ibm job status':'job_status','ibm quota':'quota_status',
                'prepare':'prepare','preflight':'preflight','monitor / collect':'monitor_collect',
                'monitor existing jobs':'monitor','monitor':'monitor','collect':'collect','analyze':'analyze'}
    collect_prefix=r'(?:(?:please |(?:can|could|would) you (?:please )?))?collect'
    collect=re.fullmatch(collect_prefix+r'(?: (?:completed jobs|completed results|jobs|results))?(?: for| from)? (.+?)(?: please)?',text)
    if collect:
        scope=collect[1].replace(',', ' ')
        scope=re.sub(r'\s+',' ',scope).strip()
        match=re.fullmatch(r'n=(5|10|15|20|25|30) (?:batch\s*(?:=|#)?\s*)0*([1-6])',scope)
        if match:return ControlRequest(operation='collect',n=int(match[1]),batch=int(match[2]))
        match=re.fullmatch(r'batch\s*(?:=|#)?\s*0*([1-6]) (?:for |at )?n=(5|10|15|20|25|30)',scope)
        if match:return ControlRequest(operation='collect',n=int(match[2]),batch=int(match[1]))
    # A malformed collection request must never become a generic job/science query.
    if re.match('^'+collect_prefix+r'\b',text):return ControlRequest(operation='collect')
    if text in commands:
        return ControlRequest(operation=commands[text])
    if text.startswith('show ') and text[5:] in ('campaign status','worker status','batch status','ibm job status','ibm quota','recovery status'):
        return ControlRequest(operation=commands[text[5:]])
    match=re.fullmatch(r'review (?:targeted )?recovery(?: for)? n=(5|10|15|20|25|30)',text)
    if match:return ControlRequest(operation='review',n=int(match[1]))
    match=re.fullmatch(r'export (?:n=(5|10|15|20|25|30) figures?|figures? (?:for )?n=(5|10|15|20|25|30))',text)
    if match:return ControlRequest(operation='export',n=int(match[1] or match[2]))
    match = re.fullmatch(r'(analyze completed n-level|analyze n-level|export figures|run batch)(?:\s+n=)?\s*(5|10|15|20|25|30)', text)
    if match:
        return ControlRequest(operation='analyze' if match[1].startswith('analyze') else ('batch' if match[1]=='run batch' else 'export'), n=int(match[2]))
    return None


class ProductionAdapter:
    """Fail-closed discovery and fixed broker delegation, never in-process execution.

    PID + creation time + exact CLI identifies a candidate, not WorkerLock ownership.
    Do not weaken security.install or treat a stored RUNNING state as liveness.
    """
    def inspect(self):
        try:
            from broker_client import call
            return call('/status')
        except Exception:
            pass  # No broker: retain fail-closed read-only process observation.
        import sys
        site = str(Path(sys.base_prefix) / 'Lib/site-packages')
        if site not in sys.path:
            sys.path.append(site)
        candidates = []
        uncertain = False
        try:
            import psutil
            production = (HERE.parent / '03_ai_assistant').resolve()
            entries = {str((production / path).resolve()).casefold() for path in
                       ('mxene.py', 'diagnostic_manager.py', '01_ai_agent/mxene.py',
                        '03_python_automation/diagnostic_manager.py',
                        '03_python_automation/research_pipeline.py')}
            for process in psutil.process_iter():
                try:
                    if 'python' not in process.name().lower():
                        continue
                    args = process.cmdline()
                    cwd = Path(process.cwd())
                    scripts = {str((cwd / arg).resolve()).casefold() for arg in args[1:] if arg.endswith('.py')}
                    relevant = bool(scripts & entries)
                    # Unknown Python entrypoints in this repo are not assumed harmless.
                    if not relevant and (cwd.is_relative_to(production) or any(str(production).casefold() in arg.casefold() for arg in args)):
                        uncertain = True
                    if relevant:
                        candidates.append({'pid': process.pid, 'created': process.create_time(),
                                           'campaign_match': CAMPAIGN in args, 'live_flag': '--live' in args})
                except psutil.NoSuchProcess:
                    continue
                except (psutil.AccessDenied, OSError):
                    uncertain = True
        except (ImportError, OSError, PermissionError):
            uncertain = True
        return {'worker': 'UNKNOWN' if uncertain else ('CANDIDATE_RUNNING' if candidates else 'NO_CANDIDATE_OBSERVED'),
                'ownership_verified': False, 'candidates': candidates, 'process_scan_complete': not uncertain,
                'can_execute': False, 'unknown_submission': None,
                'reason': 'AUTO broker unavailable or unable to verify ownership/configuration. No worker was changed.'}

    def confirmation(self, command):
        evidence=self.inspect()
        if not evidence.get('ownership_verified'):raise ValueError('Broker ownership verification unavailable')
        summary=dict(evidence['policy'])
        if command.operation=='manual':
            from broker_client import call
            try:preview=call('/manual-preview',{'campaign':command.campaign,'n':command.n,'scope':command.manual_scope.model_dump(exclude_none=True)})
            except OSError as exc:raise ValueError('Manual scope preview unavailable; restart the updated broker. No operation authorized.') from exc
            if preview.get('state')=='BLOCKED':raise ValueError(preview['reason'])
            summary['manual_preview']=preview
        if command.operation=='collect':
            from broker_client import call
            try:scope=call('/collect-preview',{'n':command.n,'batch':command.batch})
            except OSError as exc:
                raise ValueError('Canonical COLLECT preview is unavailable. Restart the updated broker and request a fresh confirmation; no collection authorized.') from exc
            if scope.get('control_state')=='BLOCKED':raise ValueError(scope['answer'])
            summary['collect_scope']=scope
        return summary

    def execute(self, command, *, expected_evidence, authorization_id):
        if command.operation not in ('start','resume','pause','preflight','monitor005','prepare','collect','single_pub','analyze','export','manual'):raise ValueError('Unsupported canonical operation')
        from broker_client import call
        return call('/dispatch',{'request_id':authorization_id})

    def operation_blocker(self, command):
        if command.operation=='manual' and (command.manual_scope is None or command.n is None):return 'Explicit registered manual scope and n-level required'
        if command.operation=='single_pub':
            if not command.single_pub or command.n is None:return 'Explicit pinned single-PUB scope required.'
            if command.single_pub.purpose!='RECOVERY':return 'Isolated TEST quota/storage policy is not approved.'
            if command.single_pub.slot>command.n:return 'Logical slot is outside selected n-level.'
        elif command.single_pub is not None:return 'Unexpected single-PUB scope.'
        if command.operation=='collect' and (command.n is None or command.batch is None):
            return 'BLOCKED: specify Collect completed jobs for n=30 Batch 006 with one supported n-level and Batch. Only original first-attempt eight-job Batches are supported; replacement or ambiguous scopes remain blocked.'
        if command.operation=='collect' and command.batch>command.n//5:return 'Explicit existing Batch sequence required'
        if command.operation!='collect' and command.batch is not None:return 'Batch scope is collect-only.'
        if command.operation=='monitor005' and not (HERE.parents[2]/'manager_diagnostics/ibm_batch_005_readonly.py').is_file():
            return 'BLOCKED: fixed-scope read-only Batch 005 diagnostic is unavailable. No substitute provider loop is permitted.'
        return {
            'monitor_collect':'BLOCKED: choose one operation, not a combined workflow. Monitor Batch 005 supports a confirmed read-only provider diagnostic for that registered scope. Collect requires one explicit n-level and Batch, verified original identities, and write authorization.',
            'batch':'No audited canonical single-batch entrypoint exists. once=True runs a full cycle and may recover or submit; it is not a batch-only operation.',
            'monitor':'BLOCKED: canonical monitor_batch also retrieves DONE results, archives them and updates registry/usage records. It is not a status-only operation; production writes are not authorized for this STEP.',
            'recover':'BLOCKED: canonical targeted recovery can persist plans and submit replacements. This STEP has no replacement authorization; use Review targeted recovery for read-only observations.',
        }.get(command.operation)


def delegate_canonical(command, project, *, authorized=False):
    """Broker-side dispatch specification; never called by the production web adapter.

    No shell, account selection, scheduler, job/PUB selection or retry loop. AUTO
    retains the canonical locking and all admission gates. The sandbox deliberately
    prevents using this as an in-process production execution escape hatch.
    Only AUTO is mapped here; individual mutating steps remain unsupported.
    """
    if not authorized:
        raise ValueError('Explicit verified execution authorization is required.')
    if command.operation in ('start','resume'):
        import research_pipeline
        return research_pipeline.run_pipeline(project,command.campaign,mode='live',
            confirm_live_submission=True,authorize_live_campaign=True,ai_mode='python',
            once=False,resume=command.operation=='resume',poll_seconds=60)
    raise ValueError('No audited single-operation canonical delegate for this request.')


def suggested_prompts(evidence, snapshot, now=None):
    """Presentation policy only. Suggestions never authorize or execute anything."""
    from datetime import datetime, timezone
    safe=['Show campaign progress.','Show Auto Manager status','Check IBM Quota','Review targeted recovery','Analyze n=5']
    try:
        captured=datetime.fromisoformat(snapshot['captured_at'])
        age=((now or datetime.now(timezone.utc))-captured).total_seconds()
        fresh=not snapshot.get('offline',True) and 0<=age<=180
    except (KeyError,TypeError,ValueError):fresh=False
    verified=evidence.get('ownership_verified') and evidence.get('worker') in ('IDLE_VERIFIED','STOPPED_VERIFIED','RUNNING_VERIFIED') and evidence.get('can_execute')
    if not fresh or not verified:
        return {'prompts':safe,'read_only_only':True,'reason':'Only read-only prompts: worker ownership or snapshot freshness is unverified.'}
    # These are editable requests, not claims that each STEP is supported.
    operations=(['Pause Auto Manager safely','Show Auto Manager status','Show campaign progress.']
                if evidence.get('worker')=='RUNNING_VERIFIED' else
                ['Run Auto Manager','Resume Auto Manager','Show Auto Manager status','Check IBM Quota','Show campaign progress.'])
    if snapshot.get('valid',0)>=snapshot.get('expected',840):
        operations=[x for x in operations if x not in ('Run Auto Manager','Resume Auto Manager')]
    return {'prompts':operations,'read_only_only':False,'reason':'Each operation still requires its own capability and safety checks.'}


def review_recovery(reader, n=None):
    snapshot=reader.status()
    if snapshot.get('offline'):
        raise ValueError('Recovery review unavailable: current local snapshot could not be verified.')
    fields=('n','treatment','expected','valid','invalid','pending','recovery_needed','status')
    rows=[{key:row.get(key) for key in fields} for row in snapshot.get('recovery_summary',[]) if n is None or row.get('n')==n]
    return {'answer':'Read-only targeted recovery review of the recorded logical slots. Pending jobs are not classified as missing. No replacement plan or submission was created.',
            'engine':'Python tool bridge','rows':rows,
            'limitations':'Persisted observations only; UNKNOWN identities require canonical reconciliation before any replacement.'}


class Supervisor:
    """Durable one-shot confirmation and dispatch contract.

    The broker must re-check ownership and acquire canonical locks on execute;
    this local transaction is additional serialization, never a substitute for them.
    """
    def __init__(self, path=None, adapter=None, clock=time.time):
        self.path = Path(path or HERE / 'state/worker_control.sqlite3').resolve()
        if not self.path.is_relative_to(HERE):
            raise ValueError('Supervisor storage must remain in Dashboard')
        self.adapter = adapter or ProductionAdapter()
        self.clock = clock
        self.guard = threading.Lock()

    def db(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, conversation TEXT NOT NULL, request TEXT NOT NULL, evidence TEXT NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL, result TEXT)')
        db.commit()
        return db

    @staticmethod
    def fingerprint(evidence):
        # Adapter evidence must contain ownership, authorization/config identity and
        # checkpoint version; never credentials. Changes require a fresh confirmation.
        return hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def status(self):
        evidence = self.adapter.inspect()
        ownership='verified' if evidence.get('ownership_verified') else 'UNKNOWN'
        step=evidence.get('step_result') or {}
        status=evidence.get('operation_status')
        receipt=evidence.get('operation_receipt')
        detail=(f"Latest STEP: {status}. " if status else '')
        if receipt:
            detail+=f"Operation: {receipt.get('operation')}; n={receipt.get('n')}; Batch: {receipt.get('batch')}; receipt prefix: {str(receipt.get('request'))[:12]}. "
        if evidence.get('failure_reason'):detail+='Reason: '+evidence['failure_reason']+'. '
        return {'answer': detail+'Worker observation: ' + evidence['worker'] + '. Lock ownership: ' + ownership + '. ' + evidence.get('reason', '') + (' Last STEP: '+step['answer'] if step else ''),
                'rows':step.get('rows',[]),
                'operation_status':status,'receipt':receipt,'step_result':step or None,
                'failure_reason':evidence.get('failure_reason'),
                'engine': 'python-worker-supervisor', 'worker_state': evidence['worker'],
                'control_available': bool(evidence.get('can_execute')), 'ownership_verified': bool(evidence.get('ownership_verified')),
                'automation':automation_view(evidence)}

    def gate(self, command, evidence):
        blocker=getattr(self.adapter,'operation_blocker',lambda command:None)(command)
        if blocker:return blocker
        if command.operation in ('preflight','monitor005') and command.n is not None:
            return 'Preflight is campaign/account scoped; an n-level argument is unsupported.'
        if not evidence.get('can_execute'):
            return evidence.get('reason', 'Execution adapter is unavailable.')
        if not evidence.get('ownership_verified'):
            return 'Worker ownership is unverified; no action performed.'
        if command.operation in ('start','resume','pause') and 'campaign_state' in (evidence.get('policy') or {}):
            action=automation_view(evidence)['actions'][command.operation]
            if not action['enabled']:return action['reason']
        if (command.operation in ('preflight','monitor005','prepare','collect','single_pub','analyze','export','manual') and isinstance(self.adapter,ProductionAdapter)
                and command.operation not in evidence.get('supported_operations',[])):
            return 'BLOCKED: the running broker has not advertised this STEP capability. Restart only the updated broker service and recheck ownership; do not start a campaign.'
        if command.operation in ('start','resume') and evidence['worker'] not in ('IDLE_VERIFIED','STOPPED_VERIFIED','RUNNING_VERIFIED'):
            return 'A verified idle or owned running worker is required.'
        policy=evidence.get('policy')
        if command.operation in ('start','resume') and policy and (not policy.get('ready') or policy.get('complete')):
            return 'Canonical authorization/frozen-input verification failed or campaign is already complete. No launch allowed.'
        if command.operation in ('start', 'resume') and evidence.get('unknown_submission') is not False:
            return 'UNKNOWN submission reconciliation is required before execution; no resubmission authorized.'
        if evidence.get('operation') in ('preflight','monitor005','prepare','collect','single_pub','analyze','export','manual') and evidence['worker']=='RUNNING_VERIFIED' and command.operation!='status':
            return 'A one-shot diagnostic is active. Wait for completion; AUTO attach/pause and concurrent steps are blocked.'
        if command.operation not in ('start', 'resume', 'pause', 'status') and evidence['worker'] not in ('IDLE_VERIFIED','STOPPED_VERIFIED'):
            return 'STEP mode is blocked while a worker is active or ownership is uncertain.'
        if command.operation == 'pause' and evidence['worker'] != 'RUNNING_VERIFIED':
            return 'There is no verified running worker to pause.'
        if command.operation in ('prepare','collect','single_pub','analyze','export','manual') and evidence.get('unknown_submission') is not False:
            return 'UNKNOWN submission reconciliation is required before canonical STEP execution.'
        if command.operation in ('analyze', 'export') and command.n is None:
            return 'Specify one n-level: Analyze n-level n=5 or Export n=5 figures. Canonical output writes require explicit confirmation; no operation has run.'
        return None

    def prepare(self, conversation, command):
        if command.operation == 'status':
            return self.status()
        with self.guard:
            evidence = self.adapter.inspect()
            with closing(self.db()) as db:
                # Recover only an exact verified launch receipt. Missing/crashed
                # receipts are never cleared, and never cause an automatic restart.
                if evidence.get('ownership_verified') and evidence.get('owner'):
                    with db:db.execute("UPDATE requests SET state='RECONCILED_OWNER' WHERE id=? AND state IN ('DISPATCHING','UNKNOWN')",(evidence['owner'],))
                unresolved=db.execute("SELECT count(*) FROM requests WHERE state IN ('DISPATCHING','UNKNOWN')").fetchone()[0]
            if unresolved:
                return {'answer':'An earlier dispatch is unresolved. Reconcile its outcome before any new control request. No action performed.',
                        'engine':'python-worker-supervisor','worker_state':evidence['worker'],'control_state':'BLOCKED'}
            if (command.operation in ('start','resume') and evidence.get('ownership_verified')
                    and evidence['worker']=='RUNNING_VERIFIED' and evidence.get('operation') not in ('preflight','monitor005','prepare','collect','single_pub','analyze','export','manual')):
                return {'answer':'Attached to the verified existing worker. No duplicate worker started.',
                        'engine':'python-worker-supervisor','control_state':'ATTACHED'}
            reason = self.gate(command, evidence)
            if reason:
                return {'answer': reason, 'engine': 'python-worker-supervisor', 'worker_state': evidence['worker'], 'control_state': 'BLOCKED'}
            try:summary = self.adapter.confirmation(command)
            except ValueError as exc:
                return {'answer':str(exc),'engine':'python-worker-supervisor','control_state':'BLOCKED'}
            scope=summary.get('collect_scope')
            if command.operation=='manual':
                preview=summary.get('manual_preview')
                if not preview or preview.get('state')!='READY':return {'answer':'Manual scope preview is unavailable','control_state':'BLOCKED'}
                command=command.model_copy(update={'manual_scope':command.manual_scope.model_copy(update={'scope_id':preview['scope_id']})})
                scope=preview
            if command.operation=='collect' and isinstance(self.adapter,ProductionAdapter):
                if not scope or scope.get('n')!=command.n or scope.get('batch')!=command.batch or scope.get('campaign_id')!=command.campaign or len(scope.get('jobs',[]))!=8:
                    return {'answer':'Canonical eight-job COLLECT preview is unavailable or mismatched. No collection authorized.',
                            'engine':'python-worker-supervisor','control_state':'BLOCKED'}
                command=command.model_copy(update={'collect_scope_id':scope['scope_id']})
            required = {'backend', 'accounts', 'quota_implications', 'authorization_identity'}
            if not required.issubset(summary) or not summary['accounts'] or not summary['authorization_identity']:
                raise ValueError('Canonical authorization scope is incomplete.')
            request_id = uuid.uuid4().hex
            with closing(self.db()) as db, db:
                db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,NULL)', (request_id, conversation,
                           command.model_dump_json(), self.fingerprint(evidence), self.clock()+120, 'AWAITING_CONFIRMATION'))
            effect=('One read-only provider diagnostic, under canonical exclusive locks; results stored only in Dashboard. No submission, account selection, reservation or campaign update. No automatic advancement.'
                    if command.operation in ('preflight','monitor005') else summary['quota_implications'])
            if command.operation=='prepare':effect='Verify the existing immutable campaign preparation only; no reset, new campaign, submission or checkpoint advancement.'
            if command.operation=='manual':effect=f"Manual {command.manual_scope.action} for n={command.n}; {command.manual_scope.kind} scope. {scope['collection_unit']} {'This authorizes canonical archive/validation writes.' if command.manual_scope.action=='collect' else 'Provider status only; no result retrieval or production writes.'} No submission, cancellation or automatic recovery."
            if command.operation in ('analyze','export'):effect=f'Authorize exactly one canonical {command.operation} for n={command.n}, including canonical analysis/publication output writes. Existing artifacts are verified/reused or versioned, never overwritten. No IBM submission or campaign advancement.'
            if command.operation=='single_pub':effect=f'Authorize exactly one {command.single_pub.action} of existing RECOVERY slot {command.single_pub.slot}, n={command.n}, {command.single_pub.treatment}, account {command.single_pub.account}. No submission or replacement is permitted. Collection may archive/validate one existing PUB.'
            if command.operation=='collect':effect=f'Authorize one COLLECT-only read of completed registered jobs for n={command.n} Batch {command.batch:03d}. Eight registered original Job IDs are listed below. This authorizes immutable archival and canonical validation/registry writes. Pending/failed jobs are not retrieved. No submission, resubmission, cancellation, recovery, analysis or advancement. Ambiguous identities remain blocked.'
            if command.operation=='monitor005':effect+=' Fixed scope: n=30 Batch 005, labK, ibm_fez, exactly eight registered job IDs; no result payload retrieval.'
            return {'answer': f"Confirm {command.operation} for {command.campaign}; backend {summary['backend']}; accounts {', '.join(summary['accounts'])}. {effect}",
                    'engine': 'python-worker-supervisor', 'control_state': 'AWAITING_CONFIRMATION', 'control_request': request_id,
                    'rows':scope['jobs'] if scope else [],'collection_scope':scope}

    def confirm(self, conversation, request_id, approved):
        if approved is not True:
            raise ValueError('Explicit confirmation is required.')
        with self.guard, closing(self.db()) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT count(*) FROM requests WHERE state IN ('DISPATCHING','UNKNOWN')").fetchone()[0]:
                raise ValueError('An earlier dispatch is unresolved. No new execution is allowed.')
            row = db.execute('SELECT * FROM requests WHERE id=? AND conversation=?', (request_id, conversation)).fetchone()
            if row is None or row['state'] != 'AWAITING_CONFIRMATION' or row['expires'] < self.clock():
                raise ValueError('Confirmation is expired, already consumed or belongs to another conversation.')
            command = ControlRequest.model_validate_json(row['request'])
            evidence = self.adapter.inspect()
            reason = self.gate(command, evidence)
            if reason or self.fingerprint(evidence) != row['evidence']:
                raise ValueError(reason or 'Worker/checkpoint/authorization changed. Request fresh confirmation.')
            # Persist before dispatch: interrupted dispatch is UNKNOWN, never replayed.
            db.execute("UPDATE requests SET state='DISPATCHING' WHERE id=?", (request_id,))
            db.commit()
            try:
                result = self.adapter.execute(command, expected_evidence=evidence, authorization_id=request_id)
                state = result['control_state']
            except Exception:
                result = {'answer': 'Dispatch outcome is unknown. Reconcile worker ownership and submission identities; do not repeat the action.', 'control_state': 'UNKNOWN'}
                state = 'UNKNOWN'
            result['engine'] = 'python-worker-supervisor'
            with db:
                db.execute('UPDATE requests SET state=?,result=? WHERE id=?', (state, json.dumps(result, allow_nan=False), request_id))
            return result


supervisor = Supervisor()


COMMAND_GROUPS = {
    'Status & Monitoring':['Campaign Status','Worker Status','Batch Status','IBM Job Status','IBM Quota','Recovery Status'],
    'AUTO Control':['Run Auto Manager','Pause Auto Manager','Resume Auto Manager'],
    'STEP-by-STEP':['Prepare','Preflight','Monitor / Collect','Collect completed jobs for n=30 Batch 006','Review Recovery','Analyze','Export Figures'],
}


def observation_freshness(stamp):
    from datetime import datetime, timezone
    try:
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds()
        return 'RECENT' if 0<=age<=180 else 'STALE'
    except (AttributeError,TypeError,ValueError):return 'UNAVAILABLE'


def operational_result(result,snapshot):
    """Local capture time is never presented as a fresh IBM observation."""
    campaigns=snapshot.get('campaign') or []
    campaign=campaigns[0] if isinstance(campaigns,list) and campaigns else {}
    stamp=snapshot.get('captured_at')
    freshness='UNAVAILABLE' if snapshot.get('offline',True) else observation_freshness(stamp)
    return {**result,'answer':result['answer']+f"\nRecorded campaign state: {campaign.get('state','UNAVAILABLE')}. Local snapshot checked: {stamp or 'N/A'} ({freshness}). This timestamp is not a fresh IBM status query."}


def automation_view(evidence):
    """Presentation of verified process state, separate from authorization policy."""
    policy=evidence.get('policy') or {};worker=evidence.get('worker');campaign=policy.get('campaign_state')
    busy=evidence.get('operation') in ('preflight','monitor005','prepare','collect','single_pub','analyze','export','manual') and worker=='RUNNING_VERIFIED'
    if not evidence.get('ownership_verified') or worker not in ('RUNNING_VERIFIED','STOPPED_VERIFIED','IDLE_VERIFIED'):state='ERROR'
    elif worker=='RUNNING_VERIFIED' and not busy:state='RUNNING'
    elif campaign=='PAUSED':state='PAUSED'
    elif campaign in ('ERROR','FAILED'):state='ERROR'
    else:state='STOPPED'
    reason=('Ownership or service health is unverified; ERROR does not imply IBM jobs failed.' if state=='ERROR' else
        'An exclusive manual/STEP operation is active; automation is not running.' if busy else
        'Pause requested; waiting for the active worker to release its lock.' if state=='RUNNING' and campaign=='PAUSED' else
        'Verified local automation state; remote jobs are not cancelled by Pause.')
    actions={}
    for op,wanted in (('start','STOPPED'),('pause','RUNNING'),('resume','PAUSED')):
        blocker=None
        if state!=wanted:blocker=f'{op.title()} requires {wanted}; current state is {state}.'
        elif busy:blocker='Wait for the exclusive manual/STEP operation to finish.'
        elif not evidence.get('can_execute'):blocker=evidence.get('reason') or 'Execution transport is unavailable.'
        elif op=='pause' and campaign=='PAUSED':blocker='Pause is already requested; await acknowledgement.'
        elif op in ('start','resume') and (not policy.get('ready') or policy.get('complete')):blocker='Authorization is unavailable or the campaign is complete.'
        elif op in ('start','resume') and evidence.get('unknown_submission') is not False:blocker='UNKNOWN submission reconciliation is required.'
        actions[op]={'enabled':not blocker,'reason':blocker or 'Preview and explicit confirmation required; fresh canonical gates still apply.'}
    return {'state':state,'reason':reason,'recorded_campaign_state':campaign,
        'authorization_status':'POLICY_CONFIGURED' if policy.get('ready') else 'UNAVAILABLE_OR_DISABLED',
        'authorization_note':'Policy configuration is not live authorization. Each operation requires its existing confirmation and checks.',
        'actions':actions}


def command_capabilities(controller, snapshot):
    """Discovery only: the same blockers/gates as execution, never permission."""
    evidence=controller.adapter.inspect()
    fresh=not snapshot.get('offline',True) and observation_freshness(snapshot.get('captured_at'))=='RECENT'
    rows=[]
    for group,commands in {**COMMAND_GROUPS,'Additional commands':['Show supported commands','Monitor Batch 005']}.items():
        for text in commands:
            command=route_control(text)
            if command.operation in ('help','status','review','campaign_status','batch_status','job_status','quota_status'):
                status='READ_ONLY'
                reason=('Verified local lock/process observation; no IBM lookup.' if command.operation=='status' else
                        'Existing local evidence/discovery only; not a fresh IBM query. Missing or stale observations remain explicit.')
            else:
                reason=controller.gate(command,evidence)
                if not reason and command.operation in ('start','resume') and not fresh:
                    reason='Campaign snapshot is stale or unavailable; no LIVE recommendation. Refresh verified evidence first.'
                if not reason and command.operation in ('start','resume') and snapshot.get('valid',0)>=snapshot.get('expected',840):
                    reason='Dataset is complete; no new campaign submission recommended.'
                if reason:status='BLOCKED'
                elif command.operation in ('start','resume') and evidence['worker']=='RUNNING_VERIFIED':
                    status,reason='AVAILABLE','Attach to the verified existing AUTO worker; do not start another.'
                else:
                    status,reason='REQUIRES_AUTHORIZATION','Explicit one-use confirmation and fresh broker checks required.'
                    if command.operation=='monitor005':reason+=' Fixed read-only labK / ibm_fez / n=30 Batch 005 diagnostic; no collection.'
            rows.append({'command':text,'group':group,'status':status,'reason':reason,
                'label':{'start':'Start Automation','pause':'Pause Automation','resume':'Resume Automation'}.get(command.operation,text)})
    return operational_result({'answer':'Python Auto Manager command capabilities. Visibility does not authorize execution. Analyze n-level n=30 requests canonical analysis with explicit write confirmation; Export requires its verified successful Analyze receipt. Analyze n=30 remains a read-only research query.',
            'engine':'python-worker-supervisor','rows':rows,
            'commands':[r for r in rows if r['group']!='Additional commands'],
            'worker_state':evidence['worker'],'snapshot_fresh':fresh,'automation':automation_view(evidence)},snapshot)
