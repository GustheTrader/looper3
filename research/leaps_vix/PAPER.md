# LEAPS Income and VIX Spike Trading: Risk and Capital-Allocation Efficiency

**Research paper.** Backtests run on free data and net of trading costs: SPY 1996–2026, sector ETFs 2001–2026, VIX sleeves 2007–2026.

> Not investment advice. Options prices here are *modelled* (calibrated to real indices), not historical quotes. Read §8 before acting on anything.

---

## Abstract

We test two additions to the momentum program in `research/momentum`:
1. **The LEAPS edge:** replace stock with deep-in-the-money long-dated calls (LEAPS) and sell short-dated calls above them for income.
2. **The VIX spike trade:** profit from volatility spikes.

We then ask how to split capital between them.

Free historical option quotes don't exist, so options are priced with Black-Scholes / Black-76 on a volatility surface built from VIX, VIX3M and VVIX. It is calibrated against two real tradable records:
- The CBOE PutWrite index (model 8.83%/yr vs actual 8.82%/yr, 1996–2026).
- The VIXY ETF (daily correlation 0.95, decay matched, 2011–2026).

Main results:
- **LEAPS deliver real capital efficiency on SPY.** The same market exposure as owning shares needs only **~28% of the capital**, and the same 10.0%/yr return. The other 72% is free for T-bills or other strategies.
- **Selling calls above the LEAPS cuts risk; it doesn't add return.** It brings in about 9%/yr of premium as cash, but rallies take nearly all of it back (payouts about 9.4%/yr). The real benefit is a smoother ride: volatility 15.7%→12.7%, Sharpe 0.59→0.71, max drawdown −52%→−40%.
- **On rotating momentum sectors, LEAPS and call-selling both fail.** LEAPS turnover (4.5 rolls a year) costs 3.8%/yr in spreads. Selling calls on sectors that are trending up pays out 2.5–3%/yr more than it collects. **Keep the momentum tilt in shares, and run LEAPS income on the index.**
- **For VIX, the money is in fading the spike, not chasing it.**
  - Shorting volatility only *after* a spike has peaked earned **+7.5%/yr at a Sharpe of 0.61** (real-ETF check, 2011+: +7.7%, Sharpe 0.59). It sat out the February 2018 short-volatility blow-up.
  - Buying volatility after the VIX term structure inverts lost money.
  - Monthly OTM VIX calls returned only **6–17 cents per premium dollar**. They are insurance, not a strategy.
- **Combined, three portfolios stand out (2007–2026):**
  - *Conservative:* matches SPY's return (10.0% vs 10.7%) with **half its drawdown** (−25% vs −51%).
  - *Balanced:* adds **+2.2%/yr** over SPY (12.9%) with a smaller drawdown (−40%).
  - *Aggressive:* earns **14.7%/yr** with SPY-like drawdown (−48%).
- **Paying for VIX-call tail hedges made every allocation worse.**

---

## 1. Questions

1. Does buying LEAPS instead of shares, and selling calls above them ("poor man's covered call", PMCC), improve return per dollar of capital and per unit of risk?
2. Does it work on the momentum-selected sectors from the earlier study, or only on the index?
3. Which ways of trading VIX spikes actually make money after costs?
4. How should capital be split between the LEAPS book, the VIX sleeves and cash? How much leverage is sensible?

---

## 2. Data and the option-pricing model

### 2.1 Data (all free, Yahoo Finance + Ken French library)
| Series | Span | Use |
|---|---|---|
| SPY and 11 SPDR sector ETFs, raw and dividend-adjusted | 1993/1998 → 2026-11 | Underlyings. The raw vs adjusted gap gives each fund's dividend yield. |
| ^VIX | 1990 → | 30-day implied-volatility level, and spike signals |
| ^VIX3M | 2006 → | Term structure (inverted vs normal) |
| ^VVIX | 2007 → | Volatility of VIX (implied vol for VIX options) |
| ^PUT (CBOE S&P 500 PutWrite index) | 1996 → | **Real** option-selling record, used for calibration |
| VIXY, SVXY | 2011 → | **Real** VIX-futures funds, used for validation |
| Fama-French daily risk-free rate | 1926 → | T-bill rate |

