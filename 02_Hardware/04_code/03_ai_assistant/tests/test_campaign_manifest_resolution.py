"""Immutable persisted level resolution and count-independent authorization."""
import sys,json,unittest
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import independent_n_campaign as campaign
from job_registry import JobRegistry
from final_configuration import load_final_config,validate_final_config
ROOT=Path(__file__).resolve().parents[4]

class ManifestResolutionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('Offline only')))
        registry=JobRegistry(JobRegistry.default_database(ROOT),readonly=True)
        self.status=registry.independent_campaign_status('vqe-independent-n-01')
        self.level=deepcopy(self.status['levels'][4])
        self.embedded=deepcopy(self.status['manifest']['levels'][4])
        self.registry=Mock();self.registry.independent_campaign_status.return_value=deepcopy(self.status)
        target=SimpleNamespace(target_id=self.embedded['target_id'],sha256=self.embedded['manifest_sha256'],
            input_hashes=self.embedded['manifest']['input_hashes'])
        self.builder=self.enterContext(patch.object(campaign,'build_target_manifest',return_value=target))

    def test_actual_persisted_status_row_resolves_same_target_as_embedded(self):
        self.assertNotIn('manifest',self.level)
        a=campaign._target_from_level(ROOT,self.level,registry=self.registry)
        b=campaign._target_from_level(ROOT,self.embedded)
        self.assertEqual(a.sha256,b.sha256)
        self.assertEqual(a.target_id,self.level['target_id'])
        self.assertEqual(self.level,self.status['levels'][4])

    def test_missing_campaign_manifest_fails_precisely(self):
        self.registry.independent_campaign_status.return_value={'exists':False}
        with self.assertRaisesRegex(RuntimeError,'immutable campaign manifest unavailable'):
            campaign._target_from_level(ROOT,self.level,registry=self.registry)
        self.builder.assert_not_called()

    def test_changed_parent_or_target_hash_rejected(self):
        parent=self.registry.independent_campaign_status.return_value['manifest']
        parent['levels'][4]['manifest']['shots_per_pub']=1
        with self.assertRaisesRegex(RuntimeError,'campaign manifest hash mismatch'):
            campaign._target_from_level(ROOT,self.level,registry=self.registry)
        self.embedded['manifest']['shots_per_pub']=1
        with self.assertRaisesRegex(RuntimeError,'target manifest hash mismatch'):
            campaign._target_from_level(ROOT,self.embedded)

    def test_missing_level_or_wrong_identity_rejected(self):
        self.level['target_id']='unregistered'
        with self.assertRaisesRegex(RuntimeError,'expected one immutable campaign level'):
            campaign._target_from_level(ROOT,self.level,registry=self.registry)
        self.level=deepcopy(self.status['levels'][4]);self.level['experiment_id']='other'
        with self.assertRaisesRegex(RuntimeError,'experiment_id mismatch'):
            campaign._target_from_level(ROOT,self.level,registry=self.registry)
        with self.assertRaisesRegex(RuntimeError,'lacks embedded manifest and campaign_id'):
            campaign._target_from_level(ROOT,{'target_id':'missing'})

    def test_frozen_source_drift_rejected(self):
        self.builder.return_value.sha256='changed'
        with self.assertRaisesRegex(RuntimeError,'differs from its immutable'):
            campaign._target_from_level(ROOT,self.level,registry=self.registry)

    def test_legacy_maximum_batches_is_ignored_by_both_validators(self):
        config=load_final_config(ROOT)
        original=deepcopy(self.status['manifest']['operator_authorization'])
        for value in (None,0,-1,21,1000,'legacy'):
            with self.subTest(value=value):
                validate_final_config({**config,'maximum_batches':value})
                campaign.validate_operator_authorization({**original,'maximum_batches':value})
