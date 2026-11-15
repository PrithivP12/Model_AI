"""Small linear observation operators with explicit analysis-increment ledgers.

No covariance is estimated from synthetic runs. Caller-provided covariance and
measurement errors are assumptions unless supported by independent evidence.
Incompatible support, unknown availability, and nonphysical analyses are rejected
rather than silently rescaled, backdated, or clipped.
"""
from dataclasses import dataclass, asdict
from datetime import date
from copy import deepcopy
import math
import numpy as np


@dataclass(frozen=True)
class Stock:
    name: str
    resource: str
    lower: float = 0.
    upper: float = math.inf


@dataclass(frozen=True)
class Operator:
    name: str
    weights: tuple[float, ...]
    unit: str
    support: str
    semantic: str


@dataclass(frozen=True)
class Observation:
    identifier: str
    value: float
    sd: float
    unit: str
    support: str
    semantic: str
    measured_start: str | None
    measured_end: str | None
    available_at: str | None
    provenance: str
    evidence: str = 'measured'


def update(stocks, values, covariance, operator, observation, *, mode, decision_date,
           state_date, previously_assimilated=()):
    """Return a copied posterior, covariance, and separately attributed mass change.

    Operational updates must be observations of the state at its exact time,
    already available by both that time and the prediction decision. Historical
    interval/unknown-time samples are only admitted by the retrospective mode.
    Independent sequential errors are a caller assumption; duplicate IDs reject.
    """
    if mode not in ('free', 'operational', 'retrospective'):
        raise ValueError('Unknown observation mode')
    x = np.array(values, dtype=float, copy=True)
    p = np.array(covariance, dtype=float, copy=True)
    h = np.asarray(operator.weights, dtype=float)
    n = len(stocks)
    if x.shape != (n,) or p.shape != (n,n) or h.shape != (n,):
        raise ValueError('State/operator/covariance dimensions differ')
    if not np.isfinite(x).all() or not np.isfinite(p).all() or not np.isfinite(h).all():
        raise ValueError('Finite state and covariance required')
    if not np.allclose(p, p.T, atol=1e-10) or np.linalg.eigvalsh(p).min() < -1e-10:
        raise ValueError('Covariance must be symmetric positive semidefinite')
    if not np.any(h) or np.any(h < 0):
        raise ValueError('Nonnegative nonzero stock operator required')
    if any(s.resource not in ('water', 'carbon', 'nitrogen') for s in stocks):
        raise ValueError('Every stock needs a conserved resource')
    if any(not s.lower <= v <= s.upper for s,v in zip(stocks,x)):
        raise ValueError('Prior state violates physical bounds')
    o = observation
    if not math.isfinite(o.value) or not math.isfinite(o.sd) or o.sd <= 0 or not o.provenance:
        raise ValueError('Finite observation, positive uncertainty and provenance required')
    if (o.unit, o.support, o.semantic) != (operator.unit, operator.support, operator.semantic):
        raise ValueError('Observation units, volume or state meaning incompatible')
    if o.value < sum(w*s.lower for w,s in zip(h,stocks) if w):
        raise ValueError('Observation below physical support bound')
    if o.value > sum(w*s.upper for w,s in zip(h,stocks) if w):
        raise ValueError('Observation above physical support bound')
    audit = {'observation': asdict(o), 'operator': asdict(operator), 'mode': mode,
             'decision_date': decision_date, 'state_date': state_date,
             'physical_flux': False, 'forecast_eligible': False,
             'analysis_increment': {'water':0., 'carbon':0., 'nitrogen':0.}}
    reason = None
    if mode == 'free': reason = 'free_running_observations_disabled'
    elif o.identifier in previously_assimilated: reason = 'duplicate_observation'
    elif mode == 'operational':
        if not o.measured_start or not o.measured_end or not o.available_at:
            reason = 'unknown_measurement_or_availability_time'
        elif not (o.measured_start == o.measured_end == state_date):
            reason = 'not_an_observation_of_current_state_time'
        elif max(date.fromisoformat(o.measured_end), date.fromisoformat(o.available_at),
                 date.fromisoformat(state_date)) > date.fromisoformat(decision_date):
            reason = 'postdecision_information'
        elif date.fromisoformat(o.available_at) > date.fromisoformat(state_date):
            reason = 'delayed_report_requires_replay_from_measured_state'
    if reason:
        return {'values':x.tolist(), 'covariance':p.tolist(), 'audit':{**audit, 'applied':False, 'reason':reason}}
    innovation = float(o.value - h@x)
    variance = float(h@p@h + o.sd**2)
    gain = p@h/variance
    posterior = x+gain*innovation
    if any(not s.lower <= v <= s.upper for s,v in zip(stocks,posterior)):
        return {'values':x.tolist(), 'covariance':p.tolist(),
                'audit':{**audit,'applied':False,'reason':'unconstrained_analysis_out_of_bounds'}}
    a = np.eye(n)-np.outer(gain,h)
    posterior_covariance = a@p@a.T+np.outer(gain,gain)*o.sd**2
    for stock, delta in zip(stocks, posterior-x):
        audit['analysis_increment'][stock.resource] += float(delta)
    audit.update(applied=True, reason=None, forecast_eligible=mode=='operational', predicted_observation=float(h@x),
                 innovation=innovation, gain=gain.tolist(),
                 posterior_observation=float(h@posterior),
                 uncertainty_status='Conditional on supplied prior/error covariance; not calibrated coverage')
    return {'values':posterior.tolist(), 'covariance':posterior_covariance.tolist(), 'audit':audit}


