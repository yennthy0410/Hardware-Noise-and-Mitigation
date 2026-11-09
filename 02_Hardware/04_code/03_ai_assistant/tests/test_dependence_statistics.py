import copy
import importlib.util
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from test_account_integration import scratch_directory

AI = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(AI))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


stats = load('mxene_test_statistics',AI/'03_python_automation/dependence_statistics.py')
agent = load('mxene_test_agent',AI/'01_ai_agent/offline_tools.py')


def observations():
    # Unequal clusters of at most four correlated observations; each design
    # cluster is distinct across n and treatment. This is synthetic evidence.
    return [dict(treatment=t,source_n=n,source_pub=p,
                 SSE=float(100+30*(p//4)+i),
                 execution_cluster=f'{t}:{n}:{p//4}')
            for i,t in enumerate(stats.TREATMENTS) for n in stats.LEVELS for p in range(1,n+1)]


def design(rows):
    return dict(observations_sha256=stats.observation_hash(rows),unit_field='execution_cluster',
                reviewer='offline fixture',evidence='Synthetic independently generated clusters',
                independence_rationale='Fixture design',cross_treatment_independence_rationale='Unpaired fixture',paired=False)


class DependenceStatisticsTests(unittest.TestCase):
    def test_exact_unique_slots_required(self):
        rows=observations()
        for invalid in (rows[:-1],rows+[rows[0]],rows[:-1]+[rows[0]], [r for r in rows if r['source_n']!=30]):
            with self.subTest(count=len(invalid)),self.assertRaises(ValueError):stats.validate_observations(invalid)

    def test_nonpositive_nonfinite(self):
        for value in (0,-1,float('nan'),float('inf'),True):
            rows=observations();rows[0]['SSE']=value
            with self.subTest(value=value),self.assertRaises(ValueError):stats.validate_observations(rows)

    def test_determinism_correlated_unequal_clusters(self):
        rows=observations();before=copy.deepcopy(rows)
        a=stats.analyze(rows,design(rows),iterations=100)
        self.assertEqual(a,stats.analyze(list(reversed(rows)),design(rows),iterations=100))
        self.assertEqual(rows,before)
        c=[r for r in a['statistics'] if r['method']=='cluster']
        self.assertEqual(len(c),8);self.assertTrue(all(len(set(r['cluster_sizes']))>1 for r in c))
        self.assertAlmostEqual(sum(r['probability_best'] for r in a['ranking']),1)
        self.assertEqual(len(a['contrasts']),14)
        self.assertTrue(any(a['statistics'][2*i]['interval_width'] != c[i]['interval_width'] for i in range(8)))

    def test_missing_batch_ids_do_not_invent_independence(self):
        rows=observations()
        for r in rows:r.pop('execution_cluster')
        result=stats.analyze(rows,iterations=100)
        self.assertFalse(result['manifest']['cluster_supported'])
        self.assertEqual(result['ranking'],[])
        self.assertIn('cluster',result['manifest']['skipped_methods'])

    def test_six_top_level_groups_skip_hierarchical(self):
        result=stats.analyze(observations(),iterations=100)
        self.assertIn('hierarchical',result['manifest']['skipped_methods'])
        self.assertAlmostEqual(sum(result['manifest']['n_level_weights'].values()),1)
        self.assertNotEqual(result['manifest']['n_level_weights']['5'],result['manifest']['n_level_weights']['30'])

    def test_hash_mismatch(self):
        rows=observations();contract=design(rows);rows[0]['SSE']+=1
        with self.assertRaisesRegex(ValueError,'hash'):stats.analyze(rows,contract,iterations=100)

    def test_nonnested_metadata(self):
        rows=observations();rows[5]['execution_cluster']=rows[0]['execution_cluster']
        with self.assertRaisesRegex(ValueError,'Nonnested'):stats.analyze(rows,design(rows),iterations=100)

    def test_pairing_not_inferred(self):
        rows=observations();contract=design(rows);contract['paired']=True
        with self.assertRaisesRegex(ValueError,'unpaired'):stats.analyze(rows,contract,iterations=100)

    def test_missing_cluster_or_review(self):
        rows=observations();rows[0].pop('execution_cluster')
        with self.assertRaisesRegex(ValueError,'Missing'):stats.analyze(rows,design(rows),iterations=100)
        rows=observations();contract=design(rows);contract.pop('reviewer')
        with self.assertRaisesRegex(ValueError,'reviewed'):stats.analyze(rows,contract,iterations=100)

    def test_no_network(self):
        with patch.object(socket,'create_connection',side_effect=AssertionError('network forbidden')), \
             patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            stats.analyze(observations(),iterations=100)

    def protected(self,root):
        from scientific_exports import _sha
        paths=[root/'C.png',root/'D.png',root/'source.csv']
        for p in paths:p.write_bytes(b'protected fixture')
        return {str(p.resolve()):_sha(p) for p in paths}

    def test_skip_export_restart_and_protected_hashes(self):
        with scratch_directory() as tmp:
            root=Path(tmp);protected=self.protected(root)
            result=stats.analyze(observations(),iterations=100)
            first=stats.export(result,root/'statistics',protected=protected)
            self.assertEqual(first,stats.export(result,root/'statistics',protected=protected))
            self.assertEqual(list((root/'statistics').glob('*.png')),[])
            (root/'C.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):stats.export(result,root/'statistics',protected=protected)

    def test_interrupted_publication_never_marks_success(self):
        with scratch_directory() as tmp:
            root=Path(tmp);protected=self.protected(root);result=stats.analyze(observations(),iterations=100)
            with patch('os.rename',side_effect=OSError('interrupted')),self.assertRaises(OSError):
                stats.export(result,root/'statistics',protected=protected)
            self.assertFalse((root/'statistics').exists())
            self.assertEqual(list(root.glob('.st-*')),[])
            stats.export(result,root/'statistics',protected=protected)

    def test_supported_export_600dpi_preserves_c_d(self):
        from scientific_exports import _sha,inspect_png
        with scratch_directory() as tmp:
            root=Path(tmp);protected=self.protected(root);rows=observations()
            result=stats.analyze(rows,design(rows),iterations=100)
            stats.export(result,root/'statistics',protected=protected)
            pngs=list((root/'statistics').glob('*.png'));self.assertEqual(len(pngs),2)
            for p in pngs:inspect_png(p,layout='landscape')
            for p,h in protected.items():self.assertEqual(_sha(Path(p)),h)

    def test_typed_allowlist_and_progressive_skill_loading(self):
        with self.assertRaises(ValueError):agent.invoke('shell',agent.CampaignRequest(Path.cwd()))
        with self.assertRaises(TypeError):agent.invoke('campaign-comparison',{})
        with self.assertRaises(ValueError):agent.CampaignRequest(Path.cwd(),'../escape')
        self.assertIn('BLOCKED',agent.load_skill('campaign-comparison'))

    def test_comparison_refuses_incomplete_and_incompatible_contract(self):
        from job_registry import JobRegistry
        from independent_n_campaign import _campaign_completion_verification
        with patch.object(JobRegistry,'independent_campaign_status',return_value={}), \
             patch('independent_n_campaign._campaign_completion_verification',return_value={
                 'ready':False, 'verified_n_levels':[], 'failures':['n=30 incomplete']}):
            result=agent.invoke('campaign-comparison',agent.CampaignRequest(AI.parents[2]))
        self.assertFalse(result['ready']);self.assertEqual(result['state'],'BLOCKED')
        self.assertIn('n=30 incomplete',result['blockers'])
        self.assertEqual([],result['missing_export_api'])


if __name__=='__main__':unittest.main()
