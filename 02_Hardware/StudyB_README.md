# Study B — Quantum Hardware Noise and Error Mitigation for MXene EIS Fitting

**Scope:** IBM QPU experiments, Qiskit Aer, VQE/VQA, QAOA, DD, TREX, and ZNE.  
**Status:** VQE hardware repeatability, characterization, and Aer noise analyses have been performed. QAOA hardware and M0–M7 mitigation must be marked *planned*, *in progress*, or *completed* according to validated records.  
**Scientific integrity:** Do not invent missing data, equate simulation with QPU measurements, or overwrite raw results.

## 1. Research background and motivation

Study A investigates quantum-assisted fitting of measured MXene electrochemical impedance spectroscopy (EIS) data using continuous VQE/VQA and discretized QAOA. Study B examines what happens when these algorithms encounter realistic quantum hardware noise. The central causal chain to investigate is:

**Hardware noise → quantum output deviation → EIS parameter deviation → impedance distortion → SSE/RMSE degradation → mitigation and recovery.**

A reduction in quantum measurement error does not necessarily improve the final EIS fit because the parameter mapping and equivalent-circuit impedance model can be nonlinear and parameter-dependent.

## 2. Research objectives

1. Characterize readout errors, 1Q/2Q gate errors, T1/T2, and circuit coherence exposure across IBM QPUs.
2. Quantify fixed-ISA execution repeatability and cross-QPU EIS fitting stability.
3. Assess repeated-transpilation reproducibility without falsely claiming physical-layout sensitivity when layouts do not change.
4. Isolate readout, gate, decoherence, and combined noise using Qiskit Aer.
5. Explain quantum-to-EIS error propagation using Jacobians, impedance sensitivity, and nonlinear SSE attribution.
6. Evaluate DD, TREX, ZNE, and their combinations relative to unmitigated hardware M0.
7. Compare mitigation gains, variability, and execution costs for VQE/VQA and QAOA, where comparable results exist.

## 3. Research questions

| ID | Research question | Key evidence |
|---|---|---|
| RQ1 | How do calibration-derived noise properties differ among IBM QPUs? | Readout/gate errors, T1/T2, circuit exposure |
| RQ2 | How reproducible are EIS parameters and SSE/RMSE across repeated fixed-ISA runs and QPUs? | Five-run distributions, expectation and parameter drift |
| RQ3 | Does repeated transpilation change ISA circuits or physical layouts? | Actual mappings, QASM/QPY, depth, 2Q counts |
| RQ4 | Which isolated noise mechanism most affects quantum outputs and EIS fitting? | Aer controlled-noise comparisons |
| RQ5 | How are quantum-output errors amplified or transformed into impedance and fitting errors? | Mapping Jacobian, sensitivity, spectral distortion, Shapley SSE |
| RQ6 | Do DD/TREX/ZNE, alone or combined, improve end-to-end EIS fitting? | M0–M7 SSE/RMSE, uncertainty, validity, runtime |
| RQ7 | Do mitigation gains and costs differ between VQE/VQA and QAOA? | Matched cross-algorithm analyses |

**Hypotheses, not established results:** TREX may reduce readout bias; DD may suppress relevant idle-time decoherence; ZNE may reduce observable bias when noise scaling is reliable; combinations may interact positively or negatively.

## 4. Scientific theory

### 4.1. EIS fitting and reference metrics

The seven-parameter EIS model uses

\[
\mathbf p=(R_s,L,R_{ct},Q_1,n_1,Q_2,n_2).
\]

Use the **exact Study A impedance model, topology, CPE convention, parameter bounds, and frequency weights**; do not silently substitute another equivalent circuit. For an unweighted complex residual,

\[
\mathrm{SSE}=\sum_{k=1}^{N}|Z_{\rm data}(f_k)-Z(f_k;\mathbf p)|^2,\qquad
\mathrm{RMSE}=\sqrt{\mathrm{SSE}/N}.
\]

If the code uses weighted/normalized SSE or a different RMSE convention, preserve that definition consistently. Classical fitting is an EIS performance comparator, not the ideal quantum baseline.

### 4.2. Hardware noise mechanisms

