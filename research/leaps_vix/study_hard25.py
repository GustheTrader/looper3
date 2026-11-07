"""
HARD-ASSET FRONTIER PLAN -- the Goal-25 framework on crypto, hard assets,
resources, energy and space (with SpaceX as the core growth asset).

Data limits (stated up front)
  * BTC daily history on Yahoo starts 2014-09, so the test runs 2015-10 .. 2026-11.
  * SpaceX (SPCX) only listed on 2026-06-12. Five months cannot be backtested, so
    the space sleeve is a chain of public proxies, equal-weighting whatever existed
    on each day: ITA (2006+) and XAR (2011+) aerospace; UFO and ROKT space ETFs
    (2019+); ARKX and Rocket Lab (2021+); XOVR and DXYZ, funds holding pre-IPO SpaceX
    shares (2024+); and SPCX itself from its listing.

Sleeves (daily excess returns over T-bills, net of costs)
  crypto    btc_tilt      BTC while above its own 50-day average, cash otherwise
            eth_tilt      ETH, same rule (from 2017-11)
  frontier  hard_rotation top 3 of GLD/SLV/GDX/GDXJ/XME/COPX/URA/XLE/XOP/OIH/DBC/CPER/PPLT/ITA/XAR/UFO
                          by SPSR (6/9/12-month blend), each held only above its own 50-day average
            space         the space/SpaceX proxy chain, full size above its 50-day average, half below
  trend     trend_hard    long/short trend (1/3/6/12-month blend) on GLD, SLV, DBB, USO, DBC, XLE, GDX, BTC,
                          each at 10% volatility
  premium   psc_gld       sell a 1-month 30/10-delta put spread on GLD, spend the credit on 25-delta calls;
                          priced from the gold volatility index (^GVZ), no skew
            spike_fade    short VIX futures after a spike peaks (from study_vix)
Combination: equal risk to the four groups (inverse 63-day volatility inside each group),
levered to a volatility target with a gross cap and hard per-sleeve limits.
Target chosen on 2015-10 .. 2020-12, tested unchanged on 2021-01 .. 2026-11.
"""
from __future__ import annotations

import itertools
import json
import sys

import numpy as np
import pandas as pd

import options_lib as ol
import study_vix as sv
from study_leadership import regime

sys.path.insert(0, str(ol.HERE.parent / "momentum"))
import momentum_lib as ml  # noqa: E402

OUT = ol.HERE / "results"
START, MID, END = "2015-10-01", "2020-12-31", "2026-11-05"
ROT = ["GLD", "SLV", "GDX", "GDXJ", "XME", "COPX", "URA", "XLE", "XOP", "OIH", "DBC", "CPER", "PPLT", "ITA", "XAR", "UFO"]
SPACE = ["ITA", "XAR", "UFO", "ROKT", "ARKX", "RKLB", "XOVR", "DXYZ", "SPCX"]
TRENDS = ["GLD", "SLV", "DBB", "USO", "DBC", "XLE", "GDX", "BTC-USD"]
GROUPS = {"crypto": ["btc_tilt", "eth_tilt"], "frontier": ["hard_rotation", "space"],
          "trend": ["trend_hard"], "premium": ["psc_gld", "spike_fade"]}
CAPS = {"btc_tilt": 0.35, "eth_tilt": 0.15, "spike_fade": 0.25, "psc_gld": 0.5, "space": 1.0,
        "hard_rotation": 1.5, "trend_hard": 2.5}


def load():
    raw = pd.read_csv(ol.DATA / "hard_assets_adj_d.csv", index_col=0, parse_dates=True)
    raw["SPCX"] = pd.read_csv(ol.DATA / "spcx_d.csv", index_col=0, parse_dates=True)["SPCX"]
    days = raw["SPY"].dropna().index                     # trade on US market days (crypto weekends roll into Monday)
    px = raw.ffill().reindex(days)
    for c in px.columns:                                 # nothing before an asset existed
        px.loc[px.index < raw[c].first_valid_index(), c] = np.nan
    rf = pd.read_csv(ml.DATA / "ff_factors_d.csv", index_col=0, parse_dates=True)["RF"].reindex(days).ffill()
    return px, rf


