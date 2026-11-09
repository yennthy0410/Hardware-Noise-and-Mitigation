import unittest,json,uuid,shutil,hashlib
from pathlib import Path
from unittest.mock import patch
from execution_records import historical
from test_jobs import JobsTests

class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).parent/'state'/('records-test-'+uuid.uuid4().hex)
        self.root.mkdir();self.execution=self.root/'03_IBM_execution_records'
        self.folder=self.execution/'replicate_block_example'/'M0_Baseline';self.folder.mkdir(parents=True)
        (self.root/'04_runtime_logs').mkdir()
        self.row=dict(key='one',target_n=30,job_id='job',account='labK',backend='ibm_fez',batch_id='batch',block='campaign-n30-block-005',recovery='Original')
        self.metadata=dict(job_id='job',account_label='labK',backend='ibm_fez',batch_id='batch',logical_block_id='campaign-n30-block-005',status='DONE',metrics_raw={'timestamps':{'running':'2026-01-01T00:00:00Z','finished':'2026-01-01T00:01:00Z'},'usage':{'qpu_charge_time_seconds':8,'value':999,'unit':'resource_units'}},api_token='MUST_NOT_APPEAR')
        self.file=self.folder/'job_retrieved_metadata.json';self.file.write_text(json.dumps(self.metadata))
    def tearDown(self):shutil.rmtree(self.root)
    def result(self):return historical({'execution_jobs':[self.row]},self.root)
    def test_link_units_and_readonly(self):
        before=self.file.read_bytes();d=self.result();r=d['rows'][0]
        self.assertEqual(r['batch_sequence'],5);self.assertEqual(r['saved_runtime'][0]['qpu_usage_seconds'],8)
        self.assertEqual(r['saved_runtime'][0]['elapsed_seconds'],60)
        self.assertNotIn('MUST_NOT_APPEAR',json.dumps(d));self.assertEqual(before,self.file.read_bytes())
    def test_malformed_optional_metrics(self):
        self.metadata['metrics_raw']={'timestamps':[], 'usage':'not recorded'}
        self.file.write_text(json.dumps(self.metadata))
        saved=self.result()['rows'][0]['saved_runtime'][0]
        self.assertIsNone(saved['qpu_usage_seconds']);self.assertIsNone(saved['elapsed_seconds'])
    def test_registered_sequence_precedes_name_and_preserves_identifiers(self):
        self.row['batch_sequence']=3
        first=self.result()['rows'][0];second=self.result()['rows'][0]
        self.assertEqual(first['batch_label'],'Batch 003');self.assertEqual(first['batch_label'],second['batch_label'])
        self.assertEqual(first['job_id'],'job');self.assertEqual(first['batch_id'],'batch')
        self.assertEqual(first['batch_sequence_source'],'Registered experiment_blocks.sequence_no')
    def test_arbitrary_block_does_not_infer_number_from_n(self):
        self.row['block']='unregistered-name';self.metadata['logical_block_id']='unregistered-name'
        self.file.write_text(json.dumps(self.metadata))
        self.assertEqual(self.result()['rows'][0]['batch_label'],'Unnumbered execution')
    def test_exact_identity(self):
        for field in ('job_id','account','backend','batch_id','block'):
            original=self.row[field];self.row[field]='wrong';self.assertEqual(self.result()['rows'][0]['saved_runtime'],[]);self.row[field]=original
    def test_missing_usage_is_not_queue_or_resource_units(self):
        del self.metadata['metrics_raw']['usage']['qpu_charge_time_seconds'];self.file.write_text(json.dumps(self.metadata))
        self.assertIsNone(self.result()['rows'][0]['saved_runtime'][0]['qpu_usage_seconds'])
    def test_runtime_folder_mapping_and_empty(self):
        self.assertEqual(self.result()['runtime_folder_entries'],0)
        p=self.root/'04_runtime_logs'/'job_retrieved_metadata.json';p.write_text(json.dumps(self.metadata));self.file.unlink()
        self.assertEqual(len(self.result()['rows'][0]['saved_runtime']),1)
    def test_original_replacement_preserved(self):
        s={'execution_jobs':[self.row,dict(self.row,key='replacement',job_id='replacement',recovery='Replacement')]}
        self.assertEqual([r['recovery'] for r in historical(s,self.root)['rows']],['Original','Replacement'])
    def test_http_contract(self):
        from fastapi.testclient import TestClient
        import app
        with patch.object(app.reader,'bundle',return_value={'records':historical({'execution_jobs':[self.row]},self.root)}):
            response=TestClient(app.app,base_url="http://127.0.0.1").get('/api/execution-records')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['rows'][0]['job_id'],'job')
    def test_one_row_per_job_and_conflicting_metadata(self):
        self.row['runtime_seconds']=12
        data=historical({'execution_jobs':[self.row,dict(self.row)]},self.root)
        self.assertEqual(len(data['rows']),1);self.assertIsNone(data['rows'][0]['runtime_seconds'])
        self.assertTrue(data['rows'][0]['record_conflict'])
        import execution_records
        self.assertTrue(execution_records.AUDIT['jobs']['job']['conflicts'])
    def test_equal_integer_float_usage_not_conflict(self):
        self.row['runtime_seconds']=8.0
        self.assertFalse(self.result()['rows'][0]['runtime_evidence_conflict'])
    def test_timestamp_normalization_and_original_preservation(self):
        self.metadata['submitted_client_utc']='2026-01-01T08:00:00+08:00'
        self.file.write_text(json.dumps(self.metadata))
        row=self.result()['rows'][0]
        self.assertEqual(row['submitted'],'2026-01-01T00:00:00Z')
        self.assertEqual(row['saved_runtime'][0]['submitted'],'2026-01-01T08:00:00+08:00')
    def test_relative_replacement_archive_and_parent(self):
        folder=self.execution/'ai_assistant/replacements/target/1';folder.mkdir(parents=True)
        raw=folder/'raw_result.json';data=dict(self.metadata,replacement_job_id='job',original_source_job_id='parent',submitted_utc='2026-01-01T01:00:00Z')
        raw.write_text(json.dumps(data));digest=hashlib.sha256(raw.read_bytes()).hexdigest()
        self.file.unlink()
        self.row['recorded_evidence']=[{'source':'replacement:1','raw_archive_path':'02_Hardware/01_vqe/output/03_IBM_execution_records/ai_assistant/replacements/target/1/raw_result.json','raw_archive_sha256':digest}]
        row=self.result()['rows'][0];self.assertEqual(row['parent_job_id'],'parent');self.assertEqual(row['runtime_seconds'],8)
        raw.write_text('{}');row=self.result()['rows'][0];self.assertTrue(row['record_conflict']);self.assertIsNone(row['runtime_seconds'])
    def test_missing_and_invalid_do_not_include_pending(self):
        self.row.update(expected_pubs=5,invalid_pubs=2,validation=[{'pub_index':1,'expectation_valid':0,'uncertainty_valid':None,'error_codes_json':'["MISSING_PUB_RESULT"]'},{'pub_index':2,'expectation_valid':1,'uncertainty_valid':None,'error_codes_json':'["UNCERTAINTY_MISSING"]'}])
        row=self.result()['rows'][0];self.assertEqual((row['invalid_pubs'],row['missing_pubs']),(1,1))
        self.row['validation']=[];self.assertIsNone(self.result()['rows'][0]['missing_pubs'])
    def test_undated_status_conflict_is_not_silently_selected(self):
        self.row['status']='RUNNING';row=self.result()['rows'][0]
        self.assertIsNone(row['status']);self.assertTrue(row['record_conflict'])
    def test_target_n_is_not_chunk_n(self):
        self.metadata['n']=5;self.file.write_text(json.dumps(self.metadata))
        self.assertEqual(self.result()['rows'][0]['target_n'],30)
    def test_navigation_and_no_polling(self):
        static=Path(__file__).parent/'static'
        self.assertIn("'IBM Execution & Runtime Records'",(static/'app.js').read_text())
        js=(static/'records.js').read_text();self.assertNotIn('setInterval',js);self.assertNotIn('/api/control',js)
        self.assertIn('recordsData?.rows',js);self.assertIn('slice(recordsPage*25',js)
        self.assertIn('synchronizeDashboard(false)',(static/'app.js').read_text())
        sync=(static/'data_sync.js').read_text()
        self.assertIn('/api/data-revision',sync);self.assertNotIn('/api/manual',sync);self.assertNotIn('/dispatch',sync)

if __name__=='__main__':unittest.main()
