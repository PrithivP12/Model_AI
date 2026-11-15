"""Fail-closed data availability and measurement-support checks for TD023.

Metadata ranges, species code lists and planned sampling are not observations.
This module never treats a registration page as a CSV or invents sample counts.
"""
import csv
from datetime import date
import io
import json
from pathlib import Path
import re

from ..io import number, sha256, write_json

TABLES = {
    'TD02301': ('Initial bag weights', ['SITE','REP','DURATION','SPECIES','TYPE','NIR_NUM','MESH','FILL_DATE','IADW','IODW']),
    'TD02303': ('Wet-chemistry measurements', ['SPECIES','BATCH','TYPE1','NIR_NUM','DUP','SAMPLEDATE','DURATION','REP','SITE','ASH','CARBON','NITROGEN','LIGNIN']),
    'TD02304': ('Monthly climate summaries', ['SITE','MEAS_MONTH','STARTYR','ENDYR','MEANTEMP','MINTEMP','MAXTEMP','PRECIP_TM']),
    'TD02306': ('Site descriptors', ['SITE','SITENAME','TEMP','PRECIP','AET','PET','BIOME']),
    'TD02307': ('Initial moisture correction', ['SPECIES','TYPE','FILL_DATE','MCF']),
    'TD02308': ('Bag placement and collection', ['SITE','REP','DURATION','SPECIES','TYPE','TAG_NUM','NIR_NUM','DATEOUT','DATEIN','WHERE_GO','ID_NR']),
    'TD02309': ('Mass-loss measurements', ['SITE','REP','SPECIES','TYPE','NIR_NUM','DATEOUT','IODW','DATEIN','FOW','IASH','FASH','IAFW','FAFW','PRM','PAFRM','TIMEOUT','COMMENT','FLAG']),
    'TD02310': ('Ash measurements and estimation flags', ['SITE','REP','SPECIES','TYPE','NIR_NUM','ASH','ASHFREE','EST','NIR_ASH','NIR_ASHFRE','NIR_EST']),
    'TD02311': ('Nutrient chemistry', ['SITE','REP','SPECIES','TYPE','NIR_NUM','STARTDATE','N']),
    'TD02312': ('Root initial-ash recalculation', ['SITE','REP','SPECIES','TYPE','NIR_NUM','IODW','FOW','IASH','FASH','I_ASH_MASS','I_RT_MASS','NEW_IASH']),
    'TD02313': ('Nitrogen retention', ['SITE','REP','SPECIES','TYPE','NIR_NUM','I_NITRO','AF_I_NITRO','F_NITRO','AF_F_NITRO','IAFW','FAFW','I_N_CONT','F_N_CONT','N_CONC','TIMEOUT','COMMENT']),
}
AGRICULTURAL_CODES = {'COST': 'Zea mays stover', 'SOST': 'Glycine max stover', 'WHST': 'Triticum stover',
                      'GLMA': 'Glycine max', 'TRAE': 'Triticum aestivum'}
REQUIRED = ['TD02301','TD02303','TD02304','TD02306','TD02309','TD02313']


def metadata_inventory(text):
    entities = {}
    for part in re.split(r'^## Entity ', text, flags=re.M)[1:]:
        name = re.match(r'\d+:\s*(TD023\d+)', part)
        if not name: continue
        attrs = {}
        for match in re.finditer(r'^- \*\*([A-Z][A-Z0-9_]*)\*\* — ((?:varchar|numeric|datetime)[^\n]*)\n\n([^\n]*)', part, flags=re.M):
            attrs.setdefault(match[1], {'type_and_units': match[2], 'description': match[3]})
        entities[name[1]] = attrs
    inventory=[]
    for name,(role,fields) in TABLES.items():
        attrs=entities.get(name,{})
        inventory.append({'entity':name,'role':role,'fields':{f:attrs.get(f) for f in fields},
                          'missing_documented_fields':[f for f in fields if f not in attrs]})
    return inventory


def require_data_csv(path, required_columns):
    """Availability gate only; does not interpret any target or final outcome."""
    path=Path(path)
    if not path.is_file():raise FileNotFoundError('Raw CSV unavailable: '+path.name)
    content=path.read_bytes()
    lower=content[:1024].lower().lstrip()
    if b'<html' in lower or b'<!doctype' in lower:
        raise ValueError('Registration/HTML response is not a scientific data table')
    reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    fields={f.upper().strip() for f in (reader.fieldnames or [])}
    if not set(required_columns)<=fields:
        raise ValueError('Required observation identity/measurement columns missing')
    return {'path':path.name,'sha256':sha256(path),'header_verified':True}


def elapsed_days(deployed, collected):
    """Elapsed exposure is from deployment, never the bag-fill date."""
    days=(date.fromisoformat(collected)-date.fromisoformat(deployed)).days
    if days<=0:raise ValueError('Collection must follow deployment')
    return days


def ash_free_remaining(initial_ash_free_g, final_ash_free_g):
    """Direct compatible masses; do not guess the ambiguous percent convention."""
    number(initial_ash_free_g,'initial ash-free mass g',.000001)
    number(final_ash_free_g,'final ash-free mass g',0)
    return final_ash_free_g/initial_ash_free_g  # >1 is retained/flagged, not clipped.


def require_viability_counts(counts):
    """A later phase may only fit after actual observed support is verified."""
    if any(counts.get(k) is None for k in ['eligible_samples','independent_sites','repeated_site_substrate_trajectories']):
        raise ValueError('Unknown observation counts cannot authorize model fitting')
    if counts['independent_sites']<8 or counts['repeated_site_substrate_trajectories']<16:
        raise ValueError('Insufficient independent sites/repeated trajectories for this proposed evaluation')
    return True


