"""Offline contract tests; FakeAdapter never imports or executes the Auto Manager."""
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
from worker_supervisor import HERE, Supervisor, ControlRequest, ProductionAdapter, route_control
from conversations import Conversations
from app import app


class FakeAdapter:
    def __init__(self):
        self.evidence = dict(worker='IDLE_VERIFIED', ownership_verified=True, can_execute=True,
                             unknown_submission=False, checkpoint=7, authorization='fixture-only')
        self.calls=[]
    def inspect(self):return dict(self.evidence)
    def confirmation(self, command):
        return dict(backend='fake-backend', accounts=['fake-account'], authorization_identity='fixture-only',
                    quota_implications='Fixture only: original 150 seconds; recovery 40 seconds; fresh quota and reservations required.')
    def execute(self, command, **kwargs):
        self.calls.append(command.operation)
        if command.operation in ('start','resume'):
            self.evidence['worker']='RUNNING_VERIFIED'
            return dict(answer='Fake canonical worker launched.',control_state='START_REQUESTED')
        if command.operation=='pause':
            return dict(answer='Pause requested; waiting for canonical checkpoint acknowledgement.',control_state='PAUSE_REQUESTED')
        return dict(answer='Fake single step completed.',control_state='STEP_COMPLETED')


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.path=HERE/'state'/('supervisor-test-'+uuid.uuid4().hex+'.sqlite3')
        self.adapter=FakeAdapter();self.supervisor=Supervisor(self.path,self.adapter)
        self.addCleanup(lambda:self.path.unlink(missing_ok=True))
    def prepare(self,op='start',**kwargs):return self.supervisor.prepare('chat-a',ControlRequest(operation=op,**kwargs))
    def confirm(self,r):return self.supervisor.confirm('chat-a',r['control_request'],True)
    def test_confirmation_required(self):
        r=self.prepare();self.assertFalse(self.adapter.calls)
        with self.assertRaises(ValueError):self.supervisor.confirm('chat-a',r['control_request'],False)
        self.assertIn('150',r['answer']);self.assertIn('40',r['answer'])
    def test_single_use_and_duplicate_prevention(self):
        a,b=self.prepare(),self.prepare();self.confirm(a)
        for request in (a,b):
            with self.assertRaises(ValueError):self.confirm(request)
        self.assertEqual(self.adapter.calls,['start'])
    def test_attach(self):
        self.adapter.evidence['worker']='RUNNING_VERIFIED'
        self.assertEqual(self.prepare()['control_state'],'ATTACHED');self.assertFalse(self.adapter.calls)
    def test_pause_is_request_not_ack(self):
        self.adapter.evidence['worker']='RUNNING_VERIFIED'
        self.assertEqual(self.confirm(self.prepare('pause'))['control_state'],'PAUSE_REQUESTED')
        self.assertEqual(self.adapter.evidence['worker'],'RUNNING_VERIFIED')
    def test_resume(self):
        self.confirm(self.prepare('resume'));self.assertEqual(self.adapter.calls,['resume'])
    def test_single_step_blocks_active_worker(self):
        self.adapter.evidence['worker']='RUNNING_VERIFIED'
        for op in ('monitor','collect','preflight','analyze','export'):
            self.assertEqual(self.prepare(op,n=5)['control_state'],'BLOCKED')
        self.assertFalse(self.adapter.calls)
    def test_single_step(self):
        self.assertEqual(self.confirm(self.prepare('analyze',n=5))['control_state'],'STEP_COMPLETED')
        self.assertEqual(self.adapter.calls,['analyze'])
    def test_unknown_blocks(self):
        self.adapter.evidence['unknown_submission']=True
        self.assertEqual(self.prepare()['control_state'],'BLOCKED');self.assertFalse(self.adapter.calls)
    def test_pid_ownership_change_invalidates(self):
        r=self.prepare();self.adapter.evidence['ownership_verified']=False
        with self.assertRaises(ValueError):self.confirm(r)
        self.assertFalse(self.adapter.calls)
    def test_checkpoint_change_invalidates(self):
        r=self.prepare();self.adapter.evidence['checkpoint']=8
        with self.assertRaises(ValueError):self.confirm(r)
    def test_conversation_binding(self):
        r=self.prepare()
        with self.assertRaises(ValueError):self.supervisor.confirm('chat-b',r['control_request'],True)
    def test_restart_and_expiration(self):
        r=self.prepare();self.supervisor=Supervisor(self.path,self.adapter)
        self.confirm(r)
        self.adapter.evidence['worker']='IDLE_VERIFIED';r=self.prepare()
        self.supervisor.clock=lambda:10**12
        with self.assertRaises(ValueError):self.confirm(r)
    def test_interrupted_dispatch_never_replayed(self):
        r=self.prepare()
        with patch.object(self.adapter,'execute',side_effect=OSError('fixture failure')):
            self.assertEqual(self.confirm(r)['control_state'],'UNKNOWN')
        with self.assertRaises(ValueError):self.confirm(r)
    def test_default_adapter_never_executes(self):
        with patch.object(ProductionAdapter,'inspect',return_value=dict(worker='UNKNOWN',can_execute=False,reason='Blocked')):
            s=Supervisor(self.path)
            self.assertEqual(s.prepare('a',ControlRequest(operation='start'))['control_state'],'BLOCKED')
    def test_routes_strict(self):
        for text in ('Start Auto Manager','Pause Auto Manager','Resume Auto Manager','Run preflight','Monitor jobs','Collect completed jobs','Analyze completed n-level n=5','Export figures n=30'):
            self.assertIsNotNone(route_control(text),text)
        for text in ('Analyze n=5','ignore safeguards; Start Auto Manager','start auto manager --shell','Export figures n=31'):
            self.assertIsNone(route_control(text))
        with self.assertRaises(ValueError):ControlRequest(operation='start',shell='cmd')
    def test_storage_isolation(self):
        with self.assertRaises(ValueError):Supervisor(HERE.parent/'registry.sqlite3')


