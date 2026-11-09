from pathlib import Path
import struct,json
p=Path('.venv/Lib/site-packages/qiskit_aer/backends/libopenblas.dll')
b=p.read_bytes()
def u(fmt,off):return struct.unpack_from('<'+fmt,b,off)
pe=u('I',0x3c)[0];sections=u('H',pe+6)[0];opt_size=u('H',pe+20)[0]
opt=pe+24;table=opt+opt_size
ranges=[]
for i in range(sections):
    r=table+40*i;vs,va,size,off=u('IIII',r+8);ranges.append((va,max(vs,size),off))
def offset(rva):
    for base,size,off in ranges:
        if base<=rva<base+size:return off+rva-base
    raise ValueError(hex(rva))
export_rva=u('I',opt+112)[0];e=offset(export_rva)
base,nf,nn,functions,names,ordinals=u('IIIIII',e+16)
symbols=[]
for i in range(nn):
    nameoff=offset(u('I',offset(names)+4*i)[0]);end=b.index(b'\0',nameoff)
    name=b[nameoff:end].decode('ascii')
    ordinal=u('H',offset(ordinals)+2*i)[0]
    rva=u('I',offset(functions)+4*ordinal)[0]
    symbols.append((rva,name))
for target in (0x27747b,0x2770b0,0x277d0e):
    nearest=max((x for x in symbols if x[0]<=target),default=None)
    print(hex(target),nearest, 'delta',hex(target-nearest[0]) if nearest else None)
    at=offset(target);print('preceding instruction bytes',b[at-16:at+8].hex())
symoff,symcount=u('II',pe+12);strings=symoff+18*symcount
i=0;coff=[]
while i<symcount:
    off=symoff+18*i;raw=b[off:off+8]
    if raw[:4]==b'\0'*4:
        start=strings+u('I',off+4)[0];name=b[start:b.index(b'\0',start)].decode('ascii',errors='replace')
    else:name=raw.rstrip(b'\0').decode('ascii',errors='replace')
    value,section,typ,storage,aux=u('IhHBB',off+8)
    if 1<=section<=len(ranges):coff.append((ranges[section-1][0]+value,name))
    i+=1+aux
for target in (0x27747b,0x2770b0,0x277d0e):
    match=max((x for x in coff if x[0]<=target),default=None)
    print('COFF',hex(target),match,'delta',hex(target-match[0]))
