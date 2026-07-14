"""
web-app-a: Demo shop backend + SRE Agent demo endpoints.
"""
import os
import time
import uuid
import logging
from flask import Flask, jsonify, request, send_from_directory

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.instrumentation.flask import FlaskInstrumentor

APP_NAME = "web-app-a"
APPI_CS = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

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
    logger.info("Azure Monitor OpenTelemetry configured for %s", APP_NAME)
else:
    logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set")

app = Flask(APP_NAME, static_folder="static", static_url_path="")
FlaskInstrumentor().instrument_app(app)

# ============================================================
# In-memory state (single-worker only, that's why --preload -w 1)
# ============================================================
STATE = {
    "payment_healthy": True,   # controls whether /api/checkout succeeds
    "orders": [],
}

# ============================================================
# Shop UI (static)
# ============================================================
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ============================================================
# Kubernetes probes
# ============================================================
@app.route("/health")
def health():
    """K8s liveness/readiness — always healthy so pod stays alive during demo."""
    return jsonify(status="healthy", app=APP_NAME), 200


# ============================================================
# Demo shop API
# ============================================================
@app.route("/api/status")
def api_status():
    return jsonify(
        app=APP_NAME,
        payment_healthy=STATE["payment_healthy"],
        total_orders=len(STATE["orders"]),
    ), 200


@app.route("/api/checkout", methods=["POST"])
def api_checkout():
    """The star of the demo. Fails when payment service is 'unhealthy'."""
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify(error="EmptyCart", message="购物车为空", code="ORD-4001"), 400

    if not STATE["payment_healthy"]:
        logger.error(
            "ORDER_PROCESSING_FAILED: downstream payment service returned unexpected null response (simulated). items=%s",
            items,
        )
        return jsonify(
            error="OrderProcessingError",
            message="Payment service returned null in checkout flow",
            code="ORD-5001",
            app=APP_NAME,
        ), 500

    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    STATE["orders"].append({"id": order_id, "items": items, "ts": time.time()})
    item_count = sum(i.get("qty", 1) for i in items)
    logger.info("order_placed order_id=%s item_count=%d", order_id, item_count)
    return jsonify(status="ok", order_id=order_id, item_count=item_count, app=APP_NAME), 200


@app.route("/api/admin/payment", methods=["POST", "GET"])
def api_admin_payment():
    """Toggle payment service health. Called by:
      - The demo shop's Ops console (manual)
      - The SRE Agent as its 'mitigation action' (automated)
    """
    if request.method == "GET":
        return jsonify(payment_healthy=STATE["payment_healthy"]), 200
    data = request.get_json(silent=True) or {}
    healthy = bool(data.get("healthy", True))
    STATE["payment_healthy"] = healthy
    logger.warning("PAYMENT_SERVICE_STATE_CHANGE new_state=%s", "healthy" if healthy else "down")
    return jsonify(payment_healthy=STATE["payment_healthy"], changed=True), 200


# ============================================================
# Legacy compat endpoints (kept so existing curl scripts work)
# ============================================================
@app.route("/ok")
def ok():
    return jsonify(message="all good", app=APP_NAME), 200


@app.route("/break")
def broken():
    """Always fail. Kept for existing demo scripts."""
    logger.error("ORDER_PROCESSING_FAILED: downstream payment service returned unexpected null response (simulated)")
    return jsonify(
        error="OrderProcessingError",
        message="Payment service returned null in checkout flow",
        code="ORD-5001",
        app=APP_NAME,
    ), 500


@app.route("/crash")
def crash():
    payload = {"stock": 100, "sold": 100, "requested": 5}
    ratio = payload["requested"] / (payload["stock"] - payload["sold"])
    return jsonify(ratio=ratio), 200


@app.route("/slow")
def slow():
    time.sleep(3)
    return jsonify(message="slow response", latency_ms=3000, app=APP_NAME), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
