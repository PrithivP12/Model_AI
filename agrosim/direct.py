"""Embedded, synchronous integration; no HTTP server or environment URL required."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import argparse
import json
import sys

from pydantic import ValidationError
from .api.models import SimulationRequest, ComparisonRequest
from .api.service import AgroSimService, ServiceError, write_atomic
from .api.settings import Settings


class AgroSim:
    """Run the existing engines in the caller's process with explicit resources.

    No data directory, network listener, credentials, or environment settings are
    needed. Calls are synchronous: use your application's bounded worker for web
    requests. Research output is saved only when output_dir is explicitly given.
    """
    def __init__(self, *, resources_dir=None):
        self._service = AgroSimService(Settings(
            data_dir=Path('.'),
            resources_dir=Path(resources_dir).resolve() if resources_dir else None))

    def crops(self):
        rows = self._service.list_crops()
        return {'count': len(rows), 'crops': rows}

    def validate(self, request):
        return self._service.validate_simulation(
            SimulationRequest.model_validate(request)).model_dump(mode='json')

    def _run(self, request, *, comparison, output_dir):
        model = (ComparisonRequest if comparison else SimulationRequest).model_validate(request)
        if comparison:
            self._service.validate_comparison(model)
        elif not self._service.validate_simulation(model).valid:
            raise ServiceError('invalid_simulation', 'Simulation inputs failed validation; call validate first')
        output = None
        if output_dir is not None:
            output = Path(output_dir).resolve()
            output.mkdir(parents=True, exist_ok=False)
        method = self._service.compare_scenarios if comparison else self._service.run_simulation
        result = method(model, run_id=uuid4().hex,
                        created_at=datetime.now(timezone.utc).isoformat(), output=output)
        value = result.model_dump(mode='json')
        def localize(item, folder):
            # Direct results must not advertise nonexistent HTTP endpoints.
            item['timeseries_url'] = None
            item['artifacts'] = ({name: str(folder / filename) for name, filename in
                [('timeseries', 'timeseries.json'), ('research_archive', 'research.json.gz'),
                 ('resolved_input', 'resolved_input.json')]} if folder else {})
            if folder:
                write_atomic(folder / 'result.json', item)
        if comparison:
            for i, item in enumerate(value['scenarios']):
                localize(item, output / f'scenario_{i}' if output else None)
            if output:
                write_atomic(output / 'result.json', value)
        else:
            localize(value, output)
        return value

    def simulate(self, request, *, output_dir=None):
        return self._run(request, comparison=False, output_dir=output_dir)

    def compare(self, request, *, output_dir=None):
        return self._run(request, comparison=True, output_dir=output_dir)


def main():
    parser = argparse.ArgumentParser(description='Run AgroSim directly without an API URL or server')
    parser.add_argument('command', choices=['crops', 'validate', 'simulate', 'compare'])
    parser.add_argument('--input', default='-', help='JSON file or - for standard input')
    parser.add_argument('--output', help='New artifact directory; existing directories are refused')
    parser.add_argument('--resources', help='Optional local environmental resources directory')
    args = parser.parse_args()
    try:
        engine = AgroSim(resources_dir=args.resources)
        if args.command == 'crops':
            value = engine.crops()
        else:
            with (open(args.input, 'rb') if args.input != '-' else __import__('contextlib').nullcontext(sys.stdin.buffer)) as stream:
                body = stream.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError('Request exceeds 2000000 bytes')
            request = json.loads(body)
            value = (engine.validate(request) if args.command == 'validate' else
                     getattr(engine, args.command)(request, output_dir=args.output))
        print(json.dumps(value, allow_nan=False))
        return 0
    except (ValidationError, ValueError, FileExistsError) as exc:
        issues = ([{'field': '.'.join(map(str, e['loc'])), 'code': e['type'], 'message': e['msg']}
                   for e in exc.errors(include_input=False, include_context=False)] if isinstance(exc, ValidationError)
                  else [{'field': 'request', 'code': 'invalid_request', 'message': str(exc)}])
        print(json.dumps({'errors': issues}), file=sys.stderr)
        return 2
    except ServiceError as exc:
        print(json.dumps({'errors': [exc.issue.model_dump()]}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({'errors': [{'field': 'execution', 'code': 'execution_failed',
              'message': 'No supported result produced; check inputs and installed dependencies.'}]}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
