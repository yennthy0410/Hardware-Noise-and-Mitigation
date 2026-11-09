"""Real HTTP authorization preparation ONLY. Never confirms a production action."""
import json
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

HERE=Path(__file__).resolve().parent
def call(path,body=None,method=None):
    request=Request('http://127.0.0.1:8766'+path,data=None if body is None else json.dumps(body).encode(),
        method=method,headers={'Content-Type':'application/json','X-MXene-AI':'local-only'})
    with urlopen(request,timeout=45) as response:
        assert response.headers['X-MXene-Ollama-Calls']=='0'
        return json.load(response)

def main():
    files=[HERE/'state'/name for name in ('auto_launch.json','auto_worker.json','broker_dispatch.sqlite3')]
    before={p:p.read_bytes() if p.exists() else None for p in files}
    key=call('/api/conversations',{})['id'];path='/api/conversations/'+key
    try:
        result=call(path+'/messages',{'message':'Show Auto Manager status'})
        assert result['worker_state']=='STOPPED_VERIFIED',result
        assert result['ownership_verified'] and result['control_available']
        request=call(path+'/messages',{'message':'Start Auto Manager'})
        assert request['control_state']=='AWAITING_CONFIRMATION',request
        assert '150' in request['answer'] and '40' in request['answer']
        assert call(path)['turns'][-1]['response']['control_request']==request['control_request']
        try:call('/api/ai/analyze',{'message':'Start Auto Manager'})
        except HTTPError as error:assert error.code==400
        else:raise AssertionError('Quick Assistant permitted control')
        # No call to /confirm: actual hardware campaigns must never run in tests.
        assert before=={p:p.read_bytes() if p.exists() else None for p in files}
        print('PASS actual HTTP: broker-backed STOPPED_VERIFIED, campaign authorization preparation, refresh, Quick read-only, zero Ollama calls; no dispatch or worker created.')
    finally:call(path,method='DELETE')

if __name__=='__main__':main()
