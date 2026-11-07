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


## Carry sleeve in the portfolio, data refresh and number audit

* `study_carry.py` builds the funding-carry sleeve (`CARRY_daily.csv`); `study_portfolio.py` now holds
  65% core / 15% VIX fade / **10% carry** / 5% momentum / 5% prediction, with FF calendars tracked at 0%.
* The carry sleeve's margin (spot crypto, fully funded while on) is charged daily in `study_margin.trading_margin`.
* Leverage in the 70%-capped layer is sized on the positions about to be held (today's weights, yesterday's VIX).
* T-bills: Ken French's daily RF is rounded to 0.01%/day; `momentum/fetch_data.py rf` writes `rf_daily.csv`
  (monthly RF spread over trading days, ^IRX after French's last month). Used by `options_lib` and `study_hard25`.
* End dates follow the data (no hard-coded end).
* `audit_portfolio.py` recomputes every headline number with separate code (`AUDIT_portfolio.json`); the page's
  prose figures are filled from the result files at build time (`[[placeholders]]` in `cf_main.html`).

Re-run order: `fetch_perps.py`, `../momentum/fetch_data.py rf`, `study_carry.py`, `study_ff.py`, `study_ff2.py`,
`audit_ff.py`, `study_portfolio.py`, `study_margin.py`, `study_capital_layer.py`, `study_final_layer.py`,
`study_perps.py`, `build_cf_page.py`, `audit_portfolio.py`, `build_cf_page.py`.
