import tempfile
import time
import unittest
from pathlib import Path

import requests

from collector import (
    CollectorService,
    CollectorState,
    RouterOSClient,
    bps_to_gbps,
    bps_to_mbps,
    normalize_ddm,
    normalize_traffic,
    number,
)
from config import Settings
from database import Database


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.auth = None
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        if self.error:
            raise self.error
        return FakeResponse(self.payload)

    def close(self):
        return None


class NormalizationTests(unittest.TestCase):
    def test_number_and_rate_conversions(self):
        self.assertEqual(number("52C"), 52)
        self.assertEqual(bps_to_mbps(496_200_000), 496.2)
        self.assertEqual(bps_to_gbps(2_500_000_000), 2.5)
        self.assertIsNone(bps_to_mbps(None))

    def test_traffic_normalization_preserves_missing_values(self):
        normalized = normalize_traffic(
            {
                "rx-bits-per-second": "496200000",
                "tx-bits-per-second": "0",
                "rx-packets-per-second": "1234",
            }
        )
        self.assertEqual(normalized["rx_mbps"], 496.2)
        self.assertEqual(normalized["tx_bps"], 0)
        self.assertEqual(normalized["rx_pps"], 1234)
        self.assertIsNone(normalized["tx_pps"])
        self.assertIsNone(normalize_traffic({})["rx_bps"])

    def test_ddm_raw_scaling_and_missing_values(self):
        normalized = normalize_ddm(
            {
                "sfp-supply-voltage": "3298",
                "sfp-rx-power": "-2064",
                "sfp-tx-power": "-1.731dBm",
            }
        )
        self.assertEqual(normalized["voltage_v"], 3.298)
        self.assertEqual(normalized["rx_power_dbm"], -2.064)
        self.assertEqual(normalized["tx_power_dbm"], -1.731)
        self.assertIsNone(normalized["temperature_c"])
        self.assertIsNone(normalized["vendor"])


class RouterClientTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings("http://router", "collector", "secret", request_timeout=2)

    def test_traffic_uses_read_only_monitor_endpoint(self):
        session = FakeSession(
            [{"name": "ether1", "rx-bits-per-second": "1000000"}]
        )
        client = RouterOSClient(self.settings, session)
        result = client.traffic()
        self.assertEqual(result["ether1"]["rx_mbps"], 1)
        self.assertTrue(session.calls[0][0].endswith("/rest/interface/monitor-traffic"))
        self.assertEqual(session.calls[0][2], 2)
        self.assertEqual(session.auth, ("collector", "secret"))

    def test_rest_timeout_propagates_without_fabricating_data(self):
        client = RouterOSClient(
            self.settings, FakeSession(error=requests.Timeout("offline"))
        )
        with self.assertRaises(requests.Timeout):
            client.traffic()

    def test_non_monitor_endpoint_is_rejected(self):
        client = RouterOSClient(self.settings, FakeSession([]))
        with self.assertRaises(ValueError):
            client._monitor("system/reboot", {})


class StateTests(unittest.TestCase):
    def test_failure_creates_gap_and_keeps_last_valid_sample(self):
        state = CollectorState("DP")
        sample = normalize_traffic({"rx-bits-per-second": "1000000"})
        state.record_traffic(1000, {"ether1": sample}, 12.3)
        state.record_failure(1001)
        snapshot = state.snapshot()
        self.assertFalse(snapshot["online"])
        self.assertEqual(snapshot["ether1"]["rx_bps"], 1_000_000)
        self.assertIsNone(snapshot["live"][-1]["interfaces"]["ether1"])
        self.assertEqual(snapshot["stats"]["ether1"]["rx"]["max_bps"], 1_000_000)


class RecoveringClient:
    def __init__(self):
        self.traffic_calls = 0

    def traffic(self):
        self.traffic_calls += 1
        if self.traffic_calls == 1:
            raise requests.Timeout("temporary")
        return {
            "ether1": normalize_traffic({"rx-bits-per-second": "1000000"}),
            "sfp-sfpplus1": normalize_traffic({"tx-bits-per-second": "1000000"}),
        }

    def ddm(self):
        return None

    def close(self):
        return None


class ServiceLifecycleTests(unittest.TestCase):
    def test_worker_recovers_after_temporary_rest_failure_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                "http://router",
                "collector",
                "secret",
                traffic_interval=0.01,
                ddm_interval=0.02,
                database_path=Path(directory) / "service.sqlite3",
            )
            database = Database(settings.database_path)
            state = CollectorState("DP")
            service = CollectorService(
                settings, database, state, client_factory=RecoveringClient
            )
            service.start()
            time.sleep(0.08)
            service.stop()

            snapshot = state.snapshot()
            self.assertTrue(snapshot["online"])
            self.assertTrue(any(sample["interfaces"]["ether1"] is None for sample in snapshot["live"]))
            self.assertTrue(any(sample["interfaces"]["ether1"] is not None for sample in snapshot["live"]))
            history = database.history(
                settings.device_name, 60, 1, ("ether1",), now=time.time()
            )
            self.assertGreater(history["interfaces"]["ether1"]["sample_count"], 0)
            self.assertEqual(service._threads, [])


if __name__ == "__main__":
    unittest.main()
