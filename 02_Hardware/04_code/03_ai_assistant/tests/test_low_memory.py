"""Offline memory regressions: sequential children, no provider access."""
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
import weakref
from unittest.mock import Mock, patch

from test_account_integration import scratch_directory, ROOT, AI
from low_memory_runtime import THREAD_LIMITS, iter_provider_jobs
from diagnostic_manager import child_environment, process_memory


class LowMemoryTests(unittest.TestCase):
    def child(self, code):
        env=child_environment()
        env.update({name:'53' for name in THREAD_LIMITS})
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,
            env=env,capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout

    def test_status_imports_no_native_or_plotting_modules(self):
        with scratch_directory() as folder:
            code=f'''
import sys, os, socket
sys.path.insert(0,{str(AI)!r})
socket.socket.connect=lambda *a: (_ for _ in ()).throw(AssertionError('offline'))
import mxene
from low_memory_runtime import THREAD_LIMITS
assert all(os.environ[k]=='1' for k in THREAD_LIMITS)
from research_pipeline import status_pipeline
assert status_pipeline({str(ROOT)!r},'absent',registry_path={str(folder/'absent.db')!r})['state']=='NOT_PLANNED'
import independent_n_campaign, batch_scheduler, targeted_recovery
assert not set(('numpy','scipy','matplotlib','qiskit_aer')) & set(sys.modules)
'''
            self.child(code)

    def test_required_sdk_and_frozen_validation_do_not_import_aer_or_plotting(self):
        code=f'''
import sys, os, socket, importlib.abc
sys.path.insert(0,{str(AI)!r})
import mxene
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in ('qiskit_aer','matplotlib'):
   raise AssertionError('Unexpected optional import: '+fullname)
sys.meta_path.insert(0,Block())
socket.socket.connect=lambda *a: (_ for _ in ()).throw(AssertionError('offline'))
from vqe_adapter import verify_frozen_inputs, treatment_options
verify_frozen_inputs({str(ROOT)!r})
treatment_options('M7_ALL',4096,{str(ROOT)!r})
from diagnostic_manager import process_memory
print(process_memory())
'''
        print('Frozen-input validation child memory:',self.child(code).strip())

    def test_pagination_exhausts_more_than_one_thousand_jobs_without_retention(self):
        refs=[];calls=[]
        class Job:
            def __init__(self,i):self.i=i
            def job_id(self):return str(self.i)
        def jobs(*,limit,skip,**filters):
            gc.collect()
            self.assertLessEqual(sum(ref() is not None for ref in refs),51)
            calls.append((limit,skip))
            page=[Job(i) for i in range(skip,min(skip+limit,1234))]
            refs.extend(weakref.ref(job) for job in page)
            return page
        ids=[job.job_id() for job in iter_provider_jobs(SimpleNamespace(jobs=jobs))]
        self.assertEqual(ids,[str(i) for i in range(1234)])
        self.assertTrue(all(limit==50 for limit,skip in calls))
        gc.collect()
        self.assertTrue(all(ref() is None for ref in refs))

    def test_pagination_failure_or_overlap_never_looks_complete(self):
        job=SimpleNamespace(job_id=lambda:'duplicate')
        with self.assertRaisesRegex(RuntimeError,'pagination'):
            list(iter_provider_jobs(SimpleNamespace(jobs=lambda **kw:[job]),page_size=1))
        def broken(**kw):
            if kw['skip']:raise OSError('offline injected provider failure')
            return [job]
        with self.assertRaises(OSError):
            list(iter_provider_jobs(SimpleNamespace(jobs=broken),page_size=1))

    def test_different_campaigns_share_exclusive_worker_lock(self):
        from worker_runtime import campaign_worker_lock
        with scratch_directory() as folder:
            with campaign_worker_lock(folder/'one'):
                with self.assertRaises(RuntimeError):
                    with campaign_worker_lock(folder/'two'):pass
            with campaign_worker_lock(folder/'two'):pass

    def test_windows_peak_memory_available(self):
        if os.name!='nt':self.skipTest('Windows working-set counters')
        memory=process_memory()
        self.assertGreater(memory['peak_rss_bytes'],0)
        self.assertGreaterEqual(memory['peak_rss_bytes'],memory['rss_bytes'])

    def test_collection_archives_before_validation_and_releases_each_result(self):
        from vqe_adapter import load_pilot
        pilot=load_pilot(ROOT)
        from qiskit.primitives.containers import PrimitiveResult, PubResult, DataBin
        import numpy as np
        references=[]
        with scratch_directory() as folder:
            bundle=folder/'bundle';bundle.mkdir()
            (bundle/'circuit.qpy').write_bytes(b'frozen fixture')
            records=folder/'records'
            names=('M0_Baseline','M1_DD')
            for name in names:
                directory=records/name;directory.mkdir(parents=True)
                (directory/'job_submitted.json').write_text(json.dumps({'job_id':name}))
            def job(identity):
                gc.collect()
                self.assertTrue(all(ref() is None for ref in references))
                result=PrimitiveResult([PubResult(DataBin(evs=np.zeros(7),stds=np.ones(7)*.1))])
                references.append(weakref.ref(result))
                return SimpleNamespace(result=lambda:result,status=lambda:'DONE',metrics=lambda:{},inputs={})
            def observe(name,jid,result,path,digest,job):
                self.assertTrue(Path(path).is_file())
                self.assertEqual(pilot.sha(path),digest)
                self.assertFalse((records/name/'expectations.csv').exists())
            args=SimpleNamespace(round='fixture',repeats=1)
            with patch.object(pilot,'paths',return_value=(folder,folder,folder,folder)), \
                 patch.object(pilot,'check_lock',return_value=(bundle,{})), \
                 patch.object(pilot,'ibm_round_dir',return_value=records), \
                 patch.object(pilot,'identity_matches',return_value=True), \
                 patch.object(pilot,'_recovery_overlay',side_effect=lambda b,r,n,rows,j:(rows,{},[])):
                observer=Mock()
                with patch('repository_paths.atomic_archive',side_effect=OSError('interrupted archive')):
                    with self.assertRaisesRegex(RuntimeError,'collection failed'):
                        pilot.collect(args,assistant_safe=True,service_override=SimpleNamespace(job=job),
                            selected_treatments=names,pub_observer=observer)
                observer.assert_not_called()
                self.assertFalse((records/names[0]/'expectations.csv').exists())
                self.assertFalse(list((records/names[0]/'raw_retrievals').glob('job_result_*.json')))
                pilot.collect(args,assistant_safe=True,service_override=SimpleNamespace(job=job),
                    selected_treatments=names,pub_observer=observe)
            gc.collect()
            self.assertTrue(all(ref() is None for ref in references))
            for name in names:
                self.assertTrue((records/name/'expectations.csv').is_file())
                raw=next((records/name/'raw_retrievals').glob('job_result_*.json'))
                value=json.loads(raw.read_text(encoding='utf-8'))
                expected=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,default=str).encode('utf-8')
                self.assertEqual(raw.read_bytes(),expected)


