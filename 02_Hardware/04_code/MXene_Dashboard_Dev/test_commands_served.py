"""Actual localhost HTTP acceptance. Read-only commands; never confirm execution."""
import json
import urllib.request

BASE='http://127.0.0.1:8766'
def request(path,body=None,method=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','X-MXene-AI':'local-only'},method=method)
    with urllib.request.urlopen(req,timeout=90) as r:
        return json.loads(r.read()),r.headers

def main():
    chat,_=request('/api/conversations',{})
    key=chat['id']
    try:
        for message in ('Campaign Status','Worker Status','Batch Status','IBM Job Status','IBM Quota',
                        'Recovery Status','Show supported STEP commands','Show supported commands',
                        'Monitor / Collect','Prepare','Analyze','Export Figures','Analyze n-level n=5','Export n=5 figures'):
            data,headers=request(f'/api/conversations/{key}/messages',{'message':message})
            assert headers['X-MXene-Ollama-Calls']=='0',message
            assert headers['Cache-Control']=='no-store'
            assert 'median SSE' not in data['answer'],message
            assert 'Local snapshot checked:' in data['answer'],message
            if 'supported' in message:
                assert any(r['command']=='Preflight' for r in data['rows'])
            if message in ('Monitor / Collect','Analyze','Export Figures'):
                assert data['control_state']=='BLOCKED',message
                assert 'control_request' not in data
            if message in ('Prepare','Analyze n-level n=5','Export n=5 figures'):
                assert data['control_state']=='AWAITING_CONFIRMATION',message
                assert data.get('control_request')
            print('PASS:',message,flush=True)
        data,_=request('/api/workspace/suggestions')
        assert len(data['commands'])==15
        assert {r['group'] for r in data['commands']}=={'Status & Monitoring','AUTO Control','STEP-by-STEP'}
        assert all('Run Batch' not in r['command'] for r in data['commands'])
        loaded,_=request('/api/conversations/'+key)
        assert len(loaded['turns'])==14
        print('PASS: persisted HTTP history and grouped capabilities; no dispatch confirmed.')
    finally:
        request('/api/conversations/'+key,{},'DELETE')

if __name__=='__main__':main()
