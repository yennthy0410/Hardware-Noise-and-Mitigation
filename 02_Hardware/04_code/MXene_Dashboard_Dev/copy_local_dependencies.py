"""Copy installed dependency distributions into the isolated venv, never edit sources."""
from pathlib import Path
import importlib.metadata as md
import json, re, shutil
from packaging.requirements import Requirement
BASE=Path(__file__).resolve().parent
DEST=BASE/'.venv_dashboard/Lib/site-packages'
sources=[Path('C:/Users/nguye/miniforge3/Lib/site-packages'),Path('C:/Users/nguye/anaconda3/Lib/site-packages')]
pending=['fastapi','uvicorn','httpx']; copied={}
while pending:
    name=pending.pop().lower().replace('_','-')
    if name in copied: continue
    found=next((d for s in sources for d in md.distributions(path=[str(s)]) if d.metadata['Name'].lower().replace('_','-')==name),None)
    if found is None: raise RuntimeError('Missing local distribution: '+name)
    for item in found.files or []:
        relative=Path(str(item))
        if '..' in relative.parts or '__pycache__' in relative.parts: continue
        src=Path(found.locate_file(item)); dst=DEST/relative
        if src.is_file(): dst.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,dst)
    copied[name]=found.version
    for req in found.requires or []:
        # Include unconditional and platform dependencies; omit optional extras.
        parsed=Requirement(req)
        if parsed.marker and not parsed.marker.evaluate({'extra':''}): continue
        pending.append(parsed.name)
(BASE/'dependency_inventory.json').write_text(json.dumps(copied,indent=2))
print(json.dumps(copied))
