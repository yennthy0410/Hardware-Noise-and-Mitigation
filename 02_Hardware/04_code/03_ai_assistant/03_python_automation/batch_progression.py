"""Read-only scientific progression policy; never authorizes Runtime submission.

The immutable per-PUB ledger is the validation evidence. Artifact/report flags
and provider Batch execution status are deliberately not completion evidence.
An optional connection keeps reservation and level transitions in one snapshot.
"""

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

from contextlib import nullcontext
import json
import math
import hashlib
from pathlib import Path

TREATMENTS = tuple(f"M{i}_{suffix}" for i, suffix in enumerate(
    ("Baseline", "DD", "TREX", "ZNE", "DD_TREX", "DD_ZNE", "TREX_ZNE", "ALL")))


def valid_scientific_values(row):
    for field in ("expectation_values", "standard_deviations"):
        values = row.get(field)
        if not isinstance(values, (list, tuple)) or len(values) != 7:
            return False
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) or (field == "standard_deviations" and v < 0)
               for v in values):
            return False
    return True


def replacement_provenance_verified(replacement, selected, hashes):
    """Verify legacy as well as new replacement archives, once per selected PUB.

    Planning already verifies the frozen ISA, theta, layout, observables and
    canonical treatment options. Bind its evidence to the actual collected job.
    """
    try:
        raw = Path(replacement["raw_archive_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != replacement["raw_archive_sha256"]:
            return False
        payload = json.loads(raw)
        metadata = json.loads(replacement["metadata_json"])
        records = payload.get("records", [])
        if metadata.get('replacement_pub_map'):
            reference=payload.get('provider_result_archive') or {}
            provider_bytes=Path(reference['path']).read_bytes()
            if hashlib.sha256(provider_bytes).hexdigest()!=reference['sha256']:return False
            provider=json.loads(provider_bytes)
            index=reference['pub_index']
            if (provider.get('job_id')!=selected['job_id'] or
                    provider.get('replacement_pub_map')!=metadata['replacement_pub_map'] or
                    records!=[provider['captured']['records'][index-1]]):return False
        return (payload.get("source_run") == "run_02" and
                payload.get("input_hashes") == hashes and
                payload.get("replacement_job_id") == selected["job_id"] and
                payload.get("original_source_job_id") == metadata.get("source_job_id") and
                payload.get("original_source_archive_sha256") == metadata.get("source_archive_sha256") and
                bool(metadata.get("source_archive_sha256")) and
                payload.get("treatment_id") == selected["treatment_id"] and
                payload.get("logical_pub_index") == selected["logical_pub_index"] and
                payload.get("source_pub_index") == replacement["source_pub_index"] and
                payload.get("runtime_options_requested") == metadata.get(
                    "runtime_options_requested", json.loads(replacement["options_json"])) and
                len(records) == 1 and records[0].get("expectation_validation") == "PASS" and
                records[0].get("uncertainty_status") == "PRESENT" and
                records[0].get("expectation_values") == selected["expectation_values"] and
                records[0].get("standard_deviations") == selected["standard_deviations"])
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return False


def treatment_recovery_provenance_verified(job, value, source_jobs):
    """Bind a treatment recovery's immutable response and original intent to its ledger."""
    try:
        meta=json.loads(job['metadata_json'])
        path=Path(meta['per_pub_source_path'])
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        sidecar=json.loads(path.with_name(path.stem+'.structured.json').read_text(encoding='utf-8'))
        record=sidecar['records'][int(value['pub_index'])-1]
        original=next(j for j in source_jobs if j['id']==meta['source_job_row_id'])
        return (job['submission_state']=='COMPLETED' and meta['source_run']=='run_02' and
            meta['input_hashes']==json.loads(job['input_hashes_json']) and bool(meta['runtime_options_requested']) and
            original['logical_block_id']==job['logical_block_id'] and original['treatment_id']==job['treatment_id'] and
            original['job_id']==meta['source_job_id'] and original['input_hashes_json']==job['input_hashes_json'] and
            digest==meta['per_pub_source_sha256']==value['source_sha256']==sidecar['source_raw_sha256'] and
            sidecar['job_id']==job['job_id'] and sidecar['treatment_id']==job['treatment_id'] and
            len(sidecar['records'])==5 and record['expectation_validation']=='PASS' and
            record['uncertainty_status']=='PRESENT' and
            record['expectation_values']==json.loads(value['expectation_values_json']) and
            record['standard_deviations']==json.loads(value['standard_deviations_json']))
    except (OSError,ValueError,KeyError,TypeError,IndexError,StopIteration):
        return False


def evaluate_batch_progression(registry, target, logical_block_id, *, _db=None):
    target_id = target if isinstance(target, str) else target.target_id
    with (nullcontext(_db) if _db is not None else registry._connection()) as db:
        saved = db.execute("SELECT * FROM experiment_targets WHERE target_id=?", (target_id,)).fetchone()
        block = db.execute("SELECT * FROM experiment_blocks WHERE target_id=? AND logical_block_id=?",
                           (target_id, logical_block_id)).fetchone()
        if saved is None or block is None:
            return dict(eligible=False, dataset_status="INCOMPLETE", expected_slots=0,
                        validated_slots=0, missing_slots=[], frozen_inputs_verified=False,
                        reason="Target or logical Batch is not registered")
        hashes = json.loads(saved["input_hashes_json"])
        names = getattr(target, "treatment_ids", TREATMENTS)
        chunk = int(saved["chunk_n"])
        start = (int(block["sequence_no"]) - 1) * chunk + 1
        expected = {(name, i) for name in names for i in range(start, start + chunk)}
        frozen = (bool(hashes) and json.loads(block["input_hashes_json"]) == hashes and
                  1 <= int(block["sequence_no"]) <= int(saved["target_n"]) // chunk)
        if not isinstance(target, str):
            frozen = frozen and target.input_hashes == hashes and target.source_run == "run_02"
        jobs = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE logical_block_id=?", (logical_block_id,))]
        replacements = [dict(r) for r in db.execute(
            "SELECT * FROM pub_replacement_jobs WHERE logical_block_id=?", (logical_block_id,))]
        unresolved = any(r["submission_state"] in ("UNKNOWN", "SUBMITTING", "RESERVED", "SUBMITTED") for r in jobs)
        unresolved = unresolved or any(r["state"] in ("UNKNOWN", "SUBMITTING", "RESERVED", "PLANNED", "SUBMITTED", "DONE") for r in replacements)
        selected = [r for r in registry.repaired_pub_values(target_id, _db=db)
                    if r["logical_block_id"] == logical_block_id]
        slots = set()
        duplicate = False
        repaired = False
        for row in selected:
            key = (row["treatment_id"], row["logical_pub_index"])
            if key in slots or key not in expected:
                duplicate = True
                continue
            identity = any(j["job_id"] == row["job_id"] and j["treatment_id"] == row["treatment_id"]
                           and json.loads(j["input_hashes_json"]) == hashes for j in jobs)
            if row["source"] == "REPLACEMENT":
                replacement = next((r for r in replacements if r["replacement_id"] == row["replacement_id"]), None)
                meta = json.loads(replacement["metadata_json"]) if replacement else {}
                identity = bool(replacement and replacement["state"] == "VALIDATED" and
                    replacement["uncertainty_state"] == "PRESENT" and
                    meta.get("source_run") == "run_02" and meta.get("input_hashes") == hashes and
                    replacement["source_pub_index"] == key[1] - start + 1 and
                    json.loads(replacement["options_json"]) and any(
                        j["job_id"] == meta.get("source_job_id") and j["treatment_id"] == key[0]
                        and json.loads(j["input_hashes_json"]) == hashes for j in jobs))
            provenance = bool(identity and row.get("job_id") and row.get("source_sha256") and
                              row.get("input_hashes") == hashes and row.get("backend") == "ibm_fez")
            frozen = frozen and provenance
            if provenance and valid_scientific_values(row):
                slots.add(key)
                repaired = repaired or row["source"] in ("REPLACEMENT", "TREATMENT_REPLACEMENT")
        missing = sorted(expected - slots)
        eligible = not missing and not duplicate and frozen and not unresolved
        status = ("REPAIRED_COMPLETE" if repaired else "COMPLETE") if eligible else (
            "INVESTIGATING" if unresolved else "INCOMPLETE")
        reason = ("Complete validated logical PUB coverage with frozen inputs" if eligible else
                  "Unresolved submission requires reconciliation" if unresolved else
                  "Missing, invalid, duplicate, or unverified logical PUB evidence")
        return dict(eligible=bool(eligible), dataset_status=status, expected_slots=len(expected),
                    validated_slots=len(slots), missing_slots=missing,
                    frozen_inputs_verified=bool(frozen and not missing), reason=reason)
