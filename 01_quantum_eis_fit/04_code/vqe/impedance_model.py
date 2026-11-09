import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# 1. ĐỌC FILE EIS GỐC
# ============================================================

from pathlib import Path


def read_eis_file(file_name):
    """
    Đọc file EIS gốc và trả về:
    - frequency: tần số, Hz
    - Z_exp: trở kháng phức thực nghiệm
    """

    folder = Path(__file__).parent
    file_path = folder / file_name

    df = pd.read_csv(file_path)

    frequency = df["Frequency (Hz)"].to_numpy(dtype=float)
    z_real = df["Z' (Ohms)"].to_numpy(dtype=float)

    # File lưu -Z'', vì vậy đổi dấu để thu được Z''
    z_imag = -df['-Z" (Ohms)'].to_numpy(dtype=float)

    Z_exp = z_real + 1j * z_imag

    print(f"Đã đọc: {file_name}")
    print(f"Số điểm dữ liệu: {len(df)}")

    return frequency, Z_exp


# ============================================================
# 2. PHƯƠNG TRÌNH CPE
# ============================================================

def impedance_cpe(omega, Q, n):
    """
    Trở kháng của phần tử pha không đổi CPE:

        Z_CPE = 1 / [Q * (j*omega)^n]

    Parameters
    ----------
    omega : ndarray
        Tần số góc, đơn vị rad/s.
    Q : float
        Hệ số CPE.
    n : float
        Số mũ CPE, thường 0 <= n <= 1.
    """

    return 1 / (Q * (1j * omega) ** n)


# ============================================================
# 3. Rct SONG SONG VỚI CPE1
# ============================================================

def impedance_rct_parallel_cpe1(omega, Rct, Q1, n1):
    """
    Tổng dẫn:

        Y = 1/Rct + Q1*(j*omega)^n1

    Trở kháng tương đương:

        Z = 1/Y
    """

    admittance = (1 / Rct) + Q1 * (1j * omega) ** n1

    return 1 / admittance


# ============================================================
# 4. PHƯƠNG TRÌNH TOÀN BỘ MẠCH
# ============================================================

def impedance_model(
    frequency,
    Rs,
    L,
    Rct,
    Q1,
    n1,
    Q2,
    n2
):
    """
    Mạch tương đương:

        Rs - L - (Rct || CPE1) - CPE2

    Tổng trở:

        Z = Rs
            + j*omega*L
            + [1/Rct + Q1*(j*omega)^n1]^(-1)
            + 1/[Q2*(j*omega)^n2]
    """

    omega = 2 * np.pi * np.asarray(frequency, dtype=float)

    # Điện trở nối tiếp
    Z_Rs = Rs

    # Cuộn cảm
    Z_L = 1j * omega * L

    # Rct song song CPE1
    Z_parallel = impedance_rct_parallel_cpe1(
        omega,
        Rct,
        Q1,
        n1
    )


    # CPE2 nối tiếp
    Z_CPE2 = impedance_cpe(
        omega,
        Q2,
        n2
    )

    return Z_Rs + Z_L + Z_parallel + Z_CPE2

if __name__ == "__main__":

    # Đây chỉ là tham số chạy thử, chưa phải kết quả fitting
    example_parameters = {
        "Rs": 0.7,
        "L": 2.0e-7,
        "Rct": 2.0,
        "Q1": 0.05,
        "n1": 0.85,
        "Q2": 0.10,
        "n2": 0.90
    }

    file_names = [
        "EIS13.csv",
        "EIS14.csv"
    ]

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(13, 5)
    )

    for axis, file_name in zip(axes, file_names):

        # Đọc từng file riêng
        frequency, Z_exp = read_eis_file(file_name)

        # Tính trở kháng từ phương trình mạch
        Z_model = impedance_model(
            frequency,
            **example_parameters
        )

        # Vẽ dữ liệu thực nghiệm
        axis.scatter(
            Z_exp.real,
            -Z_exp.imag,
            s=20,
            color="blue",
            label="Experimental data"
        )

        # Vẽ kết quả mô hình
        axis.plot(
            Z_model.real,
            -Z_model.imag,
            color="red",
            linewidth=2,
            label="Model – test parameters"
        )

        axis.set_xlabel(r"$Z'$ ($\Omega$)")
        axis.set_ylabel(r"$-Z''$ ($\Omega$)")
        axis.set_title(file_name)
        axis.grid(True, alpha=0.3)
        axis.legend()

    plt.tight_layout()
    plt.show()

    # Định nghĩa weighted complex loss
def weighted_complex_loss(
    Z_exp,
    Z_fit,
    epsilon=1e-12
):
    

    Z_exp = np.asarray(
        Z_exp,
        dtype=complex
    )

    Z_fit = np.asarray(
        Z_fit,
        dtype=complex
    )

    if Z_exp.shape != Z_fit.shape:
        raise ValueError(
            "Z_exp và Z_fit phải có cùng kích thước. "
            f"Z_exp={Z_exp.shape}, Z_fit={Z_fit.shape}"
        )

    residual_real = (
        Z_fit.real
        - Z_exp.real
    )

    residual_imag = (
        Z_fit.imag
        - Z_exp.imag
    )

    residual_squared = (
        residual_real**2
        + residual_imag**2
    )

    denominator = (
        np.abs(Z_exp)**2
        + epsilon
    )

    loss = np.mean(
        residual_squared
        / denominator
    )

    return float(loss)
