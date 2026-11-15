"""Forward-year maize yield experiment with strict feature allowlists.

No crop sequence variables. Parent identifiers are pedigree proxies, not SNPs.
Preplant features stop at March 1; hindsight features are evaluated separately.
"""
from pathlib import Path
import hashlib
import json
import warnings
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
G2F = ROOT/'g2f'
CAT = ['Field_Location', 'Hybrid_Parent1', 'Hybrid_Parent2']
STABLE_SOIL = ['1:1 Soil pH', 'Organic Matter LOI %', 'CEC/Sum of Cations me/100g', '% Sand', '% Silt', '% Clay']
PROTOCOL = {'target':'Environment x hybrid mean maize grain yield, Mg/ha',
    'train_years':[2014,2015,2016,2017,2018], 'validation_year':2019,
    'calibration_year':2020, 'final_test_year':2021,
    'selection':'Validation MAE, independently within preplant and hindsight tasks',
    'genetic_input':'Parent identifiers only; SNP file unavailable (mirror contains a macOS alias)',
    'preplant_cutoff':'March 1 of target year; historical climate uses strictly earlier years',
    'rotation_data_used':False, 'pollinator_or_microbiome_training_inputs':False,
    'forbidden':['Previous_Crop','Treatment','Yield_Mg_ha as input','Grain_Moisture','Plant_Height_cm','Date_Harvested','pollen/silk dates','current-season weather in preplant task'],
    'external_validation':False, 'scope':'Maize trials; a yield component, not a learned rotation optimizer'}


def metrics(y, p):
    return dict(mae=float(mean_absolute_error(y,p)),rmse=float(np.sqrt(mean_squared_error(y,p))),r2=float(r2_score(y,p)))


def numeric(s):
    return pd.to_numeric(s.astype(str).str.replace(',','',regex=False),errors='coerce').replace([-999,-9999],np.nan)


