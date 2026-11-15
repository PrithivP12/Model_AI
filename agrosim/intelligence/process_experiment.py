"""Fixed-split physical candidates and one-dimensional regularized flux learning."""
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict
import json,math
import numpy as np
from scipy.optimize import minimize_scalar
from .. import calibration
from ..data.lirf import old_cases,labels,soil_prior
from ..recovery.process import metrics,training_field_capacity
from ..io import write_json,sha256
from .physical import PhysicalCandidate
from .observations import fit_retention,crop_observations,emergence_requirement


def trace(case,prior,parameters,options):
    from ..recovery import process
    with patch.object(process,'DiagnosticSimulator',PhysicalCandidate):
        result=process.trace_case(case,prior,parameters,options)
    return result


def enriched(runs,observed,crop):
    score=metrics(runs,observed);lookup={c['case_id']:c for c in crop}
    pairs=[(r['harvest_diagnostics'][0]['biomass_dry_kg_ha']*.8,lookup[r['id']]['aboveground_biomass_dry_kg_ha'])for r in runs if lookup[r['id']]['aboveground_biomass_dry_kg_ha']is not None]
    if pairs:
        a,b=np.array(pairs).T;score['aboveground_biomass_mae_kg_ha']=float(np.mean(abs(a-b)));score['aboveground_biomass_bias_kg_ha']=float(np.mean(a-b))
    canopy=[v for r in runs for v in r['canopy_squared_errors']]
    score['canopy_cover_rmse']=float(np.sqrt(np.mean(canopy)))if canopy else None
    return score


