import unittest
from unittest.mock import patch

class SinglePubHTTPTests(unittest.TestCase):
    def test_http_submit_and_test_cannot_bypass_block(self):
        from fastapi.testclient import TestClient
        import app
        client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
        with patch('broker_client.call') as broker:
            for purpose in ('TEST','RECOVERY'):
                r=client.post('/api/conversations/'+'a'*32+'/single-pub',json={'purpose':purpose,'action':'submit'})
                self.assertEqual(r.status_code,409)
            broker.assert_not_called()
            self.assertEqual(client.post('/api/single-pub/preview',json={'action':'submit'}).status_code,400)
            self.assertEqual(client.post('/api/single-pub/preview',json={'shell':'bad'}).status_code,422)

    def test_preview_uses_only_broker_and_returns_actual_result(self):
        from fastapi.testclient import TestClient
        import app
        client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
        with patch('broker_client.call',return_value={'state':'BLOCKED','reason':'fixture canonical policy'}) as broker:
            response=client.post('/api/single-pub/preview',json={'purpose':'TEST','n':30})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['reason'],'fixture canonical policy')
            self.assertEqual(broker.call_args.args[0],'/single-pub-preview')
            self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')
        self.assertEqual(client.post('/api/single-pub/preview',json={'n':31}).status_code,422)
    def test_broker_single_scope_confirmation_and_replay(self):
        import test_auto_broker as fixtures
        from worker_supervisor import ControlRequest,SinglePubScope
        fixture=fixtures.BrokerTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.evidence['supported_operations'].append('single_pub')
        scope=SinglePubScope(purpose='RECOVERY',treatment='M0_Baseline',slot=1,account='labK',scope_id='a'*64,action='monitor')
        request=fixture.s.prepare('fixture-chat',ControlRequest(operation='single_pub',n=5,single_pub=scope))
        self.assertEqual(request['control_state'],'AWAITING_CONFIRMATION')
        with patch.object(fixtures.b.subprocess,'Popen') as child:
            fixture.confirm(request);child.assert_called_once()
            import json
            launch=json.loads(fixture.broker.launch_path.read_text())
            self.assertEqual(launch['single_pub'],scope.model_dump())
            with self.assertRaises(ValueError):fixture.confirm(request)

if __name__=='__main__':unittest.main()
