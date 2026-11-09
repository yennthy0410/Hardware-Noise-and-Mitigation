"""Bounded local snapshots. Never create a provider service or open production writable."""
from pathlib import Path
import csv, hashlib, json, sqlite3, sys, threading, time
from contextlib import closing
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent
CODE=HERE.parent
ROOT=CODE.parent
PROD=CODE/'03_ai_assistant'
OUT=ROOT/'01_vqe/06_DD_TREX_ZNE_mitigation/output'
sys.path.insert(0,str(PROD/'03_python_automation'))
from figure_registry import contract as figure_contract,descriptor as figure_descriptor
DB=OUT/'03_IBM_execution_records/ai_assistant/job_registry.sqlite3'
CAMPAIGN='vqe-independent-n-01'
LEVELS=(5,10,15,20,25,30)
TREATMENTS=('M0_Baseline','M1_DD','M2_TREX','M3_ZNE','M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL')
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def read_json(path):
    if path.stat().st_size>8*1024*1024: raise ValueError('Oversized JSON')
    return json.loads(path.read_text(encoding='utf-8-sig'))
def snapshot(source,destination,seconds=3):
    deadline=time.monotonic()+seconds
    def progress(*args):
        if time.monotonic()>deadline: raise TimeoutError('Snapshot deadline')
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)) as src:
        src.execute('PRAGMA query_only=ON')
        with closing(sqlite3.connect(destination)) as dst: src.backup(dst,pages=128,progress=progress,sleep=.01)

def data_revision():
    """Local evidence version only: never contact the broker or IBM."""
    paths={DB,Path(str(DB)+'-wal'),HERE/'state/worker_control.sqlite3'}
    paths.update((HERE/'state').glob('step-*.json'))
    roots=[OUT/'03_IBM_execution_records/ai_assistant/independent_n_campaigns'/CAMPAIGN/'steps',OUT/'02_run_level_data/adaptive_targets']
    from repository_paths import comparison_output_paths
    roots += list(comparison_output_paths(ROOT.parent).values())
    roots += [OUT/category/f'n={n}' for n in LEVELS for category in ('01_results_and_figures','02_run_level_data')]
    for root in roots:paths.update(p for p in root.rglob('*') if p.is_file())
    stamps=[]
    for path in sorted(paths):
        try:
            info=path.stat();stamps.append((str(path),info.st_size,info.st_mtime_ns))
        except FileNotFoundError:stamps.append((str(path),None,None))
    return hashlib.sha256(repr(stamps).encode()).hexdigest()

