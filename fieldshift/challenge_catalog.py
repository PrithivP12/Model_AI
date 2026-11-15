"""The complete 25-entry resource list supplied by the user, with explicit variants."""
FAMILIES=[
 ('smap','SMAP soil moisture and freeze/thaw',['smap_moisture','smap_freeze']),
 ('gpm_imerg','GPM / IMERG precipitation',['imerg']),
 ('landsat','Landsat reflectance and vegetation',['landsat']),
 ('modis_viirs','MODIS and VIIRS vegetation',['modis','viirs']),
 ('power','NASA POWER',['power']),
 ('srtm','SRTM terrain',['srtm','nasadem_derivative']),
 ('ecostress','ECOSTRESS temperature and evapotranspiration',['ecostress_lst','ecostress_et','ecostress_esi']),
 ('ag_data_commons','USDA Ag Data Commons',['ag_data_commons']),
 ('web_soil_survey','Web Soil Survey',['web_soil_survey']),
 ('cdl','USDA Cropland Data Layer / CropScape',['cdl']),
 ('prism','PRISM climate',['prism']),
 ('ssurgo_statsgo2','USDA SSURGO and STATSGO2 soils',['ssurgo','statsgo2']),
 ('naip','NAIP aerial imagery',['naip']),
 ('nlcd','USGS NLCD land cover',['nlcd']),
 ('nldas_gldas','NASA NLDAS and GLDAS',['nldas','gldas']),
 ('gaez','FAO GAEZ crop suitability',['gaez']),
 ('cocorahs','CoCoRaHS precipitation',['cocorahs']),
 ('nwis','USGS NWIS hydrology',['nwis']),
 ('ams','USDA AMS Market News',['ams']),
 ('nass','USDA NASS QuickStats',['nass']),
 ('openet','OpenET evapotranspiration',['openet']),
 ('landfire','USFS LANDFIRE vegetation and fuels',['landfire_vegetation','landfire_fuels']),
 ('gedi','GEDI vegetation structure and biomass',['gedi_biomass']),
 ('sentinel2','ESA Sentinel-2',['sentinel2']),
 ('era5','ECMWF ERA5',['era5']),
]
PRODUCTS={p:family for family,_,products in FAMILIES for p in products}

# Collection short names are resolved against CMR; an empty result is reported,
# never silently replaced with another mission's observations.
CMR={
 'smap_moisture':'SPL3SMP_E','smap_freeze':'SPL3FTP_E','imerg':'GPM_3IMERGDF',
 'srtm':'SRTMGL1','ecostress_lst':'ECO_L2T_LSTE','ecostress_et':'ECO_L3T_JET',
 'ecostress_esi':'ECO_L4T_ESI',
 'nldas':'NLDAS_NOAH0125_H','gldas':'GLDAS_NOAH025_3H','gedi_biomass':'GEDI_L4B_Gridded_Biomass_V2_1_2299',
}
