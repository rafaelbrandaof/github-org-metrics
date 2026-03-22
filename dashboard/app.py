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
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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


def parse_github_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def compute_repo_developer_metrics(cache_data: dict, repo_name: str, months: int = 3) -> list[dict]:
    """Compute per-developer metrics for a specific repository from cache data."""
    if not cache_data:
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=30 * months)).isoformat().replace("+00:00", "Z")
    since_date = datetime.fromisoformat(since.replace("Z", "+00:00"))

    developers: dict[str, dict] = {}

    def get_dev(name: str) -> dict:
        if name not in developers:
            developers[name] = {
                "Developer": name,
                "Commits": 0,
                "Lines Added": 0,
                "Lines Deleted": 0,
                "PRs Opened": 0,
                "PRs Reviewed": 0,
                "PR Comments": 0,
            }
        return developers[name]

    for commit in cache_data.get("commits", {}).get(repo_name, []):
        commit_date = commit.get("commit", {}).get("author", {}).get("date", "")
        if commit_date >= since:
            author_data = commit.get("author") or {}
            login = author_data.get("login")
            if login and not login.endswith("[bot]"):
                dev = get_dev(login)
                dev["Commits"] += 1
                stats = cache_data.get("commit_stats", {}).get(repo_name, {}).get(commit["sha"])
                if stats:
                    dev["Lines Added"] += stats.get("additions", 0)
                    dev["Lines Deleted"] += stats.get("deletions", 0)

    for pr in cache_data.get("pull_requests", {}).get(repo_name, []):
        user = pr.get("user") or {}
        login = user.get("login")
        if not login or login.endswith("[bot]"):
            continue
        pr_created = parse_github_date(pr["created_at"])
        if pr_created >= since_date:
            dev = get_dev(login)
            dev["PRs Opened"] += 1

    for pr_number, reviews in cache_data.get("pr_reviews", {}).get(repo_name, {}).items():
        for review in (reviews or []):
            if review and review.get("user", {}).get("login") and review.get("submitted_at"):
                review_date = parse_github_date(review["submitted_at"])
                if review_date >= since_date:
                    login = review["user"]["login"]
                    if not login.endswith("[bot]"):
                        dev = get_dev(login)
                        dev["PRs Reviewed"] += 1

    for pr_number, comments in cache_data.get("pr_comments", {}).get(repo_name, {}).items():
        for comment in (comments or []):
            if comment and comment.get("user", {}).get("login") and comment.get("created_at"):
                comment_date = parse_github_date(comment["created_at"])
                if comment_date >= since_date:
                    login = comment["user"]["login"]
                    if not login.endswith("[bot]"):
                        dev = get_dev(login)
                        dev["PR Comments"] += 1

    result = [d for d in developers.values()
              if d["Lines Added"] > 0 or d["Lines Deleted"] > 0 or d["PRs Opened"] > 0
              or d["PRs Reviewed"] > 0 or d["PR Comments"] > 0]
    result.sort(key=lambda x: x["Lines Added"], reverse=True)
    return result


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

    total_lines_added = sum(d.get("Lines Added", 0) for d in developers if isinstance(d.get("Lines Added"), int))
    total_lines_deleted = sum(d.get("Lines Deleted", 0) for d in developers if isinstance(d.get("Lines Deleted"), int))

    cache_data = load_cache(org)
    has_pr_details = cache_data.get("fetch_pr_details", False) if cache_data else False

    return {
        "developers": developers,
        "repos": repos,
        "outliers": outliers,
        "has_pr_details": has_pr_details,
        "summary": {
            "total_commits": total_commits,
            "total_prs": total_prs,
            "total_deploys": total_deploys,
            "total_devs": total_devs,
            "total_repos": total_repos,
            "avg_lead_time": round(avg_lead_time, 1),
            "avg_fail_rate": round(avg_fail_rate, 1),
            "avg_deploy_time": round(avg_deploy_time, 1),
            "total_lines_added": total_lines_added,
            "total_lines_deleted": total_lines_deleted,
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
    repo_names = [r.get("Repository", "") for r in data["repos"]]
    return render_template("dashboard.html", data=data, org=org,
                           username=session.get("username", "admin"),
                           repo_names=repo_names)


@app.route("/api/metrics/<org>")
@login_required
def api_metrics(org):
    return jsonify(get_metrics_data(org))


@app.route("/api/repo-developers/<org>/<repo_name>")
@login_required
def api_repo_developers(org, repo_name):
    cache_data = load_cache(org)
    if not cache_data:
        return jsonify({"developers": [], "has_pr_details": False})
    devs = compute_repo_developer_metrics(cache_data, repo_name)
    has_details = cache_data.get("fetch_pr_details", False)
    return jsonify({"developers": devs, "has_pr_details": has_details})


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    org = request.json.get("org", DEFAULT_ORG)
    target_repos = request.json.get("target_repos", DEFAULT_TARGET_REPOS)
    months = request.json.get("months", 3)

    cmd = [
        sys.executable, str(WORKSPACE / "github_metrics.py"),
        org, "--update-cache", "--months", str(months),
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
