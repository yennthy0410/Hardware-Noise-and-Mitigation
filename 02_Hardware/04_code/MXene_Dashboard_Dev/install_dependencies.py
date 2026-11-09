"""Install only into this dashboard venv; workaround Windows sandbox temp ACLs."""
import os
from pathlib import Path
import runpy
import sys
import zipfile

HERE = Path(__file__).resolve().parent
assert Path(sys.prefix).resolve() == HERE / '.venv', 'Use dashboard venv only'
scratch = HERE / 'tmp'
scratch.mkdir(exist_ok=True)
os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
original_mkdir = os.mkdir
def mkdir(path, mode=0o777, *, dir_fd=None):
    if Path(path).resolve().is_relative_to(HERE) and mode == 0o700:
        mode = 0o755
    return original_mkdir(path, mode, dir_fd=dir_fd)
os.mkdir = mkdir
if not (Path(sys.prefix) / 'Lib/site-packages/pip').exists():
    wheel = next((Path(sys.base_prefix)/'Lib/ensurepip/_bundled').glob('pip-*.whl'))
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(Path(sys.prefix)/'Lib/site-packages')
sys.argv = ['pip', 'install', '--no-cache-dir', '--disable-pip-version-check',
            'fastapi', 'uvicorn', 'httpx']
runpy.run_module('pip', run_name='__main__')
