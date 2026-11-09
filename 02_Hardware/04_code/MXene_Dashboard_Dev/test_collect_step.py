"""Collect boundary fixtures. No IBM credentials, provider traffic or production writes."""
import copy,json,hashlib,uuid,unittest,shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import auto_broker
pipeline,campaign,runtime,config,registry_module=auto_broker.modules()
import vqe_adapter,batch_scheduler,account_config,read_only_preflight
from repository_paths import atomic_archive

class CollectTests(unittest.TestCase):
    def setUp(self):
        self.root=auto_broker.HERE/'state'/('collect-test-'+uuid.uuid4().hex);self.root.mkdir()
        self.block='campaign-n30-block-005';self.target=SimpleNamespace(target_id='target30',target_n=30,backend='backend',input_hashes={'qpy':'frozen'},treatment_ids=tuple('M'+str(i) for i in range(8)))
        self.rows=[];self.records=[];self.jobs={};self.registry=Mock();self.registry.readonly=True
        for i,t in enumerate(self.target.treatment_ids):
            row=dict(id=i+1,target_id='target30',logical_block_id=self.block,treatment_id=t,account_label='labK',backend='backend',batch_id='batch',job_id='job'+str(i),chunk_n=5,attempt=1,input_hashes={'qpy':'frozen'},submission_state='SUBMITTED',metadata={'instance':'instance','submit_started_utc':'time'})
            self.rows.append(row);folder=self.root/t;folder.mkdir()
            common={k:row[k] for k in ('job_id','logical_block_id','treatment_id','account_label','backend','input_hashes')}
            common.update(instance='instance',n=5,pubs_per_treatment=5)
            (folder/'job_submitted.json').write_text(json.dumps(dict(common,batch_id='batch',shots_requested_per_pub=4096)))
            (folder/'submission_state.json').write_text(json.dumps(dict(common,attempt_id='attempt'+str(i),status='SUBMITTED',submit_started_utc='time',requested_shots_per_pub=4096)))
            job=Mock();job.status.return_value='DONE';job.job_id.return_value=row['job_id'];job.result.return_value='fixture result';self.jobs[row['job_id']]=job
        self.registry.rows.side_effect=lambda *_:self.rows
        self.registry.target_status.return_value={'blocks':[{'sequence_no':5,'logical_block_id':self.block}]}
        self.registry.replacement_jobs.return_value=[];self.registry.pub_validation_records.side_effect=lambda *_:self.records
        self.service=Mock();self.service.job.side_effect=lambda jid:self.jobs[jid];self.service.active_instance.return_value='instance'
        self.authorization={'allowed_accounts':['labK'],'allowed_instance_refs':{'labK':['instance-ref']}}
        self.collect=Mock(side_effect=self.archive)
        patches=[patch.object(vqe_adapter,'record_directory',return_value=self.root),patch.object(vqe_adapter,'collect_block',self.collect),patch.object(batch_scheduler,'verify_reconciliation_job'),patch.object(account_config,'make_saved_profile_service',return_value=self.service),patch.object(read_only_preflight,'_instance_ref',return_value='instance-ref')]
        self.mocks=[p.start() for p in patches]
        for p in patches:self.addCleanup(p.stop)
        self.addCleanup(lambda:shutil.rmtree(self.root))
    def archive(self,*args,**kw):
        self.assertEqual(kw['ai_mode'],'python');self.assertEqual(len(kw['treatments']),1)
        row=next(r for r in self.rows if r['id']==kw['job_row_id'])
        self.assertFalse(self.registry.readonly)
        job=kw['service_override'].job(row['job_id']);job.result()
        folder=self.root/row['treatment_id']/'raw_retrievals';folder.mkdir(exist_ok=True)
        raw=folder/'fixture.json';payload=b'{"pubs":5}';digest=hashlib.sha256(payload).hexdigest();atomic_archive(raw,(payload,),digest)
        row['metadata'].update(per_pub_source_path=str(raw),per_pub_source_sha256=digest)
        row.update(submission_state='COMPLETED',validated_pub_count=5,collected_pub_count=5)
        for i in range(1,6):self.records.append(dict(job_row_id=row['id'],pub_index=i,source_sha256=digest,expectation_valid=1,uncertainty_valid=1,error_codes_json='[]'))
    def run_collect(self):return pipeline._collect_campaign_step(self.root,self.registry,self.target,self.authorization,5)
    def test_batch006_preview_is_local_readonly_and_pins_eight_jobs(self):
        self.registry.target_status.return_value={'blocks':[{'sequence_no':6,'logical_block_id':self.block}]}
        manifest={'backend':'backend','levels':[{'target_n':30}]}
        digest=campaign._sha(manifest);manifest['manifest_sha256']=digest
        self.registry.independent_campaign_status.return_value={'exists':True,'manifest':manifest,'manifest_sha256':digest}
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(registry_module,'JobRegistry',return_value=self.registry) as constructor,patch.object(pipeline,'_typed_target',return_value=self.target),patch.object(campaign,'_campaign_has_unknown',return_value=False),patch.object(config,'load_final_config',return_value={'allowed_backend':'backend'}),patch.object(config,'campaign_authorization_from_final_config',return_value={**self.authorization,'allowed_actions':['collect']}):
            preview=pipeline.preview_collect_step(self.root,'vqe-independent-n-01',30,6)
        self.assertEqual((preview['n'],preview['batch'],preview['operation']),(30,6,'COLLECT-only'))
        self.assertEqual({r['job_id'] for r in preview['jobs']},set(self.jobs))
        self.assertEqual(len(preview['scope_id']),64)
        self.assertEqual(constructor.call_args.kwargs,{'readonly':True})
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.mocks[3].assert_not_called();self.collect.assert_not_called()
        result=pipeline._collect_campaign_step(self.root,self.registry,self.target,self.authorization,6,expected_scope=preview['scope_id'])
        self.assertEqual(result['collected_jobs'],8)

    def test_changed_confirmation_scope_blocks_before_provider_or_writes(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(pipeline.CampaignStepBlocked,'scope changed'):
            pipeline._collect_campaign_step(self.root,self.registry,self.target,self.authorization,5,expected_scope='0'*64)
        self.mocks[3].assert_not_called();self.collect.assert_not_called()
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_preview_uses_same_exact_submission_identity_blocker(self):
        p=self.root/'M0/job_submitted.json';d=json.loads(p.read_text());d['job_id']='wrong';p.write_text(json.dumps(d))
        with self.assertRaisesRegex(pipeline.CampaignStepBlocked,'Submission file conflicts with registered job identity: job_id'):
            pipeline._collect_local_scope(self.root,self.registry,self.target,self.authorization,5)
        self.mocks[3].assert_not_called();self.collect.assert_not_called()
    def test_completed_duplicate_and_no_extra_operations(self):
        a=self.run_collect();self.assertEqual(a['collected_jobs'],8);self.assertEqual(a['valid_pubs'],40)
        b=self.run_collect();self.assertEqual(b['collected_jobs'],0);self.assertEqual(b['already_archived_jobs'],8)
        self.assertEqual(self.collect.call_count,8);self.assertTrue(self.registry.readonly)
    def test_completed_check_does_not_invoke_other_job(self):
        def forbidden(*a,**kw):kw['service_override'].job('not-registered')
        self.collect.side_effect=forbidden
        result=self.run_collect();self.assertEqual(result['collected_jobs'],0)
        self.assertFalse(result['archive_and_ledger_verified'])
        for job in self.jobs.values():job.result.assert_not_called()
    def test_existing_accepted_records_unchanged_after_partial_collect(self):
        first=self.rows[0]
        self.registry.readonly=False
        self.archive(service_override=SimpleNamespace(job=lambda _:self.jobs['job0']),job_row_id=first['id'],treatments=('M0',),ai_mode='python')
        self.registry.readonly=True
        before=copy.deepcopy(self.records)
        self.run_collect()
        self.assertEqual(before,self.records[:5]);self.jobs['job0'].result.assert_called_once()
    def test_provider_verification_failure_precedes_all_writes(self):
        self.mocks[2].side_effect=RuntimeError('fixture frozen mismatch')
        with self.assertRaises(RuntimeError):self.run_collect()
        self.collect.assert_not_called();self.assertTrue(self.registry.readonly)
    def test_pending_failed_partial(self):
        self.jobs['job0'].status.return_value='QUEUED';self.jobs['job1'].status.return_value='FAILED'
        result=self.run_collect();self.assertEqual(result['pending_jobs'],1);self.assertEqual(result['failed_jobs'],1)
        self.assertEqual(result['collected_jobs'],6);self.assertEqual(result['missing_pubs'],0);self.assertEqual(result['unvalidated_pubs'],10)
        self.jobs['job0'].result.assert_not_called();self.jobs['job1'].result.assert_not_called()
    def test_invalid_attempt_and_missing_submission(self):
        self.rows[0]['attempt']=2
        with self.assertRaises(pipeline.CampaignStepBlocked):self.run_collect()
        self.rows[0]['attempt']=1;(self.root/'M0/job_submitted.json').unlink()
        with self.assertRaises(pipeline.CampaignStepBlocked):self.run_collect()
        self.collect.assert_not_called();self.service.job.assert_not_called()
    def test_identity_conflicts_all_before_mutation(self):
        for key in ('job_id','batch_id','input_hashes'):
            path=self.root/'M7/job_submitted.json';original=path.read_text();doc=json.loads(original);doc[key]='wrong';path.write_text(json.dumps(doc))
            with self.assertRaises(pipeline.CampaignStepBlocked):self.run_collect()
            self.collect.assert_not_called();path.write_text(original)
    def test_interrupted_archive_cannot_report_success(self):
        self.collect.side_effect=OSError('fixture interrupted write')
        result=self.run_collect();self.assertFalse(result['archive_and_ledger_verified']);self.assertEqual(result['collected_jobs'],0)
        self.assertTrue(self.registry.readonly)
        self.collect.side_effect=self.archive
        self.assertEqual(self.run_collect()['collected_jobs'],8)
    def test_raw_hash_conflict_blocks_repeat(self):
        self.run_collect();(self.root/'M0/raw_retrievals/fixture.json').write_text('corrupt')
        with self.assertRaises(pipeline.CampaignStepBlocked):self.run_collect()
        self.assertEqual(self.collect.call_count,8)
    def test_unknown_provider_and_replacement_scope(self):
        self.jobs['job7'].status.return_value='UNKNOWN'
        with self.assertRaises(pipeline.CampaignStepBlocked):self.run_collect()
        self.collect.assert_not_called()
        self.registry.replacement_jobs.return_value=[{'logical_block_id':self.block}]
        with self.assertRaises(pipeline.CampaignStepBlocked):self.run_collect()
    def test_provider_changes_before_result(self):
        self.jobs['job0'].status.side_effect=['DONE','RUNNING']
        result=self.run_collect();self.assertEqual(result['collected_jobs'],0);self.assertFalse(result['archive_and_ledger_verified'])
        self.jobs['job0'].result.assert_not_called()
    def test_incomplete_publication_blocks_no_overwrite(self):
        (self.root/'M0/result.json').write_text('partial')
        with self.assertRaises(pipeline.CampaignStepBlocked):self.run_collect()
        self.collect.assert_not_called()
    def test_partial_pub_validation_counts(self):
        def partial(*a,**kw):
            self.archive(*a,**kw)
            self.records[-1].update(expectation_valid=0,uncertainty_valid=None,error_codes_json='["MISSING_PUB_RESULT"]')
            self.records[-2].update(expectation_valid=0,error_codes_json='["EXPECTATION_NONFINITE_OR_NONNUMERIC"]')
            next(r for r in self.rows if r['id']==kw['job_row_id']).update(validated_pub_count=3,collected_pub_count=4)
        self.collect.side_effect=partial
        result=self.run_collect();self.assertEqual((result['valid_pubs'],result['invalid_pubs'],result['missing_pubs']),(24,8,8))

if __name__=='__main__':unittest.main()
