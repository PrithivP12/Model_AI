"""Read-only, cached source access with explicit missing-data and provenance states."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'global/cache'


def fetch_json(url,params=None,offline=False):
    CACHE.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(json.dumps([url,params],sort_keys=True).encode()).hexdigest()
    path=CACHE/f'{key}.json'
    if path.exists():return json.loads(path.read_text())
    if offline:raise RuntimeError(f'No cached response for {url}')
    session=requests.Session()
    session.headers['User-Agent']='FieldShift-research/0.1 (read-only agricultural research)'
    session.mount('https://',HTTPAdapter(max_retries=Retry(total=2,backoff_factor=.5,status_forcelist=[429,500,502,503,504],allowed_methods=['GET'])))
    r=session.get(url,params=params,timeout=(10,45));r.raise_for_status()
    data=r.json()  # HTML block pages cannot be mistaken for valid datasets.
    result={'url':r.url,'retrieved_utc':datetime.now(timezone.utc).isoformat(),
            'response_sha256':hashlib.sha256(r.content).hexdigest(),'data':data}
    path.write_text(json.dumps(result))
    return result


def nasa_power(latitude,longitude,decision_date,history_years=10,offline=False):
    decision=pd.Timestamp(decision_date)
    # Historical complete calendar years. Never include future or decision-day weather.
    end_year=decision.year-1
    start_year=end_year-history_years+1
    params={'parameters':'T2M,T2M_MIN,T2M_MAX,PRECTOTCORR,RH2M,ALLSKY_SFC_SW_DWN,GWETROOT',
            'community':'AG','longitude':longitude,'latitude':latitude,'start':f'{start_year}0101',
            'end':f'{end_year}1231','format':'JSON','time-standard':'UTC'}
    result=fetch_json('https://power.larc.nasa.gov/api/temporal/daily/point',params,offline)
    d=pd.DataFrame(result['data']['properties']['parameter'])
    d.index=pd.to_datetime(d.index,format='%Y%m%d');d=d.apply(pd.to_numeric,errors='coerce').replace([-999,-9999],np.nan)
    assert d.index.max()<decision
    d=d.sort_index();d['year']=d.index.year;d['month']=d.index.month
    annual=d.groupby('year').agg(temperature=('T2M','mean'),rain_mm=('PRECTOTCORR',lambda x:x.sum(min_count=1)))
    monthly=d.groupby(['year','month']).agg(T2M=('T2M','mean'),PRECTOTCORR=('PRECTOTCORR',lambda x:x.sum(min_count=1)),days=('T2M','count'))
    missing=float(d[['T2M','PRECTOTCORR']].isna().mean().max())
    return {'status':'available','source_url':result['url'],'retrieved_utc':result['retrieved_utc'],
            'response_sha256':result['response_sha256'],'start':str(d.index.min().date()),'end':str(d.index.max().date()),
            'missing_fraction':missing,'annual_rain_mm_mean':float(annual.rain_mm.mean()),
            'monthly_normals':{str(int(m)):{'temperature_C':float(x.T2M.mean()),'rain_mm':float(x.PRECTOTCORR.mean())} for m,x in monthly.groupby(level='month')},
            'annual_records':annual.reset_index().to_dict('records'),
            'spatial_caution':'Gridded reanalysis context, not an on-field sensor or a future weather forecast.'},d


def distance_km(lat,lon,lats,lons):
    p1=np.radians(lat);p2=np.radians(np.asarray(lats,dtype=float))
    dp=p2-p1;dl=np.radians(np.asarray(lons,dtype=float)-lon)
    a=np.sin(dp/2)**2+np.cos(p1)*np.cos(p2)*np.sin(dl/2)**2
    return 6371*2*np.arcsin(np.sqrt(np.clip(a,0,1)))


def microbiome_context(latitude,longitude,decision_date,limit=5):
    d=pd.read_csv(ROOT/'global/raw/emp_metadata.tsv',sep='\t',low_memory=False)
    soil=d[d.empo_3.isin(['Soil (non-saline)','Plant rhizosphere'])].copy()
    soil['collection_date']=pd.to_datetime(soil.collection_timestamp,errors='coerce',format='mixed',utc=True)
    cutoff=pd.Timestamp(decision_date,tz='UTC')
    # Undated samples are described in inventory, not joined as historical neighbors.
    valid=soil.latitude_deg.between(-90,90)&soil.longitude_deg.between(-180,180)&soil.collection_date.notna()&(soil.collection_date<cutoff)
    available=soil.loc[valid].copy()
    available['distance_km']=distance_km(latitude,longitude,available.latitude_deg,available.longitude_deg)
    cols=['#SampleID','study_id','country','latitude_deg','longitude_deg','distance_km','collection_timestamp','empo_3','ph','adiv_shannon','doi']
    nearest=available.nsmallest(limit,'distance_km')[cols]
    nearest=nearest.astype(object).where(pd.notna(nearest),None)
    inventory={'soil_and_rhizosphere_samples':len(soil),'countries_and_territories':int(soil.country.nunique()),
               'country_sample_counts':soil.country.value_counts().to_dict(),
               'dated_georeferenced_samples_available_by_cutoff':len(available)}
    return {'status':'reference_context_only','inventory':inventory,'nearest_reference_samples':nearest.to_dict('records'),
            'applied_to_field_prediction':False,
            'caution':'These are reference samples from other sites and habitats; microbial diversity is not a universal soil-health score.',
            'source':'Earth Microbiome Project, https://earthmicrobiome.org/data-and-code/'}


def traits(names):
    db=pd.read_csv(ROOT/'global/raw/ecocrop.csv',encoding='cp1252')
    records={}
    for name in names:
        d=db[db.ScientificName.str.casefold()==name.casefold()]
        if len(d)!=1:
            records[name]={'status':'missing_or_ambiguous','matches':len(d)};continue
        row=d.iloc[0];cols=['EcoPortCode','ScientificName','COMNAME','FAMNAME','TOPMN','TOPMX','TMIN','TMAX','ROPMN','ROPMX','RMIN','RMAX','PHOPMN','PHOPMX','PHMIN','PHMAX','GMIN','GMAX']
        records[name]={'status':'available',**{c:(None if pd.isna(row[c]) else row[c].item() if isinstance(row[c],np.generic) else row[c]) for c in cols},
                       'source':'FAO EcoCrop, OpenCLIM archival copy; species-level envelopes, not variety trials.'}
    return records


def pathogens(names):
    d=pd.read_csv(ROOT/'global/raw/phibase.csv',encoding='utf-8-sig',low_memory=False)
    out={}
    for name in names:
        # Preserve documented host species matching; no fuzzy assignment to a crop.
        mask=d.Experimental_host_species.fillna('').str.match(re.escape(name)+r'(?:\s|$|\()',case=False)
        rows=d.loc[mask]
        species=rows.Pathogen_species.str.extract(r'^([A-Z][a-z]+ [a-z][a-z-]+)')[0].dropna()
        out[name]={'status':'available' if len(rows) else 'no_matching_evidence','records':len(rows),
                   'studied_pathogen_species':sorted(set(species)),
                   'diseases':sorted(set(rows.Disease_name.dropna().astype(str))),
                   'example_dois':sorted(set(rows.DOI.dropna().astype(str)))[:8],
                   'source':'PHI-base 4.18 (2025-05-01), https://www.phi-base.org/',
                   'caution':'Experimental pathogen-host gene evidence. Does not establish field presence, soil survival, or rotation break duration.'}
    return out


def local_pollinators(latitude,longitude,decision_date,offline=False):
    end=pd.Timestamp(decision_date)-pd.Timedelta(days=1);start=end-pd.DateOffset(years=5)
    records={};queries=[]
    # Disjoint illustrative groups, not an exhaustive pollinator inventory.
    for name in ['Apis','Bombus','Xylocopa','Syrphidae']:
        params={'name':name,'strict':'true','kingdom':'Animalia','rank':'FAMILY' if name=='Syrphidae' else 'GENUS'}
        if name!='Syrphidae':params['family']='Apidae'
        match=fetch_json('https://api.gbif.org/v1/species/match',params,offline)
        taxon=match['data']
        if taxon.get('matchType')!='EXACT' or taxon.get('rank') not in ['GENUS','FAMILY']:
            queries.append({'taxon':name,'status':'unresolved'});continue
        dy=.25;dx=.25/max(.1,math.cos(math.radians(latitude)))
        params={'taxonKey':taxon['usageKey'],'hasCoordinate':'true','hasGeospatialIssue':'false',
                'decimalLatitude':f'{max(-90,latitude-dy)},{min(90,latitude+dy)}',
                'decimalLongitude':f'{max(-180,longitude-dx)},{min(180,longitude+dx)}',
                'eventDate':f'{start.date()},{end.date()}','limit':300}
        result=fetch_json('https://api.gbif.org/v1/occurrence/search',params,offline);j=result['data']
        queries.append({'taxon':name,'matched_taxon_key':taxon['usageKey'],'total_matching_records':j.get('count'),
                        'returned_records':len(j.get('results',[])),'truncated':not j.get('endOfRecords',True),'url':result['url']})
        for r in j.get('results',[]):
            if r.get('occurrenceStatus')=='ABSENT':continue
            when=pd.to_datetime(r.get('eventDate'),errors='coerce',utc=True)
            if pd.isna(when) or when>=pd.Timestamp(decision_date,tz='UTC'):continue
            keys=['key','species','scientificName','decimalLatitude','decimalLongitude','eventDate','coordinateUncertaintyInMeters','datasetKey','license','references']
            records[str(r['key'])]={k:r.get(k) for k in keys}
    return {'status':'available','search_window':f'{start.date()} to {end.date()}',
            'scope':'Approximate 55 km bounding box; four illustrative pollinator groups; capped samples per query.',
            'queries':queries,'sampled_occurrence_records':list(records.values()),
            'sampled_species':sorted(set(r['species'] for r in records.values() if r.get('species'))),
            'used_as_abundance_or_absence':False,
            'caution':'Citizen-science and collection sampling effort is uneven. No record means unknown, not absence.'}


def plant_pollinator_interactions(names,local_species,offline=False):
    out={}
    for name in names:
        result=fetch_json('https://api.globalbioticinteractions.org/interaction',
                          {'sourceTaxon':name,'interactionType':'flowersVisitedBy','limit':300},offline)
        j=result['data'];rows=[dict(zip(j['columns'],r)) for r in j.get('data',[])]
        keep=[r for r in rows if str(r.get('source_taxon_name','')).casefold()==name.casefold()
              and r.get('interaction_type') in ['flowersVisitedBy','pollinatedBy']]
        targets=sorted(set(r['target_taxon_name'] for r in keep if r.get('target_taxon_name')))
        out[name]={'status':'available' if keep else 'no_matching_evidence','global_interaction_records_returned':len(keep),
                   'truncated':len(rows)>=300,'documented_visitors_or_pollinators':targets,
                   'overlap_with_sampled_local_species':sorted(set(targets)&set(local_species)),
                   'example_study_titles':sorted(set(r['study_title'] for r in keep if r.get('study_title')))[:6],
                   'source_url':result['url'],'caution':'Potential association only. A flower visit is not necessarily effective pollination; overlaps do not prove visits to this field.'}
    return out