**Readout/measurement noise:** Assignment errors (0→1 and 1→0) bias bitstring probabilities and Pauli expectations; readout asymmetry measures unequal assignment behavior.

**Gate noise:** Imperfect 1Q/2Q operations can introduce coherent and stochastic errors. Depolarizing noise is a simplified model, not a complete hardware description.

**Decoherence:** Energy relaxation (T1) and phase decoherence (T2) depend on gate/idle durations. Circuit duration divided by T2 is a rough exposure indicator, **not** a circuit failure probability.

**Combined noise:** Readout, gate, and relaxation errors coexist. Simplified Aer models may omit crosstalk, leakage, correlated noise, and calibration drift.

### 4.3. Quantum error management approaches

| Category | Purpose | Examples |
|---|---|---|
| Error suppression | Reduce noise during physical execution | DD, control-pulse optimization |
| Quantum error mitigation (QEM) | Reduce bias in noisy estimates without full fault tolerance | TREX, ZNE, PEC, Clifford data regression, symmetry verification, virtual distillation |
| Quantum error correction (QEC) | Encode and correct logical information | Stabilizer/surface codes |

**DD is error suppression; TREX and ZNE are error mitigation.** None guarantees lower final EIS SSE. See [1–9].

### 4.4. Dynamical decoupling (DD)

DD inserts scheduled control pulses into eligible idle periods to suppress some unwanted system–environment interactions. In an open-system description,

\[
H(t)=H_S+H_B+H_{SB}(t).
\]

Its effectiveness depends on idle time, pulse sequence, noise spectrum, scheduling, and errors from additional pulses. DD acts on circuit execution **before measurement**. The appropriate EIS test is whether it improves measured outputs, inferred parameters, and fitting error—not merely whether DD was enabled. References: [1], [2], [8].

### 4.5. Twirled readout error extinction (TREX)

TREX uses measurement twirling and calibration to mitigate bias in **Pauli expectation values**. A simplified model is

\[
e_i^{\rm meas}\simeq\lambda_i e_i^{\rm ideal},\qquad
\hat e_i^{\rm TREX}\simeq e_i^{\rm meas}/\hat\lambda_i.
\]

This is a conceptual approximation, not a full implementation specification. Calibration uncertainty and small correction factors can increase estimator variance. TREX does **not** automatically correct a full QAOA Sampler bitstring distribution. References: [3], [8], [9].

### 4.6. Zero-noise extrapolation (ZNE)

ZNE estimates a zero-noise observable by measuring at several effective noise factors and extrapolating:

\[
e_i(\lambda)=e_i(0)+a_i\lambda+b_i\lambda^2+\cdots.
\]

For illustrative **linear** extrapolation at factors 1 and 3,

\[
\hat e_i(0)=[3e_i(1)-e_i(3)]/2.
\]

Actual IBM Runtime noise factors, amplification mechanisms, and extrapolators must be recorded from each job's configuration. ZNE may amplify statistical variance, be biased by imperfect scaling, or yield unstable/nonfinite estimates. References: [4], [5], [8], [9].

### 4.7. Quantum-to-EIS error propagation

For VQE/VQA, define seven Pauli expectations \(\mathbf e\) and the frozen mapping \(\mathbf p=g(\mathbf e)\). For small perturbations,

\[
\Delta\mathbf p\simeq J_g\Delta\mathbf e,\qquad
(J_g)_{ij}=\frac{\partial p_i}{\partial e_j}.
\]

Then

\[
\Delta Z(f)\simeq\nabla_{\mathbf p}Z(f;\mathbf p)\,J_g\Delta\mathbf e.
\]

The Jacobian is a **local approximation**; raw derivative magnitudes have different units. A dimensionless impedance sensitivity is

\[
S_i^{\rm norm}(f)=\left|\frac{p_i}{Z(f)}
\frac{\partial Z(f)}{\partial p_i}\right|.
\]

Report actual \(|Z_{\rm noise}-Z_{\rm ideal}|\) and SSE/RMSE alongside sensitivities. Shapley allocation of \(\Delta\mathrm{SSE}\) describes model-based attribution, **not causal identification of a physical noise channel**.

