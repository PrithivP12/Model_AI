"""Authenticated, bounded NASA file access and point extraction.

Tokens and expiring redirect URLs stay in memory. Original URLs, times, units,
QA flags and point geolocation are retained; failed or masked data stay explicit.
"""
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import io
import json
import re
import zipfile
import threading
from functools import wraps
import requests
import numpy as np
import pandas as pd
from .acquisition import CACHE,Client,Query,SourceHTTPError,credential,result,observation,public_url,json_safe

_HDF_LOCK=threading.Lock()


def serialized_hdf(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        with _HDF_LOCK:return fn(*args,**kwargs)
    return wrapped


def nasa_host(url):
    host=urlsplit(url).hostname or ''
    return urlsplit(url).scheme=='https' and any(host==d or host.endswith('.'+d) for d in
        ['earthdata.nasa.gov','earthdatacloud.nasa.gov','eosdis.nasa.gov','ornl.gov','lpdaac.usgs.gov'])


def authorize_url(url):
    if not nasa_host(url):raise ValueError('Earthdata token is restricted to verified NASA data hosts')
    token=credential('EARTHDATA_TOKEN')
    if not token:raise RuntimeError('Earthdata credential is not configured')
    response=requests.get(url,headers={'Authorization':'Bearer '+token,'Range':'bytes=0-63'},
                          stream=True,timeout=(10,30))
    status=response.status_code
    if status>=400:
        reason=None
        if 'json' in response.headers.get('Content-Type',''):
            try:
                detail=response.json()
                if detail.get('error_description')=='EULA Acceptance Failure':
                    reason='GES_DISC_EULA_NOT_ACCEPTED'
            except ValueError:pass
        response.close();raise SourceHTTPError(status,public_url(url),reason)
    if 'text/html' in response.headers.get('Content-Type',''):
        response.close();raise RuntimeError('NASA returned a login page instead of data')
    href=response.url
    meta={'url':public_url(url),'http_status':status,'etag':response.headers.get('ETag'),
          'last_modified':response.headers.get('Last-Modified'),'content_range':response.headers.get('Content-Range'),
          'redirected_host':urlsplit(href).hostname,'token_recorded':False}
    response.close()
    # Do not retain a token header for non-NASA CloudFront redirects.
    headers={'Authorization':'Bearer '+token} if nasa_host(href) else {}
    return href,headers,meta


def download(url,offline=False,max_bytes=160_000_000):
    CACHE.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(('earthdata-file|'+url).encode()).hexdigest()
    path=CACHE/(key+'.nasa.bin');meta_path=CACHE/(key+'.nasa.meta.json')
    if path.exists() and meta_path.exists():return path,json.loads(meta_path.read_text())
    if offline:raise RuntimeError('offline_cache_miss')
    href,headers,meta=authorize_url(url)
    r=requests.get(href,headers=headers,stream=True,timeout=(10,40));r.raise_for_status()
    total=0;digest=hashlib.sha256();temp=path.with_suffix('.part')
    try:
        with temp.open('wb') as f:
            for chunk in r.iter_content(1024*1024):
                total+=len(chunk)
                if total>max_bytes:raise RuntimeError('Bounded download exceeded; use remote subsetting')
                digest.update(chunk);f.write(chunk)
        temp.replace(path)
    finally:
        r.close()
        if temp.exists():temp.unlink()
    meta.update(sha256=digest.hexdigest(),file_bytes=total)
    meta_path.write_text(json.dumps(meta,indent=2));return path,meta


def cached_pixel(url,q):
    import rasterio
    from rasterio.warp import transform
    CACHE.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(f'earthdata-pixel|{url}|{q.latitude}|{q.longitude}'.encode()).hexdigest()
    path=CACHE/(key+'.nasa.pixel.json')
    if path.exists():
        cached=json_safe(json.loads(path.read_text()))
        path.write_text(json.dumps(cached,indent=2,allow_nan=False))
        return cached
    if q.offline:raise RuntimeError('offline_cache_miss')
    href,headers,meta=authorize_url(url)
    options={'GDAL_DISABLE_READDIR_ON_OPEN':'EMPTY_DIR','GDAL_HTTP_TIMEOUT':'30','GDAL_HTTP_MAX_RETRY':'1',
             'CPL_VSIL_CURL_ALLOWED_EXTENSIONS':'.tif,.TIF,.tiff'}
    if headers:options['GDAL_HTTP_HEADERS']='Authorization: '+headers['Authorization']
    with rasterio.Env(**options):
        with rasterio.open(href) as src:
            xs,ys=transform('EPSG:4326',src.crs,[q.longitude],[q.latitude])
            row,col=src.index(xs[0],ys[0])
            if not (0<=row<src.height and 0<=col<src.width):raise ValueError('Field point outside NASA raster')
            value=src.read(1,window=rasterio.windows.Window(col,row,1,1),masked=True)[0,0]
            value=None if np.ma.is_masked(value) else float(value)
            if value is not None:value=value*src.scales[0]+src.offsets[0]
            record={'value':value,'unit':src.units[0],'tags':src.tags(),'band_tags':src.tags(1),
                    'resolution':list(src.res),'crs':str(src.crs),'nodata':src.nodata,'provenance':meta}
    record=json_safe(record)
    path.write_text(json.dumps(record,indent=2,default=str,allow_nan=False));return record


def raster_product(q,product,discovery):
    selectors={'ecostress_lst':[('_LST.tif','land_surface_temperature','K'),('_LST_err.tif','temperature_uncertainty','K')],
               'ecostress_et':[('_ETdaily.tif','daily_evapotranspiration','mm/day')],
               'ecostress_esi':[('_ESI.tif','evaporative_stress_index','ratio'),('_PET.tif','potential_evapotranspiration','W/m2')],
               'gedi_biomass':[('_MU.tif','aboveground_biomass_density','Mg/ha'),('_SE.tif','biomass_standard_error','Mg/ha')]}
    assets=discovery['assets'];obs=[];provenance=list(discovery.get('provenance',[]));issues=[]
    selected_prefix=None
    # Use an explicit source layer. Do not confuse biomass standard error with biomass.
    for suffix,name,expected_unit in selectors[product]:
        candidates=[a for a in assets if a['href'].lower().endswith(suffix.lower())]
        if selected_prefix is not None:
            candidates=[a for a in candidates if a['href']==selected_prefix+suffix]
        for asset in candidates[:6 if product=='ecostress_lst' else 3]:
            try:
                record=cached_pixel(asset['href'],q);provenance.append(record['provenance'])
                if record['value'] is None:continue
                unit=record.get('unit') or record.get('band_tags',{}).get('units') or expected_unit
                # Product QA companions are retained; cloud cover is checked where present.
                qa={};quality='unverified'
                if product.startswith('ecostress'):
                    prefix=asset['href'][:-len(suffix)]
                    for qa_suffix in ['_cloud.tif','_QC.tif']:
                        matching=[a for a in assets if a['href']==prefix+qa_suffix]
                        if matching:
                            raw=cached_pixel(matching[0]['href'],q);qa[qa_suffix]=raw['value']
                    if qa.get('_cloud.tif') is not None and qa['_cloud.tif']!=0:quality='flagged'
                obs.append(observation(q,name,record['value'],unit,asset.get('start') or asset.get('end'),quality,
                    source_file=asset['href'],raster={k:v for k,v in record.items() if k!='provenance'},quality_flags=qa,
                    unit_basis='GeoTIFF units when present, otherwise selected product variable definition'))
                if selected_prefix is None:selected_prefix=asset['href'][:-len(suffix)]
                break
            except (RuntimeError,ValueError,requests.RequestException) as e:
                if isinstance(e,SourceHTTPError):raise
                issues.append({'file':asset['href'].split('/')[-1],'error':type(e).__name__})
    return result(product,'observations_extracted' if obs else 'no_valid_pixels',obs,assets,provenance,
                  extraction_issues=issues,note='Point samples from original NASA products; masked/cloudy/unchecked QA is explicit. These new values have not retrained the model.')


def srtm(q,discovery):
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.warp import transform
    candidates=[a for a in discovery['assets'] if a['href'].lower().endswith('.hgt.zip')]
    if not candidates:return result('srtm','no_matching_elevation_asset',assets=discovery['assets'])
    asset=candidates[0];path,meta=download(asset['href'],q.offline)
    with zipfile.ZipFile(path) as archive:
        name=next(n for n in archive.namelist() if n.lower().endswith('.hgt'))
        # GDAL needs the tile name to infer SRTM's geographic transform.
        with MemoryFile(archive.read(name),filename=Path(name).name) as memory:
            with memory.open() as src:
                row,col=src.index(q.longitude,q.latitude)
                value=src.read(1,window=rasterio.windows.Window(col,row,1,1),masked=True)[0,0]
                v=None if np.ma.is_masked(value) else float(value)
    obs=[] if v is None else [observation(q,'elevation',v,'m',asset['start'],'unverified',
                                         source_file=asset['href'],horizontal_datum='WGS84',vertical_datum='EGM96 geoid')]
    return result('srtm','observations_extracted' if obs else 'no_valid_pixel',obs,[asset],discovery.get('provenance',[])+[meta])


def netcdf(q,product,discovery):
    import netCDF4
    assets=[a for a in discovery['assets'] if a['href'].endswith(('.nc','.nc4'))]
    if not assets:return result(product,'no_matching_netcdf_asset',assets=discovery['assets'])
    asset=assets[0];path,meta=download(asset['href'],q.offline)
    targets={'imerg':['precipitation','precipitationCal','randomError'],
             'gldas':['SoilMoi0_10cm_inst','SoilMoi10_40cm_inst','Evap_tavg','Rainf_tavg','AvgSurfT_inst'],
             'nldas':['SoilM_0_10cm','SoilM_10_40cm','SoilT_0_10cm','Evap','Rainf','Tair']}[product]
    obs=[]
    with netCDF4.Dataset(path) as ds:
        latname=next((k for k in ds.variables if k.lower() in ['lat','latitude']),None)
        lonname=next((k for k in ds.variables if k.lower() in ['lon','longitude']),None)
        if not latname or not lonname:raise ValueError('NetCDF coordinate schema needs an explicit mapping')
        lats=np.asarray(ds[latname][:]);lons=np.asarray(ds[lonname][:]);
        if lats.ndim!=1 or lons.ndim!=1:raise ValueError('Curvilinear NetCDF grid needs an explicit geolocation mapping')
        iy=int(np.argmin(np.abs(lats-q.latitude)));ix=int(np.argmin(np.abs((lons-q.longitude+180)%360-180)))
        for name in targets:
            if name not in ds.variables:continue
            var=ds[name];indices=[]
            for dimension in var.dimensions:
                if dimension in ds[latname].dimensions:indices.append(iy)
                elif dimension in ds[lonname].dimensions:indices.append(ix)
                else:indices.append(0)
            value=var[tuple(indices)]
            if np.ma.is_masked(value) or not np.isfinite(value):continue
            obs.append(observation(q,name,float(value),getattr(var,'units','unknown'),asset['start'],'unverified',
                source_file=asset['href'],long_name=getattr(var,'long_name',name),
                pixel_latitude=float(lats[iy]),pixel_longitude=float(lons[ix]),
                note='Source scale/offset/fill handling is performed by netCDF4; fluxes are not mislabeled as accumulated rainfall.'))
    return result(product,'observations_extracted' if obs else 'no_mapped_valid_variables',obs,[asset],discovery.get('provenance',[])+[meta])


def hdf_metadata_value(dataset,key,default=None):
    if key not in dataset.attrs:return default
    value=np.asarray(dataset.attrs[key]).reshape(-1)[0]
    return value.decode() if isinstance(value,bytes) else value.item() if isinstance(value,np.generic) else value


@serialized_hdf
def smap(q,product,discovery):
    import h5py
    import fsspec
    from rasterio.warp import transform
    assets=[a for a in discovery['assets'] if a['href'].endswith('.h5')]
    if not assets:return result(product,'no_matching_hdf_asset',assets=discovery['assets'])
    asset=assets[0];version='|phase-schema-v2' if product=='smap_freeze' else ''
    cache_key=hashlib.sha256(f'smap-point|{asset["href"]}|{q.latitude}|{q.longitude}{version}'.encode()).hexdigest()
    point_path=CACHE/(cache_key+'.smap.point.json')
    if point_path.exists():
        cached=json.loads(point_path.read_text())
        if product=='smap_freeze':
            for r in cached['observations']:
                r['quality']='unverified';r['training_eligible']=False
            cached['quality_note']='Freeze/thaw QA flags are preserved; soil-moisture QA bit semantics are not assumed to apply to this different product.'
            point_path.write_text(json.dumps(cached,indent=2))
        return cached
    if q.offline:raise RuntimeError('offline_cache_miss')
    href,headers,meta=authorize_url(asset['href']);obs=[];grid_checks=[]
    # Standard NSIDC 9-km global EASE-Grid 2.0. Validate each candidate's geolocation
    # from the source file before using the projected row/column.
    x,y=transform('EPSG:4326','EPSG:6933',[q.longitude],[q.latitude])
    col=int(np.floor((x[0]+17367530.4451615)/9008.055210146))
    row=int(np.floor((7314540.8306386-y[0])/9008.055210146))
    with fsspec.open(href,'rb',headers=headers,block_size=4*1024*1024,cache_type='blockcache',client_kwargs={'timeout':__import__('aiohttp').ClientTimeout(total=40)}) as raw:
        with h5py.File(raw,'r') as f:
            for group_name in f:
                if group_name=='Metadata':continue
                group=f[group_name]
                latkey=next((k for k in group if k.startswith('latitude') and 'centroid' not in k and isinstance(group[k],h5py.Dataset) and group[k].shape[-2:]==(1624,3856)),None)
                lonkey=next((k for k in group if k.startswith('longitude') and 'centroid' not in k and isinstance(group[k],h5py.Dataset) and group[k].shape[-2:]==(1624,3856)),None)
                if not latkey or not lonkey:continue
                phases=range(group[latkey].shape[0]) if group[latkey].ndim==3 else [None]
                for phase in phases:
                    def cell(ds):return ds[phase,row,col] if ds.ndim==3 else ds[row,col]
                    lat,lon=float(cell(group[latkey])),float(cell(group[lonkey]))
                    if not -90<=lat<=90 or not -180<=lon<=180:continue
                    from .sources import distance_km
                    distance=float(distance_km(q.latitude,q.longitude,[lat],[lon])[0])
                    if distance>15:raise ValueError('SMAP grid projection did not match the file geolocation')
                    qa_key=next((k for k in group if k in ['retrieval_qual_flag','retrieval_qual_flag_pm']),None)
                    qa=int(cell(group[qa_key])) if qa_key else None
                    grid_checks.append({'group':group_name,'phase_index':phase,'row':row,'column':col,'pixel_latitude':lat,'pixel_longitude':lon,'distance_km':distance,'qa_flag':qa})
                    wanted=['soil_moisture','soil_moisture_pm','soil_moisture_error'] if product=='smap_moisture' else ['freeze_thaw','transition_direction','transition_state_flag']
                    for name in wanted:
                        if name not in group or not isinstance(group[name],h5py.Dataset) or group[name].shape[-2:]!=(1624,3856):continue
                        ds=group[name];v=float(cell(ds));fill=hdf_metadata_value(ds,'_FillValue',-9999)
                        low=hdf_metadata_value(ds,'valid_min');high=hdf_metadata_value(ds,'valid_max')
                        if not np.isfinite(v) or v==fill or v==-9999 or (low is not None and v<low) or (high is not None and v>high):continue
                        scale=hdf_metadata_value(ds,'scale_factor',1);offset=hdf_metadata_value(ds,'add_offset',0)
                        unit=hdf_metadata_value(ds,'units','source_class_code' if product=='smap_freeze' else 'm3/m3')
                        quality=('passed' if qa is not None and qa!=65535 and qa&1==0 else 'flagged' if qa is not None else 'unverified') if product=='smap_moisture' else 'unverified'
                        obs.append(observation(q,group_name+'/'+name+(f'/phase_{phase}' if phase is not None else ''),v*scale+offset,unit,asset['start'],quality,
                                               source_file=asset['href'],pixel_latitude=lat,pixel_longitude=lon,
                                               quality_flags={qa_key:qa} if qa_key else {},grid='EPSG:6933, 9008.05521 projected meters'))
    output=result(product,'observations_extracted' if obs else 'no_valid_pixels',obs,[asset],discovery.get('provenance',[])+[meta],
        geolocation_checks=grid_checks,grid_reference='https://nsidc.org/data/user-resources/help-center/guide-ease-grids',
        note='A valid file can have no usable soil-moisture retrieval at a frozen winter pixel. No missing retrieval is replaced with zero.')
    point_path.write_text(json.dumps(output,indent=2,default=str));return output


def extract(q,product,discovery):
    if not credential('EARTHDATA_TOKEN'):return discovery
    try:
        if product in ['smap_moisture','smap_freeze']:return smap(q,product,discovery)
        if product=='srtm':return srtm(q,discovery)
        if product in ['imerg','nldas','gldas']:return netcdf(q,product,discovery)
        if product in ['ecostress_lst','ecostress_et','ecostress_esi','gedi_biomass']:return raster_product(q,product,discovery)
        return discovery
    except SourceHTTPError as e:
        if e.reason=='GES_DISC_EULA_NOT_ACCEPTED':
            return result(product,'provider_terms_acceptance_required',assets=discovery.get('assets'),provenance=discovery.get('provenance'),
                provider_error='EULA Acceptance Failure',resolution_url='https://urs.earthdata.nasa.gov/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ')
        raise
