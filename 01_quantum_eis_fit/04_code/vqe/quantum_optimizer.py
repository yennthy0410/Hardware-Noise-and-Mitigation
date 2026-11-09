"""Tối ưu hybrid quantum-classical EIS bằng COBYLA.

Pipeline mỗi lần COBYLA thử một vector góc:
    21 góc -> HEA -> statevector -> 7 <Z> -> 7 tham số EIS
    -> Z_fit -> weighted complex loss.

Ví dụ chạy nhanh để kiểm tra:
    python quantum_optimizer.py --quick

Chạy fitting đầy đủ cho EIS13 và EIS14:
    python quantum_optimizer.py --maxiter 2000 --restarts 5
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import Bounds, minimize

from hea_ansatz import N_ANGLES
from parameter_mapping import PARAMETER_NAMES, PARAMETER_UNITS
from quantum_objective import QuantumEISObjective


PROJECT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = PROJECT_DIR.parents[1] / "01_vqe" / "outputs"
DEFAULT_DATASETS = [str(PROJECT_DIR.parents[1] / "01_vqe" / "data" / "EIS14.csv")]


def run_cobyla(problem, maxiter=2000, restarts=2, seed=13):
    """Chạy COBYLA nhiều lần và giữ nghiệm có loss thấp nhất."""
    if maxiter < 1 or restarts < 1:
        raise ValueError("maxiter và restarts phải lớn hơn hoặc bằng 1.")

    rng = np.random.default_rng(seed)
    history_rows = []
    restart_summaries = []
    global_evaluation = 0

    # Góc quay là tuần hoàn; giữ trong [-pi, pi] để số không tăng quá lớn.
    angle_bounds = Bounds(
        -np.pi * np.ones(N_ANGLES),
        np.pi * np.ones(N_ANGLES),
    )

    for restart in range(1, restarts + 1):
        initial_angles = rng.uniform(-np.pi, np.pi, size=N_ANGLES)
        restart_best = np.inf
        restart_calls = 0

        print(f"\n--- Restart {restart}/{restarts} ---")

        def recorded_objective(angle_values):
            nonlocal global_evaluation, restart_calls, restart_best

            loss = problem(angle_values)
            global_evaluation += 1
            restart_calls += 1
            restart_best = min(restart_best, loss)

            history_rows.append(
                {
                    "global_evaluation": global_evaluation,
                    "restart": restart,
                    "restart_evaluation": restart_calls,
                    "loss": loss,
                    "best_loss_so_far": problem.best_loss,
                }
            )

            # Không in mọi vòng vì sẽ làm Terminal rất dài.
            if restart_calls == 1 or restart_calls % 50 == 0:
                print(
                    f"evaluation={restart_calls:5d}, "
                    f"loss={loss:.6e}, "
                    f"global_best={problem.best_loss:.6e}"
                )

            return loss

        result = minimize(
            recorded_objective,
            x0=initial_angles,
            method="COBYLA",
            bounds=angle_bounds,
            options={
                "maxiter": maxiter,
                "rhobeg": 0.5,
                "tol": 1e-8,
                "catol": 1e-8,
                "disp": False,
            },
        )

        restart_summaries.append(
            {
                "restart": restart,
                "success": bool(result.success),
                "message": str(result.message),
                "evaluations_recorded": restart_calls,
                "best_loss_in_restart": float(restart_best),
                "final_reported_loss": float(result.fun),
            }
        )

        print("Kết thúc restart:", result.message)
        print(f"Loss tốt nhất restart: {restart_best:.6e}")
        print(f"Loss tốt nhất toàn bộ: {problem.best_loss:.6e}")

    if problem.best_angles is None or not np.isfinite(problem.best_loss):
        raise RuntimeError("COBYLA không tìm được nghiệm hữu hạn.")

    history = pd.DataFrame(history_rows)
    return problem.best_angles.copy(), history, restart_summaries


def calculate_fit_metrics(z_exp, z_fit, weights):
    """Tính các chỉ số sai số cuối cùng."""
    residual = z_fit - z_exp
    sse = float(np.sum(np.abs(residual) ** 2))
    weighted_loss = float(np.sum(weights * np.abs(residual) ** 2))
    rmse = float(np.sqrt(sse / (2 * len(z_exp))))

    data_energy = float(np.sum(np.abs(z_exp) ** 2))
    relative_l2 = float(
        np.sqrt(sse / data_energy) if data_energy > 0 else np.nan
    )

    return {
        "SSE_complex_ohm2": sse,
        "weighted_complex_loss_ohm2": weighted_loss,
        "RMSE_real_imag_ohm": rmse,
        "relative_L2_error": relative_l2,
    }


def save_tables(output_dir, problem, details, history, restart_summaries):
    """Lưu góc, expectation, tham số, spectrum, history và báo cáo."""
    output_dir.mkdir(parents=True, exist_ok=True)

    parameter_table = pd.DataFrame(
        {
            "parameter": PARAMETER_NAMES,
            "value": [details["parameters"][name] for name in PARAMETER_NAMES],
            "unit": PARAMETER_UNITS,
            "expectation_z": details["expectations"],
        }
    )
    parameter_table.to_csv(output_dir / "quantum_fit_parameters.csv", index=False)

    angle_table = pd.DataFrame(
        {
            "angle_index": np.arange(N_ANGLES),
            "optimal_angle_rad": details["angles"],
        }
    )
    angle_table.to_csv(output_dir / "optimal_angles.csv", index=False)

    z_fit = details["z_fit"]
    residual = details["residual"]
    spectrum_table = pd.DataFrame(
        {
            "frequency_hz": problem.frequency,
            "z_real_data_ohm": problem.z_exp.real,
            "minus_z_imag_data_ohm": -problem.z_exp.imag,
            "z_magnitude_data_ohm": np.abs(problem.z_exp),
            "phase_data_degree": np.angle(problem.z_exp, deg=True),
            "z_real_fit_ohm": z_fit.real,
            "minus_z_imag_fit_ohm": -z_fit.imag,
            "z_magnitude_fit_ohm": np.abs(z_fit),
            "phase_fit_degree": np.angle(z_fit, deg=True),
            "residual_real_fit_minus_data_ohm": residual.real,
            "residual_imag_fit_minus_data_ohm": residual.imag,
            "weight": problem.weights,
        }
    )
    spectrum_table.to_csv(output_dir / "quantum_fitted_spectrum.csv", index=False)
    history.to_csv(output_dir / "optimization_history.csv", index=False)
    pd.DataFrame(restart_summaries).to_csv(
        output_dir / "restart_summary.csv", index=False
    )

    metrics = calculate_fit_metrics(problem.z_exp, z_fit, problem.weights)
    report = {
        "model": (
            "Rs + j*w*L + Rct/(1+Rct*Q1*(j*w)^n1) "
            "+ 1/(Q2*(j*w)^n2)"
        ),
        "optimizer": "COBYLA",
        "number_of_qubits": 7,
        "number_of_layers": 3,
        "number_of_angles": N_ANGLES,
        "number_of_eis_points": int(len(problem.frequency)),
        "number_of_objective_calls": int(problem.number_of_calls),
        "normalized_objective_loss": float(details["loss"]),
        **metrics,
        "parameters": {
            name: float(details["parameters"][name])
            for name in PARAMETER_NAMES
        },
        "expectation_values": {
            f"Z{index}": float(value)
            for index, value in enumerate(details["expectations"])
        },
        "residual_definition": "Z_fit - Z_exp",
        "restarts": restart_summaries,
    }
    (output_dir / "quantum_fit_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    return report


def save_plots(output_dir, dataset_name, problem, details, history):
    """Xuất Nyquist, Bode, residual và loss convergence."""
    frequency = problem.frequency
    z_exp = problem.z_exp
    z_fit = details["z_fit"]
    residual = details["residual"]
    data_color = "#e67e22"
    fit_color = "#1565c0"

    # Nyquist plot: -Z'' theo Z'.
    fig, ax = plt.subplots(figsize=(6.6, 5.4))
    ax.scatter(z_exp.real, -z_exp.imag, s=24, color=data_color, label="Data")
    ax.plot(z_fit.real, -z_fit.imag, lw=2.1, color=fit_color, label="Quantum fit")
    ax.set_xlabel("Z' (Ohm)")
    ax.set_ylabel("-Z'' (Ohm)")
    ax.set_title(f"{dataset_name} - Quantum COBYLA Nyquist")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "nyquist.png", dpi=300)
    plt.close(fig)

    # Sắp xếp tần số để đường Bode không chạy qua lại.
    order = np.argsort(frequency)
    f_sorted = frequency[order]
    z_exp_sorted = z_exp[order]
    z_fit_sorted = z_fit[order]

    fig, axes = plt.subplots(2, 1, figsize=(7.2, 7.4), sharex=True)
    axes[0].scatter(
        frequency, np.abs(z_exp), s=20, color=data_color, label="Data"
    )
    axes[0].plot(
        f_sorted, np.abs(z_fit_sorted), lw=2, color=fit_color, label="Quantum fit"
    )
    axes[0].set_xscale("log")
    axes[0].set_yscale("log")
    axes[0].set_ylabel("|Z| (Ohm)")
    axes[0].grid(alpha=0.25)
    axes[0].legend()

    axes[1].scatter(
        frequency,
        np.angle(z_exp, deg=True),
        s=20,
        color=data_color,
        label="Data",
    )
    axes[1].plot(
        f_sorted,
        np.angle(z_fit_sorted, deg=True),
        lw=2,
        color=fit_color,
        label="Quantum fit",
    )
    axes[1].set_xscale("log")
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel("Phase (degree)")
    axes[1].grid(alpha=0.25)
    axes[1].legend()
    fig.suptitle(f"{dataset_name} - Quantum COBYLA Bode")
    fig.tight_layout()
    fig.savefig(output_dir / "bode.png", dpi=300)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(7.2, 7.2), sharex=True)
    axes[0].axhline(0, color="black", lw=1)
    axes[0].scatter(frequency, residual.real, s=20, color="#2e7d32")
    axes[0].set_xscale("log")
    axes[0].set_ylabel("Real residual (Ohm)")
    axes[0].grid(alpha=0.25)

    axes[1].axhline(0, color="black", lw=1)
    axes[1].scatter(frequency, residual.imag, s=20, color="#8e24aa")
    axes[1].set_xscale("log")
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel("Imaginary residual (Ohm)")
    axes[1].grid(alpha=0.25)
    fig.suptitle(f"{dataset_name} - Residual = fit - data")
    fig.tight_layout()
    fig.savefig(output_dir / "residuals.png", dpi=300)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.0, 4.8))
    positive_best = np.clip(
        history["best_loss_so_far"].to_numpy(dtype=float),
        np.finfo(float).tiny,
        None,
    )
    ax.plot(history["global_evaluation"], positive_best, color="#c62828")
    ax.set_yscale("log")
    ax.set_xlabel("Objective evaluation")
    ax.set_ylabel("Best normalized loss")
    ax.set_title(f"{dataset_name} - COBYLA convergence")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "loss_convergence.png", dpi=300)
    plt.close(fig)


def fit_one_dataset(dataset_name, maxiter, restarts, seed):
    """Chạy toàn bộ fitting và lưu kết quả của một dataset."""
    print("\n" + "=" * 70)
    print("BẮT ĐẦU FITTING:", dataset_name)
    print("=" * 70)

    problem = QuantumEISObjective.from_csv(dataset_name)
    best_angles, history, restart_summaries = run_cobyla(
        problem=problem,
        maxiter=maxiter,
        restarts=restarts,
        seed=seed,
    )

    final_details = problem.evaluate(best_angles, return_details=True)
    output_dir = RESULTS_DIR

    report = save_tables(
        output_dir, problem, final_details, history, restart_summaries
    )
    save_plots(output_dir, Path(dataset_name).stem, problem, final_details, history)

    print("\nFITTING HOÀN THÀNH:", dataset_name)
    print(f"Normalized loss: {report['normalized_objective_loss']:.6e}")
    print(f"RMSE: {report['RMSE_real_imag_ohm']:.6e} ohm")
    print("Bảy fitted parameters:")
    for name, value in report["parameters"].items():
        print(f"  {name:>3s} = {value:.12e}")
    print("Đã lưu kết quả tại:", output_dir)

    return report


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Hybrid quantum-classical EIS fitting bằng COBYLA."
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=DEFAULT_DATASETS,
        help="Các CSV cần fit, mặc định: EIS13.csv EIS14.csv",
    )
    parser.add_argument("--maxiter", type=int, default=2000)
    parser.add_argument("--restarts", type=int, default=2)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Kiểm tra nhanh: 100 evaluations và 1 restart.",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    if args.quick:
        maxiter = 100
        restarts = 1
        print("Đang chạy QUICK TEST, kết quả chưa dùng để báo cáo khoa học.")
    else:
        maxiter = args.maxiter
        restarts = args.restarts

    reports = {}
    for dataset_index, dataset_name in enumerate(args.datasets):
        reports[dataset_name] = fit_one_dataset(
            dataset_name=dataset_name,
            maxiter=maxiter,
            restarts=restarts,
            seed=args.seed + dataset_index * 1000,
        )

    print("\nĐÃ HOÀN THÀNH TẤT CẢ DATASET.")


if __name__ == "__main__":
    main()
