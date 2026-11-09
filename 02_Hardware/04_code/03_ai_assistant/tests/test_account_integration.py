"""Offline account integration regressions. No provider connection or submission."""
import importlib
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import shutil
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

AI = Path(__file__).resolve().parents[1]
ROOT = AI.parents[2]
sys.path.insert(0, str(AI))
from account_config import ACCOUNT_LABELS, load_saved_account_profiles
from final_configuration import load_final_config, campaign_authorization_from_final_config
from job_registry import JobRegistry
from quota import select_highest_available_account, available_quota_seconds


@contextmanager
def scratch_directory():
    base = (ROOT/'.codex_tmp').resolve()
    base.mkdir(exist_ok=True)
    path = base / ('account-test-' + uuid.uuid4().hex)
    path.mkdir(mode=0o755)
    try:
        yield path
    finally:
        if path.resolve().parent != base:
            raise RuntimeError('Refusing cleanup outside test scratch directory')
        shutil.rmtree(path)


class AccountIntegrationTests(unittest.TestCase):
    def setUp(self):
        network = patch("socket.socket.connect", side_effect=AssertionError("Offline tests forbid network access"))
        network.start()
        self.addCleanup(network.stop)
        self.config = load_final_config(ROOT)
        self.auth = campaign_authorization_from_final_config(self.config)
        self.auth['allowed_accounts'] = list(ACCOUNT_LABELS)
        self.auth['allowed_instance_refs'] = {x: ['123456abcdef'] for x in ACCOUNT_LABELS}
        self.auth['account_runtime_budget_seconds'] = {x: 600 for x in ACCOUNT_LABELS}
        self.usage = {'campaign_consumed_seconds': 1435, 'campaign_reserved_seconds': 0,
                      'per_account': {x: {'consumed_seconds': 0, 'reserved_seconds': 0} for x in ACCOUNT_LABELS}}
        self.usage['provider_reservations'] = {x: {'reserved_seconds':0, 'ambiguous':False} for x in ACCOUNT_LABELS}
        self.estimate = SimpleNamespace(stress_block_seconds=175, defensible=True, scope='full_batch')

    def report(self, label, remaining):
        return {'account_label': label, 'status': 'PASS', 'submission_eligible': True,
                'checked_utc':datetime.now(timezone.utc).isoformat(), 'pending_jobs_count':0,
                'account_wide_usage_verified': True, 'configured_instance_ref': '123456abcdef',
                'frozen_input_validation': {'input_sha256': {'isa': 'frozen'}},
                'instances': [{'instance_ref':'123456abcdef', 'usage': {'remaining_seconds': remaining,
                    'consumed_seconds': 600-remaining, 'limit_seconds': 600}}]}

    def select(self, reports):
        return select_highest_available_account(reports, runtime_estimate=self.estimate,
            usage=self.usage, authorization=self.auth, expected_hashes={'isa': 'frozen'})

    def test_ten_labels_and_imports(self):
        self.assertEqual(ACCOUNT_LABELS, tuple('lab'+x for x in 'ABCDEFGHIK'))
        self.assertEqual(set(load_saved_account_profiles()), set(ACCOUNT_LABELS))
        for name in ('account_config', 'job_registry', 'final_configuration', 'independent_n_campaign',
                     'quota', 'read_only_preflight', 'batch_scheduler', 'targeted_recovery', 'mxene'):
            importlib.import_module(name)

    def test_registration_preserves_existing_profile(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('account_setup', AI.parent/'01_vqe/setup_ibm_accounts.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sdk = Mock()
        sdk.saved_accounts.return_value = {'labG': {}}
        with patch('qiskit_ibm_runtime.QiskitRuntimeService', sdk), \
             patch('sys.argv', ['setup_ibm_accounts.py', '--configure', '--accounts', 'labG']), \
             patch('getpass.getpass') as prompt, patch('builtins.print'):
            module.main()
        sdk.save_account.assert_not_called()
        prompt.assert_not_called()

    def test_pending_accounts_do_not_gain_authorization(self):
        # Pending is a configuration condition, not a permanent property of a
        # particular account label. Never depend on the operator's live config.
        config = deepcopy(self.config)
        config.update(enabled_account_labels=list(ACCOUNT_LABELS),
                      account_selection='highest_available_quota', account_by_n={},
                      allowed_instance_refs={'labA': ['123456abcdef']},
                      campaign_runtime_budget_seconds=1800)
        for refs in (None, [], ['abcdef123456']):
            with self.subTest(labG_instance_refs=refs):
                if refs is None:
                    config['allowed_instance_refs'].pop('labG', None)
                else:
                    config['allowed_instance_refs']['labG'] = refs
                auth = campaign_authorization_from_final_config(config)
                self.assertEqual('labG' in auth['allowed_accounts'], bool(refs))
                self.assertEqual('labG' in auth['allowed_instance_refs'], bool(refs))
                self.assertEqual('labG' in auth['account_runtime_budget_seconds'], bool(refs))
                for label in ('labI', 'labK'):
                    self.assertNotIn(label, auth['allowed_accounts'])
                    self.assertNotIn(label, auth['allowed_instance_refs'])
                self.assertEqual(auth['total_runtime_budget_seconds'], 1800)
        self.assertEqual(self.config, load_final_config(ROOT))

    def test_readonly_registry_preserves_journal_and_rejects_writes(self):
        with scratch_directory() as directory:
            path = directory/'readonly.sqlite3'
            JobRegistry(path)
            db = sqlite3.connect(path)
            try:
                db.execute('PRAGMA journal_mode=DELETE')
            finally:
                db.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            registry = JobRegistry(path, readonly=True)
            with registry._connection() as db:
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
                self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("UPDATE independent_n_campaigns SET state='COMPLETE'")
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_highest_provider_quota_despite_equal_campaign_caps(self):
        result = self.select({x: self.report(x, n) for x, n in [('labG', 400), ('labI', 500), ('labK', 600)]})
        self.assertEqual(result['account_label'], 'labK')
        self.assertEqual(result['effective_remaining_seconds'], 600)
        self.assertEqual(result['internal_campaign_remaining_seconds'], 365)

    def test_account_limit_enforced_campaign_limit_monitored(self):
        reports = {x: self.report(x, 600) for x in ('labG', 'labI')}
        self.usage['per_account']['labG']['consumed_seconds'] = 451
        self.assertEqual(self.select(reports)['account_label'], 'labI')
        self.usage['campaign_consumed_seconds'] = 1620
        with self.assertLogs('quota',level='INFO'):
            result=self.select(reports)
        self.assertEqual(result['account_label'],'labI')
        self.assertTrue(result['warnings'])
        self.assertEqual(result['internal_campaign_remaining_seconds'],180)

    def test_fresh_quota_cannot_bypass_explicit_account_budget(self):
        self.assertEqual(available_quota_seconds(provider_remaining_seconds=600,
            local_budget_remaining_seconds=180), 180)
        self.assertEqual(available_quota_seconds(provider_remaining_seconds=100,
            local_budget_remaining_seconds=365), 100)
        self.assertEqual(available_quota_seconds(provider_remaining_seconds=200,
            outstanding_reservations_seconds=20, local_budget_remaining_seconds=365), 180)

    def test_bad_reports_fail_closed(self):
        for update in ({'status': 'FAIL'}, {'submission_eligible': False},
                       {'configured_instance_ref': 'bad'}, {'account_label': 'labA'},
                       {'account_wide_usage_verified': False}, {'instances': []}, {'instances': [None]},
                       {'frozen_input_validation': {'input_sha256': {'isa': 'changed'}}}):
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                self.select({'labG': self.report('labG', 600) | update})

    def test_missing_and_invalid_profiles_are_redacted(self):
        from read_only_preflight import preflight_account
        for label in ('labG', 'labI', 'labK'):
            report = preflight_account(load_saved_account_profiles((label,))[label],
                service_factory=Mock(side_effect=ValueError('SECRET_MUST_NOT_APPEAR')))
            self.assertFalse(report['submission_eligible'])
            self.assertNotIn('SECRET_MUST_NOT_APPEAR', json.dumps(report))

    def test_zero_eligible_pauses_manager_without_submit(self):
        import independent_n_campaign as campaign
        with scratch_directory() as directory:
            registry = Mock()
            registry.independent_campaign_status.return_value = {'exists': True, 'state': 'PAUSED', 'current_level_index': 4}
            def refused(*args, **kwargs):
                return self.select({})
            with patch.object(campaign, 'campaign_root', return_value=Path(directory)), \
                 patch.object(campaign, 'run_independent_campaign', side_effect=refused), \
                 patch.object(campaign, 'inspect_independent_campaign', return_value={}):
                result = campaign.run_campaign_auto_manager(ROOT, 'offline', registry)
            self.assertEqual(result['state'], 'PAUSED')
            self.assertFalse(result['submission_performed'])
            self.assertEqual(registry.checkpoint_independent_campaign.call_args.kwargs['state'], 'PAUSED')

    def test_new_accounts_registry_and_duplicate_guard(self):
        with scratch_directory() as directory:
            registry = JobRegistry(Path(directory)/'test.sqlite3')
            for label in ACCOUNT_LABELS:
                args = dict(logical_block_id=label, account_label=label, backend='ibm_fez',
                    treatments=['M0'], input_hashes={'isa':'frozen'}, quota_seconds_per_job=1,
                    actual_usage_seconds=0, n=5, replicate_blocks=1)
                rows = registry.reserve_batch(**args)
                with self.assertRaises(ValueError):
                    registry.reserve_batch(**args)
                registry.transition(rows['M0'], 'SUBMITTING')
                registry.transition(rows['M0'], 'SUBMITTED', job_id='existing-'+label)
                registry.transition(rows['M0'], 'COMPLETED')
                with self.assertRaises(ValueError):
                    registry.reserve_batch(**args)

    def test_legacy_migration_preserves_every_row(self):
        # Migrate only a temporary snapshot, never the campaign database itself.
        with scratch_directory() as directory:
            path = Path(directory)/'copy.sqlite3'
            source = sqlite3.connect(JobRegistry.default_database(ROOT).as_uri()+'?mode=ro', uri=True)
            dest = sqlite3.connect(path)
            source.backup(dest); source.close()
            tables = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {t: dest.execute('SELECT * FROM "'+t+'"').fetchall() for t in tables}
            dest.close()
            JobRegistry(path)
            from contextlib import closing
            with closing(sqlite3.connect(path)) as db:
                for table, rows in before.items():
                    self.assertEqual(rows, db.execute('SELECT * FROM "'+table+'"').fetchall(), table)
                schema = db.execute("SELECT sql FROM sqlite_master WHERE name='jobs'").fetchone()[0]
                for label in ACCOUNT_LABELS:
                    self.assertIn("'"+label+"'", schema)


if __name__ == '__main__':
    unittest.main()
