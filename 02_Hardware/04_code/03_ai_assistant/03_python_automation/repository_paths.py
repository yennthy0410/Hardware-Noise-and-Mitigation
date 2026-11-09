"""Canonical, conservative paths for MXene VQE; no IBM side effects."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

import hashlib
import os
from pathlib import Path
import shutil
import tempfile

MITIGATION_REL = Path('02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation')

def repository_root(root=None):
    if root is not None:
        candidate = Path(root).expanduser().resolve()
        if (candidate / '02_Hardware' / '04_code').is_dir():
            return candidate
        raise FileNotFoundError(f'Not an MXene repository root: {candidate}')
    for candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (candidate / '02_Hardware' / '04_code').is_dir():
            return candidate.resolve()
    raise FileNotFoundError('MXene repository root not found; pass --project explicitly')

def contained(root, *parts):
    base = Path(root).resolve()
    result = base.joinpath(*(str(p) for p in parts)).resolve()
    if result != base and base not in result.parents:
        raise ValueError('Path escapes its approved directory')
    return result

def mitigation_output(root=None, *parts):
    return contained(repository_root(root) / MITIGATION_REL / 'output', *parts)


def comparison_output_paths(root=None):
    """Only Final Comparison: flat PNG and CSV/JSON directories, no mkdir."""
    return _storage_paths.comparison_output_paths(
        repository_root(root if root is not None else Path(__file__).resolve().parents[4]))

def pilot_script(root=None):
    script = repository_root(root) / '02_Hardware/04_code/01_vqe/mitigation_pilot.py'
    if not script.is_file():
        raise FileNotFoundError(f'Canonical VQE pilot missing: {script}')
    return script

def frozen_source(root=None, source=None):
    if source is not None:
        bundle = Path(source).expanduser().resolve()
    else:
        bundle = mitigation_output(root) / '03_IBM_execution_records' / 'locked_inputs'
    required = ('circuit.qpy', 'theta.csv', 'observables.json', 'lock.json')
    if not all((bundle / name).is_file() for name in required):
        raise FileNotFoundError(f'Incomplete frozen VQE bundle: {bundle}')
    return bundle

def source_hashes(source):
    base = Path(source).resolve()
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest()
            for name in ('circuit.qpy', 'theta.csv', 'observables.json')}

def round_name(value):
    if not isinstance(value, str) or not value or value in ('.', '..') or any(c in value for c in '/\\:'):
        raise ValueError('Round name must be a safe path component')
    return value

# Share storage primitives with the canonical VQE module regardless of import order.
# This module retains its lightweight repository-discovery contract.
import importlib.util as _import_util
_storage_spec=_import_util.spec_from_file_location('_mxene_storage_paths',
    Path(__file__).resolve().parents[2]/'01_vqe'/'repository_paths.py')
_storage_paths=_import_util.module_from_spec(_storage_spec)
_storage_spec.loader.exec_module(_storage_paths)
atomic_archive=_storage_paths.atomic_archive
scratch_root_info=_storage_paths.scratch_root_info
scratch_root=_storage_paths.scratch_root
create_scratch_dir=_storage_paths.create_scratch_dir
cleanup_scratch_dir=_storage_paths.cleanup_scratch_dir


def scientific_model_path(root=None):
    """Return the one canonical Study A VQE model directory (no file copying)."""
    repo = repository_root(root)
    folder = repo / '01_quantum_eis_fit' / '04_code' / 'vqe'
    required = ('impedance_model.py',)
    missing = [name for name in required if not (folder / name).is_file()]
    if missing:
        raise FileNotFoundError(f'Study A scientific model missing: {folder} ({missing})')
    return folder.resolve()


def register_science(root=None):
    """Register canonical Study A imports for VQE; reject shadowed modules."""
    import sys
    folder = scientific_model_path(root)
    for module_name in ('impedance_model',):
        loaded = sys.modules.get(module_name)
        if loaded is not None:
            actual = Path(getattr(loaded, '__file__', '')).resolve()
            expected = folder / (module_name + '.py')
            if actual != expected:
                raise ImportError(f'Refusing shadowed {module_name}: {actual}')
    value = str(folder)
    if value not in sys.path:
        sys.path.insert(0, value)
    return folder
