"""Small operator interface for the MXene VQE research automation pipeline.

Python owns deterministic orchestration. AI diagnostics remain optional and
event-driven. The default run is offline; live QPU work requires two explicit
operator confirmations plus the existing configuration, quota and provenance
gates.
"""
from __future__ import annotations

# Preserve flat import names and direct-script startup after relocation.
import sys as _migration_sys
from pathlib import Path as _MigrationPath
_migration_root = str(_MigrationPath(__file__).resolve().parents[1])
if _migration_root not in _migration_sys.path:
    _migration_sys.path.insert(0, _migration_root)


from low_memory_runtime import limit_native_threads
limit_native_threads()

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

DEFAULT_CAMPAIGN = "vqe-independent-n-01"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mxene", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    compare = commands.add_parser('compare', help='Offline campaign comparison readiness; fails closed on unverified source contracts')
    compare.add_argument('--project', type=Path, default=Path.cwd())
    compare.add_argument('--campaign', default=DEFAULT_CAMPAIGN)

    run = commands.add_parser("run", help="Run offline by default; use explicit approvals for live automation")
    run.add_argument("--project", type=Path, default=Path.cwd())
    run.add_argument("--campaign", default=DEFAULT_CAMPAIGN)
    run.add_argument("--live", action="store_true", help="Enable the live campaign path (still requires confirmation)")
    run.add_argument("--confirm-live-submission", action="store_true",
                     help="Second explicit confirmation for QPU actions")
    run.add_argument("--resume", action="store_true",
                     help="Resume a durably paused campaign after its blocker is resolved")
    run.add_argument('--poll-seconds',type=int,choices=range(1,301),metavar='1..300',default=60,
                     help='Normal live polling interval; retry backoff is capped at 900 seconds')
    run.add_argument("--ai", choices=("python", "ai-assisted"), default="python",
                     help="Optional event-driven diagnostics; Python remains the executor")

    from account_config import ACCOUNT_LABELS
    preflight = commands.add_parser("preflight", help="Read-only saved-profile, backend, frozen-input and provider quota checks")
    preflight.add_argument("--project", type=Path, default=Path.cwd())
    preflight.add_argument("--accounts", nargs="+", choices=ACCOUNT_LABELS, default=list(ACCOUNT_LABELS))

    status = commands.add_parser("status", help="Read campaign and per-PUB status; no IBM connection")
    status.add_argument("--project", type=Path, default=Path.cwd())
    status.add_argument("--campaign", default=DEFAULT_CAMPAIGN)

    verify = commands.add_parser('verify-campaign',help='Read-only integrity, frozen provenance and accepted PUB coverage audit')
    verify.add_argument('--project',type=Path,default=Path.cwd())
    verify.add_argument('--campaign',default=DEFAULT_CAMPAIGN)

    reconcile = commands.add_parser("reconcile-collect", help="Recheck seven DONE jobs, link an existing intent, collect only; never submit")
    reconcile.add_argument("--project",type=Path,default=Path.cwd())
    reconcile.add_argument("--campaign",required=True)
    reconcile.add_argument("--block",required=True)
    reconcile.add_argument("--account",choices=ACCOUNT_LABELS,required=True)
    reconcile.add_argument("--batch-id",required=True)
    reconcile.add_argument("--m6-job-id",required=True)
    reconcile.add_argument("--treatment", help="Collect only this already reconciled treatment; verify all seven identities")

    m7 = commands.add_parser("recover-m7", help="Recover five M7 PUBs before M3 repair; never recover M3 or advance to another Batch")
    m7.add_argument("--project",type=Path,default=Path.cwd())
    m7.add_argument("--campaign",default=DEFAULT_CAMPAIGN)
    m7.add_argument("--block",default="vqe-independent-n-01-n25-block-003")
    m7.add_argument("--live",action="store_true")
    m7.add_argument("--confirm-m7-only",action="store_true")
    m7.add_argument("--authorize-m7-first-submission",action="store_true",
        help="Authorize only the original Batch 003 M7 reservation using fresh independent absence proof")
    m7.add_argument("--wait-seconds",type=int,default=0)

    combined=commands.add_parser('recover-m3-m7',help='Read original M3, then explicitly recover only Batch 003 M3/M7')
    combined.add_argument('--project',type=Path,default=Path.cwd())
    combined.add_argument('--campaign',default=DEFAULT_CAMPAIGN)
    combined.add_argument('--block',default='vqe-independent-n-01-n25-block-003')
    combined.add_argument('--live',action='store_true')
    combined.add_argument('--authorize-m3-replacement',action='store_true')
    combined.add_argument('--replacement-reason',default='')
    combined.add_argument('--wait-seconds',type=int,default=0)

    m3=commands.add_parser('recover-m3-pub15',help='Prepare or explicitly repair only Batch 003 M3 logical PUB 15')
    m3.add_argument('--project',type=Path,default=Path.cwd())
    m3.add_argument('--campaign',default=DEFAULT_CAMPAIGN)
    m3.add_argument('--block',default='vqe-independent-n-01-n25-block-003')
    m3.add_argument('--live',action='store_true')
    m3.add_argument('--confirm-m3-pub15',action='store_true')
    m3.add_argument('--wait-seconds',type=int,default=0)

    inspect_batch=commands.add_parser('inspect-m7-absence',help='Read Batch closure fields and M7 intent/history; never submit')
    inspect_batch.add_argument('--project',type=Path,default=Path.cwd())
    inspect_batch.add_argument('--campaign',default=DEFAULT_CAMPAIGN)
    inspect_batch.add_argument('--block',default='vqe-independent-n-01-n25-block-003')

    export = commands.add_parser("offline", aliases=['export'], help="Offline analysis, plots and paper exports for complete validated n levels")
    export.add_argument("--project", type=Path, default=Path.cwd())
    export.add_argument("--campaign", default=DEFAULT_CAMPAIGN)
    export.add_argument("--n", type=int, choices=(5, 10, 15, 20, 25, 30), default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == 'compare':
        import importlib.util
        path = Path(__file__).resolve().parents[1]/'01_ai_agent'/'offline_tools.py'
        spec = importlib.util.spec_from_file_location('mxene_offline_tools', path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        result = module.invoke('campaign-comparison', module.CampaignRequest(args.project,args.campaign))
        print(json.dumps(result,indent=2))
        return 0 if result['ready'] else 20
    if args.command == "run":
        if args.confirm_live_submission and not args.live:
            raise SystemExit("--confirm-live-submission requires --live")
        if args.live and not args.confirm_live_submission:
            raise SystemExit("Live mode requires both --live and --confirm-live-submission")
        from research_pipeline import run_pipeline
        result = run_pipeline(args.project, args.campaign,
            mode="live" if args.live else "offline",
            authorize_live_campaign=args.live,
            confirm_live_submission=args.confirm_live_submission,
            ai_mode=args.ai, resume=args.resume,poll_seconds=args.poll_seconds)
        if args.live:
            from manager_exit_codes import live_exit_code
            outcome=result['pipeline']
            summary={key:outcome[key] for key in ('state','pause_class','resume_required',
                'data_collection_complete','publication_exports_pending','publication_exports_independent') if key in outcome}
            code=live_exit_code(outcome)
            print(json.dumps({'campaign':args.campaign,'mode':'live','exit_code':code,**summary}),flush=True)
            return code
    elif args.command == "preflight":
        from read_only_preflight import preflight_all
        result = preflight_all(args.accounts, auth_mode="saved-profile", root=args.project)
    elif args.command == 'verify-campaign':
        from research_pipeline import verify_campaign
        result=verify_campaign(args.project,args.campaign)
        print(json.dumps(result,indent=2,default=str))
        return 0 if result['verification_passed'] else 2
    elif args.command == "reconcile-collect":
        from batch_scheduler import reconcile_and_collect_block
        result=reconcile_and_collect_block(args.project,args.campaign,args.block,args.account,
            args.batch_id,{"M6_TREX_ZNE":args.m6_job_id},
            treatments=(args.treatment,) if args.treatment else None)
    elif args.command == 'inspect-m7-absence':
        from targeted_recovery import inspect_m7_absence
        result=inspect_m7_absence(args.project,args.campaign,args.block)
    elif args.command == 'recover-m3-pub15':
        from targeted_recovery import recover_m3_pub15
        result=recover_m3_pub15(args.project,args.campaign,args.block,authorize_live=args.live,
            confirm_m3_pub15=args.confirm_m3_pub15,wait_seconds=args.wait_seconds)
    elif args.command == 'recover-m3-m7':
        from targeted_recovery import recover_m3_m7
        result=recover_m3_m7(args.project,args.campaign,args.block,authorize_live=args.live,
            authorize_m3_replacement=args.authorize_m3_replacement,replacement_reason=args.replacement_reason,
            wait_seconds=args.wait_seconds)
    elif args.command == "recover-m7":
        from targeted_recovery import recover_missing_m7
        result=recover_missing_m7(args.project,args.campaign,args.block,authorize_live=args.live,
            confirm_m7_only=args.confirm_m7_only,wait_seconds=args.wait_seconds,
            authorize_m7_first_submission=args.authorize_m7_first_submission)
    elif args.command == "status":
        from research_pipeline import status_pipeline
        result = status_pipeline(args.project, args.campaign)
    else:
        from research_pipeline import process_pipeline
        result = process_pipeline(args.project, args.campaign, n=args.n)
    json.dump(result,sys.stdout,ensure_ascii=False,indent=2,default=str)
    print()
    return 2 if ((args.command=="reconcile-collect" and result.get("collection_failures")) or
                 (args.command in ('inspect-m7-absence','recover-m7','recover-m3-pub15') and result.get('state')=='BLOCKED')) else 0


if __name__ == "__main__":
    raise SystemExit(main())
