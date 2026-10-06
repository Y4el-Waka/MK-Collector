"""Cliente RouterOS de solo lectura y workers de recoleccion."""

from __future__ import annotations

import copy
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable

import requests

from config import Settings
from database import Database


LOGGER = logging.getLogger(__name__)
INTERFACES = ("ether1", "sfp-sfpplus1")
READ_ONLY_ENDPOINTS = frozenset({"interface/monitor-traffic", "interface/ethernet/monitor"})


def number(value: Any) -> float | None:
    """Extrae un numero de una respuesta RouterOS conservando valores ausentes."""
    if value is None or value == "":
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def _integer(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def bps_to_mbps(value: float | None) -> float | None:
    return value / 1_000_000 if value is not None else None


def bps_to_gbps(value: float | None) -> float | None:
    return value / 1_000_000_000 if value is not None else None


def normalize_traffic(item: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "rx_bps": number(item.get("rx-bits-per-second")),
        "tx_bps": number(item.get("tx-bits-per-second")),
        "rx_pps": _integer(item.get("rx-packets-per-second")),
        "tx_pps": _integer(item.get("tx-packets-per-second")),
        "fp_rx_bps": number(item.get("fp-rx-bits-per-second")),
        "fp_tx_bps": number(item.get("fp-tx-bits-per-second")),
        "rx_errors": _integer(item.get("rx-errors-per-second")),
        "tx_errors": _integer(item.get("tx-errors-per-second")),
        "rx_drops": _integer(item.get("rx-drops-per-second")),
        "tx_drops": _integer(item.get("tx-drops-per-second")),
        "queue_drops": _integer(item.get("tx-queue-drops-per-second")),
    }
    mapping["rx_mbps"] = bps_to_mbps(mapping["rx_bps"])
    mapping["tx_mbps"] = bps_to_mbps(mapping["tx_bps"])
    mapping["fp_rx_mbps"] = bps_to_mbps(mapping["fp_rx_bps"])
    mapping["fp_tx_mbps"] = bps_to_mbps(mapping["fp_tx_bps"])
    return mapping


def _milli_if_raw(value: Any) -> float | None:
    parsed = number(value)
    if parsed is not None and abs(parsed) > 100:
        return parsed / 1000
    return parsed


def normalize_ddm(item: dict[str, Any]) -> dict[str, Any]:
    tx_bias = number(item.get("sfp-tx-bias-current"))
    if tx_bias is not None and abs(tx_bias) > 1000:
        tx_bias /= 1000
    return {
        "status": item.get("status"),
        "rate": item.get("rate"),
        "full_duplex": item.get("full-duplex"),
        "temperature_c": number(item.get("sfp-temperature")),
        "voltage_v": _milli_if_raw(item.get("sfp-supply-voltage")),
        "tx_bias_ma": tx_bias,
        "tx_power_dbm": _milli_if_raw(item.get("sfp-tx-power")),
        "rx_power_dbm": _milli_if_raw(item.get("sfp-rx-power")),
        "vendor": item.get("sfp-vendor-name"),
        "model": item.get("sfp-vendor-part-number"),
        "wavelength": item.get("sfp-wavelength"),
    }


class RouterOSClient:
    """Cliente limitado explicitamente a comandos monitor de solo lectura."""

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()
        self.session.auth = (settings.mikrotik_user, settings.mikrotik_pass)
        self.interface_map = {
            "ether1": settings.customer_interface,
            "sfp-sfpplus1": settings.uplink_interface,
        }

    def _monitor(self, endpoint: str, payload: dict[str, Any]) -> Any:
        if endpoint not in READ_ONLY_ENDPOINTS:
            raise ValueError("Endpoint RouterOS no permitido")
        response = self.session.post(
            f"{self.settings.mikrotik_url}/rest/{endpoint}",
            json=payload,
            timeout=self.settings.request_timeout,
        )
        response.raise_for_status()
        return response.json()

    def traffic(self) -> dict[str, dict[str, Any]]:
        physical_interfaces = tuple(
            self.interface_map[interface] for interface in INTERFACES
        )
        data = self._monitor(
            "interface/monitor-traffic",
            {"interface": ",".join(physical_interfaces), "once": ""},
        )
        if not isinstance(data, list):
            raise ValueError("Respuesta de trafico inesperada")
        by_name = {item.get("name"): item for item in data if isinstance(item, dict)}
        return {
            logical_name: normalize_traffic(by_name[self.interface_map[logical_name]])
            for logical_name in INTERFACES
            if self.interface_map[logical_name] in by_name
        }

    def ddm(self) -> dict[str, Any] | None:
        data = self._monitor(
            "interface/ethernet/monitor",
            {"numbers": self.settings.uplink_interface, "once": ""},
        )
        item = data[0] if isinstance(data, list) and data else data
        return normalize_ddm(item) if isinstance(item, dict) and item else None

    def close(self) -> None:
        self.session.close()


def _stats(samples: list[tuple[float, float]]) -> dict[str, float | None]:
    if not samples:
        return {"current_bps": None, "min_bps": None, "max_bps": None, "avg_bps": None, "max_at": None}
    peak = max(samples, key=lambda sample: sample[1])
    values = [sample[1] for sample in samples]
    return {
        "current_bps": samples[-1][1],
        "min_bps": min(values),
        "max_bps": peak[1],
        "avg_bps": sum(values) / len(values),
        "max_at": peak[0],
    }


class CollectorState:
    def __init__(self, identity: str):
        self._lock = threading.Lock()
        self._live: deque[dict[str, Any]] = deque(maxlen=60)
        self._state: dict[str, Any] = {
            "online": False,
            "identity": identity,
            "updated_at": None,
            "sampled_at": None,
            "latency_ms": None,
            "error": None,
            "ddm_error": None,
            "ether1": {},
            "sfp": {},
            "ddm": {},
        }

    def record_traffic(
        self, timestamp: float, samples: dict[str, dict[str, Any]], latency_ms: float
    ) -> None:
        live_interfaces = {interface: samples.get(interface) for interface in INTERFACES}
        with self._lock:
            if "ether1" in samples:
                self._state["ether1"] = samples["ether1"]
            if "sfp-sfpplus1" in samples:
                self._state["sfp"] = samples["sfp-sfpplus1"]
            self._state.update(
                online=True,
                updated_at=datetime.fromtimestamp(timestamp).strftime("%H:%M:%S"),
                sampled_at=timestamp,
                latency_ms=round(latency_ms, 1),
                error=None,
            )
            self._live.append({"timestamp": timestamp, "interfaces": live_interfaces})

    def record_failure(self, timestamp: float, error: str = "RouterOS no respondio") -> None:
        with self._lock:
            self._state["online"] = False
            self._state["error"] = error
            self._live.append(
                {"timestamp": timestamp, "interfaces": {interface: None for interface in INTERFACES}}
            )

    def record_ddm(self, sample: dict[str, Any]) -> None:
        with self._lock:
            self._state["ddm"] = sample
            self._state["ddm_error"] = None

    def record_ddm_failure(self) -> None:
        with self._lock:
            self._state["ddm_error"] = "Telemetria DDM no disponible"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = copy.deepcopy(self._state)
            live = copy.deepcopy(list(self._live))
        result["live"] = live
        result["stats"] = {}
        for interface in INTERFACES:
            direction_stats = {}
            for direction in ("rx", "tx"):
                field = f"{direction}_bps"
                values = [
                    (sample["timestamp"], sample["interfaces"][interface][field])
                    for sample in live
                    if sample["interfaces"].get(interface)
                    and sample["interfaces"][interface].get(field) is not None
                ]
                direction_stats[direction] = _stats(values)
            result["stats"][interface] = direction_stats
        return result


class CollectorService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        state: CollectorState,
        client_factory: Callable[[], RouterOSClient] | None = None,
    ):
        self.settings = settings
        self.database = database
        self.state = state
        self.client_factory = client_factory or (lambda: RouterOSClient(settings))
        self.stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        self.database.initialize()
        self._threads = [
            threading.Thread(target=self._traffic_worker, name="traffic-worker", daemon=True),
            threading.Thread(target=self._ddm_worker, name="ddm-worker", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 6.0) -> None:
        self.stop_event.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()

    def _traffic_worker(self) -> None:
        client = self.client_factory()
        next_cleanup = 0.0
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                timestamp = time.time()
                try:
                    samples = client.traffic()
                    latency_ms = (time.monotonic() - started) * 1000
                    if not samples:
                        raise ValueError("RouterOS no devolvio interfaces")
                    self.database.insert_traffic(timestamp, self.settings.device_name, samples)
                    self.state.record_traffic(timestamp, samples, latency_ms)
                    if time.monotonic() >= next_cleanup:
                        self.database.cleanup(self.settings.history_retention_hours, timestamp)
                        next_cleanup = time.monotonic() + 300
                except Exception as exc:  # El worker debe sobrevivir a red, HTTP, JSON y SQLite.
                    LOGGER.warning("Fallo de consulta de trafico (%s)", type(exc).__name__)
                    self.state.record_failure(timestamp)
                elapsed = time.monotonic() - started
                self.stop_event.wait(max(0.0, self.settings.traffic_interval - elapsed))
        finally:
            client.close()

    def _ddm_worker(self) -> None:
        client = self.client_factory()
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                timestamp = time.time()
                try:
                    sample = client.ddm()
                    if sample is not None:
                        self.database.insert_ddm(
                            timestamp, self.settings.device_name, "sfp-sfpplus1", sample
                        )
                        self.state.record_ddm(sample)
                    else:
                        self.state.record_ddm_failure()
                except Exception as exc:
                    LOGGER.warning("Fallo de consulta DDM (%s)", type(exc).__name__)
                    self.state.record_ddm_failure()
                elapsed = time.monotonic() - started
                self.stop_event.wait(max(0.0, self.settings.ddm_interval - elapsed))
        finally:
            client.close()
