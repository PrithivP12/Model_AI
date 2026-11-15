"""Hypothesis-first component diagnostics and sensitivity using development years only."""
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
import copy,json,math
import numpy as np
import pandas as pd
import openpyxl
from .. import calibration
from ..data.lirf import soil_prior,labels,old_cases
from ..io import write_json,sha256
from .water import DiagnosticSimulator


def training_field_capacity(root,prior):
    values=[]
    for year in [2008,2009]:
        w=openpyxl.load_workbook(Path(root)/'crop-response-lirf/raw'/f'maize{year}.xlsx',read_only=True,data_only=True)
        for r in w['Seasonal'].iter_rows(min_row=7,max_row=12,values_only=True):values.append([float(v)/100 for v in r[12:16]])
        w.close()
    result=copy.deepcopy(prior)
    for h,fc in zip(result,np.mean(values,axis=0)):
        h['theta_fc']=float(fc)
        h['theta_wp_source']=h['source']
        h['source']='2008–2009 Seasonal field-measured capacity: 12 treatment profiles, percentage/100; wilting point retained from laboratory prior'
        h['field_capacity_fit_years']=[2008,2009]
    return result


def trace_case(case,prior,parameters,options=None,coupled=False):
    instances=[]
    def factory(*args,**kwargs):
        if coupled:
            from .coupled_validation import CoupledReplay
            engine=CoupledReplay(*args,options=options,**kwargs)
        else:engine=DiagnosticSimulator(*args,options=options,**kwargs)
        instances.append(engine);return engine
    with patch.object(calibration,'DepthSimulator',factory):
        result=calibration.simulate_case(case,prior,parameters)
    run=instances[0].captured[-1];daily=pd.DataFrame(run['daily']).set_index('date')
    target=[]
    for row in result['water_predictions']:
        obs=row['observed_theta'];pred=row['predicted_theta'];t=row['date']
        event=case['events'].get(t,{})
        target.append({'case_id':case['id'],'year':case['year'],'treatment':case['treatment'],**row,
            'error':pred-obs,'irrigation_same_day_mm':event.get('irrigation_mm',0),
            'persistence_error':case['soil_observations'][result['initial_assimilation']['date']][[0,15,45,75].index(row['layer_top_cm'])]-obs,
            'rain_same_day_mm':next(w['rain_mm']for w in case['weather']if w['date']==t)})
    lag={}
    for shift in [-1,0,1]:
        errors=[]
        for row in target:
            t=(pd.Timestamp(row['date'])+pd.Timedelta(days=shift)).date().isoformat()
            if t not in daily.index:continue
            i=next(i for i,h in enumerate(prior)if h['top_cm']==row['layer_top_cm'])
            theta=daily.loc[t,f'water_layer_{i}_mm']/((prior[i]['bottom_cm']-prior[i]['top_cm'])*10)
            errors.append(theta-row['observed_theta'])
        lag[str(shift)]={'records':len(errors),'rmse':float(np.sqrt(np.mean(np.square(errors))))}
    totals={k:run['totals'][k]for k in ['rain_mm','irrigation_mm','evaporation_mm','transpiration_mm','drainage_mm','runoff_mm','plant_uptake_n_kg_ha','leached_n_kg_ha','denitrified_n_kg_ha']}
    result.update(totals=totals,water_rows=target,timing_diagnostic=lag,
        mean_water_stress_factor=float(daily.water_stress_factor.mean()),mean_n_growth_factor=float(daily.n_growth_factor.mean()),
        canopy_comparison='Measured canopy discrepancy scored only after initialization; no future canopy used as growth forcing')
    return result


