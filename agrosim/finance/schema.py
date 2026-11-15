"""Small canonical, validated economic contracts. None represents a forecast."""
from dataclasses import dataclass,field,asdict
from datetime import date
from .units import finite,area_ha,mass_kg

ORIGINS={'public_observation','reference_budget','planning_assumption','farmer_supplied','synthetic_fixture'}
QUOTES={'producer_price','wholesale','terminal','shipping_point','contract','assumed_sales','planning_price'}


def evidence(origin,source):
    if origin not in ORIGINS or not isinstance(source,dict)or not source:raise ValueError('Economic origin and traceable source required')
    if origin=='public_observation'and not(source.get('url')and source.get('record_id')):raise ValueError('Public observation needs source URL and record ID')


@dataclass
class Farm:
    country:str
    state:str|None
    county:str|None
    area:float
    area_unit:str
    currency:str
    reference_year:int
    equipment:list
    irrigation_available:bool
    soil_context:str
    production_context:str
    source:dict
    def __post_init__(self):
        area_ha(self.area,self.area_unit)
        if not self.country or not self.currency or not self.source or not self.soil_context or not self.production_context:raise ValueError('Farm context, currency and provenance required')
        if not isinstance(self.equipment,list)or type(self.irrigation_available)is not bool:raise ValueError('Explicit equipment and irrigation status required')
    @property
    def hectares(self):return area_ha(self.area,self.area_unit)


@dataclass
class Product:
    identifier:str
    crop:str
    commodity_class:str
    harvest_field:str
    marketed_fraction:float
    sale_moisture:float
    grade:str
    storage_requirements:str
    provenance:dict
    def __post_init__(self):
        finite(self.marketed_fraction,'marketable fraction',0,1);finite(self.sale_moisture,'moisture',0,.5)
        if not self.identifier or not self.crop or not self.grade or not self.commodity_class or not self.provenance.get('marketed_fraction')or not self.provenance.get('sale_moisture'):raise ValueError('Explicit product, grade, recovery and moisture provenance required')
        if self.harvest_field!='grain_dry_kg_ha':raise ValueError('First finance adapter supports explicit grain dry matter only; residue/biomass is not grain')


@dataclass
class Price:
    identifier:str
    product_id:str
    commodity_class:str
    amount:float
    currency:str
    unit:str
    quote_type:str
    country:str
    state:str|None
    county:str|None
    market_location:str
    observation_date:str
    publication_date:str
    grade:str
    price_basis:str
    origin:str
    source:dict
    fees:dict=field(default_factory=dict)
    def __post_init__(self):
        finite(self.amount,'price');date.fromisoformat(self.observation_date);date.fromisoformat(self.publication_date)
        if self.quote_type not in QUOTES or not self.currency or not self.price_basis or not self.market_location:raise ValueError('Explicit quote type, currency, location and price basis required')
        mass_kg(self.unit,self.product_id);evidence(self.origin,self.source)
        if self.origin=='public_observation'and self.quote_type in ['assumed_sales','planning_price']:raise ValueError('A planning price is not a public observed sale')


@dataclass
class CostItem:
    identifier:str
    category:str
    covers:list
    crop:str
    amount:float
    unit:str
    currency:str
    accounting:str
    cash:str
    origin:str
    source:dict
    quantity:float=1.
    included_in:str|None=None
    previous_crop:str|None=None
    expense_date:str|None=None
    payment_date:str|None=None
    economic:bool=True
    def __post_init__(self):
        finite(self.amount,'cost unit price');finite(self.quantity,'cost quantity')
        if self.unit not in ['per_ha','per_acre','total','per_kg_sold','per_m3_irrigation','per_tonne_km','per_kg_stored_day']:raise ValueError('Unsupported cost basis')
        if self.accounting not in ['variable','operating','overhead','ownership','marketing','capital_cash']:raise ValueError('Explicit cost accounting class required')
        if self.cash not in ['cash','noncash','unknown']or not self.covers or not self.category:raise ValueError('Cost coverage and cash classification required')
        if self.accounting=='capital_cash'and self.economic:raise ValueError('Capital purchases are cash uses, not current economic depreciation')
        evidence(self.origin,self.source)
        for d in [self.expense_date,self.payment_date]:
            if d:date.fromisoformat(d)


@dataclass
class MarketAccess:
    identifier:str
    product_id:str
    status:str
    buyer_id:str|None
    buyer_location:str|None
    commodity_class:str
    grade:str
    max_kg:float|None
    delivery_start:str
    delivery_end:str
    contractual_status:str
    source:dict
    distance_km:float|None=None
    freight_per_tonne_km:float|None=None
    freight_currency:str|None=None
    def __post_init__(self):
        if self.status not in ['confirmed','observed_market_activity','regional_proxy','unknown']:raise ValueError('Explicit market-access classification required')
        if not self.source or date.fromisoformat(self.delivery_start)>date.fromisoformat(self.delivery_end):raise ValueError('Market provenance/delivery interval required')
        if self.max_kg is not None:finite(self.max_kg,'buyer capacity')
        if self.status=='confirmed'and(not self.buyer_id or not self.buyer_location or self.contractual_status not in ['documented_contract','farmer_declared_contract']):raise ValueError('Confirmed market requires explicit buyer/location/contract support')
        if (self.distance_km is None)!=(self.freight_per_tonne_km is None):raise ValueError('Freight requires both documented distance and rate')
        if self.distance_km is not None:
            finite(self.distance_km,'distance');finite(self.freight_per_tonne_km,'freight rate')
            if not self.freight_currency:raise ValueError('Explicit freight currency required')
