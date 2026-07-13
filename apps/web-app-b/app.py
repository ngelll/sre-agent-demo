"""
web-app-b: Flask demo for SRE Agent scenario B (CPU spike / memory leak).
Endpoints:
  /health      -> 200 always
  /ok          -> 200 with normal payload
  /leak        -> Spins CPU at 100% for ~30s (simulates CPU spike -> HPA trigger)
  /memhog      -> Allocates 200MB and holds (simulates memory leak)
"""
import os
import time
import threading
import logging
from flask import Flask, jsonify

from opentelemetry.sdk.resources import Resource
from azure.monitor.opentelemetry import configure_azure_monitor

APP_NAME = "web-app-b"
APPI_CS = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(APP_NAME)

if APPI_CS:
    configure_azure_monitor(
        connection_string=APPI_CS,
        resource=Resource.create({"service.name": APP_NAME, "service.namespace": "sre-demo"}),
    )
    logger.info("Azure Monitor OpenTelemetry configured for %s", APP_NAME)
else:
    logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set")

app = Flask(APP_NAME)

_mem_holder = []


def _burn_cpu(duration_s: int = 30):
    end = time.time() + duration_s
    x = 0
    while time.time() < end:
        # Tight loop -> saturates one core.
        x = (x * 31 + 7) % 1_000_000_007


@app.route("/health")
def health():
    return jsonify(status="healthy", app=APP_NAME), 200


@app.route("/ok")
def ok():
    return jsonify(message="all good", app=APP_NAME), 200


@app.route("/leak")
def leak():
    """Simulates CPU spike (report generator gone wild)."""
    logger.error("REPORT_GEN_CPU_SPIKE: nightly aggregation loop stuck in report generator (simulated)")
    # Launch 2 CPU-burning threads so 1 pod's CPU goes ~100%.
    for _ in range(2):
        threading.Thread(target=_burn_cpu, args=(30,), daemon=True).start()
    return jsonify(
        error="CpuSpike",
        message="Report generator CPU spike started for 30s",
        code="RPT-9001",
        app=APP_NAME,
    ), 200


@app.route("/memhog")
def memhog():
    """Allocates 200MB and holds."""
    logger.warning("MEM_HOG: cache never evicts, holding +200MB")
    _mem_holder.append(bytearray(200 * 1024 * 1024))
    return jsonify(
        message="memory allocated (200MB held)",
        total_hold_mb=len(_mem_holder) * 200,
        app=APP_NAME,
    ), 200


@app.route("/")
def index():
    return jsonify(
        app=APP_NAME,
        endpoints=["/health", "/ok", "/leak", "/memhog"],
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