### 2.2 Volatility surface
| Component | Formula | How it was set |
|---|---|---|
| ATM 1-month IV (SPY) | `A × VIX` | **A = 0.82**, fitted so a simulated monthly ATM S&P put-write matches CBOE ^PUT: 8.83%/yr vs 8.82%/yr, monthly correlation 0.86 |
| Term structure | `θ + (iv₃₀ − θ)·g(T)/g(1/12)`, with `g(T) = (1−e^{−kT})/(kT)` | θ = long-run average 3-month IV (17.4% ATM); **k = 3.0/yr** fitted to VIX→VIX3M (R² 0.92) |
| Skew | `iv_atm − 0.15·ln(K/F)/√T` | Typical index skew. Deep-ITM LEAPS carry more vol than ATM, and OTM calls less. |
| Sector ETFs | SPY surface × (sector/SPY realized-vol ratio), blending from the 63-day ratio toward the 2-year ratio for long maturities | Long-dated options price vol mean-reversion |
| VIX futures | √(forward variance over the 30 days after expiry) from VIX/VIX3M, minus a convexity term, with the premium over spot scaled by **κ = 0.6** | κ fitted so the modelled 30-day constant-maturity futures decay matches VIXY: −64.6%/yr vs −65.4%/yr, daily correlation 0.95 |
| VIX options | Black-76 on that future, IV = VVIX × (1 + 0.4·ln(K/F)) | VIX smile slopes upward |

The resulting 18-month LEAPS IV is about 15.5% when VIX is 12, 17% at VIX 20 and 22% at VIX 45. That is realistic for SPY.

![Calibration](charts/1_calibration.png)

The model put-write ends at $5.9 against the real index's $6.6 over 30 years. The model is slightly conservative, mostly because monthly timing differs from CBOE's third-Friday rolls.

### 2.3 Trading costs
| Instrument | Half bid-ask (of option price) | Minimum | Commission |
|---|---|---|---|
| SPY 1-month calls | 2% | $0.01 | $0.65/contract |
| SPY LEAPS | 1% | $0.05 | $0.65/contract |
| Sector 1-month calls | 4% (2.5% "patient") | $0.02 | $0.65/contract |
| Sector LEAPS | 2.5% (1.2% "patient") | $0.10 | $0.65/contract |
| VIX options | 5% | $0.03 | — |

---

## 3. The LEAPS income engine

### 3.1 Mechanics (checked every month end)
1. **Buy LEAPS** at **0.80 delta**, **1.5 years** out, sized so `contracts × delta × price = λ × account value`. With λ = 1, the market exposure equals owning shares.
2. **Roll** when 6 months remain, when delta leaves 0.60–0.97, or when exposure drifts more than ±33% from target.
3. **Sell one 1-month 0.25-delta call per LEAPS.** It expires or is settled at intrinsic value at the next month end.
4. **Keep all unspent cash in T-bills.** No borrowing to buy LEAPS.

### 3.2 SPY results, Oct 1996 → Oct 2026 (30 years)
| Strategy | CAGR | Vol | Sharpe | Sortino | Max DD | CVaR 5% (monthly) | Capital in options | Call premium /yr | Call payouts /yr |
|---|---|---|---|---|---|---|---|---|---|
| Shares (buy & hold) | 10.0% | 15.3% | 0.60 | 0.85 | −51% | −9.6% | — | — | — |
| Covered call (shares + calls) | 9.7% | 12.0% | 0.70 | 0.88 | −36% | −8.1% | — | 6.9% | 7.1% |
| **LEAPS only** | **10.0%** | 15.7% | 0.59 | 0.91 | −52% | −9.1% | **28%** | — | — |
| **LEAPS + short calls** | **10.3%** | **12.7%** | **0.71** | **0.95** | **−40%** | −8.2% | 28% | 9.2% | 9.4% |
| LEAPS + calls, paused in spikes | 9.8% | 13.4% | 0.65 | 0.86 | −46% | −8.8% | 28% | 7.4% | 8.0% |

