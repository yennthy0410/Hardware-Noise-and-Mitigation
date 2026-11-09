#!/usr/bin/env python3
"""Study B / 1.1: retrospective QPU hardware noise characterization.

Reads saved EIS14 jobs and their calibration snapshots, never contacts IBM and
never submits jobs. Missing/ambiguous properties remain NaN; NO fabricated data.
"""
from __future__ import annotations
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

QPUS=('ibm_fez','ibm_kingston','ibm_marrakesh')
QPU_COLORS={'ibm_fez':'#277DA1','ibm_kingston':'#F28E2B','ibm_marrakesh':'#6B8E65'}
MODES=('fixed','unfixed')
GATE1={'id','x','sx','rz','rx','ry','u','u1','u2','u3','h'}
GATE2={'cx','cz','ecr','rzz','iswap','swap'}


def read_json(p):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError,ValueError,UnicodeError):return None

def num(v):
    if isinstance(v,dict):v=v.get('value',v.get('val',None))
    if isinstance(v,(float,int,np.number)):return float(v)
    try:return float(v)
    except (TypeError,ValueError):return np.nan

def jfirst(*v):return next((x for x in v if x is not None and x!=''),None)

def flatten_tree(obj):
    if isinstance(obj,dict):
        yield obj
        for v in obj.values():yield from flatten_tree(v)
    elif isinstance(obj,list):
        for v in obj:yield from flatten_tree(v)

def props_objects(calib):
    """Extract IBM BackendProperties-like dicts incl. nested wrappers."""
    for d in flatten_tree(calib):
        if isinstance(d.get('qubits'),list) or isinstance(d.get('gates'),list):yield d

def qubit_row(index, entry, origin, stamp, source):
    data={'backend':origin,'calibration_time_utc':stamp,'calibration_source':source,'physical_qubit':index}
    if isinstance(entry,list):
        for x in entry:
            if isinstance(x,dict) and x.get('name'):
                name=str(x['name']).lower()
                value=num(x)
                if name in ('t1','t2'):
                    unit=str(x.get('unit','s')).strip().lower()
                    factor={'s':1.0,'sec':1.0,'second':1.0,'seconds':1.0,'us':1e-6,'µs':1e-6,'μs':1e-6,'ns':1e-9}.get(unit)
                    value=value*factor if factor is not None else np.nan
                data[name]=value
    elif isinstance(entry,dict):
        for k,v in entry.items():
            if isinstance(v,(int,float,dict)):
                name=k.lower(); value=num(v)
                if name in ('t1','t2') and isinstance(v,dict):
                    unit=str(v.get('unit','s')).strip().lower()
                    factor={'s':1.0,'sec':1.0,'us':1e-6,'µs':1e-6,'μs':1e-6,'ns':1e-9}.get(unit)
                    value=value*factor if factor is not None else np.nan
                data[name]=value
    # T1/T2 from backend properties normally seconds. Store exactly per official units if declared;
    # arbitrary CSV columns t1_us/t2_us are converted in read_qubit_csv below.
    return data

def source_stamp(obj):
    if not isinstance(obj,dict):return None
    return jfirst(obj.get('last_update_date'),obj.get('calibration_time_utc'),obj.get('timestamp'),obj.get('date'))

