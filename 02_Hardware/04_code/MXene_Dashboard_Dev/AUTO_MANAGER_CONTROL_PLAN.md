> Current COLLECT capability: explicit `Collect completed jobs for n=30 Batch 005` is supported through the canonical STEP, subject to confirmation and strict original-first-attempt identity gates. Unscoped and replacement/mixed-attempt collection remain blocked. See **Scoped COLLECT-only STEP** below; earlier audit notes describe historical limitations.

# Phase 5A — AUTO broker implemented; hardware execution not exercised

This section supersedes the historical Phase 3/4 disabled-adapter notes below.
Phase 5B enables confirmed one-shot preflight and fixed-scope read-only Batch 005
monitoring, as specified below. Other mutating STEP operations remain blocked.
AI Agent, Skills, registry, loaders and prompts are unchanged.

## Phase 5B independent-operation audit and adapter

### Canonical STEP implementation: supersedes earlier blocked science adapters

`research_pipeline.execute_campaign_step(project, campaign_id, operation, n=...,
authorize_step=True, authorize_output_write=True)` is now the authoritative
prepare/analyze/export dispatcher. It does not contain a campaign state machine.
Prepare requires an existing campaign and verifies its six immutable preparations
without re-registration. Analyze calls `adaptive_targets.analyze_target` once;
Export calls `scientific_exports.export_n_level_results` once. Export does not
invoke analysis. Canonical numerical algorithms and plotting/export implementations
are unchanged. Existing AUTO uses the same functions as before.

The entrypoint owns canonical locking, validates the campaign manifest hash,
current backend/account/action authorization, frozen target reconstruction,
UNKNOWN state, complete selected-PUB coverage and source provenance. The registry
is opened read-only; checkpoints are compared before/after and never advanced.
No quota reservation or account selection is performed because these operations
submit no QPU workload; all existing submission quota/authorization gates remain
unchanged. Output writes require explicit operation-scoped confirmation.

Analyze records a create-only, fsync/atomic-published artifact receipt using the
existing `atomic_archive` helper. Its identity binds the target manifest and exact
accepted original/replacement dataset. Export checks receipt identity, canonical
output directory, and all streaming artifact hashes before packaging outputs.
No arbitrary output path or account/job/attempt argument is accepted. Missing or
changed analysis prevents export. Existing output directories are never reset.
Known precondition failures produce a durable STEP_FAILED report; unexpected
write/crash outcomes preserve the existing fail-closed broker reconciliation.

The fixed broker command now carries n-level scope in its durable launch record,
checks it against the one-use confirmed request, and delegates to this canonical
entrypoint. The wrapper observes the entrypoint's own locks rather than nesting a
second lock. Authorization/checkpoint identity is rechecked after lock acquisition.
Old brokers that do not advertise these operations keep them blocked.

Collection is intentionally not enabled: the legacy collector's retrieval/archive/
validation pass requires an independently verified exact job/attempt scope, with
submission JSON, grouped/replacement identities and restart behavior reconciled.
The canonical STEP API explicitly rejects collect/validate/recovery rather than
substituting AUTO or picking an attempt. Current Batch 005 jobs remain untouched.

Canonical entrypoint tests use the project `.venv` with isolated fixture files,
real canonical OS locks, mocked numerical/export delegates, immutable receipts,
hash mismatches, incomplete/UNKNOWN data, authorization failures and interrupted
publication. Dashboard HTTP confirmation/dispatch tests use mocked child launch.
These checks do not certify production provider connectivity or scientific
rendering on production data. Earlier classifications below are historical.

### Interrupted-session recovery verification

The existing implementation was inspected in place, not reset or rebuilt. No Git
repository is available at this directory or its parents, so uncommitted status
cannot be established. Fourteen Phase 5B source/documentation/test files are
inventoried with hashes in Dashboard `state/recovery-file-inventory.json`.
Fresh HTTP worker observation was STOPPED_VERIFIED, campaign PAUSED.

