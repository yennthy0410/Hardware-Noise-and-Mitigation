"""Thin adapter to the existing VQE pilot; scientific methods remain canonical there."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

import json
import math
import re
from pathlib import Path
import sys

_VQE_CODE = Path(__file__).resolve().parents[2] / "01_vqe"
if str(_VQE_CODE) not in sys.path:
    sys.path.insert(0, str(_VQE_CODE))

from repository_paths import repository_root, pilot_script


class ArchivedInvalidPubs(ValueError):
    """Invalid slots already durably archived and recorded in the PUB ledger."""


def collect_block_for_recovery(root, logical_block_id, *, treatments, **kwargs):
    """Collect every treatment; only known archived invalid slots enter recovery.

    Transport, disk, identity and provenance failures still stop this pass.
    """
    for treatment in treatments:
        try:
            collect_block(root,logical_block_id,treatments=(treatment,),**kwargs)
        except Exception as exc:
            cause=exc;seen=set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause,ArchivedInvalidPubs):break
                from worker_runtime import TerminalResultUnavailable
                if isinstance(cause,TerminalResultUnavailable):
                    _archive_terminal_missing(root,logical_block_id,treatment,cause,kwargs)
                    break
                cause=cause.__cause__ or cause.__context__
            else:raise


def _archive_terminal_missing(root, block, treatment, failure, kwargs):
    """Archive terminal retrieval evidence without inventing a provider result."""
    import hashlib, os
    registry=kwargs.get('registry')
    if registry is None:raise RuntimeError('Terminal recovery requires durable registry')
    rows=[row for row in registry.rows(block) if row['treatment_id']==treatment]
    if not rows:raise RuntimeError('Terminal recovery requires registered identity')
    row=rows[-1]
    if row['submission_state'] not in ('COMPLETED','FAILED','CANCELLED') or not row.get('job_id'):
        raise RuntimeError('Terminal recovery requires reconciled terminal job identity')
    metadata=row.get('metadata',{})
    if metadata.get('per_pub_source_path') or metadata.get('raw_archive_path'):
        return  # Existing results and validation always win over later retrieval failure.
    payload={'archive_format':'mxene_terminal_retrieval_evidence_v1','job_id':row['job_id'],
        'treatment_id':treatment,'backend':row['backend'],'source_run':'run_02',
        'input_hashes':row['input_hashes'],'shots_per_pub':kwargs['shots'],'records':[],
        'provider_status':failure.status,'retrieval_evidence':failure.evidence}
    folder=Path(root)/'02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/03_IBM_execution_records/ai_assistant/terminal_evidence'
    folder.mkdir(parents=True,exist_ok=True)
    blob=json.dumps(payload,sort_keys=True,allow_nan=False).encode()
    digest=hashlib.sha256(blob).hexdigest();path=folder/(digest+'.json')
    from repository_paths import atomic_archive
    atomic_archive(path,(blob,),digest)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise RuntimeError('Terminal evidence hash mismatch')
    registry.transition(row['id'],row['submission_state'],metadata={
        'per_pub_source_path':str(path),'per_pub_source_sha256':digest},event_type='TERMINAL_RESULT_UNAVAILABLE',
        details={'provider_status':failure.status,'read_attempts':len(failure.evidence)})
    registry.record_pub_validation_records(row['id'],registry.independent_experiment_id(row['target_id']),digest,[
        {'pub_index':index,'expectation_valid':False,'uncertainty_valid':None,'uncertainty_state':'MISSING',
         'error_codes':['TERMINAL_RESULT_UNAVAILABLE'],'expectation_values':None,'standard_deviations':None}
        for index in range(1,int(row.get('chunk_n') or kwargs.get('n',5))+1)])


def load_pilot(root: str | Path | None = None):
    """Load the repository's existing mitigation pilot without writing bytecode."""
    import importlib.util
    import sys
    repo = repository_root(root)
    script = pilot_script(repo)
    sys.dont_write_bytecode = True
    code_dir = str(script.parent)
    if code_dir not in sys.path:
        sys.path.insert(0, code_dir)
    existing = sys.modules.get("mitigation_pilot")
    if existing is not None:
        if Path(existing.__file__).resolve() != script.resolve():
            raise ImportError("Refusing shadowed mitigation_pilot module")
        return existing
    spec = importlib.util.spec_from_file_location("mitigation_pilot", script)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load canonical VQE pilot: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def verify_frozen_inputs(root: str | Path | None = None):
    """Delegate circuit/theta/observable validation and ideal calculation to the pilot."""
    from repository_paths import frozen_source
    repo = repository_root(root)
    pilot = load_pilot(repo)
    source = frozen_source(repo)
    return pilot.verify_source(source, repo)


