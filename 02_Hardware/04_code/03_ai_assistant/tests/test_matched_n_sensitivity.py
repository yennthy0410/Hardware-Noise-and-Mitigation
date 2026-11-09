import copy
from pathlib import Path
import unittest
from test_campaign_comparison import observations
from test_account_integration import scratch_directory
import dependence_statistics as d
import campaign_comparison as c


def fixture():
    rows=observations()
    for r in rows:
        r['job_id']+=f"-n{r['source_n']}"
        r['SSE']*=1+d.TREATMENTS.index(r['treatment'])/10
    return rows


class MatchedSensitivityTests(unittest.TestCase):
    def test_shared_resampling_preserves_exact_multiplicative_contrast(self):
        # Every treatment is a fixed multiple of M0 at every observation.
        # Shared resampling must therefore give a degenerate reduction interval.
        result=d.matched_n_sensitivity(fixture(),replicates=200,seed=71)
        for i,r in enumerate(result['statistics']):
            self.assertAlmostEqual(r['reduction_percent'],-10*i)
            self.assertAlmostEqual(r['reduction_sensitivity_low'],-10*i)
            self.assertAlmostEqual(r['reduction_sensitivity_high'],-10*i)
            self.assertAlmostEqual(r['delta_SSE'],r['median_SSE']-r['M0_median_SSE'])
        baseline=result['statistics'][0]
        self.assertEqual(baseline['delta_sensitivity_low'],0)
        self.assertEqual(baseline['delta_sensitivity_high'],0)

    def test_order_invariance_determinism_and_recorded_assumptions(self):
        rows=fixture();a=d.matched_n_sensitivity(rows,replicates=200,seed=93)
        b=d.matched_n_sensitivity(list(reversed(rows)),replicates=200,seed=93)
        self.assertEqual(a['statistics'],b['statistics'])
        self.assertEqual(a['manifest']['seed'],93)
        self.assertEqual(a['manifest']['replicates'],200)
        self.assertIn('not inferential',a['manifest']['method'])
        self.assertAlmostEqual(sum(a['manifest']['point_weights'].values()),1)

    def test_job_crossing_blocks_is_not_silently_split(self):
        rows=fixture();rows[-1]['job_id']=rows[0]['job_id']
        with self.assertRaisesRegex(ValueError,'spans n-level'):d.matched_n_sensitivity(rows,replicates=200)

    def test_ef_png_and_csv_share_numerical_records(self):
        result=d.matched_n_sensitivity(fixture(),replicates=200)
        with scratch_directory() as tmp:
            root=Path(tmp)
            records=d.generate_figures_ef(result,figure_dir=root,table_dir=root)
            c.verify_plotted_table(root/'M0_matched_n_sensitivity_statistics.csv',records)
            for name in c.FIGURE_NAMES[2:]:c.inspect_png(root/name,layout='landscape')

    def test_optional_reference_lines_can_be_omitted_explicitly(self):
        rows=fixture()
        with scratch_directory() as tmp:
            root=Path(tmp);baseline=c.statistics.median(r['SSE'] for r in rows if r['treatment']==d.TREATMENTS[0])
            records=d.generate_figure_d(rows=rows,figure_dir=root,table_dir=root,
                references=dict(verified=True,measured_M0_median_SSE=baseline,classical_SSE=None,ideal_SSE=None))
            self.assertTrue(all(r['reference_type']=='measured_M0_median' for r in records))
            c.verify_plotted_table(root/'triple_positioning_105.csv',records)


if __name__=='__main__':unittest.main()
