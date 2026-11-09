"""Offline canonical STEP boundary; no real service or production lock acquisition."""
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch
import auto_broker as broker
from worker_supervisor import route_control, ProductionAdapter, ControlRequest


class PreflightTests(unittest.TestCase):
    def test_monitor_reuses_existing_diagnostic_once_and_reports_partial_failure(self):
        fake=SimpleNamespace(main=Mock(side_effect=lambda args:(print('Mock eight pending observations; no result retrieval.'),1)[1]))
        spec=SimpleNamespace(loader=Mock())
        with patch('importlib.util.spec_from_file_location',return_value=spec) as load,patch('importlib.util.module_from_spec',return_value=fake):
            result=broker.monitor005_step(broker.HERE)
        self.assertEqual(load.call_args.args[1],broker.HERE/'manager_diagnostics/ibm_batch_005_readonly.py')
        fake.main.assert_called_once_with(['--project',str(broker.HERE)])
        self.assertEqual(result['control_state'],'STEP_FAILED')
        self.assertIn('pending',result['answer'])
    def test_unreadable_receipt_is_unknown_not_stopped(self):
        campaign=Mock();campaign.campaign_root.return_value=broker.HERE/'state/fixture-campaign'
        b=broker.Broker(broker.HERE/'state/fixture',broker.HERE)
        with patch.object(broker,'modules',return_value=(None,campaign,None,None,None)),patch.object(broker,'probe',return_value='FREE'),patch.object(Path,'exists',return_value=True),patch.object(Path,'read_text',side_effect=PermissionError('fixture sharing violation')):
            result=b.inspect()
        self.assertEqual(result['worker'],'UNKNOWN')
        self.assertFalse(result['can_execute']);self.assertFalse(result['ownership_verified'])

    def test_canonical_read_function_exact_scope_and_sanitized_results(self):
        config=Mock()
        config.load_final_config.return_value={'allowed_backend':'ibm_fez'}
        config.campaign_authorization_from_final_config.return_value={
            'allowed_accounts':['fixture'], 'allowed_instance_refs':{'fixture':['opaque']}}
        call=Mock(return_value={'fixture':{'status':'WARN','remaining_provider_quota_seconds':155,
            'checks':{'provider_quota':'PASS'},'token':'DO-NOT-EXPOSE','reasons':['private credential details']}})
        with patch.object(broker,'modules',return_value=(None,None,None,config,None)),patch.dict(sys.modules,{'read_only_preflight':SimpleNamespace(preflight_all=call)}):
            result=broker.preflight_step(broker.HERE)
        call.assert_called_once_with(('fixture',),auth_mode='saved-profile',root=broker.HERE,
                                     allowed_instance_refs={'fixture':['opaque']})
        self.assertEqual(result['rows'][0]['provider_remaining_seconds'],155)
        self.assertEqual(result['rows'][0]['status'],'WARN')
        self.assertNotIn('DO-NOT-EXPOSE',str(result));self.assertNotIn('private',str(result))

    def test_invalid_scope_never_contacts_provider(self):
        config=Mock();config.load_final_config.return_value={'allowed_backend':'other'}
        config.campaign_authorization_from_final_config.return_value={'allowed_accounts':['fixture']}
        with patch.object(broker,'modules',return_value=(None,None,None,config,None)):
            with self.assertRaises(ValueError):broker.preflight_step(broker.HERE)

    def test_missing_nonfinite_quota_not_invented(self):
        config=Mock();config.load_final_config.return_value={'allowed_backend':'ibm_fez'}
        config.campaign_authorization_from_final_config.return_value={'allowed_accounts':['a','b'],'allowed_instance_refs':{}}
        call=Mock(return_value={'a':{'status':'FAIL','remaining_provider_quota_seconds':float('nan')}})
        with patch.object(broker,'modules',return_value=(None,None,None,config,None)),patch.dict(sys.modules,{'read_only_preflight':SimpleNamespace(preflight_all=call)}):
            result=broker.preflight_step(broker.HERE)
        self.assertIsNone(result['rows'][0]['provider_remaining_seconds'])
        self.assertEqual(result['rows'][1]['status'],'UNAVAILABLE')

    def test_explicit_routes_preserve_research_analysis(self):
        for text,op in [('Run preflight','preflight'),('Monitor Batch 005','monitor005'),('Show step result','status'),('Review targeted recovery','review')]:
            self.assertEqual(route_control(text).operation,op)
        self.assertIsNone(route_control('Analyze n=5'))
        for op in ('monitor','collect','batch'):
            self.assertTrue(ProductionAdapter().operation_blocker(ControlRequest(operation=op)))
        self.assertIsNone(ProductionAdapter().operation_blocker(ControlRequest(operation='preflight')))


if __name__=='__main__':unittest.main()
