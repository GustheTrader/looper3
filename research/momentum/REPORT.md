# Momentum Research: Market-Wide Long/Short and Sector-Concentrated Momentum

*30-year study (Oct 1996 to Sep/Oct 2026), monthly rebalancing, all results net of trading costs.*

---

## TL;DR: the answers

| Question | Answer |
|---|---|
| **Best rolling window for sector ranking** | **9 months**, inside a stable 6–12 month plateau. All three sector universes peak at 9 (chart 1). Windows of 1–3 months and 18–24 months are clearly worse. |
| **Best rolling window for stock momentum** | **6 months, skipping the latest month (6-1)**, held about 3–6 months. The textbook 12-1 window has *not* worked among large caps since 1996 (chart 2). |
| **Does it hold on data never used to pick it?** | **Yes.** On 1931–1996, 65 years the study never looked at, sector momentum again peaks at 9–12 months in all four industry tests, and SPSR again beats raw relative strength in all four (section 4b, chart 6). |
| **Would weekly trading or day/week windows do better?** | **No.** With weekly rebalancing, lookbacks of 1–8 weeks lose money after costs. The best is again 39–52 weeks (9–12 months). Monthly rebalancing is as good or better, and cheaper (chart 7). |
| **Best way to *choose* the window** | **Don't optimise it every year.** A fixed blend of 6, 9 and 12 months beat walk-forward "pick the best window" in 8 of 9 strategy families. Re-optimising mostly chases noise (chart 3). |
| **Does a Sector Power Strength Rating beat plain relative strength?** | **Yes.** The composite SPSR (return + risk-adjusted return + consistency) had a higher average Sharpe than raw return ranking in every universe: 0.17 vs 0.07 (12 industries), 0.31 vs 0.22 (49 industries), 0.05 vs −0.02 (ETFs). |
| **Does sector concentration help stock momentum?** | **Yes.** Buying only winners inside the 3 strongest sectors and shorting only losers inside the 3 weakest beat plain stock momentum in 21 of 24 configurations. 12-1 Sharpe rose from 0.01 to about 0.13, and 6-1 from 0.16 to about 0.20. |
| **The single biggest improvement** | **Crash guards**, not window tuning: volatility targeting plus standing aside after a 24-month market decline. Stock long/short went from Sharpe −0.04 to **0.35** and max drawdown from −86% to **−41%**. Sector long/short (49 industries) went from 0.26 to **0.50**. |
| **Whole-market long/short momentum, honestly measured** | Weak but positive. The survivorship-free CRSP winners-minus-losers portfolio earned 8.7%/yr at 32% volatility (Sharpe 0.27, t = 1.5) with an **−81% drawdown** (2009). It is not something to trade unguarded. |
| **Free data route** | Ken French Data Library (CRSP, free, no survivorship bias) plus Yahoo Finance via `yfinance`. MCP options are listed in section 6. |

> **Not investment advice.** These are historical backtests with real limitations (section 7). The live scan in section 5 is a research output, not a recommendation.

---

## 1. What was tested

### Concept A: long/short momentum across the entire market
* **A1. Survivorship-free benchmark.** Ken French's decile portfolios built from *every* NYSE/AMEX/NASDAQ stock (CRSP), including companies that later went bankrupt. Winners-minus-Losers (WML) = top decile − bottom decile, for three windows: 1-0 (last month), 12-2 (classic) and 60-13 (5 years).
* **A2. Time-series momentum on the market itself.** Be long the US market when its own trailing L-month excess return is positive, otherwise hold cash (or go short).
* **A3. Stock window scan.** Current S&P 500 members, top decile vs bottom decile, over a grid of **10 lookbacks × 2 skips × 4 holding periods = 80 windows**.

### Concept B: sector concentration driven by a Sector Power Strength Rating
**SPSR (0–100)** is computed for every sector each month from three ingredients over the same window:
1. **Relative strength**: the sector's return minus the market's.
2. **Risk-adjusted strength**: average monthly excess return divided by its volatility. This penalises lucky, jumpy sectors.
3. **Consistency**: the share of months the sector beat the market. Steady leadership persists better than one big jump (the "frog-in-the-pan" effect).

