"""Actual historical weather interventions and restart tests, without visuals."""
from pathlib import Path
from dataclasses import replace,asdict
from datetime import date
from ..rotation_recovery.weather import historical_weather
from ..processes import Layer,SoilState,Planting,Management,Processes
from ..evidence_readiness.identifiability import ShapeKernel
from ..ecosystem import EcosystemEngine,EcosystemState
from ..io import write_json
from .kernel import ReproductiveKernel


def run_memory(root,coefficient,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    weather=historical_weather(Path(root)/'reports/rotation-recovery/weather-exact','ARL',date(2013,12,31))
    layers=[Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)]
    soil=SoilState([75,80,150],[40,30,30],[18000,13500,13500],[1500,1125,1125],[0,0,0],[0,0,0],[12,12,12],1000,20)
    plants=[Planting(date(y,5,15),'soybeans'if y==2012 else'corn',date(y,10,15))for y in [2011,2012,2013]]
    events=[Management(p.date,0 if p.crop=='soybeans'else 140,0)for p in plants]
    results={}
    for kind in ['organ_HI','source_sink']:
        for removal in [0.,1.]:
            options={'organ_model':True,'shape_alpha':1.,'root_beta_per_m':4,'sink_coefficient':coefficient,
                'observed_harvest_sowing_dates':[str(p.date)for p in plants]}
            cls=ReproductiveKernel if kind=='source_sink'else ShapeKernel
            kernel=cls(layers,processes=Processes(maize_n_dilution_a=.034),latitude=43.29925,options=options)
            engine=EcosystemEngine(kernel)
            ps=[replace(p,aboveground_residue_removal_fraction=removal)if p.date.year==2011 else p for p in plants]
            whole=engine.run(weather,engine.initialize(soil),ps,events,keep_daily=False)
            checkpoint=engine.initialize(soil);annual=[]
            for y in [2011,2012,2013]:
                yr=engine.run([d for d in weather if d.date.year==y],checkpoint,ps,events,keep_daily=False)
                path=out/f'{kind}_export{removal}_{y}.json';yr['checkpoint'].checkpoint(path);checkpoint=EcosystemState.restore(path)
                annual.append({'year':y,'harvests':yr['harvests'],'soil':asdict(checkpoint)['soil'],'ledger':yr['whole_run_balance_error']})
            if asdict(checkpoint)!=asdict(whole['checkpoint']):raise AssertionError('Resumed reproductive state changed prediction')
            results[kind+('_export'if removal else'_retain')]={'annual':annual,'final_inventory':whole['final_inventory'],
                'ledger':whole['whole_run_balance_error'],'checkpoint_matches_uninterrupted':True,
                'fluxes':whole['checkpoint'].cumulative_fluxes}
    interventions={}
    for kind in ['organ_HI','source_sink']:
        a,b=results[kind+'_retain'],results[kind+'_export']
        interventions[kind]={'first_year_shoot_export_only':True,
            '2012_grain_difference':b['annual'][1]['harvests'][0]['grain_dry_kg_ha']-a['annual'][1]['harvests'][0]['grain_dry_kg_ha'],
            '2013_grain_difference':b['annual'][2]['harvests'][0]['grain_dry_kg_ha']-a['annual'][2]['harvests'][0]['grain_dry_kg_ha'],
            '2012_Jan1_mineral_N_difference':sum(b['annual'][0]['soil']['mineral_n_kg_ha'])-sum(a['annual'][0]['soil']['mineral_n_kg_ha']),
            '2012_Jan1_water_difference':sum(b['annual'][0]['soil']['water_mm'])-sum(a['annual'][0]['soil']['water_mm']),
            '2012_Jan1_surface_residue_C_difference':b['annual'][0]['soil']['surface_residue_c_kg_ha']-a['annual'][0]['soil']['surface_residue_c_kg_ha']}
    report={'status':'reproductive_residue_carryover_and_restart_verified','runs':results,'export_minus_retention':interventions,
        'real_world_accuracy_established':False,'soil_management_are_frozen_scenarios':True,'weather':'Existing ARL2011–2013 POWER historical sequence; not new observations.'}
    write_json(out/'report.json',report);return report
