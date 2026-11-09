"""Offline scope adapters: fixture archives and mocked providers only."""
import copy
import unittest
from unittest.mock import patch,Mock
import test_collect_step as fixtures
import manual_steps as manual


class ManualTests(unittest.TestCase):
    # Reuse the eight-Job fixture, without rerunning the inherited Batch test suite.
    def setUp(self):
        fixtures.CollectTests.setUp(self)
        self.target.chunk_n=5
        self.authorization['allowed_actions']=['monitor','collect']
        self.status={'campaign_id':'vqe-independent-n-01','manifest':{'levels':[{'target_n':30}]}}
        p=patch.object(fixtures.pipeline,'_typed_target',return_value=self.target);p.start();self.addCleanup(p.stop)

    archive=fixtures.CollectTests.archive

    def run_manual(self,**scope):
        scope={'kind':'job','action':'collect','job_id':'job0',**scope}
        preview=manual.resolve(self.root,self.registry,self.status,self.authorization,30,scope)[2]
        return manual.execute_manual_scope(self.root,self.registry,self.status,self.authorization,30,scope|{'scope_id':preview['scope_id']})

    def test_manual_single_job_and_duplicate(self):
        self.assertEqual(self.run_manual()['collected_jobs'],1)
        self.assertEqual(self.run_manual()['already_archived_jobs'],1)
        self.collect.assert_called_once()
        for job in list(self.jobs.values())[1:]:job.result.assert_not_called()

    def test_manual_pub_keeps_all_parent_pubs(self):
        report=self.run_manual(kind='pub',slot=23,treatment='M0')
        self.assertEqual(report['valid_pubs'],5)
        self.assertEqual(len(self.records),5)
        self.assertEqual(report['selected_pub']['records'][0]['pub_index'],3)
        self.assertTrue(report['selected_pub']['parent_result_preserved'])
        self.jobs['job0'].result.assert_called_once()
        self.run_manual(kind='pub',slot=24,treatment='M0')
        self.jobs['job0'].result.assert_called_once()

    def test_manual_bad_mapping_has_no_writes(self):
        for scope in ({'slot':26,'treatment':'M0'},{'slot':23,'treatment':'M1'}):
            with self.assertRaises(fixtures.pipeline.CampaignStepBlocked):self.run_manual(kind='pub',**scope)
        self.collect.assert_not_called();self.service.job.assert_not_called()

    def test_manual_status_reads_no_result(self):
        before=copy.deepcopy(self.rows)
        r=self.run_manual(action='monitor')
        self.assertEqual(r['status_counts']['DONE'],1)
        self.assertEqual(r['collected_jobs'],0)
        self.assertEqual(self.rows,before);self.assertTrue(self.registry.readonly)
        self.collect.assert_not_called();self.jobs['job0'].result.assert_not_called()

    def test_manual_batch_partial_arbitrary_registered_sequence(self):
        self.registry.target_status.return_value={'blocks':[{'sequence_no':4,'logical_block_id':self.block}]}
        self.jobs['job7'].status.return_value='RUNNING'
        r=self.run_manual(kind='batch',block_id=self.block,job_id=None)
        self.assertEqual((r['collected_jobs'],r['status_counts']['RUNNING']),(7,1))
        self.jobs['job7'].result.assert_not_called()

    def test_manual_stale_confirmation_blocks(self):
        with self.assertRaisesRegex(fixtures.pipeline.CampaignStepBlocked,'scope changed'):
            manual.execute_manual_scope(self.root,self.registry,self.status,self.authorization,30,{'kind':'job','action':'collect','job_id':'job0','scope_id':'0'*64})
        self.collect.assert_not_called();self.service.job.assert_not_called()

    def test_manual_external_readonly_and_import_blocked(self):
        self.jobs['job0'].backend.return_value.name='backend'
        with patch.object(fixtures.account_config,'load_saved_account_profiles',return_value={'labK':{}}):
            r=self.run_manual(kind='external',action='monitor',account='labK')
        self.assertEqual(r['status_counts']['DONE'],1)
        self.assertIn('BLOCKED',r['import_status'])
        with self.assertRaisesRegex(fixtures.pipeline.CampaignStepBlocked,'provenance'):
            self.run_manual(kind='external',account='labK')
        self.collect.assert_not_called();self.jobs['job0'].result.assert_not_called()

    def test_manual_provider_failure_has_no_writes(self):
        self.service.job.side_effect=OSError('offline provider')
        with self.assertRaisesRegex(fixtures.pipeline.CampaignStepBlocked,'Provider identity/status'):
            self.run_manual()
        self.assertTrue(self.registry.readonly);self.collect.assert_not_called()

    def test_manual_recovery_delegates_existing_single_pub(self):
        item={'job_id':'recovery','logical_pub_index':23,'treatment_id':'M0','account_label':'labK','attempt':2,'state':'VALIDATED','raw_archive_path':'fixture'}
        self.registry.replacement_jobs.return_value=[item]
        preview={'state':'RECORDED','job_id':'recovery','scope_id':'b'*64}
        result={'state':'VALIDATED','archive_verified':True,'validation':'VALIDATED','provider_status':'DONE','checked_at':'fixture-time'}
        with patch.object(fixtures.pipeline,'preview_single_pub',return_value=preview),patch.object(fixtures.pipeline,'_single_pub_read_step',return_value=result) as collect:
            r=self.run_manual(job_id='recovery')
            self.assertEqual(r['already_archived_jobs'],1)
            self.assertEqual(r['collected_jobs'],0)
            self.assertEqual(collect.call_args.args[-1]['slot'],23)
        self.collect.assert_not_called()

    def test_manual_grouped_recovery_blocked(self):
        self.registry.replacement_jobs.return_value=[{'job_id':'recovery'},{'job_id':'recovery'}]
        with self.assertRaisesRegex(fixtures.pipeline.CampaignStepBlocked,'Grouped'):
            self.run_manual(job_id='recovery')
        self.collect.assert_not_called()


