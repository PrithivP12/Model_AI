"""Versioned preplant crop-performance experiment; rotation outcomes are test-only.

County yield is not a field treatment effect. September 1 of the preceding year is
used so that winter wheat as well as next spring's crops have a preplant cutoff.
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import warnings
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler
from . import pilot_sources as src

OUT=src.OUT/'results'
MODES=['history','history_power','history_power_nldas']
KINDS=['ridge_10','ridge_100','hist_7_10','hist_15_10','hist_15_50','extra_5','extra_15']
BASELINES=['hist_last','hist_mean5','hist_mean10','hist_trend10','hist_trend15','hist_shrunk_trend']
PROTOCOL={
    'version':'county_preplant_v1','created_before_model_fitting':True,
    'target':'NASS county mean yield, bushels per acre; corn grain, soybeans, winter wheat',
    'region':'Illinois counties with reported individual-county yields',
    'decision':'September 1 of year BEFORE harvest; winter wheat is then still preplant',
    'latest_weather':'June 30 of year BEFORE harvest; two-month observation buffer',
    'yield_history':'Only yield years <= harvest year minus 2; excludes unharvested prior-year crop',
    'training_start':2005,'validation_years':[2020,2021,2022],
    'validation_fit_rule':'Fit only labels <= validation year minus 2, excluding spatial holdout',
    'calibration_year':2023,'final_test_years':[2024,2025],
    'test_fit_rule':'For each target year, refit frozen configurations on labels <= target year minus 2',
    'spatial_holdout':'All counties with interior-point longitude >= -89 and < -88; excluded from all fitting and model selection',
    'spatial_holdout_prior_history':'Historical yields of held-out counties remain available as prediction inputs; not a cold start test',
    'selection':'Mean of crop-macro MAPE across the three validation years; baselines compete too',
    'candidates':{'feature_groups':MODES,'regressors':KINDS,'baselines':BASELINES},
    'metrics':['crop-macro MAPE','within 10% observed yield','within 20% observed yield','per-crop MAE, RMSE, R2'],
    'accuracy_definition':'Within-tolerance rate, not 1-MAPE and not rotation strategy accuracy',
    'sources_trained':['USDA NASS ordinary county crop yields','NASA POWER monthly weather','NASA NLDAS Noah monthly June states/fluxes'],
    'not_trained':['rotation trial outcomes','rotation recommendations','EMP reference samples','GBIF occurrences','unmatched disease assays','raw crop SNPs'],
    'historical_availability':'Retrospective products and revised NASS snapshot; observation cutoffs enforced but publication/revision vintage not reconstructed',
    'final_test_exposure':'Raw 2025 availability and three sample rows were inspected before protocol; test metrics do not select configurations',
    'external_rotation_evaluation':{
        'training_use':False,'parameter_tuning_use':False,'yield_bonus_calibration_use':False,
        'dataset':'Bowles et al. Dryad 10.6078/D1H409; download only after candidate protocol is frozen',
        'primary_prediction':'Crop-yield ML has no sequence effect input: identical environment/crop/history => tied rotation predictions',
        'secondary_fixed_rule':'Rank maize-containing rotations by fraction of years without maize; no fitted weights or numerical yield bonus',
        'comparison':'Observed maize-yield ordering only within site/year/block/tillage/fertilization, averaged over plot replicates',
        'ties':'Abstain on equal predicted scores; report coverage and conditional directional agreement separately',
        'limitations':'External hypothesis check, not blind confirmation: literature abstract was read during source discovery; maize harvests only, not full-sequence profit or yield'
    },
    'scope':'Crop-performance forecasting plus a separately tested untrained rotation heuristic; no causal rotation optimizer'
}


def freeze_protocol():
    OUT.mkdir(parents=True,exist_ok=True)
    p=OUT/'protocol.json'
    if p.exists() and json.loads(p.read_text())!=PROTOCOL:raise RuntimeError('Version protocol instead of changing an existing experiment')
    p.write_text(json.dumps(PROTOCOL,indent=2)+'\n')


def yield_history(yields):
    """Each feature row reads only outcomes at least two harvest years earlier."""
    rows=[]
    for (county,crop),g in yields.groupby(['county_fips','crop']):
        g=g.sort_values('year')
        for row in g.itertuples():
            h=g[g.year<=row.year-2]
            if len(h)<5:continue
            r={'county_fips':county,'crop':crop,'year':row.year,'y':row.y,
               'yield_history_latest_year':int(h.year.max()),'hist_count':len(h),
               'hist_last':float(h.y.iloc[-1]),'hist_last_age':row.year-int(h.year.iloc[-1])}
            for n in [3,5,10,15]:
                v=h.tail(n)
                r[f'hist_mean{n}']=float(v.y.mean());r[f'hist_std{n}']=float(v.y.std())
            for n in [10,15]:
                v=h.tail(n);x=v.year.to_numpy(float);z=v.y.to_numpy(float)
                slope=float(np.sum((x-x.mean())*(z-z.mean()))/np.sum((x-x.mean())**2))
                r[f'hist_slope{n}']=slope
                r[f'hist_trend{n}']=max(1.,float(z.mean()+slope*(row.year-x.mean())))
            rows.append(r)
    d=pd.DataFrame(rows)
    # Statewide crop trend uses the same strictly past outcomes, not target-year peers.
    state=yields.groupby(['crop','year']).y.mean().reset_index()
    for (crop,year),indices in d.groupby(['crop','year']).groups.items():
        h=state[(state.crop==crop)&(state.year<=year-2)].tail(10)
        slope=np.polyfit(h.year.to_numpy()-year,h.y,1)
        d.loc[indices,'hist_state_trend']=max(1.,float(slope[1]))
    d['hist_shrunk_trend']=.5*d.hist_trend10+.5*d.hist_state_trend
    return d


def power_features(counties,weather):
    rows=[]
    for c in counties.itertuples():
        g=weather[(weather.grid_lat==c.grid_lat)&(weather.grid_lon==c.grid_lon)].copy()
        for year in range(2005,2026):
            cutoff=pd.Timestamp(year-1,7,1)
            prior=g[g.date<cutoff]
            r={'county_fips':c.county_fips,'year':year,'weather_latest_date':str(prior.date.max().date())}
            # Two prior years of monthly covariates, never the growing season forecast.
            for age in [1,2]:
                for month in range(1,13):
                    if age==1 and month>6:continue
                    v=prior[(prior.date.dt.year==year-age)&(prior.date.dt.month==month)]
                    for p in src.POWER_PARAMS.split(','):
                        r[f'power_{p}_age{age}_m{month:02d}']=float(v[p].iloc[0]) if len(v)==1 else np.nan
            # Climate normals built only with years <= target-2; monthly June of
            # target-1 is kept separately as the most recent land/weather state.
            h=prior[prior.date.dt.year<=year-2]
            for p in src.POWER_PARAMS.split(','):
                for season,months in [('warm',[4,5,6,7,8,9]),('cool',[10,11,12,1,2,3])]:
                    r[f'power_normal_{p}_{season}']=float(h[h.date.dt.month.isin(months)][p].mean())
            rows.append(r)
    return pd.DataFrame(rows)


def prepare():
    y=src.outcomes();c=src.counties()
    d=yield_history(y).merge(c[['county_fips','latitude','longitude','county_name']],on='county_fips',validate='many_to_one')
    p=pd.read_csv(src.OUT/'power_monthly.csv',parse_dates=['date'])
    w=power_features(c,p)
    d=d.merge(w,on=['county_fips','year'],validate='many_to_one')
    n=pd.read_csv(src.OUT/'nldas_june.csv',dtype={'county_fips':str})
    n['year']=n.source_year+1
    d=d.merge(n,on=['county_fips','year'],validate='many_to_one')
    d['spatial_holdout']=(d.longitude>=-89)&(d.longitude< -88)
    d['relative_target']=d.y/d.hist_trend10-1
    d=d[d.year>=2005].sort_values(['year','county_fips','crop']).reset_index(drop=True)
    assert not d.duplicated(['county_fips','crop','year']).any()
    assert (d.yield_history_latest_year<=d.year-2).all()
    assert (pd.to_datetime(d.weather_latest_date)<pd.to_datetime((d.year-1).astype(str)+'-07-01')).all()
    assert (d.source_year==d.year-1).all()
    d.to_csv(src.OUT/'matched_features.csv',index=False)
    audit={'records':len(d),'counties':d.county_fips.nunique(),'crops':d.groupby('crop').size().to_dict(),
           'years':d.groupby('year').size().to_dict(),'spatial_holdout_counties':sorted(d.loc[d.spatial_holdout,'county_fips'].unique().tolist()),
           'power_grid_points':len(c[['grid_lat','grid_lon']].drop_duplicates()),
           'nldas_monthly_files':int(n.source_year.nunique()),'nldas_features':[x for x in d if x.startswith('nldas_')],
           'rotation_outcome_files_read':False,'feature_release_vintage_reconstructed':False,
           'missing_features':{k:float(v) for k,v in d.isna().mean().items() if v>0}}
    (src.OUT/'matched_data_audit.json').write_text(json.dumps(audit,indent=2))
    return d


def feature_columns(d,mode):
    numeric=['year','latitude','longitude']+[c for c in d if c.startswith('hist_')]
    if mode in ['history_power','history_power_nldas']:numeric+=[c for c in d if c.startswith('power_')]
    if mode=='history_power_nldas':numeric+=[c for c in d if c.startswith('nldas_')]
    categorical=['crop','county_fips']
    assert not set(numeric+categorical)&{'y','relative_target','system','rotation','Previous_Crop','load_time','spatial_holdout'}
    return numeric,categorical


def make_model(kind,nums,cats):
    if kind.startswith('ridge'):
        prep=ColumnTransformer([('num',make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),StandardScaler()),nums),
                                ('cat',OneHotEncoder(handle_unknown='ignore'),cats)])
        return make_pipeline(prep,Ridge(alpha=float(kind.split('_')[1]),solver='lsqr'))
    prep=ColumnTransformer([('num',SimpleImputer(strategy='median',keep_empty_features=True),nums),
                            ('cat',OrdinalEncoder(handle_unknown='use_encoded_value',unknown_value=-1),cats)])
    if kind.startswith('hist'):
        _,leaves,reg=kind.split('_')
        model=HistGradientBoostingRegressor(loss='absolute_error',max_iter=160,learning_rate=.05,
            max_leaf_nodes=int(leaves),l2_regularization=float(reg),min_samples_leaf=25,early_stopping=False,random_state=2026)
    else:
        model=ExtraTreesRegressor(n_estimators=160,min_samples_leaf=int(kind.split('_')[1]),max_features=.7,n_jobs=2,random_state=2026)
    return make_pipeline(prep,model)


def metric(d,p):
    y=d.y.to_numpy();p=np.asarray(p);ape=abs(y-p)/y
    by={}
    for crop in sorted(d.crop.unique()):
        mask=(d.crop==crop).to_numpy();a=y[mask];b=p[mask]
        by[crop]={'records':int(mask.sum()),'mae_bu_acre':float(np.mean(abs(a-b))),
                  'rmse_bu_acre':float(np.sqrt(np.mean((a-b)**2))),'r2':float(r2_score(a,b)) if len(a)>1 else None,
                  'mape_percent':float(100*np.mean(abs(a-b)/a)),
                  'within_10_percent':float(np.mean(abs(a-b)/a<=.1))}
    return {'records':len(d),'macro_mape_percent':float(np.mean([v['mape_percent'] for v in by.values()])),
            'within_10_percent':float(np.mean(ape<=.1)),'within_20_percent':float(np.mean(ape<=.2)),
            'per_crop':by}


def train_predict(d,target_year,spec,save=False):
    fit=d[(d.year<=target_year-2)&~d.spatial_holdout].copy()
    test=d[d.year==target_year].copy()
    if spec['kind']=='baseline':
        p=test[spec['mode']].to_numpy();artifact={'baseline':spec['mode']}
    else:
        nums,cats=feature_columns(d,spec['mode']);features=nums+cats
        model=make_model(spec['kind'],nums,cats);model.fit(fit[features],fit.relative_target)
        p=test.hist_trend10.to_numpy()*(1+model.predict(test[features]))
        p=np.maximum(1.,p)
        artifact={'model':model,'features':features,'spec':spec,'target':'y / hist_trend10 - 1',
                  'latest_training_year':int(fit.year.max()),'protocol':PROTOCOL}
    if save:joblib.dump(artifact,OUT/f'{spec["key"].replace("|","_")}_{target_year}.joblib')
    return test,p


def bootstrap_gain(d,model,baseline,draws=2000):
    errors=pd.DataFrame({'county':d.county_fips,'crop':d.crop,'model':abs(d.y-model)/d.y,
                         'baseline':abs(d.y-baseline)/d.y})
    # Crop-macro mean within each resampled county cluster; both test years stay together.
    county=errors.groupby(['county','crop'])[['model','baseline']].agg(['sum','count'])
    counties=sorted(errors.county.unique());crops=sorted(errors.crop.unique())
    index=pd.MultiIndex.from_product([counties,crops],names=['county','crop'])
    sums=county.reindex(index).fillna(0).to_numpy().reshape(len(counties),len(crops),4)
    rng=np.random.default_rng(2026);gains=[]
    for _ in range(draws):
        sample=sums[rng.integers(0,len(counties),len(counties))].sum(axis=0)
        model_mean=sample[:,0]/sample[:,1];baseline_mean=sample[:,2]/sample[:,3]
        gains.append(100*float(np.mean(baseline_mean-model_mean)))
    m=errors.groupby('crop')[['model','baseline']].mean().mean()
    return {'macro_mape_gain_percentage_points':100*float(m['baseline']-m['model']),
            'county_cluster_bootstrap_95pct_interval':np.quantile(gains,[.025,.975]).tolist(),
            'caution':'Only two test years in one state; county clusters do not remove shared weather dependence.'}


def run():
    warnings.filterwarnings('ignore',message='Found unknown categories')
    freeze_protocol()
    if (OUT/'report.json').exists():raise RuntimeError('Preserve existing final results; create a versioned experiment for further tuning')
    d=prepare();specs=[];rows=[]
    for mode in MODES:
        specs.extend({'key':mode+'|'+kind,'mode':mode,'kind':kind} for kind in KINDS)
    specs.extend({'key':'baseline|'+name,'mode':name,'kind':'baseline'} for name in BASELINES)
    for spec in specs:
        fold=[]
        for year in PROTOCOL['validation_years']:
            v,p=train_predict(d,year,spec);keep=~v.spatial_holdout
            m=metric(v.loc[keep],p[keep]);fold.append(m['macro_mape_percent'])
            rows.append({**spec,'validation_year':year,**{k:m[k] for k in ['macro_mape_percent','within_10_percent','records']}})
        print('Validation',spec['key'],round(float(np.mean(fold)),3),'macro MAPE%',flush=True)
    cv=pd.DataFrame(rows);cv.to_csv(OUT/'validation_folds.csv',index=False)
    board=cv.groupby(['key','mode','kind']).macro_mape_percent.mean().sort_values().reset_index()
    board.to_csv(OUT/'validation_leaderboard.csv',index=False)
    selections={'overall':board.iloc[0].to_dict(),
                'baseline':board[board.kind=='baseline'].iloc[0].to_dict(),
                **{mode:board[board['mode']==mode].iloc[0].to_dict() for mode in MODES}}
    # The selection file is written BEFORE any 2023 calibration or 2024/25 scoring.
    (OUT/'selection_frozen.json').write_text(json.dumps(selections,indent=2))
    report={'protocol':PROTOCOL,'configurations':len(specs),'validation_fits':len(rows),
            'selections':selections,'final_test':{},'calibration':{},'rotation_accuracy_established':False}
    predictions={}
    for label,spec in selections.items():
        cal,cp=train_predict(d,2023,spec)
        # Geographic holdouts never calibrate intervals or tune hyperparameters.
        quantiles={}
        for crop,g in cal.loc[~cal.spatial_holdout].groupby('crop'):
            residual=abs(g.y.to_numpy()-cp[cal.index.get_indexer(g.index)])/g.hist_trend10.to_numpy()
            level=min(1.,np.ceil((len(residual)+1)*.9)/len(residual))
            quantiles[crop]=float(np.quantile(residual,level,method='higher'))
        report['calibration'][label]={'year':2023,'relative_interval_halfwidths':quantiles,'nominal_coverage':.9}
        frames=[]
        for year in PROTOCOL['final_test_years']:
            t,p=train_predict(d,year,spec,save=True)
            f=t[['county_fips','crop','year','y','spatial_holdout','hist_trend10']].copy();f['prediction']=p
            half=t.crop.map(quantiles).to_numpy()*t.hist_trend10.to_numpy()
            f['lower']=np.maximum(0,p-half);f['upper']=p+half;frames.append(f)
        f=pd.concat(frames,ignore_index=True);predictions[label]=f
        f.to_csv(OUT/f'{label}_test_predictions.csv',index=False)
        summary={'all_counties':metric(f,f.prediction),'seen_counties':metric(f.loc[~f.spatial_holdout],f.loc[~f.spatial_holdout,'prediction']),
                 'unseen_in_fitting_counties':metric(f.loc[f.spatial_holdout],f.loc[f.spatial_holdout,'prediction']),
                 'by_year':{str(y):metric(g,g.prediction) for y,g in f.groupby('year')},
                 'interval_empirical_coverage':float(((f.y>=f.lower)&(f.y<=f.upper)).mean()),
                 'interval_warning':'Year shift and revised data violate simple exchangeability assumptions; empirical coverage only.'}
        report['final_test'][label]=summary
        print('TEST',label,round(summary['all_counties']['macro_mape_percent'],3),'MAPE%',flush=True)
    base=predictions['baseline']
    report['baseline_comparisons']={label:bootstrap_gain(f,f.prediction,base.prediction.to_numpy())
                                    for label,f in predictions.items() if label!='baseline'}
    report['completed_utc']=datetime.now(timezone.utc).isoformat()
    report['limitations']=[
        'County averages reflect different fields and management for each crop; crop rankings are not counterfactual treatment effects.',
        'No numerical rotation yield benefit is learned; independent rotation outcomes are reserved for evaluation only.',
        'No matched cultivar, microbiome, pathogen-presence or pollinator training observations exist for these county records.',
        'NLDAS values are reanalysis/model estimates without a universal good/bad retrieval flag; source fills and units handled, no guarantee of on-field accuracy.',
        'Forecast lead is long; actual growing-season weather is deliberately unavailable.',
        'One state and two recent test years do not establish global or prospective accuracy.'
    ]
    (OUT/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    return report


if __name__=='__main__':run()