![LEAPS equity curves](charts/2_leaps_equity_curves.png)

**What this means:**
* **Capital efficiency is the real LEAPS edge.** About 28 cents of LEAPS does the work of $1 of stock: the same return, and only slightly higher volatility. The other 72 cents can earn T-bill interest (included above) or fund the VIX sleeves in §5. LEAPS also cap the loss on that 28 cents. In a crash you can't lose more than the premium paid.
* **The "income stream" is real cash flow, but not free money.** Selling calls brings in about 0.75%/month of premium. In rallies you pay most of it back (call payouts ≈ premium). What you keep is **lower risk**: −12 points of max drawdown and +0.12 Sharpe. Treat call income as a **volatility dampener**, not extra yield.
* **Don't pause call-selling during VIX spikes.** It *lowered* the Sharpe from 0.71 to 0.65. Right after spikes, call premiums are at their richest, and that is exactly when selling pays best. (The spike *buy* signal belongs in the allocation layer, §5.)

### 3.3 Parameter sensitivity (SPY, 54-cell grid, averages)
| Setting | Options | Effect |
|---|---|---|
| LEAPS delta | 0.70 / **0.80** / 0.90 | Sharpe 0.66 / **0.68** / 0.62. 0.90 is too deep: it ties up capital and rolls more. |
| LEAPS tenor | 1.0 / 1.5 / **2.0** years | Sharpe 0.63 / 0.66 / 0.66. Longer LEAPS mean fewer rolls and less time decay per month. |
| Short-call delta | 0.15 / **0.25** / 0.35 | CAGR 10.3% / 9.8% / 8.6%; max DD −48% / −44% / −40%. More income means more upside given away. |
| Exposure λ | 0.5 / 1.0 / 1.5 / 2.0 | CAGR 6.4% / 9.8% / 12.6% / 14.6%; max DD −24% / −46% / −63% / −76% (paused-calls variant) |

Best single cell: 0.80 delta, 2-year LEAPS, 0.25-delta calls, Sharpe 0.74, CAGR 10.8%, DD −40%. The a priori default (1.5 years) is within noise of it.

### 3.4 Momentum sectors (top-2 SPDR by SPSR, 2001 → 2026)
| Strategy | CAGR | Sharpe | Max DD | LEAPS rolls/yr | Spread cost /yr | Call premium − payouts |
|---|---|---|---|---|---|---|
| Shares (the momentum tilt) | **9.9%** | **0.56** | −49% | — | — | — |
| Covered call on shares | 7.9% | 0.51 | −43% | — | 1.3% | **−2.6%** |
| LEAPS only | 5.8% | 0.33 | −51% | 4.5 | 3.8% | — |
| LEAPS + calls | 3.0% | 0.19 | −45% | 4.5 | 5.4% | **−3.1%** |
| LEAPS only, patient limit orders | 7.8% | 0.44 | −51% | 4.4 | 1.9% | — |
| Quarterly rotation + wider buffer (shares) | 8.4% | 0.49 | −47% | — | — | — |

Two separate failures:
1. **Turnover.** Momentum rotates sectors about 4–5 times a year per slot. Each LEAPS round trip costs about 1.5% of notional. Even patient execution leaves LEAPS 2%/yr behind shares.
2. **Selling calls on winners.** Momentum picks sectors whose returns are expected to keep beating the market, so their calls end in the money more often than option prices assume. Every sector call-selling variant **paid out more than it collected**. Selling upside on a momentum position cancels out the reason you bought it.

**Design conclusion:** run **LEAPS + call income on the broad index (SPY)**, and hold the **momentum tilt in shares**, with no calls sold on it.

---

## 4. VIX spike strategies (daily, Mar 2007 → Nov 2026)

