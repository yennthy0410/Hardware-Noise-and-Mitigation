"""Offline D/E/F provenance investigation. No fitting, bootstrap or publication.

The optional writer creates only new audit CSV/JSON, never an active publication
marker. Search results identify candidates, not scientific acceptance.
"""
from pathlib import Path
import collections
import json
import os
import statistics
import subprocess

import campaign_comparison as c
from repository_paths import comparison_output_paths, repository_root


def trace_m0(rows):
    from dependence_statistics import validate_observations
    validate_observations(rows)
    selected=sorted((r for r in rows if r['treatment']==c.TREATMENTS[0]),
                    key=lambda r:(r['SSE'],r['source_n'],r['source_pub']))
    c.require(len(selected)==105,'M0 trace requires 105 accepted observations')
    median=statistics.median(r['SSE'] for r in selected)
    return dict(count=105,formula='median of 105 pooled SSE values = sorted order statistic 53',
                median_SSE=median,median_observation=selected[52],
                adjacent_order_statistics=selected[51:54],
                level_medians={str(n):statistics.median(r['SSE'] for r in selected if r['source_n']==n)
                               for n in c.LEVELS}),selected


def search_repository(root, patterns):
    exclusions=['!**/.venv*/**','!**/site-packages/**','!**/node_modules/**']
    found={}
    for name,pattern in patterns.items():
        command=['rg','-l','--hidden','--no-ignore','-i',pattern,'.']
        for exclusion in exclusions:command+=['-g',exclusion]
        result=subprocess.run(command,cwd=root,capture_output=True,text=True,encoding='utf-8')
        if result.returncode not in (0,1):raise RuntimeError(result.stderr)
        found[name]=[dict(path=str((root/p).resolve()),sha256=c._sha(root/p))
                     for p in sorted(result.stdout.splitlines())]
    files=[];archives=[];figures=[];datasets=[];errors=[]
    def error(exc):errors.append(str(exc))
    for directory,dirs,names in os.walk(root,onerror=error):
        dirs[:]=[d for d in dirs if not d.startswith('.venv') and d not in ('site-packages','node_modules')]
        for name in names:
            path=Path(directory)/name;files.append(str(path.relative_to(root)))
            if path.suffix.lower() in ('.zip','.tar','.gz','.tgz','.7z','.rar'):archives.append(str(path))
            if name.lower().startswith(('figure_e','figure_f')):figures.append(str(path))
            if name=='source_105_pubs.csv':
                rows=c._read_csv(path);values=[float(r['SSE']) for r in rows if r['treatment']==c.TREATMENTS[0]]
                datasets.append(dict(path=str(path),sha256=c._sha(path),rows=len(rows),M0_count=len(values),
                                     M0_median=statistics.median(values),
                                     synthetic_fixture='comparison-fixtures' in path.parts))
    c.require(not errors,'Repository search incomplete: '+repr(errors))
    return dict(file_count=len(files),excluded_directories=exclusions,
                text_search='ripgrep hidden/no-ignore; binary content excluded, filenames inventoried',
                archive_candidates=archives,EF_filename_candidates=figures,
                source_tables=datasets,matches=found)


