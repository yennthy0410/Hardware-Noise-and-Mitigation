"""Offline collection: archive first, validate exact slots, never submit."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import test_batch_progression as fixtures
import vqe_adapter as adapter

class CollectionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BatchProgressionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.registry=self.f.registry;self.target=self.f.target()
        self.ids=self.f.reserve(self.target,1);self.block='n10-block-001'
        self.rowid=self.ids['M3_ZNE']
        self.registry.transition(self.rowid,'SUBMITTING')
        self.registry.transition(self.rowid,'SUBMITTED',job_id='m3',batch_id='batch')
        self.raw=self.f.directory/'raw.json';self.raw.write_text('{"untouched": true}')
        self.digest=hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.others=[r for r in self.registry.rows() if r['id']!=self.rowid]

    def collect(self,pubs):
        pilot=Mock()
        def run(args,**kwargs):
            kwargs['pub_observer']('M3_ZNE','m3',pubs,str(self.raw),self.digest,
                SimpleNamespace(metrics=lambda:{}))
            return {'ok':True}
        pilot.collect.side_effect=run
        with patch.object(adapter,'load_pilot',return_value=pilot),patch.object(adapter,'_args',return_value=SimpleNamespace()), \
             patch.object(adapter,'record_directory',return_value=self.f.directory):
            return adapter.collect_block(self.f.directory,self.block,shots=4096,n=5,account_label='labA',
                ibm_account='labA',registry=self.registry,treatments=('M3_ZNE',))

    def pubs(self):
        return [SimpleNamespace(data=SimpleNamespace(evs=[.2]*7,stds=[.01]*7),metadata={}) for _ in range(5)]

    def test_valid_collection_idempotent_and_other_treatments_unchanged(self):
        for _ in range(2):self.collect(self.pubs())
        records=self.registry.pub_validation_records(self.target.target_id)
        self.assertEqual(len(records),5)
        self.assertEqual({r['pub_index'] for r in records},{1,2,3,4,5})
        self.assertEqual(next(r for r in self.registry.rows() if r['id']==self.rowid)['validated_pub_count'],5)
        self.assertEqual(self.others,[r for r in self.registry.rows() if r['id']!=self.rowid])

    def check_invalid(self,pubs,index):
        for _ in range(2):
            with self.assertRaisesRegex(ValueError,'invalid PUB indices'):self.collect(pubs)
        self.assertEqual(hashlib.sha256(self.raw.read_bytes()).hexdigest(),self.digest)
        self.assertTrue(self.raw.with_name('raw.structured.json').is_file())
        records=self.registry.pub_validation_records(self.target.target_id)
        invalid=[r['pub_index'] for r in records if not r['expectation_valid'] or r['uncertainty_valid']!=1]
        self.assertEqual(invalid,[index])
        self.assertEqual(next(r for r in self.registry.rows() if r['id']==self.rowid)['validated_pub_count'],4)
        self.assertEqual(self.others,[r for r in self.registry.rows() if r['id']!=self.rowid])

    def test_nan_preserved_and_only_affected_pub_marked(self):
        pubs=self.pubs();pubs[2].data.evs[0]=float('nan');self.check_invalid(pubs,3)
        self.assertEqual(json.loads(self.raw.with_name('raw.structured.json').read_text())['records'][2]['expectation_values'][0],'nan')

    def test_infinite_uncertainty(self):
        pubs=self.pubs();pubs[1].data.stds[4]=float('inf');self.check_invalid(pubs,2)

    def test_missing_uncertainty(self):
        pubs=self.pubs();pubs[3].data.stds=None;self.check_invalid(pubs,4)

    def test_missing_pub(self):
        self.check_invalid(self.pubs()[:4],5)

    def test_zne_failure_preserves_other_four(self):
        pubs=self.pubs();pubs[2].metadata={'resilience':{'zne_status':'FAILED'}}
        self.check_invalid(pubs,3)

    def test_infinite_expectation_preserves_other_four(self):
        pubs=self.pubs();pubs[2].data.evs[0]=float('inf');self.check_invalid(pubs,3)

    def test_terminal_retrieval_evidence_and_planner_missing_slots(self):
        from worker_runtime import terminal_result,TerminalResultUnavailable
        from targeted_recovery import plan_invalid_pub_slots
        self.registry.transition(self.rowid,'FAILED')
        job=Mock();job.status.return_value='FAILED';job.result.side_effect=RuntimeError('Job failed: no result')
        with self.assertRaises(TerminalResultUnavailable) as caught:terminal_result(job,sleeper=Mock())
        self.assertEqual(job.result.call_count,3)
        failure=caught.exception
        kwargs=dict(registry=self.registry,shots=4096,n=5)
        adapter._archive_terminal_missing(self.f.directory,self.block,'M3_ZNE',failure,kwargs)
        first=self.registry.rows(self.block)
        adapter._archive_terminal_missing(self.f.directory,self.block,'M3_ZNE',failure,kwargs)
        self.assertEqual(first,self.registry.rows(self.block))
        self.target.shots_per_pub=4096;self.target.physical_qubits=list(range(140,147))
        with patch.object(adapter,'verify_frozen_inputs',return_value=(SimpleNamespace(parameters=[]),[1]*7,[0]*21,list(range(140,147)),None,None)), \
             patch.object(adapter,'treatment_options',return_value='canonical'), \
             patch('targeted_recovery._verify_archived_treatment_options'):
            report=plan_invalid_pub_slots(self.f.directory,self.target,self.registry,final_config={'auto_recovery_enabled':True},explicit_retry_authorization=True)
        self.assertEqual(len(report['plans']),5)
        self.assertEqual({r['source_pub_index'] for r in report['plans']},set(range(1,6)))
        self.assertEqual(self.others,[r for r in self.registry.rows() if r['id']!=self.rowid])
