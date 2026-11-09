from pathlib import Path
import os, sqlite3
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from typing import Literal
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from reader import Reader

HERE=Path(__file__).resolve().parent
reader=Reader()
app=FastAPI(title='MXene Observatory',docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','mxene-hardware-noise-and-mitigation.onrender.com'])

@app.get('/api/data-revision')
def local_data_revision():
    from reader import data_revision
    return {'revision':data_revision(),'provider_queried':False}

@app.get('/api/dashboard-data')
def synchronized_dashboard_data(fresh:bool=False):
    try:return reader.bundle(force=fresh)
    except (OSError,ValueError,KeyError,RuntimeError,sqlite3.Error) as exc:
        raise HTTPException(503,'Local synchronization unavailable: '+str(exc))
@app.middleware('http')
async def headers(request,call_next):
    from local_ai import CALL_AUDIT
    audit={'ollama_calls':0}
    token=CALL_AUDIT.set(audit)
    try:response=await call_next(request)
    finally:CALL_AUDIT.reset(token)
    response.headers['X-MXene-Chat-Protocol']='2'
    response.headers['X-MXene-Ollama-Calls']=str(audit['ollama_calls'])
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    return response
@app.get('/')
def index(): return FileResponse(HERE/'static/index.html')
@app.get('/api/health')
def health(): return {'mode':'local read-only','ibm_network':False,'ai_execution':False,'ai_execution_meaning':'No LLM-controlled execution','auto_control':'Explicit operator confirmation via separate AUTO broker; STEP disabled','local_ai_interpretation':'Explicit consent only; fixed loopback Ollama','poll_seconds':120}
@app.get('/api/status')
def status(): return reader.status()
@app.get('/api/artifacts/{key}')
def artifact(key:str,download:bool=False):
    try: path=reader.artifact(key)
    except (KeyError,ValueError,OSError): raise HTTPException(404,'Unavailable or changed artifact')
    return FileResponse(path,filename=path.name if download else None)
@app.get('/api/tables/{key}')
def table(key:str,page:int=Query(0,ge=0,le=1000)):
    try: return reader.table(key,page)
    except (KeyError,ValueError,OSError): raise HTTPException(404,'Unavailable table')
@app.get('/api/comparison')
def comparison():
    from campaign_comparison import published_comparison
    try:return published_comparison(HERE.parents[2])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {'state':'MISSING_OR_INVALID_ARTIFACT','reason':str(exc),'figures':[],'tables':[]}

@app.get('/api/comparison/artifacts/{name}')
def comparison_artifact(name:str,download:bool=False):
    from campaign_comparison import published_artifact
    try:path=published_artifact(HERE.parents[2],name)
    except (ValueError,OSError,KeyError,TypeError):raise HTTPException(404,'Canonical comparison artifact unavailable')
    return FileResponse(path,filename=path.name if download else None)

app.mount('/static',StaticFiles(directory=HERE/'static'),name='static')

@app.get('/api/charts/{key}/{kind}')
def charts(key:str,kind:str):
    from science_ui import chart
    try:return chart(reader,key,kind)
    except (ValueError,OSError,KeyError):raise HTTPException(404,'Validated chart source unavailable')
@app.get('/api/circuit')
def frozen_circuit():
    from science_ui import circuit
    try:return circuit()
    except (ValueError,OSError,KeyError):raise HTTPException(404,'Frozen circuit or provenance unavailable')

@app.get('/api/interpretations/{key}')
def figure_interpretation(key:str):
    from interpretations import interpretation
    try:return interpretation(reader,key)
    except (ValueError,OSError,KeyError,OverflowError):raise HTTPException(404,'Analysis Not Available: source missing, changed or invalid')
@app.get('/api/design')
def campaign_design():
    from science_ui import design
    return design()
@app.get('/api/circuit.svg')
def frozen_svg():
    data=frozen_circuit()
    if not data['available']:raise HTTPException(404,'Offline circuit export unavailable')
    return FileResponse(HERE/'state/frozen_circuit.svg',media_type='image/svg+xml')
class Message(BaseModel):
    role:Literal['user','assistant']
    content:str=Field(max_length=32000)
class AnalysisRequest(BaseModel):
    consent:bool=False
    model:str=Field(default='',max_length=100)
    message:str=Field(default='',max_length=32000)
    history:list[Message]=Field(default_factory=list,max_length=40)
    provider:Literal['local','cloud']='local'
    conversational:bool=False  # Old clients retain deterministic read commands.
    language:Literal['auto','en','vi','zh-Hant','zh-Hans']='auto'
    cloud_consent:bool=False
    cloud_fallback:bool=False
    context_window:int|None=Field(default=None,ge=8192,le=131072)
    response_tokens:int|None=Field(default=None,ge=256,le=4096)
    memory:str=Field(default='',max_length=4000)
    tab:str=Field(default='Overview',max_length=40)
    n:Literal[5,10,15,20,25,30]|None=None
    job_id:str|None=Field(default=None,max_length=100)
    treatment:str|None=Field(default=None,max_length=50)
    artifact:str|None=Field(default=None,max_length=32)
def authorize_ai(request,body):
    if not body.consent or request.headers.get('X-MXene-AI')!='local-only':raise HTTPException(403,'Explicit local-only consent required')
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):raise HTTPException(403,'Same-origin requests only')

