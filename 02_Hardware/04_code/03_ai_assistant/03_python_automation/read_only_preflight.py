"""Explicit, read-only IBM account preflight. This module never creates Batch/jobs."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


from datetime import datetime, timezone
import hashlib
import inspect
from importlib.metadata import version
from typing import Any

from account_config import (ACCOUNT_LABELS, AccountCredentials, SavedAccountProfile,
                            load_authorized_accounts, load_saved_account_profiles,
                            make_runtime_service, make_saved_profile_service)
from quota import NOMINAL_ACCOUNT_BUDGET_SECONDS, report_quota_fields
from final_configuration import provider_quota_thresholds, MIN_BATCH_QUOTA_SECONDS, MIN_RECOVERY_QUOTA_SECONDS


def _stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _instance_ref(crn: str) -> str:
    return hashlib.sha256(crn.encode("utf-8")).hexdigest()[:12]


def _usage_summary(raw: Any) -> dict:
    from quota import provider_quota_fields
    return provider_quota_fields(raw)


def _compatibility():
    try:
        from low_memory_runtime import remote_runtime
        remote_runtime()
        from qiskit_ibm_runtime import Batch, EstimatorV2
        batch_sig = inspect.signature(Batch)
        estimator_sig = inspect.signature(EstimatorV2)
        batch_ok = "backend" in batch_sig.parameters
        estimator_ok = "mode" in estimator_sig.parameters
        runtime_version = version("qiskit-ibm-runtime")
        version_tuple = tuple(int(part) for part in runtime_version.split(".")[:2] if part.isdigit())
        return {"available": bool(batch_ok and estimator_ok), "batch_backend_parameter": batch_ok,
                "estimator_mode_parameter": estimator_ok, "runtime_version": runtime_version,
                "estimator_v2_deprecated": version_tuple >= (0, 50),
                "batch_created": False}
    except Exception as exc:
        return {"available": False, "reason": f"SDK compatibility inspection failed ({type(exc).__name__})",
                "batch_created": False}


def _status_fields(status):
    return {"operational": getattr(status, "operational", None),
            "status_message": str(getattr(status, "status_msg", "unknown")),
            "pending_jobs": getattr(status, "pending_jobs", None)}


def _safe_reason(exc, context):
    # Provider exception strings can contain account identifiers or request details.
    return f"{context} ({type(exc).__name__})"


def _unsupported_isa_instructions(target, instructions):
    """Return frozen ISA operations absent from a BackendV2 Target.

    Qiskit 2.x Target.instruction_supported names its first argument
    ``operation_name`` (not ``instruction_name``). Keep this check explicit so
    API/type errors remain compatibility warnings instead of backend failures.
    """
    if target is None or not callable(getattr(target, "instruction_supported", None)):
        raise TypeError("backend Target does not expose instruction_supported")
    unsupported = []
    for instruction in instructions:
        operation = instruction["operation"]
        qargs = tuple(instruction["qubits"])
        if operation not in ("barrier", "delay") and not target.instruction_supported(
                operation_name=operation, qargs=qargs):
            unsupported.append((operation, qargs))
    return unsupported


def _preflight_account(credentials: AccountCredentials | SavedAccountProfile, *, service_factory=None,
                      backend_name="ibm_fez", frozen_validator=None, allowed_instance_refs=None) -> dict:
    """Check access and read-only metadata; redact all secret and CRN values."""
    checks = {}
    reasons = []
    label = credentials.label
    auth_mode = getattr(credentials, "authentication_mode", "environment")
    result = {"schema_version": 1, "account_label": label, "authentication_mode": auth_mode,
              "backend": backend_name,
              "nominal_account_budget_seconds": NOMINAL_ACCOUNT_BUDGET_SECONDS,
              "submission": False, "checked_utc": _stamp(),
              "batch_submission_eligible":False,"recovery_submission_eligible":False,
              "remaining_provider_quota_seconds":None,"estimated_submission_runtime_seconds":None,
              "required_runtime_with_margin_seconds":None,"account_selection_priority":None,
              "rejection_reason":"Preflight has not passed", "submission_eligible_scope":"read_only_preflight_only",
              "workload_authorization_required":True}
    def read_failed(exc):
        from worker_runtime import transient_read_error
        key='retryable_read_failure' if transient_read_error(exc) else 'nonretryable_read_failure'
        result[key]=True
    try:
        if service_factory is None:
            service_factory = make_saved_profile_service if isinstance(credentials, SavedAccountProfile) else make_runtime_service
        service = service_factory(credentials)
    except Exception as exc:
        read_failed(exc)
        return {**result, "status": "FAIL", "submission_eligible": False,
                "checks": {"authentication": "FAIL"}, "reasons": [_safe_reason(exc, "Authentication failed")],
                "instances": [], "usage_scope": "unknown"}
    try:
        configured_crn = getattr(credentials, "crn", None) or service.active_instance()
        if not isinstance(configured_crn, str) or not configured_crn:
            raise ValueError("configured instance identifier unavailable")
        actual_active = service.active_instance() if callable(getattr(service, "active_instance", None)) else configured_crn
        if actual_active != configured_crn:
            raise RuntimeError("service active instance did not match the configured instance")
    except Exception as exc:
        read_failed(exc)
        return {**result, "status": "FAIL", "submission_eligible": False,
                "checks": {"authentication": "PASS", "configured_instance": "FAIL"},
                "reasons": [_safe_reason(exc, "Configured instance could not be verified")],
                "instances": [], "usage_scope": "unknown"}
    result["configured_instance_ref"] = _instance_ref(configured_crn)
    if allowed_instance_refs is not None and result["configured_instance_ref"] not in allowed_instance_refs:
        return {**result, "status":"FAIL", "submission_eligible":False,
                "reasons":["Saved profile instance is not authorized"], "instances":[],
                "account_wide_usage_verified":False}
    checks["authentication"] = "PASS"
    checks["configured_instance"] = "PASS"
    if frozen_validator is not None:
        try:
            result["frozen_input_validation"] = frozen_validator()
            checks["frozen_run_02_inputs"] = "PASS"
        except Exception as exc:
            read_failed(exc)
            result["frozen_input_validation"] = {"error": _safe_reason(exc, "Frozen run_02 validation failed")}
            checks["frozen_run_02_inputs"] = "FAIL"
            reasons.append(result["frozen_input_validation"]["error"])
    else:
        checks["frozen_run_02_inputs"] = "WARN"
        reasons.append("Frozen input validator was not supplied")

    instances = []
    instance_listing_available = False
    try:
        listed = service.instances()
        if not isinstance(listed, (list, tuple)):
            raise TypeError("unsupported instance listing")
        for item in listed:
            if not isinstance(item, dict):
                continue
            crn = item.get("crn")
            if isinstance(crn, str) and crn.startswith("crn:"):
                instances.append({"crn": crn, "plan": str(item.get("plan", "unknown"))})
        instance_listing_available = True
        checks["accessible_instances"] = "PASS" if any(x["crn"] == configured_crn for x in instances) else "FAIL"
        if checks["accessible_instances"] == "FAIL":
            reasons.append("Configured CRN is not listed among instances accessible to this account")
    except Exception as exc:
        read_failed(exc)
        checks["accessible_instances"] = "WARN"
        reasons.append(_safe_reason(exc, "Accessible instance listing unavailable"))
        instances = [{"crn": configured_crn, "plan": "unknown"}]

    usage_rows = []
    configured_backend_found = False
    configured_backend_operational = None
    configured_isa_compatible = None
    pending_total = 0
    pending_available = True
    for item in instances:
        crn = item["crn"]
        row = {"instance_ref": _instance_ref(crn), "plan": item["plan"]}
        if crn != configured_crn:
            row["scope"] = "listed_only; configured profile instance was not changed"
            usage_rows.append(row)
            continue
        scoped = service
        try:
            # Qiskit usage is for the current active instance. Keep the saved profile's
            # configured instance untouched and query usage only in that scope.
            actual_active = scoped.active_instance() if callable(getattr(scoped, "active_instance", None)) else crn
            if actual_active != crn:
                raise RuntimeError("service active instance did not match the configured instance")
            usage = _usage_summary(scoped.usage())
            row["usage"] = usage
            result["usage_checked_utc"] = _stamp()
        except Exception as exc:
            read_failed(exc)
            row["usage_error"] = _safe_reason(exc, "Instance usage unavailable")
            checks["usage"] = "WARN"
            reasons.append(f"Instance {row['instance_ref']}: {row['usage_error']}")
        else:
            if checks.get("usage") != "WARN":
                checks["usage"] = "PASS"
            checks["provider_quota"] = "PASS" if usage["remaining_seconds"] > 0 else "WARN"
            if usage["remaining_seconds"] <= 0:
                reasons.append("Provider quota is exhausted")
        try:
            backend = scoped.backend(backend_name, instance=crn)
            if getattr(backend, "name", backend_name) != backend_name:
                raise RuntimeError("service returned an unexpected backend name")
            configured_backend_found = True
            status = _status_fields(backend.status())
            row["backend_status"] = status
            configured_backend_operational = status["operational"]
        except Exception as exc:
            read_failed(exc)
            row["backend_error"] = _safe_reason(exc, f"{backend_name} unavailable")
            reasons.append(f"Instance {row['instance_ref']}: {row['backend_error']}")
        else:
            frozen = result.get("frozen_input_validation")
            target = getattr(backend, "target", None)
            if not isinstance(frozen, dict):
                row["isa_compatible"] = None
                checks["isa_compatibility"] = "WARN"
                reasons.append(f"Instance {row['instance_ref']}: frozen ISA metadata unavailable")
            else:
                try:
                    unsupported = _unsupported_isa_instructions(target, frozen.get("isa_instructions", []))
                    row["isa_compatible"] = not unsupported
                    configured_isa_compatible = not unsupported
                    if unsupported:
                        checks["isa_compatibility"] = "FAIL"
                        reasons.append(f"Instance {row['instance_ref']}: frozen ISA operations unsupported ({', '.join(f'{op}@{qargs}' for op, qargs in unsupported[:8])})")
                    else:
                        checks["isa_compatibility"] = "PASS"
                except Exception as exc:
                    read_failed(exc)
                    row["isa_compatible"] = None
                    row["isa_compatibility_error"] = _safe_reason(exc, "ISA compatibility check failed")
                    checks["isa_compatibility"] = "WARN"
                    reasons.append(f"Instance {row['instance_ref']}: {row['isa_compatibility_error']}")
        try:
            jobs = scoped.jobs(limit=100, pending=True, instance=crn)
            row["pending_jobs_count"] = len(jobs)
            pending_total += len(jobs)
        except Exception as exc:
            read_failed(exc)
            pending_available = False
            row["pending_jobs"] = "unavailable"
            reasons.append(f"Instance {row['instance_ref']}: {_safe_reason(exc, 'Pending-job listing unavailable')}")
        usage_rows.append(row)

    result["instances"] = [{k: v for k, v in row.items() if k != "crn"} for row in usage_rows]
    result["accessible_instance_count"] = len(instances) if instance_listing_available else None
    result["pending_jobs_count"] = pending_total if pending_available else None
    result["pending_jobs_per_instance_limit"] = 100
    result["checks"] = checks
    if not instances or not any(x["crn"] == configured_crn for x in instances):
        checks["authentication"] = "FAIL"
    checks["backend_ibm_fez"] = "PASS" if configured_backend_found else "FAIL"
    if not configured_backend_found:
        reasons.append("Configured instance does not expose backend ibm_fez")
    if configured_backend_found and configured_backend_operational is not True:
        checks["backend_operational"] = "WARN"
        reasons.append("Backend is accessible in the configured instance but is not confirmed operational")
    else:
        checks["backend_operational"] = "PASS" if configured_backend_found else "FAIL"
    if configured_isa_compatible is not None:
        checks["isa_compatibility"] = "PASS" if configured_isa_compatible else "FAIL"
    elif configured_backend_found:
        checks["isa_compatibility"] = "WARN"
    checks["pending_jobs"] = "PASS" if pending_available and pending_total == 0 else "WARN"
    if not pending_available or pending_total:
        reasons.append("Pending workload quota overlap is unknown; reconcile before submitting")
    compat = _compatibility()
    result["sdk_batch_compatibility"] = compat
    checks["sdk_batch_compatibility"] = "PASS" if compat["available"] else "FAIL"
    if not compat["available"]:
        reasons.append("Installed SDK lacks a compatible Batch/Estimator mode API")

    if len(instances) > 1:
        result["usage_scope"] = "per_instance; account-wide cumulative usage is ambiguous"
        result["account_wide_usage_verified"] = False
        reasons.append("Multiple accessible instances exist; service.usage() is per active instance and cannot be treated as account-wide")
    elif len(instances) == 1 and instance_listing_available:
        result["usage_scope"] = "single_accessible_instance (service usage is instance-scoped)"
        result["account_wide_usage_verified"] = True
    else:
        result["usage_scope"] = "instance-scoped; account-wide scope unverified"
        result["account_wide_usage_verified"] = False
        reasons.append("Accessible instance enumeration failed; account-wide quota scope is unverified")
    result["checks"] = checks
    failed = any(value == "FAIL" for value in checks.values())
    warned = any(value == "WARN" for value in checks.values()) or not result["account_wide_usage_verified"]
    result["status"] = "FAIL" if failed else ("WARN" if warned else "PASS")
    result["submission_eligible"] = result["status"] == "PASS" and result["account_wide_usage_verified"]
    result["reasons"] = reasons
    try: remaining=report_quota_fields(result)["remaining_seconds"]
    except (ValueError,TypeError,KeyError): remaining=None
    thresholds=provider_quota_thresholds(remaining)
    result.update(thresholds, remaining_provider_quota_seconds=remaining,
        preflight_eligible=result["submission_eligible"],
        batch_submission_eligible=result["submission_eligible"] and thresholds["batch_quota_threshold_eligible"],
        recovery_submission_eligible=result["submission_eligible"] and thresholds["recovery_quota_threshold_eligible"],
        rejection_reason="; ".join(reasons) or (None if thresholds["recovery_quota_threshold_eligible"] else "Provider quota below both submission thresholds"))
    return result


def preflight_account(credentials, *, service_factory=None, backend_name="ibm_fez",
                      frozen_validator=None, allowed_instance_refs=None):
    """Read-only suitability; workload flags do not grant live authorization."""
    result=_preflight_account(credentials,service_factory=service_factory,backend_name=backend_name,
        frozen_validator=frozen_validator,allowed_instance_refs=allowed_instance_refs)
    result["preflight_eligible"]=result.get("submission_eligible",False)
    for key,value in provider_quota_thresholds(result.get("remaining_provider_quota_seconds")).items():
        result[key]=value
    common="; ".join(result.get("reasons",[]))
    result["batch_rejection_reason"]=common or (None if result["batch_quota_threshold_eligible"] else
        f"New Batch requires at least {MIN_BATCH_QUOTA_SECONDS:g}s verified provider quota")
    result["recovery_rejection_reason"]=common or (None if result["recovery_quota_threshold_eligible"] else
        f"Individual PUB recovery requires at least {MIN_RECOVERY_QUOTA_SECONDS:g}s verified provider quota")
    if result.get("reasons"):
        result["rejection_reason"]="; ".join(result["reasons"])
    return result


def frozen_bundle_summary(root):
    """Read-only revalidation of the locked run_02 circuit/observables and hashes."""
    from experiment_manifest import build_manifest
    from vqe_adapter import verify_frozen_inputs
    manifest = build_manifest(root, replicate_blocks=1, shots=4096)
    qc, observables, theta, physical, _, _ = verify_frozen_inputs(root)
    if tuple(physical) != tuple(range(140, 147)) or len(observables) != 7 or len(theta) != 21:
        raise RuntimeError("Frozen circuit layout, observable count, or theta count differs from validated run_02")
    if getattr(qc, "parameters", None):
        raise RuntimeError("Frozen circuit contains unbound parameters")
    return {"source_run": manifest.source_run, "backend": manifest.backend,
            "input_sha256": manifest.input_hashes, "physical_qubits": list(physical),
            "theta_count": len(theta), "observable_count": len(observables),
            "unbound_parameter_count": len(qc.parameters), "transpilation_performed": False,
            "isa_instructions": [{"operation": item.operation.name,
                                  "qubits": [qc.find_bit(q).index for q in item.qubits]}
                                 for item in qc.data]}


def preflight_all(labels=ACCOUNT_LABELS, *, auth_mode="environment", environ=None,
                  credential_loader=load_authorized_accounts, profile_loader=load_saved_account_profiles,
                  service_factory=None, root=None, allowed_instance_refs=None):
    if auth_mode not in ("environment", "saved-profile"):
        raise ValueError("auth_mode must be environment or saved-profile")
    loader = profile_loader if auth_mode == "saved-profile" else credential_loader
    if service_factory is None:
        service_factory = make_saved_profile_service if auth_mode == "saved-profile" else make_runtime_service
    reports = {}
    frozen_error = None
    try:
        frozen = frozen_bundle_summary(root)
    except Exception as exc:
        frozen = None
        frozen_error = exc
    for label in labels:
        try:
            credentials = loader((label,)) if auth_mode == "saved-profile" else loader((label,), environ)
            credential = credentials[label]
        except Exception as exc:
            reports[label] = {"schema_version": 1, "account_label": label, "status": "FAIL",
                              "submission": False, "submission_eligible": False,
                              "checks": {"credentials": "FAIL"},
                              "reasons": [f"Local account configuration failed ({type(exc).__name__})"],
                              "checked_utc": _stamp()}
            continue
        reports[label] = preflight_account(credential, service_factory=service_factory,
            allowed_instance_refs=None if allowed_instance_refs is None else allowed_instance_refs.get(label,()),
            frozen_validator=(lambda frozen=frozen, frozen_error=frozen_error: frozen if frozen is not None else (_raise(frozen_error))))
    return reports


def _raise(exc):
    raise exc