def prepare():
    raw=G2F/'raw'
    # Outcome plus only predeclared identifiers. All measured postplant traits excluded.
    trait=pd.read_csv(raw/'1_Training_Trait_Data_2014_2021.csv',usecols=['Env','Year','Field_Location','Hybrid','Hybrid_Parent1','Hybrid_Parent2','Yield_Mg_ha'])
    trait['Yield_Mg_ha']=pd.to_numeric(trait.Yield_Mg_ha,errors='coerce')
    valid=trait.Yield_Mg_ha.notna() & np.isfinite(trait.Yield_Mg_ha) & (trait.Yield_Mg_ha>=0)
    excluded=int((~valid).sum());trait=trait.loc[valid].copy()
    for c in CAT+['Hybrid']:trait[c]=trait[c].fillna('__unknown__').astype(str)
    # These identifiers are unordered pedigree proxies, not maternal/paternal genetics.
    pairs=np.sort(trait[['Hybrid_Parent1','Hybrid_Parent2']].to_numpy(),axis=1)
    trait[['Hybrid_Parent1','Hybrid_Parent2']]=pairs
    group=['Env','Year','Field_Location','Hybrid','Hybrid_Parent1','Hybrid_Parent2']
    data=trait.groupby(group,dropna=False).agg(y=('Yield_Mg_ha','mean'),replicates=('Yield_Mg_ha','size')).reset_index()
    conflicts=data.duplicated(['Env','Hybrid'],keep=False)
    conflicting_records=int(conflicts.sum())
    data=data.loc[~conflicts].copy()
    assert not data.duplicated(['Env','Hybrid']).any()
    meta=pd.read_csv(raw/'2_Training_Meta_Data_2014_2021.csv',encoding='cp1252',low_memory=False)
    lat=next(c for c in meta if c.startswith('Weather_Station_Latitude'))
    lon=next(c for c in meta if c.startswith('Weather_Station_Longitude'))
    # Never join previous-crop, treatment, management comments, or other excluded fields.
    coords=meta[['Env',lat,lon]].copy().rename(columns={lat:'latitude',lon:'longitude'})
    coords.latitude=numeric(coords.latitude);coords.longitude=numeric(coords.longitude)
    coords=coords.groupby('Env')[['latitude','longitude']].median().reset_index()
    coords.loc[~coords.latitude.between(-90,90),'latitude']=np.nan
    coords.loc[~coords.longitude.between(-180,180),'longitude']=np.nan
    data=data.merge(coords,on='Env',how='left',validate='many_to_one')
    weather=pd.read_csv(raw/'4_Training_Weather_Data_2014_2021.csv')
    weather['date']=pd.to_datetime(weather.Date.astype(str),format='%Y%m%d',errors='raise')
    if weather.duplicated(['Env','date']).any():raise ValueError('Duplicate environment weather dates')
    weather['month']=weather.date.dt.month
    selected=['T2M','T2M_MAX','T2M_MIN','PRECTOTCORR','RH2M','ALLSKY_SFC_SW_DWN','GWETROOT']
    weather[selected]=weather[selected].apply(pd.to_numeric,errors='coerce').replace([-999,-9999],np.nan)
    monthly=weather.groupby(['Env','month'])[selected].mean().unstack('month')
    monthly.columns=[f'wx_{c}_m{m:02d}' for c,m in monthly.columns]
    monthly=monthly.reset_index()
    envs=data[['Env','Year','Field_Location']].drop_duplicates().merge(monthly,on='Env',how='left',validate='one_to_one')
    wcols=[c for c in monthly if c!='Env']
    # Build climate normals without target-year or future-year weather leakage.
    hist=[]
    for _,r in envs.iterrows():
        earlier=envs[(envs.Field_Location==r.Field_Location)&(envs.Year<r.Year)]
        h={'Env':r.Env,'climate_history_years':earlier.Year.nunique()}
        h.update({c.replace('wx_','hist_'):earlier[c].mean() for c in wcols})
        hist.append(h)
    data=data.merge(monthly,on='Env',how='left',validate='many_to_one').merge(pd.DataFrame(hist),on='Env',how='left',validate='many_to_one')
    soil=pd.read_csv(raw/'3_Training_Soil_Data_2015_2021.csv',encoding='cp1252')
    soil=soil[['Env']+STABLE_SOIL].copy()
    for c in STABLE_SOIL:soil[c]=numeric(soil[c])
    soil=soil.groupby('Env')[STABLE_SOIL].median().add_prefix('soil_').reset_index()
    data=data.merge(soil,on='Env',how='left',validate='many_to_one')
    data=data.replace([np.inf,-np.inf],np.nan)
    # Geographic exclusions are explicit. Do not invent coordinates for missing metadata.
    audit={'plot_rows_loaded':len(valid),'invalid_yield_rows_excluded':excluded,'environment_hybrid_records':len(data),
        'conflicting_environment_hybrid_records_excluded':conflicting_records,'parent_order':'Unordered pair; maternal effects not modeled',
        'environments':data.Env.nunique(),'field_locations':data.Field_Location.nunique(),
        'years':{str(k):int(v) for k,v in data.Year.value_counts().sort_index().items()},
        'location_missing_fraction':float(data.latitude.isna().mean()),
        'join_keys':['Env','Hybrid','parent identifiers'],
        'soil_timing':'Stable properties included only in hindsight task; collection/report dates may be after planting.',
        'historical_climate_rule':'All available earlier-year weather at exact same Field_Location; no future rows.',
        'aggregation':'Plot replicates averaged within Env x Hybrid before splitting.',
        'previous_crop_columns_joined':False}
    G2F.mkdir(exist_ok=True)
    data.to_parquet(G2F/'prepared.parquet',index=False)
    (G2F/'data_audit.json').write_text(json.dumps(audit,indent=2))
    return data


def columns(data, mode, parents):
    cats=CAT if parents else CAT[:1]
    nums=['latitude','longitude']
    if mode=='preplant':
        nums+=['climate_history_years']+[c for c in data if c.startswith('hist_')]
        nums+=[c for c in data if c.startswith('wx_') and c.endswith(('_m01','_m02'))]
    else:
        nums+=[c for c in data if c.startswith(('wx_','soil_'))]
    assert not set(nums+cats)&set(PROTOCOL['forbidden'])
    return nums,cats


