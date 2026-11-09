import json
from pathlib import Path
import unittest
from unittest.mock import patch
from test_campaign_comparison import observations
from test_account_integration import scratch_directory
import campaign_comparison as c
from comparison_blocker_audit import trace_m0, search_repository


class BlockerAuditTests(unittest.TestCase):
    def test_reference_claim_is_sourced_not_replaced_by_a_literal(self):
        with scratch_directory() as tmp:
            path=Path(tmp)/'claim.json';path.write_text(json.dumps({'supplied_M0_median_SSE':123.456}))
            digest=c._sha(path)
            with patch.object(c,'published_comparison',return_value={'tables':[dict(name='triple_positioning_manifest.json',filename=path.name,sha256=digest)]}), \
                 patch.object(c,'published_artifact',return_value=path):
                value,source=c.comparison_reference_claim(tmp)
                self.assertEqual(value,123.456);self.assertFalse(source['scientifically_verified'])
                self.assertEqual(source['sha256'],digest)
                path.write_text(json.dumps({'supplied_M0_median_SSE':999}))
                with self.assertRaisesRegex(ValueError,'SHA-256'):c.comparison_reference_claim(tmp)

    def test_missing_reference_claim_cannot_fall_back_to_accepted_median(self):
        with patch.object(c,'published_comparison',return_value={'tables':[]}):
            with self.assertRaisesRegex(ValueError,'missing'):c.comparison_reference_claim(Path.cwd())

    def test_trace_is_odd_sample_order_statistic_with_exact_slot(self):
        rows=observations();trace,selected=trace_m0(rows)
        self.assertEqual(len(selected),105)
        self.assertEqual(trace['median_SSE'],selected[52]['SSE'])
        self.assertEqual(trace['median_observation'],selected[52])
        self.assertEqual({str(n) for n in c.LEVELS},set(trace['level_medians']))
        with self.assertRaisesRegex(ValueError,'840'):trace_m0(rows[:-1])

    def test_search_includes_hidden_archived_and_versioned_files(self):
        with scratch_directory() as tmp:
            root=Path(tmp);hidden=root/'.hidden/archive/v003';hidden.mkdir(parents=True)
            (hidden/'old.py').write_text('original_matched_bootstrap')
            (hidden/'Figure_E_old.png').write_bytes(b'not a scientific image')
            (hidden/'bundle.zip').write_bytes(b'not a valid archive')
            result=search_repository(root,{'candidate':'original_matched_bootstrap'})
            self.assertEqual(len(result['matches']['candidate']),1)
            self.assertEqual(len(result['archive_candidates']),1)
            self.assertEqual(len(result['EF_filename_candidates']),1)


if __name__=='__main__':unittest.main()
