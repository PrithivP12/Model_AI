"""Offline recovery replay: python -m agrosim.data_recovery_v22 --output NEW_DIR"""
import argparse
import csv
import json
from pathlib import Path
from ..io import sha256, write_json
from .joins import recover
from .inventory import observation_inventory, process_selection
from .experiment import evaluate_recovery


def run(root, output):
    root = Path(root); output = Path(output); output.mkdir(parents=True, exist_ok=False)
    phase = root / 'reports/ecosystem-v22'
    audit = recover(root, output / 'joins')
    write_json(output / 'observation_inventory.json', observation_inventory(audit))
    write_json(output / 'process_selection.json', process_selection())
    write_json(output / 'raw_provenance.json', json.loads((phase / 'raw/manifest.json').read_text()))
    write_json(output / 'metadata_provenance.json', json.loads((phase / 'metadata/manifest.json').read_text()))
    write_json(output / 'protocol.json', json.loads((phase / 'protocol_frozen.json').read_text()))
    result = evaluate_recovery(output / 'joins', output / 'diagnostic', phase / 'protocol_frozen.json')
    rows = json.loads((output / 'joins/joined_observations.json').read_text())
    fields = ['source_row', 'date', 'end_date', 'treatment', 'replicate', 'station', 'fold', 'netmineralization',
              'nitrate_ug_g', 'ammonium_ug_g', 'water_g_g', 'complete_new_predictors']
    with (output / 'verified_join_summary.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        writer.writerows({k: r[k] for k in fields} for r in rows)
    write_json(output / 'implementation_manifest.json', {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    write_json(output / 'artifact_manifest.json', {'sha256': {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}})
    return {'status': 'bounded_ecosystem_data_recovery_complete',
            'exact_complete_observations': sum(r['complete_new_predictors'] for r in rows),
            'new_fixed_development_observations': audit['coverage']['development']['both_complete'], 'candidate_diagnostic_evaluated': result['candidate_evaluated'],
            'model_promoted': False, 'simulator_coupling_enabled': False}


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', required=True)
    p.add_argument('--root', default=str(Path(__file__).resolve().parents[2])); args = p.parse_args()
    try: result = run(args.root, args.output)
    except (ValueError, OSError, KeyError, ArithmeticError) as exc: p.exit(2, f'Recovery unavailable: {exc}\n')
    print(json.dumps(result))


if __name__ == '__main__': main()
