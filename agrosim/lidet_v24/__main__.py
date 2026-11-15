"""Offline documentation audit: python -m agrosim.lidet_v24 --output NEW_DIR"""
import argparse,json
from pathlib import Path
from .audit import run

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    p.add_argument('--root',default=str(Path(__file__).resolve().parents[2]));a=p.parse_args()
    try:r=run(a.root,a.output)
    except (OSError,ValueError,KeyError)as e:p.exit(2,f'Viability audit unavailable: {e}\n')
    print(json.dumps({'decision':r['decision'],'viability_confirmed':False,'model_fit_performed':False,'status':r['status']}))