def investigate(root):
    root=repository_root(root);paths=comparison_output_paths(root)
    published=c.published_comparison(root)
    # Use the same committed manifest as the Dashboard, without changing it.
    marker=paths['data']/f"publication_v{published['version']:03d}.json"
    commit=c.read_json(marker);manifest_path=paths['data']/commit['manifest']
    c.check_hash(manifest_path,commit['manifest_sha256']);manifest=c.read_json(manifest_path)
    c.verify_snapshot(manifest['source_hashes'])
    rows=manifest['evidence']['observations'];trace,selected=trace_m0(rows)
    claim,claim_source=c.comparison_reference_claim(root)
    legacy=c.reference_audit(root,manifest['evidence'])
    search=search_repository(root,dict(
        supplied_M0=format(claim,'.16g'),accepted_M0=format(trace['median_SSE'],'.16g'),
        matched_method=r'matched.{0,35}(cluster|bootstrap|n.level)|cluster.{0,35}(matched|n.level)|M0_cluster_bootstrap',
        bootstrap_code=r'bootstrap|cluster'))
    chosen=trace['median_observation'];level=next(r for r in manifest['evidence']['levels'] if r['n']==chosen['source_n'])
    export=Path(level['export']);table=export.parent/'per_repeat.csv'
    c.check_hash(table,c.read_json(export)['csv_sha256']['per_repeat.csv'])
    fit=[r for r in c._read_csv(table) if r['treatment']==chosen['treatment'] and
         r['IBM_job_id']==chosen['job_id'] and float(r['SSE'])==chosen['SSE']]
    c.require(len(fit)==1,'Cannot trace the median observation to a unique canonical fit row')
    trace.update(claim=claim,claim_source=claim_source,difference=trace['median_SSE']-claim,
                 claimed_value_in_selected_M0=any(r['SSE']==claim for r in selected),
                 source_table=dict(path=str(table),sha256=c._sha(table),row=fit[0]),
                 analysis_directory=level['analysis'],analysis_sha256=level['analysis_sha256'],
                 export_manifest=str(export),export_sha256=level['export_sha256'],
                 analysis_receipt=level['analysis_receipt'])
    jobs=collections.defaultdict(list)
    for r in rows:jobs[r['job_id']].append(r)
    shared=[dict(job_id=j,logical_observations=len(rs),n_levels=sorted({r['source_n'] for r in rs}),
                 treatments=sorted({r['treatment'] for r in rs})) for j,rs in jobs.items() if len(rs)>1]
    recovery=[dict(job_id=j,observations=len(rs),n_levels=sorted({r['source_n'] for r in rs}))
              for j,rs in jobs.items() if any(r['source']=='REPLACEMENT' for r in rs)]
    report=dict(schema_version=1,state='BLOCKED_REFERENCE',campaign=manifest['campaign'],
        active_publication_version=published['version'],active_manifest_sha256=c._sha(manifest_path),
        observations_sha256=manifest['observations_sha256'],M0=trace,search=search,
        historical_observations_and_provenance_exact_match=True,
        root_cause='Untraceable supplied reference versus reproducible accepted-data pooled median; '
                   'no evidence identifies how the supplied value was calculated or which data version produced it.',
        EF=dict(original_recovered=False,numerical_equivalence='Not testable: no original CSV/PNG found',
            verified_design_membership={str(n):{t:sum(r['source_n']==n and r['treatment']==t for r in rows)
                                               for t in c.TREATMENTS} for n in c.LEVELS},
            available_method='dependence_statistics.analyze: unpaired reviewed execution clusters; not matched n-level blocks',
            unknown_original_details=['seed and random generator','exact matched resampling rule and RNG traversal',
                'pooled-median versus block-summary estimand and weighting','difference sign and percentage denominator',
                'percentile interpolation convention','original reference data selection/hashes',
                'justification for six exchangeable independent design blocks and treatment matching'],
            requested_but_not_original_verified=dict(replicates=10000,interval='95% percentile',blocks=6),
            shared_jobs=shared,replacement_jobs=recovery,
            limitation='No replacement job ID repeats in the selected rows; this does not establish independence. '
                       'Multiple original observations share job IDs. Shared calibration, recovery selection and '
                       'provider-level dependence remain possible; matching n labels does not verify exchangeability.'),
        proposed_separate_version=dict(state='PROPOSAL_ONLY_NOT_EXECUTED',name='matched-n sensitivity v2',
            seed_policy='Choose and record a new seed explicitly; original seed unknown',replicates=10000,
            resampling='Draw six n labels with replacement from the six observed labels, use the same draw '
                       'for every treatment, and retain all accepted observations in each sampled block.',
            estimand='Median of concatenated observations; retain unequal block sizes, not mean of block medians.',
            delta_SSE='median_SSE_treatment - median_SSE_M0 in each shared replicate',
            reduction_percent='100 * (1 - median_SSE_treatment / median_SSE_M0) in each shared replicate',
            interval='2.5th/97.5th percentiles, linear quantile interpolation, explicitly labeled sensitivity intervals',
            requirements='Review whether n-level matching is scientifically meaningful; otherwise retain descriptive '
                         'per-n contrasts without bootstrap intervals. Do not label as original reproduction, '
                         'independent experimental CI, or multiplicity-adjusted significance.'),
        publication_readiness={letter:'BLOCKED' for letter in ('D','E','F')},
        protected_hashes={**manifest['source_hashes'],**legacy['protected_hashes'],
                          str(marker):c._sha(marker),str(manifest_path):c._sha(manifest_path)})
    c.verify_snapshot(report['protected_hashes'])
    return report,selected


def write_audit(root,report,selected):
    output=comparison_output_paths(root)['data'];version=1
    def names():return [output/f'{stem}_v{version:03d}.{ext}' for stem,ext in
        [('reference_blocker_audit','json'),('M0_selected_observations_audit','csv'),('reference_blocker_audit_hashes','json')]]
    while any(p.exists() for p in names()):version+=1
    audit,table,hashes=names();c.verify_snapshot(report['protected_hashes'])
    c.write_table(table,[dict(sorted_rank=i+1,**row) for i,row in enumerate(selected)])
    report=dict(report,audit_source_code_sha256=c._sha(Path(__file__)),
                selected_observations_csv=dict(path=str(table),sha256=c._sha(table)))
    c._write_json(audit,report)
    c._write_json(hashes,{str(p):c._sha(p) for p in (audit,table)})
    c.verify_snapshot(c.read_json(hashes));c.verify_snapshot(report['protected_hashes'])
    return [str(p) for p in (audit,table,hashes)]


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=repository_root())
    parser.add_argument('--write-audit',action='store_true')
    args=parser.parse_args();report,selected=investigate(args.project)
    print(json.dumps(dict(state=report['state'],M0=report['M0'],
        output_paths=write_audit(args.project,report,selected) if args.write_audit else []),indent=2))
