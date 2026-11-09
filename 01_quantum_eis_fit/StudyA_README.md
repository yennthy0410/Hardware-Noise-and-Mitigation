# Study A — Quantum-Assisted MXene EIS Fitting: Classical, VQE/VQA, and QAOA

**Research area:** Quantum–classical inverse modeling of electrochemical impedance spectroscopy (EIS)  
**Dataset:** MXene EIS14 (measured frequency-dependent complex impedance)  
**Algorithms:** Classical optimization; continuous VQE/VQA with a hardware-efficient ansatz (HEA); discrete QAOA with QUBO encoding  
**Scope:** Offline fitting and algorithmic comparison. IBM QPU noise characterization, DD, TREX, ZNE, and hardware mitigation belong to **Study B**.

> **Reproducibility principle:** Preserve original EIS measurements, algorithm configurations, frozen parameters, and raw numerical results. Do not claim that an algorithm was run, converged, or outperformed another without matching output records.

## 1. Research background and motivation

Electrochemical impedance spectroscopy characterizes frequency-dependent transport, charge transfer, and non-ideal capacitive behavior in electrochemical systems. An equivalent circuit translates measured complex impedance into interpretable model parameters. Fitting is an inverse problem: multiple parameter combinations can yield similar spectra, and the loss landscape may be nonlinear, correlated, or ill-conditioned.

Study A investigates two quantum-assisted representations of the same seven-parameter MXene EIS fitting problem. **VQE/VQA** represents the parameters continuously through quantum expectation values, whereas **QAOA** searches a discretized parameter space encoded as a QUBO/Ising problem. A conventional classical fit provides a common reference. The goal is to assess numerical accuracy, representational limits, optimization behavior, and reproducibility—not to assert quantum advantage.

## 2. Research objectives

1. Establish a reproducible classical reference fit to the measured EIS dataset.
2. Construct a seven-parameter continuous HEA-based VQE/VQA fitting approach.
3. Formulate a 21-bit QUBO/Ising surrogate and evaluate a p=2 QAOA approach.
4. Compare reconstructed impedance spectra, residuals, SSE/RMSE, and all seven fitted parameters using consistent definitions.
5. Diagnose optimizer variability, QAOA energy landscapes, discretization limits, and surrogate approximation fidelity.
6. Produce publication-quality, independently reproducible figures and structured CSV/JSON results.
7. Provide frozen circuit/parameter inputs for Study B without changing Study A's scientific outputs.

## 3. Research questions

| ID | Research question | Evaluation evidence |
|---|---|---|
| RQ1 | How accurately does a classical optimizer reproduce measured MXene EIS? | Nyquist, Bode, residuals, SSE/RMSE |
| RQ2 | Can a seven-qubit continuous HEA-based VQE/VQA reproduce the measured EIS spectrum? | Optimized expectation mapping, fitted parameters, spectra |
| RQ3 | What fitting accuracy is achievable with a three-bit-per-parameter QAOA/QUBO representation? | Decoded bitstrings, p=2 results, discretization analysis |
| RQ4 | How do continuous and discrete quantum-assisted approaches differ from classical fitting? | Matched metrics and seven-parameter comparison |
| RQ5 | Which limitations arise from optimization, parameter discretization, and surrogate-model error? | Multi-seed results, landscapes, surrogate diagnostics |
| RQ6 | Which circuit, mapping, and optimized parameters must be frozen for reproducible hardware experiments? | Circuit QPY/QASM, angle vectors, encoding and configuration manifests |

## 4. Scientific and mathematical framework

### 4.1. Measured complex impedance

At each measured frequency \(f_k\), define the experimental impedance

\[
Z_k^{\mathrm{data}}=Z_k'+jZ_k'',\qquad \omega_k=2\pi f_k.
\]

Use the measured frequencies and complex impedance without fabricating additional points. The Nyquist convention (e.g., \(-\operatorname{Im}Z\) on the vertical axis) must be applied consistently.

### 4.2. Seven-parameter equivalent circuit

