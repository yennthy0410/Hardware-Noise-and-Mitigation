"""Kết nối HEA -> simulator -> mapping -> Z_fit -> weighted complex loss.

File này hỗ trợ hai chế độ:
1. Dùng PARAMETER_BOUNDS mặc định trong parameter_mapping.py.
2. Nhận custom parameter bounds từ bên ngoài (ví dụ dashboard / sensitivity study).

Pipeline mỗi lần evaluate:
    21 góc -> HEA -> statevector -> 7 <Z> -> 7 tham số EIS
    -> Z_fit -> weighted complex loss.

Khi chạy trực tiếp, file đánh giá một bộ 21 góc thử cho EIS13.csv
và lưu kết quả ban đầu. Đây chưa phải bước tối ưu COBYLA.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from hea_ansatz import (
    N_ANGLES,
    assign_angles,
    build_hea_7qubit_3layer,
    measure_pauli_z_expectations,
    run_statevector,
)
from impedance_model import impedance_model, read_eis_file
from parameter_mapping import (
    PARAMETER_BOUNDS,
    PARAMETER_NAMES,
    PARAMETER_UNITS,
    expectations_to_parameters,
    parameters_as_array,
)


PROJECT_DIR = Path(__file__).resolve().parent


DEFAULT_OBJECTIVE_SETTINGS = {
    "complex_weight": 1.0,
    "phase_weight": 5.0,
    "low_frequency_weight": 5.0,
    "low_frequency_cutoff_hz": 10.0,
    "use_frequency_weights": True,
}


def validate_objective_settings(objective_settings=None):
    """Validate and merge objective/loss controls without changing defaults.

    The returned dictionary always contains:
      complex_weight, phase_weight, low_frequency_weight,
      low_frequency_cutoff_hz, use_frequency_weights.
    """
    merged = deepcopy(DEFAULT_OBJECTIVE_SETTINGS)
    if objective_settings is not None:
        if not isinstance(objective_settings, dict):
            raise TypeError("objective_settings phải là dict hoặc None.")
        unknown = [key for key in objective_settings if key not in merged]
        if unknown:
            raise ValueError(f"objective_settings có trường không hợp lệ: {unknown}")
        merged.update(objective_settings)

    for key in ("complex_weight", "phase_weight", "low_frequency_weight", "low_frequency_cutoff_hz"):
        merged[key] = float(merged[key])
        if not np.isfinite(merged[key]):
            raise ValueError(f"{key} phải là số hữu hạn.")

    if merged["complex_weight"] < 0:
        raise ValueError("complex_weight phải >= 0.")
    if merged["phase_weight"] < 0:
        raise ValueError("phase_weight phải >= 0.")
    if merged["low_frequency_weight"] < 0:
        raise ValueError("low_frequency_weight phải >= 0.")
    if merged["low_frequency_cutoff_hz"] <= 0:
        raise ValueError("low_frequency_cutoff_hz phải > 0.")
    if merged["complex_weight"] == 0 and merged["phase_weight"] == 0:
        raise ValueError("complex_weight và phase_weight không thể đồng thời bằng 0.")

    merged["use_frequency_weights"] = bool(merged["use_frequency_weights"])
    return deepcopy(merged)


def resolve_eis_path(file_name):
    """Tìm file EIS trong cấu trúc project mà không cần di chuyển CSV.

    Hỗ trợ:
    - đường dẫn tuyệt đối;
    - file nằm cạnh quantum_objective.py;
    - <repo_root>/data/<dataset>.csv;
    - đường dẫn tương đối từ thư mục đang chạy terminal.

    Hàm chỉ tìm/đọc file, không thay đổi bất kỳ dữ liệu nào.
    """
    requested = Path(file_name).expanduser()

    if requested.is_absolute():
        if requested.is_file():
            return requested.resolve()
        raise FileNotFoundError(f"Không tìm thấy dataset: {requested}")

    candidates = [
        PROJECT_DIR / requested,
        PROJECT_DIR.parent / requested,
        PROJECT_DIR.parent / "data" / requested.name,
        Path.cwd() / requested,
        Path.cwd() / "data" / requested.name,
    ]

    # Nếu sau này src/ được đặt sâu hơn, tiếp tục tìm data/ ở các thư mục cha.
    for parent in PROJECT_DIR.parents:
        candidates.append(parent / "data" / requested.name)

    seen = set()
    checked = []
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()

        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        checked.append(candidate)

        if candidate.is_file():
            return candidate

    checked_text = "\n".join(f"  - {path}" for path in checked)
    raise FileNotFoundError(
        f"Không tìm thấy dataset {file_name!r}. Đã kiểm tra:\n{checked_text}"
    )


def frequency_weights(frequency):
    """Cho mỗi khoảng log-frequency tổng ảnh hưởng gần tương đương nhau."""
    frequency = np.asarray(frequency, dtype=float)

    if np.any(frequency <= 0):
        raise ValueError("Tất cả tần số phải lớn hơn 0 Hz.")

    order = np.argsort(np.log10(frequency))
    log_frequency_sorted = np.log10(frequency[order])
    spacing_sorted = np.gradient(log_frequency_sorted)

    spacing = np.empty_like(spacing_sorted)
    spacing[order] = spacing_sorted
    weights = spacing / np.mean(spacing)
    weights = np.clip(weights, 1e-8, None)
    weights = weights / np.mean(weights)

    return weights


def clean_eis_data(frequency, z_exp):
    """Loại các điểm NaN, infinity và tần số không dương."""
    frequency = np.asarray(frequency, dtype=float)
    z_exp = np.asarray(z_exp, dtype=complex)

    if frequency.shape != z_exp.shape:
        raise ValueError("frequency và Z_exp phải có cùng kích thước.")

    valid = (
        np.isfinite(frequency)
        & np.isfinite(z_exp.real)
        & np.isfinite(z_exp.imag)
        & (frequency > 0)
    )

    frequency = frequency[valid]
    z_exp = z_exp[valid]

    if len(frequency) < 8:
        raise ValueError("Dữ liệu hợp lệ quá ít để fitting EIS.")

    return frequency, z_exp


def validate_parameter_bounds(parameter_bounds=None):
    """Kiểm tra và trả về một bản copy an toàn của parameter bounds.

    Parameters
    ----------
    parameter_bounds : dict | None
        Nếu None, dùng PARAMETER_BOUNDS mặc định từ parameter_mapping.py.
        Nếu truyền dict, cần đủ 7 tham số: Rs, L, Rct, Q1, n1, Q2, n2.

    Returns
    -------
    dict
        Bản deepcopy đã kiểm tra. Hàm này KHÔNG sửa PARAMETER_BOUNDS gốc.
    """
    source = PARAMETER_BOUNDS if parameter_bounds is None else parameter_bounds

    if not isinstance(source, dict):
        raise TypeError("parameter_bounds phải là dict hoặc None.")

    missing = [name for name in PARAMETER_NAMES if name not in source]
    extra = [name for name in source if name not in PARAMETER_NAMES]

    if missing:
        raise ValueError(f"parameter_bounds thiếu tham số: {missing}")
    if extra:
        raise ValueError(f"parameter_bounds có tham số không hợp lệ: {extra}")

    validated = {}

    for name in PARAMETER_NAMES:
        setting = source[name]

        if not isinstance(setting, dict):
            raise TypeError(f"Bounds của {name} phải là dict.")

        for key in ("minimum", "maximum", "scale"):
            if key not in setting:
                raise ValueError(f"Bounds của {name} thiếu trường '{key}'.")

        minimum = float(setting["minimum"])
        maximum = float(setting["maximum"])
        scale = str(setting["scale"]).lower().strip()

        if not np.isfinite(minimum) or not np.isfinite(maximum):
            raise ValueError(f"Bounds của {name} phải là số hữu hạn.")

        if minimum >= maximum:
            raise ValueError(
                f"Bounds không hợp lệ cho {name}: minimum={minimum} "
                f"phải nhỏ hơn maximum={maximum}."
            )

        if scale not in {"linear", "log"}:
            raise ValueError(
                f"Scale của {name} phải là 'linear' hoặc 'log', nhận {scale!r}."
            )

        if scale == "log" and (minimum <= 0 or maximum <= 0):
            raise ValueError(
                f"Log mapping của {name} yêu cầu minimum và maximum > 0."
            )

        validated[name] = {
            "minimum": minimum,
            "maximum": maximum,
            "scale": scale,
        }

    return deepcopy(validated)


class QuantumEISObjective:
    """Objective có thể được gọi trực tiếp bởi scipy.optimize.minimize.

    parameter_bounds=None giữ nguyên hành vi cũ của project.
    Truyền custom bounds cho phép dashboard/sensitivity study chạy COBYLA
    với miền mapping khác mà không sửa parameter_mapping.py.
    """

    def __init__(self, frequency, z_exp, parameter_bounds=None, objective_settings=None):
        self.frequency, self.z_exp = clean_eis_data(frequency, z_exp)

        # Deepcopy + validation để mỗi objective có bộ bounds độc lập.
        # Không có thao tác nào ghi ngược vào PARAMETER_BOUNDS gốc.
        self.parameter_bounds = validate_parameter_bounds(parameter_bounds)
        self.objective_settings = validate_objective_settings(objective_settings)

        # Những đại lượng này chỉ được tạo một lần, không tạo lại mỗi vòng.
        self.circuit, self.theta_parameters = build_hea_7qubit_3layer()
        self.frequency_weights = frequency_weights(self.frequency)
        self.weights = (
            self.frequency_weights
            if self.objective_settings["use_frequency_weights"]
            else np.ones_like(self.frequency, dtype=float)
        )
        self.data_scale = max(float(np.median(np.abs(self.z_exp))), 1e-12)
        self.point_scale = np.maximum(
            np.abs(self.z_exp),
            1e-3 * np.median(np.abs(self.z_exp)),
        )

        self.number_of_calls = 0
        self.best_loss = np.inf
        self.best_angles = None

    @classmethod
    def from_csv(cls, file_name, parameter_bounds=None, objective_settings=None):
        """Đọc một file EIS rồi tạo objective.

        Ví dụ cũ (vẫn hoạt động):
            QuantumEISObjective.from_csv("EIS14.csv")

        Ví dụ custom bounds:
            QuantumEISObjective.from_csv(
                "EIS14.csv",
                parameter_bounds=my_bounds,
            )
        """
        dataset_path = resolve_eis_path(file_name)
        frequency, z_exp = read_eis_file(str(dataset_path))
        return cls(
            frequency=frequency,
            z_exp=z_exp,
            parameter_bounds=parameter_bounds,
            objective_settings=objective_settings,
        )

    def evaluate(self, angle_values, return_details=False):
        """Tính loss từ một vector gồm 21 góc Ry."""
        angle_values = np.asarray(angle_values, dtype=float)

        if angle_values.shape != (N_ANGLES,):
            raise ValueError(
                f"Objective cần đúng {N_ANGLES} góc, "
                f"nhưng nhận được {angle_values.size}."
            )
        if not np.all(np.isfinite(angle_values)):
            raise ValueError("Các góc chứa NaN hoặc infinity.")

        # 1. Gán 21 góc vào HEA.
        assigned_circuit = assign_angles(
            self.circuit,
            self.theta_parameters,
            angle_values,
        )

        # 2. Chạy statevector simulator.
        state = run_statevector(assigned_circuit)

        # 3. Đo bảy expectation values Pauli-Z.
        expectations = measure_pauli_z_expectations(state)

        # 4. Mapping thành Rs, L, Rct, Q1, n1, Q2, n2.
        # QUAN TRỌNG: dùng bounds của chính objective này.
        parameters, normalized_values = expectations_to_parameters(
            expectations,
            bounds=self.parameter_bounds,
        )
        parameter_array = parameters_as_array(parameters)

        # 5. Tính impedance từ phương trình mạch tương đương.
        z_fit = impedance_model(self.frequency, *parameter_array)
        z_fit = np.asarray(z_fit, dtype=complex)

        if z_fit.shape != self.z_exp.shape:
            raise ValueError("Z_fit và Z_exp không có cùng kích thước.")

        if not np.all(np.isfinite(z_fit.real)) or not np.all(
            np.isfinite(z_fit.imag)
        ):
            # Trả phạt lớn để optimizer tránh vùng tham số không hợp lệ.
            loss = 1e100
            residual = np.full_like(self.z_exp, np.nan + 1j * np.nan)
            raw_weighted_loss = np.inf
            loss_components = {
                "complex_component": np.inf,
                "phase_component": np.inf,
                "total": np.inf,
            }
        else:
            # 6. Residual được giữ cùng quy ước classical: fit - data.
            residual = z_fit - self.z_exp
            relative_complex_error = np.abs(residual / self.point_scale) ** 2

            phase_fit = np.angle(z_fit)
            phase_exp = np.angle(self.z_exp)

            phase_residual = np.angle(
                np.exp(1j * (phase_fit - phase_exp))
            )
            phase_error = phase_residual ** 2

            cfg = self.objective_settings
            low_frequency_factor = np.where(
                self.frequency <= cfg["low_frequency_cutoff_hz"],
                cfg["low_frequency_weight"],
                1.0,
            )

            complex_term = cfg["complex_weight"] * relative_complex_error
            phase_term = cfg["phase_weight"] * phase_error
            combined_error = complex_term + phase_term

            weighted_complex_component = float(
                np.mean(self.weights * low_frequency_factor * complex_term)
            )
            weighted_phase_component = float(
                np.mean(self.weights * low_frequency_factor * phase_term)
            )
            loss = float(weighted_complex_component + weighted_phase_component)

            loss_components = {
                "complex_component": weighted_complex_component,
                "phase_component": weighted_phase_component,
                "total": loss,
            }

            # Loss thô có đơn vị ohm^2 để lưu báo cáo và so với dữ liệu.
            raw_weighted_loss = float(
                np.sum(self.weights * np.abs(residual) ** 2)
            )

        self.number_of_calls += 1
        if np.isfinite(loss) and loss < self.best_loss:
            self.best_loss = loss
            self.best_angles = angle_values.copy()

        if not return_details:
            return loss

        return {
            "loss": loss,
            "raw_weighted_loss_ohm2": raw_weighted_loss,
            "angles": angle_values,
            "expectations": expectations,
            "normalized_values": normalized_values,
            "parameters": parameters,
            "parameter_bounds": deepcopy(self.parameter_bounds),
            "objective_settings": deepcopy(self.objective_settings),
            "loss_components": deepcopy(loss_components),
            "z_fit": z_fit,
            "residual": residual,
        }

    def __call__(self, angle_values):
        """Cho phép dùng: minimize(objective, x0, method='COBYLA')."""
        return self.evaluate(angle_values, return_details=False)


def save_initial_evaluation(problem, details, dataset_name):
    """Lưu một lần kết quả thử ban đầu; không gọi hàm này trong optimizer."""
    dataset_stem = Path(dataset_name).stem
    output_dir = PROJECT_DIR / "results" / "quantum_objective" / dataset_stem
    output_dir.mkdir(parents=True, exist_ok=True)

    parameter_table = pd.DataFrame(
        {
            "parameter": PARAMETER_NAMES,
            "candidate_value": [
                details["parameters"][name] for name in PARAMETER_NAMES
            ],
            "unit": PARAMETER_UNITS,
            "expectation_z": details["expectations"],
            "bound_minimum": [
                problem.parameter_bounds[name]["minimum"]
                for name in PARAMETER_NAMES
            ],
            "bound_maximum": [
                problem.parameter_bounds[name]["maximum"]
                for name in PARAMETER_NAMES
            ],
            "bound_scale": [
                problem.parameter_bounds[name]["scale"]
                for name in PARAMETER_NAMES
            ],
        }
    )
    parameter_table.to_csv(output_dir / "initial_parameters.csv", index=False)

    spectrum_table = pd.DataFrame(
        {
            "frequency_hz": problem.frequency,
            "z_real_exp_ohm": problem.z_exp.real,
            "z_imag_exp_ohm": problem.z_exp.imag,
            "z_real_fit_ohm": details["z_fit"].real,
            "z_imag_fit_ohm": details["z_fit"].imag,
            "residual_real_ohm": details["residual"].real,
            "residual_imag_ohm": details["residual"].imag,
            "weight": problem.weights,
        }
    )
    spectrum_table.to_csv(output_dir / "initial_spectrum.csv", index=False)

    angle_table = pd.DataFrame(
        {
            "angle_index": np.arange(N_ANGLES),
            "angle_rad": details["angles"],
        }
    )
    angle_table.to_csv(output_dir / "initial_angles.csv", index=False)

    return output_dir


def main():
    dataset_name = "EIS13.csv"

    # Không truyền parameter_bounds -> dùng bounds gốc trong parameter_mapping.py.
    # Vì vậy cách chạy cũ của project vẫn được giữ nguyên.
    problem = QuantumEISObjective.from_csv(dataset_name)

    # Bộ góc cố định chỉ để kiểm tra đường tính objective.
    rng = np.random.default_rng(seed=13)
    initial_angles = rng.uniform(-0.2, 0.2, size=N_ANGLES)

    details = problem.evaluate(initial_angles, return_details=True)

    print("\nQUANTUM OBJECTIVE ĐÃ CHẠY THÀNH CÔNG")
    print("Dataset:", dataset_name)
    print("Số điểm EIS:", len(problem.frequency))
    print("Số góc:", len(initial_angles))
    print("Số expectation values:", len(details["expectations"]))
    print("Số tham số EIS:", len(details["parameters"]))
    print(f"Normalized objective loss: {details['loss']:.12e}")
    print(
        "Raw weighted complex loss (ohm^2): "
        f"{details['raw_weighted_loss_ohm2']:.12e}"
    )

    print("\nBounds đang được objective sử dụng:")
    for name in PARAMETER_NAMES:
        setting = problem.parameter_bounds[name]
        print(
            f"{name:>3s}: "
            f"{setting['minimum']:.6g} -> {setting['maximum']:.6g} "
            f"({setting['scale']})"
        )

    print("\nBảy tham số ứng viên ban đầu:")
    for name in PARAMETER_NAMES:
        print(f"{name:>3s} = {details['parameters'][name]:.12e}")

    output_dir = save_initial_evaluation(problem, details, dataset_name)
    print("\nĐã lưu kết quả kiểm tra tại:", output_dir)
    print("Đây chưa phải kết quả fitting; bước sau mới chạy COBYLA.")


if __name__ == "__main__":
    main()