def treatment_options(treatment_id: str, shots: int, root: str | Path | None = None):
    pilot = load_pilot(root)
    if treatment_id not in {item[0] for item in pilot.TREATMENTS}:
        raise ValueError(f"Unknown mitigation treatment: {treatment_id}")
    return pilot.configuration(treatment_id, shots)


def block_round_name(logical_block_id: str) -> str:
    if not logical_block_id or any(c in logical_block_id for c in "/\\:") or logical_block_id in (".", ".."):
        raise ValueError("logical_block_id must be a safe single path component")
    return "replicate_block_" + logical_block_id


def record_directory(root: str | Path | None, logical_block_id: str) -> Path:
    repo=repository_root(root)
    from repository_paths import MITIGATION_REL, contained
    return contained(repo/MITIGATION_REL/"output","03_IBM_execution_records",
                     f"{block_round_name(logical_block_id)}_IBM_job_infor_recorded")


def _args(root, logical_block_id, *, shots, n=5, account_label=None, ibm_account=None, instance=None, **extra):
    from types import SimpleNamespace
    return SimpleNamespace(project=str(repository_root(root)),source=None,round=block_round_name(logical_block_id),
        repeats=n,shots=shots,account_label=account_label,ibm_account=ibm_account,instance=instance,
        data=None,classical_sse=None,random_sse=None,bootstrap_draws=3000,bootstrap_seed=1013,
        treatment=None,**extra)


def prepare_block(root, logical_block_id: str, *, shots: int = 4096, n: int = 5):
    """Reuse the VQE prepare engine without archiving/copying into the immutable lock."""
    return load_pilot(root).prepare(_args(root,logical_block_id,shots=shots,n=n),archive_evidence=False)


def validate_rows(rows, n: int, label: str = "Estimator PUB results", root=None):
    """Validate exactly n returned PUB rows through the canonical pilot validator."""
    if isinstance(n,bool) or not isinstance(n,int) or n < 1:
        raise ValueError("n (PUBs per treatment) must be a positive integer")
    return load_pilot(root)._validated_rows(rows,n,label)


def validate_runtime_result(result, expected_n: int, label: str = "Runtime Estimator result") -> dict:
    """Validate PUB expectations and preserve available uncertainty metadata.

    Missing standard deviations are reported as missing. Supplied uncertainty
    values must have seven finite, nonnegative entries; they are never filled in.
    """
    if isinstance(expected_n,bool) or not isinstance(expected_n,int) or expected_n<1:
        raise ValueError("expected_n must be a positive integer")
    captured=capture_runtime_result(result,label)
    if captured["pub_count"]!=expected_n:
        raise ValueError(f"{label}: expected {expected_n} PUBs, got {captured['pub_count']}")
    if captured["invalid_expectation_pubs"]:
        first=captured["expectation_errors"][0]
        raise ValueError(f"{label}: PUB {first['pub_index']} {first['message']}")
    if captured["invalid_uncertainty_pubs"]:
        raise ValueError(f"{label}: invalid numeric uncertainty values in PUB {captured['invalid_uncertainty_pubs'][0]}")
    if captured["uncertainty_status"]=="MISSING":
        raise ValueError(f"{label}: unavailable uncertainty")
    return {**captured,"validation":"PASS"}


def runtime_numerical_failure(metadata):
    """Recognize explicit numerical failure evidence, never infer from network text."""
    if isinstance(metadata,dict):
        for key,value in metadata.items():
            name=str(key).lower()
            if name in ("zne_status","extrapolation_status","numerical_status") and str(value).upper() in ("FAILED","ERROR","INVALID"):
                return True
            if name in ("error","error_message","failure_reason") and isinstance(value,str):
                message=value.lower()
                if any(word in message for word in ("zne","extrapolat","numerical")) and any(word in message for word in ("fail","error","invalid")):
                    return True
            if runtime_numerical_failure(value):return True
    elif isinstance(metadata,list):
        return any(runtime_numerical_failure(value) for value in metadata)
    return False


