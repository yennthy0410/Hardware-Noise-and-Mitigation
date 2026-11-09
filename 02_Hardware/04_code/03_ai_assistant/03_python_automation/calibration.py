"""Explicitly authorized, isolated Runtime calibration for VQE mitigation.

Calibration Batches mirror one production block (one Batch, M0-M7, chunk_n
PUBs/job) but are recorded outside research blocks and never count as PUB
progress. Historical usage is diagnostic only; only validated calibration
evidence is admitted to the production runtime estimator.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any
import uuid

from account_config import ACCOUNT_LABELS
from repository_paths import MITIGATION_REL, contained, repository_root

TREATMENTS = tuple(f"M{i}_{name}" for i, name in enumerate(
    ("Baseline", "DD", "TREX", "ZNE", "DD_TREX", "DD_ZNE", "TREX_ZNE", "ALL")))
FRESHNESS_SECONDS = 600


class CalibrationTransientReadError(RuntimeError):
    """A bounded, read-only provider retrieval failed transiently."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def calibration_root(root=None) -> Path:
    repo = repository_root(root)
    return contained(repo / MITIGATION_REL / "output" / "03_IBM_execution_records" /
                     "ai_assistant", "calibration")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _batch_status(statuses: list[str]) -> str:
    """Never let later successful jobs hide a terminal failed treatment."""
    if any(status == "FAILED" for status in statuses): return "INCOMPLETE"
    if len(statuses) == 8 and all(status == "VALIDATED" for status in statuses): return "VALIDATED"
    return "COLLECTING"


def _write_immutable_json(path: str | Path, raw_json: str) -> Path:
    archive=Path(path).resolve();archive.parent.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    if archive.exists():
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=digest:
            raise RuntimeError("An immutable calibration archive already exists with different content")
        return archive
    fd,tmp_name=tempfile.mkstemp(prefix=".calibration-result-",suffix=".tmp",dir=archive.parent)
    tmp=Path(tmp_name)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as stream:
            stream.write(raw_json);stream.flush();os.fsync(stream.fileno())
        try: os.link(tmp,archive)
        except FileExistsError:
            if hashlib.sha256(archive.read_bytes()).hexdigest()!=digest:
                raise RuntimeError("An immutable calibration archive already exists with different content")
        except OSError:
            raise RuntimeError("Filesystem does not support atomic exclusive calibration archiving") from None
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass
    return archive


def build_calibration_plan(root, *, campaign_id: str, account_label: str,
                           calibration_batches: int = 3, chunk_n: int = 5,
                           shots: int = 4096, budget_cap_seconds: float) -> dict[str, Any]:
    """Build a read-only plan; no account access or output files are created."""
    if account_label not in ACCOUNT_LABELS:
        raise ValueError("Choose one authorized account label explicitly")
    if not campaign_id or any(c in campaign_id for c in "/\\:") or campaign_id in (".", ".."):
        raise ValueError("campaign_id must be a safe path component")
    if calibration_batches < 1 or chunk_n != 5 or shots != 4096:
        raise ValueError("Calibration currently requires at least one batch, chunk_n=5, shots=4096")
    if isinstance(budget_cap_seconds, bool) or not math.isfinite(float(budget_cap_seconds)) or budget_cap_seconds <= 0:
        raise ValueError("A positive, explicit cumulative calibration budget cap is required")
    if float(budget_cap_seconds) > 600.0:
        raise ValueError("Calibration budget cap cannot exceed the nominal 600-second account budget")

    from experiment_manifest import build_target_manifest
    from repository_paths import source_hashes
    from vqe_adapter import load_pilot
    repo = repository_root(root)
    target = build_target_manifest(repo, target_n=chunk_n, chunk_n=chunk_n, shots=shots,
                                   experiment_id="vqe_runtime_calibration")
    source = Path(target.source_bundle)
    # Invoke the actual frozen validator. This is read-only and never transpiles.
    pilot = load_pilot(repo)
    circuit, observables, theta, physical, _ideal, qiskit_version = pilot.verify_source(source, repo)
    if len(circuit.parameters) != 0 or len(theta) != 21 or len(observables) != 7:
        raise RuntimeError("Frozen run_02 input validation failed")
    if tuple(physical) != tuple(range(140, 147)) or source_hashes(source) != target.input_hashes:
        raise RuntimeError("Frozen run_02 layout or source hashes differ from the target manifest")
    treatments = []
    for name in TREATMENTS:
        options = pilot.configuration(name, shots)
        options_repr = repr(options)
        flags = next((dict(zip(("DD", "TREX", "ZNE"), item[1:]))
                      for item in pilot.TREATMENTS if item[0] == name), None)
        treatments.append({"treatment_id": name, "flags": flags, "options_repr": options_repr})
    plan = {"schema_version": 1, "campaign_id": campaign_id,
        "campaign_type": "runtime_quota_calibration", "source_run": "run_02",
        "source_bundle": str(source), "input_hashes": target.input_hashes,
        "backend": "ibm_fez", "physical_qubits": list(range(140, 147)),
        "circuit_parameter_count": 0, "theta_count": 21, "observable_count": 7,
        "shots_per_pub": shots, "chunk_n": chunk_n, "treatment_ids": list(TREATMENTS),
        "treatments": treatments, "calibration_batches_required": calibration_batches,
        "jobs_per_batch": 8, "requested_pubs_per_batch": chunk_n*8,
        "total_batches": calibration_batches, "total_requested_jobs": calibration_batches*8,
        "total_requested_pubs": calibration_batches*chunk_n*8,
        "account_label": account_label, "budget_cap_seconds": float(budget_cap_seconds),
        "budget_cap_is_provider_enforced": False,
        "runtime_evidence_policy": "historical usage is preliminary only; three separately validated calibration Batch samples per M0-M7 are required",
        "qiskit_version": qiskit_version, "planned_utc": utc_now()}
    plan["plan_sha256"] = _sha(plan)
    return plan


def validate_calibration_preflight(plan: dict[str, Any], report: dict[str, Any], *,
                                   now: datetime | None = None,
                                   safety_margin_seconds: float = 60.0,
                                   budget_cap_override: float | None = None) -> dict[str, Any]:
    """Fail-closed freshness, account, instance-scope, backend and quota gate."""
    reasons = []
    report_checks=report.get("checks",{}) if isinstance(report.get("checks"),dict) else {}
    checks = {"explicit_account": report.get("account_label") == plan.get("account_label"),
        "read_only_pass": report.get("status") == "PASS" and report.get("submission") is False
            and report.get("submission_eligible") is True,
        "instance_access": report_checks.get("configured_instance")=="PASS"
            and report_checks.get("accessible_instances")=="PASS",
        "backend": report.get("backend") == plan.get("backend"),
        "backend_operational": report_checks.get("backend_ibm_fez")=="PASS"
            and report_checks.get("backend_operational")=="PASS",
        "isa_compatibility": report_checks.get("isa_compatibility")=="PASS",
        "frozen_source_hashes": report.get("frozen_input_validation", {}).get("source_run") == "run_02"
            and report.get("frozen_input_validation", {}).get("input_sha256") == plan.get("input_hashes"),
        "verified_instance_scope": report.get("account_wide_usage_verified") is True
            and bool(report.get("configured_instance_ref"))
            and "single_accessible_instance" in str(report.get("usage_scope", "")),
        "no_pending_jobs": report.get("pending_jobs_count") == 0 and report_checks.get("pending_jobs")=="PASS",
        "usage_report_pass": report_checks.get("usage")=="PASS" and report_checks.get("provider_quota")=="PASS",
        "batch_sdk_compatible": isinstance(report.get("sdk_batch_compatibility"),dict)
            and report["sdk_batch_compatibility"].get("available") is True
            and report["sdk_batch_compatibility"].get("batch_created") is False}
    try:
        checked = datetime.fromisoformat(str(report["checked_utc"]).replace("Z", "+00:00"))
        age = ((now or datetime.now(timezone.utc)) - checked).total_seconds()
        checks["fresh"] = 0 <= age <= FRESHNESS_SECONDS
    except (KeyError, TypeError, ValueError):
        checks["fresh"] = False
    rows = report.get("instances")
    usage = rows[0].get("usage") if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) else None
    remaining = usage.get("remaining_seconds") if isinstance(usage, dict) else None
    limit = usage.get("limit_seconds") if isinstance(usage, dict) else None
    checks["provider_quota"] = (isinstance(remaining, (int, float)) and not isinstance(remaining, bool)
        and math.isfinite(float(remaining)) and remaining >= 0
        and isinstance(limit, (int, float)) and not isinstance(limit, bool)
        and math.isfinite(float(limit)) and limit > 0 and remaining <= limit)
    for name, passed in checks.items():
        if not passed:
            reasons.append(f"{name} did not pass")
    budget = float(plan["budget_cap_seconds"] if budget_cap_override is None else budget_cap_override)
    if not math.isfinite(float(safety_margin_seconds)) or safety_margin_seconds<0:
        raise ValueError("Safety margin must be finite and nonnegative")
    from final_configuration import quota_eligibility
    quota_gate=quota_eligibility(remaining,safety_margin_seconds=safety_margin_seconds,estimated_seconds=budget)
    checks["quota_headroom"] = bool(checks["provider_quota"] and quota_gate["eligible"])
    if checks["provider_quota"] and not checks["quota_headroom"]:
        reasons.append("provider-reported available quota is below the original Batch threshold")
    return {"eligible": all(checks.values()) and not reasons, "checks": checks,
        "account_label": plan["account_label"], "remaining_seconds": remaining,
        "provider_limit_seconds": limit, "budget_cap_seconds": budget,
        "safety_margin_seconds":float(safety_margin_seconds),
        "reasons": reasons, "batch_created": False, "submission": False}


