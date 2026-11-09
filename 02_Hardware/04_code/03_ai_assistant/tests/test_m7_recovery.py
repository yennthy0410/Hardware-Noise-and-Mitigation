"""M7-only recovery tests: real temporary registry, fake provider; network forbidden."""
import json
from copy import deepcopy
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock,patch
import unittest
import test_batch_progression as fixtures
import test_account_integration as accounts
import targeted_recovery as recovery
from runtime_estimator import workload_quota_decision
from job_registry import JobRegistry,utc_now

class M7Tests(unittest.TestCase):
    def setUp(self,missing_m3=False):
        self.f=fixtures.BatchProgressionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        missing=[('M7_ALL',i) for i in range(1,6)]+([('M3_ZNE',5)] if missing_m3 else [])
        self.registry=self.f.registry;self.target,old,self.ids=self.f.block(self.f.target(25),missing=missing)
        self.block='vqe-independent-n-01-n25-block-003';self.cid='vqe-independent-n-01'
        self.batch='d9e6cd69-775b-4597-9267-7e27a864879d';self.rid=self.ids['M7_ALL']
        self.registry.register_independent_campaign(dict(campaign_id=self.cid,manifest_sha256='f'*64,
            levels=[dict(level_index=i,target_n=(i+1)*5,target_id=f'n{(i+1)*5}',experiment_id=f'n{(i+1)*5}') for i in range(6)],
            source_run='run_02',calibration_data_excluded=True))
        with self.registry._transaction() as db:
            db.execute('UPDATE jobs SET logical_block_id=?,account_label=?,batch_id=?',(self.block,'labK',self.batch))
            db.execute('UPDATE experiment_blocks SET logical_block_id=?',(self.block,))
            db.execute("UPDATE jobs SET submission_state='RESERVED',job_id=NULL,validated_pub_count=0,metadata_json='{}' WHERE id=?",(self.rid,))
        for name,rid in self.ids.items():
            if name!='M7_ALL':self.registry.update_pub_progress(rid,completed=5,collected=5,
                validated=4 if missing_m3 and name=='M3_ZNE' else 5)
        self.folder=self.f.directory/'records';(self.folder/'M7_ALL').mkdir(parents=True)
        (self.folder/'M7_ALL/submission_state.json').write_text(json.dumps({'attempt_id':'original','status':'RESERVED'}))
        self.enterContext(patch('vqe_adapter.record_directory',return_value=self.folder))
        self.estimate=SimpleNamespace(scope='missing_treatment_recovery',pub_count=5,treatment_id='M7_ALL',
            target_n=5,stress_block_seconds=35,defensible=True)

    def test_recovery_floor_and_diagnostic_workload_margin(self):
        for seconds,eligible in ((39,False),(40,True),(44,True),(45,True)):
            gate=workload_quota_decision(self.estimate,remaining_seconds=seconds,workload_type='missing_treatment',safety_margin_seconds=10)
            self.assertEqual(gate['eligible'],eligible);self.assertEqual(gate['minimum_remaining_seconds'],40)
        self.estimate.stress_block_seconds=20
        self.assertTrue(workload_quota_decision(self.estimate,remaining_seconds=40,workload_type='missing_treatment')['eligible'])
        self.assertTrue(workload_quota_decision(self.estimate,remaining_seconds=45,workload_type='missing_treatment')['eligible'])
        self.assertFalse(workload_quota_decision(self.estimate,remaining_seconds=600,workload_type='targeted_replacement')['eligible'])

    def test_only_one_recovery_reservation_and_preserve_original(self):
        self.registry.transition(self.rid,'CONFIRMED_NOT_SUBMITTED',release_reservation=True,metadata={'m7_absence_evidence':{'verified':True}})
        original=next(r for r in self.registry.rows() if r['id']==self.rid)
        new=self.registry.reserve_missing_m7(self.rid,account_label='labI',reservation_seconds=35,metadata={'instance':'instance'})
        with self.assertRaisesRegex(RuntimeError,'already reserved'):
            self.registry.reserve_missing_m7(self.rid,account_label='labI',reservation_seconds=35,metadata={})
        self.assertEqual(original,next(r for r in self.registry.rows() if r['id']==self.rid))
        row=next(r for r in self.registry.rows() if r['id']==new)
        self.assertEqual(row['chunk_n'],5);self.assertEqual(row['metadata']['source_account_label'],'labK')
        from independent_n_campaign import _campaign_usage
        usage=_campaign_usage(self.registry,{'levels':[{'target_id':self.target.target_id}],
            'operator_authorization':{'allowed_accounts':['labK','labI']}})
        self.assertEqual(usage['treatment_recovery_batches_reserved_or_attempted'],1)
        self.assertEqual(usage['batches_reserved_or_attempted'],2)

    def test_absence_requires_closed_batch_and_no_extra_jobs(self):
        service=Mock();service.active_instance.return_value='instance'
        for row in self.registry.rows():
            if row['id']!=self.rid:self.registry.transition(row['id'],'COMPLETED',metadata={'instance':'instance'})
        jobs=[]
        for row in self.registry.rows():
            if row['id']==self.rid:continue
            job=Mock();job.job_id.return_value=row['job_id'];job.status.return_value='DONE';jobs.append(job)
        service.jobs.return_value=jobs;service.job.side_effect=lambda jid:next(j for j in jobs if j.job_id()==jid)
        details={'id':self.batch,'backend_name':'ibm_fez','state':'closed','accepting_jobs':False,
            'closed_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}
        with patch('batch_scheduler.verify_reconciliation_job',return_value={}):
            evidence=recovery.verify_missing_m7_absence(self.f.directory,self.registry,self.block,service,batch_details=details)
            self.assertTrue(evidence['provider_history_complete'])
            service.jobs.return_value=jobs+[Mock()]
            with self.assertRaisesRegex(RuntimeError,'additional or missing'):
                recovery.verify_missing_m7_absence(self.f.directory,self.registry,self.block,service,batch_details=details)
            details['state']='active'
            with self.assertRaisesRegex(RuntimeError,'closed'):
                recovery.verify_missing_m7_absence(self.f.directory,self.registry,self.block,service,batch_details=details)

    def install_runner(self,ambiguous=False,cap=100,used=12):
        a=accounts.AccountIntegrationTests();a.setUp();self.addCleanup(a.doCleanups)
        auth=deepcopy(a.auth);auth.update(live_auto_submit=True,allowed_actions=['submit'],maximum_batches=cap)
        from read_only_preflight import _instance_ref
        auth['allowed_instance_refs']['labK']=[_instance_ref('instance')]
        config=deepcopy(a.config);config['maximum_replacement_attempts']=2
        status={'manifest':{},'levels':[{'target_id':self.target.target_id}]}
        self.enterContext(patch.object(JobRegistry,'default_database',return_value=self.registry.database))
        self.enterContext(patch.object(JobRegistry,'independent_campaign_status',return_value=status))
        self.enterContext(patch('independent_n_campaign.campaign_root',return_value=self.f.directory))
        self.enterContext(patch('independent_n_campaign._target_from_level',return_value=self.target))
        self.enterContext(patch('independent_n_campaign._campaign_usage',return_value={'batches_reserved_or_attempted':used}))
        self.enterContext(patch('final_configuration.load_final_config',return_value=config))
        self.enterContext(patch('final_configuration.campaign_authorization_from_final_config',return_value=auth))
        self.enterContext(patch.object(recovery,'verify_missing_m7_absence',return_value={'verified':True}))
        self.enterContext(patch.object(recovery,'missing_m7_runtime_estimate',return_value=self.estimate))
        self.enterContext(patch.object(recovery,'_runtime_backend_for_service',return_value=Mock()))
        self.enterContext(patch.object(recovery,'_coerce_replacement_estimator_pub',return_value='frozen-pub'))
        self.enterContext(patch('vqe_adapter.verify_frozen_inputs',return_value=(Mock(),[1]*7,[0]*21,list(range(140,147)),None,None)))
        self.enterContext(patch('vqe_adapter.treatment_options',return_value='M7-frozen-options'))
        self.enterContext(patch('batch_scheduler.verify_reconciliation_job',return_value={}))
        service=Mock();service.active_instance.return_value='instance';service.jobs.return_value=[]
        service.usage.return_value={'usage_consumed_seconds':0,'usage_limit_seconds':600}
        self.enterContext(patch('quota.preflight_and_select_account',return_value=({'account_label':'labK','effective_remaining_seconds':600},service,{'checked_utc':utc_now(),'configured_instance_ref':_instance_ref('instance')})))
        self.enterContext(patch('quota.provider_reservations',return_value={'labK':{'ambiguous':False,'reserved_seconds':0},'labA':{'ambiguous':False,'reserved_seconds':0}}))
        job=Mock();job.job_id.return_value='new-m7';job.status.return_value='QUEUED';service.job.return_value=job
        batch=Mock();batch.session_id='recovery-batch';factory=Mock();factory.return_value.__enter__=Mock(return_value=batch);factory.return_value.__exit__=Mock(return_value=False)
        estimator=Mock();estimator.return_value.run.return_value=job
        if ambiguous:estimator.return_value.run.side_effect=TimeoutError('uncertain')
        return service,factory,estimator

    def run_recovery(self,service,factory,estimator):
        return recovery.recover_missing_m7(self.f.directory,self.cid,self.block,authorize_live=True,confirm_m7_only=True,
            service_factory=lambda _:service,batch_factory=factory,estimator_factory=estimator)

    def test_five_pub_only_and_restart_no_resubmission(self):
        service,factory,estimator=self.install_runner()
        before=[r for r in self.registry.rows() if r['treatment_id']!='M7_ALL']
        result=self.run_recovery(service,factory,estimator)
        self.assertEqual(result['job_id'],'new-m7')
        estimator.return_value.run.assert_called_once_with(['frozen-pub']*5)
        self.run_recovery(service,factory,estimator)
        estimator.return_value.run.assert_called_once()
        self.assertEqual(before,[r for r in self.registry.rows() if r['treatment_id']!='M7_ALL'])

    def test_m7_first_with_four_valid_m3_pubs_and_real_absence_gate(self):
        # Build a separate incident fixture with 34 original values from the
        # outset; production validation records are immutable, including tests.
        self.setUp(missing_m3=True)
        verify=recovery.verify_missing_m7_absence
        service,factory,estimator=self.install_runner()
        jobs=[]
        for row in self.registry.rows(self.block):
            if row['treatment_id']=='M7_ALL':continue
            self.registry.transition(row['id'],'COMPLETED',metadata={'instance':'instance'})
            job=Mock();job.job_id.return_value=row['job_id'];job.status.return_value='DONE';jobs.append(job)
        service.jobs.side_effect=lambda **kw: [] if kw.get('pending') else jobs
        details={'id':self.batch,'backend_name':'ibm_fez','state':'closed','accepting_jobs':False,
                 'closed_at':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()}
        before=[r for r in self.registry.rows() if r['treatment_id']!='M7_ALL']
        pubs=self.registry.pub_validation_records(self.target.target_id)
        self.assertEqual(len(pubs),34)
        with patch.object(recovery,'verify_missing_m7_absence',side_effect=lambda *a,**kw:verify(*a,**kw,batch_details=details)) as audit, \
             patch.object(recovery,'audit_original_m3',side_effect=AssertionError('M3 is out of scope')), \
             patch.object(recovery,'recover_m3_m7',side_effect=AssertionError('M3 is out of scope')):
            self.run_recovery(service,factory,estimator)
        self.assertTrue(audit.call_args.kwargs['allow_uncollected_m3'])
        history=[c.kwargs for c in service.jobs.call_args_list if 'skip' in c.kwargs]
        self.assertEqual(len(history),3)
        self.assertEqual(sum(c.get('session_id')==self.batch for c in history),2)
        estimator.return_value.run.assert_called_once_with(['frozen-pub']*5)
        self.assertEqual(estimator.call_args.kwargs['options'],'M7-frozen-options')
        self.assertEqual(before,[r for r in self.registry.rows() if r['treatment_id']!='M7_ALL'])
        self.assertEqual(pubs,self.registry.pub_validation_records(self.target.target_id))
        self.assertEqual(self.registry.replacement_jobs(self.target.target_id),[])
        service.job.assert_called_once_with('new-m7')

    def test_original_unknown_blocks_before_new_batch(self):
        service,factory,estimator=self.install_runner()
        with self.registry._transaction() as db:
            db.execute("UPDATE jobs SET submission_state='UNKNOWN' WHERE id=?",(self.ids['M3_ZNE'],))
        with self.assertRaisesRegex(RuntimeError,'Unresolved submission intent'):
            self.run_recovery(service,factory,estimator)
        factory.assert_not_called();estimator.assert_not_called()

    def test_contradictory_closure_never_reserves_or_submits(self):
        service,factory,estimator=self.install_runner()
        before=self.registry.rows()
        with patch.object(recovery,'verify_missing_m7_absence',side_effect=recovery.BatchClosureUnverified(
                {'conflicts':['Provider closure and accepting-new-jobs fields disagree']})):
            with self.assertRaises(recovery.BatchClosureUnverified):self.run_recovery(service,factory,estimator)
        self.assertEqual(before,self.registry.rows())
        factory.assert_not_called();estimator.assert_not_called()

    def test_uncertain_run_blocks_retry(self):
        service,factory,estimator=self.install_runner(ambiguous=True)
        with self.assertRaises(TimeoutError):self.run_recovery(service,factory,estimator)
        result=self.run_recovery(service,factory,estimator)
        self.assertEqual(result['state'],'BLOCKED');estimator.return_value.run.assert_called_once()

    def test_explicit_live_flags_required(self):
        with self.assertRaisesRegex(RuntimeError,'--live'):
            recovery.recover_missing_m7(self.f.directory,self.cid,self.block)

    def test_original_account_priority_and_workload_failover(self):
        from quota import select_highest_available_account
        a=accounts.AccountIntegrationTests();a.setUp();self.addCleanup(a.doCleanups)
        reports={'labK':a.report('labK',39),'labI':a.report('labI',45)}
        result=select_highest_available_account(reports,runtime_estimate=self.estimate,usage=a.usage,
            authorization=a.auth,workload_type='missing_treatment',original_account='labK',expected_hashes={'isa':'frozen'})
        self.assertEqual(result['account_label'],'labI')
        reports['labK']=a.report('labK',45)
        self.assertEqual(select_highest_available_account(reports,runtime_estimate=self.estimate,usage=a.usage,
            authorization=a.auth,workload_type='missing_treatment',original_account='labK',expected_hashes={'isa':'frozen'})['account_label'],'labK')

    def test_done_only_collects_m7_and_does_not_advance(self):
        import numpy as np
        service,factory,estimator=self.install_runner()
        job=service.job.return_value;job.status.return_value='DONE'
        job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=np.zeros(7),stds=np.ones(7)*.1),metadata={}) for _ in range(5)]
        with patch('vqe_adapter.collect_block',return_value={}) as collect, \
             patch('batch_progression.evaluate_batch_progression',return_value={'eligible':False,'validated_slots':35}) as evaluate:
            report=self.run_recovery(service,factory,estimator)
        self.assertEqual(collect.call_args.kwargs['treatments'],('M7_ALL',))
        self.assertEqual(report['progression']['validated_slots'],35)
        self.assertFalse(report['batch004_submitted'])
        estimator.return_value.run.assert_called_once_with(['frozen-pub']*5)

    def test_nan_result_never_claims_complete(self):
        import numpy as np
        service,factory,estimator=self.install_runner()
        job=service.job.return_value;job.status.return_value='DONE'
        job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=np.full(7,np.nan),stds=np.ones(7)),metadata={}) for _ in range(5)]
        with patch('vqe_adapter.collect_block') as collect:
            with self.assertRaises(ValueError):self.run_recovery(service,factory,estimator)
        collect.assert_not_called()
        latest=max((r for r in self.registry.rows() if r['treatment_id']=='M7_ALL'),key=lambda r:r['attempt'])
        self.assertEqual(latest['job_id'],'new-m7');self.assertEqual(latest['validated_pub_count'],0)

    def test_batch_count_never_blocks_m7(self):
        service,factory,estimator=self.install_runner(cap=0,used=1000)
        result=self.run_recovery(service,factory,estimator)
        self.assertEqual(result['job_id'],'new-m7')
        self.run_recovery(service,factory,estimator)
        estimator.return_value.run.assert_called_once_with(['frozen-pub']*5)

    def test_unknown_recovery_still_blocks_without_count_limit(self):
        service,factory,estimator=self.install_runner(ambiguous=True,cap=0,used=1000)
        with self.assertRaises(TimeoutError):self.run_recovery(service,factory,estimator)
        self.assertEqual(self.run_recovery(service,factory,estimator)['state'],'BLOCKED')
        estimator.return_value.run.assert_called_once()

    def test_larger_estimate_does_not_raise_recovery_threshold(self):
        self.assertTrue(workload_quota_decision(self.estimate,remaining_seconds=600,provider_remaining_seconds=44,workload_type="missing_treatment")["eligible"])
        self.estimate.stress_block_seconds=40
        self.assertTrue(workload_quota_decision(self.estimate,remaining_seconds=45,workload_type='missing_treatment')['eligible'])
        self.assertTrue(workload_quota_decision(self.estimate,remaining_seconds=50,workload_type='missing_treatment')['eligible'])

    def test_cli_needs_only_live_and_m7_confirmation(self):
        import mxene
        with patch.object(recovery,'recover_missing_m7',return_value={'submission_performed':False}) as run:
            mxene.main(['recover-m7','--live','--confirm-m7-only'])
        self.assertTrue(run.call_args.kwargs['authorize_live'])
        self.assertNotIn('authorize_one_additional_m7_batch',run.call_args.kwargs)