def run(root, output):
    root=Path(root);phase=root/'reports/ecosystem-v24';raw=phase/'raw'
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    metadata=(raw/'metadata.txt').read_text()
    inventory=metadata_inventory(metadata)
    access=json.loads((raw/'manifest.json').read_text());original=json.loads((raw/'original_source_access.json').read_text())
    files={};failures={}
    for name in REQUIRED:
        fields=TABLES[name][1]
        try:files[name]=require_data_csv(raw/(name+'.csv'),fields)
        except (FileNotFoundError,ValueError) as exc:failures[name]=str(exc)
    # This phase's evidence supports the access-blocked branch only. Never
    # infer viability or skip a protocol because a new file happens to appear.
    if not failures:raise ValueError('Actual files are now present: perform a new explicit viability/identifier audit before defining a model or final split')
    counts={'eligible_samples':None,'independent_sites':None,'eligible_substrates':None,
        'repeated_site_substrate_trajectories':None,'agricultural_residue_samples':None,
        'maize_samples':None,'soybean_samples':None,'wheat_samples':None,'compatible_nitrogen_pairs':None}
    result={'status':'stage1_access_blocked_no_model_implemented','decision':'insufficient evidence',
        'source':{'dataset':'LIDET TD023','version':11,'doi':'10.6073/pasta/f35f56bea52d78b6a1ecf1952b4889c5',
                  'citation':'Andrews Forest LTER Site and M.E.Harmon.2016.LTER Intersite Fine Litter Decomposition Experiment(LIDET),1990–2002,ver11,Environmental Data Initiative.',
                  'license':'CC-BY4.0; attribution and processing changes required'},
        'catalog_claims_not_verified_counts':{'catalog_geographic_coverage_sites':21,'historical_method_design_sites':28,
            'standard_substrate_design':'Six leaf litters,three fine-root litters and dowels; plus wildcard litters',
            'planned_sampling':'Ten collections,annual except quarterly tropical sites,four replicates; actual eligible rows unknown'},
        'actual_observation_counts':counts,'viability_confirmed':False,
        'agricultural_residue_dictionary_codes':AGRICULTURAL_CODES,
        'codes_are_observed_crop_counts':False,'metadata_inventory':inventory,
        'availability':{'verified_CSV_files':files,'missing_files':failures,'portal_attempts':access,'original_endpoints':original,
            'EDI_metadata':'Public request returned403; no authentication workaround attempted.',
            'registration':'User specified education_k12 affiliation; optional name/email blank. Registration not submitted because browser access is disabled. No affiliation or data-use agreement falsely recorded as submitted.',
            'browser_access':'Local browser use disabled in Work settings; no screenshot or UI artifact created.'},
        'observation_contract':{'primary_if_supported':'Ash-free oven-dry mass remaining: FAFW/IAFW, corroborated against PAFRM/100 without silently choosing contradictory records.',
            'initialization':'Use each actual initial oven-dry/ash-free mass. Never use later measurements to initialize an earlier trajectory.',
            'dry_mass':'Initial air-dry bags corrected using documented conversion; final material oven dried at55C. Initial moisture correction is not optional.',
            'ash_warning':'Metadata describes IASH/FASH as percent but shorthand IAFW=IODW*IASH is ambiguous. Prefer explicit IAFW/FAFW and original calculation notes; verify raw conventions before deriving masses.',
            'root_warning':'TD02312 root initial-ash reconstruction depends on final ash. Treat as a retrospective derived correction,not a measured pre-deployment predictor. Separate or exclude from forecasting.',
            'nitrogen':'TD02313 N_CONC is F_N_CONT/I_N_CONT,not simply nitrogen concentration. Allow retention>1 where supported; field N accumulation is possible.',
            'chemistry':'Use only true initial-material chemistry(duration0/initial flags once verified). Never join post-collection NIR/lignin or pooled future samples as starting quality.',
            'identifiers':'NIR_NUM plus site,replicate,species,type,duration must agree. RepI/P and DUP encode initial/pooled/repeat samples and are not independent field bags.',
            'timing':'DATEOUT/DATEIN determine elapsed exposure; DURATION is planned, FILL_DATE is not deployment. Placement corrections/WHERE_GO and flags require auditing.',
            'flags':'Torn/missing/foreign-material/uncertain-tag codes and FLAG X must be reviewed before eligibility; no target clipping.',
            'environment':'General site TEMP/PRECIP/AET/PET and monthly climate periods are not bag microclimate. Overlapping future-period climatology must not be labelled a deployment-time forecast input.',
            'mass_fate':'Missing dry mass is not measured CO2,organic C stabilization or N mineralization.'},
        'predictive_results':{'constant':None,'training_first_order':None,'existing_AgroSim':None,'quality_aware_candidate':None,
            'reserved_groups':None,'remaining_mass_error':None,'nitrogen_retention_error':None,'cross_site_error':None},
        'model_fit_performed':False,'new_predictive_model_implemented':False,'evaluation_split_frozen':False,
        'final_outcomes_opened':False,'old_reserved_boundaries_changed':False,
        'optional_integration_justified':False,'coupling_activated':False,'stable_defaults_changed':False,
        'next_action':'Complete the one-time official registration as K–12 and provide the six required TD023 CSV files; then rerun actual viability before defining any predictive model.'}
    write_json(output/'feasibility.json',result)
    with (output/'table_inventory.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=['entity','role','field','type_units','definition']);writer.writeheader()
        for row in inventory:
            for name,field in row['fields'].items():writer.writerow({'entity':row['entity'],'role':row['role'],'field':name,
                'type_units':field['type_and_units'] if field else None,'definition':field['description'] if field else None})
    write_json(output/'artifact_manifest.json',{'sha256':{str(p.relative_to(output)):sha256(p) for p in sorted(output.rglob('*'))if p.is_file()}})
    return result
