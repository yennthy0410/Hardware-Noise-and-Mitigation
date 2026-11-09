import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
root=Path.cwd();scratch=root/'.codex_tmp/legacy-readiness'
scratch.mkdir(exist_ok=True)
os.environ['MXENE_SCRATCH_DIR']=str(scratch)
os.environ['MPLCONFIGDIR']=str(scratch/'mpl')
os.environ['TEMP']=str(scratch);os.environ['TMP']=str(scratch)
sys.path.insert(0,str(root/'02_Hardware/04_code/01_vqe/tests'))
with patch('socket.socket.connect',side_effect=AssertionError('Readiness tests forbid IBM/network calls')):
    suite=unittest.defaultTestLoader.discover('02_Hardware/04_code/01_vqe/tests')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
