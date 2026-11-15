"""Observed crop traits and observer-volume checks, without new parameter tuning."""
from pathlib import Path
import json
import numpy as np
from ..data.lirf import old_cases
from ..io import write_json
from .observations import crop_observations,emergence_requirement


def diagnose_crop(root,output,process_path=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed crop diagnosis')
    source=Path(process_path or root/'reports/scientific-intelligence/process');obs,stages=crop_observations(root,[2008,2009]);raw=json.loads((source/'training_crop_observations.json').read_text())
    # Replay no extra fit: training case predictions already stored in earlier recovery.
    predictions=json.loads((root/'reports/accuracy-recovery/reproduced-release/diagnostics/report.json').read_text())
    p=predictions['reference_parameters'];years=[2008,2009]
    from .process_experiment import trace
    from ..data.lirf import soil_prior
    from ..recovery.process import training_field_capacity
    prior=training_field_capacity(root,soil_prior(root,True));p={**p,'root_depth_m':1.05};lookup={r['case_id']:r for r in obs};rows=[]
    for y in years:
        for case in old_cases(root,y):
            run=trace(case,prior,p,{});h=run['harvest_diagnostics'][0]
            total_n=25*.018+h['soil_uptake_n_kg_ha']+h['fixed_n_kg_ha']+run['initial_assimilation'].get('plant_n_adjustment_kg_ha',0)
            residue_n=max(0,total_n-h['grain_n_kg_ha']);residue_c=(h['biomass_dry_kg_ha']-h['grain_dry_kg_ha'])*.45
            rows.append({'case_id':case['id'],'modeled_combined_root_shoot_residue_CN':residue_c/residue_n if residue_n>0 else None,
                'observed_stover_CN':lookup[case['id']]['stover_cn_ratio'],'observed_grain_N_fraction':lookup[case['id']]['grain_n_fraction'],
                'modeled_grain_N_fraction':.014,'modeled_aboveground_biomass':h['biomass_dry_kg_ha']*.8,
                'observed_aboveground_biomass':lookup[case['id']]['aboveground_biomass_dry_kg_ha'],
                'observed_biomass_sample_HI':lookup[case['id']]['biomass_sample_harvest_index']})
    development=old_cases(root,2010);_,stage2010=crop_observations(root,[2010]);emergence=raw['emergence_gdd'];stage_errors=[]
    for stage in stage2010:
        if stage['stage']!='emergence':continue
        case=development[0];gdd=0.;predicted=None
        # Growth gate tests pre-day thermal accumulation against threshold.
        for d in case['weather']:
            if gdd>=emergence:predicted=d['date'];break
            gdd+=max(0,(min(max(d['tmin_C'],8),40)+min(max(d['tmax_C'],8),40))/2-8)
        from datetime import date
        stage_errors.append({'stage':stage,'observed_emergence':stage['date'],'candidate_emergence':predicted,
            'baseline_implied_emergence':case['planting_date'],'candidate_error_days':(date.fromisoformat(predicted)-date.fromisoformat(stage['date'])).days,
            'baseline_error_days':(date.fromisoformat(case['planting_date'])-date.fromisoformat(stage['date'])).days})
    report={'status':'observed_crop_trait_diagnostics_completed','training_comparisons':rows,'development_emergence_scoring':stage_errors,
        'mean_observed_stover_CN':float(np.mean([r['observed_stover_CN']for r in rows])),
        'mean_modeled_combined_residue_CN':float(np.mean([r['modeled_combined_root_shoot_residue_CN']for r in rows])),
        'observer_volume_limit':'Observed stalk/stover excludes roots and cobs; modeled single residue pool mixes roots and shoots and assigns homogeneous N concentration. Exact equality is not expected. This cannot be used as a direct parameter fit.',
        'harvest_index_sampling_limit':'Biomass-sample HI and plot grain yield use different samples; HI is not reconstructed by dividing the separately sampled plot yield by biomass.',
        'interpretation':'Mature tissue N/partitioning and observer support remain important uncertainties for carryover. No crop-family credit or residue-N stock multiplier is introduced.',
        'parameter_changes':False,'recommendation':None}
    write_json(out/'report.json',report);return report
