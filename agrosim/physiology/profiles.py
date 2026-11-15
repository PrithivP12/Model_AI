"""Explicit biological families and experimental parameter declarations.

Sources support the named structures/processes, not arbitrary coefficients.
No values in these new profiles have been fitted to field harvest outcomes.
"""
from copy import deepcopy
from dataclasses import asdict,replace

from ..crops.profiles import profiles as original_profiles
from ..processes import default_crops

SOURCES={
 'scallion':('UMN, Growing scallions','https://extension.umn.edu/garden-and-home/yard-and-garden/gardening-in-minnesota/growing-scallions-in-home-gardens','Allium fistulosum bunching crop versus other green-onion uses'),
 'brachiaria':('Tropical Forages, Urochloa brizantha','https://www.tropicalforages.info/text/entities/urochloa_brizantha.htm','Species-specific perennial forage; no universal Brachiaria identity'),
 'ruziziensis':('Tropical Forages, Urochloa ruziziensis','https://tropicalforages.info/text/entities/urochloa_ruziziensis.htm','Distinct tropical forage species'),
 'ryegrass':('Penn State Extension, Ryegrass','https://extension.psu.edu/ryegrass','Italian and perennial ryegrass have distinct persistence'),
 'apple':('Maryland Extension, fruit garden chilling','https://www.extension.umd.edu/resource/starting-home-fruit-garden','Cultivar-dependent chilling; no universal apple threshold'),
 'grape':('UGA, dormant pruning bunch grapevines','https://extension.uga.edu/publications/detail.html?number=B1505&title=dormant-spur-and-cane-pruning-bunch-grapevines','Persistent cordon, one-year wood and retained reproductive buds'),
 'olive':('UC, olive flower biology','https://ucanr.edu/sites/default/files/2011-03/80688.pdf','Evergreen olive floral induction is cultivar-dependent; not apple dormancy'),
 'strawberry':('UMN, strawberry lifecycle','https://extension.umn.edu/garden-and-home/yard-and-garden/gardening-in-minnesota/growing-strawberries-in-the-home-garden','Crown, stolons; short-day versus day-neutral cultivars'),
 'mango':('UF IFAS, Mango Growing','https://ask.ifas.ufl.edu/publication/MG216.pdf','Persistent tree, seasonal induction and fruit development'),
 'guava':('UF IFAS, Guava Growing','https://ask.ifas.ufl.edu/publication/MG045','Perennial shoots and recurrent fruiting'),
 'cacao':('UF IFAS, Cocoa Growing','https://ask.ifas.ufl.edu/publication/HS307','Persistent wood, pollination, pods; dry beans require a separate conversion'),
 'banana':('UF IFAS, Banana Growing','https://ask.ifas.ufl.edu/publication/MG040','Corm, one bunch per pseudostem, successor suckers and parent residue'),
 'papaya':('UF IFAS, Papaya Growing','https://ask.ifas.ufl.edu/publication/MG054','Persistent herbaceous stem, continuing fruits and sex/pollination constraints'),
 'avocado':('UF IFAS, Avocado Growing','https://ask.ifas.ufl.edu/publication/MG213','Perennial structure, flowering, flooding sensitivity'),
 'passion_fruit':('UF IFAS, Passion Fruit','https://ask.ifas.ufl.edu/publication/hs1406','Persistent vine, fruit cohorts and canopy growth'),
 'citrus':('UF IFAS, Improving Flowering in Citrus','https://ask.ifas.ufl.edu/publication/HS1399','Cool or drought induction, carbohydrate status, repeated lemon bloom'),
 'lemon':('UF IFAS, Lemon Growing','https://ask.ifas.ufl.edu/publication/HS402','Evergreen woody citrus, cold sensitivity and harvest'),
 'coconut':('Philippines Department of Agriculture, Coconut course','https://ati2.da.gov.ph/ati-7/content/sites/default/files/2023-09/Coconut%20Specialist%20Course%20%28ISBN%29.pdf','Persistent palms and approximately year-long fruit maturation'),
 'date':('FAO, Date palm technical calendar','https://www.fao.org/4/Y4360E/y4360e0f.htm','Mature palms, explicit pollination and seasonal fruit management'),
 'rice':('FAO, Agronomic factors in wetland rice','https://www.fao.org/4/x5648e/x5648e0e.htm','Bund storage, water balance, wetting/drying and different flooded-soil chemistry'),
 'rice_N':('Texas A&M, rice production guidelines','https://varietytesting.tamu.edu/wp-content/uploads/sites/17/legacy-files/rice/varietyfiles/2008_Rice_Production_Guidelines.pdf','Ammonium/nitrate distinction and nitrate loss in flooded soil'),
 'pineapple':('UF IFAS, Pineapple Growing','https://ask.ifas.ufl.edu/publication/MG055','Rosette induction, plant crop and ratoon, 5–7 months induction to harvest'),
 'CAM':('Diurnal metabolite study in Ananas, Journal of Experimental Botany','https://academic.oup.com/jxb/article/53/367/341/430609','Night malate accumulation and day decarboxylation; no gas-exchange coefficients transferred'),
 'stress':('UF IFAS, environmental fruit factors','https://ask.ifas.ufl.edu/publication/HS1499','Crop-specific temperature/flooding responses; no new dose-response fitting'),
}

