"""Independent method checks on synthetic fixtures, never production observations."""
import unittest
from unittest.mock import patch
import numpy as np
from test_campaign_comparison import c,d
from test_matched_n_sensitivity import fixture


class OriginalMethodTests(unittest.TestCase):
    def test_C_original_prefix_rng_traversal_and_percentiles(self):
        rows=fixture()
        with patch.object(d,'matched_n_sensitivity',side_effect=AssertionError('C cannot be clustered')):
            actual=d.figure_c_statistics(rows)
        self.assertEqual(len(actual),808)
        rng=np.random.default_rng(1013);index=0
        for treatment in d.TREATMENTS:
            values=np.array([r['SSE'] for r in sorted(rows,key=lambda r:(r['source_n'],r['source_pub'])) if r['treatment']==treatment])
            for count in range(5,106):
                sample=values[:count]
                medians=np.median(rng.choice(sample,size=(3000,count),replace=True),axis=1)
                low,high=np.quantile(medians,[.025,.975],method='linear')
                row=actual[index];index+=1
                self.assertEqual(row['cumulative_pub_count'],count)
                self.assertEqual(row['median_SSE'],np.median(sample))
                self.assertEqual((row['CI_low'],row['CI_high']),(low,high))

    def test_D_original_PUB_bootstrap_not_clustered(self):
        rows=fixture();baseline=np.median([r['SSE'] for r in rows if r['treatment']==d.TREATMENTS[0]])
        with patch.object(d,'matched_n_sensitivity',side_effect=AssertionError('D cannot be clustered')):
            actual=d.figure_d_statistics(rows,dict(verified=True,measured_M0_median_SSE=baseline,classical_SSE=2.,ideal_SSE=3.))
        by_t={r['treatment']:r for r in actual};rng=np.random.default_rng(1013)
        for treatment in d.TREATMENTS:
            sample=np.array([r['SSE'] for r in sorted(rows,key=lambda r:(r['source_n'],r['source_pub'])) if r['treatment']==treatment])
            medians=np.median(rng.choice(sample,size=(3000,105),replace=True),axis=1)
            low,high=np.quantile(medians,[.025,.975],method='linear')
            self.assertEqual((by_t[treatment]['CI_low'],by_t[treatment]['CI_high']),(low,high))
        self.assertEqual([r['median_SSE'] for r in actual],sorted(r['median_SSE'] for r in actual))

    def test_EF_reference_draws_contrasts_before_quantiles(self):
        rows=fixture()
        # Nonproportional, unequal blocks prevent a trivial fixed-ratio check.
        for i,r in enumerate(rows):r['SSE']+=((i*17)%23)**2
        result=d.matched_n_sensitivity(rows)
        self.assertEqual(result['manifest']['seed'],1013)
        self.assertEqual(result['manifest']['replicates'],10000)
        blocks=[np.column_stack([[r['SSE'] for r in sorted(rows,key=lambda r:r['source_pub'])
                                  if r['source_n']==n and r['treatment']==t] for t in d.TREATMENTS]) for n in d.LEVELS]
        draws=np.random.default_rng(1013).integers(0,6,size=(10000,6))
        samples=np.array([np.median(np.concatenate([blocks[i] for i in draw]),axis=0) for draw in draws])
        delta=samples-samples[:,[0]];reduction=100*(1-samples/samples[:,[0]])
        point=np.median(np.concatenate(blocks),axis=0)
        for i,row in enumerate(result['statistics']):
            self.assertEqual(row['delta_SSE'],point[i]-point[0])
            self.assertEqual(row['reduction_percent'],100*(1-point[i]/point[0]))
            np.testing.assert_array_equal([row['delta_sensitivity_low'],row['delta_sensitivity_high']],np.quantile(delta[:,i],[.025,.975],method='linear'))
            np.testing.assert_array_equal([row['reduction_sensitivity_low'],row['reduction_sensitivity_high']],np.quantile(reduction[:,i],[.025,.975],method='linear'))

    def test_figure_specific_labels_seeds_and_write_gate(self):
        methods=d.figure_method_metadata()
        self.assertIn('pooled-prefix',methods['C']['label'])
        self.assertIn('PUB-level',methods['D']['label'])
        for letter in 'CD':self.assertNotIn('cluster',methods[letter]['label'])
        for letter in 'EF':self.assertIn('Matched n-level cluster',methods[letter]['label'])
        self.assertEqual({m['seed'] for m in methods.values()},{1013})
        self.assertEqual(methods['E'],methods['F'])
        with patch.object(c,'inspect_campaign',side_effect=AssertionError('Must not run')):
            with self.assertRaisesRegex(ValueError,'authorize-output-write'):
                c.restyle_publication('.',standardize_reference_methods=True)


if __name__=='__main__':unittest.main()
