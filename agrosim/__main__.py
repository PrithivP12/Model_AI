from pathlib import Path
import argparse
import json
import sys
from requests import RequestException
from yaml import YAMLError
from .io import load_config, resolve, write_json


def main(argv=None):
    parser=argparse.ArgumentParser(description='AgroSim Global scientific backend (research estimates)')
    actions=parser.add_subparsers(dest='action',required=True)
    data=actions.add_parser('data');ds=data.add_subparsers(dest='data_action',required=True)
    inv=ds.add_parser('inventory');inv.add_argument('--roots',nargs='+',required=True);inv.add_argument('--output',required=True)
    inspect=ds.add_parser('inspect');inspect.add_argument('--source',required=True);inspect.add_argument('--path',required=True)
    for command in ['ingest','build-features','coverage']:
        p=ds.add_parser(command);p.add_argument('--config',required=True);p.add_argument('--online',action='store_true')
    for command in ['simulate','compare','optimize','validate','benchmark','sensitivity','experiment','ecosystem-strategies']:
        p=actions.add_parser(command);p.add_argument('--config',required=True);p.add_argument('--output')
    ecology=actions.add_parser('ecosystem-inspect',help='Nonvisual V2 physical/experimental ecological inspection')
    ecology.add_argument('--config',required=True)
    ecology.add_argument('--output-dir',required=True)
    ecology.add_argument('--date',help='Optional ISO inspection date inside the scenario')
    complete=actions.add_parser('ecosystem-complete',help='Conserved nonvisual living ecosystem scenario and sensitivity report')
    complete.add_argument('--config',required=True)
    complete.add_argument('--output-dir',required=True)
    finance=actions.add_parser('finance-evaluate',help='Optional evidence-aware financial accounting; no biological changes')
    finance.add_argument('--config',required=True)
    finance.add_argument('--output-dir',required=True)
    crops=actions.add_parser('crop-catalog-validate',help='Execute bounded experimental canonical88-crop process/coverage tests')
    crops.add_argument('--config',required=True)
    crops.add_argument('--output-dir',required=True)
    platform=actions.add_parser('verify-platform',help='Bounded nonvisual scientific, crop, ecosystem and finance verification')
    platform.add_argument('--config',required=True)
    platform.add_argument('--output-dir',required=True)
    physiology=actions.add_parser('verify-crop-physiology',help='Opt-in final20 functional crop coverage; no V1.1 accuracy research')
    physiology.add_argument('--config',required=True)
    physiology.add_argument('--output-dir',required=True)
    args=parser.parse_args(argv)
    try:
        if args.action=='verify-crop-physiology':
            from .physiology.validation import verify
            result=verify(args.config,args.output_dir)
        elif args.action=='verify-platform':
            from .platform.verify import verify
            result=verify(args.config,args.output_dir)
        elif args.action=='crop-catalog-validate':
            from .crops.validation import validate
            result=validate(args.config,args.output_dir)
        elif args.action=='finance-evaluate':
            from .finance.runner import execute
            result=execute(args.config,args.output_dir)
        elif args.action=='ecosystem-complete':
            from .living.runner import execute
            result=execute(args.config,args.output_dir)
        elif args.action=='ecosystem-inspect':
            from .ecology_v2.inspector import inspect_ecosystem
            result=inspect_ecosystem(args.config,args.output_dir,args.date)
        elif args.action=='data':
            if args.data_action=='inventory':
                from .data.inventory import inventory
                result=inventory(args.roots,args.output)
            elif args.data_action=='inspect':
                from .data.inventory import inspect_file
                result={'requested_source':args.source,'identification_verified':False,'inspection':inspect_file(args.path)}
                if args.source=='power':
                    d=json.loads(Path(args.path).read_text());v=d.get('data',d.get('response',d))
                    from .data.power import read_power
                    coords=v['geometry']['coordinates'];_,_,meta=read_power(args.path,'inspection',coords[1],coords[0])
                    result['identification_verified']=True;result['product_metadata']=meta
            else:
                config,base=load_config(args.config)
                if args.data_action=='coverage':
                    from .data.registry import coverage
                    result=coverage(config,base)
                elif args.data_action=='build-features':
                    from .data.experiments import build_features
                    result=build_features(resolve(base,config['experiment_root']),resolve(base,config['rotation_dataset']),
                                          resolve(base,config['feature_output']))
                else:
                    from .data.power import read_power, retrieve_power
                    from .data.experiments import harmonize
                    out=resolve(base,config['ingest_output']);out.mkdir(parents=True,exist_ok=True)
                    request=config['power_subset']
                    path=resolve(base,request['path'])if request.get('path')else retrieve_power(request,resolve(base,config['cache']),not args.online)
                    frame,obs,meta=read_power(path,request['site_id'],request['latitude'],request['longitude'])
                    frame.to_csv(out/'power_daily.csv',index=False);obs.to_parquet(out/'environment_observations.parquet',index=False)
                    write_json(out/'power_provenance.json',meta)
                    result={'power':{'days':len(frame),'normalized_observations':len(obs)},
                            'experiments':harmonize(resolve(base,config['experiment_root']),out)}
                    if config.get('waite_root'):
                        from .data.waite import harmonize_waite
                        result['waite']=harmonize_waite(resolve(base,config['waite_root']),out)
                    import pandas as pd
                    inventory=pd.read_csv(out/'data_inventory.csv')
                    extra=[{'source_id':'power','doi':None,'license':'NASA source attribution in response metadata',
                        'raw_path':str(path),'sha256':meta['sha256'],'records_used':len(frame),'role':'real daily climate scenarios'}]
                    if 'waite'in result:
                        from .io import sha256
                        raw=resolve(base,config['waite_root'])/'Waite_Trial_Data.xls'
                        extra.append({'source_id':'waite','doi':result['waite']['source_doi'],'license':'CC-BY-4.0',
                            'raw_path':str(raw),'sha256':sha256(raw),'records_used':result['waite']['records'],
                            'role':'raw observed grain yield/history; not yet scored'})
                        with (out/'data_sources.md').open('a')as stream:
                            stream.write('\n\n'+result['waite']['citation']+'\n\nWaite grain moisture basis is unspecified; kept separate from dry-grain training labels.\n')
                    pd.concat([inventory,pd.DataFrame(extra)],ignore_index=True).to_csv(out/'data_inventory.csv',index=False)
                    write_json(out/'ingestion_report.json',result)
        else:
            config,base=load_config(args.config)
            if args.action=='ecosystem-strategies':
                from .transfer_recovery.strategy import compare_evidence
                result=compare_evidence(config,base)
            elif args.action=='experiment':
                from .experiments import run_experiment
                result=run_experiment(config,base)
            elif args.action in ['simulate','compare','optimize']:
                from .simulation import evaluate,compare,optimize
                result=evaluate(config,base,[config['sequence']],daily=config.get('keep_daily',False))if args.action=='simulate'else (
                    compare(config,base)if args.action=='compare'else optimize(config,base))
            elif args.action=='benchmark':
                if config.get('experiment')=='hybrid_lirf':
                    from .hybrid import run_hybrid
                    result=run_hybrid(resolve(base,config['project_root']),resolve(base,config['output_dir']))
                else:
                    from .benchmark import benchmark
                    result=benchmark(config,base)
            elif args.action=='validate':
                from .validation import validate
                result=validate(config,base)
            else:
                from .sensitivity import sensitivity
                result=sensitivity(config,base)
            target=args.output or config.get('output')
            if target:
                destination=Path(args.output).resolve()if args.output else resolve(base,target)
                write_json(destination,result)
                result={'action':args.action,'output':str(destination),'status':result.get('status'),
                        'recommendation':result.get('recommendation')}
        print(json.dumps(result,indent=2,default=str,allow_nan=False))
        return 0
    except (ValueError,KeyError,OSError,RuntimeError,ArithmeticError,ImportError,RequestException,YAMLError)as exc:
        print(json.dumps({'error':str(exc),'type':type(exc).__name__}),file=sys.stderr)
        return 2


if __name__=='__main__':sys.exit(main())
