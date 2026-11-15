"""Small retrospective hybrid experiment; never a fresh validation claim."""
from pathlib import Path
from datetime import datetime,date,timedelta
import importlib.util
import json
import numpy as np
import pandas as pd
import openpyxl
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from .processes import default_crops,WeatherDay,Planting,Management,SoilState
from .soil import HorizonLayer,DepthSimulator
from .io import sha256,write_json
from .benchmark import scores


def daily_case(root,year,treatment,fallback,temperature_fallback):
    path=root/'raw'/f'weather{year}.xlsx'
    w=openpyxl.load_workbook(path,read_only=True,data_only=True)
    station={r[0].date():r for r in w['Daily'].iter_rows(min_row=7,values_only=True)if isinstance(r[0],datetime)};w.close()
    crop=openpyxl.load_workbook(root/'raw'/f'maize{year}.xlsx',read_only=True,data_only=True)
    records={int(r[0]):r for r in crop[f'Tmnt{treatment}'].iter_rows(min_row=5,values_only=True)
             if isinstance(r[0],(int,float))and 1<=r[0]<=366};crop.close()
    start=date(year,5,15);end=date(year,9,30);weather=[];events=[];fills=[]
    for i in range((end-start).days+1):
        t=start+timedelta(days=i);r=station[t];m=records[t.timetuple().tm_yday];solar=r[8];minimum=r[4]
        if not isinstance(solar,(float,int)):
            solar=fallback['properties']['parameter']['ALLSKY_SFC_SW_DWN'][t.strftime('%Y%m%d')]
            if fallback['parameters']['ALLSKY_SFC_SW_DWN']['units']!='MJ/m^2/day':raise ValueError('Solar fallback unit')
            fills.append({'date':str(t),'variable':'solar','source':'NASA POWER prior cached fallback'})
        if not isinstance(minimum,(float,int)):
            data=temperature_fallback['data']
            if data['parameters']['T2M_MIN']['units']!='C':raise ValueError('Temperature fallback unit')
            minimum=data['properties']['parameter']['T2M_MIN'][t.strftime('%Y%m%d')]
            fills.append({'date':str(t),'variable':'tmin','source':'NASA POWER new bounded subset'})
        if not all(isinstance(v,(float,int))and np.isfinite(v)for v in [solar,minimum,r[3],r[17]]):
            raise ValueError('Missing daily observed weather')
        weather.append(WeatherDay(t,float(m[1]or 0),minimum,r[3],solar,r[17]))
        events.append(Management(t,0,float(m[2]or 0)))
    layers=[HorizonLayer(.3,.12,.28,.45),HorizonLayer(.3,.12,.28,.45),HorizonLayer(.6,.12,.28,.45)]
    state=SoilState([84,84,168],[200,150,100],[12000,10000,15000],[1000,833.3333333,1250],
                    [0,0,0],[0,0,0],[15,15,15])
    model=DepthSimulator(layers,latitude=40.448)
    # Apply the same documented corruption correction as the measured-state engine.
    from .data.lirf import old_cases
    checked=next(c for c in old_cases(root.parent,year)if c['treatment']==str(treatment))
    checked_dates={r['date']:r for r in checked['weather']}
    weather=[WeatherDay(d.date,checked_dates[str(d.date)]['rain_mm'],checked_dates[str(d.date)]['tmin_C'],
        checked_dates[str(d.date)]['tmax_C'],checked_dates[str(d.date)]['solar_MJ_m2'],checked_dates[str(d.date)]['et0_mm'])for d in weather]
    result=model.run(weather,state,[Planting(start,'corn',end)],events,keep_daily=False)
    harvest=result['harvests'][0]
    return {'mechanistic_yield':harvest['grain_dry_kg_ha'],
            'mechanistic_transpiration':harvest['transpiration_mm'],
            'mechanistic_final_water':sum(result['final_state']['water_mm']),
            'mechanistic_final_n':sum(result['final_state']['mineral_n_kg_ha']),
            'balance_error':result['max_daily_balance_error'],'filled_days':fills}


