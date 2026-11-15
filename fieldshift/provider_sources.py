"""Soil, climate, water, land-cover, agriculture and economic source adapters."""
from pathlib import Path
import calendar
import hashlib
import io
import json
import os
import re
import zipfile
import requests
import numpy as np
import pandas as pd
from .acquisition import Client,Query,CACHE,result,observation,credential
from .sources import distance_km


def conus(q):return -125<=q.longitude<=-66 and 24<=q.latitude<=50


def soil_sda(q,product):
    if not conus(q):return result(product,'outside_implemented_coverage',note='This adapter currently handles CONUS; no substitute soil values are inserted.')
    c=Client(q.offline)
    # Fixed query structure; user coordinates are validated floats, never SQL fragments.
    restriction="AND LEN(l.areasymbol)=2" if product=='statsgo2' else 'AND LEN(l.areasymbol)>2'
    sql=f"""SELECT TOP 50 l.areasymbol, mu.mukey, mu.muname, co.cokey, co.compname,
        co.comppct_r, co.drainagecl, co.hydgrp, ch.hzdept_r, ch.hzdepb_r,
        ch.ph1to1h2o_r, ch.om_r, ch.awc_r, ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r
        FROM legend l INNER JOIN mapunit mu ON mu.lkey=l.lkey
        INNER JOIN component co ON co.mukey=mu.mukey
        INNER JOIN chorizon ch ON ch.cokey=co.cokey
        WHERE mu.mukey IN (SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84(
        'point({float(q.longitude)} {float(q.latitude)})'))
        {restriction} AND co.majcompflag='Yes' AND ch.hzdept_r<30
        ORDER BY co.comppct_r DESC, ch.hzdept_r"""
    payload,meta=c.json('https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest',body={'query':sql,'format':'JSON+COLUMNNAME'})
    table=payload.get('Table',[])
    if len(table)<2:return result(product,'no_matching_mapunit',provenance=[meta],
        note='A source query returning no mapunit is not evidence that the region has no soil. STATSGO2 may require its separate spatial archive.')
    names=table[0];rows=[dict(zip(names,r)) for r in table[1:]];obs=[]
    # Dominant component's top horizon; no fabricated weighted field measurement.
    top=rows[0]
    for name,unit in [('ph1to1h2o_r','pH'),('om_r','percent'),('awc_r','cm/cm'),('sandtotal_r','percent'),('silttotal_r','percent'),('claytotal_r','percent')]:
        try:v=float(top[name])
        except (ValueError,TypeError):continue
        obs.append(observation(q,name,v,unit,f'{q.year}-12-31','unverified',
            mapunit_key=top.get('mukey'),component=top.get('compname'),horizon_top_cm=top.get('hzdept_r'),
            temporal_note='Current soil survey accessed now; historical release availability is not reconstructed.'))
    return result(product,'observations_extracted',obs,provenance=[meta],mapunit_rows=rows,
                  note='Mapped component estimates, not new on-farm lab assays; report depth and component percentages.')


def prism(q):
    if not conus(q):return result('prism','outside_implemented_coverage')
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.warp import transform
    c=Client(q.offline);obs=[];provenance=[]
    for variable,unit in [('ppt','mm/year'),('tmean','C')]:
        url=f'https://services.nacse.org/prism/data/get/us/4km/{variable}/{q.year}'
        path,meta=c.request(url,binary=True);provenance.append(meta)
        with zipfile.ZipFile(path) as archive:
            tifs=[n for n in archive.namelist() if n.lower().endswith('.tif')]
            if len(tifs)!=1:raise ValueError('Unexpected PRISM raster package')
            with MemoryFile(archive.read(tifs[0])) as memory:
                with memory.open() as src:
                    x,y=transform('EPSG:4326',src.crs,[q.longitude],[q.latitude])
                    value=list(src.sample([(x[0],y[0])],masked=True))[0][0]
                    v=None if np.ma.is_masked(value) else float(value)
                    obs.append(observation(q,variable,v,unit,f'{q.year}-12-31','passed',
                        resolution=str(src.res),source_file=tifs[0],release_timing_verified=False))
    return result('prism','observations_extracted',obs,provenance=provenance,
        note='One annual precipitation and temperature grid, cached to respect PRISM download limits; not a full daily/30-year-normal series.')


