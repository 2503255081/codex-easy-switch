import unittest

from codex_provider_launcher.diagnostics import protect, redact


class DiagnosticsTests(unittest.TestCase):
    def test_secrets_and_auth_headers_are_redacted(self):
        protect("fixture-private-value")
        result = redact("key=fixture-private-value Authorization: Bearer opaque-value sk-synthetic-token ocx_data_fixture123")
        for secret in ("fixture-private-value", "opaque-value", "sk-synthetic-token", "ocx_data_fixture123"):
            self.assertNotIn(secret, result)
        self.assertIn("[REDACTED]", result)

    def test_url_credentials_are_redacted(self):
        result = redact("https://user:password@example.com/v1?api_key=fixture&token=another")
        for secret in ("password", "fixture", "another"):
            self.assertNotIn(secret, result)