class ShortPathArchiveTests(unittest.TestCase):
    def test_long_archive_paths_and_restart(self):
        import hashlib
        from repository_paths import atomic_archive
        with scratch_directory() as folder:
            for minimum in (261,391):
                parent=folder
                while len(str(parent/'result.json'))<minimum:parent=parent/('d'*40)
                path=parent/'result.json';raw=b'{"pubs":[1,2,3]}'
                digest=hashlib.sha256(raw).hexdigest()
                atomic_archive(path,(raw,),digest)
                self.assertGreaterEqual(len(str(path)),minimum)
                self.assertEqual(path.read_bytes(),raw)
                atomic_archive(path,iter(()),digest)  # Restart reads the immutable archive.
                with self.assertRaises(RuntimeError):atomic_archive(path,(b'changed',),hashlib.sha256(b'changed').hexdigest())
                self.assertEqual(path.read_bytes(),raw)

    def test_interrupted_archive_does_not_publish_or_accept(self):
        import hashlib
        from repository_paths import atomic_archive
        with scratch_directory() as folder:
            path=folder/'result.json';raw=b'complete';digest=hashlib.sha256(raw).hexdigest()
            error=OSError('resource exhausted');error.winerror=1450
            with patch('repository_paths.os.fsync',side_effect=error):
                with self.assertRaises(OSError):atomic_archive(path,(raw,),digest)
            self.assertFalse(path.exists())
            self.assertFalse(list(folder.glob('.w*')))
            atomic_archive(path,(raw,),digest)
            self.assertEqual(path.read_bytes(),raw)

    def test_resource_error_retries_read_not_submission(self):
        from batch_monitor import classify_runtime_error
        from independent_n_campaign import _check_monitor_read,_auto_manager_error_category
        from worker_runtime import transient_read_error
        error=OSError('insufficient resources');error.winerror=1450
        wrapped=RuntimeError('archive failed');wrapped.__cause__=error
        self.assertTrue(transient_read_error(wrapped))
        self.assertEqual(_auto_manager_error_category(wrapped),'RESOURCE_WAIT')
        self.assertEqual(classify_runtime_error(error),'RESOURCE_WAIT')
        self.assertNotEqual(classify_runtime_error(error,phase='submit'),'RESOURCE_WAIT')
        with self.assertRaises(OSError):
            _check_monitor_read({'batch_identity_verified':True,'jobs':[{'failure_class':'RESOURCE_WAIT'}]})
        self.assertEqual(_auto_manager_error_category(RuntimeError('unknown submission')),'SUBMISSION_AMBIGUITY')

    def test_scratch_resource_failure_does_not_fallback(self):
        import repository_paths as paths
        error=OSError('insufficient resources');error.winerror=1450
        with patch.dict(os.environ,{'MXENE_SCRATCH_DIR':r'C:\MXeneScratch'}),patch.object(getattr(paths,'_storage_paths',paths),'_writable_probe',side_effect=error) as probe:
            with self.assertRaises(OSError):paths.scratch_root_info(ROOT)
            self.assertEqual(probe.call_count,1)

    def test_publish_tables_excludes_duplicate_plot_work(self):
        from adaptive_targets import _stage_canonical_tables
        with scratch_directory() as folder:
            source=folder/'w'/'o';source.mkdir(parents=True)
            for name in ('per_repeat.csv','round_summary.json','a_plot.csv','a_plot.png','publication_nyquist.png'):
                (source/name).write_text('fixture')
            _stage_canonical_tables(folder,source,'target')
            published=folder/'canonical_analysis/output/02_run_level_data/target'
            self.assertEqual({p.name for p in published.iterdir()},{'per_repeat.csv','round_summary.json'})
            self.assertTrue((source/'publication_nyquist.png').exists())


if __name__=='__main__':unittest.main()