def capture_runtime_result(result, label: str = "Runtime Estimator result") -> dict:
    """Capture available PUB data without hiding partial/corrupt values.

    This tolerant form is for immutable calibration archiving before validation.
    It never substitutes missing data or uncertainty values.
    """
    try: pubs=list(result)
    except Exception as exc: raise ValueError(f"{label}: result is not an iterable PUB collection") from exc
    records=[];metadata=[];invalid_expectation=[];invalid_uncertainty=[];expectation_errors=[];uncertainty_errors=[]
    def plain(value):
        if hasattr(value,"tolist"): value=value.tolist()
        if isinstance(value,tuple): return [plain(x) for x in value]
        if isinstance(value,list): return [plain(x) for x in value]
        if isinstance(value,float) and not math.isfinite(value): return str(value)
        if isinstance(value,(str,int,float,bool)) or value is None: return value
        return str(value)
    def flat_numeric(value):
        if hasattr(value,"reshape"):
            try: value=value.reshape(-1).tolist()
            except Exception: value=plain(value)
        elif hasattr(value,"tolist"): value=value.tolist()
        while isinstance(value,(list,tuple)) and len(value)==1 and isinstance(value[0],(list,tuple)):
            value=value[0]
        return list(value) if isinstance(value,(list,tuple)) else None
    for index,pub in enumerate(pubs,1):
        data=getattr(pub,"data",None)
        raw_evs=getattr(data,"evs",None)
        evs=flat_numeric(raw_evs)
        try:
            valid_evs=evs is not None and len(evs)==7 and all(math.isfinite(float(x)) for x in evs)
        except (TypeError,ValueError,OverflowError): valid_evs=False
        if valid_evs: clean_evs=[float(x) for x in evs]
        else:
            clean_evs=plain(raw_evs)
            invalid_expectation.append(index)
            if raw_evs is None: message="has no expectation values"
            elif evs is None or len(evs)!=7: message="must contain exactly seven expectation values"
            else: message="contains a non-finite or non-numeric expectation"
            expectation_errors.append({"pub_index":index,"message":message})
        raw_stds=getattr(data,"stds",None)
        if raw_stds is None:
            clean_stds=None;uncertainty="MISSING"
        else:
            stds=flat_numeric(raw_stds)
            try:
                valid_stds=stds is not None and len(stds)==7 and all(math.isfinite(float(x)) and float(x)>=0 for x in stds)
            except (TypeError,ValueError,OverflowError): valid_stds=False
            if valid_stds:
                clean_stds=[float(x) for x in stds];uncertainty="PRESENT"
            else:
                clean_stds=plain(raw_stds);uncertainty="INVALID";invalid_uncertainty.append(index)
                if stds is None or len(stds)!=7: message="must contain exactly seven standard deviations"
                else: message="contains a non-finite, non-numeric, or negative standard deviation"
                uncertainty_errors.append({"pub_index":index,"message":message})
        pub_meta=_redact_runtime_metadata(getattr(pub,"metadata",{}));metadata.append(pub_meta)
        if runtime_numerical_failure(pub_meta):
            valid_evs=False
            if index not in invalid_expectation:invalid_expectation.append(index)
            expectation_errors.append({"pub_index":index,"message":"Runtime numerical or ZNE extrapolation failure"})
        # Keep every Runtime DataBin field, including ZNE/noise-factor arrays
        # that may be exposed by a particular Runtime result schema.  This is
        # provenance only; the scientific path continues to consume evs/stds.
        raw_fields={}
        if data is not None:
            keys=[]
            if callable(getattr(data,"keys",None)):
                try: keys=list(data.keys())
                except Exception: keys=[]
            if not keys:
                try: keys=[key for key in vars(data) if not str(key).startswith("_")]
                except Exception: keys=[]
            for key in keys:
                try: raw_fields[str(key)]=_redact_runtime_metadata(plain(getattr(data,str(key))))
                except Exception as exc:
                    raw_fields[str(key)]={"capture_error":type(exc).__name__}
        records.append({"pub_index":index,"expectation_values":clean_evs,
                        "expectation_validation":"PASS" if valid_evs else "INVALID",
                        "standard_deviations":clean_stds,"uncertainty_status":uncertainty,
                        "metadata":pub_meta,"data_fields":raw_fields})
    statuses=[record["uncertainty_status"] for record in records]
    uncertainty_status=("INVALID" if "INVALID" in statuses else
        ("MISSING" if "MISSING" in statuses else "PRESENT"))
    return {"pub_count":len(pubs),"expectation_count_per_pub":7,
        "pub_metadata":metadata,"uncertainty_status":uncertainty_status,
        "records":records,"invalid_expectation_pubs":invalid_expectation,"expectation_errors":expectation_errors,
        "invalid_uncertainty_pubs":invalid_uncertainty,"uncertainty_errors":uncertainty_errors,
        "expectation_failure_rate":len(invalid_expectation)/len(pubs) if pubs else 0.0,
        "uncertainty_failure_rate":len(invalid_uncertainty)/len(pubs) if pubs else 0.0,
        "invalid_pub_failure_rate":len(set(invalid_expectation)|set(invalid_uncertainty))/len(pubs) if pubs else 0.0}


