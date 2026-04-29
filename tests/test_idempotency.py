import unittest

from groupware_migrator.engine.idempotency import (
    build_message_fingerprint,
    extract_message_id,
    normalize_message_id,
)


class TestIdempotency(unittest.TestCase):
    def test_normalize_message_id(self):
        self.assertEqual(
            normalize_message_id("  <Example-Id@Mail>  "),
            "example-id@mail",
        )

    def test_extract_message_id_from_raw_message(self):
        raw_message = (
            b"Message-ID: <id-123@example.com>\r\n"
            b"From: sender@example.com\r\n"
            b"To: receiver@example.com\r\n"
            b"Subject: Test\r\n"
            b"\r\n"
            b"Body"
        )
        self.assertEqual(extract_message_id(raw_message), "id-123@example.com")

    def test_fingerprint_uses_message_id_and_size(self):
        message_one = (
            b"Message-ID: <id-123@example.com>\r\n"
            b"Subject: One\r\n"
            b"\r\n"
            b"Body"
        )
        message_two = (
            b"Message-ID: <id-123@example.com>\r\n"
            b"Subject: Two\r\n"
            b"\r\n"
            b"Body"
        )
        fingerprint_one = build_message_fingerprint(message_one)
        fingerprint_two = build_message_fingerprint(message_two)
        self.assertEqual(fingerprint_one, fingerprint_two)

    def test_fingerprint_fallback_without_message_id(self):
        raw_message = (
            b"From: sender@example.com\r\n"
            b"To: receiver@example.com\r\n"
            b"Subject: Missing Message ID\r\n"
            b"\r\n"
            b"Body"
        )
        fingerprint_one = build_message_fingerprint(raw_message, source_id="10")
        fingerprint_two = build_message_fingerprint(raw_message, source_id="11")
        self.assertNotEqual(fingerprint_one, fingerprint_two)


if __name__ == "__main__":
    unittest.main()
