"""HEA 7 qubit, 3 lớp Ry cho hybrid quantum-classical EIS fitting.

Cấu trúc:
    Ry layer 1 -> CX(0,1), CX(2,3), CX(4,5)
    -> Ry layer 2 -> CX(1,2), CX(3,4), CX(5,6)
    -> Ry layer 3 -> tùy chọn đo q0, ..., q6.

Quy ước: q0=Rs, q1=L, q2=Rct, q3=Q1, q4=n1, q5=Q2, q6=n2.
"""

from pathlib import Path

import numpy as np
from qiskit import QuantumCircuit, qasm3
from qiskit.circuit import ParameterVector
from qiskit.quantum_info import Pauli, Statevector


N_QUBITS = 7
N_LAYERS = 3
N_ANGLES = N_QUBITS * N_LAYERS

QUBIT_TO_EIS_PARAMETER = {
    0: "Rs", 1: "L", 2: "Rct", 3: "Q1",
    4: "n1", 5: "Q2", 6: "n2",
}


def build_hea_7qubit_3layer(measure=False):
    """Tạo HEA; measure=False cho Statevector, True cho shots/QPU."""
    if not isinstance(measure, bool):
        raise TypeError("measure phải là True hoặc False.")

    if measure:
        circuit = QuantumCircuit(N_QUBITS, N_QUBITS, name="MXene_EIS_HEA")
    else:
        circuit = QuantumCircuit(N_QUBITS, name="MXene_EIS_HEA")

    theta = ParameterVector("theta", N_ANGLES)

    # Ry layer 1: theta[0] ... theta[6].
    for qubit in range(N_QUBITS):
        circuit.ry(theta[qubit], qubit)

    circuit.cx(0, 1)
    circuit.cx(2, 3)
    circuit.cx(4, 5)
    circuit.barrier(label="Layer 1")

    # Ry layer 2: theta[7] ... theta[13].
    for qubit in range(N_QUBITS):
        circuit.ry(theta[N_QUBITS + qubit], qubit)

    circuit.cx(1, 2)
    circuit.cx(3, 4)
    circuit.cx(5, 6)
    circuit.barrier(label="Layer 2")

    # Ry layer 3: theta[14] ... theta[20].
    for qubit in range(N_QUBITS):
        circuit.ry(theta[2 * N_QUBITS + qubit], qubit)

    circuit.barrier(label="Layer 3")

    if measure:
        circuit.measure(range(N_QUBITS), range(N_QUBITS))

    return circuit, theta


def assign_angles(circuit, theta, angle_values):
    """Gán đúng 21 số thực cho theta[0] ... theta[20]."""
    angle_values = np.asarray(angle_values, dtype=float)
    if angle_values.shape != (N_ANGLES,):
        raise ValueError(
            f"Cần đúng {N_ANGLES} góc, nhưng nhận {angle_values.size}."
        )
    if not np.all(np.isfinite(angle_values)):
        raise ValueError("Các góc chứa NaN hoặc infinity.")

    parameter_map = {
        theta[index]: angle_values[index]
        for index in range(N_ANGLES)
    }
    return circuit.assign_parameters(parameter_map, inplace=False)


def run_statevector(circuit_with_values):
    """Tính statevector; circuit phải được tạo với measure=False."""
    if circuit_with_values.num_parameters != 0:
        raise ValueError("Circuit vẫn còn theta chưa được gán.")
    if circuit_with_values.count_ops().get("measure", 0) > 0:
        raise ValueError(
            "Statevector expectation cần measure=False; "
            "measure=True chỉ dùng cho shots/QPU."
        )
    return Statevector.from_instruction(circuit_with_values)


def measure_pauli_z_expectations(state):
    """Tính <Z0>, ..., <Z6>; mỗi kết quả nằm trong [-1, 1]."""
    expectations = np.empty(N_QUBITS, dtype=float)
    for qubit in range(N_QUBITS):
        value = state.expectation_value(Pauli("Z"), qargs=[qubit])
        expectations[qubit] = float(np.real_if_close(value).real)
    return expectations


def hea_expectations(circuit, theta, angle_values):
    """Gán góc -> statevector -> bảy expectation values Pauli-Z."""
    assigned_circuit = assign_angles(circuit, theta, angle_values)
    state = run_statevector(assigned_circuit)
    return measure_pauli_z_expectations(state)


def export_symbolic_circuit(circuit, output_folder="results/hea_circuit"):
    """Xuất PNG và OpenQASM 3; giữ tương thích với code cũ."""
    output_dir = Path(__file__).resolve().parent / output_folder
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "measured" if circuit.count_ops().get("measure", 0) else "statevector"
    png_path = output_dir / f"hea_7qubit_3layer_{suffix}.png"
    qasm_path = output_dir / f"hea_7qubit_3layer_{suffix}.qasm"
    circuit.draw(output="mpl", filename=str(png_path), fold=35)
    qasm_path.write_text(qasm3.dumps(circuit), encoding="utf-8")
    return png_path, qasm_path


def export_both_circuits(output_folder="results/hea_circuit"):
    """Xuất riêng bản Statevector và bản measurement."""
    no_measure_circuit, _ = build_hea_7qubit_3layer(measure=False)
    measured_circuit, _ = build_hea_7qubit_3layer(measure=True)
    return {
        "statevector": export_symbolic_circuit(no_measure_circuit, output_folder),
        "measured": export_symbolic_circuit(measured_circuit, output_folder),
    }


def print_qubit_mapping():
    print("\nQuy ước qubit -> tham số EIS:")
    for qubit, parameter in QUBIT_TO_EIS_PARAMETER.items():
        print(f"q{qubit} -> {parameter}")


def main():
    circuit, theta = build_hea_7qubit_3layer(measure=False)

    print("Đã xây HEA thành công.")
    print("Số qubit:", circuit.num_qubits)
    print("Số lớp Ry:", N_LAYERS)
    print("Số góc:", len(theta))
    print("Số cổng Ry:", circuit.count_ops().get("ry", 0))
    print("Số cổng CX:", circuit.count_ops().get("cx", 0))
    print("Số measurement trong bản Statevector:",
          circuit.count_ops().get("measure", 0))
    print_qubit_mapping()

    rng = np.random.default_rng(seed=13)
    test_angles = rng.uniform(-0.2, 0.2, size=N_ANGLES)
    expectations = hea_expectations(circuit, theta, test_angles)

    print("\nExpectation values thử nghiệm:")
    for qubit, value in enumerate(expectations):
        parameter = QUBIT_TO_EIS_PARAMETER[qubit]
        print(f"<Z{qubit}> -> {parameter:>3s}: {value:+.8f}")

    exported = export_both_circuits()
    print("\nĐã xuất bản measure=False:")
    print("-", exported["statevector"][0])
    print("-", exported["statevector"][1])
    print("Đã xuất bản measure=True:")
    print("-", exported["measured"][0])
    print("-", exported["measured"][1])


if __name__ == "__main__":
    main()
