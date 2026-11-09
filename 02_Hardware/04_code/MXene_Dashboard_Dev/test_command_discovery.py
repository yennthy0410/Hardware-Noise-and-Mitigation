"""Offline capability discovery and actual ASGI request routing; no dispatch."""
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import app
from worker_supervisor import Supervisor,ProductionAdapter,command_capabilities,route_control
import test_auto_broker as fixtures


class DiscoveryTests(unittest.TestCase):
    setUp=fixtures.BrokerTests.setUp

    def snapshot(self):
        return {'captured_at':datetime.now(timezone.utc).isoformat(),'offline':False,'valid':760,'expected':840}

    def test_all_requested_routes(self):
        for text in ('Show supported commands','Show supported STEP commands','Show Auto Manager status',
                     'Run Auto Manager','Resume Auto Manager','Pause Auto Manager','Prepare Campaign',
                     'Run Preflight','Run Batch n=5','Monitor Jobs','Collect Results','Review Targeted Recovery',
                     'Analyze Results','Export Figures','Review Recovery','Campaign Status','Worker Status',
                     'Batch Status','IBM Job Status','IBM Quota','Recovery Status','Prepare','Preflight',
                     'Monitor / Collect','Analyze','Export n=30 figures','Review recovery for n=30'):
            self.assertIsNotNone(route_control(text),text)

    def test_capabilities_follow_canonical_blockers(self):
        result=command_capabilities(self.s,self.snapshot())
        rows={r['command']:r for r in result['rows']}
        self.assertEqual(len(rows),18)
        self.assertEqual(rows['Collect completed jobs for n=30 Batch 006']['status'],'REQUIRES_AUTHORIZATION')
        for command in ('Run Auto Manager','Resume Auto Manager','Preflight','Prepare'):
            self.assertEqual(rows[command]['status'],'REQUIRES_AUTHORIZATION')
        for command in ('Monitor / Collect','Analyze','Export Figures'):
            self.assertEqual(rows[command]['status'],'BLOCKED')
            self.assertTrue(rows[command]['reason'])
        self.assertEqual(rows['Show supported commands']['status'],'READ_ONLY')
        self.assertEqual({r['group'] for r in result['commands']},{'Status & Monitoring','AUTO Control','STEP-by-STEP'})
        self.assertNotIn('Run Batch n=5',[r['command'] for r in result['commands']])

    def test_status_routes_never_use_science_or_llm(self):
        from conversations import Conversations
        snapshot={**self.snapshot(),'campaign':[{'state':'PAUSED'}],
                  'execution_jobs':[{'target_n':30,'status':'QUEUED','observed_at':'2020-01-01T00:00:00+00:00'}],
                  'recovery_summary':[{'n':5,'pending':0},{'n':30,'pending':8}]}
        store=Conversations(self.root/'status-conversation.sqlite3')
        def bridge(reader,command,**kwargs):
            self.assertIn(command.operation,('get_campaign_status','get_ibm_job_status','check_ibm_quota'))
            return {'answer':'Verified fixture operational evidence','rows':[]}
        with patch('app.supervisor',self.s),patch('app.store',store),patch('app.reader.status',return_value=snapshot),patch('research.bridge',side_effect=bridge),patch('local_ai.interpret',side_effect=AssertionError('No LLM')):
            client=TestClient(app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            key=client.post('/api/conversations').json()['id']
            for message in ('Campaign Status','Worker Status','Batch Status','IBM Job Status','IBM Quota','Recovery Status','Review recovery for n=30'):
                response=client.post(f'/api/conversations/{key}/messages',json={'message':message})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')
                self.assertIn('PAUSED',response.json()['answer'])
                self.assertIn('Local snapshot checked:',response.json()['answer'])
                if message=='Batch Status':
                    self.assertEqual(response.json()['rows'][0]['freshness'],'STALE')
                    self.assertEqual(response.json()['rows'][0]['provider_batch_state'],'UNAVAILABLE')
                if message=='Review recovery for n=30':
                    self.assertEqual([r['n'] for r in response.json()['rows']],[30])

    def test_unknown_stale_complete_and_active_guards(self):
        self.evidence.update(worker='UNKNOWN',ownership_verified=False)
        rows=command_capabilities(self.s,self.snapshot())['rows']
        self.assertEqual(next(r for r in rows if r['command']=='Run Auto Manager')['status'],'BLOCKED')
        self.evidence.update(worker='STOPPED_VERIFIED',ownership_verified=True)
        for snapshot in ({'offline':True},{**self.snapshot(),'valid':840}):
            rows=command_capabilities(self.s,snapshot)['rows']
            self.assertEqual(next(r for r in rows if r['command']=='Run Auto Manager')['status'],'BLOCKED')
        self.evidence['worker']='RUNNING_VERIFIED'
        rows=command_capabilities(self.s,self.snapshot())['rows']
        self.assertEqual(next(r for r in rows if r['command']=='Run Auto Manager')['status'],'AVAILABLE')
        self.assertEqual(next(r for r in rows if r['command']=='Preflight')['status'],'BLOCKED')

    def test_old_broker_does_not_advertise_new_steps(self):
        self.evidence.pop('supported_operations')
        rows=command_capabilities(self.s,self.snapshot())['rows']
        for name in ('Preflight','Monitor Batch 005'):
            row=next(r for r in rows if r['command']==name)
            self.assertEqual(row['status'],'BLOCKED');self.assertIn('running broker',row['reason'])

    def test_http_help_precedes_science_with_existing_history(self):
        from conversations import Conversations
        store=Conversations(self.root/'conversation.sqlite3')
        with patch('app.supervisor',self.s),patch('app.store',store),patch('app.reader.status',return_value=self.snapshot()),patch('research.bridge',side_effect=AssertionError('Must not call science')),patch('local_ai.interpret',side_effect=AssertionError('Must not call LLM')),patch.object(self.adapter,'execute',side_effect=AssertionError('No execution')):
            client=TestClient(app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            key=client.post('/api/conversations').json()['id']
            store.append(key,'Analyze n=5',{'answer':'Earlier scientific question'},{'n':5})
            for message in ('Show supported STEP commands','Show supported commands','Show Auto Manager status'):
                response=client.post(f'/api/conversations/{key}/messages',json={'message':message})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')
                if 'supported' in message:self.assertIn('Preflight',str(response.json()['rows']))
            self.assertEqual(len(client.get('/api/workspace/suggestions').json()['commands']),16)
            self.assertEqual(len(client.get('/api/conversations/'+key).json()['turns']),4)
            response=client.post('/api/ai/analyze',json={'message':'Show supported STEP commands'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')


if __name__=='__main__':unittest.main()
