# Core Frontier Investment Portfolio (65% core / 35% trading sleeves)

Builds on `../leaps_vix` (the Frontier plan is the core) and the three strategy decks
(FF / contango-backwardation, Principled Trading Group integrated strategy,
15-minute prediction-market framework).

```bash
python study_ff.py          # Forward Factor SPY calendars, 2007-2026, with cost/exit sensitivity
python study_pm.py          # prediction-model proxy test on BTC 15-minute windows (needs data/btc_*.csv)
python study_portfolio.py   # 65/35 portfolio, stress periods, edge, bootstrap
python build_cf_page.py     # core_frontier.html
```

BTC intraday bars come from Yahoo (`yf.download("BTC-USD", period="60d", interval="5m")`, also 15m and 1h)
and are saved to `data/` (git-ignored).
