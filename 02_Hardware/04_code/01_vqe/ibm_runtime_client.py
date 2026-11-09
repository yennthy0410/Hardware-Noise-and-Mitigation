"""Lazy remote-only IBM client import; local Aer work belongs in another process."""
import importlib
import sys


def remote_runtime():
    if 'qiskit_ibm_runtime' not in sys.modules:
        from qiskit.utils.optionals import HAS_AER
        # Runtime's optional local executor eagerly probes/imports Aer. The
        # public scoped guard avoids that allocation and restores the probe.
        # This process exclusively uses remote IBM services, never local Aer.
        with HAS_AER.disable_locally():
            return importlib.import_module('qiskit_ibm_runtime')
    return sys.modules['qiskit_ibm_runtime']
