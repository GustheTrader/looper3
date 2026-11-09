"""
Goal-25 plan: rolling-window (walk-forward) optimisation and the deliverable edge.

1. Walk-forward: every January from 2011, look back over the previous 3 (or 5)
   years only, pick the configuration (weighting method x volatility target x
   leverage cap, always with the sleeve safety limits) with the highest
   trailing CAGR whose trailing max drawdown is no worse than -30%, and trade
   it for the next 12 months. Nothing is chosen with future data.
2. Rolling 36-month CAGR and Sharpe for the plan, QQQ and SPY.
3. Edge vs QQQ and SPY: alpha/beta, information ratio, up/down capture, share
   of rolling 12-month windows beating QQQ, share of years above 25%.
4. Block bootstrap of 5-year outcomes (6-month blocks, 10,000 paths).
5. Haircut table: what the plan returns if its live Sharpe is 25% / 50% lower.
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

import study_goal25 as g25
import options_lib as ol

OUT = ol.HERE / "results"
FIXED = "cluster_vt40_cap8_safe"


def monthly(r: pd.Series) -> pd.Series:
    return (1 + r.fillna(0)).resample("ME").prod() - 1


def max_dd(r):
    eq = (1 + r).cumprod()
    return float((eq / eq.cummax() - 1).min())


def main():
    S, rf, spy_raw, m = g25.sleeves()
    S = S.drop(columns=["momentum_ln"])
    rf_m = monthly(rf)
    # candidate configurations (all with safety limits)
    cands = {}
    for mth, vt, cap in itertools.product(["cluster", "riskpar", "sharpe_trailing"], [0.25, 0.30, 0.35, 0.40], [6.0, 8.0]):
        r, _, _ = g25.combine(S, rf, mth, vt, None, cap=cap, sleeve_caps=g25.SAFE_CAPS)
        cands[f"{mth}_vt{int(vt * 100)}_cap{int(cap)}_safe"] = monthly(r)
    C = pd.DataFrame(cands)
    fixed = C[FIXED]

    wf = {}
    for look in [36, 60]:
        out, picks = [], {}
        for y in range(2011, 2027):
            hist = C.loc[:f"{y - 1}-12-31"].iloc[-look:]
            if len(hist) < look:
                continue
            cagr = (1 + hist).prod() ** (12 / len(hist)) - 1
            dd = hist.apply(max_dd)
            ok = cagr[dd >= -0.30]
            pick = (ok if len(ok) else cagr).idxmax()
            picks[y] = pick
            out.append(C.loc[f"{y}-01-01":f"{y}-12-31", pick])
        wf[look] = (pd.concat(out), picks)

    px = pd.read_csv(ol.DATA / "growth_etf_adj_d.csv", index_col=0, parse_dates=True)
    qqq = monthly(px["QQQ"].pct_change().reindex(S.index))
    spy = monthly(px["SPY"].pct_change().reindex(S.index))
    unsafe, _, _ = g25.combine(S, rf, "cluster", 0.35, None, cap=6.0)
    unl, _, _ = g25.combine(S, rf, "cluster", None, None)
    series = {"Goal-25 Safe": fixed, "Walk-forward (3-yr look-back)": wf[36][0], "Walk-forward (5-yr look-back)": wf[60][0],
              "No sleeve limits (unsafe)": monthly(unsafe), "Unlevered core (Sharpe ~1.0)": monthly(unl),
              "QQQ": qqq, "SPY": spy}
    sleeve_m = {c: monthly(S[c] + rf) for c in S.columns}

    def stats(r, start=None):
        r = r.dropna()
        if start:
            r = r.loc[start:]
        yr = (1 + r).groupby(r.index.year).prod() - 1
        p = ol.perf(r, 12, rf_m.reindex(r.index))
        return {**{k: p[k] for k in ["cagr", "ann_vol", "sharpe", "sortino", "max_dd", "calmar", "worst"]},
                "pct_years_25": float((yr[yr.index < 2026] > 0.25).mean()), "worst_year": float(yr.min()), "best_year": float(yr.max())}

    perf_full = {k: stats(v) for k, v in series.items()}
    perf_wf_window = {k: stats(v, "2012-01-01") for k, v in series.items()}

    # edge vs benchmarks (monthly, full period)
    def edge(r, b):
        x = pd.concat([r, b], axis=1, keys=["r", "b"]).dropna()
        ex_r, ex_b = x.r - rf_m.reindex(x.index), x.b - rf_m.reindex(x.index)
        beta = float(np.cov(ex_r, ex_b)[0, 1] / ex_b.var())
        alpha = float((ex_r.mean() - beta * ex_b.mean()) * 12)
        resid = ex_r - beta * ex_b
        act = x.r - x.b
        r12 = (1 + x).rolling(12).apply(np.prod, raw=True) - 1
        up, dn = x.b > 0, x.b < 0
        return {"alpha_ann": alpha, "beta": beta, "r2": float(np.corrcoef(ex_r, ex_b)[0, 1] ** 2),
                "excess_cagr": float((1 + x.r).prod() ** (12 / len(x)) - (1 + x.b).prod() ** (12 / len(x))),
                "info_ratio": float(act.mean() / act.std() * np.sqrt(12)),
                "up_capture": float(x.r[up].mean() / x.b[up].mean()), "down_capture": float(x.r[dn].mean() / x.b[dn].mean()),
                "pct_12m_windows_ahead": float((r12.r > r12.b).dropna().mean()),
                "alpha_tstat": float(resid.mean() / resid.std() * np.sqrt(len(resid)))}
    edges = {f"{k} vs {b}": edge(series[k], series[b]) for k in ["Goal-25 Safe", "Walk-forward (3-yr look-back)"] for b in ["QQQ", "SPY"]}

    # rolling 36-month CAGR and Sharpe
    roll = {}
    for k in ["Goal-25 Safe", "QQQ", "SPY"]:
        r = series[k].dropna()
        ex = r - rf_m.reindex(r.index)
        roll[k + " CAGR"] = (1 + r).rolling(36).apply(np.prod, raw=True) ** (1 / 3) - 1
        roll[k + " Sharpe"] = ex.rolling(36).mean() / ex.rolling(36).std() * np.sqrt(12)
    roll = pd.DataFrame(roll)

    # block bootstrap of 5-year outcomes
    rng = np.random.default_rng(7)
    boot = {}
    for k in ["Goal-25 Safe", "QQQ", "SPY"]:
        r = series[k].dropna().values
        n, block, horizon = len(r), 6, 60
        res = []
        for _ in range(10000):
            path = []
            while len(path) < horizon:
                s0 = rng.integers(0, n - block)
                path.extend(r[s0:s0 + block])
            path = np.array(path[:horizon])
            eq = np.cumprod(1 + path)
            res.append((eq[-1] ** (12 / horizon) - 1, float((eq / np.maximum.accumulate(eq) - 1).min())))
        res = np.array(res)
        boot[k] = {"cagr_pct": {str(p): float(np.percentile(res[:, 0], p)) for p in [5, 10, 25, 50, 75, 90, 95]},
                   "p_cagr_above_25": float((res[:, 0] > 0.25).mean()), "p_cagr_above_15": float((res[:, 0] > 0.15).mean()),
                   "p_loss": float((res[:, 0] < 0).mean()), "dd_median": float(np.median(res[:, 1])),
                   "dd_5pct": float(np.percentile(res[:, 1], 5)),
                   "hist": np.histogram(res[:, 0], bins=np.arange(-0.15, 0.61, 0.025))[0].tolist()}
    bins = np.arange(-0.15, 0.61, 0.025).round(3).tolist()

    # Sharpe haircut table (same volatility, Sharpe cut)
    st = perf_full["Goal-25 Safe"]
    rf_avg = float(rf_m.mean() * 12)
    hair = []
    for h in [0.0, 0.25, 0.5]:
        sr = st["sharpe"] * (1 - h)
        arith = rf_avg + sr * st["ann_vol"]
        hair.append({"haircut": h, "sharpe": sr, "expected_cagr": arith - st["ann_vol"] ** 2 / 2})
    # same table at a lower volatility target (35%, cap 6)
    st2 = stats(C["cluster_vt35_cap6_safe"])
    for h in [0.0, 0.25, 0.5]:
        sr = st2["sharpe"] * (1 - h)
        hair.append({"haircut": h, "sharpe": sr, "expected_cagr": rf_avg + sr * st2["ann_vol"] - st2["ann_vol"] ** 2 / 2,
                     "version": "vt35_cap6"})

    res = {"perf_full": perf_full, "perf_since_2012": perf_wf_window, "edges": edges,
           "wf_picks_3y": {str(k): v for k, v in wf[36][1].items()}, "wf_picks_5y": {str(k): v for k, v in wf[60][1].items()},
           "bootstrap": boot, "bootstrap_bins": bins, "haircut": hair,
           "sleeve_perf": {k: stats(v) for k, v in sleeve_m.items()}}
    (OUT / "Z_wf_edge.json").write_text(json.dumps(res, indent=1, default=float))
    pd.DataFrame(series).to_csv(OUT / "Z_plan_monthly.csv")
    pd.DataFrame(sleeve_m).to_csv(OUT / "Z_sleeves_monthly.csv")
    roll.to_csv(OUT / "Z_rolling36.csv")
    C.to_csv(OUT / "Z_candidates_monthly.csv")

    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame(perf_full).T)
    print(pd.DataFrame(perf_wf_window).T[["cagr", "sharpe", "max_dd"]])
    print(pd.DataFrame(edges).T)
    print("WF 3y picks", wf[36][1]); print("WF 5y picks", wf[60][1])
    for k, b in boot.items():
        print(k, {x: round(v, 3) for x, v in b["cagr_pct"].items()}, "P>25%", round(b["p_cagr_above_25"], 3),
              "P<0", round(b["p_loss"], 3), "DD med", round(b["dd_median"], 3))
    print(pd.DataFrame(hair))


if __name__ == "__main__":
    main()
