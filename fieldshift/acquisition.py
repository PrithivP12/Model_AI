"""Cached read-only source requests, protected access, and observation contracts."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import hashlib
import json
import os
import requests
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'all-sources/cache'
SECRET_KEYS={'key','api_key','apikey','token','access_token','authorization','sig'}


def credential(name):
    """Environment overrides a private config outside the project and archives."""
    value=os.environ.get(name)
    if value:return value
    path=Path.home()/'.config/fieldshift/credentials.json'
    if not path.exists():return None
    if path.stat().st_mode & 0o077:raise RuntimeError('Private credential config must have permissions 0600')
    return json.loads(path.read_text()).get(name)


def public_url(url):
    parts=urlsplit(url)
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode([(k,v) for k,v in parse_qsl(parts.query) if k.lower() not in SECRET_KEYS]),''))


@dataclass
class Query:
    latitude:float
    longitude:float
    decision_date:str
    offline:bool=False
    sample_dates:int=3

    @property
    def cutoff(self):return pd.Timestamp(self.decision_date).tz_localize(None)
    @property
    def year(self):return self.cutoff.year-1
    @property
    def bbox(self):return [self.longitude-.005,self.latitude-.005,self.longitude+.005,self.latitude+.005]
    @property
    def interval(self):return f'{self.year}-01-01T00:00:00Z/{self.year}-12-31T23:59:59Z'


class Client:
    def __init__(self,offline=False):self.offline=offline

    def request(self,url,params=None,body=None,headers=None,auth=None,binary=False,max_bytes=60_000_000):
        CACHE.mkdir(parents=True,exist_ok=True)
        # Credential values never enter cache metadata or filenames.
        safe_params={k:v for k,v in (params or {}).items() if k.lower() not in SECRET_KEYS}
        key=hashlib.sha256(json.dumps([url,safe_params,body],sort_keys=True).encode()).hexdigest()
        path=CACHE/(key+('.bin' if binary else '.json'))
        meta_path=CACHE/(key+'.meta.json')
        if path.exists() and meta_path.exists():
            return (path if binary else json.loads(path.read_text())),json.loads(meta_path.read_text())
        if self.offline:raise RuntimeError('offline_cache_miss')
        session=requests.Session();session.headers['User-Agent']='FieldShift research data integration/0.3'
        response=session.request('POST' if body is not None else 'GET',url,params=params,json=body,
                                 headers=headers,auth=auth,timeout=(10,40),stream=binary)
        if response.status_code>=400:
            reason=None
            if 'openet-api.org'==urlsplit(url).netloc and 'credentials do not contain the necessary fields' in response.text:
                reason='OpenET service could not refresh its own Earth Engine backend credentials.'
            raise SourceHTTPError(response.status_code,public_url(response.url),reason)
        if binary:
            buf=bytearray()
            for chunk in response.iter_content(1024*1024):
                buf.extend(chunk)
                if len(buf)>max_bytes:raise RuntimeError('asset_exceeds_bounded_download_limit')
            content=bytes(buf)
            if 'text/html' in response.headers.get('Content-Type',''):raise RuntimeError('upstream_returned_html_instead_of_data')
            path.write_bytes(content);result=path
        else:
            result=response.json();content=response.content
            path.write_text(json.dumps(result))
        meta={'url':public_url(response.url),'retrieved_utc':datetime.now(timezone.utc).isoformat(),
              'sha256':hashlib.sha256(content).hexdigest(),'request_body':body,'cache_key':key}
        meta_path.write_text(json.dumps(meta,indent=2));return result,meta

    def json(self,url,params=None,**kwargs):return self.request(url,params,**kwargs)


class SourceHTTPError(RuntimeError):
    def __init__(self,status,url,reason=None):
        self.status=status;self.reason=reason
        super().__init__(f'HTTP {status}: {url}'+(f' ({reason})' if reason else ''))


def result(product,status,observations=None,assets=None,provenance=None,**kwargs):
    observations=observations or []
    return json_safe({'product':product,'status':status,'observations':observations,'assets':assets or [],
            'provenance':provenance or [],'observation_count':len(observations),
            'used_in_existing_trained_model':False,**kwargs})


def json_safe(value):
    if isinstance(value,dict):return {k:json_safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [json_safe(v) for v in value]
    if isinstance(value,(float,np.floating)):return float(value) if np.isfinite(value) else None
    if isinstance(value,np.integer):return int(value)
    return value


def observation(q,name,value,unit,date,quality='unverified',**kwargs):
    v=None if value is None else float(value)
    if v is not None and not np.isfinite(v):v=None
    timestamp=pd.Timestamp(date).tz_localize(None)
    if timestamp>=q.cutoff:raise ValueError('Observation is on or after the decision cutoff')
    return {'feature':name,'value':v,'unit':unit,'observation_date':str(timestamp),
            'requested_latitude':q.latitude,'requested_longitude':q.longitude,'quality':quality,
            'training_eligible':v is not None and quality=='passed',**kwargs}


def guarded(product,fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except SourceHTTPError as e:
        status='upstream_service_configuration_error' if e.reason else 'needs_credentials' if e.status in [401,403] else 'upstream_error'
        return result(product,status,error=str(e))
    except requests.RequestException as e:
        # Do not echo exception URLs which could contain API keys.
        return result(product,'upstream_error',error=type(e).__name__)
    except (ValueError,RuntimeError,KeyError,OSError,ImportError) as e:
        return result(product,'offline_cache_miss' if str(e)=='offline_cache_miss' else 'adapter_error',error=str(e)[:250])
