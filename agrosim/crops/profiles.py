"""Explicit transferred/exploratory profiles, not a disguised calibrated database.

All crop coefficients are frozen declarations before evaluation. Existing defaults
are never overwritten. EcoCrop environmental envelopes are metadata, not RUE or
root measurements. FAO56 values contextualize stage/water concepts, not fitted
basal-canopy coefficients in this simpler existing solver.
"""
from copy import deepcopy
from dataclasses import asdict,replace
from pathlib import Path
import json

from ..processes import default_crops,Crop
from ..crop_catalog import CropChoiceRegistry,CropRegistry,UnsupportedCropError

# Profile equations are shared; crop-specific durations are exploratory scenarios,
# not field calendars, measured cultivar maturity or independent observations.
ASSIGNMENTS={
 'leaf': 'lettuce spinach kale leek'.split(),
 'head': 'cabbage broccoli cauliflower'.split(),
 'storage': 'carrot beetroot radish turnip sugar_beet potato cassava taro tanier sweet_potato'.split(),
 'bulb':'onion garlic'.split(),
 'c3_grain':'barley oats rye'.split(),
 'c4_grain':'pearl_millet teff sorghum'.split(),
 'legume_grain':'chickpea lentil cowpea faba_bean pigeonpea peanut bambara_groundnut guar'.split(),
 'oilseed':'sunflower sesame canola safflower ethiopian_mustard flax chia'.split(),
 'pseudo_grain':'quinoa grain_amaranth'.split(),
 'fruit':'pepper tomato okra eggplant pumpkin watermelon zucchini cantaloupe cucumber'.split(),
 'pod':'bush_bean green_bean pole_bean garden_pea'.split(),
 'fiber':'cotton hemp'.split(),
 'sweet_grain':['sweet_corn'],
 'forage':'alfalfa bahiagrass bermudagrass napier_grass guinea_grass'.split(),
 'cover':['velvet_bean'],
 'sugar_stem':['sugarcane'],
}
REFERENCE={'corn':'corn','soybean':'soybeans','wheat':'spring_wheat'}
BLOCKERS={
 'rice':['flooded paddy surface-water/redox/aerenchyma and flooded-N processes absent; no upland-rice cultivar specified'],
 'scallion':['unresolved Allium species/hybrid; supply species and cultivar before assigning life cycle'],
 'brachiaria':['unresolved Urochloa species/hybrid; no single forage phenotype inferred'],
 'ryegrass':['perennial versus Italian ryegrass unresolved; dormancy/cutting profile requires identity'],
 'pineapple':['CAM carbon/water exchange and ratoon/induction lifecycle absent'],
 'strawberry':['crown/stolon, daylength-dependent flowering and cultivar/chilling lifecycle absent'],
 'grape':['woody reserves, chilling/budbreak, pruning, fruit cohorts and perennial structure absent'],
 'passion_fruit':['perennial vine reserves/structure, establishment and fruit cohorts absent'],
 'apple':['woody storage, chilling/budbreak and orchard age/fruiting absent'],
 'mango':['woody storage, seasonal floral induction and orchard age absent'],
 'coconut':['palm age/structure and multi-year fruit-development cohorts absent'],
 'date':['palm age/structure, pollination and fruit-development cohorts absent'],
 'cacao':['woody stores/structure, shade and fruit-development cohorts absent'],
 'banana':['corm/pseudostem/suckering and replacement-stem fruit lifecycle absent'],
 'papaya':['arborescent perennial establishment and continuous fruit cohort lifecycle absent'],
 'orange':['woody reserves, floral induction and fruit cohorts absent'],
 'lemon':['woody reserves, perennial establishment and fruit cohorts absent'],
 'avocado':['woody reserves, flowering/alternate bearing and fruit cohorts absent'],
 'olive':['woody reserves, chilling/flowering and alternate bearing absent'],
 'guava':['woody perennial storage/structure and recurrent fruiting absent']}
