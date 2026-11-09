"""
QAOA-QUBO Experiment 00
Publication Figure Generator for the CURRENT qaoa_circuit.py

Purpose
-------
Generate publication-ready figures from the validated Ising Hamiltonian
using the CURRENT QAOA circuit builder.

Expected current qaoa_circuit.py interface
------------------------------------------
    build_qaoa_circuit(
        h,
        j_upper,
        p=1,
        measure=False,
        barriers=False,
    )

The current circuit builder also uses:
    - qubit i <-> binary variable x_i
    - canonical bit order: x0 x1 ... x20
    - Qiskit counts order: x20 ... x1 x0
    - p-specific circuit folders:
          results/<DATASET>/qaoa/circuit/p<P>/

This script does NOT optimize gamma/beta and does NOT modify QUBO/Ising.
It only rebuilds the validated logical QAOA circuit and exports figures.

Outputs
-------
results/<DATASET>/qaoa/circuit/p<P>/publication_figures/

    qaoa_publication_schematic.png
    qaoa_publication_schematic.pdf
    qaoa_publication_schematic.svg

    qaoa_short_publication_circuit.png
    qaoa_short_publication_circuit.pdf
    qaoa_short_publication_circuit.svg

    qaoa_full_logical_circuit.png
    qaoa_full_logical_circuit.pdf
    qaoa_full_logical_circuit.svg

    qaoa_publication_figure_report.json

Notes
-----
1. The compact schematic is recommended for the main paper/report.
2. The short publication circuit is a styled, representative redraw
   of the QAOA layer for figures/slides.
3. The full gate-level Qiskit drawing is useful for appendix/supplement.
4. The identity constant C is intentionally omitted from the circuit
   because it contributes only a global phase.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch

from qaoa_circuit import (
    build_qaoa_circuit,
    get_circuit_statistics,
    get_dataset_paths,
    load_ising_coefficients,
)


# ============================================================
# Constants
# ============================================================

PARAMETER_GROUPS = [
    ("Rs", 0, 2),
    ("L", 3, 5),
    ("Rct", 6, 8),
    ("Q1", 9, 11),
    ("n1", 12, 14),
    ("Q2", 15, 17),
    ("n2", 18, 20),
]


# ============================================================
# Helpers
# ============================================================

def parameter_label_for_qubit(
    qubit: int,
) -> str:
    for name, start, end in PARAMETER_GROUPS:
        if start <= qubit <= end:
            return name

    return ""


def ensure_current_circuit_interface() -> None:
    """
    Fail with a useful message if the old qaoa_circuit.py is still active.
    """

    test_h = np.asarray(
        [0.1, -0.2],
        dtype=float,
    )

    test_j = np.zeros(
        (2, 2),
        dtype=float,
    )

    test_j[0, 1] = 0.05

    try:
        circuit = build_qaoa_circuit(
            h=test_h,
            j_upper=test_j,
            p=1,
            measure=False,
            barriers=False,
        )

    except TypeError as error:
        raise RuntimeError(
            "Your active qaoa_circuit.py does not appear to be the "
            "current upgraded version. It must support:\n\n"
            "    build_qaoa_circuit(..., barriers=False)\n\n"
            "Replace qaoa_circuit.py with the upgraded version first."
        ) from error

    if circuit.num_qubits != 2:
        raise RuntimeError(
            "Unexpected qaoa_circuit.py behavior during interface check."
        )


def resolve_output_dir(
    dataset: str,
    p: int,
) -> Path:
    paths = get_dataset_paths(
        dataset,
        p=p,
    )

    output_dir = (
        paths["output_dir"]
        / "publication_figures"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return output_dir


def save_figure_all_formats(
    figure,
    stem: Path,
    dpi: int,
) -> dict:
    """
    Save PNG + PDF + SVG.
    """

    outputs = {}

    png_path = stem.with_suffix(
        ".png"
    )

    pdf_path = stem.with_suffix(
        ".pdf"
    )

    svg_path = stem.with_suffix(
        ".svg"
    )

    figure.savefig(
        png_path,
        dpi=dpi,
        bbox_inches="tight",
    )

    figure.savefig(
        pdf_path,
        bbox_inches="tight",
    )

    figure.savefig(
        svg_path,
        bbox_inches="tight",
    )

    outputs["png"] = str(
        png_path
    )

    outputs["pdf"] = str(
        pdf_path
    )

    outputs["svg"] = str(
        svg_path
    )

    return outputs


# ============================================================
# Compact publication schematic
# ============================================================

def draw_publication_schematic(
    dataset: str,
    n_qubits: int,
    p: int,
    h: np.ndarray,
    j_upper: np.ndarray,
    output_dir: Path,
    dpi: int,
) -> dict:
    """
    Draw a compact QAOA schematic.

    This is intentionally NOT a literal rendering of all 210 possible
    ZZ interactions. A 21-qubit dense gate-level drawing becomes too
    wide for a main-paper figure.

    The schematic shows:
        |0> -> H -> U_C(gamma_l) -> U_B(beta_l)

    for all logical qubits, while explicitly defining U_C and U_B in
    the annotation.
    """

    if n_qubits != 21:
        print(
            f"WARNING: expected 21 logical qubits for the current "
            f"3-bit x 7-parameter encoding, found {n_qubits}."
        )

    # Layout width grows with QAOA depth.
    width = max(
        12.0,
        5.0 + 3.3 * p,
    )

    height = max(
        10.0,
        0.42 * n_qubits + 2.5,
    )

    fig, ax = plt.subplots(
        figsize=(
            width,
            height,
        )
    )

    ax.set_axis_off()

    # x positions
    x_start = 0.0
    x_h = 1.0

    layer_start = 2.0
    cost_width = 1.15
    mixer_width = 1.15
    layer_gap = 0.75

    total_layer_width = (
        cost_width
        + mixer_width
        + layer_gap
    )

    x_end = (
        layer_start
        + p * total_layer_width
        + 0.5
    )

    # --------------------------------------------------------
    # Logical wires
    # --------------------------------------------------------

    y_positions = []

    for qubit in range(
        n_qubits
    ):
        y = (
            n_qubits
            - 1
            - qubit
        )

        y_positions.append(
            y
        )

        ax.plot(
            [
                x_start,
                x_end,
            ],
            [
                y,
                y,
            ],
            linewidth=0.9,
        )

        parameter_name = (
            parameter_label_for_qubit(
                qubit
            )
        )

        ax.text(
            x_start - 0.12,
            y,
            (
                f"$q_{{{qubit}}}=x_{{{qubit}}}$"
                f"   ({parameter_name})"
            ),
            ha="right",
            va="center",
            fontsize=8,
        )

        ax.text(
            x_h,
            y,
            "H",
            ha="center",
            va="center",
            fontsize=8,
            bbox=dict(
                boxstyle="square,pad=0.18",
                fill=False,
            ),
        )

    # --------------------------------------------------------
    # QAOA layers
    # --------------------------------------------------------

    y_min = min(
        y_positions
    ) - 0.35

    y_max = max(
        y_positions
    ) + 0.35

    for layer in range(
        p
    ):
        x_cost = (
            layer_start
            + layer * total_layer_width
        )

        x_mixer = (
            x_cost
            + cost_width
            + 0.25
        )

        cost_box = Rectangle(
            (
                x_cost,
                y_min,
            ),
            cost_width,
            y_max - y_min,
            fill=False,
            linewidth=1.2,
        )

        mixer_box = Rectangle(
            (
                x_mixer,
                y_min,
            ),
            mixer_width,
            y_max - y_min,
            fill=False,
            linewidth=1.2,
        )

        ax.add_patch(
            cost_box
        )

        ax.add_patch(
            mixer_box
        )

        y_center = (
            0.5
            * (
                y_min
                + y_max
            )
        )

        ax.text(
            x_cost
            + cost_width / 2,
            y_center,
            (
                "$U_C$"
                "\n"
                f"$\\gamma_{{{layer}}}$"
            ),
            ha="center",
            va="center",
            fontsize=11,
        )

        ax.text(
            x_mixer
            + mixer_width / 2,
            y_center,
            (
                "$U_B$"
                "\n"
                f"$\\beta_{{{layer}}}$"
            ),
            ha="center",
            va="center",
            fontsize=11,
        )

    # --------------------------------------------------------
    # Parameter-group separators
    # --------------------------------------------------------

    for _, _, end in PARAMETER_GROUPS[:-1]:
        separator_y = (
            n_qubits
            - 1
            - end
            - 0.5
        )

        ax.plot(
            [
                x_start - 1.6,
                x_end,
            ],
            [
                separator_y,
                separator_y,
            ],
            linewidth=0.45,
            linestyle="--",
        )

    # --------------------------------------------------------
    # Mathematical definition under the circuit
    # --------------------------------------------------------

    nonzero_h = int(
        np.count_nonzero(
            h
        )
    )

    nonzero_j = int(
        np.count_nonzero(
            np.triu(
                j_upper,
                k=1,
            )
        )
    )

    definition_y = (
        y_min
        - 1.1
    )

    ax.text(
        x_start,
        definition_y,
        (
            "$H_C = \\sum_i h_i Z_i"
            " + \\sum_{i<j} J_{ij} Z_i Z_j$"
            "\n"
            "$U_C(\\gamma_l)=e^{-i\\gamma_l H_C}$,   "
            "$U_B(\\beta_l)=e^{-i\\beta_l\\sum_i X_i}$"
            "\n"
            f"Logical qubits = {n_qubits}; "
            f"nonzero $h_i$ terms = {nonzero_h}; "
            f"nonzero $J_{{ij}}$ terms = {nonzero_j}; "
            f"$p={p}$"
        ),
        ha="left",
        va="top",
        fontsize=9,
    )

    ax.set_xlim(
        x_start - 2.0,
        x_end + 0.2,
    )

    ax.set_ylim(
        definition_y - 1.5,
        y_max + 0.8,
    )

    ax.set_title(
        (
            f"QAOA–QUBO logical circuit for {dataset.upper()} "
            f"(p = {p})"
        ),
        fontsize=12,
    )

    fig.tight_layout()

    outputs = save_figure_all_formats(
        fig,
        output_dir
        / "qaoa_publication_schematic",
        dpi=dpi,
    )

    plt.close(
        fig
    )

    return outputs



# ============================================================
# Styled short publication circuit
# ============================================================

def _short_display_qubits(
    n_qubits: int,
) -> list[int | None]:
    """
    Representative rows for the short publication figure.

    For the current 21-qubit QUBO:
        q0 ... q7, ellipsis, q19, q20

    None marks the ellipsis row.
    """

    if n_qubits <= 10:
        return list(
            range(
                n_qubits
            )
        )

    return (
        list(
            range(
                min(
                    8,
                    n_qubits,
                )
            )
        )
        + [None]
        + [
            n_qubits - 2,
            n_qubits - 1,
        ]
    )


def _strongest_visible_interactions(
    display_qubits,
    j_upper: np.ndarray,
    max_terms: int = 6,
):
    """
    Select a small number of strong, REAL Ising ZZ interactions among
    the visible qubits.

    The short figure is intentionally representative rather than a
    literal drawing of every J_ij term.
    """

    visible = [
        int(
            qubit
        )
        for qubit in display_qubits
        if qubit is not None
    ]

    candidates = []

    for a, i in enumerate(
        visible
    ):
        for j in visible[
            a + 1:
        ]:
            lo = min(
                i,
                j,
            )

            hi = max(
                i,
                j,
            )

            coefficient = float(
                j_upper[
                    lo,
                    hi
                ]
            )

            if coefficient == 0.0:
                continue

            candidates.append(
                (
                    abs(
                        coefficient
                    ),
                    i,
                    j,
                    coefficient,
                )
            )

    candidates.sort(
        reverse=True,
        key=lambda item: item[0],
    )

    return candidates[
        :max_terms
    ]


def _draw_gate_box(
    ax,
    x: float,
    y: float,
    width: float,
    height: float,
    facecolor: str,
    edgecolor: str,
    text: str,
    text_color: str = "white",
    fontsize: float = 8.5,
    linewidth: float = 1.0,
    rounding: float = 0.06,
):
    patch = FancyBboxPatch(
        (
            x - width / 2,
            y - height / 2,
        ),
        width,
        height,
        boxstyle=(
            f"round,pad=0.02,"
            f"rounding_size={rounding}"
        ),
        facecolor=facecolor,
        edgecolor=edgecolor,
        linewidth=linewidth,
    )

    ax.add_patch(
        patch
    )

    ax.text(
        x,
        y,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        color=text_color,
        fontweight="bold",
    )


def draw_short_publication_circuit(
    dataset: str,
    n_qubits: int,
    p: int,
    h: np.ndarray,
    j_upper: np.ndarray,
    output_dir: Path,
    dpi: int,
) -> dict:
    """
    Draw a short, publication-style QAOA circuit inspired by the user's
    preferred template.

    IMPORTANT:
    - It uses the CURRENT Ising h_i and J_ij arrays.
    - RZ, RZZ and RX labels exactly follow qaoa_circuit.py.
    - The displayed ZZ interactions are representative REAL nonzero
      interactions selected from the visible logical qubits.
    - The optimization label is <H_C>, matching qaoa_optimizer.py.
    - No fixed "Delta = 0.08" is printed because the current EIS
      parameters use different physical/logarithmic grids.
    """

    # --------------------------------------------------------
    # Publication palette
    # --------------------------------------------------------

    background = "#FCFCF9"
    wire_palette = [
        "#1B5E7A",
        "#E76F51",
        "#3A7D44",
        "#C44536",
        "#6D6875",
        "#7B2CBF",
        "#62B01E",
        "#255F85",
        "#A44A3F",
        "#40798C",
    ]

    purple = "#6F2383"
    purple_dark = "#4C1760"
    cyan = "#139BC6"
    cyan_dark = "#08779D"
    measurement_edge = "#8C4BC3"
    navy = "#1F5B7A"
    panel_edge = "#2D6178"
    panel_fill = "#FBFCF8"
    muted = "#555555"

    # --------------------------------------------------------
    # Layout
    # --------------------------------------------------------

    display_qubits = (
        _short_display_qubits(
            n_qubits
        )
    )

    real_rows = [
        q
        for q in display_qubits
        if q is not None
    ]

    row_count = len(
        display_qubits
    )

    # one short figure is intended primarily for p=1.
    # For p>1, repeat compact cost/mixer blocks horizontally.
    layer_width = 4.9
    figure_width = max(
        14.0,
        10.5 + 3.8 * p,
    )

    figure_height = max(
        7.5,
        0.58 * row_count + 2.8,
    )

    fig, ax = plt.subplots(
        figsize=(
            figure_width,
            figure_height,
        ),
        facecolor=background,
    )

    ax.set_facecolor(
        background
    )

    ax.set_axis_off()

    # columns
    x_label = 0.4
    x_wire_start = 1.1
    x_h = 1.75

    layer_x0 = 3.4

    cost_x_offset = 0.0
    zz_x_offset = 1.35
    mixer_x_offset = 3.00
    measure_x_offset = 4.05

    panel_x = (
        layer_x0
        + p * layer_width
        + 0.5
    )

    panel_width = 3.25

    y_top = float(
        row_count
        - 1
    )

    y_positions = {}

    for row_index, qubit in enumerate(
        display_qubits
    ):
        y = (
            y_top
            - row_index
        )

        if qubit is not None:
            y_positions[
                int(
                    qubit
                )
            ] = y

    # --------------------------------------------------------
    # Headers
    # --------------------------------------------------------

    ax.text(
        x_label - 0.15,
        y_top + 1.35,
        "(b)",
        ha="left",
        va="center",
        fontsize=13,
        color="black",
    )

    ax.text(
        (
            x_wire_start
            + panel_x
            - 0.45
        ) / 2,
        y_top + 1.35,
        (
            f"QAOA $p={p}$ circuit for the "
            f"EIS QUBO surrogate ({n_qubits} qubits)"
        ),
        ha="center",
        va="center",
        fontsize=16,
        fontweight="bold",
        family="serif",
    )

    ax.text(
        x_h,
        y_top + 0.55,
        "Hadamard Layer",
        ha="center",
        va="center",
        fontsize=10,
        fontweight="bold",
    )

    if p == 1:
        ax.text(
            layer_x0 + 0.45,
            y_top + 0.55,
            r"Problem Unitary $U_C(\gamma)$",
            ha="center",
            va="center",
            fontsize=10,
            fontweight="bold",
        )

        ax.text(
            layer_x0 + mixer_x_offset,
            y_top + 0.55,
            r"Mixer $U_B(\beta)$",
            ha="center",
            va="center",
            fontsize=10,
            fontweight="bold",
        )

    # --------------------------------------------------------
    # Draw visible wires
    # --------------------------------------------------------

    for row_index, qubit in enumerate(
        display_qubits
    ):
        y = (
            y_top
            - row_index
        )

        if qubit is None:
            # vertical ellipses under each logical column
            for x in [
                x_label,
                x_h,
                layer_x0,
                layer_x0 + mixer_x_offset,
                layer_x0 + measure_x_offset,
            ]:
                ax.text(
                    x,
                    y,
                    r"$\vdots$",
                    ha="center",
                    va="center",
                    fontsize=16,
                    color="black",
                )

            continue

        qubit = int(
            qubit
        )

        wire_color = wire_palette[
            row_index
            % len(
                wire_palette
            )
        ]

        # label
        ax.text(
            x_label,
            y,
            rf"$q_{{{qubit}}}$",
            ha="right",
            va="center",
            fontsize=12,
            family="serif",
        )

        # wire ends at final measurement
        x_wire_end = (
            layer_x0
            + (p - 1) * layer_width
            + measure_x_offset
            + 0.35
        )

        ax.plot(
            [
                x_wire_start,
                x_wire_end,
            ],
            [
                y,
                y,
            ],
            color=wire_color,
            linewidth=1.15,
            solid_capstyle="round",
            zorder=1,
        )

        # Hadamard
        _draw_gate_box(
            ax=ax,
            x=x_h,
            y=y,
            width=0.42,
            height=0.50,
            facecolor=purple,
            edgecolor=purple_dark,
            text="H",
            fontsize=8.5,
        )

        # QAOA layers
        for layer in range(
            p
        ):
            base_x = (
                layer_x0
                + layer * layer_width
            )

            gamma_symbol = (
                r"\gamma"
                if p == 1
                else rf"\gamma_{{{layer}}}"
            )

            beta_symbol = (
                r"\beta"
                if p == 1
                else rf"\beta_{{{layer}}}"
            )

            # RZ cost gate
            _draw_gate_box(
                ax=ax,
                x=base_x + cost_x_offset,
                y=y,
                width=1.05,
                height=0.48,
                facecolor=purple,
                edgecolor=purple_dark,
                text=(
                    rf"$R_z(2{gamma_symbol}\,h_{{{qubit}}})$"
                ),
                fontsize=7.2,
            )

            # RX mixer gate
            _draw_gate_box(
                ax=ax,
                x=base_x + mixer_x_offset,
                y=y,
                width=1.02,
                height=0.48,
                facecolor=cyan,
                edgecolor=cyan_dark,
                text=(
                    rf"$R_x(2{beta_symbol})$"
                ),
                fontsize=7.6,
            )

            # measurement shown only after final layer
            if layer == p - 1:
                mx = (
                    base_x
                    + measure_x_offset
                )

                rect = Rectangle(
                    (
                        mx - 0.25,
                        y - 0.24,
                    ),
                    0.50,
                    0.48,
                    facecolor="white",
                    edgecolor=measurement_edge,
                    linewidth=1.0,
                )

                ax.add_patch(
                    rect
                )

                ax.text(
                    mx,
                    y,
                    "M",
                    ha="center",
                    va="center",
                    fontsize=9,
                    color="black",
                )

    # --------------------------------------------------------
    # Representative REAL RZZ interactions for first layer
    # --------------------------------------------------------

    interaction_terms = (
        _strongest_visible_interactions(
            display_qubits=(
                display_qubits
            ),
            j_upper=j_upper,
            max_terms=6,
        )
    )

    if interaction_terms:
        interaction_x_start = (
            layer_x0
            + zz_x_offset
        )

        for term_index, (
            _,
            i,
            j,
            coefficient,
        ) in enumerate(
            interaction_terms
        ):
            if (
                i not in y_positions
                or j not in y_positions
            ):
                continue

            yi = y_positions[
                i
            ]

            yj = y_positions[
                j
            ]

            # offset successive interactions to reduce overlap
            x = (
                interaction_x_start
                + 0.20
                * (
                    term_index
                    % 3
                )
            )

            ax.plot(
                [
                    x,
                    x,
                ],
                [
                    yi,
                    yj,
                ],
                color=purple_dark,
                linewidth=0.8,
                alpha=0.9,
                zorder=2,
            )

            ax.scatter(
                [
                    x,
                    x,
                ],
                [
                    yi,
                    yj,
                ],
                s=12,
                color=purple_dark,
                zorder=3,
            )

            midpoint = (
                0.5
                * (
                    yi
                    + yj
                )
            )

            ax.text(
                x + 0.06,
                midpoint,
                (
                    rf"$R_{{ZZ}}(2\gamma\,J_{{{i},{j}}})$"
                ),
                rotation=90,
                ha="left",
                va="center",
                fontsize=6.3,
                color="black",
            )

    # --------------------------------------------------------
    # Right-side mathematical panel
    # --------------------------------------------------------

    panel_y_bottom = (
        y_top
        - row_count
        + 2.2
    )

    panel_y_top = (
        y_top
        - 0.55
    )

    panel_height = (
        panel_y_top
        - panel_y_bottom
    )

    panel = Rectangle(
        (
            panel_x,
            panel_y_bottom,
        ),
        panel_width,
        panel_height,
        facecolor=panel_fill,
        edgecolor=panel_edge,
        linewidth=1.1,
    )

    ax.add_patch(
        panel
    )

    panel_center_x = (
        panel_x
        + panel_width / 2
    )

    ax.text(
        panel_center_x,
        panel_y_top - 0.42,
        "EIS QUBO Surrogate",
        ha="center",
        va="center",
        fontsize=12.5,
        color=navy,
        fontweight="bold",
    )

    ax.text(
        panel_center_x,
        panel_y_top - 0.92,
        r"$\longrightarrow$ Ising Form",
        ha="center",
        va="center",
        fontsize=12,
        color=navy,
        fontweight="bold",
        family="serif",
    )

    eq_y = (
        panel_y_top
        - 1.75
    )

    ax.text(
        panel_center_x,
        eq_y,
        (
            r"$H_C = \sum_i h_i Z_i"
            r"+\sum_{i<j}J_{ij}Z_iZ_j$"
        ),
        ha="center",
        va="center",
        fontsize=10.5,
        family="serif",
    )

    ax.text(
        panel_center_x,
        eq_y - 0.95,
        r"$U_C(\gamma)=e^{-i\gamma H_C}$",
        ha="center",
        va="center",
        fontsize=10.5,
        family="serif",
    )

    ax.text(
        panel_center_x,
        eq_y - 1.90,
        r"$U_B(\beta)=e^{-i\beta\sum_iX_i}$",
        ha="center",
        va="center",
        fontsize=10.5,
        family="serif",
    )

    ax.text(
        panel_center_x,
        panel_y_bottom + 0.55,
        r"Minimize: $\langle H_C\rangle_{\gamma,\beta}$",
        ha="center",
        va="center",
        fontsize=10,
        family="serif",
        fontstyle="italic",
    )

    # --------------------------------------------------------
    # Bottom workflow note
    # --------------------------------------------------------

    bottom_y = (
        y_top
        - row_count
        + 0.45
    )

    ax.text(
        (
            x_wire_start
            + panel_x
            + panel_width
        ) / 2,
        bottom_y,
        (
            r"Sample bitstrings $\rightarrow$ canonical "
            r"$x_0\ldots x_{20}$ $\rightarrow$ "
            r"decode 3-bit $\times$ 7 EIS parameters "
            r"$\rightarrow$ compute $Z_{\mathrm{fit}}(\omega)$"
        ),
        ha="center",
        va="center",
        fontsize=9.3,
        family="serif",
        fontstyle="italic",
        color="black",
    )

    # subtle bottom rule
    ax.plot(
        [
            x_label - 0.35,
            panel_x + panel_width,
        ],
        [
            bottom_y - 0.55,
            bottom_y - 0.55,
        ],
        color=navy,
        linewidth=0.75,
    )

    # --------------------------------------------------------
    # Figure bounds
    # --------------------------------------------------------

    ax.set_xlim(
        0.0,
        panel_x + panel_width + 0.35,
    )

    ax.set_ylim(
        bottom_y - 0.9,
        y_top + 1.8,
    )

    fig.tight_layout()

    outputs = save_figure_all_formats(
        fig,
        output_dir
        / "qaoa_short_publication_circuit",
        dpi=dpi,
    )

    plt.close(
        fig
    )

    return outputs


# ============================================================
# Full gate-level Qiskit circuit
# ============================================================

def draw_full_logical_circuit(
    circuit,
    output_dir: Path,
    dpi: int,
    fold: int,
    scale: float,
) -> dict:
    """
    Draw the actual logical gate-level circuit generated by the current
    qaoa_circuit.py.

    This figure includes all RZ/RZZ/RX gates represented in the logical
    circuit and is therefore best suited to supplementary material.
    """

    figure = circuit.draw(
        output="mpl",
        fold=int(
            fold
        ),
        scale=float(
            scale
        ),
        idle_wires=False,
    )

    outputs = save_figure_all_formats(
        figure,
        output_dir
        / "qaoa_full_logical_circuit",
        dpi=dpi,
    )

    plt.close(
        figure
    )

    return outputs


# ============================================================
# Run one dataset
# ============================================================

def generate_dataset_figures(
    dataset: str,
    p: int,
    dpi: int,
    fold: int,
    scale: float,
    skip_full: bool,
) -> dict:
    dataset = dataset.upper()

    print()
    print(
        "=" * 76
    )

    print(
        f"{dataset} — QAOA PUBLICATION FIGURE GENERATOR"
    )

    print(
        "=" * 76
    )

    (
        ising_model,
        h,
        j_upper,
    ) = load_ising_coefficients(
        dataset
    )

    n_qubits = len(
        h
    )

    # Visualization circuit uses barriers so Cost/Mixer layer boundaries
    # remain readable in the gate-level figure.
    visualization_circuit = (
        build_qaoa_circuit(
            h=h,
            j_upper=j_upper,
            p=p,
            measure=False,
            barriers=True,
        )
    )

    # Also construct the actual execution form to confirm the two circuits
    # contain the same logical model and parameter count.
    execution_circuit = (
        build_qaoa_circuit(
            h=h,
            j_upper=j_upper,
            p=p,
            measure=False,
            barriers=False,
        )
    )

    visualization_stats = (
        get_circuit_statistics(
            visualization_circuit
        )
    )

    execution_stats = (
        get_circuit_statistics(
            execution_circuit
        )
    )

    if (
        execution_circuit.num_qubits
        != visualization_circuit.num_qubits
    ):
        raise RuntimeError(
            "Execution and visualization circuits have different "
            "qubit counts."
        )

    if (
        len(
            execution_circuit.parameters
        )
        != 2 * p
    ):
        raise RuntimeError(
            f"Expected {2 * p} variational parameters, found "
            f"{len(execution_circuit.parameters)}."
        )

    output_dir = resolve_output_dir(
        dataset,
        p,
    )

    compact_outputs = (
        draw_publication_schematic(
            dataset=dataset,
            n_qubits=n_qubits,
            p=p,
            h=h,
            j_upper=j_upper,
            output_dir=output_dir,
            dpi=dpi,
        )
    )

    short_outputs = (
        draw_short_publication_circuit(
            dataset=dataset,
            n_qubits=n_qubits,
            p=p,
            h=h,
            j_upper=j_upper,
            output_dir=output_dir,
            dpi=dpi,
        )
    )

    full_outputs = {}

    if not skip_full:
        full_outputs = (
            draw_full_logical_circuit(
                circuit=visualization_circuit,
                output_dir=output_dir,
                dpi=dpi,
                fold=fold,
                scale=scale,
            )
        )

    validation = ising_model.get(
        "energy_equivalence_validation",
        {},
    )

    exact_ground = ising_model.get(
        "exact_ising_minimum",
        {},
    )

    report = {
        "dataset":
            dataset,

        "p":
            int(
                p
            ),

        "number_of_qubits":
            int(
                n_qubits
            ),

        "number_of_variational_parameters":
            int(
                2 * p
            ),

        "nonzero_h_terms":
            int(
                np.count_nonzero(
                    h
                )
            ),

        "nonzero_J_terms":
            int(
                np.count_nonzero(
                    np.triu(
                        j_upper,
                        k=1,
                    )
                )
            ),

        "source_qubo_minimum_match":
            bool(
                ising_model.get(
                    "source_qubo_minimum_match",
                    False,
                )
            ),

        "energy_equivalence_validation":
            validation,

        "exact_ground_bitstring":
            exact_ground.get(
                "bitstring"
            ),

        "exact_ground_energy":
            exact_ground.get(
                "ising_energy"
            ),

        "bit_mapping":
            (
                "qubit i <-> binary variable x_i"
            ),

        "canonical_bit_order":
            (
                "x0 x1 ... x20"
            ),

        "qiskit_counts_order":
            (
                "x20 ... x1 x0"
            ),

        "compact_figure":
            compact_outputs,

        "short_publication_circuit":
            short_outputs,

        "full_gate_figure":
            full_outputs,

        "execution_circuit_statistics":
            execution_stats,

        "visualization_circuit_statistics":
            visualization_stats,

        "recommended_main_text_figure":
            (
                "qaoa_short_publication_circuit.pdf"
            ),

        "alternative_main_text_figure":
            (
                "qaoa_publication_schematic.pdf"
            ),

        "recommended_supplementary_figure":
            (
                "qaoa_full_logical_circuit.pdf"
                if not skip_full
                else None
            ),
    }

    report_path = (
        output_dir
        / "qaoa_publication_figure_report.json"
    )

    with report_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report,
            file,
            indent=2,
        )

    print(
        f"Qubits                  : {n_qubits}"
    )

    print(
        f"QAOA p                  : {p}"
    )

    print(
        f"Variational parameters  : {2 * p}"
    )

    print(
        f"Nonzero h terms         : "
        f"{report['nonzero_h_terms']}"
    )

    print(
        f"Nonzero J terms         : "
        f"{report['nonzero_J_terms']}"
    )

    print(
        "QUBO -> Ising validation: "
        f"{bool(validation.get('passed', False))}"
    )

    print(
        "QUBO/Ising min match    : "
        f"{report['source_qubo_minimum_match']}"
    )

    print(
        "Short publication figure: "
        "qaoa_short_publication_circuit.pdf"
    )

    print()
    print(
        "Saved publication figures to:"
    )

    print(
        output_dir
    )

    print(
        "=" * 76
    )

    return report


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate publication-ready QAOA circuit figures "
            "from the current qaoa_circuit.py."
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
        "--p",
        type=int,
        default=1,
        help=(
            "QAOA depth. Use the same p as the circuit/optimizer run."
        ),
    )

    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
    )

    parser.add_argument(
        "--fold",
        type=int,
        default=60,
        help=(
            "Qiskit circuit fold value for the full gate-level figure."
        ),
    )

    parser.add_argument(
        "--scale",
        type=float,
        default=0.55,
        help=(
            "Qiskit mpl circuit scale for the full gate-level figure."
        ),
    )

    parser.add_argument(
        "--skip-full",
        action="store_true",
        help=(
            "Generate only the compact publication schematic."
        ),
    )

    args = parser.parse_args()

    if args.p < 1:
        raise ValueError(
            "--p must be >= 1."
        )

    if args.dpi < 72:
        raise ValueError(
            "--dpi should be >= 72."
        )

    ensure_current_circuit_interface()

    datasets = (
        [
            "EIS13",
            "EIS14",
        ]
        if args.dataset == "BOTH"
        else [
            args.dataset
        ]
    )

    for dataset in datasets:
        generate_dataset_figures(
            dataset=dataset,
            p=args.p,
            dpi=args.dpi,
            fold=args.fold,
            scale=args.scale,
            skip_full=args.skip_full,
        )


if __name__ == "__main__":
    main()
