"""Offline operator orchestration and canonical SDK adapter tests. No IBM traffic."""
import json
import sys
import tempfile
import time
import uuid
import shutil
import unittest
from contextlib import nullcontext, ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, MagicMock, patch
import manual_execution as m
sys.path.insert(0,str(m.HERE.parent/'03_ai_assistant/03_python_automation'))

def scratch(case):
    path=m.HERE/'state'/('manual-tests-'+uuid.uuid4().hex);path.mkdir()
    case.addCleanup(shutil.rmtree,path)
    return path


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.manager=m.ManualExecution(state=scratch(self))
        self.guard=patch('socket.socket.connect',side_effect=AssertionError('Network prohibited'));self.guard.start();self.addCleanup(self.guard.stop)
        self.cfg={'enabled_account_labels':['labA'],'allowed_backend':'ibm_fez','allowed_instance_refs':{'labA':['instance-hash']},'live_submission_enabled':True}
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        config=NS(load_final_config=lambda _:self.cfg)
        self.stack.enter_context(patch.object(m,'api',return_value=(None,None,None,config,None)))
        self.stack.enter_context(patch.object(self.manager,'lock',return_value=nullcontext()))
        self.manifest=NS(to_dict=lambda:{'hash':'frozen'})
        self.stack.enter_context(patch.object(m,'frozen_preview',return_value={'circuit':'fixture'}))
        self.stack.enter_context(patch('experiment_manifest.build_manifest',return_value=self.manifest))
        self.fresh=self.stack.enter_context(patch.object(self.manager,'fresh',return_value=(None,self.cfg,{'checked_utc':'now'},{'eligible':True})))
        self.body={'operation':'pub_submit','account':'labA','label':'experiment one','treatments':[m.TREATMENTS[0]],'pubs':[1]}

    def ready(self,body=None):
        p=self.manager.preview(body or self.body);self.manager.preflight(p['request_id'],p['scope_id']);return p

    def test_confirmation_is_required_and_consumed_once(self):
        p=self.ready();key=p['request_id']
        for approved in (False,'true',1):
            with self.assertRaises(ValueError):self.manager.authorize(key,p['scope_id'],approved)
        self.manager.authorize(key,p['scope_id'],True)
        with self.assertRaises(ValueError):self.manager.authorize(key,p['scope_id'],True)
        with patch.object(self.manager,'perform',return_value={'job_id':'mock-job'}) as perform:
            self.manager.execute(key)
            with self.assertRaises(ValueError):self.manager.execute(key)
            perform.assert_called_once()
        self.assertEqual(m.ManualExecution(state=self.manager.state).get(key)['result']['job_id'],'mock-job')

    def test_duplicate_and_expired_scopes(self):
        p=self.ready()
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.manager.preview(self.body)
        with self.manager.db() as db:db.execute('UPDATE requests SET expires=0')
        with self.assertRaises(ValueError):self.manager.authorize(p['request_id'],p['scope_id'],True)
        self.manager.preview(self.body)

    def test_failed_preflight_and_frozen_change_do_not_execute(self):
        p=self.manager.preview(self.body)
        self.fresh.side_effect=ValueError('Insufficient verified quota')
        with self.assertRaisesRegex(ValueError,'quota'):self.manager.preflight(p['request_id'],p['scope_id'])
        with self.assertRaises(ValueError):self.manager.authorize(p['request_id'],p['scope_id'],True)
        self.fresh.side_effect=None
        self.manifest.to_dict=lambda:{'hash':'changed'}
        with self.assertRaisesRegex(ValueError,'frozen inputs changed'):self.manager.preflight(p['request_id'],p['scope_id'])

    def test_manual_auto_lock_blocks(self):
        p=self.manager.preview(self.body)
        with patch.object(self.manager,'lock',side_effect=RuntimeError('AUTO owns lock')):
            with self.assertRaisesRegex(RuntimeError,'lock'):self.manager.preflight(p['request_id'],p['scope_id'])
        self.fresh.assert_not_called()

    def test_authorization_scope_and_account(self):
        with self.assertRaisesRegex(ValueError,'authorized'):self.manager.preview(self.body|{'account':'labK'})
        p=self.ready()
        with self.assertRaises(ValueError):self.manager.authorize(p['request_id'],'wrong',True)
        self.cfg['live_submission_enabled']=False
        with self.assertRaisesRegex(ValueError,'disabled'):self.manager.check_preview(self.manager.get(p['request_id']))

    def test_parser_and_paths(self):
        self.assertEqual(m.parse_console('job status abc',{'account':'labA'})['job_id'],'abc')
        self.assertIn('pub recover',m.parse_console('help')['help'])
        for command in ('powershell whoami','job status abc; rm x','python print(1)','export ../../data'):
            with self.assertRaises(ValueError):m.parse_console(command)
        for body in ({'treatments':[{}]}, {'output':'../data'}, {'pubs':[True]}, {'experiment':'QAOA'}):
            with self.assertRaises(ValueError):m.validated(self.body|body)

    def test_failed_worker_before_submission_is_blocked(self):
        p=self.ready();self.manager.authorize(p['request_id'],p['scope_id'],True)
        with patch.object(self.manager,'perform',side_effect=ValueError('Inputs changed')):
            with self.assertRaises(ValueError):self.manager.execute(p['request_id'])
        self.assertEqual(self.manager.get(p['request_id'])['state'],'BLOCKED')

    def test_nan_and_secret_redaction(self):
        result=m.clean({'token':'private','values':[float('nan'),float('inf')],'log':'Bearer private'})
        self.assertNotIn('private',json.dumps(result,allow_nan=False));self.assertEqual(result['values'],[None,None])

    def test_external_status_retrieve_no_campaign_import(self):
        job=Mock();job.job_id.return_value='external';job.backend.return_value.name='ibm_fez';job.status.return_value='DONE'
        job.metrics.return_value={'time':'recorded'};job.logs.return_value='token=secret';job.error_message.return_value=None
        service=Mock();service.job.return_value=job
        self.stack.enter_context(patch.object(self.manager,'service',return_value=(service,NS(label='labA'),self.cfg)))
        self.stack.enter_context(patch.object(self.manager,'registry',return_value=Mock()))
        self.stack.enter_context(patch.object(self.manager,'source',return_value=('external',None)))
        request=m.validated({'account':'labA','job_id':'external'})
        self.assertEqual(self.manager.read_job('a'*32,request)['status'],'DONE');job.result.assert_not_called()
        captured={'pub_count':1,'records':[],'invalid_expectation_pubs':[],'invalid_uncertainty_pubs':[]}
        with patch('vqe_adapter.capture_runtime_result',return_value=captured):
            result=self.manager.read_job('a'*32,request|{'operation':'retrieve'})
        self.assertNotIn('validated_artifact',result);self.assertIn('Not performed',result['campaign_import']);job.result.assert_called_once()
        service.job.side_effect=ValueError('Unknown Job ID')
        with self.assertRaisesRegex(ValueError,'Unknown'):self.manager.read_job('b'*32,request)

    def test_result_cache(self):
        job=Mock();wrapped=m._CachedJob(job);wrapped.result();wrapped.result();job.result.assert_called_once()

    def test_exact_targeted_recovery_reuses_adapter(self):
        request=m.validated(self.body|{'operation':'recover','job_id':'original','treatments':['M3_ZNE'],'pubs':[3]})
        service=Mock();service.job.return_value.status.return_value='DONE';service.active_instance.return_value='crn:test'
        self.fresh.return_value=(service,self.cfg,{'checked_utc':'now'}, {})
        target=NS(target_id='manual-test',shots_per_pub=4096,physical_qubits=list(range(140,147)),input_hashes={'isa':'frozen'})
        source={'account_label':'labA','treatment_id':'M3_ZNE','logical_block_id':'manual-test','job_id':'original'}
        candidate={'source_pub_index':3,'logical_pub_index':3,'source_archive_path':'archived.json','source_archive_sha256':'hash'}
        registry=Mock();registry.plan_pub_replacement.return_value={'replacement_id':10}
        self.stack.enter_context(patch.object(self.manager,'live_grant'))
        configuration=Mock();configuration.campaign_authorization_from_final_config.return_value={'live_auto_submit':True}
        with patch.object(self.manager,'registry',return_value=registry),patch.object(self.manager,'recovery_candidate',return_value=(target,source,candidate)),patch.object(m,'api',return_value=(None,None,None,configuration,None)),patch('vqe_adapter.treatment_options',return_value=NS(model_dump=lambda **_: {'shots':4096})),patch('runtime_estimator.targeted_replacement_estimate'),patch('targeted_recovery.submit_replacement_slot',return_value={'job_id':'replacement'}) as submit:
            self.assertEqual(self.manager.recover(request)['job_id'],'replacement')
            self.assertEqual(registry.plan_pub_replacement.call_args.kwargs['logical_pub_index'],3)
            self.assertEqual(registry.plan_pub_replacement.call_args.kwargs['metadata']['source_job_id'],'original')
            self.assertTrue(submit.call_args.kwargs['explicit_authorization']);submit.assert_called_once()
            service.job.return_value.status.return_value='UNKNOWN'
            with self.assertRaisesRegex(ValueError,'UNKNOWN'):self.manager.recover(request)
            submit.assert_called_once()

    def test_unknown_registry_intent_blocks_fresh_preflight(self):
        registry=Mock();registry.rows.return_value=[{'submission_state':'UNKNOWN'}]
        with patch.object(self.manager,'service',return_value=(Mock(),Mock(),self.cfg)),patch.object(self.manager,'registry',return_value=registry),patch('read_only_preflight.preflight_account') as provider:
            with self.assertRaisesRegex(ValueError,'reconciliation'):m.ManualExecution.fresh(self.manager,m.validated(self.body))
            provider.assert_not_called()


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.root=scratch(self)
        guard=patch('socket.socket.connect',side_effect=AssertionError('Network prohibited'));guard.start();self.addCleanup(guard.stop)

    def run_submission(self,mode='batch',count=2,quota=155):
        from job_registry import JobRegistry
        from batch_scheduler import submit_runtime_block
        from runtime_estimator import estimate_runtime
        registry=JobRegistry(self.root/'registry.db');records=self.root/'records';records.mkdir()
        (records/'round_plan.json').write_text('{}')
        for name in m.TREATMENTS:(records/name).mkdir()
        selected=list(m.TREATMENTS[:2] if mode=='batch' else m.TREATMENTS[:1])
        estimate=estimate_runtime([],treatments=m.TREATMENTS,target_n=count)
        manifest=NS(n=count,backend='ibm_fez',source_run='run_02',shots_per_pub=4096,replicate_blocks=1,input_hashes={'isa':'frozen'},physical_qubits=tuple(range(140,147)),treatments=[{'id':name} for name in m.TREATMENTS])
        service=Mock();service.active_instance.return_value='crn:test';service.jobs.return_value=[]
        service.usage.return_value={'usage_consumed_seconds':600-quota,'usage_limit_seconds':600}
        batch=MagicMock();batch.return_value.__enter__.return_value=NS(session_id='batch-test')
        estimator=Mock();estimator.return_value.run.side_effect=[NS(job_id=lambda i=i:'job-'+str(i)) for i in range(len(selected))]
        with patch('batch_scheduler._assert_bundle_matches'),patch('vqe_adapter.record_directory',return_value=records),patch('vqe_adapter.treatment_options',return_value={}),patch('vqe_adapter.verify_frozen_inputs',return_value=(None,[0]*7,[0]*21,tuple(range(140,147)),None,None)):
            ids=submit_runtime_block(manifest,registry,service,logical_block_id='manual-fixture',account_label='labA',instance='crn:test',reservation_seconds_per_job=150,runtime_estimate=estimate,authorize_live=True,preflight_verified=True,batch_factory=batch,estimator_factory=estimator,pilot=Mock(),root=self.root,
                adaptive_target_context={'target_id':'manual-fixture','target_n':count,'chunk_n':count,'manifest_sha256':'hash','account_label':'labA','runtime_upper_bound_seconds':None,'safety_margin_seconds':10,'preflight_checked_at':'now','quota_scope':'account_wide_verified'},
                manual_selection={'treatments':selected,'pub_labels':list(range(1,count+1)),'mode':mode})
        self.assertEqual(len(ids),len(selected));self.assertEqual(sum(r['quota_reserved_seconds'] for r in registry.rows()),150)
        for call in estimator.return_value.run.call_args_list:self.assertEqual(len(call.args[0]),count)
        for row in registry.rows():self.assertEqual(row['metadata']['pub_labels'],list(range(1,count+1)))
        return batch,estimator,service

    def test_manual_batch_subset(self):self.run_submission()[0].assert_called_once()
    def test_standalone_single_pub_no_batch(self):
        batch,estimator,service=self.run_submission('job',1);batch.assert_not_called()
        self.assertIs(estimator.call_args.kwargs['mode'],service.backend.return_value)
    def test_new_single_pub_requires_150_not_40(self):
        with self.assertRaisesRegex(RuntimeError,'quota gate'):self.run_submission('job',1,149)


