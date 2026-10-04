import tempfile
import unittest
from pathlib import Path

from database import Database


def traffic(rx, tx):
    return {
        "rx_bps": rx,
        "tx_bps": tx,
        "rx_pps": 10,
        "tx_pps": 20,
        "fp_rx_bps": rx,
        "fp_tx_bps": tx,
        "rx_errors": 0,
        "tx_errors": 0,
        "rx_drops": 0,
        "tx_drops": 0,
        "queue_drops": 0,
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "collector.sqlite3")
        self.db.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_insert_history_stats_and_peak_preserving_downsample(self):
        for offset, rx in enumerate([10, 20, 999, 30, 40, 50]):
            self.db.insert_traffic(
                1_000 + offset,
                "DP",
                {"ether1": traffic(rx, rx * 2)},
            )
        result = self.db.history("DP", 20, 3, ("ether1",), now=1_010)
        entry = result["interfaces"]["ether1"]
        self.assertEqual(entry["sample_count"], 6)
        self.assertEqual(entry["stats"]["rx"]["current_bps"], 50)
        self.assertEqual(entry["stats"]["rx"]["min_bps"], 10)
        self.assertEqual(entry["stats"]["rx"]["max_bps"], 999)
        self.assertAlmostEqual(entry["stats"]["rx"]["avg_bps"], 1149 / 6)
        self.assertTrue(any(point["rx_max_bps"] == 999 for point in entry["points"]))
        self.assertLess(len(entry["points"]), entry["sample_count"])

    def test_nullable_fields_remain_null(self):
        self.db.insert_traffic(1000, "DP", {"ether1": {"rx_bps": None}})
        result = self.db.history("DP", 10, 3, ("ether1",), now=1001)
        self.assertIsNone(result["interfaces"]["ether1"]["stats"]["rx"]["current_bps"])

    def test_cleanup_removes_traffic_and_ddm_outside_retention(self):
        self.db.insert_traffic(100, "DP", {"ether1": traffic(1, 2)})
        self.db.insert_traffic(10_000, "DP", {"ether1": traffic(3, 4)})
        self.db.insert_ddm(100, "DP", "sfp-sfpplus1", {"status": "link-ok"})
        removed = self.db.cleanup(2, now=10_000)
        self.assertEqual(removed, 2)
        result = self.db.history("DP", 20, 3, ("ether1",), now=10_001)
        self.assertEqual(result["interfaces"]["ether1"]["sample_count"], 1)


if __name__ == "__main__":
    unittest.main()
