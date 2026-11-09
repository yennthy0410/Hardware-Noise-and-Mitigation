"""Read-only campaign projection and artifact verification. No broker or provider calls."""
import json
import sqlite3
import struct
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path

from reader import OUT,DB,HERE,PROD,CAMPAIGN,Reader,read_json,sha,TREATMENTS

from figure_registry import REQUIRED_FIGURES,OPTIONAL_FIGURES,inventory as figure_inventory
PNG_NAMES=set(REQUIRED_FIGURES)


def step_records(state_dir=HERE/'state'):
    """Join durable grants to their reports; never initialize a control database."""
    records=[];db_path=state_dir/'worker_control.sqlite3'
    if not db_path.is_file():return records
    with closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        for identity,request in db.execute('SELECT id,request FROM requests ORDER BY rowid DESC'):
            if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):continue
            path=state_dir/f'step-{identity}.json'
            if not path.is_file():continue
            try:
                grant=json.loads(request);report=read_json(path)
                records.append({'request':identity,'campaign':grant['campaign'],'n':grant.get('n'),
                    'operation':grant['operation'],'status':report.get('control_state'),
                    'rows':report.get('rows',[]),'answer':report.get('answer'),
                    'updated':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()})
            except (ValueError,KeyError,OSError):continue
    return sorted(records,key=lambda r:r['updated'],reverse=True)


def png_contract(path):
    """Read PNG headers only. Scientific style is not inferred from pixels."""
    with path.open('rb') as f:
        if f.read(8)!=b'\x89PNG\r\n\x1a\n':return False
        size=None;dpi=None
        for _ in range(100):
            header=f.read(8)
            if len(header)!=8:break
            length,kind=struct.unpack('>I4s',header)
            if kind==b'IDAT':break
            if length>1024*1024:return False
            data=f.read(length);f.read(4)
            if kind==b'IHDR' and len(data)>=8:size=struct.unpack('>II',data[:8])
            if kind==b'pHYs' and len(data)==9:
                x,y,unit=struct.unpack('>IIB',data)
                if unit==1:dpi=(x*.0254,y*.0254)
        return bool(size and min(size)>=1000 and dpi and all(abs(x-600)<1 for x in dpi))


def recorded_step_observations(jobs,steps,campaign):
    """Project explicit provider reads from STEP receipts without updating the registry."""
    jobs=[dict(j) for j in jobs]
    for job in jobs:
        job.setdefault('local_status',job.get('status'))
        job.setdefault('local_observed_at',job.get('observed_at'))
    def timestamp(value):
        try:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00')) if value else datetime.min
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (ValueError,TypeError,AttributeError):return datetime.min.replace(tzinfo=timezone.utc)
    for step in steps:
        if step['campaign']!=campaign:continue
        for row in step['rows']:
            observations=row.get('provider_statuses') or ({row['job_id']:row['provider_status']} if row.get('job_id') and row.get('provider_status') else {})
            checked=row.get('provider_checked_at') or row.get('checked_at')
            if not isinstance(observations,dict) or not checked:continue
            for job in jobs:
                if job['target_n']==step['n'] and job.get('job_id') in observations:
                    if timestamp(checked)>timestamp(job.get('last_verified_provider_at')):
                        job.update(last_verified_provider_status=observations[job['job_id']],last_verified_provider_at=checked)
                    if timestamp(checked)>timestamp(job.get('observed_at')):
                        job.update(status=observations[job['job_id']],observed_at=checked,observation_source='Recorded STEP receipt')
    return jobs


