"""
CORE FRONTIER INVESTMENT PORTFOLIO -- 65% core / 35% short-term trading sleeves.

Core (65%): the Frontier plan's investment sleeves -- BTC/ETH, hard-asset rotation,
space/SpaceX chain, hard-asset trend and gold put spreads funding calls -- with the
EMA-ensemble (20/50/100/200) long/cash switch, risk balanced across four groups and
levered to a 40% volatility target (cap 6x), crypto capped. (research/leaps_vix/study_hard25.py)

Trading sleeves (35%):
  15%  VIX spike fade   short VIX futures (0.5x) after a spike has peaked     (validated on SVXY)
  10%  FF calendars     SPY calendars when Forward Factor > 0.20, VIX-band sizing (study_ff.py)
   5%  Momentum L/S     market-neutral CRSP momentum, scaled to 12% volatility
   5%  Prediction model 15-minute BTC up/down markets, KC dip-buy (study_pm.py) -- PILOT:
                        no long history, held as T-bills in this backtest
Each sleeve's idle capital earns T-bills. Rebalanced to the 65/35 split daily.
Backtest 2015-10 .. 2026-11 (limited by BTC history); 2021-26 is the untouched test period.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
LV = HERE.parent / "leaps_vix"
sys.path.insert(0, str(LV))
sys.path.insert(0, str(HERE.parent / "momentum"))
import options_lib as ol  # noqa: E402
import study_hard25 as h  # noqa: E402
import momentum_lib as ml  # noqa: E402

OUT = HERE / "results"
START, END = "2015-10-01", "2026-11-05"
WEIGHTS = {"core": 0.65, "vix_fade": 0.15, "ff_calendars": 0.10, "momentum_ls": 0.05, "prediction": 0.05}


def monthly(r):
    return (1 + r.fillna(0)).resample("ME").prod() - 1


def main():
    # ---------- core
    h.SIGNAL = h.DEFAULT_SIGNAL
    S, rf, px, bench, _ = h.sleeves()
    fade_ex = S["spike_fade"].copy()
    core_S = S.drop(columns=["spike_fade"])
    h.GROUPS = {"crypto": ["btc_tilt", "eth_tilt"], "frontier": ["hard_rotation", "space"],
                "trend": ["trend_hard"], "premium": ["psc_gld"]}
    caps = {k: v for k, v in h.CAPS.items() if k in core_S.columns}
    core, _, _ = h.combine(core_S, rf, 0.40, 6.0, caps, "cluster")
    core_W = h.combine.last_W
    idx = core.index

    # ---------- trading sleeves (total returns, idle cash in T-bills)
    fade = (rf + fade_ex).reindex(idx)
    # FF sleeve = the deck-faithful replay validated in audit_ff.py: SPY/QQQ/IWM/DIA, FF > 0.20, VIX-band size,
    # many concurrent positions, 20% reserve, exit at FF < 0.15 or 7 days left, realistic bid/ask
    ff = pd.read_csv(OUT / "FFA_daily.csv", index_col=0, parse_dates=True)["deck core: SPY QQQ IWM DIA | deck rules | realistic"]
    ff = (ff + 0.0).reindex(idx)
    ff = ff.fillna(rf.reindex(idx))
    dec = pd.read_csv(ml.DATA / "mom10_d.csv", index_col=0, parse_dates=True)
    wml = (dec["Hi PRIOR"] - dec["Lo PRIOR"]) - 2 * 12 * 0.35 * 10e-4 / 252 - 0.005 / 252
    vol = wml.rolling(126, min_periods=60).std().shift(1) * np.sqrt(252)
    mom_ex = (wml * (0.12 / vol).clip(upper=2.0)).reindex(idx).fillna(0)     # French data ends 2026-09: cash after
    mom = rf.reindex(idx) + mom_ex
    pm = rf.reindex(idx)
    sleeves = pd.DataFrame({"core": core, "vix_fade": fade, "ff_calendars": ff, "momentum_ls": mom, "prediction": pm}).loc[START:END]
    tot = sum(WEIGHTS[k] * sleeves[k] for k in WEIGHTS)
    trade_bucket = sum(WEIGHTS[k] * sleeves[k] for k in WEIGHTS if k != "core") / 0.35

    # ---------- benchmarks
    R = px.pct_change(fill_method=None)
    series_d = {"Core Frontier Portfolio (65/35)": tot, "Core only (100%)": sleeves.core, "Trading sleeves only": trade_bucket,
                "BTC": R["BTC-USD"].loc[START:END], "QQQ": R["QQQ"].loc[START:END], "GLD": R["GLD"].loc[START:END],
                "SPY": R["SPY"].loc[START:END]}
    M = pd.DataFrame({k: monthly(v) for k, v in series_d.items()})
    rfm = monthly(rf.loc[START:END])

    def stats(r, start=None):
        r = r.dropna()
        r = r.loc[start:] if start else r
        yr = (1 + r).groupby(r.index.year).prod() - 1
        p = ol.perf(r, 12, rfm.reindex(r.index))
        return {**{k: p[k] for k in ["cagr", "ann_vol", "sharpe", "sortino", "max_dd", "calmar", "worst"]},
                "worst_year": float(yr[yr.index > 2015].min()), "best_year": float(yr.max()),
                "pct_years_25": float((yr[(yr.index > 2015) & (yr.index < 2026)] > 0.25).mean())}

    perf_full = {k: stats(M[k]) for k in M}
    perf_test = {k: stats(M[k], "2021-01-01") for k in M}
    sleeve_stats = {k: stats(monthly(sleeves[k])) for k in sleeves}
    sleeve_test = {k: stats(monthly(sleeves[k]), "2021-01-01") for k in sleeves}
    corr = sleeves.pct_change().corr() if False else (sleeves.sub(rf.reindex(sleeves.index), axis=0)).corr()

    crises = {"Q4 2018 sell-off": ("2018-10-01", "2018-12-24"), "COVID crash": ("2020-02-19", "2020-03-23"),
              "2022 bear market": ("2022-01-01", "2022-12-31"), "April 2025 tariff shock": ("2025-04-01", "2025-04-30"),
              "Jan 2026 metals crash": ("2026-01-26", "2026-02-06")}
    crisis = {name: {k: float((1 + series_d[k].loc[a:b].fillna(0)).prod() - 1) for k in
                     ["Core Frontier Portfolio (65/35)", "Core only (100%)", "Trading sleeves only", "QQQ", "BTC"]}
              for name, (a, b) in crises.items()}

    def edge(r, b):
        x = pd.concat([r, b], axis=1, keys=["r", "b"]).dropna()
        er, eb = x.r - rfm.reindex(x.index), x.b - rfm.reindex(x.index)
        beta = float(np.cov(er, eb)[0, 1] / eb.var())
        res = er - beta * eb
        c = lambda s: (1 + s).prod() ** (12 / len(s)) - 1
        r12 = (1 + x).rolling(12).apply(np.prod, raw=True) - 1
        return {"alpha_ann": float(res.mean() * 12), "alpha_tstat": float(res.mean() / res.std() * np.sqrt(len(res))),
                "beta": beta, "excess_cagr": float(c(x.r) - c(x.b)),
                "up_capture": float(x.r[x.b > 0].mean() / x.b[x.b > 0].mean()),
                "down_capture": float(x.r[x.b < 0].mean() / x.b[x.b < 0].mean()),
                "pct_12m_windows_ahead": float((r12.r > r12.b).dropna().mean())}
    P = M["Core Frontier Portfolio (65/35)"]
    edges = {b: edge(P, M[b]) for b in ["QQQ", "SPY", "BTC", "GLD"]}

    rng = np.random.default_rng(5)
    boot = {}
    for k in ["Core Frontier Portfolio (65/35)", "Core only (100%)", "QQQ"]:
        r = M[k].loc["2021-01-01":].dropna().values
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
                   "p_above_25": float((res[:, 0] > 0.25).mean()), "p_loss": float((res[:, 0] < 0).mean()),
                   "dd_median": float(np.median(res[:, 1])),
                   "hist": np.histogram(np.clip(res[:, 0], -0.3, 0.9), bins=np.arange(-0.30, 0.91, 0.05))[0].tolist()}
    st = perf_test["Core Frontier Portfolio (65/35)"]
    rf_avg = float(rfm.loc["2021-01-01":].mean() * 12)
    hair = [{"haircut": hc, "sharpe": st["sharpe"] * (1 - hc),
             "expected_cagr": rf_avg + st["sharpe"] * (1 - hc) * st["ann_vol"] - st["ann_vol"] ** 2 / 2} for hc in [0, 0.25, 0.5]]

    ff = json.loads((OUT / "FF_summary.json").read_text())
    pm_res = json.loads((OUT / "PM_summary.json").read_text())
    out = {"weights": WEIGHTS, "perf_full": perf_full, "perf_test": perf_test, "sleeve_full": sleeve_stats,
           "sleeve_test": sleeve_test, "corr": corr.round(2).to_dict(), "crisis": crisis, "edges": edges,
           "bootstrap": boot, "bootstrap_bins": np.arange(-0.30, 0.91, 0.05).round(3).tolist(), "haircut": hair,
           "core_latest_weights": core_W.iloc[-1].round(3).to_dict(), "core_avg_weights": core_W.loc[START:].mean().round(3).to_dict(),
           "core_avg_gross": float(core_W.loc[START:].abs().sum(axis=1).mean()), "ff": ff, "pm": pm_res,
           "stress_crypto_crash_total": float(WEIGHTS["core"] * (core_W["btc_tilt"].max() * 0.40 + core_W["eth_tilt"].max() * 0.45)),
           "stress_vix_double_total": float(WEIGHTS["vix_fade"] * 0.5 * 1.0)}
    (OUT / "CF_summary.json").write_text(json.dumps(out, indent=1, default=float))
    M.to_csv(OUT / "CF_monthly.csv")
    daily = pd.DataFrame({"portfolio": tot, "core": sleeves.core, "trading": trade_bucket, "rf": rf.loc[START:END],
                          "core_crypto_w": (core_W["btc_tilt"] + core_W["eth_tilt"]).loc[START:END],
                          "core_gross": core_W.abs().sum(axis=1).loc[START:END],
                          "vix_fade": sleeves.vix_fade, "ff_calendars": sleeves.ff_calendars,
                          "momentum_ls": sleeves.momentum_ls, "prediction": sleeves.prediction}
                         | {f"w_{c}": core_W[c].loc[START:END] for c in core_W.columns})
    daily.to_csv(OUT / "CF_daily.csv")
    pd.DataFrame({k: monthly(v) for k, v in sleeves.items()}).to_csv(OUT / "CF_sleeves_monthly.csv")
    roll = {}
    for k in ["Core Frontier Portfolio (65/35)", "Core only (100%)", "QQQ", "BTC"]:
        r = M[k].dropna()
        ex = r - rfm.reindex(r.index)
        roll[k + " CAGR"] = (1 + r).rolling(36).apply(np.prod, raw=True) ** (1 / 3) - 1
        roll[k + " Sharpe"] = ex.rolling(36).mean() / ex.rolling(36).std() * np.sqrt(12)
    pd.DataFrame(roll).to_csv(OUT / "CF_rolling36.csv")

    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame(perf_full).T[["cagr", "ann_vol", "sharpe", "max_dd", "worst_year", "best_year"]])
    print(pd.DataFrame(perf_test).T[["cagr", "ann_vol", "sharpe", "max_dd", "worst_year"]])
    print(pd.DataFrame(sleeve_stats).T[["cagr", "ann_vol", "sharpe", "max_dd"]])
    print(pd.DataFrame(sleeve_test).T[["cagr", "sharpe", "max_dd"]])
    print(corr.round(2))
    print(pd.DataFrame(crisis).T)
    print(pd.DataFrame(edges).T)
    print({k: (round(v["cagr_pct"]["50"], 3), round(v["p_above_25"], 3), round(v["p_loss"], 3)) for k, v in boot.items()})
    print(hair)
    print("stress", out["stress_crypto_crash_total"], out["stress_vix_double_total"], "core gross", out["core_avg_gross"])
    yr = (1 + M).groupby(M.index.year).prod() - 1
    print(yr[["Core Frontier Portfolio (65/35)", "Core only (100%)", "Trading sleeves only", "QQQ", "BTC"]].round(3))


if __name__ == "__main__":
    main()