def era5(q):
    c=Client(q.offline)
    params={'latitude':q.latitude,'longitude':q.longitude,'start_date':f'{q.year}-01-01','end_date':f'{q.year}-12-31',
        'models':'era5','daily':'temperature_2m_mean,precipitation_sum,shortwave_radiation_sum','timezone':'UTC'}
    payload,meta=c.json('https://archive-api.open-meteo.com/v1/archive',params)
    daily=payload.get('daily',{});obs=[]
    for name,unit,agg in [('temperature_2m_mean','C',np.nanmean),('precipitation_sum','mm/year',np.nansum),('shortwave_radiation_sum','MJ/m2/year',np.nansum)]:
        values=np.array([np.nan if x is None else x for x in daily.get(name,[])],float)
        if not len(values) or not np.isfinite(values).any():continue
        obs.append(observation(q,name,float(agg(values)),unit,f'{q.year}-12-31','passed',
                               valid_days=int(np.isfinite(values).sum()),expected_days=366 if calendar.isleap(q.year) else 365))
    return result('era5','observations_extracted' if obs else 'no_data',obs,provenance=[meta],
                  distribution='ECMWF ERA5 accessed through the Open-Meteo archive adapter, explicitly models=era5; not an ERA5-Land substitution.',
                  license='See Open-Meteo attribution/noncommercial service conditions and original ECMWF terms.')


def landfire(q,product):
    if not conus(q):return result(product,'outside_implemented_coverage')
    c=Client(q.offline);year=min(2023,q.year);name='EVT' if product=='landfire_vegetation' else 'FBFM40'
    service=f'https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF{year}/LF{year}_{name}_CONUS/ImageServer'
    payload,meta=c.json(service+'/identify',{'f':'json','geometry':json.dumps({'x':q.longitude,'y':q.latitude,'spatialReference':{'wkid':4326}}),
        'geometryType':'esriGeometryPoint','returnGeometry':'false','returnCatalogItems':'false'})
    if payload.get('error'):return result(product,'upstream_error',provenance=[meta],error=payload['error'])
    value=payload.get('value')
    try:v=float(value)
    except (TypeError,ValueError):return result(product,'no_valid_pixel',provenance=[meta])
    return result(product,'observations_extracted',[observation(q,name,v,'class_code',f'{year}-12-31','unverified')],
                  provenance=[meta],note='Categorical vegetation/fuel class, not a continuous soil-health measurement.')


def nlcd(q):
    if not conus(q):return result('nlcd','outside_implemented_coverage')
    c=Client(q.offline);year=max([y for y in [2001,2006,2011,2013,2016,2019,2021] if y<=q.year],default=None)
    if year is None:return result('nlcd','no_dates_before_cutoff')
    layer=f'NLCD_{year}_Land_Cover_L48';dx=.001
    payload,meta=c.json('https://www.mrlc.gov/geoserver/mrlc_display/wms',{'service':'WMS','version':'1.1.1','request':'GetFeatureInfo',
        'layers':layer,'query_layers':layer,'srs':'EPSG:4326','bbox':f'{q.longitude-dx},{q.latitude-dx},{q.longitude+dx},{q.latitude+dx}',
        'width':101,'height':101,'x':50,'y':50,'info_format':'application/json'})
    obs=[]
    for f in payload.get('features',[]):
        for name,value in f.get('properties',{}).items():
            try:obs.append(observation(q,name,float(value),'class_code',f'{year}-12-31','unverified'))
            except (ValueError,TypeError):pass
    return result('nlcd','observations_extracted' if obs else 'no_valid_pixel',obs,provenance=[meta],
                  note='Legacy NLCD land-cover product selected explicitly; no claim that this is the newest Annual NLCD release.')


