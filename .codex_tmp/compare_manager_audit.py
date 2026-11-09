import sys,json,hashlib,sqlite3
from pathlib import Path
root=Path.cwd()
sys.path.insert(0,str(root/'02_Hardware/04_code/03_ai_assistant'))
from job_registry import JobRegistry
from worker_runtime import WorkerLock
from independent_n_campaign import campaign_root
backup=root/'manager_audit_backups/20261107T064712Z'
cid='vqe-independent-n-01'
with WorkerLock(campaign_root(root,cid)/'worker.lock'):
    registry=JobRegistry(JobRegistry.default_database(root),readonly=True)
    before=json.loads((backup/'accepted_coverage.json').read_text())
    after={target:registry.repaired_pub_values(target) for target in before}
    digest=hashlib.sha256(json.dumps(after,sort_keys=True,default=str).encode()).hexdigest()
    original=sqlite3.connect((backup/'job_registry.sqlite3').as_uri()+'?mode=ro',uri=True)
    current=registry._connect()
    tables=[row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    changed=[]
    for table in tables:
        a=original.execute('SELECT * FROM "'+table+'"').fetchall()
        b=[tuple(row) for row in current.execute('SELECT * FROM "'+table+'"').fetchall()]
        if a!=b:changed.append(table)
    current.close();original.close()
    changed_checkpoints=[]
    for path in (backup/'campaign').rglob('*.json'):
        relative=path.relative_to(backup/'campaign')
        live=campaign_root(root,cid)/relative
        if not live.exists() or path.read_bytes()!=live.read_bytes():changed_checkpoints.append(str(relative))
    report={'coverage_unchanged':before==after,'coverage_sha256':digest,
            'changed_database_tables':changed,'accepted_pub_count':sum(map(len,after.values())),
            'changed_campaign_json_files':changed_checkpoints,
            'schema_migration_required':False}
    (backup/'post_audit_preservation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    if before!=after or changed or changed_checkpoints:raise SystemExit(2)
