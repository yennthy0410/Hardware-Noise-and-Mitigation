"""Start local transports only. Never imports execution engines or dispatches work."""
import json,os,socket,subprocess,sys,time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
HERE=Path(__file__).resolve().parent
PYTHON=HERE/'.venv_dashboard/Scripts/python.exe'
SERVICES=(('Broker',8767,'auto_broker.py',['--serve']),('Dashboard',8766,'serve.py',[]))

def process_api():
    site=str(Path(sys.base_prefix)/'Lib/site-packages')
    if site not in sys.path:sys.path.append(site)
    import psutil
    return psutil

@contextmanager
def launch_lock():
    import msvcrt
    (HERE/'state').mkdir(exist_ok=True)
    with (HERE/'state/service-launch.lock').open('a+b') as stream:
        if os.fstat(stream.fileno()).st_size==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('Another unified launcher is active; no duplicate launch attempted')
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)

def inventory(script,args):
    ps=process_api();expected=['-B',str(HERE/script),*args];matches=[]
    for p in ps.process_iter():
        try:
            if 'python' in p.name().lower() and p.cmdline()[1:]==expected:matches.append(p)
        except ps.NoSuchProcess:continue
        except ps.AccessDenied:raise RuntimeError('Process identity unavailable; refusing an ambiguous launch/restart')
    ids={p.pid for p in matches};roots=[p for p in matches if p.ppid() not in ids]
    if len(roots)>1:raise RuntimeError('Multiple independent service instances; nothing stopped')
    return matches,expected

def healthy(name):
    try:
        if name=='Broker':
            from broker_client import call
            return call('/health').get('service')=='mxene-broker'
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:8766/api/health',timeout=2) as r:
            value=json.load(r)
            return value.get('mode')=='local read-only' and value.get('ibm_network') is False
    except Exception:return False

def port_free(port):
    with socket.socket() as sock:
        if os.name=='nt':sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:sock.bind(('127.0.0.1',port));return True
        except OSError:return False

def owns_listener(matches,port):
    ids={p.pid for p in matches}
    listeners=[c for c in process_api().net_connections(kind='tcp') if c.status=='LISTEN' and c.laddr.port==port]
    return bool(listeners) and all(c.pid in ids for c in listeners)

def ensure(service,restart=False):
    name,port,script,args=service
    matches,expected=inventory(script,args)
    if matches and not restart:
        if owns_listener(matches,port) and healthy(name):return 'HEALTHY (reused)'
        raise RuntimeError('Existing service is not healthy; inspect logs or use -Restart')
    if restart and matches:
        ps=process_api();ids={p.pid for p in matches}
        # Only these exact service processes, never --worker/--manual-worker children.
        for p in sorted(matches,key=lambda p:p.ppid() not in ids):
            try:
                if p.cmdline()[1:]!=expected:raise RuntimeError('Process identity changed; restart refused')
                p.terminate();p.wait(10)
            except ps.NoSuchProcess:pass
    if not port_free(port):raise RuntimeError(f'Port {port} is occupied; unknown listener is never terminated')
    env=dict(os.environ)
    for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS'):env[key]='1'
    env['PYTHONDONTWRITEBYTECODE']='1'
    with (HERE/f'state/service-{name.lower()}.log').open('ab') as log:
        child=subprocess.Popen([str(PYTHON),*expected],cwd=HERE.parents[2],env=env,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,shell=False,close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    for _ in range(40):
        if child.poll() is not None:raise RuntimeError('Service exited; inspect its service log')
        if healthy(name):return 'HEALTHY (started)'
        time.sleep(.5)
    raise RuntimeError('Startup health check timed out; inspect before retrying (no duplicate is launched)')

def main(restart=False):
    failed=False
    with launch_lock():
        for service in SERVICES:
            try:print(service[0]+': '+ensure(service,restart),flush=True)
            except Exception as exc:failed=True;print(service[0]+': ERROR - '+str(exc),flush=True)
    print('Dashboard URL: http://127.0.0.1:8766')
    print('No automation start/resume/pause or IBM action requested. Existing automation and campaign progress are preserved.')
    print('Saved results remain accessible with automation stopped or paused; Broker errors do not disable the Dashboard viewer.')
    return 1 if failed else 0

if __name__=='__main__':
    if sys.argv[1:] not in ([],['--restart']):raise SystemExit('Use start_all.ps1 [-Restart]')
    try:raise SystemExit(main('--restart' in sys.argv))
    except RuntimeError as exc:raise SystemExit(str(exc))
