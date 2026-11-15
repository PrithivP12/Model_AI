"""Reusable atomic C/N routing; pools are inventories, not extra biomass.

Water belongs to the existing hydrology ledger. Plant dry matter is converted
using its declared C fraction; no grain/leaf/root stock is counted twice.
"""
from dataclasses import dataclass,asdict
from copy import deepcopy
import math
from ..io import number


@dataclass(frozen=True)
class CN:
    carbon:float
    nitrogen:float

    def __post_init__(self):
        number(self.carbon,'carbon kg/ha',0);number(self.nitrogen,'nitrogen kg/ha',0)

    def scale(self,fraction):
        number(fraction,'transfer fraction',0,1)
        return CN(self.carbon*fraction,self.nitrogen*fraction)


class MaterialLedger:
    """Validate the complete transaction before changing any account."""
    def __init__(self,accounts):
        if not accounts or any(not k or not isinstance(v,CN)for k,v in accounts.items()):raise ValueError('Named C/N accounts required')
        self.accounts=deepcopy(accounts);self.transactions=[];self.ids=set()

    def total(self):
        return CN(sum(v.carbon for v in self.accounts.values()),sum(v.nitrogen for v in self.accounts.values()))

    def transfer(self,identifier,source,destination,material,*,basis):
        if not identifier or identifier in self.ids:raise ValueError('Transfer identity already applied or missing')
        if source==destination or source not in self.accounts or destination not in self.accounts:raise ValueError('Distinct declared accounts required')
        if not basis:raise ValueError('Material provenance/basis required')
        a,b=self.accounts[source],self.accounts[destination]
        if material.carbon>a.carbon+1e-9 or material.nitrogen>a.nitrogen+1e-9:raise ValueError('Material overdraw; no partial transaction applied')
        before=self.total()
        self.accounts[source]=CN(max(0,a.carbon-material.carbon),max(0,a.nitrogen-material.nitrogen))
        self.accounts[destination]=CN(b.carbon+material.carbon,b.nitrogen+material.nitrogen)
        after=self.total()
        if max(abs(after.carbon-before.carbon),abs(after.nitrogen-before.nitrogen))>1e-7:raise ArithmeticError('Material transaction failed conservation')
        self.ids.add(identifier);self.transactions.append({'id':identifier,'source':source,'destination':destination,
            'material_kg_ha':asdict(material),'basis':basis})


def harvest_transfer(soil,*,grain,shoot,roots,root_weights,shoot_removed,grain_harvest=True,basis):
    number(shoot_removed,'shoot residue removal',0,1)
    weights=list(root_weights)
    if len(weights)!=len(soil.residue_c_kg_ha)or any(not math.isfinite(w)or w<0 for w in weights)or sum(weights)<=0:raise ValueError('Explicit nonnegative rooted layer weights required')
    accounts={'grain':grain,'shoot':shoot,'roots':roots,'export':CN(0,0),
        'surface':CN(soil.surface_residue_c_kg_ha,soil.surface_residue_n_kg_ha),
        **{f'layer{i}':CN(c,n)for i,(c,n)in enumerate(zip(soil.residue_c_kg_ha,soil.residue_n_kg_ha))}}
    ledger=MaterialLedger(accounts)
    ledger.transfer('grain','grain','export'if grain_harvest else'surface',grain,basis=basis)
    ledger.transfer('shoot-export','shoot','export',shoot.scale(shoot_removed),basis=basis)
    ledger.transfer('shoot-return','shoot','surface',ledger.accounts['shoot'],basis=basis)
    total=sum(weights)
    for i,w in enumerate(weights):
        amount=roots.scale(w/total)if i<len(weights)-1 else ledger.accounts['roots']
        ledger.transfer('root-return-'+str(i),'roots',f'layer{i}',amount,basis=basis)
    if any(ledger.accounts[k].carbon>1e-7 or ledger.accounts[k].nitrogen>1e-7 for k in ['grain','shoot','roots']):raise ArithmeticError('Unrouted harvest material')
    result=deepcopy(soil);result.surface_residue_c_kg_ha=ledger.accounts['surface'].carbon;result.surface_residue_n_kg_ha=ledger.accounts['surface'].nitrogen
    result.residue_c_kg_ha=[ledger.accounts[f'layer{i}'].carbon for i in range(len(weights))]
    result.residue_n_kg_ha=[ledger.accounts[f'layer{i}'].nitrogen for i in range(len(weights))]
    return result,ledger.accounts['export'],ledger.transactions


def litter_transfer(plant,soil,compartment,fraction,*,basis):
    """Actual senesced/root material changes both aggregate and organ stock once."""
    number(fraction,'litter fraction',0,1)
    if compartment not in ['senesced_leaf','root']:raise ValueError('Only explicit senesced leaf or root stock may enter litter')
    if compartment=='root':raise ValueError('Root turnover needs an explicitly supported layer destination; no surface-root shortcut')
    if not plant.organ_state or not basis:raise ValueError('Explicit organ stock and source basis required')
    p=deepcopy(plant);s=deepcopy(soil);pool=p.organ_state['pools'][compartment]
    dry=pool['dry']*fraction;n=pool['N']*fraction
    pool['dry']-=dry;pool['N']-=n;p.biomass_dry_kg_ha-=dry;p.n_kg_ha-=n
    s.surface_residue_c_kg_ha+=dry*.45;s.surface_residue_n_kg_ha+=n
    return p,s,{'dry_kg_ha':dry,'carbon_kg_ha':dry*.45,'nitrogen_kg_ha':n,'basis':basis}
