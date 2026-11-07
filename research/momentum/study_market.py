"""
CONCEPT A -- Long/Short momentum across the ENTIRE market, and its best rolling window.

A1  Survivorship-free benchmark: Ken French "10 portfolios formed on prior
    12-2 return" (every NYSE/AMEX/NASDAQ stock). Winners-minus-Losers = P10 - P1.
A2  Time-series ("absolute") momentum on the market itself: long the market when
    its own trailing L-month excess return is > 0, short (or cash) otherwise.
A3  Cross-sectional stock momentum window scan on S&P 500 stocks:
    lookback L x skip S x holding H, long top decile / short bottom decile.
    Bias calibration: the 12-1 survivor result is compared to the French
    12-2 decile result over the same months.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

import momentum_lib as ml

OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)
START, END = "1996-10-31", "2026-09-30"   # 30 full years (French data ends 2026-09)
LOOKBACKS = [1, 2, 3, 4, 6, 9, 12, 15, 18, 24]
SKIPS = [0, 1]
HOLDS = [1, 3, 6, 12]
STOCK_COST_BPS = 10   # one-way, large caps


def a1_french_benchmark(rf):
    p = ml.load_french("mom10_m")
    wml = (p["Hi PRIOR"] - p["Lo PRIOR"]).loc[START:END]
    umd = ml.load_french("ff_mom_m").iloc[:, 0].loc[START:END]
    mkt, _ = ml.market_monthly()
    rows = {
        "French WML decile 12-2 (P10-P1)": ml.perf(wml),
        "French UMD factor": ml.perf(umd),
        "US market (buy & hold)": ml.perf(mkt.loc[START:END], rf),
    }
    # Survivorship-free window comparison: short (1-0), intermediate (12-2), long (60-13)
    for name, f in [("1-0 (last month)", "rev10_m"), ("60-13 (5 years)", "ltr10_m")]:
        q = ml.load_french(f)
        rows[f"French Hi-Lo decile {name}"] = ml.perf((q["Hi PRIOR"] - q["Lo PRIOR"]).loc[START:END])
    # By decade -- momentum is not stable through time
    for a, b in [("1996-10", "2006-09"), ("2006-10", "2016-09"), ("2016-10", "2026-09")]:
        rows[f"French WML {a[:4]}-{b[:4]}"] = ml.perf(wml.loc[a:b])
    return pd.DataFrame(rows).T, wml


def a2_time_series(rf):
    mkt, rf_all = ml.market_monthly()
    ex = (mkt - rf_all).to_frame("m")
    rows, series = [], {}
    for L, S in itertools.product(LOOKBACKS, SKIPS):
        sig = np.sign(ml.cum_return(ex, L, S)["m"])
        for mode in ["long_short", "long_cash"]:
            pos = sig if mode == "long_short" else sig.clip(lower=0)
            # position decided at end of t, earns market excess at t+1, plus cash rf
            r = (pos.shift(1) * ex["m"] + rf_all).loc[START:END]
            trn = pos.diff().abs().shift(1).loc[START:END]
            r = r - trn * 2 / 1e4   # 2 bps per unit change (index futures/ETF)
            p = ml.perf(r, rf)
            rows.append({"L": L, "S": S, "mode": mode, **p})
            series[(L, S, mode)] = r
    return pd.DataFrame(rows), series


def a3_stock_scan(rf, french_wml):
    ret = ml.yahoo_monthly("sp500_close_d.csv")
    rows, nets = [], {}
    for L, S, H in itertools.product(LOOKBACKS, SKIPS, HOLDS):
        sig = ml.cum_return(ret, L, S)
        w = ml.rank_weights(sig, frac=0.10)
        bt = ml.backtest(w, ret, H=H, cost_bps=STOCK_COST_BPS)
        g, n = bt.gross.loc[START:END], bt.net.loc[START:END]
        pg, pn = ml.perf(g), ml.perf(n)
        rows.append({"L": L, "S": S, "H": H,
                     "sharpe_gross": pg["sharpe"], "sharpe_net": pn["sharpe"],
                     "ann_ret_net": pn["ann_ret"], "ann_vol": pn["ann_vol"],
                     "t_net": pn["t_stat"], "max_dd": pn["max_dd"],
                     "worst_month": pn["worst_month"], "skew": pn["skew"],
                     "turnover_m": bt.turnover.loc[START:END].mean(),
                     "sharpe_net_1st15y": ml.perf(n.loc[:"2011-09"])["sharpe"],
                     "sharpe_net_2nd15y": ml.perf(n.loc["2011-10":])["sharpe"]})
        nets[f"L{L}_S{S}_H{H}"] = n
    grid = pd.DataFrame(rows)

    # --- bias calibration: survivor 12-1 deciles vs survivorship-free French 12-2
    surv = nets["L12_S1_H1"]
    both = pd.concat([surv, french_wml], axis=1, keys=["sp500_survivors", "french"]).dropna()
    calib = {
        "corr": both.corr().iloc[0, 1],
        "sharpe_survivors": ml.perf(both["sp500_survivors"])["sharpe"],
        "sharpe_french": ml.perf(both["french"])["sharpe"],
        "ann_ret_survivors": ml.perf(both["sp500_survivors"])["ann_ret"],
        "ann_ret_french": ml.perf(both["french"])["ann_ret"],
    }

    # --- stability: do the windows that worked in years 1-15 also work in 16-30?
    stab = grid[["sharpe_net_1st15y", "sharpe_net_2nd15y"]].corr(method="spearman").iloc[0, 1]

    # --- multiple-testing: deflated Sharpe of the single best cell
    sr_m = (grid["sharpe_net"] / np.sqrt(12)).to_numpy()
    best = grid.loc[grid["sharpe_net"].idxmax()]
    best_key = f"L{int(best.L)}_S{int(best.S)}_H{int(best.H)}"
    dsr = ml.deflated_sharpe(nets[best_key], sr_m)

    # --- "plateau" pick: average Sharpe of each cell and its lookback neighbours
    grid = grid.sort_values(["S", "H", "L"])
    grid["sharpe_net_smoothed"] = grid.groupby(["S", "H"])["sharpe_net"].transform(
        lambda s: s.rolling(3, center=True, min_periods=2).mean())

    # --- walk-forward honest selection
    wf, picks = ml.walk_forward(nets, train=120, min_train=60, every=12, start="2001-10-31")
    wf_perf = ml.perf(wf.loc[:END])
    bench = ml.perf(nets["L12_S1_H1"].loc[wf.index[0]:END])

    # --- volatility-scaled 12-1 (Barroso & Santa-Clara 2015 crash fix)
    base = nets["L12_S1_H1"]
    vol = base.rolling(12, min_periods=6).std().shift(1) * np.sqrt(12)
    scaled = (base * (0.12 / vol).clip(upper=2.0)).loc[START:END]

    extra = {
        "bias_calibration": calib,
        "rank_stability_spearman_1st_vs_2nd_half": stab,
        "best_cell": best_key,
        "best_cell_sharpe_net": best.sharpe_net,
        "deflated_sharpe_prob_best_cell": dsr,
        "n_configs_tested": len(grid),
        "walk_forward": wf_perf,
        "walk_forward_benchmark_12_1_same_period": bench,
        "walk_forward_pick_counts": picks.value_counts().to_dict(),
        "vol_scaled_12_1": ml.perf(scaled),
        "plain_12_1": ml.perf(base.loc[START:END]),
    }
    return grid, nets, wf, picks, extra


def main():
    _, rf = ml.market_monthly()
    a1, wml = a1_french_benchmark(rf)
    a1.to_csv(OUT / "A1_french_benchmark.csv")
    a2, a2s = a2_time_series(rf)
    a2.to_csv(OUT / "A2_market_time_series_grid.csv", index=False)
    grid, nets, wf, picks, extra = a3_stock_scan(rf, wml)
    grid.to_csv(OUT / "A3_stock_window_grid.csv", index=False)
    pd.DataFrame({"wf_return": wf, "pick": picks}).to_csv(OUT / "A3_walk_forward.csv")
    pd.DataFrame({k: nets[k] for k in ["L12_S1_H1", extra["best_cell"]]}
                 | {"french_wml": wml}).to_csv(OUT / "A3_key_series.csv")
    pd.DataFrame({f"L{k[0]}_S{k[1]}_{k[2]}": v for k, v in a2s.items()}).to_csv(OUT / "A2_series.csv")
    (OUT / "A_summary.json").write_text(json.dumps(extra, indent=2, default=float))

    pd.set_option("display.width", 200, "display.float_format", "{:.3f}".format)
    print("== A1 French benchmark\n", a1[["ann_ret", "ann_vol", "sharpe", "t_stat", "max_dd", "worst_month"]])
    print("\n== A2 market time-series momentum, top 8 by Sharpe")
    print(a2.sort_values("sharpe", ascending=False).head(8)[["L", "S", "mode", "cagr", "sharpe", "max_dd"]])
    print("buy&hold sharpe:", a1.loc["US market (buy & hold)", "sharpe"])
    print("\n== A3 stock grid, top 12 by net Sharpe")
    print(grid.sort_values("sharpe_net", ascending=False).head(12)[
        ["L", "S", "H", "sharpe_gross", "sharpe_net", "sharpe_net_smoothed", "ann_ret_net", "max_dd",
         "turnover_m", "sharpe_net_1st15y", "sharpe_net_2nd15y"]])
    print(json.dumps(extra, indent=1, default=float))


if __name__ == "__main__":
    main()