def collect_json_calibration(path,backend):
    raw=read_json(path)
    qrows=[]; grows=[]
    if raw is None:return qrows,grows
    for obj in props_objects(raw):
        stamp=source_stamp(obj)
        if isinstance(obj.get('qubits'),list):
            for i,entry in enumerate(obj['qubits']):
                row=qubit_row(i,entry,backend,stamp,str(path))
                if len(row)>4:qrows.append(row)
        if isinstance(obj.get('gates'),list):
            for entry in obj['gates']:
                if not isinstance(entry,dict):continue
                gate=str(entry.get('gate',entry.get('name',''))).lower()
                ids=entry.get('qubits',entry.get('qargs',[]))
                if not isinstance(ids,list):continue
                vals={}
                for p in entry.get('parameters',[]):
                    if isinstance(p,dict):
                        key=str(p.get('name','')).lower();val=num(p)
                        if key in ('gate_length','duration'):
                            unit=str(p.get('unit','s')).lower()
                            factor={'s':1.0,'us':1e-6,'µs':1e-6,'ns':1e-9}.get(unit)
                            val=val*factor if factor is not None else np.nan
                        vals[key]=val
                vals.update({k:num(entry[k]) for k in ['gate_error','gate_length','duration','error'] if k in entry})
                grows.append(dict(backend=backend,calibration_time_utc=stamp,calibration_source=str(path),gate=gate,
                                  physical_qubits=','.join(map(str,ids)),arity=len(ids),
                                  error=jfirst(vals.get('gate_error'),vals.get('error')),
                                  duration_s=jfirst(vals.get('gate_length'),vals.get('duration'))))
    return qrows,grows

def read_calibration_csv(path,backend):
    try:d=pd.read_csv(path)
    except Exception:return [],[]
    cols={str(c).lower():c for c in d.columns}
    qcol=next((cols[k] for k in ['physical_qubit','qubit','qubit_index','index'] if k in cols),None)
    gcol=next((cols[k] for k in ['gate','gate_name','operation'] if k in cols),None)
    qrows=[];grows=[]
    for _,row in d.iterrows():
        stamp=jfirst(row.get(cols.get('calibration_time_utc','')),row.get(cols.get('timestamp','')))
        if isinstance(stamp,float) and np.isnan(stamp):stamp=None
        if qcol and pd.notna(row.get(qcol)):
            q=int(row[qcol]); item={'backend':backend,'physical_qubit':q,'calibration_source':str(path),'calibration_time_utc':stamp}
            for key in ['t1','t2','t1_us','t2_us','readout_error','prob_meas0_prep1','prob_meas1_prep0']:
                if key in cols and pd.notna(row[cols[key]]):
                    value=num(row[cols[key]])
                    # Legacy export mixed seconds and microseconds in columns labeled *_us.
                    # Explicitly infer seconds for values under 0.01, otherwise µs.
                    # Record assumption for manuscript audit.
                    if key.endswith('_us'):
                        inferred='seconds_in_mislabeled_us_column' if np.isfinite(value) and 0 < value < .01 else 'microseconds'
                        item[key+'_unit_interpretation']=inferred
                        value=value if inferred.startswith('seconds') else value*1e-6
                    item[key]=value
            if len(item)>4:qrows.append(item)
        if gcol and pd.notna(row.get(gcol)):
            ids=str(row.get(cols.get('physical_qubits',''),''))
            if not ids or ids=='nan':
                if qcol and pd.notna(row.get(qcol)):ids=str(int(row[qcol]))
            item=dict(backend=backend,calibration_source=str(path),calibration_time_utc=stamp,
                      gate=str(row[gcol]).lower(),physical_qubits=ids,arity=len(re.findall(r'\d+',ids)),
                      error=num(row.get(cols.get('gate_error',''),np.nan)),
                      duration_s=num(row.get(cols.get('gate_length',''),row.get(cols.get('duration_s',''),np.nan))))
            if 'duration_ns' in cols:item['duration_s']=num(row[cols['duration_ns']])*1e-9
            grows.append(item)
    return qrows,grows

def qubits_from_file(run):
    p=run/'selected_physical_qubits.csv'
    if p.is_file():
        try:
            d=pd.read_csv(p)
            for col in ['physical_qubit','physical','physical_index','physical_qubit_index','qubit']:
                if col in d:
                    vals=pd.to_numeric(d[col],errors='coerce').dropna().astype(int).tolist()
                    if vals:return set(vals)
        except Exception:pass
    # backup from transpiler_info (some JSON variants)
    j=read_json(run/'transpiler_info.json') or {}
    for k in ['physical_qubits','selected_physical_qubits','final_layout']:
        v=j.get(k)
        if isinstance(v,list):
            vals=[int(x) for x in v if isinstance(x,(int,float))]
            if vals:return set(vals)
    return set()

