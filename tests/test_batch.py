import unittest

from groupware_migrator.engine.batch import build_batch_preview, build_batch_rows


class TestBatchCsvParsing(unittest.TestCase):
    def _base_request(self) -> dict:
        return {
            "source": {
                "protocol": "imap",
                "provider_id": "custom",
                "connection": {
                    "host": "imap.source.example.com",
                    "port": 993,
                    "username": "base-source",
                    "password": "base-source-pass",
                    "use_ssl": True,
                    "tls_profile": "modern",
                },
            },
            "destination": {
                "protocol": "imap",
                "provider_id": "custom",
                "connection": {
                    "host": "imap.destination.example.com",
                    "port": 993,
                    "username": "base-destination",
                    "password": "base-destination-pass",
                    "use_ssl": True,
                    "tls_profile": "modern",
                },
                "root_mailbox": "Migrated",
            },
            "options": {
                "dry_run": False,
                "max_errors": 25,
                "pop3_destination_mailbox": "POP3-Inbox",
            },
        }

    def test_build_batch_rows_applies_overrides(self):
        csv_payload = (
            "job_name,source_username,source_password,destination_username,destination_password,dry_run,max_errors\n"
            "wave-a-user-1,user1@source.example.com,src-pass-1,user1@dest.example.com,dst-pass-1,true,12\n"
            "wave-a-user-2,user2@source.example.com,src-pass-2,user2@dest.example.com,dst-pass-2,false,7\n"
        )

        rows = build_batch_rows(
            csv_payload,
            base_request_payload=self._base_request(),
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].valid)
        self.assertTrue(rows[1].valid)

        first_request = rows[0].request
        second_request = rows[1].request
        assert first_request is not None
        assert second_request is not None
        self.assertEqual(first_request.job_name, "wave-a-user-1")
        self.assertEqual(first_request.source.connection.username, "user1@source.example.com")
        self.assertEqual(
            first_request.destination.connection.username,
            "user1@dest.example.com",
        )
        self.assertEqual(first_request.options.max_errors, 12)
        self.assertTrue(first_request.options.dry_run)

        self.assertEqual(second_request.job_name, "wave-a-user-2")
        self.assertEqual(second_request.options.max_errors, 7)
        self.assertFalse(second_request.options.dry_run)

    def test_build_batch_rows_marks_invalid_entries(self):
        csv_payload = (
            "job_name,source_protocol,source_username,source_password,destination_username,destination_password\n"
            "broken-row,smtp,user1@source.example.com,src-pass-1,user1@dest.example.com,dst-pass\n"
        )

        rows = build_batch_rows(
            csv_payload,
            base_request_payload=self._base_request(),
        )
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].valid)
        self.assertIn("smtp", (rows[0].error or "").lower())

        preview = build_batch_preview(rows)
        self.assertEqual(preview["total_rows"], 1)
        self.assertEqual(preview["valid_rows"], 0)
        self.assertEqual(preview["invalid_rows"], 1)

    def test_build_batch_rows_applies_oauth_overrides(self):
        csv_payload = (
            "job_name,source_auth_mode,source_oauth_access_token,destination_auth_mode,destination_oauth_refresh_token,destination_oauth_client_id,destination_oauth_client_secret,destination_oauth_token_url,destination_oauth_scope\n"
            "wave-oauth-user,oauth2,src-access-token,oauth2,dst-refresh-token,dst-client-id,dst-client-secret,https://oauth.example.com/token,mail.read\n"
        )

        rows = build_batch_rows(
            csv_payload,
            base_request_payload=self._base_request(),
        )
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].valid)

        request = rows[0].request
        assert request is not None
        self.assertEqual(request.source.connection.auth_mode.value, "oauth2")
        self.assertEqual(request.source.connection.oauth_access_token, "src-access-token")
        self.assertEqual(request.destination.connection.auth_mode.value, "oauth2")
        self.assertEqual(
            request.destination.connection.oauth_refresh_token,
            "dst-refresh-token",
        )
        self.assertEqual(request.destination.connection.oauth_client_id, "dst-client-id")
        self.assertEqual(
            request.destination.connection.oauth_client_secret,
            "dst-client-secret",
        )
        self.assertEqual(
            request.destination.connection.oauth_token_url,
            "https://oauth.example.com/token",
        )
        self.assertEqual(request.destination.connection.oauth_scope, "mail.read")

    def test_build_batch_rows_applies_incremental_sync_overrides(self):
        csv_payload = (
            "job_name,sync_mode,incremental_base_job_id\n"
            "wave-inc-user,incremental,7fd6f4e7-89f4-4f86-b4bf-5ddf5bb5f62e\n"
        )

        rows = build_batch_rows(
            csv_payload,
            base_request_payload=self._base_request(),
        )
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].valid)

        request = rows[0].request
        assert request is not None
        self.assertEqual(request.options.sync_mode.value, "incremental")
        self.assertEqual(
            request.options.incremental_base_job_id,
            "7fd6f4e7-89f4-4f86-b4bf-5ddf5bb5f62e",
        )


if __name__ == "__main__":
    unittest.main()
