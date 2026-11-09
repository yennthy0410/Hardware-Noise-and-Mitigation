"""
QAOA-QUBO Experiment 00
Deterministic p=1 QAOA Landscape Diagnostic

Goal
----
Diagnose whether EIS14 p=1 is limited by:
    A) optimizer initialization / seed / insufficient search, or
    B) the p=1 QAOA ansatz + mean-energy objective itself.

The script scans the SAME scaled parameterization used by qaoa_optimizer.py:

    tau = gamma * std(H_C - C)

with:
    tau  in [-gamma_window_pi*pi, +gamma_window_pi*pi]
    beta in [0, pi]

For every grid point it evaluates the exact ideal-state expectation:

    <H_C>(gamma, beta)

Then it takes several well-separated low-energy grid points and refines them
with bound-constrained COBYLA.

This is deterministic:
    - no optimizer seed is used for the landscape grid
    - no TRUE EIS loss / SSE / RMSE is used
    - no shot sampling is used to select the best angles

Interpretation
--------------
If the deterministic landscape scan + local refinement returns essentially
the SAME minimum as the existing qaoa_optimizer.py result, increasing seed,
screen_samples, restarts, or local_maxiter is unlikely to solve p=1.

If it finds a clearly LOWER expectation, the previous optimizer probably
missed a better p=1 basin.

Expected current project structure
----------------------------------
scripts/
    qaoa_circuit.py
    qaoa_optimizer.py
    diagnose_qaoa_p1_landscape.py

results/<DATASET>/
    ising/ising_model.json
    qaoa/optimizer/p1/qaoa_optimal_parameters.json

Outputs
-------
results/<DATASET>/qaoa/p1_landscape/

    coarse_landscape.npy
    p1_landscape_grid.csv
    p1_landscape_heatmap.png
    p1_landscape_heatmap.pdf
    p1_local_refinements.csv
    p1_landscape_report.json

The .npy file is checkpointed after every completed tau row, so a stopped
scan can resume without recomputing completed rows.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from scipy.optimize import minimize
except ImportError as error:
    raise ImportError(
        "SciPy is required.\n"
        "Install with:\n"
        "    pip install scipy"
    ) from error

from qaoa_circuit import load_ising_coefficients


# ============================================================
# Paths
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
EXPERIMENT_DIR = SCRIPT_DIR.parent
RESULTS_ROOT = EXPERIMENT_DIR / "results"


def output_paths(dataset: str) -> dict:
    root = (
        RESULTS_ROOT
        / dataset.upper()
        / "qaoa"
        / "p1_landscape"
    )

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return {
        "root":
            root,

        "matrix":
            root
            / "coarse_landscape.npy",

        "checkpoint_meta":
            root
            / "coarse_landscape_checkpoint.json",

        "grid_csv":
            root
            / "p1_landscape_grid.csv",

        "heatmap_png":
            root
            / "p1_landscape_heatmap.png",

        "heatmap_pdf":
            root
            / "p1_landscape_heatmap.pdf",

        "refinements":
            root
            / "p1_local_refinements.csv",

        "report":
            root
            / "p1_landscape_report.json",
    }


# ============================================================
# Canonical bit order
# ============================================================

def canonical_bitstring_to_basis_index(
    bitstring: str,
) -> int:
    value = 0

    for qubit, character in enumerate(
        str(
            bitstring
        )
    ):
        if character == "1":
            value |= (
                1
                << qubit
            )

        elif character != "0":
            raise ValueError(
                f"Invalid bitstring: {bitstring}"
            )

    return int(
        value
    )


def basis_index_to_canonical_bitstring(
    index: int,
    n_qubits: int,
) -> str:
    return "".join(
        str(
            (
                int(
                    index
                )
                >> qubit
            )
            & 1
        )
        for qubit in range(
            n_qubits
        )
    )


def hamming_distance(
    first: str,
    second: str,
) -> int:
    if len(first) != len(second):
        raise ValueError(
            "Bitstrings have different lengths."
        )

    return int(
        sum(
            a != b
            for a, b in zip(
                first,
                second,
            )
        )
    )


# ============================================================
# Hamiltonian / exact statevector
# ============================================================

def load_problem(
    dataset: str,
):
    (
        model,
        h,
        j_upper,
    ) = load_ising_coefficients(
        dataset
    )

    h = np.asarray(
        h,
        dtype=np.float64,
    )

    j_upper = np.asarray(
        j_upper,
        dtype=np.float64,
    )

    n_qubits = len(
        h
    )

    constant = float(
        model.get(
            "ising_constant",
            0.0,
        )
    )

    exact = model.get(
        "exact_ising_minimum",
        {},
    )

    exact_bitstring = str(
        exact.get(
            "bitstring",
            "",
        )
    )

    exact_energy = (
        float(
            exact[
                "ising_energy"
            ]
        )
        if exact.get(
            "ising_energy"
        )
        is not None
        else None
    )

    return (
        model,
        h,
        j_upper,
        n_qubits,
        constant,
        exact_bitstring,
        exact_energy,
    )


def precompute_basis_energies(
    n_qubits: int,
    constant: float,
    h: np.ndarray,
    j_upper: np.ndarray,
) -> np.ndarray:
    """
    Compute exact diagonal Ising energies for all 2^n basis states.
    """

    number_of_states = (
        1
        << n_qubits
    )

    print()
    print(
        "Precomputing exact Ising basis energies..."
    )

    print(
        f"  states = {number_of_states:,}"
    )

    start = time.perf_counter()

    indices = np.arange(
        number_of_states,
        dtype=np.uint32,
    )

    spins = np.empty(
        (
            n_qubits,
            number_of_states,
        ),
        dtype=np.int8,
    )

    for qubit in range(
        n_qubits
    ):
        bits = (
            (
                indices
                >> qubit
            )
            & 1
        ).astype(
            np.int8
        )

        spins[
            qubit
        ] = (
            1
            - 2
            * bits
        )

    energies = np.full(
        number_of_states,
        constant,
        dtype=np.float64,
    )

    for i in range(
        n_qubits
    ):
        coefficient = float(
            h[
                i
            ]
        )

        if coefficient != 0.0:
            energies += (
                coefficient
                * spins[
                    i
                ]
            )

    nonzero_j = 0

    for i in range(
        n_qubits
    ):
        for j in range(
            i + 1,
            n_qubits,
        ):
            coefficient = float(
                j_upper[
                    i,
                    j
                ]
            )

            if coefficient == 0.0:
                continue

            nonzero_j += 1

            energies += (
                coefficient
                * spins[
                    i
                ]
                * spins[
                    j
                ]
            )

    print(
        f"  nonzero J terms = {nonzero_j}"
    )

    print(
        f"  energy range    = "
        f"[{np.min(energies):.12g}, "
        f"{np.max(energies):.12g}]"
    )

    print(
        f"  elapsed         = "
        f"{time.perf_counter() - start:.2f} s"
    )

    return energies


def compute_energy_scale(
    basis_energies: np.ndarray,
    constant: float,
) -> float:
    """
    Same scaling convention used by the upgraded qaoa_optimizer.py.
    """

    cost = (
        basis_energies
        - float(
            constant
        )
    )

    scale = float(
        np.std(
            cost
        )
    )

    if (
        not np.isfinite(
            scale
        )
        or scale <= 1e-14
    ):
        scale = float(
            np.max(
                np.abs(
                    cost
                )
            )
        )

    if (
        not np.isfinite(
            scale
        )
        or scale <= 1e-14
    ):
        raise RuntimeError(
            "Invalid Ising energy scale."
        )

    return scale


def apply_x_mixer_inplace(
    state: np.ndarray,
    beta: float,
    n_qubits: int,
) -> None:
    cosine = math.cos(
        float(
            beta
        )
    )

    minus_i_sine = (
        -1j
        * math.sin(
            float(
                beta
            )
        )
    )

    for qubit in range(
        n_qubits
    ):
        half = (
            1
            << qubit
        )

        full = (
            half
            << 1
        )

        view = state.reshape(
            -1,
            full,
        )

        a0 = (
            view[
                :,
                :half
            ].copy()
        )

        a1 = (
            view[
                :,
                half:
            ].copy()
        )

        view[
            :,
            :half
        ] = (
            cosine
            * a0
            +
            minus_i_sine
            * a1
        )

        view[
            :,
            half:
        ] = (
            minus_i_sine
            * a0
            +
            cosine
            * a1
        )


def p1_statevector(
    tau: float,
    beta: float,
    energy_scale: float,
    basis_energies: np.ndarray,
    constant: float,
    n_qubits: int,
) -> np.ndarray:
    """
    p=1:
        tau = gamma * energy_scale
        gamma = tau / energy_scale
    """

    gamma = (
        float(
            tau
        )
        / float(
            energy_scale
        )
    )

    number_of_states = len(
        basis_energies
    )

    state = np.full(
        number_of_states,
        1.0
        / math.sqrt(
            number_of_states
        ),
        dtype=np.complex128,
    )

    cost_energies = (
        basis_energies
        - float(
            constant
        )
    )

    state *= np.exp(
        -1j
        * gamma
        * cost_energies
    )

    apply_x_mixer_inplace(
        state=state,
        beta=float(
            beta
        ),
        n_qubits=n_qubits,
    )

    return state


def p1_expectation(
    tau: float,
    beta: float,
    energy_scale: float,
    basis_energies: np.ndarray,
    constant: float,
    n_qubits: int,
) -> float:
    state = p1_statevector(
        tau=tau,
        beta=beta,
        energy_scale=energy_scale,
        basis_energies=basis_energies,
        constant=constant,
        n_qubits=n_qubits,
    )

    probability = (
        state.real
        * state.real
        +
        state.imag
        * state.imag
    )

    return float(
        np.dot(
            probability,
            basis_energies,
        )
    )


# ============================================================
# Previous optimizer result
# ============================================================

def load_previous_optimizer(
    dataset: str,
):
    candidates = [
        (
            RESULTS_ROOT
            / dataset
            / "qaoa"
            / "optimizer"
            / "p1"
            / "qaoa_optimal_parameters.json"
        ),
        (
            RESULTS_ROOT
            / dataset
            / "qaoa"
            / "optimizer"
            / "qaoa_optimal_parameters.json"
        ),
    ]

    for path in candidates:
        if not path.exists():
            continue

        try:
            data = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

            gamma = float(
                data[
                    "optimal_gamma"
                ][
                    0
                ]
            )

            beta = float(
                data[
                    "optimal_beta"
                ][
                    0
                ]
            )

            expectation = float(
                data[
                    "optimal_expectation_energy"
                ]
            )

            return {
                "path":
                    path,

                "gamma":
                    gamma,

                "beta":
                    beta,

                "expectation":
                    expectation,
            }

        except Exception:
            continue

    return None


# ============================================================
# Coarse deterministic landscape scan
# ============================================================

def checkpoint_is_compatible(
    metadata: dict,
    dataset: str,
    grid_size: int,
    gamma_window_pi: float,
    energy_scale: float,
) -> bool:
    try:
        return (
            str(
                metadata[
                    "dataset"
                ]
            ).upper()
            == dataset.upper()
            and int(
                metadata[
                    "grid_size"
                ]
            )
            == int(
                grid_size
            )
            and np.isclose(
                float(
                    metadata[
                        "gamma_window_pi"
                    ]
                ),
                float(
                    gamma_window_pi
                ),
                rtol=0.0,
                atol=1e-14,
            )
            and np.isclose(
                float(
                    metadata[
                        "energy_scale"
                    ]
                ),
                float(
                    energy_scale
                ),
                rtol=1e-12,
                atol=1e-14,
            )
        )

    except Exception:
        return False


def scan_landscape(
    dataset: str,
    grid_size: int,
    gamma_window_pi: float,
    energy_scale: float,
    basis_energies: np.ndarray,
    constant: float,
    n_qubits: int,
    paths: dict,
    resume: bool,
):
    tau_limit = (
        float(
            gamma_window_pi
        )
        * np.pi
    )

    tau_values = np.linspace(
        -tau_limit,
        tau_limit,
        int(
            grid_size
        ),
    )

    beta_values = np.linspace(
        0.0,
        np.pi,
        int(
            grid_size
        ),
    )

    matrix = np.full(
        (
            grid_size,
            grid_size,
        ),
        np.nan,
        dtype=np.float64,
    )

    resumed = False

    if (
        resume
        and paths[
            "matrix"
        ].exists()
        and paths[
            "checkpoint_meta"
        ].exists()
    ):
        try:
            metadata = json.loads(
                paths[
                    "checkpoint_meta"
                ].read_text(
                    encoding="utf-8"
                )
            )

            saved_matrix = np.load(
                paths[
                    "matrix"
                ]
            )

            if (
                saved_matrix.shape
                == matrix.shape
                and checkpoint_is_compatible(
                    metadata,
                    dataset,
                    grid_size,
                    gamma_window_pi,
                    energy_scale,
                )
            ):
                matrix = np.asarray(
                    saved_matrix,
                    dtype=np.float64,
                )

                resumed = True

        except Exception:
            resumed = False

    metadata = {
        "dataset":
            dataset,

        "p":
            1,

        "grid_size":
            int(
                grid_size
            ),

        "gamma_window_pi":
            float(
                gamma_window_pi
            ),

        "energy_scale":
            float(
                energy_scale
            ),

        "tau_min":
            float(
                tau_values[
                    0
                ]
            ),

        "tau_max":
            float(
                tau_values[
                    -1
                ]
            ),

        "beta_min":
            float(
                beta_values[
                    0
                ]
            ),

        "beta_max":
            float(
                beta_values[
                    -1
                ]
            ),
    }

    paths[
        "checkpoint_meta"
    ].write_text(
        json.dumps(
            metadata,
            indent=2,
        ),
        encoding="utf-8",
    )

    if resumed:
        completed_rows = int(
            np.sum(
                ~np.isnan(
                    matrix
                ).any(
                    axis=1
                )
            )
        )

        print()
        print(
            f"Resuming compatible checkpoint: "
            f"{completed_rows}/{grid_size} rows complete."
        )

    print()
    print(
        "Deterministic p=1 landscape scan"
    )

    print(
        f"  grid          = "
        f"{grid_size} x {grid_size} "
        f"= {grid_size * grid_size:,} points"
    )

    print(
        f"  tau/pi range  = "
        f"[-{gamma_window_pi}, "
        f"+{gamma_window_pi}]"
    )

    print(
        "  beta/pi range = [0, 1]"
    )

    scan_start = time.perf_counter()
    newly_completed = 0

    for tau_index, tau in enumerate(
        tau_values
    ):
        if not np.isnan(
            matrix[
                tau_index
            ]
        ).any():
            continue

        row_start = time.perf_counter()

        for beta_index, beta in enumerate(
            beta_values
        ):
            if np.isfinite(
                matrix[
                    tau_index,
                    beta_index
                ]
            ):
                continue

            matrix[
                tau_index,
                beta_index
            ] = p1_expectation(
                tau=float(
                    tau
                ),
                beta=float(
                    beta
                ),
                energy_scale=energy_scale,
                basis_energies=basis_energies,
                constant=constant,
                n_qubits=n_qubits,
            )

        newly_completed += 1

        # Crash-safe checkpoint after every full tau row.
        np.save(
            paths[
                "matrix"
            ],
            matrix,
        )

        row_seconds = (
            time.perf_counter()
            - row_start
        )

        completed_rows = int(
            np.sum(
                ~np.isnan(
                    matrix
                ).any(
                    axis=1
                )
            )
        )

        remaining_rows = (
            grid_size
            - completed_rows
        )

        print(
            f"  row {completed_rows:>3d}/{grid_size}"
            f" | tau/pi={tau / np.pi:+.5f}"
            f" | row={row_seconds:.1f}s"
            f" | ETA~{remaining_rows * row_seconds / 60.0:.1f} min"
        )

    elapsed = (
        time.perf_counter()
        - scan_start
    )

    print()
    print(
        f"Landscape scan finished in "
        f"{elapsed / 60.0:.2f} min."
    )

    return (
        tau_values,
        beta_values,
        matrix,
        elapsed,
        newly_completed,
    )


# ============================================================
# Select separated low-energy starting points
# ============================================================

def select_grid_starts(
    matrix: np.ndarray,
    tau_values: np.ndarray,
    beta_values: np.ndarray,
    count: int,
    min_grid_distance: float,
):
    flat_order = np.argsort(
        matrix,
        axis=None,
    )

    selected = []

    shape = matrix.shape

    for flat_index in flat_order:
        i, j = np.unravel_index(
            int(
                flat_index
            ),
            shape,
        )

        if not np.isfinite(
            matrix[
                i,
                j
            ]
        ):
            continue

        candidate = np.asarray(
            [
                float(
                    tau_values[
                        i
                    ]
                ),
                float(
                    beta_values[
                        j
                    ]
                ),
            ],
            dtype=float,
        )

        grid_coordinate = np.asarray(
            [
                float(
                    i
                ),
                float(
                    j
                ),
            ]
        )

        if not selected:
            selected.append(
                {
                    "tau_index":
                        int(
                            i
                        ),

                    "beta_index":
                        int(
                            j
                        ),

                    "tau":
                        float(
                            candidate[
                                0
                            ]
                        ),

                    "beta":
                        float(
                            candidate[
                                1
                            ]
                        ),

                    "energy":
                        float(
                            matrix[
                                i,
                                j
                            ]
                        ),

                    "grid_coordinate":
                        grid_coordinate,
                }
            )

        else:
            distances = [
                float(
                    np.linalg.norm(
                        grid_coordinate
                        - item[
                            "grid_coordinate"
                        ]
                    )
                )
                for item in selected
            ]

            if min(
                distances
            ) >= float(
                min_grid_distance
            ):
                selected.append(
                    {
                        "tau_index":
                            int(
                                i
                            ),

                        "beta_index":
                            int(
                                j
                            ),

                        "tau":
                            float(
                                candidate[
                                    0
                                ]
                            ),

                        "beta":
                            float(
                                candidate[
                                    1
                                ]
                            ),

                        "energy":
                            float(
                                matrix[
                                    i,
                                    j
                                ]
                            ),

                        "grid_coordinate":
                            grid_coordinate,
                    }
                )

        if len(
            selected
        ) >= int(
            count
        ):
            break

    return selected


# ============================================================
# Deterministic local refinement
# ============================================================

def refine_starts(
    starts,
    gamma_window_pi: float,
    local_maxiter: int,
    energy_scale: float,
    basis_energies: np.ndarray,
    constant: float,
    n_qubits: int,
):
    tau_limit = (
        float(
            gamma_window_pi
        )
        * np.pi
    )

    rows = []

    best_energy = np.inf
    best_tau = None
    best_beta = None

    # COBYLA constraints:
    #   -tau_limit <= tau <= tau_limit
    #   0 <= beta <= pi
    constraints = [
        {
            "type":
                "ineq",

            "fun":
                lambda x:
                    float(
                        x[
                            0
                        ]
                        + tau_limit
                    ),
        },
        {
            "type":
                "ineq",

            "fun":
                lambda x:
                    float(
                        tau_limit
                        - x[
                            0
                        ]
                    ),
        },
        {
            "type":
                "ineq",

            "fun":
                lambda x:
                    float(
                        x[
                            1
                        ]
                    ),
        },
        {
            "type":
                "ineq",

            "fun":
                lambda x:
                    float(
                        np.pi
                        - x[
                            1
                        ]
                    ),
        },
    ]

    print()
    print(
        "Local refinement from separated landscape minima:"
    )

    for start_id, start in enumerate(
        starts,
        start=1,
    ):
        x0 = np.asarray(
            [
                start[
                    "tau"
                ],
                start[
                    "beta"
                ],
            ],
            dtype=float,
        )

        local_best = {
            "energy":
                float(
                    start[
                        "energy"
                    ]
                ),

            "x":
                x0.copy(),
        }

        evaluation_count = 0

        def objective(
            x,
        ):
            nonlocal evaluation_count

            evaluation_count += 1

            tau = float(
                np.clip(
                    x[
                        0
                    ],
                    -tau_limit,
                    tau_limit,
                )
            )

            beta = float(
                np.clip(
                    x[
                        1
                    ],
                    0.0,
                    np.pi,
                )
            )

            value = p1_expectation(
                tau=tau,
                beta=beta,
                energy_scale=energy_scale,
                basis_energies=basis_energies,
                constant=constant,
                n_qubits=n_qubits,
            )

            if (
                -tau_limit
                <= x[
                    0
                ]
                <= tau_limit
                and 0.0
                <= x[
                    1
                ]
                <= np.pi
                and value
                < local_best[
                    "energy"
                ]
            ):
                local_best[
                    "energy"
                ] = float(
                    value
                )

                local_best[
                    "x"
                ] = np.asarray(
                    [
                        tau,
                        beta,
                    ],
                    dtype=float,
                )

            return float(
                value
            )

        start_time = time.perf_counter()

        result = minimize(
            fun=objective,
            x0=x0,
            method="COBYLA",
            constraints=constraints,
            tol=1e-8,
            options={
                "maxiter":
                    int(
                        local_maxiter
                    ),

                "rhobeg":
                    0.20,

                "catol":
                    1e-10,

                "disp":
                    False,
            },
        )

        elapsed = (
            time.perf_counter()
            - start_time
        )

        refined_tau = float(
            local_best[
                "x"
            ][
                0
            ]
        )

        refined_beta = float(
            local_best[
                "x"
            ][
                1
            ]
        )

        refined_energy = float(
            local_best[
                "energy"
            ]
        )

        gamma = (
            refined_tau
            / float(
                energy_scale
            )
        )

        rows.append(
            {
                "start_id":
                    int(
                        start_id
                    ),

                "grid_tau":
                    float(
                        start[
                            "tau"
                        ]
                    ),

                "grid_beta":
                    float(
                        start[
                            "beta"
                        ]
                    ),

                "grid_energy":
                    float(
                        start[
                            "energy"
                        ]
                    ),

                "refined_tau":
                    refined_tau,

                "refined_tau_over_pi":
                    float(
                        refined_tau
                        / np.pi
                    ),

                "refined_gamma":
                    float(
                        gamma
                    ),

                "refined_beta":
                    refined_beta,

                "refined_beta_over_pi":
                    float(
                        refined_beta
                        / np.pi
                    ),

                "refined_energy":
                    refined_energy,

                "improvement":
                    float(
                        start[
                            "energy"
                        ]
                        - refined_energy
                    ),

                "evaluations":
                    int(
                        evaluation_count
                    ),

                "scipy_success":
                    bool(
                        result.success
                    ),

                "scipy_message":
                    str(
                        result.message
                    ),

                "runtime_seconds":
                    float(
                        elapsed
                    ),
            }
        )

        print(
            f"  start {start_id:>2d}"
            f" | grid <H>={start['energy']:.12g}"
            f" -> refined <H>={refined_energy:.12g}"
            f" | tau/pi={refined_tau / np.pi:+.8f}"
            f" | beta/pi={refined_beta / np.pi:.8f}"
        )

        if refined_energy < best_energy:
            best_energy = refined_energy
            best_tau = refined_tau
            best_beta = refined_beta

    return (
        pd.DataFrame(
            rows
        ),
        float(
            best_tau
        ),
        float(
            best_beta
        ),
        float(
            best_energy
        ),
    )


# ============================================================
# Final state diagnostics
# ============================================================

def final_state_diagnostics(
    tau: float,
    beta: float,
    energy_scale: float,
    basis_energies: np.ndarray,
    constant: float,
    n_qubits: int,
    exact_ground_bitstring: str,
    exact_ground_energy: float | None,
    shots_reference: int,
):
    state = p1_statevector(
        tau=tau,
        beta=beta,
        energy_scale=energy_scale,
        basis_energies=basis_energies,
        constant=constant,
        n_qubits=n_qubits,
    )

    probabilities = (
        state.real
        * state.real
        +
        state.imag
        * state.imag
    )

    expectation = float(
        np.dot(
            probabilities,
            basis_energies,
        )
    )

    most_probable_index = int(
        np.argmax(
            probabilities
        )
    )

    most_probable_bitstring = (
        basis_index_to_canonical_bitstring(
            most_probable_index,
            n_qubits,
        )
    )

    most_probable_probability = float(
        probabilities[
            most_probable_index
        ]
    )

    result = {
        "expectation_energy":
            expectation,

        "most_probable_bitstring":
            most_probable_bitstring,

        "most_probable_probability":
            most_probable_probability,

        "most_probable_energy":
            float(
                basis_energies[
                    most_probable_index
                ]
            ),
    }

    if exact_ground_bitstring:
        ground_index = (
            canonical_bitstring_to_basis_index(
                exact_ground_bitstring
            )
        )

        ground_probability = float(
            probabilities[
                ground_index
            ]
        )

        result.update(
            {
                "exact_ground_bitstring":
                    exact_ground_bitstring,

                "exact_ground_energy":
                    exact_ground_energy,

                "ground_probability":
                    ground_probability,

                "expected_ground_counts":
                    float(
                        shots_reference
                        * ground_probability
                    ),

                "probability_ground_seen_at_least_once":
                    float(
                        1.0
                        - (
                            1.0
                            - ground_probability
                        )
                        ** shots_reference
                    ),

                "most_probable_hamming_to_ground":
                    hamming_distance(
                        most_probable_bitstring,
                        exact_ground_bitstring,
                    ),
            }
        )

    return result


# ============================================================
# Save grid / heatmap
# ============================================================

def save_grid_csv(
    path: Path,
    tau_values,
    beta_values,
    matrix,
    energy_scale: float,
):
    rows = []

    for tau_index, tau in enumerate(
        tau_values
    ):
        gamma = (
            float(
                tau
            )
            / float(
                energy_scale
            )
        )

        for beta_index, beta in enumerate(
            beta_values
        ):
            rows.append(
                {
                    "tau_index":
                        int(
                            tau_index
                        ),

                    "beta_index":
                        int(
                            beta_index
                        ),

                    "tau":
                        float(
                            tau
                        ),

                    "tau_over_pi":
                        float(
                            tau
                            / np.pi
                        ),

                    "gamma":
                        float(
                            gamma
                        ),

                    "beta":
                        float(
                            beta
                        ),

                    "beta_over_pi":
                        float(
                            beta
                            / np.pi
                        ),

                    "expectation_energy":
                        float(
                            matrix[
                                tau_index,
                                beta_index
                            ]
                        ),
                }
            )

    pd.DataFrame(
        rows
    ).to_csv(
        path,
        index=False,
    )


def save_heatmap(
    png_path: Path,
    pdf_path: Path,
    dataset: str,
    tau_values,
    beta_values,
    matrix,
    refined_tau: float,
    refined_beta: float,
    refined_energy: float,
    previous_optimizer: dict | None,
    energy_scale: float,
):
    fig, ax = plt.subplots(
        figsize=(
            9.5,
            7.0,
        )
    )

    mesh = ax.pcolormesh(
        beta_values
        / np.pi,
        tau_values
        / np.pi,
        matrix,
        shading="auto",
    )

    colorbar = fig.colorbar(
        mesh,
        ax=ax,
    )

    colorbar.set_label(
        r"$\langle H_C\rangle$"
    )

    ax.scatter(
        [
            refined_beta
            / np.pi
        ],
        [
            refined_tau
            / np.pi
        ],
        marker="*",
        s=180,
        label=(
            "Landscape + COBYLA best "
            f"(<H>={refined_energy:.6g})"
        ),
        zorder=5,
    )

    if previous_optimizer is not None:
        previous_tau = (
            previous_optimizer[
                "gamma"
            ]
            * float(
                energy_scale
            )
        )

        ax.scatter(
            [
                previous_optimizer[
                    "beta"
                ]
                / np.pi
            ],
            [
                previous_tau
                / np.pi
            ],
            marker="x",
            s=100,
            label=(
                "Previous optimizer "
                f"(<H>={previous_optimizer['expectation']:.6g})"
            ),
            zorder=6,
        )

    ax.set_xlabel(
        r"$\beta/\pi$"
    )

    ax.set_ylabel(
        r"$\tau/\pi$  where  $\tau=\gamma\,\sigma(H_C-C)$"
    )

    ax.set_title(
        f"{dataset} - QAOA p=1 Exact Expectation Landscape"
    )

    ax.legend()

    fig.tight_layout()

    fig.savefig(
        png_path,
        dpi=300,
        bbox_inches="tight",
    )

    fig.savefig(
        pdf_path,
        bbox_inches="tight",
    )

    plt.close(
        fig
    )


# ============================================================
# Main diagnostic
# ============================================================

def run(
    dataset: str,
    grid_size: int,
    gamma_window_pi: float,
    top_starts: int,
    min_grid_distance: float,
    local_maxiter: int,
    resume: bool,
    shots_reference: int,
):
    dataset = dataset.upper()

    if grid_size < 5:
        raise ValueError(
            "--grid must be >= 5."
        )

    if top_starts < 1:
        raise ValueError(
            "--top-starts must be >= 1."
        )

    paths = output_paths(
        dataset
    )

    print()
    print(
        "=" * 80
    )

    print(
        f"{dataset} — DETERMINISTIC QAOA p=1 LANDSCAPE DIAGNOSTIC"
    )

    print(
        "=" * 80
    )

    (
        model,
        h,
        j_upper,
        n_qubits,
        constant,
        exact_ground_bitstring,
        exact_ground_energy,
    ) = load_problem(
        dataset
    )

    basis_energies = (
        precompute_basis_energies(
            n_qubits=n_qubits,
            constant=constant,
            h=h,
            j_upper=j_upper,
        )
    )

    energy_scale = (
        compute_energy_scale(
            basis_energies,
            constant,
        )
    )

    print()
    print(
        f"Energy scale std(H_C-C) = "
        f"{energy_scale:.12g}"
    )

    print(
        f"Exact Ising ground       = "
        f"{exact_ground_energy}"
    )

    print(
        f"Exact ground bitstring   = "
        f"{exact_ground_bitstring}"
    )

    previous_optimizer = (
        load_previous_optimizer(
            dataset
        )
    )

    if previous_optimizer is not None:
        print()
        print(
            "Previous p=1 optimizer:"
        )

        print(
            f"  <H>   = "
            f"{previous_optimizer['expectation']:.12g}"
        )

        print(
            f"  gamma = "
            f"{previous_optimizer['gamma']:.12g}"
        )

        print(
            f"  beta  = "
            f"{previous_optimizer['beta']:.12g}"
        )

    (
        tau_values,
        beta_values,
        matrix,
        scan_seconds,
        newly_completed_rows,
    ) = scan_landscape(
        dataset=dataset,
        grid_size=grid_size,
        gamma_window_pi=gamma_window_pi,
        energy_scale=energy_scale,
        basis_energies=basis_energies,
        constant=constant,
        n_qubits=n_qubits,
        paths=paths,
        resume=resume,
    )

    best_flat = int(
        np.nanargmin(
            matrix
        )
    )

    best_i, best_j = np.unravel_index(
        best_flat,
        matrix.shape,
    )

    coarse_best_tau = float(
        tau_values[
            best_i
        ]
    )

    coarse_best_beta = float(
        beta_values[
            best_j
        ]
    )

    coarse_best_energy = float(
        matrix[
            best_i,
            best_j
        ]
    )

    print()
    print(
        "Best coarse-grid point:"
    )

    print(
        f"  <H>      = "
        f"{coarse_best_energy:.12g}"
    )

    print(
        f"  tau/pi   = "
        f"{coarse_best_tau / np.pi:+.8f}"
    )

    print(
        f"  beta/pi  = "
        f"{coarse_best_beta / np.pi:.8f}"
    )

    starts = select_grid_starts(
        matrix=matrix,
        tau_values=tau_values,
        beta_values=beta_values,
        count=top_starts,
        min_grid_distance=min_grid_distance,
    )

    (
        refinement_table,
        refined_tau,
        refined_beta,
        refined_energy,
    ) = refine_starts(
        starts=starts,
        gamma_window_pi=gamma_window_pi,
        local_maxiter=local_maxiter,
        energy_scale=energy_scale,
        basis_energies=basis_energies,
        constant=constant,
        n_qubits=n_qubits,
    )

    refinement_table.to_csv(
        paths[
            "refinements"
        ],
        index=False,
    )

    save_grid_csv(
        path=paths[
            "grid_csv"
        ],
        tau_values=tau_values,
        beta_values=beta_values,
        matrix=matrix,
        energy_scale=energy_scale,
    )

    save_heatmap(
        png_path=paths[
            "heatmap_png"
        ],
        pdf_path=paths[
            "heatmap_pdf"
        ],
        dataset=dataset,
        tau_values=tau_values,
        beta_values=beta_values,
        matrix=matrix,
        refined_tau=refined_tau,
        refined_beta=refined_beta,
        refined_energy=refined_energy,
        previous_optimizer=previous_optimizer,
        energy_scale=energy_scale,
    )

    final_diagnostics = (
        final_state_diagnostics(
            tau=refined_tau,
            beta=refined_beta,
            energy_scale=energy_scale,
            basis_energies=basis_energies,
            constant=constant,
            n_qubits=n_qubits,
            exact_ground_bitstring=exact_ground_bitstring,
            exact_ground_energy=exact_ground_energy,
            shots_reference=shots_reference,
        )
    )

    previous_delta = None
    conclusion = (
        "No previous optimizer result was found; "
        "landscape scan produced the p=1 reference minimum."
    )

    if previous_optimizer is not None:
        previous_delta = float(
            previous_optimizer[
                "expectation"
            ]
            - refined_energy
        )

        if previous_delta > 1e-4:
            conclusion = (
                "Landscape scan found a materially lower p=1 "
                "expectation than the previous optimizer. "
                "The previous optimization likely missed a better basin."
            )

        elif abs(
            previous_delta
        ) <= 1e-6:
            conclusion = (
                "Landscape scan converged to essentially the same p=1 "
                "minimum as the previous optimizer. More seed/restart/"
                "maxiter effort is unlikely to materially improve the "
                "mean-energy p=1 solution."
            )

        else:
            conclusion = (
                "Landscape scan found only a very small improvement over "
                "the previous optimizer. The existing p=1 result is "
                "already close to the best basin found by this scan."
            )

    report = {
        "dataset":
            dataset,

        "p":
            1,

        "diagnostic_type":
            (
                "deterministic coarse landscape scan + "
                "multi-start COBYLA refinement"
            ),

        "important_limit":
            (
                "This is a high-coverage numerical landscape diagnostic, "
                "not a mathematical proof of the continuous global minimum."
            ),

        "selection_objective":
            "minimum exact ideal Ising expectation <H_C>",

        "true_eis_used":
            False,

        "grid": {
            "size":
                int(
                    grid_size
                ),

            "points":
                int(
                    grid_size
                    * grid_size
                ),

            "gamma_window_pi":
                float(
                    gamma_window_pi
                ),

            "tau_range":
                [
                    float(
                        tau_values[
                            0
                        ]
                    ),
                    float(
                        tau_values[
                            -1
                        ]
                    ),
                ],

            "beta_range":
                [
                    float(
                        beta_values[
                            0
                        ]
                    ),
                    float(
                        beta_values[
                            -1
                        ]
                    ),
                ],

            "scan_runtime_seconds":
                float(
                    scan_seconds
                ),

            "new_rows_computed_this_run":
                int(
                    newly_completed_rows
                ),
        },

        "coarse_best": {
            "tau":
                coarse_best_tau,

            "tau_over_pi":
                float(
                    coarse_best_tau
                    / np.pi
                ),

            "gamma":
                float(
                    coarse_best_tau
                    / energy_scale
                ),

            "beta":
                coarse_best_beta,

            "beta_over_pi":
                float(
                    coarse_best_beta
                    / np.pi
                ),

            "expectation_energy":
                coarse_best_energy,
        },

        "refined_best": {
            "tau":
                float(
                    refined_tau
                ),

            "tau_over_pi":
                float(
                    refined_tau
                    / np.pi
                ),

            "gamma":
                float(
                    refined_tau
                    / energy_scale
                ),

            "beta":
                float(
                    refined_beta
                ),

            "beta_over_pi":
                float(
                    refined_beta
                    / np.pi
                ),

            "expectation_energy":
                float(
                    refined_energy
                ),

            **final_diagnostics,
        },

        "exact_ground": {
            "bitstring":
                exact_ground_bitstring,

            "energy":
                exact_ground_energy,

            "expectation_gap":
                (
                    float(
                        refined_energy
                        - exact_ground_energy
                    )
                    if exact_ground_energy is not None
                    else None
                ),
        },

        "previous_optimizer":
            (
                {
                    "source":
                        str(
                            previous_optimizer[
                                "path"
                            ]
                        ),

                    "gamma":
                        float(
                            previous_optimizer[
                                "gamma"
                            ]
                        ),

                    "beta":
                        float(
                            previous_optimizer[
                                "beta"
                            ]
                        ),

                    "expectation_energy":
                        float(
                            previous_optimizer[
                                "expectation"
                            ]
                        ),

                    "improvement_found_by_landscape_scan":
                        previous_delta,
                }
                if previous_optimizer is not None
                else None
            ),

        "conclusion":
            conclusion,

        "files": {
            "grid_csv":
                str(
                    paths[
                        "grid_csv"
                    ]
                ),

            "heatmap_png":
                str(
                    paths[
                        "heatmap_png"
                    ]
                ),

            "heatmap_pdf":
                str(
                    paths[
                        "heatmap_pdf"
                    ]
                ),

            "local_refinements_csv":
                str(
                    paths[
                        "refinements"
                    ]
                ),

            "checkpoint_matrix":
                str(
                    paths[
                        "matrix"
                    ]
                ),
        },
    }

    paths[
        "report"
    ].write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print(
        "=" * 80
    )

    print(
        f"{dataset} — p=1 LANDSCAPE RESULT"
    )

    print(
        "=" * 80
    )

    print(
        f"Coarse best <H>          : "
        f"{coarse_best_energy:.12g}"
    )

    print(
        f"Refined best <H>         : "
        f"{refined_energy:.12g}"
    )

    print(
        f"Refined gamma            : "
        f"{refined_tau / energy_scale:.12g}"
    )

    print(
        f"Refined beta             : "
        f"{refined_beta:.12g}"
    )

    if exact_ground_energy is not None:
        print(
            f"Exact ground energy      : "
            f"{exact_ground_energy:.12g}"
        )

        print(
            f"Expectation-ground gap   : "
            f"{refined_energy - exact_ground_energy:.12g}"
        )

    print(
        f"Ground probability       : "
        f"{final_diagnostics.get('ground_probability')}"
    )

    print(
        f"P(ground >=1 / "
        f"{shots_reference} shots) : "
        f"{final_diagnostics.get('probability_ground_seen_at_least_once')}"
    )

    print(
        f"Most probable bitstring  : "
        f"{final_diagnostics['most_probable_bitstring']}"
    )

    if exact_ground_bitstring:
        print(
            f"Most-probable Hamming    : "
            f"{final_diagnostics.get('most_probable_hamming_to_ground')}"
        )

    if previous_optimizer is not None:
        print()
        print(
            f"Previous optimizer <H>   : "
            f"{previous_optimizer['expectation']:.12g}"
        )

        print(
            f"Landscape improvement    : "
            f"{previous_delta:.12g}"
        )

    print()
    print(
        "Conclusion:"
    )

    print(
        "  "
        + conclusion
    )

    print()
    print(
        "Saved:"
    )

    print(
        f"  {paths['heatmap_png']}"
    )

    print(
        f"  {paths['heatmap_pdf']}"
    )

    print(
        f"  {paths['grid_csv']}"
    )

    print(
        f"  {paths['refinements']}"
    )

    print(
        f"  {paths['report']}"
    )

    print(
        "=" * 80
    )


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic p=1 QAOA expectation-landscape diagnostic."
        )
    )

    parser.add_argument(
        "--dataset",
        choices=[
            "EIS13",
            "EIS14",
        ],
        default="EIS14",
    )

    parser.add_argument(
        "--grid",
        type=int,
        default=32,
        help=(
            "Coarse square grid size. 32 -> 1024 exact evaluations. "
            "64 -> 4096 exact evaluations and can be much slower."
        ),
    )

    parser.add_argument(
        "--gamma-window-pi",
        type=float,
        default=2.0,
        help=(
            "Same scaled tau window as qaoa_optimizer.py. "
            "Default: [-2pi,+2pi]."
        ),
    )

    parser.add_argument(
        "--top-starts",
        type=int,
        default=8,
        help=(
            "Number of separated low-energy grid points refined by COBYLA."
        ),
    )

    parser.add_argument(
        "--min-grid-distance",
        type=float,
        default=3.0,
        help=(
            "Minimum index-space distance between local-refinement starts."
        ),
    )

    parser.add_argument(
        "--local-maxiter",
        type=int,
        default=120,
    )

    parser.add_argument(
        "--shots-reference",
        type=int,
        default=4096,
        help=(
            "Used only to report probability of seeing the exact ground "
            "state at least once. No sampling is used for optimization."
        ),
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "Ignore an existing compatible coarse_landscape.npy checkpoint."
        ),
    )

    args = parser.parse_args()

    if args.gamma_window_pi <= 0.0:
        raise ValueError(
            "--gamma-window-pi must be > 0."
        )

    run(
        dataset=args.dataset,
        grid_size=args.grid,
        gamma_window_pi=args.gamma_window_pi,
        top_starts=args.top_starts,
        min_grid_distance=args.min_grid_distance,
        local_maxiter=args.local_maxiter,
        resume=not args.no_resume,
        shots_reference=args.shots_reference,
    )


if __name__ == "__main__":
    main()
