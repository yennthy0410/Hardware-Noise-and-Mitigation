"""Documentation contracts: no manager imports, production writes or network."""
import json, re, unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import app
BASE=Path(__file__).resolve().parent
class GuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc=json.loads((BASE/'static/guide.json').read_text(encoding='utf-8'))
        cls.client=TestClient(app,base_url='http://127.0.0.1')
    def test_ten_sections_unique_anchors(self):
        self.assertEqual(len(self.doc['sections']),10)
        ids=[x['id'] for s in self.doc['sections'] for x in [s]+s.get('topics',[])]
        self.assertEqual(len(ids),len(set(ids)))
        for id in ids:self.assertRegex(id,r'^[a-z][a-z-]+$')
    def test_all_related_links_exist(self):
        ids={x['id'] for s in self.doc['sections'] for x in [s]+s.get('topics',[])}
        for s in self.doc['sections']:
            for target in s.get('related',[]):self.assertIn(target,ids)
    def test_local_assets_available(self):
        for name in ('guide.json','guide.js','app.js','styles.css'):
            response=self.client.get('/static/'+name)
            self.assertEqual(response.status_code,200)
    def test_commands_exact_local_scripts(self):
        commands={c['id']:c['code'] for s in self.doc['sections'] for c in s.get('commands',[])}
        self.assertEqual(commands['start'],f"Set-Location '{BASE}'\n.\\start_all.ps1")
        self.assertEqual(commands['health'],'Invoke-RestMethod http://127.0.0.1:8766/api/health')
        for name in ('start_dashboard.ps1','test_dashboard.py','test_guard.py'):self.assertTrue((BASE/name).exists())
        for code in commands.values():
            self.assertNotIn('--live',code);self.assertNotIn('03_ai_assistant',code)
    def test_no_external_document_assets(self):
        html=(BASE/'static/index.html').read_text(encoding='utf-8')
        self.assertNotRegex(html,r'(?:src|href)=["\']https?://')
        js=(BASE/'static/guide.js').read_text(encoding='utf-8')
        self.assertIn("fetch('/static/guide.json')",js)
        self.assertNotIn('eval(',js)
    def test_sources_and_limits_present(self):
        text=json.dumps(self.doc)
        for term in ('BLOCKED','PLANNED','UNKNOWN','150','40','READ-ONLY','LOCAL WRITE','IBM-SIDE ACTION','Ctrl+C'):
            self.assertIn(term,text)
    def test_current_tabs_and_book(self):
        js=(BASE/'static/app.js').read_text(encoding='utf-8')
        names=re.search(r'const tabs=\[(.*?)\];',js).group(1)
        self.assertEqual(len(re.findall(r"'[^']+'",names)),7)
        self.assertIn('&#128214;',js)
    def test_help_keyboard_and_mobile_contract(self):
        html=(BASE/'static/index.html').read_text(encoding='utf-8')
        css=(BASE/'static/styles.css').read_text(encoding='utf-8')
        self.assertIn('id="context-help" aria-label=',html)
        self.assertIn(':focus-visible',css)
        self.assertIn('@media(max-width:560px)',css)
        self.assertIn('.guide-layout{grid-template-columns:1fr}',css)
if __name__=='__main__':unittest.main(verbosity=2)
