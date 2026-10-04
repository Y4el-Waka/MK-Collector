"use strict";

const INTERFACES = {
  ether1: { stateKey: "ether1", id: "ether1" },
  "sfp-sfpplus1": { stateKey: "sfp", id: "sfp" },
};

const SERIES = [
  { label: "Ether1 RX", interface: "ether1", direction: "rx", color: "#2563eb" },
  { label: "Ether1 TX", interface: "ether1", direction: "tx", color: "#d97706" },
  { label: "SFP RX", interface: "sfp-sfpplus1", direction: "rx", color: "#7c3aed" },
  { label: "SFP TX", interface: "sfp-sfpplus1", direction: "tx", color: "#059669" },
];

let activeWindow = "live";
let latestState = null;
let historyStats = null;
let historyServiceEntry = null;
let chart = null;
let tick = 0;
let historyRequest = 0;

const el = (id) => document.getElementById(id);
const available = (value) => value !== null && value !== undefined && Number.isFinite(Number(value));

function formatRate(value) {
  if (!available(value)) return "N/A";
  const rate = Number(value);
  if (Math.abs(rate) >= 1e9) return `${(rate / 1e9).toFixed(2)} Gbps`;
  if (Math.abs(rate) >= 1e6) return `${(rate / 1e6).toFixed(2)} Mbps`;
  if (Math.abs(rate) >= 1e3) return `${(rate / 1e3).toFixed(1)} Kbps`;
  return `${rate.toFixed(0)} bps`;
}

function formatNumber(value) {
  if (!available(value)) return "N/A";
  return Number(value).toLocaleString("es-MX", { maximumFractionDigits: 0 });
}

function formatMetric(value, unit, digits = 2) {
  return available(value) ? `${Number(value).toFixed(digits)} ${unit}` : "N/A";
}

function sumAvailable(...values) {
  const valid = values.filter(available).map(Number);
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) : null;
}

function formatPercent(value) {
  return available(value) ? `${Number(value).toFixed(2)}%` : "N/A";
}

function formatTimestamp(epochSeconds) {
  if (!available(epochSeconds)) return "N/A";
  return new Date(Number(epochSeconds) * 1000).toLocaleTimeString("es-MX");
}

function parseLinkCapacity(value) {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
  if (typeof value !== "string") return null;
  const match = value.trim().match(/^([0-9]+(?:\.[0-9]+)?)\s*(gbps|gbit\/s|g|mbps|mbit\/s|m|kbps|kbit\/s|k)$/i);
  if (!match) return null;
  const multipliers = { g: 1e9, m: 1e6, k: 1e3 };
  return Number(match[1]) * multipliers[match[2][0].toLowerCase()];
}

function utilization(throughput, capacity) {
  if (!available(throughput) || !available(capacity) || Number(capacity) <= 0) return null;
  return Number(throughput) / Number(capacity) * 100;
}

function qualityFromLive(samples) {
  let errors = 0;
  let drops = 0;
  let hasErrors = false;
  let hasDrops = false;
  let gaps = 0;

  for (const sample of samples || []) {
    const metrics = sample.interfaces && sample.interfaces["sfp-sfpplus1"];
    if (!metrics) {
      gaps += 1;
      continue;
    }
    for (const field of ["rx_errors", "tx_errors"]) {
      if (available(metrics[field])) {
        errors += Number(metrics[field]);
        hasErrors = true;
      }
    }
    for (const field of ["rx_drops", "tx_drops", "queue_drops"]) {
      if (available(metrics[field])) {
        drops += Number(metrics[field]);
        hasDrops = true;
      }
    }
  }

  return {
    errors: hasErrors ? Math.round(errors) : null,
    drops: hasDrops ? Math.round(drops) : null,
    gaps: (samples || []).length ? gaps : null,
  };
}

function observedHistoryTotal(points, fields) {
  let total = 0;
  let hasValues = false;
  for (const point of points || []) {
    if (!available(point.sample_count)) continue;
    for (const field of fields) {
      if (available(point[field])) {
        total += Number(point[field]) * Number(point.sample_count);
        hasValues = true;
      }
    }
  }
  return hasValues ? Math.round(total) : null;
}

