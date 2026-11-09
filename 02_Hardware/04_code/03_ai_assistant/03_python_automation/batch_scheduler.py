"""IBM Runtime Batch scheduler with a disabled-by-default live submission gate."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

from low_memory_runtime import iter_provider_jobs
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import uuid
from typing import Any

from account_config import ACCOUNT_LABELS
from experiment_manifest import ExperimentManifest
from job_registry import JobRegistry
from quota import actual_usage_from_response, actual_usage_seconds, available_quota_seconds, provider_quota_fields, provider_reservations
from runtime_estimator import workload_quota_decision


class LiveSubmissionDisabled(RuntimeError):
    pass


def _stamp(): return datetime.now(timezone.utc).isoformat()


def _write_new(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
        stream.flush(); os.fsync(stream.fileno())


def _update_owned(path: Path, attempt_id: str, payload: dict):
    old=json.loads(path.read_text(encoding="utf-8"))
    if old.get("attempt_id") != attempt_id:
        raise RuntimeError(f"Refusing to update another attempt's state file: {path}")
    temp=path.with_name(f".{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temp.open("x",encoding="utf-8") as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2,default=str); stream.flush(); os.fsync(stream.fileno())
        for retry in range(3):
            try:
                os.replace(temp,path)
                break
            except PermissionError:
                if retry==2:raise
                # Windows file sharing/OneDrive can briefly hold this owned file.
                # Retry only this local atomic replacement, never the IBM call.
                import time
                time.sleep(0.1*(retry+1))
                if json.loads(path.read_text(encoding='utf-8')).get('attempt_id')!=attempt_id:
                    raise RuntimeError('State file ownership changed during atomic update')
    finally:
        if temp.exists(): temp.unlink()


def _start_reconciled_retry(path: Path, payload: dict):
    old=json.loads(path.read_text(encoding="utf-8"))
    if old.get("status") != "CONFIRMED_NOT_SUBMITTED" or old.get("job_id"):
        raise RuntimeError(f"Retry requires reconciled no-submission state: {path}")
    attempts=list(old.get("prior_attempts",[]))
    attempts.append({k:old.get(k) for k in ("attempt_id","status","created_utc","updated_utc","error_type","reconciliation") if k in old})
    payload["prior_attempts"]=attempts
    temp=path.with_name(f".{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temp.open("x",encoding="utf-8") as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2,default=str);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists(): temp.unlink()


def _safe_value(value):
    return json.loads(json.dumps(value, default=str))


def _runtime_types():
    # Imported only after explicit authorization at the submission boundary.
    from low_memory_runtime import remote_runtime
    remote_runtime()
    from qiskit_ibm_runtime import Batch, EstimatorV2
    return Batch, EstimatorV2


def _active_instance(service, requested: str | None = None) -> str:
    active=service.active_instance() if callable(getattr(service,"active_instance",None)) else requested
    if requested and active and requested != active:
        raise RuntimeError("Requested IBM instance differs from the service's active instance")
    if not active:
        raise RuntimeError("Cannot determine the active IBM instance; refusing untraceable Runtime work")
    return str(active)


def _assert_bundle_matches(manifest: ExperimentManifest):
    from hashlib import sha256
    bundle=Path(manifest.source_bundle)
    for name, expected in manifest.input_hashes.items():
        actual=sha256((bundle/name).read_bytes()).hexdigest()
        if actual != expected: raise RuntimeError(f"Frozen input changed since manifest validation: {name}")
    lock=json.loads((bundle/"lock.json").read_text(encoding="utf-8"))
    if lock.get("source_run") != "run_02" or lock.get("backend") != manifest.backend:
        raise RuntimeError("Frozen lock no longer identifies run_02 on the manifest backend")
    if any(lock.get("hashes",{}).get(name) != expected for name,expected in manifest.input_hashes.items()):
        raise RuntimeError("Frozen lock hashes differ from the manifest")


def preflight_batch(manifest: ExperimentManifest, service, *, backend_name: str | None = None,
                    runtime_version: str | None = None, root=None) -> dict:
    """Read-only IBM service/backend check; does not construct a Batch or submit a job."""
    _assert_bundle_matches(manifest)
    name=backend_name or manifest.backend
    if name != manifest.backend: raise ValueError("Preflight backend differs from frozen manifest")
    backend=service.backend(name)
    if getattr(backend,"name",name) != name: raise RuntimeError("IBM service returned a different backend")
    from vqe_adapter import verify_frozen_inputs
    qc,obs,theta,physical,_,_=verify_frozen_inputs(root)
    if tuple(physical) != tuple(manifest.physical_qubits): raise RuntimeError("Mapped observables differ from manifest")
    unsupported=[]
    target=getattr(backend,"target",None)
    if target is None: raise RuntimeError("Backend target is unavailable for ISA preflight")
    for item in qc.data:
        operation=item.operation.name
        qubits=tuple(qc.find_bit(q).index for q in item.qubits)
        if operation not in ("barrier","delay") and not target.instruction_supported(operation_name=operation,qargs=qubits):
            unsupported.append((operation,qubits))
    if unsupported: raise RuntimeError(f"Unsupported ISA operations: {unsupported[:8]}")
    try:
        from low_memory_runtime import remote_runtime
        remote_runtime()
        from qiskit_ibm_runtime import Batch as _Batch
        batch_class_available=True
    except ImportError:
        batch_class_available=False
    if not batch_class_available: raise RuntimeError("Installed Runtime SDK has no Batch API")
    if runtime_version is None:
        from importlib.metadata import version
        runtime_version=version("qiskit-ibm-runtime")
    active_instance=_active_instance(service)
    usage=service.usage()
    if not isinstance(usage,dict): raise RuntimeError("Runtime usage response is unsupported")
    actual=actual_usage_from_response(usage)
    return {"backend":name,"instance":active_instance,"backend_status":str(backend.status()),"batch_api_available":batch_class_available,
            "runtime_version":runtime_version,"actual_usage_seconds":actual,"usage_response":usage,
            "physical_qubits":list(physical),"isa_operations":dict(qc.count_ops()),"observable_count":len(obs),
            "theta_count":len(theta),"submission":False,"checked_utc":_stamp()}


class FakeRuntimeJob:
    def __init__(self, job_id: str): self._id = job_id
    def job_id(self): return self._id
    def status(self): return "QUEUED"


class FakeBatch:
    def __init__(self, service): self.service = service; self.session_id = None
    def __enter__(self):
        self.service.batch_count += 1
        self.session_id=f"fake-batch-{self.service.batch_count:04d}"
        self.service.in_batch = True
        return self
    def __exit__(self, exc_type, exc, tb):
        self.service.in_batch = False
        return False


class FakeEstimatorV2:
    def __init__(self, *, mode, options=None):
        if not mode.service.in_batch:
            raise RuntimeError("Estimator jobs must be submitted inside one Runtime Batch")
        self.mode, self.options = mode, options
    def run(self, pubs):
        if not pubs:
            raise ValueError("Each treatment job must contain repeated PUBs")
        return self.mode.service.create_job(pubs,options=self.options)


class FakeBatchMode:
    def __init__(self, service, backend): self.service, self.backend = service, backend


class FakeRuntimeService:
    """Deterministic in-memory double; never imports IBM Runtime or accesses a network."""
    def __init__(self, *, usage_seconds: dict[str, float] | None = None):
        self.usage_seconds = usage_seconds or {label: 0.0 for label in ACCOUNT_LABELS}
        self.batch_count = 0
        self.in_batch = False
        self.jobs: list[dict[str, Any]] = []
    def usage(self, account_label: str) -> float:
        if account_label not in self.usage_seconds:
            raise RuntimeError(f"No verified usage for {account_label}")
        return float(self.usage_seconds[account_label])
    def batch(self, *, backend): return FakeBatchMode(self, backend)
    def batch_context(self, *, backend): return FakeBatch(self)
    def create_job(self, pubs, options=None):
        if not self.in_batch: raise RuntimeError("No active Batch")
        job_id=f"fake-{len(self.jobs)+1:04d}"
        self.jobs.append({"job_id":job_id,"pubs":pubs,"options":options})
        return FakeRuntimeJob(job_id)


def _locked_reconciliation(function):
    """Serialize reconciliation with the existing campaign submitter lock."""
    from functools import wraps
    @wraps(function)
    def locked(registry, identity, *args, **kwargs):
        from worker_runtime import WorkerLock
        from vqe_adapter import record_directory
        rows=registry.rows()
        row=next((r for r in rows if r["id"]==identity or r["logical_block_id"]==identity),None)
        if row is None: raise KeyError(identity)
        campaign_id,_=registry.independent_campaign_context(row.get("target_id"))
        if campaign_id:
            from independent_n_campaign import campaign_root
            directory=campaign_root(kwargs.get("root"),campaign_id)
        else:
            directory=record_directory(kwargs.get("root"),row["logical_block_id"])
        with WorkerLock(directory/"worker.lock"):
            return function(registry,identity,*args,**kwargs)
    return locked


def reconcile_campaign_intents(root, registry, campaign_id, authorization, service_factory=None):
    """Positive-only restart reconciliation. Caller must hold the campaign WorkerLock.

    An empty inventory never authorizes a retry here. A recorded Batch and one
    fully verified candidate are required; failed reads retain the durable intent.
    """
    from account_config import load_saved_account_profiles, make_saved_profile_service
    from worker_runtime import RetryingReadOnlyService
    from read_only_preflight import _instance_ref
    snapshot=registry.independent_campaign_status(campaign_id)
    targets={level['target_id'] for level in snapshot['levels']}
    unresolved=[row for row in registry.rows() if row.get('target_id') in targets
                and row['submission_state'] in ('SUBMITTING','UNKNOWN')]
    for row in unresolved:
        if row['submission_state']=='SUBMITTING' and not row.get('job_id'):
            registry.transition(row['id'],'UNKNOWN',event_type='RESTART_SUBMISSION_OUTCOME_UNKNOWN')
        account=row['account_label']
        if account not in authorization['allowed_accounts']:
            raise RuntimeError('UNKNOWN submission account requires authorization for reconciliation')
        service=service_factory(account) if service_factory else make_saved_profile_service(
            load_saved_account_profiles((account,))[account])
        service=RetryingReadOnlyService(service,authorization['recovery_limits']['max_read_attempts'])
        instance=service.active_instance()
        if _instance_ref(instance) not in authorization['allowed_instance_refs'].get(account,[]):
            raise RuntimeError('UNKNOWN submission instance requires authorized reconciliation')
        if not row.get('batch_id'):
            continue
        jobs=[service.job(row['job_id'])] if row.get('job_id') else iter_provider_jobs(
            service,session_id=row['batch_id'],instance=instance)
        matches=[]
        for job in jobs:
            try:
                verify_reconciliation_job(row,job,service,root=root)
            except (ValueError,RuntimeError):
                continue
            matches.append(job)
        if len(matches)==1:
            reconcile_unknown_job.__wrapped__(registry,row['id'],service,root=root,
                job_id=matches[0].job_id(),confirm_job_matches=True)
    pending=[row for row in registry.rows() if row.get('target_id') in targets
             and row['submission_state'] in ('UNKNOWN','SUBMITTING')]
    replacements=[r for target in targets for r in registry.replacement_jobs(target)
                  if r['state'] in ('UNKNOWN','SUBMITTING','RESERVED')]
    if pending or replacements:
        raise RuntimeError('UNKNOWN submission identity remains unresolved; reconcile before any new submission')
    from batch_progression import evaluate_batch_progression
    for level in snapshot['levels']:
        if int(level.get('level_index',0))>=int(snapshot.get('current_level_index',0)):continue
        progress=registry.target_status(level['target_id'])
        blocks=progress.get('blocks',[])
        if (len(blocks)!=int(level['target_n'])//int(progress['chunk_n']) or
                any(not evaluate_batch_progression(registry,level['target_id'],b['logical_block_id'])['eligible']
                    for b in blocks)):
            raise RuntimeError('Checkpoint advanced past incomplete logical PUB evidence; operator review required')


def _reconciliation_utc(value):
    """Require timezone evidence and normalize offsets, never guess local timezone."""
    if callable(value): value=value()
    if isinstance(value,str): value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("Reconciliation requires a timezone-aware timestamp")
    return value.astimezone(timezone.utc)


def _reconciliation_instance(value, service):
    """Only resolve documented instance names via the provider's name/CRN list."""
    if value is None or value == "":return None
    if not isinstance(value,str):raise RuntimeError("Instance must be a string or missing")
    if value.startswith("crn:"):return value
    matches={item["crn"] for item in service.instances() if item.get("name")==value and item.get("crn")}
    if len(matches)!=1:raise RuntimeError("Instance name has no unique accessible CRN")
    return matches.pop()


