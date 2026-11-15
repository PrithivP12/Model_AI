"""Offline residue evaluation: python -m agrosim.residue_v23 --output NEW_DIR"""
import argparse
import json
from pathlib import Path
from .evaluate import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try: result = run(args.root, args.output)
    except (OSError, ValueError, KeyError, ArithmeticError) as exc: parser.exit(2, f'Residue evaluation unavailable: {exc}\n')
    print(json.dumps({'status': result['status'], 'candidate_evaluated': result['candidate']['evaluated'],
                      'candidate_promoted': False, 'final_outcomes_opened': False}))


if __name__ == '__main__': main()
