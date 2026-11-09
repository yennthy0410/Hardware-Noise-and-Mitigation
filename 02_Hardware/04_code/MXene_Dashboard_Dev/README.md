## Unified startup, Batch navigation and scientific Guide — 2026-11-09

This section supersedes the two-terminal startup instructions below. No additional
dependencies are needed. From the project directory:

```powershell
& '.\02_Hardware\04_code\MXene_Dashboard_Dev\start_all.ps1'
```

For the first start after installing this update, or to restart the two services:

```powershell
& '.\02_Hardware\04_code\MXene_Dashboard_Dev\start_all.ps1' -Restart
```

Open **http://127.0.0.1:8766**. The launcher runs the existing Dashboard and Broker
entry points in hidden processes and prints health/errors and the URL. It preserves
Ollama environment settings and both original individual startup scripts. A
Windows file lock serializes unified launches; exact command-line process identity,
listener ownership, authenticated Broker health and port checks prevent duplicate
service launches. Unknown port owners, inaccessible identities and multiple
independent instances fail closed. Restart targets only the verified service
processes, never automation/manual worker descendants or arbitrary Python processes.
Logs are `state/service-dashboard.log` and `state/service-broker.log`.

Startup requests **no** automation action and no IBM submission, recovery or
cancellation. Stopped/paused automation remains stopped/paused. An already-running,
previously authorized worker is preserved, not silently paused or cancelled.
Campaign progress and pending jobs are not reset. The historical Dashboard remains
available if the Broker fails or automation is paused. An older Broker without the
new transport-only health endpoint needs `-Restart` to load this version.

### Audit findings and control states

The previous scripts were separate foreground launchers. Their individual restart
identity checks did not provide a unified health/reuse workflow. AUTO command
discovery mixed worker ownership, authorization and campaign checkpoint state;
Start/Resume could be presented as attach operations while already running. The
new presentation separates operational state from authorization and blocks invalid
Start/Pause/Resume choices using the same existing confirmation path and canonical
execution checks. No execution engine, quota or recovery policy was changed.

| Operational state | Meaning and available control |
| --- | --- |
| STOPPED | No verified automation worker is running. Start Automation is available only when existing policy and reconciliation permit it. |
| RUNNING | An automation worker owns the verified locks. Pause Automation requests a cooperative pause; running IBM jobs are not cancelled. |
| PAUSED | The campaign is recorded paused and the worker is no longer running. Resume Automation continues the existing checkpoint. |
| ERROR | Ownership/service evidence is uncertain or a recorded failure needs investigation. No automatic retry; this is not proof that IBM jobs failed. |

A pending pause remains RUNNING until worker exit/lock release is verified.
One-shot manual/STEP work is identified separately and blocks conflicting controls.
Authorization is shown as `POLICY_CONFIGURED` or `UNAVAILABLE_OR_DISABLED`.
Configured policy is not live permission: preview, direct confirmation, fresh
quota, frozen-input verification and duplicate/UNKNOWN reconciliation still apply.
Each disabled control displays its reason; Refresh automation state performs only
the existing local broker observation. The Hybrid Chat interface and its read-only
capabilities remain unchanged.

### Batch numbering and provenance

The previous records table prioritized Job IDs and used UUIDs as Batch filter
labels despite already carrying registered sequence metadata. The table now shows
Batch number, n-level, account, backend, planned PUBs, recorded execution status,
validation/accepted counts and recovery history status. Expand Batch, then Job,
to inspect treatments, PUB validation, events and original/replacement provenance.

Numbering uses `experiment_blocks.sequence_no` from the canonical projection,
falling back only to the sequence suffix of its registered logical block identifier.
It is **scoped to the n-level/logical block**, not globally assigned from n-levels
or sorted UUIDs. Missing or conflicting registration remains unnumbered. No mapping
or numbering is written into historical records. Replacement Runtime Batch UUIDs
remain in their original fields beneath the parent logical Batch. Job IDs, UUIDs,
timestamps and original/replacement relationships remain visible and copyable.

Filters cover n-level, registered Batch, treatment, account, recorded status,
original/replacement class and identifier search. Counts describe the matching
selection. Replacement submissions and repeated treatment attempts do not inflate
the original planned-PUB denominator. Historical local/provider observations retain
their timestamps and are never labeled live; refresh does not query IBM.

### User Guide additions

The existing ten-section structure and escaped-text renderer are retained.
Sections 1–5 and 8 now explain unified startup, Start/Pause/Resume, Batch navigation,
diagnostics, incomplete/recovered cohorts and provenance. Section 7 (Hybrid Chat)
is unchanged. Section 9 contains:

- **9.1 IBM QPU Execution Failures and Recovery Mechanisms**: physical noise versus
  numerical instability, Runtime/retrieval failures, missing/invalid PUBs, UNKNOWN
  reconciliation and targeted recovery. Noisy valid results are not automatically
  recovery candidates.
- **9.2 Why Error Mitigation Can Improve or Worsen EIS Fitting**: DD, TREX, ZNE,
  combinations, bias/variance, extrapolation weights, drift, parameter sensitivity
  and first-order covariance propagation. The documented canonical definition is
  `SSE = sum(abs(Zfit-Zexp)^2)` and `RMSE = sqrt(SSE/(2*N))`. The residual-linearization
  discussion explains why lower expectation error need not reduce experimental SSE.

Primary references are linked in the Guide: IBM's mitigation documentation,
Temme–Bravyi–Gambetta (2017), and van den Berg–Minev–Temme (2022). The Guide makes no
new numerical claims, causal attribution or significance assertions. Equations are
readable Unicode text, with no additional renderer or external asset dependency.

### Changed files

All files below are within this Dashboard directory:

- New: `start_all.ps1`, `service_launcher.py`, `test_usability.py`,
  `test_automation_ui.cjs`.
- Modified backend/transport: `auto_broker.py`, `broker_client.py`,
  `worker_supervisor.py`, `execution_records.py`.
- Modified frontend/documentation: `static/records.js`, `static/jobs.css`,
  `static/research_workspace.js` (AUTO controls only), `static/guide.json`,
  `static/guide.js`, `README.md`.
- Updated regression fixtures/contracts: `test_execution_records.py`,
  `test_records_ui.cjs`, `test_guide.py`, `test_guide_dom.cjs`.

No scientific calculation, execution ledger, frozen input, quota threshold,
canonical IBM submission/recovery module, Hybrid Chat provider or conversation
storage implementation was changed. Test logs/isolated fixtures are runtime
artifacts, not scientific outputs.

### Executed validation and limitations

Executed successfully: 256 selected Dashboard Python tests and 16 independent
Manual Execution/scientific-integrity tests; PowerShell launcher syntax parsing;
73 Guide/navigation DOM checks; 6 AUTO control assertions; Batch records grouping,
pagination/filter/provenance contracts; 18 Hybrid Chat assertions; 40 Manual UI and
contrast assertions; and the existing Manual Workspace contract suite. The 17 isolated Dashboard/cloud security checks also passed.

Startup/restart/process tests use mocks, except the actual Windows launcher-lock
contention test. They do not restart live services or invoke IBM. The scientific
regression uses synthetic PUB values and verifies frozen/model/data hashes. Early
Guide fixtures assumed an obsolete tab count/gallery representation and were
updated to the current interface. Final selected suites passed.

From this directory, reproduce the Dashboard checks:

```powershell
.\.venv_dashboard\Scripts\python.exe -B -m unittest test_usability test_guide test_execution_records test_worker_supervisor test_auto_broker test_command_discovery test_hybrid_agent test_research test_workspace test_interpretations test_bridge test_data_sync test_manual_steps test_campaign_progress test_canonical_steps test_single_pub test_independent_manual_http -q
node test_guide_dom.cjs
node test_records_ui.cjs
node test_automation_ui.cjs
node test_hybrid_ui.cjs
node test_independent_manual_ui.cjs
node test_manual_workspace.cjs
```

DOM tests are not rendered-browser or screen-reader certification. Real-service
restart and live IBM behavior were not exercised. No IBM job was submitted,
recovered, cancelled or queried during this implementation. Existing running
workers deliberately continue across a service restart; use the explicit Pause
Automation control if a cooperative pause is desired.

## Independent Manual Execution and Auto Manager UI — 2026-11-09

This section supersedes older statements that standalone submission is unavailable.
The three workspace tabs are **AI Chat**, **Auto Manager**, and **Manual Execution**.
No IBM job was submitted, cancelled, recovered, or queried during this upgrade.
No new packages are required. Keep the existing lightweight Ollama configuration.

### Audit findings and scope

The manager placed global dark-theme text inside white cards and tables. Scoped
light-surface CSS now covers table cells, headers, controls, disabled/hover/selected
states, expanded details, status messages, and focus indicators. The rest of the
Dashboard keeps its existing appearance. The defined normal-text color pairs pass
WCAG AA contrast calculations; browser rendering is not certified by those tests.

The old STEP flow depended on campaign/n-level/batch/scope selections, and its
advanced single-PUB view deliberately blocked new experiments. The existing
`batch_scheduler.submit_runtime_block` already owned quota reservations, durable
submission intents, Runtime creation and ambiguous-submission handling. Its new
explicit manual-selection argument reuses that same engine for treatment subsets
and standalone Runtime jobs. Default campaign calls keep their full eight-treatment
behavior. `job_registry.reserve_batch` accepts an unavailable diagnostic runtime
estimate only for isolated manual targets; the verified 150-second admission policy
still applies. No scientific calculation or recovery policy was changed.

### Operating the simplified workflow

In STEP-by-STEP choose **Execution Target** and **Operation**, then continue to
Manual Execution. The old controls remain under **Advanced registered Campaign
operations**. Manual Execution also opens directly, without loading a campaign.

1. Choose Existing IBM Job ID, Registered Campaign, or New Manual Execution.
2. Choose Check Status, Retrieve Results & Validate, Inspect Errors / Logs, List
   Jobs, Analyze, Export Figures & Data, Submit Batch, Submit Single / Selected PUBs,
   or Targeted PUB Recovery. Only relevant fields are displayed.
3. Select an authorized saved account. Submission uses the frozen VQE `run_02`
   adapter on the configured `ibm_fez` backend. Choose the original n-level,
   treatments, and comma-separated repetition labels (for example `1` or `1,3`).
   A blank Batch selection means all repetitions. Give a new experiment a distinct
   label. All eight treatment combinations are available.
4. **Preview** shows the exact PUB count, account, backend, selected repetitions,
   treatments, frozen manifest, output destination and provenance. **Preflight**
   performs provider reads and revalidates authorization, quota and inputs.
5. **Confirm / Execute** requires a direct confirmation for that exact scope.
   Approval expires after five minutes and can be consumed once. Inspect the
   returned request with **Refresh operation result**; there is no automatic retry.

For one independent PUB choose Submit Single / Selected PUBs, one treatment, and
repetition `1` (or another explicit repetition). This uses `Estimator(mode=backend)`
through the existing scheduler and does **not** create a Batch. Batch subsets use
the existing Batch context. Both are new independent experiments, not replacements.

For recovery supply the **original Job ID**, original n-level, treatment and exact
logical PUB. The original must have compatible registered provenance and archived
invalid/missing evidence. The canonical planner refuses accepted slots and pending
or UNKNOWN replacements. The canonical recovery adapter submits only the selected
invalid PUB, keeps the original Job ID and archive, and records replacement lineage.
Retrieve the replacement Job ID to validate it; submission/DONE alone is not acceptance.
Retry delays and reconciliation remain mandatory; there is no added retry-count cap.

