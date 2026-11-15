"""Farm-grouped development benchmarks; the exposed 2015 test is never re-tuned."""
from pathlib import Path
import json
import warnings
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from sklearn.linear_model import LinearRegression,Ridge,ElasticNet
from sklearn.ensemble import RandomForestRegressor,ExtraTreesRegressor,HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score
from .io import resolve,sha256,write_json


def scores(y,p,farms):
    y=np.asarray(y);p=np.asarray(p);err=p-y
    groups=pd.DataFrame({'farm':np.asarray(farms),'squared':err**2,'absolute':abs(err)})
    return {'records':len(y),'farms':len(set(farms)),'mae_kg_dry_ha':float(np.mean(abs(err))),
        'rmse_kg_dry_ha':float(np.sqrt(np.mean(err**2))),'bias_kg_dry_ha':float(np.mean(err)),
        'r2':float(r2_score(y,p))if len(y)>1 and np.var(y)>0 else None,
        'farm_macro_rmse_kg_dry_ha':float(np.sqrt(groups.groupby('farm').squared.mean()).mean())}


def make_pipeline_for(model,numbers,categories):
    prep=ColumnTransformer([
        ('numeric',make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True,add_indicator=True),StandardScaler()),numbers),
        ('category',make_pipeline(SimpleImputer(strategy='constant',fill_value='unknown'),
          OneHotEncoder(handle_unknown='ignore',sparse_output=False)),categories)])
    return make_pipeline(prep,model)


def models(seed):
    result={'linear':LinearRegression(),'ridge':Ridge(alpha=100),
        'elastic_net':ElasticNet(alpha=20,l1_ratio=.2,max_iter=5000,random_state=seed),
        'random_forest':RandomForestRegressor(n_estimators=100,min_samples_leaf=15,n_jobs=2,random_state=seed),
        'extra_trees':ExtraTreesRegressor(n_estimators=100,min_samples_leaf=15,n_jobs=2,random_state=seed),
        'hist_gradient_boosting':HistGradientBoostingRegressor(max_iter=100,max_leaf_nodes=7,l2_regularization=100,
            early_stopping=False,random_state=seed)}
    unavailable={}
    for name in ['catboost','xgboost','lightgbm']:
        try:
            if name=='catboost':
                from catboost import CatBoostRegressor
                result[name]=CatBoostRegressor(iterations=100,depth=3,learning_rate=.04,l2_leaf_reg=10,
                    loss_function='RMSE',random_seed=seed,thread_count=2,verbose=False,allow_writing_files=False)
            elif name=='xgboost':
                from xgboost import XGBRegressor
                result[name]=XGBRegressor(n_estimators=100,max_depth=2,learning_rate=.04,reg_lambda=20,
                    min_child_weight=15,n_jobs=2,random_state=seed)
            else:
                from lightgbm import LGBMRegressor
                result[name]=LGBMRegressor(n_estimators=100,num_leaves=7,min_child_samples=20,learning_rate=.04,
                    reg_lambda=20,n_jobs=2,random_state=seed,verbosity=-1)
        except (ImportError,OSError)as exc:unavailable[name]=str(exc)
    return result,unavailable


