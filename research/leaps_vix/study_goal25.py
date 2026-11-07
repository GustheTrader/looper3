"""
GOAL: higher Sharpe and > 25% a year.

Return ~ Sharpe x volatility, so the plan has two steps:
  1. raise the Sharpe by combining return streams that do not move together;
  2. lever the combination to a volatility target, with leverage that falls in stress.
Every rule is set before looking at results, and only past data is used to size
positions. The target volatility is chosen on 2008-03 .. 2016-12 and then tested
unchanged on 2017-01 .. 2026-09.

Sleeves (daily excess returns over T-bills, net of costs)
  leaders_dual   top-3 of QQQ/MDY/IJR/SMH/IGV/sector ETFs by SPSR, each held only
                 while above its own 50-day average (dual momentum), else cash
  qqq_tilt       QQQ, full size above its own 50-day average, half size below
  trend          cross-asset trend: SPY, QQQ, EFA, EEM, TLT, IEF, GLD, DBC, UUP;
                 long if the 12-month excess return is positive, short if negative,
                 each sized to 10% volatility (63-day, capped at 2x); re-signalled monthly
  momentum_ln    market-neutral CRSP momentum (French top-minus-bottom decile, daily)
  spike_fade     short VIX futures after a spike has peaked (study_vix rule)
  putwrite       CBOE S&P 500 PutWrite index (real index; volatility risk premium)
Combination
  equal          equal weights, no leverage
  riskpar        inverse 63-day volatility weights (weekly), no leverage
  riskpar_vt{X}  risk parity scaled to X% portfolio volatility (63-day), max 4x gross
  ..._regime     plus a stress dial: x0.5 while VIX >= VIX3M; x0.75 while SPY is below
                 its 200-day average; x1 otherwise
Costs: ETF trades 5 bps, trend trades 2 bps, French momentum ~35%/month turnover per leg
at 10 bps + 0.5%/yr borrow, rebalancing of sleeve weights 5 bps, and borrowing at
T-bills + 0.5%/yr on any gross exposure above 1x.
"""
from __future__ import annotations

import itertools
import json
import sys

import numpy as np
import pandas as pd

import options_lib as ol
import study_vix as sv
from study_leadership import load as load_lead, regime, rotation_weights

sys.path.insert(0, str(ol.HERE.parent / "momentum"))
import momentum_lib as ml  # noqa: E402

OUT = ol.HERE / "results"
START, MID, END = "2008-03-01", "2016-12-31", "2026-09-30"
TREND = ["SPY", "QQQ", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "UUP"]