| Sleeve | Rule | CAGR | Vol | Sharpe | Max DD | Time active |
|---|---|---|---|---|---|---|
| V0 always long VIX futures | Hold a 30-day constant-maturity future | −41% | 86% | −0.21 | −100% | 100% |
| ↳ real VIXY, 2011+ | | −48% | 70% | −0.60 | −100% | |
| V1 long vol in backwardation | Long only while VIX ≥ VIX3M | −10.6% | 48% | −0.01 | −96% | 11% |
| ↳ real VIXY, 2011+ | | −2.5% | 36% | 0.10 | −75% | |
| **V3 spike fade (0.5× short)** | VIX ≥ 25, term structure back in contango, VIX ≥ 15% below its 10-day high. Exit when VIX < 18, after 40 days, or if backwardation returns | **+7.5%** | 13% | **0.61** | **−22%** | 12% |
| ↳ real SVXY, 2011+ | | **+7.7%** | 15% | **0.59** | −25% | |

**OTM VIX calls** (bought monthly, about 60 days out, sell everything once worth 3–5× cost or hold to expiry):

| Strike / future | Take profit | $ back per $1 premium | Months that paid | Best month |
|---|---|---|---|---|
| 1.3× | 5× | **$0.17** | 4.7% | 6.9× |
| 1.6× | 5× | $0.09 | 1.7% | 6.7× |
| 2.0× | 5× | $0.07 | 1.3% | 7.6× |

![VIX strategies](charts/3_vix_spike_strategies.png)

**What this means:**
* **Buying the spike rarely pays.** By the time VIX is visibly spiking (term structure inverted), futures already price it in, and the decay resumes as soon as fear fades. Permanent long volatility loses 40–50% a year.
* **Fading the spike works.** After the peak, implied vol and futures premiums collapse toward normal, and a *short* position collects that move. The rule waits for three confirmations (still elevated, term structure normal again, already 15% off the high). Because of that, it **never shorted into a rising spike**: it was flat through February 2018 and February–March 2020, and entered afterwards. Its worst month was −17.5% (June 2010). It made +36% in 2008 and +17–20% a year in 2020–2022.
* **VIX calls are insurance with a 83–93% average premium loss.** They pay off hugely in crashes: over 2008 and 2020 they returned about 14× and 7× one month's budget. But spending even 0.25% of the account per month costs more than the protection is worth (§5). This matches Israelov's "Pathetic Protection" finding for index puts.

---

## 5. Capital allocation and risk (2007-04 → 2026-10)

### 5.1 The building blocks
* **Core:** SPY LEAPS + 0.25-delta calls at exposure λ ∈ {0.75, 1.0, 1.25, 1.5}. Spare cash in T-bills.
* **Fade:** the V3 spike-fade sleeve at weight 0, 10% or 20% of the account. It is funded from the T-bill cash the LEAPS free up, so this is where capital efficiency gets spent.
* **Boost ("buy the spike"):** while the V3 "spike over" signal is on at a month end, run the core at λ + 0.5 for the next month.
* **Tail:** OTM VIX calls costing 0, 0.25%, 0.5% or 1% of the account per month.

That gives 96 combinations.

### 5.2 Results
| Portfolio | CAGR | Vol | Sharpe | Max DD | Calmar | CVaR 5% | Worst month | 2008 | 2020 | 2022 | Capital in options |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY shares | 10.7% | 15.4% | 0.64 | −51% | 0.21 | −9.7% | — | −36.8% | +18.3% | −18.2% | — |
| SPY covered call | 10.0% | 12.4% | 0.71 | −39% | 0.25 | −8.5% | — | −27.5% | +15.8% | −18.4% | — |
| Core λ=1 (LEAPS + calls) | 10.3% | 12.7% | 0.72 | −40% | 0.26 | −8.2% | −14.5% | −28.4% | +13.8% | −17.1% | 29% |
| **Conservative:** λ 0.75 + 20% fade | 10.0% | 10.5% | **0.82** | **−25%** | **0.39** | **−6.5%** | **−8.2%** | **−15.2%** | +14.4% | **−9.9%** | 22% |
| **Balanced:** λ 1.0 + 20% fade + boost | **12.9%** | 15.0% | 0.78 | −40% | 0.32 | −9.6% | −14.5% | −21.6% | **+30.2%** | −15.2% | 29% |
| **Aggressive:** λ 1.25 + 20% fade + boost | **14.7%** | 17.9% | 0.77 | −48% | 0.31 | −11.6% | −18.2% | −28.8% | +33.4% | −19.2% | 35% |
| λ 1.5, no VIX sleeves | 14.2% | 18.7% | 0.73 | −56% | 0.25 | −12.2% | −21.9% | −41.3% | +20.2% | −21.2% | 42% |