def make_model(kind, nums, cats):
    if kind.startswith('ridge'):
        prep=ColumnTransformer([('num',make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),StandardScaler()),nums),
                                ('cat',OneHotEncoder(handle_unknown='ignore'),cats)])
        return make_pipeline(prep,Ridge(alpha=float(kind.split('_')[1]),solver='lsqr'))
    prep=ColumnTransformer([('num',SimpleImputer(strategy='median',keep_empty_features=True),nums),
                            ('cat',OrdinalEncoder(handle_unknown='use_encoded_value',unknown_value=np.nan,max_categories=128),cats)])
    if kind.startswith('hist'):
        leaves,reg=map(int,kind.split('_')[1:])
        est=HistGradientBoostingRegressor(max_iter=180,learning_rate=.07,max_leaf_nodes=leaves,l2_regularization=reg,
              min_samples_leaf=30,early_stopping=False,categorical_features=[False]*len(nums)+[True]*len(cats),random_state=2026)
    else:
        est=ExtraTreesRegressor(n_estimators=120,min_samples_leaf=10,max_features=.7,n_jobs=2,random_state=2026)
        # Trees require finite unknown category values on some sklearn versions.
        return make_pipeline(prep,SimpleImputer(strategy='constant',fill_value=-1,keep_empty_features=True),est)
    return make_pipeline(prep,est)


def cluster_bootstrap(data, pred, base, draws=1000):
    errors=pd.DataFrame({'env':data.Env.to_numpy(),'model':np.abs(data.y.to_numpy()-pred),'baseline':np.abs(data.y.to_numpy()-base)})
    groups=errors.groupby('env')[['model','baseline']].mean().to_numpy()
    rng=np.random.default_rng(46);values=[]
    for _ in range(draws):
        sample=groups[rng.integers(0,len(groups),len(groups))]
        values.append(float(np.mean(sample[:,1]-sample[:,0])))
    return {'environment_weighted_mae_gain':float(np.mean(groups[:,1]-groups[:,0])),
            'bootstrap_95pct_interval':np.quantile(values,[.025,.975]).tolist(),
            'caution':'Environment-cluster bootstrap within one year; not a global guarantee.'}


