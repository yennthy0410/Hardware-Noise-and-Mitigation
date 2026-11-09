"""Read-only projections and publication verification against isolated fixtures."""
import json,sqlite3,struct,sys,unittest,uuid,shutil,subprocess
from contextlib import closing
from unittest.mock import patch,Mock
import campaign_progress as p
sys.path.insert(0,str(p.PROD/'03_python_automation'))
from research_pipeline import _step_artifact_hashes
from independent_n_campaign import _sha


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.root=p.HERE/'state'/('progress-fixture-'+uuid.uuid4().hex);self.root.mkdir()
        self.addCleanup(lambda:shutil.rmtree(self.root))
        self.level={'target_n':5,'target_id':'target5','manifest_sha256':'target-hash','input_hashes':{'qpy':'frozen'}}
        self.accepted=[{'treatment_id':t,'logical_pub_index':i,'source':'ORIGINAL'} for t in p.TREATMENTS for i in range(1,6)]
        self.blocks=[{'logical_block_id':'b1','sequence_no':1,'chunk_n':5,'updated_at':'recorded-time'}]
        self.jobs=[{'block':'b1','job_id':str(i),'recovery':'Original','treatment_ids':[t],'collected_pubs':5,'updated_at':'recorded-time','status':'DONE'} for i,t in enumerate(p.TREATMENTS)]
        self.steps=[]

    def output(self):return p.verify_outputs(self.root,p.CAMPAIGN,self.level,self.accepted,self.steps)
    def group(self,validation=()):return p.group_batches(self.level,self.blocks,self.jobs,self.accepted,validation)[0]
    def analyze(self):
        source=self.root/'02_run_level_data/adaptive_targets/target5';source.mkdir(parents=True)
        (source/'analysis_manifest.json').write_text('{}');(source/'values.csv').write_text('value\n1')
        directory=self.root/'03_IBM_execution_records/ai_assistant/independent_n_campaigns'/p.CAMPAIGN/'steps';directory.mkdir(parents=True)
        receipt=directory/'a5-fixture.json'
        body={'campaign_id':p.CAMPAIGN,'n':5,'target_id':'target5','target_manifest_sha256':'target-hash','input_hashes':{'qpy':'frozen'},
            'dataset_sha256':_sha(sorted(self.accepted,key=lambda r:(r['treatment_id'],r['logical_pub_index']))),'output':str(source),'artifact_sha256':_step_artifact_hashes(source)}
        receipt.write_text(json.dumps(body));return source,receipt
    def export(self):
        source,receipt=self.analyze();fig=self.root/'01_results_and_figures/n=5';data=self.root/'02_run_level_data/n=5'
        fig.mkdir(parents=True);data.mkdir(parents=True)
        def chunk(kind,value):return struct.pack('>I4s',len(value),kind)+value+b'\0'*4
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>II',2000,2000))+chunk(b'pHYs',struct.pack('>IIB',23622,23622,1))+chunk(b'IDAT',b'')
        for name in p.PNG_NAMES:(fig/name).write_bytes(png)
        (data/'values.csv').write_text('value\n1')
        manifest={'target_id':'target5','n':5,'input_hashes':{'qpy':'frozen'},'partial_diagnostic_only':False,
            'source_analysis_manifest_sha256':p.sha(source/'analysis_manifest.json'),'figure_directory':str(fig),'data_directory':str(data),
            'generated_figures':sorted(p.PNG_NAMES),'figures':{name:{'sha256':p.sha(fig/name)} for name in p.PNG_NAMES},'csv_sha256':{'values.csv':p.sha(data/'values.csv')}}
        for parent in (fig,data):(parent/'export_manifest.json').write_text(json.dumps(manifest))
        self.steps=[{'campaign':p.CAMPAIGN,'n':5,'operation':'export','status':'STEP_COMPLETED','rows':[{'state':'EXPORTED','files_verified':True,'analysis_receipt':{'sha256':p.sha(receipt)},'figure_directory':str(fig),'data_directory':str(data)}]}]
        return fig,data

    def test_unique_slots_and_replacement_lineage(self):
        self.accepted.append(dict(self.accepted[0],source='REPLACEMENT'))
        validation=[{'logical_block_id':'b1','treatment_id':p.TREATMENTS[0],'pub_index':1,'expectation_valid':0,'uncertainty_valid':0}]
        result=self.group(validation)
        self.assertEqual(result['validated_pubs'],40);self.assertEqual(result['invalid_pubs'],0)
        self.assertEqual(result['recovery'],'Repaired')

    def test_done_is_not_collection_and_partial(self):
        for job in self.jobs:job['collected_pubs']=None
        self.assertEqual(self.group()['collection'],'Uncollected')
        self.jobs[0]['collected_pubs']=5
        self.assertEqual(self.group()['collection'],'Partially collected')

    def test_batch_grouping_and_missing_invalid(self):
        self.blocks.append(dict(self.blocks[0],logical_block_id='b2',sequence_no=2))
        self.accepted.pop(0)
        validation=[{'logical_block_id':'b1','treatment_id':p.TREATMENTS[0],'pub_index':1,'expectation_valid':0,'uncertainty_valid':None}]
        rows=p.group_batches(self.level,self.blocks,self.jobs,self.accepted,validation)
        self.assertEqual((rows[0]['invalid_pubs'],rows[0]['missing_pubs']),(1,0))
        self.assertEqual((rows[1]['validated_pubs'],rows[1]['missing_pubs']),(0,40))

    def test_duplicate_original_does_not_look_fully_collected(self):
        self.jobs.append(dict(self.jobs[0],job_id='conflicting'))
        self.assertNotEqual(self.group()['collection'],'Collected')

    def test_step_observations_are_scoped_historical_and_never_collection(self):
        jobs=[dict(self.jobs[0],target_n=5,status='RUNNING',collected_pubs=None,observed_at='2026-01-01T00:00:00+00:00')]
        steps=[{'campaign':p.CAMPAIGN,'n':5,'rows':[{'provider_statuses':{'0':'DONE'},'provider_checked_at':'2026-02-01T00:00:00+00:00'}]},
            {'campaign':p.CAMPAIGN,'n':30,'rows':[{'provider_statuses':{'0':'FAILED'},'provider_checked_at':'2026-03-01T00:00:00+00:00'}]}]
        result=p.recorded_step_observations(jobs,steps,p.CAMPAIGN)
        self.assertEqual(result[0]['status'],'DONE');self.assertIsNone(result[0]['collected_pubs'])
        self.assertEqual(result[0]['observation_source'],'Recorded STEP receipt')
        self.assertEqual(jobs[0]['status'],'RUNNING')

    def test_analysis_receipt_and_actual_artifacts(self):
        self.assertEqual(self.output()['analyze'],'No STEP receipt')
        source,_=self.analyze();self.assertEqual(self.output()['analyze'],'Verified')
        (source/'analysis_manifest.json').unlink()
        self.assertEqual(self.output()['analyze'],'Verification required')

    def test_changed_dataset_invalidates_analysis(self):
        self.analyze();self.accepted[0]['source']='REPLACEMENT'
        self.assertEqual(self.output()['analyze'],'Verification required')

    def test_export_verifies_receipt_hash_manifest_and_png_count(self):
        fig,_=self.export();self.assertEqual(self.output()['verified_pngs'],10)
        (fig/'nyquist.png').unlink()
        self.assertEqual(self.output()['export'],'Verification required')
        self.assertEqual(self.output()['verified_pngs'],0)

    def test_export_missing_receipt_or_changed_png(self):
        fig,_=self.export();steps=self.steps;self.steps=[]
        self.assertEqual(self.output()['export'],'Verification required')
        self.steps=steps;(fig/'nyquist.png').write_bytes(b'changed')
        self.assertEqual(self.output()['export'],'Verification required')

    def test_legacy_export_report_uses_verified_manifest_destination(self):
        self.export();row=self.steps[0]['rows'][0]
        del row['figure_directory'];del row['data_directory']
        self.assertEqual(self.output()['verified_pngs'],10)
        row['figure_directory']='conflicting-path'
        self.assertEqual(self.output()['export'],'Verification required')

    def test_export_wrong_dpi_or_conflicting_manifest(self):
        fig,data=self.export();path=fig/'nyquist.png';path.write_bytes(path.read_bytes().replace(struct.pack('>I',23622),struct.pack('>I',11811)))
        for directory in (fig,data):
            mp=directory/'export_manifest.json';m=json.loads(mp.read_text());m['figures']['nyquist.png']['sha256']=p.sha(path);mp.write_text(json.dumps(m))
        self.assertIn('600 DPI',self.output()['verification_reason'])
        (fig/'export_manifest.json').write_text('{}')
        self.assertIn('conflicts',self.output()['verification_reason'])

    def test_readonly_progress_six_levels_and_no_provider(self):
        import job_registry
        database=self.root/'registry.sqlite3'
        with closing(sqlite3.connect(database)) as db,db:
            db.executescript('CREATE TABLE independent_n_campaigns(campaign_id,state,updated_at);CREATE TABLE experiment_targets(target_id,manifest_sha256,input_hashes_json);')
            db.execute('INSERT INTO independent_n_campaigns VALUES(?,?,?)',(p.CAMPAIGN,'PAUSED','fixture-time'))
            db.execute('INSERT INTO experiment_targets VALUES(?,?,?)',('target5','target-hash','{"qpy":"frozen"}'))
        registry=job_registry.JobRegistry(database,readonly=True)
        registry.target_status=Mock(return_value={'blocks':self.blocks})
        registry.repaired_pub_values=Mock(return_value=self.accepted+self.accepted[:1])
        registry.pub_validation_records=Mock(return_value=[])
        snapshot={'levels':[dict(self.level,invalid=0,missing=0)],'execution_jobs':[dict(j,target_n=5,observed_at='old-observation') for j in self.jobs]}
        before=p.sha(database)
        with patch.object(job_registry,'JobRegistry',return_value=registry) as constructor,patch.object(p.Reader,'project',return_value=snapshot),patch('broker_client.call',side_effect=AssertionError('No broker calls')):
            report=p.read_progress(database,self.root,self.root)
        self.assertEqual(report['accepted'],40);self.assertEqual(report['expected'],840)
        self.assertEqual([l['n'] for l in report['levels']],[5,10,15,20,25,30])
        self.assertFalse(report['provider_queried']);self.assertTrue(report['read_only'])
        self.assertEqual(report['levels'][0]['provider_observed_at'],'old-observation')
        self.assertFalse(report['levels'][0]['complete']);self.assertEqual(p.sha(database),before)
        self.assertEqual(constructor.call_args.kwargs,{'readonly':True})
        self.assertFalse((self.root/'worker.lock').exists())
        self.assertFalse((self.root/'worker_control.sqlite3').exists())

    def test_output_reader_works_inside_unchanged_dashboard_sandbox(self):
        self.export()
        config=self.root/'verification.json'
        config.write_text(json.dumps({'out':str(self.root),'level':self.level,'accepted':self.accepted,'steps':self.steps}))
        script="""import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from security import install
install()
from reader import PROD,CAMPAIGN
sys.path.insert(0,str(PROD/'03_python_automation'))
from campaign_progress import verify_outputs
c=json.loads(Path(sys.argv[2]).read_text())
r=verify_outputs(Path(c['out']),CAMPAIGN,c['level'],c['accepted'],c['steps'])
assert r['verified_pngs']==10,r
assert not any(k.startswith('qiskit') for k in sys.modules)
print('sandbox verification passed')
"""
        before={str(f):p.sha(f) for f in self.root.rglob('*') if f.is_file()}
        result=subprocess.run([sys.executable,'-B','-c',script,str(p.HERE),str(config)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(before,{str(f):p.sha(f) for f in self.root.rglob('*') if f.is_file()})

    def test_progress_http_never_calls_broker_or_supervisor(self):
        import app
        from fastapi.testclient import TestClient
        with patch.object(app.reader,'bundle',return_value={'progress':{'read_only':True,'provider_queried':False}}),patch.object(app.supervisor,'status',side_effect=AssertionError('No worker probe')),patch('broker_client.call',side_effect=AssertionError('No broker')):
            client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            response=client.get('/api/campaign-progress')
            self.assertEqual(response.status_code,200);self.assertTrue(response.json()['read_only'])

    def test_wrong_startup_cannot_silently_drop_relative_recovery_archives(self):
        with patch.object(p.Path,'cwd',return_value=p.HERE):
            with self.assertRaisesRegex(ValueError,'project working directory'):p.read_progress()
