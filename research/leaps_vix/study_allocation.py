"""
CAPITAL ALLOCATION & RISK -- combine the sleeves into one account, 2007-04 .. latest.

Sleeves (monthly returns, all net of costs)
    core(lam)   SPY LEAPS (0.80 delta, 1.5y, roll at 6m) + monthly 0.25-delta short calls,
                delta exposure = lam x NAV; spare cash in T-bills       (study_leaps.simulate)
    tail        OTM VIX calls, strike 1.3x future, take profit at 5x; spend b of NAV per month
    fade        0.5x short VIX futures after a spike has peaked (V3);  weight wf of NAV
    boost       "buy the spike": while the V3 'spike over' signal is on at a month end,
                run the core at lam + 0.5 for the next month
Benchmarks: SPY shares, SPY LEAPS-only, SPY covered call (shares + calls).

Every combination of lam x b x wf x boost is evaluated; the paper reports the
efficient set and how each sleeve changes return, drawdown and tail loss.
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

import options_lib as ol
from study_leaps import Cfg, month_ends, simulate

OUT = ol.HERE / "results"
START = "2007-04-30"


def core_series(m):
    dates = [d for d in month_ends(m["raw"].index) if d >= pd.Timestamp("2006-12-01")]
    tg = {d: ["SPY"] for d in dates}
    out = {}
    for lam in [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
        r, ex = simulate(m, tg, Cfg(lam=lam), sector=False)
        out[f"core_{lam}"] = r
        out[f"capital_{lam}"] = pd.Series(ex["avg_capital_in_options"], index=r.index)
    for name, cfg in [("spy_shares", Cfg(use_leaps=False, sell_calls=False)),
                      ("spy_leaps_only", Cfg(sell_calls=False)),
                      ("spy_covered_call", Cfg(use_leaps=False))]:
        out[name] = simulate(m, tg, cfg, sector=False)[0]
    df = pd.DataFrame(out)
    df.index = df.index + pd.offsets.MonthEnd(0)
    return df


def tail_pnl(index):
    t = pd.read_csv(OUT / "V_tail_calls_K1.3_tp5.0.csv", parse_dates=["buy", "exit"])
    spend = t.groupby(t.buy + pd.offsets.MonthEnd(0)).size().astype(float)
    # bought at a month end -> the cost belongs to the NEXT month's return
    spend.index = spend.index + pd.offsets.MonthEnd(1)
    got = t.groupby(t.exit + pd.offsets.MonthEnd(0)).proceeds.sum()
    return (got.reindex(index).fillna(0) - spend.reindex(index).fillna(0))


def main():
    m = ol.load_market()
    core = core_series(m)
    core.to_csv(OUT / "A_core_series.csv")
    idx = core.loc[START:].index
    vm = pd.read_csv(OUT / "V_monthly.csv", index_col=0, parse_dates=True).reindex(idx).fillna(0)
    sigd = pd.read_csv(OUT / "V_signals_daily.csv", index_col=0, parse_dates=True)
    boost_sig = sigd.V3_signal.astype(bool).groupby(sigd.index + pd.offsets.MonthEnd(0)).last()
    boost_sig = boost_sig.shift(1).reindex(idx).fillna(False).astype(bool)   # signal at prior month end
    tail = tail_pnl(idx)
    rf_m = (m["rf"].resample("ME").last() / 12).reindex(idx)

    rows, keep = [], {}
    for lam, b, wf, boost in itertools.product([0.75, 1.0, 1.25, 1.5], [0.0, 0.0025, 0.005, 0.01],
                                               [0.0, 0.1, 0.2], [False, True]):
        base = core[f"core_{lam}"].reindex(idx)
        if boost:
            hi = core[f"core_{min(lam + 0.5, 2.0)}"].reindex(idx)
            base = base.where(~boost_sig, hi)
        r = base + b * tail + wf * vm["V3_spike_fade_short"]
        p = ol.perf(r, 12, rf_m)
        key = f"lam{lam}_tail{b}_fade{wf}_{'boost' if boost else 'noboost'}"
        yr = (1 + r).groupby(r.index.year).prod() - 1
        rows.append({"key": key, "lam": lam, "tail_budget_m": b, "fade_w": wf, "boost": boost, **p,
                     "ret_2008": yr.get(2008), "ret_2020": yr.get(2020), "ret_2022": yr.get(2022),
                     "capital_in_options": float(core[f"capital_{lam}"].iloc[-1]) + b})
        keep[key] = r
    g = pd.DataFrame(rows)
    bench = {}
    for name in ["spy_shares", "spy_leaps_only", "spy_covered_call", "core_1.0"]:
        r = core[name].reindex(idx)
        yr = (1 + r).groupby(r.index.year).prod() - 1
        bench[name] = ol.perf(r, 12, rf_m) | {"ret_2008": yr.get(2008), "ret_2020": yr.get(2020), "ret_2022": yr.get(2022)}
    g.to_csv(OUT / "A_allocation_grid.csv", index=False)
    pd.DataFrame(keep).to_csv(OUT / "A_allocation_series.csv")

    # marginal effect of each sleeve (averaged over all other settings)
    marg = {c: g.groupby(c)[["cagr", "sharpe", "max_dd", "cvar5", "calmar"]].mean().round(4).to_dict("index")
            for c in ["lam", "tail_budget_m", "fade_w", "boost"]}
    # efficient picks
    picks = {
        "max_sharpe": g.loc[g.sharpe.idxmax(), "key"],
        "max_calmar": g.loc[g.calmar.idxmax(), "key"],
        "max_cagr_with_dd_better_than_spy": g[g.max_dd > bench["spy_shares"]["max_dd"]].sort_values("cagr").iloc[-1]["key"],
    }
    # growth-optimal (Kelly) exposure for the core alone: maximise mean log return over lam
    kelly = {lam: float(np.log1p(core[f"core_{lam}"].reindex(idx)).mean() * 12) for lam in
             [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]}
    out = {"benchmarks": bench, "marginal_effects": marg, "picks": picks,
           "picked_rows": g[g.key.isin(picks.values())].to_dict("records"),
           "core_log_growth_by_lam": kelly}
    (OUT / "A_allocation_summary.json").write_text(json.dumps(out, indent=2, default=float))
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame(bench).T[["cagr", "ann_vol", "sharpe", "max_dd", "cvar5", "ret_2008", "ret_2020", "ret_2022"]])
    print(g[g.key.isin(picks.values())][["key", "cagr", "ann_vol", "sharpe", "max_dd", "calmar", "cvar5",
                                          "ret_2008", "ret_2020", "ret_2022"]])
    for c, d in marg.items():
        print(c, json.dumps(d))
    print("log growth by lam:", {k: round(v, 4) for k, v in kelly.items()})


if __name__ == "__main__":
    main()
