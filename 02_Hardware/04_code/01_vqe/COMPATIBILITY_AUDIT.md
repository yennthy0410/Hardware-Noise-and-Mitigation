# VQE compatibility migration audit

Scope: all 22 Python scripts present before migration were parsed and searched.
Study A, AI assistant and historical research output files are not migration targets.
No frozen circuit.qpy, theta.csv or observables.json exists in this repository.
Preparation/submission remain blocked; numerical source validation is unperformed.

## Updated active mitigation path users
- mitigation_pilot.py: legacy root markers, source/import paths and output paths replaced.
- run_multiple_n.py: root validation, pilot target and log location replaced.
- qpu_auto_manager.py: root validation, pilot target and all result/log locations replaced.
- qpu_auto_submit.py: root validation, pilot target, record/ledger and exception-log locations replaced.
- setup_ibm_accounts.py: explicit main/configure gate; import does not configure credentials.
- scheduler_diagnostic.py: offline metadata only, no subprocess, network, or research-data writes.
- check_circuit_duration.py: explicit main/source/online gates; timing calculation retained.

repository_paths.py owns root markers, canonical scientific imports, pilot selection,
round validation, frozen-file presence, hashes and mitigation output containment.
Original scientific modules remain solely in 01_quantum_eis_fit/04_code/vqe.
New outputs use 02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation/output and its
01_results_and_figures, 02_run_level_data, 03_IBM_execution_records, 04_runtime_logs folders.
Legacy migration code is retained but disabled. Existing round preparation and
scientific analysis output replacement are refused. No Batch or new scheduler logic added.

## Other inspected scripts: retained non-mitigation behavior
These scripts are outside the active DD/TREX/ZNE path repair; do not execute them
as migration steps. Their historical input paths are not silently redirected
to different scientific data.
- analyze_quantum_to_eis_error_propagation.py: main() uses experiments/HEA_VQE/01_simulator_to_qpu_hardware/results/EIS14 for simulation/input/output defaults.
- compare_hardware_noise_characterization.py: main() discovers old experiments root and 01_run_qpu inputs.
- compare_repeatability_5run.py: get_classical() searches reference_results, experiments initial validation and results/classical; output references still assume historical run layout.
- compare_transpilation_layout_sensitivity.py: analogous historical classical/raw-run references.
- collect_hea_circuit_timing.py: find_root() searches historical experiments/.../01_run_qpu.
- run_controlled_noise_scenarios.py: calibration(), angles_file(), main() use legacy calibration, seed_1013 theta, simulation and raw-run defaults.
- run_hardware_qpu.py: theta_file() searches legacy optimizer results; main() defaults to experiments/HEA_VQE/01_transpilation_layout_sensitivity/results/part1_multi_qpu. This is a separate RAW benchmark, not mitigation.
- rebuild_from_archive.py: SRC/BASE refer to MXene-Quantum-EIS-Hardware archive members and old scripts folders. Archive member names must remain literal; running it would write historical destinations.
- audit_src_dependencies.py: legacy patterns are intentional audit search expressions.
- build_all_offline.py, plot_controlled_noise_publication.py, plot_fixed_isa_benchmark_publication.py, plot_unfixed_noise_propagation_publication.py: existing publication-root discovery; archive/move/delete behavior retained, not executed.
- plot_eis_spectra_publication.py, plot_noise_characterization_publication.py: current Study B discovery; no active mitigation legacy root dependency.

No hard-coded Windows drive-absolute path was found in these 22 sources.
Old absolute paths in historical JSON/CSV evidence are left untouched.

## Offline verification
tests/test_repository_compatibility.py uses unittest and verified owner-marked
scratch selected by MXENE_SCRATCH_DIR or the workspace fallback.
IBM Runtime and unavailable NumPy are mocked for import/path tests; no numerical
or live API compatibility is claimed. AST hashes protect unchanged mitigation
configuration, result validation and bootstrap plotting calculations.
Run with bytecode disabled; do not run preparation or existing workflow mains.

## Remaining prerequisites
Original frozen input files and independent provenance/hash/layout evidence;
a compatible environment with numpy, pandas, matplotlib, qiskit and
qiskit-ibm-runtime (Aer/SciPy for other workflows). No packages were installed.
The original source default was:
experiments/HEA_VQE/01_simulator_to_qpu_hardware/results/EIS14/01_run_qpu/ibm_fez/EIS14/fixed/run_01/
Its circuit.qpy, theta.csv and observables.json are absent. Use an explicit
verified --source later; do not reoptimize or retranspile a replacement.
