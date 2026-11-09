"""Offline statistical primitives; never import an IBM client or modify inputs.

An execution ID is not evidence of independence. The caller must supply a
reviewed design contract bound to the exact observation hash. Without it only
the reference IID sensitivity calculation is available; no E/F plots are made.
This module is not a substitute for frozen-input and campaign readiness checks.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


import hashlib
import json
import math
from collections import Counter, defaultdict

LEVELS = (5, 10, 15, 20, 25, 30)
TREATMENTS = ('M0_Baseline', 'M1_DD', 'M2_TREX', 'M3_ZNE',
              'M4_DD_TREX', 'M5_DD_ZNE', 'M6_TREX_ZNE', 'M7_ALL')


def observation_hash(rows):
    ordered = sorted(rows, key=lambda r: (r['treatment'], r['source_n'], r['source_pub']))
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, allow_nan=False,
                                   separators=(',', ':')).encode()).hexdigest()


def validate_observations(rows):
    expected = {(t, n, p) for t in TREATMENTS for n in LEVELS for p in range(1, n + 1)}
    keys = []
    for row in rows:
        n, p = row['source_n'], row['source_pub']
        if type(n) is not int or type(p) is not int:
            raise ValueError('Logical n/PUB identities must be integers')
        keys.append((row['treatment'], n, p))
        if isinstance(row['SSE'], bool) or not math.isfinite(float(row['SSE'])) or float(row['SSE']) <= 0:
            raise ValueError('SSE must be finite and positive')
    if len(keys) != 840 or len(set(keys)) != 840 or set(keys) != expected:
        raise ValueError('Require exactly 840 unique expected logical PUB slots')
    return observation_hash(rows)


def analyze(rows, design=None, *, iterations=3000, seed=1013):
    """Pooled observation-weighted medians, IID reference and optional clusters.

    Cluster method samples K whole clusters with replacement, preserves their
    observed sizes, then takes the pooled median (a ratio empirical distribution
    estimand, not an equally weighted cluster median). Unequal sizes therefore
    produce different sample sizes in different replicates. Across treatments
    resampling is unpaired and requires an explicit cross-treatment independence
    rationale. Hierarchical/paired inference is deliberately unavailable until a
    separate reviewed experimental-design adapter exists.
    """
    digest = validate_observations(rows)
    if type(iterations) is not int or not 100 <= iterations <= 10000:
        raise ValueError('Use 100..10000 bootstrap iterations')
    from low_memory_runtime import limit_native_threads
    limit_native_threads()
    import numpy as np
    rng = np.random.default_rng(seed)
    grouped = {t: sorted((r for r in rows if r['treatment'] == t),
                         key=lambda r: (r['source_n'], r['source_pub'])) for t in TREATMENTS}
    skips = {'hierarchical': 'Six unequal n-level designs are not verified exchangeable replicates.',
             'paired': 'Shared experimental blocks and exchangeable pairing have not been verified.'}
    supported = False
    clusters = {}
    if design is None:
        skips['cluster'] = 'No reviewed independence contract; job/Batch IDs alone do not establish independence.'
    else:
        if design.get('observations_sha256') != digest:
            raise ValueError('Design contract observation hash mismatch')
        required = ('reviewer', 'evidence', 'independence_rationale', 'cross_treatment_independence_rationale')
        if any(not isinstance(design.get(k), str) or not design[k].strip() for k in required):
            raise ValueError('A reviewed design and cross-treatment independence evidence are required')
        if design.get('unit_field') != 'execution_cluster' or design.get('paired') is not False:
            raise ValueError('Only explicitly unpaired reviewed execution clusters are supported')
        owner = {}
        for t, items in grouped.items():
            groups = defaultdict(list)
            for r in items:
                unit = r.get('execution_cluster')
                if not isinstance(unit, str) or not unit.strip():
                    raise ValueError('Missing verified cluster identity')
                identity = (t, r['source_n'])
                if unit in owner and owner[unit] != identity:
                    raise ValueError('Nonnested or shared cluster contradicts unpaired design')
                owner[unit] = identity
                groups[unit].append(float(r['SSE']))
            clusters[t] = [np.asarray(groups[k]) for k in sorted(groups)]
        if any(len(v) < 2 for v in clusters.values()):
            skips['cluster'] = 'Fewer than two verified clusters for a treatment.'
        else:
            supported = True
    statistics, samples = [], {}
    for t, items in grouped.items():
        x = np.asarray([float(r['SSE']) for r in items])
        samples[t] = {}
        # One replicate at a time: bounded memory even with unequal clusters.
        iid = np.asarray([np.median(rng.choice(x, len(x), replace=True)) for _ in range(iterations)])
        samples[t]['IID'] = iid
        if supported:
            units = clusters[t]
            samples[t]['cluster'] = np.asarray([
                np.median(np.concatenate([units[i] for i in rng.integers(0, len(units), len(units))]))
                for _ in range(iterations)])
        for method, draws in samples[t].items():
            lo, hi = np.quantile(draws, [.025, .975])
            sizes = [len(a) for a in clusters[t]] if method == 'cluster' else []
            statistics.append(dict(treatment=t, method=method, median_SSE=float(np.median(x)),
                CI_low=float(lo), CI_high=float(hi), interval_width=float(hi-lo),
                observations=len(x), cluster_count=len(sizes), cluster_sizes=sizes))
    contrasts, ranking = [], []
    if supported:
        matrix = np.column_stack([samples[t]['cluster'] for t in TREATMENTS])
        for i, t in enumerate(TREATMENTS):
            # Ties split best-rank probability instead of favoring first column.
            best = matrix == matrix.min(axis=1, keepdims=True)
            probability = np.mean(best[:, i] / best.sum(axis=1))
            ranking.append(dict(treatment=t, probability_best=float(probability)))
            if i:
                differences = matrix[:, i] - matrix[:, 0]
                log_ratios = np.log(matrix[:, i] / matrix[:, 0])
                for metric, values in [('median_SSE_difference', differences), ('log_median_SSE_ratio', log_ratios)]:
                    lo, hi = np.quantile(values, [.025, .975])
                    contrasts.append(dict(treatment=t, reference=TREATMENTS[0], metric=metric,
                        CI_low=float(lo), CI_high=float(hi), method='unpaired cluster percentile'))
    else:
        skips['contrasts'] = skips['ranking'] = 'No supported joint dependence-aware sampling distribution.'
    manifest = dict(schema_version=1, observations_sha256=digest, observations=840,
        source_n_levels=list(LEVELS), n_level_weights={str(n):n/105 for n in LEVELS},
        estimand='Median of pooled observation-weighted empirical SSE per treatment',
        iterations=iterations, seed=seed, interval='ordinary percentile 2.5%, 97.5%',
        cluster_unit='reviewed execution_cluster' if supported else None,
        clusters={t:dict(count=len(units),sizes=[len(unit) for unit in units])
                  for t,units in clusters.items()},
        treatment_pairing_evidence=None,
        design_contract=design, skipped_methods=skips, cluster_supported=supported,
        warnings=['IID intervals do not account for job/Batch dependence.',
                  'Cluster independence is a reviewed design assumption, not proven by IDs.',
                  'Six unequal n-levels limit inference; no hierarchical inference is implemented.',
                  'Contrasts are exploratory and not multiplicity-adjusted; colors do not imply significance.',
                  'Recovery selection, calibration drift and backend/account differences may confound comparisons.'])
    return dict(manifest=manifest, statistics=statistics, contrasts=contrasts, ranking=ranking)


def export(result, destination, *, protected):
    """Publish one new immutable directory; caller supplies verified C/D hashes.

    Never replace a destination or any source. A failed run cannot create the
    final directory. Existing identical outputs can be reused after verification.
    This low-level writer does not certify campaign scientific readiness.
    """
    import csv
    import os
    import shutil
    import uuid
    from pathlib import Path
    from scientific_exports import _sha, inspect_png
    destination = Path(destination).resolve()
    if not protected:
        raise ValueError('Protected input and Figure C/D hashes are required')
    def verify():
        for name, digest in protected.items():
            p = Path(name).resolve()
            if destination == p or destination in p.parents:
                raise ValueError('Output overlaps protected inputs')
            if _sha(p) != digest:
                raise ValueError('Protected input changed: ' + str(p))
    verify()
    identity = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    marker = destination / 'statistical_provenance_manifest.json'
    if destination.exists():
        saved = json.loads(marker.read_text())
        if saved.get('result_sha256') != identity or saved.get('protected_hashes') != protected:
            raise FileExistsError('Immutable statistical output belongs to a different input/design')
        if any(_sha(destination / name) != digest for name, digest in saved['output_hashes'].items()):
            raise ValueError('Existing statistical output hash mismatch')
        return saved
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / ('.st-' + uuid.uuid4().hex[:16])
    stage.mkdir(exist_ok=False)
    try:
        def table(name, records, fields):
            with (stage/name).open('x', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
                writer.writeheader()
                for row in records:
                    writer.writerow({k: json.dumps(v) if isinstance(v, list) else v for k,v in row.items()})
                stream.flush(); os.fsync(stream.fileno())
        fields = ['treatment','method','median_SSE','CI_low','CI_high','interval_width','observations','cluster_count','cluster_sizes']
        table('dependence_aware_statistics.csv', result['statistics'], fields)
        table('bootstrap_method_comparison.csv', result['statistics'], fields)
        table('treatment_comparisons.csv', result['contrasts'], ['treatment','reference','metric','CI_low','CI_high','method'])
        if result['manifest']['cluster_supported']:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            with plt.rc_context({'axes.grid':False, 'figure.facecolor':'white', 'axes.facecolor':'white'}):
                fig, ax = plt.subplots(figsize=(12,7))
                try:
                    for offset, method, color in [(-.12,'IID','#D36B32'),(.12,'cluster','#277DA1')]:
                        entries = [r for r in result['statistics'] if r['method'] == method]
                        for i,r in enumerate(entries):
                            ax.plot([i+offset]*2,[r['CI_low'],r['CI_high']], color=color,
                                    label=method if i == 0 else None)
                            ax.scatter(i+offset,r['median_SSE'],color=color)
                    ax.set_xticks(range(8), [t.split('_')[0] for t in TREATMENTS]); ax.set_yscale('log')
                    ax.set_ylabel('Pooled median SSE; percentile 95% CI'); ax.legend()
                    ax.set_title('Dependence sensitivity: reviewed whole-cluster resampling')
                    fig.savefig(stage/'Figure_E_105_PUB_dependence_aware_CI.png',dpi=600,facecolor='white')
                finally: plt.close(fig)
                fig, ax = plt.subplots(figsize=(12,7))
                try:
                    ax.bar(range(8),[r['probability_best'] for r in result['ranking']],color='#277DA1')
                    ax.set_xticks(range(8),[t.split('_')[0] for t in TREATMENTS]);ax.set_ylim(0,1)
                    ax.set_ylabel('Bootstrap probability of lowest median SSE (ties split)')
                    ax.set_title('Exploratory ranking robustness; not a significance test')
                    fig.savefig(stage/'Figure_F_105_PUB_ranking_robustness.png',dpi=600,facecolor='white')
                finally: plt.close(fig)
            for p in stage.glob('*.png'): inspect_png(p,layout='landscape')
        manifest = dict(result['manifest'],result_sha256=identity,protected_hashes=protected,
                        output_hashes={p.name:_sha(p) for p in stage.iterdir()})
        with (stage/marker.name).open('x',encoding='utf-8') as stream:
            json.dump(manifest,stream,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        verify()
        # Unique final directory, no overwriting of any existing results.
        os.rename(stage,destination)
        verify()
        return manifest
    finally:
        if stage.exists(): shutil.rmtree(stage)


# Preserve the established comparison destinations. Production execution uses
# campaign_comparison verification and versioned publication.
from pathlib import Path
import os
NS = LEVELS
BOOT = 3000
SEED = 1013
BASE = Path(__file__).resolve().parents[2]
# Compatibility constants use the same project-root resolver as publication.
from repository_paths import comparison_output_paths
_paths = comparison_output_paths(Path(__file__).resolve().parents[4])
FIG = _paths['figures']
TABLES = _paths['data']
DATA = TABLES.parent
COMPARISON_TREATMENTS = [
    ("M0_Baseline", "None"),
    ("M1_DD", "DD"),
    ("M2_TREX", "TREX"),
    ("M3_ZNE", "ZNE"),
    ("M4_DD_TREX", "DD+TREX"),
    ("M5_DD_ZNE", "DD+ZNE"),
    ("M6_TREX_ZNE", "TREX+ZNE"),
    ("M7_ALL", "ALL"),
]

def _comparison_imports():
    from low_memory_runtime import limit_native_threads
    limit_native_threads()
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    return np, pd, plt

# Canonical C/D publication: ordinary IID sensitivity intervals are explicitly
# distinguished from the separately gated dependence-aware methods above.
COLORS = ('#666666','#0072B2','#009E73','#E69F00','#CC79A7','#D55E00','#56B4E9','#7B3FB5')
TREATMENT_COLORS = dict(zip(TREATMENTS,COLORS))
PUBLICATION_STYLE = dict(schema_version=1,palette=TREATMENT_COLORS,
    standalone_inches=[12.5,8],standalone_axes=[.13,.24,.84,.59],
    title_size=17,subtitle_size=11,axis_label_size=11,tick_size=10,annotation_size=10,
    footer_size=9,marker_size=7.5,error_width=1.8,cap_size=5,
    title_y=.955,subtitle_y=.895,footer_y=[.13,.085,.04],dpi=600,
    background='white',grid=False,border_color='black',border_width=1.6)
STYLE = {'axes.grid':False,'figure.facecolor':'white','axes.facecolor':'white',
         'axes.edgecolor':'black','axes.linewidth':PUBLICATION_STYLE['border_width'],
         'axes.labelweight':'bold','axes.titleweight':'bold','font.family':'DejaVu Sans',
         'font.size':11,'axes.labelsize':11,'xtick.labelsize':10,'ytick.labelsize':10,
         'legend.fontsize':9,'savefig.bbox':None}


def _publication_header(fig,title,subtitle):
    fig.text(.5,PUBLICATION_STYLE['title_y'],title,ha='center',fontsize=PUBLICATION_STYLE['title_size'],fontweight='bold')
    fig.text(.5,PUBLICATION_STYLE['subtitle_y'],subtitle,ha='center',fontsize=PUBLICATION_STYLE['subtitle_size'])


def _publication_footer(fig,lines):
    for y,line in zip(PUBLICATION_STYLE['footer_y'],lines):
        fig.text(.5,y,line,ha='center',fontsize=PUBLICATION_STYLE['footer_size'],color='#333333')


def _standalone_canvas(title,subtitle):
    _,_,plt=_comparison_imports()
    fig=plt.figure(figsize=PUBLICATION_STYLE['standalone_inches'])
    ax=fig.add_axes(PUBLICATION_STYLE['standalone_axes'])
    _publication_header(fig,title,subtitle)
    ax.tick_params(which='both',direction='out')
    return fig,ax


def _treatment_interval(ax,x,point,lower,upper,treatment,annotation=None):
    color=TREATMENT_COLORS[treatment];width=PUBLICATION_STYLE['error_width']
    ax.vlines(x,lower,upper,color=color,lw=width)
    ax.plot([x,x],[lower,upper],linestyle='none',marker='_',markersize=2*PUBLICATION_STYLE['cap_size'],
            markeredgewidth=width,color=color)
    line,=ax.plot(x,point,'o',color=color,ms=PUBLICATION_STYLE['marker_size'],zorder=3)
    line.set_gid('treatment:'+treatment)
    if annotation is not None:
        ax.annotate(annotation,(x,point),xytext=(8,8),textcoords='offset points',ha='left',
                    fontsize=PUBLICATION_STYLE['annotation_size'],color=color,zorder=5,
                    bbox=dict(facecolor='white',edgecolor='none',alpha=.97,pad=.4))


def _save_comparison(fig, directory, name):
    from scientific_exports import inspect_png
    _,_,plt=_comparison_imports();path=Path(directory)/name
    if path.exists():
        plt.close(fig);raise FileExistsError(path)
    try:
        fig.canvas.draw();bounds=fig.get_tightbbox(fig.canvas.get_renderer())
        w,h=map(float,fig.get_size_inches())
        if bounds.x0<0 or bounds.y0<0 or bounds.x1>w or bounds.y1>h:
            raise ValueError(f'Figure labels exceed fixed publication canvas: {name}: {bounds.bounds}')
        colors={line.get_gid().split(':',1)[1]:line.get_color()
                for ax in fig.axes for line in ax.lines
                if (line.get_gid() or '').startswith('treatment:')}
        if any(color!=TREATMENT_COLORS[t] for t,color in colors.items()):
            raise ValueError('Inconsistent treatment palette')
        layout=dict(inches=[w,h],axes_positions=[list(ax.get_position().bounds) for ax in fig.axes],
                    x_labels=[[t.get_text() for t in ax.get_xticklabels()] for ax in fig.axes],
                    treatment_colors=colors,content_bounds_inches=list(bounds.bounds),no_clipping=True,
                    y_labels=[ax.get_ylabel() for ax in fig.axes],y_scales=[ax.get_yscale() for ax in fig.axes],
                    y_limits=[list(ax.get_ylim()) for ax in fig.axes],figure_text=[text.get_text() for text in fig.texts],style=PUBLICATION_STYLE)
        # Fixed canvas: tight cropping would give E/F different pixel dimensions.
        fig.savefig(path,dpi=PUBLICATION_STYLE['dpi'],facecolor='white',bbox_inches=None)
    finally:plt.close(fig)
    layout['png']=inspect_png(path,layout='landscape')
    return layout


def ordinary_median_bootstrap(values, rng):
    """Original pooled IID sensitivity calculation; not cluster-adjusted."""
    np,_,_=_comparison_imports()
    draws=rng.choice(values,size=(BOOT,len(values)),replace=True)
    lower,upper=np.quantile(np.median(draws,axis=1),[.025,.975])
    return float(np.median(values)),float(lower),float(upper)


def render_figure_c(records,figure_dir):
    """Render the canonical C CSV without recalculating or changing statistics."""
    np,_,plt=_comparison_imports()
    with plt.rc_context(STYLE):
        fig,axes=plt.subplots(2,4,figsize=(16,9))
        fig.subplots_adjust(left=.07,right=.98,bottom=.24,top=.84,wspace=.30,hspace=.42)
        _publication_header(fig,'C | Pooled PUB Prefix Median SSE',
            f'Ordinary pooled-prefix bootstrap, {BOOT:,} replicates | seed {SEED}')
        for index,(treatment,label) in enumerate(COMPARISON_TREATMENTS):
            group=sorted((r for r in records if r['treatment']==treatment),key=lambda r:int(r['cumulative_pub_count']))
            counts=np.array([int(r['cumulative_pub_count']) for r in group])
            if list(counts)!=list(range(5,106)):raise ValueError('Incomplete canonical C prefix table')
            medians=np.array([float(r['median_SSE']) for r in group])
            lows=np.array([float(r['CI_low']) for r in group]);highs=np.array([float(r['CI_high']) for r in group])
            ax=axes.flat[index];color=TREATMENT_COLORS[treatment]
            ax.fill_between(counts,lows,highs,color=color,alpha=.20)
            line,=ax.plot(counts,medians,color=color,linewidth=1.8);line.set_gid('treatment:'+treatment)
            checkpoints=np.cumsum(NS)
            ax.plot(checkpoints,medians[checkpoints-5],'o',color=color,markersize=PUBLICATION_STYLE['marker_size'])
            ax.set_title(label,fontsize=12,color=color);ax.set_yscale('log');ax.set_xlim(4,106)
            ax.set_xticks([5,15,30,50,75,105])
            if index>=4:ax.set_xlabel('Cumulative pooled PUB count')
            if index%4==0:ax.set_ylabel(r'Median SSE ($\Omega^2$)')
            ax.text(.5,.025,f'Upper/lower ratio\n5 PUBs: {highs[0]/lows[0]:.2f}; 105 PUBs: {highs[-1]/lows[-1]:.2f}',
                    transform=ax.transAxes,ha='center',va='bottom',fontsize=PUBLICATION_STYLE['annotation_size'],
                    bbox=dict(facecolor='white',edgecolor=color,alpha=.95,pad=2))
        _publication_footer(fig,[
            'Shading: 2.5th-97.5th percentile sensitivity ranges; not inferential confidence intervals.',
            'Pooled prefixes are order-dependent and are not optimizer convergence.',
            'Ordinary bootstrap does not account for within-job or between-job dependence; recovery and calibration effects remain.'])
        return _save_comparison(fig,figure_dir,'Figure_C_105_PUB_convergence.png')


def figure_method_metadata():
    """Publication/UI contract; frontend never invents statistical labels."""
    labels={'C':'Ordinary pooled-prefix bootstrap, 3,000 replicates',
            'D':'Ordinary PUB-level bootstrap, 3,000 replicates',
            'E':'Matched n-level cluster bootstrap, 10,000 replicates',
            'F':'Matched n-level cluster bootstrap, 10,000 replicates'}
    return {letter:dict(label=label,seed=SEED,replicates=BOOT if letter in 'CD' else 10000,
        percentiles=[2.5,97.5],quantile_method='linear',
        unit='individual pooled PUB' if letter in 'CD' else 'whole n-level block; shared draws across treatments',
        caveat=('Ordinary bootstrap does not account for within-job or between-job dependence; no automatic significance.'
                if letter in 'CD' else 'Conditional cluster-bootstrap sensitivity intervals; n-level independence/exchangeability not established. Recovery-job dependence and calibration drift may remain.'))
        for letter,label in labels.items()}


def figure_c_statistics(rows):
    """Original ordinary pooled-prefix bootstrap, in canonical RNG traversal order."""
    validate_observations(rows)
    np,pd,_=_comparison_imports();rng=np.random.default_rng(SEED);records=[]
    for treatment,label in COMPARISON_TREATMENTS:
        values=np.asarray([r['SSE'] for r in sorted(rows,key=lambda r:(r['source_n'],r['source_pub'])) if r['treatment']==treatment])
        for count in range(5,106):
            median,lower,upper=ordinary_median_bootstrap(values[:count],rng)
            records.append(dict(treatment=treatment,cumulative_pub_count=count,median_SSE=median,
                                CI_low=lower,CI_high=upper,CI_width_ratio=upper/lower))
    return records


def generate_figure_c(*, rows, figure_dir, table_dir):
    records=figure_c_statistics(rows)
    _,pd,_=_comparison_imports()
    render_figure_c(records,figure_dir)
    pd.DataFrame(records).to_csv(Path(table_dir)/'convergence_105_statistics.csv',index=False,mode='x')
    return records


def figure_d_statistics(rows, references):
    """Data-derived positioning; optional reference lines require verified sources."""
    validate_observations(rows)
    if not references or references.get('verified') is not True:
        raise ValueError('Figure D requires verified data-derived references')
    np,pd,plt=_comparison_imports();rng=np.random.default_rng(SEED)
    baseline=float(np.median([r['SSE'] for r in rows if r['treatment']==TREATMENTS[0]]))
    if not np.isclose(baseline,references['measured_M0_median_SSE'],rtol=1e-12,atol=1e-10):
        raise ValueError('Figure D M0 reference differs from accepted observations')
    classical=references.get('classical_SSE');ideal=references.get('ideal_SSE')
    reference=classical if classical is not None else baseline
    reference_type='verified_classical' if classical is not None else 'measured_M0_median'
    statistics=[]
    for treatment,label in COMPARISON_TREATMENTS:
        values=np.asarray([r['SSE'] for r in sorted(rows,key=lambda r:(r['source_n'],r['source_pub'])) if r['treatment']==treatment])
        median,lower,upper=ordinary_median_bootstrap(values,rng)
        statistics.append(dict(treatment=treatment,label=label,n=105,median_SSE=median,
                               CI_low=lower,CI_high=upper,reference_type=reference_type,reference_SSE=reference,
                               median_over_reference=median/reference,CI_low_over_reference=lower/reference,
                               CI_high_over_reference=upper/reference,median_over_M0=median/baseline,
                               below_measured_M0=median<baseline))
    table=pd.DataFrame(statistics).sort_values(['median_SSE','treatment']).reset_index(drop=True)
    table.insert(0,'rank',table.median_SSE.rank(method='min').astype(int))
    return table.to_dict('records')


def generate_figure_d(*, rows, figure_dir, table_dir, references=None):
    records=figure_d_statistics(rows,references)
    _,pd,_=_comparison_imports()
    render_figure_d(records,references,figure_dir)
    pd.DataFrame(records).to_csv(Path(table_dir)/'triple_positioning_105.csv',index=False,mode='x')
    return records


def comparison_plot_order(records, figure):
    """Presentation only: stable numeric ordering, without mutating CSV/RNG order."""
    metric,direction={'D':('median_over_reference',1),'E':('delta_SSE',1),
                      'F':('reduction_percent',-1)}[figure]
    selected=[row for row in records if figure=='D' or row['treatment']!=TREATMENTS[0]]
    return sorted(selected,key=lambda row:(direction*float(row[metric]),row['treatment']))


def render_figure_d(records,references,figure_dir):
    _,_,plt=_comparison_imports()
    baseline=references['measured_M0_median_SSE'];classical=references.get('classical_SSE');ideal=references.get('ideal_SSE')
    reference=classical if classical is not None else baseline
    ordered=comparison_plot_order(records,'D')
    if classical is not None:
        outside=[dict(treatment=r['treatment'],low=float(r['CI_low_over_reference']),high=float(r['CI_high_over_reference']))
                 for r in ordered if not (0.9<=float(r['CI_low_over_reference'])<=float(r['CI_high_over_reference'])<=6.5)
                 or not 0.9<=float(r['median_over_reference'])<=6.5]
        if outside:raise ValueError(f'Figure D intervals/points outside requested log range [0.9, 6.5]; refusing silent clipping: {outside}')
        lines=[1,baseline/reference]+([ideal/reference] if ideal is not None else [])
        if not all(0.9<=v<=6.5 for v in lines):raise ValueError('Figure D verified reference outside requested range [0.9, 6.5]')
    with plt.rc_context(STYLE):
        fig,ax=_standalone_canvas('D | Mitigation Performance Positioning',
            f'Ordinary PUB-level bootstrap, {BOOT:,} replicates | seed {SEED}')
        for i,row in enumerate(ordered):
            point=float(row['median_over_reference'])
            _treatment_interval(ax,i,point,float(row['CI_low_over_reference']),float(row['CI_high_over_reference']),
                                row['treatment'],f'{point:.2f}x')
        ax.set_xticks(range(8),[r['label'] for r in ordered]);ax.set_yscale('log');ax.set_xlim(-.55,7.65)
        ax.set_xlabel('Mitigation treatment (ranked by median SSE)',labelpad=13)
        ax.set_ylabel('Median SSE / Classical SSE' if classical is not None else 'Median SSE / measured M0 SSE')
        if classical is not None:ax.set_ylim(.9,6.5)
        if classical is not None:ax.axhline(1,color='black',ls='--',lw=1.6,label='Classical fitting SSE = 1x')
        ax.axhline(baseline/reference,color=TREATMENT_COLORS[TREATMENTS[0]],ls='-.',lw=1.6,label=f'Measured M0 baseline = {baseline/reference:.3g}x')
        if ideal is not None:ax.axhline(ideal/reference,color='#333333',ls=':',lw=1.6,label='Noiseless ideal VQE')
        ax.legend(loc='upper left',facecolor='white',framealpha=.97)
        _publication_footer(fig,[
            '105 pooled PUBs per treatment across six n-levels.',
            'Ordinary PUB-level bootstrap, 3,000 replicates; job dependence not accounted for.',
            '95% sensitivity intervals do not establish significance; recovery jobs and calibration drift remain limitations.'])
        layout=_save_comparison(fig,figure_dir,'Figure_D_105_PUB_triple_positioning.png')
        layout.update(plot_order=[r['treatment'] for r in ordered],sort_metric='median_over_reference',sort_direction='ascending')
        return layout


def _matched_block_samples(groups, draws):
    """Shared whole-block draws; pooled medians and mean block medians differ."""
    np,_,_=_comparison_imports()
    block_medians=np.array([[np.median(block) for block in group] for group in groups]).T
    pooled=np.empty((len(draws),len(groups)));cache={}
    for i,draw in enumerate(draws):
        key=tuple(sorted(map(int,draw)))
        if key not in cache:
            cache[key]=[np.median(np.concatenate([group[j] for j in key])) for group in groups]
        pooled[i]=cache[key]
    equal=block_medians[draws].mean(axis=1)
    return pooled,equal


def supplementary_sensitivity(rows, *, replicates=10000, seed=SEED):
    """Reference-inspired checks using ONLY the canonical accepted observations.

    Neither equal-weight designs nor effective-job components prove independence.
    These tables supplement, never replace, the pooled E/F estimands.
    """
    digest=validate_observations(rows);np,_,_=_comparison_imports()
    if type(replicates) is not int or replicates<100:raise ValueError('At least 100 resamples required')
    groups=[[np.array([r['SSE'] for r in sorted(rows,key=lambda r:r['source_pub'])
                      if r['source_n']==n and r['treatment']==t]) for n in LEVELS] for t in TREATMENTS]
    jobs=defaultdict(set)
    for r in rows:jobs[r['job_id']].add(r['source_n'])
    components=[{n} for n in LEVELS];links=[]
    for job,levels in sorted(jobs.items()):
        if len(levels)>1:
            links.append(dict(job_id=job,source_n=sorted(levels)))
            touching=[g for g in components if g & levels]
            components=[g for g in components if not g & levels]+[set.union(*touching)]
    components=sorted(map(sorted,components))
    draws=np.random.default_rng(seed).integers(0,6,size=(replicates,6))
    pooled,equal=_matched_block_samples(groups,draws)
    linked=[[np.concatenate([group[LEVELS.index(n)] for n in component]) for component in components] for group in groups]
    component_draws=np.random.default_rng(seed).integers(0,len(components),size=(replicates,len(components)))
    component_samples,_=_matched_block_samples(linked,component_draws)
    point=np.array([np.median(np.concatenate(group)) for group in groups])
    equal_point=np.array([np.mean([np.median(block) for block in group]) for group in groups])
    def table(estimates,samples,method,estimand):
        delta=samples-samples[:,[0]];reduction=100*(1-samples/samples[:,[0]])
        result=[]
        for i,(t,label) in enumerate(COMPARISON_TREATMENTS):
            dl,dh=np.quantile(delta[:,i],[.025,.975],method='linear')
            rl,rh=np.quantile(reduction[:,i],[.025,.975],method='linear')
            result.append(dict(treatment=t,label=label,estimand=estimand,method=method,
                estimand_treatment_SSE=float(estimates[i]),estimand_M0_SSE=float(estimates[0]),
                delta_SSE=float(estimates[i]-estimates[0]),reduction_percent=float(100*(1-estimates[i]/estimates[0])),
                delta_sensitivity_low=float(dl),delta_sensitivity_high=float(dh),
                reduction_sensitivity_low=float(rl),reduction_sensitivity_high=float(rh)))
        return result
    return dict(equal_weight=table(equal_point,equal,'equal-weight n-level sensitivity','arithmetic mean of six n-level medians'),
        shared_job=table(point,component_samples,'effective-job connected-component sensitivity','pooled treatment median'),
        manifest=dict(observations_sha256=digest,replicates=replicates,seed=seed,rng='numpy.default_rng / PCG64',
            percentiles=[2.5,97.5],quantile_method='linear',shared_draws_across_treatments=True,
            connected_components=components,cross_n_effective_job_links=links,
            component_matches_primary=bool(np.array_equal(pooled,component_samples)),
            equal_weight_point_weights={str(n):1/6 for n in LEVELS},
            component_weighting='uniform component draws; retain all observations, including component multiplicities',
            equal_weight_formula='contrast arithmetic means of n-level medians; not pooled medians',
            delta_formula='estimand_treatment_SSE - estimand_M0_SSE',
            reduction_formula='100 * (1 - estimand_treatment_SSE / estimand_M0_SSE)',
            pairing='n-level design labels only; no paired PUB or job measurements assumed',
            limitations=['Sensitivity intervals only; independence and exchangeability are not established.',
                'Effective-job components preserve observed links, not unknown calibration/account/time dependence.',
                'Original/replacement provenance is retained; replacement jobs are not independent replicates.',
                'Equal weights change the estimand. No significance or multiplicity-adjusted claims.']))


def matched_n_sensitivity(rows, *, replicates=10000, seed=SEED):
    """Joint design-reweighting sensitivity, never an independence-based CI.

    Keep each whole n-level (all treatments and their observed job structure),
    sample six labels with replacement, and pool the retained observations.
    The common draws preserve matching across treatments. Unequal n changes
    replicate sizes; no within-block resampling or fitted data are introduced.
    """
    digest=validate_observations(rows)
    if type(replicates) is not int or replicates<100:
        raise ValueError('At least 100 sensitivity replicates required')
    np,_,_=_comparison_imports()
    jobs=defaultdict(set);job_counts=Counter();replacement_counts=Counter()
    for r in rows:
        jobs[r['job_id']].add(r['source_n']);job_counts[r['job_id']]+=1
        if r.get('source')=='REPLACEMENT':replacement_counts[r['job_id']]+=1
    if any(len(levels)>1 for levels in jobs.values()):
        raise ValueError('A job spans n-level blocks; this resampling would split its dependence structure')
    groups=[[np.array([r['SSE'] for r in sorted(rows,key=lambda r:r['source_pub'])
                       if r['source_n']==n and r['treatment']==t],dtype=float)
             for n in LEVELS] for t in TREATMENTS]
    point=np.array([np.median(np.concatenate(group)) for group in groups])
    rng=np.random.default_rng(seed)
    draws=rng.integers(0,len(LEVELS),size=(replicates,len(LEVELS)))
    matrix,_=_matched_block_samples(groups,draws)
    delta=matrix-matrix[:,[0]]
    reduction=100*(1-matrix/matrix[:,[0]])
    result=[]
    for i,(t,label) in enumerate(COMPARISON_TREATMENTS):
        dlo,dhi=np.quantile(delta[:,i],[.025,.975],method='linear')
        rlo,rhi=np.quantile(reduction[:,i],[.025,.975],method='linear')
        result.append(dict(treatment=t,label=label,count=105,median_SSE=float(point[i]),
            M0_median_SSE=float(point[0]),delta_SSE=float(point[i]-point[0]),
            delta_sensitivity_low=float(dlo),delta_sensitivity_high=float(dhi),
            reduction_percent=float(100*(1-point[i]/point[0])),
            reduction_sensitivity_low=float(rlo),reduction_sensitivity_high=float(rhi)))
    return dict(statistics=result,manifest=dict(schema_version=1,observations_sha256=digest,
        method='matched n-level cluster-bootstrap sensitivity; conditional, not inferential confidence intervals',
        estimand='median of all 105 pooled accepted SSE observations per treatment',
        delta_formula='median(SSE_treatment) - median(SSE_M0)',
        reduction_formula='100 * (1 - median(SSE_treatment) / median(SSE_M0))',
        resampling_unit='whole n-level design block, all eight treatments together',
        resampling='six labels sampled uniformly with replacement; all observations retained for every draw',
        within_block_resampling=False,shared_draws_across_treatments=True,
        point_weights={str(n):n/105 for n in LEVELS},
        replicate_weights='multiplicity(n) * n / sum(multiplicity(k) * k)',
        replicates=replicates,seed=seed,rng='numpy.default_rng / PCG64',
        percentiles=[2.5,97.5],quantile_method='linear',treatment_order=list(TREATMENTS),
        design_blocks=list(LEVELS),block_sizes_per_treatment=list(LEVELS),
        plotted_treatments=list(TREATMENTS[1:]),baseline_zero_row_retained_in_csv=True,
        job_counts=dict(job_counts),replacement_job_counts=dict(replacement_counts),
        shared_jobs_across_n=False,
        assumptions='Perturbing the mixture of the six observed designs is a sensitivity exercise. '
                    'No independence or exchangeability of experimental replicates is asserted.',
        limitations=['Only six unequal design conditions; selected accounts and calibration drift may confound n effects.',
            'All within-n job/PUB dependence is retained; common dependence between n levels is not modeled.',
            'Recovery selection and shared provider/calibration effects can remain even with distinct job IDs.',
            'Percentile sensitivity intervals are not inferential CIs, significance tests or multiplicity-adjusted results.']))


def render_figures_ef(records,method,figure_dir):
    """Same template and canonical palette for both contrast figures."""
    import gc
    _,_,plt=_comparison_imports();layouts={}
    for letter,metric,lo,hi,title,ylabel,name in (
        ('E','delta_SSE','delta_sensitivity_low','delta_sensitivity_high',
         'E | M0-Referenced SSE Difference',r'Difference in pooled median SSE ($\Omega^2$)',
         'Figure_E_M0_cluster_bootstrap_delta_SSE.png'),
        ('F','reduction_percent','reduction_sensitivity_low','reduction_sensitivity_high',
         'F | Relative SSE Reduction','Relative reduction in pooled median SSE (%)',
         'Figure_F_M0_cluster_bootstrap_reduction.png')):
        gc.collect()
        plotted=comparison_plot_order(records,letter)
        with plt.rc_context(STYLE):
            fig,ax=_standalone_canvas(title,
                f"Matched n-level cluster bootstrap, {method['replicates']:,} replicates | seed {method['seed']}")
            for i,row in enumerate(plotted):
                point=float(row[metric]);annotation=f'{point:.1f}'+('%' if letter=='F' else '')
                _treatment_interval(ax,i,point,float(row[lo]),float(row[hi]),row['treatment'],annotation)
            ax.axhline(0,color=TREATMENT_COLORS[TREATMENTS[0]],ls='--',lw=1.6)
            ax.set_xticks(range(7),[r['label'] for r in plotted]);ax.set_ylabel(ylabel)
            ax.set_xlabel('Mitigation treatment',labelpad=13);ax.set_xlim(-.55,6.65);ax.margins(y=.17)
            formula='Delta = median SSE(treatment) - median SSE(M0).' if letter=='E' else 'Reduction = 100 x [1 - median SSE(treatment) / median SSE(M0)].'
            direction='Negative difference' if letter=='E' else 'Positive reduction'
            _publication_footer(fig,[
                f"Matched n-level cluster-bootstrap sensitivity, {method['replicates']:,} replicates.",
                'Six n-level design blocks; cluster independence not established.',
                'Conditional 95% intervals; no significance established; recovery-job dependence and calibration drift may remain.'])
            layouts[letter]=_save_comparison(fig,figure_dir,name)
            layouts[letter].update(plot_order=[r['treatment'] for r in plotted],sort_metric=metric,
                                   sort_direction='ascending' if letter=='E' else 'descending',formula=formula)
            del fig,ax
    return layouts


def generate_figures_ef(result, *, figure_dir, table_dir):
    _,pd,_=_comparison_imports();records=result['statistics']
    pd.DataFrame(records).to_csv(Path(table_dir)/'M0_matched_n_sensitivity_statistics.csv',index=False,mode='x')
    render_figures_ef(records,result['manifest'],figure_dir)
    return records


def generate_cross_n_figure(summary, figure_dir):
    """Distinguish true n-level trends from order-dependent pooled prefixes."""
    _,_,plt=_comparison_imports()
    with plt.rc_context(STYLE):
        fig,axes=plt.subplots(2,2,figsize=(14,9))
        for index,(treatment,label) in enumerate(COMPARISON_TREATMENTS):
            group=sorted((r for r in summary if r['scope']=='n-level' and r['treatment']==treatment),key=lambda r:r['n'])
            for ax,(metric,stat) in zip(axes.flat,(('SSE','mean'),('SSE','median'),('RMSE','mean'),('RMSE','median'))):
                ax.plot([r['n'] for r in group],[r[f'{stat}_{metric}'] for r in group],
                        'o-',color=COLORS[index],label=label,linewidth=1.7,markersize=4)
                ax.set_xticks(NS);ax.set_xlabel('n (accepted PUBs per treatment)')
                ax.set_ylabel(f'{stat.title()} {metric}');ax.set_yscale('log')
        handles,labels=axes.flat[0].get_legend_handles_labels()
        fig.legend(handles,labels,loc='lower center',ncol=4,bbox_to_anchor=(.5,.015))
        fig.suptitle('Cross-n Descriptive Mitigation Performance',fontsize=17,fontweight='bold')
        fig.tight_layout(rect=(0,.12,1,.95))
        _save_comparison(fig,figure_dir,'Figure_cross_n_SSE_RMSE.png')


def main(argv=None):
    """All production comparison writes pass the canonical authorization gate."""
    from campaign_comparison import main as compare
    return compare(argv)


if __name__=='__main__':raise SystemExit(main())
