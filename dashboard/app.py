#!/usr/bin/env python3
"""GitHub Organization Metrics Dashboard.

A Flask web application that displays GitHub organization metrics
with a professional dashboard interface.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "github-metrics-dashboard-secret-key-change-me")

WORKSPACE = Path(__file__).resolve().parent.parent
DEFAULT_ORG = "augentic-tech"
DEFAULT_TARGET_REPOS = [
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

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "admin")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def load_csv(filepath: Path) -> list[dict]:
    if not filepath.exists():
        return []
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def load_cache(org: str) -> dict | None:
    cache_file = WORKSPACE / f"{org}_github_data_cache.json"
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)
    return None


def get_metrics_data(org: str) -> dict:
    dev_file = WORKSPACE / f"{org}_github_developer_metrics.csv"
    repo_file = WORKSPACE / f"{org}_github_repository_metrics.csv"
    outlier_file = WORKSPACE / f"{org}_github_outliers.csv"

    developers = load_csv(dev_file)
    repos = load_csv(repo_file)
    outliers = load_csv(outlier_file)

    for dev in developers:
        for key in ["Commits", "Lines Added", "Lines Deleted", "PRs Opened"]:
            if key in dev:
                try:
                    dev[key] = int(dev[key])
                except (ValueError, TypeError):
                    dev[key] = 0
        for key in ["PRs Reviewed", "PR Comments"]:
            if key in dev:
                try:
                    dev[key] = int(dev[key])
                except (ValueError, TypeError):
                    pass

    for repo in repos:
        for key in ["Commits", "PRs", "Deploys", "Branches", "Contributors"]:
            if key in repo:
                try:
                    repo[key] = int(repo[key])
                except (ValueError, TypeError):
                    repo[key] = 0
        for key in ["Lead Time (h)", "Fail %", "Deploy (m)"]:
            if key in repo:
                try:
                    repo[key] = float(repo[key])
                except (ValueError, TypeError):
                    repo[key] = 0.0

    total_commits = sum(r.get("Commits", 0) for r in repos)
    total_prs = sum(r.get("PRs", 0) for r in repos)
    total_deploys = sum(r.get("Deploys", 0) for r in repos)
    total_devs = len(developers)
    total_repos = len(repos)

    lead_times = [r["Lead Time (h)"] for r in repos if r.get("Lead Time (h)", 0) > 0]
    avg_lead_time = sum(lead_times) / len(lead_times) if lead_times else 0

    fail_rates = [r["Fail %"] for r in repos if r.get("Deploys", 0) > 0]
    avg_fail_rate = sum(fail_rates) / len(fail_rates) if fail_rates else 0

    deploy_times = [r["Deploy (m)"] for r in repos if r.get("Deploy (m)", 0) > 0]
    avg_deploy_time = sum(deploy_times) / len(deploy_times) if deploy_times else 0

    return {
        "developers": developers,
        "repos": repos,
        "outliers": outliers,
        "summary": {
            "total_commits": total_commits,
            "total_prs": total_prs,
            "total_deploys": total_deploys,
            "total_devs": total_devs,
            "total_repos": total_repos,
            "avg_lead_time": round(avg_lead_time, 1),
            "avg_fail_rate": round(avg_fail_rate, 1),
            "avg_deploy_time": round(avg_deploy_time, 1),
        },
    }


@app.route("/")
def index():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == DASHBOARD_USER and password == DASHBOARD_PASS:
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("dashboard"))
        flash("Invalid credentials. Please try again.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    org = request.args.get("org", DEFAULT_ORG)
    data = get_metrics_data(org)
    return render_template("dashboard.html", data=data, org=org, username=session.get("username", "admin"))


@app.route("/api/metrics/<org>")
@login_required
def api_metrics(org):
    return jsonify(get_metrics_data(org))


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    org = request.json.get("org", DEFAULT_ORG)
    target_repos = request.json.get("target_repos", DEFAULT_TARGET_REPOS)
    months = request.json.get("months", 3)

    cmd = [
        sys.executable, str(WORKSPACE / "github_metrics.py"),
        org, "--fast", "--months", str(months),
        "--target-repos", *target_repos,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(WORKSPACE))
        if result.returncode == 0:
            return jsonify({"status": "success", "message": "Metrics refreshed successfully"})
        return jsonify({"status": "error", "message": result.stderr or "Unknown error"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "Refresh timed out (10 min limit)"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