def soil_water_operator(layers, top_cm, bottom_cm):
    """Exact modeled-layer volume averages only; no 5cm probe to 0–30cm mapping."""
    edges = np.r_[0., np.cumsum([l.thickness_m*100 for l in layers])]
    if bottom_cm <= top_cm or not any(abs(edges-top_cm)<1e-8) or not any(abs(edges-bottom_cm)<1e-8):
        raise ValueError('Observation must span complete model layers')
    weights = tuple(1/((bottom_cm-top_cm)*10) if a>=top_cm-1e-8 and b<=bottom_cm+1e-8 else 0.
                    for a,b in zip(edges,edges[1:]))
    return Operator('profile_water', weights, 'm3/m3', f'{top_cm:g}:{bottom_cm:g}cm volume average', 'soil_water')


def apply_soil_water(state, layers, observation, covariance, **kwargs):
    op = soil_water_operator(layers, 0, sum(l.thickness_m for l in layers)*100)
    stocks = [Stock(f'water_layer_{i}_mm','water',0,l.mm(l.saturation)) for i,l in enumerate(layers)]
    result = update(stocks, state.water_mm, covariance, op, observation, **kwargs)
    copied = deepcopy(state);copied.water_mm=result['values'];copied.validate(layers)
    return copied, result


def assimilate_checkpoint(engine, checkpoint, observation, covariance, *, kind, mode, decision_date):
    """Apply a compatible end-of-day soil observation and preserve restart ledgers.

    No initial date, nitrogen species, volume, or availability is manufactured.
    Production observations must use the separate harvest-buffer diagnostic.
    """
    from ..processes import SoilState
    if checkpoint.engine_identity != engine.identity:
        raise ValueError('Observation engine/checkpoint identity mismatch')
    if not checkpoint.last_date:
        raise ValueError('A timestamped checkpoint is required for observation updates')
    copied=deepcopy(checkpoint);soil=SoilState(**copied.soil);layers=engine.physical.layers
    depth=sum(l.thickness_m for l in layers)*100
    if kind=='water_profile':
        key='water_mm';values=soil.water_mm;operator=soil_water_operator(layers,0,depth)
        stocks=[Stock(f'water_layer_{i}','water',0,l.mm(l.saturation))for i,l in enumerate(layers)]
    elif kind=='mineral_N_profile':
        key='mineral_n_kg_ha';values=soil.mineral_n_kg_ha
        operator=Operator(kind,tuple(1. for _ in layers),'kg/ha',f'0:{depth:g}cm layer stock','soil_mineral_N_NO3_plus_NH4')
        stocks=[Stock(f'mineral_N_layer_{i}','nitrogen')for i in range(len(layers))]
    elif kind in ('surface_C','surface_N'):
        resource='carbon'if kind=='surface_C'else'nitrogen'
        key='surface_residue_c_kg_ha'if kind=='surface_C'else'surface_residue_n_kg_ha'
        values=[getattr(soil,key)];stocks=[Stock(key,resource)]
        operator=Operator(kind,(1.,),'kg/ha','surface residue stock',resource+'_in_returned_residue')
    else:raise ValueError('Unsupported checkpoint observation; no inferred roots or crop-stage mapping')
    result=update(stocks,values,covariance,operator,observation,mode=mode,decision_date=decision_date,
        state_date=checkpoint.last_date,previously_assimilated=[a['observation']['identifier']for a in copied.analysis_adjustments if a.get('applied')])
    setattr(soil,key,result['values']if isinstance(getattr(soil,key),list)else result['values'][0])
    soil.validate(layers);copied.soil=asdict(soil);copied.analysis_adjustments.append(result['audit'])
    return copied,result