Saved requests survive restarts in `state/manual_runs/control.sqlite3` and are
available under **Recent manual operations / restore after restart**. Restoring a
request permits inspection, not replay of a consumed approval. Manual and AUTO use
the same canonical execution locks, so a busy manager blocks a conflicting manual
operation. Pause AUTO and wait for its current operation to release the lock. This
never cancels IBM jobs already running. A stale lock requires existing ownership
reconciliation; Manual Mode never steals it.

### Retrieval, analysis, export and diagnostics

Authorized Job IDs outside Campaigns can be inspected and archived without adding
them to the mitigation campaign. Diagnostics include available provider status,
metrics, errors/logs, account/backend, archive hashes and PUB validation, with
credential redaction. Unknown jobs and inaccessible accounts fail explicitly.
The adapter fetches a selected result once per operation and shares it with the
canonical collector. A result with NaN, invalid uncertainty, or an unexpected PUB
count is not offered as a validated EIS analysis artifact.

Analysis/Export take the **manual retrieval request ID**, which resolves a bounded,
hash-verified artifact. They reuse `mitigation_pilot.calculate_pub_eis` with the
existing EIS data. Outputs include `metrics.csv`, provenance and, for Export,
`manual_metrics.png` (SSE/RMSE diagnostic plots). These are separate from publication
exports. Use the retained advanced n-level controls for the existing full scientific
analysis/export workflow. Outputs use new per-request folders under
`state/manual_runs`; no overwrite option or arbitrary output path is exposed.
Canonical submission/collection records remain in their established execution-record
location, with unique `manual-<request-id>` targets outside campaign membership.

Arbitrary external jobs lacking verified compatible scientific input provenance
can be archived but cannot be treated as MXene EIS experiments or recovered here.
No QAOA execution adapter is installed, so QAOA and arbitrary circuits remain
unavailable. These restrictions are displayed rather than silently guessing inputs.

### Advanced Manual Command Console

Supported commands are `help`, `list jobs`, `job status <job_id>`,
`job retrieve <job_id>`, `job inspect <job_id>`, `batch submit`, `pub submit`,
`pub recover`, `analyze <manual_run_id>`, and `export <manual_run_id>`.
The run ID is a validated path capability, not a filesystem path. Completion
suggestions appear as you type. Commands use the form's account and scope and
prepare previews only; preflight and direct confirmation are still required.
The console cannot run Python, PowerShell, cancellation or arbitrary shell commands.
Copyable diagnostics show the exact local request log location.

### Safety boundary and n=30 reconciliation

AI Chat retains its read-only tool allowlist, Ollama/cloud consent rules and SQLite
conversations. It has no access to the manual dispatcher. The restricted Dashboard
only proxies authenticated requests to the existing loopback broker. That transport
does not need AUTO enabled or a campaign worker running. Scientific preflight and
execution use the existing root `.venv` through fixed worker entry points, because
the Dashboard venv intentionally lacks IBM dependencies. A stopped transport must
be restarted; this is distinct from starting AUTO.

Authorized accounts/instances/backend, fresh verified quota, unchanged frozen input
hashes, the same 21-angle vector, mapped observables and physical qubits, durable
reservation/UNKNOWN reconciliation, duplicate prevention and an exclusive execution
lock remain required. New Batch **and new standalone PUB** admission remains
`MIN_BATCH_QUOTA_SECONDS = 150.0`; genuine replacement admission remains
`MIN_RECOVERY_QUOTA_SECONDS = 40.0`. Unavailable runtime estimates are explicitly
diagnostic, not invented measurements. Failed preflight never dispatches a job.

The inspected local ledger still has 839/840 accepted PUBs (n=30: 239/240).
Replacement 18, ZNE logical PUB 27, job `dbo7ijtt7fqc73akm2e0`, is locally
SUBMITTED with no registered result archive. This does **not** contradict a remote
completion: the provider was not queried. Campaign Progress now lists existing
replacements awaiting local reconciliation. Inspect/retrieve/validate the existing
job before considering recovery. No results were invented or changed to make the
counter read 840.

### Restart commands

Run these in two separate PowerShell terminals from the project directory:

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware'
& '.\02_Hardware\04_code\MXene_Dashboard_Dev\start_auto_broker.ps1' -Restart
```

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware'
& '.\02_Hardware\04_code\MXene_Dashboard_Dev\start_dashboard.ps1' -Restart
```

Open `http://127.0.0.1:8766` and refresh the browser. The scripts restart only the
identified broker/Dashboard services; they do not start AUTO or terminate campaign
workers. Existing saved-account authorization and `final_config.json` still apply.
No new environment variables or dependency installation commands are needed.

### Changed files and offline validation

New Dashboard files: `manual_execution.py`, `static/manual_execution.js`,
`test_independent_manual.py`, `test_independent_manual_http.py`,
`test_independent_manual_ui.cjs`.

Modified Dashboard files: `app.py`, `auto_broker.py`, `broker_client.py`,
`campaign_progress.py`, `static/index.html`, `static/research_workspace.js`,
`static/research_workspace.css`, `static/manual_workspace.js`, `test_auto_broker.py`,
`test_worker_supervisor.py`, and this `README.md`. The two older HTTP test files now
use the explicit `/operator` route introduced by Hybrid Chat rather than sending
execution commands to the read-only chat endpoint.

Modified canonical modules, both under `03_ai_assistant/03_python_automation`:
`batch_scheduler.py` and `job_registry.py`. No fitting, frozen-input, recovery,
monitoring or scientific dataset file was edited.

Executed offline: 227 selected Dashboard tests; 16 new manual adapter/scientific
tests; 15 provider-quota tests; 23 combined-recovery tests; 18 reconciliation tests.
All passed. The manual scientific test ran the existing EIS calculation/export on
synthetic PUB values, checked frozen/model/data hashes, and rejected overwrite and
changed-input attempts. IBM/provider operations were mocked. UI checks passed:
40 new manual/contrast assertions, the existing Manual Workspace contract suite,
18 Hybrid Chat assertions and 14 assistant/interpretation assertions.
The 17 isolated Dashboard/cloud guard checks also passed. A local configuration
query through the broker's fixed scientific worker entry point succeeded without
contacting a provider or starting AUTO.

Run Dashboard tests from this directory:

```powershell
.\.venv_dashboard\Scripts\python.exe -B -m unittest test_hybrid_agent test_research test_workspace test_interpretations test_bridge test_data_sync test_execution_records test_auto_broker test_manual_steps test_campaign_progress test_canonical_steps test_single_pub test_worker_supervisor test_independent_manual_http -q
node test_independent_manual_ui.cjs
node test_manual_workspace.cjs
node test_hybrid_ui.cjs
node test_assistant_ui.cjs
```

From the project root, use the scientific interpreter for the manual adapter tests:

```powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
.\.venv\Scripts\python.exe -B -m unittest discover -s '02_Hardware/04_code/MXene_Dashboard_Dev' -p 'test_independent_manual.py' -v
```

Do not use blanket Dashboard unittest discovery: existing guard scripts install
permanent process-level restrictions and must run separately. The first blanket
run hit that event-loop isolation issue; subsequent selected suites passed. Initial
manual fixtures also hit Windows temporary-directory ACL restrictions and were
changed to ordinary isolated fixture directories. The UI tests use a DOM harness,
not a rendered-browser accessibility audit. Live IBM/cloud/Ollama calls were not
part of this implementation validation.

## Hybrid conversational chat upgrade — 2026-11-08

This section supersedes older AI Chat routing descriptions below. The change is
limited to chat and its provider/network boundary. No recovery is requested or
performed; n=30 Batch 006 is not rerun. Scientific calculations, experimental
files, frozen inputs, account configuration, canonical automation, broker
execution, and quota floors (150.0 s original / 40.0 s recovery) are unchanged.

### Audit and root cause

- Frontend: `static/chat.js` (stateless Quick Assistant) and
  `static/research_workspace.js` (SQLite-backed research sessions).
- Backend: `app.py`, `conversations.py`, `local_ai.py`, `research.py`.
- The browser and Pydantic input limit was 4,000 characters. The workspace
  retained only three turns, truncated each to 4,000 characters, without a summary.
- A broad secret heuristic also rejected legitimate identifiers longer than 31
  characters. It now checks recognizable credential patterns while preserving
  scientific IDs and SHA256 provenance. This is not a universal secret detector;
  users must still keep credentials out of chat.
- Broad first-match regular expressions in `research.route_command` selected one
  operation before consulting a model. Multi-question requests lost their other
  intents; English keywords constrained other languages. `select_question`
  selected one metric, not a multi-step plan.
- `local_ai.interpret` only let Ollama select existing evidence sentence indices;
  it could neither plan tools nor write a conversational explanation. The default
  was `qwen3:1.7b`, fixed loopback Ollama, a 300-token answer-selection budget,
  and no configurable model context window.
- Reused data boundaries: `Reader.status/catalog/artifact/table`, hash-checked
  exports, `research.execute/bridge`, `interpretations.interpretation`, and
  SQLite conversation storage. Scientific Python automation remains under
  `03_ai_assistant/03_python_automation`; its optional event-diagnostic AI adapter
  is separate from Dashboard chat and was not changed.
- IBM history, Batch/Job IDs, logical PUB validity, accepted replacements and
  frozen JSON metadata already exist in the local reader snapshot. The canonical
  worker/broker owns execution and reconciliation; chat must not become another
  execution engine.

### Architecture and behavior

`app.py -> conversational_agent.py -> ai_providers.py` handles conversational
requests. The provider returns a validated JSON decision: one allowlisted tool
call or an answer. Tool results drive the next decision; default maximum is eight
calls. The same validated call and arguments are executed only once per turn.
Only read tools appear in the model's registry:

| Tool | Existing source or implementation |
| --- | --- |
| `list_n_levels` | Recorded n-level accepted/invalid counts |
| `science` | Canonical accepted-PUB SSE/RMSE/parameter descriptive summaries via `research.execute` |
| `diagnose_nan` | Exported parameter/validation fields; no repair or new fitting |
| `execution_history` | Recorded jobs, Batch IDs, logical slots and accepted recovery provenance |
| `recorded_status` | Existing read-only campaign/job/worker/quota bridge |
| `catalog`, `read_artifact` | Catalogued, hash-checked CSV pages and small JSON artifacts; no supplied paths |
| `explain_figure` | Existing deterministic interpretation of a figure's underlying data |
| `documentation` | Dashboard README and three existing allowlisted project skill documents |
| `frozen_inputs` | Recorded frozen metadata; never submission validation |

There are no IBM, shell, Python-evaluation, SQL, arbitrary-file, account-editing,
submission, cancellation, monitoring, collection or recovery tools. Retrieved
documents are untrusted evidence, not instructions. Tool arguments reject unknown
fields, unsupported levels and unregistered names. Provider text cannot invoke
the operator endpoints. Existing operator buttons now use the isolated
`/api/conversations/{id}/operator` preparation route instead of chat messages;
the existing supervisor, confirmation and execution logic is unchanged.
Typing those execution commands into chat produces a read-only refusal.
Backward-compatible **read** commands remain available to older clients without
model consent. The upgraded browser explicitly selects conversational routing,
so keyword matches cannot silently consume a multi-question prompt.