The fitted parameter vector is

\[
\mathbf p=(R_s,L,R_{ct},Q_1,n_1,Q_2,n_2).
\]

The components represent series resistance \(R_s\), inductance \(L\), charge-transfer resistance \(R_{ct}\), and two constant-phase elements (CPEs). A CPE has impedance

\[
Z_{\mathrm{CPE}}(\omega;Q,n)=\frac{1}{Q(j\omega)^n}.
\]

**Critical model check:** The exact connectivity and equation implemented in Study A's `impedance_model.py` are authoritative. A shorthand such as `Rs–L–(Rct || CPE1)–CPE2` must not be interpreted as a substitute for the actual code; verify whether the second CPE is in series, parallel, or part of another branch before publishing the explicit full-circuit equation. The same function and parameter bounds must be used by all three fitting methods.

### 4.3. Objective functions and accuracy metrics

For a basic unweighted complex residual,

\[
r_k(\mathbf p)=Z_k^{\mathrm{data}}-Z(f_k;\mathbf p),\qquad
\mathrm{SSE}(\mathbf p)=\sum_{k=1}^{N}|r_k(\mathbf p)|^2,
\]

\[
\mathrm{RMSE}(\mathbf p)=\sqrt{\frac{1}{N}\sum_{k=1}^{N}|r_k(\mathbf p)|^2}.
\]

The implemented optimization objective may instead use frequency weights, normalized residuals, or a logarithmic transform. **Do not equate the optimizer's transformed loss with the reported physical SSE.** Recompute comparison SSE/RMSE from the same measured dataset and fitted spectra. State whether RMSE is defined per complex frequency point or per real-valued component.

### 4.4. Classical reference fitting

The classical fit solves a bounded nonlinear inverse problem:

\[
\mathbf p_{\mathrm{classical}}^*=\arg\min_{\mathbf p\in\mathcal P}\mathcal L(\mathbf p).
\]

Document the optimizer, initial guesses, parameter bounds, convergence settings, weighting, and termination criteria from the actual script. Classical fitting is the **numerical reference method**, not a noise-free quantum circuit or proof of the global optimum.

### 4.5. Continuous VQE/VQA fitting

The continuous approach uses a **seven-qubit hardware-efficient ansatz (HEA)** with three parameterized layers and **21 variational angles**. At a fixed angle vector \(\boldsymbol\theta\), the circuit generates measured or simulated Pauli expectation values:

\[
\mathbf e(\boldsymbol\theta)=
\big(\langle Z_0\rangle,\ldots,\langle Z_6\rangle\big).
\]

A frozen mapping \(g\) transforms them into bounded physical parameters:

\[
\mathbf p(\boldsymbol\theta)=g\!\left(\mathbf e(\boldsymbol\theta)\right),\qquad
\boldsymbol\theta^*=\arg\min_{\boldsymbol\theta}\mathcal L\big(g(\mathbf e(\boldsymbol\theta))\big).
\]

Study A has used COBYLA and multiple optimizer seeds (e.g., 13–17) in prior configurations. Verify the exact seeds, restart counts, low-frequency weighting, and final selected run from the saved configuration rather than treating historical settings as universally final. This is a **variational quantum algorithm**; the word VQE is used as the project's method label, even though the target objective is EIS fitting rather than a molecular ground-state energy.

**Important:** Seven inferred EIS parameters are not the same as 21 variational circuit angles. Export and preserve both separately.

### 4.6. Discrete QAOA/QUBO fitting

QAOA uses **three binary variables per EIS parameter**, giving **21 binary variables / 21 qubits**. For parameter \(p_i\), a three-bit code \(b_i\in\{0,\ldots,7\}\) maps to a prescribed parameter grid, for example

\[
p_i(b_i)=p_{i,\min}+\frac{b_i}{7}(p_{i,\max}-p_{i,\min}),
\]

**only if this linear decoding matches the implementation**. Logarithmic parameter decoding must be described using the actual code if employed. This representation introduces a finite **discretization floor** even with ideal quantum optimization.

