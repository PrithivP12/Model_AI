"""Observed state errors, horizon diagnostics and explicitly hypothetical stability."""
from pathlib import Path
from datetime import date,timedelta
from dataclasses import replace
import json
import numpy as np
import pandas as pd
from ..io import write_json
from ..processes import Layer,SoilState,WeatherDay,Planting,Management,Processes,default_crops
from ..ecosystem import EcosystemEngine,EcosystemState
from .trajectories import TraceKernel,VARIANTS


def run_diagnostics(root,states_dir,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False);states=Path(states_dir)
    base=root/'reports/rotation-recovery/reproduced-final'
    records=json.loads((base/'input-audit/records.json').read_text())
    observed=json.loads((base/'validated-observations/observed_tissue_and_grain.json').read_text())['rows']
    actual=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id')
    state_rows=[];yield_rows=[];transition_rows=[];sensitivity=[]
    for variant in VARIANTS:
        runs=json.loads((states/(variant+'_trajectories.json')).read_text())
        for rid,scenarios in runs.items():
            rec=records[rid];pred=[]
            for run in scenarios:
                for h in run['harvests']:
                    y=int(h['planted'][:4]);obs=observed.get(f"{rec['site_id']}:{rec['plot_id']}:{y}",{})
                    if h['crop']=='corn':
                        observed_values={'grain_maturity':obs.get('AGR33'),'shoot_C':None if any(obs.get(k)is None for k in ('AGR09','AGR24'))else obs['AGR09']+obs['AGR24'],
                            'shoot_N':None if any(obs.get(k)is None for k in ('AGR10','AGR26'))else obs['AGR10']+obs['AGR26'],
                            'shoot_dry':None if any(obs.get(k)is None for k in ('AGR04','AGR32'))else obs['AGR04']+obs['AGR32']}
                    else:observed_values={'grain_maturity':obs.get('AGR34'),'shoot_C':obs.get('AGR11'),'shoot_N':obs.get('AGR12'),'shoot_dry':obs.get('AGR05')}
                    simulated={'grain_maturity':h['grain_dry_kg_ha'],'shoot_C':h['shoot_non_grain_dry']*.45,
                               'shoot_N':h['plant_total_N']-h['grain_n_kg_ha']-h['root_residue_N'],'shoot_dry':h['shoot_non_grain_dry']}
                    for variable,value in observed_values.items():
                        if value is None or value<0:continue
                        state_rows.append({'variant':variant,'farm':rec['farm_id'],'site':rec['site_id'],'plot':rec['plot_id'],
                            'crop_year':y,'crop':h['crop'],'horizon_years':y-2010,'N_scenario':run['initial_N_scenario'],'variable':variable,
                            'observed':value,'predicted':simulated[variable],'record_context':rid,'sample_exact_time_known':False})
                    if y==rec['year']:pred.append(h['grain_dry_kg_ha'])
                transition_rows.append({'variant':variant,'record_id':rid,'N_scenario':run['initial_N_scenario'],
                    'simulated_lifecycle_events':run['lifecycle'],'maximum_ledger_error':max(abs(v)for v in run['ledger_error'].values()),
                    'horizon_years':rec['year']-2010,'state_adjustments':run['state_adjustments']})
            observed_yield=float(actual.loc[rid,'observed_yield_dry_kg_ha']);prediction=float(np.mean(pred))
            yield_rows.append({'variant':variant,'record_id':rid,'farm':rec['farm_id'],'year':rec['year'],'horizon_years':rec['year']-2010,
                               'observed':observed_yield,'prediction':prediction,'absolute_error':abs(prediction-observed_yield)})
            sensitivity.append({'variant':variant,'record_id':rid,'yield_N40':pred[0],'yield_N120':pred[1],'spread':abs(pred[0]-pred[1])})
    raw=pd.DataFrame(state_rows);raw.to_csv(out/'state_errors_all_contexts.csv',index=False)
    # The same prior plot-year appears in several target trajectories. Do not count
    # repeated contexts/scenarios as independent measured crop samples.
    dedup=raw.groupby(['variant','farm','site','plot','crop_year','crop','horizon_years','variable'],as_index=False).agg(observed=('observed','first'),predicted=('predicted','mean'),context_prediction_min=('predicted','min'),context_prediction_max=('predicted','max'))
    dedup['error']=dedup.predicted-dedup.observed;dedup.to_csv(out/'state_errors_unique_plot_year.csv',index=False)
    summaries=[]
    for (variant,crop,variable,horizon),g in dedup.groupby(['variant','crop','variable','horizon_years'],sort=True):
        summaries.append({'variant':variant,'crop':crop,'variable':variable,'horizon_years':int(horizon),'unique_plot_years':len(g),'farms':g.farm.nunique(),
            'MAE':float(g.error.abs().mean()),'RMSE':float(np.sqrt(np.mean(g.error**2))),'bias':float(g.error.mean()),
            'observed_mean':float(g.observed.mean()),'predicted_mean':float(g.predicted.mean())})
    pd.DataFrame(summaries).to_csv(out/'state_errors_by_horizon.csv',index=False)
    yieldframe=pd.DataFrame(yield_rows);yieldframe.to_csv(out/'target_yield_errors.csv',index=False)
    yields=[]
    for (variant,horizon),g in yieldframe.groupby(['variant','horizon_years'],sort=True):
        yields.append({'variant':variant,'horizon_years':int(horizon),'records':len(g),'farms':g.farm.nunique(),'MAE':float(g.absolute_error.mean())})
    pd.DataFrame(yields).to_csv(out/'yield_errors_by_horizon.csv',index=False)
    pd.DataFrame(sensitivity).to_csv(out/'initial_N_sensitivity.csv',index=False);write_json(out/'transition_audit.json',transition_rows)
    report={'status':'observed_state_and_horizon_diagnosis_complete','state_summary':summaries,'yield_by_horizon':yields,
        'overall_yield_MAE':{name:float(g.absolute_error.mean())for name,g in yieldframe.groupby('variant',sort=True)},
        'state_measurement_support':'Maturity-stage biomass/tissue versus scheduled-harvest model stock; exact sample times unknown. Soy sample follows leaf drop; leaf export/turnover is not modeled. This is a qualified diagnostic, not exact-date state validation.',
        'context_dependence_max_spread':float((dedup.context_prediction_max-dedup.context_prediction_min).max()),
        'horizon_interpretation':'Horizons 1–4 start in Jan2011. Crop, site, weather and management composition change with horizon; apparent error growth is not identified propagation causality.',
        'root_biomass_error':None,'root_reason':'No compatible measured root biomass in this fixed cohort; roots use a fixed crop fraction and are unvalidated.',
        'soil_water_error':None,'water_reason':'Existing GHG moisture units unresolved and 5cm probes differ from model layer volumes. No rescaling or assimilation.',
        'mineral_N_validation':'Prior phase: 96 concentration comparisons at one farm conditional on year-bounded density proxy. Not a compatible exact-date profile-stock observation for assimilation.',
        'residue_return_validation':None,'recommendation':None}
    write_json(out/'report.json',report);return report