The model is instructed to cite `[T1]`-style tool references. If it omits inline
citations, the server appends a list of the tools actually consulted; that list
does not assert claim-by-claim attribution. Unknown references and numerical
values not present in retrieved evidence are rejected. Independently calculated
Python findings, quantitative rows and bounded evidence excerpts are visible
under the response; complete tool evidence is saved only in Dashboard audit
files. This numeric/reference check is deliberately conservative and does not
prove that all narrative interpretations or causal claims are correct. Model
commentary is distinguished from verified Python findings. Missing data,
cached timestamps, dependence, incomplete samples and absent statistical tests
must remain explicit; a DONE job is not proof of valid PUBs.

Sessions still support create/select/rename/delete; Clear messages preserves the
session. SQLite stores complete successful turns, with a separate `memories`
table containing a bounded extractive summary of earlier questions and typed
research scopes. Recent user/assistant turns are included within configurable
limits. Older details may be omitted; original turns are retained for review.
Summaries do not promote previous model claims to new scientific evidence.
Client-supplied history and memory are ignored in persisted sessions.

Input supports 32,000 Unicode characters. A conservative UTF-8-byte upper bound
is used for model tokens: history can be shortened, but the current question is
never silently truncated. Too-large prompts produce an actionable error.
Tool results are bounded, with truncation disclosed. Defaults retain at most
100 sessions and 100 turns per session, matching the existing storage policy.
Quick Assistant remains explicitly stateless; use Research Workspace for memory.

### Setup and launch (PowerShell, repository root)

No new package dependency was added. Use the existing Dashboard environment:

```powershell
$dashboard = '.\02_Hardware\04_code\MXene_Dashboard_Dev'
& "$dashboard\.venv_dashboard\Scripts\python.exe" -m pip install -r "$dashboard\requirements.txt"
ollama list
$env:MXENE_OLLAMA_MODEL = 'qwen3:1.7b' # Or another installed JSON-capable model
$env:MXENE_AGENT_CONTEXT = '32768'
$env:MXENE_AGENT_OUTPUT_TOKENS = '1800'
& "$dashboard\start_dashboard.ps1"
```

Start the existing Ollama service separately if it is not running. The fixed
local endpoint remains `127.0.0.1:11434`; no change to Ollama installation is
required. Open `http://127.0.0.1:8766`, then AI Research Workspace → AI Chat.
Expand **AI provider, model and language**, enable local context permission,
and use **Check connection and models**. Select an installed model and a context
size that its implementation and available memory can support. `num_ctx` and
`num_predict` are sent to Ollama. The inherited small default model may be less
reliable at structured multi-step decisions than larger installed models.

For an already-running Dashboard, stop/restart only its verified Dashboard
process using `start_dashboard.ps1 -Restart`, then refresh the browser. The broker
does not need a restart for this chat upgrade. These instructions do not launch,
resume or recover any IBM work.

### Optional cloud configuration and consent

The adapter supports HTTPS **Chat Completions-compatible** APIs with JSON-object
responses (`/chat/completions`, `model`, `messages`, `max_tokens`). It does not
implement every vendor's native API. Configure an endpoint and compatible model
names supplied by your provider; there is no built-in cloud destination.

```powershell
$env:MXENE_CLOUD_BASE_URL = 'https://YOUR-PROVIDER-HOST/v1'
$env:MXENE_CLOUD_MODELS = 'YOUR-MODEL-NAME,ANOTHER-SUPPORTED-MODEL'
# Read the key without printing it or putting its literal value in shell history:
$env:MXENE_CLOUD_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Cloud API key' -AsSecureString)).Password
& '.\02_Hardware\04_code\MXene_Dashboard_Dev\start_dashboard.ps1'
```

Keys are accepted only in the server environment, never a URL, frontend field,
response, request diagnostic or stored chat preference. Do not put keys in chat
or commit them. Cloud endpoints must use HTTPS port 443, without URL credentials,
queries or fragments, and resolve exclusively to public addresses. The audit
guard permits only those resolved addresses during that adapter call; all
other outbound restrictions, including IBM restrictions, remain intact.

Select Cloud and explicitly check permission to share **prompts, conversation
history and retrieved project evidence**. Selecting a provider alone does not
authorize transmission. Cloud connection checks also require that permission
and send only a generic JSON probe. Automatic fallback requires a second explicit
checkbox as well as cloud consent. Both cloud permissions reset when the page
reloads. History stays in the same local SQLite session when providers/languages
change. Each reply identifies the provider/model actually used, including
authorized fallback. Authentication errors, rate limits, malformed responses and
timeouts use safe errors without raw provider response bodies; there are no
automatic cloud retries.

| Environment variable | Default / purpose |
| --- | --- |
| `MXENE_OLLAMA_MODEL` | `qwen3:1.7b`; installed local model |
| `MXENE_AGENT_CONTEXT` | `32768`; 8192–131072 context-token budget, overridable in chat |
| `MXENE_AGENT_OUTPUT_TOKENS` | `1800`; 256–4096 response-token limit, overridable in chat |
| `MXENE_AGENT_HISTORY_CHARS` | `10000`; 1000–24000 history-character budget |
| `MXENE_AGENT_RECENT_TURNS` | `6`; 1–20 recent turns before extractive summarization |
| `MXENE_AGENT_MAX_TOOLS` | `8`; 1–12 read calls per turn |
| `MXENE_CLOUD_BASE_URL` | Unset; configured compatible HTTPS API base |
| `MXENE_CLOUD_MODELS` | Unset; comma-separated server-side model allowlist |
| `MXENE_CLOUD_API_KEY` | Unset; secret server-side key |

Local calls retain the existing 90-second timeout (3 seconds for model discovery).
Cloud requests have a 60-second socket timeout. The agent also checks a
240-second elapsed budget between calls; an in-flight call can extend beyond
that boundary. Context and output limits must fit the selected cloud model's
own limits; incompatible provider contracts are reported rather than retried.

Language options are Auto / English / Tiếng Việt / 繁體中文 / 简体中文. Auto asks the
model to detect the latest message's language and Chinese script; an explicit
selection overrides it. Science identifiers remain unchanged. All persistence
and JSON use Unicode. The existing safe Markdown subset supports headings,
emphasis and code. Math adds common symbols, fractions, square roots and squared/
cubed powers; unsupported TeX remains visible as text (no full TeX dependency).
Responses have copy buttons and read-only tool status/evidence details. Errors
preserve previously saved turns and leave the current browser draft available
to retry; in-flight generations are not automatically replayed after a restart.

### Changed files

New: `ai_providers.py`, `agent_tools.py`, `conversational_agent.py`,
`test_hybrid_agent.py`, `test_hybrid_ui.cjs`, `test_cloud_guard.py`.

Modified: `app.py`, `conversations.py`, `security.py`, `static/chat.js`,
`static/research_workspace.js`, `static/research_workspace.css`, `README.md`.

`local_ai.py`, `research.py`, `reader.py`, `interpretations.py`, the broker,
supervisor and scientific/automation modules are reused without source changes.
Runtime conversation/snapshot/audit files remain under Dashboard `state/`.

### Verification

Run from the Dashboard directory with its existing virtual environment:

```powershell
.\.venv_dashboard\Scripts\python.exe -B -m unittest test_hybrid_agent test_research test_workspace test_interpretations test_bridge test_data_sync test_execution_records -q
.\.venv_dashboard\Scripts\python.exe -B test_guard.py
.\.venv_dashboard\Scripts\python.exe -B test_cloud_guard.py
node test_hybrid_ui.cjs
node test_assistant_ui.cjs
node --check static/research_workspace.js
```

Executed: 109 Python tests passed; 18 new and 14 existing DOM contract checks
passed; 11 existing isolation checks and 6 cloud destination checks passed.
The guard scripts must run in separate processes: importing the existing
`test_guard` in the same unittest process installs a permanent audit hook that
blocks the HTTP harness's event-loop sockets. An initial combined run exposed
that isolation issue; the commands above passed separately.

Live Ollama checks found the installed `qwen3:1.7b`. A minimal JSON probe
completed in 10.1 seconds. After validating identifier handling and listing
arguments, an end-to-end agent read completed in 24.4 seconds using a 16,384-token
context and 768 response tokens, retrieving the recorded n=20 counts. It answered
only that selected level, so this is a successful transport/tool integration
check, not proof of comprehensive answer quality. An initial 32,768-context
agent request timed out without cloud fallback. For this installation, start
with the smaller verified settings and increase them deliberately for long
prompts. Real-model multilingual and complex multi-step quality still needs
evaluation with the chosen model; the automated language cases use mocks.

Coverage includes short/long English, long Vietnamese, both Chinese scripts,
mixed language, multi-question/multi-step reads, history and provider switching,
restart restoration, missing exports, NaN diagnostics, recovered PUB records,
consent, malformed outputs, context overflow, provider outages/rate limits,
duplicate tool reuse, invalid paths and rejected write/submission tools. AI
provider language/intent contracts use mocks; those tests alone do not establish
real-model multilingual quality. Cloud transport is mocked, no real cloud call
or IBM submission is made by these tests. UI checks are DOM harnesses, not a
native-browser visual review. Existing FastAPI/Starlette emits a nonfatal httpx
deprecation notice; dependencies were not changed merely to silence it.

A real read-only data smoke check also retrieved canonical n=20 DD/TREX/ZNE SSE
summaries (20 accepted observations per treatment). At the time of that check,
the Dashboard source view reported n=30 as 239/240 accepted and returned n=30
canonical analysis as unavailable. This differs from the operator's completed
Batch 006 recovery note; no records were reconciled, modified or recovered by
this chat task. The agent reports what its existing source view can substantiate.

---

> Historical COLLECT capability: explicit `Collect completed jobs for n=30 Batch 005` is supported through the canonical STEP, subject to confirmation and strict original-first-attempt identity gates. Unscoped and replacement/mixed-attempt collection remain blocked. See **Scoped COLLECT-only STEP** below; earlier audit notes describe historical limitations. These operations now belong to the separate operator controls, not AI Chat.

The same scoped operation supports **Collect completed jobs for n=30 Batch 006**,
**Collect results for n=30 Batch 6**, and **Please collect completed jobs from Batch
006 for n=30**. Command discovery includes the explicit Batch 006 command.
Before confirmation, the secure broker performs only shared canonical local
submission/ledger/archive checks and displays the eight original Job IDs. The
confirmation pins that scope; the canonical STEP rechecks it under WorkerLock
before provider reconciliation, retrieval, validation or archive writes. Malformed
Collect requests and canonical blockers never fall back to research chat.
`Show step result` displays and retains the actual receipt, operation status and
collection counts across chat reloads. Restart both long-running services after
updating these modules; editing files alone does not reload their imported router.

# Phase 5B follow-up: grouped commands and read-only monitoring

## Canonical STEP entrypoints (latest)

This section supersedes the earlier Prepare/Analyze/Export blocked classifications
below. The sole manager implementation now exposes
`03_ai_assistant/03_python_automation/research_pipeline.py:execute_campaign_step`.
It acquires the existing campaign WorkerLock itself, opens the existing registry
read-only, verifies campaign/target/frozen-input identities and current canonical
authorization, and rejects UNKNOWN submissions. It never calls an AUTO cycle,
changes the campaign checkpoint, selects an account or submits a job.

