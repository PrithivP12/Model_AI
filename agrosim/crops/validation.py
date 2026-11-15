"""Bounded process tests and explicit failure inventory, not field calibration."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from datetime import date,timedelta
from pathlib import Path
import csv,gzip,json,math

from ..processes import Simulator,Layer,SoilState,WeatherDay,Planting,Management
from ..io import load_config,write_json,sha256
from ..simulation import prepare
from .profiles import profiles,SOURCES,REFERENCE
from .products import mappings
from .runtime import run


PARAM_UNITS={'base_temp_C':'degC','optimum_temp_C':'degC','maximum_temp_C':'degC','kill_minimum_temp_C':'degC',
 'maturity_gdd':'degC day','max_root_depth_m':'m','root_biomass_fraction':'fraction','rue_g_dry_per_MJ_PAR':'g dry/MJ PAR',
 'max_lai':'m2/m2','specific_leaf_area_m2_kg':'m2/kg dry','vegetative_leaf_fraction':'fraction','extinction_coefficient':'dimensionless',
 'kc_mid':'dimensionless','depletion_fraction':'fraction','plant_n_fraction':'kgN/kg dry','grain_n_fraction':'kgN/kg dry',
 'harvest_index':'fraction','seed_dry_kg_ha':'kg dry/ha','uptake_max_n_kg_ha_day':'kgN/ha/day','fixation_max_n_kg_ha_day':'kgN/ha/day','fixation_inhibition_n_kg_ha':'kgN/ha'}


def scenario(profile,context='favorable',days=360,start=date(2020,4,1)):
    crop=profile['crop_parameters'];a=profile['archetype_parameters']
    t=a.get('reference_mean_temperature_C',crop['optimum_temp_C']-2)
    layers=[Layer(.3,.1,.3,.5),Layer(.7,.1,.3,.5)]
    soil=SoilState([90.,210.],[100.,60.],[10000.,15000.],[900.,1300.],[300.,200.],[6.,4.],[t,t],300.,6.)
    weather=[]
    for i in range(days):
        mean=5. if profile['identifier']=='garlic'and i<40 else t
        rain=5.;solar=20.
        if context=='dry':rain=0.
        if context=='heat':mean=crop['maximum_temp_C']+4
        if context=='cold':mean=crop['base_temp_C']-5
        d=start+timedelta(days=i);weather.append(WeatherDay(d,rain,mean-3,mean+3,solar,4.))
    if context=='dry':soil.water_mm=[l.mm(l.wilting_point)for l in layers]
    key=crop['name'];end=weather[-1].date
    deadline=date(end.year+1,12,31)if a.get('perennial')else end
    plants=[Planting(start,key,deadline)];events=[Management(start,100.,0.)]
    return Simulator(layers,latitude=40.),soil,weather,plants,events


def save_daily(path,rows):
    fields=['date','crop']+sorted({k for r in rows for k,v in r.items()if isinstance(v,(int,float))})
    with path.open('wb')as raw:
        with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0)as stream:
            import io
            wrapper=io.TextIOWrapper(stream,encoding='utf8',newline='');w=csv.DictWriter(wrapper,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows);wrapper.flush();wrapper.detach()


def scalar(run_result):
    state=run_result['final_state']['ecosystem'];s=state['soil'];flux=state['cumulative_fluxes']
    return {'product_dry_kg_ha':sum(x['product_dry_kg_ha']for x in run_result['product_harvests']),
        'product_harvest_count':sum(x['product_dry_kg_ha']>1e-9 for x in run_result['product_harvests']),
        'net_assimilated_C_kg_ha':flux.get('net_assimilated_c_kg_ha',0.),'plant_uptake_N_kg_ha':flux.get('plant_uptake_n_kg_ha',0.),
        'fixed_N_kg_ha':flux.get('fixed_n_kg_ha',0.),'transpiration_mm':flux.get('transpiration_mm',0.),
        'final_water_mm':sum(s['water_mm']),'final_mineral_N_kg_ha':sum(s['mineral_n_kg_ha']),
        'final_humus_C_kg_ha':sum(s['humus_c_kg_ha']),'final_residue_C_kg_ha':sum(s['residue_c_kg_ha'])+s['surface_residue_c_kg_ha'],
        'living_plant_at_end':state['plant']is not None,'maximum_daily_error':max(run_result['maximum_daily_error'].values()),
        'maximum_whole_error':max(map(abs,run_result['conservation'].values())),'checkpoint_restores':run_result['checkpoint_restores']}


def validate(config_path,output):
    cfg,base=load_config(config_path);directory=Path(output);directory.mkdir(parents=True,exist_ok=False)
    ps=profiles();financial=mappings();results=[];harvest_rows=[];tests={};ecology={};source={}
    for ident,p in ps.items():
        if not p['crop_parameters']:
            tests[ident]={'status':'blocked','growth_simulation':False,'blockers':p['blockers'],'independent_accuracy':None};continue
        print('crop',ident,flush=True)
        for context in cfg['contexts']:
            physical,soil,weather,plants,events=scenario(p,context,days=cfg['smoke_days'])
            result=run(physical,soil,weather,plants,events,[ident],resume=True)
            value=scalar(result);rows=result.pop('daily');growth=result.pop('growth_days')
            result['phenology']={'transitions':growth,'scope':'Executed model states, not observed phenology'}
            sub=directory/'crops'/ident;sub.mkdir(parents=True,exist_ok=True)
            save_daily(sub/(context+'-daily.csv.gz'),rows)
            write_json(sub/(context+'-state.json'),result)
            results.append({'crop_id':ident,'context':context,**value})
            harvest_rows.extend({'crop_id':ident,'context':context,**{k:v for k,v in h.items()if k not in ['material_transfers','organ_state']}}for h in result['product_harvests'])
            if context=='favorable':
                whole=run(physical,soil,weather,plants,events,[ident],resume=False)
                same=whole['final_state']==result['final_state']and whole['harvests']==result['harvests']and whole['product_harvests']==result['product_harvests']
                if not same:raise ArithmeticError('Continuous/resumed crop state mismatch: '+ident)
                positive=value['product_dry_kg_ha']>1e-9
                tests[ident]={'status':'executed'if positive else'zero_product_blocked','growth_simulation':True,
                   'daily_growth':value['net_assimilated_C_kg_ha']>0,'harvest_obtained':positive,'conservation_pass':value['maximum_whole_error']<1e-6 and value['maximum_daily_error']<1e-7,
                   'checkpoint_exact':same,'independent_accuracy':None,'reference_environment':'Declared synthetic controlled20MJ/day, daily5mm rain, explicit soil/N; profile-specific temperature and garlic chilling, not observations',
                   'actual_phenology_days':(date.fromisoformat(result['harvests'][0]['harvested'])-plants[0].date).days+1 if result['harvests']else None,
                   'literature_duration_error':None,'reason':'No independently matched cultivar/phenology observations, so no empirical error reported'}
        ecology[ident]={'shared_soil_water_C_N':True,'residue_to_existing_decomposition':True,
            'legacy_guilds':'available unchanged through IntegratedCropKernel','new_crop_host_status':'disabled unless caller explicitly supplies TierD host assumptions; no universal pest compatibility',
            'no_new_organisms':True}
    # Fixed representative ecosystem batches, not every possible rotation.
    living,lbase=load_config((base/cfg['living_config']).resolve());integrations=[]
    batches=[('cereal_legume',['barley','cowpea','barley']),('cereal_oilseed_legume',['sorghum','sunflower','chickpea']),
      ('root_rotation',['potato','barley','cowpea']),('vegetables',['lettuce','broccoli','beetroot']),
      ('fruit_pods',['tomato','pole_bean','okra']),('fiber_oilseed',['hemp','cotton','flax']),
      ('pseudocereals',['quinoa','grain_amaranth','chia']),('bulbs',['onion','garlic','onion']),
      ('immature_kernel',['sweet_corn','cowpea','barley']),
      ('cover_legume',['velvet_bean','sorghum','lentil']),('forage_perennial',['alfalfa']),('tropical_grass_perennial',['napier_grass']),('sugar_perennial',['sugarcane'])]
    for name,sequence in batches:
        print('integration',name,flush=True)
        physical,soil,_,_,_=scenario(ps[sequence[0]]);begin=date(2020,1,1);end=date(2022,12,31)
        per=ps[sequence[0]]['archetype_parameters'].get('perennial');weather=[]
        for i in range((end-begin).days+1):
            d=begin+timedelta(days=i);t=19. if name in ['vegetables','bulbs','forage_perennial']else 27.
            if name=='bulbs'and d.year==2021 and d.timetuple().tm_yday<=40:t=5.
            weather.append(WeatherDay(d,5.,t-3,t+3,20.,4.))
        if per:plants=[Planting(begin,sequence[0],date(2023,12,31))];events=[Management(date(y,1,1),100.,0.)for y in range(2020,2023)]
        else:plants=[Planting(date(y,1,1),crop,date(y,12,15))for y,crop in zip(range(2020,2023),sequence)];events=[Management(p.date,100.,0.)for p in plants]
        hosts={crop:{'tier':'D','compatible_guilds':['folivore','host_pressure'],'source':'Explicit illustrative host compatibility for this integration, not measured crop-pest specificity'}for crop in sequence if crop in ['barley','potato','tomato','sorghum']}
        result=run(physical,soil,weather,plants,events,sequence,living_config=living,host_assumptions=hosts)
        # Same environmental/initial states, own conserved on/off physical loops.
        off=deepcopy(living);off['enabled'].update(weed=False,herbivore=False,pathogen=False)
        control=run(physical,soil,weather,plants,events,sequence,living_config=off,host_assumptions=hosts)
        sub=directory/'integrations'/name;sub.mkdir(parents=True,exist_ok=False)
        save_daily(sub/'living-daily.csv.gz',result.pop('daily'));result.pop('growth_days',None)
        save_daily(sub/'control-daily.csv.gz',control.pop('daily'));control.pop('growth_days',None)
        write_json(sub/'living-state.json',result);write_json(sub/'control-state.json',control)
        a,b=scalar(result),scalar(control);integrations.append({'id':name,'sequence':sequence,'perennial':per,'days':len(weather),
             'living':a,'control':b,'on_minus_off':{k:a[k]-b[k]for k in a if isinstance(a[k],(int,float))and not isinstance(a[k],bool)},
             'host_assumptions':hosts,'empirical_validation':False})
    # Reuse existing NASA history for one appropriately bounded experimental
    # temperate rotation; do not force every crop through Illinois.
    c,cbase=load_config((base/cfg['reference_config']).resolve());physical,soil,weather,provenance,_=prepare(c,cbase)
    sequence=['barley','chickpea','sunflower'];plants=[Planting(date(y,4,20),crop,date(y,10,15))for y,crop in zip([2027,2028,2029],sequence)]
    events=[Management(p.date,100. if p.crop!='chickpea'else 20.,20.)for p in plants]
    nasa=run(physical,soil,weather[0][1],plants,events,sequence,living_config=living)
    sub=directory/'integrations'/'NASA_Illinois';sub.mkdir(parents=True,exist_ok=False);save_daily(sub/'daily.csv.gz',nasa.pop('daily'));nasa.pop('growth_days',None)
    write_json(sub/'state.json',nasa);source={'actual_source':provenance[0],'experimental_sequence':sequence,'model_result':scalar(nasa),
         'role':'Actual cached NASA POWER forcing under explicit hypothetical crop/soil/management; no local-field or crop suitability validation'}
    write_json(directory/'NASA_forcing.json',source)
    canonical=[]
    for ident,p in ps.items():
        row=deepcopy(p)
        if tests[ident]['status']=='zero_product_blocked':row['implementation_status']='PARTIALLY_EXECUTABLE';row['blockers']+=['No nonzero product obtained in chosen smoke environment; do not claim complete crop runtime']
        row['runtime_test']=tests[ident];row['financial_mapping']=financial[ident];canonical.append(row)
    write_json(directory/'crop_registry_88.json',{'canonical_entries':88,'crops':canonical,'old_species_archive_entries':92,
      'reconciliation':'Original92 species inventory and screenshot88 crop-use choices are different sets, not92minus4. Existing records preserved. User-facing canonical identities exactly match all88 screenshot names.',
      'groupings_are_not_taxonomy':True})
    counts=dict(Counter(p['implementation_status']for p in canonical))
    params={p['identifier']:{'crop':p['crop_parameters'],'archetype':p['archetype_parameters']}for p in canonical}
    write_json(directory/'crop_parameters.json',params)
    write_json(directory/'parameter_provenance.json',{'sources':SOURCES,'parameters':{p['identifier']:p['parameter_provenance']for p in canonical},
       'units':PARAM_UNITS,'new_numerical_profiles':'Explicit TierC/D only, no claims of measured crop-specific calibration or empirical bounds',
       'arch_parameter_units':{'establishment_gdd':'degC day','product_start_fraction':'fraction thermal development','product_allocation_fraction':'fraction available new biomass',
        'product_maturation_gdd':'degC day','senescence_day':'1/day','root_turnover_day':'1/day','harvest_fraction':'fraction stock',
        'harvest_interval_days':'day','harvest_ready_fraction':'fraction thermal development','reserve_fraction':'fraction new biomass',
        'reserve_mobilization_day':'1/day','dormancy_temp_C':'degC','waterlogging_suppression_at_saturation':'fraction','photoperiod_min_hours':'h','chilling_required_days':'day',
        'pegging_gdd':'degC day',
        'reference_days':'day','reference_mean_temperature_C':'degC'}})
    write_json(directory/'archetype_inventory.json',{name:{'crop_ids':[p['identifier']for p in canonical if p['archetype']==name],
        'implemented':not name.startswith('blocked_'),'explicit_blockers':sorted({x for p in canonical if p['archetype']==name for x in p['blockers']})}
        for name in sorted({p['archetype']for p in canonical})})
    write_json(directory/'crop_ecosystem_compatibility.json',{'crops':ecology,'representative_multiyear_integrations':integrations,
                                                           'NASA_integration':source,'existing_living_engine_modified':False})
    write_json(directory/'crop_financial_compatibility.json',financial)
    write_json(directory/'crop_validation_metrics.json',{'crops':tests,'independent_accuracy':None,'yield_MAE':None,'phenology_MAE':None,
        'scope':'Executed process sanity, controlled synthetic environment/continuation/conservation; not new independent field validation or literature-range validation'})
    def csvwrite(name,rows):
        fields=sorted({k for r in rows for k in r})
        with (directory/name).open('w')as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:json.dumps(v,sort_keys=True)if isinstance(v,(dict,list))else v for k,v in row.items()}for row in rows)
    csvwrite('crop_growth_results.csv',results);csvwrite('crop_harvest_results.csv',harvest_rows)
    csvwrite('crop_support_matrix.csv',[{'crop_id':p['identifier'],'common_name':p['common_name'],'archetype':p['archetype'],
        'implementation_status':p['implementation_status'],'validation_status':p['validation_status'],'financial_support':p['financial_support'],
        'blockers':p['blockers'],'growth_simulated':tests[p['identifier']]['growth_simulation']}for p in canonical])
    readiness={'status':'bounded_crop_expansion_executed_with_disclosed_blockers','counts':counts,'canonical_crop_count':88,
       'claimed_executable':sum(p['implementation_status'].startswith('EXECUTABLE')for p in canonical),
       'profile_context_cases':len(results),'multiyear_integrations':len(integrations)+1,'independently_validated_new_crops':0,
       'maximum_whole_balance_error':max(r['maximum_whole_error']for r in results),
       'maximum_daily_balance_error':max(r['maximum_daily_error']for r in results),'full88_executable':False,
       'V1_1_work_started':False,'farmer_recommendation':None}
    write_json(directory/'crop_expansion_readiness.json',readiness)
    lines=['# AGROSIM GLOBAL — 88-CROP FUNCTIONAL EXPANSION REPORT','',
      f'Canonical crop choices:88. Executable coverage:{readiness["claimed_executable"]}; status counts:{counts}. This is experimental process execution, not accuracy validation.','',
      f'{len(results)} profile/environment cases and{len(integrations)+1} representative multi-year integrations completed. Maximum crop smoke whole-run W/C/N residual:{readiness["maximum_whole_balance_error"]:.12g}.','',
      'New profiles reuse daily resource-limited crop growth and the existing soil/living material accounts. Organ allocation, maturation cohorts, senescence/root returns, bulbs, partial fruit/cutting harvest and herbaceous reserves are additive. Original corn/soybean/spring-wheat pathways remain separate.','',
      'All new numeric crop coefficients are transferred or explicitly hypothetical (TierC/D). No new empirical yield/phenology errors can be reported; matched independent cultivar observations are unavailable. No parameter fitting or sealed-outcome access performed.','',
      'Twenty named choices remain blocked because identity or lifecycle processes are missing. Perennial trees/palms, CAM, paddy and unresolved taxa are not executed as annual grains. Full88/88 coverage is not claimed.','',
      'Financial product identities are mapped for executable profiles; new regional prices/costs/recovery/fresh-basis data remain unavailable. An explicit dry-matter conversion adapter reuses finance without modifying it. Same-year multiple-harvest cash timing remains a finance blocker.','',
      '| Crop | Archetype | Implementation | Blocker |','|---|---|---|---|']
    for p in canonical:lines.append(f'| {p["common_name"]} | {p["archetype"]} | {p["implementation_status"]} | {"; ".join(p["blockers"])} |')
    (directory/'CROP_EXPANSION_FINDINGS.md').write_text('\n'.join(lines)+'\n')
    write_json(directory/'implementation.json',{str(p.relative_to(Path(__file__).parents[2])):sha256(p)for p in sorted(Path(__file__).parent.glob('*.py'))})
    write_json(directory/'manifest.json',{str(p.relative_to(directory)):sha256(p)for p in sorted(directory.rglob('*'))if p.is_file()})
    return readiness
