"""
Validation test for QAOA-QUBO binary encoding.

Tests:
1. 3-bit integer conversion
2. 21-bit decoding
3. Encode/decode round trip
4. Qiskit bit-order conversion
5. Integration with EIS13 and EIS14
6. True EIS loss / SSE / RMSE evaluation

This test DOES NOT:
- build a QUBO
- fit a quadratic surrogate
- run QAOA
- run an optimizer
"""

from __future__ import annotations

import numpy as np

from binary_encoding import (
    PARAMETER_ORDER,
    TOTAL_BITS,
    bits_to_integer,
    integer_to_bits,
    decode_bitstring,
    encode_physical_parameters,
    canonical_to_qiskit_bitstring,
    qiskit_to_canonical_bitstring,
)

from eis_evaluation import EISEvaluator


# ============================================================
# Utility
# ============================================================

def print_parameters(decoded: dict) -> None:
    """Pretty-print decoded EIS parameters."""

    print("Bitstring:", decoded["bitstring"])

    for name in PARAMETER_ORDER:
        level = decoded["levels"][name]
        encoded = decoded["encoded_values"][name]
        physical = decoded["physical_parameters"][name]

        print(
            f"  {name:>3s}: "
            f"level={level}, "
            f"encoded={encoded:.8g}, "
            f"physical={physical:.10g}"
        )


# ============================================================
# Test 1
# 3-bit mapping
# ============================================================

def test_three_bit_mapping():
    print("\n========================================")
    print("TEST 1 — 3-bit mapping")
    print("========================================")

    for integer in range(8):

        bits = integer_to_bits(integer)

        recovered = bits_to_integer(bits)

        bit_text = "".join(
            str(int(bit))
            for bit in bits
        )

        print(
            f"{bit_text} -> {recovered}"
        )

        assert recovered == integer

    print("PASS: 000...111 mapping is correct.")


# ============================================================
# Test 2
# Decode important 21-bit states
# ============================================================

def test_decode_states():
    print("\n========================================")
    print("TEST 2 — Decode 21-bit states")
    print("========================================")

    states = {
        "all_zero": "0" * TOTAL_BITS,

        "all_one": "1" * TOTAL_BITS,

        "mixed": (
            "101"  # Rs
            "010"  # L
            "111"  # Rct
            "001"  # Q1
            "100"  # n1
            "011"  # Q2
            "110"  # n2
        ),
    }

    decoded_results = {}

    for label, bitstring in states.items():

        print(f"\n--- {label} ---")

        decoded = decode_bitstring(
            bitstring
        )

        decoded_results[label] = decoded

        print_parameters(decoded)

        assert len(decoded["bitstring"]) == 21
        assert decoded["parameter_vector"].shape == (7,)
        assert np.all(
            np.isfinite(
                decoded["parameter_vector"]
            )
        )

    # --------------------------------------------------------
    # Explicit boundary checks
    # --------------------------------------------------------

    minimum = decoded_results[
        "all_zero"
    ]["physical_parameters"]

    maximum = decoded_results[
        "all_one"
    ]["physical_parameters"]

    assert np.isclose(
        minimum["Rs"],
        1e-6,
    )

    assert np.isclose(
        maximum["Rs"],
        1e6,
    )

    assert np.isclose(
        minimum["L"],
        1e-12,
    )

    assert np.isclose(
        maximum["L"],
        1e-2,
    )

    assert np.isclose(
        minimum["Rct"],
        1e-6,
    )

    assert np.isclose(
        maximum["Rct"],
        1e8,
    )

    assert np.isclose(
        minimum["Q1"],
        1e-12,
    )

    assert np.isclose(
        maximum["Q1"],
        1e3,
    )

    assert np.isclose(
        minimum["n1"],
        0.30,
    )

    assert np.isclose(
        maximum["n1"],
        1.00,
    )

    assert np.isclose(
        minimum["Q2"],
        1e-12,
    )

    assert np.isclose(
        maximum["Q2"],
        1e3,
    )

    assert np.isclose(
        minimum["n2"],
        0.30,
    )

    assert np.isclose(
        maximum["n2"],
        1.00,
    )

    print(
        "\nPASS: parameter minimum and maximum "
        "states are correct."
    )

    return decoded_results


