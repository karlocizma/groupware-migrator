from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from groupware_migrator.connectors.factory import create_source_connector
from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import JobStatus, MigrationRequest


def _read_request(path: str) -> MigrationRequest:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return MigrationRequest.from_dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Groupware Migrator - IMAP/POP3 migration engine",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to migration request JSON file.",
    )
    parser.add_argument(
        "--state-db",
        default="data/state.db",
        help="SQLite DB path used to persist job state.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Build and print migration plan without creating/running a job.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Override request options and execute in dry-run mode.",
    )
    parser.add_argument(
        "--resume-job-id",
        help="Resume a previously failed/incomplete job by ID.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    request = _read_request(args.config)
    if args.dry_run:
        request.options.dry_run = True

    state_store = SQLiteStateStore(args.state_db)
    runner = MigrationRunner(state_store=state_store)

    if args.plan_only:
        source_connector = create_source_connector(request)
        plan = runner.plan(request, source_connector=source_connector)
        print(json.dumps(plan.to_dict(), indent=2))
        return 0

    report = runner.run(
        request=request,
        resume_job_id=args.resume_job_id,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.status is not JobStatus.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
