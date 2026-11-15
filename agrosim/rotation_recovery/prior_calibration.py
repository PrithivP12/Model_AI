"""Prior-crop calibration from real grain and tissue N; no rotation label bonus."""
from pathlib import Path
from collections import defaultdict
import json
import numpy as np
import pandas as pd
from ..io import write_json
from .experiment import simulate,pair_predictions,scores


def prior_scores(predictions,records,observations,farms,crop):
    unique={}
    for rid,prediction in predictions.items():
        rec=records[rid]
        if rec['farm_id']not in farms:continue
        for result in prediction['scenarios']:
            for h in result['harvests']:
                year=int(h['planted'][:4])
                if year>=rec['year']or h['crop']!=crop:continue
                key=f"{rec['site_id']}:{rec['plot_id']}:{year}";obs=observations.get(key,{})
                grain=obs.get('AGR19'if crop=='soybeans'else 'AGR17')
                if grain is None:continue
                seed_n=70*.035 if crop=='soybeans'else 25*.018
                # The caller's whole-plant N fraction determines soybean seed N too.
                seed_n=prediction.get('soy_seed_n',seed_n)if crop=='soybeans'else seed_n
                total_n=seed_n+h['soil_uptake_n_kg_ha']+h['fixed_n_kg_ha'];left_n=max(0,total_n-h['grain_n_kg_ha'])
                remainder=h['biomass_dry_kg_ha']-h['grain_dry_kg_ha'];rootmass=h['biomass_dry_kg_ha']*.2;shoot=max(0,remainder-rootmass)
                predicted_n=left_n*shoot/remainder if remainder>0 else 0.;predicted_c=shoot*.45
                obs_n=obs.get('AGR12'if crop=='soybeans'else 'AGR10');obs_c=obs.get('AGR11'if crop=='soybeans'else 'AGR09')
                if crop=='corn':
                    obs_n=None if obs_n is None or obs.get('AGR26')is None else obs_n+obs['AGR26']
                    obs_c=None if obs_c is None or obs.get('AGR24')is None else obs_c+obs['AGR24']
                if key not in unique:unique[key]={'farm_id':rec['farm_id'],'observed_grain':grain*(.87 if crop=='soybeans'else .845),'observed_N':obs_n,'observed_C':obs_c,'predicted_grain':[],'predicted_N':[],'predicted_C':[]}
                unique[key]['predicted_grain'].append(h['grain_dry_kg_ha']);unique[key]['predicted_N'].append(predicted_n);unique[key]['predicted_C'].append(predicted_c)
    per_farm=defaultdict(lambda:defaultdict(list));rows=[]
    for key,row in unique.items():
        pg=float(np.mean(row['predicted_grain']));pn=float(np.mean(row['predicted_N']));pc=float(np.mean(row['predicted_C']))
        per_farm[row['farm_id']]['grain'].append(abs(pg-row['observed_grain']))
        if row['observed_N']is not None:per_farm[row['farm_id']]['N'].append(abs(pn-row['observed_N']))
        if row['observed_C']is not None:per_farm[row['farm_id']]['C'].append(abs(pc-row['observed_C']))
        rows.append({'key':key,'farm_id':row['farm_id'],'predicted_grain':pg,'observed_grain':row['observed_grain'],'predicted_shoot_N':pn,'observed_shoot_N':row['observed_N'],'predicted_shoot_C':pc,'observed_shoot_C':row['observed_C']})
    score={'grain_MAE':float(np.mean([np.mean(v['grain'])for v in per_farm.values()]))if per_farm else None,
        'N_MAE':float(np.mean([np.mean(v['N'])for v in per_farm.values()if v['N']]))if any(v['N']for v in per_farm.values())else None,
        'C_MAE':float(np.mean([np.mean(v['C'])for v in per_farm.values()if v['C']]))if any(v['C']for v in per_farm.values())else None,
        'grain_records':len(unique),'N_records':sum(r['observed_shoot_N']is not None for r in rows),'N_farms':len({r['farm_id']for r in rows if r['observed_shoot_N']is not None})}
    return score,rows


