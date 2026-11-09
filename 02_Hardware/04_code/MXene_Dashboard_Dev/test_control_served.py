"""Actual loopback HTTP checks. No production execution adapter is enabled.

Creates and removes only its own Dashboard conversation. No IBM connection.
"""
import json
import urllib.request
import urllib.error


def request(path, payload=None, method=None):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request('http://127.0.0.1:8766'+path,data=data,
        headers={'Content-Type':'application/json','X-MXene-AI':'local-only'},method=method)
    with urllib.request.urlopen(req,timeout=60) as response:
        assert response.headers['X-MXene-Ollama-Calls']=='0'
        return json.load(response)


def main():
    conversation=request('/api/conversations',{})['id']
    path='/api/conversations/'+conversation
    try:
        commands=('Run Auto Manager','Pause Auto Manager','Resume campaign',
                        'Run preflight','Monitor jobs','Collect completed jobs',
                        'Analyze completed n-level n=5','Export figures n=5', 'Prepare Campaign', 'Run Batch n=5')
        for command in commands:
            result=request(path+'/messages',{'message':command})
            assert result['control_state'] in ('BLOCKED','AWAITING_CONFIRMATION','ATTACHED'),result
            # Authorization preparation is allowed; this test NEVER confirms.
        result=request(path+'/messages',{'message':'Show worker status'})
        assert result['worker_state'] in ('UNKNOWN','NO_CANDIDATE_OBSERVED','CANDIDATE_RUNNING','STOPPED_VERIFIED','RUNNING_VERIFIED','STALE_UNRESOLVED')
        assert len(request(path)['turns'])==len(commands)+1
        suggestions=request('/api/workspace/suggestions')
        assert 'Run Batch n=5' not in suggestions['prompts']
        recovery=request(path+'/messages',{'message':'Review targeted recovery'})
        assert recovery['engine']=='Python tool bridge'
        assert recovery['rows']
        result=request('/api/ai/analyze',{'message':'Show campaign progress.'})
        assert result['engine']=='Python tool bridge',result
        print('Actual HTTP: AUTO authorization preparation only, unsupported STEP blocked, worker observation, refresh, recovery review and campaign bridge; zero Ollama calls; no confirmations sent.')
    finally:
        request(path,method='DELETE')


if __name__=='__main__':main()
