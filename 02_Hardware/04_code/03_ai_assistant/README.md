# MXene Auto Manager ? migrated implementation

This is the implementation reference for the three-component manager. The
migration relocates code and preserves its scientific and submission behavior;
it does not certify an incomplete campaign or repair missing historical exports.

## Status and authority

The 26 former root implementations now live in `01_ai_agent/` (operator CLI and
optional AI advice) or `03_python_automation/` (24 deterministic modules).
Root files are small compatibility entrypoints, not duplicate implementations.
`_migration_bridge.py` loads the canonical source into the existing module
namespace, preserving legacy imports, monkeypatch targets and CLI behavior.
Direct canonical CLI paths are also supported. Numeric directory names are
filesystem components; legacy flat module names remain the import interface.

`03_python_automation/dependence_statistics.py` is the **single canonical
campaign-level statistical implementation**, directly in that directory.
There is no `scientific/` subdirectory. It contains preserved Figure C/D code
and the existing dependence-aware E/F implementation. The old
`../01_vqe/compare_105_pubs.py` forwards to it and contains no numerical methods.
Default direct invocation remains D only; `--figure-c` remains C only.

Python owns authorization, durable state, quota, frozen inputs and idempotency.
AI advice is optional, bounded and advisory. No arbitrary model-generated shell
is executed. `01_ai_agent/offline_tools.py` exposes typed allowlisted readiness
requests and loads only the requested skill. An autonomous LLM decision engine,
Ollama-default policy and official Codex adapter are not newly implemented by
this migration. Existing provider behavior is preserved.

## Actual architecture

```text
03_ai_assistant/
  01_ai_agent/
    mxene.py                 # canonical operator CLI
    ai_assistance.py         # bounded optional provider advice
    offline_tools.py         # typed comparison readiness and skill loading
  02_skills/
    workflow-manager/SKILL.md
    campaign-comparison/SKILL.md
    dependence-aware-statistics/SKILL.md
  03_python_automation/
    dependence_statistics.py # single C/D/E/F implementation
    ...                      # 24 relocated deterministic modules listed below
  tests/                     # existing offline tests plus migration regressions
  _migration_bridge.py       # shared compatibility loader
  <26 legacy module names>.py # forwarding entrypoints, retained deliberately
  __init__.py
  final_config.json          # existing location; not moved or modified
  <historical config/source backups> # retained, not active canonical code
  FILE_INVENTORY.csv
  README.md
```

Credentials/saved IBM profiles, SQLite registry, raw archives, frozen bundles,
scientific results, logs and rollback backups remain in their existing protected
locations. They were not moved into code components. Tests remain separate from
LIVE execution. No obsolete source or backup was automatically deleted.

The full file-by-file inventory includes old_path, proposed_path, purpose,
imports, imported_by, execution_entrypoint, migration_risk and action:
[FILE_INVENTORY.csv](FILE_INVENTORY.csv). Callers are static import observations;
dynamic imports and external shell callers cannot be exhaustively inferred.

## Canonical module responsibilities

