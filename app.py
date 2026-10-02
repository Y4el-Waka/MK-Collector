import os
from dotenv import load_dotenv
load_dotenv()
import re
import time
import threading
from datetime import datetime
from flask import Flask, jsonify, render_template_string

import requests


# =========================================================
# CONFIG
# =========================================================

MIKROTIK_URL = os.getenv("MIKROTIK_URL", "http://192.168.250.2")
MIKROTIK_USER = os.getenv("MIKROTIK_USER", "collector")
MIKROTIK_PASS = os.getenv("MIKROTIK_PASS", "")

TRAFFIC_INTERVAL = 1
DDM_INTERVAL = 5

app = Flask(__name__)

session = requests.Session()
session.auth = (MIKROTIK_USER, MIKROTIK_PASS)


# =========================================================
# STATE
# =========================================================

state_lock = threading.Lock()

state = {
    "online": False,
    "identity": "DP-PRUEBAS",
    "updated_at": None,
    "error": None,

    "ether1": {},
    "sfp": {},
    "ddm": {},
}


# =========================================================
# HELPERS
# =========================================================

def number(value):
    """Extrae un número de valores RouterOS como:
       52, 52C, 3298, 3.298V, -2064, -2.064dBm
    """
    if value is None:
        return None

    match = re.search(r"-?\d+(?:\.\d+)?", str(value))

    if not match:
        return None

    return float(match.group())


def router_post(endpoint, payload):
    response = session.post(
        f"{MIKROTIK_URL}/rest/{endpoint}",
        json=payload,
        timeout=4,
    )

    response.raise_for_status()

    return response.json()


def normalize_traffic(item):
    return {
        "rx_mbps": number(item.get("rx-bits-per-second", 0)) / 1_000_000,
        "tx_mbps": number(item.get("tx-bits-per-second", 0)) / 1_000_000,

        "rx_pps": int(number(item.get("rx-packets-per-second", 0)) or 0),
        "tx_pps": int(number(item.get("tx-packets-per-second", 0)) or 0),

        "fp_rx_mbps": number(
            item.get("fp-rx-bits-per-second", 0)
        ) / 1_000_000,

        "fp_tx_mbps": number(
            item.get("fp-tx-bits-per-second", 0)
        ) / 1_000_000,

        "rx_errors": int(
            number(item.get("rx-errors-per-second", 0)) or 0
        ),

        "tx_errors": int(
            number(item.get("tx-errors-per-second", 0)) or 0
        ),

        "rx_drops": int(
            number(item.get("rx-drops-per-second", 0)) or 0
        ),

        "tx_drops": int(
            number(item.get("tx-drops-per-second", 0)) or 0
        ),

        "queue_drops": int(
            number(item.get("tx-queue-drops-per-second", 0)) or 0
        ),
    }


def normalize_ddm(item):
    temperature = number(item.get("sfp-temperature"))
    voltage = number(item.get("sfp-supply-voltage"))
    tx_bias = number(item.get("sfp-tx-bias-current"))
    tx_power = number(item.get("sfp-tx-power"))
    rx_power = number(item.get("sfp-rx-power"))

    # RouterOS puede devolver valores crudos.
    if voltage is not None and voltage > 100:
        voltage /= 1000

    if tx_power is not None and abs(tx_power) > 100:
        tx_power /= 1000

    if rx_power is not None and abs(rx_power) > 100:
        rx_power /= 1000

    return {
        "status": item.get("status"),
        "rate": item.get("rate"),
        "full_duplex": item.get("full-duplex"),

        "temperature_c": temperature,
        "voltage_v": voltage,
        "tx_bias_ma": tx_bias,
        "tx_power_dbm": tx_power,
        "rx_power_dbm": rx_power,

        "vendor": item.get("sfp-vendor-name"),
        "model": item.get("sfp-vendor-part-number"),
        "wavelength": item.get("sfp-wavelength"),
    }


# =========================================================
# COLLECTOR
# =========================================================

def traffic_worker():
    while True:
        try:
            data = router_post(
                "interface/monitor-traffic",
                {
                    "interface": "ether1,sfp-sfpplus1",
                    "once": "",
                },
            )

            interfaces = {
                item.get("name"): item
                for item in data
            }

            ether = normalize_traffic(
                interfaces.get("ether1", {})
            )

            sfp = normalize_traffic(
                interfaces.get("sfp-sfpplus1", {})
            )

            with state_lock:
                state["ether1"] = ether
                state["sfp"] = sfp
                state["online"] = True
                state["updated_at"] = datetime.now().strftime(
                    "%H:%M:%S"
                )
                state["error"] = None

        except Exception as exc:
            with state_lock:
                state["online"] = False
                state["error"] = str(exc)

        time.sleep(TRAFFIC_INTERVAL)


