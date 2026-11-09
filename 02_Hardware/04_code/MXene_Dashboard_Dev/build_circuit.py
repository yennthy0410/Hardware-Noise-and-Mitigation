"""Explicit OFFLINE QPY visualization; never invoked by the web server."""
import os,sys,json,hashlib,html
from pathlib import Path
HOME=Path(__file__).resolve().parent
SOURCE=HOME.parent.parent/'01_vqe/06_DD_TREX_ZNE_mitigation/output/03_IBM_execution_records/locked_inputs'
sys.dont_write_bytecode=True
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
def guard(event,args):
    if event in ('socket.connect','subprocess.Popen','os.system'):raise PermissionError('Offline circuit exporter')
    if event=='import' and args[0].startswith(('qiskit_ibm_runtime','qiskit_aer')):raise PermissionError('No Runtime/simulation')
    if event=='open' and not isinstance(args[0],int):
        mode,flags=args[1:];writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
        if writing and str(args[0]).lower()!=os.devnull.lower() and not Path(args[0]).resolve().is_relative_to(HOME):raise PermissionError('Dashboard-only writes')
sys.addaudithook(guard)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
lock=json.loads((SOURCE/'lock.json').read_text())
for name,digest in lock['hashes'].items():
    if sha(SOURCE/name)!=digest:raise ValueError('Frozen hash mismatch: '+name)
from qiskit import qpy
with (SOURCE/'circuit.qpy').open('rb') as f:circuits=qpy.load(f)
if len(circuits)!=1:raise ValueError('Ambiguous circuit bundle')
c=circuits[0];ops=[]
for item in c.data:
    ops.append(dict(gate=item.operation.name,qubits=[c.find_bit(q).index for q in item.qubits],clbits=[c.find_bit(q).index for q in item.clbits],params=[str(p) for p in item.operation.params]))
active=sorted({q for op in ops for q in op['qubits']});width=max(900,140+len(ops)*84);height=150+len(active)*68
parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Actual frozen run_02 ISA circuit"><rect width="100%" height="100%" fill="white"/><text x="15" y="25" font-family="sans-serif" font-size="18">Frozen run_02 • actual QPY instruction order • idle wires omitted</text>']
for row,q in enumerate(active):
    y=80+row*68;parts.append(f'<text x="10" y="{y+5}" font-family="monospace" font-size="16">q[{q}]</text><path d="M 95 {y} H {width-20}" stroke="#444"/>')
for index,op in enumerate(ops):
    x=120+index*84;ys=[80+active.index(q)*68 for q in op['qubits']];label=html.escape(op['gate']);tip=html.escape(json.dumps(op))
    if not ys:continue
    if len(ys)>1:parts.append(f'<path d="M{x} {min(ys)} V{max(ys)}" stroke="#333"/>')
    for y in ys:parts.append(f'<g><title>{tip}</title><rect x="{x-30}" y="{y-18}" width="60" height="36" fill="white" stroke="#222"/><text x="{x}" y="{y+4}" font-family="monospace" font-size="12" text-anchor="middle">{label}</text></g>')
    parts.append(f'<text x="{x}" y="{height-35}" font-size="10" text-anchor="middle">{index}</text>')
parts.append('</svg>');svg=HOME/'state/frozen_circuit.svg';svg.write_text(''.join(parts),encoding='utf-8')
data=dict(source='locked_inputs/circuit.qpy',hashes=lock['hashes'],svg_sha256=sha(svg),depth=c.depth(),qubits=c.num_qubits,clbits=c.num_clbits,active_qubits=active,idle_wires_omitted=c.num_qubits-len(active),two_qubit_gates=sum(len(op['qubits'])==2 and op['gate']!='barrier' for op in ops),operations=ops,measurements=sum(op['gate']=='measure' for op in ops),note='Every QPY instruction retained in order. Shared multi-qubit gates connected; parameters and classical operands are in the exact instruction table. No simulation or hardware execution.')
(HOME/'state/frozen_circuit.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
print(json.dumps({k:data[k] for k in ('depth','qubits','two_qubit_gates','measurements','idle_wires_omitted')}))
