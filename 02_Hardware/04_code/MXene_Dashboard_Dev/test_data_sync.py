"""Isolated local revisions, snapshot refresh and provider/collection separation."""
import json,sqlite3,unittest,uuid,shutil
from pathlib import Path
from contextlib import closing
from unittest.mock import patch,Mock
import reader
from campaign_progress import group_batches,recorded_step_observations

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.root=reader.HERE/'state'/('sync-fixture-'+uuid.uuid4().hex)
        (self.root/'state').mkdir(parents=True)
        self.source=self.root/'registry.sqlite3';self.out=self.root/'output'
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute('CREATE TABLE evidence(accepted INTEGER)');db.execute('INSERT INTO evidence VALUES(0)')
        self.addCleanup(lambda:shutil.rmtree(self.root))
        for name,value in [('HERE',self.root),('OUT',self.out),('DB',self.source)]:
            p=patch.object(reader,name,value);p.start();self.addCleanup(p.stop)
        self.r=reader.Reader()
        def project(path):
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                accepted=db.execute('SELECT accepted FROM evidence').fetchone()[0]
            return {'valid':accepted,'execution_jobs':[],'levels':[]}
        self.r.project=Mock(side_effect=project);self.r.catalog=Mock(return_value=[])

    def test_registry_change_invalidates_without_ttl_or_restart(self):
        first=self.r.status();self.assertEqual(first['valid'],0)
        self.assertIs(self.r.status(),first);self.r.project.assert_called_once()
        with closing(sqlite3.connect(self.source)) as db,db:db.execute('UPDATE evidence SET accepted=40')
        after=self.r.status();self.assertEqual(after['valid'],40)
        self.assertNotEqual(first['revision'],after['revision']);self.assertEqual(self.r.project.call_count,2)

    def test_terminal_step_analysis_export_and_png_changes_invalidate(self):
        before=self.r.status()['revision']
        for path,body in [(self.root/'state/step-fixture.json','{"control_state":"STEP_COMPLETED"}'),
            (self.out/'03_IBM_execution_records/ai_assistant/independent_n_campaigns'/reader.CAMPAIGN/'steps/a25-fixture.json','{}'),
            (self.out/'01_results_and_figures/n=25/export_manifest.json','{}'),
            (self.out/'01_results_and_figures/n=25/06_mitigation_three_tier_positioning.png','fixture')]:
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
            current=self.r.status()['revision'];self.assertNotEqual(before,current);before=current
        self.assertEqual(self.r.project.call_count,5)

    def test_force_refresh_bypasses_cache_without_source_writes(self):
        before=reader.sha(self.source)
        self.r.status();self.r.status(force=True)
        self.assertEqual(self.r.project.call_count,2);self.assertEqual(reader.sha(self.source),before)
        self.assertFalse((self.out/'worker.lock').exists())

    def test_all_views_share_snapshot_and_revision(self):
        with patch('campaign_progress.read_progress',return_value={'levels':[]}) as progress,patch('execution_records.historical',return_value={'rows':[]}) as records:
            bundle=self.r.bundle()
        self.assertIs(progress.call_args.kwargs['snapshot'],bundle['snapshot'])
        self.assertIs(records.call_args.args[0],bundle['snapshot'])
        self.assertEqual({bundle[k]['revision'] for k in ('snapshot','progress','records')},{bundle['revision']})
        self.assertEqual(progress.call_args.kwargs['database'],self.root/'state/snapshot.sqlite3')

    def test_provider_done_does_not_collect_or_accept(self):
        jobs=[{'job_id':str(i),'target_n':30,'status':'RUNNING','observed_at':'2026-01-01T00:00:00Z','block':'b6','recovery':'Original','treatment_ids':[t],'collected_pubs':0,'updated_at':'fixture'} for i,t in enumerate(reader.TREATMENTS)]
        block={'logical_block_id':'b6','sequence_no':6,'chunk_n':5,'updated_at':'fixture'}
        original=group_batches({},[block],jobs,[],[])[0]
        self.assertFalse(original['all_original_jobs_recorded_done'])
        steps=[{'campaign':reader.CAMPAIGN,'n':30,'rows':[{'provider_statuses':{str(i):'DONE' for i in range(8)},'provider_checked_at':'2026-02-01T00:00:00Z'}]}]
        checked=recorded_step_observations(jobs,steps,reader.CAMPAIGN)
        current=group_batches({},[block],checked,[],[])[0]
        self.assertEqual(current['local_execution'],{'RUNNING':8})
        self.assertEqual(current['last_verified_provider_execution'],{'DONE':8})
        self.assertEqual(current['validated_pubs'],0);self.assertEqual(current['collection'],'Uncollected')
        self.assertIn('completed; collection required',current['collection_message'])
        self.assertEqual(jobs[0]['status'],'RUNNING')

    def test_seven_done_is_not_completed_batch(self):
        jobs=[{'job_id':str(i),'block':'b','recovery':'Original','treatment_ids':[t],'collected_pubs':0,'updated_at':'fixture','status':'DONE' if i<7 else 'RUNNING'} for i,t in enumerate(reader.TREATMENTS)]
        b=group_batches({},[{'logical_block_id':'b','sequence_no':1,'chunk_n':5,'updated_at':'fixture'}],jobs,[],[])[0]
        self.assertFalse(b['all_original_jobs_recorded_done']);self.assertIn('not verified',b['collection_message'])

    def test_older_provider_receipt_does_not_override_newer_local_observation(self):
        jobs=[{'job_id':'job','target_n':30,'status':'FAILED','observed_at':'2026-03-01T00:00:00Z'}]
        steps=[{'campaign':reader.CAMPAIGN,'n':30,'rows':[{'provider_statuses':{'job':'RUNNING'},'provider_checked_at':'2026-02-01T00:00:00Z'}]}]
        result=recorded_step_observations(jobs,steps,reader.CAMPAIGN)[0]
        self.assertEqual(result['status'],'FAILED');self.assertEqual(result['local_status'],'FAILED')
        self.assertEqual(result['last_verified_provider_status'],'RUNNING')
        self.assertEqual(result['last_verified_provider_at'],'2026-02-01T00:00:00Z')

    def test_read_only_sync_endpoint_never_contacts_provider(self):
        import app
        from fastapi.testclient import TestClient
        with patch.object(app,'reader',self.r),patch('campaign_progress.read_progress',return_value={}),patch('execution_records.historical',return_value={}),patch('broker_client.call',side_effect=AssertionError('No broker')):
            client=TestClient(app.app,base_url='http://127.0.0.1')
            self.assertFalse(client.get('/api/data-revision').json()['provider_queried'])
            result=client.get('/api/dashboard-data?fresh=true')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['snapshot']['valid'],0)
