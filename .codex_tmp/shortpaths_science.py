import sys,os,json,hashlib,gc,traceback,tempfile,shutil,math,csv
from pathlib import Path
from unittest.mock import patch
ROOT=Path.cwd();AI=ROOT/'02_Hardware/04_code/03_ai_assistant';sys.path.insert(0,str(AI))
from low_memory_runtime import limit_native_threads
limit_native_threads()
from datetime import datetime
AUDIT=Path((ROOT/'.codex_tmp/shortpaths_science_directory.txt').read_text()) if '--resume-audit' in sys.argv else ROOT/'.codex_tmp'/('sp'+datetime.now().strftime('%H%M%S'))
AUDIT.mkdir(exist_ok='--resume-audit' in sys.argv)
(ROOT/'.codex_tmp/shortpaths_science_directory.txt').write_text(str(AUDIT))
(AUDIT/'02_Hardware/04_code/01_vqe').mkdir(parents=True,exist_ok=True)
for rel in ['data/EIS14.csv','01_quantum_eis_fit/04_code/vqe/hea_ansatz.py','01_quantum_eis_fit/04_code/vqe/parameter_mapping.py','01_quantum_eis_fit/04_code/vqe/impedance_model.py']:
    dest=AUDIT/rel;dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():shutil.copy2(ROOT/rel,dest)
os.environ['MXENE_SCRATCH_DIR']=str(AUDIT/'scratch');os.environ['MPLCONFIGDIR']=str(AUDIT/'mpl');os.environ['TEMP']=str(AUDIT);os.environ['TMP']=str(AUDIT)
tempfile.tempdir=str(AUDIT)
sys.dont_write_bytecode=True
BACKUP=Path((ROOT/'.codex_tmp/shortpaths_directory.txt').read_text())
from job_registry import JobRegistry
registry=JobRegistry(BACKUP/'registry.sqlite3',readonly=True)
from research_pipeline import _typed_target
from vqe_adapter import load_pilot
pilot=load_pilot(ROOT)
import repository_paths as paths
import targeted_recovery as recovery
import adaptive_targets as analysis
import scientific_exports as exports
status=registry.independent_campaign_status('vqe-independent-n-01')
refs={}
for row in registry.rows()+[r for l in status['levels'] for r in registry.replacement_jobs(l['target_id'])]:
    for obj in (row,row.get('metadata',{})):
        for key in ('raw_archive','per_pub_source','source_archive'):
            if obj.get(key+'_path') and obj.get(key+'_sha256'):refs[obj[key+'_path']]=obj[key+'_sha256']
bundle,lock=pilot.check_lock(ROOT/paths.MITIGATION_REL)
refs.update({str(bundle/name):digest for name,digest in lock['hashes'].items()})
def verify_sources():
    for name,expected in refs.items():
        with Path(name).open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=expected:raise RuntimeError('Protected source hash mismatch: '+name)
verify_sources()
def guard(event,args):
    if event=='socket.connect':raise RuntimeError('Production readiness analysis forbids network access')
    candidates=[]
    if event=='open':
        path,mode,flags=args
        if isinstance(path,(str,bytes,os.PathLike)) and ((mode and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            candidates=[path]
    elif event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.utime'):candidates=[args[0]]
    elif event in ('os.rename','os.link'):candidates=[args[0],args[1]]
    for item in candidates:
        if os.fsdecode(item).lower() in ('nul',r'\\.\nul'):continue
        p=Path(os.fsdecode(item)).resolve()
        if not p.is_relative_to(AUDIT):
            if event=='os.mkdir' and p.is_dir():continue
            raise RuntimeError('Analysis attempted write outside isolated output: '+str(p))
sys.addaudithook(guard)
real_contained=paths.contained;real_assemble=recovery.assemble_repaired_dataset;real_verify=recovery._verified_raw_source
output_prefix=(ROOT/paths.MITIGATION_REL/'output/02_run_level_data/adaptive_targets').resolve()
def contained(base,*parts):
    result=real_contained(base,*parts)
    if result.is_relative_to(output_prefix):return AUDIT/'analysis'/result.relative_to(output_prefix)
    return result
def scratch(prefix='t',root=None):
    import uuid
    folder=AUDIT/(prefix+uuid.uuid4().hex[:8]);folder.mkdir(mode=0o755);return folder
scratch_paths=[]
def cleanup(folder,root=None):
    folder=Path(folder).resolve()
    if folder.parent!=AUDIT:raise RuntimeError('Unexpected scratch cleanup target')
    scratch_paths.extend(str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file())
    shutil.rmtree(folder)
def assemble(root,target,reg):return real_assemble(AUDIT,target,reg)
def verify(root,*args,**kwargs):return real_verify(ROOT,*args,**kwargs)
results=[]
with patch.object(paths,'contained',side_effect=contained),patch.object(paths,'create_scratch_dir',side_effect=scratch),patch.object(paths,'cleanup_scratch_dir',side_effect=cleanup),patch.object(recovery,'assemble_repaired_dataset',side_effect=assemble),patch.object(recovery,'_verified_raw_source',side_effect=verify):
    for level in status['levels']:
        target=_typed_target(level,ROOT,registry)
        repaired=recovery.repaired_dataset_status(target,registry)
        if not repaired['analysis_eligible']:
            results.append({'n':target.target_n,'state':'NOT_COMPLETE','accepted':len(repaired['rows']),'expected':target.target_n*8});continue
        print('ANALYZE n='+str(target.target_n),flush=True)
        try:
            report=analysis.analyze_target(ROOT,target,registry)
            exported=exports.export_n_level_results(AUDIT,target,registry,report)
            assert report['artifacts_verified'] and exported['files_verified'] and len(exported['png_files'])==10
            manifest=json.loads((Path(report['output'])/'analysis_manifest.json').read_text())
            canonical=Path(report['output'])/manifest['canonical_combined_analysis']
            with (canonical/'per_repeat.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
            assert len(rows)==target.target_n*8
            assert all(math.isfinite(float(row[key])) for row in rows for key in ('SSE','RMSE'))
            for name in exported['png_files']:
                exports.inspect_png(Path(exported['figures'])/name,layout='square' if name in exports.PAPER_FIGURES else 'landscape')
            results.append({'n':target.target_n,'state':'PASS','analyzed_pubs':len(rows),'analysis':report,'export':exported})
            print('PASS n='+str(target.target_n)+' ten PNGs at 600 DPI',flush=True)
        except Exception as exc:
            results.append({'n':target.target_n,'state':'FAIL','error_type':type(exc).__name__,'error':str(exc)})
            traceback.print_exc()
        finally:
            if 'matplotlib.pyplot' in sys.modules:sys.modules['matplotlib.pyplot'].close('all')
            gc.collect()
        (AUDIT/'science-report.json').write_text(json.dumps({'results':results},indent=2))
verify_sources()
(AUDIT/'science-report.json').write_text(json.dumps({'results':results,'protected_files_verified':len(refs),'protected_files_unchanged':True,'network_forbidden':True,'production_writes_forbidden':True,'scratch_max_relative_length':max(map(len,scratch_paths),default=0),'scratch_canonical_max_relative_length':max((len(p) for p in scratch_paths if p.startswith('w')),default=0)},indent=2))
print('REPORT '+str(AUDIT/'science-report.json'),flush=True)
raise SystemExit(1 if any(r['state']=='FAIL' for r in results) else 0)
