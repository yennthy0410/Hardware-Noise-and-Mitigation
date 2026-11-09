"""Read-only presentation contracts; no fitted values or statistics are recomputed."""
import csv,json,math
from datetime import datetime,timezone
from reader import HERE,OUT,sha,read_json
SOURCES={'nyquist':'nyquist_curves.csv','bode_magnitude':'bode_curves.csv','bode_phase':'bode_curves.csv','residual_real':'residual_curves.csv','residual_imag':'residual_curves.csv'}
SOURCES.update(residual_real_normalized='residual_curves.csv',residual_imag_normalized='residual_curves.csv')

def design():
    base=OUT/'03_IBM_execution_records/ai_assistant/independent_n_campaigns/vqe-independent-n-01'
    result=[]
    for n in (5,10,15,20,25,30):
        p=base/f'n{n}/experiment_manifest.json'
        if not p.exists():continue
        d=read_json(p);record={k:d.get(k) for k in ('target_n','backend','shots_per_pub','chunk_n','blocks_required','jobs_per_block','total_requested_pubs','input_hashes','source_run','physical_qubits')}
        record['treatments']=[{k:t.get(k) for k in ('id','dd','trex','zne','runtime_options')} for t in d.get('treatments',[])]
        record['source']=f'n{n}/experiment_manifest.json';record['source_sha256']=sha(p);result.append(record)
    return result
def chart(reader,key,kind):
    artifact=next((a for a in reader.status()['artifacts'] if a['id']==key),None)
    if not artifact or kind not in SOURCES or artifact['name']!=SOURCES[kind] or artifact['level'] not in (5,10,15,20,25,30):raise ValueError('No verified numerical source for this chart')
    path=reader.artifact(key)
    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if not rows or len(rows)>10000:raise ValueError('Unsupported numerical source size')
    prefixes=['Experimental','Ideal','M0_Baseline','M1_DD','M2_TREX','M3_ZNE','M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL'];series=[]
    for prefix in prefixes:
        x=prefix+'_Z_real' if kind=='nyquist' else 'frequency_Hz'
        base_kind=kind.removesuffix('_normalized')
        suffix={'nyquist':'_minus_Z_imag','bode_magnitude':'_abs_Z_ohm','bode_phase':'_phase_deg','residual_real':'_delta_Z_real_ohm','residual_imag':'_delta_Z_imag_ohm'}[base_kind]
        y=prefix+suffix
        if y not in rows[0] or x not in rows[0]:continue
        xs=[float(r[x]) for r in rows];ys=[float(r[y]) for r in rows]
        if kind.endswith('_normalized'):
            denominators=[float(r['Experimental_abs_Z_ohm']) for r in rows]
            if not all(math.isfinite(v) and v>=0 for v in denominators):raise ValueError('Invalid normalization')
            ys=[v/max(d,2.220446049250313e-16) for v,d in zip(ys,denominators)]
        if not all(math.isfinite(v) for v in xs+ys) or (kind!='nyquist' and any(v<=0 for v in xs)):raise ValueError('Invalid chart source values')
        series.append(dict(name=prefix,x=xs,y=ys,type='scatter',mode='lines'))
    if not series:raise ValueError('No supported columns')
    frequency=[float(r['frequency_Hz']) for r in rows] if 'frequency_Hz' in rows[0] else None
    if frequency is not None and not all(math.isfinite(v) and v>0 for v in frequency):raise ValueError('Invalid frequency')
    return dict(frequency=frequency,series=series,source=artifact,timestamp=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),x_label="Z′ (Ω)" if kind=='nyquist' else 'Frequency (Hz)',y_label=('Normalized residual (dimensionless)' if kind.endswith('_normalized') else {'nyquist':'−Z″ (Ω)','bode_magnitude':'|Z| (Ω)','bode_phase':'Phase (degrees)','residual_real':'Real residual (Ω)','residual_imag':'Imaginary residual (Ω)'}[kind]),x_scale='linear' if kind=='nyquist' else 'log',method=('Canonical component residual divided by max(measured magnitude, float64 epsilon); no refitting.' if kind.endswith('_normalized') else 'Exact exported curve values; no refitting, interpolation or bootstrap recomputation.'))
