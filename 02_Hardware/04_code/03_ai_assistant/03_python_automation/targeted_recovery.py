"""Per-logical-PUB recovery for independent-n VQE targets.

Read-only archive revalidation and planning never submit. Live one-PUB and
incident-scoped treatment recovery require explicit authorization and quota gates.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

from low_memory_runtime import iter_provider_jobs

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any


def _verified_raw_source(root, row: dict[str,Any], registry, *, expected_pubs: int, expected_shots: int,
                         expected_hashes: dict[str,str], allow_missing=False) -> tuple[Path,str,dict[str,Any],str,int]:
    """Resolve and verify current or historical job-level raw result archives.

    Older adoption rows recorded ``raw_archive_path/raw_archive_sha256`` rather
    than the newer ``per_pub_source_*`` keys. Calibration archives are also
    resolvable from their recorded campaign, sequence, and IBM job ID. In every
    case the exact same immutable file is hash-checked before use.
    """
    metadata=row.get("metadata",{}) or {}
    source=(metadata.get("per_pub_source_path") or metadata.get("raw_archive_path") or
            metadata.get("source_archive_path"))
    digest=(metadata.get("per_pub_source_sha256") or metadata.get("raw_archive_sha256") or
            metadata.get("source_archive_sha256"))
    source_kind="JOB_RESULT_ARCHIVE"
    if not source and row.get("target_id"):
        references=[item for item in registry.pub_archive_references(row["target_id"])
            if int(item["job_row_id"])==int(row["id"])]
        if references:
            identities={(item["archive_path"],item["archive_sha256"],item["source_job_id"],item["treatment_id"])
                for item in references}
            if len(identities)!=1:
                raise RuntimeError("Existing per-PUB archive references disagree for this treatment job")
            source,digest,_job_id,_treatment=next(iter(identities))
            source_kind=references[0]["source_kind"]
    if not source:
        campaign=metadata.get("source_campaign_id")
        sequence=metadata.get("source_sequence") or metadata.get("source_sequence_no")
        job_id=row.get("job_id")
        if campaign and sequence and job_id:
            records=(Path(root)/"02_Hardware"/"01_vqe"/"06_DD_TREX_ZNE_mitigation"/"output"/
                     "03_IBM_execution_records"/"ai_assistant"/"calibration"/str(campaign)/"raw_results"/
                     f"sequence_{int(sequence):03d}")
            source=str(records/f"{job_id}.json")
            source_kind="HISTORICAL_CALIBRATION_JOB_ARCHIVE"
    if not source:
        raise RuntimeError("No immutable collected PUB archive/hash is recorded for this treatment")
    path=Path(source).resolve()
    output_root=(Path(root)/"02_Hardware"/"01_vqe"/"06_DD_TREX_ZNE_mitigation"/"output"/"03_IBM_execution_records").resolve()
    allowed=output_root in path.parents
    # Offline test registries and their mock provider archives may live under
    # the configured scratch root. A production registry can never use this
    # allowance because it is not itself located in scratch.
    if not allowed:
        # Path validation is read-only: do not mkdir/probe scratch once per PUB.
        from repository_paths import repository_root
        database=Path(registry.database).resolve()
        try:repo=repository_root(root)
        except FileNotFoundError:repo=repository_root()
        candidates=[Path(repo)/'.mxs']
        for key in ('MXENE_SCRATCH_DIR','MXENE_SCRATCH_ROOT'):
            if os.environ.get(key):candidates.append(Path(os.environ[key]))
        for candidate in candidates:
            scratch=candidate.expanduser().resolve()
            if scratch in database.parents and scratch in path.parents:allowed=True
    if not allowed:
        raise RuntimeError("Source result archive resolves outside the IBM execution-record tree")
    if not path.is_file(): raise RuntimeError("Collected PUB archive is missing")
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if not isinstance(digest,str) or len(digest)!=64 or actual!=digest:
        raise RuntimeError("Collected PUB archive is missing its registered SHA-256 or the hash does not match")
    payload=json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload,dict):
        # Historical Qiskit PrimitiveResult was serialized as a JSON string
        # containing repr(...). Preserve those immutable bytes; use only a
        # provenance-verified structured view produced by read-only retrieval.
        sidecar=path.with_name(path.stem+".structured.json")
        if not sidecar.is_file():
            raise RuntimeError(f"Legacy repr archive requires a structured sidecar: {sidecar}")
        derived=json.loads(sidecar.read_text(encoding="utf-8-sig"))
        if (not isinstance(derived,dict) or
            derived.get("archive_format")!="mxene_structured_pubs_v2" or
            derived.get("source_raw_sha256")!=actual or
            derived.get("job_id")!=row.get("job_id") or
            derived.get("treatment_id")!=row.get("treatment_id") or
            not isinstance(derived.get("records"),list)):
            raise RuntimeError(f"Legacy structured sidecar provenance invalid: {sidecar}")
        payload=derived
    job_id=row.get("job_id");treatment=row.get("treatment_id")
    if payload.get("job_id",job_id)!=job_id or payload.get("treatment_id",treatment)!=treatment:
        raise RuntimeError("Immutable archive Job ID or treatment does not match the registered treatment")
    if payload.get("backend",row.get("backend"))!="ibm_fez":
        raise RuntimeError("Immutable archive backend differs from ibm_fez")
    if payload.get("source_run",metadata.get("source_run","run_02"))!="run_02":
        raise RuntimeError("Immutable archive is not tied to Fixed ISA run_02")
    archived_hashes=payload.get("input_hashes") or metadata.get("input_hashes") or row.get("input_hashes")
    if archived_hashes and archived_hashes!=expected_hashes:
        raise RuntimeError("Immutable source archive frozen-input hashes differ from the target")
    if row.get("input_hashes") and row["input_hashes"]!=expected_hashes:
        raise RuntimeError("Registered original treatment frozen-input hashes differ from the target")
    recorded_shots=payload.get("shots_per_pub",metadata.get("shots_per_pub",metadata.get("shots_requested_per_pub",expected_shots)))
    if int(recorded_shots)!=expected_shots:
        raise RuntimeError("Immutable source archive requested shots do not match the VQE mitigation protocol")
    pubs=payload.get("pub_results")
    if pubs is None:
        records=payload.get("records",payload.get("pubs"))
        if isinstance(records,list):pubs=records
    if pubs is not None:
        if not isinstance(pubs,list) or (len(pubs)!=expected_pubs and not (allow_missing and len(pubs)<expected_pubs)):
            raise RuntimeError("Immutable source archive PUB count differs from the original treatment job")
        indices=[int(pub.get("pub_index",idx+1)) for idx,pub in enumerate(pubs)]
        if (indices!=list(range(1,expected_pubs+1)) if not allow_missing else
            len(set(indices))!=len(indices) or any(i not in range(1,expected_pubs+1) for i in indices)):
            raise RuntimeError("Immutable source archive PUB indices are missing, duplicate, or out of order")
    else:
        raise RuntimeError("Immutable job archive does not contain inspectable per-PUB records")
    source_kind=("HISTORICAL_CALIBRATION_JOB_ARCHIVE" if
        metadata.get("historical_adoption") or metadata.get("source_campaign_id") else source_kind)
    return path,actual,payload,source_kind,len(pubs)


def _reconcile_archive_pub_references(registry, job: dict[str,Any], payload: dict[str,Any],
                                      archive_path: Path, archive_sha256: str,
                                      source_kind: str, manifest) -> int:
    pubs=payload.get("pub_results",payload.get("records"))
    if not isinstance(pubs,list):raise RuntimeError("Immutable source archive has no PUB list")
    validation={(int(item["pub_index"]),item["treatment_id"]):item
        for item in registry.pub_validation_records(manifest.target_id)
        if item["logical_block_id"]==job["logical_block_id"]}
    existing_refs={int(item["pub_index"]):item for item in registry.pub_archive_references(manifest.target_id)
        if int(item["job_row_id"])==int(job["id"])}
    added=0
    for expected_index,pub in enumerate(pubs,1):
        fields=pub.get("data_fields",{}) or {}
        evs=pub.get("expectation_values",pub.get("evs",fields.get("evs")))
        try: expectation_valid=(len(evs)==7 and all(math.isfinite(float(value)) for value in evs))
        except (TypeError,ValueError,OverflowError):expectation_valid=False
        stds=pub.get("standard_deviations",pub.get("stds",fields.get("stds")))
        try: uncertainty_valid=(stds is not None and len(stds)==7 and
            all(math.isfinite(float(value)) and float(value)>=0 for value in stds))
        except (TypeError,ValueError,OverflowError):uncertainty_valid=False
        from vqe_adapter import runtime_numerical_failure
        if runtime_numerical_failure(pub.get("metadata",{})):expectation_valid=False
        reported_expectation=pub.get("expectation_validation")
        if reported_expectation is not None and (reported_expectation=="PASS")!=expectation_valid:
            raise RuntimeError(f"PUB {expected_index} archive expectation validity conflicts with the validation ledger")
        reported_uncertainty=pub.get("uncertainty_status")
        if reported_uncertainty is not None and (reported_uncertainty=="PRESENT")!=uncertainty_valid:
            raise RuntimeError(f"PUB {expected_index} archive uncertainty validity conflicts with the validation ledger")
        saved=validation.get((expected_index,job["treatment_id"]))
        if saved is None:
            raise RuntimeError(f"PUB {expected_index} has no adopted per-PUB validation provenance")
        if bool(saved["expectation_valid"])!=expectation_valid:
            raise RuntimeError(f"PUB {expected_index} archive expectation validity conflicts with the adoption ledger")
        if saved.get("uncertainty_valid") is not None and bool(saved["uncertainty_valid"])!=uncertainty_valid:
            raise RuntimeError(f"PUB {expected_index} archive uncertainty validity conflicts with the adoption ledger")
        reference=registry.record_pub_archive_reference(job_row_id=int(job["id"]),pub_index=expected_index,
            source_job_id=job["job_id"],treatment_id=job["treatment_id"],archive_path=str(archive_path),
            archive_sha256=archive_sha256,source_kind=source_kind)
        if expected_index not in existing_refs:added+=1
    return added


def _verify_archived_treatment_options(root, row: dict[str,Any], shots: int) -> None:
    import json
    from vqe_adapter import treatment_options
    metadata=row.get("metadata",{}) or {}
    archived=metadata.get("treatment_options_repr") or metadata.get("runtime_options_requested")
    if not archived:
        raise RuntimeError("Original treatment EstimatorOptions provenance is missing")
    options=treatment_options(row["treatment_id"],shots,root)
    canonical_json=(options.model_dump(mode="json") if callable(getattr(options,"model_dump",None)) else None)
    canonical_repr=str(options)
    if isinstance(archived,str):
        try:archived=json.loads(archived)
        except json.JSONDecodeError:pass
    matched=(archived==canonical_json if isinstance(archived,dict) and canonical_json is not None
        else archived==canonical_repr)
    if not matched:
        raise RuntimeError("Archived mitigation options differ from canonical treatment settings")


def plan_invalid_pub_slots(root, manifest, registry, *, final_config: dict[str,Any] | None=None,
                           explicit_retry_authorization: bool=False,
                           replacement_account: str | None=None,
                           dry_run: bool=False) -> dict[str,Any]:
    """Verify original archives and create idempotent plans only for invalid slots."""
    from vqe_adapter import treatment_options,verify_frozen_inputs
    if replacement_account is not None:
        from final_configuration import load_final_config,validate_final_config
        config=validate_final_config(final_config) if final_config is not None else load_final_config(root)
        if replacement_account not in config["enabled_account_labels"]:
            raise RuntimeError("Replacement account is not enabled in final_config.json")
        if not config["allowed_instance_refs"].get(replacement_account):
            raise RuntimeError("Replacement account has no configured instance scope in final_config.json")
        final_config=config
    circuit,observables,theta,physical,_ideal,_version=verify_frozen_inputs(root)
    if len(theta)!=21 or len(observables)!=7 or tuple(physical)!=(140,141,142,143,144,145,146):
        raise RuntimeError("Frozen Fixed ISA run_02 circuit/layout/observables verification failed before replacement planning")
    if len(circuit.parameters)!=0:raise RuntimeError("Frozen Fixed ISA run_02 circuit contains unbound parameters")
    status=registry.target_status(manifest.target_id)
    if not status.get("exists"):
        raise RuntimeError("Target must be registered before PUB recovery")
    original_values=registry.validated_pub_values(manifest.target_id)
    validations=registry.pub_validation_records(manifest.target_id)
    replacement_rows=registry.replacement_jobs(manifest.target_id)
    planned=[];blocked=[];archive_references_added=0;verified_archives=[]
    for block in status["blocks"]:
        sequence=int(block["sequence_no"])
        for treatment in manifest.treatment_ids:
            jobs=[row for row in registry.rows(block["logical_block_id"]) if row["treatment_id"]==treatment]
            if not jobs: continue
            job=max(jobs,key=lambda item:item["attempt"])
            if job["submission_state"] not in ("COMPLETED","FAILED","CANCELLED"):
                continue
            valid_indexes={int(row["pub_index"]) for row in original_values
                if row["logical_block_id"]==block["logical_block_id"] and row["treatment_id"]==treatment
                and row.get("uncertainty_state")=="PRESENT"}
            # An already accepted replacement fills its global target slot.
            accepted={int(row["logical_pub_index"]) for row in replacement_rows
                if row["treatment_id"]==treatment and row["state"]=="VALIDATED"}
            bad=[]
            for pub_index in range(1,manifest.chunk_n+1):
                global_index=(sequence-1)*manifest.chunk_n+pub_index
                if pub_index in valid_indexes or global_index in accepted: continue
                bad.append((pub_index,global_index))
            if not bad: continue
            try:
                raw_path,source_hash,payload,source_kind,pub_count=_verified_raw_source(root,job,registry,
                    expected_pubs=manifest.chunk_n,expected_shots=manifest.shots_per_pub,
                    expected_hashes=manifest.input_hashes,allow_missing=True)
                _verify_archived_treatment_options(root,job,manifest.shots_per_pub)
                if not dry_run:
                    archive_references_added+=_reconcile_archive_pub_references(registry,job,payload,raw_path,
                        source_hash,source_kind,manifest)
                else:
                    prior_refs=[item for item in registry.pub_archive_references(manifest.target_id)
                        if int(item["job_row_id"])==int(job["id"])]
                    archive_references_added+=max(0,pub_count-len(prior_refs))
                verified_archives.append({"job_id":job["job_id"],"treatment_id":treatment,
                    "path":str(raw_path),"sha256":source_hash,"pub_count":pub_count,"source_kind":source_kind})
            except RuntimeError as exc:
                blocked.append({"block_id":block["logical_block_id"],"treatment_id":treatment,
                    "reason":str(exc),"replacement_planned":False})
                continue
            record_map={(int(item["pub_index"]),item["treatment_id"]):item for item in validations
                if item["logical_block_id"]==block["logical_block_id"]}
            for source_pub,logical_pub in bad:
                record=record_map.get((source_pub,treatment))
                options=treatment_options(treatment,manifest.shots_per_pub,root)
                options=(options.model_dump(mode="json") if callable(getattr(options,"model_dump",None)) else str(options))
                if not isinstance(options,dict):options={"canonical_serialized_options":options}
                prior=next((item for item in replacement_rows if item["treatment_id"]==treatment and
                    int(item["logical_pub_index"])==logical_pub),None)
                desired_account=replacement_account or (prior["account_label"] if prior else block["account_label"])
                if dry_run:
                    planned.append({"replacement_id":prior["replacement_id"] if prior else None,
                        "block_id":block["logical_block_id"],"treatment_id":treatment,
                        "source_pub_index":source_pub,"logical_pub_index":logical_pub,
                        "pub_index_zero_based":source_pub-1,"state":prior["state"] if prior else "DRY_RUN",
                        "source_account_label":block["account_label"],"replacement_account_label":desired_account,
                        "original_job_id":job["job_id"],"replacement_job_id":prior["job_id"] if prior else None,
                        "source_archive_path":str(raw_path),"source_archive_sha256":source_hash})
                    continue
                entry=registry.plan_pub_replacement(target_id=manifest.target_id,
                    logical_block_id=block["logical_block_id"],treatment_id=treatment,
                    source_pub_index=source_pub,logical_pub_index=logical_pub,options=options,
                    allow_new_attempt=bool(final_config and explicit_retry_authorization and
                        final_config.get("auto_recovery_enabled")),
                    max_attempts=None,
                    replacement_account_label=desired_account,
                    metadata={"source_run":manifest.source_run,"source_job_id":job["job_id"],
                        "source_account_label":block["account_label"],
                        "source_archive_path":str(raw_path),"source_archive_sha256":source_hash,
                        "source_validation_errors":json.loads(record["error_codes_json"]) if record else ["MISSING_VALIDATION_RECORD"],
                        "shots_per_pub":manifest.shots_per_pub,"physical_qubits":list(manifest.physical_qubits),
                        "input_hashes":manifest.input_hashes})
                planned.append({"replacement_id":entry["replacement_id"],"block_id":block["logical_block_id"],
                    "treatment_id":treatment,"source_pub_index":source_pub,"logical_pub_index":logical_pub,
                    "pub_index_zero_based":source_pub-1,"state":entry["state"],
                    "source_account_label":entry.get("source_account_label"),
                    "replacement_account_label":entry["account_label"],
                    "original_job_id":job["job_id"],"replacement_job_id":entry["job_id"],
                    "source_archive_path":str(raw_path),"source_archive_sha256":source_hash})
    return {"target_id":manifest.target_id,"target_n":manifest.target_n,"plans":planned,
        "blocked":blocked,"verified_source_archives":verified_archives,
        "pub_archive_references_added":archive_references_added,
        "selected_replacement_account":replacement_account,
        "dry_run":dry_run,"automatic_submission":False,"raw_archives_modified":False}


def _runtime_backend_for_service(service,backend_name: str):
    """Resolve and verify a backend object on the already-authenticated service."""
    if backend_name!="ibm_fez":
        raise RuntimeError("Targeted replacement is restricted to backend ibm_fez")
    backend=service.backend(backend_name)
    resolved_name=getattr(backend,"name",None)
    if callable(resolved_name):
        resolved_name=resolved_name()
    if isinstance(backend,str) or resolved_name!=backend_name:
        raise RuntimeError(f"Authenticated Runtime service returned an unexpected backend for {backend_name}")
    return backend


def _coerce_replacement_estimator_pub(circuit,observables,theta):
    """Build and locally coerce one EstimatorV2 PUB for the circuit's parameter state."""
    from qiskit.primitives.containers import EstimatorPub
    parameter_count=int(circuit.num_parameters)
    if parameter_count==0:
        pub_like=(circuit,observables)
    else:
        parameters=tuple(circuit.parameters)
        values=tuple(float(value) for value in theta)
        if len(parameters)!=parameter_count or len(values)!=parameter_count:
            raise ValueError("Frozen theta length must match the circuit's ordered parameter list")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Parameterized replacement PUB requires finite frozen theta values")
        # EstimatorV2 binds the final axis in circuit.parameters order.
        pub_like=(circuit,observables,values)
    return EstimatorPub.coerce(pub_like)


