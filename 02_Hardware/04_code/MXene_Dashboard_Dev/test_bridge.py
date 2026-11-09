"""Focused HTTP bridge tests: no IBM/Ollama connections and no production writes."""
import threading,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import app
import research

class Snapshot:
    lock=threading.Lock()
    def status(self):
        return dict(offline=False,valid=40,expected=840,captured_at='fixture',execution_jobs=[dict(target_n=5,status='QUEUED',ledger_states=['SUBMITTED'],job_id='internal-only')])

class Registry:
    def __init__(self,path,readonly=False):
        if not readonly:raise AssertionError('Writable manager registry forbidden')
    def independent_campaign_status(self,campaign,event_limit):
        assert event_limit==5
        return {'state':'PAUSED','exists':True}

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app,base_url='http://127.0.0.1')
        _,self.quota,self.config=research._manager_modules()
        for name,value in [('app.reader',Snapshot()),('research._workflow_skill',lambda:{'name':'workflow-manager'}),('research._manager_modules',lambda:(Registry,self.quota,self.config))]:
            p=patch(name,value);p.start();self.addCleanup(p.stop)
        p=patch('research.Path.cwd',return_value=research.HERE.parents[2]);p.start();self.addCleanup(p.stop)
        p=patch('research.save_evidence');self.saved=p.start();self.addCleanup(p.stop)
        p=patch('local_ai.call',side_effect=AssertionError('Unnecessary Ollama request'));self.ai=p.start();self.addCleanup(p.stop)
    def ask(self,text):
        return self.client.post('/api/ai/analyze',headers={'X-MXene-AI':'local-only'},json={'message':text})
    def test_campaign_http_to_manager(self):
        r=self.ask('Show campaign progress.');self.assertEqual(r.status_code,200);self.assertIn('40 of 840',r.json()['answer']);self.ai.assert_not_called();self.saved.assert_called_once()
    def test_missing_model_never_blocks_tools(self):
        r=self.client.post('/api/ai/analyze',headers={'X-MXene-AI':'local-only'},json={'message':'Show campaign progress.','model':'not-installed','consent':False})
        self.assertEqual(r.status_code,200);self.assertEqual(r.headers['X-MXene-Ollama-Calls'],'0');self.assertEqual(r.headers['X-MXene-Chat-Protocol'],'2');self.ai.assert_not_called()
    def test_combined_analyze_compare_routes_directly(self):
        with patch('research.execute',return_value={'rows':[],'sentences':['verified'],'warning':'Fixture','chart':None}) as calculate:
            self.assertEqual(self.ask('Analyze n=5 and compare DD with TREX.').status_code,200)
            self.assertEqual(calculate.call_args.args[1].levels,[5]);self.assertEqual(set(calculate.call_args.args[1].treatments),{'M1_DD','M2_TREX'});self.ai.assert_not_called()
    def test_model_exact_match_no_random_fallback(self):
        from local_ai import resolve_model
        with patch('local_ai.models',return_value=['unrelated-model']):
            with self.assertRaises(ValueError):resolve_model('qwen3:1.7b')
        with patch('local_ai.models',return_value=['unrelated-model','qwen3:1.7b']):self.assertEqual(resolve_model('qwen3:1.7b'),'qwen3:1.7b')
    def test_pending_jobs_internal_identity(self):
        r=self.ask('Show pending jobs.');self.assertEqual(r.status_code,200);self.assertIn('QUEUED: 1',r.json()['answer']);self.assertNotIn('internal-only',r.text)
    def test_worker_unknown_not_claimed_idle(self):
        with patch('worker_supervisor.ProductionAdapter.inspect',return_value={'worker':'UNKNOWN','ownership_verified':False,'can_execute':False}):
            r=self.ask('Show worker status.');self.assertEqual(r.status_code,200);self.assertIn('UNKNOWN',r.json()['answer'])
    def test_quota_reuses_provider_validation(self):
        with patch.object(self.quota,'provider_reservations',return_value={'labA':{'reserved_seconds':0,'ambiguous':False}}),patch('research.Path.glob',return_value=iter([])):
            r=self.ask('Check IBM quota.');self.assertEqual(r.status_code,200);self.assertIn('Unavailable',r.json()['answer']);self.assertIn('150',r.json()['answer']);self.ai.assert_not_called()
    def test_analysis_direct_no_llm(self):
        with patch('research.execute',return_value={'rows':[],'sentences':['Verified fixture metric: 2.'],'warning':'Fixture only','chart':None}) as calculate:
            r=self.ask('Analyze n=5.');self.assertEqual(r.status_code,200);calculate.assert_called_once();self.ai.assert_not_called()
    def test_compare_selects_two_treatments(self):
        with patch('research.execute',return_value={'rows':[],'sentences':[],'warning':'Fixture','chart':None}) as calculate:
            self.ask('Compare DD and TREX.');self.assertEqual(set(calculate.call_args.args[1].treatments),{'M1_DD','M2_TREX'})
    def test_control_denied(self):
        for text in ('Resume Auto Manager','Submit jobs','Cancel job','Start worker'):
            self.assertEqual(self.ask(text).status_code,400)
    def test_typed_api_refuses_extra_or_action(self):
        for payload in ({'operation':'submit'},{'operation':'get_campaign_status','shell':'bad'}):
            self.assertEqual(self.client.post('/api/research/tool',headers={'X-MXene-AI':'local-only'},json=payload).status_code,422)
    def test_origin_denied(self):
        r=self.client.post('/api/ai/analyze',headers={'X-MXene-AI':'local-only','Origin':'https://external.invalid'},json={'message':'Show campaign progress'})
        self.assertEqual(r.status_code,403)
    def test_ambiguous_reasoning_requires_consent(self):
        self.assertEqual(self.ask('Why does this happen?').status_code,403);self.ai.assert_not_called()
    def test_wrong_working_directory_fail_closed(self):
        with patch('research.Path.cwd',return_value=research.HERE):
            r=self.ask('Show campaign progress');self.assertEqual(r.status_code,400);self.assertIn('working directory',r.json()['detail'])
    def test_unknown_snapshot_refused(self):
        with patch.object(Snapshot,'status',return_value={'offline':True}):self.assertEqual(self.ask('Show campaign progress').status_code,400)

if __name__=='__main__':unittest.main()
