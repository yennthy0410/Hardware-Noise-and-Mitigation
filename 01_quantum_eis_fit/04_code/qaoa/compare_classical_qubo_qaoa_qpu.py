from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colors as mcolors


# ============================================================
# Project paths
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

METHOD_ORDER = [
    "Classical",
    "Exact QUBO",
    "QAOA Simulator",
    "QAOA QPU",
]

METHOD_STYLES = {
    "Classical": {"ls": "-", "lw": 2.4},
    "Exact QUBO": {"ls": "--", "lw": 2.2},
    "QAOA Simulator": {"ls": "-.", "lw": 2.2},
    "QAOA QPU": {"ls": ":", "lw": 2.6},
}

# Fixed method colors for SSE/RMSE figures.
# These colors stay consistent across the report.
METHOD_COLORS = {
    "Classical": "#1f77b4",       # blue
    "Exact QUBO": "#2ca02c",      # green
    "QAOA Simulator": "#ff7f0e",  # orange
    "QAOA QPU": "#d62728",        # red
}

METHOD_EDGE_COLORS = {
    "Classical": "#0d3d66",
    "Exact QUBO": "#1b6b1b",
    "QAOA Simulator": "#a94f00",
    "QAOA QPU": "#8c1c13",
}


# ============================================================
# Generic helpers
# ============================================================


def save_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")



def save_figure(fig, path: Path):
    fig.savefig(path, dpi=300, bbox_inches="tight")
    fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)



def hamming_distance(a, b):
    if not a or not b or len(a) != len(b):
        return np.nan
    return int(sum(x != y for x, y in zip(a, b)))



def validate_classical_root():
    expected = (PROJECT_ROOT / "reference_results" / "classical").resolve()
    actual = CLASSICAL_ROOT.resolve()
    if actual != expected:
        raise RuntimeError(
            "Classical source must be reference_results/classical.\n"
            f"Expected: {expected}\nActual: {actual}"
        )



def fmt_value(x):
    if pd.isna(x):
        return "NaN"
    x = float(x)
    if x == 0:
        return "0"
    if abs(x) >= 1e3 or abs(x) < 1e-3:
        return f"{x:.4e}"
    return f"{x:.6f}".rstrip("0").rstrip(".")



def fmt_percent(x):
    if pd.isna(x):
        return "NaN"
    sign = "+" if float(x) > 0 else ""
    return f"{sign}{float(x):.2f}%"



def soften_color(color, mix_with="white", alpha=0.55):
    base = np.array(mcolors.to_rgb(color))
    mix = np.array(mcolors.to_rgb(mix_with))
    return tuple((1 - alpha) * base + alpha * mix)



def error_color(error_percent, vmax=30.0):
    """Green = small error, yellow = medium, red = large error."""
    if pd.isna(error_percent):
        return (1, 1, 1, 1)
    value = min(abs(float(error_percent)), vmax) / vmax
    rgba = plt.get_cmap("RdYlGn_r")(value)
    soft = soften_color(rgba[:3], mix_with="white", alpha=0.45)
    return (*soft, 1.0)



def ratio_color(value, vmin=0.7, vmax=1.6):
    if pd.isna(value):
        return (1, 1, 1, 1)
    x = float(value)
    x = max(vmin, min(vmax, x))
    norm = (x - vmin) / (vmax - vmin)
    rgba = plt.get_cmap("RdYlGn_r")(norm)
    soft = soften_color(rgba[:3], mix_with="white", alpha=0.40)
    return (*soft, 1.0)



def rel_error_percent(value, reference):
    reference = float(reference)
    value = float(value)
    if reference == 0:
        return np.nan
    return 100.0 * (value - reference) / abs(reference)



def relative_l2(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = np.linalg.norm(a)
    if denom == 0:
        return np.nan
    return float(np.linalg.norm(b - a) / denom)



def ensure_columns(df: pd.DataFrame, required, path: Path):
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns: {missing}")


# ============================================================
# Classical reference
# ============================================================


def load_classical_parameters(dataset: str):
    path = CLASSICAL_ROOT / dataset / "fit_parameters.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing Classical parameters:\n{path}")

    df = pd.read_csv(path)
    lower = {str(c).strip().lower(): c for c in df.columns}

    if "parameter" in lower and "value" in lower:
        pcol = lower["parameter"]
        vcol = lower["value"]
        mapping = {str(row[pcol]).strip(): float(row[vcol]) for _, row in df.iterrows()}
        params = {name: float(mapping[name]) for name in PARAMETER_ORDER}
    elif all(name in df.columns for name in PARAMETER_ORDER):
        row = df.iloc[0]
        params = {name: float(row[name]) for name in PARAMETER_ORDER}
    else:
        raise ValueError(
            f"Cannot parse Classical parameter file:\n{path}\nColumns: {list(df.columns)}"
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
    ensure_columns(df, required, path)

    frequency = df["frequency_hz"].to_numpy(dtype=float)
    z_exp = df["z_real_data_ohm"].to_numpy(dtype=float) - 1j * df[
        "minus_z_imag_data_ohm"
    ].to_numpy(dtype=float)
    z_classical = df["z_real_fit_ohm"].to_numpy(dtype=float) - 1j * df[
        "minus_z_imag_fit_ohm"
    ].to_numpy(dtype=float)

    order = np.argsort(frequency)
    return frequency[order], z_exp[order], z_classical[order], path


# ============================================================
# Exact QUBO
# ============================================================


def load_exact_qubo(dataset: str):
    path = RESULTS_ROOT / dataset / "ising" / "ising_model.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing Ising model:\n{path}")

    model = json.loads(path.read_text(encoding="utf-8"))
    validation = model.get("energy_equivalence_validation", {})
    if validation and validation.get("passed") is not True:
        raise RuntimeError(f"{dataset}: QUBO -> Ising validation did not PASS.")
    if model.get("source_qubo_minimum_match") is not True:
        raise RuntimeError(f"{dataset}: QUBO / Ising exact minimum mismatch.")

    exact = model.get("exact_ising_minimum", {})
    bitstring = str(exact.get("bitstring", "")).strip()
    if not bitstring:
        raise KeyError(f"{dataset}: exact_ising_minimum.bitstring missing.")

    decoded = decode_bitstring(bitstring)
    params = {name: float(decoded["physical_parameters"][name]) for name in PARAMETER_ORDER}
    vector = np.asarray(decoded["parameter_vector"], dtype=float)
    energy = None if exact.get("ising_energy") is None else float(exact["ising_energy"])
    return bitstring, energy, params, vector, path