def nwis(q):
    c=Client(q.offline);box=[q.longitude-.25,q.latitude-.25,q.longitude+.25,q.latitude+.25]
    sites,sm=c.json('https://api.waterdata.usgs.gov/ogcapi/v1/collections/monitoring-locations/items',
        {'f':'json','bbox':','.join(map(str,box)),'limit':100,'agency_code':'USGS'})
    features=sites.get('features',[])
    if not features:return result('nwis','no_nearby_monitoring_locations',provenance=[sm])
    features=[f for f in features if f.get('geometry',{}).get('type')=='Point' and f.get('properties',{}).get('agency_code')=='USGS']
    features.sort(key=lambda f:float(distance_km(q.latitude,q.longitude,[f['geometry']['coordinates'][1]],[f['geometry']['coordinates'][0]])[0]))
    provenance=[sm];obs=[];used=[]
    # Query measurements spatially first so inactive nearby station records cannot
    # hide an active gauge. The API's daily time extent is expressed as dates.
    local,lm=c.json('https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items',
        {'f':'json','bbox':','.join(map(str,box)),'time':f'{q.year}-01-01/{q.year}-12-31','parameter_code':'00060','limit':10})
    provenance.append(lm)
    for f in local.get('features',[]):
        r=f['properties'];date=r.get('time');value=r.get('value')
        if date is None or value is None or pd.Timestamp(date).year!=q.year:continue
        try:obs.append(observation(q,str(r.get('parameter_code','water_measurement')),float(value),r.get('unit_of_measure','unknown'),date,'unverified',
            monitoring_location_id=r.get('monitoring_location_id'),source_properties=r,measurement_geometry=f.get('geometry')))
        except (ValueError,TypeError):continue
    if obs:return result('nwis','observations_extracted',obs,provenance=provenance,
        note='Nearby streamflow measurements from the modern USGS API; not a direct measurement of irrigation access at the field.')
    for site in features[:3]:
        sid=site.get('id') or site['properties']['id']
        payload,meta=c.json('https://api.waterdata.usgs.gov/ogcapi/v1/collections/daily/items',
            {'f':'json','monitoring_location_id':sid,'time':f'{q.year}-01-01/{q.year}-12-31','limit':10})
        provenance.append(meta);used.append({'id':sid,'properties':site['properties'],'geometry':site['geometry']})
        for f in payload.get('features',[]):
            p=f['properties'];date=p.get('time') or p.get('date') or p.get('datetime');v=p.get('value')
            if date is None or v is None or pd.Timestamp(date).year!=q.year:continue
            try:obs.append(observation(q,str(p.get('parameter_code','water_measurement')),float(v),p.get('unit_of_measure','unknown'),date,'unverified',
                monitoring_location_id=sid,source_properties=p))
            except (ValueError,TypeError):continue
        if obs:break
    return result('nwis','observations_extracted' if obs else 'sites_discovered_no_measurements',obs,provenance=provenance,
                  monitoring_locations=used,note='Modern USGS OGC Water Data API. Nearby stream/well observations are not direct measurements of irrigation access at the field.')


def cocorahs(q):
    c=Client(q.offline)
    path,sm=c.request('https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt',binary=True,max_bytes=30_000_000)
    candidates=[]
    for line in path.read_text().splitlines():
        if not line.startswith(('US1','CA1','BF1')):continue
        try:
            lat,lon=float(line[12:20]),float(line[21:30]);dist=float(distance_km(q.latitude,q.longitude,[lat],[lon])[0])
        except ValueError:continue
        if dist<100:candidates.append((dist,line[:11],lat,lon,line[41:71].strip()))
    candidates.sort()
    provenance=[sm]
    for dist,station,lat,lon,name in candidates[:3]:
        path,meta=c.request(f'https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/{station}.dly',binary=True,max_bytes=5_000_000)
        provenance.append(meta);obs=[]
        for line in path.read_text().splitlines():
            if len(line)<269 or line[17:21]!='PRCP' or int(line[11:15])!=q.year:continue
            year,month=int(line[11:15]),int(line[15:17])
            for day in range(1,calendar.monthrange(year,month)[1]+1):
                block=line[21+(day-1)*8:21+day*8];value=int(block[:5]);qflag=block[6:7].strip()
                if value==-9999 or qflag:continue
                obs.append(observation(q,'daily_precipitation',value/10,'mm',f'{year}-{month:02d}-{day:02d}','passed',
                    station_id=station,station_latitude=lat,station_longitude=lon,distance_km=dist,
                    measurement_flag=block[5:6].strip(),source_flag=block[7:8].strip()))
        if obs:return result('cocorahs','observations_extracted',obs,provenance=provenance,
            station={'id':station,'name':name,'distance_km':dist},distribution='CoCoRaHS observations redistributed by NOAA GHCN-Daily; US1/CA1/BF1 station prefixes verified in CoCoRaHS documentation.',
            note='Only stations within 100 km are considered. Missing/multiday periods are not assumed to have zero rainfall.')
    return result('cocorahs','no_recent_observations_within_100km',provenance=provenance)


