"""Process-level safety rails in addition to the GET-only HTTP interface."""
import os, sys
from pathlib import Path
HOME=Path(__file__).resolve().parent
OUTPUT=HOME.parent.parent/'01_vqe/06_DD_TREX_ZNE_mitigation/output'
PRODUCTION=HOME.parent/'03_ai_assistant'
def install():
    sys.dont_write_bytecode=True
    for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS'): os.environ[key]='1'
    def inside(value):
        try: return Path(value).resolve().is_relative_to(HOME)
        except (ValueError,TypeError): return False
    def guard(event,args):
        if event=='socket.connect':
            from local_ai import LOCAL_AI
            from broker_client import BROKER_ACCESS
            from ai_providers import CLOUD_DESTINATIONS
            if not ((LOCAL_AI.get() and args[1]==('127.0.0.1',11434)) or
                    (BROKER_ACCESS.get() and args[1]==('127.0.0.1',8767)) or
                    args[1] in CLOUD_DESTINATIONS.get()):
                raise PermissionError('Outbound denied except explicitly scoped local adapters')
        if event in ('subprocess.Popen','os.system'): raise PermissionError('Dashboard prohibits subprocesses')
        if event=='import' and args[0].split('.')[0] in {'qiskit','qiskit_ibm_runtime','qiskit_aer'}: raise PermissionError('IBM modules prohibited')
        if event=='open':
            path,mode,flags=args
            writing=(isinstance(mode,str) and any(x in mode for x in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
            if writing and not isinstance(path,int) and not inside(path): raise PermissionError('Writes restricted to dashboard directory')
            if not writing and not isinstance(path,int):
                resolved=Path(path).resolve()
                allowed=any(resolved.is_relative_to(base) for base in (HOME,OUTPUT,Path(sys.base_prefix).resolve()))
                allowed=allowed or (resolved.is_relative_to(PRODUCTION) and resolved.suffix=='.py')
                # Existing progressive loader: exactly these audited instructions,
                # not arbitrary repository documents or credential files.
                allowed=allowed or resolved in {(PRODUCTION/'02_skills'/name/'SKILL.md').resolve() for name in ('workflow-manager','campaign-comparison','dependence-aware-statistics')}
                if not allowed: raise PermissionError('Read outside dashboard source allowlist')
        if event=='sqlite3.connect':
            name=str(args[0])
            if not inside(name) and not (name.startswith('file:') and 'mode=ro' in name): raise PermissionError('External SQLite must be read-only')
        if event in ('os.remove','os.rmdir','os.mkdir','os.rename'):
            for path in args[:2] if event=='os.rename' else args[:1]:
                if not inside(path): raise PermissionError('Filesystem mutations restricted to dashboard')
    sys.addaudithook(guard)
