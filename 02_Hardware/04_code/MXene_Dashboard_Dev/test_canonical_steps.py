"""Real canonical STEP boundary/locks and immutable receipts; fixture science only."""
import copy,hashlib,json,sqlite3,unittest,uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import auto_broker

pipeline,campaign,runtime,config,registry_module=auto_broker.modules()


class CanonicalStepTests(unittest.TestCase):
    def setUp(self):
        self.root=auto_broker.HERE/'state'/('step-fixture-'+uuid.uuid4().hex[:10]);self.root.mkdir()
        self.db=self.root/'registry.sqlite3'
        with closing(sqlite3.connect(self.db)) as db,db:db.execute('CREATE TABLE protected(value TEXT)');db.execute("INSERT INTO protected VALUES('accepted')")
        self.before=hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.directory=self.root/'campaign'
        manifest={'backend':'fixture','levels':[{'target_n':n} for n in pipeline.N_LEVELS]}
        digest=campaign._sha(manifest);manifest['manifest_sha256']=digest
        self.status={'exists':True,'manifest':manifest,'manifest_sha256':digest,'state':'PAUSED','current_level_index':5,'checkpoint':{'batch':5}}
        self.registry=Mock();self.registry.independent_campaign_status.side_effect=lambda *_:copy.deepcopy(self.status)
        self.registry.target_status.return_value={'exists':True}
        self.target=SimpleNamespace(target_id='target5',target_n=5,sha256='frozen-target',input_hashes={'qpy':'frozen'},treatment_ids=('M0','M1'))
        self.repaired={'analysis_eligible':True,'status':'COMPLETE','duplicate_slots':False,
                       'counts_by_treatment':{'M0':5,'M1':5},
                       'rows':[{'treatment_id':t,'logical_pub_index':i,'job_id':t,'input_hashes':{'qpy':'frozen'},'source_sha256':'raw'} for t in ('M0','M1') for i in range(1,6)]}
        import repository_paths,adaptive_targets,targeted_recovery,scientific_exports
        self.paths=repository_paths
        self.output=self.root/repository_paths.MITIGATION_REL/'output/02_run_level_data/adaptive_targets/target5'
        def analyze(*args):
            self.assertEqual(auto_broker.probe(self.directory/'worker.lock'),'BUSY')
            existed=self.output.exists();self.output.mkdir(parents=True,exist_ok=True)
            if not existed:
                (self.output/'analysis_manifest.json').write_text('{"fixture":true}')
                (self.output/'result.csv').write_text('metric,value\nSSE,1.5\n')
            return {'output':str(self.output),'artifacts_verified':True,'already_complete':existed}
        patches=[patch.object(registry_module,'JobRegistry',return_value=self.registry),
                 patch.object(campaign,'campaign_root',return_value=self.directory),
                 patch.object(campaign,'_campaign_has_unknown',return_value=False),
                 patch.object(pipeline,'_typed_target',return_value=self.target),
                 patch.object(campaign,'dry_run_independent_campaign',return_value={'requested_pubs':840}),
                 patch.object(config,'load_final_config',return_value={'allowed_backend':'fixture'}),
                 patch.object(config,'campaign_authorization_from_final_config',return_value={'allowed_accounts':['fixture'],'allowed_actions':['prepare','collect','analyze','plot']}),
                 patch.object(targeted_recovery,'repaired_dataset_status',side_effect=lambda *_:copy.deepcopy(self.repaired)),
                 patch.object(repository_paths,'repository_root',return_value=self.root),
                 patch.object(adaptive_targets,'analyze_target',side_effect=analyze),
                 patch.object(scientific_exports,'export_n_level_results',return_value={'files_verified':True,'already_exists':True,
                     'figures':str(self.root/repository_paths.MITIGATION_REL/'output/01_results_and_figures/n=5'),
                     'data':str(self.root/repository_paths.MITIGATION_REL/'output/02_run_level_data/n=5'),
                     'png_files':list(scientific_exports.PAPER_FIGURES+scientific_exports.WIDE_FIGURES)})]
        self.mocks=[p.start() for p in patches]
        for p in patches:self.addCleanup(p.stop)
        self.addCleanup(self.clean)
    def clean(self):
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),self.before)
        # Exact fixture-owned files only; never canonical production paths.
        for p in sorted(self.root.rglob('*'),key=lambda p:len(p.parts),reverse=True):
            if p.is_file():p.unlink()
            elif p.is_dir():p.rmdir()
        self.root.rmdir()
    def run_step(self,op,**kw):
        return pipeline.execute_campaign_step(self.root,'vqe-independent-n-01',op,
            n=kw.pop('n',None if op=='prepare' else 5),registry_path=self.db,
            authorize_step=kw.pop('authorize_step',True),authorize_output_write=kw.pop('authorize_output_write',True),**kw)
    def test_collect_dispatch_owns_lock_and_never_analyzes(self):
        def collect(*args):
            self.assertEqual(auto_broker.probe(self.directory/'worker.lock'),'BUSY')
            self.assertEqual(args[-1],1)
            return {'state':'COLLECT_CHECKED'}
        with patch.object(pipeline,'_collect_campaign_step',side_effect=collect) as call:
            result=self.run_step('collect',batch=1)
            call.assert_called_once();self.assertEqual(result['campaign_state'],'PAUSED')
            self.mocks[-2].assert_not_called();self.mocks[-1].assert_not_called()
        with runtime.campaign_worker_lock(self.directory):
            with self.assertRaises(Exception):self.run_step('collect',batch=1)
    def test_collect_requires_scope_and_write_authorization(self):
        with self.assertRaises(ValueError):self.run_step('collect')
        with self.assertRaises(PermissionError):self.run_step('collect',batch=1,authorize_output_write=False)
    def test_manual_scope_uses_canonical_lock_and_write_authorization(self):
        import manual_steps
        scope={'kind':'job','action':'collect','job_id':'fixture','scope_id':'a'*64}
        def dispatch(*args):
            self.assertEqual(auto_broker.probe(self.directory/'worker.lock'),'BUSY')
            self.assertEqual(args[-1],scope)
            return {'state':'COLLECT_CHECKED'}
        with patch.object(manual_steps,'execute_manual_scope',side_effect=dispatch) as call:
            with self.assertRaises(PermissionError):self.run_step('manual',manual_scope=scope,authorize_output_write=False)
            call.assert_not_called()
            self.assertEqual(self.run_step('manual',manual_scope=scope)['campaign_state'],'PAUSED')
            with runtime.campaign_worker_lock(self.directory):
                with self.assertRaises(Exception):self.run_step('manual',manual_scope=scope)
            call.assert_called_once();self.mocks[-2].assert_not_called();self.mocks[-1].assert_not_called()
    def test_prepare_only_verifies_existing_and_repeats(self):
        for _ in range(2):self.assertEqual(self.run_step('prepare')['result']['state'],'ALREADY_PREPARED')
        self.assertEqual(self.mocks[3].call_count,12)
        self.mocks[9].assert_not_called();self.mocks[10].assert_not_called()
        self.assertEqual(self.mocks[0].call_args.kwargs,{'readonly':True})
    def test_analyze_once_without_export_or_advancement(self):
        result=self.run_step('analyze')
        self.assertEqual(result['result']['state'],'ANALYZED');self.assertFalse(result['campaign_progression_changed'])
        receipt=result['result']['analysis_receipt']
        self.assertEqual(hashlib.sha256(Path(receipt['path']).read_bytes()).hexdigest(),receipt['sha256'])
        self.mocks[9].assert_called_once();self.mocks[10].assert_not_called()
        self.assertEqual((self.output/'result.csv').read_text(),'metric,value\nSSE,1.5\n')
        self.assertEqual(auto_broker.probe(self.directory/'worker.lock'),'FREE')
    def test_export_only_verifies_receipt_and_reuses_canonical_export(self):
        self.run_step('analyze');self.mocks[9].reset_mock()
        result=self.run_step('export');self.assertTrue(result['result']['already_exists'])
        self.mocks[9].assert_not_called();self.mocks[10].assert_called_once()
        self.assertEqual(self.mocks[10].call_args.args[3]['artifacts_verified'],True)
        exported=result['result']
        self.assertEqual(exported['png_count'],10)
        self.assertEqual(exported['output'],exported['figure_directory'])
        self.assertEqual(Path(exported['output']),self.root/self.paths.MITIGATION_REL/'output/01_results_and_figures/n=5')
        self.assertEqual(exported['analysis_output'],str(self.output))
        self.assertEqual(Path(exported['export_manifest']).parent,Path(exported['data_directory']))
    def test_export_reports_existing_versioned_destination_without_analyzing(self):
        self.run_step('analyze');self.mocks[9].reset_mock()
        value=self.mocks[10].return_value
        value['figures']+='/v001-fixture';value['data']+='/v001-fixture'
        result=self.run_step('export')['result']
        self.assertEqual(result['output'],value['figures'])
        self.assertEqual(result['data_directory'],value['data'])
        self.mocks[9].assert_not_called()
    def test_export_missing_or_changed_artifacts_refused(self):
        with self.assertRaises(RuntimeError):self.run_step('export')
        self.run_step('analyze');(self.output/'result.csv').write_text('corrupt')
        with self.assertRaises(RuntimeError):self.run_step('export')
        self.mocks[10].assert_not_called()
    def test_export_rejects_wrong_receipt_provenance_without_running_science(self):
        self.run_step('analyze');self.mocks[9].reset_mock()
        path=next((self.directory/'steps').glob('*.json'))
        receipt=json.loads(path.read_text());receipt['n']=25
        path.write_text(json.dumps(receipt))
        with self.assertRaises(pipeline.CampaignStepBlocked):self.run_step('export')
        self.mocks[9].assert_not_called();self.mocks[10].assert_not_called()
    def test_repeat_analysis_retains_immutable_receipt(self):
        self.run_step('analyze');p=next((self.directory/'steps').glob('*.json'));before=p.read_bytes()
        self.assertTrue(self.run_step('analyze')['result']['already_complete']);self.assertEqual(p.read_bytes(),before)
    def test_interrupted_receipt_write_not_reported_success_and_restart(self):
        with patch.object(self.paths,'atomic_archive',side_effect=OSError('fixture interrupted write')):
            with self.assertRaises(OSError):self.run_step('analyze')
        with self.assertRaises(RuntimeError):self.run_step('export')
        self.assertTrue(self.run_step('analyze')['result']['already_complete'])
        self.assertEqual(self.run_step('export')['result']['state'],'EXPORTED')
    def test_wrong_scope_and_authorization_never_analyze(self):
        for kwargs in ({'authorize_step':False},{'authorize_output_write':False},{'n':None},{'n':True},{'n':6}):
            with self.assertRaises((ValueError,PermissionError)):self.run_step('analyze',**kwargs)
        with self.assertRaises(ValueError):pipeline.execute_campaign_step(self.root,'other','prepare',authorize_step=True)
        with self.assertRaises(ValueError):self.run_step('collect')
        self.mocks[9].assert_not_called()
    def test_unknown_pending_and_frozen_mismatch_block_science(self):
        self.mocks[2].return_value=True
        with self.assertRaises(RuntimeError):self.run_step('analyze')
        self.mocks[2].return_value=False;self.repaired['analysis_eligible']=False
        with self.assertRaises(RuntimeError):self.run_step('analyze')
        self.repaired['analysis_eligible']=True;self.repaired['rows'][0]['input_hashes']={'qpy':'wrong'}
        with self.assertRaises(RuntimeError):self.run_step('analyze')
        self.mocks[9].assert_not_called();self.mocks[10].assert_not_called()
    def test_active_worker_conflict(self):
        with runtime.campaign_worker_lock(self.directory):
            with self.assertRaises(RuntimeError):self.run_step('prepare')
        self.mocks[3].assert_not_called()
    def test_parent_manifest_corruption_and_action_rejection(self):
        self.status['manifest']['backend']='wrong'
        with self.assertRaises(RuntimeError):self.run_step('prepare')
        self.mocks[3].assert_not_called()
    def test_missing_prepared_target_is_not_reported_ready(self):
        self.registry.target_status.return_value={'exists':False}
        with self.assertRaises(RuntimeError):self.run_step('prepare')
        self.mocks[4].assert_not_called()
    def test_active_account_authorization_required_even_without_submission(self):
        self.mocks[6].return_value={'allowed_accounts':[],'allowed_actions':['analyze']}
        with self.assertRaises(PermissionError):self.run_step('analyze')
        self.mocks[9].assert_not_called()

if __name__=='__main__':unittest.main()
