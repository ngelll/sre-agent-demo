"""
web-app-b: Report Center — SRE Agent Scenario B demo.
"""
import os
import time
import uuid
import threading
import logging
from flask import Flask, jsonify, send_from_directory

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.instrumentation.flask import FlaskInstrumentor

APP_NAME = "web-app-b"
APPI_CS = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
POD_NAME = os.environ.get("POD_NAME", "web-app-b-local")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(APP_NAME)

if APPI_CS:
    os.environ.setdefault("OTEL_SERVICE_NAME", APP_NAME)
    os.environ.setdefault("OTEL_RESOURCE_ATTRIBUTES", f"service.name={APP_NAME},service.namespace=sre-demo")
    os.environ.setdefault("OTEL_TRACES_SAMPLER", "always_on")
    os.environ.setdefault("APPLICATIONINSIGHTS_SAMPLING_PERCENTAGE", "100")
    configure_azure_monitor(connection_string=APPI_CS)
    logger.info("Azure Monitor OpenTelemetry configured for %s (pod=%s)", APP_NAME, POD_NAME)
else:
    logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set")

app = Flask(APP_NAME, static_folder="static", static_url_path="")
FlaskInstrumentor().instrument_app(app)

_mem_holder = []
_kube_client = None


def _get_kube_client():
    global _kube_client
    if _kube_client is not None:
        return _kube_client
    try:
        from kubernetes import client, config
        config.load_incluster_config()
        _kube_client = client.CoreV1Api()
        logger.info("in-cluster kube client loaded")
    except Exception as e:
        logger.warning("kube client init failed: %s", e)
        _kube_client = False
    return _kube_client


def _burn_cpu(duration_s=30):
    end = time.time() + duration_s
    x = 0
    while time.time() < end:
        x = (x * 31 + 7) % 1_000_000_007


def _read_own_cpu_percent():
    try:
        with open("/proc/stat") as f:
            fields = f.readline().split()
        cpu_total_1 = sum(int(x) for x in fields[1:])
        with open("/proc/self/stat") as f:
            parts = f.read().split()
        proc_1 = int(parts[13]) + int(parts[14])
        time.sleep(0.3)
        with open("/proc/stat") as f:
            fields = f.readline().split()
        cpu_total_2 = sum(int(x) for x in fields[1:])
        with open("/proc/self/stat") as f:
            parts = f.read().split()
        proc_2 = int(parts[13]) + int(parts[14])
        total_delta = cpu_total_2 - cpu_total_1
        proc_delta = proc_2 - proc_1
        if total_delta <= 0:
            return 0
        pct = (proc_delta / total_delta) * (os.cpu_count() or 1) * 100
        return round(min(max(pct, 0), 100))
    except Exception:
        return 0


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/health")
def health():
    return jsonify(status="healthy", app=APP_NAME, pod=POD_NAME), 200


@app.route("/api/cluster")
def api_cluster():
    pods = [POD_NAME]
    kc = _get_kube_client()
    if kc:
        try:
            resp = kc.list_namespaced_pod(namespace="sre-demo", label_selector="app=web-app-b")
            pods = [p.metadata.name for p in resp.items if p.status.phase == "Running"]
        except Exception as e:
            logger.warning("list pods failed: %s", e)
    return jsonify(
        pods=sorted(pods),
        cpu_percent=_read_own_cpu_percent(),
        this_pod=POD_NAME,
    ), 200


@app.route("/api/report/daily", methods=["POST"])
def api_report_daily():
    report_id = f"RPT-D-{uuid.uuid4().hex[:6].upper()}"
    time.sleep(0.3)
    logger.info("report_generated kind=daily id=%s", report_id)
    return jsonify(report_id=report_id, kind="daily"), 200


@app.route("/api/report/weekly", methods=["POST"])
def api_report_weekly():
    report_id = f"RPT-W-{uuid.uuid4().hex[:6].upper()}"
    time.sleep(1.5)
    logger.info("report_generated kind=weekly id=%s", report_id)
    return jsonify(report_id=report_id, kind="weekly"), 200


@app.route("/api/report/monthly", methods=["POST"])
def api_report_monthly():
    report_id = f"RPT-M-{uuid.uuid4().hex[:6].upper()}"
    logger.error(
        "REPORT_GEN_CPU_SPIKE: monthly aggregation loop stuck in report generator (simulated). report_id=%s pod=%s",
        report_id, POD_NAME,
    )
    for _ in range(2):
        threading.Thread(target=_burn_cpu, args=(30,), daemon=True).start()
    time.sleep(2)
    return jsonify(report_id=report_id, kind="monthly", warning="heavy_cpu_started"), 200


@app.route("/ok")
def ok():
    return jsonify(message="all good", app=APP_NAME), 200


@app.route("/leak")
def leak():
    logger.error("REPORT_GEN_CPU_SPIKE: nightly aggregation loop stuck in report generator (simulated)")
    for _ in range(2):
        threading.Thread(target=_burn_cpu, args=(30,), daemon=True).start()
    return jsonify(error="CpuSpike", code="RPT-9001", app=APP_NAME), 200


@app.route("/memhog")
def memhog():
    logger.warning("MEM_HOG: cache never evicts, holding +200MB")
    _mem_holder.append(bytearray(200 * 1024 * 1024))
    return jsonify(total_hold_mb=len(_mem_holder) * 200), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