| Command | Verified classification | Boundary |
| --- | --- | --- |
| Campaign Status | IMPLEMENTED_AND_TESTED | Persisted campaign state and accepted counts; no provider query. |
| Worker Status | IMPLEMENTED_AND_TESTED | Broker process/lock ownership, not a stored RUNNING flag. |
| Batch Status | PARTIALLY_IMPLEMENTED | Recorded membership/job evidence; general fresh Batch state/TTL unavailable. |
| IBM Job Status | PARTIALLY_IMPLEMENTED | Local provider observations; fresh lookup limited to confirmed Batch 005 diagnostic. |
| IBM Quota | PARTIALLY_IMPLEMENTED | Saved quota evidence/reservations; fresh checks require confirmed preflight. |
| Recovery Status | IMPLEMENTED_AND_TESTED | Existing read-only logical-slot summaries. |
| Prepare | BLOCKED | Idempotent preparation adapter for this existing campaign is not verified. |
| Preflight | IMPLEMENTED_AND_TESTED | One canonical call under locks; offline mocks, not hardware certification. |
| Monitor | PARTIALLY_IMPLEMENTED | Confirmed fixed Batch 005 diagnostic only; generic canonical monitor also collects/writes. |
| Collect | SCOPED / REQUIRES AUTHORIZATION | Canonical original-first-attempt Batch adapter; ambiguous, replacement and unscoped requests remain blocked. |
| Review Recovery | IMPLEMENTED_AND_TESTED | Read-only, including selected n-level; no replacement planning/submission. |
| Analyze | BLOCKED | Canonical analysis-only isolation/publication authorization unverified; existing Analyze n=30 read-only research query is retained. |
| Export Figures | BLOCKED | Verified complete analysis manifest, target/provenance and standalone publication authorization required. |

Recovered defects: command chips used an ambiguous question-mark separator; use
an ASCII separator. Completed STEP report loading now validates bounded durable
evidence and matching terminal outcome. Missing, inaccessible or corrupt reports
block further control with an explicit reconciliation reason while preserving the
verified process observation. Neither receipt nor locks are cleared; no retry is
performed. The broker independently enforces this control block at dispatch.

Verification: nine Python syntax checks; 41 distinct focused tests across targeted
runs (40 initial tests plus one added inspection regression; 28 broker tests passed
on recheck); 11 isolation checks; 12 actual localhost HTTP status/help/blocked
commands with zero Ollama calls; served-JavaScript/HTTP prompt persistence and
editing checks using a DOM harness. No native-browser visual verification or
production provider query was performed. Seven production registry/WAL/SHM/lock
fingerprints and 228 protected source/output hashes were unchanged. Prior legacy
guide-test failures were not rerun or claimed repaired.

Remaining work is the blocked adapters and real deployment/provider verification,
not restoration of lost code. AUTO, read bridge, Quick Assistant, persistent
workspace, Ollama, existing AI Agent and Skills remain intact. No production
manager was started or interrupted.

Follow-up: fixed-scope `Monitor Batch 005` is now supported through the same
one-use broker wrapper, reusing the existing project-level
`manager_diagnostics/ibm_batch_005_readonly.py:main` without modifying it. This is
not `batch_monitor.monitor_batch`: it reads registered identities, verifies labK's
canonical account/instance scope, queries statuses and Batch metadata only, and
never retrieves result payloads. Exactly one diagnostic invocation is made under
the canonical locks. The diagnostic source hash is included in broker policy
identity. Authorization names n=30 Batch 005/labK/ibm_fez. Missing/mismatched job
identities stop the diagnostic before provider access. Its existing offline tests
cover pending, terminal, UNKNOWN/network failure and metadata failures.

The broker advertises supported operations in verified status. The Dashboard
blocks new STEP capabilities when talking to an older broker. READ_ONLY command
groups use local snapshot evidence (with separate capture/provider timestamps);
only explicitly confirmed preflight/Monitor Batch 005 may perform provider reads.
`Monitor / Collect` never means two operations: it asks for one scoped command.
The table below remains valid for the generic/mutating functions. No canonical
preparation/collection/analysis/export implementation has been duplicated or
silently replaced with an AUTO cycle.

The implementation reuses `Broker.dispatch` and its fixed child template. For
`preflight` only, the child acquires `worker_runtime.campaign_worker_lock`, checks
the unchanged authorization/configuration fingerprint again, calls
`read_only_preflight.preflight_all` once and saves the sanitized result under
Dashboard state. It never calls the AUTO pipeline or advances a campaign.
Use `Run preflight`, explicitly confirm the provider-read operation, then
`Show step result`. No LLM, submission authorization or account selection is involved.
The saved-profile factory and `campaign_authorization_from_final_config` supply
the existing account/instance scope; the canonical preflight currently supports
ibm_fez, so a different configured backend is blocked rather than assumed safe.

