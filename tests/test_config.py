import os
import unittest
from unittest.mock import patch

from config import Settings


class SettingsTests(unittest.TestCase):
    def test_interface_defaults_preserve_logical_contract(self):
        with patch.dict(os.environ, {"MIKROTIK_PASS": "secret"}, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.customer_interface, "ether1")
        self.assertEqual(settings.uplink_interface, "sfp-sfpplus1")

    def test_physical_interfaces_are_loaded_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "MIKROTIK_PASS": "secret",
                "CUSTOMER_INTERFACE": "ether4",
                "UPLINK_INTERFACE": "sfp-uplink",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.customer_interface, "ether4")
        self.assertEqual(settings.uplink_interface, "sfp-uplink")


if __name__ == "__main__":
    unittest.main()
