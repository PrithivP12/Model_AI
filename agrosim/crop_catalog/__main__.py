"""Nonvisual crop support inspection. No unsupported yield function is exposed."""
import argparse,json
from .registry import CropRegistry,ARCHETYPES

def main():
    p=argparse.ArgumentParser();p.add_argument('--crop');p.add_argument('--check',action='store_true');p.add_argument('--archetypes',action='store_true')
    p.add_argument('--collection',choices=['scientific','requested'],default='scientific',help='Preserved species inventory or user-requested crop choices')
    p.add_argument('--search',help='Search requested crop-choice names')
    a=p.parse_args();registry=CropRegistry()
    if a.collection=='requested':
        choices=registry.selection_catalog()
        if a.archetypes:p.error('--archetypes describes the scientific collection')
        if a.crop:result={'crop':choices.resolve(a.crop),'support':choices.support(a.crop)}
        elif a.search:result={'matches':[{'crop':c,'support':choices.support(c['identifier'])} for c in choices.search(a.search)]}
        else:result=choices.report()
        print(json.dumps(result,indent=2));return
    if a.search:p.error('--search requires --collection requested')
    if a.archetypes:result=ARCHETYPES
    elif a.crop:result={'crop':registry.resolve(a.crop),'support':registry.support(a.crop)}
    else:result={'validated_entries':len(registry.crops),'crops':[{'identifier':c,'common_name':v['common_name'],**registry.support(c)}for c,v in registry.crops.items()]}
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