| Canonical capability audited | Disposition |
| --- | --- |
| `read_only_preflight.preflight_all` | Enabled. Returns reports without writing them. Saved-profile provider reads and frozen bundle validation; canonical locks serialize execution. |
| Campaign prepare/register | Blocked. Registry/manifest writes, no verified existing-campaign idempotent prepare adapter. |
| `batch_monitor.monitor_batch` | Blocked. Also calls result retrieval, archival, validation, registry transitions, usage and monitor snapshot writes. Cannot expose as read-only monitoring. |
| `vqe_adapter.collect_block`, `collect_block_for_recovery` | Blocked. Durable archive/validation writes require precise account/block/attempt scope and additional authorization. |
| Targeted recovery planning/submission | Blocked. Plans persist; replacement execution may submit. Existing read-only recovery review is retained. |
| `research_pipeline.process_pipeline`, `adaptive_targets.analyze_target`, `scientific_exports.export_n_level_results` | Blocked for execution. Combined canonical analysis/export writes; existing `Analyze n=5` is a different, read-only research query and remains enabled. |

Confirmed preflight can diagnose a completed campaign or UNKNOWN remote identity
because it does not reconcile or submit anything. Unverified local worker ownership
still blocks it. Concurrent AUTO/STEP and AUTO attach/pause of a preflight child
are rejected. A broker/child crash does not cause retry. Results are retained
separately from conversations and replay is prohibited after refresh. Lock files
are managed only by canonical locking during future authorized execution; no
production lock or registry was written during the offline development tests.

Remaining gates: verify deployment permissions, real provider-read behavior and
timeouts in the operator environment. Mutating STEP paths require separate scope,
authorization and isolated output/publication verification; do not enable them
by calling an entire AUTO cycle with `once=True`. Hardware readiness is unproven.

## Exact execution path

Workspace -> typed Supervisor -> conversation-bound single-use confirmation ->
`broker_client.call` -> loopback `auto_broker.Broker.dispatch` -> fixed command:
`<project>/.venv/Scripts/python.exe -B <dashboard>/auto_broker.py --worker <request UUID>`.
The child invokes `research_pipeline.run_pipeline(project, campaign, mode='live',
authorize_live_campaign=True, confirm_live_submission=True, ai_mode='python',
once=False, resume=<requested>, poll_seconds=60)`. That is the existing production
AUTO workflow. The broker does not choose jobs, PUBs, accounts or recovery work.

The fixed wrapper temporarily decorates `worker_runtime.campaign_worker_lock` in
its own process: it calls the original context manager, records ownership only
after both original OS locks are held, and releases using that same context.
No production source file or locking semantics are replaced on disk. The wrapper
restores the original function on exit. Receipt fields: campaign, request, PID,
creation time, exact executable/argv and ancestor PIDs for Windows venv launchers.
Status requires matching identity AND both lock bytes busy. Read-only probes never
create, delete or clear production lock files. Foreign/unreadable ownership is
UNKNOWN; dead workers without a terminal receipt are STALE_UNRESOLVED.

## Security and persistence

Run `start_auto_broker.ps1` explicitly in a separate terminal. Startup never starts
a campaign. Port 8767 binds only to 127.0.0.1; only /status and /dispatch exist.
The web process retains security.install, IBM-import and subprocess denials and
production-write denial. A ContextVar grants only the fixed broker loopback port.
The separate broker has the operator's existing manager permissions; it is a
trusted local execution boundary, not an OS sandbox against that same Windows user.
Its random server-only bearer token is protected by Windows user-bound DPAPI in
Dashboard state and is never sent to the browser, logged or stored in the campaign
registry. There is no plaintext fallback; broker startup upgrades its development
token file. Protect the local Windows account; same-user processes are trusted.

Dispatch requires a matching unexpired DISPATCHING row in dashboard-only SQLite;
the browser cannot send a command, path, account or script to the broker. The row
binds ownership, checkpoint, config authorization, frozen manifest and code hashes.
Broker and child consume separate durable one-use receipts before any launch or
canonical execution. Detached browser requests do not govern worker lifetime.
Dashboard refresh/status never restart workers. No automatic restart after crash.
Fresh canonical quota/reservation/frozen/UNKNOWN gates remain in the manager.

