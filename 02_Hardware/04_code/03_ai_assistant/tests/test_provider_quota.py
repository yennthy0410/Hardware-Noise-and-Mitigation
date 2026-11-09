"""Provider quota regression tests; all services are mocks and networking is blocked."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_account_integration as fixtures
from account_config import SavedAccountProfile
from read_only_preflight import preflight_account, preflight_all, _instance_ref
from quota import provider_quota_fields, provider_reservations, account_quota_report


class ProviderQuotaTests(unittest.TestCase):
    def setUp(self):
        guard=patch('socket.socket.connect', side_effect=AssertionError('No network in offline tests'))
        guard.start(); self.addCleanup(guard.stop)
        self.fixture=fixtures.AccountIntegrationTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)

    def service(self, consumed=100, limit=600, pending=()):
        service=Mock()
        service.active_instance.return_value='crn:test'
        service.instances.return_value=[{'crn':'crn:test','plan':'open'}]
        service.usage.return_value={'usage_consumed_seconds':consumed,'usage_limit_seconds':limit}
        service.jobs.return_value=list(pending)
        backend=Mock(); backend.name='ibm_fez'
        backend.status.return_value=SimpleNamespace(operational=True,status_msg='active',pending_jobs=0)
        backend.target.instruction_supported.return_value=True
        service.backend.return_value=backend
        return service

    def preflight(self, service):
        with patch('read_only_preflight._compatibility',return_value={'available':True,'batch_created':False}):
            return preflight_account(SavedAccountProfile('labA'), service_factory=lambda _:service,
                frozen_validator=lambda:{'input_sha256':{'isa':'frozen'},'isa_instructions':[]},
                allowed_instance_refs=[_instance_ref('crn:test')])

    def test_history_outside_campaign_and_exhaustion(self):
        f=self.fixture
        # A has no campaign usage, but IBM reports 590 seconds used elsewhere.
        result=f.select({'labA':f.report('labA',10),'labB':f.report('labB',300),'labC':f.report('labC',0)})
        self.assertEqual(result['account_label'],'labB')
        self.assertFalse(result['account_reports']['labA']['eligible'])
        self.assertFalse(result['account_reports']['labC']['eligible'])

    def test_legacy_consumption_only_selection_fails_closed(self):
        from quota import select_account
        with self.assertRaisesRegex(RuntimeError,'Provider remaining quota'):
            select_account(Mock(),{'labA':0},reservation_seconds=175)

    def test_registry_verified_headroom_and_concurrent_reservations(self):
        from job_registry import JobRegistry
        with fixtures.scratch_directory() as directory:
            registry=JobRegistry(directory/'quota.sqlite3')
            args=dict(logical_block_id='first',account_label='labA',backend='ibm_fez',
                treatments=['M0'],input_hashes={'isa':'frozen'},quota_seconds_per_job=175,
                actual_usage_seconds=500,n=5,replicate_blocks=1,safety_margin_seconds=10,
                available_budget_seconds=149,expected_active_reserved_seconds=0)
            with self.assertRaisesRegex(ValueError,'insufficient'):registry.reserve_batch(**args)
            args['available_budget_seconds']=155
            registry.reserve_batch(**args)
            self.assertEqual(sum(row['quota_reserved_seconds'] for row in registry.rows()),150)
            self.assertEqual(registry.rows()[0]['metadata']['estimated_runtime_seconds'],175)
            args['logical_block_id']='second' 
            with self.assertRaisesRegex(ValueError,'reservations changed'):registry.reserve_batch(**args)

    def test_internal_reservation_credits_measured_usage_once(self):
        from independent_n_campaign import _campaign_usage
        registry=MagicMock()
        row=dict(account_label='labA',submission_state='SUBMITTED',qpu_usage_seconds=50,
            quota_reserved_seconds=175)
        with patch('quota.provider_reservations',return_value={}):
            db=registry._connection.return_value.__enter__.return_value
            db.execute.return_value.fetchall.side_effect=[[row],[],[]]
            db.execute.return_value.fetchone.return_value=[1]
            result=_campaign_usage(registry,{'levels':[{'target_id':'offline'}]},self.fixture.auth)
        self.assertEqual(result['campaign_consumed_seconds'],50)
        self.assertEqual(result['campaign_reserved_seconds'],125)

    def test_stale_missing_future_and_naive_timestamps(self):
        f=self.fixture
        for stamp in (None,'invalid',datetime.now().isoformat(),
                (datetime.now(timezone.utc)-timedelta(seconds=601)).isoformat(),
                (datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat()):
            with self.subTest(stamp=stamp), self.assertRaises(RuntimeError):
                f.select({'labA':f.report('labA',600)|{'checked_utc':stamp}})

    def test_missing_and_inconsistent_provider_fields(self):
        for raw in ({}, {'usage_limit_seconds':600,'usage_remaining_seconds':600},
                {'usage_consumed_seconds':0}, {'usage_consumed_seconds':False,'usage_limit_seconds':600},
                {'usage_consumed_seconds':500,'usage_limit_seconds':600,'usage_remaining_seconds':200}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                provider_quota_fields(raw)
        self.assertEqual(provider_quota_fields({'usage_consumed_seconds':200,'usage_allocation_seconds':600})['remaining_seconds'],400)
        self.assertEqual(provider_quota_fields({'usage_consumed_seconds':0,'usage_limit_seconds':600,
            'usage_limit_reached':True})['remaining_seconds'],0)

    def test_preflight_all_backends_pending_and_ambiguous_instances(self):
        service=self.service(pending=[Mock()])
        report=self.preflight(service)
        self.assertFalse(report['submission_eligible'])
        self.assertNotIn('backend_name', service.jobs.call_args.kwargs)
        service=self.service()
        service.instances.return_value.append({'crn':'crn:second','plan':'open'})
        self.assertFalse(self.preflight(service)['account_wide_usage_verified'])

    def test_provider_limit_below_local_600_is_not_invented_or_rejected(self):
        report=self.preflight(self.service(consumed=20,limit=300))
        self.assertTrue(report['submission_eligible'])
        self.assertEqual(report['instances'][0]['usage']['remaining_seconds'],280)

    def test_each_saved_profile_is_queried_separately(self):
        services={'labA':self.service(600),'labB':self.service(200)}
        factory=Mock(side_effect=lambda profile:services[profile.label])
        with patch('read_only_preflight.frozen_bundle_summary',return_value={'isa_instructions':[]}), \
             patch('read_only_preflight._compatibility',return_value={'available':True}):
            reports=preflight_all(('labA','labB'),auth_mode='saved-profile',service_factory=factory,
                allowed_instance_refs={x:[_instance_ref('crn:test')] for x in services})
        self.assertEqual(factory.call_count,2)
        for service in services.values():service.usage.assert_called_once()
        self.assertFalse(reports['labA']['submission_eligible'])
        self.assertTrue(reports['labB']['submission_eligible'])

    def test_reservations_not_subtracted_twice_and_ambiguous_work_excluded(self):
        f=self.fixture
        f.usage['provider_reservations']['labA']['reserved_seconds']=20
        f.usage['per_account']['labA']['reserved_seconds']=20
        selected=f.select({'labA':f.report('labA',210)})
        self.assertEqual(selected['provider_available_seconds'],190)
        f.usage['provider_reservations']['labA']['ambiguous']=True
        with self.assertRaises(RuntimeError): f.select({'labA':f.report('labA',600)})

    def test_all_registry_workloads_reservations_and_completed_usage(self):
        registry=MagicMock()
        db=registry._connection.return_value.__enter__.return_value
        rows=[dict(account_label='labA',state='COMPLETED',job_id='old',quota_reserved_seconds=175,qpu_usage_seconds=140),
              dict(account_label='labA',state='RESERVED',job_id=None,quota_reserved_seconds=20,qpu_usage_seconds=None),
              dict(account_label='labB',state='SUBMITTED',job_id='outside-campaign',quota_reserved_seconds=175,qpu_usage_seconds=50)]
        db.execute.return_value.fetchall.side_effect=[rows,[]]
        result=provider_reservations(registry)
        self.assertEqual(result['labA'],{'reserved_seconds':20,'ambiguous':False})
        self.assertTrue(result['labB']['ambiguous'])

    def test_adaptive_gate_uses_runtime_plus_margin(self):
        from adaptive_targets import evaluate_new_batch
        f=self.fixture
        manifest=SimpleNamespace(backend='ibm_fez',source_run='run_02',input_hashes={'isa':'frozen'},
            chunk_n=5,target_n=25,target_id='offline')
        f.estimate.target_n=5
        report=f.report('labA',600)
        result=evaluate_new_batch(manifest,account_label='labA',preflight_report=report,
            runtime_estimate=f.estimate,remaining_seconds=180,safety_margin_seconds=10)
        self.assertTrue(result['checks']['quota_headroom'])
        self.assertEqual(result['quota']['required_quota_seconds'],150)
        self.assertEqual(result['quota']['required_runtime_with_margin_seconds'],185)

    def test_replacement_40_second_claim_preserves_estimate_and_blocks_races(self):
        import test_batch_progression as progression
        f=progression.BatchProgressionTests();f.setUp();self.addCleanup(f.doCleanups)
        target,block,ids=f.block(missing=((progression.TREATMENTS[0],1),(progression.TREATMENTS[0],2)))
        plans=[]
        for index in (1,2):
            item=f.registry.plan_pub_replacement(target_id=target.target_id,logical_block_id=block,
                treatment_id=progression.TREATMENTS[0],source_pub_index=index,logical_pub_index=index,
                options={'shots':4096},metadata={'source_run':'run_02','input_hashes':f.hashes,
                    'source_job_id':block+'-'+progression.TREATMENTS[0],'source_archive_sha256':'e'*64})
            plans.append(item['replacement_id'])
        args=dict(reservation_seconds=1000,preflight_checked_at=datetime.now(timezone.utc).isoformat(),
            quota_scope='account_wide_verified',runtime_upper_bound_seconds=1000,safety_margin_seconds=75,
            expected_reserved_seconds=0)
        with self.assertRaisesRegex(RuntimeError,'insufficient'):
            f.registry.claim_pub_replacement_submission(plans[0],available_budget_seconds=39,**args)
        f.registry.claim_pub_replacement_submission(plans[0],available_budget_seconds=45,**args)
        row=next(r for r in f.registry.replacement_jobs(target.target_id) if r['replacement_id']==plans[0])
        self.assertEqual(row['quota_reserved_seconds'],40)
        self.assertEqual(row['metadata']['runtime_upper_bound_seconds'],1000)
        with self.assertRaisesRegex(RuntimeError,'reservations changed or ambiguous'):
            f.registry.claim_pub_replacement_submission(plans[1],available_budget_seconds=45,**args)
        other=next(r for r in f.registry.replacement_jobs(target.target_id) if r['replacement_id']==plans[1])
        self.assertEqual(other['state'],'PLANNED')

    def test_155_seconds_submits_fake_full_batch_with_1000_second_estimate(self):
        import json
        from job_registry import JobRegistry
        from batch_scheduler import submit_runtime_block
        names=tuple('M'+str(i) for i in range(8))
        estimate=SimpleNamespace(target_n=5,scope='full_batch',defensible=True,stress_block_seconds=1000,
            per_treatment={name:{'stress_envelope_seconds':125} for name in names})
        manifest=SimpleNamespace(n=5,backend='ibm_fez',source_run='run_02',shots_per_pub=4096,
            replicate_blocks=1,input_hashes={'isa':'frozen'},physical_qubits=tuple(range(140,147)),
            treatments=[{'id':name} for name in names])
        with fixtures.scratch_directory() as root:
            registry=JobRegistry(root/'registry.db');records=root/'records';records.mkdir()
            (records/'round_plan.json').write_text('{}')
            for name in names:(records/name).mkdir()
            service=self.service(consumed=445)
            batch=MagicMock();batch.return_value.__enter__.return_value=SimpleNamespace(session_id='fake-batch')
            estimator=Mock();estimator.return_value.run.side_effect=[SimpleNamespace(job_id=lambda i=i:'fake-'+str(i)) for i in range(8)]
            with patch('batch_scheduler._assert_bundle_matches'),patch('vqe_adapter.record_directory',return_value=records),patch('vqe_adapter.treatment_options',return_value={}),patch('vqe_adapter.verify_frozen_inputs',return_value=(None,[0]*7,[0]*21,tuple(range(140,147)),None,None)):
                ids=submit_runtime_block(manifest,registry,service,logical_block_id='offline',account_label='labA',
                    instance='crn:test',reservation_seconds_per_job=25,runtime_estimate=estimate,
                    authorize_live=True,preflight_verified=True,local_budget_remaining_seconds=155,
                    batch_factory=batch,estimator_factory=estimator,pilot=Mock(),root=root)
            self.assertEqual(len(ids),8)
            self.assertEqual(estimator.return_value.run.call_count,8)
            self.assertTrue(all(len(call.args[0])==5 for call in estimator.return_value.run.call_args_list))
            self.assertEqual(sum(row['quota_reserved_seconds'] for row in registry.rows()),150)
            self.assertTrue(all(row['metadata']['estimated_runtime_seconds']==125 for row in registry.rows()))
            self.assertEqual(sum(json.loads((records/name/'submission_state.json').read_text())['quota_reservation_seconds_per_treatment'] for name in names),150)

    def test_refresh_exhausted_provider_prevents_batch_creation(self):
        from batch_scheduler import submit_runtime_block
        f=self.fixture; service=self.service(600)
        f.estimate.target_n=5
        f.estimate.per_treatment={str(i):{'stress_envelope_seconds':175/8} for i in range(8)}
        manifest=SimpleNamespace(n=5,backend='ibm_fez',source_run='run_02',physical_qubits=tuple(range(140,147)),
            treatments=[{'id':str(i)} for i in range(8)])
        batch=Mock(); estimator=Mock(); registry=Mock(); registry.rows.return_value=[]
        with patch('batch_scheduler._assert_bundle_matches'), \
             patch('batch_scheduler.provider_reservations',return_value={'labA':{'reserved_seconds':0,'ambiguous':False}}), \
             patch('vqe_adapter.verify_frozen_inputs',return_value=(None,[0]*7,[0]*21,tuple(range(140,147)),None,None)):
            with self.assertRaisesRegex(RuntimeError,'quota gate'):
                submit_runtime_block(manifest,registry,service,logical_block_id='offline',account_label='labA',
                    instance='crn:test',reservation_seconds_per_job=1,runtime_estimate=f.estimate,
                    authorize_live=True,preflight_verified=True,local_budget_remaining_seconds=365,
                    batch_factory=batch,estimator_factory=estimator,pilot=Mock())
        service.usage.assert_called_once()
        batch.assert_not_called(); estimator.assert_not_called(); registry.reserve_batch.assert_not_called()
