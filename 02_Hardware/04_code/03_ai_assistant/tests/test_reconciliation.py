"""Offline interrupted-submission reconciliation; no IBM calls."""
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import test_batch_progression as fixtures
import batch_scheduler as scheduler
from worker_runtime import WorkerLock

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BatchProgressionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.registry=self.f.registry
        self.target=self.f.target()
        self.ids=self.f.reserve(self.target,1)
        self.block='n10-block-001'
        self.folder=self.f.directory/'records';self.folder.mkdir()
        self.enterContext(patch('vqe_adapter.record_directory',return_value=self.folder))
        self.enterContext(patch.object(self.registry,'independent_campaign_context',return_value=(None,None)))
        self.rowid=self.ids['M6_TREX_ZNE']
        self.service=Mock();self.service.job.return_value.backend.return_value.name='ibm_fez'

    def test_reserved_preserved_on_restart(self):
        # Historical reservations must not be retroactively resized by the new policy.
        with self.registry._transaction() as db:
            db.execute('UPDATE jobs SET quota_reserved_seconds=1')
        self.registry.transition(self.rowid,'SUBMITTING')
        scheduler.recover_interrupted_block(self.registry,self.block,confirm_process_stopped=True)
        rows={r['treatment_id']:r for r in self.registry.rows(self.block)}
        self.assertEqual(rows['M6_TREX_ZNE']['submission_state'],'UNKNOWN')
        self.assertEqual(rows['M7_ALL']['submission_state'],'RESERVED')
        self.assertEqual(rows['M7_ALL']['quota_reserved_seconds'],1)

    def test_lock_blocks_mutation(self):
        with WorkerLock(self.folder/'worker.lock'):
            with self.assertRaisesRegex(RuntimeError,'worker'):
                scheduler.recover_interrupted_block(self.registry,self.block,confirm_process_stopped=True)
        self.assertTrue(all(r['submission_state']=='RESERVED' for r in self.registry.rows(self.block)))

    def test_missing_provider_evidence_leaves_submitting(self):
        self.registry.transition(self.rowid,'SUBMITTING')
        with patch.object(scheduler,'verify_reconciliation_job',side_effect=RuntimeError('Batch mismatch')):
            with self.assertRaisesRegex(RuntimeError,'Batch mismatch'):
                scheduler.reconcile_unknown_job(self.registry,self.rowid,self.service,job_id='provider-six',confirm_job_matches=True)
        row=next(r for r in self.registry.rows() if r['id']==self.rowid)
        self.assertEqual(row['submission_state'],'SUBMITTING');self.assertIsNone(row['job_id'])

    def test_verified_submitting_reconciles_and_keeps_reserved(self):
        self.registry.transition(self.rowid,'SUBMITTING')
        with patch.object(scheduler,'verify_reconciliation_job',return_value={'batch_id':'batch','frozen_inputs_verified':True}):
            result=scheduler.reconcile_unknown_job(self.registry,self.rowid,self.service,job_id='provider-six',confirm_job_matches=True)
        self.assertEqual(result['job_id'],'provider-six')
        rows={r['treatment_id']:r for r in self.registry.rows()}
        self.assertEqual(rows['M6_TREX_ZNE']['submission_state'],'SUBMITTED')
        self.assertEqual(rows['M7_ALL']['submission_state'],'RESERVED')
        self.assertTrue((self.folder/'M6_TREX_ZNE/job_submitted.json').is_file())
        self.service.run.assert_not_called()

    def test_nonempty_search_never_confirms_absence(self):
        self.service.active_instance.return_value=None
        self.service.jobs.return_value=[Mock()]
        with self.assertRaisesRegex(RuntimeError,'candidate jobs'):
            scheduler.reconcile_unknown_job(self.registry,self.rowid,self.service,confirm_no_matching_job=True,quiet_period_seconds=0)
        self.assertTrue(all(r['submission_state']=='RESERVED' for r in self.registry.rows()))

    def test_duplicate_provider_id_rejected(self):
        other=self.ids['M0_Baseline'];self.registry.transition(other,'SUBMITTING')
        self.registry.transition(other,'SUBMITTED',job_id='taken')
        with patch.object(scheduler,'verify_reconciliation_job',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'another registry intent'):
                scheduler.reconcile_unknown_job(self.registry,self.rowid,self.service,job_id='taken',confirm_job_matches=True)
        self.assertEqual(next(r for r in self.registry.rows() if r['id']==self.rowid)['submission_state'],'RESERVED')

    def test_local_record_conflict_does_not_attach_job(self):
        import json
        path=self.folder/'M6_TREX_ZNE';path.mkdir()
        (path/'job_submitted.json').write_text(json.dumps({'job_id':'different'}))
        with patch.object(scheduler,'verify_reconciliation_job',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'local record conflict'):
                scheduler.reconcile_unknown_job(self.registry,self.rowid,self.service,job_id='candidate',confirm_job_matches=True)
        self.assertIsNone(next(r for r in self.registry.rows() if r['id']==self.rowid)['job_id'])

    def test_verified_reserved_can_attach_without_guessing_absence(self):
        with patch.object(scheduler,'verify_reconciliation_job',return_value={'batch_id':'batch'}):
            scheduler.reconcile_unknown_job(self.registry,self.rowid,self.service,job_id='verified',confirm_job_matches=True)
        self.assertEqual(next(r for r in self.registry.rows() if r['id']==self.rowid)['submission_state'],'SUBMITTED')

    def test_provider_identity_and_science_verifier(self):
        from datetime import datetime, timezone
        from dataclasses import make_dataclass
        from qiskit import QuantumCircuit
        row=next(r for r in self.registry.rows() if r['id']==self.rowid)
        row['batch_id']='batch';row['metadata']['instance']='instance'
        row['metadata']['submit_started_utc']=row['created_at']
        circuit=QuantumCircuit(7);observables=['ZIIIIII']*7
        job=Mock();job.backend.return_value.name='ibm_fez'
        job.instance.return_value='instance'
        job.session_id='batch';job.creation_date=datetime.now(timezone.utc)
        job.inputs={'pubs':[(circuit,observables)]*5,'options':{'default_shots':4096,'resilience':{'zne_mitigation':True},'environment':{'log_level':'INFO'}}}
        options=make_dataclass('Options',[('default_shots',int),('resilience',dict),('environment',dict)])(4096,{'zne_mitigation':True},{'log_level':'WARNING'})
        self.service.active_instance.return_value='instance'
        with patch('repository_paths.frozen_source',return_value=self.folder), \
             patch('repository_paths.source_hashes',return_value=row['input_hashes']), \
             patch('vqe_adapter.verify_frozen_inputs',return_value=(circuit,observables,[0]*21,list(range(140,147)),None,None)), \
             patch('vqe_adapter.treatment_options',return_value=options):
            self.assertTrue(scheduler.verify_reconciliation_job(row,job,self.service)['frozen_inputs_verified'])
            row['metadata']['submit_started_utc']='2026-11-06T21:32:28.782101+00:00'
            job.creation_date='2026-11-07T05:32:28+08:00'
            self.assertTrue(scheduler.verify_reconciliation_job(row,job,self.service)['frozen_inputs_verified'])
            job.creation_date='2026-11-07T05:32:27+08:00'
            with self.assertRaisesRegex(RuntimeError,'precedes submission'):
                scheduler.verify_reconciliation_job(row,job,self.service)
            job.creation_date='2026-11-07T05:32:28+08:00'
            job.job_id.return_value='known';row['job_id']='known'
            self.assertEqual(scheduler.verify_reconciliation_job(row,job,self.service)['identity_basis'],'recorded_job_id')
            job.job_id.return_value='wrong'
            with self.assertRaisesRegex(RuntimeError,'recorded job ID'):
                scheduler.verify_reconciliation_job(row,job,self.service)
            job.job_id.return_value='known'
            row['metadata']['instance']=None
            self.assertTrue(scheduler.verify_reconciliation_job(row,job,self.service)['frozen_inputs_verified'])
            row['metadata']['instance']=''
            self.assertTrue(scheduler.verify_reconciliation_job(row,job,self.service)['frozen_inputs_verified'])
            row['job_id']=None
            with self.assertRaisesRegex(RuntimeError,'Missing intent instance'):
                scheduler.verify_reconciliation_job(row,job,self.service)
            row['job_id']='known';row['metadata']['instance']='instance'
            job.instance.return_value='crn:other'
            self.service.instances.return_value=[{'name':'instance','crn':'crn:expected'}]
            with self.assertRaisesRegex(RuntimeError,'differs'):
                scheduler.verify_reconciliation_job(row,job,self.service)
            job.instance.return_value='instance'
            job.session_id='other'
            with self.assertRaisesRegex(RuntimeError,'Batch ID'):
                scheduler.verify_reconciliation_job(row,job,self.service)
            job.session_id='batch';job.inputs['options']['resilience']['zne_mitigation']=False
            with self.assertRaisesRegex(RuntimeError,'treatment mismatch'):
                scheduler.verify_reconciliation_job(row,job,self.service)
            job.inputs['options']['resilience']['zne_mitigation']=True
            wrong=QuantumCircuit(7);wrong.x(0);job.inputs['pubs']=[(wrong,observables)]*5
            with self.assertRaisesRegex(RuntimeError,'circuit'):
                scheduler.verify_reconciliation_job(row,job,self.service)

    def test_timestamp_offsets_and_missing_timezone(self):
        self.assertEqual(scheduler._reconciliation_utc('2026-11-07T05:32:28+08:00'),
                         scheduler._reconciliation_utc('2026-11-06T21:32:28Z'))
        with self.assertRaisesRegex(RuntimeError,'timezone-aware'):
            scheduler._reconciliation_utc('2026-11-06T21:32:28')

    def test_atomic_link_is_idempotent_preserves_intent_and_audit(self):
        self.registry.transition(self.rowid,'SUBMITTING',metadata={'submit_started_utc':'2026-11-06T21:32:28.782101+00:00'})
        row=next(r for r in self.registry.rows() if r['id']==self.rowid)
        self.assertTrue(self.registry.link_reconciled_job(row,'provider-six',{'verified':True}))
        self.assertFalse(self.registry.link_reconciled_job(row,'provider-six',{'verified':True}))
        after=next(r for r in self.registry.rows() if r['id']==self.rowid)
        self.assertEqual(after['metadata']['submit_started_utc'],row['metadata']['submit_started_utc'])
        with self.registry._connection() as db:
            count=db.execute("SELECT COUNT(*) FROM recovery_events WHERE job_row_id=? AND event_type='AMBIGUOUS_JOB_FOUND'",(self.rowid,)).fetchone()[0]
        self.assertEqual(count,1)
        with self.assertRaisesRegex(RuntimeError,'historical'):
            self.registry.link_reconciled_job(row,'different',{})

    def cli_fixture(self):
        import numpy as np
        jobs={}
        for name,rid in self.ids.items():
            self.registry.transition(rid,'RESERVED',batch_id='batch',metadata={'instance':None if name=='M7_ALL' else 'instance'})
            if name=='M7_ALL':continue
            self.registry.transition(rid,'SUBMITTING',metadata={'submit_started_utc':'2026-11-06T21:32:28+00:00'})
            jid='job-'+name
            if name!='M6_TREX_ZNE':self.registry.transition(rid,'SUBMITTED',job_id=jid)
            job=Mock();job.job_id.return_value=jid;job.status.return_value='DONE'
            job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=np.zeros(7),stds=np.ones(7)*.1),metadata={}) for _ in range(5)]
            jobs[jid]=job
        service=Mock();service.active_instance.return_value='instance'
        service.jobs.return_value=list(reversed(list(jobs.values())))
        service.job.side_effect=lambda jid:jobs[jid]
        return jobs,service

    def test_cli_collects_only_verified_seven_and_restart_skips_validated(self):
        import hashlib
        jobs,service=self.cli_fixture();calls=[]
        def collect(*args,**kwargs):
            name=kwargs['treatments'][0];calls.append(name)
            result=kwargs['service_override'].job('job-'+name).result()
            self.assertEqual(len(result),5)
            with self.assertRaises(RuntimeError):kwargs['service_override'].job('job-M7_ALL')
            registry=kwargs['registry'];row=next(r for r in registry.rows() if r['treatment_id']==name)
            raw=self.f.directory/(name+'.json');raw.write_text('{}');digest=hashlib.sha256(raw.read_bytes()).hexdigest()
            registry.record_pub_validation_records(row['id'],row['target_id'],digest,[dict(pub_index=i,
                expectation_valid=True,uncertainty_valid=True,uncertainty_state='PRESENT',
                expectation_values=[0.0]*7,standard_deviations=[.1]*7) for i in range(1,6)])
            registry.transition(row['id'],'COMPLETED',metadata={'per_pub_source_path':str(raw),'per_pub_source_sha256':digest})
            registry.update_pub_progress(row['id'],completed=5,collected=5,validated=5)
        m7=next(r for r in self.registry.rows() if r['treatment_id']=='M7_ALL')
        with patch.object(scheduler.JobRegistry,'default_database',return_value=self.registry.database), \
             patch.object(scheduler.JobRegistry,'independent_campaign_context',return_value=('campaign',4)), \
             patch('independent_n_campaign.campaign_root',return_value=self.folder), \
             patch.object(scheduler,'verify_reconciliation_job',return_value={'frozen_inputs_verified':True}), \
             patch('vqe_adapter.collect_block',side_effect=collect):
            for _ in range(2):
                report=scheduler.reconcile_and_collect_block(self.f.directory,'campaign',self.block,'labA','batch',
                    {'M6_TREX_ZNE':'job-M6_TREX_ZNE'},service_factory=lambda _:service)
                self.assertEqual(sum(r['validated_pubs'] for r in report['jobs']),35)
                self.assertFalse(report['progression_ready'])
        self.assertEqual(len(calls),7)
        self.assertEqual(m7,next(r for r in self.registry.rows() if r['treatment_id']=='M7_ALL'))
        service.run.assert_not_called()

    def test_cli_job_set_mismatch_commits_nothing(self):
        jobs,service=self.cli_fixture();service.jobs.return_value=list(jobs.values())[:-1]
        before=self.registry.rows()
        with patch.object(scheduler.JobRegistry,'default_database',return_value=self.registry.database), \
             patch.object(scheduler.JobRegistry,'independent_campaign_context',return_value=('campaign',4)), \
             patch('independent_n_campaign.campaign_root',return_value=self.folder):
            with self.assertRaisesRegex(RuntimeError,'seven proposed'):
                scheduler.reconcile_and_collect_block(self.f.directory,'campaign',self.block,'labA','batch',
                    {'M6_TREX_ZNE':'job-M6_TREX_ZNE'},service_factory=lambda _:service)
        self.assertEqual(before,self.registry.rows())

    def test_cli_rejects_nonfinite_or_missing_uncertainties(self):
        import numpy as np
        jobs,service=self.cli_fixture()
        jobs['job-M0_Baseline'].result.return_value[0].data.evs[0]=np.nan
        jobs['job-M1_DD'].result.return_value[0].data.stds=None
        def collect(*args,**kwargs):
            from vqe_adapter import validate_runtime_result
            result=kwargs['service_override'].job('job-'+kwargs['treatments'][0]).result()
            captured=validate_runtime_result(result,5)
            if captured['uncertainty_status']!='PRESENT':raise ValueError('Missing uncertainties')
        with patch.object(scheduler.JobRegistry,'default_database',return_value=self.registry.database), \
             patch.object(scheduler.JobRegistry,'independent_campaign_context',return_value=('campaign',4)), \
             patch('independent_n_campaign.campaign_root',return_value=self.folder), \
             patch.object(scheduler,'verify_reconciliation_job',return_value={'frozen_inputs_verified':True}), \
             patch('vqe_adapter.collect_block',side_effect=collect):
            report=scheduler.reconcile_and_collect_block(self.f.directory,'campaign',self.block,'labA','batch',
                {'M6_TREX_ZNE':'job-M6_TREX_ZNE'},service_factory=lambda _:service)
        self.assertIn('ValueError',report['collection_failures']['M0_Baseline'])
        self.assertIn('ValueError',report['collection_failures']['M1_DD'])
        self.assertIn('non-finite',report['collection_failures']['M0_Baseline'])
        self.assertTrue(list(self.folder.glob('*/collection_failure_*.json')))
        self.assertEqual(sum(r['validated_pubs'] for r in report['jobs']),0)
        service.run.assert_not_called()

    def test_cli_dispatches_without_live_submission_authorization(self):
        import mxene
        with patch.object(scheduler,'reconcile_and_collect_block',return_value={'collection_failures':{},'submission_performed':False}) as run:
            code=mxene.main(['reconcile-collect','--campaign','campaign','--block','block',
                '--account','labK','--batch-id','batch','--m6-job-id','m6'])
        self.assertEqual(code,0)
        self.assertEqual(run.call_args.args[-1],{'M6_TREX_ZNE':'m6'})

    def test_atomic_reconciliation_record_never_overwrites(self):
        import json
        path=self.folder/'record.json'
        scheduler._write_reconciled_record(path,{'job_id':'first'})
        with self.assertRaises(FileExistsError):
            scheduler._write_reconciled_record(path,{'job_id':'second'})
        self.assertEqual(json.loads(path.read_text()),{'job_id':'first'})
        self.assertFalse(list(self.folder.glob('*.reconcile.tmp')))

    def test_instance_missing_empty_equivalent_and_different(self):
        service=Mock()
        service.instances.return_value=[{'name':'research','crn':'crn:verified'}]
        for missing in (None,''):
            self.assertFalse(scheduler._compare_reconciliation_instances(missing,'crn:verified',service,label='test'))
            with self.assertRaisesRegex(RuntimeError,'Active provider instance is missing'):
                scheduler._compare_reconciliation_instances('crn:verified',missing,service,label='test')
        self.assertTrue(scheduler._compare_reconciliation_instances('research','crn:verified',service,label='test'))
        self.assertTrue(scheduler._compare_reconciliation_instances('crn:verified','crn:verified',service,label='test'))
        with self.assertRaisesRegex(RuntimeError,'differs'):
            scheduler._compare_reconciliation_instances('crn:other','crn:verified',service,label='test')
        service.instances.return_value.append({'name':'research','crn':'crn:other'})
        with self.assertRaisesRegex(RuntimeError,'unique'):
            scheduler._compare_reconciliation_instances('research','crn:verified',service,label='test')

    def test_targeted_collection_preserves_all_other_rows(self):
        jobs,service=self.cli_fixture()
        before=[r for r in self.registry.rows() if r['treatment_id']!='M3_ZNE']
        calls=[]
        def collect(*args,**kwargs):calls.append(kwargs['treatments'])
        with patch.object(scheduler.JobRegistry,'default_database',return_value=self.registry.database), \
             patch.object(scheduler.JobRegistry,'independent_campaign_context',return_value=('campaign',4)), \
             patch('independent_n_campaign.campaign_root',return_value=self.folder), \
             patch.object(scheduler,'verify_reconciliation_job',return_value={'frozen_inputs_verified':True}), \
             patch('vqe_adapter.collect_block',side_effect=collect):
            scheduler.reconcile_and_collect_block(self.f.directory,'campaign',self.block,'labA','batch',
                {'M6_TREX_ZNE':'job-M6_TREX_ZNE'},service_factory=lambda _:service,treatments=('M3_ZNE',))
        self.assertEqual(calls,[('M3_ZNE',)])
        self.assertEqual(before,[r for r in self.registry.rows() if r['treatment_id']!='M3_ZNE'])
        self.assertEqual(service.job.call_count,7)
        service.run.assert_not_called()
