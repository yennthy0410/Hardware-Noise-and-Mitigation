"""Offline, read-only installation checker for MXene Hybrid Level 2.

Run: python -B check_install.py [--assistant-dir PATH] [--project PATH]
No project modules are imported, no IBM access, no jobs, no files written.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

import argparse
import ast
import importlib.util
import sys
from pathlib import Path

EXPECTED = (
    'mxene.py', 'research_pipeline.py', 'independent_n_campaign.py',
    'batch_scheduler.py', 'batch_monitor.py', 'targeted_recovery.py',
    'job_registry.py', 'vqe_adapter.py', 'scientific_exports.py',
    'account_config.py', 'quota.py', 'runtime_estimator.py',
    'final_configuration.py', 'experiment_manifest.py',
    'read_only_preflight.py', 'ai_assistance.py', 'repository_paths.py',
)
OPTIONAL = ('adaptive_targets.py', 'calibration.py', 'calibration_worker.py',
            'assistant_cli.py', 'final_config.json')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assistant-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--project', type=Path, help='MXene project root (optional)')
    args = parser.parse_args()
    root = args.assistant_dir.resolve()
    print('Assistant directory:', root)
    if not root.is_dir():
        print('FAIL: assistant directory does not exist')
        return 2
    files = sorted(root.rglob('*.py'))
    print('Python source files:', len(files))
    failures = []
    imported = set()
    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding='utf-8-sig'), filename=str(file))
        except (OSError, UnicodeError, SyntaxError) as exc:
            failures.append(f'SYNTAX/READ: {file.relative_to(root)}: {type(exc).__name__}: {exc}')
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split('.')[0])
    for name in EXPECTED:
        if not (root / name).is_file():
            failures.append('MISSING REQUIRED SOURCE: ' + name)
    for name in OPTIONAL:
        if not (root / name).exists():
            print('Optional/legacy absent:', name)
    # Detect missing modules known to be local to this research project.
    known_local = {Path(name).stem for name in EXPECTED + OPTIONAL if name.endswith('.py')}
    known_local.update(('mitigation_pilot', 'mitigation_plot', 'repository_paths',
                        'parameter_mapping', 'impedance_model', 'hea_ansatz'))
    search_dirs = [root]
    if args.project:
        project = args.project.resolve()
        search_dirs.extend([project / '02_Hardware' / '04_code' / '01_vqe',
                            project / '01_quantum_eis_fit' / '01_vqe',
                            project / '01_quantum_eis_fit' / '04_code' / 'vqe', project])
    for module in sorted(imported & known_local):
        if not any((folder / (module + '.py')).is_file() or
                   (folder / module / '__init__.py').is_file() for folder in search_dirs):
            # Scientific files may be resolved dynamically via repository_paths.
            label = 'SCIENTIFIC MODULE NOT VERIFIED' if module in {'mitigation_pilot','mitigation_plot','parameter_mapping','impedance_model','hea_ansatz'} else 'UNRESOLVED LOCAL IMPORT'
            failures.append(f'{label}: {module}')
    if args.project:
        model_file = project / '01_quantum_eis_fit' / '04_code' / 'vqe' / 'impedance_model.py'
        if model_file.is_file():
            print('Study A impedance model: FOUND', model_file)
        else:
            failures.append(f'STUDY A SCIENTIFIC MODEL MISSING: {model_file}')
    print('Syntax check:', 'PASS' if not any(x.startswith('SYNTAX/READ') for x in failures) else 'FAIL')
    if failures:
        print('\nISSUES:')
        for item in failures:
            print(' -', item)
        print('\nNOT READY FOR LIVE IBM EXECUTION. This checker cannot establish runtime compatibility.')
        return 1
    print('PASS: expected sources present, Python syntax parsed, known local imports found.')
    print('NOT VERIFIED: dynamic imports, external packages, configuration, IBM credentials, tests, or live behavior.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
