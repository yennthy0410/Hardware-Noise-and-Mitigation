"""Adaptive cumulative PUB targets around complete eight-job Runtime Batches.

This module plans and analyzes; it does not create IBM services or enable live
submission. A block with fewer than eight submitted treatments stays incomplete.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


from datetime import datetime, timezone
import json
import hashlib
import math
import os
from pathlib import Path
from typing import Any, Iterable

from batch_progression import evaluate_batch_progression
from account_config import ACCOUNT_LABELS
from experiment_manifest import TargetManifest, build_target_manifest
from runtime_estimator import RuntimeEstimate, quota_decision

FRESH_PREFLIGHT_SECONDS=600
SAFETY_MARGIN_SECONDS=60.0
PLOT_CONTRACT_VERSION=4



def _verify_png_contract(path: Path, *, layout: str, min_short_side: int = 1000,
                         required_dpi: float = 600.0) -> dict[str, Any]:
    from scientific_exports import inspect_png
    return inspect_png(path, layout=layout, min_short_side=min_short_side,
                       required_dpi=required_dpi)


def register_target(manifest: TargetManifest, registry) -> dict[str,Any]:
    registry.register_target(target_id=manifest.target_id,target_n=manifest.target_n,
        chunk_n=manifest.chunk_n,manifest_sha256=manifest.sha256,input_hashes=manifest.input_hashes)
    status=registry.target_status(manifest.target_id)
    validated=status.get("repaired_validated_pubs_by_treatment",{})
    min_validated=min(validated.values(),default=0)
    prior_blocks=status.get("blocks",[])
    blockers=[]
    if any(not evaluate_batch_progression(registry,manifest,item["logical_block_id"])["eligible"] and
           item.get("retry_resolution")!="RECONCILED_NOT_SUBMITTED" for item in prior_blocks):
        blockers.append("an earlier adaptive Batch is incomplete or unresolved; reconcile it before another Batch")
    if len(set(validated.values()))>1:
        blockers.append("treatments have unequal cumulative validated PUB counts")
    remaining=max(0,manifest.target_n-min_validated)
    planned=(remaining//manifest.chunk_n) if remaining else 0
    return {"manifest":manifest.to_dict(),"manifest_sha256":manifest.sha256,"status":status,
        "cumulative_validated_pubs_per_treatment":min_validated,
        "remaining_validated_pubs_per_treatment":remaining,
        "additional_complete_blocks_required":planned,"blockers":blockers}


def _freshness(report: dict[str,Any], now: datetime | None = None) -> tuple[bool,str]:
    try:
        checked=datetime.fromisoformat(report["checked_utc"].replace("Z","+00:00"))
    except (KeyError,TypeError,ValueError):
        return False,"preflight timestamp unavailable or invalid"
    if checked.tzinfo is None:
        return False,"preflight timestamp must include timezone"
    age=((now or datetime.now(timezone.utc))-checked).total_seconds()
    if age<0 or age>FRESH_PREFLIGHT_SECONDS:
        return False,"preflight is not fresh (maximum age 600 seconds)"
    return True,"fresh read-only preflight"


def remaining_seconds_from_report(report: dict[str,Any]) -> float | None:
    usage=usage_fields_from_report(report)
    return usage["remaining_seconds"] if usage else None


def usage_fields_from_report(report: dict[str,Any]) -> dict[str,float] | None:
    from quota import report_quota_fields
    try:
        return report_quota_fields(report)
    except (ValueError, TypeError, KeyError):
        return None


def evaluate_new_batch(manifest: TargetManifest, *, account_label: str,
                       preflight_report: dict[str,Any] | None,
                       runtime_estimate: RuntimeEstimate | None,
                       remaining_seconds: float | None = None,
                       safety_margin_seconds: float = SAFETY_MARGIN_SECONDS,
                       now: datetime | None = None) -> dict[str,Any]:
    """Fail-closed readiness report; all checks are local data only."""
    reasons=[];checks={"authorized_account":account_label in ACCOUNT_LABELS,
        "backend":manifest.backend=="ibm_fez","frozen_source":manifest.source_run=="run_02",
        "fresh_preflight":False,"configured_instance":False,"preflight_backend":False,"frozen_hashes":False,
        "isa_compatibility":False,"batch_compatibility":False,"provider_usage":False,
        "quota_scope":False,"pending_jobs_reconciled":False,
        "runtime_estimate_matches_chunk":False,"defensible_runtime_bound":False,"quota_headroom":False}
    if not checks["authorized_account"]: reasons.append("account label is not authorized")
    if not checks["backend"] or not checks["frozen_source"]: reasons.append("plan is not pinned to run_02 on ibm_fez")
    report=preflight_report or {}
    if report.get("account_label")!=account_label: reasons.append("preflight account does not match selected account")
    fresh,why=_freshness(report,now)
    checks["fresh_preflight"]=fresh and report.get("status")=="PASS" and report.get("submission") is False and report.get("submission_eligible") is True
    if not checks["fresh_preflight"]: reasons.append(why if not fresh else "latest preflight is not PASS/read-only")
    report_checks=report.get("checks",{}) if isinstance(report.get("checks"),dict) else {}
    checks["configured_instance"]=report_checks.get("configured_instance")=="PASS" and report_checks.get("accessible_instances")=="PASS"
    if not checks["configured_instance"]: reasons.append("configured IBM instance/accessibility did not PASS")
    checks["preflight_backend"]=report.get("backend")==manifest.backend and report_checks.get("backend_ibm_fez")=="PASS" and report_checks.get("backend_operational")=="PASS"
    if not checks["preflight_backend"]: reasons.append("preflight did not PASS the manifest backend and operational status")
    frozen=report.get("frozen_input_validation",{}) if isinstance(report.get("frozen_input_validation"),dict) else {}
    checks["frozen_hashes"]=frozen.get("source_run")==manifest.source_run and frozen.get("input_sha256")==manifest.input_hashes
    if not checks["frozen_hashes"]: reasons.append("preflight run_02 input hashes differ from the target manifest")
    checks["isa_compatibility"]=report_checks.get("isa_compatibility")=="PASS"
    if not checks["isa_compatibility"]: reasons.append("preflight ISA compatibility did not PASS")
    sdk=report.get("sdk_batch_compatibility",{})
    checks["batch_compatibility"]=isinstance(sdk,dict) and sdk.get("available") is True and sdk.get("batch_created") is False
    if not checks["batch_compatibility"]: reasons.append("Runtime Batch SDK compatibility is unavailable or was not read-only")
    checks["provider_usage"]=report_checks.get("usage")=="PASS" and report_checks.get("provider_quota")=="PASS" and usage_fields_from_report(report) is not None
    if not checks["provider_usage"]: reasons.append("provider usage and limit fields are missing or did not PASS")
    checks["quota_scope"]=report.get("account_wide_usage_verified") is True and "single_accessible_instance" in str(report.get("usage_scope",""))
    if not checks["quota_scope"]: reasons.append("account-wide quota scope is not verified")
    pending=report.get("pending_jobs_count")
    checks["pending_jobs_reconciled"]=isinstance(pending,int) and pending==0 and report_checks.get("pending_jobs")=="PASS"
    if not checks["pending_jobs_reconciled"]: reasons.append("pending jobs are nonzero or unavailable; reconcile before another Batch")
    if remaining_seconds is None and checks["fresh_preflight"] and report.get("account_label")==account_label:
        remaining_seconds=remaining_seconds_from_report(report)
    scope=getattr(runtime_estimate,"scope",None)
    checks["runtime_estimate_matches_chunk"]=bool(runtime_estimate and scope in (None,"full_batch")
        and runtime_estimate.target_n==manifest.chunk_n)
    if not checks["runtime_estimate_matches_chunk"]: reasons.append("runtime estimate workload scope/count does not match the requested action")
    from runtime_estimator import workload_quota_decision
    checks["defensible_runtime_bound"]=bool(runtime_estimate and (
        getattr(runtime_estimate,"defensible",False) or getattr(runtime_estimate,"operator_approved_provisional",False)))
    quota=workload_quota_decision(runtime_estimate,remaining_seconds=remaining_seconds,
        workload_type="original_batch",
        safety_margin_seconds=safety_margin_seconds,
        provider_remaining_seconds=remaining_seconds_from_report(report))
    eligible_quota=quota["eligible"]
    checks["quota_headroom"]=eligible_quota
    if not eligible_quota:reasons.extend(quota["reasons"])
    diagnostics={"defensible_runtime_bound":checks.pop("defensible_runtime_bound"),"runtime_warnings":quota["warnings"]}
    return {"eligible":all(checks.values()),"checks":checks,"runtime_diagnostics":diagnostics,"reasons":list(dict.fromkeys(reasons)),
        "account_label":account_label,"backend":manifest.backend,"target_id":manifest.target_id,
        "target_n":manifest.target_n,"chunk_n":manifest.chunk_n,"runtime_bound_seconds":getattr(runtime_estimate,"stress_block_seconds",None),
        "quota":quota,"batch_created":False,"submission":False,
        "batch_submission_eligible":all(checks.values()),
        "recovery_submission_eligible":False,
        "remaining_provider_quota_seconds":remaining_seconds_from_report(report),
        "estimated_submission_runtime_seconds":getattr(runtime_estimate,"stress_block_seconds",None),
        "required_runtime_with_margin_seconds":quota["required_runtime_with_margin_seconds"],
        "account_selection_priority":report.get("account_selection_priority"),
        "rejection_reason":"; ".join(dict.fromkeys(reasons)) or None}


def reserve_next_block(manifest: TargetManifest, registry, *, account_label: str,
                       preflight_report: dict[str,Any], runtime_estimate: RuntimeEstimate,
                       safety_margin_seconds: float = SAFETY_MARGIN_SECONDS,
                       now: datetime | None = None) -> dict[str,Any]:
    """Durably reserve one future Batch after fresh quota/runtime gates pass.

    This records intent only. It never constructs a Runtime Batch or submits jobs.
    """
    fresh,_=_freshness(preflight_report,now)
    usage=(usage_fields_from_report(preflight_report) if fresh and preflight_report.get("status")=="PASS"
           and preflight_report.get("submission_eligible") is True and preflight_report.get("account_label")==account_label else None)
    gate=evaluate_new_batch(manifest,account_label=account_label,preflight_report=preflight_report,
        runtime_estimate=runtime_estimate,remaining_seconds=usage["remaining_seconds"] if usage else None,
        safety_margin_seconds=safety_margin_seconds,now=now)
    if not gate["eligible"]: raise RuntimeError("Adaptive Batch refused: "+"; ".join(gate["reasons"]))
    progress=register_target(manifest,registry)
    if progress["blockers"]: raise RuntimeError("Adaptive Batch refused: "+"; ".join(progress["blockers"]))
    status=progress["status"]
    validated=status.get("repaired_validated_pubs_by_treatment",{})
    if any(validated.get(name,0)>=manifest.target_n for name in manifest.treatment_ids):
        raise RuntimeError("Adaptive target already reached")
    blocks=status.get("blocks",[])
    retry=bool(blocks and blocks[-1].get("retry_resolution")=="RECONCILED_NOT_SUBMITTED"
               and blocks[-1].get("retryable_not_submitted") is True)
    sequence=int(blocks[-1]["sequence_no"]) if retry else len(blocks)+1
    if sequence>manifest.blocks_required: raise RuntimeError("Adaptive target has no remaining planned block")
    logical_block_id=(blocks[-1]["logical_block_id"] if retry else f"{manifest.target_id}-block-{sequence:03d}")
    reservations={name:float(runtime_estimate.per_treatment[name]["stress_envelope_seconds"])
                  for name in manifest.treatment_ids}
    if (any(not math.isfinite(x) or x<=0 for x in reservations.values()) or
            not math.isclose(sum(reservations.values()),runtime_estimate.stress_block_seconds,abs_tol=1e-6)):
        raise RuntimeError("Treatment reservations must match the complete Batch runtime estimate")
    from quota import provider_reservations,available_quota_seconds
    snapshot=provider_reservations(registry)[account_label]
    if snapshot["ambiguous"]:raise RuntimeError("Provider quota reservation overlap is ambiguous")
    available=available_quota_seconds(provider_remaining_seconds=usage["remaining_seconds"],
        outstanding_reservations_seconds=snapshot["reserved_seconds"])
    row_ids=registry.reserve_batch(logical_block_id=logical_block_id,account_label=account_label,
        backend=manifest.backend,treatments=list(manifest.treatment_ids),input_hashes=manifest.input_hashes,
        quota_seconds_per_job=0,quota_seconds_by_treatment=reservations,
        actual_usage_seconds=usage["consumed_seconds"],budget_seconds=usage["limit_seconds"],
        available_budget_seconds=available,expected_active_reserved_seconds=snapshot["reserved_seconds"],
        safety_margin_seconds=safety_margin_seconds,n=manifest.chunk_n,replicate_blocks=1,
        adaptive_target={"target_id":manifest.target_id,"target_n":manifest.target_n,"chunk_n":manifest.chunk_n,
            "manifest_sha256":manifest.sha256,"account_label":account_label,
            "runtime_upper_bound_seconds":runtime_estimate.stress_block_seconds,
            "runtime_bound_kind":"workload_runtime_estimate",
            "safety_margin_seconds":safety_margin_seconds,"preflight_checked_at":preflight_report["checked_utc"],
            "quota_scope":"account_wide_verified"})
    return {"logical_block_id":logical_block_id,"sequence_no":sequence,"row_ids":row_ids,
        "estimated_seconds_by_treatment":reservations,
        "reserved_seconds_by_treatment":{name:gate["quota"]["reservation_seconds"]/len(reservations) for name in reservations},"quota_gate":gate,"batch_created":False,"submission":False}


def submit_mock_target_block(manifest: TargetManifest, registry, service, *, logical_block_id: str,
                             circuit: Any, observables: list[Any], estimator_factory=None,
                             treatment_options_provider=None) -> list[str]:
    """Offline-only Batch lifecycle simulation using FakeRuntimeService.

    This boundary intentionally cannot accept QiskitRuntimeService. It tests job
    ordering, durable IDs, and partial failure recovery without IBM access.
    """
    from batch_scheduler import FakeRuntimeService, FakeEstimatorV2
    from job_registry import utc_now
    if not isinstance(service,FakeRuntimeService):
        raise TypeError("Adaptive Phase 6A scheduler accepts FakeRuntimeService only; live submission is disabled")
    if manifest.backend!="ibm_fez" or manifest.source_run!="run_02":
        raise ValueError("Adaptive target must remain pinned to run_02 on ibm_fez")
    if len(manifest.treatment_ids)!=8 or len(observables)!=7:
        raise ValueError("Expected eight M0-M7 treatments and seven observables")
    if len(getattr(circuit,"parameters",()))!=0:
        raise ValueError("Adaptive jobs require the bound frozen circuit with zero parameters")
    if treatment_options_provider is None:
        from vqe_adapter import treatment_options as treatment_options_provider
    estimator_factory=estimator_factory or FakeEstimatorV2
    all_rows=registry.rows(logical_block_id)
    latest={}
    for row in all_rows:
        if row["treatment_id"] not in latest or row["attempt"]>latest[row["treatment_id"]]["attempt"]:
            latest[row["treatment_id"]]=row
    rows=list(latest.values())
    if len(rows)!=8 or {row["target_id"] for row in rows}!={manifest.target_id}:
        raise RuntimeError("Adaptive Batch requires exactly eight pre-reserved treatment rows")
    if any(row["submission_state"]!="RESERVED" or row["job_id"] for row in rows):
        raise RuntimeError("Adaptive block already started or has unresolved submissions; reconcile before retry")
    old_attempts=[row for row in all_rows if row["attempt"]<max(item["attempt"] for item in rows)]
    if old_attempts and any(row["submission_state"]!="CONFIRMED_NOT_SUBMITTED" or row["job_id"] or row["batch_id"]
                            for row in old_attempts):
        raise RuntimeError("Adaptive retry requires every prior treatment attempt to be confirmed NOT_SUBMITTED")
    if any(row["account_label"]!=rows[0]["account_label"] or row["backend"]!=manifest.backend or row["chunk_n"]!=manifest.chunk_n for row in rows):
        raise RuntimeError("Adaptive block account/backend/chunk metadata is inconsistent")
    ordered=sorted(rows,key=lambda row:manifest.treatment_ids.index(row["treatment_id"]))
    returned=[]
    try:
        with service.batch_context(backend=manifest.backend) as batch:
            batch_mode=service.batch(backend=manifest.backend)
            batch_id=getattr(batch,"session_id",None)
            for row in ordered:
                registry.transition(row["id"],"RESERVED",batch_id=batch_id,
                    metadata={"batch_id":batch_id,"batch_context_entered_utc":utc_now(),
                        "target_id":manifest.target_id,"target_n":manifest.target_n,"chunk_n":manifest.chunk_n,
                        "treatment_order":list(manifest.treatment_ids),"execution_order":"not_assumed"},
                    event_type="ADAPTIVE_BATCH_CONTEXT_ENTERED",details={"batch_id":batch_id})
            for index,row in enumerate(ordered):
                treatment=row["treatment_id"]
                registry.transition(row["id"],"SUBMITTING",batch_id=batch_id,
                    metadata={"submit_started_utc":utc_now(),"submission_order":index})
                options=treatment_options_provider(treatment,manifest.shots_per_pub)
                job=estimator_factory(mode=batch_mode,options=options).run([(circuit,observables)]*manifest.chunk_n)
                job_id=str(job.job_id())
                # Persist the job ID immediately after the returned handle.
                registry.transition(row["id"],"SUBMITTED",job_id=job_id,batch_id=batch_id,
                    metadata={"submitted_client_utc":utc_now(),"submitted_pub_count":manifest.chunk_n,
                        "target_n":manifest.target_n,"chunk_n":manifest.chunk_n,
                        "treatment_options":str(options),"submission_order":index,
                        "execution_order":"not_assumed","input_hashes":manifest.input_hashes},
                    event_type="ADAPTIVE_JOB_ID_PERSISTED",details={"job_id":job_id,"batch_id":batch_id})
                returned.append(job_id)
    except Exception as exc:
        current=registry.rows(logical_block_id)
        for row in current:
            if row["submission_state"]=="SUBMITTING":
                registry.transition(row["id"],"UNKNOWN",event_type="ADAPTIVE_SUBMIT_AMBIGUOUS",
                    details={"error_type":type(exc).__name__,"automatic_retry":False})
            elif row["submission_state"]=="RESERVED":
                registry.transition(row["id"],"CONFIRMED_NOT_SUBMITTED",release_reservation=True,
                    event_type="ADAPTIVE_JOB_NOT_ATTEMPTED",details={"automatic_retry":False})
        raise
    if len(returned)!=8: raise RuntimeError("Adaptive fake Batch did not return eight job IDs")
    return returned


def aggregate_validated_blocks(blocks: Iterable[dict[str,Any]], treatments: tuple[str,...],
                              expected_per_treatment: int | None = None) -> dict[str,Any]:
    """Summarize validated per-PUB SSE/RMSE without recomputing scientific values.

    Each item supplies block_id, batch_id, account_label, and rows from the
    canonical mitigation_pilot ``per_repeat.csv``. Within-block variation is
    computed across its PUB metrics; between-block variation is computed across
    block means. These are kept separate in the returned tables.
    """
    import statistics
    rows=[]; per_block=[]; cumulative={t:0 for t in treatments}
    blocks=list(blocks)
    if not blocks: raise ValueError("No completed validated blocks supplied")
    frozen_hashes=None
    for block in blocks:
        required=("block_id","batch_id","account_label","rows","job_ids","input_hashes","created_at","updated_at")
        if any(not block.get(key) for key in required): raise ValueError("Block analysis input lacks provenance")
        if frozen_hashes is None: frozen_hashes=block["input_hashes"]
        elif block["input_hashes"]!=frozen_hashes:
            raise ValueError("Validated blocks have different frozen source hashes")
        grouped={t:[] for t in treatments}
        for row in block["rows"]:
            treatment=row.get("treatment")
            if treatment not in grouped: raise ValueError("Unexpected treatment in canonical analysis rows")
            try:
                sse,rmse=float(row["SSE"]),float(row["RMSE"])
            except (KeyError,TypeError,ValueError) as exc:
                raise ValueError("Canonical per-PUB SSE/RMSE values must be numeric") from exc
            if not math.isfinite(sse) or not math.isfinite(rmse):
                raise ValueError("Canonical per-PUB SSE/RMSE values must be finite")
            row={**row,"SSE":sse,"RMSE":rmse}
            local_pub=row.get("pub")
            cumulative[treatment]+=1
            enriched={**row,"pub_within_block":local_pub,"pub":cumulative[treatment],
                      "logical_block_id":block["block_id"],"batch_id":block["batch_id"],
                      "account_label":block["account_label"],"job_id":block["job_ids"].get(treatment),
                      "source_input_hashes_json":json.dumps(block["input_hashes"],sort_keys=True),
                      "block_created_at":block["created_at"],"block_updated_at":block["updated_at"]}
            grouped[treatment].append(enriched);rows.append(enriched)
        for treatment in treatments:
            values=grouped[treatment]
            if not values: raise ValueError(f"Validated block is missing {treatment}")
            if expected_per_treatment is not None and len(values)!=expected_per_treatment:
                raise ValueError(f"{block['block_id']}/{treatment}: expected {expected_per_treatment} validated PUBs, got {len(values)}")
            per_block.append({"logical_block_id":block["block_id"],"batch_id":block["batch_id"],
                "account_label":block["account_label"],"treatment":treatment,"validated_pubs":len(values),
                "job_id":block["job_ids"].get(treatment),"source_input_hashes_json":json.dumps(block["input_hashes"],sort_keys=True),
                "block_created_at":block["created_at"],"block_updated_at":block["updated_at"],
                "within_batch_mean_SSE":statistics.fmean(float(r["SSE"]) for r in values),
                "within_batch_std_SSE":statistics.stdev(float(r["SSE"]) for r in values) if len(values)>1 else None,
                "within_batch_mean_RMSE":statistics.fmean(float(r["RMSE"]) for r in values),
                "within_batch_std_RMSE":statistics.stdev(float(r["RMSE"]) for r in values) if len(values)>1 else None})
    treatment_summary=[]
    for treatment in treatments:
        pub_values=[row for row in rows if row["treatment"]==treatment]
        batch_values=[row for row in per_block if row["treatment"]==treatment]
        means=[float(row["within_batch_mean_SSE"]) for row in batch_values]
        rmse_means=[float(row["within_batch_mean_RMSE"]) for row in batch_values]
        treatment_summary.append({"treatment":treatment,"validated_pub_count":len(pub_values),
            "validated_block_count":len(batch_values),
            "pooled_mean_SSE":statistics.fmean(float(row["SSE"]) for row in pub_values),
            "between_batch_mean_SSE":statistics.fmean(means),
            "between_batch_std_SSE":statistics.stdev(means) if len(means)>1 else None,
            "pooled_mean_RMSE":statistics.fmean(float(row["RMSE"]) for row in pub_values),
            "between_batch_mean_RMSE":statistics.fmean(rmse_means),
            "between_batch_std_RMSE":statistics.stdev(rmse_means) if len(rmse_means)>1 else None,
            "between_batch_variance_estimable":len(means)>1})
    return {"per_pub":rows,"per_block":per_block,"summary":treatment_summary,
        "variation_note":"Within-batch PUB variation and between-batch variation are reported separately; no PUBs are synthesized."}


def _target_qpu_usage_provenance(root,manifest,registry):
    """Verify job-level QPU charges from immutable local archives only."""
    from repository_paths import MITIGATION_REL
    from targeted_recovery import repaired_dataset_status
    repo=Path(root).resolve();status=registry.target_status(manifest.target_id)
    repaired=repaired_dataset_status(manifest,registry)
    if repaired.get("analysis_eligible") and any(item.get("source")=="REPLACEMENT" for item in repaired.get("rows",[])):
        used_ids={str(item["job_id"]) for item in repaired["rows"] if item.get("job_id")}
    else:
        used_ids=set()
        for block in status.get("blocks",[]):
            if block.get("status") not in ("VALIDATED","COMPLETE"):continue
            latest={}
            for row in registry.rows(block["logical_block_id"]):
                if row["treatment_id"] not in latest or row["attempt"]>latest[row["treatment_id"]]["attempt"]:
                    latest[row["treatment_id"]]=row
            used_ids.update(str(row["job_id"]) for row in latest.values() if row.get("job_id"))
    candidates={}
    for row in registry.rows():
        if row.get("job_id") and str(row["job_id"]) in used_ids:candidates[str(row["job_id"])]=row
    for row in registry.replacement_jobs(manifest.target_id):
        if row.get("job_id") and str(row["job_id"]) in used_ids:candidates[str(row["job_id"])]=row
    records=[];by_job={}
    record_root=(repo/MITIGATION_REL/"output"/"03_IBM_execution_records").resolve()
    for job_id,row in sorted(candidates.items()):
        metadata=row.get("metadata",{}) or {}
        archive_path=row.get("raw_archive_path") or metadata.get("raw_archive_path") or metadata.get("source_archive_path")
        archive_hash=row.get("raw_archive_sha256") or metadata.get("raw_archive_sha256") or metadata.get("source_archive_sha256")
        item={"job_id":job_id,"treatment_id":row.get("treatment_id"),"account_label":row.get("account_label"),
            "backend":row.get("backend"),"batch_id":row.get("batch_id"),"usage_scope":"JOB_LEVEL",
            "usage_source":"IMMUTABLE_JOB_RESULT_ARCHIVE_PROVIDER_METRICS","verified":False,
            "qpu_usage_seconds":None,"archive_sha256":archive_hash,"observed_utc":None}
        try:
            path=Path(archive_path).resolve()
            if not archive_path or not archive_hash or record_root not in path.parents or not path.is_file():
                raise ValueError("registered raw archive path/hash is missing or outside execution records")
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if actual!=archive_hash:raise ValueError("registered raw archive SHA-256 does not match")
            raw=json.loads(path.read_text(encoding="utf-8-sig"))
            if raw.get("job_id") not in (None,job_id):raise ValueError("raw archive Job ID differs from registry")
            if raw.get("backend",row.get("backend"))!=manifest.backend:raise ValueError("raw archive backend differs from target")
            metrics=raw.get("provider_metrics") or {};usage=(metrics.get("usage") or {})
            value=usage.get("qpu_charge_time_seconds")
            direct=raw.get("qpu_usage_seconds")
            if value is None and direct is not None:value=direct
            if value is None or isinstance(value,bool) or not math.isfinite(float(value)) or float(value)<0:
                raise ValueError("immutable archive has no finite provider QPU usage")
            # The provider metric is the preferred source. A direct cached
            # value is accepted only when the archive itself records a
            # completed retrieval status; a bare number is not provenance.
            if usage.get("qpu_charge_time_seconds") is not None and usage.get("status") not in ("complete", "COMPLETED"):
                raise ValueError("provider usage metric is not marked complete in the immutable archive")
            if usage.get("qpu_charge_time_seconds") is None and raw.get("qpu_usage_status") not in ("complete", "COMPLETED"):
                raise ValueError("direct archived usage lacks a verified completion status")
            if direct is not None and not math.isclose(float(direct),float(value),rel_tol=0,abs_tol=1e-9):
                raise ValueError("provider metric conflicts with archived QPU usage field")
            item.update({"verified":True,"qpu_usage_seconds":float(value),
                "observed_utc":(usage.get("timestamps") or metrics.get("timestamps") or {}).get("finished")
                    or raw.get("collected_utc") or row.get("updated_at"),
                "archive_path":str(path),"provider_usage_status":usage.get("status"),
                "provider_usage_unit":usage.get("unit"),"provider_resource_value":usage.get("value")})
        except (OSError,ValueError,TypeError,json.JSONDecodeError) as exc:
            item["verification_error"]=str(exc)
        records.append(item);by_job[job_id]=item
    per_treatment={}
    for treatment in manifest.treatment_ids:
        selected=[row for row in records if row["treatment_id"]==treatment]
        complete=bool(selected) and all(row["verified"] for row in selected)
        per_treatment[treatment]={"selected_job_count":len(selected),
            "verified_job_count":sum(bool(row["verified"]) for row in selected),
            "total_job_level_qpu_seconds":sum(row["qpu_usage_seconds"] for row in selected) if complete else None,
            "job_ids":[row["job_id"] for row in selected],"complete":complete}
    report={"scope":"job-level provider charges summed once per selected job; no Batch allocation or per-PUB division",
        "jobs":records,"by_treatment":per_treatment,
        "complete_comparable":all(value["complete"] for value in per_treatment.values()),
        "batch_level_usage_available":False,"estimated_per_pub_usage_used":False}
    report["sha256"]=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
    return report,by_job


def analyze_target(root: str | Path, manifest: TargetManifest, registry, *, new_version=False) -> dict[str,Any]:
    """Reuse scientific analysis while keeping disposable work in configured scratch."""
    from repository_paths import repository_root,MITIGATION_REL,contained,create_scratch_dir,cleanup_scratch_dir
    repo=repository_root(root)
    output_base=contained(repo/MITIGATION_REL/"output","02_run_level_data","adaptive_targets",manifest.target_id)
    usage_provenance,usage_by_job=_target_qpu_usage_provenance(repo,manifest,registry)
    output=output_base
    if new_version:
        output=next_analysis_version(output_base,usage_provenance['sha256'])
    elif output_base.exists():
        saved_path=output_base/"analysis_manifest.json"
        saved=json.loads(saved_path.read_text(encoding="utf-8")) if saved_path.is_file() else {}
        if (saved.get("qpu_usage_provenance_sha256")!=usage_provenance["sha256"] or
                saved.get("plot_contract_version")!=PLOT_CONTRACT_VERSION):
            version_digest=hashlib.sha256(
                f'{usage_provenance["sha256"]}:plot-contract:{PLOT_CONTRACT_VERSION}'.encode("utf-8")).hexdigest()
            index=1
            while True:
                candidate=output_base.parent/f"{manifest.target_id}_v{index:03d}-{version_digest[:12]}"
                if not candidate.exists():output=candidate;break
                candidate_manifest=candidate/"analysis_manifest.json"
                if candidate_manifest.is_file():
                    candidate_data=json.loads(candidate_manifest.read_text(encoding="utf-8"))
                    if (candidate_data.get("qpu_usage_provenance_sha256")==usage_provenance["sha256"] and
                            candidate_data.get("plot_contract_version")==PLOT_CONTRACT_VERSION):
                        output=candidate;break
                index+=1
    output.parent.mkdir(parents=True,exist_ok=True)
    staging=create_scratch_dir("ca",repo)
    try:
        return _analyze_target_staged(repo,manifest,registry,output,staging,usage_provenance,usage_by_job)
    finally:
        cleanup_scratch_dir(staging,repo)


def next_analysis_version(output_base, usage_sha256):
    """Reserve no files; choose a fresh canonical sibling, even if base is missing."""
    output_base=Path(output_base)
    digest=hashlib.sha256(f'{usage_sha256}:plot-contract:{PLOT_CONTRACT_VERSION}'.encode()).hexdigest()
    index=1
    while any(output_base.parent.glob(output_base.name+f'_v{index:03d}-*')):
        index+=1
    return output_base.parent/f'{output_base.name}_v{index:03d}-{digest[:12]}'


def _stage_canonical_tables(staging, source, target_id):
    """Publish canonical data, never duplicate scratch charts/input replicas."""
    import shutil
    destination=staging/"canonical_analysis"/"output"/"02_run_level_data"/target_id
    destination.mkdir(parents=True,exist_ok=False)
    for path in source.iterdir():
        if path.is_file() and path.suffix in (".csv",".json") and not path.name.startswith("a_"):
            shutil.copy2(path,destination/path.name)


def _analyze_target_staged(root: str | Path,manifest: TargetManifest,registry,output: Path,staging: Path,
                           usage_provenance: dict[str,Any],usage_by_job: dict[str,Any]) -> dict[str,Any]:
    """Render and validate in scratch, then commit a verified fresh output tree."""
    import uuid
    from repository_paths import MITIGATION_REL,contained
    from vqe_adapter import analyze_block, load_pilot, validate_rows
    import csv
    repo=Path(root);status=registry.target_status(manifest.target_id)
    if not status.get("exists"): raise RuntimeError("Target is not registered")
    from targeted_recovery import repaired_dataset_status
    repaired=repaired_dataset_status(manifest,registry)
    def verify_final(directory):
        expected=["cumulative_per_pub.csv","per_batch_variation.csv","cumulative_summary.csv","analysis_manifest.json",
            "publication_figures/publication_nyquist.png","publication_figures/publication_bode_magnitude.png",
            "publication_figures/publication_bode_phase.png","publication_figures/publication_residual_real_normalized.png",
            "publication_figures/publication_residual_imag_normalized.png",
            "publication_figures/publication_residual_real.png","publication_figures/publication_residual_imag.png",
            "publication_figures/05_mitigation_median_CI_convergence.png",
            "publication_figures/06_mitigation_three_tier_positioning.png",
            "comparison_figures/distribution_pilot.png","pub_source_manifest.csv"]
        if any(not (directory/name).is_file() for name in expected): return False
        saved=json.loads((directory/"analysis_manifest.json").read_text(encoding="utf-8"))
        if (saved.get("target_manifest_sha256")!=manifest.sha256 or
                saved.get("plot_contract_version")!=PLOT_CONTRACT_VERSION): return False
        usage_complete=saved.get("qpu_usage_coverage",{}).get("complete_comparable") is True
        if usage_complete != (directory/"comparison_figures"/"04_mitigation_accuracy_cost.png").is_file():return False
        try:
            for name in expected[4:11]:
                _verify_png_contract(directory/name, layout="square", min_short_side=2000)
            for name in expected[11:14]:
                _verify_png_contract(directory/name, layout="landscape")
            if usage_complete:
                _verify_png_contract(directory/"comparison_figures/04_mitigation_accuracy_cost.png",
                                     layout="landscape")
        except (OSError, ValueError, RuntimeError, FileNotFoundError):
            return False
        return True
    if output.exists():
        if verify_final(output):
            saved=json.loads((output/"analysis_manifest.json").read_text(encoding="utf-8"))
            saved_counts=saved.get("validated_pubs_by_treatment",{})
            if any(saved_counts.get(name)!=manifest.target_n for name in manifest.treatment_ids):
                raise FileExistsError("Existing analysis is not an exact complete target; refusing to reuse it")
            if saved.get("dataset_status") in ("COMPLETE","REPAIRED_COMPLETE"):
                if not repaired["analysis_eligible"] or saved.get("dataset_status")!=repaired["status"]:
                    raise FileExistsError("Existing complete analysis does not match the selected repaired dataset")
                if saved.get("qpu_usage_provenance_sha256")!=usage_provenance["sha256"]:
                    raise FileExistsError("Existing analysis usage provenance differs; select its versioned analysis output")
                return {"target_id":manifest.target_id,"output":str(output),"dataset_status":repaired["status"],
                    "validated_pubs_by_treatment":repaired["counts_by_treatment"],
                    "artifacts_verified":True,"already_complete":True}
            return {"target_id":manifest.target_id,"output":str(output),
                    "validated_pubs_by_treatment":status["cumulative_validated_pubs_by_treatment"],
                    "blocks_included":[item["logical_block_id"] for item in saved["included_blocks"]],
                    "artifacts_verified":True,"already_complete":True}
        raise FileExistsError(f"Adaptive target output exists but is incomplete or inconsistent; refusing overwrite: {output}")
    if repaired["analysis_eligible"] and any(row["source"]=="REPLACEMENT" for row in repaired["rows"]):
        return _analyze_repaired_target_staged(repo,manifest,registry,output,staging,repaired,
            usage_provenance,usage_by_job)
    counts=status["cumulative_validated_pubs_by_treatment"]
    if any(counts[name]!=manifest.target_n for name in manifest.treatment_ids):
        raise RuntimeError("Target analysis requires target_n validated PUBs for every treatment")
    block_inputs=[]
    for block in status["blocks"]:
        if block["status"] not in ("VALIDATED","COMPLETE"): continue
        block_id=block["logical_block_id"]
        analysis_dir=contained(repo/MITIGATION_REL/"output","02_run_level_data","replicate_block_"+block_id)
        per_repeat=analysis_dir/"per_repeat.csv"
        if not per_repeat.is_file():
            analyze_block(repo,block_id,shots=manifest.shots_per_pub,n=manifest.chunk_n)
        with per_repeat.open(encoding="utf-8-sig",newline="") as handle: rows=list(csv.DictReader(handle))
        if len(rows)!=manifest.chunk_n*8:
            raise ValueError(f"Canonical analysis returned an unexpected PUB row count for {block_id}")
        jobs=registry.rows(block_id); account=block["account_label"]
        latest_jobs={}
        for job in jobs:
            if job["treatment_id"] not in latest_jobs or job["attempt"]>latest_jobs[job["treatment_id"]]["attempt"]:
                latest_jobs[job["treatment_id"]]=job
        expected_jobs={name:row["job_id"] for name,row in latest_jobs.items()}
        if len(expected_jobs)!=8 or any(not value for value in expected_jobs.values()):
            raise ValueError(f"Adaptive block does not have eight persisted job IDs: {block_id}")
        for row in rows:
            if row.get("job_id")!=expected_jobs.get(row.get("treatment")):
                raise ValueError(f"Canonical analysis job provenance differs from the adaptive registry: {block_id}")
        for row in rows:
            row["SSE"]=float(row["SSE"]);row["RMSE"]=float(row["RMSE"])
        if any(job["input_hashes"]!=manifest.input_hashes for job in latest_jobs.values()):
            raise ValueError(f"Adaptive block input hashes differ from target manifest: {block_id}")
        block_inputs.append({"block_id":block_id,"batch_id":block.get("batch_id"),"account_label":account,"rows":rows,
            "job_ids":{name:row["job_id"] for name,row in latest_jobs.items()},"input_hashes":manifest.input_hashes,
            "created_at":block.get("created_at"),"updated_at":block.get("updated_at")})
    result=aggregate_validated_blocks(block_inputs,manifest.treatment_ids,expected_per_treatment=manifest.chunk_n)
    if any(item.name!=".mxs-owner" for item in staging.iterdir()):
        raise FileExistsError("Owned adaptive analysis scratch directory must be otherwise empty")
    for name,data in (("cumulative_per_pub.csv",result["per_pub"]),("per_batch_variation.csv",result["per_block"]),("cumulative_summary.csv",result["summary"])):
        if not data: continue
        with (staging/name).open("w",encoding="utf-8",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
    analysis_manifest={"schema_version":2,"plot_contract_version":PLOT_CONTRACT_VERSION,
        "target_manifest_sha256":manifest.sha256,
        "target_id":manifest.target_id,"target_n":manifest.target_n,"chunk_n":manifest.chunk_n,
        "source_run":manifest.source_run,"backend":manifest.backend,"input_hashes":manifest.input_hashes,
        "physical_qubits":list(manifest.physical_qubits),"shots_per_pub":manifest.shots_per_pub,
        "included_blocks":[{"logical_block_id":block["block_id"],"batch_id":block["batch_id"],
            "account_label":block["account_label"],"job_ids":block["job_ids"],
            "input_hashes":block["input_hashes"],"created_at":block["created_at"],
            "updated_at":block["updated_at"]} for block in block_inputs],
        "validated_pubs_by_treatment":counts,"within_between_variation_separated":True,
        "plot_uncertainty_note":"Canonical pilot plots resample measured PUBs; consult per_batch_variation.csv for batch-level spread.",
        "qpu_usage_provenance_sha256":usage_provenance["sha256"],
        "qpu_usage_coverage":usage_provenance,
        "canonical_combined_analysis":"canonical_analysis/output/02_run_level_data/"+manifest.target_id,
        "per_pub_source_map":"pub_source_manifest.csv",
        "created_utc":datetime.now(timezone.utc).isoformat()}
    with (staging/"analysis_manifest.json").open("w",encoding="utf-8") as handle:
        json.dump(analysis_manifest,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
    pilot=load_pilot(repo)
    # Reuse the publication plotting implementation with the validated cumulative rows.
    import pandas as pd
    frame=pd.DataFrame(result["per_pub"])
    plot_dir=staging/"publication_figures"
    tables=[{"treatment":item["treatment"],"median_SSE":float(frame.loc[frame.treatment==item["treatment"],"SSE"].median())} for item in result["summary"]]
    lock=json.loads((Path(manifest.source_bundle)/"lock.json").read_text(encoding="utf-8"))
    ideal_sse=lock.get("ideal_SSE")
    pilot.create_mos2_style_figures(frame,tables,plot_dir,staging/"w"/"p",manifest.target_id,ideal_sse,
        seed=1013,bootstrap=3000,n=manifest.target_n,artifact_prefix="a")
    # Reuse mitigation_pilot.analyze for cumulative validated observations. The
    # temporary combined table is an index-preserving concatenation of raw PUB
    # rows already collected for this target; every exported row records its
    # originating block, treatment, PUB index, and IBM job ID.
    import shutil
    from vqe_adapter import _args, record_directory
    combined_round="independent_target_"+manifest.target_id
    canonical_root=staging/"w"
    records_root=canonical_root/"i"
    canonical_out=canonical_root/"o"
    canonical_charts=canonical_root/"p"
    source_map=[]
    for treatment in manifest.treatment_ids:
        treatment_dir=records_root/treatment
        treatment_dir.mkdir(parents=True,exist_ok=True)
        merged=[];per_pub_job={};next_pub=1
        for block in status["blocks"]:
            if block["status"] not in ("VALIDATED","COMPLETE"): continue
            block_id=block["logical_block_id"]
            raw_path=record_directory(repo,block_id)/treatment/"expectations.csv"
            if not raw_path.is_file(): raise FileNotFoundError(f"Validated block raw PUB table is missing: {raw_path}")
            with raw_path.open(encoding="utf-8-sig",newline="") as handle:
                raw_rows=list(csv.DictReader(handle))
            validate_rows(raw_rows,manifest.chunk_n,f"{block_id}/{treatment}",root=repo)
            block_jobs=[job for job in registry.rows(block_id) if job["treatment_id"]==treatment]
            if not block_jobs: raise RuntimeError(f"Validated block has no registry treatment row: {block_id}/{treatment}")
            job=max(block_jobs,key=lambda item:item["attempt"])
            for raw in raw_rows:
                source_pub=int(raw["pub"])
                row={"pub":next_pub}
                for key in [*(f"Z{i}" for i in range(7)),*(f"std_Z{i}" for i in range(7))]:
                    row[key]=raw[key]
                merged.append(row)
                per_pub_job[str(next_pub)]=job["job_id"]
                source_map.append({"treatment_id":treatment,"combined_pub":next_pub,"source_block_id":block_id,
                    "source_pub":source_pub,"job_id":job["job_id"],"batch_id":block.get("batch_id"),
                    "account_label":job["account_label"],"input_hashes":job["input_hashes"]})
                next_pub+=1
        if len(merged)!=manifest.target_n: raise RuntimeError(f"Combined canonical analysis has wrong PUB count for {treatment}")
        with (treatment_dir/"expectations.csv").open("x",encoding="utf-8",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(merged[0]));writer.writeheader();writer.writerows(merged)
        with (treatment_dir/"pub_source_manifest.json").open("x",encoding="utf-8") as handle:
            json.dump({"per_pub_job_id":per_pub_job,"source_run":"run_02","target_id":manifest.target_id},handle,indent=2)
    pub_map_path=staging/"pub_source_manifest.csv"
    with pub_map_path.open("x",encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(source_map[0]));writer.writeheader();writer.writerows(source_map)
    canonical_args=_args(repo,combined_round,shots=manifest.shots_per_pub,n=manifest.target_n)
    canonical_args._analysis_override={"artifact_label":"a","out_dir":canonical_out,"records_root":records_root,
        "charts_dir":canonical_charts,"recovery_dir":canonical_root/"r",
        "qpu_usage_by_job":usage_by_job}
    pilot.analyze(canonical_args)
    publication_dir=staging/"publication_figures"
    for name in ("publication_nyquist.png","publication_bode_magnitude.png","publication_bode_phase.png",
                 "publication_residual_real.png","publication_residual_imag.png",
                 "publication_residual_real_normalized.png","publication_residual_imag_normalized.png"):
        destination=publication_dir/name
        if destination.exists(): raise FileExistsError(f"Refusing to overwrite cumulative publication figure: {destination}")
        shutil.copy2(canonical_out/name,destination)
    comparison_dir=staging/"comparison_figures";comparison_dir.mkdir(parents=True,exist_ok=True)
    comparison_names=["distribution_pilot.png"]
    if usage_provenance["complete_comparable"]:comparison_names.append("04_mitigation_accuracy_cost.png")
    for name in comparison_names:
        destination=comparison_dir/name
        if destination.exists(): raise FileExistsError(f"Refusing to overwrite cumulative comparison figure: {destination}")
        shutil.copy2(canonical_out/name,destination)
    # The canonical pilot calls its input one round; this independent-n wrapper
    # records that these observations are concatenated from distinct validated
    # Batches and keeps the per-PUB provenance map alongside its analysis.
    summary_path=canonical_out/"round_summary.json"
    summary=json.loads(summary_path.read_text(encoding="utf-8"))
    summary["independent_n_aggregate"]={"target_id":manifest.target_id,"target_n":manifest.target_n,
        "chunk_n":manifest.chunk_n,"source_blocks":[block["block_id"] for block in block_inputs],
        "scientific_values_unchanged":True}
    summary["warning"]="Independent validated PUB observations are grouped by source Batch in pub_source_manifest.csv."
    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    if not verify_final(staging): raise RuntimeError("Adaptive target analysis artifact/figure validation failed")
    _stage_canonical_tables(staging,canonical_out,manifest.target_id)
    # Prepare and verify a short same-volume commit tree first. The completed
    # scientific result still lands in its permanent output directory; only
    # disposable rendering/staging lives in MXENE_SCRATCH_DIR.
    commit=output.parent/f".c_{uuid.uuid4().hex[:8]}"
    commit_owned=False
    try:
        shutil.copytree(staging,commit,ignore=shutil.ignore_patterns(".mxs-owner","w"));commit_owned=True
        if not verify_final(commit): raise RuntimeError("Adaptive target commit copy failed artifact/figure validation")
        commit.rename(output);commit_owned=False
    except FileExistsError:
        if commit_owned and commit.exists():shutil.rmtree(commit)
        if not verify_final(output):raise
    except Exception:
        if commit_owned and commit.exists():shutil.rmtree(commit)
        raise
    return {"target_id":manifest.target_id,"output":str(output),"validated_pubs_by_treatment":counts,
        "blocks_included":[item["block_id"] for item in block_inputs],"variation_note":result["variation_note"],
        "artifacts_verified":True,"already_complete":False}


def _analyze_repaired_target_staged(root,manifest,registry,output: Path,staging: Path,
                                   repaired: dict[str,Any],usage_provenance: dict[str,Any] | None=None,
                                   usage_by_job: dict[str,Any] | None=None) -> dict[str,Any]:
    """Canonical analysis over one selected valid result per logical PUB slot."""
    import csv,shutil,uuid
    from collections import defaultdict
    from repository_paths import MITIGATION_REL,contained
    from vqe_adapter import load_pilot,_args,verify_frozen_inputs
    from targeted_recovery import assemble_repaired_dataset
    if usage_provenance is None:
        usage_provenance={"scope":"job-level provider charges; usage evidence was not supplied",
            "jobs":[],"by_treatment":{},"complete_comparable":False,
            "batch_level_usage_available":False,"estimated_per_pub_usage_used":False,
            "omitted_figure_reason":"verified local provider usage evidence was not supplied"}
        usage_provenance["sha256"]=hashlib.sha256(json.dumps(usage_provenance,sort_keys=True,
            separators=(",",":"),default=str).encode()).hexdigest()
    usage_by_job=usage_by_job or {}
    dataset=assemble_repaired_dataset(root,manifest,registry)
    if not dataset.get("dataset_path"):raise RuntimeError("Repaired dataset has not reached a complete status")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite any existing n-level analysis: {output}")
    selected=repaired["rows"]
    target_blocks=registry.target_status(manifest.target_id)["blocks"]
    block_by_id={block["logical_block_id"]:block for block in target_blocks}
    if not repaired["analysis_eligible"]:
        raise RuntimeError("Repaired dataset is not complete for every treatment")
    if any(item["input_hashes"]!=manifest.input_hashes for item in selected):
        raise RuntimeError("Selected repaired PUB provenance contains a different frozen-input hash")
    canonical_root=staging/"w";records_root=canonical_root/"i"
    canonical_out=canonical_root/"o"
    charts=canonical_root/"p";source_index={};source_rows=[]
    by_treatment={name:sorted((item for item in selected if item["treatment_id"]==name),key=lambda item:item["logical_pub_index"])
        for name in manifest.treatment_ids}
    for treatment,items in by_treatment.items():
        if len(items)!=manifest.target_n: raise RuntimeError(f"Complete-only gate failed for {treatment}")
        folder=records_root/treatment;folder.mkdir(parents=True,exist_ok=True);csv_path=folder/"expectations.csv"
        with csv_path.open("x",encoding="utf-8",newline="") as handle:
            fields=["pub",*(f"Z{i}" for i in range(7)),*(f"std_Z{i}" for i in range(7))]
            writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
            per_pub={}
            for ordinal,item in enumerate(items,1):
                ev=item["expectation_values"];std=item["standard_deviations"]
                if len(ev)!=7 or any(not math.isfinite(float(v)) for v in ev):
                    raise ValueError("Selected repaired expectations must contain seven finite values")
                if std is not None and (len(std)!=7 or any(not math.isfinite(float(v)) or float(v)<0 for v in std)):
                    raise ValueError("Selected replacement uncertainty is invalid")
                row={"pub":ordinal,**{f"Z{i}":float(ev[i]) for i in range(7)}}
                row.update({f"std_Z{i}":float(std[i]) if std is not None else "" for i in range(7)})
                writer.writerow(row)
                per_pub[str(ordinal)]=item["job_id"]
                source_index[(treatment,ordinal)]=item
                source_block=block_by_id[item["logical_block_id"]]
                source_pub=item["logical_pub_index"]-(int(source_block["sequence_no"])-1)*manifest.chunk_n
                source_rows.append({"target_id":manifest.target_id,"experiment_id":manifest.experiment_id,
                    "target_n":manifest.target_n,"logical_pub_index":item["logical_pub_index"],
                    "treatment_id":treatment,"canonical_pub_index":ordinal,"combined_pub":ordinal,
                    "source_block_id":item["logical_block_id"],"source":item["source"],
                    "original_or_replacement_job_id":item["job_id"],"job_id":item["job_id"],
                    "source_pub":source_pub,"source_batch_id":item["batch_id"],"batch_id":item["batch_id"],
                    "account_label":item["account_label"],"backend":item["backend"],
                    "source_sha256":item["source_sha256"],"input_hashes":json.dumps(item["input_hashes"],sort_keys=True)})
            with (folder/"pub_source_manifest.json").open("x",encoding="utf-8") as handle:
                json.dump({"per_pub_job_id":per_pub,"source_run":"run_02","target_id":manifest.target_id,
                    "repaired_dataset":True},handle,ensure_ascii=False,indent=2)
    _qc,_obs,_theta,_layout,ideal_expectations,_qver=verify_frozen_inputs(root)
    if len(ideal_expectations)!=7 or any(not math.isfinite(float(x)) for x in ideal_expectations):
        raise RuntimeError("Canonical noiseless reference could not be verified from the immutable run_02 source")
    pilot=load_pilot(root);canonical_args=_args(root,"repaired_"+manifest.target_id,
        shots=manifest.shots_per_pub,n=manifest.target_n)
    canonical_args._analysis_override={"artifact_label":"a","out_dir":canonical_out,"records_root":records_root,
        "charts_dir":charts,"recovery_dir":canonical_root/"r",
        "lock_overrides":{"ideal_expectations":[float(x) for x in ideal_expectations]},
        "qpu_usage_by_job":usage_by_job}
    pilot.analyze(canonical_args)
    with (canonical_out/"per_repeat.csv").open(encoding="utf-8-sig",newline="") as handle:
        analyzed=list(csv.DictReader(handle))
    if len(analyzed)!=manifest.target_n*len(manifest.treatment_ids):
        raise RuntimeError("Canonical VQE analysis did not return exactly one row per repaired logical PUB slot")
    by_block=defaultdict(list);per_pub=[]
    for row in analyzed:
        treatment=row["treatment"];ordinal=int(row["pub"]);source=source_index[(treatment,ordinal)]
        block_id=source["logical_block_id"]
        item={**row,"target_id":manifest.target_id,"experiment_id":manifest.experiment_id,
            "n":manifest.target_n,"logical_pub_index":source["logical_pub_index"],
            "block_id":block_id,"batch_id":source["batch_id"],"account_label":source["account_label"],
            "source":source["source"],"source_sha256":source["source_sha256"],
            "input_hashes":json.dumps(source["input_hashes"],sort_keys=True)}
        per_pub.append(item);by_block[block_id].append(item)
    block_inputs=[]
    for block in sorted(target_blocks,key=lambda x:x["sequence_no"]):
        block_rows=by_block.get(block["logical_block_id"],[])
        if not block_rows:continue
        if any(sum(r["treatment"]==name for r in block_rows)!=manifest.chunk_n for name in manifest.treatment_ids):
            raise RuntimeError("Repaired block slot selection is not complete treatment-by-treatment")
        block_job_ids={name:next((row["job_id"] for row in block_rows if row["treatment"]==name),None)
            for name in manifest.treatment_ids}
        if any(not value for value in block_job_ids.values()):raise RuntimeError("Repaired block is missing original/replacement job provenance")
        block_inputs.append({"block_id":block["logical_block_id"],"batch_id":block.get("batch_id"),
            "account_label":block["account_label"],"rows":block_rows,"job_ids":block_job_ids,
            "input_hashes":manifest.input_hashes,"created_at":block.get("created_at"),"updated_at":block.get("updated_at")})
    aggregate=aggregate_validated_blocks(block_inputs,manifest.treatment_ids,expected_per_treatment=manifest.chunk_n)
    for filename,data in (("cumulative_per_pub.csv",aggregate["per_pub"]),
            ("per_batch_variation.csv",aggregate["per_block"]),("cumulative_summary.csv",aggregate["summary"])):
        with (staging/filename).open("x",encoding="utf-8-sig",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(data[0]) if data else ["treatment"]);writer.writeheader();writer.writerows(data)
    with (staging/"pub_source_manifest.csv").open("x",encoding="utf-8-sig",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(source_rows[0]));writer.writeheader();writer.writerows(source_rows)
    pubdir=staging/"publication_figures";cmpdir=staging/"comparison_figures";pubdir.mkdir();cmpdir.mkdir()
    for name in ("publication_nyquist.png","publication_bode_magnitude.png","publication_bode_phase.png",
            "publication_residual_real.png","publication_residual_imag.png","publication_residual_real_normalized.png",
            "publication_residual_imag_normalized.png","05_mitigation_median_CI_convergence.png",
            "06_mitigation_three_tier_positioning.png"):
        shutil.copy2(canonical_out/name,pubdir/name)
    for name in ("distribution_pilot.png",):
        shutil.copy2(canonical_out/name,cmpdir/name)
    if usage_provenance["complete_comparable"]:
        shutil.copy2(canonical_out/"04_mitigation_accuracy_cost.png",cmpdir/"04_mitigation_accuracy_cost.png")
    analysis_manifest={"schema_version":2,"plot_contract_version":PLOT_CONTRACT_VERSION,
        "target_manifest_sha256":manifest.sha256,"target_id":manifest.target_id,
        "target_n":manifest.target_n,"chunk_n":manifest.chunk_n,"source_run":"run_02","backend":"ibm_fez",
        "input_hashes":manifest.input_hashes,"physical_qubits":list(manifest.physical_qubits),
        "shots_per_pub":manifest.shots_per_pub,"dataset_status":repaired["status"],
        "validated_pubs_by_treatment":repaired["counts_by_treatment"],"selected_slot_count":len(selected),
        "replacement_selected_count":sum(item["source"]=="REPLACEMENT" for item in selected),
        "ideal_reference_source":"canonical verify_source from immutable run_02 theta; lock.json left unchanged",
        "included_blocks":[{"logical_block_id":block["logical_block_id"],"batch_id":block.get("batch_id"),
            "account_label":block["account_label"],"input_hashes":manifest.input_hashes}
            for block in target_blocks],
        "canonical_combined_analysis":"canonical_analysis/output/02_run_level_data/"+manifest.target_id,
        "repaired_dataset_manifest":str(Path(dataset["dataset_path"])/"dataset_manifest.json"),
        "incomplete_original_batches_preserved":True,"canonical_analysis":"mitigation_pilot.analyze",
        "qpu_usage_provenance_sha256":usage_provenance["sha256"],
        "qpu_usage_coverage":usage_provenance,
        "created_utc":datetime.now(timezone.utc).isoformat()}
    with (staging/"analysis_manifest.json").open("x",encoding="utf-8") as handle:
        json.dump(analysis_manifest,handle,ensure_ascii=False,indent=2)
    required=["cumulative_per_pub.csv","per_batch_variation.csv","cumulative_summary.csv","analysis_manifest.json",
        "pub_source_manifest.csv",*("publication_figures/"+name for name in (
            "publication_nyquist.png","publication_bode_magnitude.png","publication_bode_phase.png",
            "publication_residual_real.png","publication_residual_imag.png","publication_residual_real_normalized.png",
            "publication_residual_imag_normalized.png","05_mitigation_median_CI_convergence.png",
            "06_mitigation_three_tier_positioning.png")),
        "comparison_figures/distribution_pilot.png"]
    if usage_provenance["complete_comparable"]:
        required.append("comparison_figures/04_mitigation_accuracy_cost.png")
    if any(not (staging/name).is_file() for name in required): raise RuntimeError("Repaired complete export is missing required artifacts")
    # Apply the same contracts to repaired and ordinary n-level analyses.
    publication_names = [name for name in required if name.startswith("publication_figures/publication_")]
    comparison_names = [name for name in required if name.endswith(".png") and name not in publication_names]
    if len(publication_names) != 7:
        raise RuntimeError("Expected exactly seven publication EIS figures")
    for name in publication_names:
        _verify_png_contract(staging/name, layout="square", min_short_side=2000)
    for name in comparison_names:
        _verify_png_contract(staging/name, layout="landscape")
    _stage_canonical_tables(staging,canonical_out,manifest.target_id)
    # Same-volume, non-overwriting commit.
    commit=output.parent/f".r_{uuid.uuid4().hex[:8]}";shutil.copytree(staging,commit,ignore=shutil.ignore_patterns(".mxs-owner","w"))
    try: commit.rename(output)
    except Exception:
        if commit.exists():shutil.rmtree(commit)
        raise
    return {"target_id":manifest.target_id,"output":str(output),"dataset_status":repaired["status"],
        "validated_pubs_by_treatment":repaired["counts_by_treatment"],"artifacts_verified":True,
        "replacement_selected_count":analysis_manifest["replacement_selected_count"],
        "variation_note":aggregate["variation_note"]}


def analyze_partial_target(root: str | Path, manifest: TargetManifest, registry, *,
                           campaign_id: str, ai_mode: str = "python") -> dict[str,Any]:
    """Create opt-in diagnostic EIS analysis from immutable per-PUB validated evidence only."""
    import csv,hashlib,shutil,uuid
    import numpy as np
    from repository_paths import (MITIGATION_REL,repository_root,contained,
        create_scratch_dir,cleanup_scratch_dir)
    from vqe_adapter import load_pilot
    repo=repository_root(root);status=registry.target_status(manifest.target_id)
    if not status.get("exists"):raise RuntimeError("Independent n target is not registered")
    campaign=registry.independent_campaign_status(campaign_id)
    if not campaign.get("exists"):raise RuntimeError("Partial analysis requires its registered independent-n campaign")
    from independent_n_campaign import load_campaign_authorization
    authorized,auth_revision=load_campaign_authorization(repo,campaign_id,campaign["manifest"],campaign["manifest_sha256"])
    policy=authorized.get("completeness_policy",{})
    if not policy.get("partial_analysis_approved"):
        raise RuntimeError("Partial scientific analysis requires explicit versioned completeness_policy.partial_analysis_approved=true")
    validation=registry.pub_validation_records(manifest.target_id)
    valid=registry.validated_pub_values(manifest.target_id)
    from vqe_adapter import record_directory
    raw_archives_verified=True
    provenance_verified=True
    job_lookup={(row["logical_block_id"],row["treatment_id"]):row for block in status.get("blocks",[])
        for row in registry.rows(block["logical_block_id"])}
    expected_jobs={(block["logical_block_id"],treatment) for block in status.get("blocks",[])
        for treatment in manifest.treatment_ids}
    seen_archives=set()
    for block_id,treatment in expected_jobs:
        archive_dir=record_directory(repo,block_id)/treatment/"raw_retrievals"
        candidates=list(archive_dir.glob("job_result_*.json")) if archive_dir.is_dir() else []
        adopted_meta=job_lookup.get((block_id,treatment),{}).get("metadata",{})
        adopted_path=adopted_meta.get("raw_archive_path")
        if adopted_path:
            source=Path(adopted_path).resolve()
            allowed=(repo/MITIGATION_REL/"output"/"03_IBM_execution_records"/"ai_assistant"/"calibration"/
                "vqe-calibration-pilot-02"/"raw_results"/"sequence_001").resolve()
            try:source.relative_to(allowed)
            except ValueError:raise ValueError("Adopted historical archive path is outside its immutable calibration archive")
            if source.is_file():candidates.append(source)
        valid_archives=[]
        for path in candidates:
            digest_name=path.stem.removeprefix("job_result_")
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest_name==actual or (adopted_path and path.resolve()==Path(adopted_path).resolve() and
                                       actual==adopted_meta.get("raw_archive_sha256")):
                valid_archives.append(actual)
        if not valid_archives:raw_archives_verified=False
        else:seen_archives.update((block_id,treatment,value) for value in valid_archives)
    for item in validation:
        if (item["experiment_id"]!=manifest.experiment_id or item["logical_block_id"] not in
                {block["logical_block_id"] for block in status.get("blocks",[])} or
                len(item["source_sha256"])!=64 or item["treatment_id"] not in manifest.treatment_ids or
                item["source_sha256"] not in {value for b,t,value in seen_archives if b==item["logical_block_id"] and t==item["treatment_id"]}):
            provenance_verified=False
    for row in valid:
        try:stored_hashes=json.loads(row["input_hashes_json"])
        except (TypeError,json.JSONDecodeError):stored_hashes=None
        if stored_hashes!=manifest.input_hashes:
            raise ValueError("Validated PUB frozen-input provenance differs from the selected n experiment")
        if row["experiment_id"]!=manifest.experiment_id or len(row["source_sha256"])!=64:
            raise ValueError("Validated PUB experiment/hash provenance is invalid")
        if row["source_sha256"] not in {value for b,t,value in seen_archives if b==row["logical_block_id"] and t==row["treatment_id"]}:
            provenance_verified=False
    if not raw_archives_verified:raise ValueError("One or more registered Batch treatment raw archives are missing or fail their content hash")
    if not provenance_verified:raise ValueError("Per-PUB or experiment provenance does not match its immutable raw archive")
    manifest_key={"target_manifest_sha256":manifest.sha256,"sse_plot_contract":"canonical-m0-m7-600dpi-nyquist-range-v3",
        "validation_records":[
        {key:row.get(key) for key in ("logical_block_id","batch_id","treatment_id","pub_index","source_sha256","error_codes_json")}
        for row in validation],"valid_pub_keys":[(row["logical_block_id"],row["treatment_id"],row["pub_index"],row["source_sha256"]) for row in valid]}
    digest=hashlib.sha256(json.dumps(manifest_key,sort_keys=True,default=str,separators=(",",":" )).encode()).hexdigest()
    root_out=contained(repo/MITIGATION_REL/"output","02_run_level_data","diagnostics","independent_n_partial",manifest.target_id)
    output=contained(root_out,"snapshot_"+digest[:16])
    report_path=output/"diagnostic_report.json"
    if output.exists():
        if not report_path.is_file():raise FileExistsError("Existing partial diagnostic directory is incomplete; refusing overwrite")
        saved=json.loads(report_path.read_text(encoding="utf-8"))
        if saved.get("snapshot_sha256")!=digest:raise FileExistsError("Existing partial diagnostic provenance differs")
        artifact_hashes=saved.get("artifact_sha256",{})
        verified_hashes={name:(output/name).is_file() and hashlib.sha256((output/name).read_bytes()).hexdigest()==value
            for name,value in artifact_hashes.items()}
        files_verified=bool(verified_hashes) and all(verified_hashes.values())
        figure_names=saved.get("artifacts",[])
        figures_verified=bool(saved.get("figures")) and all(item.get("valid",False) for item in saved.get("figures",{}).values()) and all(
            verified_hashes.get(name,False) for name in figure_names)
        csv_names=[name for name in artifact_hashes if name.endswith(".csv")]
        analysis_verified=bool(saved.get("analysis_verified")) and bool(csv_names) and all(verified_hashes.get(name,False) for name in csv_names)
        checksum_path=output/"diagnostic_report.sha256"
        report_hash=hashlib.sha256(report_path.read_bytes()).hexdigest()
        report_verified=(checksum_path.is_file() and checksum_path.read_text(encoding="ascii").strip()==report_hash and
            saved.get("snapshot_sha256")==digest and saved.get("analysis_state")=="PARTIAL_DIAGNOSTIC_ONLY"
            and saved.get("completed_n_level") is False and saved.get("production_comparison") is False)
        return {"target_id":manifest.target_id,"output":str(output),"snapshot_sha256":digest,
            "partial_diagnostic_only":True,"artifacts_verified":bool(saved.get("artifacts_verified",False) and files_verified and figures_verified and report_verified and analysis_verified),
            "analysis_verified":analysis_verified,
            "raw_archives_verified":saved.get("raw_archives_verified",False),
            "provenance_verified":saved.get("provenance_verified",False),
            "figures_verified":figures_verified,
            "report_verified":report_verified,
            "already_exists":True}

    pilot=load_pilot(repo)
    source=Path(manifest.source_bundle)
    _circuit,_obs,_theta,_physical,ideal_expectations,_version=pilot.verify_source(source,repo)
    pilot.register_science(repo)
    lock=json.loads((source/"lock.json").read_text(encoding="utf-8"))
    from impedance_model import read_eis_file
    frequency,zexp=read_eis_file(str(repo/"data"/"EIS14.csv"))
    frequency=np.asarray(frequency,dtype=float);zexp=np.asarray(zexp,dtype=complex)
    expected_treatments=tuple(manifest.treatment_ids)
    grouped={name:[] for name in expected_treatments}
    for item in valid:
        if item["treatment_id"] not in grouped:raise ValueError("Per-PUB evidence contains an unexpected treatment")
        evs=json.loads(item["expectation_values_json"])
        if len(evs)!=7 or any(not math.isfinite(float(x)) for x in evs):raise ValueError("Validated PUB storage contains invalid expectations")
        parameters,zfit,sse,rmse=pilot.calculate_pub_eis(evs,frequency,zexp)
        record={"experiment_id":item["experiment_id"],"target_id":manifest.target_id,
            "logical_block_id":item["logical_block_id"],"batch_id":item["batch_id"],
            "treatment":item["treatment_id"],"treatment_label":pilot.treatment_display(item["treatment_id"]),
            "pub_index":item["pub_index"],"pub":len(grouped[item["treatment_id"]])+1,
            "job_id":item["job_id"],"account_label":item["account_label"],
            "backend":item["backend"],"source_sha256":item["source_sha256"],
            "input_hashes_json":item["input_hashes_json"],"uncertainty_state":item["uncertainty_state"],
            "SSE":sse,"RMSE":rmse,"expectation_MAE_vs_ideal":float(np.mean(np.abs(np.asarray(evs)-np.asarray(ideal_expectations,dtype=float))))}
        record.update(parameters);record.update({f"Z{i}":float(evs[i]) for i in range(7)})
        grouped[item["treatment_id"]].append((record,zfit))
    ideal_params,ideal_curve,ideal_sse,_ideal_rmse=pilot.calculate_pub_eis(ideal_expectations,frequency,zexp)
    rows=[];summary=[];chosen_curves={"Ideal":ideal_curve}
    missing_by_treatment={}
    for name in expected_treatments:
        observations=sorted(grouped[name],key=lambda pair:(pair[0]["logical_block_id"],pair[0]["pub_index"]))
        treatment_rows=[pair[0] for pair in observations];rows.extend(treatment_rows)
        count=len(treatment_rows);missing=max(0,manifest.target_n-count)
        missing_by_treatment[name]=missing
        sses=[item["SSE"] for item in treatment_rows];rmses=[item["RMSE"] for item in treatment_rows]
        summary.append({"treatment":name,"treatment_label":pilot.treatment_display(name),
            "expected_target_pubs":manifest.target_n,"validated_pub_count":count,
            "missing_or_invalid_pub_count":missing,"median_SSE":float(np.median(sses)) if sses else None,
            "mean_SSE":float(np.mean(sses)) if sses else None,
            "std_SSE":float(np.std(sses,ddof=1)) if len(sses)>1 else None,
            "SSE_min":float(np.min(sses)) if sses else None,
            "SSE_q25":float(np.percentile(sses,25)) if sses else None,
            "SSE_q75":float(np.percentile(sses,75)) if sses else None,
            "SSE_p90":float(np.percentile(sses,90)) if sses else None,
            "SSE_max":float(np.max(sses)) if sses else None,
            "median_RMSE":float(np.median(rmses)) if rmses else None,
            "mean_RMSE":float(np.mean(rmses)) if rmses else None,
            "RMSE_std":float(np.std(rmses,ddof=1)) if len(rmses)>1 else None,
            "RMSE_q25":float(np.percentile(rmses,25)) if rmses else None,
            "RMSE_q75":float(np.percentile(rmses,75)) if rmses else None,
            "RMSE_p90":float(np.percentile(rmses,90)) if rmses else None})
        if observations:
            best=min(observations,key=lambda pair:pair[0]["SSE"])
            chosen_curves[f"{pilot.treatment_display(name)} (n={count})"]=best[1]
    validation_rows=[]
    for item in validation:
        validation_rows.append({"experiment_id":item["experiment_id"],"logical_block_id":item["logical_block_id"],
            "batch_id":item["batch_id"],"treatment":item["treatment_id"],"pub_index":item["pub_index"],
            "expectation_valid":bool(item["expectation_valid"]),
            "uncertainty_valid":None if item["uncertainty_valid"] is None else bool(item["uncertainty_valid"]),
            "uncertainty_state":item["uncertainty_state"],"error_codes":item["error_codes_json"],
            "source_sha256":item["source_sha256"],"job_id":item["job_id"],"account_label":item["account_label"],
            "backend":item["backend"]})
    output.parent.mkdir(parents=True,exist_ok=True)
    staging=create_scratch_dir("pa",repo)
    commit=None;commit_owned=False
    try:
        def write_csv(path,records):
            if not records:return
            with path.open("x",encoding="utf-8",newline="") as handle:
                writer=csv.DictWriter(handle,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
        write_csv(staging/"per_pub_eis.csv",rows)
        write_csv(staging/"pub_validation.csv",validation_rows)
        write_csv(staging/"treatment_sample_counts.csv",summary)
        write_csv(staging/"validated_pub_provenance.csv",[
            {key:row[key] for key in ("experiment_id","logical_block_id","batch_id","treatment","treatment_label","pub_index","job_id","account_label","backend","source_sha256","input_hashes_json","uncertainty_state")}
            for row in rows])
        blocks=status["blocks"]
        warning=("PARTIAL DIAGNOSTIC ONLY: this is not a completed n-level or production M0-M7 comparison. "
            "Treatment sample sizes may differ, creating unequal precision and potential selection bias. "
            "No missing PUBs or values were imputed.")
        artifacts=[]
        if rows:
            import pandas as pd
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            frame=pd.DataFrame(rows)
            labels=list(chosen_curves.keys());tables=[{"treatment":item["treatment"],"median_SSE":item["median_SSE"]}
                for item in summary if item["median_SSE"] is not None]
            artifacts.extend(pilot.publication_mitigation_figures(staging,frequency,zexp,chosen_curves,
                frame,labels,tables,n=manifest.target_n))
            classical_sse=pilot._classical_sse_reference(repo)
            sse_plot_artifacts=pilot.plot_sse_treatment_figures(frame,staging,classical_sse,
                n=manifest.target_n)
            artifacts.extend(name for name in sse_plot_artifacts if name.lower().endswith(".png"))
            # Preserve the established wide convergence/positioning templates
            # when a measured baseline exists; otherwise export safe coverage/SSE diagnostics.
            baseline=next((item["median_SSE"] for item in summary if item["treatment"]=="M0_Baseline"),None)
            if baseline is not None and math.isfinite(float(baseline)) and float(baseline)>0:
                pilot.create_mos2_style_figures(frame,tables,staging,staging,
                    f"PARTIAL_n{manifest.target_n}",ideal_sse,seed=1013,bootstrap=300,n=manifest.target_n)
                artifacts.extend(["05_mitigation_median_CI_convergence.png","06_mitigation_three_tier_positioning.png"])
            else:
                byname=[(item["treatment_label"],item["median_SSE"]) for item in summary]
                present=[(name,value) for name,value in byname if value is not None]
                filename="partial_sse_distribution.png"
                fig,ax=plt.subplots(figsize=(15,6.5))
                if present:
                    ax.bar([item[0] for item in present],[item[1] for item in present],color="#277DA1")
                    ax.set_xticks(range(len(present)),[item[0] for item in present],rotation=20,ha="right")
                ax.set_ylabel("Per-PUB SSE (Ω²)");ax.set_title(f"SSE Diagnostic (n={manifest.target_n})")
                fig.tight_layout();fig.savefig(staging/filename,dpi=230);plt.close(fig);artifacts.append(filename)
        from PIL import Image
        figure_checks={}
        for filename in artifacts:
            with Image.open(staging/filename) as image:
                dpi=image.info.get("dpi")
                if filename.startswith("publication_"):
                    passed=image.width==image.height and bool(dpi) and all(abs(float(v)-600)<=1 for v in dpi[:2])
                elif filename=="distribution_pilot.png":
                    passed=image.width>image.height and bool(dpi) and all(abs(float(v)-600)<=1 for v in dpi[:2])
                elif filename=="06_mitigation_three_tier_positioning.png":
                    passed=image.width>image.height and bool(dpi) and all(abs(float(v)-600)<=1 for v in dpi[:2])
                else:passed=image.width>image.height
                figure_checks[filename]={"width":image.width,"height":image.height,"dpi":dpi,"valid":passed}
                if not passed:raise RuntimeError("Partial diagnostic figure validation failed: "+filename)
        csv_names=[name for name in ("per_pub_eis.csv","pub_validation.csv","treatment_sample_counts.csv","validated_pub_provenance.csv","sse_plot_summary.csv")
            if (staging/name).is_file()]
        if "treatment_sample_counts.csv" not in csv_names:
            raise RuntimeError("Partial diagnostic treatment sample-count CSV is missing")
        if rows and not {"per_pub_eis.csv","validated_pub_provenance.csv"}.issubset(csv_names):
            raise RuntimeError("Partial diagnostic PUB EIS/provenance CSV is missing")
        if validation and "pub_validation.csv" not in csv_names:
            raise RuntimeError("Partial diagnostic PUB validation CSV is missing")
        analysis_verified=("treatment_sample_counts.csv" in csv_names and
            (not rows or {"per_pub_eis.csv","validated_pub_provenance.csv"}.issubset(csv_names)) and
            (not validation or "pub_validation.csv" in csv_names))
        if not analysis_verified:raise RuntimeError("Partial diagnostic analysis CSV verification failed")
        artifact_hashes={name:hashlib.sha256((staging/name).read_bytes()).hexdigest() for name in [*artifacts,*csv_names]}
        report={"schema_version":1,"campaign_id":campaign_id,"experiment_id":manifest.experiment_id,
            "target_id":manifest.target_id,"target_n":manifest.target_n,"source_run":manifest.source_run,
            "backend":manifest.backend,"shots_per_pub":manifest.shots_per_pub,
            "input_hashes":manifest.input_hashes,"snapshot_sha256":digest,
            "raw_archives_verified":raw_archives_verified,"provenance_verified":provenance_verified,
            "analysis_state":"PARTIAL_DIAGNOSTIC_ONLY","completed_n_level":False,
            "production_comparison":False,"blocks":[{"logical_block_id":block["logical_block_id"],
                "batch_id":block.get("batch_id"),"status":block["status"]} for block in blocks],
            "treatment_sample_counts":summary,"missing_or_invalid_pubs_by_treatment":missing_by_treatment,
            "unequal_sample_sizes":len({item["validated_pub_count"] for item in summary})>1,
            "warning":warning,"per_pub_eis_count":len(rows),"per_pub_validation_count":len(validation),
            "figures":figure_checks,"analysis_verified":analysis_verified,
            "figures_verified":all(item.get("valid",False) for item in figure_checks.values()),
            "artifacts":artifacts,"artifact_sha256":artifact_hashes,"artifacts_verified":True,
            "scientific_formulas":"mitigation_pilot.calculate_pub_eis; unchanged",
            "created_utc":datetime.now(timezone.utc).isoformat()}
        with (staging/"diagnostic_report.json").open("x",encoding="utf-8") as handle:
            json.dump(report,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
        readback=json.loads((staging/"diagnostic_report.json").read_text(encoding="utf-8"))
        report_verified=(json.dumps(readback,ensure_ascii=False,sort_keys=True,default=str)==
            json.dumps(report,ensure_ascii=False,sort_keys=True,default=str) and readback.get("snapshot_sha256")==digest)
        if not report_verified:raise RuntimeError("Partial diagnostic report read-back verification failed")
        report_hash=hashlib.sha256((staging/"diagnostic_report.json").read_bytes()).hexdigest()
        with (staging/"diagnostic_report.sha256").open("x",encoding="ascii") as handle:
            handle.write(report_hash+"\n");handle.flush();os.fsync(handle.fileno())
        commit=output.parent/f".c_{uuid.uuid4().hex[:8]}"
        shutil.copytree(staging,commit,ignore=shutil.ignore_patterns(".mxs-owner","w"));commit_owned=True
        commit.rename(output);commit_owned=False
    except Exception:
        if commit_owned and commit is not None and commit.exists():shutil.rmtree(commit)
        cleanup_scratch_dir(staging,repo)
        raise
    cleanup_scratch_dir(staging,repo)
    return {"target_id":manifest.target_id,"output":str(output),"snapshot_sha256":digest,
        "partial_diagnostic_only":True,"completed_n_level":False,"production_comparison":False,
        "treatment_sample_counts":summary,"artifacts_verified":True,
        "analysis_verified":analysis_verified,
        "raw_archives_verified":raw_archives_verified,"provenance_verified":provenance_verified,
        "figures_verified":all(item.get("valid",False) for item in figure_checks.values()),
        "report_verified":report_verified,"already_exists":False}


def audit_incomplete_batch_archives(root: str | Path,manifest: TargetManifest,registry,logical_block_id: str) -> dict[str,Any]:
    """Read-only recovery investigation of local provider archives and PUB evidence."""
    import hashlib
    from vqe_adapter import record_directory
    from repository_paths import repository_root
    repo=repository_root(root)
    status=registry.target_status(manifest.target_id)
    block=next((item for item in status.get("blocks",[]) if item["logical_block_id"]==logical_block_id),None)
    if block is None:raise KeyError(logical_block_id)
    jobs={row["treatment_id"]:row for row in registry.rows(logical_block_id)}
    validations=[row for row in registry.pub_validation_records(manifest.target_id)
        if row["logical_block_id"]==logical_block_id]
    valid_values=[row for row in registry.validated_pub_values(manifest.target_id)
        if row["logical_block_id"]==logical_block_id]
    per_treatment={};raw_verified=True;provenance_verified=True
    for treatment in manifest.treatment_ids:
        row=jobs.get(treatment)
        archive_dir=record_directory(repo,logical_block_id)/treatment/"raw_retrievals"
        candidates=list(archive_dir.glob("job_result_*.json")) if archive_dir.is_dir() else []
        adopted_meta=(row or {}).get("metadata",{})
        adopted_path=adopted_meta.get("raw_archive_path")
        if adopted_path:
            source=Path(adopted_path).resolve()
            allowed=(repo/"02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/03_IBM_execution_records"/
                "ai_assistant/calibration/vqe-calibration-pilot-02/raw_results/sequence_001").resolve()
            try:source.relative_to(allowed)
            except ValueError:raise ValueError("Adopted historical archive path is outside its immutable calibration archive")
            if source.is_file():candidates.append(source)
        hashes=[]
        for path in candidates:
            named=path.stem.removeprefix("job_result_")
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if named==actual or (adopted_path and path.resolve()==Path(adopted_path).resolve() and
                                 actual==adopted_meta.get("raw_archive_sha256")):
                hashes.append(actual)
        if not hashes:raw_verified=False
        treatment_validations=[item for item in validations if item["treatment_id"]==treatment]
        treatment_values=[item for item in valid_values if item["treatment_id"]==treatment]
        for item in treatment_validations:
            if (item["batch_id"]!=block.get("batch_id") or item["experiment_id"]!=manifest.experiment_id or
                    item["source_sha256"] not in hashes or item["treatment_id"] not in manifest.treatment_ids):
                provenance_verified=False
        if row is None or row.get("input_hashes")!=manifest.input_hashes or not row.get("job_id"):
            provenance_verified=False
        error_codes=[]
        for item in treatment_validations:
            try:error_codes.extend(json.loads(item["error_codes_json"]))
            except Exception:provenance_verified=False
        per_treatment[treatment]={"expected_pubs":manifest.chunk_n,
            "validation_records":len(treatment_validations),"validated_pubs":len(treatment_values),
            "valid_fraction":len(treatment_values)/float(manifest.chunk_n),
            "error_codes":sorted(set(error_codes)),"archive_count":len(candidates),
            "archive_hashes_verified":bool(hashes)}
    return {"logical_block_id":logical_block_id,"batch_execution_status":"INCOMPLETE",
        "raw_archives_verified":raw_verified,"provenance_verified":provenance_verified,
        "per_treatment":per_treatment,"investigation_source":"local immutable archives and SQLite PUB validation",
        "read_only":True,"automatic_qpu_resubmit":False}
