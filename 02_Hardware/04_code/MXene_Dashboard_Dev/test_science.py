import csv,json,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import app,AnalysisRequest
from reader import HERE
from science_ui import chart,circuit,classify_slot,design
class FixtureReader:
    def __init__(self,path):self.path=path;self.a={'id':'curve','name':'nyquist_curves.csv','level':5,'version':'v1','sha256':'fixture'}
    def status(self):return {'artifacts':[self.a],'levels':[],'execution_jobs':[],'captured_at':'fixture timestamp'}
    def artifact(self,key):return self.path
class ScientificUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=HERE/'state'/('chart-fixture-'+uuid.uuid4().hex+'.csv')
        cls.path.write_text('Experimental_Z_real,Experimental_minus_Z_imag\n1,-2\n3,4\n')
    def test_exact_chart_values_no_sign_recalculation(self):
        d=chart(FixtureReader(self.path),'curve','nyquist');self.assertEqual(d['series'][0]['x'],[1.,3.]);self.assertEqual(d['series'][0]['y'],[-2.,4.]);self.assertEqual(d['x_scale'],'linear')
    def test_no_cross_scope_or_invented_ci(self):
        reader=FixtureReader(self.path);reader.a['level']='statistics'
        with self.assertRaises(ValueError):chart(reader,'curve','nyquist')
        with self.assertRaises(ValueError):chart(FixtureReader(self.path),'curve','median_CI')
    def test_nonfinite_source_refused(self):
        p=HERE/'state'/('bad-curve-'+uuid.uuid4().hex+'.csv');p.write_text('Experimental_Z_real,Experimental_minus_Z_imag\n1,nan\n')
        with self.assertRaises(ValueError):chart(FixtureReader(p),'curve','nyquist')
    def test_real_frozen_provenance(self):
        c=circuit();self.assertTrue(c['available']);self.assertEqual(len(c['theta']),21);self.assertEqual(c['hashes']['circuit.qpy'],'2bd4899c52f87441e60416cb6e0aeb3341648ff09cd4c89fad17986128b6381c');self.assertEqual(c['two_qubit_gates'],6);self.assertEqual(c['depth'],20);self.assertEqual(c['measurements'],0)
    def test_six_actual_manifests(self):self.assertEqual([d['target_n'] for d in design()],[5,10,15,20,25,30])
    def test_pending_original_not_missing(self):
        j={'status':'QUEUED','ledger_states':['SUBMITTED'],'job_id':'known'}
        self.assertEqual(classify_slot(None,[j],[],True),'Awaiting original job')
    def test_pending_replacement_not_accepted(self):
        j={'status':'RUNNING','ledger_states':['SUBMITTED'],'job_id':'replacement'}
        self.assertTrue(classify_slot(None,[],[j],True).startswith('Replacement submitted'))
    def test_unknown_never_replaced(self):
        j={'status':None,'ledger_states':['UNKNOWN'],'job_id':None}
        self.assertIn('reconciliation',classify_slot(None,[j],[],True))
    def test_accepted_wins_over_failed_attempt(self):
        j={'status':'FAILED','ledger_states':['FAILED'],'job_id':'old'}
        self.assertEqual(classify_slot({'source':'REPLACEMENT'},[j],[],True),'Recovered')
    def test_done_awaits_science(self):
        j={'status':'DONE','ledger_states':['COMPLETED'],'job_id':'known'}
        self.assertIn('retrieval / validation',classify_slot(None,[j],[]))
    def test_ai_no_consent_no_backend(self):
        client=TestClient(app,base_url='http://127.0.0.1')
        with patch('local_ai.models') as backend:
            self.assertEqual(client.post('/api/ai/connect',json={'consent':False}).status_code,403);backend.assert_not_called()
    def test_ai_foreign_origin_denied(self):
        client=TestClient(app,base_url='http://127.0.0.1')
        self.assertEqual(client.post('/api/ai/connect',json={'consent':True},headers={'X-MXene-AI':'local-only','Origin':'https://other.example'}).status_code,403)
    def test_ibm_live_action_routes_absent(self):
        client=TestClient(app,base_url='http://127.0.0.1')
        for route in ('/api/ibm/submit','/api/ibm/cancel','/api/ibm/retrieve','/api/ibm/recover'):
            self.assertEqual(client.post(route,json={}).status_code,404)
    def test_ai_honest_failure(self):
        client=TestClient(app,base_url='http://127.0.0.1')
        with patch('local_ai.models',side_effect=ConnectionError):
            r=client.post('/api/ai/connect',json={'consent':True},headers={'X-MXene-AI':'local-only'});self.assertEqual(r.status_code,503);self.assertIn('no fallback',r.json()['detail'])
    def test_ai_endpoints_fixed(self):
        from local_ai import call
        with self.assertRaises(ValueError):call('https://external.example')
    def test_false_count_withheld(self):
        from local_ai import verify_count_claim
        with self.assertRaises(ValueError):verify_count_claim('The accepted valid PUB count for n=5 is 5.',{'levels':[{'valid':40}]})
        verify_count_claim('The accepted valid PUB count for n=5 is 40.',{'levels':[{'valid':40}]})
    def test_context_scope_and_no_tools(self):
        from local_ai import interpret
        body=AnalysisRequest(consent=True,model='local-test',message='Explain',n=5)
        with patch('local_ai.models',return_value=['local-test']),patch('local_ai.call',return_value={'message':{'content':'Fixture-only response'}}) as backend:
            r=interpret(FixtureReader(self.path),body);payload=backend.call_args.args[1];self.assertNotIn('tools',payload);self.assertFalse(payload['stream']);self.assertEqual(r['context']['n'],5)
    def test_gallery_layout_and_chat_contract(self):
        js=(HERE/'static/gallery.js').read_text();chat=(HERE/'static/chat.js').read_text();html=(HERE/'static/index.html').read_text()
        for name in ('nyquist.png','bode_magnitude.png','residual_real_normalized.png','05_mitigation_median_CI_convergence.png'):self.assertIn(name,js)
        for term in ('chat-launch','chat-full','chat-min','chat-new','chat-consent','Shift+Enter','No cloud','no fallback'):self.assertIn(term,chat)
        self.assertIn('/static/chat.js',html);self.assertNotIn('https://',chat)
if __name__=='__main__':unittest.main(verbosity=2)
