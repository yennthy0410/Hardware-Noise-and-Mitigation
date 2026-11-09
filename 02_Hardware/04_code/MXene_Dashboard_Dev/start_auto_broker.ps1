param([switch]$Restart)
$ErrorActionPreference='Stop'
if ($Restart) {
@'
import pathlib,sys
sys.path.insert(0,str(pathlib.Path(sys.base_prefix)/'Lib/site-packages'))
import psutil
expected=pathlib.Path(sys.argv[1]).resolve()
matches=[]
for process in psutil.process_iter():
    try:
        if 'python' not in process.name().lower():continue
        if process.cmdline()[1:]==['-B',str(expected),'--serve']:matches.append(process)
    except psutil.NoSuchProcess:pass
    except psutil.AccessDenied:
        raise SystemExit('Process identity inaccessible; broker restart refused')
ids={p.pid for p in matches}
roots=[p for p in matches if p.ppid() not in ids]
if len(roots)>1:raise SystemExit('Multiple independent broker services; nothing stopped')
# Windows venv launcher and its base interpreter form one verified service.
# Never enumerate/terminate --worker descendants or any other Python command.
for process in sorted(matches,key=lambda p:p.pid in {x.pid for x in roots}):
    try:
        if process.cmdline()[1:]!=['-B',str(expected),'--serve']:raise SystemExit('Process identity changed; restart refused')
        process.terminate();process.wait(10)
    except psutil.NoSuchProcess:pass
if matches:print('Restarted only the verified AUTO broker service; no campaign worker was stopped.')
'@ | & "$PSScriptRoot\.venv_dashboard\Scripts\python.exe" -B - (Join-Path $PSScriptRoot 'auto_broker.py')
    if ($LASTEXITCODE -ne 0) { throw 'Broker identity verification failed.' }
}
$env:PYTHONDONTWRITEBYTECODE='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
& "$PSScriptRoot\.venv_dashboard\Scripts\python.exe" -B "$PSScriptRoot\auto_broker.py" --serve