class Reader:
    def __init__(self):
        self.lock=threading.RLock(); self.cached=None; self.at=0; self.files={};self.revision=None
    def invalidate(self):
        with self.lock:self.revision=None;self.at=0
    def status(self,force=False):
        with self.lock:
            revision=data_revision()
            if not force and self.cached and not self.cached.get('offline') and revision==self.revision and time.monotonic()-self.at<120:return self.cached
            try:
                snap=HERE/'state/snapshot.sqlite3'
                snapshot(DB,snap)
                value=self.project(snap)
                value['artifacts']=self.catalog()
                value['figure_contract']=figure_contract()
                value['captured_at']=datetime.now(timezone.utc).isoformat()
                value['offline']=False; value['error']=None
                if data_revision()!=revision:raise RuntimeError('Canonical evidence changed during snapshot; refresh again')
                value['revision']=revision;self.revision=revision
                self.cached=value; self.at=time.monotonic()
            except Exception as exc:
                if not self.cached: self.cached={'levels':[],'jobs':[],'recovery':[],'events':[],'reviews':[],'artifacts':self.catalog(),'expected':840}
                self.cached=dict(self.cached,offline=True,error=type(exc).__name__+': local snapshot unavailable')
                self.at=time.monotonic()
            return self.cached

    def bundle(self,force=False):
        """One locked local snapshot supplies all three Dashboard views."""
        from campaign_progress import read_progress
        from execution_records import historical
        with self.lock:
            value=self.status(force=force)
            if value.get('offline'):raise RuntimeError(value.get('error') or 'Local snapshot unavailable')
            progress=read_progress(database=HERE/'state/snapshot.sqlite3',out=OUT,snapshot=value)
            records=historical(value,root=OUT)
            if data_revision()!=value['revision']:raise RuntimeError('Canonical evidence changed during refresh; retry required')
            progress['revision']=records['revision']=value['revision']
            return {'revision':value['revision'],'snapshot':value,'progress':progress,'records':records}
    def project(self,snap):
        # Only import the audited stdlib ledger; readonly=True prevents schema creation.
        sys.dont_write_bytecode=True
        sys.path.insert(0,str(PROD/'03_python_automation'))
        from job_registry import JobRegistry
        registry=JobRegistry(snap,readonly=True)
        with sqlite3.connect(snap.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            def rows(sql,args=()): return [dict(r) for r in db.execute(sql,args)]
            campaign=rows('SELECT campaign_id,state,current_level_index,updated_at FROM independent_n_campaigns WHERE campaign_id=?',(CAMPAIGN,))
            levels=rows('SELECT target_n,target_id,state,updated_at FROM independent_n_campaign_levels WHERE campaign_id=? ORDER BY level_index',(CAMPAIGN,))
            jobs=rows('SELECT job_id,batch_id,logical_block_id,treatment_id,account_label,backend,submission_state,created_at,updated_at FROM jobs WHERE target_id IN (SELECT target_id FROM independent_n_campaign_levels WHERE campaign_id=?) ORDER BY id DESC LIMIT 1000',(CAMPAIGN,))
            replacements=rows('SELECT replacement_id,logical_block_id,treatment_id,logical_pub_index,attempt,state,job_id,batch_id,account_label,backend,source_account_label,error_codes_json,created_at,updated_at FROM pub_replacement_jobs WHERE target_id IN (SELECT target_id FROM independent_n_campaign_levels WHERE campaign_id=?) ORDER BY updated_at DESC LIMIT 1000',(CAMPAIGN,))
            observed={}
            for monitor in rows('SELECT checked_at,snapshot_json FROM batch_monitor_state'):
                for j in json.loads(monitor['snapshot_json']).get('jobs',[]):
                    if j.get('job_id'): observed[j['job_id']]={'provider_status':j.get('status'),'observed_at':monitor['checked_at']}
            for j in jobs: j.update(kind='Original',**observed.get(j['job_id'],{}))
            for j in replacements: jobs.append(dict(j,kind='Replacement',submission_state=j['state'],**observed.get(j['job_id'],{})))
            accepted_by_target={}
            for level in levels:
                accepted=registry.repaired_pub_values(level['target_id'])
                accepted_by_target[level['target_id']]=accepted
                keys={(r['treatment_id'],r['logical_pub_index']) for r in accepted}
                invalid=rows('SELECT v.treatment_id,v.pub_index,b.sequence_no,t.chunk_n FROM pub_validation_records v JOIN jobs j ON j.id=v.job_row_id JOIN experiment_blocks b ON b.logical_block_id=j.logical_block_id JOIN experiment_targets t ON t.target_id=j.target_id WHERE j.target_id=? AND (v.expectation_valid=0 OR v.uncertainty_valid=0)',(level['target_id'],))
                bad={(r['treatment_id'],(r['sequence_no']-1)*r['chunk_n']+r['pub_index']) for r in invalid}-keys
                expected={(t,i) for t in TREATMENTS for i in range(1,level['target_n']+1)}
                level.update(expected=len(expected),valid=len(keys & expected),invalid=len(bad & expected),missing=len(expected-keys-bad),recovered=sum(r['source']!='ORIGINAL' for r in accepted))
            from jobs_view import project_jobs
            execution_jobs=project_jobs(db,CAMPAIGN,accepted_by_target)
            from campaign_progress import recorded_step_observations,step_records
            execution_jobs=recorded_step_observations(execution_jobs,step_records(),CAMPAIGN)
            from science_ui import recovery_rows
            blocks={b['logical_block_id']:b for b in rows('SELECT logical_block_id,sequence_no,chunk_n FROM experiment_blocks')}
            recovery_summary=recovery_rows(levels,accepted_by_target,execution_jobs,blocks)
            events=rows('SELECT event_type,created_at FROM independent_n_campaign_events WHERE campaign_id=? ORDER BY id DESC LIMIT 200',(CAMPAIGN,))
            reviews=rows('SELECT logical_block_id,batch_execution_status,recovery_status,raw_archives_verified,provenance_verified,analysis_verified,figures_verified,progression_eligible,reviewed_at FROM batch_recovery_reviews ORDER BY review_id DESC LIMIT 100')
        frozen=read_json(OUT/'03_IBM_execution_records/locked_inputs/lock.json')
        frozen={k:frozen[k] for k in ('source_run','backend','dataset','physical_layout_logical_order','isa_depth','isa_size','circuit_qubits','shots_requested','hashes') if k in frozen}
        return dict(campaign=campaign,levels=levels,jobs=jobs,execution_jobs=execution_jobs,recovery=replacements,recovery_summary=recovery_summary,events=events,reviews=reviews,frozen=frozen,expected=840,valid=sum(l['valid'] for l in levels),invalid=sum(l['invalid'] for l in levels),missing=sum(l['missing'] for l in levels),recovered=sum(l['recovered'] for l in levels))
    def catalog(self):
        result=[]; self.files={}
        from figure_registry import inventory
        for n in LEVELS:
            for version in inventory(OUT,n)['versions']:
                for item in version['figures']:
                    if item['display_eligible']:
                        self.add(result,Path(item['path']),n,version['version'],item['sha256'])
                for manifest in version['data_manifests']:
                    for name,digest in manifest.get('csv_sha256',{}).items():
                        if Path(name).name==name:self.add(result,Path(version['data_directory'])/name,n,version['version'],digest)
        # Comparison is read exclusively through its hash-verified publication contract.
        from campaign_comparison import published_comparison
        from repository_paths import comparison_output_paths
        try:
            publication=published_comparison(ROOT.parent);paths=comparison_output_paths(ROOT.parent)
            for kind in ('figures','tables'):
                for entry in publication[kind]:
                    self.add(result,paths['figures' if kind=='figures' else 'data']/entry['filename'],
                             'statistics',f"v{publication['version']:03d}",entry['sha256'])
        except (OSError,ValueError,KeyError,TypeError):
            pass  # Statistics view exposes the explicit missing/invalid state.
        return list({item['id']:item for item in result}.values())
    def add(self,items,path,level,version,expected):
        path=path.resolve()
        allowed=[OUT/'01_results_and_figures',OUT/'02_run_level_data']
        if not any(path.is_relative_to(p.resolve()) for p in allowed) or not path.is_file() or path.suffix not in ('.png','.csv','.json'): return
        if path.stat().st_size>30*1024*1024: return
        digest=sha(path)
        if expected and digest!=expected: return
        key=hashlib.sha256(str(path).encode()).hexdigest()[:24]
        self.files[key]=(path,digest)
        items.append(dict(id=key,name=path.name,level=level,version=version,kind=path.suffix[1:],sha256=digest,timestamp=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),finality='Published matching export pair; scientific acceptance not independently recertified' if expected else 'File present; completion not certified',verification='manifest hash matched' if expected else 'file present; scientific acceptance not asserted'))
    def artifact(self,key):
        item=self.files.get(key)
        if not item: raise KeyError(key)
        path,digest=item
        if sha(path)!=digest: raise ValueError('Artifact changed; refresh required')
        if path.suffix=='.json':
            self.no_secrets(read_json(path))
        elif path.suffix=='.csv':
            with path.open(encoding='utf-8-sig',newline='') as f:
                self.no_secrets({k:None for k in next(csv.reader(f),[])})
        return path
    @staticmethod
    def no_secrets(value):
        if isinstance(value,dict):
            for key,item in value.items():
                normalized=str(key).lower().replace('-','_')
                if any(word in normalized for word in ('password','token','api_key','apikey','secret','credential','authorization','access_key')):
                    raise ValueError('Sensitive field; artifact not exposed')
                Reader.no_secrets(item)
        elif isinstance(value,list):
            for item in value: Reader.no_secrets(item)
    def table(self,key,page):
        path=self.artifact(key)
        if path.suffix=='.json': return {'json':read_json(path)}
        if path.suffix!='.csv': raise ValueError('Not a table')
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f); out=[]
            for i,row in enumerate(reader):
                if i>=page*100+100: break
                if i>=page*100: out.append(row)
            return {'columns':reader.fieldnames,'rows':out,'page':page}