def county(q):
    payload,meta=Client(q.offline).json('https://geocoding.geo.census.gov/geocoder/geographies/coordinates',
        {'x':q.longitude,'y':q.latitude,'benchmark':'Public_AR_Current','vintage':'Current_Current','format':'json'})
    rows=payload.get('result',{}).get('geographies',{}).get('Counties',[])
    if not rows:raise ValueError('No Census county for requested point')
    return rows[0],meta


def nass(q):
    key=credential('NASS_API_KEY')
    if not key:return result('nass','needs_credentials',credential_environment_variable='NASS_API_KEY',
        official_url='https://quickstats.nass.usda.gov/api',note='County agricultural statistics; no credentials configured and no values invented.')
    loc,lm=county(q)
    payload,meta=Client(q.offline).json('https://quickstats.nass.usda.gov/api/api_GET/',
        {'key':key,'commodity_desc':'CORN','statisticcat_desc':'YIELD','agg_level_desc':'COUNTY',
         'state_fips_code':loc['STATE'],'county_code':loc['COUNTY'],'year__GE':q.year-4,'year__LE':q.year,
         'freq_desc':'ANNUAL','domain_desc':'TOTAL','format':'JSON'})
    obs=[]
    for r in payload.get('data',[]):
        v=pd.to_numeric(str(r.get('Value','')).replace(',',''),errors='coerce')
        if pd.isna(v):continue
        obs.append(observation(q,'county_corn_yield',v,r.get('unit_desc','unknown'),f'{r["year"]}-12-31','unverified',county=loc['NAME'],
            release_timing_verified=False))
    return result('nass','observations_extracted' if obs else 'no_matching_statistics',obs,provenance=[lm,meta])


def ams(q):
    key=credential('AMS_API_KEY')
    if not key:return result('ams','needs_credentials',credential_environment_variable='AMS_API_KEY',
        official_url='https://mymarketnews.ams.usda.gov/mymarketnews-api/authentication')
    c=Client(q.offline);payload,meta=c.json('https://marsapi.ams.usda.gov/services/v1.2/reports',auth=(key,''))
    report_id=os.environ.get('AMS_REPORT_ID')
    if not report_id:return result('ams','catalogue_ready_dataset_selection_required',provenance=[meta],reports=payload,
        required_configuration='AMS_REPORT_ID',note='A commodity/market report must be selected; unrelated prices are not treated as local crop returns.')
    data,dm=c.json(f'https://marsapi.ams.usda.gov/services/v1.2/reports/{int(report_id)}/Details',auth=(key,''))
    return result('ams','records_retrieved_mapping_required',provenance=[meta,dm],records=data,
        note='Report-specific units, commodity grades, location and publication date must be mapped before training.')


def openet(q):
    key=credential('OPENET_API_KEY')
    if not key:return result('openet','needs_credentials',credential_environment_variable='OPENET_API_KEY',
        official_url='https://openetdata.org/',note='Actual footprint coverage must be checked by the service; no ET substituted from another mission.')
    body={'date_range':[f'{q.year}-01-01',f'{q.year}-12-31'],'interval':'monthly','geometry':[q.longitude,q.latitude],
          'model':'Ensemble','variable':'ET','reference_et':'gridMET','units':'mm','file_format':'JSON'}
    payload,meta=Client(q.offline).json('https://openet-api.org/raster/timeseries/point',body=body,headers={'Authorization':key})
    rows=payload if isinstance(payload,list) else payload.get('data',[]);obs=[]
    for r in rows:
        date=r.get('time') or r.get('date');value=r.get('et') if 'et' in r else r.get('ET')
        if date and value is not None:obs.append(observation(q,'monthly_et',value,'mm/month',date,'unverified'))
    return result('openet','observations_extracted' if obs else 'no_coverage_or_unrecognized_schema',obs,provenance=[meta])


