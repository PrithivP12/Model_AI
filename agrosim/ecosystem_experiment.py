"""Common-weather multi-year ecosystem comparisons and controlled ablations."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
import numpy as np
from .ecosystem import EcosystemEngine
from .simulation import prepare,schedule,economics
from .io import write_json


def compare_ecosystems(config,base):
    physical,soil,scenarios,provenance,area=prepare(config,base)
    if config.get('physical_candidate_options'):
        from .intelligence.physical import PhysicalCandidate
        physical=PhysicalCandidate(physical.layers,physical.crops,physical.processes,physical.latitude,options=config['physical_candidate_options'])
    strategies=config['strategies']
    if len(strategies)<3:raise ValueError('Compare at least three explicit rotation strategies including the current one')
    if len(strategies)*len(scenarios)>150:raise ValueError('Ecosystem experiment exceeds its bounded run budget')
    start=perf_counter();runs=[]
    for strategy in strategies:
        controls=deepcopy(config)
        for name,changes in strategy.get('management_by_crop',{}).items():controls['management_by_crop'][name].update(changes)
        planting,events,resources=schedule(strategy['sequence'],controls,physical.crops,area)
        for name,weather in scenarios:
            engine=EcosystemEngine(physical,config.get('biology'))
            result=engine.run(weather,engine.initialize(soil),planting,events,keep_daily=config.get('keep_daily',False))
            state=result['checkpoint'];ledger=state.cumulative_fluxes
            cost=economics({'harvests':result['harvests']},strategy['sequence'],controls,area,resources)
            result['checkpoint']=asdict(state)
            rows=result['daily'];risk=sum(r['erosion_susceptibility_index']for r in rows)if rows else None
            runs.append({'strategy_id':strategy['id'],'sequence':strategy['sequence'],'weather_scenario':name,
                'resources':resources,'economics':cost,'final_water_mm':result['final_inventory']['water'],
                'final_organic_carbon_kg_ha':sum(state.soil['humus_c_kg_ha']),
                'leached_n_kg_ha':ledger.get('leached_n_kg_ha',0.),'erosion_susceptibility_sum':risk,
                'biological_pressure_indices':state.biological,'simulation':result})
    summaries=[]
    for strategy in strategies:
        group=[r for r in runs if r['strategy_id']==strategy['id']]
        summaries.append({'strategy_id':strategy['id'],'mean_final_water_mm':float(np.mean([r['final_water_mm']for r in group])),
            'mean_organic_carbon_kg_ha':float(np.mean([r['final_organic_carbon_kg_ha']for r in group])),
            'mean_leached_n_kg_ha':float(np.mean([r['leached_n_kg_ha']for r in group])),
            'minimum_scenario_net_return':float(min(r['economics']['net_return']for r in group))if group[0]['economics']else None,
            'irrigation_m3':group[0]['resources']['irrigation_m3']})
    requested=config.get('pareto_objectives',['minimum_scenario_net_return','mean_leached_n_kg_ha','mean_organic_carbon_kg_ha'])
    directions={'minimum_scenario_net_return':1,'mean_leached_n_kg_ha':-1,'mean_organic_carbon_kg_ha':1,'irrigation_m3':-1}
    if not requested or set(requested)-set(directions):raise ValueError('Uncertain biological indices cannot enter the supported physical/economic Pareto objectives')
    for r in summaries:
        if any(r[k]is None for k in requested):raise ValueError('Requested objective lacks explicit economics or a computed physical result')
        score=lambda a:tuple(a[k]*directions[k]for k in requested)
        own=score(r)
        r['pareto_optimal']=not any(all(a>=b for a,b in zip(score(other),own))and any(a>b for a,b in zip(score(other),own))for other in summaries)
    return {'status':'multi_year_coupled_ecosystem_scenarios','current_strategy':strategies[0]['id'],'runs':runs,'summaries':summaries,
        'pareto_objectives':requested,'objective_definitions':{'mean_organic_carbon_kg_ha':'Humus C only; excludes residue and standing plant C. Not a sequestration estimate.'},'weather_provenance':provenance,'elapsed_seconds':perf_counter()-start,
        'recommendation':None,'causal_rotation_effect_established':False,'independent_accuracy_established':False,
        'interpretation':'Scenario ensemble spread is sensitivity, not a calibrated confidence percentage. Biological indices remain separate from the physical/economic Pareto criteria.'}


def residue_loop_experiment():
    """Controlled two-year mechanism check, not experimental rotation validation.

    Two identical first-year soybean crops differ only in shoot residue export.
    Second-year maize receives no N fertilizer. All losses, fixation and exports
    enter the same stock ledger. Parameters/weather are explicit test assumptions.
    """
    from datetime import date,timedelta
    from dataclasses import replace
    from .processes import Layer,SoilState,WeatherDay,Planting,Processes,default_crops
    from .soil import DepthSimulator
    weather=[WeatherDay(date(2025,1,1)+timedelta(days=i),3,15,28,20,3)for i in range(730)]
    crops=default_crops();crops['soybeans']=replace(crops['soybeans'],harvest_index=.15)
    physical=DepthSimulator([Layer(.3,.1,.3,.5),Layer(.7,.1,.3,.5)],crops,
        Processes(humus_decay_per_day_20C=0,denitrification_fraction_day=0))
    soil=SoilState([90,210],[2,3],[0,0],[0,0],[0,0],[0,0],[20,20])
    runs={};errors={'water':0.,'nitrogen':0.,'carbon':0.}
    for name,removal in [('retained',0.),('removed',1.),('retained_no_residue_decay',0.)]:
        kernel=physical if name!='retained_no_residue_decay'else DepthSimulator(physical.layers,crops,replace(physical.processes,residue_decay_per_day_20C=0))
        engine=EcosystemEngine(kernel)
        plantings=[Planting(date(2025,5,1),'soybeans',date(2025,10,1),removal),
                   Planting(date(2026,5,1),'corn',date(2026,10,1))]
        result=engine.run(weather,engine.initialize(soil),plantings)
        harvests=result['harvests'];daily=result['daily'];y2=[r for r in daily if r['date']>='2026-01-01']
        boundary=next(r for r in daily if r['date']=='2025-12-31')
        runs[name]={'year_1_grain_kg_ha':harvests[0]['grain_dry_kg_ha'],
            'year_2_grain_kg_ha':harvests[1]['grain_dry_kg_ha'],
            'residue_c_after_year_1':boundary['residue_c_kg_ha'],
            'mineral_n_after_year_1':boundary['soil_mineral_n_kg_ha'],
            'year_1_fixed_n_kg_ha':harvests[0]['fixed_n_kg_ha'],
            'year_2_mineralized_n_kg_ha':sum(r['mineralized_n_kg_ha']for r in y2),
            'year_2_immobilized_n_kg_ha':sum(r['immobilized_n_kg_ha']for r in y2),
            'year_2_soil_n_uptake_kg_ha':harvests[1]['soil_uptake_n_kg_ha'],
            'whole_run_balance_error':result['whole_run_balance_error'],'final_state':result['checkpoint'].soil}
        for k in errors:errors[k]=max(errors[k],abs(result['whole_run_balance_error'][k]))
    return {'status':'synthetic_conservation_and_mechanism_check','runs':runs,'maximum_ledger_error':errors,
        'evidence_class':'exploratory_scenario','independent_accuracy_established':False,
        'assumptions':'Constant warm weather/rain; low initial N; illustrative soybean HI .15; no fertilizer, humus mineralization or denitrification; compare shoot residue retention plus a residue-decay-off diagnostic knockout.',
        'interpretation':'Demonstrates existing persistent fixation/residue/decomposition/N-uptake/growth feedback. Magnitudes are not field-calibrated rotation benefits.'}
