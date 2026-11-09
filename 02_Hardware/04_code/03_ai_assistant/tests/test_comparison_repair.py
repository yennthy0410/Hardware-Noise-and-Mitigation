"""Fixture-only reconstruction and immutable receipt-history tests."""
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

AI=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(AI/'03_python_automation'),str(AI)]
from test_account_integration import scratch_directory
import step_artifacts as s
import repair_comparison_artifacts as repair
from adaptive_targets import next_analysis_version


def level(n,ready=False):
    return dict(n=n,ready=ready,scientific_complete=True,selected_archives_verified=True,
                accepted=n*8,expected=n*8,duplicate_slots=False,dataset_sha256='a'*64,
                target_id=f'target{n}',analysis_receipt=None,candidates=[{'actual_sha256':None}])


class RepairTests(unittest.TestCase):
    def setUp(self):
        blocker=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        blocker.start();self.addCleanup(blocker.stop)

    def test_missing_tree_requires_new_version_and_ready_skips(self):
        self.assertEqual(repair.action_for_level(level(5)),'NEW_VERSION_ANALYZE_AND_EXPORT')
        self.assertEqual(repair.action_for_level(level(5,True)),'SKIP_READY')

    def test_scientific_failure_cannot_authorize_reconstruction(self):
        for field,value in [('scientific_complete',False),('selected_archives_verified',False),
                            ('accepted',39),('duplicate_slots',True)]:
            row=level(5);row[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):repair.action_for_level(row)

    def test_existing_manifest_requires_diagnosis_not_new_fit(self):
        row=level(5);row['candidates']=[{'actual_sha256':'existing'}]
        with self.assertRaisesRegex(ValueError,'existing analysis'):repair.action_for_level(row)
        row=level(5);row['existing_analysis_manifests']=['orphan/analysis_manifest.json']
        with self.assertRaisesRegex(ValueError,'unbound analysis'):repair.action_for_level(row)

    def test_exact_cost_copy_and_existing_conflict(self):
        with scratch_directory() as folder:
            source=folder/'source.png';source.write_bytes(b'fixture cost bytes')
            manifest=folder/'export_manifest.json';manifest.write_text('{"fixture":true}')
            target=folder/'restored.png'
            plan=dict(source=str(source),target=str(target),sha256=repair._sha(source),
                      export_manifest=str(manifest),export_manifest_sha256=repair._sha(manifest))
            with patch.object(repair,'inspect_png'):
                repair.restore_cost_png(plan);self.assertEqual(target.read_bytes(),source.read_bytes())
                repair.restore_cost_png(plan);self.assertEqual(target.read_bytes(),source.read_bytes())
                target.write_bytes(b'conflicting existing file')
                with self.assertRaisesRegex(ValueError,'SHA-256'):repair.restore_cost_png(plan)
                self.assertEqual(target.read_bytes(),b'conflicting existing file')

    def test_plan_excludes_n30_and_requires_n30_ready(self):
        evidence={'levels':[level(n,n==30) for n in (5,10,15,20,25,30)]}
        self.assertEqual([x['n'] for x in repair.repair_plan(evidence)],[5,10,15,20,25])
        evidence['levels'][-1]['ready']=False
        with self.assertRaisesRegex(ValueError,'n=30'):repair.repair_plan(evidence)

    def test_no_writes_without_authorization(self):
        with patch.object(repair,'protected_inventory',side_effect=AssertionError('must not run')):
            with self.assertRaises(PermissionError):repair.execute_plan(Path.cwd(),{},[],authorize=False)

    def test_receipt_chain_preserves_base_and_rejects_tampering(self):
        with scratch_directory() as folder:
            digest='a'*64;base=folder/f'a25-{digest[:24]}.json'
            original=json.dumps({'n':25,'dataset_sha256':digest}).encode();base.write_bytes(original)
            path,extra=s.next_analysis_receipt(folder,25,digest)
            path.write_text(json.dumps({'n':25,'dataset_sha256':digest,**extra}))
            self.assertEqual(s.latest_analysis_receipt(folder,25,digest),path)
            self.assertEqual(base.read_bytes(),original)
            base.write_text('changed')
            with self.assertRaisesRegex(ValueError,'history'):s.latest_analysis_receipt(folder,25,digest)

    def test_first_version_without_base_and_gap_rejected(self):
        with scratch_directory() as folder:
            path,extra=s.next_analysis_receipt(folder,5,'a'*64)
            self.assertIsNone(extra['previous_receipt'])
            path.write_text(json.dumps({'n':5,'dataset_sha256':'a'*64,**extra}))
            self.assertEqual(s.latest_analysis_receipt(folder,5,'a'*64),path)
            path.rename(folder/path.name.replace('-v001','-v002'))
            with self.assertRaisesRegex(ValueError,'gap'):s.latest_analysis_receipt(folder,5,'a'*64)

    def test_analysis_version_skips_partial_old_directories(self):
        with scratch_directory() as folder:
            base=folder/'target15';(folder/'target15_v001-old').mkdir()
            path=next_analysis_version(base,'usage')
            self.assertTrue(path.name.startswith('target15_v002-'))
            self.assertFalse(path.exists());self.assertFalse(base.exists())

    def test_receipt_bound_valid_analysis_needs_only_export(self):
        with scratch_directory() as folder:
            out=folder/'analysis';out.mkdir();(out/'analysis_manifest.json').write_text('{}')
            receipt=folder/'receipt.json';receipt.write_text(json.dumps({'output':str(out),'artifact_sha256':s.artifact_hashes(out)}))
            row=level(25);row['analysis_receipt']=str(receipt)
            self.assertEqual(repair.action_for_level(row),'EXPORT_ONLY')
            (out/'analysis_manifest.json').write_text('changed')
            with self.assertRaisesRegex(ValueError,'SHA-256'):repair.action_for_level(row)

    def test_canonical_step_new_receipt_export_and_old_bytes_preserved(self):
        # Reuse the existing canonical STEP fixture without changing Dashboard code.
        sys.path.insert(0,str(AI.parent/'MXene_Dashboard_Dev'))
        from test_canonical_steps import CanonicalStepTests
        fixture=CanonicalStepTests('runTest');fixture.setUp()
        try:
            first=fixture.run_step('analyze')['result']
            old=Path(first['analysis_receipt']['path']);before=old.read_bytes()
            output=fixture.output.parent/'target5_v001-fixture'
            def analyze(*args,**kw):
                self.assertEqual(kw,{'new_version':True})
                output.mkdir();(output/'analysis_manifest.json').write_text('{"fixture_version":1}')
                return {'output':str(output),'artifacts_verified':True}
            fixture.mocks[9].side_effect=analyze
            new=fixture.run_step('analyze',new_analysis_version=True)['result']
            self.assertNotEqual(new['analysis_receipt']['path'],str(old));self.assertEqual(old.read_bytes(),before)
            receipt=json.loads(Path(new['analysis_receipt']['path']).read_text())
            self.assertEqual(receipt['previous_receipt']['name'],old.name)
            exported=fixture.run_step('export')['result']
            self.assertEqual(exported['analysis_output'],str(output))
            self.assertEqual(old.read_bytes(),before)
            with self.assertRaises(ValueError):fixture.run_step('export',new_analysis_version=True)
        finally:fixture.doCleanups()


if __name__=='__main__':unittest.main()
