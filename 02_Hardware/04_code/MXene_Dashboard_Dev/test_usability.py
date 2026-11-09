"""Offline launcher/state/documentation contracts; no IBM or service processes."""
import json,os,unittest,uuid,shutil
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace as NS
import service_launcher as launch
from worker_supervisor import automation_view

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.root=launch.HERE/'state'/('launcher-test-'+uuid.uuid4().hex);self.root.mkdir();(self.root/'state').mkdir()
        self.addCleanup(shutil.rmtree,self.root)
        p=patch.object(launch,'HERE',self.root);p.start();self.addCleanup(p.stop)
        self.expected=['-B',str(self.root/'serve.py')]

    def test_reuse_prevents_duplicate(self):
        with patch.object(launch,'inventory',return_value=([Mock()],self.expected)),patch.object(launch,'owns_listener',return_value=True),patch.object(launch,'healthy',return_value=True),patch.object(launch.subprocess,'Popen') as popen:
            self.assertIn('reused',launch.ensure(launch.SERVICES[1]));popen.assert_not_called()

    def test_unhealthy_or_foreign_listener_never_duplicates(self):
        with patch.object(launch,'inventory',return_value=([Mock()],self.expected)),patch.object(launch,'owns_listener',return_value=False),patch.object(launch.subprocess,'Popen') as popen:
            with self.assertRaises(RuntimeError):launch.ensure(launch.SERVICES[1])
            popen.assert_not_called()
        with patch.object(launch,'inventory',return_value=([],self.expected)),patch.object(launch,'port_free',return_value=False),patch.object(launch.subprocess,'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError,'occupied'):launch.ensure(launch.SERVICES[1])
            popen.assert_not_called()

    def test_start_inherits_ollama_no_automation_command(self):
        with patch.object(launch,'inventory',return_value=([],self.expected)),patch.object(launch,'port_free',return_value=True),patch.object(launch,'healthy',return_value=True),patch.dict(os.environ,{'MXENE_OLLAMA_MODEL':'fixture-local','OLLAMA_CONTEXT_LENGTH':'8192'}),patch.object(launch.subprocess,'Popen') as popen:
            popen.return_value.poll.return_value=None
            self.assertIn('started',launch.ensure(launch.SERVICES[1]))
            args,kw=popen.call_args;self.assertEqual(args[0][1:],self.expected)
            self.assertEqual(kw['env']['MXENE_OLLAMA_MODEL'],'fixture-local');self.assertEqual(kw['env']['OLLAMA_CONTEXT_LENGTH'],'8192')
            self.assertFalse(kw['shell']);self.assertNotIn('--worker',args[0]);self.assertNotIn('--live',args[0])

    def test_restart_only_verified_service(self):
        service=Mock(pid=42);service.ppid.return_value=1;service.cmdline.return_value=['python',*self.expected]
        class Gone(Exception):pass
        with patch.object(launch,'inventory',return_value=([service],self.expected)),patch.object(launch,'process_api',return_value=NS(NoSuchProcess=Gone)),patch.object(launch,'port_free',return_value=True),patch.object(launch,'healthy',return_value=True),patch.object(launch.subprocess,'Popen') as popen:
            popen.return_value.poll.return_value=None;launch.ensure(launch.SERVICES[1],True)
            service.terminate.assert_called_once();service.wait.assert_called_once_with(10);popen.assert_called_once()
            service.cmdline.return_value=['python','unrelated.py']
            with self.assertRaisesRegex(RuntimeError,'identity changed'):launch.ensure(launch.SERVICES[1],True)

    def test_launcher_lock_excludes_concurrent_starts(self):
        with launch.launch_lock():
            with self.assertRaises(RuntimeError):
                with launch.launch_lock():pass

    def test_ambiguous_processes_refused(self):
        class Gone(Exception):pass
        class Denied(Exception):pass
        processes=[]
        for pid in (1,2):
            p=Mock(pid=pid);p.ppid.return_value=0;p.name.return_value='python.exe';p.cmdline.return_value=['python',*self.expected];processes.append(p)
        with patch.object(launch,'process_api',return_value=NS(process_iter=lambda:processes,NoSuchProcess=Gone,AccessDenied=Denied)):
            with self.assertRaisesRegex(RuntimeError,'Multiple'):launch.inventory('serve.py',[])

class AutomationTests(unittest.TestCase):
    def evidence(self,state,worker='STOPPED_VERIFIED'):
        return {'worker':worker,'ownership_verified':True,'can_execute':True,'unknown_submission':False,'operation':'start','policy':{'campaign_state':state,'ready':True,'complete':False}}
    def test_stopped_start_paused_resume_running_pause(self):
        for campaign,worker,state,enabled in [('STOPPED','STOPPED_VERIFIED','STOPPED','start'),('PAUSED','STOPPED_VERIFIED','PAUSED','resume'),('RUNNING','RUNNING_VERIFIED','RUNNING','pause')]:
            view=automation_view(self.evidence(campaign,worker));self.assertEqual(view['state'],state)
            self.assertEqual([k for k,v in view['actions'].items() if v['enabled']],[enabled]);self.assertEqual(view['authorization_status'],'POLICY_CONFIGURED')
    def test_unverified_error_and_pending_pause(self):
        evidence=self.evidence('PAUSED','RUNNING_VERIFIED');view=automation_view(evidence)
        self.assertEqual(view['state'],'RUNNING');self.assertFalse(any(x['enabled'] for x in view['actions'].values()))
        evidence['ownership_verified']=False;view=automation_view(evidence);self.assertEqual(view['state'],'ERROR')
        self.assertFalse(any(x['enabled'] for x in view['actions'].values()))
    def test_authorization_and_unknown_are_separate(self):
        evidence=self.evidence('STOPPED');evidence['policy']['ready']=False
        self.assertEqual(automation_view(evidence)['state'],'STOPPED');self.assertFalse(automation_view(evidence)['actions']['start']['enabled'])
        evidence['policy']['ready']=True;evidence['unknown_submission']=True
        self.assertIn('UNKNOWN',automation_view(evidence)['actions']['start']['reason'])
    def test_manual_worker_is_not_automation(self):
        evidence=self.evidence('STOPPED','RUNNING_VERIFIED');evidence['operation']='collect'
        view=automation_view(evidence);self.assertEqual(view['state'],'STOPPED');self.assertFalse(any(x['enabled'] for x in view['actions'].values()))
    def test_server_gate_rechecks_operational_state(self):
        from worker_supervisor import Supervisor,ControlRequest
        adapter=Mock();adapter.operation_blocker.return_value=None;controller=NS(adapter=adapter)
        for state,wrong,right in [('PAUSED','start','resume'),('STOPPED','resume','start')]:
            evidence=self.evidence(state)
            self.assertIn('requires',Supervisor.gate(controller,ControlRequest(operation=wrong),evidence))
            self.assertIsNone(Supervisor.gate(controller,ControlRequest(operation=right),evidence))

class DocumentationTests(unittest.TestCase):
    def test_viewer_available_while_paused_and_broker_offline(self):
        from fastapi.testclient import TestClient
        import app
        with patch.object(app.reader,'bundle',return_value={'campaign':[{'state':'PAUSED'}],'artifacts':[]}),patch('broker_client.call',side_effect=AssertionError('Viewer must not call broker')):
            client=TestClient(app.app,base_url='http://127.0.0.1')
            self.assertEqual(client.get('/').status_code,200)
            self.assertEqual(client.get('/api/dashboard-data').json()['campaign'][0]['state'],'PAUSED')
            self.assertEqual(client.get('/static/guide.json').status_code,200)
    def test_theory_and_canonical_metric_definition(self):
        data=json.loads((Path(__file__).parent/'static/guide.json').read_text(encoding='utf-8'))
        safety=next(s for s in data['sections'] if s['id']=='safety')
        self.assertEqual(len(safety['topics']),2)
        text=json.dumps(safety,ensure_ascii=False)
        for term in ('9.1','9.2','TREX','ZNE','DD','2N','UNKNOWN','NaN','provenance','covariance','does not necessarily','1612.02058','2012.09738'):self.assertIn(term,text)

if __name__=='__main__':unittest.main()
