"""
MARGIN & LEVERAGE LAYER -- "Portfolio Margin Risk-Based Haircuts" deck applied to
the 65/35 Core Frontier portfolio.

Goal: the biggest CAGR per unit of drawdown (Calmar = CAGR / |max drawdown|) with
no calendar year worse than -15%.

Leverage rules (applied to the whole 65/35 portfolio's excess return over T-bills;
borrowing above 1x costs T-bills + 0.5%):
  constant      1.0x, 1.25x, 1.5x, 1.75x, 2.0x
  deck_vix      the deck's VIX bands: <15 2.0x, 15-20 1.5x, 20-30 1.25x, 30-50 1.0x, >50 0.75x
  deck_vix_125  the same bands x 1.25
Annual loss-budget throttle (optional, novel): each day leverage is capped so that a
99% one-month loss (2.33 x 21-day volatility) cannot take the year past -15%, given the
year-to-date result (with a 2% safety margin). Resets every 1 January.

Hedges from the deck's "triple hedge" (calendars are already a trading sleeve):
  SPY put backspread  monthly, 2 months out: sell 1 put 3% out of the money, buy 2 puts
                      8% out of the money; notional h_spy x account; skewed vol surface
  VIX call backspread monthly, 2 months out: sell 1 call at the VIX future, buy 2 calls
                      at 1.4x; structure value x s_vix % of the account per VIX point
Both marked daily with the calibrated models in research/leaps_vix/options_lib.py.

Margin use follows the deck's haircut table (midpoints, unhedged vs hedged) by VIX regime;
crypto sits outside portfolio margin and must be fully funded.
Selection: best Calmar meeting the -15% rule on 2015-10 .. 2020-12; tested on 2021-01 .. 2026-11.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "leaps_vix"))
import options_lib as ol  # noqa: E402

OUT = HERE / "results"
DESIGN_END, TEST_START = "2020-12-31", "2021-01-01"


def put_backspread(m, idx, short_otm=0.03, long_otm=0.08, tenor=60, half=0.03):
    """Daily P&L per $1 of SPY notional of a rolling monthly 1x2 put backspread."""
    S, q, rf, vix = m["raw"]["SPY"], m["q"]["SPY"], m["rf"], m["vix"]
    A = ol.PARAMS["A_VIX"]
    days = idx
    ends = set(pd.Series(days, index=days).groupby([days.year, days.month]).last())
    pnl = pd.Series(0.0, index=days)
    pos, prev = None, 0.0
    for t in days:
        St, r, qq, iv30 = S[t], rf[t], q[t], A * vix[t]
        val = 0.0
        if pos:
            left = max((pos["exp"] - t).days, 0) / 365
            P = lambda K: ol.bs_price(St, K, left, r, qq, ol.iv(St, K, max(left, 1 / 365), r, qq, iv30), call=False) if left > 0 else max(K - St, 0)
            val = (2 * P(pos["k2"]) - P(pos["k1"])) / pos["S0"]
            pnl[t] = val - prev
        if t in ends:
            if pos:                                   # close at bid/ask
                pnl[t] -= half * abs(val) + half * 0.0
            T = tenor / 365
            k1, k2 = St * (1 - short_otm), St * (1 - long_otm)
            p1 = ol.bs_price(St, k1, T, r, qq, ol.iv(St, k1, T, r, qq, iv30), call=False)
            p2 = ol.bs_price(St, k2, T, r, qq, ol.iv(St, k2, T, r, qq, iv30), call=False)
            entry = (2 * p2 - p1) / St
            cost = half * (p1 + 2 * p2) / St
            pnl[t] -= cost
            pos = {"exp": t + pd.Timedelta(days=tenor), "k1": k1, "k2": k2, "S0": St}
            prev = entry
        else:
            prev = val
    return pnl


def vix_backspread(m, idx, mult=1.4, tenor=60, half=0.05):
    """Daily change in value (VIX points) of a rolling monthly 1x2 VIX call backspread."""
    vix, v3, vv, rf = m["vix"], m["vix3m"], m["vvix"], m["rf"]
    days = idx
    ends = set(pd.Series(days, index=days).groupby([days.year, days.month]).last())
    pnl = pd.Series(0.0, index=days)
    pos, prev = None, 0.0
    for t in days:
        v, x3, w, r = vix[t] * 100, v3[t] * 100, vv[t], rf[t]
        val = 0.0
        if pos:
            left = max((pos["exp"] - t).days, 0)
            if left > 0:
                F = ol.vix_future(v / 100, x3 / 100, w, left) * 100
                val = 2 * ol.vix_call(F, pos["k2"], left, r, w) - ol.vix_call(F, pos["k1"], left, r, w)
            else:
                val = 2 * max(v - pos["k2"], 0) - max(v - pos["k1"], 0)
            pnl[t] = val - prev
        if t in ends:
            if pos:
                pnl[t] -= half * abs(val)
            F = ol.vix_future(v / 100, x3 / 100, w, tenor) * 100
            k1, k2 = F, F * mult
            c1, c2 = ol.vix_call(F, k1, tenor, r, w), ol.vix_call(F, k2, tenor, r, w)
            pnl[t] -= half * (c1 + 2 * c2)
            pos = {"exp": t + pd.Timedelta(days=tenor), "k1": k1, "k2": k2}
            prev = 2 * c2 - c1
        else:
            prev = val
    return pnl


def haircut(vix, hedged):
    """Deck's haircut table midpoints by regime: unhedged vs triple-hedged."""
    if vix < 0.20:
        return 0.135 if hedged else 0.275
    if vix < 0.30:
        return 0.185 if hedged else 0.36
    if vix < 0.50:
        return 0.245 if hedged else 0.475
    return 0.345 if hedged else 0.65


