"""Measured-data parity test of persistent coupling, with exactly the same folds."""
from pathlib import Path
from dataclasses import asdict
import json
import numpy as np
from ..ecosystem import EcosystemEngine,decode_plant
from ..soil import DepthSimulator
from ..data.lirf import old_cases,labels,soil_prior
from ..io import write_json
from .process import trace_case,metrics,training_field_capacity


class CoupledReplay(DepthSimulator):
    def __init__(self,*args,options=None,**kwargs):
        super().__init__(*args,**kwargs);self.captured=[]
        if options:raise ValueError('Measured coupled replay uses unchanged default physical equations')

    def run(self,weather,initial_state,plantings=(),management=(),keep_daily=True,initial_plant=None,allow_unharvested=False):
        engine=EcosystemEngine(self);state=engine.initialize(initial_state,initial_plant)
        result=engine.run(weather,state,plantings,management,keep_daily)
        checkpoint=result['checkpoint'];plant=decode_plant(checkpoint.plant)
        if plant and not allow_unharvested:raise ValueError('Active plant remains outside declared harvest window')
        result.update(final_state=checkpoint.soil,active_plant_state=plant,totals=checkpoint.cumulative_fluxes,
                      initial_inventory=state.initial_inventory)
        self.captured.append(result);return result


def run_coupled_validation(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed coupling validation')
    p=json.loads((root/'reports/completion-pass/quality-corrected/depth-refinement/selected.json').read_text())['parameters'];p['root_depth_m']=1.05
    prior=training_field_capacity(root,soil_prior(root,True));cases=old_cases(root,2010);observed={2010:labels(root,2010)}
    protocol={'fit_years':[2008,2009],'validation_year':2010,'new_fit':False,'fresh_independent_test':False,
        'purpose':'Check that persistent daily coupling reproduces the same measured-data physical predictions and conservation; no ecological accuracy gain expected.',
        'initial_conditions':'Exactly the same measured profile/canopy initialization and explicit adjustment ledger as the reference',
        'organism_predictions':'disabled because no calibrated resource-linked organism fluxes are identifiable'}
    write_json(out/'protocol_frozen.json',protocol)
    ref=[trace_case(c,prior,p)for c in cases];coupled=[trace_case(c,prior,p,coupled=True)for c in cases]
    dy=max(abs(a['predicted_yield_dry_kg_ha']-b['predicted_yield_dry_kg_ha'])for a,b in zip(ref,coupled))
    dw=max(abs(x['predicted_theta']-y['predicted_theta'])for a,b in zip(ref,coupled)for x,y in zip(a['water_rows'],b['water_rows']))
    report={'status':'same_fold_measured_replay_completed','protocol':protocol,'reference':metrics(ref,observed),
        'coupled':metrics(coupled,observed),'maximum_yield_difference_kg_ha':dy,'maximum_theta_difference':dw,
        'parity_passed':dy<1e-7 and dw<1e-10,'empirical_accuracy_improved':False,
        'ablation_scope':{'physical_vs_persistent_coupled':'executed on identical cases',
            'trait_registry_and_learned_flux':'disabled identity behavior; no new predictive mechanism is claimed',
            'advanced_ecology':'not evaluated as an accuracy improvement; unsupported quantitative dynamics remain disabled'},'recommendation':None}
    write_json(out/'predictions.json',{'physical':ref,'coupled':coupled});write_json(out/'report.json',report);return report
