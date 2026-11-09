"""Durable SQLite submission ledger with idempotency and recovery events."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


from contextlib import contextmanager, nullcontext
from batch_progression import evaluate_batch_progression, valid_scientific_values, replacement_provenance_verified
from datetime import datetime, timezone, timedelta
import json
import math
import re
from pathlib import Path
import sqlite3
from typing import Any
from account_config import ACCOUNT_LABELS

ACCOUNT_LABELS_SQL = ",".join("'" + label + "'" for label in ACCOUNT_LABELS)
ACTIVE_STATES = ("RESERVED", "SUBMITTING", "SUBMITTED", "UNKNOWN")
FINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED", "CONFIRMED_NOT_SUBMITTED")

# Authoritative lifecycle policy. Execution completion does not imply PUB validity;
# batch_progression derives scientific readiness from immutable selected evidence.
JOB_TRANSITIONS = {
    "RESERVED":{"RESERVED","SUBMITTING","CONFIRMED_NOT_SUBMITTED"},
    "SUBMITTING":{"SUBMITTING","SUBMITTED","UNKNOWN","CONFIRMED_NOT_SUBMITTED"},
    "SUBMITTED":{"SUBMITTED","COMPLETED","FAILED","CANCELLED","UNKNOWN"},
    "UNKNOWN":{"UNKNOWN","SUBMITTED","CONFIRMED_NOT_SUBMITTED","COMPLETED","FAILED","CANCELLED"},
    **{state:{state} for state in FINAL_STATES},
}
REPLACEMENT_TRANSITIONS = {
    "PLANNED":{"PLANNED","RESERVED","SUBMITTING"},
    "RESERVED":{"RESERVED","SUBMITTING","UNKNOWN"},
    "SUBMITTING":{"SUBMITTING","UNKNOWN","SUBMITTED","PLANNED"},
    "UNKNOWN":{"UNKNOWN","SUBMITTED","PLANNED"},
    "SUBMITTED":{"SUBMITTED","DONE","VALIDATED","FAILED","UNKNOWN"},
    "DONE":{"DONE","VALIDATED","FAILED"},
    "VALIDATED":{"VALIDATED"}, "FAILED":{"FAILED"},
}


def validate_state_transition(kind, current, state):
    policy=JOB_TRANSITIONS if kind=='job' else REPLACEMENT_TRANSITIONS
    if state not in policy.get(current,set()):
        raise ValueError(f"Invalid {kind} state transition: {current} -> {state}")


def validate_provider_identity(job_id, existing=None):
    if job_id is not None:
        if not isinstance(job_id,str) or not job_id.strip() or job_id.strip().lower() in {'none','null'}:
            raise ValueError('Provider job ID must be a nonempty identity')
        if existing and existing!=job_id:
            raise RuntimeError('Recorded provider job ID is immutable')


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_NO_SUBMISSION_EVENTS={"CONFIRMED_NO_SUBMISSION","BATCH_NOT_ATTEMPTED",
    "ADAPTIVE_JOB_NOT_ATTEMPTED","LOCAL_RESERVATION_RECOVERED_WITHOUT_SUBMISSION"}


def _retry_resolution(db,logical_block_id: str,*,target_id: str | None=None,
                      treatments: set[str] | None=None,
                      require_block_status: bool=True) -> dict[str,Any]:
    """Derive an internal retry flag only from eight audited no-submit rows."""
    block=db.execute("SELECT status,batch_id FROM experiment_blocks WHERE logical_block_id=?",
        (logical_block_id,)).fetchone()
    if block is None or (require_block_status and block["status"]!="CONFIRMED_NOT_SUBMITTED"):
        return {"state":"NOT_RETRYABLE","retryable":False}
    query="SELECT * FROM jobs WHERE logical_block_id=?"
    params=[logical_block_id]
    if target_id is not None:
        query+=" AND target_id=?";params.append(target_id)
    history=db.execute(query+" ORDER BY treatment_id,attempt",params).fetchall()
    latest={}
    for row in history:
        old=latest.get(row["treatment_id"])
        if old is None or row["attempt"]>old["attempt"]:latest[row["treatment_id"]]=row
    expected=treatments or set(latest)
    if (block["batch_id"] is not None or set(latest)!=expected or len(expected)!=8 or
            any(row["job_id"] is not None or row["batch_id"] is not None for row in history)):
        return {"state":"NOT_RETRYABLE","retryable":False}
    for row in latest.values():
        if row["submission_state"]!="CONFIRMED_NOT_SUBMITTED" or row["quota_reserved_seconds"]!=0:
            return {"state":"NOT_RETRYABLE","retryable":False}
        metadata=json.loads(row["metadata_json"] or "{}")
        if metadata.get("batch_context_entered_utc") or metadata.get("batch_id"):
            return {"state":"NOT_RETRYABLE","retryable":False}
        audit=db.execute("SELECT event_type FROM recovery_events WHERE job_row_id=?",(row["id"],)).fetchall()
        if not any(item["event_type"] in _NO_SUBMISSION_EVENTS for item in audit):
            return {"state":"NOT_RETRYABLE","retryable":False}
    return {"state":"RECONCILED_NOT_SUBMITTED","retryable":True}


class JobRegistry:
    def __init__(self, database: str | Path, *, readonly: bool = False):
        self.database = Path(database)
        self.readonly=readonly
        if not readonly:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        elif not self.database.is_file():
            raise FileNotFoundError(self.database)

    @staticmethod
    def default_database(root: str | Path | None = None) -> Path:
        """Return the protected registry location under mitigation output; do not create it."""
        import sys
        vqe_code = Path(__file__).resolve().parents[2] / "01_vqe"
        if str(vqe_code) not in sys.path:
            sys.path.insert(0, str(vqe_code))
        from repository_paths import mitigation_output
        return mitigation_output(root, "03_IBM_execution_records", "ai_assistant", "job_registry.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        target=f"file:{self.database.resolve().as_posix()}?mode=ro" if self.readonly else str(self.database)
        connection = sqlite3.connect(target, timeout=30, isolation_level=None, uri=self.readonly)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if self.readonly:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        self._migrate_account_label_constraints()
        with self._connection() as db:
            db.executescript(f"""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    logical_block_id TEXT NOT NULL,
                    account_label TEXT NOT NULL CHECK(account_label IN ({ACCOUNT_LABELS_SQL})),
                    backend TEXT NOT NULL DEFAULT 'ibm_fez',
                    job_id TEXT,
                    batch_id TEXT,
                    treatment_id TEXT NOT NULL,
                    submission_state TEXT NOT NULL,
                    input_hashes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    quota_reserved_seconds REAL NOT NULL DEFAULT 0 CHECK(quota_reserved_seconds >= 0),
                    qpu_usage_seconds REAL CHECK(qpu_usage_seconds IS NULL OR qpu_usage_seconds >= 0),
                    metadata_json TEXT NOT NULL DEFAULT '{{}}',
                    attempt INTEGER NOT NULL DEFAULT 1,
                    n INTEGER,
                    replicate_blocks INTEGER,
                    UNIQUE(logical_block_id, treatment_id, attempt)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS unique_active_treatment
                    ON jobs(logical_block_id, treatment_id)
                    WHERE submission_state IN ('RESERVED','SUBMITTING','SUBMITTED','UNKNOWN','COMPLETED');
                CREATE UNIQUE INDEX IF NOT EXISTS unique_job_id ON jobs(job_id) WHERE job_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS recovery_events (
                    id INTEGER PRIMARY KEY,
                    job_row_id INTEGER NOT NULL REFERENCES jobs(id),
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_targets (
                    target_id TEXT PRIMARY KEY, target_n INTEGER NOT NULL, chunk_n INTEGER NOT NULL,
                    manifest_sha256 TEXT NOT NULL, input_hashes_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_blocks (
                    logical_block_id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL REFERENCES experiment_targets(target_id),
                    sequence_no INTEGER NOT NULL,
                    account_label TEXT NOT NULL CHECK(account_label IN ({ACCOUNT_LABELS_SQL})),
                    backend TEXT NOT NULL, batch_id TEXT, status TEXT NOT NULL DEFAULT 'RESERVED',
                    chunk_n INTEGER NOT NULL, target_n INTEGER NOT NULL, input_hashes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, quota_reserved_seconds REAL NOT NULL DEFAULT 0,
                    runtime_upper_bound_seconds REAL, safety_margin_seconds REAL NOT NULL DEFAULT 0,
                    preflight_checked_at TEXT, quota_scope TEXT
                );
                CREATE TABLE IF NOT EXISTS batch_recovery_reviews (
                    review_id INTEGER PRIMARY KEY, logical_block_id TEXT NOT NULL REFERENCES experiment_blocks(logical_block_id),
                    batch_execution_status TEXT NOT NULL CHECK(batch_execution_status IN ('COMPLETE','INCOMPLETE')),
                    recovery_status TEXT NOT NULL CHECK(recovery_status IN ('INVESTIGATING','RECOVERED','UNRECOVERABLE')),
                    completeness_policy_version TEXT NOT NULL, valid_fraction_by_treatment_json TEXT NOT NULL,
                    raw_archives_verified INTEGER NOT NULL, provenance_verified INTEGER NOT NULL,
                    analysis_verified INTEGER NOT NULL, figures_verified INTEGER NOT NULL,
                    report_verified INTEGER NOT NULL DEFAULT 0, progression_eligible INTEGER NOT NULL,
                    review_json TEXT NOT NULL, reviewed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_monitor_state (
                    logical_block_id TEXT PRIMARY KEY, batch_id TEXT, account_label TEXT NOT NULL,
                    backend TEXT NOT NULL, status TEXT NOT NULL, checked_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_monitor_events (
                    id INTEGER PRIMARY KEY, logical_block_id TEXT NOT NULL, batch_id TEXT,
                    job_row_id INTEGER, event_type TEXT NOT NULL, details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_usage_snapshots (
                    id INTEGER PRIMARY KEY, account_label TEXT NOT NULL, consumed_seconds REAL NOT NULL,
                    quota_scope TEXT NOT NULL, source TEXT NOT NULL, checked_at TEXT NOT NULL,
                    logical_block_id TEXT
                );
                CREATE TABLE IF NOT EXISTS independent_n_campaigns (
                    campaign_id TEXT PRIMARY KEY,manifest_sha256 TEXT NOT NULL,manifest_json TEXT NOT NULL,
                    state TEXT NOT NULL,current_level_index INTEGER NOT NULL DEFAULT 0,
                    checkpoint_json TEXT NOT NULL DEFAULT '{{}}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS independent_n_campaign_levels (
                    campaign_id TEXT NOT NULL REFERENCES independent_n_campaigns(campaign_id),
                    level_index INTEGER NOT NULL,target_n INTEGER NOT NULL,target_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,state TEXT NOT NULL,checkpoint_json TEXT NOT NULL DEFAULT '{{}}',
                    updated_at TEXT NOT NULL,PRIMARY KEY(campaign_id,level_index),UNIQUE(campaign_id,target_id)
                );
                CREATE TABLE IF NOT EXISTS independent_n_campaign_events (
                    id INTEGER PRIMARY KEY,campaign_id TEXT NOT NULL,level_index INTEGER,
                    event_type TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pub_validation_records (
                    job_row_id INTEGER NOT NULL REFERENCES jobs(id),
                    experiment_id TEXT NOT NULL,logical_block_id TEXT NOT NULL,batch_id TEXT NOT NULL,
                    treatment_id TEXT NOT NULL,pub_index INTEGER NOT NULL,
                    expectation_valid INTEGER NOT NULL,uncertainty_valid INTEGER,
                    uncertainty_state TEXT NOT NULL,error_codes_json TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,expectation_values_json TEXT,
                    standard_deviations_json TEXT,pub_metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,PRIMARY KEY(job_row_id,pub_index));
                CREATE TABLE IF NOT EXISTS validated_pub_values (
                    job_row_id INTEGER NOT NULL REFERENCES jobs(id),
                    experiment_id TEXT NOT NULL,logical_block_id TEXT NOT NULL,batch_id TEXT NOT NULL,
                    treatment_id TEXT NOT NULL,pub_index INTEGER NOT NULL,
                    expectation_values_json TEXT NOT NULL,standard_deviations_json TEXT,
                    uncertainty_state TEXT NOT NULL,source_sha256 TEXT NOT NULL,
                    input_hashes_json TEXT NOT NULL,account_label TEXT NOT NULL,backend TEXT NOT NULL,
                    job_id TEXT NOT NULL,created_at TEXT NOT NULL,
                    PRIMARY KEY(job_row_id,pub_index));
                CREATE TABLE IF NOT EXISTS pub_replacement_jobs (
                    replacement_id INTEGER PRIMARY KEY,
                    target_id TEXT NOT NULL REFERENCES experiment_targets(target_id),
                    logical_block_id TEXT NOT NULL REFERENCES experiment_blocks(logical_block_id),
                    treatment_id TEXT NOT NULL,
                    source_pub_index INTEGER NOT NULL CHECK(source_pub_index>0),
                    logical_pub_index INTEGER NOT NULL CHECK(logical_pub_index>0),
                    attempt INTEGER NOT NULL CHECK(attempt>0),
                    account_label TEXT NOT NULL CHECK(account_label IN ({ACCOUNT_LABELS_SQL})),
                    source_account_label TEXT,
                    backend TEXT NOT NULL CHECK(backend='ibm_fez'),
                    state TEXT NOT NULL CHECK(state IN ('PLANNED','RESERVED','SUBMITTING','UNKNOWN','SUBMITTED','DONE','VALIDATED','FAILED')),
                    job_id TEXT,batch_id TEXT,
                    input_hashes_json TEXT NOT NULL,options_json TEXT NOT NULL,
                    quota_reserved_seconds REAL NOT NULL DEFAULT 0 CHECK(quota_reserved_seconds>=0),
                    qpu_usage_seconds REAL,
                    raw_archive_path TEXT,raw_archive_sha256 TEXT,
                    expectation_values_json TEXT,standard_deviations_json TEXT,
                    uncertainty_state TEXT,error_codes_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{{}}',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    UNIQUE(target_id,treatment_id,logical_pub_index,attempt));
                CREATE UNIQUE INDEX IF NOT EXISTS replacement_provider_pub_identity
                    ON pub_replacement_jobs(job_id,COALESCE(json_extract(metadata_json,'$.provider_pub_index'),1))
                    WHERE job_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS replacement_slot_active
                    ON pub_replacement_jobs(target_id,treatment_id,logical_pub_index)
                    WHERE state IN ('PLANNED','RESERVED','SUBMITTING','UNKNOWN','SUBMITTED','DONE','VALIDATED');
                CREATE TABLE IF NOT EXISTS pub_replacement_events (
                    event_id INTEGER PRIMARY KEY,replacement_id INTEGER NOT NULL REFERENCES pub_replacement_jobs(replacement_id),
                    event_type TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pub_archive_references (
                    reference_id INTEGER PRIMARY KEY,job_row_id INTEGER NOT NULL REFERENCES jobs(id),
                    pub_index INTEGER NOT NULL CHECK(pub_index>0),source_job_id TEXT NOT NULL,
                    treatment_id TEXT NOT NULL,archive_path TEXT NOT NULL,archive_sha256 TEXT NOT NULL,
                    source_kind TEXT NOT NULL,created_at TEXT NOT NULL,
                    UNIQUE(job_row_id,pub_index));
                CREATE TRIGGER IF NOT EXISTS pub_archive_references_no_update
                    BEFORE UPDATE ON pub_archive_references
                    BEGIN SELECT RAISE(ABORT,'PUB archive references are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS pub_archive_references_no_delete
                    BEFORE DELETE ON pub_archive_references
                    BEGIN SELECT RAISE(ABORT,'PUB archive references are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS pub_validation_records_no_update
                    BEFORE UPDATE ON pub_validation_records
                    BEGIN SELECT RAISE(ABORT,'PUB validation records are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS pub_validation_records_no_delete
                    BEFORE DELETE ON pub_validation_records
                    BEGIN SELECT RAISE(ABORT,'PUB validation records are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS validated_pub_values_no_update
                    BEFORE UPDATE ON validated_pub_values
                    BEGIN SELECT RAISE(ABORT,'validated PUB values are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS validated_pub_values_no_delete
                    BEFORE DELETE ON validated_pub_values
                    BEGIN SELECT RAISE(ABORT,'validated PUB values are immutable'); END;
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            for name, declaration in (("backend", "TEXT NOT NULL DEFAULT 'ibm_fez'"),
                                      ("batch_id", "TEXT"), ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
                                      ("n", "INTEGER"), ("replicate_blocks", "INTEGER"),
                                      ("target_id", "TEXT"), ("target_n", "INTEGER"), ("chunk_n", "INTEGER"),
                                      ("submitted_pub_count", "INTEGER NOT NULL DEFAULT 0"),
                                      ("completed_pub_count", "INTEGER NOT NULL DEFAULT 0"),
                                      ("collected_pub_count", "INTEGER NOT NULL DEFAULT 0"),
                                      ("validated_pub_count", "INTEGER NOT NULL DEFAULT 0"),
                                      ("completed_at", "TEXT"), ("collected_at", "TEXT"), ("validated_at", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            replacement_columns={row["name"] for row in db.execute("PRAGMA table_info(pub_replacement_jobs)")}
            if "source_account_label" not in replacement_columns:
                db.execute("ALTER TABLE pub_replacement_jobs ADD COLUMN source_account_label TEXT")
            db.execute("UPDATE pub_replacement_jobs SET source_account_label=account_label WHERE source_account_label IS NULL")
            if "treatment_validation_state" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN treatment_validation_state TEXT NOT NULL DEFAULT 'NOT_VALIDATED'")
            if "invalid_pub_count" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN invalid_pub_count INTEGER NOT NULL DEFAULT 0")
            review_columns={row["name"] for row in db.execute("PRAGMA table_info(batch_recovery_reviews)")}
            if "report_verified" not in review_columns:
                db.execute("ALTER TABLE batch_recovery_reviews ADD COLUMN report_verified INTEGER NOT NULL DEFAULT 0")

    def _migrate_account_label_constraints(self) -> None:
        """Atomically migrate account/PUB identity constraints without changing row values."""
        with self._connection() as db:
            tables={row["name"]:row["sql"] for row in db.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' AND name IN ('jobs','experiment_blocks','pub_replacement_jobs')")}
            pattern=r"CHECK\s*\(\s*account_label\s+IN\s*\(([^)]+)\)\s*\)"
            old_checks={}
            for name,sql in tables.items():
                match=re.search(pattern,sql or "",re.I)
                if match:
                    labels=set(re.findall(r"'([^']+)'",match.group(1)))
                    if labels < set(ACCOUNT_LABELS):
                        old_checks[name]=match.group(0)
            targets=list(old_checks)
            if "job_id TEXT UNIQUE" in tables.get("pub_replacement_jobs","") and "pub_replacement_jobs" not in targets:
                targets.append("pub_replacement_jobs")
            if not targets:
                return
            objects=list(db.execute(
                "SELECT type,name,sql,tbl_name FROM sqlite_master WHERE type IN ('index','trigger') "
                "AND tbl_name IN ('jobs','experiment_blocks','pub_replacement_jobs') AND sql IS NOT NULL"))
            original_counts={name:db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in targets}
            db.execute("PRAGMA foreign_keys=OFF")
            try:
                db.execute("BEGIN IMMEDIATE")
                for name in targets:
                    temp=name+"_account_labels_migration"
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(temp,)).fetchone():
                        raise RuntimeError("An unfinished account-label migration requires manual review")
                    sql=re.sub(r'^(CREATE\s+TABLE\s+)(?:"'+re.escape(name)+r'"|'+re.escape(name)+r')(?=\s*\()',
                        lambda match: match.group(1)+temp,tables[name],count=1,flags=re.I)
                    if name in old_checks:sql=sql.replace(old_checks[name],f"CHECK(account_label IN ({ACCOUNT_LABELS_SQL}))")
                    if name=="pub_replacement_jobs":sql=sql.replace("job_id TEXT UNIQUE","job_id TEXT")
                    db.execute(sql)
                    columns=[row["name"] for row in db.execute(f"PRAGMA table_info({name})")]
                    quoted=",".join('"'+column.replace('"','""')+'"' for column in columns)
                    db.execute(f"INSERT INTO {temp} ({quoted}) SELECT {quoted} FROM {name}")
                    db.execute(f"DROP TABLE {name}")
                    db.execute(f"ALTER TABLE {temp} RENAME TO {name}")
                for item in objects:
                    if item["tbl_name"] in targets:
                        db.execute(item["sql"])
                if 'pub_replacement_jobs' in targets:
                    # Commit the replacement identity constraint with the rebuilt
                    # table. A failed index creation must restore the old schema.
                    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS replacement_provider_pub_identity
                        ON pub_replacement_jobs(job_id,COALESCE(json_extract(metadata_json,'$.provider_pub_index'),1))
                        WHERE job_id IS NOT NULL""")
                for name,count in original_counts.items():
                    if db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]!=count:
                        raise RuntimeError("Account-label migration changed historical row counts")
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("Account-label migration detected foreign-key violations")
                db.execute("PRAGMA user_version=2")
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, *, logical_block_id: str, account_label: str, treatment_id: str,
                input_hashes: dict[str, str], quota_seconds: float, actual_usage_seconds: float,
                n: int, replicate_blocks: int,
                budget_seconds: float = 600.0, safety_margin_seconds: float = 0.0,
                backend: str = "ibm_fez") -> int:
        return self.reserve_batch(logical_block_id=logical_block_id, account_label=account_label,
            backend=backend, treatments=[treatment_id], input_hashes=input_hashes,
            quota_seconds_per_job=quota_seconds, actual_usage_seconds=actual_usage_seconds,
            budget_seconds=budget_seconds, safety_margin_seconds=safety_margin_seconds,
            n=n,replicate_blocks=replicate_blocks)[treatment_id]

    def reserve_batch(self, *, logical_block_id: str, account_label: str, backend: str,
                      treatments: list[str], input_hashes: dict[str, str],
                      quota_seconds_per_job: float, actual_usage_seconds: float,
                      budget_seconds: float = 600.0, safety_margin_seconds: float = 0.0,
                      n: int, replicate_blocks: int,
                      quota_seconds_by_treatment: dict[str, float] | None = None,
                      adaptive_target: dict[str, Any] | None = None,
                      available_budget_seconds: float | None = None,
                      expected_active_reserved_seconds: float | None = None) -> dict[str, int]:
        if account_label not in ACCOUNT_LABELS:
            raise ValueError(f"Unknown account label: {account_label}")
        for value, label in ((quota_seconds_per_job, "quota_seconds_per_job"), (actual_usage_seconds, "actual_usage_seconds"), (budget_seconds, "budget_seconds"), (safety_margin_seconds, "safety_margin_seconds")):
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{label} must be finite and nonnegative")
        if not logical_block_id or not backend or not treatments or len(set(treatments)) != len(treatments) or not input_hashes:
            raise ValueError("block ID, backend, unique treatments, and frozen input hashes are required")
        if isinstance(n, bool) or not isinstance(n, int) or n < 1 or isinstance(replicate_blocks, bool) or not isinstance(replicate_blocks, int) or replicate_blocks < 1:
            raise ValueError("n and replicate_blocks must be positive integers")
        if quota_seconds_by_treatment is not None:
            if set(quota_seconds_by_treatment) != set(treatments):
                raise ValueError("Per-treatment quota reservations must cover exactly the reserved treatments")
            for treatment_id, amount in quota_seconds_by_treatment.items():
                if not math.isfinite(float(amount)) or float(amount) < 0:
                    raise ValueError(f"Invalid quota reservation for treatment {treatment_id}")
        else:
            quota_seconds_by_treatment = {name: float(quota_seconds_per_job) for name in treatments}
        from final_configuration import quota_eligibility
        estimate_by_treatment=dict(quota_seconds_by_treatment)
        admission=quota_eligibility(0,workload_type="original_batch")
        quota_seconds_by_treatment={name:admission["reservation_seconds"]/len(treatments) for name in treatments}
        now = utc_now()
        with self._transaction() as db:
            adaptive_sequence=None
            encoded_hashes=json.dumps(input_hashes,sort_keys=True)
            if adaptive_target is not None:
                required={"target_id","target_n","chunk_n","manifest_sha256","account_label",
                          "runtime_upper_bound_seconds","safety_margin_seconds","preflight_checked_at","quota_scope"}
                if not required.issubset(adaptive_target):
                    raise ValueError("Adaptive target reservation is missing gate metadata")
                if adaptive_target["account_label"]!=account_label or adaptive_target["chunk_n"]!=n or adaptive_target["target_n"]<n:
                    raise ValueError("Adaptive block/account/PUB count does not match its target plan")
                if adaptive_target["quota_scope"]!="account_wide_verified":
                    raise ValueError("Adaptive Batch requires verified account-wide quota scope")
                target=db.execute("SELECT * FROM experiment_targets WHERE target_id=?",(adaptive_target["target_id"],)).fetchone()
                if target and (target["target_n"]!=adaptive_target["target_n"] or target["chunk_n"]!=adaptive_target["chunk_n"] or
                               target["manifest_sha256"]!=adaptive_target["manifest_sha256"] or target["input_hashes_json"]!=encoded_hashes):
                    raise ValueError("Adaptive target ID conflicts with its immutable manifest")
                if not target:
                    db.execute("INSERT INTO experiment_targets VALUES(?,?,?,?,?,?)",
                        (adaptive_target["target_id"],adaptive_target["target_n"],adaptive_target["chunk_n"],
                         adaptive_target["manifest_sha256"],encoded_hashes,now))
                prior=db.execute("SELECT * FROM experiment_blocks WHERE target_id=? ORDER BY sequence_no",
                                 (adaptive_target["target_id"],)).fetchall()
                if any(item["status"] != "CONFIRMED_NOT_SUBMITTED" and not
                       evaluate_batch_progression(self, adaptive_target["target_id"],
                           item["logical_block_id"], _db=db)["eligible"] for item in prior):
                    raise ValueError("Prior adaptive Batch lacks verified repaired PUB coverage; reconcile before another Batch")
                retry_confirmed=bool(prior and prior[-1]["status"]=="CONFIRMED_NOT_SUBMITTED")
                if retry_confirmed:
                    resolution=_retry_resolution(db,logical_block_id,target_id=adaptive_target["target_id"],
                        treatments=set(treatments))
                    if not resolution["retryable"]:
                        raise ValueError("Retry requires all eight treatments to have audited, job-ID-free NOT_SUBMITTED attempts and no Batch")
                if retry_confirmed and prior[-1]["logical_block_id"]!=logical_block_id:
                    raise ValueError("Confirmed no-submission retry must reuse the original logical block ID")
                adaptive_sequence=int(prior[-1]["sequence_no"]) if retry_confirmed else len(prior)+1
                if adaptive_sequence>adaptive_target["target_n"]//adaptive_target["chunk_n"]:
                    raise ValueError("Adaptive target already has all planned blocks")
            block_rows = db.execute("SELECT DISTINCT input_hashes_json FROM jobs WHERE logical_block_id=?", (logical_block_id,)).fetchall()
            if block_rows and any(row["input_hashes_json"] != encoded_hashes for row in block_rows):
                raise ValueError("Frozen-input hash mismatch within logical replicate block")
            old_rows = db.execute("SELECT DISTINCT n,replicate_blocks FROM jobs WHERE logical_block_id=?", (logical_block_id,)).fetchall()
            if old_rows and any(row["n"] is None or row["replicate_blocks"] is None for row in old_rows):
                raise ValueError("Existing historical block has no explicit n semantics; use a new logical_block_id")
            if old_rows and any(row["n"] != n or row["replicate_blocks"] != replicate_blocks for row in old_rows):
                raise ValueError("n or replicate_blocks conflicts with this logical block's recorded plan")
            identity = db.execute("SELECT DISTINCT account_label,backend FROM jobs WHERE logical_block_id=?", (logical_block_id,)).fetchall()
            if identity and any(row["account_label"] != account_label or row["backend"] != backend for row in identity):
                raise ValueError("Logical replicate block is pinned to one account and backend")
            for treatment_id in treatments:
                existing = db.execute("SELECT submission_state FROM jobs WHERE logical_block_id=? AND treatment_id=? ORDER BY attempt DESC LIMIT 1", (logical_block_id, treatment_id)).fetchone()
                if existing and existing["submission_state"] not in FINAL_STATES:
                    raise ValueError(f"Duplicate or unresolved block/treatment: {logical_block_id}/{treatment_id} ({existing['submission_state']})")
                if existing and existing["submission_state"] != "CONFIRMED_NOT_SUBMITTED":
                    raise ValueError("Retry requires a CONFIRMED_NOT_SUBMITTED prior attempt")
            from quota import provider_reservations
            snapshot=provider_reservations(self,connection=db)[account_label]
            active_reserved=snapshot["reserved_seconds"]
            if snapshot["ambiguous"]:
                raise ValueError("Quota reservation overlap is ambiguous; reconcile existing jobs")
            if available_budget_seconds is not None:
                if (isinstance(available_budget_seconds,bool) or not math.isfinite(available_budget_seconds)
                        or available_budget_seconds<0 or expected_active_reserved_seconds is None):
                    raise ValueError("Invalid verified available quota")
                if not math.isclose(float(active_reserved),expected_active_reserved_seconds,abs_tol=1e-9):
                    raise ValueError("Quota reservations changed; repeat provider and budget preflight")
                available=available_budget_seconds
            else:
                locally_measured=db.execute("SELECT COALESCE(SUM(qpu_usage_seconds),0) FROM jobs WHERE account_label=? AND submission_state='COMPLETED'",(account_label,)).fetchone()[0]
                available=max(0.0,float(budget_seconds)-max(float(locally_measured),float(actual_usage_seconds))-active_reserved)
            gate=quota_eligibility(available,estimated_seconds=sum(estimate_by_treatment.values()),
                safety_margin_seconds=safety_margin_seconds)
            if not gate["eligible"]:raise ValueError("Verified provider/internal quota is insufficient: "+"; ".join(gate["reasons"]))
            if adaptive_target is not None:
                values=(account_label,backend,n,adaptive_target["target_n"],encoded_hashes,now,
                    sum(float(quota_seconds_by_treatment[name]) for name in treatments),
                    (None if adaptive_target['target_id'].startswith('manual-') and adaptive_target['runtime_upper_bound_seconds'] is None else float(adaptive_target["runtime_upper_bound_seconds"])),float(adaptive_target["safety_margin_seconds"]),
                    adaptive_target["preflight_checked_at"],adaptive_target["quota_scope"],logical_block_id)
                if retry_confirmed:
                    db.execute("UPDATE experiment_blocks SET account_label=?,backend=?,batch_id=NULL,status='RESERVED',chunk_n=?,target_n=?,input_hashes_json=?,updated_at=?,quota_reserved_seconds=?,runtime_upper_bound_seconds=?,safety_margin_seconds=?,preflight_checked_at=?,quota_scope=? WHERE logical_block_id=?",values)
                else:
                    db.execute("INSERT INTO experiment_blocks(logical_block_id,target_id,sequence_no,account_label,backend,status,chunk_n,target_n,input_hashes_json,created_at,updated_at,quota_reserved_seconds,runtime_upper_bound_seconds,safety_margin_seconds,preflight_checked_at,quota_scope) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (logical_block_id,adaptive_target["target_id"],adaptive_sequence,account_label,backend,"RESERVED",n,
                         adaptive_target["target_n"],encoded_hashes,now,now,
                         sum(float(quota_seconds_by_treatment[name]) for name in treatments),
                         (None if adaptive_target['target_id'].startswith('manual-') and adaptive_target['runtime_upper_bound_seconds'] is None else float(adaptive_target["runtime_upper_bound_seconds"])),float(adaptive_target["safety_margin_seconds"]),
                         adaptive_target["preflight_checked_at"],adaptive_target["quota_scope"]))
            reserved = {}
            for treatment_id in treatments:
                attempt = db.execute("SELECT COALESCE(MAX(attempt),0)+1 AS n FROM jobs WHERE logical_block_id=? AND treatment_id=?", (logical_block_id,treatment_id)).fetchone()["n"]
                target_fields=(adaptive_target["target_id"],adaptive_target["target_n"],adaptive_target["chunk_n"]) if adaptive_target else (None,None,None)
                cur = db.execute("INSERT INTO jobs(logical_block_id,account_label,backend,treatment_id,submission_state,input_hashes_json,created_at,updated_at,quota_reserved_seconds,attempt,n,replicate_blocks,target_id,target_n,chunk_n) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (logical_block_id,account_label,backend,treatment_id,"RESERVED",encoded_hashes,now,now,float(quota_seconds_by_treatment[treatment_id]),attempt,n,replicate_blocks,*target_fields))
                db.execute("UPDATE jobs SET metadata_json=? WHERE id=?",(json.dumps({
                    "quota_policy":gate["quota_policy"],"estimated_runtime_seconds":estimate_by_treatment[treatment_id],
                    "safety_margin_seconds":safety_margin_seconds,"quota_reservation_basis":"fixed_admission_threshold"}),cur.lastrowid))
                reserved[treatment_id] = int(cur.lastrowid)
            return reserved

    def transition(self, row_id: int, state: str, *, job_id: str | None = None,
                   batch_id: str | None = None, metadata: dict[str, Any] | None = None,
                   qpu_usage_seconds: float | None = None, release_reservation: bool = False,
                   event_type: str | None = None,
                   details: dict[str, Any] | None = None) -> None:
        allowed = {"RESERVED", "SUBMITTING", "SUBMITTED", "UNKNOWN", *FINAL_STATES}
        if state not in allowed:
            raise ValueError(f"Invalid submission state: {state}")
        now = utc_now()
        with self._transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (row_id,)).fetchone()
            if row is None:
                raise KeyError(row_id)
            current=row["submission_state"]
            validate_state_transition('job',current,state)
            validate_provider_identity(job_id,row['job_id'])
            if row['batch_id'] and batch_id and row['batch_id']!=batch_id:
                raise RuntimeError('Recorded provider Batch ID is immutable')
            if job_id and (db.execute('SELECT id FROM jobs WHERE job_id=? AND id!=?',(job_id,row_id)).fetchone()
                    or db.execute('SELECT replacement_id FROM pub_replacement_jobs WHERE job_id=?',(job_id,)).fetchone()):
                raise RuntimeError('Provider job ID already belongs to another intent')
            if release_reservation and state != "CONFIRMED_NOT_SUBMITTED":
                raise ValueError("Quota reservation can be released only after confirmed non-submission")
            existing_meta = json.loads(row["metadata_json"] or "{}")
            existing_meta.update(metadata or {})
            submitted_count=row["chunk_n"] if row["target_id"] and state=="SUBMITTED" and job_id else row["submitted_pub_count"]
            completed_count=row["completed_pub_count"]
            completed_at=now if row["target_id"] and state=="COMPLETED" else row["completed_at"]
            db.execute("UPDATE jobs SET submission_state=?,job_id=COALESCE(?,job_id),batch_id=COALESCE(?,batch_id),qpu_usage_seconds=COALESCE(?,qpu_usage_seconds),quota_reserved_seconds=CASE WHEN ? THEN 0 ELSE quota_reserved_seconds END,metadata_json=?,updated_at=?,submitted_pub_count=?,completed_pub_count=?,completed_at=? WHERE id=?",
                       (state,job_id,batch_id,qpu_usage_seconds,int(release_reservation),json.dumps(existing_meta,sort_keys=True),now,submitted_count,completed_count,completed_at,row_id))
            if event_type:
                db.execute("INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                           (row_id,event_type,json.dumps(details or {},sort_keys=True),now))
            if row["target_id"]:
                if batch_id:
                    db.execute("UPDATE experiment_blocks SET batch_id=COALESCE(batch_id,?),updated_at=? WHERE logical_block_id=?",(batch_id,now,row["logical_block_id"]))
                progress=db.execute("SELECT COUNT(*) AS jobs,SUM(CASE WHEN job_id IS NOT NULL THEN 1 ELSE 0 END) AS submitted,SUM(CASE WHEN submission_state='CONFIRMED_NOT_SUBMITTED' THEN 1 ELSE 0 END) AS confirmed,SUM(CASE WHEN submission_state IN ('RESERVED','SUBMITTING','SUBMITTED','UNKNOWN') THEN 1 ELSE 0 END) AS active FROM jobs j WHERE logical_block_id=? AND target_id IS NOT NULL AND attempt=(SELECT MAX(j2.attempt) FROM jobs j2 WHERE j2.logical_block_id=j.logical_block_id AND j2.treatment_id=j.treatment_id)",(row["logical_block_id"],)).fetchone()
                if progress["jobs"]==8 and progress["submitted"]==0 and progress["confirmed"]==8:
                    block_status="CONFIRMED_NOT_SUBMITTED"
                elif progress["jobs"]==8 and progress["confirmed"]>0 and progress["submitted"]<8:
                    block_status="INCOMPLETE"
                elif progress["jobs"]==8 and progress["active"]==0:
                    block_status="INCOMPLETE"
                elif progress["active"]:
                    block_status="IN_PROGRESS"
                else:
                    block_status=state
                current_block=db.execute("SELECT status,batch_id FROM experiment_blocks WHERE logical_block_id=?",
                    (row["logical_block_id"],)).fetchone()
                retry_resolution=_retry_resolution(db,row["logical_block_id"],target_id=row["target_id"],
                    require_block_status=False)
                if retry_resolution["retryable"]:
                    block_status="CONFIRMED_NOT_SUBMITTED"
                elif current_block and current_block["status"] in ("INCOMPLETE","RECOVERY_REQUIRED") and block_status not in ("VALIDATED","COMPLETE"):
                    block_status=current_block["status"]
                db.execute("UPDATE experiment_blocks SET status=?,updated_at=? WHERE logical_block_id=? AND status NOT IN ('COMPLETE','VALIDATED')",
                           (block_status,now,row["logical_block_id"]))

    def reserve_missing_m7(self, original_id, *, account_label, reservation_seconds, metadata,
                           first_submission_evidence=None):
        """Append a single five-PUB attempt; never reuse or rewrite the original intent."""
        from final_configuration import quota_eligibility
        metadata={**metadata,"estimated_runtime_seconds":reservation_seconds}
        reservation_seconds=quota_eligibility(0,workload_type="missing_treatment")["reservation_seconds"]
        with self._transaction() as db:
            original=db.execute("SELECT * FROM jobs WHERE id=?",(original_id,)).fetchone()
            if first_submission_evidence is not None:
                proof=first_submission_evidence
                if (not original or original['logical_block_id']!='vqe-independent-n-01-n25-block-003' or
                        original['batch_id']!='d9e6cd69-775b-4597-9267-7e27a864879d' or
                        original['treatment_id']!='M7_ALL' or original['account_label']!='labK' or
                        original['attempt']!=1 or original['submission_state']!='RESERVED' or original['job_id'] or
                        original['chunk_n']!=5 or json.loads(original['metadata_json']).get('submit_started_utc') or
                        proof.get('verified') is not True or proof.get('provider_history_complete') is not True or
                        proof.get('verification_basis')!='never_started_intent_and_complete_cross_batch_history' or
                        proof.get('original_batch_id')!=original['batch_id'] or
                        metadata.get('operator_authorized_m7_first_submission') is not True):
                    raise RuntimeError('Exact original M7 reservation and explicit verified first-submission evidence required')
                checked=datetime.fromisoformat(proof.get('checked_utc',''))
                age=(datetime.now(timezone.utc)-checked).total_seconds()
                if not 0<=age<=120:raise RuntimeError('M7 first-submission evidence is not fresh')
                if db.execute("SELECT 1 FROM jobs WHERE submission_state IN ('UNKNOWN','SUBMITTING') LIMIT 1").fetchone() or db.execute("SELECT 1 FROM pub_replacement_jobs WHERE state IN ('UNKNOWN','SUBMITTING') LIMIT 1").fetchone():
                    raise RuntimeError('UNKNOWN submission blocks M7 first submission')
                if db.execute("SELECT 1 FROM jobs WHERE logical_block_id=? AND treatment_id='M7_ALL' AND id<>?",(original['logical_block_id'],original_id)).fetchone():
                    raise RuntimeError('M7 recovery already reserved; reconcile instead of retrying')
                # Preserve the entire original reservation and its quota amount in
                # an append-only event. Resolve its lifecycle, never mark FAILED,
                # so the existing unique-active-treatment index remains intact.
                snapshot=dict(original)
                now=utc_now()
                db.execute('INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)',
                    (original_id,'M7_FIRST_SUBMISSION_AUTHORIZED',json.dumps({'original_reservation':snapshot,
                        'evidence':proof},sort_keys=True,default=str),now))
                db.execute("UPDATE jobs SET submission_state='CONFIRMED_NOT_SUBMITTED',updated_at=? WHERE id=?",(now,original_id))
                metadata={**metadata,'m7_absence_evidence':proof,'original_reservation_snapshot':snapshot,
                    'm7_first_submission':True}
                original=db.execute('SELECT * FROM jobs WHERE id=?',(original_id,)).fetchone()
            if not original or original["treatment_id"]!="M7_ALL" or original["job_id"] or original["submission_state"]!="CONFIRMED_NOT_SUBMITTED":
                raise RuntimeError("M7 absence must be durably confirmed before reservation")
            if original["chunk_n"]!=5 or not (first_submission_evidence or json.loads(original["metadata_json"]).get("m7_absence_evidence")):
                raise RuntimeError("Missing M7 absence evidence or incorrect PUB count")
            latest=db.execute("SELECT id FROM jobs WHERE logical_block_id=? AND treatment_id='M7_ALL' ORDER BY attempt DESC LIMIT 1",(original["logical_block_id"],)).fetchone()
            if latest["id"]!=original_id:raise RuntimeError("M7 recovery already reserved; reconcile instead of retrying")
            now=utc_now()
            meta={**metadata,"m7_treatment_recovery":True,"source_job_row_id":original_id,
                  "source_account_label":original["account_label"],"source_batch_id":original["batch_id"]}
            cur=db.execute("""INSERT INTO jobs(logical_block_id,account_label,backend,treatment_id,submission_state,
                input_hashes_json,created_at,updated_at,quota_reserved_seconds,attempt,n,replicate_blocks,target_id,target_n,chunk_n,metadata_json)
                VALUES(?,?,?,'M7_ALL','RESERVED',?,?,?,?,?,?,?,?,?,?,?)""",
                (original["logical_block_id"],account_label,original["backend"],original["input_hashes_json"],now,now,
                 reservation_seconds,original["attempt"]+1,5,1,original["target_id"],original["target_n"],5,json.dumps(meta,sort_keys=True)))
            rid=cur.lastrowid
            db.execute("INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (rid,"M7_ONLY_RECOVERY_RESERVED",json.dumps(meta,sort_keys=True),now))
            return rid

    def reserve_treatment_recovery(self, originals, *, account_label, metadata, estimates, reservation_by_treatment=None):
        """Atomically reserve this incident's five-PUB jobs, retaining original evidence."""
        from final_configuration import quota_eligibility
        floor=quota_eligibility(0,workload_type="combined_recovery")["reservation_seconds"]
        reservations=reservation_by_treatment or {name:floor/len(estimates) for name in estimates}
        if set(reservations)!=set(estimates) or any(not math.isfinite(v) or v<0 for v in reservations.values()) or sum(reservations.values())>floor:
            raise ValueError("Invalid shared recovery admission reservation")
        ids=[]
        with self._transaction() as db:
            for original_id in originals:
                row=db.execute('SELECT * FROM jobs WHERE id=?',(original_id,)).fetchone()
                if not row or row['logical_block_id']!='vqe-independent-n-01-n25-block-003' or row['chunk_n']!=5:
                    raise RuntimeError('Recovery scope mismatch')
                name=row['treatment_id'];old=json.loads(row['metadata_json'])
                if name=='M3_ZNE':
                    evidence=old.get('unresolved_retrieval',{})
                    if not row['job_id'] or row['validated_pub_count'] or not evidence.get('replacement_reason') or not evidence.get('provider_done_verified'):
                        raise RuntimeError('M3 requires unresolved retrieval evidence and explicit replacement reason')
                elif name=='M7_ALL':
                    if row['job_id'] or row['submission_state']!='CONFIRMED_NOT_SUBMITTED' or not old.get('m7_absence_evidence'):
                        raise RuntimeError('M7 requires conclusive absence evidence')
                else:raise RuntimeError('Only M3 and M7 may be recovered')
                if db.execute('SELECT count(*) FROM jobs WHERE logical_block_id=? AND treatment_id=?',(row['logical_block_id'],name)).fetchone()[0]!=1:
                    raise RuntimeError('Recovery already reserved; reconcile instead')
                now=utc_now()
                if name=='M3_ZNE':
                    # This is a retrieval status, never a scientific invalidity claim.
                    db.execute("UPDATE jobs SET submission_state='RETRIEVAL_UNRESOLVED',updated_at=? WHERE id=?",(now,original_id))
                meta={**metadata,'combined_treatment_recovery':True,'source_job_row_id':original_id,
                      'source_job_id':row['job_id'],'source_account_label':row['account_label'],
                      'source_batch_id':row['batch_id'],'original_submission_state':row['submission_state'],
                      'runtime_options_requested':metadata['options_by_treatment'][name],
                      'estimated_runtime_seconds':estimates[name],'quota_reservation_basis':'shared_fixed_recovery_threshold'}
                cur=db.execute("""INSERT INTO jobs(logical_block_id,account_label,backend,treatment_id,submission_state,
                    input_hashes_json,created_at,updated_at,quota_reserved_seconds,attempt,n,replicate_blocks,target_id,target_n,chunk_n,metadata_json)
                    VALUES(?,?,?,?,'RESERVED',?,?,?,?,?,?,?,?,?,?,?)""",
                    (row['logical_block_id'],account_label,row['backend'],name,row['input_hashes_json'],now,now,
                     reservations[name],row['attempt']+1,5,1,row['target_id'],row['target_n'],5,json.dumps(meta,sort_keys=True)))
                ids.append(cur.lastrowid)
                db.execute('INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)',
                           (cur.lastrowid,'COMBINED_RECOVERY_RESERVED',json.dumps(meta,sort_keys=True),now))
        return ids

    def link_reconciled_job(self, snapshot, job_id, evidence):
        """Atomic compare-and-link; preserve intent metadata and append one audit event."""
        with self._transaction() as db:
            row=db.execute("SELECT * FROM jobs WHERE id=?",(snapshot["id"],)).fetchone()
            if row is None: raise KeyError(snapshot["id"])
            for key in ("logical_block_id","treatment_id","account_label","backend","batch_id"):
                if row[key]!=snapshot[key]: raise RuntimeError("Reconciliation intent changed: "+key)
            if json.loads(row["input_hashes_json"])!=snapshot["input_hashes"]:
                raise RuntimeError("Reconciliation frozen hashes changed")
            if row["job_id"]:
                if row["job_id"]!=job_id: raise RuntimeError("Cannot replace historical IBM job ID")
                return False
            if row["submission_state"] not in ("SUBMITTING","UNKNOWN","RESERVED"):
                raise RuntimeError("Reconciliation intent state changed")
            if db.execute("SELECT id FROM jobs WHERE job_id=? AND id!=?",(job_id,row["id"])).fetchone():
                raise RuntimeError("Provider job already linked to another intent")
            meta=json.loads(row["metadata_json"] or "{}")
            if meta.get("submit_started_utc")!=snapshot["metadata"].get("submit_started_utc"):
                raise RuntimeError("Submission intent timestamp changed")
            meta["reconciliation_evidence"]=evidence
            now=utc_now()
            db.execute("UPDATE jobs SET job_id=?,submission_state='SUBMITTED',submitted_pub_count=chunk_n,metadata_json=?,updated_at=? WHERE id=?",
                (job_id,json.dumps(meta,sort_keys=True),now,row["id"]))
            db.execute("INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (row["id"],"AMBIGUOUS_JOB_FOUND",json.dumps({"job_id":job_id,"evidence":evidence},sort_keys=True),now))
            return True

    def mark_interrupted_submissions_unknown(self, logical_block_id: str | None = None) -> int:
        """Recover pre-submit reservations and mark ambiguous SUBMITTING rows UNKNOWN."""
        now = utc_now()
        with self._transaction() as db:
            if logical_block_id:
                rows = db.execute("SELECT id FROM jobs WHERE submission_state='SUBMITTING' AND logical_block_id=?", (logical_block_id,)).fetchall()
            else:
                rows = db.execute("SELECT id FROM jobs WHERE submission_state='SUBMITTING'").fetchall()
            for row in rows:
                db.execute("UPDATE jobs SET submission_state='UNKNOWN',updated_at=? WHERE id=?", (now,row["id"]))
                db.execute("INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                           (row["id"],"INTERRUPTED_SUBMISSION_REQUIRES_RECONCILIATION",json.dumps({"automatic_retry":False}),now))
            # RESERVED alone is not provider evidence of non-submission. Preserve
            # its reservation and identity until explicit reconciliation.
            query="SELECT logical_block_id FROM experiment_blocks"+(" WHERE logical_block_id=?" if logical_block_id else "")
            block_ids=[item["logical_block_id"] for item in db.execute(query,(logical_block_id,) if logical_block_id else ()).fetchall()]
            for block_id in block_ids:
                progress=db.execute("SELECT COUNT(*) AS jobs,SUM(CASE WHEN job_id IS NOT NULL THEN 1 ELSE 0 END) AS submitted,SUM(CASE WHEN submission_state='COMPLETED' THEN 1 ELSE 0 END) AS completed,SUM(CASE WHEN submission_state='CONFIRMED_NOT_SUBMITTED' THEN 1 ELSE 0 END) AS confirmed,SUM(CASE WHEN submission_state IN ('RESERVED','SUBMITTING','SUBMITTED','UNKNOWN') THEN 1 ELSE 0 END) AS active,MIN(validated_pub_count) AS min_validated,MAX(chunk_n) AS chunk_n FROM jobs j WHERE logical_block_id=? AND target_id IS NOT NULL AND attempt=(SELECT MAX(j2.attempt) FROM jobs j2 WHERE j2.logical_block_id=j.logical_block_id AND j2.treatment_id=j.treatment_id)",(block_id,)).fetchone()
                if progress["jobs"]==8 and progress["submitted"]==8 and progress["completed"]==8 and progress["min_validated"]==progress["chunk_n"]:
                    status="VALIDATED"
                elif progress["jobs"]==8 and progress["submitted"]==0 and progress["confirmed"]==8:
                    status="CONFIRMED_NOT_SUBMITTED"
                elif progress["jobs"]==8 and (progress["active"]==0 or (progress["confirmed"] and progress["submitted"]<8)):
                    status="INCOMPLETE"
                else: status="IN_PROGRESS"
                db.execute("UPDATE experiment_blocks SET status=?,updated_at=? WHERE logical_block_id=? AND status NOT IN ('COMPLETE','VALIDATED')",(status,now,block_id))
            return len(rows)

    def rows(self, logical_block_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as db:
            if logical_block_id:
                rows = db.execute("SELECT * FROM jobs WHERE logical_block_id=? ORDER BY id", (logical_block_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM jobs ORDER BY id").fetchall()
            result = [dict(row) for row in rows]
        for row in result:
            row["input_hashes"] = json.loads(row.pop("input_hashes_json"))
            row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
        return result

    def events(self, row_id: int) -> list[dict[str, Any]]:
        with self._connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM recovery_events WHERE job_row_id=? ORDER BY id", (row_id,)).fetchall()]

    def record_monitor_snapshot(self, *, logical_block_id: str, batch_id: str | None,
                                account_label: str, backend: str, status: str,
                                snapshot: dict[str, Any], events: list[dict[str, Any]]) -> None:
        """Persist read-only Runtime observations without changing submission identity."""
        if account_label not in ACCOUNT_LABELS or not logical_block_id or not status:
            raise ValueError("Monitor snapshot requires a valid block, account, and status")
        now=utc_now(); encoded=json.dumps(snapshot,sort_keys=True,default=str)
        with self._transaction() as db:
            rows=db.execute("SELECT DISTINCT account_label,backend,batch_id FROM jobs WHERE logical_block_id=?",(logical_block_id,)).fetchall()
            if not rows: raise KeyError(logical_block_id)
            if any(row["account_label"]!=account_label or row["backend"]!=backend for row in rows):
                raise ValueError("Monitor identity differs from the recorded account/backend")
            known={row["batch_id"] for row in rows if row["batch_id"]}
            if batch_id and known and known!={batch_id}:
                raise ValueError("Monitor Batch ID differs from the durable job registry")
            old=db.execute("SELECT * FROM batch_monitor_state WHERE logical_block_id=?",(logical_block_id,)).fetchone()
            if old and (old["account_label"]!=account_label or old["backend"]!=backend or
                        (old["batch_id"] and batch_id and old["batch_id"]!=batch_id)):
                raise ValueError("Monitor snapshot conflicts with prior durable Batch identity")
            db.execute("INSERT INTO batch_monitor_state(logical_block_id,batch_id,account_label,backend,status,checked_at,snapshot_json) VALUES(?,?,?,?,?,?,?) "
                       "ON CONFLICT(logical_block_id) DO UPDATE SET batch_id=COALESCE(excluded.batch_id,batch_monitor_state.batch_id),status=excluded.status,checked_at=excluded.checked_at,snapshot_json=excluded.snapshot_json",
                       (logical_block_id,batch_id,account_label,backend,status,now,encoded))
            for event in events:
                db.execute("INSERT INTO batch_monitor_events(logical_block_id,batch_id,job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?,?,?)",
                    (logical_block_id,batch_id,event.get("job_row_id"),event["event_type"],
                     json.dumps(event.get("details",{}),sort_keys=True,default=str),now))
            integrity_failures={"INCOMPLETE_PUBS","RESULT_CORRUPTION","JOB_FAILURE","BATCH_ID_MISMATCH"}
            if any(event.get("event_type") in integrity_failures for event in events):
                db.execute("UPDATE experiment_blocks SET status='INVALIDATED',updated_at=? WHERE logical_block_id=? AND status IN ('VALIDATED','COMPLETE')",
                    (now,logical_block_id))

    def monitor_snapshot(self, logical_block_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            state=db.execute("SELECT * FROM batch_monitor_state WHERE logical_block_id=?",(logical_block_id,)).fetchone()
            events=db.execute("SELECT * FROM batch_monitor_events WHERE logical_block_id=? ORDER BY id",(logical_block_id,)).fetchall()
        if not state: return None
        result=dict(state); result["snapshot"]=json.loads(result.pop("snapshot_json"))
        result["events"]=[{**dict(event),"details":json.loads(event["details_json"])} for event in events]
        return result

    def record_account_usage(self, *, account_label: str, consumed_seconds: float,
                             quota_scope: str, source: str = "Runtime service.usage",
                             logical_block_id: str | None = None) -> None:
        """Persist provider usage while retaining the reported scope verbatim."""
        value=float(consumed_seconds)
        if account_label not in ACCOUNT_LABELS or not math.isfinite(value) or value<0:
            raise ValueError("Invalid provider usage snapshot")
        with self._transaction() as db:
            db.execute("INSERT INTO account_usage_snapshots(account_label,consumed_seconds,quota_scope,source,checked_at,logical_block_id) VALUES(?,?,?,?,?,?)",
                (account_label,value,str(quota_scope),source,utc_now(),logical_block_id))

    def account_usage_history(self, account_label: str) -> list[dict[str, Any]]:
        if account_label not in ACCOUNT_LABELS: raise ValueError("Unknown account label")
        with self._connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM account_usage_snapshots WHERE account_label=? ORDER BY id",(account_label,)).fetchall()]

    def register_independent_campaign(self, manifest: dict[str,Any]) -> None:
        """Register an immutable six-level campaign plan in this SQLite ledger."""
        campaign_id=manifest.get("campaign_id");digest=manifest.get("manifest_sha256")
        levels=manifest.get("levels")
        if not campaign_id or not digest or not isinstance(levels,list) or len(levels)!=6:
            raise ValueError("Independent-n campaign requires an ID, hash, and six ordered levels")
        now=utc_now();encoded=json.dumps(manifest,sort_keys=True,separators=(",",":"),default=str)
        with self._transaction() as db:
            existing=db.execute("SELECT manifest_sha256 FROM independent_n_campaigns WHERE campaign_id=?",(campaign_id,)).fetchone()
            if existing:
                if existing[0]!=digest: raise ValueError("Campaign ID already exists with a different immutable manifest")
                return
            db.execute("INSERT INTO independent_n_campaigns(campaign_id,manifest_sha256,manifest_json,state,current_level_index,checkpoint_json,created_at,updated_at) VALUES(?,?,?,'PLANNED',0,'{}',?,?)",
                (campaign_id,digest,encoded,now,now))
            for index,level in enumerate(levels):
                if level.get("level_index")!=index or level.get("target_n")!=(index+1)*5:
                    raise ValueError("Independent levels must be ordered n=5,10,15,20,25,30")
                db.execute("INSERT INTO independent_n_campaign_levels VALUES(?,?,?,?,?,'PLANNED','{}',?)",
                    (campaign_id,index,level["target_n"],level["target_id"],level["experiment_id"],now))
            db.execute("INSERT INTO independent_n_campaign_events(campaign_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (campaign_id,"INDEPENDENT_CAMPAIGN_PLANNED",json.dumps({"manifest_sha256":digest}),now))

    def checkpoint_independent_campaign(self,campaign_id: str,*,state: str,
            level_index: int | None=None,level_state: str | None=None,
            checkpoint: dict[str,Any] | None=None,event_type: str="CAMPAIGN_CHECKPOINT") -> None:
        allowed={"PLANNED","RUNNING","PAUSED","STOPPED","BLOCKED","COMPLETE","PARTIAL"}
        if state not in allowed: raise ValueError("Invalid independent campaign state")
        now=utc_now();data=checkpoint or {}
        with self._transaction() as db:
            row=db.execute("SELECT current_level_index FROM independent_n_campaigns WHERE campaign_id=?",(campaign_id,)).fetchone()
            if row is None: raise KeyError(campaign_id)
            if level_index is not None:
                level=db.execute("SELECT state FROM independent_n_campaign_levels WHERE campaign_id=? AND level_index=?",(campaign_id,level_index)).fetchone()
                if level is None: raise KeyError((campaign_id,level_index))
                if level_index>row[0]: raise RuntimeError("Cannot begin an n level before the preceding level is complete")
                if level_state is not None:
                    db.execute("UPDATE independent_n_campaign_levels SET state=?,checkpoint_json=?,updated_at=? WHERE campaign_id=? AND level_index=?",
                        (level_state,json.dumps(data,sort_keys=True,default=str),now,campaign_id,level_index))
            db.execute("UPDATE independent_n_campaigns SET state=?,current_level_index=MAX(current_level_index,?),checkpoint_json=?,updated_at=? WHERE campaign_id=?",
                (state,level_index or 0,json.dumps(data,sort_keys=True,default=str),now,campaign_id))
            db.execute("INSERT INTO independent_n_campaign_events(campaign_id,level_index,event_type,details_json,created_at) VALUES(?,?,?,?,?)",
                (campaign_id,level_index,event_type,json.dumps(data,sort_keys=True,default=str),now))

    def complete_independent_level(self,campaign_id: str,level_index: int) -> None:
        """Atomically advance on scientific data; publication artifacts are downstream."""
        with self._transaction() as db:
            campaign=db.execute("SELECT current_level_index FROM independent_n_campaigns WHERE campaign_id=?",(campaign_id,)).fetchone()
            level=db.execute("SELECT * FROM independent_n_campaign_levels WHERE campaign_id=? AND level_index=?",(campaign_id,level_index)).fetchone()
            if campaign is None or level is None: raise KeyError((campaign_id,level_index))
            if int(campaign["current_level_index"]) > level_index and level["state"] in ("COMPLETE","REPAIRED_COMPLETE"):
                return
            if int(campaign["current_level_index"]) != level_index:
                raise RuntimeError("Independent n levels must complete in strict order")
            target=db.execute("SELECT * FROM experiment_targets WHERE target_id=?",(level["target_id"],)).fetchone()
            blocks=db.execute("SELECT * FROM experiment_blocks WHERE target_id=? ORDER BY sequence_no",(level["target_id"],)).fetchall()
            if (target is None or int(target["target_n"]) != int(level["target_n"]) or
                    len(blocks) != int(level["target_n"]) // int(target["chunk_n"])):
                raise RuntimeError("Independent n level has missing Batch blocks")
            decisions=[evaluate_batch_progression(self,level["target_id"],b["logical_block_id"],_db=db) for b in blocks]
            if not all(d["eligible"] for d in decisions):
                raise RuntimeError("Independent n level lacks complete validated logical PUB evidence")
            state="REPAIRED_COMPLETE" if any(d["dataset_status"]=="REPAIRED_COMPLETE" for d in decisions) else "COMPLETE"
            now=utc_now(); next_index=level_index+1
            db.execute("UPDATE independent_n_campaign_levels SET state=?,updated_at=? WHERE campaign_id=? AND level_index=?",(state,now,campaign_id,level_index))
            db.execute("UPDATE independent_n_campaigns SET state=?,current_level_index=?,updated_at=? WHERE campaign_id=?",("COMPLETE" if next_index==6 else "RUNNING",next_index,now,campaign_id))
            db.execute("INSERT INTO independent_n_campaign_events(campaign_id,level_index,event_type,details_json,created_at) VALUES(?,?,?,?,?)",
                (campaign_id,level_index,"INDEPENDENT_N_LEVEL_"+state,json.dumps({"target_n":level["target_n"],"data_collection_complete":True,"publication_exports_independent":True}),now))

    def complete_independent_level_repaired(self,campaign_id: str,level_index: int,*,
            analysis_manifest_sha256: str | None=None,export_manifest_sha256: str | None=None) -> None:
        """Compatibility entry point; artifact hashes are not progression prerequisites."""
        self.complete_independent_level(campaign_id,level_index)

    def independent_campaign_status(self,campaign_id: str, *, event_limit=100) -> dict[str,Any]:
        """Compact operational snapshot; event_limit=None reads the full audit offline."""
        if event_limit is not None and (isinstance(event_limit,bool) or not isinstance(event_limit,int) or event_limit<1):
            raise ValueError('event_limit must be a positive integer or None')
        with self._connection() as db:
            campaign=db.execute("SELECT * FROM independent_n_campaigns WHERE campaign_id=?",(campaign_id,)).fetchone()
            if campaign is None: return {"campaign_id":campaign_id,"exists":False}
            levels=[dict(row) for row in db.execute("SELECT * FROM independent_n_campaign_levels WHERE campaign_id=? ORDER BY level_index",(campaign_id,))]
            if event_limit is None:
                events=[dict(row) for row in db.execute("SELECT * FROM independent_n_campaign_events WHERE campaign_id=? ORDER BY id",(campaign_id,))]
            else:
                events=[dict(row) for row in db.execute("SELECT * FROM independent_n_campaign_events WHERE campaign_id=? ORDER BY id DESC LIMIT ?",(campaign_id,event_limit))][::-1]
        for level in levels:
            level["checkpoint_json"]=json.loads(level["checkpoint_json"])
            level["target_status"]=self.target_status(level["target_id"])
        return {"campaign_id":campaign_id,"exists":True,"manifest":json.loads(campaign["manifest_json"]),
            "manifest_sha256":campaign["manifest_sha256"],"state":campaign["state"],
            "current_level_index":campaign["current_level_index"],"checkpoint":json.loads(campaign["checkpoint_json"]),
            "levels":levels,"events":events,"event_limit":event_limit}

    def register_target(self, *, target_id: str, target_n: int, chunk_n: int,
                        manifest_sha256: str, input_hashes: dict[str,str]) -> None:
        """Register an immutable adaptive target idempotently."""
        if not target_id or target_n<1 or chunk_n not in (5,10) or target_n%chunk_n:
            raise ValueError("Invalid adaptive target definition")
        now=utc_now();encoded=json.dumps(input_hashes,sort_keys=True)
        with self._transaction() as db:
            row=db.execute("SELECT * FROM experiment_targets WHERE target_id=?",(target_id,)).fetchone()
            expected=(target_n,chunk_n,manifest_sha256,encoded)
            if row:
                actual=(row["target_n"],row["chunk_n"],row["manifest_sha256"],row["input_hashes_json"])
                if actual!=expected: raise ValueError("Adaptive target ID is already bound to another manifest")
                return
            db.execute("INSERT INTO experiment_targets VALUES(?,?,?,?,?,?)",
                (target_id,target_n,chunk_n,manifest_sha256,encoded,now))

    def update_pub_progress(self, row_id: int, *, completed: int | None = None,
                            collected: int | None = None, validated: int | None = None,
                            event_type: str = "PUB_PROGRESS_UPDATED") -> None:
        """Persist monotonic PUB stage counts; only validated PUBs advance a target."""
        now=utc_now()
        with self._transaction() as db:
            row=db.execute("SELECT * FROM jobs WHERE id=?",(row_id,)).fetchone()
            if row is None: raise KeyError(row_id)
            if not row["target_id"]: raise ValueError("PUB progress counters apply only to adaptive target jobs")
            values={"completed_pub_count":row["completed_pub_count"] if completed is None else completed,
                    "collected_pub_count":row["collected_pub_count"] if collected is None else collected,
                    "validated_pub_count":row["validated_pub_count"] if validated is None else validated}
            cap=int(row["chunk_n"])
            if any(isinstance(v,bool) or not isinstance(v,int) or v<0 or v>cap for v in values.values()):
                raise ValueError("PUB stage counts must be integer counts within the requested chunk")
            if values["completed_pub_count"]<row["completed_pub_count"] or values["collected_pub_count"]<row["collected_pub_count"] or values["validated_pub_count"]<row["validated_pub_count"]:
                raise ValueError("PUB stage counts cannot decrease")
            if not (values["validated_pub_count"]<=values["collected_pub_count"]<=values["completed_pub_count"]<=row["submitted_pub_count"]):
                raise ValueError("PUB progress must satisfy validated <= collected <= completed <= submitted")
            stamps={"completed_at":now if values["completed_pub_count"] and not row["completed_at"] else row["completed_at"],
                    "collected_at":now if values["collected_pub_count"] and not row["collected_at"] else row["collected_at"],
                    "validated_at":now if values["validated_pub_count"] and not row["validated_at"] else row["validated_at"]}
            db.execute("UPDATE jobs SET completed_pub_count=?,collected_pub_count=?,validated_pub_count=?,completed_at=?,collected_at=?,validated_at=?,updated_at=? WHERE id=?",
                (values["completed_pub_count"],values["collected_pub_count"],values["validated_pub_count"],stamps["completed_at"],stamps["collected_at"],stamps["validated_at"],now,row_id))
            db.execute("INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (row_id,event_type,json.dumps(values,sort_keys=True),now))
            block=row["logical_block_id"]
            aggregate=db.execute("SELECT COUNT(*) AS jobs,SUM(CASE WHEN job_id IS NOT NULL THEN 1 ELSE 0 END) AS submitted,SUM(CASE WHEN submission_state='COMPLETED' THEN 1 ELSE 0 END) AS completed,MIN(validated_pub_count) AS min_validated,MAX(chunk_n) AS chunk_n,COUNT(DISTINCT treatment_id) AS treatments,SUM(CASE WHEN submission_state IN ('RESERVED','SUBMITTING','SUBMITTED','UNKNOWN') THEN 1 ELSE 0 END) AS active FROM jobs j WHERE logical_block_id=? AND target_id IS NOT NULL AND attempt=(SELECT MAX(j2.attempt) FROM jobs j2 WHERE j2.logical_block_id=j.logical_block_id AND j2.treatment_id=j.treatment_id)",(block,)).fetchone()
            if (event_type=="ADAPTIVE_PUBS_COLLECTED_AND_VALIDATED" and aggregate["jobs"]==8 and
                    aggregate["treatments"]==8 and aggregate["submitted"]==8 and aggregate["completed"]==8 and
                    aggregate["min_validated"]==aggregate["chunk_n"]):
                db.execute("UPDATE experiment_blocks SET status='VALIDATED',updated_at=? WHERE logical_block_id=?",(now,block))
            elif aggregate["jobs"]==8 and aggregate["active"]==0:
                db.execute("UPDATE experiment_blocks SET status='INCOMPLETE',updated_at=? WHERE logical_block_id=? AND status NOT IN ('VALIDATED','COMPLETE','INVALIDATED')",(now,block))

    def independent_experiment_id(self,target_id: str) -> str:
        with self._connection() as db:
            row=db.execute("SELECT experiment_id FROM independent_n_campaign_levels WHERE target_id=?",
                (target_id,)).fetchone()
        return str(row[0]) if row else target_id

    def independent_campaign_context(self,target_id: str) -> tuple[str | None,int | None]:
        with self._connection() as db:
            row=db.execute("SELECT campaign_id,level_index FROM independent_n_campaign_levels WHERE target_id=?",
                (target_id,)).fetchone()
        return (str(row[0]),int(row[1])) if row else (None,None)

    def record_pub_validation_records(self,row_id: int,experiment_id: str,source_sha256: str,
                                      records: list[dict[str,Any]]) -> None:
        """Persist per-PUB validation plus a separate immutable valid-PUB subset."""
        if not isinstance(source_sha256,str) or len(source_sha256)!=64:
            raise ValueError("Per-PUB source must be identified by a SHA-256 digest")
        now=utc_now()
        with self._transaction() as db:
            row=db.execute("SELECT * FROM jobs WHERE id=?",(row_id,)).fetchone()
            if row is None: raise KeyError(row_id)
            if not row["target_id"] or not row["batch_id"] or not row["job_id"]:
                raise ValueError("Per-PUB scientific records require registered target, Batch, and job provenance")
            input_hashes=json.loads(row["input_hashes_json"])
            for item in records:
                pub_index=item.get("pub_index")
                if isinstance(pub_index,bool) or not isinstance(pub_index,int) or pub_index<1:
                    raise ValueError("PUB indices are positive, one-based integers")
                ev_valid=item.get("expectation_valid")
                std_valid=item.get("uncertainty_valid")
                if not isinstance(ev_valid,bool) or (std_valid is not None and not isinstance(std_valid,bool)):
                    raise ValueError("Per-PUB validity fields must be booleans or null for missing uncertainty")
                payload=(experiment_id,row["logical_block_id"],row["batch_id"],row["treatment_id"],pub_index,
                    int(ev_valid),None if std_valid is None else int(std_valid),item.get("uncertainty_state","MISSING"),
                    json.dumps(item.get("error_codes",[]),sort_keys=True,separators=(",",":")),source_sha256,
                    json.dumps(item.get("expectation_values"),sort_keys=True,separators=(",",":"),allow_nan=False)
                        if ev_valid else None,
                    json.dumps(item.get("standard_deviations"),sort_keys=True,separators=(",",":"),allow_nan=False)
                        if std_valid is True else None,
                    json.dumps(item.get("metadata",{}),sort_keys=True,separators=(",",":"),default=str))
                old=db.execute("SELECT experiment_id,logical_block_id,batch_id,treatment_id,pub_index,expectation_valid,uncertainty_valid,uncertainty_state,error_codes_json,source_sha256,expectation_values_json,standard_deviations_json,pub_metadata_json FROM pub_validation_records WHERE job_row_id=? AND pub_index=?",
                    (row_id,pub_index)).fetchone()
                if old:
                    if tuple(old)!=payload:
                        raise RuntimeError("Existing immutable per-PUB validation conflicts with retrieved evidence")
                    continue
                db.execute("INSERT INTO pub_validation_records(job_row_id,experiment_id,logical_block_id,batch_id,treatment_id,pub_index,expectation_valid,uncertainty_valid,uncertainty_state,error_codes_json,source_sha256,expectation_values_json,standard_deviations_json,pub_metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (row_id,*payload,now))
                if ev_valid and std_valid is not False:
                    db.execute("INSERT INTO validated_pub_values(job_row_id,experiment_id,logical_block_id,batch_id,treatment_id,pub_index,expectation_values_json,standard_deviations_json,uncertainty_state,source_sha256,input_hashes_json,account_label,backend,job_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row_id,experiment_id,row["logical_block_id"],row["batch_id"],row["treatment_id"],pub_index,
                         json.dumps(item["expectation_values"],separators=(",",":"),allow_nan=False),
                         json.dumps(item.get("standard_deviations"),separators=(",",":"),allow_nan=False) if std_valid is True else None,
                         item.get("uncertainty_state","MISSING"),source_sha256,row["input_hashes_json"],
                         row["account_label"],row["backend"],row["job_id"],now))
                if item.get("error_codes"):
                    db.execute("INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                        (row_id,"PUB_VALIDATION_FAILURE",json.dumps({"experiment_id":experiment_id,
                            "batch_id":row["batch_id"],"treatment_id":row["treatment_id"],"pub_index":pub_index,
                            "error_codes":item["error_codes"],"source_sha256":source_sha256,
                            "automatic_qpu_resubmit":False},sort_keys=True),now))

    def set_treatment_validation_state(self,row_id: int,*,state: str,invalid_pub_count: int) -> None:
        allowed={"COMPLETE_VALIDATED","PARTIAL_VALIDATED","FAILED_VALIDATION","INCOMPLETE","NOT_VALIDATED"}
        if state not in allowed or invalid_pub_count<0: raise ValueError("Invalid treatment PUB validation state")
        with self._transaction() as db:
            cur=db.execute("UPDATE jobs SET treatment_validation_state=?,invalid_pub_count=?,updated_at=? WHERE id=?",
                (state,invalid_pub_count,utc_now(),row_id))
            if cur.rowcount!=1: raise KeyError(row_id)
            if state in ("PARTIAL_VALIDATED","FAILED_VALIDATION","INCOMPLETE"):
                block=db.execute("SELECT logical_block_id FROM jobs WHERE id=?",(row_id,)).fetchone()[0]
                db.execute("UPDATE experiment_blocks SET status='INCOMPLETE',updated_at=? WHERE logical_block_id=? AND status NOT IN ('COMPLETE','VALIDATED')",
                    (utc_now(),block))

    def pub_validation_records(self,target_id: str) -> list[dict[str,Any]]:
        with self._connection() as db:
            rows=db.execute("SELECT p.*,j.job_id,j.account_label,j.backend,j.input_hashes_json,j.target_id,j.chunk_n,j.target_n FROM pub_validation_records p JOIN jobs j ON j.id=p.job_row_id WHERE j.target_id=? ORDER BY p.logical_block_id,p.treatment_id,p.pub_index",
                (target_id,)).fetchall()
        return [dict(row) for row in rows]

    def record_pub_archive_reference(self, *, job_row_id: int, pub_index: int,
            source_job_id: str, treatment_id: str, archive_path: str,
            archive_sha256: str, source_kind: str) -> dict[str,Any]:
        """Append an immutable archive pointer after its SHA-256 is verified."""
        import hashlib
        from pathlib import Path
        path=Path(archive_path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=archive_sha256:
            raise ValueError("PUB archive reference requires a matching existing SHA-256")
        if pub_index<1 or not source_job_id or not treatment_id or not source_kind:
            raise ValueError("PUB archive reference fields are incomplete")
        now=utc_now()
        with self._transaction() as db:
            job=db.execute("SELECT job_id,treatment_id FROM jobs WHERE id=?",(job_row_id,)).fetchone()
            if job is None or job["job_id"]!=source_job_id or job["treatment_id"]!=treatment_id:
                raise ValueError("PUB archive reference does not match the registered source job/treatment")
            old=db.execute("SELECT * FROM pub_archive_references WHERE job_row_id=? AND pub_index=?",
                (job_row_id,pub_index)).fetchone()
            if old:
                expected=(source_job_id,treatment_id,str(path),archive_sha256,source_kind)
                observed=tuple(old[key] for key in ("source_job_id","treatment_id","archive_path","archive_sha256","source_kind"))
                if observed!=expected:
                    raise RuntimeError("Existing PUB archive provenance reference conflicts; refusing replacement")
                return dict(old)
            db.execute("INSERT INTO pub_archive_references(job_row_id,pub_index,source_job_id,treatment_id,archive_path,archive_sha256,source_kind,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (job_row_id,pub_index,source_job_id,treatment_id,str(path),archive_sha256,source_kind,now))
            return dict(db.execute("SELECT * FROM pub_archive_references WHERE job_row_id=? AND pub_index=?",
                (job_row_id,pub_index)).fetchone())

    def pub_archive_references(self,target_id: str) -> list[dict[str,Any]]:
        with self._connection() as db:
            rows=db.execute("SELECT r.* FROM pub_archive_references r JOIN jobs j ON j.id=r.job_row_id WHERE j.target_id=? ORDER BY j.logical_block_id,j.treatment_id,r.pub_index",
                (target_id,)).fetchall()
        return [dict(row) for row in rows]

    def validated_pub_values(self,target_id: str) -> list[dict[str,Any]]:
        with self._connection() as db:
            rows=db.execute("SELECT v.*,j.target_id,j.target_n,j.chunk_n,j.submission_state FROM validated_pub_values v JOIN jobs j ON j.id=v.job_row_id WHERE j.target_id=? ORDER BY v.logical_block_id,v.treatment_id,v.pub_index",
                (target_id,)).fetchall()
        return [dict(row) for row in rows]

    def record_independent_target_event(self,target_id: str,event_type: str,details: dict[str,Any]) -> None:
        encoded=json.dumps(details,sort_keys=True,separators=(",",":"),default=str);now=utc_now()
        with self._transaction() as db:
            level=db.execute("SELECT campaign_id,level_index FROM independent_n_campaign_levels WHERE target_id=?",
                (target_id,)).fetchone()
            if level is None: return
            exists=db.execute("SELECT 1 FROM independent_n_campaign_events WHERE campaign_id=? AND level_index=? AND event_type=? AND details_json=?",
                (level["campaign_id"],level["level_index"],event_type,encoded)).fetchone()
            if not exists:
                db.execute("INSERT INTO independent_n_campaign_events(campaign_id,level_index,event_type,details_json,created_at) VALUES(?,?,?,?,?)",
                    (level["campaign_id"],level["level_index"],event_type,encoded,now))

    def target_status(self, target_id: str) -> dict[str, Any]:
        with self._connection() as db:
            target=db.execute("SELECT * FROM experiment_targets WHERE target_id=?",(target_id,)).fetchone()
            if target is None: return {"target_id":target_id,"exists":False,"treatments":{},"blocks":[]}
            rows=db.execute("SELECT * FROM jobs WHERE target_id=? ORDER BY logical_block_id,treatment_id,attempt",(target_id,)).fetchall()
            blocks=db.execute("""SELECT b.*,r.batch_execution_status AS public_execution_status,
                    COALESCE(r.recovery_status,CASE WHEN b.status IN ('VALIDATED','COMPLETE') THEN 'NOT_REQUIRED' ELSE 'INVESTIGATING' END) AS public_recovery_status,
                    r.completeness_policy_version,r.valid_fraction_by_treatment_json,
                    r.raw_archives_verified,r.provenance_verified,r.analysis_verified,r.figures_verified,
                    r.report_verified,r.progression_eligible
                FROM experiment_blocks b LEFT JOIN batch_recovery_reviews r ON r.review_id=(
                    SELECT MAX(r2.review_id) FROM batch_recovery_reviews r2 WHERE r2.logical_block_id=b.logical_block_id)
                WHERE b.target_id=? ORDER BY b.sequence_no""",(target_id,)).fetchall()
        decisions={row["logical_block_id"]:evaluate_batch_progression(self,target_id,row["logical_block_id"])
                   for row in blocks}
        valid_block_ids={block_id for block_id,decision in decisions.items() if decision["eligible"]}
        # An explicitly adopted historical partial dataset remains execution-INCOMPLETE,
        # but its individually validated PUBs must be visible in campaign status.
        for row in rows:
            try:
                if json.loads(row["metadata_json"] or "{}").get("historical_adoption"):
                    valid_block_ids.add(row["logical_block_id"])
            except (TypeError, json.JSONDecodeError):
                pass
        treatments={}
        names=[f"M{i}_{suffix}" for i,suffix in enumerate(("Baseline","DD","TREX","ZNE","DD_TREX","DD_ZNE","TREX_ZNE","ALL"))]
        for name in names:
            group=[row for row in rows if row["treatment_id"]==name]
            latest={}
            for row in group:
                key=row["logical_block_id"]
                if key not in latest or row["attempt"]>latest[key]["attempt"]: latest[key]=row
            eligible=[row for key,row in latest.items() if key in valid_block_ids]
            treatments[name]={"submitted_jobs":sum(row["job_id"] is not None for row in group),
                "completed_jobs":sum(row["submission_state"]=="COMPLETED" for row in group),
                "collected_jobs":sum(row["collected_pub_count"]>0 for row in group),
                "complete_validated_jobs":sum(row["treatment_validation_state"]=="COMPLETE_VALIDATED" for row in group),
                "partial_validated_jobs":sum(row["treatment_validation_state"]=="PARTIAL_VALIDATED" for row in group),
                "failed_validation_jobs":sum(row["treatment_validation_state"]=="FAILED_VALIDATION" for row in group),
                "invalid_pub_count":sum(int(row["invalid_pub_count"] or 0) for row in group),
                "validated_jobs":sum(bool(row["chunk_n"]) and row["validated_pub_count"]==row["chunk_n"] for row in eligible),
                "submitted_pubs":sum(row["submitted_pub_count"] for row in group),
                "completed_pubs":sum(row["completed_pub_count"] for row in group),
                "collected_pubs":sum(row["collected_pub_count"] for row in group),
                "validated_pubs":sum(row["validated_pub_count"] for row in eligible)}
        block_items=[]
        for row in blocks:
            item=dict(row)|{"batch_execution_status":row["public_execution_status"] or
                ("COMPLETE" if row["status"] in ("VALIDATED","COMPLETE") else "INCOMPLETE"),
                "recovery_status":row["public_recovery_status"],
                "valid_fraction_by_treatment":json.loads(row["valid_fraction_by_treatment_json"] or "{}")}
            resolution=self.retry_resolution(row["logical_block_id"],target_id=target_id)
            item["retry_resolution"]=resolution["state"]
            item["retryable_not_submitted"]=resolution["retryable"]
            decision=decisions[row["logical_block_id"]]
            item["dataset_status"]=decision["dataset_status"]
            item["progression_eligible"]=decision["eligible"]
            item["progression_decision"]=decision
            block_items.append(item)
        replacement_rows=self.replacement_jobs(target_id)
        repaired_rows=self.repaired_pub_values(target_id)
        repaired_counts={name:sum(item["treatment_id"]==name for item in repaired_rows) for name in names}
        return {"target_id":target_id,"exists":True,"target_n":target["target_n"],"chunk_n":target["chunk_n"],
            "manifest_sha256":target["manifest_sha256"],"treatments":treatments,
            "cumulative_validated_pubs_by_treatment":{key:value["validated_pubs"] for key,value in treatments.items()},
            "repaired_validated_pubs_by_treatment":repaired_counts,
            "replacement_jobs":replacement_rows,
            "repaired_validated_pub_count":len(repaired_rows),
            "blocks":block_items}

    def retry_resolution(self,logical_block_id: str,*,target_id: str | None=None) -> dict[str,Any]:
        """Internal, evidence-derived no-submission resolution for one Batch block."""
        with self._connection() as db:
            return _retry_resolution(db,logical_block_id,target_id=target_id)

    def plan_pub_replacement(self, *, target_id: str, logical_block_id: str,
            treatment_id: str, source_pub_index: int, logical_pub_index: int,
            options: dict[str,Any], metadata: dict[str,Any] | None=None,
            allow_new_attempt: bool=False, max_attempts: int | None=None,
            replacement_account_label: str | None=None) -> dict[str,Any]:
        """Append a replacement intent for one invalid logical PUB slot.

        The source account remains immutable provenance. The replacement account
        defaults to that source account, but may be independently selected for
        the new job after the caller validates its scope.
        """
        if any(isinstance(v,bool) or not isinstance(v,int) or v<1 for v in (source_pub_index,logical_pub_index)):
            raise ValueError("PUB indices must be positive integers")
        if not isinstance(options,dict) or not options:
            raise ValueError("Canonical treatment options are required")
        now=utc_now()
        with self._transaction() as db:
            block=db.execute("SELECT * FROM experiment_blocks WHERE logical_block_id=? AND target_id=?",
                (logical_block_id,target_id)).fetchone()
            if block is None: raise KeyError((target_id,logical_block_id))
            if block["backend"]!="ibm_fez": raise ValueError("Replacement backend must remain ibm_fez")
            original=db.execute("SELECT * FROM jobs WHERE logical_block_id=? AND target_id=? AND treatment_id=? ORDER BY attempt DESC LIMIT 1",
                (logical_block_id,target_id,treatment_id)).fetchone()
            if original is None: raise KeyError((logical_block_id,treatment_id))
            if original["input_hashes_json"]!=block["input_hashes_json"]:
                raise RuntimeError("Original treatment input hashes differ from immutable block")
            replacement_account=replacement_account_label or block["account_label"]
            if replacement_account not in ACCOUNT_LABELS:raise ValueError("Replacement account label is not supported")
            previous=db.execute("SELECT * FROM pub_replacement_jobs WHERE target_id=? AND treatment_id=? AND logical_pub_index=? ORDER BY attempt DESC",
                (target_id,treatment_id,logical_pub_index)).fetchall()
            if previous:
                current=previous[0]
                if current["state"]=="PLANNED" and current["job_id"] is None:
                    source_account=current["source_account_label"] or block["account_label"]
                    next_meta=json.loads(current["metadata_json"] or "{}")
                    if current["account_label"]!=replacement_account:
                        next_meta.update({"replacement_account_override":replacement_account!=source_account,
                            "replacement_account_selected":replacement_account,
                            "replacement_account_previous":current["account_label"]})
                        db.execute("UPDATE pub_replacement_jobs SET account_label=?,source_account_label=?,metadata_json=?,updated_at=? WHERE replacement_id=? AND state='PLANNED' AND job_id IS NULL",
                            (replacement_account,source_account,json.dumps(next_meta,sort_keys=True),now,current["replacement_id"]))
                        db.execute("INSERT INTO pub_replacement_events(replacement_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                            (current["replacement_id"],"REPLACEMENT_ACCOUNT_SELECTED",json.dumps({"source_account_label":source_account,
                                "previous_replacement_account":current["account_label"],"replacement_account_label":replacement_account},sort_keys=True),now))
                        current=db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(current["replacement_id"],)).fetchone()
                    return dict(current)
                if current["state"]!="FAILED" or not allow_new_attempt:
                    if current["account_label"]!=replacement_account:
                        raise RuntimeError("Replacement account is immutable after submission intent; a new account requires explicit approval for a new attempt after reconciliation")
                    return dict(current)
            attempt=(int(previous[0]["attempt"])+1) if previous else 1
            # max_attempts is accepted only for legacy callers. No counter is
            # an authorization gate; every new attempt still needs FAILED,
            # explicit retry policy, fresh quota and an unclaimed logical slot.
            if any(v['treatment_id']==treatment_id and v['logical_pub_index']==logical_pub_index
                   for v in self.repaired_pub_values(target_id,_db=db)):
                raise RuntimeError('Logical PUB slot already has an accepted result')
            plan_metadata=dict(metadata or {})|{"source_account_label":block["account_label"],
                "replacement_account_label":replacement_account,
                "replacement_account_override":replacement_account!=block["account_label"]}
            if previous:
                delay=min(60*2**min(attempt-2,4),900)
                failed_at=datetime.fromisoformat(previous[0]['updated_at'].replace('Z','+00:00'))
                plan_metadata.update(recovery_delay_seconds=delay,
                    not_before_utc=(failed_at+timedelta(seconds=delay)).isoformat())
            cur=db.execute("""INSERT INTO pub_replacement_jobs(target_id,logical_block_id,treatment_id,source_pub_index,
                logical_pub_index,attempt,account_label,source_account_label,backend,state,input_hashes_json,options_json,metadata_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (target_id,logical_block_id,treatment_id,source_pub_index,logical_pub_index,attempt,
                 replacement_account,block["account_label"],block["backend"],"PLANNED",block["input_hashes_json"],
                 json.dumps(options,sort_keys=True,separators=(",",":"),default=str),
                 json.dumps(plan_metadata,sort_keys=True,separators=(",",":"),default=str),now,now))
            replacement_id=int(cur.lastrowid)
            db.execute("INSERT INTO pub_replacement_events(replacement_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (replacement_id,"REPLACEMENT_PLANNED",json.dumps({"logical_pub_index":logical_pub_index,
                 "source_pub_index":source_pub_index,"automatic_submission":False},sort_keys=True),now))
            return dict(db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(replacement_id,)).fetchone())

    def reassign_planned_replacement_account(self, replacement_id: int, account_label: str) -> dict[str,Any]:
        """Atomic account switch only before any replacement submission claim."""
        if account_label not in ACCOUNT_LABELS:
            raise ValueError("Unknown replacement account")
        with self._transaction() as db:
            row=db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",
                (replacement_id,)).fetchone()
            if row is None: raise KeyError(replacement_id)
            if row["state"]!="PLANNED" or row["job_id"] is not None or row["batch_id"] is not None:
                raise RuntimeError("Replacement account is pinned after a submission claim")
            if row["account_label"]==account_label:
                return dict(row)
            original=row["source_account_label"] or row["account_label"]
            metadata=json.loads(row["metadata_json"] or "{}")
            metadata.update({"source_account_label":original,"replacement_account_label":account_label,
                "previous_replacement_account":row["account_label"]})
            now=utc_now()
            db.execute("UPDATE pub_replacement_jobs SET account_label=?,source_account_label=?,metadata_json=?,updated_at=? "
                "WHERE replacement_id=? AND state='PLANNED' AND job_id IS NULL AND batch_id IS NULL",
                (account_label,original,json.dumps(metadata,sort_keys=True),now,replacement_id))
            db.execute("INSERT INTO pub_replacement_events(replacement_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (replacement_id,"REPLACEMENT_ACCOUNT_SELECTED",json.dumps({"source_account_label":original,
                 "replacement_account_label":account_label},sort_keys=True),now))
            return dict(db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(replacement_id,)).fetchone())

    def transition_pub_replacement(self, replacement_id: int, state: str, *, job_id: str | None=None,
            batch_id: str | None=None, quota_reserved_seconds: float | None=None,
            qpu_usage_seconds: float | None=None, metadata: dict[str,Any] | None=None,
            event_type: str | None=None, details: dict[str,Any] | None=None) -> dict[str,Any]:
        allowed={"PLANNED","RESERVED","SUBMITTING","UNKNOWN","SUBMITTED","DONE","VALIDATED","FAILED"}
        if state not in allowed: raise ValueError("Invalid replacement state")
        for value,label in ((quota_reserved_seconds,"reservation"),(qpu_usage_seconds,"QPU usage")):
            if value is not None and (isinstance(value,bool) or not math.isfinite(float(value)) or float(value)<0):
                raise ValueError(f"Replacement {label} must be finite and nonnegative")
        now=utc_now()
        with self._transaction() as db:
            row=db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(replacement_id,)).fetchone()
            if row is None: raise KeyError(replacement_id)
            validate_state_transition('replacement',row['state'],state)
            validate_provider_identity(job_id,row['job_id'])
            if state=='PLANNED' and row['state']!='PLANNED':
                evidence=(metadata or {}).get('submission_reconciliation',{})
                if (row['job_id'] or (metadata or {}).get('retryable_after_reconciliation') is not True
                        or evidence.get('provider_query_complete') is not True):
                    raise RuntimeError('UNKNOWN replacement requires complete non-submission reconciliation')
            if job_id:
                if db.execute('SELECT id FROM jobs WHERE job_id=?',(job_id,)).fetchone():
                    raise RuntimeError('Provider job ID already belongs to another intent')
                siblings=db.execute('SELECT * FROM pub_replacement_jobs WHERE job_id=? AND replacement_id!=?',
                                   (job_id,replacement_id)).fetchall()
                if siblings:
                    # Sharing requires the same explicit pre-submission ordered intent
                    # map. Never infer which logical slot an extra response belongs to.
                    mapping=json.loads(row['metadata_json'] or '{}').get('replacement_pub_map')
                    if not mapping or any(json.loads(other['metadata_json'] or '{}').get('replacement_pub_map')!=mapping
                                          for other in siblings):
                        raise RuntimeError('Provider job ID already belongs to another intent')
                    if len({entry['replacement_id'] for entry in mapping})!=len(mapping):
                        raise RuntimeError('Duplicate replacement PUB mapping')
                    for other in [row,*siblings]:
                        if (other['replacement_id'] not in [entry['replacement_id'] for entry in mapping]
                                or any(other[key]!=row[key] for key in ('account_label','backend','input_hashes_json','target_id'))):
                            raise RuntimeError('Conflicting replacement PUB mapping')
            if row["job_id"] and job_id and row["job_id"]!=job_id: raise RuntimeError("Replacement job ID is immutable")
            prior_meta=json.loads(row['metadata_json'] or '{}')
            next_meta=prior_meta|dict(metadata or {})
            mapping=next_meta.get('replacement_pub_map')
            if mapping:
                if row['state']!='PLANNED' and mapping!=prior_meta.get('replacement_pub_map'):
                    raise RuntimeError('Submitted replacement PUB mapping is immutable')
                if [entry.get('pub_index') for entry in mapping]!=list(range(1,len(mapping)+1)):
                    raise RuntimeError('Invalid replacement PUB mapping indices')
                entry=next((entry for entry in mapping if entry.get('replacement_id')==replacement_id),None)
                if not entry or entry.get('treatment_id')!=row['treatment_id'] or entry.get('logical_pub_index')!=row['logical_pub_index']:
                    raise RuntimeError('Replacement PUB mapping differs from logical slot')
                next_meta['provider_pub_index']=entry['pub_index']
            elif prior_meta.get('replacement_pub_map'):
                raise RuntimeError('Replacement PUB mapping cannot be removed')
            batch_value=batch_id if batch_id is not None else row["batch_id"]
            if row["batch_id"] and batch_id and row["batch_id"]!=batch_id:
                evidence=next_meta.get("submission_reconciliation",{})
                prior_ids=list(next_meta.get("prior_batch_ids",[]))
                retryable_empty_batch=(row["state"]=="SUBMITTING" and row["job_id"] is None
                    and next_meta.get("retryable_after_reconciliation") is True
                    and evidence.get("provider_query_complete") is True
                    and evidence.get("batch_id")==row["batch_id"]
                    and evidence.get("provider_job_count")==0)
                if not retryable_empty_batch:
                    raise RuntimeError("Replacement Batch ID is immutable without verified empty-Batch reconciliation")
                prior_ids.append(row["batch_id"])
                next_meta["prior_batch_ids"]=list(dict.fromkeys(prior_ids))
                next_meta["retryable_after_reconciliation"]=False
                batch_value=batch_id
                details=dict(details or {})|{"previous_batch_id":row["batch_id"],
                    "batch_rollover_reason":"provider-confirmed-empty-closed-batch"}
            db.execute("""UPDATE pub_replacement_jobs SET state=?,job_id=COALESCE(job_id,?),batch_id=?,
                quota_reserved_seconds=COALESCE(?,quota_reserved_seconds),qpu_usage_seconds=COALESCE(?,qpu_usage_seconds),
                metadata_json=?,updated_at=? WHERE replacement_id=?""",
                (state,job_id,batch_value,quota_reserved_seconds,qpu_usage_seconds,
                 json.dumps(next_meta,sort_keys=True,separators=(",",":"),default=str),now,replacement_id))
            db.execute("INSERT INTO pub_replacement_events(replacement_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (replacement_id,event_type or ("REPLACEMENT_"+state),json.dumps(details or {},sort_keys=True,default=str),now))
            return dict(db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(replacement_id,)).fetchone())

    def claim_pub_replacement_submission(self, replacement_id: int, *, reservation_seconds: float,
            preflight_checked_at: str, quota_scope: str, runtime_upper_bound_seconds: float,
            safety_margin_seconds: float, instance_ref: str | None=None,
            available_budget_seconds: float | None=None, expected_reserved_seconds: float | None=None) -> dict[str,Any]:
        """Atomic compare-and-set; prevents two workers submitting the same slot."""
        now=utc_now()
        if any(not math.isfinite(float(value)) or float(value)<0 for value in
                (reservation_seconds,runtime_upper_bound_seconds,safety_margin_seconds)):
            raise ValueError("Replacement runtime reservations must be finite and nonnegative")
        with self._transaction() as db:
            row=db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(replacement_id,)).fetchone()
            if row is None: raise KeyError(replacement_id)
            if row["state"]!="PLANNED" or row["job_id"] is not None:
                raise RuntimeError("Replacement slot is already claimed or requires reconciliation")
            if available_budget_seconds is not None:
                from quota import provider_reservations
                from final_configuration import quota_eligibility
                snapshot=provider_reservations(self,connection=db)[row["account_label"]]
                if (snapshot["ambiguous"] or expected_reserved_seconds is None or
                        not math.isclose(snapshot["reserved_seconds"],expected_reserved_seconds,abs_tol=1e-9)):
                    raise RuntimeError("Quota reservations changed or ambiguous; repeat preflight")
                gate=quota_eligibility(available_budget_seconds,workload_type="targeted_replacement",
                    estimated_seconds=runtime_upper_bound_seconds,safety_margin_seconds=safety_margin_seconds)
                if not gate["eligible"]:raise RuntimeError("Verified unreserved quota insufficient for replacement")
                reservation_seconds=gate["reservation_seconds"]
            metadata=json.loads(row["metadata_json"] or "{}")|{"preflight_checked_utc":preflight_checked_at,
                "quota_scope":quota_scope,"runtime_upper_bound_seconds":runtime_upper_bound_seconds,
                "safety_margin_seconds":safety_margin_seconds,"instance_ref":instance_ref,
                "replacement_account_label":row["account_label"],
                "source_account_label":row["source_account_label"]}
            changed=db.execute("UPDATE pub_replacement_jobs SET state='SUBMITTING',quota_reserved_seconds=?,metadata_json=?,updated_at=? WHERE replacement_id=? AND state='PLANNED' AND job_id IS NULL",
                (reservation_seconds,json.dumps(metadata,sort_keys=True),now,replacement_id)).rowcount
            if changed!=1: raise RuntimeError("Replacement submission claim lost a concurrency race")
            db.execute("INSERT INTO pub_replacement_events(replacement_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (replacement_id,"REPLACEMENT_SUBMISSION_CLAIMED",json.dumps({"preflight_checked_at":preflight_checked_at,
                 "quota_scope":quota_scope,"runtime_upper_bound_seconds":runtime_upper_bound_seconds,
                 "instance_ref":instance_ref,"replacement_account_label":row["account_label"],
                 "source_account_label":row["source_account_label"]},sort_keys=True),now))
            return dict(db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(replacement_id,)).fetchone())

    def archive_pub_replacement(self, replacement_id: int, *, archive_path: str, archive_sha256: str,
            expectation_values: list[float] | None, standard_deviations: list[float] | None,
            uncertainty_state: str, error_codes: list[str], qpu_usage_seconds: float | None,
            provider_metadata: dict[str,Any] | None=None) -> dict[str,Any]:
        """Persist immutable archive provenance before validation outcome is acted upon."""
        import hashlib
        path=Path(archive_path)
        actual=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual!=archive_sha256: raise ValueError("Replacement raw archive hash does not verify")
        if expectation_values is not None and (len(expectation_values)!=7 or any(not math.isfinite(float(v)) for v in expectation_values)):
            expectation_values=None
        if standard_deviations is not None and (len(standard_deviations)!=7 or any(not math.isfinite(float(v)) or float(v)<0 for v in standard_deviations)):
            standard_deviations=None
        now=utc_now()
        with self._transaction() as db:
            row=db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(replacement_id,)).fetchone()
            if row is None: raise KeyError(replacement_id)
            if row["raw_archive_sha256"] and row["raw_archive_sha256"]!=archive_sha256:
                raise RuntimeError("Replacement raw archive is immutable and already has a different hash")
            valid=expectation_values is not None and standard_deviations is not None and not error_codes
            encoded_ev=json.dumps(expectation_values,separators=(",",":"),allow_nan=False) if expectation_values is not None else None
            encoded_std=json.dumps(standard_deviations,separators=(",",":"),allow_nan=False) if standard_deviations is not None else None
            state="VALIDATED" if valid else "FAILED"
            validate_state_transition('replacement',row['state'],state)
            if not row['job_id']:
                raise RuntimeError('Cannot accept replacement evidence without a reconciled job ID')
            if row['state']=='VALIDATED' and (row['expectation_values_json']!=encoded_ev
                    or row['standard_deviations_json']!=encoded_std or row['uncertainty_state']!=uncertainty_state):
                raise RuntimeError('Accepted replacement scientific values are immutable')
            db.execute("""UPDATE pub_replacement_jobs SET state=?,raw_archive_path=?,raw_archive_sha256=?,expectation_values_json=?,
                standard_deviations_json=?,uncertainty_state=?,error_codes_json=?,qpu_usage_seconds=COALESCE(?,qpu_usage_seconds),
                metadata_json=?,updated_at=? WHERE replacement_id=?""",
                (state,str(path),archive_sha256,encoded_ev,encoded_std,uncertainty_state,
                 json.dumps(error_codes,sort_keys=True),qpu_usage_seconds,
                 json.dumps(json.loads(row["metadata_json"] or "{}")|dict(provider_metadata or {}),sort_keys=True,default=str),now,replacement_id))
            db.execute("INSERT INTO pub_replacement_events(replacement_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                (replacement_id,"REPLACEMENT_RESULT_"+state,json.dumps({"raw_archive_sha256":archive_sha256,
                 "qpu_usage_seconds":qpu_usage_seconds,"error_codes":error_codes},sort_keys=True),now))
            return dict(db.execute("SELECT * FROM pub_replacement_jobs WHERE replacement_id=?",(replacement_id,)).fetchone())

    def replacement_jobs(self, target_id: str) -> list[dict[str,Any]]:
        with self._connection() as db:
            rows=db.execute("SELECT * FROM pub_replacement_jobs WHERE target_id=? ORDER BY logical_block_id,treatment_id,logical_pub_index,attempt",(target_id,)).fetchall()
        output=[]
        for row in rows:
            item=dict(row);item["input_hashes"]=json.loads(item.pop("input_hashes_json"));item["options"]=json.loads(item.pop("options_json"));item["metadata"]=json.loads(item.pop("metadata_json"));item["error_codes"]=json.loads(item.pop("error_codes_json"));output.append(item)
        return output

    def replacement_events(self,replacement_id: int) -> list[dict[str,Any]]:
        """Return the append-only audit trail for one targeted replacement."""
        with self._connection() as db:
            rows=db.execute("SELECT * FROM pub_replacement_events WHERE replacement_id=? ORDER BY event_id",
                (replacement_id,)).fetchall()
        output=[]
        for row in rows:
            item=dict(row);item["details"]=json.loads(item.pop("details_json"));output.append(item)
        return output

    def replacement_runtime_candidates(self) -> list[dict[str,Any]]:
        """Read replacement rows across n-levels for runtime calibration only."""
        with self._connection() as db:
            rows=db.execute("SELECT * FROM pub_replacement_jobs ORDER BY created_at,replacement_id").fetchall()
        output=[]
        for row in rows:
            item=dict(row);item["input_hashes"]=json.loads(item.pop("input_hashes_json"));
            item["options"]=json.loads(item.pop("options_json"));
            item["metadata"]=json.loads(item.pop("metadata_json"));
            item["error_codes"]=json.loads(item.pop("error_codes_json"));output.append(item)
        return output

    def repaired_pub_values(self, target_id: str, *, _db=None) -> list[dict[str,Any]]:
        """Select exactly one valid observation per target logical slot, preferring originals."""
        with (nullcontext(_db) if _db is not None else self._connection()) as db:
            target=db.execute("SELECT * FROM experiment_targets WHERE target_id=?",(target_id,)).fetchone()
            if target is None: return []
            blocks=db.execute("SELECT logical_block_id,sequence_no FROM experiment_blocks WHERE target_id=? ORDER BY sequence_no",(target_id,)).fetchall()
            originals=db.execute("""SELECT v.*,b.sequence_no FROM validated_pub_values v
                JOIN jobs j ON j.id=v.job_row_id JOIN experiment_blocks b ON b.logical_block_id=j.logical_block_id
                WHERE j.target_id=? ORDER BY b.sequence_no,v.treatment_id,v.pub_index,j.attempt""",(target_id,)).fetchall()
            source_jobs=[dict(r) for r in db.execute("SELECT * FROM jobs WHERE target_id=?",(target_id,))]
            replacements=db.execute("SELECT * FROM pub_replacement_jobs WHERE target_id=? AND state='VALIDATED' ORDER BY logical_pub_index,attempt",(target_id,)).fetchall()
        selected={}
        hashes=json.loads(target["input_hashes_json"])
        for row in originals:
            source_job=next(j for j in source_jobs if j['id']==row['job_row_id'])
            if json.loads(source_job['metadata_json']).get('combined_treatment_recovery'):
                from batch_progression import treatment_recovery_provenance_verified
                if not treatment_recovery_provenance_verified(source_job,row,source_jobs):continue
            if json.loads(row["input_hashes_json"]) != hashes: continue
            if row["uncertainty_state"]!="PRESENT": continue
            if row["uncertainty_state"] != "PRESENT" or not valid_scientific_values({
                    "expectation_values":json.loads(row["expectation_values_json"] or "null"),
                    "standard_deviations":json.loads(row["standard_deviations_json"] or "null")}): continue
            slot=(row["treatment_id"],(int(row["sequence_no"])-1)*int(target["chunk_n"])+int(row["pub_index"]))
            selected.setdefault(slot,{"treatment_id":row["treatment_id"],"logical_pub_index":slot[1],
                "logical_block_id":row["logical_block_id"],"expectation_values":json.loads(row["expectation_values_json"]),
                "standard_deviations":json.loads(row["standard_deviations_json"]) if row["standard_deviations_json"] else None,
                "source":("TREATMENT_REPLACEMENT" if any(j['id']==row['job_row_id'] and
                    json.loads(j['metadata_json']).get('combined_treatment_recovery') and j['treatment_id']=='M3_ZNE'
                    for j in source_jobs) else "ORIGINAL"),"job_id":row["job_id"],"batch_id":row["batch_id"],"source_sha256":row["source_sha256"],
                "account_label":row["account_label"],
                "source_account_label":json.loads(source_job['metadata_json']).get('source_account_label',row['account_label']),
                "source_job_id":json.loads(source_job['metadata_json']).get('source_job_id',row['job_id']),
                "source_job_row_id":json.loads(source_job['metadata_json']).get('source_job_row_id',row['job_row_id']),
                "backend":row["backend"],"input_hashes":json.loads(row["input_hashes_json"]),"created_at":row["created_at"]})
        for row in replacements:
            meta=json.loads(row["metadata_json"])
            if meta.get("source_run") != "run_02" or meta.get("input_hashes") != hashes: continue
            if not any(j["logical_block_id"]==row["logical_block_id"] and
                       j["treatment_id"]==row["treatment_id"] and j["job_id"]==meta.get("source_job_id") and
                       json.loads(j["input_hashes_json"])==hashes for j in source_jobs): continue
            if json.loads(row["input_hashes_json"]) != hashes: continue
            if row["uncertainty_state"] != "PRESENT" or not valid_scientific_values({
                    "expectation_values":json.loads(row["expectation_values_json"] or "null"),
                    "standard_deviations":json.loads(row["standard_deviations_json"] or "null")}): continue
            key=(row["treatment_id"],int(row["logical_pub_index"]))
            if key in selected: continue
            candidate={"treatment_id":row["treatment_id"],"logical_pub_index":key[1],
                "logical_block_id":row["logical_block_id"],"expectation_values":json.loads(row["expectation_values_json"]),
                "standard_deviations":json.loads(row["standard_deviations_json"]) if row["standard_deviations_json"] else None,
                "source":"REPLACEMENT","job_id":row["job_id"],"batch_id":row["batch_id"],
                "source_sha256":row["raw_archive_sha256"],"account_label":row["account_label"],
                "source_account_label":row["source_account_label"],"backend":row["backend"],
                "input_hashes":json.loads(row["input_hashes_json"]),"created_at":row["updated_at"],
                "replacement_id":row["replacement_id"]}
            if replacement_provenance_verified(row,candidate,hashes):
                selected[key]=candidate
        return [selected[key] for key in sorted(selected,key=lambda item:(item[0],item[1]))]

    def record_batch_recovery_review(self,logical_block_id: str,*,batch_execution_status: str,
            recovery_status: str,completeness_policy_version: str,
            valid_fraction_by_treatment: dict[str,float],raw_archives_verified: bool,
            provenance_verified: bool,analysis_verified: bool=False,figures_verified: bool=False,
            report_verified: bool=False,progression_eligible: bool=False,minimum_fraction: float | None=None,
            insufficient_data_override: bool=False,details: dict[str,Any] | None=None) -> int:
        """Append an auditable recovery review; raw IBM archives are never changed."""
        if batch_execution_status not in ("COMPLETE","INCOMPLETE"):
            raise ValueError("Batch execution status must be COMPLETE or INCOMPLETE")
        if recovery_status not in ("INVESTIGATING","RECOVERED","UNRECOVERABLE"):
            raise ValueError("Recovery status must be INVESTIGATING, RECOVERED, or UNRECOVERABLE")
        for flag in (raw_archives_verified,provenance_verified,analysis_verified,figures_verified,report_verified,progression_eligible):
            if not isinstance(flag,bool): raise ValueError("Recovery verification flags must be booleans")
        if not isinstance(valid_fraction_by_treatment,dict) or any(
                not isinstance(name,str) or isinstance(value,bool) or not isinstance(value,(int,float)) or
                not math.isfinite(float(value)) or not 0<=float(value)<=1
                for name,value in valid_fraction_by_treatment.items()):
            raise ValueError("Per-treatment valid PUB fractions must be finite values within [0,1]")
        detail_data=dict(details or {})
        if minimum_fraction is not None:detail_data["minimum_valid_pub_fraction_per_treatment"]=float(minimum_fraction)
        detail_data["insufficient_data_override"]=bool(insufficient_data_override)
        encoded=json.dumps(detail_data,sort_keys=True,separators=(",",":"),default=str)
        fractions=json.dumps(valid_fraction_by_treatment,sort_keys=True,separators=(",",":"),allow_nan=False)
        now=utc_now()
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM experiment_blocks WHERE logical_block_id=?",(logical_block_id,)).fetchone() is None:
                raise KeyError(logical_block_id)
            target_id=db.execute("SELECT target_id FROM experiment_blocks WHERE logical_block_id=?",
                (logical_block_id,)).fetchone()[0]
            decision=evaluate_batch_progression(self,target_id,logical_block_id,_db=db)
            progression_eligible=decision["eligible"]
            if recovery_status == "RECOVERED" and not progression_eligible:
                raise ValueError("RECOVERED requires complete validated logical PUB evidence")
            cur=db.execute("""INSERT INTO batch_recovery_reviews(logical_block_id,batch_execution_status,recovery_status,
                completeness_policy_version,valid_fraction_by_treatment_json,raw_archives_verified,provenance_verified,
                analysis_verified,figures_verified,report_verified,progression_eligible,review_json,reviewed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(logical_block_id,batch_execution_status,recovery_status,
                completeness_policy_version,fractions,int(raw_archives_verified),int(provenance_verified),
                int(analysis_verified),int(figures_verified),int(report_verified),int(progression_eligible),encoded,now))
            rows=db.execute("SELECT id FROM jobs WHERE logical_block_id=? ORDER BY treatment_id",(logical_block_id,)).fetchall()
            if rows:
                db.execute("INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
                    (rows[0]["id"],"BATCH_RECOVERY_REVIEW",json.dumps({"batch_execution_status":batch_execution_status,
                        "recovery_status":recovery_status,"progression_eligible":progression_eligible,
                        "completeness_policy_version":completeness_policy_version,"details":details or {}},sort_keys=True,default=str),now))
            return int(cur.lastrowid)

    def latest_batch_recovery_review(self,logical_block_id: str) -> dict[str,Any] | None:
        with self._connection() as db:
            row=db.execute("SELECT * FROM batch_recovery_reviews WHERE logical_block_id=? ORDER BY review_id DESC LIMIT 1",
                (logical_block_id,)).fetchone()
        if row is None:return None
        result=dict(row);result["valid_fraction_by_treatment"]=json.loads(result.pop("valid_fraction_by_treatment_json"))
        result["details"]=json.loads(result.pop("review_json"))
        # SQLite stores Boolean flags as INTEGER 0/1. Normalize at the Registry
        # boundary so downstream strict `is True` checks behave consistently.
        # Reject malformed flags rather than silently treating them as verified.
        for flag in ("raw_archives_verified", "provenance_verified", "analysis_verified",
                     "figures_verified", "report_verified", "progression_eligible"):
            value=result[flag]
            if type(value) is not int or value not in (0, 1):
                raise ValueError(f"Invalid persisted recovery flag: {flag}")
            result[flag]=bool(value)
        return result

    def progress_independent_level_partial(self,campaign_id: str,level_index: int,*,
            completeness_policy_version: str,minimum_fraction: float,
            insufficient_data_action: str="STOP",approved_policy: dict[str,Any] | None=None) -> None:
        """Compatibility API: partial analysis never authorizes partial progression."""
        self.complete_independent_level(campaign_id,level_index)