def active_couplers(run):
    p=run/'twoq_operations.csv'
    out=set()
    if not p.is_file():return out
    try:d=pd.read_csv(p)
    except Exception:return out
    for _,r in d.iterrows():
        vals=[]
        for k in ['physical_qubits','qargs','qubits','coupler','edge']:
            if k in d:
                s=str(r[k]);q=[int(i) for i in re.findall(r'\d+',s)]
                if len(q)==2:vals=q;break
        if not vals:
            for pair in [('physical_qubit_0','physical_qubit_1'),('control','target'),('physical_control','physical_target')]:
                if all(k in d for k in pair) and all(pd.notna(r[k]) for k in pair):vals=[int(r[k]) for k in pair];break
        if len(vals)==2:out.add(tuple(vals))
    return out

def job_identity(run):
    out={}
    for n in ['job_retrieved_metadata.json','job_submitted.json','job_info.json']:
        j=read_json(run/n)
        if not isinstance(j,dict):continue
        out['job_id']=jfirst(out.get('job_id'),j.get('job_id'),j.get('id'),(j.get('Details') or {}).get('Job ID'))
        out['submitted_at_utc']=jfirst(out.get('submitted_at_utc'),j.get('job_created_or_submitted_at_utc'),j.get('submitted_at_utc'),(j.get('Status and usage') or {}).get('Created'))
    return out

def valid_mean(v):
    a=pd.to_numeric(pd.Series(list(v)),errors='coerce').to_numpy(float)
    return float(np.mean(a[np.isfinite(a)])) if np.isfinite(a).any() else np.nan

def valid_median(v):
    a=pd.to_numeric(pd.Series(list(v)),errors='coerce').to_numpy(float)
    return float(np.median(a[np.isfinite(a)])) if np.isfinite(a).any() else np.nan

def dedup(df,keys):
    if df.empty:return df
    # Key + source snapshot: preserve genuine time variability but collapse repeated identical records.
    return df.drop_duplicates(subset=[k for k in keys if k in df],keep='first').copy()

def fig_save(fig,p):
    """Render to short temporary filename to avoid Windows/OneDrive image path issues."""
    import shutil
    from tempfile import TemporaryDirectory
    from repository_paths import scratch_root
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='qn_',dir=scratch_root()) as folder:
        temp=Path(folder)/'chart.png'
        fig.savefig(temp,dpi=230,bbox_inches='tight',facecolor='white',pad_inches=.22)
        shutil.copyfile(temp,p)
    plt.close(fig)


def plot_metric(ax,df,measure,title,ylabel,scale=1):
    values=[]
    for backend in QPUS:
        subset=df.loc[df.backend==backend,measure] if measure in df.columns else pd.Series(dtype=float)
        numbers=pd.to_numeric(subset,errors='coerce').dropna()
        values.append(float(numbers.median()*scale) if len(numbers) else np.nan)
    xs=np.arange(len(QPUS))
    finite=[v for v in values if np.isfinite(v)]
    ceiling=max(finite) if finite else 1.0
    ceiling=ceiling if ceiling>0 else 1.0
    ax.set_ylim(0,ceiling*1.32)
    for k,(backend,value) in enumerate(zip(QPUS,values)):
        if np.isfinite(value):
            ax.bar(k,value,width=.63,color=QPU_COLORS[backend],edgecolor='white',linewidth=1.0,zorder=3)
            label=f'{value:.4g}'
            ax.annotate(label,(k,value),xytext=(0,7),textcoords='offset points',ha='center',fontsize=10,fontweight='semibold')
        else:
            ax.text(k,ceiling*.13,'N/A',ha='center',color='#6b7280',fontsize=10)
    ax.set_xticks(xs,[b.removeprefix('ibm_').capitalize() for b in QPUS],fontsize=10)
    ax.set_ylabel(ylabel,fontsize=10,labelpad=8)
    ax.set_title(title,fontsize=12,fontweight='semibold',pad=13)
    ax.spines[['top','right']].set_visible(False)
    ax.spines['left'].set_color('#ccd3dd');ax.spines['bottom'].set_color('#ccd3dd')
    ax.grid(axis='y',color='#e9edf2',linewidth=.8,zorder=0)
    ax.set_axisbelow(True)


