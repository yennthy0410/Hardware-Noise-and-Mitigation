"""Single non-secret configuration schema for the distributable assistant."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


import json
import math
from pathlib import Path
from typing import Any

from account_config import ACCOUNT_LABELS

SCHEMA_VERSION = 1
DEFAULT_CONFIG_NAME = "final_config.json"
MIN_BATCH_QUOTA_SECONDS = 150.0
MIN_RECOVERY_QUOTA_SECONDS = 40.0
PREFERRED_NEW_BATCH_ACCOUNTS = ("labK", "labI", "labG")


def quota_eligibility(available_seconds, *, workload_type="original_batch",
                      provider_remaining_seconds=None, estimated_seconds=None,
                      safety_margin_seconds=0.0):
    """Single admission policy; callers supply freshly verified, unreserved quota.

    Authorization balances and provider reservations are reduced upstream once.
    Estimates and margins are telemetry, never an admission or reservation size.
    """
    recovery=("targeted_replacement","missing_treatment","combined_recovery")
    if workload_type not in ("original_batch",*recovery):raise ValueError("Unsupported workload_type")
    minimum=MIN_BATCH_QUOTA_SECONDS if workload_type=="original_batch" else MIN_RECOVERY_QUOTA_SECONDS
    def valid(value):
        return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0
    provider=available_seconds if provider_remaining_seconds is None else provider_remaining_seconds
    reasons=[];warnings=[]
    for label,value in (("verified available quota",available_seconds),("verified provider remaining quota",provider)):
        if not valid(value):reasons.append(label+" is missing or invalid")
        elif value<minimum:reasons.append(f"{label} {value:.3f}s is below required {minimum:g}s")
    runtime_total=(float(estimated_seconds)+float(safety_margin_seconds)
                   if valid(estimated_seconds) and valid(safety_margin_seconds) else None)
    if runtime_total is None:warnings.append("Runtime estimate/margin unavailable or invalid; diagnostic only")
    elif valid(available_seconds) and runtime_total>available_seconds:
        warnings.append("Estimated runtime plus safety margin exceeds available quota; diagnostic only")
    return {"eligible":not reasons,"quota_policy":"verified-unreserved-fixed-threshold-v1",
        "minimum_remaining_seconds":minimum,"required_quota_seconds":minimum,
        "reservation_seconds":minimum,"remaining_seconds":available_seconds,
        "remaining_provider_quota_seconds":provider,"estimated_seconds":estimated_seconds,
        "safety_margin_seconds":safety_margin_seconds,
        "required_runtime_with_margin_seconds":runtime_total,
        "required_seconds_including_margin":minimum,  # legacy key: admission threshold, not telemetry
        "reasons":reasons,"warnings":warnings}


def provider_quota_thresholds(remaining):
    """Provider-only display flags use the same policy as transactional admission."""
    return {"batch_quota_threshold_eligible":quota_eligibility(remaining)["eligible"],
        "recovery_quota_threshold_eligible":quota_eligibility(remaining,workload_type="targeted_replacement")["eligible"]}


def validate_final_config(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("final config schema_version must be 1")
    accounts = value.get("enabled_account_labels")
    if (not isinstance(accounts, list) or len(accounts) != len(set(accounts))
            or any(label not in ACCOUNT_LABELS for label in accounts)):
        raise ValueError("enabled_account_labels must be a unique subset of " + ", ".join(ACCOUNT_LABELS))
    if value.get("allowed_backend") != "ibm_fez":
        raise ValueError("allowed_backend must be ibm_fez")
    for key in ("runtime_budget_seconds_per_account", "safety_margin_seconds"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)) or number < 0:
            raise ValueError(f"{key} must be a finite nonnegative number")
    # 0 in JSON or None in an already-normalized config disables the separate
    # replacement budget. Account caps still apply; campaign budget is telemetry.
    replacement_budget = value.get("maximum_replacement_qpu_budget_seconds")
    if replacement_budget is not None and (
        isinstance(replacement_budget, bool)
        or not isinstance(replacement_budget, (int, float))
        or not math.isfinite(float(replacement_budget))
        or replacement_budget < 0
    ):
        raise ValueError("maximum_replacement_qpu_budget_seconds must be nonnegative, finite, or null")
    if value["runtime_budget_seconds_per_account"] <= 0:
        raise ValueError("runtime_budget_seconds_per_account must be positive")
    attempts = value.get("maximum_replacement_attempts")
    if attempts is not None and (isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0):
        raise ValueError("maximum_replacement_attempts must be a nonnegative integer")
    for key in ("auto_recovery_enabled", "live_submission_enabled"):
        if not isinstance(value.get(key), bool):
            raise ValueError(f"{key} must be a boolean")
    selection=value.get("account_selection","fixed_by_n")
    if selection not in ("fixed_by_n","highest_available_quota"):
        raise ValueError("Unsupported account_selection")
    account_by_n=value.get("account_by_n",{})
    if not isinstance(account_by_n,dict) or any(str(n) not in {"5","10","15","20","25","30"} or label not in accounts
            for n,label in account_by_n.items()):
        raise ValueError("account_by_n must map supported n levels to enabled account labels")
    instance_refs=value.get("allowed_instance_refs",{})
    if not isinstance(instance_refs,dict) or any(label not in accounts or not isinstance(refs,list) or
            any(not isinstance(ref,str) or len(ref)!=12 or any(c not in "0123456789abcdef" for c in ref.lower()) for ref in refs)
            for label,refs in instance_refs.items()):
        raise ValueError("allowed_instance_refs must map enabled accounts to opaque 12-character instance references")
    campaign_budget=value.get("campaign_runtime_budget_seconds",sum(float(value["runtime_budget_seconds_per_account"]) for _ in accounts))
    max_batches=value.get("maximum_batches",21)
    if isinstance(campaign_budget,bool) or not isinstance(campaign_budget,(int,float)) or not math.isfinite(float(campaign_budget)) or campaign_budget<0:
        raise ValueError("campaign_runtime_budget_seconds must be finite and nonnegative")
    # Retain maximum_batches as ignored legacy metadata, never an authorization gate.
    # Legacy time_window_utc is accepted as ignored metadata. Operating hours
    # are unrestricted; QPU submission remains controlled by separate gates.
    if value["live_submission_enabled"]:
        if selection=="fixed_by_n" and set(account_by_n)!={"5","10","15","20","25","30"}:
            raise ValueError("Live final config requires an account selection for each independent n level")
        used=set(account_by_n.values()) if selection=="fixed_by_n" else set()
        if not used.issubset(set(instance_refs)) or any(not instance_refs[label] for label in used):
            raise ValueError("Live final config requires opaque authorized instance references for every selected account")
    prohibited = {"api_key", "apikey", "crn", "token", "secret", "credentials"}
    def inspect(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if any(part in str(key).lower() for part in prohibited):
                    raise ValueError("final config must not contain credentials, tokens, or CRNs")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
        elif isinstance(item, str) and item.lower().startswith("crn:"):
            raise ValueError("final config must not contain credentials, tokens, or CRNs")
    inspect(value)
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled_account_labels": list(accounts),
        "allowed_backend": "ibm_fez",
        "runtime_budget_seconds_per_account": float(value["runtime_budget_seconds_per_account"]),
        "safety_margin_seconds": float(value["safety_margin_seconds"]),
        "maximum_replacement_attempts": None,  # legacy field; approved unlimited policy
        "maximum_replacement_qpu_budget_seconds": (
            None if replacement_budget is None or replacement_budget == 0
            else float(replacement_budget)
        ),
        "auto_recovery_enabled": value["auto_recovery_enabled"],
        "live_submission_enabled": value["live_submission_enabled"],
        "account_by_n":{str(n):label for n,label in account_by_n.items()},
        "account_selection":selection,
        "allowed_instance_refs":{label:list(refs) for label,refs in instance_refs.items()},
        "campaign_runtime_budget_seconds":float(campaign_budget),
        "maximum_batches":max_batches,
        "time_window_utc":None,
        "quota_policy":"verified-unreserved-fixed-threshold-v1",
        "runtime_estimate_and_margin_mode":"diagnostic_only",
        "original_batch_minimum_quota_seconds":MIN_BATCH_QUOTA_SECONDS,
        "replacement_minimum_quota_seconds":MIN_RECOVERY_QUOTA_SECONDS,
    }


def load_final_config(project: str | Path) -> dict[str, Any]:
    path = Path(project).resolve() / "02_Hardware" / "04_code" / "03_ai_assistant" / DEFAULT_CONFIG_NAME
    if not path.is_file():
        raise FileNotFoundError(f"Final configuration is missing: {path}")
    return validate_final_config(json.loads(path.read_text(encoding="utf-8-sig")))


def campaign_authorization_from_final_config(value: dict[str,Any]) -> dict[str,Any]:
    """Build active execution gates from final config, without legacy revisions."""
    config=validate_final_config(value)
    authorized=[label for label in config["enabled_account_labels"]
        if config["allowed_instance_refs"].get(label)]
    return {"allowed_accounts":authorized,
        "account_by_n":config["account_by_n"],
        "account_selection":config["account_selection"],
        "allowed_instance_refs":{label:config["allowed_instance_refs"][label] for label in authorized},
        "account_runtime_budget_seconds":{label:config["runtime_budget_seconds_per_account"]
            for label in authorized},
        "total_runtime_budget_seconds":config["campaign_runtime_budget_seconds"],
        "maximum_batches":config["maximum_batches"],
        "safety_margin_seconds":config["safety_margin_seconds"],
        "recovery_limits":{"max_read_attempts":3,"max_qpu_resubmissions":0},
        "allowed_actions":["prepare","preflight","dry-run","submit","monitor","collect",
            "validate","recover","analyze","plot","report"],
        "live_auto_submit":config["live_submission_enabled"],
        "completeness_policy":{"version":"complete-only-repaired-v1"}}
