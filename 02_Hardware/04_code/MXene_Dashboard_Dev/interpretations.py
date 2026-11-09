"""Bounded, deterministic descriptive summaries of hash-verified exported curves.

No fitting, bootstrap, inference, network access or production writes.
"""
from collections import OrderedDict
import math
from science_ui import SOURCES, chart

_cache = OrderedDict()

def concise_summary(rows,kind,n):
    """Summarize existing metrics; never substitute curve RMS for canonical RMSE."""
    from research import LABELS
    distribution=kind=='distribution_pilot'
    field='median' if distribution else 'rms'
    candidates=[r for r in rows if isinstance(r.get(field),(float,int)) and math.isfinite(r[field])]
    baseline=next((r for r in candidates if r.get('treatment') in ('M0_Baseline','None')),None)
    if not candidates or baseline is None:return ['A quantitative baseline comparison is unavailable for this figure. No ranking is inferred from the image.']
    best=min(candidates,key=lambda r:r[field]);b=baseline[field];v=best[field]
    name=LABELS.get(best['treatment'],best['treatment']);unit=best.get('unit','')
    metric=('median canonical SSE' if distribution else 'pointwise complex-error RMS (not canonical per-PUB RMSE)' if kind=='nyquist' else 'phase-difference RMS' if kind=='bode_phase' else 'magnitude-difference RMS' if kind=='bode_magnitude' else 'normalized residual RMS' if kind.endswith('_normalized') else 'component residual RMS')
    count=best.get('count') if distribution else best.get('points')
    scope=f"{count} accepted PUB observations" if distribution else f"{count} frequency points"
    first=f"At n={n}, None has {metric} {b:.6g} {unit}; {name} has the lowest value, {v:.6g} {unit}, across {scope}."
    change=f"The absolute reduction is {b-v:.6g} {unit}"
    change+=f" ({100*(b-v)/b:.4g}% relative to None)." if b>0 else '; a percentage is undefined because the baseline is zero.'
    conclusion='This is a descriptive ranking, not evidence of statistical significance or a causal quantum-noise effect.'
    if distribution and any(r.get('count',0)<n for r in candidates):conclusion='This preliminary accepted subset supports a descriptive ranking only; missing observations and execution dependence limit inference.'
    return [first,change,conclusion]

def interpretation(reader, key):
    snapshot = reader.status()
    image = next((a for a in snapshot['artifacts'] if a['id'] == key), None)
    if not image or image['level'] not in (5, 10, 15, 20, 25, 30):
        raise ValueError('A registered n-level figure is required')
    reader.artifact(key)  # Revalidate even on a cache hit.
    from reader import figure_descriptor,figure_contract
    registered=figure_descriptor(image['name'])
    if registered is None:raise ValueError('Figure is outside the shared publication contract')
    kind = image['name'].removesuffix('.png')
    source = next((a for a in snapshot['artifacts'] if a['level'] == image['level']
                   and a['version'] == image['version'] and a['name'] == SOURCES.get(kind)), None)
    base = dict(publication_contract=figure_contract()['version'],figure_descriptor=registered,available=False, n=image['level'], figure=image['name'], source=image,
                finality=image.get('finality', 'Finality not independently established'),
                method='Descriptive exported-curve summary; no refitting or statistical inference.')
    if kind=='06_mitigation_three_tier_positioning':
        return dict(base,method='Original create_mos2_style_figures: median SSE relative to the measured reference; bootstrap intervals describe PUB replicates, not independent job reproducibility.',reason='The original PNG is verified. Its positioning numerical table and reference provenance are not part of this exported numerical contract; no ranking is inferred from pixels.')
    if kind=='distribution_pilot':
        from research import execute,ToolRequest
        result=execute(reader,ToolRequest(levels=[image['level']],metric='SSE'))
        return dict(base,available=bool(result['rows']),compact=concise_summary(result['rows'],kind,image['level']),summaries=[{'text':v} for v in result['sentences']],warning='Accepted-observation subset summary; it may differ from the full published distribution. '+result['warning'],reason='Complete accepted PUB data are unavailable.',timestamp=result['captured_at'])
    if not source:
        return dict(base, reason='No verified numerical/method contract is exposed for this figure and publication version. Normalization, PUB-level distributions and confidence intervals are not inferred from PNG pixels.')
    reader.artifact(source['id'])
    version = (key, image['level'], image['version'], kind, image['sha256'], source['sha256'], image.get('finality'), source.get('timestamp'))
    if version in _cache:
        return _cache[version]
    data = chart(reader, source['id'], kind)
    measured = next((s for s in data['series'] if s['name'] == 'Experimental'), None)
    summaries = []
    for s in data['series']:
        if not s['name'].startswith('M'):
            continue
        if kind.startswith('residual_'):
            values = s['y']; unit = 'dimensionless' if kind.endswith('_normalized') else 'ohm'
            meaning = 'The signed mean describes residual bias; RMS measures combined bias and dispersion. These summaries alone do not establish systematic frequency structure.'
        elif measured and len(s['y']) == len(measured['y']):
            if kind == 'nyquist':
                if data.get('frequency') is None:
                    summaries.append(dict(treatment=s['name'],text='Frequency correspondence is unavailable; fit quality is not quantified.'))
                    continue
                values=[math.hypot(x-a,y-b) for x,y,a,b in zip(s['x'],s['y'],measured['x'],measured['y'])]
                unit='ohm';meaning='Complex-plane disagreement at matching measured frequencies. A locus alone does not identify a unique circuit mechanism or quantum-noise cause.'
            elif s['x'] == measured['x']:
                values = [a-b for a,b in zip(s['y'],measured['y'])]
                unit = 'degrees' if kind == 'bode_phase' else 'ohm'
                meaning = ('Direct exported phase differences are not phase-unwrapped here; branch-cut effects may affect their interpretation.' if kind == 'bode_phase' else 'Magnitude differences quantify agreement over the sampled frequencies without weighting by frequency or measurement uncertainty.')
            else:
                continue
        else:
            continue
        if not values or not all(math.isfinite(v) for v in values):
            continue
        count = len(values); mean = math.fsum(v/count for v in values)
        rms = math.hypot(*values)/math.sqrt(count)
        peak = max(abs(v) for v in values)
        frequencies=data.get('frequency')
        if frequencies:
            peak_f=frequencies[max(range(count),key=lambda i:abs(values[i]))]
            meaning+=f' Largest absolute deviation occurs at {peak_f:.6g} Hz.'
        summaries.append(dict(treatment=s['name'],points=count,mean=mean,rms=rms,max_absolute=peak,unit=unit,
                              text=f"{s['name']}: {count} exported points; {'mean distance' if kind == 'nyquist' else 'signed mean'} {mean:.6g} {unit}, RMS {rms:.6g} {unit}, maximum absolute deviation {peak:.6g} {unit}. {meaning}"))
    result = dict(base, available=bool(summaries), summaries=summaries, numerical_source=source,
                  compact=concise_summary(summaries,kind,image['level']),
                  timestamp=data['timestamp'], reason='No aligned treatment and experimental series are available.' if not summaries else '',
                  warning='Descriptive observations, not confidence intervals or evidence of treatment significance. Incomplete accepted subsets are preliminary.')
    _cache[version] = result
    while len(_cache) > 64:
        _cache.popitem(last=False)
    return result
