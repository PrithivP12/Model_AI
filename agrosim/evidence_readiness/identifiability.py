"""Conditional sensitivity, exact structural alias and joint composite calibration."""
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import json,math
import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from .. import calibration
from ..io import write_json
from ..data.lirf import old_cases,soil_prior,labels
from ..recovery.process import training_field_capacity
from ..intelligence.observations import crop_observations
from ..transfer_recovery.cross_environment import lirf_trace,lirf_score
from ..transfer_recovery.kernel import TransferKernel


class ShapeKernel(TransferKernel):
    def growth_temperature_factor(self,day,crop):
        if crop.name!='corn'or'shape_alpha'not in self.options:return super().growth_temperature_factor(day,crop)
        alpha=self.options['shape_alpha']
        if not math.isfinite(alpha)or not 0<=alpha<=1:raise ValueError('Shape blend is a bounded diagnostic, not free regional yield scaling')
        t=day.tmean_C;tri=super().growth_temperature_factor(day,crop)
        plateau=max(0,min(1,(t-8)/7))if t<15 else 1. if t<=35 else max(0,min(1,(50-t)/15))
        return (1-alpha)*tri+alpha*plateau


def training_predictions(cases,prior,p,alpha):
    rows=[];runs=[]
    with patch('agrosim.transfer_recovery.cross_environment.TransferKernel',ShapeKernel):
        for case in cases:
            result,daily,_=lirf_trace(case,prior,p,{'root_beta_per_m':4,'shape_alpha':alpha})
            runs.append(result);rows.extend(daily)
    return runs,pd.DataFrame(rows)


def observation_vector(runs,daily,biomass,lai):
    lookup={r['id']:r for r in runs};values=[];target=[];names=[];normalizers=[]
    for cid in sorted(biomass):
        h=lookup[cid]['harvest_diagnostics'][0];values.append(h['biomass_dry_kg_ha']*.8);target.append(biomass[cid]);names.append(cid+':biomass');normalizers.append(biomass[cid]*np.sqrt(len(biomass)))
    for row in lai:
        d=daily[daily.case_id.eq(row['case_id'])&daily.date.eq(row['date'])]
        if len(d)!=1:raise ValueError('Measured LAI date lacks unique simulated daily support')
        values.append(float(d.iloc[0].lai));target.append(row['value']);names.append(row['case_id']+':LAI:'+row['date']);normalizers.append(4*np.sqrt(len(lai)))
    return np.asarray(values),np.asarray(target),np.asarray(normalizers),names


def parameter_matrix():
    return [
      {'parameters':['SLA','vegetative_leaf_fraction'],'evidence':'dated LAI and canopy/biomass','identifiable':'product only in legacy LAI equation','rule':'Never fit separately; coefficient=SLA*leaf_fraction'},
      {'parameters':['RUE','temperature_response'],'evidence':'biomass/radiation and dated LAI in one training year','identifiable':'conditional and confounded across climates','rule':'Fixed alternative shapes; two-composite fit, not unconstrained shape/RUE fit'},
      {'parameters':['maturity_GDD','development_temperature','flowering_fraction'],'evidence':'field-level CropLog stage notes','identifiable':'not full treatment/genotype development curve','rule':'Keep fixed; no flowering/phase regression against ambiguous notes'},
      {'parameters':['root_fraction','root_profile'],'evidence':'inferred root extent from water, no measured roots','identifiable':'not independently measured','rule':'Scenario/frozen conservative root allocation'},
      {'parameters':['grain_HI','grain_N','tissue_N_partition'],'evidence':'sample HI, final biomass, grain/stover concentrations','identifiable':'sample-specific ratios partly constrained; organ transfer rates not','rule':'Preserve measured support, no concentration-to-stock shortcut'},
      {'parameters':['residue_decay','humus_turnover','retention_fraction'],'evidence':'standing production, incomplete dated stocks','identifiable':'not separated from unknown return and initial states','rule':'Sensitivity interventions only'},
      {'parameters':['soil_hydraulics','initial_water','root_uptake'],'evidence':'LIRF matched profiles/retention; rotation unit conflict','identifiable':'conditional by depth/timing/management','rule':'Do not backfill field initial water from satellite or retention'},
      {'parameters':['N_supply','N_demand','fixation'],'evidence':'fertilizer, partial tissue and mineral N','identifiable':'initial states/dose completeness confounded','rule':'No arbitrary legume bonuses or unmeasured mineral-N assimilation'},
    ]


