"""Read-only repository discovery and mitigation path contract (no IBM imports)."""
from pathlib import Path
import hashlib
import os
import sys
import uuid
import shutil
import time
import warnings

CODE_REL = Path("02_Hardware/04_code/01_vqe")
STUDY_REL = Path("01_quantum_eis_fit/04_code/vqe")
MITIGATION_REL = Path("02_Hardware/01_vqe/06_DD_TREX_ZNE_mitigation")
FROZEN_NAMES = ("circuit.qpy", "theta.csv", "observables.json")
OUTPUT_FOLDERS = ("01_results_and_figures", "02_run_level_data",
                  "03_IBM_execution_records", "04_runtime_logs")
SCRATCH_ENV = "MXENE_SCRATCH_DIR"
WORKSPACE_SCRATCH_NAME = ".mxs"
SCRATCH_OWNER_MARKER = ".mxs-owner"

def repository_root(explicit=None):
    starts = [Path(explicit).resolve()] if explicit is not None else [Path(__file__).resolve().parent, Path.cwd().resolve()]
    candidates = starts if explicit is not None else [p for start in starts for p in (start, *start.parents)]
    for root in candidates:
        if all((root / p).is_dir() for p in (CODE_REL, STUDY_REL, MITIGATION_REL / "output")) and (root / "data/EIS14.csv").is_file():
            if all((root / STUDY_REL / name).is_file() for name in ("hea_ansatz.py", "parameter_mapping.py", "impedance_model.py")):
                output = root / MITIGATION_REL / "output"
                if not output.resolve().is_relative_to(root):
                    raise ValueError("Mitigation output resolves outside the repository")
                for name in OUTPUT_FOLDERS:
                    contained(output, name)
                return root
    raise FileNotFoundError("Invalid repository root: require Study A VQE modules, VQE code, mitigation output directory and data/EIS14.csv")

def contained(base, *parts):
    base = Path(base).resolve()
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Path escapes allowed directory: " + str(target))
    return target


def comparison_output_paths(root=None):
    """Final Comparison only: flat PNG and CSV/JSON publication directories."""
    repo=Path(root).resolve() if root is not None else repository_root()
    if not (repo/'02_Hardware/04_code').is_dir():
        raise FileNotFoundError(f'Not an MXene repository root: {repo}')
    output=contained(repo,MITIGATION_REL,'output')
    return {'figures':contained(output,'01_results_and_figures','compare'),
            'data':contained(output,'02_run_level_data','compare')}


def _writable_probe(directory: Path) -> None:
    """Create and remove one unique probe file; never inspect or remove other files."""
    directory.mkdir(parents=True,exist_ok=True)
    probe=directory/(".p"+uuid.uuid4().hex[:8])
    with probe.open("x",encoding="ascii") as handle:
        handle.write("mxene scratch probe\n")
        handle.flush();os.fsync(handle.fileno())
    probe.unlink()


def scratch_root_info(root=None) -> dict:
    """Select MXENE_SCRATCH_DIR when writable; otherwise verify workspace ``.mxs``.

    Scratch roots are never placed inside Study A, frozen inputs, or the
    mitigation output tree. The returned path is suitable only for disposable
    tests, mocks, intermediate analysis staging, and caches.
    """
    repo=repository_root(root)
    configured=os.environ.get(SCRATCH_ENV)
    requested=Path(configured).expanduser().resolve() if configured else None
    protected=(repo/STUDY_REL).resolve(),(repo/MITIGATION_REL/"output").resolve()
    if requested is not None:
        if any(requested==path or requested.is_relative_to(path) for path in protected):
            external_error="configured scratch path is inside a protected research directory"
        else:
            try:
                _writable_probe(requested)
                return {"path":requested,"source":"environment","external_accessible":True,"fallback_reason":None}
            except OSError as exc:
                if getattr(exc,"winerror",None)==1450:
                    raise  # Resource exhaustion is not evidence of an unwritable location.
                external_error=type(exc).__name__
    else:
        external_error="MXENE_SCRATCH_DIR is not set"
    fallback=(repo/WORKSPACE_SCRATCH_NAME).resolve()
    if any(fallback==path or fallback.is_relative_to(path) for path in protected):
        raise ValueError("Workspace scratch fallback resolves inside protected research data")
    try:
        _writable_probe(fallback)
    except OSError as exc:
        if getattr(exc,"winerror",None)==1450:raise
        raise PermissionError("Neither MXENE_SCRATCH_DIR nor workspace-local .mxs is writable") from exc
    return {"path":fallback,"source":"workspace_fallback","external_accessible":False,
        "fallback_reason":external_error}


def scratch_root(root=None) -> Path:
    return Path(scratch_root_info(root)["path"])


