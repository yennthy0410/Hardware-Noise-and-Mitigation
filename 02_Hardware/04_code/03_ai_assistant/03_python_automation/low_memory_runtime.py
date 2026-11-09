"""Standard-library-only startup policy for the single campaign worker."""

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

import os

THREAD_LIMITS = (
    'OPENBLAS_NUM_THREADS', 'OPENBLAS_DEFAULT_NUM_THREADS', 'MKL_NUM_THREADS',
    'OMP_NUM_THREADS', 'OMP_THREAD_LIMIT', 'NUMEXPR_NUM_THREADS',
    'NUMEXPR_MAX_THREADS', 'BLIS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
    'GOTO_NUM_THREADS', 'RAYON_NUM_THREADS', 'QISKIT_NUM_PROCS',
)


def limit_native_threads(environment=None):
    env = os.environ if environment is None else environment
    env.update({key: '1' for key in THREAD_LIMITS})
    env.update(OMP_DYNAMIC='FALSE', MKL_DYNAMIC='FALSE', QISKIT_PARALLEL='FALSE')
    return env


def remote_runtime():
    """Load the shared remote client only when an IBM operation needs it."""
    from pathlib import Path
    import sys
    limit_native_threads()
    directory=str(Path(__file__).resolve().parents[2]/'01_vqe')
    if directory not in sys.path:sys.path.insert(0,directory)
    from ibm_runtime_client import remote_runtime as load
    return load()


def iter_provider_jobs(service, *, page_size=50, **filters):
    """Exhaust bounded pages; failed/overlapping reads never prove absence.

    Retain IDs only to detect unstable pagination, not SDK job/result objects.
    No fallback to an unbounded read when a provider lacks pagination support.
    """
    if not 1 <= page_size <= 100:
        raise ValueError('Provider page size must be 1..100')
    offset = 0
    seen = set()
    while True:
        page = service.jobs(limit=page_size, skip=offset, **filters)
        count = 0
        for job in page:
            identity = job.job_id()
            if identity in seen:
                raise RuntimeError('Provider inventory changed or pagination did not advance; reconcile again')
            seen.add(identity)
            count += 1
            if count > page_size:
                raise RuntimeError('Provider ignored bounded page size')
            yield job
        # Runtime's jobs(limit, skip) fills the requested limit across its
        # internal pages, so a short returned page establishes exhaustion.
        if count < page_size:
            return
        offset += count
        del page
