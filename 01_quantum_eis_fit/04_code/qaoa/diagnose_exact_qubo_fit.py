"""
diagnose_exact_qubo_fit.py

Purpose
-------
Diagnose WHY the Exact-QUBO ground-state EIS fit is worse than Classical.

This script compares four things for one dataset:
1) Experimental spectrum
2) Classical continuous fit
3) Exact QUBO ground state
4) Best TRUE discrete training state used by the surrogate

It uses the project's existing:
- binary_encoding.decode_bitstring
- eis_evaluation.EISEvaluator
- src/impedance_model.py

So it does NOT redefine decoding or EIS loss.

Put this file in:
experiments/QAOA_QUBO/00_initial_pipeline_validation/scripts/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ============================================================
# Paths
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
EXPERIMENT_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = EXPERIMENT_DIR.parents[2]

RESULTS_ROOT = EXPERIMENT_DIR / "results"
CLASSICAL_ROOT = PROJECT_ROOT / "reference_results" / "classical"
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from binary_encoding import PARAMETER_ORDER, decode_bitstring
from eis_evaluation import EISEvaluator
from impedance_model import impedance_model


PARAMETER_UNITS = {
    "Rs": "Ohm",
    "L": "H",
    "Rct": "Ohm",
    "Q1": "S*s^n1",
    "n1": "-",
    "Q2": "S*s^n2",
    "n2": "-",
}


# ============================================================
# Helpers
# ============================================================

def clean_bitstring(value) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    text = text.zfill(21)
    if len(text) != 21 or any(c not in "01" for c in text):
        raise ValueError(f"Invalid 21-bit bitstring: {text}")
    return text


def load_exact_ground(dataset: str):
    path = RESULTS_ROOT / dataset / "ising" / "ising_model.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing Ising model: {path}")

    model = json.loads(path.read_text(encoding="utf-8"))

    exact = model.get("exact_ising_minimum", {})
    if "bitstring" not in exact:
        raise KeyError(
            f"exact_ising_minimum.bitstring not found in {path}"
        )

    bitstring = clean_bitstring(exact["bitstring"])
    energy = exact.get("ising_energy")

    return bitstring, None if energy is None else float(energy), path


def load_best_true_training_state(dataset: str):
    path = (
        RESULTS_ROOT
        / dataset
        / "qubo_validation"
        / "quadratic_surrogate"
        / "surrogate_training_samples.csv"
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Missing surrogate training samples: {path}"
        )

    df = pd.read_csv(
        path,
        dtype={"bitstring": str},
    )

    required = [
        "bitstring",
        "true_weighted_complex_loss",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path} missing columns: {missing}"
        )

    idx = df["true_weighted_complex_loss"].astype(float).idxmin()
    row = df.loc[idx]

    return clean_bitstring(row["bitstring"]), row.to_dict(), path


def load_classical_parameters(dataset: str):
    path = CLASSICAL_ROOT / dataset / "fit_parameters.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"Missing Classical fit parameters: {path}"
        )

    df = pd.read_csv(path)
    cols = {str(c).strip().lower(): c for c in df.columns}

    if "parameter" not in cols or "value" not in cols:
        raise ValueError(
            "Expected Classical fit_parameters.csv columns: "
            "parameter,value,unit"
        )

    mapping = {
        str(row[cols["parameter"]]).strip():
            float(row[cols["value"]])
        for _, row in df.iterrows()
    }

    missing = [p for p in PARAMETER_ORDER if p not in mapping]
    if missing:
        raise ValueError(
            f"Missing Classical parameters: {missing}"
        )

    params = {
        p: float(mapping[p])
        for p in PARAMETER_ORDER
    }

    vector = np.asarray(
        [params[p] for p in PARAMETER_ORDER],
        dtype=float,
    )

    return params, vector, path


def load_classical_spectrum(dataset: str):
    path = CLASSICAL_ROOT / dataset / "fitted_spectrum.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"Missing Classical fitted spectrum: {path}"
        )

    df = pd.read_csv(path)

    required = [
        "frequency_hz",
        "z_real_data_ohm",
        "minus_z_imag_data_ohm",
        "z_real_fit_ohm",
        "minus_z_imag_fit_ohm",
    ]

    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path} missing columns: {missing}"
        )

    f = df["frequency_hz"].to_numpy(dtype=float)

    z_exp = (
        df["z_real_data_ohm"].to_numpy(dtype=float)
        - 1j * df["minus_z_imag_data_ohm"].to_numpy(dtype=float)
    )

    z_classical = (
        df["z_real_fit_ohm"].to_numpy(dtype=float)
        - 1j * df["minus_z_imag_fit_ohm"].to_numpy(dtype=float)
    )

    order = np.argsort(f)

    return (
        f[order],
        z_exp[order],
        z_classical[order],
        path,
    )


def evaluate_vector(
    evaluator: EISEvaluator,
    vector: np.ndarray,
):
    result = evaluator.evaluate(
        np.asarray(vector, dtype=float),
        return_arrays=False,
    )

    return {
        "weighted_complex_loss":
            float(result["weighted_complex_loss"]),
        "SSE":
            float(result["SSE"]),
        "RMSE":
            float(result["RMSE"]),
    }


def decoded_from_bitstring(bitstring: str):
    decoded = decode_bitstring(bitstring)

    params = {
        name:
            float(decoded["physical_parameters"][name])
        for name in PARAMETER_ORDER
    }

    vector = np.asarray(
        decoded["parameter_vector"],
        dtype=float,
    )

    return params, vector


def percent_difference(value, reference):
    if reference == 0:
        return np.nan

    return (
        (value - reference)
        / abs(reference)
        * 100.0
    )


# ============================================================
# Plots
# ============================================================

def plot_nyquist(
    path,
    dataset,
    z_exp,
    z_classical,
    z_exact,
    z_best_true,
):
    fig, ax = plt.subplots(figsize=(7.8, 6.2))

    ax.scatter(
        z_exp.real,
        -z_exp.imag,
        s=24,
        label="Experimental",
        zorder=4,
    )

    ax.plot(
        z_classical.real,
        -z_classical.imag,
        linewidth=2.1,
        label="Classical",
    )

    ax.plot(
        z_exact.real,
        -z_exact.imag,
        linewidth=2.0,
        label="Exact QUBO ground",
    )

    ax.plot(
        z_best_true.real,
        -z_best_true.imag,
        linewidth=1.8,
        linestyle="--",
        label="Best TRUE discrete training",
    )

    ax.set_title(f"{dataset} - Nyquist Diagnostic")
    ax.set_xlabel(r"$Z'$ ($\Omega$)")
    ax.set_ylabel(r"$-Z''$ ($\Omega$)")
    ax.grid(True, alpha=0.28)
    ax.legend()

    fig.tight_layout()
    fig.savefig(
        path,
        dpi=260,
        bbox_inches="tight",
    )
    plt.close(fig)


def plot_bode(
    path,
    dataset,
    frequency,
    z_exp,
    z_classical,
    z_exact,
    z_best_true,
):
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(8.4, 8.2),
        sharex=True,
    )

    ax_mag, ax_phase = axes

    ax_mag.scatter(
        frequency,
        np.abs(z_exp),
        s=18,
        label="Experimental",
        zorder=4,
    )

    ax_mag.plot(
        frequency,
        np.abs(z_classical),
        linewidth=2.1,
        label="Classical",
    )

    ax_mag.plot(
        frequency,
        np.abs(z_exact),
        linewidth=2.0,
        label="Exact QUBO ground",
    )

    ax_mag.plot(
        frequency,
        np.abs(z_best_true),
        linewidth=1.8,
        linestyle="--",
        label="Best TRUE discrete training",
    )

    ax_mag.set_xscale("log")
    ax_mag.set_yscale("log")
    ax_mag.set_ylabel(r"$|Z|$ ($\Omega$)")
    ax_mag.set_title(f"{dataset} - Bode Diagnostic")
    ax_mag.grid(True, which="both", alpha=0.25)
    ax_mag.legend()

    ax_phase.scatter(
        frequency,
        np.angle(z_exp, deg=True),
        s=18,
        label="Experimental",
        zorder=4,
    )

    ax_phase.plot(
        frequency,
        np.angle(z_classical, deg=True),
        linewidth=2.1,
        label="Classical",
    )

    ax_phase.plot(
        frequency,
        np.angle(z_exact, deg=True),
        linewidth=2.0,
        label="Exact QUBO ground",
    )

    ax_phase.plot(
        frequency,
        np.angle(z_best_true, deg=True),
        linewidth=1.8,
        linestyle="--",
        label="Best TRUE discrete training",
    )

    ax_phase.set_xscale("log")
    ax_phase.set_xlabel("Frequency (Hz)")
    ax_phase.set_ylabel("Phase (degree)")
    ax_phase.grid(True, which="both", alpha=0.25)
    ax_phase.legend()

    fig.tight_layout()
    fig.savefig(
        path,
        dpi=260,
        bbox_inches="tight",
    )
    plt.close(fig)


def plot_residuals(
    path,
    dataset,
    frequency,
    z_exp,
    z_classical,
    z_exact,
    z_best_true,
):
    r_classical = z_classical - z_exp
    r_exact = z_exact - z_exp
    r_best = z_best_true - z_exp

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(8.4, 8.2),
        sharex=True,
    )

    axes[0].plot(
        frequency,
        r_classical.real,
        linewidth=1.7,
        label="Classical",
    )
    axes[0].plot(
        frequency,
        r_exact.real,
        linewidth=1.7,
        label="Exact QUBO ground",
    )
    axes[0].plot(
        frequency,
        r_best.real,
        linewidth=1.5,
        linestyle="--",
        label="Best TRUE discrete training",
    )
    axes[0].axhline(0.0, linewidth=1.0)
    axes[0].set_xscale("log")
    axes[0].set_ylabel(r"$\Delta Z'$ ($\Omega$)")
    axes[0].set_title(f"{dataset} - Residual Diagnostic")
    axes[0].grid(True, which="both", alpha=0.25)
    axes[0].legend()

    axes[1].plot(
        frequency,
        r_classical.imag,
        linewidth=1.7,
        label="Classical",
    )
    axes[1].plot(
        frequency,
        r_exact.imag,
        linewidth=1.7,
        label="Exact QUBO ground",
    )
    axes[1].plot(
        frequency,
        r_best.imag,
        linewidth=1.5,
        linestyle="--",
        label="Best TRUE discrete training",
    )
    axes[1].axhline(0.0, linewidth=1.0)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel(r"$\Delta Z''$ ($\Omega$)")
    axes[1].grid(True, which="both", alpha=0.25)
    axes[1].legend()

    fig.tight_layout()
    fig.savefig(
        path,
        dpi=260,
        bbox_inches="tight",
    )
    plt.close(fig)


# ============================================================
# Diagnosis logic
# ============================================================

def classify_diagnosis(
    classical_eval,
    exact_eval,
    best_true_eval,
):
    exact_vs_best = (
        exact_eval["weighted_complex_loss"]
        / best_true_eval["weighted_complex_loss"]
    )

    best_vs_classical = (
        best_true_eval["weighted_complex_loss"]
        / classical_eval["weighted_complex_loss"]
    )

    exact_vs_classical = (
        exact_eval["weighted_complex_loss"]
        / classical_eval["weighted_complex_loss"]
    )

    if exact_vs_best > 1.25:
        diagnosis = (
            "SURROGATE/QUBO MIS-RANKING: the Exact-QUBO ground state "
            "is much worse than the best TRUE discrete state."
        )
        next_action = (
            "Improve quadratic surrogate / refinement before changing QAOA p."
        )

    elif best_vs_classical > 1.50:
        diagnosis = (
            "DISCRETE-SPACE / OBJECTIVE LIMITATION: the best TRUE discrete "
            "state is already much worse than the continuous Classical fit."
        )
        next_action = (
            "Do NOT change QAOA p yet. First inspect 3-bit encoding bounds "
            "and the weighted objective. Consider narrower physically justified "
            "bounds or more bits only after this diagnostic."
        )

    else:
        diagnosis = (
            "QUBO DISCRETE SOLUTION IS ACCEPTABLE: Exact-QUBO and best TRUE "
            "discrete states are reasonably close to Classical."
        )
        next_action = (
            "Now investigate QAOA depth p and output-state selection."
        )

    return {
        "exact_vs_best_true_weighted_loss_ratio":
            float(exact_vs_best),
        "best_true_vs_classical_weighted_loss_ratio":
            float(best_vs_classical),
        "exact_vs_classical_weighted_loss_ratio":
            float(exact_vs_classical),
        "diagnosis":
            diagnosis,
        "recommended_next_action":
            next_action,
    }


# ============================================================
# Run
# ============================================================

def run(dataset: str):
    dataset = dataset.upper()

    print("\n" + "=" * 84)
    print(f"{dataset} - EXACT QUBO FIT DIAGNOSTIC")
    print("=" * 84)

    out_dir = (
        RESULTS_ROOT
        / dataset
        / "exact_qubo_fit_diagnostic"
    )
    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Exact ground
    exact_bitstring, exact_energy, ising_path = (
        load_exact_ground(dataset)
    )
    exact_params, exact_vector = (
        decoded_from_bitstring(
            exact_bitstring
        )
    )

    # Best TRUE discrete training state
    best_bitstring, best_row, training_path = (
        load_best_true_training_state(
            dataset
        )
    )
    best_params, best_vector = (
        decoded_from_bitstring(
            best_bitstring
        )
    )

    # Classical
    (
        classical_params,
        classical_vector,
        classical_param_path,
    ) = load_classical_parameters(dataset)

    (
        frequency,
        z_exp,
        z_classical,
        classical_spectrum_path,
    ) = load_classical_spectrum(dataset)

    # Physical spectra
    z_exact = np.asarray(
        impedance_model(
            frequency,
            *exact_vector,
        ),
        dtype=complex,
    )

    z_best_true = np.asarray(
        impedance_model(
            frequency,
            *best_vector,
        ),
        dtype=complex,
    )

    # Same project evaluator for all three parameter sets.
    evaluator = EISEvaluator(dataset)

    classical_eval = evaluate_vector(
        evaluator,
        classical_vector,
    )

    exact_eval = evaluate_vector(
        evaluator,
        exact_vector,
    )

    best_true_eval = evaluate_vector(
        evaluator,
        best_vector,
    )

    diagnosis = classify_diagnosis(
        classical_eval,
        exact_eval,
        best_true_eval,
    )

    # Parameter comparison.
    rows = []

    for i, name in enumerate(PARAMETER_ORDER):
        rows.append(
            {
                "parameter": name,
                "unit": PARAMETER_UNITS[name],
                "exact_bits":
                    exact_bitstring[i * 3:(i + 1) * 3],
                "best_true_bits":
                    best_bitstring[i * 3:(i + 1) * 3],
                "classical_value":
                    classical_params[name],
                "exact_qubo_value":
                    exact_params[name],
                "best_true_discrete_value":
                    best_params[name],
                "exact_vs_classical_percent":
                    percent_difference(
                        exact_params[name],
                        classical_params[name],
                    ),
                "best_true_vs_classical_percent":
                    percent_difference(
                        best_params[name],
                        classical_params[name],
                    ),
            }
        )

    parameter_table = pd.DataFrame(rows)
    parameter_table.to_csv(
        out_dir / "parameter_diagnostic.csv",
        index=False,
    )

    # Metrics.
    metrics_table = pd.DataFrame(
        [
            {
                "method": "Classical",
                **classical_eval,
            },
            {
                "method": "Exact QUBO ground",
                **exact_eval,
            },
            {
                "method": "Best TRUE discrete training",
                **best_true_eval,
            },
        ]
    )

    metrics_table.to_csv(
        out_dir / "metrics_diagnostic.csv",
        index=False,
    )

    # Spectrum.
    pd.DataFrame(
        {
            "frequency_hz": frequency,
            "z_real_data_ohm": z_exp.real,
            "minus_z_imag_data_ohm": -z_exp.imag,

            "z_real_classical_ohm": z_classical.real,
            "minus_z_imag_classical_ohm":
                -z_classical.imag,

            "z_real_exact_qubo_ohm": z_exact.real,
            "minus_z_imag_exact_qubo_ohm":
                -z_exact.imag,

            "z_real_best_true_discrete_ohm":
                z_best_true.real,
            "minus_z_imag_best_true_discrete_ohm":
                -z_best_true.imag,
        }
    ).to_csv(
        out_dir / "spectrum_diagnostic.csv",
        index=False,
    )

    # Plots.
    plot_nyquist(
        out_dir / "nyquist_diagnostic.png",
        dataset,
        z_exp,
        z_classical,
        z_exact,
        z_best_true,
    )

    plot_bode(
        out_dir / "bode_diagnostic.png",
        dataset,
        frequency,
        z_exp,
        z_classical,
        z_exact,
        z_best_true,
    )

    plot_residuals(
        out_dir / "residuals_diagnostic.png",
        dataset,
        frequency,
        z_exp,
        z_classical,
        z_exact,
        z_best_true,
    )

    report = {
        "dataset": dataset,
        "exact_qubo_ground": {
            "bitstring": exact_bitstring,
            "ising_energy": exact_energy,
            "parameters": exact_params,
            "metrics": exact_eval,
        },
        "best_true_discrete_training": {
            "bitstring": best_bitstring,
            "parameters": best_params,
            "metrics": best_true_eval,
            "saved_training_row": best_row,
        },
        "classical": {
            "parameters": classical_params,
            "metrics": classical_eval,
        },
        "diagnosis": diagnosis,
        "sources": {
            "ising_model": str(ising_path),
            "surrogate_training_samples":
                str(training_path),
            "classical_fit_parameters":
                str(classical_param_path),
            "classical_fitted_spectrum":
                str(classical_spectrum_path),
            "decoder":
                str(SCRIPT_DIR / "binary_encoding.py"),
            "evaluator":
                str(SCRIPT_DIR / "eis_evaluation.py"),
        },
    }

    (
        out_dir
        / "diagnostic_report.json"
    ).write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Terminal summary.
    print(f"\nExact QUBO bitstring : {exact_bitstring}")
    print(f"Best TRUE bitstring  : {best_bitstring}")

    print("\nWeighted complex loss:")
    print(
        f"  Classical           = "
        f"{classical_eval['weighted_complex_loss']:.12g}"
    )
    print(
        f"  Exact QUBO ground   = "
        f"{exact_eval['weighted_complex_loss']:.12g}"
    )
    print(
        f"  Best TRUE discrete  = "
        f"{best_true_eval['weighted_complex_loss']:.12g}"
    )

    print("\nSSE:")
    print(
        f"  Classical           = "
        f"{classical_eval['SSE']:.12g}"
    )
    print(
        f"  Exact QUBO ground   = "
        f"{exact_eval['SSE']:.12g}"
    )
    print(
        f"  Best TRUE discrete  = "
        f"{best_true_eval['SSE']:.12g}"
    )

    print("\nRMSE:")
    print(
        f"  Classical           = "
        f"{classical_eval['RMSE']:.12g}"
    )
    print(
        f"  Exact QUBO ground   = "
        f"{exact_eval['RMSE']:.12g}"
    )
    print(
        f"  Best TRUE discrete  = "
        f"{best_true_eval['RMSE']:.12g}"
    )

    print("\nDiagnosis:")
    print(" ", diagnosis["diagnosis"])
    print("\nNext action:")
    print(" ", diagnosis["recommended_next_action"])

    print("\nSaved to:")
    print(out_dir)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dataset",
        choices=["EIS13", "EIS14"],
        required=True,
    )

    args = parser.parse_args()
    run(args.dataset)


if __name__ == "__main__":
    main()