class CalibrationRegistry:
    """SQLite campaign state isolated from production experiment tables."""
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY, plan_sha256 TEXT NOT NULL,
                    plan_json TEXT NOT NULL, created_utc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS calibration_batches (
                    campaign_id TEXT NOT NULL, sequence_no INTEGER NOT NULL,
                    batch_id TEXT, status TEXT NOT NULL, account_label TEXT NOT NULL,
                    instance_ref TEXT NOT NULL,
                    budget_cap_seconds REAL NOT NULL, created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL, PRIMARY KEY(campaign_id,sequence_no),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(campaign_id));
                CREATE TABLE IF NOT EXISTS calibration_jobs (
                    campaign_id TEXT NOT NULL, sequence_no INTEGER NOT NULL,
                    treatment_id TEXT NOT NULL, status TEXT NOT NULL, job_id TEXT,
                    qpu_usage_seconds REAL, evidence_json TEXT, validation_error_json TEXT,
                    updated_utc TEXT NOT NULL,
                    PRIMARY KEY(campaign_id,sequence_no,treatment_id),
                    UNIQUE(job_id));
                CREATE TABLE IF NOT EXISTS calibration_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
                    sequence_no INTEGER, event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL, created_utc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS calibration_raw_results (
                    campaign_id TEXT NOT NULL, sequence_no INTEGER NOT NULL,
                    treatment_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    result_sha256 TEXT NOT NULL, archive_path TEXT NOT NULL,
                    raw_result_json TEXT NOT NULL, created_utc TEXT NOT NULL,
                    PRIMARY KEY(campaign_id,sequence_no,treatment_id),
                    UNIQUE(job_id));
                CREATE TRIGGER IF NOT EXISTS calibration_raw_results_no_update
                    BEFORE UPDATE ON calibration_raw_results
                    BEGIN SELECT RAISE(ABORT,'calibration raw results are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS calibration_raw_results_no_delete
                    BEFORE DELETE ON calibration_raw_results
                    BEGIN SELECT RAISE(ABORT,'calibration raw results are immutable'); END;
                CREATE TABLE IF NOT EXISTS calibration_worker_state (
                    campaign_id TEXT PRIMARY KEY, state_json TEXT NOT NULL,
                    updated_utc TEXT NOT NULL);
            """)
            columns={row[1] for row in db.execute("PRAGMA table_info(calibration_jobs)")}
            if "validation_error_json" not in columns:
                db.execute("ALTER TABLE calibration_jobs ADD COLUMN validation_error_json TEXT")

    def create_campaign(self, plan: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT plan_sha256 FROM campaigns WHERE campaign_id=?",
                             (plan["campaign_id"],)).fetchone()
            if row:
                if row[0] != plan["plan_sha256"]:
                    raise RuntimeError("Calibration campaign ID already exists with a different plan")
                return
            db.execute("INSERT INTO campaigns VALUES(?,?,?,?)",
                (plan["campaign_id"], plan["plan_sha256"], _json(plan), utc_now()))
            db.execute("INSERT INTO calibration_events(campaign_id,event_type,details_json,created_utc) VALUES(?,?,?,?)",
                (plan["campaign_id"], "CAMPAIGN_PLANNED", _json({"plan_sha256": plan["plan_sha256"]}), utc_now()))

    def reserve_batch(self, plan: dict[str, Any], sequence_no: int, *, gate: dict[str, Any],
                      instance_ref: str) -> None:
        if not gate.get("eligible"):
            raise RuntimeError("Calibration quota/preflight gate refused: " + "; ".join(gate.get("reasons", [])))
        if sequence_no < 1 or sequence_no > int(plan["calibration_batches_required"]):
            raise ValueError("Calibration Batch sequence is outside the campaign plan")
        now = utc_now()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            campaign = db.execute("SELECT plan_sha256 FROM campaigns WHERE campaign_id=?",
                                  (plan["campaign_id"],)).fetchone()
            if not campaign or campaign[0] != plan["plan_sha256"]:
                raise RuntimeError("Calibration plan is not durably registered")
            prior = db.execute("SELECT status FROM calibration_batches WHERE campaign_id=? ORDER BY sequence_no DESC LIMIT 1",
                               (plan["campaign_id"],)).fetchone()
            if prior and prior[0] not in ("VALIDATED", "FAILED_SETTLED"):
                raise RuntimeError("Earlier calibration Batch is incomplete or unresolved; reconcile it first")
            if db.execute("SELECT 1 FROM calibration_batches WHERE campaign_id=? AND sequence_no=?",
                          (plan["campaign_id"], sequence_no)).fetchone():
                raise RuntimeError("Calibration Batch sequence already exists; duplicate submission blocked")
            spent=db.execute("SELECT COALESCE(SUM(qpu_usage_seconds),0) FROM calibration_jobs WHERE campaign_id=?",
                             (plan["campaign_id"],)).fetchone()[0]
            remaining_cap=float(plan["budget_cap_seconds"])-float(spent or 0)
            if remaining_cap<=0:
                raise RuntimeError("Cumulative calibration budget cap has been consumed")
            batch_cap=min(float(plan["budget_cap_seconds"])/int(plan["calibration_batches_required"]),remaining_cap)
            db.execute("INSERT INTO calibration_batches VALUES(?,?,NULL,'RESERVED',?,?,?,?,?)",
                (plan["campaign_id"], sequence_no, plan["account_label"], instance_ref,
                 batch_cap, now, now))
            for treatment in plan["treatment_ids"]:
                db.execute("INSERT INTO calibration_jobs(campaign_id,sequence_no,treatment_id,status,updated_utc) VALUES(?,?,?,'RESERVED',?)",
                           (plan["campaign_id"], sequence_no, treatment, now))
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (plan["campaign_id"], sequence_no, "CALIBRATION_QUOTA_RESERVED",
                 _json({"batch_budget_reservation_seconds":batch_cap,
                        "campaign_budget_cap_seconds":plan["budget_cap_seconds"],
                        "provider_budget_enforced":False,
                        "quota_scope":"verified_account_scope"}), now))

    def begin_batch(self, campaign_id: str, sequence_no: int, batch_id: str | None) -> None:
        now = utc_now()
        with sqlite3.connect(self.path) as db:
            cur = db.execute("UPDATE calibration_batches SET batch_id=?,status='SUBMITTING',updated_utc=? WHERE campaign_id=? AND sequence_no=? AND status='RESERVED'",
                             (batch_id, now, campaign_id, sequence_no))
            if cur.rowcount != 1: raise RuntimeError("Calibration Batch is not in RESERVED state")
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id, sequence_no, "CALIBRATION_BATCH_CONTEXT_ENTERED", _json({"batch_id": batch_id}), now))

    def mark_job_submitting(self, campaign_id: str, sequence_no: int, treatment_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            cur = db.execute("UPDATE calibration_jobs SET status='SUBMITTING',updated_utc=? WHERE campaign_id=? AND sequence_no=? AND treatment_id=? AND status='RESERVED' AND job_id IS NULL",
                             (utc_now(), campaign_id, sequence_no, treatment_id))
            if cur.rowcount != 1: raise RuntimeError("Calibration job already attempted; reconcile before retry")

    def persist_job_id(self, campaign_id: str, sequence_no: int, treatment_id: str, job_id: str) -> None:
        if not job_id: raise ValueError("Runtime returned an empty job ID")
        now = utc_now()
        with sqlite3.connect(self.path) as db:
            cur = db.execute("UPDATE calibration_jobs SET status='SUBMITTED',job_id=?,updated_utc=? WHERE campaign_id=? AND sequence_no=? AND treatment_id=? AND status='SUBMITTING' AND job_id IS NULL",
                             (job_id, now, campaign_id, sequence_no, treatment_id))
            if cur.rowcount != 1: raise RuntimeError("Cannot persist calibration job ID into the current attempt")
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id, sequence_no, "CALIBRATION_JOB_ID_PERSISTED",
                 _json({"treatment_id": treatment_id, "job_id": job_id}), now))

    def finish_submission(self, campaign_id: str, sequence_no: int) -> None:
        now=utc_now()
        with sqlite3.connect(self.path) as db:
            jobs=db.execute("SELECT status,job_id FROM calibration_jobs WHERE campaign_id=? AND sequence_no=?",
                            (campaign_id,sequence_no)).fetchall()
            if len(jobs)!=8 or any(state!="SUBMITTED" or not job_id for state,job_id in jobs):
                raise RuntimeError("Calibration Batch submission is partial; it cannot be marked submitted")
            db.execute("UPDATE calibration_batches SET status='SUBMITTED',updated_utc=? WHERE campaign_id=? AND sequence_no=? AND status='SUBMITTING'",
                       (now,campaign_id,sequence_no))
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id,sequence_no,"CALIBRATION_EIGHT_JOB_SUBMISSION_COMPLETE",
                 _json({"automatic_retry":False}),now))

    def mark_interrupted_unknown(self, campaign_id: str, sequence_no: int) -> bool:
        now = utc_now()
        with sqlite3.connect(self.path) as db:
            state=db.execute("SELECT status FROM calibration_batches WHERE campaign_id=? AND sequence_no=?",
                             (campaign_id,sequence_no)).fetchone()
            if state is None: raise KeyError((campaign_id,sequence_no))
            if state[0] not in ("RESERVED","SUBMITTING"):
                return False
            db.execute("UPDATE calibration_jobs SET status='UNKNOWN',updated_utc=? WHERE campaign_id=? AND sequence_no=? AND status='SUBMITTING' AND job_id IS NULL",
                       (now, campaign_id, sequence_no))
            db.execute("UPDATE calibration_batches SET status='INCOMPLETE',updated_utc=? WHERE campaign_id=? AND sequence_no=? AND status IN ('RESERVED','SUBMITTING')",
                       (now, campaign_id, sequence_no))
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id, sequence_no, "CALIBRATION_RESTART_REQUIRES_RECONCILIATION",
                 _json({"automatic_retry": False}), now))
            return True

    def save_evidence(self, campaign_id: str, sequence_no: int, treatment_id: str,
                      job_id: str, evidence: dict[str, Any]) -> None:
        now = utc_now()
        with sqlite3.connect(self.path) as db:
            cur = db.execute("UPDATE calibration_jobs SET status='VALIDATED',qpu_usage_seconds=?,evidence_json=?,updated_utc=? WHERE campaign_id=? AND sequence_no=? AND treatment_id=? AND job_id=? AND status IN ('SUBMITTED','COMPLETED')",
                (evidence["qpu_usage_seconds"], _json(evidence), now, campaign_id, sequence_no, treatment_id, job_id))
            if cur.rowcount != 1: raise RuntimeError("Calibration evidence does not match a submitted job")
            statuses = [row[0] for row in db.execute("SELECT status FROM calibration_jobs WHERE campaign_id=? AND sequence_no=?",
                                                       (campaign_id, sequence_no)).fetchall()]
            batch_status = _batch_status(statuses)
            db.execute("UPDATE calibration_batches SET status=?,updated_utc=? WHERE campaign_id=? AND sequence_no=?",
                       (batch_status, now, campaign_id, sequence_no))
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id, sequence_no, "CALIBRATION_USAGE_EVIDENCE_VALIDATED",
                 _json({"treatment_id": treatment_id, "job_id": job_id,
                        "qpu_usage_seconds": evidence["qpu_usage_seconds"]}), now))

    def save_collected_result(self, campaign_id: str, sequence_no: int, treatment_id: str,
                              job_id: str, evidence: dict[str, Any], raw_result: dict[str, Any],
                              archive_path: str | Path) -> str:
        """Atomically register an immutable result snapshot and validated evidence.

        The JSON archive is created exclusively. A repeated identical collection
        is idempotent; a different payload for the same job is refused.
        """
        raw_json = _json(raw_result)
        raw_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        archive = _write_immutable_json(archive_path,raw_json)
        now = utc_now()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT result_sha256 FROM calibration_raw_results WHERE campaign_id=? AND sequence_no=? AND treatment_id=?",
                                  (campaign_id,sequence_no,treatment_id)).fetchone()
            if existing and existing[0] != raw_hash:
                raise RuntimeError("A different immutable calibration result is already registered")
            if not existing:
                db.execute("INSERT INTO calibration_raw_results VALUES(?,?,?,?,?,?,?,?)",
                    (campaign_id,sequence_no,treatment_id,job_id,raw_hash,str(archive),raw_json,now))
            row = db.execute("SELECT status,job_id,evidence_json FROM calibration_jobs WHERE campaign_id=? AND sequence_no=? AND treatment_id=?",
                             (campaign_id,sequence_no,treatment_id)).fetchone()
            if row is None or row[1] != job_id:
                raise RuntimeError("Collected calibration result does not match a registered job ID")
            if row[0] == "VALIDATED":
                if not row[2] or json.loads(row[2]).get("result_sha256") != raw_hash:
                    raise RuntimeError("Validated calibration evidence conflicts with the immutable result")
                return raw_hash
            if row[0] not in ("SUBMITTED", "COMPLETED"):
                raise RuntimeError(f"Cannot collect calibration result from state {row[0]}")
            evidence = {**evidence,"result_sha256":raw_hash,"result_archive":str(archive),
                        "result_values_persisted":True}
            cur = db.execute("UPDATE calibration_jobs SET status='VALIDATED',qpu_usage_seconds=?,evidence_json=?,updated_utc=? WHERE campaign_id=? AND sequence_no=? AND treatment_id=? AND job_id=? AND status IN ('SUBMITTED','COMPLETED')",
                (evidence["qpu_usage_seconds"],_json(evidence),now,campaign_id,sequence_no,treatment_id,job_id))
            if cur.rowcount != 1:
                raise RuntimeError("Calibration job changed during result collection")
            statuses = [r[0] for r in db.execute("SELECT status FROM calibration_jobs WHERE campaign_id=? AND sequence_no=?",
                                                  (campaign_id,sequence_no)).fetchall()]
            batch_status = _batch_status(statuses)
            db.execute("UPDATE calibration_batches SET status=?,updated_utc=? WHERE campaign_id=? AND sequence_no=?",
                       (batch_status,now,campaign_id,sequence_no))
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id,sequence_no,"CALIBRATION_RAW_RESULT_ARCHIVED",
                 _json({"treatment_id":treatment_id,"job_id":job_id,"result_sha256":raw_hash,
                        "archive_path":str(archive),"uncertainty_status":evidence.get("uncertainty_status")}),now))
        return raw_hash

    def archive_unvalidated_result(self, campaign_id: str, sequence_no: int, treatment_id: str,
                                   job_id: str, raw_result: dict[str, Any],
                                   archive_path: str | Path) -> str:
        """Preserve partial/corrupt provider data without certifying the job."""
        raw_json=_json(raw_result);raw_hash=hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        archive=_write_immutable_json(archive_path,raw_json);now=utc_now()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            existing=db.execute("SELECT result_sha256 FROM calibration_raw_results WHERE campaign_id=? AND sequence_no=? AND treatment_id=?",
                (campaign_id,sequence_no,treatment_id)).fetchone()
            if existing and existing[0]!=raw_hash:
                raise RuntimeError("A different immutable calibration result is already registered")
            if not existing:
                db.execute("INSERT INTO calibration_raw_results VALUES(?,?,?,?,?,?,?,?)",
                    (campaign_id,sequence_no,treatment_id,job_id,raw_hash,str(archive),raw_json,now))
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id,sequence_no,"CALIBRATION_RAW_RESULT_ARCHIVED_UNVALIDATED",
                 _json({"treatment_id":treatment_id,"job_id":job_id,"result_sha256":raw_hash,
                        "archive_path":str(archive)}),now))
        return raw_hash

    def record_worker_state(self, campaign_id: str, state: dict[str, Any]) -> None:
        now=utc_now()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO calibration_worker_state VALUES(?,?,?) ON CONFLICT(campaign_id) DO UPDATE SET state_json=excluded.state_json,updated_utc=excluded.updated_utc",
                       (campaign_id,_json(state),now))
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,NULL,?,?,?)",
                       (campaign_id,"WORKER_CHECKPOINT",_json(state),now))

    def record_event(self, campaign_id: str, sequence_no: int | None, event_type: str,
                     details: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id,sequence_no,event_type,_json(details),utc_now()))

    def fail_job(self, campaign_id: str, sequence_no: int, treatment_id: str, *, job_id: str | None,
                 failure_class: str, validation_error: dict[str, Any] | None = None,
                 qpu_usage_seconds: float | None = None) -> None:
        now = utc_now()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE calibration_jobs SET status='FAILED',job_id=COALESCE(job_id,?),qpu_usage_seconds=COALESCE(?,qpu_usage_seconds),validation_error_json=COALESCE(?,validation_error_json),updated_utc=? WHERE campaign_id=? AND sequence_no=? AND treatment_id=?",
                       (job_id,qpu_usage_seconds,_json(validation_error) if validation_error else None,now,campaign_id,sequence_no,treatment_id))
            db.execute("UPDATE calibration_batches SET status='INCOMPLETE',updated_utc=? WHERE campaign_id=? AND sequence_no=?",
                       (now, campaign_id, sequence_no))
            db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                (campaign_id, sequence_no, "CALIBRATION_JOB_FAILED", _json({"treatment_id": treatment_id,
                    "job_id": job_id, "failure_class": failure_class, "validation_error":validation_error,
                    "qpu_usage_seconds":qpu_usage_seconds,"automatic_retry": False}), now))

    def reconcile_archived_validation(self, campaign_id: str, sequence_no: int, treatment_id: str,
                                      job_id: str, *, result_sha256: str,
                                      validation_error: dict[str, Any] | None,
                                      qpu_usage_seconds: float | None) -> None:
        """Persist offline archive validation and independently verified provider usage."""
        now=utc_now(); details={"treatment_id":treatment_id,"job_id":job_id,
            "result_sha256":result_sha256,"validation_error":validation_error,
            "qpu_usage_seconds":qpu_usage_seconds,"automatic_resubmit":False}
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            raw=db.execute("SELECT job_id,result_sha256 FROM calibration_raw_results WHERE campaign_id=? AND sequence_no=? AND treatment_id=?",
                (campaign_id,sequence_no,treatment_id)).fetchone()
            if raw is None or raw[0]!=job_id or raw[1]!=result_sha256:
                raise RuntimeError("Offline archive reconciliation does not match immutable registered evidence")
            cur=db.execute("UPDATE calibration_jobs SET qpu_usage_seconds=COALESCE(?,qpu_usage_seconds),validation_error_json=?,updated_utc=? WHERE campaign_id=? AND sequence_no=? AND treatment_id=? AND job_id=?",
                (qpu_usage_seconds,_json(validation_error) if validation_error else None,now,campaign_id,sequence_no,treatment_id,job_id))
            if cur.rowcount!=1:
                raise RuntimeError("Offline archive reconciliation did not match a registered treatment job")
            if validation_error:
                db.execute("UPDATE calibration_jobs SET status='FAILED' WHERE campaign_id=? AND sequence_no=? AND treatment_id=? AND job_id=?",
                    (campaign_id,sequence_no,treatment_id,job_id))
            statuses=[row[0] for row in db.execute("SELECT status FROM calibration_jobs WHERE campaign_id=? AND sequence_no=?",
                (campaign_id,sequence_no)).fetchall()]
            db.execute("UPDATE calibration_batches SET status=?,updated_utc=? WHERE campaign_id=? AND sequence_no=?",
                (_batch_status(statuses),now,campaign_id,sequence_no))
            encoded=_json(details)
            exists=db.execute("SELECT 1 FROM calibration_events WHERE campaign_id=? AND sequence_no=? AND event_type='CALIBRATION_ARCHIVE_REVALIDATED' AND details_json=?",
                (campaign_id,sequence_no,encoded)).fetchone()
            if not exists:
                db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                    (campaign_id,sequence_no,"CALIBRATION_ARCHIVE_REVALIDATED",encoded,now))

    def status(self, campaign_id: str) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            campaign = db.execute("SELECT * FROM campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            batches = db.execute("SELECT * FROM calibration_batches WHERE campaign_id=? ORDER BY sequence_no", (campaign_id,)).fetchall()
            jobs = db.execute("SELECT * FROM calibration_jobs WHERE campaign_id=? ORDER BY sequence_no,treatment_id", (campaign_id,)).fetchall()
            events = db.execute("SELECT * FROM calibration_events WHERE campaign_id=? ORDER BY id", (campaign_id,)).fetchall()
            raw_results = db.execute("SELECT sequence_no,treatment_id,job_id,result_sha256,archive_path,created_utc FROM calibration_raw_results WHERE campaign_id=? ORDER BY sequence_no,treatment_id",(campaign_id,)).fetchall()
            worker = db.execute("SELECT state_json,updated_utc FROM calibration_worker_state WHERE campaign_id=?",(campaign_id,)).fetchone()
        if campaign is None: return {"campaign_id": campaign_id, "exists": False}
        return {"campaign_id": campaign_id, "exists": True, "plan": json.loads(campaign["plan_json"]),
            "batches": [dict(row) for row in batches], "jobs": [dict(row) for row in jobs],
            "events": [dict(row) for row in events],
            "raw_results":[dict(row) for row in raw_results],
            "worker_state":({"state":json.loads(worker["state_json"]),"updated_utc":worker["updated_utc"]} if worker else None)}


def revalidate_calibration_archives(registry: CalibrationRegistry, campaign_id: str,
                                    sequence_no: int = 1) -> dict[str, Any]:
    """Revalidate registered immutable archives without Runtime access or archive writes."""
    status=registry.status(campaign_id)
    if not status.get("exists"): raise KeyError(campaign_id)
    plan=status["plan"]
    batch=next((row for row in status["batches"] if row["sequence_no"]==sequence_no),None)
    if batch is None: raise KeyError(f"sequence {sequence_no}")
    jobs={row["treatment_id"]:row for row in status["jobs"] if row["sequence_no"]==sequence_no}
    archives={row["treatment_id"]:row for row in status["raw_results"] if row["sequence_no"]==sequence_no}
    reports=[]
    for treatment in TREATMENTS:
        job=jobs.get(treatment); archived=archives.get(treatment)
        if not job or not job.get("job_id") or not archived:
            reports.append({"treatment_id":treatment,"status":"MISSING_ARCHIVE_OR_JOB","validation":"FAIL"})
            continue
        path=Path(archived["archive_path"])
        errors=[]; raw=None; digest=None
        try:
            payload=path.read_bytes(); digest=hashlib.sha256(payload).hexdigest()
            if digest!=archived["result_sha256"]: errors.append({"code":"ARCHIVE_HASH_MISMATCH"})
            raw=json.loads(payload.decode("utf-8"))
        except Exception as exc:
            errors.append({"code":"ARCHIVE_READ_OR_JSON_ERROR","error_type":type(exc).__name__})
        if raw is not None:
            expected={"campaign_id":campaign_id,"calibration_batch_sequence":sequence_no,
                "calibration_batch_id":batch["batch_id"],"treatment_id":treatment,"job_id":job["job_id"],
                "account_label":plan["account_label"],"backend":plan["backend"],"source_run":plan["source_run"],
                "input_hashes":plan["input_hashes"],"shots_per_pub":plan["shots_per_pub"]}
            for key,value in expected.items():
                if raw.get(key)!=value: errors.append({"code":"PROVENANCE_MISMATCH","field":key})
            if int(raw.get("schema_version",1))>=2:
                for path_key,hash_key,code in (
                    ("provider_response_path","provider_response_sha256","PROVIDER_RESPONSE_ARCHIVE_HASH_MISMATCH"),
                    ("provider_metadata_path","provider_metadata_sha256","PROVIDER_METADATA_ARCHIVE_HASH_MISMATCH")):
                    linked=raw.get(path_key);claimed=raw.get(hash_key)
                    try:
                        linked_path=Path(linked)
                        linked_digest=hashlib.sha256(linked_path.read_bytes()).hexdigest()
                        if not isinstance(claimed,str) or linked_digest!=claimed:
                            errors.append({"code":code,"field":path_key})
                        elif path_key=="provider_metadata_path":
                            linked_metadata=json.loads(linked_path.read_text(encoding="utf-8"))
                            if linked_metadata.get("raw_response_sha256")!=raw.get("provider_response_sha256"):
                                errors.append({"code":"PROVIDER_METADATA_RESPONSE_LINK_MISMATCH"})
                    except Exception:
                        errors.append({"code":code,"field":path_key})
            treatment_config=next((item for item in plan["treatments"] if item["treatment_id"]==treatment),{})
            if raw.get("treatment_options_repr")!=treatment_config.get("options_repr"):
                errors.append({"code":"TREATMENT_OPTIONS_MISMATCH"})
            if raw.get("job_status") not in ("DONE","COMPLETED"):
                errors.append({"code":"PROVIDER_JOB_NOT_DONE"})
            pubs=raw.get("pub_results")
            if not isinstance(pubs,list) or len(pubs)!=int(plan["chunk_n"]):
                errors.append({"code":"INCOMPLETE_PUBS","expected":int(plan["chunk_n"]),
                    "actual":len(pubs) if isinstance(pubs,list) else None})
            else:
                for pub_idx,pub in enumerate(pubs,1):
                    evs=pub.get("expectation_values") if isinstance(pub,dict) else None
                    if not isinstance(evs,list) or len(evs)!=7:
                        errors.append({"code":"EXPECTATION_SHAPE_INVALID","pub_index":pub_idx,
                            "actual_count":len(evs) if isinstance(evs,list) else None})
                    else:
                        for value_idx,value in enumerate(evs,1):
                            try: finite=math.isfinite(float(value)) and not isinstance(value,bool)
                            except (TypeError,ValueError,OverflowError): finite=False
                            if not finite:
                                errors.append({"code":"EXPECTATION_NONFINITE_OR_NONNUMERIC",
                                    "pub_index":pub_idx,"expectation_index":value_idx})
                    stds=pub.get("standard_deviations") if isinstance(pub,dict) else None
                    uncertainty=pub.get("uncertainty_status") if isinstance(pub,dict) else None
                    if stds is None:
                        if uncertainty not in ("MISSING",None):
                            errors.append({"code":"UNCERTAINTY_STATUS_MISMATCH","pub_index":pub_idx})
                    elif not isinstance(stds,list) or len(stds)!=7:
                        errors.append({"code":"UNCERTAINTY_SHAPE_INVALID","pub_index":pub_idx})
                    else:
                        for value_idx,value in enumerate(stds,1):
                            try: valid=math.isfinite(float(value)) and float(value)>=0 and not isinstance(value,bool)
                            except (TypeError,ValueError,OverflowError): valid=False
                            if not valid:
                                errors.append({"code":"UNCERTAINTY_NONFINITE_OR_INVALID",
                                    "pub_index":pub_idx,"uncertainty_index":value_idx})
            usage=raw.get("qpu_usage_seconds"); metrics=raw.get("provider_metrics")
            metric_usage=(metrics.get("usage",{}).get("qpu_charge_time_seconds")
                if isinstance(metrics,dict) and isinstance(metrics.get("usage"),dict) else None)
            usage_verified=(raw.get("usage_source")=="provider_job_metrics" and
                isinstance(usage,(int,float)) and not isinstance(usage,bool) and math.isfinite(float(usage)) and usage>0 and
                isinstance(metric_usage,(int,float)) and not isinstance(metric_usage,bool) and
                math.isfinite(float(metric_usage)) and float(metric_usage)==float(usage))
            if usage is not None and not usage_verified:
                errors.append({"code":"PROVIDER_USAGE_PROVENANCE_UNVERIFIED"})
            if usage is None: errors.append({"code":"PROVIDER_USAGE_MISSING"})
        else: usage=None; usage_verified=False
        error_doc={"schema_version":1,"source":"immutable_archive","result_sha256":digest,
            "errors":errors,"automatic_resubmit":False} if errors else None
        # A valid archive may reconcile an archive-validation failure, while this
        # command deliberately does not promote FAILED rows to research evidence.
        registry.reconcile_archived_validation(campaign_id,sequence_no,treatment,job["job_id"],
            result_sha256=archived["result_sha256"],validation_error=error_doc,
            qpu_usage_seconds=float(usage) if usage_verified else None)
        reports.append({"treatment_id":treatment,"job_id":job["job_id"],
            "status":"FAILED" if errors else job["status"],"validation":"FAIL" if errors else "PASS",
            "errors":errors,"qpu_usage_seconds":float(usage) if usage_verified else None,
            "usage_verified":usage_verified,"result_sha256":archived["result_sha256"],
            "archive_unchanged":digest==archived["result_sha256"],"automatic_resubmit":False})
    latest=registry.status(campaign_id)
    latest_batch=next(row for row in latest["batches"] if row["sequence_no"]==sequence_no)
    failed=[row for row in reports if row["validation"]!="PASS"]
    if failed:
        event_key=f"ARCHIVE_VALIDATION:{failed[0]['treatment_id']}:{failed[0].get('result_sha256')}"
        details={"event_key":event_key,"available":False,"diagnosis":None,
            "reason":"Offline archive validation found unresolved invalid or missing evidence; AI provider was not invoked.",
            "event_class":"ARCHIVE_VALIDATION_FAILURE","treatment_id":failed[0]["treatment_id"],
            "safe_recovery_suggestions":["Keep the treatment FAILED; do not fabricate values or resubmit."],
            "ai_actions_executed":False}
        with sqlite3.connect(registry.path) as db:
            exists=db.execute("SELECT 1 FROM calibration_events WHERE campaign_id=? AND sequence_no=? AND event_type='AI_DIAGNOSTIC' AND details_json=?",
                (campaign_id,sequence_no,_json(details))).fetchone()
            if not exists:
                db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                    (campaign_id,sequence_no,"AI_DIAGNOSTIC",_json(details),utc_now()))
            prior_state=(latest.get("worker_state") or {}).get("state",{})
            stopped_utc=(prior_state.get("stopped_utc") if prior_state.get("recovery_required") else utc_now())
            worker_state={"state":"STOPPED","mode":"python","sequence_no":sequence_no,
                "stopped_utc":stopped_utc,"batch_state":latest_batch["status"],
                "stop_reason":"RECOVERY_REQUIRED","recovery_required":True,
                "failed_treatments":[row["treatment_id"] for row in failed],
                "validation_errors":{row["treatment_id"]:row["errors"] for row in failed},
                "automatic_resubmit":False}
            db.execute("INSERT INTO calibration_worker_state VALUES(?,?,?) ON CONFLICT(campaign_id) DO UPDATE SET state_json=excluded.state_json,updated_utc=excluded.updated_utc",
                (campaign_id,_json(worker_state),utc_now()))
            checkpoint=_json({**worker_state,"checkpoint_reason":"OFFLINE_ARCHIVE_REVALIDATION"})
            seen=db.execute("SELECT 1 FROM calibration_events WHERE campaign_id=? AND sequence_no=? AND event_type='WORKER_CHECKPOINT' AND details_json=?",
                (campaign_id,sequence_no,checkpoint)).fetchone()
            if not seen:
                db.execute("INSERT INTO calibration_events(campaign_id,sequence_no,event_type,details_json,created_utc) VALUES(?,?,?,?,?)",
                    (campaign_id,sequence_no,"WORKER_CHECKPOINT",checkpoint,utc_now()))
    return {"campaign_id":campaign_id,"sequence_no":sequence_no,"offline_only":True,
        "batch_status":latest_batch["status"],"recovery_required":bool(failed),
        "jobs":reports,"automatic_resubmit":False}


def collect_calibration_batch(plan: dict[str, Any], sequence_no: int,
                              registry: CalibrationRegistry, service, *, root=None,
                              archive_root: str | Path | None = None) -> dict[str, Any]:
    """Read-only retrieval and exact workload validation; no retries or results merge."""
    from quota import qpu_seconds_from_metrics
    from vqe_adapter import (capture_runtime_result, _redact_runtime_metadata,
                             serialize_runtime_result_archive)
    from repository_paths import contained
    status = registry.status(plan["campaign_id"])
    # Revalidate immutable Fixed ISA source before consuming any provider result.
    from repository_paths import repository_root,source_hashes
    repo=repository_root(root)
    source=Path(plan["source_bundle"]).resolve()
    if source_hashes(source)!=plan.get("input_hashes"):
        raise RuntimeError("Frozen run_02 input hashes no longer match the calibration plan")
    from vqe_adapter import load_pilot
    circuit,observables,theta,physical,_ideal,_version=load_pilot(repo).verify_source(source,repo)
    if len(circuit.parameters)!=0 or len(theta)!=21 or len(observables)!=7 or tuple(physical)!=tuple(range(140,147)):
        raise RuntimeError("Frozen run_02 circuit/layout/observable validation failed during collection")
    batch = next((item for item in status["batches"] if item["sequence_no"] == sequence_no), None)
    if batch is None or batch["status"] not in ("SUBMITTED", "COLLECTING", "INCOMPLETE", "VALIDATED"):
        raise RuntimeError("Calibration Batch has no submitted jobs to collect")
    reports = []
    for row in (item for item in status["jobs"] if item["sequence_no"] == sequence_no):
        if row["status"] == "VALIDATED":
            raw_row=next((item for item in status.get("raw_results",[]) if item["sequence_no"]==sequence_no
                          and item["treatment_id"]==row["treatment_id"]),None)
            if raw_row is None:
                reports.append({"treatment_id":row["treatment_id"],"status":"LEGACY_VALIDATED_NO_RAW_ARCHIVE"})
                continue
            archive=Path(raw_row["archive_path"])
            current_hash=hashlib.sha256(archive.read_bytes()).hexdigest() if archive.is_file() else None
            evidence=json.loads(row["evidence_json"]) if row.get("evidence_json") else {}
            if current_hash!=raw_row["result_sha256"] or evidence.get("result_sha256")!=raw_row["result_sha256"]:
                raise RuntimeError("Stored immutable calibration result hash verification failed")
            reports.append({"treatment_id": row["treatment_id"], "status": "ALREADY_VALIDATED",
                            "result_sha256":raw_row["result_sha256"]}); continue
        if row["status"] == "FAILED":
            reports.append({"treatment_id":row["treatment_id"],"job_id":row["job_id"],
                            "status":"ALREADY_FAILED","automatic_resubmit":False});continue
        if not row["job_id"]:
            reports.append({"treatment_id": row["treatment_id"], "status": row["status"],
                            "failure_class": "SUBMISSION_AMBIGUITY", "automatic_retry": False}); continue
        try:
            job = service.job(row["job_id"])
            state = str(job.status()).upper().split(".")[-1]
            if state not in ("DONE", "COMPLETED"):
                reports.append({"treatment_id": row["treatment_id"], "job_id": row["job_id"],
                                "status": state, "automatic_retry": False}); continue
            result = job.result()
            # Archive the unvalidated Runtime response immediately. This
            # content-addressed response survives later metadata/API or PUB
            # validation failures and is never overwritten.
            archive_base=Path(archive_root).resolve() if archive_root is not None else calibration_root(root)
            response_bytes=serialize_runtime_result_archive(result)
            response_hash=hashlib.sha256(response_bytes).hexdigest()
            response_path=contained(archive_base,plan["campaign_id"],"raw_responses",
                f"sequence_{sequence_no:03d}",f"{row['job_id']}_{response_hash}.json")
            _write_immutable_json(response_path,response_bytes.decode("utf-8"))
            # The Runtime result is already in memory and must still be
            # archived if an optional provider metadata endpoint is failing.
            # Record the failure explicitly; never infer QPU usage from it.
            provider_metadata_errors=[]
            try:
                metrics = job.metrics() if callable(getattr(job, "metrics", None)) else {}
            except Exception as metadata_exc:
                metrics={}
                provider_metadata_errors.append({"field":"metrics","error_type":type(metadata_exc).__name__})
            usage = qpu_seconds_from_metrics(metrics)
            from vqe_adapter import treatment_options
            options_repr = repr(treatment_options(row["treatment_id"], int(plan["shots_per_pub"]), root))
            treatment = next(item for item in plan["treatments"] if item["treatment_id"] == row["treatment_id"])
            job_timestamps={}
            for attr in ("creation_date","start_time","end_time","done"):
                try: value=getattr(job,attr,None)
                except Exception as metadata_exc:
                    provider_metadata_errors.append({"field":attr,"error_type":type(metadata_exc).__name__})
                    value=None
                if callable(value):
                    try: value=value()
                    except Exception as metadata_exc:
                        provider_metadata_errors.append({"field":attr,"error_type":type(metadata_exc).__name__})
                        value=None
                if value is not None:
                    job_timestamps[attr]=str(value)
            try: job_session=getattr(job,"session_id",None)
            except Exception as metadata_exc:
                provider_metadata_errors.append({"field":"session_id","error_type":type(metadata_exc).__name__})
                job_session=None
            if callable(job_session):
                try: job_session=job_session()
                except Exception as metadata_exc:
                    provider_metadata_errors.append({"field":"session_id","error_type":type(metadata_exc).__name__})
                    job_session=None
            observed_backend=None
            try: backend_value=getattr(job,"backend",None)
            except Exception as metadata_exc:
                provider_metadata_errors.append({"field":"backend","error_type":type(metadata_exc).__name__})
                backend_value=None
            if callable(backend_value):
                try: backend_value=backend_value()
                except Exception as metadata_exc:
                    provider_metadata_errors.append({"field":"backend","error_type":type(metadata_exc).__name__})
                    backend_value=None
            if backend_value is not None:
                try: observed_backend=getattr(backend_value,"name",None)
                except Exception as metadata_exc:
                    provider_metadata_errors.append({"field":"backend_name","error_type":type(metadata_exc).__name__})
                    observed_backend=None
                if callable(observed_backend):
                    try: observed_backend=observed_backend()
                    except Exception as metadata_exc:
                        provider_metadata_errors.append({"field":"backend_name","error_type":type(metadata_exc).__name__})
                        observed_backend=None
                if observed_backend is None: observed_backend=str(backend_value)
            provider_metadata={"schema_version":1,"job_id":row["job_id"],
                "campaign_id":plan["campaign_id"],"sequence_no":sequence_no,
                "batch_id":batch["batch_id"],"account_label":plan["account_label"],
                "backend":observed_backend or plan["backend"],"job_status":state,
                "raw_response_sha256":response_hash,
                "metrics":_redact_runtime_metadata(metrics),"timestamps":job_timestamps,
                "runtime_session_id":_redact_runtime_metadata(job_session),
                "metadata_errors":provider_metadata_errors}
            metadata_json=_json(provider_metadata)
            metadata_hash=hashlib.sha256(metadata_json.encode("utf-8")).hexdigest()
            metadata_path=contained(archive_base,plan["campaign_id"],"provider_metadata",
                f"sequence_{sequence_no:03d}",f"{row['job_id']}_{metadata_hash}.json")
            _write_immutable_json(metadata_path,metadata_json)
            # Scientific/schema validation starts only after raw result and
            # provider metadata archives have both been persisted.
            validation = capture_runtime_result(result,row["treatment_id"])
            raw_result={"schema_version":2,"synthetic":False,
                "campaign_id":plan["campaign_id"],"calibration_batch_sequence":sequence_no,
                "calibration_batch_id":batch["batch_id"],"runtime_session_id":_redact_runtime_metadata(job_session),
                "treatment_id":row["treatment_id"],"job_id":row["job_id"],
                "account_label":plan["account_label"],"instance_ref":batch["instance_ref"],"backend":plan["backend"],
                "source_run":plan["source_run"],"input_hashes":plan["input_hashes"],
                "shots_per_pub":plan["shots_per_pub"],"treatment_flags":treatment["flags"],
                "treatment_options_repr":options_repr,"job_status":state,
                "observed_backend":observed_backend,
                "provider_response_path":str(response_path),"provider_response_sha256":response_hash,
                "provider_metadata_path":str(metadata_path),"provider_metadata_sha256":metadata_hash,
                "timestamps":job_timestamps,"qpu_usage_seconds":float(usage) if usage is not None else None,
                "usage_source":"provider_job_metrics","provider_metrics":_redact_runtime_metadata(metrics),
                "provider_metadata_errors":provider_metadata_errors,
                "pub_results":validation["records"],"pub_metadata":validation["pub_metadata"],
                "expectation_count_per_pub":7,"uncertainty_status":validation["uncertainty_status"],
                "invalid_expectation_pubs":validation["invalid_expectation_pubs"],
                "expectation_errors":validation["expectation_errors"],
                "invalid_uncertainty_pubs":validation["invalid_uncertainty_pubs"],
                "uncertainty_errors":validation["uncertainty_errors"],
                "expectation_failure_rate":validation["expectation_failure_rate"],
                "uncertainty_failure_rate":validation["uncertainty_failure_rate"],
                "invalid_pub_failure_rate":validation["invalid_pub_failure_rate"]}
            archive=contained(archive_base,plan["campaign_id"],"raw_results",
                              f"sequence_{sequence_no:03d}",f"{row['job_id']}.json")
            invalid_reason=None
            if validation["pub_count"]!=int(plan["chunk_n"]): invalid_reason="INCOMPLETE_PUBS"
            elif validation["invalid_expectation_pubs"]: invalid_reason="CORRUPT_EXPECTATION_VALUES"
            elif validation["invalid_uncertainty_pubs"]: invalid_reason="INVALID_NUMERIC_UNCERTAINTY"
            elif usage is None or usage<=0: invalid_reason="MISSING_PROVIDER_USAGE"
            elif options_repr!=treatment["options_repr"]: invalid_reason="TREATMENT_OPTIONS_MISMATCH"
            elif observed_backend is not None and observed_backend!=plan["backend"]: invalid_reason="BACKEND_MISMATCH"
            if invalid_reason:
                structured_error={"schema_version":1,"source":"runtime_result_validation",
                    "failure_class":invalid_reason,"pub_count":validation["pub_count"],
                    "expected_pub_count":int(plan["chunk_n"]),
                    "invalid_expectation_pubs":validation["invalid_expectation_pubs"],
                    "expectation_errors":validation.get("expectation_errors",[]),
                    "invalid_uncertainty_pubs":validation["invalid_uncertainty_pubs"],
                    "uncertainty_status":validation["uncertainty_status"],
                    "automatic_resubmit":False}
                result_hash=registry.archive_unvalidated_result(plan["campaign_id"],sequence_no,
                    row["treatment_id"],row["job_id"],raw_result,archive)
                registry.fail_job(plan["campaign_id"],sequence_no,row["treatment_id"],
                    job_id=row["job_id"],failure_class=invalid_reason,
                    validation_error=structured_error,
                    qpu_usage_seconds=float(usage) if usage is not None and usage>0 else None)
                reports.append({"treatment_id":row["treatment_id"],"job_id":row["job_id"],
                    "status":"FAILED_VALIDATION","failure_class":invalid_reason,
                    "pub_count":validation["pub_count"],"invalid_expectation_pubs":validation["invalid_expectation_pubs"],
                    "invalid_uncertainty_pubs":validation["invalid_uncertainty_pubs"],
                    "uncertainty_status":validation["uncertainty_status"],"result_sha256":result_hash,
                    "archive_path":str(archive),"automatic_resubmit":False});continue
            validation["validation"]="PASS"
            evidence = {"schema_version": 1, "campaign_id": plan["campaign_id"],
                "calibration_batch_sequence": sequence_no, "calibration_batch_id": batch["batch_id"],
                "treatment_id": row["treatment_id"], "job_id": row["job_id"],
                "account_label": plan["account_label"], "instance_ref": batch["instance_ref"],
                "backend": plan["backend"], "source_run": plan["source_run"],
                "input_hashes": plan["input_hashes"], "physical_qubits": plan["physical_qubits"],
                "circuit_parameter_count": plan["circuit_parameter_count"],
                "theta_count": plan["theta_count"], "observable_count": plan["observable_count"],
                "shots_per_pub": plan["shots_per_pub"], "pub_count": validation["pub_count"],
                "expectation_count_per_pub": validation["expectation_count_per_pub"],
                "pub_validation": validation["validation"], "treatment_flags": treatment["flags"],
                "treatment_options_repr": options_repr,
                "batch_workload": "one_batch_eight_jobs_M0_M7", "job_status": state,
                "observed_backend":observed_backend,
                "qpu_usage_seconds": float(usage), "usage_source": "provider_job_metrics",
                "usage_verified": True, "collected_utc": utc_now(),
                "uncertainty_status":validation["uncertainty_status"],
                "job_timestamps":job_timestamps,"runtime_session_id":_redact_runtime_metadata(job_session),
                "synthetic": False, "result_values_persisted": True}
            result_hash=registry.save_collected_result(plan["campaign_id"],sequence_no,
                row["treatment_id"],row["job_id"],evidence,raw_result,archive)
            reports.append({"treatment_id": row["treatment_id"], "job_id": row["job_id"],
                            "status": "VALIDATED", "qpu_usage_seconds": float(usage),
                            "pub_count": validation["pub_count"],
                            "uncertainty_status":validation["uncertainty_status"],
                            "result_sha256":result_hash,"archive_path":str(archive)})
        except Exception as exc:
            if isinstance(exc,CalibrationTransientReadError):
                registry.record_event(plan["campaign_id"],sequence_no,"CALIBRATION_TRANSIENT_READ_FAILURE",
                    {"treatment_id":row["treatment_id"],"job_id":row["job_id"],"error_type":type(exc).__name__,"automatic_resubmit":False})
                reports.append({"treatment_id": row["treatment_id"], "job_id": row["job_id"],
                                "status": "RETRYABLE_READ_ERROR", "failure_class": "NETWORK",
                                "error_type": type(exc).__name__, "automatic_resubmit": False})
            else:
                registry.fail_job(plan["campaign_id"], sequence_no, row["treatment_id"],
                    job_id=row["job_id"], failure_class="RESULT_OR_METADATA_INVALID:" + type(exc).__name__,
                    validation_error={"schema_version":1,"source":"collector_exception",
                        "error_type":type(exc).__name__,"automatic_resubmit":False})
                reports.append({"treatment_id": row["treatment_id"], "job_id": row["job_id"],
                                "status": "FAILED", "failure_class": "RESULT_OR_METADATA_INVALID",
                                "error_type": type(exc).__name__, "automatic_resubmit": False})
    return {"campaign_id": plan["campaign_id"], "sequence_no": sequence_no,
            "batch_status": registry.status(plan["campaign_id"])["batches"][sequence_no-1]["status"],
            "jobs": reports, "submission_performed": False, "cancellation_performed": False}


def submit_calibration_batch(plan: dict[str, Any], sequence_no: int, *, registry: CalibrationRegistry,
                             service, preflight_report: dict[str, Any], budget_cap_seconds: float,
                             safety_margin_seconds: float, authorize_calibration: bool,
                             approved_treatments: tuple[str, ...], root=None,
                             batch_factory=None, estimator_factory=None) -> list[str]:
    """Submit one explicitly approved M0-M7 calibration Batch.

    This is intentionally not used by tests. A budget cap is a local spend
    authorization/reservation; IBM does not enforce a per-campaign QPU charge
    ceiling, so provider usage can exceed it while a submitted job is running.
    """
    if not authorize_calibration:
        raise RuntimeError("Calibration submission disabled; explicit authorization is required")
    if tuple(approved_treatments) != TREATMENTS:
        raise RuntimeError("Explicit approval is required for each M0-M7 calibration job in order")
    if not math.isfinite(float(safety_margin_seconds)) or safety_margin_seconds < 0:
        raise ValueError("Safety margin must be finite and nonnegative")
    if float(budget_cap_seconds) != float(plan["budget_cap_seconds"]):
        raise RuntimeError("Submitted calibration budget cap differs from the registered plan")
    registered=registry.status(plan["campaign_id"])
    if not registered.get("exists") or registered["plan"].get("plan_sha256")!=plan.get("plan_sha256"):
        raise RuntimeError("Calibration plan does not match the isolated registry")
    spent=sum(float(row["qpu_usage_seconds"] or 0) for row in registered["jobs"])
    campaign_cap_remaining=float(plan["budget_cap_seconds"])-spent
    if campaign_cap_remaining<=0:
        raise RuntimeError("Cumulative calibration budget cap has been consumed")
    report_gate = validate_calibration_preflight(plan, preflight_report,
        safety_margin_seconds=safety_margin_seconds,budget_cap_override=campaign_cap_remaining)
    if not report_gate["eligible"]:
        raise RuntimeError("Fresh read-only calibration preflight refused: " + "; ".join(report_gate["reasons"]))
    from final_configuration import quota_eligibility
    if not quota_eligibility(campaign_cap_remaining)["eligible"]:
        raise RuntimeError("Authorized calibration budget is below the original Batch threshold")
    if not service or not callable(getattr(service, "usage", None)):
        raise RuntimeError("Provider usage must be re-read immediately before calibration submission")
    from quota import provider_quota_fields
    usage_response = service.usage()
    remaining=provider_quota_fields(usage_response)["remaining_seconds"]
    if not quota_eligibility(min(remaining,campaign_cap_remaining))["eligible"]:
        raise RuntimeError("Immediate verified quota is below original Batch threshold")
    active = service.active_instance() if callable(getattr(service, "active_instance", None)) else None
    if not active:
        raise RuntimeError("Cannot verify active IBM instance before calibration")
    from read_only_preflight import _instance_ref
    if _instance_ref(str(active)) != preflight_report.get("configured_instance_ref"):
        raise RuntimeError("Active IBM instance differs from fresh preflight")
    from experiment_manifest import build_manifest
    from batch_scheduler import preflight_batch
    manifest = build_manifest(root, n=int(plan["chunk_n"]), replicate_blocks=1,
                              shots=int(plan["shots_per_pub"]), experiment_id="runtime_calibration")
    preflight_batch(manifest, service, root=root)
    backend=service.backend(plan["backend"])
    if getattr(backend,"name",plan["backend"])!=plan["backend"]:
        raise RuntimeError("Runtime returned a different backend than the frozen calibration plan")
    backend_status=backend.status() if callable(getattr(backend,"status",None)) else None
    if getattr(backend_status,"operational",None) is not True:
        raise RuntimeError("Backend ibm_fez is not currently confirmed operational")
    from vqe_adapter import verify_frozen_inputs, treatment_options
    circuit, observables, theta, physical, _ideal, _version = verify_frozen_inputs(root)
    if len(theta) != 21 or len(observables) != 7 or tuple(physical) != tuple(plan["physical_qubits"]):
        raise RuntimeError("Frozen run_02 source did not pass the calibration preflight")
    if batch_factory is None or estimator_factory is None:
        from low_memory_runtime import remote_runtime
        remote_runtime()
        from qiskit_ibm_runtime import Batch, EstimatorV2
        batch_factory = batch_factory or Batch
        estimator_factory = estimator_factory or EstimatorV2
    if len(approved_treatments) != 8 or set(approved_treatments) != set(TREATMENTS):
        raise RuntimeError("Exactly eight individually approved calibration treatments are required")
    gate = {**report_gate, "eligible": True, "reasons": []}
    registry.reserve_batch(plan, sequence_no, gate=gate,
                           instance_ref=str(preflight_report["configured_instance_ref"]))
    returned = []
    current_treatment = None
    batch_id = None
    try:
        with batch_factory(backend=backend) as batch:
            batch_id = str(getattr(batch, "session_id", "") or "") or None
            registry.begin_batch(plan["campaign_id"], sequence_no, batch_id)
            for treatment in TREATMENTS:
                current_treatment = treatment
                registry.mark_job_submitting(plan["campaign_id"], sequence_no, treatment)
                options = treatment_options(treatment, int(plan["shots_per_pub"]), root)
                job = estimator_factory(mode=batch, options=options).run([(circuit, observables)] * int(plan["chunk_n"]))
                job_id = str(job.job_id())
                registry.persist_job_id(plan["campaign_id"], sequence_no, treatment, job_id)
                returned.append(job_id)
    except Exception as exc:
        registry.mark_interrupted_unknown(plan["campaign_id"], sequence_no)
        raise RuntimeError("Calibration Batch is incomplete/ambiguous; reconcile recorded IDs; automatic retry is disabled") from exc
    registry.finish_submission(plan["campaign_id"],sequence_no)
    return returned