SUBTYPES={
 'scallion':{'fistulosum':'Allium fistulosum; bunching onion, destructive shoot harvest',
              'cepa_green':'Allium cepa; immature green-onion use, not a mature bulb crop'},
 'brachiaria':{'brizantha':'Urochloa brizantha; persistent tropical cutting forage',
               'ruziziensis':'Urochloa ruziziensis; persistent tropical cutting forage'},
 'ryegrass':{'perenne':'Lolium perenne; perennial cutting forage',
             'multiflorum':'Lolium multiflorum; declared annual/Italian cutting system, terminate after one season'},
}

# family, base/optimum/maximum C, juvenile days, flowering interval days,
# fruit-development days, initial structural threshold kg dry/ha, source key.
# These are EXPLORATORY configuration values (Tier D), not empirical bounds.
SPECS={
 'apple':('deciduous_wood',4,24,38,730,365,150,500,'apple'),
 'grape':('deciduous_wood',5,25,40,365,365,120,200,'grape'),
 'olive':('evergreen_wood',7,25,40,730,365,180,500,'olive'),
 'mango':('evergreen_wood',10,28,42,730,365,150,500,'mango'),
 'orange':('evergreen_wood',8,26,40,730,365,240,500,'citrus'),
 'lemon':('evergreen_wood',8,26,40,730,120,180,500,'lemon'),
 'avocado':('evergreen_wood',8,25,38,730,365,240,500,'avocado'),
 'guava':('evergreen_wood',10,28,42,365,120,150,300,'guava'),
 'cacao':('evergreen_wood',12,27,38,730,60,180,300,'cacao'),
 'passion_fruit':('perennial_vine',8,26,38,180,60,90,100,'passion_fruit'),
 'coconut':('palm',12,28,42,1825,45,365,1500,'coconut'),
 'date':('palm',10,30,45,1460,365,210,1500,'date'),
 'banana':('successor_herb',10,28,40,180,1,120,150,'banana'),
 'papaya':('persistent_herb',10,28,40,180,30,180,150,'papaya'),
 'strawberry':('crown_stolon',3,22,34,60,21,45,20,'strawberry'),
 'pineapple':('CAM_rosette',10,28,42,450,1,180,30,'pineapple'),
 'rice':('paddy_cereal',10,28,42,0,1,90,0,'rice'),
}


