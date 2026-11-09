"""Pure read-only STEP artifact readers shared by the broker and Dashboard."""
import hashlib
import json
from pathlib import Path
import re


def analysis_receipts(directory, n, dataset_sha256):
    """Ordered immutable receipt history for one exact accepted dataset."""
    directory=Path(directory);stem=f'a{n}-{dataset_sha256[:24]}'
    base=directory/(stem+'.json')
    versions=[]
    for path in directory.glob(stem+'-v*.json'):
        match=re.fullmatch(re.escape(stem)+r'-v([0-9]{3,})\.json',path.name)
        if not match:raise ValueError('Malformed versioned analysis receipt name')
        versions.append((int(match[1]),path))
    versions.sort()
    if [v for v,_ in versions]!=list(range(1,len(versions)+1)):
        raise ValueError('Analysis receipt history has a gap or duplicate version')
    previous=base if base.is_file() else None
    for version,path in versions:
        saved=json.loads(path.read_text(encoding='utf-8'))
        expected=({'name':previous.name,'sha256':hashlib.sha256(previous.read_bytes()).hexdigest()}
                  if previous else None)
        if (saved.get('receipt_version')!=version or saved.get('previous_receipt')!=expected
                or saved.get('dataset_sha256')!=dataset_sha256 or saved.get('n')!=n):
            raise ValueError('Versioned analysis receipt provenance/history mismatch')
        previous=path
    return ([base] if base.is_file() else [])+[p for _,p in versions]


def latest_analysis_receipt(directory, n, dataset_sha256):
    history=analysis_receipts(directory,n,dataset_sha256)
    return history[-1] if history else Path(directory)/f'a{n}-{dataset_sha256[:24]}.json'


def next_analysis_receipt(directory, n, dataset_sha256):
    history=analysis_receipts(directory,n,dataset_sha256)
    version=sum('-v' in p.stem for p in history)+1
    previous=history[-1] if history else None
    return (Path(directory)/f'a{n}-{dataset_sha256[:24]}-v{version:03d}.json',
            {'receipt_version':version,'previous_receipt':
             {'name':previous.name,'sha256':hashlib.sha256(previous.read_bytes()).hexdigest()} if previous else None})


def payload_sha256(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def artifact_hashes(directory):
    """Bounded, streaming inventory of canonical analysis outputs, not raw QPU history."""
    directory=Path(directory).resolve();result={}
    for path in sorted(directory.rglob('*')):
        if not path.is_file():continue
        if path.is_symlink() or not path.resolve().is_relative_to(directory):
            raise RuntimeError('Analysis artifact escapes canonical output')
        if len(result)>=512:raise RuntimeError('Analysis artifact inventory exceeds STEP bound')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        result[path.relative_to(directory).as_posix()]=digest.hexdigest()
    if 'analysis_manifest.json' not in result:raise RuntimeError('Canonical analysis manifest missing')
    return result