def tilt(price, ret, rf, low=0.0, cost=1e-3):
    e = regime(price.ffill()).map({True: 1.0, False: low}).where(price.notna(), 0.0)
    return (e * (ret - rf) - e.diff().abs().fillna(0) * cost).where(price.notna(), 0.0)


def sleeves():
    px, rf = load()
    R = px.pct_change(fill_method=None)
    out = {}
    out["btc_tilt"] = tilt(px["BTC-USD"], R["BTC-USD"], rf)
    out["eth_tilt"] = tilt(px["ETH-USD"], R["ETH-USD"], rf)

    # hard-asset rotation with dual momentum
    mpx = px[ROT].resample("ME").last()
    mret = mpx.pct_change(fill_method=None)
    mkt = px["SPY"].resample("ME").last().pct_change()
    score = sum(ml.spsr(mret, mkt, L, 1) for L in (6, 9, 12)) / 3
    rank = score.rank(axis=1, ascending=False)
    me = px.groupby([px.index.year, px.index.month]).tail(1).index
    top = pd.DataFrame(0.0, index=me, columns=ROT)
    held = []
    for d in me:
        k = d + pd.offsets.MonthEnd(0)
        r = rank.loc[k].dropna() if k in rank.index else pd.Series(dtype=float)
        if len(r) < 6:
            continue
        held = [h for h in held if r.get(h, 99) <= 5]
        for c in r.sort_values().index:
            if len(held) >= 3:
                break
            if c not in held:
                held.append(c)
        top.loc[d, held] = 1 / 3
    top = top.reindex(px.index).ffill().shift(1).fillna(0)
    above = (px[ROT] > px[ROT].rolling(50).mean()).shift(1, fill_value=False).astype(float)
    W = top * above
    out["hard_rotation"] = (W * R[ROT].fillna(0)).sum(axis=1) - W.diff().abs().sum(axis=1).fillna(0) * 5e-4 - W.sum(axis=1) * rf
    rot_hist = top.loc[START:].astype(bool).mean().sort_values(ascending=False)

    # space / SpaceX proxy chain: equal weight of whatever exists, then a 50-day tilt
    sp = R[SPACE].where(px[SPACE].shift(1).notna())
    space_ret = sp.mean(axis=1, skipna=True).fillna(0)
    space_px = (1 + space_ret).cumprod()
    out["space"] = tilt(space_px, space_ret, rf, low=0.5, cost=5e-4)

    # hard-asset trend
    T = px[TRENDS]
    XR = T.pct_change(fill_method=None)
    ex = XR.sub(rf, axis=0)
    month_end = px.index.to_series().groupby([px.index.year, px.index.month]).transform("max") == px.index.to_series()
    sig = sum(np.sign(np.log1p(ex).rolling(L, min_periods=int(L * 0.9)).sum()) for L in (21, 63, 126, 252)) / 4
    sig = sig.where(month_end).ffill()
    vol = XR.rolling(63, min_periods=40).std() * np.sqrt(252)
    pos = (sig * (0.10 / vol).clip(upper=2.0)).shift(1)
    n = pos.notna().sum(axis=1).replace(0, np.nan)
    out["trend_hard"] = ((pos * ex).sum(axis=1) / n - pos.diff().abs().sum(axis=1) / n * 3e-4).fillna(0)

    # put spread financing calls on GLD, priced from ^GVZ (flat smile, no skew advantage)
    out["psc_gld"] = psc_flat(px["GLD"], px["^GVZ"] / 100, rf).reindex(px.index).fillna(0)

    # VIX spike fade
    m = ol.load_market()
    fade, _ = sv.v3_fade(sv.panel(m))
    out["spike_fade"] = fade.reindex(px.index).fillna(0)

    S = pd.DataFrame(out).loc[START:END]
    bench = {b: R[b].loc[START:END] for b in ["BTC-USD", "GLD", "XLE", "QQQ", "SPY"]}
    bench["Space proxy (untilted)"] = space_ret.loc[START:END]
    return S, rf.loc[START:END], px, bench, rot_hist