# ============================================================
# Test 3
# Encode -> Decode round trip
# ============================================================

def test_round_trip(decoded_results):
    print("\n========================================")
    print("TEST 3 — Encode/decode round trip")
    print("========================================")

    original = decoded_results["mixed"]

    physical_parameters = original[
        "parameter_vector"
    ]

    encoded_again = (
        encode_physical_parameters(
            physical_parameters
        )
    )

    original_bitstring = original[
        "bitstring"
    ]

    recovered_bitstring = encoded_again[
        "bitstring"
    ]

    print(
        "Original :",
        original_bitstring,
    )

    print(
        "Recovered:",
        recovered_bitstring,
    )

    assert (
        original_bitstring
        == recovered_bitstring
    )

    print(
        "PASS: exact grid point survives "
        "encode/decode round trip."
    )


# ============================================================
# Test 4
# Qiskit display order
# ============================================================

def test_qiskit_bit_order():
    print("\n========================================")
    print("TEST 4 — Qiskit bit order")
    print("========================================")

    canonical = (
        "101"
        "010"
        "111"
        "001"
        "100"
        "011"
        "110"
    )

    qiskit_string = (
        canonical_to_qiskit_bitstring(
            canonical
        )
    )

    recovered = (
        qiskit_to_canonical_bitstring(
            qiskit_string
        )
    )

    print("Canonical :", canonical)
    print("Qiskit    :", qiskit_string)
    print("Recovered :", recovered)

    assert recovered == canonical

    print(
        "PASS: canonical/Qiskit bit order "
        "conversion is reversible."
    )


# ============================================================
# Test 5
# Integration with EIS objective
# ============================================================

def test_eis_integration(decoded_results):
    print("\n========================================")
    print("TEST 5 — EIS objective integration")
    print("========================================")

    # Use the mixed state rather than all-zero/all-one states.
    #
    # Extreme parameter combinations can generate extremely poor
    # impedance responses and are not useful as the first
    # integration test.

    bitstring = decoded_results[
        "mixed"
    ]["bitstring"]

    parameters = decoded_results[
        "mixed"
    ]["parameter_vector"]

    print("\nTest bitstring:")
    print(bitstring)

    print("\nPhysical parameter vector:")
    for name, value in zip(
        PARAMETER_ORDER,
        parameters,
    ):
        print(
            f"{name:>3s} = {value:.10g}"
        )

    for dataset in (
        "EIS13",
        "EIS14",
    ):

        print("\n----------------------------------------")
        print(f"Dataset: {dataset}")
        print("----------------------------------------")

        evaluator = EISEvaluator(
            dataset
        )

        result = evaluator.evaluate(
            parameters,
            return_arrays=False,
        )

        loss = result[
            "weighted_complex_loss"
        ]

        sse = result["SSE"]
        rmse = result["RMSE"]

        print(
            "weighted_complex_loss = "
            f"{loss:.12g}"
        )

        print(
            "SSE                   = "
            f"{sse:.12g}"
        )

        print(
            "RMSE                  = "
            f"{rmse:.12g}"
        )

        assert np.isfinite(loss)
        assert np.isfinite(sse)
        assert np.isfinite(rmse)

        assert loss >= 0
        assert sse >= 0
        assert rmse >= 0

        print(
            f"PASS: {dataset} successfully evaluated."
        )


# ============================================================
# Main
# ============================================================

def main():

    print("\n")
    print("========================================")
    print("QAOA-QUBO Experiment 00")
    print("Binary Encoding Validation")
    print("========================================")

    test_three_bit_mapping()

    decoded_results = (
        test_decode_states()
    )

    test_round_trip(
        decoded_results
    )

    test_qiskit_bit_order()

    test_eis_integration(
        decoded_results
    )

    print("\n")
    print("========================================")
    print("ALL TESTS PASSED")
    print("========================================")
    print(
        "21-bit state"
        " -> 7 EIS parameters"
        " -> EIS objective"
    )
    print(
        "validated for EIS13 and EIS14."
    )
    print("========================================\n")


if __name__ == "__main__":
    main()
    