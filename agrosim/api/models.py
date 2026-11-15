"""Versioned JSON contracts. No request accepts a local path, URL fetch or code."""
from datetime import date as ISODate
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

Number=Annotated[float,Field(strict=True,ge=0,le=1e9)]
Fraction=Annotated[float,Field(strict=True,ge=0,le=1)]
Identifier=Annotated[str,Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
Text=Annotated[str,Field(min_length=1,max_length=1000)]

class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,validate_default=True)

class Source(Contract):
    origin:Literal['observed','user_supplied','source_reference','regional_proxy','hypothetical']
    description:Text
    source_id:Identifier|None=None

class Location(Contract):
    latitude:Annotated[float,Field(strict=True,ge=-90,le=90)]
    longitude:Annotated[float,Field(strict=True,ge=-180,le=180)]
    country:Annotated[str,Field(pattern=r'^[A-Z]{2}$')]|None=None
    state:Annotated[str,Field(max_length=40)]|None=None
    county:Annotated[str,Field(max_length=80)]|None=None

class Farm(Contract):
    location:Location
    area:Annotated[float,Field(strict=True,gt=0,le=1e7)]
    area_unit:Literal['ha','acre']='ha'

class SoilLayer(Contract):
    thickness_m:Annotated[float,Field(strict=True,gt=0,le=5)]
    wilting_point:Fraction
    field_capacity:Fraction
    saturation:Fraction
    residual_water_content:Fraction=.02
    drainage_fraction_day:Fraction=.5
    root_penetrability:Fraction=1.
    @model_validator(mode='after')
    def physical(self):
        if not self.residual_water_content<=self.wilting_point<self.field_capacity<self.saturation:raise ValueError('Require residual <= wilting < field capacity < saturation')
        return self

class SoilState(Contract):
    water_mm:list[Number]=Field(min_length=1,max_length=10)
    mineral_n_kg_ha:list[Number]=Field(min_length=1,max_length=10)
    humus_c_kg_ha:list[Number]=Field(min_length=1,max_length=10)
    humus_n_kg_ha:list[Number]=Field(min_length=1,max_length=10)
    residue_c_kg_ha:list[Number]=Field(min_length=1,max_length=10)
    residue_n_kg_ha:list[Number]=Field(min_length=1,max_length=10)
    temperature_C:list[Annotated[float,Field(strict=True,ge=-80,le=80)]]=Field(min_length=1,max_length=10)
    surface_residue_c_kg_ha:Number=0.
    surface_residue_n_kg_ha:Number=0.
    snow_mm:Number=0.

class Soil(Contract):
    layers:list[SoilLayer]=Field(min_length=1,max_length=10)
    initial_state:SoilState
    source:Source
    @model_validator(mode='after')
    def dimensions(self):
        for name,value in self.initial_state.model_dump().items():
            if isinstance(value,list)and len(value)!=len(self.layers):raise ValueError('One '+name+' value required per soil layer')
        if sum(l.thickness_m for l in self.layers)>10:raise ValueError('Maximum soil profile depth is 10 m')
        for l,w in zip(self.layers,self.initial_state.water_mm):
            if w>l.thickness_m*1000*l.saturation:raise ValueError('Initial soil water exceeds saturation')
        return self

class WeatherDay(Contract):
    date:ISODate
    rain_mm:Annotated[float,Field(strict=True,ge=0,le=2000)]
    tmin_C:Annotated[float,Field(strict=True,ge=-100,le=70)]
    tmax_C:Annotated[float,Field(strict=True,ge=-100,le=70)]
    solar_MJ_m2:Annotated[float,Field(strict=True,ge=0,le=60)]
    et0_mm:Annotated[float,Field(strict=True,ge=0,le=40)]|None=None
    @model_validator(mode='after')
    def order(self):
        if self.tmin_C>self.tmax_C:raise ValueError('Minimum temperature exceeds maximum')
        return self

class Weather(Contract):
    kind:Literal['inline','controlled_reference','cached_nasa_power']
    source:Source
    days:list[WeatherDay]|None=Field(default=None,max_length=1831)
    source_start_year:int|None=Field(default=None,ge=1981,le=2100)

class Planting(Contract):
    crop_id:Identifier
    subtype:Identifier|None=None
    planted:ISODate
    harvest_deadline:ISODate
    residue_removal_fraction:Fraction=0.
    harvest_mode:Literal['grain','terminate_to_residue']='grain'

