"""Read-only canonical comparison adapter and explicitly gated offline publisher.

Never collects, fits, repairs a PUB, or advances a campaign. Exported PUB_index
is source-local: only the verified analysis source map supplies logical slots.
"""
from __future__ import annotations
import csv
import json
import math
import os
from pathlib import Path
import statistics
import sys

AI = Path(__file__).resolve().parents[1]
if str(AI) not in sys.path:
    sys.path.append(str(AI))
from low_memory_runtime import limit_native_threads
limit_native_threads()

from scientific_exports import (_sha, _read_csv, _export_pair_matches, inspect_png,
                                CSV_FILES, PAPER_FIGURES, WIDE_FIGURES, OPTIONAL_FIGURES)
from repository_paths import repository_root, MITIGATION_REL, contained
from step_artifacts import artifact_hashes

LEVELS = (5, 10, 15, 20, 25, 30)
TREATMENTS = ('M0_Baseline','M1_DD','M2_TREX','M3_ZNE','M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_hash(path, expected):
    path = Path(path)
    actual = _sha(path) if path.is_file() else None
    require(actual == expected and expected is not None,
            f'SHA-256 verification failed: {path}; expected={expected}; actual={actual or "MISSING"}')
    return actual


def verify_snapshot(hashes):
    for path, expected in hashes.items():
        check_hash(path, expected)


def verify_selected_archives(root, target, registry, selected):
    """Reuse canonical raw-source verification; bind every selected value to bytes."""
    from targeted_recovery import _verified_raw_source
    from batch_progression import replacement_provenance_verified
    jobs={(j['job_id'],j['treatment_id']):j for j in registry.rows() if j.get('target_id')==target.target_id}
    replacements={r['replacement_id']:r for r in registry.replacement_jobs(target.target_id)}
    hashes={};cache={}
    for row in selected:
        if row['source']=='REPLACEMENT':
            replacement=replacements.get(row['replacement_id'])
            require(replacement and replacement['state']=='VALIDATED','Selected recovery is not VALIDATED')
            # The canonical verifier expects SQLite JSON columns, retained by the reader.
            if 'metadata_json' not in replacement:
                replacement=dict(replacement,metadata_json=json.dumps(replacement['metadata']),
                                 options_json=json.dumps(replacement['options']))
            require(replacement_provenance_verified(replacement,row,target.input_hashes),
                    f'Recovery archive provenance failed: {row["replacement_id"]}')
            p=Path(replacement['raw_archive_path']).resolve();hashes[str(p)]=_sha(p)
            payload=read_json(p)
            reference=payload.get('provider_result_archive')
            if reference:hashes[str(Path(reference['path']).resolve())]=reference['sha256']
            continue
        key=(row['job_id'],row['treatment_id'])
        require(key in jobs,'Accepted original job missing')
        if key not in cache:
            p,digest,payload,_,_=_verified_raw_source(root,jobs[key],registry,
                expected_pubs=target.chunk_n,expected_shots=target.shots_per_pub,
                expected_hashes=target.input_hashes,allow_missing=True)
            hashes[str(p.resolve())]=digest
            sidecar=p.with_name(p.stem+'.structured.json')
            if sidecar.is_file():hashes[str(sidecar.resolve())]=_sha(sidecar)
            # Retain only seven values/stds per PUB, not large provider metadata.
            pubs=payload.get('pub_results',payload.get('records',payload.get('pubs')))
            payload={'records':[{'expectation_values':pub.get('expectation_values',pub.get('evs',(pub.get('data_fields') or {}).get('evs'))),
                                 'standard_deviations':pub.get('standard_deviations',pub.get('stds',(pub.get('data_fields') or {}).get('stds')))}
                                for pub in pubs]}
            del pubs
            cache[key]=(p,digest,payload,sidecar)
        p,digest,payload,sidecar=cache[key]
        if row['source_sha256']!=digest:
            require(sidecar.is_file() and _sha(sidecar)==row['source_sha256'],
                    'Accepted source digest differs from both raw archive and sidecar')
            payload=read_json(sidecar)
            require(payload.get('source_raw_sha256')==digest,'Sidecar raw hash mismatch')
        records=payload.get('pub_results',payload.get('records',payload.get('pubs')))
        index=(row['logical_pub_index']-1)%target.chunk_n
        require(isinstance(records,list) and index<len(records),'Selected raw PUB missing')
        record=records[index];fields=record.get('data_fields',{}) or {}
        ev=record.get('expectation_values',record.get('evs',fields.get('evs')))
        sd=record.get('standard_deviations',record.get('stds',fields.get('stds')))
        require(ev==row['expectation_values'] and sd==row['standard_deviations'],
                f'Accepted values differ from immutable archive: {key} PUB {index+1}')
    return hashes


def unique_index(rows, key, label):
    result = {}
    for row in rows:
        identity = key(row)
        require(identity not in result, f'Duplicate {label}: {identity}')
        result[identity] = row
    return result


def verified_export_pair(figures, data, source_hash):
    """Current export contract, plus the recorded schema-3 optional-cost variant.

    The current writer deliberately emits ten PNGs. Earlier schema-3 exports
    could contain an eleventh cost PNG; every recorded artifact remains required.
    This reader never changes the writer's output inventory policy.
    """
    if _export_pair_matches(figures,data,source_hash,require_pair_manifest=True):return True
    try:
        em=read_json(data/'export_manifest.json')
        expected=set(PAPER_FIGURES+WIDE_FIGURES+OPTIONAL_FIGURES)
        if (em.get('schema_version')!=3 or em.get('source_analysis_manifest_sha256')!=source_hash or
                set(em.get('generated_figures',[]))!=expected or set(em.get('figures',{}))!=expected or
                set(em.get('csv_sha256',{}))!=set(CSV_FILES) or
                em.get('qpu_usage_coverage',{}).get('complete_comparable') is not True or
                em.get('omitted_optional_figures')!=[] or
                em.get('figure_directory')!=str(figures) or em.get('data_directory')!=str(data) or
                read_json(figures/'export_manifest.json')!=em):return False
        for name,digest in em['csv_sha256'].items():check_hash(contained(data,name),digest)
        for name,item in em['figures'].items():
            check_hash(contained(figures,name),item['sha256'])
            inspect_png(figures/name,layout='square' if name in PAPER_FIGURES else 'landscape')
        return True
    except (ValueError,OSError,KeyError,TypeError):return False


def join_observations(target, selected, source_map, repeats, canonical):
    """One-to-one immutable job/source-PUB join; never infer a logical index."""
    slots = unique_index(selected, lambda r:(r['treatment_id'],r['logical_pub_index']), 'accepted slot')
    expected = {(t,p) for t in TREATMENTS for p in range(1,target.target_n+1)}
    require(set(slots)==expected, 'Accepted logical slot inventory differs from complete design')
    maps = unique_index(source_map, lambda r:(r['treatment_id'],int(r['logical_pub_index'])), 'source slot')
    require(set(maps)==expected, 'Source map must contain all and only accepted slots')
    exported = unique_index(repeats, lambda r:(r['treatment'],r['IBM_job_id'],int(r['PUB_index'])), 'export identity')
    fitted = unique_index(canonical, lambda r:(r['treatment'],int(r['pub'])), 'canonical fit identity')
    require(len(exported)==len(fitted)==len(expected), 'Unexpected exported/canonical PUB count')
    used=set(); used_fit=set(); result=[]
    for key in sorted(expected):
        accepted=slots[key]; source=maps[key]
        for a,b in [('job_id','job_id'),('source_sha256','source_sha256'),('source','source'),
                    ('source_block_id','logical_block_id'),('batch_id','batch_id'),
                    ('account_label','account_label'),('backend','backend')]:
            require(source.get(a)==str(accepted.get(b) or ''), f'Source provenance mismatch {key}: {a}')
        require(source['target_id']==target.target_id and int(source['target_n'])==target.target_n,
                f'Source target mismatch {key}')
        require(json.loads(source['input_hashes'])==target.input_hashes==accepted['input_hashes'],
                f'Frozen source mismatch {key}')
        identity=(key[0],source['job_id'],int(source['source_pub']))
        fit_key=(key[0],int(source['combined_pub']))
        require(identity not in used and fit_key not in used_fit, 'Ambiguous source mapping')
        used.add(identity);used_fit.add(fit_key)
        require(identity in exported and fit_key in fitted, f'Missing joined observation {key}')
        row=exported[identity]; fit=fitted[fit_key]
        require(row['experiment_id']==target.experiment_id and int(row['n'])==target.target_n,
                f'Export experiment mismatch {key}')
        values={name:float(row[name]) for name in ('SSE','RMSE')}
        for name,value in values.items():
            require(math.isfinite(value) and value>0, f'Invalid {name} {key}')
            require(value==float(fit[name]), f'Export differs from canonical {name} {key}')
        # Canonical EIS14 uses 132 complex points, i.e. 264 scalar residuals.
        require(math.isclose(values['RMSE']**2*264,values['SSE'],rel_tol=1e-10),
                f'Canonical SSE/RMSE definition mismatch {key}')
        result.append(dict(treatment=key[0],source_n=target.target_n,source_pub=key[1],
                           job_id=accepted['job_id'],source=accepted['source'],
                           source_sha256=accepted['source_sha256'],logical_block_id=accepted['logical_block_id'],
                           account_label=accepted['account_label'],**values))
    require(used==set(exported) and used_fit==set(fitted), 'Unused scientific observations')
    return result


def verify_analysis(target, selected, output, export_path, receipt=None):
    """Validate both current STEP receipts and older manifest-bound exports."""
    from independent_n_campaign import _sha as payload_hash
    output=Path(output); export_path=Path(export_path)
    em=read_json(export_path); am_path=output/'analysis_manifest.json'
    check_hash(am_path,em.get('source_analysis_manifest_sha256'))
    am=read_json(am_path)
    expected={'target_id':target.target_id,'target_n':target.target_n,
              'target_manifest_sha256':target.sha256,'input_hashes':target.input_hashes,
              'source_run':'run_02','backend':'ibm_fez'}
    require(all(am.get(k)==v for k,v in expected.items()), 'Analysis manifest provenance mismatch')
    require(am.get('dataset_status') in ('COMPLETE','REPAIRED_COMPLETE'), 'Incomplete analysis')
    require(am.get('validated_pubs_by_treatment')=={t:target.target_n for t in TREATMENTS},
            'Analysis treatment counts mismatch')
    hashes={str(am_path.resolve()):_sha(am_path)}
    if receipt:
        rp=Path(receipt); saved=read_json(rp)
        digest=payload_hash(sorted(selected,key=lambda r:(r['treatment_id'],r['logical_pub_index'])))
        expected_receipt={'n':target.target_n,'target_id':target.target_id,'input_hashes':target.input_hashes,
                          'target_manifest_sha256':target.sha256,'dataset_sha256':digest}
        require(all(saved.get(k)==v for k,v in expected_receipt.items()), 'STEP receipt provenance mismatch')
        require(Path(saved['output']).resolve()==output.resolve(), 'STEP output location mismatch')
        for name,expected_hash in saved['artifact_sha256'].items():
            check_hash(contained(output,name),expected_hash)
        require(artifact_hashes(output)==saved['artifact_sha256'], 'STEP artifact inventory mismatch')
        hashes[str(rp.resolve())]=_sha(rp)
    require(em.get('target_id')==target.target_id and em.get('experiment_id')==target.experiment_id
            and em.get('n')==target.target_n and em.get('input_hashes')==target.input_hashes
            and em.get('source_run')=='run_02' and em.get('partial_diagnostic_only') is False,
            'Export provenance mismatch')
    figures=Path(em['figure_directory']);data=export_path.parent
    require(verified_export_pair(figures,data,_sha(am_path)),
            f'Export pair inventory/hash/PNG contract failed: {export_path}')
    canonical=contained(output,am['canonical_combined_analysis'])/'per_repeat.csv'
    source=output/'pub_source_manifest.csv'
    rows=join_observations(target,selected,_read_csv(source),_read_csv(data/'per_repeat.csv'),_read_csv(canonical))
    for p in [source,canonical,export_path,figures/'export_manifest.json']:
        hashes[str(p.resolve())]=_sha(p)
    if am.get('dataset_status')=='REPAIRED_COMPLETE':
        dm_path=Path(am['repaired_dataset_manifest']).resolve();dm=read_json(dm_path)
        require(dm.get('target_id')==target.target_id and dm.get('input_hashes')==target.input_hashes
                and dm.get('n')==target.target_n and dm.get('source_run')=='run_02',
                'Repaired dataset manifest provenance mismatch')
        values_path=dm_path.parent/'selected_pub_values.csv'
        check_hash(values_path,dm.get('selected_pub_values_sha256'))
        values=unique_index(_read_csv(values_path),lambda r:(r['treatment_id'],int(r['logical_pub_index'])),'dataset slot')
        require(set(values)=={(r['treatment_id'],r['logical_pub_index']) for r in selected},'Dataset slot mismatch')
        for r in selected:
            saved=values[(r['treatment_id'],r['logical_pub_index'])]
            require(saved['job_id']==r['job_id'] and saved['source_sha256']==r['source_sha256'] and
                    json.loads(saved['input_hashes'])==target.input_hashes,'Dataset source mismatch')
            for i in range(7):
                require(float(saved[f'Z{i}'])==r['expectation_values'][i] and
                        float(saved[f'std_Z{i}'])==r['standard_deviations'][i],'Dataset accepted values mismatch')
        hashes[str(dm_path)]=_sha(dm_path);hashes[str(values_path)]=_sha(values_path)
    for name,digest in artifact_hashes(output).items():hashes[str(contained(output,name))]=digest
    for name,digest in em['csv_sha256'].items():hashes[str(contained(data,name))]=digest
    for name,item in em['figures'].items():hashes[str(contained(figures,name))]=item['sha256']
    return rows,hashes,am


def audit_export(export_path):
    """Artifact integrity independent of whether its analysis source still exists."""
    ep=Path(export_path);em=read_json(ep);failures=[]
    paths=[(ep.parent/name,digest) for name,digest in em.get('csv_sha256',{}).items()]
    paths += [(Path(em['figure_directory'])/name,item['sha256']) for name,item in em.get('figures',{}).items()]
    for path,digest in paths:
        try:check_hash(path,digest)
        except ValueError as exc:failures.append(str(exc))
    pair_ok=verified_export_pair(Path(em['figure_directory']),ep.parent,em.get('source_analysis_manifest_sha256'))
    return dict(path=str(ep),sha256=_sha(ep),schema_version=em.get('schema_version'),
                artifact_hash_failures=failures,artifact_count=len(paths),pair_verified=pair_ok,
                analysis_expected_sha256=em.get('source_analysis_manifest_sha256'))


def inspect_campaign(project, campaign='vqe-independent-n-01', *, registry=None, status=None):
    """No writes; independently verify scientific coverage, then publication evidence."""
    from job_registry import JobRegistry
    from independent_n_campaign import _target_from_level, campaign_root, _sha as payload_hash
    from targeted_recovery import repaired_dataset_status
    root=repository_root(project)
    registry=registry or JobRegistry(JobRegistry.default_database(root),readonly=True)
    status=status or registry.independent_campaign_status(campaign)
    directory=campaign_root(root,campaign)
    output=root/MITIGATION_REL/'output'
    parent=output/'02_run_level_data/adaptive_targets'
    result=dict(campaign=campaign,state='BLOCKED',ready=False,verified_n_levels=[],levels=[],blockers=[],
                observations=[],protected_hashes={},verified_usage=[],network_calls=0,production_writes=0,
                scientific_completion_basis='accepted logical slots plus immutable analysis/export evidence; original execution state preserved')
    require(status.get('exists'), 'Campaign not registered')
    require(sorted(l['target_n'] for l in status['levels'])==list(LEVELS), 'Require exactly six n levels')
    with registry._connect() as db:
        require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','SQLite integrity failed')
    for level in status['levels']:
        n=level['target_n']; item=dict(n=n,legacy_state=level['state'],ready=False,failures=[],candidates=[])
        result['levels'].append(item)
        try:
            target=_target_from_level(root,level,registry=registry)
            require(tuple(target.treatment_ids)==TREATMENTS,'Unexpected treatment inventory')
            data=repaired_dataset_status(target,registry);selected=data['rows']
            item.update(accepted=len(selected),expected=n*8,counts=data['counts_by_treatment'],
                        duplicate_slots=data['duplicate_slots'],execution_complete=data['execution_complete'],
                        scientific_complete=data['analysis_eligible'])
            require(data['analysis_eligible'] and not data['duplicate_slots'] and
                    data['counts_by_treatment']=={t:n for t in TREATMENTS},'Incomplete accepted logical coverage')
            require(all(r['input_hashes']==target.input_hashes for r in selected),'Frozen inputs mismatch')
            archive_hashes=verify_selected_archives(root,target,registry,selected)
            result['protected_hashes'].update(archive_hashes)
            item['selected_archives_verified']=True
            digest=payload_hash(sorted(selected,key=lambda r:(r['treatment_id'],r['logical_pub_index'])))
            item['dataset_sha256']=digest
            item['target_id']=target.target_id
            from step_artifacts import latest_analysis_receipt,analysis_receipts
            receipt=latest_analysis_receipt(directory/'steps',n,digest)
            for historical in analysis_receipts(directory/'steps',n,digest):
                result['protected_hashes'][str(historical.resolve())]=_sha(historical)
            reports=sorted((directory/f'n{n}').glob('scientific_report*.json'))
            item['scientific_reports']=[str(p) for p in reports]
            item['analysis_receipt']=str(receipt) if receipt.is_file() else None
            paths=[parent/target.target_id,*sorted(parent.glob(target.target_id+'_v*'))]
            item['existing_analysis_manifests']=[str(p/'analysis_manifest.json') for p in paths
                                                 if (p/'analysis_manifest.json').is_file()]
            if receipt.is_file():
                saved=read_json(receipt)
                require(saved.get('campaign_id')==campaign,'Receipt campaign mismatch')
                rp=Path(saved['output']).resolve()
                require(rp.parent==parent.resolve() and (rp.name==target.target_id or rp.name.startswith(target.target_id+'_v')),
                        'Receipt output escapes canonical analysis root')
                paths=[rp]  # Do not silently fall back from a damaged current STEP receipt.
            exports=sorted((output/'02_run_level_data'/f'n={n}').glob('**/export_manifest.json'))
            item['export_audits']=[audit_export(ep) for ep in exports]
            successes=[]
            for ep in exports:
                em=read_json(ep)
                expected_figures=output/'01_results_and_figures'/f'n={n}'/ep.parent.relative_to(output/'02_run_level_data'/f'n={n}')
                for ap in paths:
                    actual=_sha(ap/'analysis_manifest.json') if (ap/'analysis_manifest.json').is_file() else None
                    candidate=dict(analysis=str(ap/'analysis_manifest.json'),export=str(ep),
                                   expected_sha256=em.get('source_analysis_manifest_sha256'),actual_sha256=actual)
                    item['candidates'].append(candidate)
                    if actual is None or actual!=candidate['expected_sha256']:continue
                    try:
                        require(Path(em['figure_directory']).resolve()==expected_figures.resolve(),
                                f'Export figure location does not match canonical pair: {ep}')
                        rows,hashes,am=verify_analysis(target,selected,ap,ep,receipt if receipt.is_file() else None)
                        successes.append((ep,ap,rows,hashes,am))
                    except (ValueError,OSError,KeyError,TypeError) as exc:candidate['failure']=str(exc)
            require(successes,'No complete canonical analysis/export pair verifies; see exact candidate paths and hashes')
            # Multiple immutable exports are allowed only if all mapped scientific values agree.
            require(all(s[2]==successes[0][2] for s in successes),'Conflicting valid canonical scientific versions')
            ep,ap,rows,hashes,am=successes[-1]
            from adaptive_targets import _target_qpu_usage_provenance
            usage,_=_target_qpu_usage_provenance(root,target,registry)
            result['verified_usage'].extend(dict(source_n=n,**u) for u in usage['jobs'] if u['verified'])
            item.update(ready=True,analysis=str(ap),export=str(ep),analysis_sha256=_sha(ap/'analysis_manifest.json'),
                        export_sha256=_sha(ep),classification='A/B: canonical evidence verified; legacy discovery/state not authoritative',
                        qpu_usage_comparable=usage['complete_comparable'],qpu_usage_coverage=usage)
            result['observations'].extend(rows);result['protected_hashes'].update(hashes)
            result['verified_n_levels'].append(n)
        except (ValueError,OSError,KeyError,TypeError,RuntimeError,MemoryError) as exc:
            reason=str(exc) or type(exc).__name__
            item['failures'].append(reason);result['blockers'].append(f'n={n}: {reason}')
    if not result['blockers']:
        from dependence_statistics import validate_observations
        result['observations_sha256']=validate_observations(result['observations'])
        result.update(ready=True,state='READY_TO_COMPARE')
    return result


def summarize(rows):
    """Descriptive summaries of existing canonical fits, no independent-unit inference."""
    from dependence_statistics import validate_observations
    validate_observations(rows)
    records=[]
    for n in (*LEVELS,105):
        groups={t:[r for r in rows if r['treatment']==t and (n==105 or r['source_n']==n)] for t in TREATMENTS}
        for t,group in groups.items():
            item=dict(n=n,scope='pooled' if n==105 else 'n-level',treatment=t,count=len(group))
            for metric in ('SSE','RMSE'):
                values=[r[metric] for r in group]
                baseline=[r[metric] for r in groups[TREATMENTS[0]]]
                for stat,fn in [('mean',statistics.mean),('median',statistics.median),('sample_SD',statistics.stdev)]:
                    item[f'{stat}_{metric}']=fn(values)
                for stat,fn in [('mean',statistics.mean),('median',statistics.median)]:
                    ratio=fn(values)/fn(baseline)
                    item[f'{stat}_{metric}_over_None']=ratio
                    item[f'{stat}_{metric}_reduction_percent']=100*(1-ratio)
            records.append(item)
    return records


def write_table(path, rows):
    with Path(path).open('x',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    expected=[{k:'' if v is None else str(v) for k,v in row.items()} for row in rows]
    require(_read_csv(Path(path))==expected,f'CSV round-trip verification failed: {path}')


FIGURE_NAMES = ('Figure_C_105_PUB_convergence.png', 'Figure_D_105_PUB_triple_positioning.png',
                'Figure_E_M0_cluster_bootstrap_delta_SSE.png', 'Figure_F_M0_cluster_bootstrap_reduction.png')


def comparison_reference_claim(project):
    """Read the recorded operator claim; hash integrity does not verify its science.

    Never manufacture a reference from a code literal or replace it with the
    accepted median. Its original observations/manifest remain required.
    """
    publication=published_comparison(project)
    entries=[r for r in publication['tables'] if r['name']=='triple_positioning_manifest.json']
    require(len(entries)==1,'Recorded M0 reference claim is missing from the canonical publication')
    entry=entries[0];path=published_artifact(project,entry['filename'])
    check_hash(path,entry['sha256']);record=read_json(path)
    value=record.get('supplied_M0_median_SSE')
    require(type(value) in (int,float) and math.isfinite(value) and value>0,
            'Recorded M0 reference claim is not a finite positive number')
    return value,dict(path=str(path.resolve()),sha256=entry['sha256'],
                      field='supplied_M0_median_SSE',scientifically_verified=False,
                      origin='Recorded operator claim; original scientific source not recovered')


def reference_audit(root, evidence):
    """Retrospective audit of the superseded numeric-reference request.

    Kept for comparison_blocker_audit.py provenance; never a publication gate.
    Current publication uses validated_reference_values and visual templates only.
    """
    historical=root/'02_Hardware/04_code/results'
    audit=historical/'04_runtime_logs/comparison_105_pubs/v001'
    marker=read_json(audit/'COMPLETE.json')
    check_hash(audit/'comparison_manifest.json',marker['manifest_sha256'])
    check_hash(audit/'SHA256SUMS.json',marker['hash_inventory_sha256'])
    inventory=read_json(audit/'SHA256SUMS.json');verify_snapshot(inventory)
    inventory.update({str(p.resolve()):_sha(p) for p in audit.iterdir() if p.is_file()})
    tables=historical/'02_run_level_data/comparison_105_pubs/v001'
    old=_read_csv(tables/'source_105_pubs.csv')
    def normalize(rows):
        return sorted([dict(r,source_n=int(r['source_n']),source_pub=int(r['source_pub']),
                            SSE=float(r['SSE']),RMSE=float(r['RMSE'])) for r in rows],
                      key=lambda r:(r['treatment'],r['source_n'],r['source_pub']))
    require(normalize(old)==normalize(evidence['observations']),
            'Accepted observations/provenance differ from historical v001; publication stopped')
    from vqe_adapter import load_pilot
    classical=load_pilot(root)._classical_sse_reference(root)
    metric=root/'01_quantum_eis_fit/01_vqe/output/EIS14_VQE_metrics.csv'
    spectrum=metric.with_name('classical_fitted_spectrum.csv')
    inventory.update({str(p.resolve()):_sha(p) for p in (metric,spectrum)})
    measured=statistics.median(r['SSE'] for r in evidence['observations'] if r['treatment']==TREATMENTS[0])
    expected,claim_source=comparison_reference_claim(root)
    inventory[claim_source['path']]=claim_source['sha256']
    blockers={}
    if not math.isclose(measured,expected,rel_tol=1e-12,abs_tol=1e-10):
        blockers['D']=f'M0 reference mismatch: supplied={expected!r}; accepted and historical={measured!r}'
    # No inferred replacement for the absent matched-n contract. The independent
    # execution-cluster implementation is a different method and cannot substitute.
    blockers['E']=blockers['F']='Matched n-level cluster-bootstrap reference CSV, manifest and method not located'
    refs={'classical_SSE':classical,'measured_M0_median_SSE':measured,
          'supplied_M0_median_SSE':expected,'supplied_ideal_SSE':258.05897604193257,
          'M0_claim_source':claim_source,'verified':False}
    if not math.isclose(classical,120.22451879422366,rel_tol=1e-12):
        blockers['D']='Classical reference mismatch'
    # Triple positioning needs the missing supplied reference manifest as well.
    blockers.setdefault('D','Supplied triple-positioning reference manifest unavailable for verification')
    return dict(blockers=blockers,references=refs,protected_hashes=inventory,
                historical_convergence=str(tables/'convergence_105_statistics.csv'),
                historical_source=str(tables/'source_105_pubs.csv'))


def _write_json(path, value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,allow_nan=False)
        stream.flush();os.fsync(stream.fileno())


def validated_reference_values(root, evidence):
    """Canonical recalculation only: visual-template values are not consulted."""
    from vqe_adapter import load_pilot, verify_frozen_inputs
    from repository_paths import register_science, frozen_source
    import numpy as np
    pilot=load_pilot(root);register_science(root)
    from impedance_model import read_eis_file
    frequency,zexp=read_eis_file(str(root/'data/EIS14.csv'))
    frequency=np.asarray(frequency,float);zexp=np.asarray(zexp,complex)
    require(len(frequency)==132 and len(zexp)==132,'Expected 132 complex EIS measurements')
    protected=dict(evidence['protected_hashes'])
    for relative in ('data/EIS14.csv','01_quantum_eis_fit/04_code/vqe/impedance_model.py',
                     '01_quantum_eis_fit/04_code/vqe/parameter_mapping.py','01_quantum_eis_fit/04_code/vqe/hea_ansatz.py'):
        p=root/relative;protected[str(p.resolve())]=_sha(p)
    selected=unique_index(evidence['observations'],lambda r:(r['treatment'],r['source_n'],r['source_pub']),'comparison slot')
    max_sse_error=0.;max_rmse_error=0.;seen=set()
    for level in evidence['levels']:
        analysis=Path(level['analysis']);am=read_json(analysis/'analysis_manifest.json')
        # Cumulative fit tables retain original job labels for replacements.
        # The receipt-bound selected dataset is the accepted expectation source.
        repaired=am.get('repaired_dataset_manifest')
        path=Path(repaired).parent/'selected_pub_values.csv' if repaired else analysis/'cumulative_per_pub.csv'
        check_hash(path,protected.get(str(path.resolve())))
        for row in _read_csv(path):
            key=(row['treatment_id' if repaired else 'treatment'],level['n'],int(row['logical_pub_index']))
            require(key in selected and key not in seen,'Recalculation slot mismatch');seen.add(key)
            require(row['job_id']==selected[key]['job_id'] and row['source_sha256']==selected[key]['source_sha256'],
                    'Recalculation job/source provenance mismatch')
            _,_,sse,rmse=pilot.calculate_pub_eis([float(row[f'Z{i}']) for i in range(7)],frequency,zexp)
            max_sse_error=max(max_sse_error,abs(sse-selected[key]['SSE']))
            max_rmse_error=max(max_rmse_error,abs(rmse-selected[key]['RMSE']))
            require(math.isclose(sse,selected[key]['SSE'],rel_tol=1e-10,abs_tol=1e-9) and
                    math.isclose(rmse,selected[key]['RMSE'],rel_tol=1e-10,abs_tol=1e-10),
                    f'Canonical SSE/RMSE recalculation mismatch: {key}')
    require(seen==set(selected),'Incomplete canonical recalculation')
    refs=dict(verified=True,measured_M0_median_SSE=statistics.median(
        r['SSE'] for r in evidence['observations'] if r['treatment']==TREATMENTS[0]),
        classical_SSE=None,ideal_SSE=None,omissions={},verification={},
        recalculation=dict(observations=len(seen),max_abs_SSE_error=max_sse_error,max_abs_RMSE_error=max_rmse_error,
                           SSE_formula='sum(abs(Zfit-Zexp)**2)',RMSE_formula='sqrt(SSE/(2*132))'))
    try:
        refs['classical_SSE']=pilot._classical_sse_reference(root)
        for name in ('EIS14_VQE_metrics.csv','classical_fitted_spectrum.csv'):
            p=root/'01_quantum_eis_fit/01_vqe/output'/name;protected[str(p.resolve())]=_sha(p)
        refs['verification']['classical']='Canonical validator cross-checks reported SSE against 132 archived complex residuals'
    except (OSError,ValueError,KeyError) as exc:refs['omissions']['classical']=str(exc)
    try:
        bundle=frozen_source(root);lock=read_json(bundle/'lock.json')
        for name,digest in lock['hashes'].items():
            p=contained(bundle,name);check_hash(p,digest);protected[str(p)]=digest
        protected[str((bundle/'lock.json').resolve())]=_sha(bundle/'lock.json')
        *_,ev,qiskit_version=verify_frozen_inputs(root)
        _,curve,ideal_sse,_=pilot.calculate_pub_eis(ev,frequency,zexp)
        for level in evidence['levels']:
            data=Path(level['export']).parent
            table=data/'nyquist_curves.csv';summary=data/'round_summary.json'
            check_hash(table,protected.get(str(table.resolve())));check_hash(summary,protected.get(str(summary.resolve())))
            points=_read_csv(table)
            require(len(points)==132,'Ideal reference spectrum coverage mismatch')
            saved=np.array([complex(float(r['Ideal_Z_real']),float(r['Ideal_Z_imag'])) for r in points])
            require(np.allclose(saved,curve,rtol=1e-10,atol=1e-10) and
                    math.isclose(read_json(summary)['ideal_SSE'],ideal_sse,rel_tol=1e-10),
                    'Ideal frozen-model reference differs from validated export')
        refs['ideal_SSE']=ideal_sse
        refs['verification']['ideal']='Offline seven-qubit Statevector from locked theta; canonical parameter/model mapping; all six exported curves agree'
        refs['verification']['qiskit_version']=qiskit_version
    except (OSError,ValueError,KeyError,ImportError,RuntimeError) as exc:refs['omissions']['ideal']=str(exc)
    verify_snapshot(protected)
    return dict(references=refs,protected_hashes=protected)


def verify_plotted_table(path,records):
    saved=_read_csv(path)
    require(len(saved)==len(records),'Figure CSV row-count mismatch')
    for actual,expected in zip(saved,records):
        require(set(actual)==set(expected),'Figure CSV column mismatch')
        for key,value in expected.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                require(float(actual[key])==float(value),f'Figure CSV value mismatch: {key}')
            else:require(actual[key]==str(value),f'Figure CSV value mismatch: {key}')


def publish(project, campaign, *, authorize_output_write=False):
    if authorize_output_write is not True:
        raise PermissionError('Explicit --authorize-output-write required; no production writes performed')
    evidence=inspect_campaign(project,campaign)
    require(evidence['ready'],json.dumps(evidence['blockers']))
    from dependence_statistics import (generate_figure_c,generate_figure_d,matched_n_sensitivity,
                                       generate_figures_ef,BOOT,SEED,COMPARISON_TREATMENTS,figure_method_metadata)
    from repository_paths import comparison_output_paths
    from datetime import datetime,timezone
    import importlib.metadata
    import shutil,uuid
    root=repository_root(project);audit=validated_reference_values(root,evidence)
    protected={**evidence['protected_hashes'],**audit['protected_hashes']}
    destinations=comparison_output_paths(root);figures,tables=destinations['figures'],destinations['data']
    for directory in destinations.values():directory.mkdir(parents=True,exist_ok=True)
    # Preserve all previous publications and audits, including the Figure C bytes.
    for directory in destinations.values():
        for path in directory.iterdir():
            if path.is_file():protected[str(path.resolve())]=_sha(path)
    ledger=root/MITIGATION_REL/'output/03_IBM_execution_records/ai_assistant/job_registry.sqlite3'
    for path in (ledger,Path(str(ledger)+'-wal')):
        if path.is_file():protected[str(path.resolve())]=_sha(path)
    verify_snapshot(protected)
    reused={}
    prior=published_comparison(root)
    if prior.get('version'):
        marker=read_json(tables/f"publication_v{prior['version']:03d}.json")
        previous=read_json(tables/marker['manifest'])
        verify_snapshot(previous['source_hashes'])
        protected.update(previous['source_hashes'])
        require(previous['observations_sha256']==evidence['observations_sha256'],
                'Existing Figure C refers to different accepted observations')
        for name in (FIGURE_NAMES[0],'convergence_105_statistics.csv'):
            entry=previous['artifacts'][name]
            check_hash(destinations[entry['kind']]/entry['filename'],entry['sha256']);reused[name]=entry
        # Verify all preserved C medians directly without regenerating its bands.
        preserved_rows=_read_csv(tables/reused['convergence_105_statistics.csv']['filename'])
        require(len(preserved_rows)==808 and
                {(r['treatment'],int(r['cumulative_pub_count'])) for r in preserved_rows}==
                {(t,k) for t in TREATMENTS for k in range(5,106)},'Incomplete preserved Figure C table')
        for row in preserved_rows:
            values=[r['SSE'] for r in sorted(evidence['observations'],key=lambda r:(r['source_n'],r['source_pub']))
                    if r['treatment']==row['treatment']][:int(row['cumulative_pub_count'])]
            require(statistics.median(values)==float(row['median_SSE']),'Preserved Figure C median mismatch')
    version=1
    names=list(FIGURE_NAMES)+['source_105_pubs.csv','comparison_summary.csv','convergence_105_statistics.csv',
        'verified_job_usage.csv','comparison_manifest.json','triple_positioning_manifest.json','triple_positioning_105.csv',
        'M0_matched_n_sensitivity_statistics.csv','statistical_provenance_manifest.json','summary_report.json','SHA256SUMS.json']
    def filename(name):
        p=Path(name);return name if version==1 else f'{p.stem}__v{version:03d}{p.suffix}'
    while ((tables/f'publication_v{version:03d}.json').exists() or
           any((figures if name.endswith('.png') else tables).joinpath(filename(name)).exists() for name in names)):
        version+=1
    stage=root/'.codex_tmp'/('comparison-'+uuid.uuid4().hex);stage.mkdir(parents=True,mode=0o755)
    rows=evidence['observations'];summary=summarize(rows)
    write_table(stage/'source_105_pubs.csv',rows);write_table(stage/'comparison_summary.csv',summary)
    if evidence['verified_usage']:write_table(stage/'verified_job_usage.csv',evidence['verified_usage'])
    if not reused:
        records=generate_figure_c(rows=rows,figure_dir=stage,table_dir=stage)
        verify_plotted_table(stage/'convergence_105_statistics.csv',records)
    records=generate_figure_d(rows=rows,figure_dir=stage,table_dir=stage,references=audit['references'])
    verify_plotted_table(stage/'triple_positioning_105.csv',records)
    sensitivity=matched_n_sensitivity(rows)
    records=generate_figures_ef(sensitivity,figure_dir=stage,table_dir=stage)
    verify_plotted_table(stage/'M0_matched_n_sensitivity_statistics.csv',records)
    warning=('Sample SD is descriptive PUB variability. C/D use ordinary IID bootstrap sensitivity intervals; '
             'E/F use matched n-level block-resampling sensitivity intervals. Neither establishes independent '
             'experimental uncertainty or statistical significance. Pooled-prefix curves are not optimizer convergence. '
             'Recovery selection, unequal n, account/calibration effects and between-block dependence remain limitations.')
    ordinary=dict(draws=BOOT,seed=SEED,percentiles=[2.5,97.5],quantile_method='linear',
                  treatment_order=list(TREATMENTS),unit='pooled logical PUB; IID sensitivity only',
                  legacy_CI_columns='CI_low/CI_high denote sensitivity bounds, not inferential confidence intervals')
    _write_json(stage/'triple_positioning_manifest.json',dict(state='COMPLETE',**audit['references'],
        ordinary_bootstrap=ordinary,visual_reference_policy='visual templates only; no supplied numerical targets'))
    _write_json(stage/'statistical_provenance_manifest.json',sensitivity['manifest'])
    _write_json(stage/'summary_report.json',dict(warning=warning,blockers={},
        preserved_C=bool(reused),references=audit['references'],
        winners=[min((r for r in summary if r['n']==n),key=lambda r:r['median_SSE']) for n in (*LEVELS,105)]))
    verify_snapshot(protected)
    current=inspect_campaign(project,campaign)
    require(current['ready'] and current['observations_sha256']==evidence['observations_sha256'] and
            current['protected_hashes']==evidence['protected_hashes'],'Scientific evidence changed during plotting')
    artifacts=dict(reused);hashes={str((destinations[e['kind']]/e['filename']).resolve()):e['sha256'] for e in reused.values()}
    for p in stage.iterdir():
        if p.suffix=='.png':inspect_png(p,layout='landscape')
        dest=(figures if p.suffix=='.png' else tables)/filename(p.name)
        with p.open('rb') as src,dest.open('xb') as dst:shutil.copyfileobj(src,dst)
        check_hash(dest,_sha(p));hashes[str(dest.resolve())]=_sha(dest)
        artifacts[p.name]=dict(filename=dest.name,sha256=_sha(dest),kind='figures' if p.suffix=='.png' else 'data')
    require(all(name in artifacts for name in FIGURE_NAMES),'Four comparison figures are required for completion')
    for name in FIGURE_NAMES:inspect_png(figures/artifacts[name]['filename'],layout='landscape')
    timestamp=datetime.now(timezone.utc).isoformat()
    manifest=dict(schema_version=3,campaign=campaign,version=version,published_at=timestamp,state='COMPLETE',
        observations=840,source_n_levels=list(LEVELS),observations_sha256=evidence['observations_sha256'],
        artifacts=artifacts,artifacts_sha256=hashes,source_hashes=protected,blockers={},warning=warning,
        treatment_labels=dict(COMPARISON_TREATMENTS),evidence=evidence,references=audit['references'],
        ordinary_bootstrap=ordinary,cluster_bootstrap=sensitivity['manifest'],
        method_summary='C/D: ordinary IID sensitivity (3,000 draws, seed 1013). E/F: matched n-level sensitivity '
                       '(10,000 draws, seed 1013). Percentile 95% conditional sensitivity intervals; no inferential confidence claims.',
        figure_methods=figure_method_metadata(),
        preserved_artifacts=reused,
        software_versions={name:importlib.metadata.version(name) for name in ('numpy','pandas','matplotlib','Pillow')},
        python_version=sys.version,source_code_sha256={str(p.resolve()):_sha(p) for p in
            (Path(__file__),Path(__file__).with_name('dependence_statistics.py'),Path(__file__).with_name('repository_paths.py'),
             Path(__file__).with_name('scientific_exports.py'),Path(__file__).resolve().parents[2]/'01_vqe/repository_paths.py')})
    mp=tables/filename('comparison_manifest.json');_write_json(mp,manifest)
    hashes={**hashes,str(mp.resolve()):_sha(mp)};hp=tables/filename('SHA256SUMS.json');_write_json(hp,hashes)
    verify_snapshot(hashes);verify_snapshot(protected)
    _write_json(tables/f'publication_v{version:03d}.json',dict(version=version,published_at=timestamp,
                manifest=mp.name,manifest_sha256=_sha(mp),hash_inventory=hp.name,hash_inventory_sha256=_sha(hp)))
    require(stage.resolve().parent==(root/'.codex_tmp').resolve() and stage.name.startswith('comparison-'),
            'Refusing cleanup outside comparison scratch directory')
    shutil.rmtree(stage)
    return dict(state=manifest['state'],version=version,blockers={},
                output_directories=[str(figures),str(tables)],manifest=str(mp))


def published_comparison(project=None):
    """Dashboard shares the publisher contract; no historical fallback."""
    from repository_paths import comparison_output_paths
    paths=comparison_output_paths(project)
    markers=sorted(paths['data'].glob('publication_v*.json'),key=lambda p:int(p.stem.rsplit('v',1)[1]))
    if not markers:return dict(state='MISSING_ARTIFACT',reason='No canonical publication marker',figures=[],tables=[])
    def safe(folder,name):
        require(Path(name).name==name and '/' not in name and '\\' not in name,'Invalid artifact name')
        return paths[folder]/name
    marker=read_json(markers[-1]);mp=safe('data',marker['manifest']);check_hash(mp,marker['manifest_sha256'])
    hp=safe('data',marker['hash_inventory']);check_hash(hp,marker['hash_inventory_sha256'])
    manifest=read_json(mp);inventory=read_json(hp)
    figures=[];tables=[]
    for logical,entry in manifest['artifacts'].items():
        path=safe(entry['kind'],entry['filename']);check_hash(path,entry['sha256'])
        require(inventory.get(str(path.resolve()))==entry['sha256'],'Artifact inventory mismatch')
        item=dict(name=logical,filename=path.name,sha256=entry['sha256'])
        (figures if entry['kind']=='figures' else tables).append(item)
    summary_entry=manifest['artifacts']['comparison_summary.csv']
    summary=_read_csv(safe('data',summary_entry['filename']))
    contrast_entry=manifest['artifacts'].get('M0_matched_n_sensitivity_statistics.csv')
    contrasts=_read_csv(safe('data',contrast_entry['filename'])) if contrast_entry else []
    for path in (mp,hp,markers[-1]):
        tables.append(dict(name=path.name,filename=path.name,sha256=_sha(path)))
    return dict(state=manifest['state'],version=manifest['version'],published_at=manifest['published_at'],
                observations=manifest['observations'],n_levels=manifest['source_n_levels'],
                treatment_labels=manifest['treatment_labels'],figures=figures,tables=tables,summary=summary,
                contrasts=contrasts,references=manifest.get('references',{}),
                treatment_colors=manifest.get('publication_style',{}).get('palette',{}),
                figure_methods=manifest.get('figure_methods',{}),
                method_summary=manifest.get('method_summary',manifest['warning']),
                blockers=manifest['blockers'],warning=manifest['warning'],
                missing_figures=[name for name in FIGURE_NAMES if name not in manifest['artifacts']])


def published_artifact(project, name):
    from repository_paths import comparison_output_paths
    result=published_comparison(project);paths=comparison_output_paths(project)
    for kind in ('figures','tables'):
        for entry in result[kind]:
            if entry['filename']==name:return paths['figures' if kind=='figures' else 'data']/name
    raise ValueError('Artifact is not part of the active canonical publication')


def restyle_publication(project, *, authorize_output_write=False, acknowledge_deleted_comparison_pngs=False, reference_sensitivities=False, standardize_reference_methods=False, preserve_figure_c=False):
    """Versioned rendering, optionally restoring the original reference RNG contract.

    Default rendering retains primary statistical bytes and methods. Explicit
    standardization verifies ordinary C/D unchanged and changes E/F resampling
    seed only, with exact checks that all central estimates remain unchanged.
    An explicit acknowledgement records missing prior comparison PNGs as absent.
    It never excuses a modified artifact or any missing CSV, scientific input,
    per-n artifact, receipt or execution record.
    """
    require(authorize_output_write is True,'Explicit --authorize-output-write required')
    from repository_paths import comparison_output_paths
    from dependence_statistics import (render_figure_c,render_figure_d,render_figures_ef,PUBLICATION_STYLE,
        figure_c_statistics,figure_d_statistics,matched_n_sensitivity,figure_method_metadata)
    from datetime import datetime,timezone
    import shutil,uuid
    root=repository_root(project);paths=comparison_output_paths(root)
    figures,data=paths['figures'],paths['data']
    markers=sorted(data.glob('publication_v*.json'),key=lambda p:int(p.stem.rsplit('v',1)[1]))
    require(markers,'A committed numerical publication is required for styling-only publication')
    marker=read_json(markers[-1]);mp=contained(data,marker['manifest']);hp=contained(data,marker['hash_inventory'])
    check_hash(mp,marker['manifest_sha256']);check_hash(hp,marker['hash_inventory_sha256'])
    previous=read_json(mp);inventory=read_json(hp)
    expected={**previous['source_hashes'],**inventory,str(markers[-1].resolve()):_sha(markers[-1]),str(hp.resolve()):_sha(hp)}
    deleted=dict(previous.get('missing_prior_artifacts',previous.get('operator_deleted_artifacts',{})));protected={}
    for name,digest in expected.items():
        path=Path(name).resolve()
        if (not path.exists() and path.parent==figures.resolve() and path.suffix.lower()=='.png'
                and acknowledge_deleted_comparison_pngs is True):
            deleted[str(path)]=dict(expected_sha256=digest,status='prior comparison PNG missing; absence acknowledged; historical path not restored')
        else:
            check_hash(path,digest);protected[str(path)]=digest
    # Current scientific evidence must still pass the complete canonical gate.
    evidence=inspect_campaign(root,previous['campaign'])
    require(evidence['ready'],json.dumps(evidence['blockers']))
    require(evidence['observations_sha256']==previous['observations_sha256'],'Accepted dataset changed since numerical publication')
    protected.update(evidence['protected_hashes'])
    for directory in paths.values():
        for path in directory.iterdir():
            if path.is_file():protected[str(path.resolve())]=_sha(path)
    numeric={}
    def artifact(name):
        entry=previous['artifacts'][name];path=contained(paths[entry['kind']],entry['filename'])
        check_hash(path,entry['sha256'])
        if name.endswith('.csv'):numeric[name]=entry['sha256']
        return path
    c_rows=_read_csv(artifact('convergence_105_statistics.csv'))
    d_rows=_read_csv(artifact('triple_positioning_105.csv'))
    ef_rows=_read_csv(artifact('M0_matched_n_sensitivity_statistics.csv'))
    method=read_json(artifact('statistical_provenance_manifest.json'))
    require(len(c_rows)==808 and len(d_rows)==8 and len(ef_rows)==8,'Canonical plotting CSV coverage mismatch')
    require(method['observations_sha256']==evidence['observations_sha256'],'Statistical provenance dataset mismatch')
    medians={t:statistics.median(r['SSE'] for r in evidence['observations'] if r['treatment']==t) for t in TREATMENTS}
    baseline=medians[TREATMENTS[0]]
    for row in d_rows:
        require(float(row['median_SSE'])==medians[row['treatment']],'Positioning CSV median mismatch')
    for row in ef_rows:
        value=medians[row['treatment']]
        require(float(row['delta_SSE'])==value-baseline and
                float(row['reduction_percent'])==100*(1-value/baseline),'Contrast CSV formula mismatch')
    for row in c_rows:
        values=[r['SSE'] for r in sorted(evidence['observations'],key=lambda r:(r['source_n'],r['source_pub']))
                if r['treatment']==row['treatment']][:int(row['cumulative_pub_count'])]
        require(float(row['median_SSE'])==statistics.median(values),'Prefix CSV median mismatch')
    for name in previous['artifacts']:
        if name.endswith('.csv'):artifact(name)
    references=previous['references']
    prior_method=dict(method)
    if standardize_reference_methods:
        # Fresh canonical reference verification; never force historical image values.
        audited=validated_reference_values(root,evidence)
        protected.update(audited['protected_hashes']);references=audited['references']
        for key in ('measured_M0_median_SSE','classical_SSE','ideal_SSE'):
            require(references.get(key)==previous['references'].get(key),'Verified reference changed: '+key)
        # Recompute with the ORIGINAL ordinary methods and demand exact CSV equivalence.
        verify_plotted_table(artifact('convergence_105_statistics.csv'),figure_c_statistics(evidence['observations']))
        verify_plotted_table(artifact('triple_positioning_105.csv'),figure_d_statistics(evidence['observations'],references))
        refreshed=matched_n_sensitivity(evidence['observations'],replicates=10000,seed=1013)
        for old,new in zip(ef_rows,refreshed['statistics']):
            require(old['treatment']==new['treatment'],'Contrast treatment order changed')
            for key in ('median_SSE','M0_median_SSE','delta_SSE','reduction_percent'):
                require(float(old[key])==new[key],'Central estimate changed: '+key)
        ef_rows=refreshed['statistics'];method=refreshed['manifest']
        reference_sensitivities=True

    version=int(marker['version'])+1
    def filename(name):
        path=Path(name);return f'{path.stem}__v{version:03d}{path.suffix}'
    reserved=list(FIGURE_NAMES)+['comparison_manifest.json','plot_style_manifest.json','summary_report.json','SHA256SUMS.json',
        'M0_matched_n_sensitivity_statistics.csv','statistical_provenance_manifest.json',
        'cluster_bootstrap_n_level_sensitivity.csv','cluster_bootstrap_shared_job_sensitivity.csv',
        'supplementary_sensitivity_manifest.json','reference_method_verification.json']
    while ((data/f'publication_v{version:03d}.json').exists() or
           any((figures if n.endswith('.png') else data).joinpath(filename(n)).exists() for n in reserved)):
        version+=1
    stage=root/'.codex_tmp'/('comparison-style-'+uuid.uuid4().hex);stage.mkdir(parents=True,mode=0o755)
    if standardize_reference_methods:
        write_table(stage/'M0_matched_n_sensitivity_statistics.csv',ef_rows)
        verify_plotted_table(stage/'M0_matched_n_sensitivity_statistics.csv',ef_rows)
        _write_json(stage/'statistical_provenance_manifest.json',method)
        _write_json(stage/'reference_method_verification.json',dict(
            ordinary_C_and_D_recomputed_and_exact_CSV_match=True,central_estimates_unchanged=True,
            prior_cluster_seed=prior_method['seed'],cluster_seed=method['seed'],replicates=method['replicates'],
            contrast_before_percentiles=True,shared_draws_E_F=True,
            figure_methods=figure_method_metadata(),references=references,
            observations_sha256=evidence['observations_sha256']))
    if preserve_figure_c:
        artifact(FIGURE_NAMES[0])
        prior_layout=read_json(artifact('plot_style_manifest.json'))
        layouts={'C':prior_layout['layouts']['C']}
    else:layouts={'C':render_figure_c(c_rows,stage)}
    import gc
    gc.collect()
    layouts['D']=render_figure_d(d_rows,references,stage)
    layouts.update(render_figures_ef(ef_rows,method,stage))
    require(layouts['D']['inches']==layouts['E']['inches']==layouts['F']['inches'] and
            layouts['D']['axes_positions']==layouts['E']['axes_positions']==layouts['F']['axes_positions'],
            'Standalone figure geometry must be identical')
    require(layouts['E']['x_labels']==layouts['F']['x_labels'],'E/F treatment order differs')
    if standardize_reference_methods:
        for letter,contract in figure_method_metadata().items():
            require(any(contract['label'] in text for text in layouts[letter]['figure_text']),
                    'Figure-specific statistical label mismatch: '+letter)
    if reference_sensitivities:
        from dependence_statistics import supplementary_sensitivity
        supplementary=supplementary_sensitivity(evidence['observations'],replicates=method['replicates'],seed=method['seed'])
        write_table(stage/'cluster_bootstrap_n_level_sensitivity.csv',supplementary['equal_weight'])
        write_table(stage/'cluster_bootstrap_shared_job_sensitivity.csv',supplementary['shared_job'])
        _write_json(stage/'supplementary_sensitivity_manifest.json',supplementary['manifest'])
    _write_json(stage/'plot_style_manifest.json',dict(style=PUBLICATION_STYLE,layouts=layouts,
        prior_numerical_CSV_sha256=numeric,primary_statistics_recomputed=bool(standardize_reference_methods),
        ordinary_C_D_verified_unchanged=bool(standardize_reference_methods),
        prior_manifest=dict(path=str(mp),sha256=_sha(mp)),operator_deleted_artifacts=deleted))
    summary=read_json(artifact('summary_report.json'))
    summary.update(preserved_C=bool(preserve_figure_c),styling_only=not (standardize_reference_methods or reference_sensitivities),
                   central_estimates_unchanged=True,numerical_results_unchanged=not standardize_reference_methods,
                   operator_deleted_artifacts=deleted)
    _write_json(stage/'summary_report.json',summary)
    verify_snapshot(protected)
    # Every original CSV is referenced in place; no new numerical dataset exists.
    artifacts={name:entry for name,entry in previous['artifacts'].items()
               if (not name.endswith('.png') or (preserve_figure_c and name==FIGURE_NAMES[0]))
               and name not in ('summary_report.json','plot_style_manifest.json')}
    for source in stage.iterdir():
        destination=(figures if source.suffix=='.png' else data)/filename(source.name)
        with source.open('rb') as src,destination.open('xb') as dst:shutil.copyfileobj(src,dst)
        check_hash(destination,_sha(source))
        artifacts[source.name]=dict(filename=destination.name,kind='figures' if source.suffix=='.png' else 'data',sha256=_sha(destination))
    hashes={str((paths[e['kind']]/e['filename']).resolve()):e['sha256'] for e in artifacts.values()}
    manifest=dict(previous,schema_version=4,version=version,published_at=datetime.now(timezone.utc).isoformat(),
        state='COMPLETE',blockers={},evidence=evidence,artifacts=artifacts,artifacts_sha256=hashes,
        source_hashes=protected,preserved_artifacts={FIGURE_NAMES[0]:artifacts[FIGURE_NAMES[0]]} if preserve_figure_c else {},publication_style=PUBLICATION_STYLE,
        styling_only=not (standardize_reference_methods or reference_sensitivities),
        prior_numerical_CSV_sha256=numeric,missing_prior_artifacts=deleted,
        previous_publication_manifest=dict(path=str(mp),sha256=_sha(mp)))
    manifest['numerical_CSV_sha256']={name:entry['sha256'] for name,entry in artifacts.items() if name.endswith('.csv')}
    if standardize_reference_methods:
        manifest.update(figure_methods=figure_method_metadata(),references=references,cluster_bootstrap=method,
            experiment='Exp. 01 - IBM Batch / Python-Based Automation System',
            method_summary='C: ordinary pooled-prefix bootstrap, 3,000 replicates. D: ordinary PUB-level bootstrap, 3,000 replicates. '
                           'E/F: matched n-level cluster bootstrap, 10,000 replicates. Seed 1013 throughout; conditional sensitivity intervals, no automatic significance.',
            reference_method_standardization=True,central_estimates_unchanged=True)
    manifest['source_code_sha256']={name:_sha(Path(name)) for name in previous['source_code_sha256']}
    dashboard=root/'02_Hardware/04_code/MXene_Dashboard_Dev'
    manifest['dashboard_source_sha256']={str(p):_sha(p) for p in
        (dashboard/'static/comparison.js',dashboard/'app.py',dashboard/'reader.py') if p.is_file()}
    new_manifest=data/filename('comparison_manifest.json');_write_json(new_manifest,manifest)
    hashes={**hashes,str(new_manifest.resolve()):_sha(new_manifest)}
    new_hashes=data/filename('SHA256SUMS.json');_write_json(new_hashes,hashes)
    verify_snapshot(hashes);verify_snapshot(protected)
    _write_json(data/f'publication_v{version:03d}.json',dict(version=version,published_at=manifest['published_at'],
        manifest=new_manifest.name,manifest_sha256=_sha(new_manifest),hash_inventory=new_hashes.name,hash_inventory_sha256=_sha(new_hashes)))
    require(stage.resolve().parent==(root/'.codex_tmp').resolve() and stage.name.startswith('comparison-style-'),
            'Refusing cleanup outside comparison scratch directory')
    shutil.rmtree(stage)
    return dict(state='COMPLETE',version=version,manifest=str(new_manifest),operator_deleted_pngs=len(deleted),
                figures=[str(figures/artifacts[name]['filename']) for name in FIGURE_NAMES])


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=repository_root())
    parser.add_argument('--campaign',default='vqe-independent-n-01')
    parser.add_argument('--authorize-output-write',action='store_true')
    parser.add_argument('--restyle-only',action='store_true')
    parser.add_argument('--preserve-figure-c',action='store_true')
    parser.add_argument('--standardize-reference-methods',action='store_true')
    parser.add_argument('--reference-sensitivities',action='store_true')
    parser.add_argument('--acknowledge-deleted-comparison-pngs','--acknowledge-missing-comparison-pngs',action='store_true')
    args=parser.parse_args(argv)
    if args.restyle_only or args.standardize_reference_methods:
        result=restyle_publication(args.project,authorize_output_write=args.authorize_output_write,
            acknowledge_deleted_comparison_pngs=args.acknowledge_deleted_comparison_pngs,
            reference_sensitivities=args.reference_sensitivities,
            standardize_reference_methods=args.standardize_reference_methods,preserve_figure_c=args.preserve_figure_c)
    elif args.authorize_output_write:
        result=publish(args.project,args.campaign,authorize_output_write=True)
    else:
        result=inspect_campaign(args.project,args.campaign)
        result={k:v for k,v in result.items() if k not in ('observations','protected_hashes')}
    print(json.dumps(result,indent=2,allow_nan=False))
    return 0 if result.get('state') in ('READY_TO_COMPARE','COMPLETE') else 20


if __name__=='__main__':raise SystemExit(main())
