"""Conservative stock-transfer contracts for future observed biological models.

No rate, organism density, host relationship or flux is inferred here. The active
simulator does not execute these proposals. A transaction can check a supplied
material amount; that is distinct from learning or validating the supplied rate.
"""
from dataclasses import dataclass,asdict,replace
from copy import deepcopy
from math import isfinite
from .io import number


@dataclass(frozen=True)
class Material:
    carbon_kg_ha:float=0.
    nitrogen_kg_ha:float=0.
    water_mm:float=0.

    def __post_init__(self):
        for key,value in asdict(self).items():number(value,key,0)


@dataclass(frozen=True)
class Provenance:
    state_origin:str
    evidence_class:str
    source_reference:str|None
    source_record_ids:tuple[str,...]=()
    quantity_basis:str='kg C/ha; kg N/ha; mm water'

    def validate(self):
        if self.state_origin not in ['observed','estimated','simulated','scenario_assumed','unknown']:raise ValueError('Invalid material state origin')
        if self.evidence_class not in ['empirically_validated','calibrated_not_independently_validated','literature_parameterized','exploratory_scenario','unsupported_disabled']:raise ValueError('Invalid material evidence class')
        if self.state_origin!='unknown'and not self.source_reference:raise ValueError('Material provenance needs a source reference')
        if self.state_origin=='observed'and not self.source_record_ids:raise ValueError('Observed material needs matched source records')
        if self.quantity_basis!='kg C/ha; kg N/ha; mm water':raise ValueError('Canonical per-area units required; counts and per-individual rates cannot be moved as stocks')


@dataclass(frozen=True)
class ResourcePool:
    identifier:str
    resource:str
    material:Material|None
    provenance:Provenance
    organism_id:str|None=None
    life_stage:str|None=None

    def validate(self):
        self.provenance.validate()
        if not self.identifier or not self.resource:raise ValueError('Pool identity/resource required')
        if self.provenance.state_origin=='unknown'and self.material is not None:raise ValueError('Unknown biomass cannot become a numeric stock')
        if self.material is None and self.provenance.state_origin!='unknown':raise ValueError('Known pool requires an explicit material stock')
        if bool(self.organism_id)!=bool(self.life_stage):raise ValueError('Organism pool needs both identity and life stage')


class StockExchange:
    def __init__(self,pools,registry):
        self.registry=registry;self.pools={};self.audit=[]
        for pool in pools:
            pool.validate()
            if pool.identifier in self.pools:raise ValueError('Duplicate resource pool')
            if pool.organism_id:
                if pool.organism_id not in registry.organisms or pool.life_stage not in registry.organisms[pool.organism_id].stages:raise ValueError('Unregistered organism/life stage')
            self.pools[pool.identifier]=pool

    def totals(self):
        if any(p.material is None for p in self.pools.values()):return None
        return {key:sum(getattr(p.material,key)for p in self.pools.values())for key in asdict(Material())}

    def transfer(self,source,destination,material,evidence,interaction,host_compatible=None):
        """Validate one explicitly supplied integrated flux; never derive a rate."""
        evidence.validate()
        if source==destination:raise ValueError('Source and destination must differ')
        a=self.pools[source];b=self.pools[destination]
        if a.material is None or b.material is None:raise ValueError('Unknown resource stock: quantitative transfer disabled')
        if evidence.state_origin=='unknown'or evidence.evidence_class=='unsupported_disabled':raise ValueError('Unsupported flux remains disabled')
        if interaction not in ['feeding','predation','life_cycle_transition','mortality_to_residue']:raise ValueError('Unknown interaction type')
        actor=b if interaction=='feeding'else a
        if not actor.organism_id:raise ValueError('Interaction requires a registered organism stock')
        traits=self.registry.organisms[actor.organism_id]
        if not any(i['kind']==interaction and i['stage']==actor.life_stage for i in traits.interactions):raise ValueError('No interaction contract for this life stage')
        if interaction=='feeding':
            if host_compatible is not True:raise ValueError('Positive stage-specific host compatibility required; unknown is not compatible')
            if traits.feeding_resource_by_stage.get(actor.life_stage)!=a.resource:raise ValueError('Source is not the specified feeding resource')
        if interaction=='mortality_to_residue'and b.resource not in ['surface_residue','soil_residue']:raise ValueError('Dead biomass must enter a tracked residue pool')
        if interaction=='life_cycle_transition'and a.organism_id!=b.organism_id:raise ValueError('Life-cycle transition cannot change organism identity')
        if interaction=='predation'and not b.organism_id:raise ValueError('Predator must have a registered stock and life stage')
        if interaction=='life_cycle_transition':
            targets=[i['target']for i in traits.interactions if i['kind']==interaction and i['stage']==actor.life_stage]
            if b.life_stage not in targets:raise ValueError('Invalid destination life stage')
        before=self.totals();left={};right={}
        for key,amount in asdict(material).items():
            stock=getattr(a.material,key)
            if amount>stock:raise ValueError('Resource overdraw; no transfer was applied')
            left[key]=stock-amount;right[key]=getattr(b.material,key)+amount
        # All validation precedes mutation, preventing partially applied transactions.
        self.pools[source]=replace(a,material=Material(**left));self.pools[destination]=replace(b,material=Material(**right))
        after=self.totals()
        if before is not None and max(abs(after[k]-before[k])for k in before)>1e-9:raise ArithmeticError('Biological material conservation failed')
        self.audit.append({'source':source,'destination':destination,'interaction':interaction,'material':asdict(material),
            'evidence':asdict(evidence),'supplied_amount_not_predicted_rate':True,'active_simulator_applied':False})
        return self.audit[-1]

    def soil_residue_exchange(self,soil,pool_id,material,evidence):
        """Supported future bridge: body C/N -> physical surface residue, once.

        Body water routing and feeding from an unresolved root/shoot compartment
        remain unsupported. Returned soil + organism inventories conserve C/N.
        The caller must include organism stocks in any whole-ecosystem ledger.
        """
        if material.water_mm:raise ValueError('Body-water routing needs a supported hydrological destination')
        identifier='__physical_surface_residue__'
        if identifier in self.pools:raise ValueError('Physical residue bridge already staged')
        bridge=ResourcePool(identifier,'surface_residue',Material(soil.surface_residue_c_kg_ha,soil.surface_residue_n_kg_ha,0),
            Provenance('simulated','literature_parameterized','Existing conservative physical soil state'))
        before=deepcopy(self.pools);audit_before=len(self.audit)
        try:
            self.pools[identifier]=bridge
            self.transfer(pool_id,identifier,material,evidence,'mortality_to_residue')
            target=self.pools.pop(identifier).material;new=deepcopy(soil)
            new.surface_residue_c_kg_ha=target.carbon_kg_ha;new.surface_residue_n_kg_ha=target.nitrogen_kg_ha
            return new
        except Exception:
            self.pools=before;self.audit=self.audit[:audit_before];raise
