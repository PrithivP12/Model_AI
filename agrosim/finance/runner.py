"""Offline optional financial evaluation of preserved actual AgroSim outputs."""
from copy import deepcopy
from dataclasses import asdict,replace
from datetime import date
from itertools import combinations
from pathlib import Path
import csv,json,hashlib

from ..io import load_config,resolve,sha256,write_json
from ..crop_catalog import CropRegistry,CropChoiceRegistry
from .schema import Farm,Product,Price,CostItem,MarketAccess
from .units import MOISTURE_SOURCE,UNIT_SOURCE
from .market import history
from .accounting import evaluate_rotation,reconcile_costs


def price_override(base,updates):
    allowed={'amount','currency','unit','grade','commodity_class','quote_type','observation_date','publication_date','price_basis','market_location','state','county','source','origin'}
    if set(updates)-allowed or not {'origin','source','observation_date','publication_date'}<=updates.keys():raise ValueError('Price override requires explicit origin, source and observed/available dates')
    if updates['origin']not in ['farmer_supplied','planning_assumption','synthetic_fixture']:raise ValueError('Override is not a public market observation')
    values={**asdict(base),**updates,'identifier':'override-'+base.identifier}
    values['quote_type']=updates.get('quote_type','assumed_sales')
    values['source']={'override':updates['source'],'reference_quote':base.source,'overridden_fields':sorted(updates)}
    return Price(**values)


def read_agronomy(spec,base,farm):
    path=resolve(base,spec['path']);payload=json.loads(path.read_text());kind=spec['format'];runs=[]
    if kind=='stable_v1':
        if payload.get('product')!='AgroSim Ecosystem Evaluator V1':
            # Verify schema/shape instead of accepting arbitrary total biomass.
            if 'physical_configuration'not in payload or 'source_provenance'not in payload:raise ValueError('Expected actual V1 ecosystem report')
        area=payload['area_ha']
        for r in payload['runs']:
            net={}
            for period in r['periods']:
                y=date.fromisoformat(period['end']).year;net[y]=net.get(y,0.)+period['ledger']['water']['inputs']['irrigation_mm']
            total=sum(net.values())
            irrigation={y:r['resources']['irrigation_m3']*v/total*farm.hectares/area if total else 0. for y,v in net.items()}
            initial=r['initial_state'];final=r['final_state']
            runs.append({'id':r['run_id'],'strategy':r['strategy_id'],'environment':r['scenario_id'],'weather_sha256':r['weather_sha256'],
                'harvests':r['harvests'],'irrigation':irrigation,'yield_origin':'agrosim_stable',
                'ecology':{'initial_humus_C_kg_ha':sum(initial['soil']['humus_c_kg_ha']),
                    'final_humus_C_kg_ha':sum(final['soil']['humus_c_kg_ha']),
                    'initial_mineral_N_kg_ha':sum(initial['soil']['mineral_n_kg_ha']),
                    'final_mineral_N_kg_ha':sum(final['soil']['mineral_n_kg_ha']),
                    'final_residue_C_kg_ha':sum(final['soil']['residue_c_kg_ha'])+final['soil']['surface_residue_c_kg_ha'],
                    'role':'Existing physical quantities beside finance; no sustainability score'}})
        climate=payload['source_provenance']
    elif kind=='living':
        scenario=json.loads((path.parent/'scenario_configuration.json').read_text());eff=scenario['irrigation_efficiency']
        if not 0<eff<=1:raise ValueError('Invalid preserved irrigation efficiency')
        for row in payload['runs']:
            if not row['label'].startswith('full:'):continue
            r=json.loads((path.parent/row['id']/'state.json').read_text());irrigation={};previous=0.
            for annual in r['annual_states']:
                value=annual['state']['ecosystem']['cumulative_fluxes'].get('irrigation_mm',0.)
                irrigation[annual['year']]=(value-previous)*10*farm.hectares/eff;previous=value
            runs.append({'id':r['id'],'strategy':r['strategy'],'environment':r['label'],'weather_sha256':r['forcing_hash'],
                'harvests':r['harvests'],'irrigation':irrigation,'yield_origin':'agrosim_living',
                'ecology':{'summary':r['summary'],'scope':'Conditional on explicitly hypothetical Tier D biological dynamics'}})
        climate=json.loads((path.parent/'forcing_provenance.json').read_text())
    elif kind=='manual_yield_not_simulated':
        if not spec.get('source'):raise ValueError('Manual yield provenance required')
        for r in payload['runs']:
            runs.append({**r,'yield_origin':'manual_yield_not_simulated','ecology':None})
        climate=[]
    else:raise ValueError('Explicit stable_v1/living/manual_yield_not_simulated format required')
    if not runs:raise ValueError('No compatible agronomic runs')
    for r in runs:
        for h in r['harvests']:
            if r['yield_origin']!='manual_yield_not_simulated':CropRegistry().require_quantitative(h['crop'])
            if 'grain_dry_kg_ha'not in h:raise ValueError('No explicit grain dry harvest')
    return runs,{'path':spec['path'],'sha256':sha256(path),'mode':kind,'NASA_environmental_forcing':climate,
                 'NASA_is_a_market_price_source':False,'numerical_outputs_modified':False}


