"""Bounded inspection, not an assertion that every discovered asset is integrated."""
from pathlib import Path, PurePosixPath
import csv
import json
import os
import zipfile
import tarfile
import re
from ..io import sha256, write_json

EXCLUDE = {'.venv', '.git', 'node_modules', '__pycache__', '.sites-checkout', 'dist'}
SUFFIXES = {'.csv','.tsv','.json','.parquet','.tif','.tiff','.nc','.nc4','.h5','.hdf5','.hdf',
            '.grib','.grb','.gpkg','.shp','.zarr','.zip','.gz','.xlsx','.xls','.rda','.bin'}


def safe_members(members, max_size=2_000_000_000):
    size = 0
    for name, length, link in members:
        path = PurePosixPath(name.replace('\\', '/'))
        if not path.parts or path.is_absolute() or '..' in path.parts or ':' in path.parts[0] or link:
            raise ValueError('Unsafe archive member or link')
        size += length
        if size > max_size:
            raise ValueError('Archive expands beyond configured 2 GB limit')
    return size


def inspect_file(path):
    p = Path(path)
    with p.open('rb') as stream:
        magic = stream.read(16)
    result = {'format': 'unidentified', 'kind': 'unknown', 'status': 'unidentified'}
    if magic.startswith(b'\xd0\xcf\x11\xe0'):
        import xlrd
        book=xlrd.open_workbook(p,on_demand=True)
        sheets={name:{'rows':book.sheet_by_name(name).nrows,'columns':book.sheet_by_name(name).ncols}
                for name in book.sheet_names()[:15]}
        book.release_resources()
        return dict(format='xls',kind='tabular_science_candidate',status='metadata_inspected',sheets=sheets)
    if magic.startswith(b'\x89PNG')or magic.startswith(b'\xff\xd8\xff'):
        return dict(format='rendered_image',kind='visualization_not_numeric_science',status='identified')
    if magic.startswith(b'GRIB'):
        return dict(format='grib',kind='numeric_array_candidate',status='needs_optional_product_reader')
    if magic.startswith(b'\x0e\x03\x13\x01'):
        return dict(format='hdf4',kind='numeric_array_candidate',status='needs_optional_hdf4_reader')
    if magic.startswith(b'BZh')and p.suffix=='.rda':
        return dict(format='bzip2_R_data',kind='compressed_science_table_candidate',status='requires_product_reader')
    if p.suffix=='.bin':
        with p.open('rb')as stream:line=stream.readline(1024).decode('ascii',errors='replace')
        if re.match(r'^[A-Z0-9]{11}\d{6}[A-Z]{4}',line):
            return dict(format='ghcnd_fixed_width_candidate',kind='numeric_station_observations',status='header_identified_not_normalized',
                        station_id=line[:11],year=line[11:15],month=line[15:17],element=line[17:21])
        if re.match(r'^[A-Z0-9]{11}\s+-?\d+\.\d+',line):
            return dict(format='station_list_fixed_width_candidate',kind='station_metadata',status='header_identified_not_normalized')
    if magic.startswith(b'PK\x03\x04'):
        with zipfile.ZipFile(p) as z:
            infos = z.infolist()
            if len(infos)>20000:
                raise ValueError('Too many archive members')
            total = safe_members([(i.filename, i.file_size, (i.external_attr>>16)&0o170000==0o120000) for i in infos])
            xlsx = '[Content_Types].xml' in z.namelist() and 'xl/workbook.xml' in z.namelist()
            if xlsx:
                import openpyxl
                w = openpyxl.load_workbook(p, read_only=True, data_only=True)
                sample = {s:list(w[s].values)[:0] for s in []}
                for name in w.sheetnames[:8]:
                    sheet = w[name]
                    sample[name] = {'rows':sheet.max_row,'columns':sheet.max_column,
                                   'first_row':[str(v)[:180] for v in next(sheet.iter_rows(values_only=True), ())[:15]]}
                w.close()
                return dict(format='xlsx',kind='tabular_science_candidate',status='inspected',sheets=sample)
            return dict(format='zip',kind='archive',status='inspected_not_extracted',members=len(infos),
                        expanded_bytes=total,member_sample=[i.filename for i in infos[:15]])
    if magic.startswith(b'PAR1'):
        import pyarrow.parquet as pq
        meta = pq.ParquetFile(p)
        return dict(format='parquet',kind='tabular_science_candidate',status='inspected',
                    rows=meta.metadata.num_rows,columns=meta.schema.names)
    if magic.startswith((b'\x89HDF', b'CDF')):
        if magic.startswith(b'\x89HDF'):
            import h5py
            with h5py.File(p) as h:
                names=[]
                def visitor(name,obj):
                    if len(names)<30 and isinstance(obj,h5py.Dataset):
                        names.append({'variable':name,'shape':obj.shape,'dtype':str(obj.dtype),
                                      'attributes':{k:str(v)[:250]for k,v in list(obj.attrs.items())[:12]}})
                        if obj.size and obj.dtype.kind in 'biuf':
                            try:names[-1]['bounded_first_value']=str(obj[tuple(0 for _ in obj.shape)])
                            except (OSError,TypeError,ValueError):names[-1]['bounded_first_value']='unreadable'
                h.visititems(visitor)
                attributes={k:str(v)[:500]for k,v in list(h.attrs.items())[:20]}
            return dict(format='hdf5',kind='numeric_array',status='metadata_inspected',variables=names,attributes=attributes)
        import netCDF4
        with netCDF4.Dataset(p) as ds:
            vs={k:{'shape':v.shape,'attributes':{a:str(v.getncattr(a))[:250]for a in v.ncattrs()[:12]},
                   'bounded_first_value':str(v[tuple(0 for _ in v.shape)])if v.size else None}
                for k,v in list(ds.variables.items())[:30]}
        return dict(format='netcdf',kind='numeric_array',status='metadata_inspected',variables=vs)
    if magic.startswith((b'II*',b'MM\x00*',b'II+',b'MM\x00+')):
        import rasterio
        with rasterio.open(p) as ds:
            return dict(format=ds.driver,kind='numeric_raster_candidate',status='metadata_inspected',
                        crs=str(ds.crs),bounds=list(ds.bounds),shape=[ds.height,ds.width],bands=ds.count,
                        units=list(ds.units),scales=list(ds.scales),offsets=list(ds.offsets),
                        nodata=str(ds.nodata),tags=ds.tags(),
                        bounded_first_value=str(ds.read(1,window=rasterio.windows.Window(0,0,1,1),masked=True)[0,0]))
    if p.suffix in {'.gz','.tar'} and tarfile.is_tarfile(p):
        with tarfile.open(p) as t:
            members=t.getmembers()
            if len(members)>20000:raise ValueError('Too many archive members')
            size=safe_members([(m.name,m.size,m.issym()or m.islnk())for m in members])
        return dict(format='tar',kind='archive',status='inspected_not_extracted',expanded_bytes=size,
                    member_sample=[m.name for m in members[:15]])
    if magic.lstrip().startswith((b'{', b'[')):
        if p.stat().st_size>30_000_000:
            return dict(format='json',kind='large_json',status='header_only_size_limit')
        d=json.loads(p.read_text())
        keys=list(d)[:30]if isinstance(d,dict)else []
        payload = d.get('data',d.get('response',d)) if isinstance(d,dict) else None
        is_power=isinstance(payload,dict)and 'parameter'in (payload.get('properties')or {})
        is_stac=isinstance(payload,dict)and ('stac_version'in payload or 'features'in payload and 'context'in payload)
        return dict(format='json',kind='numeric_power'if is_power else 'catalog_metadata'if is_stac else
                    'derived_observations'if 'observations'in keys else 'json_unknown_semantics',status='inspected',
                    keys=keys,product=payload.get('header',{})if is_power else None)
    if p.suffix.lower()in {'.csv','.tsv'}:
        encoding='utf-8-sig'
        try:
            with p.open(encoding=encoding)as stream:
                sample=stream.read(32768)
        except UnicodeDecodeError:
            encoding='cp1252'
            with p.open(encoding=encoding)as stream:sample=stream.read(32768)
        delimiter='\t'if p.suffix.lower()=='.tsv'else ','
        rows=list(csv.reader(sample.splitlines(),delimiter=delimiter))[:4]
        return dict(format='delimited',kind='tabular_science_candidate',status='sample_inspected',
                    encoding=encoding,columns=rows[0]if rows else [],sample_rows=max(0,len(rows)-1))
    if magic.startswith(b'%PDF'):return dict(format='pdf',kind='documentation',status='not_numeric_science')
    return result