def ddm_worker():
    while True:
        try:
            data = router_post(
                "interface/ethernet/monitor",
                {
                    "numbers": "sfp-sfpplus1",
                    "once": "",
                },
            )

            if isinstance(data, list):
                item = data[0] if data else {}
            else:
                item = data

            ddm = normalize_ddm(item)

            with state_lock:
                state["ddm"] = ddm

        except Exception as exc:
            print("DDM error:", exc)

        time.sleep(DDM_INTERVAL)


# =========================================================
# API
# =========================================================

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify(state)


# =========================================================
# FRONTEND
# =========================================================

HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>MikroTik Collector</title>

<style>

body {
    margin: 0;
    background: #090b10;
    color: #e7eaf0;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 1100px;
    margin: auto;
    padding: 30px;
}

header {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

h1 {
    margin: 0;
    font-size: 26px;
}

.subtitle {
    color: #888;
    margin-top: 6px;
}

.status {
    font-weight: bold;
}

.online {
    color: #55d187;
}

.offline {
    color: #ff6565;
}

.grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 18px;
    margin-top: 30px;
}

.card {
    background: #11141b;
    border: 1px solid #242832;
    border-radius: 15px;
    padding: 22px;
}

.card h2 {
    margin-top: 0;
    font-size: 16px;
    color: #aaa;
}

.metrics {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 15px;
}

.metric {
    background: #0c0f15;
    border-radius: 10px;
    padding: 15px;
}

.label {
    color: #777;
    font-size: 12px;
    text-transform: uppercase;
}

.value {
    margin-top: 8px;
    font-size: 28px;
    font-weight: bold;
}

.unit {
    color: #888;
    font-size: 13px;
}

.ddm {
    grid-column: 1 / -1;
}

.ddm-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 14px;
}

.small-value {
    font-size: 20px;
    margin-top: 6px;
}

canvas {
    width: 100%;
    height: 260px;
    background: #0c0f15;
    border-radius: 12px;
    margin-top: 20px;
}

.error {
    color: #ff7474;
    margin-top: 15px;
}

@media(max-width: 700px) {

    .grid {
        grid-template-columns: 1fr;
    }

    .ddm {
        grid-column: auto;
    }

    .ddm-grid {
        grid-template-columns: repeat(2, 1fr);
    }
}

</style>

</head>

<body>

<div class="container">

<header>

<div>
<h1>DP-PRUEBAS</h1>
<div class="subtitle">
MikroTik Telemetry Collector v0.1
</div>
</div>

<div id="status" class="status offline">
● OFFLINE
</div>

</header>


<div class="grid">

<div class="card">

<h2>ETHER1 · CLIENTE</h2>

<div class="metrics">

<div class="metric">
<div class="label">RX</div>
<div id="ether-rx" class="value">0.00</div>
<div class="unit">Mbps</div>
</div>

<div class="metric">
<div class="label">TX</div>
<div id="ether-tx" class="value">0.00</div>
<div class="unit">Mbps</div>
</div>

</div>

</div>


<div class="card">

<h2>SFP-SFPPLUS1 · UPLINK</h2>

<div class="metrics">

<div class="metric">
<div class="label">RX</div>
<div id="sfp-rx" class="value">0.00</div>
<div class="unit">Mbps</div>
</div>

<div class="metric">
<div class="label">TX</div>
<div id="sfp-tx" class="value">0.00</div>
<div class="unit">Mbps</div>
</div>

</div>

</div>


<div class="card ddm">

<h2>ÓPTICA / DDM</h2>

<div class="ddm-grid">

<div>
<div class="label">Link</div>
<div id="link" class="small-value">---</div>
</div>

<div>
<div class="label">Velocidad</div>
<div id="rate" class="small-value">---</div>
</div>

<div>
<div class="label">Temperatura</div>
<div id="temp" class="small-value">---</div>
</div>

<div>
<div class="label">RX Power</div>
<div id="rx-power" class="small-value">---</div>
</div>

<div>
<div class="label">TX Power</div>
<div id="tx-power" class="small-value">---</div>
</div>

