"""One-child Auto Manager launcher with persistent native-crash diagnostics.

Usage: python diagnostic_manager.py -- run --project . ...
No automatic restart, provider calls, or campaign writes occur in this launcher.
All authorization and restart decisions remain in mxene and its registry.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

from low_memory_runtime import THREAD_LIMITS, limit_native_threads
from manager_exit_codes import exit_outcome


def child_environment():
    env = os.environ.copy()
    # Set before the child imports any native numerical module. No science
    # configuration, Qiskit estimator options, or accepted results are changed.
    limit_native_threads(env)
    env.update(PYTHONFAULTHANDLER='1', PYTHONUNBUFFERED='1')
    return env


def process_memory(child=None):
    """Read Windows working set and lifetime peak through the process handle.

    The supervisor keeps the handle alive after exit, so short lived children
    still have an OS peak measurement. No monitoring subprocess is needed.
    """
    if os.name != 'nt':
        return {}
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    try:
        handle = int(child._handle) if child is not None else -1
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
                wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb):
            return {}
        return {'rss_bytes': counters.WorkingSetSize,
                'peak_rss_bytes': counters.PeakWorkingSetSize,
                'commit_bytes': counters.PagefileUsage,
                'peak_commit_bytes': counters.PeakPagefileUsage}
    except (AttributeError, TypeError, ValueError, OSError):
        return {}


def process_tree_memory(pid):
    """Include Windows venv redirector descendants, not just the launcher PID."""
    try:
        import psutil
    except ImportError:
        return []
    records=[]
    try:
        parent=psutil.Process(pid)
        processes=[parent,*parent.children(recursive=True)]
    except psutil.Error:
        return records
    for process in processes:
        try:
            info=process.memory_info()
            records.append({'pid':process.pid,'rss_bytes':info.rss,
                'peak_rss_bytes':getattr(info,'peak_wset',info.rss),
                'threads':process.num_threads()})
        except psutil.Error:
            continue
    return records


def supervise(command, directory, *, env=None):
    """Run exactly one child; capture startup, PID, output, and actual OS exit."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    stem = directory / f'manager-{stamp}-{os.getpid()}'
    events_path = stem.with_suffix('.jsonl')
    out_path = stem.with_suffix('.stdout.log')
    err_path = stem.with_suffix('.stderr.log')
    print(f'Diagnostics: {events_path}', flush=True)
    print(f'Progress: {out_path}', flush=True)
    with events_path.open('x', encoding='utf-8', buffering=1) as events:
        def record(event, **fields):
            events.write(json.dumps({'utc': datetime.now(timezone.utc).isoformat(),
                'event': event, **fields}) + '\n')
            events.flush()
            os.fsync(events.fileno())
        versions = {}
        for name in ('numpy', 'scipy', 'qiskit', 'qiskit-aer', 'qiskit-ibm-runtime'):
            try: versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError: versions[name] = None
        env = child_environment() if env is None else env
        record('LAUNCH', supervisor_pid=os.getpid(), supervisor_parent_pid=os.getppid(),
            executable=sys.executable, python=sys.version, command=command,
            cwd=str(Path.cwd()), packages=versions,
            thread_limits={key: env.get(key) for key in THREAD_LIMITS})
        # Native stderr and faulthandler share the persistent child stderr file.
        # Keep both descriptors open until the child has fully exited.
        with out_path.open('xb') as stdout, err_path.open('xb') as stderr:
            try:
                child = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr)
            except Exception as exc:
                record('LAUNCH_FAILED', error_type=type(exc).__name__)
                raise
            record('CHILD_STARTED', child_pid=child.pid)
            peaks={};sampled_tree_peak=0
            def sample():
                nonlocal sampled_tree_peak
                processes=process_tree_memory(child.pid)
                sampled_tree_peak=max(sampled_tree_peak,sum(p['rss_bytes'] for p in processes))
                for item in processes:
                    pid=item['pid']
                    peaks[pid]=max(peaks.get(pid,0),item['peak_rss_bytes'])
                return processes
            while True:
                try:
                    code = child.wait(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    record('CHILD_MEMORY', child_pid=child.pid, processes=sample(), **process_memory(child))
                except KeyboardInterrupt:
                    # Windows console Ctrl-C reaches the child as well. Let its
                    # existing interrupt handler checkpoint; never restart it.
                    record('SUPERVISOR_INTERRUPTED', child_pid=child.pid)
            unsigned = code & 0xffffffff
            sample()
            record('CHILD_EXIT', child_pid=child.pid, returncode=code,
                outcome=exit_outcome(code),
                **process_memory(child),
                observed_process_peak_rss_bytes=peaks,
                sampled_tree_peak_rss_bytes=sampled_tree_peak,
                exit_hex=f'0x{unsigned:08x}',
                native_exception=bool(os.name=='nt' and unsigned >= 0x80000000),
                automatic_restart=False)
    print(f'Child {child.pid} exited {code} (0x{unsigned:08x}); no restart.', flush=True)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostic-directory', type=Path,
        default=Path(__file__).resolve().parents[4] / 'manager_diagnostics')
    parser.add_argument('manager_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    forwarded = args.manager_args
    if forwarded[:1] == ['--']: forwarded = forwarded[1:]
    if not forwarded: forwarded = ['--help']
    command = [sys.executable, '-B', '-u', '-X', 'faulthandler',
               str(Path(__file__).resolve().parents[1]/'mxene.py'), *forwarded]
    code = supervise(command, args.diagnostic_directory)
    # Preserve the Windows DWORD status through CPython's signed exit integer.
    return code - (1 << 32) if os.name=='nt' and code > 0x7fffffff else code


if __name__ == '__main__':
    raise SystemExit(main())
