"""Finite predeclared temperature-response diagnostic on exposed cohorts only."""
from pathlib import Path
from unittest.mock import patch
import json

from ..data.lirf import old_cases,soil_prior,labels
from ..recovery.process import training_field_capacity
from ..intelligence.observations import crop_observations
from ..reproductive_recovery.experiment import parameter_profiles
from ..transfer_recovery.cross_environment import lirf_trace,lirf_score
from ..io import write_json,sha256
from .kernel import V11Kernel


def evaluate(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    parameters,_,_=parameter_profiles(root)
    prior=training_field_capacity(root,soil_prior(root,True))
    protocol={'hypothesis':'Suppressing development above the optimum may avoid heat-driven premature maturity.',
        'candidate':'wang_engel_temperature_only','fixed_parameters':parameters,'fit':False,
        'parameter_origin':'Frozen V1 growth cardinal temperatures transferred to development; not cultivar development measurements',
        'training_years':[2008,2009],'exposed_development_years':[2010],
        'selection_rule':'No promotion without stage observations and fixed-cohort rotation noninferiority',
        'sealed_final_test_access':False,'independent_accuracy_claim':False,
        'source':'https://doi.org/10.1016/S0308-521X(98)00028-6',
        'implementation_sha256':sha256(Path(__file__).with_name('kernel.py')),
        'normalization':'Peak development degree-day rate equals optimum minus base; no yield fit'}
    write_json(out/'protocol.json',protocol)
    results={};paired=[]
    for role,years in [('training_diagnostic',[2008,2009]),('exposed_development',[2010])]:
        cases=[c for year in years for c in old_cases(root,year)]
        crop_obs,_=crop_observations(root,years);observed={year:labels(root,year)for year in years}
        models={};predictions={}
        for name,response in [('V1','legacy'),('V1_1_candidate','wang_engel_diagnostic')]:
            options={'root_beta_per_m':4,'shape_alpha':0,'organ_model':False,'phenology_response':response}
            runs=[]
            with patch('agrosim.transfer_recovery.cross_environment.TransferKernel',V11Kernel):
                for case in cases:
                    run,_,_=lirf_trace(case,prior,parameters,options,initialize_canopy=False);runs.append(run)
            predictions[name]=runs;models[name]=lirf_score(runs,observed,crop_obs)
            write_json(out/(role+'_'+name+'_predictions.json'),runs)
        for a,b in zip(predictions['V1'],predictions['V1_1_candidate']):
            if a['id']!=b['id']:raise ArithmeticError('Evaluation case order changed')
            paired.append({'role':role,'case_id':a['id'],'V1_yield':a['predicted_yield_dry_kg_ha'],
                'candidate_yield':b['predicted_yield_dry_kg_ha'],
                'V1_maturity_harvest':a['harvest_diagnostics'][0]['harvested'],
                'candidate_maturity_harvest':b['harvest_diagnostics'][0]['harvested']})
        results[role]={'years':years,'models':models,'difference_candidate_minus_V1':{
            key:models['V1_1_candidate'][key]-models['V1'][key]if models['V1'][key]is not None and models['V1_1_candidate'][key]is not None else None for key in
            ['yield_mae_kg_dry_ha','yield_bias_kg_dry_ha','water_rmse_m3_m3','aboveground_biomass_MAE','canopy_RMSE']},
            'phenology_MAE_days':None,'phenology_reason':'No matched physiological-stage dates for this diagnostic',
            'uncertainty_of_independent_improvement':None,
            'uncertainty_reason':'One development field-year, six treatments; insufficient independent groups for an improvement interval'}
    report={'protocol':protocol,'evaluations':results,'paired_predictions':paired,'promotion':'RETAIN_V1',
        'candidate_status':'EXPERIMENTAL_UNPROMOTED','rotation_candidate_evaluated':False,
        'reason':'Stage-specific development coefficients/stage dates unavailable; rotation noninferiority not established. No yield-only promotion.',
        'independent_final_test':False,'default_changed':False}
    write_json(out/'report.json',report);return report
