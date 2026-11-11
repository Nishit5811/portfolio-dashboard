#!/usr/bin/env python3
"""
Run the dashboard on your own computer, with live Yahoo prices refreshed every hour
and the "Add trade" / "Upload export" buttons saving straight into inputs/.

    pip install -r requirements.txt
    python serve_local.py            # then open http://localhost:8050
    python serve_local.py --port 9000 --every 30   # refresh every 30 minutes
"""
import argparse
import csv
import datetime as dt
import os
import re
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

import build_data

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
INPUTS = os.path.join(HERE, "inputs")
TRADES = os.path.join(INPUTS, "trades", "manual_trades.csv")

app = Flask(__name__)
lock = threading.Lock()
state = {"last_build": None, "last_error": None}


def rebuild():
    with lock:
        try:
            build_data.build(offline=False)
            state["last_build"] = dt.datetime.now().isoformat(timespec="seconds")
            state["last_error"] = None
        except SystemExit as e:
            state["last_error"] = str(e)
        except Exception as e:  # keep serving the last good data.json
            state["last_error"] = repr(e)


def scheduler(minutes):
    while True:
        time.sleep(minutes * 60)
        rebuild()


@app.get("/")
def index():
    return send_from_directory(SITE, "index.html")


@app.get("/<path:name>")
def static_files(name):
    resp = send_from_directory(SITE, name)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/info")
def info():
    return jsonify(mode="local", **state)


@app.post("/api/refresh")
def refresh():
    rebuild()
    return jsonify(ok=state["last_error"] is None, **state)


@app.post("/api/trade")
def add_trade():
    t = request.get_json(force=True)
    sym = re.sub(r"[^A-Z0-9&-]", "", str(t.get("symbol", "")).upper())
    action = str(t.get("action", "")).upper()
    try:
        dt.date.fromisoformat(t["date"])
        qty, price = float(t["qty"]), float(t["price"])
        charges = float(t.get("charges") or 0)
    except (KeyError, ValueError):
        return jsonify(ok=False, error="Date, quantity and price are required"), 400
    if not sym or action not in ("BUY", "SELL", "DIVIDEND") or qty <= 0 or price < 0:
        return jsonify(ok=False, error="Check symbol, side, quantity and price"), 400
    os.makedirs(os.path.dirname(TRADES), exist_ok=True)
    new_file = not os.path.exists(TRADES) or os.path.getsize(TRADES) == 0
    with open(TRADES, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(build_data.TRADE_COLUMNS)
        w.writerow([t["date"], sym, action, qty, price, charges, t.get("exchange", ""),
                    t.get("ticker", ""), t.get("name", ""), t.get("sector", ""), t.get("notes", "")])
    rebuild()
    return jsonify(ok=state["last_error"] is None, **state)


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    sym = re.sub(r"[^A-Z0-9&-]", "", request.form.get("symbol", "").upper())
    if not f or not sym or not f.filename.lower().endswith(".xlsx"):
        return jsonify(ok=False, error="Choose the stock and a broker .xlsx export"), 400
    name = f"{sym}_{dt.datetime.now():%Y%m%d-%H%M%S}.xlsx"
    path = os.path.join(INPUTS, name)
    f.save(path)
    if not build_data._parse_broker_xlsx(path, sym):
        os.remove(path)
        return jsonify(ok=False, error="That file doesn't look like a transaction-history export"), 400
    rebuild()
    return jsonify(ok=state["last_error"] is None, saved=name, **state)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--every", type=int, default=60, help="minutes between price refreshes")
    a = ap.parse_args()
    print("Fetching prices…")
    rebuild()
    if state["last_error"]:
        print("! first build failed:", state["last_error"])
    threading.Thread(target=scheduler, args=(a.every,), daemon=True).start()
    print(f"Dashboard: http://localhost:{a.port}")
    app.run(host="127.0.0.1", port=a.port)
