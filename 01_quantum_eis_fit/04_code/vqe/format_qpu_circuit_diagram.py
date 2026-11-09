"""
format_qpu_circuit_diagram.py

MUC TIEU
========
Quet 5 thu muc hardware RAW trong <PROJECT_ROOT>/results/:

    hardware_qpu_5runs_unfixed_transpiler/
    hardware_qpu_5runs_unfixed_transpiler_ibm_kingston/
    hardware_qpu_5runs_unfixed_transpiler_real/
    hardware_qpu_5seed_only_optimizer_changed/
    hardware_qpu/

NGUYEN TAC DISCOVERY
====================
- Chi can tim thay anh circuit_diagram.* / hardware_circuit_diagram.* trong 4 nguon tren.
- Script tim QPY RAW nam CUNG thu muc voi diagram:
      circuit_diagram.*          -> uu tien circuit.qpy
      hardware_circuit_diagram.* -> uu tien hardware_circuit.qpy
- Neu co diagram nhung khong co QPY cung thu muc, script KHONG doan circuit; no ghi vao
  rejected_diagrams.csv de kiem tra.

NGUYEN TAC KHOA HOC
===================
1. KHONG build lai HEA.
2. KHONG nap theta tu optimizer de tao circuit moi.
3. KHONG transpile lai.
4. KHONG append/copy/move/drop gate.
5. KHONG remap circuit data.
6. Chi qpy.load() RAW hardware circuit va dung QuantumCircuit.draw(wire_order=...)
   de DOI THU TU HIEN THI cac dong thanh q0, q1, ... q6.
7. Thu tu q0..q6 lay tu initial_index_layout (hoac initial_layout + input_qubit_mapping)
   cua CHINH RAW QPY. Khong dung sorted physical qubit de doan logical mapping.
8. Structural SHA256 duoc kiem tra truoc/sau khi draw. Neu circuit thay doi, script dung.

OUTPUT
======
    <PROJECT_ROOT>/experiments/HEA_VQE/shared_artifacts/isa_wire_ordered/
        hardware_qpu_5runs_unfixed_transpiler/...
        hardware_qpu_5runs_unfixed_transpiler_ibm_kingston/...
        hardware_qpu_5runs_unfixed_transpiler_real/...
        hardware_qpu_5seed_only_optimizer_changed/...
        hardware_qpu/...
        master_index.csv
        rejected_diagrams.csv
        master_manifest.json

Moi circuit output co:
- raw_source.qpy                  : byte-for-byte copy cua QPY hardware
- raw_saved_diagram.*            : copy diagram hardware goc
- qpy_redraw_diagram.png         : Qiskit raw/default wire order
- logical_order_diagram.png      : CUNG circuit, chi doi display row q0..q6
- logical_to_physical_mapping.csv/.json
- isa_metadata.json

QUAN TRONG: logical_order_diagram.png KHONG phai circuit duoc rebuild. No la cung object
RAW ISA, gate sequence/parameters/physical wire indices giu nguyen; chi doi row display.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import qiskit
from qiskit import QuantumCircuit, qpy


# =============================================================================
# 1. PROJECT CONFIGURATION
# =============================================================================

# Script thuong duoc dat tai <PROJECT_ROOT>/shared_tools/, nhung ham nay cung
# ho tro chay tu noi khac: tim ancestor/cwd co results/<TARGET_SOURCE_DIR>.
TARGET_SOURCE_DIRS = [
    "hardware_qpu_5runs_unfixed_transpiler",
    "hardware_qpu_5runs_unfixed_transpiler_ibm_kingston",
    "hardware_qpu_5runs_unfixed_transpiler_real",
    "hardware_qpu_5seed_only_optimizer_changed",
    "hardware_qpu",
]

def detect_project_root() -> Path:
    candidates: list[Path] = []
    here = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    for base in [cwd, here.parent, here.parent.parent]:
        for candidate in [base, *base.parents]:
            if candidate not in candidates:
                candidates.append(candidate)

    for candidate in candidates:
        results = candidate / "results"
        if results.is_dir() and any((results / name).is_dir() for name in TARGET_SOURCE_DIRS):
            return candidate

    # Fallback de loi sau do noi ro duong dan du kien.
    return here.parent.parent

PROJECT_ROOT = detect_project_root()
RESULTS_ROOT = PROJECT_ROOT / "results"

# Giu ten bien EXPERIMENT_NAMES de phan con lai cua script khong can rebuild;
# o ban nay moi "experiment" chinh la mot hardware source folder.
EXPERIMENT_NAMES = TARGET_SOURCE_DIRS
EXPERIMENTS_ROOT = RESULTS_ROOT

DATASET_NAMES = {"EIS13", "EIS14"}
N_LOGICAL_QUBITS = 7

OUTPUT_ROOT = (
    PROJECT_ROOT
    / "experiments"
    / "HEA_VQE"
    / "shared_artifacts"
    / "isa_wire_ordered"
)
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

# Tao san output rieng cho TUNG hardware source.
# Quan trong: hardware_qpu_5runs_unfixed_transpiler se luon co folder output
# de luu cac file wire-ordered moi sau khi script duoc update/chay lai.
SOURCE_OUTPUT_DIRS = {
    source_name: OUTPUT_ROOT / source_name
    for source_name in TARGET_SOURCE_DIRS
}
for source_output_dir in SOURCE_OUTPUT_DIRS.values():
    source_output_dir.mkdir(parents=True, exist_ok=True)

# Chi lay nhung ten QPY duoc hardware pipeline luu.
RAW_QPY_FILENAMES = {
    "circuit.qpy",
    "hardware_circuit.qpy",
}

# Thu muc output/derived khong bao gio duoc xem la raw input.
SKIP_DIR_NAMES = {
    "05_circuit_artifacts",
    "circuit_artifacts",
    "qpu_circuits_reordered",
    "isa_wire_ordered",
    "shared_artifacts",
    "__pycache__",
}

# Marker giup phan biet hardware output voi QPY sinh tu optimizer/simulator.
HARDWARE_MARKERS_FOR_CIRCUIT_QPY = {
    "job_info.json",
    "result.json",
    "config.json",
    "transpiler_info.json",
    "expectations.csv",
}

HARDWARE_MARKERS_FOR_COMMON_ISA = {
    "isa_layout.json",
    "observables.json",
    "hardware_circuit.qasm",
    "hardware_circuit_diagram.png",
}

# Metadata raw nen copy de truy vet.
RAW_METADATA_FILENAMES = {
    "job_info.json",
    "result.json",
    "config.json",
    "transpiler_info.json",
    "isa_layout.json",
    "observables.json",
    "expectations.csv",
    "parameters.csv",
    "summary.csv",
    "theta.csv",
    "theta_source_optimal_angles.csv",
    "isa_parameter_order.csv",
    "experiment_config.json",
}


# =============================================================================
# 2. BASIC HELPERS
# =============================================================================

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_dump_safe(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def detect_dataset(path: Path) -> str:
    for part in path.parts:
        if part in DATASET_NAMES:
            return part
    return "UNKNOWN"


def as_json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# =============================================================================
# 3. RAW HARDWARE SOURCE DISCOVERY
# =============================================================================

def path_contains_skipped_dir(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(part in SKIP_DIR_NAMES for part in rel.parts)


def classify_raw_hardware_qpy(qpy_path: Path) -> tuple[bool, str, list[str]]:
    """
    Chi chap nhan QPY co ten + neighbor files phu hop hardware output.

    Return:
        accepted, source_type, matched_markers
    """
    if qpy_path.name not in RAW_QPY_FILENAMES:
        return False, "not_raw_hardware_filename", []

    sibling_names = {
        p.name
        for p in qpy_path.parent.iterdir()
        if p.is_file()
    }

    if qpy_path.name == "circuit.qpy":
        matched = sorted(sibling_names & HARDWARE_MARKERS_FOR_CIRCUIT_QPY)

        # De tranh lay QPY do optimizer/simulator sinh ra, yeu cau marker manh.
        strong = (
            "job_info.json" in sibling_names
            or "result.json" in sibling_names
            or "transpiler_info.json" in sibling_names
        )
        if strong:
            return True, "raw_hardware_job_circuit", matched
        return False, "circuit_qpy_without_hardware_job_markers", matched

    if qpy_path.name == "hardware_circuit.qpy":
        matched = sorted(sibling_names & HARDWARE_MARKERS_FOR_COMMON_ISA)
        strong = (
            "isa_layout.json" in sibling_names
            or "hardware_circuit.qasm" in sibling_names
            or "hardware_circuit_diagram.png" in sibling_names
        )
        if strong:
            return True, "raw_common_isa_hardware_circuit", matched
        return False, "hardware_circuit_without_common_isa_markers", matched

    return False, "unknown", []


def is_circuit_diagram_file(path: Path) -> bool:
    """Hardware diagram marker requested by the user."""
    if not path.is_file():
        return False
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".svg"}:
        return False
    stem = path.stem.lower()
    return (
        stem == "circuit_diagram"
        or stem == "hardware_circuit_diagram"
        or stem.startswith("circuit_diagram_")
        or stem.startswith("hardware_circuit_diagram_")
    )


def find_qpy_for_diagram(diagram_path: Path) -> tuple[Path | None, str]:
    """
    Tim QPY trong CUNG folder voi circuit_diagram.

    Khong tim sang optimizer folder khac, khong rebuild tu theta.
    """
    parent = diagram_path.parent
    stem = diagram_path.stem.lower()

    preferred: list[Path] = []
    if stem.startswith("hardware_circuit_diagram"):
        preferred.extend([
            parent / "hardware_circuit.qpy",
            parent / "circuit.qpy",
        ])
    else:
        preferred.extend([
            parent / "circuit.qpy",
            parent / "hardware_circuit.qpy",
        ])

    for p in preferred:
        if p.exists() and p.is_file():
            return p, "preferred_same_folder_qpy"

    qpy_files = sorted(p for p in parent.glob("*.qpy") if p.is_file())
    if len(qpy_files) == 1:
        return qpy_files[0], "single_same_folder_qpy"
    if len(qpy_files) > 1:
        return None, "multiple_qpy_in_diagram_folder_ambiguous"
    return None, "diagram_without_same_folder_qpy"


def discover_raw_hardware_qpy() -> tuple[list[dict], list[dict]]:
    """
    Discovery rule moi:
      1) chi quet 5 TARGET_SOURCE_DIRS duoi PROJECT_ROOT/results;
      2) tim circuit_diagram image;
      3) tu diagram tim RAW QPY cung folder;
      4) moi QPY chi xu ly 1 lan, ke ca neu co nhieu diagram marker.
    """
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen_qpy: set[str] = set()

    for source_name in TARGET_SOURCE_DIRS:
        source_root = RESULTS_ROOT / source_name

        if not source_root.exists():
            print(f"[SKIP] source folder khong ton tai: {source_root}")
            continue

        diagrams = [
            p for p in sorted(source_root.rglob("*"))
            if is_circuit_diagram_file(p)
            and not path_contains_skipped_dir(p, source_root)
        ]

        print(f"[DISCOVERY] {source_name}: {len(diagrams)} circuit diagram marker(s)")

        for diagram_path in diagrams:
            qpy_path, resolution = find_qpy_for_diagram(diagram_path)
            relative_diagram = diagram_path.relative_to(source_root)

            if qpy_path is None:
                rejected.append({
                    "experiment": source_name,
                    "dataset": detect_dataset(relative_diagram),
                    "qpy_path": diagram_path,  # de report co path chung
                    "relative_path": relative_diagram,
                    "source_type": resolution,
                    "hardware_markers": [diagram_path.name],
                    "diagram_path": diagram_path,
                })
                continue

            key = str(qpy_path.resolve())
            if key in seen_qpy:
                continue
            seen_qpy.add(key)

            relative_qpy = qpy_path.relative_to(source_root)
            accepted.append({
                "experiment": source_name,
                "experiment_root": source_root,
                "results_root": source_root,
                "qpy_path": qpy_path,
                "relative_path": relative_qpy,
                "dataset": detect_dataset(relative_qpy),
                "source_type": f"diagram_discovered_raw_qpy:{resolution}",
                "hardware_markers": [diagram_path.name],
                "source_saved_diagram": diagram_path,
            })

    return accepted, rejected


# =============================================================================
# 4. EXACT CIRCUIT INSPECTION - NO MODIFICATION
# =============================================================================

def meaningful_active_qubits(circuit: QuantumCircuit) -> list[int]:
    """
    Physical wire indices that carry non-measure/non-barrier operations.
    Chi doc; khong dung de remap.
    """
    active: set[int] = set()
    for instruction in circuit.data:
        if instruction.operation.name in {"measure", "barrier"}:
            continue
        for q in instruction.qubits:
            active.add(int(circuit.find_bit(q).index))
    return sorted(active)


def all_operated_qubits(circuit: QuantumCircuit) -> list[int]:
    used: set[int] = set()
    for instruction in circuit.data:
        for q in instruction.qubits:
            used.add(int(circuit.find_bit(q).index))
    return sorted(used)


def safe_param_repr(param: Any) -> Any:
    """Stable-enough representation for structural audit hashing."""
    if isinstance(param, bool):
        return param
    if isinstance(param, int):
        return int(param)
    if isinstance(param, float):
        if math.isnan(param):
            return "NaN"
        if math.isinf(param):
            return "+Inf" if param > 0 else "-Inf"
        return format(param, ".17g")

    try:
        value = float(param)
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "+Inf" if value > 0 else "-Inf"
        return format(value, ".17g")
    except Exception:
        return str(param)


def structural_payload(circuit: QuantumCircuit) -> dict[str, Any]:
    """
    Canonical audit representation.

    QUAN TRONG:
    - Khong thay doi circuit.
    - Qubit indices la indices TRONG RAW ISA QPY.
    - Vi vay structural hash bao gom ca physical-wire placement cua gate.
    """
    operations = []

    for instruction in circuit.data:
        op = instruction.operation
        q_indices = [
            int(circuit.find_bit(q).index)
            for q in instruction.qubits
        ]
        c_indices = [
            int(circuit.find_bit(c).index)
            for c in instruction.clbits
        ]

        operations.append(
            {
                "name": op.name,
                "qubits": q_indices,
                "clbits": c_indices,
                "params": [safe_param_repr(p) for p in op.params],
            }
        )

    return {
        "num_qubits": int(circuit.num_qubits),
        "num_clbits": int(circuit.num_clbits),
        "global_phase": str(getattr(circuit, "global_phase", 0)),
        "operations": operations,
    }


def circuit_structural_sha256(circuit: QuantumCircuit) -> str:
    payload = structural_payload(circuit)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(text)


def safe_layout_metadata(circuit: QuantumCircuit) -> dict[str, Any]:
    """
    DOC layout metadata only.
    Tuyet doi KHONG dung mapping nay de rebuild/reorder circuit.
    """
    layout = getattr(circuit, "layout", None)
    if layout is None:
        return {
            "layout_present": False,
            "layout_repr": None,
            "initial_layout_repr": None,
            "final_layout_repr": None,
            "input_qubit_mapping_repr": None,
            "final_index_layout_filter_ancillas_true": None,
        }

    final_index = None
    final_index_fn = getattr(layout, "final_index_layout", None)
    if callable(final_index_fn):
        try:
            final_index = [
                int(x)
                for x in final_index_fn(filter_ancillas=True)
            ]
        except Exception:
            final_index = None

    return {
        "layout_present": True,
        "layout_repr": str(layout),
        "initial_layout_repr": str(getattr(layout, "initial_layout", None)),
        "final_layout_repr": str(getattr(layout, "final_layout", None)),
        "input_qubit_mapping_repr": str(getattr(layout, "input_qubit_mapping", None)),
        "final_index_layout_filter_ancillas_true": final_index,
        "IMPORTANT": (
            "Layout metadata is recorded for audit only. "
            "It is NOT applied to reorder/remap any gate or qubit."
        ),
    }


# =============================================================================
# 4B. LOGICAL-ORDER DISPLAY MAPPING (DISPLAY ONLY - NO CIRCUIT MODIFICATION)
# =============================================================================

def get_initial_logical_to_physical(
    circuit: QuantumCircuit,
    n_logical: int = N_LOGICAL_QUBITS,
) -> tuple[list[int] | None, dict[str, Any]]:
    """
    Return logical q0..q(n-1) -> physical/output-wire index selected at the
    INITIAL layout stage.

    This mapping is used ONLY to order rows in the drawing.  It is NOT applied
    to circuit.data and is never used to rebuild/transpile the circuit.

    Preferred source: TranspileLayout.initial_index_layout(filter_ancillas=True)
    because it directly maps each INPUT logical-qubit position to its output
    physical-wire position after the layout stage.
    """
    layout = getattr(circuit, "layout", None)
    info: dict[str, Any] = {
        "mapping_source": None,
        "mapping_confidence": "none",
        "logical_to_initial_physical": None,
        "logical_to_final_physical": None,
        "routing_changed_logical_positions": None,
        "warning": None,
    }

    if layout is None:
        info["warning"] = "Circuit has no TranspileLayout metadata."
        return None, info

    initial_map = None

    # Best / most direct Qiskit API.
    initial_index_fn = getattr(layout, "initial_index_layout", None)
    if callable(initial_index_fn):
        try:
            values = list(initial_index_fn(filter_ancillas=True))
            if len(values) >= n_logical:
                candidate = [int(x) for x in values[:n_logical]]
                if len(set(candidate)) == n_logical:
                    initial_map = candidate
                    info["mapping_source"] = "initial_index_layout"
                    info["mapping_confidence"] = "high"
        except Exception:
            pass

    # Fallback: reconstruct from initial_layout + input_qubit_mapping.
    if initial_map is None:
        initial_layout = getattr(layout, "initial_layout", None)
        input_map = getattr(layout, "input_qubit_mapping", None)
        if initial_layout is not None and isinstance(input_map, dict):
            try:
                virtual_to_physical = initial_layout.get_virtual_bits()
                candidate_dict: dict[int, int] = {}
                for virtual_bit, physical in virtual_to_physical.items():
                    if virtual_bit not in input_map:
                        continue
                    logical_index = int(input_map[virtual_bit])
                    if 0 <= logical_index < n_logical:
                        candidate_dict[logical_index] = int(physical)

                if set(candidate_dict) == set(range(n_logical)):
                    candidate = [candidate_dict[i] for i in range(n_logical)]
                    if len(set(candidate)) == n_logical:
                        initial_map = candidate
                        info["mapping_source"] = (
                            "initial_layout_plus_input_qubit_mapping"
                        )
                        info["mapping_confidence"] = "high"
            except Exception:
                pass

    # Record final positions as metadata so routing-induced permutation can be
    # seen explicitly.  This is NOT used for the row order.
    final_map = None
    final_index_fn = getattr(layout, "final_index_layout", None)
    if callable(final_index_fn):
        try:
            values = list(final_index_fn(filter_ancillas=True))
            if len(values) >= n_logical:
                candidate = [int(x) for x in values[:n_logical]]
                if len(set(candidate)) == n_logical:
                    final_map = candidate
        except Exception:
            pass

    info["logical_to_initial_physical"] = initial_map
    info["logical_to_final_physical"] = final_map

    if initial_map is not None and final_map is not None:
        changed = [
            i for i, (p0, pf) in enumerate(zip(initial_map, final_map))
            if p0 != pf
        ]
        info["routing_changed_logical_positions"] = changed
        if changed:
            info["warning"] = (
                "Routing changed the final logical positions for q"
                + ", q".join(str(i) for i in changed)
                + ". logical_order_diagram is ordered by INITIAL logical mapping "
                  "for comparison with the original HEA. Gates remain on their "
                  "raw physical wires; the figure does not dynamically relabel "
                  "logical states after routing."
            )

    if initial_map is None:
        info["warning"] = (
            "Could not determine a complete q0..q6 INITIAL logical-to-physical "
            "mapping. No logical-order diagram will be generated."
        )
        return None, info

    # Basic range sanity check against the raw circuit bit list.
    if any(p < 0 or p >= circuit.num_qubits for p in initial_map):
        info["warning"] = (
            "Initial layout contains a physical index outside circuit.num_qubits; "
            "logical-order diagram skipped."
        )
        return None, info

    return initial_map, info


def build_display_wire_order(
    circuit: QuantumCircuit,
    logical_to_physical: list[int],
) -> list[int]:
    """
    Build a FULL wire_order permutation required by QuantumCircuit.draw().

    First rows: physical wires corresponding to input logical q0, q1, ..., q6.
    Remaining quantum wires: original numeric order.
    Classical bits: original order after all quantum bits.

    This list is a visualization argument only. It never mutates the circuit.
    """
    logical_set = set(logical_to_physical)

    remaining_qubits = [
        i for i in range(circuit.num_qubits)
        if i not in logical_set
    ]

    classical_bits = [
        circuit.num_qubits + i
        for i in range(circuit.num_clbits)
    ]

    wire_order = list(logical_to_physical) + remaining_qubits + classical_bits

    expected = list(range(circuit.num_qubits + circuit.num_clbits))
    if sorted(wire_order) != expected:
        raise RuntimeError(
            "Internal error: display wire_order is not a full permutation "
            "of all circuit bits."
        )

    return wire_order


def save_logical_order_diagram(
    circuit: QuantumCircuit,
    output_dir: Path,
    logical_to_physical: list[int],
) -> dict[str, Any]:
    """
    Draw the SAME raw ISA circuit with rows ordered q0..q6.

    Critical safety property:
      * No QuantumCircuit is constructed.
      * No operation is appended/copied/moved.
      * No transpilation is performed.
      * Only circuit.draw(..., wire_order=...) is called.

    A structural hash is checked before and after drawing to verify that the
    circuit object was not modified by the visualization step.
    """
    png_path = output_dir / "logical_order_diagram.png"
    txt_path = output_dir / "logical_order_diagram.txt"

    wire_order = build_display_wire_order(circuit, logical_to_physical)
    hash_before = circuit_structural_sha256(circuit)

    result: dict[str, Any] = {
        "saved": False,
        "path": None,
        "wire_order": wire_order,
        "logical_first_physical_wires": logical_to_physical,
        "structural_sha256_before_draw": hash_before,
        "structural_sha256_after_draw": None,
        "circuit_unchanged_after_draw": None,
        "error": None,
    }

    try:
        fig = circuit.draw(
            output="mpl",
            fold=35,
            idle_wires=False,
            with_layout=True,
            wire_order=wire_order,
        )
        fig.savefig(
            png_path,
            dpi=300,
            bbox_inches="tight",
        )
        plt.close(fig)
        result["saved"] = True
        result["path"] = str(png_path)

    except Exception as exc:
        result["error"] = str(exc)
        try:
            text = str(
                circuit.draw(
                    output="text",
                    fold=120,
                    idle_wires=False,
                    with_layout=True,
                    wire_order=wire_order,
                )
            )
            txt_path.write_text(
                f"Matplotlib draw error: {exc}\n\n{text}",
                encoding="utf-8",
            )
            result["path"] = str(txt_path)
        except Exception as text_exc:
            result["error"] += f" | text fallback failed: {text_exc}"

    hash_after = circuit_structural_sha256(circuit)
    result["structural_sha256_after_draw"] = hash_after
    result["circuit_unchanged_after_draw"] = (hash_before == hash_after)

    if hash_before != hash_after:
        raise RuntimeError(
            "Safety check failed: circuit structural hash changed after drawing."
        )

    return result


def save_logical_mapping_files(
    output_dir: Path,
    mapping_info: dict[str, Any],
) -> tuple[str, str]:
    """Save auditable q0..q6 -> physical-wire mapping in JSON and CSV."""
    json_path = output_dir / "logical_to_physical_mapping.json"
    csv_path = output_dir / "logical_to_physical_mapping.csv"

    json_dump_safe(mapping_info, json_path)

    initial_map = mapping_info.get("logical_to_initial_physical") or []
    final_map = mapping_info.get("logical_to_final_physical") or []

    rows = []
    for logical_idx in range(N_LOGICAL_QUBITS):
        initial_p = initial_map[logical_idx] if logical_idx < len(initial_map) else None
        final_p = final_map[logical_idx] if logical_idx < len(final_map) else None
        rows.append({
            "logical_qubit": f"q{logical_idx}",
            "logical_index": logical_idx,
            "initial_physical_wire": initial_p,
            "final_physical_wire": final_p,
            "changed_by_routing": (
                None if initial_p is None or final_p is None
                else bool(initial_p != final_p)
            ),
            "display_row": logical_idx if initial_p is not None else None,
            "mapping_source": mapping_info.get("mapping_source"),
        })

    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return str(json_path), str(csv_path)


# =============================================================================
# 5. PROVENANCE / ORIGINAL FILES
# =============================================================================

def find_original_saved_diagram(qpy_path: Path) -> Path | None:
    candidates = []

    if qpy_path.name == "circuit.qpy":
        candidates.extend([
            qpy_path.parent / "circuit_diagram.png",
            qpy_path.parent / "circuit_diagram.jpg",
            qpy_path.parent / "circuit_diagram.jpeg",
        ])

    if qpy_path.name == "hardware_circuit.qpy":
        candidates.extend([
            qpy_path.parent / "hardware_circuit_diagram.png",
            qpy_path.parent / "hardware_circuit_diagram.jpg",
            qpy_path.parent / "hardware_circuit_diagram.jpeg",
        ])

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def find_source_qasm(qpy_path: Path) -> Path | None:
    candidate = qpy_path.with_suffix(".qasm")
    return candidate if candidate.exists() and candidate.is_file() else None


def collect_nearby_raw_metadata_files(qpy_path: Path) -> list[Path]:
    """
    Copy metadata ngay tai hardware artifact folder.
    Voi common_isa, cung xem parent job folder 1 cap de lay metadata neu co.
    """
    roots = [qpy_path.parent]
    if qpy_path.parent.name == "common_isa":
        roots.append(qpy_path.parent.parent)

    found: list[Path] = []
    seen: set[str] = set()

    for root in roots:
        if not root.exists():
            continue
        for name in RAW_METADATA_FILENAMES:
            p = root / name
            if p.exists() and p.is_file():
                key = str(p.resolve())
                if key not in seen:
                    found.append(p)
                    seen.add(key)

    return sorted(found)


def try_read_json(path: Path) -> Any | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def extract_job_backend_info(qpy_path: Path) -> dict[str, Any]:
    info = {
        "job_id": None,
        "backend": None,
        "optimizer_seed": None,
        "hardware_run_index": None,
    }

    # 1) Prefer config.json
    config_candidates = [
        qpy_path.parent / "config.json",
        qpy_path.parent.parent / "config.json",
    ]
    for p in config_candidates:
        if not p.exists():
            continue
        data = try_read_json(p)
        if isinstance(data, dict):
            info["job_id"] = info["job_id"] or data.get("job_id")
            info["backend"] = info["backend"] or data.get("backend")
            info["optimizer_seed"] = (
                info["optimizer_seed"]
                if info["optimizer_seed"] is not None
                else data.get("optimizer_seed")
            )
            info["hardware_run_index"] = (
                info["hardware_run_index"]
                if info["hardware_run_index"] is not None
                else data.get("hardware_run_index")
            )

    # 2) job_info.json fallback
    job_candidates = [
        qpy_path.parent / "job_info.json",
        qpy_path.parent.parent / "job_info.json",
    ]
    for p in job_candidates:
        if not p.exists():
            continue
        data = try_read_json(p)
        if not isinstance(data, dict):
            continue
        details = data.get("Details", {})
        if isinstance(details, dict):
            info["job_id"] = info["job_id"] or details.get("Job ID")
            info["backend"] = info["backend"] or details.get("Quantum computer")
            if info["optimizer_seed"] is None:
                info["optimizer_seed"] = details.get("Optimizer seed")
            if info["hardware_run_index"] is None:
                info["hardware_run_index"] = details.get("Hardware run index")

    return info


# =============================================================================
# 6. OUTPUT PATH / EXACT COPY
# =============================================================================

def build_output_dir(source_info: dict, circuit_index: int, total_circuits: int) -> Path:
    # Mirror path duoi experiment/results de truy vet 1:1.
    rel = source_info["relative_path"]
    source_output_root = SOURCE_OUTPUT_DIRS[source_info["experiment"]]
    base = (
        source_output_root
        / rel.parent
        / qpy_path_to_output_name(rel.name)
    )

    if total_circuits > 1:
        base = base / f"circuit_{circuit_index:02d}"

    base.mkdir(parents=True, exist_ok=True)
    return base


def qpy_path_to_output_name(filename: str) -> str:
    return Path(filename).stem


def copy_exact(src: Path, dst: Path) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    src_hash = sha256_file(src)
    dst_hash = sha256_file(dst)
    return {
        "source": str(src),
        "copy": str(dst),
        "source_sha256": src_hash,
        "copy_sha256": dst_hash,
        "byte_exact_verified": src_hash == dst_hash,
    }


# =============================================================================
# 7. DRAW RAW QPY WITHOUT ALTERING IT
# =============================================================================

def save_qpy_redraw(circuit: QuantumCircuit, output_dir: Path) -> str:
    """
    Ve truc tiep circuit da qpy.load().

    idle_wires=False CHI AN cac wire khong co gate tren HINH.
    No khong xoa/sua wire trong circuit va khong save circuit moi.
    """
    png_path = output_dir / "qpy_redraw_diagram.png"
    txt_path = output_dir / "qpy_redraw_diagram.txt"

    try:
        fig = circuit.draw(
            output="mpl",
            fold=35,
            idle_wires=False,
        )
        fig.savefig(
            png_path,
            dpi=300,
            bbox_inches="tight",
        )
        plt.close(fig)
        return str(png_path)
    except Exception as exc:
        text = str(
            circuit.draw(
                output="text",
                fold=120,
                idle_wires=False,
            )
        )
        txt_path.write_text(
            f"Matplotlib draw error: {exc}\n\n{text}",
            encoding="utf-8",
        )
        return str(txt_path)


# =============================================================================
# 8. PROCESS ONE RAW QPY
# =============================================================================

def process_raw_qpy(source_info: dict) -> list[dict[str, Any]]:
    qpy_path: Path = source_info["qpy_path"]

    try:
        with open(qpy_path, "rb") as f:
            circuits = list(qpy.load(f))
        if not circuits:
            raise ValueError("QPY contains no circuit")
    except Exception as exc:
        print(f"[ERROR] Cannot load raw QPY: {qpy_path}\n        {exc}")
        return [{
            "status": "FAILED_TO_LOAD_RAW_QPY",
            "experiment": source_info["experiment"],
            "dataset": source_info["dataset"],
            "source_qpy": str(qpy_path),
            "source_type": source_info["source_type"],
            "error": str(exc),
        }]

    source_qpy_hash = sha256_file(qpy_path)
    source_qasm = find_source_qasm(qpy_path)
    source_saved_diagram = source_info.get("source_saved_diagram")
    if source_saved_diagram is None:
        source_saved_diagram = find_original_saved_diagram(qpy_path)
    job_backend = extract_job_backend_info(qpy_path)

    rows = []

    for circuit_index, circuit in enumerate(circuits):
        output_dir = build_output_dir(
            source_info,
            circuit_index=circuit_index,
            total_circuits=len(circuits),
        )

        print("\n" + "=" * 100)
        print(f"EXPERIMENT : {source_info['experiment']}")
        print(f"DATASET    : {source_info['dataset']}")
        print(f"RAW QPY    : {qpy_path}")
        print(f"TYPE       : {source_info['source_type']}")
        print(f"OUTPUT     : {output_dir}")
        print("ACTION     : RAW COPY + DISPLAY-ONLY WIRE REORDER. NO GATE REMAP. NO RETRANSPILE.")
        print("=" * 100)

        # ---------------------------------------------------------------------
        # A. Exact byte-for-byte QPY copy
        # ---------------------------------------------------------------------
        exact_qpy = copy_exact(qpy_path, output_dir / "raw_source.qpy")

        if not exact_qpy["byte_exact_verified"]:
            raise RuntimeError(
                f"SHA256 mismatch while copying {qpy_path}. "
                "Stopping because raw ISA copy is not exact."
            )

        # ---------------------------------------------------------------------
        # B. Exact QASM copy, if raw run saved one
        # ---------------------------------------------------------------------
        exact_qasm = None
        if source_qasm is not None:
            exact_qasm = copy_exact(source_qasm, output_dir / "raw_source.qasm")

        # ---------------------------------------------------------------------
        # C. Exact saved diagram copy, if raw run saved one
        # ---------------------------------------------------------------------
        exact_saved_diagram = None
        if source_saved_diagram is not None:
            suffix = source_saved_diagram.suffix.lower()
            exact_saved_diagram = copy_exact(
                source_saved_diagram,
                output_dir / f"raw_saved_diagram{suffix}",
            )

        # ---------------------------------------------------------------------
        # D. Redraw directly from QPY - RAW visualization only
        # ---------------------------------------------------------------------
        redraw_path = save_qpy_redraw(circuit, output_dir)

        # ---------------------------------------------------------------------
        # D2. Logical q0..q6 row order - DISPLAY ONLY, no circuit modification
        # ---------------------------------------------------------------------
        logical_to_physical, logical_mapping_info = (
            get_initial_logical_to_physical(circuit)
        )

        logical_diagram_info = {
            "saved": False,
            "path": None,
            "error": logical_mapping_info.get("warning"),
        }

        if logical_to_physical is not None:
            logical_diagram_info = save_logical_order_diagram(
                circuit=circuit,
                output_dir=output_dir,
                logical_to_physical=logical_to_physical,
            )

        logical_mapping_json, logical_mapping_csv = save_logical_mapping_files(
            output_dir=output_dir,
            mapping_info=logical_mapping_info,
        )

        # ---------------------------------------------------------------------
        # E. Copy raw metadata for provenance
        # ---------------------------------------------------------------------
        metadata_copy_records = []
        raw_metadata_dir = output_dir / "raw_metadata"

        for metadata_file in collect_nearby_raw_metadata_files(qpy_path):
            # Parent name prefix prevents same filename collision when reading
            # both common_isa and parent job folder.
            if metadata_file.parent == qpy_path.parent:
                out_name = metadata_file.name
            else:
                out_name = f"parent_{metadata_file.name}"

            record = copy_exact(
                metadata_file,
                raw_metadata_dir / out_name,
            )
            metadata_copy_records.append(record)

        # ---------------------------------------------------------------------
        # F. READ-ONLY circuit facts
        # ---------------------------------------------------------------------
        active_qubits = meaningful_active_qubits(circuit)
        operated_qubits = all_operated_qubits(circuit)
        layout_meta = safe_layout_metadata(circuit)
        structural_hash = circuit_structural_sha256(circuit)

        gate_counts = {
            str(name): int(count)
            for name, count in circuit.count_ops().items()
        }

        metadata = {
            "generated_at_utc": now_utc(),
            "scientific_rule": (
                "The source ISA circuit data is never rebuilt, remapped, or "
                "retranspiled. logical_order_diagram changes drawing row order "
                "only via QuantumCircuit.draw(wire_order=...)."
            ),
            "experiment": source_info["experiment"],
            "dataset": source_info["dataset"],
            "source_type": source_info["source_type"],
            "hardware_markers": source_info["hardware_markers"],
            "source_results_root": str(source_info["results_root"]),
            "source_qpy": str(qpy_path),
            "source_qpy_relative": str(source_info["relative_path"]),
            "source_qpy_sha256": source_qpy_hash,
            "exact_qpy_copy": exact_qpy,
            "exact_qasm_copy": exact_qasm,
            "exact_raw_saved_diagram_copy": exact_saved_diagram,
            "qpy_redraw_diagram": redraw_path,
            "logical_order_diagram": logical_diagram_info,
            "logical_to_physical_mapping": logical_mapping_info,
            "logical_mapping_json": logical_mapping_json,
            "logical_mapping_csv": logical_mapping_csv,
            "qiskit_version": getattr(qiskit, "__version__", None),
            "circuit_index_in_qpy": circuit_index,
            "total_circuits_in_qpy": len(circuits),
            "num_qubits": int(circuit.num_qubits),
            "num_clbits": int(circuit.num_clbits),
            "num_parameters": int(circuit.num_parameters),
            "depth": int(circuit.depth()),
            "size": int(circuit.size()),
            "gate_counts": gate_counts,
            "meaningful_active_physical_wire_indices": active_qubits,
            "all_operated_wire_indices": operated_qubits,
            "structural_sha256": structural_hash,
            "structural_hash_definition": (
                "SHA256 of raw operation sequence including operation name, "
                "raw QPY qubit indices, clbit indices, gate parameters, "
                "num_qubits, num_clbits and global phase. No qubit normalization."
            ),
            "layout_metadata_read_only": layout_meta,
            "job_backend_info": job_backend,
            "raw_metadata_copies": metadata_copy_records,
        }

        json_dump_safe(metadata, output_dir / "isa_metadata.json")

        rows.append({
            "status": "OK",
            "experiment": source_info["experiment"],
            "dataset": source_info["dataset"],
            "source_type": source_info["source_type"],
            "source_qpy": str(qpy_path),
            "source_qpy_relative": str(source_info["relative_path"]),
            "source_qpy_sha256": source_qpy_hash,
            "exact_copy_verified": exact_qpy["byte_exact_verified"],
            "circuit_index": circuit_index,
            "job_id": job_backend.get("job_id"),
            "backend": job_backend.get("backend"),
            "optimizer_seed_metadata_only": job_backend.get("optimizer_seed"),
            "hardware_run_index": job_backend.get("hardware_run_index"),
            "num_qubits": int(circuit.num_qubits),
            "depth": int(circuit.depth()),
            "size": int(circuit.size()),
            "gate_counts": as_json_string(gate_counts),
            "active_physical_wire_indices": as_json_string(active_qubits),
            "all_operated_wire_indices": as_json_string(operated_qubits),
            "initial_logical_to_physical_display_order": as_json_string(
                logical_mapping_info.get("logical_to_initial_physical")
            ),
            "final_logical_to_physical_metadata": as_json_string(
                logical_mapping_info.get("logical_to_final_physical")
            ),
            "logical_mapping_source": logical_mapping_info.get("mapping_source"),
            "routing_changed_logical_positions": as_json_string(
                logical_mapping_info.get("routing_changed_logical_positions")
            ),
            "logical_order_diagram": logical_diagram_info.get("path"),
            "logical_order_circuit_unchanged": logical_diagram_info.get(
                "circuit_unchanged_after_draw"
            ),
            "final_index_layout_metadata_only": as_json_string(
                layout_meta.get("final_index_layout_filter_ancillas_true")
            ),
            "structural_sha256": structural_hash,
            "raw_saved_diagram_found": source_saved_diagram is not None,
            "raw_qasm_found": source_qasm is not None,
            "output_dir": str(output_dir),
            "error": None,
        })

        print(f"--> EXACT QPY COPY VERIFIED: {exact_qpy['byte_exact_verified']}")
        print(f"--> SOURCE SHA256          : {source_qpy_hash}")
        print(f"--> STRUCTURAL SHA256      : {structural_hash}")
        print(f"--> ACTIVE RAW WIRE INDEX  : {active_qubits}")
        if logical_to_physical is not None:
            mapping_text = ", ".join(
                f"q{i}->p{p}" for i, p in enumerate(logical_to_physical)
            )
            print(f"--> LOGICAL DISPLAY ORDER  : {mapping_text}")
            print(
                "--> CIRCUIT UNCHANGED DRAW : "
                f"{logical_diagram_info.get('circuit_unchanged_after_draw')}"
            )
        else:
            print(
                "--> LOGICAL DISPLAY ORDER  : SKIPPED | "
                f"{logical_mapping_info.get('warning')}"
            )

    return rows


# =============================================================================
# 9. DUPLICATE / COMPARISON REPORT
# =============================================================================

def save_duplicate_structural_report(master_df: pd.DataFrame) -> Path:
    out_path = OUTPUT_ROOT / "duplicate_structural_groups.csv"

    if master_df.empty or "structural_sha256" not in master_df.columns:
        pd.DataFrame().to_csv(out_path, index=False)
        return out_path

    ok = master_df[master_df["status"] == "OK"].copy()
    if ok.empty:
        pd.DataFrame().to_csv(out_path, index=False)
        return out_path

    counts = Counter(ok["structural_sha256"].dropna().astype(str))
    duplicate_hashes = {h for h, count in counts.items() if count > 1}

    dup = ok[ok["structural_sha256"].astype(str).isin(duplicate_hashes)].copy()
    if not dup.empty:
        dup["same_structure_group_size"] = dup["structural_sha256"].map(counts)
        dup = dup.sort_values(
            ["structural_sha256", "experiment", "dataset", "source_qpy"],
            kind="stable",
        )

    dup.to_csv(out_path, index=False)
    return out_path


def save_rejected_candidates(rejected: list[dict]) -> Path:
    out_path = OUTPUT_ROOT / "rejected_diagrams.csv"
    rows = []
    for item in rejected:
        rows.append({
            "experiment": item["experiment"],
            "dataset": item["dataset"],
            "qpy_path": str(item["qpy_path"]),
            "relative_path": str(item["relative_path"]),
            "reason": item["source_type"],
            "matched_markers": as_json_string(item["hardware_markers"]),
        })
    pd.DataFrame(rows).to_csv(out_path, index=False)
    return out_path


# =============================================================================
# 10. MAIN
# =============================================================================

def main() -> None:
    print("\n" + "=" * 100)
    print("WIRE-ORDER RAW ISA CIRCUITS FROM 5 HARDWARE RESULT SOURCES")
    print("=" * 100)
    print("RULE: RAW QPY ONLY / DISPLAY-ONLY q0..q6 ROW ORDER / NO GATE REMAP / NO RETRANSPILE")
    print(f"PROJECT ROOT : {PROJECT_ROOT}")
    print(f"OUTPUT ROOT  : {OUTPUT_ROOT}")
    print("SOURCE OUTPUTS:")
    for source_name in TARGET_SOURCE_DIRS:
        print(f"  {source_name}: {SOURCE_OUTPUT_DIRS[source_name]}")
    print("=" * 100)

    accepted, rejected = discover_raw_hardware_qpy()

    by_exp = defaultdict(int)
    by_exp_dataset = defaultdict(int)
    for item in accepted:
        by_exp[item["experiment"]] += 1
        by_exp_dataset[(item["experiment"], item["dataset"])] += 1

    print(f"\nDiagram-linked RAW QPY accepted: {len(accepted)}")
    for experiment_name in EXPERIMENT_NAMES:
        print(
            f"  {experiment_name}: {by_exp[experiment_name]} "
            f"(EIS13={by_exp_dataset[(experiment_name, 'EIS13')]}, "
            f"EIS14={by_exp_dataset[(experiment_name, 'EIS14')]})"
        )

    print(f"Rejected/ambiguous diagram candidates: {len(rejected)}")

    if not accepted:
        rejected_path = save_rejected_candidates(rejected)
        print("\n[STOP] Khong tim thay diagram-linked RAW hardware QPY phu hop.")
        print(f"Kiem tra ly do tai: {rejected_path}")
        return

    master_rows: list[dict[str, Any]] = []

    for idx, source_info in enumerate(accepted, start=1):
        print(
            f"\n[{idx}/{len(accepted)}] "
            f"{source_info['experiment']} | "
            f"{source_info['dataset']} | "
            f"{source_info['qpy_path']}"
        )
        try:
            master_rows.extend(process_raw_qpy(source_info))
        except Exception as exc:
            print(f"[ERROR] {source_info['qpy_path']}: {exc}")
            master_rows.append({
                "status": "FAILED_TO_PROCESS",
                "experiment": source_info["experiment"],
                "dataset": source_info["dataset"],
                "source_type": source_info["source_type"],
                "source_qpy": str(source_info["qpy_path"]),
                "source_qpy_relative": str(source_info["relative_path"]),
                "error": str(exc),
            })

    master_df = pd.DataFrame(master_rows)
    master_index = OUTPUT_ROOT / "master_index.csv"
    master_df.to_csv(master_index, index=False)

    duplicate_report = save_duplicate_structural_report(master_df)
    rejected_report = save_rejected_candidates(rejected)

    # Per-experiment index
    if not master_df.empty and "experiment" in master_df.columns:
        for experiment_name in EXPERIMENT_NAMES:
            exp_df = master_df[master_df["experiment"] == experiment_name]
            exp_dir = OUTPUT_ROOT / experiment_name
            exp_dir.mkdir(parents=True, exist_ok=True)
            exp_df.to_csv(exp_dir / "experiment_index.csv", index=False)

    manifest = {
        "generated_at_utc": now_utc(),
        "project_root": str(PROJECT_ROOT),
        "output_root": str(OUTPUT_ROOT),
        "scientific_rule": (
            "Only raw hardware-saved QPY files are accepted. "
            "No optimizer circuit rebuild, no gate/qubit remap in circuit data, "
            "no retranspilation, no new QPU execution. A separate display-only "
            "logical q0..q6 diagram may reorder DRAWING ROWS via wire_order."
        ),
        "accepted_raw_qpy_count": len(accepted),
        "rejected_qpy_candidate_count": len(rejected),
        "master_circuit_record_count": len(master_rows),
        "master_index_csv": str(master_index),
        "duplicate_structural_groups_csv": str(duplicate_report),
        "rejected_qpy_candidates_csv": str(rejected_report),
        "experiment_counts": {
            exp: {
                "raw_qpy": by_exp[exp],
                "EIS13": by_exp_dataset[(exp, "EIS13")],
                "EIS14": by_exp_dataset[(exp, "EIS14")],
            }
            for exp in EXPERIMENT_NAMES
        },
    }
    json_dump_safe(manifest, OUTPUT_ROOT / "master_manifest.json")

    ok_count = 0
    failed_count = 0
    if not master_df.empty and "status" in master_df.columns:
        ok_count = int((master_df["status"] == "OK").sum())
        failed_count = int(len(master_df) - ok_count)

    print("\n" + "=" * 100)
    print("HOAN TAT")
    print("=" * 100)
    print(f"Raw QPY accepted : {len(accepted)}")
    print(f"Circuit records  : {len(master_rows)}")
    print(f"OK               : {ok_count}")
    print(f"Failed           : {failed_count}")
    print(f"Master index     : {master_index}")
    print(f"Duplicate report : {duplicate_report}")
    print(f"Rejected report  : {rejected_report}")
    print(f"Output root      : {OUTPUT_ROOT}")
    print("=" * 100)

    print("\nIMPORTANT:")
    print("- raw_source.qpy = byte-for-byte copy of hardware-saved QPY.")
    print("- qpy_redraw_diagram = raw wire order from Qiskit; circuit is not altered.")
    print("- logical_order_diagram = SAME raw circuit, rows displayed q0..q6 via wire_order.")
    print("- logical_to_physical_mapping.csv shows q0..q6 -> physical wires.")
    print("- Structural hash is checked before/after logical-order drawing.")
    print("- No clean/rebuilt circuit is created and no gate is moved or dropped.")


if __name__ == "__main__":
    main()


