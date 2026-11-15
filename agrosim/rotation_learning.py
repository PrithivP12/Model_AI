"""Direct observed yield contrasts with nested farm validation and abstention."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor,ExtraTreesRegressor
from sklearn.model_selection import GroupKFold
from .benchmark import make_pipeline_for
from .io import write_json,sha256


def paired_records(frame):
    rows=[];keys=['site_id','year','replicate','tillage','drainage','nitrogen_treatment','landscape']
    numerical=['latitude','longitude','year','planting_doy','n_recorded_by_sowing_kg_ha']+[c for c in frame if c.startswith(('climate_','soil_SOIL','prior_bio_'))]
    for values,g in frame.groupby(keys,dropna=False):
        a=g[g.rotation_family=='CC']
        if a.empty:continue
        for family,b in g[g.rotation_family!='CC'].groupby('rotation_family'):
            r={**dict(zip(keys,values)),'farm_id':g.farm_id.iloc[0],'family':family,
               'observed_difference':float(b.observed_yield_dry_kg_ha.mean()-a.observed_yield_dry_kg_ha.mean()),
               'reference_records':';'.join(a.record_id),'candidate_records':';'.join(b.record_id)}
            for c in numerical:
                av=float(a[c].mean())if a[c].notna().any()else np.nan;bv=float(b[c].mean())if b[c].notna().any()else np.nan
                if c in ['latitude','longitude','year']or c.startswith('climate_'):r[c]=bv
                else:r[c+'_reference']=av;r[c+'_candidate']=bv;r[c+'_difference']=bv-av
            rows.append(r)
    result=pd.DataFrame(rows);result['pair_id']=[f'PAIR-{i:05d}'for i in range(len(result))];return result


def baseline(train,test,kind):
    if kind=='zero':return np.zeros(len(test))
    # Equal farm contribution, not equal reused plot-pair contribution.
    mean=float(train.groupby('farm_id').observed_difference.mean().mean())
    if kind=='mean':return np.full(len(test),mean)
    values=train.groupby(['family','farm_id']).observed_difference.mean().groupby('family').mean()
    return test.family.map(values).fillna(mean).to_numpy(dtype=float)


def fit_predict(train,test,spec,numbers,categories):
    if isinstance(spec,str):return baseline(train,test,spec)
    weights=1/train.groupby('farm_id').pair_id.transform('count').to_numpy();weights*=len(weights)/weights.sum()
    pipeline=make_pipeline_for(clone(spec),numbers,categories)
    pipeline.fit(train[numbers+categories],train.observed_difference,
                 **{pipeline.steps[-1][0]+'__sample_weight':weights})
    return pipeline.predict(test[numbers+categories])


def metrics(frame,column):
    y=frame.observed_difference.to_numpy();p=frame[column].to_numpy();error=p-y
    grouped=frame.assign(squared=error**2).groupby('farm_id').squared.mean()
    decided=abs(p)>1e-6;correct=np.sign(p)==np.sign(y)
    return {'pairs':len(frame),'farms':int(frame.farm_id.nunique()),'mae_difference_kg_dry_ha':float(np.mean(abs(error))),
        'rmse_difference_kg_dry_ha':float(np.sqrt(np.mean(error**2))),'farm_macro_rmse_kg_dry_ha':float(np.sqrt(grouped).mean()),
        'decisions':int(decided.sum()),'decision_coverage':float(np.mean(decided)),
        'directional_agreement':float(np.mean(correct[decided]))if decided.any()else None,
        'mean_decision_regret_kg_dry_ha':float(np.mean(np.where(correct[decided],0,abs(y[decided]))))if decided.any()else None,
        'always_choose_diversified_agreement':float(np.mean(y>0)),
        'always_choose_diversified_regret_kg_dry_ha':float(np.mean(np.maximum(0,-y)))}


def train_rotation(features,output):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed direct-rotation experiment')
    d=pd.read_parquet(features);d=d[(d.year<=2014)&~d.farm_id.isin(['KELLOGG','ORR','LAN'])].copy()
    pairs=paired_records(d)
    categories=['family','tillage','drainage','nitrogen_treatment','landscape']
    excluded={'observed_difference','farm_id','site_id','pair_id','replicate','reference_records','candidate_records'}
    numbers=[c for c in pairs if c not in excluded and c not in categories]
    pairs[categories]=pairs[categories].fillna('unknown').astype(str)
    candidates={'zero':'zero','mean':'mean','family_mean':'family','ridge_100':Ridge(alpha=100),
        'ridge_1000':Ridge(alpha=1000),'hist':HistGradientBoostingRegressor(max_iter=100,max_leaf_nodes=5,min_samples_leaf=15,l2_regularization=100,early_stopping=False,random_state=817),
        'extra':ExtraTreesRegressor(n_estimators=100,min_samples_leaf=20,max_features=.6,random_state=817,n_jobs=2)}
    outer=list(GroupKFold(n_splits=min(4,pairs.farm_id.nunique())).split(pairs,groups=pairs.farm_id))
    protocol={'source_features_sha256':sha256(features),'target':'observed candidate minus continuous-maize yield, kg dry grain/ha',
        'outer_folds':4,'selection':'inner farm-grouped CV; one additional training farm reserved for interval calibration',
        'scope':'development nested cross-validation; all previously exposed 2015 outcomes excluded',
        'features':numbers+categories,'causal_rotation_identification':False,
        'sampling_units':'Pairs reuse reference plots. Farms, not pair counts, determine independence.'}
    write_json(out/'protocol_frozen.json',protocol);all_predictions=[];selection=[]
    for fold,(a,b)in enumerate(outer):
        outer_train=pairs.iloc[a];test=pairs.iloc[b];farms=sorted(outer_train.farm_id.unique())
        calibration_farm=farms[fold%len(farms)];train=outer_train[outer_train.farm_id!=calibration_farm];cal=outer_train[outer_train.farm_id==calibration_farm]
        if train.farm_id.nunique()<2:raise ValueError('Insufficient independent training farms')
        inner=list(GroupKFold(n_splits=min(3,train.farm_id.nunique())).split(train,groups=train.farm_id))
        leaderboard=[]
        for name,spec in candidates.items():
            p=np.empty(len(train))
            for x,z in inner:p[z]=fit_predict(train.iloc[x],train.iloc[z],spec,numbers,categories)
            temporary=train.assign(prediction=p);leaderboard.append({'model':name,**metrics(temporary,'prediction')})
        selected=min(leaderboard,key=lambda r:r['farm_macro_rmse_kg_dry_ha'])['model'];spec=candidates[selected]
        cp=fit_predict(train,cal,spec,numbers,categories);residual=abs(cp-cal.observed_difference.to_numpy())
        width=float(np.quantile(residual,min(1,np.ceil((len(residual)+1)*.9)/len(residual)),method='higher'))
        predictions=test.copy();predictions['prediction']=fit_predict(train,test,spec,numbers,categories)
        predictions['lower']=predictions.prediction-width;predictions['upper']=predictions.prediction+width;predictions['fold']=fold
        for name in ['zero','mean','family_mean']:predictions[name]=fit_predict(train,test,candidates[name],numbers,categories)
        all_predictions.append(predictions)
        selection.append({'fold':fold,'selected':selected,'fit_farms':sorted(train.farm_id.unique()),'calibration_farm':calibration_farm,
                          'test_farms':sorted(test.farm_id.unique()),'interval_halfwidth_kg_dry_ha':width,'inner_leaderboard':leaderboard})
        print('completed direct-rotation fold',fold,selected,flush=True)
    scored=pd.concat(all_predictions).sort_index();scored.to_csv(out/'predictions.csv',index=False)
    intervals={'coverage':float(np.mean((scored.observed_difference>=scored.lower)&(scored.observed_difference<=scored.upper))),
        'decisive_coverage':float(np.mean((scored.lower>0)|(scored.upper<0))),
        'coverage_guaranteed_on_new_geography':False}
    report={'status':'direct_rotation_nested_development_evaluation','protocol':protocol,'folds':selection,
        'models':{name:metrics(scored,name)for name in ['prediction','zero','mean','family_mean']},'interval':intervals,
        'predictions_sha256':sha256(out/'predictions.csv'),'recommendation':None,'worldwide_accuracy_established':False,
        'limits':['Actual recorded operations and crop histories, not archived operational forecasts.',
            'Treatment-label matching does not remove all dose, soil, cultivar and management confounding.',
            'One farm supplies each fold calibration interval; inter-farm exchangeability is unproven.']}
    write_json(out/'report.json',report);return report
