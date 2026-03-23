#!/usr/bin/env python3
"""GitHub Organization Metrics Dashboard.

A Flask web application that displays GitHub organization metrics
with a professional dashboard interface and time-period filtering.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
)

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY", "github-metrics-dashboard-secret-key-change-me"
)

WORKSPACE = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ORG = os.environ.get("GITHUB_ORG", "augentic-tech")
DEFAULT_TARGET_REPOS = os.environ.get(
    "TARGET_REPOS",
    "qomitto-enrollment qomitto-gateway qomitto-data-service "
    "qomitto-search-service qomitto-driver-license-service qomitto-camunda "
    "qomitto-order-service qomitto-report-service qomitto-backoffice",
).split()

DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.environ.get("DASHBOARD_PASS", "admin")

PERIOD_OPTIONS = [
    {"value": 1, "label": "Last Month"},
    {"value": 3, "label": "Last 3 Months"},
    {"value": 6, "label": "Last 6 Months"},
    {"value": 12, "label": "Last Year"},
]


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
        return list(csv.DictReader(f))


def _find_cache(org: str) -> Path | None:
    for base in [WORKSPACE, PROJECT_ROOT]:
        p = base / f"{org}_github_data_cache.json"
        if p.exists():
            return p
    return None


def load_cache(org: str) -> dict | None:
    p = _find_cache(org)
    if p:
        with open(p) as f:
            return json.load(f)
    return None


def parse_gh_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


def analyze_cache(cache_data: dict, months: int = 3) -> dict:
    """Re-analyze the raw cache for a given time window. Instant, no API calls."""
    if not cache_data:
        return _empty_result()

    since = (
        (datetime.now(timezone.utc) - timedelta(days=30 * months))
        .isoformat()
        .replace("+00:00", "Z")
    )
    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    has_pr_details = cache_data.get("fetch_pr_details", False)
    repo_names = [r["name"] for r in cache_data.get("repos", [])]

    devs: dict[str, dict] = {}

    def gd(n):
        if n not in devs:
            devs[n] = {
                "Developer": n,
                "Commits": 0,
                "Lines Added": 0,
                "Lines Deleted": 0,
                "PRs Opened": 0,
                "PRs Reviewed": 0,
                "PR Comments": 0,
                "repos": {},
            }
        return devs[n]

    for rn in repo_names:
        for c in cache_data.get("commits", {}).get(rn, []):
            cd = c.get("commit", {}).get("author", {}).get("date", "")
            if cd >= since:
                login = (c.get("author") or {}).get("login")
                if login and not login.endswith("[bot]"):
                    d = gd(login)
                    d["Commits"] += 1
                    d["repos"][rn] = d["repos"].get(rn, 0) + 1
                    st = cache_data.get("commit_stats", {}).get(rn, {}).get(c["sha"])
                    if st:
                        d["Lines Added"] += st.get("additions", 0)
                        d["Lines Deleted"] += st.get("deletions", 0)

        for pr in cache_data.get("pull_requests", {}).get(rn, []):
            login = (pr.get("user") or {}).get("login")
            if not login or login.endswith("[bot]"):
                continue
            prc = parse_gh_date(pr["created_at"])
            pru = parse_gh_date(pr["updated_at"])
            if prc >= since_dt or pru >= since_dt:
                d = gd(login)
                d["repos"][rn] = d["repos"].get(rn, 0) + 1
                if prc >= since_dt:
                    d["PRs Opened"] += 1

        if has_pr_details:
            for _, reviews in cache_data.get("pr_reviews", {}).get(rn, {}).items():
                for rv in reviews or []:
                    if (
                        rv
                        and rv.get("user", {}).get("login")
                        and rv.get("submitted_at")
                    ):
                        if parse_gh_date(rv["submitted_at"]) >= since_dt:
                            login = rv["user"]["login"]
                            if not login.endswith("[bot]"):
                                gd(login)["PRs Reviewed"] += 1
            for _, comments in cache_data.get("pr_comments", {}).get(rn, {}).items():
                for cm in comments or []:
                    if cm and cm.get("user", {}).get("login") and cm.get("created_at"):
                        if parse_gh_date(cm["created_at"]) >= since_dt:
                            login = cm["user"]["login"]
                            if not login.endswith("[bot]"):
                                gd(login)["PR Comments"] += 1

    developers = []
    for d in devs.values():
        if (
            d["Lines Added"] > 0
            or d["Lines Deleted"] > 0
            or d["PRs Opened"] > 0
            or d["PRs Reviewed"] > 0
            or d["PR Comments"] > 0
        ):
            top = sorted(d["repos"].items(), key=lambda x: x[1], reverse=True)
            d["Repositories"] = ", ".join(r for r, _ in top[:5]) + (
                f" +{len(top) - 5} more" if len(top) > 5 else ""
            )
            del d["repos"]
            developers.append(d)
    developers.sort(key=lambda x: x["Lines Added"], reverse=True)

    repos = _build_repo_metrics(cache_data, since, since_dt, repo_names)

    total_commits = sum(r.get("Commits", 0) for r in repos)
    total_prs = sum(r.get("PRs", 0) for r in repos)
    total_deploys = sum(r.get("Deploys", 0) for r in repos)
    lead_times = [r["Lead Time (h)"] for r in repos if r.get("Lead Time (h)", 0) > 0]
    avg_lt = sum(lead_times) / len(lead_times) if lead_times else 0
    fail_rates = [r["Fail %"] for r in repos if r.get("Deploys", 0) > 0]
    avg_fr = sum(fail_rates) / len(fail_rates) if fail_rates else 0
    deploy_ts = [r["Deploy (m)"] for r in repos if r.get("Deploy (m)", 0) > 0]
    avg_dt = sum(deploy_ts) / len(deploy_ts) if deploy_ts else 0

    return {
        "developers": developers,
        "repos": repos,
        "outliers": [],
        "has_pr_details": has_pr_details,
        "summary": {
            "total_commits": total_commits,
            "total_prs": total_prs,
            "total_deploys": total_deploys,
            "total_devs": len(developers),
            "total_repos": len(repos),
            "avg_lead_time": round(avg_lt, 1),
            "avg_fail_rate": round(avg_fr, 1),
            "avg_deploy_time": round(avg_dt, 1),
        },
    }


def _build_repo_metrics(cache_data, since, since_dt, repo_names):
    from collections import Counter

    repos_out = []
    for repo_info in cache_data.get("repos", []):
        rn = repo_info["name"]
        if rn not in repo_names:
            continue

        activity = 0
        for c in cache_data.get("commits", {}).get(rn, []):
            cd = c.get("commit", {}).get("author", {}).get("date", "")
            if cd >= since:
                activity += 1

        pr_count = 0
        merge_times = []
        for pr in cache_data.get("pull_requests", {}).get(rn, []):
            prc = parse_gh_date(pr["created_at"])
            pru = parse_gh_date(pr["updated_at"])
            if prc >= since_dt or pru >= since_dt:
                pr_count += 1
            if pr.get("merged_at") and prc >= since_dt:
                branch = pr.get("head", {}).get("ref")
                fc = cache_data.get("branch_first_commits", {}).get(rn, {}).get(branch)
                if fc:
                    bsd = fc.get("commit", {}).get("committer", {}).get("date")
                    if bsd:
                        hrs = (
                            parse_gh_date(pr["merged_at"]) - parse_gh_date(bsd)
                        ).total_seconds() / 3600
                        if hrs <= 90 * 24:
                            merge_times.append(hrs)

        dep_count = dep_fail = 0
        dep_dur = []
        wf = cache_data.get("workflow_runs", {}).get(rn)
        if wf and "workflow_runs" in wf:
            names = [r["name"].lower() for r in wf["workflow_runs"] if r.get("name")]
            ci_kw = ("ci", "test", "build", "deploy")
            ci_names = [n for n in names if any(k in n for k in ci_kw)]
            target = (
                Counter(ci_names).most_common(1)[0][0]
                if ci_names
                else (Counter(names).most_common(1)[0][0] if names else None)
            )
            if target:
                for run in wf["workflow_runs"]:
                    if run.get("name", "").lower() != target:
                        continue
                    rd = parse_gh_date(run.get("created_at", ""))
                    if rd < since_dt:
                        continue
                    dep_count += 1
                    if run.get("conclusion") == "failure":
                        dep_fail += 1
                    elif (
                        run.get("conclusion") == "success"
                        and run.get("created_at")
                        and run.get("updated_at")
                    ):
                        dep_dur.append(
                            (
                                parse_gh_date(run["updated_at"])
                                - parse_gh_date(run["created_at"])
                            ).total_seconds()
                            / 60
                        )

        if activity == 0 and pr_count == 0:
            continue

        branches = cache_data.get("branches", {}).get(rn, []) or []
        contribs = cache_data.get("contributors", {}).get(rn, []) or []

        def fmt_date(ds):
            try:
                return parse_gh_date(ds).strftime("%d/%m/%y")
            except Exception:
                return ""

        repos_out.append(
            {
                "Repository": rn,
                "Commits": activity,
                "PRs": pr_count,
                "Lead Time (h)": round(sum(merge_times) / len(merge_times), 1)
                if merge_times
                else 0.0,
                "Deploys": dep_count,
                "Fail %": round(dep_fail / dep_count * 100, 1) if dep_count else 0.0,
                "Deploy (m)": round(sum(dep_dur) / len(dep_dur), 1) if dep_dur else 0.0,
                "Created": fmt_date(repo_info.get("created_at", "")),
                "Updated": fmt_date(repo_info.get("updated_at", "")),
                "Language": repo_info.get("language") or "N/A",
                "Branches": len(branches) if isinstance(branches, list) else 0,
                "Contributors": len(contribs) if isinstance(contribs, list) else 0,
            }
        )
    repos_out.sort(key=lambda x: x["Commits"], reverse=True)
    return repos_out


def _empty_result():
    return {
        "developers": [],
        "repos": [],
        "outliers": [],
        "has_pr_details": False,
        "summary": {
            "total_commits": 0,
            "total_prs": 0,
            "total_deploys": 0,
            "total_devs": 0,
            "total_repos": 0,
            "avg_lead_time": 0,
            "avg_fail_rate": 0,
            "avg_deploy_time": 0,
        },
    }


def compute_repo_developer_metrics(
    cache_data: dict, repo_name: str, months: int = 3
) -> list[dict]:
    if not cache_data:
        return []
    since = (
        (datetime.now(timezone.utc) - timedelta(days=30 * months))
        .isoformat()
        .replace("+00:00", "Z")
    )
    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    devs: dict[str, dict] = {}

    def gd(n):
        if n not in devs:
            devs[n] = {
                "Developer": n,
                "Commits": 0,
                "Lines Added": 0,
                "Lines Deleted": 0,
                "PRs Opened": 0,
                "PRs Reviewed": 0,
                "PR Comments": 0,
            }
        return devs[n]

    for c in cache_data.get("commits", {}).get(repo_name, []):
        cd = c.get("commit", {}).get("author", {}).get("date", "")
        if cd >= since:
            login = (c.get("author") or {}).get("login")
            if login and not login.endswith("[bot]"):
                d = gd(login)
                d["Commits"] += 1
                st = cache_data.get("commit_stats", {}).get(repo_name, {}).get(c["sha"])
                if st:
                    d["Lines Added"] += st.get("additions", 0)
                    d["Lines Deleted"] += st.get("deletions", 0)
    for pr in cache_data.get("pull_requests", {}).get(repo_name, []):
        login = (pr.get("user") or {}).get("login")
        if not login or login.endswith("[bot]"):
            continue
        if parse_gh_date(pr["created_at"]) >= since_dt:
            gd(login)["PRs Opened"] += 1
    for _, reviews in cache_data.get("pr_reviews", {}).get(repo_name, {}).items():
        for rv in reviews or []:
            if rv and rv.get("user", {}).get("login") and rv.get("submitted_at"):
                if parse_gh_date(rv["submitted_at"]) >= since_dt:
                    login = rv["user"]["login"]
                    if not login.endswith("[bot]"):
                        gd(login)["PRs Reviewed"] += 1
    for _, comments in cache_data.get("pr_comments", {}).get(repo_name, {}).items():
        for cm in comments or []:
            if cm and cm.get("user", {}).get("login") and cm.get("created_at"):
                if parse_gh_date(cm["created_at"]) >= since_dt:
                    login = cm["user"]["login"]
                    if not login.endswith("[bot]"):
                        gd(login)["PR Comments"] += 1
    result = [
        d
        for d in devs.values()
        if d["Lines Added"] > 0
        or d["Lines Deleted"] > 0
        or d["PRs Opened"] > 0
        or d["PRs Reviewed"] > 0
        or d["PR Comments"] > 0
    ]
    result.sort(key=lambda x: x["Lines Added"], reverse=True)
    return result


@app.route("/")
def index():
    return (
        redirect(url_for("dashboard"))
        if session.get("logged_in")
        else redirect(url_for("login"))
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (
            request.form.get("username") == DASHBOARD_USER
            and request.form.get("password") == DASHBOARD_PASS
        ):
            session["logged_in"] = True
            session["username"] = request.form["username"]
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    org = request.args.get("org", DEFAULT_ORG)
    months = int(request.args.get("months", 3))
    cache_data = load_cache(org)
    if cache_data:
        data = analyze_cache(cache_data, months)
    else:
        data = _empty_result()
    repo_names = [r.get("Repository", "") for r in data["repos"]]
    return render_template(
        "dashboard.html",
        data=data,
        org=org,
        months=months,
        username=session.get("username", "admin"),
        repo_names=repo_names,
        period_options=PERIOD_OPTIONS,
    )


@app.route("/api/metrics/<org>")
@login_required
def api_metrics(org):
    months = int(request.args.get("months", 3))
    cache_data = load_cache(org)
    return jsonify(analyze_cache(cache_data, months) if cache_data else _empty_result())


@app.route("/api/repo-developers/<org>/<repo_name>")
@login_required
def api_repo_developers(org, repo_name):
    months = int(request.args.get("months", 3))
    cache_data = load_cache(org)
    if not cache_data:
        return jsonify({"developers": [], "has_pr_details": False})
    return jsonify(
        {
            "developers": compute_repo_developer_metrics(cache_data, repo_name, months),
            "has_pr_details": cache_data.get("fetch_pr_details", False),
        }
    )


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    org = request.json.get("org", DEFAULT_ORG)
    target_repos = request.json.get("target_repos", DEFAULT_TARGET_REPOS)
    months = request.json.get("months", 12)
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "github_metrics.py"),
        org,
        "--update-cache",
        "--months",
        str(months),
        "--target-repos",
        *target_repos,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600, cwd=str(PROJECT_ROOT)
        )
        if result.returncode == 0:
            return jsonify({"status": "success", "message": "Metrics refreshed"})
        return jsonify(
            {"status": "error", "message": result.stderr[-500:] or "Unknown error"}
        ), 500
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "Timed out (60 min)"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/collection-info")
@login_required
def api_collection_info():
    org = DEFAULT_ORG
    cache_path = _find_cache(org)
    if not cache_path:
        return jsonify({"last_updated": None, "cache_size": 0})

    st = cache_path.stat()
    return jsonify(
        {
            "last_updated": datetime.fromtimestamp(
                st.st_mtime, tz=timezone.utc
            ).isoformat(),
            "cache_size_mb": round(st.st_size / 1024 / 1024, 1),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