| Workspace command | Exactly one delegated operation |
| --- | --- |
| Prepare / Prepare campaign | Verify all six existing frozen target preparations and registrations; return ALREADY_PREPARED. Never reset or create a new campaign. |
| Run preflight | Existing one-shot canonical preflight. |
| Monitor Batch 005 | Existing confirmed read-only diagnostic, fixed registered job scope. |
| Analyze n-level n=5 | Call canonical `adaptive_targets.analyze_target` once for this complete n-level; includes its existing numerical/plot artifact generation, but no publication export. |
| Export n=5 figures | Call canonical `scientific_exports.export_n_level_results` once using a verified STEP analysis receipt; never call analysis. |
| Review Recovery / Review recovery for n=30 | Existing read-only slot review. |

Use any of the six supported n-levels explicitly for canonical analysis/export.
`Export n=25 figures`, `Export figure n=25`, and `Export figures for n=25`
all request the same canonical Export STEP (case and whitespace insensitive).
`Show step result` reads the latest operation receipt and reports RUNNING,
SUCCEEDED, FAILED or BLOCKED, including failure reasons and the verified Analyze
receipt path/hash when available. An unverified newer launch never displays an
older operation's success. Active operations and unresolved launches block new
Analyze/Export dispatches; Export never runs Analyze automatically.
Successful Export results identify the actual `figure_directory`, `data_directory`,
`export_manifest`, and ten PNG filenames/count, including a versioned destination
when the canonical exporter selects one. `output` is the publication figure
directory for Export; `analysis_output` separately identifies the source analysis.
The Figures view uses matching export manifests and hashes under the established
mitigation `output/01_results_and_figures/n=25` tree. Select n=25 and allow the
120-second local snapshot/figure refresh; do not look under a new repository-root
output directory or rerun Analyze just to discover exported files.
Both require separate confirmation of canonical output writes. Existing analysis
and publication paths retain their canonical create-only/versioned behavior;
regeneration/overwrite is not offered. Exactly n accepted logical PUBs for each
treatment, their provenance and immutable inputs are checked before science.
Incomplete n=30 is rejected without submission or campaign advancement.
`Analyze n=5` retains its earlier read-only research-query behavior for compatibility;
use **Analyze n-level n=5** to request the canonical operation deliberately.

The new immutable handoff receipt under the canonical campaign `steps/` directory
binds the target manifest, selected original/replacement records and streaming
artifact hashes. Export refuses a missing/mismatched receipt or changed files.
Repeat analysis reuses verified existing canonical output; repeat export uses
canonical existing-output verification. Interrupted receipt writes cannot be
reported successful. Unexpected crashes remain fail-closed in the broker, require
operator reconciliation, and never cause an automatic replay. Known incomplete
data/receipt checks return STEP_FAILED with a reason and no automatic progression.

**Collect remains BLOCKED.** The collector combines result retrieval, immutable
archive/sidecar creation and ledger validation, and resolves submission-side files
by treatment. An independent adapter still needs a verified job/attempt contract
against the registry, provider and submission archive, including grouped and
replacement attempts. Generic Monitor/Collect does not guess that scope. Standalone
Validate and recovery submission are also not exposed. None of these commands
uses `once=True` or re-submits Batch 005.

Canonical tests run with the project's `.venv` (which has Pillow/scientific
dependencies), not the lightweight Dashboard environment. Tests use isolated
registries/output trees and mocked science/provider boundaries. Numerical engines
and original figures were not regenerated during development. No production STEP
was executed. A SQLite online-backup snapshot and code rollback copies are retained
in Dashboard `state/canonical-step-baseline-49b345552b/`; no migration was needed.

Final verification for these entrypoints: 12 canonical STEP tests, 78 focused
Dashboard/broker tests, 11 isolation checks and 14 served HTTP commands passed.
HTTP analysis/export requests stopped at confirmation; no production execution
was approved. Canonical tests use mocked scientific delegates and do not claim
new numerical/600-DPI rendering validation. The only changed file in the prior
protected-source baseline is the explicitly authorized `research_pipeline.py`;
production SQLite and scientific data/artifacts remain unchanged.

Load updated code by restarting only the Dashboard and its separate broker using
the commands below. Each subsequent execution still needs its own explicit
confirmation; starting either service does not start Auto Manager.

Interrupted-session recovery was verified in place. The existing 14-file Phase 5B
implementation is present; no reset, migration or second engine was needed. This
directory has no Git repository, so uncommitted status is unavailable. The only
code repairs are clearer command-chip separators and fail-closed handling of
missing/corrupt/inaccessible completed STEP reports. Tests cover preservation of
ownership observation, blocking further dispatch and leaving receipts intact.
See AUTO_MANAGER_CONTROL_PLAN.md for the per-command recovery classification.
Current verification: 41 distinct focused tests across targeted runs, nine syntax
checks, 11 isolation checks, actual HTTP routing and served UI/HTTP DOM tests pass.
No hardware or native-browser visual verification is claimed; blocked STEP
operations and previous legacy guide-test failures remain explicitly documented.

The Research Workspace now always exposes a collapsible **Python Auto Manager
Commands** panel above its input, outside the scrolling conversation. Three
independently collapsible groups contain exactly these prompts:

- **Status & Monitoring:** Campaign Status, Worker Status, Batch Status, IBM Job
  Status, IBM Quota, Recovery Status.
- **AUTO Control:** Run Auto Manager, Pause Auto Manager, Resume Auto Manager.
- **STEP-by-STEP:** Prepare, Preflight, Monitor / Collect, Review Recovery, Analyze,
  Export Figures.

Chips insert editable text; they never send or execute it. The fixed n=5 Batch
prompt is removed. Quick Assistant has no operational suggestions. Each chip
reports READ_ONLY, AVAILABLE, REQUIRES_AUTHORIZATION or BLOCKED and exposes its
reason. The same supervisor gates and adapter blockers determine discovery and
execution. A running broker must advertise a STEP before it is offered as enabled;
an older broker requires a service restart, not a campaign start.

`Show supported commands` and `Show supported STEP commands` are explicitly
routed before scientific analysis and Ollama. The old bugs were a missing help
alias and `workspaceSuggestions(c.turns.length===0)`, which hid controls after the
first message. The panel is now independent of conversation length and refresh.

Status commands use existing local snapshots/manager read functions, not IBM
network calls. They include local capture timestamps, recorded campaign state,
and provider observation timestamps/freshness where available. Missing provider
Batch state/TTL is UNAVAILABLE; a job observation does not establish Batch state.
Worker status uses actual broker process/lock verification, not a stored RUNNING
flag. Quota status uses saved evidence and existing reservation calculations;
it does not choose an account or refresh IBM quota. `Review recovery for n=30`
filters existing slot summaries and never creates plans or replacements.

## Supported one-operation STEP execution

- `Run preflight` / `Preflight`: existing canonical `preflight_all`, once.
- `Monitor Batch 005`: new narrowly scoped adapter to the **existing**
  `manager_diagnostics/ibm_batch_005_readonly.py:main`. It verifies eight registered
  n=30 Batch 005 identities, uses canonical labK saved-profile/instance authorization,
  and delegates status/TTL reads to that diagnostic. It never calls `result()`,
  submits, cancels, collects or changes quota reservations. Partial/network failures
  are STEP_FAILED with UNKNOWN/unavailable observations, not invented job failures.

Both require explicit confirmation, a stopped verified worker, the canonical
exclusive locks, a one-use broker grant, and matching code/config/checkpoint
fingerprints. They save results only in Dashboard state, exit, and do not advance
the campaign. `Show step result` reads completion. A running diagnostic cannot be
mistaken for AUTO, paused with the campaign pause API or run concurrently.
This supersedes the older blanket BLOCKED description for **Monitor Batch 005**
below; generic `Monitor jobs` remains blocked because canonical `monitor_batch`
also retrieves, archives and writes registry/usage records.

`Monitor / Collect` deliberately requests clarification: choose one operation.
Preparation remains blocked pending a verified idempotent existing-campaign
adapter. Collection still needs exact account/block/attempt scope and archive/write
authorization. Canonical analysis also creates plots/files and lacks an audited
analysis-only contract. `export_n_level_results` exists, but standalone export
requires a verified complete analysis manifest, target/provenance and publication
write authorization. These operations are not simulated. `Analyze n=30` remains
the existing read-only scientific query, not canonical regeneration.

Verification: **120 focused tests passed**, plus **10 existing offline diagnostic
tests**. Actual localhost HTTP checks cover 12 status/help/blocked commands with
zero Ollama calls, persisted history and grouped capabilities. Served JS/HTTP DOM
tests cover chip editing without sending, old conversations and refresh; CSS keeps
the panel outside the message scroller. Native browser verification was blocked
because Computer Use could not safely determine the browser URL. No claim of
visual/mobile rendering verification is made. The three legacy guide-test failures
documented below remain outside this focused run.

No real diagnostic/provider query or production STEP was executed. LIVE readiness
still requires operator-environment connectivity, permissions, timeouts and safety
review; mocked tests alone do not certify hardware execution. Restart only the
Dashboard with `start_dashboard.ps1 -Restart`; load the updated broker separately
with `start_auto_broker.ps1 -Restart`. Neither launcher starts Auto Manager.

# Earlier Phase 5B: one-shot canonical preflight and safe STEP boundaries

This section supersedes older STEP-disabled notes below. No production operation
was executed to test Phase 5B; hardware readiness is not certified.

| Workspace command | Implementation / safety boundary |
| --- | --- |
| Run preflight | Supported after explicit confirmation: exactly one `read_only_preflight.preflight_all` call, saved-profile authentication, canonical authorized accounts and instance allowlist. No AUTO loop, reservation or campaign update. |
| Show step result | Read the last persisted preflight result and current verified worker observation; refresh does not replay it. PASS/WARN/FAIL remain diagnostic, not LIVE authorization. |
| Analyze n=5 | Existing read-only scientific bridge; calculates from validated existing data. Does not rerun canonical publication generation. |
| Review targeted recovery | Existing read-only local slot review; no recovery plans or replacements. |
| Prepare campaign | BLOCKED: canonical preparation writes registry/manifests; safe idempotent preparation of this existing campaign is not verified. |
| Monitor Batch 005 / Monitor jobs | BLOCKED: canonical monitor also retrieves DONE results, archives and updates registry/usage records. Not a status-only step. Use existing job observations for local status. |
| Collect completed jobs | Requires explicit n-level and Batch scope plus confirmation. Original first-attempt Batches only; see Scoped COLLECT-only STEP. |
| Run targeted recovery / Run Batch n=5 | BLOCKED: replacement/submission authorization is absent; no safe single-batch delegate. `once=True` is a campaign cycle, not one batch. |
| Analyze n-level n=5 / Export figures | BLOCKED: canonical `process_pipeline` combines analysis/export and writes canonical outputs; no isolated export-only authorization. |

The preflight child uses the existing broker's fixed command and both canonical
WorkerLocks. It runs only with stopped, verified ownership, rechecks the policy
inside the locks, saves a sanitized result in Dashboard `state/step-<UUID>.json`,
and exits. It never calls `run_pipeline`. Account labels/check outcomes and finite
provider quota values are retained; credential/instance details and provider error
text are not copied into chat. A failed diagnostic check is not a failed dispatch.
The existing one-use authorization, 120-second expiry, interrupted-dispatch
reconciliation and crash fail-closed behavior apply. Active preflight cannot be
mistaken for AUTO or paused through the campaign pause API.

