import sys, json, sqlite3, hashlib, shutil, os
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path.cwd();AI=ROOT/'02_Hardware/04_code/03_ai_assistant'
sys.path.insert(0,str(AI))
from low_memory_runtime import limit_native_threads
limit_native_threads()
from job_registry import JobRegistry
from worker_runtime import campaign_worker_lock
from independent_n_campaign import campaign_root
CID='vqe-independent-n-01'
def fingerprint(path):
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        tables={}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            quote=lambda s:'"'+s.replace('"','""')+'"'
            cols=[r[1] for r in conn.execute('PRAGMA table_info('+quote(name)+')')]
            digest=hashlib.sha256();count=0
            for row in conn.execute('SELECT * FROM '+quote(name)+' ORDER BY '+','.join(map(quote,cols))):
                digest.update(json.dumps(row,separators=(',',':'),ensure_ascii=False,default=lambda v:v.hex()).encode());digest.update(b'\n');count+=1
            tables[name]={'count':count,'sha256':digest.hexdigest(),'columns':cols}
        schema=list(conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"))
        return {'tables':tables,'schema_sha256':hashlib.sha256(json.dumps(schema).encode()).hexdigest(),
            'integrity':[r[0] for r in conn.execute('PRAGMA integrity_check')],
            'foreign_keys':list(conn.execute('PRAGMA foreign_key_check'))}
    finally:conn.close()
def accepted(path):
    registry=JobRegistry(path,readonly=True);status=registry.independent_campaign_status(CID)
    data={l['target_id']:registry.repaired_pub_values(l['target_id']) for l in status['levels']}
    return {'count':sum(map(len,data.values())),'sha256':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest(),
        'per_level':{k:len(v) for k,v in data.items()},'state':status['state'],'index':status['current_level_index'],'checkpoint':status.get('checkpoint')}
def inactive():
    import psutil
    for p in psutil.process_iter(['pid','cmdline']):
        cmd=p.info.get('cmdline') or []
        if '--live' in cmd and any(Path(x).name.lower() in ('mxene.py','diagnostic_manager.py') for x in cmd):
            raise RuntimeError('LIVE worker detected: '+str(p.pid))
def main():
    inactive();production=JobRegistry.default_database(ROOT).resolve()
    directory=ROOT/'manager_audit_backups'/('readiness-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    directory.mkdir(parents=True,exist_ok=False)
    with campaign_worker_lock(campaign_root(ROOT,CID)):
        inactive()
        before=fingerprint(production)
        assert before['integrity']==['ok'] and not before['foreign_keys']
        snapshot=directory/'registry-consistent.sqlite3'
        with sqlite3.connect(production.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(snapshot) as dst:
            src.execute('BEGIN');src.backup(dst,pages=256,sleep=.1)
        assert fingerprint(snapshot)['tables']==before['tables']
        assert fingerprint(production)['tables']==before['tables']
        sidecars={};forensic=directory/'physical-forensics';forensic.mkdir()
        for suffix in ('','-wal','-shm'):
            source=Path(str(production)+suffix)
            if source.exists():
                dest=forensic/(production.name+suffix);shutil.copy2(source,dest)
                with dest.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
                sidecars[suffix or 'main']={'size':dest.stat().st_size,'sha256':digest}
            else:sidecars[suffix]={'present':False}
        selected=accepted(snapshot);assert selected==accepted(production)
        report={'production':str(production),'backup':str(snapshot),'before':before,'accepted_before':selected,
            'physical_files':sidecars,'restore_note':'Use registry-consistent.sqlite3 alone. Online backup incorporates committed WAL; SHM is transient. Physical sidecars are forensic copies, not a restore set.'}
        trial=directory/'migration-trial.sqlite3';shutil.copy2(snapshot,trial)
        JobRegistry(trial);first=fingerprint(trial);selected_after=accepted(trial)
        assert first['tables']==before['tables'] and first['integrity']==['ok'] and not first['foreign_keys']
        assert selected_after==selected
        JobRegistry(trial);second=fingerprint(trial)
        assert first==second and accepted(trial)==selected
        report.update(trial_after=first,migration_idempotent=True,all_table_values_preserved=True,accepted_preserved=True)
        (directory/'backup-and-trial.json').write_text(json.dumps(report,indent=2))
        (ROOT/'.codex_tmp/readiness_directory.txt').write_text(str(directory))
        print(json.dumps({'directory':str(directory),'backup_valid':True,'trial_valid':True,'idempotent':True,'accepted':selected},default=str))
if __name__=='__main__':main()