def authorize_tools(request):
    if request.headers.get('X-MXene-AI')!='local-only':raise HTTPException(403,'Dashboard request header required')
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):raise HTTPException(403,'Same-origin requests only')

def authorize_operator(request):
    # Render is public even when a proxy supplies a loopback Host header.
    if os.environ.get('RENDER') or request.url.hostname not in ('127.0.0.1','localhost'):
        raise HTTPException(403,'Operator controls are available only on the local Dashboard')
    authorize_tools(request)

from research import BridgeRequest
@app.post('/api/research/tool')
def research_tool(body:BridgeRequest,request:Request):
    authorize_tools(request)
    from research import bridge
    try:return bridge(reader,body)
    except ValueError as exc:raise HTTPException(400,str(exc))
    except Exception:raise HTTPException(503,'Verified local manager evidence is unavailable. No action was performed.')
@app.post('/api/ai/connect')
def ai_connect(body:AnalysisRequest,request:Request):
    authorize_ai(request,body)
    from local_ai import models
    try:return {'models':models(),'endpoint':'127.0.0.1:11434','cloud_fallback':False}
    except Exception:raise HTTPException(503,'Local Ollama unavailable. Start it separately and check installed models; no fallback was used.')
def run_assistant(body:AnalysisRequest,request:Request):
    authorize_tools(request)
    control=route_control(body.message)
    if control:
        if control.operation in READ_CONTROL:
            try:return read_control(control)
            except ValueError as exc:raise HTTPException(503,str(exc))
        raise HTTPException(400,'AI Chat is read-only. Use the existing Auto Manager controls for an explicitly authorized operation; no action was performed.')
    if body.conversational or body.consent or body.provider=='cloud':
        if body.provider=='local':authorize_ai(request,body)
        elif not body.cloud_consent:raise HTTPException(403,'Explicit cloud data-sharing consent required')
        from conversational_agent import run
        from ai_providers import ProviderError
        try:return run(reader,body,AGENT_PROGRESS.get())
        except PermissionError as exc:raise HTTPException(403,str(exc))
        except ValueError as exc:raise HTTPException(400,str(exc))
        except (ProviderError,RuntimeError) as exc:raise HTTPException(503,str(exc))
        except Exception:raise HTTPException(503,'AI analysis was interrupted or unavailable. Your saved history is intact; no IBM action was performed.')
    from research import route_command,bridge
    if len(body.message)>4000:raise HTTPException(403,'Explicit local AI consent required for long conversational prompts.')
    try:
        command=route_command(body.message,body.n)
        if command is not None:return bridge(reader,command,history=body.history)
    except ValueError as exc:raise HTTPException(400,str(exc))
    except Exception:raise HTTPException(503,'Python tool evidence is unavailable. No IBM action was performed.')
    authorize_ai(request,body)
    raise HTTPException(403,'Explicit local AI consent required for conversational understanding. Existing short Python read commands remain available.')