Each ingredient is z-scored across sectors, the three are averaged, and the result is turned into a percentile rank. Tested on:
* **French 12 industries** and **French 49 industries** (CRSP, survivorship-free), Oct 1996 to Sep 2026.
* **SPDR sector ETFs** (XLB…XLY, plus XLRE and XLC once they exist): real tradable funds, Jan 2001 to Oct 2026.
* **B2: sector-concentrated stock momentum.** S&P 500 stocks, long the top-momentum names only inside the top-k SPSR sectors and short the bottom names only inside the bottom-k sectors.

Grid per universe: 2 signals (RS vs SPSR) × 10 lookbacks × 2 skips × 3 holds × 3 portfolio sizes × 2 modes (long/short, long-only) = **360 configurations**.

### How "best window" was judged (overfitting control)
Testing hundreds of windows on one history guarantees that *something* looks great by luck. So every family was judged five ways:

| Method | What it does |
|---|---|
| `in_sample_best` | Best cell over the whole sample. **An overfit upper bound**, shown only to measure the optimism gap. |
| `wf_grid` | Walk-forward: each year, pick the best cell on the trailing 10 years, then trade it the next year. |
| `wf_lookback` | Walk-forward over the lookback only, with other settings fixed in advance. |
| `fixed_12` | The textbook 12-month window, chosen before seeing any data. |
| `ensemble` | No selection at all: equal capital in the 6-, 9- and 12-month versions. |
| `ens_guarded` | Ensemble + volatility targeting (12%/yr, max 2× leverage) + stand aside when the market's 24-month return is negative. **Both guards were taken from published papers, not tuned here.** |

Also reported: first-half vs second-half rank stability, and the **Deflated Sharpe Ratio** (Bailey & López de Prado 2014), the probability that the best of N tries is real after accounting for the number of tries.

Trading costs: 10 bps one-way for stocks and industries, 5 bps for ETFs, 2 bps for market timing.

---

## 2. Concept A results: whole-market long/short momentum

### A1. Survivorship-free CRSP benchmark (Oct 1996 to Sep 2026)

| Portfolio | Ann. return | Vol | Sharpe | t-stat | Max DD |
|---|---|---|---|---|---|
| **WML 12-2** (top − bottom decile) | 8.7% | 31.8% | 0.27 | 1.50 | **−81%** |
| UMD factor (Fama-French momentum) | 4.2% | 17.4% | 0.24 | 1.33 | −58% |
| WML 1-0 (last month only) | −0.5% | 25.3% | −0.02 | −0.11 | −79% |
| WML 60-13 (5-year) | −0.1% | 20.7% | −0.00 | −0.02 | −81% |
| US market buy & hold | 11.2% | 15.8% | 0.56 | 3.09 | −50% |

By decade, WML 12-2 Sharpe was 0.41 (1996–2006), 0.16 (2006–2016) and 0.25 (2016–2026).

**Reading:** the intermediate window (about one year) is the *only* one of the three French windows that earns anything. Very short windows (1 month) reverse, and very long windows (5 years) reverse. Even the intermediate window is weak and crash-prone over the last 30 years, much weaker than its famous 1927–1993 record.