def reconcile_replacement_submission(registry,target_id: str,replacement_id: int,service,*,
        account_label: str,lookback_seconds: float=60.0,lookahead_seconds: float=300.0,
        page_size: int=100) -> dict[str,Any]:
    """Read-only reconcile an UNKNOWN one-PUB submit before making it retryable.

    Job history is exhaustively paged for the UTC date containing the recorded
    submission claim. A unique job in the bounded claim window is attached;
    an empty window after a complete provider query returns the same logical
    replacement ID to PLANNED and records the evidence. Ambiguous/provider
    failures remain UNKNOWN.
    """
    from datetime import datetime,timedelta,timezone
    from read_only_preflight import _instance_ref
    if (not math.isfinite(float(lookback_seconds)) or lookback_seconds<0
            or not math.isfinite(float(lookahead_seconds)) or lookahead_seconds<=0):
        raise ValueError("Reconciliation time bounds must be finite and nonnegative")
    if isinstance(page_size,bool) or not isinstance(page_size,int) or page_size<1:
        raise ValueError("page_size must be a positive integer")
    rows=registry.replacement_jobs(target_id)
    item=next((row for row in rows if int(row["replacement_id"])==int(replacement_id)),None)
    if item is None: raise KeyError(replacement_id)
    if item["state"]!="UNKNOWN" or item.get("job_id") is not None or item.get("batch_id") is not None:
        raise RuntimeError("Only an UNKNOWN replacement without a Job ID or Batch ID can be reconciled")
    if item["account_label"]!=account_label:
        raise RuntimeError("Reconciliation account must match the registered replacement account")
    expected_ref=item.get("metadata",{}).get("instance_ref")
    active=service.active_instance()
    active_ref=_instance_ref(active)
    if not expected_ref or active_ref!=expected_ref:
        raise RuntimeError("Authenticated saved-profile instance does not match the replacement's registered scope")
    _runtime_backend_for_service(service,item["backend"])
    changed=datetime.fromisoformat(str(item["updated_at"]).replace("Z","+00:00"))
    if changed.tzinfo is None: changed=changed.replace(tzinfo=timezone.utc)
    claimed_at=changed.astimezone(timezone.utc)
    window_start=claimed_at-timedelta(seconds=float(lookback_seconds))
    window_end=claimed_at+timedelta(seconds=float(lookahead_seconds))
    day_start=claimed_at.replace(hour=0,minute=0,second=0,microsecond=0)
    day_end=day_start+timedelta(days=1)
    found=[];total=0;skip=0;pages=0
    try:
        while True:
            page=service.jobs(limit=page_size,skip=skip,backend_name=item["backend"],
                instance=active,created_after=day_start,created_before=day_end,descending=False)
            pages+=1;total+=len(page)
            for job in page:
                created=getattr(job,"creation_date",None)
                if callable(created): created=created()
                if created is None: continue
                if isinstance(created,str): created=datetime.fromisoformat(created.replace("Z","+00:00"))
                if created.tzinfo is None: created=created.replace(tzinfo=timezone.utc)
                created=created.astimezone(timezone.utc)
                if not window_start<=created<=window_end: continue
                job_id=getattr(job,"job_id",None)
                if callable(job_id): job_id=job_id()
                backend=getattr(job,"backend",None)
                if callable(backend): backend=backend()
                resolved=getattr(backend,"name",None)
                if callable(resolved): resolved=resolved()
                if not job_id or resolved!=item["backend"]: continue
                job_instance=getattr(job,"instance",None)
                if callable(job_instance): job_instance=job_instance()
                if job_instance is not None and _instance_ref(job_instance)!=expected_ref: continue
                session=getattr(job,"session_id",None)
                if callable(session): session=session()
                found.append({"job_id":str(job_id),"batch_id":str(session) if session else None,
                    "created_utc":created.isoformat().replace("+00:00","Z")})
            if len(page)<page_size: break
            skip+=len(page)
    except Exception as exc:
        registry.transition_pub_replacement(replacement_id,"UNKNOWN",
            metadata={"reconciliation_query_error_type":type(exc).__name__},
            event_type="REPLACEMENT_RECONCILIATION_QUERY_FAILED",
            details={"error_type":type(exc).__name__,"provider_evidence_complete":False})
        raise RuntimeError("Provider reconciliation query failed; replacement remains UNKNOWN") from exc
    checked=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    evidence={"method":"QiskitRuntimeService.jobs","provider_query_complete":True,
        "provider_query_account_label":account_label,"instance_ref":expected_ref,
        "backend":item["backend"],"query_day_start_utc":day_start.isoformat().replace("+00:00","Z"),
        "query_day_end_utc":day_end.isoformat().replace("+00:00","Z"),
        "claim_window_start_utc":window_start.isoformat().replace("+00:00","Z"),
        "claim_window_end_utc":window_end.isoformat().replace("+00:00","Z"),
        "provider_jobs_in_query_day":total,"provider_query_pages":pages,
        "candidate_jobs_in_claim_window":found,"checked_utc":checked}
    def refreshed():
        return next(row for row in registry.replacement_jobs(target_id)
            if int(row["replacement_id"])==int(replacement_id))
    if len(found)==1:
        candidate=found[0]
        registry.transition_pub_replacement(replacement_id,"SUBMITTED",job_id=candidate["job_id"],
            batch_id=candidate["batch_id"],metadata={"submission_reconciliation":evidence},
            event_type="REPLACEMENT_RECONCILED_JOB_FOUND",
            details={"job_id":candidate["job_id"],"batch_id":candidate["batch_id"],
                "provider_evidence_complete":True})
        return refreshed()
    if found:
        registry.transition_pub_replacement(replacement_id,"UNKNOWN",
            metadata={"submission_reconciliation":evidence},
            event_type="REPLACEMENT_RECONCILIATION_AMBIGUOUS",
            details={"candidate_job_ids":[item["job_id"] for item in found],
                "provider_evidence_complete":True})
        raise RuntimeError("Multiple provider jobs match the submission window; replacement remains UNKNOWN")
    registry.transition_pub_replacement(replacement_id,"PLANNED",quota_reserved_seconds=0,
        metadata={"submission_reconciliation":evidence,"retryable_after_reconciliation":True},
        event_type="REPLACEMENT_CONFIRMED_NOT_SUBMITTED",
        details={"provider_evidence_complete":True,"provider_jobs_in_query_day":total,
            "candidate_jobs_in_claim_window":0,"replacement_id":int(replacement_id)})
    return refreshed()


def reconcile_replacement_batch(registry,target_id: str,replacement_id: int,service,*,
        account_label: str,batch_id: str,page_size: int=100) -> dict[str,Any]:
    """Reconcile one persisted Runtime Batch and its complete job inventory read-only."""
    from read_only_preflight import _instance_ref
    from low_memory_runtime import remote_runtime
    remote_runtime()
    from qiskit_ibm_runtime import Batch
    from job_registry import utc_now
    if isinstance(page_size,bool) or not isinstance(page_size,int) or page_size<1:
        raise ValueError("page_size must be a positive integer")
    item=next((row for row in registry.replacement_jobs(target_id)
        if int(row["replacement_id"])==int(replacement_id)),None)
    if item is None: raise KeyError(replacement_id)
    if (item["state"]!="UNKNOWN" or item.get("job_id") is not None
            or item.get("batch_id")!=batch_id):
        raise RuntimeError("Replacement must be UNKNOWN with this exact registered Batch ID and no Job ID")
    if item["account_label"]!=account_label:
        raise RuntimeError("Reconciliation account must match the registered replacement account")
    expected_ref=item.get("metadata",{}).get("instance_ref")
    active=service.active_instance()
    if not expected_ref or _instance_ref(active)!=expected_ref:
        raise RuntimeError("Authenticated saved-profile instance does not match the replacement scope")
    _runtime_backend_for_service(service,item["backend"])
    try:
        runtime_batch=Batch.from_id(batch_id,service=service)
        status=str(runtime_batch.status() or "").upper()
        details=runtime_batch.details() or {}
        provider_id=details.get("id") or details.get("session_id") or batch_id
        if str(provider_id)!=batch_id:
            raise RuntimeError("Provider returned a different Batch ID")
        accepting=details.get("accepting_jobs")
        if status not in {"CLOSED","INACTIVE"} or accepting is not False:
            raise RuntimeError("Provider has not confirmed this Batch is closed to submissions")
        jobs=[];skip=0;pages=0
        while True:
            page=service.jobs(limit=page_size,skip=skip,session_id=batch_id,
                instance=active,descending=False)
            pages+=1
            for job in page:
                def value(obj,name):
                    result=getattr(obj,name,None)
                    return result() if callable(result) else result
                job_id=value(job,"job_id")
                session=value(job,"session_id")
                backend=value(job,"backend")
                backend_name=getattr(backend,"name",None) if backend is not None else None
                backend_name=backend_name() if callable(backend_name) else backend_name
                if str(session or "")!=batch_id:
                    raise RuntimeError("Batch-scoped provider query returned an unscoped job")
                jobs.append({"job_id":str(job_id) if job_id else None,
                    "session_id":str(session),"backend":backend_name,
                    "status":str(value(job,"status")) if value(job,"status") is not None else None})
            if len(page)<page_size: break
            skip+=len(page)
    except Exception as exc:
        registry.transition_pub_replacement(replacement_id,"UNKNOWN",
            metadata={"batch_reconciliation_error_type":type(exc).__name__},
            event_type="REPLACEMENT_BATCH_RECONCILIATION_FAILED",
            details={"batch_id":batch_id,"provider_evidence_complete":False,
                "error_type":type(exc).__name__})
        raise RuntimeError("Exact Batch reconciliation failed; replacement remains UNKNOWN") from exc
    evidence={"method":"QiskitRuntimeService.jobs(session_id=...) and Batch.from_id",
        "provider_query_complete":True,"account_label":account_label,"instance_ref":expected_ref,
        "backend":item["backend"],"batch_id":batch_id,"batch_status":status,
        "accepting_jobs":accepting,"state_reason":details.get("state_reason"),
        "provider_job_count":len(jobs),"provider_query_pages":pages,
        "jobs":jobs,"checked_utc":utc_now()}
    def refreshed():
        return next(row for row in registry.replacement_jobs(target_id)
            if int(row["replacement_id"])==int(replacement_id))
    if len(jobs)==1 and jobs[0]["job_id"]:
        job=jobs[0]
        registry.transition_pub_replacement(replacement_id,"SUBMITTED",job_id=job["job_id"],
            metadata={"submission_reconciliation":evidence},
            event_type="REPLACEMENT_BATCH_RECONCILED_JOB_FOUND",
            details={"batch_id":batch_id,"job_id":job["job_id"],"provider_evidence_complete":True})
        return refreshed()
    if jobs:
        registry.transition_pub_replacement(replacement_id,"UNKNOWN",
            metadata={"submission_reconciliation":evidence},
            event_type="REPLACEMENT_BATCH_RECONCILIATION_AMBIGUOUS",
            details={"batch_id":batch_id,"job_count":len(jobs),"provider_evidence_complete":True})
        raise RuntimeError("Batch contains multiple or unidentified jobs; replacement remains UNKNOWN")
    registry.transition_pub_replacement(replacement_id,"PLANNED",quota_reserved_seconds=0,
        metadata={"submission_reconciliation":evidence,"retryable_after_reconciliation":True},
        event_type="REPLACEMENT_CONFIRMED_NOT_SUBMITTED",
        details={"batch_id":batch_id,"batch_status":status,"accepting_jobs":False,
            "provider_evidence_complete":True,"provider_job_count":0})
    return refreshed()