Pause reuses `control_independent_campaign(action='pause')`, never terminate/kill.
PAUSE_REQUESTED is not acknowledgement; the canonical loop acknowledges at its
existing safe boundary and leaves IBM jobs intact. A verified active worker is
attached instead of duplicated. Legacy workers without a trusted receipt remain
UNKNOWN and cannot be adopted or paused by this adapter.

## Verification and remaining scope

The real-process fixture test uses the canonical project interpreter and real
canonical locks/AUTO outer loop with a fake registry and campaign cycle. It verifies
RUNNING_VERIFIED, attachment, reconstruction after web restart, canonical pause
acknowledgement and STOPPED_VERIFIED exit. No production campaign is launched.

Offline tests cover fixed launch templates, authorization expiry/change, one-use
dispatch, wrapper ownership under REAL canonical locks on fixture files, duplicate
locks, resume, pause boundary, UNKNOWN, config/quota-block propagation, crash and
refresh. Campaign cycles/provider execution are mocked; this is not an end-to-end
hardware campaign certification. The actual HTTP test prepares but NEVER confirms
a production authorization; verifies no launch/worker/dispatch artifacts appear.
Read-only production inspection observed STOPPED_VERIFIED, ibm_fez, valid configured
account scope, no UNKNOWN submissions and an incomplete campaign. No production
worker was launched or interrupted. Before LIVE: inspect current status again,
review scope/limits in the confirmation and resolve any UNKNOWN/STALE evidence.

Start broker: `.\start_auto_broker.ps1` (or `-Restart` for that verified service only).
Restart Dashboard only: `.\start_dashboard.ps1 -Restart`.
Stopping the broker does not pause an AUTO worker; use canonical cooperative pause.

## Historical Phase 4 audit (superseded where noted above)

## Phase 4 verification update

AUTO aliases `Run Auto Manager`, `Resume campaign`, `Pause Auto Manager` and
`Show Auto Manager status` are recognized deterministically. `delegate_canonical`
maps AUTO to the existing `research_pipeline.run_pipeline` with both authorization
flags, `once=False`, no account override and canonical resume semantics. It is a
broker-side dispatch function, **not connected to an enabled production broker**.
Neither prompts nor Ollama can invoke it. Production start/pause/resume stay BLOCKED.

`test_phase4_control.py` now exercises real canonical WorkerLock exclusion/release
and the real `run_campaign_auto_manager` outer loop with mocked campaign cycles,
fixture markers and a fake registry. It verifies checkpoint-boundary pause,
resume forwarding and no retry after UNKNOWN. This does not verify a real worker's
lock-owner identity or the inner IBM/scientific cycle. No production worker is run.

The supervisor now blocks ALL fresh control requests after unresolved DISPATCHING
or UNKNOWN dispatches, including after restart. Reconciliation remains required;
there is deliberately no UI bypass or automatic clearing of those records.

Safe STEP available: **Review targeted recovery** uses the existing read-only
canonical-backed snapshot. Existing `Analyze n=5`, quota and campaign progress
queries remain read-only Python operations. No step advances the campaign.

Explicitly blocked STEP requests:
- Prepare: no audited idempotent registration adapter; never reset this campaign.
- Preflight: provider reads/report writes require a restricted broker and lock.
- Monitor/Collect: canonical functions require verified block/account/attempt
  selection; the Dashboard must not independently schedule them.
- Analyze n-level: canonical process_pipeline combines analysis and exports and
  writes publication outputs; output authorization/ownership are unverified.
- Export: no audited export-only campaign step; do not silently rerun analysis.
- Run Batch n=5: `once=True` executes a whole cycle, not a single batch; unsafe.

Workspace prompt suggestions populate editable input only. Unknown ownership,
offline snapshots, future timestamps or observations older than 180 seconds show
read-only prompts only. The eight requested operational suggestions exist for a
verified idle/available adapter and fresh snapshot; current production cannot
qualify. Completed campaign snapshots suppress preparation and submission prompts.
Suggested prompts are never execution authorization. Quick Assistant has no
operational suggestions and rejects operational aliases.

Updated 2026-11-08. `worker_supervisor.py` and workspace routes now provide a
typed, durable control-plane contract. The default production adapter is discovery
only and has no execute method. Production control requests return BLOCKED without
creating a LIVE authorization. Tests use a fake execution adapter exclusively.