def _compare_reconciliation_instances(intent, active, service, *, label):
    if active is None or active == "":raise RuntimeError("Active provider instance is missing")
    if not isinstance(active,str) or (intent is not None and not isinstance(intent,str)):
        raise RuntimeError("Invalid instance type")
    if intent is None or intent == "":return False
    if intent==active:return True
    if _reconciliation_instance(intent,service)!=_reconciliation_instance(active,service):
        raise RuntimeError("Active saved-profile instance differs from submission intent")
    return True


def verify_reconciliation_job(row, job, service, *, root=None):
    """Fail closed on identity, immutable PUB inputs and requested treatment options."""
    from datetime import timedelta
    from dataclasses import asdict
    import numpy as np
    from qiskit.primitives.containers import EstimatorPub
    from vqe_adapter import verify_frozen_inputs, treatment_options
    from repository_paths import frozen_source, source_hashes
    if job.backend().name != row["backend"]: raise RuntimeError("Reconciliation backend mismatch")
    session=getattr(job,"session_id",None)
    if callable(session): session=session()
    if not row.get("batch_id") or str(session)!=row["batch_id"]:
        raise RuntimeError("Reconciliation Batch ID missing or mismatched")
    active=service.active_instance()
    intent_present=_compare_reconciliation_instances(row["metadata"].get("instance"),active,service,
        label=row["treatment_id"])
    provider_instance=getattr(job,"instance",None)
    if callable(provider_instance):provider_instance=provider_instance()
    if provider_instance is not None:
        if not _compare_reconciliation_instances(provider_instance,active,service,label="provider job "+str(job.job_id())):
            raise RuntimeError("Provider job instance is missing")
    if not intent_present and (not row.get("job_id") or row["job_id"]!=job.job_id() or provider_instance in (None,"")):
        raise RuntimeError("Missing intent instance requires exact recorded job ID and provider job instance")
    recorded_id=row.get("job_id")
    if recorded_id and job.job_id()!=recorded_id:
        raise RuntimeError("Reconciliation recorded job ID mismatch")
    started=row["metadata"].get("submit_started_utc")
    if not started and not recorded_id:
        raise RuntimeError("Unmapped job has no recorded submission intent timestamp")
    started=_reconciliation_utc(started or row["created_at"])
    created=_reconciliation_utc(getattr(job,"creation_date",None))
    # Compare the represented second, allowing only sub-second truncation.
    # Offset conversion is exact; no multi-second clock-skew allowance.
    if created < started.replace(microsecond=0):
        raise RuntimeError("Reconciliation creation timestamp precedes submission intent")
    if source_hashes(frozen_source(root)) != row["input_hashes"]:
        raise RuntimeError("Reconciliation frozen hashes mismatch")
    qc,obs,theta,physical,_,_=verify_frozen_inputs(root)
    if len(theta)!=21 or len(obs)!=7 or tuple(physical)!=(140,141,142,143,144,145,146):
        raise RuntimeError("Reconciliation frozen input dimensions mismatch")
    inputs=job.inputs
    pubs=inputs.get("pubs",[])
    if len(pubs)!=row["chunk_n"]: raise RuntimeError("Reconciliation PUB count mismatch")
    expected=EstimatorPub.coerce((qc,obs))
    for raw in pubs:
        pub=EstimatorPub.coerce(raw)
        if (pub.circuit!=expected.circuit or
            pub.observables.tolist()!=expected.observables.tolist() or
            not np.array_equal(pub.parameter_values.as_array(),expected.parameter_values.as_array()) or
            pub.precision!=expected.precision):
            raise RuntimeError("Reconciliation circuit, theta, observables or precision mismatch")
    requested=asdict(treatment_options(row["treatment_id"],4096,root))
    actual={**inputs.get("options",{})}
    if "resilience_level" in inputs: actual["resilience_level"]=inputs["resilience_level"]
    def compare(want,got,path="options"):
        for key,value in want.items():
            if key=="_VERSION" or type(value).__name__=="UnsetType": continue
            if isinstance(value,dict):
                compare(value,got.get(key,{}) if isinstance(got,dict) else {},path+"."+key)
            elif not isinstance(got,dict) or key not in got or (
                    list(value)!=list(got[key]) if isinstance(value,(tuple,list)) else value!=got[key]):
                raise RuntimeError("Reconciliation treatment mismatch: "+path+"."+key)
    # Runtime may normalize logging, tags and privacy metadata. These do not
    # define a scientific treatment. All scientific option trees stay strict.
    requested.pop("environment",None)
    requested.pop("max_execution_time",None)
    compare(requested,actual)
    return {"batch_id":str(session),"job_creation_date":created.isoformat(),
            "frozen_inputs_verified":True,"treatment_verified":row["treatment_id"],"pub_count":len(pubs),
            "identity_basis":"recorded_job_id" if recorded_id else "submission_intent_and_scientific_inputs",
            "submit_started_utc":started.isoformat()}


