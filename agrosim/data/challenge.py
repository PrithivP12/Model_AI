"""Common contract for all challenge routes, with product-specific safety exclusions."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import json,math
import pandas as pd
from fieldshift.challenge_catalog import FAMILIES,CMR
from ..io import sha256,write_json
from .quality import eligible


def normalize_product(product,document,location,decision_date,raw_path,verified_at):
    rows=[];issues=[]
    for original in document.get('observations',[]):
        value=original.get('value');unit=original.get('unit');variable=original.get('feature')
        if not isinstance(value,(int,float))or not math.isfinite(value):issues.append('missing_or_nonfinite');continue
        if abs(original.get('requested_latitude',999)-location['latitude'])>.002 or abs(original.get('requested_longitude',999)-location['longitude'])>.002:
            raise ValueError('Source observations do not match requested point')
        raw_value=value;raw_unit=unit;quality=original.get('quality','unverified');normalization='identity; raw unit retained'
        timestamp=pd.Timestamp(original['observation_date'])
        timestamp=timestamp.tz_localize('UTC')if timestamp.tzinfo is None else timestamp.tz_convert('UTC')
        start=timestamp.to_pydatetime()
        end=start;native=original.get('raster',{}).get('resolution')or original.get('cellsize')or original.get('grid')
        if unit in ['cm**3/cm**3','m3/m3','m^3/m^3']:
            unit='m3/m3'
            if not 0<=value<=1:quality='flagged'
        if unit=='K':value-=273.15;unit='C';normalization='Kelvin minus 273.15'
        if product in ['modis','viirs']:
            end=start+timedelta(days=16)
            if 'NDVI'in str(variable)and not -1<=value<=1:quality='flagged'
        if product=='imerg'and variable=='precipitation'and unit=='mm/day':
            # Selected source is GPM_3IMERGDF V07 daily, not half-hourly IMERG.
            if 'GPM_3IMERGDF.07'not in original.get('source_file',''):quality='unverified'
            else:unit='mm';normalization='documented UTC daily mean mm/day × one full UTC day';end=start+timedelta(days=1)
        if product=='landsat':
            # The legacy route did not extract QA_RADSAT. Never promote that result.
            quality='unverified';issues.append('radiometric_saturation_QA_not_in_recorded_sample')
        if product=='sentinel2'and unit=='DN':
            quality='unverified';issues.append('DN_offset_and_quantification_not_verified')
        if product in ['ssurgo','web_soil_survey','statsgo2']:
            issues.append('map_unit_component_horizon_joins_and_bottom_depth_required_before_stock_initialization')
        if product=='gedi_biomass':issues.append('woody_landscape_context_not_crop_biomass')
        row={'site_id':location['site_id'],'field_id':location.get('field_id'),'source_id':product,
            'product_id':original.get('source_product')or CMR.get(product)or product,'product_version':None,
            'variable':variable,'value':float(value),'unit':unit,'raw_value':raw_value,'raw_unit':raw_unit,
            'time_start':start.isoformat(),'time_end':end.isoformat(),'available_at':verified_at,
            'availability_basis':'conservative time this local numerical artifact was verified; not assumed observation-time release',
            'native_resolution':json.dumps(native),'aggregation_method':normalization,
            'quality_status':quality,'valid_fraction':None,'uncertainty':None,
            'geometry_or_footprint':json.dumps(original.get('measurement_geometry')or {'requested_point':[location['longitude'],location['latitude']]}),
            'representativeness':'source footprint/context; no field-scale precision created by resampling',
            'raw_asset_id':sha256(raw_path),'processing_version':'agrosim-challenge-v1',
            'source_details_json':json.dumps(original,default=str),'decision_eligible':False,'model_use':'context_only_not_fitted'}
        row['decision_eligible']=eligible(row,decision_date)
        rows.append(row)
    return rows,sorted(set(issues))


def integrate_challenge(config,base):
    from ..io import resolve
    root=resolve(base,config['recorded_results_root']);out=resolve(base,config['output_dir']);out.mkdir(parents=True,exist_ok=True)
    location=config['location'];now=datetime.now(timezone.utc).isoformat();all_rows=[];states=[]
    for family,label,products in FAMILIES:
        for product in products:
            path=root/f'{product}.json'
            if product=='web_soil_survey':
                states.append({'resource':label,'source_id':product,'state':'shared_upstream','duplicate_of':'ssurgo','observations':0});continue
            if not path.exists():
                states.append({'resource':label,'source_id':product,'state':'no_local_numerical_result','observations':0});continue
            document=json.loads(path.read_text());rows,issues=normalize_product(product,document,location,config['decision_date'],path,now)
            all_rows+=rows
            states.append({'resource':label,'source_id':product,'state':'normalized_context'if rows else document.get('status','no_data'),
                'observations':len(rows),'eligible_after_quality_and_cutoff':sum(r['decision_eligible']for r in rows),
                'sha256':sha256(path),'role':config.get('roles',{}).get(product,'source-specific context; not a yield truth or an automatic rotation benefit'),
                'remaining_controls':issues,'model_consumed':False})
    frame=pd.DataFrame(all_rows)
    if frame.empty:raise ValueError('No real numerical source observations found')
    frame.to_parquet(out/'field_context.parquet',index=False);frame.to_csv(out/'field_context.csv',index=False)
    write_json(out/'source_states.json',states)
    report={'status':'all_challenge_routes_accounted_for','families':len(FAMILIES),'product_routes':len(states),
        'numeric_context_observations':len(frame),'eligible_after_quality_and_cutoff':int(frame.decision_eligible.sum()),
        'source_products_with_numeric_context':int(frame.source_id.nunique()),'wss_ssurgo_deduplicated':True,
        'all_sources_model_integrated':False,'sources':states,'forcing_in_active_example':'NASA POWER through its product-specific daily adapter',
        'limitations':['Context normalization is not predictive benefit. Unverified QA and unknown scale/offset stay ineligible.',
            'No post-decision vegetation or ET is used as counterfactual crop truth.',
            'Market, soil, crop-history and land-cover records need their own verified domain mappings before numerical control of the crop model.',
            'Missing sources remain explicit failed/unavailable routes; no synthetic or unrelated source substitution.']}
    write_json(out/'report.json',report);return report
