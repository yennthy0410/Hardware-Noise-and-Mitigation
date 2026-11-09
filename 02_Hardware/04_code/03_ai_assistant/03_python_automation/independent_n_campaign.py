"""Sequential orchestrator for six fully independent VQE sample-size experiments."""
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
import re
import time
from contextlib import nullcontext
from typing import Any, Callable

from calibration import utc_now
from experiment_manifest import build_manifest, build_target_manifest
from job_registry import JobRegistry, ACCOUNT_LABELS
from quota import preflight_and_select_account, AccountUnavailable, AccountSelectionUnavailable
from repository_paths import MITIGATION_REL, contained, repository_root

N_LEVELS=(5,10,15,20,25,30)
N_ACTIONS={"prepare","preflight","dry-run","submit","monitor","collect","validate","analyze","plot","report"}


def _sha(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()


def campaign_root(root,campaign_id: str) -> Path:
    return contained(repository_root(root)/MITIGATION_REL/"output"/"03_IBM_execution_records"/
                     "ai_assistant"/"independent_n_campaigns",campaign_id)


def _contains_sensitive_fields(value: Any, key: str="") -> bool:
    if any(part in key.lower() for part in ("api_key","apikey","secret","token","crn","credential")):
        return True
    if isinstance(value,dict): return any(_contains_sensitive_fields(v,str(k)) for k,v in value.items())
    if isinstance(value,list): return any(_contains_sensitive_fields(v,key) for v in value)
    return isinstance(value,str) and bool(re.search(r"crn:v1:",value,re.I))


def validate_operator_authorization(auth: dict[str,Any]) -> dict[str,Any]:
    if _contains_sensitive_fields(auth):
        raise ValueError("Authorization must use opaque instance references and contain no credentials or CRNs")
    allowed=auth.get("allowed_accounts")
    if not isinstance(allowed,list) or not allowed or len(set(allowed))!=len(allowed) or any(x not in ACCOUNT_LABELS for x in allowed):
        raise ValueError("allowed_accounts must explicitly list unique labels from "+", ".join(ACCOUNT_LABELS))
    by_n=auth.get("account_by_n", {})
    auto_selection=auth.get("account_selection")=="highest_available_quota"
    if auth.get("account_selection", "fixed_by_n") not in ("fixed_by_n", "highest_available_quota"):
        raise ValueError("Unsupported account selection mode")
    if not isinstance(by_n,dict) or any(str(k) not in {str(n) for n in N_LEVELS} or v not in allowed for k,v in by_n.items()):
        raise ValueError("account_by_n contains invalid levels or account labels")
    if not auto_selection and set(by_n)!={str(n) for n in N_LEVELS}:
        raise ValueError("Fixed account mode requires an account for each n level")
    refs=auth.get("allowed_instance_refs")
    if not isinstance(refs,dict) or set(refs)!=set(allowed):
        raise ValueError("allowed_instance_refs must map every authorized account label to approved opaque instance refs")
    for label,values in refs.items():
        if not isinstance(values,list) or not values or any(not isinstance(v,str) or not re.fullmatch(r"[0-9a-f]{12}",v) for v in values):
            raise ValueError(f"{label} must have one or more 12-character read-only preflight instance refs")
    budgets=auth.get("account_runtime_budget_seconds")
    if not isinstance(budgets,dict) or set(budgets)!=set(allowed):
        raise ValueError("account_runtime_budget_seconds must specify a separate cap for each authorized account")
    for label,value in budgets.items():
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)) or not 0<float(value)<=600:
            raise ValueError(f"{label} runtime cap must be positive and no greater than 600 planning seconds")
    total=auth.get("total_runtime_budget_seconds")
    if isinstance(total,bool) or not isinstance(total,(int,float)) or not math.isfinite(float(total)) or total<0:
        raise ValueError("total_runtime_budget_seconds must be finite nonnegative monitoring data")
    # Legacy maximum_batches is retained in manifests but has no authorization effect.
    margin=auth.get("safety_margin_seconds")
    if isinstance(margin,bool) or not isinstance(margin,(int,float)) or not math.isfinite(float(margin)) or margin<0:
        raise ValueError("safety_margin_seconds must be finite and nonnegative")
    recovery=auth.get("recovery_limits")
    if not isinstance(recovery,dict): raise ValueError("recovery_limits are required")
    reads=recovery.get("max_read_attempts",3);resubmits=recovery.get("max_qpu_resubmissions",0)
    if isinstance(reads,bool) or not isinstance(reads,int) or not 1<=reads<=10:
        raise ValueError("max_read_attempts must be an integer in [1,10]")
    if resubmits!=0:
        raise ValueError("QPU resubmission recovery is not enabled in Phase 8B; max_qpu_resubmissions must be 0")
    actions=auth.get("allowed_actions")
    if not isinstance(actions,list) or not actions or len(set(actions))!=len(actions) or not set(actions)<=N_ACTIONS:
        raise ValueError("allowed_actions must be an explicit subset of the documented campaign actions")
    auto=auth.get("live_auto_submit",False)
    if not isinstance(auto,bool): raise ValueError("live_auto_submit must be a boolean; default is false")
    policy=auth.get("completeness_policy",{})
    if not isinstance(policy,dict): raise ValueError("completeness_policy must be an explicit policy object")
    version=policy.get("version","1.0")
    threshold=policy.get("minimum_valid_pub_fraction_per_treatment",0.8)
    analysis_approved=policy.get("partial_analysis_approved",False)
    partial_approved=policy.get("partial_progression_approved",False)
    insufficient_action=policy.get("insufficient_data_action","STOP")
    insufficient_approved=policy.get("insufficient_data_policy_approved",False)
    if not isinstance(version,str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,32}",version):
        raise ValueError("completeness_policy.version must be a short immutable version label")
    if isinstance(threshold,bool) or not isinstance(threshold,(int,float)) or not math.isfinite(float(threshold)) or not 0<float(threshold)<=1:
        raise ValueError("minimum_valid_pub_fraction_per_treatment must be within (0,1]")
    if not isinstance(analysis_approved,bool) or not isinstance(partial_approved,bool) or not isinstance(insufficient_approved,bool):
        raise ValueError("Completeness policy approvals must be booleans")
    if insufficient_action not in ("STOP","CONTINUE_WITH_WARNING"):
        raise ValueError("insufficient_data_action must be STOP or CONTINUE_WITH_WARNING")
    if insufficient_action!="STOP" and not insufficient_approved:
        raise ValueError("CONTINUE_WITH_WARNING requires explicit insufficient_data_policy_approved=true")
    policy={"version":version,"minimum_valid_pub_fraction_per_treatment":float(threshold),
        "partial_analysis_approved":analysis_approved,"partial_progression_approved":partial_approved,
        "insufficient_data_action":insufficient_action,
        "insufficient_data_policy_approved":insufficient_approved,
        "threshold_is_operational_heuristic":True}
    result={**auth,"total_runtime_budget_seconds":float(total),
        "account_runtime_budget_seconds":{k:float(v) for k,v in budgets.items()},
        "safety_margin_seconds":float(margin),
        "recovery_limits":{"max_read_attempts":reads,"max_qpu_resubmissions":0},
        "allowed_actions":actions,"live_auto_submit":auto,"completeness_policy":policy}
    return result


def _authorization_revision_directory(root,campaign_id: str) -> Path:
    return campaign_root(root,campaign_id)/"authorization_versions"


def load_campaign_authorization(root,campaign_id: str,manifest: dict[str,Any],manifest_sha256: str):
    """Load the immutable base authorization or its validated append-only revision chain."""
    base=validate_operator_authorization(manifest["operator_authorization"])
    directory=_authorization_revision_directory(root,campaign_id)
    paths=sorted(directory.glob("authorization_v*.json"),key=lambda path:path.name) if directory.is_dir() else []
    previous=_sha({"operator_authorization":base})
    expected_version=2
    current=base
    metadata={"version":1,"sha256":previous,"path":None,"is_revision":False}
    for path in paths:
        match=re.fullmatch(r"authorization_v(\d+)\.json",path.name)
        if not match or int(match.group(1))!=expected_version:
            raise ValueError("Campaign authorization revision chain is nonsequential; operator review is required")
        payload=json.loads(path.read_text(encoding="utf-8"))
        core={key:value for key,value in payload.items() if key!="authorization_sha256"}
        digest=_sha(core)
        if (payload.get("schema_version")!=1 or payload.get("campaign_id")!=campaign_id or
            payload.get("parent_manifest_sha256")!=manifest_sha256 or
            payload.get("authorization_version")!=expected_version or
            payload.get("previous_authorization_sha256")!=previous or
            payload.get("authorization_sha256")!=digest):
            raise ValueError("Campaign authorization revision failed its parent/hash-chain validation")
        revised=validate_operator_authorization(payload.get("operator_authorization",{}))
        if (revised["account_by_n"].get("5",base["account_by_n"].get("5"))
            !=base["account_by_n"].get("5")):
            raise ValueError("Versioned authorization must preserve the existing official n=5 account mapping")
        current=revised;previous=digest;metadata={"version":expected_version,"sha256":digest,
            "path":str(path),"is_revision":True};expected_version+=1
    return current,metadata


def stage_campaign_authorization_revision(root,campaign_id: str,authorization: dict[str,Any],registry: JobRegistry,*,approved: bool=False):
    """Append a new operator-approved authorization without editing the campaign manifest."""
    if not approved:
        raise PermissionError("Versioned authorization activation requires explicit operator approval")
    status=registry.independent_campaign_status(campaign_id)
    if not status.get("exists"):raise KeyError(campaign_id)
    manifest=status["manifest"];base=validate_operator_authorization(manifest["operator_authorization"])
    revised=validate_operator_authorization(authorization)
    if (revised["account_by_n"].get("5",base["account_by_n"].get("5"))
            !=base["account_by_n"].get("5")):
        raise ValueError("A revision may not change n=5; it must preserve the official data/account mapping")
    _current,_meta=load_campaign_authorization(root,campaign_id,manifest,status["manifest_sha256"])
    version=_meta["version"]+1
    core={"schema_version":1,"campaign_id":campaign_id,"authorization_version":version,
        "parent_manifest_sha256":status["manifest_sha256"],"previous_authorization_sha256":_meta["sha256"],
        "operator_authorization":revised}
    payload={**core,"authorization_sha256":_sha(core)}
    path=_authorization_revision_directory(root,campaign_id)/f"authorization_v{version:03d}.json"
    _exclusive_json(path,payload)
    return {"campaign_id":campaign_id,"authorization_version":version,"path":str(path),
        "authorization_sha256":payload["authorization_sha256"],"campaign_manifest_modified":False,
        "live_submission_performed":False}