# ============================================================
# QAOA simulator
# ============================================================


def qaoa_simulator_paths(dataset: str, p: int):
    simulator = RESULTS_ROOT / dataset / "qaoa" / "simulator" / f"p{p}"
    optimizer = RESULTS_ROOT / dataset / "qaoa" / "optimizer" / f"p{p}"
    return {
        "report": simulator / "qaoa_simulator_report.json",
        "parameters": simulator / "qaoa_simulator_parameters.csv",
        "optimizer": optimizer / "qaoa_optimal_parameters.json",
    }



def extract_qaoa_bitstring(report: dict):
    candidates = [
        report.get("qaoa_candidate", {}).get("bitstring"),
        report.get("selected_qaoa_eis_candidate", {}).get("bitstring"),
        report.get("recommended_eis_candidate_bitstring"),
    ]
    for value in candidates:
        if value:
            return str(value).strip()
    return None



def load_qaoa_simulator(dataset: str, p: int):
    paths = qaoa_simulator_paths(dataset, p)

    if paths["report"].exists():
        report = json.loads(paths["report"].read_text(encoding="utf-8"))
        bitstring = extract_qaoa_bitstring(report)
        if bitstring:
            decoded = decode_bitstring(bitstring)
            params = {name: float(decoded["physical_parameters"][name]) for name in PARAMETER_ORDER}
            vector = np.asarray(decoded["parameter_vector"], dtype=float)
            return bitstring, params, vector, paths["report"], "simulator_report"

    if paths["parameters"].exists():
        df = pd.read_csv(paths["parameters"])
        if "parameter" in df.columns and "qaoa_simulator_value" in df.columns:
            mapping = {str(row["parameter"]).strip(): float(row["qaoa_simulator_value"]) for _, row in df.iterrows()}
            if all(name in mapping for name in PARAMETER_ORDER):
                params = {name: float(mapping[name]) for name in PARAMETER_ORDER}
                vector = np.asarray([params[name] for name in PARAMETER_ORDER], dtype=float)
                bitstring = None
                if "bits" in df.columns:
                    groups = []
                    for name in PARAMETER_ORDER:
                        rows = df[df["parameter"].astype(str) == name]
                        if rows.empty:
                            groups = []
                            break
                        groups.append(str(rows.iloc[0]["bits"]).strip())
                    if groups:
                        bitstring = "".join(groups)
                return bitstring, params, vector, paths["parameters"], "simulator_parameters_csv"

    if paths["optimizer"].exists():
        report = json.loads(paths["optimizer"].read_text(encoding="utf-8"))
        bitstring = report.get("recommended_eis_candidate_bitstring")
        if bitstring:
            bitstring = str(bitstring).strip()
            decoded = decode_bitstring(bitstring)
            params = {name: float(decoded["physical_parameters"][name]) for name in PARAMETER_ORDER}
            vector = np.asarray(decoded["parameter_vector"], dtype=float)
            return bitstring, params, vector, paths["optimizer"], "optimizer_recommended_candidate"

    raise FileNotFoundError(
        f"No QAOA simulator result found for {dataset} p={p}.\n"
        f"Searched:\n  {paths['report']}\n  {paths['parameters']}\n  {paths['optimizer']}"
    )


# ============================================================
# QAOA QPU
# ============================================================


def qpu_result_dir(dataset: str, p: int):
    return RESULTS_ROOT / dataset / "qpu" / f"p{p}"