def sleeves():
    px, spy_raw, rf = load_lead()
    idx = px.index
    R = px.pct_change(fill_method=None)
    out = {}

    # dual-momentum leaders
    top, _, _ = rotation_weights(px)
    above = (px > px.rolling(50).mean()).shift(1, fill_value=False)
    W = top * above.reindex(columns=top.columns).astype(float)
    r = (W * R[top.columns].fillna(0)).sum(axis=1) - W.diff().abs().sum(axis=1).fillna(0) * 5e-4
    out["leaders_dual"] = r - W.sum(axis=1) * rf          # excess over cash on the invested part

    # QQQ with its own 50-day tilt
    e = regime(px["QQQ"]).map({True: 1.0, False: 0.5})
    out["qqq_tilt"] = e * (R["QQQ"] - rf) - e.diff().abs().fillna(0) * 5e-4

    # cross-asset trend
    xa = pd.read_csv(ol.DATA / "cross_asset_adj_d.csv", index_col=0, parse_dates=True).reindex(idx)
    XR = xa.pct_change(fill_method=None)
    ex = XR.sub(rf, axis=0)
    me = idx.to_series().groupby([idx.year, idx.month]).transform("max") == idx.to_series()
    # blend of 1-, 3-, 6- and 12-month trend signals (Hurst, Ooi & Pedersen style)
    sig = sum(np.sign(np.log1p(ex).rolling(L, min_periods=int(L * 0.9)).sum()) for L in (21, 63, 126, 252)) / 4
    sig = sig.where(me).ffill()
    vol = XR.rolling(63, min_periods=40).std() * np.sqrt(252)
    size = (0.10 / vol).clip(upper=2.0)
    pos = (sig * size).shift(1)
    n = pos.notna().sum(axis=1).replace(0, np.nan)
    out["trend"] = ((pos * ex).sum(axis=1) / n - (pos.diff().abs().sum(axis=1) / n) * 2e-4).fillna(0)

    # market-neutral momentum (survivorship-free)
    dec = pd.read_csv(ml.DATA / "mom10_d.csv", index_col=0, parse_dates=True)
    wml = (dec["Hi PRIOR"] - dec["Lo PRIOR"]) - 2 * 12 * 0.35 * 10e-4 / 252 - 0.005 / 252
    out["momentum_ln"] = wml.reindex(idx)

    # spike fade (daily)
    m = ol.load_market()
    d = sv.panel(m)
    fade, _ = sv.v3_fade(d)
    out["spike_fade"] = fade.reindex(idx)

    # put-write (real CBOE index)
    put = m["put"].reindex(idx)
    out["putwrite"] = put.pct_change(fill_method=None) - rf

    # momentum, volatility-scaled to 12% (Barroso & Santa-Clara 2015 crash fix)
    wv = out["momentum_ln"].rolling(126, min_periods=60).std().shift(1) * np.sqrt(252)
    out["momentum_vs"] = out["momentum_ln"] * (0.12 / wv).clip(upper=2.0)

    # put spreads sold to finance upside calls (zero net premium), SPY and QQQ
    add_qqq(m)
    for u in ["SPY", "QQQ"]:
        out[f"psc_{u.lower()}"] = putspread_calls(m, u).reindex(idx)

    S = pd.DataFrame(out).loc[START:END]
    return S, rf.loc[START:END], spy_raw, m


def add_qqq(m):
    """Give the market panel QQQ prices, dividend yield and vol ratios (as study_leadership)."""
    if "QQQ" in m["raw"]:
        return
    graw = pd.read_csv(ol.DATA / "growth_etf_raw_d.csv", index_col=0, parse_dates=True)
    gadj = pd.read_csv(ol.DATA / "growth_etf_adj_d.csv", index_col=0, parse_dates=True)
    idx = m["raw"].index
    m["raw"]["QQQ"], m["adj"]["QQQ"] = graw["QQQ"].reindex(idx), gadj["QQQ"].reindex(idx)
    tr = np.log(m["adj"]["QQQ"] / m["adj"]["QQQ"].shift(252)) - np.log(m["raw"]["QQQ"] / m["raw"]["QQQ"].shift(252))
    m["q"]["QQQ"] = tr.clip(0, 0.08).fillna(0.007)
    lr = np.log(m["adj"]).diff()
    for win, key in [(63, "rel"), (504, "rel_long")]:
        rv = lr["QQQ"].rolling(win, min_periods=40).std() / lr["SPY"].rolling(win, min_periods=40).std()
        m[key]["QQQ"] = rv.clip(0.6, 2.5).fillna(1.25)