### A2. Market time-series momentum (long market / cash)
* Every lookback from 9 to 24 months beat buy & hold on Sharpe (0.62–0.72 vs 0.56; best: 9-1 at 0.72), mostly by cutting drawdowns from −50% to about −20–25%. Shorter lookbacks were hit-or-miss.
* Long/**short** (instead of long/cash) is much worse (Sharpe 0.04–0.55), because shorting a market that drifts upward is costly.
* Out of sample (2001–2026): fixed 12-month Sharpe 0.68, ensemble 0.74, walk-forward grid 0.54. Buy & hold was 0.64 with a −50% drawdown, vs −20% for the trend rule.

### A3. Stock window scan (S&P 500 members), chart 2
* The best cells cluster at **lookback 4–9 months, skip 1, hold 3–6** (Sharpe 0.10–0.13 net).
* **Long lookbacks (15–24 months) are reliably negative** (long-term reversal), and holding 12 months hurts.
* The best single cell (6-1, hold 6) has a **Deflated Sharpe probability of 0.18**: statistically indistinguishable from luck after 80 tries.
* **Survivorship-bias calibration:** the S&P 500 survivors' 12-1 strategy correlates 0.84 with the CRSP WML, but its Sharpe is −0.02 vs 0.27. Using only today's survivors removes the losers that actually went bust, and that kills the short leg. Absolute stock-level numbers in this study are therefore **too pessimistic for the short side**. Relative comparisons (window A vs window B, plain vs concentrated) are still informative.

---

## 3. Concept B results: sector concentration via SPSR

### B1. The lookback plateau (chart 1)
Average net Sharpe of sector long/short, across all other settings, by lookback:

| Lookback (months) | 1 | 2 | 3 | 4 | 6 | **9** | 12 | 15 | 18 | 24 |
|---|---|---|---|---|---|---|---|---|---|---|
| French 12 industries | −0.01 | 0.06 | 0.10 | 0.15 | 0.18 | **0.27** | 0.23 | 0.12 | 0.06 | 0.02 |
| French 49 industries | 0.22 | 0.26 | 0.25 | 0.30 | 0.34 | **0.35** | 0.32 | 0.25 | 0.20 | 0.14 |
| SPDR sector ETFs | −0.14 | −0.09 | −0.01 | −0.01 | 0.07 | **0.14** | 0.12 | 0.07 | 0.04 | −0.04 |

The same shape appears in long-only mode, and walk-forward selection over lookbacks picked **9 months more often than any other** in 5 of the 9 families (no other window led more than one). This is the most robust finding of the study: **rank sectors on roughly 9 months (anywhere from 6 to 12 is fine).**

### B1. Concrete configuration: SPSR, 9-month window, skip 1, hold 3
| Universe | Mode | Holdings | Ann. return | CAGR | Vol | Sharpe | Max DD | Benchmark |
|---|---|---|---|---|---|---|---|---|
| French 12 ind. | long-only | top 2 | 14.9% | 14.2% | 17.9% | 0.71 | −41% | Market 10.4% CAGR, Sharpe 0.56, DD −50% |
| French 12 ind. | long/short | 2 vs 2 | 6.3% | 4.9% | 17.0% | 0.37 | −41% | |
| French 49 ind. | long-only | top 5 | 15.0% | 13.8% | 20.2% | 0.63 | −58% | |
| French 49 ind. | long/short | 5 vs 5 | 8.1% | 6.2% | 20.1% | 0.41 | −51% | |
| SPDR ETFs | long-only | top 2 | 12.3% | 11.5% | 16.0% | 0.66 | −45% | SPY 8.9% CAGR, Sharpe 0.53, DD −51% |
| SPDR ETFs | long/short | 2 vs 2 | 3.5% | 2.5% | 14.8% | 0.24 | −43% | |

⚠️ These rows use the 9-month window that the study *found*, so they are partly in-sample. The honest out-of-sample versions are in chart 3. For example, the long-only sector tilt has an information ratio vs the market of about 0.26–0.37 out of sample, not the 0.41 seen in-sample.

**Concentration:** with 12 sectors, fewer holdings gives more return but more drawdown (k=1 is best on Sharpe but most fragile). With 49 industries, 5–8 holdings is the sweet spot. Long-only concentration in the top sectors is the **most practical version** of the idea: no shorting, low turnover (about 30% per month), and it beats the market on return and Sharpe in every universe, and on drawdown too in the 12-industry and ETF versions (the 49-industry version has a deeper drawdown, −58%).

### B2. Sector-concentrated stock momentum
| Variant (stock window 6-1, hold 3, 25 long / 25 short) | Sharpe | 1st 15y | 2nd 15y |
|---|---|---|---|
| Plain stock momentum | 0.16 | 0.01 | 0.38 |
| Concentrated (sector SPSR 9m, top/bottom 3 of 11 GICS sectors) | **0.22** | 0.11 | 0.39 |
| Concentrated (sector SPSR 6m, top/bottom 3) | **0.23** | 0.15 | 0.37 |

Averaged over sector settings: sector window 9 months is best (0.19), then 12 (0.17), 6 (0.17) and 3 (0.12). Using the 3–4 strongest sectors beats using 2. The concentration also helped most where plain momentum was weakest (the first 15 years).

---

## 4. How to choose the window (chart 3): the most important practical finding

Out-of-sample Sharpe, Oct 2001 onwards, net of costs:

| Family | In-sample best (overfit) | WF full grid | WF lookback | Fixed 12 | Ensemble 6/9/12 | **Ensemble + guards** |
|---|---|---|---|---|---|---|
| Market trend (long/cash) | 0.73 | 0.54 | 0.56 | 0.68 | 0.74 | **0.77** |
| Stock L/S (S&P 500) | 0.04 | −0.21 | −0.13 | −0.08 | −0.04 | **0.35** |
| Sector-concentrated stock L/S | 0.09 | −0.08 | −0.03 | 0.09 | 0.08 | **0.35** |
| Sector L/S, 12 industries | 0.32 | 0.10 | 0.24 | 0.21 | 0.22 | **0.39** |
| Sector L/S, 49 industries | 0.37 | 0.10 | 0.28 | 0.20 | 0.26 | **0.49** |
| Sector L/S, SPDR ETFs | 0.24 | −0.05 | −0.01 | 0.16 | **0.18** | 0.16 |
| Top-2 long-only, 12 ind. (IR vs mkt) | 0.41 | 0.26 | 0.28 | **0.37** | 0.35 | 0.35 |
| Top-5 long-only, 49 ind. (IR vs mkt) | 0.31 | **0.26** | 0.22 | 0.11 | 0.18 | 0.25 |
| Top-2 long-only, SPDR (IR vs SPY) | 0.33 | 0.07 | 0.17 | **0.32** | 0.26 | 0.22 |

Lessons:
1. **"Optimising" the window walk-forward over a big grid is the worst method almost everywhere.** It gives back most of the in-sample edge, and sometimes all of it.
2. **A fixed, pre-chosen intermediate window, or a 6/9/12 blend, is as good as it gets.** The blend is the safest because no single window has to be right.
3. **Crash guards add more than any window choice** for every long/short book (chart 4). The ETF universe is the exception: it was already low-volatility.
4. For long-only sector concentration the guards add little. That book already has market-like risk.

**Recommended production setting:**
* Sector rating: **SPSR, blend of 6/9/12-month windows, skip 1 month, rebalance monthly with 3-month overlapping holds.**
* Stock momentum inside sectors: **6-1 window**, top/bottom 3 of 11 sectors.
* Long/short books: **always on** volatility targeting plus the 24-month bear-market switch.

---

## 4b. Robustness: unseen history and weekly windows (`study_robustness.py`)

### D1. Out-of-sample era: 1931-01 to 1996-09 (65 years, never used to choose anything)
The identical sector grid, rerun on CRSP industries before the study period. Average net Sharpe by lookback:

| Lookback (months) | 1 | 2 | 3 | 4 | 6 | 9 | 12 | 15 | 18 | 24 |
|---|---|---|---|---|---|---|---|---|---|---|
| 12 industries, long/short | 0.13 | 0.06 | 0.07 | 0.07 | 0.10 | 0.24 | **0.27** | 0.15 | 0.05 | −0.03 |
| 12 industries, long-only (IR) | 0.24 | 0.18 | 0.20 | 0.19 | 0.24 | 0.33 | **0.36** | 0.24 | 0.15 | 0.14 |
| 49 industries, long/short | 0.14 | 0.13 | 0.15 | 0.15 | 0.21 | 0.31 | **0.36** | 0.29 | 0.25 | 0.16 |
| 49 industries, long-only (IR) | 0.34 | 0.33 | 0.34 | 0.34 | 0.41 | 0.55 | **0.57** | 0.49 | 0.44 | 0.35 |

* The peak sits at 9–12 months in every case. In 1931–1996 the 12-month window edges out 9, which is the reverse of 1996–2026. **A 9-to-12-month band (or the 6/9/12 blend) is the robust choice**, not a single exact number.
* SPSR beat raw relative strength in all four tests here too: 0.12 vs 0.10, 0.23 vs 0.22, 0.26 vs 0.17 and 0.44 vs 0.39.
* SPSR on a 9-month window, top 5 of 49 industries long-only, made 18.0%/yr in 1931–1996 with an information ratio of 0.64. Drawdowns were large (−60%), since the 1930s are included.

### D2. Weekly rebalancing, lookbacks in weeks (1 week to 2 years)
| Lookback (weeks) | 1 | 2 | 4 | 8 | 13 | 26 | 39 | 52 | 78 | 104 |
|---|---|---|---|---|---|---|---|---|---|---|
| 49 industries (1996–2026) | −0.34 | −0.26 | −0.11 | −0.07 | 0.07 | 0.23 | 0.33 | **0.34** | 0.20 | 0.12 |
| SPDR ETFs (2001–2026) | −0.46 | −0.47 | −0.40 | −0.41 | −0.26 | −0.14 | 0.02 | **0.04** | −0.05 | −0.14 |

* Short windows reverse, and costs on weekly turnover make it worse. Holding longer helps: by holding period, the average Sharpe was −0.12 for 1 week, 0.06 for 4 weeks and 0.21 for 13 weeks (49 industries).
* The best weekly cell (SPSR, 39 weeks, skip 1 week, hold 13 weeks: Sharpe 0.41) is essentially the monthly 9-1 / hold-3 rule. Trading weekly adds turnover, not edge.
* For the ETFs, weekly long/short is close to zero or negative everywhere. **Use monthly rebalancing.**

---

## 5. Live market scan (as of 2026-10-31)

Produced by `study_sectors.py` → `results/B3_live_scan.json`, chart 5.

**SPDR sector ETFs, SPSR on a 9-month window:**

| Rank | ETF | SPSR | 9-month return |
|---|---|---|---|
| 1 | XLE (Energy) | 100 | +39.9% |
| 2 | XLK (Technology) | 91 | +25.2% |
| 3 | XLV (Health Care) | 82 | +21.0% |
| … | XLP, XLB, XLF, XLRE, XLU, XLI | 73 → 27 | |
| 10 | XLY (Cons. Discretionary) | 18 | −4.2% |
| 11 | XLC (Communication) | 9 | −16.1% |

**French 49 industries (as of Sep 2026):**
* Strongest: Computer Hardware, Semiconductors, Electrical Equipment, Petroleum & Natural Gas, Machinery.
* Weakest: Apparel, Business Services, Autos, Real Estate, Medical Equipment.

**Crash guard status:** the market's 24-month return is +36%, so the long/short books are **on**.

**Stock candidates** (6-1 momentum, inside the top/bottom 3 GICS sectors by 9-month SPSR):
* Long side (IT, Energy, Health Care): MRNA, DELL, MU, AMD, SNDK, INTC, CRWD, HPE, MRVL, OKTA …
* Short side (Cons. Discretionary, Communication Services, Industrials): CHTR, ROL, PNR, APTV, LULU, NKE, LHX, TSCO, HII, LVS …

*Free Yahoo data has bad ticks and corporate-action glitches. Check any name on a second source before acting.*

---

## 6. Data route: what was used, and free MCP options

**Used in this study (both free):**
1. **Kenneth R. French Data Library** (`mba.tuck.dartmouth.edu`). Monthly and daily returns for the whole market, 10 momentum deciles (3 windows), 12 and 49 industries, back to 1926. Built from CRSP, so it includes dead companies. This is the gold-standard free source for 30+ year momentum work.
2. **Yahoo Finance via the `yfinance` Python library.** Adjusted daily closes for SPY, the 11 SPDR sector ETFs and today's S&P 500 members since 1993. The member list comes from the `datasets/s-and-p-500-companies` GitHub CSV.

**Free MCP servers** (so Claude can fetch this data directly in future sessions):

| MCP server | Cost | Notes |
|---|---|---|
| `Alex2Yang97/yahoo-finance-mcp` (GitHub), or `narumiruna/yfinance-mcp` | Free, no key | Runs locally and wraps `yfinance`: historical prices, fundamentals, options. **Best free fit** for ETF/stock history. |
| Alpha Vantage official MCP: `https://mcp.alphavantage.co/mcp?apikey=YOUR_KEY` | Free key (25 requests/day) | Hosted, nothing to install. The daily limit makes a 500-stock scan slow, so it's better for ETFs. |
| Firecrawl MCP → `finance-yahoo-com/equities/history` (already connected here) | 5 credits per symbol | Works today, but every bar passes through the chat, so it is only practical for a few ETFs. |

> **Beginner note: what MCP is.** MCP (Model Context Protocol) is a standard "plug" that lets an AI assistant (the front end you talk to) call a data service (the back end). A Yahoo Finance MCP server is a tiny local back end: Claude sends it "give me SPY daily prices since 1996", it calls Yahoo and returns the rows. For **bulk research** like this (500 stocks × 30 years), it is faster to run the Python scripts directly. MCP shines for interactive questions like "what's XLE's 9-month SPSR today?".

**Network note for this cloud environment:** finance hosts are blocked by default. A personal environment named **`looper3`** was created that allows exactly `mba.tuck.dartmouth.edu`, `query1/query2.finance.yahoo.com`, `fc.yahoo.com` and `finance.yahoo.com`. Pick it for future sessions so `fetch_data.py` works.

---

## 7. Limitations: read before trusting any number
1. **Survivorship bias (stock level).** S&P 500 tests use *today's* members. The CRSP comparison shows this badly understates the short leg. Fix: a point-in-time constituent database (CRSP/Compustat, Norgate, or the paid Sharadar tier).
2. **Multiple testing.** Hundreds of configurations were run. The Deflated Sharpe probabilities of the best single cells were 0.18 (stocks) and 0.35–0.79 (sectors), mostly below the 0.95 bar. Trust the **plateau** (6–12 months) and the **method comparison**, not any one cell.
3. **French industries are not directly tradable.** They are value-weighted CRSP portfolios. The SPDR ETFs are the tradable check, and they confirm the 9-month peak, though with weaker numbers.
4. **Costs and frictions are simplified.** Flat bps per unit of turnover, with no short-borrow fees, taxes or market impact. Short lookbacks (1–3 months) turn over the most and would suffer most from realistic costs.
5. **Granularity.** The main study is monthly. A weekly check (section 4b) found no benefit from faster trading; intraday was not tested.
6. **Volatility targeting uses leverage up to 2×** in calm periods. That needs margin and is not available in all accounts.

---

## 8. Literature this design is based on
* Jegadeesh & Titman (1993, *J. Finance*): the original 3–12 month winners-minus-losers result, and overlapping-portfolio holding periods.
* Moskowitz & Grinblatt (1999, *J. Finance*), "Do Industries Explain Momentum?": industry momentum is strong and drives much of stock momentum.
* Novy-Marx (2012, *JFE*): the "intermediate" 7–12 month past return matters more than the recent 2–6 months.
* Moskowitz, Ooi & Pedersen (2012, *JFE*): time-series momentum.
* Asness, Moskowitz & Pedersen (2013, *J. Finance*): value and momentum everywhere.
* Daniel & Moskowitz (2016, *JFE*), "Momentum Crashes": crashes come in market rebounds after bear markets.
* Cooper, Gutierrez & Hameed (2004, *J. Finance*): momentum only pays after up markets (the 24-month market-state switch).
* Barroso & Santa-Clara (2015, *JFE*), "Momentum Has Its Moments": volatility scaling halves crash risk.
* Da, Gurun & Warachka (2014, *RFS*), "Frog in the Pan": continuous, steady momentum persists better (the SPSR consistency term).
* Faber (2010), "Relative Strength Strategies for Investing": sector rotation with ETFs.
* Bailey & López de Prado (2014, *J. Portfolio Mgmt*): the Deflated Sharpe Ratio.

---

## 9. Files
| File | Purpose |
|---|---|
| `fetch_data.py` | Downloads all data (French + Yahoo) into `data/` (git-ignored) |
| `momentum_lib.py` | Engine: signals, SPSR, portfolio weights, backtest, stats, deflated Sharpe, walk-forward |
| `study_market.py` | Concept A (A1–A3) → `results/A*` |
| `study_sectors.py` | Concept B (B1–B3, live scan) → `results/B*` |
| `study_selection.py` | Window-selection head-to-head → `results/C*` |
| `study_robustness.py` | 1931–1996 out-of-sample rerun + weekly windows → `results/D*` |
| `make_charts.py` | Charts → `charts/` |
| `results/*.csv/json` | Every grid cell and every return series, for your own analysis |
