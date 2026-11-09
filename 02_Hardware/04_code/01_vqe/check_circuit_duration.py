"""Explicit timing query; safe to import without IBM or file operations."""
def main():
    from pathlib import Path
    from qiskit import qpy
    from qiskit.transpiler.preset_passmanagers import (
        generate_preset_pass_manager,
    )
    from qiskit_ibm_runtime import QiskitRuntimeService
    
    # Thay bằng đường dẫn circuit.qpy thực tế của run_01
    import argparse
    from repository_paths import repository_root, frozen_source
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--source", required=True)
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    QPY_PATH = frozen_source(repository_root(args.project), args.source) / "circuit.qpy"
    if not args.online:
        parser.error("Timing queries require explicit --online; no IBM connection made")
    
    service = QiskitRuntimeService(
        channel="ibm_quantum_platform"
    )
    
    backend = service.backend("ibm_fez")
    
    # Đọc đúng ISA circuit đã lưu, không transpile lại layout
    with QPY_PATH.open("rb") as f:
        circuit = qpy.load(f)[0]
    
    print("ISA circuit depth:", circuit.depth())
    print("Circuit qubits:", circuit.num_qubits)
    
    # Scheduling lại chính ISA circuit đó.
    # optimization_level=0 để tránh tối ưu hóa làm đổi ISA circuit.
    pm = generate_preset_pass_manager(
        target=backend.target,
        optimization_level=0,
        scheduling_method="alap",
    )
    
    scheduled = pm.run(circuit)
    
    # Thời gian một shot (không phải QPU usage)
    duration_s = scheduled.estimate_duration(
        backend.target,
        unit="s",
    )
    
    duration_us = duration_s * 1e6
    
    print("Circuit duration:", duration_us, "µs")

if __name__ == "__main__":
    main()
