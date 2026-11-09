"""Small provider boundary. Secrets stay server-side; cloud is opt-in per request."""
import http.client
import ipaddress
import json
import os
import socket
import ssl
from contextvars import ContextVar
from urllib.parse import urlsplit
import local_ai

CLOUD_DESTINATIONS=ContextVar('mxene_cloud_destinations',default=frozenset())


class ProviderError(RuntimeError):
    pass


def cloud_config():
    try:
        endpoint=urlsplit(os.environ.get('MXENE_CLOUD_BASE_URL',''))
        port=endpoint.port
    except ValueError:raise ProviderError('Cloud base URL is invalid. Configure a valid HTTPS API base URL.') from None
    if (endpoint.scheme!='https' or not endpoint.hostname or endpoint.username or endpoint.password
            or endpoint.query or endpoint.fragment or port not in (None,443)):
        raise ProviderError('Cloud requires a server-configured HTTPS base URL on port 443 without credentials or query parameters.')
    models=[v.strip() for v in os.environ.get('MXENE_CLOUD_MODELS','').split(',') if v.strip()]
    if not models or not os.environ.get('MXENE_CLOUD_API_KEY'):
        raise ProviderError('Cloud is not configured: set its model allowlist and API key in the server environment.')
    return endpoint,models


def configuration():
    from conversational_agent import CONTEXT,OUTPUT
    try:
        _,models=cloud_config();configured=True
    except (ValueError,ProviderError):models=[];configured=False
    return {'default_provider':'local','local_default_model':local_ai.DEFAULT_MODEL,
            'cloud_configured':configured,'cloud_models':models,'context_window':CONTEXT,
            'response_tokens':OUTPUT,'languages':['auto','en','vi','zh-Hant','zh-Hans']}


def cloud_call(payload):
    endpoint,_=cloud_config()
    # Pin the guard to public destinations resolved for the configured host only.
    try:addresses=socket.getaddrinfo(endpoint.hostname,443,type=socket.SOCK_STREAM)
    except OSError:raise ProviderError('Cloud host could not be resolved. Check the server endpoint and network connection.') from None
    destinations=frozenset(item[4] for item in addresses)
    if not destinations or any(not ipaddress.ip_address(addr[0]).is_global for addr in destinations):
        raise ProviderError('Cloud endpoint must resolve exclusively to public addresses.')
    token=CLOUD_DESTINATIONS.set(destinations)
    connection=None
    try:
        connection=http.client.HTTPSConnection(endpoint.hostname,443,timeout=60,context=ssl.create_default_context())
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
        connection.request('POST',endpoint.path.rstrip('/')+'/chat/completions',body=body,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['MXENE_CLOUD_API_KEY']})
        response=connection.getresponse()
        if response.status==429:raise ProviderError('Cloud rate limit reached. Wait before retrying; no automatic retry occurred.')
        if response.status in (401,403):raise ProviderError('Cloud authentication denied. Check the server-side API key and model permissions.')
        if response.status!=200:raise ProviderError('Cloud API returned HTTP '+str(response.status)+'. No automatic retry occurred.')
        raw=response.read(1_048_577)
        if len(raw)>1_048_576:raise ProviderError('Cloud response exceeded the safe size limit.')
        return json.loads(raw)
    except ProviderError:raise
    except Exception:
        # Do not surface URL, headers, response body or exception text containing secrets.
        raise ProviderError('Cloud connection failed, timed out, or returned malformed JSON.') from None
    finally:
        if connection:connection.close()
        CLOUD_DESTINATIONS.reset(token)


class Provider:
    def __init__(self,request):
        self.request=request;self.name=request.provider
        self.model='';self.fallback_used=False

    def connect(self):
        if self.name=='cloud':
            if not self.request.cloud_consent:raise PermissionError('Cloud data-sharing authorization is required for prompts, history and retrieved project evidence.')
            _,models=cloud_config()
            self.model=self.request.model if self.request.provider=='cloud' and self.request.model else models[0]
            if self.model not in models:raise ProviderError('Cloud model is not in the server-configured allowlist.')
        else:
            try:self.model=local_ai.resolve_model(self.request.model)
            except Exception:
                if not self._fallback():raise ProviderError('Ollama is unavailable or the selected model is not installed. Start Ollama and check the model list.') from None
        return self

    def _fallback(self):
        if self.name!='local' or not self.request.cloud_fallback or not self.request.cloud_consent:return False
        self.name='cloud';self.fallback_used=True;self.connect();return True

    def complete(self,messages,schema,context,output):
        if self.name=='local':
            try:
                response=local_ai.call('/api/chat',{'model':self.model,'messages':messages,'format':schema,
                    'stream':False,'think':False,'keep_alive':'5m','options':{'num_ctx':context,'num_predict':output,'temperature':0}})
                if response.get('done_reason')=='length':raise ProviderError('Model response exceeded the token limit. Increase the response-token setting.')
                return response['message']['content']
            except ProviderError:raise
            except Exception:
                if not self._fallback():raise ProviderError('Ollama request failed or timed out. No cloud fallback was authorized or performed.') from None
        response=cloud_call({'model':self.model,'messages':messages,'stream':False,'temperature':0,
            'max_tokens':output,'response_format':{'type':'json_object'}})
        try:
            choice=response['choices'][0]
            if choice.get('finish_reason')=='length':raise ProviderError('Cloud response exceeded the token limit. Increase the response-token setting.')
            return choice['message']['content']
        except (KeyError,IndexError,TypeError):raise ProviderError('Cloud returned an unsupported response format.') from None
