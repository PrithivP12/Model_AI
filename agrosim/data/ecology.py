"""Plot-matched measured insect/damage records; sampling methods remain separate."""
from collections import defaultdict
from pathlib import Path
import hashlib,json
import pandas as pd
import openpyxl
from .management import text,numeric
from ..io import sha256,write_json


def ingest_ecology(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    raw=root/'raw/Sustainable_Corn_Research_Data_2011-2015_0.xlsx'
    w=openpyxl.load_workbook(raw,read_only=True,data_only=True);it=w['IPM'].iter_rows(values_only=True)
    h=next(it);next(it);next(it);rows=[];invalid=0;definitions={}
    for index,values in enumerate(it,start=4):
        r=dict(zip(h,values));site=text(r['uniqueid']);plot=text(r['plotid']);year=numeric(r['year'])
        if year is None:continue
        try:when=pd.Timestamp(r['date']);when=when if pd.notna(when)else None
        except (ValueError,TypeError):when=None
        if when is None:when=pd.Timestamp(year=int(year)+1,month=1,day=1);date_quality='year only; next-year availability'
        else:date_quality='recorded measurement date'
        for kind,value,parts,unit in [
            ('insect',numeric(r['IPM07']),[text(r['IPM01']),text(r['IPM02']),text(r['IPM05']),text(r['IPM06'])],text(r['IPM08'])),
            ('damage',numeric(r['IPM14']),[text(r['IPM10']),text(r['IPM11']),text(r['IPM12']),text(r['IPM13'])],'%')]:
            if value is None:continue
            if value<0 or kind=='damage'and value>100:invalid+=1;continue
            label='|'.join([kind,*parts,unit]);key='bio_'+hashlib.sha256(label.encode()).hexdigest()[:12]
            definitions[key]={'kind':kind,'attributes':parts,'unit':unit,
                'interpretation':'method-specific sampled activity/abundance, not whole-field population'if kind=='insect'else 'visual damage rating, not a confirmed pathogen load'}
            rows.append({'site_id':site,'plot_id':plot,'year':int(year),'date':when.date().isoformat(),'date_quality':date_quality,
                'feature_id':key,'kind':kind,'value':value,'unit':unit,'sampling_method':text(r['IPM01'])or None,
                'common_name':text(r['IPM02'])or None,'plant_assessed':text(r['IPM10'])or None,
                'damage_type':text(r['IPM12'])or None,'plant_sample':text(r['IPM09'])or None,'raw_row':index})
    w.close();frame=pd.DataFrame(rows);frame.to_parquet(out/'ecology_observations.parquet',index=False)
    write_json(out/'ecology_feature_dictionary.json',definitions)
    report={'source_doi':'10.15482/USDA.ADC/1411953','raw_sha256':sha256(raw),'observations':len(frame),
        'features':len(definitions),'invalid_values_excluded':invalid,'sites':int(frame.site_id.nunique()),
        'kinds':frame.kind.value_counts().to_dict(),'unmatched_ecosystems_inferred':False,
        'limits':['Trap methods and measurement units are never pooled into a universal insect-abundance score.',
            'Visual disease ratings are symptom observations, not microbial community assays.',
            'No causal pest/pollination contribution to yield is inferred from mere occurrence.']}
    write_json(out/'ecology_quality.json',report);return report


def enrich_features(features,management,ecology,output):
    data=pd.read_parquet(features);operations=pd.read_parquet(management);observations=pd.read_parquet(ecology)
    d=data.merge(operations,on=['site_id','plot_id','year'],validate='one_to_one')
    d['planting_doy']=pd.to_datetime(d.planting_date_resolved).dt.dayofyear
    d['recorded_n_total_conditional']=d.fertilizer_n_kg_ha_resolved
    d['seed_variety_recorded']=d.variety_resolved.fillna('unknown')
    n_sowing=[]
    for r in d.to_dict('records'):
        if r['zero_treatment_conflict']:n_sowing.append(None);continue
        if r['nitrogen_treatment']=='NIT1'and not r['zero_treatment_conflict']:n_sowing.append(0.);continue
        if not r['fertilizer_scope_complete']:n_sowing.append(None);continue
        if pd.isna(r['planting_date_resolved']):n_sowing.append(None);continue
        events=json.loads(r['operation_events_json'])
        values=[e['nitrogenelem']for e in events if e['date']and e['date']<=r['planting_date_resolved']and e['nitrogenelem']is not None]
        n_sowing.append(sum(values)if values else None)
    d['n_recorded_by_sowing_kg_ha']=n_sowing
    grouped={key:g for key,g in observations.groupby(['site_id','plot_id'])};extra=[]
    for row in d.to_dict('records'):
        g=grouped.get((row['site_id'],row['plot_id']));out={}
        if g is not None:
            cutoff=f"{row['year']}-04-01";earliest=f"{row['year']-2}-04-01"
            prior=g[(g.date<cutoff)&(g.date>=earliest)]
            current=g[(g.date>=f"{row['year']}-01-01")&(g.date<=f"{row['year']}-08-01")]
            for prefix,part in [('prior_',prior),('inseason_',current)]:
                for key,a in part.groupby('feature_id'):out[prefix+key]=float(a.value.mean())
        extra.append(out)
    expanded=pd.concat([d,pd.DataFrame(extra,index=d.index)],axis=1)
    expanded.to_parquet(output,index=False)
    return {'records':len(expanded),'prior_biological_features':sum(c.startswith('prior_bio_')for c in expanded),
        'inseason_biological_features':sum(c.startswith('inseason_bio_')for c in expanded),
        'n_totals_resolved':int(expanded.recorded_n_total_conditional.notna().sum()),
        'postdecision_ecology_in_preplant_features':False,'source_publication_vintage':'not reconstructed'}
