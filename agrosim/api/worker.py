"""Internal fixed-command child worker. Request bodies never become commands."""
import argparse,json
from .models import SimulationRequest,ComparisonRequest
from .service import AgroSimService,ServiceError,write_atomic
from .settings import Settings
from .jobs import VALID_ID

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-id',required=True);parser.add_argument('--kind',choices=['simulation','comparison'],required=True);parser.add_argument('--created-at',required=True)
    args=parser.parse_args()
    if not VALID_ID.fullmatch(args.run_id):return 2
    settings=Settings.from_env();directory=settings.data_dir/'runs'/args.run_id
    try:
        data=json.loads((directory/'input.json').read_text());service=AgroSimService(settings)
        if args.kind=='simulation':service.run_simulation(SimulationRequest.model_validate(data),run_id=args.run_id,created_at=args.created_at,output=directory)
        else:service.compare_scenarios(ComparisonRequest.model_validate(data),run_id=args.run_id,created_at=args.created_at,output=directory)
        return 0
    except ServiceError as exc:write_atomic(directory/'error.json',exc.issue.model_dump());return 1
    except Exception:
        # Retain a private numerical diagnostic; API never exposes this file.
        import traceback;traceback.print_exc()
        write_atomic(directory/'error.json',{'field':'simulation','code':'unsupported_or_failed_execution','message':'The supplied scenario did not produce a supported conserved execution. Review inputs; no result was substituted.'});return 1

if __name__=='__main__':raise SystemExit(main())
