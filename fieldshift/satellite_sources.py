"""NASA collection discovery, public point subsets and QA-aware raster extraction."""
import json
import os
import re
import requests
import numpy as np
import pandas as pd
from .acquisition import Client,Query,result,observation,SourceHTTPError,credential
from .challenge_catalog import CMR

PC='https://planetarycomputer.microsoft.com/api/stac/v1'
TESVIS='https://modis.ornl.gov/rst/api/v1'


def cmr(q,product):
    client=Client(q.offline);name=CMR[product]
    params={'short_name':name,'bounding_box':','.join(map(str,q.bbox)),
            'temporal':q.interval.replace('/',','),'page_size':8 if product=='ecostress_lst' else 3,'sort_key':'-start_date'}
    if product=='srtm':params.pop('temporal')  # SRTM acquired terrain in 2000, not the requested crop year.
    if product=='gedi_biomass':params['temporal']=f'2018-01-01T00:00:00Z,{q.cutoff.strftime("%Y-%m-%d")}T00:00:00Z'
    payload,meta=client.json('https://cmr.earthdata.nasa.gov/search/granules.json',params)
    entries=payload.get('feed',{}).get('entry',[]);assets=[]
    for e in entries:
        for link in e.get('links',[]):
            href=link.get('href','')
            if 'data#' not in link.get('rel','') or link.get('inherited') or not href.startswith('https://'):continue
            assets.append({'href':href,'granule_id':e.get('id'),'title':e.get('title'),
                           'start':e.get('time_start'),'end':e.get('time_end'),'type':link.get('type')})
    status='no_matching_granules' if not entries else 'needs_credentials' if not credential('EARTHDATA_TOKEN') else 'assets_discovered_not_extracted'
    discovered=result(product,status,assets=assets,provenance=[meta],collection_short_name=name,
                  granules_returned=len(entries),credential_environment_variable='EARTHDATA_TOKEN',
                  note='Catalogue records and asset URLs are not physical observations. Original files are not counted as integrated measurements until downloaded, subset and quality checked.')
    if entries and credential('EARTHDATA_TOKEN'):
        from .protected_sources import extract
        return extract(q,product,discovered)
    return discovered


def numeric_range(value):
    if not value:return None
    tokens=re.findall(r'-?\d+(?:\.\d+)?',str(value))
    return tuple(map(float,tokens[:2])) if len(tokens)>=2 else None


def tesvis(q,product,shortname,band_patterns,qa_pattern=None):
    c=Client(q.offline)
    try:dates,dm=c.json(f'{TESVIS}/{shortname}/dates',{'latitude':q.latitude,'longitude':q.longitude})
    except SourceHTTPError as e:
        if e.status not in [400,404] or product not in CMR:raise
        fallback=cmr(q,product);fallback['public_subset_status']='not_available_for_requested_point'
        return fallback
    # Fixed-site-only products return a JSON string at unsupported global locations.
    if not isinstance(dates,dict):
        fallback=cmr(q,product) if product in CMR else result(product,'no_global_subset')
        fallback['public_subset_status']='not_available_for_requested_point'
        fallback['public_subset_provenance']=dm
        return fallback
    valid=[d for d in dates.get('dates',[]) if pd.Timestamp(d['calendar_date'])<q.cutoff]
    if not valid:return result(product,'no_dates_before_cutoff',provenance=[dm])
    # Small representative sample, not a full mission time series.
    valid=sorted(valid,key=lambda x:x['calendar_date'])[-q.sample_dates:]
    bands,bm=c.json(f'{TESVIS}/{shortname}/bands');band_info={b['band']:b for b in bands['bands']}
    chosen=[n for n in band_info if any(re.search(p,n,re.I) for p in band_patterns)]
    if qa_pattern:chosen+= [n for n in band_info if re.search(qa_pattern,n,re.I) and n not in chosen]
    payload,pm=c.json(f'{TESVIS}/{shortname}/subset',{'latitude':q.latitude,'longitude':q.longitude,
        'startDate':valid[0]['modis_date'],'endDate':valid[-1]['modis_date'],'kmAboveBelow':0,'kmLeftRight':0})
    if not isinstance(payload,dict):return result(product,'no_point_subset',provenance=[dm,bm,pm])
    selected=set(chosen);rows=payload.get('subset',[]);qa={}
    for row in rows:
        if qa_pattern and re.search(qa_pattern,row.get('band',''),re.I):
            values=np.asarray(row['data'],float);qa[row['calendar_date']]=bool(values.size and np.all(values==0))
    obs=[]
    for row in rows:
        name=row['band']
        if name not in selected:continue
        b=band_info[name];values=np.asarray(row['data'],float)
        if b.get('fill_value') is not None:values=values[values!=float(b['fill_value'])]
        vr=numeric_range(b.get('valid_range'))
        if vr:values=values[(values>=vr[0])&(values<=vr[1])]
        if not values.size:continue
        value=float(np.median(values)*float(b.get('scale_factor',1))+float(b.get('add_offset',0)))
        quality='passed' if qa_pattern and qa.get(row['calendar_date']) else 'flagged' if qa_pattern else 'unverified'
        obs.append(observation(q,name,value,b.get('units','unknown'),row['calendar_date'],quality,
                               source_product=shortname,cellsize=payload.get('cellsize'),
                               quality_note='MODIS/VIIRS pixel reliability must equal zero to pass. Other products retain unverified QA.'))
    return result(product,'observations_extracted' if obs else 'no_valid_pixels',obs,provenance=[dm,bm,pm],
                  source_product=shortname,subset_header={k:v for k,v in payload.items() if k!='subset'},
                  sampling='At most three most recent composite dates; not a complete historical series.')


