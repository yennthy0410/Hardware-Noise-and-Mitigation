"""Read only minidump headers, exception fields, and PEB command classification."""
import struct,json,os
from pathlib import Path
def inspect(path):
    b=path.read_bytes()
    def u(fmt,off):return struct.unpack_from('<'+fmt,b,off)
    count,directory=u('II',8)
    streams={}
    for i in range(count):
        typ,size,rva=u('III',directory+12*i);streams[typ]=(size,rva)
    ranges=[]
    if 5 in streams:
        r=streams[5][1];n=u('I',r)[0]
        for i in range(n):
            addr,size,off=u('QII',r+4+i*16);ranges.append((addr,size,off))
    if 9 in streams:
        r=streams[9][1];n,off=u('QQ',r)
        for i in range(n):
            addr,size=u('QQ',r+16+i*16);ranges.append((addr,size,off));off+=size
    def memory(addr,size):
        for base,length,off in ranges:
            if base<=addr and addr+size<=base+length:return b[off+addr-base:off+addr-base+size]
        raise ValueError('memory not captured')
    def ptr(addr):return struct.unpack('<Q',memory(addr,8))[0]
    result={'dump':path.name,'streams':sorted(streams)}
    modules=[]
    if 4 in streams:
        r=streams[4][1]
        for i in range(u('I',r)[0]):
            base,size,checksum,timestamp,name=u('QIIII',r+4+i*108)
            length=u('I',name)[0]
            filename=b[name+4:name+4+length].decode('utf-16le').split('\\')[-1]
            modules.append((base,size,filename))
    if 6 in streams:
        r=streams[6][1];code,flags,record,addr,n=u('IIQQI',r+8)
        params=u('15Q',r+40)
        result.update(exception_code=hex(code),exception_address=hex(addr),parameters=list(params[:n]))
        try:
            context_size,context=u('II',r+160)
            rsp=u('Q',context+152)[0]
            r12=u('Q',context+216)[0]
            result['indirect_call_pointer']=hex(ptr(r12))
            words=struct.unpack('<16Q',memory(rsp,128))
            result['stack_pointer_module_candidates']=[{'slot':i,'module':name,'offset':hex(word-base)}
                for i,word in enumerate(words) for base,size,name in modules if base<=word<base+size]
        except (ValueError,struct.error):pass
    if 3 in streams:
        r=streams[3][1];n=u('I',r)[0];result['thread_count']=n
        teb=u('Q',r+4+16)[0]
        try:
            peb=ptr(teb+0x60);process_params=ptr(peb+0x20)
            length=struct.unpack('<H',memory(process_params+0x70,2))[0]
            cmd=memory(ptr(process_params+0x78),length).decode('utf-16le')
            result['command_kind']='inline' if ' -c ' in cmd else 'script'
            # Do not expose arbitrary inline code, account tokens or credentials.
            result['command_markers']=[x for x in ('mxene.py','qpu_auto_manager.py','research_pipeline.py',
                'unittest','pytest','test_','qiskit','numpy','scipy','faulthandler','import ',
                'run_pipeline','run_campaign_auto_manager','campaign_status.py','-m pip','--live',
                '--resume','diagnose','mitigation_pilot.py','qiskit_aer') if x in cmd]
            if ' -c ' not in cmd and not any(x in cmd.lower() for x in ('token','password','api_key')):
                result['script_command']=cmd
            try:
                envaddr=ptr(process_params+0x80)
                for start,size,off in ranges:
                    if start<=envaddr<start+size:
                        raw=b[off+envaddr-start:off+min(size,envaddr-start+65536)]
                        environment=raw.decode('utf-16le',errors='ignore').split('\0\0')[0]
                        result['codex_environment_keys']=[item.split('=',1)[0] for item in environment.split('\0')
                            if item.startswith('CODEX_')]
                        break
            except (ValueError,struct.error):pass
        except (ValueError,struct.error) as e:result['command_unavailable']=str(e)
    return result
results=[]
for p in sorted((Path(os.environ['LOCALAPPDATA'])/'CrashDumps').glob('python*.dmp')):
    try:results.append(inspect(p))
    except Exception as e:results.append({'dump':p.name,'error':type(e).__name__})
Path('.codex_tmp/minidump_summary.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print(json.dumps(results,indent=2))