A quadratic surrogate is written

\[
E(\mathbf x)=c+\sum_i h_i x_i+\sum_{i<j}J_{ij}x_ix_j,
\qquad \mathbf x\in\{0,1\}^{21}.
\]

Its QUBO coefficients can be mapped to an Ising cost Hamiltonian. A p=2 QAOA circuit alternates two cost and two mixer layers:

\[
|\psi(\boldsymbol\gamma,\boldsymbol\beta)\rangle=
\prod_{\ell=1}^{2}\left[e^{-i\beta_\ell H_M}e^{-i\gamma_\ell H_C}\right]|+\rangle^{\otimes21}.
\]

The four QAOA angles are \((\gamma_1,\beta_1,\gamma_2,\beta_2)\). The **surrogate energy** is not automatically the true EIS SSE. If a surrogate was trained on `log10(weighted complex loss)`, its predictions must not be labeled as raw SSE without a validated transformation. Report surrogate validation error and independently recompute the true EIS fitting metrics after bitstring decoding.

### 4.7. QAOA optimization landscapes and fidelity

Coarse and refined p=2 landscapes show conditional two-dimensional slices of the four-angle objective; the other two angles must be held fixed at explicitly recorded values. Refined landscapes should be centered on promising coarse regions, with axis limits and grid resolution documented. Use separate diagnostics for the QUBO/Ising landscape, surrogate fidelity, and decoded true EIS SSE. Leading zeroes in 21-bit bitstrings must be preserved (read as strings and validate length) to avoid corrupt decoding.

### 4.8. Fair comparison of the three methods

All methods must use the same measured EIS data, equivalent-circuit equation, units, evaluation frequency grid, and reported metric definitions. Show:

- **Classical** — numerical reference fit.
- **VQE/VQA (cont.)** — continuous expectation-to-parameter mapping.
- **QAOA (disc.)** — discrete bitstring-to-parameter decoding.

An algorithm may have lower SSE but larger drift in selected parameters; fit quality and parameter identifiability should be discussed separately. A single result does not establish quantum computational advantage or statistically significant superiority.

## 5. Project organization

```text
01_quantum_eis_fit/
├── 01_vqe/
│   ├── output/
│   └── figure/                 # If present in the current project
├── 02_qaoa/
│   ├── output/
│   └── figure/                 # If present
├── 03_compare_vqe_qaoa/
│   ├── output/
│   └── figure/
└── 04_code/
    ├── vqe/
    ├── qaoa/
    └── compare_vqe_qaoa.py
```

The dataset is normally located at `data/EIS14/EIS14.csv` (some project revisions use `data/EIS14.csv`). Resolve the actual path rather than creating a second copy. The existing Study A code is the authoritative source for the model, HEA circuit, QAOA encoding, and plotting.

### 5.1. `01_vqe` — Continuous quantum-assisted fitting

**Purpose:** Test whether a continuously parameterized quantum circuit can infer EIS model parameters.  
**Method:** Load measured EIS; build HEA; optimize angles on a simulator; estimate seven Pauli expectations; apply the fixed parameter mapping; calculate fitted impedance and errors; repeat with documented seeds.  
**Outputs:** Fitted spectrum, optimized circuit angles, mapped parameters, optimization trace, metrics, and publication figures.  
**Meaning:** Quantifies continuous representation quality, optimization stability, and fitting accuracy.

### 5.2. `02_qaoa` — Discrete quantum-assisted fitting

**Purpose:** Test the same inverse problem using a finite binary parameter lattice.  
**Method:** Encode seven parameters with three bits each; build and validate the QUBO surrogate; construct the p=2 QAOA cost/mixer circuit; optimize four angles; sample or evaluate bitstrings; decode the selected candidate; compute **true** EIS SSE/RMSE.  
**Outputs:** Encoding and surrogate records, QAOA angles, bitstring probabilities, selected parameter vector, reconstructed spectrum, landscape/fidelity diagnostics.  
**Meaning:** Quantifies discrete search performance, surrogate mismatch, and parameter resolution limitations.

