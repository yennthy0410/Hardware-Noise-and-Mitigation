"""Bounded offline diagnostics tests; do not intentionally crash the host."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock,patch
from test_account_integration import scratch_directory
import diagnostic_manager as diagnostic


class DiagnosticTests(unittest.TestCase):
    def test_lock_released_after_abrupt_process_exit(self):
        from worker_runtime import WorkerLock
        with scratch_directory() as folder:
            lock=folder/'worker.lock'
            code=('import os,sys;sys.path.insert(0,sys.argv[1]);from worker_runtime import WorkerLock;'
                  'guard=WorkerLock(sys.argv[2]);guard.__enter__();os._exit(7)')
            result=subprocess.run([sys.executable,'-B','-c',code,str(Path(diagnostic.__file__).parent),str(lock)],
                capture_output=True,timeout=20,env=diagnostic.child_environment())
            self.assertEqual(result.returncode,7)
            with WorkerLock(lock):pass

    def test_limits_are_child_only(self):
        with patch.dict(os.environ,{'OPENBLAS_NUM_THREADS':'99'}):
            env=diagnostic.child_environment()
            self.assertEqual(os.environ['OPENBLAS_NUM_THREADS'],'99')
            self.assertEqual(env['OPENBLAS_NUM_THREADS'],'1')
            self.assertEqual(env['PYTHONFAULTHANDLER'],'1')

    def test_child_exit_and_native_status_are_persisted_without_restart(self):
        with scratch_directory() as folder:
            child=Mock(pid=321);child.wait.return_value=0xc0000005
            with patch.object(diagnostic.subprocess,'Popen',return_value=child) as popen:
                self.assertEqual(diagnostic.supervise(['test-child'],folder),0xc0000005)
            popen.assert_called_once();child.wait.assert_called_once()
            events=[json.loads(line) for line in next(folder.glob('*.jsonl')).read_text().splitlines()]
            self.assertEqual(events[-1]['exit_hex'],'0xc0000005')
            self.assertFalse(events[-1]['automatic_restart'])
            self.assertEqual(events[1]['child_pid'],321)

    def test_real_child_has_faulthandler_and_persistent_streams(self):
        with scratch_directory() as folder:
            # Outer timeout bounds the test, without adding a manager watchdog.
            code=('import sys;sys.path.insert(0,sys.argv[1]);import diagnostic_manager as d;'
                  'raise SystemExit(d.supervise([sys.executable,"-X","faulthandler","-c",'
                  '"import faulthandler,sys;print(faulthandler.is_enabled());print(\'stderr marker\',file=sys.stderr)"],sys.argv[2]))')
            result=subprocess.run([sys.executable,'-B','-c',code,str(Path(diagnostic.__file__).parent),str(folder)],
                capture_output=True,text=True,timeout=20,env=diagnostic.child_environment())
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('True',next(folder.glob('*.stdout.log')).read_text())
            self.assertIn('stderr marker',next(folder.glob('*.stderr.log')).read_text())
            events=[json.loads(line) for line in next(folder.glob('*.jsonl')).read_text().splitlines()]
            self.assertEqual(events[-1]['event'],'CHILD_EXIT')
            self.assertEqual(events[-1]['returncode'],0)
