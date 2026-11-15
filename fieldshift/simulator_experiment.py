"""Run paired historical-climate replays and assumed-parameter sensitivity."""
from dataclasses import asdict,replace
from datetime import date
from itertools import product
from pathlib import Path
from copy import deepcopy
import argparse
import hashlib
import json
import numpy as np
import pandas as pd
from . import simulator as sim
from .sources import nasa_power

ROOT=Path(__file__).resolve().parents[1]
REFERENCES={
    'crop_et':'https://www.fao.org/4/X0490E/x0490e0a.htm',
    'water_stress':'https://www.fao.org/4/X0490E/x0490e0e.htm',
    'reference_et':'https://www.fao.org/4/X0490E/x0490e07.htm',
    'residue_n_cycle':'https://www.nrcs.usda.gov/sites/default/files/2022-10/National-Agronomy-Manual.pdf'
}


def weather_from_frame(frame):
    names=['date','rain_mm','tmin_C','tmax_C','solar_MJ_m2']
    if any(c not in frame for c in names):raise ValueError('Weather needs columns '+', '.join(names))
    weather=[]
    for row in frame.to_dict('records'):
        supplied=row.get('et0_mm')
        et0=None if supplied is None or pd.isna(supplied) else float(supplied)
        weather.append(sim.WeatherDay(pd.Timestamp(row['date']).date(),float(row['rain_mm']),
            float(row['tmin_C']),float(row['tmax_C']),float(row['solar_MJ_m2']),et0))
    return weather


def forcing(config,offline=False,csv=None):
    if csv:
        d=pd.read_csv(csv)
        provenance={'source':'user-supplied weather file','filename':str(Path(csv).name),
                    'sha256':hashlib.sha256(Path(csv).read_bytes()).hexdigest()}
    else:
        metadata,daily=nasa_power(config['latitude'],config['longitude'],config['decision_date'],
                                  history_years=config['historical_weather_years'],offline=offline)
        d=daily.reset_index().rename(columns={'index':'date','PRECTOTCORR':'rain_mm','T2M_MIN':'tmin_C',
            'T2M_MAX':'tmax_C','ALLSKY_SFC_SW_DWN':'solar_MJ_m2'})
        d=d[['date','rain_mm','tmin_C','tmax_C','solar_MJ_m2']]
        provenance=metadata
    d['date']=pd.to_datetime(d.date)
    # This runner is a historical replay. The core engine also accepts scenario forcing.
    if (d.date>=pd.Timestamp(config['decision_date'])).any():raise ValueError('Historical replay weather must precede decision date')
    w=weather_from_frame(d)
    for day in w:day.validate()
    if any(b.date!=a.date+pd.Timedelta(days=1) for a,b in zip(w,w[1:])):raise ValueError('Missing or unordered daily weather')
    return d,w,provenance


def parameter_sets(config):
    layers=tuple(sim.Layer(**x) for x in config['layers']);initial=sim.SoilState(**config['initial_state'])
    crops=sim.default_crops()
    if config.get('crop_parameters'):
        for name,changes in config['crop_parameters'].items():
            if name not in crops:raise ValueError('Unsupported crop parameter key')
            crops[name]=replace(crops[name],**changes)
    processes=sim.Processes(**config.get('processes',{}))
    sets=[('base',layers,initial,crops,processes)]
    rng=np.random.default_rng(config['parameter_sampling_seed'])
    if not 0<=config['parameter_samples']<=100:raise ValueError('Use 0–100 sensitivity samples')
    for i in range(config['parameter_samples']):
        capacity=float(rng.uniform(.85,1.15));mineral_scale=float(rng.uniform(.5,1.5));organic_scale=float(rng.uniform(.8,1.2))
        altered=[];s=deepcopy(initial)
        for j,layer in enumerate(layers):
            fc=layer.wilting_point+(layer.field_capacity-layer.wilting_point)*capacity
            changed=replace(layer,field_capacity=fc,drainage_fraction_day=float(rng.uniform(.3,.7)))
            altered.append(changed)
            relative=(s.water_mm[j]-layer.mm(layer.wilting_point))/layer.mm(layer.field_capacity-layer.wilting_point)
            s.water_mm[j]=changed.mm(changed.wilting_point)+relative*changed.mm(changed.field_capacity-changed.wilting_point)
            s.mineral_n_kg_ha[j]*=mineral_scale;s.humus_c_kg_ha[j]*=organic_scale;s.humus_n_kg_ha[j]*=organic_scale
        changed_crops={name:replace(c,rue_g_dry_per_MJ_PAR=c.rue_g_dry_per_MJ_PAR*float(rng.uniform(.85,1.15)),
            maturity_gdd=c.maturity_gdd*float(rng.uniform(.9,1.1)),
            fixation_max_n_kg_ha_day=c.fixation_max_n_kg_ha_day*float(rng.uniform(.5,1.5)),
            plant_n_fraction=c.plant_n_fraction*float(rng.uniform(.85,1.15))) for name,c in crops.items()}
        proc=replace(processes,residue_decay_per_day_20C=processes.residue_decay_per_day_20C*float(rng.uniform(.5,1.5)),
                     humus_decay_per_day_20C=processes.humus_decay_per_day_20C*float(rng.uniform(.5,1.5)))
        sets.append((f'sample_{i+1:02d}',tuple(altered),s,changed_crops,proc))
    # Equal-weight sensitivities, not samples from a fitted posterior or confidence distribution.
    return sets


