"""Availability is separate from same-sample compatibility or prediction support."""
def observation_inventory(audit):
    rows = [
        ('initial_nitrate_ammonium', [55], 'directly_measured_processed_lab_concentration', 'microgram N/g dry soil', '0–25cm composite',
         '100 exact T0 matches in2001–2002; zero in2010–2014',
         'Collection date in table55 differs from initial-analysis date in table56. No shared sample-batch identifier or permitted nearest-date repair.'),
        ('soil_water', [30], 'derived_from_direct_fresh_and_dry_mass', 'g water/g dry soil', '0–25cm composite',
         '100 exact T0 matches in2001–2002; zero in2010–2014',
         'Initial field/composite moisture is compatible context, not an observed full incubation water trajectory; not converted to volumetric water using assumed density.'),
        ('soil_temperature', [167], 'directly_measured_context', 'C', '10cm point at chamber sampling',
         'Exact plot/day candidates counted, excluded from bag-equivalent predictor set',
         'Different point/depth and sampling purpose; cannot replace the isolated-bag temperature history.'),
        ('soil_total_C_N', [164], 'directly_measured_total_elemental_concentration', 'percent C/N', 'Core-specific sections/horizons',
         'Recovered by date/plot/core/station/depth; no automatic depth or temporal filling',
         'Total C is not automatically organic C; total soil C/N is not decomposable substrate or returned residue. Exact bag-equivalent stock not established.'),
        ('organic_C_N_substrate', [164, 73], 'unavailable_for_bag_initialization', None, None,
         'No independent decomposable substrate stock/composition identified',
         'Cannot infer labile or microbially accessible pools from measured total C or from the net-N target.'),
        ('residue_mass_composition', [73, 271], 'incompatible_or_partial', 'Plant percent C/N; separate stover dry mass', 'Standing tissues versus actual retained material',
         'MCSE tissue chemistry exists; documented stover return table is GLBRC BCSE G plots',
         'Standing plant composition does not measure returned mass. MCSE T plots and BCSE G plots are different experiments; no cross-experiment joins.'),
        ('independent_microbe_biomass', [26], 'derived_from_independent_microscopy', 'microgram C/g dry soil', '1990–1996 station/composite microscopy',
         'Independent of same-assay CO2, but no contemporaneous support for2001–2014 bags',
         'Biovolume-to-C conversion is derived; microbial N and turnover are not identified.1995–1996 numeric outcomes remain sealed.'),
        ('microbe_CFI_biomass', [25], 'derived_same_assay_endpoint', 'microgram C/N per g dry soil', '1989–1996 fumigation incubation',
         'Previously recovered; not a new independent initialization',
         'CFI C/N depends on the same respiration/N assay. Do not use it to independently validate those endpoints.'),
        ('management_events', [159, 150], 'recorded_management', 'kg N/ha for fertilization', 'Dated treatment/replicate/plot log',
         'Recovered pre-T0 same-year exact-plot candidate event identities; not used in candidate',
         'Missing log events do not establish zero input. Field applications cannot be injected into an isolated bag after collection.'),
        ('soil_texture', [699, 14], 'directly_measured_historical_or_partial', 'percent sand/silt/clay', 'T1/T2 at0–10/10–25cm; baseline1988 at0–15cm',
         'Metadata inspected; no raw download or automatic assignment',
         'Surface texture table lacks dates and covers two treatments; historical baseline differs in sampling design/depth.'),
        ('bulk_density', [308, 71, 14], 'derived_from_measured_core_mass_volume', 'Soil mass/core volume', 'Year/core/section or historical shallow excavation',
         'Deep-core data recovered but table308 lacks exact sampling dates',
         'Different cores, horizons and years do not establish the bag soil mass or a0–25cm stock conversion. Known core diameter changes can affect compression.'),
        ('independent_respiration', [28], 'directly_measured_total_soil_flux_not_partitioned', 'kg CO2-C/ha/day', 'Field chambers,1991–2014',
         'Metadata inspected; raw data not downloaded or modeled',
         'Inspected metadata supplies no independent root/microbial partition. Total soil CO2 is not automatically microbial respiration or an isolated bag flux.'),
    ]
    return {'schema_version': 1, 'observations': [
        {'variable': name, 'table_ids': ids, 'source_urls': [f'https://lter.kbs.msu.edu/datatables/{i}' for i in ids],
         'classification': kind, 'unit': unit, 'support': support, 'availability': available,
         'compatibility_limit': limit, 'unknown_measurements_filled': False}
        for name, ids, kind, unit, support, available, limit in rows],
        'verified_exact_join_coverage': audit['coverage'], 'context_coverage': audit['context_compatibility'],
        'classification_is_global_absence_claim': False}


def process_selection():
    return {'selected_for_one_limited_diagnostic': 'net nitrogen mineralization',
        'net_nitrogen_mineralization': '100 complete initial N/water observations support one exploratory2001→2002 check only; no complete new fixed-development cohort.',
        'residue_decomposition': 'Not selected: no matched actual MCSE residue-return and decomposition time series recovered.',
        'microbial_respiration': 'Not selected: CFI/correlated endpoints and total chamber respiration do not establish independent microbial rate initialization/partition.',
        'soil_microbial_C_change': 'Not selected: microscopy is historical and lacks contemporary bag support, N and turnover; no second process model.',
        'whole_ecosystem_biological_prediction_supported': False,
        'next_critical_recovery': 'Original sample/batch log linking field collection date, laboratory initial analysis, burial/retrieval dates, plot/station/depth and aliquot identities; retain raw initial NH4/NO3 and water measurements.'}
