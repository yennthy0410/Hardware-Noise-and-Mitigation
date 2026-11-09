import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import app, HERE
import campaign_comparison as comparison


class ComparisonHTTPTests(unittest.TestCase):
    def test_explicit_missing_state_without_historical_fallback(self):
        with patch.object(comparison,'published_comparison',side_effect=ValueError('SHA-256 mismatch')):
            response=TestClient(app,base_url='http://localhost').get('/api/comparison')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['state'],'MISSING_OR_INVALID_ARTIFACT')
        self.assertIn('SHA-256',response.json()['reason'])

    def test_uses_project_root_and_shared_contract(self):
        with patch.object(comparison,'published_comparison',return_value={'state':'BLOCKED_REFERENCE'}) as method:
            response=TestClient(app,base_url='http://localhost').get('/api/comparison')
        self.assertEqual(response.json()['state'],'BLOCKED_REFERENCE')
        method.assert_called_once_with(HERE.parents[2])

    def test_unlisted_download_refused(self):
        with patch.object(comparison,'published_artifact',side_effect=ValueError('not committed')):
            response=TestClient(app,base_url='http://localhost').get('/api/comparison/artifacts/stale.png')
        self.assertEqual(response.status_code,404)

    def test_frontend_uses_pngs_and_no_second_statistics_engine(self):
        source=(HERE/'static/comparison.js').read_text()
        self.assertIn('/api/comparison',source)
        self.assertIn('Figure_E_M0_cluster_bootstrap_delta_SSE.png',source)
        self.assertNotIn('Plotly',source)
        self.assertNotIn('04_code/results',source)
        self.assertIn('Missing / blocked artifact',source)


if __name__=='__main__':unittest.main()
