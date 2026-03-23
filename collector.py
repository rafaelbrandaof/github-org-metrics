#!/usr/bin/env python3
"""Weekly metrics collector.

Designed to run every Monday via cron. Fetches the last N months of data
from GitHub, updates the cache, and regenerates CSV reports. The cache
file preserves all historical API data so the dashboard can re-analyze
any time window instantly without additional API calls.

Usage:
    python collector.py                     # Default: 12 months, all target repos
    python collector.py --months 6          # Last 6 months
    GITHUB_ORG=my-org python collector.py   # Override organization
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [collector] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))

ORG = os.environ.get("GITHUB_ORG", "augentic-tech")
TARGET_REPOS = os.environ.get(
    "TARGET_REPOS",
    "qomitto-enrollment qomitto-gateway qomitto-data-service "
    "qomitto-search-service qomitto-driver-license-service qomitto-camunda "
    "qomitto-order-service qomitto-report-service qomitto-backoffice",
).split()
MONTHS = int(os.environ.get("COLLECT_MONTHS", "12"))


def run_collection(months: int | None = None) -> bool:
    months = months or MONTHS
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    cache_src = ROOT / f"{ORG}_github_data_cache.json"
    cache_dst = DATA_DIR / f"{ORG}_github_data_cache.json"

    if cache_dst.exists() and not cache_src.exists():
        shutil.copy2(cache_dst, cache_src)
        log.info("Restored cache from data volume")

    cmd = [
        sys.executable,
        str(ROOT / "github_metrics.py"),
        ORG,
        "--update-cache",
        "--months",
        str(months),
        "--target-repos",
        *TARGET_REPOS,
    ]

    log.info(
        "Starting collection: org=%s months=%d repos=%d", ORG, months, len(TARGET_REPOS)
    )
    log.info("Command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd, cwd=str(ROOT), timeout=7200, capture_output=True, text=True
        )
        if result.returncode != 0:
            log.error(
                "Collection failed (exit %d):\n%s",
                result.returncode,
                result.stderr[-2000:],
            )
            return False
        log.info("Collection completed successfully")
    except subprocess.TimeoutExpired:
        log.error("Collection timed out after 2 hours")
        return False

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    for pattern in [f"{ORG}_github_*.csv", f"{ORG}_github_data_cache.json"]:
        for src in ROOT.glob(pattern):
            dst = DATA_DIR / src.name
            shutil.copy2(src, dst)
            log.info("Saved %s -> %s", src.name, dst)

    snapshot_dir = DATA_DIR / "snapshots" / stamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for f in DATA_DIR.glob(f"{ORG}_github_*.csv"):
        shutil.copy2(f, snapshot_dir / f.name)
    log.info("Snapshot saved: %s", snapshot_dir)

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Weekly metrics collector")
    parser.add_argument("--months", type=int, default=MONTHS)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("GITHUB_TOKEN not set")
        sys.exit(1)

    ok = run_collection(args.months)
    sys.exit(0 if ok else 1)
