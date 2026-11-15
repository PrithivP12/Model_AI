"""Controlled NASA-forced ecological-memory interventions with explicit pathways."""
from pathlib import Path
from dataclasses import replace
from datetime import date
import numpy as np
from ..io import write_json
from ..processes import Layer,SoilState,Planting,Management,Processes
from ..ecosystem import EcosystemEngine
from ..rotation_recovery.weather import historical_weather
from .kernel import TransferKernel


def run_memory(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    weather=historical_weather(root/'reports/rotation-recovery/weather-exact','ARL',date(2013,12,31))
    layers=[Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)]
    initial=SoilState([75,80,150],[40,30,30],[18000,13500,13500],[1500,1125,1125],[0,0,0],[0,0,0],[12,12,12],1000,20)
    plantings=[Planting(date(2011,5,15),'soybeans',date(2011,10,15)),Planting(date(2012,5,15),'corn',date(2012,10,15)),Planting(date(2013,5,15),'corn',date(2013,10,15))]
    events=[Management(p.date,140 if p.crop=='corn'else 0,0)for p in plantings]
    variants={'retain':{},'export_first_shoot':{'export':True},'no_residue_decay':{'decay':0.},'no_fixation':{'fixation':0.}}
    runs={}
    for name,spec in variants.items():
        ps=[replace(p,aboveground_residue_removal_fraction=1.)if spec.get('export')and p.date.year==2011 else p for p in plantings]
        k=TransferKernel(layers,processes=Processes(maize_n_dilution_a=.034,residue_decay_per_day_20C=.008*spec.get('decay',1.)),
            latitude=43.29925,options={'root_beta_per_m':4,'observed_harvest_sowing_dates':[str(p.date)for p in ps]})
        if 'fixation'in spec:k.crops={**k.crops,'soybeans':replace(k.crops['soybeans'],fixation_max_n_kg_ha_day=0.)}
        e=EcosystemEngine(k);result=e.run(weather,e.initialize(initial),ps,events,keep_daily=True)
        rows=result['daily'];annual=[]
        for y in [2011,2012,2013]:
            d=[r for r in rows if r['date'].startswith(str(y))];last=d[-1]
            annual.append({'year':y,'harvests':[h for h in result['harvests']if h['planted'].startswith(str(y))],
                'year_end':{key:last[key]for key in ['soil_water_mm','soil_mineral_n_kg_ha','soil_organic_c_kg_ha','residue_c_kg_ha']},
                'fluxes':{key:float(sum(r.get(key,0.)for r in d))for key in ['evaporation_mm','transpiration_mm','leached_n_kg_ha','denitrified_n_kg_ha','mineralized_n_kg_ha','immobilized_n_kg_ha','fixed_n_kg_ha','plant_uptake_n_kg_ha','respired_c_kg_ha','harvested_c_kg_ha','harvested_n_kg_ha']}})
        runs[name]={'annual':annual,'final_inventory':result['final_inventory'],'whole_run_balance_error':result['whole_run_balance_error'],
            'cumulative_fluxes':result['checkpoint'].cumulative_fluxes}
    control=runs['retain'];comparisons=[]
    for name in variants:
        if name=='retain':continue
        for a,b in zip(control['annual'],runs[name]['annual']):
            comparisons.append({'intervention':name,'year':a['year'],
                'grain_difference':sum(h['grain_dry_kg_ha']for h in b['harvests'])-sum(h['grain_dry_kg_ha']for h in a['harvests']),
                'year_end_state_difference':{k:b['year_end'][k]-a['year_end'][k]for k in a['year_end']},
                'flux_difference':{k:b['fluxes'][k]-a['fluxes'][k]for k in a['fluxes']},
                'manipulated_variable':'First soybean harvest shoot export only'if name=='export_first_shoot'else'One named process knockout; diagnostic, not a biological alternative',
                'everything_else':'Same weather, planting/crops, N, irrigation, starting stocks and kernel parameters except named manipulation'})
    initial_grain=lambda run:run['annual'][0]['harvests'][0]['grain_dry_kg_ha']
    if initial_grain(runs['retain'])!=initial_grain(runs['export_first_shoot']):raise AssertionError('Residue export changed prior growth before manipulation')
    if max(abs(v)for r in runs.values()for v in r['whole_run_balance_error'].values())>1e-6:raise ArithmeticError('Memory experiment ledger failed')
    report={'status':'controlled_ecosystem_memory_pathways_executed','runs':runs,'comparisons':comparisons,
        'first_crop_growth_identical_before_residue_export':True,
        'evidence':'NASA ARL weather drives a declared soil/crop/calendar/management scenario. These are not observed trial conditions or real rotation accuracy labels.',
        'pathways':['shoot export to explicit harvested C/N output','surface residue -> evaporation/cover','residue C/N -> mineralization/immobilization -> crop uptake','water and mineral N -> next-crop assimilation'],
        'model_world_causal_intervention_only':True,'real_world_rotation_benefit_validated':False,'recommendation':None}
    write_json(out/'report.json',report);return report