| Canonical file | Responsibility |
|---|---|
| `01_ai_agent/ai_assistance.py` | Optional, event-driven AI diagnostics with no research or IBM write tools. |
| `01_ai_agent/mxene.py` | Small operator interface for the MXene VQE research automation pipeline. |
| `01_ai_agent/offline_tools.py` | Typed, fail-closed offline tools; no model-generated shell or IBM execution. |
| `03_python_automation/account_config.py` | Ephemeral, redacted references for explicitly authorized IBM accounts. |
| `03_python_automation/adaptive_targets.py` | Adaptive cumulative PUB targets around complete eight-job Runtime Batches. |
| `03_python_automation/batch_monitor.py` | Read-only IBM Runtime Batch monitoring and validated-result inspection. |
| `03_python_automation/batch_progression.py` | Read-only scientific progression policy; never authorizes Runtime submission. |
| `03_python_automation/batch_scheduler.py` | IBM Runtime Batch scheduler with a disabled-by-default live submission gate. |
| `03_python_automation/calibration.py` | Explicitly authorized, isolated Runtime calibration for VQE mitigation. |
| `03_python_automation/check_install.py` | Offline, read-only installation checker for MXene Hybrid Level 2. |
| `03_python_automation/dependence_statistics.py` | Canonical C/D comparison plus offline dependence-aware E/F statistics; no IBM calls. |
| `03_python_automation/diagnostic_manager.py` | One-child Auto Manager launcher with persistent native-crash diagnostics. |
| `03_python_automation/experiment_manifest.py` | Validated, immutable-input experiment manifest construction. |
| `03_python_automation/final_configuration.py` | Single non-secret configuration schema for the distributable assistant. |
| `03_python_automation/independent_n_campaign.py` | Sequential orchestrator for six fully independent VQE sample-size experiments. |
| `03_python_automation/job_registry.py` | Durable SQLite submission ledger with idempotency and recovery events. |
| `03_python_automation/low_memory_runtime.py` | Standard-library-only startup policy for the single campaign worker. |
| `03_python_automation/manager_exit_codes.py` | Stable process outcomes. Windows native exception DWORDs stay unmodified. |
| `03_python_automation/mitigation_pilot.py` | MXene EIS14: reproducible, submission-explicit 2^3 mitigation pilot (7 qubits). |
| `03_python_automation/quota.py` | Cumulative, account-aware quota selection. Unknown usage fails closed. |
| `03_python_automation/read_only_preflight.py` | Explicit, read-only IBM account preflight. This module never creates Batch/jobs. |
| `03_python_automation/repository_paths.py` | Canonical, conservative paths for MXene VQE; no IBM side effects. |
| `03_python_automation/research_pipeline.py` | Thin command-line entry point for the existing VQE independent-n backend. |
| `03_python_automation/runtime_estimator.py` | Read-only calibration from integrity-checked historical IBM QPU usage records. |
| `03_python_automation/scientific_exports.py` | Non-overwriting paper exports for one already analyzed independent n level. |
| `03_python_automation/targeted_recovery.py` | Per-logical-PUB recovery for independent-n VQE targets. |
| `03_python_automation/vqe_adapter.py` | Thin adapter to the existing VQE pilot; scientific methods remain canonical there. |
| `03_python_automation/worker_runtime.py` | Single-process campaign lock and bounded retries for read-only provider methods. |


`independent_n_campaign.py` remains a deterministic campaign state machine in
Python automation, not an LLM policy engine. `research_pipeline.py` routes the
CLI to it. `scientific_exports.py` remains the authoritative per-level publication
contract. `vqe_adapter.py` still loads the VQE implementation by verified path.

**Two mitigation implementations are intentionally retained.** The relocated
assistant implementation is `03_python_automation/mitigation_pilot.py`. The VQE
implementation remains `../01_vqe/mitigation_pilot.py`. They differ in 11 functions:
_runtime_serializable, analyze, atomic_json, collect, configuration,
create_mos2_style_figures, preflight, service_obj, sha, submit and verify_source.
The relocation preserves the assistant function bodies; it does not claim these
two variants are equivalent. The established VQE adapter still selects the VQE
file explicitly. Existing import-order behavior of the two repository_paths
modules is preserved; they are not merged by filename alone.

## Frozen scientific design and real campaign structure

Campaign `vqe-independent-n-01` uses fixed ISA run_02, the same 21 theta angles,
physical qubits 140?146, seven mapped observables, measured EIS dataset,
seven-parameter mapping (Rs, L, Rct, Q1, n1, Q2, n2) and canonical model at
`01_quantum_eis_fit/04_code/vqe/impedance_model.py`. Original/replacement jobs
retain the frozen manifest and inputs. Shots remain 4096 per PUB.

The controlled treatments are M0_Baseline=None, M1_DD=DD, M2_TREX=TREX,
M3_ZNE=ZNE, M4_DD_TREX=DD+TREX, M5_DD_ZNE=DD+ZNE,
M6_TREX_ZNE=TREX+ZNE and M7_ALL=DD+TREX+ZNE. Their mitigation settings and all
scientific calculations are unchanged.

