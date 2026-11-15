"""NASA-forced, resource-feasible strategy scenarios; never a universal winner."""
from dataclasses import asdict,replace
from copy import deepcopy
import math
import numpy as np
from ..simulation import prepare,schedule,economics
from ..ecosystem import EcosystemEngine
from .plant import BiologicalKernel


def compare_strategies(config,base):
    physical,soil,weather,provenance,area=prepare(config,base)
    strategies=config['strategies'];current=config['current_strategy']
    if len(strategies)<3 or len({s['id']for s in strategies})!=len(strategies):raise ValueError('At least three unique strategies required')
    if current not in {s['id']for s in strategies}:raise ValueError('Named current strategy must be included')
    priorities=config.get('farmer_priorities',['water_supply','N_loss','soil_organic_C'])
    allowed={'water_supply','N_loss','soil_organic_C','scenario_net_return'}
    if not priorities or set(priorities)-allowed:raise ValueError('Unknown or unsupported farmer priority')
    for transform in config.get('climate_sensitivities',[]):
        rain=transform['rain_multiplier'];heat=transform['temperature_delta_C']
        if not 0<=rain<=3 or not -10<=heat<=10:raise ValueError('Climate sensitivity outside declared bounds')
        weather.extend([(name+'__'+transform['id'],[replace(d,rain_mm=d.rain_mm*rain,tmin_C=d.tmin_C+heat,tmax_C=d.tmax_C+heat)for d in days])for name,days in list(weather)if '__'not in name])
    if len(weather)*len(strategies)>100:raise ValueError('Strategy scenario budget exceeded')
    runs=[];rejected=[]
    for strategy in strategies:
        controls=deepcopy(config)
        for crop,changes in strategy.get('management_by_crop',{}).items():controls['management_by_crop'][crop].update(changes)
        try:plants,events,resources=schedule(strategy['sequence'],controls,physical.crops,area)
        except ValueError as exc:rejected.append({'id':strategy['id'],'reason':str(exc)});continue
        for name,days in weather:
            options={'root_beta_per_m':4,**config.get('biological_profile',{}),'observed_harvest_sowing_dates':[str(p.date)for p in plants],'harvest_calendar_role':'planned'}
            # Planned deadlines are scenario operations, not observed maturity/harvest.
            kernel=BiologicalKernel(physical.layers,physical.crops,physical.processes,physical.latitude,options=options)
            engine=EcosystemEngine(kernel);result=engine.run(days,engine.initialize(soil),plants,events,keep_daily=True)
            state=result['checkpoint'];daily=result['daily'];flux=state.cumulative_fluxes
            cost=economics({'harvests':result['harvests']},strategy['sequence'],controls,area,resources)
            cover=[];stress=[]
            for row in daily:
                residue=1-math.exp(-row['residue_c_kg_ha']/.45/2000.)
                canopy=1-math.exp(-.6*row['lai']);cover.append(1-(1-canopy)*(1-residue))
                if row['crop']:stress.append(row['water_stress_factor'])
            slope=config.get('erosion_slope_scenario')
            erosion=None if slope is None else float(np.mean([slope*(1-c)*(1-math.exp(-(d.rain_mm+r['runoff_mm'])/25))for c,d,r in zip(cover,days,daily)]))
            annual=[{k:row[k]for k in ['date','soil_water_mm','soil_mineral_n_kg_ha','soil_organic_c_kg_ha','residue_c_kg_ha']}for row in daily if row['date'].endswith('12-31')]
            runs.append({'strategy_id':strategy['id'],'sequence':strategy['sequence'],'scenario_id':name,
                'weather_role':'declared hypothetical climate sensitivity'if '__'in name else'historical-weather replay mapped to future dates, not a forecast',
                'crop_productivity':[{'crop':h['crop'],'planted':h['planted'],'grain_kg_dry_ha':h['grain_dry_kg_ha']}for h in result['harvests']],
                'resources':resources,'economics':cost,'actual_ET_mm':flux.get('transpiration_mm',0)+flux.get('evaporation_mm',0),
                'soil_evaporation_mm':flux.get('evaporation_mm',0),'crop_transpiration_mm':flux.get('transpiration_mm',0),
                'returned_shoot_C_kg_ha':sum(h.get('shoot_residue_c_added_kg_ha',0.)for h in result['harvests']),
                'returned_shoot_N_kg_ha':sum(h.get('shoot_residue_n_added_kg_ha',0.)for h in result['harvests']),
                'returned_root_C_kg_ha':sum(h.get('root_residue_C',0.)for h in result['harvests']),
                'returned_root_N_kg_ha':sum(h.get('root_residue_N',0.)for h in result['harvests']),
                'leaf_litter_C_kg_ha':flux.get('leaf_litter_C_kg_ha',0),'leaf_litter_N_kg_ha':flux.get('leaf_litter_N_kg_ha',0),
                'mineralized_N_kg_ha':flux.get('mineralized_n_kg_ha',0),'immobilized_N_kg_ha':flux.get('immobilized_n_kg_ha',0),
                'potential_crop_transpiration_mm':flux.get('potential_transpiration_mm',0),'mean_crop_water_factor':float(np.mean(stress))if stress else None,
                'final_mineral_N_kg_ha':sum(state.soil['mineral_n_kg_ha']),'leached_N_kg_ha':flux.get('leached_n_kg_ha',0),
                'denitrified_N_kg_ha':flux.get('denitrified_n_kg_ha',0),'fixed_N_kg_ha':flux.get('fixed_n_kg_ha',0),
                'final_humus_C_kg_ha':sum(state.soil['humus_c_kg_ha']),'final_residue_C_kg_ha':sum(state.soil['residue_c_kg_ha'])+state.soil['surface_residue_c_kg_ha'],
                'mean_cover_proxy':float(np.mean(cover)),'erosion_susceptibility_proxy':erosion,'erosion_mass_loss':None,
                'annual_states':annual,'budget_error':result['whole_run_balance_error'],
                'mechanism_explanation':{'growth':'Intercepted PAR, temperature, water/N uptake and optional organ transfers; no history bonus.',
                    'memory':'Root/shoot residues, humus mineralization, fixation, N losses and water stocks persist.',
                    'cover':'LAI and residue cover proxies; erosion is not calibrated soil loss.'},
                'biological_risks':{'quantitative_pest_pathogen_pollinator_effects':None,'reason':'Local presence, initial abundance and defensible rates unavailable; trait registry preserved.'}})
            # Opt-in telemetry for the local application. This does not alter the
            # solver, aggregate metrics, defaults, or any existing experiment.
            if config.get('keep_daily',False):
                runs[-1]['daily']=daily
                runs[-1]['weather']=[{'date':str(d.date),'rain_mm':d.rain_mm,'tmin_C':d.tmin_C,
                    'tmax_C':d.tmax_C,'solar_MJ_m2':d.solar_MJ_m2}for d in days]
                runs[-1]['management']=[{'date':str(e.date),'fertilizer_n_kg_ha':e.fertilizer_n_kg_ha,
                    'irrigation_mm':e.irrigation_mm}for e in events]
    feasible={r['strategy_id']for r in runs}
    if current not in feasible or len(feasible)<3:raise ValueError('Need current plus two feasible complete alternatives; cannot silently compare fewer')
    summaries=[]
    for strategy in strategies:
        g=[r for r in runs if r['strategy_id']==strategy['id']]
        if not g:continue
        summaries.append({'strategy_id':strategy['id'],'is_current':strategy['id']==current,'scenarios':len(g),
            'water_supply':g[0]['resources']['irrigation_m3'],'N_loss':float(np.mean([r['leached_N_kg_ha']+r['denitrified_N_kg_ha']for r in g])),
            'soil_organic_C':float(np.mean([r['final_humus_C_kg_ha']for r in g])),
            'scenario_net_return':float(min(r['economics']['net_return']for r in g))if g[0]['economics']else None,
            'climate_sensitivity_net_return_range':[float(min(r['economics']['net_return']for r in g)),float(max(r['economics']['net_return']for r in g))]if g[0]['economics']else None})
    direction={'water_supply':-1,'N_loss':-1,'soil_organic_C':1,'scenario_net_return':1}
    if any(s[k]is None for s in summaries for k in priorities):raise ValueError('Requested priority lacks supported scenario output')
    for s in summaries:
        own=[s[k]*direction[k]for k in priorities]
        s['scenario_pareto']=not any(all(a>=b for a,b in zip([t[k]*direction[k]for k in priorities],own))and any(a>b for a,b in zip([t[k]*direction[k]for k in priorities],own))for t in summaries)
    return {'status':'experimental_ecosystem_strategy_scenarios','current_strategy':current,'farmer_priorities':priorities,'summaries':summaries,'runs':runs,'rejected':rejected,
        'weather_provenance':provenance,'climate_sensitivities':config.get('climate_sensitivities',[]),
        'forecast_available':False,'scenario_spread_is_prediction_interval':False,'recommendation':None,'validated_rotation_ranking':False,
        'unsupported':{'pest_populations':None,'disease_penalties':None,'calibrated_erosion':None,'carbon_sequestration_claim':None},
        'limitations':['Biological profile and soil/management scenarios are not verified farm calibration.','No sum of unlike-crop tonnes is used as an optimization objective.','Economic prices and climate transformations are supplied assumptions, not future probabilities.']}
