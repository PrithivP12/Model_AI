"""Audit decision evidence against stronger baselines and clustered uncertainty."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from .io import write_json


def clustered_comparison(predictions,model='prediction',baseline='family_mean',samples=2000,seed=817):
    d=predictions.copy();y=d.observed_difference.to_numpy();p=d[model].to_numpy();b=d[baseline].to_numpy()
    d['model_mae']=abs(p-y);d['baseline_mae']=abs(b-y)
    d['model_regret']=np.where(np.sign(p)==np.sign(y),0,abs(y))
    d['baseline_regret']=np.where(np.sign(b)==np.sign(y),0,abs(y))
    # Abstentions are not free decisions. This comparison is only on shared decisions.
    d=d[(abs(d[model])>1e-6)&(abs(d[baseline])>1e-6)]
    if d.empty:return {'comparable_decisions':0,'interval':None}
    farm=d.groupby('farm_id')[['model_mae','baseline_mae','model_regret','baseline_regret']].mean()
    deltas=np.column_stack([farm.model_mae-farm.baseline_mae,farm.model_regret-farm.baseline_regret])
    rng=np.random.default_rng(seed);draws=np.array([deltas[rng.integers(0,len(farm),len(farm))].mean(axis=0)for _ in range(samples)])
    bounds=np.quantile(draws,[.025,.975],axis=0)
    return {'comparable_decisions':len(d),'independent_farms':len(farm),'method':'farm-cluster bootstrap, equal weight per farm',
        'samples':samples,'seed':seed,'difference_convention':'model error minus baseline error; negative favors model',
        'mae_difference_kg_dry_ha':{'mean':float(deltas[:,0].mean()),'bootstrap_95_percent_range':[float(v)for v in bounds[:,0]]},
        'regret_difference_kg_dry_ha':{'mean':float(deltas[:,1].mean()),'bootstrap_95_percent_range':[float(v)for v in bounds[:,1]]},
        'caution':'Only nine development farms; range is exploratory and not a guaranteed deployment confidence interval.'}


def audit_release(project,output):
    project=Path(project);direct=project/'reports/reassessed/direct-rotation'
    if not(direct/'report.json').exists():raise FileNotFoundError('Run the current direct-rotation experiment before the release audit')
    report=json.loads((direct/'report.json').read_text());predictions=pd.read_csv(direct/'predictions.csv')
    comparison=clustered_comparison(predictions)
    calibrated=project/'reports/reassessed/calibration/report.json'
    process=json.loads(calibrated.read_text())if calibrated.exists()else None
    primary=report['models']['prediction'];baseline=report['models']['family_mean']
    conditions={'beats_crop_history_baseline_on_MAE':primary['mae_difference_kg_dry_ha']<baseline['mae_difference_kg_dry_ha'],
        'beats_crop_history_baseline_on_decision_regret':primary['mean_decision_regret_kg_dry_ha']<baseline['mean_decision_regret_kg_dry_ha'],
        'intervals_calibrated_to_nominal':report['interval']['coverage']>=.9,
        'some_intervals_support_a_direction':report['interval']['decisive_coverage']>0,
        'independent_new_region_final_test':False,
        'daily_water_model_beats_persistence':bool(process and process['soil_water_beats_persistence'])}
    result={'status':'scientific_release_gate_evaluated','checks':conditions,'all_checks_pass':all(conditions.values()),
        'farm_cluster_comparison':comparison,'recommendation':None,
        'deployment_status':'research_only','failed_checks':[k for k,v in conditions.items()if not v],
        'limits':['Passing software tests is independent from predictive skill.',
            'A new site/crop cannot inherit maize development accuracy by providing coordinates.',
            'The existing Australia wheat experiment is a separate crop/site model and failed its mean-yield baseline.']}
    write_json(output,result);return result