def psc_flat(price, iv, rf, short_put=0.30, long_put=0.10, call=0.25, half_spread=0.04):
    """Monthly put spread sold to buy calls, marked daily with Black-Scholes at the
    index-implied ATM vol (0.9 x GVZ), one flat vol for all strikes."""
    from scipy.stats import norm
    days = price.dropna().index.intersection(iv.dropna().index)
    ends = [d for d in pd.Series(days, index=days).groupby([days.year, days.month]).last()]
    pnl = pd.Series(0.0, index=days)

    def k_for(S, T, r, s, target_call_delta):
        d1 = norm.ppf(np.clip(target_call_delta, 1e-4, 1 - 1e-4))
        return S * np.exp(-(d1 * s * np.sqrt(T) - (r + 0.5 * s * s) * T))

    for d0, d1 in zip(ends[:-1], ends[1:]):
        S0, s0, r0 = price[d0], 0.9 * iv[d0], rf[d0] * 252
        T = max((d1 - d0).days, 1) / 365
        k1, k2, kc = k_for(S0, T, r0, s0, 1 - short_put), k_for(S0, T, r0, s0, 1 - long_put), k_for(S0, T, r0, s0, call)
        P = lambda S, K, t, s, r: ol.bs_price(S, K, t, r, 0.0, s, call=False)
        C = lambda S, K, t, s, r: ol.bs_price(S, K, t, r, 0.0, s, call=True)
        p1, p2, c = P(S0, k1, T, s0, r0), P(S0, k2, T, s0, r0), C(S0, kc, T, s0, r0)
        credit = p1 * (1 - half_spread) - p2 * (1 + half_spread)
        n_c = max(credit, 0) / (c * (1 + half_spread))
        prev = n_c * c - p1 + p2 - half_spread * (p1 + p2 + n_c * c)
        for t in days[(days > d0) & (days <= d1)]:
            left = max((d1 - t).days, 0) / 365
            St = price[t]
            if left <= 0:
                v = n_c * max(St - kc, 0) - max(k1 - St, 0) + max(k2 - St, 0)
            else:
                st, rt = 0.9 * iv[t], rf[t] * 252
                v = n_c * C(St, kc, left, st, rt) - P(St, k1, left, st, rt) + P(St, k2, left, st, rt)
            pnl[t] = (v - prev) / S0
            prev = v
    return pnl


def combine(S, rf, target=None, cap=4.0, caps=None, method="cluster"):
    S = S.fillna(0.0)
    vol = S.rolling(63, min_periods=40).std().shift(1) * np.sqrt(252)
    live = (S.rolling(63, min_periods=40).std().shift(1) > 0)
    w = pd.DataFrame(0.0, index=S.index, columns=S.columns)
    if method == "cluster":
        for cols in GROUPS.values():
            iv = (1.0 / vol[cols].clip(lower=0.02)).where(live[cols], 0.0)
            w[cols] = iv.div(iv.sum(axis=1).replace(0, np.nan), axis=0).fillna(0).values
        ngroups = sum((w[cols].sum(axis=1) > 0).astype(int) for cols in GROUPS.values()).replace(0, np.nan)
        w = w.div(ngroups, axis=0).fillna(0)
    else:
        iv = (1.0 / vol.clip(lower=0.02)).where(live, 0.0)
        w = iv.div(iv.sum(axis=1), axis=0).fillna(0)
    fri = pd.Series(S.index.weekday == 4, index=S.index)
    w = w.where(fri, np.nan).ffill().fillna(0)
    base = (w * S).sum(axis=1)
    lev = pd.Series(1.0, index=S.index)
    if target:
        pv = base.rolling(63, min_periods=40).std().shift(1) * np.sqrt(252)
        lev = (target / pv.clip(lower=0.01)).clip(upper=cap).fillna(1.0).where(fri, np.nan).ffill().fillna(1.0)
    W = w.mul(lev, axis=0)
    if caps:
        for c, mx in caps.items():
            W[c] = W[c].clip(upper=mx)
    gross = W.abs().sum(axis=1)
    to = W.diff().abs().sum(axis=1).fillna(0)
    excess = (W * S).sum(axis=1) - to * 5e-4 - (gross - 1).clip(lower=0) * 0.005 / 252
    combine.last_W = W
    return rf + excess, lev, gross