class ScientificTests(unittest.TestCase):
    def test_existing_calculation_export_and_input_integrity(self):
        from experiment_manifest import build_manifest
        import hashlib
        root=scratch(self);manager=m.ManualExecution(state=root)
        manifest=build_manifest(m.PROJECT,n=1)
        preview=m.frozen_preview(manifest)
        self.assertEqual(len(preview['theta_vector']),21)
        self.assertEqual(len(preview['mapped_observables']),7)
        inputs=[Path(manifest.source_bundle)/n for n in ('circuit.qpy','theta.csv','observables.json','lock.json')]
        inputs += [m.PROJECT/'data/EIS14.csv',m.HERE.parent/'01_vqe/mitigation_pilot.py']
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        artifact=root/'fixture.json'
        artifact.write_text(json.dumps({'job_id':'offline-fixture','input_hashes':manifest.input_hashes,'captured':{'records':[{'pub_index':1,'expectation_validation':'PASS','expectation_values':[0.1]*7}]}}))
        ref={'path':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}
        with patch.object(manager,'get',return_value={'result':{'validated_artifact':ref}}),patch('socket.socket.connect',side_effect=AssertionError('Network prohibited')):
            result=manager.analyze('a'*32,{'run_id':'b'*32,'operation':'export'})
            self.assertTrue((root/('a'*32)/'manual_metrics.png').is_file())
            self.assertGreaterEqual(result['rows'][0]['SSE'],0)
            with self.assertRaises(FileExistsError):manager.analyze('a'*32,{'run_id':'b'*32,'operation':'export'})
            artifact.write_text('{}')
            with self.assertRaisesRegex(ValueError,'changed'):manager.analyze('c'*32,{'run_id':'b'*32,'operation':'analyze'})
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})


if __name__=='__main__':unittest.main()