def resolve(identifier,*,subtype=None):
    original=original_profiles()
    if identifier not in original:raise ValueError('Unknown canonical crop choice')
    if original[identifier]['crop_parameters']:return deepcopy(original[identifier])
    if identifier in SUBTYPES:
        if subtype not in SUBTYPES[identifier]:raise ValueError(identifier+' requires explicit subtype: '+', '.join(SUBTYPES[identifier]))
        donor='leek'if identifier=='scallion'else'alfalfa'if identifier=='ryegrass'else'napier_grass'
        p=deepcopy(original[donor]);p.update(identifier=identifier,common_name=original[identifier]['common_name'],
            taxonomy=deepcopy(original[identifier]['taxonomy']),source_choice=deepcopy(original[identifier]['source_choice']),
            selected_subtype=subtype,identity_scope=SUBTYPES[identifier][subtype],blockers=[],validation_status='experimental_not_field_validated')
        p['crop_parameters']['name']=identifier
        c=p['crop_parameters'];a=p['archetype_parameters']
        c.update(fixation_max_n_kg_ha_day=0.,plant_n_fraction=.025,grain_n_fraction=.025)
        a.update(requires_pegging=False,pegging_gdd=0.,chilling_required_days=0.,photoperiod_min_hours=0.,
                 product_id=identifier+'_shoot_dry',product_organ='leaf_and_pseudostem'if identifier=='scallion'else'cut_forage')
        if identifier=='scallion':
            a.update(perennial=False,repeated_harvest=False,reference_days=75,harvest_ready_fraction=1.)
            c['maturity_gdd']=1000.
        elif identifier=='ryegrass':
            a.update(perennial=subtype=='perenne',reference_days=60,harvest_interval_days=50)
            c.update(base_temp_C=3.,optimum_temp_C=22.,maximum_temp_C=35.,maturity_gdd=650.)
        else:
            c.update(base_temp_C=10.,optimum_temp_C=28.,maximum_temp_C=42.,maturity_gdd=800.,max_root_depth_m=1.2)
        p['physiology_family']='declared_identity_existing_'+p['archetype']
        p['parameter_provenance']={k:{'tier':'C'if k not in ['name','maturity_gdd']else'D','units':UNITS.get(k,'declared profile field'),
             'source':donor,'applicability':'Explicit species/crop-use subtype; transferred/exploratory profile, not fitted'}for k in c}
        p['parameter_provenance']['subtype']={'tier':'B','source':identifier,'units':'identity','applicability':SUBTYPES[identifier][subtype]}
        return p
    family,base,opt,maximum,juvenile,interval,fruit_days,threshold,source=SPECS[identifier]
    p=deepcopy(original[identifier]);donor=default_crops()['spring_wheat']
    crop=replace(donor,name=identifier,base_temp_C=float(base),optimum_temp_C=float(opt),maximum_temp_C=float(maximum),
        maturity_gdd=1800. if identifier=='rice'else 3000.,max_root_depth_m=.6 if identifier in ['pineapple','strawberry','rice']else 1.5,
        root_biomass_fraction=.18,rue_g_dry_per_MJ_PAR=2.5,max_lai=6.,specific_leaf_area_m2_kg=20.,
        vegetative_leaf_fraction=.5,plant_n_fraction=.018,grain_n_fraction=.015,seed_dry_kg_ha=100.,
        uptake_max_n_kg_ha_day=4.,fixation_max_n_kg_ha_day=0.,
        kill_minimum_temp_C=-35. if identifier=='apple'else-20. if identifier=='grape'else-12. if identifier=='strawberry'else-7. if identifier=='olive'else-3.)
    values={'juvenile_days':float(juvenile),'flower_interval_days':float(interval),'fruit_days':float(fruit_days),
        'minimum_structure_dry':float(threshold),'root_half_mass_dry':300.,'reserve_fraction':.15,'root_fraction':.18,
        'stem_fraction':.25,'fruit_fraction':.35,'leaf_turnover_day':.003,'root_turnover_day':.0003,
        'reserve_mobilization_day':.025,'leaf_recovery_LAI':.6,'bud_fraction':.02,'budbreak_gdd':100.,
        'chill_hours_required':600. if identifier=='apple'else 300. if identifier=='grape'else 0.,
        'chill_min_C':0.,'chill_max_C':7.2,'dormancy_temperature_C':10.,'dormancy_daylength_h':11.,
        'cool_induction_max_C':20.,'cool_induction_days':30.,'dry_induction_days':21.,
        'flower_gdd':80.,'pruning_fraction':0.,'runner_fraction':.06 if identifier=='strawberry'else 0.,
        'successor_start_gdd':700.,'stem_flowering_gdd':2200.,'successor_seed_dry':10.,
        'aerenchyma_fraction':.9 if identifier=='rice'else 0.,'night_conductance_mol_m2_s':.05,
        'leaf_n_fraction':.025,'root_n_fraction':.012,'structural_n_fraction':.004,'fruit_n_fraction':.012,
        'leaf_N_resorption_fraction':.5,
        'CO2_gradient_mol_mol':.0002,'pressure_kPa':101.3,'malate_capacity_C_per_leaf_dry':.08,
        'initial_age_days':0.}
    p.update(crop_parameters=asdict(crop),archetype='special_'+family,physiology_family=family,
        physiology_parameters=values,implementation_status='EXECUTABLE_EXPERIMENTAL',validation_status='experimental_not_field_validated',
        blockers=[],archetype_parameters={'perennial':identifier!='rice','repeated_harvest':identifier!='rice',
          'product_id':identifier+'_rough_grain_dry'if identifier=='rice'else identifier+'_whole_pod_dry'if identifier=='cacao'else identifier+'_whole_fruit_dry',
          'product_organ':'rough_grain_with_husk'if identifier=='rice'else'whole_pod'if identifier=='cacao'else'whole_fruit',
          'lifecycle_scope':'Opt-in declared cultivar/propagule and reproductive assumptions; not a universal species prediction'},
        requirements=deepcopy(original[identifier]['requirements']),unavailable=unavailable(identifier))
    p['parameter_provenance']={k:{'tier':'C'if value==asdict(donor).get(k)else'D','units':UNITS.get(k,'declared crop field'),'source':'existing V1 spring-wheat default or explicit experimental declaration',
        'applicability':'Transferred model coefficient; no new crop-specific field calibration'}for k,value in asdict(crop).items()}
    for k,v in values.items():p['parameter_provenance']['physiology.'+k]={'tier':'D','units':UNITS.get(k,'fraction'if 'fraction'in k else'declared scenario units'),
        'source':'Explicit exploratory scenario','applicability':'Requires cultivar/stock/environment justification; not measured universal trait'}
    for k in ['base_temp_C','optimum_temp_C','maximum_temp_C','maturity_gdd','kill_minimum_temp_C','seed_dry_kg_ha']:
        p['parameter_provenance'][k]['tier']='D'
    p['mechanism_sources']=[source,'stress']+(['CAM']if identifier=='pineapple'else['rice_N']if identifier=='rice'else[])
    return p