## Audited canonical call map

| Operation | Existing implementation | Integration constraint |
|---|---|---|
| AUTO start/resume | `01_ai_agent/mxene.py` run -> `research_pipeline.run_pipeline` -> `independent_n_campaign.run_campaign_auto_manager` | Both LIVE flags, final config authorization, frozen inputs, quotas, UNKNOWN and canonical lock remain authoritative |
| Pause | `independent_n_campaign.control_independent_campaign(action='pause')` | Writes marker/checkpoint; returns request, NOT worker acknowledgement; loop checks at safe cycle boundaries |
| Ownership | `worker_runtime.campaign_worker_lock`, `WorkerLock` | OS lock contains no PID/start-time/owner receipt; mere lock-file presence proves nothing |
| Preflight | `read_only_preflight.preflight_all` | Provider network/authentication and report writes; not a pure Dashboard read |
| Monitor | `batch_monitor.monitor_batch` | Registry writes and potential raw result archival; requires account, block and frozen identity; expects all eight treatments |
| Collect | `vqe_adapter.collect_block`, `collect_block_for_recovery` | Account/block/attempt and archive binding required; cannot safely dispatch an unspecified campaign collect without canonical selection audit |
| Analyze/export | `research_pipeline.process_pipeline(n=...)` | Complete-only gate; calls analyze_target/export_n_level_results and writes canonical outputs; needs single-step lock and output authorization |

`run_pipeline(once=True)` is NOT a monitor-only step: it can execute submission.
Original/recovery quota thresholds remain 150/40 seconds; runtime estimate plus
margin is diagnostic only. No execution logic or quota constants were changed.

## Activation blockers

1. Establish a verifiable owner receipt tied to the actual canonical lock lifetime,
   PID creation time and campaign. Adopt existing workers only when this identity
   is proven. PID reuse, wrapper children and denied process inspection must fail
   closed. Current process discovery deliberately cannot authorize pause/start.
2. Implement and independently validate a narrow execution broker. The current
   process sandbox prohibits subprocesses and all production writes. Do not relax
   it globally or treat an injected mock adapter as a production implementation.
3. Bind confirmation to canonical configuration, explicit account/instance scopes,
   checkpoint identity and quota effects. Revalidate at dispatch; retain the manager's
   fresh provider checks and reservation accounting. No LLM authorization.
4. Run the REAL canonical worker under fake IBM services and isolated frozen inputs,
   registry and output paths. Verify lock acquisition races, UNKNOWN reconciliation,
   crash recovery and safe pause acknowledgement. Current tests cover only the
   supervisor contract, not these integration cases.
5. Audit per-step account/block selection and acquire the same canonical locks;
   never substitute a complete worker cycle for a requested individual step.

The adapter protocol is `inspect`, `confirmation`, `execute`. Execution must
atomically recheck ownership and acquire canonical locks; Dashboard request
serialization is not a replacement for WorkerLock. Start/Resume can attach to a
verified running worker; mutations are excluded while a worker is active.
Requests survive refresh in dashboard-only SQLite, expire after 120 seconds and
are consumed before dispatch. DISPATCHING/UNKNOWN must be reconciled, never retried
automatically. Pausing never kills a process or cancels an IBM job.

## Existing authority

The root manager compatibility entrypoint forwards to `01_ai_agent/mxene.py`.
That CLI calls the existing deterministic campaign implementation in
`03_python_automation/independent_n_campaign.py`. `worker_runtime.py` owns
`campaign_worker_lock`, including the repository-wide `auto_manager.lock` and
campaign `worker.lock`. `job_registry.py` owns durable identities, checkpoints,
accepted logical slots and UNKNOWN reconciliation. Never duplicate these.

## Preconditions before implementing control

1. Audit the actual CLI arguments and the manager's available safe pause boundary.
   The existing `control_independent_campaign(..., action="pause")` creates a durable pause marker and checkpoints a request. Reuse it only after verifying worker acknowledgement at safe boundaries. Do not invent flags, kill processes, or describe stopping Python as cancelling IBM work.
2. Resolve the workspace root once, using the existing launcher convention.
   Relative archive provenance must be validated from that root. Direct startup
   from the dashboard folder currently causes replacement provenance checks to
   omit accepted records; the read bridge refuses that working directory.