def _redact_runtime_metadata(value, key: str = ""):
    """Serialize available PUB metadata while removing credential/CRN fields."""
    sensitive=("api_key","apikey","secret","token","credential","crn")
    if any(item in key.lower() for item in sensitive): return "[REDACTED]"
    if isinstance(value,dict): return {str(name):_redact_runtime_metadata(item,str(name)) for name,item in value.items()}
    if isinstance(value,(list,tuple)): return [_redact_runtime_metadata(item,key) for item in value]
    if isinstance(value,str): return re.sub(r"crn:v1:[A-Za-z0-9:._/-]+","[REDACTED_CRN]",value,flags=re.IGNORECASE)
    try: return json.loads(json.dumps(value,default=str))
    except Exception: return str(value)


def serialize_runtime_result_archive(result) -> bytes:
    """Serialize the retrieved Runtime result before scientific validation.

    Qiskit's RuntimeEncoder preserves native primitive result objects. Offline
    fakes and alternate schemas use a structural fallback that archives every
    PUB DataBin field and its metadata without validating or changing values.
    """
    module=type(result).__module__
    if module.startswith(("qiskit", "qiskit_ibm_runtime")):
        try:
            from low_memory_runtime import remote_runtime
            remote_runtime()
            from qiskit_ibm_runtime import RuntimeEncoder
            return json.dumps(result,cls=RuntimeEncoder,ensure_ascii=False,sort_keys=True,
                              indent=2,default=str).encode("utf-8")
        except Exception:
            pass
    def plain(value):
        if hasattr(value,"tolist"):
            try: value=value.tolist()
            except Exception: pass
        if isinstance(value,dict): return {str(k):plain(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)): return [plain(v) for v in value]
        if isinstance(value,float) and not math.isfinite(value): return str(value)
        if isinstance(value,(str,int,float,bool)) or value is None: return value
        return str(value)
    pubs=[]
    for index,pub in enumerate(list(result),1):
        data=getattr(pub,"data",None);fields={}
        keys=[]
        if callable(getattr(data,"keys",None)):
            try: keys=list(data.keys())
            except Exception: pass
        if not keys:
            try: keys=[key for key in vars(data) if not str(key).startswith("_")]
            except Exception: pass
        for key in keys:
            try: fields[str(key)]=plain(getattr(data,str(key)))
            except Exception as exc: fields[str(key)]={"capture_error":type(exc).__name__}
        pubs.append({"pub_index":index,"data_fields":fields,
                     "metadata":plain(getattr(pub,"metadata",{}))})
    payload={"archive_format":"runtime_pub_data_fields_v1","pubs":pubs,
             "result_metadata":plain(getattr(result,"metadata",{}))}
    return json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2,default=str).encode("utf-8")


