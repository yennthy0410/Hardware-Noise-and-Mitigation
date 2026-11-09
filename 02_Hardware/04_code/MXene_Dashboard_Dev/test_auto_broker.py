"""No production launches: subprocesses and provider execution are mocked."""
import json, sqlite3, time, unittest, uuid
from pathlib import Path
from unittest.mock import patch, Mock
from contextlib import closing
import auto_broker as b
from worker_supervisor import Supervisor, ControlRequest, ProductionAdapter

class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.root=b.HERE/'state'/('broker-fixture-'+uuid.uuid4().hex);self.root.mkdir()
        self.broker=b.Broker(self.root,self.root)
        self.policy=dict(backend='fake-backend',accounts=['fixture-account'],authorization_identity='fixture',
            checkpoint='fixture',ready=True,complete=False,unknown_submission=False,quota_implications='150/40; fresh canonical quota required')
        self.evidence=dict(worker='STOPPED_VERIFIED',ownership_verified=True,can_execute=True,unknown_submission=False,
                           supported_operations=['start','resume','pause','preflight','monitor005','prepare','collect','analyze','export'],
                           policy=self.policy,owner=None,reason='Fixture')
        p=patch.object(self.broker,'inspect',side_effect=lambda:dict(self.evidence));p.start();self.addCleanup(p.stop)
        self.adapter=ProductionAdapter()
        self.collect_preview={'campaign_id':b.CAMPAIGN,'n':30,'batch':5,'operation':'COLLECT-only','scope_id':'a'*64,
            'jobs':[{'job_id':f'fixture-job-{i}','treatment_id':f'M{i}'} for i in range(8)]}
        def call(path,payload=None):
            if path=='/status':return self.broker.inspect()
            if path=='/collect-preview':return dict(self.collect_preview,n=payload['n'],batch=payload['batch'])
            return self.broker.dispatch(payload['request_id'])
        p=patch('broker_client.call',side_effect=call);p.start();self.addCleanup(p.stop)
        self.s=Supervisor(self.root/'worker_control.sqlite3',self.adapter)
        def clean():
            for path in self.root.iterdir():
                if path.is_file():path.unlink()
            self.root.rmdir()
        self.addCleanup(clean)
    def prepare(self,op='start'):return self.s.prepare('fixture-chat',ControlRequest(operation=op))
    def confirm(self,r):return self.s.confirm('fixture-chat',r['control_request'],True)
    def test_manual_http_requires_scoped_one_shot_confirmation(self):
        from fastapi.testclient import TestClient
        from conversations import Conversations
        import app
        self.evidence['supported_operations'].append('manual')
        preview={'state':'READY','scope_id':'c'*64,'jobs':[{'job_id':'registered-original','attempt':1}],
            'collection_unit':'Entire parent Job preserved','scope':{'kind':'pub','slot':23}}
        def call(path,payload=None):
            if path=='/status':return self.broker.inspect()
            if path=='/manual-preview':return preview
            return self.broker.dispatch(payload['request_id'])
        with patch('broker_client.call',side_effect=call),patch.object(app,'store',Conversations(self.root/'chat.sqlite3')),patch.object(app,'supervisor',self.s),patch.object(b.subprocess,'Popen') as child:
            client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            chat=client.post('/api/conversations',json={}).json()['id']
            body={'operation':'manual','n':30,'manual_scope':{'kind':'pub','action':'collect','job_id':'registered-original','slot':23,'treatment':'M0_Baseline'}}
            r=client.post(f'/api/conversations/{chat}/manual',json=body)
            self.assertEqual(r.status_code,200);self.assertEqual(r.json()['control_state'],'AWAITING_CONFIRMATION')
            self.assertIn('archive/validation writes',r.json()['answer']);child.assert_not_called()
            request=r.json()['control_request'];url=f'/api/conversations/{chat}/controls/{request}/confirm'
            self.assertGreaterEqual(client.post(url,json={'approved':False}).status_code,400);child.assert_not_called()
            self.assertEqual(client.post(url,json={'approved':True}).status_code,200);child.assert_called_once()
            self.assertNotEqual(client.post(url,json={'approved':True}).status_code,200);child.assert_called_once()
            launch=json.loads(self.broker.launch_path.read_text())
            self.assertEqual(launch['manual_scope']['scope_id'],'c'*64)
            self.assertEqual(launch['manual_scope']['slot'],23)
            body['operation']='export'
            self.assertEqual(client.post(f'/api/conversations/{chat}/manual',json=body).status_code,409)

    def test_manual_active_worker_and_canonical_blocker(self):
        from worker_supervisor import ManualScope
        command=ControlRequest(operation='manual',n=30,manual_scope=ManualScope(kind='job',action='collect',job_id='fixture-job'))
        self.evidence['supported_operations'].append('manual')
        self.evidence.update(worker='RUNNING_VERIFIED',operation='manual')
        self.assertEqual(self.s.prepare('fixture-chat',command)['control_state'],'BLOCKED')
        self.evidence.update(worker='STOPPED_VERIFIED')
        def call(path,payload=None):
            return self.broker.inspect() if path=='/status' else {'state':'BLOCKED','reason':'Exact provenance conflict fixture'}
        with patch('broker_client.call',side_effect=call),patch.object(b.subprocess,'Popen') as child:
            r=self.s.prepare('fixture-chat',command)
            self.assertEqual(r['control_state'],'BLOCKED');self.assertIn('Exact provenance conflict',r['answer']);child.assert_not_called()

    def test_export_variants_use_confirmed_canonical_http_route(self):
        from fastapi.testclient import TestClient
        import app
        from conversations import Conversations
        from worker_supervisor import route_control
        store=Conversations(self.root/'export-chat.sqlite3')
        variants=('Export n=25 figures','Export figure n=25','Export figures for n=25',
                  '  EXPORT   figures for n = 25!  ')
        with patch.object(app,'store',store),patch.object(app,'supervisor',self.s),patch.object(app.reader,'status',return_value={'campaign':[{'state':'PAUSED'}]}),patch.object(b.subprocess,'Popen') as child,patch('local_ai.interpret',side_effect=AssertionError('No generic interpretation')):
            client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            chat=client.post('/api/conversations',json={}).json()['id']
            for text in variants:
                command=route_control(text)
                self.assertEqual((command.operation,command.n),('export',25))
                response=client.post(f'/api/conversations/{chat}/operator',json={'message':text})
                self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')
                self.assertEqual(response.json()['control_state'],'AWAITING_CONFIRMATION')
                self.assertIn('output writes',response.json()['answer'])
            child.assert_not_called()
            request=response.json()['control_request']
            response=client.post(f'/api/conversations/{chat}/controls/{request}/confirm',json={'approved':True})
            self.assertEqual(response.json()['control_state'],'START_REQUESTED');child.assert_called_once()
            self.assertEqual(json.loads(self.broker.launch_path.read_text())['operation'],'export')
        self.assertIsNone(route_control('Explain how to export figures for n=25'))
        self.assertIsNone(route_control('Analyze n=25'))

    def test_scientific_steps_cannot_overlap_or_confirm_after_worker_starts(self):
        pending=self.s.prepare('fixture-chat',ControlRequest(operation='export',n=25))
        with patch.object(b.subprocess,'Popen',side_effect=AssertionError('No duplicate')):
            for active in ('analyze','export'):
                self.evidence.update(worker='RUNNING_VERIFIED',operation=active)
                for op in ('analyze','export'):
                    self.assertEqual(self.s.prepare('fixture-chat',ControlRequest(operation=op,n=25))['control_state'],'BLOCKED')
                with self.assertRaises(ValueError):self.confirm(pending)

    def test_latest_step_observation_states_receipt_and_reason(self):
        request=uuid.uuid4().hex
        receipt={'request':request,'operation':'analyze','n':25,'state':'LOCKED'}
        observation=self.broker.step_observation(receipt,None,'RUNNING_VERIFIED')
        self.assertEqual(observation['operation_status'],'RUNNING')
        for control,status in (('STEP_COMPLETED','SUCCEEDED'),('STEP_FAILED','FAILED'),('STEP_FAILED','BLOCKED')):
            result={'answer':'Fixture outcome','rows':[],'control_state':control,'operation_status':status,
                    'failure_reason':None if status=='SUCCEEDED' else 'Fixture failure reason'}
            b.atomic(self.root/('step-'+request+'.json'),result)
            receipt.update(state='EXITED',outcome=control)
            observation=self.broker.step_observation(receipt,None,'STOPPED_VERIFIED')
            self.assertEqual(observation['operation_status'],status)
            self.evidence.update(observation)
            response=self.s.status()
            self.assertIn(status,response['answer'])
            self.assertEqual(response['receipt']['request'],request)
            self.assertEqual(response['step_result'],result)
            self.assertEqual(response['failure_reason'],result['failure_reason'])
        newer={'request':uuid.uuid4().hex,'operation':'export','n':25}
        observation=self.broker.step_observation(receipt,newer,'STALE_UNRESOLVED')
        self.assertEqual(observation['operation_receipt']['request'],newer['request'])
        self.assertIsNone(observation['step_result'])
        self.assertEqual(observation['operation_status'],'BLOCKED')
        receipt.update(state='CRASHED')
        self.assertEqual(self.broker.step_observation(receipt,None,'STALE_UNRESOLVED')['operation_status'],'FAILED')
        (self.root/('step-'+request+'.json')).unlink()
        self.assertTrue(self.broker.step_observation(receipt,None,'STALE_UNRESOLVED')['step_report_error'])
    def test_collect_explicit_scope_confirmed_no_duplicate(self):
        from worker_supervisor import route_control
        command=route_control('Collect completed jobs for n=30 Batch 005.')
        self.assertEqual((command.operation,command.n,command.batch),('collect',30,5))
        response=self.s.prepare('fixture-chat',command)
        self.assertEqual(response['control_state'],'AWAITING_CONFIRMATION')
        self.assertIn('No submission',response['answer'])
        with patch.object(b.subprocess,'Popen') as child:
            self.confirm(response);child.assert_called_once()
            launch=json.loads(self.broker.launch_path.read_text())
            self.assertEqual((launch['operation'],launch['n'],launch['batch']),('collect',30,5))
            with self.assertRaises(ValueError):self.confirm(response)
    def test_collect006_variants_confirm_scope_without_chat_or_writes(self):
        from fastapi.testclient import TestClient
        import app
        from conversations import Conversations
        from worker_supervisor import route_control
        store=Conversations(self.root/'collect006-chat.sqlite3')
        variants=('Collect completed jobs for n=30 Batch 006','Collect results for n=30 Batch 6',
                  'Please collect completed jobs from Batch 006 for n=30',
                  'Collect n = 30, Batch #006','collect jobs for batch=006 at n=30',
                  'Can you collect completed jobs for n=30 Batch 006?',
                  'Collect Batch 006 for n=30')
        with patch.object(app,'store',store),patch.object(app,'supervisor',self.s),patch.object(app.reader,'status',return_value={'campaign':[{'state':'PAUSED'}]}),patch.object(app,'run_assistant',side_effect=AssertionError('No generic fallback')),patch.object(b.subprocess,'Popen') as child:
            client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            chat=client.post('/api/conversations',json={}).json()['id']
            for text in variants:
                command=route_control(text);self.assertEqual((command.operation,command.n,command.batch),('collect',30,6))
                response=client.post(f'/api/conversations/{chat}/operator',json={'message':text}).json()
                self.assertEqual(response['control_state'],'AWAITING_CONFIRMATION')
                self.assertIn('COLLECT-only',response['answer']);self.assertIn('Batch 006',response['answer'])
                self.assertEqual(len(response['rows']),8)
                self.assertEqual(response['collection_scope']['campaign_id'],b.CAMPAIGN)
            child.assert_not_called()
            for text in ('Collect n=25 Batch 006','Collect n=30 Batch 007','Collect n=30 Batch 006 and submit replacements'):
                response=client.post(f'/api/conversations/{chat}/operator',json={'message':text}).json()
                self.assertEqual(response['control_state'],'BLOCKED')
            child.assert_not_called()
            self.collect_preview.update(control_state='BLOCKED',answer='Submission file conflicts with registered job identity: job_id')
            response=client.post(f'/api/conversations/{chat}/operator',json={'message':variants[0]}).json()
            self.assertEqual(response['control_state'],'BLOCKED')
            self.assertIn(self.collect_preview['answer'],response['answer']);child.assert_not_called()

    def test_collect006_latest_result_preserves_receipt_counts_in_conversation(self):
        from fastapi.testclient import TestClient
        import app
        from conversations import Conversations
        store=Conversations(self.root/'collect-status-chat.sqlite3')
        self.evidence.update(operation_status='SUCCEEDED',operation_receipt={'request':'f'*32,'operation':'collect','n':30,'batch':6},
            step_result={'answer':'Canonical collect returned: COLLECT_CHECKED','rows':[{'collected_jobs':8,'valid_pubs':40,'missing_pubs':0}]})
        with patch.object(app,'store',store),patch.object(app,'supervisor',self.s),patch.object(app.reader,'status',return_value={'campaign':[{'state':'PAUSED'}]}):
            client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            chat=client.post('/api/conversations',json={}).json()['id']
            response=client.post(f'/api/conversations/{chat}/operator',json={'message':'Show step result'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['rows'][0]['collected_jobs'],8)
            saved=client.get('/api/conversations/'+chat).json()['turns'][-1]['response']
            self.assertEqual(saved['receipt']['request'],'f'*32)
            self.assertEqual(saved['operation_status'],'SUCCEEDED')
    def test_collect_http_route_and_quick_assistant_refusal(self):
        from fastapi.testclient import TestClient
        import app
        from conversations import Conversations
        store=Conversations(self.root/'chat.sqlite3')
        with patch.object(app,'store',store),patch.object(app,'supervisor',self.s),patch.object(app.reader,'status',return_value={'campaign':[{'state':'PAUSED'}]}),patch.object(b.subprocess,'Popen') as child:
            client=TestClient(app.app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            chat=client.post('/api/conversations',json={}).json()['id']
            response=client.post(f'/api/conversations/{chat}/operator',json={'message':'Collect completed jobs for n=30 Batch 005.'})
            self.assertEqual(response.status_code,200);self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')
            self.assertEqual(response.json()['control_state'],'AWAITING_CONFIRMATION');child.assert_not_called()
            request=response.json()['control_request']
            result=client.post(f'/api/conversations/{chat}/controls/{request}/confirm',json={'approved':True})
            self.assertEqual(result.json()['control_state'],'START_REQUESTED');child.assert_called_once()
            self.assertEqual(client.post('/api/ai/analyze',json={'message':'Collect completed jobs for n=30 Batch 005.'}).status_code,400)
    def test_start_fixed_template_async_and_replay_blocked(self):
        r=self.prepare()
        with patch.object(b.subprocess,'Popen') as launch:
            result=self.confirm(r)
            self.assertEqual(result['control_state'],'START_REQUESTED');launch.assert_called_once()
            args,kw=launch.call_args
            self.assertEqual(args[0],[str(b.PYTHON),'-B',str(b.SCRIPT),'--worker',r['control_request']])
            self.assertFalse(kw['shell']);self.assertEqual(kw['env']['OPENBLAS_NUM_THREADS'],'1')
            with self.assertRaises(ValueError):self.confirm(r)
            with self.assertRaises(ValueError):self.broker.dispatch(r['control_request'])
    def test_attach_does_not_spawn(self):
        self.evidence['worker']='RUNNING_VERIFIED'
        with patch.object(b.subprocess,'Popen',side_effect=AssertionError('Duplicate')):
            self.assertEqual(self.prepare()['control_state'],'ATTACHED')
    def test_unknown_and_crash_block_start(self):
        for state in ('UNKNOWN','STALE_UNRESOLVED'):
            self.evidence.update(worker=state,ownership_verified=False,can_execute=False)
            self.assertEqual(self.prepare()['control_state'],'BLOCKED')
    def test_expired_authorization(self):
        r=self.prepare()
        with closing(sqlite3.connect(self.s.path)) as db, db:db.execute('UPDATE requests SET expires=0')
        with self.assertRaises(ValueError):self.confirm(r)
    def test_policy_change_requires_fresh_confirmation(self):
        r=self.prepare();self.policy['authorization_identity']='changed'
        with self.assertRaises(ValueError):self.confirm(r)
    def test_unknown_submission_and_complete_protection(self):
        self.evidence['unknown_submission']=True
        self.assertEqual(self.prepare()['control_state'],'BLOCKED')
        self.evidence['unknown_submission']=False;self.policy['complete']=True
        self.assertEqual(self.prepare()['control_state'],'BLOCKED')
    def test_safe_pause_calls_canonical_no_termination(self):
        self.evidence['worker']='RUNNING_VERIFIED'
        campaign=Mock();campaign.control_independent_campaign.return_value={'state':'PAUSE_REQUESTED'}
        registry=Mock()
        with patch.object(b,'modules',return_value=(Mock(),campaign,Mock(),Mock(),registry)),patch.object(b.subprocess,'Popen',side_effect=AssertionError('No pause subprocess')):
            self.assertEqual(self.confirm(self.prepare('pause'))['control_state'],'PAUSE_REQUESTED')
            self.assertEqual(campaign.control_independent_campaign.call_args.kwargs,{'action':'pause'})
    def test_resume_persists_intent_no_browser_loop(self):
        r=self.prepare('resume');self.s=Supervisor(self.s.path,self.adapter)
        with patch.object(b.subprocess,'Popen') as launch:self.confirm(r);launch.assert_called_once()
        self.assertEqual(json.loads(self.broker.launch_path.read_text())['operation'],'resume')
    def test_direct_broker_without_grant_refused(self):
        # Initialize schema but do not confirm any operation.
        self.prepare()
        with self.assertRaises(ValueError):self.broker.dispatch(uuid.uuid4().hex)
    def test_spawn_failure_not_retried(self):
        with patch.object(b.subprocess,'Popen',side_effect=OSError('fixture launch failure')) as launch:
            result=self.confirm(self.prepare());self.assertEqual(result['control_state'],'UNKNOWN')
            self.assertEqual(self.prepare()['control_state'],'BLOCKED');self.assertEqual(launch.call_count,1)
    def test_step_disabled(self):
        self.assertEqual(self.s.prepare('fixture-chat',ControlRequest(operation='collect'))['control_state'],'BLOCKED')
    def test_canonical_steps_preserve_explicit_n_and_output_authorization(self):
        for op in ('analyze','export'):
            self.assertEqual(self.s.prepare('fixture-chat',ControlRequest(operation=op))['control_state'],'BLOCKED')
            result=self.s.prepare('fixture-chat',ControlRequest(operation=op,n=5))
            self.assertIn('output writes',result['answer'])
            with patch.object(b.subprocess,'Popen') as child:
                self.confirm(result);child.assert_called_once()
            launch=json.loads(self.broker.launch_path.read_text())
            self.assertEqual((launch['operation'],launch['n']),(op,5))
    def test_canonical_step_unknown_and_concurrent_diagnostic_block(self):
        self.evidence['unknown_submission']=True
        for op in ('prepare','analyze','export'):
            self.assertEqual(self.s.prepare('fixture-chat',ControlRequest(operation=op,n=None if op=='prepare' else 5))['control_state'],'BLOCKED')
        self.evidence.update(unknown_submission=False,worker='RUNNING_VERIFIED',operation='analyze')
        self.assertEqual(self.prepare('resume')['control_state'],'BLOCKED')
        self.assertEqual(self.prepare('pause')['control_state'],'BLOCKED')
    def test_http_canonical_step_confirmation_reaches_existing_broker(self):
        from fastapi.testclient import TestClient
        from app import app
        from conversations import Conversations
        store=Conversations(self.root/'canonical-conversations.sqlite3')
        with patch('app.supervisor',self.s),patch('app.store',store),patch('app.reader.status',return_value={'campaign':[{'state':'PAUSED'}]}),patch.object(b.subprocess,'Popen') as child,patch('local_ai.interpret',side_effect=AssertionError('No LLM')):
            client=TestClient(app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            chat=client.post('/api/conversations').json()['id']
            response=client.post(f'/api/conversations/{chat}/operator',json={'message':'Analyze n-level n=5'})
            self.assertEqual(response.status_code,200);self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')
            self.assertIn('output writes',response.json()['answer']);child.assert_not_called()
            request=response.json()['control_request']
            response=client.post(f'/api/conversations/{chat}/controls/{request}/confirm',json={'approved':True})
            self.assertEqual(response.json()['control_state'],'START_REQUESTED');child.assert_called_once()
            self.assertEqual(json.loads(self.broker.launch_path.read_text())['n'],5)
            self.assertEqual(client.post('/api/ai/analyze',json={'message':'Analyze n-level n=5'}).status_code,400)
    def test_durable_step_report_requires_valid_matching_completion(self):
        identity=uuid.uuid4().hex
        receipt={'request':identity,'operation':'monitor005','state':'EXITED','outcome':'STEP_COMPLETED'}
        path=self.root/('step-'+identity+'.json')
        with self.assertRaises(FileNotFoundError):self.broker.completed_step_result(receipt)
        for value in ('{', '[]',json.dumps({'answer':'Partial','rows':[],'control_state':'STEP_FAILED'})):
            path.write_text(value)
            with self.assertRaises(ValueError):self.broker.completed_step_result(receipt)
        result={'answer':'Fixture completed','rows':[],'control_state':'STEP_COMPLETED'}
        b.atomic(path,result)
        self.assertEqual(self.broker.completed_step_result(receipt),result)
        with patch.object(type(path),'read_text',side_effect=PermissionError('Fixture sharing error')):
            with self.assertRaises(PermissionError):self.broker.completed_step_result(receipt)
        self.assertEqual(self.broker.completed_step_result(receipt),result)
    def test_incomplete_evidence_blocks_control_without_changing_report(self):
        self.evidence['can_execute']=False
        self.evidence['reason']='Completed STEP report requires reconciliation.'
        with patch.object(b.subprocess,'Popen',side_effect=AssertionError('No launch')):
            for op in ('start','resume','preflight','monitor005'):
                response=self.prepare(op)
                self.assertEqual(response['control_state'],'BLOCKED')
                self.assertIn('reconciliation',response['answer'])
    def test_inspect_preserves_ownership_but_blocks_missing_step_report(self):
        identity=uuid.uuid4().hex
        b.atomic(self.broker.receipt_path,{'request':identity,'operation':'monitor005',
                                         'state':'EXITED','outcome':'STEP_COMPLETED'})
        campaign=Mock();campaign.campaign_root.return_value=self.root/'campaign'
        processes=Mock();processes.process_iter.return_value=[]
        with patch.object(b,'modules',return_value=(None,campaign,None,None,None)),patch.object(b,'psutil_module',return_value=processes),patch.object(b,'probe',return_value='FREE'),patch.object(self.broker,'policy',return_value=self.policy):
            evidence=b.Broker.inspect(self.broker)
        self.assertEqual(evidence['worker'],'STOPPED_VERIFIED')
        self.assertTrue(evidence['ownership_verified']);self.assertFalse(evidence['can_execute'])
        self.assertIn('report',evidence['reason'])
        self.assertTrue(self.broker.receipt_path.exists())
    def test_preflight_single_dispatch_refresh_and_no_auto_gate(self):
        self.policy.update(ready=False,complete=True,unknown_submission=True)
        self.evidence['unknown_submission']=True
        r=self.prepare('preflight')
        self.assertIn('No submission',r['answer'])
        with patch.object(b.subprocess,'Popen') as launch:
            result=self.confirm(r)
            self.assertEqual(result['control_state'],'START_REQUESTED')
            launch.assert_called_once()
            self.assertEqual(json.loads(self.broker.launch_path.read_text())['operation'],'preflight')
            self.s=Supervisor(self.s.path,self.adapter)
            with self.assertRaises(ValueError):self.confirm(r)
            self.s.status();launch.assert_called_once()
    def test_active_step_cannot_attach_pause_or_spawn(self):
        for active in ('preflight','monitor005'):
            self.evidence.update(worker='RUNNING_VERIFIED',operation=active)
            with patch.object(b.subprocess,'Popen',side_effect=AssertionError('No concurrent launch')):
                for op in ('start','resume','pause','preflight','monitor005'):
                    self.assertEqual(self.prepare(op)['control_state'],'BLOCKED')
    def test_monitor_confirmation_fixed_scope_and_once(self):
        result=self.prepare('monitor005')
        self.assertIn('labK',result['answer']);self.assertIn('eight registered',result['answer'])
        with patch.object(b.subprocess,'Popen') as launch:
            self.confirm(result);launch.assert_called_once()
            with self.assertRaises(ValueError):self.confirm(result)
            self.assertEqual(json.loads(self.broker.launch_path.read_text())['operation'],'monitor005')
    def test_http_preflight_and_unsafe_steps_are_deterministic(self):
        from fastapi.testclient import TestClient
        from app import app
        from conversations import Conversations
        store=Conversations(self.root/'conversations.sqlite3')
        with patch('app.supervisor',self.s),patch('app.store',store),patch.object(b.subprocess,'Popen') as launch,patch('local_ai.interpret',side_effect=AssertionError('No LLM')):
            client=TestClient(app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            chat=client.post('/api/conversations').json()['id']
            for command in ('Monitor Jobs','Collect completed jobs','Export figures','Run targeted recovery'):
                response=client.post(f'/api/conversations/{chat}/operator',json={'message':command})
                self.assertEqual(response.json()['control_state'],'BLOCKED')
                self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')
            launch.assert_not_called()
            quick=client.post('/api/ai/analyze',json={'message':'Run preflight'})
            self.assertEqual(quick.status_code,400)
            response=client.post(f'/api/conversations/{chat}/operator',json={'message':'Run preflight'})
            request=response.json()['control_request'];launch.assert_not_called()
            response=client.post(f'/api/conversations/{chat}/controls/{request}/confirm',json={'approved':True})
            self.assertEqual(response.json()['control_state'],'START_REQUESTED');launch.assert_called_once()
            self.evidence['step_result']={'answer':'Fixture preflight completed','rows':[{'account':'fixture','status':'PASS'}]}
            result=client.post(f'/api/conversations/{chat}/operator',json={'message':'Show step result'}).json()
            self.assertEqual(result['rows'][0]['status'],'PASS')
            self.assertIn('Fixture preflight',result['answer'])
            self.assertEqual(client.get('/api/conversations/'+chat).json()['turns'][-1]['response']['rows'],result['rows'])
    def test_http_confirmation_to_broker_fixed_launch_and_refresh(self):
        from fastapi.testclient import TestClient
        from app import app
        from conversations import Conversations
        store=Conversations(self.root/'conversations.sqlite3')
        with patch('app.supervisor',self.s),patch('app.store',store),patch.object(b.subprocess,'Popen') as launch,patch('local_ai.interpret',side_effect=AssertionError('No LLM')):
            client=TestClient(app,base_url='http://127.0.0.1',headers={'X-MXene-AI':'local-only'})
            chat=client.post('/api/conversations').json()['id']
            response=client.post(f'/api/conversations/{chat}/operator',json={'message':'Resume Auto Manager'})
            self.assertEqual(response.headers['X-MXene-Ollama-Calls'],'0')
            request=response.json()['control_request']
            response=client.post(f'/api/conversations/{chat}/controls/{request}/confirm',json={'approved':True})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['control_state'],'START_REQUESTED')
            self.assertEqual(launch.call_count,1)
            self.assertEqual(client.get('/api/conversations/'+chat).json()['turns'][-1]['response']['control_state'],'START_REQUESTED')
    def test_dpapi_secret_not_plaintext(self):
        from broker_client import save_token,read_token
        secret='a'*64;path=self.root/'fixture-token'
        save_token(path,secret)
        self.assertNotIn(secret,path.read_text());self.assertEqual(read_token(path),secret)

class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.process=dict(pid=123,created=123.5,exe='fixed-python',argv=['fixed','command'],campaign=b.CAMPAIGN)
        self.receipt=dict(self.process,state='LOCKED')
    def test_all_identity_fields_and_both_locks_required(self):
        self.assertEqual(b.ownership(self.receipt,self.process,['BUSY','BUSY']),'RUNNING_VERIFIED')
        for field in self.process:
            changed=dict(self.process);changed[field]='wrong'
            self.assertEqual(b.ownership(self.receipt,changed,['BUSY','BUSY']),'UNKNOWN')
        for locks in (['FREE','BUSY'],['BUSY','FREE'],['UNKNOWN','BUSY']):
            self.assertEqual(b.ownership(self.receipt,self.process,locks),'UNKNOWN')
    def test_stopped_crash_and_foreign_owner(self):
        self.assertEqual(b.ownership(None,None,['FREE','FREE']),'STOPPED_VERIFIED')
        self.assertEqual(b.ownership(self.receipt,None,['FREE','FREE']),'STALE_UNRESOLVED')
        self.assertEqual(b.ownership(dict(self.receipt,state='EXITED'),None,['FREE','FREE']),'STOPPED_VERIFIED')
        self.assertEqual(b.ownership(self.receipt,self.process,['BUSY','BUSY'],True),'UNKNOWN')

class WorkerWrapperTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0,str(b.PRODUCTION/'03_python_automation'))
        import worker_runtime
        self.runtime=worker_runtime
        self.root=b.HERE/'state'/('wrapper-fixture-'+uuid.uuid4().hex);self.root.mkdir()
        self.directory=self.root/'campaign';self.request=uuid.uuid4().hex
        self.policy=dict(ready=True,complete=False,unknown_submission=False)
        b.atomic(self.root/'auto_launch.json',dict(request=self.request,operation='resume',policy=self.policy,created=time.time()))
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db, db:
            db.execute('CREATE TABLE requests(id TEXT,state TEXT,request TEXT)')
            db.execute('INSERT INTO requests VALUES(?,?,?)',(self.request,'START_REQUESTED',json.dumps({'operation':'resume'})))
        self.broker=b.Broker(self.root,self.root)
        self.pipeline=Mock();self.campaign=Mock();self.campaign.campaign_root.return_value=self.directory
        for target,value in (('auto_broker.PROJECT',self.root),('auto_broker.STATE',self.root)):
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        p=patch.object(b,'Broker',return_value=self.broker);p.start();self.addCleanup(p.stop)
        p=patch.object(self.broker,'policy',return_value=self.policy);p.start();self.addCleanup(p.stop)
        p=patch.object(b,'modules',return_value=(self.pipeline,self.campaign,self.runtime,Mock(),Mock()));p.start();self.addCleanup(p.stop)
        def clean():
            if self.directory.exists():
                for p in self.directory.iterdir():p.unlink()
                self.directory.rmdir()
            for p in self.root.iterdir():p.unlink()
            self.root.rmdir()
        self.addCleanup(clean)
    def test_wrapper_receipt_only_after_canonical_locks_and_no_replay(self):
        def pipeline(*args,**kwargs):
            self.assertTrue(kwargs['resume']);self.assertFalse(kwargs['once'])
            self.assertFalse(self.broker.receipt_path.exists())
            with self.runtime.campaign_worker_lock(self.directory):
                receipt=json.loads(self.broker.receipt_path.read_text())
                self.assertEqual(receipt['state'],'LOCKED');self.assertEqual(receipt['request'],self.request)
                self.assertEqual(b.probe(self.directory/'worker.lock'),'BUSY')
                self.assertEqual(b.probe(self.root/'auto_manager.lock'),'BUSY')
            return {'pipeline':{'state':'PAUSED'}}
        self.pipeline.run_pipeline.side_effect=pipeline
        b.worker(self.request)
        self.assertEqual(json.loads(self.broker.receipt_path.read_text())['state'],'EXITED')
        self.assertEqual(b.probe(self.directory/'worker.lock'),'FREE')
        with self.assertRaises(sqlite3.IntegrityError):b.worker(self.request)
        self.assertEqual(self.pipeline.run_pipeline.call_count,1)
    def test_quota_block_and_crash_are_not_retried(self):
        self.pipeline.run_pipeline.return_value={'pipeline':{'state':'BLOCKED_AUTHORIZATION'}}
        b.worker(self.request)
        self.assertEqual(json.loads(self.broker.receipt_path.read_text())['outcome'],'BLOCKED_AUTHORIZATION')
        self.assertEqual(self.pipeline.run_pipeline.call_count,1)
    def test_exception_records_crash_not_stopped(self):
        self.pipeline.run_pipeline.side_effect=RuntimeError('fixture')
        with self.assertRaises(RuntimeError):b.worker(self.request)
        self.assertEqual(json.loads(self.broker.receipt_path.read_text())['state'],'CRASHED')
    def test_changed_gate_before_start_never_calls_pipeline(self):
        self.policy['unknown_submission']=True
        with self.assertRaises(ValueError):b.worker(self.request)
        self.pipeline.run_pipeline.assert_not_called()
    def test_wrapper_delegates_canonical_step_and_observes_its_lock(self):
        launch=json.loads(self.broker.launch_path.read_text());launch.update(operation='analyze',n=5)
        b.atomic(self.broker.launch_path,launch)
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db,db:
            db.execute('UPDATE requests SET request=?',(json.dumps({'operation':'analyze','n':5}),))
        def step(project,campaign,operation,**kwargs):
            self.assertEqual(kwargs,{'n':5,'authorize_step':True,'authorize_output_write':True})
            with self.runtime.campaign_worker_lock(self.directory):
                self.assertEqual(json.loads(self.broker.receipt_path.read_text())['state'],'LOCKED')
            return {'operation':'analyze','campaign_state':'PAUSED','result':{'state':'ANALYZED'}}
        self.pipeline.execute_campaign_step.side_effect=step
        b.worker(self.request)
        self.pipeline.execute_campaign_step.assert_called_once()
        self.pipeline.run_pipeline.assert_not_called()
        self.assertEqual(json.loads(self.broker.receipt_path.read_text())['outcome'],'STEP_COMPLETED')
    def test_collect_worker_scope_and_failed_archive_report(self):
        launch=json.loads(self.broker.launch_path.read_text());launch.update(operation='collect',n=30,batch=6,collect_scope_id='a'*64)
        b.atomic(self.broker.launch_path,launch)
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db,db:
            db.execute('UPDATE requests SET request=?',(json.dumps({'operation':'collect','n':30,'batch':6,'collect_scope_id':'a'*64}),))
        def step(project,campaign,operation,**kwargs):
            self.assertEqual(kwargs,{'n':30,'batch':6,'collect_scope_id':'a'*64,'authorize_step':True,'authorize_output_write':True})
            with self.runtime.campaign_worker_lock(self.directory):
                self.assertEqual(json.loads(self.broker.receipt_path.read_text())['state'],'LOCKED')
            return {'operation':'collect','campaign_state':'PAUSED','result':{'state':'COLLECTION_INCOMPLETE','archive_and_ledger_verified':False,'collected_jobs':0}}
        self.pipeline.execute_campaign_step.side_effect=step
        b.worker(self.request)
        self.pipeline.execute_campaign_step.assert_called_once();self.pipeline.run_pipeline.assert_not_called()
        self.assertEqual(json.loads(self.broker.receipt_path.read_text())['outcome'],'STEP_FAILED')
    def test_manual_worker_propagates_scope_and_status_without_write_grant(self):
        scope={'kind':'job','action':'monitor','job_id':'registered','scope_id':'a'*64}
        launch=json.loads(self.broker.launch_path.read_text());launch.update(operation='manual',n=30,manual_scope=scope)
        b.atomic(self.broker.launch_path,launch)
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db,db:
            db.execute('UPDATE requests SET request=?',(json.dumps({'operation':'manual','n':30,'manual_scope':scope}),))
        def step(project,campaign,operation,**kwargs):
            self.assertEqual(kwargs,{'n':30,'manual_scope':scope,'authorize_step':True,'authorize_output_write':False})
            with self.runtime.campaign_worker_lock(self.directory):
                self.assertEqual(json.loads(self.broker.receipt_path.read_text())['manual_scope'],scope)
            return {'operation':'manual','campaign_state':'PAUSED','result':{'state':'OBSERVED','collected_jobs':0,'scope':scope}}
        self.pipeline.execute_campaign_step.side_effect=step
        b.worker(self.request)
        self.pipeline.execute_campaign_step.assert_called_once();self.pipeline.run_pipeline.assert_not_called()
        result=self.broker.completed_step_result(json.loads(self.broker.receipt_path.read_text()))
        self.assertEqual(result['rows'][0]['collected_jobs'],0)
        self.assertEqual(result['rows'][0]['scope'],scope)
    def test_export_result_displays_publication_destination_and_count(self):
        launch=json.loads(self.broker.launch_path.read_text());launch.update(operation='export',n=25)
        b.atomic(self.broker.launch_path,launch)
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db,db:
            db.execute('UPDATE requests SET request=?',(json.dumps({'operation':'export','n':25}),))
        destination=str(self.root/'output/01_results_and_figures/n=25')
        self.pipeline.execute_campaign_step.return_value={'operation':'export','campaign_state':'PAUSED',
            'result':{'state':'EXPORTED','figure_directory':destination,'png_count':10,'files_verified':True}}
        b.worker(self.request)
        result=self.broker.completed_step_result(json.loads(self.broker.receipt_path.read_text()))
        self.assertIn('10 PNGs verified in '+destination,result['answer'])
        self.assertEqual(result['rows'][0]['figure_directory'],destination)
        self.pipeline.run_pipeline.assert_not_called()
    def test_canonical_not_ready_is_reported_without_auto_advance(self):
        launch=json.loads(self.broker.launch_path.read_text());launch.update(operation='export',n=30)
        b.atomic(self.broker.launch_path,launch)
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db,db:
            db.execute('UPDATE requests SET request=?',(json.dumps({'operation':'export','n':30}),))
        class Blocked(RuntimeError):pass
        self.pipeline.CampaignStepBlocked=Blocked
        self.pipeline.execute_campaign_step.side_effect=Blocked('Complete dataset required')
        b.worker(self.request)
        result=json.loads((self.root/('step-'+self.request+'.json')).read_text())
        self.assertEqual(result['control_state'],'STEP_FAILED')
        self.assertIn('Complete dataset required',result['answer'])
        self.assertEqual(result['operation_status'],'BLOCKED')
        self.assertEqual(result['failure_reason'],'Complete dataset required')
        self.pipeline.run_pipeline.assert_not_called()
    def test_canonical_crash_keeps_failure_reason_and_never_retries(self):
        launch=json.loads(self.broker.launch_path.read_text());launch.update(operation='analyze',n=25)
        b.atomic(self.broker.launch_path,launch)
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db,db:
            db.execute('UPDATE requests SET request=?',(json.dumps({'operation':'analyze','n':25}),))
        class Blocked(RuntimeError):pass
        self.pipeline.CampaignStepBlocked=Blocked
        self.pipeline.execute_campaign_step.side_effect=OSError('Fixture artifact write failed')
        with self.assertRaises(OSError):b.worker(self.request)
        receipt=json.loads(self.broker.receipt_path.read_text())
        result=self.broker.step_observation(receipt,launch,'STALE_UNRESOLVED')
        self.assertEqual(result['operation_status'],'FAILED')
        self.assertIn('Fixture artifact write failed',result['failure_reason'])
        with self.assertRaises(sqlite3.IntegrityError):b.worker(self.request)
        self.pipeline.execute_campaign_step.assert_called_once()
        self.pipeline.run_pipeline.assert_not_called()
    def test_preflight_one_call_under_real_fixture_locks_no_pipeline(self):
        launch=json.loads(self.broker.launch_path.read_text());launch['operation']='preflight'
        b.atomic(self.broker.launch_path,launch)
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db, db:
            db.execute('UPDATE requests SET request=?',(json.dumps({'operation':'preflight'}),))
        def step(project):
            self.assertEqual(project,self.root)
            self.assertEqual(b.probe(self.directory/'worker.lock'),'BUSY')
            self.assertEqual(b.probe(self.root/'auto_manager.lock'),'BUSY')
            return {'answer':'Fixture checks','rows':[{'status':'FAIL'}],'control_state':'STEP_COMPLETED'}
        with patch.object(b,'preflight_step',side_effect=step) as call:
            b.worker(self.request);call.assert_called_once()
            with self.assertRaises(sqlite3.IntegrityError):b.worker(self.request)
            call.assert_called_once()
        self.pipeline.run_pipeline.assert_not_called()
        self.assertEqual(json.loads(self.broker.receipt_path.read_text())['outcome'],'STEP_COMPLETED')
        self.assertEqual(json.loads((self.root/('step-'+self.request+'.json')).read_text())['rows'][0]['status'],'FAIL')
        self.assertEqual(b.probe(self.directory/'worker.lock'),'FREE')
    def test_monitor_one_operation_under_fixture_locks_unknown_does_not_submit(self):
        self.policy['unknown_submission']=True
        launch=json.loads(self.broker.launch_path.read_text())
        launch.update(operation='monitor005',policy=self.policy)
        b.atomic(self.broker.launch_path,launch)
        with closing(sqlite3.connect(self.root/'worker_control.sqlite3')) as db, db:
            db.execute('UPDATE requests SET request=?',(json.dumps({'operation':'monitor005'}),))
        def step(project):
            self.assertEqual(b.probe(self.directory/'worker.lock'),'BUSY')
            return {'answer':'Mock provider PENDING; no collection.','rows':[],'control_state':'STEP_COMPLETED'}
        with patch.object(b,'monitor005_step',side_effect=step) as call:
            b.worker(self.request);call.assert_called_once_with(self.root)
        self.pipeline.run_pipeline.assert_not_called()
        self.assertEqual(json.loads(self.broker.receipt_path.read_text())['state'],'EXITED')

if __name__=='__main__':unittest.main()
