"""Training-only RUE compensation experiment and fixed-cohort transfer harness."""
from pathlib import Path
from dataclasses import asdict,replace
from unittest.mock import patch
from time import perf_counter
import json
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from .. import calibration
from ..io import write_json
from ..processes import default_crops,Processes
from ..data.lirf import old_cases,soil_prior,labels
from ..recovery.process import training_field_capacity,metrics
from ..intelligence.observations import crop_observations
from ..rotation_recovery.inputs import prepare_record
from ..rotation_recovery.experiment import pair_predictions,scores
from ..state_intelligence.trajectories import run_trajectory
from ..state_intelligence.paired import bootstrap_changes
from .kernel import TransferKernel

DAILY_COLUMNS=['date','variant','environment','crop','site','plot','case_id','phase','planting_date',
    'tmin_C','tmax_C','tmean_C','solar_MJ_m2','rain_mm','irrigation_mm',
    'active_growth_day','phenology_fraction_before_growth','gdd_before_growth','growth_temperature_factor',
    'plant_total_N_kg_ha','plant_biomass_dry_kg_ha','lai','root_depth_m','water_stress_factor','n_growth_factor',
    'available_PAR_MJ_m2','intercepted_PAR_MJ_m2','growth_dry_kg_ha','net_assimilated_c_kg_ha',
    'radiation_potential_dry_kg_ha','temperature_loss_dry_kg_ha','water_loss_dry_kg_ha','N_loss_dry_kg_ha',
    'evaporation_mm','transpiration_mm','potential_transpiration_mm','soil_water_mm','soil_mineral_n_kg_ha',
    'water_layer_0_mm','water_layer_1_mm','water_layer_2_mm','water_layer_3_mm',
    'mineral_n_layer_0_kg_ha','mineral_n_layer_1_kg_ha','mineral_n_layer_2_kg_ha','mineral_n_layer_3_kg_ha']


def lirf_trace(case,prior,parameters,options,initialize_canopy=True):
    engines=[]
    def factory(*args,**kwargs):
        engine=TransferKernel(*args,options=options,**kwargs);engines.append(engine);return engine
    with patch.object(calibration,'DepthSimulator',factory):
        result=calibration.simulate_case(case,prior,parameters,initialize_canopy=initialize_canopy)
    engine=engines[0];run=engine.captured[-1]
    water=[]
    cutoff=result['initial_assimilation']['date']
    for row in result['water_predictions']:
        i=[0,15,45,75].index(row['layer_top_cm']);event=case['events'].get(row['date'],{})
        water.append({**row,'case_id':case['id'],'error':row['predicted_theta']-row['observed_theta'],
            'persistence_error':case['soil_observations'][cutoff][i]-row['observed_theta'],
            'irrigation_same_day_mm':event.get('irrigation_mm',0),
            'rain_same_day_mm':next(w['rain_mm']for w in case['weather']if w['date']==row['date'])})
    result['water_rows']=water;result['totals']=run['totals']
    weather={d['date']:d for d in case['weather']}
    daily=[]
    for row in run['daily']:
        d=weather[row['date']]
        daily.append({**row,'tmin_C':d['tmin_C'],'tmax_C':d['tmax_C'],'tmean_C':(d['tmin_C']+d['tmax_C'])/2,
            'solar_MJ_m2':d['solar_MJ_m2'],'case_id':case['id'],'environment':'LIRF',
            'crop':'corn','phase':'post_observed_initialization',
            'planting_date':case['planting_date']})
    # Retain crop state observations and exact initialization support separately.
    return result,daily,{'parameters':parameters,'options':options,'initialize_canopy_requested':initialize_canopy,'initialization':result['initial_assimilation'],
        'whole_run_balance_error':run['whole_run_balance_error']}