Preflight performs real provider reads only when the operator confirms it later.
Its canonical locks may create/update lock metadata during that authorized run;
the web server itself remains sandboxed. No production locks were acquired in
development tests. Quick Assistant rejects operational STEP commands. Provider
outages return canonical FAIL/WARN results where supported, never synthetic
success. There is no automatic retry or advancement.

Restart the separately installed broker with `.\start_auto_broker.ps1 -Restart`
in its own terminal to load this adapter, then use `.\start_dashboard.ps1 -Restart`
in another terminal (full directory below). Neither command starts a campaign.

Phase 5B focused tests include HTTP request/confirmation/result persistence,
zero-LLM routing, real isolated canonical locks, replay refusal, exclusive worker
checks and canonical preflight scope/redaction. Production preflight and IBM
access were not exercised. Native browser rendering has not been tested for this
change; the existing workspace UI is reused without frontend execution changes.

Final focused run: **111 tests passed** (`test_phase5b`, `test_auto_broker`,
`test_auto_process`, `test_worker_supervisor`, `test_phase4_control`,
`test_workspace`, `test_bridge`, `test_research`, `test_interpretations`).
`test_guard.py`: **11 isolation checks passed**. A Windows receipt-sharing race
found during the native-process test now returns UNKNOWN rather than raising or
claiming stopped ownership; its regression test passes.
Three pre-existing `test_guide.py` expectations still fail: nine visible tabs,
startup-command entries, and a PLANNED label no longer present in research mode.
They were reproduced with the pre-edit guide content and were not suppressed or
rewritten to claim a clean full suite. Registry/WAL/SHM/lock fingerprints remained
unchanged across verification; all 228 previously protected source/output hashes
also matched. These checks do not certify hardware execution.

# Phase 5A AUTO control (historical)

AUTO broker integration is implemented and tested offline. STEP support is now
limited to the Phase 5B table above. The existing Python Auto Manager is still the sole campaign orchestrator;
AI Agent, Skills, Ollama and scientific calculations are unchanged.

In AI Research Workspace: **Start Auto Manager**, **Resume Auto Manager**,
**Pause Auto Manager safely**, **Show Auto Manager status** (or **Monitor Auto
Manager**). Start/Resume require a separate explicit campaign-scoped confirmation.
Quick Assistant remains read-only. Browser refresh never starts a worker.

The broker uses the fixed canonical interpreter and a fixed wrapper command. The
wrapper calls research_pipeline.run_pipeline with once=False and canonical LIVE
flags; no account/job/PUB selection or recovery loop is implemented here. Ownership
requires both canonical locks, a lock-held receipt, exact executable/arguments,
PID creation time and campaign identity. Unknown or crashed owners fail closed;
legacy workers without receipts cannot be adopted. Pause is cooperative, never kill.

Start the explicitly opted-in loopback broker in a separate PowerShell terminal:

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
.\start_auto_broker.ps1
```

This starts only the broker service, not a campaign. The broker's random credential
is Windows DPAPI-protected and never reaches the browser. The Dashboard sandbox
permits only this scoped loopback channel; production writes, subprocesses and IBM
imports remain prohibited in the web process. Broker permissions are those of the
local operator; same-user malicious processes are outside this trust boundary.

Restart only Dashboard in another terminal:

```powershell
.\start_dashboard.ps1 -Restart
```

Browser: http://127.0.0.1:8766. Broker restart, when needed, is separately
`.\start_auto_broker.ps1 -Restart`; it verifies the --serve listener and never
terminates a --worker process. Stopping the broker does not pause a campaign.

Verification: 80 focused offline Python tests pass, including real canonical lock
fixtures, wrapper receipts, fixed-command dispatch, authorization, expiry, replay,
pause/resume, UNKNOWN, blocked-quota propagation, crash and DPAPI storage. Eleven
security checks, 14 assistant DOM and 70 guide DOM checks pass. The actual HTTP
path reported STOPPED_VERIFIED and prepared a LIVE confirmation without confirming
it. No production launch, worker or dispatch record was created. A real isolated child process using the canonical project interpreter verified
lock ownership, attach, refresh, cooperative pause and clean exit. Its registry
and IBM-facing campaign cycle were mocked; these checks are not hardware campaign certification. See
AUTO_MANAGER_CONTROL_PLAN.md for boundaries and remaining LIVE requirements.

The following Phase 4/3 notes are historical and superseded for AUTO control.

# Phase 4 control integration ? partial, production execution blocked

Workspace now recognizes Run Auto Manager, Resume campaign, Pause Auto Manager,
Show Auto Manager status, Prepare Campaign, Run Batch n=5, Run preflight,
Monitor Jobs, Collect Results, Review targeted recovery, Analyze Results and
Export Figures. Routine commands never need Ollama. **This is not LIVE-ready.**

New-conversation suggestions fill the editable input and never send automatically.
Unknown worker ownership or stale snapshots show only read-only suggestions.
Review targeted recovery now returns actual local recovery-summary rows, preserving
pending/UNKNOWN distinctions. Existing read-only Analyze n=5, quota and progress
commands remain available. Quick Assistant rejects operational commands.

The real canonical OS lock and outer AUTO loop are tested on isolated fixtures.
AUTO dispatch maps only to research_pipeline.run_pipeline; no scheduler, recovery
loop or job/account selection is added. That dispatch is not activated in the
production web adapter: a verified lock-owner receipt and restricted execution
broker remain missing. Unsupported STEP commands state their exact blocker rather
than substituting an entire campaign cycle. Pending UNKNOWN/DISPATCHING actions
now block new requests after restart as well as replay of old confirmations.

See AUTO_MANAGER_CONTROL_PLAN.md for per-command limitations and test scope.
The existing process sandbox and production files remain unchanged.

Phase 4 verification: 60 focused Python tests passed; actual HTTP checks passed
for ten blocked operational commands, read-only recovery review, prompt filtering,
refresh and campaign progress with zero Ollama calls. Served frontend DOM/HTTP
checks verified that prompt clicks populate the input without sending and that
suggestions disappear after a conversation turn. Fourteen assistant DOM checks,
70 guide DOM checks and seven isolation checks passed. Native-browser visual
testing was not performed. All 228 protected fingerprints checked were unchanged.
The initial fixture run encountered Windows ACL errors in tempfile-created
directories; fixtures now use ordinary inherited-ACL directories and pass. Seven
old canonical-control-test-* fixture directories remain in state because sandbox
approval policy rejected cleanup. These contain no production campaign data.

## Three research interfaces

- **Quick Assistant:** floating, one-off question/answer, no conversation history sent or saved. Closing/reopening clears its input and result; late responses cannot repopulate a closed widget. Scientific audit evidence remains internal.
- **AI Research Workspace:** full-page sidebar, New Chat, titles, open/rename/delete (with confirmation), independent server-owned context and refresh persistence. Both interfaces share `run_assistant`, the deterministic router, Python bridge and scoped Ollama adapter. No second execution engine or manager exists.
- **Scientific interpretations:** approximately three quantitative sentences by default, followed by independently collapsible detailed analysis. Baseline/best values and valid percentage reductions use existing numbers. Pointwise complex/component RMS remains distinct from canonical per-PUB SSE/RMSE; rankings remain descriptive.

### Storage and boundaries

`conversations.py` owns only `state/conversations.sqlite3`. Conversations and successful question/answer turns are transactionally stored there, never in the production registry. History comes from the selected server-side conversation, not client-supplied history. A bounded recent history plus retained validated scientific scope supports follow-ups across refreshes. Maximum 100 conversations and 100 turns per conversation; no automatic deletion. Per-chat locks prevent concurrent mutations. Scientific audit IDs are retained internally, but ordinary rendering displays only answer text, quantitative tables and charts.

Conversation mutations require same-origin requests and the existing Dashboard request header. Known credential formats and credential assignments are rejected; do not paste arbitrary secrets (no detector can recognize all possible secret strings). No credential discovery or storage is added. Conversation deletion does not remove scientific audits or original data. Quick requests ignore submitted history and never insert conversation rows.

Derived charts render lazily on expansion to avoid loading many Plotly figures at once. Safe text rendering supports headings, bold, inline/fenced code and Unicode scientific notation without HTML execution. LaTeX is displayed literally; a math typesetter was not added. Workspace chat state is separate from the shared session-scoped local-AI permission setting.

### Validation and limitations

Final checks: 50 focused Python tests, 14 assistant/interpretation DOM checks, 70 guide/frontend DOM checks and seven isolation checks passed. Served-JavaScript tests over real HTTP passed for both interfaces; 228 protected fingerprints remained unchanged.

Focused Python tests cover CRUD, persistence, cross-conversation isolation, multi-turn scope retention, stateless Quick requests, secret rejection, concurrent-turn rejection, protected storage paths and exact scientific summaries. Served frontend DOM harness tests exercise the actual HTTP workspace CRUD/follow-up/refresh routes and Quick Python commands; these are not native-browser visual tests. Native-browser responsive rendering remains unverified in this Windows environment.

Actual HTTP checks returned 760/840 campaign progress through Quick Assistant with zero Ollama calls, n=5 DD/TREX and independent n=10 Rct workspace analysis, retained n=5 RMSE follow-up, and successful local qwen3:1.7b reasoning. Test conversations were deleted via their own IDs. Original scientific files and production records are untouched.

The restart script now also recognizes verified Dashboard owners with retained loopback sockets when Windows does not label their socket LISTENING; command-line validation still prevents stopping other Python processes.

Restart only the Dashboard:

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
.\start_dashboard.ps1 -Restart
```

Open http://127.0.0.1:8766. Earlier sections below are historical implementation notes.

## Chat routing deployment fix ? verified against port 8766

Root cause: the listener was an old, non-reloading Dashboard process. It served current static JavaScript from disk, but retained the old consent-first Python route and mandatory client model-name validation. The minimal client no longer supplied a model; the old route therefore asked for consent and then rejected an empty model. The served JavaScript matched disk and carried `Cache-Control: no-store`; this was not a stale-JavaScript-cache finding.

Fixes: restart only the command-line-verified `serve.py` listener; protocol header `X-MXene-Chat-Protocol: 2` lets the frontend refuse a stale backend without requesting consent. Per-request `X-MXene-Ollama-Calls` measures adapter calls across the HTTP request. Deterministic routing still precedes model discovery/validation and consent. `qwen3:1.7b` is the explicit default, verified installed; optional `MXENE_OLLAMA_MODEL` or explicit model requests must match installed model IDs exactly. No unrelated-model fallback.

Local reasoning permission is an inline session setting, stored only as a granted flag in sessionStorage. No browser confirm() is used. Deterministic requests require no AI permission; refresh preserves an existing local permission. Reasoning retains the existing no-cloud boundary and security guard.

Actual HTTP acceptance: both `Show campaign progress.` and `Analyze n=5 and compare DD with TREX.` returned 200 with zero Ollama calls, even with an intentionally nonexistent requested model. Progress was 760/840; scientific comparison returned actual DD/TREX rows. All other documented deterministic commands also returned 200 / zero calls. `Explain why DD+ZNE may be unstable.` returned 200 using qwen3:1.7b (one discovery and one generation call).

`test_served_chat.cjs` loads the served JavaScript and drives it through real localhost HTTP with a DOM harness; both commands pass before/after reload and a stale-protocol fixture produces a restart error without permission prompts. This is NOT a real browser rendering test. Chrome launch failed with Windows Mojo/Crashpad Access denied.

