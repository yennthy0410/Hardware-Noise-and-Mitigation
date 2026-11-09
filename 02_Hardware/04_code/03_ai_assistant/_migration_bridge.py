"""Load relocated implementation into its legacy module identity.

Fixed local paths only; no generated commands or external code. Executing in
the existing module namespace preserves monkeypatching, circular imports and
old CLI __main__ behavior without a second implementation.
"""
from pathlib import Path


def load(namespace, component):
    if component not in ('01_ai_agent', '03_python_automation'):
        raise ValueError('Unrecognized canonical component')
    root = Path(__file__).resolve().parent
    original = Path(namespace['__file__']).resolve()
    if original.parent != root or original.suffix != '.py':
        raise ValueError('Compatibility entrypoint must be at the assistant root')
    canonical = root / component / original.name
    # Relocated modules also import siblings that have no legacy bridge.
    import sys
    if str(canonical.parent) not in sys.path:
        sys.path.insert(0, str(canonical.parent))
    namespace['__file__'] = str(canonical)
    namespace['__cached__'] = None
    exec(compile(canonical.read_bytes(), str(canonical), 'exec'), namespace)
