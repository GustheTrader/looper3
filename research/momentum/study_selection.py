"""
WHICH ROLLING WINDOW SHOULD YOU ACTUALLY USE?  -- honest selection head-to-head.

For each strategy family we compare five ways of choosing the lookback window:

  fixed_12      the textbook window chosen BEFORE looking at data (12 months; 12-1 when skip=1)
  wf_grid       walk-forward: every year pick the best cell of the whole grid on the
                trailing 10 years (what most people do when they "optimise")
  wf_lookback   walk-forward over the lookback only, other settings fixed a priori
  ensemble      no selection at all: split capital equally across L = 6, 9, 12 months
  ens_guarded   ensemble + two crash guards from the literature:
                  - volatility targeting (Barroso & Santa-Clara 2015), 12%/yr cap 2x
                  - stand aside when the market's 24-month return is negative
                    (Cooper, Gutierrez & Hameed 2004; Daniel & Moskowitz 2016)
                    (long/short families only)
  in_sample_best  the single best cell over the full sample -- an overfit
                  UPPER BOUND, shown only to measure the optimism gap.

All results are net of trading costs and evaluated over the same out-of-sample
months (from 2001-10, after the first 5 years of walk-forward training).
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

import momentum_lib as ml

OUT = Path(__file__).parent / "results"
LOOKBACKS = [1, 2, 3, 4, 6, 9, 12, 15, 18, 24]
ENS = [6, 9, 12]
OOS_START = "2001-10-31"


def guard(r: pd.Series, mkt: pd.Series, regime: bool = True) -> pd.Series:
    vol = r.rolling(12, min_periods=6).std().shift(1) * np.sqrt(12)
    scaled = r * (0.12 / vol).clip(upper=2.0)
    if regime:
        bear = ml.cum_return(mkt.to_frame("m"), 24, 0)["m"].shift(1) < 0
        scaled = scaled.where(~bear.reindex(scaled.index).fillna(False), 0.0)
    return scaled


def compare(name: str, by_L: dict, grid: dict, mkt: pd.Series, end: str, long_short=True,
            fixed=12, rf=None) -> dict:
    ens = pd.concat([by_L[L] for L in ENS], axis=1).mean(axis=1)
    wf_g, pick_g = ml.walk_forward(grid, start=OOS_START)
    wf_l, pick_l = ml.walk_forward(by_L, start=OOS_START)
    is_best_key = max(grid, key=lambda k: ml.perf(grid[k].loc[OOS_START:end]).get("sharpe", -9))
    series = {
        "fixed_12": by_L[fixed], "wf_grid": wf_g, "wf_lookback": wf_l, "ensemble": ens,
        "ens_guarded": guard(ens, mkt, regime=long_short),
        "in_sample_best": grid[is_best_key],
    }
    res = {}
    for k, s in series.items():
        res[k] = ml.perf(s.loc[OOS_START:end], rf)
    res["in_sample_best"]["cell"] = is_best_key
    res["wf_lookback"]["picks"] = pick_l.value_counts().to_dict()
    return res, {f"{name}|{k}": v.loc[OOS_START:end] for k, v in series.items()}


def main():
    mkt, rf = ml.market_monthly()
    results, curves = {}, {}

    # ---------- 1. market time-series momentum (long market / cash)
    ex = (mkt - rf).to_frame("m")
    by_L, grid = {}, {}
    for L, S in itertools.product(LOOKBACKS, [0, 1]):
        pos = (ml.cum_return(ex, L, S)["m"] > 0).astype(float)
        r = pos.shift(1) * ex["m"] + rf - pos.diff().abs().shift(1) * 2 / 1e4
        r = r.loc["1996-10":]
        grid[f"L{L}_S{S}"] = r
        if S == 1:
            by_L[L] = r
    res, c = compare("market_trend_long_cash", by_L, grid, mkt, "2026-09-30", long_short=False, rf=rf)
    res["buy_hold"] = ml.perf(mkt.loc[OOS_START:"2026-09-30"], rf)
    results["market_trend_long_cash"], curves = res, curves | c
    curves["market_trend_long_cash|buy_hold"] = mkt.loc[OOS_START:"2026-09-30"]

    # ---------- 2. stock cross-sectional L/S (S&P 500 survivors)
    sret = ml.yahoo_monthly("sp500_close_d.csv")
    by_L, grid = {}, {}
    for L, S, H in itertools.product(LOOKBACKS, [0, 1], [1, 3, 6, 12]):
        w = ml.rank_weights(ml.cum_return(sret, L, S), frac=0.10)
        n = ml.backtest(w, sret, H=H, cost_bps=10).net.loc["1996-10":"2026-09"]
        grid[f"L{L}_S{S}_H{H}"] = n
        if S == 1 and H == 3:
            by_L[L] = n
    results["stock_LS_sp500"], c = compare("stock_LS_sp500", by_L, grid, mkt, "2026-09-30")
    curves |= c

    # ---------- 3. sector SPSR L/S and long-only, three universes
    e = ml.yahoo_monthly("etf_close_d.csv")
    spy = e.pop("SPY")
    unis = {
        "ind12": (ml.load_french("ind12_m").loc["1993":], mkt, 2, 10, "2026-09-30"),
        "ind49": (ml.load_french("ind49_m").loc["1993":], mkt, 5, 10, "2026-09-30"),
        "spdr": (e, spy, 2, 5, str(e.index[-1].date())),
    }
    for uname, (ret, m, k, cost, end) in unis.items():
        for mode in ["long_short", "long_only"]:
            by_L, grid = {}, {}
            for sig_name, L, S, H in itertools.product(["RS", "SPSR"], LOOKBACKS, [0, 1], [1, 3, 6]):
                sig = ml.cum_return(ret, L, S) if sig_name == "RS" else ml.spsr(ret, m, L, S)
                w = ml.rank_weights(sig, n_long=k, n_short=k, long_only=(mode == "long_only"))
                n = ml.backtest(w, ret, H=H, cost_bps=cost).net.loc["1996-10":end]
                if mode == "long_only":
                    n = n - m.reindex(n.index)   # judge vs the market (active return)
                grid[f"{sig_name}_L{L}_S{S}_H{H}"] = n
                if sig_name == "SPSR" and S == 1 and H == 3:
                    by_L[L] = n
            key = f"sector_{uname}_{mode}"
            results[key], c = compare(key, by_L, grid, m, end, long_short=(mode == "long_short"))
            curves |= c

    # ---------- 4. sector-concentrated stock momentum
    cons = pd.read_csv(ml.DATA / "sp500_constituents.csv")
    sector_of = cons.assign(tk=cons.Symbol.str.replace(".", "-", regex=False)).set_index("tk")["GICS Sector"].reindex(sret.columns)
    sect = sret.T.groupby(sector_of).mean().T
    emkt = sret.mean(axis=1)
    by_L, grid = {}, {}
    for sL, stL in itertools.product(LOOKBACKS, [6, 12]):
        rating = ml.spsr(sect, emkt, sL, 0)
        top, bot = rating.rank(axis=1, ascending=False) <= 3, rating.rank(axis=1) <= 3
        al = pd.DataFrame({c: top[sector_of[c]] for c in sret.columns if sector_of[c] in top}).reindex(columns=sret.columns, fill_value=False)
        ash = pd.DataFrame({c: bot[sector_of[c]] for c in sret.columns if sector_of[c] in bot}).reindex(columns=sret.columns, fill_value=False)
        w = ml.rank_weights(ml.cum_return(sret, stL, 1), n_long=25, n_short=25, allowed=al, allowed_short=ash)
        n = ml.backtest(w, sret, H=3, cost_bps=10).net.loc["1996-10":"2026-09"]
        grid[f"sectorL{sL}_stockL{stL}"] = n
        if stL == 6:
            by_L[sL] = n
    results["concentrated_stock_LS"], c = compare("concentrated_stock_LS", by_L, grid, mkt, "2026-09-30")
    curves |= c

    (OUT / "C_selection.json").write_text(json.dumps(results, indent=2, default=float))
    pd.DataFrame(curves).to_csv(OUT / "C_selection_series.csv")
    rows = []
    for fam, d in results.items():
        for meth, p in d.items():
            rows.append({"family": fam, "method": meth, "sharpe": p.get("sharpe"), "ann_ret": p.get("ann_ret"),
                         "ann_vol": p.get("ann_vol"), "max_dd": p.get("max_dd"), "worst_month": p.get("worst_month")})
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "C_selection_table.csv", index=False)
    pd.set_option("display.width", 200, "display.float_format", "{:.3f}".format)
    print(t.to_string(index=False))
    for fam, d in results.items():
        print(fam, "IS best:", d["in_sample_best"]["cell"], "| wf_lookback picks:", d["wf_lookback"]["picks"])


if __name__ == "__main__":
    main()
