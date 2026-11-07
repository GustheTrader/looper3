# Core Frontier Investment Portfolio (65% core / 35% trading sleeves)

Builds on `../leaps_vix` (the Frontier plan is the core) and the three strategy decks
(FF / contango-backwardation, Principled Trading Group integrated strategy,
15-minute prediction-market framework).

```bash
python study_ff.py          # Forward Factor SPY calendars, 2007-2026, with cost/exit sensitivity
python study_pm.py          # prediction-model proxy test on BTC 15-minute windows (needs data/btc_*.csv)
python study_portfolio.py   # 65/35 portfolio, stress periods, edge, bootstrap
python study_margin.py      # portfolio-margin deck: leverage rules, loss-budget throttle, hedges, margin use
python study_capital_layer.py  # leverage on the trading sleeves only (VIX fade capped)
python study_final_layer.py    # leverage target with the 70% margin cap; spot vs futures crypto
python build_cf_page.py     # core_frontier.html
```

BTC intraday bars come from Yahoo (`yf.download("BTC-USD", period="60d", interval="5m")`, also 15m and 1h)
and are saved to `data/` (git-ignored).

## Perps, FF retest and FF audit (Kadan–Liu–Tang paper)

| Script | What it does |
|---|---|
| `fetch_perps.py` | Deribit (2019-04 →) and Hyperliquid (2023-05 →) hourly perp funding; CBOE single-stock / ETF vol-index histories |
| `study_perps.py` | Crypto via perps vs spot (real funding), perp margin in the 70%-capped leverage layer, liquidation check, funding-carry sleeve, carry replacing FF |
| `study_ff2.py` | FF on SPX with 9/30/93/182-day term points and on 11 stocks/ETFs whose back month is built with the Kadan volatility factor model |
| `audit_ff.py` | Audit: formula vs deck, pricer unit tests, live option chains vs our IV inputs and costs, deck-faithful multi-ETF replay (`python audit_ff.py D` re-runs the replay only) |

Findings: perp funding is pro-cyclical (≈15%/yr over T-bills when the crypto trend is fully on), so core crypto stays spot;
the market-neutral carry sleeve (5.8%/yr 2021–26, −1% DD) replaces the FF slot. The audit found two bugs (bid/ask charged as %
of deep ITM/OTM leg prices; near-zero-debit leverage) and that the earlier FF test was not deck-faithful. Run as written on
SPY/QQQ/IWM/DIA, the deck reproduces March 2020 (+72% before costs, +50% realistic) but adds only ≈ +0.9%/yr over 2011–26.
The Kadan-modelled single-name back month failed validation against real chains. The FF sleeve in `study_portfolio.py` now
uses the deck replay (`FFA_daily.csv`, realistic costs).
