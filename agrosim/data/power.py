"""NASA POWER daily product adapter; metadata, units and fill values are mandatory."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import json
import math
import pandas as pd
import requests
from ..io import sha256, write_json

MAPPING={'T2M_MIN':('tmin_C','C'),'T2M_MAX':('tmax_C','C'),
         'PRECTOTCORR':('rain_mm','mm/day'),'ALLSKY_SFC_SW_DWN':('solar_MJ_m2','MJ/m^2/day')}


def read_power(path, site_id, latitude, longitude):
    path=Path(path);wrapper=json.loads(path.read_text())
    data=wrapper.get('data',wrapper.get('response',wrapper))
    header=data.get('header',{})
    parameters=data.get('properties',{}).get('parameter',{})
    if not all(k in parameters for k in MAPPING):raise ValueError('Not the required NASA POWER daily variables')
    if header.get('time_standard')not in ['UTC','LST']:
        raise ValueError('Explicit POWER time standard required')
    if header.get('time_standard')!='UTC':raise ValueError('This adapter currently normalizes UTC daily intervals only')
    coords=data['geometry']['coordinates']
    if abs(latitude-float(coords[1]))>.002 or abs(longitude-float(coords[0]))>.002:
        raise ValueError('POWER requested point does not match site; native footprint is still coarse')
    dates=sorted(parameters['T2M_MIN'])
    if not dates or any(len(k)!=8 for k in dates):raise ValueError('Daily POWER YYYYMMDD keys required')
    if any(set(parameters[k])!=set(dates)for k in MAPPING):
        raise ValueError('POWER variables have different date coverage')
    fill=header.get('fill_value',-999)
    rows=[];observations=[]
    available=wrapper.get('retrieved_utc')or wrapper.get('retrieved_at')
    digest=sha256(path)
    for key in dates:
        t=datetime.strptime(key,'%Y%m%d');row={'date':t.date().isoformat()}
        for parameter,(variable,expected_unit)in MAPPING.items():
            unit=data['parameters'][parameter]['units'];value=parameters[parameter].get(key)
            if value is None or value==fill or not isinstance(value,(float,int))or isinstance(value,bool)or not math.isfinite(value):
                raise ValueError(f'Missing or fill POWER value {parameter} {key}')
            if unit==expected_unit:factor=1
            elif parameter=='ALLSKY_SFC_SW_DWN'and unit=='kW-hr/m^2/day':factor=3.6
            else:raise ValueError(f'Unverified POWER unit {parameter}: {unit}')
            normalized=float(value)*factor
            if variable in ['tmin_C','tmax_C']and not -100<=normalized<=70:raise ValueError('Physically implausible observed daily temperature')
            if variable in ['rain_mm','solar_MJ_m2']and normalized<0:raise ValueError('Negative POWER flux')
            row[variable]=normalized
            observations.append({'site_id':site_id,'field_id':None,'variable':variable,'value':normalized,
                'unit':'mm'if variable=='rain_mm'else 'MJ/m2'if variable=='solar_MJ_m2'else 'C',
                'time_start':t.isoformat()+'Z','time_end':(t+timedelta(days=1,seconds=-1)).isoformat()+'Z',
                'available_at':available,'source_id':'power','product_id':'POWER daily point AG UTC',
                'product_version':header.get('api',{}).get('version'),'native_resolution':'parameter-specific coarse grid; not a field measurement',
                'aggregation_method':'daily point extraction; precipitation rate × 1 day',
                'quality_status':'passed','valid_fraction':1.,'uncertainty':None,
                'geometry_or_footprint':json.dumps(data['geometry']),'raw_asset_id':digest,
                'processing_version':'agrosim-power-v1','raw_value':value,'raw_unit':unit,'conversion_factor':factor})
        if row['tmin_C']>row['tmax_C']:raise ValueError('Inverted POWER temperatures')
        rows.append(row)
    frame=pd.DataFrame(rows)
    stamps=pd.to_datetime(frame.date)
    if len(stamps)>1 and not (stamps.diff().dropna()==pd.Timedelta(days=1)).all():raise ValueError('Daily POWER gaps')
    return frame,pd.DataFrame(observations),{'sha256':digest,'path':str(path),'header':header,'query':wrapper.get('url'),
        'retrieved_utc':available,'site_point':[latitude,longitude],'native_resolution_is_field_scale':False}


def retrieve_power(config, cache, offline=True):
    """Immutable bounded point query; explicit online opt-in and no authentication."""
    from hashlib import sha256 as digest
    params={'latitude':config['latitude'],'longitude':config['longitude'],'start':config['start'].replace('-',''),
            'end':config['end'].replace('-',''),'parameters':','.join(MAPPING),'community':'AG',
            'format':'JSON','time-standard':'UTC'}
    a=datetime.fromisoformat(config['start']);b=datetime.fromisoformat(config['end'])
    if not 0<=(b-a).days<=3660:raise ValueError('Download limited to ten years per point query')
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    target=cache/(digest(json.dumps(params,sort_keys=True).encode()).hexdigest()+'.json')
    if target.exists():return target
    if offline:raise FileNotFoundError('POWER subset absent from local cache; opt in with --online')
    endpoint='https://power.larc.nasa.gov/api/temporal/daily/point'
    response=requests.get(endpoint,params=params,timeout=(10,90));response.raise_for_status()
    if len(response.content)>20_000_000:raise ValueError('POWER response exceeds 20 MB')
    write_json(target,{'url':response.url,'retrieved_utc':datetime.now(timezone.utc).isoformat(),'data':response.json()})
    return target
