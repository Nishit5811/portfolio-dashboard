#!/usr/bin/env python3
"""
Builds site/data.json for the portfolio dashboard.

  python build_data.py            # live prices + history from Yahoo Finance (needs `pip install yfinance`)
  python build_data.py --offline  # no internet: uses LTPs from inputs/holdings_snapshot.csv

Inputs
  inputs/<SYMBOL>.xlsx           one broker "Equity Transaction History" export per stock
                                 (file name = symbol; map symbol -> Yahoo ticker in config.json)
  inputs/holdings_snapshot.csv   optional broker holdings export, used only as an offline price fallback

Cost method matches the broker: same-day buys and sells offset first, the rest is FIFO.
Realised P&L excludes charges (as the broker shows it); charges are reported separately.
"""
import argparse
import csv
import datetime as dt
import glob
import json
import math
import os
import sys
from collections import defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------- parsing
def _num(x):
    try:
        v = float(str(x).replace(",", ""))
        return 0.0 if math.isnan(v) else v
    except (TypeError, ValueError):
        return 0.0


def _symbol_from_filename(path):
    # MTNL.xlsx, MTNL_2027-01.xlsx, MTNL-export.xlsx -> MTNL
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.replace("-", "_").split("_")[0].upper()


def _parse_broker_xlsx(path, sym):
    raw = pd.read_excel(path, header=None)
    hdr = raw.index[raw.iloc[:, 0].astype(str).str.strip().str.upper() == "TXN TYPE"]
    if not len(hdr):
        print(f"! skipped {path}: no 'TXN TYPE' header row", file=sys.stderr)
        return []
    df = pd.read_excel(path, header=int(hdr[0]))
    df.columns = [str(c).strip().upper() for c in df.columns]
    value_col = next((c for c in df.columns if c.startswith("VALUE")), None)
    rows = []
    for _, r in df.iterrows():
        ttype = str(r.get("TXN TYPE", "")).strip()
        if not ttype or ttype.lower() == "nan":
            continue
        date = pd.to_datetime(str(r["DATE"]).strip(), format="%d-%b-%Y").date()
        action = str(r.get("ACTION", "")).strip().upper()
        qty = _num(r.get("QTY"))
        price = _num(r.get("TXN PRICE"))
        charges = _num(r.get("STT")) + _num(r.get("BROKERAGE")) + _num(r.get("ADDITIONAL CHARGES"))
        remarks = str(r.get("REMARKS", "") or "")
        remarks = "" if remarks.lower() == "nan" else remarks.strip()
        exch = str(r.get("EXCHANGE", "")).strip()
        if ttype.lower().startswith("corporate") and "dividend" in remarks.lower():
            kind, amount = "DIVIDEND", _num(r.get(value_col)) if value_col else qty * price
        elif action in ("BUY", "SELL"):
            kind, amount = action, qty * price
            if ttype.lower() == "manual":
                remarks = remarks or "Manual entry / transfer-in"
        else:
            continue
        rows.append(dict(date=date, symbol=sym, kind=kind, qty=qty, price=price,
                         amount=round(amount, 2), charges=round(charges, 2),
                         exchange=exch, source=ttype, remarks=remarks))
    return rows


TRADE_COLUMNS = ["date", "symbol", "action", "qty", "price", "charges", "exchange",
                 "ticker", "name", "sector", "notes"]


def load_manual_trades(trades_dir):
    """inputs/trades/*.csv — trades you add yourself (from the dashboard form or by hand).
    action: BUY | SELL | DIVIDEND (for DIVIDEND, price = dividend per share)."""
    rows, meta = [], {}
    for path in sorted(glob.glob(os.path.join(trades_dir, "*.csv"))):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
                if not r.get("date") or not r.get("symbol"):
                    continue
                sym = r["symbol"].upper()
                kind = r.get("action", "").upper()
                if kind in ("DIV", "DIVIDEND"):
                    kind = "DIVIDEND"
                if kind not in ("BUY", "SELL", "DIVIDEND"):
                    print(f"! {path}: unknown action {kind!r}", file=sys.stderr)
                    continue
                qty, price = _num(r.get("qty")), _num(r.get("price"))
                rows.append(dict(date=pd.to_datetime(r["date"], dayfirst=False).date(), symbol=sym,
                                 kind=kind, qty=qty, price=price, amount=round(qty * price, 2),
                                 charges=round(_num(r.get("charges")), 2),
                                 exchange=r.get("exchange", "").upper(), source="Added by you",
                                 remarks=r.get("notes", "")))
                if r.get("ticker"):
                    meta[sym] = {"ticker": r["ticker"], "name": r.get("name") or sym,
                                 "sector": r.get("sector") or "—"}
    return rows, meta


