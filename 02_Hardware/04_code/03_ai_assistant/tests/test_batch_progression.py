"""Offline progression regressions against real temporary SQLite registries."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_registry import JobRegistry, utc_now
from batch_progression import evaluate_batch_progression, TREATMENTS
from test_account_integration import scratch_directory


class BatchProgressionTests(unittest.TestCase):
    def setUp(self):
        guard=patch("socket.socket.connect",side_effect=AssertionError("Offline only"))
        guard.start(); self.addCleanup(guard.stop)
        self.directory=self.enterContext(scratch_directory())
        self.registry=JobRegistry(self.directory/"progress.sqlite3")
        self.hashes={"isa":"a"*64,"theta":"b"*64,"observables":"c"*64}

    def target(self,n=10):
        return SimpleNamespace(target_id=f"n{n}",target_n=n,chunk_n=5,blocks_required=n//5,
            treatment_ids=TREATMENTS,input_hashes=self.hashes,source_run="run_02",
            backend="ibm_fez",sha256="d"*64)

    def reserve(self,target,sequence):
        return self.registry.reserve_batch(logical_block_id=f"{target.target_id}-block-{sequence:03d}",
            account_label="labA",backend="ibm_fez",treatments=list(TREATMENTS),input_hashes=self.hashes,
            quota_seconds_per_job=1,actual_usage_seconds=0,n=5,replicate_blocks=1,
            adaptive_target=dict(target_id=target.target_id,target_n=target.target_n,chunk_n=5,
                manifest_sha256=target.sha256,account_label="labA",runtime_upper_bound_seconds=8,
                safety_margin_seconds=0,preflight_checked_at=utc_now(),quota_scope="account_wide_verified"))

    def block(self,target=None,sequence=1,missing=()):
        target=target or self.target()
        ids=self.reserve(target,sequence)
        block=f"{target.target_id}-block-{sequence:03d}"
        for name,row_id in ids.items():
            self.registry.transition(row_id,"SUBMITTING")
            self.registry.transition(row_id,"SUBMITTED",job_id=f"{block}-{name}",batch_id=f"batch-{block}")
            self.registry.transition(row_id,"COMPLETED",qpu_usage_seconds=1)
            records=[dict(pub_index=i,expectation_valid=True,uncertainty_valid=True,
                uncertainty_state="PRESENT",expectation_values=[0.2]*7,standard_deviations=[0.01]*7)
                for i in range(1,6) if (name,i) not in missing]
            self.registry.record_pub_validation_records(row_id,target.target_id,"e"*64,records)
        return target,block,ids

    def replacement(self,target,block,name,index):
        sequence=int(block.rsplit("-",1)[1]); logical=(sequence-1)*5+index
        source=f"{block}-{name}"
        meta=dict(source_run="run_02",input_hashes=self.hashes,source_job_id=source,
                  source_archive_sha256="e"*64)
        options={"shots":4096,"treatment":name}
        row=self.registry.plan_pub_replacement(target_id=target.target_id,logical_block_id=block,
            treatment_id=name,source_pub_index=index,logical_pub_index=logical,options=options,metadata=meta)
        rid=row["replacement_id"]; job=f"replacement-{rid}"
        self.registry.transition_pub_replacement(rid,"SUBMITTING")
        self.registry.transition_pub_replacement(rid,"SUBMITTED",job_id=job,batch_id=f"replacement-batch-{rid}")
        payload=dict(source_run="run_02",input_hashes=self.hashes,replacement_job_id=job,
            original_source_job_id=source,original_source_archive_sha256="e"*64,
            treatment_id=name,logical_pub_index=logical,source_pub_index=index,
            runtime_options_requested=options,records=[dict(expectation_validation="PASS",uncertainty_status="PRESENT",
                expectation_values=[0.3]*7,standard_deviations=[0.02]*7)])
        raw=json.dumps(payload).encode(); path=self.directory/f"replacement-{rid}.json";path.write_bytes(raw)
        self.registry.archive_pub_replacement(rid,archive_path=str(path),archive_sha256=hashlib.sha256(raw).hexdigest(),
            expectation_values=[0.3]*7,standard_deviations=[0.02]*7,uncertainty_state="PRESENT",error_codes=[],qpu_usage_seconds=1)
        return rid,path

    def decision(self,target,block):
        return evaluate_batch_progression(self.registry,target,block)

    def test_normal_40_without_analysis_and_repeated_evaluation(self):
        t,b,_=self.block()
        first=self.decision(t,b)
        self.assertTrue(first["eligible"]); self.assertEqual(first["dataset_status"],"COMPLETE")
        self.assertEqual(first["validated_slots"],40)
        self.assertEqual(first,self.decision(t,b))
        self.assertIsNone(self.registry.latest_batch_recovery_review(b))
        self.reserve(t,2)
        with self.assertRaises(ValueError): self.reserve(t,2)

    def test_39_plus_one_and_original_incomplete(self):
        t,b,_=self.block(missing=[(TREATMENTS[0],5)])
        self.replacement(t,b,TREATMENTS[0],5)
        self.assertEqual(self.decision(t,b)["dataset_status"],"REPAIRED_COMPLETE")
        status=self.registry.target_status(t.target_id)["blocks"][0]
        self.assertEqual(status["status"],"INCOMPLETE")
        self.assertTrue(status["progression_eligible"])
        self.reserve(t,2)

    def test_38_plus_two(self):
        missing=[(TREATMENTS[0],4),(TREATMENTS[0],5)]
        t,b,_=self.block(missing=missing)
        for name,index in missing:self.replacement(t,b,name,index)
        self.assertTrue(self.decision(t,b)["eligible"])
        self.assertEqual(len(self.registry.repaired_pub_values(t.target_id)),40)

    def test_missing_slot_blocks_even_when_status_manually_complete(self):
        t,b,_=self.block(missing=[(TREATMENTS[0],5)])
        with self.registry._transaction() as db: db.execute("UPDATE experiment_blocks SET status='COMPLETE'")
        self.assertFalse(self.decision(t,b)["eligible"])
        with self.assertRaises(ValueError):self.reserve(t,2)

    def test_nonfinite_and_missing_uncertainty_block(self):
        t,b,ids=self.block()
        rows=self.registry.repaired_pub_values(t.target_id)
        for field,value in (("expectation_values",[float("nan")]*7),
                            ("expectation_values",[float("inf")]*7),
                            ("standard_deviations",[-1]*7),
                            ("standard_deviations",None)):
            with self.subTest(field=field,value=value):
                corrupt=[dict(row) for row in rows];corrupt[0][field]=value
                with patch.object(self.registry,"repaired_pub_values",return_value=corrupt):
                    self.assertFalse(self.decision(t,b)["eligible"])

    def test_replacement_hash_mismatch_blocks(self):
        t,b,_=self.block(missing=[(TREATMENTS[0],5)])
        rid,_=self.replacement(t,b,TREATMENTS[0],5)
        with self.registry._transaction() as db:
            db.execute("UPDATE pub_replacement_jobs SET input_hashes_json=? WHERE replacement_id=?",(json.dumps({"isa":"wrong"}),rid))
        d=self.decision(t,b);self.assertFalse(d["eligible"]);self.assertFalse(d["frozen_inputs_verified"])

    def test_replacement_identity_and_archive_tamper_block(self):
        t,b,_=self.block(missing=[(TREATMENTS[0],5)])
        rid,path=self.replacement(t,b,TREATMENTS[0],5)
        path.write_text("{}")
        self.assertFalse(self.decision(t,b)["eligible"])

    def test_duplicate_selected_slots_block_and_valid_original_wins(self):
        t,b,_=self.block()
        with self.assertRaisesRegex(RuntimeError,"already has an accepted"):
            self.replacement(t,b,TREATMENTS[0],5)
        rows=self.registry.repaired_pub_values(t.target_id)
        self.assertEqual(len(rows),40)
        self.assertTrue(all(r["source"]=="ORIGINAL" for r in rows))
        with patch.object(self.registry,"repaired_pub_values",return_value=rows+[rows[0]]):
            self.assertFalse(self.decision(t,b)["eligible"])

    def test_unknown_never_retries_even_with_full_ledger(self):
        t,b,ids=self.block()
        for state in ("UNKNOWN","SUBMITTING"):
            with self.subTest(state=state):
                with self.registry._transaction() as db:
                    db.execute("UPDATE jobs SET submission_state=? WHERE id=?",(state,ids[TREATMENTS[0]]))
                self.assertFalse(self.decision(t,b)["eligible"])
                with self.assertRaises(ValueError):self.reserve(t,2)
                with self.assertRaises(ValueError):self.reserve(t,1)

    def test_review_without_artifacts_uses_data(self):
        t,b,_=self.block(missing=[(TREATMENTS[0],5)])
        self.replacement(t,b,TREATMENTS[0],5)
        self.registry.record_batch_recovery_review(b,batch_execution_status="INCOMPLETE",recovery_status="RECOVERED",
            completeness_policy_version="complete-only",valid_fraction_by_treatment={name:1 for name in TREATMENTS},
            raw_archives_verified=True,provenance_verified=True)
        review=self.registry.latest_batch_recovery_review(b)
        self.assertTrue(review["progression_eligible"])
        self.assertFalse(review["analysis_verified"]);self.assertFalse(review["figures_verified"]);self.assertFalse(review["report_verified"])

    def test_review_cannot_manufacture_recovery(self):
        t,b,_=self.block(missing=[(TREATMENTS[0],5)])
        with self.assertRaises(ValueError):
            self.registry.record_batch_recovery_review(b,batch_execution_status="INCOMPLETE",recovery_status="RECOVERED",
                completeness_policy_version="complete-only",valid_fraction_by_treatment={name:1 for name in TREATMENTS},
                raw_archives_verified=True,provenance_verified=True,progression_eligible=True,
                analysis_verified=True,figures_verified=True,report_verified=True)

    def test_sequential_n25_recovery_then_n30_completes_without_exports(self):
        levels=[dict(level_index=i,target_n=(i+1)*5,target_id=f"n{(i+1)*5}",experiment_id=f"n{(i+1)*5}") for i in range(6)]
        self.registry.register_independent_campaign(dict(campaign_id="offline",manifest_sha256="f"*64,levels=levels,source_run="run_02",calibration_data_excluded=True))
        for i in range(6):
            t=self.target((i+1)*5)
            for sequence in range(1,t.blocks_required+1):
                missing=[(TREATMENTS[0],5)] if i==4 and sequence==5 else []
                _,b,_=self.block(t,sequence,missing)
                if missing:self.replacement(t,b,TREATMENTS[0],5)
            if i==5:
                import independent_n_campaign as campaign
                auth=dict(live_auto_submit=True,allowed_accounts=["labA"],
                    allowed_actions=["prepare","preflight","dry-run","submit","monitor","collect","validate"])
                with patch.object(campaign,"load_campaign_authorization",return_value=(auth,{})), \
                     patch.object(campaign,"campaign_root",return_value=self.directory), \
                     patch.object(campaign,"_target_from_level",return_value=t), \
                     patch.object(campaign,"_account_for_level",return_value="labA"), \
                     patch.object(campaign,"inspect_independent_campaign",return_value={}), \
                     patch.object(campaign,"_finalize_level",side_effect=AssertionError("No publication gate")), \
                     patch.object(campaign,"_campaign_completion_verification",side_effect=AssertionError("No artifact gate")):
                    result=campaign.run_independent_campaign(self.directory,"offline",self.registry,
                        authorize_live_campaign=True,_worker_lock_held=True,
                        service_factory=Mock(side_effect=AssertionError("No IBM jobs")))
                    self.assertTrue(result["data_collection_complete"])
            else:
                self.registry.complete_independent_level_repaired("offline",i)
            self.registry.complete_independent_level("offline",i)  # idempotent
            status=self.registry.independent_campaign_status("offline")
            self.assertEqual(status["current_level_index"],i+1)
            self.assertEqual(status["levels"][i]["state"],"REPAIRED_COMPLETE" if i==4 else "COMPLETE")
        self.assertEqual(status["state"],"COMPLETE")
        self.assertEqual(len([e for e in status["events"] if e["event_type"].startswith("INDEPENDENT_N_LEVEL_")]),6)

    def test_progression_does_not_authorize_live_or_skip_preflight(self):
        from batch_scheduler import submit_runtime_block
        from adaptive_targets import reserve_next_block
        t,b,_=self.block();self.assertTrue(self.decision(t,b)["eligible"])
        with self.assertRaises((RuntimeError,PermissionError,ValueError)):
            submit_runtime_block(t,self.registry,Mock(),logical_block_id=b,account_label="labA",instance=None,reservation_seconds_per_job=1)
        with self.assertRaises(RuntimeError):
            reserve_next_block(t,self.registry,account_label="labA",preflight_report={},runtime_estimate=None)
        with self.assertRaises(ValueError):
            self.registry.reserve_batch(logical_block_id="quota",account_label="labA",backend="ibm_fez",
                treatments=list(TREATMENTS),input_hashes=self.hashes,quota_seconds_per_job=100,
                actual_usage_seconds=599,n=5,replicate_blocks=1)


if __name__ == "__main__": unittest.main()

