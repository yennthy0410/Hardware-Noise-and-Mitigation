"""GET-only verification and immutable-file fingerprints; writes only local evidence."""
import json, os, sys, urllib.request
from pathlib import Path
from reader import HERE,PROD,OUT,ROOT,Reader,sha
os.chdir(ROOT.parent)
baseline=HERE/'state/source_hashes.json'
if '--baseline' in sys.argv:
    r=Reader();r.catalog()
    paths={p for p,d in r.files.values()}
    paths.update(PROD.glob('03_python_automation/*.py'))
    paths.update(PROD.glob('01_ai_agent/*.py'))
    paths.update((OUT/'03_IBM_execution_records/locked_inputs').glob('*'))
    hashes={str(p):sha(p) for p in paths if p.is_file()}
    baseline.write_text(json.dumps(hashes,indent=2))
    print('Baseline immutable files:',len(hashes))
else:
    hashes=json.loads(baseline.read_text())
    changed=[p for p,h in hashes.items() if sha(Path(p))!=h]
    def get(path):
        with urllib.request.urlopen('http://127.0.0.1:8766'+path,timeout=25) as response:return response.read()
    status=json.loads(get('/api/status'))
    assert not status['offline'],status
    image=next(a for a in status['artifacts'] if a['kind']=='png')
    import hashlib
    assert hashlib.sha256(get('/api/artifacts/'+image['id'])).hexdigest()==image['sha256']
    table=next(a for a in status['artifacts'] if a['kind']=='csv')
    table_response=json.loads(get('/api/tables/'+table['id']))
    result={'source_files_checked':len(hashes),'changed_sources':changed,'health':json.loads(get('/api/health')),'valid':status['valid'],'recovered':status['recovered'],'missing':status['missing'],'jobs_displayed':len(status['jobs']),'artifacts':len(status['artifacts']),'png_verified':image['name'],'table_verified':table['name'],'table_rows':len(table_response['rows']),'campaign':status['campaign']}
    (HERE/'state/verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    assert not changed
