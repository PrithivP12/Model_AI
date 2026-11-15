"""Re-fetch the exact, checksum-pinned source files used in these experiments."""
import argparse
import hashlib
import json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
COLLECTIONS={'disease':(ROOT/'data/manifest.json',ROOT/'data/raw'),
             'amendments':(ROOT/'amendment-study/data/manifest.json',ROOT/'amendment-study/data/raw'),
             'yield':(ROOT/'g2f/manifest.json',ROOT/'g2f/raw'),
             'global':(ROOT/'global/manifest.json',ROOT/'global/raw')}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--collection',choices=['all']+list(COLLECTIONS),default='all')
    args=parser.parse_args()
    selected=COLLECTIONS if args.collection=='all' else {args.collection:COLLECTIONS[args.collection]}
    for label,(manifest,target) in selected.items():
        target.mkdir(parents=True,exist_ok=True)
        for entry in json.loads(manifest.read_text()):
            name=entry['file']
            if Path(name).name!=name:raise ValueError('Manifest filename must be a basename')
            dest=target/name
            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()==entry['sha256']:
                print(label,name,'verified cached',flush=True);continue
            r=requests.get(entry['url'],timeout=(10,90));r.raise_for_status()
            if hashlib.sha256(r.content).hexdigest()!=entry['sha256']:raise ValueError(f'Checksum mismatch: {label}/{name}')
            dest.write_bytes(r.content);print(label,name,'downloaded and verified',flush=True)


if __name__=='__main__':main()