function serviceWindowLabel(windowName) {
  return { live: "LIVE 60s", "20m": "20 MIN", "1h": "1 HOUR", "2h": "2 HOURS" }[windowName] || "N/A";
}

function updateServiceSummary(state) {
  const windowLabel = serviceWindowLabel(activeWindow);
  const liveStats = ((state.stats || {})["sfp-sfpplus1"] || {});
  const stats = activeWindow === "live" ? liveStats : ((historyServiceEntry || {}).stats || {});
  const current = activeWindow === "live"
    ? { rx_bps: (state.sfp || {}).rx_bps, tx_bps: (state.sfp || {}).tx_bps }
    : {
        rx_bps: (stats.rx || {}).current_bps,
        tx_bps: (stats.tx || {}).current_bps,
      };
  const quality = activeWindow === "live"
    ? qualityFromLive(state.live || [])
    : {
        errors: observedHistoryTotal((historyServiceEntry || {}).points, ["rx_errors", "tx_errors"]),
        drops: observedHistoryTotal((historyServiceEntry || {}).points, ["rx_drops", "tx_drops", "queue_drops"]),
        gaps: null,
      };
  const rx = stats.rx || {};
  const tx = stats.tx || {};
  const capacity = parseLinkCapacity((state.ddm || {}).rate);

  const rates = {
    "summary-current-rx": current.rx_bps,
    "summary-current-tx": current.tx_bps,
    "summary-avg-rx": rx.avg_bps,
    "summary-avg-tx": tx.avg_bps,
    "summary-peak-rx": rx.max_bps,
    "summary-peak-tx": tx.max_bps,
  };
  Object.entries(rates).forEach(([id, value]) => { el(id).textContent = formatRate(value); });

  el("summary-window").textContent = windowLabel;
  el("summary-window-detail").textContent = windowLabel;
  el("summary-peak-rx-time").textContent = formatTimestamp(rx.max_at);
  el("summary-peak-tx-time").textContent = formatTimestamp(tx.max_at);
  el("summary-errors").textContent = formatNumber(quality.errors);
  el("summary-drops").textContent = formatNumber(quality.drops);
  el("summary-gaps").textContent = formatNumber(quality.gaps);
  el("summary-capacity").textContent = formatRate(capacity);
  el("summary-current-rx-util").textContent = formatPercent(utilization(current.rx_bps, capacity));
  el("summary-current-tx-util").textContent = formatPercent(utilization(current.tx_bps, capacity));
  el("summary-peak-rx-util").textContent = formatPercent(utilization(rx.max_bps, capacity));
  el("summary-peak-tx-util").textContent = formatPercent(utilization(tx.max_bps, capacity));
}

function datasetDefinition(series) {
  return {
    label: series.label,
    data: [],
    borderColor: series.color,
    backgroundColor: series.color,
    pointBackgroundColor: "#ffffff",
    pointBorderColor: series.color,
    pointBorderWidth: 2,
    pointRadius: (context) => context.raw && context.raw.isPeak ? 3 : 0,
    pointHoverRadius: (context) => context.raw && context.raw.isPeak ? 5 : 3,
    pointHitRadius: 10,
    spanGaps: false,
    clip: 6,
  };
}