def dump_csv(path,rows):
    fields=sorted({k for r in rows for k in r})
    with path.open('w')as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for row in rows:w.writerow({k:json.dumps(v,sort_keys=True,allow_nan=False)if isinstance(v,(dict,list))else v for k,v in row.items()})


def execute(config_path,output):
    cfg,base=load_config(config_path);farm=Farm(**cfg['farm']);products=[Product(**p)for p in cfg['products']]
    scenario_ids=[s['id']for s in cfg['risk_scenarios']]
    if not scenario_ids or len(set(scenario_ids))!=len(scenario_ids)or 'reference'not in scenario_ids:raise ValueError('Unique scenario identifiers including reference required')
    if cfg['decision_date']>cfg['start']:raise ValueError('Planning decision must precede the financial horizon')
    source_path=resolve(base,cfg['economic_data']);data=json.loads(source_path.read_text())if source_path.is_file()else {'prices':[],'budgets':[],'source_acquisition':[{'status':'economic_cache_unavailable'}]}
    raw_assets=resolve(base,cfg['raw_source_directory']) if cfg.get('raw_source_directory') else None
    asset_status=[]
    for asset in data.get('assets',[]):
        raw=raw_assets/asset['file'] if raw_assets else None
        if raw is not None and raw.exists():
            if sha256(raw)!=asset['sha256']:raise ValueError('Raw economic source hash mismatch: '+asset['file'])
            asset_status.append({'file':asset['file'],'status':'hash_verified'})
        else:asset_status.append({'file':asset['file'],'status':'normalized_only_raw_not_available'})
    observed=[Price(**p)for p in data['prices']];quotes=[];quote_ids={};coverage=[]
    if not cfg.get('cost_coverage_declaration')or not cfg['cost_coverage_declaration'].get('source'):
        raise ValueError('Explicit cost-coverage scope and omitted-cost assumptions required')
    direct=[Price(**p)for p in cfg.get('direct_prices',[])]
    if len({p.product_id for p in direct})!=len(direct)or any(p.origin not in ['farmer_supplied','planning_assumption','synthetic_fixture']for p in direct):
        raise ValueError('One explicitly supplied price per product with honest origin required')
    for product in products:
        local=next((p for p in direct if p.product_id==product.identifier),None)
        if local:
            quotes.append(local);quote_ids[product.identifier]=local.identifier
            coverage.append({'product_id':product.identifier,'status':'explicit_supplied_price','origin':local.origin,'source':local.source})
            continue
        eligible=[p for p in observed if p.product_id==product.identifier and p.commodity_class==product.commodity_class and p.publication_date<=cfg['decision_date'] and p.observation_date<=cfg['decision_date']]
        if not eligible:
            coverage.append({'product_id':product.identifier,'status':'price_unavailable'});continue
        last=max(eligible,key=lambda p:(p.observation_date,p.publication_date))
        age=(date.fromisoformat(cfg['decision_date'])-date.fromisoformat(last.observation_date)).days
        if age>cfg['price_policy'].get('max_observation_age_days',120)and not cfg['price_policy']['allow_stale']:
            coverage.append({'product_id':product.identifier,'status':'latest_observation_stale','age_days':age});continue
        # Taking a historical observed price forward requires explicit adoption.
        if cfg['price_policy']['kind']!='adopt_latest_as_constant_planning_assumption' or not cfg['price_policy'].get('source'):raise ValueError('Explicit future planning-price adoption required')
        q=replace(last,identifier='planning-'+last.identifier,origin='planning_assumption',quote_type='assumed_sales',
            source={'policy':cfg['price_policy']['source'],'reference_observation':asdict(last),
                    'scope':'Adopted constant planning price; not an observed future price or Illinois farmgate quote'})
        if product.identifier in cfg.get('price_overrides',{}):q=price_override(q,cfg['price_overrides'][product.identifier])
        quotes.append(q);quote_ids[product.identifier]=q.identifier
        coverage.append({'product_id':product.identifier,'observations':len(eligible),'latest':last.observation_date,
                         'source_state':last.state,'farm_state':farm.state,'grade':last.grade,'price_policy':'assumed_future_sale','local_buyer_confirmed':False})
    runs,agro_provenance=read_agronomy(cfg['agronomy'],base,farm)
    markets=[MarketAccess(**m)for m in cfg['markets']];market_ids={m.product_id:m.identifier for m in markets}
    budget_catalog=data['budgets'];by_crop={p.crop:p for p in products};finance=[];excluded=[];reconciliation=[]
    for run in runs:
        costs={};previous=cfg.get('initial_previous_crop');sales=[]
        for h in run['harvests']:
            year=date.fromisoformat(h['harvested']).year;crop=h['crop'];pid=by_crop[crop].identifier
            candidates=[b for b in budget_catalog if b['crop']==crop and b['source']['published_at']<=cfg['decision_date']]
            matched=[b for b in candidates if b['previous_crop']==previous and b['previous_crop']is not None]
            generic=[b for b in candidates if b['previous_crop']is None]
            budget=(matched or generic or [None])[0]
            rawitems=[CostItem(**x)for x in budget['items']]if budget else []
            if budget:reconciliation.append({'run':run['id'],'year':year,'crop':crop,'budget_id':budget['id'],'preceding_crop':previous,
                                            'exact_sequence_budget':bool(matched),'source_reconciliation':budget['reconciliation']})
            overridden=[CostItem(**x)for x in cfg.get('cost_overrides',[])if x['crop']==crop]
            extra=[CostItem(**x)for x in cfg.get('additional_costs',[])if x['crop']==crop]
            selected,replaced=reconcile_costs(rawitems,overridden,extra);excluded.extend({'run':run['id'],'year':year,**x}for x in replaced)
            normalized=[]
            for item in selected:
                # Dates/cash interpretations are explicit configured scenario assumptions.
                timing=cfg['cost_policy']['timing_by_accounting'][item.accounting]
                incurred=h['planted']if timing=='planting'else h['harvested']if timing=='harvest'else f'{year}-12-31'
                cash=cfg['cost_policy'].get('cash_interpretation_overrides',{}).get(item.category,item.cash)
                source={**item.source,'adoption':cfg['cost_policy']['source'],'source_amount_is_actual_farm_expense':False,
                        'incurrence_timing_assumption':timing,'payment_assumption':'same day as incurrence when classified cash',
                        'benchmark_held_constant_across_future_years':True}
                normalized.append(replace(item,expense_date=item.expense_date or incurred,
                    payment_date=item.payment_date or incurred,cash=cash,source=source))
            costs[year]=normalized if budget or overridden else None
            sales.append({'date':h['harvested'],'product_id':pid,'harvest_year':year,'fraction':cfg['sales_policy']['sale_fraction'],
                          'price_id':quote_ids.get(pid),'market_id':market_ids.get(pid),'source':cfg['sales_policy']['source'],
                          'allow_assumed_access':cfg['sales_policy']['allow_assumed_access']})
            previous=crop
        for scenario in cfg['risk_scenarios']:
            result=evaluate_rotation(farm=farm,products=products,harvests=run['harvests'],prices=quotes,sales=sales,markets=markets,
                costs=costs,scenario=scenario,decision_date=cfg['decision_date'],start=cfg['start'],end=cfg['end'],
                irrigation_by_year=run['irrigation'],storage=cfg['storage'],opening_cash=cfg.get('opening_cash'),
                cash_adjustments=cfg.get('cash_adjustments',[]),discount_rate=cfg['discount_rate'],
                allow_regional_proxy=cfg['price_policy']['allow_regional_proxy'],allow_stale=cfg['price_policy']['allow_stale'],
                fx_rate=cfg.get('fx_rate'),constraints=cfg.get('constraints'),yield_origin=run['yield_origin'])
            result.update(run_id=run['id'],strategy=run['strategy'],environment=run['environment'],weather_sha256=run['weather_sha256'],ecology=run['ecology'])
            finance.append(result)
    rankings=[];contrasts=[];sensitivity=[]
    groups={}
    for row in finance:groups.setdefault((row['environment'],row['scenario']['id']),[]).append(row)
    for (environment,scenario),rows in sorted(groups.items()):
        allowed=[r for r in rows if r['conditional_ranking_eligible']]
        # Do not quietly rank a subset when a competing option cannot be evaluated.
        all_available=len(allowed)==len(rows)
        ranked=sorted(allowed,key=lambda x:(-x['net_present_value'],x['strategy']))if all_available else []
        rankings.append({'environment':environment,'scenario':scenario,'status':'conditional_benchmark_scenario_ranking'if ranked else'comparison_unavailable_or_infeasible',
            'ordered_strategies':[r['strategy']for r in ranked],'objective':'highest_dated_economic_NPV',
            'water_efficiency_order':[r['strategy']for r in sorted(allowed,key=lambda r:-(r['return_per_irrigation_m3'] if r['return_per_irrigation_m3']is not None else -float('inf')))]if all_available else[],
            'farmer_recommendation':None,'market_access_guaranteed':False})
        for a,b in combinations(rows,2):
            contrasts.append({'environment':environment,'scenario':scenario,'strategy_a':a['strategy'],'strategy_b':b['strategy'],
                'matched_weather':a['weather_sha256']==b['weather_sha256'],'matched_area':a['area_ha']==b['area_ha'],
                'net_return_a_minus_b':a['return_above_full_economic_costs']-b['return_above_full_economic_costs'] if a['return_above_full_economic_costs']is not None and b['return_above_full_economic_costs']is not None else None,
                'npv_a_minus_b':a['net_present_value']-b['net_present_value']if a['net_present_value']is not None and b['net_present_value']is not None else None})
    for row in finance:
        if row['scenario']['id']=='reference':continue
        control=next((x for x in finance if x['run_id']==row['run_id']and x['scenario']['id']=='reference'),None)
        if control:sensitivity.append({'run_id':row['run_id'],'strategy':row['strategy'],'environment':row['environment'],'scenario':row['scenario']['id'],
            'net_return_change':row['return_above_full_economic_costs']-control['return_above_full_economic_costs'] if row['return_above_full_economic_costs']is not None and control['return_above_full_economic_costs']is not None else None,
            'npv_change':row['net_present_value']-control['net_present_value']if row['net_present_value']is not None and control['net_present_value']is not None else None})
    directory=Path(output).resolve();directory.mkdir(parents=True,exist_ok=False)
    # No online refresh is hidden in financial evaluation. Preserve exact normalized inputs.
    write_json(directory/'financial_assumptions.json',cfg)
    if source_path.exists():(directory/'economic_inputs.json').write_bytes(source_path.read_bytes())
    write_json(directory/'price_provenance.json',{'sources':data.get('source_acquisition',[]),'assets':data.get('assets',[]),
        'planning_prices':[asdict(x)for x in quotes],'unit_sources':{'bushel_weight':UNIT_SOURCE,'moisture':MOISTURE_SOURCE}})
    write_json(directory/'market_access.json',[asdict(m)for m in markets])
    write_json(directory/'agronomic_provenance.json',agro_provenance)
    write_json(directory/'risk_scenarios.json',{'scenarios':cfg['risk_scenarios'],'sensitivity':sensitivity,'loss_probability':None})
    write_json(directory/'price_history.json',history(observed,cfg['decision_date']))
    write_json(directory/'cost_reconciliation.json',{'selected_budgets':reconciliation,'excluded_or_replaced':excluded,
                                                   'policy':'One complete matched/fallback benchmark per crop-year; field overrides replace covered items; never sum full ERS and farmdoc accounts'})
    coverage_report={'sources':data.get('quality',[]),'raw_source_integrity':asset_status,'price_coverage':coverage,'AMS_status':data.get('AMS_status','unavailable'),
        'critical_scenario_problems':sorted({p for r in finance for p in r['problems']}),
        'scenario_warnings':sorted({p for r in finance for p in r['warnings']}),
        'regional_cost_transfer':'Spring-wheat expense scenario adopts Northern Great Plains all-wheat account (mixed spring/winter/durum); not an Illinois spring-wheat cost estimate.',
        'publicly_verified_farm_specific_prices_or_costs':False,'farmer_or_scenario_prices_supplied':len(direct)+len(cfg.get('price_overrides',{})),
        'independent_yield_validation':False,'prices_are_future_forecasts':False,
        'support_for_catalog':[{'identifier':c['identifier'],'economic_data':'available_reference'if c['implementation']['kernel_key']in by_crop else 'unavailable_in_this_reference',
                                'simulated_yield_available':bool(c['implementation']['kernel_key'])}for c in CropRegistry().crops.values()],
        'requested_choices':CropChoiceRegistry().report()['registered_choices'],
        'input_accounting_scope':'Benchmark reference expenses and declared assumptions, not actual farm profit; future values held constant without an estimated inflation/price distribution'}
    coverage_report['cost_coverage_declaration']=cfg['cost_coverage_declaration']
    write_json(directory/'data_quality.json',coverage_report)
    report={'status':'optional_financial_engine_conditional_scenarios','farm':asdict(farm),'products':[asdict(p)for p in products],
        'source_counts':{'NASS_price_records':len(observed),'economic_benchmarks':len(budget_catalog),'AMS_price_records':0},
        'runs':finance,'rankings':rankings,'rotation_differences':contrasts,'data_quality':coverage_report,
        'discount_rate':cfg['discount_rate'],'farmer_recommendation':None,'forecast_accuracy':None,
        'yield_forecast_accuracy_improved':False,'market_demand_inferred_from_acreage':False}
    downside=[]
    for environment in sorted({r['environment']for r in finance}):
        candidates=[r for r in finance if r['environment']==environment and r['scenario']['id']=='downside']
        downside.append({'environment':environment,'objective':'highest_return_in_named_downside_scenario_not_loss_probability',
            'ordered_strategies':[r['strategy']for r in sorted(candidates,key=lambda x:-x['return_above_full_economic_costs'])]if candidates and all(r['conditional_ranking_eligible']for r in candidates)else[],
            'farmer_recommendation':None})
    report['downside_comparisons']=downside
    write_json(directory/'financial_report.json',report)
    dump_csv(directory/'market_prices.csv',[asdict(p)for p in observed+quotes])
    dump_csv(directory/'rotation_finances.csv',[{k:r[k]for k in ['run_id','strategy','environment','status','yield_support','gross_sales_revenue','full_economic_costs','return_above_full_economic_costs','net_present_value','net_return_per_ha','net_return_per_acre','cash_flow_complete','net_cash_flow','irrigation_m3','return_per_irrigation_m3','hard_constraint_violations','conditional_ranking_eligible']}|{'scenario':r['scenario']['id']}for r in finance])
    dump_csv(directory/'annual_crop_finances.csv',[{k:v for k,v in a.items()if k!='cost_lines'}|{'run_id':r['run_id'],'strategy':r['strategy'],'scenario':r['scenario']['id']}for r in finance for a in r['annual_crop_finances']])
    dump_csv(directory/'cost_breakdown.csv',[c|{'run_id':r['run_id'],'strategy':r['strategy'],'scenario':r['scenario']['id']}for r in finance for c in r['cost_breakdown']])
    write_json(directory/'break_even_analysis.json',[{'run_id':r['run_id'],'scenario':r['scenario']['id'],'crop':a['crop'],'year':a['year'],**a['break_even']}for r in finance for a in r['annual_crop_finances']])
    write_findings(report,directory)
    write_json(directory/'implementation.json',{str(p.relative_to(Path(__file__).parents[2])):sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    write_json(directory/'manifest.json',{str(p.relative_to(directory)):sha256(p)for p in sorted(directory.rglob('*'))if p.is_file()})
    if sha256(resolve(base,cfg['agronomy']['path']))!=agro_provenance['sha256']:raise ArithmeticError('Agronomic input changed during finance evaluation')
    return {'status':report['status'],'evaluations':len(finance),'conditional_rankings':sum(bool(r['ordered_strategies'])for r in rankings),
            'report':str(directory/'financial_report.json'),'farmer_recommendation':None}


def write_findings(report,directory):
    lines=['# AGROSIM GLOBAL — FINANCIAL INTELLIGENCE COMPLETION REPORT','',
       'Optional, deterministic accounting over unchanged AgroSim harvests. These are conditional benchmark financial scenarios, not verified farm profits or future-price forecasts. No farmer recommendation is issued.','',
       f'Sources:{report["source_counts"]["NASS_price_records"]} cached current-version NASS price records and{report["source_counts"]["economic_benchmarks"]} economic benchmark accounts. In the prepared reference these cover Illinois corn/soybeans, North Dakota spring wheat, ERS regions and matched Illinois farmdoc crop-sequence budgets. The AMS adapter requires credentials/report-specific mapping; no AMS prices were retrieved.','',
       'All source prices retain their quote type and geography. The demonstration explicitly adopts the latest compatible observation as a constant hypothetical future sale price, rather than treating it as an observed future/local contract. Local buyer access and actual farm expenses remain unknown. Spring-wheat costs use a declared Northern Great Plains mixed-wheat proxy; Illinois farmdoc wheat is not silently substituted.','',
       '## First matched historical-weather context, reference prices','',
       '| Strategy | Sales revenue | Full economic costs | Return above full costs | Dated economic NPV |','|---|---:|---:|---:|---:|']
    env=report['runs'][0]['environment']
    def fmt(v):return 'unavailable'if v is None else f'{v:.4f}'
    for r in report['runs']:
        if r['environment']==env and r['scenario']['id']=='reference':
            lines.append(f'| {r["strategy"]} | {fmt(r["gross_sales_revenue"])} | {fmt(r["full_economic_costs"])} | {fmt(r["return_above_full_economic_costs"])} | {fmt(r["net_present_value"])} |')
    lines+=['','All values are in the configured currency over the matched horizon and farm area; default demonstration is1ha. Negative returns are retained. Three different crop harvests are priced separately; dry mass is never used as an economic ranking. Dated NPV incorporates declared expense and sale timing. Stored or unsold grain is not counted as immediate sales revenue.','',
      '## Assumptions and costs','',
      'Dry grain is converted to sale-moisture mass using explicitly declared marketable recovery and crop-specific trade bushels (corn56lb, soybean/wheat60lb). Moisture is explicitly a scenario standard, not an observed quality test. ERS or a matched farmdoc enterprise account is selected once; source totals are not added to their components. Farmer field overrides replace their covered expense categories. First-year prior crop remains unknown and uses generic regional costs. No preceding crop is invented.','',
      'Actual irrigation-supply m³ is allocated from the preserved simulation. A declared illustrative purchased-water fee replaces any baseline purchased-water item; it does not claim measured irrigation energy costs. Depreciation/opportunity costs remain distinct from cash capital purchases. Unknown cash classifications prevent a complete cash balance. No government payments, insurance indemnities or tax credits are assumed.','',
      '## Risk and comparison','',
      'Reference/downside/upside and individual price, expense and marketability sensitivities share each physical climate history and farm area. Only marketable recovery changes in harvest sensitivity; the original biological harvest is untouched. Scenario parameters are editable, hypothetical and carry no probabilities. Rankings are conditional on complete inputs and hard constraints; incomplete competitors prevent a final ranking. Water use, carbon, nitrogen and residue states are presented beside finance without a sustainability score.','',
      '## Limits','',
      'A state producer-price average is not a local farm contract. ND spring-wheat prices are not Illinois observations; regional proxy transfer is explicitly enabled for this scenario. The cost accounts are regional/planning references, not this hypothetical field’s expenses, exact fertilizer rates or machinery fleet. Holding source-dollar amounts constant in future years is a declared planning assumption, not an inflation-adjusted forecast. No local buyers, unmet demand or haul route is invented. No subsidy eligibility is inferred.','',
      'The physical model retains its earlier yield/water/rotation validation failures. Finance on living-mode harvests is additionally conditional on hypothetical biological states. A manual yield is labeled manual_yield_not_simulated and cannot make an unsupported crop recommendation.','',
      'See FINANCE.md for schema/API/CLI, deterministic reproduction, source coverage, synthetic farmer-interface examples and complete acceptance evidence.']
    (directory/'FINANCIAL_FINDINGS.md').write_text('\n'.join(lines)+'\n')
