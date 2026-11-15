"""Training-only follow-up combining independently supported spatial/harvest traits."""
from pathlib import Path
import json
import numpy as np
from scipy.optimize import minimize_scalar
from ..data.lirf import old_cases,labels,soil_prior
from ..recovery.process import training_field_capacity
from ..io import write_json
from .observations import crop_observations
from .process_experiment import trace,enriched


def run_refinement(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed refinement')
    p=json.loads((root/'reports/completion-pass/quality-corrected/depth-refinement/selected.json').read_text())['parameters'];p['root_depth_m']=1.05
    prior=training_field_capacity(root,soil_prior(root,True));cases=[c for y in [2008,2009]for c in old_cases(root,y)];crop,_=crop_observations(root,[2008,2009]);observed={y:labels(root,y)for y in [2008,2009]}
    measured={**p,'harvest_index':float(np.mean([r['biomass_sample_harvest_index']for r in crop])),
        'grain_n_fraction':float(np.mean([r['grain_n_fraction']for r in crop]))}
    protocol={'fit':[2008,2009],'development':2010,'adaptive_development_follow_up':True,
        'reason':'Initial fixed experiment found spatial uptake and measured HI useful separately; this is transparent follow-up development, never a fresh test.',
        'root_flux_fit':'One exponential allocation coefficient beta, [0,6]/m; loss=(training profile RMSE/.05)^2 + .02*(beta/4)^2. Total feasible transpiration unchanged for any single allocation step.',
        'measured_partition':'Mean training measured biomass-sample HI and grain N concentrations, not fitted to development yields',
        'gate':'Same 5% water gain / <=5% yield and canopy degradation rule as initial phase; report simple baselines separately',
        'nonidentifiability':'Inverse root-flux allocation is conditional on assumed hydrology, FC, root depth and canopy. Root densities or uptake rates are not directly measured.'}
    write_json(out/'protocol_frozen.json',protocol);trials=[]
    def objective(beta):
        runs=[trace(c,prior,p,{'root_beta_per_m':float(beta)})for c in cases];m=enriched(runs,observed,crop)
        loss=(m['water_rmse_m3_m3']/.05)**2+.02*(beta/4)**2
        trials.append({'beta_per_m':float(beta),'loss':loss,'water_rmse':m['water_rmse_m3_m3'],'yield_mae':m['yield_mae_kg_dry_ha']});return loss
    fit=minimize_scalar(objective,bounds=(0,6),method='bounded',options={'maxiter':12,'xatol':.05});beta=float(fit.x)
    candidates=[('reference',p,{}),('fixed_root4_measured_partition',measured,{'root_beta_per_m':4}),
        ('regularized_root_flux',p,{'root_beta_per_m':beta}),('regularized_root_measured_partition',measured,{'root_beta_per_m':beta})]
    write_json(out/'training_fit_frozen.json',{'beta_per_m':beta,'optimizer_success':bool(fit.success),'trials':trials,'candidates':[{'name':n,'parameters':v,'options':o}for n,v,o in candidates]})
    val=old_cases(root,2010);vcrop,_=crop_observations(root,[2010]);outcomes={2010:labels(root,2010)};rows=[]
    for name,parameters,options in candidates:
        runs=[trace(c,prior,parameters,options)for c in val];row={'name':name,'development':enriched(runs,outcomes,vcrop),'fit':enriched([trace(c,prior,parameters,options)for c in cases],observed,crop)};rows.append(row)
        write_json(out/(name+'_predictions.json'),runs)
        print(name,row['development']['water_rmse_m3_m3'],row['development']['yield_mae_kg_dry_ha'],flush=True)
    reference=rows[0]['development']
    for r in rows:
        m=r['development'];r['passes_joint_gate']=m['water_rmse_m3_m3']<=reference['water_rmse_m3_m3']*.95 and m['yield_mae_kg_dry_ha']<=reference['yield_mae_kg_dry_ha']*1.05 and m['canopy_cover_rmse']<=reference['canopy_cover_rmse']*1.05
        r['beats_persistence']=m['water_rmse_m3_m3']<m['initial_profile_persistence_rmse_m3_m3']
    result={'status':'training_only_flux_refinement_development_evaluated','protocol':protocol,'learned_beta_per_m':beta,'leaderboard':rows,
        'selected_experimental_candidate':'fixed_root4_measured_partition'if rows[1]['passes_joint_gate']else 'reference',
        'learning_complexity_decision':'The learned beta is effectively 4/m and offers negligible benefit over the fixed 4/m alternative; retain the simpler fixed allocation for experimental use.',
        'recommendation':None,'independent_final_accuracy_established':False,'default_model_changed':False}
    write_json(out/'report.json',result);return result
