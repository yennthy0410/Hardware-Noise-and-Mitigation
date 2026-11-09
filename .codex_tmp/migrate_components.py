"""One-time, hash-guarded migration. Requires the fresh offline baseline first."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path.cwd()
AI = ROOT/'02_Hardware/04_code/03_ai_assistant'
AUDIT = Path((ROOT/'.codex_tmp/migration_audit.txt').read_text())
BEFORE = json.loads((AUDIT/'before.json').read_text())

def unchanged(path):
    expected = BEFORE['source_hashes'][path.relative_to(ROOT).as_posix()]
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError('Concurrent/source change detected: '+str(path))

def bootstrap(text):
    tree = ast.parse(text)
    end = 0
    for node in tree.body:
        if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str) and end == 0:
            end = node.end_lineno
        elif isinstance(node,ast.ImportFrom) and node.module=='__future__':
            end = node.end_lineno
        else: break
    lines=text.splitlines(keepends=True)
    lines.insert(end, '\n# Preserve flat import names and direct-script startup after relocation.\n'
        'import sys as _migration_sys\nfrom pathlib import Path as _MigrationPath\n'
        '_migration_root = str(_MigrationPath(__file__).resolve().parents[1])\n'
        'if _migration_root not in _migration_sys.path:\n    _migration_sys.path.insert(0, _migration_root)\n\n')
    return ''.join(lines)

replacements = {
 'experiment_manifest.py': [('parents[1] / "01_vqe"','parents[2] / "01_vqe"')],
 'vqe_adapter.py': [('parents[1] / "01_vqe"','parents[2] / "01_vqe"')],
 'job_registry.py': [('parents[1] / "01_vqe"','parents[2] / "01_vqe"')],
 'low_memory_runtime.py': [(".parent.parent/'01_vqe'",".parents[2]/'01_vqe'")],
 'repository_paths.py': [(".parent.parent/'01_vqe'",".parents[2]/'01_vqe'")],
 'research_pipeline.py': [('AI_DIR=Path(__file__).resolve().parent','AI_DIR=Path(__file__).resolve().parents[1]')],
 'check_install.py': [('default=Path(__file__).resolve().parent)','default=Path(__file__).resolve().parents[1])')],
 'diagnostic_manager.py': [(".parents[3] / 'manager_diagnostics'",".parents[4] / 'manager_diagnostics'"),
                           ("Path(__file__).with_name('mxene.py')","Path(__file__).resolve().parents[1]/'mxene.py'")],
 'mxene.py': [(".parent/'01_ai_agent'/'offline_tools.py'",".parents[1]/'01_ai_agent'/'offline_tools.py'")],
}

sources=sorted(p for p in AI.glob('*.py') if p.name not in ('__init__.py','_migration_bridge.py'))
assert len(sources)==26, len(sources)
for p in sources:unchanged(p)
stats=AI/'03_python_automation/dependence_statistics.py'
compare=AI.parent/'01_vqe/compare_105_pubs.py'
unchanged(stats);unchanged(compare)
for p in sources:
    dest=AI/('01_ai_agent' if p.name in ('mxene.py','ai_assistance.py') else '03_python_automation')/p.name
    if dest.exists():raise FileExistsError(dest)
    text=p.read_text(encoding='utf-8-sig')
    for old,new in replacements.get(p.name,[]):
        assert old in text,(p.name,old)
        text=text.replace(old,new)
    text=bootstrap(text)
    ast.parse(text)
    dest.write_text(text,encoding='utf-8')

# Keep the validated E/F functions intact. C/D numerical statements are copied
# from the audited script; only imports and the treatment-constant name change.
old_compare=compare.read_text(encoding='utf-8-sig')
tree=ast.parse(old_compare)
parts=[]
for node in tree.body:
    if isinstance(node,ast.FunctionDef):
        body=ast.get_source_segment(old_compare,node).replace('TREATMENTS','COMPARISON_TREATMENTS')
        lines=body.splitlines()
        # Both original functions have a one-line docstring.
        lines.insert(2,'    np, pd, plt = _comparison_imports()')
        if node.name=='generate_figure_c':lines.insert(3,'    CHECKPOINTS = np.cumsum(NS)')
        parts.append('\n'.join(lines))
constants=next(ast.get_source_segment(old_compare,n) for n in tree.body
    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TREATMENTS' for t in n.targets))
extra='''

# Figure C/D compatibility contract. Keep the original BASE/results locations;
# production adaptation is blocked until logical-slot provenance is verified.
from pathlib import Path
import os
NS = LEVELS
BOOT = 3000
SEED = 1013
BASE = Path(__file__).resolve().parents[2]
DATA = BASE / "results" / "02_run_level_data"
FIG = BASE / "results" / "01_results_and_figures" / "comparison_105_pubs"
TABLES = DATA / "comparison_105_pubs"
'''+constants.replace('TREATMENTS','COMPARISON_TREATMENTS')+'''

def _comparison_imports():
    from low_memory_runtime import limit_native_threads
    limit_native_threads()
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    return np, pd, plt

'''+ '\n\n'.join(parts)+'''

def main(argv=None):
    """Legacy direct CLI: D only by default; --figure-c selects C only."""
    import argparse
    parser = argparse.ArgumentParser(description='Offline 105-PUB comparison; Figure D only by default.')
    parser.add_argument('--figure-c', action='store_true', help='Explicitly regenerate original Figure C instead of D')
    args = parser.parse_args(argv)
    if args.figure_c:
        generate_figure_c()
    else:
        generate_figure_d()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
'''
combined=bootstrap(stats.read_text(encoding='utf-8-sig'))+extra
ast.parse(combined)
stats.write_text(combined,encoding='utf-8')
print('Canonical modules prepared; root and comparison implementations retained pending equivalence checks.')
