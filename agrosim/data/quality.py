"""Consequential conversions and decision-date eligibility checks."""
from datetime import datetime,timezone,time
import numpy as np


def accumulation(rate, unit, seconds):
    if not np.isfinite(rate)or rate<0 or not np.isfinite(seconds)or seconds<=0:
        raise ValueError('Nonnegative rate and positive actual interval required')
    factors={'mm/hour':3600,'mm/hr':3600,'mm/day':86400,'kg m-2 s-1':1}
    if unit not in factors:raise ValueError('Unsupported precipitation rate unit')
    return float(rate*seconds/factors[unit])


def eligible(observation, decision_date):
    def stamp(value, end_of_unknown_day=False):
        v=str(value)
        t=datetime.fromisoformat(v.replace('Z','+00:00'))
        if len(v)==10 and end_of_unknown_day:t=datetime.combine(t.date(),time.max)
        return t.replace(tzinfo=timezone.utc)if t.tzinfo is None else t.astimezone(timezone.utc)
    cutoff=stamp(decision_date)
    for name in ['time_end','available_at']:
        value=observation.get(name)
        if not value:return False
        if stamp(value,True)>cutoff:return False
    return observation.get('quality_status')=='passed'


def landsat_ndvi(red_dn, nir_dn, qa_pixel, qa_radsat):
    red=np.asarray(red_dn,dtype=float)*.0000275-.2
    nir=np.asarray(nir_dn,dtype=float)*.0000275-.2
    qa=np.asarray(qa_pixel,dtype=np.uint16)
    # Collection 2 Level 2: fill, dilated cloud, cirrus, cloud, shadow, snow.
    valid=((qa & 0b111111)==0)&(np.asarray(qa_radsat)==0)&((red+nir)>0)&(red>=0)&(nir>=0)
    out=np.full(np.broadcast_shapes(red.shape,nir.shape,qa.shape),np.nan)
    np.divide(nir-red,nir+red,out=out,where=valid)
    return out, valid


def categorical_resampling(method):
    if method not in ['nearest','mode']:raise ValueError('Categorical classes require nearest or mode resampling')
    return method
