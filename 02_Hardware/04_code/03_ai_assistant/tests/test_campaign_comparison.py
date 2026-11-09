"""Offline contracts: fixture observations are synthetic, never campaign evidence."""
import copy
import json
import math
from pathlib import Path
import socket
import sys
from test_account_integration import scratch_directory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

AI=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(AI/'03_python_automation'),str(AI)]
import campaign_comparison as c
import dependence_statistics as d


def fixture(n=5):
    target=SimpleNamespace(target_n=n,target_id='fixture',experiment_id='fixture-exp',
                           input_hashes={'theta':'fixed'},sha256='target-hash')
    selected=[];maps=[];exported=[];fits=[]
    for t in c.TREATMENTS:
        for p in range(1,n+1):
            # Every source PUB is 1: renaming it to a logical slot must fail.
            job=f'{t}-job{p}'
            row=dict(treatment_id=t,logical_pub_index=p,job_id=job,source_sha256='source',
                     source='ORIGINAL',logical_block_id=f'block{p}',batch_id=f'batch{p}',
                     account_label='fixture',backend='ibm_fez',input_hashes=target.input_hashes)
            selected.append(row)
            maps.append(dict(treatment_id=t,logical_pub_index=str(p),job_id=job,source_sha256='source',
                             source='ORIGINAL',source_block_id=f'block{p}',batch_id=f'batch{p}',
                             account_label='fixture',backend='ibm_fez',input_hashes=json.dumps(target.input_hashes),
                             target_id=target.target_id,target_n=str(n),source_pub='1',combined_pub=str(p)))
            exported.append(dict(treatment=t,IBM_job_id=job,PUB_index='1',n=str(n),experiment_id=target.experiment_id,
                                 SSE=str(264*p),RMSE=str(math.sqrt(p))))
            fits.append(dict(treatment=t,pub=str(p),SSE=str(264*p),RMSE=str(math.sqrt(p))))
    return target,selected,maps,exported,fits


def observations():
    return [row for n in c.LEVELS for row in c.join_observations(*fixture(n))]


