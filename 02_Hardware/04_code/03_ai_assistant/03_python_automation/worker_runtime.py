
"""Single-process campaign lock and bounded retries for read-only provider methods."""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)

import os
from pathlib import Path
import time
from contextlib import contextmanager


@contextmanager
def campaign_worker_lock(directory):
    """One active campaign worker per repository, plus its existing identity lock."""
    directory=Path(directory)
    with WorkerLock(directory.parent/'auto_manager.lock'), WorkerLock(directory/'worker.lock'):
        yield

class WorkerLock:
    def __init__(self, path):
        self.path=Path(path)
        self._handle=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # OS-backed lock: stale files are safe; the lock is released on process death.
        self._handle=open(self.path,'a+b')
        try:
            if os.name=='nt':
                import msvcrt
                self._handle.seek(0)
                if self._handle.read(1)==b'':
                    self._handle.seek(0);self._handle.write(b'0');self._handle.flush()
                self._handle.seek(0);msvcrt.locking(self._handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:
            self._handle.close();self._handle=None
            raise RuntimeError('Another worker already owns this campaign lock') from None
        return self
    def __exit__(self,*args):
        if self._handle is not None:
            try:
                if os.name=='nt':
                    import msvcrt
                    self._handle.seek(0);msvcrt.locking(self._handle.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self._handle.fileno(),fcntl.LOCK_UN)
            finally:
                self._handle.close();self._handle=None

def resource_exhaustion_error(exc):
    seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc,OSError) and getattr(exc,'winerror',None)==1450:return True
        exc=exc.__cause__ or exc.__context__
    return False


def transient_read_error(exc):
    """Recognize transport failures, including SDK exceptions wrapping requests."""
    seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc,OSError) and getattr(exc,'winerror',None)==1450:
            return True  # Read/archive retry only; UNKNOWN submission gates still take precedence.
        if isinstance(exc,(TimeoutError,ConnectionError)):
            return True
        # HTTP rate limiting and service outages are read-retryable; missing
        # local archives, permissions and arbitrary OSErrors are not.
        response=getattr(exc,'response',None)
        code=getattr(response,'status_code',None) or getattr(exc,'status_code',None)
        if isinstance(code,int) and code in (408,429,500,502,503,504):return True
        if type(exc).__module__.startswith(('requests.','urllib3.')) and type(exc).__name__ in {
                'ConnectionError','Timeout','ConnectTimeout','ReadTimeout','ProxyError',
                'NewConnectionError','MaxRetryError','ProtocolError'}:
            return True
        exc=exc.__cause__ or exc.__context__
    return False


class RetryingReadOnlyService:
    """Proxy only provider reads. Never retry submit, run, cancel or mutation calls."""
    _READS=frozenset({'job','jobs','usage','backend','backends','instances','active_instance'})
    def __init__(self,service,attempts=3):
        if not isinstance(attempts,int) or isinstance(attempts,bool) or not 1<=attempts<=5:
            raise ValueError('Read attempts must be 1..5')
        self._service=service;self._attempts=attempts
    def __getattr__(self,name):
        value=getattr(self._service,name)
        if name not in self._READS or not callable(value):return value
        def call(*args,**kwargs):
            for attempt in range(self._attempts):
                try:
                    result=value(*args,**kwargs)
                    if name=='job':return RetryingReadOnlyJob(result,self._attempts)
                    if name=='jobs':return [RetryingReadOnlyJob(job,self._attempts) for job in result]
                    return result
                except Exception as exc:
                    if not transient_read_error(exc) or attempt==self._attempts-1:raise
                    time.sleep(min(2**attempt,4))
        return call


class RetryingReadOnlyJob(RetryingReadOnlyService):
    """Preserve the SDK object interface; mutations pass through exactly once."""
    _READS=frozenset({'result','status','metrics','backend','properties','tags'})


class TerminalResultUnavailable(RuntimeError):
    """Repeated permanent result errors from an authoritatively terminal job."""
    def __init__(self, status, evidence):
        super().__init__('Terminal provider result permanently unavailable')
        self.status=status
        self.evidence=evidence


def terminal_result(job, *, attempts=3, sleeper=time.sleep):
    """Bounded read retries; network, authorization and unknown errors propagate."""
    evidence=[]
    terminal={'DONE','COMPLETED','FAILED','ERROR','CANCELLED','CANCELED'}
    for attempt in range(attempts):
        status=str(job.status()).upper().split('.')[-1]
        if status not in terminal:raise RuntimeError('Job is not terminal; retrieval deferred')
        try:return job.result()
        except Exception as exc:
            if transient_read_error(exc):raise
            message=str(exc).lower()
            code=getattr(exc,'status_code',None) or getattr(getattr(exc,'response',None),'status_code',None)
            permanent=(code in (404,410) or any(term in message for term in (
                'result not found','results not found','result unavailable','results unavailable',
                'result permanently unavailable','no result','job failed','job was cancelled',
                'job was canceled','job has failed','job has been cancelled',
                'numerical failure','zne extrapolation failed','extrapolation failure')))
            if not permanent or code in (401,403):raise
            confirmed=str(job.status()).upper().split('.')[-1]
            if confirmed!=status:raise RuntimeError('Contradictory terminal provider evidence') from exc
            evidence.append({'status':status,'error_type':type(exc).__name__,'status_code':code,'read_attempt':attempt+1})
            if attempt+1<attempts:sleeper(min(2**attempt,4))
    raise TerminalResultUnavailable(status,evidence)