def load_transactions(inputs_dir):
    """Broker exports (inputs/<SYMBOL>*.xlsx) + your own entries (inputs/trades/*.csv).

    - Several exports of the same stock may overlap; identical rows are counted once
      (keeping the highest count seen in any single file, so genuine repeated fills survive).
    - A trade you added by hand is dropped automatically once a broker export
      contains the same stock / date / side with at least that quantity.
    """
    from collections import Counter
    by_sym_files = defaultdict(list)
    for path in sorted(glob.glob(os.path.join(inputs_dir, "*.xlsx"))):
        by_sym_files[_symbol_from_filename(path)].append(path)
    broker = []
    for sym, paths in by_sym_files.items():
        best = Counter()
        sample = {}
        for path in paths:
            c = Counter()
            for row in _parse_broker_xlsx(path, sym):
                key = (row["date"], row["kind"], row["qty"], row["price"], row["charges"])
                c[key] += 1
                sample[key] = row
            for k, n in c.items():
                best[k] = max(best[k], n)
        for k, n in best.items():
            broker += [dict(sample[k]) for _ in range(n)]

    manual, meta = load_manual_trades(os.path.join(inputs_dir, "trades"))
    broker_qty = defaultdict(float)
    for t in broker:
        broker_qty[(t["symbol"], t["date"], t["kind"])] += t["qty"]
    kept = []
    for t in manual:
        k = (t["symbol"], t["date"], t["kind"])
        if broker_qty[k] + 1e-9 >= t["qty"]:
            print(f"  manual {t['kind']} {t['symbol']} {t['date']} already in broker export - skipped")
            broker_qty[k] -= t["qty"]
            continue
        kept.append(t)
    txns = broker + kept
    txns.sort(key=lambda t: (t["date"], t["symbol"], 0 if t["kind"] == "BUY" else 1))
    load_transactions.extra_meta = meta
    return txns


# --------------------------------------------------------------------------- ledger
def run_ledger(txns):
    """Returns per-symbol end-of-day state and realised P&L per sell."""
    by_sym = defaultdict(list)
    for t in txns:
        by_sym[t["symbol"]].append(t)
    states = {}   # sym -> {date: (qty, cost, realised_cum, div_cum)}
    for sym, items in by_sym.items():
        lots, realised, divs = [], 0.0, 0.0
        snap = {}
        days = sorted({t["date"] for t in items})
        for d in days:
            day = [t for t in items if t["date"] == d]
            buys = [[t["qty"], t["price"]] for t in day if t["kind"] == "BUY"]
            for t in day:
                if t["kind"] == "DIVIDEND":
                    divs += t["amount"]
            for t in [t for t in day if t["kind"] == "SELL"]:
                rem, pnl = t["qty"], 0.0
                for pool in (buys, lots):          # same-day first, then FIFO
                    while rem > 1e-9 and pool:
                        take = min(rem, pool[0][0])
                        pnl += take * (t["price"] - pool[0][1])
                        pool[0][0] -= take
                        rem -= take
                        if pool[0][0] <= 1e-9:
                            pool.pop(0)
                if rem > 1e-9:
                    print(f"! {sym} {d}: sold {rem} more than held", file=sys.stderr)
                t["realised"] = round(pnl, 2)
                realised += pnl
            lots += [b for b in buys if b[0] > 1e-9]
            qty = sum(l[0] for l in lots)
            cost = sum(l[0] * l[1] for l in lots)
            snap[d] = (qty, cost, realised, divs)
        states[sym] = snap
    return states


def xirr(flows):
    """flows: list of (date, amount) — negative = money in, positive = money out/terminal value."""
    flows = [(d, a) for d, a in flows if abs(a) > 1e-9]
    if len(flows) < 2 or not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    d0 = min(d for d, _ in flows)
    ts = [((d - d0).days / 365.0, a) for d, a in flows]
    f = lambda r: sum(a / (1 + r) ** t for t, a in ts)
    lo, hi = -0.9999, 10.0
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if flo * fm <= 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


