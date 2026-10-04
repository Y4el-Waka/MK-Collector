"""Persistencia SQLite corta para trafico y telemetria optica."""

from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


TRAFFIC_COLUMNS = (
    "rx_bps",
    "tx_bps",
    "rx_pps",
    "tx_pps",
    "fp_rx_bps",
    "fp_tx_bps",
    "rx_errors",
    "tx_errors",
    "rx_drops",
    "tx_drops",
    "queue_drops",
)


class Database:
    """Abre una conexion por operacion; es seguro usarlo desde varios threads."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS traffic_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    device TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    rx_bps REAL,
                    tx_bps REAL,
                    rx_pps INTEGER,
                    tx_pps INTEGER,
                    fp_rx_bps REAL,
                    fp_tx_bps REAL,
                    rx_errors INTEGER,
                    tx_errors INTEGER,
                    rx_drops INTEGER,
                    tx_drops INTEGER,
                    queue_drops INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_traffic_device_interface_time
                    ON traffic_samples(device, interface, timestamp);

                CREATE TABLE IF NOT EXISTS ddm_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    device TEXT NOT NULL,
                    interface TEXT NOT NULL,
                    status TEXT,
                    rate TEXT,
                    full_duplex TEXT,
                    temperature_c REAL,
                    voltage_v REAL,
                    tx_bias_ma REAL,
                    tx_power_dbm REAL,
                    rx_power_dbm REAL,
                    vendor TEXT,
                    model TEXT,
                    wavelength TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ddm_device_interface_time
                    ON ddm_samples(device, interface, timestamp);
                """
            )

    def insert_traffic(
        self,
        timestamp: float,
        device: str,
        samples: dict[str, dict[str, Any]],
    ) -> None:
        if not samples:
            return
        placeholders = ", ".join("?" for _ in range(14))
        rows = [
            (timestamp, device, interface, *(sample.get(key) for key in TRAFFIC_COLUMNS))
            for interface, sample in samples.items()
        ]
        with self._connect() as connection:
            connection.executemany(
                f"""
                INSERT INTO traffic_samples (
                    timestamp, device, interface, {", ".join(TRAFFIC_COLUMNS)}
                ) VALUES ({placeholders})
                """,
                rows,
            )

    def insert_ddm(
        self,
        timestamp: float,
        device: str,
        interface: str,
        sample: dict[str, Any],
    ) -> None:
        fields = (
            "status",
            "rate",
            "full_duplex",
            "temperature_c",
            "voltage_v",
            "tx_bias_ma",
            "tx_power_dbm",
            "rx_power_dbm",
            "vendor",
            "model",
            "wavelength",
        )
        values = (timestamp, device, interface, *(sample.get(field) for field in fields))
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO ddm_samples (
                    timestamp, device, interface, {", ".join(fields)}
                ) VALUES ({", ".join("?" for _ in values)})
                """,
                values,
            )

    def cleanup(self, retention_hours: float, now: float | None = None) -> int:
        cutoff = (now if now is not None else time.time()) - retention_hours * 3600
        with self._connect() as connection:
            traffic = connection.execute(
                "DELETE FROM traffic_samples WHERE timestamp < ?", (cutoff,)
            ).rowcount
            ddm = connection.execute(
                "DELETE FROM ddm_samples WHERE timestamp < ?", (cutoff,)
            ).rowcount
        return traffic + ddm

    def history(
        self,
        device: str,
        seconds: int,
        bucket_seconds: int,
        interfaces: Iterable[str],
        now: float | None = None,
    ) -> dict[str, Any]:
        end = now if now is not None else time.time()
        start = end - seconds
        interface_list = tuple(interfaces)
        placeholders = ", ".join("?" for _ in interface_list)
        query = f"""
            SELECT timestamp, interface, {", ".join(TRAFFIC_COLUMNS)}
            FROM traffic_samples
            WHERE device = ? AND timestamp >= ? AND timestamp <= ?
              AND interface IN ({placeholders})
            ORDER BY timestamp ASC
        """
        with self._connect() as connection:
            rows = connection.execute(
                query, (device, start, end, *interface_list)
            ).fetchall()

        grouped_rows: dict[str, list[dict[str, Any]]] = {
            interface: [] for interface in interface_list
        }
        for row in rows:
            grouped_rows[row["interface"]].append(dict(row))

        result: dict[str, Any] = {}
        for interface in interface_list:
            raw = grouped_rows[interface]
            result[interface] = {
                "points": self._downsample(raw, bucket_seconds),
                "stats": {
                    "rx": self._stats(raw, "rx_bps"),
                    "tx": self._stats(raw, "tx_bps"),
                },
                "sample_count": len(raw),
            }

        return {
            "from": start,
            "to": end,
            "bucket_seconds": bucket_seconds,
            "interfaces": result,
        }

    @staticmethod
    def _stats(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
        samples = [(row["timestamp"], row[field]) for row in rows if row[field] is not None]
        if not samples:
            return {
                "current_bps": None,
                "min_bps": None,
                "max_bps": None,
                "avg_bps": None,
                "max_at": None,
            }
        max_sample = max(samples, key=lambda sample: sample[1])
        values = [sample[1] for sample in samples]
        return {
            "current_bps": samples[-1][1],
            "min_bps": min(values),
            "max_bps": max_sample[1],
            "avg_bps": sum(values) / len(values),
            "max_at": max_sample[0],
        }

    @staticmethod
    def _downsample(rows: list[dict[str, Any]], bucket_seconds: int) -> list[dict[str, Any]]:
        buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            bucket = math.floor(row["timestamp"] / bucket_seconds)
            buckets[bucket].append(row)

        points: list[dict[str, Any]] = []
        for bucket_rows in buckets.values():
            point: dict[str, Any] = {
                "timestamp": bucket_rows[-1]["timestamp"],
                "sample_count": len(bucket_rows),
            }
            for field in TRAFFIC_COLUMNS:
                values = [row[field] for row in bucket_rows if row[field] is not None]
                point[field] = sum(values) / len(values) if values else None
                if field in {"rx_bps", "tx_bps"}:
                    prefix = field.removesuffix("_bps")
                    point[f"{prefix}_min_bps"] = min(values) if values else None
                    point[f"{prefix}_max_bps"] = max(values) if values else None
            points.append(point)
        return points