Six independent experiments run in n=5,10,15,20,25,30 order. Each needs n accepted
logical PUBs per treatment: 105 pooled observations per treatment, 840 total.
PUBs are not jobs. This campaign manifest uses chunk_n=5: eight original
Estimator jobs, five PUBs per treatment job, per original Runtime Batch; n/5
original Batches at each level. Totals are 21 planned original Batches and 168
original jobs. Recovery attempts do not count as original Batches or additional
accepted slots. Historical grouped recoveries retain their recorded packing.

The lifecycle remains reconcile ? monitor ? retrieve ? durable archive ? validate
? targeted recovery ? fresh quota/account selection ? next Batch. Logical slots
are identified by target, treatment, block and logical index, not by source-local
PUB index alone. Original/replacement job IDs, attempt history, archive hashes
and selected-source provenance remain authoritative. Account failover preserves
explicit authorization, priority and backend/frozen compatibility checks.

Comparability and repeatability benefit from frozen inputs; targeted recovery
avoids redundant QPU work. Calibration drift, account/backend differences,
within-job/Batch dependence and recovery selection remain interpretation limits.

## Quota, recovery and restart safeguards

`03_python_automation/final_configuration.py:quota_eligibility` remains the single
quota gate: MIN_BATCH_QUOTA_SECONDS=150.0 for original Batches and
MIN_RECOVERY_QUOTA_SECONDS=40.0 for targeted replacements. Estimates plus safety
margin are diagnostics, not independent submission thresholds. Fresh provider
quota, verified unreserved balances, active reservations, explicit account limits,
backend access and authorization still constrain every submission.

WorkerLock, immutable manifests, durable intent identity, duplicate prevention
and UNKNOWN reconciliation are preserved. An ambiguous submission is paused;
it is never automatically replaced. Confirmed invalid/missing logical slots have
unlimited targeted replacement attempts with bounded delays. Accepted valid
PUBs are retained individually. Raw writes must succeed and verify before
collection/acceptance. Resource or analysis failures do not authorize resubmitting
accepted PUBs. Native numerical pools remain limited to one per worker.

## Final comparison and dependence-aware statistics

`mxene compare` is read-only and delegates to `campaign_comparison.inspect_campaign`.
Readiness verifies exactly 840 accepted slots, frozen manifests, immutable raw
archives, current STEP analysis receipts when present, canonical per-PUB source
maps, analysis-manifest SHA-256, and paired publication CSV/PNG hashes. Legacy
reports are discoverable audit evidence; their absence is not itself proof that
a current STEP analysis is invalid. Missing analysis files remain blockers.
Original Batch execution status is preserved independently of repaired scientific
completeness. No state transition or IBM operation occurs in this adapter.

Canonical inputs remain beneath
`02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/`.
Source-local `PUB_index` is joined with treatment and IBM job ID through
`pub_source_manifest.csv`; it is never renamed to a logical PUB number.
Ten obsolete `scientific_exports` references were removed, using current hash,
paired-export and PNG validators plus this explicit canonical source adapter.

From the repository root, read-only preflight:

```powershell
.\.venv\Scripts\python.exe -B 02_Hardware/04_code/03_ai_assistant/03_python_automation/campaign_comparison.py
```

Only after explicit operator authorization, append `--authorize-output-write`.
The writer repeats strict readiness checks. Final Comparison alone uses these
flat, project-root-resolved directories (independent of the working directory):

```text
02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/01_results_and_figures/compare/   PNG only
02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/02_run_level_data/compare/      CSV/JSON only
```

The first generation retains original basenames. Later generations append
`__v002`, `__v003`, etc. No existing file is overwritten; no subdirectories are
created inside these two directories. `publication_vNNN.json` commits last and
binds the manifest and SHA-256 inventory. An interrupted generation has no
commit marker and is never served. Historical `04_code/results/.../v001` outputs
are read-only scientific evidence, never a new publication destination.

Figure C and its convergence CSV are preserved byte-for-byte and referenced by
later manifests. They are not regenerated when the accepted observation hash is
unchanged. Its 808 medians are checked directly against those observations.

Visual references impose no numerical targets. Figure D uses actual treatment
medians and M0, with optional classical and noiseless ideal lines verified by the
existing canonical calculations. All 840 accepted SSE/RMSE values are recalculated
from receipt-bound selected expectations with `calculate_pub_eis`; originals and
replacement jobs are distinguished using `selected_pub_values.csv`. A legacy
cumulative fit table's original job label is not substituted for recovery identity.
Classical residuals and the offline seven-qubit ideal model are checked against
canonical sources. Unavailable optional lines are omitted with a recorded reason.

