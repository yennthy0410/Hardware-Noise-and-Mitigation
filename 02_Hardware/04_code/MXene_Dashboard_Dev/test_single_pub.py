"""Offline single-PUB preview/read contracts; LIVE creation is intentionally blocked."""
import copy,hashlib,json,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace
import test_canonical_steps as fixtures
from test_canonical_steps import pipeline,runtime

class SinglePubTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CanonicalStepTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.target.backend='fixture';self.f.target.treatment_ids=('M0_Baseline','M1_DD')
        self.f.registry.database=self.f.db;self.f.registry.repaired_pub_values.return_value=[]
        self.f.registry.replacement_jobs.return_value=[]
        self.cfg={'allowed_backend':'fixture','enabled_account_labels':['labK'],'allowed_instance_refs':{'labK':['ref']}}
        self.f.mocks[5].return_value=self.cfg
        self.f.mocks[6].return_value={'allowed_accounts':['labK'],'allowed_actions':['monitor','collect','prepare','analyze','plot']}
    def preview(self,purpose='RECOVERY',slot=1):
        return pipeline.preview_single_pub(self.f.root,'vqe-independent-n-01',5,purpose,'M0_Baseline',slot,'labK',registry_path=self.f.db)
    def plan(self,state='SUBMITTED'):
        self.item={'replacement_id':1,'logical_pub_index':1,'attempt':1,'treatment_id':'M0_Baseline','state':state,'job_id':'existing-job','account_label':'labK','backend':'fixture','batch_id':'batch','metadata':{'instance_ref':'ref'},'input_hashes':{'qpy':'frozen'}}
        self.f.registry.replacement_jobs.return_value=[self.item]
    def test_test_live_blocked_no_namespace_or_count_write(self):
        result=self.preview('TEST',None);self.assertFalse(result['submission_enabled']);self.assertEqual(result['state'],'BLOCKED')
        self.assertIn('quota policy',result['reason']);self.f.registry.transition.assert_not_called()
    def test_pending_original_without_plan_not_missing(self):
        result=self.preview();self.assertEqual(result['state'],'BLOCKED');self.assertIn('Pending',result['reason'])
    def test_scope_identity_and_unknown(self):
        self.plan();a=self.preview();self.assertEqual(a['scope_id'],self.preview()['scope_id'])
        self.item['attempt']=2;self.assertNotEqual(a['scope_id'],self.preview()['scope_id'])
        self.item['state']='UNKNOWN';self.assertEqual(self.preview()['state'],'BLOCKED')
    def test_accepted_slot_never_enables_submission(self):
        self.plan('VALIDATED');self.f.registry.repaired_pub_values.return_value=[{'treatment_id':'M0_Baseline','logical_pub_index':1}]
        result=self.preview();self.assertTrue(result['accepted']);self.assertFalse(result['submission_enabled'])
    def test_grouped_replacement_is_not_single_pub(self):
        self.plan();self.f.registry.replacement_jobs.return_value.append(dict(self.item,replacement_id=2,logical_pub_index=2))
        self.assertEqual(self.preview()['state'],'BLOCKED')
    def test_exact_slot_attempt_binding_and_worker_lock(self):
        self.plan();spec={'purpose':'RECOVERY','treatment':'M0_Baseline','slot':1,'account':'labK','scope_id':'0'*64,'action':'monitor'}
        with self.assertRaises(pipeline.CampaignStepBlocked):self.f.run_step('single_pub',single_pub=spec)
        with runtime.campaign_worker_lock(self.f.directory):
            with self.assertRaises(Exception):self.f.run_step('single_pub',single_pub=spec)
    def test_completed_collect_reuses_canonical_archival(self):
        self.plan();preview=self.preview();spec={'purpose':'RECOVERY','treatment':'M0_Baseline','slot':1,'account':'labK','scope_id':preview['scope_id'],'action':'collect'}
        job=Mock();job.inputs={'pubs':[object()]};job.job_id.return_value='existing-job';job.session_id='batch';job.backend.return_value=SimpleNamespace(name='fixture');job.status.return_value='DONE'
        service=Mock();service.job.return_value=job
        import account_config,read_only_preflight,targeted_recovery,repository_paths
        raw=self.f.root/repository_paths.MITIGATION_REL/'output/03_IBM_execution_records/replacements/raw.json'
        def collect(project,target,registry,rid,reads):
            self.assertEqual(rid,1);self.assertFalse(registry.readonly)
            reads.job('existing-job').result()
            raw.parent.mkdir(parents=True,exist_ok=True);raw.write_text('{"fixture":true}')
            self.item.update(state='VALIDATED',raw_archive_path=str(raw),raw_archive_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
            return {'state':'VALIDATED'}
        with patch.object(account_config,'make_saved_profile_service',return_value=service),patch.object(read_only_preflight,'_instance_ref',return_value='ref'),patch.object(targeted_recovery,'collect_replacement',side_effect=collect) as call:
            result=self.f.run_step('single_pub',single_pub=spec)
            self.assertTrue(result['result']['archive_verified']);call.assert_called_once();job.result.assert_called_once()
            self.assertTrue(self.f.registry.readonly)
    def test_monitor_and_pending_collect_do_not_retrieve(self):
        self.plan();preview=self.preview();spec={'purpose':'RECOVERY','treatment':'M0_Baseline','slot':1,'account':'labK','scope_id':preview['scope_id'],'action':'monitor'}
        job=Mock();job.inputs={'pubs':[object()]};job.job_id.return_value='existing-job';job.session_id='batch';job.backend.return_value=SimpleNamespace(name='fixture');job.status.return_value='QUEUED'
        service=Mock();service.job.return_value=job
        import account_config,read_only_preflight,targeted_recovery
        with patch.object(account_config,'make_saved_profile_service',return_value=service),patch.object(read_only_preflight,'_instance_ref',return_value='ref'),patch.object(targeted_recovery,'collect_replacement') as collect:
            result=self.f.run_step('single_pub',single_pub=spec);self.assertEqual(result['result']['provider_status'],'QUEUED')
            spec['action']='collect';result=self.f.run_step('single_pub',single_pub=spec);self.assertEqual(result['result']['state'],'NOT_COLLECTED')
            job.result.assert_not_called();collect.assert_not_called()
            job.inputs={'pubs':[object(),object()]}
            with self.assertRaises(pipeline.CampaignStepBlocked):self.f.run_step('single_pub',single_pub=spec)
            job.inputs={'pubs':[object()]};service.job.side_effect=TimeoutError('fixture connection timeout')
            with self.assertRaises(TimeoutError):self.f.run_step('single_pub',single_pub=spec)
            collect.assert_not_called();self.assertTrue(self.f.registry.readonly)

if __name__=='__main__':unittest.main()
