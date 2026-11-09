import json, unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import research
from research import ToolRequest,select_question,execute,validate_selection

class ReaderFixture:
    def status(self):
        return dict(levels=[dict(target_n=5,valid=40)],artifacts=[dict(level=5,name=name,id=name,version='v1',sha256='fixture') for name in ('params_table.csv','per_pub_validation.csv')],recovery_summary=[dict(n=5,treatment=label,slots=[dict(logical_pub_slot=i+1,accepted_job_id=tid) for i in range(5)]) for tid,label in research.LABELS.items()],captured_at='fixture')

class ResearchTests(unittest.TestCase):
    def test_incomplete_level_has_no_invented_scientific_metrics(self):
        result=execute(ReaderFixture(),ToolRequest(levels=[30]))
        self.assertEqual(result['rows'],[])
        self.assertIn('Scientific analysis is not yet available for n=30.',result['sentences'])
    def rows(self):
        return [dict(n='5',treatment=tid,IBM_job_id=tid,PUB_index=str(i),logical_pub_index=str(i+1),selected_for_repaired_dataset='True',SSE=str(i+1),RMSE=str(i+1),Rct=str(i+1)) for tid in research.LABELS for i in range(5)]
    def run_tool(self,**kwargs):
        with patch('research.read_rows',return_value=self.rows()):return execute(ReaderFixture(),ToolRequest(**kwargs))
    def test_natural_scope(self):
        q=select_question('Plot RMSE versus n for all eight treatments')
        self.assertEqual(q.levels,list(research.LEVELS));self.assertEqual(q.metric,'RMSE');self.assertEqual(q.tool,'generate_derived_chart')
    def test_followup(self):
        h=[SimpleNamespace(role='user',content='Compare Rct at n=10 for DD+ZNE')]
        q=select_question('Why this variability?',h)
        self.assertEqual(q.levels,[10]);self.assertEqual(q.metric,'Rct');self.assertEqual(q.treatments,['M5_DD_ZNE'])
    def test_statistics(self):
        r=self.run_tool();self.assertEqual(r['rows'][0]['median'],3);self.assertEqual(r['rows'][0]['count'],5)
        self.assertEqual(r['rows'][0]['improvement_pct'],0)
        self.assertIn('shared execution',r['warning'].lower())
    def test_no_invented_intervals(self):
        r=self.run_tool(tool='calculate_uncertainty');self.assertNotIn('ci',r['rows'][0]);self.assertIn('unavailable',r['warning'])
    def test_missing(self):
        r=self.run_tool(levels=[30]);self.assertEqual(r['rows'],[]);self.assertEqual(r['missing'],[30])
    def test_duplicate_refused(self):
        rows=self.rows();rows.append(rows[0])
        with patch('research.read_rows',return_value=rows),self.assertRaises(ValueError):execute(ReaderFixture(),ToolRequest())
    def test_nonnumeric_refused(self):
        rows=self.rows();rows[0]['SSE']='nan'
        with patch('research.read_rows',return_value=rows),self.assertRaises(ValueError):execute(ReaderFixture(),ToolRequest())
    def test_arguments(self):
        with self.assertRaises(ValueError):ToolRequest(tool='exec',levels=[5])
        with self.assertRaises(ValueError):ToolRequest(levels=[500])
        with self.assertRaises(ValueError):self.run_tool(treatments=['shell'])
    def test_chart_exact(self):
        r=self.run_tool(tool='generate_derived_chart');self.assertEqual(len(r['chart']['series']),8);self.assertEqual(r['chart']['series'][0]['y'],[3])
    def test_claims_rejected(self):
        self.assertEqual(validate_selection('{"sentence_ids":[0]}',['mean 3']),['mean 3'])
        for s in ('{"answer":"mean 999"}','{"sentence_ids":[99]}','{"sentence_ids":[true]}'):
            with self.assertRaises(ValueError):validate_selection(s,['mean 3'])
    def test_traceability_internal(self):
        r=self.run_tool();self.assertEqual(r['evidence'][0]['sha256'],'fixture')
        self.assertNotIn('fixture',' '.join(r['sentences']))
        s=Path('static/chat.js').read_text(encoding='utf-8');self.assertNotIn('d.sources',s);self.assertNotIn('chat-job',s);self.assertIn('mxene-local-context-permission',s)
    def test_model_failure_honest(self):
        import local_ai
        req=SimpleNamespace(consent=True,message='Explain n=5',history=[],n=5,model='')
        with patch('research.execute',return_value=self.run_tool()),patch('local_ai.models',side_effect=ConnectionError),self.assertRaises(ConnectionError):local_ai.interpret(ReaderFixture(),req)
    def test_isolated_artifacts(self):
        self.assertTrue((research.HERE/'state'/'research').is_relative_to(research.HERE))
        from tempfile import gettempdir
        with patch('research.save_evidence') as save,patch('local_ai.models',return_value=['local']),patch('local_ai.call',return_value={'message':{'content':'{"sentence_ids":[0]}'}}),patch('research.execute',return_value=self.run_tool()):
            import local_ai
            r=local_ai.interpret(ReaderFixture(),SimpleNamespace(consent=True,message='n=5',history=[],n=5,model='local'))
            self.assertNotIn('evidence',r);save.assert_called_once()
    def test_research_navigation(self):
        js=Path('static/app.js').read_text(encoding='utf-8').splitlines()[0]
        self.assertNotIn('IBM Jobs',js);self.assertNotIn('Settings & Logs',js)
        s=Path('static/workspace.js').read_text(encoding='utf-8')
        self.assertIn("subnav(['Research Summary','System Architecture']",s)

if __name__=='__main__':unittest.main()