def putspread_calls(m, u, short_put=0.30, long_put=0.10, call=0.25):
    """Each month end: sell a 1-month put spread (short `short_put`-delta put, long
    `long_put`-delta put) and spend the whole net credit on `call`-delta calls of the
    same expiry: upside call exposure for zero net premium. Marked to model daily.
    Returns daily P&L per $1 of underlying notional (an overlay on T-bill cash)."""
    raw, q, rf, vix = m["raw"][u], m["q"][u], m["rf"], m["vix"]
    rel, rl = m["rel"][u], m["rel_long"][u]
    A = ol.PARAMS["A_VIX"]
    cost = ol.COSTS["SPY"]["short"]
    days = raw.dropna().loc["2007-01-01":].index
    ends = [d for d in pd.Series(days, index=days).groupby([days.year, days.month]).last()]
    pnl = pd.Series(0.0, index=days)
    for d0, d1 in zip(ends[:-1], ends[1:]):
        S0, r0, q0, iv30 = raw[d0], rf[d0], q[d0], A * vix[d0]
        rr = (rel[d0], rl[d0])
        T = max((d1 - d0).days, 1) / 365
        # put delta = call delta - e^{-qT}
        k1 = ol.strike_for_delta(S0, T, r0, q0, iv30, np.exp(-q0 * T) - short_put, rr)
        k2 = ol.strike_for_delta(S0, T, r0, q0, iv30, np.exp(-q0 * T) - long_put, rr)
        kc = ol.strike_for_delta(S0, T, r0, q0, iv30, call, rr)

        def value(S, t_left, r, qq, ivv, rrr):
            put = lambda K: ol.bs_price(S, K, t_left, r, qq, ol.iv(S, K, t_left, r, qq, ivv, rrr), call=False)
            return put(k1), put(k2), ol.call_price(S, kc, t_left, r, qq, ivv, rrr)

        p1, p2, c = value(S0, T, r0, q0, iv30, rr)
        credit = cost.sell(p1) - cost.buy(p2)
        n_c = max(credit, 0) / cost.buy(c)
        prev = n_c * c - p1 + p2                      # position value at mid
        prev -= (p1 - cost.sell(p1)) + (cost.buy(p2) - p2) + n_c * (cost.buy(c) - c)   # entry spreads
        span = days[(days > d0) & (days <= d1)]
        for t in span:
            left = max((d1 - t).days, 0) / 365
            St = raw[t]
            if left <= 0:
                v = n_c * max(St - kc, 0) - max(k1 - St, 0) + max(k2 - St, 0)
            else:
                a, b, cc = value(St, left, rf[t], q[t], A * vix[t], (rel[t], rl[t]))
                v = n_c * cc - a + b
            pnl[t] = (v - prev) / S0
            prev = v
    return pnl


def cluster_weights(S, vol):
    groups = {"growth": ["leaders_dual", "qqq_tilt", "psc_spy", "psc_qqq"], "premium": ["putwrite", "spike_fade"],
              "trend": ["trend"], "momentum": ["momentum_vs"]}
    W = pd.DataFrame(0.0, index=S.index, columns=S.columns)
    live = [g for g in groups.values() if any(c in S.columns for c in g)]
    for g in live:
        cols = [c for c in g if c in S.columns]
        iv = 1.0 / vol[cols].clip(lower=0.02)
        W[cols] = iv.div(iv.sum(axis=1), axis=0).div(len(live)).values
    # rescale so each group contributes similar volatility: divide by the group's own vol
    return W


SAFE_CAPS = {"spike_fade": 0.25, "momentum_vs": 1.0, "psc_spy": 0.5, "psc_qqq": 0.5}


