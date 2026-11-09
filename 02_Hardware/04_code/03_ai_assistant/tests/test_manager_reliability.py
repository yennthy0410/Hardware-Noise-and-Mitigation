"""Offline restart, identity, retry and transactional state regressions."""
import json
import unittest
from unittest.mock import Mock,patch
import test_batch_progression as fixtures
from worker_runtime import RetryingReadOnlyService
from job_registry import validate_state_transition
import batch_scheduler as scheduler
from independent_n_campaign import _auto_manager_error_category


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BatchProgressionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.registry=self.f.registry

    def test_result_retry_never_retries_submission_or_validation(self):
        service=Mock();job=service.job.return_value
        job.result.side_effect=[TimeoutError(),['raw']]
        proxy=RetryingReadOnlyService(service,2)
        with patch('worker_runtime.time.sleep'):
            self.assertEqual(proxy.job('existing').result(),['raw'])
        job.result.side_effect=ValueError('nonfinite')
        with self.assertRaises(ValueError):proxy.job('existing').result()
        self.assertEqual(job.result.call_count,3)
        service.run.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):proxy.run([])
        service.run.assert_called_once()

    def test_wrapped_transport_retry_is_bounded(self):
        cause=ConnectionError('transport')
        error=RuntimeError('SDK failure');error.__cause__=cause
        service=Mock();service.usage.side_effect=error
        with patch('worker_runtime.time.sleep') as sleep:
            with self.assertRaises(RuntimeError):RetryingReadOnlyService(service,3).usage()
        self.assertEqual(service.usage.call_count,3);self.assertEqual(sleep.call_count,2)
        self.assertEqual(_auto_manager_error_category(error),'TRANSIENT_READ')
        self.assertNotEqual(_auto_manager_error_category(ValueError('bug')),'TRANSIENT_READ')

    def test_immutable_and_unique_job_identity_rolls_back(self):
        ids=self.f.reserve(self.f.target(),1);a,b=list(ids.values())[:2]
        for row in (a,b):self.registry.transition(row,'SUBMITTING')
        self.registry.transition(a,'SUBMITTED',job_id='accepted',batch_id='original')
        for kwargs in ({'job_id':'different'},{'batch_id':'different'}):
            with self.assertRaises(RuntimeError):self.registry.transition(a,'SUBMITTED',**kwargs)
        with self.assertRaises(RuntimeError):self.registry.transition(b,'SUBMITTED',job_id='accepted')
        for value in ('', 'None',123):
            with self.assertRaises(ValueError):self.registry.transition(b,'SUBMITTED',job_id=value)
        self.assertEqual(next(r for r in self.registry.rows() if r['id']==a)['job_id'],'accepted')
        self.assertEqual(next(r for r in self.registry.rows() if r['id']==b)['submission_state'],'SUBMITTING')

    def test_terminal_replacement_cannot_regress(self):
        for current,state in [('VALIDATED','PLANNED'),('FAILED','SUBMITTED'),('DONE','SUBMITTING')]:
            with self.assertRaises(ValueError):validate_state_transition('replacement',current,state)

    def test_atomic_state_file_retries_only_owned_write(self):
        path=self.f.directory/'state.json';path.write_text(json.dumps({'attempt_id':'a'}))
        real=scheduler.os.replace
        with patch.object(scheduler.os,'replace',side_effect=[PermissionError(),None]) as replace,patch('time.sleep'):
            scheduler._update_owned(path,'a',{'attempt_id':'a','status':'SUBMITTED'})
        self.assertEqual(replace.call_count,2)
        scheduler._update_owned(path,'a',{'attempt_id':'a','status':'SUBMITTED'})
        self.assertEqual(json.loads(path.read_text())['status'],'SUBMITTED')

    def test_restart_empty_inventory_preserves_unknown_and_reservation(self):
        ids=self.f.reserve(self.f.target(),1);row=list(ids.values())[0]
        self.registry.transition(row,'SUBMITTING',batch_id='original')
        auth={'allowed_accounts':['labA'],'allowed_instance_refs':{'labA':['ref']},
              'recovery_limits':{'max_read_attempts':1}}
        service=Mock();service.jobs.return_value=[]
        with patch.object(self.registry,'independent_campaign_status',return_value={'levels':[{'target_id':'n10'}]}), \
             patch('read_only_preflight._instance_ref',return_value='ref'):
            with self.assertRaisesRegex(RuntimeError,'UNKNOWN'):
                scheduler.reconcile_campaign_intents(self.f.directory,self.registry,'campaign',auth,lambda _:service)
        self.assertEqual(self.registry.rows()[0]['submission_state'],'UNKNOWN')
        self.assertEqual(self.registry.rows()[-1]['submission_state'],'RESERVED')
        service.jobs.assert_called_once_with(limit=50,skip=0,session_id='original',instance=service.active_instance())
        service.run.assert_not_called()

    def test_pipeline_uses_authoritative_manifest_resolver(self):
        from research_pipeline import _typed_target
        with patch('independent_n_campaign._target_from_level',return_value='resolved') as resolver:
            self.assertEqual(_typed_target({'target_id':'legacy'},self.f.directory,self.registry),'resolved')
        resolver.assert_called_once_with(self.f.directory,{'target_id':'legacy'},registry=self.registry)

    def test_monitor_preserves_raw_invalid_result_idempotently(self):
        from batch_monitor import _archive_monitor_result
        from pathlib import Path
        raw=b'{"evs": "nan", "stds": "inf"}'
        with patch('vqe_adapter.record_directory',return_value=self.f.directory), \
             patch('vqe_adapter.serialize_runtime_result_archive',return_value=raw):
            first=_archive_monitor_result(self.f.directory,'block','M3_ZNE',object())
            second=_archive_monitor_result(self.f.directory,'block','M3_ZNE',object())
        self.assertEqual(first,second)
        self.assertEqual(Path(first['monitor_raw_archive_path']).read_bytes(),raw)

    def test_restart_attaches_only_one_verified_candidate(self):
        ids=self.f.reserve(self.f.target(),1);row=list(ids.values())[0]
        self.registry.transition(row,'SUBMITTING',batch_id='original')
        auth={'allowed_accounts':['labA'],'allowed_instance_refs':{'labA':['ref']},
              'recovery_limits':{'max_read_attempts':1}}
        job=Mock();job.job_id.return_value='verified';job.backend.return_value.name='ibm_fez'
        service=Mock();service.jobs.return_value=[job];service.job.return_value=job
        with patch.object(self.registry,'independent_campaign_status',return_value={'levels':[{'target_id':'n10'}]}), \
             patch('read_only_preflight._instance_ref',return_value='ref'), \
             patch('vqe_adapter.record_directory',return_value=self.f.directory/'records'), \
             patch.object(scheduler,'verify_reconciliation_job',return_value={'batch_id':'original','frozen_inputs_verified':True}):
            scheduler.reconcile_campaign_intents(self.f.directory,self.registry,'campaign',auth,lambda _:service)
        self.assertEqual(self.registry.rows()[0]['job_id'],'verified')
        self.assertEqual(self.registry.rows()[-1]['submission_state'],'RESERVED')
        service.run.assert_not_called()

    def test_complete_checkpoint_does_not_hide_failed_verification(self):
        import independent_n_campaign as campaign
        registry=Mock();registry.independent_campaign_status.return_value={
            'exists':True,'state':'COMPLETE','current_level_index':6}
        with patch.object(campaign,'campaign_root',return_value=self.f.directory), \
             patch.object(campaign,'run_independent_campaign',side_effect=RuntimeError('Frozen validation failed')), \
             patch.object(campaign,'inspect_independent_campaign',return_value={}):
            result=campaign.run_campaign_auto_manager(self.f.directory,'campaign',registry)
        self.assertEqual(result['state'],'PAUSED')
        self.assertEqual(registry.checkpoint_independent_campaign.call_args.kwargs['checkpoint']['pause_class'],
                         'PAUSED_OPERATOR_ACTION')
