"""Ánh xạ 7 expectation values Pauli-Z thành 7 tham số vật lý EIS.

Đầu vào:
    [<Z0>, <Z1>, ..., <Z6>] với mỗi giá trị thuộc [-1, 1].

Đầu ra theo đúng thứ tự của impedance_model.py:
    [Rs, L, Rct, Q1, n1, Q2, n2]

File này chỉ thực hiện mapping. Nó chưa tính Z_fit, loss hoặc chạy COBYLA.
"""

from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent
SIMULATOR_OUTPUT = (
    PROJECT_DIR / "results" / "simulator_test" / "pauli_z_expectations.csv"
)
MAPPING_OUTPUT = (
    PROJECT_DIR / "results" / "simulator_test" / "mapped_eis_parameters.csv"
)

PARAMETER_NAMES = ["Rs", "L", "Rct", "Q1", "n1", "Q2", "n2"]
PARAMETER_UNITS = [
    "ohm",
    "H",
    "ohm",
    "S*s^n1",
    "dimensionless",
    "S*s^n2",
    "dimensionless",
]

# Giữ cùng miền tìm kiếm với classical_baseline.py.
# "log" dành cho đại lượng dương trải qua nhiều bậc độ lớn.
# "linear" phù hợp cho số mũ CPE n nằm trong một khoảng ngắn.
PARAMETER_BOUNDS = {
    "Rs":  {"minimum": 0.1,  "maximum": 50, "scale": "log"},
    "L":   {"minimum": 1e-9, "maximum": 1e-5, "scale": "log"},
    "Rct": {"minimum": 0.5,  "maximum": 500, "scale": "log"},
    "Q1":  {"minimum": 1e-6, "maximum": 1, "scale": "log"},
    "n1":  {"minimum": 0.50,  "maximum": 1.00, "scale": "linear"},
    "Q2":  {"minimum": 1e-3, "maximum": 5e-3, "scale": "log"},
    "n2":  {"minimum": 0.88,  "maximum": 0.99, "scale": "linear"},
}


def validate_expectations(expectations):
    """Kiểm tra simulator đã trả đúng 7 số hữu hạn trong [-1, 1]."""
    values = np.asarray(expectations, dtype=float)

    if values.shape != (7,):
        raise ValueError(
            f"Cần đúng 7 expectation values, nhưng nhận được {values.size}."
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("Expectation values chứa NaN hoặc infinity.")
    if np.any(values < -1.0 - 1e-10) or np.any(values > 1.0 + 1e-10):
        raise ValueError("Expectation values phải nằm trong đoạn [-1, 1].")

    # Loại bỏ sai số số học rất nhỏ, ví dụ 1.0000000000000002.
    return np.clip(values, -1.0, 1.0)


def map_one_value(expectation, minimum, maximum, scale):
    """Ánh xạ một expectation từ [-1, 1] sang miền tham số vật lý."""
    normalized = (expectation + 1.0) / 2.0

    if scale == "linear":
        value = minimum + normalized * (maximum - minimum)
    elif scale == "log":
        if minimum <= 0 or maximum <= 0:
            raise ValueError("Mapping logarithm yêu cầu hai giới hạn dương.")
        log_minimum = np.log10(minimum)
        log_maximum = np.log10(maximum)
        value = 10.0 ** (
            log_minimum + normalized * (log_maximum - log_minimum)
        )
    else:
        raise ValueError(f"Kiểu mapping không hợp lệ: {scale}")

    return float(value), float(normalized)


def expectations_to_parameters(expectations, bounds=PARAMETER_BOUNDS):
    """Chuyển 7 expectation values thành dictionary 7 tham số EIS."""
    expectations = validate_expectations(expectations)
    parameters = {}
    normalized_values = {}

    for index, name in enumerate(PARAMETER_NAMES):
        setting = bounds[name]
        value, normalized = map_one_value(
            expectation=expectations[index],
            minimum=setting["minimum"],
            maximum=setting["maximum"],
            scale=setting["scale"],
        )
        parameters[name] = value
        normalized_values[name] = normalized

    return parameters, normalized_values


def parameters_as_array(parameters):
    """Tạo array theo đúng thứ tự đối số của impedance_model()."""
    return np.array([parameters[name] for name in PARAMETER_NAMES], dtype=float)


def load_expectations(csv_path=SIMULATOR_OUTPUT):
    """Đọc 7 expectation values do quantum_simulator.py đã xuất."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy {csv_path}\n"
            "Hãy chạy: python quantum_simulator.py"
        )

    table = pd.read_csv(csv_path)
    required_columns = {"qubit", "expectation_z"}
    missing = required_columns.difference(table.columns)
    if missing:
        raise ValueError(f"File expectation thiếu cột: {sorted(missing)}")

    table = table.sort_values("qubit")
    return validate_expectations(table["expectation_z"].to_numpy())


def save_mapped_parameters(
    expectations,
    parameters,
    normalized_values,
    output_path=MAPPING_OUTPUT,
    bounds=PARAMETER_BOUNDS,
):
    """Lưu bảng mapping để người dùng kiểm tra từng qubit."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for qubit, name in enumerate(PARAMETER_NAMES):
        rows.append(
            {
                "qubit": qubit,
                "parameter": name,
                "expectation_z": expectations[qubit],
                "normalized_0_to_1": normalized_values[name],
                "fitted_candidate_value": parameters[name],
                "unit": PARAMETER_UNITS[qubit],
                "minimum": bounds[name]["minimum"],
                "maximum": bounds[name]["maximum"],
                "mapping_scale": bounds[name]["scale"],
            }
        )

    table = pd.DataFrame(rows)
    table.to_csv(output_path, index=False)
    return table, output_path


def main():
    # 1. Đọc output của simulator.
    expectations = load_expectations()

    # 2. Mapping sang 7 tham số vật lý.
    parameters, normalized_values = expectations_to_parameters(expectations)

    # 3. Lưu và in kết quả kiểm tra.
    table, output_path = save_mapped_parameters(
        expectations, parameters, normalized_values
    )

    print("\nMAPPING ĐÃ CHẠY THÀNH CÔNG")
    print(table.to_string(index=False))
    print("\nThứ tự đưa vào impedance_model:")
    print(parameters_as_array(parameters))
    print("\nĐã lưu:", output_path)
    print("\nĐây mới là một bộ tham số ứng viên, chưa phải fitted parameters.")
    print("COBYLA phải thay đổi góc và giảm loss để tìm bộ tối ưu.")


if __name__ == "__main__":
    main()