</div>

</div>


<div class="card ddm">

<h2>TRÁFICO LIVE · ÚLTIMOS 60 SEGUNDOS</h2>

<canvas id="chart" width="1000" height="260"></canvas>

<div id="last-update" class="subtitle"></div>
<div id="error" class="error"></div>

</div>

</div>

</div>


<script>

const history = {
    etherRx: [],
    etherTx: [],
    sfpRx: [],
    sfpTx: []
};

const MAX = 60;

function push(arr, value) {
    arr.push(value);
    if (arr.length > MAX) arr.shift();
}


function drawChart() {

    const canvas = document.getElementById("chart");
    const ctx = canvas.getContext("2d");

    const w = canvas.width;
    const h = canvas.height;

    ctx.clearRect(0, 0, w, h);

    const all = [
        ...history.etherRx,
        ...history.etherTx,
        ...history.sfpRx,
        ...history.sfpTx
    ];

    const maxValue = Math.max(10, ...all) * 1.15;

    ctx.strokeStyle = "#242832";
    ctx.lineWidth = 1;

    for (let i = 1; i < 5; i++) {
        const y = (h / 5) * i;

        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
    }


    function line(data, color) {

        if (data.length < 2) return;

        ctx.strokeStyle = color;
        ctx.lineWidth = 2;

        ctx.beginPath();

        data.forEach((value, i) => {

            const x = (i / (MAX - 1)) * w;
            const y = h - ((value / maxValue) * h);

            if (i === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }

        });

        ctx.stroke();
    }

    line(history.etherRx, "#4da3ff");
    line(history.etherTx, "#ffad4d");

    line(history.sfpRx, "#9c72ff");
    line(history.sfpTx, "#56d38b");
}


async function update() {

    try {

        const response = await fetch("/api/state");

        const data = await response.json();

        const ether = data.ether1 || {};
        const sfp = data.sfp || {};
        const ddm = data.ddm || {};

        document.getElementById("ether-rx").textContent =
            (ether.rx_mbps || 0).toFixed(2);

        document.getElementById("ether-tx").textContent =
            (ether.tx_mbps || 0).toFixed(2);

        document.getElementById("sfp-rx").textContent =
            (sfp.rx_mbps || 0).toFixed(2);

        document.getElementById("sfp-tx").textContent =
            (sfp.tx_mbps || 0).toFixed(2);


        document.getElementById("link").textContent =
            ddm.status || "---";

        document.getElementById("rate").textContent =
            ddm.rate || "---";

        document.getElementById("temp").textContent =
            ddm.temperature_c != null
            ? ddm.temperature_c + " °C"
            : "---";

        document.getElementById("rx-power").textContent =
            ddm.rx_power_dbm != null
            ? ddm.rx_power_dbm.toFixed(3) + " dBm"
            : "---";

        document.getElementById("tx-power").textContent =
            ddm.tx_power_dbm != null
            ? ddm.tx_power_dbm.toFixed(3) + " dBm"
            : "---";


        const status = document.getElementById("status");

        if (data.online) {

            status.textContent = "● ONLINE";
            status.className = "status online";

        } else {

            status.textContent = "● OFFLINE";
            status.className = "status offline";
        }


        document.getElementById("last-update").textContent =
            data.updated_at
            ? "Última muestra: " + data.updated_at
            : "";

        document.getElementById("error").textContent =
            data.error || "";


        push(history.etherRx, ether.rx_mbps || 0);
        push(history.etherTx, ether.tx_mbps || 0);

        push(history.sfpRx, sfp.rx_mbps || 0);
        push(history.sfpTx, sfp.tx_mbps || 0);

        drawChart();

    }

    catch (error) {

        document.getElementById("error").textContent =
            "Frontend: " + error;
    }
}


update();

setInterval(update, 1000);

</script>

</body>
</html>
"""


@app.route("/")
def dashboard():
    return render_template_string(HTML)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    if not MIKROTIK_PASS:
        raise RuntimeError(
            "Define MIKROTIK_PASS antes de iniciar."
        )

    threading.Thread(
        target=traffic_worker,
        daemon=True,
    ).start()

    threading.Thread(
        target=ddm_worker,
        daemon=True,
    ).start()

    print()
    print("MikroTik Collector v0.1")
    print("----------------------")
    print(f"Router: {MIKROTIK_URL}")
    print("Dashboard: http://127.0.0.1:5000")
    print()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
    )