`test_http_outage.py` tests the same application over an isolated real TCP/HTTP listener with Ollama connection refusal injected: deterministic commands return 200; reasoning returns 503. It does not stop the shared Ollama service. Focused validation: 37 Python checks, 24 chat DOM checks, seven isolation checks; 228 protected file hashes unchanged. Full-suite and visual browser success are not claimed.

Restart only the Dashboard (verified on this machine):

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
.\start_dashboard.ps1 -Restart
```

The restart option resolves the loopback listener and verifies its exact `serve.py` command line before stopping it. It never kills all Python processes or touches Auto Manager. It uses the already installed base-environment psutil and fails closed if process verification is unavailable. The launcher records the actual server PID. Open http://127.0.0.1:8766 and refresh the page. The corrected server was already restarted and tested during this fix.

## Audited Python Auto Manager bridge

The existing chat endpoint now routes simple commands to typed Python tools before considering Ollama. A separate `POST /api/research/tool` accepts exactly eight allowlisted operations: `get_campaign_status`, `get_worker_status`, `get_ibm_job_status`, `check_ibm_quota`, `get_validated_results`, `analyze_existing_results`, `compare_mitigation_treatments`, `export_derived_figures`. No commands, module paths, SQL, provider instances or filesystem targets are accepted. Same-origin/header checks apply; local AI consent is required only if the request actually needs Ollama. No new model server or worker exists.

### Actual integration audit

| Component | Existing implementation and bridge use |
|---|---|
| Frontend/backend | `static/chat.js` -> `app.py` -> typed bridge in existing `research.py` |
| Manager status | `JobRegistry.independent_campaign_status(..., event_limit=5)` on dashboard snapshot, `readonly=True` |
| Accepted results | Existing `Reader.project` calls `JobRegistry.repaired_pub_values`; research joins accepted logical slots and publication validation records |
| Job monitoring | Existing `jobs_view.project_jobs` consumes persisted observations. `batch_monitor.monitor_batch` is NOT invoked: it performs provider monitoring and persistence |
| Quota | Reuses `quota.report_quota_fields`, `provider_reservations`, `available_quota_seconds` and centralized `final_configuration.quota_eligibility`; no account-selection or provider service factory called |
| Manager lifecycle | `01_ai_agent/mxene.py` delegates to `run_campaign_auto_manager` / `run_independent_campaign`; untouched and not launched |
| Collect/recovery | Existing `mitigation_pilot.collect` and `targeted_recovery.collect_replacement`; NOT exposed |
| Science/export | Existing `mitigation_pilot.analyze` and `scientific_exports.export_n_level_results` produce canonical tables. Bridge reuses those exports and existing dashboard deterministic summaries; it does not rerun canonical fitting/export against production |
| Derived export | Existing Plotly chart data path and download toolbar; evidence/chart specification saved separately. Browser PNG export is not visually tested |
| Worker safety | Existing `worker_runtime.campaign_worker_lock` and WorkerLock remain authoritative. Bridge never acquires them; worker liveness is UNKNOWN without verified process ownership |
| Skills | Existing `01_ai_agent/offline_tools.load_skill('workflow-manager')` reused; exact skill hash retained internally. Only three existing skills exist: workflow-manager, campaign-comparison and dependence-aware-statistics. Comparison/statistics execution remains blocked by existing source-contract requirements |
| Ollama | Existing scoped loopback adapter; model discovery verified `qwen3:1.7b`. Simple commands do not invoke it; no new inference service |

Security permits read access to precisely the three existing SKILL.md files in addition to already allowed production Python modules. This is not a global guard bypass. Scientific/statistical execution tools are not registered by interpreting skill text.

### Verified behavior and limitations

End-to-end in-process HTTP tests, using actual manager modules, returned campaign PAUSED with **760/840 accepted PUBs**, persisted pending-job observations, n=5 scientific metrics and DD/TREX comparisons. The guard-enabled bridge also returned 760/840. All saved quota evidence tested was stale, so fresh quota is explicitly unavailable; there is no IBM network refresh. Worker liveness is not inferred from campaign state or a stale lock file.

Migration issue found: archive provenance uses paths relative to the workspace root. Running from the dashboard directory omitted 16 replacement PUBs in a diagnostic check. The existing `serve.py` already changes to the correct workspace root; the bridge now rejects an incorrect working directory. No production code or records were changed.

Focused checks: 26 Python bridge/research tests, 20 frontend chat DOM checks, and seven isolation checks passed. No live IBM job action or Ollama inference was executed. A real loopback model discovery succeeded. Browser visual behavior and PNG download were not certified. The running dashboard needs a dashboard-only restart to load backend changes.

Future Start/Pause/Resume and step control are design-only in `AUTO_MANAGER_CONTROL_PLAN.md`. The existing pause marker function was identified but never called.

Startup:

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
.\start_dashboard.ps1
```

Open http://127.0.0.1:8766. Stop only the foreground dashboard with Ctrl+C before restarting it; do not stop production workers. Earlier sections below describe historical implementation stages.

## Conversational research mode

Research navigation now prioritizes Overview, Campaign, AI Agent, Results, Statistics and User Guide. Campaign Lifecycle, Execution Flow, execution-plan, job/recovery/log navigation, source panels, dataset versions and hashes are hidden. Backend operational readers and immutable records remain intact. Original PNG downloads remain available.

`research.py` validates bounded typed requests, resolves natural-language n-level/metric/treatment scope, and reuses canonical per-PUB exports. Logical acceptance is joined through publication validation mappings and the read-only accepted-slot snapshot. Complete exports are not automatically treated as complete accepted datasets. Results can therefore be preliminary subsets. Available research operations provide descriptive treatment/n-level comparisons, medians/means/sample standard deviations, baseline differences, rankings, parameter shifts and Plotly median-versus-n charts. No refitting or replacement scientific engine is introduced.

Scientific answers use a constrained evidence-selection contract: deterministic Python generates numerical findings, and local Ollama selects relevant sentence IDs. Unverified prose/indices are rejected. This deliberately limits open-ended AI narration. Equivalent-circuit explanations are curated from the canonical model. Natural-language routing supports the documented English metric/n-level/treatment patterns; it is not a general scientific programming interface. Frequency-resolved conversational tools remain unsupported: use the Results chart and figure-analysis action instead. Inference, error propagation and causal noise attribution are NOT implemented without defensible clusters/covariance. Proposed tool names are bounded dispatch categories, not arbitrary code execution.

Evidence and derived chart specifications are atomically written only under `state/research/`. No HTTP route serves these internal records. Ordinary responses omit source metadata. Local Ollama consent is requested once per browser session; models are discovered internally and inference is serialized. No cloud fallback, IBM tools or shell execution exists. Tables are bounded to 1,000 rows / 4 MB; no raw PUB histories or numerical libraries are loaded by the research layer. Model keep-alive is five minutes.

Interpretation repair: the actual Nyquist export includes frequency correspondence; the earlier adapter omitted it. Normalized residuals now use the canonical residual / max(measured magnitude, machine epsilon) transformation. Distribution summaries use accepted per-PUB SSE. The convergence CI table contract remains unavailable: intervals are not reverse-engineered from PNGs.

Before: nine operational navigation tabs, connection/context controls and visible source metadata. After: six research navigation tabs, minimal conversational input, quantitative comparison tables/charts and internal-only evidence records.

Validation: 27 focused Python checks, 20 chat DOM checks and seven security checks passed. Read-only validation confirmed 228 protected files unchanged and real research rows for n=5?20. n=25/30 exports are unavailable to this adapter. Accepted-slot checks can produce preliminary subsets even when a publication contains all planned rows.

Validation uses focused offline tests only. Process command-line inspection was denied, so LIVE worker absence was not assumed. Browser screenshots were not verified in this environment. Legacy UI tests asserting removed controls/navigation require updated expectations; full-suite compatibility is not claimed.

After stopping only the foreground Dashboard with Ctrl+C, restart:

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
.\start_dashboard.ps1
```

Open http://127.0.0.1:8766. This starts no production worker. Earlier implementation notes below describe historical versions and may include controls now hidden.

## Architecture, assistant and figure interpretation update

Verification: 58 Python tests, 24 chat DOM checks, 74 guide DOM checks, 5,002 diagram geometry/control assertions and 7 isolation checks pass. These are not visual browser certification. The read-only production fingerprint check covers 228 unchanged source files. In-process HTTP checks verified eight Nyquist treatment summaries for each available n=5/10/15/20 publication. No live AI generation was requested during testing.

The compact architecture separates Python-controlled execution from production AI assistance and read-only Dashboard chat. Authorized production orchestration remains subject to Python safety enforcement. Circuit, treatment, job and recovery views are retained.

Floating **MXene AI Assistant** uses the existing shared local Ollama backend. Advanced settings contain consent, connection, model and optional job/treatment context. No cloud gateway is implemented; OpenRouter/OpenAI/Gemini/Claude are not shown as connected. The assistant never generates simulated responses.

All nine Results figures have a Scientific Interpretation panel. `interpretations.py` produces descriptive summaries from the existing hash-verified curve adapter, cached by figure/level/version/source hashes (maximum 64 entries). Sources are revalidated on cache hits. Nyquist summaries report coordinate ranges without assuming frequency pairing; Bode comparisons require matching frequency arrays. Residual summaries are descriptive, not new fitting or confidence intervals. Normalized residual and distribution/convergence contracts remain unsupported and explicitly unavailable. Source timestamps, hashes and finality are displayed. Figure chat includes the same deterministic summary with consent.

Start after stopping only the existing foreground dashboard with Ctrl+C (new backend route requires a restart):

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
.\start_dashboard.ps1
```

Open http://127.0.0.1:8766. No production worker is started. Browser rendering has not been visually certified; previous Chrome IPC restrictions remain a limitation.

# MXene Observatory ? isolated scientific dashboard

## Scientific diagram redesign (current frontend extension)

Overview and Campaign now share `static/diagrams.js` and `static/diagrams.css`.
There was no graph layout library in the inspected project (Plotly was used for
curves), so this extension uses a small dependency-free SVG hierarchy renderer
rather than adding a framework or CDN. Node positions are derived from graph
ranks/subtree spans, wrapped text and hierarchy spacing. Workflow edges use
reserved inter-rank lanes and exterior return rails; treatment branches use
hierarchical tree routing. Colors distinguish frozen inputs, automation, advisory
AI, Runtime, validation, recovery, UNKNOWN and scientific analysis.

* Architecture includes the frozen circuit/theta/observables/manifest bundle,
  Python gates, account/quota selection, Runtime, archive/validation, recovery,
  analysis/comparison and the dashboard observer. Data/control/advice have
  different line styles and real arrowheads.
* Lifecycle includes eight explicit decisions plus pending-job waiting and
  UNKNOWN reconciliation. Return paths are separate from the forward workflow.
* Execution Flow is a lazy expandable campaign ? n ? block ? Batch ? job ?
  logical-slot tree built from the existing API. A read-only real-data check
  expanded 1,059 nodes and retained all 177 recorded executions. Original and
  replacement attempts remain distinct and reference the same scientific slot.
* Campaign Design has six branches, each expandable to eight treatments, with
  planned counts separate from recorded acceptance. Execution Plan is a true
  **single-iteration DAG**; continuation leaves require a new gated iteration.
  Its companion execution tree shows actual block/Batch/job/PUB relationships.
