"""Run and audit every resource in the user's original challenge list."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
from pathlib import Path
import copy
import json
import numpy as np
import pandas as pd
from .acquisition import Query,ROOT,guarded,result,observation
from .challenge_catalog import FAMILIES,PRODUCTS
from . import satellite_sources as sat,provider_sources as p
from .sources import nasa_power


def power(q):
    payload,_=nasa_power(q.latitude,q.longitude,q.decision_date,offline=q.offline)
    obs=[]
    for r in payload['annual_records']:
        for name,unit in [('temperature','C'),('rain_mm','mm/year')]:
            obs.append(observation(q,name,r[name],unit,f'{r["year"]}-12-31','passed'))
    return result('power','observations_extracted',obs,provenance=[{k:payload[k] for k in ['source_url','retrieved_utc','response_sha256']}])


def jobs(q):
    return {
        'smap_moisture':lambda:sat.tesvis(q,'smap_moisture','SPL3SMP_E',[r'^soil_moisture$',r'^retrieval_qual_flag$']),
        'smap_freeze':lambda:sat.cmr(q,'smap_freeze'),
        'imerg':lambda:sat.cmr(q,'imerg'),
        'landsat':lambda:sat.stac(q,'landsat','landsat-c2-l2'),
        'modis':lambda:sat.tesvis(q,'modis','MOD13Q1',[r'_NDVI$',r'_EVI$'],r'pixel_reliability$'),
        'viirs':lambda:sat.tesvis(q,'viirs','VNP13A1',[r'_NDVI$',r'_EVI$'],r'pixel_reliability$'),
        'power':lambda:power(q),
        'srtm':lambda:sat.cmr(q,'srtm'),
        'nasadem_derivative':lambda:sat.stac(q,'nasadem_derivative','nasadem'),
        'ecostress_lst':lambda:sat.cmr(q,'ecostress_lst'),
        'ecostress_et':lambda:sat.cmr(q,'ecostress_et'),
        'ecostress_esi':lambda:sat.tesvis(q,'ecostress_esi','ECO4ESIPTJPL',[r'^ESIavg$',r'^PET$']),
        'ag_data_commons':lambda:p.ag_data_commons(q),
        'cdl':lambda:sat.stac(q,'cdl','usda-cdl'),
        'prism':lambda:p.prism(q),
        'ssurgo':lambda:p.soil_sda(q,'ssurgo'),
        'statsgo2':lambda:p.soil_sda(q,'statsgo2'),
        'naip':lambda:sat.stac(q,'naip','naip'),
        'nlcd':lambda:p.nlcd(q),
        'nldas':lambda:sat.cmr(q,'nldas'),
        'gldas':lambda:sat.cmr(q,'gldas'),
        'gaez':lambda:p.gaez(q),
        'cocorahs':lambda:p.cocorahs(q),
        'nwis':lambda:p.nwis(q),
        'ams':lambda:p.ams(q),
        'nass':lambda:p.nass(q),
        'openet':lambda:p.openet(q),
        'landfire_vegetation':lambda:p.landfire(q,'landfire_vegetation'),
        'landfire_fuels':lambda:p.landfire(q,'landfire_fuels'),
        'gedi_biomass':lambda:sat.tesvis(q,'gedi_biomass','GEDI04_B',[r'^MU$',r'^SE$',r'^QF$']),
        'sentinel2':lambda:sat.stac(q,'sentinel2','sentinel-2-l2a'),
        'era5':lambda:p.era5(q),
    }


def summarize(results):
    families=[]
    for family,label,products in FAMILIES:
        rows=[results.get(product,{'status':'not_run','observations':[],'assets':[]}) for product in products]
        measured=[product for product,row in zip(products,rows) if row.get('observation_count',0)>0]
        status='observations_available' if len(measured)==len(products) else 'partial_observations' if measured else 'no_observations'
        families.append({'source_id':family,'source':label,'status':status,'products':products,
                         'products_with_observations':measured,'product_statuses':{key:r['status'] for key,r in zip(products,rows)},
                         'physical_observation_count':sum(r.get('observation_count',0) for r in rows),
                         'training_status':'not_retrained_with_these_new_observations'})
    return families


def collect(q,out,workers=4):
    if not -90<=q.latitude<=90 or not -180<=q.longitude<=180:raise ValueError('Invalid coordinates')
    if q.cutoff.tzinfo is not None:raise ValueError('Use a date without a timezone')
    out=Path(out);out.mkdir(parents=True,exist_ok=True);results={}
    tasks=jobs(q)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(guarded,key,fn):key for key,fn in tasks.items()}
        for future in as_completed(futures):
            key=futures[future]
            try:row=future.result()
            except Exception as e:row=result(key,'adapter_error',error=type(e).__name__)
            results[key]=row
            (out/f'{key}.json').write_text(json.dumps(row,indent=2,default=str,allow_nan=False))
            print(key,row['status'],row.get('observation_count',0),'observations',flush=True)
    # WSS is an interface to the same NRCS soil data. Do not query or weight it twice.
    alias=copy.deepcopy(results['ssurgo']);alias['product']='web_soil_survey';alias['duplicate_of']='ssurgo'
    alias['note']='Web Soil Survey and SDA/SSURGO expose the same underlying mapped soil source; these are not independent measurements.'
    results['web_soil_survey']=alias
    (out/'web_soil_survey.json').write_text(json.dumps(alias,indent=2,default=str,allow_nan=False))
    assert set(results)==set(PRODUCTS),f'Missing products: {set(PRODUCTS)-set(results)}'
    return write_report(q,out,results)


def write_report(q,out,results):
    out=Path(out)
    assert set(results)==set(PRODUCTS),f'Missing products: {set(PRODUCTS)-set(results)}'
    for row in results.values():
        for obs in row.get('observations',[]):
            if obs['requested_latitude']!=q.latitude or obs['requested_longitude']!=q.longitude:
                raise ValueError('Cannot mix observations from different requested locations')
    families=summarize(results);features=[]
    for product,row in results.items():
        if row.get('duplicate_of'):continue
        for r in row.get('observations',[]):
            # Normalize into a tidy feature store without silently applying them to
            # a previously fitted model that never saw these features.
            features.append({'source_family':PRODUCTS[product],'product':product,**r})
    pd.DataFrame(features).to_csv(out/'observations.csv',index=False)
    report={'query':q.__dict__,'challenge_resource_entries':len(FAMILIES),'product_adapters':len(PRODUCTS),
            'families':families,'product_status_counts':dict(Counter(r['status'] for r in results.values())),
            'observations_extracted_excluding_shared_aliases':len(features),
            'products_with_physical_observations':sum(bool(r.get('observation_count')) for r in results.values()),
            'unique_products_with_physical_observations':sum(bool(r.get('observation_count')) for r in results.values() if not r.get('duplicate_of')),
            'all_listed_sources_fully_extracted':all(f['status']=='observations_available' for f in families),
            'credentials_needed':sorted({r['credential_environment_variable'] for r in results.values() if r.get('credential_environment_variable') and r['status']=='needs_credentials'}),
            'provider_terms_gates':{product:r.get('resolution_url') for product,r in results.items() if r['status']=='provider_terms_acceptance_required'},
            'quality_counts':dict(Counter(r.get('quality','unverified') for r in features)),
            'rotation_databases_used':False,'new_model_training_completed':False,
            'interpretation':'Adapter coverage, catalogue discovery, raw assets, physical observations, QA-passed features, and trained model usage are distinct stages.'}
    (out/'integration_report.json').write_text(json.dumps(report,indent=2,default=str))
    lines=['# Challenge dataset integration audit','',
           f'Every one of the {len(FAMILIES)} listed resource entries has a route and an explicit result. This is not a claim that every source returned physical data.','',
           '| Resource | Physical observations | Product status |','|---|---:|---|']
    for f in families:lines.append(f"| {f['source']} | {f['physical_observation_count']} | {'; '.join(k+': '+v for k,v in f['product_statuses'].items())} |")
    lines+=['',f"Extracted {len(features)} observations from {report['unique_products_with_physical_observations']} distinct product routes ({report['products_with_physical_observations']} including the shared Web Soil Survey alias). Quality states: {report['quality_counts']}. These counts describe this demonstration, not global coverage.",'',
            'Protected NASA access outcomes are recorded per product above. A terms-acceptance status records the provider response, not whether the user accepted terms. Credentials, provider terms gates and dataset-selection requirements are listed in the JSON report. No catalogue record is counted as a physical measurement.','',
            'New observations are in `observations.csv`, with units, times, source names and quality flags. They have not been used to retrain or improve the previously reported models. Web Soil Survey is deduplicated from SSURGO.','',
            'Original SRTMGL1 and SRTM-derived NASADEM are recorded separately. ERA5 is accessed via Open-Meteo with the ERA5 model explicitly selected. CoCoRaHS is obtained via its NOAA GHCN-Daily distribution.']
    (out/'integration_report.md').write_text('\n'.join(lines)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--field',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--offline',action='store_true')
    parser.add_argument('--workers',type=int,default=4);args=parser.parse_args();f=json.loads(args.field.read_text())
    q=Query(f['latitude'],f['longitude'],f['decision_date'],offline=args.offline)
    collect(q,args.output,max(1,min(4,args.workers)))


if __name__=='__main__':main()
