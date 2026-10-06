"""Configuracion del collector cargada exclusivamente desde variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser numerico") from exc
    if value <= 0:
        raise ValueError(f"{name} debe ser mayor que cero")
    return value


def _nonempty_string(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise ValueError(f"{name} no puede estar vacio")
    return value


@dataclass(frozen=True)
class Settings:
    mikrotik_url: str
    mikrotik_user: str
    mikrotik_pass: str
    traffic_interval: float = 1.0
    ddm_interval: float = 5.0
    history_retention_hours: float = 3.0
    request_timeout: float = 4.0
    database_path: Path = BASE_DIR / "mk_collector.sqlite3"
    device_name: str = "DP-PRUEBAS"
    customer_interface: str = "ether1"
    uplink_interface: str = "sfp-sfpplus1"

    @classmethod
    def from_env(cls) -> "Settings":
        retention = _positive_float("HISTORY_RETENTION_HOURS", 3)
        if retention < 2:
            raise ValueError("HISTORY_RETENTION_HOURS debe ser al menos 2")

        database_path = Path(
            os.getenv("DATABASE_PATH", str(BASE_DIR / "mk_collector.sqlite3"))
        ).expanduser()

        return cls(
            mikrotik_url=os.getenv("MIKROTIK_URL", "http://192.168.250.2").rstrip("/"),
            mikrotik_user=os.getenv("MIKROTIK_USER", "collector"),
            mikrotik_pass=os.getenv("MIKROTIK_PASS", ""),
            traffic_interval=_positive_float("TRAFFIC_INTERVAL", 1),
            ddm_interval=_positive_float("DDM_INTERVAL", 5),
            history_retention_hours=retention,
            request_timeout=_positive_float("ROUTEROS_TIMEOUT", 4),
            database_path=database_path,
            device_name=os.getenv("DEVICE_NAME", "DP-PRUEBAS"),
            customer_interface=_nonempty_string("CUSTOMER_INTERFACE", "ether1"),
            uplink_interface=_nonempty_string("UPLINK_INTERFACE", "sfp-sfpplus1"),
        )
