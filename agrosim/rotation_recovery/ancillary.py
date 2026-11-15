"""Audit other workbook sensors before treating them as equivalent observations."""
from pathlib import Path
from collections import Counter,defaultdict
import json
import openpyxl
import pandas as pd
from ..io import write_json,sha256
from .audit import numeric,text,stamp


def audit_ancillary(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if(out/'report.json').exists():raise RuntimeError('Preserve completed ancillary sensor audit')
    records=json.loads((root/'reports/rotation-recovery/input-audit/records.json').read_text());plots={(r['site_id'],r['plot_id'])for r in records.values()}
    source=root/'rotation-learning-v1/raw/Sustainable_Corn_Research_Data_2011-2015_0.xlsx';w=openpyxl.load_workbook(source,read_only=True,data_only=True)
    it=w['GHG'].iter_rows(values_only=True);header=next(it);description=next(it);units=dict(zip(header,next(it)));rows=[]
    for i,row in enumerate(it,start=4):
        r=dict(zip(header,row));y=numeric(r.get('year'))
        if(text(r.get('uniqueid')),text(r.get('plotid')))not in plots or y is None or y>2014:continue
        values={k:numeric(r.get(k))for k in ['GHG11','GHG12','GHG13','GHG14']}
        if all(v is None for v in values.values()):continue
        rows.append({'site_id':text(r['uniqueid']),'plot_id':text(r['plotid']),'source_row':i,'date':stamp(r['date']),'year':int(y),'position':text(r['position']),**values})
    frame=pd.DataFrame(rows);frame.to_csv(out/'GHG_sensor_source_values.csv',index=False)
    by_site={}
    for site,g in frame.groupby('site_id'):
        v=g.GHG11.dropna();by_site[site]={'moisture_values':len(v),'minimum_source_value':float(v.min())if len(v)else None,
            'median_source_value':float(v.median())if len(v)else None,'maximum_source_value':float(v.max())if len(v)else None,
            'values_over_declared_fraction_range':int((v>1).sum()),'values_within_fraction_range_but_contextually_ambiguous':int(((v>=0)&(v<=1)).sum())}
    alternatives={}
    for name in ['Water_Tile Flow','Water_Water Quality','Water_Water Table']:
        it=w[name].iter_rows(values_only=True);h=next(it);next(it);u=dict(zip(h,next(it)));count=0
        for row in it:
            r=dict(zip(h,row));t=stamp(r.get('date'))
            if(text(r.get('uniqueid')),text(r.get('plotid')))in plots and t and '2011-01-01'<=t<='2014-12-31':count+=1
        alternatives[name]={'matched_source_rows':count,'units':u,'not_equivalent_to_modeled_field_average_water_content':True}
    # Workbook notes were checked, without inferring a correction from value magnitude.
    notes=[]
    for row in w['Notes'].iter_rows(values_only=True):
        if any(k in str(row).lower()for k in ['ghg11','moisture','water filled','wfps','volumetric']):notes.append([str(v)if v is not None else None for v in row])
    w.close()
    report={'status':'ancillary_observation_support_audited','source_sha256':sha256(source),'GHG_units':{k:units[k]for k in ['GHG11','GHG12','GHG13','GHG14']},
        'GHG_coverage':{k:int(frame[k].notna().sum())for k in ['GHG11','GHG12','GHG13','GHG14']},'by_site':by_site,'source_notes_matching_water':notes,
        'water_validation_status':'unverified_units_and_nonequivalent_support',
        'reason':'GHG11 is labeled m3/m3 at 5cm but site values largely range in tens and sometimes exceed plausible volumetric saturation after /100. Percentage VWC vs WFPS/mixed units are not resolved by the available notes. Do not guess /100, cherry-pick the few <=1 cells, or equate a 5cm point probe with a 0–30cm model layer.',
        'initial_moisture_assimilation':False,'soil_N_status':'GHG13 supplies nitrate only at 0–10cm; GHG14 is absent in the matched records. Do not fill missing ammonium with zero or score it as measured total mineral N.',
        'other_water_tables':alternatives,'equivalent_water_RMSE_available':False,'recommendation':None}
    write_json(out/'report.json',report);return report
