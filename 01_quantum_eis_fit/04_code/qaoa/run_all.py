"""Offline EIS14 QAOA p=2 plotting pipeline; PNG 600 DPI only."""
import argparse
import subprocess
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--landscape', action='store_true', help='Recompute exact p=2 energy slices (CPU/RAM intensive)')
    parser.add_argument('--grid', type=int, default=7)
    parser.add_argument('--keep-legacy', action='store_true', help='Do not delete existing PDF/SVG from QAOA figure directory')
    args = parser.parse_args()
    scripts = Path(__file__).resolve().parent
    figures = scripts.parents[1] / '02_qaoa' / 'figure'
    figures.mkdir(parents=True, exist_ok=True)
    if not args.keep_legacy:
        for ext in ('*.pdf', '*.svg'):
            for old in figures.glob(ext):
                old.unlink()
                print('Removed legacy:', old.name)
    for name in ('plot_qaoa_eis14.py', 'plot_p2_angles.py', 'plot_surrogate_diagnostics.py', 'rebuild_p2_diagnostics.py'):
        cmd = [sys.executable, str(scripts / name)]
        if name == 'rebuild_p2_diagnostics.py' and args.landscape:
            cmd += ['--landscape', '--grid', str(args.grid)]
        print('Running:', name, flush=True)
        subprocess.run(cmd, check=True)
    print('Completed. PNG only:', figures)

if __name__ == '__main__':
    main()
