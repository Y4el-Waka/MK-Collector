import tempfile
import unittest
from pathlib import Path

from app import create_app
from collector import CollectorState
from config import Settings
from database import Database


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            "http://router",
            "collector",
            "super-secret",
            database_path=Path(self.temp.name) / "api.sqlite3",
        )
        self.database = Database(self.settings.database_path)
        self.state = CollectorState("DP-PRUEBAS")
        app = create_app(self.settings, self.database, self.state)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_dashboard_and_compatible_state_endpoint(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ether1", response.json)
        self.assertIn("sfp", response.json)
        self.assertIn("ddm", response.json)
        self.assertNotIn("super-secret", response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_history_windows_and_downsample_intervals(self):
        for window, expected in (("20m", 3), ("1h", 5), ("2h", 10)):
            response = self.client.get(f"/api/history?window={window}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["window"], window)
            self.assertEqual(response.json["bucket_seconds"], expected)

    def test_invalid_history_window(self):
        response = self.client.get("/api/history?window=5y")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["allowed"], ["20m", "1h", "2h"])


if __name__ == "__main__":
    unittest.main()
