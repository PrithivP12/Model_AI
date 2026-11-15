"""Diagnostic state constraint from observed *prior* shoot production, not verified return.

The frozen scenario assumes all non-grain shoot residue retained. Replacing its
modeled production with measured C/N diagnoses state errors under that assumption.
All positive/negative state-estimation adjustments are explicit ledger terms.
"""
from pathlib import Path
from datetime import timedelta,date
from dataclasses import replace
import json
import numpy as np
import pandas as pd
from ..processes import SoilState,Processes,default_crops
from ..io import write_json
from .mechanisms import ReconstructionKernel
from .inputs import prepare_record
from .experiment import pair_predictions,scores


def constrained_run(weather,state,plants,events,kernel,observed,site,plot,target_year,apply_observations=True):
    initial=kernel.inventories(state,None);totals={};adjustment={'carbon':0.,'nitrogen':0.,'water':0.};harvests=[];audit=[];offset=0
    for planting in plants:
        stop=next(i for i,w in enumerate(weather)if w.date==planting.harvest_deadline)
        result=kernel.run(weather[offset:stop+1],state,[planting],[e for e in events if weather[offset].date<=e.date<=weather[stop].date],keep_daily=False)
        state=SoilState(**result['final_state']);offset=stop+1;harvests.extend(result['harvests'])
        for key,value in result['totals'].items():totals[key]=totals.get(key,0)+value
        if not apply_observations or planting.date.year>=target_year:continue
        if str(planting.date)not in kernel.options.get('observed_harvest_sowing_dates',[]):continue
        key=f'{site}:{plot}:{planting.date.year}';obs=observed.get(key,{})
        crop=planting.crop
        if crop=='soybeans':values=[obs.get('AGR11'),obs.get('AGR12')]
        else:
            values=[None if obs.get('AGR09')is None or obs.get('AGR24')is None else obs['AGR09']+obs['AGR24'],
                    None if obs.get('AGR10')is None or obs.get('AGR26')is None else obs['AGR10']+obs['AGR26']]
        if any(v is None or v<0 for v in values):continue
        h=result['harvests'][-1];dc=values[0]-h['shoot_residue_c_added_kg_ha'];dn=values[1]-h['shoot_residue_n_added_kg_ha']
        if state.surface_residue_c_kg_ha+dc< -1e-7 or state.surface_residue_n_kg_ha+dn< -1e-7:raise ArithmeticError('Cannot replace production already decomposed before observed harvest boundary')
        state.surface_residue_c_kg_ha+=dc;state.surface_residue_n_kg_ha+=dn;adjustment['carbon']+=dc;adjustment['nitrogen']+=dn
        audit.append({'crop_year':planting.date.year,'source_row':obs.get('source_row'),'applied_date':str(planting.harvest_deadline),
            'C_state_adjustment_kg_ha':dc,'N_state_adjustment_kg_ha':dn,'observation':'measured standing aboveground non-grain production, not a residue-return observation',
            'retention_fraction':1.,'retention_status':'Frozen hypothetical all-retained scenario; not verified',
            'not_target_outcome':planting.date.year<target_year,'forecast_eligible':False})
    if offset<len(weather):
        result=kernel.run(weather[offset:],state,management=[e for e in events if e.date>=weather[offset].date],keep_daily=False);state=SoilState(**result['final_state'])
        for key,value in result['totals'].items():totals[key]=totals.get(key,0)+value
    final=kernel.inventories(state,None);ledger={k:final[k]-initial[k]-totals.get(k+'_inputs',0)+totals.get(k+'_outputs',0)-adjustment[k]for k in initial}
    if max(abs(v)for v in ledger.values())>1e-6:raise ArithmeticError('Measurement-constrained stock ledger failed')
    return {'harvests':harvests,'adjustments':audit,'signed_state_adjustments':adjustment,'maximum_balance_error':max(abs(v)for v in ledger.values())}


