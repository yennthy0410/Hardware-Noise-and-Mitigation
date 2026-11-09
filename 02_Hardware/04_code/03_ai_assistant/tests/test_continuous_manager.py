"""Unattended scheduling against offline providers and temporary SQLite only."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import test_batch_progression as fixtures
import independent_n_campaign as campaign
import vqe_adapter as adapter
from manager_exit_codes import live_exit_code,exit_outcome


class ContinuousManagerTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BatchProgressionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.directory=self.f.directory

    def run_cycles(self,effects,*,state=None,sleeper=None):
        registry=Mock();registry.independent_campaign_status.return_value=state or {
            'exists':True,'current_level_index':5,'state':'RUNNING','checkpoint':{}}
        sleeper=sleeper or Mock()
        with patch.object(campaign,'campaign_root',return_value=self.directory), \
             patch.object(campaign,'run_independent_campaign',side_effect=effects) as cycle, \
             patch.object(campaign,'inspect_independent_campaign',return_value={}):
            result=campaign.run_campaign_auto_manager(self.directory,'test',registry,
                authorize_live_campaign=True,resume=True,sleeper=sleeper)
        return result,registry,cycle,sleeper

    def test_consecutive_read_failures_back_off_and_resume_without_manual_flags(self):
        result,registry,cycle,sleep=self.run_cycles([TimeoutError('quota refresh')]*6+[{'state':'COMPLETE'}])
        self.assertEqual(result['state'],'COMPLETE')
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[60,120,240,480,900,900])
        self.assertEqual([c.kwargs['resume'] for c in cycle.call_args_list],[True]+[False]*6)
        self.assertTrue(all(c.kwargs['checkpoint']['pause_class']=='PAUSED_RETRYABLE'
                            for c in registry.checkpoint_independent_campaign.call_args_list))

    def test_resource_exhaustion_backoff_preserves_submission_gates(self):
        error=OSError('WinError 1450 accessing scratch');error.winerror=1450
        result,registry,cycle,sleep=self.run_cycles([error]*6+[{'state':'COMPLETE'}])
        self.assertEqual(result['state'],'COMPLETE')
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[60,120,240,480,900,900])
        self.assertTrue(all(c.kwargs['checkpoint']['failure_category']=='RESOURCE_WAIT'
                            and c.kwargs['checkpoint']['automatic_new_qpu_submission'] is False
                            for c in registry.checkpoint_independent_campaign.call_args_list))

    def test_sleep_interrupt_checkpoints_operator_pause(self):
        for effects in ([TimeoutError('read')],[{'state':'RUNNING'}]):
            result,registry,_,_=self.run_cycles(effects,sleeper=Mock(side_effect=KeyboardInterrupt))
            self.assertEqual(live_exit_code(result),20)
            self.assertEqual(registry.checkpoint_independent_campaign.call_args.kwargs['event_type'],'AUTO_MANAGER_INTERRUPTED')

    def test_operator_marker_during_retry_is_not_cleared_by_stale_resume(self):
        def pause(_):(self.directory/'.campaign.pause').touch()
        result,_,cycle,_=self.run_cycles([TimeoutError('read')],sleeper=pause)
        self.assertEqual(live_exit_code(result),20);self.assertEqual(cycle.call_count,1)
        self.assertTrue((self.directory/'.campaign.pause').exists())

    def test_stop_marker_is_clean_stop_without_provider_cycle(self):
        (self.directory/'.campaign.stop').touch()
        result,_,cycle,_=self.run_cycles([])
        self.assertEqual(live_exit_code(result),10);cycle.assert_not_called()

    def test_unknown_submission_overrides_network_retry(self):
        registry=self.f.registry;target=self.f.target(30)
        row=next(iter(self.f.reserve(target,1).values()))
        registry.transition(row,'SUBMITTING')
        current={'exists':True,'state':'RUNNING','current_level_index':5,
                 'levels':[{'target_id':target.target_id}],'checkpoint':{}}
        mock=Mock(wraps=registry);mock.independent_campaign_status.return_value=current
        mock.checkpoint_independent_campaign=Mock()
        sleep=Mock()
        with patch.object(campaign,'campaign_root',return_value=self.directory), \
             patch.object(campaign,'run_independent_campaign',side_effect=TimeoutError('submit response lost')) as cycle, \
             patch.object(campaign,'inspect_independent_campaign',return_value={}):
            result=campaign.run_campaign_auto_manager(self.directory,'c',mock,sleeper=sleep)
        self.assertEqual(live_exit_code(result),20);sleep.assert_not_called();cycle.assert_called_once()
        self.assertEqual(mock.checkpoint_independent_campaign.call_args.kwargs['checkpoint']['failure_category'],'SUBMISSION_AMBIGUITY')

    def test_contradiction_and_local_archive_failures_never_retry(self):
        for exc in (RuntimeError('contradictory provider evidence'),PermissionError('archive'),FileNotFoundError('frozen input')):
            result,_,cycle,sleep=self.run_cycles([exc])
            self.assertEqual(live_exit_code(result),20);sleep.assert_not_called();cycle.assert_called_once()

    def test_collection_continues_only_after_archived_invalid_slots(self):
        wrapped=RuntimeError('pilot validation failed');wrapped.__cause__=adapter.ArchivedInvalidPubs('slot 3 archived')
        with patch.object(adapter,'collect_block',side_effect=[wrapped,{}]) as collect:
            adapter.collect_block_for_recovery(None,'b',treatments=('M0','M1'))
        self.assertEqual([c.kwargs['treatments'] for c in collect.call_args_list],[('M0',),('M1',)])
        for error in (ValueError('unverified source'),ConnectionError('read'),OSError('disk')):
            with patch.object(adapter,'collect_block',side_effect=error) as collect:
                with self.assertRaises(type(error)):
                    adapter.collect_block_for_recovery(None,'b',treatments=('M0','M1'))
            collect.assert_called_once()

    def test_monitor_contradiction_beats_transient_failure(self):
        with self.assertRaisesRegex(RuntimeError,'reconciliation'):
            campaign._check_monitor_read({'batch_identity_verified':True,
                'jobs':[{'failure_class':'NETWORK'},{'failure_class':'BATCH_ID_MISMATCH'}]})
        with self.assertRaises(ConnectionError):
            campaign._check_monitor_read({'batch_identity_verified':True,'jobs':[{'failure_class':'NETWORK'}]})

    def test_continuous_n30_six_batches_and_partial_recovery_preserve_600(self):
        registry=self.f.registry
        levels=[dict(level_index=i,target_n=(i+1)*5,target_id=f'n{(i+1)*5}',experiment_id=f'n{(i+1)*5}') for i in range(6)]
        registry.register_independent_campaign(dict(campaign_id='offline',manifest_sha256='f'*64,
            levels=levels,source_run='run_02',calibration_data_excluded=True))
        for index in range(5):
            target=self.f.target((index+1)*5)
            for sequence in range(1,target.blocks_required+1):self.f.block(target,sequence)
            registry.complete_independent_level('offline',index)
        original={r['id']:r['job_id'] for r in registry.rows()}
        self.assertEqual(sum(len(registry.repaired_pub_values(l['target_id'])) for l in levels[:5]),600)
        target=self.f.target(30);cycle_no=0
        def execute(*args,**kwargs):
            nonlocal cycle_no
            cycle_no+=1
            missing=[(fixtures.TREATMENTS[0],5)] if cycle_no==1 else []
            _,block,_=self.f.block(target,cycle_no,missing)
            if missing:self.f.replacement(target,block,*missing[0])
            if cycle_no==6:registry.complete_independent_level('offline',5)
            return {'state':registry.independent_campaign_status('offline')['state']}
        with patch.object(campaign,'campaign_root',return_value=self.directory), \
             patch.object(campaign,'run_independent_campaign',side_effect=execute), \
             patch.object(campaign,'_finalize_level',side_effect=AssertionError('Offline only')):
            result=campaign.run_campaign_auto_manager(self.directory,'offline',registry,sleeper=Mock())
        self.assertEqual(result['state'],'COMPLETE');self.assertEqual(cycle_no,6)
        self.assertEqual(len(registry.repaired_pub_values('n30')),240)
        self.assertEqual(original,{r['id']:r['job_id'] for r in registry.rows() if r['id'] in original})

    def test_exit_codes_and_offline_dispatch(self):
        import mxene
        for state,code in [('COMPLETE',0),('STOPPED',10),('PAUSED',20),('BLOCKED_AUTHORIZATION',20)]:
            with patch('research_pipeline.run_pipeline',return_value={'pipeline':{'state':state}}):
                self.assertEqual(mxene.main(['run','--live','--confirm-live-submission']),code)
        self.assertEqual(exit_outcome(0xc0000005),'NATIVE_CRASH_OR_SIGNAL')
        with patch('research_pipeline.process_pipeline',return_value={}) as offline, \
             patch('research_pipeline.run_pipeline',side_effect=AssertionError('No live execution')):
            self.assertEqual(mxene.main(['offline','--n','30']),0)
            offline.assert_called_once()

    def test_terminal_partial_collection_reaches_planner_and_failed_replacement_retries(self):
        import test_manager_pause as pauses
        f=pauses.ManagerPauseTests();f.setUp();self.addCleanup(f.doCleanups)
        registry,state=f.run_setup(5)
        target=SimpleNamespace(target_n=30,target_id='n30',experiment_id='n30',
            treatment_ids=fixtures.TREATMENTS,input_hashes={})
        registry.target_status.return_value={'blocks':[{'logical_block_id':'n30-block-001'}]}
        registry.rows.return_value=[{'submission_state':'SUBMITTED','account_label':'labI',
            'metadata':{'instance':'original'}} for _ in range(8)]
        registry.latest_batch_recovery_review.return_value={'recovery_status':'INVESTIGATING'}
        replacement={'state':'SUBMITTED','replacement_id':1,'account_label':'labI','logical_block_id':'n30-block-001'}
        registry.replacement_jobs.side_effect=lambda *_:[replacement]
        def collect(*args):
            replacement['state']='FAILED'
            return {'replacement_id':1,'state':'FAILED'}
        service=Mock();service.active_instance.return_value='original'
        observed=f.observed();observed['block_status']='INCOMPLETE'
        observed['jobs'][0]['failure_class']='RESULT_CORRUPTION'
        auth=campaign.load_campaign_authorization(None,None,None,None)[0]
        config={'live_submission_enabled':True,'auto_recovery_enabled':True,'allowed_backend':'ibm_fez'}
        with patch('final_configuration.validate_final_config',side_effect=lambda x:x), \
             patch('final_configuration.campaign_authorization_from_final_config',return_value=auth), \
             patch.object(campaign,'_target_from_level',return_value=target), \
             patch.object(campaign,'_repaired_block_complete',return_value=False), \
             patch.object(campaign,'inspect_independent_campaign',return_value={'state':'RUNNING'}), \
             patch('read_only_preflight._instance_ref',return_value='ref'), \
             patch('batch_monitor.monitor_batch',return_value=observed), \
             patch('vqe_adapter.collect_block_for_recovery') as archive, \
             patch('targeted_recovery.plan_invalid_pub_slots',return_value={'blocked':[]}) as plan, \
             patch('targeted_recovery.collect_replacement',side_effect=collect), \
             patch('batch_scheduler.submit_runtime_block',side_effect=AssertionError('No new Batch')):
            result=campaign.run_independent_campaign(f.directory,'c',registry,authorize_live_campaign=True,
                once=True,resume=True,service_factory=lambda _:service,final_config=config)
        archive.assert_called_once();plan.assert_called_once()
        self.assertTrue(result['recovery_waiting']);self.assertEqual(replacement['state'],'FAILED')

    def test_preflight_network_failure_is_preserved_for_outer_retry(self):
        import test_provider_quota as providers
        f=providers.ProviderQuotaTests();f.setUp();self.addCleanup(f.doCleanups)
        service=f.service();service.usage.side_effect=TimeoutError('temporary quota refresh')
        report=f.preflight(service)
        self.assertTrue(report['retryable_read_failure'])
        self.assertFalse(report['submission_eligible'])

    def test_compact_event_snapshot_preserves_full_durable_history(self):
        registry=self.f.registry
        levels=[dict(level_index=i,target_n=(i+1)*5,target_id=f'n{(i+1)*5}',experiment_id=f'n{(i+1)*5}') for i in range(6)]
        registry.register_independent_campaign(dict(campaign_id='audit',manifest_sha256='f'*64,levels=levels))
        for index in range(110):
            registry.checkpoint_independent_campaign('audit',state='PAUSED',level_index=0,
                checkpoint={'cycle':index},event_type='TEST_RETRY')
        self.assertEqual(len(registry.independent_campaign_status('audit')['events']),100)
        complete=registry.independent_campaign_status('audit',event_limit=None)['events']
        self.assertEqual(sum(e['event_type']=='TEST_RETRY' for e in complete),110)


    def test_proxy_error_retries_without_replacement(self):
        from requests.exceptions import ProxyError
        result,_,cycle,sleep=self.run_cycles([ProxyError('proxy unavailable'),{'state':'COMPLETE'}])
        self.assertEqual(result['state'],'COMPLETE');sleep.assert_called_once_with(60)
        self.assertEqual(cycle.call_count,2)

    def test_terminal_result_errors_are_bounded_and_network_never_becomes_missing(self):
        from worker_runtime import terminal_result,TerminalResultUnavailable
        for status in ('DONE','FAILED','CANCELLED'):
            job=Mock();job.status.return_value=status;job.result.side_effect=RuntimeError('result unavailable')
            sleep=Mock()
            with self.assertRaises(TerminalResultUnavailable):terminal_result(job,sleeper=sleep)
            self.assertEqual(job.result.call_count,3)
            self.assertEqual([c.args[0] for c in sleep.call_args_list],[1,2])
        for exc in (TimeoutError('network'),PermissionError('access denied')):
            job=Mock();job.status.return_value='DONE';job.result.side_effect=exc
            with self.assertRaises(type(exc)):terminal_result(job,sleeper=Mock())
            job.result.assert_called_once()
        for status in ('QUEUED','RUNNING'):
            job=Mock();job.status.return_value=status
            with self.assertRaisesRegex(RuntimeError,'not terminal'):terminal_result(job,sleeper=Mock())
            job.result.assert_not_called()

    def test_unlimited_failed_attempts_restart_and_eventual_single_slot_acceptance(self):
        import targeted_recovery as recovery
        from job_registry import JobRegistry
        target,block,_=self.f.block(missing=[(fixtures.TREATMENTS[0],5)])
        target.experiment_id='offline';target.shots_per_pub=4096
        registry=self.f.registry;original=registry.repaired_pub_values(target.target_id)
        for attempt in range(1,9):
            row=registry.plan_pub_replacement(target_id=target.target_id,logical_block_id=block,
                treatment_id=target.treatment_ids[0],source_pub_index=5,logical_pub_index=5,options={'shots':4096},
                allow_new_attempt=True,max_attempts=1,metadata={'source_run':'run_02','input_hashes':target.input_hashes,
                    'source_job_id':block+'-'+target.treatment_ids[0],'source_archive_sha256':'e'*64})
            rid=row['replacement_id'];self.assertEqual(row['attempt'],attempt)
            registry.transition_pub_replacement(rid,'SUBMITTING')
            registry.transition_pub_replacement(rid,'SUBMITTED',job_id='retry-'+str(attempt))
            job=Mock();job.status.return_value='DONE';job.metrics.return_value={}
            job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=[float('nan') if attempt<8 else .3]*7,stds=[.02]*7),metadata={})]
            service=Mock();service.job.return_value=job
            result=recovery.collect_replacement(self.directory,target,registry,rid,service)
            self.assertEqual(result['state'],'FAILED' if attempt<8 else 'VALIDATED')
            registry=JobRegistry(registry.database)  # restart every attempt
            if attempt>1:
                metadata=next(r for r in registry.replacement_jobs(target.target_id) if r['replacement_id']==rid)['metadata']
                self.assertEqual(metadata['recovery_delay_seconds'],min(60*2**min(attempt-2,4),900))
                self.assertIn('not_before_utc',metadata)
        self.assertEqual(len(registry.replacement_jobs(target.target_id)),8)
        accepted=registry.repaired_pub_values(target.target_id)
        self.assertEqual(len(accepted),40)
        self.assertEqual([r for r in accepted if r['source']=='ORIGINAL'],original)
        self.assertTrue(self.f.decision(target,block)['eligible'])

    def test_grouped_replacement_accepts_each_slot_independently(self):
        import targeted_recovery as recovery
        target,block,_=self.f.block(missing=[(fixtures.TREATMENTS[0],i) for i in (3,4,5)])
        target.experiment_id='offline';target.shots_per_pub=4096
        registry=self.f.registry;rows=[]
        for index in (3,4,5):
            rows.append(registry.plan_pub_replacement(target_id=target.target_id,logical_block_id=block,
                treatment_id=target.treatment_ids[0],source_pub_index=index,logical_pub_index=index,options={'shots':4096},
                metadata={'source_run':'run_02','input_hashes':target.input_hashes,
                    'source_job_id':block+'-'+target.treatment_ids[0],'source_archive_sha256':'e'*64}))
        mapping=[dict(replacement_id=row['replacement_id'],pub_index=index,logical_pub_index=row['logical_pub_index'],treatment_id=row['treatment_id']) for index,row in enumerate(rows,1)]
        for row in rows:
            rid=row['replacement_id'];registry.transition_pub_replacement(rid,'PLANNED',metadata={'replacement_pub_map':mapping})
            registry.transition_pub_replacement(rid,'SUBMITTING')
            registry.transition_pub_replacement(rid,'SUBMITTED',job_id='grouped')
        job=Mock();job.status.return_value='DONE';job.metrics.return_value={}
        job.result.return_value=[SimpleNamespace(data=SimpleNamespace(evs=[.3]*7,stds=[.02]*7),metadata={}),
            SimpleNamespace(data=SimpleNamespace(evs=[float('nan')]*7,stds=[.02]*7),metadata={})]
        service=Mock();service.job.return_value=job
        reports=[recovery.collect_replacement(self.directory,target,registry,row['replacement_id'],service) for row in rows]
        self.assertEqual([r['state'] for r in reports],['VALIDATED','FAILED','FAILED'])
        self.assertEqual(len(registry.repaired_pub_values(target.target_id)),38)
        for row in rows:recovery.collect_replacement(self.directory,target,registry,row['replacement_id'],service)
        self.assertEqual(len(registry.repaired_pub_values(target.target_id)),38)

    def test_scientific_failure_retries_only_analysis_and_live_enables_it(self):
        result,registry,cycle,sleep=self.run_cycles([campaign.ScientificCompletionPending('figure export failed'),{'state':'COMPLETE'}])
        self.assertEqual(result['state'],'COMPLETE');sleep.assert_called_once_with(60)
        self.assertTrue(all(c.kwargs['automatic_science'] for c in cycle.call_args_list))
        self.assertEqual(registry.checkpoint_independent_campaign.call_args.kwargs['checkpoint']['failure_category'],'SCIENTIFIC_RETRY')
        with patch.object(campaign,'_finalize_level',side_effect=OSError('disk full')):
            with self.assertRaises(campaign.ScientificCompletionPending):campaign._complete_science()


    def test_complete_campaign_runs_science_without_provider_access(self):
        registry=self.f.registry;targets={};levels=[]
        for index in range(6):
            target=self.f.target((index+1)*5);targets[target.target_id]=target
            levels.append(dict(level_index=index,target_n=target.target_n,target_id=target.target_id,experiment_id=target.target_id))
        registry.register_independent_campaign(dict(campaign_id='science',manifest_sha256='f'*64,levels=levels,source_run='run_02',calibration_data_excluded=True))
        for index,level in enumerate(levels):
            target=targets[level['target_id']]
            for sequence in range(1,target.blocks_required+1):self.f.block(target,sequence)
            if index<5:registry.complete_independent_level('science',index)
        auth=dict(live_auto_submit=True,allowed_accounts=['labA'],allowed_actions=['prepare','preflight','dry-run','submit','monitor','collect','validate'])
        provider=Mock(side_effect=AssertionError('Complete data cannot access IBM'))
        with patch.object(campaign,'load_campaign_authorization',return_value=(auth,{})), \
             patch.object(campaign,'campaign_root',return_value=self.directory), \
             patch.object(campaign,'_target_from_level',side_effect=lambda root,level:targets[level['target_id']]), \
             patch.object(campaign,'inspect_independent_campaign',return_value={}), \
             patch.object(campaign,'_finalize_level') as science:
            result=campaign.run_independent_campaign(self.directory,'science',registry,authorize_live_campaign=True,
                _worker_lock_held=True,automatic_science=True,service_factory=provider)
        self.assertEqual(result['state'],'COMPLETE');self.assertTrue(result['publication_exports_verified'])
        self.assertEqual({c.args[4].target_n for c in science.call_args_list},{5,10,15,20,25,30})
        provider.assert_not_called()

    def test_publication_export_exactly_ten_real_pngs_600dpi_and_idempotent(self):
        import scientific_exports as exports
        from PIL import Image
        target=SimpleNamespace(target_id='n5',target_n=5,experiment_id='n5',treatment_ids=fixtures.TREATMENTS,
            shots_per_pub=4096,input_hashes=self.f.hashes)
        source=self.directory/'source';source.mkdir()
        canonical=source/'canonical';canonical.mkdir()
        publication=source/'publication_figures';publication.mkdir()
        comparison=source/'comparison_figures';comparison.mkdir()
        for name in exports.PUBLICATION_FIGURES:
            with Image.new('L',(4800,4800),255) as picture:picture.save(publication/name,dpi=(600,600))
        for name in exports.WIDE_FIGURES:
            destination=comparison if name=='distribution_pilot.png' else publication
            with Image.new('L',(1600,1000),255) as picture:picture.save(destination/name,dpi=(600,600))
        (comparison/'04_mitigation_accuracy_cost.png').write_bytes(b'Optional figure must not be exported')
        repeats=[{'treatment':name,'pub':index,'SSE':1,'RMSE':.1} for name in fixtures.TREATMENTS for index in range(1,6)]
        exports._write_csv(canonical/'per_repeat.csv',repeats)
        for name in ('accuracy_cost_data.csv','bode_curves.csv','nyquist_curves.csv','residual_curves.csv','sse_plot_data.csv','summary_mean_std.csv','tail_stats.csv'):
            exports._write_csv(canonical/name,[{'treatment':fixtures.TREATMENTS[0],'value':1}])
        (canonical/'round_summary.json').write_text('{}')
        (source/'analysis_manifest.json').write_text(json.dumps({'target_id':'n5','canonical_combined_analysis':'canonical','qpu_usage_coverage':{'complete_comparable':True}}))
        stage=self.directory/'stage';stage.mkdir()
        registry=Mock();registry.pub_validation_records.return_value=[];registry.rows.return_value=[]
        with patch('repository_paths.repository_root',return_value=self.directory), \
             patch('repository_paths.create_scratch_dir',return_value=stage), \
             patch('repository_paths.cleanup_scratch_dir'), patch.object(exports,'_display',side_effect=lambda x:x):
            result=exports.export_n_level_results(self.directory,target,registry,{'output':str(source),'artifacts_verified':True})
            again=exports.export_n_level_results(self.directory,target,registry,{'output':str(source),'artifacts_verified':True})
        self.assertTrue(result['files_verified']);self.assertTrue(again['already_exists'])
        self.assertEqual(len(result['png_files']),10)
        self.assertEqual(set(result['png_files']),set(exports.PAPER_FIGURES+exports.WIDE_FIGURES))
        self.assertEqual(set(result['csv_files']),set(exports.CSV_FILES))


    def test_registry_schema_upgrade_preserves_attempts_and_events(self):
        from job_registry import JobRegistry
        target,block,_=self.f.block(missing=[(fixtures.TREATMENTS[0],5)])
        self.f.replacement(target,block,fixtures.TREATMENTS[0],5)
        registry=self.f.registry;before=registry.replacement_jobs(target.target_id)
        with registry._connection() as db:
            sql=db.execute("SELECT sql FROM sqlite_master WHERE name='pub_replacement_jobs'").fetchone()[0]
            sql=sql.replace('pub_replacement_jobs','legacy_replacements',1).replace('job_id TEXT,','job_id TEXT UNIQUE,')
            db.execute('PRAGMA foreign_keys=OFF');db.execute('BEGIN IMMEDIATE')
            db.execute(sql);db.execute('INSERT INTO legacy_replacements SELECT * FROM pub_replacement_jobs')
            db.execute('DROP TABLE pub_replacement_jobs');db.execute('ALTER TABLE legacy_replacements RENAME TO pub_replacement_jobs')
            events=[tuple(row) for row in db.execute('SELECT * FROM pub_replacement_events')];db.commit()
        connect=JobRegistry._connect
        class FailIndex:
            def __init__(self,db):self.db=db
            def __getattr__(self,key):return getattr(self.db,key)
            def execute(self,sql,*args):
                if 'CREATE UNIQUE INDEX IF NOT EXISTS replacement_provider_pub_identity' in sql:
                    raise RuntimeError('injected index failure')
                return self.db.execute(sql,*args)
        with patch.object(JobRegistry,'_connect',lambda instance:FailIndex(connect(instance))):
            with self.assertRaisesRegex(RuntimeError,'injected index failure'):JobRegistry(registry.database)
        self.assertEqual(before,registry.replacement_jobs(target.target_id))
        with registry._connection() as db:
            self.assertIn('job_id TEXT UNIQUE',db.execute("SELECT sql FROM sqlite_master WHERE name='pub_replacement_jobs'").fetchone()[0])
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='pub_replacement_jobs_account_labels_migration'").fetchall())
        upgraded=JobRegistry(registry.database)
        self.assertEqual(before,upgraded.replacement_jobs(target.target_id))
        with upgraded._connection() as db:
            self.assertEqual(events,[tuple(row) for row in db.execute('SELECT * FROM pub_replacement_events')])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(len(upgraded.repaired_pub_values(target.target_id)),40)


    def test_queued_running_replacements_wait_without_retrieval_or_new_intent(self):
        import targeted_recovery as recovery
        target,block,_=self.f.block(missing=[(fixtures.TREATMENTS[0],5)])
        row=self.f.registry.plan_pub_replacement(target_id=target.target_id,logical_block_id=block,
            treatment_id=target.treatment_ids[0],source_pub_index=5,logical_pub_index=5,options={'shots':4096})
        rid=row['replacement_id'];self.f.registry.transition_pub_replacement(rid,'SUBMITTING')
        self.f.registry.transition_pub_replacement(rid,'SUBMITTED',job_id='pending')
        before=self.f.registry.replacement_jobs(target.target_id)
        for state in ('QUEUED','RUNNING'):
            job=Mock();job.status.return_value=state;service=Mock();service.job.return_value=job
            result=recovery.collect_replacement(self.directory,target,self.f.registry,rid,service)
            self.assertEqual(result['state'],'SUBMITTED');job.result.assert_not_called()
            self.assertEqual(before,self.f.registry.replacement_jobs(target.target_id))

    def test_persisted_cooldown_blocks_submission_before_any_provider_call(self):
        import targeted_recovery as recovery
        target,block,_=self.f.block(missing=[(fixtures.TREATMENTS[0],5)])
        kwargs=dict(target_id=target.target_id,logical_block_id=block,treatment_id=target.treatment_ids[0],
            source_pub_index=5,logical_pub_index=5,options={'shots':4096},allow_new_attempt=True)
        row=self.f.registry.plan_pub_replacement(**kwargs)
        self.f.registry.transition_pub_replacement(row['replacement_id'],'SUBMITTING')
        self.f.registry.transition_pub_replacement(row['replacement_id'],'SUBMITTED',job_id='confirmed-failed')
        self.f.registry.transition_pub_replacement(row['replacement_id'],'FAILED')
        row=self.f.registry.plan_pub_replacement(**kwargs)
        provider=Mock()
        with patch('final_configuration.validate_final_config',side_effect=lambda c:c):
            result=recovery.submit_replacement_slot(self.directory,target,self.f.registry,row['replacement_id'],provider,
                final_config={'live_submission_enabled':True},campaign_authorization={'live_auto_submit':True,'allowed_actions':['submit']},
                preflight_report={},runtime_estimate=None,explicit_authorization=True,active_instance_ref='',authorized_instance_refs=[])
        self.assertEqual(result['state'],'PLANNED');self.assertGreater(result['retry_after_seconds'],0)
        self.assertEqual(provider.mock_calls,[])


if __name__=='__main__':unittest.main()
