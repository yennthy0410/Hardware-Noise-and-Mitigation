"""Offline n25/n30 progression and durable pause regressions; no provider I/O."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest
import test_batch_progression as fixtures
import independent_n_campaign as campaign


class ManagerPauseTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BatchProgressionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.directory=self.f.directory

    def observed(self):
        return {'batch_identity_verified':True,'jobs':[
            {'job_id':f'original-{i}','status':'DONE'} for i in range(8)]}

    def test_partial_invalid_batch_waits_before_archive_recovery(self):
        observed=self.observed()
        observed['jobs'][3]['failure_class']='RESULT_CORRUPTION'
        for status in ('RUNNING','QUEUED','UNAVAILABLE'):
            observed['jobs'][7]['status']=status
            self.assertFalse(campaign._recovery_sources_terminal(observed))
        observed['jobs'][7]['status']='DONE'
        self.assertTrue(campaign._recovery_sources_terminal(observed))

    def test_identity_and_unknown_never_become_retryable(self):
        for mutate in (lambda o:o.update(batch_identity_verified=False),
                       lambda o:o['jobs'][0].update(job_id=None,status='UNKNOWN'),
                       lambda o:o['jobs'][0].update(failure_class='BATCH_ID_MISMATCH')):
            observed=self.observed();mutate(observed)
            with self.assertRaises(RuntimeError):campaign._recovery_sources_terminal(observed)

    def test_retryable_pause_rechecks_until_clear(self):
        registry=Mock();registry.independent_campaign_status.return_value={
            'exists':True,'state':'PAUSED','current_level_index':5,
            'checkpoint':{'pause_class':'PAUSED_RETRYABLE'}}
        sleep=Mock()
        with patch.object(campaign,'campaign_root',return_value=self.directory), \
             patch.object(campaign,'run_independent_campaign',side_effect=[{'state':'PAUSED'},{'state':'PAUSED'},{'state':'COMPLETE'}]) as run:
            result=campaign.run_campaign_auto_manager(self.directory,'c',registry,sleeper=sleep)
        self.assertEqual(result['state'],'COMPLETE');self.assertEqual(run.call_count,3)
        self.assertEqual(sleep.call_count,2)
        self.assertTrue(all(not c.kwargs['resume'] for c in run.call_args_list))

    def run_setup(self,index=4,pause_class=None):
        levels=[{} for _ in range(6)]
        state={'exists':True,'state':'PAUSED','current_level_index':index,
               'manifest':{'backend':'ibm_fez','source_run':'run_02','calibration_data_excluded':True,'levels':levels},
               'manifest_sha256':'abc','checkpoint':{'pause_class':pause_class}}
        registry=Mock();registry.independent_campaign_status.return_value=state
        auth={'live_auto_submit':True,'allowed_accounts':['labI'],
              'allowed_instance_refs':{'labI':['ref']},
              'recovery_limits':{'max_read_attempts':1},
              'allowed_actions':['prepare','preflight','dry-run','submit','monitor','collect','validate']}
        self.enterContext(patch.object(campaign,'campaign_root',return_value=self.directory))
        self.enterContext(patch.object(campaign,'load_campaign_authorization',return_value=(auth,{})))
        self.enterContext(patch('batch_scheduler.reconcile_campaign_intents'))
        return registry,state

    def test_operator_pause_requires_explicit_resume_repeatedly(self):
        registry,state=self.run_setup(5,'PAUSED_OPERATOR_ACTION')
        with patch.object(campaign,'_target_from_level') as target:
            for _ in range(2):
                result=campaign.run_independent_campaign(self.directory,'c',registry,authorize_live_campaign=True)
                self.assertTrue(result['resume_required'])
            target.assert_not_called()

    def test_n25_repaired_advances_to_n30_with_exports_false(self):
        registry,state=self.run_setup()
        registry.independent_campaign_status.side_effect=[state,state,{**state,'current_level_index':5}]
        target=SimpleNamespace(target_n=25,blocks_required=5,treatment_ids=('M0',),target_id='n25',experiment_id='n25')
        registry.target_status.return_value={'blocks':[{'logical_block_id':f'b{i}','status':'INCOMPLETE'} for i in range(5)],
            'analysis_verified':False,'figures_verified':False,'report_verified':False}
        with patch.object(campaign,'_target_from_level',side_effect=[target,RuntimeError('reached n30')]), \
             patch.object(campaign,'_repaired_block_complete',return_value=True), \
             patch('targeted_recovery.repaired_dataset_status',return_value={'analysis_eligible':True,'counts_by_treatment':{'M0':25}}):
            with self.assertRaisesRegex(RuntimeError,'reached n30'):
                campaign.run_independent_campaign(self.directory,'c',registry,authorize_live_campaign=True,resume=True)
        registry.complete_independent_level.assert_called_once_with('c',4)

    def test_repeated_n30_resume_preserves_unknown_submission(self):
        registry,state=self.run_setup(5)
        target=SimpleNamespace(target_n=30,target_id='n30',experiment_id='n30')
        registry.target_status.return_value={'blocks':[{'logical_block_id':'n30-block-001'}]}
        registry.rows.return_value=[{'submission_state':'UNKNOWN','account_label':'labI'}]
        with patch.object(campaign,'_target_from_level',return_value=target), \
             patch.object(campaign,'_repaired_block_complete',return_value=False), \
             patch('batch_scheduler.submit_runtime_block') as submit:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError,'ambiguous submission'):
                    campaign.run_independent_campaign(self.directory,'c',registry,authorize_live_campaign=True,resume=True)
            submit.assert_not_called()

    def test_repeated_n30_resume_monitors_partial_batch_without_recovery(self):
        registry,state=self.run_setup(5)
        target=SimpleNamespace(target_n=30,target_id='n30',experiment_id='n30',
                               treatment_ids=tuple(f'M{i}' for i in range(8)),input_hashes={})
        registry.target_status.return_value={'blocks':[{'logical_block_id':'n30-block-001'}]}
        registry.rows.return_value=[{'submission_state':'SUBMITTED','account_label':'labI',
                                    'metadata':{'instance':'original-instance'}} for _ in range(8)]
        service=Mock();service.active_instance.return_value='original-instance'
        observed=self.observed();observed['block_status']='INCOMPLETE'
        observed['jobs'][5]['failure_class']='RESULT_CORRUPTION'
        observed['jobs'][7]['status']='RUNNING'
        with patch.object(campaign,'_target_from_level',return_value=target), \
             patch.object(campaign,'_repaired_block_complete',return_value=False), \
             patch.object(campaign,'inspect_independent_campaign',return_value={'state':'RUNNING'}), \
             patch('read_only_preflight._instance_ref',return_value='ref'), \
             patch('batch_monitor.monitor_batch',return_value=observed) as monitor, \
             patch('targeted_recovery.plan_invalid_pub_slots') as recovery, \
             patch('batch_scheduler.submit_runtime_block') as submit:
            for _ in range(2):
                result=campaign.run_independent_campaign(self.directory,'c',registry,
                    authorize_live_campaign=True,resume=True,once=True,service_factory=lambda _:service)
                self.assertTrue(result['recovery_waiting'])
            self.assertEqual(monitor.call_count,2)
            recovery.assert_not_called();submit.assert_not_called()


if __name__=='__main__':unittest.main()
