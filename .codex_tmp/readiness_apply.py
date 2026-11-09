import sys, json, shutil, hashlib
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'.codex_tmp'))
import production_readiness as audit
from unittest.mock import patch
from job_registry import JobRegistry
from worker_runtime import campaign_worker_lock
from independent_n_campaign import campaign_root
from research_pipeline import verify_campaign
ROOT=Path.cwd();directory=Path((ROOT/'.codex_tmp/readiness_directory.txt').read_text())
report=json.loads((directory/'backup-and-trial.json').read_text());production=Path(report['production'])
trial=directory/'migration-atomic-trial.sqlite3';shutil.copy2(report['backup'],trial)
before=audit.fingerprint(trial)
connect=JobRegistry._connect
class FailIndex:
    def __init__(self,db):self.db=db
    def __getattr__(self,key):return getattr(self.db,key)
    def execute(self,sql,*args):
        if 'CREATE UNIQUE INDEX IF NOT EXISTS replacement_provider_pub_identity' in sql:raise RuntimeError('injected index failure')
        return self.db.execute(sql,*args)
with patch.object(JobRegistry,'_connect',lambda instance:FailIndex(connect(instance))):
    try:JobRegistry(trial)
    except RuntimeError as exc:assert str(exc)=='injected index failure'
    else:raise AssertionError('Rollback injection did not execute')
assert audit.fingerprint(trial)==before
JobRegistry(trial);migrated=audit.fingerprint(trial)
assert migrated['tables']==before['tables'] and migrated['integrity']==['ok'] and not migrated['foreign_keys']
assert audit.accepted(trial)==report['accepted_before']
JobRegistry(trial);assert audit.fingerprint(trial)==migrated
with campaign_worker_lock(campaign_root(ROOT,audit.CID)):
    audit.inactive()
    assert audit.fingerprint(production)==report['before'],'Production changed since backup; refuse migration'
    assert audit.accepted(production)==report['accepted_before']
    JobRegistry(production)
    after=audit.fingerprint(production)
    assert after==migrated,'Production differs from verified migration trial'
    selected=audit.accepted(production);assert selected==report['accepted_before']
    status=verify_campaign(ROOT,audit.CID);assert status['verification_passed']
    result={'backup':report['backup'],'rollback_injection_passed':True,'idempotent':True,
        'all_table_rows_unchanged':after['tables']==before['tables'],'before':before,'after':after,
        'accepted_before':report['accepted_before'],'accepted_after':selected,'campaign_verification':status,
        'migration_applied':True,'submission_performed':False}
    (directory/'production-migration.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({'migration_applied':True,'all_table_rows_unchanged':True,'accepted':selected['count'],'integrity':after['integrity'],'state':selected['state']}))
