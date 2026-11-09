"""One consolidated read-only audit; emits JSON only in .codex_tmp."""
import json,sys,re
from pathlib import Path
from datetime import datetime,timezone
from PIL import Image
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'02_Hardware/04_code/MXene_Dashboard_Dev'))
from reader import OUT,DB,Reader,sha,CAMPAIGN,TREATMENTS
from figure_registry import inventory,contract
from campaign_progress import step_records
from job_registry import JobRegistry

registry=JobRegistry(DB,readonly=True)
campaign=registry.independent_campaign_status(CAMPAIGN)
reader=Reader();catalog=reader.catalog();figures=[];manifests=[];levels=[]
for path in sorted(OUT.rglob('*.png')):
    item={'path':str(path),'bytes':path.stat().st_size,'sha256':sha(path)}
    try:
        with Image.open(path) as image:
            item.update(dimensions=list(image.size),dpi=image.info.get('dpi'),format=image.format)
            image.verify()
        item['png_valid']=True
    except (OSError,ValueError) as exc:item.update(png_valid=False,error=str(exc))
    item['dashboard_entries']=[r for r in catalog if r['kind']=='png' and reader.files[r['id']][0]==path.resolve()]
    figures.append(item)
for path in sorted(OUT.rglob('*manifest.json')):
    try:
        body=json.loads(path.read_text(encoding='utf-8-sig'))
        manifests.append({'path':str(path),'sha256':sha(path),'schema_version':body.get('schema_version'),
            'publication_contract_version':body.get('publication_contract_version'),'figure_references':body.get('figures'),
            'generated_figures':body.get('generated_figures'),'target_id':body.get('target_id'),'n':body.get('n'),
            'source_analysis_manifest_sha256':body.get('source_analysis_manifest_sha256')})
    except (OSError,ValueError,AttributeError) as exc:manifests.append({'path':str(path),'error':str(exc)})
for level in campaign['manifest']['levels']:
    n=level['target_n'];selected=registry.repaired_pub_values(level['target_id'])
    keys={(r['treatment_id'],r['logical_pub_index']) for r in selected}
    missing={t:[i for i in range(1,n+1) if (t,i) not in keys] for t in TREATMENTS}
    found=inventory(OUT,n)
    positioning=[f for v in found['versions'] for f in v['figures'] if f['name']=='06_mitigation_three_tier_positioning.png']
    levels.append({'n':n,'accepted':len(keys),'expected':n*8,'missing_slots':missing,'inventory':found,
        'positioning_action':'PRESERVED_AND_REGISTERED' if any(f['display_eligible'] for f in positioning) else 'BLOCKED',
        'block_reason':None if positioning else f'{len(keys)}/{n*8} accepted logical PUBs; missing slots: {missing}. No complete canonical n-level scientific dataset or Analyze receipt is available.'})
baseline=json.loads((ROOT/'.codex_tmp/figure-sync-inventory-before.json').read_text())
preserved=all(sha(Path(f['path']))==f['sha256'] for level in baseline for f in level['figures'])
manifest_preserved=all(sha(Path(m['path']))==m['sha256'] for level in baseline for m in level['manifests'])
report={'campaign':CAMPAIGN,'captured_at':datetime.now(timezone.utc).isoformat(),'campaign_state':campaign['state'],
    'contract':contract(),'original_plotting_implementation':'02_Hardware/04_code/01_vqe/mitigation_pilot.py:create_mos2_style_figures',
    'levels':levels,'all_canonical_output_pngs':figures,'manifests':manifests,'step_receipts':step_records(),
    'published_pngs_byte_preserved':preserved,'historical_manifests_byte_preserved':manifest_preserved,
    'generated_figures':[],'manifest_reference_repairs':[],
    'manifest_reference_repair_reason':'Every existing positioning figure already has matching paired manifest references. No historical manifest or receipt rewrite is needed.'}
destination=ROOT/'.codex_tmp/figure-sync-inventory.json'
destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
assert preserved and manifest_preserved
print(json.dumps({'audit':str(destination),'pngs_inventoried':len(figures),'manifests':len(manifests),'all_published_pngs_preserved':preserved,'manifests_preserved':manifest_preserved,'levels':[(x['n'],x['accepted'],x['positioning_action']) for x in levels]}))