# Quick Assistant is deliberately stateless. Workspace history is server-owned.
@app.post('/api/ai/analyze')
def ai_analyze(body:AnalysisRequest,request:Request):
    return run_assistant(body.model_copy(update={'history':[],'memory':''}),request)

from conversations import store,safe_text
from contextvars import ContextVar
AGENT_PROGRESS=ContextVar('agent_progress',default=lambda value:None)
agent_status={}

@app.get('/api/ai/config')
def ai_configuration(request:Request):
    authorize_tools(request)
    from ai_providers import configuration
    return configuration()

@app.post('/api/ai/provider-check')
def provider_check(body:AnalysisRequest,request:Request):
    authorize_tools(request)
    from ai_providers import Provider,ProviderError
    try:
        if body.provider=='local':
            authorize_ai(request,body)
            from local_ai import models
            return {'provider':'local','models':models(),'available':True}
        if not body.cloud_consent:raise HTTPException(403,'Explicit cloud data-sharing consent required')
        provider=Provider(body).connect()
        provider.complete([{'role':'user','content':'Return JSON: {"ok":true}'}],{},8192,256)
        from ai_providers import configuration
        return {'provider':'cloud','models':configuration()['cloud_models'],'available':True}
    except HTTPException:raise
    except PermissionError as exc:raise HTTPException(403,str(exc))
    except ProviderError as exc:raise HTTPException(503,str(exc))
    except Exception:raise HTTPException(503,'Provider unavailable. Check server configuration and installed models.')

@app.get('/api/conversations/{key}/progress')
def conversation_progress(key:str,request:Request):
    authorize_tools(request)
    return conversation_call(lambda:(store.get(key),agent_status.get(key,{'state':'idle','tool':None}))[1])

@app.post('/api/conversations/{key}/clear')
def conversation_clear(key:str,request:Request):
    authorize_tools(request)
    result=conversation_call(lambda:store.clear(key))
    agent_status.pop(key,None)
    return result
from worker_supervisor import ControlRequest,supervisor,route_control,suggested_prompts,review_recovery,command_capabilities,operational_result,observation_freshness

READ_CONTROL=('help','status','review','campaign_status','batch_status','job_status','quota_status')

def read_control(command):
    snapshot=reader.status()
    if command.operation=='help':return command_capabilities(supervisor,snapshot)
    if command.operation=='status':
        from datetime import datetime,timezone
        result=supervisor.status()
        result['answer']+=' Process/lock check: '+datetime.now(timezone.utc).isoformat()+'.'
    elif command.operation=='review':result=review_recovery(reader,command.n)
    elif command.operation=='batch_status':
        rows=[{'batch':j.get('batch_id') or 'UNKNOWN','n':j.get('target_n'),
               'job':j.get('job_id') or 'UNKNOWN','recorded_job_status':j.get('status') or 'UNKNOWN',
               'provider_batch_state':'UNAVAILABLE','provider_observed_at':j.get('observed_at') or 'N/A',
               'freshness':observation_freshness(j.get('observed_at'))} for j in snapshot.get('execution_jobs',[])]
        result={'answer':'Registered Batch membership and locally recorded job observations only. Provider Batch state/TTL were not queried; job state does not establish Batch state.',
                'rows':rows,'engine':'Python tool bridge'}
    else:
        from research import bridge,BridgeRequest
        op={'campaign_status':'get_campaign_status','job_status':'get_ibm_job_status','quota_status':'check_ibm_quota'}[command.operation]
        result=bridge(reader,BridgeRequest(operation=op))
    return operational_result(result,snapshot)

@app.get('/api/workspace/suggestions')
def workspace_suggestions(request:Request):
    authorize_tools(request)
    return command_capabilities(supervisor,reader.status())

@app.get('/api/campaign-progress')
def campaign_progress(request:Request):
    authorize_tools(request)
    try:return reader.bundle()['progress']
    except (OSError,ValueError,KeyError,RuntimeError,sqlite3.Error) as exc:
        raise HTTPException(503,'Local campaign evidence unavailable: '+str(exc))