# --------------------------------------------------------------------------- prices
def fetch_yahoo(tickers, start):
    import yfinance as yf
    daily = yf.download(tickers, start=start - dt.timedelta(days=7), interval="1d",
                        auto_adjust=False, progress=False, group_by="ticker", threads=True)
    intraday = yf.download(tickers, period="5d", interval="60m",
                           auto_adjust=False, progress=False, group_by="ticker", threads=True)

    def closes(frame, tk):
        try:
            s = frame[tk]["Close"] if isinstance(frame.columns, pd.MultiIndex) else frame["Close"]
        except KeyError:
            return pd.Series(dtype=float)
        return s.dropna()

    out_daily, out_intra = {}, {}
    for tk in tickers:
        s = closes(daily, tk)
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        out_daily[tk] = s[~s.index.duplicated(keep="last")]
        si = closes(intraday, tk)
        if len(si):
            idx = pd.to_datetime(si.index)
            idx = idx.tz_convert(IST) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(IST)
            si.index = idx
        out_intra[tk] = si
    # live quote: last 1d close is today's running price during market hours
    quotes = {}
    for tk in tickers:
        s = out_daily[tk]
        if len(s) == 0:
            continue
        last_intra = out_intra[tk]
        ltp = float(last_intra.iloc[-1]) if len(last_intra) else float(s.iloc[-1])
        last_day = s.index[-1].date()
        prev = float(s.iloc[-2]) if len(s) > 1 else float(s.iloc[-1])
        quote_time = last_intra.index[-1].isoformat() if len(last_intra) else str(last_day)
        quotes[tk] = dict(ltp=ltp, prev_close=prev, as_of=quote_time, last_day=str(last_day))
    return out_daily, out_intra, quotes


def snapshot_quotes(path, cfg):
    quotes = {}
    if not os.path.exists(path):
        return quotes
    with open(path, newline="") as fh:
        lines = [l for l in fh.read().splitlines() if l.strip()]
    asof = lines[0].split("as on")[-1].strip() if "as on" in lines[0] else ""
    start = next(i for i, l in enumerate(lines) if l.startswith('"SYMBOL"'))
    for row in csv.DictReader(lines[start:]):
        sym = row["SYMBOL"].strip()
        tk = cfg["symbols"].get(sym, {}).get("ticker", sym)
        ltp = _num(row["LTP"])
        chg = _num(row.get("TODAY'S CHG %", row.get("LTP %", 0)))
        quotes[tk] = dict(ltp=ltp, prev_close=ltp / (1 + chg / 100), as_of=asof, last_day=None)
    return quotes


