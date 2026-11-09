import json,sqlite3,unittest
from jobs_view import project_jobs,TREATMENTS,LABELS

class JobsTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        common='target_id TEXT, logical_block_id TEXT, treatment_id TEXT, account_label TEXT, backend TEXT, job_id TEXT, batch_id TEXT, metadata_json TEXT, input_hashes_json TEXT, quota_reserved_seconds REAL, qpu_usage_seconds REAL, attempt INTEGER, updated_at TEXT'
        self.db.executescript(f'''
        CREATE TABLE independent_n_campaign_levels(campaign_id,target_id,target_n);
        CREATE TABLE jobs(id INTEGER PRIMARY KEY,{common},submission_state TEXT,submitted_pub_count INTEGER,chunk_n INTEGER,validated_at TEXT,validated_pub_count INTEGER,invalid_pub_count INTEGER);
        CREATE TABLE pub_replacement_jobs(replacement_id INTEGER PRIMARY KEY,{common},state TEXT,source_pub_index INTEGER,logical_pub_index INTEGER,source_account_label TEXT,raw_archive_sha256 TEXT,uncertainty_state TEXT,error_codes_json TEXT,expectation_values_json TEXT);
        CREATE TABLE batch_monitor_state(checked_at,snapshot_json);
        CREATE TABLE pub_validation_records(job_row_id,pub_index,expectation_valid,uncertainty_valid,uncertainty_state,error_codes_json,source_sha256,created_at);
        CREATE TABLE recovery_events(job_row_id,event_type,created_at);
        CREATE TABLE batch_monitor_events(job_row_id,event_type,created_at);
        CREATE TABLE pub_replacement_events(replacement_id,event_type,created_at);
        ''')
        self.accepted={};self.ids=[]
        for n in (5,10,15,20,25,30):
            target=f'n{n}';self.db.execute('INSERT INTO independent_n_campaign_levels VALUES(?,?,?)',('campaign',target,n))
            for t in TREATMENTS:
                for batch in (1,2):
                    job=f'{target}-{t}-{batch}';rid=self.add('jobs',target,t,job,block=f'block-{batch:03d}');self.ids.append(rid)
                    for pub in range(1,6):self.db.execute('INSERT INTO pub_validation_records VALUES(?,?,?,?,?,?,?,?)',(rid,pub,1,1,'PRESENT','[]','hash','2026-01-01'))
                    observation={'job_id':job,'status':'DONE','observed_utc':'2026-01-01T01:00:00Z','timestamps':{'created':'2026-01-01T00:00:00Z','running':'2026-01-01T00:05:00Z','finished':'2026-01-01T00:06:00Z'}}
                    for _ in range(2):self.db.execute('INSERT INTO batch_monitor_state VALUES(?,?)',('2026-01-01',json.dumps({'jobs':[observation]})))
        for pub in (1,2):self.add('pub_replacement_jobs','n30',TREATMENTS[3],'replacement-shared',pub=pub)
    def tearDown(self):self.db.close()
    def add(self,table,target,treatment,job,block='block-003',pub=1):
        values=dict(target_id=target,logical_block_id=block,treatment_id=treatment,account_label='labA',backend='backend',job_id=job,batch_id=block,metadata_json='{}',input_hashes_json='{"circuit":"hash"}',quota_reserved_seconds=150,qpu_usage_seconds=12,attempt=1,updated_at='2026-01-02T00:00:00Z')
        if table=='jobs':values.update(submission_state='COMPLETED',submitted_pub_count=5,chunk_n=5)
        else:values.update(state='VALIDATED',source_pub_index=pub,logical_pub_index=pub,uncertainty_state='PRESENT',error_codes_json='[]')
        return self.db.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",tuple(values.values())).lastrowid
    def view(self):return project_jobs(self.db,'campaign',self.accepted)
    def test_six_levels_eight_treatments_multibatch(self):
        rows=self.view()
        for n in (5,10,15,20,25,30):
            original=[r for r in rows if r['target_n']==n and r['recovery']=='Original']
            self.assertEqual(len(original),16);self.assertEqual({r['treatment'] for r in original},set(LABELS))
    def test_poll_records_not_executions(self):self.assertEqual(len(self.view()),97)
    def test_grouped_replacements_preserve_slots_and_usage(self):
        row=next(r for r in self.view() if r['job_id']=='replacement-shared')
        self.assertEqual(row['expected_pubs'],2);self.assertEqual(row['valid_pubs'],2)
        self.assertEqual(len(row['provenance']),2);self.assertEqual(row['runtime_seconds'],12);self.assertEqual(row['reserved_seconds'],150)
    def test_missing_timestamps_and_validation_not_fabricated(self):
        rid=self.add('jobs','n5',TREATMENTS[0],'unfinished')
        self.db.execute('UPDATE jobs SET submission_state=?,qpu_usage_seconds=NULL WHERE id=?',('SUBMITTED',rid))
        row=next(r for r in self.view() if r['job_id']=='unfinished')
        for k in ('status','observed_at','submitted','queued','started','finished','valid_pubs','invalid_pubs','runtime_seconds'):self.assertIsNone(row[k],k)
        self.assertEqual(row['validation_state'],'Not fully validated')
    def test_done_not_scientific_acceptance(self):
        self.db.execute('UPDATE pub_validation_records SET expectation_valid=0 WHERE job_row_id=? AND pub_index=1',(self.ids[0],))
        row=self.view()[0];self.assertEqual(row['status'],'DONE');self.assertEqual(row['valid_pubs'],4);self.assertEqual(row['invalid_pubs'],1);self.assertEqual(row['validation_state'],'Partial / invalid');self.assertEqual(row['accepted_pubs'],0)
    def test_unknown_identity_retained(self):
        rid=self.add('jobs','n5',TREATMENTS[0],None);self.db.execute('UPDATE jobs SET submission_state=? WHERE id=?',('UNKNOWN',rid))
        row=next(r for r in self.view() if r['job_id'] is None);self.assertEqual(row['ledger_states'],['UNKNOWN']);self.assertIsNone(row['status'])
    def test_treatment_mapping_and_default_order(self):
        rows=[r for r in self.view() if r['target_n']==5]
        self.assertEqual([r['treatment'] for r in rows],[label for label in LABELS for _ in range(2)])
        self.assertEqual([r['block'] for r in rows[:2]],['block-001','block-002'])
    def test_accepted_selection_separate(self):
        job=f'n5-{TREATMENTS[0]}-1';self.accepted={'n5':[{'job_id':job,'account_label':'labA','treatment_id':TREATMENTS[0],'logical_pub_index':1}]}
        self.assertEqual(self.view()[0]['accepted_pubs'],1);self.assertEqual(self.view()[0]['valid_pubs'],5)
    def test_event_history_deduplicated_and_readonly(self):
        for _ in range(2):self.db.execute('INSERT INTO batch_monitor_events VALUES(?,?,?)',(self.ids[0],'REMOTE_DONE','2026-01-01'))
        before=self.db.total_changes;row=self.view()[0];self.assertEqual(row['events'],[{'event':'REMOTE_DONE','timestamp':'2026-01-01'}]);self.assertEqual(self.db.total_changes,before)
    def test_unsubmitted_plan_not_execution(self):
        rid=self.add('pub_replacement_jobs','n5',TREATMENTS[0],None);self.db.execute('UPDATE pub_replacement_jobs SET state=? WHERE replacement_id=?',('PLANNED',rid));self.assertEqual(len(self.view()),97)
if __name__=='__main__':unittest.main(verbosity=2)
