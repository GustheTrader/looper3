"""
VALIDATION OF THE VIX SPIKE FADE (15% sleeve) AGAINST REAL VIX DATA AND REAL VIX FUNDS

The sleeve is modelled: short 0.5x a 30-day constant-maturity VIX future, whose daily return comes from
options_lib.vix_future (VIX / VIX3M / VVIX). This script checks every link with independent data:

  V1  VIX inputs     Yahoo ^VIX / ^VIX3M / ^VVIX vs CBOE's official daily files
  V2  futures model  modelled 30-day CM future vs the REAL VIXY fund (2011+) and VXX (2018+):
                     daily correlation, beta, yearly returns (the contango bleed)
  V3  the trade      the same fade days traded with the REAL SVXY fund. SVXY was -1x short-term VIX
                     futures until 2018-02-27 and -0.5x from 2018-02-28, so before that date its daily
                     return is halved to match the sleeve's 0.5x size (no other adjustment).
  V4  per trade      every fade trade: model P&L vs real-SVXY P&L
  V5  look-ahead     bump VIX on one day -> that day's position must not change
  V6  costs          the model charges no trading costs; add VIX-futures / SVXY round-trip costs
Writes results/VV_summary.json and results/VV_trades.csv.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "leaps_vix"))
import options_lib as ol  # noqa: E402
import study_vix as sv  # noqa: E402

OUT, CB = HERE / "results", HERE / "data" / "cboe"
SVXY_SWITCH = pd.Timestamp("2018-02-28")      # ProShares cut SVXY to -0.5x effective 2018-02-28


def cboe(sym):
    d = pd.read_csv(CB / f"{sym}.csv")
    d.index = pd.to_datetime(d.iloc[:, 0], format="%m/%d/%Y")
    return pd.to_numeric(d.iloc[:, -1], errors="coerce").dropna() / 100


def stats(r):
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    return {"cagr": float(eq.iloc[-1] ** (1 / yrs) - 1), "max_dd": float((eq / eq.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0, "days_active": float((r != 0).mean())}


def main():
    m = ol.load_market()
    out = {}
    # ---------------- V1 VIX inputs vs CBOE
    v1 = {}
    for y, c in [("^VIX", "VIX"), ("^VIX3M", "VIX3M"), ("^VVIX", "VVIX")]:
        a = m["vol"][y].dropna() / 100
        b = cboe(c)
        j = pd.concat([a, b], axis=1, keys=["yahoo", "cboe"]).dropna()
        dif = (j.yahoo - j.cboe).abs()
        v1[c] = {"days": int(len(j)), "max_abs_diff_pts": float(dif.max() * 100), "days_diff_over_0.05pt": int((dif > 0.0005).sum()),
                 "worst_date": str(dif.idxmax().date()), "last": str(j.index[-1].date())}
    out["V1_inputs"] = v1

    # ---------------- V2 futures model vs real VIXY / VXX
    d = sv.panel(m)
    model = d.cm_ret
    vol = m["vol"]
    v2 = {}
    for fund, start in [("VIXY", "2011-01-05"), ("UVXY", "2011-10-05")]:
        if fund not in vol:
            continue
        real = vol[fund].pct_change().reindex(d.index).loc[start:]
        j = pd.concat([model.loc[start:], real], axis=1, keys=["model", "real"]).dropna()
        if fund == "UVXY":
            continue
        beta = float(np.cov(j.model, j.real)[0, 1] / j.model.var())
        yr = (1 + j).groupby(j.index.year).prod() - 1
        v2[fund] = {"corr": float(j.corr().iloc[0, 1]), "beta_real_on_model": float(np.cov(j.real, j.model)[0, 1] / j.model.var()),
                    "beta_model_on_real": beta,
                    "cagr_model": stats(j.model)["cagr"], "cagr_real": stats(j.real)["cagr"],
                    "yearly": {int(k): {"model": float(r.model), "real": float(r.real)} for k, r in yr.iterrows()}}
    import yfinance as yf
    vxx = yf.download("VXX", start="2018-01-25", progress=False, auto_adjust=False)["Close"].squeeze().pct_change()
    j = pd.concat([model, vxx.reindex(d.index)], axis=1, keys=["model", "real"]).dropna()
    v2["VXX"] = {"corr": float(j.corr().iloc[0, 1]), "beta_real_on_model": float(np.cov(j.real, j.model)[0, 1] / j.model.var()),
                 "cagr_model": stats(j.model)["cagr"], "cagr_real": stats(j.real)["cagr"]}
    out["V2_futures_model"] = v2

    # ---------------- V3 the fade on real SVXY
    fade, pos = sv.v3_fade(d)
    svxy = vol["SVXY"].pct_change().reindex(d.index)
    lev = pd.Series(np.where(d.index < SVXY_SWITCH, 1.0, 0.5), index=d.index)
    svxy05 = svxy * (0.5 / lev)                                           # SVXY scaled to a -0.5x exposure
    real_fade = svxy05.where(pos, 0.0)
    st = "2011-10-05"
    j = pd.concat([fade.loc[st:], real_fade.loc[st:]], axis=1, keys=["model", "real"]).dropna()
    on = pos.loc[j.index]
    jj = j[on]
    yr = (1 + j).groupby(j.index.year).prod() - 1
    out["V3_fade_vs_SVXY"] = {"period": [str(j.index[0].date()), str(j.index[-1].date())],
                              "model": stats(j.model), "real_svxy": stats(j.real),
                              "corr_on_fade_days": float(jj.corr().iloc[0, 1]), "fade_days": int(on.sum()),
                              "yearly": {int(k): {"model": float(r.model), "real": float(r.real)} for k, r in yr.iterrows()},
                              "test_2021": {"model": stats(j.model.loc["2021":]), "real_svxy": stats(j.real.loc["2021":])},
                              "old_check_unscaled_svxy_cagr": stats(svxy.where(pos, 0.0).loc[st:])["cagr"]}
    # what happened on 5 Feb 2018 (Volmageddon)
    out["V3_fade_vs_SVXY"]["volmageddon_2018_02_05"] = {"position_on": bool(pos.get(pd.Timestamp("2018-02-05"), False)),
                                                        "svxy_return_that_day": float(svxy.get(pd.Timestamp("2018-02-06"), np.nan))}

    # ---------------- V4 per-trade
    blk = (pos != pos.shift(1, fill_value=False)).cumsum()
    trades = []
    for b, g in j.assign(on=on).groupby(blk.loc[j.index]):
        if not g.on.iloc[0]:
            continue
        trades.append({"entry": str(g.index[0].date()), "exit": str(g.index[-1].date()), "days": len(g),
                       "vix_entry": float(d.v.shift(1).loc[g.index[0]] * 100),
                       "model": float((1 + g.model).prod() - 1), "real_svxy": float((1 + g.real).prod() - 1)})
    T = pd.DataFrame(trades)
    T["diff"] = T.model - T.real_svxy
    T.to_csv(OUT / "VV_trades.csv", index=False)
    out["V4_trades"] = {"n": int(len(T)), "win_model": float((T.model > 0).mean()), "win_real": float((T.real_svxy > 0).mean()),
                        "same_sign": float((np.sign(T.model) == np.sign(T.real_svxy)).mean()),
                        "corr": float(T[["model", "real_svxy"]].corr().iloc[0, 1]), "mean_diff": float(T["diff"].mean()),
                        "worst_model": float(T.model.min()), "worst_real": float(T.real_svxy.min()),
                        "largest_gap": T.loc[T["diff"].abs().idxmax()].to_dict()}

    # ---------------- V5 look-ahead
    t0 = d.loc["2020-03-02":].index[0]
    d2 = d.copy()
    d2.loc[t0, "v"] = 0.30; d2.loc[t0, "v3"] = 0.40                       # force an entry-like reading on t0
    _, pos2 = sv.v3_fade(d2)
    nxt = d.index[d.index.get_loc(t0) + 1]
    out["V5_lookahead"] = {"day": str(t0.date()), "pos_same_day_unchanged": bool(pos2[t0] == pos[t0]),
                           "rule": "position on day t is decided from the close of t-1 (shift 1)"}

    # ---------------- V6 costs
    entries = (pos & ~pos.shift(1, fill_value=False)).loc[st:].sum()
    yrs = len(j) / 252
    rt = {"VIX futures (0.05 pt/side ≈ 0.25% of notional/side)": 0.005, "SVXY ETF (spread 0.05%/side)": 0.001}
    out["V6_costs"] = {"trades_per_year": float(entries / yrs),
                       "drag_per_year": {k: float(entries / yrs * c * 0.5) for k, c in rt.items()}}
    (OUT / "VV_summary.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: v for k, v in out.items() if k not in ("V2_futures_model",)}, indent=1, default=float)[:6000])
    print(json.dumps({k: {x: y for x, y in v.items() if x != "yearly"} for k, v in out["V2_futures_model"].items()}, indent=1))
    print(T.to_string(index=False))


if __name__ == "__main__":
    main()
