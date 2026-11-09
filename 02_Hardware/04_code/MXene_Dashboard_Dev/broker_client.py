"""Narrow loopback channel. Never exposes broker credentials to the browser."""
import json, os, base64
from contextvars import ContextVar
from pathlib import Path
from urllib.request import Request, urlopen, ProxyHandler, build_opener

BROKER_ACCESS=ContextVar('mxene_auto_broker',default=False)
HERE=Path(__file__).resolve().parent

def secret_bytes(data, *, decrypt=False):
    """Windows user-bound DPAPI; no machine-wide or plaintext secret fallback."""
    if os.name!='nt':raise RuntimeError('AUTO broker requires Windows DPAPI secret storage')
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.c_void_p)]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.c_void_p));output=Blob()
    crypt=ctypes.windll.crypt32
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
        raise RuntimeError('Windows secret protection failed')
    try:return ctypes.string_at(output.data,output.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(output.data)

def save_token(path,token):
    path.write_text('dpapi:'+base64.b64encode(secret_bytes(token.encode('ascii'))).decode('ascii'),encoding='ascii')

def read_token(path):
    text=path.read_text(encoding='ascii').strip()
    if not text.startswith('dpapi:'):raise ValueError('Restart broker to initialize protected secret storage')
    return secret_bytes(base64.b64decode(text[6:],validate=True),decrypt=True).decode('ascii')

def call(path,payload=None):
    if path not in ('/health','/status','/dispatch','/single-pub-preview','/collect-preview','/manual-preview','/manual-operation'):raise ValueError('Unsupported broker endpoint')
    token=read_token(HERE/'state/auto_broker.token')
    if len(token)!=64:raise ValueError('Broker credential unavailable')
    req=Request('http://127.0.0.1:8767'+path,
        data=None if payload is None else json.dumps(payload,allow_nan=False).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    scope=BROKER_ACCESS.set(True)
    try:
        # Do not send localhost credentials to environment-configured proxies.
        from urllib.error import HTTPError
        try:response=build_opener(ProxyHandler({})).open(req,timeout=2 if path=='/health' else 90 if path=='/manual-operation' else 30)
        except HTTPError as error:
            if path!='/manual-operation':raise
            response=error
        with response:
            data=response.read(128001)
            if len(data)>128000:raise ValueError('Oversized broker response')
            return json.loads(data)
    finally:BROKER_ACCESS.reset(scope)