def verify_outputs(out,campaign,level,accepted,steps):
    from step_artifacts import artifact_hashes as _step_artifact_hashes,payload_sha256 as _sha
    n=level['target_n'];target=level['target_id']
    result={'analyze':'No STEP receipt','export':'No STEP receipt','verified_pngs':0,'analysis_receipt':None,'figure_directory':None,'verification_reason':None}
    discovered=figure_inventory(out,n)
    best=max(discovered['versions'],key=lambda v:v['verified_pngs'],default=None)
    result.update(figure_inventory=discovered,verified_pngs=best['verified_pngs'] if best else 0,expected_pngs=len(best['expected_figures']) if best else len(PNG_NAMES))
    directory=out/'03_IBM_execution_records/ai_assistant/independent_n_campaigns'/campaign
    candidates=list((directory/'steps').glob(f'a{n}-*.json'))
    successes=[s for s in steps if s['campaign']==campaign and s['n']==n and s['status']=='STEP_COMPLETED']
    if candidates:result['analyze']='Verification required'
    verified=[]
    for path in candidates:
        try:
            receipt=read_json(path);source=Path(receipt['output']).resolve()
            parent=(out/'02_run_level_data/adaptive_targets').resolve()
            if source.parent!=parent or not (source.name==target or source.name.startswith(target+'_v')):raise ValueError('Analysis output is outside canonical target scope')
            expected={'campaign_id':campaign,'n':n,'target_id':target,'target_manifest_sha256':level['manifest_sha256'],
                'input_hashes':level['input_hashes'],'dataset_sha256':_sha(sorted(accepted,key=lambda r:(r['treatment_id'],r['logical_pub_index'])))}
            if any(receipt.get(k)!=v for k,v in expected.items()):raise ValueError('Analyze receipt provenance differs from current selected dataset')
            keys={(r['treatment_id'],r['logical_pub_index']) for r in accepted}
            if len(accepted)!=n*8 or keys!={(t,i) for t in TREATMENTS for i in range(1,n+1)}:raise ValueError('Analyze coverage is incomplete or conflicting')
            if _step_artifact_hashes(source)!=receipt['artifact_sha256']:raise ValueError('Analyze artifacts missing or changed')
            verified.append((path,source));result.update(analyze='Verified',analysis_receipt=str(path))
        except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:result['verification_reason']=str(exc)
    export_steps=[s for s in successes if s['operation']=='export' and any(r.get('state')=='EXPORTED' and r.get('files_verified') is True for r in s['rows'])]
    fig_base=out/f'01_results_and_figures/n={n}';data_base=out/f'02_run_level_data/n={n}'
    manifests=list(data_base.glob('export_manifest.json'))+list(data_base.glob('*/export_manifest.json'))
    if export_steps or manifests:result['export']='Verification required'
    for mp in manifests:
        try:
            m=read_json(mp);fig=fig_base if mp.parent==data_base else fig_base/mp.parent.name
            if not mp.resolve().is_relative_to(data_base.resolve()) or not fig.resolve().is_relative_to(fig_base.resolve()):raise ValueError('Export artifact directory escapes canonical n-level scope')
            if not export_steps:raise ValueError('No successful canonical Export STEP report for this n-level')
            if not verified:raise ValueError('Analyze receipt/artifacts do not verify')
            if m.get('target_id')!=target or m.get('n')!=n or m.get('input_hashes')!=level['input_hashes'] or m.get('partial_diagnostic_only') is not False:raise ValueError('Export provenance mismatch')
            sources=[(p,s) for p,s in verified if sha(s/'analysis_manifest.json')==m.get('source_analysis_manifest_sha256')]
            if not sources:raise ValueError('Export source does not match verified Analyze artifacts')
            # Bind export reports to this successful Analyze receipt, not just an n-level label.
            # Older canonical STEP reports recorded the analysis binding but omitted
            # export directories. Their paired manifests still verify the destination.
            if not any(any(r.get('state')=='EXPORTED' and r.get('files_verified') is True and r.get('analysis_receipt',{}).get('sha256')==sha(p) and r.get('figure_directory',str(fig))==str(fig) and r.get('data_directory',str(mp.parent))==str(mp.parent) for r in e['rows']) for e in export_steps for p,_ in sources):raise ValueError('Export report is not bound to verified Analyze receipt and destination')
            if read_json(fig/'export_manifest.json')!=m:raise ValueError('Export manifest pair conflicts')
            if m.get('figure_directory')!=str(fig) or m.get('data_directory')!=str(mp.parent):raise ValueError('Export manifest destination mismatch')
            expected=set(m.get('generated_figures') or m.get('figures',{}))
            if not PNG_NAMES.issubset(expected) or not expected.issubset(PNG_NAMES|set(OPTIONAL_FIGURES)) or set(m.get('figures',{}))!=expected or {p.name for p in fig.glob('*.png')}!=expected:raise ValueError('Publication PNG inventory differs from declared contract')
            if any(not (fig/name).is_file() or sha(fig/name)!=m['figures'][name]['sha256'] or not png_contract(fig/name) for name in expected):raise ValueError('PNG missing, changed or not 600 DPI')
            csv_hashes=m.get('csv_sha256',{})
            if not csv_hashes or any(Path(name).name!=name or not (mp.parent/name).is_file() or sha(mp.parent/name)!=digest for name,digest in csv_hashes.items()):raise ValueError('Export data files missing or changed')
            result.update(export='Verified',verified_pngs=len(expected),expected_pngs=len(expected),figure_directory=str(fig),verification_reason=None)
            break
        except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:result['verification_reason']=str(exc)
    return result


