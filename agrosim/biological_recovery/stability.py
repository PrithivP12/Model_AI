"""Synthetic compartment/memory checks; not field accuracy evidence."""
from pathlib import Path
from datetime import date,timedelta
from dataclasses import asdict
from ..io import write_json
from ..processes import Layer,SoilState,WeatherDay,Planting,Management,Processes
from ..ecosystem import EcosystemEngine,EcosystemState
from .plant import BiologicalKernel


def run_stability(output,years=12):
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    start=date(2020,1,1);end=date(2020+years-1,12,31);weather=[];day=start
    while day<=end:
        warm=4<=day.month<=10;weather.append(WeatherDay(day,6. if day.toordinal()%3==0 else 0.,15. if warm else -4.,28. if warm else 6.,20. if warm else 8.,3. if warm else .6));day+=timedelta(days=1)
    layers=[Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)]
    soil=SoilState([75,80,150],[40,30,30],[18000,13500,13500],[1500,1125,1125],[0,0,0],[0,0,0],[12,12,12],1000,20)
    results=[]
    for drop in [0.,1.]:
        plants=[Planting(date(y,5,1),'corn'if y%2==0 else'soybeans',date(y,10,15))for y in range(2020,2020+years)]
        events=[Management(p.date,140 if p.crop=='corn'else 0,0)for p in plants]
        options={'organ_model':True,'temperature_plateau':True,'maturity_leaf_drop_fraction':drop,'observed_harvest_sowing_dates':[str(p.date)for p in plants]}
        engine=EcosystemEngine(BiologicalKernel(layers,processes=Processes(maize_n_dilution_a=.034),options=options))
        whole=engine.run(weather,engine.initialize(soil),plants,events,keep_daily=False)
        checkpoint=engine.initialize(soil);annual=[]
        for year in range(2020,2020+years):
            w=[d for d in weather if d.date.year==year];ps=[p for p in plants if p.date.year==year];es=[e for e in events if e.date.year==year]
            run=engine.run(w,checkpoint,ps,es,keep_daily=False)
            path=out/f'drop{drop}_checkpoint.json';run['checkpoint'].checkpoint(path);checkpoint=EcosystemState.restore(path)
            annual.append({'year':year,'grain':sum(h['grain_dry_kg_ha']for h in run['harvests']),'inventory':run['final_inventory'],
                'mineral_N':sum(checkpoint.soil['mineral_n_kg_ha']),'surface_residue_C':checkpoint.soil['surface_residue_c_kg_ha'],
                'ledger_error':run['whole_run_balance_error']})
        assert asdict(checkpoint)==asdict(whole['checkpoint'])
        results.append({'leaf_drop_fraction':drop,'years':years,'harvests':len(whole['harvests']),
            'maximum_ledger_error':max(abs(v)for r in annual for v in r['ledger_error'].values()),'checkpoint_matches_uninterrupted':True,'annual':annual})
    report={'status':'conservative_organ_memory_scenarios_verified','scenarios':results,'biological_accuracy_established':False,'synthetic_runs_are_training_targets':False}
    write_json(out/'report.json',report);return report
