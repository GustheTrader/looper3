"""
ROBUSTNESS -- two checks on the headline "9-month sector window" finding.

D1  True out-of-sample history: rerun the exact sector grid on 1931-01 .. 1996-09,
    a 65-year period the original study never looked at (French 12 & 49
    industries, CRSP). If 6-12 months wins there too, the finding is not a
    product of the 1996-2026 sample.
D2  Finer granularity: weekly rebalancing with lookbacks measured in weeks
    (1 week .. 2 years), on daily data aggregated to Friday closes:
    French 49 industries (daily) and SPDR sector ETFs. Answers "is a monthly
    grid hiding a better short-term window?"
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

import momentum_lib as ml
from study_sectors import sector_scan

OUT = Path(__file__).parent / "results"


# ----------------------------------------------------------------- D1


def d1_pre1996():
    mkt, rf = ml.market_monthly()
    rows, summ = [], {}
    for name, ks in [("ind12_m", [1, 2, 3]), ("ind49_m", [3, 5, 8])]:
        ret = ml.load_french(name)
        cfg = dict(ret=ret, mkt=mkt, start="1931-01-31", end="1996-09-30", ks=ks, cost=10)
        g, nets = sector_scan(name, cfg, rf)
        g["period"] = "1931-1996"
        rows.append(g)
        out = {}
        for mode in ["long_short", "long_only"]:
            gm = g[g["mode"] == mode]
            out[mode] = {
                "mean_score_by_lookback": gm.groupby("L")["score"].mean().round(3).to_dict(),
                "mean_score_by_signal": gm.groupby("signal")["score"].mean().round(3).to_dict(),
                "spsr_L9_S1_H3": gm[(gm.signal == "SPSR") & (gm.L == 9) & (gm.S == 1) & (gm.H == 3)]
                [["k", "score", "ann_ret", "max_dd"]].round(3).to_dict("records"),
            }
        summ[name] = out
    pd.concat(rows).to_csv(OUT / "D1_pre1996_sector_grid.csv", index=False)
    return summ


# ----------------------------------------------------------------- D2


def weekly(ret_d: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns -> Friday-to-Friday compounded weekly returns."""
    w = np.expm1(np.log1p(ret_d).resample("W-FRI").sum(min_count=1))
    return w.iloc[:-1] if ret_d.index[-1] < w.index[-1] else w   # drop a partial week


def d2_weekly():
    WEEKS = [1, 2, 4, 8, 13, 26, 39, 52, 78, 104]
    ff = ml.load_french("ff_factors_d")
    unis = {}
    ind = ml.load_french("ind49_d").loc["1994":"2026-09"]
    unis["ind49_weekly"] = (weekly(ind), weekly((ff["Mkt-RF"] + ff["RF"]).loc["1994":"2026-09"].to_frame("m"))["m"],
                            "1996-10-01", "2026-09-30", 5, 10)
    px = pd.read_csv(ml.DATA / "etf_close_d.csv", index_col=0, parse_dates=True)
    er = weekly(px.pct_change(fill_method=None).iloc[1:])
    spy = er.pop("SPY")
    unis["spdr_weekly"] = (er, spy, "2001-01-01", str(er.index[-1].date()), 2, 5)

    rows = []
    for uname, (ret, mkt, start, end, k, cost) in unis.items():
        for sig_name, L, S, H in itertools.product(["RS", "SPSR"], WEEKS, [0, 1], [1, 4, 13]):
            sig = ml.cum_return(ret, L, S) if sig_name == "RS" else ml.spsr(ret, mkt, L, S)
            w = ml.rank_weights(sig, n_long=k, n_short=k)
            bt = ml.backtest(w, ret, H=H, cost_bps=cost)
            n = bt.net.loc[start:end]
            p = ml.perf(n, periods=52)
            rows.append({"universe": uname, "signal": sig_name, "L_weeks": L, "S_weeks": S, "H_weeks": H,
                         "sharpe": p["sharpe"], "ann_ret": p["ann_ret"], "max_dd": p["max_dd"],
                         "turnover_per_week": bt.turnover.loc[start:end].mean()})
    g = pd.DataFrame(rows)
    g.to_csv(OUT / "D2_weekly_sector_grid.csv", index=False)
    summ = {}
    for u in g.universe.unique():
        gu = g[g.universe == u]
        summ[u] = {
            "mean_sharpe_by_lookback_weeks": gu.groupby("L_weeks")["sharpe"].mean().round(3).to_dict(),
            "mean_sharpe_by_hold_weeks": gu.groupby("H_weeks")["sharpe"].mean().round(3).to_dict(),
            "mean_sharpe_by_signal": gu.groupby("signal")["sharpe"].mean().round(3).to_dict(),
            "best": gu.loc[gu.sharpe.idxmax()].to_dict(),
        }
    return summ


def main():
    res = {"D1_pre1996": d1_pre1996(), "D2_weekly": d2_weekly()}
    (OUT / "D_robustness.json").write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
