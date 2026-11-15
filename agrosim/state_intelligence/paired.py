"""Compact measured paired-effect regression with fully nested farm preprocessing."""
from pathlib import Path
import json
import math
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.linear_model import Ridge
from sklearn.base import BaseEstimator, TransformerMixin
from ..io import write_json
from ..rotation_recovery.experiment import scores


ALPHAS=(1.,10.,100.,1000.)
RAW={'history','weather','management','soil_properties'}
STATES={'soil_water_state','mineral_N_state','residue_state','physiology_state'}


class StableWeightedScaler(TransformerMixin,BaseEstimator):
    """Weighted z scores with explicitly nonnegative centered variance.

    Detect numerical near-constants before dividing, so roundoff in otherwise
    equal physical states cannot become a learned decision signal.
    """
    def fit(self,x,y=None,sample_weight=None):
        x=np.asarray(x,dtype=float);w=np.ones(len(x))if sample_weight is None else np.asarray(sample_weight)
        reference=x[0];centered=x-reference
        self.mean_=reference+np.average(centered,axis=0,weights=w)
        self.var_=np.average((x-self.mean_)**2,axis=0,weights=w)
        threshold=100*np.finfo(float).eps*np.maximum(1.,abs(self.mean_))
        self.scale_=np.sqrt(self.var_);self.scale_[self.scale_<=threshold]=1.
        self.n_features_in_=x.shape[1];return self

    def transform(self,x):return (np.asarray(x,dtype=float)-self.mean_)/self.scale_


def farm_weights(groups):
    groups=np.asarray(groups);unique,counts=np.unique(groups,return_counts=True)
    mapping=dict(zip(unique,counts))
    return np.asarray([len(groups)/(len(unique)*mapping[g])for g in groups])


def fit_ridge(x,y,farms,alpha):
    model=Pipeline([('impute',SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True)),
                    ('scale',StableWeightedScaler()),('ridge',Ridge(alpha=alpha,solver='svd'))])
    weights=farm_weights(farms)
    model.fit(x,y,scale__sample_weight=weights,ridge__sample_weight=weights)
    return model


def select_alpha(frame, columns, base=None):
    """Only original fit farms reach this function, including preprocessing fits."""
    y=frame.observed_difference.to_numpy()
    offset=np.zeros(len(frame))if base is None else np.asarray(base)
    rows=[]
    for alpha in ALPHAS:
        errors=[]
        for held in sorted(frame.farm_id.unique()):
            train=frame.farm_id.to_numpy()!=held;valid=~train
            model=fit_ridge(frame.loc[train,columns],y[train]-offset[train],frame.loc[train,'farm_id'],alpha)
            pred=model.predict(frame.loc[valid,columns])+offset[valid]
            errors.append(float(np.sqrt(np.mean((pred-y[valid])**2))))
        rows.append({'alpha':alpha,'inner_farm_macro_RMSE':float(np.mean(errors))})
    selected=min(rows,key=lambda r:(r['inner_farm_macro_RMSE'],-r['alpha']))['alpha']
    return selected,rows


def bootstrap_changes(frame,candidate,reference,seed=1819):
    delta=[]
    for _,g in frame.groupby('farm_id',sort=True):
        y=g.observed_difference.to_numpy();p=g[candidate].to_numpy();q=g[reference].to_numpy()
        regret=lambda a:np.where(a>1e-6,np.maximum(-y,0),np.maximum(y,0))
        correct=lambda a:((np.sign(a)==np.sign(y))&(abs(a)>1e-6)).astype(float)
        delta.append([np.mean(abs(p-y)-abs(q-y)),np.mean(regret(p)-regret(q)),np.mean(correct(p)-correct(q))])
    delta=np.asarray(delta);rng=np.random.default_rng(seed);draw=rng.integers(0,len(delta),(4000,len(delta)));sample=delta[draw].mean(axis=1)
    return {'farm_macro_MAE_change':float(delta[:,0].mean()),'MAE_change_95pct':np.quantile(sample[:,0],[.025,.975]).tolist(),
            'farm_macro_regret_change':float(delta[:,1].mean()),'regret_change_95pct':np.quantile(sample[:,1],[.025,.975]).tolist(),
            'all_pair_signed_correctness_change_95pct':np.quantile(sample[:,2],[.025,.975]).tolist(),
            'scope':'Fixed-prediction bootstrap of seven exposed farms; not a new independent test or multiplicity-adjusted causal inference.'}