For QAOA p=2, 21-qubit bitstrings encode seven discretized EIS parameters (three bits per parameter). Track distribution drift, decoding, good-solution probability, discretization error, and final EIS SSE. VQE's expectation Jacobian cannot be transferred unchanged to QAOA best-shot decoding.

### 4.8. Mitigation effectiveness and uncertainty

Let M0 denote hardware execution with the investigated mitigation options disabled, while explicitly documenting other Runtime settings. Define

\[
G_{\rm SSE}(M_k)=100\%\,
\frac{\mathrm{SSE}(M_0)-\mathrm{SSE}(M_k)}
{\mathrm{SSE}(M_0)}.
\]

Positive gain means lower SSE; negative gain means worse SSE. Also compare expectation error, normalized parameter drift, RMSE, run-to-run variability, valid-result rate, shots, QPU usage, and total overhead. Use uncertainty intervals and avoid choosing a winner from a single best run.

## 5. Study B organization and meaning of each part

```text
02_Hardware/
├── 01_vqe/
│   ├── 01_hardware_noise_characterization/
│   ├── 02_fixed_isa_repeatability/
│   ├── 03_repeated_transpilation_reproducibility/
│   ├── 04_controlled_noise_scenarios/
│   ├── 05_noise_to_eis_error_propagation/
│   ├── 06_mitigation_benchmark_M0_M7/
│   ├── 07_accuracy_cost_tradeoff/
│   └── 08_summary/
├── 02_qaoa/                    # Corresponding experiments; QPU work pending
├── 03_compare_vqe_qaoa/        # Cross-algorithm analysis when data are available
└── 04_code/
    ├── 01_vqe/
    ├── 02_qaoa/
    └── 04_ai_mitigation_assistant/  # Optional execution orchestration
```

### Part 01 — Hardware noise characterization

**Purpose:** Establish the physical noise context for `ibm_fez`, `ibm_kingston`, and `ibm_marrakesh`.

**Method:** Record calibration snapshots and timestamps, readout assignment/asymmetry, 1Q/2Q errors, T1/T2, circuit duration, and coherence exposure. Prefer metrics of **actually mapped physical qubits/gates** to backend-wide medians.

**Results:** Three cross-QPU multipanel figures: measurement noise; gate error/coherence budget; decoherence/circuit exposure.

**Meaning:** Explains which noise sources warrant investigation; calibration values alone do not prove which mechanism caused EIS fitting degradation.

### Part 02 — Fixed ISA repeatability

**Purpose:** Quantify run-to-run EIS fitting variability on a given physical circuit and between QPUs.

**Method:** Freeze optimal \(\theta^*\), base logical circuit, per-backend ISA, physical layout, mapped observables, shots, and execution options. Repeat the **same saved ISA** five times per backend.

**Results:** Cross-QPU SSE/RMSE distributions, expectation drift, parameter drift, Nyquist/Bode/residuals, job provenance.

**Meaning:** Estimates hardware execution repeatability at a specified mapping; it does not sample all possible layouts or automatically separate shot noise from temporal drift.

### Part 03 — Repeated transpilation reproducibility

**Purpose:** Test whether recompilation changes ISA representation or physical mapping.

**Method:** Recompile the same logical circuit and \(\theta^*\), save seeds, actual layouts, normalized circuit/QASM, QPY, depth, and 2Q gate counts; compare fitting results cautiously.

**Meaning:** If layouts and circuit representations remain unchanged, this supports **transpilation reproducibility**, not physical-layout sensitivity. Layout sensitivity requires genuinely distinct mappings and controlled executions.

### Part 04 — Controlled noise scenarios (Qiskit Aer)

**Purpose:** Isolate the impact of individual noise mechanisms.

**Method:** Using frozen scientific inputs and calibration-derived parameters, compare ideal/noiseless, measurement-only, gate-only, decoherence-only, and combined-noise cases, as actually implemented. Record noise models, seeds, shots, and software versions.

**Results:** Cross-QPU \(\Delta\mathrm{SSE}\), RMSE, expectation drift, parameter drift, and noise ranking.

**Meaning:** Establishes mechanistic hypotheses for DD/TREX/ZNE. Aer is a controlled approximation—not proof of the separate causal contributions on a real QPU. **Aer ideal baseline and hardware M0 are different.**