@app.post('/api/conversations/{key}/manual')
def manual_workspace_request(key:str,body:ControlRequest,request:Request):
    authorize_operator(request)
    if body.operation not in ('manual','analyze','export'):
        raise HTTPException(409,'Manual workspace permits status/collection and n-level Analyze/Export only')
    if body.operation!='manual' and body.manual_scope is not None:
        raise HTTPException(409,'Analyze and Export are complete n-level operations, not Job/PUB operations')
    try:
        with store.turn(key):
            store.get(key)
            result=supervisor.prepare(key,body)
            store.append(key,'Manual '+body.operation,result,{'n':body.n})
            return result
    except ValueError as exc:raise HTTPException(400,str(exc))

@app.post('/api/conversations/{key}/operator')
def existing_operator_request(key:str,body:AnalysisRequest,request:Request):
    """Existing exact command preparation, isolated from conversational input/LLMs."""
    authorize_operator(request)
    control=route_control(body.message)
    if control is None:raise HTTPException(400,'Select an existing operator control.')
    try:
        with store.turn(key):
            store.get(key)
            response=read_control(control) if control.operation in READ_CONTROL else operational_result(supervisor.prepare(key,control),reader.status())
            store.append(key,'Operator control: '+body.message,response,{})
            return response
    except ValueError as exc:raise HTTPException(400,str(exc))

@app.get('/api/manual/result')
def manual_workspace_result(request:Request):
    authorize_tools(request)
    return supervisor.status()
class RenameConversation(BaseModel):
    title:str=Field(min_length=1,max_length=120)


class IndependentManualRequest(BaseModel):
    model_config={'extra':'forbid'}
    action:Literal['config','parse','preview','preflight','confirm','result']
    request:dict=Field(default_factory=dict)
    request_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')
    scope_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    command:str|None=Field(default=None,max_length=1000)
    approved:bool=Field(default=False,strict=True)
    consent:bool=False  # Shared chat transport flag has no execution authority.


@app.post('/api/manual-execution')
def independent_manual(body:IndependentManualRequest,request:Request):
    authorize_operator(request)
    from broker_client import call
    try:return call('/manual-operation',body.model_dump(exclude={'consent'},exclude_none=True))
    except Exception:raise HTTPException(503,'Manual execution service unavailable. Start the broker transport; AUTO does not need to run. Inspect the existing request before retrying a confirmation.')

def conversation_call(action):
    try:return action()
    except ValueError as exc:raise HTTPException(400,str(exc))
    except sqlite3.Error:raise HTTPException(503,'Conversation storage is busy or unavailable. Retry when the current operation finishes.')

@app.get('/api/conversations')
def conversations_list(request:Request):
    authorize_tools(request)
    return conversation_call(store.listing)

@app.post('/api/conversations')
def conversations_create(request:Request):
    authorize_tools(request)
    return conversation_call(store.create)

@app.get('/api/conversations/{key}')
def conversations_get(key:str,request:Request):
    authorize_tools(request)
    return conversation_call(lambda:store.get(key))

@app.patch('/api/conversations/{key}')
def conversations_rename(key:str,body:RenameConversation,request:Request):
    authorize_tools(request)
    return conversation_call(lambda:store.rename(key,body.title))

@app.delete('/api/conversations/{key}')
def conversations_delete(key:str,request:Request):
    authorize_tools(request)
    result=conversation_call(lambda:store.delete(key))
    agent_status.pop(key,None)
    return result