def lirf_score(runs,observed,crop_observations_):
    score=metrics(runs,observed);lookup={r['case_id']:r for r in crop_observations_}
    biomass=[(r['harvest_diagnostics'][0]['biomass_dry_kg_ha']*.8,lookup[r['id']]['aboveground_biomass_dry_kg_ha'])for r in runs
             if lookup[r['id']]['aboveground_biomass_dry_kg_ha']is not None]
    score['aboveground_biomass_MAE']=float(np.mean([abs(p-y)for p,y in biomass]))if biomass else None
    canopy=[e for r in runs for e in r['canopy_squared_errors']]
    score['canopy_RMSE']=float(np.sqrt(np.mean(canopy)))if canopy else None
    return score


def summarize_daily(frame):
    """Same physiological bins, not invented observed flowering dates."""
    d=frame.copy();d['stage']='no_growth';d['crop']=d.crop.fillna('bare_soil')
    active=d.active_growth_day.fillna(0).eq(1)
    p=d.phenology_fraction_before_growth
    d.loc[active&(p<.45),'stage']='vegetative_proxy'
    d.loc[active&(p>=.45)&(p<.6),'stage']='flowering_proxy'
    d.loc[active&(p>=.6),'stage']='grain_fill_proxy'
    rows=[]
    for keys,g in d.groupby(['variant','environment','crop','stage'],dropna=False,sort=True):
        denominator=float(g.intercepted_PAR_MJ_m2.sum())
        rows.append({**dict(zip(['variant','environment','crop','stage'],keys)),'days':len(g),
            'Tmin_mean':float(g.tmin_C.mean()),'Tmax_mean':float(g.tmax_C.mean()),'Tmean':float(g.tmean_C.mean()),
            'mean_temperature_factor':float(g.growth_temperature_factor.mean())if g.growth_temperature_factor.notna().any()else None,
            'mean_LAI':float(g.lai.mean()),'solar_mean':float(g.solar_MJ_m2.mean()),
            'intercepted_PAR_sum':denominator,'growth_dry_sum':float(g.growth_dry_kg_ha.sum()),
            'mean_water_factor':float(g.water_stress_factor.mean()),'mean_N_growth_factor':float(g.n_growth_factor.mean()),
            'transpiration_sum':float(g.transpiration_mm.sum()),'evaporation_sum':float(g.evaporation_mm.sum()),
            'root_water_supply_context':'modeled uptake, not a measured flux'})
    return rows


def transfer_gate(lirf,rotation,name):
    ratios={'LIRF_yield':lirf[name]['yield_mae_kg_dry_ha']/lirf['root_reference']['yield_mae_kg_dry_ha'],
        'LIRF_water':lirf[name]['water_rmse_m3_m3']/lirf['root_reference']['water_rmse_m3_m3'],
        'LIRF_canopy':lirf[name]['canopy_RMSE']/lirf['root_reference']['canopy_RMSE'],
        'LIRF_biomass':lirf[name]['aboveground_biomass_MAE']/lirf['root_reference']['aboveground_biomass_MAE'],
        'rotation_yield':rotation[name]['yield_MAE']/rotation['root_reference']['yield_MAE'],
        'rotation_contrast':rotation[name]['mae_difference_kg_dry_ha']/rotation['root_reference']['mae_difference_kg_dry_ha'],
        'rotation_regret':rotation[name]['control_fallback_regret_all_pairs']/rotation['root_reference']['control_fallback_regret_all_pairs']}
    return {'per_metric_ratio':ratios,'worst_ratio':max(ratios.values()),
        'joint_transfer_gate':max(ratios.values())<=1.05 and rotation[name]['yield_MAE']<rotation['root_reference']['yield_MAE']}


