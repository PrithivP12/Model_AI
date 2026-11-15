"""Reconstruction after source-QC correction; exposed tests stay labelled exposed."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from .calibration import simulate_case,summary
from .data.lirf import soil_prior,labels
from .io import write_json,sha256


def case_features(case,run):
    weather=case['weather'];events=case['events'];first=run['initial_assimilation']['date']
    return {'year':case['year'],'treatment':case['treatment'],
        'irrigation_mm':sum(e['irrigation_mm']for t,e in events.items()if first<t<=weather[-1]['date']),
        'rain_mm':sum(w['rain_mm']for w in weather if w['date']>first),
        'solar_MJ_m2':sum(w['solar_MJ_m2']for w in weather if w['date']>first),
        'et0_mm':sum(w['et0_mm']for w in weather if w['date']>first),
        'initial_water_mm':sum(v*(b-a)*10 for v,(a,b)in zip(case['soil_observations'][first],[(0,15),(15,45),(45,75),(75,105)])),
        'simulated_yield':run['predicted_yield_dry_kg_ha']}


def yield_metrics(y,p):
    y=np.asarray(y);p=np.asarray(p);error=p-y
    return {'records':len(y),'mae_kg_dry_ha':float(np.mean(abs(error))),
        'rmse_kg_dry_ha':float(np.sqrt(np.mean(error**2))),'bias_kg_dry_ha':float(np.mean(error)),
        'r2':float(1-np.sum(error**2)/np.sum((y-y.mean())**2))if np.var(y)>0 else None}


def run_corrected(project,prepared,selection_path,output):
    project=Path(project);prepared=Path(prepared);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Completed corrected evaluation preserved')
    data=json.loads((prepared/'lirf_cases_development.json').read_text());test=json.loads((prepared/'lirf_cases_test_inputs.json').read_text())
    selected=json.loads(Path(selection_path).read_text())['parameters'];prior=soil_prior(project,include_deep=True)
    protocol={'selected_parameters_sha256':sha256(selection_path),'parameters':selected,'fit_years':[2008,2009],
        'selection_year':2010,'evaluation_years':[2012,2013],'evaluation_years_previously_exposed':True,
        'reason':'Source weather corruption discovered after initial test scoring; corrected replay is a transparent reanalysis, not a fresh untouched test.',
        'forcing':'historical reconstruction using actual full-season weather and irrigation, conditional on an initial measured soil profile and optional canopy',
        'source_QC_fix':'2010-07-16 station -231.6 C / 80.1 C replaced using documented NASA POWER subset',
        'source_inputs':{p.name:sha256(p)for p in prepared.glob('lirf_cases*.json')},
        'kernel_sha256':sha256(project/'agrosim/processes.py'),'independent_sites':1,
        'hybrid_grid':[.1,1,10,100],'hybrid_selection':'2010 MAE only; never refit on test',
        'soil_water_scoring':'first measured profile initializes storage; only later measured profiles scored',
        'candidate_profile':'7 explicit layers to 225 cm; limited deep retention data and assumed deep organic pools disclosed in development protocol'}
    write_json(out/'protocol_frozen.json',protocol)
    old=[c for c in data if c['year']in [2008,2009,2010]]
    dev_runs=[simulate_case(c,prior,selected)for c in old];test_runs=[simulate_case(c,prior,selected)for c in test]
    baseline_runs=[simulate_case(c,soil_prior(project),{})for c in test]
    df=pd.DataFrame([case_features(c,r)for c,r in zip(old,dev_runs)]);tf=pd.DataFrame([case_features(c,r)for c,r in zip(test,test_runs)])
    observed={y:labels(project,y)for y in [2008,2009,2010]}
    y=np.array([observed[r['year']][r['treatment']]for r in dev_runs]);fit=df.year<2010;val=df.year==2010
    columns=['irrigation_mm','rain_mm','solar_MJ_m2','et0_mm','initial_water_mm'];predictions={'calibrated_simulator':tf.simulated_yield.to_numpy()}
    chosen={}
    for kind in ['ml_only','residual','state_feature_ml']:
        features=columns+['simulated_yield']if kind=='state_feature_ml'else columns
        offset=df.simulated_yield.to_numpy()if kind=='residual'else np.zeros(len(df))
        test_offset=tf.simulated_yield.to_numpy()if kind=='residual'else np.zeros(len(tf))
        candidates=[]
        for alpha in [.1,1,10,100]:
            model=make_pipeline(StandardScaler(),Ridge(alpha=alpha));model.fit(df.loc[fit,features],y[fit]-offset[fit])
            vp=np.maximum(0,model.predict(df.loc[val,features])+offset[val]);mae=float(np.mean(abs(vp-y[val])))
            candidates.append((mae,alpha,model))
        mae,alpha,model=min(candidates,key=lambda r:r[:2]);chosen[kind]={'alpha':alpha,'validation_mae_kg_dry_ha':mae}
        predictions[kind]=np.maximum(0,model.predict(tf[features])+test_offset)
    predictions['training_mean']=np.full(len(tf),float(y[fit].mean()))
    write_json(out/'hybrid_selection_frozen.json',chosen)
    frozen=tf[['year','treatment']].copy()
    for key,p in predictions.items():frozen[key]=p
    frozen.to_csv(out/'predictions_frozen.csv',index=False)
    # Labels are already exposed from the preserved earlier evaluation, never used above.
    test_yields={year:labels(project,year)for year in [2012,2013]};ty=np.array([test_yields[r['year']][r['treatment']]for r in test_runs])
    report={'status':'weather_QC_corrected_reconstruction_evaluated','protocol':protocol,
        'models':{name:yield_metrics(ty,p)for name,p in predictions.items()},
        'daily_engine':summary(test_runs,test_yields),'uncalibrated_daily_engine':summary(baseline_runs,test_yields),
        'per_year':{str(year):summary([r for r in test_runs if r['year']==year],test_yields)for year in [2012,2013]},
        'recommended_model':None,'recommendation':None,'fresh_independent_accuracy_claim':False,
        'soil_water_beats_persistence':summary(test_runs)['water_rmse_m3_m3']<summary(test_runs)['initial_profile_persistence_rmse_m3_m3'],
        'predictions_sha256':sha256(out/'predictions_frozen.csv'),
        'limits':['Single farm, treatment means are not independent regions.',
            'Observed weather/irrigation reconstruction cannot establish preplant performance.',
            '2012–13 initial mineral N, root N and deep organic stocks have assumed components.',
            'Corrected reanalysis does not erase initial failed validation; both are retained.']}
    write_json(out/'process_predictions.json',test_runs);write_json(out/'report.json',report)
    return report