def run_hybrid(project_root,output):
    root=Path(project_root)/'crop-response-lirf';out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if (out/'report.json').exists():raise RuntimeError('Completed hybrid experiment preserved')
    subset=list((out/'raw').glob('*.json'))
    if len(subset)!=1:raise FileNotFoundError('Bounded NASA 2011-08-11 temperature subset is required')
    fallback=json.loads((root/'nasa_solar_fallback.json').read_text());temperature=json.loads(subset[0].read_text())
    spec=importlib.util.spec_from_file_location('lirf_reader',root/'experiment.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    protocol={'fit_years':[2008,2009],'selection_year':2010,'retrospective_test_year':2011,
        'new_untouched_test':False,'reason':'2011 outcomes were exposed during an earlier experiment; these comparisons are exploratory.',
        'process_engine_sha256':sha256(Path(project_root)/'fieldshift/simulator.py'),
        'crop_parameters':'Existing illustrative maize parameters, not fitted to these yields.',
        'soil':'Assumed 0–30/30–60/60–120 cm profile; FC .28, WP .12, saturation .45; initial FC and mineral N 450 kg/ha. Not measured site soil.',
        'calendar':'Fixed May 15 through September 30; actual source planting dates not used.',
        'forcing':'Observed-season weather and treatment rain/irrigation; not a preplant prediction.',
        'missing_weather_subset':{'path':str(subset[0]),'sha256':sha256(subset[0])},
        'models':['simulator alone','ML ridge alone','simulator + ridge residual','simulator states + ridge'],
        'ridge_grid':[.1,1,10,100],'selection_metric':'2010 MAE; 2008–09 only fitted',
        'production_prediction_enabled':False}
    protocol['temperature_QC_correction']='Physically invalid 2010-07-16 station extremes replaced from NASA POWER; prior reports are preserved, new runs are a corrected retrospective reconstruction.'
    write_json(out/'protocol_frozen.json',protocol)
    rows=[]
    for year in range(2008,2012):
        for item in module.features(year):
            state=daily_case(root,year,item['treatment'],fallback,temperature)
            rows.append({**item,**state})
    frame=pd.DataFrame(rows)
    x=['irrigation_mm','rain_mm','solar_MJ_m2','reference_et0_mm']
    states=['mechanistic_yield','mechanistic_transpiration','mechanistic_final_water','mechanistic_final_n']
    # Original outcomes loaded separately, then restricted to each declared split.
    labels={year:module.labels(year)for year in [2008,2009,2010]}
    train=frame.year<2010;val=frame.year==2010;test=frame.year==2011
    y=np.concatenate([labels[2008],labels[2009]]);vy=labels[2010]
    chosen={};predictions={'simulator':frame.loc[test,'mechanistic_yield'].to_numpy()};validation={}
    for kind in ['ml_alone','residual','state_features']:
        names=x+states if kind=='state_features'else x
        offset=frame.mechanistic_yield.to_numpy()if kind=='residual'else np.zeros(len(frame))
        candidates=[]
        for alpha in protocol['ridge_grid']:
            pipeline=make_pipeline(StandardScaler(),Ridge(alpha=alpha))
            pipeline.fit(frame.loc[train,names],y-offset[train])
            vp=np.maximum(0,pipeline.predict(frame.loc[val,names])+offset[val])
            candidates.append((float(np.mean(abs(vp-vy))),alpha,pipeline))
        mae,alpha,pipeline=min(candidates,key=lambda r:r[:2]);chosen[kind]={'ridge_alpha':alpha,'validation_mae_kg_dry_ha':mae}
        predictions[kind]=np.maximum(0,pipeline.predict(frame.loc[test,names])+offset[test])
    write_json(out/'selection_frozen.json',chosen)
    frozen=frame.loc[test,['year','treatment']].copy()
    for k,v in predictions.items():frozen[k]=v
    frozen.to_csv(out/'predictions_frozen.csv',index=False)
    observed=module.labels(2011)
    metrics={name:scores(observed,p,['LIRF']*len(p))for name,p in predictions.items()}
    frame.drop(columns=['balance_error','filled_days']).to_csv(out/'mechanistic_features.csv',index=False)
    report={'status':'retrospective_hybrid_experiment','protocol':protocol,'selection':chosen,'retrospective_2011_metrics':metrics,
        'fresh_accuracy_improvement_claim':False,'predictions_sha256':sha256(out/'predictions_frozen.csv'),
        'observed_yields_dry_kg_ha':observed.tolist(),'recommendation':None,
        'limitations':['One site and six treatment means per year. No global or rotation inference.',
            'Initial soil properties, N and planting calendar are assumed; soil-water trajectories have no independent observed validation.',
            'Residual corrections are separate statistical yields; physical crop-state mass balances are not modified.',
            'An improvement here requires a new independent test before deployment.'],
        'max_daily_balance_error':{pool:max(r['balance_error'][pool]for r in rows)for pool in ['water','nitrogen','carbon']}}
    write_json(out/'report.json',report)
    return report