* The actual QPY-derived circuit is displayed in folded views with physical
  wires, category colors, CZ connectivity, barriers, instruction indices and
  full-precision parameter tooltips. Display columns pack disjoint instructions;
  no gates are added, removed or retranspiled. All 106 source operations occur
  once across the views, with per-wire order preserved. Desktop views use ten
  columns; narrow displays use four. The 21-theta, mapping, observables and exact
  instruction tables remain. No QASM is invented and no frozen file is changed.
* The eight-treatment DD/TREX/ZNE matrix uses colored checks for recorded enabled
  settings and muted disabled symbols. Filtering is display-only. Recorded
  Runtime options remain available in the separate configuration table.

Controls: Fit all, Readable view, zoom +/?, drag-to-pan, Ctrl/Command + wheel,
keyboard arrows/+/-/F and browser full-screen. Full-screen failure is reported.
Hover/focus shows source details, and the text outline provides a nonvisual
representation. Expanding branches focuses the relevant hierarchy and renders
only opened descendants. No backend API or AI integration was modified.

Validation: **50 existing Python tests**, **74 existing frontend DOM checks**,
**24 chat checks**, **7 isolation checks**, and **5,716 diagram geometry/data/control
assertions** passed. These assert directed topology, a DAG for one execution
iteration, no routed segment crossing a node interior in tested workflows,
node/text-line bounds, all QPY operands/parameters/order, lazy expansion/collapse,
actual treatment activation, viewport controls and mobile instruction coverage.
The assertion count includes per-segment/per-node checks; it is not a count of
independent test scenarios. All 228 baseline production fingerprints were unchanged.

**Browser limitation:** a fresh isolated headless Chrome launch failed with
Windows Mojo/Crashpad IPC `Access is denied`. Full layout-engine rendering,
font metrics, visual connector intersections and desktop/mobile appearance are
therefore **not visually certified**. Programmatic geometry tests are not a claim
of visual perfection. No backend service or production worker was restarted.

Changed files: `static/workspace.js`, `static/index.html`, `static/guide.json`,
`test_guide_dom.cjs`, `README.md`. Added: `static/diagrams.js`,
`static/diagrams.css`, `test_diagrams.cjs`. Read-only verification artifacts and
isolated browser-profile files are confined to `state/`.

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
node .\test_diagrams.cjs
.\start_dashboard.ps1
```

Current URL remains **http://127.0.0.1:8766**. If the dashboard is already running,
reload the browser; this frontend extension needs no server restart. The existing
AI backend is local Ollama. A multi-provider OpenRouter/OpenAI/Gemini/Anthropic
Gateway is **not implemented by this frontend-only diagram change**, whose strict
scope preserves backend APIs and the current AI integration.


This incremental FastAPI/HTML/CSS/vanilla-JavaScript application reads the existing MXene campaign without modifying production code, registries, archives, frozen inputs or scientific exports. The existing nine tabs, User Guide and contextual Help are retained.

## Start and open

Current verified URL: **http://127.0.0.1:8766**. The actual `serve.py` was already configured for port 8766 when this upgrade began; historical reports used 8765. Run one dashboard only.

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
.\start_dashboard.ps1
```

Stop a foreground dashboard with **Ctrl+C in its launch terminal**. Do not terminate unrelated Python processes. There is no HTTP shutdown endpoint. The isolated `.venv_dashboard` environment is already installed. The earlier dependency inventory is in `dependency_inventory.json`; no production Python environment was modified. Local charting uses vendored Plotly.js 3.5.0 copied from the machine's installed Plotly distribution, with its MIT license in `static/PLOTLY_LICENSE.txt`. No CDN or package download is required.

## Implemented views

* **Overview:** Research Summary, System Architecture, Campaign Lifecycle and Execution Flow sub-tabs. Accessible SVG diagrams describe verified responsibilities, frozen inputs, Python gates, advisory AI, IBM Batch/jobs/PUBs, archive/validation, recovery and UNKNOWN reconciliation. Diagrams describe workflow, not current hardware activity. Recovery loops make the workflow a graph; it is not claimed to be an acyclic graph.
* **Campaign:** Campaign Design, Frozen ISA Circuit, Mitigation Treatments and Execution Plan. Six source manifests supply actual conditions and treatment options. A six-branch diagram distinguishes planned counts. Observed execution timestamps are a chronological table, not invented durations or a Gantt chart. Collected counts remain N/A if collection evidence is incomplete.
* **Frozen circuit:** an explicit offline QPY export verifies all three frozen source hashes first. The genuine QPY has 156 wires, seven active physical wires, depth 20, six two-qubit gates, zero measurements and 106 instructions including directives (the saved ISA size excludes directives). The SVG retains instruction order and connects shared gates; 149 idle wires are explicitly omitted. Exact operands/parameters, all 21 theta values, physical mapping and mapped observables are shown in tables. The server revalidates source and SVG hashes on every circuit request. No Qiskit, Aer or IBM SDK is imported by the server.
* **IBM Jobs:** existing six-level minimal white table remains. Live Monitoring is a separate **unavailable** tab: there is no authorized, credential-safe status-only IBM adapter in this dashboard. No network lookup or automatic provider polling was enabled. Cached status stays explicitly labeled.
* **Recovery:** six levels, eight treatment rows per level, expandable logical-slot and execution evidence. Accepted sources are canonical; pending originals/replacements are never counted as valid or missing. UNKNOWN pauses, confirmed invalid records, terminal failures and DONE jobs awaiting retrieval/validation are distinct. Missing and Replacement Needed are N/A when permanent absence/eligibility cannot be established. The dashboard never authorizes recovery; it does not claim to distinguish retrieval from validation when evidence cannot do so.
* **Results Gallery:** fixed 3/2/2/2 panels: Nyquist/Bode magnitude/Bode phase; real/imaginary residuals; normalized real/imaginary residuals; distribution/median-CI convergence. Actual convergence filename is `05_mitigation_median_CI_convergence.png`. Extra published figures remain downloadable. Each n-level/version is isolated. Missing results show Data Unavailable; no other level or campaign plot is substituted.
* **Interactive figures:** Nyquist, Bode and unnormalized residual charts consume exact hash-verified CSV values. Nyquist uses the exported minus-imaginary column. Frequency plots use positive Hz on logarithmic axes. Plotly supports hover, legend toggles, zoom/reset and responsive layout with white backgrounds/no grids. Normalized residuals, distribution and CI panels remain PNG-only because no independently verified interactive numerical/method contract is exposed. No fitting, interpolation, bootstrap or CI inference occurs. PNG exports are unchanged.
* **Statistics:** existing C/D/E/F artifacts only when present. The production comparison adapter remains blocked. Conditional cluster inference requires reviewed provenance; unsupported hierarchical/paired methods are not presented as available.
* **Global AI chat:** floating launcher on all nine tabs; compact/resizable, fullscreen and minimized states; conversation preserved through tab navigation, but not page reloads. New Chat, Enter/Shift+Enter, loading/error states, model selection and response copying work. Non-streaming generation has no verified cancellation operation, so Stop Generation is explicitly unavailable. Minimizing does not cancel generation.

## AI root cause and verified integration

Previously, Analyze with AI was deliberately disabled in the frontend, no analysis route existed, and the process guard rejected every outbound connection, including loopback. The upgrade introduces `local_ai.py`, without changing the production Agent or its provider code.

The user must tick local-only consent and click **Connect local Ollama**. The adapter can contact only literal `127.0.0.1:11434`, only `/api/tags` and `/api/chat`, in a scoped `ContextVar` permission reset in `finally`. The socket guard otherwise remains active; IBM SDK imports, subprocesses and external outbound connections remain blocked. The server never starts Ollama, downloads models, requests credentials or falls back to a cloud model. A functioning Ollama installation with an existing local model is a prerequisite; no new service-install/start command is claimed verified.

Consent-gated POST endpoints require `X-MXene-AI: local-only` and reject foreign browser Origins. Model names must match the actual local model list. Inputs, history and output size are bounded; only one inference is allowed at a time; timeout is 90 seconds; the model is asked to unload afterward. There are no tool definitions or command execution interfaces.

Server-selected context includes local levels and a bounded subset of registered job records. Optional Job ID/treatment filters cannot create provider requests. Figure context includes verified artifact metadata and, for supported curves, explicitly labeled first/last exported point excerpts. CSV context is a bounded row excerpt. **PNG pixels are not supplied.** Sources, snapshot timestamp, model and limitations accompany each answer. User/model prose is rendered as text, never executable HTML.

A real connectivity/generation check succeeded with **qwen3:1.7b**. A quantitative probe initially returned the wrong accepted count (5 instead of 40 for n=5). That initial failure is preserved in `state/ai_verification_initial_failed_claim.json`. The interface now separates authoritative numeric records from AI interpretation, and a narrow consistency guard withholds recognizable contradictory accepted-PUB-count claims. It is **not comprehensive scientific fact validation**. A subsequent actual qualitative response explained why DONE is not scientific acceptance; its context correctly contained 40/40 accepted PUBs. Evidence is in `state/ai_verification.json`. Independently verify every scientific interpretation, regardless of model availability.

## Read-only boundaries and sources

The production SQLite source is opened with `mode=ro` and `query_only=ON`; a bounded online backup is stored under this directory's `state/`. All subsequent SQL is against that local snapshot. A browser poll every 120 seconds refreshes local evidence, not IBM status. The source connection can briefly participate in WAL reader coordination; zero contention is not claimed. No migrations/checkpoints or provider calls are made.

Canonical production location: `../03_ai_assistant/`, including `01_ai_agent/`, `02_skills/`, and `03_python_automation/`. The only reused scientific-selection code in the server is the audited read-only `JobRegistry.repaired_pub_values()` selector on the private snapshot. This does not rewrite acceptance or scientific methods.

Scientific root: `../../01_vqe/06_DD_TREX_ZNE_mitigation/output/`:

* `03_IBM_execution_records/ai_assistant/job_registry.sqlite3`
* `03_IBM_execution_records/ai_assistant/independent_n_campaigns/vqe-independent-n-01/nN/experiment_manifest.json`
* `03_IBM_execution_records/locked_inputs/{circuit.qpy,theta.csv,observables.json,lock.json}`
* `01_results_and_figures/n=N[/version]/` and `02_run_level_data/n=N[/version]/`

Matched per-n export-manifest pairs and file hashes gate publication display. Version, source timestamp and hash are shown. Matched publication does not independently certify final campaign completion. Historical/adaptive/test directories are excluded. Statistics allowlists retain existing `../results/.../comparison_105_pubs` and published output equivalents. Credentials, SQLite downloads, raw IBM archives and raw secret-bearing logs are not exposed.

HTTP data routes remain read-only GETs; the only new POST routes perform explicitly requested local AI interpretation. `security.install()` is never globally disabled. CSP permits inline **styling** for Plotly's renderer, but not inline scripts, arbitrary external scripts or external connections. IBM Live Monitoring needs a separately reviewed opt-in status/metrics service with registered-ID and authorized-account enforcement; this is documented, **not implemented**.

## Implementation files

