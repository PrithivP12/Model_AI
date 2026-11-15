"""Australian wheat temporal/plot holdouts in original grain measurement units."""
from pathlib import Path
import json,math
import numpy as np
import pandas as pd
import xlrd
from sklearn.base import clone
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor,ExtraTreesRegressor
from .benchmark import make_pipeline_for
from .io import write_json,sha256


def normalize_waite_environment(project,out):
    raw=Path(project)/'research/waite/Waite_Trial_Data.xls';w=xlrd.open_workbook(raw,on_demand=True)
    s=w.sheet_by_name('Climate Data');weather=[]
    for i in range(5,s.nrows):
        r=s.row_values(i)
        if not isinstance(r[0],(float,int)):continue
        for m in range(1,13):
            for name,value,unit in [('temperature',r[m],'C'),('rainfall',r[12+m],'mm/month')]:
                if isinstance(value,(float,int))and math.isfinite(value):
                    weather.append({'year':int(r[0]),'month':m,'variable':name,'value':float(value),'unit':unit,
                                    'source':'Waite workbook, Bureau of Meteorology monthly table'})
    soil=[];s=w.sheet_by_name('Select Soil Data');top=None;bottom=None
    for i in range(s.nrows):
        r=s.row_values(i)
        if str(r[0]).startswith('Soil carbon data'):
            import re
            depths=re.findall(r'\d+(?:\.\d+)?',str(r[0]));top,bottom=map(float,depths[:2])
        if top is not None and isinstance(r[0],(float,int))and r[1]:
            for name,j,unit,kind in [('total_organic_carbon',2,'%', 'measured'),('total_nitrogen',3,'%', 'measured'),
                ('bulk_carbon_stock',4,'Mg C/ha','reported stock'),('particulate_carbon',5,'Mg C/ha','MIR-PLSR prediction'),
                ('humus_carbon',6,'Mg C/ha','MIR-PLSR prediction'),('resistant_carbon',7,'Mg C/ha','MIR-PLSR prediction')]:
                if isinstance(r[j],(float,int)):
                    soil.append({'year':int(r[0]),'rotation_label':str(r[1]),'top_cm':top,'bottom_cm':bottom,
                                 'variable':name,'value':float(r[j]),'unit':unit,'evidence_kind':kind,
                                 'plot_mapping_verified':False})
    w.release_resources();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(weather).to_parquet(out/'waite_monthly_weather.parquet',index=False)
    pd.DataFrame(soil).to_parquet(out/'waite_soil_observations.parquet',index=False)
    write_json(out/'waite_environment_quality.json',{'raw_sha256':sha256(raw),'weather_records':len(weather),'soil_records':len(soil),
        'soil_not_joined_to_plots_reason':'Aggregate rotation labels need a verified plot/treatment crosswalk; no plot correspondence is guessed.',
        'pan_evaporation_used':False,'modeled_carbon_fractions_used_as_validation_truth':False})
    return pd.DataFrame(weather)


def error(y,p):
    y=np.array(y);p=np.array(p);d=p-y
    return {'records':len(y),'mae_kg_grain_ha':float(np.mean(abs(d))),'rmse_kg_grain_ha':float(np.sqrt(np.mean(d*d))),
            'bias_kg_grain_ha':float(np.mean(d)),'r2':float(1-sum(d*d)/sum((y-y.mean())**2))if np.var(y)>0 else None}


def contrasts(d,p):
    frame=d.copy();frame['prediction']=p;rows=[]
    for year,g in frame.groupby('year'):
        reference=g[g.continuous_last3];alternative=g[~g.continuous_last3]
        if reference.empty or alternative.empty:continue
        observed=float(alternative.observed_yield_original.mean()-reference.observed_yield_original.mean())
        estimated=float(alternative.prediction.mean()-reference.prediction.mean())
        rows.append({'year':int(year),'observed_difference_kg_grain_ha':observed,'predicted_difference_kg_grain_ha':estimated,
                     'reference_plots':len(reference),'alternative_plots':len(alternative)})
    if not rows:return {'years':0}
    actual=np.array([r['observed_difference_kg_grain_ha']for r in rows]);pred=np.array([r['predicted_difference_kg_grain_ha']for r in rows]);decided=abs(pred)>1e-6
    return {'years':len(rows),'pairs':rows,'rmse_difference_kg_grain_ha':float(np.sqrt(np.mean((pred-actual)**2))),
            'decisions':int(decided.sum()),'directional_agreement':float(np.mean(np.sign(pred[decided])==np.sign(actual[decided])))if decided.any()else None,
            'always_choose_diversified_agreement':float(np.mean(actual>0)),
            'causal_rotation_effect_established':False}


