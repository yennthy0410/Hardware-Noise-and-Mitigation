"""
evaluate_qaoa_qpu_eis.py

Decode completed QAOA-QPU results into the seven physical EIS parameters,
insert those parameters into the physical impedance model, and compare:

    Experimental EIS
    Classical reference
    QAOA QPU

Default final choices
---------------------
EIS13 -> QAOA p = 1
EIS14 -> QAOA p = 2

IMPORTANT
---------
At this evaluation stage, raw EIS data are NOT sent back into the quantum
circuit. The hardware workflow has already produced observed QPU states.

Pipeline:
    QPU counts
      -> selected canonical 21-bit bitstring
      -> binary_encoding.decode_bitstring()
      -> [Rs, L, Rct, Q1, n1, Q2, n2]
      -> impedance_model(frequency, parameters)
      -> compare with Experimental and Classical

Classical source is locked to:
    reference_results/classical/<DATASET>/

Default QPU state:
    the same state selected by run_qaoa_qpu.py:
    minimum Ising-energy bitstring among states actually observed on QPU.

Outputs
-------
results/<DATASET>/qaoa/qpu/p<P>/eis_evaluation/

    qpu_decoded_parameters.csv
    qpu_decoded_parameters.json

    qpu_vs_classical_metrics.csv
    qpu_vs_classical_parameters.csv
    qpu_fitted_spectrum.csv
    qpu_eis_report.json

    nyquist_qpu_vs_classical.png/.pdf
    bode_qpu_vs_classical.png/.pdf
    residuals_qpu_vs_classical.png/.pdf

    sse_qpu_vs_classical.png/.pdf
    rmse_qpu_vs_classical.png/.pdf
    parameter_comparison_table.png/.pdf
    pointwise_error.png/.pdf

Combined summary:
    results/qaoa_qpu_eis_evaluation/qpu_vs_classical_summary.csv

Examples
--------
    python evaluate_qaoa_qpu_eis.py --dataset BOTH
    python evaluate_qaoa_qpu_eis.py --dataset EIS13
    python evaluate_qaoa_qpu_eis.py --dataset EIS14
    python evaluate_qaoa_qpu_eis.py --dataset BOTH --nyquist-xmax 60
"""

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
# Paths
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
EXPERIMENT_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = EXPERIMENT_DIR.parents[2]

RESULTS_ROOT = EXPERIMENT_DIR / "results"
DATA_ROOT = PROJECT_ROOT / "data"
CLASSICAL_ROOT = PROJECT_ROOT / "reference_results" / "classical"
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# ============================================================
# Existing project modules
# ============================================================

from binary_encoding import PARAMETER_ORDER, decode_bitstring
from eis_evaluation import EISEvaluator
from impedance_model import read_eis_file, impedance_model


DEFAULT_P = {
    "EIS13": 1,
    "EIS14": 2,
}

PARAMETER_UNITS = {
    "Rs": "Ohm",
    "L": "H",
    "Rct": "Ohm",
    "Q1": "S*s^n1",
    "n1": "-",
    "Q2": "S*s^n2",
    "n2": "-",
}

METHOD_COLORS = {
    "Classical": "#1f77b4",
    "QAOA QPU": "#2ca02c",
}


# ============================================================
# General helpers
# ============================================================

def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )


def save_figure(fig, path: Path) -> None:
    fig.savefig(path, dpi=300, bbox_inches="tight")
    fig.savefig(path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


def validate_bitstring(bitstring: str) -> str:
    bitstring = str(bitstring).strip().replace(" ", "")

    if len(bitstring) != 21:
        raise ValueError(
            f"Expected a 21-bit canonical QAOA bitstring, got "
            f"{len(bitstring)} bits: {bitstring}"
        )

    if any(bit not in "01" for bit in bitstring):
        raise ValueError(f"Invalid bitstring: {bitstring}")

    return bitstring


def fmt_value(value) -> str:
    if pd.isna(value):
        return "NaN"

    value = float(value)

    if value == 0:
        return "0"

    if abs(value) >= 1e3 or abs(value) < 1e-3:
        return f"{value:.4e}"

    return f"{value:.6f}".rstrip("0").rstrip(".")


def fmt_percent(value) -> str:
    if pd.isna(value):
        return "NaN"

    value = float(value)
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def soften_color(color, mix_with="white", alpha=0.45):
    base = np.asarray(mcolors.to_rgb(color), dtype=float)
    mix = np.asarray(mcolors.to_rgb(mix_with), dtype=float)
    return tuple((1.0 - alpha) * base + alpha * mix)


def relative_error_color(error_percent, vmax=30.0):
    if pd.isna(error_percent):
        return (1, 1, 1, 1)

    scaled = min(abs(float(error_percent)), vmax) / vmax
    rgba = plt.get_cmap("RdYlGn_r")(scaled)
    soft = soften_color(rgba[:3], "white", alpha=0.45)
    return (*soft, 1.0)


# ============================================================
# QPU result loading
# ============================================================

def qpu_result_dir(dataset: str, p: int) -> Path:
    return RESULTS_ROOT / dataset / "qpu" / f"p{p}"


def load_selected_qpu_bitstring(dataset: str, p: int):
    root = qpu_result_dir(dataset, p)

    if not root.exists():
        raise FileNotFoundError(
            f"QPU result directory does not exist:\n{root}\n\n"
            "Run run_qaoa_qpu.py first."
        )

    parameter_json = root / "qpu_best_observed_parameters.json"

    if parameter_json.exists():
        data = json.loads(parameter_json.read_text(encoding="utf-8"))
        bitstring = data.get("canonical_bitstring")

        if bitstring:
            return (
                validate_bitstring(bitstring),
                "qpu_best_observed_parameters.json",
                parameter_json,
            )

    summary_json = root / "qpu_result_summary.json"

    if summary_json.exists():
        data = json.loads(summary_json.read_text(encoding="utf-8"))
        bitstring = data.get("best_observed_canonical_bitstring")

        if bitstring:
            return (
                validate_bitstring(bitstring),
                "qpu_result_summary.json",
                summary_json,
            )

    counts_csv = root / "qpu_counts_energy_ranked.csv"

    if counts_csv.exists():
        df = pd.read_csv(counts_csv)

        required = ["canonical_bitstring", "ising_energy"]
        missing = [column for column in required if column not in df.columns]

        if missing:
            raise ValueError(
                f"{counts_csv} missing columns: {missing}"
            )

        if df.empty:
            raise ValueError(f"{counts_csv} is empty.")

        best_index = df["ising_energy"].astype(float).idxmin()
        bitstring = str(df.loc[best_index, "canonical_bitstring"])

        return (
            validate_bitstring(bitstring),
            "qpu_counts_energy_ranked.csv",
            counts_csv,
        )

    raise FileNotFoundError(
        "Completed QPU result was not found.\n"
        f"Searched under:\n{root}\n\n"
        "Expected one of:\n"
        "  qpu_best_observed_parameters.json\n"
        "  qpu_result_summary.json\n"
        "  qpu_counts_energy_ranked.csv\n\n"
        "If the IBM job was submitted with --no-wait, run:\n"
        f"  python run_qaoa_qpu.py --dataset {dataset} --resume"
    )


def load_qpu_job_metadata(dataset: str, p: int) -> dict:
    root = qpu_result_dir(dataset, p)
    metadata = {}

    for filename in [
        "qpu_preparation.json",
        "qpu_submission.json",
        "qpu_result_summary.json",
        "job_metrics.json",
    ]:
        path = root / filename

        if not path.exists():
            continue

        try:
            metadata[filename] = json.loads(
                path.read_text(encoding="utf-8")
            )
        except Exception as error:
            metadata[filename] = {
                "read_error": f"{type(error).__name__}: {error}"
            }

    return metadata


# ============================================================
# Decode QPU bitstring
# ============================================================

def decode_qpu_state(bitstring: str):
    """
    Decode one canonical 21-bit QPU state.

    Compatibility note
    ------------------
    Different versions of binary_encoding.py used in this project return
    slightly different diagnostic keys. The physical decoding itself is the
    same. Therefore this function only REQUIRES:

        physical_parameters
        parameter_vector

    and reconstructs optional diagnostics when they are absent:
        parameter_bits
        integer_levels
        normalized_levels
        equivalent_expectations

    For a 3-bit level k in [0, 7]:
        normalized = k / 7
        equivalent HEA expectation = 2*k/7 - 1
    """

    bitstring = validate_bitstring(
        bitstring
    )

    decoded_raw = decode_bitstring(
        bitstring
    )

    if "physical_parameters" not in decoded_raw:
        raise KeyError(
            "binary_encoding.decode_bitstring() did not return "
            "'physical_parameters'. "
            f"Available keys: {list(decoded_raw.keys())}"
        )

    physical_parameters = decoded_raw[
        "physical_parameters"
    ]

    parameters = {
        name:
            float(
                physical_parameters[
                    name
                ]
            )
        for name in PARAMETER_ORDER
    }

    if "parameter_vector" in decoded_raw:
        parameter_vector = np.asarray(
            decoded_raw[
                "parameter_vector"
            ],
            dtype=float,
        )
    else:
        parameter_vector = np.asarray(
            [
                parameters[
                    name
                ]
                for name in PARAMETER_ORDER
            ],
            dtype=float,
        )

    # --------------------------------------------------------
    # Reconstruct optional decoder diagnostics safely.
    # --------------------------------------------------------

    parameter_bits = dict(
        decoded_raw.get(
            "parameter_bits",
            {},
        )
    )

    integer_levels = dict(
        decoded_raw.get(
            "integer_levels",
            {},
        )
    )

    normalized_levels = dict(
        decoded_raw.get(
            "normalized_levels",
            {},
        )
    )

    equivalent_expectations = dict(
        decoded_raw.get(
            "equivalent_expectations",
            {},
        )
    )

    rows = []

    for index, name in enumerate(
        PARAMETER_ORDER
    ):
        start = int(
            index * 3
        )

        stop = int(
            start + 3
        )

        # Canonical project order:
        # x0-x2 Rs, x3-x5 L, ... x18-x20 n2.
        bits = parameter_bits.get(
            name
        )

        if bits is None:
            bits = bitstring[
                start:stop
            ]

        bits = str(
            bits
        )

        level = integer_levels.get(
            name
        )

        if level is None:
            level = int(
                bits,
                2,
            )

        level = int(
            level
        )

        normalized = normalized_levels.get(
            name
        )

        if normalized is None:
            normalized = (
                float(
                    level
                )
                / 7.0
            )

        normalized = float(
            normalized
        )

        equivalent_expectation = equivalent_expectations.get(
            name
        )

        if equivalent_expectation is None:
            equivalent_expectation = (
                2.0
                * normalized
                - 1.0
            )

        equivalent_expectation = float(
            equivalent_expectation
        )

        parameter_bits[
            name
        ] = bits

        integer_levels[
            name
        ] = level

        normalized_levels[
            name
        ] = normalized

        equivalent_expectations[
            name
        ] = equivalent_expectation

        rows.append(
            {
                "parameter":
                    name,

                "unit":
                    PARAMETER_UNITS[
                        name
                    ],

                "bits":
                    bits,

                "integer_level":
                    level,

                "normalized_level":
                    normalized,

                "equivalent_expectation":
                    equivalent_expectation,

                "physical_value":
                    float(
                        parameters[
                            name
                        ]
                    ),

                "qubit_start":
                    start,

                "qubit_stop":
                    stop - 1,
            }
        )

    decoded = dict(
        decoded_raw
    )

    decoded[
        "bitstring"
    ] = bitstring

    decoded[
        "parameter_bits"
    ] = parameter_bits

    decoded[
        "integer_levels"
    ] = integer_levels

    decoded[
        "normalized_levels"
    ] = normalized_levels

    decoded[
        "equivalent_expectations"
    ] = equivalent_expectations

    decoded[
        "physical_parameters"
    ] = parameters

    decoded[
        "parameter_vector"
    ] = parameter_vector

    return (
        decoded,
        parameters,
        parameter_vector,
        pd.DataFrame(
            rows
        ),
    )


# ============================================================
# Experimental EIS
# ============================================================

def load_experimental_eis(dataset: str):
    path = DATA_ROOT / f"{dataset}.csv"

    if not path.exists():
        raise FileNotFoundError(
            f"Experimental EIS file not found:\n{path}"
        )

    try:
        frequency, z_exp = read_eis_file(str(path))
    except Exception:
        frequency, z_exp = read_eis_file(path.name)

    frequency = np.asarray(frequency, dtype=float)
    z_exp = np.asarray(z_exp, dtype=complex)

    valid = (
        np.isfinite(frequency)
        & (frequency > 0)
        & np.isfinite(z_exp.real)
        & np.isfinite(z_exp.imag)
    )

    frequency = frequency[valid]
    z_exp = z_exp[valid]

    if len(frequency) < 8:
        raise ValueError(
            f"{dataset}: fewer than 8 valid EIS data points."
        )

    order = np.argsort(frequency)

    return (
        frequency[order],
        z_exp[order],
        path,
    )


# ============================================================
# Classical reference
# ============================================================

def validate_classical_root():
    expected = (
        PROJECT_ROOT
        / "reference_results"
        / "classical"
    ).resolve()

    actual = CLASSICAL_ROOT.resolve()

    if actual != expected:
        raise RuntimeError(
            "Classical source is not the approved reference_results path.\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def load_classical_parameters(dataset: str):
    validate_classical_root()

    path = (
        CLASSICAL_ROOT
        / dataset
        / "fit_parameters.csv"
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Missing Classical parameter file:\n{path}"
        )

    df = pd.read_csv(path)
    lower = {
        str(column).strip().lower(): column
        for column in df.columns
    }

    if "parameter" in lower and "value" in lower:
        pcol = lower["parameter"]
        vcol = lower["value"]

        mapping = {
            str(row[pcol]).strip(): float(row[vcol])
            for _, row in df.iterrows()
        }

        missing = [
            name
            for name in PARAMETER_ORDER
            if name not in mapping
        ]

        if missing:
            raise ValueError(
                f"{path} missing Classical parameters: {missing}"
            )

        parameters = {
            name: float(mapping[name])
            for name in PARAMETER_ORDER
        }

    elif all(name in df.columns for name in PARAMETER_ORDER):
        row = df.iloc[0]

        parameters = {
            name: float(row[name])
            for name in PARAMETER_ORDER
        }

    else:
        raise ValueError(
            f"Cannot parse Classical parameter file:\n{path}\n"
            f"Columns: {list(df.columns)}"
        )

    vector = np.asarray(
        [parameters[name] for name in PARAMETER_ORDER],
        dtype=float,
    )

    return parameters, vector, path


# ============================================================
# Metrics
# ============================================================

def direct_complex_metrics(z_exp, z_fit):
    residual = np.asarray(z_fit, dtype=complex) - np.asarray(
        z_exp,
        dtype=complex,
    )

    squared_error = np.abs(residual) ** 2

    sse = float(np.sum(squared_error))

    rmse = float(
        np.sqrt(
            sse
            / (
                2.0
                * len(z_exp)
            )
        )
    )

    mae_complex = float(
        np.mean(np.abs(residual))
    )

    relative_l2 = float(
        np.linalg.norm(residual)
        / max(
            np.linalg.norm(z_exp),
            1e-15,
        )
    )

    return {
        "SSE": sse,
        "RMSE": rmse,
        "MAE_complex": mae_complex,
        "relative_L2": relative_l2,
    }


def evaluator_metrics(
    evaluator: EISEvaluator,
    parameter_vector: np.ndarray,
):
    result = evaluator.evaluate(
        parameter_vector,
        return_arrays=False,
    )

    wanted = [
        "weighted_complex_loss",
        "SSE",
        "RMSE",
    ]

    missing = [
        key
        for key in wanted
        if key not in result
    ]

    if missing:
        raise KeyError(
            f"EISEvaluator missing keys {missing}. "
            f"Returned={list(result.keys())}"
        )

    return {
        "weighted_complex_loss":
            float(result["weighted_complex_loss"]),
        "SSE":
            float(result["SSE"]),
        "RMSE":
            float(result["RMSE"]),
    }


# ============================================================
# Figures
# ============================================================

def save_nyquist(
    output_dir,
    dataset,
    p,
    z_exp,
    z_classical,
    z_qpu,
    x_max,
):
    fig, ax = plt.subplots(figsize=(7.6, 6.1))

    ax.scatter(
        z_exp.real,
        -z_exp.imag,
        s=28,
        label="Experimental",
        zorder=5,
    )

    ax.plot(
        z_classical.real,
        -z_classical.imag,
        linewidth=2.2,
        color=METHOD_COLORS["Classical"],
        label="Classical",
    )

    ax.plot(
        z_qpu.real,
        -z_qpu.imag,
        "--",
        linewidth=2.2,
        color=METHOD_COLORS["QAOA QPU"],
        label=f"QAOA QPU (p={p})",
    )

    ax.set_title(
        f"{dataset} - Nyquist: Classical vs QAOA QPU"
    )
    ax.set_xlabel(r"$Z'$ ($\Omega$)")
    ax.set_ylabel(r"$-Z''$ ($\Omega$)")
    ax.grid(True, alpha=0.28)
    ax.legend()

    if x_max is not None:
        ax.set_xlim(0.0, float(x_max))

    fig.tight_layout()

    save_figure(
        fig,
        output_dir / "nyquist_qpu_vs_classical.png",
    )


def save_bode(
    output_dir,
    dataset,
    p,
    frequency,
    z_exp,
    z_classical,
    z_qpu,
):
    fig, axes = plt.subplots(
        2,
        1,
        figsize=(8.5, 8.4),
        sharex=True,
    )

    ax = axes[0]

    ax.scatter(
        frequency,
        np.abs(z_exp),
        s=24,
        label="Experimental",
        zorder=5,
    )

    ax.plot(
        frequency,
        np.abs(z_classical),
        linewidth=2.1,
        color=METHOD_COLORS["Classical"],
        label="Classical",
    )

    ax.plot(
        frequency,
        np.abs(z_qpu),
        "--",
        linewidth=2.1,
        color=METHOD_COLORS["QAOA QPU"],
        label=f"QAOA QPU (p={p})",
    )

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylabel(r"$|Z|$ ($\Omega$)")
    ax.set_title(
        f"{dataset} - Bode: Classical vs QAOA QPU"
    )
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    ax = axes[1]

    ax.scatter(
        frequency,
        np.angle(z_exp, deg=True),
        s=24,
        label="Experimental",
        zorder=5,
    )

    ax.plot(
        frequency,
        np.angle(z_classical, deg=True),
        linewidth=2.1,
        color=METHOD_COLORS["Classical"],
        label="Classical",
    )

    ax.plot(
        frequency,
        np.angle(z_qpu, deg=True),
        "--",
        linewidth=2.1,
        color=METHOD_COLORS["QAOA QPU"],
        label=f"QAOA QPU (p={p})",
    )

    ax.set_xscale("log")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Phase (degree)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    fig.tight_layout()

    save_figure(
        fig,
        output_dir / "bode_qpu_vs_classical.png",
    )


def save_residuals(
    output_dir,
    dataset,
    p,
    frequency,
    z_exp,
    z_classical,
    z_qpu,
):
    r_classical = z_classical - z_exp
    r_qpu = z_qpu - z_exp

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(8.5, 8.1),
        sharex=True,
    )

    ax = axes[0]

    ax.plot(
        frequency,
        r_classical.real,
        "o-",
        markersize=3.0,
        linewidth=1.4,
        color=METHOD_COLORS["Classical"],
        label="Classical",
    )

    ax.plot(
        frequency,
        r_qpu.real,
        "o--",
        markersize=3.0,
        linewidth=1.4,
        color=METHOD_COLORS["QAOA QPU"],
        label=f"QAOA QPU (p={p})",
    )

    ax.axhline(0.0, linewidth=1.0)
    ax.set_xscale("log")
    ax.set_ylabel(r"$\Delta Z'$ ($\Omega$)")
    ax.set_title(
        f"{dataset} - Residuals (Fit - Experimental)"
    )
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    ax = axes[1]

    ax.plot(
        frequency,
        r_classical.imag,
        "o-",
        markersize=3.0,
        linewidth=1.4,
        color=METHOD_COLORS["Classical"],
        label="Classical",
    )

    ax.plot(
        frequency,
        r_qpu.imag,
        "o--",
        markersize=3.0,
        linewidth=1.4,
        color=METHOD_COLORS["QAOA QPU"],
        label=f"QAOA QPU (p={p})",
    )

    ax.axhline(0.0, linewidth=1.0)
    ax.set_xscale("log")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel(r"$\Delta Z''$ ($\Omega$)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    fig.tight_layout()

    save_figure(
        fig,
        output_dir / "residuals_qpu_vs_classical.png",
    )


def save_metric_bar(
    output_dir,
    dataset,
    metric_name,
    classical,
    qpu,
):
    methods = ["Classical", "QAOA QPU"]
    values = [float(classical), float(qpu)]
    colors = [
        METHOD_COLORS["Classical"],
        METHOD_COLORS["QAOA QPU"],
    ]

    fig, ax = plt.subplots(figsize=(6.4, 5.1))

    bars = ax.bar(
        methods,
        values,
        color=colors,
        edgecolor="#333333",
        linewidth=0.8,
        width=0.58,
    )

    ax.set_title(
        f"{dataset} - {metric_name} Comparison",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_ylabel(metric_name)
    ax.grid(
        True,
        axis="y",
        alpha=0.25,
        linestyle="--",
    )

    ymax = max(values) if max(values) > 0 else 1.0

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 0.025 * ymax,
            f"{value:.5g}",
            ha="center",
            va="bottom",
            fontsize=9.5,
            fontweight="bold",
        )

    ax.set_ylim(0, ymax * 1.16)
    fig.tight_layout()

    save_figure(
        fig,
        output_dir / f"{metric_name.lower()}_qpu_vs_classical.png",
    )


def save_parameter_table(
    output_dir,
    dataset,
    parameter_df,
):
    display = parameter_df.copy()

    display["Classical"] = display["classical"].map(fmt_value)
    display["QAOA QPU"] = display["qaoa_qpu"].map(fmt_value)
    display["Relative error"] = display[
        "qpu_relative_error_percent"
    ].map(fmt_percent)

    display = display[
        [
            "parameter",
            "unit",
            "Classical",
            "QAOA QPU",
            "Relative error",
        ]
    ].rename(
        columns={
            "parameter": "Parameter",
            "unit": "Unit",
        }
    )

    fig, ax = plt.subplots(figsize=(11.5, 6.7))
    ax.axis("off")

    table = ax.table(
        cellText=display.values,
        colLabels=display.columns,
        cellLoc="center",
        colWidths=[
            0.16,
            0.11,
            0.23,
            0.23,
            0.23,
        ],
        loc="center",
    )

    table.auto_set_font_size(False)
    table.set_fontsize(10.5)
    table.scale(1.0, 1.8)

    header_bg = "#203864"
    classical_bg = "#EAF2F8"

    for column in range(len(display.columns)):
        cell = table[(0, column)]
        cell.set_facecolor(header_bg)
        cell.set_text_props(
            color="white",
            weight="bold",
        )
        cell.set_edgecolor("#B8C6D1")
        cell.set_linewidth(1.1)

    for row_index in range(len(display)):
        error = parameter_df.iloc[
            row_index
        ]["qpu_relative_error_percent"]

        error_bg = relative_error_color(error)

        for column in range(len(display.columns)):
            cell = table[(row_index + 1, column)]
            cell.set_edgecolor("#D0D7DE")
            cell.set_linewidth(0.85)

            if column == 0:
                cell.set_facecolor(
                    "#FBFCFC"
                    if row_index % 2 == 0
                    else "#F7F9F9"
                )
                cell.set_text_props(weight="bold")

            elif column == 1:
                cell.set_facecolor(
                    "#FBFCFC"
                    if row_index % 2 == 0
                    else "#F7F9F9"
                )

            elif column == 2:
                cell.set_facecolor(classical_bg)

            elif column in [3, 4]:
                cell.set_facecolor(error_bg)

                if column == 4:
                    cell.set_text_props(weight="bold")

    ax.set_title(
        f"{dataset} - Classical vs QAOA QPU: 7 EIS Parameters",
        fontsize=14,
        weight="bold",
        pad=22,
    )

    fig.text(
        0.5,
        0.04,
        (
            "QPU cells: green = low |relative error|, "
            "yellow/orange = medium, red = high."
        ),
        ha="center",
        fontsize=10,
        style="italic",
    )

    fig.text(
        0.5,
        0.015,
        (
            "Relative error (%) = "
            "(QAOA QPU - Classical) / |Classical| × 100"
        ),
        ha="center",
        fontsize=9.5,
    )

    fig.tight_layout(rect=[0.01, 0.07, 0.99, 0.94])

    save_figure(
        fig,
        output_dir / "parameter_comparison_table.png",
    )


def save_pointwise_error(
    output_dir,
    dataset,
    frequency,
    z_exp,
    z_classical,
    z_qpu,
):
    classical_error = np.abs(z_classical - z_exp)
    qpu_error = np.abs(z_qpu - z_exp)

    fig, ax = plt.subplots(figsize=(8.0, 5.3))

    ax.plot(
        frequency,
        classical_error,
        linewidth=1.6,
        color=METHOD_COLORS["Classical"],
        label="Classical",
    )

    ax.plot(
        frequency,
        qpu_error,
        "--",
        linewidth=1.6,
        color=METHOD_COLORS["QAOA QPU"],
        label="QAOA QPU",
    )

    ax.set_xscale("log")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel(
        r"$|Z_{\mathrm{fit}}-Z_{\mathrm{exp}}|$ ($\Omega$)"
    )
    ax.set_title(
        f"{dataset} - Pointwise EIS Error"
    )
    ax.grid(True, which="both", alpha=0.25)
    ax.legend()

    fig.tight_layout()

    save_figure(
        fig,
        output_dir / "pointwise_error.png",
    )


# ============================================================
# One dataset
# ============================================================

def evaluate_dataset(
    dataset: str,
    p: int,
    nyquist_xmax,
):
    dataset = dataset.upper()

    print()
    print("=" * 82)
    print(
        f"{dataset} - CLASSICAL vs QAOA QPU EIS EVALUATION (p={p})"
    )
    print("=" * 82)

    qpu_root = qpu_result_dir(dataset, p)
    output_dir = qpu_root / "eis_evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # 1. Load QPU-selected state
    # --------------------------------------------------------

    (
        bitstring,
        bitstring_source,
        bitstring_source_path,
    ) = load_selected_qpu_bitstring(
        dataset,
        p,
    )

    print(
        f"QPU canonical bitstring : {bitstring}"
    )
    print(
        f"Bitstring source         : {bitstring_source}"
    )

    # --------------------------------------------------------
    # 2. Decode 21 bits -> 7 physical parameters
    # --------------------------------------------------------

    (
        decoded,
        qpu_parameters,
        qpu_vector,
        qpu_parameter_table,
    ) = decode_qpu_state(
        bitstring
    )

    qpu_parameter_table.to_csv(
        output_dir / "qpu_decoded_parameters.csv",
        index=False,
    )

    save_json(
        output_dir / "qpu_decoded_parameters.json",
        {
            "dataset": dataset,
            "p": int(p),
            "canonical_bitstring": bitstring,
            "parameter_bits":
                decoded["parameter_bits"],
            "integer_levels":
                decoded["integer_levels"],
            "equivalent_expectations":
                decoded["equivalent_expectations"],
            "physical_parameters":
                qpu_parameters,
        },
    )

    # --------------------------------------------------------
    # 3. Experimental EIS
    # --------------------------------------------------------

    (
        frequency,
        z_exp,
        eis_path,
    ) = load_experimental_eis(dataset)

    # --------------------------------------------------------
    # 4. Classical reference
    # --------------------------------------------------------

    (
        classical_parameters,
        classical_vector,
        classical_parameter_path,
    ) = load_classical_parameters(dataset)

    # Evaluate both methods at EXACTLY the same frequency points.
    z_classical = np.asarray(
        impedance_model(
            frequency,
            *classical_vector,
        ),
        dtype=complex,
    )

    z_qpu = np.asarray(
        impedance_model(
            frequency,
            *qpu_vector,
        ),
        dtype=complex,
    )

    # --------------------------------------------------------
    # 5. Same project evaluator for both
    # --------------------------------------------------------

    evaluator = EISEvaluator(dataset)

    classical_project_metrics = evaluator_metrics(
        evaluator,
        classical_vector,
    )

    qpu_project_metrics = evaluator_metrics(
        evaluator,
        qpu_vector,
    )

    classical_direct_metrics = direct_complex_metrics(
        z_exp,
        z_classical,
    )

    qpu_direct_metrics = direct_complex_metrics(
        z_exp,
        z_qpu,
    )

    # --------------------------------------------------------
    # 6. Metrics CSV
    # --------------------------------------------------------

    metrics_df = pd.DataFrame(
        [
            {
                "dataset": dataset,
                "method": "Classical",
                "qaoa_p": np.nan,
                "weighted_complex_loss":
                    classical_project_metrics[
                        "weighted_complex_loss"
                    ],
                "SSE":
                    classical_project_metrics["SSE"],
                "RMSE":
                    classical_project_metrics["RMSE"],
                "direct_SSE":
                    classical_direct_metrics["SSE"],
                "direct_RMSE":
                    classical_direct_metrics["RMSE"],
                "direct_MAE_complex":
                    classical_direct_metrics["MAE_complex"],
                "direct_relative_L2":
                    classical_direct_metrics["relative_L2"],
            },
            {
                "dataset": dataset,
                "method": "QAOA QPU",
                "qaoa_p": int(p),
                "weighted_complex_loss":
                    qpu_project_metrics[
                        "weighted_complex_loss"
                    ],
                "SSE":
                    qpu_project_metrics["SSE"],
                "RMSE":
                    qpu_project_metrics["RMSE"],
                "direct_SSE":
                    qpu_direct_metrics["SSE"],
                "direct_RMSE":
                    qpu_direct_metrics["RMSE"],
                "direct_MAE_complex":
                    qpu_direct_metrics["MAE_complex"],
                "direct_relative_L2":
                    qpu_direct_metrics["relative_L2"],
            },
        ]
    )

    metrics_df.to_csv(
        output_dir / "qpu_vs_classical_metrics.csv",
        index=False,
    )

    # --------------------------------------------------------
    # 7. Parameter comparison CSV + image table
    # --------------------------------------------------------

    parameter_rows = []

    for index, name in enumerate(PARAMETER_ORDER):
        classical_value = float(
            classical_parameters[name]
        )

        qpu_value = float(
            qpu_parameters[name]
        )

        relative_error = (
            (
                qpu_value
                - classical_value
            )
            / abs(classical_value)
            * 100.0
            if classical_value != 0
            else np.nan
        )

        parameter_rows.append(
            {
                "parameter": name,
                "unit": PARAMETER_UNITS[name],
                "qpu_bits":
                    bitstring[
                        index * 3:
                        index * 3 + 3
                    ],
                "classical":
                    classical_value,
                "qaoa_qpu":
                    qpu_value,
                "qpu_relative_error_percent":
                    relative_error,
            }
        )

    parameter_df = pd.DataFrame(parameter_rows)

    parameter_df.to_csv(
        output_dir / "qpu_vs_classical_parameters.csv",
        index=False,
    )

    save_parameter_table(
        output_dir,
        dataset,
        parameter_df,
    )

    # --------------------------------------------------------
    # 8. Spectrum CSV
    # --------------------------------------------------------

    residual_classical = z_classical - z_exp
    residual_qpu = z_qpu - z_exp

    spectrum_df = pd.DataFrame(
        {
            "frequency_hz":
                frequency,

            "z_exp_real_ohm":
                z_exp.real,

            "z_exp_imag_ohm":
                z_exp.imag,

            "minus_z_exp_imag_ohm":
                -z_exp.imag,

            "z_exp_abs_ohm":
                np.abs(z_exp),

            "z_exp_phase_deg":
                np.angle(
                    z_exp,
                    deg=True,
                ),

            "z_classical_real_ohm":
                z_classical.real,

            "z_classical_imag_ohm":
                z_classical.imag,

            "minus_z_classical_imag_ohm":
                -z_classical.imag,

            "z_classical_abs_ohm":
                np.abs(z_classical),

            "z_classical_phase_deg":
                np.angle(
                    z_classical,
                    deg=True,
                ),

            "z_qpu_real_ohm":
                z_qpu.real,

            "z_qpu_imag_ohm":
                z_qpu.imag,

            "minus_z_qpu_imag_ohm":
                -z_qpu.imag,

            "z_qpu_abs_ohm":
                np.abs(z_qpu),

            "z_qpu_phase_deg":
                np.angle(
                    z_qpu,
                    deg=True,
                ),

            "classical_residual_real_ohm":
                residual_classical.real,

            "classical_residual_imag_ohm":
                residual_classical.imag,

            "classical_residual_abs_ohm":
                np.abs(residual_classical),

            "qpu_residual_real_ohm":
                residual_qpu.real,

            "qpu_residual_imag_ohm":
                residual_qpu.imag,

            "qpu_residual_abs_ohm":
                np.abs(residual_qpu),

            "classical_pointwise_squared_error_ohm2":
                np.abs(residual_classical) ** 2,

            "qpu_pointwise_squared_error_ohm2":
                np.abs(residual_qpu) ** 2,
        }
    )

    spectrum_df.to_csv(
        output_dir / "qpu_fitted_spectrum.csv",
        index=False,
    )

    # --------------------------------------------------------
    # 9. Figures
    # --------------------------------------------------------

    save_nyquist(
        output_dir=output_dir,
        dataset=dataset,
        p=p,
        z_exp=z_exp,
        z_classical=z_classical,
        z_qpu=z_qpu,
        x_max=nyquist_xmax,
    )

    save_bode(
        output_dir=output_dir,
        dataset=dataset,
        p=p,
        frequency=frequency,
        z_exp=z_exp,
        z_classical=z_classical,
        z_qpu=z_qpu,
    )

    save_residuals(
        output_dir=output_dir,
        dataset=dataset,
        p=p,
        frequency=frequency,
        z_exp=z_exp,
        z_classical=z_classical,
        z_qpu=z_qpu,
    )

    save_metric_bar(
        output_dir=output_dir,
        dataset=dataset,
        metric_name="SSE",
        classical=classical_project_metrics["SSE"],
        qpu=qpu_project_metrics["SSE"],
    )

    save_metric_bar(
        output_dir=output_dir,
        dataset=dataset,
        metric_name="RMSE",
        classical=classical_project_metrics["RMSE"],
        qpu=qpu_project_metrics["RMSE"],
    )

    save_pointwise_error(
        output_dir=output_dir,
        dataset=dataset,
        frequency=frequency,
        z_exp=z_exp,
        z_classical=z_classical,
        z_qpu=z_qpu,
    )

    # --------------------------------------------------------
    # 10. Provenance report
    # --------------------------------------------------------

    qpu_metadata = load_qpu_job_metadata(
        dataset,
        p,
    )

    report = {
        "dataset": dataset,
        "qaoa_p": int(p),

        "evaluation_pipeline": (
            "QPU observed bitstring -> binary decoder -> seven physical "
            "EIS parameters -> impedance_model -> compare with experimental "
            "EIS and Classical reference"
        ),

        "important_note": (
            "Experimental EIS data are not re-entered into the quantum "
            "circuit during this evaluation stage."
        ),

        "qpu_state": {
            "canonical_bitstring": bitstring,
            "source_type": bitstring_source,
            "source_path": str(bitstring_source_path),
        },

        "classical_reference": {
            "parameter_source":
                str(classical_parameter_path),
            "parameters":
                classical_parameters,
        },

        "qpu_decoded_parameters":
            qpu_parameters,

        "metrics": {
            "classical": {
                "project_EISEvaluator":
                    classical_project_metrics,
                "direct_curve":
                    classical_direct_metrics,
            },
            "qaoa_qpu": {
                "project_EISEvaluator":
                    qpu_project_metrics,
                "direct_curve":
                    qpu_direct_metrics,
            },
        },

        "experimental_eis_source":
            str(eis_path),

        "project_sources": {
            "binary_decoder":
                str(
                    SCRIPT_DIR
                    / "binary_encoding.py"
                ),
            "eis_evaluator":
                str(
                    SCRIPT_DIR
                    / "eis_evaluation.py"
                ),
            "impedance_model":
                str(
                    SRC_DIR
                    / "impedance_model.py"
                ),
        },

        "qpu_metadata":
            qpu_metadata,

        "outputs": {
            "metrics_csv":
                str(
                    output_dir
                    / "qpu_vs_classical_metrics.csv"
                ),
            "parameters_csv":
                str(
                    output_dir
                    / "qpu_vs_classical_parameters.csv"
                ),
            "spectrum_csv":
                str(
                    output_dir
                    / "qpu_fitted_spectrum.csv"
                ),
            "nyquist_png":
                str(
                    output_dir
                    / "nyquist_qpu_vs_classical.png"
                ),
            "bode_png":
                str(
                    output_dir
                    / "bode_qpu_vs_classical.png"
                ),
            "residuals_png":
                str(
                    output_dir
                    / "residuals_qpu_vs_classical.png"
                ),
            "sse_png":
                str(
                    output_dir
                    / "sse_qpu_vs_classical.png"
                ),
            "rmse_png":
                str(
                    output_dir
                    / "rmse_qpu_vs_classical.png"
                ),
            "parameter_table_png":
                str(
                    output_dir
                    / "parameter_comparison_table.png"
                ),
        },
    }

    save_json(
        output_dir / "qpu_eis_report.json",
        report,
    )

    # --------------------------------------------------------
    # 11. Terminal summary
    # --------------------------------------------------------

    print()
    print("Decoded QPU physical parameters:")

    for name in PARAMETER_ORDER:
        print(
            f"  {name:<4s} = "
            f"{qpu_parameters[name]:.12g}"
        )

    print()
    print("EIS metrics from project EISEvaluator:")

    print(
        f"  Classical SSE  = "
        f"{classical_project_metrics['SSE']:.12g}"
    )
    print(
        f"  QAOA QPU SSE   = "
        f"{qpu_project_metrics['SSE']:.12g}"
    )
    print(
        f"  Classical RMSE = "
        f"{classical_project_metrics['RMSE']:.12g}"
    )
    print(
        f"  QAOA QPU RMSE  = "
        f"{qpu_project_metrics['RMSE']:.12g}"
    )

    print()
    print(
        f"Saved to: {output_dir}"
    )
    print("=" * 82)

    return [
        {
            "dataset": dataset,
            "qaoa_p": np.nan,
            "method": "Classical",
            **classical_project_metrics,
        },
        {
            "dataset": dataset,
            "qaoa_p": int(p),
            "method": "QAOA QPU",
            **qpu_project_metrics,
        },
    ]


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Decode QAOA-QPU result into EIS parameters and compare "
            "QPU EIS fit against Classical and Experimental data."
        )
    )

    parser.add_argument(
        "--dataset",
        choices=[
            "EIS13",
            "EIS14",
            "BOTH",
        ],
        default="BOTH",
    )

    parser.add_argument(
        "--eis13-p",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--eis14-p",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--nyquist-xmax",
        type=float,
        default=None,
        help=(
            "Optional fixed Nyquist x-axis maximum. "
            "Example: --nyquist-xmax 60"
        ),
    )

    args = parser.parse_args()

    p_map = {
        "EIS13": int(args.eis13_p),
        "EIS14": int(args.eis14_p),
    }

    datasets = (
        ["EIS13", "EIS14"]
        if args.dataset == "BOTH"
        else [args.dataset]
    )

    summary_rows = []

    for dataset in datasets:
        summary_rows.extend(
            evaluate_dataset(
                dataset=dataset,
                p=p_map[dataset],
                nyquist_xmax=args.nyquist_xmax,
            )
        )

    summary_dir = (
        RESULTS_ROOT
        / "qaoa_qpu_eis_evaluation"
    )

    summary_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_path = (
        summary_dir
        / "qpu_vs_classical_summary.csv"
    )

    pd.DataFrame(
        summary_rows
    ).to_csv(
        summary_path,
        index=False,
    )

    print()
    print("Combined QPU vs Classical summary:")
    print(summary_path)


if __name__ == "__main__":
    main()