3. Build a separate explicit operator authorization boundary, not an LLM tool.
   A normal chat message must never grant LIVE authorization. Retain both existing
   CLI confirmations, campaign/account scope and all canonical Python gates.
4. Review credential isolation and service-process permissions independently.
   Do not disable the dashboard security guard to make control possible.

## Proposed control requests (not registered routes)

- **Start:** pass a typed campaign request to the existing worker entrypoint under
  explicit authorization. The worker itself must acquire both existing locks
  before doing work; a UI pre-check is not sufficient to prevent races.
- **Resume:** invoke the same worker with its existing resume semantics. Reuse
  checkpoints; reconcile all existing and UNKNOWN identities before submitting.
- **Pause:** reuse `control_independent_campaign` and verify that its durable marker is
  acknowledged by the running worker at a safe checkpoint. Keep remote jobs intact. Do not
  use forced termination as ordinary pause or write registry states from the UI.
- **Individual steps:** audit each canonical function's read/write/network
  effects, then run only supported steps inside the worker's locking and
  authorization context. A second process must not collect/recover concurrently.
- **Progress:** publish bounded read-only heartbeat/status evidence from the
  authorized worker. Distinguish heartbeat freshness, process liveness, campaign
  state and IBM job state. Until verified, worker liveness stays UNKNOWN.

## Required acceptance tests before activation

Use a separate fixture registry and fake provider: concurrent Start/Resume
requests must yield one worker; stale lock files must not imply active ownership;
pause acknowledgement must survive restart; UNKNOWN must never resubmit; stopped
local workers must not cancel remote jobs; authorization/quota/frozen hashes and
reservations must remain enforced. Verify interrupted submission, process death,
recovery provenance and checkpoint resumption. No production activation until
these tests and the permission boundary are independently reviewed.

Workspace control routing and the offline supervisor contract are implemented.
Production subprocess launch, registry writes and provider calls remain disabled.
The existing scientific tool bridge remains read-only to production.


## Scoped COLLECT-only STEP (offline verified; not production LIVE certified)

AI Research Workspace accepts **Collect completed jobs for n=30 Batch 005.** It requests explicit authorization for provider result reads, immutable raw archival and canonical registry/validation writes. Typing a command does not execute it. Quick Assistant remains read-only.

The secure broker passes `operation="collect", n=30, batch=5` to the existing `research_pipeline.execute_campaign_step` under canonical campaign/global WorkerLocks. AUTO is not started. No submission, cancellation, replacement, analysis, export or campaign advancement occurs.

Supported scope is deliberately narrow: an existing original Batch with exactly eight distinct M0-M7 jobs, five local PUB indices each, one attempt per treatment (attempt 1), one registered Batch ID, and no linked replacement records. Batch sequence comes from the target registry, never from provider list order. The frozen target, campaign manifest, current account/backend/action authorization and UNKNOWN gates remain mandatory.

Before any write, every `job_submitted.json` and `submission_state.json` must agree with the registered job, logical block, treatment, account, instance, backend, frozen hashes, five-PUB mapping and 4096 requested shots. The submission intent must have an attempt ID and its submission-start timestamp must match the sole registered first attempt. Legacy registries do not store that UUID separately: uniqueness plus exact job/intent binding is required; multiple attempts are refused. The registered Batch ID must match the submission file. The existing `batch_scheduler.verify_reconciliation_job` verifies provider identity, instance, creation time, Batch, circuit, theta, observables, precision and requested scientific treatment options. Unavailable provider evidence blocks the operation; it is never guessed.

Only currently DONE/COMPLETED jobs reach `vqe_adapter.collect_block` with one explicit treatment and `job_row_id`. A narrow service facade refuses other job IDs, and result retrieval rechecks completion. This reuses the AUTO pilot's archive-before-validation observer, content-addressed `atomic_archive`, canonical PUB ledger transactions and progress updates. No alternative result decoding or scientific validation engine is added. The existing registry is opened without schema initialization; write mode is enabled only after all scope/provider checks, under the canonical lock.

Responses report completed jobs found, newly collected jobs, already archived jobs, valid/invalid/missing PUBs, pending/failed jobs, unvalidated PUBs and campaign state. Missing means explicit missing PUB results in an archived response; pending/failed jobs remain unvalidated, not fabricated missing results. Existing verified archives/ledger records are reused without retrieval. Counts do not imply CSV publication or analysis completion.

