"""Non-overwriting paper exports for one already analyzed independent n level.

This module packages outputs from the canonical mitigation analyzer. It does
not calculate EIS parameters or alter PUB validity decisions.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


import csv
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any


from figure_registry import (PUBLICATION_FIGURES, PAPER_FIGURES, WIDE_FIGURES, OPTIONAL_FIGURES, CONTRACT_VERSION)
# Figure canvas sizes are controlled by mitigation_pilot.py.
# Publication: square; comparison: landscape. Do not pin exact pixel dimensions:
# bbox_inches='tight' and source figure revisions can legitimately change them.
FIGURE_MIN_SHORT_SIDE = 1000
FIGURE_DPI = 600.0

CSV_FILES = (
    "accuracy_cost_data.csv", "bode_curves.csv", "nyquist_curves.csv",
    "params_table.csv", "per_repeat.csv", "residual_curves.csv",
    "sse_plot_data.csv", "summary_mean_std.csv", "tail_stats.csv",
    "round_summary.json", "per_pub_validation.csv", "errors.csv", "runtime.csv",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


PUBLICATION_PIXELS = (4800, 4800)
PUBLICATION_DPI = 600.0

def inspect_png(path, *, layout, min_short_side=1000, required_dpi=PUBLICATION_DPI):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Required PNG missing: {path}")
    # Read-only comparison consumers need hashes/CSV helpers without Pillow.
    from PIL import Image
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError(f"Not a PNG: {path}")
        width, height = image.size
        dpi = image.info.get("dpi")
        image.verify()
    dpi_ok = (isinstance(dpi, (tuple, list)) and len(dpi) >= 2
              and all(isinstance(x, (float, int)) and abs(float(x)-required_dpi) <= 1
                      for x in dpi[:2]))
    geometry_ok = ((width, height) == PUBLICATION_PIXELS if layout == "square"
                   else width > height and min(width, height) >= min_short_side
                   if layout == "landscape" else False)
    if not dpi_ok or not geometry_ok:
        raise ValueError(f"PNG contract mismatch: {path.name}: {width}x{height}, dpi={dpi}, layout={layout}")
    return {"width": width, "height": height, "dpi": list(dpi[:2]), "layout": layout, "valid": True}


def _verify_png(path: Path, *, layout: str) -> dict[str, Any]:
    inspect_png(path, layout=layout, min_short_side=FIGURE_MIN_SHORT_SIDE,
                required_dpi=FIGURE_DPI)
    return {"sha256": _sha(path), "valid": True}


def _verify_figures(figure_dir: Path) -> dict[str, Any]:
    report = {}
    for name in PAPER_FIGURES:
        report[name] = _verify_png(figure_dir/name, layout="square")
    for name in WIDE_FIGURES:
        report[name] = _verify_png(figure_dir/name, layout="landscape")
    return report


def _verify_optional_figure(figure_dir: Path, name: str) -> dict[str, Any]:
    if name not in OPTIONAL_FIGURES:
        raise ValueError(f"Unexpected optional figure: {name}")
    return _verify_png(figure_dir/name, layout="landscape")


def _export_pair_matches(figure_dir: Path, data_dir: Path, source_digest: str, *,
                         require_pair_manifest: bool, check_locations: bool=True) -> bool:
    """Return true only for a complete, hash-verified paired export."""
    data_manifest_path=data_dir/"export_manifest.json"
    if not data_manifest_path.is_file():return False
    try:
        data_manifest=json.loads(data_manifest_path.read_text(encoding="utf-8"))
        if data_manifest.get("source_analysis_manifest_sha256")!=source_digest:return False
        csv_hashes=data_manifest.get("csv_sha256",{})
        figure_hashes=data_manifest.get("figures",{})
        generated=data_manifest.get("generated_figures")
        expected=set(PAPER_FIGURES+WIDE_FIGURES)
        if generated is not None:
            generated=set(generated)
            if generated!=expected:return False
            expected=generated
            omitted={item.get("name"):item.get("reason") for item in data_manifest.get("omitted_optional_figures",[])
                if isinstance(item,dict)}
            if any(name not in expected and not omitted.get(name) for name in OPTIONAL_FIGURES):return False
        elif data_manifest.get("schema_version",1)>=3:
            return False
        else:
            expected.update(OPTIONAL_FIGURES)  # legacy exports required this figure
        if set(csv_hashes)!=set(CSV_FILES) or set(figure_hashes)!=expected:return False
        if any(not (data_dir/name).is_file() or _sha(data_dir/name)!=digest
               for name,digest in csv_hashes.items()):return False
        if any(not (figure_dir/name).is_file() or _sha(figure_dir/name)!=item.get("sha256")
               for name,item in figure_hashes.items()):return False
        verified=_verify_figures(figure_dir)
        for name in OPTIONAL_FIGURES:
            if name in expected:verified[name]=_verify_optional_figure(figure_dir,name)
        if verified!=figure_hashes:return False
        figure_manifest_path=figure_dir/"export_manifest.json"
        if require_pair_manifest and not figure_manifest_path.is_file():return False
        if figure_manifest_path.is_file():
            figure_manifest=json.loads(figure_manifest_path.read_text(encoding="utf-8"))
            if figure_manifest!=data_manifest:return False
            if check_locations and (data_manifest.get("figure_directory")!=str(figure_dir)
                    or data_manifest.get("data_directory")!=str(data_dir)):return False
        return True
    except (OSError,ValueError,TypeError,KeyError):
        return False


def _select_export_pair(figure_base: Path, data_base: Path, source_digest: str):
    """Reuse a verified pair or reserve a fresh same-version directory pair."""
    if figure_base.exists() and data_base.exists() and _export_pair_matches(
            figure_base,data_base,source_digest,require_pair_manifest=False):
        return figure_base,data_base,True,0
    # Check existing versioned pairs before reserving another version. Incomplete
    # or inconsistent pairs are immutable and skipped, never partially reused.
    for index in range(1,10000):
        name=f"v{index:03d}-{source_digest[:12]}"
        figure_candidate=figure_base/name;data_candidate=data_base/name
        if figure_candidate.exists() and data_candidate.exists() and _export_pair_matches(
                figure_candidate,data_candidate,source_digest,require_pair_manifest=True):
            return figure_candidate,data_candidate,True,index
    if not figure_base.exists() and not data_base.exists():
        return figure_base,data_base,False,0
    for index in range(1,10000):
        name=f"v{index:03d}-{source_digest[:12]}"
        figure_candidate=figure_base/name;data_candidate=data_base/name
        if not figure_candidate.exists() and not data_candidate.exists():
            return figure_candidate,data_candidate,False,index
    raise RuntimeError("Could not reserve a non-overwriting versioned export pair")


def export_n_level_results(project: str | Path, target, registry, analysis: dict[str, Any], *,
                           partial: bool = False) -> dict[str, Any]:
    """Package verified COMPLETE canonical outputs beneath the requested n-level roots.

    Existing output directories are immutable. Partial outputs are namespaced
    in the existing content-addressed diagnostics path and must not be passed
    through this paper-export function.
    """
    from repository_paths import MITIGATION_REL, create_scratch_dir, cleanup_scratch_dir, repository_root

    if partial:
        raise ValueError("Partial diagnostics remain in the content-addressed diagnostics tree; paper export requires complete analysis")
    if analysis.get("artifacts_verified") is not True:
        raise RuntimeError("Paper export requires verified canonical analysis artifacts")
    repo = repository_root(project)
    source_root = Path(analysis["output"]).resolve()
    manifest_path = source_root / "analysis_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Canonical analysis manifest is missing")
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("target_id") != target.target_id:
        raise ValueError("Canonical analysis belongs to a different independent n experiment")
    figure_base = repo / MITIGATION_REL / "output" / "01_results_and_figures" / f"n={target.target_n}"
    data_base = repo / MITIGATION_REL / "output" / "02_run_level_data" / f"n={target.target_n}"
    source_digest=_sha(manifest_path)
    figure_root,data_root,already_exists,export_version=_select_export_pair(
        figure_base,data_base,source_digest)
    if already_exists:
        return {"figures":str(figure_root),"data":str(data_root),"files_verified":True,
            "csv_files":list(CSV_FILES),"png_files":sorted(path.name for path in figure_root.glob("*.png")),
            "partial_diagnostic_only":False,"already_exists":True,
            "source_analysis_manifest_sha256":source_digest,"export_version":export_version}

    canonical_rel = source_manifest.get("canonical_combined_analysis")
    if not canonical_rel:
        raise ValueError("Canonical combined analysis path is absent from analysis manifest")
    canonical_root = source_root / canonical_rel
    canonical_root = canonical_root.resolve()
    if source_root not in canonical_root.parents:
        raise ValueError("Canonical analysis path escapes its verified output directory")
    source_figures = source_root / "publication_figures"
    comparison = source_root / "comparison_figures"
    staged = create_scratch_dir("ex", repo)
    stage_figures = staged / "figures"
    stage_data = staged / "data"
    stage_figures.mkdir()
    stage_data.mkdir()
    try:
        for name in PUBLICATION_FIGURES:
            shutil.copyfile(source_figures / name, stage_figures / name.removeprefix("publication_"))
        wide_sources = {
            "distribution_pilot.png": comparison / "distribution_pilot.png",
            "05_mitigation_median_CI_convergence.png": source_figures / "05_mitigation_median_CI_convergence.png",
            "06_mitigation_three_tier_positioning.png": source_figures / "06_mitigation_three_tier_positioning.png",
        }
        for name, source in wide_sources.items():
            if not source.is_file():
                raise FileNotFoundError(f"Required preserved wide figure is missing: {source}")
            shutil.copyfile(source, stage_figures / name)
        omitted_optional=[{"name":"04_mitigation_accuracy_cost.png",
            "reason":"Publication contract requires exactly seven square and three wide figures"}]

        provenance_path = source_root / "pub_source_manifest.csv"
        provenance = _read_csv(provenance_path) if provenance_path.is_file() else []
        source_by_key = {(row.get("treatment_id"), str(row.get("combined_pub"))): row for row in provenance}
        canonical_repeat = canonical_root / "per_repeat.csv"
        repeats = _read_csv(canonical_repeat)
        per_repeat = []
        parameters = {"Rs", "L", "Rct", "Q1", "n1", "Q2", "n2"}
        for row in repeats:
            source = source_by_key.get((row.get("treatment"), str(row.get("pub"))), {})
            per_repeat.append({
                "experiment_id": target.experiment_id, "n": target.target_n,
                "batch_id": source.get("batch_id"), "IBM_job_id": source.get("job_id", row.get("job_id")),
                "treatment": row.get("treatment"), "treatment_label": _display(row.get("treatment")),
                "PUB_index": source.get("source_pub", row.get("pub")),
                **{key: row.get(key) for key in parameters},
                "SSE": row.get("SSE"), "RMSE": row.get("RMSE"),
            })
        if len(per_repeat) != target.target_n * len(target.treatment_ids):
            raise ValueError("Canonical per-PUB table does not contain the expected independent-n sample count")
        _write_csv(stage_data / "per_repeat.csv", per_repeat)
        _write_csv(stage_data / "params_table.csv", per_repeat)

        validation = registry.pub_validation_records(target.target_id)
        block_ids = {item["logical_block_id"] for item in source_manifest.get("included_blocks", [])}
        validation_rows = []
        if source_manifest.get("dataset_status")=="REPAIRED_COMPLETE":
            selected_sources=_read_csv(source_root/"pub_source_manifest.csv")
            for item in selected_sources:
                validation_rows.append({"experiment_id":target.experiment_id,"n":target.target_n,
                    "logical_pub_index":item.get("logical_pub_index"),"batch_id":item.get("batch_id"),
                    "IBM_job_id":item.get("job_id"),"treatment":item.get("treatment_id"),
                    "treatment_label":_display(item.get("treatment_id")),"PUB_index":item.get("source_pub"),
                    "expectation_valid":True,"uncertainty_valid":"UNKNOWN_IF_MISSING",
                    "error_code":"[]","source_sha256":item.get("source_sha256"),
                    "selected_for_repaired_dataset":True,"source":item.get("source")})
            for item in validation:
                codes=item.get("error_codes_json") or "[]"
                if item.get("logical_block_id") in block_ids and (not item.get("expectation_valid") or codes not in ("[]","")):
                    validation_rows.append({"experiment_id":item.get("experiment_id"),"n":target.target_n,
                        "logical_pub_index":"","batch_id":item.get("batch_id"),"IBM_job_id":item.get("job_id"),
                        "treatment":item.get("treatment_id"),"treatment_label":_display(item.get("treatment_id")),
                        "PUB_index":item.get("pub_index"),"expectation_valid":item.get("expectation_valid"),
                        "uncertainty_valid":item.get("uncertainty_valid"),"error_code":codes,
                        "source_sha256":item.get("source_sha256"),"selected_for_repaired_dataset":False,
                        "source":"INVALID_ORIGINAL"})
        else:
            for item in validation:
                if item.get("logical_block_id") not in block_ids: continue
                validation_rows.append({
                    "experiment_id": item.get("experiment_id"), "n": target.target_n,
                    "batch_id": item.get("batch_id"), "IBM_job_id": item.get("job_id"),
                    "treatment": item.get("treatment_id"), "treatment_label": _display(item.get("treatment_id")),
                    "PUB_index": item.get("pub_index"), "expectation_valid": item.get("expectation_valid"),
                    "uncertainty_valid": item.get("uncertainty_valid"), "error_code": item.get("error_codes_json"),
                    "source_sha256": item.get("source_sha256"),
                })
        _write_csv(stage_data / "per_pub_validation.csv", validation_rows)
        _write_csv(stage_data / "errors.csv", [row for row in validation_rows if
            str(row.get("expectation_valid")).lower() in ("false", "0") or row.get("error_code") not in (None, "", "[]")])
        jobs = [row for row in registry.rows() if row.get("logical_block_id") in block_ids]
        latest = {}
        for row in jobs:
            key = (row.get("logical_block_id"), row.get("treatment_id"))
            if key not in latest or int(row.get("attempt", 0)) > int(latest[key].get("attempt", 0)):
                latest[key] = row
        usage_jobs=source_manifest.get("qpu_usage_coverage",{}).get("jobs",[])
        usage_by_job={str(item.get("job_id")):item for item in usage_jobs if item.get("job_id")}
        runtime_fields=["experiment_id","n","logical_block_id","batch_id","IBM_job_id","treatment",
            "account_label","backend","IBM_job_status","provider_qpu_usage_seconds","usage_verified",
            "usage_source","usage_scope","usage_observed_utc","usage_archive_sha256",
            "requested_shots_per_PUB","requested_PUBs"]
        def runtime_record(row,requested_pubs):
            job_id=str(row.get("job_id") or "")
            usage=usage_by_job.get(job_id,{})
            verified=usage.get("verified") is True
            return {"experiment_id":target.experiment_id,"n":target.target_n,
                "logical_block_id":row.get("logical_block_id"),"batch_id":row.get("batch_id"),
                "IBM_job_id":job_id or None,"treatment":row.get("treatment_id"),
                "account_label":row.get("account_label"),"backend":row.get("backend"),
                "IBM_job_status":row.get("metadata",{}).get("last_remote_monitor",{}).get("status",row.get("state")),
                "provider_qpu_usage_seconds":usage.get("qpu_usage_seconds") if verified else None,
                "usage_verified":verified,"usage_source":usage.get("usage_source"),
                "usage_scope":usage.get("usage_scope"),"usage_observed_utc":usage.get("observed_utc"),
                "usage_archive_sha256":usage.get("archive_sha256"),
                "requested_shots_per_PUB":target.shots_per_pub,"requested_PUBs":requested_pubs}
        _write_csv(stage_data / "runtime.csv", [{
            **runtime_record(row,row.get("submitted_pub_count")),
        } for row in latest.values()])
        if source_manifest.get("dataset_status")=="REPAIRED_COMPLETE":
            replacements=registry.replacement_jobs(target.target_id)
            with (stage_data/"runtime.csv").open("a",encoding="utf-8",newline="") as handle:
                writer=csv.DictWriter(handle,fieldnames=runtime_fields)
                for row in replacements:
                    if row["state"] not in ("VALIDATED","DONE","FAILED"):continue
                    writer.writerow(runtime_record(row,1))

        copies = ("accuracy_cost_data.csv", "bode_curves.csv", "nyquist_curves.csv", "residual_curves.csv",
                  "sse_plot_data.csv", "summary_mean_std.csv", "tail_stats.csv")
        for name in copies:
            source = canonical_root / name
            if not source.is_file():
                raise FileNotFoundError(f"Canonical scientific table is missing: {source}")
            shutil.copyfile(source, stage_data / name)
        summary = json.loads((canonical_root / "round_summary.json").read_text(encoding="utf-8"))
        summary.update({"experiment_id": target.experiment_id, "n": target.target_n,
            "target_id": target.target_id, "source_run": "run_02",
            "analysis_state": source_manifest.get("dataset_status","COMPLETE"),
            "incomplete_batches_preserved":source_manifest.get("dataset_status")=="REPAIRED_COMPLETE"})
        (stage_data / "round_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        # Include treatment-level human-readable labels in exported comparison data.
        accuracy_path = stage_data / "accuracy_cost_data.csv"
        accuracy_rows = _read_csv(accuracy_path)
        for row in accuracy_rows:
            row["treatment_label"] = _display(row.get("treatment"))
        accuracy_path.unlink()
        _write_csv(accuracy_path, accuracy_rows)
        missing = [name for name in CSV_FILES if not (stage_data / name).is_file()]
        if missing:
            raise RuntimeError("Scientific export is missing required tables: " + ", ".join(missing))
        figure_checks = _verify_figures(stage_figures)
        generated_figures=sorted(figure_checks)
        export_manifest = {"schema_version": 3,"publication_contract_version":CONTRACT_VERSION,"export_version":export_version,
            "export_id":f"n{target.target_n}-v{export_version:03d}-{source_digest[:12]}",
            "experiment_id": target.experiment_id, "target_id": target.target_id,
            "n": target.target_n, "source_run": "run_02", "source_analysis_manifest_sha256": _sha(manifest_path),
            "input_hashes": target.input_hashes, "partial_diagnostic_only": False,
            "figure_directory":str(figure_root),"data_directory":str(data_root),
            "csv_sha256": {name: _sha(stage_data / name) for name in CSV_FILES}, "figures": figure_checks,
            "generated_figures":generated_figures,"omitted_optional_figures":omitted_optional,
            "qpu_usage_coverage":source_manifest.get("qpu_usage_coverage",{
                "complete_comparable":False,"omitted_figure_reason":"analysis manifest has no verified QPU usage coverage"})}
        (stage_data / "export_manifest.json").write_text(json.dumps(export_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (stage_figures / "export_manifest.json").write_text(
            json.dumps(export_manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        # Verify the complete staged pair before publishing either directory.
        if _export_pair_matches(stage_figures,stage_data,source_digest,
                require_pair_manifest=True,check_locations=False) is not True:
            raise RuntimeError("Staged figure/data export pair failed hash or inventory verification")
        figure_root.parent.mkdir(parents=True,exist_ok=True)
        data_root.parent.mkdir(parents=True,exist_ok=True)
        token=uuid.uuid4().hex[:10]
        staged_figure_destination=figure_root.parent/f".stage-{token}-figures"
        staged_data_destination=data_root.parent/f".stage-{token}-data"
        try:
            shutil.copytree(stage_figures,staged_figure_destination)
            shutil.copytree(stage_data,staged_data_destination)
            if not _export_pair_matches(staged_figure_destination,staged_data_destination,
                    source_digest,require_pair_manifest=True,check_locations=False):
                raise RuntimeError("Output-volume staging copy failed hash or inventory verification")
            # Each directory is atomically renamed within its destination parent.
            # A crash between the two renames leaves an unpaired immutable version
            # that _select_export_pair will never reuse.
            if figure_root.exists() or data_root.exists():
                raise FileExistsError("Versioned export destination was claimed concurrently")
            os.rename(staged_figure_destination,figure_root)
            os.rename(staged_data_destination,data_root)
            if not _export_pair_matches(figure_root,data_root,source_digest,require_pair_manifest=True):
                raise RuntimeError("Published figure/data export pair failed final verification")
        finally:
            for path in (staged_figure_destination,staged_data_destination):
                if path.is_dir():shutil.rmtree(path)
        return {"figures": str(figure_root), "data": str(data_root), "files_verified": True,
            "csv_files": list(CSV_FILES), "png_files": [path.name for path in figure_root.glob("*.png")],
            "partial_diagnostic_only": False,"already_exists":False,
            "source_analysis_manifest_sha256":source_digest,"export_version":export_version,
            "export_id":export_manifest["export_id"]}
    finally:
        cleanup_scratch_dir(staged, repo)


def export_batch_evidence(project: str | Path, target, registry, logical_block_id: str) -> dict[str, Any]:
    """Write one completed Batch's verified per-PUB evidence without merging n levels."""
    from repository_paths import MITIGATION_REL, create_scratch_dir, cleanup_scratch_dir, repository_root
    from vqe_adapter import analyze_block

    repo = repository_root(project)
    status = registry.target_status(target.target_id)
    block = next((item for item in status.get("blocks", []) if item.get("logical_block_id") == logical_block_id), None)
    if block is None or block.get("status") not in ("VALIDATED", "COMPLETE"):
        raise RuntimeError("Per-Batch paper evidence requires a fully validated Batch")
    validation = [item for item in registry.pub_validation_records(target.target_id)
        if item.get("logical_block_id") == logical_block_id]
    if not validation or any(not item.get("expectation_valid") for item in validation):
        raise RuntimeError("Validated Batch has missing or invalid per-PUB validation evidence")
    from vqe_adapter import block_round_name
    canonical = repo / MITIGATION_REL / "output" / "02_run_level_data" / block_round_name(logical_block_id)
    per_repeat_path = canonical / "per_repeat.csv"
    if not per_repeat_path.is_file():
        analysis = analyze_block(repo, logical_block_id, shots=target.shots_per_pub, n=target.chunk_n)
        canonical = Path(analysis["output"])
        per_repeat_path = canonical / "per_repeat.csv"
        if not per_repeat_path.is_file():
            raise FileNotFoundError("Canonical per-Batch EIS analysis did not emit per_repeat.csv")
    per_repeat = _read_csv(per_repeat_path)
    rows_by_treatment = {(row.get("treatment"), str(row.get("pub"))): row for row in per_repeat}
    jobs = registry.rows(logical_block_id)
    latest = {}
    for row in jobs:
        key = row.get("treatment_id")
        if key not in latest or int(row.get("attempt", 0)) > int(latest[key].get("attempt", 0)):
            latest[key] = row
    enriched = []
    for item in validation:
        source = rows_by_treatment.get((item["treatment_id"], str(item["pub_index"])))
        if source is None:
            raise ValueError("Canonical per-PUB EIS result is missing a validated PUB")
        job = latest[item["treatment_id"]]
        enriched.append({"experiment_id": target.experiment_id, "n": target.target_n,
            "logical_block_id": logical_block_id, "batch_id": block.get("batch_id"),
            "IBM_job_id": job.get("job_id"), "treatment": item["treatment_id"],
            "treatment_label": _display(item["treatment_id"]), "PUB_index": item["pub_index"],
            **{key: source.get(key) for key in ("Rs", "L", "Rct", "Q1", "n1", "Q2", "n2", "SSE", "RMSE")},
            "source_sha256": item.get("source_sha256"), "input_hashes": json.dumps(job.get("input_hashes", {}), sort_keys=True)})
    validation_rows = [{"experiment_id": item.get("experiment_id"), "n": target.target_n,
        "logical_block_id": logical_block_id, "batch_id": item.get("batch_id"),
        "IBM_job_id": item.get("job_id"), "treatment": item.get("treatment_id"),
        "treatment_label": _display(item.get("treatment_id")), "PUB_index": item.get("pub_index"),
        "expectation_valid": item.get("expectation_valid"), "uncertainty_valid": item.get("uncertainty_valid"),
        "error_code": item.get("error_codes_json"), "source_sha256": item.get("source_sha256")}
        for item in validation]
    runtime = [{"logical_block_id": logical_block_id, "batch_id": row.get("batch_id"),
        "IBM_job_id": row.get("job_id"), "treatment": row.get("treatment_id"),
        "account_label": row.get("account_label"), "backend": row.get("backend"),
        "provider_qpu_usage_seconds": row.get("qpu_usage_seconds"),
        "requested_shots_per_PUB": target.shots_per_pub, "requested_PUBs": row.get("submitted_pub_count")}
        for row in latest.values()]
    manifest = {"schema_version": 1, "experiment_id": target.experiment_id, "target_id": target.target_id,
        "n": target.target_n, "chunk_n": target.chunk_n, "source_run": "run_02",
        "logical_block_id": logical_block_id, "batch_id": block.get("batch_id"),
        "input_hashes": target.input_hashes, "raw_source_hashes": sorted({item["source_sha256"] for item in validation}),
        "per_pub_sha256": {"per_repeat.csv": None, "per_pub_validation.csv": None, "errors.csv": None, "runtime.csv": None}}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()
    output = repo / MITIGATION_REL / "output" / "02_run_level_data" / f"n={target.target_n}" / "batches" / logical_block_id
    if output.exists():
        prior = output / "batch_export_manifest.json"
        if prior.is_file():
            saved = json.loads(prior.read_text(encoding="utf-8"))
            if saved.get("snapshot_sha256") == digest and all((output / name).is_file() and
                    _sha(output / name) == checksum for name, checksum in saved.get("per_pub_sha256", {}).items()):
                return {"output": str(output), "files_verified": True, "already_exists": True}
        raise FileExistsError("Refusing to overwrite existing per-Batch scientific evidence")
    staging = create_scratch_dir("be", repo)
    try:
        _write_csv(staging / "per_repeat.csv", enriched)
        _write_csv(staging / "per_pub_validation.csv", validation_rows)
        _write_csv(staging / "errors.csv", [item for item in validation_rows if item.get("error_code") not in (None, "", "[]")])
        _write_csv(staging / "runtime.csv", runtime)
        manifest["per_pub_sha256"] = {name: _sha(staging / name) for name in
            ("per_repeat.csv", "per_pub_validation.csv", "errors.csv", "runtime.csv")}
        manifest["snapshot_sha256"] = digest
        (staging / "batch_export_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, output)
        return {"output": str(output), "files_verified": True, "already_exists": False}
    finally:
        cleanup_scratch_dir(staging, repo)


def _display(treatment: str | None) -> str | None:
    if treatment is None:
        return None
    from vqe_adapter import load_pilot
    return load_pilot().treatment_display(treatment)