def run_prior_calibration(root,output,weather_dir=None,audit_dir=None,observation_path=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed prior-crop calibration')
    audit_dir=Path(audit_dir or root/'reports/rotation-recovery/input-audit');weather_dir=Path(weather_dir or root/'reports/rotation-recovery/weather-exact')
    observation_path=Path(observation_path or root/'reports/rotation-recovery/prior-crop-diagnosis/observed_tissue_and_grain.json')
    observations=json.loads(observation_path.read_text())['rows'];records=json.loads((audit_dir/'records.json').read_text());soils=json.loads((audit_dir/'soil_observations.json').read_text())
    old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text());fixed=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv')
    ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()));folds=json.loads((root/'reports/reassessed/direct-rotation/report.json').read_text())['folds']
    specs=[('baseline',{'scheduled_harvest':True})]+[(f'maturity_{gdd}',{'scheduled_harvest':True,'maize_maturity_gdd':gdd})for gdd in [1900,2300]]
    specs.extend((f'soy_rue{rue}_N{n}',{'scheduled_harvest':True,'soy_rue_multiplier':rue,'soy_N_fraction':n})for rue in [1,1.25,1.5]for n in [.025,.03,.035]if not(rue==1 and n==.035))
    protocol={'adaptive_development_follow_up':True,'same_106_pairs_and_farm_folds':True,
        'hypothesis':'The process model underpredicts measured prior soybean/maize grain and overpredicts measured soybean shoot N; current maize-only calibration cannot repair previous-crop state propagation.',
        'crop_parameter_candidates':[{'name':n,'parameters':p}for n,p in specs],
        'maize_selection':'Fit-farm contrast RMSE/2000 + target maize yield MAE/10000 + .01*((maturity-1500)/400)^2.',
        'soy_selection':'Fit-farm prior soybean grain MAE/3000 + available shoot-N MAE/100 + .05*((RUE_multiplier-1)^2+(N_fraction/.035-1)^2). If no tissue N exists in fit farms, N fraction is not fitted and stays .035.',
        'observations':'Existing raw crop-specific grain yield moisture conversions .87 soybean/.845 maize. Tissue N is measured standing aboveground production, not verified returned residue/mineralization.',
        'leakage_rule':'No target-farm maize or prior-soybean observation used in its own parameter fit; farm grouping includes all plots/seasons.',
        'no_arbitrary_history_multiplier':True,'no_final_validation_claim':True}
    write_json(out/'protocol_frozen.json',protocol);preds={};pairs=fixed.copy()
    raw=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet').set_index('record_id');y={rid:float(raw.loc[rid,'observed_yield_dry_kg_ha'])for rid in ids}
    for name,params in specs:
        pred,meta=simulate(root,records,old,soils,weather_dir,ids,'C_soil',params)
        for value in pred.values():value['soy_seed_n']=70*params.get('soy_N_fraction',.035)
        preds[name]=pred;pairs[name]=pair_predictions(fixed,pred);write_json(out/(name+'_record_predictions.json'),pred)
        print('prior calibration candidate',name,flush=True)
    selected=fixed.copy();selected['D_crop_calibrated']=np.nan;selection=[];target_errors={};validation_rows=[]
    for fold in folds:
        train=pairs[pairs.farm_id.isin(fold['fit_farms'])];test=fixed[fixed.farm_id.isin(fold['test_farms'])]
        if test.empty:continue
        maturity=[];soy=[]
        for name,params in specs:
            if name=='baseline'or name.startswith('maturity_'):
                macro=[]
                for farm,g in train.groupby('farm_id'):
                    rids={rid for r in g.to_dict('records')for col in ['reference_records','candidate_records']for rid in r[col].split(';')}
                    macro.append(float(np.sqrt(np.mean((g[name]-g.observed_difference)**2)))/2000+float(np.mean([abs(preds[name][rid]['prediction']-y[rid])for rid in rids]))/10000)
                gdd=params.get('maize_maturity_gdd',1500);maturity.append({'name':name,'objective':float(np.mean(macro)+.01*((gdd-1500)/400)**2)})
            if name=='baseline'or name.startswith('soy_'):
                score,_=prior_scores(preds[name],records,observations,fold['fit_farms'],'soybeans')
                if score['grain_MAE']is None:continue
                if score['N_MAE']is None and params.get('soy_N_fraction',.035)!=.035:continue
                obj=score['grain_MAE']/3000+(score['N_MAE']/100 if score['N_MAE']is not None else 0)+.05*((params.get('soy_rue_multiplier',1)-1)**2+(params.get('soy_N_fraction',.035)/.035-1)**2)
                soy.append({'name':name,'objective':float(obj),'fit_crop_scores':score})
        m=min(maturity,key=lambda v:(v['objective'],v['name']))['name'];b=min(soy,key=lambda v:(v['objective'],v['name']))['name']if soy else 'baseline'
        params={**dict(specs)[m],**dict(specs)[b]};params['scheduled_harvest']=True
        selection.append({'fold':fold['fold'],'fit_farms':fold['fit_farms'],'calibration_farm':fold['calibration_farm'],'test_farms':fold['test_farms'],'maturity_selected':m,'soy_selected':b,'parameters':params,'maturity_fit':maturity,'soy_fit':soy})
        # Recompute combined physical trajectories; never sum fitted yield effects.
        fold_ids=sorted({rid for r in test.to_dict('records')for col in ['reference_records','candidate_records']for rid in r[col].split(';')})
        pred,_=simulate(root,records,old,soils,weather_dir,fold_ids,'C_soil',params)
        for value in pred.values():value['soy_seed_n']=70*params.get('soy_N_fraction',.035)
        selected.loc[test.index,'D_crop_calibrated']=pair_predictions(test,pred)
        for rid in fold_ids:target_errors[rid]=abs(pred[rid]['prediction']-y[rid])
        for crop in ['corn','soybeans']:
            score,obsrows=prior_scores(pred,records,observations,fold['test_farms'],crop)
            validation_rows.extend({**r,'crop':crop,'fold':fold['fold']}for r in obsrows)
        write_json(out/(f'fold_{fold["fold"]}_selected_record_predictions.json'),pred)
    write_json(out/'selections_frozen.json',selection);selected.to_csv(out/'paired_predictions.csv',index=False);pd.DataFrame(validation_rows).to_csv(out/'held_farm_prior_crop_validation.csv',index=False)
    report={'status':'crop_specific_calibration_on_fit_farms_evaluated','protocol':protocol,'D_crop_calibrated':scores(selected,'D_crop_calibrated'),
        'D_crop_calibrated_absolute_yield_MAE':float(np.mean(list(target_errors.values()))),'fold_selection':selection,
        'verified_returned_residue_or_mineralization':False,'water_error':None,'recommendation':None,'independent_final_test':False}
    write_json(out/'report.json',report);return report