def schedule(sequence,first_year,config):
    plantings=[];events=[]
    for i,crop in enumerate(sequence):
        m=config['management_by_crop'][crop];year=first_year+i
        sow=date.fromisoformat(f'{year}-{m["planting_month_day"]}')
        harvest=date.fromisoformat(f'{year}-{m["harvest_deadline_month_day"]}')
        plantings.append(sim.Planting(sow,crop,harvest,m['residue_removal_fraction']))
        # A disclosed demonstration split, not an adaptive fertilizer recommendation.
        n=m['fertilizer_n_kg_ha']
        events.extend([sim.Management(sow,.5*n),sim.Management(sow+pd.Timedelta(days=30),.5*n)])
    return plantings,events


def pareto_flags(table):
    values=table[['median_total_grain_dry_kg_ha','fertilizer_n_kg_ha','median_leached_n_kg_ha']].to_numpy()
    return [not any((other[0]>=row[0] and other[1]<=row[1] and other[2]<=row[2]) and
                    (other[0]>row[0] or other[1]<row[1] or other[2]<row[2]) for other in values) for row in values]


def run(config_path,out,offline=False,csv=None):
    config_path=Path(config_path);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    config=json.loads(config_path.read_text())
    if not 1<=config['seasons']<=5:raise ValueError('Use 1–5 simulation seasons')
    if not 1<=len(config['candidate_crops'])<=5 or len(set(config['candidate_crops']))!=len(config['candidate_crops']):raise ValueError('Use distinct candidate crops')
    if not config.get('soil_profile_provenance') or not config.get('initial_state_provenance'):raise ValueError('Initial soil assumptions need provenance')
    frame,weather,provenance=forcing(config,offline,csv)
    frame.to_csv(out/'weather_input.csv',index=False)
    sets=parameter_sets(config);sequences=list(product(config['candidate_crops'],repeat=config['seasons']))
    years=range(weather[0].date.year,weather[-1].date.year-config['seasons']+2)
    windows=[(y,[d for d in weather if y<=d.date.year<y+config['seasons']]) for y in years]
    windows=[(y,w) for y,w in windows if w and w[0].date==date(y,1,1) and w[-1].date==date(y+config['seasons']-1,12,31)]
    if not windows:raise ValueError('Need complete calendar-year climate windows')
    samples=[{'sample_id':name,'layers':[asdict(x) for x in layers],'initial_state':asdict(initial),
              'crop_parameters':{k:asdict(v) for k,v in crops.items()},'processes':asdict(processes)}
             for name,layers,initial,crops,processes in sets]
    assumptions={'simulator_version':sim.VERSION,'config':config,'parameter_sets':samples,
                 'parameter_status':'All numerical soil, crop, management and process defaults are illustrative, not site/cultivar calibrated',
                 'parameter_sources':'Mechanisms informed by listed references. Numerical defaults and uncertainty ranges are explicit scenario assumptions.',
                 'references':REFERENCES,'weather_provenance':provenance,
                 'training_or_calibration_performed':False,'rotation_outcomes_loaded':False,
                 'climate_windows':[[y,y+config['seasons']-1] for y,_ in windows],
                 'sensitivity_interpretation':'Equal-weight exploratory parameter perturbations; overlapping historical windows are correlated; percentiles are not confidence intervals'}
    # Persist parameter assumptions before any scenario comparisons.
    (out/'assumptions.json').write_text(json.dumps(assumptions,indent=2,allow_nan=False))
    rows=[];all_harvests=[];detailed={};max_error={k:0. for k in ['water','nitrogen','carbon']}
    common_details={'corn-corn-corn','corn-soybeans-corn','soybeans-corn-corn'}
    detail_keys=common_details.intersection('-'.join(x) for x in sequences)
    if not detail_keys:detail_keys={'-'.join(x) for x in sequences[:3]}
    for sample,layers,initial,crops,processes in sets:
        engine=sim.Simulator(layers,crops,processes,config['latitude'])
        for start,w in windows:
            for sequence in sequences:
                key='-'.join(sequence);plantings,events=schedule(sequence,start,config)
                detail=sample=='base' and start==windows[-1][0] and key in detail_keys
                result=engine.run(w,initial,plantings,events,keep_daily=detail)
                if len(result['harvests'])!=len(sequence):raise ArithmeticError('Incomplete harvest records')
                total=result['totals'];meta={'sequence':key,'climate_start_year':start,'sample_id':sample}
                row={**meta,'kind':'simulation_not_observation','total_grain_dry_kg_ha':sum(h['grain_dry_kg_ha'] for h in result['harvests']),
                     'fertilizer_n_kg_ha':total['fertilizer_n_kg_ha'],'fixed_n_kg_ha':total['fixed_n_kg_ha'],
                     'leached_n_kg_ha':total['leached_n_kg_ha'],'drainage_mm':total['drainage_mm'],'runoff_mm':total['runoff_mm'],
                     'evapotranspiration_mm':total['evaporation_mm']+total['transpiration_mm'],
                     'end_mineral_n_kg_ha':sum(result['final_state']['mineral_n_kg_ha']),
                     'change_humus_c_kg_ha':sum(result['final_state']['humus_c_kg_ha'])-sum(initial.humus_c_kg_ha),
                     'change_system_c_kg_ha':result['final_inventory']['carbon']-result['initial_inventory']['carbon'],
                     'nonmature_harvests':sum(h['reason']!='thermal_maturity' for h in result['harvests']),
                     **{k+'_whole_balance_error':v for k,v in result['whole_run_balance_error'].items()}}
                rows.append(row)
                all_harvests.extend({**meta,**h} for h in result['harvests'])
                for k,v in result['max_daily_balance_error'].items():max_error[k]=max(max_error[k],v)
                if detail:
                    pd.DataFrame(result['daily']).to_csv(out/f'{key}_daily.csv',index=False)
                    (out/f'{key}_run.json').write_text(json.dumps({k:v for k,v in result.items() if k!='daily'},indent=2))
                    detailed[key]=result
        print('Completed parameter set',sample,flush=True)
    runs=pd.DataFrame(rows);runs.to_csv(out/'scenario_runs.csv',index=False)
    harvests=pd.DataFrame(all_harvests);harvests.to_csv(out/'simulated_harvests.csv',index=False)
    summary=[]
    for key,g in runs.groupby('sequence'):
        summary.append({'sequence':key,'scenario_runs':len(g),'median_total_grain_dry_kg_ha':float(g.total_grain_dry_kg_ha.median()),
            'grain_p10_kg_ha':float(g.total_grain_dry_kg_ha.quantile(.1)),'grain_p90_kg_ha':float(g.total_grain_dry_kg_ha.quantile(.9)),
            'fertilizer_n_kg_ha':float(g.fertilizer_n_kg_ha.iloc[0]),'median_fixed_n_kg_ha':float(g.fixed_n_kg_ha.median()),
            'median_leached_n_kg_ha':float(g.leached_n_kg_ha.median()),'median_end_mineral_n_kg_ha':float(g.end_mineral_n_kg_ha.median()),
            'median_change_humus_c_kg_ha':float(g.change_humus_c_kg_ha.median()),'nonmature_harvest_fraction':float(g.nonmature_harvests.sum()/(len(g)*config['seasons']))})
    summary=pd.DataFrame(summary);summary['scenario_pareto_nondominated']=pareto_flags(summary)
    summary.to_csv(out/'sequence_summary.csv',index=False)
    # Compare the same year-3 corn under the same weather and parameters; year-1
    # histories differ, so no different commodity is mistaken for a rotation gain.
    last=harvests[harvests.sequence.isin(['corn-corn-corn','corn-soybeans-corn'])].copy()
    last=last[pd.to_datetime(last.planted).dt.year==last.climate_start_year+2]
    paired=last.pivot(index=['sample_id','climate_start_year'],columns='sequence',values='grain_dry_kg_ha')
    comparison=None
    if {'corn-corn-corn','corn-soybeans-corn'}<=set(paired):
        paired['simulated_year3_corn_difference_kg_ha']=paired['corn-soybeans-corn']-paired['corn-corn-corn']
        paired.to_csv(out/'paired_year3_corn.csv')
        difference=paired.simulated_year3_corn_difference_kg_ha
        comparison={'paired_scenarios':len(paired),'median_difference_dry_kg_ha':float(difference.median()),
                    'p10_difference':float(difference.quantile(.1)),'p90_difference':float(difference.quantile(.9)),
                    'fraction_positive':float((difference>0).mean()),
                    'meaning':'Assumption-sensitive simulated contrast, not measured benefit, accuracy or probability of farmer success'}
    report={'version':sim.VERSION,'status':'working_uncalibrated_simulator','scenario_runs':len(runs),'sequences':len(sequences),
            'seasons':config['seasons'],'weather_source':'NASA POWER historical weather' if csv is None else 'Supplied historical weather',
            'forcing_start':weather[0].date.isoformat(),'forcing_end':weather[-1].date.isoformat(),
            'climate_windows':len(windows),'parameter_sets':len(sets),'mass_balance_max_daily_errors':max_error,
            'whole_run_max_absolute_errors':{k:float(abs(runs[k+'_whole_balance_error']).max()) for k in ['water','nitrogen','carbon']},
            'paired_year3_corn':comparison,'rotation_outcome_data_used_for_fitting':False,'observational_validation_completed':False,
            'accuracy':None,'sequence_summary':summary.to_dict('records'),
            'limitations':['Initial soil conditions and crop/process parameters are assumptions, not measurements of this field.',
                'Historical weather replay is not a future weather forecast. Overlapping windows are not independent trials.',
                'Mineral N is a single mobile pool: nitrate/ammonium separation, volatilization and runoff N are not modeled.',
                'Soil liquid water freezing, snow sublimation, capillary rise and groundwater coupling are not modeled.',
                'Soil hydraulic properties are fixed within a run: no modeled soil structural improvement from added organic matter.',
                'Disease, pests, pollination, phosphorus, potassium, pH and microbial community composition are not modeled.',
                'Soybean phenology omits photoperiod. Spring wheat only; winter-wheat vernalization and perennial crops unsupported.',
                'RUE and harvest-index growth is simplified; simulated dry yields are not directly comparable to moisture-standardized observations.',
                'Fixation uses a simplified demand/rate limit and mineral-N suppression; no measured nodulation or explicit fixation energy cost.',
                'Higher grain mass across different crops is not equivalent to greater profit, nutritional value or sustainability.']}
    (out/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    write_markdown(report,assumptions,out)
    plot(detailed,summary,out,report['weather_source'])
    print('Saved',out/'SIMULATOR_REPORT.md',flush=True)
    return report


def write_markdown(r,assumptions,out):
    lines=['# Field Shift daily crop and soil simulator','',
           '**Working from-scratch research simulator. Yield predictions and sequence rankings are uncalibrated.**','',
           'This engine carries soil water, mineral nitrogen, fresh residues, humus carbon/nitrogen and snow continuously between crops. Crop growth is limited by intercepted radiation, temperature, water supply and nitrogen. Legumes fix only unmet nitrogen demand under a rate limit. Grain exports nitrogen at harvest; any remaining nitrogen benefit must pass through the tracked soil and residue pools. No preset rotation yield bonus or fixed soybean nitrogen credit is applied.','',
           '## What ran','',
           f'- {r["scenario_runs"]} runs: {r["sequences"]} sequences of {r["seasons"]} seasons × {r["climate_windows"]} historical climate windows × {r["parameter_sets"]} parameter sets.',
           f'- Forcing: {r["weather_source"]}, {r["forcing_start"]} to {r["forcing_end"]}. Every sequence within a paired scenario receives the same weather and starting soil. Soil state carries forward within each run and resets only between separate scenarios.',
           '- The soil profile, initial water and nitrogen, cultivar-free crop parameters, fertilizer schedule and all sensitivity ranges are disclosed assumptions. They are not measurements of this farm.',
           '- No rotation outcome file was read, and no fitting or calibration was performed.','',
           '## Numerical verification','',
           'Daily and full-run ledgers account for all modeled water, nitrogen and carbon inputs, exports, transfers and losses. A daily balance error larger than 1e-7 in the respective units stops execution. Carbon uses net biomass assimilation as atmospheric input; decomposition respiration is an explicit output. Biological nitrogen fixation is an external atmospheric input, not nitrogen created inside a soil pool.','',
           '| Ledger | Largest daily absolute error | Largest full-run absolute error | Units |','|---|---:|---:|---|']
    for k,unit in [('water','mm'),('nitrogen','kg N/ha'),('carbon','kg C/ha')]:
        lines.append(f'| {k} | {r["mass_balance_max_daily_errors"][k]:.3g} | {r["whole_run_max_absolute_errors"][k]:.3g} | {unit} |')
    lines+=['','Conservation verifies bookkeeping. It does not establish biological realism or yield accuracy.','',
            '## Scenario outputs','',
            'The table shows medians across historical replays and explicit parameter perturbations. The 10th–90th percentiles are sensitivity ranges, not confidence intervals or forecast probabilities. Grain values are dry biomass and must not be compared directly with moisture-standardized grain-yield observations.','',
            f'| Sequence | Grain dry mass (kg/ha, {r["seasons"]}-season total) | P10–P90 | Applied N (kg/ha) | N leaching (kg/ha) |',
            '|---|---:|---:|---:|---:|']
    for row in r['sequence_summary']:
        lines.append(f'| {row["sequence"]} | {row["median_total_grain_dry_kg_ha"]:,.0f} | {row["grain_p10_kg_ha"]:,.0f}–{row["grain_p90_kg_ha"]:,.0f} | {row["fertilizer_n_kg_ha"]:.0f} | {row["median_leached_n_kg_ha"]:.1f} |')
    if r['paired_year3_corn']:
        c=r['paired_year3_corn']
        lines+=['','## A controlled sequence contrast','',
                f'Compare year-3 corn in corn–soybean–corn with year-3 corn in corn–corn–corn. Weather, starting state, final corn parameters and final corn fertilizer are identical within each of {c["paired_scenarios"]} paired scenarios. The only differences arise from the earlier crop history and its associated disclosed management. Median simulated year-3 dry-grain difference: {c["median_difference_dry_kg_ha"]:,.0f} kg/ha; sensitivity P10–P90: {c["p10_difference"]:,.0f} to {c["p90_difference"]:,.0f} kg/ha. This is an output of the assumed mechanism, not evidence that the difference happens on a farm.','',
                'The different crop in year 2 also receives a different fertilizer rate. This is a cropping-system scenario contrast; it is not an isolated experimentally identified crop-species effect. The full mass balances show the fertilizer inputs and grain-N exports.']
    lines+=['','## Interpreting candidate sequences','',
            'No single “best rotation” is declared. `sequence_summary.csv` marks scenario-specific nondominated options when maximizing harvested dry grain while minimizing fertilizer N and modeled N leaching. These are tradeoffs under the assumptions, not validated recommendations. Composition differs between sequences, and a kilogram of corn is not economically equivalent to a kilogram of soybeans.','',
            '## Important missing processes','']
    lines.extend('- '+x for x in r['limitations'])
    lines+=['','## Files and reuse','',
            '`assumptions.json` stores all numerical parameters and weather provenance before simulation. `weather_input.csv` contains real weather observations/reanalysis inputs. `scenario_runs.csv`, `simulated_harvests.csv` and `paired_year3_corn.csv` contain simulated outputs. Selected runs include daily water/N/C diagnostics and final soil states.','',
            'Run the example from the project directory:','',
            '```sh',
            '.venv/bin/python -m fieldshift.simulator_experiment --config examples/simulator_illinois.json --output my-simulation --weather simulator-results/illinois/weather_input.csv --offline',
            '.venv/bin/python -m unittest test_simulator.py',
            '```','',
            'The core `Simulator.run` accepts a user-supplied layered soil state, crop parameters, daily weather, sowing/harvest dates, fertilizer and irrigation events. A custom CSV may be passed to the historical runner with `--weather`; it needs `date,rain_mm,tmin_C,tmax_C,solar_MJ_m2` and optional `et0_mm`. Missing dates or nonfinite forcing are rejected. Cross-year crop calendars can be handled by the core when the supplied crop model is suitable; the example runner uses one spring-sown crop per calendar year.','',
            '## Mechanism references','',
            'The code is original; the scientific ideas are established. Numerical defaults are disclosed illustrative assumptions rather than calibrated values from these sources.','']
    lines.extend('- '+k+': '+v for k,v in REFERENCES.items())
    lines+=['','![Daily simulated water and nitrogen](soil_state_comparison.png)','',
            '![Scenario tradeoffs](scenario_tradeoffs.png)']
    (out/'SIMULATOR_REPORT.md').write_text('\n'.join(lines)+'\n')


def plot(detailed,summary,out,weather_source='NASA POWER historical weather'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'axes.spines.top':False,'axes.spines.right':False,'font.size':10})
    if detailed:
        fig,axes=plt.subplots(3,1,figsize=(11,8),sharex=True,layout='constrained')
        for key,result in detailed.items():
            d=pd.DataFrame(result['daily']);d['date']=pd.to_datetime(d.date)
            for ax,col in zip(axes,['soil_water_mm','soil_mineral_n_kg_ha','plant_biomass_dry_kg_ha']):
                ax.plot(d.date,d[col],lw=1.15,label=key.replace('soybeans','soy'))
        for ax,label in zip(axes,['Profile soil water (mm)','Mineral nitrogen (kg N/ha)','Live crop dry biomass (kg/ha)']):ax.set_ylabel(label)
        axes[0].legend(frameon=False,ncol=3);axes[-1].set_xlabel('Historical weather date')
        fig.suptitle(f'Uncalibrated daily simulation · assumed soil · {weather_source}',fontsize=13)
        fig.savefig(out/'soil_state_comparison.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,6),sharey=True,layout='constrained')
    y=np.arange(len(summary));median=summary.median_total_grain_dry_kg_ha.to_numpy()/1000
    lower=summary.grain_p10_kg_ha.to_numpy()/1000;upper=summary.grain_p90_kg_ha.to_numpy()/1000
    axes[0].errorbar(median,y,xerr=np.array([median-lower,upper-median]),fmt='o',color='#267788',capsize=3)
    axes[0].set(yticks=y,yticklabels=[x.replace('soybeans','soy') for x in summary.sequence],
                xlabel='Dry grain mass over the sequence (Mg/ha)',title='Median and P10–P90 sensitivity range')
    axes[0].invert_yaxis();axes[0].set_xlim(left=0)
    axes[1].barh(y-.16,summary.fertilizer_n_kg_ha,height=.29,label='Applied fertilizer N',color='#a66d40')
    axes[1].barh(y+.16,summary.median_leached_n_kg_ha,height=.29,label='Modeled N leaching',color='#4c8093')
    axes[1].set(xlabel='Total over the sequence (kg N/ha)',title='Nitrogen inputs and modeled loss')
    axes[1].legend(frameon=False,loc='lower right')
    fig.suptitle('Uncalibrated scenario tradeoffs · ranges are not confidence intervals',fontsize=13)
    fig.savefig(out/'scenario_tradeoffs.png',dpi=160);plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--offline',action='store_true');parser.add_argument('--weather',type=Path)
    args=parser.parse_args();run(args.config,args.output,args.offline,args.weather)


if __name__=='__main__':main()
