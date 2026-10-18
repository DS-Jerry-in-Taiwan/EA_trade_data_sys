"""HTTP instrumentation and Prometheus export routes."""

import time

from flask import Blueprint, request


def install_request_metrics(app, metrics):
    @app.before_request
    def before_request():
        request._start_time = time.time()

    @app.after_request
    def after_request(response):
        elapsed = time.time() - request._start_time
        try:
            metrics.api_requests_total.labels(
                method=request.method, endpoint=request.path or "unknown",
                status=response.status_code,
            ).inc()
            metrics.api_request_duration_seconds.labels(
                method=request.method, endpoint=request.path or "unknown"
            ).observe(elapsed)
        except Exception:
            pass
        return response


def create_blueprint(context, metrics, update_component_metrics, started_at):
    bp = Blueprint("metrics", __name__)

    @bp.get("/metrics")
    @bp.get("/api/v1/metrics")
    def prometheus_metrics():
        metrics.service_uptime_seconds.set(time.time() - started_at)
        update_component_metrics()
        metrics.mt5_connected.set(1 if context.tick_consumer.connected else 0)
        return metrics.generate_latest(), 200, {"Content-Type": "text/plain; charset=utf-8"}

    return bp


__all__ = ["create_blueprint", "install_request_metrics"]