def build_campaign_manifest(root,campaign_id: str,operator_authorization: dict[str,Any]) -> dict[str,Any]:
    if not campaign_id or campaign_id in (".","..") or any(c in campaign_id for c in "/\\:"):
        raise ValueError("campaign_id must be one safe path component")
    auth=validate_operator_authorization(operator_authorization)
    levels=[]
    for index,n in enumerate(N_LEVELS):
        experiment_id=f"{campaign_id}-n{n}"
        target=build_target_manifest(root,target_n=n,chunk_n=5,shots=4096,experiment_id=experiment_id)
        levels.append({"level_index":index,"target_n":n,"experiment_id":experiment_id,
            "target_id":target.target_id,"manifest":target.to_dict(),"manifest_sha256":target.sha256,
            "account_label":auth["account_by_n"].get(str(n)) if not auth.get("account_selection")=="highest_available_quota" else None,"required_batches":n//5,
            "required_jobs":8*(n//5),"requested_pubs":8*n})
    core={"schema_version":1,"campaign_id":campaign_id,"campaign_type":"independent_n_vqe_mitigation",
        "levels":levels,"total_levels":6,"total_batches_required":21,"total_estimator_jobs":168,
        "total_requested_pubs":840,"chunk_n":5,"shots_per_pub":4096,"backend":"ibm_fez",
        "source_run":"run_02","source_bundle":levels[0]["manifest"]["source_bundle"],
        "input_hashes":levels[0]["manifest"]["input_hashes"],"physical_qubits":list(range(140,147)),
        "theta_count":21,"observable_count":7,"treatment_ids":list(levels[0]["manifest"]["treatment_ids"]),
        "operator_authorization":auth,"live_submission_enabled_by_default":False,
        "calibration_data_excluded":True,"created_utc":utc_now()}
    core["manifest_sha256"]=_sha(core)
    return core


def _exclusive_json(path: Path,payload: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True,default=str)
    if path.exists():
        if path.read_text(encoding="utf-8")!=encoded: raise FileExistsError(f"Immutable campaign artifact differs; refusing overwrite: {path}")
        return
    with path.open("x",encoding="utf-8") as handle:
        handle.write(encoded);handle.flush();os.fsync(handle.fileno())


def plan_independent_campaign(root,campaign_id: str,authorization: dict[str,Any],registry: JobRegistry) -> dict[str,Any]:
    manifest=build_campaign_manifest(root,campaign_id,authorization)
    directory=campaign_root(root,campaign_id)
    _exclusive_json(directory/"campaign_manifest.json",manifest)
    for level in manifest["levels"]:
        target=level["manifest"]|{"manifest_sha256":level["manifest_sha256"]}
        _exclusive_json(directory/f"n{level['target_n']}"/"experiment_manifest.json",target)
        from adaptive_targets import register_target
        from experiment_manifest import TargetManifest
        values={**level["manifest"],"physical_qubits":tuple(level["manifest"]["physical_qubits"]),
            "treatment_ids":tuple(level["manifest"]["treatment_ids"]),"treatments":tuple(level["manifest"]["treatments"])}
        register_target(TargetManifest(**values),registry)
    registry.register_independent_campaign(manifest)
    return {"manifest":manifest,"manifest_path":str(directory/"campaign_manifest.json"),
            "batch_count":21,"job_count":168,"requested_pub_count":840,
            "live_submission_enabled":False,"submission_performed":False}


def dry_run_independent_campaign(root,campaign_id: str,registry: JobRegistry) -> dict[str,Any]:
    status=registry.independent_campaign_status(campaign_id)
    if not status.get("exists"): raise KeyError(campaign_id)
    manifest=status["manifest"]
    levels=[]
    for item in status["levels"]:
        target=item["target_status"]
        counts=target.get("cumulative_validated_pubs_by_treatment",{})
        level_manifest=next(entry for entry in manifest["levels"] if entry["level_index"]==item["level_index"])
        blocks=target.get("blocks",[])
        sequence=len(blocks)+1
        levels.append({"n":item["target_n"],"experiment_id":item["experiment_id"],
            "target_id":item["target_id"],"account_label":(None if item["state"]=="PLANNED" else level_manifest.get("account_label")), "account_selection_pending":item["state"]=="PLANNED",
            "required_batches":item["target_n"]//5,"registered_batches":len(blocks),
            "next_block_id":(f"{item['experiment_id']}-block-{sequence:03d}" if sequence<=item["target_n"]//5 else None),
            "planned_jobs_in_next_batch":8 if sequence<=item["target_n"]//5 else 0,
            "planned_pubs_in_next_batch":40 if sequence<=item["target_n"]//5 else 0,
            "backend":"ibm_fez","shots_per_pub":4096,"chunk_n":5,
            "treatment_ids":level_manifest["manifest"]["treatment_ids"],
            "input_hashes":level_manifest["manifest"]["input_hashes"],
            "result_directory":str(repository_root(root)/MITIGATION_REL/"output"/"02_run_level_data"/"adaptive_targets"/item["target_id"]),
            "batch_records_directory":str(repository_root(root)/MITIGATION_REL/"output"/"03_IBM_execution_records"/"ai_assistant"/"independent_n_campaigns"/campaign_id/f"n{item['target_n']}"),
            "runtime_preflight_performed":False,"quota_gate_performed":False,
            "validated_pubs_by_treatment":counts,"ready_to_advance":item["state"]=="COMPLETE" or not any(
                not block.get("progression_eligible") and block["status"] != "CONFIRMED_NOT_SUBMITTED" for block in blocks)})
    return {"campaign_id":campaign_id,"state":status["state"],"levels":levels,
        "planned_batches":21,"planned_jobs":168,"requested_pubs":840,
        "live_auto_submit_authorized":manifest["operator_authorization"]["live_auto_submit"],
        "live_submission_enabled":False,"batch_created":False,"submission_performed":False}


def _target_from_level(root, level: dict[str,Any], *, registry=None):
    """Resolve immutable embedded targets from either a manifest level or a status row.

    Status rows contain IDs/checkpoints, not the target manifest. Its authoritative
    copy is in independent_n_campaigns.manifest_json -> levels[].manifest.
    The existing write-free builder verifies current frozen scientific provenance.
    """
    identity=level.get("target_id","<unknown>")
    resolved=level
    if "manifest" not in level:
        campaign_id=level.get("campaign_id")
        if not campaign_id:
            raise RuntimeError(f"Cannot resolve target {identity}: level lacks embedded manifest and campaign_id")
        registry=registry or JobRegistry(JobRegistry.default_database(root),readonly=True)
        status=registry.independent_campaign_status(campaign_id)
        parent=status.get("manifest")
        if not isinstance(parent,dict):
            raise RuntimeError(f"Cannot resolve target {identity}: immutable campaign manifest unavailable")
        digest=_sha({k:v for k,v in parent.items() if k!="manifest_sha256"})
        if digest!=status.get("manifest_sha256") or digest!=parent.get("manifest_sha256"):
            raise RuntimeError(f"Cannot resolve target {identity}: campaign manifest hash mismatch")
        matches=[item for item in parent.get("levels",[]) if item.get("target_id")==identity]
        if len(matches)!=1:
            raise RuntimeError(f"Cannot resolve target {identity}: expected one immutable campaign level, found {len(matches)}")
        resolved=matches[0]
        for key in ("level_index","target_n","experiment_id","target_id"):
            if level.get(key)!=resolved.get(key):
                raise RuntimeError(f"Cannot resolve target {identity}: persisted level {key} mismatch")
    needed=("manifest","target_n","experiment_id","target_id","manifest_sha256")
    missing=[key for key in needed if key not in resolved]
    if missing:
        raise RuntimeError(f"Cannot resolve target {identity}: missing immutable fields {missing}")
    saved=resolved["manifest"]
    if not isinstance(saved,dict) or _sha(saved)!=resolved["manifest_sha256"]:
        raise RuntimeError(f"Cannot resolve target {identity}: immutable target manifest hash mismatch")
    target=build_target_manifest(root,target_n=int(resolved["target_n"]),chunk_n=5,shots=4096,
                                 experiment_id=resolved["experiment_id"])
    if target.target_id!=resolved["target_id"] or target.sha256!=resolved["manifest_sha256"]:
        raise RuntimeError("Independent n target differs from its immutable campaign manifest")
    if saved.get("input_hashes")!=target.input_hashes or saved.get("source_run")!="run_02":
        raise RuntimeError("Independent n manifest no longer matches frozen Fixed ISA run_02")
    return target


def _campaign_report(directory: Path, name: str, payload: dict[str,Any]) -> Path:
    """Write a campaign-scoped report once; reports are immutable evidence."""
    path=directory/name
    if name=='scientific_report.json' and path.exists() and json.loads(path.read_text(encoding='utf-8'))!=payload:
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
        path=directory/('scientific_report_'+digest+'.json')
    _exclusive_json(path,payload)
    if json.loads(path.read_text(encoding="utf-8"))!=payload:
        raise RuntimeError("Campaign report read-back verification failed: "+str(path))
    return path


def _validated_pub_fractions(registry: JobRegistry,target,*,logical_block_id: str | None=None) -> dict[str,float]:
    """Return valid PUB fractions from this independent target only."""
    values=registry.validated_pub_values(target.target_id)
    counts={name:0 for name in target.treatment_ids}
    for row in values:
        if logical_block_id is None or row["logical_block_id"]==logical_block_id:
            if row["treatment_id"] in counts:counts[row["treatment_id"]]+=1
    denominator=target.chunk_n if logical_block_id else target.target_n
    return {name:min(1.0,counts[name]/float(denominator)) for name in target.treatment_ids}


def _partial_policy_allows(policy: dict[str,Any],fractions: dict[str,float]) -> tuple[bool,dict[str,Any]]:
    threshold=float(policy["minimum_valid_pub_fraction_per_treatment"])
    below={name:value for name,value in fractions.items() if value+1e-12<threshold}
    approved=bool(policy.get("partial_analysis_approved") and policy.get("partial_progression_approved"))
    insufficient_override=(bool(below) and policy.get("insufficient_data_action")=="CONTINUE_WITH_WARNING"
        and bool(policy.get("insufficient_data_policy_approved")))
    return approved and (not below or insufficient_override),{
        "policy_version":policy["version"],"minimum_valid_pub_fraction_per_treatment":threshold,
        "threshold_is_operational_heuristic":True,"valid_fraction_by_treatment":fractions,
        "below_threshold":below,"insufficient_data_action":policy["insufficient_data_action"],
        "partial_analysis_approved":bool(policy.get("partial_analysis_approved")),
        "partial_progression_approved":bool(policy.get("partial_progression_approved")),
        "eligible":approved and (not below or insufficient_override)}


def _ai_event(ai_mode: str, event: dict[str,Any], *, explicit: bool=False) -> dict[str,Any] | None:
    if ai_mode not in ("python","ai-assisted","autonomous"):
        raise ValueError("ai_mode must be python, ai-assisted, or autonomous")
    if ai_mode=="python" or not explicit:
        return None
    from ai_assistance import request_diagnosis
    # The wrapper allowlists fields and has no tools or mutation capability.
    try: return request_diagnosis(event)
    except Exception as exc:
        return {"available":False,"reason":"AI diagnostics unavailable: "+type(exc).__name__,
                "ai_actions_executed":False}


def control_independent_campaign(root,campaign_id: str,registry: JobRegistry,action: str) -> dict[str,Any]:
    if action not in ("pause","stop"):
        raise ValueError("Campaign control action must be pause or stop")
    status=registry.independent_campaign_status(campaign_id)
    if not status.get("exists"): raise KeyError(campaign_id)
    if status["current_level_index"]>=6 or status["state"]=="COMPLETE":
        return {"campaign_id":campaign_id,"state":"COMPLETE","stop_requested":False,
                "submission_performed":False,"cancellation_performed":False}
    directory=campaign_root(root,campaign_id)
    marker=directory/(".campaign.pause" if action=="pause" else ".campaign.stop")
    marker.parent.mkdir(parents=True,exist_ok=True)
    with marker.open("a",encoding="utf-8") as handle:
        handle.write(utc_now()+"\n");handle.flush();os.fsync(handle.fileno())
    registry.checkpoint_independent_campaign(campaign_id,state="PAUSED" if action=="pause" else "STOPPED",
        level_index=status["current_level_index"],checkpoint={"control_marker":str(marker)},
        event_type="CAMPAIGN_"+action.upper()+"_REQUESTED")
    return {"campaign_id":campaign_id,"state":action.upper()+"_REQUESTED","marker":str(marker),
            "submission_performed":False,"cancellation_performed":False}


def _read_only_repaired_progress(registry: JobRegistry, level: dict[str, Any]) -> dict[str, Any]:
    """Display effective logical-PUB coverage; never modify provider or campaign state.

    Original counts are retained separately for historical traceability.
    A replacement counts only when JobRegistry.repaired_pub_values selects it.
    """
    original = dict(level["target_status"].get("cumulative_validated_pubs_by_treatment", {}) or {})
    target_id = level["target_id"]
    expected_n = int(level["target_n"])
    manifest = level.get("manifest") or {}
    treatments = tuple(manifest.get("treatment_ids") or original.keys())
    records = registry.repaired_pub_values(target_id)
    slots = {}
    replacement_slots = set()
    for item in records:
        treatment = item.get("treatment_id")
        index = item.get("logical_pub_index")
        if treatment not in treatments or isinstance(index, bool) or not isinstance(index, int):
            continue
        if not 1 <= index <= expected_n:
            continue
        key = (treatment, index)
        slots[key] = item
        if item.get("source") == "REPLACEMENT":
            replacement_slots.add(key)
    effective = {name: sum((name, i) in slots for i in range(1, expected_n + 1)) for name in treatments}
    from batch_progression import evaluate_batch_progression
    blocks=level["target_status"].get("blocks",[])
    decisions=[evaluate_batch_progression(registry,target_id,b["logical_block_id"]) for b in blocks]
    complete = (bool(treatments) and bool(decisions) and
                all(effective[name] == expected_n for name in treatments) and
                all(decision["eligible"] for decision in decisions))
    return {
        "progress": effective,
        "original_progress": original,
        "validated_replacement_pubs": len(replacement_slots),
        "repaired_valid_pubs": sum(effective.values()),
        "expected_pubs": expected_n * len(treatments),
        "repaired_complete": complete,
        "progress_source": "selected_repaired_logical_pub_slots",
    }



def _verified_repaired_block_evidence(root, registry, target, block_id: str) -> tuple[bool, dict]:
    """Reuse validated registry evidence without rerunning scientific analysis."""
    from batch_progression import evaluate_batch_progression
    decision=evaluate_batch_progression(registry,target,block_id)
    return decision["eligible"], {"raw_archives_verified":decision["eligible"],
        "provenance_verified":decision["frozen_inputs_verified"],"progression_decision":decision}


def _repaired_block_complete(registry, target, block_id: str) -> bool:
    """Compatibility wrapper for the shared scientific progression policy."""
    from batch_progression import evaluate_batch_progression
    return evaluate_batch_progression(registry,target,block_id)["eligible"]


def inspect_independent_campaign(registry: JobRegistry,campaign_id: str,root: str | Path | None=None) -> dict[str,Any]:
    status=registry.independent_campaign_status(campaign_id)
    if not status.get("exists"): return status
    manifest=status["manifest"]
    authorization,auth_meta=(manifest["operator_authorization"],{"version":1,"is_revision":False,"path":None})
    if root is not None:
        authorization,auth_meta=load_campaign_authorization(root,campaign_id,manifest,status["manifest_sha256"])
    usage=_campaign_usage(registry,manifest,authorization)
    levels=[]
    for level in status["levels"]:
        jobs=[]
        for row in registry.rows():
            if row.get("target_id")!=level["target_id"]:continue
            metadata=row.get("metadata",{})
            remote=metadata.get("last_remote_monitor",{})
            jobs.append({"job_id":row.get("job_id"),"treatment_id":row["treatment_id"],
                "ibm_job_status":remote.get("status","UNKNOWN"),
                "local_submission_state":row.get("submission_state"),"logical_block_id":row["logical_block_id"],
                "batch_id":row.get("batch_id"),"qpu_usage_seconds":row.get("qpu_usage_seconds")})
        coverage = _read_only_repaired_progress(registry, level)
        levels.append({"n":level["target_n"],"experiment_id":level["experiment_id"],"target_id":level["target_id"],
            "state":level["state"],**coverage,
            "blocks":level["target_status"].get("blocks",[]),"jobs":jobs})
    return {"campaign_id":campaign_id,"state":status["state"],"current_level_index":status["current_level_index"],
        "manifest_sha256":status["manifest_sha256"],"levels":levels,
        "execution_account_labels":list(authorization["allowed_accounts"]),
        "usage":usage,"events":status["events"]}


class ScientificCompletionPending(RuntimeError):
    """Dataset is complete; retry only downstream analysis/export."""


def _complete_science(*args):
    try:
        return _finalize_level(*args)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ScientificCompletionPending(str(exc)) from exc
    finally:
        import sys, gc
        if "matplotlib.pyplot" in sys.modules:
            sys.modules["matplotlib.pyplot"].close("all")
        gc.collect()


def _finalize_level(root,campaign_id: str,level_index: int,level: dict[str,Any],target,
                   registry: JobRegistry,directory: Path) -> Path:
    """Run downstream cumulative analysis and verify publication exports."""
    from adaptive_targets import analyze_target
    analysis=analyze_target(root,target,registry)
    if analysis.get("artifacts_verified") is not True:
        raise RuntimeError("Cumulative analysis or required figure validation did not pass")
    from scientific_exports import export_n_level_results
    paper_export=export_n_level_results(root,target,registry,analysis)
    analysis_manifest=json.loads((Path(analysis["output"])/"analysis_manifest.json").read_text(encoding="utf-8"))
    stable_analysis={key:analysis.get(key) for key in ("target_id","output","validated_pubs_by_treatment",
        "blocks_included","artifacts_verified") if key in analysis}
    report_state=analysis_manifest.get("dataset_status","COMPLETE")
    report={"campaign_id":campaign_id,"experiment_id":target.experiment_id,"target_id":target.target_id,
        "n":target.target_n,"state":report_state,"validated_pubs_per_treatment":target.target_n,
        "analysis":stable_analysis,"paper_export":paper_export,"source_run":"run_02","input_hashes":target.input_hashes,
        "account_labels":sorted({item["account_label"] for item in registry.target_status(target.target_id)["blocks"]}),
        "generated_utc":analysis_manifest["created_utc"]}
    report_path=_campaign_report(directory/f"n{target.target_n}","scientific_report.json",report)
    return report_path


def _campaign_completion_verification(root,registry: JobRegistry,status: dict[str,Any]) -> dict[str,Any]:
    """Scientific completeness is independent of preserved original execution state."""
    from campaign_comparison import inspect_campaign
    evidence=inspect_campaign(root,status['campaign_id'],registry=registry,status=status)
    return {'ready':evidence['ready'],'verified_n_levels':evidence['verified_n_levels'],
            'failures':evidence['blockers'],'levels':evidence['levels']}


def run_independent_campaign(root,campaign_id: str,registry: JobRegistry, *,
        authorize_live_campaign: bool=False, ai_mode: str="python", explicit_ai: bool=False,
        account_label: str | None=None,
        once: bool=False, resume: bool=False, service_factory: Callable[[str],Any] | None=None,
        sleeper: Callable[[float],None]=time.sleep, poll_seconds: int=60,
        final_config: dict[str,Any] | None=None,_worker_lock_held: bool=False, automatic_science: bool=False) -> dict[str,Any]:
    """Run one-at-a-time n/Batch pipeline. Only explicit, fully gated mode can touch IBM.

    Original jobs are never resubmitted. Invalid PUB slots may receive a distinct
    replacement job only through the separately gated final-config path.
    ``service_factory`` is injection-only for offline tests; production uses saved profiles.
    """
    from worker_runtime import campaign_worker_lock, RetryingReadOnlyService
    status=registry.independent_campaign_status(campaign_id)
    if not status.get("exists"): raise KeyError(campaign_id)
    manifest=status["manifest"]
    if final_config is not None:
        from final_configuration import validate_final_config,campaign_authorization_from_final_config
        final_config=validate_final_config(final_config)
        if final_config.get("allowed_backend") != manifest.get("backend"):
            raise RuntimeError("Final configuration backend does not match the immutable campaign")
        if not final_config.get("live_submission_enabled",False):
            return {"campaign_id":campaign_id,"state":"BLOCKED_AUTHORIZATION","live_submission_enabled":False,
                "reason":"Live submission is disabled by final_config.json","submission_performed":False,"batch_created":False}
        # The distributable application's final_config is the execution policy.
        # Old campaign authorization files remain audit data, not active flags.
        authorization=campaign_authorization_from_final_config(final_config)
        authorization_revision={"source":"final_config.json","legacy_authorization_read_only":True}
    else:
        authorization,authorization_revision=load_campaign_authorization(root,campaign_id,manifest,status["manifest_sha256"])
    if not authorize_live_campaign or not authorization.get("live_auto_submit",False):
        return {"campaign_id":campaign_id,"state":"BLOCKED_AUTHORIZATION","live_submission_enabled":False,
            "reason":"Both operator live_auto_submit=true and --authorize-live-campaign are required",
            "submission_performed":False,"batch_created":False}
    if not authorization["allowed_accounts"]:
        raise AccountSelectionUnavailable({})
    required_actions={"prepare","preflight","dry-run","submit","monitor","collect","validate"}
    missing_actions=required_actions-set(authorization["allowed_actions"])
    if missing_actions:
        raise RuntimeError("Campaign authorization omits required pipeline actions: "+", ".join(sorted(missing_actions)))
    if manifest.get("calibration_data_excluded") is not True or manifest.get("source_run")!="run_02":
        raise RuntimeError("Campaign manifest is not an independent production run_02 campaign")
    directory=campaign_root(root,campaign_id)
    stop=directory/".campaign.stop";pause=directory/".campaign.pause"
    if (status.get("checkpoint") or {}).get("pause_class")=="PAUSED_OPERATOR_ACTION" and not resume:
        return {"campaign_id":campaign_id,"state":"PAUSED","resume_required":True,
            "reason":"Explicit --resume approval is required for an operator-action pause",
            "submission_performed":False}
    if pause.exists():
        if not resume:
            return {"campaign_id":campaign_id,"state":"PAUSED","reason":"Use resume-independent-n-campaign to clear the pause marker",
                "submission_performed":False}
    if stop.exists(): raise RuntimeError("A stopped campaign cannot be resumed; create a new campaign ID after review")
    if status["state"]=="STOPPED": raise RuntimeError("A stopped campaign cannot be resumed")
    if explicit_ai and ai_mode!="python":
        _ai_event(ai_mode,{"campaign_id":campaign_id,"event_class":"OPERATOR_REQUEST",
            "local_state":status["state"],"safe_summary":"Operator explicitly requested one campaign status diagnosis"},explicit=True)
    reports=[]
    initial_level_index=int(status["current_level_index"])
    rejected_accounts_by_block={}
    lock_context=nullcontext() if _worker_lock_held else campaign_worker_lock(directory)
    with lock_context:
        if resume and pause.exists():pause.unlink()
        from batch_scheduler import reconcile_campaign_intents
        reconcile_campaign_intents(root,registry,campaign_id,authorization,service_factory)
        while True:
            status=registry.independent_campaign_status(campaign_id)
            index=int(status["current_level_index"])
            if index>=6:
                from batch_progression import evaluate_batch_progression
                complete=all(
                    len(item["target_status"]["blocks"]) == int(item["target_n"]) // int(item["target_status"]["chunk_n"])
                    and all(evaluate_batch_progression(registry,item["target_id"],block["logical_block_id"])["eligible"]
                            for block in item["target_status"]["blocks"])
                    for item in status["levels"])
                if not complete:
                    raise RuntimeError("Campaign data collection has incomplete logical PUB evidence")
                if automatic_science:
                    for level_index, completed_level in enumerate(manifest["levels"]):
                        completed_target=_target_from_level(root,completed_level)
                        _complete_science(root,campaign_id,level_index,completed_level,completed_target,registry,directory)
                return inspect_independent_campaign(registry,campaign_id)|{"reports":reports,
                    "state":"COMPLETE",
                    "data_collection_complete":True,"publication_exports_verified":automatic_science,
                    "submission_performed":bool(reports)}
            if stop.exists():
                registry.checkpoint_independent_campaign(campaign_id,state="STOPPED",level_index=index,event_type="CAMPAIGN_STOPPED")
                return inspect_independent_campaign(registry,campaign_id)|{"reports":reports,"submission_performed":bool(reports)}
            if pause.exists():
                registry.checkpoint_independent_campaign(campaign_id,state="PAUSED",level_index=index,event_type="CAMPAIGN_PAUSED")
                return inspect_independent_campaign(registry,campaign_id)|{"reports":reports,"submission_performed":bool(reports)}
            level=manifest["levels"][index];target=_target_from_level(root,level)
            progress=registry.target_status(target.target_id)
            blocks=progress.get("blocks",[])
            # Existing jobs must always be accessed through their registered account.
            # For a new Batch, this value is only a placeholder: the fresh quota
            # selector below chooses the actual submitting account.
            account=_account_for_level(target,authorization,blocks)
            if account not in authorization["allowed_accounts"]: raise RuntimeError("Selected n-level account is not enabled")
            if target.target_n==5:
                if not blocks:
                    raise RuntimeError("n=5 is reserved for its existing official dataset; no new n=5 Batch may be created")
            incomplete=next((item for item in blocks if not _repaired_block_complete(registry,target,item["logical_block_id"])),None)
            if incomplete is not None:
                block_id=incomplete["logical_block_id"]
            else:
                sequence=len(blocks)+1
                if sequence>target.blocks_required:
                    progress_counts=progress.get("cumulative_validated_pubs_by_treatment",{})
                    from targeted_recovery import repaired_dataset_status
                    repaired_final=repaired_dataset_status(target,registry)
                    fully_complete=(len(blocks)==target.blocks_required
                        and repaired_final.get("analysis_eligible") is True
                        and all(repaired_final.get("counts_by_treatment",{}).get(name)==target.target_n for name in target.treatment_ids)
                        and all(_repaired_block_complete(registry,target,item["logical_block_id"]) for item in blocks))
                    if fully_complete:
                        if automatic_science:_complete_science(root,campaign_id,index,level,target,registry,directory)
                        registry.complete_independent_level(campaign_id,index)
                        continue
                    registry.checkpoint_independent_campaign(campaign_id,state="PAUSED",level_index=index,
                        level_state="RECOVERY_REQUIRED",checkpoint={"reason":"incomplete data cannot be analyzed or progressed under complete-only policy",
                            "batch_execution_states":[item.get("batch_execution_status", item.get("status", "UNKNOWN")) for item in blocks]},
                        event_type="CAMPAIGN_INCOMPLETE_BLOCK")
                    raise RuntimeError("Independent n is incomplete; recover or replace invalid PUB slots before analysis or progression")
                block_id=f"{target.experiment_id}-block-{sequence:03d}"
            current_rows=registry.rows(block_id)
            archived_recovery=False
            if current_rows and all(row["submission_state"] in ("COMPLETED","FAILED","CANCELLED") for row in current_rows):
                # Verified immutable source data permits recovery even when the
                # original account can no longer authenticate or submit.
                from adaptive_targets import audit_incomplete_batch_archives
                try:
                    audit=audit_incomplete_batch_archives(root,target,registry,block_id)
                    archived_recovery=(audit.get("raw_archives_verified") is True and audit.get("provenance_verified") is True)
                except (OSError,ValueError,RuntimeError):
                    archived_recovery=False
            if current_rows:
                registered_accounts={row["account_label"] for row in current_rows}
                if len(registered_accounts)!=1:
                    raise RuntimeError("Existing Batch has inconsistent registered accounts")
                account=next(iter(registered_accounts))
                if not archived_recovery and account not in authorization["allowed_accounts"]:
                    raise RuntimeError("Existing Batch account is not authorized")
                if not archived_recovery and not authorization["allowed_instance_refs"].get(account):
                    raise RuntimeError("Existing Batch account lacks authorized instance scope")
            service=None;active=None
            if current_rows:
                if any(row["submission_state"] in ("UNKNOWN","SUBMITTING","RESERVED") for row in current_rows):
                    registry.checkpoint_independent_campaign(campaign_id,state="BLOCKED",level_index=index,
                        level_state="BLOCKED",checkpoint={"block_id":block_id,"reason":"ambiguous or interrupted submission requires reconciliation"},
                        event_type="CAMPAIGN_AMBIGUOUS_SUBMISSION")
                    _ai_event(ai_mode,{"campaign_id":campaign_id,"sequence_no":index+1,"event_class":"SUBMISSION_AMBIGUITY",
                        "local_state":"BLOCKED","safe_summary":"Existing block has unresolved submit state; no retry was attempted"},explicit=True)
                    raise RuntimeError("Existing Batch has ambiguous submission state; reconcile existing IDs before resume")
                if not archived_recovery:
                    from account_config import load_saved_account_profiles,make_saved_profile_service
                    service=service_factory(account) if service_factory else make_saved_profile_service(
                        load_saved_account_profiles((account,))[account])
                    active=service.active_instance() if callable(getattr(service,"active_instance",None)) else None
                    if active:
                        from read_only_preflight import _instance_ref
                        if _instance_ref(active) not in authorization["allowed_instance_refs"][account]:
                            raise RuntimeError("Current saved profile instance is not authorized for this campaign")
            elif len(blocks)<target.blocks_required:
                if target.target_n==5:
                    raise RuntimeError("n=5 has no original Batch to recover; new execution starts at n=10")
                usage=_campaign_usage(registry,manifest,authorization)
                # Prepare is local and read-only with respect to the frozen source.
                from vqe_adapter import prepare_block
                record=__import__("vqe_adapter").record_directory(root,block_id)
                if not (record/"round_plan.json").is_file():
                    try: prepare_block(root,block_id,shots=4096,n=5)
                    except Exception as exc:
                        diagnosis=_ai_event(ai_mode,{"campaign_id":campaign_id,"sequence_no":index+1,
                            "event_class":"PREPARE_ERROR","local_state":"BLOCKED",
                            "safe_summary":"Offline VQE preparation raised "+type(exc).__name__},explicit=True)
                        registry.checkpoint_independent_campaign(campaign_id,state="BLOCKED",level_index=index,
                            level_state="BLOCKED",checkpoint={"block_id":block_id,"error_type":type(exc).__name__,
                            "ai_diagnosis":diagnosis},event_type="CAMPAIGN_PREPARE_ERROR")
                        raise
                from account_config import load_saved_account_profiles,make_saved_profile_service
                from read_only_preflight import preflight_account,_instance_ref,frozen_bundle_summary
                from vqe_adapter import verify_frozen_inputs
                def frozen_validator():
                    verify_frozen_inputs(root)
                    return {"source_run":"run_02","input_sha256":target.input_hashes}
                from experiment_manifest import build_manifest
                runtime_manifest=build_manifest(root,n=5,replicate_blocks=1,shots=4096,experiment_id=target.experiment_id)
                from runtime_estimator import historical_batch_estimate as _historical_runtime_estimate
                try:
                    estimate=_historical_runtime_estimate(root,runtime_manifest,require_defensible=False)
                except Exception as exc:
                    diagnosis=_ai_event(ai_mode,{"campaign_id":campaign_id,"sequence_no":index+1,
                        "event_class":"RUNTIME_EVIDENCE_UNAVAILABLE","local_state":"BLOCKED",
                        "safe_summary":"Defensible whole-Batch runtime evidence unavailable: "+type(exc).__name__},explicit=True)
                    registry.checkpoint_independent_campaign(campaign_id,state="BLOCKED",level_index=index,
                        level_state="BLOCKED",checkpoint={"block_id":block_id,"error_type":type(exc).__name__,
                        "ai_diagnosis":diagnosis},event_type="CAMPAIGN_RUNTIME_EVIDENCE_BLOCKED")
                    raise
                chosen,service,preflight=preflight_and_select_account(root,target,
                    runtime_estimate=estimate,usage=usage,authorization=authorization,
                    excluded=rejected_accounts_by_block.get(block_id,()),service_factory=service_factory,
                    attempts=authorization["recovery_limits"]["max_read_attempts"])
                account=chosen["account_label"]
                registry.record_independent_target_event(target.target_id,"BATCH_ACCOUNT_SELECTED",
                    {"block_id":block_id,"account_label":account,"account_decisions":chosen["account_reports"],
                     "input_hashes":target.input_hashes,"submission_performed":False})
                active=service.active_instance()
                reference=_instance_ref(active)
                if reference not in authorization["allowed_instance_refs"].get(account,()):
                    raise RuntimeError("Selected account instance is not authorized")
                checked=datetime.fromisoformat(preflight["checked_utc"].replace("Z","+00:00"))
                pf_dir=directory/f"n{target.target_n}"/"preflight_reports"
                pf_dir.mkdir(parents=True,exist_ok=True)
                _exclusive_json(pf_dir/f"preflight_{account}_{checked.strftime('%Y%m%dT%H%M%S%fZ')}.json",preflight)
                from adaptive_targets import evaluate_new_batch,remaining_seconds_from_report,usage_fields_from_report
                from vqe_adapter import treatment_options
                treatment_options_json={}
                for treatment in target.treatment_ids:
                    options=treatment_options(treatment,4096,root)
                    treatment_options_json[treatment]=(options.model_dump(mode="json") if callable(getattr(options,"model_dump",None)) else str(options))
                _campaign_report(directory/f"n{target.target_n}",f"batch_{len(blocks)+1:03d}_dry_run.json",
                    {"experiment_id":target.experiment_id,"target_id":target.target_id,"target_manifest_sha256":target.sha256,
                     "source_run":"run_02","input_hashes":target.input_hashes,"backend":"ibm_fez",
                     "physical_qubits":list(range(140,147)),"theta_count":21,"observable_count":7,
                     "shots_per_pub":4096,"chunk_n":5,"jobs":[{"treatment_id":t,"submission_order":i+1,
                        "requested_pubs":5,"estimator_options":treatment_options_json[t]} for i,t in enumerate(target.treatment_ids)],
                     "batch_mode":"Runtime Batch","requested_pub_count":40,"submission_performed":False,
                     "batch_created":False,"job_ids_created":False})
                quota=remaining_seconds_from_report(preflight)
                if quota is None: raise RuntimeError("Fresh provider remaining quota is missing or ambiguous")
                provider_usage=usage_fields_from_report(preflight)
                if provider_usage is None: raise RuntimeError("Fresh provider quota fields or account-wide scope are unavailable")
                account_cap=authorization["account_runtime_budget_seconds"][account]-usage["per_account"][account]["consumed_seconds"]-usage["per_account"][account]["reserved_seconds"]
                campaign_cap=authorization["total_runtime_budget_seconds"]-usage["campaign_consumed_seconds"]-usage["campaign_reserved_seconds"]
                from quota import available_quota_seconds, campaign_budget_monitor
                reservations=usage["provider_reservations"][account]
                if reservations["ambiguous"]:
                    raise RuntimeError("Provider quota reservation overlap is ambiguous; reconcile existing jobs")
                effective_remaining=available_quota_seconds(provider_remaining_seconds=quota,
                    outstanding_reservations_seconds=reservations["reserved_seconds"],
                    local_budget_remaining_seconds=max(0.0,account_cap))
                gate=evaluate_new_batch(target,account_label=account,preflight_report=preflight,
                    runtime_estimate=estimate,remaining_seconds=effective_remaining,
                    safety_margin_seconds=authorization["safety_margin_seconds"])
                gate_path=directory/f"n{target.target_n}"/f"batch_{len(blocks)+1:03d}_quota_gate_{checked.strftime('%Y%m%dT%H%M%S%fZ')}.json"
                _campaign_report(directory/f"n{target.target_n}",gate_path.name,{"experiment_id":target.experiment_id,
                    "n":target.target_n,"block_id":block_id,"account_label":account,"preflight_checked_utc":preflight.get("checked_utc"),
                    "provider_remaining_seconds":quota,"authorization_account_remaining_seconds":account_cap,
                    "campaign_remaining_seconds":campaign_cap,"effective_remaining_seconds":effective_remaining,
                    **campaign_budget_monitor(authorization,usage,estimate.stress_block_seconds+authorization["safety_margin_seconds"]),
                    "runtime_upper_bound_seconds":estimate.stress_block_seconds,"safety_margin_seconds":authorization["safety_margin_seconds"],
                    "eligible":gate["eligible"],"gate":gate,"batch_created":False})
                if not gate["eligible"]:
                    diagnosis=_ai_event(ai_mode,{"campaign_id":campaign_id,"sequence_no":index+1,
                        "event_class":"PREFLIGHT_OR_QUOTA_REFUSAL","local_state":"BLOCKED",
                        "safe_summary":"Fresh Batch eligibility checks refused this submission"},explicit=True)
                    registry.checkpoint_independent_campaign(campaign_id,state="BLOCKED",level_index=index,level_state="BLOCKED",
                        checkpoint={"block_id":block_id,"quota_gate":gate,"ai_diagnosis":diagnosis},event_type="CAMPAIGN_QUOTA_REFUSED")
                    raise RuntimeError("Independent Batch quota/preflight gate refused: "+"; ".join(gate["reasons"]))
                from batch_scheduler import submit_runtime_block
                reservation=max(float(item["stress_envelope_seconds"]) for item in estimate.per_treatment.values())/5.0
                from adaptive_targets import register_target
                register_target(target,registry)
                context={"target_id":target.target_id,"target_n":target.target_n,"chunk_n":5,
                    "manifest_sha256":target.sha256,"account_label":account,
                    "runtime_upper_bound_seconds":estimate.stress_block_seconds,
                    "safety_margin_seconds":authorization["safety_margin_seconds"],
                    "preflight_checked_at":preflight["checked_utc"],"quota_scope":"account_wide_verified"}
                try:
                    ids=submit_runtime_block(runtime_manifest,registry,service,logical_block_id=block_id,
                        account_label=account,instance=active,reservation_seconds_per_job=reservation,
                        safety_margin_seconds=authorization["safety_margin_seconds"],runtime_estimate=estimate,
                        authorize_live=True,preflight_verified=True,
                        quota_budget_seconds=provider_usage["consumed_seconds"]+effective_remaining,
                        local_budget_remaining_seconds=max(0.0,account_cap),
                        adaptive_target_context=context,root=root)
                except AccountUnavailable as exc:
                    if registry.rows(block_id):
                        raise RuntimeError("Existing submission intent requires reconciliation; no account failover") from exc
                    rejected_accounts_by_block.setdefault(block_id,set()).add(account)
                    registry.record_independent_target_event(target.target_id,"BATCH_ACCOUNT_TEMPORARILY_INELIGIBLE",
                        {"block_id":block_id,"account_label":account,"reason":str(exc),"submission_performed":False})
                    continue
                except Exception as exc:
                    # The scheduler persists UNKNOWN on ambiguous submit errors.
                    # This checkpoint is diagnostic only; never retry this block.
                    diagnosis=_ai_event(ai_mode,{"campaign_id":campaign_id,"sequence_no":index+1,
                        "event_class":"BATCH_SUBMISSION_ERROR","local_state":"BLOCKED",
                        "safe_summary":"Batch submit raised "+type(exc).__name__+"; inspect registered job IDs"},explicit=True)
                    registry.checkpoint_independent_campaign(campaign_id,state="BLOCKED",level_index=index,
                        level_state="BLOCKED",checkpoint={"block_id":block_id,"error_type":type(exc).__name__,
                        "ai_diagnosis":diagnosis,"automatic_qpu_retry":False},event_type="CAMPAIGN_SUBMISSION_ERROR")
                    raise
                _campaign_report(directory/f"n{target.target_n}",f"batch_{len(blocks)+1:03d}_submission.json",
                    {"experiment_id":target.experiment_id,"n":target.target_n,"block_id":block_id,
                     "batch_id":registry.rows(block_id)[0].get("batch_id"),"job_ids":ids,"account_label":account,
                     "submitted_utc":utc_now(),"submission_order":[f"M{i}" for i in range(8)],
                     "execution_order_guaranteed":False,"input_hashes":target.input_hashes})
            else:
                service=service_factory(account) if service_factory else make_saved_profile_service(
                    load_saved_account_profiles((account,))[account])
                active=service.active_instance() if callable(getattr(service,"active_instance",None)) else None
            registry.checkpoint_independent_campaign(campaign_id,state="RUNNING",level_index=index,level_state="RUNNING",
                checkpoint={"n":target.target_n,"experiment_id":target.experiment_id,"block_id":block_id},event_type="CAMPAIGN_BATCH_ACTIVE")
            # Always collect existing jobs with their recorded original account and instance.
            recorded_rows=registry.rows(block_id)
            recorded_accounts={row["account_label"] for row in recorded_rows}
            recorded_instances={str((row.get("metadata") or {}).get("instance") or "")
                                for row in recorded_rows}
            if len(recorded_accounts)!=1 or (not archived_recovery and (len(recorded_instances)!=1 or "" in recorded_instances)):
                raise RuntimeError("Recorded batch account/instance missing or inconsistent; collect paused")
            if not archived_recovery:
                collect_account=next(iter(recorded_accounts))
                collect_instance=next(iter(recorded_instances))
                collect_service=(service if collect_account==account and str(active)==collect_instance else
                    (service_factory(collect_account) if service_factory else
                     make_saved_profile_service(load_saved_account_profiles((collect_account,))[collect_account])))
                collect_active=(collect_service.active_instance() if
                    callable(getattr(collect_service,"active_instance",None)) else None)
                if str(collect_active)!=collect_instance:
                    raise RuntimeError("Original submitted instance differs from collection service; no resubmission")
                retry_service=RetryingReadOnlyService(collect_service,attempts=authorization["recovery_limits"]["max_read_attempts"])
            from batch_monitor import monitor_batch
            incomplete_read_attempts=0
            max_read_attempts=authorization["recovery_limits"]["max_read_attempts"]
            while True:
                if stop.exists() or pause.exists(): break
                try:
                    observed=({"block_status":"INCOMPLETE","jobs":[],"verified_archived_source":True}
                              if archived_recovery else monitor_batch(registry,retry_service,block_id,treatments=target.treatment_ids,
                        chunk_n=5,expected_hashes=target.input_hashes,quota_scope="account_wide_verified",root=root))
                except Exception as exc:
                    event={"campaign_id":campaign_id,"sequence_no":index+1,"event_class":"MONITOR_ERROR",
                        "local_state":"PAUSED","safe_summary":type(exc).__name__}
                    diagnosis=_ai_event(ai_mode,event,explicit=True)
                    registry.checkpoint_independent_campaign(campaign_id,state="PAUSED",level_index=index,level_state="PAUSED",
                        checkpoint={"block_id":block_id,"error_type":type(exc).__name__,"ai_diagnosis":diagnosis},event_type="CAMPAIGN_MONITOR_ERROR")
                    raise
                if not archived_recovery:
                    _check_monitor_read(observed)
                if (observed["block_status"]=="INCOMPLETE" and not archived_recovery
                        and not _recovery_sources_terminal(observed)):
                    # A failed result can coexist with queued/running jobs. Do
                    # not plan replacements from uncollected monitor archives.
                    if once:
                        return inspect_independent_campaign(registry,campaign_id,root)|{
                            "recovery_waiting":True,"submission_performed":False}
                    sleeper(poll_seconds)
                    continue
                if observed["block_status"] in ("READY_TO_COLLECT","INCOMPLETE"):
                    job_reports=observed.get("jobs",[])
                    if observed["block_status"]=="INCOMPLETE":
                        latest_review=registry.latest_batch_recovery_review(block_id)
                        if not latest_review or latest_review["recovery_status"]!="INVESTIGATING":
                            registry.record_batch_recovery_review(block_id,batch_execution_status="INCOMPLETE",
                                recovery_status="INVESTIGATING",completeness_policy_version=authorization["completeness_policy"]["version"],
                                valid_fraction_by_treatment=_validated_pub_fractions(registry,target,logical_block_id=block_id),
                                raw_archives_verified=False,provenance_verified=False,
                                details={"reason":"Inspecting provider status, local PUB validation, and immutable archives",
                                    "automatic_qpu_resubmit":False})
                    terminal_statuses={"DONE","COMPLETED","ERROR","CANCELLED","CANCELED","FAILED"}
                    all_terminal=bool(job_reports) and all(str(item.get("status","")).upper() in terminal_statuses for item in job_reports)
                    if observed["block_status"]=="INCOMPLETE" and all_terminal and incomplete_read_attempts<max_read_attempts:
                        incomplete_read_attempts+=1
                        from vqe_adapter import collect_block_for_recovery
                        # Re-read existing job results only. The adapter's raw response archive is content-addressed.
                        try:
                            collect_block_for_recovery(root,block_id,treatments=target.treatment_ids,shots=4096,n=5,account_label=collect_account,ibm_account=collect_account,
                                instance=collect_instance,registry=registry,service_override=retry_service,ai_mode=ai_mode)
                        except Exception as exc:
                            raise RuntimeError('Read-only recollection failed for block ' + block_id) from exc
                        if incomplete_read_attempts<max_read_attempts:
                            sleeper(min(2**(incomplete_read_attempts-1),8))
                        continue
                if observed["block_status"]=="READY_TO_COLLECT":
                    from vqe_adapter import collect_block_for_recovery
                    collect_block_for_recovery(root,block_id,treatments=target.treatment_ids,shots=4096,n=5,account_label=collect_account,ibm_account=collect_account,
                        instance=collect_instance,registry=registry,service_override=retry_service,
                        ai_mode=ai_mode)
                    observed=monitor_batch(registry,retry_service,block_id,treatments=target.treatment_ids,
                        chunk_n=5,expected_hashes=target.input_hashes,quota_scope="account_wide_verified",root=root)
                    _check_monitor_read(observed)
                reports.append({"n":target.target_n,"block_id":block_id,"status":observed["block_status"]})
                if _repaired_block_complete(registry,target,block_id):
                    break
                if observed["block_status"] in ("INCOMPLETE",):
                    from targeted_recovery import (plan_invalid_pub_slots,collect_replacement,
                        repaired_dataset_status)
                    plan=plan_invalid_pub_slots(root,target,registry,final_config=final_config,
                        # Automatic bounded retries are permitted only when the
                        # operator enabled auto recovery and authorized this live
                        # campaign. Confirmed failures remain eligible without a numerical attempt cap.
                        # UNKNOWN/SUBMITTING jobs are never retried.
                        explicit_retry_authorization=bool(
                            authorize_live_campaign and final_config
                            and final_config.get("live_submission_enabled")
                            and final_config.get("auto_recovery_enabled")))
                    # Every n level uses the same recovery policy. Never silently
                    # advance when archive verification blocked a missing PUB.
                    if plan.get("blocked"):
                        registry.record_independent_target_event(target.target_id,
                            "RECOVERY_SOURCE_VERIFICATION_BLOCKED", {
                                "block_id": block_id,
                                "blocked_treatments": sorted({str(x.get("treatment_id")) for x in plan["blocked"]}),
                                "blocked_count": len(plan["blocked"]),
                                "automatic_qpu_retry": False})
                        raise RuntimeError("Recovery source verification blocked; inspect immutable archive provenance before retry")
                    replacement_results=[]
                    replacement_rows=registry.replacement_jobs(target.target_id)
                    planned=[item for item in replacement_rows if item["state"]=="PLANNED"]
                    if planned and authorize_live_campaign and final_config and final_config.get("live_submission_enabled"):
                        from account_config import load_saved_account_profiles,make_saved_profile_service
                        from read_only_preflight import preflight_account,_instance_ref
                        from runtime_estimator import targeted_replacement_estimate as _targeted_replacement_runtime_estimate
                        from experiment_manifest import build_manifest
                        from targeted_recovery import submit_replacement_slot
                        runtime_manifest=build_manifest(root,n=5,replicate_blocks=1,shots=4096,experiment_id=target.experiment_id)
                        for replacement in planned:
                            # A durable PLANNED record is the only state eligible
                            # for a new QPU action. Never resubmit UNKNOWN/DONE.
                            stage="replacement_runtime"
                            try:
                                replacement_estimate=_targeted_replacement_runtime_estimate(
                                    root,runtime_manifest,replacement["treatment_id"],registry=registry,target_id=target.target_id)
                                excluded=set()
                                while True:
                                    stage="account_selection"
                                    selected,replacement_service,fresh=preflight_and_select_account(root,target,
                                        runtime_estimate=replacement_estimate,usage=_campaign_usage(registry,manifest,authorization),
                                        authorization=authorization,workload_type="targeted_replacement",
                                        original_account=replacement.get("source_account_label") or replacement["metadata"].get("source_account_label"),
                                        excluded=excluded,service_factory=service_factory,
                                        attempts=authorization["recovery_limits"]["max_read_attempts"])
                                    replacement_account=selected["account_label"]
                                    if replacement_account!=replacement["account_label"]:
                                        replacement=registry.reassign_planned_replacement_account(
                                            int(replacement["replacement_id"]),replacement_account)
                                    replacement_ref=fresh["configured_instance_ref"]
                                    registry.record_independent_target_event(target.target_id,"REPLACEMENT_ACCOUNT_SELECTED",
                                        {"replacement_id":replacement["replacement_id"],"source_account_label":replacement["source_account_label"],
                                         "replacement_account_label":replacement_account,"account_decisions":selected["account_reports"],
                                         "input_hashes":target.input_hashes,"submission_performed":False})
                                    stage="replacement_submission"
                                    try:
                                        result=submit_replacement_slot(root,target,registry,
                                            int(replacement["replacement_id"]),replacement_service,final_config=final_config,
                                            campaign_authorization=authorization,preflight_report=fresh,
                                            runtime_estimate=replacement_estimate,explicit_authorization=True,
                                            active_instance_ref=replacement_ref,
                                            authorized_instance_refs=authorization["allowed_instance_refs"].get(replacement_account,[]))
                                    except AccountUnavailable:
                                        current=next(r for r in registry.replacement_jobs(target.target_id)
                                                     if r["replacement_id"]==replacement["replacement_id"])
                                        if current["state"]!="PLANNED" or current.get("job_id"):
                                            raise RuntimeError("Replacement submission intent requires reconciliation")
                                        excluded.add(replacement_account)
                                        continue
                                    replacement_results.append(result)
                                    break
                            except Exception as exc:
                                # Keep the error actionable without logging credentials,
                                # instance refs, provider payloads or arbitrary exception text.
                                registry.record_independent_target_event(target.target_id,
                                    "REPLACEMENT_GATE_BLOCKED",{
                                        "replacement_id":int(replacement["replacement_id"]),
                                        "treatment_id":replacement["treatment_id"],
                                        "logical_pub_index":int(replacement["logical_pub_index"]),
                                        "stage":stage,"error_type":type(exc).__name__,
                                        "automatic_retry":False})
                                raise
                    # Read-only result polling/revalidation for previously registered replacements.
                    for replacement in registry.replacement_jobs(target.target_id):
                        if replacement["state"] in ("SUBMITTED","DONE"):
                            try:
                                replacement_account=replacement["account_label"]
                                from account_config import load_saved_account_profiles,make_saved_profile_service
                                replacement_profile=load_saved_account_profiles((replacement_account,))[replacement_account]
                                replacement_service=(service_factory(replacement_account) if service_factory
                                    else make_saved_profile_service(replacement_profile))
                                replacement_results.append(collect_replacement(root,target,registry,
                                    int(replacement["replacement_id"]),replacement_service))
                            except Exception as exc:
                                # A corrupt archive/identity needs operator action;
                                # a network read enters the bounded outer retry loop.
                                raise
                    # Batch-level recovery must not depend on whole-n analysis eligibility.
                    # The original Batch remains INCOMPLETE; only selected logical slots
                    # are promoted after independent archive/provenance verification.
                    block_recovered=_repaired_block_complete(registry,target,block_id)
                    if not block_recovered:
                        pending=[item for item in registry.replacement_jobs(target.target_id)
                            if item.get("logical_block_id")==block_id and item.get("state") in ("PLANNED","SUBMITTED","DONE")]
                        retry_failed=bool(final_config and final_config.get('auto_recovery_enabled') and
                            any(item.get('state')=='FAILED' for item in replacement_results))
                        if pending or retry_failed:
                            if once:
                                return inspect_independent_campaign(registry,campaign_id,root)|{
                                    "reports":reports,"recovery_waiting":True,"submission_performed":False}
                            sleeper(poll_seconds)
                            continue
                        registry.checkpoint_independent_campaign(campaign_id,state="PAUSED",level_index=index,
                            level_state="RECOVERY_REQUIRED",checkpoint={"block_id":block_id,
                                "reason":"Batch evidence or selected logical PUB slots remain unverified",
                                "automatic_qpu_resubmit":False,"analysis_allowed":False},
                            event_type="CAMPAIGN_INCOMPLETE_BLOCK")
                        raise RuntimeError("Batch recovery evidence is incomplete; no progression or new submission occurred")
                    break
                if once: break
                sleeper(poll_seconds)
            if stop.exists() or pause.exists(): continue
            # A Batch is complete and PUB-validated before canonical EIS work begins.
            status=registry.target_status(target.target_id)
            from targeted_recovery import repaired_dataset_status
            repaired_status=repaired_dataset_status(target,registry)
            batch_complete=(all(_repaired_block_complete(registry,target,item["logical_block_id"])
                for item in status["blocks"]))
            if (repaired_status["analysis_eligible"] and batch_complete
                    and len(status["blocks"])==target.blocks_required):
                if automatic_science:_complete_science(root,campaign_id,index,level,target,registry,directory)
                registry.complete_independent_level(campaign_id,index)
                if once:
                    return inspect_independent_campaign(registry,campaign_id)|{"reports":reports,
                        "data_collection_complete":True,"publication_exports_pending":not automatic_science,
                        "state":"RUNNING" if automatic_science else registry.independent_campaign_status(campaign_id)["state"],
                        "submission_performed":bool(reports)}
            elif once:
                return inspect_independent_campaign(registry,campaign_id)|{"reports":reports,"submission_performed":bool(reports)}


def _recovery_sources_terminal(observed: dict[str,Any]) -> bool:
    """Only a fully identified, terminal Batch may enter archive recovery."""
    if observed.get("batch_identity_verified") is not True:
        raise RuntimeError("Batch identity requires reconciliation before recovery")
    jobs=observed.get("jobs",[])
    if len(jobs)!=8 or any(not job.get("job_id") for job in jobs):
        raise RuntimeError("Unknown submission requires reconciliation before recovery")
    if any(job.get("failure_class") in ("SUBMISSION_AMBIGUITY","BATCH_ID_MISMATCH",
            "AUTHENTICATION","BACKEND","QUOTA") for job in jobs):
        raise RuntimeError("Batch preflight or identity verification failed before recovery")
    terminal={"DONE","COMPLETED","ERROR","CANCELLED","CANCELED","FAILED"}
    return all(str(job.get("status","")).upper() in terminal for job in jobs)


def _check_monitor_read(observed):
    failures={job.get('failure_class') for job in observed.get('jobs',[])}
    if (observed.get('batch_identity_verified') is not True or
            failures & {'SUBMISSION_AMBIGUITY','BATCH_ID_MISMATCH','AUTHENTICATION','BACKEND'}):
        raise RuntimeError('Provider identity or access requires reconciliation before progression')
    if 'LOCAL_ARCHIVE_IO' in failures:
        raise RuntimeError('Local archive write/integrity failure; existing job IDs require safe read retry')
    if 'RESOURCE_WAIT' in failures:
        error=OSError('WinError 1450: insufficient resources during read/archive; retry existing IDs')
        error.winerror=1450
        raise error
    if 'NETWORK' in failures:
        raise ConnectionError('Temporary read-only provider failure')


def run_campaign_auto_manager(root,campaign_id: str,registry: JobRegistry, *,
        authorize_live_campaign: bool=False,ai_mode: str="python",explicit_ai: bool=False,
        account_label: str | None=None,resume: bool=False,
        service_factory: Callable[[str],Any] | None=None,sleeper: Callable[[float],None]=time.sleep,
        poll_seconds: int=60,final_config: dict[str,Any] | None=None) -> dict[str,Any]:
    """Keep the campaign process alive across polling cycles and recoverable blocks.

    Each cycle delegates scientific validation, quota gates, collection and
    progression to ``run_independent_campaign``. Exceptions are checkpointed
    without raw messages or secrets; the next cycle re-runs fresh gates against
    durable job IDs. Unknown submissions remain unretryable in the registry.
    """
    if isinstance(poll_seconds,bool) or not isinstance(poll_seconds,int) or not 1<=poll_seconds<=300:
        raise ValueError("poll_seconds must be between 1 and 300")
    from worker_runtime import campaign_worker_lock
    directory=campaign_root(root,campaign_id)
    status=registry.independent_campaign_status(campaign_id)
    if not status.get("exists"):raise KeyError(campaign_id)
    failure_count=0;resume_next=resume;last_diagnostic_key=None
    def log(event,**fields):
        print(json.dumps({'utc':utc_now(),'pid':os.getpid(),'campaign':campaign_id,
                          'event':event,**fields}),flush=True)
    def interrupted():
        current=registry.independent_campaign_status(campaign_id)
        registry.checkpoint_independent_campaign(campaign_id,state='PAUSED',
            level_index=min(int(current.get('current_level_index',0)),5),
            checkpoint={'pause_class':'PAUSED_OPERATOR_ACTION','reason':'Operator interrupt'},
            event_type='AUTO_MANAGER_INTERRUPTED')
        log('PAUSED_OPERATOR_ACTION',reason='operator_interrupt')
        return {'campaign_id':campaign_id,'state':'PAUSED','pause_class':'PAUSED_OPERATOR_ACTION'}
    with campaign_worker_lock(directory):
        if status.get('state')=='STOPPED':
            return {'campaign_id':campaign_id,'state':'STOPPED'}
        while True:
            stop=directory/".campaign.stop";pause=directory/".campaign.pause"
            try:
                if stop.exists() or (pause.exists() and not resume_next):
                    current=registry.independent_campaign_status(campaign_id)
                    state='STOPPED' if stop.exists() else 'PAUSED'
                    registry.checkpoint_independent_campaign(campaign_id,state=state,
                        level_index=min(int(current.get('current_level_index',0)),5),
                        checkpoint={'pause_class':'PAUSED_OPERATOR_ACTION','reason':'Operator marker'},
                        event_type='AUTO_MANAGER_'+state)
                    log(state,reason='operator_marker')
                    return {'campaign_id':campaign_id,'state':state,'pause_class':'PAUSED_OPERATOR_ACTION'}
                requested_resume=resume_next;resume_next=False
                result=run_independent_campaign(root,campaign_id,registry,
                    authorize_live_campaign=authorize_live_campaign,ai_mode=ai_mode,
                    explicit_ai=explicit_ai,account_label=account_label,once=True,
                    resume=requested_resume,service_factory=service_factory,sleeper=sleeper,
                    poll_seconds=poll_seconds,final_config=final_config,_worker_lock_held=True,automatic_science=True)
                resume_next=False;last_diagnostic_key=None
                state=result.get("state")
                log('CYCLE',state=state,recovery_waiting=bool(result.get('recovery_waiting')))
                if state in ("COMPLETE","STOPPED"):return result
                if stop.exists() or pause.exists():continue
                if state=="PAUSED":
                    checkpoint=registry.independent_campaign_status(campaign_id).get("checkpoint") or {}
                    if checkpoint.get("pause_class")!="PAUSED_RETRYABLE":
                        return result|{'pause_class':'PAUSED_OPERATOR_ACTION'}
                    failure_count+=1
                else:failure_count=0
                if state=="BLOCKED_AUTHORIZATION":return result
            except KeyboardInterrupt:
                return interrupted()
            except Exception as exc:
                current=registry.independent_campaign_status(campaign_id)
                index=min(int(current.get("current_level_index",0)),5)
                failure_count+=1
                delay=min(poll_seconds*(2**min(failure_count-1,4)),900)
                category=_auto_manager_error_category(exc)
                if _campaign_has_unknown(registry,current):category='SUBMISSION_AMBIGUITY'
                if category not in ("TRANSIENT_READ","PROVIDER_WAIT","SCIENTIFIC_RETRY","RESOURCE_WAIT"):
                    checkpoint={"failure_category":category,"error_type":type(exc).__name__,
                        "pause_class":"PAUSED_OPERATOR_ACTION",
                        "automatic_new_qpu_submission":False,
                        "reason":str(exc),
                        "account_decisions":getattr(exc,"decisions",{}),
                        "previous_checkpoint":{k:v for k,v in (current.get('checkpoint') or {}).items()
                                               if k!='previous_checkpoint'},
                        "fresh_selection_on_resume":True}
                    registry.checkpoint_independent_campaign(campaign_id,state="PAUSED",level_index=index,
                        checkpoint=checkpoint,event_type="AUTO_MANAGER_PAUSED_SAFETY_GATE")
                    log('PAUSED_OPERATOR_ACTION',category=category,error_type=type(exc).__name__)
                    if ai_mode!="python":
                        diagnosis=_ai_event(ai_mode,{"campaign_id":campaign_id,
                            "event_class":"AUTO_MANAGER_SAFETY_GATE","local_state":"PAUSED",
                            "error_type":type(exc).__name__,"failure_category":category,
                            "safe_summary":"Campaign paused at a quota/budget or ambiguous-submission gate; no automatic retry occurred"},explicit=True)
                        registry.checkpoint_independent_campaign(campaign_id,state="PAUSED",level_index=index,
                            checkpoint=checkpoint|{"ai_diagnosis":diagnosis},event_type="AUTO_MANAGER_SAFETY_DIAGNOSTIC")
                    return inspect_independent_campaign(registry,campaign_id,root)|{
                        "state":"PAUSED","pause_class":"PAUSED_OPERATOR_ACTION",
                        "resume_required":True,"submission_performed":False}
                checkpoint={"failure_category":category,"error_type":type(exc).__name__,
                    "pause_class":"PAUSED_RETRYABLE","reason":str(exc),
                    "retry_cycle":failure_count,"retry_after_seconds":delay,
                    "automatic_new_qpu_submission":False,
                    "note":"A later cycle must pass fresh preflight, quota, scope, runtime and duplicate gates."}
                registry.checkpoint_independent_campaign(campaign_id,state="PAUSED",level_index=index,
                    checkpoint=checkpoint,event_type="AUTO_MANAGER_BLOCKED_RETRYABLE")
                log('PAUSED_RETRYABLE',category=category,retry_cycle=failure_count,retry_after_seconds=delay)
                diagnostic_key=(type(exc).__name__,category)
                if ai_mode!="python" and diagnostic_key!=last_diagnostic_key:
                    diagnosis=_ai_event(ai_mode,{"campaign_id":campaign_id,"event_class":"AUTO_MANAGER_BLOCKED",
                        "local_state":"BLOCKED","error_type":type(exc).__name__,
                        "failure_category":category,"safe_summary":"Automatic campaign cycle was blocked; no automatic retry submission is permitted"},explicit=True)
                    registry.checkpoint_independent_campaign(campaign_id,state="BLOCKED",level_index=index,
                        checkpoint=checkpoint|{"ai_diagnosis":diagnosis},event_type="AUTO_MANAGER_DIAGNOSTIC")
                    last_diagnostic_key=diagnostic_key
                if stop.exists() or pause.exists():continue
                try:sleeper(delay)
                except KeyboardInterrupt:return interrupted()
                continue
            if stop.exists() or pause.exists():continue
            delay=min(poll_seconds*2**min(max(failure_count-1,0),4),900)
            try:sleeper(delay)
            except KeyboardInterrupt:return interrupted()


def _campaign_has_unknown(registry,current):
    targets={level['target_id'] for level in current.get('levels',[])}
    if not targets:return False
    if any(row.get('target_id') in targets and row['submission_state'] in ('UNKNOWN','SUBMITTING')
           for row in registry.rows()):return True
    return any(row['state'] in ('UNKNOWN','SUBMITTING','RESERVED')
               for target in targets for row in registry.replacement_jobs(target))


def _auto_manager_error_category(exc: Exception) -> str:
    from worker_runtime import transient_read_error,resource_exhaustion_error
    message=str(exc).lower()
    if isinstance(exc,ScientificCompletionPending):return "SCIENTIFIC_RETRY"
    if isinstance(exc,AccountSelectionUnavailable):
        return 'PROVIDER_WAIT' if exc.retryable else 'QUOTA_OR_BUDGET'
    if any(term in message for term in ('ambiguous','unknown submission','reconcile','contradict','conflict','mismatch')):
        return 'SUBMISSION_AMBIGUITY'
    if resource_exhaustion_error(exc):return 'RESOURCE_WAIT'
    if transient_read_error(exc):return 'TRANSIENT_READ'
    if any(term in message for term in ("quota","runtime budget","safety margin","budget cap",
            "replacement budget","runtime evidence",
            "runtime upper bound","runtime bound")):return "QUOTA_OR_BUDGET"
    if any(term in message for term in ("authentication","credential","profile")):return "AUTHENTICATION"
    if any(term in message for term in ("backend","instance","preflight")):return "PREFLIGHT_OR_BACKEND"
    if any(term in message for term in ("pub","archive","validation","incomplete")):return "RECOVERY_OR_VALIDATION"
    if any(term in message for term in ("analysis","figure","export")):return "SCIENTIFIC_EXPORT"
    return "TRANSIENT_OR_UNCLASSIFIED"


def _account_for_level(target, authorization: dict[str,Any], blocks: list[dict[str,Any]]) -> str:
    """Resolve a provisional account only; fresh quota selects new Batch account.

    Existing Batch account identity comes from registered jobs, never this fallback.
    """
    account=authorization["allowed_accounts"][0] if authorization["allowed_accounts"] else None
    if not account: raise RuntimeError("No configured account for this n level")
    return account


def _campaign_usage(registry: JobRegistry,manifest: dict[str,Any],authorization: dict[str,Any] | None=None) -> dict[str,Any]:
    ids=[level["target_id"] for level in manifest["levels"]]
    placeholders=",".join("?" for _ in ids)
    with registry._connection() as db:
        rows=db.execute(f"SELECT account_label,submission_state,qpu_usage_seconds,quota_reserved_seconds,metadata_json,job_id,logical_block_id,treatment_id,attempt,validated_pub_count,batch_id FROM jobs WHERE target_id IN ({placeholders})",ids).fetchall()
        replacement_rows=db.execute(f"SELECT account_label,state,qpu_usage_seconds,quota_reserved_seconds,job_id,batch_id FROM pub_replacement_jobs WHERE target_id IN ({placeholders})",ids).fetchall()
    authorized=authorization or manifest["operator_authorization"]
    by_account={label:{"consumed_seconds":0.0,"reserved_seconds":0.0} for label in authorized["allowed_accounts"]}
    total_used=total_reserved=0.0;submitted_blocks=0
    treatment_recovery_groups=set()
    for row in rows:
        meta=json.loads(dict(row).get('metadata_json') or '{}')
        if meta.get('m7_treatment_recovery') or meta.get('combined_treatment_recovery'):
            # Combined reservations are one Batch even before IBM returns its ID.
            treatment_recovery_groups.add((row['account_label'],row['logical_block_id']))
    treatment_recovery_batches=len(treatment_recovery_groups)
    for row in rows:
        info=by_account.setdefault(row["account_label"],{"consumed_seconds":0.0,"reserved_seconds":0.0})
        if row["qpu_usage_seconds"] is not None:
            info["consumed_seconds"]+=float(row["qpu_usage_seconds"]);total_used+=float(row["qpu_usage_seconds"])
        if row["submission_state"] in ("RESERVED","SUBMITTING","SUBMITTED","UNKNOWN"):
            outstanding=max(0.0,float(row["quota_reserved_seconds"] or 0)-float(row["qpu_usage_seconds"] or 0))
            info["reserved_seconds"]+=outstanding;total_reserved+=outstanding
    replacement_batches=0
    for row in replacement_rows:
        info=by_account.get(row["account_label"])
        if info is None:
            info=by_account.setdefault(row["account_label"],{"consumed_seconds":0.0,"reserved_seconds":0.0})
        if row["qpu_usage_seconds"] is not None:
            info["consumed_seconds"]+=float(row["qpu_usage_seconds"]);total_used+=float(row["qpu_usage_seconds"])
        if row["state"] in ("SUBMITTING","UNKNOWN","SUBMITTED","DONE"):
            outstanding=max(0.0,float(row["quota_reserved_seconds"] or 0)-float(row["qpu_usage_seconds"] or 0))
            info["reserved_seconds"]+=outstanding;total_reserved+=outstanding
        if row["job_id"] or row["state"] in ("SUBMITTING","UNKNOWN","SUBMITTED","DONE","VALIDATED"):
            replacement_batches+=1
    with registry._connection() as db:
        submitted_blocks=db.execute(f"SELECT COUNT(*) FROM experiment_blocks WHERE target_id IN ({placeholders}) AND status!='CONFIRMED_NOT_SUBMITTED'",ids).fetchone()[0]
    with registry._connection() as db:
        historical_rows=[dict(row) for row in db.execute("SELECT account_label,consumed_seconds,quota_scope,source,checked_at,logical_block_id FROM account_usage_snapshots ORDER BY id").fetchall()]
    # Execution counters are reporting only. Original attempts and recovery jobs
    # remain separate even when a recovery fills an original logical PUB slot.
    first={};recovery_jobs=[]
    for raw in rows:
        row=dict(raw);meta=json.loads(row.get("metadata_json") or "{}")
        if meta.get("m7_treatment_recovery") or meta.get("combined_treatment_recovery"):
            recovery_jobs.append(row);continue
        if not row.get("logical_block_id") or not row.get("treatment_id"):continue
        key=(row["logical_block_id"],row["treatment_id"])
        if key not in first or row.get("attempt",1)<first[key].get("attempt",1):first[key]=row
    groups={}
    for (block,treatment),row in first.items():groups.setdefault(block,[]).append(row)
    originals_submitted=sum(any(row.get("job_id") for row in group) for group in groups.values())
    originals_completed=sum(len(group)==8 and all(row["submission_state"]=="COMPLETED" for row in group) for group in groups.values())
    recovery_jobs += [dict(row) for row in replacement_rows]
    submitted_recovery=[row for row in recovery_jobs if row.get("job_id")]
    recovery_batch_ids={(row["account_label"],row.get("batch_id") or row["job_id"]) for row in submitted_recovery}
    recorded_validated=sum(int(dict(row).get("validated_pub_count") or 0) for row in rows)+sum(row["state"]=="VALIDATED" for row in replacement_rows)
    from quota import provider_reservations
    return {"per_account":by_account,"provider_reservations":provider_reservations(registry),
        "historical_usage_records":historical_rows,
        "campaign_consumed_seconds":total_used,
        "campaign_reserved_seconds":total_reserved,"original_batches_reserved_or_attempted":int(submitted_blocks),"replacement_batches_reserved_or_attempted":replacement_batches,"treatment_recovery_batches_reserved_or_attempted":int(treatment_recovery_batches),
        "planned_original_batches":manifest.get("total_batches_required"),
        "submitted_original_batches":originals_submitted,"completed_original_batches":originals_completed,
        "submitted_recovery_batches":len(recovery_batch_ids),"submitted_recovery_jobs":len(submitted_recovery),
        "recorded_validated_pubs":recorded_validated,"batch_counts_monitoring_only":True,
        "recovery_batches_reserved_or_attempted":replacement_batches+int(treatment_recovery_batches),
        "batches_reserved_or_attempted":int(submitted_blocks)+replacement_batches+int(treatment_recovery_batches)}
