"""Focused offline verification; canonical locks use disposable Dashboard fixtures."""
import sys
import uuid
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from worker_supervisor import HERE, ControlRequest, delegate_canonical, route_control, suggested_prompts, review_recovery
import test_worker_supervisor as previous

sys.path.insert(0,str(HERE.parent/'03_ai_assistant/03_python_automation'))
import worker_runtime
import independent_n_campaign as canonical
import research_pipeline


class RegistryFixture:
    def __init__(self):self.events=[]
    def independent_campaign_status(self,campaign):
        return {'exists':True,'state':'PAUSED','current_level_index':5,'checkpoint':{}}
    def checkpoint_independent_campaign(self,campaign,**event):self.events.append(event)


class CanonicalContractTests(unittest.TestCase):
    def setUp(self):
        # Inherited ACLs: Python tempfile's Windows mode=700 is inaccessible in
        # this sandbox. Remove only exact files created by this fixture.
        self.root=HERE/'state'/('canonical-fixture-'+uuid.uuid4().hex)
        self.root.mkdir();self.directory=self.root/'campaign'
        def cleanup():
            for path in (self.root/'auto_manager.lock',self.directory/'worker.lock',self.directory/'.campaign.pause'):
                path.unlink(missing_ok=True)
            if self.directory.exists():self.directory.rmdir()
            self.root.rmdir()
        self.addCleanup(cleanup)
        self.registry=RegistryFixture()
        p=patch.object(canonical,'campaign_root',return_value=self.directory);p.start();self.addCleanup(p.stop)
    def run_worker(self,**kwargs):
        return canonical.run_campaign_auto_manager(self.root,'vqe-independent-n-01',self.registry,
                    authorize_live_campaign=True,sleeper=lambda _:None,**kwargs)
    def test_real_canonical_lock_excludes_second_owner_then_releases(self):
        with worker_runtime.campaign_worker_lock(self.directory):
            with self.assertRaises(RuntimeError):
                with worker_runtime.campaign_worker_lock(self.directory):pass
        # A historical lock file is harmless; only the OS lock decides exclusion.
        with worker_runtime.campaign_worker_lock(self.directory):pass
    def test_auto_uses_real_canonical_lock_around_cycle(self):
        def cycle(*args,**kwargs):
            self.assertTrue(kwargs['_worker_lock_held']);self.assertTrue(kwargs['automatic_science'])
            with self.assertRaises(RuntimeError):
                with worker_runtime.campaign_worker_lock(self.directory):pass
            return {'state':'COMPLETE'}
        with patch.object(canonical,'run_independent_campaign',side_effect=cycle) as run:
            self.assertEqual(self.run_worker()['state'],'COMPLETE');self.assertEqual(run.call_count,1)
    def test_real_pause_ack_before_cycle_no_kill(self):
        self.directory.mkdir();(self.directory/'.campaign.pause').write_text('fixture')
        with patch.object(canonical,'run_independent_campaign',side_effect=AssertionError('Cycle must not start')):
            self.assertEqual(self.run_worker()['state'],'PAUSED')
        self.assertEqual(self.registry.events[-1]['event_type'],'AUTO_MANAGER_PAUSED')
    def test_resume_delegates_to_checkpoint_cycle(self):
        self.directory.mkdir();(self.directory/'.campaign.pause').write_text('fixture')
        with patch.object(canonical,'run_independent_campaign',return_value={'state':'COMPLETE'}) as cycle:
            self.run_worker(resume=True)
            self.assertTrue(cycle.call_args.kwargs['resume']);self.assertEqual(cycle.call_count,1)
    def test_unknown_exception_returns_operator_pause_without_retry(self):
        with patch.object(canonical,'run_independent_campaign',side_effect=TimeoutError('fixture')) as cycle, \
             patch.object(canonical,'_campaign_has_unknown',return_value=True), \
             patch.object(canonical,'inspect_independent_campaign',return_value={'state':'PAUSED'}):
            result=self.run_worker()
            self.assertEqual(result['pause_class'],'PAUSED_OPERATOR_ACTION');self.assertEqual(cycle.call_count,1)
            self.assertFalse(result['submission_performed'])
    def test_auto_delegate_exact_entrypoint_no_once_or_account_selection(self):
        for operation in ('start','resume'):
            with patch.object(research_pipeline,'run_pipeline',return_value={'fixture':True}) as run:
                delegate_canonical(ControlRequest(operation=operation),self.root,authorized=True)
                kw=run.call_args.kwargs
                self.assertFalse(kw['once']);self.assertTrue(kw['authorize_live_campaign'])
                self.assertTrue(kw['confirm_live_submission']);self.assertEqual(kw['resume'],operation=='resume')
                self.assertNotIn('account_label',kw);self.assertEqual(run.call_count,1)
    def test_delegate_refuses_missing_authorization_and_unsafe_batch_step(self):
        with patch.object(research_pipeline,'run_pipeline',side_effect=AssertionError('Must not execute')):
            with self.assertRaises(ValueError):delegate_canonical(ControlRequest(operation='start'),self.root)
            with self.assertRaises(ValueError):delegate_canonical(ControlRequest(operation='batch',n=5),self.root,authorized=True)


class SuggestionTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.evidence={'worker':'IDLE_VERIFIED','ownership_verified':True,'can_execute':True}
        self.snapshot={'captured_at':self.now.isoformat(),'offline':False,'valid':760,'expected':840}
    def test_verified_fresh_has_auto_only_suggestions(self):
        result=suggested_prompts(self.evidence,self.snapshot,self.now)
        self.assertIn('Run Auto Manager',result['prompts']);self.assertNotIn('Run Batch n=5',result['prompts'])
    def test_unknown_or_stale_or_future_only_safe(self):
        for snapshot,evidence in ((self.snapshot,{}),({**self.snapshot,'offline':True},self.evidence),
            ({**self.snapshot,'captured_at':(self.now-timedelta(seconds=181)).isoformat()},self.evidence),
            ({**self.snapshot,'captured_at':(self.now+timedelta(seconds=1)).isoformat()},self.evidence)):
            result=suggested_prompts(evidence,snapshot,self.now)
            self.assertTrue(result['read_only_only']);self.assertNotIn('Run Auto Manager',result['prompts'])
    def test_complete_dataset_no_submit_prompt(self):
        result=suggested_prompts(self.evidence,{**self.snapshot,'valid':840},self.now)
        self.assertNotIn('Run Auto Manager',result['prompts']);self.assertNotIn('Run Batch n=5',result['prompts'])
    def test_command_aliases(self):
        for text,op in [('Run Auto Manager','start'),('Resume campaign','resume'),('Show Auto Manager status','status'),
                        ('Prepare Campaign','prepare'),('Run Batch n=5','batch'),('Collect Results','collect'),('Analyze Results','analyze')]:
            self.assertEqual(route_control(text).operation,op)
    def test_pending_recovery_not_reclassified_and_provenance_hidden(self):
        class Reader:
            def status(self):return {'offline':False,'recovery_summary':[{'n':5,'treatment':'DD','pending':2,'valid':3,'status':'Awaiting original job','slots':[{'job_id':'private'}]}]}
        result=review_recovery(Reader());self.assertEqual(result['rows'][0]['pending'],2)
        self.assertNotIn('slots',result['rows'][0]);self.assertNotIn('private',str(result))


class DispatchRecoveryTests(unittest.TestCase):
    setUp=previous.SupervisorTests.setUp
    prepare=previous.SupervisorTests.prepare
    confirm=previous.SupervisorTests.confirm
    def test_fresh_request_cannot_bypass_unresolved_dispatch_after_restart(self):
        request=self.prepare()
        with patch.object(self.adapter,'execute',side_effect=TimeoutError('fixture')):self.confirm(request)
        from worker_supervisor import Supervisor
        self.supervisor=Supervisor(self.path,self.adapter)
        self.assertEqual(self.prepare()['control_state'],'BLOCKED')


if __name__=='__main__':unittest.main()
