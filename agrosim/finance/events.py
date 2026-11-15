"""Dated crop-product lots over the existing, read-only financial ledger.

No prices, dry-matter fractions, management or buyer access are inferred.
Annual expenses are explicitly allocated, never charged once per picking.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date

from .accounting import evaluate_rotation
from .units import finite
from ..crops.products import SaleBasisProduct


def bind_product_events(events, plantings):
    """Attach real establishment windows to executed product exports."""
    rows=[];seen=set()
    for event in events:
        when=date.fromisoformat(event['date'])
        matching=[p for p in plantings if p.crop==event['crop'] and p.date<=when<=p.harvest_deadline]
        if len(matching)!=1:raise ValueError('Product export needs one matching explicit establishment window')
        planted=matching[0].date.isoformat()
        identity=f'{event["crop"]}:{planted}:{event["date"]}:{event["product_id"]}'
        if identity in seen:raise ValueError('Duplicate executed product export')
        seen.add(identity)
        rows.append({**deepcopy(event),'lot_id':identity,'planted':planted,'harvested':event['date']})
    return rows


def allocate_season_costs(harvests, season_costs, *, irrigation_by_year=None, source):
    """Allocate supplied crop/year expenses by produced dry mass.

    This is an explicit bookkeeping convention, not an estimated production
    function. Fixed area/total expenses split once. Sales/storage/water unit
    rates stay unchanged and use their actual lot quantities in accounting.
    Multiple crops sharing annual irrigation require separate supplied accounts.
    """
    if not source:raise ValueError('Cost allocation provenance required')
    groups={};accounts={};water={};allocations=[]
    for h in harvests:
        key=f'{h["crop"]}:{int(h["harvested"][:4])}'
        groups.setdefault(key,[]).append(h)
    if set(season_costs)!=set(groups):raise ValueError('One explicit cost account per harvested crop/year required')
    irrigation_by_year=irrigation_by_year or {}
    for year,quantity in irrigation_by_year.items():
        finite(quantity,'annual irrigation')
        if quantity and sum(int(k.rsplit(':',1)[1])==int(year)for k in groups)!=1:
            raise ValueError('Shared irrigation needs explicit crop/lot allocation')
    for key,rows in groups.items():
        rows=sorted(rows,key=lambda x:x['harvested']);year=int(key.rsplit(':',1)[1])
        total=sum(finite(h['product_dry_kg_ha'],'product dry harvest')for h in rows)
        for i,h in enumerate(rows):
            share=h['product_dry_kg_ha']/total if total else float(i==0)
            identity=h['lot_id'];accounts[identity]=[]
            water[identity]=irrigation_by_year.get(year,0.)*share
            for item in season_costs[key]:
                if item.crop!=h['crop']:raise ValueError('Season expense has incompatible crop')
                multiplier=share if item.unit in ['per_ha','per_acre','total']else 1.
                provenance={**deepcopy(item.source),'lot_allocation':{'policy':'produced_dry_mass',
                    'share':share,'source':deepcopy(source),'season_account':key,'source_item':item.identifier}}
                accounts[identity].append(replace(item,quantity=item.quantity*multiplier,source=provenance))
            allocations.append({'lot_id':identity,'season_account':key,'share':share})
    return accounts,water,{'policy':'produced_dry_mass','source':deepcopy(source),'allocations':allocations,
                          'per_quantity_rates_not_multiplied_by_mass_share':True}


def evaluate_product_events(*, harvests, product_specs, costs_by_lot, irrigation_by_lot=None,
                            allocation=None, **kwargs):
    """Price actual organ exports using explicit conversion and lot accounts."""
    specs={s['crop']:s for s in product_specs}
    if len(specs)!=len(product_specs):raise ValueError('One product/grade specification per crop required')
    products=[];converted=[];dry_by_lot={}
    for s in product_specs:
        if not s.get('source'):raise ValueError('Product conversion provenance required')
        dm=finite(s['dry_matter_fraction'],'sale-product dry fraction',.001,1)
        products.append(SaleBasisProduct(s['product_id'],s['crop'],s['commodity_class'],
            'market_product_mass_kg_ha',s['marketed_fraction'],0.,s['grade'],'caller-declared storage',
            {'dry_matter_fraction':{'value':dm,'source':deepcopy(s['source'])},
             'marketed_fraction':deepcopy(s['source']),'sale_moisture':{'value':0.,'semantics':'already at sale basis'}}))
    for h in harvests:
        s=specs[h['crop']]
        if h['product_id']!=s['product_id']:raise ValueError('Executed product identity does not match sale specification')
        identity=h['lot_id']
        if identity in dry_by_lot:raise ValueError('Duplicate product lot')
        dry_by_lot[identity]=finite(h['product_dry_kg_ha'],'product dry harvest')
        converted.append({**deepcopy(h),'market_product_mass_kg_ha':dry_by_lot[identity]/s['dry_matter_fraction']})
    result=evaluate_rotation(harvests=converted,products=products,costs={},multiple_harvests=True,
                             costs_by_lot=costs_by_lot,irrigation_by_lot=irrigation_by_lot,**kwargs)
    rows=result.pop('annual_crop_finances')
    for row in rows:
        row['product_mass_at_sale_basis_kg_ha']=row.pop('grain_dry_kg_ha')
        row['total_product_mass_at_sale_basis_kg']=row.pop('total_grain_dry_kg')
        row['source_product_dry_kg_ha']=dry_by_lot[row['lot_id']]
    result.update(harvest_lot_finances=rows,accounting_version='dated_product_lots_v1',
        cost_allocation=deepcopy(allocation),organ_conversion={'specifications':deepcopy(product_specs),
        'physical_water_flux':False,'scope':'Explicit sale basis; fresh product mass is not simulated soil-water consumption'},
        product_quality_scope='One explicit product/grade per crop; separately graded coproducts unsupported')
    return result