def run_waite(project,output):
    project=Path(project);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if (out/'report.json').exists():raise RuntimeError('Completed Waite evaluation is preserved')
    weather=normalize_waite_environment(project,out/'data')
    data=pd.read_parquet(project/'reports/agrosim/data/waite_rotation_dataset.parquet')
    data=data[(data.current_crop=='wheat')&(data.year>=1930)].copy()
    records=[]
    for r in data.to_dict('records'):
        history=json.loads(r['previous_crops_json']);by_year={a['year']:a['crop']for a in history}
        if any(r['year']-i not in by_year for i in range(1,4)):continue
        for i in range(1,4):r[f'previous_{i}']=by_year[r['year']-i]or 'unknown'
        for crop in ['wheat','peas','pasture','fallow','oats','barley']:
            r['last6_'+crop]=sum(by_year.get(r['year']-i)==crop for i in range(1,7))
        r['continuous_last3']=all(r[f'previous_{i}']=='wheat'for i in range(1,4))
        for variable in ['rainfall','temperature']:
            current=weather[(weather.year==r['year'])&(weather.month<=4)&(weather.variable==variable)]
            prior=weather[(weather.year<r['year'])&(weather.year>=r['year']-10)&(weather.variable==variable)]
            r['preplant_'+variable]=float(current.value.sum()if variable=='rainfall'else current.value.mean())
            r['prior10_'+variable]=float(prior.groupby('year').value.sum().mean()if variable=='rainfall'else prior.value.mean())
        r['held_plot']=int(r['plot_id'])%5==0;records.append(r)
    d=pd.DataFrame(records).reset_index(drop=True)
    numbers=['year','continuous_last3']+[c for c in d if c.startswith(('last6_','preplant_','prior10_'))]
    cats=['previous_1','previous_2','previous_3'];fit=d[(d.year<=1969)&~d.held_plot];select=d[(d.year.between(1970,1979))&~d.held_plot]
    calibration=d[(d.year.between(1980,1989))&~d.held_plot];test=d[d.year>=1990]
    protocol={'fit_years':[1930,1969],'selection_years':[1970,1979],'interval_calibration_years':[1980,1989],'reserved_final_test_years':[1990,1993],
        'held_out_plots':'integer plot ID divisible by 5, never fitted/selected/calibrated',
        'target_unit':'kg grain/ha at original unspecified moisture basis; never mixed with dry-grain maize',
        'features':numbers+cats,'preplant_cutoff':'May 1 assumed for this Australian site; January–April weather only plus prior-year histories',
        'soil_features_used':False,'cause':'Rotation-labelled soil table lacks a verified plot crosswalk.',
        'source_doi':'10.4225/08/55E5165EC0D29','sites':1,'global_accuracy_test':False,
        'within_site_contrast':'wheat after three wheat years vs wheat with other observed crops in the preceding three years; management/soil confounding remains'}
    write_json(out/'protocol_frozen.json',protocol)
    models={'ridge':Ridge(alpha=100),'extra_trees':ExtraTreesRegressor(n_estimators=180,min_samples_leaf=10,random_state=73171,n_jobs=2),
            'hist':HistGradientBoostingRegressor(max_iter=120,max_leaf_nodes=7,l2_regularization=20,early_stopping=False,random_state=73171)}
    fitted={};leaderboard=[]
    mean=float(fit.observed_yield_original.mean())
    leaderboard.append({'model':'mean',**error(select.observed_yield_original,np.full(len(select),mean))})
    for name,model in models.items():
        pipeline=make_pipeline_for(model,numbers,cats);pipeline.fit(fit[numbers+cats],fit.observed_yield_original)
        fitted[name]=pipeline;leaderboard.append({'model':name,**error(select.observed_yield_original,np.maximum(0,pipeline.predict(select[numbers+cats])))})
    best=min(leaderboard,key=lambda r:r['rmse_kg_grain_ha'])['model']
    write_json(out/'selection_frozen.json',{'selected':best,'leaderboard':leaderboard})
    predict=lambda a:np.full(len(a),mean)if best=='mean'else np.maximum(0,fitted[best].predict(a[numbers+cats]))
    residual=abs(calibration.observed_yield_original.to_numpy()-predict(calibration))
    q=min(1,np.ceil((len(residual)+1)*.9)/len(residual));halfwidth=float(np.quantile(residual,q,method='higher'))
    prediction=predict(test);frozen=test[['record_id','plot_id','year','held_plot','continuous_last3']].copy();frozen['prediction']=prediction
    frozen['mean_baseline']=mean;frozen['lower']=np.maximum(0,prediction-halfwidth);frozen['upper']=prediction+halfwidth
    frozen.to_csv(out/'test_predictions_frozen.csv',index=False)
    report={'status':'australian_wheat_reserved_test_evaluated','protocol':protocol,'selection':best,'selection_leaderboard':leaderboard,
        'test':error(test.observed_yield_original,prediction),'mean_baseline':error(test.observed_yield_original,np.full(len(test),mean)),
        'interval':{'nominal':.9,'halfwidth_kg_grain_ha':halfwidth,'coverage':float(np.mean((test.observed_yield_original>=frozen.lower)&(test.observed_yield_original<=frozen.upper))),
                    'coverage_guarantee_across_time_or_plots':False},'rotation_contrasts':contrasts(test,prediction),
        'withheld_plots_future_years':error(test.loc[test.held_plot,'observed_yield_original'],prediction[test.held_plot.to_numpy()]),
        'prediction_sha256':sha256(out/'test_predictions_frozen.csv'),'recommendation':None,'worldwide_rotation_accuracy_established':False,
        'limitations':['Only one Australian historical site. Unspecified grain moisture basis.',
            'Annual comparisons are not independent farms and do not prove a causal crop-switch effect.',
            'Historical data release vintages and actual planting dates are not reconstructed.',
            'Soil concentrations and modeled carbon fractions are normalized but not guessed onto plots.']}
    write_json(out/'report.json',report);d.to_parquet(out/'data/site_seasons.parquet',index=False)
    return report
