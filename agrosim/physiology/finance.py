"""Product identity and unsupported-economics boundary over dated accounting."""
from copy import deepcopy

from .profiles import resolve
from ..finance.events import bind_product_events,evaluate_product_events


def product_support(identifier,subtype=None):
    p=resolve(identifier,subtype=subtype);a=p['archetype_parameters']
    return {'crop_id':identifier,'product_id':a['product_id'],'harvest_organ':a['product_organ'],
        'computed_basis':'kg harvested product dry matter/ha','market_unit':'kg at explicitly supplied sale basis',
        'dry_matter_fraction':None,'recovery_factor':None,'grade':None,'storage_suitability':None,
        'regional_price':None,'buyer_access':None,'complete_cost_account':None,
        'expected_cost_categories':['establishment/propagule','fertilizer','water/energy','labour','harvest','handling/storage','marketing','land/equipment'],
        'financial_status':'financial_data_unavailable','financial_prediction':None,
        'processing_scope':'Cacao whole pods, coconut whole fruit and rice rough grain are not processed beans, copra or milled rice'}


def finance_events(result,plantings):
    """Aggregate only simultaneous identical products; preserve source cohorts."""
    groups={}
    for h in result['product_harvests']:
        key=(h['crop'],h['date'],h['product_id'])
        row=groups.setdefault(key,{'crop':h['crop'],'date':h['date'],'product_id':h['product_id'],
            'product_dry_kg_ha':0.,'product_N_kg_ha':0.,'source_cohort_ids':[]})
        row['product_dry_kg_ha']+=h['product_dry_kg_ha'];row['product_N_kg_ha']+=h.get('product_N_kg_ha',0.)
        if h.get('cohort_id')is not None:row['source_cohort_ids'].append(h['cohort_id'])
    return bind_product_events(list(groups.values()),plantings)


def evaluate_optional_finance(result,plantings,*,financial_inputs=None):
    lots=finance_events(result,plantings)
    if financial_inputs is None:
        return {'status':'financial_data_unavailable','actual_product_lots':lots,'financial_prediction':None,
                'missing':['supplied product conversion/quality','compatible price/cost accounts','market access/sale plan'],
                'farmer_recommendation':None}
    output=evaluate_product_events(harvests=deepcopy(lots),**financial_inputs)
    output['agronomic_profile_status']='EXECUTABLE_EXPERIMENTAL_NOT_FIELD_VALIDATED'
    output['independent_accuracy']=None
    return output


def demonstrate_finance(result,plantings,settings):
    from ..platform.verify import _finance
    output=_finance(result,plantings,settings)
    for value in output.get('outcomes',{}).values():
        value['accounting_input_mode_tag']=value['yield_support']
        value['yield_support']='agrosim_special_experimental_not_field_validated'
    return output