function initChart() {
  if (typeof Chart === "undefined") {
    el("chart-empty").textContent = "CHART.JS NO DISPONIBLE";
    return;
  }
  const context = el("traffic-chart").getContext("2d");
  chart = new Chart(context, {
    type: "line",
    data: { datasets: SERIES.map(datasetDefinition) },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      normalized: true,
      parsing: false,
      layout: { padding: 0 },
      interaction: { mode: "index", intersect: false, axis: "x" },
      plugins: {
        legend: {
          display: true,
          align: "start",
          labels: {
            color: "#52525b",
            usePointStyle: true,
            pointStyle: "line",
            boxWidth: 18,
            boxHeight: 6,
            padding: 20,
            font: { family: "Inter, ui-sans-serif, system-ui, sans-serif", size: 11, weight: "600" },
          },
          onClick(event, item, legend) {
            const instance = legend.chart;
            const index = item.datasetIndex;
            instance.setDatasetVisibility(index, !instance.isDatasetVisible(index));
            updateChartBounds();
            instance.update("none");
          },
        },
        tooltip: {
          backgroundColor: "#ffffff",
          titleColor: "#18181b",
          bodyColor: "#3f3f46",
          borderColor: "#deded8",
          borderWidth: 1,
          cornerRadius: 12,
          padding: 13,
          caretPadding: 9,
          boxPadding: 5,
          displayColors: true,
          usePointStyle: true,
          titleFont: { family: "Inter, ui-sans-serif, system-ui, sans-serif", size: 12, weight: "700" },
          bodyFont: { family: "Inter, ui-sans-serif, system-ui, sans-serif", size: 11, weight: "600" },
          bodySpacing: 7,
          callbacks: {
            title(items) {
              if (!items.length) return "";
              const peak = items.find((item) => item.raw && item.raw.isPeak);
              const timestamp = peak && peak.raw.peakAt ? peak.raw.peakAt : items[0].parsed.x;
              return new Date(timestamp).toLocaleTimeString("es-MX");
            },
            label(context) {
              const prefix = context.raw && context.raw.isPeak ? "PEAK · " : "";
              return `${prefix}${context.dataset.label}    ${formatRate(context.parsed.y)}`;
            },
            labelColor(context) {
              return {
                borderColor: context.dataset.borderColor,
                backgroundColor: context.dataset.borderColor,
                borderWidth: 0,
              };
            },
          },
        },
      },
      scales: {
        x: {
          type: "linear",
          offset: false,
          bounds: "data",
          grace: 0,
          grid: { color: "rgba(113, 113, 122, .10)", drawTicks: false },
          border: { color: "#e4e4e0" },
          ticks: {
            color: "#71717a",
            maxTicksLimit: 8,
            padding: 9,
            font: { family: "Inter, ui-sans-serif, system-ui, sans-serif", size: 10 },
            callback(value) {
              return new Date(value).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: activeWindow === "live" ? "2-digit" : undefined });
            },
          },
        },
        y: {
          beginAtZero: true,
          min: 0,
          grace: 0,
          grid: { color: "rgba(113, 113, 122, .11)", drawTicks: false },
          border: { display: false },
          ticks: {
            color: "#71717a",
            padding: 10,
            maxTicksLimit: 6,
            font: { family: "Inter, ui-sans-serif, system-ui, sans-serif", size: 10 },
            callback: formatRate,
          },
        },
      },
      elements: { line: { borderWidth: 2, tension: 0.16, cubicInterpolationMode: "monotone" } },
    },
  });
}

function datasetFor(series, points, stats, gapSeconds) {
  const data = [];
  let previous = null;
  for (const point of points) {
    const timestamp = Number(point.timestamp) * 1000;
    const value = point[`${series.direction}_bps`];
    if (previous !== null && timestamp - previous > gapSeconds * 2500) {
      data.push({ x: previous + 1, y: null });
    }
    data.push({ x: timestamp, y: available(value) ? Number(value) : null });
    previous = timestamp;
  }

  const directionStats = stats && stats[series.direction];
  if (directionStats && available(directionStats.max_bps) && available(directionStats.max_at)) {
    const peakX = Number(directionStats.max_at) * 1000;
    const candidates = data.filter((point) => available(point.y));
    if (candidates.length) {
      const peakPoint = candidates.reduce((nearest, point) => (
        Math.abs(point.x - peakX) < Math.abs(nearest.x - peakX) ? point : nearest
      ));
      peakPoint.y = Number(directionStats.max_bps);
      peakPoint.isPeak = true;
      peakPoint.peakAt = peakX;
    }
  }

  return data;
}

function alignedSeriesData(seriesData) {
  const timestamps = [...new Set(seriesData.flatMap((data) => data.map((point) => point.x)))].sort((a, b) => a - b);
  return seriesData.map((data) => {
    const byTimestamp = new Map(data.map((point) => [point.x, point]));
    return timestamps.map((timestamp) => byTimestamp.get(timestamp) || { x: timestamp, y: null });
  });
}

