"""Use existing satellite observations with explicit QA/time/volume exclusions."""
from pathlib import Path
from datetime import date
import json,math
from ..io import write_json,sha256


def screen_landsat(observations):
    by={r['feature']:r for r in observations}
    blockers=[]
    for key in ['red','nir08','qa_pixel','qa_radsat']:
        if key not in by:blockers.append('missing_'+key)
    for key in ['red','nir08']:
        if key in by and(by[key]['unit']!='reflectance'or not isinstance(by[key]['value'],(int,float))or not math.isfinite(by[key]['value'])or not 0<=by[key]['value']<=1):blockers.append('invalid_reflectance_'+key)
    qa=by.get('qa_pixel',{}).get('value');sat=by.get('qa_radsat',{}).get('value')
    if qa is not None and(int(qa)!=qa or int(qa)&0b111111):blockers.append('fill_cloud_shadow_snow_or_cirrus')
    if sat is not None and(int(sat)!=sat or int(sat)!=0):blockers.append('radiometric_saturation_or_occlusion')
    # All reflectance and QA must refer to the identical acquisition/point.
    ids={(r.get('source_item'),r.get('observation_date'),r.get('requested_latitude'),r.get('requested_longitude'))for r in by.values()}
    if len(ids)>1:blockers.append('mixed_acquisition_or_point')
    value=None
    if all(k in by for k in ['red','nir08'])and not any(k.startswith('invalid_reflectance')for k in blockers):
        red,nir=by['red']['value'],by['nir08']['value']
        if nir+red>1e-8:value=(nir-red)/(nir+red)
        else:blockers.append('undefined_NDVI_denominator')
    return {'derived_NDVI':value,'retrieval_QA_complete':not blockers,'blockers':blockers,
        'validated_LAI_operator':False,'assimilable':False,'model_increment':None,
        'reason':'A spectral ratio is context, not leaf area or biomass without crop/date/footprint and a validated observer.'}


def screen_smap(row,target_year,model_depth_cm):
    blockers=[];year=int(str(row['observation_date'])[:4]);flag=row.get('quality_flags',{}).get('retrieval_qual_flag')
    if target_year<2015:blockers.append('target_predates_SMAP')
    if year!=target_year:blockers.append('observation_year_mismatch')
    if flag is None or int(flag)&3:blockers.append('not_recommended_retrieval_quality')
    if model_depth_cm!=5:blockers.append('surface5cm_vs_model_volume_mismatch')
    blockers.append('regional_pixel_not_matched_field_volume')
    return {'context_value':row['value'],'native_unit':row['unit'],'grid':row.get('grid'),'retrieval_flag':flag,
        'blockers':blockers,'assimilable':False,'profile_water_update':None}


def build_context(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False);rows=[]
    names=['landsat','imerg','smap_moisture','srtm','ecostress_et','ecostress_lst','ecostress_esi']
    for name in names:
        path=root/'all-sources/illinois'/(name+'.json');d=json.loads(path.read_text());obs=d.get('observations',[])
        entry={'source':name,'asset':str(path.relative_to(root)),'sha256':sha256(path),'local_status':d.get('status'),
            'observations':obs,'role':'context_only','assimilated':False,'predictive_value_measured':None,
            'initialization_value_measured':None,'available_at_historic_decision':None,
            'original_legacy_pass_does_not_override_product_QA':True}
        if name=='landsat':entry['observer_screen']=screen_landsat(obs)
        elif name=='smap_moisture':entry['observer_screen']=[screen_smap(r,2014,30)for r in obs]
        elif name=='srtm':entry.update(elevation_m=next((r['value']for r in obs if r.get('feature')=='elevation'and r.get('unit')=='m'),None),
            slope=None,erosion_mass=None,terrain_status='Single unverified elevation sample; no slope neighborhood or terrain water routing inferred.')
        elif name=='imerg':entry['context_limit']='Two single-cell values (rain and error) are not a seasonal precipitation record. No forecast or new forcing skill is claimed.'
        else:entry['context_limit']='Unverified QA and acquisition-specific thermal/ET support; no daily crop flux or stress initialization inferred.'
        rows.append(entry)
    report={'status':'existing_NASA_context_physically_screened','location':{'site_id':'ILLINOIS_DEMO','latitude':40.061188,'longitude':-88.234042},
        'sources':rows,'satellite_state_updates_applied':0,'additional_predictive_accuracy_established':False,
        'role_definitions':{'forcing':'POWER enters daily equations.','initialization':'Only compatible state observations may change initial stocks. None of these satellite samples qualifies.','context':'Visible numeric values and blockers, not hidden yield factors or simulated observations.'},
        'sources_not_forced_into_physics':True}
    write_json(out/'report.json',report);return report
