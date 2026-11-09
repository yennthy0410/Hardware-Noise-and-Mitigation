"""Actual TCP/HTTP outage injection using the same app, without stopping shared Ollama.
Seeds the read-only snapshot from the running Dashboard; no new manager process.
"""
import os,json,threading,time,urllib.request,urllib.error,socket
from unittest.mock import patch
from reader import HERE
os.chdir(HERE.parents[2])
import uvicorn
from app import app,reader
with urllib.request.urlopen('http://127.0.0.1:8766/api/status') as response:reader.cached=json.load(response)
reader.at=time.monotonic();reader.catalog()
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(16)
port=listener.getsockname()[1]
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True)
def ask(message):
    req=urllib.request.Request(f'http://127.0.0.1:{port}/api/ai/analyze',data=json.dumps({'message':message,'consent':True}).encode(),headers={'Content-Type':'application/json','X-MXene-AI':'local-only'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:return e.code,json.load(e)
try:
    with patch('local_ai.call',side_effect=ConnectionRefusedError('Injected local Ollama outage')) as ai:
        thread.start()
        deadline=time.monotonic()+5
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        for message in ('Show campaign progress.','Analyze n=5 and compare DD with TREX.'):
            status,data=ask(message);assert status==200,(status,data);ai.assert_not_called();print(message,'HTTP 200, zero Ollama calls')
        status,data=ask('Explain why DD+ZNE may be unstable.');assert status==503;(ai.assert_called_once());print('Reasoning during injected outage: HTTP 503, honest unavailable response')
finally:
    server.should_exit=True;thread.join(5);listener.close()