class ComparisonTests(unittest.TestCase):
    def test_source_local_pub_requires_identity_join(self):
        args=fixture();rows=c.join_observations(*args)
        self.assertEqual(len(rows),40)
        self.assertEqual({r['source_pub'] for r in rows},set(range(1,6)))
        args=list(args);args[3]=list(reversed(args[3]))
        self.assertEqual(c.join_observations(*args),rows)

    def test_duplicates_missing_and_out_of_range_rejected(self):
        for index in (1,2,3,4):
            args=list(fixture());args[index][-1]=copy.deepcopy(args[index][0])
            with self.subTest(index=index),self.assertRaises(ValueError):c.join_observations(*args)
        args=list(fixture());args[1].pop()
        with self.assertRaisesRegex(ValueError,'inventory'):c.join_observations(*args)

    def test_wrong_job_frozen_hash_and_archive_digest_rejected(self):
        for field,value in [('job_id','wrong'),('input_hashes','{}'),('source_sha256','bad')]:
            args=fixture();args[2][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):c.join_observations(*args)

    def test_metric_tampering_rejected(self):
        for metric in ('SSE','RMSE'):
            args=fixture();args[3][0][metric]='123'
            with self.assertRaisesRegex(ValueError,'canonical'):c.join_observations(*args)
        args=fixture();args[3][0]['RMSE']=args[4][0]['RMSE']='2'
        with self.assertRaisesRegex(ValueError,'definition'):c.join_observations(*args)

    def test_hash_error_has_exact_expected_actual_path(self):
        with scratch_directory() as tmp:
            p=Path(tmp)/'manifest.json';p.write_text('{}');actual=c._sha(p)
            with self.assertRaises(ValueError) as error:c.check_hash(p,'expected')
            self.assertIn(actual,str(error.exception));self.assertIn(str(p),str(error.exception))
            p.unlink()
            with self.assertRaisesRegex(ValueError,'actual=MISSING'):c.check_hash(p,'expected')

    def test_missing_analysis_never_rebinds_export_hash(self):
        with scratch_directory() as tmp:
            p=Path(tmp);ep=p/'export_manifest.json'
            ep.write_text(json.dumps({'source_analysis_manifest_sha256':'a'*64}))
            target,selected,*_=fixture()
            with self.assertRaisesRegex(ValueError,'MISSING'):c.verify_analysis(target,selected,p,ep)
            self.assertEqual(c.read_json(ep)['source_analysis_manifest_sha256'],'a'*64)

    def test_descriptive_sample_sd_ratios_and_no_ci(self):
        rows=observations();table=c.summarize(rows)
        self.assertEqual(len(table),56)
        first=table[0];self.assertEqual(first['count'],5)
        self.assertEqual(first['mean_SSE'],792)
        self.assertAlmostEqual(first['sample_SD_SSE'],264*math.sqrt(2.5))
        self.assertEqual(first['median_SSE_over_None'],1)
        self.assertEqual(first['median_SSE_reduction_percent'],0)
        self.assertFalse(any('CI' in key for r in table for key in r))
        self.assertEqual(table[-1]['count'],105)

    def test_output_authorization_precedes_even_preflight(self):
        with patch.object(c,'inspect_campaign',side_effect=AssertionError('must not run')):
            with self.assertRaisesRegex(PermissionError,'authorize-output-write'):c.publish(Path.cwd(),'fixture')

    def test_comparison_paths_ignore_working_directory(self):
        import os
        from repository_paths import comparison_output_paths
        expected=comparison_output_paths()
        previous=Path.cwd()
        with scratch_directory() as tmp:
            try:
                os.chdir(tmp)
                self.assertEqual(comparison_output_paths(),expected)
            finally:os.chdir(previous)
        self.assertTrue(str(expected['figures']).replace('\\','/').endswith('output/01_results_and_figures/compare'))
        self.assertTrue(str(expected['data']).replace('\\','/').endswith('output/02_run_level_data/compare'))

    def test_current_receipt_bound_to_selected_dataset_and_all_artifacts(self):
        from independent_n_campaign import _sha as payload_hash
        with scratch_directory() as tmp:
            root=Path(tmp);out=root/'analysis';out.mkdir();data=root/'export';data.mkdir()
            (out/'fit').mkdir();fig=root/'figures';fig.mkdir()
            target,selected,maps,repeats,fits=fixture()
            c.write_table(out/'pub_source_manifest.csv',maps)
            c.write_table(out/'fit/per_repeat.csv',fits)
            c.write_table(data/'per_repeat.csv',repeats)
            am=dict(target_id=target.target_id,target_n=target.target_n,target_manifest_sha256=target.sha256,
                    input_hashes=target.input_hashes,source_run='run_02',backend='ibm_fez',dataset_status='COMPLETE',
                    validated_pubs_by_treatment={t:5 for t in c.TREATMENTS},canonical_combined_analysis='fit')
            (out/'analysis_manifest.json').write_text(json.dumps(am))
            em=dict(target_id=target.target_id,experiment_id=target.experiment_id,n=5,input_hashes=target.input_hashes,
                    source_run='run_02',partial_diagnostic_only=False,figure_directory=str(fig),
                    source_analysis_manifest_sha256=c._sha(out/'analysis_manifest.json'),
                    csv_sha256={'per_repeat.csv':c._sha(data/'per_repeat.csv')},figures={})
            ep=data/'export_manifest.json';ep.write_text(json.dumps(em));(fig/'export_manifest.json').write_text(json.dumps(em))
            receipt=dict(n=5,target_id=target.target_id,input_hashes=target.input_hashes,target_manifest_sha256=target.sha256,
                         dataset_sha256=payload_hash(sorted(selected,key=lambda r:(r['treatment_id'],r['logical_pub_index']))),
                         output=str(out),artifact_sha256=c.artifact_hashes(out))
            rp=root/'receipt.json';rp.write_text(json.dumps(receipt))
            # This unit isolates receipt/source mapping. Real PNG contracts are tested separately.
            with patch.object(c,'_export_pair_matches',return_value=True):
                rows,_,_=c.verify_analysis(target,selected,out,ep,rp)
                self.assertEqual(len(rows),40)
                receipt['dataset_sha256']='wrong';rp.write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError,'receipt provenance'):c.verify_analysis(target,selected,out,ep,rp)
                receipt['dataset_sha256']=payload_hash(sorted(selected,key=lambda r:(r['treatment_id'],r['logical_pub_index'])))
                rp.write_text(json.dumps(receipt));(out/'pub_source_manifest.csv').write_text('changed')
                with self.assertRaisesRegex(ValueError,'SHA-256'):c.verify_analysis(target,selected,out,ep,rp)

    def test_blocked_preflight_cannot_publish(self):
        with patch.object(c,'inspect_campaign',return_value={'ready':False,'blockers':['missing artifact']}):
            with self.assertRaisesRegex(ValueError,'missing artifact'):c.publish(Path.cwd(),'fixture',authorize_output_write=True)

    def test_no_network_in_statistics_or_join(self):
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
            c.summarize(observations())

    def test_versioned_publication_and_interruption_with_fixture_evidence(self):
        from repository_paths import comparison_output_paths
        with scratch_directory() as tmp:
            root=Path(tmp);(root/'02_Hardware/04_code').mkdir(parents=True)
            source=root/'protected.txt';source.write_text('immutable fixture');rows=observations()
            for r in rows:r['job_id']+=f"-n{r['source_n']}"
            evidence=dict(ready=True,blockers=[],observations=rows,observations_sha256=d.validate_observations(rows),
                          protected_hashes={str(source):c._sha(source)},verified_usage=[])
            audit=dict(protected_hashes={},references={'verified':True},blockers={})
            def render_c(**kwargs):
                records=[]
                for t in c.TREATMENTS:
                    values=[r['SSE'] for r in rows if r['treatment']==t]
                    records.extend(dict(treatment=t,cumulative_pub_count=k,median_SSE=c.statistics.median(values[:k])) for k in range(5,106))
                c.write_table(kwargs['table_dir']/'convergence_105_statistics.csv',records)
                (kwargs['figure_dir']/c.FIGURE_NAMES[0]).write_bytes(b'fixture image C')
                return records
            def render_d(**kwargs):
                records=[dict(treatment=t,median_SSE=c.statistics.median(r['SSE'] for r in rows if r['treatment']==t)) for t in c.TREATMENTS]
                c.write_table(kwargs['table_dir']/'triple_positioning_105.csv',records)
                (kwargs['figure_dir']/c.FIGURE_NAMES[1]).write_bytes(b'fixture image D')
                return records
            def render_ef(result,**kwargs):
                c.write_table(kwargs['table_dir']/'M0_matched_n_sensitivity_statistics.csv',result['statistics'])
                for name in c.FIGURE_NAMES[2:]:(kwargs['figure_dir']/name).write_bytes(b'fixture image EF')
                return result['statistics']
            with patch.object(c,'inspect_campaign',return_value=evidence),patch.object(c,'validated_reference_values',return_value=audit), \
                 patch.object(d,'generate_figure_c',side_effect=render_c),patch.object(c,'inspect_png'), \
                 patch.object(d,'generate_figure_d',side_effect=render_d),patch.object(d,'generate_figures_ef',side_effect=render_ef), \
                 patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
                result=c.publish(root,'fixture',authorize_output_write=True)
                self.assertEqual(result['state'],'COMPLETE');self.assertEqual(result['version'],1)
                paths=comparison_output_paths(root)
                self.assertFalse((root/'02_Hardware/04_code/results').exists())
                self.assertTrue(all(p.suffix=='.png' for p in paths['figures'].iterdir()))
                self.assertTrue(all(p.suffix in ('.csv','.json') for p in paths['data'].iterdir()))
                manifest=c.read_json(result['manifest']);c.verify_snapshot(manifest['artifacts_sha256'])
                published=c.published_comparison(root)
                self.assertEqual(len(published['figures']),4);self.assertEqual(len(published['summary']),56)
                with self.assertRaises(ValueError):c.published_artifact(root,'../protected.txt')
                with patch.object(d,'generate_figure_c',side_effect=AssertionError('C must be reused')):
                    second=c.publish(root,'fixture',authorize_output_write=True)
                self.assertEqual(second['version'],2);c.verify_snapshot(manifest['artifacts_sha256'])
                self.assertFalse((paths['figures']/'Figure_C_105_PUB_convergence__v002.png').exists())
                with patch.object(d,'generate_figure_d',side_effect=OSError('fixture interruption')):
                    with self.assertRaisesRegex(OSError,'interruption'):c.publish(root,'fixture',authorize_output_write=True)
                self.assertFalse((paths['data']/'publication_v003.json').exists())
                self.assertEqual(c.published_comparison(root)['version'],2)
                (paths['data']/'comparison_summary__v002.csv').write_text('tampered')
                with self.assertRaisesRegex(ValueError,'SHA-256'):c.published_comparison(root)
                c.verify_snapshot(evidence['protected_hashes'])

    def test_historical_optional_cost_contract_still_requires_every_hash(self):
        with scratch_directory() as tmp:
            root=Path(tmp);data=root/'data';data.mkdir();fig=root/'fig';fig.mkdir()
            names=list(c.PAPER_FIGURES+c.WIDE_FIGURES+c.OPTIONAL_FIGURES)
            for name in c.CSV_FILES:(data/name).write_bytes(b'fixture csv')
            for name in names:(fig/name).write_bytes(b'fixture png')
            em=dict(schema_version=3,source_analysis_manifest_sha256='source',generated_figures=names,
                    figures={name:{'sha256':c._sha(fig/name)} for name in names},
                    csv_sha256={name:c._sha(data/name) for name in c.CSV_FILES},
                    qpu_usage_coverage={'complete_comparable':True},omitted_optional_figures=[],
                    figure_directory=str(fig),data_directory=str(data))
            for path in (data/'export_manifest.json',fig/'export_manifest.json'):path.write_text(json.dumps(em))
            with patch.object(c,'_export_pair_matches',return_value=False),patch.object(c,'inspect_png') as png:
                self.assertTrue(c.verified_export_pair(fig,data,'source'));self.assertEqual(png.call_count,len(names))
                (fig/c.OPTIONAL_FIGURES[0]).unlink()
                self.assertFalse(c.verified_export_pair(fig,data,'source'))

    def test_no_missing_scientific_exports_api_references(self):
        import ast
        tree=ast.parse(Path(d.__file__).read_text())
        used={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)
              and isinstance(n.value,ast.Name) and n.value.id=='exports'}
        self.assertEqual(used,set())

    def test_figures_600dpi_immutable_and_descriptive(self):
        with scratch_directory() as tmp:
            root=Path(tmp);rows=observations()
            refs=dict(verified=True,classical_SSE=1000.,ideal_SSE=1500.,
                      measured_M0_median_SSE=c.statistics.median(r['SSE'] for r in rows if r['treatment']==c.TREATMENTS[0]))
            d.generate_figure_c(rows=rows,figure_dir=root,table_dir=root)
            d.generate_figure_d(rows=rows,figure_dir=root,table_dir=root,references=refs)
            d.generate_cross_n_figure(c.summarize(rows),root)
            hashes={str(p):c._sha(p) for p in root.glob('*.png')}
            self.assertEqual(len(hashes),3)
            for p in hashes:c.inspect_png(p,layout='landscape')
            with self.assertRaises(FileExistsError):d.generate_figure_d(rows=rows,figure_dir=root,table_dir=root,references=refs)
            c.verify_snapshot(hashes)
            for p in root.glob('*.csv'):self.assertIn('CI_',p.read_text())
            with self.assertRaisesRegex(ValueError,'verified data-derived'):d.generate_figure_d(rows=rows,figure_dir=root,table_dir=root)


if __name__=='__main__':unittest.main()