def run_process(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Completed physical experiment is immutable')
    source=root/'reports/completion-pass/quality-corrected/depth-refinement/selected.json';p=json.loads(source.read_text())['parameters'];p['root_depth_m']=1.05
    prior=training_field_capacity(root,soil_prior(root,True));retention,retention_audit=fit_retention(root,prior)
    train=[c for y in [2008,2009]for c in old_cases(root,y)];validation=old_cases(root,2010)
    crops,stages=crop_observations(root,[2008,2009]);emergence,emergence_audit=emergence_requirement(train,stages)
    measurements={y:labels(root,y)for y in [2008,2009]}
    measured_hi=float(np.mean([c['biomass_sample_harvest_index']for c in crops]));grain_n=float(np.mean([c['grain_n_fraction']for c in crops]))
    hydraulic={'redistribution':'darcy','retention':[asdict(r)for r in retention],'substeps':24}
    candidates=[('reference',p,{}),('root_weighted_2',p,{'root_beta_per_m':2}),('root_weighted_4',p,{'root_beta_per_m':4}),
        ('darcy',p,hydraulic),('darcy_roots',p,{**hydraulic,'root_beta_per_m':2}),
        ('emergence',p,{'emergence_gdd':emergence}),
        ('measured_partition',{**p,'harvest_index':measured_hi,'grain_n_fraction':grain_n},{}),
        ('root_emergence',p,{'root_beta_per_m':2,'emergence_gdd':emergence})]
    protocol={'fit_years':[2008,2009],'development_year':2010,'test_outcomes_loaded':False,
        'candidates':[name for name,_,_ in candidates]+['regularized_conductivity','biomass_calibrated_rue'],
        'selection':'All candidate definitions frozen before scoring; fit conductivity and RUE only on 2008–2009. Report all 2010 outcomes, including failures.',
        'acceptance':'At least 5% lower layer water RMSE; yield MAE and canopy RMSE no more than 5% worse than frozen reference. Persistence/ML/rotation baselines also required for any release.',
        'conductivity_learning':'One log multiplier, bounds [.1,10], weight .02*log(multiplier)^2; latent flux inverse calibration from observed profiles, not directly identified conductivity.',
        'biomass_learning':'One RUE parameter from observed final aboveground biomass, bounds [2.5,5], weight .02*log(RUE/frozen_RUE)^2. Other parameters fixed.',
        'forbidden':'No simulated targets, no 2012–2013/Australia labels, no observation time shift, no arbitrary yield multipliers',
        'references':['https://www.ars.usda.gov/pacific-west-area/riverside-ca/agricultural-water-efficiency-and-salinity-research-unit/docs/model/rosetta-hydraulic-functions/','https://doi.org/10.1016/S0022-1694(01)00456-5'],
        'root_distribution_status':'Scenario shape parameters for a compensated exponential allocation; not an implementation of the referenced full model.',
        'saturation_and_conductivity_unmeasured':True}
    write_json(out/'protocol_frozen.json',protocol);write_json(out/'hydraulics.json',retention_audit);write_json(out/'training_crop_observations.json',{'measurements':crops,'stage_records':stages,'emergence_fit':emergence_audit,'emergence_gdd':emergence,'measured_mean_hi':measured_hi,'measured_mean_grain_n_fraction':grain_n})
    training=[];stored={}
    for name,parameters,options in candidates:
        runs=[trace(c,prior,parameters,options)for c in train];stored[name]=runs
        row={'name':name,'fit':enriched(runs,measurements,crops)};training.append(row);print('fit',name,row['fit']['water_rmse_m3_m3'],row['fit']['yield_mae_kg_dry_ha'],flush=True)
    learned_trace=[]
    def conductivity_objective(logscale):
        options={**hydraulic,'conductivity_scale':float(np.exp(logscale))};runs=[trace(c,prior,p,options)for c in train]
        score=metrics(runs,measurements);loss=(score['water_rmse_m3_m3']/.05)**2+.02*logscale**2
        learned_trace.append({'conductivity_scale':float(np.exp(logscale)),'loss':loss,'fit_water_rmse':score['water_rmse_m3_m3']});return loss
    fit=minimize_scalar(conductivity_objective,bounds=(np.log(.1),np.log(10)),method='bounded',options={'maxiter':9,'xatol':.05})
    learned_scale=float(np.exp(fit.x));candidates.append(('regularized_conductivity',p,{**hydraulic,'conductivity_scale':learned_scale}))
    lookup={c['case_id']:c['aboveground_biomass_dry_kg_ha']for c in crops};rue_trace=[]
    def biomass_objective(rue):
        runs=[trace(c,prior,{**p,'rue':float(rue)},{})for c in train]
        errors=[(r['harvest_diagnostics'][0]['biomass_dry_kg_ha']*.8-lookup[r['id']])/lookup[r['id']]for r in runs]
        loss=float(np.mean(np.square(errors))+.02*np.log(rue/p['rue'])**2);rue_trace.append({'rue':float(rue),'loss':loss});return loss
    fit_rue=minimize_scalar(biomass_objective,bounds=(2.5,5.),method='bounded',options={'maxiter':10,'xatol':.03})
    candidates.append(('biomass_calibrated_rue',{**p,'rue':float(fit_rue.x)},{}))
    frozen={'candidates':[{'name':name,'parameters':parameters,'options':options}for name,parameters,options in candidates],
        'conductivity_fit':learned_trace,'conductivity_optimizer_success':bool(fit.success),'biomass_fit':rue_trace,'biomass_optimizer_success':bool(fit_rue.success)}
    write_json(out/'fitting_frozen_before_validation.json',frozen)
    val_crops,_=crop_observations(root,[2010]);outcomes={**measurements,2010:labels(root,2010)};leader=[]
    for name,parameters,options in candidates:
        runs=[trace(c,prior,parameters,options)for c in validation]
        fitrow=next((r['fit']for r in training if r['name']==name),None)
        if fitrow is None:fitrow=enriched([trace(c,prior,parameters,options)for c in train],measurements,crops)
        row={'name':name,'fit':fitrow,'development':enriched(runs,outcomes,val_crops)};leader.append(row)
        write_json(out/(name+'_predictions.json'),runs)
        print('development',name,row['development']['water_rmse_m3_m3'],row['development']['yield_mae_kg_dry_ha'],flush=True)
    reference=leader[0]['development']
    for row in leader:
        v=row['development'];row['passes_joint_gate']=(v['water_rmse_m3_m3']<=reference['water_rmse_m3_m3']*.95 and v['yield_mae_kg_dry_ha']<=reference['yield_mae_kg_dry_ha']*1.05 and (v['canopy_cover_rmse']is None or v['canopy_cover_rmse']<=reference['canopy_cover_rmse']*1.05))
        row['beats_persistence']=v['water_rmse_m3_m3']<v['initial_profile_persistence_rmse_m3_m3']
    report={'status':'fixed_split_physical_and_flux_experiments_completed','protocol':protocol,'leaderboard':leader,
        'learned_components':{'conductivity_scale':learned_scale,'biomass_calibrated_rue':float(fit_rue.x),'targets':'existing observed profiles and measured aboveground biomass, never synthetic simulation labels','identifiability':'A one-parameter conditional inverse fit; conductivity is not directly measured and may compensate other structural errors.'},
        'retained_candidates':[r['name']for r in leader if r['passes_joint_gate']],'default_equations_changed':False,'recommendation':None,'new_final_test_accuracy_established':False}
    write_json(out/'report.json',report);return report
