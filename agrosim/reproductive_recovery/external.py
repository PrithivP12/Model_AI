"""Compatible partial checks on accessed tables, not whole-crop validation."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from ..io import write_json,sha256


def audit_external(root,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    base=Path(root)/'reports/product-validation/external-audit/raw'
    gh=pd.read_csv(base/'Ghana_ground_data_v5.csv');ken=pd.read_csv(base/'biophysical_measurements.csv')
    stage=pd.read_csv(base/'Pheno_stages_BBCH.csv');climate=pd.read_csv(base/'Agroclimatic.csv')
    stage['date']=pd.to_datetime(stage.Date,format='%m/%d/%Y');ken['date']=pd.to_datetime(ken.Date,format='%m/%d/%Y')
    climate['date']=pd.to_datetime(climate.Date,format='%m/%d/%Y')
    if climate.date.duplicated().any():raise ValueError('Weather dates are not unique')
    all_plant=stage[stage.Stage.str.lower().eq('planting')].copy()
    # Intercrop species share plot IDs and dates. A maize LAI measurement must
    # never be duplicated as cowpea/grass phenology just because IDs match.
    maize=stage[stage.Crop.str.startswith('Maize',na=False)].copy()
    plant=maize[maize.Stage.str.lower().eq('planting')].copy()
    plant[['Station','Xid','Crop','date','BBCH']].to_csv(out/'observed_Kenya_planting.csv',index=False)
    availability=[]
    for study in ['Ghana','Kenya']:
        for field in ['sowing_dates','variety','management_history','soil_hydraulics','initial_water_N','daily_temperature_mean',
            'daily_temperature_extrema','daily_rain','daily_radiation','fertilizer_irrigation','measurement_dates',
            'LAI_definition','grain_yield_units_moisture']:
            status='not_established';evidence='Not documented in the audited tabular subset; never filled as a measured input.'
            if study=='Kenya':
                if field=='sowing_dates':status='observed_partial';evidence=f'{len(plant)} Planting BBCH0 rows, {plant.Xid.nunique()} plot identifiers. Dates recovered from Pheno_stages_BBCH.csv; not necessarily every plot-season.'
                if field in ['daily_temperature_mean','daily_rain']:status='observed';evidence='Agroclimatic.csv; Kenya README defines Celsius and mm, daily stations; gaps must be checked.'
                if field=='measurement_dates':status='observed';evidence='Explicit %m/%d/%Y dates; plot/site identities retained.'
                if field=='LAI_definition':status='partial';evidence='README: biweekly plot medians, dimensionless. Green-versus-total leaf and measurement instrument/support not established.'
            elif field=='measurement_dates':
                a=pd.to_datetime(gh.date,format='%d/%m/%Y',errors='coerce');b=pd.to_datetime(gh.date,format='%m/%d/%Y',errors='coerce')
                status='observed_qualified';evidence=f'Day-first valid {a.notna().sum()}/{len(gh)}; month-first valid {b.notna().sum()}/{len(gh)}. No silent per-row format mixing.'
            elif field=='LAI_definition':status='partial';evidence='Four named LAI readings per field/date; dimensional ratio by repository description. Exact green/total and spatial replicate support unresolved.'
            availability.append({'study':study,'field':field,'status':status,'evidence':evidence})
    pd.DataFrame(availability).to_csv(out/'evidence_availability.csv',index=False)
    # Within-visit heterogeneity across up to four readings. Not instrument
    # error, independent prediction, or a calibrated simulator validation.
    heterogeneity=[];negative=0
    for row in gh.to_dict('records'):
        values=[float(row[k])for k in ['lai1','lai2','lai3','lai4']if pd.notna(row[k])and row[k]>=0]
        negative+=sum(pd.notna(row[k])and row[k]<0 for k in ['lai1','lai2','lai3','lai4'])
        if len(values)<2:continue
        heterogeneity.append({'field':row['field_code'],'date_raw':row['date'],'n':len(values),
            'mean_LAI':float(np.mean(values)),'within_visit_SD':float(np.std(values,ddof=1)),
            'leave_one_reading_out_MAE':float(np.mean([abs(v-np.mean(values[:i]+values[i+1:]))for i,v in enumerate(values)]))})
    pd.DataFrame(heterogeneity).to_csv(out/'Ghana_LAI_heterogeneity.csv',index=False)
    joins=[];thermal=[]
    for (station,xid,season),g in maize.groupby(['Station','Xid','Crop'],sort=True):
        planting=g[g.Stage.str.lower().eq('planting')]
        if len(planting)!=1:continue
        sow=planting.iloc[0].date
        future_plant=plant[(plant.Xid==xid)&(plant.date>sow)].date
        end=future_plant.min()if len(future_plant)else pd.Timestamp.max
        observed=ken[(ken['Plot ID']==xid)&(ken.date>=sow)&(ken.date<end)]
        for row in observed.to_dict('records'):
            before=g[g.date<=row['date']].sort_values('date')
            latest=before.iloc[-1]if len(before)else None
            joins.append({'station':station,'plot':xid,'season':season,'planting_date':str(sow.date()),
                'measurement_date':str(row['date'].date()),'days_after_observed_planting':(row['date']-sow).days,
                'LAI':float(row['LAI'])if pd.notna(row['LAI'])else None,
                'latest_observed_stage':None if latest is None else latest.Stage,
                'latest_stage_date':None if latest is None else str(latest.date.date()),
                'stage_is_interpolated':False})
        emergence=g[g.Stage.str.lower().eq('emergence')]
        if len(emergence)!=1:continue
        emerge=emergence.iloc[0].date;code='KBT'if station=='Kabete'else'KSH'if station=='Kishenyi'else'MKT'
        window=climate[(climate.date>sow)&(climate.date<=emerge)]
        complete=len(window)==(emerge-sow).days and window[code+'_Tmp'].notna().all()
        thermal.append({'station':station,'plot':xid,'season':season,'planting_date':str(sow.date()),
            'emergence_date':str(emerge.date()),'days':(emerge-sow).days,'temperature_complete':bool(complete),
            'mean_temperature_base8_proxy_GDD':float(np.maximum(0,window[code+'_Tmp'].to_numpy()-8).sum())if complete else None,
            'exact_engine_thermal_time':False,'reason':'Mean-temperature proxy differs from the engine separately clipped daily extrema; no stage coefficient fitted.'})
    pd.DataFrame(joins).to_csv(out/'Kenya_LAI_observed_seasons.csv',index=False)
    if len({(r['plot'],r['measurement_date'])for r in joins})!=len(joins):raise ValueError('A measurement was assigned to multiple maize seasons')
    pd.DataFrame(thermal).to_csv(out/'Kenya_observed_emergence_thermal_proxy.csv',index=False)
    report={'status':'external_partial_support_recovered','availability_matrix':availability,
        'correction_to_previous_audit':'Kenya DOES contain observed planting dates in stage rows. The earlier claim of missing sowing support was too broad; complete full-model inputs remain absent.',
        'Kenya_all_species_planting_rows':len(all_plant),'Kenya_maize_planting_rows':len(plant),'Kenya_planting_plots':int(plant.Xid.nunique()),'Kenya_LAI_rows_joined_to_observed_sowing':len(joins),
        'Kenya_observed_emergence_intervals':len(thermal),'Kenya_complete_mean_temperature_intervals':sum(r['temperature_complete']for r in thermal),
        'Ghana_visits_with_multiple_nonnegative_LAI':len(heterogeneity),'Ghana_negative_LAI_values_flagged':int(negative),
        'Ghana_median_within_visit_LAI_SD':float(np.median([r['within_visit_SD']for r in heterogeneity]))if heterogeneity else None,
        'partial_process_scope':'Observation consistency/heterogeneity and dated phenology operators only. No fictional soil or management and no free-running growth/yield score.',
        'input_sha256':{p.name:sha256(p)for p in sorted(base.glob('*.csv'))},
        'new_independent_model_accuracy':None,'source_tables_already_accessed':True,'whole_model_validation_eligible':False}
    write_json(out/'report.json',report);return report