DAYS={'lettuce':75,'spinach':65,'radish':40,'kale':90,'leek':130,'cabbage':150,'broccoli':135,'cauliflower':140,
 'carrot':130,'beetroot':100,'turnip':90,'sugar_beet':180,'potato':130,'cassava':300,'taro':210,'tanier':240,'sweet_potato':150,
 'onion':150,'garlic':180,'barley':110,'oats':110,'rye':120,'pearl_millet':100,'teff':100,'sorghum':130,
 'chickpea':120,'lentil':110,'cowpea':100,'faba_bean':130,'pigeonpea':180,'peanut':140,'bambara_groundnut':150,'guar':110,
 'sunflower':120,'sesame':110,'canola':130,'safflower':130,'ethiopian_mustard':140,'flax':110,'chia':130,
 'quinoa':130,'grain_amaranth':120,'pepper':180,'tomato':170,'okra':160,'eggplant':190,'pumpkin':130,'watermelon':100,
 'zucchini':100,'cantaloupe':110,'cucumber':110,'bush_bean':90,'green_bean':90,'pole_bean':130,'garden_pea':90,
 'cotton':190,'hemp':140,'sweet_corn':95,'alfalfa':70,'bahiagrass':70,'bermudagrass':70,'napier_grass':90,'guinea_grass':80,'velvet_bean':130,'sugarcane':300}
COOL=set('lettuce spinach kale leek cabbage broccoli cauliflower carrot beetroot radish turnip sugar_beet onion garlic barley oats rye chickpea lentil faba_bean canola ethiopian_mustard flax garden_pea alfalfa'.split())
LEGUMES=set(ASSIGNMENTS['legume_grain']+ASSIGNMENTS['pod']+['alfalfa','velvet_bean'])
SOURCES={
 'existing':{'path':'agrosim/processes.py','scope':'Existing unvalidated crop defaults; transfer is Tier C, not crop-specific measurement'},
 'fao56_stages':{'url':'https://www.fao.org/4/x0490e/x0490e0b.htm','scope':'Crop-specific calendar-stage examples and Kc framework; they do not validate this model or the declared GDD transformation'},
 'fao56_water':{'url':'https://www.fao.org/4/x0490e/x0490e0e.htm','scope':'Root-zone depletion/available-water framework; no new species coefficients copied'},
 'aquacrop':{'url':'https://www.fao.org/aquacrop/overview/calculation-scheme/','scope':'Canopy/transpiration/biomass/product separation context, not AquaCrop source code, calibration or water-productivity coefficients'},
 'scenario':{'scope':'Explicit hypothetical archetype/crop scenario assumptions, no claimed empirical bounds or probability'}}