class Management(Contract):
    date:ISODate
    fertilizer_n_kg_ha:Annotated[float,Field(strict=True,ge=0,le=2000)]=0.
    irrigation_mm:Annotated[float,Field(strict=True,ge=0,le=2000)]=0.
    source:Source

class Pool(Contract):
    dry:Number
    N:Number

class InitialPlant(Contract):
    crop_id:Identifier
    pools:dict[Literal['green_leaf','senesced_leaf','structural_shoot','root','grain','labile_N'],Pool]
    age_days:Annotated[float,Field(strict=True,ge=0,le=73000)]
    prior_season_shoot_dry:Number=0.
    primary_stem_gdd:Number=0.
    primary_stem_age_days:Number=0.
    source:Source

class Pollination(Contract):
    status:Literal['assumed_satisfied','observed_satisfied','unavailable']
    source:Text
    sex:Literal['female','male','hermaphrodite','compatible_cultivar']|None=None

class Pruning(Contract):
    date:ISODate
    fraction:Fraction
    source:Text

class CropOptions(Contract):
    pollination:Pollination|None=None
    flowering_type:Literal['day_neutral','short_day']|None=None
    short_day_chill_hours:Annotated[float,Field(strict=True,ge=0,le=3000)]|None=None
    night_VPD_kPa:Annotated[float,Field(strict=True,gt=0,le=10)]|None=None
    night_VPD_source:Text|None=None
    flower_induction_dates:list[ISODate]=Field(default_factory=list,max_length=20)
    pruning:list[Pruning]=Field(default_factory=list,max_length=100)

class Paddy(Contract):
    provenance:Text
    initial_pond_mm:Annotated[float,Field(strict=True,ge=0,le=1000)]
    bund_height_mm:Annotated[float,Field(strict=True,ge=0,le=1000)]
    infiltration_limit_mm_day:Annotated[float,Field(strict=True,ge=0,le=1000)]
    percolation_limit_mm_day:Annotated[float,Field(strict=True,ge=0,le=500)]
    initial_ammonium_fraction:Fraction
    fertilizer_form:Literal['ammonium_equivalent']
    nitrification_day:Annotated[float,Field(strict=True,ge=0,le=5)]
    denitrification_day:Annotated[float,Field(strict=True,ge=0,le=5)]
    redox_relaxation_day:Annotated[float,Field(strict=True,ge=0,le=5)]
    NH4_mobility:Fraction=.1
    NO3_mobility:Fraction=1.
    oxic_microsite_fraction:Fraction=.05
    drain_dates:list[ISODate]=Field(default_factory=list,max_length=1831)

class Living(Contract):
    fixture_id:Literal['hypothetical_functional_network_v1']
    assumptions:Literal['hypothetical_assumptions_adopted']
    enabled:dict[Literal['weed','herbivore','predator','microbes','detritivore','pathogen'],bool]=Field(default_factory=dict)

class ProductSpec(Contract):
    crop_id:Identifier
    product_id:Identifier
    commodity_class:Text
    grade:Text
    marketed_fraction:Fraction
    dry_matter_fraction:Annotated[float,Field(strict=True,gt=0,le=1)]
    source:Source

class PriceInput(Contract):
    identifier:Identifier
    product_id:Identifier
    amount:Number
    unit:Literal['kg','tonne','lb','bu']='kg'
    date:ISODate
    source:Source

class CostInput(Contract):
    identifier:Identifier
    crop_id:Identifier
    year:int=Field(ge=1900,le=2200)
    category:Identifier
    covers:list[Identifier]=Field(min_length=1,max_length=20)
    amount:Number
    unit:Literal['per_ha','per_acre','total','per_kg_sold','per_m3_irrigation','per_kg_stored_day']='per_ha'
    accounting:Literal['variable','operating','overhead','ownership','marketing','capital_cash']='variable'
    cash:Literal['cash','noncash','unknown']='cash'
    economic:bool=True
    expense_date:ISODate
    payment_date:ISODate|None=None
    source:Source

class MarketInput(Contract):
    identifier:Identifier
    product_id:Identifier
    status:Literal['confirmed','regional_proxy','unknown']
    source:Source
    buyer_id:Text|None=None
    buyer_location:Text|None=None
    max_kg:Number|None=None

class Sale(Contract):
    product_id:Identifier
    price_id:Identifier
    market_id:Identifier
    fraction:Fraction=1.
    date:ISODate|None=None

