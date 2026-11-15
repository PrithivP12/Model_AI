"""Sensitivity-informed, one-parameter calibration with a fixed validation gate."""
from pathlib import Path
import json
import numpy as np
from ..io import write_json,sha256
from ..data.lirf import old_cases,labels,soil_prior
from ..corrected_experiment import yield_metrics
from .process import trace_case,metrics,training_field_capacity


def run_yield_recovery(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed growth diagnosis')
    source=root/'reports/completion-pass/quality-corrected/depth-refinement/selected.json'
    p=json.loads(source.read_text())['parameters'];p['root_depth_m']=1.05
    prior=training_field_capacity(root,soil_prior(root,True))
    train=[c for y in [2008,2009]for c in old_cases(root,y)];val=old_cases(root,2010)
    observed={y:labels(root,y)for y in [2008,2009,2010]}
    protocol={'fit_years':[2008,2009],'validation_year':2010,'test_observations_loaded':False,
        'parameters_sha256':sha256(source),'water_configuration':'training-year field capacity and 105 cm root zone, previously selected on 2010 water RMSE',
        'hypothesis':'Radiation-use efficiency is a major local yield sensitivity; increasing it may reduce training underprediction but may fail temporal transfer.',
        'parameters_varied':'RUE only, 0.8/1.0/1.2 times frozen value; no simultaneous HI refit because RUE/HI cannot be separately identified from these yields alone',
        'fit_selection':'lowest training yield MAE','retention_gate':'strictly improve 2010 yield MAE without degrading water RMSE over reference',
        'prediction_horizon':'post-initialization historical reconstruction, not a preplant forecast',
        'independent_final_validation':False}
    write_json(out/'protocol_frozen.json',protocol);fits=[]
    for scale in [.8,1.,1.2]:
        parameters={**p,'rue':p['rue']*scale};runs=[trace_case(c,prior,parameters)for c in train]
        fits.append({'rue_multiplier':scale,'parameters':parameters,'fit':metrics(runs,observed)})
        write_json(out/f'fit_rue_{scale}.json',runs)
    chosen=min(fits,key=lambda r:r['fit']['yield_mae_kg_dry_ha']);write_json(out/'training_selection_frozen.json',chosen)
    comparisons=[]
    for row in fits:
        runs=[trace_case(c,prior,row['parameters'])for c in val]
        comparisons.append({**row,'validation':metrics(runs,observed)})
        write_json(out/f'validation_rue_{row["rue_multiplier"]}.json',runs)
    reference=next(r for r in comparisons if r['rue_multiplier']==1.)
    candidate=next(r for r in comparisons if r['rue_multiplier']==chosen['rue_multiplier'])
    accept=(candidate['validation']['yield_mae_kg_dry_ha']<reference['validation']['yield_mae_kg_dry_ha']and
        candidate['validation']['water_rmse_m3_m3']<=reference['validation']['water_rmse_m3_m3'])
    trainmean=float(np.mean([observed[c['year']][c['treatment']]for c in train]));vy=[observed[c['year']][c['treatment']]for c in val]
    # Non-fitted diagnostics isolate what mechanisms are wrong, not their causal attribution.
    diagnostics={}
    for name,cases in [('fit',train),('validation',val)]:
        runs=[trace_case(c,prior,p)for c in cases]
        canopy_errors=[v for r in runs for v in r['canopy_squared_errors']]
        diagnostics[name]={'mean_n_growth_factor':float(np.mean([r['mean_n_growth_factor']for r in runs])),
            'mean_water_stress_factor':float(np.mean([r['mean_water_stress_factor']for r in runs])),
            'subsequent_canopy_rmse':float(np.sqrt(np.mean(canopy_errors)))if canopy_errors else None,
            'canopy_scored_observations':len(canopy_errors),
            'harvest_reasons':[h['reason']for r in runs for h in r['harvest_diagnostics']],
            'mean_growing_days':float(np.mean([h['growing_days']for r in runs for h in r['harvest_diagnostics']])),
            'mean_soil_n_uptake_kg_ha':float(np.mean([h['soil_uptake_n_kg_ha']for r in runs for h in r['harvest_diagnostics']]))}
    report={'status':'sensitivity_informed_growth_gate_evaluated','protocol':protocol,'comparisons':comparisons,
        'training_selected_rue_multiplier':chosen['rue_multiplier'],'retained':accept,'production_parameters_changed':False,
        'default_after_gate':'existing parameterization; optional candidate remains experimental even if gate passes',
        'train_mean_baseline_validation':yield_metrics(vy,np.full(len(val),trainmean)),'mechanism_diagnostics':diagnostics,
        'uncertainty':'Six treatment means share one validation site-year; treatment-level resampling would not establish temporal or geographic generalization. No generalization CI is defensible here.',
        'limits':['Observed canopy only initializes once; simulated subsequent canopy can reveal phenology/LAI mismatch.',
          'End-season dry grain does not separately identify RUE, HI, root allocation, vegetative N demand and reproductive N transfer.',
          'Calendar forcing, initial N and deep organic pools remain assumptions; changing them based on exposed-year errors is prohibited.',
          'No flowering-date, root-uptake or daily assimilation truth is available for uniquely isolating those mechanisms.'],
        'recommendation':None}
    write_json(out/'report.json',report);return report