def rotation_scores(frame,predicted):
    d=frame.copy();d['prediction']=predicted
    keys=['site_id','year','replicate','tillage','drainage','nitrogen_treatment','landscape']
    pairs=[]
    for _,g in d.groupby(keys,dropna=False):
        a=g[g.rotation_family=='CC']
        if a.empty:continue
        for family,b in g[g.rotation_family!='CC'].groupby('rotation_family'):
            observed=float(b.observed_yield_dry_kg_ha.mean()-a.observed_yield_dry_kg_ha.mean())
            estimated=float(b.prediction.mean()-a.prediction.mean())
            pairs.append({'site_id':g.site_id.iloc[0],'farm':g.farm_id.iloc[0],
                'family':family,'observed_difference_kg_dry_ha':observed,'predicted_difference_kg_dry_ha':estimated})
    if not pairs:return {'pairs':0}
    pairs=pd.DataFrame(pairs);y=pairs.observed_difference_kg_dry_ha.to_numpy();p=pairs.predicted_difference_kg_dry_ha.to_numpy()
    decided=(abs(p)>1e-6)&(abs(y)>1e-6);correct=(np.sign(y)==np.sign(p))&decided
    return {'pairs':len(pairs),'independent_farms':int(pairs.farm.nunique()),
        'difference_rmse_kg_dry_ha':float(np.sqrt(np.mean((y-p)**2))),
        'difference_mae_kg_dry_ha':float(np.mean(abs(y-p))),
        'decisions':int(decided.sum()),'decision_coverage':float(decided.mean()),
        'directional_agreement':float(correct[decided].mean())if decided.any()else None,
        'always_choose_noncontinuous_maize_agreement':float(np.mean(y>0)),
        'mean_decision_regret_kg_dry_ha':float(np.where(correct[decided],0,abs(y[decided])).mean())if decided.any()else None,
        'causal_rotation_effect':False}