class Finance(Contract):
    decision_date:ISODate
    currency:Annotated[str,Field(pattern=r'^[A-Z]{3}$')]
    products:list[ProductSpec]=Field(min_length=1,max_length=20)
    prices:list[PriceInput]=Field(default_factory=list,max_length=100)
    costs:list[CostInput]=Field(default_factory=list,max_length=300)
    markets:list[MarketInput]=Field(default_factory=list,max_length=50)
    sales:list[Sale]=Field(default_factory=list,max_length=100)
    sale_timing:Literal['on_harvest','explicit']='explicit'
    discount_rate:Fraction
    opening_cash:Number|None=None
    storage_fraction:Fraction=0.
    storage_capacity_kg:Number=0.
    storage_daily_loss_fraction:Fraction=0.
    storage_source:Source|None=None
    use_regional_benchmarks:bool=False

class SimulationRequest(Contract):
    farm:Farm
    scenario_id:Identifier='user_scenario'
    reference_scenario_id:Identifier|None=None
    adopt_reference_assumptions:bool=False
    allow_experimental_profiles:bool=False
    start:ISODate|None=None
    end:ISODate|None=None
    plantings:list[Planting]=Field(default_factory=list,max_length=20)
    soil:Soil|None=None
    weather:Weather|None=None
    management:list[Management]|None=Field(default=None,max_length=2000)
    mode:Literal['standard','experimental_living']='standard'
    living:Living|None=None
    initial_plant:InitialPlant|None=None
    crop_options:dict[Identifier,CropOptions]=Field(default_factory=dict,max_length=20)
    paddy:Paddy|None=None
    finance:Finance|None=None

class RequirementsRequest(Contract):
    farm:Farm
    crops:list[Identifier]=Field(min_length=1,max_length=20)

class ComparisonRequest(Contract):
    scenarios:list[SimulationRequest]=Field(min_length=2,max_length=4)
    ranking:Literal['none','conditional_financial']='none'

class Issue(Contract):
    field:str
    code:str
    message:str

class Validation(Contract):
    valid:bool
    errors:list[Issue]
    warnings:list[Issue]
    engine:str|None=None
    required_inputs:list[str]=Field(default_factory=list)

class Job(Contract):
    run_id:str
    kind:Literal['simulation','comparison']
    status:Literal['queued','running','completed','failed','canceled']
    created_at:str
    updated_at:str
    progress:float|None=None
    error:Issue|None=None
    result_url:str|None=None

class ProductOutcome(Contract):
    crop_id:str
    product_id:str
    harvested:str
    product_dry_kg_ha:float
    total_product_dry_kg:float
    product_N_kg_ha:float|None=None
    saleable_mass_kg:float|None=None
    cohort_id:str|None=None

class Agriculture(Contract):
    products:list[ProductOutcome]
    seasonal_products:list[dict]
    planting_dates:list[dict]
    developmental_stages:list[dict]
    water:dict[str,float]
    initial_soil:dict
    final_soil:dict
    active_crop_at_end:str|None
    crop_survival_status:str
    terminal_events:list[dict]
    conservation:dict[str,float]

class FinancialOutcome(Contract):
    status:Literal['evaluated_conditional','financial_data_unavailable','partially_evaluated']
    missing:list[str]
    currency:str|None=None
    revenue:float|None=None
    operating_cost:float|None=None
    full_economic_cost:float|None=None
    net_return:float|None=None
    net_cash_flow:float|None=None
    detail:dict|None=None

class SimulationResult(Contract):
    schema_version:Literal['1.0']='1.0'
    run_id:str
    created_at:str
    engine_version:str
    selected_engine:str
    scenario_id:str
    mode:Literal['standard','experimental_living']
    area_ha:float
    crops:list[dict]
    units:dict[str,str]
    input_provenance:list[dict]
    validation_status:str
    parameter_evidence:str
    warnings:list[Issue]
    agriculture:Agriculture
    finance:FinancialOutcome
    ecosystem:dict|None=None
    timeseries_url:str
    artifacts:dict[str,str]
    numerical_sha256:str
    independent_accuracy:None=None
    farmer_recommendation:None=None

class ComparisonResult(Contract):
    schema_version:Literal['1.0']='1.0'
    run_id:str
    scenarios:list[SimulationResult]
    comparison_basis:dict
    conditional_ranking:list[dict]|None=None
    ranking_unavailable_reason:str|None=None
    farmer_recommendation:None=None
