"""Cumulative, account-aware quota selection. Unknown usage fails closed."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

import math
from datetime import datetime, timezone
from typing import Mapping
from job_registry import ACCOUNT_LABELS, JobRegistry
from final_configuration import PREFERRED_NEW_BATCH_ACCOUNTS, provider_quota_thresholds, quota_eligibility

NOMINAL_ACCOUNT_BUDGET_SECONDS = 600.0


def provider_quota_fields(response):
    """Normalize provider-period usage; never substitute Registry or nominal usage.

    Require explicit consumption: the SDK can synthesize remaining with a zero
    consumption default when the provider omits that field.
    """
    if not isinstance(response, dict):
        raise ValueError("Provider quota response is not an object")
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    consumed = response.get('usage_consumed_seconds')
    limit = response.get('usage_limit_seconds')
    if limit is None:
        limit = response.get('usage_allocation_seconds')
    if not number(consumed) or not number(limit):
        raise ValueError('Provider consumption or quota limit is missing or invalid')
    calculated = max(0.0, float(limit)-float(consumed))
    remaining = response.get('usage_remaining_seconds', calculated)
    if not number(remaining) or remaining > calculated + 1e-6:
        raise ValueError('Provider remaining quota is invalid or inconsistent')
    # A plan-wide exhaustion flag takes precedence over an instance allocation.
    if response.get('usage_limit_reached') is True:
        remaining = 0.0
    return {'consumed_seconds': float(consumed), 'limit_seconds': float(limit),
            'remaining_seconds': float(remaining)}


def report_quota_fields(report, *, now=None):
    """Accept only fresh, unambiguous, single-instance provider evidence."""
    try:
        checked = datetime.fromisoformat(report.get('usage_checked_utc', report['checked_utc']).replace('Z', '+00:00'))
        age = ((now or datetime.now(timezone.utc))-checked).total_seconds()
        if not 0 <= age <= 600:
            raise ValueError('Provider quota is stale or future-dated')
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('Provider quota timestamp is missing, invalid or stale') from exc
    rows = report.get('instances')
    if (report.get('account_wide_usage_verified') is not True or
            not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or
            rows[0].get('instance_ref') != report.get('configured_instance_ref')):
        raise ValueError('Provider quota scope is ambiguous')
    usage = rows[0].get('usage')
    if not isinstance(usage, dict):
        raise ValueError('Provider quota fields are missing')
    return provider_quota_fields({'usage_'+key: usage.get(key)
        for key in ('consumed_seconds', 'limit_seconds', 'remaining_seconds')})


def provider_reservations(registry, *, connection=None):
    """Read all local workloads, including those outside the current campaign.

    Only job-ID-free RESERVED work is provably not yet charged by IBM. In-flight
    or unreconciled work is excluded instead of subtracting a possibly charged
    reservation twice. Completed usage is already in the provider's period total.
    """
    result = {label: {'reserved_seconds': 0.0, 'ambiguous': False} for label in ACCOUNT_LABELS}
    from contextlib import nullcontext
    with (registry._connection() if connection is None else nullcontext(connection)) as db:
        originals = db.execute('SELECT account_label,submission_state AS state,job_id,quota_reserved_seconds,qpu_usage_seconds FROM jobs').fetchall()
        replacements = db.execute('SELECT account_label,state,job_id,quota_reserved_seconds,qpu_usage_seconds FROM pub_replacement_jobs').fetchall()
    for row in list(originals)+list(replacements):
        item = result[row['account_label']]
        state = row['state']
        if state == 'RESERVED' and not row['job_id'] and row['qpu_usage_seconds'] is None:
            item['reserved_seconds'] += float(row['quota_reserved_seconds'] or 0)
        elif state in ('RESERVED','SUBMITTING','SUBMITTED','UNKNOWN','DONE'):
            item['ambiguous'] = True
        elif row['job_id'] and row['qpu_usage_seconds'] is None:
            item['ambiguous'] = True
    return result


def select_account(registry: JobRegistry, usage_seconds: Mapping[str, float], *,
                   reservation_seconds: float, safety_margin_seconds: float = 60.0,
                   budget_seconds: float = NOMINAL_ACCOUNT_BUDGET_SECONDS,
                   eligible_accounts: tuple[str, ...] = ACCOUNT_LABELS) -> str:
    """Deprecated scalar-usage interface: consumption alone cannot prove quota."""
    raise RuntimeError("Provider remaining quota, freshness and scope are required; use select_highest_available_account")


def actual_usage_seconds(service) -> float:
    """Read verified cumulative usage from the IBM service response; fail closed if unknown."""
    response = service.usage()
    return actual_usage_from_response(response)


def actual_usage_from_response(response: dict) -> float:
    """Convert an already-fetched Runtime usage response to cumulative consumed seconds."""
    if not isinstance(response, dict):
        raise RuntimeError("IBM usage() returned an unsupported response")
    remaining = response.get("usage_remaining_seconds")
    limit = response.get("usage_limit_seconds")
    consumed = response.get("usage_consumed_seconds")
    if consumed is not None:
        value = float(consumed)
    elif remaining is not None and limit is not None:
        value = float(limit) - float(remaining)
    else:
        raise RuntimeError("IBM usage response lacks cumulative seconds")
    if not math.isfinite(value) or value < 0:
        raise RuntimeError("IBM reported invalid cumulative QPU usage")
    return value


def reconcile_job_usage(registry: JobRegistry, row_id: int, job) -> float | None:
    """Record final IBM QPU lock-time usage when the Runtime job reports it."""
    metrics = job.metrics() if callable(getattr(job, "metrics", None)) else {}
    seconds = qpu_seconds_from_metrics(metrics)
    if seconds is not None:
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise RuntimeError("Runtime job reported invalid QPU usage")
    status = str(job.status()).upper().split(".")[-1]
    metadata = {"metrics": metrics, "timestamps": metrics.get("timestamps") if isinstance(metrics, dict) else None,
                "creation_date": str(getattr(job,"creation_date",None))}
    state = "COMPLETED" if status in ("DONE","COMPLETED") else ("FAILED" if status in ("ERROR","FAILED") else ("CANCELLED" if status=="CANCELLED" else "SUBMITTED"))
    registry.transition(row_id,state,qpu_usage_seconds=seconds,metadata=metadata,event_type="ACTUAL_USAGE_RECONCILED" if seconds is not None else "USAGE_UNAVAILABLE",details={"qpu_usage_seconds":seconds})
    return seconds


def qpu_seconds_from_metrics(metrics) -> float | None:
    if not isinstance(metrics,dict): return None
    usage=metrics.get("usage",metrics.get("qpu_usage_s",metrics.get("usage_estimation")))
    value=None
    if isinstance(usage,dict):
        for key in ("quantum_seconds","qpu_seconds","qpu_usage_s","usage_seconds","qpu_charge_time_seconds"):
            if isinstance(usage.get(key),(int,float)):
                value=usage[key];break
        if value is None and isinstance(usage.get("details"),list):
            for detail in usage["details"]:
                metric=detail.get("metric",{}) if isinstance(detail,dict) else {}
                if metric.get("id")=="caffe3ca-729c-5750-8777-df63bbf8bfd9":
                    value=metric.get("value");break
    elif isinstance(usage,(int,float)):
        value=usage
    if value is None: return None
    value=float(value)
    if not math.isfinite(value) or value<0: raise RuntimeError("Runtime reported invalid QPU usage")
    return value


def available_quota_seconds(*, provider_remaining_seconds: float, outstanding_reservations_seconds: float = 0.0,
                            local_budget_remaining_seconds: float | None = None) -> float:
    """Return conservative spendable seconds, with reservations deducted once."""
    values=(provider_remaining_seconds,outstanding_reservations_seconds)
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<0 for x in values):
        raise ValueError("Quota and reservations must be finite nonnegative seconds")
    if local_budget_remaining_seconds is not None and (isinstance(local_budget_remaining_seconds,bool) or
            not isinstance(local_budget_remaining_seconds,(int,float)) or
            not math.isfinite(local_budget_remaining_seconds) or local_budget_remaining_seconds<0):
        raise ValueError("Local budget remaining must be finite nonnegative seconds")
    available=provider_remaining_seconds-outstanding_reservations_seconds
    if local_budget_remaining_seconds is not None:
        available=min(available,local_budget_remaining_seconds)
    return max(0.0,float(available))


def campaign_budget_monitor(authorization, usage, required_seconds):
    """Historical campaign allocation is telemetry, never submission authority."""
    remaining=(float(authorization["total_runtime_budget_seconds"])
        -float(usage["campaign_consumed_seconds"])-float(usage["campaign_reserved_seconds"]))
    return {"campaign_budget_mode":"monitoring_only",
            "internal_campaign_remaining_seconds":remaining,
            "warnings":(["Local campaign budget exhausted or insufficient for this workload; monitoring only"]
                        if remaining < required_seconds or remaining <= 0 else [])}


def log_campaign_budget_monitor(monitor):
    """Informational telemetry only; never emit a quota failure for a global balance.

    Preserve the historical warning field in JSON reports for compatibility.
    Default logging sends WARNING to stderr, which PowerShell can surface as a
    native-command error even though Python's eligibility decision succeeded.
    """
    import logging
    for message in monitor.get('warnings',()):
        logging.getLogger(__name__).info('%s',message)


def account_quota_report(reports, *, runtime_estimate, usage, authorization,
                         workload_type="original_batch", expected_hashes=None, excluded=(), original_account=None):
    """Offline account-level decision table; provider and internal limits stay separate."""
    from runtime_estimator import workload_quota_decision
    margin=float(authorization["safety_margin_seconds"])
    monitor=campaign_budget_monitor(authorization,usage,float(runtime_estimate.stress_block_seconds)+margin)
    decisions={}
    for label in authorization["allowed_accounts"]:
        local=usage["per_account"].get(label, {"consumed_seconds":0, "reserved_seconds":0})
        headroom=max(0.0, float(authorization["account_runtime_budget_seconds"][label])
            -float(local["consumed_seconds"])-float(local["reserved_seconds"]))
        item={"provider_remaining_seconds":None, "provider_available_seconds":None,
              "internal_account_remaining_seconds":headroom,
              "internal_campaign_remaining_seconds":monitor["internal_campaign_remaining_seconds"],
              "estimated_batch_seconds":getattr(runtime_estimate,"stress_block_seconds",None),
              "safety_margin_seconds":margin, "eligible":False, "reason":None,
              "batch_submission_eligible":False,"recovery_submission_eligible":False,
              "remaining_provider_quota_seconds":None,
              "estimated_submission_runtime_seconds":getattr(runtime_estimate,"stress_block_seconds",None),
              "required_runtime_with_margin_seconds":None,"account_selection_priority":None,
              "rejection_reason":None}
        item.update(monitor)
        decisions[label]=item
        report=reports.get(label) or {}
        item['retryable']=False
        try:
            if label in excluded: raise ValueError("account excluded after a pre-submission gate failure")
            fields=report_quota_fields(report)
            item["provider_remaining_seconds"]=fields["remaining_seconds"]
            item["remaining_provider_quota_seconds"]=fields["remaining_seconds"]
            item.update(provider_quota_thresholds(fields["remaining_seconds"]))
            if label in excluded: raise ValueError("account excluded for this workload")
            if report.get("account_label")!=label: raise ValueError("preflight account mismatch")
            if report.get("configured_instance_ref") not in authorization["allowed_instance_refs"].get(label,()):
                raise ValueError("instance not authorized")
            if expected_hashes is not None and (report.get("frozen_input_validation") or {}).get("input_sha256")!=expected_hashes:
                raise ValueError("frozen inputs mismatch")
            if report.get("status")!="PASS" or report.get("submission_eligible") is not True:
                raise ValueError("account preflight not PASS")
            if report.get("pending_jobs_count") != 0 or isinstance(report.get("pending_jobs_count"),bool):
                raise ValueError("provider pending jobs are nonzero or unavailable; quota overlap is ambiguous")
            reservation=usage.get("provider_reservations",{}).get(label)
            if reservation is None:
                raise ValueError("provider reservation reconciliation is missing")
            if reservation["ambiguous"]:
                raise ValueError("unreconciled jobs: provider reservation overlap is ambiguous")
            available=available_quota_seconds(provider_remaining_seconds=fields["remaining_seconds"],
                outstanding_reservations_seconds=reservation["reserved_seconds"])
            item["provider_available_seconds"]=available
            effective=min(available,headroom)
            gate=workload_quota_decision(runtime_estimate,remaining_seconds=effective,
                workload_type=workload_type,safety_margin_seconds=margin,
                provider_remaining_seconds=fields["remaining_seconds"])
            item.update(effective_remaining_seconds=effective, quota_gate=gate,
                        runtime_warnings=gate["warnings"],
                        required_runtime_with_margin_seconds=gate["required_runtime_with_margin_seconds"])
            if not gate["eligible"]:
                required=gate.get("required_quota_seconds")
                reasons=list(gate["reasons"])
                if required is not None:
                    if available<required: reasons.append("provider available quota insufficient")
                    if headroom<required: reasons.append("internal account budget insufficient")
                raise ValueError("; ".join(reasons))
            item["eligible"]=True
            item["batch_submission_eligible"]=workload_type=="original_batch"
            item["recovery_submission_eligible"]=workload_type in ("targeted_replacement","missing_treatment","combined_recovery")
        except (ValueError,TypeError,KeyError) as exc:
            item["reason"]=("Saved-profile authentication failed" if
                (report.get("checks") or {}).get("authentication")=="FAIL" else str(exc))
        item["rejection_reason"]=item["reason"]
        # Only scheduling changes: every retry still needs a fresh full gate.
        # Local authorization exhaustion and ambiguous reservations never heal
        # merely by waiting for the provider's quota window to refresh.
        required=quota_eligibility(headroom,workload_type=workload_type)["required_quota_seconds"]
        reservation=usage.get('provider_reservations',{}).get(label,{})
        if (label not in excluded and headroom>=required and not reservation.get('ambiguous',True)):
            read_retry=report.get('retryable_read_failure') is True and not report.get('nonretryable_read_failure')
            gate=item.get('quota_gate') or {}
            quota_retry=(bool(gate) and not gate['eligible'] and
                report.get('status')=='PASS' and report.get('submission_eligible') is True)
            checks=report.get('checks') or {}
            exhausted=(checks.get('provider_quota')=='WARN' and
                report.get('account_wide_usage_verified') is True and
                all(value=='PASS' for key,value in checks.items() if key!='provider_quota'))
            item['retryable']=bool(read_retry or quota_retry or exhausted)
    preferred=([original_account] if workload_type in ("targeted_replacement","missing_treatment","combined_recovery") and original_account else [])+list(PREFERRED_NEW_BATCH_ACCOUNTS)
    ordered=list(dict.fromkeys(label for label in preferred if label in decisions))
    ordered+=sorted((label for label in decisions if label not in ordered),
                    key=lambda label: (-(decisions[label]["provider_available_seconds"]
                        if decisions[label]["provider_available_seconds"] is not None
                        else decisions[label]["provider_remaining_seconds"] or 0),label))
    for priority,label in enumerate(ordered,1): decisions[label]["account_selection_priority"]=priority
    return decisions


class AccountUnavailable(RuntimeError):
    """Account rejected before submission intent; safe to evaluate another account."""


class AccountSelectionUnavailable(RuntimeError):
    def __init__(self, decisions):
        self.decisions=decisions
        self.retryable=any(item.get('retryable') is True for item in decisions.values())
        super().__init__("QUOTA_OR_BUDGET: all authorized accounts unsuitable: "+
                         "; ".join(f"{label}: {item['reason']}" for label,item in decisions.items()))


def select_highest_available_account(reports, *, runtime_estimate, usage, authorization,
                                     workload_type="original_batch", expected_hashes=None, excluded=(), original_account=None):
    decisions=account_quota_report(reports,runtime_estimate=runtime_estimate,usage=usage,
        authorization=authorization,workload_type=workload_type,expected_hashes=expected_hashes,excluded=excluded,
        original_account=original_account)
    candidates=[label for label,item in decisions.items() if item["eligible"]]
    if not candidates:
        raise AccountSelectionUnavailable(decisions)
    label=min(candidates,key=lambda label:decisions[label]["account_selection_priority"])
    log_campaign_budget_monitor(decisions[label])
    return {"account_label":label, **decisions[label], "account_reports":decisions,
        "rejected_accounts":{key:value["reason"] for key,value in decisions.items() if not value["eligible"]}}


def preflight_and_select_account(root, target, *, runtime_estimate, usage, authorization,
                                 workload_type="original_batch", original_account=None,
                                 excluded=(), service_factory=None, attempts=3):
    """Fresh read-only selection cycle. Never claims, reserves or submits jobs.

    Retry only reads and service discovery. Submission is the caller's separate
    boundary; rejected accounts never cause an uncertain submission to be retried.
    """
    from account_config import load_saved_account_profiles, make_saved_profile_service
    from read_only_preflight import preflight_account, frozen_bundle_summary
    from worker_runtime import RetryingReadOnlyService
    from worker_runtime import transient_read_error
    import time
    if isinstance(attempts,bool) or not isinstance(attempts,int) or not 1<=attempts<=5:
        raise ValueError("Read-only attempts must be between 1 and 5")
    frozen=frozen_bundle_summary(root)
    if frozen["input_sha256"] != target.input_hashes:
        raise RuntimeError("Frozen input hashes differ from target")
    if not authorization["allowed_accounts"]:
        raise AccountSelectionUnavailable({})
    preferred=([original_account] if workload_type in ("targeted_replacement","missing_treatment","combined_recovery") and original_account else [])+list(PREFERRED_NEW_BATCH_ACCOUNTS)
    labels=list(dict.fromkeys(label for label in preferred if label in authorization["allowed_accounts"]))
    labels += [label for label in authorization["allowed_accounts"] if label not in labels]
    profiles=load_saved_account_profiles(tuple(labels))
    reports={};services={}
    for label,profile in profiles.items():
        if label in excluded:
            reports[label]={"status":"FAIL","submission_eligible":False,
                            "reasons":["Account excluded after a pre-submission gate failure"]}
            continue
        for attempt in range(attempts):
            try:
                service=service_factory(label) if service_factory else make_saved_profile_service(profile)
                service=RetryingReadOnlyService(service,attempts=attempts)
                report=preflight_account(profile,service_factory=lambda _,svc=service:svc,
                    allowed_instance_refs=authorization["allowed_instance_refs"].get(label,()),
                    frozen_validator=lambda:frozen)
                services[label]=service
            except Exception as exc:
                report={"account_label":label,"status":"FAIL","submission_eligible":False,
                        "checks":{"authentication":"FAIL"},
                        "retryable_read_failure":transient_read_error(exc),
                        "reasons":["Read-only account discovery failed: "+type(exc).__name__]}
            reports[label]=report
            # Valid but exhausted/unsuitable accounts need failover, not retries.
            if report.get("status")=="PASS":
                break
            if report.get('retryable_read_failure') is not True:break
            if attempt<attempts-1:time.sleep(min(2**attempt,4))
            checks=report.get("checks",{})
            if (report.get("account_wide_usage_verified") is True and
                    all(checks.get(key)=="PASS" for key in ("authentication","configured_instance",
                        "backend_ibm_fez","backend_operational","isa_compatibility")) and
                    (checks.get("provider_quota")=="WARN" or report.get("pending_jobs_count",0))):
                break
    chosen=select_highest_available_account(reports,runtime_estimate=runtime_estimate,usage=usage,
        authorization=authorization,workload_type=workload_type,expected_hashes=target.input_hashes,
        excluded=excluded,original_account=original_account)
    label=chosen["account_label"]
    reports[label]["account_selection_priority"]=chosen["account_selection_priority"]
    return chosen,services[label],reports[label]
