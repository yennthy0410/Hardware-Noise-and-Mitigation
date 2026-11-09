import unittest,uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import app
from conversations import Conversations
from reader import HERE
from research import select_question
from interpretations import concise_summary

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.path=HERE/'state'/('conversation-fixture-'+uuid.uuid4().hex+'.sqlite3')
        self.store=Conversations(self.path)
        self.client=TestClient(app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
        p=patch('app.store',self.store);p.start();self.addCleanup(p.stop)
        self.addCleanup(lambda:self.path.unlink(missing_ok=True))
        def tool(reader,command,history=()):
            scope=select_question(command.question,history,command.n)
            return dict(answer=f'Calculated {scope.metric} for n={scope.levels}',rows=[],chart=None,research_scope=scope.model_dump(),audit_ref='internal-audit')
        p=patch('research.bridge',side_effect=tool);self.bridge=p.start();self.addCleanup(p.stop)
    def create(self):return self.client.post('/api/conversations').json()['id']
    def send(self,key,text):return self.client.post(f'/api/conversations/{key}/messages',json={'message':text})
    def test_create_persist_rename_delete(self):
        key=self.create();self.assertEqual(self.send(key,'Analyze n=5.').status_code,200)
        self.assertEqual(len(Conversations(self.path).get(key)['turns']),1)
        self.assertEqual(self.client.patch('/api/conversations/'+key,json={'title':'DD research'}).json()['title'],'DD research')
        self.assertTrue(self.client.delete('/api/conversations/'+key).json()['deleted'])
        self.assertEqual(self.client.get('/api/conversations/'+key).status_code,400)
    def test_context_and_conversation_isolation(self):
        a,b=self.create(),self.create()
        self.send(a,'Analyze n=5 and compare DD with TREX.')
        self.send(b,'Analyze Rct at n=10.')
        for message in ('What about RMSE?','Compare treatments.','What about SSE?','What about RMSE?','What about Rct?'):
            r=self.send(a,message);self.assertEqual(r.status_code,200);self.assertEqual(r.json()['research_scope']['levels'],[5])
        self.assertEqual(self.send(b,'What about RMSE?').json()['research_scope']['levels'],[10])
        self.assertEqual(len(self.store.get(b)['turns']),2)
    def test_quick_does_not_persist_or_use_history(self):
        key=self.create()
        r=self.client.post('/api/ai/analyze',json={'message':'Analyze n=5.','history':[{'role':'user','content':'Analyze n=30'}]})
        self.assertEqual(r.status_code,200);self.assertEqual(self.bridge.call_args.kwargs['history'],[])
        self.assertEqual(self.store.get(key)['turns'],[])
    def test_client_cannot_inject_workspace_history(self):
        key=self.create();self.send(key,'Analyze n=5.')
        r=self.client.post(f'/api/conversations/{key}/messages',json={'message':'What about RMSE?','history':[{'role':'user','content':'n=30'}]})
        self.assertEqual(r.json()['research_scope']['levels'],[5])
    def test_secrets_not_saved(self):
        key=self.create();self.assertEqual(self.send(key,'api_key=fixture-secret-value').status_code,400)
        self.assertEqual(self.store.get(key)['turns'],[]);self.bridge.assert_not_called()
    def test_failure_does_not_save_partial_turn(self):
        key=self.create()
        with patch('research.bridge',side_effect=ValueError('Unavailable')):self.assertEqual(self.send(key,'Analyze n=5').status_code,400)
        self.assertEqual(self.store.get(key)['turns'],[])
    def test_mutation_origin_refused(self):
        self.assertEqual(self.client.post('/api/conversations',headers={'Origin':'https://other.invalid'}).status_code,403)
    def test_storage_isolation(self):
        with self.assertRaises(ValueError):Conversations(HERE.parent/'production.sqlite3')
    def test_duplicate_busy_turn_refused(self):
        key=self.create()
        with self.store.turn(key):self.assertEqual(self.send(key,'Analyze n=5').status_code,400)
    def test_evidence_preserved_without_ui_metadata(self):
        key=self.create();self.send(key,'Analyze n=5')
        turn=self.store.get(key)['turns'][0];self.assertEqual(turn['response']['audit_ref'],'internal-audit')
        self.assertNotIn('audit',turn['response']['answer'])
    def test_rms_summary_exact_and_distinct(self):
        rows=[dict(treatment='M0_Baseline',rms=10,unit='ohm',points=132),dict(treatment='M1_DD',rms=4,unit='ohm',points=132)]
        summary=' '.join(concise_summary(rows,'nyquist',5));self.assertIn('60%',summary);self.assertIn('6 ohm',summary);self.assertIn('not canonical per-PUB RMSE',summary);self.assertIn('DD',summary)
    def test_zero_baseline_no_percentage_invention(self):
        self.assertIn('undefined',' '.join(concise_summary([dict(treatment='M0_Baseline',rms=0,unit='ohm',points=132)],'residual_real',5)))
    def test_sse_is_canonical_median_and_preliminary(self):
        rows=[dict(treatment='None',median=100,unit='ohm²',count=5),dict(treatment='DD',median=50,unit='ohm²',count=4)]
        text=' '.join(concise_summary(rows,'distribution_pilot',5));self.assertIn('median canonical SSE',text);self.assertIn('50%',text);self.assertIn('preliminary',text)

if __name__=='__main__':unittest.main()
