import unittest
import copy
from unittest.mock import patch
from pathlib import Path
from test_campaign_comparison import c,d
from test_matched_n_sensitivity import fixture
from test_account_integration import scratch_directory


class PublicationStyleTests(unittest.TestCase):
    def test_plot_only_ranking_and_stable_ties(self):
        records=[dict(treatment=t,label=t,median_over_reference=v,delta_SSE=v-2,
                      reduction_percent=100*(1-v/2)) for t,v in
                 [('M7_ALL',4),('M0_Baseline',2),('M2_TREX',1),('M1_DD',1)]]
        before=copy.deepcopy(records)
        self.assertEqual([r['treatment'] for r in d.comparison_plot_order(records,'D')],
                         ['M1_DD','M2_TREX','M0_Baseline','M7_ALL'])
        for letter in 'EF':
            self.assertEqual([r['treatment'] for r in d.comparison_plot_order(records,letter)],
                             ['M1_DD','M2_TREX','M7_ALL'])
        self.assertEqual(records,before)

    def test_D_fixed_log_range_and_no_silent_interval_clipping(self):
        refs=dict(verified=True,classical_SSE=1000.,ideal_SSE=1500.,
                  measured_M0_median_SSE=c.statistics.median(r['SSE'] for r in fixture() if r['treatment']==d.TREATMENTS[0]))
        records=d.figure_d_statistics(fixture(),refs)
        with scratch_directory() as tmp:
            layout=d.render_figure_d(records,refs,Path(tmp))
            self.assertEqual(layout['y_scales'],['log'])
            self.assertEqual(layout['y_limits'],[[.9,6.5]])
            self.assertEqual(layout['y_labels'],['Median SSE / Classical SSE'])
            self.assertEqual(layout['plot_order'],[r['treatment'] for r in sorted(records,key=lambda r:(r['median_over_reference'],r['treatment']))])
        bad=copy.deepcopy(records);bad[0]['CI_high_over_reference']=6.6
        with scratch_directory() as tmp:
            with self.assertRaisesRegex(ValueError,'refusing silent clipping'):
                d.render_figure_d(bad,refs,Path(tmp))
            self.assertFalse(list(Path(tmp).glob('*.png')))

    def test_palette_and_reference_block_algebra(self):
        import numpy as np
        self.assertEqual(list(d.TREATMENT_COLORS.values()),['#666666','#0072B2','#009E73','#E69F00','#CC79A7','#D55E00','#56B4E9','#7B3FB5'])
        # Unequal block sizes make pooled and equal-weight estimands different.
        groups=[[np.array([1.,3.]),np.array([10.,20.,30.])],
                [np.array([2.,6.]),np.array([20.,40.,60.])]]
        draws=np.array([[0,1],[1,1],[0,0]])
        pooled,equal=d._matched_block_samples(groups,draws)
        for k,selection in enumerate(draws):
            for t,blocks in enumerate(groups):
                self.assertEqual(pooled[k,t],np.median(np.concatenate([blocks[i] for i in selection])))
                self.assertEqual(equal[k,t],np.mean([np.median(blocks[i]) for i in selection]))
        self.assertNotEqual(pooled[0,0],equal[0,0])

    def test_components_and_primary_equivalence(self):
        rows=fixture();supp=d.supplementary_sensitivity(rows,replicates=100)
        self.assertTrue(supp['manifest']['component_matches_primary'])
        primary=d.matched_n_sensitivity(rows,replicates=100)['statistics']
        for a,b in zip(primary,supp['shared_job']):
            for key in ('delta_SSE','reduction_percent','delta_sensitivity_low','delta_sensitivity_high'):
                self.assertEqual(a[key],b[key])
        rows[-1]['job_id']=rows[0]['job_id']
        linked=d.supplementary_sensitivity(rows,replicates=100)
        self.assertIn([5,30],linked['manifest']['connected_components'])
        self.assertEqual(len(linked['manifest']['connected_components']),5)

    def test_render_geometry_palette_and_csv_only(self):
        rows=fixture();result=d.matched_n_sensitivity(rows,replicates=100)
        original=copy.deepcopy(result)
        with scratch_directory() as tmp:
            folder=Path(tmp)
            # Renderers must consume supplied values without resampling them.
            with patch.object(d,'matched_n_sensitivity',side_effect=AssertionError('No recalculation')):
                layouts=d.render_figures_ef(result['statistics'],result['manifest'],folder)
            e,f=layouts['E'],layouts['F']
            self.assertEqual(e['axes_positions'],f['axes_positions'])
            self.assertEqual(e['inches'],[12.5,8])
            self.assertEqual(e['x_labels'],[[r['label'] for r in sorted(result['statistics'][1:],key=lambda r:(r['delta_SSE'],r['treatment']))]])
            self.assertEqual(e['x_labels'],f['x_labels'])
            self.assertEqual(e['treatment_colors'],{k:v for k,v in d.TREATMENT_COLORS.items() if k!=d.TREATMENTS[0]})
            self.assertEqual(e['treatment_colors'],f['treatment_colors'])
            self.assertTrue(e['no_clipping'] and f['no_clipping'])
            self.assertEqual(result,original)
            from PIL import Image
            for path in folder.glob('*.png'):
                with Image.open(path) as image:
                    self.assertEqual(image.size,(7500,4800))
                    self.assertTrue(all(abs(v-600)<.1 for v in image.info['dpi']))

    def test_clipping_is_rejected(self):
        _,_,plt=d._comparison_imports()
        with scratch_directory() as tmp:
            fig,ax=plt.subplots();fig.text(2,2,'outside canvas')
            with self.assertRaisesRegex(ValueError,'exceed fixed'):
                d._save_comparison(fig,Path(tmp),'bad.png')


if __name__=='__main__':unittest.main()
