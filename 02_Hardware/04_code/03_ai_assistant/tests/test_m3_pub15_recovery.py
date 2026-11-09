"""Exactly M3 logical PUB 15: real ledger, archives and submitter; fake IBM only."""
import hashlib
import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_batch_progression as fixtures
import test_account_integration as accounts
import targeted_recovery as recovery
from job_registry import JobRegistry
from read_only_preflight import _instance_ref


class M3Pub15Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BatchProgressionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.registry=self.f.registry;self.root=self.f.directory
        self.archive_dir=self.root/'02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/03_IBM_execution_records'
        self.archive_dir.mkdir(parents=True)
        self.target=self.f.target(25);self.target.shots_per_pub=4096;self.target.experiment_id='test-n25'
        self.ids=self.f.reserve(self.target,3);self.block='vqe-independent-n-01-n25-block-003';self.cid='vqe-independent-n-01'
        with self.registry._transaction() as db:
            db.execute('UPDATE jobs SET logical_block_id=?,account_label=?',(self.block,'labK'))
            db.execute('UPDATE experiment_blocks SET logical_block_id=?,account_label=?,sequence_no=3',(self.block,'labK'))
        self.archives={}
        for name,rid in self.ids.items():
            jid='dbnc6e8ou4as73b493sg' if name=='M7_ALL' else 'original-'+name
            self.registry.transition(rid,'SUBMITTING')
            self.registry.transition(rid,'SUBMITTED',job_id=jid,batch_id=recovery.M7_RECOVERY_SCOPE['original_batch_id'])
            records=[{'pub_index':i,'expectation_values':['nan']*7 if name=='M3_ZNE' and i==5 else [.2]*7,
                'standard_deviations':['nan']*7 if name=='M3_ZNE' and i==5 else [.01]*7} for i in range(1,6)]
            payload={'job_id':jid,'treatment_id':name,'source_run':'run_02','backend':'ibm_fez',
                'input_hashes':self.target.input_hashes,'shots_per_pub':4096,'records':records}
            path=self.archive_dir/(name+'.json');path.write_text(json.dumps(payload));digest=hashlib.sha256(path.read_bytes()).hexdigest()
            self.archives[name]=path.read_bytes()
            self.registry.transition(rid,'COMPLETED',qpu_usage_seconds=1,metadata={'instance':'instance',
                'per_pub_source_path':str(path),'per_pub_source_sha256':digest})
            good=records[:4] if name=='M3_ZNE' else records
            self.registry.record_pub_validation_records(rid,self.target.target_id,digest,[dict(r,
                expectation_valid=True,uncertainty_valid=True,uncertainty_state='PRESENT') for r in good])
            self.registry.update_pub_progress(rid,completed=5,collected=5,validated=len(good))
        self.before=self.registry.rows(self.block);self.values=self.registry.pub_validation_records(self.target.target_id)
        a=accounts.AccountIntegrationTests();a.setUp();self.addCleanup(a.doCleanups)
        self.cfg=deepcopy(a.config);self.cfg['live_submission_enabled']=True;self.cfg['maximum_replacement_attempts']=1
        self.auth=deepcopy(a.auth);self.auth.update(live_auto_submit=True,allowed_actions=['submit'])
        self.auth['allowed_instance_refs']['labK']=[_instance_ref('instance')]
        self.preflight=a.report('labK',600);self.preflight.update(backend='ibm_fez',configured_instance_ref=_instance_ref('instance'))
        self.preflight['instances'][0]['instance_ref']=_instance_ref('instance')
        self.service=Mock();self.service.active_instance.return_value='instance';self.service.jobs.return_value=[]
        self.service.usage.return_value={'usage_consumed_seconds':0,'usage_limit_seconds':600}
        self.job=Mock();self.job.job_id.return_value='new-m3-pub15';self.job.status.return_value='DONE';self.job.metrics.return_value={}
        self.job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=[.3]*7,stds=[.02]*7),metadata={})]
        original=Mock();original.status.return_value='DONE'
        self.service.job.side_effect=lambda jid:self.job if jid=='new-m3-pub15' else original
        self.batch=Mock();self.batch.return_value.__enter__=Mock(return_value=SimpleNamespace(session_id='new-batch'))
        self.batch.return_value.__exit__=Mock(return_value=False)
        self.estimator=Mock();self.estimator.return_value.run.return_value=self.job
        estimate=SimpleNamespace(scope='single_treatment_replacement',treatment_id='M3_ZNE',pub_count=1,target_n=1,
            stress_block_seconds=20,defensible=True)
        patches=[patch.object(JobRegistry,'default_database',return_value=self.registry.database),
            patch.object(JobRegistry,'independent_campaign_status',return_value={'manifest':{},'levels':[{'target_id':self.target.target_id}]}),
            patch('independent_n_campaign.campaign_root',return_value=self.root),
            patch('independent_n_campaign._target_from_level',return_value=self.target),
            patch('independent_n_campaign._campaign_usage',return_value=a.usage),
            patch('final_configuration.load_final_config',return_value=self.cfg),
            patch('final_configuration.campaign_authorization_from_final_config',return_value=self.auth),
            patch('vqe_adapter.verify_frozen_inputs',return_value=(SimpleNamespace(parameters=[]),[1]*7,[0]*21,list(range(140,147)),None,None)),
            patch('repository_paths.frozen_source',return_value=self.root),patch('repository_paths.source_hashes',return_value=self.target.input_hashes),
            patch.object(recovery,'_verify_archived_treatment_options',return_value=None),
            patch.object(recovery,'_runtime_backend_for_service',return_value=Mock()),
            patch.object(recovery,'_coerce_replacement_estimator_pub',return_value='frozen-M3-pub'),
            patch('vqe_adapter.treatment_options',return_value=SimpleNamespace(treatment='M3_ZNE')),
            patch('batch_scheduler.verify_reconciliation_job',return_value={}),
            patch('runtime_estimator.targeted_replacement_estimate',return_value=estimate),
            patch('quota.preflight_and_select_account',return_value=({'account_label':'labK','effective_remaining_seconds':600},self.service,self.preflight))]
        for p in patches:self.enterContext(p)

    def run_recovery(self,live=True):
        return recovery.recover_m3_pub15(self.root,self.cid,self.block,authorize_live=live,confirm_m3_pub15=live,
            service_factory=lambda _:self.service,batch_factory=self.batch,estimator_factory=self.estimator)

    def test_one_pub_complete_and_idempotent(self):
        result=self.run_recovery()
        self.estimator.return_value.run.assert_called_once_with(['frozen-M3-pub'])
        self.assertEqual(result['state'],'REPAIRED_COMPLETE');self.assertEqual(result['progression']['validated_slots'],40)
        self.assertFalse(result['batch004_submitted'])
        self.assertEqual(self.before,self.registry.rows(self.block));self.assertEqual(self.values,self.registry.pub_validation_records(self.target.target_id))
        for name,raw in self.archives.items():self.assertEqual(raw,(self.archive_dir/(name+'.json')).read_bytes())
        row=self.registry.replacement_jobs(self.target.target_id)[0]
        self.assertEqual((row['treatment_id'],row['logical_pub_index'],row['source_pub_index']),('M3_ZNE',15,5))
        self.assertEqual(row['job_id'],'new-m3-pub15')
        self.assertFalse(self.run_recovery()['submission_performed']);self.estimator.return_value.run.assert_called_once()

    def test_prepare_is_read_only(self):
        result=self.run_recovery(False);self.assertEqual(result['state'],'READY')
        self.assertEqual(self.registry.replacement_jobs(self.target.target_id),[])
        self.service.job.assert_not_called();self.estimator.assert_not_called()

    def test_nonfinite_replacement_rejected_without_retry(self):
        self.job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=[float('nan')]*7,stds=[float('nan')]*7),metadata={})]
        result=self.run_recovery();self.assertFalse(result['progression']['eligible'])
        self.assertEqual(result['progression']['validated_slots'],39)
        self.assertEqual(self.run_recovery()['state'],'BLOCKED');self.estimator.return_value.run.assert_called_once()

    def test_unknown_blocks(self):
        with self.registry._transaction() as db:db.execute("UPDATE jobs SET submission_state='UNKNOWN' WHERE id=?",(self.ids['M0_Baseline'],))
        self.assertEqual(self.run_recovery()['state'],'BLOCKED');self.estimator.assert_not_called()

    def test_lost_response_blocks_duplicate(self):
        self.estimator.return_value.run.side_effect=TimeoutError('lost response')
        with self.assertRaises(TimeoutError):self.run_recovery()
        self.assertEqual(self.run_recovery()['state'],'BLOCKED');self.estimator.return_value.run.assert_called_once()

    def test_missing_returned_job_id_stays_unknown(self):
        self.job.job_id.return_value=None
        with self.assertRaisesRegex(RuntimeError,'no replacement job ID'):self.run_recovery()
        row=self.registry.replacement_jobs(self.target.target_id)[0]
        self.assertIsNone(row['job_id']);self.assertEqual(row['state'],'UNKNOWN')
        self.assertEqual(self.run_recovery()['state'],'BLOCKED')

    def test_id_committed_before_interruption_resumes_collection(self):
        self.batch.return_value.__exit__.side_effect=TimeoutError('interrupted after acceptance')
        with self.assertRaises(TimeoutError):self.run_recovery()
        self.batch.return_value.__exit__.side_effect=None
        self.assertEqual(self.run_recovery()['state'],'REPAIRED_COMPLETE');self.estimator.return_value.run.assert_called_once()

    def test_tampered_original_archive_blocks(self):
        (self.archive_dir/'M3_ZNE.json').write_text('{}')
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.estimator.assert_not_called()

    def test_quota_drop_before_claim_blocks(self):
        self.service.usage.return_value={'usage_consumed_seconds':599,'usage_limit_seconds':600}
        with patch.object(recovery,'_select_recovery_account',return_value=('labK',self.service,self.preflight,'instance',Mock())):
            with self.assertRaises(RuntimeError):self.run_recovery()
        self.estimator.assert_not_called()

    def test_authorized_failover_preserves_original_account(self):
        report=deepcopy(self.preflight);report['account_label']='labI'
        self.auth['allowed_instance_refs']['labI']=[_instance_ref('instance')]
        with patch('quota.preflight_and_select_account',return_value=(
                {'account_label':'labI','effective_remaining_seconds':600},self.service,report)):
            result=recovery.recover_m3_pub15(self.root,self.cid,self.block,authorize_live=True,confirm_m3_pub15=True,
                service_factory=lambda label: (_ for _ in ()).throw(AssertionError('Original account access not required')),
                batch_factory=self.batch,estimator_factory=self.estimator)
        self.assertEqual(result['state'],'REPAIRED_COMPLETE')
        row=self.registry.replacement_jobs(self.target.target_id)[0]
        self.assertEqual(row['account_label'],'labI');self.assertEqual(row['source_account_label'],'labK')
        self.assertEqual(self.before,self.registry.rows(self.block))

    def test_another_missing_slot_blocks(self):
        selected=self.registry.repaired_pub_values(self.target.target_id)
        with patch.object(JobRegistry,'repaired_pub_values',return_value=selected[1:]):
            with self.assertRaisesRegex(RuntimeError,'other 39'):self.run_recovery()
        self.estimator.assert_not_called()

    def test_explicit_confirmation_and_cli(self):
        with self.assertRaisesRegex(RuntimeError,'confirm-m3-pub15'):
            recovery.recover_m3_pub15(self.root,self.cid,self.block,authorize_live=True)
        import mxene
        with patch.object(recovery,'recover_m3_pub15',return_value={}) as run:
            mxene.main(['recover-m3-pub15','--live','--confirm-m3-pub15'])
        self.assertTrue(run.call_args.kwargs['confirm_m3_pub15'])