**Still blocked:** replacement/mixed-attempt Batches, campaign-wide collection, missing/conflicting submission identities, unknown provider states, active workers, unauthorized instances, changed frozen inputs, incomplete legacy publication without bound validation evidence, and inconsistent raw/archive/ledger writes. Interrupted raw-only writes can be retried through the same canonical immutable archive path; partial legacy publication or partially committed validation requires reconciliation. No files are repaired or overwritten automatically. Scientific-invalid PUBs remain archived and individually validated; this STEP never schedules recovery.

Restart the Dashboard and its existing broker in separate terminals from this directory:

```powershell
.\start_dashboard.ps1 -Restart
```
```powershell
.\start_auto_broker.ps1 -Restart
```

These restart only the verified services, not the production Auto Manager. Browser: http://127.0.0.1:8766. Do not confirm a production COLLECT request until provider access and the scope's safety gates have been verified by the operator. Mocked tests do not establish production LIVE readiness.


## Single-PUB section ? preview and existing-job reads, LIVE creation BLOCKED

AI Research Workspace now has **AI Chat** and **Auto Manager** sections. The existing command panel appears once in Auto Manager; conversation history/New Chat remain in AI Chat. Selecting a command switches to the editable chat input and does not execute it. Quick Assistant is unchanged and read-only.

Under Auto Manager / STEP-by-STEP, Single-PUB Execution defaults to TEST preview. Purpose, n-level, canonical M0?M7 treatment, configured authorized account and existing logical slot can be selected. Opening/selecting controls performs no provider request. Prepare calls `/api/single-pub/preview` -> authenticated broker `/single-pub-preview` -> canonical `research_pipeline.preview_single_pub`. This reads existing configuration/registry/frozen target references without creating plans, reservations or namespaces. Relative account labels contain no credentials. Recorded candidates are not new submission authorization.

**Implemented:** preview; typed exact scope binding; existing single-PUB recovery job monitoring; DONE-only collection through `execute_campaign_step(operation="single_pub")` and existing `targeted_recovery.collect_replacement`. These operations require the existing one-time, conversation-bound broker confirmation, campaign/global WorkerLock and unchanged manifest/UNKNOWN gates. Account, instance, backend, Batch, Job ID, one-PUB provider payload and current replacement attempt must agree. Changed attempts invalidate the scope fingerprint. Grouped/mapped replacements fail closed. Pending/failed/UNKNOWN jobs are not collected or replaced. Collection verifies the canonical raw archive digest before reporting success. Accepted data and campaign checkpoints are not advanced by a monitor request. No independent execution loop was added.

**Not implemented/enabled:** new TEST or RECOVERY job submission from this panel. Disabled Preflight/Authorize/Submit buttons and HTTP 409 responses cannot be bypassed by crafted UI payloads. TEST has no separately approved quota or canonical isolated storage/ledger contract; no test namespace or result is claimed to exist. RECOVERY's existing `submit_replacement_pipeline`/`submit_replacement_slot` already create one `Estimator.run([pub])` job, enforce frozen science, 40-second recovery quota and atomic plan claims, and preserve UNKNOWN on ambiguous submission. However, the Dashboard still needs a fresh original/prior-attempt terminal-state reconciliation and provider quota quote bound to an exact one-time submission approval. A saved PLANNED state alone is insufficient, especially across accounts. The generic CLI is not exposed as an escape hatch. Ordinary AUTO Batch structure and 150/40 quota policy are unchanged.

Public request API: `POST /api/conversations/{id}/single-pub` supports only RECOVERY `monitor`/`collect` with n, treatment, slot, account and the canonical preview scope ID; it prepares a confirmation, not an immediate operation. Completed results use the existing Show step result / worker-status path. No Ollama call is needed. The canonical replacement ID/attempt plus broker one-use grant provide read/collect replay protection; no new LIVE idempotency contract is claimed.

Deployment requires restarting the two existing services in separate terminals, from this Dashboard directory:

```powershell
.\start_dashboard.ps1 -Restart
```
```powershell
.\start_auto_broker.ps1 -Restart
```

Neither command starts the production Auto Manager. Browser: http://127.0.0.1:8766. No LIVE readiness claim: authenticated provider access and the new reads were tested only with mocks; submission remains blocked pending the contracts above.