def create_scratch_dir(prefix="t",root=None) -> Path:
    """Create one short uniquely named scratch directory with an ownership marker."""
    base=scratch_root(root)
    label="".join(ch for ch in str(prefix).lower() if ch.isalnum())[:3] or "t"
    token=uuid.uuid4().hex[:8]
    target=base/f"{label}_{token}"
    target.mkdir()
    (target/SCRATCH_OWNER_MARKER).write_text(token,encoding="ascii")
    return target


def cleanup_scratch_dir(path,root=None) -> None:
    """Remove only a direct child created by ``create_scratch_dir``."""
    repo=repository_root(root);target=Path(path).resolve()
    allowed={(repo/WORKSPACE_SCRATCH_NAME).resolve()}
    if os.environ.get(SCRATCH_ENV):allowed.add(Path(os.environ[SCRATCH_ENV]).expanduser().resolve())
    if target.parent not in allowed or not target.is_dir():
        raise ValueError("Refusing cleanup outside a directly owned scratch directory")
    marker=target/SCRATCH_OWNER_MARKER
    if not marker.is_file():
        raise ValueError("Refusing cleanup without the scratch ownership marker")
    token=marker.read_text(encoding="ascii").strip()
    if len(token)!=8 or target.name[-8:]!=token:
        raise ValueError("Scratch ownership marker does not match the directory name")
    # Windows/OneDrive can briefly hold a just-closed SQLite fixture. Retry
    # only sharing/access locks on this already verified, directly owned path.
    for attempt in range(40):
        try:
            shutil.rmtree(target)
            return
        except PermissionError:
            if attempt == 39:
                warnings.warn(f"Owned scratch directory remains locked; left in place: {target}",RuntimeWarning)
                return
            time.sleep(0.25)


def atomic_archive(path, chunks, expected_sha256):
    """Fsync, publish without overwrite, then verify before any ledger update.

    A short sibling is needed for same-volume atomic publication. An interrupted
    write can leave an unreferenced temporary file, never a partial final archive.
    Existing immutable archives are compared, never repaired or replaced here.
    """
    path=Path(path)
    def digest(file):
        with file.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    if path.exists():
        if digest(path)!=expected_sha256:raise RuntimeError('Immutable archive hash conflict: '+str(path))
        return path
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.parent/('.w'+uuid.uuid4().hex[:16])
    try:
        with temporary.open('xb') as stream:
            for chunk in chunks:stream.write(chunk)
            stream.flush();os.fsync(stream.fileno())
        if digest(temporary)!=expected_sha256:raise RuntimeError('Archive staging hash mismatch: '+str(path))
        try:
            if os.name=='nt':os.rename(temporary,path)  # Windows refuses an existing destination.
            else:os.link(temporary,path)
        except FileExistsError:pass
        if digest(path)!=expected_sha256:raise RuntimeError('Immutable archive hash conflict: '+str(path))
        return path
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass  # Never mask the original write/resource error.

def round_name(value):
    if not value or value in (".", "..") or any(c in value for c in '/\\:'):
        raise ValueError("Round must be a single directory name")
    return value

def mitigation_output(root=None, *parts):
    root = repository_root(root)
    return contained(root / MITIGATION_REL / "output", *parts)

def pilot_script(root=None, explicit=None):
    expected = repository_root(root) / CODE_REL / "mitigation_pilot.py"
    selected = Path(explicit).resolve() if explicit else expected
    if selected != expected or not selected.is_file():
        raise ValueError("Pilot must be the existing VQE script: " + str(expected))
    return selected

def register_science(root=None):
    directory = repository_root(root) / STUDY_REL
    # Import canonical modules without creating __pycache__ in read-only Study A.
    sys.dont_write_bytecode = True
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    for name in ("hea_ansatz", "parameter_mapping", "impedance_model"):
        module = sys.modules.get(name)
        if module is not None and Path(getattr(module, "__file__", "")).resolve() != directory / (name + ".py"):
            raise ImportError("Refusing shadowed scientific module: " + name)
    return directory

def frozen_source(root=None, explicit=None):
    root = repository_root(root)
    if explicit:
        source = Path(explicit).resolve()
    else:
        source = mitigation_output(root, "03_IBM_execution_records", "locked_inputs")
        if not (source / "lock.json").is_file():
            raise FileNotFoundError("Original frozen bundle not configured; supply --source with verified original circuit.qpy, theta.csv, observables.json. No circuit will be generated.")
    missing = [str(source / name) for name in FROZEN_NAMES if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing frozen artifacts: " + ", ".join(missing))
    return source

def source_hashes(source):
    return {name: hashlib.sha256((Path(source) / name).read_bytes()).hexdigest() for name in FROZEN_NAMES}
