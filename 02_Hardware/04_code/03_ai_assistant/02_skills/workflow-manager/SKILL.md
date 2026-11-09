---
name: workflow-manager
description: Route MXene campaign work through existing Python safety checks and offline final-stage readiness.
---

Python remains authoritative. Use `01_ai_agent/mxene.py` (the root `mxene.py`
remains a compatibility entrypoint); never execute
model-generated shell or construct IBM submissions from model output.

Canonical deterministic modules live directly in `03_python_automation/`;
existing flat imports forward there without duplicating implementations.
The current deterministic lifecycle is reconcile, monitor, retrieve, durable
archive, validate, targeted recovery, account/quota selection, then next Batch.
Retain WorkerLock, UNKNOWN pauses, authorization and frozen run_02 checks.
Runtime estimates are diagnostics; centralized original/recovery quota floors
are 150/40 seconds with verified unreserved balances.

Final intended sequence is six fully collected/recovered/analyzed n-levels,
verified Figures C and D, dependence-aware statistics or documented method
skips, then a final report. Load campaign-comparison only at that boundary.
Load dependence-aware-statistics only after C/D verification.

Current limitation: `mxene.py compare` is a read-only readiness tool. Its
source-layout adapter is blocked; it does not generate figures or certify final
campaign completion. Existing LIVE completion does not yet invoke these new
stages. Do not represent the proposed end-to-end agent as implemented.
