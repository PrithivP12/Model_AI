"""Small source adapters: raw evidence, explicit mappings and no credential leaks."""
from dataclasses import asdict
from datetime import date
from pathlib import Path
import calendar
import csv
import hashlib
import json
import re
import requests
from .schema import Price,CostItem


def fetch_json(source,query,*,key=None,report_id=None,section=None,session=None):
    """Explicit online acquisition; offline evaluation never calls this function."""
    if not key:return {'status':'missing_credentials','source':source,'payload':None}
    if source=='nass':
        url='https://quickstats.nass.usda.gov/api/api_GET/';params={**query,'key':key,'format':'JSON'};auth=None
    elif source=='ams':
        if report_id is None or not str(report_id).isdigit()or not section:return {'status':'report_and_section_required','source':source,'payload':None}
        if section not in ['Details','Summary','Report Details']:raise ValueError('Explicit supported AMS report section required')
        url=f'https://marsapi.ams.usda.gov/services/v1.2/reports/{int(report_id)}/{section}';params=query;auth=(key,'')
    else:raise ValueError('Unknown economic API source')
    try:
        r=(session or requests).get(url,params=params,auth=auth,timeout=20)
        if r.status_code!=200:return {'status':'unavailable','source':source,'http_status':r.status_code,'payload':None}
        payload=r.json()
        if source=='nass'and 'data'not in payload:return {'status':'invalid_response','source':source,'payload':None}
        return {'status':'downloaded','source':source,'query':query,'url':url,'payload':payload}
    except (requests.RequestException,ValueError):
        # Never expose request exception text, response error pages or credentials.
        return {'status':'unavailable','source':source,'payload':None}


def nass_prices(path,*,product_id,commodity,commodity_class,record_source):
    payload=json.loads(Path(path).read_text());prices=[];excluded=[]
    for i,row in enumerate(payload.get('data',[])):
        reason=None
        if row.get('commodity_desc')!=commodity or row.get('class_desc')!=commodity_class or row.get('statisticcat_desc')!='PRICE RECEIVED':reason='incompatible_commodity_or_measure'
        if row.get('freq_desc')!='MONTHLY'or row.get('unit_desc')!='$ / BU':reason='unsupported_frequency_or_unit'
        try:amount=float(str(row['Value']).replace(',',''))
        except (ValueError,KeyError):reason='suppressed_or_missing_value'
        try:
            y=int(row['year']);m=int(row['begin_code']);end=date(y,m,calendar.monthrange(y,m)[1]).isoformat()
            # Conservative current-version availability, not original release reconstruction.
            available=date.fromisoformat(row['load_time'][:10]).isoformat()
        except (ValueError,KeyError):reason='unknown_availability_or_period'
        if reason:excluded.append({'row':i,'reason':reason});continue
        ident='nass-'+hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()[:20]
        prices.append(Price(ident,product_id,commodity_class,amount,'USD','bu','producer_price','US',row.get('state_alpha'),None,
            row['location_desc'],end,available,'unspecified','state_average_prices_received','public_observation',
            {**record_source,'record_id':ident,'raw_row':i,'short_desc':row['short_desc'],
             'availability_semantics':'latest load_time is conservative version-availability date, not a verified original release date'}))
    return prices,excluded


def ams_prices(payload,*,report_id,field_map,constants,source):
    """Report-specific mapping required; heterogeneous reports cannot share guesses.

    field_map maps canonical Price fields to exact source field names. No implicit
    range averaging or farmgate conversion occurs. Unknown quotes/units fail.
    """
    rows=payload.get('results')
    if not isinstance(rows,list):raise ValueError('This explicit AMS adapter expects a results section')
    result=[]
    for i,row in enumerate(rows):
        values={name:row[column] for name,column in field_map.items()}
        values={**constants,**values};values['amount']=float(values['amount'])
        values['identifier']=f'ams-{report_id}-{i}'
        values['source']={**source,'record_id':values['identifier'],'report_id':report_id,'raw_row':i}
        result.append(Price(**values))
    return result