def metrics(runs,observed):
    output=calibration.summary(runs,observed)
    rows=pd.DataFrame([row for run in runs for row in run['water_rows']])
    output['water_bias_m3_m3']=float(rows.error.mean())
    output['layer_metrics']={str(int(k)):{'records':len(g),'bias':float(g.error.mean()),'rmse':float(np.sqrt(np.mean(g.error**2)))}for k,g in rows.groupby('layer_top_cm')}
    rows['depth_weight']=rows.layer_bottom_cm-rows.layer_top_cm
    profile=rows.groupby(['case_id','date']).apply(lambda g:pd.Series({
        'model_error':float(np.average(g.error,weights=g.depth_weight)),
        'persistence_error':float(np.average(g.persistence_error,weights=g.depth_weight))}),include_groups=False)
    output['equivalent_0_105cm_profile']={'records':len(profile),'model_rmse_m3_m3':float(np.sqrt(np.mean(profile.model_error**2))),
        'persistence_rmse_m3_m3':float(np.sqrt(np.mean(profile.persistence_error**2))),
        'interpretation':'thickness-weighted 0–105 cm storage divided by 1050 mm; does not validate layer-wise fluctuations'}
    output['mean_flux_mm']={k:float(np.mean([r['totals'][k]for r in runs]))for k in ['evaporation_mm','transpiration_mm','drainage_mm','runoff_mm','rain_mm','irrigation_mm']}
    # Same-day event association is descriptive, not a causal estimate or a time-shift fit.
    output['wet_day_rmse']=float(np.sqrt(np.mean(rows.loc[(rows.irrigation_same_day_mm+rows.rain_same_day_mm)>0,'error']**2)))
    output['dry_day_rmse']=float(np.sqrt(np.mean(rows.loc[(rows.irrigation_same_day_mm+rows.rain_same_day_mm)==0,'error']**2)))
    return output


def run_diagnostics(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Existing diagnostics are immutable; use a new directory')
    parameters=json.loads((root/'reports/completion-pass/quality-corrected/depth-refinement/selected.json').read_text())['parameters']
    prior=soil_prior(root,True);training=[c for y in [2008,2009]for c in old_cases(root,y)]
    observed={year:labels(root,year)for year in [2008,2009]}
    field=training_field_capacity(root,prior)
    cases=[('reference',parameters,prior,{}),('no_evaporation',parameters,prior,{'no_evaporation':True}),
       ('no_transpiration_diagnostic',parameters,prior,{'no_transpiration':True}),
       ('no_drainage',parameters,prior,{'no_drainage':True}),
       ('fao56_dual',parameters,prior,{'evaporation':'fao56_dual'}),
       ('training_field_capacity',parameters,field,{}),
       ('root_zone_105cm',{**parameters,'root_depth_m':1.05},prior,{}),
       ('field_capacity_and_dual',parameters,field,{'evaporation':'fao56_dual'})]
    protocol={'fit_years':[2008,2009],'heldout_observations_used':False,
        'parameters_source':'Frozen preceding training/development selection; not refitted here',
        'hypotheses':{name:'Isolate '+name+' relative to otherwise identical reference'for name,_,_,_ in cases},
        'no_transpiration_status':'Diagnostic knockout with original growth/stress retained; never an admissible predictive candidate',
        'timing':'Evaluate -1/0/+1 day only to quantify unresolved sampling time; do not select a shift for prediction',
        'references':['https://www.fao.org/4/X0490E/x0490e0c.htm','https://www.fao.org/4/x0490e/x0490e0e.htm'],
        'provider_model_ET_not_used_as_truth':True,'soil_measurement_geometry':'0–15 cm row TDR and 15 cm-radius neutron support near drip row, not field polygon average'}
    write_json(out/'protocol_frozen.json',protocol);reports={};all_rows=[];timing=[]
    for name,p,h,options in cases:
        runs=[trace_case(c,h,p,options)for c in training];reports[name]=metrics(runs,observed)
        for r in runs:
            all_rows.extend({**v,'diagnostic':name}for v in r['water_rows'])
            timing.append({'case_id':r['id'],'diagnostic':name,'timing':r['timing_diagnostic']})
        print(name,reports[name]['water_rmse_m3_m3'],reports[name]['water_bias_m3_m3'],flush=True)
    # One-factor sensitivity precedes any new parameter calibration.
    sensitivity=[]
    for parameter in ['rue','sla','leaf_fraction','maturity_gdd','harvest_index','wilting_scale','root_depth_m','bare_soil_evap_coefficient','drainage_fraction_day']:
        for scale in [.8,1.2]:
            altered={**parameters,parameter:parameters[parameter]*scale}
            runs=[trace_case(c,prior,altered)for c in training]
            sensitivity.append({'parameter':parameter,'multiplier':scale,**metrics(runs,observed)})
    pd.DataFrame(all_rows).to_csv(out/'water_errors.csv',index=False)
    write_json(out/'timing_diagnostics.json',timing);write_json(out/'sensitivity.json',sensitivity)
    result={'status':'development_root_cause_diagnostics_completed','protocol':protocol,'diagnostics':reports,
        'sensitivity':sensitivity,'reference_parameters':parameters,'unchanged_default_equations':True,'recommendation':None}
    write_json(out/'report.json',result);return result
