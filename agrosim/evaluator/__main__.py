"""Usage: python -m agrosim.evaluator CONFIG.yaml --output NEW_DIRECTORY"""
import argparse
import json
from .engine import write_evaluation


def main():
    parser = argparse.ArgumentParser(description='Run the existing three-crop ecosystem evaluator; no training or field recommendation.')
    parser.add_argument('config', help='YAML configuration with soil, location, management, dated crop slots and at least three strategies')
    parser.add_argument('--output', required=True, help='A new directory; existing directories are never overwritten')
    args = parser.parse_args()
    try:
        report = write_evaluation(args.config, args.output)
    except (ValueError, ArithmeticError, FileNotFoundError, FileExistsError, KeyError) as exc:
        parser.exit(2, f'Evaluation unavailable: {exc}\n')
    print(json.dumps({'status': report['status'], 'runs': len(report['runs']),
                      'matched_rotation_comparisons': len(report['rotation_comparisons']),
                      'report': str(args.output) + '/report.json', 'farmer_recommendation': None}))


if __name__ == '__main__':
    main()
