"""Plan or explicitly authorize missing canonical analysis/export reconstruction.

Fixed scope: vqe-independent-n-01, n=5..25. No IBM calls, no n=30 writes,
no comparison generation. Existing files and receipts are never replaced.
"""
import argparse
import json
from pathlib import Path
import sys

AI=Path(__file__).resolve().parents[1]
if str(AI) not in sys.path:sys.path.append(str(AI))
from campaign_comparison import inspect_campaign,require,read_json,check_hash,verify_snapshot,_sha
from scientific_exports import inspect_png
from repository_paths import MITIGATION_REL,repository_root,contained
from step_artifacts import artifact_hashes

CAMPAIGN='vqe-independent-n-01'
REPAIR_LEVELS=(5,10,15,20,25)


def action_for_level(level):
    """Fail closed on corrupt existing bytes; a missing upstream tree may be rebuilt."""
    if level['ready']:return 'SKIP_READY'
    require(level.get('scientific_complete') and level.get('selected_archives_verified') and
            level.get('accepted')==level.get('expected') and not level.get('duplicate_slots'),
            f'n={level["n"]}: accepted dataset/archive gate failed')
    require(level.get('dataset_sha256'), 'Verified dataset fingerprint is absent')
    receipt_path=level.get('analysis_receipt')
    if receipt_path:
        receipt=read_json(receipt_path);output=Path(receipt['output'])
        # Missing artifacts are distinct from changed bytes. Never reinterpret a
        # conflicting stored hash as permission to replace an existing artifact.
        for name,expected in receipt['artifact_sha256'].items():
            path=contained(output,name)
            if path.is_file():check_hash(path,expected)
        if (output/'analysis_manifest.json').is_file():
            require(artifact_hashes(output)==receipt['artifact_sha256'],
                    'Partially present analysis with a manifest requires explicit diagnosis')
            return 'EXPORT_ONLY'
    require(level.get('candidates') and all(c['actual_sha256'] is None for c in level['candidates']),
            f'n={level["n"]}: an existing analysis manifest requires explicit reuse/repair diagnosis')
    require(not level.get('existing_analysis_manifests'),
            f'n={level["n"]}: existing unbound analysis must be verified for reuse before regenerating')
    return 'NEW_VERSION_ANALYZE_AND_EXPORT'


def repair_plan(evidence):
    require({l['n'] for l in evidence['levels']}=={5,10,15,20,25,30},'Require all six audited levels')
    require(next(l for l in evidence['levels'] if l['n']==30)['ready'],'n=30 must remain verified before repair')
    return [{'n':l['n'],'action':action_for_level(l),'dataset_sha256':l['dataset_sha256'],
             'target_id':l['target_id'],'historical_receipt':l.get('analysis_receipt'),
             'analysis_path_pattern':f'02_run_level_data/adaptive_targets/{l["target_id"]}_vNNN-<usage-contract-hash>',
             'export_path_pattern':f'01_results_and_figures/n={l["n"]}/vNNN-<analysis-hash> and 02_run_level_data/n={l["n"]}/vNNN-<analysis-hash>'}
            for l in evidence['levels'] if l['n'] in REPAIR_LEVELS]


def protected_inventory(root, evidence):
    """Preserve every preexisting analysis/publication file, including partial trees."""
    output=root/MITIGATION_REL/'output'
    hashes=dict(evidence['protected_hashes'])
    folders=[output/'01_results_and_figures',output/'02_run_level_data',
             output/'03_IBM_execution_records/ai_assistant/independent_n_campaigns'/CAMPAIGN/'steps']
    for folder in folders:
        for p in folder.rglob('*'):
            if p.is_file():hashes[str(p.resolve())]=_sha(p)
    return hashes


def cost_png_plan(root):
    """Recover only exact manifest-bound bytes; never estimate or redraw QPU cost."""
    output=Path(root)/MITIGATION_REL/'output';plans=[]
    name='04_mitigation_accuracy_cost.png'
    for ep in sorted((output/'02_run_level_data/n=5').glob('**/export_manifest.json')):
        em=read_json(ep);fig=Path(em['figure_directory']);fp=fig/'export_manifest.json'
        if not fp.is_file() or name not in em.get('figures',{}):continue
        require(read_json(fp)==em,'n=5 paired manifests differ; cannot restore cost artifact')
        expected_dir=output/'01_results_and_figures/n=5'/ep.parent.relative_to(output/'02_run_level_data/n=5')
        require(fig.resolve()==expected_dir.resolve(),'Cost destination outside canonical paired export')
        target=fig/name;digest=em['figures'][name]['sha256']
        if target.exists():
            check_hash(target,digest);continue
        matches=[p for p in (Path(root)/'.codex_tmp').glob(
            '*/analysis/vqe-independent-n-01-n5-target-n5-chunk-5/comparison_figures/'+name)
                 if p.is_file() and _sha(p)==digest]
        require(matches,'No exact n=5 cost PNG copy found; no redraw or usage fabrication is authorized')
        source=sorted(matches)[0];inspect_png(source,layout='landscape')
        plans.append({'source':str(source.resolve()),'target':str(target.resolve()),'sha256':digest,
                      'export_manifest':str(ep.resolve()),'export_manifest_sha256':_sha(ep)})
    return plans


