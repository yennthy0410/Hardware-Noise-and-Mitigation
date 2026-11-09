"""Offline Batch admission/closure evidence regressions; no provider mutation."""
import json
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock,patch
import unittest
import test_m7_recovery as m7
import targeted_recovery as recovery


class ClosureTests(unittest.TestCase):
    def setUp(self):
        self.f=m7.M7Tests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.registry=self.f.registry;self.batch=self.f.batch
        self.now=datetime.now(timezone.utc)
        self.details={'id':self.batch,'backend_name':'ibm_fez','mode':'batch','state':'closed',
            'accepting_jobs':False,'closed_at':(self.now-timedelta(days=1)).isoformat()}
        self.service=Mock();self.service.active_instance.return_value='instance'
        jobs=[]
        for row in self.registry.rows(self.f.block):
            if row['id']==self.f.rid:continue
            self.registry.transition(row['id'],'COMPLETED',metadata={'instance':'instance'})
            job=Mock();job.job_id.return_value=row['job_id'];job.status.return_value='DONE';jobs.append(job)
        self.service.jobs.return_value=jobs

    def inspect(self,details=None,**kwargs):
        return recovery.inspect_runtime_batch_closure(self.service,self.batch,expected_instance='instance',
            batch_details=self.details if details is None else details,now=self.now,**kwargs)

    def verify(self,details=None,**kw):
        return recovery.verify_missing_m7_absence(self.f.f.directory,self.registry,self.f.block,self.service,
            batch_details=self.details if details is None else details,allow_uncollected_m3=True,**kw)

    def test_closed_missing_acceptance_is_sufficient(self):
        self.details.pop('accepting_jobs')
        evidence=self.verify()
        self.assertTrue(evidence['closure_evidence']['submission_closed'])
        provenance=evidence['closure_evidence']['accepting_jobs_evidence']
        self.assertFalse(provenance['field_present'])
        self.assertFalse(provenance['default_applied'])
        self.service.job.assert_not_called()

    def test_supported_handle_supplies_missing_identity_and_backend(self):
        details=dict(self.details,id=None,backend_name=None)
        handle=Mock();handle.session_id=self.batch;handle.backend.return_value='ibm_fez'
        handle.details.return_value=details;handle.status.return_value='Closed'
        with patch('qiskit_ibm_runtime.Batch.from_id',return_value=handle) as lookup:
            result=recovery.inspect_runtime_batch_closure(self.service,self.batch,expected_instance='instance',now=self.now)
        lookup.assert_called_once_with(self.batch,service=self.service)
        self.assertTrue(result['settled']);handle.close.assert_not_called()

    def test_transition_timestamp_replaces_missing_closed_at(self):
        self.details['timestamps']=[{'status':'closed','timestamp':self.details.pop('closed_at')}]
        self.assertTrue(self.inspect()['settled'])
        self.assertEqual(self.inspect()['closure_time_source'],'timestamps.closed')

    def test_optional_status_read_failure_keeps_conclusive_details(self):
        handle=Mock();handle.session_id=self.batch;handle.backend.return_value='ibm_fez'
        handle.details.return_value=self.details;handle.status.side_effect=TimeoutError('transient')
        with patch('qiskit_ibm_runtime.Batch.from_id',return_value=handle):
            result=recovery.inspect_runtime_batch_closure(self.service,self.batch,expected_instance='instance',now=self.now)
        self.assertTrue(result['settled'])
        self.assertEqual(result['provider_read_warnings'][0]['api'],'Batch.status')

    def test_expired_completed_require_explicit_nonacceptance(self):
        for state in ('expired','completed'):
            self.assertTrue(self.inspect(dict(self.details,state=state))['settled'])
            self.assertFalse(self.inspect(dict(self.details,state=state,accepting_jobs=None))['submission_closed'])

    def test_closed_with_accepting_true_is_conflict(self):
        result=self.inspect(dict(self.details,accepting_jobs=True))
        self.assertFalse(result['settled']);self.assertTrue(result['conflicts'])
        self.assertEqual(result['classification'],'CONTRADICTORY')

    def test_contradiction_collects_independent_inventory_without_authorizing(self):
        for job in self.service.jobs.return_value:job.session_id=self.batch
        with self.assertRaises(recovery.BatchClosureUnverified) as caught:
            self.verify(dict(self.details,accepting_jobs=True))
        audit=caught.exception.evidence['independent_inventory_evidence']
        self.assertTrue(audit['provider_history_complete'])
        self.assertTrue(audit['matches_only_original_m0_m6'])
        self.assertFalse(audit['recovery_authorized'])
        self.assertFalse(caught.exception.evidence['submission_closed'])
        self.assertEqual(self.service.jobs.call_count,3)
        self.assertGreater(self.service.jobs.call_args.kwargs['created_before'],
                           datetime.fromisoformat(self.details['closed_at']))
        self.assertTrue(all(call.kwargs['limit']==50 and call.kwargs['skip']==0
                            for call in self.service.jobs.call_args_list))

    def test_contradictory_inventory_failure_is_explicit_and_stays_blocked(self):
        self.service.jobs.side_effect=TimeoutError('offline')
        with self.assertRaises(recovery.BatchClosureUnverified) as caught:
            self.verify(dict(self.details,accepting_jobs=True))
        audit=caught.exception.evidence['independent_inventory_evidence']
        self.assertFalse(audit['provider_history_complete'])
        self.assertEqual(audit['read_error']['error_type'],'TimeoutError')

    def test_contradictory_inventory_extra_job_is_not_absence(self):
        for job in self.service.jobs.return_value:job.session_id=self.batch
        extra=Mock();extra.job_id.return_value='unmapped';extra.status.return_value='DONE';extra.session_id=self.batch
        self.service.jobs.return_value.append(extra)
        with self.assertRaises(recovery.BatchClosureUnverified) as caught:
            self.verify(dict(self.details,accepting_jobs=True))
        audit=caught.exception.evidence['independent_inventory_evidence']
        self.assertFalse(audit['matches_only_original_m0_m6'])
        self.assertTrue(audit['blockers'])

    def test_historical_ledger_unknown_blocks_reserved_intent(self):
        with self.registry._transaction() as db:
            db.execute('INSERT INTO recovery_events(job_row_id,event_type,details_json,created_at) VALUES(?,?,?,?)',
                       (self.f.rid,'M7_SUBMISSION_OUTCOME_UNKNOWN','{}',self.now.isoformat()))
        with self.assertRaisesRegex(RuntimeError,'event history contains submission evidence'):self.verify()
        self.service.jobs.assert_not_called()

    def test_native_true_is_not_overridden_by_derived_closed_status(self):
        handle=Mock();handle.session_id=self.batch;handle.backend.return_value='ibm_fez'
        handle.details.return_value=dict(self.details,accepting_jobs=True)
        handle.status.return_value='Closed'
        with patch('qiskit_ibm_runtime.Batch.from_id',return_value=handle):
            result=recovery.inspect_runtime_batch_closure(self.service,self.batch,expected_instance='instance',now=self.now)
        self.assertFalse(result['submission_closed'])
        self.assertTrue(result['execution_terminal'])
        self.assertEqual(result['accepting_jobs'],True)
        self.assertIn('GET /sessions/{id}',result['accepting_jobs_evidence']['source'])
        self.assertFalse(result['accepting_jobs_evidence']['stale_proven'])
        handle.close.assert_not_called()

    def test_stale_prior_acceptance_does_not_override_fresh_closed_details(self):
        prior={'batch_id':self.batch,'instance':'instance','submission_closed':False,
               'accepting_jobs':True,'observed_utc':(self.now-timedelta(days=2)).isoformat()}
        details=dict(self.details);details.pop('accepting_jobs')
        self.assertTrue(self.inspect(details,prior_observation=prior)['settled'])

    def test_fresh_explicit_provider_correction_resolves_contradiction(self):
        handle=Mock();handle.session_id=self.batch;handle.backend.return_value='ibm_fez'
        initial=dict(self.details,accepting_jobs=True)
        handle.details.side_effect=[initial,self.details]
        handle.status.return_value='Closed'
        with patch('qiskit_ibm_runtime.Batch.from_id',return_value=handle):
            result=recovery.inspect_runtime_batch_closure(self.service,self.batch,expected_instance='instance',now=self.now)
        self.assertTrue(result['settled'])
        self.assertEqual(result['closure_resolution'],'fresh_provider_closed_and_explicit_nonacceptance')
        self.assertEqual(result['previous_contradictory_observation']['provider_details'],initial)
        self.assertEqual(handle.details.call_count,2)
        handle.close.assert_not_called();handle.cancel.assert_not_called()

    def test_recheck_requires_explicit_same_batch_nonacceptance(self):
        for changes in ({'accepting_jobs':True},{'accepting_jobs':None},{'id':'other'},
                        {'state':'active'},{'backend_name':'other'}):
            with self.subTest(changes=changes):
                handle=Mock();handle.session_id=self.batch;handle.backend.return_value='ibm_fez'
                handle.details.side_effect=[dict(self.details,accepting_jobs=True),dict(self.details,**changes)]
                handle.status.return_value='Closed'
                with patch('qiskit_ibm_runtime.Batch.from_id',return_value=handle):
                    result=recovery.inspect_runtime_batch_closure(self.service,self.batch,expected_instance='instance',now=self.now)
                self.assertFalse(result['settled'])
                self.assertEqual(result['classification'],'CONTRADICTORY')

    def test_failed_recheck_preserves_original_contradiction(self):
        handle=Mock();handle.session_id=self.batch;handle.backend.return_value='ibm_fez'
        handle.details.side_effect=[dict(self.details,accepting_jobs=True),TimeoutError('offline')]
        handle.status.return_value='Closed'
        with patch('qiskit_ibm_runtime.Batch.from_id',return_value=handle):
            result=recovery.inspect_runtime_batch_closure(self.service,self.batch,expected_instance='instance',now=self.now)
        self.assertFalse(result['settled'])
        self.assertEqual(result['closure_recheck_error']['error_type'],'TimeoutError')

    def test_persisted_contradiction_requires_explicit_resolution_on_resume(self):
        with self.assertRaises(recovery.BatchClosureUnverified):
            self.verify(dict(self.details,accepting_jobs=True))
        missing=dict(self.details);missing.pop('accepting_jobs')
        for _ in range(2):
            with self.assertRaises(recovery.BatchClosureUnverified):self.verify(missing)
        self.assertTrue(self.verify()['provider_history_complete'])

    def test_closed_timestamp_does_not_make_current_native_true_stale(self):
        result=self.inspect(dict(self.details,accepting_jobs=True),batch_status='Closed')
        self.assertFalse(result['settled'])
        self.assertIn('resolution_required',result)

    def test_missing_state_and_acceptance_do_not_default_to_closed(self):
        details=dict(self.details);details.pop('state');details.pop('accepting_jobs')
        self.assertFalse(self.inspect(details)['submission_closed'])

    def test_active_acceptance_and_stale_closed_timestamp_remain_blocked(self):
        self.assertFalse(self.inspect(dict(self.details,state='active',accepting_jobs=True),
                                     batch_status='In progress, accepting new jobs')['settled'])

    def test_duplicate_original_identity_blocks(self):
        with self.registry._transaction() as db:
            db.execute("UPDATE jobs SET batch_id='different' WHERE treatment_id='M0_Baseline'")
        with self.assertRaisesRegex(RuntimeError,'seven distinct job IDs'):self.verify()
        self.service.jobs.assert_not_called()

    def test_done_jobs_do_not_prove_closure(self):
        with self.assertRaises(recovery.BatchClosureUnverified) as caught:
            self.verify(dict(self.details,state='active',accepting_jobs=True))
        self.assertEqual(caught.exception.evidence['provider_details']['state'],'active')
        self.assertTrue(caught.exception.evidence['missing_evidence'])
        self.service.jobs.assert_not_called();self.service.job.assert_not_called()

    def test_inactive_and_timeout_are_not_closure(self):
        details=dict(self.details,state='inactive',accepting_jobs=None,max_time=1,started_at=self.details['closed_at'])
        self.assertFalse(self.inspect(details,batch_status='Pending')['submission_closed'])

    def test_nonacceptance_without_timestamp_uses_persisted_observation(self):
        self.details.update(state='active',accepting_jobs=False,closed_at=None,
            last_job_completed=(self.now-timedelta(days=2)).isoformat())
        inspect=recovery.inspect_runtime_batch_closure
        with patch.object(recovery,'inspect_runtime_batch_closure',side_effect=lambda *a,**kw:inspect(*a,**kw,now=self.now)):
            with self.assertRaises(recovery.BatchClosureUnverified) as caught:self.verify()
        self.assertTrue(caught.exception.evidence['submission_closed'])
        self.assertFalse(caught.exception.evidence['settled'])
        self.assertEqual(len([e for e in self.registry.events(self.f.rid) if e['event_type']=='M7_BATCH_CLOSURE_OBSERVED']),1)
        with patch.object(recovery,'inspect_runtime_batch_closure',side_effect=lambda *a,**kw:inspect(*a,**kw,now=self.now+timedelta(seconds=121))):
            result=self.verify()
        self.assertTrue(result['provider_history_complete'])
        self.assertIsNone(result['closed_at'])
        self.assertEqual(result['closure_evidence']['classification'],'SEALED_TO_NEW_SUBMISSIONS')
        self.service.job.assert_not_called()

    def test_old_observation_never_overrides_now_accepting(self):
        prior={'batch_id':self.batch,'instance':'instance','submission_closed':True,
               'observed_utc':(self.now-timedelta(days=1)).isoformat()}
        result=self.inspect(dict(self.details,state='active',accepting_jobs=True),prior_observation=prior)
        self.assertFalse(result['submission_closed'])

    def test_string_false_is_not_boolean_evidence(self):
        self.assertTrue(self.inspect(dict(self.details,accepting_jobs='false'))['conflicts'])

    def test_identity_backend_and_instance_mismatches_block(self):
        for fields in ({'id':'another'},{'backend_name':'another'},{'mode':'dedicated'}):
            self.assertFalse(self.inspect(dict(self.details,**fields))['settled'])
        self.service.active_instance.return_value='another-instance'
        self.assertFalse(self.inspect()['settled'])

    def test_no_timestamp_and_no_prior_observation_has_precise_diagnostics(self):
        with self.assertRaises(recovery.BatchClosureUnverified) as caught:self.verify(dict(self.details,closed_at=None))
        result=caught.exception.evidence
        self.assertIn('provider_details',result);self.assertIn('retry_after_utc',result)
        self.assertTrue(any('120-second' in x for x in result['missing_evidence']))

    def test_provider_read_error_identifies_api_and_missing_fields(self):
        with patch('qiskit_ibm_runtime.Batch.from_id',side_effect=TimeoutError('offline')):
            with self.assertRaises(recovery.BatchClosureUnverified) as caught:
                recovery.inspect_runtime_batch_closure(self.service,self.batch,expected_instance='instance')
        self.assertEqual(caught.exception.evidence['provider_read_error'],{'api':'Batch.from_id','error_type':'TimeoutError'})
        self.assertIsNone(caught.exception.evidence['provider_details'])

    def test_unknown_m7_intent_blocks_even_when_closed(self):
        self.registry.transition(self.f.rid,'SUBMITTING');self.registry.transition(self.f.rid,'UNKNOWN')
        with self.assertRaisesRegex(RuntimeError,'submission evidence'):self.verify()
        self.service.jobs.assert_not_called()

    def test_historical_m7_intent_blocks_even_if_current_file_reserved(self):
        path=self.f.folder/'M7_ALL/submission_state.json';state=json.loads(path.read_text())
        state['reconciliation_history']=[{'status':'UNKNOWN'}];path.write_text(json.dumps(state))
        with self.assertRaisesRegex(RuntimeError,'historical'):self.verify()
        self.service.jobs.assert_not_called()

    def test_extra_job_blocks_and_preserves_originals(self):
        before=self.registry.rows(self.f.block)
        extra=Mock();extra.job_id.return_value='unmapped-m7'
        self.service.jobs.return_value.append(extra)
        with self.assertRaisesRegex(RuntimeError,'additional or missing'):self.verify()
        self.assertEqual(before,self.registry.rows(self.f.block))

    def test_cli_read_only_diagnostic(self):
        import mxene
        with patch.object(recovery,'inspect_m7_absence',return_value={'state':'BLOCKED','submission_performed':False}) as inspect:
            self.assertEqual(mxene.main(['inspect-m7-absence']),2)
        inspect.assert_called_once()
