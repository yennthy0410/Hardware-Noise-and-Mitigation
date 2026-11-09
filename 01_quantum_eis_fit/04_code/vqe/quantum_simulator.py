"""Chạy statevector simulator cho HEA 7 qubit, 3 layer.

Input của simulator:
    1. HEA đã được xây trong hea_ansatz.py.
    2. Một vector gồm 21 góc Ry (theta).

Output của simulator:
    1. Statevector gồm 2**7 = 128 biên độ phức.
    2. Bảy expectation values Pauli-Z: <Z0>, ..., <Z6>.

Lưu ý: file CSV EIS chưa đi trực tiếp vào simulator. Dữ liệu EIS được dùng
ở bước sau để tính Z_fit, residual và loss khi optimizer cập nhật các góc.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from hea_ansatz import (
    N_ANGLES,
    N_QUBITS,
    QUBIT_TO_EIS_PARAMETER,
    assign_angles,
    build_hea_7qubit_3layer,
    measure_pauli_z_expectations,
    run_statevector,
)


RESULT_FOLDER = Path(__file__).resolve().parent / "results" / "simulator_test"


def create_test_angles(seed=13):
    """Tạo 21 góc thử cố định để kết quả có thể tái lập."""
    rng = np.random.default_rng(seed)
    return rng.uniform(-0.2, 0.2, size=N_ANGLES)


def save_angles(angle_values, output_folder):
    """Lưu 21 góc Ry được đưa vào simulator."""
    angle_table = pd.DataFrame(
        {
            "angle_index": np.arange(N_ANGLES),
            "layer": np.repeat(np.arange(1, 4), N_QUBITS),
            "qubit": np.tile(np.arange(N_QUBITS), 3),
            "angle_rad": angle_values,
        }
    )
    path = output_folder / "test_angles.csv"
    angle_table.to_csv(path, index=False)
    return path


def save_statevector(state, output_folder):
    """Lưu 128 biên độ phức và xác suất của trạng thái 7 qubit."""
    amplitudes = np.asarray(state.data)
    dimension = 2**N_QUBITS

    # Qiskit hiển thị chuỗi bit theo thứ tự q6...q0.
    basis_states = [format(index, f"0{N_QUBITS}b") for index in range(dimension)]
    state_table = pd.DataFrame(
        {
            "basis_state_q6_to_q0": basis_states,
            "amplitude_real": amplitudes.real,
            "amplitude_imag": amplitudes.imag,
            "probability": np.abs(amplitudes) ** 2,
        }
    )
    path = output_folder / "statevector_amplitudes.csv"
    state_table.to_csv(path, index=False)
    return path, state_table


def save_expectations(expectations, output_folder):
    """Lưu 7 expectation values và giá trị chuẩn hóa sơ bộ [0, 1]."""
    expectation_table = pd.DataFrame(
        {
            "qubit": np.arange(N_QUBITS),
            "eis_parameter": [
                QUBIT_TO_EIS_PARAMETER[index] for index in range(N_QUBITS)
            ],
            "expectation_z": expectations,
            # Đây chỉ là phép đổi miền, chưa phải tham số vật lý cuối cùng.
            "normalized_0_to_1": (expectations + 1.0) / 2.0,
        }
    )
    path = output_folder / "pauli_z_expectations.csv"
    expectation_table.to_csv(path, index=False)
    return path, expectation_table


def run_simulator(angle_values, output_folder=RESULT_FOLDER):
    """Gán góc, chạy simulator và xuất các kết quả kiểm tra."""
    angle_values = np.asarray(angle_values, dtype=float)
    if angle_values.shape != (N_ANGLES,):
        raise ValueError(
            f"Simulator cần đúng {N_ANGLES} góc, "
            f"nhưng nhận được {angle_values.size}."
        )

    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    # Bước 1: xây HEA còn chứa các biến theta.
    circuit, theta = build_hea_7qubit_3layer()

    # Bước 2: thay 21 biến theta bằng 21 số thực.
    assigned_circuit = assign_angles(circuit, theta, angle_values)

    # Bước 3: tính trạng thái cuối |psi(theta)> bằng statevector chính xác.
    state = run_statevector(assigned_circuit)

    # Bước 4: tính <Zq> = <psi|Zq|psi> cho từng qubit.
    expectations = measure_pauli_z_expectations(state)

    # Bước 5: lưu input và output để kiểm tra độc lập.
    angles_path = save_angles(angle_values, output_folder)
    state_path, state_table = save_statevector(state, output_folder)
    expectation_path, expectation_table = save_expectations(
        expectations, output_folder
    )

    circuit_path = output_folder / "assigned_circuit.png"
    assigned_circuit.draw(output="mpl", filename=str(circuit_path), fold=30)

    probability_sum = float(state_table["probability"].sum())

    print("\nSIMULATOR ĐÃ CHẠY THÀNH CÔNG")
    print("Số qubit:", N_QUBITS)
    print("Số góc đầu vào:", len(angle_values))
    print("Số biên độ statevector:", len(state.data))
    print(f"Tổng xác suất (phải xấp xỉ 1): {probability_sum:.12f}")
    print("\nBảy expectation values Pauli-Z:")
    print(expectation_table.to_string(index=False))
    print("\nCác file đã xuất:")
    print("-", angles_path)
    print("-", state_path)
    print("-", expectation_path)
    print("-", circuit_path)
    print("\nĐây chưa phải fitting EIS và chưa phải tham số vật lý cuối cùng.")

    return state, expectations


def main():
    # Bộ góc này chỉ dùng để kiểm tra simulator. Sau này COBYLA sẽ cung cấp góc.
    test_angles = create_test_angles(seed=13)
    run_simulator(test_angles)


if __name__ == "__main__":
    main()
