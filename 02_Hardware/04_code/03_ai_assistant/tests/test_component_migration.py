"""Offline compatibility checks for the three-component relocation."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from test_account_integration import AI, ROOT
from diagnostic_manager import child_environment


def statistics_module():
    spec=importlib.util.spec_from_file_location('migration_statistics',AI/'03_python_automation/dependence_statistics.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class ComponentMigrationTests(unittest.TestCase):
    def test_root_modules_are_only_wrappers(self):
        wrappers=[]
        for p in AI.glob('*.py'):
            if p.name in ('__init__.py','_migration_bridge.py'):continue
            tree=ast.parse(p.read_text())
            self.assertFalse(any(isinstance(n,(ast.FunctionDef,ast.ClassDef)) for n in tree.body),p.name)
            self.assertIn('_load(globals(),',p.read_text());wrappers.append(p)
        self.assertEqual(len(wrappers),26)

    def test_canonical_imports_and_patch_identity(self):
        import batch_scheduler,independent_n_campaign,quota,worker_runtime,ai_assistance,mxene
        for module in (batch_scheduler,independent_n_campaign,quota,worker_runtime):
            self.assertEqual(Path(module.__file__).parent,AI/'03_python_automation')
        for module in (ai_assistance,mxene):self.assertEqual(Path(module.__file__).parent,AI/'01_ai_agent')
        with patch.object(quota,'available_quota_seconds',return_value=123):
            from quota import available_quota_seconds
            self.assertEqual(available_quota_seconds(),123)

    def test_configuration_and_science_paths_stay_fixed(self):
        from final_configuration import load_final_config
        spec=importlib.util.spec_from_file_location('migration_assistant_paths',AI/'03_python_automation/repository_paths.py')
        paths=importlib.util.module_from_spec(spec);spec.loader.exec_module(paths)
        from research_pipeline import AI_DIR,VQE_DIR
        self.assertEqual(AI_DIR,AI);self.assertEqual(VQE_DIR,AI.parent/'01_vqe')
        self.assertTrue(load_final_config(ROOT))
        self.assertEqual(paths.pilot_script(ROOT),AI.parent/'01_vqe/mitigation_pilot.py')
        self.assertEqual(paths.scientific_model_path(ROOT),ROOT/'01_quantum_eis_fit/04_code/vqe')
        self.assertTrue((AI/'03_python_automation/mitigation_pilot.py').is_file())

    def test_legacy_and_canonical_cli_help(self):
        for p in (AI/'mxene.py',AI/'01_ai_agent/mxene.py',AI/'diagnostic_manager.py',
                  AI/'03_python_automation/diagnostic_manager.py',AI/'03_python_automation/dependence_statistics.py'):
            with self.subTest(path=p):
                result=subprocess.run([sys.executable,'-B',str(p),'--help'],cwd=ROOT,
                    capture_output=True,text=True,timeout=25,env=child_environment())
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('usage:',result.stdout)

    def test_comparison_uses_canonical_write_gate(self):
        module=statistics_module()
        with patch('campaign_comparison.main',return_value=20) as gate:
            self.assertEqual(module.main([]),20);gate.assert_called_once_with([])
        self.assertEqual(module.BASE,AI.parent)
        spec=importlib.util.spec_from_file_location('migration_legacy_comparison',AI.parent/'01_vqe/compare_105_pubs.py')
        legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
        import dependence_statistics
        self.assertIs(legacy.generate_figure_c,dependence_statistics.generate_figure_c)
        self.assertIs(legacy.generate_figure_d,dependence_statistics.generate_figure_d)
        self.assertEqual(legacy.TREATMENTS,dependence_statistics.COMPARISON_TREATMENTS)
        self.assertEqual(Path(dependence_statistics.__file__),AI/'03_python_automation/dependence_statistics.py')

    def test_statistics_import_has_no_native_modules_or_network(self):
        code='''
import sys,socket
from pathlib import Path
socket.socket.connect=lambda *a: (_ for _ in ()).throw(AssertionError('network'))
sys.path.insert(0,sys.argv[1])
import dependence_statistics
assert not {'numpy','scipy','matplotlib','qiskit_ibm_runtime'} & set(sys.modules)
assert Path(dependence_statistics.__file__).name=='dependence_statistics.py'
'''
        result=subprocess.run([sys.executable,'-B','-c',code,str(AI/'03_python_automation')],cwd=ROOT,
            capture_output=True,text=True,timeout=25,env=child_environment())
        self.assertEqual(result.returncode,0,result.stderr)

    def test_offline_resume_cannot_enable_live(self):
        from research_pipeline import run_pipeline
        from test_account_integration import scratch_directory
        with scratch_directory() as folder,patch('socket.socket.connect',side_effect=AssertionError('network')):
            result=run_pipeline(ROOT,'unplanned-migration-fixture',mode='offline',resume=True,
                                registry_path=folder/'absent.db')
            self.assertFalse(result['submission_performed'])
            self.assertFalse((folder/'absent.db').exists())

    def test_bridge_rejects_unapproved_locations(self):
        from _migration_bridge import load
        with self.assertRaises(ValueError):load({'__file__':str(AI/'quota.py')},'../elsewhere')
        with self.assertRaises(ValueError):load({'__file__':str(ROOT/'quota.py')},'03_python_automation')


if __name__=='__main__':unittest.main()
