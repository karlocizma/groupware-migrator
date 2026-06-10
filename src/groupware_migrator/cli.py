from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from groupware_migrator.connectors.factory import create_source_connector
from groupware_migrator.engine.runner import MigrationRunner
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import JobStatus, MigrationRequest


# ---------------------------------------------------------------------------
# Migrate (legacy default command)
# ---------------------------------------------------------------------------

def _read_request(path: str) -> MigrationRequest:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return MigrationRequest.from_dict(payload)


def _build_migrate_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, help="Path to migration request JSON file.")
    parser.add_argument("--state-db", default="data/state.db", help="SQLite DB path for job state.")
    parser.add_argument("--plan-only", action="store_true", help="Print plan without running a job.")
    parser.add_argument("--dry-run", action="store_true", help="Execute in dry-run mode (no writes).")
    parser.add_argument("--resume-job-id", help="Resume a previously failed/incomplete job by ID.")


def _run_migrate(args: argparse.Namespace) -> int:
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

    report = runner.run(request=request, resume_job_id=args.resume_job_id)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.status is not JobStatus.FAILED else 1


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def _backup_db(db_path: Path, output_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(output_path))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    # Verify integrity of the backup
    conn = sqlite3.connect(str(output_path))
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    if result != "ok":
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Backup integrity check failed: {result}")


def _run_backup(args: argparse.Namespace) -> int:
    db_path = Path(args.state_db)
    output_path = Path(args.output)
    if output_path.exists() and not args.force:
        print(f"Error: {output_path} already exists. Use --force to overwrite.", file=sys.stderr)
        return 1
    print(f"Backing up {db_path} → {output_path} …")
    _backup_db(db_path, output_path)
    size_kb = output_path.stat().st_size // 1024
    print(f"Backup complete. {size_kb} KB written to {output_path}")
    return 0


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def _restore_db(backup_path: Path, db_path: Path, *, force: bool) -> None:
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    # Verify backup integrity before touching anything
    conn = sqlite3.connect(str(backup_path))
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    if result != "ok":
        raise RuntimeError(f"Backup file failed integrity check: {result}")
    if db_path.exists() and not force:
        raise FileExistsError(
            f"{db_path} already exists. Use --force to overwrite. "
            "Consider backing up the existing database first."
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(backup_path))
    dst = sqlite3.connect(str(db_path))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()


def _run_restore(args: argparse.Namespace) -> int:
    backup_path = Path(args.from_path)
    db_path = Path(args.state_db)
    try:
        print(f"Restoring {backup_path} → {db_path} …")
        _restore_db(backup_path, db_path, force=args.force)
        print(f"Restore complete. {db_path} is ready.")
        return 0
    except (FileNotFoundError, FileExistsError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Groupware Migrator — IMAP/POP3/CalDAV/CardDAV migration engine",
    )
    subparsers = parser.add_subparsers(dest="command")

    # backup
    backup_p = subparsers.add_parser("backup", help="Back up the state database.")
    backup_p.add_argument("--state-db", default="data/state.db", help="Source database path.")
    backup_p.add_argument("--output", required=True, help="Backup output file path.")
    backup_p.add_argument("--force", action="store_true", help="Overwrite output file if it exists.")

    # restore
    restore_p = subparsers.add_parser("restore", help="Restore the state database from a backup.")
    restore_p.add_argument("--state-db", default="data/state.db", help="Target database path.")
    restore_p.add_argument("--from", dest="from_path", required=True, help="Backup file to restore from.")
    restore_p.add_argument("--force", action="store_true", help="Overwrite existing database.")

    return parser


def main() -> int:
    # Legacy interface: groupware-migrator --config ... (no subcommand)
    # Route to subcommand parser only when a known subcommand is first arg.
    known_subcommands = {"backup", "restore"}
    first_arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if first_arg in known_subcommands:
        parser = build_parser()
        args = parser.parse_args()
        if args.command == "backup":
            return _run_backup(args)
        if args.command == "restore":
            return _run_restore(args)

    # Legacy migrate interface
    migrate_parser = argparse.ArgumentParser(
        description="Groupware Migrator — run a migration job",
    )
    _build_migrate_parser(migrate_parser)
    args = migrate_parser.parse_args()
    return _run_migrate(args)


if __name__ == "__main__":
    sys.exit(main())