### 5.3. `03_compare_vqe_qaoa` — Cross-method evaluation

**Purpose:** Compare all three approaches under matched evaluation rules.  
**Method:** Validate source CSVs, align frequencies, recompute complex residuals and metrics, and plot seven-parameter differences.  
**Outputs:** Comparison CSV/JSON and standardized PNG figures.  
**Meaning:** Separates differences due to representation and optimization from differences due to plotting or metric definitions.

### 5.4. `04_code` — Scientific source of truth

Keep algorithm-specific modules in `04_code/vqe` and `04_code/qaoa`. Avoid duplicate HEA or impedance-model implementations in Study B. Study B may import Study A modules, but must not silently alter their scientific definitions.

## 6. Recommended figures and tables

| Figure / table | Scientific question answered |
|---|---|
| Nyquist comparison | How closely do the three methods reconstruct complex impedance? |
| Bode magnitude and phase | Where do errors occur across the frequency range? |
| Real/imaginary residuals | Are there systematic fitting errors or frequency-dependent biases? |
| SSE and RMSE comparison | What is the overall fit quality under identical definitions? |
| Seven-parameter comparison | Which inferred physical parameters differ between methods? |
| Relative parameter deviation from Classical | How far do quantum-assisted estimates depart from the reference fit? |
| VQE optimizer convergence / seed variation | How stable is continuous variational optimization? |
| QAOA coarse/refined p=2 landscapes | What regions of the variational energy surface are explored? |
| QUBO surrogate fidelity and discretization | Does the surrogate and finite grid preserve the true EIS objective? |

**Publication template:** white background; strong but unobtrusive axes; consistent font; legends inside axes when they do not overlap data; no clipped labels; independent PNG at **600 DPI**. Use square figures for Nyquist/Bode/residual plots as appropriate, but allow wider tables and dense diagnostics to avoid overlapping text. Keep method labels exactly **Classical**, **VQE/VQA (cont.)**, and **QAOA (disc.)** across all comparison figures. Do not put `EIS14` in plot titles unless required by the manuscript; dataset names may remain in filenames and provenance.

## 7. Reproducible execution and rebuilding

1. **Verify inputs:** measured EIS CSV, frequency units, impedance sign convention, equivalent-circuit equation, bounds, and initial parameters.
2. **Freeze configurations:** optimizer seeds, loss weights, circuit depths, parameter mapping, QUBO encoding, surrogate training settings, and software versions.
3. **Run or inspect Classical fit:** preserve its parameter CSV and reconstructed spectrum.
4. **Run or inspect VQE/VQA:** preserve the 21-angle vector, seven mapped parameters, optimizer logs, and fitted spectrum.
5. **Run or inspect QAOA:** preserve the QUBO coefficients, four p=2 angles, bitstrings, decoded parameters, and surrogate diagnostics.
6. **Validate comparisons:** align frequencies and independently recompute SSE/RMSE from reconstructed impedance.
7. **Rebuild figures offline:** prefer scripts that read existing CSV/JSON rather than rerunning optimization. Save new plots to a separate test folder when verifying code changes.
8. **Archive provenance:** record original file hashes, timestamps, software versions, and source-to-figure mappings.

### 7.1. Example offline comparison command

From the project root `MXene QPU Hardware`:

```powershell
python "01_quantum_eis_fit/04_code/compare_vqe_qaoa.py"
```

This command is valid **only when the file exists at this location and its required CSV inputs are present**. Check the script's current input/output configuration before running; an existing output filename may be overwritten. Do not run IBM QPU submission commands to rebuild Study A plots.

### 7.2. Essential input files to verify

```text
01_quantum_eis_fit/01_vqe/output/
    classical_fitted_spectrum.csv
    quantum_fitted_spectrum.csv
    classical_fit_parameters.csv
    vqe_fit_parameters.csv

01_quantum_eis_fit/02_qaoa/output/
    qaoa_simulator_fitted_spectrum.csv
    [QAOA parameters, QUBO and p=2 diagnostics as actually saved]
```

