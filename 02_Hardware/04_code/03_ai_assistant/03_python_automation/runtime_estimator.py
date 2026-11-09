"""Read-only calibration from integrity-checked historical IBM QPU usage records.

The estimator deliberately does not fit a PUB-count scaling law. It reports the
target-n observations, the per-treatment observed envelope, and refuses quota
eligibility until each treatment has enough exact-n repetitions.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


from final_configuration import MIN_BATCH_QUOTA_SECONDS, MIN_RECOVERY_QUOTA_SECONDS, provider_quota_thresholds
from dataclasses import dataclass
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
from typing import Any, Mapping


@dataclass(frozen=True)
class UsageSample:
    treatment_id: str
    n: int
    qpu_seconds: float
    job_id: str
    provenance: str
    record_path: str


@dataclass(frozen=True)
class RuntimeEstimate:
    target_n: int
    per_treatment: dict[str, dict[str, Any]]
    observed_target_block_seconds: float | None
    stress_block_seconds: float | None
    safety_factor: float
    minimum_exact_n_samples: int
    defensible: bool
    blockers: tuple[str, ...]
    historical_preliminary_sample_count: int = 0
    scope: str = "full_batch"
    operator_approved_provisional: bool = False


@dataclass(frozen=True)
class ReplacementUsageSample:
    """Integrity-checked usage for one isolated replacement Estimator job."""
    treatment_id: str
    pub_count: int
    qpu_seconds: float
    job_id: str
    backend: str
    shots: int
    input_hashes: Mapping[str, str]
    treatment_options_repr: str
    provenance: str
    record_path: str


@dataclass(frozen=True)
class ReplacementRuntimeEstimate:
    """Treatment-specific empirical envelope for exactly one replacement PUB.

    The envelope is a planning bound based on comparable isolated jobs, not a
    provider guarantee. No PUB-count scaling or full-Batch aggregation occurs.
    """
    target_n: int
    treatment_id: str
    pub_count: int
    per_treatment: dict[str, dict[str, Any]]
    observed_target_block_seconds: float | None
    stress_block_seconds: float | None
    safety_factor: float
    minimum_exact_n_samples: int
    defensible: bool
    blockers: tuple[str, ...]
    scope: str = "single_treatment_replacement"
    operator_approved_provisional: bool = False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _provider_usage(raw: Mapping[str, Any], metadata: Mapping[str, Any]) -> tuple[float | None, bool]:
    """Return charge seconds and whether raw IBM usage says the measurement completed."""
    metrics = raw.get("metrics")
    if not isinstance(metrics, dict):
        metrics = metadata.get("metrics_raw")
    if not isinstance(metrics, dict):
        return None, False
    usage = metrics.get("usage")
    if not isinstance(usage, dict):
        return None, False
    value = None
    for field in ("qpu_charge_time_seconds", "quantum_seconds", "qpu_seconds", "qpu_usage_s"):
        candidate = _number(usage.get(field))
        if candidate is not None:
            value = candidate
            break
    raw_value = _number(raw.get("qpu_usage_s"))
    if raw_value is None:
        raw_value = _number(metadata.get("qpu_usage_s"))
    if value is None or raw_value is None or not math.isclose(value, raw_value, rel_tol=0, abs_tol=1e-9):
        return None, usage.get("status") == "complete"
    return value, usage.get("status") == "complete"


def load_verified_samples(records_root: str | Path, *, input_hashes: Mapping[str, str],
                          treatment_flags: Mapping[str, Mapping[str, int]],
                          treatment_options_repr: Mapping[str, str],
                          backend: str = "ibm_fez", shots: int = 4096
                          ) -> tuple[list[UsageSample], list[dict[str, Any]]]:
    """Read legacy qpu_usage_status.csv files and keep only traceable positive usage.

    ``DONE`` API refreshes and ``CACHED`` provider artifacts are distinguished in
    the returned provenance field. Zero, pending, mismatched, or incomplete usage
    rows are excluded with a reason; this function never mutates its inputs.
    """
    root = Path(records_root)
    samples: list[UsageSample] = []
    excluded: list[dict[str, Any]] = []
    for block in sorted(root.glob("n=*_IBM_job_infor_recorded")):
        match = re.fullmatch(r"n=(\d+)_IBM_job_infor_recorded", block.name)
        status_csv = block / "qpu_usage_status.csv"
        if not match or not status_csv.is_file():
            continue
        n = int(match.group(1))
        with status_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            status_rows = list(csv.DictReader(handle))
        for row in status_rows:
            treatment = str(row.get("treatment", ""))
            folder = block / treatment
            submitted = _read_json(folder / "job_submitted.json")
            plan = _read_json(folder / "treatment_plan.json")
            options = _read_json(folder / "mitigation_options_requested.json")
            raw = _read_json(folder / "job_usage_raw.json")
            metadata = _read_json(folder / "job_retrieved_metadata.json")
            job_id = str(row.get("job_id", ""))
            usage = _number(row.get("qpu_usage_s"))
            checks = {
                "known_treatment": treatment in treatment_flags and treatment in treatment_options_repr,
                "job_id_matches": bool(job_id) and job_id == submitted.get("job_id") and
                    raw.get("job_id", job_id) == job_id and metadata.get("job_id", job_id) == job_id,
                "backend_matches": submitted.get("backend") == backend,
                "shots_match": submitted.get("shots_requested_per_pub") == shots and
                    plan.get("shots_per_PUB_requested") == shots,
                "pub_count_matches": submitted.get("repeats") == n and plan.get("repeats") == n,
                "frozen_hashes_match": submitted.get("base_ISA_sha256") == input_hashes.get("circuit.qpy") and
                    submitted.get("theta_sha256") == input_hashes.get("theta.csv") and
                    submitted.get("observables_sha256") == input_hashes.get("observables.json"),
                "treatment_flags_match": plan.get("flags") == treatment_flags.get(treatment),
                "options_match": options.get("repr") == treatment_options_repr.get(treatment),
            }
            measured, complete = _provider_usage(raw, metadata)
            checks["provider_usage_complete_and_matches"] = complete and measured is not None and usage is not None and math.isclose(measured, usage, rel_tol=0, abs_tol=1e-9)
            state = str(row.get("status", ""))
            source = str(row.get("source", ""))
            if state == "DONE" and "IBM metrics/usage API" in source:
                provenance = "fresh_api_recorded"
            elif state == "CACHED" and source in ("job_usage_raw.json", "job_retrieved_metadata.json"):
                provenance = "cached_provider_artifact"
            else:
                provenance = "unverified_source"
            checks["provenance_source_recognized"] = provenance != "unverified_source"
            checks["positive_usage"] = usage is not None and usage > 0
            failed = [name for name, passed in checks.items() if not passed]
            if failed:
                excluded.append({"treatment_id": treatment, "n": n, "job_id": job_id,
                                 "qpu_seconds": usage, "provenance": provenance,
                                 "failed_checks": failed, "record_path": str(block)})
                continue
            samples.append(UsageSample(treatment, n, float(usage), job_id, provenance, str(folder)))
    return samples, excluded


def load_verified_calibration_samples(calibration_database: str | Path, *, campaign_id: str | None = None,
                                      input_hashes: Mapping[str, str],
                                      treatment_flags: Mapping[str, Mapping[str, int]],
                                      treatment_options_repr: Mapping[str, str],
                                      backend: str = "ibm_fez", shots: int = 4096,
                                      target_n: int = 5) -> tuple[list[UsageSample], list[dict[str, Any]]]:
    """Load only complete, matched calibration evidence from the isolated registry.

    Historical job records remain preliminary. Calibration samples count toward
    runtime sufficiency only when their full Batch workload, account/instance
    scope, run_02 hashes, treatment options, shots, PUB count and provider usage
    all validate. The returned stress envelope is still an empirical scenario,
    never a guaranteed provider charge ceiling.
    """
    import sqlite3
    path = Path(calibration_database)
    if not path.is_file(): return [], [{"campaign_id": campaign_id, "reason": "calibration registry missing"}]
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        if campaign_id is None:
            campaign_rows=db.execute("SELECT campaign_id,plan_json FROM campaigns ORDER BY campaign_id").fetchall()
        else:
            row=db.execute("SELECT campaign_id,plan_json FROM campaigns WHERE campaign_id=?",(campaign_id,)).fetchone()
            campaign_rows=[row] if row else []
        if not campaign_rows: return [], [{"campaign_id": campaign_id, "reason": "campaign not registered"}]
        plans={row["campaign_id"]:json.loads(row["plan_json"]) for row in campaign_rows}
        query=("SELECT b.status AS batch_status,b.batch_id AS runtime_batch_id,b.account_label,b.instance_ref,j.* "
            "FROM calibration_jobs j JOIN calibration_batches b USING(campaign_id,sequence_no) ")
        if campaign_id is None:
            rows=db.execute(query+"ORDER BY j.campaign_id,j.sequence_no,j.treatment_id").fetchall()
        else:
            rows=db.execute(query+"WHERE j.campaign_id=? ORDER BY j.sequence_no,j.treatment_id",(campaign_id,)).fetchall()
    samples=[]; excluded=[]; seen_batches=set()
    for row in rows:
        campaign=row["campaign_id"]
        plan=plans[campaign]
        try: evidence=json.loads(row["evidence_json"] or "{}")
        except json.JSONDecodeError: evidence={}
        treatment=row["treatment_id"]
        expected=next((item for item in plan.get("treatments",[]) if item.get("treatment_id")==treatment),{})
        checks={
            "campaign_identity":evidence.get("campaign_id")==campaign,
            "campaign_complete":row["batch_status"]=="VALIDATED",
            "batch_identity":bool(row["runtime_batch_id"]) and evidence.get("calibration_batch_id")==row["runtime_batch_id"],
            "job_complete":row["status"]=="VALIDATED",
            "account_scope_verified":bool(row["account_label"]) and bool(row["instance_ref"])
                and evidence.get("account_label")==row["account_label"]
                and evidence.get("instance_ref")==row["instance_ref"],
            "backend_matches":evidence.get("backend")==backend and plan.get("backend")==backend,
            "source_run_matches":evidence.get("source_run")=="run_02" and plan.get("source_run")=="run_02",
            "frozen_hashes_match":evidence.get("input_hashes")==dict(input_hashes)==plan.get("input_hashes"),
            "layout_matches":evidence.get("physical_qubits")==list(range(140,147)),
            "circuit_bound":evidence.get("circuit_parameter_count")==0,
            "theta_and_observable_counts":evidence.get("theta_count")==21 and evidence.get("observable_count")==7,
            "shots_match":evidence.get("shots_per_pub")==shots,
            "pub_count_matches":evidence.get("pub_count")==target_n and plan.get("chunk_n")==target_n,
            "expectations_validated":evidence.get("expectation_count_per_pub")==7 and evidence.get("pub_validation")=="PASS",
            "treatment_flags_match":evidence.get("treatment_flags")==expected.get("flags")
                and treatment in treatment_flags and evidence.get("treatment_flags")==treatment_flags.get(treatment),
            "options_match":evidence.get("treatment_options_repr")==expected.get("options_repr")
                and treatment_options_repr.get(treatment)==expected.get("options_repr"),
            "matching_batch_workload":evidence.get("batch_workload")=="one_batch_eight_jobs_M0_M7",
            "provider_usage_verified":evidence.get("usage_verified") is True
                and evidence.get("usage_source")=="provider_job_metrics",
            "job_id_matches":bool(row["job_id"]) and evidence.get("job_id")==row["job_id"],
            "positive_usage":isinstance(row["qpu_usage_seconds"],(int,float)) and row["qpu_usage_seconds"]>0
                and evidence.get("qpu_usage_seconds")==row["qpu_usage_seconds"],
            "not_synthetic":evidence.get("synthetic") is False,
        }
        failed=[name for name,passed in checks.items() if not passed]
        if failed:
            excluded.append({"campaign_id":campaign,"treatment_id":treatment,
                "sequence_no":row["sequence_no"],"job_id":row["job_id"],"failed_checks":failed})
            continue
        batch_key=(campaign,treatment,row["runtime_batch_id"])
        if batch_key in seen_batches:
            excluded.append({"campaign_id":campaign,"treatment_id":treatment,
                "sequence_no":row["sequence_no"],"job_id":row["job_id"],
                "failed_checks":["duplicate_calibration_batch_sample"]})
            continue
        seen_batches.add(batch_key)
        samples.append(UsageSample(treatment_id=treatment,n=int(target_n),
            qpu_seconds=float(row["qpu_usage_seconds"]),job_id=str(row["job_id"]),
            provenance="validated_calibration_provider_metrics",
            record_path=f"{path}::{campaign}/{row['sequence_no']}/{treatment}"))
    return samples,excluded


def estimate_runtime(samples: list[UsageSample], *, treatments: tuple[str, ...], target_n: int,
                     safety_factor: float = 1.5, minimum_exact_n_samples: int = 3) -> RuntimeEstimate:
    """Produce a non-linear empirical stress envelope and an evidence sufficiency gate.

    The stress envelope sums each treatment's maximum verified observation at any
    recorded PUB count, multiplied by ``safety_factor``. It is a conservative
    planning scenario, not a statistically calibrated prediction. Defensibility
    requires at least ``minimum_exact_n_samples`` observations per treatment at
    exactly ``target_n``; no interpolation or linear scaling is performed.
    """
    if isinstance(target_n, bool) or not isinstance(target_n, int) or target_n < 1:
        raise ValueError("target_n must be a positive integer")
    if isinstance(minimum_exact_n_samples, bool) or not isinstance(minimum_exact_n_samples, int) or minimum_exact_n_samples < 2:
        raise ValueError("minimum_exact_n_samples must be at least 2")
    if not math.isfinite(float(safety_factor)) or safety_factor < 1:
        raise ValueError("safety_factor must be finite and at least 1")
    per_treatment: dict[str, dict[str, Any]] = {}
    observed_target_total = 0.0
    all_target_present = True
    stress_total = 0.0
    all_stress_present = True
    blockers = []
    for treatment in treatments:
        group = [item for item in samples if item.treatment_id == treatment]
        exact = [item.qpu_seconds for item in group if item.n == target_n]
        values = [item.qpu_seconds for item in group]
        max_observed = max(values) if values else None
        target_median = median(exact) if exact else None
        if exact:
            observed_target_total += target_median
        else:
            all_target_present = False
        if max_observed is not None:
            stress_upper = max_observed * safety_factor
            stress_total += stress_upper
        else:
            stress_upper = None
            all_stress_present = False
        if len(exact) < minimum_exact_n_samples:
            blockers.append(f"{treatment}: {len(exact)} verified samples at n={target_n}; need {minimum_exact_n_samples}")
        # Diagnostic only: this fit quantifies observed trend and a per-job
        # intercept. It is never used to predict target_n or the stress envelope.
        descriptive_fit = None
        if len(group) >= 3 and len({item.n for item in group}) >= 3:
            xs = [float(item.n) for item in group]
            ys = [item.qpu_seconds for item in group]
            xbar, ybar = sum(xs) / len(xs), sum(ys) / len(ys)
            denominator = sum((x - xbar) ** 2 for x in xs)
            if denominator > 0:
                slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / denominator
                intercept = ybar - slope * xbar
                residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
                descriptive_fit = {
                    "seconds_per_pub_slope": slope,
                    "per_job_intercept_seconds": intercept,
                    "residual_rmse_seconds": math.sqrt(sum(r * r for r in residuals) / len(residuals)),
                    "residual_range_seconds": max(residuals) - min(residuals),
                    "used_for_prediction": False,
                }
        per_treatment[treatment] = {
            "exact_target_n_samples": len(exact),
            "target_n_median_seconds": target_median,
            "all_pub_counts_sample_count": len(group),
            "observed_min_seconds": min(values) if values else None,
            "observed_max_seconds": max_observed,
            "observed_pub_counts": sorted({item.n for item in group}),
            "descriptive_range_seconds_across_pub_counts": (max(values) - min(values)) if values else None,
            "descriptive_linear_diagnostic": descriptive_fit,
            "stress_envelope_seconds": stress_upper,
        }
    return RuntimeEstimate(
        target_n=target_n,
        per_treatment=per_treatment,
        observed_target_block_seconds=observed_target_total if all_target_present else None,
        stress_block_seconds=stress_total if all_stress_present else None,
        safety_factor=float(safety_factor),
        minimum_exact_n_samples=minimum_exact_n_samples,
        defensible=not blockers,
        blockers=tuple(blockers),
    )


def estimate_replacement_runtime(samples: list[ReplacementUsageSample], *, treatment_id: str,
        backend: str, shots: int, input_hashes: Mapping[str, str], treatment_options_repr: str,
        safety_factor: float = 1.5, minimum_samples: int = 3) -> ReplacementRuntimeEstimate:
    """Estimate one treatment's one-PUB replacement without Batch assumptions.

    Samples must already carry provider-verified usage and exact workload
    provenance. Only exact, single-PUB, treatment/backend/shots/input/options
    matches count. The maximum observed charge times ``safety_factor`` is an
    empirical stress envelope, never a hard provider ceiling.
    """
    if not treatment_id or not backend or shots <= 0:
        raise ValueError("treatment_id, backend and positive shots are required")
    if not math.isfinite(float(safety_factor)) or safety_factor < 1:
        raise ValueError("safety_factor must be finite and at least 1")
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or minimum_samples < 2:
        raise ValueError("minimum_samples must be at least 2")
    matched = [s for s in samples if s.treatment_id == treatment_id and s.pub_count == 1
        and s.backend == backend and s.shots == shots and dict(s.input_hashes) == dict(input_hashes)
        and s.treatment_options_repr == treatment_options_repr and s.qpu_seconds > 0
        and math.isfinite(float(s.qpu_seconds)) and bool(s.job_id)
        and s.provenance in {"verified_replacement_provider_metrics", "fresh_api_recorded"}]
    values = [float(s.qpu_seconds) for s in matched]
    blockers = () if len(values) >= minimum_samples else (
        f"{treatment_id}: {len(values)} verified exact one-PUB replacement usage samples; need {minimum_samples}",
    )
    observed = max(values) if values else None
    envelope = observed * safety_factor if observed is not None else None
    return ReplacementRuntimeEstimate(target_n=1, treatment_id=treatment_id, pub_count=1,
        per_treatment={treatment_id: {"exact_one_pub_samples": len(values),
            "observed_max_seconds": observed, "stress_envelope_seconds": envelope,
            "sample_job_ids": [s.job_id for s in matched]}},
        observed_target_block_seconds=observed, stress_block_seconds=envelope,
        safety_factor=float(safety_factor), minimum_exact_n_samples=minimum_samples,
        defensible=not blockers, blockers=blockers)


def load_verified_replacement_samples(rows: list[Mapping[str, Any]], *, treatment_id: str,
        backend: str, shots: int, input_hashes: Mapping[str, str],
        treatment_options_repr: str) -> tuple[list[ReplacementUsageSample], list[dict[str, Any]]]:
    """Verify durable one-PUB replacement archives before using their usage.

    This excludes original multi-PUB calibration jobs, non-provider metrics,
    archives without metrics, mismatched options/inputs and edited archives.
    It is read-only and never contacts IBM.
    """
    samples=[];excluded=[]
    for row in rows:
        if row.get("treatment_id")!=treatment_id:
            continue
        archive_path=row.get("raw_archive_path");expected_sha=row.get("raw_archive_sha256")
        reasons=[]
        if not archive_path or not expected_sha:
            excluded.append({"replacement_id":row.get("replacement_id"),"reason":"archive reference/hash missing"});continue
        path=Path(str(archive_path))
        try:
            content=path.read_bytes()
            if hashlib.sha256(content).hexdigest()!=expected_sha:
                reasons.append("archive SHA-256 mismatch")
            raw=json.loads(content) if not reasons else {}
        except (OSError,json.JSONDecodeError):
            raw={};reasons.append("archive missing or invalid JSON")
        metrics=raw.get("provider_metrics") if isinstance(raw,dict) else None
        usage=metrics.get("usage") if isinstance(metrics,dict) else None
        metric_seconds=None
        if isinstance(usage,dict) and usage.get("status")=="complete":
            for name in ("qpu_charge_time_seconds","quantum_seconds","qpu_seconds","qpu_usage_s"):
                metric_seconds=_number(usage.get(name))
                if metric_seconds is not None: break
        row_seconds=_number(row.get("qpu_usage_seconds"))
        records=raw.get("records") if isinstance(raw,dict) else None
        checks={
            "provider_job_id":bool(row.get("job_id")) and raw.get("replacement_job_id")==row.get("job_id"),
            "treatment":raw.get("treatment_id")==treatment_id,
            "backend":raw.get("backend")==backend and row.get("backend")==backend,
            "shots":raw.get("shots_per_pub")==shots,
            "one_pub":isinstance(records,list) and len(records)==1,
            "source_run":raw.get("source_run")=="run_02",
            "input_hashes":raw.get("input_hashes")==dict(input_hashes)==row.get("input_hashes"),
            "options":json.dumps(raw.get("runtime_options_requested"),sort_keys=True,separators=(",",":"),default=str)==treatment_options_repr,
            "instance_scope":bool(raw.get("account_label")) and bool(raw.get("instance_ref")),
            "provider_usage":metric_seconds is not None and row_seconds is not None and math.isclose(metric_seconds,row_seconds,rel_tol=0,abs_tol=1e-9),
            "provider_provenance":raw.get("usage_source")=="provider_job_metrics",
        }
        reasons.extend(name for name,passed in checks.items() if not passed)
        if reasons:
            excluded.append({"replacement_id":row.get("replacement_id"),"job_id":row.get("job_id"),"reasons":reasons})
            continue
        samples.append(ReplacementUsageSample(treatment_id=treatment_id,pub_count=1,
            qpu_seconds=float(row_seconds),job_id=str(row["job_id"]),backend=backend,shots=shots,
            input_hashes=dict(input_hashes),treatment_options_repr=treatment_options_repr,
            provenance="verified_replacement_provider_metrics",record_path=str(path)))
    return samples,excluded


def check_submission_quota(remaining_seconds: float | None,
                           estimated_seconds: float | None, *,
                           safety_margin_seconds: float = 10.0,
                           minimum_remaining_seconds: float = MIN_BATCH_QUOTA_SECONDS) -> dict[str, Any]:
    """Compatibility wrapper around the centralized fixed-threshold policy."""
    from final_configuration import quota_eligibility
    if minimum_remaining_seconds not in (MIN_BATCH_QUOTA_SECONDS,MIN_RECOVERY_QUOTA_SECONDS):
        raise ValueError("Quota minimum must be the centralized 150s or 40s policy")
    return quota_eligibility(remaining_seconds,
        workload_type="original_batch" if minimum_remaining_seconds==MIN_BATCH_QUOTA_SECONDS else "targeted_replacement",
        estimated_seconds=estimated_seconds,safety_margin_seconds=safety_margin_seconds)


def workload_quota_decision(estimate, *, remaining_seconds: float | None,
                            workload_type: str,
                            safety_margin_seconds: float = 10.0,
                            provider_remaining_seconds: float | None = None) -> dict[str, Any]:
    """Quota gate for either a full eight-treatment Batch or a single replacement PUB.

    Provider floors are independent of the workload and local spending caps.
    Original Batch: 150 seconds. Individual replacement PUB: 40 seconds.
    Runtime estimates and margins are diagnostic only. No provider access.
    """
    if workload_type not in ("original_batch", "targeted_replacement", "missing_treatment", "combined_recovery"):
        raise ValueError("Unsupported workload_type")
    scope = getattr(estimate, "scope", None)
    expected_scope = {"original_batch":"full_batch","targeted_replacement":"single_treatment_replacement",
                      "missing_treatment":"missing_treatment_recovery", "combined_recovery":"combined_treatment_recovery"}[workload_type]
    from final_configuration import quota_eligibility
    result=quota_eligibility(remaining_seconds,workload_type=workload_type,
        provider_remaining_seconds=provider_remaining_seconds,
        estimated_seconds=getattr(estimate,"stress_block_seconds",None),safety_margin_seconds=safety_margin_seconds)
    result.update(provider_quota_thresholds(result["remaining_provider_quota_seconds"]))
    result["estimated_submission_runtime_seconds"]=getattr(estimate,"stress_block_seconds",None)
    reasons=list(result["reasons"])
    if scope not in (expected_scope, None if workload_type == "original_batch" else expected_scope):
        reasons.append(f"estimate scope must be {expected_scope}")
    if estimate is None or not (getattr(estimate, "defensible", False) or getattr(estimate, "operator_approved_provisional", False)):
        result["warnings"].append("workload estimate has not passed evidence validation; diagnostic only")
        result["warnings"].extend(getattr(estimate, "blockers", ()))
    if workload_type == "targeted_replacement" and (getattr(estimate, "pub_count", None) != 1 or not getattr(estimate, "treatment_id", None)):
        reasons.append("replacement estimate must describe exactly one PUB and one treatment")
    if workload_type == "missing_treatment" and (getattr(estimate,"pub_count",None)!=5 or
            getattr(estimate,"treatment_id",None)!="M7_ALL"):
        reasons.append("missing-treatment recovery requires verified exact-five-PUB M7 estimate")
    if workload_type == "combined_recovery":
        names=getattr(estimate,"treatment_ids",())
        counts=getattr(estimate,'pub_counts',{name:5 for name in names})
        if (not names or len(set(names))!=len(names) or set(names)-{"M3_ZNE","M7_ALL"} or set(counts)!=set(names)
            or any(isinstance(v,bool) or not isinstance(v,int) or not 1<=v<=5 for v in counts.values())
            or ('M7_ALL' in counts and counts['M7_ALL']!=5)
            or getattr(estimate,"pub_count",None)!=sum(counts.values())):
            reasons.append("Combined recovery requires exact unresolved M3 count and five M7 PUBs")
    result.update({"workload_type": workload_type, "reasons": list(dict.fromkeys(reasons))})
    result["eligible"] = not result["reasons"]
    result["batch_submission_eligible"] = result["eligible"] and workload_type == "original_batch"
    result["recovery_submission_eligible"] = result["eligible"] and workload_type in ("targeted_replacement","missing_treatment","combined_recovery")
    result["rejection_reason"] = "; ".join(result["reasons"]) or None
    return result


def quota_decision(estimate: RuntimeEstimate, *, remaining_seconds: float | None,
                   safety_margin_seconds: float = 10.0,
                   minimum_remaining_seconds: float = MIN_BATCH_QUOTA_SECONDS) -> dict[str, Any]:
    """Apply the shared new-Batch floor to the recorded workload estimate.

    Estimate confidence and runtime plus margin remain diagnostic warnings.
    Provider verification, authorization and workload identity are separate gates.
    """
    result = check_submission_quota(
        remaining_seconds, getattr(estimate, "stress_block_seconds", None),
        safety_margin_seconds=safety_margin_seconds,
        minimum_remaining_seconds=minimum_remaining_seconds)
    reasons = list(result["reasons"])
    if not (getattr(estimate, "defensible", False) or getattr(estimate, "operator_approved_provisional", False)):
        result["warnings"].append("workload estimate has not passed evidence validation; diagnostic only")
        result["warnings"].extend(getattr(estimate, "blockers", ()))
    result["reasons"] = list(dict.fromkeys(reasons))
    result["eligible"] = not result["reasons"]
    return result


# Explicit operator-approved provisional ORIGINAL Batch planning policy.
# One n=5 Original Batch had the following registry-reported QPU usage per
# treatment (seconds). These values are NOT independently verified here and
# MUST NOT be presented as two independent calibration observations.
# Authorization: 2026-11-05, factor 1.25, exactly 5 PUBs per treatment.
APPROVED_ORIGINAL_5PUB_SECONDS = {
    "M0_Baseline": 8.0, "M1_DD": 8.0, "M2_TREX": 16.0,
    "M3_ZNE": 18.0, "M4_DD_TREX": 16.0, "M5_DD_ZNE": 18.0,
    "M6_TREX_ZNE": 28.0, "M7_ALL": 28.0,
}
APPROVED_ORIGINAL_SAFETY_FACTOR = 1.25


def historical_batch_estimate(root, manifest, *, require_defensible=True):
    """Use verified exact-n history when sufficient; otherwise approved 5-PUB plan.

    The provisional plan is an explicit spending decision, not a statistically
    defensible runtime estimate or an IBM upper bound. No scaling by campaign n.
    The estimate is eligible only for exactly eight treatments, five PUBs each,
    4096 shots, ibm_fez, and frozen run_02 inputs. Replacement is unaffected.
    """
    from repository_paths import MITIGATION_REL, repository_root
    from vqe_adapter import treatment_options, verify_frozen_inputs

    treatments = tuple(t['id'] for t in manifest.treatments)
    if not treatments or set(treatments) != set(APPROVED_ORIGINAL_5PUB_SECONDS) or len(treatments) != 8:
        raise RuntimeError('Provisional runtime policy requires exactly M0-M7')
    if (manifest.backend != 'ibm_fez' or manifest.shots_per_pub != 4096
            or manifest.n != 5 or getattr(manifest, 'replicate_blocks', 1) != 1
            or getattr(manifest, 'source_run', 'run_02') != 'run_02'):
        raise RuntimeError('Provisional runtime policy applies only to one 5-PUB run_02 Batch on ibm_fez')
    _, observables, theta, physical, _, _ = verify_frozen_inputs(root)
    if len(observables) != 7 or len(theta) != 21 or tuple(physical) != tuple(range(140, 147)):
        raise RuntimeError('Frozen ISA provenance does not match the approved planning workload')

    treatment_flags = {t['id']: {'DD': t['dd'], 'TREX': t['trex'], 'ZNE': t['zne']}
                       for t in manifest.treatments}
    opts = {name: repr(treatment_options(name, manifest.shots_per_pub, root)) for name in treatments}
    records = repository_root(root) / MITIGATION_REL / 'output' / '03_IBM_execution_records'
    samples, excluded = load_verified_samples(records, input_hashes=manifest.input_hashes,
        treatment_flags=treatment_flags, treatment_options_repr=opts,
        backend=manifest.backend, shots=manifest.shots_per_pub)
    empirical = estimate_runtime(samples, treatments=treatments, target_n=5,
        safety_factor=APPROVED_ORIGINAL_SAFETY_FACTOR, minimum_exact_n_samples=2)
    if empirical.defensible:
        return empirical

    # The fallback is deliberately labelled operator-approved rather than
    # fabricated as verified historical samples. It satisfies the quota gate
    # only because the operator explicitly authorized this spending scenario.
    per_treatment = {}
    for name in treatments:
        seconds = APPROVED_ORIGINAL_5PUB_SECONDS[name]
        per_treatment[name] = {
            'exact_target_n_samples': sum(s.treatment_id == name and s.n == 5 for s in samples),
            'observed_max_seconds': seconds,
            'stress_envelope_seconds': seconds * APPROVED_ORIGINAL_SAFETY_FACTOR,
            'provenance': 'operator_reported_n5_registry_snapshot_unverified_here',
            'statistically_defensible': False,
        }
    observed = sum(APPROVED_ORIGINAL_5PUB_SECONDS.values())
    return RuntimeEstimate(
        target_n=5, per_treatment=per_treatment,
        observed_target_block_seconds=observed,
        stress_block_seconds=observed * APPROVED_ORIGINAL_SAFETY_FACTOR,
        safety_factor=APPROVED_ORIGINAL_SAFETY_FACTOR,
        minimum_exact_n_samples=2,
        defensible=False,
        blockers=(), historical_preliminary_sample_count=len(samples),
        scope='full_batch', operator_approved_provisional=True,
    )


def targeted_replacement_estimate(root, manifest, treatment_id, *, registry, target_id=None):
    """Estimate one replacement PUB; never borrow a whole-Batch estimate."""
    from vqe_adapter import treatment_options
    options = repr(treatment_options(treatment_id, manifest.shots_per_pub, root))
    if not target_id:
        raise RuntimeError('Target ID is required to select replacement usage evidence')
    rows = registry.replacement_jobs(target_id)
    samples, excluded = load_verified_replacement_samples(rows, treatment_id=treatment_id,
        backend=manifest.backend, shots=manifest.shots_per_pub,
        input_hashes=manifest.input_hashes, treatment_options_repr=options)
    estimate = estimate_replacement_runtime(samples, treatment_id=treatment_id,
        backend=manifest.backend, shots=manifest.shots_per_pub,
        input_hashes=manifest.input_hashes, treatment_options_repr=options,
        minimum_samples=2)
    if estimate.defensible:
        return estimate
    # Explicit operator-approved cold-start spending policy (not statistical evidence).
    # Use the full 5-PUB treatment charge as the conservative planning baseline
    # for a single replacement; do NOT divide by five or invent measurements.
    if (manifest.backend != 'ibm_fez' or manifest.shots_per_pub != 4096
            or getattr(manifest, 'source_run', 'run_02') != 'run_02'
            or treatment_id not in APPROVED_ORIGINAL_5PUB_SECONDS):
        raise RuntimeError('Cold-start replacement policy is not authorized for this workload')
    from vqe_adapter import verify_frozen_inputs
    _, observables, theta, physical, _, _ = verify_frozen_inputs(root)
    if len(observables) != 7 or len(theta) != 21 or tuple(physical) != tuple(range(140,147)):
        raise RuntimeError('Frozen ISA does not match cold-start replacement authorization')
    observed = APPROVED_ORIGINAL_5PUB_SECONDS[treatment_id]
    bound = observed * APPROVED_ORIGINAL_SAFETY_FACTOR
    return ReplacementRuntimeEstimate(
        target_n=1, treatment_id=treatment_id, pub_count=1,
        per_treatment={treatment_id: {'exact_one_pub_samples': 0,
            'observed_max_seconds': None, 'stress_envelope_seconds': bound,
            'provenance': 'operator_approved_5pub_treatment_baseline_for_1pub_cold_start'}},
        observed_target_block_seconds=None, stress_block_seconds=bound,
        safety_factor=APPROVED_ORIGINAL_SAFETY_FACTOR,
        minimum_exact_n_samples=2, defensible=False, blockers=estimate.blockers,
        operator_approved_provisional=True)