def load_qaoa_qpu(dataset: str, p: int):
    root = qpu_result_dir(dataset, p)
    if not root.exists():
        raise FileNotFoundError(f"Missing QPU result directory:\n{root}")

    # 1) Prefer evaluated parameter CSV if it exists.
    param_csv = root / "eis_evaluation" / "qpu_vs_classical_parameters.csv"
    if param_csv.exists():
        df = pd.read_csv(param_csv)
        if "parameter" in df.columns:
            qpu_col = None
            for candidate in ["qaoa_qpu", "qpu_value", "qpu", "value"]:
                if candidate in df.columns:
                    qpu_col = candidate
                    break
            if qpu_col is not None:
                mapping = {str(row["parameter"]).strip(): float(row[qpu_col]) for _, row in df.iterrows()}
                if all(name in mapping for name in PARAMETER_ORDER):
                    params = {name: float(mapping[name]) for name in PARAMETER_ORDER}
                    vector = np.asarray([params[name] for name in PARAMETER_ORDER], dtype=float)
                    bitstring = None
                    if "qpu_bits" in df.columns:
                        rows = [str(df[df["parameter"] == name].iloc[0]["qpu_bits"]).strip() for name in PARAMETER_ORDER]
                        bitstring = "".join(rows)
                    return bitstring, params, vector, param_csv, "qpu_evaluation_csv"

    # 2) Raw parameter JSON from hardware run.
    parameter_json = root / "qpu_best_observed_parameters.json"
    if parameter_json.exists():
        data = json.loads(parameter_json.read_text(encoding="utf-8"))
        bitstring = data.get("canonical_bitstring")
        if bitstring:
            decoded = decode_bitstring(str(bitstring).strip())
            params = {name: float(decoded["physical_parameters"][name]) for name in PARAMETER_ORDER}
            vector = np.asarray(decoded["parameter_vector"], dtype=float)
            return str(bitstring).strip(), params, vector, parameter_json, "qpu_best_observed_parameters"

    # 3) Summary JSON fallback.
    summary_json = root / "qpu_result_summary.json"
    if summary_json.exists():
        data = json.loads(summary_json.read_text(encoding="utf-8"))
        bitstring = data.get("best_observed_canonical_bitstring")
        if bitstring:
            decoded = decode_bitstring(str(bitstring).strip())
            params = {name: float(decoded["physical_parameters"][name]) for name in PARAMETER_ORDER}
            vector = np.asarray(decoded["parameter_vector"], dtype=float)
            return str(bitstring).strip(), params, vector, summary_json, "qpu_result_summary"

    # 4) Ranked counts fallback: pick state with lowest Ising energy.
    counts_csv = root / "qpu_counts_energy_ranked.csv"
    if counts_csv.exists():
        df = pd.read_csv(counts_csv)
        ensure_columns(df, ["canonical_bitstring", "ising_energy"], counts_csv)
        if df.empty:
            raise ValueError(f"{counts_csv} is empty.")
        best_index = df["ising_energy"].astype(float).idxmin()
        bitstring = str(df.loc[best_index, "canonical_bitstring"]).strip()
        decoded = decode_bitstring(bitstring)
        params = {name: float(decoded["physical_parameters"][name]) for name in PARAMETER_ORDER}
        vector = np.asarray(decoded["parameter_vector"], dtype=float)
        return bitstring, params, vector, counts_csv, "qpu_counts_energy_ranked"

    raise FileNotFoundError(
        "No usable QPU result found.\n"
        f"Checked under: {root}\n"
        "Need one of:\n"
        "  eis_evaluation/qpu_vs_classical_parameters.csv\n"
        "  qpu_best_observed_parameters.json\n"
        "  qpu_result_summary.json\n"
        "  qpu_counts_energy_ranked.csv"
    )


# ============================================================
# Metrics and data assembly
# ============================================================


def evaluate(evaluator: EISEvaluator, vector):
    result = evaluator.evaluate(np.asarray(vector, dtype=float), return_arrays=False)
    return {
        "weighted_complex_loss": float(result["weighted_complex_loss"]),
        "SSE": float(result["SSE"]),
        "RMSE": float(result["RMSE"]),
    }



def method_record(label, vector, bitstring, source_path, source_type, z_fit, metrics):
    params = {name: float(vector[i]) for i, name in enumerate(PARAMETER_ORDER)}
    return {
        "label": label,
        "vector": np.asarray(vector, dtype=float),
        "params": params,
        "bitstring": bitstring,
        "source_path": str(source_path),
        "source_type": source_type,
        "z_fit": np.asarray(z_fit, dtype=complex),
        "metrics": metrics,
    }


# ============================================================
# Figures
# ============================================================


def plot_nyquist(output_dir, dataset, p, z_exp, methods, x_max):
    fig, ax = plt.subplots(figsize=(8.0, 6.4))
    ax.scatter(z_exp.real, -z_exp.imag, s=28, label="Experimental", zorder=6)
    for label in METHOD_ORDER:
        style = METHOD_STYLES[label]
        z = methods[label]["z_fit"]
        suffix = f" (p={p})" if "QAOA" in label else ""
        ax.plot(z.real, -z.imag, linestyle=style["ls"], linewidth=style["lw"], label=label + suffix)
    ax.set_title(f"{dataset} - Nyquist: Classical vs QUBO vs QAOA vs QPU")
    ax.set_xlabel(r"$Z'$ ($\Omega$)")
    ax.set_ylabel(r"$-Z''$ ($\Omega$)")
    ax.grid(True, alpha=0.28)
    ax.legend()
    if x_max is not None:
        ax.set_xlim(0.0, float(x_max))
    fig.tight_layout()
    save_figure(fig, output_dir / "nyquist_classical_qubo_qaoa_qpu.png")



def plot_bode(output_dir, dataset, p, frequency, z_exp, methods):
    fig, axes = plt.subplots(2, 1, figsize=(8.8, 8.6), sharex=True)

    ax = axes[0]
    ax.scatter(frequency, np.abs(z_exp), s=24, label="Experimental", zorder=6)
    for label in METHOD_ORDER:
        style = METHOD_STYLES[label]
        z = methods[label]["z_fit"]
        suffix = f" (p={p})" if "QAOA" in label else ""
        ax.plot(frequency, np.abs(z), linestyle=style["ls"], linewidth=style["lw"], label=label + suffix)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylabel(r"$|Z|$ ($\Omega$)")
    ax.set_title(f"{dataset} - Bode: Classical vs QUBO vs QAOA vs QPU")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    ax = axes[1]
    ax.scatter(frequency, np.angle(z_exp, deg=True), s=24, label="Experimental", zorder=6)
    for label in METHOD_ORDER:
        style = METHOD_STYLES[label]
        z = methods[label]["z_fit"]
        suffix = f" (p={p})" if "QAOA" in label else ""
        ax.plot(frequency, np.angle(z, deg=True), linestyle=style["ls"], linewidth=style["lw"], label=label + suffix)
    ax.set_xscale("log")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Phase (degree)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    fig.tight_layout()
    save_figure(fig, output_dir / "bode_classical_qubo_qaoa_qpu.png")



