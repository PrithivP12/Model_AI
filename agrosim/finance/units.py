"""Mass conventions are product-specific; bushels here are trade weight, not volume."""
import math

LB_KG=.45359237
ACRE_HA=.40468564224
BUSHEL_LB={'corn_grain':56.,'soybean_seed':60.,'spring_wheat_grain':60.}
UNIT_SOURCE='https://www.ams.usda.gov/sites/default/files/media/Book2.pdf'
MOISTURE_SOURCE='https://ask.ifas.ufl.edu/publication/AG442'


def finite(value,name,minimum=0.,maximum=None):
    if isinstance(value,bool)or not isinstance(value,(int,float))or not math.isfinite(value)or value<minimum or (maximum is not None and value>maximum):
        raise ValueError(f'{name} must be finite within declared bounds')
    return float(value)


def mass_kg(unit,product=None):
    if unit=='kg':return 1.
    if unit in ['tonne','metric_tonne']:return 1000.
    if unit=='lb':return LB_KG
    if unit=='bu' and product in BUSHEL_LB:return BUSHEL_LB[product]*LB_KG
    raise ValueError('Unsupported mass unit or missing crop-specific bushel convention')


def area_ha(value,unit):
    value=finite(value,'area',1e-12)
    if unit=='ha':return value
    if unit=='acre':return value*ACRE_HA
    raise ValueError('Explicit ha/acre area unit required')


def saleable(dry_kg,fraction,moisture):
    return finite(dry_kg,'grain dry mass')*finite(fraction,'marketable recovery fraction',0,1)/(1-finite(moisture,'sale moisture wet-basis',0,.5))


def fx(amount,source_currency,target_currency,*,rate=None,as_of=None):
    if source_currency==target_currency:return amount
    if not rate or rate.get('from_currency')!=source_currency or rate.get('to_currency')!=target_currency or not rate.get('source')or rate.get('date')!=as_of:
        raise ValueError('Documented matching currency rate, date and source required')
    return amount*finite(rate['rate'],'FX rate',1e-12)