### Part 05 — Noise-to-EIS error propagation

**Purpose:** Explain how quantum measurement errors become parameter and spectral fitting errors.

**Method:** Use Part 04 outputs with the **exact Study A** `parameter_mapping.py` and `impedance_model.py`. Compute Jacobian, normalized sensitivity, parameter changes, actual spectral distortion, and nonlinear SSE attribution.

**Results:** Cross-QPU mapping amplification, SSE attribution, and impedance distortion. Per-backend Jacobians/OAT diagnostics are supplementary.

**Meaning:** Connects quantum output quality to the reliability of scientific EIS inference; local linearization and Shapley have interpretive limitations.

### Part 06 — DD/TREX/ZNE mitigation benchmark

**Purpose:** Determine whether mitigation improves end-to-end EIS fitting and whether combinations interact.

| Treatment | DD | TREX | ZNE |
|---|:---:|:---:|:---:|
| M0 — None | — | — | — |
| M1 — DD | ✓ | — | — |
| M2 — TREX | — | ✓ | — |
| M3 — ZNE | — | — | ✓ |
| M4 — DD + TREX | ✓ | ✓ | — |
| M5 — DD + ZNE | ✓ | — | ✓ |
| M6 — TREX + ZNE | — | ✓ | ✓ |
| M7 — ALL | ✓ | ✓ | ✓ |

This is a \(2^3\) factorial design: evaluate **main effects and interactions**, not just eight separate SSE bars.

**Method:** Preserve frozen scientific inputs, backend-specific base ISA, and observable definitions. Explicitly record DD sequence, TREX/measurement settings, ZNE factors and extrapolator, Runtime options, shots, PUBs, timestamps, QPU usage, and recovery lineage. DD and ZNE can change actual scheduled/executed circuits even if the base ISA is fixed.

**Validation:** Do not silently clip, replace, or discard nonfinite data. Preserve valid PUBs, identify missing logical slots, reconcile unknown/duplicate submissions, verify quota, and require authorization before QPU submission/recovery.

**QAOA limitation:** Estimator TREX/ZNE cannot automatically be treated as mitigation of a Sampler bitstring distribution. Define an observable-compatible workflow or explicitly separate estimator-level mitigation from decoded-solution improvement.

**Meaning:** Tests whether mitigation of quantum observables actually improves the final EIS result, and at what cost.

### Part 07 — Accuracy–cost trade-off

**Purpose:** Compare mitigation benefits with QPU runtime, shots, calibration overhead, execution count, variability, and valid-result fraction.

**Method:** Report median and uncertainty of SSE/RMSE alongside cost, including accuracy–cost Pareto analysis when supported.

**Meaning:** The smallest SSE does not necessarily represent the most robust or resource-efficient method.

### Part 08 — Summary

**Purpose:** Synthesize hardware characterization, repeatability, Aer sensitivity, error propagation, and mitigation evidence.

**Method:** Separate empirical findings from hypotheses, simulations from hardware measurements, and completed from planned experiments. Report limitations and provenance.

**Meaning:** Establishes the supported conclusions about MXene EIS fitting reliability on noisy quantum hardware.

## 6. Cross-algorithm VQE/VQA–QAOA comparison

Use the same measured EIS dataset, impedance model, and evaluation definitions. Report both ideal-algorithm baselines and hardware M0 for each algorithm. Compare **relative mitigation gain, fitting error, robustness, valid-result rate, and resource cost**, not absolute SSE alone. VQE uses continuous expectation mapping; QAOA uses discretized bitstring decoding and may have a nonzero discretization floor even in the ideal simulator.

## 7. Reproducible execution workflow