C/D ordinary bootstrap (3,000 draws, seed 1013) is an IID sensitivity analysis,
not independent experimental inference. The retained CSV `CI_*` columns name
sensitivity bounds for schema compatibility. E/F use 10,000 shared n-level block
resamples with seed 1013, PCG64 and linear 2.5/97.5 percentiles. Each draw
samples six labels uniformly with replacement and retains every observation in
each selected block for every treatment. Points are pooled medians; baseline
weights are n/105 and replicate weights are multiplicity(n)*n / total retained
observations. Repeated block compositions are cached without changing the draws.
A job spanning n-levels is rejected because that resampling would split its
observed dependence structure.

E = median(SSE_treatment) - median(SSE_M0).
F = 100 * [1 - median(SSE_treatment) / median(SSE_M0)].

These are explicitly **sensitivity intervals**, not inferential confidence
intervals, significance tests or multiplicity-adjusted conclusions. Six unequal
n-levels are design conditions, not established independent/exchangeable
replicates. Shared calibration and recovery-selection effects remain limitations.
Pooled-prefix curves are not optimizer convergence. Sample SD remains descriptive.
The original unpaired execution-cluster API below is retained for its separate
research contracts and is not used for E/F in this publication.

C/D/E/F share the publication palette and typography in PUBLICATION_STYLE.
D/E/F use identical fixed canvases and axes margins; E/F show seven colored
mitigation categories, gray zero lines and point-adjacent numerical labels. The
M0 zero self-contrast remains in the CSV as a validation row but is not plotted.
Template rankings, numbers and claims about shared recovery jobs are not copied.

`M0_matched_n_sensitivity_statistics.csv` drives both E/F; the statistical
provenance manifest records formulas, weighting, RNG, observed job structure and
limitations. The Dashboard consumes the same committed figures, summary CSV and
contrast CSV through `/api/comparison`, without frontend plotting or bootstrap.
Only verified job-level usage is exported; incomplete coverage is not total cost.
Historical discrepancy audits remain immutable records of a superseded numerical
reference requirement. They are not gates for the current visual-template task.

The existing `analyze` and `export` functions for dependence-aware analysis are
unchanged. A reviewed design contract bound to the exact observation hash is
required for whole-cluster resampling. It records unit identities, reviewer,
evidence, independence assumptions and unpaired treatment rationale. Job/Batch
IDs alone do not prove independence. Whole clusters retain their actual unequal
sizes. The estimand is pooled observation-weighted median SSE; n-level weights
are n/105. Cluster counts/sizes, seeds, interval construction and assumptions
are recorded. Intervals may be wider or narrower than IID intervals.

When supported, unpaired differences/log ratios and ranking robustness are
exploratory, not multiplicity-adjusted. E/F are white 600-DPI PNGs without grids.
Tables are dependence_aware_statistics.csv, bootstrap_method_comparison.csv and
treatment_comparisons.csv, with statistical_provenance_manifest.json. Isolated
immutable-directory publication verifies protected hashes, closes figures and
supports restart without overwriting history.

Hierarchical methods remain skipped: six unequal n designs do not establish
exchangeability. Paired methods remain skipped without verified shared blocks.
Production cluster contrasts/ranking remain unavailable until source adaptation
and reviewed design evidence exist. Unsupported methods produce explicit skip
reasons, not fabricated figures. IID bootstrap is not cluster-adjusted, and
colors or interval overlap do not establish significance.

## Offline verification commands

