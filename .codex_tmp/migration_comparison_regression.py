"""Offline old/new C/D output equivalence using isolated synthetic sources."""
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path.cwd();AI=ROOT/'02_Hardware/04_code/03_ai_assistant'
AUDIT=Path((ROOT/'.codex_tmp/migration_audit.txt').read_text())
sys.path.insert(0,str(AI))
from low_memory_runtime import limit_native_threads
limit_native_threads()
import numpy as np
import pandas as pd
import scientific_exports as canonical_exports


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module);return module


def fixture(module, directory):
    base=directory/'a/b/c';results=base/'results'
    module.BASE=base;module.DATA=results/'02_run_level_data'
    module.FIG=results/'01_results_and_figures/comparison_105_pubs'
    module.TABLES=module.DATA/'comparison_105_pubs'
    datasets={}
    treatments=getattr(module,'COMPARISON_TREATMENTS',module.TREATMENTS)
    frequency=np.arange(1,133,dtype=float)
    curves={'Experimental':np.ones(132,dtype=complex), 'Ideal':np.ones(132,dtype=complex)*1.1}
    ideal=float(np.sum(np.abs(curves['Ideal']-curves['Experimental'])**2))
    for n in module.NS:
        rows=[dict(treatment=t,pub=p,SSE=float(100+7*i+p),job_id=f'fake-{n}-{i}')
              for i,(t,_) in enumerate(treatments) for p in range(1,n+1)]
        frame=pd.DataFrame(rows);folder=module.DATA/f'n={n}';folder.mkdir(parents=True)
        frame.to_csv(folder/'per_repeat.csv',index=False)
        datasets[n]=(frame,{'ideal_SSE':ideal},frequency,curves)
        (results/'01_results_and_figures'/f'n={n}').mkdir(parents=True)
        for i,(t,_) in enumerate(treatments):
            d=results/'03_IBM_execution_records'/f'n={n}_IBM_job_infor_recorded'/t
            d.mkdir(parents=True)
            (d/'job_retrieved_metadata.json').write_text(json.dumps(dict(job_id=f'fake-{n}-{i}',account_label='fixture')))
    (directory/'data').mkdir(parents=True);(directory/'data/EIS14.csv').write_text('synthetic reference fixture')
    (directory/'src/common').mkdir(parents=True);(directory/'src/common/impedance_model.py').write_text('# fixture identity only; no model execution')
    exports=types.ModuleType('scientific_exports');exports.STYLE={}
    exports.digest=canonical_exports._sha
    exports.read_json=lambda p:json.loads(Path(p).read_text())
    def require(condition,message):
        if not condition:raise ValueError(message)
    exports.require=require
    exports.equal=lambda a,b,message:require(np.allclose(a,b),message)
    exports.validate_sources=lambda root,ns:(None,datasets)
    exports.validate_folder=lambda p:require(p.is_dir(),'Missing figure folder')
    def verify_snapshot(root,values):
        for p,h in values.items():require(canonical_exports._sha(root/p)==h,'Protected file changed')
    exports.verify_snapshot=verify_snapshot
    def save(fig,folder,name):
        import matplotlib.pyplot as plt
        try:fig.savefig(folder/name,dpi=600,facecolor='white')
        finally:plt.close(fig)
    exports.save=save
    exports.validate_png=lambda p:canonical_exports.inspect_png(p,layout='landscape')
    return exports


class ComparisonMigrationRegression(unittest.TestCase):
    def test_c_and_d_match_original_science_and_preserve_sources(self):
        old=load('original_comparison',AUDIT/'source-before/02_Hardware/04_code/01_vqe/compare_105_pubs.py')
        new=load('migrated_comparison',AI/'03_python_automation/dependence_statistics.py')
        reports=[]
        for name,module in [('before',old),('after',new)]:
            exports=fixture(module,AUDIT/'comparison-fixtures'/name)
            with patch('socket.socket.connect',side_effect=AssertionError('offline only')):
                module.generate_figure_c();gc.collect()
                before_c=exports.digest(module.FIG/'Figure_C_105_PUB_convergence.png')
                with patch.dict(sys.modules,{'scientific_exports':exports}):module.generate_figure_d()
                self.assertEqual(before_c,exports.digest(module.FIG/'Figure_C_105_PUB_convergence.png'))
            gc.collect()
            manifest=json.loads((module.TABLES/'triple_positioning_manifest.json').read_text())
            self.assertEqual(manifest['reference_type'],'measured_M0_median')
            self.assertEqual(len(manifest['source_observations']),840)
            pngs={p.name:dict(sha256=exports.digest(p),inspection=canonical_exports.inspect_png(p,layout='landscape')) for p in module.FIG.glob('*.png')}
            tables={p.name:exports.digest(p) for p in module.TABLES.glob('*.csv')}
            reports.append(dict(pngs=pngs,tables=tables,results=manifest['results']))
        self.assertEqual(reports[0],reports[1])
        (AUDIT/'comparison-equivalence.json').write_text(json.dumps(dict(
            passed=True,network_forbidden=True,synthetic_sources=True,production_sources_validated=False,
            preserved_C=True,reference_type='measured_M0_median',**reports[1]),indent=2))


if __name__=='__main__':unittest.main(verbosity=2)