def inventory(roots, output, max_files=5000, max_bytes=2_000_000_000):
    rows=[];inspected_roots=[];total=0;bounded=False;seen={}
    for root in roots:
        root=Path(root).expanduser().resolve()
        inspected_roots.append({'path':str(root),'exists':root.exists()})
        if not root.exists():continue
        for base, dirs, files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in EXCLUDE and not d.startswith('.') and not Path(base,d).is_symlink())
            for name in sorted(files):
                p=Path(base,name)
                if p.is_symlink()or p.suffix.lower()not in SUFFIXES:continue
                size=p.stat().st_size
                if len(rows)>=max_files or total+size>max_bytes:
                    bounded=True;continue
                total+=size
                row={'path':str(p),'bytes':size,'sha256':sha256(p)}
                try:row.update(inspect_file(p))
                except Exception as exc:row.update(status='inspection_failed',kind='unknown',error=f'{type(exc).__name__}: {str(exc)[:180]}')
                row['duplicate_of']=seen.get(row['sha256'])
                seen.setdefault(row['sha256'],str(p))
                rows.append(row)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    cols=['path','bytes','sha256','format','kind','status','duplicate_of','error','metadata']
    with output.open('w',newline='')as stream:
        writer=csv.DictWriter(stream,fieldnames=cols);writer.writeheader()
        for r in rows:writer.writerow({**{k:r.get(k)for k in cols if k!='metadata'},'metadata':json.dumps({k:v for k,v in r.items()if k not in cols},default=str)})
    audit={'roots':inspected_roots,'files_inspected':len(rows),'bytes_hashed':total,'limits':{'max_files':max_files,'max_bytes':max_bytes},
           'limit_reached':bounded,'excluded_directories':sorted(EXCLUDE),'uninspected_locations':'No claim about other locations, including Downloads.',
           'integration_claim':'Discovery and bounded inspection only; product-specific validation is separate.'}
    write_json(output.with_suffix('.scope.json'),audit)
    return audit
