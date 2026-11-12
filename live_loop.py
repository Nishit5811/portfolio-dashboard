#!/usr/bin/env python3
"""
Market-hours price loop, run by .github/workflows/live.yml.

Every few minutes during the NSE/BSE session it rebuilds data.json with fresh Yahoo
prices and force-pushes it to the `live-data` branch. The dashboard reads that file
straight from raw.githubusercontent.com, so prices update without redeploying the site.

    python live_loop.py                 # loop until the session ends (or ~5h45m)
    python live_loop.py --once          # one publish, then exit
"""
import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time

import build_data

HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BRANCH = "live-data"
PUB = "/tmp/live-pub"
MAX_RUNTIME = 5 * 3600 + 45 * 60          # stay under GitHub's 6-hour job limit
OPEN, CLOSE = dt.time(9, 0), dt.time(15, 45)


def sh(*args, cwd=HERE, check=True):
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True)


def market_window(now):
    return now.weekday() < 5 and OPEN <= now.time() <= CLOSE


def publish(path):
    """Force-push data.json as a single commit on the live-data branch (no history build-up)."""
    remote = sh("git", "remote", "get-url", "origin").stdout.strip()
    header_key = "http.https://github.com/.extraheader"
    header = sh("git", "config", "--get", header_key, check=False).stdout.strip()
    shutil.rmtree(PUB, ignore_errors=True)
    os.makedirs(PUB)
    shutil.copy(path, os.path.join(PUB, "data.json"))
    sh("git", "init", "-q", "-b", BRANCH, cwd=PUB)
    if header:
        sh("git", "config", header_key, header, cwd=PUB)
    sh("git", "config", "user.name", "portfolio-bot", cwd=PUB)
    sh("git", "config", "user.email", "actions@users.noreply.github.com", cwd=PUB)
    sh("git", "add", "data.json", cwd=PUB)
    sh("git", "commit", "-q", "-m", f"prices {dt.datetime.now(IST):%Y-%m-%d %H:%M} IST", cwd=PUB)
    sh("git", "push", "-q", "-f", remote, f"HEAD:{BRANCH}", cwd=PUB)


def pull_main():
    """Pick up trades added while the loop is running."""
    r = sh("git", "pull", "-q", "--ff-only", "origin", "main", check=False)
    if r.returncode:
        print("  (pull skipped:", r.stderr.strip()[:120], ")")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=3, help="minutes between refreshes")
    a = ap.parse_args()
    start = time.time()
    n = 0
    while True:
        now = dt.datetime.now(IST)
        t0 = time.time()
        try:
            pull_main()
            out = build_data.build(offline=False)
            if out.get("price_source") == "yahoo":
                publish(os.path.join(HERE, "site", "data.json"))
                n += 1
                print(f"{now:%H:%M:%S} IST  published #{n}  value=₹{out['summary']['current_value']:,.0f}  "
                      f"({time.time() - t0:.0f}s)", flush=True)
            else:
                print(f"{now:%H:%M:%S} IST  Yahoo unavailable, not publishing", flush=True)
        except Exception as e:  # keep looping; the next round usually recovers
            print(f"{now:%H:%M:%S} IST  error: {e!r}", file=sys.stderr, flush=True)
        if a.once or not market_window(dt.datetime.now(IST)) or time.time() - start > MAX_RUNTIME:
            break
        step = a.every * 60
        time.sleep(max(20, step - (time.time() % step)))   # align to the next N-minute mark
    print(f"done: {n} publishes")


if __name__ == "__main__":
    main()
