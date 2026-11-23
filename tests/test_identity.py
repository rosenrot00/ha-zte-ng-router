"""Device identifiers must be stable, normalized and privacy-conscious."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "zte_ng_router"
spec = importlib.util.spec_from_file_location("zte_identity", ROOT / "identity.py")
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class NormalizeMacTests(unittest.TestCase):
    def test_common_mac_formats(self):
        for value in (
            "AA:BB:CC:DD:EE:FF",
            "aa-bb-cc-dd-ee-ff",
            "aabb.ccdd.eeff",
            "AABBCCDDEEFF",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    identity.normalize_mac(value), "aa:bb:cc:dd:ee:ff"
                )

    def test_non_mac_is_rejected(self):
        for value in ("", "not-a-mac", "aa:bb:cc:dd:ee", "zz:zz:zz:zz:zz:zz", None, 123):
            with self.subTest(value=value):
                self.assertIsNone(identity.normalize_mac(value))


class DeviceIdentifierTests(unittest.TestCase):
    def test_wlan_mac_from_sim_info_is_preferred(self):
        data = {
            "sim_info": {
                "wlan_mac_address": "A0:B1:C2:D3:E4:F5",
                "imei": "123456789012345",
            },
            "device": {"mac_address": "00:11:22:33:44:55"},
        }
        self.assertEqual(
            identity.device_identifier_from_data(data), "a0:b1:c2:d3:e4:f5"
        )

    def test_goform_or_partial_payload_falls_back_to_serial(self):
        data = {"device": {"serial_number": "ZTE1234567"}}
        self.assertEqual(
            identity.device_identifier_from_data(data), "ZTE1234567"
        )

    def test_missing_or_placeholder_values_are_ignored(self):
        for data in (
            {"sim_info": {"wlan_mac_address": "unknown"}},
            {"sim_info": {"wlan_mac_address": ""}},
            {},
            None,
        ):
            with self.subTest(data=data):
                self.assertIsNone(identity.device_identifier_from_data(data))

    def test_entry_unique_id_is_prefixed(self):
        self.assertEqual(
            identity.entry_unique_id("aa:bb:cc:dd:ee:ff"),
            "zte_ng_router_aa:bb:cc:dd:ee:ff",
        )

    def test_only_legacy_address_ids_are_migrated(self):
        mac = "aa:bb:cc:dd:ee:ff"
        self.assertTrue(
            identity.should_migrate_unique_id("zte_ng_router_http://192.168.0.1", mac)
        )
        self.assertFalse(
            identity.should_migrate_unique_id(identity.entry_unique_id(mac), mac)
        )
        self.assertFalse(
            identity.should_migrate_unique_id(identity.entry_unique_id("11:22:33:44:55:66"), mac)
        )
        self.assertFalse(identity.should_migrate_unique_id(None, mac))


if __name__ == "__main__":
    unittest.main()
