"""Offline evaluation: python -m agrosim.nitrogen_ml --output NEW_DIRECTORY"""
import argparse
import json
from pathlib import Path
from .evaluate import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New output directory, never overwritten')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[2]), help='Existing AgroSim repository with preserved raw KBS observations')
    args = parser.parse_args()
    try:
        result = run(args.root, args.output)
    except (ValueError, OSError, ArithmeticError, KeyError) as exc:
        parser.exit(2, f'Offline nitrogen evaluation unavailable: {exc}\n')
    print(json.dumps({'status': result['status'], 'training': result['training_n'], 'development': result['development_n'],
                      'useful_improvement': result['useful_development_improvement_gate_passed'], 'retained_estimator': result['retained_estimator'],
                      'simulator_coupling_enabled': False}))


if __name__ == '__main__': main()