def _write_reconciled_record(path, payload):
    """Publish complete JSON create-only; a crash cannot expose a truncated record."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name("."+uuid.uuid4().hex+".reconcile.tmp")
    try:
        with temp.open("x",encoding="utf-8") as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2,default=str)
            stream.flush();os.fsync(stream.fileno())
        os.link(temp,path)  # atomic, fails if historical destination already exists
    finally:
        if temp.exists():temp.unlink()


def _check_reconciliation_records(registry, block, root, *, candidate_row=None, candidate_id=None):
    """Check local evidence before any SQLite mutation; never overwrite conflicts."""
    from vqe_adapter import record_directory
    output=record_directory(root,block)
    for row in registry.rows(block):
        expected_id=candidate_id if row["id"]==candidate_row else row["job_id"]
        for filename in ("job_submitted.json","submission_state.json"):
            path=output/row["treatment_id"]/filename
            if not path.exists(): continue
            old=json.loads(path.read_text(encoding="utf-8"))
            for key,value in (("logical_block_id",block),("treatment_id",row["treatment_id"]),
                              ("account_label",row["account_label"]),("backend",row["backend"]),
                              ("input_hashes",row["input_hashes"]),("job_id",expected_id)):
                if old.get(key) is not None and old[key]!=value:
                    raise RuntimeError("Reconciliation local record conflict: "+str(path)+" field "+key)


@_locked_reconciliation
def recover_interrupted_block(registry: JobRegistry, logical_block_id: str, *, root=None,
                              confirm_process_stopped: bool = False, normalize_interrupted: bool = True,
                              treatments=None) -> dict:
    """Reconcile local restart state only after the operator confirms the submitter stopped."""
    if not confirm_process_stopped:
        raise RuntimeError("Confirm the prior submit process has stopped before recovery")
    _check_reconciliation_records(registry,logical_block_id,root)
    if normalize_interrupted:
        registry.mark_interrupted_submissions_unknown(logical_block_id)
    from vqe_adapter import record_directory
    output=record_directory(root,logical_block_id)
    recovered=[]
    for row in registry.rows(logical_block_id):
        if treatments is not None and row["treatment_id"] not in treatments:continue
        treatment=row["treatment_id"]
        folder=output/treatment
        submitted=folder/"job_submitted.json"
        state=folder/"submission_state.json"
        meta=row["metadata"]
        if submitted.exists():
            existing=json.loads(submitted.read_text(encoding="utf-8"))
            if existing.get("job_id")!=row["job_id"]:
                raise RuntimeError(f"Registry/submission-record job ID disagreement: {submitted}")
        if row["job_id"]:
            payload={"job_id":row["job_id"],**meta,"logical_block_id":logical_block_id,
                     "treatment":treatment,"backend":row["backend"],"account_label":row["account_label"],
                     "batch_id":row["batch_id"],"input_hashes":row["input_hashes"]}
            if not submitted.exists():
                _write_reconciled_record(submitted,payload)
        if state.exists():
            old=json.loads(state.read_text(encoding="utf-8"))
            if (old.get("logical_block_id") not in (None,logical_block_id) or
                old.get("treatment_id") not in (None,treatment) or
                old.get("account_label") not in (None,row["account_label"]) or
                old.get("backend") not in (None,row["backend"]) or
                old.get("input_hashes") not in (None,row["input_hashes"]) or
                old.get("job_id") not in (None,row["job_id"])):
                raise RuntimeError(f"Registry/submission-state provenance disagreement: {state}")
            if old.get("status")!=row["submission_state"] or old.get("job_id")!=row["job_id"]:
                old.setdefault("reconciliation_history",[]).append({k:old.get(k) for k in ("status","job_id","updated_utc")})
            old.update({"status":row["submission_state"],"job_id":row["job_id"],"updated_utc":row["updated_at"]})
            if old.get("attempt_id"):
                _update_owned(state,old["attempt_id"],old)
        else:
            _write_reconciled_record(state,{"attempt_id":"recovered-"+str(row["id"]),"logical_block_id":logical_block_id,
                "treatment_id":treatment,"status":row["submission_state"],"job_id":row["job_id"],
                "backend":row["backend"],"account_label":row["account_label"],"input_hashes":row["input_hashes"],
                "updated_utc":row["updated_at"]})
        recovered.append({"treatment_id":treatment,"state":row["submission_state"],"job_id":row["job_id"]})
    return {"logical_block_id":logical_block_id,"jobs":recovered}


@_locked_reconciliation
def reconcile_unknown_job(registry: JobRegistry, row_id: int, service, *, root=None,
                          job_id: str | None = None, confirm_job_matches: bool = False,
                          confirm_no_matching_job: bool = False, quiet_period_seconds: float = 120.0) -> dict:
    """Resolve UNKNOWN only by verifying an IBM job or confirming a complete no-match search."""
    row=next((item for item in registry.rows() if item["id"]==row_id),None)
    if row is None: raise KeyError(row_id)
    if row["submission_state"] not in ("UNKNOWN","SUBMITTING","RESERVED"):
        raise RuntimeError("Only unresolved submission intents require reconciliation")
    from datetime import datetime, timedelta
    started=row["metadata"].get("submit_started_utc") or row.get("created_at")
    if not started: raise RuntimeError("Unknown submission has no recorded submit timestamp for provider reconciliation")
    created_after=datetime.fromisoformat(started.replace("Z","+00:00"))-timedelta(seconds=5)
    created_before=datetime.now(timezone.utc)+timedelta(seconds=5)
    if job_id:
        if not confirm_job_matches: raise RuntimeError("Review IBM job details and set confirm_job_matches=True")
        job=service.job(job_id)
        backend=job.backend()
        backend_name=getattr(backend,"name",None)
        if backend_name != row["backend"]: raise RuntimeError("Reconciliation job backend mismatch")
        evidence=verify_reconciliation_job(row,job,service,root=root)
        if any(r["job_id"]==job_id and r["id"]!=row_id for r in registry.rows()):
            raise RuntimeError("Provider job already belongs to another registry intent")
        _check_reconciliation_records(registry,row["logical_block_id"],root,candidate_row=row_id,candidate_id=job_id)
        registry.link_reconciled_job(row,job_id,{**evidence,"reconciled_utc":_stamp()})
        recover_interrupted_block.__wrapped__(registry,row["logical_block_id"],root=root,confirm_process_stopped=True)
        return {"state":"SUBMITTED","job_id":job_id}
    if not confirm_no_matching_job:
        raise RuntimeError("First inspect candidate IBM jobs; require explicit confirmation after a complete empty result")
    age=(created_before-datetime.fromisoformat(started.replace("Z","+00:00"))).total_seconds()
    if age < quiet_period_seconds:
        raise RuntimeError(f"Wait {quiet_period_seconds-age:.0f}s for Runtime job listing consistency before confirming absence")
    if service.active_instance()!=row["metadata"].get("instance"):
        raise RuntimeError("Reconciliation instance mismatch")
    matches=sum(1 for _ in iter_provider_jobs(service,backend_name=row["backend"],instance=row["metadata"].get("instance"),
                         created_after=created_after,created_before=created_before,descending=False))
    if matches:
        raise RuntimeError(f"Found {matches} candidate jobs in the recorded submit interval; inspect and reconcile by job ID")
    _check_reconciliation_records(registry,row["logical_block_id"],root)
    registry.transition(row_id,"CONFIRMED_NOT_SUBMITTED",release_reservation=True,
        metadata={"reconciled_utc":_stamp(),"reconciliation":"empty IBM job search in recorded submit interval; operator confirmed"},
        event_type="CONFIRMED_NO_SUBMISSION",details={"created_after":created_after.isoformat(),"created_before":created_before.isoformat()})
    recover_interrupted_block.__wrapped__(registry,row["logical_block_id"],root=root,confirm_process_stopped=True)
    return {"state":"CONFIRMED_NOT_SUBMITTED","job_id":None}


def reconcile_and_collect_block(root, campaign_id, logical_block_id, account_label,
                                batch_id, job_assignments, *, service_factory=None, treatments=None):
    """Operator CLI: verify live evidence, reconcile identities, collect; never submit.

    A single campaign lock spans the live audit and all local writes. Unassigned
    intents remain unresolved. SQLite commits precede atomic submission-file
    repair, which is replayable after a crash. No campaign checkpoint is cleared.
    """
    from worker_runtime import WorkerLock
    from independent_n_campaign import campaign_root
    from account_config import load_saved_account_profiles, make_saved_profile_service
    from vqe_adapter import collect_block, record_directory
    from batch_progression import valid_scientific_values
    import hashlib
    database=JobRegistry.default_database(root)
    with WorkerLock(campaign_root(root,campaign_id)/"worker.lock"):
        reader=JobRegistry(database,readonly=True)
        rows=reader.rows(logical_block_id)
        if len(rows)!=8 or len({r["treatment_id"] for r in rows})!=8:
            raise RuntimeError("Expected one existing intent per treatment; reconcile attempts manually")
        for row in rows:
            if (reader.independent_campaign_context(row["target_id"])[0]!=campaign_id or
                row["account_label"]!=account_label or row["backend"]!="ibm_fez" or row["batch_id"]!=batch_id or row["chunk_n"]!=5):
                raise RuntimeError("Campaign, account, Batch, backend or PUB-count mismatch")
        mapping={r["treatment_id"]:r["job_id"] for r in rows if r["job_id"]}
        for treatment,job_id in job_assignments.items():
            if treatment not in {r["treatment_id"] for r in rows}:raise ValueError("Unknown treatment")
            if treatment in mapping and mapping[treatment]!=job_id:raise RuntimeError("Cannot replace historical job ID")
            mapping[treatment]=job_id
        # This command intentionally leaves M7 unresolved for the seven-job incident.
        expected={"M0_Baseline","M1_DD","M2_TREX","M3_ZNE","M4_DD_TREX","M5_DD_ZNE","M6_TREX_ZNE"}
        if set(mapping)!=expected or len(set(mapping.values()))!=7:
            raise RuntimeError("Expected exactly seven distinct M0-M6 identities; M7 needs separate evidence")
        profile=load_saved_account_profiles((account_label,))[account_label]
        service=service_factory(profile) if service_factory else make_saved_profile_service(profile)
        active_instance=service.active_instance()
        for row in rows:
            if row["treatment_id"] not in mapping:continue  # M7 is not being reconciled
            present=_compare_reconciliation_instances(row["metadata"].get("instance"),active_instance,service,
                label=row["treatment_id"])
            if not present and not row["job_id"]:
                raise RuntimeError("Missing intent instance on an unmapped job; no identity can be inferred")
        jobs=iter_provider_jobs(service,backend_name="ibm_fez",instance=active_instance,session_id=batch_id)
        ids=[j.job_id() for j in jobs]
        if len(ids)!=7 or set(ids)!=set(mapping.values()):
            raise RuntimeError("Live Batch listing is not exactly the seven proposed jobs; no changes committed")
        verified={}; evidence={}
        for row in rows:
            treatment=row["treatment_id"]
            if treatment not in mapping:continue
            # Refetch by exact ID, never infer treatment from list order.
            job=service.job(mapping[treatment])
            if job.job_id()!=mapping[treatment]:raise RuntimeError("Provider returned a different job ID")
            if str(job.status()).upper() not in ("DONE","COMPLETED","JOBSTATUS.DONE"):
                raise RuntimeError("All seven jobs must be DONE before reconciliation/collection")
            evidence[treatment]=verify_reconciliation_job(row,job,service,root=root)
            _check_reconciliation_records(reader,logical_block_id,root,candidate_row=row["id"],candidate_id=job.job_id())
            verified[job.job_id()]=job
        selected=set(mapping) if treatments is None else set(treatments)
        if not selected or not selected.issubset(mapping):
            raise ValueError("Collection selection must contain verified treatments only")
        if treatments is not None and any(not r["job_id"] for r in rows if r["treatment_id"] in selected):
            raise RuntimeError("Targeted collection requires an already reconciled job ID")
        registry=JobRegistry(database)
        for row in rows:
            name=row["treatment_id"]
            if name not in selected:continue
            if not row["job_id"]:
                registry.link_reconciled_job(row,mapping[name],{**evidence[name],"reconciled_utc":_stamp()})
        # Reuse durable file repair, under the already-held campaign lock.
        recover_interrupted_block.__wrapped__(registry,logical_block_id,root=root,confirm_process_stopped=True,
            normalize_interrupted=False,treatments=tuple(selected))

        # Return the verified provider object unchanged: the canonical collector
        # archives its response before the adapter validates individual PUBs.
        # Validating inside job.result() would lose the evidence on failure.
        class CollectionReads:
            def job(self,job_id):
                if job_id not in verified:raise RuntimeError("Collection refused unverified job ID")
                return verified.pop(job_id)
            def active_instance(self):return service.active_instance()

        def complete(row):
            records=[p for p in registry.pub_validation_records(row["target_id"]) if p["job_row_id"]==row["id"]]
            if len(records)!=5 or {p["pub_index"] for p in records}!={1,2,3,4,5}:return False
            for record in records:
                if not record["expectation_valid"] or record["uncertainty_valid"]!=1 or not valid_scientific_values({
                        "expectation_values":json.loads(record["expectation_values_json"] or "null"),
                        "standard_deviations":json.loads(record["standard_deviations_json"] or "null")}):return False
            raw=Path(row["metadata"].get("per_pub_source_path",""))
            digest=row["metadata"].get("per_pub_source_sha256")
            return (row["submission_state"]=="COMPLETED" and row["validated_pub_count"]==5 and raw.is_file()
                    and hashlib.sha256(raw.read_bytes()).hexdigest()==digest
                    and all(p["source_sha256"]==digest for p in records))
        failures={}
        for row in registry.rows(logical_block_id):
            name=row["treatment_id"]
            if name not in selected or complete(row):continue
            try:
                collect_block(root,logical_block_id,shots=4096,n=5,account_label=account_label,
                    ibm_account=account_label,instance=row["metadata"].get("instance"),registry=registry,
                    service_override=CollectionReads(),ai_mode="python",treatments=(name,))
            except Exception as exc:
                import traceback
                diagnostic={"job_id":row["job_id"],"treatment":name,
                    "traceback":"".join(traceback.format_exception(type(exc),exc,exc.__traceback__))}
                payload=json.dumps(diagnostic,sort_keys=True).encode("utf-8")
                path=record_directory(root,logical_block_id)/name/("collection_failure_"+hashlib.sha256(payload).hexdigest()+".json")
                if not path.exists():_write_reconciled_record(path,diagnostic)
                failures[name]=diagnostic["traceback"]+"\nDiagnostic: "+str(path)
        output=[]
        for row in registry.rows(logical_block_id):
            name=row["treatment_id"]
            records=[p for p in registry.pub_validation_records(row["target_id"]) if p["job_row_id"]==row["id"]]
            raw=Path(row["metadata"].get("per_pub_source_path",""))
            digest=row["metadata"].get("per_pub_source_sha256")
            archive_verified=raw.is_file() and hashlib.sha256(raw.read_bytes()).hexdigest()==digest
            valid_indices={p["pub_index"] for p in records if archive_verified and p["source_sha256"]==digest
                and 1<=p["pub_index"]<=5 and p["expectation_valid"] and p["uncertainty_valid"]==1
                and valid_scientific_values({"expectation_values":json.loads(p["expectation_values_json"] or "null"),
                    "standard_deviations":json.loads(p["standard_deviations_json"] or "null")})}
            valid=len(valid_indices) if name in mapping else 0
            if name in mapping and valid!=5 and name not in failures:
                failures[name]="Five validated PUBs and their immutable archive are not available; inspect collection evidence"
            output.append({"treatment":name,"job_id":row["job_id"],"provider_status":"DONE" if name in mapping else "UNRESOLVED",
                "local_state":row["submission_state"],"validated_pubs":valid,"unvalidated_pubs":5-valid,
                "unvalidated_pub_indices":sorted(set(range(1,6))-valid_indices),
                "action_required":failures.get(name) or ("None" if valid==5 else "Resolve provider identity/absence" if name not in mapping else "Inspect collection evidence")})
        return {"campaign":campaign_id,"block":logical_block_id,"account":account_label,
            "jobs":output,"collection_failures":failures,"progression_ready":False,
            "safety_gate":"M7 remains unresolved; campaign checkpoint unchanged","submission_performed":False}


def submit_block(manifest: ExperimentManifest, registry: JobRegistry, service: FakeRuntimeService,
                 *, logical_block_id: str, account_label: str, circuit: Any,
                 observables: list[Any], parameter_values: list[float], estimator_factory=FakeEstimatorV2,
                 treatment_options_provider=None,
                 quota_reservation_seconds: float = 1.0) -> list[str]:
    """Submit exactly eight independent fake Estimator jobs in one fake Batch."""
    if not isinstance(service, FakeRuntimeService):
        raise LiveSubmissionDisabled("Phase 3A allows FakeRuntimeService only")
    if manifest.backend != "ibm_fez" or manifest.source_run != "run_02":
        raise ValueError("Manifest is not pinned to the validated run_02/ibm_fez input")
    if len(manifest.treatments) != 8 or len(observables) != 7:
        raise ValueError("Expected eight treatments and seven observables")
    if len(parameter_values) != 21:
        raise ValueError("Expected the frozen 21-angle vector")
    if quota_reservation_seconds <= 0:
        raise ValueError("A positive per-job quota reservation estimate is required")
    if treatment_options_provider is None:
        from vqe_adapter import treatment_options as treatment_options_provider
    usage = service.usage(account_label)  # explicit actual-usage read before reservation/submission
    jobs: list[tuple[str, int]] = []
    for treatment in manifest.treatments:
        row_id = registry.reserve_batch(logical_block_id=logical_block_id, account_label=account_label,
            backend=manifest.backend, treatments=[treatment["id"]], input_hashes=manifest.input_hashes,
            quota_seconds_per_job=quota_reservation_seconds * manifest.n, actual_usage_seconds=usage,
            n=manifest.n, replicate_blocks=manifest.replicate_blocks)[treatment["id"]]
        registry.transition(row_id, "SUBMITTING")
        jobs.append((treatment["id"], row_id))
    ids = []
    try:
        with service.batch_context(backend=manifest.backend):
            batch_mode = service.batch(backend=manifest.backend)
            for treatment_id, row_id in jobs:
                options = treatment_options_provider(treatment_id, manifest.shots_per_pub)
                pub = (circuit, observables, [parameter_values])
                pubs = [pub] * manifest.n
                job = estimator_factory(mode=batch_mode, options=options).run(pubs)
                job_id = job.job_id()
                registry.transition(row_id, "SUBMITTED", job_id=job_id)
                ids.append(job_id)
    except Exception as exc:
        # Any exception after a submit attempt can be ambiguous. Do not release reservations or retry.
        for _, row_id in jobs[len(ids):]:
            registry.transition(row_id, "UNKNOWN", event_type="BATCH_SUBMISSION_INTERRUPTED", details={"error_type":type(exc).__name__,"automatic_retry":False})
        raise
    if len(ids) != 8:
        raise RuntimeError("Batch did not create exactly eight treatment jobs")
    return ids


def submit_runtime_block(manifest: ExperimentManifest, registry: JobRegistry, service, *,
                         logical_block_id: str, account_label: str, instance: str | None,
                         reservation_seconds_per_job: float, safety_margin_seconds: float = 10.0,
                         runtime_estimate=None,
                         authorize_live: bool = False, preflight_verified: bool = False,
                         quota_budget_seconds: float = 600.0,
                         local_budget_remaining_seconds: float | None = None,
                         adaptive_target_context: dict[str, Any] | None = None,
                         batch_factory=None, estimator_factory=None,
                         root=None, pilot=None, manual_selection=None) -> list[str]:
    """Submit M0 through M7 inside one IBM Batch; no submission unless authorized.

    `service`, Batch and Estimator can be injected for offline integration tests. The
    production defaults are imported only after `authorize_live=True`.
    """
    if not authorize_live:
        raise LiveSubmissionDisabled("Live QPU submission is disabled; pass explicit authorization")
    if not preflight_verified:
        raise RuntimeError("Run the separate read-only IBM Batch preflight before submission")
    # Explicit manual selections share this exact submission/reservation engine.
    # Original campaign calls do not enter this branch or change their policy.
    manual = manual_selection is not None
    if manual:
        if (not isinstance(manual_selection,dict) or set(manual_selection)!={'treatments','mode','pub_labels'}
                or not logical_block_id.startswith('manual-') or adaptive_target_context is None
                or not adaptive_target_context.get('target_id','').startswith('manual-')):
            raise ValueError('Manual selection requires its isolated manual target and block')
        selected=manual_selection['treatments'];labels=manual_selection['pub_labels']
        if (not isinstance(selected,list) or not selected or len(selected)!=len(set(selected))
                or set(selected)-{t['id'] for t in manifest.treatments}
                or manual_selection['mode'] not in ('batch','job')
                or (manual_selection['mode']=='job' and len(selected)!=1)
                or not isinstance(labels,list) or len(labels)!=manifest.n or len(labels)!=len(set(labels))
                or any(type(v) is not int or not 1<=v<=30 for v in labels)):
            raise ValueError('Invalid exact manual treatment/PUB selection')
    # An operator-approved provisional envelope is allowed only for the
    # original full-Batch workload. Replacement jobs retain their own gate.
    verified = bool(getattr(runtime_estimate, "defensible", False))
    provisional = (
        getattr(runtime_estimate, "operator_approved_provisional", False) is True
        and getattr(runtime_estimate, "scope", None) == "full_batch"
        and getattr(runtime_estimate, "target_n", None) == 5
        and getattr(runtime_estimate, "safety_factor", None) == 1.25
    )
    if runtime_estimate is None:
        raise RuntimeError("Original-Batch workload description is missing")
    if provisional and (
        manifest.backend != "ibm_fez" or manifest.source_run != "run_02"
        or manifest.n != 5 or manifest.shots_per_pub != 4096
    ):
        raise RuntimeError("Provisional planning is restricted to the approved frozen 5-PUB workload")
    if getattr(runtime_estimate, "target_n", None) != manifest.n:
        raise RuntimeError("Runtime calibration PUB count differs from the manifest")
    if not isinstance(reservation_seconds_per_job, (int,float)) or reservation_seconds_per_job <= 0:
        raise ValueError("A positive per-PUB minimum quota estimate is required")
    per_treatment_estimates = [item.get("stress_envelope_seconds")
        for item in getattr(runtime_estimate, "per_treatment", {}).values()]
    manifest_treatment_ids={item["id"] for item in manifest.treatments}
    if not manual and (len(per_treatment_estimates) != 8 or any(value is None for value in per_treatment_estimates)
            or set(getattr(runtime_estimate,"per_treatment",{})) != manifest_treatment_ids):
        raise RuntimeError("Complete eight-treatment runtime envelope is required before submission")
    if not logical_block_id or any(c in logical_block_id for c in "/\\:"):
        raise ValueError("logical_block_id must be one safe path component")
    if manifest.source_run != "run_02" or manifest.backend != "ibm_fez":
        raise ValueError("Manifest must remain pinned to Fixed ISA run_02 / ibm_fez")
    if len(manifest.treatments) != 8:
        raise ValueError("Expected eight independent mitigation treatment jobs")
    _assert_bundle_matches(manifest)
    if batch_factory is None or estimator_factory is None:
        real_batch, real_estimator = _runtime_types()
        batch_factory = batch_factory or real_batch
        estimator_factory = estimator_factory or real_estimator
    from vqe_adapter import verify_frozen_inputs, treatment_options, record_directory
    if pilot is None:
        from vqe_adapter import load_pilot
        pilot=load_pilot(root)
    qc, obs, theta, physical, _, _ = verify_frozen_inputs(root)
    if len(theta) != 21 or len(obs) != 7 or tuple(physical) != tuple(manifest.physical_qubits):
        raise RuntimeError("Frozen input verification differs from the manifest")
    try:
        backend=service.backend(manifest.backend)
        actual_instance=_active_instance(service,instance)
        # Query all backends; an external pending job can spend this same allocation.
        if service.jobs(limit=1,pending=True,instance=actual_instance):
            raise RuntimeError("Provider quota has pending workloads; reconcile before submission")
        fields=provider_quota_fields(service.usage())
        if service.active_instance()!=actual_instance:
            raise RuntimeError("Provider quota instance changed before submission")
    except Exception as exc:
        from quota import AccountUnavailable
        raise AccountUnavailable("Read-only provider refresh failed before Batch reservation: "+type(exc).__name__) from exc
    reservations=provider_reservations(registry)[account_label]
    if reservations["ambiguous"]:
        raise RuntimeError("Provider quota reservation overlap is ambiguous")
    measured_usage=fields["consumed_seconds"]
    # Kept for legacy callers; campaign callers pass only the per-account cap.
    # The total campaign allocation is monitoring-only.
    internal_headroom=(local_budget_remaining_seconds if local_budget_remaining_seconds is not None
        else max(0.0,float(quota_budget_seconds)-measured_usage))
    remaining=available_quota_seconds(provider_remaining_seconds=fields["remaining_seconds"],
        outstanding_reservations_seconds=reservations["reserved_seconds"],
        local_budget_remaining_seconds=internal_headroom)
    active_reservations=reservations["reserved_seconds"]
    quota_gate=workload_quota_decision(runtime_estimate,remaining_seconds=remaining,
        workload_type="original_batch",safety_margin_seconds=safety_margin_seconds,
        provider_remaining_seconds=fields["remaining_seconds"])
    if not quota_gate["eligible"]:
        from quota import AccountUnavailable
        raise AccountUnavailable("Original Batch quota gate refused: "+"; ".join(quota_gate["reasons"]))
    treatment_ids=selected if manual else [item["id"] for item in manifest.treatments]
    record_dir=record_directory(root,logical_block_id)
    if not (record_dir/"round_plan.json").is_file():
        raise FileNotFoundError("Run the VQE adapter's prepare-block phase before submission")
    existing_rows=registry.rows(logical_block_id)
    latest={}
    for row in existing_rows:
        if row["account_label"] != account_label or row["backend"] != manifest.backend or row["input_hashes"] != manifest.input_hashes:
            raise RuntimeError("Logical block account, backend, or frozen inputs conflict with prior registry rows")
        if row["treatment_id"] not in latest or row["attempt"] > latest[row["treatment_id"]]["attempt"]:
            latest[row["treatment_id"]]=row
    pending=[]
    for treatment in treatment_ids:
        folder=record_dir/treatment
        submitted=folder/"job_submitted.json"
        state=folder/"submission_state.json"
        prior=latest.get(treatment)
        if prior is None or prior["submission_state"]=="CONFIRMED_NOT_SUBMITTED":
            pending.append(treatment)
            if submitted.exists(): raise FileExistsError(f"Job file exists without active registry state: {submitted}")
            if prior is None and state.exists(): raise RuntimeError(f"Unregistered treatment state requires reconciliation: {state}")
            if prior and not state.is_file(): raise RuntimeError(f"Confirmed no-submission state file is missing: {state}")
        elif prior["submission_state"] in ("SUBMITTED","COMPLETED"):
            if not submitted.is_file(): raise RuntimeError(f"Registry job ID has no engine record; run restart recovery: {submitted}")
        else:
            raise RuntimeError(f"Treatment {treatment} is unresolved ({prior['submission_state']}); reconcile before retry")
    if not pending: return []
    if any(row["batch_id"] or row["metadata"].get("batch_context_entered_utc") or
           row["submission_state"] in ("SUBMITTED", "COMPLETED") for row in existing_rows):
        raise RuntimeError("This logical block already has a Runtime Batch/job; completing missing treatments in another Batch would violate one-block/one-Batch semantics")
    estimate_by_treatment=runtime_estimate.per_treatment
    # Reserve the eight distinct treatment envelopes, not eight copies of
    # the largest treatment envelope (which would over-reserve quota).
    reservation_by_treatment={
        treatment: float(estimate_by_treatment[treatment]["stress_envelope_seconds"] or 0) if manual else float(estimate_by_treatment[treatment]["stress_envelope_seconds"])
        for treatment in pending
    }
    if any(not math.isfinite(value) or (value < 0 if manual else value <= 0)
           for value in reservation_by_treatment.values()):
        raise RuntimeError("Invalid per-treatment runtime reservation")
    if not manual and len(pending) == len(manifest_treatment_ids) and not math.isclose(
        sum(reservation_by_treatment.values()), float(runtime_estimate.stress_block_seconds),
        rel_tol=1e-9, abs_tol=1e-6
    ):
        raise RuntimeError("Treatment reservations do not match the approved full-Batch envelope")
    rows=registry.reserve_batch(logical_block_id=logical_block_id,account_label=account_label,
        backend=manifest.backend,treatments=pending,input_hashes=manifest.input_hashes,
        quota_seconds_per_job=0,actual_usage_seconds=measured_usage,
        budget_seconds=quota_budget_seconds,safety_margin_seconds=safety_margin_seconds,
        available_budget_seconds=remaining,expected_active_reserved_seconds=active_reservations,
        n=manifest.n,replicate_blocks=manifest.replicate_blocks,
        quota_seconds_by_treatment=reservation_by_treatment,
        adaptive_target=adaptive_target_context)
    estimated_by_treatment=({t:estimate_by_treatment[t]['stress_envelope_seconds'] for t in pending} if manual else dict(reservation_by_treatment))
    if manual:
        for treatment,row_id in rows.items():
            registry.transition(row_id,'RESERVED',metadata={'manual_execution':True,
                'execution_mode':manual_selection['mode'],'pub_labels':labels,
                'estimated_runtime_seconds':estimated_by_treatment[treatment]})
    reservation_by_treatment={name:quota_gate["reservation_seconds"]/len(pending) for name in pending}
    state_paths={}
    attempt_id=uuid.uuid4().hex
    try:
        for index,treatment in enumerate(pending):
            folder=record_dir/treatment
            state=folder/"submission_state.json"
            stamp=_stamp()
            state_payload={"status":"RESERVED","attempt_id":attempt_id,"logical_block_id":logical_block_id,
                "treatment_id":treatment,"account_label":account_label,"instance":actual_instance,"backend":manifest.backend,
                "input_hashes":manifest.input_hashes,"created_utc":stamp,"updated_utc":stamp,
                "n":manifest.n,"pubs_per_treatment":manifest.n,"replicate_blocks":manifest.replicate_blocks,
                "requested_shots_per_pub":manifest.shots_per_pub,
                "quota_reservation_seconds_per_treatment":reservation_by_treatment[treatment],
                "estimated_runtime_seconds":estimated_by_treatment[treatment],
                "safety_margin_seconds":safety_margin_seconds,"quota_policy":quota_gate["quota_policy"],
                "submission_order":index,"execution_order":"not_assumed"}
            if latest.get(treatment): _start_reconciled_retry(state,state_payload)
            else: _write_new(state,state_payload)
            state_paths[treatment]=state
    except Exception:
        for row_id in rows.values():
            registry.transition(row_id,"CONFIRMED_NOT_SUBMITTED",release_reservation=True,
                event_type="LOCAL_PREPARATION_FAILED_BEFORE_RUNTIME_SUBMIT",details={"automatic_retry":False})
        for state in state_paths.values():
            try:
                _update_owned(state,attempt_id,{**json.loads(state.read_text(encoding="utf-8")),"status":"CONFIRMED_NOT_SUBMITTED"})
            except Exception: pass
        raise
    returned=[]
    attempted_index=None
    batch_id=None
    try:
        from contextlib import nullcontext
        with (nullcontext(backend) if manual and manual_selection['mode']=='job' else batch_factory(backend=backend)) as batch:
            session_id=getattr(batch,"session_id",None)
            batch_id=str(session_id) if session_id else None
            batch_entered=_stamp()
            for row_id in rows.values():
                registry.transition(row_id,"RESERVED",batch_id=batch_id,
                    metadata={"runtime_job_context_entered_utc" if manual and manual_selection['mode']=='job' else "batch_context_entered_utc":batch_entered,"batch_id":batch_id},
                    event_type="RUNTIME_JOB_CONTEXT_ENTERED" if manual and manual_selection['mode']=='job' else "RUNTIME_BATCH_CONTEXT_ENTERED",details={"batch_id":batch_id,"backend":manifest.backend})
            for index,treatment in enumerate(pending):
                row_id=rows[treatment]
                state=state_paths[treatment]
                prior=json.loads(state.read_text(encoding="utf-8"))
                submitting={**prior,"status":"SUBMITTING","submit_started_utc":_stamp()}
                _update_owned(state,attempt_id,submitting)
                registry.transition(row_id,"SUBMITTING",batch_id=batch_id,metadata={"submit_started_utc":submitting["submit_started_utc"],"submission_order":index,"instance":actual_instance})
                options=treatment_options(treatment,manifest.shots_per_pub,root)
                estimator=estimator_factory(mode=batch,options=options)
                # Repeated PUBs mirror the existing pilot's repeats semantics; circuit remains bound.
                attempted_index=index
                pub=(qc,obs)
                job=estimator.run([pub] * manifest.n)
                job_id=job.job_id()
                if not isinstance(job_id,str) or not job_id.strip():
                    raise RuntimeError('Runtime returned no verified job ID; submission is ambiguous')
                submitted_at=_stamp()
                metadata={"logical_block_id":logical_block_id,"treatment_id":treatment,"backend":manifest.backend,
                    "account_label":account_label,"instance":actual_instance,"batch_id":batch_id,
                    "submitted_client_utc":submitted_at,"submission_order":index,"execution_order":"not_assumed",
                    "shots_requested_per_pub":manifest.shots_per_pub,"n":manifest.n,
                    "pubs_per_treatment":manifest.n,"replicate_blocks":manifest.replicate_blocks,
                    "input_hashes":manifest.input_hashes}
                if manual:metadata.update(manual_execution=True,execution_mode=manual_selection['mode'],pub_labels=labels)
                # Persist the returned job ID before fetching optional metadata or writing artifacts.
                registry.transition(row_id,"SUBMITTED",job_id=job_id,batch_id=batch_id,metadata=metadata)
                returned.append((treatment,job_id,row_id,metadata))
                metadata.update({"job_creation_date":_safe_value(getattr(job,"creation_date",None)),
                                 "runtime_options_requested":_safe_value(options)})
                registry.transition(row_id,"SUBMITTED",metadata={"job_creation_date":metadata["job_creation_date"],
                    "runtime_options_requested":metadata["runtime_options_requested"]})
                _write_new(record_dir/treatment/"job_submitted.json",{"job_id":job_id,**metadata})
                _update_owned(state,attempt_id,{**submitting,"status":"SUBMITTED","job_id":job_id,"updated_utc":_stamp()})
    except BaseException as exc:
        for index,treatment in enumerate(pending):
            if treatment in {item[0] for item in returned}: continue
            row_id=rows[treatment]
            current=registry.rows(logical_block_id)
            row=next(item for item in current if item["id"]==row_id)
            if row["submission_state"] in ("RESERVED","SUBMITTING"):
                # The currently invoked run() is ambiguous; later never-attempted jobs are safe to release.
                ambiguous=(row["submission_state"]=="SUBMITTING" and index==attempted_index)
                state_name="UNKNOWN" if ambiguous else "CONFIRMED_NOT_SUBMITTED"
                registry.transition(row_id,state_name,release_reservation=not ambiguous,
                    event_type="BATCH_SUBMIT_AMBIGUOUS" if ambiguous else "BATCH_NOT_ATTEMPTED",
                    details={"error_type":type(exc).__name__,"automatic_retry":False,"reconciliation_required":ambiguous})
                try:
                    old=json.loads(state_paths[treatment].read_text(encoding="utf-8"))
                    _update_owned(state_paths[treatment],attempt_id,{**old,"status":state_name,"updated_utc":_stamp(),"error_type":type(exc).__name__})
                except Exception:
                    pass
        raise
    if len(returned) != len(pending): raise RuntimeError("Batch did not return an ID for every reserved treatment")
    return [job_id for _,job_id,_,_ in returned]