# --------------------------------------------------------------------------- build
def build(offline=False):
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    txns = load_transactions(os.path.join(HERE, "inputs"))
    if not txns:
        sys.exit("No transactions found in inputs/")
    states = run_ledger(txns)
    syms = sorted(states)
    extra = getattr(load_transactions, "extra_meta", {})
    meta = {s: cfg["symbols"].get(s) or extra.get(s) or {"name": s, "ticker": f"{s}.NS", "sector": "—"}
            for s in syms}
    tick = {s: meta[s]["ticker"] for s in syms}
    bench_tk = cfg["benchmark"]["ticker"]
    first = txns[0]["date"]
    now = dt.datetime.now(IST)
    today = now.date()

    daily_px, intra_px, quotes, source = {}, {}, {}, "broker-snapshot"
    snap = snapshot_quotes(os.path.join(HERE, "inputs", "holdings_snapshot.csv"), cfg)
    if not offline:
        try:
            daily_px, intra_px, quotes = fetch_yahoo(list(tick.values()) + [bench_tk], first)
            source = "yahoo"
            # any symbol Yahoo returned nothing for: try its alternate tickers
            for s_ in syms:
                if len(daily_px.get(tick[s_], [])):
                    continue
                for alt in meta[s_].get("alt_tickers", []):
                    try:
                        d2, i2, q2 = fetch_yahoo([alt], first)
                    except Exception as e:
                        print(f"  {s_}: {alt} failed ({e!r})")
                        continue
                    if len(d2.get(alt, [])):
                        print(f"  {s_}: {tick[s_]} had no data, using {alt}")
                        tick[s_] = alt
                        daily_px.update(d2); intra_px.update(i2); quotes.update(q2)
                        break
                else:
                    print(f"! {s_}: no Yahoo data for {tick[s_]} or alternates", file=sys.stderr)
            for s_ in syms:
                print(f"  {s_:10s} {tick[s_]:14s} days={len(daily_px.get(tick[s_], []))} "
                      f"ltp={quotes.get(tick[s_], {}).get('ltp')}")
        except Exception as e:  # network down, yfinance missing, Yahoo hiccup
            print(f"! Yahoo fetch failed ({e!r}); falling back to snapshot", file=sys.stderr)
    if source != "yahoo":
        quotes = snap
    else:
        # last resort for a stock Yahoo can't price: broker snapshot LTP (flagged in the page)
        for s_ in syms:
            primary = meta[s_]["ticker"]
            if tick[s_] not in quotes and primary in snap:
                print(f"! {s_}: using broker snapshot price {snap[primary]['ltp']}", file=sys.stderr)
                quotes[tick[s_]] = dict(snap[primary], fallback=True)

    # ---- date axis
    if source == "yahoo" and len(daily_px.get(bench_tk, [])):
        axis = [d.date() for d in daily_px[bench_tk].index if d.date() >= first]
    else:
        axis = [d.date() for d in pd.bdate_range(first, today)]
    txn_days = {t["date"] for t in txns}
    axis = sorted(set(axis) | {d for d in txn_days if d <= today} | {today})

    def state_on(sym, d):
        snap = states[sym]
        keys = [k for k in snap if k <= d]
        return snap[max(keys)] if keys else (0.0, 0.0, 0.0, 0.0)

    def px_series(tk):
        s = daily_px.get(tk)
        if s is None or not len(s):
            return None
        s = s.copy()
        s.index = [i.date() for i in s.index]
        return s

    series = {"dates": [d.isoformat() for d in axis], "value": [], "cost": [], "net_invested": [],
              "realised": [], "dividends": [], "total_pnl": [], "benchmark": [], "stocks": {}}
    pxs = {s: px_series(tick[s]) for s in syms}
    bench = px_series(bench_tk)

    def price_on(sym, d):
        if d == today and tick[sym] in quotes:
            return quotes[tick[sym]]["ltp"]
        s = pxs[sym]
        if s is None:
            q = quotes.get(tick[sym])
            return q["ltp"] if q and q.get("fallback") else None
        s = s[[i <= d for i in s.index]]
        return float(s.iloc[-1]) if len(s) else None

    # net invested (cash basis, incl. charges)
    flows_by_day = defaultdict(float)
    for t in txns:
        if t["kind"] == "BUY":
            flows_by_day[t["date"]] += t["amount"] + t["charges"]
        elif t["kind"] == "SELL":
            flows_by_day[t["date"]] -= t["amount"] - t["charges"]

    bench_units, net_inv = 0.0, 0.0
    bench_last = None
    for d in axis:
        net_inv += flows_by_day.get(d, 0.0)
        tot_v = tot_c = tot_r = tot_d = 0.0
        have_all = True
        for s in syms:
            q, c, r, dv = state_on(s, d)
            p = price_on(s, d)
            st = series["stocks"].setdefault(s, {"qty": [], "close": [], "cost": [], "booked": []})
            st["qty"].append(round(q, 4))
            st["close"].append(None if p is None else round(p, 4))
            st["cost"].append(round(c, 2))
            st["booked"].append(round(r + dv, 2))
            if q > 0 and p is None:
                have_all = False
            tot_v += q * (p or 0)
            tot_c += c
            tot_r += r
            tot_d += dv
        series["value"].append(round(tot_v, 2) if have_all else None)
        series["cost"].append(round(tot_c, 2))
        series["net_invested"].append(round(net_inv, 2))
        series["realised"].append(round(tot_r, 2))
        series["dividends"].append(round(tot_d, 2))
        series["total_pnl"].append(round(tot_v - tot_c + tot_r + tot_d, 2) if have_all else None)
        # benchmark mirror: same rupee flows into the index
        b = None
        if bench is not None:
            bs = bench[[i <= d for i in bench.index]]
            b = float(bs.iloc[-1]) if len(bs) else bench_last
        if d == today and bench_tk in quotes:
            b = quotes[bench_tk]["ltp"]
        if b:
            bench_units += flows_by_day.get(d, 0.0) / b
            bench_last = b
            series["benchmark"].append(round(bench_units * b, 2))
        else:
            series["benchmark"].append(None)

    # ---- holdings
    holdings = []
    for s in syms:
        q, c, r, dv = states[s][max(states[s])]
        tk = tick[s]
        qt = quotes.get(tk, {})
        ltp = qt.get("ltp")
        prev = qt.get("prev_close")
        sym_tx = [t for t in txns if t["symbol"] == s]
        charges = sum(t["charges"] for t in sym_tx)
        value = q * ltp if ltp else None
        flows = []
        for t in sym_tx:
            if t["kind"] == "BUY":
                flows.append((t["date"], -(t["amount"] + t["charges"])))
            elif t["kind"] == "SELL":
                flows.append((t["date"], t["amount"] - t["charges"]))
            elif t["kind"] == "DIVIDEND":
                flows.append((t["date"], t["amount"]))
        if value:
            flows.append((today, value))
        x = xirr(flows)
        holdings.append(dict(
            symbol=s, name=meta[s]["name"], ticker=tk, sector=meta[s].get("sector", "—"),
            exchange=sorted({t["exchange"] for t in sym_tx if t["exchange"]}),
            qty=q, avg_price=round(c / q, 4) if q else None, invested=round(c, 2),
            ltp=ltp, prev_close=prev, quote_as_of=qt.get("as_of"),
            price_fallback=bool(qt.get("fallback")),
            day_change=(ltp - prev) if ltp and prev else None,
            day_change_pct=((ltp / prev - 1) * 100) if ltp and prev else None,
            day_pnl=(q * (ltp - prev)) if ltp and prev else None,
            value=value,
            unrealised=(value - c) if value is not None else None,
            unrealised_pct=((value / c - 1) * 100) if value and c else None,
            realised=round(r, 2), dividends=round(dv, 2), charges=round(charges, 2),
            total_pnl=((value or 0) - c + r + dv),
            xirr=None if x is None else x * 100,
            first_buy=min(t["date"] for t in sym_tx if t["kind"] == "BUY").isoformat(),
            status="Holding" if q > 0 else "Exited"))

    # ---- portfolio summary
    flows = []
    for t in txns:
        if t["kind"] == "BUY":
            flows.append((t["date"], -(t["amount"] + t["charges"])))
        elif t["kind"] == "SELL":
            flows.append((t["date"], t["amount"] - t["charges"]))
        elif t["kind"] == "DIVIDEND":
            flows.append((t["date"], t["amount"]))
    cur_value = sum(h["value"] or 0 for h in holdings)
    flows.append((today, cur_value))
    px = xirr(flows)
    invested = sum(h["invested"] for h in holdings)
    summary = dict(
        current_value=cur_value, invested=invested,
        unrealised=cur_value - invested,
        unrealised_pct=(cur_value / invested - 1) * 100 if invested else None,
        realised=sum(h["realised"] for h in holdings),
        dividends=sum(h["dividends"] for h in holdings),
        charges=sum(h["charges"] for h in holdings),
        day_pnl=sum(h["day_pnl"] or 0 for h in holdings),
        prev_value=sum(h["qty"] * (h["prev_close"] or 0) for h in holdings),
        xirr=None if px is None else px * 100,
        net_invested=series["net_invested"][-1],
        benchmark_value=series["benchmark"][-1],
    )
    summary["total_pnl"] = summary["unrealised"] + summary["realised"] + summary["dividends"]
    summary["total_pnl_after_charges"] = summary["total_pnl"] - summary["charges"]
    summary["day_pnl_pct"] = summary["day_pnl"] / summary["prev_value"] * 100 if summary["prev_value"] else None

    # ---- intraday portfolio value (current holdings, hourly bars, last 5 sessions)
    intraday = {"ts": [], "value": []}
    if source == "yahoo":
        frames = {s: intra_px[tick[s]] for s in syms if len(intra_px.get(tick[s], []))}
        if frames:
            df = pd.DataFrame(frames).sort_index().ffill().dropna()
            for ts, row in df.iterrows():
                v = 0.0
                for s in frames:
                    q = state_on(s, ts.date())[0]
                    v += q * row[s]
                intraday["ts"].append(ts.isoformat())
                intraday["value"].append(round(v, 2))
        bi = intra_px.get(bench_tk)
        if bi is not None and len(bi):
            intraday["benchmark_ts"] = [t.isoformat() for t in bi.index]
            intraday["benchmark"] = [round(float(v), 2) for v in bi.values]

    out = dict(
        portfolio_name=cfg.get("portfolio_name", "Portfolio"),
        github_repo=cfg.get("github_repo", ""),
        github_branch=cfg.get("github_branch", "main"),
        generated_at=now.isoformat(timespec="seconds"),
        price_source=source,
        benchmark=cfg["benchmark"],
        benchmark_quote=quotes.get(bench_tk),
        summary=summary, holdings=holdings, series=series, intraday=intraday,
        transactions=[{**t, "date": t["date"].isoformat()} for t in txns],
    )
    os.makedirs(os.path.join(HERE, "site"), exist_ok=True)
    path = os.path.join(HERE, "site", "data.json")
    with open(path, "w") as fh:
        json.dump(out, fh, separators=(",", ":"), default=lambda o: None)
    print(f"wrote {path}  source={source}  value=₹{cur_value:,.0f}  holdings={len(holdings)}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip Yahoo; use holdings snapshot prices")
    build(**vars(ap.parse_args()))