def stability(output, years=20):
    """Hypothetical annual cycles check ledgers/restarts, never empirical accuracy."""
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    start=date(2020,1,1);end=date(2020+years-1,12,31)
    weather=[];day=start
    while day<=end:
        summer=4<=day.month<=10
        weather.append(WeatherDay(day,6. if day.toordinal()%3==0 else 0.,12. if summer else -4.,27. if summer else 6.,20. if summer else 8.,3. if summer else .6));day+=timedelta(days=1)
    layers=[Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)]
    scenarios=[]
    for name,initial_n,fertilizer,removal in [('no_N_inputs',40,0,0),('recorded_scenario_N140',40,140,0),('N200_with_residue_removal',120,200,1)]:
        plants=[Planting(date(y,5,1),'corn'if(y-2020)%2==0 else'soybeans',date(y,10,15),removal)for y in range(2020,2020+years)]
        events=[Management(p.date,fertilizer if p.crop=='corn'else 0,0)for p in plants]
        crops=default_crops();kernel=TraceKernel(layers,crops,Processes(maize_n_dilution_a=.034),40.,options={'root_beta_per_m':4,'observed_harvest_sowing_dates':[str(p.date)for p in plants]})
        engine=EcosystemEngine(kernel)
        soil=SoilState([75,80,150],[initial_n*.4,initial_n*.3,initial_n*.3],[18000,13500,13500],[1500,1125,1125],[0,0,0],[0,0,0],[12,12,12],1000,20)
        state=engine.initialize(soil);harvests=[];annual=[];max_error=0.;restart_difference=0.
        for y in range(2020,2020+years):
            w=[d for d in weather if d.date.year==y];p=[p for p in plants if p.date.year==y];e=[e for e in events if e.date.year==y]
            result=engine.run(w,state,p,e,keep_daily=False)
            # JSON round trip annually is an actual restart, not just object reuse.
            path=out/(name+'_checkpoint.json');result['checkpoint'].checkpoint(path);state=EcosystemState.restore(path)
            harvests.extend(result['harvests']);max_error=max(max_error,max(abs(v)for v in result['whole_run_balance_error'].values()))
            annual.append({'year':y,'inventory':result['final_inventory'],'yield':sum(h['grain_dry_kg_ha']for h in result['harvests'])})
        whole=engine.run(weather,engine.initialize(soil),plants,events,keep_daily=False)
        restart_difference=max(abs(whole['final_inventory'][k]-result['final_inventory'][k])for k in whole['final_inventory'])
        if asdict_state(whole['checkpoint'])!=asdict_state(state):raise AssertionError('Annual restart differs from uninterrupted run')
        scenarios.append({'name':name,'years':years,'harvests':len(harvests),'max_ledger_error':max_error,
            'restart_max_inventory_difference':restart_difference,'annual':annual,
            'initial_N':initial_n,'maize_fertilizer':fertilizer,'shoot_removal_fraction':removal,
            'evidence':'hypothetical stress/stability scenario, not observed farm performance'})
    report={'status':'long_horizon_numerics_checked','scenarios':scenarios,'biological_accuracy_established':False,'synthetic_runs_used_as_accuracy_labels':False}
    write_json(out/'report.json',report);return report


def asdict_state(state):
    from dataclasses import asdict
    return asdict(state)
