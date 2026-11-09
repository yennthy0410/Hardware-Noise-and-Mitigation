"""Real Windows process/lock integration; fake registry/cycle, no IBM imports."""
import json, os, sqlite3, sys, time, unittest, uuid
from pathlib import Path
from unittest.mock import patch
import auto_broker as b
from worker_supervisor import Supervisor,ProductionAdapter,ControlRequest

CHILD = r'''
import sys,json
from pathlib import Path
sys.path.insert(0,HOME)
import auto_broker as b
from unittest.mock import patch
root=Path(ROOT);b.PROJECT=root;b.STATE=root;b.SCRIPT=Path(__file__).resolve()
pipeline,campaign,runtime,config,registry=b.modules()
class Ledger:
    def independent_campaign_status(self,c):
        return {'exists':True,'state':'RUNNING','current_level_index':5,'checkpoint':{}}
    def checkpoint_independent_campaign(self,c,**values):
        (root/'checkpoint.json').write_text(json.dumps(values))
ledger=Ledger()
original=b.Broker
broker=original(root,root)
b.Broker=lambda:broker
broker.policy=lambda:POLICY
def run(project,campaign_id,**kwargs):
    assert kwargs['once'] is False and kwargs['authorize_live_campaign'] and kwargs['confirm_live_submission']
    return {'pipeline':campaign.run_campaign_auto_manager(root,campaign_id,ledger,
            authorize_live_campaign=True,resume=kwargs['resume'],poll_seconds=1)}
with patch.object(campaign,'campaign_root',return_value=root/'campaign'), \
     patch.object(campaign,'run_independent_campaign',return_value={'state':'RUNNING'}), \
     patch.object(pipeline,'run_pipeline',side_effect=run):
    b.worker(sys.argv[2])
'''

class NativeProcessTests(unittest.TestCase):
    def test_real_child_identity_attach_pause_and_exit(self):
        root=b.HERE/'state'/('native-auto-fixture-'+uuid.uuid4().hex);root.mkdir()
        script=root/'fixture_worker.py'
        policy=dict(backend='fake-backend',accounts=['fixture'],authorization_identity='fixture',checkpoint='fixture',
                    ready=True,complete=False,unknown_submission=False,quota_implications='Fixture only 150/40')
        script.write_text('HOME='+repr(str(b.HERE))+'\nROOT='+repr(str(root))+'\nPOLICY='+repr(policy)+'\n'+CHILD,encoding='utf-8')
        broker=b.Broker(root,root)
        pipeline,campaign,runtime,config,registry=b.modules()
        class Ledger:
            @staticmethod
            def default_database(project):return root/'fixture-ledger'
            def __init__(self,*args,**kwargs):pass
            def independent_campaign_status(self,c):return {'exists':True,'state':'RUNNING','current_level_index':5}
            def checkpoint_independent_campaign(self,c,**values):(root/'pause-request.json').write_text(json.dumps(values))
        fake_registry=type('RegistryModule',(),{'JobRegistry':Ledger})
        def call(path,payload=None):return broker.inspect() if path=='/status' else broker.dispatch(payload['request_id'])
        try:
            with patch.object(b,'SCRIPT',script), \
                 patch.object(broker,'policy',return_value=policy),patch.object(campaign,'campaign_root',return_value=root/'campaign'), \
                 patch.object(b,'modules',return_value=(pipeline,campaign,runtime,config,fake_registry)), \
                 patch('broker_client.call',side_effect=call):
                supervisor=Supervisor(root/'worker_control.sqlite3',ProductionAdapter())
                self.assertEqual(broker.inspect()['worker'],'STOPPED_VERIFIED')
                request=supervisor.prepare('fixture-chat',ControlRequest(operation='resume'))
                result=supervisor.confirm('fixture-chat',request['control_request'],True)
                self.assertEqual(result['control_state'],'START_REQUESTED')
                def wait_state(expected):
                    deadline=time.monotonic()+25
                    while time.monotonic()<deadline:
                        evidence=broker.inspect()
                        if evidence['worker']==expected:return evidence
                        time.sleep(.2)
                    self.fail('Fixture worker did not reach '+expected+'; '+str(evidence))
                evidence=wait_state('RUNNING_VERIFIED')
                self.assertTrue(evidence['ownership_verified'])
                # Simulate web-server restart: reconstruct from durable files.
                supervisor=Supervisor(root/'worker_control.sqlite3',ProductionAdapter())
                self.assertEqual(supervisor.prepare('fixture-chat',ControlRequest(operation='start'))['control_state'],'ATTACHED')
                request=supervisor.prepare('fixture-chat',ControlRequest(operation='pause'))
                self.assertEqual(supervisor.confirm('fixture-chat',request['control_request'],True)['control_state'],'PAUSE_REQUESTED')
                wait_state('STOPPED_VERIFIED')
                self.assertEqual(json.loads((root/'checkpoint.json').read_text())['event_type'],'AUTO_MANAGER_PAUSED')
                self.assertEqual(json.loads((root/'auto_worker.json').read_text())['outcome'],'PAUSED')
        finally:
            # Only this exact disposable fixture process may be cleaned up on a
            # failed test. Never target canonical production worker commands.
            psutil=b.psutil_module()
            for process in psutil.process_iter():
                try:
                    if process.cmdline()[1:3]==['-B',str(script)]:
                        process.terminate();process.wait(5)
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
            for folder in (root/'campaign',root):
                if folder.exists():
                    for path in folder.iterdir():
                        if path.is_file():path.unlink()
                    folder.rmdir()

if __name__=='__main__':unittest.main()