def submit_replacement_slot(root, manifest, registry, replacement_id: int, service, *,
        final_config: dict[str,Any], campaign_authorization: dict[str,Any],
        preflight_report: dict[str,Any], runtime_estimate, explicit_authorization: bool,
        active_instance_ref: str, authorized_instance_refs: list[str],
        batch_factory=None, estimator_factory=None) -> dict[str,Any]:
    """Submit one invalid PUB in its own new Runtime Batch after all safety gates."""
    from final_configuration import validate_final_config
    from adaptive_targets import usage_fields_from_report
    from vqe_adapter import verify_frozen_inputs, treatment_options
    config=validate_final_config(final_config)
    if not explicit_authorization or not config["live_submission_enabled"]:
        raise RuntimeError("Replacement QPU action requires explicit authorization and live_submission_enabled=true")
    if not campaign_authorization.get("live_auto_submit") or "submit" not in campaign_authorization.get("allowed_actions",[]):
        raise RuntimeError("Existing campaign authorization does not permit live submission")
    rows=registry.replacement_jobs(manifest.target_id)
    item=next((row for row in rows if int(row["replacement_id"])==int(replacement_id)),None)
    if item is None: raise KeyError(replacement_id)
    if item["state"]!="PLANNED": raise RuntimeError("Only a durable PLANNED slot can be submitted; reconcile other states")
    from datetime import datetime,timezone
    due=item['metadata'].get('not_before_utc')
    if due:
        remaining_delay=(datetime.fromisoformat(due.replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()
        if remaining_delay>0:
            return {'replacement_id':replacement_id,'state':'PLANNED','retry_after_seconds':remaining_delay}
    if any(row["treatment_id"]==item["treatment_id"] and row["logical_pub_index"]==item["logical_pub_index"]
           for row in registry.repaired_pub_values(manifest.target_id)):
        raise RuntimeError("Logical PUB slot already has a validated observation; no resubmission")
    if item["account_label"] not in config["enabled_account_labels"] or item["backend"]!=config["allowed_backend"]:
        raise RuntimeError("Replacement account/backend is not enabled in final configuration")
    source_account=item.get("source_account_label") or item["metadata"].get("source_account_label")
    source_jobs=[row for row in registry.rows(item["logical_block_id"]) if row["treatment_id"]==item["treatment_id"]]
    if not source_jobs or source_jobs[-1]["account_label"]!=source_account:
        raise RuntimeError("Replacement source-account provenance differs from the original treatment job")
    if not active_instance_ref or active_instance_ref not in set(authorized_instance_refs):
        raise RuntimeError("Selected saved-profile instance is not explicitly authorized")
    if item["metadata"].get("input_hashes")!=manifest.input_hashes:
        raise RuntimeError("Replacement frozen-input hashes differ from the target")
    if (getattr(runtime_estimate,"scope",None)!="single_treatment_replacement"
            or getattr(runtime_estimate,"treatment_id",None)!=item["treatment_id"]
            or getattr(runtime_estimate,"pub_count",None)!=1
            or getattr(runtime_estimate,"target_n",None)!=1):
        raise RuntimeError("Replacement submission requires a defensible one-PUB estimate for this exact treatment")
    if item["metadata"].get("source_run")!="run_02":
        raise RuntimeError("Replacement is not traced to Fixed ISA run_02")
    usage=usage_fields_from_report(preflight_report)
    campaign_id,_level=registry.independent_campaign_context(manifest.target_id)
    if campaign_id:
        from independent_n_campaign import _campaign_usage
        campaign_manifest=registry.independent_campaign_status(campaign_id)["manifest"]
        local_usage=_campaign_usage(registry,campaign_manifest,campaign_authorization)
        account_usage=local_usage["per_account"].get(item["account_label"])
        if account_usage is None:raise RuntimeError("Replacement account is outside campaign quota scope")
        account_headroom=float(campaign_authorization["account_runtime_budget_seconds"][item["account_label"]])-account_usage["consumed_seconds"]-account_usage["reserved_seconds"]
        from quota import campaign_budget_monitor,log_campaign_budget_monitor
        budget_monitor=campaign_budget_monitor(campaign_authorization,local_usage,
            runtime_estimate.stress_block_seconds+config["safety_margin_seconds"])
        log_campaign_budget_monitor(budget_monitor)
        provider_remaining=usage["remaining_seconds"] if usage else None
        remaining=min(provider_remaining,account_headroom) if provider_remaining is not None else None
    else:
        remaining=usage["remaining_seconds"] if usage else None
    # Single-PUB provider threshold is independent of full-Batch eligibility.
    if (preflight_report.get("status") != "PASS" or
            preflight_report.get("submission_eligible") is not True or
            preflight_report.get("account_label") != item["account_label"] or
            preflight_report.get("configured_instance_ref") != active_instance_ref or
            preflight_report.get("backend") != manifest.backend):
        raise RuntimeError("Fresh replacement account/backend/instance preflight is not eligible")
    if usage is None or remaining is None:
        raise RuntimeError("Replacement provider quota is missing or ambiguous")
    from runtime_estimator import workload_quota_decision
    replacement_quota = workload_quota_decision(
        runtime_estimate, remaining_seconds=remaining,
        workload_type="targeted_replacement",
        safety_margin_seconds=config["safety_margin_seconds"],
        provider_remaining_seconds=usage["remaining_seconds"])
    if not replacement_quota["eligible"]:
        from quota import AccountUnavailable
        raise AccountUnavailable("Targeted replacement quota gate refused: " + "; ".join(replacement_quota["reasons"]))
    bound = float(runtime_estimate.stress_block_seconds)
    if not math.isfinite(bound) or bound <= 0:
        raise RuntimeError("Replacement runtime estimate must be finite and positive")
    # No separate replacement budget. The provider and authorized account
    # accounting is preserved; only the account limit is enforced locally.
    verify_frozen_inputs(root)
    if batch_factory is None or estimator_factory is None:
        from batch_scheduler import _runtime_types
        real_batch,real_estimator=_runtime_types();batch_factory=batch_factory or real_batch;estimator_factory=estimator_factory or real_estimator
    circuit,observables,theta,physical,_,_=verify_frozen_inputs(root)
    if len(theta)!=21 or len(observables)!=7 or tuple(physical)!=(140,141,142,143,144,145,146):
        raise RuntimeError("Frozen circuit/layout/observable verification failed")
    treatment=item["treatment_id"]
    options=treatment_options(treatment,manifest.shots_per_pub,root)
    try:
        backend=_runtime_backend_for_service(service,manifest.backend)
    except Exception as exc:
        from quota import AccountUnavailable
        raise AccountUnavailable("Read-only replacement backend unavailable: "+type(exc).__name__) from exc
    # Validate the exact PUB shape before claiming the replacement or creating
    # a Runtime Batch, so local coercion errors cannot leave a false submit intent.
    pub=_coerce_replacement_estimator_pub(circuit,observables,theta)
    batch=None
    from batch_scheduler import _active_instance
    try:
        actual_instance=_active_instance(service,None)
    except Exception as exc:
        from quota import AccountUnavailable
        raise AccountUnavailable("Read-only replacement instance unavailable: "+type(exc).__name__) from exc
    from read_only_preflight import _instance_ref
    if actual_instance is not None and _instance_ref(actual_instance)!=str(active_instance_ref):
        raise RuntimeError("Runtime active instance differs from the authorized preflight instance")
    from quota import provider_quota_fields, provider_reservations, available_quota_seconds
    try:
        if service.jobs(limit=1,pending=True,instance=actual_instance):
            raise RuntimeError("Replacement provider quota has pending workloads")
        refreshed=provider_quota_fields(service.usage())
        if service.active_instance()!=actual_instance:
            raise RuntimeError("Replacement provider quota instance changed")
    except Exception as exc:
        from quota import AccountUnavailable
        raise AccountUnavailable("Read-only replacement quota refresh failed before submission: "+type(exc).__name__) from exc
    reservations=provider_reservations(registry)[item["account_label"]]
    if reservations["ambiguous"]:
        raise RuntimeError("Replacement quota reservation overlap is ambiguous")
    fresh_available=available_quota_seconds(provider_remaining_seconds=refreshed["remaining_seconds"],
        outstanding_reservations_seconds=reservations["reserved_seconds"])
    fresh_gate=workload_quota_decision(runtime_estimate,remaining_seconds=min(remaining,fresh_available),
        workload_type="targeted_replacement",safety_margin_seconds=config["safety_margin_seconds"],
        provider_remaining_seconds=refreshed["remaining_seconds"])
    if not fresh_gate["eligible"]:
        from quota import AccountUnavailable
        raise AccountUnavailable("Fresh replacement quota is insufficient: "+"; ".join(fresh_gate["reasons"]))
    registry.claim_pub_replacement_submission(replacement_id,reservation_seconds=fresh_gate["reservation_seconds"],
        preflight_checked_at=preflight_report["checked_utc"],quota_scope=str(preflight_report.get("usage_scope")),
        runtime_upper_bound_seconds=bound,safety_margin_seconds=config["safety_margin_seconds"],
        instance_ref=active_instance_ref,available_budget_seconds=fresh_gate["remaining_seconds"],
        expected_reserved_seconds=reservations["reserved_seconds"])
    from datetime import datetime,timezone
    stamp=lambda:datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    try:
        with batch_factory(backend=backend) as runtime_batch:
            batch=runtime_batch
            batch_created_utc=stamp()
            batch_id=getattr(runtime_batch,"session_id",None)
            if callable(batch_id): batch_id=batch_id()
            if batch_id is not None:
                registry.transition_pub_replacement(replacement_id,"SUBMITTING",batch_id=str(batch_id),
                    event_type="REPLACEMENT_BATCH_ID_PERSISTED",metadata={"batch_created_utc":batch_created_utc,
                        "replacement_account_label":item["account_label"],
                        "source_account_label":source_account,"instance_ref":active_instance_ref,
                        "runtime_options_requested":item["options"]},
                    details={"backend":manifest.backend,"replacement_account_label":item["account_label"],
                        "source_account_label":source_account,"instance_ref":active_instance_ref,
                        "batch_created_utc":batch_created_utc})
            estimator=estimator_factory(mode=runtime_batch,options=options)
            job=estimator.run([pub])
            job_id=job.job_id()
            if not isinstance(job_id,str) or not job_id.strip():raise RuntimeError('Provider returned no replacement job ID')
            submitted_utc=stamp()
            if batch_id is None:
                for name in ("batch_id","id"):
                    value=getattr(runtime_batch,name,None)
                    if callable(value): value=value()
                    if value: batch_id=str(value);break
            registry.transition_pub_replacement(replacement_id,"SUBMITTED",job_id=str(job_id),batch_id=batch_id,
                event_type="REPLACEMENT_JOB_ID_PERSISTED",metadata={"submitted_utc":submitted_utc,
                    "replacement_account_label":item["account_label"],
                    "source_account_label":source_account,"instance_ref":active_instance_ref,
                    "backend":manifest.backend,"input_hashes":manifest.input_hashes,
                    "shots_per_pub":manifest.shots_per_pub,"runtime_options_requested":item["options"]},
                details={"treatment_id":treatment,"logical_pub_index":item["logical_pub_index"],
                    "replacement_account_label":item["account_label"],"source_account_label":source_account,
                    "instance_ref":active_instance_ref,"backend":manifest.backend,"submitted_utc":submitted_utc,
                    "qpu_resubmit_of_original":False})
        return {"replacement_id":replacement_id,"job_id":str(job_id),"batch_id":batch_id,
            "state":"SUBMITTED","treatment_id":treatment,"logical_pub_index":item["logical_pub_index"]}
    except BaseException as exc:
        # Once estimator.run may have executed, never automatically retry.
        current=next(r for r in registry.replacement_jobs(manifest.target_id) if r['replacement_id']==replacement_id)
        if not current['job_id']:
            registry.transition_pub_replacement(replacement_id,"UNKNOWN",
                event_type="REPLACEMENT_SUBMIT_AMBIGUOUS",details={"error_type":type(exc).__name__,"automatic_retry":False})
        raise


def revalidate_replacement_archive(root,manifest,registry,replacement_id: int) -> dict[str,Any]:
    """Validate one stored replacement archive without contacting IBM."""
    rows=registry.replacement_jobs(manifest.target_id)
    item=next((row for row in rows if int(row["replacement_id"])==int(replacement_id)),None)
    if item is None: raise KeyError(replacement_id)
    if item["state"]=="VALIDATED": return {"replacement_id":replacement_id,"state":"VALIDATED","idempotent":True}
    if item.get("raw_archive_path") and item.get("raw_archive_sha256"):
        archive=Path(item["raw_archive_path"])
        if archive.is_file() and hashlib.sha256(archive.read_bytes()).hexdigest()==item["raw_archive_sha256"]:
            saved=json.loads(archive.read_text(encoding="utf-8"));records=saved.get("records",[])
            errors=[];record=records[0] if len(records)==1 else None
            if len(records)!=1:errors.append("PUB_COUNT_INVALID")
            evs=record.get("expectation_values") if record else None;stds=record.get("standard_deviations") if record else None
            if not record or record.get("expectation_validation")!="PASS":errors.append("INVALID_EXPECTATION")
            if record and record.get("uncertainty_status")=="INVALID":errors.append("INVALID_UNCERTAINTY")
            if record and record.get("uncertainty_status")=="MISSING":errors.append("UNCERTAINTY_MISSING")
            state=registry.archive_pub_replacement(replacement_id,archive_path=str(archive),
                archive_sha256=item["raw_archive_sha256"],expectation_values=evs,standard_deviations=stds,
                uncertainty_state=record.get("uncertainty_status","MISSING") if record else "MISSING",
                error_codes=errors,qpu_usage_seconds=item.get("qpu_usage_seconds"),
                provider_metadata={"revalidated_from_immutable_archive":True})
            return {"replacement_id":replacement_id,"state":state["state"],"archive":str(archive),
                "archive_sha256":item["raw_archive_sha256"],"errors":errors,"revalidated_from_archive":True}
    raise RuntimeError("No verified immutable replacement archive is available for offline validation")


def collect_replacement(root, manifest, registry, replacement_id: int, service) -> dict[str,Any]:
    """Read one registered replacement job, archive its response once, validate per PUB."""
    from vqe_adapter import capture_runtime_result
    rows=registry.replacement_jobs(manifest.target_id)
    item=next((row for row in rows if int(row["replacement_id"])==int(replacement_id)),None)
    if item is None: raise KeyError(replacement_id)
    if item["state"]=="VALIDATED": return {"replacement_id":replacement_id,"state":"VALIDATED","idempotent":True}
    if item.get("raw_archive_path") and item.get("raw_archive_sha256"):
        return revalidate_replacement_archive(root,manifest,registry,replacement_id)
    if item["state"] not in {"SUBMITTED","DONE"} or not item["job_id"]:
        raise RuntimeError("Replacement has no unambiguous registered IBM job ID")
    job=service.job(item["job_id"])
    status=str(job.status()).upper().split(".")[-1]
    if status not in {"DONE","COMPLETED","ERROR","FAILED","CANCELLED","CANCELED"}:
        return {"replacement_id":replacement_id,"state":"SUBMITTED","provider_status":status}
    from worker_runtime import terminal_result,TerminalResultUnavailable
    evidence=None
    try:result=terminal_result(job)
    except TerminalResultUnavailable as exc:
        result=[];evidence=exc.evidence
    registry.transition_pub_replacement(replacement_id,"DONE",event_type="REPLACEMENT_PROVIDER_TERMINAL",
        details={"provider_status":status})
    captured=capture_runtime_result(result,"replacement PUB")
    del result
    records=captured["records"]
    errors=[]
    mapping=item['metadata'].get('replacement_pub_map')
    provider_archive=None
    if mapping:
        expected_indices=list(range(1,len(mapping)+1))
        if ([entry.get('pub_index') for entry in mapping]!=expected_indices or
                len({entry.get('replacement_id') for entry in mapping})!=len(mapping)):
            raise RuntimeError('Ambiguous replacement PUB mapping')
        by_id={row['replacement_id']:row for row in rows}
        for entry in mapping:
            sibling=by_id.get(entry['replacement_id'])
            if (not sibling or sibling['metadata'].get('replacement_pub_map')!=mapping or
                    sibling['job_id']!=item['job_id'] or
                    entry.get('logical_pub_index')!=sibling['logical_pub_index'] or
                    entry.get('treatment_id')!=sibling['treatment_id']):
                raise RuntimeError('Contradictory replacement PUB mapping')
        selected=next((entry['pub_index'] for entry in mapping if entry['replacement_id']==replacement_id),None)
        if selected is None:raise RuntimeError('Replacement is missing from explicit PUB mapping')
        # Archive the entire response before projecting one slot. Each sibling
        # keeps its own independently validated immutable per-slot archive.
        folder=Path(root)/'02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/03_IBM_execution_records/ai_assistant/replacements'/manifest.target_id
        folder.mkdir(parents=True,exist_ok=True)
        blob=json.dumps({'job_id':item['job_id'],'replacement_pub_map':mapping,'captured':captured},sort_keys=True,allow_nan=False,default=str).encode()
        digest=hashlib.sha256(blob).hexdigest();path=folder/('provider-'+digest+'.json')
        from repository_paths import atomic_archive
        atomic_archive(path,(blob,),digest)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('Provider archive hash mismatch')
        provider_archive={'path':str(path),'sha256':digest,'pub_index':selected}
        if len(records)>len(mapping):raise RuntimeError('Unexpected PUBs contradict replacement intent')
        record=records[selected-1] if selected<=len(records) else None
        records=[record] if record else []
    else:
        if captured['pub_count']!=1:errors.append('PUB_COUNT_INVALID')
        record=records[0] if records else None
    evs=record.get("expectation_values") if record else None
    stds=record.get("standard_deviations") if record else None
    if not record or record.get("expectation_validation")!="PASS": errors.append("INVALID_EXPECTATION")
    if record and record.get("uncertainty_status")=="INVALID": errors.append("INVALID_UNCERTAINTY")
    if record and record.get("uncertainty_status")=="MISSING": errors.append("UNCERTAINTY_MISSING")
    # Missing uncertainty stays explicitly missing and does not invalidate finite expectations.
    metrics=job.metrics() if callable(getattr(job,"metrics",None)) else {}
    from quota import qpu_seconds_from_metrics
    usage=qpu_seconds_from_metrics(metrics)
    # Preserve only provider runtime metadata needed for provenance/calibration;
    # never persist credentials, request headers or arbitrary service objects.
    provider_metrics={}
    if isinstance(metrics,dict):
        if isinstance(metrics.get("usage"),dict): provider_metrics["usage"]=metrics["usage"]
        for field in ("timestamps","circuits_execution_time_ns","backend","job_id"):
            if field in metrics: provider_metrics[field]=metrics[field]
    raw={"schema_version":1,"target_id":manifest.target_id,"experiment_id":manifest.experiment_id,
        "replacement_id":replacement_id,"replacement_job_id":item["job_id"],"batch_id":item["batch_id"],
        "source_run":"run_02","backend":item["backend"],"account_label":item["account_label"],
        "source_account_label":item["source_account_label"],"instance_ref":item["metadata"].get("instance_ref"),
        "submitted_utc":item["metadata"].get("submitted_utc"),
        "original_source_job_id":item["metadata"].get("source_job_id"),
        "original_source_archive_path":item["metadata"].get("source_archive_path"),
        "original_source_archive_sha256":item["metadata"].get("source_archive_sha256"),
        "treatment_id":item["treatment_id"],"source_pub_index":item["source_pub_index"],
        "logical_pub_index":item["logical_pub_index"],"input_hashes":manifest.input_hashes,
        "shots_per_pub":manifest.shots_per_pub,
        "runtime_options_requested":item["metadata"].get("runtime_options_requested",item["options"]),
        "terminal_retrieval_evidence":evidence,
        "provider_metrics":provider_metrics,"qpu_usage_seconds":usage,
        "usage_source":"provider_job_metrics" if usage is not None else None,
        "records":records,"provider_result_archive":provider_archive,"result_metadata":captured.get("metadata"),
        "expectation_errors":captured.get("expectation_errors"),"uncertainty_errors":captured.get("uncertainty_errors")}
    encoded=json.dumps(raw,sort_keys=True,separators=(",",":"),allow_nan=False,default=str).encode("utf-8")
    base=Path(root)/"02_Hardware"/"01_vqe"/"06_DD_TREX_ZNE_mitigation"/"output"/"03_IBM_execution_records"/"ai_assistant"/"replacements"/manifest.target_id/str(replacement_id)
    base.mkdir(parents=True,exist_ok=True);archive=base/"raw_result.json"
    digest=hashlib.sha256(encoded).hexdigest()
    from repository_paths import atomic_archive
    atomic_archive(archive,(encoded,),hashlib.sha256(encoded).hexdigest())
    stored=registry.archive_pub_replacement(replacement_id,archive_path=str(archive),archive_sha256=digest,
        expectation_values=evs,standard_deviations=stds,
        uncertainty_state=record.get("uncertainty_status","MISSING") if record else "MISSING",
        error_codes=errors,qpu_usage_seconds=usage,provider_metadata={"provider_status":status,
            "metrics_available":bool(provider_metrics),"usage_source":"provider_job_metrics" if usage is not None else None,
            "usage_verified":usage is not None,"response_pub_count":captured["pub_count"]})
    return {"replacement_id":replacement_id,"state":stored["state"],"archive":str(archive),
        "archive_sha256":digest,"qpu_usage_seconds":stored["qpu_usage_seconds"],"errors":errors}


def repaired_dataset_status(manifest, registry) -> dict[str,Any]:
    """Determine completeness from selected original/replacement observations."""
    rows=registry.repaired_pub_values(manifest.target_id)
    counts={name:sum(row["treatment_id"]==name for row in rows) for name in manifest.treatment_ids}
    duplicate_slots=len({(row["treatment_id"],row["logical_pub_index"]) for row in rows})!=len(rows)
    from batch_progression import evaluate_batch_progression
    target_status=registry.target_status(manifest.target_id)
    blocks=target_status.get("blocks",[])
    decisions=[evaluate_batch_progression(registry,manifest,b["logical_block_id"]) for b in blocks]
    complete=(len(blocks)==manifest.blocks_required and not duplicate_slots and
              all(d["eligible"] for d in decisions))
    has_replacements=any(d["dataset_status"]=="REPAIRED_COMPLETE" for d in decisions)
    execution_complete=(len(blocks)==manifest.blocks_required and all(
        block.get("status") in ("VALIDATED","COMPLETE") for block in blocks))
    status=("REPAIRED_COMPLETE" if has_replacements else "COMPLETE") if complete else "INCOMPLETE"
    return {"target_id":manifest.target_id,"status":status,"counts_by_treatment":counts,
        "expected_per_treatment":manifest.target_n,"selected_observation_count":len(rows),
        "duplicate_slots":duplicate_slots,"rows":rows,"execution_complete":execution_complete,
        "analysis_eligible":complete}


def assemble_repaired_dataset(root, manifest, registry) -> dict[str,Any]:
    """Write a content-verified, versioned selected-PUB dataset when complete."""
    import csv
    from datetime import datetime,timezone
    status=repaired_dataset_status(manifest,registry)
    if not status["analysis_eligible"]:
        return {key:value for key,value in status.items() if key!="rows"}|{"dataset_path":None}
    selected=status["rows"]
    if len({(row["treatment_id"],row["logical_pub_index"]) for row in selected})!=len(selected):
        raise RuntimeError("Repaired dataset contains duplicate logical PUB slots")
    expected=manifest.target_n*len(manifest.treatment_ids)
    if len(selected)!=expected: raise RuntimeError("Repaired dataset does not contain exactly n PUBs per treatment")
    if any(row["input_hashes"]!=manifest.input_hashes for row in selected):
        raise RuntimeError("Repaired dataset contains mismatched frozen-input provenance")
    originals={(row["job_id"],row["treatment_id"]):row for row in registry.rows()}
    replacements={row["replacement_id"]:row for row in registry.replacement_jobs(manifest.target_id)}
    for row in selected:
        if row["source"] in ("ORIGINAL","TREATMENT_REPLACEMENT"):
            source=originals.get((row["job_id"],row["treatment_id"]))
            if not source: raise RuntimeError("Selected original PUB has no immutable job registry row")
            path,digest,_payload,_kind,_count=_verified_raw_source(root,source,registry,
                expected_pubs=manifest.chunk_n,expected_shots=manifest.shots_per_pub,
                expected_hashes=manifest.input_hashes,allow_missing=True)
            if digest!=row["source_sha256"]:
                # Some legacy per-PUB ledgers refer to the derived structured
                # sidecar rather than the immutable raw Runtime archive. Accept
                # this ONLY when its exact SHA-256 matches the ledger and its
                # recorded raw digest matches the verified original archive.
                sidecar=path.with_name(path.stem+".structured.json")
                if not sidecar.is_file():
                    raise RuntimeError(
                        f"Selected original PUB archive hash mismatch; missing sidecar: "
                        f"{row['treatment_id']} PUB {row['logical_pub_index']}")
                sidecar_bytes=sidecar.read_bytes()
                sidecar_digest=hashlib.sha256(sidecar_bytes).hexdigest()
                if sidecar_digest!=row["source_sha256"]:
                    raise RuntimeError(
                        f"Selected original PUB provenance mismatch: {row['treatment_id']} "
                        f"PUB {row['logical_pub_index']}; raw={digest[:12]}, "
                        f"sidecar={sidecar_digest[:12]}, ledger={row['source_sha256'][:12]}")
                derived=json.loads(sidecar_bytes.decode("utf-8-sig"))
                if (not isinstance(derived,dict) or
                    derived.get("archive_format")!="mxene_structured_pubs_v2" or
                    derived.get("source_raw_sha256")!=digest or
                    derived.get("job_id")!=row["job_id"] or
                    derived.get("treatment_id")!=row["treatment_id"] or
                    not isinstance(derived.get("records"),list) or
                    len(derived["records"])!=manifest.chunk_n):
                    raise RuntimeError("Selected structured sidecar fails immutable raw-source provenance")
                pub_index=(int(row["logical_pub_index"])-1)%manifest.chunk_n
                record=derived["records"][pub_index]
                if (int(record.get("pub_index",-1))!=pub_index+1 or
                    record.get("expectation_validation")!="PASS" or
                    record.get("uncertainty_status")!="PRESENT" or
                    record.get("expectation_values")!=row["expectation_values"] or
                    record.get("standard_deviations")!=row["standard_deviations"]):
                    raise RuntimeError("Selected sidecar PUB values differ from validated ledger")
        else:
            replacement=replacements.get(row.get("replacement_id"))
            if not replacement or replacement["state"]!="VALIDATED": raise RuntimeError("Selected replacement is not validated")
            path=Path(replacement["raw_archive_path"] or "")
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=replacement["raw_archive_sha256"]:
                raise RuntimeError("Selected replacement immutable archive hash mismatch")
    import io
    buffer=io.StringIO(newline="")
    fields=["target_id","experiment_id","n","logical_pub_index","treatment_id","source","logical_block_id",
        "batch_id","job_id","account_label","backend","source_sha256","input_hashes",
        *(f"Z{i}" for i in range(7)),*(f"std_Z{i}" for i in range(7))]
    writer=csv.DictWriter(buffer,fieldnames=fields);writer.writeheader()
    for row in selected:
        ev=row["expectation_values"];std=row["standard_deviations"]
        writer.writerow({"target_id":manifest.target_id,"experiment_id":manifest.experiment_id,"n":manifest.target_n,
            "logical_pub_index":row["logical_pub_index"],"treatment_id":row["treatment_id"],"source":row["source"],
            "logical_block_id":row["logical_block_id"],"batch_id":row["batch_id"],"job_id":row["job_id"],
            "account_label":row["account_label"],"backend":row["backend"],"source_sha256":row["source_sha256"],
            "input_hashes":json.dumps(row["input_hashes"],sort_keys=True),
            **{f"Z{i}":ev[i] for i in range(7)},
            **{f"std_Z{i}":std[i] if std is not None else "" for i in range(7)}})
    data=buffer.getvalue().encode("utf-8-sig");data_hash=hashlib.sha256(data).hexdigest()
    root=Path(root).resolve()
    directory=root/"02_Hardware"/"01_vqe"/"06_DD_TREX_ZNE_mitigation"/"output"/"03_IBM_execution_records"/"ai_assistant"/"repaired_datasets"/manifest.target_id
    directory.mkdir(parents=True,exist_ok=True)
    version=1
    while True:
        destination=directory/f"v{version:03d}"
        if not destination.exists(): break
        manifest_path=destination/"dataset_manifest.json";csv_path=destination/"selected_pub_values.csv"
        if manifest_path.is_file() and csv_path.is_file():
            prior=json.loads(manifest_path.read_text(encoding="utf-8"))
            if prior.get("selected_pub_values_sha256")==data_hash and hashlib.sha256(csv_path.read_bytes()).hexdigest()==data_hash:
                return {key:value for key,value in status.items() if key!="rows"}|{"dataset_path":str(destination),"dataset_version":version,"already_assembled":True}
        version+=1
    stage=directory/f".stage-{version:03d}-{data_hash[:8]}"
    stage.mkdir()
    try:
        csv_path=stage/"selected_pub_values.csv"
        with csv_path.open("xb") as handle:handle.write(data);handle.flush()
        dataset_manifest={"schema_version":1,"dataset_status":status["status"],"target_id":manifest.target_id,
            "experiment_id":manifest.experiment_id,"n":manifest.target_n,"chunk_n":manifest.chunk_n,
            "source_run":"run_02","backend":manifest.backend,"shots_per_pub":manifest.shots_per_pub,
            "input_hashes":manifest.input_hashes,"physical_qubits":list(manifest.physical_qubits),
            "validated_pubs_by_treatment":status["counts_by_treatment"],"selected_pub_count":len(selected),
            "replacement_selected_count":sum(row["source"] in ("REPLACEMENT","TREATMENT_REPLACEMENT") for row in selected),
            "original_batch_execution_status_preserved":True,"selected_pub_values_sha256":data_hash,
            "created_utc":datetime.now(timezone.utc).isoformat()}
        with (stage/"dataset_manifest.json").open("x",encoding="utf-8") as handle:
            json.dump(dataset_manifest,handle,ensure_ascii=False,indent=2);handle.flush()
        stage.rename(destination)
    except Exception:
        # This exact stage directory was created by this call. Never clean an
        # older stage path or a destination/version that already existed.
        import shutil
        if stage.is_dir():shutil.rmtree(stage)
        raise
    return {key:value for key,value in status.items() if key!="rows"}|{"dataset_path":str(destination),
        "dataset_version":version,"selected_pub_count":len(selected),
        "selected_pub_values_sha256":data_hash,"already_assembled":False}


class BatchClosureUnverified(RuntimeError):
    """Provider evidence and precise blockers, suitable for CLI diagnostics."""
    def __init__(self,evidence):
        self.evidence=evidence
        super().__init__('Original IBM Batch closure unverified: '+json.dumps(evidence,sort_keys=True,default=str))


def inspect_runtime_batch_closure(service,batch_id,*,expected_instance,batch_details=None,
                                  batch_status=None,prior_observation=None,now=None):
    """Read supported Batch APIs; distinguish execution state from submission admission.

    Batch.details inherits Session.details: accepting_jobs is copied from the
    GET /sessions/{id} response with dict.get, without inference or a default.
    Batch.status is derived from another details read, not independent evidence
    that a conflicting native acceptance flag is stale. Preserve that conflict.
    Neither a timeout calculation nor DONE jobs proves submission closure.
    Missing closure time uses a durable observation plus the same 120s lag gate.
    """
    from datetime import datetime,timezone,timedelta
    from batch_scheduler import _reconciliation_utc
    now=now or datetime.now(timezone.utc)
    evidence={'batch_id':batch_id,'provider_details':batch_details,'provider_status':batch_status,
        'queried_api':'Batch.from_id / Batch.details / Batch.status',
        'observed_utc':now.isoformat(),'missing_evidence':[],'conflicts':[],
        'submission_closed':False,'settled':False}
    missing=evidence['missing_evidence'];conflicts=evidence['conflicts']
    supplied_details=batch_details is not None
    handle_id=None;handle_backend=None
    if batch_details is None:
        stage='Batch.from_id'
        try:
            from low_memory_runtime import remote_runtime
            remote_runtime()
            from qiskit_ibm_runtime import Batch
            handle=Batch.from_id(batch_id,service=service)
            handle_id=handle.session_id;handle_backend=handle.backend()
            stage='Batch.details';batch_details=handle.details()
            evidence['provider_details']=batch_details
            # status() performs a second details read in Qiskit. A transient
            # failure there must not discard conclusive first-response evidence.
            try:
                batch_status=handle.status()
                evidence['provider_status']=batch_status
            except Exception as exc:
                evidence['provider_read_warnings']=[{'api':'Batch.status','error_type':type(exc).__name__}]
        except Exception as exc:
            evidence['provider_read_error']={'api':stage,'error_type':type(exc).__name__}
            missing.append('Successful '+stage+' response')
            raise BatchClosureUnverified(evidence) from exc
    details=batch_details if isinstance(batch_details,dict) else {}
    if not isinstance(batch_details,dict):missing.append('Batch.details dictionary')
    evidence.update(handle_session_id=handle_id,handle_backend=handle_backend)
    ids=[v for v in (details.get('id'),handle_id) if v is not None]
    backends=[v for v in (details.get('backend_name'),handle_backend) if v is not None]
    if not ids:missing.append('Batch identity from details.id or Batch.session_id')
    if any(v!=batch_id for v in ids):conflicts.append('Provider Batch identity differs from original Batch')
    if not backends:missing.append('Backend from details.backend_name or Batch.backend()')
    if any(v!='ibm_fez' for v in backends):conflicts.append('Provider backend differs from ibm_fez')
    if details.get('mode') not in (None,'batch'):conflicts.append('Provider mode is not batch')
    if service.active_instance()!=expected_instance:conflicts.append('Active instance changed during Batch lookup')
    state=str(details.get('state') or '').strip().lower()
    status=str(batch_status or '').strip().lower()
    accepting=details.get('accepting_jobs')
    evidence.update(accepting_jobs=accepting,accepting_jobs_evidence={
        'source':('supplied_batch_details' if supplied_details else 'Batch.details -> Session.details -> GET /sessions/{id}'),
        'field_present':'accepting_jobs' in details,
        'inferred':False,'default_applied':False,
        'freshness':('unverified_supplied_snapshot' if supplied_details else 'read_during_this_inspection'),
        'stale_proven':False})
    if accepting is not None and type(accepting) is not bool:
        conflicts.append('accepting_jobs must be a Boolean, not a string or number')
    terminal=state in ('closed','expired','completed') or status in ('closed','expired','completed')
    if state in ('closed','expired','completed') and status=='pending':
        conflicts.append('Terminal details.state conflicts with Pending Batch.status')
    status_accepting=status=='in progress, accepting new jobs'
    status_sealed=status=='in progress, not accepting new jobs'
    # Terminal closed has documented no-new-jobs semantics even when the flag is omitted.
    sealed=(state=='closed' or status=='closed' or accepting is False or status_sealed)
    if accepting is True or status_accepting or state=='pending_inactive':
        if sealed:
            conflicts.append('Provider closure and accepting-new-jobs fields disagree')
            evidence['resolution_required']='Fresh consistent provider closure/nonacceptance evidence; Closed status alone does not prove accepting_jobs stale'
        sealed=False
    if (prior_observation and prior_observation.get('batch_id')==batch_id and
            prior_observation.get('instance')==expected_instance and
            any(message in prior_observation.get('conflicts',[]) for message in (
                'Provider closure and accepting-new-jobs fields disagree',
                'Prior native acceptance contradiction requires fresh explicit nonacceptance')) and
            accepting is not False):
        if 'Provider closure and accepting-new-jobs fields disagree' not in conflicts:
            conflicts.append('Prior native acceptance contradiction requires fresh explicit nonacceptance')
        sealed=False
    if not sealed:missing.append('Provider state/status Closed or explicit non-acceptance of new jobs')
    evidence.update(normalized_state=state,execution_terminal=terminal,
        classification=('CONTRADICTORY' if conflicts else 'TERMINAL' if terminal else 'SEALED_TO_NEW_SUBMISSIONS' if sealed else 'ACCEPTING_OR_UNVERIFIED'))
    evidence['submission_closed']=bool(sealed and not missing and not conflicts)
    times=[]
    if details.get('closed_at') is not None:times.append(('closed_at',details['closed_at']))
    transitions=details.get('timestamps') or []
    if isinstance(transitions,list):
        for item in transitions:
            if isinstance(item,dict) and str(item.get('status','')).lower() in ('closed','expired','completed'):
                times.append(('timestamps.'+str(item['status']),item.get('timestamp')))
    parsed=[]
    for source,value in times:
        try:
            stamp=_reconciliation_utc(value)
            if stamp>now:raise ValueError('Future timestamp')
            parsed.append((source,stamp))
        except (ValueError,TypeError,RuntimeError):
            evidence.setdefault('unusable_timestamps',[]).append({'field':source,'value':value})
    # Use the latest terminal transition conservatively. A job-completion time is never a closure time.
    if parsed and terminal:
        source,closed=max(parsed,key=lambda x:x[1]);history_end=closed
        evidence.update(closed_at=closed.isoformat(),closure_time_source=source)
    else:
        closed=None;history_end=now
        if (prior_observation and prior_observation.get('batch_id')==batch_id and
            prior_observation.get('submission_closed') is True and
            prior_observation.get('instance')==expected_instance):
            try:
                first=_reconciliation_utc(prior_observation['observed_utc'])
                if first<=now:history_end=first
            except (KeyError,TypeError,ValueError,RuntimeError):pass
        evidence.update(closed_at=None,closure_time_source='first_verified_nonacceptance_observation')
    evidence['history_end_utc']=history_end.isoformat()
    evidence['settled']=bool(evidence['submission_closed'] and (now-history_end).total_seconds()>=120)
    if evidence['submission_closed'] and not evidence['settled']:
        missing.append('120-second provider-listing consistency interval after verified closure or first nonacceptance observation')
        evidence['retry_after_utc']=(history_end+timedelta(seconds=120)).isoformat()
    evidence['instance']=expected_instance
    # A Closed status string is derived evidence and cannot resolve native True.
    # One bounded, read-only recheck can observe a provider-side correction. Never
    # call close/cancel or treat an omitted flag as resolution of this conflict.
    if (not supplied_details and state=='closed' and accepting is True and
            conflicts==['Provider closure and accepting-new-jobs fields disagree']):
        try:
            refreshed=handle.details()
            evidence['closure_recheck_details']=refreshed
            if (isinstance(refreshed,dict) and refreshed.get('id')==batch_id and
                    refreshed.get('backend_name')=='ibm_fez' and
                    refreshed.get('state')=='closed' and refreshed.get('accepting_jobs') is False):
                resolved=inspect_runtime_batch_closure(service,batch_id,expected_instance=expected_instance,
                    batch_details=refreshed,now=now)
                if resolved['submission_closed']:
                    resolved['accepting_jobs_evidence'].update(
                        source='Batch.details -> Session.details -> GET /sessions/{id}',
                        freshness='read_during_this_inspection')
                    resolved['closure_resolution']='fresh_provider_closed_and_explicit_nonacceptance'
                    resolved['previous_contradictory_observation']=evidence
                    return resolved
        except Exception as exc:
            evidence['closure_recheck_error']={'api':'Batch.details','error_type':type(exc).__name__}
    return evidence


def inspect_original_m7_intent(root,registry,block):
    """Independent local audit; never release a reservation or infer provider absence."""
    from vqe_adapter import record_directory
    from batch_scheduler import _check_reconciliation_records
    rows=registry.rows(block)
    m7=[r for r in rows if r['treatment_id']=='M7_ALL']
    blockers=[]
    report={'provider_absence_verified':False,'blockers':blockers,
        'validated_original_pubs':sum(r['validated_pub_count'] for r in rows if r['attempt']==1),
        'unknown_jobs':[r['id'] for r in registry.rows() if r['submission_state'] in ('UNKNOWN','SUBMITTING')],
        'unknown_replacements':[r['replacement_id'] for r in registry.replacement_runtime_candidates()
            if r['state'] in ('UNKNOWN','SUBMITTING')]}
    if report['unknown_jobs'] or report['unknown_replacements']:blockers.append('Unresolved UNKNOWN/SUBMITTING records')
    if len(m7)!=1 or m7[0]['attempt']!=1:
        blockers.append('Existing or non-original M7 recovery intent requires reconciliation')
        return report
    row=m7[0];events=registry.events(row['id'])
    report.update(original_job_row_id=row['id'],original_batch_id=row['batch_id'],
        submission_state=row['submission_state'],job_id=row['job_id'],
        quota_reserved_seconds=row['quota_reserved_seconds'],
        reservation_events=[{'event_type':e['event_type'],'created_at':e['created_at']} for e in events])
    if row['job_id'] or row['metadata'].get('submit_started_utc') or row['submission_state'] not in ('RESERVED','CONFIRMED_NOT_SUBMITTED'):
        blockers.append('Original M7 has submission evidence')
    for event in events:
        kind=event['event_type'];data=json.loads(event['details_json'])
        if (any(marker in kind for marker in ('UNKNOWN','SUBMISSION_INTENT','JOB_ID_PERSISTED','JOB_SUBMITTED')) or
                data.get('job_id') or data.get('submit_started_utc')):
            blockers.append('M7 event history contains submission evidence: '+kind)
    try:
        _check_reconciliation_records(registry,block,root)
        folder=record_directory(root,block)/'M7_ALL'
        state=json.loads((folder/'submission_state.json').read_text(encoding='utf-8'))
        if ((folder/'job_submitted.json').exists() or state.get('job_id') or state.get('submit_started_utc') or
                state.get('status') not in ('RESERVED','CONFIRMED_NOT_SUBMITTED')):
            blockers.append('Local M7 submission record is unresolved')
        if any(item.get('job_id') or item.get('submit_started_utc') or item.get('status') in ('SUBMITTING','UNKNOWN','SUBMITTED')
               for item in state.get('reconciliation_history',[]) if isinstance(item,dict)):
            blockers.append('Local M7 historical submission intent is unresolved')
    except (OSError,ValueError,RuntimeError) as exc:
        blockers.append('Local record verification failed: '+type(exc).__name__)
    report['local_no_start_evidence']=not blockers
    return report


def inspect_original_m7_inventory(service,instance,batch,others,created_after,observed_until):
    """Read all pages even when closure conflicts; observation is not authorization.

    Do not truncate at a disputed closed_at timestamp. Inventory absence only
    describes these reads; it does not establish that future admission is sealed.
    """
    known={r['job_id'] for r in others}
    report={'provider_history_complete':False,'recovery_authorized':False,'blockers':[],
        'history_end_utc':observed_until.isoformat(),'batch_inventories':[]}
    stage='Batch inventory'
    try:
        for _ in range(2):
            jobs=iter_provider_jobs(service,instance=instance,session_id=batch)
            inventory=[]
            for job in jobs:
                session=getattr(job,'session_id',None)
                session=session() if callable(session) else session
                inventory.append({'job_id':job.job_id(),'batch_id':str(session),
                    'status':str(job.status()).upper().split('.')[-1]})
            report['batch_inventories'].append(inventory)
            if len(inventory)!=7 or {j['job_id'] for j in inventory}!=known:
                report['blockers'].append('Batch inventory differs from the seven registered M0-M6 jobs')
            if any(j['batch_id']!=batch or j['status']!='DONE' for j in inventory):
                report['blockers'].append('Original job Batch identity or DONE status unverified')
        stage='Provider history'
        history=iter_provider_jobs(service,instance=instance,backend_name='ibm_fez',
            created_after=created_after,created_before=observed_until)
        report['history_job_ids']=[j.job_id() for j in history]
        if len(report['history_job_ids'])!=7 or set(report['history_job_ids'])!=known:
            report['blockers'].append('Provider history has additional or missing jobs; reconcile identities')
        if service.active_instance()!=instance:report['blockers'].append('Provider instance changed')
        report['provider_history_complete']=True
        report['matches_only_original_m0_m6']=not report['blockers']
    except Exception as exc:
        report['read_error']={'api':stage,'error_type':type(exc).__name__}
        report['blockers'].append('Complete '+stage+' unavailable')
    return report


def _verify_m7_absence_independent_of_admission(root,registry,block,service,instance,m7,others,closure):
    """Prove this never-started intent absent, not that the old Batch is sealed.

    Called only by the explicit incident-scoped M7 flow under WorkerLock. A
    missing/extra job, unknown outcome, failed read, or changed local record
    fails closed. The old Batch is never reused or altered.
    """
    from datetime import datetime,timezone
    from batch_scheduler import _reconciliation_utc,verify_reconciliation_job
    if (block!=M7_RECOVERY_SCOPE['logical_block_id'] or m7['batch_id']!=M7_RECOVERY_SCOPE['original_batch_id'] or
            m7['account_label']!='labK' or closure.get('normalized_state')!='closed' or
            not ((closure.get('accepting_jobs') is True and
                  closure.get('conflicts')==['Provider closure and accepting-new-jobs fields disagree']) or
                 (closure.get('settled') is True and not closure.get('conflicts'))) or
            not closure.get('closed_at')):
        raise RuntimeError('Independent absence requires the exact original closed/accepting incident')
    now=datetime.now(timezone.utc)
    created=_reconciliation_utc(m7['created_at']).replace(microsecond=0)
    if (now-max(created,_reconciliation_utc(closure['closed_at']))).total_seconds()<120:
        raise RuntimeError('Original intent/terminal observation has not passed the 120-second consistency interval')
    before=inspect_original_m7_intent(root,registry,block)
    if not before.get('local_no_start_evidence'):raise RuntimeError('M7 local intent is unresolved')
    if before.get('validated_original_pubs')!=34:
        raise RuntimeError('Independent first submission is scoped to the 34-PUB original incident')
    known={r['job_id']:r for r in others}
    snapshots=[];proofs={}
    for _ in range(2):
        batch_jobs=iter_provider_jobs(service,instance=instance,session_id=m7['batch_id'])
        # No session, backend, status or upper-time filter: also catch submissions
        # outside the original Batch, including jobs after its disputed closed_at.
        history=iter_provider_jobs(service,instance=instance,created_after=created)
        for label,jobs in (('Batch inventory',batch_jobs),('cross-Batch instance history',history)):
            ids=[]
            for job in jobs:
                identity=job.job_id()
                ids.append(identity)
                if identity not in known:
                    raise RuntimeError(label+' contains additional jobs; M7 absence unverified')
                if label=='cross-Batch instance history':
                    if str(job.status()).upper().split('.')[-1]!='DONE':
                        raise RuntimeError('Original M0-M6 jobs must all be provider DONE')
                    proofs[identity]=verify_reconciliation_job(known[identity],job,service,root=root)
            if len(ids)!=7 or set(ids)!=set(known):
                raise RuntimeError(label+' is incomplete or contains additional jobs; M7 absence unverified')
            snapshots.append({'query':label,'job_ids':sorted(ids)})
        if service.active_instance()!=instance:raise RuntimeError('Original provider instance changed')
    after=inspect_original_m7_intent(root,registry,block)
    if before!=after or not after.get('local_no_start_evidence'):
        raise RuntimeError('Local M7 evidence changed during provider audit')
    return {'verified':True,'verification_basis':'never_started_intent_and_complete_cross_batch_history',
        'original_batch_id':m7['batch_id'],'original_account':m7['account_label'],
        'closed_at':closure['closed_at'],'closure_evidence':closure,
        'original_batch_closure_verified':closure['submission_closed'],'new_batch_required':True,
        'verified_job_ids':sorted(known),'provider_identity_proofs':proofs,
        'provider_inventory_snapshots':snapshots,'provider_history_complete':True,
        'history_start_utc':created.isoformat(),'history_scope':'all jobs in original instance since reservation; no upper bound',
        'm7_submit_started_absent':True,'local_intent_evidence':after,
        'checked_utc':datetime.now(timezone.utc).isoformat()}


def verify_missing_m7_absence(root, registry, block, service, *, batch_details=None, allow_uncollected_m3=False,
                             batch_status=None, allow_independent_absence=False):
    """Read-only negative evidence: closed Batch, exhaustive history, durable no-start intent."""
    from batch_scheduler import _reconciliation_utc, _check_reconciliation_records
    from vqe_adapter import record_directory
    from datetime import datetime, timezone, timedelta
    local=inspect_original_m7_intent(root,registry,block)
    if local['blockers']:raise RuntimeError('M7 local absence audit blocked: '+'; '.join(local['blockers']))
    all_rows=registry.rows(block)
    if len([r for r in all_rows if r['treatment_id']=='M7_ALL'])!=1:
        raise RuntimeError('Existing M7 recovery intent requires reconciliation; no new submission')
    rows=[r for r in all_rows if r['attempt']==1]
    if len(rows)!=8:raise RuntimeError("Absence audit requires exactly the original eight intents")
    from batch_progression import TREATMENTS
    if {r['treatment_id'] for r in rows}!=set(TREATMENTS):
        raise RuntimeError('Original intents must map uniquely to M0-M7')
    m7=next(r for r in rows if r["treatment_id"]=="M7_ALL")
    if m7["job_id"] or m7["submission_state"] not in ("RESERVED","CONFIRMED_NOT_SUBMITTED") or m7["metadata"].get("submit_started_utc"):
        raise RuntimeError("M7 has submission evidence; reconcile identity, never submit again")
    others=[r for r in rows if r["id"]!=m7["id"]]
    if any(r['submission_state'] in ('UNKNOWN','SUBMITTING','RESERVED') for r in others):
        raise RuntimeError('Original submission is unresolved; reconcile UNKNOWN intent before M7 absence')
    if any(not r["job_id"] or ((r["submission_state"]!="COMPLETED" or r["validated_pub_count"]!=5)
            and not (allow_uncollected_m3 and r["treatment_id"]=="M3_ZNE")) for r in others):
        raise RuntimeError("All M0-M6 must already be completed with five validated PUBs")
    batch=m7["batch_id"];instance=others[0]["metadata"].get("instance")
    if not batch or any(r['batch_id']!=batch for r in others) or len({r['job_id'] for r in others})!=7:
        raise RuntimeError('Original M0-M6 require seven distinct job IDs in the original Batch')
    if not instance or service.active_instance()!=instance:raise RuntimeError("Original account instance mismatch")
    _check_reconciliation_records(registry,block,root)
    folder=record_directory(root,block)/"M7_ALL"
    state=json.loads((folder/'submission_state.json').read_text(encoding='utf-8'))
    if (folder/'job_submitted.json').exists() or state.get('job_id') or state.get('submit_started_utc') or state.get('status') not in ('RESERVED','CONFIRMED_NOT_SUBMITTED'):
        raise RuntimeError("M7 local submission record is unresolved")
    if any(item.get('job_id') or item.get('submit_started_utc') or item.get('status') in ('SUBMITTING','UNKNOWN','SUBMITTED')
           for item in state.get('reconciliation_history',[]) if isinstance(item,dict)):
        raise RuntimeError('M7 historical submission intent is unresolved; no absence inference')
    prior=None
    for event in registry.events(m7['id']):
        if event['event_type']=='M7_BATCH_CLOSURE_OBSERVED':
            candidate=json.loads(event['details_json'])
            if candidate.get('submission_closed') is not True:prior=candidate
            elif prior is None or prior.get('submission_closed') is not True:prior=candidate
    closure=inspect_runtime_batch_closure(service,batch,expected_instance=instance,
        batch_details=batch_details,batch_status=batch_status,prior_observation=prior)
    closure['local_intent_evidence']=local
    if allow_independent_absence and closure['normalized_state']=='closed':
        try:
            return _verify_m7_absence_independent_of_admission(root,registry,block,service,instance,m7,others,closure)
        except Exception as exc:
            closure['independent_absence_error']={'error_type':type(exc).__name__}
            # Keep provider exception messages (potentially containing credentials)
            # out of diagnostics. Local validation messages contain no secrets.
            if type(exc) is RuntimeError:closure['independent_absence_error']['reason']=str(exc)
            closure['missing_evidence'].append('Conclusive independent M7 absence proof')
            raise BatchClosureUnverified(closure) from exc
    if closure['classification']=='CONTRADICTORY' and closure['normalized_state']=='closed':
        closure['independent_inventory_evidence']=inspect_original_m7_inventory(
            service,instance,batch,others,_reconciliation_utc(m7['created_at']).replace(microsecond=0),
            datetime.now(timezone.utc))
        closure['missing_evidence'].append(
            'Authoritative resolution of native closed/accepting contradiction; inventory snapshots alone do not prove admission sealed')
    if prior is None or prior.get('submission_closed') is not True or not closure['submission_closed']:
        from job_registry import utc_now
        with registry._transaction() as db:
            db.execute('INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)',
                (m7['id'],'M7_BATCH_CLOSURE_OBSERVED',json.dumps(closure,sort_keys=True,default=str),utc_now()))
    if not closure['settled'] or closure['conflicts']:
        raise BatchClosureUnverified(closure)
    closed=_reconciliation_utc(closure['history_end_utc'])
    known={r['job_id'] for r in others}
    for _ in range(2):
        jobs={j.job_id() for j in iter_provider_jobs(service,instance=instance,backend_name='ibm_fez',session_id=batch)}
        if len(jobs)!=7 or jobs!=known:
            raise RuntimeError("Original Batch contains additional or missing jobs; M7 identity unresolved")
    # Exact registered M0-M6 identities were already reconciled. Compare provider
    # inventories against those immutable IDs without re-fetching their inputs/results.
    history={j.job_id() for j in iter_provider_jobs(service,instance=instance,backend_name='ibm_fez',
        created_after=_reconciliation_utc(m7['created_at']).replace(microsecond=0),created_before=closed+timedelta(seconds=1))}
    if len(history)!=7 or history!=known:
        raise RuntimeError("Provider history has additional/missing jobs in original submission window; inspect identities")
    if service.active_instance()!=instance:raise RuntimeError('Original instance changed during absence audit')
    return {'original_batch_id':batch,'original_account':m7['account_label'],'closed_at':closure['closed_at'],'closure_evidence':closure,
        'verified_job_ids':sorted(known),'m7_submit_started_absent':True,'provider_history_complete':True,
        'checked_utc':datetime.now(timezone.utc).isoformat()}


def missing_m7_runtime_estimate(root, registry, hashes, treatment="M7_ALL"):
    """Use verified exact-five-PUB treatment jobs, never scale a one-PUB estimate."""
    from runtime_estimator import UsageSample, estimate_runtime, APPROVED_ORIGINAL_SAFETY_FACTOR
    from quota import qpu_seconds_from_metrics
    from types import SimpleNamespace
    samples=[]
    for row in registry.rows():
        if row['treatment_id']!=treatment or row['submission_state']!='COMPLETED' or row['validated_pub_count']!=5 or row['input_hashes']!=hashes:continue
        try:
            path,digest,_,_,_=_verified_raw_source(root,row,registry,expected_pubs=5,expected_shots=4096,expected_hashes=hashes)
            _verify_archived_treatment_options(root,row,4096)
            for file in path.parent.glob('provider_metadata_*.json'):
                if hashlib.sha256(file.read_bytes()).hexdigest()!=file.stem.removeprefix('provider_metadata_'):continue
                data=json.loads(file.read_text(encoding='utf-8'))
                seconds=qpu_seconds_from_metrics(data.get('metrics'))
                if data.get('job_id')==row['job_id'] and data.get('raw_result_sha256')==digest and seconds is not None and seconds>0:
                    samples.append(UsageSample(treatment,5,seconds,row['job_id'],'verified_provider_metrics',str(file)));break
        except (ValueError,RuntimeError,OSError,KeyError):continue
    unique={sample.job_id:sample for sample in samples}
    estimate=estimate_runtime(list(unique.values()),treatments=(treatment,),target_n=5,
        safety_factor=APPROVED_ORIGINAL_SAFETY_FACTOR,minimum_exact_n_samples=2)
    if not estimate.defensible:raise RuntimeError(f'Two verified exact-five-PUB {treatment} runtime samples required; '+str(estimate.blockers))
    return SimpleNamespace(**{**estimate.__dict__,'scope':'missing_treatment_recovery','pub_count':5,'treatment_id':treatment})


M7_RECOVERY_SCOPE = {
    "campaign_id":"vqe-independent-n-01",
    "logical_block_id":"vqe-independent-n-01-n25-block-003",
    "original_batch_id":"d9e6cd69-775b-4597-9267-7e27a864879d",
    "original_account":"labK", "treatment_id":"M7_ALL", "pub_count":5,
}


def recover_missing_m7(root, campaign_id, block, *, authorize_live=False, confirm_m7_only=False,
                       wait_seconds=0, service_factory=None, batch_factory=None, estimator_factory=None,
                       authorize_m7_first_submission=False):
    """One operator-authorized M7 action. Never enters the campaign advancement loop."""
    import time
    from job_registry import JobRegistry
    from worker_runtime import WorkerLock
    from independent_n_campaign import campaign_root, _target_from_level, _campaign_usage
    from final_configuration import load_final_config, campaign_authorization_from_final_config
    from account_config import load_saved_account_profiles, make_saved_profile_service
    from quota import preflight_and_select_account, AccountUnavailable, provider_quota_fields, provider_reservations, available_quota_seconds
    from runtime_estimator import workload_quota_decision
    from vqe_adapter import verify_frozen_inputs, treatment_options, record_directory, collect_block, validate_runtime_result
    from batch_scheduler import (_stamp,_runtime_types,_update_owned,_start_reconciled_retry,
        _write_reconciled_record,verify_reconciliation_job,recover_interrupted_block)
    from batch_progression import evaluate_batch_progression
    if not authorize_live or not confirm_m7_only:raise RuntimeError('Requires --live and --confirm-m7-only; no submission performed')
    if not isinstance(wait_seconds,int) or not 0<=wait_seconds<=86400:raise ValueError('wait_seconds must be 0..86400')
    cfg=load_final_config(root);auth=campaign_authorization_from_final_config(cfg)
    if not auth['live_auto_submit'] or 'submit' not in auth['allowed_actions']:
        raise RuntimeError('Existing configuration does not authorize recovery submission')
    make_service=service_factory or (lambda label:make_saved_profile_service(load_saved_account_profiles((label,))[label]))
    with WorkerLock(campaign_root(root,campaign_id)/'worker.lock'):
        registry=JobRegistry(JobRegistry.default_database(root))
        status=registry.independent_campaign_status(campaign_id)
        rows=registry.rows(block);original=next(r for r in rows if r['treatment_id']=='M7_ALL' and r['attempt']==1)
        if original['account_label']!=M7_RECOVERY_SCOPE['original_account'] or original['batch_id']!=M7_RECOVERY_SCOPE['original_batch_id'] or block!=M7_RECOVERY_SCOPE['logical_block_id'] or campaign_id!=M7_RECOVERY_SCOPE['campaign_id']:
            raise RuntimeError('M7-only recovery is scoped to the audited n25 Batch 003 incident')
        level=next(l for l in status['levels'] if l['target_id']==original['target_id'])
        target=_target_from_level(root,level,registry=registry)
        if any(r['input_hashes']!=target.input_hashes for r in rows):raise RuntimeError('Registry frozen inputs differ from campaign')
        latest=max((r for r in rows if r['treatment_id']=='M7_ALL'),key=lambda r:r['attempt'])
        submitted=False
        if latest['id']==original['id']:
            # M3's invalid scientific PUB is a separate repair, not evidence of
            # an uncertain M7 submission. Keep all identity/UNKNOWN gates while
            # allowing M7 to run first without reading or recovering M3 results.
            if (any(r['submission_state'] in ('UNKNOWN','SUBMITTING') for r in rows) or
                    any(r['logical_block_id']==block and r['state'] in ('UNKNOWN','SUBMITTING','RESERVED','PLANNED')
                        for r in registry.replacement_jobs(target.target_id))):
                raise RuntimeError('Unresolved submission intent in original block; reconcile before M7-only submission')
            evidence=verify_missing_m7_absence(root,registry,block,make_service('labK'),allow_uncollected_m3=True,
                allow_independent_absence=authorize_m7_first_submission)
            if not authorize_m7_first_submission and original['submission_state']!='CONFIRMED_NOT_SUBMITTED':
                registry.transition(original['id'],'CONFIRMED_NOT_SUBMITTED',release_reservation=True,
                    metadata={'m7_absence_evidence':evidence},event_type='CONFIRMED_NO_SUBMISSION',details=evidence)
            estimate=missing_m7_runtime_estimate(root,registry,target.input_hashes)
            account,service,preflight,instance,backend=_select_recovery_account(
                root,target,registry,status,auth,estimate,make_service,'missing_treatment')
            qc,obs,theta,physical,_,_=verify_frozen_inputs(root)
            if len(theta)!=21 or len(obs)!=7 or tuple(physical)!=tuple(range(140,147)):raise RuntimeError('Frozen shape mismatch')
            pub=_coerce_replacement_estimator_pub(qc,obs,theta);options=treatment_options('M7_ALL',4096,root)
            if batch_factory is None or estimator_factory is None:
                real_batch,real_estimator=_runtime_types();batch_factory=batch_factory or real_batch;estimator_factory=estimator_factory or real_estimator
            if authorize_m7_first_submission:
                # Repeat after preparation and quota selection, immediately before
                # committing intent and entering the new Runtime Batch.
                evidence=verify_missing_m7_absence(root,registry,block,make_service('labK'),
                    allow_uncollected_m3=True,allow_independent_absence=True)
            rid=registry.reserve_missing_m7(original['id'],account_label=account,reservation_seconds=estimate.stress_block_seconds,
                metadata={'instance':instance,'source_run':'run_02','runtime_options_requested':repr(options),
                          'operator_authorized_m7_first_submission':authorize_m7_first_submission,
                          'runtime_upper_bound_seconds':estimate.stress_block_seconds,'preflight_checked_at':preflight['checked_utc']},
                first_submission_evidence=evidence if authorize_m7_first_submission else None)
            archive_block=block+'-m7-first-submission' if authorize_m7_first_submission else block
            folder=record_directory(root,archive_block)/'M7_ALL';state_path=folder/'submission_state.json'
            if not authorize_m7_first_submission:
                old=json.loads(state_path.read_text(encoding='utf-8'))
                _update_owned(state_path,old['attempt_id'],{**old,'status':'CONFIRMED_NOT_SUBMITTED'})
            import uuid
            payload={'attempt_id':uuid.uuid4().hex,'logical_block_id':block,'treatment_id':'M7_ALL','status':'RESERVED',
                'account_label':account,'instance':instance,'backend':'ibm_fez','input_hashes':target.input_hashes,
                'source_account_label':'labK','source_batch_id':original['batch_id'],'n':5,'requested_shots_per_pub':4096,
                'created_utc':_stamp(),'updated_utc':_stamp()}
            if authorize_m7_first_submission:
                folder.mkdir(parents=True,exist_ok=True)
                _write_reconciled_record(state_path,payload)
            else:_start_reconciled_retry(state_path,payload)
            started=_stamp();registry.transition(rid,'SUBMITTING',metadata={'submit_started_utc':started},event_type='M7_ONLY_SUBMISSION_INTENT',details={'pubs':5})
            _update_owned(state_path,payload['attempt_id'],{**payload,'status':'SUBMITTING','submit_started_utc':started})
            try:
                with batch_factory(backend=backend) as batch:
                    bid=batch.session_id
                    if callable(bid):bid=bid()
                    if not bid:raise RuntimeError('No recovery Batch ID returned')
                    if str(bid)==original['batch_id']:raise RuntimeError('Recovery requires a NEW IBM Batch')
                    registry.transition(rid,'SUBMITTING',batch_id=str(bid),event_type='M7_RECOVERY_BATCH_CREATED')
                    job=estimator_factory(mode=batch,options=options).run([pub]*5)
                    jid=job.job_id()
                    if not jid:raise RuntimeError('Provider returned no job ID')
                    registry.transition(rid,'SUBMITTED',job_id=jid,event_type='M7_ONLY_JOB_ID_PERSISTED',details={'job_id':jid})
                    submitted=True
            except BaseException:
                row=next(r for r in registry.rows(block) if r['id']==rid)
                if not row['job_id']:registry.transition(rid,'UNKNOWN',event_type='M7_SUBMISSION_OUTCOME_UNKNOWN',details={'automatic_retry':False})
                raise
            latest=next(r for r in registry.rows(block) if r['id']==rid)
        else:
            if latest['submission_state'] in ('RESERVED','SUBMITTING','UNKNOWN') or not latest['job_id']:
                return {'state':'BLOCKED','reason':'Existing M7 recovery intent requires reconciliation; no retry','job_id':latest['job_id']}
            service=make_service(latest['account_label'])
        # Repair only the latest M7 record; do not touch the completed original treatments.
        archive_block=block+'-m7-first-submission' if latest['metadata'].get('m7_first_submission') else block
        folder=record_directory(root,archive_block)/'M7_ALL';record=folder/'job_submitted.json'
        if not record.exists():
            _write_reconciled_record(record,{**latest['metadata'],'job_id':latest['job_id'],'account_label':latest['account_label'],
                'backend':'ibm_fez','batch_id':latest['batch_id'],'input_hashes':target.input_hashes,'logical_block_id':block,'treatment':'M7_ALL'})
        elif json.loads(record.read_text(encoding='utf-8')).get('job_id')!=latest['job_id']:raise RuntimeError('Existing M7 job record conflicts')
        state_path=folder/'submission_state.json'
        state=json.loads(state_path.read_text(encoding='utf-8'))
        if state.get('job_id') not in (None,latest['job_id']):raise RuntimeError('M7 submission state job ID conflict')
        _update_owned(state_path,state['attempt_id'],{**state,'job_id':latest['job_id'],'batch_id':latest['batch_id'],
            'status':latest['submission_state'],'updated_utc':_stamp()})
        job=service.job(latest['job_id']);verify_reconciliation_job(latest,job,service,root=root)
        deadline=time.monotonic()+wait_seconds
        while str(job.status()).upper().split('.')[-1] not in ('DONE','COMPLETED','ERROR','FAILED','CANCELLED') and time.monotonic()<deadline:
            time.sleep(min(30,max(0,deadline-time.monotonic())))
        remote=str(job.status()).upper().split('.')[-1]
        if remote not in ('DONE','COMPLETED'):
            return {'state':remote,'job_id':latest['job_id'],'submission_performed':submitted,'automatic_retry':False}
        result=job.result();captured=validate_runtime_result(result,5,'M7_ALL')
        if any(r['uncertainty_status']!='PRESENT' for r in captured['records']):raise RuntimeError('M7 uncertainty missing; targeted review required')
        class JobRead:
            def result(self):return result
            def __getattr__(self,key):return getattr(job,key)
        class Reads:
            def job(self,jid):
                if jid!=latest['job_id']:raise RuntimeError('Only M7 retrieval is authorized')
                return JobRead()
        collect_block(root,block,shots=4096,n=5,account_label=latest['account_label'],ibm_account=latest['account_label'],
            instance=latest['metadata']['instance'],registry=registry,service_override=Reads(),treatments=('M7_ALL',),
            job_row_id=latest['id'],archive_block_id=archive_block if archive_block!=block else None)
        decision=evaluate_batch_progression(registry,target,block)
        registry.record_independent_target_event(target.target_id,'M7_ONLY_COLLECTION_REVIEW',decision)
        return {'job_id':latest['job_id'],'submission_performed':submitted,'progression':decision,'batch004_submitted':False,
            'note':'Campaign checkpoint retained; normal explicit resume is separate'}


def _select_recovery_account(root, target, registry, status, auth, estimate, make_service, workload_type):
    from independent_n_campaign import _campaign_usage
    from quota import (preflight_and_select_account, AccountUnavailable, provider_quota_fields,
                       provider_reservations, available_quota_seconds)
    from runtime_estimator import workload_quota_decision
    excluded=set()
    while True:
        chosen,service,preflight=preflight_and_select_account(root,target,runtime_estimate=estimate,
            usage=_campaign_usage(registry,status['manifest'],auth),authorization=auth,
            workload_type=workload_type,original_account='labK',excluded=excluded,service_factory=make_service)
        account=chosen['account_label'];instance=service.active_instance()
        try:
            from read_only_preflight import _instance_ref
            if (_instance_ref(instance)!=preflight.get('configured_instance_ref') or
                _instance_ref(instance) not in auth['allowed_instance_refs'].get(account,())):
                raise AccountUnavailable('Selected provider instance is not the freshly authorized instance')
            backend=_runtime_backend_for_service(service,'ibm_fez')
            if service.jobs(limit=1,pending=True,instance=instance):raise AccountUnavailable('Provider pending jobs require reconciliation')
            fields=provider_quota_fields(service.usage())
            if service.active_instance()!=instance:raise AccountUnavailable('Provider instance changed')
            reserves=provider_reservations(registry)[account]
            if reserves['ambiguous']:raise AccountUnavailable('Provider reservation overlap is ambiguous')
            available=available_quota_seconds(provider_remaining_seconds=fields['remaining_seconds'],
                outstanding_reservations_seconds=reserves['reserved_seconds'])
            gate=workload_quota_decision(estimate,remaining_seconds=min(available,chosen['effective_remaining_seconds']),
                workload_type=workload_type,safety_margin_seconds=auth['safety_margin_seconds'],provider_remaining_seconds=fields['remaining_seconds'])
            if not gate['eligible']:raise AccountUnavailable('; '.join(gate['reasons']))
            break
        except (AccountUnavailable,TimeoutError,ConnectionError,OSError,ValueError):
            excluded.add(account)
    return account, service, preflight, instance, backend


def recover_m3_m7(root, campaign_id, block, *, authorize_live=False,
                  authorize_m3_replacement=False, replacement_reason='', wait_seconds=0,
                  service_factory=None, batch_factory=None, estimator_factory=None):
    """Recover only Batch 003 M3/M7; no campaign advancement or automatic resubmit."""
    import time
    from types import SimpleNamespace
    from job_registry import JobRegistry, utc_now
    from worker_runtime import WorkerLock
    from independent_n_campaign import campaign_root, _target_from_level
    from final_configuration import load_final_config, campaign_authorization_from_final_config
    from account_config import load_saved_account_profiles, make_saved_profile_service
    from vqe_adapter import collect_block, verify_frozen_inputs, treatment_options, record_directory
    from batch_scheduler import _runtime_types, verify_reconciliation_job, _write_reconciled_record
    from batch_progression import evaluate_batch_progression
    if campaign_id!=M7_RECOVERY_SCOPE['campaign_id'] or block!=M7_RECOVERY_SCOPE['logical_block_id']:
        raise RuntimeError('Combined recovery is restricted to n25 Batch 003')
    if not isinstance(wait_seconds,int) or not 0<=wait_seconds<=86400:raise ValueError('wait_seconds must be 0..86400')
    make_service=service_factory or (lambda label:make_saved_profile_service(load_saved_account_profiles((label,))[label]))
    with WorkerLock(campaign_root(root,campaign_id)/'worker.lock'):
        registry=JobRegistry(JobRegistry.default_database(root))
        status=registry.independent_campaign_status(campaign_id)
        originals={r['treatment_id']:r for r in registry.rows(block) if r['attempt']==1}
        m3=originals['M3_ZNE'];m7=originals['M7_ALL']
        if any(r['batch_id']!=M7_RECOVERY_SCOPE['original_batch_id'] or r['account_label']!='labK' for r in originals.values()):
            raise RuntimeError('Original incident provenance mismatch')
        target=_target_from_level(root,next(l for l in status['levels'] if l['target_id']==m3['target_id']),registry=registry)
        if any(r['input_hashes']!=target.input_hashes for r in registry.rows(block)):
            raise RuntimeError('Frozen campaign hashes mismatch')
        recoveries=[r for r in registry.rows(block) if r['attempt']>1]
        if any(not r['metadata'].get('combined_treatment_recovery') for r in recoveries):
            raise RuntimeError('Another recovery workflow exists; reconcile it before combined recovery')
        per_pub=[r for r in registry.replacement_jobs(target.target_id) if r['logical_block_id']==block and r['treatment_id']=='M3_ZNE']
        if any(not r['metadata'].get('combined_pub_recovery') for r in per_pub):
            raise RuntimeError('Existing M3 replacement plans need their original recovery workflow')
        submission_performed=False;retrieval_errors=[]
        audit=audit_original_m3(root,target,registry,m3)
        if not recoveries and not any(r['state']!='PLANNED' for r in per_pub):
            # Always attempt read-only collection before deciding whether replacement is needed.
            for attempt in range(3):
                try:
                    original_service=make_service('labK')
                    collect_block(root,block,shots=4096,n=5,account_label='labK',ibm_account='labK',
                        instance=m3['metadata'].get('instance'),registry=registry,service_override=original_service,
                        treatments=('M3_ZNE',),job_row_id=m3['id'])
                    m3=next(r for r in registry.rows(block) if r['id']==m3['id'])
                    if m3['validated_pub_count']==5:break
                    retrieval_errors.append({'attempt':attempt+1,'error':'Collector did not validate five PUBs'})
                except Exception as exc:
                    retrieval_errors.append({'attempt':attempt+1,'error_type':type(exc).__name__})
            m3=next(r for r in registry.rows(block) if r['id']==m3['id'])
            audit=audit_original_m3(root,target,registry,m3)
            evidence={'checked_utc':utc_now(),'original_job_id':m3['job_id'],
                'status':'PER_PUB_AUDIT','attempts':retrieval_errors,'pub_audit':audit,
                'replacement_reason':replacement_reason if authorize_m3_replacement else ''}
            registry.transition(m3['id'],m3['submission_state'],metadata={'unresolved_retrieval':evidence},
                event_type='ORIGINAL_M3_PER_PUB_AUDIT',details=evidence)
            if not authorize_live:
                return {'state':'READ_ONLY','m3_validated':audit.get('validated_count',m3['validated_pub_count']),'m3_pub_audit':audit,
                    'retrieval_errors':retrieval_errors,'submission_performed':False,'batch004_submitted':False}
            cfg=load_final_config(root);auth=campaign_authorization_from_final_config(cfg)
            if not auth['live_auto_submit'] or 'submit' not in auth['allowed_actions']:
                raise RuntimeError('Existing configuration does not authorize recovery')
            if not audit['archive_verified']:
                raise RuntimeError('M3 archive provenance is unverified; preserve existing PUBs and reconcile before replacement')
            if audit['unresolved'] and (not authorize_m3_replacement or not replacement_reason.strip()):
                raise RuntimeError('Requires --authorize-m3-replacement and --replacement-reason for unresolved M3 PUBs')
            recoveries,submission_performed=_submit_combined_pub_recovery(root,target,registry,status,auth,cfg,
                originals,audit,make_service,replacement_reason,batch_factory,estimator_factory)
        per_pub=[r for r in registry.replacement_jobs(target.target_id) if r['logical_block_id']==block and r['treatment_id']=='M3_ZNE']
        deadline=time.monotonic()+wait_seconds
        results=_collect_combined_pub_replacements(root,target,registry,per_pub,make_service,deadline)
        for row in recoveries:
            service=make_service(row['account_label'])
            if not row['job_id']:
                # Match a positive identity only. Empty history never authorizes a retry.
                matches=[]
                if row['batch_id'] and row['metadata'].get('submit_started_utc'):
                    for candidate in iter_provider_jobs(service,session_id=row['batch_id'],instance=row['metadata']['instance']):
                        try:proof=verify_reconciliation_job(row,candidate,service,root=root)
                        except (ValueError,RuntimeError):continue
                        matches.append((candidate,proof))
                if len(matches)!=1:
                    results.append({'treatment':row['treatment_id'],'state':'BLOCKED','reason':'Submission identity unresolved; no retry'})
                    continue
                registry.link_reconciled_job(row,matches[0][0].job_id(),matches[0][1])
                row=next(r for r in registry.rows(block) if r['id']==row['id'])
            job=service.job(row['job_id']);verify_reconciliation_job(row,job,service,root=root)
            while str(job.status()).upper().split('.')[-1] not in ('DONE','COMPLETED','ERROR','FAILED','CANCELLED') and time.monotonic()<deadline:
                time.sleep(min(30,max(0,deadline-time.monotonic())))
            remote=str(job.status()).upper().split('.')[-1]
            if remote in ('DONE','COMPLETED'):
                archive_block=block+'-recovery-'+str(row['id'])
                record=record_directory(root,archive_block)/row['treatment_id']/'job_submitted.json'
                payload={**row['metadata'],'job_id':row['job_id'],'account_label':row['account_label'],
                    'backend':row['backend'],'batch_id':row['batch_id'],'input_hashes':row['input_hashes'],
                    'logical_block_id':block,'treatment':row['treatment_id']}
                if not record.exists():_write_reconciled_record(record,payload)
                elif any(json.loads(record.read_text()).get(key)!=payload[key] for key in ('job_id','account_label','batch_id','input_hashes','source_job_row_id')):raise RuntimeError('Recovery archive identity conflicts')
                for attempt in range(3):
                    try:
                        collect_block(root,block,shots=4096,n=5,account_label=row['account_label'],ibm_account=row['account_label'],
                            instance=row['metadata']['instance'],registry=registry,service_override=service,
                            treatments=(row['treatment_id'],),job_row_id=row['id'],archive_block_id=archive_block)
                        fresh=next(r for r in registry.rows(block) if r['id']==row['id'])
                        if fresh['validated_pub_count']==5:break
                    except (TimeoutError,ConnectionError,OSError):
                        if attempt==2:raise
            fresh=next(r for r in registry.rows(block) if r['id']==row['id'])
            results.append({'treatment':row['treatment_id'],'job_id':row['job_id'],'state':remote,
                            'validated_pubs':fresh['validated_pub_count']})
        decision=evaluate_batch_progression(registry,target,block)
        # Re-evaluate inside the same transaction as the completion write.
        with registry._transaction() as db:
            decision=evaluate_batch_progression(registry,target,block,_db=db)
            if decision['eligible'] and decision['validated_slots']==40:
                db.execute('UPDATE experiment_blocks SET status=?,updated_at=? WHERE logical_block_id=?',
                    (decision['dataset_status'],utc_now(),block))
        selected=[r for r in registry.repaired_pub_values(target.target_id) if r['logical_block_id']==block]
        dataset={'logical_block_id':block,'progression':decision,'selected_pubs':selected}
        digest=hashlib.sha256(json.dumps(dataset,sort_keys=True,default=str).encode()).hexdigest()
        destination=campaign_root(root,campaign_id)/'recovery_datasets'/('batch003_'+digest+'.json')
        if not destination.exists():_write_reconciled_record(destination,dataset)
        registry.record_independent_target_event(target.target_id,'COMBINED_RECOVERY_REVIEW',decision)
        return {'jobs':results,'m3_pub_audit':audit,'progression':decision,'dataset_path':str(destination),
                'submission_performed':submission_performed,'batch004_submitted':False}


def audit_original_m3(root, target, registry, row):
    """Inspect every original slot; reconcile counters only from verified immutable evidence."""
    from batch_progression import valid_scientific_values
    from job_registry import utc_now
    block=row['logical_block_id']
    with registry._connection() as db:
        sequence=db.execute('SELECT sequence_no FROM experiment_blocks WHERE logical_block_id=?',(block,)).fetchone()[0]
    offset=(sequence-1)*5
    ledger={r['pub_index']:r for r in registry.pub_validation_records(target.target_id) if r['job_row_id']==row['id']}
    result={'original_job_id':row['job_id'],'registered_validated_count':row['validated_pub_count'],
        'valid':[],'missing':[],'invalid':[],'unverified':[],'slots':[],'archive_verified':False}
    try:
        path,digest,payload,kind,_=_verified_raw_source(root,row,registry,expected_pubs=5,expected_shots=4096,
            expected_hashes=target.input_hashes,allow_missing=True)
        _verify_archived_treatment_options(root,row,4096)
    except (RuntimeError,OSError,ValueError) as exc:
        result.update(unverified=list(range(offset+1,offset+6)),archive_error=str(exc),
            unresolved=list(range(offset+1,offset+6)))
        return result
    result.update(archive_verified=True,archive_path=str(path),archive_sha256=digest)
    records={int(r.get('pub_index',i)):r for i,r in enumerate(payload.get('records',payload.get('pub_results',[])),1)}
    additions=[]
    for index in range(1,6):
        record=records.get(index);saved=ledger.get(index);logical=offset+index
        if record is None:
            category='missing'
            if saved and saved['expectation_valid'] and saved['uncertainty_valid']==1:
                raise RuntimeError('Archive omits an accepted original M3 PUB; preserve it and investigate')
        else:
            fields=record.get('data_fields',{}) or {}
            evs=record.get('expectation_values',record.get('evs',fields.get('evs')))
            stds=record.get('standard_deviations',record.get('stds',fields.get('stds')))
            good=valid_scientific_values({'expectation_values':evs,'standard_deviations':stds})
            category='valid' if good else 'invalid'
            if saved:
                if saved['source_sha256']!=digest or bool(saved['expectation_valid'] and saved['uncertainty_valid']==1)!=good:
                    raise RuntimeError('Immutable original M3 validation conflicts with verified archive; no replacement authorized')
                if good and (json.loads(saved['expectation_values_json'])!=evs or json.loads(saved['standard_deviations_json'])!=stds):
                    raise RuntimeError('Original M3 scientific values conflict with archive')
            elif good:
                additions.append(dict(pub_index=index,expectation_valid=True,uncertainty_valid=True,
                    uncertainty_state='PRESENT',expectation_values=evs,standard_deviations=stds,metadata=record.get('metadata',{})))
        result[category].append(logical)
        result['slots'].append({'source_pub_index':index,'logical_pub_index':logical,'status':category})
    if additions:
        registry.record_pub_validation_records(row['id'],registry.independent_experiment_id(target.target_id),digest,additions)
    # Do not reset to a presumed count: this exact count is backed by all five audited slots.
    count=len(result['valid'])
    if count!=row['validated_pub_count']:
        with registry._transaction() as db:
            current=db.execute('SELECT validated_pub_count FROM jobs WHERE id=?',(row['id'],)).fetchone()[0]
            if current!=row['validated_pub_count']:raise RuntimeError('Original count changed during archive audit')
            db.execute('UPDATE jobs SET validated_pub_count=?,collected_pub_count=MAX(collected_pub_count,?),completed_pub_count=MAX(completed_pub_count,?),updated_at=? WHERE id=?',
                (count,count,count,utc_now(),row['id']))
            db.execute('INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)',
                (row['id'],'M3_COUNT_RECONCILED_FROM_ARCHIVE',json.dumps(result,sort_keys=True),utc_now()))
    result['validated_count']=count
    result['unresolved']=sorted(result['missing']+result['invalid']+result['unverified'])
    return result


def _submit_combined_pub_recovery(root,target,registry,status,auth,cfg,originals,audit,
                                 make_service,reason,batch_factory,estimator_factory):
    """Use the existing one-PUB ledger and M7 intent inside one Runtime Batch."""
    from types import SimpleNamespace
    from job_registry import utc_now
    from runtime_estimator import targeted_replacement_estimate, workload_quota_decision
    from batch_scheduler import _runtime_types, verify_reconciliation_job
    from vqe_adapter import verify_frozen_inputs,treatment_options
    from read_only_preflight import _instance_ref
    from quota import qpu_seconds_from_metrics
    m3=originals['M3_ZNE'];m7=originals['M7_ALL'];block=m3['logical_block_id']
    accepted={r['logical_pub_index'] for r in registry.repaired_pub_values(target.target_id)
              if r['logical_block_id']==block and r['treatment_id']=='M3_ZNE'}
    slots=[r for r in audit['slots'] if r['logical_pub_index'] in audit['unresolved'] and r['logical_pub_index'] not in accepted]
    if slots:
        source_service=make_service(m3['account_label']);job=source_service.job(m3['job_id'])
        verify_reconciliation_job(m3,job,source_service,root=root)
        if str(job.status()).upper().split('.')[-1] not in ('DONE','COMPLETED'):
            raise RuntimeError('Original M3 must be verified DONE before targeted replacement')
        registry.transition(m3['id'],'COMPLETED',qpu_usage_seconds=qpu_seconds_from_metrics(job.metrics()),
            event_type='ORIGINAL_M3_DONE_VERIFIED_FOR_TARGETED_RECOVERY')
    need_m7=not m7['job_id']
    if need_m7:
        evidence=verify_missing_m7_absence(root,registry,block,make_service('labK'),allow_uncollected_m3=True)
        registry.transition(m7['id'],'CONFIRMED_NOT_SUBMITTED',release_reservation=True,
            metadata={'m7_absence_evidence':evidence},event_type='CONFIRMED_NO_SUBMISSION',details=evidence)
    elif m7['validated_pub_count']!=5:
        raise RuntimeError('M7 already has an identity; reconcile and collect it, never submit again')
    counts={};estimates={}
    if slots:
        counts['M3_ZNE']=len(slots)
        estimates['M3_ZNE']=targeted_replacement_estimate(root,target,'M3_ZNE',registry=registry,target_id=target.target_id)
    if need_m7:
        counts['M7_ALL']=5
        estimates['M7_ALL']=missing_m7_runtime_estimate(root,registry,target.input_hashes)
    if not counts:return [],False
    for name,est in estimates.items():
        gate=workload_quota_decision(est,remaining_seconds=1e12,
            workload_type='targeted_replacement' if name=='M3_ZNE' else 'missing_treatment')
        if not gate['eligible']:raise RuntimeError('Recovery component estimate rejected: '+gate['rejection_reason'])
    estimate=SimpleNamespace(scope='combined_treatment_recovery',treatment_ids=tuple(counts),pub_counts=counts,
        pub_count=sum(counts.values()),defensible=all(e.defensible for e in estimates.values()),
        operator_approved_provisional=all(e.defensible or getattr(e,'operator_approved_provisional',False) for e in estimates.values()),
        stress_block_seconds=sum(e.stress_block_seconds*(len(slots) if name=='M3_ZNE' else 1) for name,e in estimates.items()))
    account,service,preflight,instance,backend=_select_recovery_account(root,target,registry,status,auth,estimate,make_service,'combined_recovery')
    qc,obs,theta,physical,_,_=verify_frozen_inputs(root)
    if len(theta)!=21 or len(obs)!=7 or tuple(physical)!=tuple(range(140,147)):
        raise RuntimeError('Frozen shape mismatch')
    pub=_coerce_replacement_estimator_pub(qc,obs,theta)
    canonical={name:treatment_options(name,4096,root) for name in counts}
    if batch_factory is None or estimator_factory is None:
        real_batch,real_estimator=_runtime_types();batch_factory=batch_factory or real_batch;estimator_factory=estimator_factory or real_estimator
    plans=[]
    for slot in slots:
        plan=registry.plan_pub_replacement(target_id=target.target_id,logical_block_id=block,treatment_id='M3_ZNE',
            source_pub_index=slot['source_pub_index'],logical_pub_index=slot['logical_pub_index'],
            options={'canonical_serialized_options':repr(canonical['M3_ZNE'])},replacement_account_label=account,
            max_attempts=cfg['maximum_replacement_attempts'],metadata={'combined_pub_recovery':True,
                'source_run':'run_02','source_job_id':m3['job_id'],'source_archive_path':audit['archive_path'],
                'source_archive_sha256':audit['archive_sha256'],'input_hashes':target.input_hashes,
                'shots_per_pub':4096,'physical_qubits':list(range(140,147)),'replacement_reason':reason,
                'instance':instance,'instance_ref':_instance_ref(instance),'runtime_options_requested':repr(canonical['M3_ZNE'])})
        if plan['state']!='PLANNED':raise RuntimeError('Existing replacement intent requires reconciliation')
        plans.append(plan['replacement_id'])
    from final_configuration import quota_eligibility
    shared_reservation=quota_eligibility(0,workload_type="combined_recovery")["reservation_seconds"]/(len(plans)+int(need_m7))
    # Durable reservations precede Batch creation. A crash here blocks automatic retry.
    mids=registry.reserve_treatment_recovery([m7['id']],account_label=account,
        estimates={'M7_ALL':estimates['M7_ALL'].stress_block_seconds},metadata={'instance':instance,'source_run':'run_02',
            'input_hashes':target.input_hashes,'options_by_treatment':{'M7_ALL':repr(canonical['M7_ALL'])},
            'preflight_checked_at':preflight['checked_utc']},
        reservation_by_treatment={'M7_ALL':shared_reservation}) if need_m7 else []
    for rid in plans:
        registry.claim_pub_replacement_submission(rid,reservation_seconds=shared_reservation,
            preflight_checked_at=preflight['checked_utc'],quota_scope='account_wide_verified',
            runtime_upper_bound_seconds=estimates['M3_ZNE'].stress_block_seconds,
            safety_margin_seconds=auth['safety_margin_seconds'],instance_ref=_instance_ref(instance))
    try:
        with batch_factory(backend=backend) as batch:
            bid=batch.session_id
            if callable(bid):bid=bid()
            if not bid:raise RuntimeError('No recovery Batch ID returned')
            for rid in plans:
                tag=f'mxene-replacement-{target.target_id}-{rid}'
                options=treatment_options('M3_ZNE',4096,root)
                options.environment.job_tags=[tag]
                registry.transition_pub_replacement(rid,'SUBMITTING',batch_id=str(bid),
                    metadata={'instance':instance,'submit_started_utc':utc_now(),'recovery_job_tag':tag},
                    event_type='COMBINED_PUB_SUBMISSION_INTENT')
                job=estimator_factory(mode=batch,options=options).run([pub]);jid=job.job_id()
                if not jid:raise RuntimeError('Provider returned no replacement job ID')
                registry.transition_pub_replacement(rid,'SUBMITTED',job_id=jid,metadata={'submitted_utc':utc_now()},
                    event_type='COMBINED_PUB_JOB_ID_PERSISTED')
            for rid in mids:
                registry.transition(rid,'SUBMITTING',batch_id=str(bid),metadata={'submit_started_utc':utc_now()},
                    event_type='COMBINED_M7_SUBMISSION_INTENT')
                job=estimator_factory(mode=batch,options=canonical['M7_ALL']).run([pub]*5);jid=job.job_id()
                if not jid:raise RuntimeError('Provider returned no M7 job ID')
                registry.transition(rid,'SUBMITTED',job_id=jid,event_type='COMBINED_M7_JOB_ID_PERSISTED')
    except BaseException:
        for row in registry.replacement_jobs(target.target_id):
            if row['replacement_id'] in plans and row['state']=='SUBMITTING' and not row['job_id']:
                registry.transition_pub_replacement(row['replacement_id'],'UNKNOWN',event_type='COMBINED_PUB_OUTCOME_UNKNOWN')
        for row in registry.rows(block):
            if row['id'] in mids and row['submission_state']=='SUBMITTING' and not row['job_id']:
                registry.transition(row['id'],'UNKNOWN',event_type='COMBINED_M7_OUTCOME_UNKNOWN')
        raise
    return [r for r in registry.rows(block) if r['id'] in mids],True


def _collect_combined_pub_replacements(root,target,registry,rows,make_service,deadline):
    """Reuse one-PUB collection; reconcile positive identities with unique intent tags."""
    import time
    from batch_scheduler import verify_reconciliation_job
    results=[]
    for item in rows:
        rid=item['replacement_id'];service=make_service(item['account_label'])
        synthetic={**item,'chunk_n':1,'metadata':item['metadata']}
        if not item['job_id']:
            matches=[];tag=item['metadata'].get('recovery_job_tag')
            if item['batch_id'] and tag:
                for candidate in iter_provider_jobs(service,session_id=item['batch_id'],instance=item['metadata']['instance']):
                    tags=candidate.tags() if callable(getattr(candidate,'tags',None)) else getattr(candidate,'tags',[])
                    if not isinstance(tags,(tuple,list)) or tag not in tags:continue
                    try:proof=verify_reconciliation_job(synthetic,candidate,service,root=root)
                    except (RuntimeError,ValueError):continue
                    matches.append((candidate,proof))
            if len(matches)!=1:
                results.append({'replacement_id':rid,'logical_pub_index':item['logical_pub_index'],'state':'BLOCKED',
                    'reason':'Submission identity unresolved; no retry'})
                continue
            registry.transition_pub_replacement(rid,'SUBMITTED',job_id=matches[0][0].job_id(),
                metadata={'submission_reconciliation':matches[0][1]},event_type='COMBINED_PUB_IDENTITY_RECONCILED')
            item=next(r for r in registry.replacement_jobs(target.target_id) if r['replacement_id']==rid)
            synthetic={**item,'chunk_n':1}
        job=service.job(item['job_id']);verify_reconciliation_job(synthetic,job,service,root=root)
        while str(job.status()).upper().split('.')[-1] not in ('DONE','COMPLETED','ERROR','FAILED','CANCELLED') and time.monotonic()<deadline:
            time.sleep(min(30,max(0,deadline-time.monotonic())))
        for attempt in range(3):
            try:
                report=collect_replacement(root,target,registry,rid,service);break
            except (TimeoutError,ConnectionError,OSError):
                if attempt==2:raise
        results.append({**report,'treatment':'M3_ZNE','logical_pub_index':item['logical_pub_index'],'job_id':item['job_id']})
    return results


def _audit_m3_pub15_scope(root,target,registry,block):
    """Read-only exact-slot and archive audit; retain the 39 accepted observations."""
    from batch_progression import TREATMENTS,valid_scientific_values
    from vqe_adapter import verify_frozen_inputs
    from repository_paths import source_hashes,frozen_source
    qc,obs,theta,physical,_,_=verify_frozen_inputs(root)
    if (target.source_run!='run_02' or target.shots_per_pub!=4096 or
            len(theta)!=21 or len(obs)!=7 or tuple(physical)!=tuple(range(140,147)) or
            len(qc.parameters)!=0 or source_hashes(frozen_source(root))!=target.input_hashes):
        raise RuntimeError('Frozen Fixed ISA run_02 inputs differ from the registered target')
    rows=registry.rows(block)
    m3=next(r for r in rows if r['treatment_id']=='M3_ZNE' and r['attempt']==1)
    m7=next((r for r in rows if r['job_id']=='dbnc6e8ou4as73b493sg'),None)
    if (m3['batch_id']!=M7_RECOVERY_SCOPE['original_batch_id'] or m3['account_label']!='labK' or
            m3['submission_state']!='COMPLETED' or m3['validated_pub_count']!=4 or
            not m7 or m7['treatment_id']!='M7_ALL' or m7['submission_state']!='COMPLETED' or m7['validated_pub_count']!=5):
        raise RuntimeError('Expected original four-valid-PUB M3 and completed five-PUB M7 recovery')
    selected=[r for r in registry.repaired_pub_values(target.target_id) if r['logical_block_id']==block]
    expected={(name,i) for name in TREATMENTS for i in range(11,16)}-{('M3_ZNE',15)}
    retained=[r for r in selected if (r['treatment_id'],r['logical_pub_index'])!=('M3_ZNE',15)]
    if len(retained)!=39 or {(r['treatment_id'],r['logical_pub_index']) for r in retained}!=expected:
        raise RuntimeError('Recovery requires exactly the other 39 validated logical PUBs')
    archives={}
    def fields(record):
        data=record.get('data_fields',{}) or {}
        return {'expectation_values':record.get('expectation_values',record.get('evs',data.get('evs'))),
                'standard_deviations':record.get('standard_deviations',record.get('stds',data.get('stds')))}
    for value in retained:
        source=next((r for r in rows if r['job_id']==value['job_id'] and r['treatment_id']==value['treatment_id']),None)
        if source is None:raise RuntimeError('Retained PUB has no original/recovered treatment identity')
        if source['job_id'] not in archives:
            archives[source['job_id']]=_verified_raw_source(root,source,registry,expected_pubs=5,
                expected_shots=4096,expected_hashes=target.input_hashes)
        path,digest,payload,_,_=archives[source['job_id']]
        record=payload.get('records',payload.get('pub_results'))[(value['logical_pub_index']-1)%5]
        actual=fields(record)
        if (value['source_sha256']!=digest or not valid_scientific_values(actual) or
                any(actual[key]!=value[key] for key in actual)):
            raise RuntimeError('Retained PUB archive/value provenance mismatch')
    path,digest,payload,_,_=archives[m3['job_id']]
    invalid=fields(payload.get('records',payload.get('pub_results'))[4])
    try:nonfinite=all(len(values)==7 and any(not math.isfinite(float(x)) for x in values) for values in invalid.values())
    except (TypeError,ValueError,OverflowError):nonfinite=False
    if not nonfinite:raise RuntimeError('M3 source PUB 5 must have archived non-finite expectation and uncertainty')
    _verify_archived_treatment_options(root,m3,4096)
    return {'source':m3,'archive_path':str(path),'archive_sha256':digest,'retained':retained,
        'already_selected':any(r['treatment_id']=='M3_ZNE' and r['logical_pub_index']==15 for r in selected)}


def recover_m3_pub15(root,campaign_id,block,*,authorize_live=False,confirm_m3_pub15=False,
                     wait_seconds=0,service_factory=None,batch_factory=None,estimator_factory=None):
    """Incident-only one-PUB repair; no campaign loop or M7 submission path."""
    import time
    from worker_runtime import WorkerLock
    from independent_n_campaign import campaign_root,_target_from_level
    from job_registry import JobRegistry
    from final_configuration import load_final_config,campaign_authorization_from_final_config
    from account_config import load_saved_account_profiles,make_saved_profile_service
    from runtime_estimator import targeted_replacement_estimate
    from batch_scheduler import verify_reconciliation_job
    from batch_progression import evaluate_batch_progression
    from vqe_adapter import treatment_options
    from read_only_preflight import _instance_ref
    if campaign_id!=M7_RECOVERY_SCOPE['campaign_id'] or block!=M7_RECOVERY_SCOPE['logical_block_id']:
        raise RuntimeError('M3 PUB 15 recovery is restricted to n25 Batch 003')
    if authorize_live and not confirm_m3_pub15:raise RuntimeError('Requires --live and --confirm-m3-pub15')
    if not isinstance(wait_seconds,int) or not 0<=wait_seconds<=86400:raise ValueError('wait_seconds must be 0..86400')
    with WorkerLock(campaign_root(root,campaign_id)/'worker.lock'):
        registry=JobRegistry(JobRegistry.default_database(root),readonly=not authorize_live)
        status=registry.independent_campaign_status(campaign_id)
        rows=registry.rows(block);original=next(r for r in rows if r['treatment_id']=='M3_ZNE' and r['attempt']==1)
        level=next(l for l in status['levels'] if l['target_id']==original['target_id'])
        target=_target_from_level(root,level,registry=registry)
        audit=_audit_m3_pub15_scope(root,target,registry,block)
        plans=[r for r in registry.replacement_jobs(target.target_id)
               if r['logical_block_id']==block and r['treatment_id']=='M3_ZNE' and r['logical_pub_index']==15]
        if len(plans)>1:raise RuntimeError('Multiple PUB 15 attempts require manual reconciliation')
        item=plans[0] if plans else None
        if item and (item['source_pub_index']!=5 or item['metadata'].get('source_job_id')!=original['job_id'] or
                item['metadata'].get('source_archive_sha256')!=audit['archive_sha256'] or item['input_hashes']!=target.input_hashes):
            raise RuntimeError('Existing PUB 15 intent has conflicting immutable provenance')
        if (any(r['submission_state'] in ('UNKNOWN','SUBMITTING') for r in registry.rows()) or
                any(r['state'] in ('UNKNOWN','SUBMITTING','RESERVED') for r in registry.replacement_runtime_candidates())):
            return {'state':'BLOCKED','reason':'Unresolved submission requires reconciliation; no retry','submission_performed':False}
        if audit['already_selected']:
            decision=evaluate_batch_progression(registry,target,block)
            if not decision['eligible']:raise RuntimeError('Selected PUB 15 does not establish complete frozen provenance')
            return {'state':decision['dataset_status'],'progression':decision,'submission_performed':False,'batch004_submitted':False}
        if item and item['state']=='FAILED':
            return {'state':'BLOCKED','reason':'Existing failed replacement requires explicit review; no automatic retry','submission_performed':False}
        if not authorize_live:
            estimate=targeted_replacement_estimate(root,target,'M3_ZNE',registry=registry,target_id=target.target_id)
            return {'state':'READY' if item is None or item['state']=='PLANNED' else 'RESUME_REQUIRED',
                    'treatment_id':'M3_ZNE','logical_pub_index':15,'source_pub_index':5,
                    'preserved_pub_count':39,'original_job_id':original['job_id'],'source_archive_sha256':audit['archive_sha256'],
                    'runtime_upper_bound_seconds':estimate.stress_block_seconds,
                    'operator_approved_provisional_estimate':getattr(estimate,'operator_approved_provisional',False),
                    'submission_performed':False,'provider_preflight_required':True}
        cfg=load_final_config(root);auth=campaign_authorization_from_final_config(cfg)
        make_service=service_factory or (lambda label:make_saved_profile_service(load_saved_account_profiles((label,))[label]))
        submitted=False
        if item is None or item['state']=='PLANNED':
            # The immutable DONE archive was verified above. Retrieval access to
            # an exhausted original account is not a prerequisite for authorized
            # failover; fresh provider checks apply to the selected new account.
            estimate=targeted_replacement_estimate(root,target,'M3_ZNE',registry=registry,target_id=target.target_id)
            account,service,preflight,instance,backend=_select_recovery_account(
                root,target,registry,status,auth,estimate,make_service,'targeted_replacement')
            options=treatment_options('M3_ZNE',4096,root)
            plan=registry.plan_pub_replacement(target_id=target.target_id,logical_block_id=block,treatment_id='M3_ZNE',
                source_pub_index=5,logical_pub_index=15,options={'canonical_serialized_options':repr(options)},
                replacement_account_label=account,max_attempts=cfg['maximum_replacement_attempts'],
                metadata={'m3_pub15_only':True,'source_run':'run_02','source_job_id':original['job_id'],
                    'source_archive_path':audit['archive_path'],'source_archive_sha256':audit['archive_sha256'],
                    'input_hashes':target.input_hashes,'shots_per_pub':4096,'physical_qubits':list(range(140,147)),
                    'instance':instance,'instance_ref':_instance_ref(instance),'runtime_options_requested':repr(options)})
            rid=plan['replacement_id']
            submit_replacement_slot(root,target,registry,rid,service,final_config=cfg,campaign_authorization=auth,
                preflight_report=preflight,runtime_estimate=estimate,explicit_authorization=True,
                active_instance_ref=_instance_ref(instance),authorized_instance_refs=auth['allowed_instance_refs'][account],
                batch_factory=batch_factory,estimator_factory=estimator_factory)
            submitted=True
            item=next(r for r in registry.replacement_jobs(target.target_id) if r['replacement_id']==rid)
        else:service=make_service(item['account_label'])
        if _instance_ref(service.active_instance())!=item['metadata'].get('instance_ref'):
            raise RuntimeError('Replacement provider instance differs from pinned submission instance')
        synthetic={**item,'chunk_n':1,'metadata':{**item['metadata'],'instance':service.active_instance()}}
        job=service.job(item['job_id']);verify_reconciliation_job(synthetic,job,service,root=root)
        deadline=time.monotonic()+wait_seconds
        while str(job.status()).upper().split('.')[-1] not in ('DONE','COMPLETED','ERROR','FAILED','CANCELLED') and time.monotonic()<deadline:
            time.sleep(min(30,max(0,deadline-time.monotonic())))
        result=collect_replacement(root,target,registry,item['replacement_id'],service)
        refreshed=_audit_m3_pub15_scope(root,target,registry,block)
        if audit['retained']!=refreshed['retained']:raise RuntimeError('Accepted original PUB values changed during recovery')
        decision=evaluate_batch_progression(registry,target,block)
        registry.record_independent_target_event(target.target_id,'M3_PUB15_ONLY_COLLECTION_REVIEW',decision)
        if decision['eligible']:
            registry.record_batch_recovery_review(block,batch_execution_status='INCOMPLETE',recovery_status='RECOVERED',
                completeness_policy_version='complete-only-repaired-v1',
                valid_fraction_by_treatment={name:1.0 for name in target.treatment_ids},
                raw_archives_verified=True,provenance_verified=decision['frozen_inputs_verified'],
                details={'dataset_status':decision['dataset_status'],'replacement_id':item['replacement_id'],
                    'treatment_id':'M3_ZNE','logical_pub_index':15,'validated_slots':40,'batch004_submitted':False})
        return {'state':decision['dataset_status'],'replacement':result,'progression':decision,
            'submission_performed':submitted,'batch004_submitted':False}


def inspect_m7_absence(root,campaign_id,block,*,service_factory=None):
    """Read-only provider diagnostic under the existing worker lock; never submit."""
    from worker_runtime import WorkerLock
    from independent_n_campaign import campaign_root
    from job_registry import JobRegistry
    from account_config import load_saved_account_profiles,make_saved_profile_service
    if campaign_id!=M7_RECOVERY_SCOPE['campaign_id'] or block!=M7_RECOVERY_SCOPE['logical_block_id']:
        raise RuntimeError('M7 absence inspection is scoped to n25 Batch 003')
    with WorkerLock(campaign_root(root,campaign_id)/'worker.lock'):
        registry=JobRegistry(JobRegistry.default_database(root))
        make_service=service_factory or (lambda label:make_saved_profile_service(load_saved_account_profiles((label,))[label]))
        local=inspect_original_m7_intent(root,registry,block)
        try:service=make_service('labK')
        except Exception as exc:
            return {'state':'BLOCKED','submission_performed':False,'closure_evidence':{
                'batch_id':M7_RECOVERY_SCOPE['original_batch_id'],'provider_details':None,'provider_status':None,
                'local_intent_evidence':local,
                'provider_read_error':{'api':'QiskitRuntimeService(name=labK)','error_type':type(exc).__name__,
                    'cause_type':type(exc.__cause__).__name__ if exc.__cause__ else None},
                'missing_evidence':['Authenticated provider access','Current Batch details and closure semantics',
                    'Complete original Batch inventory with job identities and DONE states',
                    'Complete provider history through the current observation'],
                'submission_closed':False,'settled':False}}
        try:
            evidence=verify_missing_m7_absence(root,registry,block,service,allow_uncollected_m3=True,
                allow_independent_absence=True)
            return {'state':'M7_ABSENCE_VERIFIED','submission_performed':False,'evidence':evidence}
        except BatchClosureUnverified as exc:
            return {'state':'BLOCKED','submission_performed':False,'closure_evidence':exc.evidence}
