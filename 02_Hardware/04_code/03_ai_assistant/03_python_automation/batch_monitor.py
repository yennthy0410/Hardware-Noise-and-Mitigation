"""Read-only IBM Runtime Batch monitoring and validated-result inspection."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


from datetime import datetime, timezone
from typing import Any

from job_registry import JobRegistry
from quota import actual_usage_from_response, qpu_seconds_from_metrics
from vqe_adapter import validate_runtime_result


def classify_runtime_error(exc: BaseException, *, phase: str = "monitor") -> str:
    """Return a stable failure class without persisting potentially sensitive text."""
    name=type(exc).__name__.lower(); msg=str(exc).lower()
    text=f"{name} {msg}"
    from worker_runtime import transient_read_error,resource_exhaustion_error
    if phase!='submit' and resource_exhaustion_error(exc):return "RESOURCE_WAIT"
    if phase!='submit' and transient_read_error(exc):return 'NETWORK'
    if phase!='submit' and (isinstance(exc,OSError) or 'archive hash conflict' in text or 'archive staging hash mismatch' in text):return 'LOCAL_ARCHIVE_IO'
    if any(token in text for token in ("authentication","unauthorized","forbidden","credential","invalid token","401")):
        return "AUTHENTICATION"
    if any(token in text for token in ("403","not authorized","authorization failed")):
        return "AUTHENTICATION"
    if any(token in text for token in ("quota","usage limit","resource exhausted","429")):
        return "QUOTA"
    if phase=="usage": return "QUOTA"
    if any(token in text for token in ("backend unavailable","backend not operational","backend is unavailable","backendnotfound","job backend differs","runtime instance differs","maintenance")):
        return "BACKEND"
    if "session id differs" in text: return "BATCH_ID_MISMATCH"
    if phase=="submit" and any(token in text for token in ("timeout","timed out","connection reset","network")):
        return "SUBMISSION_AMBIGUITY"
    if any(token in text for token in ("timeout","timed out","connection","network","ssl","temporarily unavailable")):
        return "NETWORK"
    return "JOB_FAILURE" if phase=="job" else "RESULT_CORRUPTION"


def _status(value: Any) -> str:
    raw=getattr(value,"name",None) or getattr(value,"value",None) or str(value)
    return str(raw).upper().split(".")[-1]


def _archive_monitor_result(root, logical_block_id, treatment, result):
    """Keep diagnostic reads immutable before attempting scientific decoding."""
    import hashlib,os
    from vqe_adapter import serialize_runtime_result_archive,record_directory
    raw=serialize_runtime_result_archive(result)
    digest=hashlib.sha256(raw).hexdigest()
    folder=record_directory(root,logical_block_id)/treatment/'raw_monitor_retrievals'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/f'job_result_{digest}.json'
    from repository_paths import atomic_archive
    atomic_archive(path,(raw,),digest)
    return {'monitor_raw_archive_path':str(path),'monitor_raw_archive_sha256':digest}


def _safe_timestamp(job, metrics: dict) -> dict[str, Any]:
    timestamps=metrics.get("timestamps") if isinstance(metrics,dict) else None
    creation=getattr(job,"creation_date",None)
    if callable(creation):
        try: creation=creation()
        except Exception: creation=None
    return {"creation":str(creation) if creation is not None else None,
            "runtime":timestamps if isinstance(timestamps,(dict,list,str,int,float)) else None,
            "observed_utc":datetime.now(timezone.utc).isoformat()}


def _latest_treatment_rows(rows: list[dict]) -> list[dict]:
    latest={}
    for row in rows:
        old=latest.get(row["treatment_id"])
        if old is None or int(row.get("attempt",1))>int(old.get("attempt",1)):
            latest[row["treatment_id"]]=row
    return list(latest.values())


def record_monitor_failure(registry: JobRegistry, logical_block_id: str, exc: BaseException,
                          *, phase: str = "monitor") -> dict[str, Any]:
    """Durably classify a read-only monitor setup/authentication failure."""
    rows=_latest_treatment_rows(registry.rows(logical_block_id))
    if not rows: raise KeyError(logical_block_id)
    account=rows[0]["account_label"];backend=rows[0]["backend"]
    batch_ids={row["batch_id"] for row in rows if row.get("batch_id")}
    category=classify_runtime_error(exc,phase=phase)
    batch_id=next(iter(batch_ids)) if len(batch_ids)==1 else None
    snapshot={"logical_block_id":logical_block_id,"batch_id":batch_id,"recorded_batch_ids":sorted(batch_ids),
        "account_label":account,"backend":backend,"checked_utc":datetime.now(timezone.utc).isoformat(),
        "failure_class":category,"error_type":type(exc).__name__,"submission_performed":False,
        "cancellation_performed":False}
    registry.record_monitor_snapshot(logical_block_id=logical_block_id,batch_id=batch_id,
        account_label=account,backend=backend,status=category,snapshot=snapshot,
        events=[{"job_row_id":None,"event_type":category,"details":{"phase":phase,"error_type":type(exc).__name__}}])
    return snapshot


def monitor_batch(registry: JobRegistry, service, logical_block_id: str, *,
                  treatments: tuple[str,...] | None = None, chunk_n: int | None = None,
                  expected_hashes: dict[str,str] | None = None,
                  quota_scope: str = "provider-reported-scope-unverified", root=None) -> dict[str, Any]:
    """Poll one recorded Batch and jobs. It never submits, cancels, or retries."""
    all_rows=registry.rows(logical_block_id)
    if not all_rows: raise KeyError(logical_block_id)
    rows=_latest_treatment_rows(all_rows)
    account=rows[0]["account_label"]; backend=rows[0]["backend"]
    hashes=rows[0]["input_hashes"]
    if any(row["account_label"]!=account or row["backend"]!=backend or row["input_hashes"]!=hashes for row in rows):
        raise RuntimeError("Block contains inconsistent account, backend, or frozen-input identity")
    if expected_hashes is not None and hashes!=expected_hashes:
        raise RuntimeError("Registry frozen-input hashes differ from the requested manifest")
    recorded_instances={row.get("metadata",{}).get("instance") for row in rows if row.get("metadata",{}).get("instance")}
    active_instance=service.active_instance() if callable(getattr(service,"active_instance",None)) else None
    if recorded_instances and active_instance and recorded_instances!={str(active_instance)}:
        raise RuntimeError("Selected Runtime instance differs from the recorded Batch instance")
    wanted=tuple(treatments or (row["treatment_id"] for row in sorted(rows,key=lambda item:item["treatment_id"])))
    if len(wanted)!=8 or len(set(wanted))!=8:
        raise ValueError("Monitoring requires exactly eight distinct M0-M7 treatments")
    by_treatment={row["treatment_id"]:row for row in rows}
    if set(by_treatment)!=set(wanted):
        raise RuntimeError("Recorded Batch does not contain exactly the expected eight treatments")
    if chunk_n is None:
        chunk_n=next((int(row["chunk_n"]) for row in rows if row.get("chunk_n") is not None),
                     next((int(row["n"]) for row in rows if row.get("n") is not None),None))
    if chunk_n is None or chunk_n<1:
        raise RuntimeError("Cannot determine the recorded PUB chunk size")
    batch_ids={row["batch_id"] for row in rows if row.get("batch_id")}
    if len(batch_ids)>1: raise RuntimeError("The eight jobs have conflicting Runtime Batch IDs")
    batch_id=next(iter(batch_ids),None)

    job_reports=[]; events=[]; all_done=True; all_valid=True; any_failure=False; any_ambiguous=False
    observed_batch_ids=set(); observed_batch_by_treatment={}
    for treatment in wanted:
        result=job=None  # release the preceding SDK result before retrieving another
        row=by_treatment[treatment]; job_id=row.get("job_id")
        if not job_id:
            state=row["submission_state"]
            category="SUBMISSION_AMBIGUITY" if state in ("UNKNOWN","SUBMITTING") else "INCOMPLETE_PUBS"
            any_ambiguous|=category=="SUBMISSION_AMBIGUITY"; all_done=False; all_valid=False
            report={"treatment_id":treatment,"job_id":None,"submission_state":state,
                    "status":"UNKNOWN" if category=="SUBMISSION_AMBIGUITY" else "NOT_SUBMITTED",
                    "failure_class":category,"automatic_retry":False}
            job_reports.append(report); events.append({"job_row_id":row["id"],"event_type":category,"details":report})
            continue
        try:
            job=service.job(job_id)
        except Exception as exc:
            import traceback

            print(
                f"\nMONITOR ERROR | Treatment={treatment} | Job={job_id}",
                flush=True
            )
            traceback.print_exc()

            category=classify_runtime_error(exc)
            report={"treatment_id":treatment,"job_id":job_id,"status":"UNAVAILABLE",
                    "failure_class":category,"error_type":type(exc).__name__}
            job_reports.append(report); events.append({"job_row_id":row["id"],"event_type":category,"details":report})
            all_done=False; all_valid=False; any_failure|=category in ("AUTHENTICATION","QUOTA","BACKEND","JOB_FAILURE","BATCH_ID_MISMATCH")
            continue
        try:
            state=_status(job.status())
            backend_reader=getattr(job,"backend",None)
            if callable(backend_reader):
                remote_backend=backend_reader()
                remote_backend=getattr(remote_backend,"name",remote_backend)
                if remote_backend and str(remote_backend)!=backend:
                    raise ValueError("Runtime job backend differs from the recorded Batch backend")
            reported_batch=getattr(job,"session_id",None)
            if callable(reported_batch):
                try: reported_batch=reported_batch()
                except Exception: reported_batch=None
            reported_batch=str(reported_batch) if reported_batch else None
            if reported_batch: observed_batch_ids.add(reported_batch)
            observed_batch_by_treatment[treatment]=reported_batch
            if batch_id and reported_batch and reported_batch!=batch_id:
                raise ValueError("Runtime job session ID differs from the recorded Batch ID")
            metrics=job.metrics() if callable(getattr(job,"metrics",None)) else {}
            if not isinstance(metrics,dict): metrics={}
            usage=qpu_seconds_from_metrics(metrics)
            timestamps=_safe_timestamp(job,metrics)
            record={"status":state,"job_creation_date":timestamps["creation"],"timestamps":timestamps["runtime"],
                    "observed_utc":timestamps["observed_utc"],"qpu_usage_seconds":usage,
                    "runtime_session_id":reported_batch}
            normalized="COMPLETED" if state in ("DONE","COMPLETED") else ("FAILED" if state in ("ERROR","FAILED") else ("CANCELLED" if state=="CANCELLED" else "SUBMITTED"))
            if state in ("ERROR","FAILED","CANCELLED"):
                all_done=False; all_valid=False; any_failure=True
                category="JOB_FAILURE"
                record["failure_class"]=category
            elif normalized=="COMPLETED":
                all_done &= True
                try:
                    result=job.result()
                    record.update(_archive_monitor_result(root,logical_block_id,treatment,result))
                    try: returned_count=len(result)
                    except Exception: returned_count=0
                    if row.get("target_id") and returned_count:
                        registry.update_pub_progress(row["id"],completed=min(returned_count,chunk_n),
                            event_type="REMOTE_PUB_RESULT_COUNT_RETRIEVED")
                    validation=validate_runtime_result(result,chunk_n,f"{treatment}/{job_id}")
                    record.update(validation)
                except ValueError as exc:
                    message=str(exc)
                    category="INCOMPLETE_PUBS" if "expected " in message and " PUBs, got " in message else "RESULT_CORRUPTION"
                    record.update({"failure_class":category,"validation_error":message})
                    all_valid=False; any_failure=True
                except Exception as exc:
                    category=classify_runtime_error(exc)
                    record.update({"failure_class":category,"error_type":type(exc).__name__})
                    all_valid=False; any_failure|=category in ("AUTHENTICATION","QUOTA","BACKEND","JOB_FAILURE","BATCH_ID_MISMATCH")
            else:
                all_done=False; all_valid=False; category="IN_PROGRESS"
            prior_state=row["submission_state"]
            if normalized!=prior_state or usage is not None or record.get("failure_class") or (reported_batch and not row.get("batch_id")):
                transition_state=normalized
                if normalized=="SUBMITTED" and prior_state=="UNKNOWN": transition_state="SUBMITTED"
                registry.transition(row["id"],transition_state,batch_id=reported_batch,qpu_usage_seconds=usage,
                    metadata={"last_remote_monitor":record},event_type="REMOTE_JOB_MONITORED",
                    details={"status":state,"qpu_usage_seconds":usage,"failure_class":record.get("failure_class")})
            report={"treatment_id":treatment,"job_id":job_id,**record}
            job_reports.append(report)
            events.append({"job_row_id":row["id"],"event_type":record.get("failure_class","REMOTE_JOB_MONITORED"),"details":report})
        except Exception as exc:
            category=classify_runtime_error(exc)
            report={"treatment_id":treatment,"job_id":job_id,"status":"UNAVAILABLE",
                    "failure_class":category,"error_type":type(exc).__name__}
            job_reports.append(report);events.append({"job_row_id":row["id"],"event_type":category,"details":report})
            all_done=False;all_valid=False;any_failure|=category in ("AUTHENTICATION","QUOTA","BACKEND","JOB_FAILURE","BATCH_ID_MISMATCH")

    usage_snapshot=None; usage_error=None
    if callable(getattr(service,"usage",None)):
        try:
            raw=service.usage(); consumed=actual_usage_from_response(raw)
            registry.record_account_usage(account_label=account,consumed_seconds=consumed,
                quota_scope=quota_scope,logical_block_id=logical_block_id)
            usage_snapshot={"consumed_seconds":consumed,"quota_scope":quota_scope,
                            "source":"Runtime service.usage","scope_verified":quota_scope=="account_wide_verified"}
        except Exception as exc:
            usage_error={"failure_class":classify_runtime_error(exc,phase="usage"),"error_type":type(exc).__name__}
            events.append({"job_row_id":None,"event_type":"QUOTA_RECONCILIATION_UNAVAILABLE","details":usage_error})

    current=registry.rows(logical_block_id)
    current_latest=_latest_treatment_rows(current)
    stage_validated=bool(current_latest) and all(int(item.get("validated_pub_count") or 0)==chunk_n for item in current_latest)
    if not batch_id and len(observed_batch_by_treatment)==8 and all(observed_batch_by_treatment.values()) and len(observed_batch_ids)==1:
        batch_id=next(iter(observed_batch_ids))
        for row in rows:
            if not row.get("batch_id") and row.get("job_id"):
                registry.transition(row["id"],row["submission_state"],batch_id=batch_id,
                    metadata={"batch_id_recovered_from_job_session":True},
                    event_type="BATCH_ID_RECOVERED_FROM_ALL_EIGHT_JOB_SESSIONS",
                    details={"batch_id":batch_id,"automatic_retry":False})
    stored_batch_ids={row.get("batch_id") for row in rows}
    batch_identity_verified=(len(rows)==8 and batch_id is not None and
        (stored_batch_ids=={batch_id} or
         (len(observed_batch_by_treatment)==8 and all(value==batch_id for value in observed_batch_by_treatment.values()))))
    if len(rows)!=8 or not batch_identity_verified: block_status="INCOMPLETE"
    elif observed_batch_ids and observed_batch_ids!={batch_id}: block_status="INCOMPLETE"
    elif any_ambiguous: block_status="INCOMPLETE"
    elif any_failure: block_status="INCOMPLETE"
    elif stage_validated: block_status="VALIDATED"
    elif all_done and all_valid: block_status="READY_TO_COLLECT"
    elif all_done: block_status="INCOMPLETE"
    else: block_status="IN_PROGRESS"
    block_quota_reserved=sum(float(row.get("quota_reserved_seconds") or 0) for row in rows)
    observed_job_usage=sum(float(row.get("qpu_usage_seconds") or 0) for row in registry.rows(logical_block_id)
                           if row.get("qpu_usage_seconds") is not None)
    snapshot={"logical_block_id":logical_block_id,"batch_id":batch_id,"account_label":account,
        "backend":backend,"input_hashes":hashes,"expected_treatments":list(wanted),"chunk_n":chunk_n,
        "checked_utc":datetime.now(timezone.utc).isoformat(),"batch_identity_verified":batch_identity_verified,
        "batch_status_source":"derived_from_eight_recorded_job_statuses","jobs":job_reports,
        "quota_usage":usage_snapshot,"quota_usage_error":usage_error,
        "quota_reconciliation":{"provider_consumed_seconds":usage_snapshot["consumed_seconds"] if usage_snapshot else None,
            "provider_scope":quota_scope,"block_recorded_job_usage_seconds":observed_job_usage,
            "remaining_block_reservations_seconds":block_quota_reserved,
            "automatically_released_reservation":False},
        "submission_performed":False,"cancellation_performed":False}
    registry.record_monitor_snapshot(logical_block_id=logical_block_id,batch_id=batch_id,
        account_label=account,backend=backend,status=block_status,snapshot=snapshot,events=events)
    # Provider execution remains unchanged; scientific readiness is independent.
    target_id=next((row.get("target_id") for row in rows if row.get("target_id")),None)
    if target_id:
        from batch_progression import evaluate_batch_progression
        decision=evaluate_batch_progression(registry,target_id,logical_block_id)
        snapshot.update(dataset_status=decision["dataset_status"],
                        progression_eligible=decision["eligible"],progression_decision=decision)
    return snapshot|{"block_status":block_status,"persisted_events":len(events)}