def model_specs(groups):
    specs={'direct_paired':RAW,'hybrid_paired':RAW|STATES,
           'hybrid_residual':RAW|STATES,'state_only':STATES,
           'hybrid_retrospective_observed_states':RAW|{'retrospective_observed_state'}}
    for group in sorted(STATES|{'history','weather','management'}):
        specs['hybrid_without_'+group]=(RAW|STATES)-{group}
    return {name:sorted(k for k,v in groups.items()if v in included)for name,included in specs.items()}


def run_paired(root,features_dir,states_dir,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    frame=pd.read_csv(Path(features_dir)/'features.csv')
    manifest=json.loads((Path(features_dir)/'feature_manifest.json').read_text());groups={k:v['group']for k,v in manifest.items()}
    fixed=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv')
    physical=pd.read_csv(Path(states_dir)/'paired_predictions.csv')
    if not frame.pair_id.equals(fixed.pair_id)or not frame.fold.equals(fixed.fold)or not np.allclose(frame.observed_difference,fixed.observed_difference,rtol=0,atol=1e-8):
        raise ValueError('Fixed cohort/folds/targets changed')
    if not physical.pair_id.equals(frame.pair_id):raise ValueError('Physical pair order mismatch')
    specs=model_specs(groups)
    for name,columns in specs.items():
        if not columns or any(c not in manifest for c in columns):raise ValueError('Explicit feature whitelist required')
    write_json(out/'protocol_frozen.json',{'models':specs,'alphas':ALPHAS,'label':'Observed paired dry-grain difference, not synthetic yields',
        'prediction_role':'Retrospective reconstruction; archived availability unknown, not an operational preplant forecast',
        'fit_rule':'Original fit farms only, nested leave-one-fit-farm-out alpha selection; preprocessing refit in every inner split.',
        'selection_objective':'Inner farm-macro RMSE only; retain all negative results, no outer-score parameter selection.',
        'weights':'Equal farms with within-farm equal pair weights.',
        'physical_hybrid':'Simulated states remain unchanged. Learned outcome corrections are separately labeled and are not conserved fluxes.'})
    predictions=frame[['pair_id','farm_id','site_id','year','fold','observed_difference']].copy()
    predictions['always_CS_policy']=1.;predictions['always_CC_policy']=-1.;predictions['zero_effect']=0.
    predictions['history_baseline']=fixed.family_history_baseline
    predictions['previous_ecosystem']=fixed.corrected_coupled
    predictions['scheduled_ecosystem']=physical.scheduled_control
    for name in specs:predictions[name]=np.nan
    folds=json.loads((root/'reports/reassessed/direct-rotation/report.json').read_text())['folds']
    selection=[];intervals=[];corrections=[];coefficients=[]
    for fold in folds:
        fit=frame.farm_id.isin(fold['fit_farms']);cal=frame.farm_id.eq(fold['calibration_farm']);test=frame.farm_id.isin(fold['test_farms'])
        if not test.any():continue
        assert not (fit&cal|fit&test|cal&test).any()
        if frame.loc[fit,'farm_id'].nunique()<2:raise ValueError('At least two original fit farms required')
        for name,columns in specs.items():
            residual=name=='hybrid_residual'
            offset=physical.scheduled_control.to_numpy()if residual else np.zeros(len(frame))
            alpha,inner=select_alpha(frame.loc[fit],columns,offset[fit])
            model=fit_ridge(frame.loc[fit,columns],frame.loc[fit,'observed_difference'].to_numpy()-offset[fit],frame.loc[fit,'farm_id'],alpha)
            correction=model.predict(frame.loc[test,columns]);pred=correction+offset[test]
            predictions.loc[test,name]=pred
            selection.append({'model':name,'fold':fold['fold'],'fit_farms':sorted(frame.loc[fit,'farm_id'].unique()),
                'calibration_farms':sorted(frame.loc[cal,'farm_id'].unique()),'test_farms':sorted(frame.loc[test,'farm_id'].unique()),
                'alpha':alpha,'inner_selection':inner,'outer_yields_used_to_fit_or_select':False})
            coefficients.append({'model':name,'fold':fold['fold'],'imputation_statistics':model['impute'].statistics_.tolist(),
                'scaler_means':model['scale'].mean_.tolist(),'ridge_intercept':float(model['ridge'].intercept_),
                'coefficient_standardized':model['ridge'].coef_.tolist(),
                'feature_names':model['impute'].get_feature_names_out(columns).tolist()})
            if residual:
                corrections.extend({'pair_id':pid,'mechanistic_contrast':float(b),'learned_outcome_correction':float(c),'physical_flux':False,
                                    'ecosystem_state_modified':False}for pid,b,c in zip(frame.loc[test,'pair_id'],offset[test],correction))
            calpred=model.predict(frame.loc[cal,columns])+offset[cal]if cal.any()else np.array([])
            residuals=abs(calpred-frame.loc[cal,'observed_difference'].to_numpy())
            # New-farm exchangeability requires calibration-group maxima, not rows.
            m=frame.loc[cal,'farm_id'].nunique();rank=math.ceil((m+1)*.9)
            qgroup=None if rank>m else float(np.sort(residuals)[rank-1])
            if qgroup is not None:raise AssertionError('Fixed design unexpectedly permits a finite 90% group interval; implement group maxima before changing cohort')
            qrow=float(np.quantile(residuals,.9,method='higher'))if len(residuals)else None
            for pid,yy,pp in zip(frame.loc[test,'pair_id'],frame.loc[test,'observed_difference'],pred):
                intervals.append({'model':name,'pair_id':pid,'fold':fold['fold'],'calibration_farms':m,
                    'nominal90_group_lower':None,'nominal90_group_upper':None,'group_interval_unbounded':True,
                    'group_decision':0,'group_interval_covers':True,
                    'row90_diagnostic_halfwidth':qrow,'row90_diagnostic_covers':None if qrow is None else abs(yy-pp)<=qrow,
                    'row_interval_has_new_farm_guarantee':False})
    if predictions[list(specs)].isna().any().any():raise ValueError('Every fixed pair must receive a held-farm prediction')
    predictions.to_csv(out/'paired_predictions.csv',index=False)
    write_json(out/'selections.json',selection);write_json(out/'fit_artifacts.json',coefficients)
    write_json(out/'learned_outcome_corrections.json',corrections);pd.DataFrame(intervals).to_csv(out/'intervals.csv',index=False)
    models={}
    for name in predictions.columns[6:]:
        v=scores(predictions,name);v['yield_MAE']=None
        v['yield_MAE_reason']='A contrast alone does not identify either absolute yield.'
        if name.endswith('_policy'):
            for metric in ['mae_difference_kg_dry_ha','rmse_difference_kg_dry_ha','farm_macro_rmse_kg_dry_ha']:v[metric]=None
            v['contrast_magnitude_predicted']=False
        models[name]=v
    byfarm=[]
    for farm,g in predictions.groupby('farm_id',sort=True):
        for name in models:byfarm.append({'farm':farm,'model':name,**scores(g,name)})
    pd.DataFrame(byfarm).to_csv(out/'farm_metrics.csv',index=False)
    uncertainty={name:bootstrap_changes(predictions,name,'history_baseline')for name in specs}
    for name in specs:
        if name.startswith('hybrid_without_'):uncertainty[name+'_vs_full']=bootstrap_changes(predictions,name,'hybrid_paired')
    interval_summary={}
    for name in specs:
        rows=[r for r in intervals if r['model']==name]
        finite=[r for r in rows if r['row90_diagnostic_halfwidth']is not None]
        interval_summary[name]={'nominal90_new_farm_width':None,'interval_unbounded':True,'coverage':1.,'decision_coverage':0.,
            'interpretation':'Vacuous group interval; no supported directional decision.',
            'row_diagnostic_coverage':float(np.mean([r['row90_diagnostic_covers']for r in finite]))if finite else None,
            'row_diagnostic_mean_width':float(np.mean([2*r['row90_diagnostic_halfwidth']for r in finite]))if finite else None,
            'row_diagnostic_has_new_farm_guarantee':False}
    history=models['history_baseline'];gates={}
    for name in specs:
        v=models[name];gates[name]=bool(v['farm_macro_rmse_kg_dry_ha']<history['farm_macro_rmse_kg_dry_ha'] and
            v['mae_difference_kg_dry_ha']<=history['mae_difference_kg_dry_ha']and
            v['correct_signed_decisions_fraction_all_pairs']>=history['correct_signed_decisions_fraction_all_pairs']and
            v['control_fallback_regret_all_pairs']<=history['control_fallback_regret_all_pairs'])
    report={'status':'grouped_paired_statistical_and_hybrid_experiment_complete','pairs':106,'farms':7,'models':models,
        'joint_development_gate_vs_history':gates,'farm_cluster_uncertainty':uncertainty,'uncertainty':interval_summary,
        'unseen_development_farms':'Each prediction excludes that farm from fitting/preprocessing/alpha selection; all farms were exposed in prior phases.',
        'operational_forecast_accuracy':None,'new_independent_final_accuracy':None,'recommendation':None,
        'causal_effect_identified':False,'limitation':'Matched treatments may differ in fertilizer/management; archived availability is unknown. Seven farms and multiple exploratory ablations cannot establish global or causal accuracy.'}
    write_json(out/'report.json',report);return report
