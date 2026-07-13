"""
web-app-a: Flask demo for SRE Agent scenario A (5xx error surge).
Endpoints:
  /health      -> 200 always
  /ok          -> 200 with normal payload
  /break       -> 500 always (simulates 5xx surge)
  /crash       -> unhandled exception (simulates code bug)
  /slow        -> 200 after 3s sleep (latency demo)
"""
import os
import time
import logging
from flask import Flask, jsonify

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from azure.monitor.opentelemetry import configure_azure_monitor

APP_NAME = "web-app-a"
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


@app.route("/health")
def health():
    return jsonify(status="healthy", app=APP_NAME), 200


@app.route("/ok")
def ok():
    logger.info("ok endpoint hit")
    return jsonify(message="all good", app=APP_NAME), 200


@app.route("/break")
def broken():
    """Intentional 5xx for SRE Agent scenario A demo."""
    logger.error("ORDER_PROCESSING_FAILED: downstream payment service returned unexpected null response (simulated)")
    return jsonify(
        error="OrderProcessingError",
        message="Payment service returned null in checkout flow",
        code="ORD-5001",
        app=APP_NAME,
    ), 500


@app.route("/crash")
def crash():
    """Unhandled exception path for RCA testing."""
    logger.warning("Attempting to divide by zero in inventory calculator")
    payload = {"stock": 100, "sold": 100, "requested": 5}
    ratio = payload["requested"] / (payload["stock"] - payload["sold"])  # ZeroDivisionError
    return jsonify(ratio=ratio), 200


@app.route("/slow")
def slow():
    time.sleep(3)
    return jsonify(message="slow response", latency_ms=3000, app=APP_NAME), 200


@app.route("/")
def index():
    return jsonify(
        app=APP_NAME,
        endpoints=["/health", "/ok", "/break", "/crash", "/slow"],
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
