"""Read-only post-migration readiness evidence; deny network access."""
import sys
from pathlib import Path
ROOT=Path.cwd();AI=ROOT/'02_Hardware/04_code/03_ai_assistant'
sys.path.insert(0,str(AI));sys.path.insert(0,str(ROOT/'.codex_tmp'))
from low_memory_runtime import limit_native_threads
limit_native_threads()
import ast
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import socket
import sqlite3
import production_readiness as baseline

socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('OFFLINE AUDIT forbids network'))
baseline.inactive()
OUT=ROOT/'manager_audit_backups'/('final-offline-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
OUT.mkdir();(ROOT/'.codex_tmp/final_offline_audit_dir.txt').write_text(str(OUT))
prior=Path((ROOT/'.codex_tmp/migration_audit.txt').read_text())
prior_state=json.loads((prior/'registry-after.json').read_text())
from job_registry import JobRegistry
from independent_n_campaign import (dry_run_independent_campaign,_target_from_level,
    _repaired_block_complete,load_campaign_authorization)
from research_pipeline import verify_campaign
from final_configuration import quota_eligibility,load_final_config
from vqe_adapter import verify_frozen_inputs
db=JobRegistry.default_database(ROOT).resolve()
fingerprint=baseline.fingerprint(db);accepted=baseline.accepted(db)
assert fingerprint==prior_state['fingerprint'];assert accepted==prior_state['accepted']
registry=JobRegistry(db,readonly=True);status=registry.independent_campaign_status(baseline.CID)
dry=dry_run_independent_campaign(ROOT,baseline.CID,registry)
verification=verify_campaign(ROOT,baseline.CID)
frozen=verify_frozen_inputs(ROOT)
level=status['levels'][5];target=_target_from_level(ROOT,status['manifest']['levels'][5])
blocks=registry.target_status(target.target_id)['blocks']
checked=[dict(logical_block_id=b['logical_block_id'],state=b['status'],
              repaired_complete=_repaired_block_complete(registry,target,b['logical_block_id'])) for b in blocks]
first_incomplete=next((b['logical_block_id'] for b in checked if not b['repaired_complete']),None)
next_block=first_incomplete or f'{target.experiment_id}-block-{len(blocks)+1:03d}'
ids={}
with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
    for table,desc in fingerprint['tables'].items():
        cols=[name for name in desc['columns'] if name in ('job_id','replacement_job_id','original_job_id')]
        for col in cols:
            values=sorted(str(row[0]) for row in conn.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL AND "{col}" != ?',('',)))
            ids[f'{table}.{col}']=dict(count=len(values),unique=len(set(values)),sha256=hashlib.sha256(json.dumps(values).encode()).hexdigest())
spec=importlib.util.spec_from_file_location('audit_offline_tools',AI/'01_ai_agent/offline_tools.py')
tools=importlib.util.module_from_spec(spec);sys.modules[spec.name]=tools;spec.loader.exec_module(tools)
comparison=tools.comparison_readiness(tools.CampaignRequest(ROOT))
config=load_final_config(ROOT)
quota=[]
for value in (39,40,45,149,150,155,175):
    quota.append(dict(remaining=value,original=quota_eligibility(value,estimated_seconds=1000,safety_margin_seconds=100)['eligible'],
        recovery=quota_eligibility(value,workload_type='targeted_replacement',estimated_seconds=1000,safety_margin_seconds=100)['eligible']))
import mxene,diagnostic_manager,research_pipeline
entrypoints={m.__name__:m.__file__ for m in (mxene,diagnostic_manager,research_pipeline)}
report=dict(campaign=baseline.CID,state=status['state'],checkpoint=status.get('checkpoint'),
    registry_unchanged_since_migration=True,accepted=accepted,sqlite=fingerprint,
    job_identity_inventory=ids,verification=verification,frozen=frozen,
    n30=dict(blocks=checked,next_local_candidate=next_block,rows_for_005=len(registry.rows(f'{target.experiment_id}-block-005')),
             rows_for_006=len(registry.rows(f'{target.experiment_id}-block-006')),dry_run=dry['levels'][5]),
    quota_cases=quota,entrypoints=entrypoints,comparison=comparison,
    configuration={k:config.get(k) for k in ('live_submission_enabled','auto_recovery_enabled','enabled_account_labels','account_selection')},
    provider_checked=False,network_calls=0,production_writes=0)
(OUT/'audit.json').write_text(json.dumps(report,indent=2,default=str))
assert baseline.fingerprint(db)==fingerprint
print(json.dumps(dict(audit=str(OUT),accepted=accepted['count'],integrity=fingerprint['integrity'],
    verification=verification['verification_passed'],n30_blocks=checked,next_local_candidate=next_block,
    unresolved_identities=verification['unresolved_identities'],comparison_ready=comparison['ready']),indent=2))