![Allocation frontier](charts/4_allocation_frontier.png)
![Recommended mixes](charts/5_recommended_mixes.png)

### 5.3 What each sleeve does (average over all other settings)
| Sleeve | Setting | Avg CAGR | Avg Sharpe | Avg Max DD |
|---|---|---|---|---|
| Spike fade | 0% → 10% → 20% | 7.1% → 7.8% → 8.6% | 0.42 → 0.46 → 0.50 | −45% → −44% → −42% |
| Buy-the-spike boost | off → on | 7.4% → 8.2% | 0.45 → 0.47 | −42% → −45% |
| **VIX-call tail hedge** | 0 → 0.25% → 0.5% → 1%/month | **12.5% → 9.8% → 7.1% → 2.0%** | 0.75 → 0.59 → 0.42 → 0.06 | −43% → −43% → −44% → −44% |
| Core exposure λ | 0.75 → 1.0 → 1.25 → 1.5 | 5.0% → 7.0% → 8.8% → 10.6% | 0.36 → 0.45 → 0.49 → 0.53 | −30% → −40% → −48% → −56% |

* **The fade sleeve is the best use of the capital LEAPS free up.** It adds return *and* reduces drawdown, because it earns in the recovery right after the core's worst months.
* **The boost adds return in rebounds** (2020: +30% vs +14%), for a modestly deeper drawdown.
* **Tail hedging with VIX calls never paid for itself.** Every budget level lowered return and Sharpe and **did not reduce max drawdown at all**. The 2008 drawdown builds over months, and monthly calls mostly expire worthless between spikes.

### 5.4 How much leverage? (Kelly and drawdown budgets)
Growth rate (mean log return) of the core keeps rising with λ through 2.0 in 2007–2026: 6.3% at 0.5, 9.8% at 1.0, 13.3% at 1.5, 15.9% at 2.0. So the formal "Kelly" answer is above 2×. **We don't recommend acting on it.**
- The estimate comes from one strong bull era.
- In the 30-year SPY test, λ = 2 had an **−76% to −88%** drawdown (1999–2002 + 2008).
- Kelly sizing assumes you know the true return, and you don't.

The standard practice is **half-Kelly or less**, with a **drawdown budget** on top. Choose λ so the stress-test drawdown is one you would actually sit through:

| Drawdown you can tolerate | Suggested setting |
|---|---|
| ~25% | Conservative: λ 0.75 + fade |
| ~40% (covered-call-like) | Balanced: λ 1.0 + fade + boost |
| ~50% (stock-market-like) | Aggressive: λ 1.25 + fade + boost |
| > 55% | Not recommended |

---

## 6. Recommended playbook

**Account structure (Balanced):**
```
 ~29%  SPY LEAPS, 0.80 delta, 1.5-2 yr, sized to 1.0x account delta
 ~20%  spike-fade sleeve capital (idle in T-bills until the signal fires)
 ~51%  T-bills / money market (collateral, dry powder for the boost)
  +    sell 1 x 1-month 0.25-delta SPY call per LEAPS, every month
  +    momentum sector tilt: hold the top-2 SPSR sectors in SHARES (no calls),
       funded from the T-bill bucket if you want the tilt (not tested together here)
```

**Monthly checklist (last trading day):**
1. **Roll LEAPS** with < 6 months left or delta outside 0.60–0.97. Re-size if exposure drifted ±33%.
2. **Sell next month's 0.25-delta call** on SPY, even in spikes, because premiums are richest then. Close or roll calls that are in the money.
3. **Check the spike-fade signal** daily (or use the month-end state): VIX ≥ 25, VIX < VIX3M, and VIX ≥ 15% below its 10-day high.
   - If it's **on:** short VIX futures exposure at 0.5× sleeve capital (e.g. SVXY), and raise LEAPS exposure to λ + 0.5 for one month.
   - **Exit** when VIX < 18, after 40 days, or the moment VIX ≥ VIX3M again.
