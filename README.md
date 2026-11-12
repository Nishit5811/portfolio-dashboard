# Portfolio Dashboard (live, Yahoo Finance)

A personal InvestRight / Moneycontrol-style dashboard for account 6803948.

- **Overview**: value, today's P&L, unrealised/total P&L, XIRR, a performance chart (value vs invested vs NIFTY 50), allocation, cash flow and P&L by stock
- **Filters**: 1D (hourly intraday), 1W, 1M, 3M, 6M, YTD, 1Y, All, or any **year** and **month**, plus a stock filter
- **Cash flow**: money bought / sold / dividends, monthly or yearly, net invested over time, table
- **Transactions**: full ledger, grouped fills, search, copy as CSV
- **Detailed view per stock** (click any holding): price with your average cost and every buy/sell marked, position value vs invested, quantity history, trades & dividends
- **＋ Add trade / ⇪ Upload export**: record new buys, sells and dividends, or drop in a fresh broker export
- **Live prices:** during market hours (Mon–Fri 09:05–15:45 IST) a job refreshes Yahoo prices every 3 minutes and publishes them to the `live-data` branch; the app checks every minute. Outside market hours the site rebuilds after the close and once a day.

P&L follows your broker's method: same-day buys and sells offset first, the rest is FIFO. Realised P&L excludes charges, which are shown separately. XIRR is after charges.

---

## Option A — Hosted website, refreshed hourly (GitHub, free)

1. Create a new GitHub repository (e.g. `portfolio-dashboard`) and upload everything in this folder.
2. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. In `config.json` set `"github_repo": "<your-username>/portfolio-dashboard"`. This makes the Add-trade button open GitHub pre-filled.
4. **Actions** tab → *Refresh portfolio dashboard* → **Run workflow**. About 2 minutes later your site is live at
   `https://<your-username>.github.io/portfolio-dashboard/`

After that it rebuilds itself on the hourly schedule and whenever you add a trade or an export.

> **Privacy:** GitHub Pages on a free account requires a **public** repo, so anyone with the link (or who finds the repo) can see your holdings. To keep it private, use GitHub Pro (Pages from a private repo) or Option B.

## Option B — Run it on your laptop

```bash
pip install -r requirements.txt
python serve_local.py          # open http://localhost:8050
```

It fetches Yahoo prices at start-up and then every hour (`--every 30` for every 30 min). In this mode **Add trade** and **Upload export** save directly and the page refreshes immediately.

---

## Adding new trades later

**From the dashboard** — click **＋ Add trade**, choose Buy / Sell / Dividend, the stock (or *New stock…* with its Yahoo ticker), date, quantity, price and charges.
- Local mode: saved to `inputs/trades/manual_trades.csv` and the page rebuilds immediately.
- Hosted mode: GitHub opens with a small file pre-filled under `inputs/trades/`. Press **Commit changes** and the site updates in about 2 minutes.

**From a broker export** — download the stock's *Equity Transaction History* .xlsx and either use **⇪ Upload export** or save it in `inputs/` named `SYMBOL_anything.xlsx` (e.g. `MTNL_2027-01.xlsx`). The part before `_` tells the dashboard which stock it is.
- Overlapping rows across exports are counted once.
- A trade you added by hand is skipped automatically once an export contains it (same stock, date, side and quantity), so nothing is double-counted.

**A new stock** needs a Yahoo ticker: NSE → `SYMBOL.NS` (e.g. `TATAPOWER.NS`), BSE-only → `BSECODE.BO` (e.g. `SUNSHIEL.BO`). The Add-trade form stores it, or add it to `config.json` under `symbols`.

## Files

| Path | What |
|---|---|
| `inputs/*.xlsx` | Broker transaction exports, one or more per stock |
| `inputs/trades/*.csv` | Trades you add (`date,symbol,action,qty,price,charges,exchange,ticker,name,sector,notes`) |
| `inputs/holdings_snapshot.csv` | Broker holdings export, used only as an offline price fallback |
| `config.json` | Names, Yahoo tickers, sectors, benchmark, GitHub repo |
| `build_data.py` | Parses everything, pulls Yahoo prices, computes P&L/XIRR/series → `site/data.json` |
| `site/index.html` | The dashboard |
| `serve_local.py` | Local server with hourly refresh and save endpoints |
| `.github/workflows/refresh.yml` | Hourly build and deploy to GitHub Pages |

`python build_data.py --offline` builds without internet, using the snapshot LTPs.

Yahoo Finance quotes for NSE/BSE are typically delayed about 15 minutes, and Yahoo occasionally rate-limits. If a run fails, the previous site stays up and the next hourly run retries. This dashboard is for personal tracking, not investment advice.
