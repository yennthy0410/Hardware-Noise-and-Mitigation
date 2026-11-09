"""Incident-scoped first submission: real ledger, offline provider, no QPU calls."""
import json
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock,patch
import test_m7_recovery as fixtures
import targeted_recovery as recovery


class FirstSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.M7Tests();self.f.setUp(missing_m3=True)
        self.addCleanup(self.f.doCleanups)
        self.real_verify=recovery.verify_missing_m7_absence
        self.service,self.batch,self.estimator=self.f.install_runner()
        self.old=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        with self.f.registry._transaction() as db:
            db.execute('UPDATE jobs SET created_at=?',(self.old,))
        self.jobs=[]
        for row in self.f.registry.rows(self.f.block):
            if row['treatment_id']=='M7_ALL':continue
            self.f.registry.transition(row['id'],'COMPLETED',metadata={'instance':'instance'})
            job=Mock();job.job_id.return_value=row['job_id'];job.status.return_value='DONE'
            job.session_id=self.f.batch;self.jobs.append(job)
        self.service.jobs.side_effect=lambda **kw: [] if kw.get('pending') else list(self.jobs)
        self.details={'id':self.f.batch,'backend_name':'ibm_fez','mode':'batch','state':'closed',
                      'closed_at':self.old,'accepting_jobs':True}
        handle=Mock();handle.session_id=self.f.batch;handle.backend.return_value='ibm_fez'
        handle.details.return_value=self.details;handle.status.return_value='Closed'
        self.enterContext(patch('qiskit_ibm_runtime.Batch.from_id',return_value=handle))
        self.audit=self.enterContext(patch.object(recovery,'verify_missing_m7_absence',wraps=self.real_verify))
        self.enterContext(patch('vqe_adapter.record_directory',side_effect=lambda root,block:
            self.f.folder if block==self.f.block else self.f.folder/'recovery'))
        self.original=next(r for r in self.f.registry.rows() if r['id']==self.f.rid)
        self.original_file=(self.f.folder/'M7_ALL/submission_state.json').read_bytes()
        self.other_rows=[r for r in self.f.registry.rows() if r['treatment_id']!='M7_ALL']
        self.pubs=self.f.registry.pub_validation_records(self.f.target.target_id)
        self.assertEqual(len(self.pubs),34)

    def run_first(self,authorized=True):
        return recovery.recover_missing_m7(self.f.f.directory,self.f.cid,self.f.block,
            authorize_live=True,confirm_m7_only=True,authorize_m7_first_submission=authorized,
            service_factory=lambda _:self.service,batch_factory=self.batch,estimator_factory=self.estimator)

    def test_exact_first_submission_preserves_original_provenance_and_resumes(self):
        self.run_first()
        self.assertEqual(self.audit.call_count,2)
        self.estimator.return_value.run.assert_called_once_with(['frozen-pub']*5)
        original=next(r for r in self.f.registry.rows() if r['id']==self.f.rid)
        self.assertEqual(original['submission_state'],'CONFIRMED_NOT_SUBMITTED')
        self.assertEqual(original['quota_reserved_seconds'],self.original['quota_reserved_seconds'])
        self.assertEqual(original['metadata'],self.original['metadata'])
        self.assertEqual(self.original_file,(self.f.folder/'M7_ALL/submission_state.json').read_bytes())
        latest=max(self.f.registry.rows(),key=lambda r:r['id'])
        proof=latest['metadata']['m7_absence_evidence']
        self.assertFalse(proof['original_batch_closure_verified'])
        self.assertTrue(proof['closure_evidence']['provider_details']['accepting_jobs'])
        self.assertEqual(len(proof['provider_inventory_snapshots']),4)
        events=self.f.registry.events(self.f.rid)
        event=next(e for e in events if e['event_type']=='M7_FIRST_SUBMISSION_AUTHORIZED')
        snapshot=json.loads(event['details_json'])['original_reservation']
        self.assertEqual(snapshot['submission_state'],'RESERVED')
        self.assertIsNone(snapshot['job_id'])
        self.assertTrue((self.f.folder/'recovery/M7_ALL/job_submitted.json').is_file())
        self.run_first()
        self.estimator.return_value.run.assert_called_once()
        self.assertEqual(self.other_rows,[r for r in self.f.registry.rows() if r['treatment_id']!='M7_ALL'])
        self.assertEqual(self.pubs,self.f.registry.pub_validation_records(self.f.target.target_id))

    def test_no_explicit_first_submission_authorization_preserves_gate(self):
        with self.assertRaises(recovery.BatchClosureUnverified):self.run_first(False)
        self.batch.assert_not_called();self.estimator.assert_not_called()

    def test_new_m7_outside_batch_at_final_recheck_blocks(self):
        reads=0
        def jobs(**kw):
            nonlocal reads
            if kw.get('pending'):return []
            reads+=1
            if reads>=6 and 'session_id' not in kw:
                extra=Mock();extra.job_id.return_value='outside-m7';return self.jobs+[extra]
            return list(self.jobs)
        self.service.jobs.side_effect=jobs
        with self.assertRaises(recovery.BatchClosureUnverified):self.run_first()
        self.batch.assert_not_called();self.estimator.assert_not_called()
        self.assertEqual(self.original,next(r for r in self.f.registry.rows() if r['id']==self.f.rid))

    def test_incomplete_inventory_blocks(self):
        self.jobs.pop()
        with self.assertRaises(recovery.BatchClosureUnverified):self.run_first()
        self.batch.assert_not_called()

    def test_scientific_identity_mismatch_blocks(self):
        with patch('batch_scheduler.verify_reconciliation_job',side_effect=RuntimeError('wrong treatment')):
            with self.assertRaises(recovery.BatchClosureUnverified):self.run_first()
        self.batch.assert_not_called()

    def test_unknown_blocks_before_any_submission(self):
        with self.f.registry._transaction() as db:
            db.execute("UPDATE jobs SET submission_state='UNKNOWN' WHERE id=?",(self.f.ids['M3_ZNE'],))
        with self.assertRaises(RuntimeError):self.run_first()
        self.batch.assert_not_called()

    def test_lost_acceptance_blocks_retry_and_keeps_original_files(self):
        self.estimator.return_value.run.side_effect=TimeoutError('lost acceptance')
        with self.assertRaises(TimeoutError):self.run_first()
        self.assertEqual(self.run_first()['state'],'BLOCKED')
        self.estimator.return_value.run.assert_called_once()
        self.assertEqual(self.original_file,(self.f.folder/'M7_ALL/submission_state.json').read_bytes())

    def test_original_batch_cannot_be_reused(self):
        self.batch.return_value.__enter__.return_value.session_id=self.f.batch
        with self.assertRaisesRegex(RuntimeError,'NEW IBM Batch'):self.run_first()
        self.estimator.assert_not_called()

    def test_durable_job_id_survives_interruption_without_resubmission(self):
        self.batch.return_value.__exit__.side_effect=TimeoutError('after job ID commit')
        with self.assertRaises(TimeoutError):self.run_first()
        self.batch.return_value.__exit__.side_effect=None
        self.assertEqual(self.run_first()['job_id'],'new-m7')
        self.estimator.return_value.run.assert_called_once()

    def test_done_collects_only_new_m7_into_separate_archive(self):
        from types import SimpleNamespace
        job=self.service.job.return_value;job.status.return_value='DONE'
        job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=[.2]*7,stds=[.01]*7),metadata={}) for _ in range(5)]
        def collect(*args,**kwargs):
            rid=kwargs['job_row_id']
            records=[dict(pub_index=i,expectation_valid=True,uncertainty_valid=True,uncertainty_state='PRESENT',
                          expectation_values=[.2]*7,standard_deviations=[.01]*7) for i in range(1,6)]
            self.f.registry.record_pub_validation_records(rid,self.f.target.target_id,'e'*64,records)
            self.f.registry.update_pub_progress(rid,completed=5,collected=5,validated=5)
            self.f.registry.transition(rid,'COMPLETED',qpu_usage_seconds=1)
        with patch('vqe_adapter.collect_block',side_effect=collect) as collector:
            result=self.run_first()
        self.assertEqual(collector.call_args.kwargs['treatments'],('M7_ALL',))
        self.assertEqual(collector.call_args.kwargs['archive_block_id'],self.f.block+'-m7-first-submission')
        self.assertFalse(result['batch004_submitted'])
        self.assertEqual(len(self.f.registry.pub_validation_records(self.f.target.target_id)),39)
        self.assertEqual(self.pubs,[r for r in self.f.registry.pub_validation_records(self.f.target.target_id)
                                  if r['job_row_id'] in self.f.ids.values()])
        self.assertEqual(self.other_rows,[r for r in self.f.registry.rows() if r['treatment_id']!='M7_ALL'])

    def test_cli_passes_explicit_authorization(self):
        import mxene
        with patch.object(recovery,'recover_missing_m7',return_value={}) as run:
            mxene.main(['recover-m7','--live','--confirm-m7-only','--authorize-m7-first-submission'])
        self.assertTrue(run.call_args.kwargs['authorize_m7_first_submission'])
