import hashlib
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch
from interpretations import interpretation, _cache
from reader import HERE

class Source:
    def __init__(self, path):
        self.path = path
        self.image = dict(id='image', name='residual_real.png', level=5, version='v1', sha256='png', finality='Published, not certified final')
        self.csv = dict(id='csv', name='residual_curves.csv', level=5, version='v1', sha256='', timestamp='fixture')
        self.refresh()
    def refresh(self):
        self.csv['sha256'] = hashlib.sha256(self.path.read_bytes()).hexdigest()
    def status(self):
        return {'artifacts': [self.image, self.csv]}
    def artifact(self, key):
        if key == 'csv' and hashlib.sha256(self.path.read_bytes()).hexdigest() != self.csv['sha256']:
            raise ValueError('Changed source')
        return self.path

class InterpretationTests(unittest.TestCase):
    def setUp(self):
        self.path = HERE/'state'/('interpretation-fixture-'+uuid.uuid4().hex+'.csv')
        self.path.write_text('frequency_Hz,M0_Baseline_delta_Z_real_ohm\n1,-2\n10,2\n')
        self.reader = Source(self.path)
        _cache.clear()
    def test_metrics_and_provenance(self):
        d = interpretation(self.reader, 'image')
        self.assertTrue(d['available'])
        self.assertEqual(d['summaries'][0]['mean'], 0)
        self.assertEqual(d['summaries'][0]['rms'], 2)
        self.assertEqual(d['numerical_source']['sha256'], self.reader.csv['sha256'])
        self.assertIn('not confidence intervals', d['warning'])
    def test_cache_revalidates_and_invalidates(self):
        old = interpretation(self.reader, 'image')
        with patch('interpretations.chart', side_effect=AssertionError('Cache miss')):
            self.assertIs(interpretation(self.reader, 'image'), old)
        self.path.write_text('frequency_Hz,M0_Baseline_delta_Z_real_ohm\n1,4\n')
        with self.assertRaises(ValueError): interpretation(self.reader, 'image')
        self.reader.refresh()
        self.assertEqual(interpretation(self.reader, 'image')['summaries'][0]['mean'], 4)
    def test_six_levels_and_no_cross_level(self):
        for n in (5,10,15,20,25,30):
            self.reader.image['level'] = n
            self.reader.csv['level'] = 999
            self.assertFalse(interpretation(self.reader,'image')['available'])
    def test_unsupported_statistics_honest(self):
        for name in ('05_mitigation_median_CI_convergence.png',):
            self.reader.image['name'] = name
            self.assertIn('not inferred',interpretation(self.reader,'image')['reason'])
    def test_nonfinite_refused(self):
        self.path.write_text('frequency_Hz,M0_Baseline_delta_Z_real_ohm\n1,nan\n')
        self.reader.refresh()
        with self.assertRaises(ValueError): interpretation(self.reader,'image')
    def test_bode_comparison_uses_recorded_values(self):
        self.reader.image['name']='bode_magnitude.png'
        self.reader.csv['name']='bode_curves.csv'
        self.path.write_text('frequency_Hz,Experimental_abs_Z_ohm,M0_Baseline_abs_Z_ohm\n1,10,12\n10,20,18\n')
        self.reader.refresh()
        row=interpretation(self.reader,'image')['summaries'][0]
        self.assertEqual(row['mean'],0)
        self.assertEqual(row['rms'],2)
    def test_nyquist_does_not_assume_frequency_pairing(self):
        self.reader.image['name']='nyquist.png'
        self.reader.csv['name']='nyquist_curves.csv'
        self.path.write_text('Experimental_Z_real,Experimental_minus_Z_imag,M0_Baseline_Z_real,M0_Baseline_minus_Z_imag\n1,2,3,4\n')
        self.reader.refresh()
        row=interpretation(self.reader,'image')['summaries'][0]
        self.assertNotIn('rms',row)
        self.assertIn('not quantified',row['text'])
    def test_frontend_and_architecture_permissions(self):
        chat=(HERE/'static/chat.js').read_text(encoding='utf-8')
        self.assertIn('Quick Assistant',chat)
        self.assertIn("assistantRequest('/api/ai/analyze'",chat)
        self.assertIn('mxene-local-context-permission',chat)
        self.assertIn('data-interpretation', (HERE/'static/gallery.js').read_text(encoding='utf-8'))
        graph=(HERE/'static/diagrams.js').read_text(encoding='utf-8')
        self.assertIn('Authorized orchestration remains subject to Python safety gates',graph)
        self.assertIn('No IBM execution or recovery authority',graph)

if __name__ == '__main__': unittest.main()
