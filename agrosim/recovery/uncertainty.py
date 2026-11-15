"""Grouped interval diagnostics without pretending reused pairs are independent."""
from pathlib import Path
import json,math
import numpy as np
import pandas as pd
from ..io import write_json,sha256
from ..rotation_learning import paired_records,baseline


def conformal_radius(scores,coverage=.9):
    scores=np.asarray(scores,dtype=float)
    if not 0<coverage<1 or not len(scores)or not np.isfinite(scores).all()or (scores<0).any():raise ValueError('Finite nonnegative calibration scores and valid coverage required')
    rank=math.ceil((len(scores)+1)*coverage)
    return None if rank>len(scores)else float(np.sort(scores)[rank-1])


def cluster_bootstrap(frame,reference='family_mean',candidate='prediction',draws=4000):
    groups=[]
    for farm,g in frame.groupby('farm_id'):
        y=g.observed_difference.to_numpy();a=g[candidate].to_numpy();b=g[reference].to_numpy()
        groups.append([np.mean(abs(a-y)-abs(b-y)),np.mean((np.sign(a)==np.sign(y)).astype(float)-(np.sign(b)==np.sign(y)).astype(float))])
    groups=np.asarray(groups);rng=np.random.default_rng(817)
    samples=groups[rng.integers(0,len(groups),(draws,len(groups)))].mean(axis=1)
    return {'farms':len(groups),'resampling_unit':'farm; preserves all dependent contrasts within a farm',
        'macro_mae_change_kg_ha':float(groups[:,0].mean()),'mae_change_95pct_percentile_interval':np.quantile(samples[:,0],[.025,.975]).tolist(),
        'macro_directional_agreement_change':float(groups[:,1].mean()),'directional_change_95pct_percentile_interval':np.quantile(samples[:,1],[.025,.975]).tolist(),
        'interpretation':'Exploratory uncertainty across nine exposed development farms; correlated fitted folds and geographic selection limit inference.'}


def run_uncertainty(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed uncertainty audit')
    source=root/'reports/reassessed/direct-rotation';legacy=json.loads((source/'report.json').read_text())
    predictions=pd.read_csv(source/'predictions.csv').copy()
    d=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet')
    pairs=paired_records(d[(d.year<=2014)&~d.farm_id.isin(['KELLOGG','ORR','LAN'])])
    protocol={'coverage':.9,'model':'fixed strongest simple family-history baseline',
        'folds':'Reuse exact outer farm partition from prior nested development evaluation',
        'calibration':'same explicitly reserved farm as previous experiment; zero overlap with fitting/test farms',
        'scores':'Maximum absolute residual per calibration farm, not reused pair residual counts',
        'new_independent_test':False,'source_sha256':sha256(source/'predictions.csv')}
    write_json(out/'protocol_frozen.json',protocol);rows=[];folds=[]
    for fold in legacy['folds']:
        train=pairs[pairs.farm_id.isin(fold['fit_farms'])];cal=pairs[pairs.farm_id==fold['calibration_farm']];test=pairs[pairs.farm_id.isin(fold['test_farms'])]
        assert not set(train.farm_id)&set(cal.farm_id)|set(train.farm_id)&set(test.farm_id)|set(cal.farm_id)&set(test.farm_id)
        score=cal.assign(residual=abs(baseline(train,cal,'family')-cal.observed_difference)).groupby('farm_id').residual.max()
        radius=conformal_radius(score,.9);p=baseline(train,test,'family')
        # Null bounds explicitly encode unbounded intervals; JSON Infinity is forbidden.
        for (_,record),value in zip(test.iterrows(),p):rows.append({'pair_id':record.pair_id,'farm_id':record.farm_id,'fold':fold['fold'],
            'observed_difference':record.observed_difference,'prediction':float(value),'lower':None if radius is None else float(value-radius),
            'upper':None if radius is None else float(value+radius),'unbounded':radius is None,'decisive':False if radius is None else abs(value)>radius})
        folds.append({'fold':fold['fold'],'fit_farms':fold['fit_farms'],'calibration_farms':[fold['calibration_farm']],
            'test_farms':fold['test_farms'],'calibration_groups':len(score),'halfwidth_kg_ha':radius,'unbounded':radius is None})
    pd.DataFrame(rows).to_csv(out/'grouped_predictions.csv',index=False)
    oldcovered=(predictions.observed_difference>=predictions.lower)&(predictions.observed_difference<=predictions.upper)
    report={'status':'group_calibration_limitation_identified','protocol':protocol,'folds':folds,
        'previous_row_residual_intervals':{'coverage':float(oldcovered.mean()),'mean_width_kg_dry_ha':float((predictions.upper-predictions.lower).mean()),
            'farm_macro_coverage':float(predictions.assign(covered=oldcovered).groupby('farm_id').covered.mean().mean()),
            'decisive_fraction':float(((predictions.lower>0)|(predictions.upper<0)).mean()),'new_farm_coverage_guaranteed':False},
        'group_validity_attempt':{'coverage':1.,'coverage_is_vacuous':True,'mean_width_kg_dry_ha':None,'width_status':'unbounded',
            'decisive_fraction':0.,'reason':'One independent calibration farm per fold cannot support a finite distribution-free nominal 90% new-farm interval. At least nine exchangeable calibration farms are required, additional to model fitting and final evaluation farms.',
            'exchangeability_verified':False,'conditional_on_exchangeability_only':True},
        'complex_vs_family_cluster_bootstrap':cluster_bootstrap(predictions),
        'selected_default':'family_history_baseline','new_accuracy_established':False,'recommendation':None,
        'next_calibration_plan':'Freeze process/contrast predictors, collect or reserve at least 9 independent calibration farms with paired outcomes and separate final farms, then evaluate group simultaneous coverage, width and useful decisions without retuning.'}
    write_json(out/'report.json',report);return report
