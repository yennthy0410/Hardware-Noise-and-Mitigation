"""Operator HTTP/transport contracts; all execution and provider calls mocked."""
import unittest
from unittest.mock import patch,Mock
from fastapi.testclient import TestClient
import app
import auto_broker

class ManualHTTPTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})

    def test_manual_endpoint_is_separate_and_strict(self):
        with patch('broker_client.call',return_value={'state':'READY'}) as broker:
            r=self.client.post('/api/manual-execution',json={'action':'config'})
            self.assertEqual(r.status_code,200);self.assertEqual(broker.call_args.args[0],'/manual-operation')
            for extra in ({'approved':'true'},{'shell':'whoami'}):
                self.assertEqual(self.client.post('/api/manual-execution',json={'action':'confirm',**extra}).status_code,422)
            self.assertEqual(self.client.post('/api/manual-execution',json={'action':'config'},headers={'Origin':'http://attacker.invalid'}).status_code,403)

    def test_broker_unavailable_is_clear(self):
        with patch('broker_client.call',side_effect=OSError('offline')):
            r=self.client.post('/api/manual-execution',json={'action':'config'})
            self.assertEqual(r.status_code,503);self.assertIn('broker',r.text.lower())

    def test_preflight_fixed_scientific_interpreter_no_auto_start(self):
        with patch('manual_execution.ManualExecution'),patch.object(auto_broker.subprocess,'run',return_value=Mock(returncode=0,stdout='{"state":"READY"}')) as run,patch.object(auto_broker.subprocess,'Popen') as launch:
            self.assertEqual(auto_broker.manual_operation({'action':'preflight','request_id':'a'*32})['state'],'READY')
            self.assertEqual(run.call_args.args[0],[str(auto_broker.PYTHON),'-B',str(auto_broker.SCRIPT),'--manual-query'])
            self.assertFalse(run.call_args.kwargs['shell']);launch.assert_not_called()

if __name__=='__main__':unittest.main()
