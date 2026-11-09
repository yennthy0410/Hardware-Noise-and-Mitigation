"""Sequential local-only CLI checks; child network calls are denied."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path.cwd();AI=ROOT/'02_Hardware/04_code/03_ai_assistant'
AUDIT=Path((ROOT/'.codex_tmp/migration_audit.txt').read_text())
sys.path.insert(0,str(AI))
from diagnostic_manager import child_environment
code="""
import sys,runpy,socket
sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:]
socket.socket.connect=lambda *a: (_ for _ in ()).throw(AssertionError('offline verification forbids network'))
runpy.run_path(sys.argv[0],run_name='__main__')
"""
cases=[]
for entry in (AI/'mxene.py',AI/'01_ai_agent/mxene.py'):
    for args in (['--help'],['status','--project',str(ROOT),'--campaign','vqe-independent-n-01'],
                 ['run','--project',str(ROOT),'--campaign','vqe-independent-n-01'],
                 ['run','--project',str(ROOT),'--campaign','vqe-independent-n-01','--resume'],
                 ['offline','--help'],['compare','--project',str(ROOT),'--campaign','vqe-independent-n-01']):
        cases.append((entry,args,20 if args[0]=='compare' else 0))
for entry in (AI/'diagnostic_manager.py',AI/'03_python_automation/diagnostic_manager.py',
              AI/'check_install.py',AI/'03_python_automation/check_install.py',
              AI/'research_pipeline.py',AI/'03_python_automation/research_pipeline.py',
              AI/'mitigation_pilot.py',AI/'03_python_automation/mitigation_pilot.py',
              AI.parent/'01_vqe/compare_105_pubs.py',AI/'03_python_automation/dependence_statistics.py'):
    cases.append((entry,['--help'],0))
reports=[]
for i,(entry,args,expected) in enumerate(cases):
    result=subprocess.run([sys.executable,'-B','-c',code,str(AI),str(entry),*args],
        cwd=ROOT,env=child_environment(),capture_output=True,text=True,timeout=120)
    (AUDIT/f'cli-{i:02d}.stdout.txt').write_text(result.stdout,encoding='utf-8')
    (AUDIT/f'cli-{i:02d}.stderr.txt').write_text(result.stderr,encoding='utf-8')
    reports.append(dict(entry=entry.relative_to(ROOT).as_posix(),args=args,expected=expected,
                        exit_code=result.returncode,passed=result.returncode==expected))
    print(entry.name,args[0],result.returncode,flush=True)
(AUDIT/'cli-checks.json').write_text(json.dumps(reports,indent=2))
raise SystemExit(0 if all(r['passed'] for r in reports) else 1)