def combine(S, rf, method, target=None, regime_dial=None, cap=4.0, sleeve_caps=None):
    S = S.fillna(0.0)
    vol = S.rolling(63, min_periods=40).std().shift(1) * np.sqrt(252)
    if method == "equal":
        w = pd.DataFrame(1.0 / S.shape[1], index=S.index, columns=S.columns)
    elif method == "cluster":
        w = cluster_weights(S, vol)
    elif method == "sharpe_design":
        # risk budgets proportional to each sleeve's Sharpe measured on 2008-2016 only
        d = S.loc[:MID]
        sh = (d.mean() / d.std() * np.sqrt(252)).clip(lower=0)
        iv = (sh / vol.clip(lower=0.02))
        w = iv.div(iv.sum(axis=1), axis=0)
    elif method == "sharpe_trailing":
        # walk-forward: risk budgets from each sleeve's trailing 3-year Sharpe (past data only)
        mu = S.rolling(756, min_periods=252).mean().shift(1)
        sd = S.rolling(756, min_periods=252).std().shift(1)
        sh = (mu / sd * np.sqrt(252)).clip(lower=0).fillna(0.1) + 0.1    # shrink toward equal
        iv = sh / vol.clip(lower=0.02)
        w = iv.div(iv.sum(axis=1), axis=0)
    else:
        iv = 1.0 / vol.clip(lower=0.02)
        w = iv.div(iv.sum(axis=1), axis=0)
    # re-weight weekly (Fridays) to keep trading down
    fri = S.index.weekday == 4
    w = w.where(pd.Series(fri, index=S.index), np.nan).ffill().bfill()
    base = (w * S).sum(axis=1)
    lev = pd.Series(1.0, index=S.index)
    if target:
        pv = base.rolling(63, min_periods=40).std().shift(1) * np.sqrt(252)
        lev = (target / pv.clip(lower=0.01)).clip(upper=cap).fillna(1.0)
        lev = lev.where(pd.Series(fri, index=S.index), np.nan).ffill().fillna(1.0)
    if regime_dial is not None:
        lev = lev * regime_dial.reindex(S.index).fillna(1.0)
    W = w.mul(lev, axis=0)
    if sleeve_caps:
        for c, mx in sleeve_caps.items():
            if c in W:
                W[c] = W[c].clip(upper=mx)          # unused weight stays in cash
    combine.last_W = W
    gross = W.abs().sum(axis=1)
    turnover = W.diff().abs().sum(axis=1).fillna(0)
    excess = (W * S).sum(axis=1) - turnover * 5e-4 - (gross - 1).clip(lower=0) * 0.005 / 252
    return rf + excess, lev, gross