def monthly(r):
    return (1 + r.fillna(0)).resample("ME").prod() - 1


def main():
    S, rf, px, bench, rot_hist = sleeves()
    rf_m = monthly(rf)
    sl_stats = {c: ol.perf(S[c] + rf, 252, rf) for c in S.columns}
    corr = S.corr().round(2)

    cands, daily = {}, {}
    for mth, vt, cap in itertools.product(["cluster", "riskpar"], [0.20, 0.25, 0.30, 0.35, 0.40], [3.0, 4.0, 6.0]):
        r, lev, gross = combine(S, rf, vt, cap, CAPS, mth)
        key = f"{mth}_vt{int(vt * 100)}_cap{int(cap)}"
        cands[key], daily[key] = monthly(r), r
    C = pd.DataFrame(cands)
    design, test = C.loc[:MID], C.loc["2021-01-01":]
    cagr = lambda x: (1 + x).prod() ** (12 / len(x)) - 1
    dd = lambda x: float(((1 + x).cumprod() / (1 + x).cumprod().cummax() - 1).min())
    tab = pd.DataFrame({k: {"design_cagr": cagr(design[k]), "test_cagr": cagr(test[k]), "full_cagr": cagr(C[k]),
                            "design_dd": dd(design[k]), "full_dd": dd(C[k]),
                            "design_sharpe": float(((design[k] - rf_m.reindex(design.index)).mean() / (design[k] - rf_m.reindex(design.index)).std()) * np.sqrt(12)),
                            "sharpe": float(((C[k] - rf_m).mean() / (C[k] - rf_m).std()) * np.sqrt(12))} for k in C}).T
    # same rule as Goal-25: highest design-half Sharpe among settings with design CAGR >= 25%
    ok = tab[tab.design_cagr >= 0.25]
    chosen = (ok.sort_values("design_sharpe", ascending=False).index[0] if len(ok)
              else tab.sort_values("design_cagr", ascending=False).index[0])

    # walk-forward (3-yr look-back), same rule as Goal-25
    picks, wf = {}, []
    for y in range(2019, 2027):
        h = C.loc[:f"{y - 1}-12-31"].iloc[-36:]
        if len(h) < 36:
            continue
        c_ = (1 + h).prod() ** (12 / len(h)) - 1
        d_ = h.apply(dd)
        okk = c_[d_ >= -0.30]
        picks[y] = (okk if len(okk) else c_).idxmax()
        wf.append(C.loc[f"{y}-01-01":f"{y}-12-31", picks[y]])
    wf = pd.concat(wf)

    unl, _, _ = combine(S, rf, None, 1.0, None, "cluster")
    plan = C[chosen]
    series = {"Frontier Plan": plan, "Walk-forward (3-yr look-back)": wf, "Unlevered core": monthly(unl)}
    series |= {("BTC" if k == "BTC-USD" else k): monthly(v) for k, v in bench.items()}
    P = pd.DataFrame(series)

    def stats(r, start=None):
        r = r.dropna()
        r = r.loc[start:] if start else r
        yr = (1 + r).groupby(r.index.year).prod() - 1
        p = ol.perf(r, 12, rf_m.reindex(r.index))
        return {**{k: p[k] for k in ["cagr", "ann_vol", "sharpe", "sortino", "max_dd", "calmar", "worst"]},
                "pct_years_25": float((yr[(yr.index > 2015) & (yr.index < 2026)] > 0.25).mean()),
                "worst_year": float(yr[yr.index > 2015].min()), "best_year": float(yr.max())}

    def edge(r, b):
        x = pd.concat([r, b], axis=1, keys=["r", "b"]).dropna()
        er, eb = x.r - rf_m.reindex(x.index), x.b - rf_m.reindex(x.index)
        beta = float(np.cov(er, eb)[0, 1] / eb.var())
        res = er - beta * eb
        act = x.r - x.b
        r12 = (1 + x).rolling(12).apply(np.prod, raw=True) - 1
        up, dn = x.b > 0, x.b < 0
        return {"alpha_ann": float(res.mean() * 12), "alpha_tstat": float(res.mean() / res.std() * np.sqrt(len(res))),
                "beta": beta, "excess_cagr": float(cagr(x.r) - cagr(x.b)), "info_ratio": float(act.mean() / act.std() * np.sqrt(12)),
                "up_capture": float(x.r[up].mean() / x.b[up].mean()), "down_capture": float(x.r[dn].mean() / x.b[dn].mean()),
                "pct_12m_windows_ahead": float((r12.r > r12.b).dropna().mean())}

    rng = np.random.default_rng(11)
    boot = {}
    for k in ["Frontier Plan", "BTC", "GLD", "QQQ"]:
        r = P[k].loc["2021-01-01":].dropna().values      # test period only: excludes the 2016-17 crypto boom
        res = []
        for _ in range(10000):
            path = []
            while len(path) < 60:
                s0 = rng.integers(0, len(r) - 6)
                path.extend(r[s0:s0 + 6])
            eq = np.cumprod(1 + np.array(path[:60]))
            res.append((eq[-1] ** (12 / 60) - 1, float((eq / np.maximum.accumulate(eq) - 1).min())))
        res = np.array(res)
        boot[k] = {"cagr_pct": {str(p): float(np.percentile(res[:, 0], p)) for p in [5, 25, 50, 75, 95]},
                   "p_cagr_above_25": float((res[:, 0] > 0.25).mean()), "p_loss": float((res[:, 0] < 0).mean()),
                   "dd_median": float(np.median(res[:, 1])),
                   "hist": np.histogram(np.clip(res[:, 0], -0.3, 0.9), bins=np.arange(-0.30, 0.91, 0.05))[0].tolist()}
    st = stats(plan, "2021-01-01")                          # expectations from the test period only
    rf_avg = float(rf_m.loc["2021-01-01":].mean() * 12)
    hair = [{"haircut": h, "sharpe": st["sharpe"] * (1 - h),
             "expected_cagr": rf_avg + st["sharpe"] * (1 - h) * st["ann_vol"] - st["ann_vol"] ** 2 / 2} for h in [0, 0.25, 0.5]]

    W = combine(S, rf, float(chosen.split("_vt")[1].split("_")[0]) / 100, float(chosen.split("_cap")[1]), CAPS,
                chosen.split("_vt")[0])[0]
    Wm = combine.last_W
    rr = W.loc[START:]
    roll = {}
    for k in ["Frontier Plan", "BTC", "GLD", "QQQ"]:
        r = P[k].dropna()
        ex = r - rf_m.reindex(r.index)
        roll[k + " CAGR"] = (1 + r).rolling(36).apply(np.prod, raw=True) ** (1 / 3) - 1
        roll[k + " Sharpe"] = ex.rolling(36).mean() / ex.rolling(36).std() * np.sqrt(12)

    spcx = px["SPCX"].dropna()
    spcx_stats = {"first_date": str(spcx.index[0].date()), "first_close": float(spcx.iloc[0]), "last_close": float(spcx.iloc[-1]),
                  "return_since_listing": float(spcx.iloc[-1] / spcx.iloc[0] - 1),
                  "ann_vol": float(spcx.pct_change().std() * np.sqrt(252)),
                  "max_dd": float((spcx / spcx.cummax() - 1).min())}
    out = {"chosen": chosen, "design_table": tab.round(4).to_dict("index"),
           "perf_full": {k: stats(v) for k, v in series.items()},
           "perf_test": {k: stats(v, "2021-01-01") for k, v in series.items()},
           "edges": {f"Frontier Plan vs {b}": edge(plan, P[b]) for b in ["BTC", "GLD", "QQQ", "SPY"]},
           "wf_picks_3y": {str(k): v for k, v in picks.items()}, "bootstrap": boot,
           "bootstrap_bins": np.arange(-0.30, 0.91, 0.05).round(3).tolist(), "haircut": hair,
           "sleeve_perf": {k: {x: v[x] for x in ["cagr", "ann_vol", "sharpe", "max_dd"]} for k, v in sl_stats.items()},
           "corr": corr.to_dict(), "rotation_share_held": rot_hist.round(3).to_dict(),
           "latest_weights": Wm.iloc[-1].round(3).to_dict(), "avg_weights": Wm.loc[START:].mean().round(3).to_dict(),
           "avg_gross": float(Wm.abs().sum(axis=1).mean()),
           # crash day like 12 Mar 2020: BTC -40%, ETH -45% in one day
           "stress_crypto_crash_day_max": float(Wm["btc_tilt"].max() * 0.40 + Wm["eth_tilt"].max() * 0.45),
           "stress_crypto_crash_day_avg": float(Wm["btc_tilt"].loc[START:].mean() * 0.40 + Wm["eth_tilt"].loc[START:].mean() * 0.45),
           "stress_vix_x2_day": float(Wm["spike_fade"].max() * 0.5),
           "worst_day": float(rr.min()), "worst_day_date": str(rr.idxmin().date()), "spacex": spcx_stats}
    (OUT / "H_summary.json").write_text(json.dumps(out, indent=1, default=float))
    P.to_csv(OUT / "H_plan_monthly.csv")
    pd.DataFrame({k: monthly(S[k] + rf) for k in S.columns}).to_csv(OUT / "H_sleeves_monthly.csv")
    pd.DataFrame(roll).to_csv(OUT / "H_rolling36.csv")
    C.to_csv(OUT / "H_candidates_monthly.csv")

    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame(sl_stats).T[["cagr", "ann_vol", "sharpe", "max_dd"]])
    print(corr)
    print(tab.sort_values("sharpe", ascending=False).head(12))
    print("chosen:", chosen)
    print(pd.DataFrame(out["perf_full"]).T[["cagr", "ann_vol", "sharpe", "max_dd", "worst_year", "pct_years_25"]])
    print(pd.DataFrame(out["perf_test"]).T[["cagr", "sharpe", "max_dd"]])
    print(pd.DataFrame(out["edges"]).T)
    print({k: (round(v["cagr_pct"]["50"], 3), round(v["p_cagr_above_25"], 3), round(v["p_loss"], 3)) for k, v in boot.items()})
    print(hair, "\nrotation held:", out["rotation_share_held"], "\nweights:", out["avg_weights"], "gross", out["avg_gross"])
    print("stress crypto crash max/avg:", out["stress_crypto_crash_day_max"], out["stress_crypto_crash_day_avg"], "VIX x2:", out["stress_vix_x2_day"], "worst day", out["worst_day"], out["worst_day_date"])
    print("SpaceX:", spcx_stats, "\nWF picks:", picks)


if __name__ == "__main__":
    main()