**No numerical result should be inferred from a missing file.** Verify actual headers and units before processing.

## 8. Interpretation and scientific significance

Study A is a controlled, primarily simulation-based investigation of how continuous and discrete quantum representations solve a nonlinear EIS inverse problem. Its scientific value lies in: (i) a common classical reference; (ii) interpretable seven-parameter outputs; (iii) explicit continuous-vs-discrete constraints; (iv) independent verification of fit quality; and (v) frozen inputs for a subsequent hardware-noise study.

**Study B is a distinct follow-on experiment:** it examines fixed/unfixed ISA execution, IBM hardware noise, Qiskit Aer noise mechanisms, quantum-to-EIS error propagation, and DD/TREX/ZNE mitigation. Do not present Study A simulator performance as hardware performance.

### 8.1. Limitations

- Classical optimizers and variational quantum optimizers can converge to local minima.
- EIS parameters may be correlated or poorly identifiable despite a visually good fit.
- A seven-qubit HEA does not guarantee an expressive or trainable parameter map.
- Three-bit QAOA encoding imposes quantization error; a QUBO surrogate may misrank true EIS solutions.
- Conditional two-dimensional QAOA landscapes do not fully describe the four-dimensional objective.
- Finite simulation shots (if used), optimizer seeds, and numerical tolerances affect repeatability.
- No quantum speedup or hardware advantage is demonstrated solely by these comparisons.

## 9. References

The following sources support the general methods. Project-specific EIS circuit topology, objective weights, QUBO training, and output conventions must additionally be cited from the actual implementation and experiment logs.

1. Barsoukov, E. & Macdonald, J. R. (eds.). *Impedance Spectroscopy: Theory, Experiment, and Applications*, 3rd ed. Wiley, 2018. https://doi.org/10.1002/9781119381860
2. Lasia, A. *Electrochemical Impedance Spectroscopy and its Applications*. Springer, 2014. https://doi.org/10.1007/978-1-4614-8933-7
3. Cerezo, M. et al. *Variational quantum algorithms*. Nature Reviews Physics **3**, 625–644 (2021). https://doi.org/10.1038/s42254-021-00348-9
4. Kandala, A. et al. *Hardware-efficient variational quantum eigensolver for small molecules and quantum magnets*. Nature **549**, 242–246 (2017). https://doi.org/10.1038/nature23879
5. Peruzzo, A. et al. *A variational eigenvalue solver on a photonic quantum processor*. Nature Communications **5**, 4213 (2014). https://doi.org/10.1038/ncomms5213
6. Farhi, E., Goldstone, J. & Gutmann, S. *A Quantum Approximate Optimization Algorithm*. arXiv:1411.4028 (2014). https://arxiv.org/abs/1411.4028
7. Lucas, A. *Ising formulations of many NP problems*. Frontiers in Physics **2**, 5 (2014). https://doi.org/10.3389/fphy.2014.00005
8. Qiskit documentation: Quantum circuits and transpilation. https://quantum.cloud.ibm.com/docs/

## 10. Reporting checklist

- [ ] The measured dataset, units, and frequency order are documented.
- [ ] The exact impedance model and CPE connectivity match Study A code.
- [ ] Classical, VQE/VQA, and QAOA use matched reporting metrics.
- [ ] The 21 VQE angles and seven fitted EIS parameters are stored separately.
- [ ] QAOA bitstrings retain all 21 binary digits and decode correctly.
- [ ] Surrogate energy is never mislabeled as physical EIS SSE.
- [ ] All figures use consistent method labels and verified 600-DPI export.
- [ ] Figures are traceable to source CSV/JSON and code versions.
- [ ] Missing or incomplete results are clearly marked, not filled with invented data.
- [ ] No claims of quantum advantage or QPU mitigation appear without appropriate evidence.
