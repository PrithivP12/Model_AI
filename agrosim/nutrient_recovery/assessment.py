"""Same-support maize production and explicit transfer-path summaries."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from ..io import write_json


def assess(root,run,out):
    root=Path(root);run=Path(run);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    measured=json.loads((root/'reports/rotation-recovery/reproduced-final/validated-observations/observed_tissue_and_grain.json').read_text())['rows']
    records=json.loads((root/'reports/rotation-recovery/reproduced-final/input-audit/records.json').read_text())
    rows=[];flux=[];summary={}
    for path in sorted((run/'rotation').glob('*_memory.json')):
        model=path.name.removesuffix('_memory.json');memory=json.loads(path.read_text());grouped={}
        for m in memory:
            rec=records[m['record_id']]
            flux.append({'model':model,'record_id':m['record_id'],'initial_N':m['initial_N'],
                **{key:m['fluxes'].get(key,0.)for key in ['mineralized_n_kg_ha','immobilized_n_kg_ha','plant_uptake_n_kg_ha','harvested_n_kg_ha','leaf_litter_N_kg_ha','maturity_internal_N_to_grain_kg_ha']},
                'analysis_N_increment':m['analysis_increments']['nitrogen'],
                'Jan1_mineral_N':sum(m['Jan1_state']['mineral_n_kg_ha']),
                'Jan1_residue_C':sum(m['Jan1_state']['residue_c_kg_ha'])+m['Jan1_state']['surface_residue_c_kg_ha'],
                'Jan1_residue_N':sum(m['Jan1_state']['residue_n_kg_ha'])+m['Jan1_state']['surface_residue_n_kg_ha']})
            for h in m['harvests']:
                if h['crop']!='corn':continue
                key=(rec['site_id'],rec['plot_id'],int(h['planted'][:4]));grouped.setdefault(key,[]).append(h)
        for (site,plot,y),hs in grouped.items():
            obs=measured.get(f'{site}:{plot}:{y}',{})
            if any(obs.get(k)is None for k in ['AGR09','AGR24','AGR10','AGR26','AGR25','AGR33']):continue
            C=obs['AGR09']+obs['AGR24'];N=obs['AGR10']+obs['AGR26']
            if min(C,N,obs['AGR33'],obs['AGR25'])<=0:continue
            predictions=[]
            for h in hs:
                pools=h['organ_state']['pools'];p=[v for k,v in pools.items()if k not in ['grain','root']]
                predictions.append({'production_C':sum(x['dry']for x in p)*.45,'production_N':sum(x['N']for x in p),
                    'grain_C':pools['grain']['dry']*.45,'grain_N':pools['grain']['N'],
                    'returned_shoot_C':h['shoot_residue_c_added_kg_ha'],'returned_shoot_N':h['shoot_residue_n_added_kg_ha'],
                    'grain_dry':h['grain_dry_kg_ha'],'root_N':pools['root']['N']})
            q={key:float(np.mean([p[key]for p in predictions]))for key in predictions[0]}
            rows.append({'model':model,'site':site,'plot':plot,'year':y,**q,'observed_production_C':C,'observed_production_N':N,
                'observed_CN':C/N,'predicted_CN':q['production_C']/q['production_N'],
                'observed_NHI':obs['AGR25']/(obs['AGR25']+N),'predicted_NHI':q['grain_N']/(q['grain_N']+q['production_N']),
                'observed_grain_N':obs['AGR25'],'source_row':obs['source_row'],'actual_residue_return_measured':False})
    frame=pd.DataFrame(rows);fl=pd.DataFrame(flux)
    for name,g in frame.groupby('model'):
        summary[name]={'unique_maize_plot_years':len(g),'production_C_MAE':float(abs(g.production_C-g.observed_production_C).mean()),
            'production_N_MAE':float(abs(g.production_N-g.observed_production_N).mean()),
            'grain_N_MAE':float(abs(g.grain_N-g.observed_grain_N).mean()),'CN_median':float(g.predicted_CN.median()),
            'observed_CN_median':float(g.observed_CN.median()),'NHI_median':float(g.predicted_NHI.median()),
            'observed_NHI_median':float(g.observed_NHI.median())}
    keyset=lambda name:set(map(tuple,frame[frame.model.eq(name)][['site','plot','year']].to_numpy()))
    if any(keyset(name)!=keyset('material_bridge')for name in frame.model.unique()):raise AssertionError('Crop-N support cohort changed across variants')
    frame.to_csv(out/'same_support_maize_CN.csv',index=False);fl.to_csv(out/'record_N_cycles.csv',index=False)
    # Model variants can change many connected fluxes. These differences are
    # exact output changes, not additive causal allocations or field flux truth.
    cycles={name:{k:float(g[k].mean())for k in fl if k not in ['model','record_id','initial_N']}for name,g in fl.groupby('model')}
    report={'status':'same_support_CN_and_carryover_assessed','production_quality':summary,'mean_record_cycles':cycles,
        'observation_composition_variants_are_retrospective':True,'new_independent_accuracy':None,
        'uncertainty':'Scenario differences and observational analysis increments, not calibrated probability intervals or verified residue return.',
        'no_alternative_decomposition_fit':'Existing data do not identify turnover versus input composition; stable decomposition unchanged.'}
    write_json(out/'report.json',report);return report
