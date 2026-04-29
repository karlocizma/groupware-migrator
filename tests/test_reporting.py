from pathlib import Path
import tempfile
import unittest

from groupware_migrator.engine.reporting import build_job_report, build_job_report_csv
from groupware_migrator.engine.state import SQLiteStateStore
from groupware_migrator.models import MigrationPlan, MigrationPlanItem, MigrationRequest


class TestReporting(unittest.TestCase):
    def test_build_job_report_and_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_store = SQLiteStateStore(Path(temp_dir) / "state.db")
            request = MigrationRequest.from_dict(
                {
                    "job_name": "report-test",
                    "source": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.source",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                    "destination": {
                        "protocol": "imap",
                        "connection": {
                            "host": "imap.dest",
                            "username": "user",
                            "password": "pass",
                        },
                    },
                }
            )
            plan = MigrationPlan(
                items=[
                    MigrationPlanItem(
                        source_mailbox="INBOX",
                        destination_mailbox="Migrated/INBOX",
                        estimated_messages=5,
                    )
                ]
            )

            job_id = state_store.create_job(request, plan)
            state_store.increment_counters(job_id, migrated=3, skipped=1, failed=1)
            state_store.append_audit_event(job_id, "job_started")
            state_store.append_audit_event(job_id, "mailbox_completed")

            report = build_job_report(state_store, job_id=job_id, audit_event_limit=20)
            self.assertEqual(report["job"]["job_id"], job_id)
            self.assertEqual(report["metrics"]["processed_count"], 5)
            self.assertEqual(report["audit"]["event_type_counts"]["job_started"], 1)

            csv_payload = build_job_report_csv(report)
            self.assertIn("metrics,migrated_count,3", csv_payload)
            self.assertIn("plan,INBOX -> Migrated/INBOX,5", csv_payload)


if __name__ == "__main__":
    unittest.main()
