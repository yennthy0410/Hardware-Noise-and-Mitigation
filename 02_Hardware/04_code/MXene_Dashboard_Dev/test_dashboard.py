"""Offline isolated fixtures; never initialize a production registry."""
import os, sqlite3, unittest, uuid
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
import reader
from app import app

class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=reader.HERE/'state'/('tests-'+uuid.uuid4().hex[:8])
        cls.base.mkdir()
        cls.client=TestClient(app,base_url='http://127.0.0.1')
    def test_snapshot_consistency_and_source_hash(self):
        src=self.base/'source.sqlite3';dst=self.base/'copy.sqlite3'
        with sqlite3.connect(src) as db: db.execute('CREATE TABLE evidence(id PRIMARY KEY, value)');db.execute('INSERT INTO evidence VALUES(1,42)')
        before=reader.sha(src);reader.snapshot(src,dst)
        with sqlite3.connect(dst) as db:self.assertEqual(db.execute('SELECT value FROM evidence').fetchone()[0],42)
        self.assertEqual(before,reader.sha(src))
    def test_missing_source_does_not_create_database(self):
        path=self.base/'absent.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):reader.snapshot(path,self.base/'absent-copy.sqlite3')
        self.assertFalse(path.exists())
    def test_html_and_static(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertIn('MXene Observatory',self.client.get('/').text)
        self.assertEqual(self.client.get('/static/app.js').status_code,200)
    def test_get_only(self):
        self.assertEqual(self.client.post('/api/status').status_code,405)
        self.assertEqual(self.client.post('/api/submit').status_code,404)
    def test_host_validation(self):self.assertEqual(self.client.get('/',headers={'Host':'evil.example'}).status_code,400)
    def test_no_path_api(self):
        self.assertEqual(self.client.get('/api/artifacts/not-a-catalog-id').status_code,404)
        self.assertEqual(self.client.get('/static/%2e%2e/reader.py').status_code,404)
    def test_health_has_no_execution(self):
        result=self.client.get('/api/health').json()
        self.assertFalse(result['ibm_network']);self.assertFalse(result['ai_execution'])
    def test_security_headers(self):self.assertIn("default-src 'self'",self.client.get('/').headers['content-security-policy'])
    def test_csv_pagination(self):
        path=self.base/'data.csv';path.write_text('id,value\n'+''.join(f'{n},{n*2}\n' for n in range(230)))
        r=reader.Reader();r.files['fixture']=(path,reader.sha(path))
        self.assertEqual(len(r.table('fixture',0)['rows']),100)
        self.assertEqual(r.table('fixture',1)['rows'][0]['id'],'100')
        self.assertEqual(len(r.table('fixture',2)['rows']),30)
    def test_changed_artifact_refused(self):
        path=self.base/'changed.csv';path.write_text('a\n1');r=reader.Reader();r.files['x']=(path,reader.sha(path));path.write_text('a\n2')
        with self.assertRaises(ValueError):r.artifact('x')
    def test_failure_returns_stale(self):
        r=reader.Reader();r.cached={'valid':7};r.at=0
        with patch.object(reader,'snapshot',side_effect=TimeoutError):result=r.status()
        self.assertEqual(result['valid'],7);self.assertTrue(result['offline'])
    def test_poll_cache(self):
        r=reader.Reader();r.cached={'valid':7};r.at=reader.time.monotonic()
        with patch.object(reader,'snapshot') as backup:self.assertEqual(r.status()['valid'],7);backup.assert_not_called()
    def test_table_page_bound(self):self.assertEqual(self.client.get('/api/tables/x?page=-1').status_code,422)
    def test_sensitive_json_refused(self):
        path=self.base/'sensitive.json';path.write_text('{"nested":{"api_key":"fixture-only"}}')
        r=reader.Reader();r.files['secret']=(path,reader.sha(path))
        with self.assertRaises(ValueError):r.artifact('secret')

if __name__=='__main__':unittest.main(verbosity=2)
