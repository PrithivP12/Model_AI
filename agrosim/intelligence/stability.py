"""Long-run consistency and time-step sensitivity; no synthetic accuracy claims."""
from pathlib import Path
from dataclasses import replace,asdict
from datetime import date
import json
import numpy as np
from ..io import load_config,write_json
from ..simulation import prepare
from ..processes import Planting,Management,Processes,SoilState
from ..ecosystem import EcosystemEngine,EcosystemState
from ..data.lirf import soil_prior,old_cases
from ..recovery.process import training_field_capacity
from .physical import PhysicalCandidate
from .observations import fit_retention
from .process_experiment import trace


def run_stability(root,output,refinement_path=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed stability test')
    refinement=json.loads(Path(refinement_path or root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())
    chosen=next(c for c in refinement['candidates']if c['name']=='fixed_root4_measured_partition')
    config,base=load_config(root/'configs/ecosystem.yaml');config['end']='2036-12-31';config['weather_scenarios']=config['weather_scenarios'][:1];config['weather_scenarios'][0]['source_start_year']=2016
    physical,soil,scenarios,provenance,_=prepare(config,base);weather=scenarios[0][1];crops=dict(physical.crops)
    crops['corn']=replace(crops['corn'],harvest_index=chosen['parameters']['harvest_index'],grain_n_fraction=chosen['parameters']['grain_n_fraction'],max_root_depth_m=1.05)
    p=Processes(maize_n_dilution_a=.034);runs=[];restart_checked=False
    for initial_n,soc in [(40,20000),(40,60000),(120,20000),(120,60000)]:
        for strategy in ['continuous_maize','alternating_maize_soybean']:
            state=SoilState(**asdict(soil));state.mineral_n_kg_ha=[initial_n*.4,initial_n*.3,initial_n*.3]
            state.humus_c_kg_ha=[soc*.4,soc*.3,soc*.3];state.humus_n_kg_ha=[v/12 for v in state.humus_c_kg_ha]
            kernel=PhysicalCandidate(physical.layers,crops,p,physical.latitude,options=chosen['options']);engine=EcosystemEngine(kernel)
            plants=[];events=[]
            for i,year in enumerate(range(2027,2037)):
                crop='corn'if strategy=='continuous_maize'or i%2==0 else 'soybeans';t=date(year,5,15)
                plants.append(Planting(t,crop,date(year,10,15)));events.append(Management(t,140 if crop=='corn'else 0,20))
            result=engine.run(weather,engine.initialize(state),plants,events)
            yearly=[{'crop':h['crop'],'year':int(h['planted'][:4]),'grain_dry_kg_ha':h['grain_dry_kg_ha'],'fixed_N':h['fixed_n_kg_ha']}for h in result['harvests']]
            soil_end=result['checkpoint'].soil
            row={'strategy':strategy,'initial_n':initial_n,'initial_soc':soc,'annual_harvests':yearly,
                'max_whole_run_error':max(abs(v)for v in result['whole_run_balance_error'].values()),
                'max_daily_error':max(result['max_daily_balance_error'].values()),
                'final_mineral_n':sum(soil_end['mineral_n_kg_ha']),'final_humus_c':sum(soil_end['humus_c_kg_ha']),
                'maximum_residue_c':max(d['residue_c_kg_ha']for d in result['daily']),
                'maximum_soil_n':max(d['soil_mineral_n_kg_ha']for d in result['daily']),
                'grain_yield_min':min(h['grain_dry_kg_ha']for h in result['harvests']),'grain_yield_max':max(h['grain_dry_kg_ha']for h in result['harvests'])}
            runs.append(row)
            if not restart_checked:
                split=next(i for i,w in enumerate(weather)if w.date==date(2032,6,1))
                a=engine.run(weather[:split],engine.initialize(state),plants,events);path=out/'mid_crop_checkpoint.json';a['checkpoint'].checkpoint(path)
                b=engine.run(weather[split:],EcosystemState.restore(path),plants,events)
                assert asdict(b['checkpoint'])==asdict(result['checkpoint'])
                restart_checked=True
    prior=training_field_capacity(root,soil_prior(root,True));retention,_=fit_retention(root,prior);case=old_cases(root,2010)[0];parameters=chosen['parameters']
    substeps={}
    for steps in [24,48,96]:
        options={'redistribution':'darcy','retention':[asdict(r)for r in retention],'substeps':steps}
        run=trace(case,prior,parameters,options)
        substeps[str(steps)]={'water_rmse':float(np.sqrt(np.mean(run['water_squared_errors']))),'yield':run['predicted_yield_dry_kg_ha'],
            'max_balance_error':run['max_balance_error']}
    report={'status':'ten_year_conservation_restart_and_timestep_checks_completed','runs':runs,'restart_matches_exactly':restart_checked,
        'maximum_whole_run_error':max(r['max_whole_run_error']for r in runs),'maximum_daily_error':max(r['max_daily_error']for r in runs),
        'darcy_substep_sensitivity_one_development_case':substeps,'darcy_convergence_is_not_accuracy':True,
        'forcing':provenance[0],'no_annual_state_reset':True,'empirical_long_term_validation':False,
        'interpretation':'Eight ten-year initialization/sequence tests check stock/ledger stability under an existing mapped weather scenario. Yield magnitudes and long-term C/N trends remain unvalidated.',
        'recommendation':None}
    write_json(out/'report.json',report);return report