def run_assimilation(root,output,weather_dir=None,audit_dir=None,observation_path=None,mechanism_path=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed observation-constrained diagnosis')
    audit_dir=Path(audit_dir or root/'reports/rotation-recovery/input-audit');weather_dir=Path(weather_dir or root/'reports/rotation-recovery/weather-exact');observation_path=Path(observation_path or root/'reports/rotation-recovery/prior-crop-diagnosis/observed_tissue_and_grain.json')
    records=json.loads((audit_dir/'records.json').read_text());soils=json.loads((audit_dir/'soil_observations.json').read_text());observed=json.loads(observation_path.read_text())['rows'];old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    fixed=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv');ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    cal=json.loads((root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())['candidates'][1]['parameters'];predictions={};control={};audits=[];max_error=0
    write_json(out/'protocol_frozen.json',{'same_106_pairs':True,'role':'Retrospective diagnostic with measured prior shoot-production constraints and explicit state adjustments; not a free prediction model.',
        'E_verified_carryover':False,'all_retained_assumption':'Retain the frozen prior scenario, do not represent it as observed residue return.',
        'target_year_outcomes_never_assimilated':True,'ledger':'State estimation C/N additions/removals are separate from biological external fluxes, and included explicitly in whole-run reconciliation.'})
    for i,rid in enumerate(ids):
        rec=records[rid];outputs=[];controls=[]
        for n,pulsed in [(40,True),(120,False)]:
            weather,layers,state,plants,events,evidence=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],weather_dir,'C_soil',n,pulsed)
            crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=cal['harvest_index'],grain_n_fraction=cal['grain_n_fraction'])
            kernel=ReconstructionKernel(layers,crops,Processes(maize_n_dilution_a=.034),rec['latitude'],options={'root_beta_per_m':4,'observed_harvest_sowing_dates':[str(p.date)for p,s in zip(plants,rec['timeline'])if s['harvest_date']]})
            for apply in [False,True]:
                result=constrained_run(weather,state,plants,events,kernel,observed,rec['site_id'],rec['plot_id'],rec['year'],apply)
                y=next(h['grain_dry_kg_ha']for h in result['harvests']if h['planted'].startswith(str(rec['year'])))
                (outputs if apply else controls).append(y);max_error=max(max_error,result['maximum_balance_error'])
                if apply:audits.extend({'record_id':rid,'scenario_initial_N':n,**a}for a in result['adjustments'])
        predictions[rid]={'prediction':float(np.mean(outputs))};control[rid]={'prediction':float(np.mean(controls))}
        if i%60==0:print('residue state diagnostic',i,flush=True)
    resultframe=fixed.copy();resultframe['E_prior_tissue_constrained']=pair_predictions(fixed,predictions);resultframe['E_segmented_no_adjustments']=pair_predictions(fixed,control)
    resultframe.to_csv(out/'paired_predictions.csv',index=False);write_json(out/'state_adjustments.json',audits)
    write_json(out/'record_predictions.json',{'E_prior_tissue_constrained':predictions,'E_segmented_no_adjustments':control})
    baseline=pd.read_csv(Path(mechanism_path or root/'reports/rotation-recovery/mechanisms/all_candidate_predictions.csv')).set_index('pair_id')
    parity=float(np.max(abs(resultframe.E_segmented_no_adjustments.to_numpy()-baseline.loc[resultframe.pair_id,'D_harvest_timing'].to_numpy())))
    report={'status':'prior_production_state_constraint_diagnostic_completed','models':{name:scores(resultframe,name)for name in ['E_prior_tissue_constrained','E_segmented_no_adjustments']},
        'records_with_any_constraint':len({a['record_id']for a in audits}),'target_records':len(ids),'maximum_ledger_error_including_state_adjustments':max_error,
        'segmented_control_max_difference_vs_full_run':parity,'verified_carryover':False,'preplant_eligible':False,
        'interpretation':'Any improvement only diagnoses sensitivity to measured past tissue production under a retention assumption. It does not validate decomposition, fixation, root inputs or causal rotation benefits.',
        'recommendation':None}
    write_json(out/'report.json',report);return report