def profiles():
    choices=CropChoiceRegistry();base=CropRegistry(); result={}
    assignment={crop:group for group,crops in ASSIGNMENTS.items()for crop in crops}
    for ident,choice in choices.choices.items():
        ref=choice['reference_crop_id'];taxonomy=choice['taxonomy'];arch=assignment.get(ident)
        env=deepcopy(base.resolve(ref)['suitability']) if ref and choice['profile_mapping']=='exact'else {}
        if ident in REFERENCE:
            kernel=REFERENCE[ident];p=asdict(default_crops()[kernel]);status='EXECUTABLE_REFERENCE';arch='legacy_'+kernel
            ext={};parameter_evidence={k:{'tier':'C','source':'existing','scope':'Frozen existing illustrative profile; not new independent validation'}for k in p if k!='name'}
        elif arch:
            donor='spring_wheat' if ident in COOL else 'soybeans' if ident in LEGUMES else 'corn'
            p=asdict(default_crops()[donor]);p['name']=ident
            cool=ident in COOL;t=19. if cool else 27.
            p.update(base_temp_C=3. if cool else 8.,optimum_temp_C=22. if cool else 28.,maximum_temp_C=36. if cool else 42.,
                     kill_minimum_temp_C=-4. if cool else 0.,maturity_gdd=(t-(3 if cool else 8))*DAYS[ident],
                     seed_dry_kg_ha=20.,root_biomass_fraction=.2,plant_n_fraction=.025,grain_n_fraction=.025,
                     harvest_index=.5,fixation_max_n_kg_ha_day=3. if ident in LEGUMES else 0.,
                     rue_g_dry_per_MJ_PAR=2.5 if cool else 3.,max_root_depth_m=1.,max_lai=5.,kc_mid=1.,
                     specific_leaf_area_m2_kg=24.,vegetative_leaf_fraction=.5)
            if arch in ['leaf','head','storage','bulb','fruit','pod']:
                p.update(max_root_depth_m=.6,max_lai=4.,root_biomass_fraction=.15,rue_g_dry_per_MJ_PAR=2.2)
            if arch in ['storage','bulb']:
                p.update(root_biomass_fraction=.12,plant_n_fraction=.018,seed_dry_kg_ha=100. if ident in ['potato','cassava','taro','tanier','sweet_potato','garlic']else 10.)
            if arch=='forage':p.update(root_biomass_fraction=.3,vegetative_leaf_fraction=.6,seed_dry_kg_ha=25.,max_root_depth_m=1.2)
            if arch=='sugar_stem':p.update(root_biomass_fraction=.25,seed_dry_kg_ha=300.,plant_n_fraction=.012)
            if arch in ['legume_grain','pod']:p.update(plant_n_fraction=.035,grain_n_fraction=.04)
            group_product={'leaf':'leaf','head':'head_or_inflorescence','storage':'storage_organ','bulb':'bulb','c3_grain':'grain','c4_grain':'grain',
             'legume_grain':'seed','oilseed':'seed','pseudo_grain':'grain','fruit':'fruit','pod':'immature_pod','fiber':'raw_fiber_bearing_organ',
             'sweet_grain':'immature_kernel','forage':'cut_forage','cover':'cover_shoot','sugar_stem':'sugar_bearing_stem'}[arch]
            ext={'establishment_gdd':80.,'product_start_fraction':.45,'product_allocation_fraction':.55,
                 'product_maturation_gdd':120.,'senescence_day':.005,'root_turnover_day':.001,
                 'harvest_fraction':.75 if arch in ['fruit','pod','forage','sugar_stem']else 1.,
                 'harvest_interval_days':21 if arch in ['fruit','pod']else 60,
                 'harvest_ready_fraction':.85 if arch=='sweet_grain'else 1.,
                 'perennial':arch in ['forage','sugar_stem'],'repeated_harvest':arch in ['fruit','pod','forage','sugar_stem'],
                 'reserve_fraction':.12 if arch in ['forage','sugar_stem']else 0.,'reserve_mobilization_day':.02,
                 'dormancy_temp_C':5.,'waterlogging_suppression_at_saturation':.8,
                 'photoperiod_min_hours':11. if arch=='bulb'else 0.,'chilling_required_days':30. if ident=='garlic'else 0.,
                 'product_id':ident+'_'+group_product+'_dry','product_organ':group_product,
                 'lifecycle_scope':'persistent herbaceous cutting/stem system; no woody, CAM, orchard or sucker inference'if arch in ['forage','sugar_stem']else
                   'planted first-cycle storage crop with destructive harvest, no ratoon/suckering'if ident in ['cassava','taro','tanier','sweet_potato','potato']else
                   'spring or warm-season cultivar scenario; no winter-cultivar vernalization implied',
                 'reference_days':DAYS[ident],'reference_mean_temperature_C':t,
                 'photosynthesis_scope':'Generic existing RUE response; no crop-specific photosynthesis/CO2 curve',
                 'host_compatibility':{},'product_field':'product_dry_kg_ha'}
            ext['requires_pegging']=ident in ['peanut','bambara_groundnut']
            ext['pegging_gdd']=150. if ext['requires_pegging']else 0.
            if ident=='hemp':ext['product_organ']='raw_fiber_bearing_stem';ext['product_id']='hemp_raw_stem_dry'
            if ident=='cotton':ext['product_organ']='unginned_bolls';ext['product_id']='cotton_unginned_bolls_dry'
            if ident=='leek':ext['product_organ']='leaf_and_pseudostem';ext['product_id']='leek_shoot_dry'
            if ext['requires_pegging']:ext['product_organ']='underground_pods';ext['product_id']=ident+'_underground_pods_dry'
            status='EXECUTABLE_EXPERIMENTAL';parameter_evidence={}
            for k in p:
                if k=='name':continue
                parameter_evidence[k]={'tier':'C'if p[k]==asdict(default_crops()[donor]).get(k)else'D',
                    'source':'existing'if p[k]==asdict(default_crops()[donor]).get(k)else'scenario',
                    'donor':donor,'scope':'Transferred default or explicit archetype/crop scenario, not fitted/measured for this crop'}
            for k,v in ext.items():
                if isinstance(v,(int,float))and not isinstance(v,bool):
                    parameter_evidence['archetype.'+k]={'tier':'D','source':'scenario','scope':'Declared hypothetical process parameter, not a measured universal crop trait'}
        else:
            p=None;ext={};status='CATALOG_ONLY';arch='blocked_'+('paddy'if ident=='rice'else'unresolved_identity'if choice['taxonomy_ambiguity']else'CAM'if ident=='pineapple'else'perennial_fruiting')
            parameter_evidence={}
        requirements={'climate':env,'soil':{'pH':None,'salinity':None,'nutrient_PK_effects':None},
            'water':{'source':'actual shared layer stocks and declared root/uptake profile'if p else None,'irrigation_requirement':None},
            'nitrogen':{'plant_fraction':p['plant_n_fraction']if p else None,'fixation_kg_ha_day':p['fixation_max_n_kg_ha_day']if p else None},
            'residue':{'C_fraction':.45 if p else None,'N_from':'actual organ N stocks, no imposed empirical C:N'if p else None},
            'life_cycle':'perennial_herbaceous'if ext.get('perennial')else'annual_or_first_crop_cycle'if p else'unsupported'}
        result[ident]={'identifier':ident,'common_name':choice['common_name'],'taxonomy':taxonomy,'family':taxonomy['family']if taxonomy else None,
            'taxonomy_ambiguity':choice['taxonomy_ambiguity'],'source_choice':deepcopy(choice),'archetype':arch,'implementation_status':status,
            'validation_status':'no_new_empirical_validation','crop_parameters':p,'archetype_parameters':ext,'parameter_provenance':parameter_evidence,
            'requirements':requirements,'blockers':BLOCKERS.get(ident,[]),'financial_support':'regional_or_class_proxies'if ident in REFERENCE else'unavailable_until_compatible_price_cost_conversion_inputs',
            'farmer_recommendation':None,'global_cultivar_scope_claimed':False}
        for value in parameter_evidence.values():
            value['empirical_uncertainty_interval']=None
            value['identifiability']='Not estimated from independent local observations; no probability distribution inferred'
    if len(result)!=88 or set(result)!=(set(assignment)|set(REFERENCE)|set(BLOCKERS)):raise ValueError('Exact88 requested crops required; unresolved assignment')
    for r in result.values():
        if r['crop_parameters']:Crop(**r['crop_parameters']).validate()
    return result


class CropRegistry88:
    def __init__(self):self.crops=profiles();self.choices=CropChoiceRegistry()
    def resolve(self,name):return deepcopy(self.crops[self.choices.resolve(name)['identifier']])
    def require(self,name):
        r=self.resolve(name)
        if not r['crop_parameters']:raise UnsupportedCropError(r['common_name']+': '+'; '.join(r['blockers']))
        return r
    def parameters(self,names):
        return {r['crop_parameters']['name']:Crop(**r['crop_parameters'])for r in [self.require(n)for n in names]}