def run_identifiability(root,observations,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    p=json.loads((root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())['candidates'][1]['parameters']
    cases=[c for y in (2008,2009)for c in old_cases(root,y)];prior=training_field_capacity(root,soil_prior(root,True))
    traits,_=crop_observations(root,[2008,2009]);biomass={r['case_id']:r['aboveground_biomass_dry_kg_ha']for r in traits}
    lai=[r for r in observations['LAI']if r['year']in (2008,2009)]
    columns=['rue','sla','leaf_fraction','maturity_gdd','shape_alpha'];jac=[];sensitivity=[]
    for key in columns:
        vectors=[]
        for sign in (-1,1):
            q={**p};alpha=.5
            if key=='shape_alpha':alpha+=sign*.05
            else:q[key]*=math.exp(sign*.05)
            runs,daily=training_predictions(cases,prior,q,alpha)
            predicted,target,scales,names=observation_vector(runs,daily,biomass,lai)
            vectors.append(predicted/scales);sensitivity.append({'parameter':key,'sign':sign,'normalized_predictions':(predicted/scales).tolist()})
        jac.append((vectors[1]-vectors[0])/.1)
    j=np.array(jac).T;s=np.linalg.svd(j,compute_uv=False);rank=int(np.sum(s>s[0]*1e-6))
    norms=np.linalg.norm(j,axis=0);cosine=j.T@j/np.maximum(1e-20,np.outer(norms,norms))
    pd.DataFrame(j,index=names,columns=columns).to_csv(out/'scaled_sensitivity.csv')
    # Exact inverse product perturbation is distinct from local derivative evidence.
    q={**p,'sla':p['sla']*1.2,'leaf_fraction':p['leaf_fraction']/1.2}
    a,da=training_predictions(cases,prior,p,.5);b,db=training_predictions(cases,prior,q,.5)
    va,_,_,_=observation_vector(a,da,biomass,lai);vb,_,_,_=observation_vector(b,db,biomass,lai)
    alias=float(np.max(abs(va-vb)))
    if alias>1e-7:raise ArithmeticError('Expected legacy SLA/leaf alias changed; diagnose before fitting')
    write_json(out/'sensitivity_report.json',{'columns':columns,'singular_values':s.tolist(),'local_rank_at_relative_1e6':rank,
        'column_cosines':cosine.tolist(),'column_norms':norms.tolist(),'exact_inverse_SLA_leaf_max_prediction_change':alias,
        'scope':'Local dimensionless finite differences, not parameter CIs or global identifiability. Outcome scaling is not estimated sensor noise.',
        'training_biomass':len(biomass),'training_dated_LAI':len(lai),'parameter_identifiability_matrix':parameter_matrix()})
    fits=[]
    for name,alpha in [('joint_triangle',0.),('joint_plateau',1.)]:
        trace=[];cache={}
        def objective(x):
            key=tuple(float(v)for v in x)
            if key in cache:return cache[key]
            q={**p,'rue':float(x[0]),'sla':float(x[1]/p['leaf_fraction'])}
            runs,daily=training_predictions(cases,prior,q,alpha)
            predicted,target,scale,_=observation_vector(runs,daily,biomass,lai)
            residual=np.r_[(predicted-target)/scale,np.sqrt(.02)*np.log(x/np.array([3.2,8.8]))]
            trace.append({'RUE':float(x[0]),'leaf_area_coefficient':float(x[1]),'objective':float(np.sum(residual**2))});cache[key]=residual;return residual
        fit=least_squares(objective,[p['rue'],p['sla']*p['leaf_fraction']],bounds=([1.5,4.],[4.5,16.]),max_nfev=18,ftol=1e-5,xtol=1e-5,gtol=1e-5)
        fits.append({'name':name,'shape_alpha':alpha,'RUE':float(fit.x[0]),'leaf_area_coefficient':float(fit.x[1]),'success':bool(fit.success),
            'optimizer_message':str(fit.message),'function_evaluations':fit.nfev,'all_evaluations':trace,'fit_years':[2008,2009],
            'observations':{'biomass':12,'dated_LAI':19},'unidentified_parameters_fixed':p,
            'parameter_semantics':'Leaf area coefficient is SLA*leaf fraction; SLA representation is computational only, not a separate measured estimate.'})
        print('joint fit',name,fit.x,fit.success,flush=True)
    write_json(out/'joint_fit_frozen.json',fits)
    return {'sensitivity':json.loads((out/'sensitivity_report.json').read_text()),'fits':fits}