def unavailable(identifier):
    return {'independent_field_accuracy':None,'local_yield_interval':None,'local_pest_abundance':None,
        'methane_flux':None,'phosphorus_response':None,'salinity_response':None,'regional_profit':None,
        'alternate_bearing_prediction':None,'detailed_hourly_CAM':None,'measured_soil_Eh_mV':None,
        'product_moisture_and_recovery':None,'crop_specific_pollinator_dynamics':None}


def coverage(subtypes=None):
    result={};subtypes=subtypes or {}
    for ident,p in original_profiles().items():
        if ident in SUBTYPES and ident not in subtypes:
            result[ident]={**p,'implementation_status':'REQUIRES_SUBTYPE','available_subtypes':SUBTYPES[ident],
                'numerical_prediction':None};continue
        result[ident]=resolve(ident,subtype=subtypes.get(ident))
    return result


UNITS={'base_temp_C':'degC','optimum_temp_C':'degC','maximum_temp_C':'degC','kill_minimum_temp_C':'degC',
 'maturity_gdd':'degC day','max_root_depth_m':'m','rue_g_dry_per_MJ_PAR':'g dry/MJ PAR','specific_leaf_area_m2_kg':'m2/kg dry',
 'seed_dry_kg_ha':'kg dry/ha','uptake_max_n_kg_ha_day':'kg N/ha/day','fixation_max_n_kg_ha_day':'kg N/ha/day',
 'juvenile_days':'day','flower_interval_days':'day','fruit_days':'day','minimum_structure_dry':'kg dry/ha',
 'root_half_mass_dry':'kg dry/ha','leaf_turnover_day':'1/day','root_turnover_day':'1/day','reserve_mobilization_day':'1/day',
 'leaf_recovery_LAI':'m2/m2','budbreak_gdd':'degC day','chill_hours_required':'hour','chill_min_C':'degC','chill_max_C':'degC',
 'dormancy_temperature_C':'degC','dormancy_daylength_h':'hour','cool_induction_max_C':'degC','cool_induction_days':'day',
 'dry_induction_days':'day','flower_gdd':'degC day','successor_start_gdd':'degC day','stem_flowering_gdd':'degC day',
 'successor_seed_dry':'kg dry/ha','night_conductance_mol_m2_s':'mol H2O/m2 leaf/s','CO2_gradient_mol_mol':'mol/mol',
 'pressure_kPa':'kPa','malate_capacity_C_per_leaf_dry':'kg C/kg leaf dry','initial_age_days':'day',
 'plant_n_fraction':'kg N/kg dry','grain_n_fraction':'kg N/kg dry','fixation_inhibition_n_kg_ha':'kg N/ha'}
from ..crops.validation import PARAM_UNITS
UNITS={**PARAM_UNITS,**UNITS,**{k:'kg N/kg dry'for k in ['leaf_n_fraction','root_n_fraction','structural_n_fraction','fruit_n_fraction']}}
