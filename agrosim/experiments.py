"""Portable execution of extended experiments through the common CLI."""
from pathlib import Path
from .io import resolve,write_json


def prepare_extended(config,base):
    from .data.management import prepare_management
    from .data.ecology import ingest_ecology,enrich_features
    from .data.lirf import old_cases,new_cases
    root=resolve(base,config['project_root']);out=resolve(base,config['output_dir']);out.mkdir(parents=True,exist_ok=True)
    management=prepare_management(root/'rotation-learning-v1',out/'data')
    ecology=ingest_ecology(root/'rotation-learning-v1',out/'data')
    features=enrich_features(root/'reports/agrosim/data/site_seasons.parquet',out/'data/plot_management.parquet',
        out/'data/ecology_observations.parquet',out/'data/enhanced_site_seasons.parquet')
    cases=[]
    for year in range(2008,2012):cases.extend(old_cases(root,year))
    test,quality=new_cases(root)
    write_json(out/'lirf_cases_development.json',cases);write_json(out/'lirf_cases_test_inputs.json',test)
    write_json(out/'weather_quality.json',quality)
    report={'status':'extended_real_observations_prepared','management':management,'ecology':ecology,'features':features,
            'development_irrigation_cases':len(cases),'later_year_irrigation_cases':len(test),'recommendation':None}
    write_json(out/'preparation_report.json',report);return report


def run_experiment(config,base):
    name=config['experiment']
    if name=='prepare_extended':return prepare_extended(config,base)
    root=resolve(base,config.get('project_root','..'));out=resolve(base,config['output_dir'])
    if name=='intelligence_process':
        from .intelligence.process_experiment import run_process
        return run_process(root,out)
    if name=='intelligence_refinement':
        from .intelligence.refinement import run_refinement
        return run_refinement(root,out)
    if name=='intelligence_feedback':
        from .intelligence.feedback import run_feedback
        return run_feedback(root,out,config.get('draws',96))
    if name=='intelligence_rotation':
        from .intelligence.rotation import run_rotation
        return run_rotation(root,out)
    if name=='intelligence_stability':
        from .intelligence.stability import run_stability
        return run_stability(root,out)
    if name=='direct_rotation':
        from .rotation_learning import train_rotation
        return train_rotation(resolve(base,config['features']),out)
    if name=='waite':
        from .waite_experiment import run_waite
        return run_waite(root,out)
    if name=='challenge_context':
        from .data.challenge import integrate_challenge
        return integrate_challenge(config,base)
    if name=='calibration_audit':
        from .corrected_experiment import run_corrected
        return run_corrected(root,resolve(base,config['prepared_root']),resolve(base,config['selection_path']),out)
    if name=='biology':
        from .biology import run_biology
        return run_biology(config)
    if name=='evidence_audit':
        from .evidence import audit_release
        return audit_release(root,out/'report.json')
    if name=='biological_ablation':
        from .biological_ablation import run_ablation
        return run_ablation(resolve(base,config['features']),out)
    if name=='ecosystem_compare':
        if config.get('output')and resolve(base,config['output']).exists():raise RuntimeError('Preserve completed ecosystem experiment; choose a new output path')
        from .ecosystem_experiment import compare_ecosystems
        return compare_ecosystems(config,base)
    if name=='coupled_validation':
        from .recovery.coupled_validation import run_coupled_validation
        return run_coupled_validation(root,out)
    if name=='residue_loop':
        from .ecosystem_experiment import residue_loop_experiment
        if(out/'report.json').exists():raise RuntimeError('Preserve completed residue-loop experiment')
        result=residue_loop_experiment();write_json(out/'report.json',result);return result
    if name=='recovery_uncertainty':
        from .recovery.uncertainty import run_uncertainty
        return run_uncertainty(root,out)
    if name=='recovery_yield':
        from .recovery.yield_growth import run_yield_recovery
        return run_yield_recovery(root,out)
    if name=='recovery_diagnostics':
        from .recovery.process import run_diagnostics
        return run_diagnostics(root,out)
    if name=='recovery_formulations':
        from .recovery.evaluate import run_recovery
        return run_recovery(root,out,config.get('audit_exposed',False))
    raise ValueError('Unknown extended experiment: '+str(name))
