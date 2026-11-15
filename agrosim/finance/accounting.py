"""Deterministic product inventory, accrual margins and dated cash accounting.

All values are conditional input scenarios. No crop equation or observed quote
is modified. No terminal stock is recognized as an immediate cash sale.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import math

from .schema import Product,Price,Farm,CostItem,MarketAccess
from .units import ACRE_HA,finite,saleable,fx
from .market import usable_price


def reconcile_costs(reference,overrides=(),extras=()):
    """Explicit item precedence, with aggregate overlap protected in each ledger."""
    active=list(reference);excluded=[]
    for replacement in overrides:
        if replacement.origin not in ['farmer_supplied','planning_assumption','synthetic_fixture']:raise ValueError('Explicit override origin required')
        removed=[]
        for existing in active:
            overlap=set(existing.covers)&set(replacement.covers)
            if overlap:
                if not set(existing.covers)<=set(replacement.covers):raise ValueError('Partial override of an aggregate cost needs a documented item breakdown')
                removed.append(existing)
        for old in removed:
            active.remove(old);excluded.append({'item':asdict(old),'reason':'explicit_field_override','replaced_by':replacement.identifier})
        active.append(replacement)
    active+=list(extras)
    ids={x.identifier:x for x in active}
    if len(ids)!=len(active):raise ValueError('Duplicate cost item identifiers')
    selected=[]
    for x in active:
        if x.included_in:
            if x.included_in not in ids or not set(x.covers)<=set(ids[x.included_in].covers):raise ValueError('Included cost needs matching parent coverage')
            excluded.append({'item':asdict(x),'reason':'already_included_in_budget_total'});continue
        for y in selected:
            if set(x.covers)&set(y.covers) and ((x.economic and y.economic)or(x.cash==y.cash=='cash')):
                raise ValueError('Overlapping expense coverage would double count: '+x.identifier+' / '+y.identifier)
        selected.append(x)
    return selected,excluded


def present_value(amount,when,start,rate):
    rate=finite(rate,'discount rate',0,1)
    return amount/(1+rate)**((date.fromisoformat(when)-date.fromisoformat(start)).days/365.25)


def cost_value(item,farm,*,sold_kg,irrigation_m3,kg_days,distance_km=None,fx_rate=None,as_of=None):
    basis={'per_ha':farm.hectares,'per_acre':farm.hectares/ACRE_HA,'total':1.,'per_kg_sold':sold_kg,
           'per_m3_irrigation':irrigation_m3,'per_kg_stored_day':kg_days}
    if item.unit=='per_tonne_km':
        if distance_km is None:raise ValueError('Freight cost requires explicit supported distance')
        basis[item.unit]=sold_kg/1000*finite(distance_km,'distance')
    return fx(item.amount*item.quantity*basis[item.unit],item.currency,farm.currency,rate=fx_rate,as_of=as_of)


def evaluate_rotation(*,farm,products,harvests,prices,sales,markets,costs,scenario,decision_date,start,end,
                      irrigation_by_year=None,storage=None,opening_cash=None,cash_adjustments=(),discount_rate=None,
                      allow_regional_proxy=False,allow_stale=False,fx_rate=None,constraints=None,yield_origin=None,
                      multiple_harvests=False,costs_by_lot=None,irrigation_by_lot=None):
    if yield_origin not in ['agrosim_stable','agrosim_living','manual_yield_not_simulated','synthetic_fixture']:
        raise ValueError('Explicit agronomic or manual yield-support classification required')
    if discount_rate is None:raise ValueError('Declared discount rate required')
    finite(discount_rate,'discount rate',0,1)
    if start>end:raise ValueError('Invalid financial horizon')
    scenario=deepcopy(scenario);constraints=constraints or {};irrigation_by_year=irrigation_by_year or {}
    for key in ['price_multiplier','cost_multiplier','marketability_multiplier']:finite(scenario[key],key,0,5)
    if not scenario.get('source')or scenario.get('probability')is not None:raise ValueError('Explicit hypothetical scenario provenance; probabilities not inferred')
    product_by_crop={p.crop:p for p in products};product_by_id={p.identifier:p for p in products}
    price_by_id={p.identifier:p for p in prices};market_by_id={m.identifier:m for m in markets}
    if len(product_by_crop)!=len(products)or len(price_by_id)!=len(prices)or len(market_by_id)!=len(markets):raise ValueError('Unique product, quote and market identities required')
    rows=sorted(deepcopy(harvests),key=lambda x:x['harvested']);lots={};records={};problems=[];warnings=[];events=[];cash=[];economic=[];transactions=[]
    if not rows:raise ValueError('At least one explicitly supported harvest required')
    years=set();last_harvest=None
    last_establishment=None
    if not multiple_harvests and (costs_by_lot is not None or irrigation_by_lot is not None):
        raise ValueError('Lot accounts require explicit multiple-harvest mode')
    if multiple_harvests and costs:
        raise ValueError('Multiple harvests require explicit lot cost accounts; annual costs cannot be repeated')
    storage=storage or {'initial_fraction':0.,'capacity_kg':0.,'daily_loss_fraction':0.,'source':None}
    fraction=finite(storage['initial_fraction'],'initial stored fraction',0,1);daily_loss=finite(storage['daily_loss_fraction'],'storage loss/day',0,.999)
    capacity=storage.get('capacity_kg')
    if fraction and(not storage.get('source')or capacity is None):raise ValueError('Storage needs declared capacity, losses and provenance')
    if capacity is not None:finite(capacity,'storage capacity')
    for h in rows:
        if h['crop']not in product_by_crop:raise ValueError('No compatible saleable product for harvest')
        when=date.fromisoformat(h['harvested']);year=when.year
        if year in years and not multiple_harvests:raise ValueError('First finance rotation adapter requires at most one harvest per calendar year')
        years.add(year)
        if not start<=str(when)<=end:raise ValueError('Harvest outside matched financial horizon')
        establishment=(h['crop'],h['planted'])
        if multiple_harvests and date.fromisoformat(h['planted'])>when:raise ValueError('Planting follows harvest')
        if last_harvest and h['planted']<=last_harvest and (not multiple_harvests or establishment!=last_establishment):raise ValueError('Duplicate or overlapping harvested crop sequence')
        last_establishment=establishment
        last_harvest=h['harvested'];p=product_by_crop[h['crop']]
        if p.harvest_field not in h:raise ValueError('Missing explicit grain dry harvest; total biomass/residue cannot substitute')
        identity=f'{h["crop"]}:{h["planted"]}:{h["harvested"]}'
        if multiple_harvests:
            identity=h.get('lot_id',identity)
            if not isinstance(identity,str)or not identity or identity in lots:raise ValueError('Unique nonempty harvest lot identities required')
        recovery=p.marketed_fraction*scenario['marketability_multiplier']
        if recovery>1:raise ValueError('Marketability scenario exceeds harvested material')
        dry=finite(h[p.harvest_field],'dry harvest')*farm.hectares
        quantity=saleable(dry,recovery,p.sale_moisture)
        lots[identity]={'id':identity,'product_id':p.identifier,'crop':h['crop'],'year':year,'harvested':h['harvested'],
            'produced_kg':quantity,'sold_kg':0.,'stored_kg':0.,'unsold_kg':0.,'lost_kg':0.,'kg_days':0.,'last_storage_update':h['harvested']}
        records[identity]={'lot_id':identity,'crop':h['crop'],'year':year,'grain_dry_kg_ha':h[p.harvest_field],
            'total_grain_dry_kg':dry,'marketed_fraction':recovery,'sale_moisture':p.sale_moisture,'produced_marketable_kg':quantity,
            'sales_revenue':0.,'sales_price_available':True,'cost_lines':[],'irrigation_m3':(irrigation_by_lot or {}).get(identity,0.)if multiple_harvests else irrigation_by_year.get(year,0.),'break_even':None}
        events.append((h['harvested'],0,'harvest',identity))
    if multiple_harvests:
        if costs_by_lot is None or set(costs_by_lot)!=set(lots):raise ValueError('Every harvest lot requires an explicit cost account')
        supplied=irrigation_by_lot or {}
        if set(supplied)-set(lots):raise ValueError('Irrigation attributed to unknown harvest lot')
        for value in supplied.values():finite(value,'lot irrigation')
        allocated={y:sum(records[k]['irrigation_m3']for k in records if records[k]['year']==y)for y in years}
        if any(abs(allocated.get(y,0.)-irrigation_by_year.get(y,0.))>1e-8 for y in set(allocated)|set(irrigation_by_year)):
            raise ValueError('Lot irrigation must reconcile with annual physical water use')
    if len({(x.get('crop'),x.get('harvested'))for x in rows})!=len(rows):raise ValueError('Duplicate harvest would double count production')
    for i,s in enumerate(sales):
        if s.get('lot_id')is not None and (not multiple_harvests or s['lot_id']not in lots):raise ValueError('Sale selects unknown harvest lot')
        if not start<=s['date']<=end:raise ValueError('Sale outside horizon; represent later transactions explicitly in a longer financial horizon')
        if not s.get('source'):raise ValueError('Sale plan provenance required')
        finite(s['fraction'],'sale fraction',0,1)
        if s.get('max_kg')is not None:finite(s['max_kg'],'sale max kg')
        events.append((s['date'],1,'sale',i))
    if len({json_key for json_key in [s.get('identifier',str(i))for i,s in enumerate(sales)]})!=len(sales):raise ValueError('Duplicate sale identities')
    events.append((end,2,'close',None));used_market={};lot_revenues={k:[] for k in lots};logistics={k:[] for k in lots}
    def update_storage(when):
        for lot in lots.values():
            dt=max(0,(date.fromisoformat(when)-date.fromisoformat(lot['last_storage_update'])).days)
            if not lot['stored_kg']or not dt:continue
            old=lot['stored_kg'];rate=-math.log1p(-daily_loss) if daily_loss else 0.
            loss=old*(-math.expm1(-rate*dt)) if rate else 0.
            lot['kg_days']+=loss/rate if rate else old*dt
            lot['stored_kg']-=loss;lot['lost_kg']+=loss;lot['last_storage_update']=when
    for when,_,kind,index in sorted(events):
        update_storage(when)
        if kind=='harvest':
            lot=lots[index];lot['stored_kg']=lot['produced_kg']*fraction;lot['unsold_kg']=lot['produced_kg']-lot['stored_kg']
            if sum(x['stored_kg']for x in lots.values())>(capacity or 0)+1e-8:problems.append('storage_capacity_exceeded')
            continue
        if kind=='close':continue
        sale=sales[index];eligible=[x for x in lots.values() if x['harvested']<=when and x['product_id']==sale['product_id'] and (sale.get('harvest_year')is None or x['year']==sale['harvest_year'])and(sale.get('lot_id')is None or x['id']==sale['lot_id'])]
        if not eligible:problems.append('sale_has_no_compatible_harvest');continue
        market=market_by_id.get(sale.get('market_id'));price=price_by_id.get(sale.get('price_id'))
        access_ok=bool(market and market.product_id==sale['product_id'] and market.delivery_start<=when<=market.delivery_end)
        if access_ok:
            access_ok=market.status!='unknown'or sale.get('allow_assumed_access')is True
            p=product_by_id[sale['product_id']];access_ok &= market.grade==p.grade and market.commodity_class==p.commodity_class
        if not market or not access_ok:
            problems.append('unavailable_or_incompatible_market_access')
            for lot in eligible:records[lot['id']]['sales_price_available']=False
            continue
        if not price:
            problems.append('missing_price')
            for lot in eligible:records[lot['id']]['sales_price_available']=False
            continue
        p=product_by_id[sale['product_id']]
        use=usable_price(price,p,farm,decision_date,allow_regional_proxy=allow_regional_proxy,allow_stale=allow_stale,
                         adjustments=sale.get('price_adjustments'),fx_rate=fx_rate)
        warnings+=use['warnings']
        if not use['available']:
            problems+=use['problems']
            for lot in eligible:records[lot['id']]['sales_price_available']=False
            continue
        if market.status!='confirmed':warnings.append('sales_conditional_on_unconfirmed_access')
        netprice=use['price_per_kg']*scenario['price_multiplier']
        fee_per_kg=0.
        if price.fees:
            if not price.fees.get('source')or not price.fees.get('currency'):raise ValueError('Fee source and currency required')
            fee_per_kg=fx(finite(price.fees['per_kg'],'price fee'),price.fees['currency'],farm.currency,rate=fx_rate,as_of=decision_date)
        covered=sale.get('price_adjustments',{}).get('covers',[])
        if market.distance_km is not None and 'freight'in covered:raise ValueError('Freight already included in price netback')
        if netprice<0:problems.append('negative_net_price');continue
        available=sum(x['unsold_kg']+x['stored_kg'] if when==x['harvested'] else x['stored_kg']for x in eligible)
        limit=available*sale['fraction']
        if sale.get('max_kg')is not None:limit=min(limit,sale['max_kg'])
        if market.max_kg is not None:limit=min(limit,max(0,market.max_kg-used_market.get(market.identifier,0.)))
        if sale.get('required_min_kg')is not None and limit<sale['required_min_kg']:problems.append('contract_minimum_not_met')
        for lot in sorted(eligible,key=lambda x:x['harvested']):
            pool=lot['unsold_kg']+lot['stored_kg'] if when==lot['harvested'] else lot['stored_kg'];q=min(pool,limit)
            if q<=0:continue
            from_unsold=min(q,lot['unsold_kg']) if when==lot['harvested'] else 0.
            lot['unsold_kg']-=from_unsold;lot['stored_kg']-=q-from_unsold;lot['sold_kg']+=q;limit-=q
            used_market[market.identifier]=used_market.get(market.identifier,0.)+q
            revenue=q*netprice;rec=records[lot['id']];rec['sales_revenue']+=revenue
            record={'date':when,'lot_id':lot['id'],'product_id':p.identifier,'quantity_kg':q,'price_per_kg':netprice,'amount':revenue,
                    'quote_id':price.identifier,'market_id':market.identifier,'market_status':market.status,'sale_is_observed':False,
                    'origin':sale['source'],'source_quote_type':price.quote_type,'scenario':scenario['id']}
            transactions.append(record);lot_revenues[lot['id']].append(record)
            cash.append({'date':when,'amount':revenue,'kind':'scenario_sales_receipt','lot_id':lot['id']})
            economic.append({'date':when,'amount':revenue,'kind':'sales','lot_id':lot['id']})
            if market.distance_km is not None:
                freight=fx(q/1000*market.distance_km*market.freight_per_tonne_km,market.freight_currency,farm.currency,rate=fx_rate,as_of=decision_date)
                logistics[lot['id']].append((when,freight,market.source,'freight'))
            if fee_per_kg:logistics[lot['id']].append((when,q*fee_per_kg,price.fees['source'],'marketing_fee'))
    cost_lines=[];cash_complete=True;costs_complete=True
    for identity,rec in records.items():
        lot=lots[identity];items=costs_by_lot[identity]if multiple_harvests else costs.get(rec['year'])
        if not items:
            problems.append('missing_cost_account');items=[];costs_complete=False;cash_complete=False
        account_available=bool(items)
        items,_=reconcile_costs(items)
        for item in items:
            if item.crop!=rec['crop']:raise ValueError('Cost attributed to wrong crop/year')
            available=item.source.get('published_at')or item.source.get('available_at')
            if not available or available>decision_date:
                problems.append('cost_unavailable_at_decision');account_available=False;costs_complete=False;cash_complete=False
                continue
            if not item.expense_date:raise ValueError('Explicit cost-incurrence timing required')
            if not start<=item.expense_date<=end:raise ValueError('Expense outside financial horizon')
            if item.payment_date and item.payment_date<start:raise ValueError('Payments before horizon require an explicit opening obligation account')
            if date.fromisoformat(item.expense_date).year!=rec['year']:raise ValueError('Expense belongs to a different crop year')
            if set(item.covers)&{x[3]for x in logistics[identity]}:raise ValueError('Marketing/logistics expense is already applied')
            amount=cost_value(item,farm,sold_kg=lot['sold_kg'],irrigation_m3=rec['irrigation_m3'],kg_days=lot['kg_days'],fx_rate=fx_rate,as_of=decision_date)*scenario['cost_multiplier']
            line={**asdict(item),'lot_id':identity,'year':rec['year'],'amount_evaluated':amount,'evaluated_currency':farm.currency,
                  'unit_price':item.amount,'source_quantity':item.quantity,'cost_scenario_multiplier':scenario['cost_multiplier']}
            rec['cost_lines'].append(line);cost_lines.append(line)
            if item.economic:economic.append({'date':item.expense_date,'amount':-amount,'kind':'economic_expense','lot_id':identity})
            if item.cash=='unknown'and amount>0:cash_complete=False
            elif item.cash=='cash':
                if not item.payment_date:cash_complete=False
                else:cash.append({'date':item.payment_date,'amount':-amount,'kind':'expense_payment','lot_id':identity})
        for when,amount,source,category in logistics[identity]:
            amount*=scenario['cost_multiplier']
            line={'identifier':'sale_'+category,'category':category,'accounting':'marketing','economic':True,'cash':'cash',
                  'amount_evaluated':amount,'evaluated_currency':farm.currency,'year':rec['year'],'lot_id':identity,'source':source,'origin':'planning_assumption'}
            rec['cost_lines'].append(line);cost_lines.append(line)
            cash.append({'date':when,'amount':-amount,'kind':'freight_payment','lot_id':identity});economic.append({'date':when,'amount':-amount,'kind':'freight','lot_id':identity})
        economic_items=[x for x in rec['cost_lines']if x['economic']]
        operating=sum(x['amount_evaluated']for x in economic_items if x['accounting']in ['variable','operating'])
        variable=sum(x['amount_evaluated']for x in economic_items if x['accounting']in ['variable','marketing'])
        total=sum(x['amount_evaluated']for x in economic_items);revenue=rec['sales_revenue'] if rec['sales_price_available'] else None
        full_supported=account_available and revenue is not None
        perkg_cost_slope=sum(x['amount_evaluated']/lot['sold_kg'] for x in economic_items if x.get('unit')=='per_kg_sold' and lot['sold_kg']>0)
        if logistics[identity]and lot['sold_kg']>0:perkg_cost_slope+=sum(a for _,a,_,_ in logistics[identity])*scenario['cost_multiplier']/lot['sold_kg']
        fixed_equiv=total-perkg_cost_slope*lot['sold_kg']
        realized_price=rec['sales_revenue']/lot['sold_kg'] if lot['sold_kg']>0 and revenue is not None else None
        effective_margin=realized_price-perkg_cost_slope if realized_price is not None else None
        rec.update(sold_kg=lot['sold_kg'],stored_kg=lot['stored_kg'],unsold_kg=lot['unsold_kg'],storage_loss_kg=lot['lost_kg'],
            revenue=revenue,total_variable_costs=variable if account_available else None,
            contribution_margin=revenue-variable if revenue is not None and account_available else None,
            total_operating_costs=operating if account_available else None,
            return_above_operating_costs=revenue-operating if full_supported else None,
            full_economic_costs=total if account_available else None,return_above_full_economic_costs=revenue-total if full_supported else None,
            break_even={'selling_price_per_kg':total/lot['sold_kg'] if full_supported and lot['sold_kg']>0 else None,
                'saleable_sold_kg':fixed_equiv/effective_margin if full_supported and effective_margin is not None and effective_margin>0 else None,
                'saleable_sold_kg_per_ha':fixed_equiv/effective_margin/farm.hectares if full_supported and effective_margin is not None and effective_margin>0 else None,
                'scope':'Local algebra holding fixed/scenario expenses and unit-sales expense slope constant; not a crop yield forecast. Storage/capacity changes require rerun.'})
        if abs(lot['produced_kg']-lot['sold_kg']-lot['stored_kg']-lot['unsold_kg']-lot['lost_kg'])>1e-7:raise ArithmeticError('Saleable product inventory failed')
    for adj in cash_adjustments:
        if adj['kind']not in ['loan_draw','loan_principal','capital_contribution','owner_withdrawal']:raise ValueError('Subsidy, insurance or other transfer requires a separate documented eligibility implementation')
        if not adj.get('source'):raise ValueError('Financial obligation provenance required')
        amount=finite(adj['amount'],'financial cash adjustment');date.fromisoformat(adj['date'])
        if adj['date']<start:raise ValueError('Cash adjustment precedes opening balance')
        cash.append({'date':adj['date'],'amount':amount if adj['kind']in ['loan_draw','capital_contribution']else -amount,'kind':adj['kind'],'source':adj['source']})
    annual=list(records.values());complete=not problems and costs_complete and all(r['revenue']is not None for r in annual)
    cash=sorted(cash,key=lambda x:(x['date'],x['kind']));by_period={}
    for line in cash:by_period[line['date']]=by_period.get(line['date'],0.)+line['amount']
    running=finite(opening_cash,'opening cash') if opening_cash is not None else None;balances=[]
    for when,change in sorted(by_period.items()):
        if running is not None and cash_complete and start<=when<=end:running+=change
        balances.append({'date':when,'known_cash_change':change,'cash_change':change if cash_complete else None,
                         'closing_cash':running if cash_complete and start<=when<=end else None,'within_horizon':start<=when<=end})
    revenue=sum(r['revenue'] for r in annual) if all(r['revenue']is not None for r in annual) else None
    total_cost=sum(r['full_economic_costs'] for r in annual) if costs_complete else None
    economic_npv=sum(present_value(x['amount'],x['date'],start,discount_rate)for x in economic) if complete else None
    full_net=revenue-total_cost if complete else None
    # Hard constraints are evaluated on total, area-scaled quantities.
    violations=[]
    economic_crop_cost=sum(x['amount_evaluated']for x in cost_lines if x['economic'])
    if constraints.get('max_total_economic_cost')is not None and economic_crop_cost>constraints['max_total_economic_cost']:violations.append('economic_budget_exceeded')
    if constraints.get('max_irrigation_m3')is not None and sum(irrigation_by_year.values())>constraints['max_irrigation_m3']:violations.append('irrigation_limit_exceeded')
    if constraints.get('allowed_crops')is not None and any(r['crop']not in constraints['allowed_crops']for r in annual):violations.append('crop_not_allowed')
    required=constraints.get('required_equipment_by_crop',{})
    if any(set(required.get(r['crop'],[]))-set(farm.equipment)for r in annual):violations.append('equipment_unavailable')
    if sum(irrigation_by_year.values())>0 and not farm.irrigation_available:violations.append('irrigation_unavailable')
    if constraints.get('require_confirmed_market')and any(x['market_status']!='confirmed'for x in transactions):violations.append('confirmed_market_required')
    if constraints.get('require_confirmed_market')and (not transactions or any(market_by_id.get(s.get('market_id')) is None or market_by_id[s['market_id']].status!='confirmed' for s in sales)):
        violations.append('confirmed_market_required')
    if constraints.get('min_return')is not None:
        if full_net is None:violations.append('minimum_return_unverifiable')
        elif full_net<constraints['min_return']:violations.append('return_below_minimum')
    if constraints.get('max_cash_exposure')is not None:
        if not cash_complete or opening_cash is None:violations.append('cash_exposure_unverifiable')
        elif max([max(0,-x['closing_cash'])for x in balances if x['closing_cash']is not None]or[0])>constraints['max_cash_exposure']:violations.append('cash_exposure_exceeded')
    return {'status':'evaluated_conditional_scenario'if complete else 'partially_evaluated','yield_support':yield_origin,
        'currency':farm.currency,'reference_year':farm.reference_year,'area_ha':farm.hectares,'start':start,'end':end,'discount_rate':discount_rate,
        'scenario':scenario,'annual_crop_finances':annual,'inventory':list(lots.values()),'sales_transactions':transactions,'cost_breakdown':cost_lines,
        'gross_sales_revenue':revenue,'full_economic_costs':total_cost,'return_above_full_economic_costs':full_net,
        'total_variable_costs':sum(r['total_variable_costs']for r in annual)if costs_complete else None,
        'contribution_margin':sum(r['contribution_margin']for r in annual)if complete else None,
        'total_operating_costs':sum(r['total_operating_costs']for r in annual)if costs_complete else None,
        'return_above_operating_costs':sum(r['return_above_operating_costs']for r in annual)if complete else None,
        'net_present_value':economic_npv,'npv_scope':'Dated modeled sales minus incurred economic expenses; unvalued stocks are not cash receipts',
        'cost_scope':'Full economic costs within supplied line-item coverage, not a verified complete farm enterprise account',
        'net_return_per_ha':full_net/farm.hectares if full_net is not None else None,
        'net_return_per_acre':full_net/farm.hectares*ACRE_HA if full_net is not None else None,
        'cash_flows':cash,'cash_balance_by_period':balances,'cash_flow_complete':cash_complete and complete,
        'known_net_cash_flow':sum(x['amount']for x in cash if start<=x['date']<=end),
        'net_cash_flow':sum(x['amount']for x in cash if start<=x['date']<=end)if cash_complete and complete else None,
        'cash_npv':sum(present_value(x['amount'],x['date'],start,discount_rate)for x in cash if start<=x['date']<=end)if cash_complete and complete else None,
        'future_cash_obligations':[x for x in cash if x['date']>end],
        'irrigation_m3':sum(irrigation_by_year.values()),
        'irrigation_cost':sum(x['amount_evaluated']for x in cost_lines if x.get('unit')=='per_m3_irrigation'),
        'return_per_irrigation_cost':full_net/sum(x['amount_evaluated']for x in cost_lines if x.get('unit')=='per_m3_irrigation') if full_net is not None and sum(x['amount_evaluated']for x in cost_lines if x.get('unit')=='per_m3_irrigation')>0 else None,
        'return_per_irrigation_m3':full_net/sum(irrigation_by_year.values())if full_net is not None and sum(irrigation_by_year.values())>0 else None,
        'problems':sorted(set(problems)),'warnings':sorted(set(warnings)),
        'hard_constraint_violations':sorted(set(violations)),
        'conditional_ranking_eligible':complete and not violations and yield_origin not in ['manual_yield_not_simulated','synthetic_fixture'],
        'farmer_recommendation':None,'loss_probability':None,'government_payments_included':False,
        'inventory_valuation_in_sales':False,'input_harvests_modified':False}
