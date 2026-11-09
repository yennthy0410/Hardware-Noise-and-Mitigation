import sys, os
from pathlib import Path
sys.dont_write_bytecode=True
# Archived provenance paths are relative to the original workspace root.
os.chdir(Path(__file__).resolve().parents[3])
import asyncio
# Windows creates an internal loopback socketpair when initializing an event loop.
# Create that infrastructure before denying all subsequent outbound connections.
loop=asyncio.new_event_loop()
asyncio.set_event_loop(loop)
from security import install
install()
import uvicorn
if __name__=='__main__':
    (Path(__file__).resolve().parent/'state/server.pid').write_text(str(os.getpid()),encoding='ascii')
    server=uvicorn.Server(uvicorn.Config('app:app',host='127.0.0.1',port=8766,workers=1,reload=False,log_level='warning',access_log=False))
    loop.run_until_complete(server.serve())

