"""Conservative plot-specific operations; ambiguous records never become doses."""
import re
import json
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd
import openpyxl
from ..io import sha256,write_json


def text(value):
    if value is None or str(value).strip().lower()in ['', 'none','n/a','na']:return ''
    return str(value).strip()


def numeric(value):
    try:
        v=float(value)
        return v if np.isfinite(v)else None
    except (TypeError,ValueError):return None


def scope(operation,plot):
    """Return applies/excluded/ambiguous plus a reason; do not extract arbitrary numbers."""
    comment=text(operation.get('comments'));lower=comment.lower()
    if 'assum'in lower or 'populated'in lower:return 'ambiguous','curator assumption'
    rotation=text(operation.get('croprot')).upper()
    family=plot.get('rotation_family',plot.get('family','')).split('_')[0]
    if rotation and rotation not in {'CC','CS','CSW','CWS'}:return 'ambiguous','unsupported rotation scope'
    if rotation and rotation!=family:return 'excluded','different cropping family'
    crop=text(operation.get('cashcrop')).lower()
    fertilizer_crop=text(operation.get('fertilizercrop')).lower()
    if crop not in ['corn','maize']or fertilizer_crop and fertilizer_crop not in ['corn','maize']:
        return 'excluded','different or unspecified cash crop'
    plot_id=text(plot.get('plot_id',plot.get('plot'))).upper()
    if re.search(r'not applied to the following plots',lower):
        tail=re.split(r'need\s*:',comment,flags=re.I)[-1]
        ids=set(re.findall(r'\b\d{2,4}[EW]?\b',tail.upper()))
        return ('applies','explicit sensor nonapplication list')if plot_id in ids else ('excluded','different plot in nonapplication list')
    match=re.search(r'application made to plots?\s+([\d\w,\s]+)\.?$',comment,re.I)
    if match:
        ids=set(re.findall(r'\b\d{2,4}[EW]?\b',match.group(1).upper()))
        return ('applies','explicit plot list')if plot_id in ids else ('excluded','different plot')
    treatments=set(re.findall(r'\bNIT[1-4]\b',comment.upper()))
    if treatments and ('only'in lower or 'plots with treatment'in lower):
        return ('applies','explicit N treatment')if plot.get('nitrogen_treatment',plot.get('nitrogen'))in treatments else ('excluded','different N treatment')
    if 'all cc plots'in lower and family!='CC':return 'excluded','CC scope'
    if 'plot'in lower or 'block'in lower or 'corner'in lower or 'watershed'in lower or 'lysimeter'in lower:
        if not any(s in lower for s in ['all plots','all cc plots','starter fertilizer','planter']):
            return 'ambiguous','unparsed spatial or treatment scope'
    return 'applies','site/year/crop scope with no contradictory qualifier'


def resolve_operations(operations,plot):
    year=int(plot['year']);site=plot.get('site_id',plot.get('site'));events=[];uncertain=[];plantings=[]
    for op in operations:
        if text(op.get('uniqueid'))!=site or numeric(op.get('cropyear'))!=year:continue
        if op.get('operation')not in ['plant_corn','fertilizer_synthetic','fertilizer_manure']:continue
        status,reason=scope(op,plot)
        if status=='excluded':continue
        if status=='ambiguous':
            uncertain.append({'row':op['_row'],'reason':reason,'operation':op['operation']});continue
        when=op.get('date');when=str(when)[:10]if when else None
        if op['operation']=='plant_corn':
            if when and re.fullmatch(r'\d{4}-\d{2}-\d{2}',when):
                plantings.append({'date':when,'variety':text(op.get('planthybrid'))or None,'row':op['_row'],
                    'replant':bool(re.search(r'replant|three times',text(op.get('comments')),re.I))})
            continue
        values={k:numeric(op.get(k))for k in ['nitrogenelem','phosphoruselem','potassiumelem']}
        if any(v is not None and v<0 for v in values.values()):raise ValueError('Negative nutrient dose')
        if plot.get('nitrogen_treatment',plot.get('nitrogen'))=='NIT1'and (values['nitrogenelem']or 0)>0:
            if reason=='site/year/crop scope with no contradictory qualifier'and 'all plots'not in text(op.get('comments')).lower():
                continue
        events.append({'date':when,'source_row':op['_row'],'scope_reason':reason,**values})
    n=[e['nitrogenelem']for e in events if e['nitrogenelem']is not None]
    unresolved_fertilizer=any(e['operation'].startswith('fertilizer')for e in uncertain)
    treatment=plot.get('nitrogen_treatment',plot.get('nitrogen'))
    known_total=sum(n)if n and not unresolved_fertilizer else None
    conflict=treatment=='NIT1'and known_total is not None and known_total>1e-8
    if conflict:known_total=None
    if treatment=='NIT1'and not conflict:known_total=0.
    dates=sorted({p['date']for p in plantings});replant=any(p['replant']for p in plantings)
    planting_date=dates[-1]if dates else None
    # A replant date is explicit; incomplete emergence histories remain marked.
    return {'plot_id':text(plot.get('plot_id',plot.get('plot'))),'site_id':site,'year':year,
        'fertilizer_n_kg_ha_resolved':known_total,'known_event_n_sum_kg_ha':sum(n)if n else None,
        'fertilizer_scope_complete':not unresolved_fertilizer,'zero_treatment_conflict':conflict,
        'planting_date_resolved':planting_date,'replant_or_multiple_dates':replant or len(dates)>1,
        'variety_resolved':'; '.join(sorted({p['variety']for p in plantings if p['variety']}))or None,
        'operation_source_rows_json':json.dumps([e['source_row']for e in events]),
        'operation_events_json':json.dumps(events),'unresolved_operations_json':json.dumps(uncertain),
        'management_basis':'recorded actual operations; not an archived preplant plan'}


def prepare_management(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    raw=root/'raw/Sustainable_Corn_Research_Data_2011-2015_0.xlsx'
    w=openpyxl.load_workbook(raw,read_only=True,data_only=True)
    it=w['Field Operations'].iter_rows(values_only=True);header=next(it);next(it);units=next(it)
    for name in ['nitrogenelem','phosphoruselem','potassiumelem']:
        if units[header.index(name)]!='kg per hectare':raise ValueError('Unverified elemental fertilizer units')
    operations=[{**dict(zip(header,r)),'_row':i}for i,r in enumerate(it,start=4)];w.close()
    plots=pd.read_csv(root/'plot_features.csv',dtype={'plot':str})
    result=pd.DataFrame([resolve_operations(operations,r)for r in plots.to_dict('records')])
    result.to_parquet(out/'plot_management.parquet',index=False);result.to_csv(out/'plot_management.csv',index=False)
    report={'raw_sha256':sha256(raw),'plot_years':len(result),
        'resolved_n_totals':int(result.fertilizer_n_kg_ha_resolved.notna().sum()),
        'resolved_planting_dates':int(result.planting_date_resolved.notna().sum()),
        'zero_treatment_conflicts':int(result.zero_treatment_conflict.sum()),
        'maximum_resolved_n_kg_ha':float(result.fertilizer_n_kg_ha_resolved.max()),
        'scope_policy':'Positive explicit plot lists, exclusion lists and N treatment scope applied before summing. Ambiguous or curator-assumed events prevent a complete total.',
        'forecast_vintage':'unknown; actual operations only, not evidence that all doses were planned before planting'}
    write_json(out/'management_quality.json',report);return report
