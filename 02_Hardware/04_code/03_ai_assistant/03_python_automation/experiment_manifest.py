"""Validated, immutable-input experiment manifest construction."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any
import sys

_VQE_CODE = Path(__file__).resolve().parents[2] / "01_vqe"
if str(_VQE_CODE) not in sys.path:
    sys.path.insert(0, str(_VQE_CODE))

from repository_paths import MITIGATION_REL, repository_root, frozen_source

FROZEN_NAMES = ("circuit.qpy", "theta.csv", "observables.json")
REQUIRED_VALIDATION = (
    "qpy_deserialized", "zero_unbound_parameters", "backend_verified_from_saved_metadata",
    "layout_verified_from_saved_metadata_and_qubit_table", "seven_observables_match_layout",
    "theta_21_finite_and_vector_hash_matches", "already_transpiled_no_retranspilation",
)
ADAPTIVE_TARGETS = (5, 10, 15, 20, 25, 30)
ADAPTIVE_CHUNKS = (5, 10)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ExperimentManifest:
    schema_version: int
    experiment_id: str
    source_run: str
    backend: str
    source_bundle: str
    input_hashes: dict[str, str]
    physical_qubits: tuple[int, ...]
    shots_per_pub: int
    n: int
    pubs_per_treatment: int
    treatments: tuple[dict[str, Any], ...]
    jobs_per_replicate_block: int
    replicate_blocks: int
    total_jobs: int
    total_requested_pubs: int
    legacy_n_semantics: str
    validation: dict[str, bool]
    provenance_note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def pub_count(self) -> int:
        """Compatibility accessor for total requested PUBs in the whole plan."""
        return self.total_requested_pubs

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TargetManifest:
    """Schema-3 cumulative target plan; each chunk is a complete eight-job Batch."""
    schema_version: int
    experiment_id: str
    target_id: str
    target_n: int
    chunk_n: int
    blocks_required: int
    source_run: str
    backend: str
    source_bundle: str
    input_hashes: dict[str, str]
    physical_qubits: tuple[int, ...]
    shots_per_pub: int
    treatment_ids: tuple[str, ...]
    treatments: tuple[dict[str, Any], ...]
    jobs_per_block: int
    pubs_per_block_per_treatment: int
    total_jobs: int
    total_requested_pubs: int
    validation: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        payload=json.dumps(self.to_dict(),sort_keys=True,separators=(",",":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_manifest(root: str | Path | None = None, *, n: int = 5, replicate_blocks: int = 1,
                   shots: int = 4096, experiment_id: str = "vqe_mitigation_phase3a") -> ExperimentManifest:
    """Validate the selected run_02 lock and return a deterministic plan; writes nothing."""
    if isinstance(replicate_blocks, bool) or not isinstance(replicate_blocks, int) or replicate_blocks < 1:
        raise ValueError("replicate_blocks must be a positive integer")
    if isinstance(shots, bool) or shots < 1:
        raise ValueError("shots must be a positive integer")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("n (PUBs per treatment) must be a positive integer")
    if not experiment_id or any(c in experiment_id for c in "/\\:"):
        raise ValueError("experiment_id must be a nonempty single path component")
    repo = repository_root(root)
    bundle = frozen_source(repo)
    lock_path = bundle / "lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("source_run") != "run_02":
        raise ValueError("Frozen bundle must explicitly record source_run=run_02")
    if lock.get("backend") != "ibm_fez":
        raise ValueError("Frozen bundle backend must be ibm_fez")
    locked_shots = lock.get("shots_requested", lock.get("shots", -1))
    if int(locked_shots) != int(shots):
        raise ValueError("Requested shots differ from the frozen run_02 lock")
    hashes: dict[str, str] = {}
    for name in FROZEN_NAMES:
        actual = _sha(bundle / name)
        if lock.get("hashes", {}).get(name) != actual:
            raise ValueError(f"Frozen input hash mismatch: {name}")
        hashes[name] = actual
    validation = lock.get("validation", {})
    failed = [name for name in REQUIRED_VALIDATION if validation.get(name) is not True]
    if failed:
        raise ValueError("Frozen bundle validation incomplete: " + ", ".join(failed))
    layout = tuple(int(q) for q in lock.get("physical_observable_qubits", lock.get("observable_physical_qubits", ())))
    if layout != tuple(range(140, 147)):
        raise ValueError("Frozen observable layout must be physical qubits 140..146")
    from vqe_adapter import load_pilot
    pilot = load_pilot(repo)
    treatment_rows = []
    for name, dd, trex, zne in pilot.TREATMENTS:
        options = pilot.configuration(name, int(shots))
        if hasattr(options, "model_dump"):
            options = options.model_dump(mode="json")
        else:
            options = str(options)
        treatment_rows.append({"id": name, "dd": dd, "trex": trex, "zne": zne, "runtime_options": options})
    ts = tuple(treatment_rows)
    if tuple(row["id"] for row in ts) != tuple(f"M{i}_{suffix}" for i,suffix in enumerate(
            ("Baseline","DD","TREX","ZNE","DD_TREX","DD_ZNE","TREX_ZNE","ALL"))):
        raise ValueError("Canonical VQE treatments must remain ordered M0 through M7")
    blocks = int(replicate_blocks)
    return ExperimentManifest(
        schema_version=2, experiment_id=experiment_id, source_run="run_02", backend="ibm_fez",
        source_bundle=str(bundle), input_hashes=hashes, physical_qubits=layout,
        shots_per_pub=int(shots), n=int(n), pubs_per_treatment=int(n), treatments=ts,
        jobs_per_replicate_block=len(ts), replicate_blocks=blocks,
        total_jobs=len(ts) * blocks, total_requested_pubs=len(ts) * int(n) * blocks,
        legacy_n_semantics="n is the requested repeated PUB count per treatment, consistent with the VQE pilot; replicate_blocks counts independent logical Batch blocks.",
        validation={name: True for name in REQUIRED_VALIDATION},
        provenance_note=("Source is Fixed ISA Repeatability run_02. Preserve the documented Study A theta provenance discrepancy; "
                         f"the source lock currently records studyA_angle_discrepancy_preserved={validation.get('studyA_angle_discrepancy_preserved')}. "
                         "Do not substitute run_01 or Study A theta."),
    )


def build_target_manifest(root: str | Path | None = None, *, target_n: int,
                          chunk_n: int = 5, shots: int = 4096,
                          experiment_id: str = "vqe_mitigation_adaptive") -> TargetManifest:
    """Build a write-free target plan pinned to the immutable run_02 bundle.

    A target must be divisible by its selected chunk size so no treatment is
    silently over-collected. Chunk sizes 5 and 10 are the only approved values.
    """
    if isinstance(target_n,bool) or not isinstance(target_n,int) or target_n not in ADAPTIVE_TARGETS:
        raise ValueError(f"target_n must be one of {ADAPTIVE_TARGETS}")
    if isinstance(chunk_n,bool) or not isinstance(chunk_n,int) or chunk_n not in ADAPTIVE_CHUNKS:
        raise ValueError(f"chunk_n must be one of {ADAPTIVE_CHUNKS}")
    if target_n % chunk_n:
        raise ValueError("target_n must be divisible by chunk_n to avoid overshooting the target")
    base=build_manifest(root,n=chunk_n,replicate_blocks=1,shots=shots,experiment_id=experiment_id)
    blocks=target_n//chunk_n
    treatments=base.treatments
    # A target is an experiment identity, not merely a sample-size label.
    # Including experiment_id prevents independent campaigns/n-levels from
    # sharing counters, Batch IDs, results, or analysis directories.
    target_id=f"{experiment_id}-target-n{target_n}-chunk-{chunk_n}"
    return TargetManifest(
        schema_version=3,experiment_id=experiment_id,target_id=target_id,target_n=target_n,
        chunk_n=chunk_n,blocks_required=blocks,source_run=base.source_run,backend=base.backend,
        source_bundle=base.source_bundle,input_hashes=base.input_hashes,
        physical_qubits=base.physical_qubits,shots_per_pub=base.shots_per_pub,
        treatment_ids=tuple(item["id"] for item in treatments),treatments=treatments,
        jobs_per_block=8,pubs_per_block_per_treatment=chunk_n,total_jobs=blocks*8,
        total_requested_pubs=blocks*8*chunk_n,validation=base.validation)

