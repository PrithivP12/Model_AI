"""Disabled ecological evidence contracts; no organisms or penalties activated."""
from copy import deepcopy

CONTRACTS={
 'residue_host_disease':{
    'state_variables':['host-compatible viable inoculum by residue/soil support','infected functional leaf/stalk biomass','host susceptibility'],
    'interactions':['host-compatible infection','residue survival','lesion-linked canopy/senescence or lodging with explicit material fate'],
    'parameters_needed':['independently measured local inoculum','temperature/moisture response','infection/lesion progression','genotype resistance','residue survival'],
    'validation_targets':['dated local disease incidence/severity','inoculum/residue samples','green leaf loss','lodged and unharvested grain'],
    'source':'https://doi.org/10.1094/PDIS-11-10-0837',
    'evidence_limit':'Wisconsin field studies support a possible residue/disease pathway; they do not establish presence or explain this cohort.'},
 'root_feeding_pest':{
    'state_variables':['local life-stage density and body C/N','root biomass and functional root geometry','host compatibility and rotation-adaptation phenotype'],
    'interactions':['root material consumption','predation/mortality to tracked pools','root-linked uptake effects'],
    'parameters_needed':['independent presence and counts','life-stage timing','feeding/assimilation/respiration fluxes','root injury to uptake relationship'],
    'validation_targets':['root injury scores','larval/adult counts','root dry mass','water/N uptake'],
    'source':'https://doi.org/10.1073/pnas.1301886110',
    'evidence_limit':'Rotation-adapted rootworms are documented; soybean history is not a universal presence/absence rule.'},
 'microbial_function':{
    'state_variables':['active microbial C and N within conserved inventories','substrate quality and accessible mineral N'],
    'interactions':['substrate consumption','microbial immobilization/mineralization','respiration and turnover'],
    'parameters_needed':['measured microbial stocks','carbon-use/humification yield','substrate-specific turnover','gross N-transfer evidence'],
    'validation_targets':['residue mass/C/N loss','CO2 efflux','mineral N','microbial biomass','15N pool dilution where gross rates required'],
    'source':'https://doi.org/10.2134/agronj2012.0246',
    'evidence_limit':'Community association or diversity is not a calibrated rate. Existing C/N stoichiometry must be checked before adding compartments.'},
 'weed_competition':{
    'state_variables':['weed species/functional traits and seedbank','weed biomass C/N','overlapping root/canopy resource demand'],
    'interactions':['explicit shared light/water/N competition','management removal and residue return'],
    'parameters_needed':['local species/densities','emergence and resource demand','actual herbicide/tillage efficacy'],
    'validation_targets':['weed biomass/density through season','crop uptake/canopy','treatment removal records'],
    'source':'https://doi.org/10.1017/wet.2024.1',
    'evidence_limit':'Published long-term weed/fertility/tillage interactions are hypotheses here; local weeds and control efficacy are absent.'},
 'rooting_and_other_nutrients':{
    'state_variables':['depth-specific functional roots','measured compaction/hydraulics','P/K stocks if a future solver is justified'],
    'interactions':['root-limited water/nutrient uptake','measured tillage/structure changes'],
    'parameters_needed':['root profiles','dated bulk density/penetration resistance','complete fertilizer and nutrient analyses'],
    'validation_targets':['root depth/density','depth-equivalent moisture/N','tissue P/K','hydraulic response'],
    'source':'https://doi.org/10.2134/agronj2016.01.0005',
    'evidence_limit':'N-only nutrition and a coarse root profile cannot identify unmeasured P/K or compaction effects from a grain contrast.'},
}


def assess_contract(identifier,evidence=None):
    evidence=evidence or {}
    if evidence.get('inferred_from_rotation_yield_gap'):raise ValueError('A yield gap cannot establish organism presence or calibrate a missing ecological process')
    contract=deepcopy(CONTRACTS[identifier])
    return {**contract,'identifier':identifier,'enabled':False,'status':'unsupported_disabled',
        'missing_evidence':[k for k in contract['parameters_needed']if evidence.get(k)is None],
        'local_population':None,'yield_penalty':None,'resource_flux':None,
        'activation_blocker':'No independently calibrated conservative solver is registered. Host contracts belong to the existing trait architecture; this catalog does not register or simulate organisms.'}


def research_manifest():return {key:assess_contract(key)for key in CONTRACTS}
