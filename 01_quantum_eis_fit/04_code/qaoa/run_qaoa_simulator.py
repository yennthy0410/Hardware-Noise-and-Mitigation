"""
run_qaoa_simulator.py

Evaluate optimized QAOA simulator EIS fit against Classical.

Added outputs
-------------
This version saves:
- Nyquist
- Bode
- Residuals
- SSE/RMSE comparison chart
- Parameters CSV
- Spectrum CSV
- Metrics CSV
- JSON report
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

# Classical reference is intentionally locked to:
#   reference_results/classical/<DATASET>/
# No fallback to results/<DATASET>/... is allowed.
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# ============================================================
# Existing project modules
# ============================================================

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
# Optimizer result
# ============================================================

def find_optimizer_result(dataset: str, p: int) -> Path:
    dataset = dataset.upper()
    qaoa_root = RESULTS_ROOT / dataset / "qaoa"

    candidates = [
        qaoa_root / "optimizer" / f"p{p}" / "qaoa_optimal_parameters.json",
        qaoa_root / "optimizer_multistart" / "qaoa_optimal_parameters.json",
        qaoa_root / "optimizer" / "qaoa_optimal_parameters.json",
    ]

    for path in candidates:
        if not path.exists():
            continue

        data = json.loads(path.read_text(encoding="utf-8"))
        stored_p = int(data.get("p", p))

        if stored_p == p:
            return path

    raise FileNotFoundError(
        "Cannot find qaoa_optimal_parameters.json.\n"
        "Run qaoa_optimizer.py first.\n\nSearched:\n  "
        + "\n  ".join(str(path) for path in candidates)
    )


def load_optimizer_result(dataset: str, p: int):
    path = find_optimizer_result(dataset, p)
    report = json.loads(path.read_text(encoding="utf-8"))

    gamma = np.asarray(report["optimal_gamma"], dtype=float)
    beta = np.asarray(report["optimal_beta"], dtype=float)

    if len(gamma) != p or len(beta) != p:
        raise ValueError(f"{path}: expected {p} gamma and {p} beta values.")

    return report, gamma, beta, path


def select_qaoa_bitstring(optimizer_report: dict, selection: str):
    """
    recommended:
        lowest-Ising-energy finite-shot candidate saved by optimizer.

    most-probable:
        most-probable ideal QAOA bitstring (diagnostic only).
    """

    if selection == "recommended":
        bitstring = optimizer_report.get("recommended_eis_candidate_bitstring")

        if bitstring is None:
            raise KeyError(
                "Optimizer output does not contain "
                "'recommended_eis_candidate_bitstring'.\n"
                "Use upgraded qaoa_optimizer.py first, or run this "
                "script with --selection most-probable."
            )

        return str(bitstring).strip(), "lowest-Ising-energy finite-shot QAOA candidate"

    if selection == "most-probable":
        bitstring = optimizer_report.get("most_probable_bitstring")

        if bitstring is None:
            raise KeyError(
                "Optimizer output does not contain "
                "'most_probable_bitstring'."
            )

        return str(bitstring).strip(), "most-probable ideal QAOA bitstring"

    raise ValueError(f"Unknown selection rule: {selection}")


# ============================================================
# Exact QUBO ground only for Hamming diagnostic
# ============================================================

def load_exact_ground(dataset: str):
    path = RESULTS_ROOT / dataset / "ising" / "ising_model.json"

    if not path.exists():
        return None, None, path

    model = json.loads(path.read_text(encoding="utf-8"))
    exact = model.get("exact_ising_minimum", {})

    bitstring = exact.get("bitstring")
    energy = exact.get("ising_energy")

    return (
        None if bitstring is None else str(bitstring).strip(),
        None if energy is None else float(energy),
        path,
    )


def hamming_distance(first: str | None, second: str | None):
    if first is None or second is None:
        return None

    if len(first) != len(second):
        return None

    return int(sum(a != b for a, b in zip(first, second)))


# ============================================================
# Classical reference
# ============================================================

def validate_classical_reference_root() -> None:
    """
    Enforce that Classical data comes ONLY from:
        reference_results/classical/<DATASET>/

    This prevents accidental reuse of QAOA/QUBO-generated files.
    """
    expected = (
        PROJECT_ROOT
        / "reference_results"
        / "classical"
    ).resolve()

    actual = CLASSICAL_ROOT.resolve()

    if actual != expected:
        raise RuntimeError(
            "CLASSICAL_ROOT is not the approved reference_results path.\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )



def load_classical_parameters(dataset: str):
    path = CLASSICAL_ROOT / dataset / "fit_parameters.csv"

    if not path.exists():
        raise FileNotFoundError(f"Missing Classical parameters:\n{path}")

    df = pd.read_csv(path)

    lower = {str(column).strip().lower(): column for column in df.columns}

    if "parameter" in lower and "value" in lower:
        pcol = lower["parameter"]
        vcol = lower["value"]

        mapping = {
            str(row[pcol]).strip(): float(row[vcol])
            for _, row in df.iterrows()
        }

        missing = [name for name in PARAMETER_ORDER if name not in mapping]

        if missing:
            raise ValueError(f"{path} missing parameters: {missing}")

        params = {name: float(mapping[name]) for name in PARAMETER_ORDER}

    elif all(name in df.columns for name in PARAMETER_ORDER):
        row = df.iloc[0]
        params = {name: float(row[name]) for name in PARAMETER_ORDER}

    else:
        raise ValueError(
            f"Cannot parse Classical parameter file:\n{path}\n"
            f"Columns: {list(df.columns)}"
        )

    vector = np.asarray([params[name] for name in PARAMETER_ORDER], dtype=float)

    return params, vector, path


def load_classical_spectrum(dataset: str):
    path = CLASSICAL_ROOT / dataset / "fitted_spectrum.csv"

    if not path.exists():
        raise FileNotFoundError(f"Missing Classical spectrum:\n{path}")

    df = pd.read_csv(path)

    required = [
        "frequency_hz",
        "z_real_data_ohm",
        "minus_z_imag_data_ohm",
        "z_real_fit_ohm",
        "minus_z_imag_fit_ohm",
    ]

    missing = [column for column in required if column not in df.columns]

    if missing:
        raise ValueError(f"{path} missing required columns: {missing}")

    frequency = df["frequency_hz"].to_numpy(dtype=float)

    z_exp = (
        df["z_real_data_ohm"].to_numpy(dtype=float)
        - 1j * df["minus_z_imag_data_ohm"].to_numpy(dtype=float)
    )

    z_classical = (
        df["z_real_fit_ohm"].to_numpy(dtype=float)
        - 1j * df["minus_z_imag_fit_ohm"].to_numpy(dtype=float)
    )

    order = np.argsort(frequency)

    return frequency[order], z_exp[order], z_classical[order], path


# ============================================================
# Metrics
# ============================================================

def evaluate_with_project_evaluator(evaluator: EISEvaluator, parameter_vector: np.ndarray):
    result = evaluator.evaluate(parameter_vector, return_arrays=False)

    required = ["weighted_complex_loss", "SSE", "RMSE"]
    missing = [key for key in required if key not in result]

    if missing:
        raise KeyError(
            "EISEvaluator did not return expected keys: "
            f"{missing}. Returned: {list(result.keys())}"
        )

    return {
        "weighted_complex_loss": float(result["weighted_complex_loss"]),
        "SSE": float(result["SSE"]),
        "RMSE": float(result["RMSE"]),
    }


def plotted_curve_metrics(z_exp, z_fit):
    residual = np.asarray(z_fit) - np.asarray(z_exp)

    sse = float(np.sum(np.abs(residual) ** 2))
    rmse = float(np.sqrt(sse / (2.0 * len(residual))))

    return {
        "SSE_complex_ohm2": sse,
        "RMSE_real_imag_ohm": rmse,
    }


# ============================================================
# Figure helpers
# ============================================================

def save_figure(fig, png_path: Path):
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    fig.savefig(png_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def save_nyquist(output_dir, dataset, z_exp, z_classical, z_qaoa, x_max):
    fig, ax = plt.subplots(figsize=(7.4, 6.0))

    ax.scatter(
        z_exp.real,
        -z_exp.imag,
        s=28,
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
        z_qaoa.real,
        -z_qaoa.imag,
        "--",
        linewidth=2.1,
        label="QAOA simulator",
    )

    ax.set_title(f"{dataset} - Nyquist: QAOA Simulator vs Classical")
    ax.set_xlabel(r"$Z'$ ($\Omega$)")
    ax.set_ylabel(r"$-Z''$ ($\Omega$)")
    ax.grid(True, alpha=0.28)
    ax.legend()

    if x_max is not None:
        ax.set_xlim(0.0, float(x_max))

    fig.tight_layout()
    save_figure(fig, output_dir / "nyquist_qaoa_vs_classical.png")


def save_bode(output_dir, dataset, frequency, z_exp, z_classical, z_qaoa):
    fig, axes = plt.subplots(2, 1, figsize=(8.4, 8.2), sharex=True)
    magnitude, phase = axes

    magnitude.scatter(
        frequency,
        np.abs(z_exp),
        s=24,
        label="Experimental",
        zorder=4,
    )

    magnitude.plot(
        frequency,
        np.abs(z_classical),
        linewidth=2.0,
        label="Classical",
    )

    magnitude.plot(
        frequency,
        np.abs(z_qaoa),
        "--",
        linewidth=2.0,
        label="QAOA simulator",
    )

    magnitude.set_xscale("log")
    magnitude.set_yscale("log")
    magnitude.set_ylabel(r"$|Z|$ ($\Omega$)")
    magnitude.set_title(f"{dataset} - Bode: QAOA Simulator vs Classical")
    magnitude.grid(True, which="both", alpha=0.25)
    magnitude.legend()

    phase.scatter(
        frequency,
        np.angle(z_exp, deg=True),
        s=24,
        label="Experimental",
        zorder=4,
    )

    phase.plot(
        frequency,
        np.angle(z_classical, deg=True),
        linewidth=2.0,
        label="Classical",
    )

    phase.plot(
        frequency,
        np.angle(z_qaoa, deg=True),
        "--",
        linewidth=2.0,
        label="QAOA simulator",
    )

    phase.set_xscale("log")
    phase.set_xlabel("Frequency (Hz)")
    phase.set_ylabel("Phase (degree)")
    phase.grid(True, which="both", alpha=0.25)
    phase.legend()

    fig.tight_layout()
    save_figure(fig, output_dir / "bode_qaoa_vs_classical.png")


def save_residuals(output_dir, dataset, frequency, z_exp, z_classical, z_qaoa):
    residual_classical = z_classical - z_exp
    residual_qaoa = z_qaoa - z_exp

    fig, axes = plt.subplots(2, 1, figsize=(8.4, 8.2), sharex=True)

    axes[0].plot(
        frequency,
        residual_classical.real,
        "o-",
        markersize=3,
        linewidth=1.4,
        label="Classical",
    )

    axes[0].plot(
        frequency,
        residual_qaoa.real,
        "o--",
        markersize=3,
        linewidth=1.4,
        label="QAOA simulator",
    )

    axes[0].axhline(0.0, linewidth=1.0)
    axes[0].set_xscale("log")
    axes[0].set_ylabel(r"$\Delta Z'$ ($\Omega$)")
    axes[0].set_title(f"{dataset} - Residuals (Fit - Data)")
    axes[0].grid(True, which="both", alpha=0.25)
    axes[0].legend()

    axes[1].plot(
        frequency,
        residual_classical.imag,
        "o-",
        markersize=3,
        linewidth=1.4,
        label="Classical",
    )

    axes[1].plot(
        frequency,
        residual_qaoa.imag,
        "o--",
        markersize=3,
        linewidth=1.4,
        label="QAOA simulator",
    )

    axes[1].axhline(0.0, linewidth=1.0)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel(r"$\Delta Z''$ ($\Omega$)")
    axes[1].grid(True, which="both", alpha=0.25)
    axes[1].legend()

    fig.tight_layout()
    save_figure(fig, output_dir / "residuals_qaoa_vs_classical.png")


def save_sse_rmse_chart(output_dir, dataset, classical_eval, qaoa_eval):
    """
    Save one comparison chart containing evaluator SSE and RMSE.
    """
    fig, ax = plt.subplots(figsize=(7.6, 5.8))

    metric_names = ["SSE", "RMSE"]
    x = np.arange(len(metric_names))
    width = 0.36

    classical_values = [
        classical_eval["SSE"],
        classical_eval["RMSE"],
    ]

    qaoa_values = [
        qaoa_eval["SSE"],
        qaoa_eval["RMSE"],
    ]

    ax.bar(
        x - width / 2,
        classical_values,
        width,
        label="Classical",
    )

    ax.bar(
        x + width / 2,
        qaoa_values,
        width,
        label="QAOA simulator",
    )

    ax.set_xticks(x)
    ax.set_xticklabels(metric_names)
    ax.set_ylabel("Metric value")
    ax.set_title(f"{dataset} - SSE / RMSE: QAOA Simulator vs Classical")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend()

    for xpos, value in zip(x - width / 2, classical_values):
        ax.text(
            xpos,
            value,
            f"{value:.4g}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    for xpos, value in zip(x + width / 2, qaoa_values):
        ax.text(
            xpos,
            value,
            f"{value:.4g}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    fig.tight_layout()
    save_figure(fig, output_dir / "sse_rmse_qaoa_vs_classical.png")


# ============================================================
# One dataset
# ============================================================

def run_dataset(dataset: str, p: int, selection: str, nyquist_xmax: float | None):
    dataset = dataset.upper()

    validate_classical_reference_root()

    print()
    print("=" * 78)
    print(f"{dataset} — QAOA SIMULATOR EIS FIT — p={p}")
    print("=" * 78)

    output_dir = RESULTS_ROOT / dataset / "qaoa" / "simulator" / f"p{p}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1) Best gamma / beta and chosen QAOA state
    optimizer_report, gamma, beta, optimizer_path = load_optimizer_result(dataset, p)
    qaoa_bitstring, selection_rule = select_qaoa_bitstring(optimizer_report, selection)

    # 2) Exact QUBO ground only for Hamming diagnostic
    exact_ground_bitstring, exact_ground_energy, ising_path = load_exact_ground(dataset)

    # 3) Decode QAOA bitstring
    decoded = decode_bitstring(qaoa_bitstring)

    qaoa_parameters = {
        name: float(decoded["physical_parameters"][name])
        for name in PARAMETER_ORDER
    }

    qaoa_vector = np.asarray(decoded["parameter_vector"], dtype=float)

    # 4) Classical reference
    classical_parameters, classical_vector, classical_parameter_path = load_classical_parameters(dataset)
    frequency, z_exp, z_classical, classical_spectrum_path = load_classical_spectrum(dataset)

    # 5) Build QAOA impedance curve
    z_qaoa = np.asarray(
        impedance_model(frequency, *qaoa_vector),
        dtype=complex,
    )

    # 6) Metrics
    evaluator = EISEvaluator(dataset)

    qaoa_eval = evaluate_with_project_evaluator(evaluator, qaoa_vector)
    classical_eval = evaluate_with_project_evaluator(evaluator, classical_vector)

    qaoa_plot_metrics = plotted_curve_metrics(z_exp, z_qaoa)
    classical_plot_metrics = plotted_curve_metrics(z_exp, z_classical)

    # 7) Parameter table
    parameter_rows = []

    for index, name in enumerate(PARAMETER_ORDER):
        classical_value = float(classical_parameters[name])
        qaoa_value = float(qaoa_parameters[name])

        deviation = (
            (qaoa_value - classical_value) / abs(classical_value) * 100.0
            if classical_value != 0
            else np.nan
        )

        start = index * 3

        parameter_rows.append(
            {
                "parameter": name,
                "unit": PARAMETER_UNITS.get(name, ""),
                "bits": qaoa_bitstring[start:start + 3],
                "classical_value": classical_value,
                "qaoa_simulator_value": qaoa_value,
                "deviation_from_classical_percent": deviation,
            }
        )

    pd.DataFrame(parameter_rows).to_csv(
        output_dir / "qaoa_simulator_parameters.csv",
        index=False,
    )

    # 8) Full spectrum table
    residual_classical = z_classical - z_exp
    residual_qaoa = z_qaoa - z_exp

    pd.DataFrame(
        {
            "frequency_hz": frequency,

            "z_real_data_ohm": z_exp.real,
            "z_imag_data_ohm": z_exp.imag,
            "minus_z_imag_data_ohm": -z_exp.imag,
            "z_magnitude_data_ohm": np.abs(z_exp),
            "phase_data_degree": np.angle(z_exp, deg=True),

            "z_real_classical_ohm": z_classical.real,
            "z_imag_classical_ohm": z_classical.imag,
            "minus_z_imag_classical_ohm": -z_classical.imag,
            "z_magnitude_classical_ohm": np.abs(z_classical),
            "phase_classical_degree": np.angle(z_classical, deg=True),

            "z_real_qaoa_simulator_ohm": z_qaoa.real,
            "z_imag_qaoa_simulator_ohm": z_qaoa.imag,
            "minus_z_imag_qaoa_simulator_ohm": -z_qaoa.imag,
            "z_magnitude_qaoa_simulator_ohm": np.abs(z_qaoa),
            "phase_qaoa_simulator_degree": np.angle(z_qaoa, deg=True),

            "residual_real_classical_ohm": residual_classical.real,
            "residual_imag_classical_ohm": residual_classical.imag,
            "residual_abs_classical_ohm": np.abs(residual_classical),

            "residual_real_qaoa_simulator_ohm": residual_qaoa.real,
            "residual_imag_qaoa_simulator_ohm": residual_qaoa.imag,
            "residual_abs_qaoa_simulator_ohm": np.abs(residual_qaoa),
        }
    ).to_csv(
        output_dir / "qaoa_simulator_fitted_spectrum.csv",
        index=False,
    )

    # 9) Metrics table
    pd.DataFrame(
        [
            {
                "method": "Classical",
                "weighted_complex_loss": classical_eval["weighted_complex_loss"],
                "SSE": classical_eval["SSE"],
                "RMSE": classical_eval["RMSE"],
                "plotted_curve_SSE": classical_plot_metrics["SSE_complex_ohm2"],
                "plotted_curve_RMSE": classical_plot_metrics["RMSE_real_imag_ohm"],
            },
            {
                "method": "QAOA Simulator",
                "weighted_complex_loss": qaoa_eval["weighted_complex_loss"],
                "SSE": qaoa_eval["SSE"],
                "RMSE": qaoa_eval["RMSE"],
                "plotted_curve_SSE": qaoa_plot_metrics["SSE_complex_ohm2"],
                "plotted_curve_RMSE": qaoa_plot_metrics["RMSE_real_imag_ohm"],
            },
        ]
    ).to_csv(
        output_dir / "qaoa_simulator_fit_metrics.csv",
        index=False,
    )

    # 10) Figures
    save_nyquist(output_dir, dataset, z_exp, z_classical, z_qaoa, nyquist_xmax)
    save_bode(output_dir, dataset, frequency, z_exp, z_classical, z_qaoa)
    save_residuals(output_dir, dataset, frequency, z_exp, z_classical, z_qaoa)
    save_sse_rmse_chart(output_dir, dataset, classical_eval, qaoa_eval)

    # 11) Report
    report = {
        "dataset": dataset,
        "p": int(p),
        "methodology": (
            "QAOA state is selected by the QUBO/Ising result before TRUE EIS evaluation. "
            "TRUE EIS metrics are evaluation only."
        ),
        "optimizer_source": str(optimizer_path),
        "best_angles": {
            "gamma": [float(value) for value in gamma],
            "beta": [float(value) for value in beta],
        },
        "qaoa_candidate": {
            "selection_rule": selection_rule,
            "bitstring": qaoa_bitstring,
            "decoded_parameters": qaoa_parameters,
            "hamming_to_exact_qubo_ground": hamming_distance(qaoa_bitstring, exact_ground_bitstring),
        },
        "exact_qubo_ground_diagnostic": {
            "bitstring": exact_ground_bitstring,
            "ising_energy": exact_ground_energy,
            "source": str(ising_path),
        },
        "classical": {
            "parameters": classical_parameters,
            "project_evaluator_metrics": classical_eval,
            "plotted_curve_metrics": classical_plot_metrics,
        },
        "qaoa_simulator": {
            "parameters": qaoa_parameters,
            "project_evaluator_metrics": qaoa_eval,
            "plotted_curve_metrics": qaoa_plot_metrics,
        },
        "ratio_qaoa_over_classical": {
            "weighted_complex_loss": (
                qaoa_eval["weighted_complex_loss"] / classical_eval["weighted_complex_loss"]
                if classical_eval["weighted_complex_loss"] != 0 else None
            ),
            "SSE": (
                qaoa_eval["SSE"] / classical_eval["SSE"]
                if classical_eval["SSE"] != 0 else None
            ),
            "RMSE": (
                qaoa_eval["RMSE"] / classical_eval["RMSE"]
                if classical_eval["RMSE"] != 0 else None
            ),
        },
        "sources": {
            "classical_parameters": str(classical_parameter_path),
            "classical_spectrum": str(classical_spectrum_path),
            "binary_decoder": str(SCRIPT_DIR / "binary_encoding.py"),
            "evaluator": str(SCRIPT_DIR / "eis_evaluation.py"),
        },
        "files": {
            "nyquist": str(output_dir / "nyquist_qaoa_vs_classical.png"),
            "bode": str(output_dir / "bode_qaoa_vs_classical.png"),
            "residuals": str(output_dir / "residuals_qaoa_vs_classical.png"),
            "sse_rmse_chart": str(output_dir / "sse_rmse_qaoa_vs_classical.png"),
            "parameters_csv": str(output_dir / "qaoa_simulator_parameters.csv"),
            "spectrum_csv": str(output_dir / "qaoa_simulator_fitted_spectrum.csv"),
            "metrics_csv": str(output_dir / "qaoa_simulator_fit_metrics.csv"),
        },
    }

    (output_dir / "qaoa_simulator_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 12) Terminal summary
    print(f"Optimizer source         : {optimizer_path}")
    print(f"Classical parameters     : {classical_parameter_path}")
    print(f"Classical spectrum       : {classical_spectrum_path}")
    print(f"QAOA selection rule      : {selection_rule}")
    print(f"QAOA bitstring           : {qaoa_bitstring}")
    print(f"Hamming to Exact QUBO    : {hamming_distance(qaoa_bitstring, exact_ground_bitstring)}")

    print("\nBest gamma:")
    for index, value in enumerate(gamma):
        print(f"  gamma[{index}] = {value:.12g}")

    print("\nBest beta:")
    for index, value in enumerate(beta):
        print(f"  beta[{index}]  = {value:.12g}")

    print("\nDecoded QAOA parameters:")
    for name in PARAMETER_ORDER:
        print(f"  {name:<4s} = {qaoa_parameters[name]:.12g}")

    print("\nProject evaluator metrics:")
    print(f"  Classical weighted loss = {classical_eval['weighted_complex_loss']:.12g}")
    print(f"  QAOA weighted loss      = {qaoa_eval['weighted_complex_loss']:.12g}")
    print(f"  Classical SSE           = {classical_eval['SSE']:.12g}")
    print(f"  QAOA SSE                = {qaoa_eval['SSE']:.12g}")
    print(f"  Classical RMSE          = {classical_eval['RMSE']:.12g}")
    print(f"  QAOA RMSE               = {qaoa_eval['RMSE']:.12g}")

    print("\nSaved outputs:")
    print(f"  {output_dir / 'nyquist_qaoa_vs_classical.png'}")
    print(f"  {output_dir / 'bode_qaoa_vs_classical.png'}")
    print(f"  {output_dir / 'residuals_qaoa_vs_classical.png'}")
    print(f"  {output_dir / 'sse_rmse_qaoa_vs_classical.png'}")
    print(f"  {output_dir / 'qaoa_simulator_parameters.csv'}")
    print(f"  {output_dir / 'qaoa_simulator_fitted_spectrum.csv'}")
    print(f"  {output_dir / 'qaoa_simulator_fit_metrics.csv'}")
    print(f"  {output_dir / 'qaoa_simulator_report.json'}")
    print("=" * 78)

    return {
        "dataset": dataset,
        "p": p,
        "bitstring": qaoa_bitstring,
        "hamming_to_exact_qubo": hamming_distance(qaoa_bitstring, exact_ground_bitstring),
        "qaoa_weighted_loss": qaoa_eval["weighted_complex_loss"],
        "qaoa_SSE": qaoa_eval["SSE"],
        "qaoa_RMSE": qaoa_eval["RMSE"],
        "classical_weighted_loss": classical_eval["weighted_complex_loss"],
        "classical_SSE": classical_eval["SSE"],
        "classical_RMSE": classical_eval["RMSE"],
    }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Evaluate optimized QAOA simulator EIS fit against Classical."
    )

    parser.add_argument(
        "--dataset",
        choices=["EIS13", "EIS14", "BOTH"],
        default="BOTH",
    )

    parser.add_argument(
        "--p",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--selection",
        choices=["recommended", "most-probable"],
        default="recommended",
        help=(
            "recommended = preferred lowest-Ising-energy finite-shot candidate "
            "saved by the optimizer."
        ),
    )

    parser.add_argument(
        "--nyquist-xmax",
        type=float,
        default=None,
        help=(
            "Optional fixed Nyquist x maximum, e.g. 60. "
            "Default auto-scale avoids hiding a poor QAOA fit."
        ),
    )

    args = parser.parse_args()

    if args.p < 1:
        raise ValueError("--p must be >= 1.")

    datasets = ["EIS13", "EIS14"] if args.dataset == "BOTH" else [args.dataset]

    rows = []

    for dataset in datasets:
        rows.append(
            run_dataset(
                dataset=dataset,
                p=args.p,
                selection=args.selection,
                nyquist_xmax=args.nyquist_xmax,
            )
        )

    if len(rows) > 1:
        summary_dir = RESULTS_ROOT / "qaoa"
        summary_dir.mkdir(parents=True, exist_ok=True)

        summary_path = summary_dir / f"qaoa_simulator_p{args.p}_summary.csv"

        pd.DataFrame(rows).to_csv(summary_path, index=False)

        print(f"\nCombined summary: {summary_path}")

if __name__ == "__main__":
    main()
