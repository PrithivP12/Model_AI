"""Same folds, learner and controls isolate recorded management and biology."""
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold
from .benchmark import make_pipeline_for,scores,rotation_scores
from .io import sha256,write_json


def run_ablation(features,output):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed ablation')
    data=pd.read_parquet(features);data=data[(data.year<=2014)&~data.farm_id.isin(['KELLOGG','ORR','LAN'])].copy().reset_index(drop=True)
    base=[c for c in data if c.startswith(('climate_','soil_SOIL'))]+['latitude','longitude','year','planned_maturity','planned_seeding_rate']
    cats=['tillage','drainage','nitrogen_treatment','landscape','rotation_family','previous_crop'];management=['planting_doy','n_recorded_by_sowing_kg_ha']
    prior=[c for c in data if c.startswith('prior_bio_')];current=[c for c in data if c.startswith('inseason_bio_')]
    tasks={'base':(base,cats),'management':(base+management,cats+['seed_variety_recorded']),
        'management_prior_biology':(base+management+prior,cats+['seed_variety_recorded']),
        'management_prior_and_August_biology':(base+management+prior+current,cats+['seed_variety_recorded'])}
    folds=list(GroupKFold(n_splits=4).split(data,groups=data.farm_id));y=data.observed_yield_dry_kg_ha.to_numpy()
    protocol={'features_sha256':sha256(features),'development_years':[2011,2014],'excluded_years':[2015],
        'excluded_farms':['KELLOGG','ORR','LAN'],'learner':'HistGradientBoosting max_iter100 max_leaf_nodes7 L2=100 seed942',
        'folds':[{'fit_farms':sorted(data.iloc[a].farm_id.unique()),'evaluation_farms':sorted(data.iloc[b].farm_id.unique())}for a,b in folds],
        'interpretation':'Fixed development ablation; August biology is in-season only, never a preplant predictor',
        'no_cross_source_taxon_population_inference':True}
    write_json(out/'protocol_frozen.json',protocol);predictions=data[['record_id','farm_id','year']].copy();results={}
    for name,(numeric,categorical)in tasks.items():
        p=np.empty(len(data));data[categorical]=data[categorical].fillna('unknown').astype(str)
        for a,b in folds:
            fit=data.iloc[a];weights=1/fit.groupby('farm_id').record_id.transform('count').to_numpy();weights*=len(weights)/sum(weights)
            model=make_pipeline_for(HistGradientBoostingRegressor(max_iter=100,max_leaf_nodes=7,l2_regularization=100,early_stopping=False,random_state=942),numeric,categorical)
            model.fit(fit[numeric+categorical],y[a],histgradientboostingregressor__sample_weight=weights)
            p[b]=np.maximum(0,model.predict(data.iloc[b][numeric+categorical]))
        predictions[name]=p;results[name]={'yield':scores(y,p,data.farm_id),'rotation':rotation_scores(data,p)}
    predictions.to_csv(out/'predictions.csv',index=False)
    report={'status':'controlled_biological_ablation_completed','protocol':protocol,'models':results,
        'prior_biology_MAE_improvement_kg_dry_ha':results['management']['yield']['mae_kg_dry_ha']-results['management_prior_biology']['yield']['mae_kg_dry_ha'],
        'inseason_biology_MAE_improvement_kg_dry_ha':results['management_prior_biology']['yield']['mae_kg_dry_ha']-results['management_prior_and_August_biology']['yield']['mae_kg_dry_ha'],
        'recommendation':None,'fresh_independent_test':False,'ecosystem_causal_effect_established':False}
    write_json(out/'report.json',report);return report
