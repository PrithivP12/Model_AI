"""Observed production quality versus modeled production; never residue sensing."""
from pathlib import Path
from collections import defaultdict
import json
import numpy as np
import pandas as pd
from ..processes import Processes
from ..io import write_json


def stoichiometric_net_N_per_C(cn,humification_fraction=.25,humus_cn=12.):
    if not np.isfinite(cn)or cn<=0:raise ValueError('Positive finite residue C:N required')
    return 1/cn-humification_fraction/humus_cn


def quality_audit(root,out,memory=None,model_identifier='source_sink'):
    root=Path(root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    observations=json.loads((root/'reports/rotation-recovery/reproduced-final/validated-observations/observed_tissue_and_grain.json').read_text())['rows']
    records=json.loads((root/'reports/rotation-recovery/reproduced-final/input-audit/records.json').read_text())
    if memory is None:memory=json.loads((root/'reports/reproductive-recovery/final/rotation/source_sink_memory.json').read_text())
    models=defaultdict(list)
    for row in memory:
        rec=records[row['record_id']]
        for h in row['harvests']:
            key=(rec['site_id'],rec['plot_id'],int(h['planted'][:4]),h['crop'])
            models[key].append({'C':h['shoot_residue_c_added_kg_ha'],'N':h['shoot_residue_n_added_kg_ha'],
                'grain_N':h['grain_n_kg_ha'],'grain':h['grain_dry_kg_ha'],'fixed_N':h['fixed_n_kg_ha']})
    rows=[];p=Processes();threshold=p.humus_cn/p.humification_fraction
    for (site,plot,y,crop),group in sorted(models.items()):
        obs=observations.get(f'{site}:{plot}:{y}',{})
        fields=['AGR09','AGR24','AGR10','AGR26']if crop=='corn'else['AGR11','AGR12']
        if any(obs.get(f)is None or obs[f]<0 for f in fields):continue
        C=obs['AGR09']+obs['AGR24']if crop=='corn'else obs['AGR11']
        N=obs['AGR10']+obs['AGR26']if crop=='corn'else obs['AGR12']
        predicted={k:float(np.mean([g[k]for g in group]))for k in group[0]}
        if C<=0 or N<=0 or predicted['C']<=0 or predicted['N']<=0:continue
        observed_CN=C/N;model_CN=predicted['C']/predicted['N']
        grain_N=obs.get('AGR25'if crop=='corn'else'AGR28')
        grain_dry=obs.get('AGR33'if crop=='corn'else'AGR34')
        rows.append({'site':site,'plot':plot,'year':y,'crop':crop,'source_row':obs['source_row'],
            'observed_production_C':C,'observed_production_N':N,'observed_CN':observed_CN,
            'predicted_production_C':predicted['C'],'predicted_production_N':predicted['N'],'predicted_CN':model_CN,
            'model_net_N_per_1000C_potential':1000*stoichiometric_net_N_per_C(model_CN),
            'observed_quality_net_N_per_1000C_potential':1000*stoichiometric_net_N_per_C(observed_CN),
            'opposite_stoichiometric_sign':(model_CN<threshold)!=(observed_CN<threshold),
            'partial_fixed_minus_grain_N':predicted['fixed_N']-predicted['grain_N'],
            'model_aboveground_N_harvest_index':predicted['grain_N']/(predicted['grain_N']+predicted['N']),
            'observed_aboveground_N_harvest_index':None if grain_N is None or grain_N<0 else grain_N/(grain_N+N),
            'observed_grain_N_fraction':None if grain_N is None or grain_dry is None or grain_dry<=0 else grain_N/grain_dry,
            'actual_return_fraction_measured':False,'grain_N_fraction_model':predicted['grain_N']/predicted['grain']if predicted['grain']else None,
            'support':'Maize nongrain R6: vegetative+cob; some source C/cob values use standards.'if crop=='corn'else'Soybean R8 after leaf drop versus modeled shoot including retained leaves: non-equivalent; descriptive only.'})
    frame=pd.DataFrame(rows);frame.to_csv(out/'production_quality.csv',index=False)
    summary={}
    for crop,g in frame.groupby('crop'):
        summary[crop]={'matched_unique_plot_years':len(g),'observed_CN_median':float(g.observed_CN.median()),
            'predicted_CN_median':float(g.predicted_CN.median()),'opposite_stoichiometric_sign_fraction':float(g.opposite_stoichiometric_sign.mean()),
            'C_MAE':float(abs(g.predicted_production_C-g.observed_production_C).mean()),
            'N_MAE':float(abs(g.predicted_production_N-g.observed_production_N).mean()),
            'mean_fixed_minus_grain_N':float(g.partial_fixed_minus_grain_N.mean()),
            'model_NHI_median':float(g.model_aboveground_N_harvest_index.median()),
            'observed_NHI_median':float(g.observed_aboveground_N_harvest_index.median())if g.observed_aboveground_N_harvest_index.notna().any()else None,
            'observed_grain_N_fraction_median':float(g.observed_grain_N_fraction.median())if g.observed_grain_N_fraction.notna().any()else None,
            'valid_as_actual_returned_residue_check':False,
            'directly_comparable_nongrain_production_support':crop=='corn'}
    report={'status':'crop_production_stoichiometry_audited','model_identifier':model_identifier,'models':summary,'model_humification_threshold_CN':threshold,
        'equation':'Net residue N transfer per decomposed C =1/residue_CN − humification_fraction/humus_CN; kinetics, moisture, N limitation and stocks still constrain actual flux.',
        'threshold_is_empirically_validated':False,'new_independent_accuracy':None,
        'scope':'Candidate production quality is compared with source maturity samples. Actual residue return, timing and roots remain unmeasured. This diagnoses a represented pathway, not a fitted rotation penalty or a verified field N flux.'}
    write_json(out/'report.json',report);return report
