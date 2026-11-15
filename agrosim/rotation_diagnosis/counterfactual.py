"""Controlled history × N inputs and chemistry × decay assays, not field effects."""
from pathlib import Path
from dataclasses import replace,asdict
from datetime import date
import itertools,json
from ..processes import Layer,SoilState,Planting,Management,Processes,default_crops
from ..evidence_readiness.identifiability import ShapeKernel
from ..rotation_recovery.weather import historical_weather
from ..ecosystem import EcosystemEngine
from ..io import write_json


def soil():
    return SoilState([75,80,150],[40,30,30],[18000,13500,13500],[1500,1125,1125],[0,0,0],[0,0,0],[12,12,12],1000,20)


def run_factorials(root,out):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    layers=[Layer(.3,.12,.28,.45),Layer(.3,.12,.28,.45),Layer(.6,.12,.28,.45)]
    cal=json.loads((root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())['candidates'][1]['parameters']
    crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=cal['harvest_index'],grain_n_fraction=cal['grain_n_fraction'])
    w=historical_weather(root/'reports/rotation-recovery/weather-exact','ARL',date(2012,12,31));cells=[]
    for prev,prior_N,current_N,decay in itertools.product(['corn','soybeans'],[0.,140.],[0.,140.],[False,True]):
        plants=[Planting(date(2011,5,15),prev,date(2011,10,15)),Planting(date(2012,5,15),'corn',date(2012,10,15))]
        events=[Management(plants[0].date,prior_N,0),Management(plants[1].date,current_N,0)]
        options={'root_beta_per_m':4,'observed_harvest_sowing_dates':[str(p.date)for p in plants]}
        k=ShapeKernel(layers,crops,processes=Processes(maize_n_dilution_a=.034,residue_decay_per_day_20C=.008 if decay else 0),latitude=43.29925,options=options)
        engine=EcosystemEngine(k);initial=engine.initialize(soil());first=engine.run([d for d in w if d.date.year==2011],initial,plants,events,keep_daily=False)
        second=engine.run([d for d in w if d.date.year==2012],first['checkpoint'],plants,events,keep_daily=False)
        h1,h2=first['harvests'][0],second['harvests'][0]
        key=f'{prev}_priorN{prior_N}_currentN{current_N}_decay{decay}'
        first['checkpoint'].checkpoint(out/(key+'_boundary.json'))
        cells.append({'previous_crop':prev,'prior_N':prior_N,'current_N':current_N,'decay':decay,
            'previous_grain':h1['grain_dry_kg_ha'],'current_grain':h2['grain_dry_kg_ha'],
            'prior_fixed_N':h1['fixed_n_kg_ha'],'prior_grain_N_export':h1['grain_n_kg_ha'],
            'prior_partial_fixed_minus_grain_N':h1['fixed_n_kg_ha']-h1['grain_n_kg_ha'],
            'precurrent_soil':first['checkpoint'].soil,'prior_harvest':h1,'current_harvest':h2,
            'cumulative_fluxes':second['checkpoint'].cumulative_fluxes,'ledger_error':second['whole_run_balance_error']})
    def cell(crop,pn,cn,decay):return next(c for c in cells if(c['previous_crop'],c['prior_N'],c['current_N'],c['decay'])==(crop,pn,cn,decay))
    contrasts=[]
    for cn,decay in itertools.product([0.,140.],[False,True]):
        a,b,c,d=[cell(crop,pn,cn,decay)['current_grain']for crop,pn in [('corn',0.),('soybeans',0.),('corn',140.),('soybeans',140.)]]
        interaction=(d-c)-(b-a)
        contrasts.append({'current_N':cn,'decay':decay,'previous_crop_effect_at_prior_N0':b-a,
            'previous_crop_effect_at_prior_N140':d-c,'prior_N_effect_after_corn':c-a,'prior_N_effect_after_soy':d-b,
            'history_by_prior_N_interaction':interaction,'joint_difference':d-a,
            'identity_residual':(b-a)+(c-a)+interaction-(d-a),
            'unequal_package_soy_N0_minus_corn_N140':b-c})
    chemistry=[];weather=[d for d in w if date(2012,5,15)<=d.date<=date(2012,8,15)]
    for CN,decay in itertools.product([25.,70.],[False,True]):
        s=soil();s.surface_residue_c_kg_ha=2000.;s.surface_residue_n_kg_ha=2000./CN
        # Isolate residue chemistry from plant uptake, humus turnover and losses.
        k=ShapeKernel(layers,processes=Processes(humus_decay_per_day_20C=0.,residue_decay_per_day_20C=.008 if decay else 0.,mineral_n_mobility=0.,denitrification_fraction_day=0.),latitude=43.29925)
        r=k.run(weather,s,[],[],keep_daily=False)
        chemistry.append({'CN':CN,'decay':decay,'mineralized_N':r['totals']['mineralized_n_kg_ha'],
            'immobilized_N':r['totals']['immobilized_n_kg_ha'],'final_mineral_N':sum(r['final_state']['mineral_n_kg_ha']),
            'starting_residue_N':2000./CN,'ledger':r['whole_run_balance_error']})
    report={'status':'16_history_N_decay_cells_and4_chemistry_assays','cells':cells,'factorial_contrasts':contrasts,'chemistry':chemistry,
        'field_causal_effect':False,'all_soil_management_are_declared_scenarios':True,
        'prior_N140_for_soy_is_a_diagnostic_not_a_practice_recommendation':True,
        'chemistry_total_N_differs_by_declared_residue_N_stock':True,
        'maximum_conservation_error':max([abs(v)for c in cells for v in c['ledger_error'].values()]+[abs(v)for c in chemistry for v in c['ledger'].values()])}
    if any(abs(c['identity_residual'])>1e-8 for c in contrasts):raise AssertionError('Factorial interaction identity failed')
    write_json(out/'report.json',report);return report
