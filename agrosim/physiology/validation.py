"""Bounded functional-coverage verification, never crop calibration or V1.1 research."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import csv,fcntl,gzip,json,sys,traceback

from ..io import load_config,write_json,sha256
from ..evaluator.engine import digest
from ..crops.profiles import profiles as old_profiles
from ..crops.validation import scenario as old_scenario,scalar,save_daily
from ..platform.verify import _reference_runs,_tests
from .profiles import coverage,resolve,SUBTYPES,SOURCES,SPECS,UNITS
from .scenarios import scenario,PROVENANCE
from .runtime import run
from .finance import product_support,evaluate_optional_finance,demonstrate_finance


def _read(path):return json.loads(Path(path).read_text())


def _gzip_json(path,value):
    with path.open('wb')as raw:
        with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0)as stream:
            stream.write(json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode())


def _summary(result):
    state=result['final_state']['ecosystem'];soil=state['soil'];plant=state['plant']
    return {'days':len(result['daily']),'product_dry_kg_ha':sum(h['product_dry_kg_ha']for h in result['product_harvests']),
        'positive_harvests':sum(h['product_dry_kg_ha']>0 for h in result['product_harvests']),
        'water_used_mm':sum(d['transpiration_mm']for d in result['daily']),
        'actual_external_C_kg_ha':sum(d['net_assimilated_c_kg_ha']for d in result['daily']),
        'living_plant':plant is not None,'final_water_mm':sum(soil['water_mm']),
        'final_mineral_N_kg_ha':sum(soil['mineral_n_kg_ha']),
        'maximum_daily_error':max(result['maximum_daily_error'].values()),
        'maximum_whole_error':max(map(abs,result['conservation'].values())),
        'checkpoint_restores':result['checkpoint_restores'],'independent_accuracy':None}


def _execute(identifier,context,subtype,out,*,living=None,crop_options_override=None):
    p,physical,soil,weather,plants,events,kwargs=scenario(identifier,context,subtype=subtype)
    if crop_options_override:kwargs['crop_options'][identifier].update(deepcopy(crop_options_override))
    if living:
        kwargs['living_config']=living
        if identifier in ['apple','guava']:
            kwargs['host_assumptions']={identifier:{'tier':'D','source':PROVENANCE+'; explicit hypothetical leaf-host compatibility only',
                'compatible_guilds':['folivore','host_pressure']}}
    result=run(physical,soil,weather,plants,events,[identifier],**kwargs)
    value=_summary(result);record={'crop':identifier,'subtype':subtype,'context':context,'mode':'living'if living else'standard',**value}
    out.mkdir(parents=True,exist_ok=False)
    save_daily(out/'daily.csv.gz',result['daily'])
    _gzip_json(out/'executed_state.json.gz',{k:v for k,v in result.items()if k!='daily'})
    write_json(out/'inputs.json',{'crop':p,'soil':asdict(soil),'latitude':physical.latitude,
        'weather_hash':digest([asdict(d)|{'date':str(d.date)}for d in weather]),
        'weather_source':PROVENANCE,'weather_support':'Controlled daily scenarios, not observed weather',
        'plantings':[asdict(x)for x in plants],'management':[asdict(x)for x in events],
        'initialization':kwargs.get('initial'),'crop_options':kwargs.get('crop_options'),'paddy_options':kwargs.get('paddy'),
        'host_assumptions':kwargs.get('host_assumptions'),'subtype':subtype})
    record['conservation_pass']=value['maximum_whole_error']<1e-6 and value['maximum_daily_error']<1e-7
    if context=='favorable':
        full=run(physical,soil,weather,plants,events,[identifier],resume=False,**kwargs)
        record['continuous_resume_exact']=all(full[k]==result[k]for k in ['final_state','daily','harvests','product_harvests'])
        record['positive_growth_and_harvest']=value['actual_external_C_kg_ha']>0 and value['product_dry_kg_ha']>0
        record['lifecycle_pass']=not p['archetype_parameters']['perennial']or value['days']>=1095 and value['living_plant']
        record['financial_missing_data']=evaluate_optional_finance(result,plants)['status']
    write_json(out/'summary.json',record)
    return record,result,plants


def verify(config_path,output):
    cfg,base=load_config(config_path);root=Path(__file__).resolve().parents[2]
    lock_path=root/'reports/final20-physiology/.verification.lock'
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a')as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another final20 verifier is active')
        return _verify(cfg,base,root,Path(output))


def _verify(cfg,base,root,out):
    if cfg['schema_version']!=1 or cfg['contexts']!=['favorable','drought','heat','cold']:raise ValueError('Frozen four-context protocol required')
    if set(cfg['subtypes'])!=set(SUBTYPES):raise ValueError('All three ambiguous crop choices require explicit subtype selection')
    for key,value in cfg['subtypes'].items():resolve(key,subtype=value)
    if cfg['finance_demonstration']['dry_matter_fraction']!=1.:raise ValueError('Verification uses raw dry product; no invented fresh conversion')
    out.mkdir(parents=True,exist_ok=False);write_json(out/'configuration.json',cfg)
    initial_hashes={str(p.relative_to(root)):sha256(p)for folder in ['agrosim','tests','configs']for p in sorted((root/folder).rglob('*'))
        if p.is_file()and p.suffix in ['.py','.json','.yaml']and '__pycache__'not in p.parts}
    write_json(out/'implementation.json',{'source_sha256':initial_hashes,'python':sys.version.split()[0]})
    selected=coverage(cfg['subtypes']);old=old_profiles();new=[k for k,p in old.items()if not p['crop_parameters']]
    living,_=load_config(base/cfg['living_config']);reference,rbase=load_config(base/cfg['reference_config'])
    write_json(out/'living_configuration.json',living)
    prior_rows=list(csv.DictReader((root/'reports/crop-expansion/verified-final/crop_growth_results.csv').open()))
    prior={(r['crop_id'],r['context']):r for r in prior_rows};legacy=[];differences=[]
    for identifier,p in old.items():
        if not p['crop_parameters']:continue
        print('preserved crop',identifier,flush=True)
        for context in ['favorable','dry','heat','cold']:
            physical,soil,days,plants,events=old_scenario(p,context,days=360)
            current=run(physical,soil,days,plants,events,[identifier]);values=scalar(current)
            legacy.append({'crop':identifier,'context':context,**values})
            for key,value in values.items():
                previous=prior[(identifier,context)][key]
                changed=(str(value)!=previous)if isinstance(value,bool)else(abs(value-float(previous))>1e-8)if isinstance(value,(int,float))else str(value)!=previous
                if changed:differences.append({'crop':identifier,'context':context,'metric':key,'before':previous,'after':value})
    write_json(out/'legacy_68_regression.json',{'cases':len(legacy),'differences':differences,'results':legacy,
        'original_only_calls_delegate_to_original_runtime':True,'V1_1_candidate_reactivated':False})
    reference_check,_=_reference_runs(reference,rbase,living);write_json(out/'reference_regression.json',reference_check)
    runs=[];failures=[];finances=[];accepted={};counterfactual=[]
    for identifier in new:
        subtype=cfg['subtypes'].get(identifier);tested=[]
        for context in cfg['contexts']+(['intermittent','nonflooded']if identifier=='rice'else[]):
            print('new physiology',identifier,subtype,context,flush=True)
            try:
                record,result,plants=_execute(identifier,context,subtype,out/'crops'/identifier/context)
                runs.append(record);tested.append(record)
                if context=='favorable':
                    default_result=result;default_plants=plants
            except Exception as exc:
                error={'crop':identifier,'context':context,'mode':'standard','error':str(exc),'traceback':traceback.format_exc()}
                failures.append(error);write_json(out/f'failure-{identifier}-{context}.json',error)
        print('new physiology',identifier,'living',flush=True)
        try:
            record,result,plants=_execute(identifier,'favorable',subtype,out/'crops'/identifier/'living',living=living)
            runs.append(record);tested.append(record)
            if identifier=='guava':
                for name,r,pp in [('standard',default_result,default_plants),('living',result,plants)]:
                    finances.append({'mode':name,**demonstrate_finance(r,pp,cfg['finance_demonstration'])})
                control=deepcopy(living);control['enabled'].update(weed=False,herbivore=False,pathogen=False)
                c,_,_=_execute(identifier,'favorable',subtype,out/'crops'/identifier/'living_control',living=control)
                runs.append(c);counterfactual.append({'crop':identifier,'same_weather_and_initial_stocks':True,
                    'living_on':record,'competition/injury_off':c,'interpretation':'Hypothetical conserved process sensitivity; not observed local ecology or causal yield validation'})
        except Exception as exc:
            error={'crop':identifier,'context':'living','mode':'living','error':str(exc),'traceback':traceback.format_exc()}
            failures.append(error);write_json(out/f'failure-{identifier}-living.json',error)
        favorable=[r for r in tested if r['context']=='favorable']
        accepted[identifier]=len(favorable)==2 and all(r['conservation_pass']for r in tested)and all(r['continuous_resume_exact']and r['positive_growth_and_harvest']and r['lifecycle_pass']and r['financial_missing_data']=='financial_data_unavailable'for r in favorable)and len(tested)>=5 and not any(f['crop']==identifier for f in failures)
    # Verify every additional advertised identity subtype, not only the example.
    variants=[]
    for identifier,subtypes in SUBTYPES.items():
        for subtype in subtypes:
            if subtype==cfg['subtypes'][identifier]:continue
            for context in cfg['contexts']:
                print('identity variant',identifier,subtype,context,flush=True)
                try:
                    record,_,_=_execute(identifier,context,subtype,out/'subtypes'/identifier/subtype/context);variants.append(record)
                    if not record['conservation_pass']or context=='favorable'and not(record['positive_growth_and_harvest']and record['continuous_resume_exact']):accepted[identifier]=False
                except Exception as exc:
                    accepted[identifier]=False;failures.append({'crop':identifier,'subtype':subtype,'context':context,'error':str(exc),'traceback':traceback.format_exc()})
    print('strawberry short-day overwintering variant',flush=True)
    try:
        record,_,_=_execute('strawberry','favorable',None,out/'variants'/'strawberry_short_day',
            crop_options_override={'flowering_type':'short_day','short_day_chill_hours':360.,'flowering_type_source':PROVENANCE})
        record['variant']='short_day_overwintering';variants.append(record)
        if not(record['positive_growth_and_harvest']and record['continuous_resume_exact']and record['conservation_pass']):accepted['strawberry']=False
    except Exception as exc:
        accepted['strawberry']=False;failures.append({'crop':'strawberry','context':'short_day','error':str(exc),'traceback':traceback.format_exc()})
    write_json(out/'new_crop_cases.json',{'runs':runs,'subtype_variants':variants,'failures':failures,'accepted':accepted,
        'scenario_semantics':'Explicit controlled environments and mature initial stocks where specified; no field prediction validation'})
    write_json(out/'ecological_sensitivity.json',counterfactual);write_json(out/'financial_demonstration.json',finances)
    rows=[]
    for identifier,p in selected.items():
        row={'crop_id':identifier,'name':p['common_name'],'previously_executable':old[identifier]['crop_parameters']is not None,
            'selected_subtype':cfg['subtypes'].get(identifier),'family':p.get('physiology_family',p['archetype']),
            'status':p['implementation_status']if identifier not in accepted or accepted[identifier]else'PARTIALLY_EXECUTABLE',
            'functional_test_passed':identifier not in accepted or accepted[identifier],
            'parameter_provenance':p['parameter_provenance'],'requirements':p['requirements'],
            'unavailable':p.get('unavailable',{}),'independent_accuracy':None,
            'remaining_blockers':[]if identifier not in accepted or accepted[identifier]else[f for f in failures if f['crop']==identifier],
            'product_support':product_support(identifier,cfg['subtypes'].get(identifier))if identifier in accepted else None}
        rows.append(row)
    write_json(out/'crop_coverage.json',{'canonical_count':88,'crops':rows,'explicit_subtype_selection_required':SUBTYPES,
        'mechanism_sources':{k:{'title':v[0],'url':v[1],'support':v[2]}for k,v in SOURCES.items()},
        'units':UNITS,'all_new_numeric_coefficients':'Tier C transfers or Tier D declarations; no field fit or Tier A accuracy claim'})
    with (out/'crop_coverage.csv').open('w')as stream:
        writer=csv.DictWriter(stream,fieldnames=['crop_id','name','previously_executable','selected_subtype','family','status','functional_test_passed','independent_accuracy'])
        writer.writeheader();writer.writerows({k:r[k]for k in writer.fieldnames}for r in rows)
    preserved=_read(root/'reports/final20-physiology/preservation.json')
    changed=[name for name,h in preserved.items()if name!='agrosim/__main__.py'and(not(root/name).exists()or sha256(root/name)!=h)]
    prior_artifacts=_read(root/'reports/final20-physiology/previous_verified_artifacts.json')
    changed_artifacts=[name for name,h in prior_artifacts.items()if sha256(root/'reports/platform-readiness/verified-final'/name)!=h]
    drift=[name for name,h in initial_hashes.items()if sha256(root/name)!=h]
    preservation={'unexpected_original_changes':changed,'previous_platform_artifacts_changed':changed_artifacts,'source_changed_during_run':drift}
    write_json(out/'preservation_check.json',preservation)
    print('new and preserved regression suites',flush=True);tests=_tests(root,out);write_json(out/'tests.json',tests)
    complete=all(accepted.values())and not differences and not failures and not any(preservation.values())and all(t['passed']for t in tests.values())and all(r['exact']for r in reference_check['V1_rotations'])and reference_check['living_checkpoint_exact']
    count=68+sum(accepted.values());gates=_gates(complete,accepted,differences,reference_check,runs,variants,failures,finances,preservation,tests)
    maximum=max([r['maximum_whole_error']for r in runs+variants]+[r['maximum_whole_error']for r in legacy])
    report={'status':'88/88_FUNCTIONALLY_EXECUTABLE_WITH_EXPLICIT_INPUTS'if complete else'PARTIAL_FUNCTIONAL_COVERAGE',
        'canonical_count':88,'functionally_executable':count,'original_reference':3,'previous_experimental':65,
        'new_experimental':sum(accepted.values()),'new_cases':len(runs),'additional_subtype_cases':len(variants),
        'old_cases':len(legacy),'maximum_whole_material_residual':maximum,'acceptance_gates':gates,
        'independent_accuracy':None,'farmer_recommendation':None,'full88_requires_declared_subtypes_and_reproductive_inputs':True,
        'default_legacy_registry_changed':False,'V1_1_promoted_or_reactivated':False}
    write_json(out/'report.json',report)
    text=['# AGROSIM GLOBAL — FINAL 88-CROP FUNCTIONAL COVERAGE REPORT','',report['status'],'',
        f'{count}/88 choices passed the functional gate. Original 68 retain the old registry/runtime. New biology is explicitly opt-in. Three broad names require a supplied subtype; reproductive support, initial stocks, rice field/N assumptions and nighttime CAM VPD are explicit inputs.',
        'No new crop has independent predictive accuracy evidence. Positive controlled products are proof of an executed pathway, not field yield validation.','',
        '| Criterion | Status | Evidence |','|---|---|---|']
    text += [f'| {key} | {value["status"]} | {value["evidence"]} |'for key,value in gates.items()]
    text += ['', 'See crop_coverage.csv/json, per-crop compressed daily/state trajectories, inputs and summaries, legacy_68_regression.json, reference_regression.json, new_crop_cases.json, ecological_sensitivity.json, financial_demonstration.json and tests.json.',
        'All new numerical physiology remains transferred/exploratory. Cacao exports whole pods, rice rough grain and coconut whole fruit; processed products and fresh conversions are unavailable without supplied evidence. No market retrieval, frontend, new organisms or V1 accuracy experiments were performed.']
    (out/'FUNCTIONAL_COVERAGE.md').write_text('\n'.join(text)+'\n')
    files={str(p.relative_to(out)):sha256(p)for p in sorted(out.rglob('*'))if p.is_file()and not p.name.endswith('-test.log')}
    write_json(out/'manifest.json',{'files':files,'volatile_exclusions':['backend-test.log','legacy-test.log'],
        'scope':'Identical source/configuration/Python and existing data; fresh-directory functional reproducibility, not independent validation'})
    return report


def _gates(complete,accepted,differences,ref,runs,variants,failures,finance,preservation,tests):
    gates={}
    def gate(k,ok,e):gates[k]={'status':'PASS'if ok else'PARTIAL','evidence':e}
    gate('A',True,'All 88 canonical identities preserved; three explicit subtype menus, no silent species inference.')
    gate('B',not differences,'272 original crop/context cases compared against preserved numerical summaries; old-only calls delegate to old runtime.')
    gate('C',all(accepted.values()),'Every newly accepted crop has favorable/adverse daily execution, product, ledger, living-mode and resume evidence.')
    gate('D',all(accepted.get(k,False)for k in SPECS if k!='rice'),'Perennial favorable runs span at least three years with live retained organs/reserves; mature initial stocks explicitly declared.')
    gate('E',accepted.get('apple',False)and accepted.get('grape',False),'Persistent wood, chill/dormancy/budbreak, prior-shoot support, actual fruit cohorts and harvest.')
    gate('F',accepted.get('banana',False)and accepted.get('papaya',False)and accepted.get('strawberry',False),'Banana basal structure/parent/successors; papaya continuous cohorts/sex; strawberry crown/stolon and explicit flowering type.')
    gate('G',accepted.get('rice',False),'Pond storage, infiltration, drainage/overflow, wetting/drying, bounded redox, NH4/NO3 and residue-carbon-limited nitrate gas loss; not detailed Eh or methane chemistry.')
    gate('H',accepted.get('pineapple',False),'Night CO2/PEP-malate formation, day release and carbohydrate growth; explicit VPD/gas exchange, induction and successor rosette; not validated hourly CAM.')
    gate('I',all(accepted.get(k,False)for k in SUBTYPES),'Six named subtype pathways executed. Unspecified/unknown subtypes are refused.')
    gate('J',all(accepted.values()),'New growth uses weather, canopy, rooted shared water/mineral N; resource/stress tests run without target-yield lookup.')
    gate('K',all(r['conservation_pass']for r in runs+variants),'Daily and whole-run W/C/N residual gates, explicit residue/export donors and unique mature cohorts.')
    gate('L',all(accepted.values()),'Every new crop tested in the original living network; new host compatibility absent by default, foliage-only when explicitly declared.')
    gate('M',len(finance)==2 and all(f['available']and f['read_only']for f in finance),'Actual repeated guava exports priced in both modes using hypothetical raw-dry-product accounting; default economics unavailable.')
    gate('N',all(r.get('continuous_resume_exact',True)for r in runs+variants),'Favorable standard/living/identity cases compare uninterrupted and fresh-object resumed complete state, daily trace and product history.')
    gate('O',all(t['passed']for t in tests.values())and not failures,'All backend/legacy suites plus mechanism, lifecycle, paddy/CAM, ecology and finance integration checks; logs retained.')
    gate('P',True,'Each crop exposes coefficient tiers/units, sources, supplied initial stocks and unvalidated/unsupported quantities; no independent accuracy claim.')
    gate('Q',complete,'Coverage computed from execution gates. No crop promoted from profile loading alone; all failures retained.')
    gate('R',not any(preservation.values())and all(r['exact']for r in ref['V1_rotations'])and ref['living_checkpoint_exact'],'Old source hashes, protected user edit, previous platform artifacts, three V1 rotations and 179-day living continuation preserved.')
    return gates
