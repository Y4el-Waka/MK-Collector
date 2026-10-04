"""MK-Collector v0.2: aplicacion Flask y lifecycle del servicio."""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify, render_template, request

from collector import CollectorService, CollectorState, INTERFACES
from config import Settings
from database import Database


HISTORY_WINDOWS: dict[str, tuple[int, int]] = {
    "20m": (20 * 60, 3),
    "1h": (60 * 60, 5),
    "2h": (2 * 60 * 60, 10),
}


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    state: CollectorState | None = None,
) -> Flask:
    settings = settings or Settings.from_env()
    database = database or Database(settings.database_path)
    state = state or CollectorState(settings.device_name)
    database.initialize()

    flask_app = Flask(__name__)
    flask_app.config.update(JSON_SORT_KEYS=False)
    flask_app.extensions["mk_settings"] = settings
    flask_app.extensions["mk_database"] = database
    flask_app.extensions["mk_state"] = state

    @flask_app.after_request
    def no_cache_api(response: Any) -> Any:
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @flask_app.get("/")
    def dashboard() -> str:
        return render_template("dashboard.html", device_name=settings.device_name)

    @flask_app.get("/api/state")
    def api_state() -> Any:
        return jsonify(state.snapshot())

    @flask_app.get("/api/history")
    def api_history() -> Any:
        window = request.args.get("window", "20m")
        definition = HISTORY_WINDOWS.get(window)
        if definition is None:
            return jsonify(error="Ventana no valida", allowed=list(HISTORY_WINDOWS)), 400
        seconds, bucket_seconds = definition
        payload = database.history(
            device=settings.device_name,
            seconds=seconds,
            bucket_seconds=bucket_seconds,
            interfaces=INTERFACES,
        )
        payload["window"] = window
        return jsonify(payload)

    return flask_app


app = create_app()


def main() -> None:
    settings: Settings = app.extensions["mk_settings"]
    database: Database = app.extensions["mk_database"]
    state: CollectorState = app.extensions["mk_state"]

    if not settings.mikrotik_pass:
        raise RuntimeError("Define MIKROTIK_PASS antes de iniciar.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    service = CollectorService(settings, database, state)
    service.start()

    print()
    print("MikroTik Telemetry Collector v0.2")
    print("----------------------------------")
    print(f"Router: {settings.mikrotik_url}")
    print("Dashboard: http://127.0.0.1:5000")
    print()

    try:
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    finally:
        service.stop()


if __name__ == "__main__":
    main()

