"""Two provider floors and safe account failover; all IBM calls are mocked."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import json
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_account_integration as fixtures
import test_batch_progression as progression
from final_configuration import MIN_BATCH_QUOTA_SECONDS, MIN_RECOVERY_QUOTA_SECONDS, provider_quota_thresholds
from runtime_estimator import workload_quota_decision
from quota import select_highest_available_account, preflight_and_select_account, AccountSelectionUnavailable


class QuotaFailoverTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AccountIntegrationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.auth=deepcopy(self.f.auth)
        self.usage=deepcopy(self.f.usage);self.usage['campaign_consumed_seconds']=0
        self.estimate=SimpleNamespace(stress_block_seconds=20,defensible=True,scope='full_batch',target_n=5)
        self.recovery=SimpleNamespace(stress_block_seconds=10,defensible=True,
            scope='single_treatment_replacement',target_n=1,pub_count=1,treatment_id='M0_Baseline')

    def select(self,quotas,*,recovery=False,original=None,**updates):
        reports={label:self.f.report(label,seconds) for label,seconds in quotas.items()}
        reports.update(updates)
        return select_highest_available_account(reports,runtime_estimate=self.recovery if recovery else self.estimate,
            usage=self.usage,authorization=self.auth,expected_hashes={'isa':'frozen'},
            workload_type='targeted_replacement' if recovery else 'original_batch',original_account=original)

    def test_fixed_threshold_boundaries_large_estimates_and_failover(self):
        from final_configuration import quota_eligibility
        self.estimate.stress_block_seconds=1000
        self.recovery.stress_block_seconds=1000
        for seconds in (39,40,45,149,150,155,175):
            for recovery,minimum in ((False,150),(True,40)):
                with self.subTest(seconds=seconds,recovery=recovery):
                    workload="targeted_replacement" if recovery else "original_batch"
                    gate=quota_eligibility(seconds,workload_type=workload,estimated_seconds=1000,safety_margin_seconds=75)
                    self.assertEqual(gate['eligible'],seconds>=minimum)
                    self.assertEqual(gate['required_quota_seconds'],minimum)
                    self.assertEqual(gate['required_runtime_with_margin_seconds'],1075)
                    self.assertTrue(gate['warnings'])
                    selected=self.select({'labK':seconds,'labI':600},recovery=recovery)
                    self.assertEqual(selected['account_label'],'labK' if seconds>=minimum else 'labI')

    def test_threshold_reservations_and_account_limits(self):
        self.estimate.stress_block_seconds=1000
        for recovery,remaining,allowed_reserved in ((False,155,5),(True,45,5)):
            self.recovery.stress_block_seconds=1000
            self.usage['provider_reservations']['labK']['reserved_seconds']=allowed_reserved
            self.assertEqual(self.select({'labK':remaining,'labI':600},recovery=recovery)['account_label'],'labK')
            self.usage['provider_reservations']['labK']['reserved_seconds']=allowed_reserved+1
            self.assertEqual(self.select({'labK':remaining,'labI':600},recovery=recovery)['account_label'],'labI')
        self.usage['provider_reservations']['labK']['reserved_seconds']=0
        self.usage['per_account']['labK']['consumed_seconds']=451
        self.assertEqual(self.select({'labK':155,'labI':600})['account_label'],'labI')

    def test_batch_149_rejected(self):
        with self.assertRaises(AccountSelectionUnavailable):self.select({'labK':149})

    def test_batch_150_inclusive_and_no_fixed_185_floor(self):
        d=self.select({'labK':150});self.assertTrue(d['batch_submission_eligible'])
        self.assertEqual(d['required_runtime_with_margin_seconds'],30)
        self.assertEqual(MIN_BATCH_QUOTA_SECONDS,150)

    def test_recovery_39_rejected(self):
        with self.assertRaises(AccountSelectionUnavailable):self.select({'labK':39},recovery=True)

    def test_recovery_40_inclusive(self):
        d=self.select({'labK':40},recovery=True)
        self.assertTrue(d['recovery_submission_eligible']);self.assertFalse(d['batch_submission_eligible'])
        self.assertEqual(MIN_RECOVERY_QUOTA_SECONDS,40)

    def test_threshold_table(self):
        for seconds,batch,recovery in [(17,False,False),(39,False,False),(40,False,True),
                (100,False,True),(149,False,True),(150,True,True)]:
            with self.subTest(seconds=seconds):
                self.assertEqual(provider_quota_thresholds(seconds),dict(
                    batch_quota_threshold_eligible=batch,recovery_quota_threshold_eligible=recovery))

    def test_new_batch_cannot_use_single_pub_recovery_threshold(self):
        from adaptive_targets import evaluate_new_batch
        target=SimpleNamespace(backend='ibm_fez',source_run='run_02',input_hashes={'isa':'frozen'},
            chunk_n=5,target_n=25,target_id='test')
        result=evaluate_new_batch(target,account_label='labK',preflight_report=self.f.report('labK',40),
            runtime_estimate=self.recovery,remaining_seconds=40,safety_margin_seconds=10)
        self.assertFalse(result['eligible'])
        self.assertFalse(result['checks']['runtime_estimate_matches_chunk'])
        self.assertEqual(result['quota']['minimum_remaining_seconds'],150)
        self.assertFalse(result['recovery_submission_eligible'])

    def test_preflight_public_flags_distinguish_workloads(self):
        import test_provider_quota as provider_fixtures
        f=provider_fixtures.ProviderQuotaTests();f.setUp();self.addCleanup(f.doCleanups)
        for seconds in (17,39,40,100,149,150):
            with self.subTest(seconds=seconds):
                report=f.preflight(f.service(consumed=600-seconds))
                self.assertEqual(report['remaining_provider_quota_seconds'],seconds)
                self.assertEqual(report['batch_submission_eligible'],seconds>=150)
                self.assertEqual(report['recovery_submission_eligible'],seconds>=40)
                self.assertTrue(report['workload_authorization_required'])
                self.assertIsNone(report['estimated_submission_runtime_seconds'])

    def test_preferred_labk_beats_more_quota(self):
        self.assertEqual(self.select({'labK':150,'labI':600})['account_label'],'labK')

    def test_exhausted_labk_falls_back_to_labi(self):
        d=self.select({'labK':17,'labI':150,'labG':600})
        self.assertEqual(d['account_label'],'labI');self.assertIn('labK',d['rejected_accounts'])

    def test_exhausted_labk_labi_falls_back_to_labg(self):
        self.assertEqual(self.select({'labK':0,'labI':39,'labG':150})['account_label'],'labG')

    def test_other_accounts_rank_by_current_verified_quota(self):
        self.assertEqual(self.select({'labK':0,'labI':0,'labG':0,'labA':170,'labB':220})['account_label'],'labB')

    def test_recovery_original_first(self):
        self.assertEqual(self.select({'labA':40,'labK':600},recovery=True,original='labA')['account_label'],'labA')

    def test_recovery_original_exhausted_or_unauthorized_falls_back(self):
        self.assertEqual(self.select({'labA':39,'labK':40},recovery=True,original='labA')['account_label'],'labK')
        self.auth['allowed_accounts'].remove('labA')
        self.assertEqual(self.select({'labA':600,'labI':40},recovery=True,original='labA')['account_label'],'labI')

    def test_stale_quota_not_reused(self):
        stale=self.f.report('labK',600);stale['checked_utc']=(datetime.now(timezone.utc)-timedelta(seconds=601)).isoformat()
        self.assertEqual(self.select({'labI':150},labK=stale)['account_label'],'labI')

    def test_runtime_and_campaign_budget_only_warn(self):
        self.estimate.stress_block_seconds=145
        self.assertTrue(self.select({'labK':150})['eligible'])
        self.estimate.stress_block_seconds=20
        self.usage['campaign_consumed_seconds']=1780
        with self.assertLogs('quota',level='INFO'):
            decision=self.select({'labK':600})
        self.assertTrue(decision['eligible'])
        self.assertTrue(decision['warnings'])

    def test_exhausted_campaign_budget_batch_and_recovery(self):
        self.usage['campaign_consumed_seconds']=self.auth['total_runtime_budget_seconds']
        for recovery,quota in [(False,150),(True,40)]:
            with self.subTest(recovery=recovery), self.assertLogs('quota',level='INFO'):
                d=self.select({'labK':quota},recovery=recovery)
                self.assertTrue(d['eligible'])
                self.assertEqual(d['internal_campaign_remaining_seconds'],0)
                self.assertEqual(d['campaign_budget_mode'],'monitoring_only')
                self.assertIsNone(d['rejection_reason'])
            with self.assertRaises(AccountSelectionUnavailable):
                self.select({'labK':quota-1},recovery=recovery)

    def test_workload_failover_with_exhausted_campaign(self):
        self.usage['campaign_consumed_seconds']=2000
        self.estimate.stress_block_seconds=175
        d=self.select({'labK':149,'labI':185})
        self.assertEqual(d['account_label'],'labI')
        self.assertEqual(d['internal_campaign_remaining_seconds'],-200)
        self.assertIn('labK',d['rejected_accounts'])
        self.assertTrue(d['warnings'])

    def test_paused_campaign_resumes_with_fresh_quota_and_monitoring_warning(self):
        import independent_n_campaign as campaign
        self.usage['campaign_consumed_seconds']=1800
        target=SimpleNamespace(input_hashes={'isa':'frozen'})
        self.auth['allowed_accounts']=['labK']
        checkpoint={'block_id':'n25-block-003','job_ids':['historical-job']}
        registry=Mock()
        registry.independent_campaign_status.return_value={
            'exists':True,'state':'PAUSED','current_level_index':4,'checkpoint':deepcopy(checkpoint)}
        def resume(*args,**kwargs):
            self.assertTrue(kwargs['resume'])
            self.assertTrue(kwargs['authorize_live_campaign'])
            chosen,_,_=preflight_and_select_account(None,target,runtime_estimate=self.estimate,
                usage=self.usage,authorization=self.auth,service_factory=lambda _:Mock())
            (directory/'.campaign.stop').touch()
            return {'state':'READY','selection':chosen}
        with fixtures.scratch_directory() as directory, \
             patch.object(campaign,'campaign_root',return_value=directory), \
             patch.object(campaign,'run_independent_campaign',side_effect=resume), \
             patch('read_only_preflight.frozen_bundle_summary',return_value={'input_sha256':target.input_hashes}), \
             patch('read_only_preflight.preflight_account',return_value=self.f.report('labK',150)) as fresh:
            with self.assertNoLogs('quota',level='WARNING'):
                result=campaign.run_campaign_auto_manager(directory,'existing',registry,resume=True,
                    authorize_live_campaign=True)
            self.assertEqual(result['state'],'STOPPED')
        fresh.assert_called_once()
        self.assertEqual(registry.checkpoint_independent_campaign.call_args.kwargs['state'],'STOPPED')
        self.assertEqual(registry.independent_campaign_status.return_value['checkpoint'],checkpoint)

    def test_zero_global_allocation_is_valid_monitoring_data_in_both_authorizations(self):
        from final_configuration import campaign_authorization_from_final_config
        from independent_n_campaign import validate_operator_authorization
        config=deepcopy(self.f.config);config['campaign_runtime_budget_seconds']=0
        auth=campaign_authorization_from_final_config(config)
        self.assertEqual(auth['total_runtime_budget_seconds'],0)
        legacy=deepcopy(auth)
        # Legacy manifests predate the separate recover action in final_config.
        legacy['allowed_actions']=[action for action in legacy['allowed_actions'] if action!='recover']
        self.assertEqual(validate_operator_authorization(legacy)['total_runtime_budget_seconds'],0)
        self.assertEqual(auth['account_runtime_budget_seconds'],
                         campaign_authorization_from_final_config(self.f.config)['account_runtime_budget_seconds'])

    def test_zero_global_budget_preserves_account_and_provider_gates_without_stderr_warning(self):
        self.auth['total_runtime_budget_seconds']=0
        self.usage['campaign_consumed_seconds']=2500
        before=deepcopy(self.usage)
        self.estimate.stress_block_seconds=175
        with self.assertNoLogs('quota',level='WARNING'):
            chosen=self.select({'labK':184,'labI':185})
        self.assertEqual(chosen['account_label'],'labK')
        self.assertEqual(chosen['internal_campaign_remaining_seconds'],-2500)
        self.assertEqual(chosen['required_runtime_with_margin_seconds'],185)
        self.assertEqual(self.usage,before)
        self.usage['per_account']['labK']['consumed_seconds']=451
        self.usage['per_account']['labI']['consumed_seconds']=590
        with self.assertRaises(AccountSelectionUnavailable) as caught:
            self.select({'labK':184,'labI':600})
        self.assertIn('internal account budget insufficient',caught.exception.decisions['labI']['reason'])

    def test_recovery_telemetry_is_info_and_keeps_historical_accounting(self):
        from quota import campaign_budget_monitor,log_campaign_budget_monitor
        self.auth['total_runtime_budget_seconds']=0
        self.usage['campaign_consumed_seconds']=2000
        before=deepcopy(self.usage)
        report=campaign_budget_monitor(self.auth,self.usage,20)
        with self.assertLogs('quota',level='INFO') as captured:
            log_campaign_budget_monitor(report)
        self.assertTrue(all(record.levelname=='INFO' for record in captured.records))
        self.assertEqual(self.usage,before)
        self.assertTrue(self.select({'labK':40},recovery=True)['eligible'])
        self.usage['provider_reservations']['labK']['ambiguous']=True
        with self.assertRaises(AccountSelectionUnavailable):self.select({'labK':600},recovery=True)

    def test_provider_floor_independent_of_local_headroom(self):
        self.usage['campaign_consumed_seconds']=1770
        self.assertTrue(self.select({'labK':150})['eligible'])
        gate=workload_quota_decision(self.recovery,remaining_seconds=20,provider_remaining_seconds=40,
            workload_type='targeted_replacement',safety_margin_seconds=10)
        self.assertFalse(gate['eligible'])

    def test_preflight_cycle_retries_reads_then_fails_over(self):
        self.auth['allowed_accounts']=['labK','labI']
        target=SimpleNamespace(input_hashes={'isa':'frozen'})
        services={'labK':Mock(),'labI':Mock()}; calls=[]
        def preflight(profile,**kwargs):
            calls.append(profile.label)
            if profile.label=='labK':raise TimeoutError('transient read')
            return self.f.report('labI',150)
        with patch('read_only_preflight.frozen_bundle_summary',return_value={'input_sha256':target.input_hashes}), \
             patch('read_only_preflight.preflight_account',side_effect=preflight):
            chosen,service,report=preflight_and_select_account(None,target,runtime_estimate=self.estimate,
                usage=self.usage,authorization=self.auth,service_factory=lambda label:services[label],attempts=2)
        self.assertEqual(calls,['labK','labK','labI']);self.assertEqual(chosen['account_label'],'labI')
        self.assertEqual(report['frozen_input_validation']['input_sha256'],target.input_hashes)
        services['labI'].run.assert_not_called()

    def test_preflight_cycle_keeps_scope_and_frozen_hashes(self):
        changed=self.f.report('labK',600);changed['frozen_input_validation']['input_sha256']={'isa':'wrong'}
        self.assertEqual(self.select({'labI':150},labK=changed)['account_label'],'labI')
        self.assertEqual(self.f.report('labI',150)['frozen_input_validation']['input_sha256'],{'isa':'frozen'})

    def test_all_exhausted_waits_and_rechecks_with_fresh_quota(self):
        import independent_n_campaign as campaign
        with self.assertRaises(AccountSelectionUnavailable) as caught:self.select({'labK':0,'labI':39})
        with fixtures.scratch_directory() as directory:
            registry=Mock();registry.independent_campaign_status.return_value={
                'exists':True,'state':'PAUSED','current_level_index':5,'checkpoint':{}}
            sleep=Mock()
            with patch.object(campaign,'campaign_root',return_value=directory), \
                 patch.object(campaign,'run_independent_campaign',side_effect=[caught.exception,{'state':'COMPLETE'}]) as run:
                result=campaign.run_campaign_auto_manager(directory,'existing',registry,resume=True,sleeper=sleep)
            self.assertEqual(result['state'],'COMPLETE')
            checkpoint=registry.checkpoint_independent_campaign.call_args.kwargs['checkpoint']
            self.assertEqual(checkpoint['pause_class'],'PAUSED_RETRYABLE')
            self.assertEqual(checkpoint['failure_category'],'PROVIDER_WAIT')
            sleep.assert_called_once_with(60)
            self.assertEqual([call.kwargs['resume'] for call in run.call_args_list],[True,False])

    def test_archived_recovery_does_not_require_original_account_access(self):
        import independent_n_campaign as campaign
        f=progression.BatchProgressionTests();f.setUp();self.addCleanup(f.doCleanups)
        t,b,ids=f.block(f.target(5),missing=[('M0_Baseline',5)])
        t.experiment_id=t.target_id
        levels=[dict(level_index=i,target_n=(i+1)*5,target_id=f'n{(i+1)*5}',experiment_id=f'n{(i+1)*5}') for i in range(6)]
        f.registry.register_independent_campaign(dict(campaign_id='offline',manifest_sha256='f'*64,
            levels=levels,source_run='run_02',calibration_data_excluded=True))
        f.registry.plan_pub_replacement(target_id=t.target_id,logical_block_id=b,treatment_id='M0_Baseline',
            source_pub_index=5,logical_pub_index=5,options={'shots':4096},metadata={'source_run':'run_02'})
        auth=dict(live_auto_submit=True,allowed_accounts=['labK'],allowed_instance_refs={'labK':['123456abcdef']},
            allowed_actions=['prepare','preflight','dry-run','submit','monitor','collect','validate'],
            recovery_limits={'max_read_attempts':2},completeness_policy={'version':'complete-only'})
        service_factory=Mock(side_effect=AssertionError('Original provider must not be accessed'))
        with patch.object(campaign,'load_campaign_authorization',return_value=(auth,{})), \
             patch.object(campaign,'campaign_root',return_value=f.directory), \
             patch.object(campaign,'_target_from_level',return_value=t), \
             patch.object(campaign,'inspect_independent_campaign',return_value={}), \
             patch('adaptive_targets.audit_incomplete_batch_archives',return_value={'raw_archives_verified':True,'provenance_verified':True}), \
             patch('targeted_recovery.plan_invalid_pub_slots',return_value={'blocked':[]}):
            result=campaign.run_independent_campaign(f.directory,'offline',f.registry,authorize_live_campaign=True,
                once=True,_worker_lock_held=True,service_factory=service_factory)
        self.assertTrue(result['recovery_waiting']);service_factory.assert_not_called()
        self.assertEqual(f.registry.rows(b)[0]['account_label'],'labA')

    def test_unknown_submission_never_retries(self):
        f=progression.BatchProgressionTests();f.setUp();self.addCleanup(f.doCleanups)
        t,b,ids=f.block()
        with f.registry._transaction() as db:
            db.execute("UPDATE jobs SET submission_state='UNKNOWN' WHERE id=?",(ids[t.treatment_ids[0]],))
        self.assertFalse(f.decision(t,b)['eligible'])
        with self.assertRaises(ValueError):f.reserve(t,2)

    def test_validated_pub_cannot_be_resubmitted_on_new_account(self):
        from targeted_recovery import submit_replacement_slot
        f=progression.BatchProgressionTests();f.setUp();self.addCleanup(f.doCleanups)
        name=progression.TREATMENTS[0]
        t,b,ids=f.block(missing=[(name,1)])
        row=f.registry.plan_pub_replacement(target_id=t.target_id,logical_block_id=b,treatment_id=name,
            source_pub_index=1,logical_pub_index=1,options={'shots':4096},replacement_account_label='labK',
            metadata={'source_run':'run_02','input_hashes':t.input_hashes})
        f.registry.record_pub_validation_records(ids[name],t.target_id,'e'*64,[dict(pub_index=1,expectation_valid=True,uncertainty_valid=True,uncertainty_state='PRESENT',expectation_values=[.2]*7,standard_deviations=[.01]*7)])
        reassigned=f.registry.reassign_planned_replacement_account(row['replacement_id'],'labI')
        self.assertEqual(reassigned['account_label'],'labI')
        self.assertEqual(reassigned['source_account_label'],'labA')
        self.assertEqual(json.loads(reassigned['input_hashes_json']),t.input_hashes)
        cfg=deepcopy(self.f.config);cfg['live_submission_enabled']=True;cfg['maximum_replacement_attempts']=1
        auth=deepcopy(self.auth);auth['live_auto_submit']=True;auth['allowed_actions']=['submit']
        service=Mock()
        with self.assertRaisesRegex(RuntimeError,'already has a validated observation'):
            submit_replacement_slot(None,t,f.registry,row['replacement_id'],service,final_config=cfg,
                campaign_authorization=auth,preflight_report={},runtime_estimate=self.recovery,
                explicit_authorization=True,active_instance_ref='123456abcdef',authorized_instance_refs=['123456abcdef'])
        service.run.assert_not_called()
        original=f.registry.rows(b)[0]
        self.assertEqual(original['account_label'],'labA');self.assertEqual(original['input_hashes'],t.input_hashes)


if __name__=='__main__':unittest.main()