def ers_costs(path,*,crop,region,year,source):
    rows=list(csv.DictReader(Path(path).open(encoding='utf-8-sig')))
    rows=[r for r in rows if r['Region']==region and int(r['Year'])==year]
    if not rows:raise ValueError('No exact ERS crop/region/year account')
    expected={'corn':'Corn','soybeans':'Soybean','spring_wheat':'Wheat'}[crop]
    if any(r['Commodity']!=expected for r in rows):raise ValueError('ERS commodity mismatch')
    mapping={'Seed':('seed','variable','cash'), 'Fertilizer':('fertilizer','variable','cash'),
        'Chemicals':('chemicals','variable','cash'),'Custom services':('machine_hire','variable','cash'),
        'Fuel, lube, and electricity':('fuel_and_energy','variable','cash'),'Repairs':('machinery_repair','variable','cash'),
        'Purchased irrigation water':('purchased_water','variable','cash'),
        'Interest on operating inputs':('operating_interest','operating','noncash'),
        'Interest on operating capital':('operating_interest','operating','noncash'),
        'Other variable expenses':('other_variable','variable','cash'),
        'Hired labor':('hired_labor','overhead','cash'),'Opportunity cost of unpaid labor':('unpaid_labor','ownership','noncash'),
        'Capital recovery of machinery and equipment':('capital_recovery','ownership','noncash'),
        'Opportunity cost of land':('land','ownership','noncash'),
        'Taxes and insurance':('taxes_insurance','overhead','cash'),'General farm overhead':('general_overhead','overhead','cash')}
    items=[];reported={}
    for row in rows:
        if row['Category'] not in ['Operating costs','Allocated overhead']:continue
        name=row['Item'].strip()
        if name.startswith('Total,'):reported[row['Category']]=float(row['Value']);continue
        if row['Units']!='dollars per planted acre'or name not in mapping:raise ValueError('Unmapped ERS cost item/unit: '+name)
        category,account,cash=mapping[name]
        covers=['machinery_depreciation','capital_interest'] if category=='capital_recovery' else ['fuel','utilities'] if category=='fuel_and_energy' else ['property_insurance','taxes'] if category=='taxes_insurance' else [category]
        items.append(CostItem('ers-'+crop+'-'+category,category,covers,crop,float(row['Value']),'per_acre','USD',account,cash,
            'reference_budget',{**source,'record_id':f'{crop}:{region}:{year}:{name}','region':region,'year':year,
               'source_item':name,'source_accounting':row['Category'],'cash_classification':'accounting interpretation of economic benchmark, not actual farm payments',
               'compatibility':'Northern Great Plains aggregate wheat, including spring/winter/durum, is a proxy rather than Illinois spring-wheat account' if crop=='spring_wheat' else 'Heartland regional aggregate, not a farm expense record'}))
    sums={c:sum(x.amount for x in items if x.source['source_accounting']==c)for c in reported}
    if any(abs(sums[c]-reported[c])>.1 for c in reported):raise ValueError('ERS detailed items do not reconcile with source totals')
    return items,{'reported':reported,'summed':sums,'region':region,'year':year,'source':source}


FARMDOC_MAP={
 'Fertilizers':('fertilizer','variable','cash'),'Pesticides':('chemicals','variable','cash'),'Seed':('seed','variable','cash'),
 'Drying':('drying','variable','cash'),'Storage':('storage','marketing','cash'),'Crop insurance':('crop_insurance','variable','cash'),
 'Machine hire/lease':('machine_hire','variable','cash'),'Utilities':('utilities','variable','cash'),
 'Machine repair':('machinery_repair','variable','cash'),'Fuel and oil':('fuel','variable','cash'),
 'Light vehicle':('light_vehicle','variable','cash'),'Mach. depreciation':('machinery_depreciation','ownership','noncash'),
 'Hired labor':('hired_labor','overhead','cash'),'Building repair and rent':('building_repair_rent','overhead','cash'),
 'Building depreciation':('building_depreciation','ownership','noncash'),'Insurance':('property_insurance','overhead','cash'),
 'Misc':('misc_overhead','overhead','cash'),'Interest (non-land)':('operating_interest','operating','unknown'),
 'Land costs':('land','ownership','unknown')}


def farmdoc_budgets(path,*,source):
    from pypdf import PdfReader
    reader=PdfReader(path)
    # Match the actual table heading, not a guessed PDF page number.
    candidates=[p.extract_text() for p in reader.pages if 'Table 2.'in p.extract_text() and 'High Productivity'in p.extract_text()]
    if len(candidates)!=1:raise ValueError('Exactly one Central Illinois high-productivity table required')
    text=candidates[0];parsed={}
    labels=list(FARMDOC_MAP)+['Total non-land costs','Price per bu']
    for label in labels:
        lines=[line for line in text.splitlines() if re.match(r'^'+re.escape(label)+r'\s+[-$0-9]',line.strip())]
        if len(lines)!=1:raise ValueError('Missing/ambiguous farmdoc line: '+label)
        values=[float(t.replace('$','').replace(',',''))for t in lines[0].strip()[len(label):].split()]
        expected=5 if label=='Land costs'else 6
        if len(values)!=expected:raise ValueError('Unexpected farmdoc column count: '+label)
        parsed[label]=values
    output=[]
    for column,crop,prev in [(0,'corn','soybeans'),(1,'corn','corn'),(2,'soybeans','corn'),(3,'soybeans','soybeans')]:
        items=[]
        for label,(category,account,cash)in FARMDOC_MAP.items():
            items.append(CostItem(f'farmdoc-{column}-{category}',category,[category],crop,parsed[label][column],'per_acre','USD',account,cash,
                'reference_budget',{**source,'record_id':f'Table2:{column}:{label}','region':'Central Illinois high productivity','budget_year':2027,
                'source_item':label,'column':column,'scope':'Projected whole enterprise benchmark; not simulator-specific or farm actual cost'},previous_crop=prev))
        nonland=sum(x.amount for x in items if x.category!='land')
        if abs(nonland-parsed['Total non-land costs'][column])>1.:raise ValueError('Farmdoc item reconciliation failed')
        output.append({'crop':crop,'previous_crop':prev,'items':items,'source_nonland_total_per_acre':parsed['Total non-land costs'][column],
            'item_nonland_total_per_acre':nonland,'published_rounding_residual':nonland-parsed['Total non-land costs'][column],
            'planning_price_per_bu':parsed['Price per bu'][column]})
    return output
