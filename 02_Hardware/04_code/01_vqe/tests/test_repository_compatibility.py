"""Offline compatibility tests. Writes use verified owner-marked scratch."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from contextlib import ExitStack

CODE = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(CODE))
import repository_paths as paths
from repository_paths import create_scratch_dir, cleanup_scratch_dir

def load_script(name):
    spec = importlib.util.spec_from_file_location("audit_" + name, CODE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def fake_repository(root):
    for directory in (paths.CODE_REL, paths.STUDY_REL, paths.MITIGATION_REL / "output", Path("data")):
        (root / directory).mkdir(parents=True, exist_ok=True)
    for name in ("hea_ansatz.py", "parameter_mapping.py", "impedance_model.py"):
        (root / paths.STUDY_REL / name).write_text("# import fixture\n")
    (root / "data/EIS14.csv").write_text("fixture\n")
    (root / paths.CODE_REL / "mitigation_pilot.py").write_text("# fixture\n")
    return root

class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.scratch=create_scratch_dir("vc",CODE.parents[2])
        self.addCleanup(cleanup_scratch_dir,self.scratch,CODE.parents[2])
        self.root = fake_repository(self.scratch/"r")
        self.runtime = MagicMock()
        self.runtime.QiskitRuntimeService.side_effect = AssertionError("IBM is forbidden")
        self.runtime.EstimatorV2.side_effect = AssertionError("Submission is forbidden")
        self.modules = patch.dict(sys.modules, {"numpy": MagicMock(), "qiskit_ibm_runtime": self.runtime})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.pilot = load_script("mitigation_pilot")

    def args(self, **values):
        return types.SimpleNamespace(project=str(self.root), source=None, round="n=5", **values)

    def test_root_validation(self):
        self.assertEqual(paths.repository_root(self.root), self.root.resolve())
        with self.assertRaises(FileNotFoundError):
            paths.repository_root(self.root / "data")

    def test_discovery_from_different_cwd(self):
        with patch("pathlib.Path.cwd", return_value=self.root / "data"):
            # Explicit root is independent of process cwd.
            self.assertEqual(paths.repository_root(self.root), self.root.resolve())

    def test_missing_markers(self):
        (self.root / "data/EIS14.csv").unlink()
        with self.assertRaises(FileNotFoundError):
            paths.repository_root(self.root)

    def test_output_contract(self):
        for folder in paths.OUTPUT_FOLDERS:
            target = paths.mitigation_output(self.root, folder, "n=5")
            self.assertTrue(target.is_relative_to(self.root / paths.MITIGATION_REL / "output"))
            self.assertFalse(target.exists())

    def test_traversal_rejected(self):
        for name in ("../outside", "..", "a/b", "a\\b", "C:escape"):
            with self.assertRaises(ValueError):
                paths.round_name(name)
        with self.assertRaises(ValueError):
            paths.mitigation_output(self.root, "../../../outside")

    def test_pilot_target(self):
        target = paths.pilot_script(self.root)
        self.assertEqual(target, self.root / paths.CODE_REL / "mitigation_pilot.py")
        with self.assertRaises(ValueError):
            paths.pilot_script(self.root, self.root / "other.py")

    def test_frozen_missing_no_preparation_or_network(self):
        before = sorted(str(p) for p in self.root.rglob("*"))
        with self.assertRaisesRegex(FileNotFoundError, "circuit.qpy.*theta.csv.*observables.json"):
            self.pilot.prepare(self.args())
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))
        self.runtime.QiskitRuntimeService.assert_not_called()
        self.runtime.EstimatorV2.assert_not_called()

    def test_explicit_missing_artifacts(self):
        with self.assertRaises(FileNotFoundError) as error:
            paths.frozen_source(self.root, self.root / "original")
        for name in paths.FROZEN_NAMES:
            self.assertIn(str(self.root / "original" / name), str(error.exception))

    def test_unlocked_bundle_not_adopted(self):
        folder = paths.mitigation_output(self.root, "03_IBM_execution_records", "locked_inputs")
        folder.mkdir(parents=True)
        for name in paths.FROZEN_NAMES:
            (folder / name).write_bytes(b"NON-SCIENTIFIC TEST FIXTURE")
        with self.assertRaises(FileNotFoundError):
            paths.frozen_source(self.root)
        # Hash test only; these fixture bytes are never passed to Qiskit or preparation.
        self.assertEqual(set(paths.source_hashes(folder)), set(paths.FROZEN_NAMES))

    def test_preflight_never_prepares(self):
        with patch.object(self.pilot, "prepare", side_effect=AssertionError("implicit prepare")) as prepare:
            with self.assertRaises(FileNotFoundError):
                self.pilot.preflight(self.args())
        prepare.assert_not_called()

    def test_legacy_migration_disabled(self):
        with patch("shutil.move", side_effect=AssertionError("move")) as move:
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                self.pilot.migrate_legacy_job_files(self.root, "n=5")
        move.assert_not_called()

    def test_json_preserves_history(self):
        target = self.root / "historical.json"
        target.write_text('{"original": true}')
        before = target.read_bytes()
        with self.assertRaises(FileExistsError):
            self.pilot.js({"replacement": True}, target)
        self.assertEqual(target.read_bytes(), before)

    def test_submission_needs_explicit_authorization(self):
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            self.pilot.submit(self.args(submit=False))
        self.runtime.EstimatorV2.assert_not_called()

    def test_imports_have_no_io_or_ibm(self):
        names = ("mitigation_pilot", "run_multiple_n", "qpu_auto_submit", "qpu_auto_manager",
                 "setup_ibm_accounts", "scheduler_diagnostic", "check_circuit_duration")
        with ExitStack() as stack:
            for name in ("pathlib.Path.mkdir", "pathlib.Path.write_text", "pathlib.Path.write_bytes",
                         "shutil.move", "shutil.copy2", "subprocess.run", "subprocess.Popen", "builtins.input"):
                stack.enter_context(patch(name, side_effect=AssertionError(name)))
            for name in names:
                load_script(name)
        self.runtime.QiskitRuntimeService.assert_not_called()
        self.runtime.QiskitRuntimeService.save_account.assert_not_called()
        self.runtime.EstimatorV2.assert_not_called()

    def test_scientific_import_location(self):
        old_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), old_path))
        directory = paths.register_science(self.root)
        self.assertEqual(directory, self.root / paths.STUDY_REL)
        self.assertTrue(sys.dont_write_bytecode)

    def test_shadowed_science_rejected(self):
        with patch.dict(sys.modules, {"hea_ansatz": types.SimpleNamespace(__file__=str(self.root / "wrong.py"))}):
            with self.assertRaises(ImportError):
                paths.register_science(self.root)

    def test_existing_science_functions_unchanged(self):
        tree = ast.parse((CODE / "mitigation_pilot.py").read_text(encoding="utf-8"))
        # Only the explicit remote-client import guard is new; retain the
        # original fingerprint for every scientific option and calculation.
        configuration=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='configuration')
        guard=ast.parse('from ibm_runtime_client import remote_runtime\nremote_runtime()').body
        self.assertEqual([ast.dump(n) for n in configuration.body[:2]],[ast.dump(n) for n in guard])
        configuration.body=configuration.body[2:]
        expected = {
            "configuration": "a61c7fa3db68f0442e8a1935e06630a018b03da5c232fb25c967fcd0a2ba9901",
            "_validated_rows": "482f4bfb42785aa72655fce76c3eed58cfc425c25aa10c0ed83911abcd46f223",
            "create_mos2_style_figures": "4b186e77b739f6319bcf26db6ff8f69e9ca91ff471cbc6ba27cae4614b9eecf5",
        }
        actual = {n.name: hashlib.sha256(ast.dump(n).encode()).hexdigest()
                  for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in expected}
        self.assertEqual(expected, actual)

    def test_all_vqe_sources_parse(self):
        for path in CODE.glob("*.py"):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

    def test_mitigation_no_legacy_path_literals(self):
        for name in ("mitigation_pilot", "run_multiple_n", "qpu_auto_submit", "qpu_auto_manager"):
            tree = ast.parse((CODE / (name + ".py")).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn("experiments/HEA_VQE", node.value)
                    self.assertNotIn("src/HEA_VQE", node.value)
                    self.assertNotIn("src/common", node.value)

if __name__ == "__main__":
    unittest.main()
