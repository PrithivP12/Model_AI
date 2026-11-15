"""Existing LIRF 2010 development water/canopy check without new fitting."""
from pathlib import Path
from unittest.mock import patch
import json
from ..io import write_json
from ..data.lirf import old_cases,soil_prior,labels
from ..recovery import process
from ..intelligence.process_experiment import enriched
from ..intelligence.observations import crop_observations
from .plant import BiologicalKernel


class WaterReplay(BiologicalKernel):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.capture_runs=True


def run_water(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    frozen=json.loads((root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())
    parameters=next(x['parameters']for x in frozen['candidates']if x['name']=='fixed_root4_measured_partition')
    prior=process.training_field_capacity(root,soil_prior(root,True));cases=old_cases(root,2010)
    observed={2010:labels(root,2010)};crop,_=crop_observations(root,[2010]);scores={}
    for name,p,options in [('reference',parameters,{'root_beta_per_m':4}),
        ('temperature_plateau',parameters,{'root_beta_per_m':4,'temperature_plateau':True}),
        ('long1900',{**parameters,'maturity_gdd':1900},{'root_beta_per_m':4})]:
        with patch.object(process,'DiagnosticSimulator',WaterReplay):runs=[process.trace_case(c,prior,p,options)for c in cases]
        scores[name]=enriched(runs,observed,crop);write_json(out/(name+'_predictions.json'),runs)
    report={'status':'existing_valid_support_water_and_canopy_checked','development_year':2010,'fit_changed':False,'models':scores,
        'independent_test':False,'reserved_2012_2013_Australia_outcomes_used':False,
        'support':'Same original 0–15/15–45/45–75/75–105cm measured profiles, timing and canopy initialization. Layer geometry remains a row-support approximation.',
        'organ_model_score':None,'organ_model_reason':'Existing replay assimilates aggregate biomass/N from canopy. It cannot initialize measured organ pools; silently inferring new organ observations would invalidate a matched comparison.',
        'phenology':'Existing LIRF emergence records remain in prior diagnostics; no new emergence submodel or fit is introduced.',
        'recommendation':None}
    write_json(out/'report.json',report);return report