@app.post('/api/conversations/{key}/messages')
def conversations_message(key:str,body:AnalysisRequest,request:Request):
    authorize_tools(request)
    try:
        safe_text(body.message)
        if not body.message.strip():raise ValueError('Enter a research question.')
        with store.turn(key):
            conversation=store.get(key)
            if len(conversation['turns'])>=100:raise ValueError('Start a new conversation; this chat has reached 100 turns.')
            from conversational_agent import history_context
            memory,messages=history_context(conversation['turns'])
            store.remember(key,memory)
            history=[Message(**m) for m in messages]
            context=conversation['turns'][-1]['context'] if conversation['turns'] else {}
            scope=context.get('research_scope')
            if scope:
                from research import LABELS,ToolRequest
                verified=ToolRequest.model_validate(scope)
                history.append(Message(role='user',content='Retained research scope: n='+','.join(map(str,verified.levels))+'; '+verified.metric+'; '+(' and '.join(LABELS[t] for t in verified.treatments) if verified.treatments else 'all treatments')))
            current=body.model_copy(update={'history':history,'memory':memory,'n':body.n if body.n is not None else context.get('n')})
            control=route_control(body.message)
            if control and control.operation not in READ_CONTROL:
                raise ValueError('AI Chat is read-only. No manager action was prepared or executed. Use the existing dedicated operator controls.')
            agent_status[key]={'state':'starting','tool':None}
            progress_token=AGENT_PROGRESS.set(lambda value:agent_status.__setitem__(key,value))
            try:
                response=run_assistant(current,request)
                agent_status[key]={'state':'complete','tool':None}
            except Exception:
                agent_status[key]={'state':'failed','tool':None}
                raise
            finally:AGENT_PROGRESS.reset(progress_token)
            store.append(key,body.message,response,{'n':current.n,'research_scope':response.get('research_scope') or scope})
            return response
    except ValueError as exc:raise HTTPException(400,str(exc))
    except sqlite3.Error:raise HTTPException(503,'Conversation could not be saved. Your earlier history is intact; keep the current draft and retry.')

class ControlConfirmation(BaseModel):
    model_config={'extra':'forbid'}
    approved:Literal[True]
    consent:bool=False  # Shared transport privacy flag is NOT LIVE authorization.

@app.post('/api/conversations/{key}/controls/{request_id}/confirm')
def confirm_worker_control(key:str,request_id:str,body:ControlConfirmation,request:Request):
    authorize_operator(request)
    try:
        with store.turn(key):
            store.get(key)
            response=operational_result(supervisor.confirm(key,request_id,body.approved),reader.status())
            store.append(key,'Explicit operator confirmation',response,{})
            return response
    except ValueError as exc:raise HTTPException(400,str(exc))

@app.get('/api/execution-records')
def execution_records():
    try:return reader.bundle()['records']
    except (OSError,ValueError,KeyError,RuntimeError,sqlite3.Error) as exc:
        raise HTTPException(503,'Local synchronization unavailable: '+str(exc))


class SinglePubRequest(BaseModel):
    model_config={'extra':'forbid','strict':True}
    purpose:Literal['TEST','RECOVERY']='TEST'
    n:Literal[5,10,15,20,25,30]=5
    treatment:Literal['M0_Baseline','M1_DD','M2_TREX','M3_ZNE','M4_DD_TREX','M5_DD_ZNE','M6_TREX_ZNE','M7_ALL']='M0_Baseline'
    slot:int|None=Field(default=None,ge=1,le=30)
    account:str|None=Field(default=None,pattern=r'^lab[A-IK]$')
    scope_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    action:Literal['preview','preflight','submit','monitor','collect']='preview'
    consent:bool=False

@app.post('/api/single-pub/preview')
def single_pub_preview(body:SinglePubRequest,request:Request):
    authorize_operator(request)
    if body.action!='preview':raise HTTPException(400,'Preview endpoint cannot execute operations')
    from broker_client import call
    try:return call('/single-pub-preview',body.model_dump(include={'n','purpose','treatment','slot','account'}))
    except Exception:raise HTTPException(503,'Canonical preview unavailable. No operation executed; check the broker service.')

@app.post('/api/conversations/{key}/single-pub')
def single_pub_request(key:str,body:SinglePubRequest,request:Request):
    authorize_operator(request)
    if body.action not in ('monitor','collect') or body.purpose!='RECOVERY':
        raise HTTPException(409,'Single-PUB submission/preflight authorization is not enabled. TEST requires an approved isolated quota/storage policy; RECOVERY requires a fresh provider/quota authorization contract. No job submitted.')
    from worker_supervisor import ControlRequest,SinglePubScope
    try:
        scope=SinglePubScope.model_validate(body.model_dump(include={'purpose','treatment','slot','account','scope_id','action'}))
        with store.turn(key):
            store.get(key)
            response=supervisor.prepare(key,ControlRequest(operation='single_pub',n=body.n,single_pub=scope))
            store.append(key,'Single-PUB '+body.action,response,{'n':body.n})
            return response
    except ValueError as exc:raise HTTPException(400,str(exc))
