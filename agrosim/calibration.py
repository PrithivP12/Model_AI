"""Measured-profile initialization and train-only daily-engine calibration."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import json,math,time
import numpy as np
import pandas as pd
from .processes import Layer,SoilState,Planting,Management,WeatherDay,Processes,default_crops
from .soil import DepthSimulator
from .data.lirf import soil_prior,labels,DEPTHS
from .io import write_json,sha256


def simulate_case(case,prior,parameters,*,initialize_canopy=True):
    layers=[]
    for h in prior:
        wp=h['theta_wp']*parameters.get('wilting_scale',1);fc=wp+(h['theta_fc']-wp)*parameters.get('capacity_scale',1)
        layers.append(Layer((h['bottom_cm']-h['top_cm'])/100,wp,fc,.55,parameters.get('drainage_fraction_day',.5),min(.025,wp*.25)))
    n=len(layers);initial_n=case['initial_n_kg_ha'];weights=np.array([.25,.5,.25]+[0.]*(n-3))
    state=SoilState([l.mm(l.field_capacity)for l in layers],list(initial_n*weights),[6000,10000,8000,6000]+[0.]*(n-4),
        [500,833.333333,666.6666667,500]+[0.]*(n-4),[0.]*n,[0.]*n,[15.]*n)
    crop=replace(default_crops()['corn'],rue_g_dry_per_MJ_PAR=parameters.get('rue',3.2),
        specific_leaf_area_m2_kg=parameters.get('sla',22),vegetative_leaf_fraction=parameters.get('leaf_fraction',.4),
        maturity_gdd=parameters.get('maturity_gdd',1500),harvest_index=parameters.get('harvest_index',.5),
        max_root_depth_m=parameters.get('root_depth_m',1.05),kill_minimum_temp_C=-5,
        grain_n_fraction=parameters.get('grain_n_fraction',default_crops()['corn'].grain_n_fraction))
    processes=Processes(bare_soil_evap_coefficient=parameters.get('bare_soil_evap_coefficient',.8),
                        infiltration_limit_mm_day=100,maize_n_dilution_a=parameters.get('n_dilution_a',0))
    engine=DepthSimulator(layers,{'corn':crop},processes,40.448)
    weather=[WeatherDay(date.fromisoformat(r['date']),r['rain_mm'],r['tmin_C'],r['tmax_C'],r['solar_MJ_m2'],r['et0_mm'])for r in case['weather']]
    events=[Management(date.fromisoformat(t),r['n_kg_ha'],r['irrigation_mm'])for t,r in case['events'].items()
            if weather[0].date<=date.fromisoformat(t)<=weather[-1].date]
    canopy=case.get('canopy_observations',{})
    earliest=max(min(case['soil_observations']),min(canopy)if canopy else min(case['soil_observations']))
    first=min(t for t in case['soil_observations']if t>=earliest);cutoff=date.fromisoformat(first)
    planting=Planting(date.fromisoformat(case['planting_date']),'corn',weather[-1].date)
    spin=engine.run([w for w in weather if w.date<=cutoff],state,[planting],[e for e in events if e.date<=cutoff],
                    keep_daily=False,allow_unharvested=True)
    plant=spin['active_plant_state']
    if plant is None:raise ValueError('Model killed or matured crop before observation initialization')
    state=SoilState(**spin['final_state']);before=sum(state.water_mm)
    observed=case['soil_observations'][first]
    for i,theta in enumerate(observed):
        if not 0<=theta<=layers[i].saturation:raise ValueError('Initial measured soil water outside physical bounds')
        state.water_mm[i]=layers[i].mm(theta)
    if n>4 and first in case.get('full_soil_observations',{}):
        for i,theta in enumerate(case['full_soil_observations'][first][4:],start=4):state.water_mm[i]=layers[i].mm(theta)
    available=sorted(t for t in canopy if t<=first)
    assimilation={'date':first,'water_adjustment_mm':sum(state.water_mm)-before,'canopy_assimilated':False}
    if initialize_canopy and available and (cutoff-date.fromisoformat(available[-1])).days<=10:
        cover=canopy[available[-1]]
        if 0<cover<.98:
            lai=-math.log(1-cover)/crop.extinction_coefficient
            biomass=lai*10000/(crop.specific_leaf_area_m2_kg*crop.vegetative_leaf_fraction*(1-crop.root_biomass_fraction))
            assimilation.update(canopy_assimilated=True,biomass_adjustment_dry_kg_ha=biomass-plant.biomass_dry_kg_ha,
                                plant_carbon_adjustment_kg_ha=(biomass-plant.biomass_dry_kg_ha)*.45,
                                plant_n_adjustment_kg_ha=biomass*crop.plant_n_fraction-plant.n_kg_ha,
                                plant_n_assumption='biomass × configured plant N fraction')
            plant.biomass_dry_kg_ha=biomass;plant.n_kg_ha=biomass*crop.plant_n_fraction
    result=engine.run([w for w in weather if w.date>cutoff],state,[],[e for e in events if e.date>cutoff],
                      initial_plant=plant,keep_daily=True)
    predictions={r['date']:r for r in result['daily']};errors=[];baseline=[];water_rows=[];canopy_errors=[]
    for t,thetas in case['soil_observations'].items():
        if t<=first or t not in predictions:continue
        daily=predictions[t]
        for i,theta in enumerate(thetas):
            pred=daily[f'water_layer_{i}_mm']/layers[i].thickness_m/1000
            errors.append(pred-theta);baseline.append(observed[i]-theta)
            water_rows.append({'date':t,'layer_top_cm':DEPTHS[i][0],'layer_bottom_cm':DEPTHS[i][1],
                              'observed_theta':theta,'predicted_theta':pred})
    for t,cover in canopy.items():
        if t>first and t in predictions:
            canopy_errors.append((1-math.exp(-crop.extinction_coefficient*predictions[t]['lai']))-cover)
    return {'id':case['id'],'year':case['year'],'treatment':case['treatment'],
        'harvest_diagnostics':result['harvests'],'maximum_modeled_lai':max(r['lai']for r in result['daily']),
        'predicted_yield_dry_kg_ha':sum(h['grain_dry_kg_ha']for h in result['harvests']),
        'water_squared_errors':list(np.square(errors)),'water_absolute_errors':list(abs(np.array(errors))),
        'water_baseline_squared_errors':list(np.square(baseline)),
        'canopy_squared_errors':list(np.square(canopy_errors)),
        'water_predictions':water_rows,'initial_assimilation':assimilation,
        'max_balance_error':result['max_daily_balance_error']}


def summary(results,observed=None):
    errors=np.concatenate([r['water_squared_errors']for r in results]);baseline=np.concatenate([r['water_baseline_squared_errors']for r in results])
    out={'cases':len(results),'soil_water_layer_observations':len(errors),'water_rmse_m3_m3':float(np.sqrt(np.mean(errors))),
         'initial_profile_persistence_rmse_m3_m3':float(np.sqrt(np.mean(baseline))),
         'max_balance_error':{k:max(r['max_balance_error'][k]for r in results)for k in ['water','nitrogen','carbon']}}
    if observed is not None:
        paired=[(r['predicted_yield_dry_kg_ha'],observed[r['year']][r['treatment']])for r in results if r['treatment']in observed[r['year']]]
        if paired:
            p,y=np.array(paired).T
            out.update(yield_records=len(y),yield_mae_kg_dry_ha=float(np.mean(abs(p-y))),yield_rmse_kg_dry_ha=float(np.sqrt(np.mean((p-y)**2))),
                       yield_bias_kg_dry_ha=float(np.mean(p-y)),yield_r2=float(1-sum((p-y)**2)/sum((y-y.mean())**2))if np.var(y)>0 else None)
    return out


def calibrate(project,output,trials=48):
    project=Path(project);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if (out/'report.json').exists():raise RuntimeError('Calibration report preserved; choose a fresh output directory')
    data=json.loads((project/'reports/completion-pass/lirf_cases_development.json').read_text())
    test=json.loads((project/'reports/completion-pass/lirf_cases_test_inputs.json').read_text())
    if not test:raise ValueError('No eligible independent test inputs')
    prior=soil_prior(project);rng=np.random.default_rng(73171)
    specifications=[{}]+[{'rue':float(rng.uniform(2.4,4.3)),'sla':float(rng.uniform(18,35)),
        'leaf_fraction':float(rng.uniform(.3,.55)),'maturity_gdd':float(rng.uniform(1200,1650)),
        'harvest_index':float(rng.uniform(.45,.6)),'capacity_scale':float(rng.uniform(.85,1.25)),
        'bare_soil_evap_coefficient':float(rng.uniform(.2,.85)),'drainage_fraction_day':float(rng.uniform(.1,.8))}for _ in range(trials-1)]
    protocol={'fit':[2008,2009],'select':2010,'diagnostic_exposed':2011,'independent_test':[2012,2013],
        'trials':trials,'seed':73171,'objective':'fit yield MAE / 12000 + profile theta RMSE / .30; select among top 8 by the same 2010 metric',
        'prediction_horizon':'historical reconstruction using full-season actual weather/irrigation, initialized with measured soil/canopy',
        'test_years_already_exposed_in_project':True,'new_untouched_test':False,
        'kernel_sha256':sha256(project/'agrosim/processes.py'),'test_labels_read_after_prediction_freeze':True,
        'prior_profile':prior,'forbidden':'No source estimated ET, Kcb, Ks, roots or modeled water is fitted or scored as measured truth.',
        'limits':['Same farm temporal/irrigation-system test, not global or rotation validation.',
            'Crop N fractions, initial organic pools and 2012–13 initial mineral N are assumptions.',
            'Hourly weather extrema differ from fine-time-resolution daily extremes.',
            'No numerical confidence guarantee from six treatment means per development year.']}
    write_json(out/'protocol_frozen.json',protocol)
    observed={y:labels(project,y)for y in [2008,2009,2010]};train=[c for c in data if c['year']in [2008,2009]]
    leaderboard=[];start=time.monotonic()
    for i,p in enumerate(specifications):
        runs=[simulate_case(c,prior,p)for c in train];metrics=summary(runs,observed)
        objective=metrics['yield_mae_kg_dry_ha']/12000+metrics['water_rmse_m3_m3']/.30
        leaderboard.append({'trial':i,'parameters':p,'objective':objective,'fit':metrics})
        print(json.dumps({'trial':i,'objective':objective}),flush=True)
        if time.monotonic()-start>480:raise RuntimeError('Calibration runtime exceeded eight-minute budget')
    selected=[]
    for row in sorted(leaderboard,key=lambda r:r['objective'])[:8]:
        runs=[simulate_case(c,prior,row['parameters'])for c in data if c['year']==2010];m=summary(runs,observed)
        selected.append({**row,'selection':m,'selection_objective':m['yield_mae_kg_dry_ha']/12000+m['water_rmse_m3_m3']/.30})
    chosen=min(selected,key=lambda r:r['selection_objective']);write_json(out/'selection_frozen.json',chosen)
    write_json(out/'fitting_leaderboard.json',leaderboard)
    # Freeze independent-year predictions before reading any reserved annual yield labels.
    predicted=[simulate_case(c,prior,chosen['parameters'])for c in test]
    baseline=[simulate_case(c,prior,{})for c in test]
    write_json(out/'test_predictions_frozen.json',{'selected':predicted,'uncalibrated':baseline})
    test_observed={y:labels(project,y)for y in [2012,2013]}
    report={'status':'measured_daily_engine_calibration_completed','protocol':protocol,'selection':chosen,
        'test':summary(predicted,test_observed),'uncalibrated_test':summary(baseline,test_observed),
        'test_by_year':{str(y):summary([r for r in predicted if r['year']==y],test_observed)for y in [2012,2013]if any(r['year']==y for r in predicted)},
        'predictions_sha256':sha256(out/'test_predictions_frozen.json'),'recommendation':None,
        'worldwide_rotation_accuracy_established':False,'elapsed_seconds':time.monotonic()-start}
    write_json(out/'report.json',report);return report
