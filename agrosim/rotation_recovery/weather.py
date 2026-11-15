"""Bounded, cached historical subsets of the existing NASA POWER daily product."""
from pathlib import Path
from datetime import date
import json,time
import pandas as pd
from ..io import write_json,sha256
from ..data.power import retrieve_power,read_power
from ..processes import WeatherDay


def prepare_weather(root,output,online=False,cache=None):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    fixed=pd.read_csv(root/'reports/rotation-recovery/fixed_cohort.csv');ids=set(fixed[['reference_records','candidate_records']].stack().str.split(';').explode())
    frame=pd.read_parquet(root/'reports/reassessed/data/enhanced_site_seasons.parquet');sites=frame[frame.record_id.isin(ids)][['site_id','latitude','longitude']].drop_duplicates().sort_values('site_id')
    if len(sites)>8:raise ValueError('Historical weather request budget exceeded')
    manifest={};shared={}
    for row in sites.itertuples():
        # Reuse exact duplicate queries only. A rounded latitude/longitude is not
        # proof of a shared, parameter-specific native grid footprint.
        cell=(float(row.latitude),float(row.longitude))
        if cell not in shared:
            config={'latitude':float(row.latitude),'longitude':float(row.longitude),'start':'2011-01-01','end':'2014-12-31'}
            last=None
            for attempt in range(2):
                try:
                    path=retrieve_power(config,Path(cache)if cache else out/'raw',offline=not online);frame,_,meta=read_power(path,row.site_id,row.latitude,row.longitude);last=None;break
                except Exception as e:
                    last=e
                    if not online or attempt:break
                    time.sleep(2)
            if last is not None:
                manifest[row.site_id]={'status':'unavailable','reason':str(last),'requested_point':[row.latitude,row.longitude]};continue
            shared[cell]=(path,frame,meta,config)
        path,frame,meta,config=shared[cell]
        normalized=out/(row.site_id+'.csv');frame.to_csv(normalized,index=False)
        manifest[row.site_id]={'status':'available','raw_path':str(path.resolve()),'raw_sha256':sha256(path),
            'normalized_path':str(normalized.resolve()),'days':len(frame),'requested_point':[row.latitude,row.longitude],
            'queried_point':[config['latitude'],config['longitude']],'native_cell_reuse':abs(config['latitude']-row.latitude)>1e-6 or abs(config['longitude']-row.longitude)>1e-6,
            'metadata':meta,'role':'Retrospective daily gridded meteorology, not a plot weather station or historically available forecast'}
        print('weather',row.site_id,len(frame),flush=True)
    write_json(out/'manifest.json',manifest);return manifest


def historical_weather(output,site,end):
    out=Path(output);manifest=json.loads((out/'manifest.json').read_text());record=manifest[site]
    if record['status']!='available':raise ValueError('Historical forcing unavailable; do not replace with invented observations')
    path=out/(site+'.csv');d=pd.read_csv(path);d=d[d.date<=str(end)]
    days=[WeatherDay(date.fromisoformat(r.date),r.rain_mm,r.tmin_C,r.tmax_C,r.solar_MJ_m2)for r in d.itertuples()]
    if not days or str(days[-1].date)!=str(end):raise ValueError('Historical weather lacks declared period')
    return days
