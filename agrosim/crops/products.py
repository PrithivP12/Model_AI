"""Explicit crop-organ bridge to unchanged financial accounting.

Fresh sale mass is supplied dry-matter conversion, not a physical soil-water
flux. No default vegetable moisture, price, cost or marketability is invented.
"""
from dataclasses import dataclass
from copy import deepcopy
from ..finance.schema import Product
from ..finance.units import finite
from ..finance.accounting import evaluate_rotation
from .profiles import CropRegistry88,REFERENCE


@dataclass
class SaleBasisProduct(Product):
    def __post_init__(self):
        finite(self.marketed_fraction,'marketed fraction',0,1)
        if self.sale_moisture!=0 or self.harvest_field!='market_product_mass_kg_ha':raise ValueError('Organ bridge needs explicitly converted sale-basis mass')
        if not self.identifier or not self.crop or not self.commodity_class or not self.grade or not self.provenance.get('dry_matter_fraction'):
            raise ValueError('Explicit product, quality and conversion provenance required')


def mappings():
    out={}
    for ident,p in CropRegistry88().crops.items():
        if ident in REFERENCE:
            pid={'corn':'corn_grain','soybean':'soybean_seed','wheat':'spring_wheat_grain'}[ident];organ='grain';field='grain_dry_kg_ha';kind='existing_grain'
        elif p['crop_parameters']:
            a=p['archetype_parameters'];pid=a['product_id'];organ=a['product_organ'];field='product_dry_kg_ha';kind='crop_organ_dry_mass'
        else:pid=None;organ=None;field=None;kind='unavailable'
        out[ident]={'crop_id':ident,'product_id':pid,'product_organ':organ,'harvest_field':field,'mass_basis':kind,
          'market_unit':'kg_with_explicit_basis'if pid else None,'default_market_dry_matter_fraction':None,'default_recovery':None,
          'default_grade':None,'regional_price_and_cost_support':'existing_finance_regional_or_class_proxies'if ident in REFERENCE else'unavailable',
          'financial_estimate':None,'required_inputs':['market dry-matter fraction/source','recovery/grade','compatible price','cost account','market access'],
          'repeated_harvest_finance':'blocked_same_year_cohort_timing'if p['archetype_parameters'].get('repeated_harvest')else'one_crop_harvest_per_year_supported',
          'no_crop_yield_created':True}
    return out


def evaluate_products(*,harvests,product_specs,**kwargs):
    """Single harvest/year bridge; full accounting remains in existing finance.

    Specs explicitly require product_id,crop,commodity_class,grade,
    marketed_fraction,dry_matter_fraction,source. Multiple same-year cohorts are
    refused by the existing engine, never merged with fabricated sale timing.
    """
    specs={s['crop']:s for s in product_specs}
    if len(specs)!=len(product_specs):raise ValueError('One explicit product specification per crop required')
    products=[];converted=[]
    for s in product_specs:
        if not s.get('source'):raise ValueError('Market conversion provenance required')
        dm=finite(s['dry_matter_fraction'],'sale-product dry matter fraction',.001,1)
        products.append(SaleBasisProduct(s['product_id'],s['crop'],s['commodity_class'],'market_product_mass_kg_ha',
            s['marketed_fraction'],0.,s['grade'],'caller-declared storage',
            {'dry_matter_fraction':{'value':dm,'source':s['source']},'marketed_fraction':s['source'],
             'sale_moisture':{'value':0.,'semantics':'Input already at explicit sale-mass basis'}}))
    for h in harvests:
        if 'product_dry_kg_ha'not in h:raise ValueError('Executed product-organ dry harvest required; total plant biomass is not a product')
        s=specs[h['crop']]
        if h.get('product_id')is not None and h['product_id']!=s['product_id']:
            raise ValueError('Executed harvest product identity does not match the sale-product specification')
        converted.append({**deepcopy(h),'market_product_mass_kg_ha':h['product_dry_kg_ha']/s['dry_matter_fraction']})
    result=evaluate_rotation(harvests=converted,products=products,**kwargs)
    for row in result['annual_crop_finances']:
        row['product_mass_at_sale_basis_kg_ha']=row.pop('grain_dry_kg_ha')
        row['total_product_mass_at_sale_basis_kg']=row.pop('total_grain_dry_kg')
        row['source_product_dry_kg_ha']=next(h['product_dry_kg_ha']for h in harvests if h['crop']==row['crop']and int(h['harvested'][:4])==row['year'])
    result['organ_conversion']={'specifications':deepcopy(product_specs),'physical_water_flux':False,
        'scope':'Supplied dry-matter fraction converts product dry mass to market basis; not a new plant-water or hydrology model',
        'legacy_grain_adapter_used_for_new_organs':False}
    return result