Run from the workspace root, sequentially, while LIVE is stopped. These commands
perform no IBM submission. The `run --resume` below remains OFFLINE because it
has neither LIVE flag. Do not substitute LIVE flags during migration checks.

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware'
$ai = '.\02_Hardware\04_code\03_ai_assistant'
$python = '.\.venv\Scripts\python.exe'
$env:OPENBLAS_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'; $env:NUMEXPR_NUM_THREADS='1'
$env:TEMP=(Resolve-Path '.codex_tmp').Path
$env:TMP=$env:TEMP; $env:TMPDIR=$env:TEMP
& $python -B "$ai\03_python_automation\check_install.py" --project .
& $python -B "$ai\01_ai_agent\mxene.py" --help
& $python -B "$ai\mxene.py" status --project . --campaign vqe-independent-n-01
& $python -B "$ai\mxene.py" run --project . --campaign vqe-independent-n-01 --resume
& $python -B "$ai\mxene.py" compare --project . --campaign vqe-independent-n-01
# Expected compare exit: 20 while its scientific readiness gates are blocked.
& $python -B "$ai\03_python_automation\dependence_statistics.py" --help
& $python -B -m unittest discover -s "$ai\tests" -p test_component_migration.py -v
& $python -B -m unittest discover -s "$ai\tests" -p test_dependence_statistics.py -v
& $python -B -m unittest discover -s "$ai\tests" -v
# Full suite has a known missing setup_ibm_accounts.py error; do not hide it.
```

The existing `diagnostic_manager.py` entrypoint and its new canonical path both
work. Its default log directory and child mxene target retain their old resolved
locations. Read-only status, offline dry-run/resume, help and synthetic analysis
are the migration checks; no actual LIVE resume or production export is run.

## Regression results and unresolved failures

Manager baseline: 268 tests, 267 pass, one pre-existing missing-file error.
Post-relocation manager suite: 276 tests, 275 pass, the same error. The eight
new migration tests and 15 existing statistical tests pass. C/D synthetic
old/new regression matches PNG and CSV bytes, verifies 600 DPI, retains C during
D and verifies the measured-M0 fallback. This fixture equivalence does not
certify the unavailable production source-validation adapter.

The manager failure is test_registration_preserves_existing_profile because
`../01_vqe/setup_ibm_accounts.py` is absent. The legacy VQE baseline has 27 tests:
19 pass, four assertion failures and four errors. Known failures concern the
missing historical per_pub_eis.csv fixture (two tests), plotting AST snapshot,
bootstrap canvas dimensions, square canvas size and wide figure/output contract.
The post-migration legacy run has exactly the same 19 passes, four assertion failures and four errors; failure names match the baseline.
Two additional compatibility tests fail because `../01_vqe/qpu_auto_submit.py`
is absent. These files were already missing before migration. No expected
scientific hashes, golden dimensions or historical fixtures were fabricated.

## Rollback, evidence and cleanup

The audit directory linked below contains an online SQLite backup, before/after
registry and accepted-slot fingerprints, protected scientific-file hashes,
complete source-before copies, actual migration map, final tree, test logs,
algorithm/output equivalence and exact changed-file hashes. Online backup
includes committed WAL; never combine it with stale WAL/SHM sidecars. No schema
migration was performed. Code rollback does not require restoring the database.
Never restore an old registry over newer accepted PUBs.

All historical backup/config files and both mitigation implementations remain.
Deletion-candidates.csv is review evidence only: caches may be rebuildable, but
rollback baselines, historical configs and distinct source variants are not
approved for deletion. No caches, backups, production results or code variants
were automatically removed or archived elsewhere.

Audit and rollback: [migration evidence](../../../manager_audit_backups/migration-20261107T150331Z/).

- [Actual migration map](../../../manager_audit_backups/migration-20261107T150331Z/migration-map.csv)
- [Final tree](../../../manager_audit_backups/migration-20261107T150331Z/final-tree.txt)
- [Deletion candidates ? no deletion authorized](../../../manager_audit_backups/migration-20261107T150331Z/deletion-candidates.csv)

Final verification: all **22 CLI checks** passed (including expected comparison
exit 20), and **23 focused migration/statistics tests** passed after installing
the comparison wrapper. All **16 SQLite table fingerprints**, schema and
accepted selections match the consistent backup; integrity is `ok`, with **760
accepted PUBs**. All **3,746 protected output/model/archive files** and the
protected output inventory are unchanged. No IBM calls or submissions occurred.

Maximum observed process peak RSS: manager post-tests **396.9 MiB**, synthetic
C/D equivalence **411.7 MiB**, legacy plotting suite **1,077.7 MiB**. Suites ran
sequentially with native numerical pools capped at one; existing diagnostic
subprocess tests remain bounded.

- [Exact changed files](../../../manager_audit_backups/migration-20261107T150331Z/changed-files.csv)
- [Final change/test report](../../../manager_audit_backups/migration-20261107T150331Z/change-report.json)
- [CLI checks](../../../manager_audit_backups/migration-20261107T150331Z/cli-checks.json)
- [Protected file verification](../../../manager_audit_backups/migration-20261107T150331Z/protected-after.json)

Styling-only publication: `campaign_comparison.py --restyle-only --authorize-output-write`
reuses all committed CSVs in place without recomputing statistics. An explicit
`--acknowledge-deleted-comparison-pngs` records operator-deleted comparison PNGs
as absent; all remaining hashes and canonical scientific readiness still pass.
The versioned plot_style_manifest records palette, geometry, clipping checks
and unchanged CSV hashes. Existing PNGs are never overwritten.

### Reference-method integration (current accepted dataset only)

Inspected the older project's `compare_105_pubs.py` (the exact `(3)` filename
was not found), SHA-256 `cc7e339c1150882cd4d403418d26b35f2c1fdac768c651b8fa76c2c3b4ceb4b7`,
and `compare_105_pubs_cluster_bootstrap.py`, SHA-256
`342cd95d5f62de15472586d27f2291a621ae393413f0d93de63d048cc631591b`.
These files are methodology references only; no reference CSV is loaded.

C/D retain their existing 3,000-draw, seed-1013 pooled median calculations,
prefix ordering, log axes, percentile bounds and current verified reference SSEs.
The common style adapts the reference fonts, annotations, direct interval endpoints
(which need not contain the point estimate), caps, margins and footer hierarchy.
Approved treatment colors replace the references' two-color performance grouping.

The cluster reference's joint whole-block resampling, contrasts, linear quantiles
and component-union logic are integrated in the existing statistics module.
Primary E/F now use 10,000 draws and the original reference seed 1013.
Historical v005 used seed 20261109 and remains unchanged. `--reference-sensitivities` adds two separate CSV tables while retaining
all primary CSV bytes. Equal-weight sensitivity contrasts arithmetic means of
six n-level medians (weights 1/6), NOT medians of the pooled 105 observations.
Component sensitivity pools entire connected sets of n-levels sharing effective
selected jobs. Components and links are derived from current selected job IDs;
no claim about the other experiment's recovery links is imported. Matching is
by design n-level only, not paired PUBs or experimentally independent jobs.
Both checks use 10,000 draws, seed 1013, shared treatment draws and linear
2.5/97.5 percentiles. All intervals remain sensitivity ranges, not inferential CIs.
The current canonical receipt/accepted-slot audit is retained instead of copying
the reference experiment's directory-specific audit and fixed recovery assumptions.

### Exp. 01 final original-method standardization

C is ordinary pooled-prefix bootstrap (counts 5..105; checkpoints 5,15,30,50,75,105),
and D is ordinary PUB-level bootstrap (105 values). Both use 3,000 replicates and
seed 1013 in canonical treatment traversal order. Neither accounts for within-job
or between-job dependence. These methods have not been replaced with clustering.
E/F use the same 10,000 matched n-level block selections, seed 1013, and contrast
within each replicate before linear percentile quantiles. Their intervals remain
conditional cluster-bootstrap sensitivity intervals; significance is not established.

The existing publisher's `--standardize-reference-methods` option recomputes and
exactly verifies C/D against the saved CSVs, keeps those CSV bytes, regenerates E/F
bounds with seed 1013, and requires identical central estimates. Optional verified
classical/ideal references are revalidated. Missing historical comparison PNGs
can be explicitly acknowledged without restoring or rewriting their old paths;
missing scientific inputs/CSVs, changed hashes and invalid provenance still fail.

```powershell
.\.venv\Scripts\python.exe -B 02_Hardware/04_code/03_ai_assistant/03_python_automation/campaign_comparison.py --standardize-reference-methods --acknowledge-missing-comparison-pngs --authorize-output-write
```

The operator must explicitly authorize publication before using the final flag.
The committed manifest supplies separate C/D/E/F method labels to the Dashboard.