function niceAxisMaximum(maxValue) {
  if (!available(maxValue) || maxValue <= 0) return 1_000_000;
  const target = maxValue * 1.04;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  const normalized = target / magnitude;
  const steps = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  const step = steps.find((candidate) => candidate >= normalized) || 10;
  return step * magnitude;
}

function updateChartBounds() {
  if (!chart) return;
  const visiblePoints = chart.data.datasets.flatMap((dataset, index) => (
    chart.isDatasetVisible(index) ? dataset.data.filter((point) => available(point.y)) : []
  ));
  const xScale = chart.options.scales.x;
  const yScale = chart.options.scales.y;

  const timestamps = [...new Set(visiblePoints.map((point) => point.x))];
  if (timestamps.length >= 2) {
    xScale.min = Math.min(...timestamps);
    xScale.max = Math.max(...timestamps);
  } else {
    delete xScale.min;
    delete xScale.max;
  }

  const values = visiblePoints.map((point) => Number(point.y));
  yScale.max = niceAxisMaximum(values.length ? Math.max(...values) : 0);
}

function setChart(seriesData) {
  if (!chart) return;
  const aligned = alignedSeriesData(seriesData);
  chart.data.datasets.forEach((dataset, index) => {
    dataset.data = aligned[index] || [];
  });
  updateChartBounds();
  chart.update("none");
  const hasValues = aligned.some((data) => data.some((point) => available(point.y)));
  el("chart-empty").style.display = hasValues ? "none" : "grid";
}

function renderLiveChart(state) {
  const live = state.live || [];
  const seriesData = SERIES.map((series) => {
    const points = live.map((sample) => {
      const metrics = (sample.interfaces || {})[series.interface];
      return { timestamp: sample.timestamp, [`${series.direction}_bps`]: metrics ? metrics[`${series.direction}_bps`] : null };
    });
    return datasetFor(series, points, (state.stats || {})[series.interface], 1);
  });
  setChart(seriesData);
}

function renderHistoryChart(payload) {
  const seriesData = SERIES.map((series) => {
    const entry = (payload.interfaces || {})[series.interface] || { points: [], stats: {} };
    return datasetFor(series, entry.points || [], entry.stats || {}, payload.bucket_seconds || 5);
  });
  setChart(seriesData);
}

function updateInterfaceCard(interfaceName, current, stats) {
  const id = INTERFACES[interfaceName].id;
  for (const direction of ["rx", "tx"]) {
    const directionStats = stats && stats[direction] ? stats[direction] : {};
    el(`${id}-${direction}-current`).textContent = formatRate(current && current[`${direction}_bps`]);
    el(`${id}-${direction}-peak`).textContent = formatRate(directionStats.max_bps);
    el(`${id}-${direction}-avg`).textContent = formatRate(directionStats.avg_bps);
  }
  el(`${id}-pps`).textContent = formatNumber(sumAvailable(current && current.rx_pps, current && current.tx_pps));
  el(`${id}-errors`).textContent = formatNumber(sumAvailable(current && current.rx_errors, current && current.tx_errors));
  el(`${id}-drops`).textContent = formatNumber(sumAvailable(current && current.rx_drops, current && current.tx_drops, current && current.queue_drops));
}

function updateCurrentValues(state) {
  const ether = state.ether1 || {};
  const sfp = state.sfp || {};
  const selectedStats = historyStats || state.stats || {};
  updateInterfaceCard("ether1", ether, selectedStats.ether1);
  updateInterfaceCard("sfp-sfpplus1", sfp, selectedStats["sfp-sfpplus1"]);

  const values = {
    "side-ether-rx": ether.rx_bps, "side-ether-tx": ether.tx_bps,
    "side-sfp-rx": sfp.rx_bps, "side-sfp-tx": sfp.tx_bps,
  };
  Object.entries(values).forEach(([id, value]) => { el(id).textContent = formatRate(value); });
}

