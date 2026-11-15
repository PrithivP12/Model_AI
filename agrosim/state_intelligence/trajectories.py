"""Audited multi-year state trajectories and conservative mechanism interventions."""
from pathlib import Path
from dataclasses import replace, asdict
from datetime import date
from copy import deepcopy
import json
import numpy as np
import pandas as pd
from ..io import write_json
from ..processes import SoilState, Processes, default_crops, CARBON_FRACTION
from ..rotation_recovery.mechanisms import ReconstructionKernel
from ..rotation_recovery.inputs import prepare_record
from ..rotation_recovery.experiment import pair_predictions, scores
from .observer import Stock, Operator, Observation, update


class TraceKernel(ReconstructionKernel):
    """Telemetry only: identical state equations to the scheduled kernel."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.capture_runs=False
        self.lifecycle=[]
        self.growth_stats={}

    def _grow(self, state, plant, day, et0, flux):
        before=plant.gdd; crop=self.crops[plant.planting.crop]
        result=super()._grow(state,plant,day,et0,flux)
        if not plant.growth_ended:
            stats=self.growth_stats.setdefault(str(plant.planting.date),{'peak_LAI':0.,'water_sum':0.,'N_sum':0.,'days':0})
            stats['peak_LAI']=max(stats['peak_LAI'],flux['lai']);stats['water_sum']+=flux['water_stress_factor']
            stats['N_sum']+=flux['n_growth_factor'];stats['days']+=1
        if before<crop.maturity_gdd<=plant.gdd:
            self.lifecycle.append({'event':'physiological_maturity','date':str(day.date),'planted':str(plant.planting.date),'origin':'simulated'})
        return result

    def harvest_reason(self, plant, day, killed):
        if killed and not plant.growth_ended:
            self.lifecycle.append({'event':'crop_death_frost','date':str(day.date),'planted':str(plant.planting.date),'origin':'simulated'})
        return super().harvest_reason(plant,day,killed)

    def _harvest(self, state, plant, day, depth, reason, flux):
        root_c=sum(state.residue_c_kg_ha);root_n=sum(state.residue_n_kg_ha)
        n=plant.n_kg_ha;biomass=plant.biomass_dry_kg_ha
        h=super()._harvest(state,plant,day,depth,reason,flux)
        stats=self.growth_stats.get(str(plant.planting.date),{'peak_LAI':0.,'water_sum':0.,'N_sum':0.,'days':0})
        h.update(root_residue_C=sum(state.residue_c_kg_ha)-root_c,
                 root_residue_N=sum(state.residue_n_kg_ha)-root_n,
                 plant_total_N=n,shoot_non_grain_dry=max(0.,biomass*(1-self.crops[plant.planting.crop].root_biomass_fraction)-h['grain_dry_kg_ha']),
                 root_dry=biomass*self.crops[plant.planting.crop].root_biomass_fraction,
                 peak_LAI=stats['peak_LAI'],mean_water_stress=stats['water_sum']/max(1,stats['days']),
                 mean_N_growth_factor=stats['N_sum']/max(1,stats['days']))
        self.lifecycle.append({'event':'cover_termination' if plant.planting.harvest_mode=='terminate_to_residue' else 'harvest_and_residue_return',
                               'date':str(day.date),'planted':str(plant.planting.date),'origin':reason,
                               'removed_shoot_fraction':plant.planting.aboveground_residue_removal_fraction})
        return h


def shoot_observation(row, crop):
    fields=('AGR11','AGR12') if crop=='soybeans' else ('AGR09','AGR10','AGR24','AGR26')
    if any(row.get(f) is None or row[f]<0 for f in fields):return None
    if crop=='soybeans':return [row['AGR11'],row['AGR12']]
    return [row['AGR09']+row['AGR24'],row['AGR10']+row['AGR26']]


def constrain_production(state, harvest, observed, *, site, plot, mode, relative_sd,
                         target_year, resources=('carbon','nitrogen')):
    """A measured-production operator at the harvest boundary, NOT a residue sensor.

    All-retained scenario maps production to a new transfer buffer. Old residues,
    roots, mineral N and flux totals are untouched; the analysis increment is
    separately added to the surface pool. Exact sample/report dates are unknown.
    """
    year=int(harvest['planted'][:4]); row=observed.get(f'{site}:{plot}:{year}',{})
    if year>=target_year:return state, [], {'water':0.,'carbon':0.,'nitrogen':0.}
    values=shoot_observation(row,harvest['crop'])
    if values is None:return state, [], {'water':0.,'carbon':0.,'nitrogen':0.}
    prior=[harvest['shoot_residue_c_added_kg_ha'],harvest['shoot_residue_n_added_kg_ha']]
    stocks=[Stock('fresh_shoot_C','carbon'),Stock('fresh_shoot_N','nitrogen')]
    covariance=np.diag([max(1.,.5*v)**2 for v in prior])
    current=prior;audits=[];increments={'water':0.,'carbon':0.,'nitrogen':0.}
    for i,resource in enumerate(('carbon','nitrogen')):
        if resource not in resources:continue
        op=Operator('fresh_shoot_'+resource,tuple(1. if k==i else 0. for k in range(2)),
                    'kg/ha','aboveground non-grain production','standing_shoot_'+resource)
        obs=Observation(f'{site}:{plot}:{year}:{resource}',values[i],max(1.,relative_sd*values[i]),
                        op.unit,op.support,op.semantic,None,None,None,
                        f'CSCAP Agronomic row {row.get("source_row")}; maturity-stage sample, some C/cob values may use standards per dictionary')
        result=update(stocks,current,covariance,op,obs,mode=mode,
                      decision_date=f'{target_year}-01-01',state_date=harvest['harvested'])
        current=result['values'];covariance=result['covariance'];a=result['audit']
        a.update(crop_year=year,target_year=target_year,retention_fraction=1.,
                 retention_evidence='Unverified all-retained scenario; production is not returned residue',
                 sample_time='R6 maize/R8 soy; unknown exact day',
                 prior_error='50% relative SD with 1 kg/ha floor, sensitivity assumption',
                 observation_error=f'{relative_sd:.0%} relative SD with 1 kg/ha floor, sensitivity assumption')
        audits.append(a)
        for k,v in a['analysis_increment'].items():increments[k]+=v
    copied=deepcopy(state)
    copied.surface_residue_c_kg_ha+=current[0]-prior[0]
    copied.surface_residue_n_kg_ha+=current[1]-prior[1]
    return copied,audits,increments


def run_trajectory(weather, layers, state, plants, events, kernel, *, observed=None,
                   site='',plot='',target_year=None,mode='free',relative_sd=.5,
                   resources=('carbon','nitrogen')):
    """Split at real harvest/year boundaries with exact explicit W/C/N accounting."""
    first=deepcopy(state);initial=kernel.inventories(first,None)
    totals={};adjustments={'water':0.,'carbon':0.,'nitrogen':0.};harvests=[];audit=[];annual=[];pretarget=None
    boundaries=sorted({p.harvest_deadline for p in plants}|{date(y,12,31)for y in range(weather[0].date.year,weather[-1].date.year+1)})
    offset=0;plant=None
    for stop in boundaries:
        if stop>weather[-1].date or offset>=len(weather):continue
        end=next(i for i in range(offset,len(weather))if weather[i].date==stop)
        start=weather[offset].date
        if start==date(target_year,1,1):pretarget={**asdict(state),'standing_plant':asdict(plant)if plant else None}
        active=[p for p in plants if start<=p.date<=stop]
        run=kernel.run(weather[offset:end+1],state,active,[e for e in events if start<=e.date<=stop],keep_daily=False,
                       initial_plant=plant,allow_unharvested=True)
        plant=run['active_plant_state']
        state=SoilState(**run['final_state']);state.validate(layers)
        for k,v in run['totals'].items():totals[k]=totals.get(k,0.)+v
        for h in run['harvests']:
            harvests.append(h)
            if observed and str(h['planted'])in kernel.options['observed_harvest_sowing_dates']:
                state,new,delta=constrain_production(state,h,observed,site=site,plot=plot,mode=mode,
                    relative_sd=relative_sd,target_year=target_year,resources=resources)
                audit.extend(new)
                for k,v in delta.items():adjustments[k]+=v
        if stop.month==12 and stop.day==31:annual.append({'year':stop.year,'state':asdict(state),'standing_plant':asdict(plant)if plant else None})
        offset=end+1
    final=kernel.inventories(state,plant)
    ledger={k:final[k]-initial[k]-totals.get(k+'_inputs',0.)+totals.get(k+'_outputs',0.)-adjustments[k]for k in initial}
    if max(abs(v)for v in ledger.values())>1e-6:raise ArithmeticError('State-aware whole-trajectory ledger failed')
    if pretarget is None:raise ValueError('Target-year starting state not captured')
    return {'harvests':harvests,'pretarget_state':pretarget,'annual_states':annual,'final_state':asdict(state),
            'physical_fluxes':totals,'state_adjustments':adjustments,'observation_audit':audit,
            'lifecycle':kernel.lifecycle,'ledger_error':ledger}


VARIANTS={
    'scheduled_control':{},
    'no_residue_decay':{'residue_decay':0.},
    'no_fixation':{'fixation':0.},
    'remove_shoot_residue':{'remove_shoot':1.},
    'retro_sd25':{'mode':'retrospective','sd':.25},
    'retro_sd50':{'mode':'retrospective','sd':.5},
    'retro_sd100':{'mode':'retrospective','sd':1.},
    'retro_C_only':{'mode':'retrospective','sd':.5,'resources':['carbon']},
    'retro_N_only':{'mode':'retrospective','sd':.5,'resources':['nitrogen']},
    'operational':{'mode':'operational','sd':.5},
}


def run_states(root, output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    base=root/'reports/rotation-recovery/reproduced-final'
    records=json.loads((base/'input-audit/records.json').read_text());soils=json.loads((base/'input-audit/soil_observations.json').read_text())
    observed=json.loads((base/'validated-observations/observed_tissue_and_grain.json').read_text())['rows']
    old=json.loads((root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/eligible_trajectories.json').read_text())
    fixed=pd.read_csv(root/'reports/scientific-intelligence/reproduced-crop-scoped/rotation/paired_predictions.csv')
    ids=sorted(set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode()))
    cal=json.loads((root/'reports/scientific-intelligence/refinement/training_fit_frozen.json').read_text())['candidates'][1]['parameters']
    predictions=fixed[['pair_id','farm_id','site_id','year','fold','observed_difference','reference_records','candidate_records']].copy()
    reports={}
    write_json(out/'protocol.json',{'variants':VARIANTS,'source':'fixed independent-LIRF partition parameters from previous phase; no new crop parameter fits',
        'mode_B':'No historical exact tissue timestamp or availability exists. Operational observations will be rejected, not backdated.',
        'mode_C':'Prior-only production-buffer diagnosis with assumed retention; no target-crop outcome as input.'})
    for name,spec in VARIANTS.items():
        runs={};record_predictions={};error=0.;applied=0;rejected=0
        for i,rid in enumerate(ids):
            rec=records[rid];outputs=[]
            for n,pulsed in [(40,True),(120,False)]:
                weather,layers,state,plants,events,_=prepare_record(root,rec,old[rid],soils[rec['site_id']+':'+rec['plot_id']],root/'reports/rotation-recovery/weather-exact','C_soil',n,pulsed)
                plants=[replace(p,aboveground_residue_removal_fraction=spec.get('remove_shoot',0.))for p in plants]
                crops=default_crops();crops['corn']=replace(crops['corn'],max_root_depth_m=1.05,harvest_index=cal['harvest_index'],grain_n_fraction=cal['grain_n_fraction'])
                crops['soybeans']=replace(crops['soybeans'],fixation_max_n_kg_ha_day=crops['soybeans'].fixation_max_n_kg_ha_day*spec.get('fixation',1.))
                process=Processes(maize_n_dilution_a=.034,residue_decay_per_day_20C=.008*spec.get('residue_decay',1.))
                kernel=TraceKernel(layers,crops,process,rec['latitude'],options={'root_beta_per_m':4,'observed_harvest_sowing_dates':[str(p.date)for p,s in zip(plants,rec['timeline'])if s['harvest_date']]})
                run=run_trajectory(weather,layers,state,plants,events,kernel,observed=observed,site=rec['site_id'],plot=rec['plot_id'],target_year=rec['year'],mode=spec.get('mode','free'),relative_sd=spec.get('sd',.5),resources=spec.get('resources',('carbon','nitrogen')))
                y=next(h['grain_dry_kg_ha']for h in run['harvests']if h['planted'].startswith(str(rec['year'])))
                outputs.append({'initial_N_scenario':n,**run});error=max(error,max(abs(v)for v in run['ledger_error'].values()))
                applied+=sum(a['applied']for a in run['observation_audit']);rejected+=sum(not a['applied']for a in run['observation_audit'])
            runs[rid]=outputs;record_predictions[rid]={'prediction':float(np.mean([next(h['grain_dry_kg_ha']for h in r['harvests']if h['planted'].startswith(str(rec['year'])))for r in outputs]))}
            if i%70==0:print('state trajectories',name,i,flush=True)
        write_json(out/(name+'_trajectories.json'),runs);write_json(out/(name+'_record_predictions.json'),record_predictions)
        predictions[name]=pair_predictions(fixed,record_predictions)
        reports[name]={**scores(predictions,name),'maximum_WCN_ledger_error':error,'observation_updates_applied':applied,'observations_rejected_or_disabled':rejected}
    reference=pd.read_csv(base/'observation-constrained/paired_predictions.csv')
    parity=float(np.max(abs(predictions.scheduled_control-reference.E_segmented_no_adjustments)))
    if parity>1e-8:raise ArithmeticError('New state telemetry changed scheduled-control predictions')
    predictions.to_csv(out/'paired_predictions.csv',index=False)
    report={'status':'state_trajectories_and_ablation_complete','same_pairs':106,'models':reports,
        'scheduled_control_parity_max_difference':parity,'operational_parity_max_difference':float(np.max(abs(predictions.operational-predictions.scheduled_control))),
        'historical_operational_assimilation_supported':False,'soil_moisture_assimilation':False,'verified_residue_return':False,
        'observation_error_calibrated':False,'recommendation':None}
    write_json(out/'report.json',report);return report