def gaez(q):
    c=Client(q.offline)
    # Search the FAO-owned portal, not all third-party ArcGIS items.
    payload,meta=c.json('https://gaez.fao.org/api/search/v1/collections/dataset/items',{'limit':100})
    fs=payload.get('features',[]);chosen=[f for f in fs if 'suitability' in f.get('properties',{}).get('title','').lower()]
    if not chosen:return result('gaez','catalogue_ready_dataset_selection_required',provenance=[meta],catalogue_items=fs,
        note='GAEZ is distinct from EcoCrop; no EcoCrop score is mislabeled as a GAEZ raster.')
    row=chosen[0];url=row['properties'].get('url')
    if not url:return result('gaez','catalogue_ready_dataset_selection_required',provenance=[meta],catalogue_items=chosen)
    details,dm=c.json(url,{'f':'json'})
    # Select a source raster by its catalogued maize name. Do not guess crop, irrigation or management codes.
    rasters,rm=c.json(url+'/query',{'f':'json','where':"Name LIKE '%MZE%'",'outFields':'*','returnGeometry':'false','resultRecordCount':10})
    if not rasters.get('features'):
        # The catalogue's historic crop names/codes are read from its own metadata.
        # The public service does not consistently support distinct-value queries.
        rasters,rm=c.json(url+'/query',{'f':'json','where':"year LIKE '19%'",'outFields':'*','returnGeometry':'false','resultRecordCount':300})
    rows=[f['attributes'] for f in rasters.get('features',[])]
    selected=[r for r in rows if r.get('crop')=='Maize' and str(r.get('year','')).startswith('1981')
              and str(r.get('water_supply','')).strip().startswith('Rainfed') and r.get('input_level')=='Low'
              and 'suitability index in classes' in r.get('variable','').lower()]
    if not selected:return result('gaez','catalogue_ready_dataset_selection_required',provenance=[meta,dm,rm],raster_candidates=rows)
    reference=selected[0];href=reference['download_url'];pixel_key=f'gaez|{reference["objectid"]}|{q.latitude}|{q.longitude}'
    cached=CACHE/(hashlib.sha256(pixel_key.encode()).hexdigest()+'.pixel.json')
    if cached.exists():value,raster_meta=json.loads(cached.read_text())
    else:
        from .satellite_sources import raster_pixel
        value,raster_meta=raster_pixel(href,q);cached.write_text(json.dumps([value,raster_meta]))
    obs=[observation(q,'maize_suitability_class_low_input_rainfed',value,reference['units'],'2010-12-31','unverified',
                     reference_scenario=reference,raster=raster_meta)]
    return result('gaez','observations_extracted',obs,assets=[{'href':href,'role':'historical_reference_raster'}],
        provenance=[meta,dm,rm],reference_scenario=reference,
        note='Explicit historical 1981-2010 maize / low-input / rainfed reference scenario. This is not an assertion about actual management at the requested farm; categorical class is not a probability.')


def ag_data_commons(q):
    # This resource is a repository, not a single spatial layer. Its public OAI
    # endpoint is queried without selecting or importing any rotation dataset.
    url='https://agdatacommons.nal.usda.gov/api/graphql'
    return result('ag_data_commons','catalogue_connector_requires_configuration',
        assets=[{'href':'https://agdatacommons.nal.usda.gov/','role':'catalogue'}],
        official_url='https://www.nal.usda.gov/services/agdatacommons',
        note='Ag Data Commons contains heterogeneous datasets. An accession/file with compatible variables must be explicitly selected. No rotation datasets are searched or imported.',
        configured_endpoint=url)
