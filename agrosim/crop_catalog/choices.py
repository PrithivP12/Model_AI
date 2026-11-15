"""Requested agricultural names, distinct from the preserved species inventory.

A choice may be a species, crop-use group or unresolved common name. It is
selectable for recording a plan without becoming an executable crop profile.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import re
import unicodedata

from .registry import CropRegistry, UnsupportedCropError


def normalize_name(value):
    if not isinstance(value,str) or not value.strip():
        raise UnsupportedCropError('A nonempty crop choice identifier or name is required')
    return ' '.join(re.sub(r'[^\w]+',' ',unicodedata.normalize('NFKC',value).casefold().replace('_',' ')).split())


class CropChoiceRegistry:
    """Offline lookup/selection API; never generates crop coefficients or yields."""
    def __init__(self, *, base=None, document=None):
        self.base=base if base is not None else CropRegistry()
        path=Path(__file__).with_name('data')/'requested_choices.json'
        self.catalog=deepcopy(document) if document is not None else json.loads(path.read_text())
        self.choices={row['identifier']:row for row in self.catalog['choices']}
        self._names={}
        self.validate()

    def validate(self):
        rows=self.catalog['choices']
        if len(self.choices)!=len(rows) or self.catalog['selection_count']!=len(rows):
            raise ValueError('Choice identifiers and actual declared count must agree; no fixed92-choice requirement')
        if not rows: raise ValueError('Crop choices must not be empty')
        if len({r['common_name'].casefold() for r in rows})!=len(rows): raise ValueError('Duplicate displayed crop name')
        self._names={}
        allowed={'exact','scoped_existing_profile','separate_crop_group','broader_common_name','unimplemented','unresolved'}
        for c in rows:
            ident=c['identifier']
            if not re.fullmatch('[a-z][a-z0-9_]*',ident): raise ValueError('Stable lowercase choice identifier required')
            if c['selection_group'] not in self.catalog['groups']: raise ValueError('Unknown screenshot group')
            if c['profile_mapping'] not in allowed: raise ValueError('Unknown crop-use mapping')
            ref=c['reference_crop_id'];profile=c['profile_crop_id']
            if ref is not None and ref not in self.base.crops: raise ValueError('Unknown scientific catalog reference')
            if profile is not None and (profile!=ref or c['profile_mapping']not in ['exact','scoped_existing_profile']):
                raise ValueError('Crop group/broad common name must not inherit a reference quantitative profile')
            if c['profile_mapping']in ['exact','scoped_existing_profile'] and profile is None:
                raise ValueError('Exact/scoped existing entry needs its explicit scientific profile reference')
            if c['profile_mapping']=='scoped_existing_profile' and not c['scope_note']:
                raise ValueError('Explicit restricted executable profile scope required')
            if c['profile_mapping']=='unresolved' and (c['taxonomy'] is not None or not c['taxonomy_ambiguity']):
                raise ValueError('Unresolved common name must retain explicit taxonomy uncertainty')
            if not c['registration_source'] or c['predicted_yield'] is not None or c['crop_coefficients_created']:
                raise ValueError('Name registration cannot create predictions or undocumented coefficients')
            for label in [ident,c['common_name'],*c['aliases']]:
                key=normalize_name(label)
                if key in self._names and self._names[key]!=ident: raise ValueError('Ambiguous crop-choice alias: '+label)
                self._names[key]=ident
        return True

    def resolve(self, identifier):
        key=normalize_name(identifier)
        if key not in self._names: raise UnsupportedCropError('Unknown crop choice: '+str(identifier))
        return deepcopy(self.choices[self._names[key]])

    def search(self, query='', *, group=None):
        if group is not None and group not in self.catalog['groups']: raise ValueError('Unknown crop-choice group')
        query=normalize_name(query) if query else ''
        result=[]
        for c in self.choices.values():
            if group is not None and c['selection_group']!=group: continue
            names=[c['identifier'],c['common_name'],*c['aliases']]
            if c['taxonomy']: names.append(c['taxonomy']['canonical_name'])
            if query and not any(query in normalize_name(n) for n in names): continue
            result.append(deepcopy(c))
        return result

    def support(self, identifier):
        c=self.resolve(identifier);profile=c['profile_crop_id'];reference=c['reference_crop_id']
        core=self.base.support(profile) if profile else None
        implemented=bool(core and core['quantitative_eligible'])
        missing=list(core['missing_processes']) if core else (
            self.base.support(reference)['missing_processes'] if reference else ['crop_specific_process_implementation'])
        if not implemented:
            missing=sorted(set(missing)|{'crop_specific_parameter_profile'})
            if c['profile_mapping']=='separate_crop_group': missing=sorted(set(missing)|{'crop_group_harvest_and_development_profile'})
            if c['taxonomy_ambiguity'] or c['profile_mapping']=='broader_common_name': missing=sorted(set(missing)|{'explicit_species_or_crop_use_identity'})
        return {'identifier':c['identifier'],'common_name':c['common_name'],'registered':True,'selectable':True,
            'planning_record_eligible':True,'support_level':core['support_level'] if core else 'cataloged',
            'quantitative_eligible':implemented,'kernel_key':self.base.require_quantitative(profile) if implemented else None,
            'executable_profile_reference':profile if implemented else None,
            'scope_note':c['scope_note'],'missing_processes':missing,
            'taxonomy_ambiguous':bool(c['taxonomy_ambiguity']) or c['profile_mapping']=='broader_common_name',
            'independently_validated':False,'farmer_recommendation_eligible':False,
            'predicted_yield':None,'registration_establishes_species_identity':False,
            'registration_is_prediction_support':False}

    def require_quantitative(self, identifier):
        c=self.resolve(identifier);status=self.support(c['identifier'])
        if not status['quantitative_eligible']:
            raise UnsupportedCropError(f'{c["common_name"]} is registered for selection; quantitative simulation unavailable: '+', '.join(status['missing_processes']))
        return status['kernel_key']

    def selection(self, identifiers):
        if not isinstance(identifiers,(list,tuple)) or not identifiers: raise ValueError('An explicit nonempty list of crop choices is required')
        chosen=[self.resolve(c) for c in identifiers]
        if len({c['identifier'] for c in chosen})!=len(chosen): raise ValueError('Duplicate choice or alias in selection')
        statuses=[self.support(c['identifier']) for c in chosen]
        return {'collection':self.catalog['collection'],'choices':[{'identifier':c['identifier'],'common_name':c['common_name'],
                'selection_group':c['selection_group'],'support':s} for c,s in zip(chosen,statuses)],
                'quantitative_profiles_available':all(s['quantitative_eligible'] for s in statuses),
                'scenario_execution_checked':False,
                'scenario_validation_required':['location','soil','dated_crop_sequence','weather','seed_water_and_nutrient_management'],
                'predictions':None,'farmer_recommendation':None}

    def report(self):
        rows=[{**c,'support':self.support(c['identifier'])} for c in self.choices.values()]
        return {'collection':self.catalog['collection'],'registered_choices':len(rows),
                'group_counts':dict(Counter(c['selection_group'] for c in rows)),
                'groups':deepcopy(self.catalog['groups']),
                'quantitative_choices':[c['identifier'] for c in rows if c['support']['quantitative_eligible']],
                'registered_without_quantitative_model':sum(not c['support']['quantitative_eligible'] for c in rows),
                'scientific_catalog_entries_preserved':len(self.base.crops),
                'scope':self.catalog['scope'],'grouping_semantics':self.catalog['grouping_semantics'],
                'new_crop_models':0,'choices':rows}
