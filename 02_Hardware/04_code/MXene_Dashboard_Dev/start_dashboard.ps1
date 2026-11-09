param([switch]$Restart)
$ErrorActionPreference = 'Stop'
if ($Restart) {
    # Verify the listener's exact script before stopping it. Never target all Python.
    @'
import pathlib,sys
sys.path.insert(0,str(pathlib.Path(sys.base_prefix)/'Lib'/'site-packages'))
import psutil
expected=pathlib.Path(sys.argv[1]).resolve()
matches=[]
for process in psutil.process_iter():
    try:
        if 'python' not in process.name().lower():continue
        if process.cmdline()[1:]==['-B',str(expected)]:matches.append(process)
    except psutil.NoSuchProcess:pass
    except psutil.AccessDenied:raise SystemExit('Python identity inaccessible; nothing stopped')
ids={p.pid for p in matches};roots={p.pid for p in matches if p.ppid() not in ids}
if len(roots)>1:raise SystemExit('Ambiguous independent Dashboard services; nothing stopped')
for process in sorted(matches,key=lambda p:p.pid in roots):
    try:
        if process.cmdline()[1:]!=['-B',str(expected)]:raise SystemExit('Identity changed; restart refused')
        process.terminate();process.wait(10)
        print('Stopped verified Dashboard service',process.pid)
    except psutil.NoSuchProcess:pass
'@ | & "$PSScriptRoot\.venv_dashboard\Scripts\python.exe" -B - (Join-Path $PSScriptRoot 'serve.py')
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard restart verification failed; no replacement started.' }
}
$env:PYTHONDONTWRITEBYTECODE='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
& "$PSScriptRoot\.venv_dashboard\Scripts\python.exe" -B "$PSScriptRoot\serve.py"
