"""Computed, evidence-qualified strategy explanations on identical weather scenarios."""
from copy import deepcopy
import numpy as np
from ..biological_recovery.decision import compare_strategies

DIFFERENCES={
    'soil_evaporation_mm':'soil_evaporation_mm','crop_transpiration_mm':'crop_transpiration_mm',
    'actual_ET_mm':'actual_ET_mm','returned_shoot_C_kg_ha':'returned_shoot_C_kg_ha',
    'returned_shoot_N_kg_ha':'returned_shoot_N_kg_ha','returned_root_C_kg_ha':'returned_root_C_kg_ha',
    'returned_root_N_kg_ha':'returned_root_N_kg_ha','final_mineral_N_kg_ha':'final_mineral_N_kg_ha',
    'mineralized_N_kg_ha':'mineralized_N_kg_ha','immobilized_N_kg_ha':'immobilized_N_kg_ha',
    'leached_N_kg_ha':'leached_N_kg_ha','denitrified_N_kg_ha':'denitrified_N_kg_ha',
    'final_humus_C_kg_ha':'final_humus_C_kg_ha','final_residue_C_kg_ha':'final_residue_C_kg_ha',
    'mean_cover_proxy':'mean_cover_proxy','mean_crop_water_factor':'mean_crop_water_factor'}


def explain_difference(reference,alternative):
    if reference['scenario_id']!=alternative['scenario_id']:raise ValueError('Counterfactual comparison requires identical weather scenario')
    deltas={name:alternative[key]-reference[key]if alternative[key]is not None and reference[key]is not None else None for name,key in DIFFERENCES.items()}
    ra={x['planted']:x for x in reference['crop_productivity']};rb={x['planted']:x for x in alternative['crop_productivity']}
    if ra.keys()!=rb.keys():raise ValueError('Strategies lack equivalent dated crop slots')
    harvest=[]
    for slot in sorted(ra):
        a,b=ra[slot],rb[slot]
        harvest.append({'slot':slot,'reference_crop':a['crop'],'alternative_crop':b['crop'],
            'dry_yield_difference':b['grain_kg_dry_ha']-a['grain_kg_dry_ha']if a['crop']==b['crop']else None,
            'unlike_crop_yield_comparison':'not comparable as tonnes'if a['crop']!=b['crop']else None})
    price=None if reference['economics']is None or alternative['economics']is None else alternative['economics']['net_return']-reference['economics']['net_return']
    text=(f"Under the same {reference['scenario_id']} scenario, {alternative['strategy_id']} minus {reference['strategy_id']} "
          f"returns {deltas['returned_shoot_C_kg_ha']:+.2f} kg shoot C/ha and {deltas['returned_shoot_N_kg_ha']:+.2f} kg shoot N/ha; "
          f"modeled soil evaporation changes {deltas['soil_evaporation_mm']:+.2f} mm, mineralized N {deltas['mineralized_N_kg_ha']:+.2f} kg N/ha "
          f"and final mineral N {deltas['final_mineral_N_kg_ha']:+.2f} kg N/ha. "
          "These are jointly computed pathway differences, not proof that residue alone caused each change. Yield superiority is not established.")
    return {'reference':reference['strategy_id'],'alternative':alternative['strategy_id'],'scenario_id':reference['scenario_id'],
        'differences':deltas,'harvest_comparisons':harvest,'net_return_difference':price,'explanation':text,
        'evidence_roles':{'environment':'Cached NASA POWER gridded estimates, historical scenario replay; not measured future weather.',
            'soil_and_management':'Caller-declared measured/scenario provenance; unknown conditions are not observations.',
            'plant_and_soil_processes':'Literature-shaped parameters and conditional calibration; transfer failures documented.',
            'organism_effects':'Unsupported quantitative effects disabled.',
            'comparison':'Model-generated counterfactual, not a measured causal rotation effect.'},
        'confidence_probability':None,'local_validation_required':True}


def enrich_strategy_result(result):
    """Pure postprocessing: farmer priorities never alter trajectories or weather."""
    out=deepcopy(result);current=out['current_strategy'];runs=out['runs']
    scenarios={r['scenario_id']for r in runs if r['strategy_id']==current}
    for strategy in {r['strategy_id']for r in runs}:
        if {r['scenario_id']for r in runs if r['strategy_id']==strategy}!=scenarios:
            raise ValueError('All feasible strategies require the identical complete climate ensemble')
    comparisons=[]
    for scenario in sorted(scenarios):
        ref=next(r for r in runs if r['strategy_id']==current and r['scenario_id']==scenario)
        for r in runs:
            if r['scenario_id']==scenario and r['strategy_id']!=current:comparisons.append(explain_difference(ref,r))
    tradeoffs=[]
    for strategy in sorted({r['alternative']for r in comparisons}):
        rows=[r for r in comparisons if r['alternative']==strategy]
        ranges={key:{'minimum':float(min(r['differences'][key]for r in rows)),'maximum':float(max(r['differences'][key]for r in rows))}
                for key in DIFFERENCES if all(r['differences'][key]is not None for r in rows)}
        tradeoffs.append({'alternative':strategy,'relative_to':current,'scenario_count':len(rows),'computed_difference_ranges':ranges,
            'yield_superiority_established':False,'strategies_statistically_distinguishable':None,
            'reason':'Scenario ranges are sensitivities; no empirically calibrated prediction distribution or field-specific contrast interval.',
            'forced_winner':None})
    out.update(schema_version='ecosystem-evidence-v1',scenario_comparisons=comparisons,scenario_tradeoffs=tradeoffs,
        evidence_contract={'computed_metrics_are_observations':False,'NASA_input_role':'historical gridded forcing','future_weather_role':'scenario_only',
            'new_independent_validation':False,'recommendation_issued':False,
            'uncertainty':'Unknown future scenario probability; no confidence from counting scenarios.',
            'local_expert_review':['Verify soil, genotype, calendars, residue return and N/irrigation records.','Review physical water/N/yield transfer limitations before field decisions.']})
    return out


def compare_evidence(config,base):return enrich_strategy_result(compare_strategies(config,base))