def signed_href(href,offline=False):
    if '.blob.core.windows.net/' not in href:return href
    if offline:raise RuntimeError('remote_raster_not_cached')
    # Anonymous public SAS signatures are refreshed, never persisted in provenance.
    r=requests.get('https://planetarycomputer.microsoft.com/api/sas/v1/sign',params={'href':href},timeout=(10,30))
    r.raise_for_status();return r.json()['href']


def raster_pixel(href,q,index=1):
    import rasterio
    from rasterio.warp import transform
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif,.TIF,.tiff',
                      GDAL_HTTP_TIMEOUT='25',GDAL_HTTP_MAX_RETRY='1',GDAL_HTTP_MULTIRANGE='YES'):
        with rasterio.open(signed_href(href,q.offline)) as src:
            x,y=transform('EPSG:4326',src.crs,[q.longitude],[q.latitude])
            col,row=src.index(x[0],y[0])[1],src.index(x[0],y[0])[0]
            if not (0<=row<src.height and 0<=col<src.width):raise ValueError('Point outside raster asset')
            values=src.read(indexes=index,window=rasterio.windows.Window(col,row,1,1),masked=True)
            v=None if np.ma.is_masked(values[0,0]) else float(values[0,0])
            return v,{'crs':str(src.crs),'resolution':list(src.res),'nodata':src.nodata,'band_count':src.count}


def stac(q,product,collection):
    c=Client(q.offline);params={'collections':collection,'bbox':','.join(map(str,q.bbox)),'limit':5}
    if product!='nasadem_derivative':params['datetime']=f'{q.year-5}-01-01T00:00:00Z/{q.year}-12-31T23:59:59Z'
    # Prefer a growing-season capture for optical stress context, never a future image.
    if product in ['landsat','sentinel2']:params['datetime']=f'{q.year}-05-01T00:00:00Z/{q.year}-10-31T23:59:59Z'
    payload,meta=c.json(PC+'/search',params);items=payload.get('features',[])
    if product=='cdl':
        # Exclude secondary cultivated masks: only actual crop classification assets.
        items=[i for i in items if not any(s in i['id'].lower() for s in ['cultivated','confidence'])]
    if not items:return result(product,'no_spatial_temporal_coverage',provenance=[meta])
    items.sort(key=lambda i:i['properties'].get('eo:cloud_cover',0))
    provenance=[meta];obs=[];assets=[];errors=[]
    for item in items[:3 if product in ['landsat','sentinel2'] else 1]:
        a=item['assets'];date=item['properties'].get('datetime') or item['properties'].get('end_datetime')
        if not date:date=f'{min(q.year,2000) if product=="nasadem_derivative" else q.year}-01-01'
        if pd.Timestamp(date).tz_localize(None)>=q.cutoff:continue
        chosen={'landsat':['red','nir08','qa_pixel'],'sentinel2':['B04','B08','SCL'],
                'naip':['image'],'nasadem_derivative':['elevation'],'cdl':['cropland','data']}[product]
        if product=='cdl':chosen=[k for k,v in a.items() if v.get('href','').lower().endswith(('.tif','.tiff'))][:1]
        values={};metadata={}
        for key in chosen:
            if key not in a:continue
            href=a[key]['href'];assets.append({'href':href,'asset':key,'item_id':item['id'],'datetime':date})
            pixel_key=f'{product}|{item["id"]}|{key}|{q.latitude}|{q.longitude}'
            import hashlib
            from .acquisition import CACHE
            cached=CACHE/(hashlib.sha256(pixel_key.encode()).hexdigest()+'.pixel.json')
            try:
                if cached.exists():v,m=json.loads(cached.read_text())
                else:
                    v,m=raster_pixel(href,q);cached.write_text(json.dumps([v,m]))
                bands=a[key].get('raster:bands') or [{}];b=bands[0]
                values[key]=None if v is None else v*float(b.get('scale',1))+float(b.get('offset',0))
                metadata[key]=m
            except Exception as e:errors.append({'asset':key,'error':type(e).__name__})
        quality='unverified'
        if product=='landsat' and values.get('qa_pixel') is not None:
            quality='passed' if int(values['qa_pixel'])&63==0 else 'flagged'
        elif product=='sentinel2' and values.get('SCL') is not None:
            quality='passed' if int(values['SCL']) in [4,5] else 'flagged'
        for key,v in values.items():
            unit='m' if product=='nasadem_derivative' else 'class_code' if product=='cdl' else 'DN' if product in ['naip','sentinel2'] else 'reflectance' if key in ['red','nir08'] else 'bitfield'
            obs.append(observation(q,key,v,unit,date,quality,source_item=item['id'],raster=metadata.get(key)))
        if product=='landsat' and values.get('red') is not None and values.get('nir08') is not None:
            red,nir=values['red'],values['nir08'];ndvi=(nir-red)/(nir+red) if nir+red!=0 else None
            if ndvi is not None and not -1<=ndvi<=1:quality='flagged'
            obs.append(observation(q,'ndvi',ndvi,'ratio',date,quality,source_item=item['id']))
        # S2 post-baseline-4 DN offsets are deliberately not guessed. Raw bands and
        # scene classification are delivered; physical NDVI needs harmonization.
        if obs and quality=='passed':break
    status='observations_extracted' if obs else 'assets_discovered_not_extracted'
    return result(product,status,obs,assets,provenance,errors=errors,
                  caveat='SRTM-derived NASADEM is labeled separately from original SRTMGL1. Sentinel-2 values remain DN pending verified baseline/offset harmonization.')