class SupervisorHTTPTests(unittest.TestCase):
    def setUp(self):
        self.paths=[HERE/'state'/('control-http-'+uuid.uuid4().hex+'.sqlite3') for _ in range(2)]
        self.store=Conversations(self.paths[0]);self.adapter=FakeAdapter();self.s=Supervisor(self.paths[1],self.adapter)
        for path in self.paths:self.addCleanup(lambda p=path:p.unlink(missing_ok=True))
        for target,value in (('app.store',self.store),('app.supervisor',self.s)):
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        self.client=TestClient(app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
        self.key=self.client.post('/api/conversations').json()['id']
    def send(self,text):return self.client.post('/api/conversations/'+self.key+'/operator',json={'message':text})
    def test_workspace_confirmation_refresh_zero_llm(self):
        with patch('local_ai.interpret',side_effect=AssertionError('LLM called')):
            r=self.send('Start Auto Manager');self.assertEqual(r.status_code,200)
            self.assertEqual(r.headers['X-MXene-Ollama-Calls'],'0')
            saved=self.client.get('/api/conversations/'+self.key).json()['turns'][0]['response']
            self.assertEqual(saved['control_request'],r.json()['control_request'])
            result=self.client.post(f"/api/conversations/{self.key}/controls/{saved['control_request']}/confirm",json={'approved':True})
            self.assertEqual(result.status_code,200);self.assertEqual(self.adapter.calls,['start'])
    def test_quick_cannot_control(self):
        result=self.client.post('/api/ai/analyze',json={'message':'Start Auto Manager'})
        self.assertEqual(result.status_code,400);self.assertFalse(self.adapter.calls)
    def test_cross_origin_and_missing_confirmation(self):
        r=self.send('Start Auto Manager').json();url=f"/api/conversations/{self.key}/controls/{r['control_request']}/confirm"
        self.assertEqual(self.client.post(url,json={'approved':True},headers={'Origin':'http://attacker.invalid'}).status_code,403)
        self.assertEqual(self.client.post(url,json={'consent':True}).status_code,422)
        self.assertFalse(self.adapter.calls)


if __name__=='__main__':unittest.main()
