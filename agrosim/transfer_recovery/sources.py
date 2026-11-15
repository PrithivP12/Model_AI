"""Offline source-use evidence; product names are not proof of field inference."""
from pathlib import Path
import json
import yaml
from ..io import write_json,sha256

PROFILES={
 'power':{'variable':'Tmin/Tmax, corrected daily rain and shortwave radiation',
    'native_support':'Parameter-specific coarse source grids; meteorology commonly 0.5° x 0.625° and solar source/product dependent. Returned point coordinates do not prove plot support.',
    'temporal_support':'Daily UTC integrals/means/extrema in this cache',
    'limitations':'Grid representation and UTC/local-day mismatch; no per-point error SD supplied; no fake uncertainty.',
    'documentation':'https://power.larc.nasa.gov/docs/methodology/data/sources_meteorology/'},
 'imerg':{'variable':'Precipitation estimate','native_support':'0.1° (~10km), product/version dependent footprint',
    'temporal_support':'Half-hourly/daily/monthly product-specific; local sample is a selected product, not an interchangeable time average.',
    'limitations':'Early/Late/Final latency and gauge calibration differ; use correct accumulation units and availability.',
    'documentation':'https://gpm.nasa.gov/resources/documents/imerg-v07-technical-documentation'},
 'smap':{'variable':'Surface soil moisture/freeze thaw; modeled root zone is a separate product',
    'native_support':'Product-dependent km grids; surface retrieval senses approximately top5cm, not a 0–30cm field average.',
    'temporal_support':'Revisit/product-dependent; no data before the 2015 mission for the 2011–2014 rotation cohort.',
    'limitations':'Retrieval flags, vegetation/freeze/land and support mismatch; coarse retrieval cannot be plot truth.',
    'documentation':'https://smap.jpl.nasa.gov/mission/description/'},
 'landsat':{'variable':'Surface reflectance/vegetation context and thermal products',
    'native_support':'Optical surface reflectance 30m, thermal native support different; actual product footprint and QA required.',
    'temporal_support':'Acquisition-specific; platform/revisit and cloud gaps vary.',
    'limitations':'Cloud/shadow/saturation QA and scale offsets required; reflectance/NDVI is not directly LAI or yield.',
    'documentation':'https://www.usgs.gov/landsat-missions/landsat-surface-reflectance'},
 'modis':{'variable':'Vegetation indices','native_support':'MOD13Q1 250m; other MOD13 products differ.',
    'temporal_support':'MOD13Q1 16-day composite, not an instantaneous daily crop measurement.',
    'limitations':'Quality flags and composite support; LAI/biomass needs an independently validated operator.',
    'documentation':'https://modis.gsfc.nasa.gov/data/dataprod/mod13.php'},
 'srtm':{'variable':'Elevation/terrain context','native_support':'SRTMGL1 approximately30m posting; terrain/vegetation and void limitations.',
    'temporal_support':'February2000 mission, static terrain representation.',
    'limitations':'Posting is not vertical accuracy; slope cannot imply calibrated erosion or lateral water routing.',
    'documentation':'https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm'},
 'ecostress':{'variable':'Thermal/ET context, model-derived ET products',
    'native_support':'Approximately70m, product/version and geolocation dependent.',
    'temporal_support':'ISS acquisition-specific instantaneous/daytime support, not a complete daily flux series.',
    'limitations':'Cloud/geolocation/error flags required; ET is retrieved/model-derived, not independent truth for all crop fluxes.',
    'documentation':'https://data.nasa.gov/dataset/ecostress-gridded-evapotranspiration-instantaneous-and-daytime-l3-global-70-m-v002-b0768'},
}


def audit_sources(root,output):
    root=Path(root);out=Path(output);out.mkdir(parents=True,exist_ok=False)
    registry=yaml.safe_load((root/'configs/data_sources.yaml').read_text());rows=[]
    for record in registry['sources']:
        local=[]
        for rel in record.get('legacy_evidence',[]):
            path=(root/'configs'/rel).resolve()
            if path.exists():
                d=json.loads(path.read_text());obs=d.get('observations',[])
                local.append({'path':str(path.relative_to(root)),'sha256':sha256(path),'observations':len(obs),
                    'sample_quality_statuses':sorted({str(o.get('quality','unknown'))for o in obs}),'state':d.get('status')})
        sid=record['source_id'];family=record.get('family_id',sid)
        spec=PROFILES.get('smap'if sid.startswith('smap')else family,PROFILES.get(sid))
        rows.append({'source_id':sid,'resource':record['resource'],'product_id':record.get('product_id'),
            'documented_role':record.get('role'),'source_documentation':record['documentation_url'],
            'support_guidance':spec,'local_assets':local,'consumed_by_transfer_model':sid=='power',
            'phase_role':'Historical gridded weather forcing / future replay scenario'if sid=='power'else'Existing context or adapter; no new numerical physiology control',
            'claim':'No field-scale precision, calibration benefit or complete integration inferred from a sample.',
            'unknown_metadata_remains_unknown':True})
    manifest=json.loads((root/'reports/rotation-recovery/weather-exact/manifest.json').read_text())
    power=[{'site':site,'raw_sha256':v['raw_sha256'],'normalized_sha256':sha256(root/'reports/rotation-recovery/weather-exact'/(site+'.csv')),
            'daily_records':v['days'],'point':v['requested_point'],'queried_point':v['queried_point'],'native_footprint_known_exactly':False,
            'availability':v['metadata'].get('retrieved_utc'),'role':'2011–2014 reconstruction only; 2026 retrieval is not historic preplant availability'}for site,v in sorted(manifest.items())]
    report={'status':'source_roles_and_physical_support_audited','sources':rows,'priority_product_guidance':PROFILES,'rotation_power_forcing':power,
        'new_large_downloads':False,'all_NASA_sources_physically_integrated':False,
        'physical_forcing_this_phase':['POWER cached Tmin/Tmax/rain/solar in rotation and strategy runs'],
        'LIRF_forcing':'Existing experimental daily weather, ET0 and actual irrigation, separately identified; not relabeled POWER.',
        'preplant_separation':'Observed future weather and archived actual management are reconstruction only; scenario replay is hypothetical. In-season operators require measurement/availability dates.',
        'field_level_validation_claim':False}
    write_json(out/'report.json',report);return report