function updateDdm(ddm) {
  const value = (name, fallback = "N/A") => ddm && ddm[name] !== null && ddm[name] !== undefined && ddm[name] !== "" ? ddm[name] : fallback;
  const status = value("status");
  const statusBadge = el("ddm-state");
  statusBadge.textContent = status;
  statusBadge.className = `neutral-badge ${/up|link-ok|running/i.test(String(status)) ? "positive" : ""}`;
  el("ddm-link").textContent = status;
  el("ddm-rate").textContent = value("rate");
  el("ddm-duplex").textContent = value("full_duplex");
  el("ddm-temp").textContent = formatMetric(ddm && ddm.temperature_c, "°C", 1);
  el("ddm-voltage").textContent = formatMetric(ddm && ddm.voltage_v, "V", 3);
  el("ddm-bias").textContent = formatMetric(ddm && ddm.tx_bias_ma, "mA", 2);
  el("ddm-rx-power").textContent = formatMetric(ddm && ddm.rx_power_dbm, "dBm", 3);
  el("ddm-tx-power").textContent = formatMetric(ddm && ddm.tx_power_dbm, "dBm", 3);
  el("ddm-vendor").textContent = value("vendor");
  el("ddm-model").textContent = value("model");
  el("ddm-wavelength").textContent = value("wavelength");
}

function updateStatus(state) {
  const status = el("status");
  status.className = `status ${state.online ? "online" : "offline"}`;
  status.innerHTML = `<span class="status-dot"></span>${state.online ? "ONLINE" : "OFFLINE"}`;
  el("last-update").textContent = state.updated_at || "N/A";
  el("latency").textContent = available(state.latency_ms) ? `${Number(state.latency_ms).toFixed(1)} ms` : "N/A";
  el("footer-status").textContent = state.online ? "RECOLECCIÓN ACTIVA" : (state.error || "SIN CONEXIÓN");
}

async function loadHistory() {
  if (activeWindow === "live") return;
  const requestId = ++historyRequest;
  try {
    const response = await fetch(`/api/history?window=${encodeURIComponent(activeWindow)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (requestId !== historyRequest || payload.window !== activeWindow) return;
    historyStats = Object.fromEntries(Object.entries(payload.interfaces || {}).map(([name, entry]) => [name, entry.stats]));
    historyServiceEntry = (payload.interfaces || {})["sfp-sfpplus1"] || null;
    renderHistoryChart(payload);
    if (latestState) {
      updateCurrentValues(latestState);
      updateServiceSummary(latestState);
    }
  } catch (error) {
    historyServiceEntry = null;
    if (latestState) updateServiceSummary(latestState);
    el("footer-status").textContent = `HISTÓRICO NO DISPONIBLE · ${error.message}`;
  }
}

async function poll() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    latestState = await response.json();
    updateStatus(latestState);
    updateCurrentValues(latestState);
    updateDdm(latestState.ddm || {});
    updateServiceSummary(latestState);
    if (activeWindow === "live") renderLiveChart(latestState);
    else if (++tick % 5 === 0) loadHistory();
  } catch (error) {
    const status = el("status");
    status.className = "status offline";
    status.innerHTML = '<span class="status-dot"></span>OFFLINE';
    el("footer-status").textContent = `FRONTEND SIN RESPUESTA · ${error.message}`;
  }
}

function selectWindow(windowName) {
  activeWindow = windowName;
  historyStats = null;
  historyServiceEntry = null;
  historyRequest += 1;
  tick = 0;
  document.querySelectorAll(".window-tabs button").forEach((button) => {
    const selected = button.dataset.window === windowName;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  const titles = { live: "Live 60 seconds", "20m": "Historical · 20 minutes", "1h": "Historical · 1 hour", "2h": "Historical · 2 hours" };
  el("window-title").textContent = titles[windowName];
  if (latestState) updateServiceSummary(latestState);
  if (windowName === "live") {
    if (latestState) {
      updateCurrentValues(latestState);
      renderLiveChart(latestState);
    }
  } else {
    loadHistory();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initChart();
  document.querySelectorAll(".window-tabs button").forEach((button) => button.addEventListener("click", () => selectWindow(button.dataset.window)));
  poll();
  window.setInterval(poll, 1000);
});