def apply_style():
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.titlesize':12,
                         'figure.facecolor':'white','axes.facecolor':'white',
                         'savefig.facecolor':'white','axes.labelcolor':'#25334a',
                         'text.color':'#1b2c46','xtick.color':'#475569','ytick.color':'#475569'})


def create_benchmark_figure(benchmark,path):
    """Complete in-frame table with the metric as a real first column."""
    rows=[
        ('Coverage','Physical qubits','calibrated_qubit_count','integer'),
        ('Coverage','Calibration sources','calibration_sources','integer'),
        ('Readout','Median readout error','median_readout_error_pct','pct'),
        ('Readout','Median readout asymmetry','median_readout_asymmetry_pct','pct'),
        ('Coherence','Median T1','median_T1_us','us'),
        ('Coherence','Median T2','median_T2_us','us'),
        ('Native gates','Median single-qubit gate error','median_1Q_gate_error_pct','pct'),
        ('Native gates','Median two-qubit gate error','median_2Q_gate_error_pct','pct'),
        ('Gate timing','Median coherence-to-1Q-gate ratio','median_T2_over_1Q_gate','ratio'),
        ('Gate timing','Median coherence-to-2Q-gate ratio','median_T2_over_2Q_gate','ratio'),
        ('Circuit timing','Median circuit exposure relative to T2','median_circuit_over_T2','ratio'),
        ('Crosstalk','Characterization status','crosstalk_status','status'),
    ]
    headers=['Hardware metric','ibm_fez','ibm_kingston','ibm_marrakesh','Unit']
    lookup={r['backend']:r for r in benchmark.to_dict('records')}
    unit={'integer':'count','pct':'%','us':'µs','ratio':'dimensionless','status':'—'}
    def format_item(v,kind):
        if v is None or (isinstance(v,(float,int,np.number)) and not np.isfinite(v)):return 'N/A'
        if kind=='status':return 'Not characterized' if 'not_characterized' in str(v) else str(v).replace('_',' ')
        if kind=='integer':return str(int(v))
        if kind=='pct':return f'{float(v):.4f}'.rstrip('0').rstrip('.')
        if kind=='us':return f'{float(v):.2f}'
        if kind=='ratio':return f'{float(v):,.3g}'
        return str(v)
    table_data=[]
    for group,name,key,kind in rows:
        table_data.append([name]+[format_item(lookup.get(b,{}).get(key),kind) for b in QPUS]+[unit[kind]])
    fig,ax=plt.subplots(figsize=(16,8.5));ax.axis('off')
    fig.suptitle('Cross-QPU Hardware Noise Characterization',x=.5,y=.96,fontsize=19,fontweight='bold')
    fig.text(.5,.909,'Calibration-derived statistics across the HEA physical-qubit footprint',
             ha='center',fontsize=11,color='#64748b')
    table=ax.table(cellText=table_data,colLabels=headers,cellLoc='center',colLoc='center',
                   colWidths=[.36,.185,.185,.185,.085],bbox=[.012,.095,.976,.775])
    table.auto_set_font_size(False);table.set_fontsize(10.4)
    group_starts={0,2,4,6,8,10,11}
    for (r,c),cell in table.get_celld().items():
        cell.set_linewidth(.45);cell.set_edgecolor('#d6dfeb')
        cell.set_text_props(color='#263648',va='center')
        if r==0:
            cell.set_facecolor('#eaf0f8');cell.set_text_props(weight='bold',fontsize=11,color='#1d3251')
            if c in (1,2,3):cell.set_text_props(color=QPU_COLORS[QPUS[c-1]],weight='bold',fontsize=11)
        else:
            cell.set_facecolor('#f5f8fc' if r%2==0 else 'white')
            if r-1 in group_starts:
                cell.set_linewidth(.8);cell.set_edgecolor('#aabbd1')
            if c==0:cell.set_text_props(ha='left',weight='medium',fontsize=10.0)
            if c==4:cell.set_text_props(color='#64748b',fontsize=9)
    fig.text(.025,.052,'N/A indicates missing or insufficient calibration timing data. Crosstalk requires independent diagnostics.',
             fontsize=9.5,color='#687487')
    fig_save(fig,path)

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--qpu-root',type=Path,help='Folder containing ibm_fez/EIS14/{fixed,unfixed}/run_XX')
    a.add_argument('--out',type=Path,help='Output folder (default results/EIS14/04_noise_characterization)')
    a.add_argument('--modes',nargs='+',choices=MODES,default=list(MODES))
    args=a.parse_args()
    repo=None
    for p in [Path.cwd().resolve(),*Path.cwd().resolve().parents,Path(__file__).resolve().parent,*Path(__file__).resolve().parents]:
        if (p/'experiments'/'HEA_VQE'/'01_simulator_to_qpu_hardware').is_dir():repo=p;break
    if args.qpu_root:qroot=args.qpu_root.resolve()
    elif repo:qroot=repo/'experiments'/'HEA_VQE'/'01_simulator_to_qpu_hardware'/'results'/'EIS14'/'01_run_qpu'
    else:raise SystemExit('Cannot find repo. Pass --qpu-root.')
    output=(args.out.resolve() if args.out else qroot.parent/'04_noise_characterization')
    if not qroot.exists():raise SystemExit(f'QPU input directory missing: {qroot}')
    output.mkdir(parents=True,exist_ok=True)
    qs=[];gs=[];runs=[];sources=defaultdict(int)
    for backend in QPUS:
        for mode in args.modes:
            for idx in range(1,6):
                run=qroot/backend/'EIS14'/mode/f'run_{idx:02d}'
                if not run.exists():continue
                used=qubits_from_file(run);couplers=active_couplers(run)
                ident=job_identity(run)
                runs.append(dict(backend=backend,mode=mode,run=idx,job_id=ident.get('job_id'),
                                 submitted_at_utc=ident.get('submitted_at_utc'),
                                 physical_qubits=','.join(map(str,sorted(used))),
                                 twoq_couplers=';'.join(f'{x}-{y}' for x,y in sorted(couplers)),
                                 qubit_mapping_available=bool(used)))
                calib=run/'calibrations'
                files=list(calib.rglob('*')) if calib.exists() else []
                files=[p for p in files if p.is_file() and p.suffix.lower() in {'.csv','.json'}]
                for p in files:
                    if p.suffix.lower()=='.json':qq,gg=collect_json_calibration(p,backend)
                    else:qq,gg=read_calibration_csv(p,backend)
                    sources[str(p)]+=len(qq)+len(gg)
                    for row in qq:
                        if used and row['physical_qubit'] not in used:continue
                        qs.append(dict(row,mode=mode,run=idx,job_id=ident.get('job_id'),in_hea_layout=bool(used)))
                    for row in gg:
                        ids=[int(x) for x in re.findall(r'\d+',str(row['physical_qubits']))]
                        if not ids:continue
                        # For 2Q require gate used in circuit when coupler data exist, else both in layout.
                        if used and not set(ids).issubset(used):continue
                        if len(ids)==2 and couplers and tuple(ids) not in couplers and tuple(reversed(ids)) not in couplers:continue
                        gs.append(dict(row,mode=mode,run=idx,job_id=ident.get('job_id'),in_hea_layout=bool(used)))
    run_df=pd.DataFrame(runs)
    if run_df.empty:raise SystemExit(f'No existing run_01..05 folders under {qroot}')
    qdf=pd.DataFrame(qs);gdf=pd.DataFrame(gs)
    # Avoid treating repeated copies of identical calibration values as statistically independent observations.
    qdf=dedup(qdf,['backend','physical_qubit','calibration_time_utc','t1','t2','readout_error','prob_meas0_prep1','prob_meas1_prep0'])
    gdf=dedup(gdf,['backend','gate','physical_qubits','calibration_time_utc','error','duration_s'])
    for c in ['t1','t2','readout_error','prob_meas0_prep1','prob_meas1_prep0']:
        if c not in qdf:qdf[c]=np.nan
        qdf[c]=pd.to_numeric(qdf[c],errors='coerce')
    for c in ['error','duration_s']:
        if c not in gdf:gdf[c]=np.nan
        gdf[c]=pd.to_numeric(gdf[c],errors='coerce')
    for col in ['backend','physical_qubit','calibration_source','calibration_time_utc']:
        if col not in qdf:qdf[col]=pd.Series(dtype='object')
    for col in ['backend','physical_qubits','calibration_source','calibration_time_utc']:
        if col not in gdf:gdf[col]=pd.Series(dtype='object')
    if 'arity' not in gdf:gdf['arity']=np.nan
    if 'gate' not in gdf:gdf['gate']=''
    # Derive readout error only when both confusion rates are provided.
    mask=qdf['readout_error'].isna() & qdf['prob_meas0_prep1'].notna() & qdf['prob_meas1_prep0'].notna()
    qdf.loc[mask,'readout_error']=(qdf.loc[mask,'prob_meas0_prep1']+qdf.loc[mask,'prob_meas1_prep0'])/2
    # Duration sanity: IBM BackendProperties gate_length values are seconds; don't silently infer units.
    for col in ['t1','t2']:
        qdf.loc[qdf[col]<=0,col]=np.nan
    gdf.loc[gdf['duration_s']<=0,'duration_s']=np.nan
    # Merged calibration exports can contain either 't1' in seconds or 't1_us' already
    # normalized by read_calibration_csv into seconds. Never pool unconverted units.
    for kind in ('t1','t2'):
        us_col=kind+'_us'
        if us_col in qdf:
            candidates=pd.to_numeric(qdf[us_col],errors='coerce')
            qdf[kind]=qdf[kind].where(qdf[kind].notna(),candidates)
    qdf['T1_us']=qdf['t1']*1e6;qdf['T2_us']=qdf['t2']*1e6
    # Physical coherence times above 10 ms are suspicious for superconducting
    # transmon snapshots; exclude from figures, retain raw values for auditing.
    for kind in ('t1','t2'):
        bad=qdf[kind].notna() & ((qdf[kind]<1e-8)|(qdf[kind]>0.01))
        if bad.any():
            print(f'[WARNING] {int(bad.sum())} implausible {kind.upper()} values excluded; check calibration units.',file=sys.stderr)
            qdf.loc[bad,kind]=np.nan
        qdf['T'+kind[1:]+'_us']=qdf[kind]*1e6
    qdf['readout_error_percent']=qdf['readout_error']*100
    gdf['gate_error_percent']=gdf['error']*100
    gdf['gate_duration_ns']=gdf['duration_s']*1e9
    # Gate-wise ratio: conservative min(T2) for 2Q gate, T2 for 1Q. Pairing is within QPU snapshot only.
    gdf['coherence_to_gate_ratio']=np.nan
    for i,r in gdf.iterrows():
        ids=[int(x) for x in re.findall(r'\d+',str(r.get('physical_qubits','')))]
        if not ids or not np.isfinite(r['duration_s']) or r['duration_s']<=0:continue
        eligible=qdf[(qdf.backend==r.backend)&qdf.physical_qubit.isin(ids)]
        if 'calibration_time_utc' in qdf and pd.notna(r.get('calibration_time_utc')):
            matched=eligible[eligible.calibration_time_utc==r['calibration_time_utc']]
            if not matched.empty:eligible=matched
        t2=[valid_median(eligible.loc[eligible.physical_qubit==j,'t2']) for j in ids]
        if len(t2)==len(ids) and all(np.isfinite(v) for v in t2):gdf.at[i,'coherence_to_gate_ratio']=min(t2)/r['duration_s']
    # Circuit exposure from previously stored, provenance-labelled circuit estimates.
    # Not job QPU usage or job execution time.
    exposures=[]
    for rr in runs:
        backend,mode,idx=rr['backend'],rr['mode'],rr['run']
        run=qroot/backend/'EIS14'/mode/f'run_{idx:02d}'
        meta=read_json(run/'transpiler_info.json') or {}
        timing=read_json(run/'circuit_timing.json') or {}
        saved_exposure=read_json(run/'circuit_coherence_summary.json') or {}
        # Accepted ONLY if fields say explicitly that these are scheduled time in seconds/ns.
        duration=np.nan; source='not_available'
        for k,scale in [('scheduled_circuit_duration_s',1),('scheduled_duration_s',1),('scheduled_circuit_duration_ns',1e-9),('scheduled_duration_ns',1e-9)]:
            if np.isfinite(num(meta.get(k))) and num(meta[k])>0:
                duration=num(meta[k])*scale;source=f'transpiler_info.json:{k}';break
        if np.isfinite(num(timing.get('circuit_duration_s'))) and num(timing.get('circuit_duration_s')) > 0:
            duration=num(timing['circuit_duration_s'])
            source='circuit_timing.json:'+str(timing.get('timing_source','Target'))
        used=[int(x) for x in re.findall(r'\d+',rr['physical_qubits'])]
        relevant=qdf[(qdf.backend==backend)&qdf.physical_qubit.isin(used)]
        t1=valid_median(relevant['t1']);t2=valid_median(relevant['t2'])
        # Prefer per-run coherent T2 matching when available, avoiding pooled dates.
        run_ratio=num(saved_exposure.get('median_circuit_over_T2'))
        exposures.append(dict(backend=backend,mode=mode,run=idx,job_id=rr['job_id'],
                              scheduled_circuit_duration_s=duration,duration_source=source,
                              median_T1_s=t1,median_T2_s=t2,
                              circuit_over_T1=duration/t1 if np.isfinite(duration) and np.isfinite(t1) and t1>0 else np.nan,
                              circuit_over_T2=run_ratio if np.isfinite(num(run_ratio)) else (duration/t2 if np.isfinite(duration) and np.isfinite(t2) and t2>0 else np.nan)))
    expdf=pd.DataFrame(exposures)
    # Traceability outputs (not additional plots).
    run_df.to_csv(output/'00_runs_inventory.csv',index=False)
    qdf.to_csv(output/'01_qubit_calibration_raw.csv',index=False)
    gdf.to_csv(output/'01_gate_calibration_raw.csv',index=False)
    expdf.to_csv(output/'01_circuit_coherence_exposure.csv',index=False)
    # One color per QPU consistently across every comparison panel.
    apply_style()
    qdf['readout_asymmetry_percent']=abs(qdf['prob_meas0_prep1']-qdf['prob_meas1_prep0'])*100
    fig,axs=plt.subplots(1,2,figsize=(13.4,5),layout='constrained')
    plot_metric(axs[0],qdf,'readout_error_percent','Readout Assignment Error','Median error, %')
    plot_metric(axs[1],qdf,'readout_asymmetry_percent','Readout Asymmetry','Median asymmetry, %')
    fig.suptitle('Measurement Noise Characterization',fontsize=17,fontweight='bold')
    fig_save(fig,output/'02_measurement_error_comparison.png')

    fig,axs=plt.subplots(1,3,figsize=(17,5.2),layout='constrained')
    plot_metric(axs[0],qdf,'T1_us','Energy Relaxation','Median T1, µs')
    plot_metric(axs[1],qdf,'T2_us','Transverse Coherence','Median T2, µs')
    plot_metric(axs[2],expdf,'circuit_over_T2','Circuit Coherence Exposure','Median duration-to-T2 ratio')
    fig.suptitle('Decoherence and Circuit Exposure',fontsize=17,fontweight='bold')
    fig_save(fig,output/'03_decoherence_comparison.png')

    fig,axs=plt.subplots(2,2,figsize=(14.8,10),layout='constrained')
    one=gdf[gdf.arity==1];two=gdf[gdf.arity==2]
    plot_metric(axs[0,0],one,'gate_error_percent','Single-Qubit Gate Error','Median calibration error, %')
    plot_metric(axs[0,1],two,'gate_error_percent','Two-Qubit Gate Error','Median calibration error, %')
    plot_metric(axs[1,0],one,'coherence_to_gate_ratio','Single-Qubit Coherence-to-Gate Ratio','Median T2-to-gate-duration ratio')
    plot_metric(axs[1,1],two,'coherence_to_gate_ratio','Two-Qubit Coherence-to-Gate Ratio','Median T2-to-gate-duration ratio')
    fig.suptitle('Native Gate Fidelity and Coherence Budget',fontsize=17,fontweight='bold')
    fig_save(fig,output/'04_gate_error_comparison.png')

    bench=[]
    for backend in QPUS:
        q=qdf[qdf.backend==backend];g=gdf[gdf.backend==backend];e=expdf[expdf.backend==backend]
        bench.append(dict(backend=backend,calibrated_qubit_count=q.physical_qubit.nunique() if not q.empty else 0,
                          calibration_sources=len(set(q.calibration_source.dropna())|set(g.calibration_source.dropna())),
                          median_readout_error_pct=valid_median(q.readout_error_percent),
                          median_readout_asymmetry_pct=valid_median(q.readout_asymmetry_percent),
                          median_T1_us=valid_median(q.T1_us),median_T2_us=valid_median(q.T2_us),
                          median_1Q_gate_error_pct=valid_median(g.loc[g.arity==1,'gate_error_percent']),
                          median_2Q_gate_error_pct=valid_median(g.loc[g.arity==2,'gate_error_percent']),
                          median_T2_over_1Q_gate=valid_median(g.loc[g.arity==1,'coherence_to_gate_ratio']),
                          median_T2_over_2Q_gate=valid_median(g.loc[g.arity==2,'coherence_to_gate_ratio']),
                          median_circuit_over_T2=valid_median(e.circuit_over_T2),
                          crosstalk_status='not_characterized_from_calibrations'))
    bdf=pd.DataFrame(bench);bdf.to_csv(output/'05_cross_QPU_noise_benchmark.csv',index=False)
    create_benchmark_figure(bdf,output/'05_cross_QPU_noise_benchmark.png')
    inventory=run_df[['backend','mode','run','job_id','physical_qubits','twoq_couplers']].copy()
    inventory['crosstalk_measured']=False
    inventory['note']='No isolated-versus-simultaneous crosstalk diagnostic in saved calibration; not characterized'
    inventory.to_csv(output/'06_crosstalk_evidence_inventory.csv',index=False)
    report={'input_folder':str(qroot),'output_folder':str(output),'runs_discovered':len(runs),
            'qubit_calibration_rows':len(qdf),'gate_calibration_rows':len(gdf),
            'calibration_files_with_properties':sum(v>0 for v in sources.values()),
            'scheduled_circuit_durations_available':int(expdf.scheduled_circuit_duration_s.notna().sum()),
            'limitations':['Calibration snapshots may differ from job-time conditions: inspect timestamps.',
                           'Median ratios are descriptive; no noise attribution to EIS is performed.',
                           'Crosstalk not measured without paired diagnostic circuits.',
                           'Circuit exposure uses saved Target-based circuit duration estimates if present; not guaranteed job-time scheduling.',
                           'For apples-to-apples comparison, compare common gate types and snapshot conditions.']}
    (output/'README_results.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report,indent=2))
    if not len(qdf) and not len(gdf):print('[WARNING] Saved run calibrations were not parsed. Figures show N/A; inspect calibrations/ structure and 01_* CSVs.',file=sys.stderr)

if __name__=='__main__':main()