def run_cross_environment(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False);started=perf_counter()
    frozen=json.loads((root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())
    p=next(r['parameters']for r in frozen['candidates']if r['name']=='fixed_root4_measured_partition')
    prior=training_field_capacity(root,soil_prior(root,True))
    train=[c for y in [2008,2009]for c in old_cases(root,y)];train_crop,_=crop_observations(root,[2008,2009])
    train_lookup={r['case_id']:r['aboveground_biomass_dry_kg_ha']for r in train_crop};fit_trace=[];cache={}
    def objective(rue):
        runs=[lirf_trace(c,prior,{**p,'rue':float(rue)},{'root_beta_per_m':4,'temperature_plateau':True})[0]for c in train]
        errors=[(r['harvest_diagnostics'][0]['biomass_dry_kg_ha']*.8-train_lookup[r['id']])/train_lookup[r['id']]for r in runs]
        loss=float(np.mean(np.square(errors))+.01*np.log(rue/3.2)**2)
        fit_trace.append({'RUE':float(rue),'objective':loss,'observed_biomass_relative_MAE':float(np.mean(abs(np.asarray(errors))))});return loss
    fit=minimize_scalar(objective,bounds=(1.5,4.5),method='bounded',options={'maxiter':12,'xatol':.015})
    fitted=float(fit.x)
    refdaily=[];training_initialization=[]
    for c in train:
        result,daily,origin=lirf_trace(c,prior,p,{'root_beta_per_m':4});refdaily.extend(daily)
        training_initialization.append({'case':c['id'],**origin['initialization']})
    d=pd.DataFrame(refdaily);active=d.active_growth_day.fillna(0).eq(1)
    t=d.loc[active,'tmean_C'].to_numpy();par=d.loc[active,'intercepted_PAR_MJ_m2'].to_numpy()
    legacy=d.loc[active,'growth_temperature_factor'].to_numpy()
    plateau=np.where(t<15,np.clip((t-8)/7,0,1),np.where(t<=35,1,np.clip((50-t)/15,0,1)))
    energy_rue=float(p['rue']*np.sum(par*legacy)/np.sum(par*plateau))
    write_json(out/'training_fit_frozen_before_development.json',{'fit_years':[2008,2009],'fit_cases':[c['id']for c in train],
        'plateau_fitted_RUE':fitted,'optimizer_success':bool(fit.success),'fit_trace':fit_trace,
        'plateau_training_energy_RUE':energy_rue,'energy_reference_RUE':p['rue'],
        'labels':'Measured aboveground biomass for scalar fit only; no rotation or 2010 values used',
        'energy_scope':'Post measured-initialization training trajectories; conditional exposure calculation, not a fitted productivity bonus.'})
    write_json(out/'training_initialization_audit.json',training_initialization)
    rotation_plant={'rue':3.2,'sla':22.,'leaf_fraction':.4,'maturity_gdd':1500.}
    lirf_plant={k:p[k]for k in rotation_plant}
    variants={
        'original_no_root':({'root_beta_per_m':0},{},True),
        'root_reference':({'root_beta_per_m':4},{},True),
        'plateau':({'root_beta_per_m':4,'temperature_plateau':True},{},True),
        'plateau_fitted_RUE':({'root_beta_per_m':4,'temperature_plateau':True},{'rue':fitted},True),
        'plateau_training_energy_RUE':({'root_beta_per_m':4,'temperature_plateau':True},{'rue':energy_rue},True),
        'rotation_plant_reference':({'root_beta_per_m':4},rotation_plant,True),
        'rotation_plant_plateau':({'root_beta_per_m':4,'temperature_plateau':True},rotation_plant,True),
        'organs_reference':({'root_beta_per_m':4,'organ_model':True},{},True),
        'organs_plateau':({'root_beta_per_m':4,'organ_model':True,'temperature_plateau':True},{},True),
        'no_canopy_reference':({'root_beta_per_m':4},{},False),
        'no_canopy_plateau':({'root_beta_per_m':4,'temperature_plateau':True},{},False)}
    dev=old_cases(root,2010);dev_crop,_=crop_observations(root,[2010]);observed={2010:labels(root,2010)}
    lirf={};all_daily=[]
    for name,(options,changes,canopy)in variants.items():
        runs=[];meta=[]
        for c in dev:
            result,rows,origin=lirf_trace(c,prior,{**p,**changes},options,canopy)
            runs.append(result);meta.append(origin)
            all_daily.extend({key:value for key,value in {'variant':name,**r}.items()if key in DAILY_COLUMNS}for r in rows)
        lirf[name]=lirf_score(runs,observed,dev_crop)
        write_json(out/(name+'_LIRF_predictions.json'),runs);write_json(out/(name+'_LIRF_configuration.json'),meta)
        print('cross LIRF',name,lirf[name]['yield_mae_kg_dry_ha'],flush=True)
    # Same eligible pairs and nuisance N scenarios; no rotation labels fit a coefficient.
    base=root/'reports/rotation-recovery/reproduced-final';records=json.loads((base/'input-audit/records.json').read_text())
    soils=json.loads((base/'input-audit/soil_observations.json').read_text());old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    fixed=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv')
    ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    actual=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
    rotation_specs={k:(v[0],v[1])for k,v in variants.items()if k in ['original_no_root','root_reference','plateau','plateau_fitted_RUE','plateau_training_energy_RUE']}
    rotation_specs.update({'LIRF_plant_reference':({'root_beta_per_m':4},lirf_plant),
        'LIRF_plant_plateau':({'root_beta_per_m':4,'temperature_plateau':True},lirf_plant),
        'LIRF_plant_plateau_fitted_RUE':({'root_beta_per_m':4,'temperature_plateau':True},{**lirf_plant,'rue':fitted})})
    paired=fixed[['pair_id','farm_id','site_id','year','fold','observed_difference','reference_records','candidate_records']].copy();rotation={}
    for name,(options,changes)in rotation_specs.items():
        predictions={};trajectories={};max_error=0.
        for i,rid in enumerate(ids):
            rec=records[rid];scenarios=[]
            for initial_n,pulsed in [(40,True),(120,False)]:
                weather,layers,state,plants,events,_=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',initial_n,pulsed)
                crops=default_crops();c=crops['corn'];crops['corn']=replace(c,max_root_depth_m=1.05,harvest_index=p['harvest_index'],grain_n_fraction=p['grain_n_fraction'],
                    rue_g_dry_per_MJ_PAR=changes.get('rue',3.2),specific_leaf_area_m2_kg=changes.get('sla',22),
                    vegetative_leaf_fraction=changes.get('leaf_fraction',.4),maturity_gdd=changes.get('maturity_gdd',1500))
                opts={**options,'observed_harvest_sowing_dates':[str(pl.date)for pl,s in zip(plants,rec['timeline'])if s['harvest_date']]}
                k=TransferKernel(layers,crops,Processes(maize_n_dilution_a=.034),rec['latitude'],options=opts)
                # Capture post-segmentation daily diagnostics only once for each
                # actual physical plot trajectory; target contexts repeat crops.
                run=run_trajectory(weather,layers,state,plants,events,k,target_year=rec['year'])
                run['initial_N_scenario']=initial_n;run['stage_snapshots']=k.stage_snapshots;scenarios.append(run)
                max_error=max(max_error,max(abs(v)for v in run['ledger_error'].values()))
                # Telemetry is populated by a fresh full trajectory to retain daily rows.
                # Only the latest target context per plot is emitted, N40 scenario.
                latest=max(v['year']for v in records.values()if v['site_id']==rec['site_id']and v['plot_id']==rec['plot_id'])
                if initial_n==40 and rec['year']==latest:
                    kk=TransferKernel(layers,crops,Processes(maize_n_dilution_a=.034),rec['latitude'],options=opts)
                    rr=kk.run(weather,state,plants,events,keep_daily=True)
                    for row,day in zip(rr['daily'],weather):
                        row={'variant':name,'environment':'rotation','site':rec['site_id'],'plot':rec['plot_id'],'case_id':rid,
                            **row,'tmin_C':day.tmin_C,'tmax_C':day.tmax_C,'tmean_C':day.tmean_C,'solar_MJ_m2':day.solar_MJ_m2,
                            'phase':'seed_start_N40_scenario'}
                        all_daily.append({key:value for key,value in row.items()if key in DAILY_COLUMNS})
            predictions[rid]={'prediction':float(np.mean([next(h['grain_dry_kg_ha']for h in r['harvests']if h['planted'].startswith(str(rec['year'])))for r in scenarios]))}
            trajectories[rid]=scenarios
            if i%80==0:print('cross rotation',name,i,flush=True)
        write_json(out/(name+'_rotation_records.json'),predictions);write_json(out/(name+'_rotation_states.json'),trajectories)
        paired[name]=pair_predictions(fixed,predictions)
        rotation[name]={**scores(paired,name),'yield_MAE':float(np.mean([abs(predictions[rid]['prediction']-float(actual.loc[rid,'observed_yield_dry_kg_ha']))for rid in ids])),
            'maximum_WCN_ledger_error':max_error}
    daily=pd.DataFrame(all_daily).reindex(columns=DAILY_COLUMNS)
    daily.to_csv(out/'daily_physiology.csv.gz',index=False,compression={'method':'gzip','mtime':0,'compresslevel':3})
    write_json(out/'stage_environment_summaries.json',summarize_daily(daily))
    paired['history_baseline']=fixed.family_history_baseline;paired.to_csv(out/'paired_predictions.csv',index=False)
    previous=json.loads((root/'reports/biological-recovery/reproduced-final/water/report.json').read_text())['models']
    parity={'LIRF_reference_yield':abs(lirf['root_reference']['yield_mae_kg_dry_ha']-previous['reference']['yield_mae_kg_dry_ha']),
        'LIRF_reference_water':abs(lirf['root_reference']['water_rmse_m3_m3']-previous['reference']['water_rmse_m3_m3'])}
    oldpairs=pd.read_csv(root/'reports/biological-recovery/reproduced-final/biology/paired_predictions.csv')
    parity['rotation_reference_max_difference']=float(np.max(abs(paired.root_reference-oldpairs.legacy_scheduled)))
    if max(parity.values())>1e-8:raise AssertionError('Existing reference changed')
    gates={}
    for name in ['plateau','plateau_fitted_RUE','plateau_training_energy_RUE']:
        gates[name]=transfer_gate(lirf,rotation,name)
    report={'status':'cross_environment_compensation_experiment_complete','fit_years':[2008,2009],'development_lirf':2010,'fixed_rotation_pairs':106,
        'LIRF':lirf,'rotation':rotation,'history_baseline':scores(paired,'history_baseline'),'joint_transfer_gates':gates,
        'reference_parity':parity,'fitted_RUE':fitted,'training_energy_RUE':energy_rue,
        'LIRF_native_plant':lirf_plant,'rotation_native_plant':rotation_plant,
        'uncertainty_vs_history':{name:bootstrap_changes(paired,name,'history_baseline')for name in ['plateau','plateau_fitted_RUE','plateau_training_energy_RUE']},
        'organ_LIRF':['organs_reference','organs_plateau'],
        'organ_LIRF_support':'Seed initialized scenario organ fractions are retained through spinup. Audit finds no qualifying canopy update in any 2008–2010 case, so no organ-stock fabrication is needed to score aggregate crop/water outcomes. Individual organ accuracy is not established.',
        'training_canopy_updates_actually_applied':sum(r['canopy_assimilated']for r in training_initialization),
        'development_canopy_updates_actually_applied':sum(r['initialization']['canopy_assimilated']for r in json.loads((out/'root_reference_LIRF_configuration.json').read_text())),
        'existing_organ_rotation_reference':'reports/biological-recovery/reproduced-final/biology/report.json; same pairs, retained without claiming a new organ test.',
        'daily_stage_semantics':'0–.45/.45–.6/.6–1 GDD fractions are modeled stage proxies; flowering dates not observed in rotation cohort.',
        'recommendation':None,'independent_accuracy_established':False,'model_default_changed':False}
    write_json(out/'report.json',report)
    write_json(out/'performance.json',{'elapsed_seconds':perf_counter()-started,'daily_records':len(daily),'rotation_runs':len(rotation_specs)*len(ids)*2,
        'note':'Wall time is a profile, not a scientific outcome; no physical equations optimized.'})
    return report
