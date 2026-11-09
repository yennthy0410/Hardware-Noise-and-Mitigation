"""Shared publication contract and read-only inventory. No scientific calculations."""
import hashlib
import json
import struct
from pathlib import Path

CONTRACT_VERSION='mitigation-publication-v3'
PAPER_FIGURES=('nyquist.png','bode_magnitude.png','bode_phase.png','residual_real.png','residual_imag.png','residual_real_normalized.png','residual_imag_normalized.png')
PUBLICATION_FIGURES=tuple('publication_'+name for name in PAPER_FIGURES)
WIDE_FIGURES=('distribution_pilot.png','05_mitigation_median_CI_convergence.png','06_mitigation_three_tier_positioning.png')
OPTIONAL_FIGURES=('04_mitigation_accuracy_cost.png',)
REQUIRED_FIGURES=PAPER_FIGURES+WIDE_FIGURES
FIGURES=[dict(name=name,label=label,group=group,chart=chart,required=name in REQUIRED_FIGURES) for name,label,group,chart in (
 ('nyquist.png','Nyquist','EIS fitting','nyquist'),('bode_magnitude.png','Bode Magnitude','EIS fitting','bode_magnitude'),('bode_phase.png','Bode Phase','EIS fitting','bode_phase'),
 ('residual_real.png','Real Residual','Residuals','residual_real'),('residual_imag.png','Imaginary Residual','Residuals','residual_imag'),
 ('residual_real_normalized.png','Normalized Real Residual','Normalized residuals','residual_real_normalized'),('residual_imag_normalized.png','Normalized Imaginary Residual','Normalized residuals','residual_imag_normalized'),
 ('distribution_pilot.png','Distribution','Mitigation statistics',None),('05_mitigation_median_CI_convergence.png','Median / confidence interval convergence','Mitigation statistics',None),
 ('06_mitigation_three_tier_positioning.png','Mitigation positioning against measured references','Mitigation statistics',None),
 ('04_mitigation_accuracy_cost.png','Accuracy and cost (optional)','Mitigation statistics',None))]

def contract():return {'version':CONTRACT_VERSION,'required':list(REQUIRED_FIGURES),'optional':list(OPTIONAL_FIGURES),'figures':FIGURES}

def descriptor(name):return next((dict(f) for f in FIGURES if f['name']==name),None)

def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def read_json(path):
    if path.stat().st_size>8*1024*1024:raise ValueError('Oversized manifest')
    return json.loads(path.read_text(encoding='utf-8-sig'))

def png_info(path):
    """Inventory metadata; a manifest SHA binds the original verified PNG bytes."""
    width=height=None;dpi=None
    with path.open('rb') as f:
        if f.read(8)!=b'\x89PNG\r\n\x1a\n':raise ValueError('Not a PNG')
        for _ in range(100):
            header=f.read(8)
            if len(header)!=8:break
            length,kind=struct.unpack('>I4s',header)
            if kind==b'IDAT':break
            if length>1024*1024:raise ValueError('Oversized PNG header')
            data=f.read(length);f.read(4)
            if kind==b'IHDR' and len(data)>=8:width,height=struct.unpack('>II',data[:8])
            if kind==b'pHYs' and len(data)==9:
                x,y,unit=struct.unpack('>IIB',data)
                if unit==1:dpi=[x*.0254,y*.0254]
    valid=bool(width and height and min(width,height)>=1000 and dpi and all(abs(x-600)<1 for x in dpi))
    return {'bytes':path.stat().st_size,'width':width,'height':height,'dpi':dpi,'metadata_valid':valid,'sha256':sha(path)}

def inventory(out,n):
    out=Path(out).resolve();figbase=out/f'01_results_and_figures/n={n}';database=out/f'02_run_level_data/n={n}'
    versions={'direct'}
    for base in (figbase,database):
        versions.update(p.name for p in base.glob('*') if p.is_dir() and p.name.startswith('v'))
    result=[]
    for version in sorted(versions):
        fig=figbase if version=='direct' else figbase/version
        data=database if version=='direct' else database/version
        if not fig.resolve().is_relative_to(figbase) or not data.resolve().is_relative_to(database):continue
        manifests=[];issues=[];matched=[]
        for name in ('export_manifest.json','publication_manifest.json'):
            dp=data/name;fp=fig/name
            if not dp.is_file() and not fp.is_file():continue
            try:
                dm=read_json(dp);fm=read_json(fp)
                manifests.append({'path':str(dp),'sha256':sha(dp),'schema_version':dm.get('schema_version'),'declared_contract':dm.get('publication_contract_version'),'figure_references':list(dm.get('figures',{}))})
                if dm!=fm or dm.get('partial_diagnostic_only') or dm.get('n')!=n:raise ValueError('Manifest pair/scope conflict')
                matched.append(dm)
            except (OSError,ValueError,TypeError) as exc:issues.append(name+': '+str(exc))
        entries=[]
        for path in sorted(fig.glob('*.png')):
            if not path.resolve().is_relative_to(figbase):continue
            item={'name':path.name,'path':str(path),'n':n,'version':version,'descriptor':descriptor(path.name)}
            try:
                item.update(png_info(path))
                references=[m.get('figures',{}).get(path.name) for m in matched if path.name in m.get('figures',{})]
                hashes={r.get('sha256') for r in references if isinstance(r,dict)}
                item['manifest_verified']=bool(references) and hashes=={item['sha256']} and all(r.get('valid') is True for r in references)
                item['display_eligible']=bool(item['descriptor'] and item['metadata_valid'] and item['manifest_verified'])
                item['reference_status']='Verified' if item['manifest_verified'] else 'Conflicting reference' if references else 'Missing manifest reference'
            except (OSError,ValueError,TypeError,struct.error) as exc:item.update(display_eligible=False,error=str(exc))
            entries.append(item)
        if not entries and not manifests and not issues:continue
        present={e['name'] for e in entries};verified={e['name'] for e in entries if e['display_eligible']}
        declared=set().union(*(set(m.get('generated_figures') or m.get('figures',{})) for m in matched)) if matched else set(REQUIRED_FIGURES)
        result.append({'version':version,'figure_directory':str(fig),'data_directory':str(data),'contract_version':CONTRACT_VERSION,
            'expected_figures':sorted(declared),'missing_expected':sorted(declared-present),'missing_required':sorted(set(REQUIRED_FIGURES)-present),
            'verified_pngs':len(verified),'figures':entries,'manifests':manifests,'issues':issues,'data_manifests':matched})
    return {'n':n,'contract':contract(),'versions':result}