# Margin per $ of notional by instrument (portfolio margin), for the utilisation estimate.
FIXED_MARGIN = {"w_btc_tilt": 1.0, "w_eth_tilt": 1.0,          # crypto: outside PM, fully funded
                "w_trend_hard": 0.08,                          # futures initial margin
                "w_psc_gld": 0.06}                             # defined-risk put spread: spread width
ETF_SLEEVES = ["w_hard_rotation", "w_space"]                   # ETFs: deck haircut by VIX regime
_WC = HERE / "results" / "WEIGHTS_choice.json"
W_TR = json.loads(_WC.read_text())["weights"] if _WC.exists() else {"vix_fade": 0.15, "carry": 0.10, "momentum_ls": 0.05, "prediction": 0.05, "ff_calendars": 0.0}
# per $ of sleeve: VIX futures 0.5 notional x 20% margin; momentum L/S 2 legs x 15%; prediction stakes 20%
TRADING_MARGIN_STATIC = W_TR["vix_fade"] * 0.5 * 0.20 + W_TR["momentum_ls"] * 2 * 0.15 + W_TR["prediction"] * 0.2


def trading_margin(D):
    """Daily trading-sleeve margin: static part + carry (spot crypto fully funded while on) + FF calendars
    (debits are fully paid: margin = open calendar value / sleeve equity, from the replay)."""
    on = D["carry_on"] if "carry_on" in D else 0.0
    ffx = D["ff_exposure"] if "ff_exposure" in D else 0.0
    dx = D["decay_exposure"] if "decay_exposure" in D else 0.0
    return TRADING_MARGIN_STATIC + W_TR["carry"] * on + W_TR.get("ff_calendars", 0.0) * ffx + W_TR.get("decay", 0.0) * dx


def utilisation(D, vix, L, hedged, trading_k=1.0):
    """Margin used / account equity for leverage L on the 65/35 portfolio."""
    core = sum(D[c] * v for c, v in FIXED_MARGIN.items())
    hc = pd.Series([haircut(v, hedged) for v in vix.values], index=D.index)
    core = core + sum(D[c] for c in ETF_SLEEVES) * hc
    return L * (0.65 * core + trading_k * trading_margin(D))


def deck_bands(vix):
    return np.select([vix < 0.15, vix < 0.20, vix < 0.30, vix < 0.50], [2.0, 1.5, 1.25, 1.0], 0.75)