def plot_residuals(output_dir, dataset, p, frequency, z_exp, methods):
    fig, axes = plt.subplots(2, 1, figsize=(8.8, 8.4), sharex=True)

    ax = axes[0]
    for label in METHOD_ORDER:
        style = METHOD_STYLES[label]
        r = methods[label]["z_fit"] - z_exp
        suffix = f" (p={p})" if "QAOA" in label else ""
        ax.plot(frequency, r.real, linestyle=style["ls"], linewidth=style["lw"] - 0.5, label=label + suffix)
    ax.axhline(0.0, linewidth=1.0)
    ax.set_xscale("log")
    ax.set_ylabel(r"$\Delta Z'$ ($\Omega$)")
    ax.set_title(f"{dataset} - Residuals (Fit - Experimental)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    ax = axes[1]
    for label in METHOD_ORDER:
        style = METHOD_STYLES[label]
        r = methods[label]["z_fit"] - z_exp
        suffix = f" (p={p})" if "QAOA" in label else ""
        ax.plot(frequency, r.imag, linestyle=style["ls"], linewidth=style["lw"] - 0.5, label=label + suffix)
    ax.axhline(0.0, linewidth=1.0)
    ax.set_xscale("log")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel(r"$\Delta Z''$ ($\Omega$)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    fig.tight_layout()
    save_figure(fig, output_dir / "residuals_classical_qubo_qaoa_qpu.png")



def plot_sse_rmse_bars(output_dir: Path, dataset: str, metrics_df: pd.DataFrame):
    """
    Publication-style SSE/RMSE comparison.

    Fixed method colors:
        Classical      = blue
        Exact QUBO     = green
        QAOA Simulator = orange
        QAOA QPU       = red

    Lower SSE/RMSE means better EIS fitting accuracy.
    """
    ordered = (
        metrics_df
        .set_index("method")
        .loc[METHOD_ORDER]
        .reset_index()
    )

    methods = ordered["method"].tolist()
    sse_values = ordered["SSE"].to_numpy(dtype=float)
    rmse_values = ordered["RMSE"].to_numpy(dtype=float)

    colors = [
        METHOD_COLORS[
            method
        ]
        for method in methods
    ]

    edgecolors = [
        METHOD_EDGE_COLORS[
            method
        ]
        for method in methods
    ]

    x = np.arange(
        len(
            methods
        )
    )

    def annotate_bars(
        ax,
        bars,
        values,
    ):
        ymax = float(
            np.nanmax(
                values
            )
        ) if len(values) else 1.0

        if (
            not np.isfinite(
                ymax
            )
            or ymax <= 0
        ):
            ymax = 1.0

        for bar, value in zip(
            bars,
            values,
        ):
            ax.text(
                bar.get_x()
                + bar.get_width()
                / 2.0,

                bar.get_height()
                + 0.018
                * ymax,

                f"{value:.3g}",

                ha="center",
                va="bottom",
                fontsize=9.5,
                fontweight="bold",
            )

        ax.set_ylim(
            0,
            ymax
            * 1.14,
        )

    def style_axis(
        ax,
        metric_name,
    ):
        ax.set_xticks(
            x
        )

        ax.set_xticklabels(
            methods,
            rotation=20,
            ha="right",
            fontsize=10.5,
        )

        ax.set_ylabel(
            metric_name,
            fontsize=11.5,
        )

        ax.set_title(
            f"{dataset} - {metric_name} comparison",
            fontsize=13.5,
            fontweight="bold",
        )

        ax.grid(
            True,
            axis="y",
            alpha=0.24,
            linestyle="--",
            linewidth=0.8,
        )

        ax.set_axisbelow(
            True
        )

    # ========================================================
    # Combined SSE + RMSE figure
    # ========================================================

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(
            12.0,
            5.0,
        ),
    )

    bars_sse = axes[
        0
    ].bar(
        x,
        sse_values,
        color=colors,
        edgecolor=edgecolors,
        linewidth=1.15,
        width=0.64,
    )

    style_axis(
        axes[
            0
        ],
        "SSE",
    )

    annotate_bars(
        axes[
            0
        ],
        bars_sse,
        sse_values,
    )

    axes[
        0
    ].text(
        0.99,
        0.97,
        "Lower is better",
        transform=axes[
            0
        ].transAxes,
        ha="right",
        va="top",
        fontsize=9.2,
        style="italic",
    )

    bars_rmse = axes[
        1
    ].bar(
        x,
        rmse_values,
        color=colors,
        edgecolor=edgecolors,
        linewidth=1.15,
        width=0.64,
    )

    style_axis(
        axes[
            1
        ],
        "RMSE",
    )

    annotate_bars(
        axes[
            1
        ],
        bars_rmse,
        rmse_values,
    )

    axes[
        1
    ].text(
        0.99,
        0.97,
        "Lower is better",
        transform=axes[
            1
        ].transAxes,
        ha="right",
        va="top",
        fontsize=9.2,
        style="italic",
    )

    fig.suptitle(
        f"{dataset} - EIS fitting accuracy across all stages",
        fontsize=14.5,
        fontweight="bold",
        y=1.02,
    )

    fig.tight_layout()

    save_figure(
        fig,
        output_dir
        / "sse_rmse_classical_qubo_qaoa_qpu.png",
    )

    # ========================================================
    # Separate SSE / RMSE figures
    # ========================================================

    for (
        metric_name,
        values,
        filename,
    ) in [
        (
            "SSE",
            sse_values,
            "sse_classical_qubo_qaoa_qpu.png",
        ),
        (
            "RMSE",
            rmse_values,
            "rmse_classical_qubo_qaoa_qpu.png",
        ),
    ]:
        fig, ax = plt.subplots(
            figsize=(
                8.0,
                5.4,
            )
        )

        bars = ax.bar(
            x,
            values,
            color=colors,
            edgecolor=edgecolors,
            linewidth=1.2,
            width=0.62,
        )

        style_axis(
            ax,
            metric_name,
        )

        annotate_bars(
            ax,
            bars,
            values,
        )

        ax.text(
            0.99,
            0.97,
            "Lower is better",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=9.5,
            style="italic",
        )

        fig.tight_layout()

        save_figure(
            fig,
            output_dir
            / filename,
        )


def plot_parameter_table(output_dir: Path, dataset: str, table_df: pd.DataFrame):
    display_df = table_df.copy()

    for col in ["classical", "exact_qubo", "qaoa_simulator", "qaoa_qpu"]:
        display_df[col] = display_df[col].map(fmt_value)

    for col in [
        "exact_qubo_rel_err_pct",
        "qaoa_sim_rel_err_pct",
        "qaoa_qpu_rel_err_pct",
    ]:
        display_df[col] = display_df[col].map(fmt_percent)

    display_df = display_df[
        [
            "parameter",
            "unit",
            "classical",
            "exact_qubo",
            "qaoa_simulator",
            "qaoa_qpu",
            "exact_qubo_rel_err_pct",
            "qaoa_sim_rel_err_pct",
            "qaoa_qpu_rel_err_pct",
        ]
    ]
    display_df.columns = [
        "Parameter",
        "Unit",
        "Classical",
        "Exact QUBO",
        "QAOA Sim",
        "QAOA QPU",
        "QUBO err vs C",
        "QAOA err vs C",
        "QPU err vs C",
    ]

    fig, ax = plt.subplots(figsize=(15.8, 4.8))
    ax.axis("off")
    ax.set_title(f"{dataset} - 7-parameter comparison and relative error vs Classical", pad=14)
    table = ax.table(
        cellText=display_df.values,
        colLabels=display_df.columns,
        cellLoc="center",
        colLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.55)

    header_color = soften_color("lightsteelblue", alpha=0.35)
    nrows = len(display_df) + 1
    ncols = len(display_df.columns)

    for c in range(ncols):
        table[(0, c)].set_facecolor(header_color)
        table[(0, c)].set_text_props(weight="bold")

    error_cols = {
        6: "exact_qubo_rel_err_pct",
        7: "qaoa_sim_rel_err_pct",
        8: "qaoa_qpu_rel_err_pct",
    }
    for r in range(1, nrows):
        for c in range(ncols):
            cell = table[(r, c)]
            cell.set_edgecolor("0.75")
            if c in error_cols:
                err = table_df.iloc[r - 1][error_cols[c]]
                cell.set_facecolor(error_color(err))

    fig.tight_layout()
    save_figure(fig, output_dir / "parameter_comparison_table.png")



def plot_relative_error_heatmap(output_dir: Path, dataset: str, table_df: pd.DataFrame):
    heatmap_df = table_df.set_index("parameter")[[
        "exact_qubo_rel_err_pct",
        "qaoa_sim_rel_err_pct",
        "qaoa_qpu_rel_err_pct",
    ]].copy()
    heatmap_df.columns = ["Exact QUBO", "QAOA Sim", "QAOA QPU"]
    data = heatmap_df.to_numpy(dtype=float)

    vmax = float(max(5.0, np.nanmax(np.abs(data))))
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    im = ax.imshow(data, cmap="coolwarm", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(np.arange(data.shape[1]), heatmap_df.columns)
    ax.set_yticks(np.arange(data.shape[0]), heatmap_df.index)
    ax.set_title(f"{dataset} - Relative error (%) vs Classical")

    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            value = data[i, j]
            label = "NaN" if np.isnan(value) else f"{value:+.1f}"
            ax.text(j, i, label, ha="center", va="center", fontsize=9)

    cbar = fig.colorbar(im, ax=ax, shrink=0.9)
    cbar.set_label("Relative error (%)")
    fig.tight_layout()
    save_figure(fig, output_dir / "relative_error_heatmap.png")



def plot_metrics_table(output_dir: Path, dataset: str, metrics_df: pd.DataFrame):
    display_df = metrics_df.copy()
    for col in ["weighted_complex_loss", "SSE", "RMSE", "SSE_ratio_to_classical", "RMSE_ratio_to_classical"]:
        display_df[col] = display_df[col].map(fmt_value)
    display_df = display_df[[
        "method",
        "weighted_complex_loss",
        "SSE",
        "RMSE",
        "SSE_ratio_to_classical",
        "RMSE_ratio_to_classical",
        "sse_rank",
        "rmse_rank",
    ]]
    display_df.columns = [
        "Method",
        "Weighted loss",
        "SSE",
        "RMSE",
        "SSE/C",
        "RMSE/C",
        "SSE rank",
        "RMSE rank",
    ]

    fig, ax = plt.subplots(figsize=(11.0, 3.7))
    ax.axis("off")
    ax.set_title(f"{dataset} - Method-level fit metrics summary", pad=12)
    table = ax.table(
        cellText=display_df.values,
        colLabels=display_df.columns,
        cellLoc="center",
        colLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.55)

    header_color = soften_color("lightsteelblue", alpha=0.35)
    nrows = len(display_df) + 1
    ncols = len(display_df.columns)
    for c in range(ncols):
        table[(0, c)].set_facecolor(header_color)
        table[(0, c)].set_text_props(weight="bold")

    for r in range(1, nrows):
        for c in range(ncols):
            cell = table[(r, c)]
            cell.set_edgecolor("0.75")
            if c == 4:
                cell.set_facecolor(ratio_color(metrics_df.iloc[r - 1]["SSE_ratio_to_classical"]))
            if c == 5:
                cell.set_facecolor(ratio_color(metrics_df.iloc[r - 1]["RMSE_ratio_to_classical"]))

    fig.tight_layout()
    save_figure(fig, output_dir / "metrics_summary_table.png")



def plot_stage_transition_table(output_dir: Path, dataset: str, stage_df: pd.DataFrame):
    display_df = stage_df.copy()
    for col in [
        "mean_abs_rel_change_pct",
        "max_abs_rel_change_pct",
        "parameter_relative_l2",
        "SSE_ratio_to_previous",
        "RMSE_ratio_to_previous",
    ]:
        display_df[col] = display_df[col].map(fmt_value)

    display_df = display_df[[
        "from_method",
        "to_method",
        "mean_abs_rel_change_pct",
        "max_abs_rel_change_pct",
        "parameter_relative_l2",
        "SSE_ratio_to_previous",
        "RMSE_ratio_to_previous",
        "bitstring_hamming_distance",
    ]]
    display_df.columns = [
        "From",
        "To",
        "Mean |rel Δ| %",
        "Max |rel Δ| %",
        "Param rel L2",
        "SSE ratio",
        "RMSE ratio",
        "Hamming d",
    ]

    fig, ax = plt.subplots(figsize=(12.8, 3.8))
    ax.axis("off")
    ax.set_title(f"{dataset} - Transition summary across stages", pad=12)
    table = ax.table(
        cellText=display_df.values,
        colLabels=display_df.columns,
        cellLoc="center",
        colLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.55)

    header_color = soften_color("lightsteelblue", alpha=0.35)
    nrows = len(display_df) + 1
    ncols = len(display_df.columns)
    for c in range(ncols):
        table[(0, c)].set_facecolor(header_color)
        table[(0, c)].set_text_props(weight="bold")

    for r in range(1, nrows):
        for c in range(ncols):
            cell = table[(r, c)]
            cell.set_edgecolor("0.75")
            if c == 2:
                cell.set_facecolor(error_color(stage_df.iloc[r - 1]["mean_abs_rel_change_pct"], vmax=40.0))
            elif c == 5:
                cell.set_facecolor(ratio_color(stage_df.iloc[r - 1]["SSE_ratio_to_previous"], vmin=0.7, vmax=1.5))
            elif c == 6:
                cell.set_facecolor(ratio_color(stage_df.iloc[r - 1]["RMSE_ratio_to_previous"], vmin=0.7, vmax=1.5))

    fig.tight_layout()
    save_figure(fig, output_dir / "stage_transition_summary_table.png")


# ============================================================
# Table builders
# ============================================================


def build_parameter_table(methods: dict):
    rows = []
    classical = methods["Classical"]["params"]
    qubo = methods["Exact QUBO"]["params"]
    qaoa = methods["QAOA Simulator"]["params"]
    qpu = methods["QAOA QPU"]["params"]

    for name in PARAMETER_ORDER:
        c = float(classical[name])
        q = float(qubo[name])
        qa = float(qaoa[name])
        qp = float(qpu[name])
        rows.append(
            {
                "parameter": name,
                "unit": PARAMETER_UNITS[name],
                "classical": c,
                "exact_qubo": q,
                "qaoa_simulator": qa,
                "qaoa_qpu": qp,
                "exact_qubo_rel_err_pct": rel_error_percent(q, c),
                "qaoa_sim_rel_err_pct": rel_error_percent(qa, c),
                "qaoa_qpu_rel_err_pct": rel_error_percent(qp, c),
                "qubo_to_qaoa_rel_change_pct": rel_error_percent(qa, q),
                "qaoa_to_qpu_rel_change_pct": rel_error_percent(qp, qa),
            }
        )
    return pd.DataFrame(rows)



def build_metrics_table(methods: dict):
    rows = []
    classical_sse = methods["Classical"]["metrics"]["SSE"]
    classical_rmse = methods["Classical"]["metrics"]["RMSE"]
    for label in METHOD_ORDER:
        m = methods[label]["metrics"]
        rows.append(
            {
                "method": label,
                "weighted_complex_loss": float(m["weighted_complex_loss"]),
                "SSE": float(m["SSE"]),
                "RMSE": float(m["RMSE"]),
                "SSE_ratio_to_classical": float(m["SSE"]) / classical_sse if classical_sse != 0 else np.nan,
                "RMSE_ratio_to_classical": float(m["RMSE"]) / classical_rmse if classical_rmse != 0 else np.nan,
            }
        )
    df = pd.DataFrame(rows)
    df["sse_rank"] = df["SSE"].rank(method="dense", ascending=True).astype(int)
    df["rmse_rank"] = df["RMSE"].rank(method="dense", ascending=True).astype(int)
    return df



def build_stage_transition_table(methods: dict):
    transitions = [
        ("Classical", "Exact QUBO"),
        ("Exact QUBO", "QAOA Simulator"),
        ("QAOA Simulator", "QAOA QPU"),
        ("Classical", "QAOA QPU"),
    ]
    rows = []
    for src, dst in transitions:
        a = methods[src]["vector"]
        b = methods[dst]["vector"]
        rel = []
        for av, bv in zip(a, b):
            if av == 0:
                rel.append(np.nan)
            else:
                rel.append(100.0 * (bv - av) / abs(av))
        rel = np.asarray(rel, dtype=float)

        sse_src = methods[src]["metrics"]["SSE"]
        sse_dst = methods[dst]["metrics"]["SSE"]
        rmse_src = methods[src]["metrics"]["RMSE"]
        rmse_dst = methods[dst]["metrics"]["RMSE"]

        rows.append(
            {
                "from_method": src,
                "to_method": dst,
                "mean_abs_rel_change_pct": float(np.nanmean(np.abs(rel))),
                "max_abs_rel_change_pct": float(np.nanmax(np.abs(rel))),
                "parameter_relative_l2": relative_l2(a, b),
                "SSE_ratio_to_previous": float(sse_dst / sse_src) if sse_src != 0 else np.nan,
                "RMSE_ratio_to_previous": float(rmse_dst / rmse_src) if rmse_src != 0 else np.nan,
                "bitstring_hamming_distance": hamming_distance(methods[src]["bitstring"], methods[dst]["bitstring"]),
            }
        )
    return pd.DataFrame(rows)



def build_bitstring_table(methods: dict):
    ref = methods["Exact QUBO"]["bitstring"]
    rows = []
    for label in METHOD_ORDER:
        rows.append(
            {
                "method": label,
                "bitstring": methods[label]["bitstring"],
                "hamming_to_exact_qubo": hamming_distance(methods[label]["bitstring"], ref),
                "source_type": methods[label]["source_type"],
                "source_path": methods[label]["source_path"],
            }
        )
    return pd.DataFrame(rows)


# ============================================================
# Main compare logic
# ============================================================


def compare_dataset(dataset: str, p: int, nyquist_xmax=None):
    dataset = dataset.upper()
    validate_classical_root()

    print()
    print("=" * 90)
    print(f"{dataset} — CLASSICAL vs EXACT QUBO vs QAOA SIMULATOR vs QAOA QPU (p={p})")
    print("=" * 90)

    output_dir = RESULTS_ROOT / dataset / "comparison" / "classical_qubo_qaoa_qpu"
    output_dir.mkdir(parents=True, exist_ok=True)

    classical_params, classical_vector, classical_param_path = load_classical_parameters(dataset)
    frequency, z_exp, z_classical, classical_spectrum_path = load_classical_spectrum(dataset)

    qubo_bitstring, qubo_energy, qubo_params, qubo_vector, qubo_path = load_exact_qubo(dataset)
    qaoa_bitstring, qaoa_params, qaoa_vector, qaoa_path, qaoa_type = load_qaoa_simulator(dataset, p)
    qpu_bitstring, qpu_params, qpu_vector, qpu_path, qpu_type = load_qaoa_qpu(dataset, p)

    z_qubo = np.asarray(impedance_model(frequency, *qubo_vector), dtype=complex)
    z_qaoa = np.asarray(impedance_model(frequency, *qaoa_vector), dtype=complex)
    z_qpu = np.asarray(impedance_model(frequency, *qpu_vector), dtype=complex)

    evaluator = EISEvaluator(dataset)
    m_classical = evaluate(evaluator, classical_vector)
    m_qubo = evaluate(evaluator, qubo_vector)
    m_qaoa = evaluate(evaluator, qaoa_vector)
    m_qpu = evaluate(evaluator, qpu_vector)

    methods = {
        "Classical": method_record("Classical", classical_vector, None, classical_param_path, "reference_results/classical", z_classical, m_classical),
        "Exact QUBO": method_record("Exact QUBO", qubo_vector, qubo_bitstring, qubo_path, "exact_ising_ground", z_qubo, m_qubo),
        "QAOA Simulator": method_record("QAOA Simulator", qaoa_vector, qaoa_bitstring, qaoa_path, qaoa_type, z_qaoa, m_qaoa),
        "QAOA QPU": method_record("QAOA QPU", qpu_vector, qpu_bitstring, qpu_path, qpu_type, z_qpu, m_qpu),
    }
    methods["Classical"]["params"] = classical_params
    methods["Exact QUBO"]["params"] = qubo_params
    methods["QAOA Simulator"]["params"] = qaoa_params
    methods["QAOA QPU"]["params"] = qpu_params

    # CSV tables
    parameter_df = build_parameter_table(methods)
    parameter_df.to_csv(output_dir / "parameter_comparison.csv", index=False)

    metrics_df = build_metrics_table(methods)
    metrics_df.to_csv(output_dir / "metrics_summary.csv", index=False)

    stage_df = build_stage_transition_table(methods)
    stage_df.to_csv(output_dir / "stage_transition_summary.csv", index=False)

    bitstring_df = build_bitstring_table(methods)
    bitstring_df.to_csv(output_dir / "bitstring_summary.csv", index=False)

    # Pointwise spectrum CSV for all methods
    spectrum_df = pd.DataFrame(
        {
            "frequency_hz": frequency,
            "z_exp_real_ohm": z_exp.real,
            "z_exp_imag_ohm": z_exp.imag,
            "minus_z_exp_imag_ohm": -z_exp.imag,
            "z_exp_abs_ohm": np.abs(z_exp),
            "z_exp_phase_deg": np.angle(z_exp, deg=True),
            "z_classical_real_ohm": z_classical.real,
            "z_classical_imag_ohm": z_classical.imag,
            "z_qubo_real_ohm": z_qubo.real,
            "z_qubo_imag_ohm": z_qubo.imag,
            "z_qaoa_real_ohm": z_qaoa.real,
            "z_qaoa_imag_ohm": z_qaoa.imag,
            "z_qpu_real_ohm": z_qpu.real,
            "z_qpu_imag_ohm": z_qpu.imag,
            "classical_pointwise_sq_error": np.abs(z_classical - z_exp) ** 2,
            "qubo_pointwise_sq_error": np.abs(z_qubo - z_exp) ** 2,
            "qaoa_pointwise_sq_error": np.abs(z_qaoa - z_exp) ** 2,
            "qpu_pointwise_sq_error": np.abs(z_qpu - z_exp) ** 2,
        }
    )
    spectrum_df.to_csv(output_dir / "fitted_spectra_all_methods.csv", index=False)

    # Figures
    plot_nyquist(output_dir, dataset, p, z_exp, methods, nyquist_xmax)
    plot_bode(output_dir, dataset, p, frequency, z_exp, methods)
    plot_residuals(output_dir, dataset, p, frequency, z_exp, methods)
    plot_sse_rmse_bars(output_dir, dataset, metrics_df)
    plot_parameter_table(output_dir, dataset, parameter_df)
    plot_relative_error_heatmap(output_dir, dataset, parameter_df)
    plot_metrics_table(output_dir, dataset, metrics_df)
    plot_stage_transition_table(output_dir, dataset, stage_df)

    report = {
        "dataset": dataset,
        "qaoa_p": int(p),
        "objective": "Assess whether QAOA is a useful EIS fitting method relative to Classical, Exact QUBO, and QPU execution.",
        "sources": {
            "classical_fit_parameters": str(classical_param_path),
            "classical_fitted_spectrum": str(classical_spectrum_path),
            "exact_qubo_source": str(qubo_path),
            "qaoa_simulator_source": str(qaoa_path),
            "qaoa_qpu_source": str(qpu_path),
        },
        "selected_bitstrings": {
            "exact_qubo": qubo_bitstring,
            "qaoa_simulator": qaoa_bitstring,
            "qaoa_qpu": qpu_bitstring,
        },
        "summary": {
            "best_sse_method": metrics_df.sort_values("SSE", ascending=True).iloc[0]["method"],
            "best_rmse_method": metrics_df.sort_values("RMSE", ascending=True).iloc[0]["method"],
            "qpu_sse_ratio_to_classical": float(metrics_df.loc[metrics_df["method"] == "QAOA QPU", "SSE_ratio_to_classical"].iloc[0]),
            "qpu_rmse_ratio_to_classical": float(metrics_df.loc[metrics_df["method"] == "QAOA QPU", "RMSE_ratio_to_classical"].iloc[0]),
            "qaoa_sim_hamming_to_exact_qubo": hamming_distance(qaoa_bitstring, qubo_bitstring),
            "qpu_hamming_to_exact_qubo": hamming_distance(qpu_bitstring, qubo_bitstring),
        },
        "outputs": {
            "nyquist_png": str(output_dir / "nyquist_classical_qubo_qaoa_qpu.png"),
            "bode_png": str(output_dir / "bode_classical_qubo_qaoa_qpu.png"),
            "residuals_png": str(output_dir / "residuals_classical_qubo_qaoa_qpu.png"),
            "sse_png": str(output_dir / "sse_classical_qubo_qaoa_qpu.png"),
            "rmse_png": str(output_dir / "rmse_classical_qubo_qaoa_qpu.png"),
            "sse_rmse_png": str(output_dir / "sse_rmse_classical_qubo_qaoa_qpu.png"),
            "parameter_table_png": str(output_dir / "parameter_comparison_table.png"),
            "relative_error_heatmap_png": str(output_dir / "relative_error_heatmap.png"),
            "metrics_table_png": str(output_dir / "metrics_summary_table.png"),
            "stage_transition_table_png": str(output_dir / "stage_transition_summary_table.png"),
            "parameter_csv": str(output_dir / "parameter_comparison.csv"),
            "metrics_csv": str(output_dir / "metrics_summary.csv"),
            "stage_csv": str(output_dir / "stage_transition_summary.csv"),
            "bitstring_csv": str(output_dir / "bitstring_summary.csv"),
            "spectra_csv": str(output_dir / "fitted_spectra_all_methods.csv"),
        },
    }
    save_json(output_dir / "compare_report.json", report)

    # terminal summary
    print("Saved comparison to:")
    print(f"  {output_dir}")
    print()
    print("Method metrics:")
    print(metrics_df.to_string(index=False))
    print()
    print("Stage summary:")
    print(stage_df.to_string(index=False))
    print("=" * 90)

    result_rows = []
    for _, row in metrics_df.iterrows():
        result_rows.append(
            {
                "dataset": dataset,
                "qaoa_p": int(p),
                **row.to_dict(),
            }
        )
    return result_rows



def main():
    parser = argparse.ArgumentParser(
        description="Compare Classical, Exact QUBO, QAOA simulator, and QAOA QPU fits."
    )
    parser.add_argument("--dataset", choices=["EIS13", "EIS14", "BOTH"], default="BOTH")
    parser.add_argument("--eis13-p", type=int, default=1)
    parser.add_argument("--eis14-p", type=int, default=2)
    parser.add_argument("--nyquist-xmax", type=float, default=None)
    args = parser.parse_args()

    p_map = {"EIS13": int(args.eis13_p), "EIS14": int(args.eis14_p)}
    datasets = ["EIS13", "EIS14"] if args.dataset == "BOTH" else [args.dataset]

    all_rows = []
    for dataset in datasets:
        all_rows.extend(compare_dataset(dataset, p_map[dataset], args.nyquist_xmax))

    summary_df = pd.DataFrame(all_rows)
    summary_path = RESULTS_ROOT / "comparison_all_methods_summary.csv"
    summary_df.to_csv(summary_path, index=False)

    print()
    print("FINAL SUMMARY CSV:")
    print(summary_path)


if __name__ == "__main__":
    main()