def group_batches(level,blocks,jobs,accepted,validation):
    accepted_keys={(r['treatment_id'],r['logical_pub_index']) for r in accepted}
    result=[]
    for block in blocks:
        members=[j for j in jobs if j['block']==block['logical_block_id']]
        start=(block['sequence_no']-1)*block['chunk_n']
        expected={(t,i) for t in TREATMENTS for i in range(start+1,start+block['chunk_n']+1)}
        valid=accepted_keys & expected
        bad={(v['treatment_id'],start+v['pub_index']) for v in validation if v['logical_block_id']==block['logical_block_id'] and (v['expectation_valid']==0 or v['uncertainty_valid']==0)}-valid
        originals=[j for j in members if j['recovery']!='Replacement'];replacements=[j for j in members if j['recovery']=='Replacement']
        complete=len(originals)==8 and {j['treatment_ids'][0] for j in originals}==set(TREATMENTS) and all((j.get('collected_pubs') or 0)==block['chunk_n'] for j in originals)
        any_collected=any((j.get('collected_pubs') or 0)>0 for j in members)
        all_done=len(originals)==8 and all(j.get('status') in ('DONE','COMPLETED') for j in originals)
        provider_verified=all_done and all(j.get('last_verified_provider_status') in ('DONE','COMPLETED') and j.get('last_verified_provider_at')==j.get('observed_at') for j in originals)
        message=('Collection recorded' if complete else 'IBM execution completed; collection required.' if provider_verified else 'IBM execution last recorded as completed; collection required.' if all_done else 'Collection required; IBM completion is not verified for all eight original jobs.')
        result.append({'block_id':block['logical_block_id'],'sequence':block['sequence_no'],'chunk_n':block['chunk_n'],'batch_id':block.get('batch_id'),
            'execution':dict(Counter(j.get('status') or 'UNKNOWN' for j in originals)),
            'local_execution':dict(Counter(j.get('local_status',j.get('status')) or 'UNKNOWN' for j in originals)),
            'last_verified_provider_execution':dict(Counter(j.get('last_verified_provider_status') or 'UNKNOWN' for j in originals)),
            'provider_verified_at':max((j['last_verified_provider_at'] for j in originals if j.get('last_verified_provider_at')),default=None),
            'local_observed_at':max((j.get('local_observed_at') or j.get('observed_at') for j in originals if j.get('local_observed_at') or j.get('observed_at')),default=None),
            'collection_message':message,'all_original_jobs_recorded_done':all_done,
            'original_jobs':len(originals),'replacement_jobs':len(replacements),'validated_pubs':len(valid),'expected_pubs':len(expected),
            'invalid_pubs':len(bad & expected),'missing_pubs':len(expected-valid-bad),
            'collection':'Collected' if complete else 'Partially collected' if any_collected else 'Uncollected',
            'recovery':'Required' if bad else 'Repaired' if any(r.get('source')=='REPLACEMENT' and (r['treatment_id'],r['logical_pub_index']) in expected for r in accepted) else 'Pending recovery' if any(j.get('accepted_pubs',0)==0 for j in replacements) else 'No unresolved invalid slots',
            'updated':max([block['updated_at']]+[j['updated_at'] for j in members]),'jobs':members})
    return result


