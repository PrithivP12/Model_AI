"""Original measured LAI/stage cells, preserving time and observation support."""
from pathlib import Path
from datetime import datetime,date,timedelta
import openpyxl
from ..io import sha256,write_json
from ..data.lirf import measured,valid


def extract_lai(root,years=(2008,2009,2010)):
    rows=[];stages=[];inventory=[]
    for year in years:
        if year not in (2008,2009,2010):raise ValueError('Only exposed development years authorized for this extraction')
        p=Path(root)/'crop-response-lirf/raw'/f'maize{year}.xlsx';w=openpyxl.load_workbook(p,read_only=True,data_only=True);digest=sha256(p)
        for t in range(1,7):
            sheet=w[f'Tmnt{t}']
            if sheet['P3'].value!='LAI':raise ValueError('Original LAI column changed; verify schema')
            for r in sheet.iter_rows(min_row=5):
                if not valid(r[0].value)or not 1<=r[0].value<=366:continue
                cell=r[15]
                if not measured(cell):continue
                if not 0<=cell.value<=15:raise ValueError('Measured LAI outside physical audit bounds')
                day=date(year,1,1)+timedelta(days=int(r[0].value)-1)
                rows.append({'case_id':f'LIRF:{year}:{t}','year':year,'treatment':str(t),'date':str(day),'value':float(cell.value),
                    'unit':'m2 leaf/m2 ground','source_sheet':sheet.title,'source_cell':cell.coordinate,'sha256':digest,
                    'quality':'original measurement highlight theme9, finite LAI','support':'treatment-mean manually measured leaf area; roots excluded',
                    'available_at_historic_decision':None,'measurement_error_sd':None,'role':'development state validation, not forecast assimilation'})
        for cells in w['CropLog'].iter_rows():
            values=[c.value for c in cells]
            if isinstance(values[0],datetime)and str(values[1]).lower()=='growth stage':
                stages.append({'year':year,'date':str(values[0].date()),'stage_raw':str(values[2]),'notes':str(values[3]),
                    'source_sheet':'CropLog','source_row':cells[0].row,'sha256':digest,
                    'support':'field/block notes; not assumed treatment-specific exact crop-stage truth'})
        inventory.append({'year':year,'sha256':digest,'sheets':w.sheetnames,'measured_LAI_records':sum(r['year']==year for r in rows)})
        w.close()
    return {'LAI':rows,'stage_notes':stages,'workbooks':inventory,
        'prior_missingness':'2008 daily LAI absent;2009 only four treatments measured;2010 is exposed development.',
        'tissue_support':'Existing2008–2009 C&N concentrations and biomass-sample HI; do not infer separate organ stocks without matched dry component masses.',
        'root_support':'Methodology explicitly says rooting depth estimated from water uptake, not directly measured.',
        'source_methodology':'research/completion-pass/methodology.txt, Plant Measurements section'}


def write_observation_audit(root,out):
    report=extract_lai(root);write_json(Path(out)/'observations.json',report);return report