def circuit():
    source=OUT/'03_IBM_execution_records/locked_inputs';lock=read_json(source/'lock.json')
    for name,digest in lock['hashes'].items():
        if sha(source/name)!=digest:raise ValueError('Frozen input hash mismatch')
    with (source/'theta.csv').open(encoding='utf-8-sig',newline='') as f:theta=list(csv.DictReader(f))
    artifact=HERE/'state/frozen_circuit.json'
    result={'theta':theta,'observables':read_json(source/'observables.json'),'layout':lock['physical_layout_logical_order'],'hashes':lock['hashes'],'available':False}
    if artifact.exists():
        data=read_json(artifact)
        if data['hashes']!=lock['hashes'] or sha(HERE/'state/frozen_circuit.svg')!=data['svg_sha256']:raise ValueError('Circuit export provenance mismatch')
        result.update(data,available=True)
    return result

def classify_slot(accepted,originals,replacements,invalid=False):
    if accepted:return 'Recovered' if accepted.get('source')!='ORIGINAL' else 'Valid / no recovery needed'
    all_jobs=originals+replacements
    if any('UNKNOWN' in j['ledger_states'] or ('SUBMITTING' in j['ledger_states'] and not j['job_id']) for j in all_jobs):return 'UNKNOWN / reconciliation required'
    if any(j['status'] in ('QUEUED','RUNNING','INITIALIZING') or ('SUBMITTED' in j['ledger_states'] and j['status'] not in ('DONE','FAILED','CANCELLED','ERROR')) for j in replacements):return 'Replacement submitted / awaiting confirmation or result'
    if any(j['status'] in ('QUEUED','RUNNING','INITIALIZING') or ('SUBMITTED' in j['ledger_states'] and j['status'] not in ('DONE','FAILED','CANCELLED','ERROR')) for j in originals):return 'Awaiting original job'
    if invalid:return 'Confirmed invalid; submission eligibility requires manager checks'
    if any(j['status']=='DONE' for j in all_jobs):return 'Awaiting retrieval / validation'
    if any(j['status'] in ('FAILED','CANCELLED','ERROR') for j in all_jobs):return 'Terminal failure; recovery eligibility requires manager checks'
    return 'Not yet submitted / evidence unavailable'

def recovery_rows(levels,accepted,executions,blocks):
    from jobs_view import TREATMENTS,LABELS
    result=[]
    for level in levels:
        n=level['target_n'];target=level['target_id'];selected={(v['treatment_id'],v['logical_pub_index']):v for v in accepted.get(target,[])}
        for tid,label in zip(TREATMENTS,LABELS):
            slots=[]
            for index in range(1,n+1):
                originals=[];replacements=[];bad=False
                for j in executions:
                    if j['target_n']!=n or tid not in j['treatment_ids']:continue
                    matches=[]
                    for p in j['provenance']:
                        b=blocks.get(p['logical_block_id'])
                        if p['kind']=='replacement':match=p['logical_pub_index']==index
                        else:match=b is not None and (b['sequence_no']-1)*b['chunk_n']<index<=b['sequence_no']*b['chunk_n']
                        if match:matches.append(p)
                    if not matches:continue
                    (replacements if j['recovery']!='Original' else originals).append(j)
                    for v in j['validation']:
                        b=blocks.get(j['block']);logical=v.get('logical_pub_index') or ((b['sequence_no']-1)*b['chunk_n']+v['pub_index'] if b else None)
                        if logical==index and (v.get('expectation_valid')==0 or v.get('uncertainty_valid')==0):bad=True
                a=selected.get((tid,index));status=classify_slot(a,originals,replacements,bad)
                slots.append({'logical_pub_slot':index,'status':status,'accepted_job_id':a.get('job_id') if a else None,'accepted_source':a.get('source') if a else None,'accepted_sha256':a.get('source_sha256') if a else None,'execution_keys':[j['key'] for j in originals+replacements]})
            valid=sum(s['accepted_job_id'] is not None for s in slots);pending=sum(s['status'].startswith(('Awaiting','Replacement submitted')) for s in slots);invalid=sum(s['status'].startswith('Confirmed invalid') for s in slots)
            result.append(dict(n=n,treatment=label,expected=n,valid=valid,invalid=invalid,missing=None,pending=pending,recovery_needed=invalid+sum(s['status'].startswith('Terminal failure') for s in slots),replacement_needed=None,status='Complete' if valid==n else 'See logical-slot evidence; missing/eligibility not inferred',updated=level['updated_at'],slots=slots))
    return result
