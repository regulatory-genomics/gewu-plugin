import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


class SettingsSchemaTests(unittest.TestCase):
    def test_default_and_explicit_browser_modes(self):
        manifest = Path(__file__).resolve().parents[1] / "plugin/plugin.json"
        validator = Draft202012Validator(json.loads(manifest.read_text())["settingsSchema"])
        self.assertTrue(validator.is_valid({}))
        self.assertTrue(validator.is_valid({"mode": "managed"}))
        self.assertFalse(validator.is_valid({"mode": "cdp"}))
        self.assertTrue(validator.is_valid({"mode": "cdp", "cdpEndpoint": "http://localhost:9222"}))