4. **Never** hold long VIX futures/ETPs as a standing position, and **don't** run a monthly VIX-call budget.

---

## 7. Answers to the four questions
1. **LEAPS + calls vs shares:** yes, better per unit of risk (Sharpe 0.71 vs 0.60, DD −40% vs −51%) and **3.6× more capital-efficient** (28% of capital for equal exposure). Call income is a risk reducer, not extra return.
2. **On momentum sectors:** no. Hold the momentum tilt in shares, and keep LEAPS and calls on the index.
3. **VIX spikes:** fade them after the peak (Sharpe 0.6, confirmed on SVXY). Don't chase them, and don't pay for standing VIX-call insurance.
4. **Allocation:** spend the capital LEAPS free up on the spike-fade sleeve, add the post-spike boost, and set λ from your drawdown budget (0.75 / 1.0 / 1.25). This beat SPY on return-to-drawdown in all three versions.

---

## 8. Limitations and risks: read before trading
1. **Modelled option prices.** No free historical quotes exist. The model matches the ^PUT and VIXY records on average, but individual months can differ (^PUT monthly correlation 0.86). Real fills, skew changes and early-assignment events are not modelled exactly.
2. **Early assignment and dividends.** Short ITM calls on ETFs can be assigned early, especially before ex-dividend dates. A PMCC must then exercise or sell the LEAPS. The monthly model ignores this.
3. **Gap risk on the short-vol sleeve.** Short VIX exposure can lose 50–90% in a day (February 2018: SVXY's predecessor fell ~90%). The fade rule avoided that event by design, but a **second spike after entry** is the main risk. Keep the sleeve small (≤ 20% of the account at 0.5×), and respect the stop.
4. **VIX ETPs decay and can be restructured.** VIXY loses over 50% a year when held, and SVXY changed its leverage in 2018. Read each fund's prospectus.
5. **Sample.** The VIX sleeves cover 2007–2026 (one financial crisis, one pandemic crash). The allocation grid is **in-sample**: the three named mixes were picked after seeing the results, so expect less going forward. The sleeve *rules* were set before testing, from the literature.
6. **Leverage.** λ > 1 needs options approval and, for the short-VIX sleeve, margin. Margin calls in a crash can force selling at the worst moment.
7. **Taxes.** Options and VIX ETPs often produce short-term gains. Not modelled.
8. **Combining with momentum.** The share-based momentum tilt and the LEAPS core were tested separately, not together.

---

## 9. Literature
* Whaley (2002), "Return and Risk of CBOE Buy Write Monthly Index", *J. Derivatives*: covered calls give near-equity returns with lower risk.
* Israelov & Nielsen (2015), "Covered Calls Uncovered", *Financial Analysts Journal*: covered-call returns come from equity exposure + a short-volatility premium + an unrewarded short equity-timing bet.
* Carr & Wu (2009), "Variance Risk Premiums", *Review of Financial Studies*: implied variance sits above realized variance on average. This is why selling options pays.
* Whaley (2013), "Trading Volatility: At What Cost?", *J. Portfolio Management*: VIX futures ETPs lose money structurally.
* Cheng (2019), "The VIX Premium", *Review of Financial Studies*: the VIX futures premium falls, and can turn negative, when risk is high, then recovers.
* Israelov (2017), "Pathetic Protection: The Elusive Benefits of Protective Puts", *J. Alternative Investments*: buying protection is a costly way to reduce drawdowns.
* Moran & Dash (2007), "VIX Futures and Options: Pricing and Using Volatility Products to Manage Downside Risk", *J. Trading*.
* Black (1976), "The Pricing of Commodity Contracts": the Black-76 model used for VIX options.
* Kelly (1956) and Thorp (2006): growth-optimal sizing and why fractional Kelly is used in practice.

---

## 10. Reproduce
See `README.md`. Pipeline: `fetch_data.py` → `calibrate.py` → `study_leaps.py` → `study_vix.py` → `study_allocation.py` → `make_charts.py`. Every return series and grid cell is in `results/`.