| File | Role |
|---|---|
| `app.py` | Existing routes plus charts, frozen metadata/SVG, design and consent-gated AI routes |
| `reader.py`, `jobs_view.py` | Existing snapshot/catalog/jobs projections, with recovery summaries and collection evidence |
| `science_ui.py` | Exact exported curve projection, frozen provenance checking, source manifest projection and conservative slot classification |
| `local_ai.py`, `security.py` | Fixed loopback Ollama adapter and scoped network authorization |
| `build_circuit.py` | Explicit offline QPY decoder/SVG export; no simulation or Runtime imports |
| `static/app.js`, `static/index.html` | Existing application integration |
| `static/gallery.js`, `static/workspace.js` | Gallery, diagrams, Campaign and Recovery sub-tabs |
| `static/chat.js` | Global floating session chat UI |
| `static/science.css` | Scoped gallery/diagram/chat styles; white Jobs/Recovery preserved |
| `static/plotly.min.js`, `static/PLOTLY_LICENSE.txt` | Local plotting asset/license |
| `static/guide.json` | Centralized updated documentation and safety boundaries |
| `test_science.py`, `test_chat_dom.cjs` | New numerical/provenance/classification/AI authorization/chat tests |
| `test_guide.py`, `test_guide_dom.cjs`, `verify_live.py` | Existing checks updated for added views and current port |
| `state/frozen_circuit.{json,svg}` | Dashboard-only derived QPY visualization and provenance |

No production code or scientific output was moved or edited. Previous README is retained as development-only historical documentation in `state/README_before_scientific_upgrade.md`.

## Tests and known gaps

```powershell
Set-Location 'C:\Users\nguye\OneDrive\Desktop\MXene QPU Hardware\02_Hardware\04_code\MXene_Dashboard_Dev'
& .\.venv_dashboard\Scripts\python.exe -B -m unittest test_dashboard test_guide test_jobs test_science -v
node .\test_guide_dom.cjs
node .\test_chat_dom.cjs
& .\.venv_dashboard\Scripts\python.exe -B .\test_guard.py
Invoke-RestMethod http://127.0.0.1:8766/api/health
```

Results: **50 Python tests**, **74 frontend DOM checks**, **24 chat DOM checks** and **7 isolation checks** passed. Checks cover exact curve values/sign, cross-level rejection, nonfinite rejection, real frozen hashes/21 angles, six manifests, pending/UNKNOWN/recovered classifications, consent/Origin enforcement, unsupported endpoints, honest backend failure and a contradictory-count rejection. Existing dashboard/guide/jobs tests remain passing. Starlette emits the existing test-client deprecation warning; no tests failed.

Real HTTP verified six manifests, frozen circuit, 48 treatment recovery summaries, 760 accepted PUBs, and Nyquist data with 10 series ? 132 points. No IBM provider audit was performed. All checked 228 original baseline fingerprints remained unchanged. The live database/WAL may legitimately change independently; logical production writes are prohibited by this code.

**Visual limitation:** automated tests execute frontend DOM contracts, not a full layout engine. Desktop/mobile rendering, screen-reader behavior and Plotly interaction have not been visually certified; previous isolated browser launches were blocked by Windows IPC restrictions. AI fluency and scientific factuality are not certified. IBM live lookup, interactive CI/normalized/distribution contracts, independently proven recovery eligibility and production comparison source adaptation remain unavailable.
# Worker-control integration status (2026-11-08)

**Partial implementation; production AUTO/STEP execution is NOT enabled or LIVE-ready.**
Research Workspace recognizes Start/Pause/Resume Auto Manager, Show worker status,
Run preflight, Monitor jobs, Collect completed jobs, Analyze completed n-level n=5,
and Export figures n=5 before Ollama. Quick Assistant remains read-only.
Existing research commands such as Analyze n=5 continue using the read-only bridge.

`worker_supervisor.py` implements a typed control-plane contract, conversation-bound
120-second confirmations, persistent dispatch state and fail-closed ownership checks.
Its production adapter only observes candidate processes; it cannot launch or pause
workers. No setting or chat prompt enables production execution. A candidate PID,
creation time, exact entrypoint or stored RUNNING state does not prove WorkerLock
ownership. Unknown/partial process discovery stays UNKNOWN. Dashboard security is
unchanged: no subprocess launch, IBM imports, outbound IBM access or production writes.

The dashboard-only `state/worker_control.sqlite3` stores pending confirmations and
dispatch results; it never replaces the campaign registry. A dispatch interrupted
after durable intent is never automatically replayed. Fake-adapter tests cover
ownership/checkpoint changes, duplicate start, attach, safe pause requests (not
acknowledgement), resume, single-step exclusion, UNKNOWN, expiry, replay and refresh.
These tests do **not** certify the canonical worker integration. See
`AUTO_MANAGER_CONTROL_PLAN.md` for exact adapter blockers and audited functions.

Verification for this change: 47 focused Python tests passed (`test_worker_supervisor`,
`test_workspace`, `test_bridge`), 14 assistant UI and 70 guide DOM checks passed,
and all seven isolation checks passed. `test_control_served.py` verified the actual
localhost endpoint: eight blocked control commands, ownership-unverified status,
refresh persistence and the existing 760/840 campaign query, with zero Ollama calls.
The served workspace DOM/HTTP regression also passed. These are not native-browser
visual tests. The first served-test run had an incorrect expected engine label;
that assertion was corrected to the existing API value and the test passed.
`verify_live.py` found all 228 protected source/scientific fingerprints unchanged;
this is not a before/after hash certification of the entire production registry.
No production action was attempted. Only the verified Dashboard was restarted.



### IBM Execution & Runtime Records

A top-level historical-records tab reuses `jobs_view.project_jobs` and the existing read-only SQLite snapshot. It is separate from AI Research Workspace and AUTO/STEP controls. Records load on opening and explicit **Reload saved records** only; this tab never polls IBM or starts a worker.

Canonical sources beneath `02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output/`:
- `03_IBM_execution_records/ai_assistant/job_registry.sqlite3`: campaign-level membership, original jobs, replacement attempts, validation and saved monitoring/recovery events.
- `03_IBM_execution_records/replicate_block_*/<treatment>/job_submitted.json` and `job_retrieved_metadata.json`: recorded identity, submission/collection times, status and `metrics_raw.timestamps` / `usage.qpu_charge_time_seconds`.
- `04_runtime_logs/`: empty at inspection. The same explicit `job_retrieved_metadata.json` schema is supported here if present; arbitrary raw logs are deliberately not served. Unknown schemas, unlinked records and credentials are never displayed.

The table supports all six n-levels or the entire campaign, Batch/treatment filters, Job ID search, sorting, 25-record pages and expandable runtime/event/validation/recovery details. Metadata joins require matching job, account, backend, logical block and Batch IDs. Execution duration is finish minus running time, not QPU usage; resource-unit values are not interpreted as seconds. Missing values remain N/A. All original/replacement executions in the canonical projection remain visible, including UNKNOWN identities.

No second execution database is created. The existing private dashboard snapshot remains the only database copy. Large/unsupported metadata is skipped; these skips are reported. The view does not certify scientific completion or refresh provider status.


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


### Unified campaign history: completed recorded-schema integration

The top-level **IBM Execution & Runtime Records** tab uses one row per registered IBM Job ID. Original jobs and grouped replacements come from the existing `jobs_view.project_jobs` projection and canonical accepted-slot selection. Unknown intents without a Job ID are counted separately; they are never fabricated into jobs. No new database, provider client or monitoring loop is introduced.

Schema inventory (canonical mitigation `output` directory): 152 `job_submitted.json`, 132 `job_retrieved_metadata.json`, 132 `job_metrics_raw.json`, and 19 `round_plan.json` files. No `job_info.json` was found. `04_runtime_logs` contains no files at this inspection. It supports the already audited submission/retrieval metadata contracts when present; unknown log formats are not guessed. Metrics files without identifiers are not independently joined by timestamps. Their recorded metrics are already embedded in the identity-bearing retrieval metadata.

Additional evidence comes from exact registry-referenced replacement `raw_result.json` archives (`replacement_job_id`, `original_source_job_id`, `submitted_utc`, `provider_metrics`, QPU seconds) and adopted calibration archives (`job_id`, `runtime_session_id`, provider metrics). Repository-relative recorded paths resolve only inside the canonical execution root; hashes and available identities are checked. The embedded legacy `n=5` is a per-job chunk size and never overrides the registry's experiment n-level. Batch sequence is read from registered experiment blocks, with recorded logical-block identifier fallback for older projections.

The compact table shows n, Batch sequence, treatment, full Job ID with Copy, account, planned PUBs, UTC submission/start/finish, recorded QPU seconds, status and execution classification. Account and original/replacement filters complement n/Batch/treatment filters, Job ID search, sorting and 25-row pagination. Expanded rows show accepted/invalid/explicitly missing PUBs, retrieval/validation status, recovery attempt and parent Job ID, elapsed time and recorded events. Missing PUBs mean explicit validation evidence; uncollected queued work is not inferred missing. Accepted counts are not submitted counts.

Original timestamp strings remain in saved details; UTC conversion requires an explicit offset. Client submission time and provider creation time remain distinct, with the fallback basis labeled. Elapsed time is calculated only from recorded start/finish timestamps. QPU seconds come solely from explicit IBM/registry usage fields, never resource units, elapsed time or reservations. Numerically equal integer/float values agree. Conflicting values become N/A and a diagnostic flag; field evidence, source hashes and joins remain in server-side `execution_records.AUDIT`. Dated status history can advance; undated conflicting statuses are not silently resolved.

The verified local snapshot has 177 unique jobs: n=5: 9; n=10: 19; n=15: 26; n=20: 33; n=25: 42; n=30: 48. Classification: 160 original, 17 replacement. This describes saved records, not current IBM status or final campaign completion. Availability and integrity checks are in `state/execution-records-verification.json`. No automatic polling occurs while this historical tab is active. Manual reload uses saved local evidence only.


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

### Canonical Final Comparison publication

The Statistics tab consumes `/api/comparison` and displays the existing PNGs; it
does not calculate or redraw C/D/E/F. Full-resolution PNG/CSV/JSON downloads use
`/api/comparison/artifacts/{filename}`. The same published CSV supplies the table.

Only these mitigation output directories are authoritative:

- `01_results_and_figures/compare/`: flat 600-DPI PNG files only.
- `02_run_level_data/compare/`: flat CSV and JSON files only.

`publication_vNNN.json` selects an immutable generation and binds its manifest and
SHA-256 inventory. Later artifact filenames carry `__vNNN`; no old files move or
change. Missing/corrupt canonical artifacts are explicit errors, never a fallback
to `04_code/results`. Completion status, version, UTC publication time, treatment
labels, n-levels and actual descriptive statistics come from that same contract.

Reference images are visual templates only. The active publication computes D
from accepted observations and verified optional classical/ideal sources. Figure C
is reused unchanged. E/F present matched n-level resampling sensitivity intervals
(10,000 shared draws, seed 1013); C/D use ordinary IID sensitivity intervals
(3,000 draws, seed 1013). Neither method establishes independent experimental
confidence intervals. The API returns the method description and contrast table
from the active canonical manifest/CSV; the UI never recomputes them.

Offline endpoint checks: `.\.venv_dashboard\Scripts\python.exe -B -m unittest test_comparison -v`.

The active comparison publication supplies its shared treatment palette through
`/api/comparison`; table labels use those colors. All four figures are original
600-DPI canonical PNGs, selected by the commit-last publication marker. Styling
versions reuse the verified CSV files and do not introduce frontend calculations.

Exp. 01 standardization: each figure section reads its method label, seed and
caveat from the canonical manifest. C is ordinary pooled-prefix bootstrap; D is
ordinary PUB-level bootstrap; E/F are conditional matched n-level cluster-bootstrap
sensitivity intervals. All use seed 1013; C/D have 3,000 replicates and E/F 10,000.