def run():
    warnings.filterwarnings('ignore',message='Found unknown categories')
    out=G2F/'results';out.mkdir(parents=True,exist_ok=True)
    # Protocol is persisted before selection. Existing final results are not silently overwritten.
    if (out/'report.json').exists():raise RuntimeError('Final report already exists. Preserve it; use a separately versioned experiment.')
    (out/'protocol.json').write_text(json.dumps(PROTOCOL,indent=2))
    d=prepare()
    train=d[d.Year<=2018].copy();val=d[d.Year==2019].copy();cal=d[d.Year==2020].copy();test=d[d.Year==2021].copy()
    kinds=['ridge_10','ridge_100','hist_7_10','hist_15_10','hist_31_30','extra_trees']
    rows=[];specs={}
    for mode in ['preplant','hindsight']:
        for parents in [False,True]:
            nums,cats=columns(d,mode,parents);cols=nums+cats
            for kind in kinds:
                model=make_model(kind,nums,cats);model.fit(train[cols],train.y)
                p=model.predict(val[cols]);key=f'{mode}|parents={parents}|{kind}'
                rows.append({'key':key,'mode':mode,'parents':parents,'kind':kind,**metrics(val.y,p)})
                specs[key]=(model,cols)
                print('Validated',key,round(rows[-1]['mae'],3),flush=True)
    leaderboard=pd.DataFrame(rows).sort_values('mae');leaderboard.to_csv(out/'validation_leaderboard.csv',index=False)
    selected={mode:leaderboard[leaderboard['mode']==mode].iloc[0].to_dict() for mode in ['preplant','hindsight']}
    # Freeze both task winners and ablation winners BEFORE evaluating any final labels.
    frozen={'task_winners':selected,'ablations':{f'{mode}|parents={parents}':leaderboard[(leaderboard['mode']==mode)&(leaderboard.parents==parents)].iloc[0].to_dict()
             for mode in ['preplant','hindsight'] for parents in [False,True]}}
    (out/'selection_frozen.json').write_text(json.dumps(frozen,indent=2))
    fit=pd.concat([train,val],ignore_index=True)
    mean_base=np.full(len(test),fit.y.mean())
    site_mean=fit.groupby('Field_Location').y.mean()
    site_base=test.Field_Location.map(site_mean).fillna(fit.y.mean()).to_numpy()
    report={'protocol':PROTOCOL,'candidate_count':len(rows),'split_records':{'fit':len(fit),'calibration':len(cal),'test':len(test)},
            'final_test_environments':test.Env.nunique(),'baselines':{'global_mean':metrics(test.y,mean_base),'historical_site_mean':metrics(test.y,site_base)},'tasks':{},'ablations':{}}
    seen=set(fit.Field_Location);unknown=~test.Field_Location.isin(seen)
    report['new_site_test_records']=int(unknown.sum())
    for task,row in frozen['ablations'].items():
        key=row['key'];_,cols=specs[key];nums,cats=columns(d,row['mode'],bool(row['parents']))
        model=make_model(row['kind'],nums,cats);model.fit(fit[cols],fit.y)
        cp=model.predict(cal[cols]);p=model.predict(test[cols])
        # Approximate split-conformal marginal interval; year shift breaks exchangeability.
        residual=np.abs(cal.y.to_numpy()-cp);q=float(np.quantile(residual,min(1,np.ceil((len(residual)+1)*.9)/len(residual)),method='higher'))
        lower=p-q;upper=p+q
        summary={'selected_configuration':key,'test':metrics(test.y,p),'interval_nominal_coverage':.9,
                 'interval_empirical_coverage':float(np.mean((test.y>=lower)&(test.y<=upper))),
                 'interval_halfwidth_Mg_ha':q,'site_baseline_comparison':cluster_bootstrap(test,p,site_base),
                 'year_shift_interval_warning':'Coverage is empirical; exchangeability across years is not established.'}
        report['ablations'][task]=summary
        if key==selected[row['mode']]['key']:
            report['tasks'][row['mode']]=summary
            if unknown.any():summary['new_site_metrics']=metrics(test.y[unknown],p[unknown])
            artifact={'model':model,'features':cols,'mode':row['mode'],'uses_pedigree':bool(row['parents']),
                      'interval_halfwidth':q,'source':'G2F 2014-2021, DOI 10.25739/tq5e-ak26',
                      'scope':'Maize only; no crop sequence effect, no SNP input; research use.',
                      'known_parent1':sorted(set(fit.Hybrid_Parent1)),'known_parent2':sorted(set(fit.Hybrid_Parent2)),
                      'validation':summary,'protocol':PROTOCOL}
            joblib.dump(artifact,out/f'{row["mode"]}_yield.joblib')
            pd.DataFrame({'Env':test.Env,'Hybrid':test.Hybrid,'actual':test.y,'prediction':p,'lower':lower,'upper':upper,'site_baseline':site_base}).to_csv(out/f'{row["mode"]}_test_predictions.csv',index=False)
    report['limitations']=['No SNP markers; pedigree IDs are a limited genetic proxy.',
        'Maize yield results do not validate choosing another crop or a crop sequence.',
        'Test year is 2021 at research sites; no prospective farmer validation.',
        'Hindsight task includes realized weather and potentially postplant soil; cannot guide preplant decisions.',
        'Preplant climate uses earlier years, but operational data-release latency was not reconstructed.',
        'Microbiome and pollinator sources remain contextual; they lack matched yield labels.']
    (out/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'records':len(d),'test_environments':test.Env.nunique(),'tasks':report['tasks'],'baselines':report['baselines']},indent=2))


if __name__=='__main__':run()
