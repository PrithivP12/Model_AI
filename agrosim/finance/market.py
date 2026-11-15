"""Quote compatibility and descriptive history, with conservative as-of gates."""
from collections import defaultdict
from dataclasses import asdict
from datetime import date
import math
import statistics
from .schema import Price,Product,Farm
from .units import mass_kg,fx,finite


def usable_price(price,product,farm,decision_date,*,allow_regional_proxy=False,allow_stale=False,max_age_days=120,
                 adjustments=None,fx_rate=None):
    problems=[]; warnings=[];decision=date.fromisoformat(decision_date)
    if price.product_id!=product.identifier:problems.append('commodity_product_mismatch')
    if price.commodity_class!=product.commodity_class:problems.append('commodity_class_mismatch')
    if price.grade!=product.grade:problems.append('grade_mismatch')
    if date.fromisoformat(price.publication_date)>decision:problems.append('not_available_at_decision')
    if price.quote_type not in ['planning_price','assumed_sales','contract']:
        if date.fromisoformat(price.observation_date)>decision:problems.append('future_observation')
        age=(decision-date.fromisoformat(price.observation_date)).days
        if age>max_age_days:
            (warnings if allow_stale else problems).append('stale_price')
    if price.country!=farm.country:problems.append('country_mismatch')
    if price.state is None or price.state!=farm.state:
        (warnings if allow_regional_proxy else problems).append('regional_price_proxy')
    if farm.county and price.county!=farm.county:warnings.append('not_county_or_farm_specific')
    # Delivered/wholesale quotations require an explicit netback; no hidden freight.
    if price.quote_type in ['wholesale','terminal','shipping_point'] and (adjustments is None or not adjustments.get('source') or adjustments.get('per_kg') is None):
        problems.append('nonproducer_quote_requires_documented_netback')
    try:per_kg=fx(price.amount,price.currency,farm.currency,rate=fx_rate,as_of=decision_date)/mass_kg(price.unit,product.identifier)
    except ValueError:per_kg=None;problems.append('unsupported_currency_conversion')
    if adjustments is not None:
        if not adjustments.get('source')or adjustments.get('currency')!=farm.currency:problems.append('adjustment_provenance_or_currency_missing')
        else:
            adjustment=adjustments.get('per_kg')
            if isinstance(adjustment,bool)or not isinstance(adjustment,(int,float))or not math.isfinite(adjustment):problems.append('invalid_netback')
            elif per_kg is not None:per_kg+=adjustment
    if per_kg is not None and per_kg<0:problems.append('negative_net_sale_price')
    return {'available':not problems,'price_per_kg':None if problems else per_kg,'problems':problems,'warnings':warnings,
            'quote_type_preserved':price.quote_type,'origin':price.origin,'guaranteed_sale':False}


def history(prices,as_of):
    groups=defaultdict(list);seen={};duplicates=0;rejected=[]
    for p in prices:
        if p.origin!='public_observation' or p.publication_date>as_of or p.observation_date>as_of:continue
        # Mutually incompatible series remain separate even after unit conversion.
        key=(p.product_id,p.commodity_class,p.country,p.state,p.county,p.grade,p.quote_type,p.price_basis,p.currency,p.unit,p.market_location)
        identity=(key,p.observation_date)
        if identity in seen:
            if seen[identity]!=p.amount:raise ValueError('Conflicting duplicate price observations; choose a documented version')
            duplicates+=1;continue
        seen[identity]=p.amount;groups[key].append(p)
    result=[]
    for key,rows in sorted(groups.items(),key=lambda x:str(x[0])):
        rows=sorted(rows,key=lambda x:x.observation_date);values=[p.amount for p in rows];months=defaultdict(list)
        for p in rows:months[p.observation_date[5:7]].append(p.amount)
        gaps=[]
        for a,b in zip(rows,rows[1:]):
            da=date.fromisoformat(a.observation_date);db=date.fromisoformat(b.observation_date)
            distance=(db.year-da.year)*12+db.month-da.month
            if distance>1:gaps.append({'after':a.observation_date,'before':b.observation_date,'missing_months':distance-1})
        sd=statistics.stdev(values) if len(values)>1 else None
        result.append({'series':dict(zip(['product_id','commodity_class','country','state','county','grade','quote_type','price_basis','currency','unit','market_location'],key)),
            'observations':len(rows),'start':rows[0].observation_date,'end':rows[-1].observation_date,
            'mean':statistics.mean(values),'median':statistics.median(values),'sample_std':sd,
            'coefficient_of_variation':sd/statistics.mean(values) if sd is not None and statistics.mean(values)>0 else None,
            'min':min(values),'max':max(values),'recent_up_to_3_mean':statistics.mean(values[-3:]),
            'prior_up_to_12_mean':statistics.mean(values[-15:-3]) if len(values)>3 else None,
            'seasonal_month_means':{m:{'mean':statistics.mean(v),'n':len(v)} for m,v in sorted(months.items())},
            'missing_month_intervals':gaps,'forecast':None,'scope':'Unweighted observed monthly-price dispersion; not forecast volatility, a sale guarantee or a loss probability'})
    return {'series':result,'identical_duplicates_removed':duplicates,'future_prices_predicted':False}
