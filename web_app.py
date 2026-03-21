#!/usr/bin/env python3
"""Flask web dashboard for GitHub organization metrics."""

from __future__ import annotations

import os
import secrets
from functools import wraps
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from github_metrics import get_metrics_for_dashboard

DEFAULT_ORG = "augentic-tech"
DEFAULT_TARGET_REPOS: list[str] = [
    "qomitto-enrollment",
    "qomitto-gateway",
    "qomitto-data-service",
    "qomitto-search-service",
    "qomitto-driver-license-service",
    "qomitto-camunda",
    "qomitto-order-service",
    "qomitto-report-service",
    "qomitto-backoffice",
]


def _parse_target_repos() -> list[str]:
    raw = os.environ.get("GITHUB_TARGET_REPOS", "").strip()
    if not raw:
        return list(DEFAULT_TARGET_REPOS)
    if "," in raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return [p for p in raw.split() if p]


def _parse_months() -> int:
    try:
        return max(1, int(os.environ.get("GITHUB_MONTHS", "3")))
    except ValueError:
        return 3


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

    @app.context_processor
    def inject_config() -> dict[str, Any]:
        return {
            "dashboard_org": os.environ.get("GITHUB_ORG", DEFAULT_ORG),
            "dashboard_repos": _parse_target_repos(),
        }

    def require_login(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    @app.route("/")
    def index():
        if session.get("authenticated"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error: str | None = None
        password_env = os.environ.get("DASHBOARD_PASSWORD")
        if not password_env:
            error = (
                "Server misconfiguration: set DASHBOARD_PASSWORD in the environment "
                "(Replit Secrets)."
            )

        if request.method == "POST" and password_env:
            password = request.form.get("password", "")
            if secrets.compare_digest(password, password_env):
                session["authenticated"] = True
                session.permanent = True
                nxt = request.args.get("next") or url_for("dashboard")
                if nxt.startswith("/"):
                    return redirect(nxt)
                return redirect(url_for("dashboard"))
            error = "Invalid password."

        return render_template("login.html", error=error)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @require_login
    def dashboard():
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return render_template(
                "dashboard.html",
                metrics=None,
                error="Set GITHUB_TOKEN in the environment (Replit Secrets) to load data.",
            )
        return render_template("dashboard.html", metrics=None, error=None)

    @app.route("/api/metrics")
    @require_login
    def api_metrics():
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return jsonify({"error": "GITHUB_TOKEN is not set."}), 503
        org = os.environ.get("GITHUB_ORG", DEFAULT_ORG)
        months = _parse_months()
        target = _parse_target_repos()
        use_cache = request.args.get("use_cache", "1") not in ("0", "false", "False")
        update_cache = request.args.get("update_cache", "0") in ("1", "true", "True")
        fast = request.args.get("fast", "1") not in ("0", "false", "False")

        try:
            payload = get_metrics_for_dashboard(
                org,
                months,
                token,
                target_repos=target,
                use_cache=use_cache,
                update_cache=update_cache,
                fetch_pr_details=not fast,
                anonymize=False,
            )
        except Exception as e:  # noqa: BLE001 — surface errors to the UI
            return jsonify({"error": str(e)}), 500
        return jsonify(payload)

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