def collect_block(root, logical_block_id: str, *, shots: int, account_label: str,
                  ibm_account: str, n: int = 5, instance: str | None = None, registry=None,
                  service_override=None, ai_mode: str = "python", treatments=None,
                  job_row_id=None, archive_block_id=None):
    pilot=load_pilot(root)
    args=_args(root,logical_block_id,shots=shots,n=n,account_label=account_label,ibm_account=ibm_account,instance=instance)
    if archive_block_id is not None:args.round=block_round_name(archive_block_id)
    before_rows=registry.rows(logical_block_id) if registry is not None else []
    if job_row_id is not None:
        before_rows=[row for row in before_rows if row['id']==job_row_id]
        if len(before_rows)!=1:raise RuntimeError('Collection requires exactly one registered job')
    if treatments is not None:
        before_rows=list({row["treatment_id"]:row for row in before_rows if row["treatment_id"] in treatments}.values())
    rows_by_treatment={row["treatment_id"]:row for row in before_rows}
    def observe(treatment,job_id,runtime_result,raw_path,source_hash,job):
        if registry is None:return
        row=rows_by_treatment.get(treatment)
        if row is None or not row.get("target_id"):return
        if row.get('job_id')!=job_id:
            raise RuntimeError('Collected provider job differs from the registered attempt')
        captured=capture_runtime_result(runtime_result,treatment)
        # Persist a derived, non-destructive structured view immediately after
        # collection. The original IBM archive and its registered digest stay immutable.
        import hashlib as _hashlib
        import os as _os
        import uuid as _uuid
        source_path=Path(raw_path)
        if not source_path.is_file() or _hashlib.sha256(source_path.read_bytes()).hexdigest()!=source_hash:
            raise RuntimeError("Original IBM archive does not match registered SHA-256")
        sidecar=source_path.with_name(source_path.stem+".structured.json")
        structured={"archive_format":"mxene_structured_pubs_v2",
            "source_raw_sha256":source_hash,"job_id":job_id,
            "treatment_id":treatment,"pub_count":captured["pub_count"],
            "records":captured["records"]}
        if sidecar.exists():
            existing=json.loads(sidecar.read_text(encoding="utf-8-sig"))
            if (not isinstance(existing,dict) or
                existing.get("archive_format")!="mxene_structured_pubs_v2" or
                existing.get("source_raw_sha256")!=source_hash or
                existing.get("job_id")!=job_id or
                existing.get("treatment_id")!=treatment or
                existing.get("records")!=structured["records"]):
                raise RuntimeError("Existing structured sidecar conflicts with immutable IBM source")
        else:
            from repository_paths import atomic_archive
            encoded=json.dumps(structured,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode('utf-8')
            atomic_archive(sidecar,(encoded,),_hashlib.sha256(encoded).hexdigest())
        expected=int(row.get("chunk_n") or n);items=[]
        for index,record in enumerate(captured["records"],1):
            errors=[]
            if record["expectation_validation"]!="PASS":
                err=next((x for x in captured["expectation_errors"] if x["pub_index"]==index),{})
                errors.append("EXPECTATION_SHAPE_INVALID" if "exactly seven" in err.get("message","") else "EXPECTATION_NONFINITE_OR_NONNUMERIC")
            if record["uncertainty_status"]=="INVALID":errors.append("UNCERTAINTY_NONFINITE_OR_INVALID")
            if record["uncertainty_status"]=="MISSING":errors.append("UNCERTAINTY_MISSING")
            if index>expected:errors.append("UNEXPECTED_PUB_RESULT")
            items.append({"pub_index":index,"expectation_valid":record["expectation_validation"]=="PASS",
                "uncertainty_valid":True if record["uncertainty_status"]=="PRESENT" else
                    (False if record["uncertainty_status"]=="INVALID" else None),
                "uncertainty_state":record["uncertainty_status"],"error_codes":errors,
                "expectation_values":record["expectation_values"],
                "standard_deviations":record["standard_deviations"],"metadata":record.get("metadata",{})})
        if captured["pub_count"]<expected:
            for index in range(captured["pub_count"]+1,expected+1):
                items.append({"pub_index":index,"expectation_valid":False,"uncertainty_valid":None,
                    "uncertainty_state":"MISSING","error_codes":["MISSING_PUB_RESULT"],
                    "expectation_values":None,"standard_deviations":None,"metadata":{}})
        experiment_id=registry.independent_experiment_id(row["target_id"])
        registry.record_pub_validation_records(row["id"],experiment_id,source_hash,items)
        metrics=job.metrics() if callable(getattr(job,"metrics",None)) else {}
        from quota import qpu_seconds_from_metrics
        measured=qpu_seconds_from_metrics(metrics)
        registry.transition(row["id"],row["submission_state"] if row["submission_state"] in ("FAILED","CANCELLED") else "COMPLETED",qpu_usage_seconds=measured,
            metadata={"per_pub_source_path":str(raw_path),"per_pub_source_sha256":source_hash,
                "provider_metrics_present":bool(metrics)},event_type="PUB_SOURCE_ARCHIVED_FOR_VALIDATION",
            details={"pub_count":captured["pub_count"],"source_sha256":source_hash})
        valid_count=sum(bool(item["expectation_valid"]) and item["uncertainty_valid"] is True
            and item["pub_index"]<=expected for item in items)
        exact_count=captured["pub_count"]==expected
        all_valid=exact_count and valid_count==expected and not captured["invalid_expectation_pubs"] and not captured["invalid_uncertainty_pubs"]
        state=("COMPLETE_VALIDATED" if all_valid else
            ("PARTIAL_VALIDATED" if valid_count>0 else "FAILED_VALIDATION"))
        invalid_count=max(0,expected-min(valid_count,expected))
        if not exact_count:invalid_count=max(1,invalid_count)
        registry.set_treatment_validation_state(row["id"],state=state,invalid_pub_count=invalid_count)
        actual_count=min(expected,captured["pub_count"])
        completed=max(int(row.get("completed_pub_count") or 0),actual_count)
        collected=max(int(row.get("collected_pub_count") or 0),actual_count)
        validated=max(int(row.get("validated_pub_count") or 0),min(expected,valid_count))
        registry.update_pub_progress(row["id"],completed=completed,collected=collected,validated=validated,
            event_type="ADAPTIVE_PUBS_COLLECTED_AND_VALIDATED" if all_valid else "ADAPTIVE_PUB_COLLECTION_INCOMPLETE_OR_INVALID")
        if not all_valid:
            codes=sorted({code for item in items for code in item["error_codes"]})
            diagnostic={"target_id":row["target_id"],"experiment_id":experiment_id,
                "block_id":logical_block_id,"batch_id":row.get("batch_id"),
                "treatment_id":treatment,"invalid_pub_indices":[item["pub_index"] for item in items if item["error_codes"]],
                "error_codes":codes,"source_sha256":source_hash,"automatic_qpu_resubmit":False}
            registry.record_independent_target_event(row["target_id"],"PUB_VALIDATION_FAILURE",diagnostic)
            from independent_n_campaign import _ai_event
            campaign_label,_level_index=registry.independent_campaign_context(row["target_id"])
            safe_event={"campaign_id":campaign_label or experiment_id,"sequence_no":_level_index,
                "event_class":"NONFINITE_OR_INCOMPLETE_PUB",
                "treatment_id":treatment,"local_state":"RECOVERY_REQUIRED",
                "safe_summary":f"{treatment} contains invalid or missing PUB evidence ({', '.join(codes) or 'PUB_COUNT_MISMATCH'})."}
            diagnosis=_ai_event(ai_mode,safe_event,explicit=True)
            ai_details={"experiment_id":experiment_id,"treatment_id":treatment,
                "source_sha256":source_hash,"event_class":"NONFINITE_OR_INCOMPLETE_PUB",
                "available":bool(diagnosis and diagnosis.get("available")),
                "diagnosis":diagnosis.get("diagnosis") if diagnosis else None,
                "reason":diagnosis.get("reason") if diagnosis else "AI provider not invoked in python mode",
                "ai_actions_executed":False,"automatic_qpu_resubmit":False}
            registry.record_independent_target_event(row["target_id"],"AI_DIAGNOSTIC",ai_details)
            raise ArchivedInvalidPubs(f"{treatment}: invalid PUB indices {diagnostic['invalid_pub_indices']}; errors {codes}; original result archived at {raw_path}")
    result=pilot.collect(args,assistant_safe=True,service_override=service_override,
        pub_observer=observe if registry is not None else None, selected_treatments=treatments)
    if registry is not None:
        import csv
        import hashlib,math
        from quota import qpu_seconds_from_metrics
        round_records=record_directory(root,archive_block_id or logical_block_id)
        # Import previously collected complete CSVs into the per-PUB ledger only
        # when their original immutable Runtime retrieval archive is available.
        # The source archive itself is never rewritten.
        for row in before_rows:
            if not row.get("target_id"):continue
            prior_pub_records=registry.pub_validation_records(row["target_id"])
            if any(item["job_row_id"]==row["id"] for item in prior_pub_records):continue
            expectations=round_records/row["treatment_id"]/"expectations.csv"
            metadata_file=round_records/row["treatment_id"]/"job_retrieved_metadata.json"
            pubmeta_file=round_records/row["treatment_id"]/"pub_result_metadata.json"
            if not expectations.is_file() or not metadata_file.is_file():continue
            try:metadata=json.loads(metadata_file.read_text(encoding="utf-8"))
            except (OSError,ValueError):continue
            raw_path=Path(metadata.get("raw_result_path",""))
            if not raw_path.is_file():continue
            source_hash=hashlib.sha256(raw_path.read_bytes()).hexdigest()
            try:
                with expectations.open(encoding="utf-8-sig",newline="") as handle:saved_rows=list(csv.DictReader(handle))
                pubmeta=json.loads(pubmeta_file.read_text(encoding="utf-8")) if pubmeta_file.is_file() else []
            except (OSError,ValueError):continue
            expected=int(row.get("chunk_n") or n);items=[]
            for index,saved in enumerate(saved_rows,1):
                if int(saved.get("pub",index))!=index:errors=["PUB_INDEX_ORDER_INVALID"]
                else:errors=[]
                try:evs=[float(saved[f"Z{i}"]) for i in range(7)]
                except (KeyError,TypeError,ValueError):evs=[]
                ev_valid=len(evs)==7 and all(math.isfinite(value) for value in evs)
                if not ev_valid:errors.append("EXPECTATION_NONFINITE_OR_NONNUMERIC")
                fields=pubmeta[index-1].get("available_data_fields",[]) if index<=len(pubmeta) and isinstance(pubmeta[index-1],dict) else []
                uncertainty_missing="stds" not in fields and bool(fields)
                try:stds=[float(saved[f"std_Z{i}"]) for i in range(7)]
                except (KeyError,TypeError,ValueError):stds=[]
                if uncertainty_missing:
                    uncertainty_state="MISSING";uncertainty_valid=None;stds=None
                else:
                    uncertainty_valid=len(stds)==7 and all(math.isfinite(value) and value>=0 for value in stds)
                    uncertainty_state="PRESENT" if uncertainty_valid else "INVALID"
                    if not uncertainty_valid:errors.append("UNCERTAINTY_NONFINITE_OR_INVALID")
                items.append({"pub_index":index,"expectation_valid":ev_valid,"uncertainty_valid":uncertainty_valid,
                    "uncertainty_state":uncertainty_state,"error_codes":errors,
                    "expectation_values":evs if ev_valid else saved,"standard_deviations":stds,
                    "metadata":_redact_runtime_metadata(pubmeta[index-1].get("metadata",{}))
                        if index<=len(pubmeta) and isinstance(pubmeta[index-1],dict) else {}})
            for index in range(len(items)+1,expected+1):
                items.append({"pub_index":index,"expectation_valid":False,"uncertainty_valid":None,
                    "uncertainty_state":"MISSING","error_codes":["MISSING_PUB_RESULT"],
                    "expectation_values":None,"standard_deviations":None,"metadata":{}})
            if not items:continue
            experiment_id=registry.independent_experiment_id(row["target_id"])
            registry.record_pub_validation_records(row["id"],experiment_id,source_hash,items)
            valid_count=sum(item["expectation_valid"] and item["uncertainty_valid"] is True
                and item["pub_index"]<=expected for item in items)
            complete=len(saved_rows)==expected and valid_count==expected and not any(
                "PUB_INDEX_ORDER_INVALID" in item["error_codes"] for item in items)
            state="COMPLETE_VALIDATED" if complete else ("PARTIAL_VALIDATED" if valid_count else "FAILED_VALIDATION")
            invalid_count=max(0,expected-min(valid_count,expected))
            if not complete:invalid_count=max(1,invalid_count)
            registry.set_treatment_validation_state(row["id"],state=state,invalid_pub_count=invalid_count)
            if not complete:
                details={"experiment_id":experiment_id,"batch_id":row.get("batch_id"),
                    "treatment_id":row["treatment_id"],"source_sha256":source_hash,
                    "invalid_pub_indices":[item["pub_index"] for item in items if item["error_codes"]],
                    "error_codes":sorted({code for item in items for code in item["error_codes"]}),
                    "automatic_qpu_resubmit":False}
                registry.record_independent_target_event(row["target_id"],"PUB_VALIDATION_FAILURE",details)
                from independent_n_campaign import _ai_event
                campaign_label,level_index=registry.independent_campaign_context(row["target_id"])
                diagnosis=_ai_event(ai_mode,{"campaign_id":campaign_label or experiment_id,"sequence_no":level_index,
                    "event_class":"NONFINITE_OR_INCOMPLETE_PUB",
                    "treatment_id":row["treatment_id"],"local_state":"RECOVERY_REQUIRED",
                    "safe_summary":"Saved PUB evidence failed per-PUB validation; no QPU resubmission."},explicit=True)
                registry.record_independent_target_event(row["target_id"],"AI_DIAGNOSTIC",{
                    "experiment_id":experiment_id,"event_class":"NONFINITE_OR_INCOMPLETE_PUB",
                    "treatment_id":row["treatment_id"],"source_sha256":source_hash,
                    "available":bool(diagnosis and diagnosis.get("available")),
                    "diagnosis":diagnosis.get("diagnosis") if diagnosis else None,
                    "reason":diagnosis.get("reason") if diagnosis else "AI provider not invoked in python mode",
                    "ai_actions_executed":False,"automatic_qpu_resubmit":False})
        for row in registry.rows(logical_block_id):
            if job_row_id is not None and row['id']!=job_row_id:continue
            if treatments is not None and (row["treatment_id"] not in treatments or
                row["id"]!=rows_by_treatment[row["treatment_id"]]["id"]):continue
            metadata_path=round_records/row["treatment_id"]/"job_retrieved_metadata.json"
            if metadata_path.is_file():
                metadata=json.loads(metadata_path.read_text(encoding="utf-8"))
                status=str(metadata.get("status","")).upper().split(".")[-1]
                terminal="COMPLETED" if status in ("DONE","COMPLETED") else ("FAILED" if status in ("ERROR","FAILED") else ("CANCELLED" if status=="CANCELLED" else "SUBMITTED"))
                measured=metadata.get("qpu_usage_s")
                if measured is None: measured=qpu_seconds_from_metrics(metadata.get("metrics_raw"))
                registry.transition(row["id"],terminal,qpu_usage_seconds=measured,
                    metadata={"collected_metadata_path":str(metadata_path),"timestamps":metadata.get("timestamps")},
                    event_type="COLLECTION_RECONCILED",details={"status":status,"qpu_usage_seconds":measured})
                if row.get("target_id"):
                    evidence=registry.pub_validation_records(row["target_id"])
                    treatment_records=[item for item in evidence if item["job_row_id"]==row["id"]]
                    actual=sum("MISSING_PUB_RESULT" not in json.loads(item["error_codes_json"]) for item in treatment_records)
                    completed=min(int(row["chunk_n"]),actual)
                    collected=completed
                    validated=min(int(row["chunk_n"]),sum(item["expectation_valid"] and item["uncertainty_valid"]!=0
                        for item in treatment_records if item["pub_index"]<=int(row["chunk_n"])))
                    if not treatment_records:
                        expectations=round_records/row["treatment_id"]/"expectations.csv"
                        if expectations.is_file():
                            with expectations.open(encoding="utf-8-sig",newline="") as handle:collected_rows=list(csv.DictReader(handle))
                            collected=completed=min(int(row["chunk_n"]),len(collected_rows))
                            try:validate_rows(collected_rows,int(row["chunk_n"]),str(expectations),root=root);validated=collected
                            except Exception:validated=0
                    reported=metadata.get("pub_results")
                    if terminal=="COMPLETED" and isinstance(reported,int) and 0<=reported<=int(row["chunk_n"]): completed=max(completed,reported)
                    registry.update_pub_progress(row["id"],completed=completed,collected=collected,validated=validated,
                        event_type="ADAPTIVE_PUBS_COLLECTED_AND_VALIDATED" if validated==collected==int(row["chunk_n"]) else "ADAPTIVE_PUB_COLLECTION_INCOMPLETE_OR_INVALID")
                    if validated!=int(row["chunk_n"]):
                        registry.transition(row["id"],terminal,event_type="COLLECTION_VALIDATION_FAILED",
                            details={"classification":"INCOMPLETE_PUBS" if collected<int(row["chunk_n"]) else "RESULT_CORRUPTION",
                                     "collected_pub_count":collected,"expected_pub_count":int(row["chunk_n"]),
                                     "automatic_retry":False})
    return result


def analyze_block(root, logical_block_id: str, *, shots: int = 4096, n: int = 5):
    """Delegate existing EIS validation, SSE/RMSE analysis and plotting as one protected pass."""
    return load_pilot(root).analyze(_args(root,logical_block_id,shots=shots,n=n))


def plot_block(root, logical_block_id: str, *, shots: int = 4096, n: int = 5):
    """The canonical analyze function owns all plotting calculations and output."""
    return analyze_block(root,logical_block_id,shots=shots,n=n)
