"""Combined recovery regressions: fake IBM only, real atomic SQLite ledger."""
import hashlib
import json
from unittest.mock import Mock, patch
import unittest
from types import SimpleNamespace
import test_batch_progression as fixtures
import test_m7_recovery as m7_tests
import targeted_recovery as recovery
from batch_progression import evaluate_batch_progression
from runtime_estimator import workload_quota_decision


class CombinedTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BatchProgressionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.registry=self.f.registry
        self.target=self.f.target(25);self.target.shots_per_pub=4096;self.target.experiment_id='test-n25';self.ids=self.f.reserve(self.target,3)
        self.block='vqe-independent-n-01-n25-block-003';self.cid='vqe-independent-n-01'
        self.batch='d9e6cd69-775b-4597-9267-7e27a864879d';self.rid=self.ids['M7_ALL']
        with self.registry._transaction() as db:
            db.execute('UPDATE jobs SET logical_block_id=?,account_label=?,batch_id=?',(self.block,'labK',self.batch))
            db.execute('UPDATE experiment_blocks SET logical_block_id=?,sequence_no=3',(self.block,))
            db.execute("UPDATE jobs SET submission_state='RESERVED',job_id=NULL,validated_pub_count=0,metadata_json='{}' WHERE id=?",(self.rid,))
        for name,rid in self.ids.items():
            if name=='M7_ALL':continue
            self.registry.transition(rid,'SUBMITTING')
            self.registry.transition(rid,'SUBMITTED',job_id='original-'+name,batch_id=self.batch)
            self.registry.transition(rid,'COMPLETED')
            if name!='M3_ZNE':
                items=[dict(pub_index=i,expectation_valid=True,uncertainty_valid=True,uncertainty_state='PRESENT',expectation_values=[.2]*7,standard_deviations=[.01]*7) for i in range(1,6)]
                self.registry.record_pub_validation_records(rid,self.target.target_id,'e'*64,items)
                self.registry.update_pub_progress(rid,completed=5,collected=5,validated=5)
        self.folder=self.f.directory/'02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/03_IBM_execution_records/records';self.folder.mkdir(parents=True)
        self.enterContext(patch('vqe_adapter.record_directory',side_effect=lambda root,block:self.folder/block))
        self.estimate=SimpleNamespace(scope='missing_treatment_recovery',pub_count=5,treatment_id='M7_ALL',target_n=5,stress_block_seconds=35,defensible=True)
        self.service,self.factory,self.estimator=m7_tests.M7Tests.install_runner(self)
        self.service.job.return_value.status.return_value='DONE'
        self.valid_original=False
        self.collect=self.enterContext(patch('vqe_adapter.collect_block',side_effect=self.capture))
        self.enterContext(patch('vqe_adapter.treatment_options',side_effect=lambda name,*a:SimpleNamespace(treatment=name,environment=SimpleNamespace(job_tags=None))))
        self.enterContext(patch.object(recovery,'_verify_archived_treatment_options',return_value=None))
        self.enterContext(patch('runtime_estimator.targeted_replacement_estimate',return_value=SimpleNamespace(
            scope='single_treatment_replacement',treatment_id='M3_ZNE',pub_count=1,target_n=1,defensible=True,stress_block_seconds=20)))
        self.jobs={};self.counter=0
        self.estimator.return_value.run.side_effect=self.submit
        self.service.job.side_effect=lambda jid:self.jobs.get(jid,self.service.job.return_value)
        self.seed_original(0)

    def submit(self,pubs):
        self.counter+=1;job=Mock();job.job_id.return_value='recovery-'+str(self.counter)
        job.status.return_value='DONE';job.metrics.return_value={}
        job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=[.3]*7,stds=[.02]*7),metadata={}) for _ in pubs]
        self.jobs[job.job_id()]=job
        return job

    def seed_original(self,count):
        rid=self.ids['M3_ZNE'];row=next(r for r in self.registry.rows(self.block) if r['id']==rid)
        raw=self.folder/'original-m3.json'
        records=[dict(pub_index=i,expectation_validation='PASS' if i<=count else 'FAIL',
            uncertainty_status='PRESENT' if i<=count else 'INVALID',expectation_values=[.2]*7 if i<=count else None,
            standard_deviations=[.01]*7 if i<=count else None) for i in range(1,6)]
        payload=dict(job_id=row['job_id'],treatment_id='M3_ZNE',input_hashes=self.target.input_hashes,
            source_run='run_02',backend='ibm_fez',shots_per_pub=4096,records=records)
        raw.write_text(json.dumps(payload));digest=hashlib.sha256(raw.read_bytes()).hexdigest()
        self.registry.transition(rid,'COMPLETED',metadata={'per_pub_source_path':str(raw),'per_pub_source_sha256':digest})
        items=[dict(r,expectation_valid=True,uncertainty_valid=True,uncertainty_state='PRESENT') for r in records[:count]]
        self.registry.record_pub_validation_records(rid,self.target.target_id,digest,items)
        self.registry.update_pub_progress(rid,completed=5,collected=5,validated=count)

    def capture(self,root,block,**kw):
        rid=kw['job_row_id'];row=next(r for r in self.registry.rows(block) if r['id']==rid)
        if row['attempt']==1:raise OSError('Proxy retrieval unavailable')
        folder=self.folder/kw.get('archive_block_id',block)/row['treatment_id'];folder.mkdir(parents=True,exist_ok=True)
        raw=folder/'raw.json';raw.write_text('{}');digest=hashlib.sha256(raw.read_bytes()).hexdigest()
        records=[dict(pub_index=i,expectation_validation='PASS',uncertainty_status='PRESENT',expectation_values=[.2]*7,standard_deviations=[.01]*7) for i in range(1,6)]
        raw.with_name('raw.structured.json').write_text(json.dumps(dict(source_raw_sha256=digest,job_id=row['job_id'],treatment_id=row['treatment_id'],records=records)))
        items=[dict(r,expectation_valid=True,uncertainty_valid=True,uncertainty_state='PRESENT') for r in records]
        self.registry.record_pub_validation_records(rid,self.target.target_id,digest,items)
        self.registry.transition(rid,'COMPLETED',metadata={'per_pub_source_path':str(raw),'per_pub_source_sha256':digest})
        self.registry.update_pub_progress(rid,completed=5,collected=5,validated=5)

    def run_combined(self,**kw):
        return recovery.recover_m3_m7(self.f.directory,self.cid,self.block,authorize_live=True,
            authorize_m3_replacement=True,replacement_reason='Original DONE result unavailable after bounded read retries',
            service_factory=lambda _:self.service,batch_factory=self.factory,estimator_factory=self.estimator,**kw)

    def test_only_unresolved_pubs_one_batch_and_restart(self):
        self.seed_original(4)
        before=self.registry.pub_validation_records(self.target.target_id)
        result=self.run_combined()
        self.assertEqual(self.factory.call_count,1)
        self.assertEqual([len(c.args[0]) for c in self.estimator.return_value.run.call_args_list],[1,5])
        self.assertEqual(result['progression']['dataset_status'],'REPAIRED_COMPLETE')
        self.assertEqual(result['progression']['validated_slots'],40)
        self.assertFalse(result['batch004_submitted'])
        original=next(r for r in self.registry.rows(self.block) if r['id']==self.ids['M3_ZNE'])
        self.assertEqual(original['validated_pub_count'],4)
        self.assertEqual(original['submission_state'],'COMPLETED')
        after=self.registry.pub_validation_records(self.target.target_id)
        self.assertEqual(before,[r for r in after if r['job_row_id'] in self.ids.values()])
        self.run_combined();self.assertEqual(self.estimator.return_value.run.call_count,2)
        self.assertEqual(len(self.registry.repaired_pub_values(self.target.target_id)),40)

    def test_valid_m3_only_submits_m7(self):
        self.seed_original(5)
        result=self.run_combined()
        self.estimator.return_value.run.assert_called_once_with(['frozen-pub']*5)
        self.assertEqual(self.estimator.call_args.kwargs['options'].treatment,'M7_ALL')
        self.assertEqual(result['progression']['dataset_status'],'COMPLETE')

    def test_ambiguous_m7_blocks_both(self):
        with patch.object(recovery,'verify_missing_m7_absence',side_effect=RuntimeError('ambiguous M7')):
            with self.assertRaisesRegex(RuntimeError,'ambiguous'):self.run_combined()
        self.estimator.assert_not_called();self.assertEqual(len(self.registry.rows(self.block)),8)

    def test_lost_response_never_retries_and_can_reconcile(self):
        self.seed_original(4)
        self.estimator.return_value.run.side_effect=TimeoutError('lost response')
        with self.assertRaises(TimeoutError):self.run_combined()
        result=self.run_combined()
        self.assertEqual(self.estimator.return_value.run.call_count,1)
        self.assertFalse(result['progression']['eligible'])
        self.assertTrue(all(r['state']=='BLOCKED' for r in result['jobs']))
        row=self.registry.replacement_jobs(self.target.target_id)[0]
        job=self.submit(['pub']);job.tags.return_value=[row['metadata']['recovery_job_tag']]
        self.service.jobs.return_value=[job]
        result=self.run_combined()
        self.assertEqual(result['jobs'][0]['state'],'VALIDATED')
        self.assertEqual(self.estimator.return_value.run.call_count,1)

    def test_no_live_only_reads(self):
        result=recovery.recover_m3_m7(self.f.directory,self.cid,self.block,service_factory=lambda _:self.service)
        self.assertEqual(result['state'],'READ_ONLY');self.estimator.assert_not_called()

    def test_no_specific_m3_authorization(self):
        with self.assertRaisesRegex(RuntimeError,'authorize-m3-replacement'):
            recovery.recover_m3_m7(self.f.directory,self.cid,self.block,authorize_live=True,service_factory=lambda _:self.service)
        self.estimator.assert_not_called()

    def test_combined_quota_requires_sum_and_margin(self):
        estimate=SimpleNamespace(scope='combined_treatment_recovery',pub_count=10,treatment_ids=('M3_ZNE','M7_ALL'),defensible=True,stress_block_seconds=70)
        self.assertFalse(workload_quota_decision(estimate,remaining_seconds=39,workload_type='combined_recovery')['eligible'])
        self.assertTrue(workload_quota_decision(estimate,remaining_seconds=40,workload_type='combined_recovery')['eligible'])

    def test_tampered_archive_blocks_completion(self):
        self.seed_original(4);self.run_combined()
        row=self.registry.replacement_jobs(self.target.target_id)[0]
        from pathlib import Path
        Path(row['raw_archive_path']).write_text('tampered')
        self.assertFalse(evaluate_batch_progression(self.registry,self.target,self.block)['eligible'])

    def test_late_original_selected_once_without_overwriting_replacement(self):
        self.run_combined()
        rid=self.ids['M3_ZNE']
        items=[dict(pub_index=i,expectation_valid=True,uncertainty_valid=True,uncertainty_state='PRESENT',
                    expectation_values=[.4]*7,standard_deviations=[.02]*7) for i in range(1,6)]
        self.registry.record_pub_validation_records(rid,self.target.target_id,'f'*64,items)
        selected=self.registry.repaired_pub_values(self.target.target_id)
        self.assertEqual(len(selected),40)
        self.assertTrue(all(r['source']=='ORIGINAL' for r in selected if r['treatment_id']=='M3_ZNE'))
        self.assertEqual(len(self.registry.replacement_jobs(self.target.target_id)),5)

    def test_stale_count_reconciled_from_archive(self):
        self.seed_original(4)
        with self.registry._transaction() as db:
            db.execute('UPDATE jobs SET validated_pub_count=1 WHERE id=?',(self.ids['M3_ZNE'],))
        result=self.run_combined()
        self.assertEqual(result['m3_pub_audit']['valid'],[11,12,13,14])
        self.assertEqual([r['logical_pub_index'] for r in self.registry.replacement_jobs(self.target.target_id)],[15])

    def test_overstated_count_reconciled_without_losing_valid_pubs(self):
        self.seed_original(4)
        with self.registry._transaction() as db:
            db.execute('UPDATE jobs SET validated_pub_count=5 WHERE id=?',(self.ids['M3_ZNE'],))
        result=self.run_combined()
        self.assertEqual(result['m3_pub_audit']['validated_count'],4)
        self.assertEqual([r['logical_pub_index'] for r in self.registry.replacement_jobs(self.target.target_id)],[15])

    def test_interrupt_before_batch_never_resubmits(self):
        self.seed_original(4)
        self.factory.return_value.__enter__.side_effect=TimeoutError('Batch creation uncertain')
        with self.assertRaises(TimeoutError):self.run_combined()
        result=self.run_combined()
        self.estimator.return_value.run.assert_not_called()
        self.assertFalse(result['progression']['eligible'])
        self.assertEqual(self.factory.call_count,1)

    def test_interrupt_after_m3_before_m7_reconciles_without_repeating_m3(self):
        self.seed_original(4)
        def submit(pubs):
            if len(pubs)==5:raise TimeoutError('M7 response lost')
            return self.submit(pubs)
        self.estimator.return_value.run.side_effect=submit
        with self.assertRaises(TimeoutError):self.run_combined()
        result=self.run_combined()
        self.assertEqual(self.estimator.return_value.run.call_count,2)
        self.assertEqual(result['jobs'][0]['state'],'VALIDATED')
        self.assertEqual(result['jobs'][1]['state'],'BLOCKED')
        self.assertEqual(result['progression']['validated_slots'],35)

    def test_wrong_tag_cannot_attach_unknown_identical_circuit(self):
        self.seed_original(4)
        self.estimator.return_value.run.side_effect=TimeoutError('lost response')
        with self.assertRaises(TimeoutError):self.run_combined()
        unrelated=self.submit(['pub']);unrelated.tags.return_value=['different-slot']
        self.service.jobs.return_value=[unrelated]
        result=self.run_combined()
        self.assertEqual(result['jobs'][0]['state'],'BLOCKED')
        self.assertIsNone(self.registry.replacement_jobs(self.target.target_id)[0]['job_id'])

    def test_missing_pub_distinguished_from_invalid(self):
        self.seed_original(4)
        raw=self.folder/'original-m3.json';payload=json.loads(raw.read_text())
        payload['records']=payload['records'][:4];raw.write_text(json.dumps(payload))
        # A partial response is a different immutable source, so use a new fixture without a ledger.
        other=CombinedTests();other.setUp();self.addCleanup(other.doCleanups)
        raw2=other.folder/'original-m3.json'
        payload['job_id']='original-M3_ZNE';raw2.write_text(json.dumps(payload))
        digest=hashlib.sha256(raw2.read_bytes()).hexdigest()
        other.registry.transition(other.ids['M3_ZNE'],'COMPLETED',metadata={'per_pub_source_sha256':digest})
        result=other.run_combined()
        self.assertEqual(result['m3_pub_audit']['missing'],[15])
        self.assertEqual(result['m3_pub_audit']['invalid'],[])
        self.assertEqual(result['m3_pub_audit']['valid'],[11,12,13,14])

    def test_missing_archive_never_resubmits_valid_originals(self):
        self.seed_original(4);(self.folder/'original-m3.json').unlink()
        with self.assertRaisesRegex(RuntimeError,'unverified'):self.run_combined()
        self.estimator.assert_not_called()

    def test_cli_dispatch(self):
        import mxene
        with patch.object(recovery,'recover_m3_m7',return_value={}) as run:
            mxene.main(['recover-m3-m7','--live','--authorize-m3-replacement','--replacement-reason','unavailable'])
        self.assertTrue(run.call_args.kwargs['authorize_live'])
        self.assertTrue(run.call_args.kwargs['authorize_m3_replacement'])


def count_case(count):
    def test(self):
        self.seed_original(count)
        result=self.run_combined()
        self.assertEqual(result['m3_pub_audit']['valid'],list(range(11,11+count)))
        self.assertEqual(result['m3_pub_audit']['unresolved'],list(range(11+count,16)))
        self.assertEqual([len(c.args[0]) for c in self.estimator.return_value.run.call_args_list],[1]*(5-count)+[5])
        self.assertEqual(self.factory.call_count,1)
        self.assertEqual(result['progression']['validated_slots'],40)
        self.assertTrue(result['progression']['eligible'])
        self.assertEqual(len(self.registry.repaired_pub_values(self.target.target_id)),40)
    return test

for count in range(6):
    setattr(CombinedTests,f'test_m3_count_{count}',count_case(count))
