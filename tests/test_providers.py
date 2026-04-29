import unittest

from groupware_migrator.providers import get_provider_preset, get_provider_presets


class TestProviderPresets(unittest.TestCase):
    def test_presets_include_custom_and_gmail(self):
        presets = get_provider_presets()
        ids = {preset["id"] for preset in presets}
        self.assertIn("custom", ids)
        self.assertIn("gmail", ids)

    def test_get_provider_preset_returns_copy(self):
        preset = get_provider_preset("gmail")
        self.assertIsNotNone(preset)
        assert preset is not None
        preset["name"] = "Modified"

        fresh = get_provider_preset("gmail")
        self.assertEqual(fresh["name"], "Gmail")

    def test_missing_provider_returns_none(self):
        self.assertIsNone(get_provider_preset("not-real"))

    def test_provider_defaults_include_auth_mode_and_oauth_hints(self):
        preset = get_provider_preset("gmail")
        self.assertIsNotNone(preset)
        assert preset is not None

        source_imap_defaults = preset["source_defaults"]["imap"]
        destination_imap_defaults = preset["destination_defaults"]["imap"]
        self.assertIn("auth_mode", source_imap_defaults)
        self.assertIn("oauth_token_url", source_imap_defaults)
        self.assertIn("oauth_scope", source_imap_defaults)
        self.assertIn("auth_mode", destination_imap_defaults)


if __name__ == "__main__":
    unittest.main()
