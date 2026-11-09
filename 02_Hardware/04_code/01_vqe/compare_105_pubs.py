"""Legacy offline comparison entrypoint; C/D/E/F live in dependence_statistics.

Default: read-only canonical readiness. Production writes require explicit
--authorize-output-write and verified evidence. No numerical implementation
is duplicated here. Legacy read access to comparison constants is retained.
"""
from pathlib import Path
import sys

_code = Path(__file__).resolve().parents[1] / '03_ai_assistant' / '03_python_automation'
if str(_code) not in sys.path:
    sys.path.insert(0, str(_code))
import dependence_statistics as _canonical
from dependence_statistics import generate_figure_c, generate_figure_d, main


def __getattr__(name):
    if name == 'TREATMENTS':
        return _canonical.COMPARISON_TREATMENTS
    if name == 'CHECKPOINTS':
        np, _, _ = _canonical._comparison_imports()
        return np.cumsum(_canonical.NS)
    return getattr(_canonical, name)


if __name__ == '__main__':
    raise SystemExit(main())