def read_progress(database=DB,out=OUT,state_dir=HERE/'state',snapshot=None):
    # Existing recovery receipts contain repository-relative archive paths. The
    # established serve.py startup sets this cwd; never silently undercount them.
    if Path(database).resolve()==DB.resolve() and Path.cwd().resolve()!=HERE.parents[2].resolve():
        raise ValueError('Legacy archive provenance requires the project working directory. Start Dashboard with start_dashboard.ps1 (serve.py).')
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(PROD/'03_python_automation'))
    from job_registry import JobRegistry
    registry=JobRegistry(database,readonly=True)
    # Reuse the canonical accepted-slot and execution-lineage readers, not a new database.
    snapshot=dict(snapshot) if snapshot is not None else Reader().project(Path(database).resolve())
    limitations=['Legacy exports without a matching STEP receipt require verification.','Missing PUB coverage may include pending or uncollected jobs.','PNG style is not certified by hash/DPI checks.']
    try:steps=step_records(state_dir)
    except (sqlite3.Error,OSError,ValueError):
        steps=[];limitations.append('STEP receipt index is unavailable; scientific output verification is incomplete.')
    snapshot['execution_jobs']=recorded_step_observations(snapshot['execution_jobs'],steps,CAMPAIGN)
    levels=[];reconciliation=[]
    with registry._connection() as db:
        campaigns=[dict(r) for r in db.execute('SELECT campaign_id,state,updated_at FROM independent_n_campaigns')]
        for row in snapshot['levels']:
            target=registry.target_status(row['target_id']);accepted=registry.repaired_pub_values(row['target_id'])
            replacements=registry.replacement_jobs(row['target_id']) if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pub_replacement_jobs'").fetchone() else []
            for replacement in replacements:
                if replacement.get('job_id') and replacement['state'] in ('SUBMITTED','DONE','UNKNOWN','SUBMITTING'):
                    reconciliation.append({'n':row['target_n'],'job_id':replacement['job_id'],
                        'treatment':replacement['treatment_id'],'logical_pub':replacement['logical_pub_index'],
                        'local_state':replacement['state'],'updated_at':replacement['updated_at'],
                        'message':'Remote completion is unverified here. Inspect and retrieve this existing job; do not infer that another recovery is needed.'})
            frozen=dict(db.execute('SELECT manifest_sha256,input_hashes_json FROM experiment_targets WHERE target_id=?',(row['target_id'],)).fetchone())
            level=dict(row,manifest_sha256=frozen['manifest_sha256'],input_hashes=json.loads(frozen['input_hashes_json']))
            validation=registry.pub_validation_records(row['target_id'])
            batches=group_batches(level,target['blocks'],snapshot['execution_jobs'],accepted,validation)
            output=verify_outputs(out,CAMPAIGN,level,accepted,steps)
            unique={(r['treatment_id'],r['logical_pub_index']) for r in accepted if r['treatment_id'] in TREATMENTS and 1<=r['logical_pub_index']<=row['target_n']}
            level_jobs=[j for j in snapshot['execution_jobs'] if j['target_n']==row['target_n']]
            levels.append(dict(n=row['target_n'],target_id=row['target_id'],accepted=len(unique),expected=row['target_n']*8,
                execution=dict(Counter(j.get('status') or 'UNKNOWN' for j in level_jobs)),
                provider_observed_at=max((j['observed_at'] for j in level_jobs if j.get('observed_at')),default=None),
                invalid=row['invalid'],missing=row['missing'],batch_count=len(batches),collections=dict(Counter(b['collection'] for b in batches)),
                batches=batches,**output,complete=len(unique)==row['target_n']*8 and output['analyze']=='Verified' and output['export']=='Verified'))
    for n in (5,10,15,20,25,30):
        if not any(l['n']==n for l in levels):
            levels.append(dict(n=n,target_id=None,accepted=0,expected=n*8,execution={},provider_observed_at=None,
                invalid=0,missing=n*8,batch_count=0,collections={},batches=[],analyze='Verification required',export='Verification required',
                verified_pngs=0,complete=False,verification_reason='Canonical n-level registration is missing'))
    levels.sort(key=lambda l:l['n'])
    latest=next((s for s in steps if s['campaign']==CAMPAIGN and s['status']=='STEP_COMPLETED'),None)
    current=next(c for c in campaigns if c['campaign_id']==CAMPAIGN)
    return {'campaign_id':CAMPAIGN,'campaign_state':current['state'],'last_recorded_update':current['updated_at'],
        'accepted':sum(l['accepted'] for l in levels),'expected':sum(l['expected'] for l in levels),'levels':levels,
        'campaigns':campaigns,'last_successful_step':latest,'scientific_output_status':'Complete' if levels and all(l['complete'] for l in levels) else 'Pending / verification required',
        'captured_at':datetime.now(timezone.utc).isoformat(),'freshness':'Recorded local state; provider timestamps below are historical, never LIVE.',
        'provider_queried':False,'read_only':True,'limitations':limitations,'reconciliation':reconciliation}