def simulate(base_ex, rf, lev_sched, hedge_pnl, throttle):
    """Apply leverage (with optional annual loss-budget throttle) and hedges; returns daily returns, leverage."""
    vol = base_ex.rolling(63, min_periods=40).std().shift(1).fillna(base_ex.std())
    out, levs = [], []
    nav_year_start, nav = 1.0, 1.0
    year = None
    for i, t in enumerate(base_ex.index):
        if t.year != year:
            year, nav_year_start = t.year, nav
        L = lev_sched.iloc[i]
        if throttle:
            ytd = nav / nav_year_start - 1
            budget = 0.15 + ytd - 0.02
            Lmax = budget / (2.33 * vol.iloc[i] * np.sqrt(21)) if budget > 0 else 0.0
            L = min(L, max(Lmax, 0.0))
        r = rf.iloc[i] + L * base_ex.iloc[i] - max(L - 1, 0) * 0.005 / 252 + hedge_pnl.iloc[i]
        nav *= 1 + r
        out.append(r)
        levs.append(L)
    return pd.Series(out, index=base_ex.index), pd.Series(levs, index=base_ex.index)


def metrics(r, rf):
    eq = (1 + r).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    n = len(r)
    cagr = float(eq.iloc[-1] ** (252 / n) - 1)
    yr = (1 + r).groupby(r.index.year).prod() - 1
    ex = r - rf
    return {"cagr": cagr, "max_dd": dd, "calmar": cagr / -dd if dd < 0 else np.nan,
            "sharpe": float(ex.mean() / ex.std() * np.sqrt(252)), "vol": float(r.std() * np.sqrt(252)),
            "worst_year": float(yr[yr.index > 2015].min()), "worst_year_at": int(yr[yr.index > 2015].idxmin())}