def benchmark(config,base):
    path=resolve(base,config['features']);out=resolve(base,config['output_dir'])
    if (out/'report.json').exists():raise RuntimeError('Preserve this completed benchmark; use a distinct output directory for a new protocol')
    out.mkdir(parents=True,exist_ok=True)
    data=pd.read_parquet(path)
    data=data[(data.year<=config['maximum_year'])&~data.farm_id.isin(config['excluded_farms'])].copy().reset_index(drop=True)
    if data.empty or data.year.max()>2014:raise ValueError('Development-only benchmark excludes the previously exposed 2015 test')
    climate=[c for c in data if c.startswith('climate_')]
    soil=[c for c in data if c.startswith('soil_SOIL')]
    numbers=climate+soil+['latitude','longitude','year','planned_maturity','planned_seeding_rate']
    controls=['tillage','drainage','nitrogen_treatment','landscape']
    categories=controls+['rotation_family','previous_crop']
    extra_numbers=config.get('extra_numeric',[])
    extra_categories=config.get('extra_categories',[])
    numbers+=extra_numbers;categories+=extra_categories
    for column in numbers+categories:
        if column not in data:raise ValueError('Requested predictor is missing: '+column)
        if column.startswith('inseason_')and config.get('prediction_horizon','').startswith(('preplant','retrospective planting')):
            raise ValueError('In-season observation requested for a planting-time model')
    features=numbers+categories
    forbidden={'observed_yield_dry_kg_ha','observed_yield_original','site_id','farm_id','plot_id','treatment_id','record_id','fertilizer_n_kg_ha'}
    if forbidden.intersection(features):raise ValueError('Target/identifier or ambiguous management leakage')
    data[categories]=data[categories].fillna('unknown').astype(str)
    seed=config.get('seed',942);candidates,unavailable=models(seed)
    specs={name:(model,numbers,categories)for name,model in candidates.items()}
    specs.update({
      'ablation_climate':(Ridge(alpha=100),climate+['latitude','longitude','year'],controls),
      'ablation_climate_soil':(Ridge(alpha=100),climate+soil+['latitude','longitude','year'],controls),
      'ablation_climate_soil_history':(Ridge(alpha=100),climate+soil+['latitude','longitude','year'],categories)})
    if extra_numbers or extra_categories:
        # Same learner isolates contribution of matched management/biology features.
        common=[c for c in numbers if c not in extra_numbers]
        common_cats=[c for c in categories if c not in extra_categories]
        specs['ablation_no_enrichment']=(HistGradientBoostingRegressor(max_iter=100,max_leaf_nodes=7,l2_regularization=100,early_stopping=False,random_state=seed),common,common_cats)
    splitter=GroupKFold(n_splits=min(4,data.farm_id.nunique()));folds=list(splitter.split(data,groups=data.farm_id))
    protocol={'seed':seed,'dataset_sha256':sha256(path),'features':features,'records':len(data),
        'target':'maize dry grain kg/ha','selection':'fixed model configurations; no hyperparameter search against out-of-fold predictions',
        'scope':'development cross-validation; not an untouched final test','excluded_farms':config['excluded_farms'],
        'prediction_horizon':config.get('prediction_horizon','retrospective preplant covariates'),
        'excluded_years':[2015],'ambiguous_aggregate_fertilizer_features_used':False,
        'folds':[{'fold':i,'training_farms':sorted(data.iloc[a].farm_id.unique()),'validation_farms':sorted(data.iloc[b].farm_id.unique())}for i,(a,b)in enumerate(folds)],
        'temporal':'fit 2011–2013 development farms; evaluate 2014 development farms; all reused research data'}
    write_json(out/'protocol_frozen.json',protocol)
    predictions=data[['record_id','farm_id','site_id','year','rotation_family','observed_yield_dry_kg_ha']].copy()
    report={'protocol':protocol,'models':{},'unavailable_models':unavailable,'recommendation_ready':False,
        'worldwide_accuracy_established':False,'status':'development_benchmark_completed',
        'limitations':['Grouped folds prevent same-farm plot leakage; geographic proximity between distinct farms may still matter.',
            'Current-year proposed management and pre-study NASA climate normals are retrospective covariates, not archived operational forecasts.',
            'Soil data are sparse, and many management values cannot be assigned reliably to plots.',
            'This is maize yield and observed cropping-system contrast prediction, not a validated global multi-crop optimizer.',
            'Previously exposed 2015 observations remain excluded. New independent validation is still required.',
            'Single-crop training data do not justify temporal deep networks or genome models.']}
    y=data.observed_yield_dry_kg_ha.to_numpy()
    tasks={'mean':None,**specs}
    for name,spec in tasks.items():
        prediction=np.empty(len(data));fold_metrics=[]
        for i,(a,b)in enumerate(folds):
            train=data.iloc[a];test=data.iloc[b]
            assert not set(train.farm_id)&set(test.farm_id)
            weights=1/train.groupby('farm_id').record_id.transform('count').to_numpy();weights*=len(weights)/weights.sum()
            if spec is None:p=np.full(len(b),np.average(y[a],weights=weights))
            else:
                model,nums,cats=spec;pipeline=make_pipeline_for(clone(model),nums,cats)
                pipeline.fit(train[nums+cats],y[a],**{pipeline.steps[-1][0]+'__sample_weight':weights})
                p=np.maximum(0,pipeline.predict(test[nums+cats]))
            prediction[b]=p;fold_metrics.append({'fold':i,**scores(y[b],p,test.farm_id)})
        predictions[name]=prediction
        early=data.year<2014;late=data.year==2014
        if spec is None:tp=np.full(late.sum(),y[early].mean())
        else:
            model,nums,cats=spec;pipeline=make_pipeline_for(clone(model),nums,cats)
            train=data[early];weights=1/train.groupby('farm_id').record_id.transform('count').to_numpy();weights*=len(weights)/weights.sum()
            pipeline.fit(train[nums+cats],y[early],**{pipeline.steps[-1][0]+'__sample_weight':weights})
            tp=np.maximum(0,pipeline.predict(data.loc[late,nums+cats]))
        report['models'][name]={'grouped_oof':scores(y,prediction,data.farm_id),'folds':fold_metrics,
            'rotation_contrasts':rotation_scores(data,prediction),
            'temporal_2014':scores(y[late],tp,data.loc[late,'farm_id']),
            'by_year':{str(year):scores(y[g.index],prediction[g.index],g.farm_id)for year,g in data.groupby('year')},
            'by_farm':{farm:scores(y[g.index],prediction[g.index],g.farm_id)for farm,g in data.groupby('farm_id')}}
        print(json.dumps({'completed':name,'grouped_mae':report['models'][name]['grouped_oof']['mae_kg_dry_ha']}),flush=True)
    predictions.to_csv(out/'out_of_fold_predictions.csv',index=False)
    leaderboard=sorted(report['models'],key=lambda name:report['models'][name]['grouped_oof']['farm_macro_rmse_kg_dry_ha'])
    report['development_leaderboard']=leaderboard
    report['predictions_sha256']=sha256(out/'out_of_fold_predictions.csv')
    write_json(out/'report.json',report)
    return report