def restore_cost_png(plan):
    from repository_paths import atomic_archive
    source=Path(plan['source']);target=Path(plan['target'])
    check_hash(Path(plan['export_manifest']),plan['export_manifest_sha256'])
    check_hash(source,plan['sha256'])
    if target.exists():check_hash(target,plan['sha256']);return
    inspect_png(source,layout='landscape')
    atomic_archive(target,(source.read_bytes(),),plan['sha256'])
    check_hash(target,plan['sha256']);inspect_png(target,layout='landscape')


def execute_plan(root, evidence, plan, *, authorize=False):
    if authorize is not True:raise PermissionError('Explicit --authorize-repair-writes required')
    from research_pipeline import execute_campaign_step
    root=repository_root(root);protected=protected_inventory(root,evidence);results=[]
    initial={l['n']:l['dataset_sha256'] for l in evidence['levels']}
    try:
        for png in cost_png_plan(root):
            restore_cost_png(png);protected[png['target']]=png['sha256']
            results.append({'operation':'restore_exact_n5_cost_png',**png})
        for item in plan:
            n=item['n']
            require(n in REPAIR_LEVELS,'Repair scope excludes n=30')
            if item['action']=='SKIP_READY':continue
            # A fresh read-only audit before each level detects dataset drift and
            # safely skips work another authorized process has already completed.
            current=inspect_campaign(root,CAMPAIGN)
            require(all(l.get('dataset_sha256')==initial[l['n']] for l in current['levels']),
                    'Accepted dataset changed; stop and obtain a new repair plan')
            current_plan=repair_plan(current)
            action=next(i['action'] for i in current_plan if i['n']==n)
            if action=='SKIP_READY':continue
            require(action==item['action'],'Repair prerequisites changed; re-plan')
            verify_snapshot(protected)
            if action=='NEW_VERSION_ANALYZE_AND_EXPORT':
                result=execute_campaign_step(root,CAMPAIGN,'analyze',n=n,
                    authorize_step=True,authorize_output_write=True,new_analysis_version=True)
                require(result['result']['state']=='ANALYZED','Canonical Analyze did not succeed')
                results.append(result)
                # Protect new artifacts too before Export and all later work.
                out=Path(result['result']['analysis_output'])
                protected.update({str(contained(out,name)):digest for name,digest in artifact_hashes(out).items()})
                receipt=result['result']['analysis_receipt'];protected[receipt['path']]=receipt['sha256']
            result=execute_campaign_step(root,CAMPAIGN,'export',n=n,
                authorize_step=True,authorize_output_write=True)
            require(result['result']['state']=='EXPORTED','Canonical Export did not succeed')
            results.append(result);verify_snapshot(protected)
        final=inspect_campaign(root,CAMPAIGN)
        require(all(l.get('dataset_sha256')==initial[l['n']] for l in final['levels']),
                'Accepted dataset changed during repair')
        require(final['ready'],json.dumps(final['blockers']))
        return {'state':'READY_TO_COMPARE','operations':results,'verified_n_levels':final['verified_n_levels'],
                'preserved_existing_files':len(protected),'comparison_executed':False}
    finally:
        verify_snapshot(protected)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=Path.cwd())
    parser.add_argument('--authorize-repair-writes',action='store_true')
    args=parser.parse_args(argv);root=repository_root(args.project)
    evidence=inspect_campaign(root,CAMPAIGN)
    plan=repair_plan(evidence)
    if not args.authorize_repair_writes:
        print(json.dumps({'state':'REPAIR_PLAN_REQUIRES_AUTHORIZATION','production_writes':0,
                          'verified_n_levels':evidence['verified_n_levels'],'plan':plan,
                          'separate_cost_png_restore':cost_png_plan(root)},indent=2))
        return 0
    result=execute_plan(root,evidence,plan,authorize=True)
    from offline_tools import comparison_readiness,CampaignRequest
    final=comparison_readiness(CampaignRequest(root,CAMPAIGN))
    require(final['ready'],json.dumps(final['blockers']))
    result['existing_comparison_checker']=final
    print(json.dumps(result,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
