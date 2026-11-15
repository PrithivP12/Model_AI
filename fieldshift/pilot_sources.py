"""Matched county/crop/year inputs; no rotation outcome source is read here."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import io
import json
import time
import requests
import numpy as np
import pandas as pd
from .acquisition import Client, credential, CACHE
from .protected_sources import download

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'rotation-pilot'
POWER_PARAMS='T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,RH2M,GWETROOT,ALLSKY_SFC_SW_DWN'
NLDAS_VARIABLES=['SoilM_0_10cm','SoilM_10_40cm','SoilM_40_100cm','SoilM_100_200cm',
                 'SoilT_0_10cm','Evap','Rainf','Qs','Qsb','SnowDepth']
DESCRIPTIONS={'corn':'CORN, GRAIN - YIELD, MEASURED IN BU / ACRE',
              'soybeans':'SOYBEANS - YIELD, MEASURED IN BU / ACRE',
              'wheat':'WHEAT, WINTER - YIELD, MEASURED IN BU / ACRE'}


def counties():
    OUT.mkdir(exist_ok=True)
    path=OUT/'counties_2024.txt'
    url='https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_gaz_counties_17.txt'
    if not path.exists():
        r=requests.get(url,timeout=30);r.raise_for_status();path.write_bytes(r.content)
    d=pd.read_csv(path,sep='\t',dtype={'GEOID':str});d.columns=d.columns.str.strip()
    d=d.rename(columns={'INTPTLAT':'latitude','INTPTLONG':'longitude','GEOID':'county_fips','NAME':'county_name'})
    # One request per MERRA-2 grid point, shared by nearby county interior points.
    d['grid_lat']=(d.latitude/.5).round()*.5
    d['grid_lon']=(d.longitude/.625).round()*.625
    return d


def outcomes():
    frames=[];audit={}
    for crop,description in DESCRIPTIONS.items():
        path=OUT/f'{crop}_nass.json'
        if not path.exists():
            p,m=Client().json('https://quickstats.nass.usda.gov/api/api_GET/',
                {'key':credential('NASS_API_KEY'),'commodity_desc':crop.upper(),'statisticcat_desc':'YIELD',
                 'agg_level_desc':'COUNTY','state_alpha':'IL','year__GE':2000,'year__LE':2025,
                 'source_desc':'SURVEY','freq_desc':'ANNUAL','reference_period_desc':'YEAR',
                 'domain_desc':'TOTAL','format':'JSON'})
            path.write_text(json.dumps({'data':p,'provenance':m}))
        raw=pd.DataFrame(json.loads(path.read_text())['data']['data'])
        d=raw[(raw.short_desc==description)&(raw.prodn_practice_desc=='ALL PRODUCTION PRACTICES')].copy()
        # NASS codes 998/999 are aggregate OTHER counties, not independent counties.
        d['county_fips']=d.state_ansi.astype(str).str.zfill(2)+d.county_ansi.astype(str).str.zfill(3)
        d['y']=pd.to_numeric(d.Value.str.replace(',','',regex=False),errors='coerce')
        d['year']=d.year.astype(int);d['crop']=crop
        keep=d.y.notna()&(d.y>0)&d.county_fips.isin(counties().county_fips)
        audit[crop]={'downloaded_rows':len(raw),'exact_description_rows':len(d),'invalid_or_aggregate_rows_excluded':int((~keep).sum())}
        d=d.loc[keep,['county_fips','crop','year','y','load_time']]
        if d.duplicated(['county_fips','crop','year']).any():raise ValueError('Conflicting county/crop/year outcomes')
        frames.append(d)
    result=pd.concat(frames,ignore_index=True).sort_values(['county_fips','crop','year']).reset_index(drop=True)
    result.to_csv(OUT/'county_yields.csv',index=False)
    (OUT/'outcome_audit.json').write_text(json.dumps(audit,indent=2))
    return result


def power_grid(lat,lon):
    params={'parameters':POWER_PARAMS,'community':'AG','latitude':lat,'longitude':lon,
            'start':1999,'end':2024,'format':'JSON'}
    # Respect the provider's concurrency guidance; retry only temporary failures.
    for attempt in range(3):
        try:
            payload,meta=Client().json('https://power.larc.nasa.gov/api/temporal/monthly/point',params)
            break
        except (requests.RequestException,RuntimeError):
            if attempt==2:raise
            time.sleep(1+attempt)
    d=pd.DataFrame(payload['properties']['parameter'])
    d=d.loc[[s for s in d.index if str(s)[4:6]!='13']].copy()
    d['date']=pd.to_datetime(d.index,format='%Y%m')
    for col in POWER_PARAMS.split(','):d[col]=pd.to_numeric(d[col],errors='coerce').replace([-999,-9999],np.nan)
    d['grid_lat']=lat;d['grid_lon']=lon
    return d.reset_index(drop=True),{'grid_lat':lat,'grid_lon':lon,'request':meta,'units':payload['parameters']}


def collect_power():
    path=OUT/'power_monthly.csv'
    if path.exists():return pd.read_csv(path,parse_dates=['date'])
    grids=counties()[['grid_lat','grid_lon']].drop_duplicates()
    rows=[];provenance=[]
    with ThreadPoolExecutor(3) as pool:
        futures={pool.submit(power_grid,float(r.grid_lat),float(r.grid_lon)):(r.grid_lat,r.grid_lon) for r in grids.itertuples()}
        for future in as_completed(futures):
            d,m=future.result();rows.append(d);provenance.append(m)
            print('POWER grids',len(rows),'/',len(grids),flush=True)
    d=pd.concat(rows,ignore_index=True);d.to_csv(path,index=False)
    (OUT/'power_provenance.json').write_text(json.dumps(provenance,indent=2))
    return d


def nldas_catalogue():
    path=OUT/'nldas_catalogue.json'
    if not path.exists():
        d,m=Client().json('https://cmr.earthdata.nasa.gov/search/granules.json',
            {'short_name':'NLDAS_NOAH0125_M','temporal':'1999-01-01T00:00:00Z,2024-12-31T23:59:59Z',
             'page_size':1000,'sort_key':'start_date'})
        path.write_text(json.dumps({'data':d,'provenance':m}))
    entries=json.loads(path.read_text())['data']['feed']['entry']
    # June of the preceding year: two months before September 1 planning date.
    selected=[e for e in entries if pd.Timestamp(e['time_start']).month==6]
    if len(selected)!=26:raise ValueError('Incomplete NLDAS June catalogue')
    return selected


def nldas_file(entry):
    url=next(l['href'] for l in entry['links'] if '/data/NLDAS/' in l.get('href','') and l['href'].endswith('.nc'))
    path,meta=download(url)
    return entry,path,meta


def collect_nldas():
    path=OUT/'nldas_june.csv'
    if path.exists():return pd.read_csv(path)
    entries=nldas_catalogue();files=[]
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(nldas_file,e) for e in entries]
        for future in as_completed(futures):
            files.append(future.result());print('NLDAS files',len(files),'/',len(entries),flush=True)
    # netCDF4/HDF5 access stays serial; those libraries need not be thread-safe.
    import netCDF4
    c=counties();rows=[];provenance=[]
    for entry,raw,meta in sorted(files,key=lambda x:x[0]['time_start']):
        with netCDF4.Dataset(raw) as ds:
            lats=np.asarray(ds['lat'][:]);lons=np.asarray(ds['lon'][:]);units={}
            for row in c.itertuples():
                iy=int(np.argmin(abs(lats-row.latitude)));ix=int(np.argmin(abs(lons-row.longitude)))
                record={'county_fips':row.county_fips,'source_year':pd.Timestamp(entry['time_start']).year,
                        'pixel_latitude':float(lats[iy]),'pixel_longitude':float(lons[ix])}
                for name in NLDAS_VARIABLES:
                    if name not in ds.variables:continue
                    var=ds[name];indices=[iy if dim=='lat' else ix if dim=='lon' else 0 for dim in var.dimensions]
                    value=var[tuple(indices)]
                    record['nldas_'+name]=np.nan if np.ma.is_masked(value) else float(value)
                    units[name]=getattr(var,'units','unknown')
                rows.append(record)
        provenance.append({'date':entry['time_start'],'granule_id':entry['id'],'units':units,**meta})
    d=pd.DataFrame(rows);d.to_csv(path,index=False)
    (OUT/'nldas_provenance.json').write_text(json.dumps(provenance,indent=2))
    return d


def collect():
    outcomes()
    # Independent public weather and protected NLDAS acquisition may run together.
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(collect_power),pool.submit(collect_nldas)]
        for future in futures:future.result()
    print('Matched-source acquisition complete',flush=True)


if __name__=='__main__':collect()
