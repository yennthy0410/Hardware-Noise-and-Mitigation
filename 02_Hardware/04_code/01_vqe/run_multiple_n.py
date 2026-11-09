#!/usr/bin/env python3
"""Safe orchestration of independent MXene mitigation rounds.

Wraps mitigation_pilot.py without altering locked circuits, IBM options,
job ledgers, or analysis. No IBM submission unless --submit is supplied.
"""
import argparse
import datetime as dt
import pathlib
import subprocess
import sys
from repository_paths import repository_root, mitigation_output, pilot_script


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('phase', choices=['prepare', 'preflight', 'submit', 'collect', 'refresh-usage', 'analyze'],
                    help='Same phase as mitigation_pilot.py, run sequentially for each n')
    ap.add_argument('--n-values', nargs='+', type=int, default=[10, 15, 20, 25, 30],
                    help='Independent repeats/PUBs per treatment per round')
    ap.add_argument('--account-label', default=None, help='Account label for IBM job tracking')
    ap.add_argument('--ibm-account', default=None, help='Locally saved Qiskit IBM account name')
    ap.add_argument('--instance', default=None, help='Optional IBM instance CRN')
    ap.add_argument('--treatment', default=None, help='Restrict pilot to one treatment, especially for submit')
    ap.add_argument('--submit', action='store_true', help='Explicitly AUTHORIZE real IBM submissions')
    ap.add_argument('--max-jobs-per-round', type=int, default=1,
                    help='Max new IBM jobs for EACH n, not a total across all rounds')
    ap.add_argument('--online', action='store_true', help='Online preflight for backend validation')
    ap.add_argument('--shuffle', action='store_true', help='Shuffle treatments per round; deterministic seed')
    ap.add_argument('--shuffle-seed', type=int, default=1013)
    ap.add_argument('--project', type=pathlib.Path, default=None,
                    help='Root of MXene QPU Hardware (auto-detected by runner by default)')
    ap.add_argument('--pilot-script', type=pathlib.Path, default=pathlib.Path(__file__).with_name('mitigation_pilot.py'))
    ap.add_argument('--execute', action='store_true', help='Actually invoke the pilot runner. Without this, print plan only')
    ap.add_argument('--continue-on-error', action='store_true', help='Continue other rounds after a failure')
    args = ap.parse_args()
    if args.ibm_account and args.account_label and args.ibm_account != args.account_label:
        ap.error('--ibm-account and --account-label must match')
    if args.ibm_account and not args.account_label:
        args.account_label = args.ibm_account
    ns = args.n_values
    if not ns or any(n <= 0 for n in ns) or len(ns) != len(set(ns)):
        ap.error('--n-values must contain distinct positive integers')
    if args.max_jobs_per_round < 1 or args.max_jobs_per_round > 8:
        ap.error('--max-jobs-per-round must be between 1 and 8')
    if args.phase == 'submit' and args.execute and not args.submit:
        ap.error('Real submission requires BOTH --execute and --submit')
    if args.phase == 'submit' and args.execute and not args.account_label:
        ap.error('Real submission requires --account-label (example: labA)')
    script = pilot_script(args.project, args.pilot_script)
    if args.execute and not script.is_file():
        ap.error(f'Cannot find pilot script: {script}. Place run_multiple_n.py beside mitigation_pilot.py.')
    project = repository_root(args.project)
    print(f'Phase: {args.phase}; n-values: {ns}', flush=True)
    print('Existing n=5 is untouched; each round uses its own n=<N> folders.', flush=True)
    print(f'Execution: {"ENABLED" if args.execute else "DRY RUN"}', flush=True)
    if args.phase == 'submit':
        print('IBM submission:', 'AUTHORIZED' if args.submit and args.execute else 'NOT AUTHORIZED', flush=True)
        print(f'Max new jobs per round: {args.max_jobs_per_round}', flush=True)
        print('CAUTION: 8 treatments x n PUBs each; DD/TREX/ZNE may amplify actual QPU usage.', flush=True)
    for n in ns:
        cmd = [sys.executable, str(script), args.phase, '--repeats', str(n), '--round', f'n={n}']
        if project: cmd += ['--project', str(project)]
        if args.account_label: cmd += ['--account-label', args.account_label]
        if args.ibm_account: cmd += ['--ibm-account', args.ibm_account]
        if args.instance: cmd += ['--instance', args.instance]
        if args.phase == 'preflight' and args.online: cmd.append('--online')
        if args.phase == 'submit' and args.treatment:
            cmd += ['--treatment', args.treatment]
        if args.phase == 'submit':
            cmd += ['--resume', '--max-jobs', str(args.max_jobs_per_round)]
            if args.submit: cmd.append('--submit')
            if args.shuffle:
                cmd += ['--shuffle', '--shuffle-seed', str(args.shuffle_seed+n)]
        print('\n' + '='*65, flush=True)
        print(f'ROUND n={n}: ' + subprocess.list2cmdline(cmd), flush=True)
        if not args.execute: continue
        logdir = mitigation_output(project, '04_runtime_logs')
        logdir.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        logfile = logdir / f'{args.phase}_n{n}_{now}.log'
        # Real-time streaming and durable console log; never log secret tokens.
        with logfile.open('w', encoding='utf-8') as fh:
            fh.write('COMMAND: ' + subprocess.list2cmdline(cmd) + '\n')
            fh.flush()
            proc = subprocess.Popen(cmd, cwd=project, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, bufsize=1, encoding='utf-8', errors='replace')
            for line in proc.stdout:
                print(line, end='', flush=True)
                fh.write(line)
                fh.flush()
            code = proc.wait()
            fh.write(f'\nEXIT_CODE: {code}\n')
        print(f'Log: {logfile}', flush=True)
        if code:
            print(f'STOP: n={n} failed (exit {code}).', file=sys.stderr, flush=True)
            if not args.continue_on_error: return code
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
