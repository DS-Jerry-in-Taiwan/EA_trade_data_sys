"""Read-only API authentication for Gateway trade-query routes."""

import hmac
import os

from flask import jsonify, request


def require_readonly_api_key(config_loader):
    config = config_loader()
    if hasattr(config, "api_gateway"):
        env_name = config.api_gateway.readonly_api_key_env
    else:
        cfg = config.get("api_gateway", {})
        env_name = cfg.get("readonly_api_key_env", "READONLY_API_KEY")
    expected_key = os.getenv(env_name)
    if not expected_key:
        return jsonify({"error": "readonly api key not configured"}), 503
    provided_key = request.headers.get("X-API-Key")
    if not provided_key or not hmac.compare_digest(provided_key, expected_key):
        return jsonify({"error": "unauthorized"}), 401
    return None


__all__ = ["require_readonly_api_key"]
