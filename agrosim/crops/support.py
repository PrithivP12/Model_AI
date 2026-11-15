"""Minimal farmer-facing support discovery, never invented field initialization."""
from copy import deepcopy
from ..finance.units import area_ha,finite
from .profiles import CropRegistry88
from .products import mappings


def assess_farm(*,location,current_crop,area,area_unit='ha',soil=None,management=None,allow_experimental_profiles=False):
    lat=finite(location['latitude']+90,'latitude shifted',0,180)-90
    lon=finite(location['longitude']+180,'longitude shifted',0,360)-180
    hectares=area_ha(area,area_unit);registry=CropRegistry88();current=registry.resolve(current_crop)
    missing=[]
    if soil is None:missing.append('soil depth, hydraulics, initial water/mineralN/C/N with provenance')
    if management is None:missing.append('planting/harvest dates, cultivar scope, seed/propagule, fertilizer and irrigation assumptions')
    missing.append('compatible dated weather scenario or reference explicitly selected by caller')
    products=mappings();rows=[]
    for crop in registry.crops.values():
        executable=crop['crop_parameters']is not None
        rows.append({'crop_id':crop['identifier'],'model_status':crop['implementation_status'],
                     'experimental_profile_opt_in_required':crop['implementation_status']=='EXECUTABLE_EXPERIMENTAL',
                     'parameter_profile_available':executable,
                     'simulation_ready':False,'required_inputs':deepcopy(missing),'climate_suitability':None,
                     'crop_specific_blockers':crop['blockers'],
                     'financial_support':products[crop['identifier']]['regional_price_and_cost_support']})
    return {'canonical_crop_count':88,'location':{'latitude':lat,'longitude':lon},'area_ha':hectares,'current_crop':current['identifier'],
            'crops':rows,'scope':'Support discovery only; location/area/crop cannot uniquely identify field soil, management, initial stocks or local buyers',
            'default_coefficients_visible_in_profile_registry':True,'experimental_policy_requested':allow_experimental_profiles,
            'soil_or_management_invented':False,'farmer_recommendation':None}