def main():
    D = pd.read_csv(OUT / "CF_daily.csv", index_col=0, parse_dates=True)
    m = ol.load_market()
    idx = D.index
    rf = D.rf
    base_ex = D.portfolio - rf
    vix = m["vix"].reindex(idx).ffill()
    vix_l = vix.shift(1).bfill()                               # yesterday's VIX decides today's leverage

    hp = put_backspread(m, idx)                                  # per $1 notional
    hv = vix_backspread(m, idx) / 100                            # per "1% of NAV per VIX point" unit
    hedge_stats = {"spy_put_backspread_ann": float(hp.sum() / (len(hp) / 252)),
                   "vix_backspread_ann_per_unit": float(hv.sum() / (len(hv) / 252)),
                   "spy_bs_2020_02_03": float(hp.loc["2020-02-19":"2020-03-23"].sum()),
                   "vix_bs_2020_02_03": float(hv.loc["2020-02-19":"2020-03-23"].sum()),
                   "spy_bs_2022": float(hp.loc["2022"].sum()), "vix_bs_2022": float(hv.loc["2022"].sum()),
                   "corr_spy_bs_with_portfolio": float(hp.corr(base_ex)), "corr_vix_bs_with_portfolio": float(hv.corr(base_ex))}

    schedules = {f"const_{L}": pd.Series(L, index=idx) for L in [1.0, 1.25, 1.5, 1.75, 2.0]}
    schedules["deck_vix"] = pd.Series(deck_bands(vix_l.values), index=idx)
    schedules["deck_vix_x1.25"] = schedules["deck_vix"] * 1.25
    rows, keep = [], {}
    for (sname, sched), thr, hs, hvx in itertools.product(schedules.items(), [False, True], [0.0, 0.25, 0.5], [0.0, 0.5, 1.0]):
        hedge = hs * hp + hvx * hv
        r, lev = simulate(base_ex, rf, sched, hedge, thr)
        key = f"{sname}{'_throttle' if thr else ''}_spy{hs}_vix{hvx}"
        row = {"key": key, "schedule": sname, "throttle": thr, "spy_hedge": hs, "vix_hedge": hvx, "avg_lev": float(lev.mean())}
        for per, sl in {"design": slice(None, DESIGN_END), "test": slice(TEST_START, None), "full": slice(None, None)}.items():
            mm = metrics(r.loc[sl], rf.loc[sl])
            row |= {f"{per}_{k}": v for k, v in mm.items()}
        rows.append(row)
        keep[key] = (r, lev)
    g = pd.DataFrame(rows)

    ok = g[g.design_worst_year >= -0.15]
    chosen = ok.sort_values("design_calmar", ascending=False).iloc[0]["key"]
    ok_full = g[g.full_worst_year >= -0.15]
    best_full = ok_full.sort_values("full_calmar", ascending=False).iloc[0]["key"]

    # margin utilisation of the chosen configuration (deck haircut table; crypto fully funded outside PM)
    r_c, lev_c = keep[chosen]
    hedged = (g.set_index("key").loc[chosen, "spy_hedge"] > 0) or (g.set_index("key").loc[chosen, "vix_hedge"] > 0)
    util = utilisation(D, vix, lev_c, hedged)
    util_unhedged = utilisation(D, vix, lev_c, False)
    margin = {"avg_util": float(util.mean()), "max_util": float(util.max()), "max_util_date": str(util.idxmax().date()),
              "pct_days_above_70": float((util > 0.70).mean()), "avg_util_unhedged": float(util_unhedged.mean()),
              "max_util_unhedged": float(util_unhedged.max())}
    # leverage frontier with margin use (no hedges: they cost more than they saved)
    frontier = []
    for L in [1.0, 1.25, 1.5, 1.75, 2.0]:
        k = f"const_{L}_spy0.0_vix0.0"
        rr = g.set_index("key").loc[k]
        u = utilisation(D, vix, L, False)
        uh = utilisation(D, vix, L, True)
        frontier.append({"leverage": L, **{c: rr[c] for c in ["test_cagr", "test_max_dd", "test_calmar", "test_worst_year",
                                                              "full_cagr", "full_max_dd", "full_calmar", "full_worst_year", "full_sharpe"]},
                         "avg_util": float(u.mean()), "max_util": float(u.max()), "pct_days_over_70": float((u > 0.70).mean()),
                         "avg_util_deck_hedged_haircuts": float(uh.mean())})
    pd.DataFrame(frontier).to_csv(OUT / "MG_frontier.csv", index=False)

    base_r = D.portfolio
    base = {per: metrics(base_r.loc[sl], rf.loc[sl]) for per, sl in
            {"design": slice(None, DESIGN_END), "test": slice(TEST_START, None), "full": slice(None, None)}.items()}
    yr = lambda r: {int(k): float(v) for k, v in ((1 + r).groupby(r.index.year).prod() - 1).items()}
    out = {"chosen": chosen, "best_full_with_rule": best_full, "frontier": frontier, "base_65_35": base, "hedges": hedge_stats, "margin": margin,
           "chosen_row": g.set_index("key").loc[chosen].to_dict(), "best_full_row": g.set_index("key").loc[best_full].to_dict(),
           "yearly_chosen": yr(r_c), "yearly_base": yr(base_r), "avg_lev_by_year": {int(k): float(v) for k, v in lev_c.groupby(lev_c.index.year).mean().items()}}
    (OUT / "MG_summary.json").write_text(json.dumps(out, indent=1, default=float))
    g.to_csv(OUT / "MG_grid.csv", index=False)
    pd.DataFrame({"base_65_35": base_r, "chosen": r_c, "chosen_leverage": lev_c, "util": util,
                  "best_full": keep[best_full][0]}).to_csv(OUT / "MG_daily.csv")

    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print("hedges", {k: round(v, 4) for k, v in hedge_stats.items()})
    cols = ["key", "avg_lev", "design_cagr", "design_calmar", "design_worst_year", "test_cagr", "test_max_dd", "test_calmar",
            "test_worst_year", "full_cagr", "full_max_dd", "full_calmar", "full_worst_year", "full_sharpe"]
    print(g.sort_values("design_calmar", ascending=False)[cols].head(15).to_string(index=False))
    print("-- meeting -15% rule in design, top by design Calmar")
    print(ok.sort_values("design_calmar", ascending=False)[cols].head(10).to_string(index=False))
    print("chosen:", chosen, "| best full with rule:", best_full)
    print("base", {p: {k: round(v, 3) for k, v in d.items()} for p, d in base.items()})
    print("margin", margin)
    print(pd.DataFrame(frontier).to_string(index=False))
    print("yearly chosen", {k: round(v, 3) for k, v in out["yearly_chosen"].items()})
    print("yearly base  ", {k: round(v, 3) for k, v in out["yearly_base"].items()})
    print("lev by year", {k: round(v, 2) for k, v in out["avg_lev_by_year"].items()})


if __name__ == "__main__":
    main()
