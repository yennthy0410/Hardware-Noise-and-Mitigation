# MXene quantum EIS automation

The Auto Manager now uses three code components under
`02_Hardware/04_code/03_ai_assistant/`:

- `01_ai_agent/`: operator CLI, optional AI advice and typed readiness tools.
- `02_skills/`: workflow, campaign-comparison and statistical instructions.
- `03_python_automation/`: deterministic IBM execution, recovery, validation,
  science, exports and the single canonical `dependence_statistics.py`.

The 26 legacy root module paths remain compatibility entrypoints. Both distinct
mitigation implementations are preserved. No production data, IBM records or
historical outputs are relocated. Numerical C/D/E/F implementations are now
consolidated without a `scientific/` subdirectory; the old comparison script is
a forwarding wrapper.

See the [implementation README](02_Hardware/04_code/03_ai_assistant/README.md)
for exact module responsibilities, frozen run_02 design, 150/40-second quota
policy, targeted recovery, statistical limitations, tested PowerShell commands,
rollback and known test failures. See the
[file inventory](02_Hardware/04_code/03_ai_assistant/FILE_INVENTORY.csv) for all
actual paths and static import relationships.

**Migration is not a claim of full production readiness.** Final campaign
comparison remains blocked by incomplete data and unverified source/export
contracts. Existing missing-file and plotting/fixture test failures are reported
rather than hidden. No IBM jobs were submitted during migration.