1. **Freeze inputs:** EIS dataset, model topology, parameter bounds, theta/angles, logical circuit, ISA, physical mapping, observables, decoding, and software versions.
2. **Record calibration:** Backend, timestamp, mapped-qubit/gate properties, and circuit durations.
3. **Collect fixed-ISA and transpilation data:** Preserve immutable raw IBM job IDs, inputs, and outputs.
4. **Run Aer scenarios:** Record the exact noise channel construction and controlled assumptions.
5. **Analyze propagation:** Reuse Study A modules; validate finite expectations, parameters, impedance, and SSE.
6. **Run mitigation only with authorization:** Check backend, quota, duplicate/UNKNOWN state, frozen inputs, and job provenance.
7. **Retrieve and validate:** Retain valid PUBs and document targeted recovery of invalid/missing slots.
8. **Export figures:** PNG 600 DPI, legible text, consistent color/labels. Prefer cross-QPU Results; retain backend diagnostics separately.
9. **Protect sources:** Never overwrite raw measurements or original IBM results during a plotting rebuild. Use distinct test/output destinations.

**Code reuse:** Study B should import shared HEA/EIS modules from `01_quantum_eis_fit/04_code/vqe/`, not duplicate `src/HEA_VQE` or `src/common`. The optional AI assistant belongs under Study B code and serves mitigation execution, monitoring, recovery, and provenance—not a separate scientific treatment.

## 8. Scientific significance and limitations

The central contribution is the **end-to-end evaluation of quantum error mitigation for a downstream inverse scientific problem**. It asks whether improving quantum measurements produces more reliable MXene EIS parameters and spectra, rather than assuming lower quantum error automatically means lower fitting error.

Expected contributions, subject to data validation, include cross-QPU repeatability, calibrated Aer noise sensitivity, quantum-to-EIS error propagation, factorial DD/TREX/ZNE interactions, and accuracy–cost comparisons.

Limitations include calibration drift, finite shots, incomplete hardware noise models, QPU-specific mappings, limited repetitions, nonlinear mapping, local Jacobian approximations, QAOA discretization and sampling, and ZNE variance amplification. **Do not present planned mitigation or QAOA hardware outcomes as completed results.**

## 9. References

The following are foundational publications and official implementation references. Check the installed Qiskit/IBM Runtime version against the relevant documentation.

1. Viola, L. & Lloyd, S. (1998). *Dynamical suppression of decoherence in two-state quantum systems.* Physical Review A 58, 2733. https://doi.org/10.1103/PhysRevA.58.2733
2. Viola, L., Knill, E. & Lloyd, S. (1999). *Dynamical Decoupling of Open Quantum Systems.* Physical Review Letters 82, 2417. https://doi.org/10.1103/PhysRevLett.82.2417
3. van den Berg, E., Minev, Z. K. & Temme, K. (2022). *Model-free readout-error mitigation for quantum expectation values.* Physical Review A 105, 032620. https://doi.org/10.1103/PhysRevA.105.032620
4. Temme, K., Bravyi, S. & Gambetta, J. M. (2017). *Error Mitigation for Short-Depth Quantum Circuits.* Physical Review Letters 119, 180509. https://doi.org/10.1103/PhysRevLett.119.180509
5. Li, Y. & Benjamin, S. C. (2017). *Efficient Variational Quantum Simulator Incorporating Active Error Minimization.* Physical Review X 7, 021050. https://doi.org/10.1103/PhysRevX.7.021050
6. Endo, S., Cai, Z., Benjamin, S. C. & Yuan, X. (2021). *Hybrid quantum-classical algorithms and quantum error mitigation.* Journal of the Physical Society of Japan 90, 032001. https://doi.org/10.7566/JPSJ.90.032001
7. Cai, Z. et al. (2023). *Quantum error mitigation.* Reviews of Modern Physics 95, 045005. https://doi.org/10.1103/RevModPhys.95.045005
8. IBM Quantum. *Error mitigation and suppression techniques.* https://quantum.cloud.ibm.com/docs/en/guides/error-mitigation-and-suppression-techniques
9. IBM Quantum. *Estimator noise management.* https://quantum.cloud.ibm.com/docs/en/guides/estimator-noise-management
10. Qiskit Aer. *Noise models.* https://qiskit.github.io/qiskit-aer/apidocs/aer_noise.html
11. IBM Quantum. *Combine error mitigation techniques.* https://quantum.cloud.ibm.com/docs/en/tutorials/combine-error-mitigation-techniques

---

**Reporting rule:** Cite actual job IDs, calibration snapshots, frozen manifests, and source CSV/JSON for all numerical findings. Literature mechanisms and mathematical examples are theoretical background, not measured results.
