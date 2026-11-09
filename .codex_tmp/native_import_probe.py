import faulthandler,os,sys,json,time,ctypes
faulthandler.enable(all_threads=True)
print(json.dumps({'pid':os.getpid(),'parent':os.getppid(),'python':sys.version}),flush=True)
class Memory(ctypes.Structure):
    _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in
        ('total_physical','available_physical','total_pagefile','available_pagefile','total_virtual','available_virtual','extended')]
m=Memory();m.length=ctypes.sizeof(m)
if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
    print('memory',json.dumps({k:getattr(m,k) for k,_ in m._fields_}),flush=True)
import numpy as np
print('numpy imported',np.__version__,flush=True)
import scipy.linalg
print('scipy imported',flush=True)
import qiskit
print('qiskit imported',flush=True)
import qiskit_aer
print('aer imported',flush=True)
import qiskit_ibm_runtime
print('runtime imported; no service instantiated',flush=True)
np.testing.assert_allclose(np.linalg.solve(np.eye(4),np.ones(4)),np.ones(4))
np.testing.assert_allclose(scipy.linalg.expm(np.zeros((2,2))),np.eye(2))
np.show_config()
print('small offline numerical checks passed; sampling window 10 seconds',flush=True)
time.sleep(10)
print('probe normal exit',flush=True)