def main():
    S, rf, spy_raw, m = sleeves()
    vix, vix3m = m["vix"].reindex(S.index).ffill(), m["vix3m"].reindex(S.index).ffill()
    spy = spy_raw.reindex(S.index)
    spy200 = spy_raw.rolling(200).mean().reindex(S.index)
    dial = pd.Series(1.0, index=S.index)
    dial[spy < spy200] = 0.75
    dial[vix >= vix3m] = 0.5
    dial = dial.shift(1).fillna(1.0)

    # sleeve stats + correlations
    S_all = S
    srows = []
    for c in S_all.columns:
        r = S[c].dropna() + rf.reindex(S[c].dropna().index)
        srows.append({"sleeve": c, **ol.perf(r, 252, rf)})
    sl = pd.DataFrame(srows)
    corr = S.corr().round(2)

    rows, series = [], {}
    S = S.drop(columns=["momentum_ln"])      # replaced by the volatility-scaled version
    configs = [("equal", None, False, 4.0)]
    configs += [(mth, None, False, 4.0) for mth in ["riskpar", "cluster", "sharpe_design", "sharpe_trailing"]]
    configs += [(mth, t, rg, cap) for mth, t, rg, cap in itertools.product(
        ["riskpar", "cluster", "sharpe_design", "sharpe_trailing"], [0.20, 0.25, 0.30, 0.35], [False, True], [4.0, 5.0, 6.0])]
    configs += [("cluster", t, False, cap, "safe") for t, cap in itertools.product([0.30, 0.35, 0.40], [6.0, 8.0])]
    for cfg in configs:
        method, tgt, rg, cap = cfg[:4]
        safe = len(cfg) > 4
        r, lev, gross = combine(S, rf, method, tgt, dial if rg else None, cap=cap, sleeve_caps=SAFE_CAPS if safe else None)
        key = method + (f"_vt{int(tgt * 100)}" if tgt else "") + ("_regime" if rg else "") + (f"_cap{int(cap)}" if tgt else "") + ("_safe" if safe else "")
        series[key] = r
        for pname, (a, b) in {"full": (START, END), "design_2008_16": (START, MID), "test_2017_26": ("2017-01-01", END)}.items():
            x = r.loc[a:b]
            yr = (1 + x).groupby(x.index.year).prod() - 1
            rows.append({"key": key, "period": pname, **ol.perf(x, 252, rf.loc[a:b]),
                         "avg_gross": float(gross.loc[a:b].mean()), "max_gross": float(gross.loc[a:b].max()),
                         "ret_2008": yr.get(2008), "ret_2020": yr.get(2020), "ret_2022": yr.get(2022),
                         "worst_year": float(yr.min()), "pct_years_above_25": float((yr > 0.25).mean())})
    spy_tr = pd.read_csv(ol.DATA / "growth_etf_adj_d.csv", index_col=0, parse_dates=True)
    for b in ["SPY", "QQQ"]:
        r = spy_tr[b].pct_change().reindex(S.index)
        series[b] = r
        for pname, (a, c) in {"full": (START, END), "design_2008_16": (START, MID), "test_2017_26": ("2017-01-01", END)}.items():
            x = r.loc[a:c]
            yr = (1 + x).groupby(x.index.year).prod() - 1
            rows.append({"key": b, "period": pname, **ol.perf(x, 252, rf.loc[a:c]), "ret_2008": yr.get(2008),
                         "ret_2020": yr.get(2020), "ret_2022": yr.get(2022), "worst_year": float(yr.min()),
                         "pct_years_above_25": float((yr > 0.25).mean())})
    g = pd.DataFrame(rows)

    # choose on the design half only: the highest-Sharpe configuration whose design-half CAGR >= 25%
    d = g[(g.period == "design_2008_16") & g.key.str.contains("_vt")]
    ok = d[d.cagr >= 0.25]
    chosen = ok.sort_values("sharpe").iloc[-1]["key"] if len(ok) else d.sort_values("cagr").iloc[-1]["key"]

    ds = d[d.key.str.endswith("_safe")]
    oks = ds[ds.cagr >= 0.25]
    chosen_safe = oks.sort_values("sharpe").iloc[-1]["key"] if len(oks) else ds.sort_values("cagr").iloc[-1]["key"]
    summary = {"chosen_on_design_half": chosen, "chosen_safe_on_design_half": chosen_safe}
    for label, key in [("detail", chosen), ("detail_safe", chosen_safe)]:
        summary[label] = describe(S, rf, dial, key)
    (OUT / "Z_summary.json").write_text(json.dumps(summary, indent=1))
    sl.to_csv(OUT / "Z_sleeves.csv", index=False)
    corr.to_csv(OUT / "Z_corr.csv")
    g.to_csv(OUT / "Z_goal25.csv", index=False)
    pd.DataFrame(series).to_csv(OUT / "Z_series_daily.csv")
    print("chosen:", chosen, "| safe:", chosen_safe)


def describe(S, rf, dial, key):
    mth = key.split("_vt")[0]
    tgt = int(key.split("_vt")[1].split("_")[0]) / 100
    cap = float(key.split("_cap")[1].split("_")[0])
    r, lev, gross = combine(S, rf, mth, tgt, dial if "_regime" in key else None, cap=cap,
                            sleeve_caps=SAFE_CAPS if key.endswith("_safe") else None)
    W = combine.last_W
    fade_w = W["spike_fade"].where(S["spike_fade"].fillna(0) != 0)   # weight on days the fade position is on
    return {"avg_weight_x_nav": W.mean().round(3).to_dict(), "avg_gross": float(gross.mean()),
              "pct_days_at_cap": float((lev >= cap - 1e-9).mean()),
              "yearly": {int(k): round(float(v), 4) for k, v in ((1 + r).groupby(r.index.year).prod() - 1).items()},
              "worst_month": float(((1 + r).groupby([r.index.year, r.index.month]).prod() - 1).min()),
              "worst_day": float(r.min()), "worst_day_date": str(r.idxmin().date()),
              "latest_weights": W.iloc[-1].round(3).to_dict(),
              # stress: VIX futures double in a day while the fade sleeve is at its largest weight
              "stress_vix_futures_x2_loss": float(fade_w.max() * 0.5 * 1.0),
              "stress_vix_futures_x2_loss_avg_weight": float(fade_w.mean() * 0.5)}


if __name__ == "__main__":
    main()
