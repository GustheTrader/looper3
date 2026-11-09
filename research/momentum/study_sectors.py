"""
CONCEPT B -- Sector concentration driven by a Sector Power Strength Rating (SPSR).

B1  Sector rotation window scan on three universes:
      * French 12 industries   (CRSP, survivorship-free)     1996-10 .. 2026-09
      * French 49 industries   (CRSP, survivorship-free)     1996-10 .. 2026-09
      * SPDR sector ETFs       (real tradable funds, Yahoo)  2001-01 .. latest month
    Ranking signal: raw relative strength ("RS") vs the composite SPSR.
    Portfolios: long top-k minus short bottom-k (long/short), and long-only top-k
    (concentrated) judged against the market.
B2  Sector-concentrated STOCK momentum (S&P 500 members): only buy momentum
    winners that sit in the top-rated sectors, only short losers in the
    bottom-rated sectors. Compared with plain stock momentum.
B3  Live scan: today's sector ratings and stock long/short lists.
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
SKIPS = [0, 1]
HOLDS = [1, 3, 6]
GICS_TO_ETF = {
    "Materials": "XLB", "Energy": "XLE", "Financials": "XLF", "Industrials": "XLI",
    "Information Technology": "XLK", "Consumer Staples": "XLP", "Utilities": "XLU",
    "Health Care": "XLV", "Consumer Discretionary": "XLY", "Real Estate": "XLRE",
    "Communication Services": "XLC",
}


def universes():
    mkt, rf = ml.market_monthly()
    u = {}
    for name in ["ind12_m", "ind49_m"]:
        r = ml.load_french(name).loc["1993-01":]
        u[name] = dict(ret=r, mkt=mkt, start="1996-10-31", end="2026-09-30",
                       ks=[1, 2, 3] if name == "ind12_m" else [3, 5, 8], cost=10)
    e = ml.yahoo_monthly("etf_close_d.csv")
    spy = e.pop("SPY")
    u["spdr_etf"] = dict(ret=e, mkt=spy, start="2001-01-31", end=str(e.index[-1].date()),
                         ks=[1, 2, 3], cost=5)
    return u, rf


def sector_scan(name, cfg, rf):
    ret, mkt = cfg["ret"], cfg["mkt"]
    rows, nets = [], {}
    sig_cache = {}
    for L, S in itertools.product(LOOKBACKS, SKIPS):
        sig_cache[("RS", L, S)] = ml.cum_return(ret, L, S)
        sig_cache[("SPSR", L, S)] = ml.spsr(ret, mkt, L, S)
    for (sname, L, S), sig in sig_cache.items():
        for k, H, mode in itertools.product(cfg["ks"], HOLDS, ["long_short", "long_only"]):
            w = ml.rank_weights(sig, n_long=k, n_short=k, long_only=(mode == "long_only"))
            bt = ml.backtest(w, ret, H=H, cost_bps=cfg["cost"])
            n = bt.net.loc[cfg["start"]:cfg["end"]]
            if mode == "long_only":
                active = n - mkt.reindex(n.index)       # return vs. the market
                p = ml.perf(n, rf)
                score = ml.perf(active)["sharpe"]        # information ratio
            else:
                p = ml.perf(n)
                score = p["sharpe"]
            key = f"{sname}_L{L}_S{S}_H{H}_k{k}_{mode}"
            nets[key] = n if mode == "long_short" else n - mkt.reindex(n.index)
            rows.append({"universe": name, "signal": sname, "L": L, "S": S, "H": H, "k": k,
                         "mode": mode, "score": score, "sharpe": p["sharpe"],
                         "ann_ret": p["ann_ret"], "ann_vol": p["ann_vol"], "cagr": p["cagr"],
                         "max_dd": p["max_dd"], "worst_month": p["worst_month"],
                         "turnover_m": bt.turnover.loc[cfg["start"]:cfg["end"]].mean(),
                         "score_1st_half": ml.perf(nets[key].iloc[:len(n) // 2])["sharpe"],
                         "score_2nd_half": ml.perf(nets[key].iloc[len(n) // 2:])["sharpe"]})
    return pd.DataFrame(rows), nets


def summarise(name, grid, nets, cfg, rf):
    out = {}
    mkt = cfg["mkt"].loc[cfg["start"]:cfg["end"]]
    out["market_buy_hold"] = ml.perf(mkt, rf)
    for mode in ["long_short", "long_only"]:
        g = grid[grid["mode"] == mode]
        best = g.loc[g["score"].idxmax()]
        key = f"{best.signal}_L{best.L}_S{best.S}_H{best.H}_k{best.k}_{mode}"
        cand = {f"{r.signal}_L{r.L}_S{r.S}_H{r.H}_k{r.k}_{mode}": nets[f"{r.signal}_L{r.L}_S{r.S}_H{r.H}_k{r.k}_{mode}"]
                for r in g.itertuples()}
        wf, picks = ml.walk_forward(cand, train=120, min_train=60, every=12)
        # mean score by lookback = the "plateau" view (robust to one lucky cell)
        by_L = g.groupby(["L"])["score"].mean().round(3).to_dict()
        by_LS = g.groupby(["signal"])["score"].mean().round(3).to_dict()
        by_S = g.groupby(["S"])["score"].mean().round(3).to_dict()
        out[mode] = {
            "best_cell": key, "best_score": best.score, "best_ann_ret": best.ann_ret,
            "best_max_dd": best.max_dd,
            "deflated_sharpe_prob": ml.deflated_sharpe(nets[key], (g["score"] / np.sqrt(12)).to_numpy()),
            "n_configs": len(g),
            "mean_score_by_lookback": by_L, "mean_score_by_signal": by_LS, "mean_score_by_skip": by_S,
            "half_rank_stability_spearman": g[["score_1st_half", "score_2nd_half"]].corr("spearman").iloc[0, 1],
            "walk_forward_oos": ml.perf(wf),
            "walk_forward_top_picks": picks.value_counts().head(5).to_dict(),
        }
    return out


# ----------------------------------------------------------------- B2


def concentrated_stock_momentum(rf):
    ret = ml.yahoo_monthly("sp500_close_d.csv")
    cons = pd.read_csv(ml.DATA / "sp500_constituents.csv")
    cons["tk"] = cons["Symbol"].str.replace(".", "-", regex=False)
    sector_of = cons.set_index("tk")["GICS Sector"].reindex(ret.columns)
    # Equal-weight GICS sector indices built from the members themselves
    sect = ret.T.groupby(sector_of).mean().T
    mkt = ret.mean(axis=1)
    start, end = "1996-10-31", "2026-09-30"
    rows, series = [], {}
    for sL, kS, stL in itertools.product([3, 6, 9, 12], [2, 3, 4], [6, 12]):
        rating = ml.spsr(sect, mkt, sL, 0)
        top = rating.rank(axis=1, ascending=False) <= kS
        bot = rating.rank(axis=1, ascending=True) <= kS
        allow_long = pd.DataFrame({c: top[sector_of[c]] if sector_of[c] in top else False for c in ret.columns})
        allow_short = pd.DataFrame({c: bot[sector_of[c]] if sector_of[c] in bot else False for c in ret.columns})
        sig = ml.cum_return(ret, stL, 1)
        for label, al, ash in [("plain", None, None), ("concentrated", allow_long, allow_short)]:
            if label == "plain" and (sL, kS) != (3, 2):
                continue  # plain does not depend on sector parameters
            w = ml.rank_weights(sig, n_long=25, n_short=25, allowed=al, allowed_short=ash)
            bt = ml.backtest(w, ret, H=3, cost_bps=10)
            n = bt.net.loc[start:end]
            p = ml.perf(n)
            key = f"{label}_stockL{stL}" + ("" if label == "plain" else f"_sectorL{sL}_k{kS}")
            series[key] = n
            rows.append({"variant": label, "stock_L": stL, "sector_L": sL if label != "plain" else None,
                         "k_sectors": kS if label != "plain" else None, **p,
                         "sharpe_1st15y": ml.perf(n.loc[:"2011-09"])["sharpe"],
                         "sharpe_2nd15y": ml.perf(n.loc["2011-10":])["sharpe"]})
    return pd.DataFrame(rows), series, sect, sector_of, ret


# ----------------------------------------------------------------- B3 live scan


def live_scan(u, sect, sector_of, stock_ret, bestL):
    scan = {}
    e = u["spdr_etf"]
    r = ml.spsr(e["ret"], e["mkt"], bestL["spdr_etf"], 0).iloc[-1].dropna().sort_values(ascending=False)
    rs = ml.cum_return(e["ret"], bestL["spdr_etf"], 0).iloc[-1]
    scan["as_of"] = str(e["ret"].index[-1].date())
    scan["spdr_etf_spsr"] = {k: {"spsr": round(v, 1), f"ret_{bestL['spdr_etf']}m": round(rs[k], 4)} for k, v in r.items()}
    f = u["ind49_m"]
    r49 = ml.spsr(f["ret"], f["mkt"], bestL["ind49_m"], 0).iloc[-1].dropna().sort_values(ascending=False)
    scan["ff49_as_of"] = str(f["ret"].index[-1].date())
    scan["ff49_top8"] = r49.head(8).round(1).to_dict()
    scan["ff49_bottom8"] = r49.tail(8).round(1).to_dict()
    # stock lists: 6-1 stock momentum among names inside the top / bottom 3 GICS
    # sectors ranked by 9-month SPSR (the windows the research settled on)
    mkt = stock_ret.mean(axis=1)
    rating = ml.spsr(sect, mkt, 9, 0).iloc[-1]
    top = rating.nlargest(3).index
    bot = rating.nsmallest(3).index
    m = ml.cum_return(stock_ret, 6, 1).iloc[-1]
    scan["stock_window"] = "6-1 (6-month return, skipping the latest month)"
    scan["gics_sector_spsr_9m"] = rating.sort_values(ascending=False).round(1).to_dict()
    scan["long_candidates"] = m[sector_of.isin(top)].nlargest(15).round(3).to_dict()
    scan["short_candidates"] = m[sector_of.isin(bot)].nsmallest(15).round(3).to_dict()
    # crash-guard status for the long/short books
    m24 = ml.cum_return(u["ind12_m"]["mkt"].to_frame("m"), 24, 0)["m"].dropna()
    scan["guard_market_24m_return"] = round(float(m24.iloc[-1]), 4)
    scan["guard_long_short_active"] = bool(m24.iloc[-1] > 0)
    return scan


def main():
    u, rf = universes()
    allgrid, summary, bestL = [], {}, {}
    for name, cfg in u.items():
        g, nets = sector_scan(name, cfg, rf)
        allgrid.append(g)
        summary[name] = summarise(name, g, nets, cfg, rf)
        bestL[name] = int(pd.Series(summary[name]["long_short"]["mean_score_by_lookback"]).idxmax())
        print(name, json.dumps(summary[name], indent=1, default=float))
    pd.concat(allgrid).to_csv(OUT / "B1_sector_grid.csv", index=False)

    b2, b2s, sect, sector_of, sret = concentrated_stock_momentum(rf)
    b2.to_csv(OUT / "B2_concentrated_stock_momentum.csv", index=False)
    pd.DataFrame(b2s).to_csv(OUT / "B2_series.csv")
    pd.set_option("display.width", 220, "display.float_format", "{:.3f}".format)
    print(b2.sort_values("sharpe", ascending=False).head(12)[
        ["variant", "stock_L", "sector_L", "k_sectors", "ann_ret", "ann_vol", "sharpe", "max_dd", "sharpe_1st15y", "sharpe_2nd15y"]])
    print(b2[b2.variant == "plain"][["stock_L", "ann_ret", "sharpe", "max_dd"]])

    scan = live_scan(u, sect, sector_of, sret, bestL)
    summary["best_lookback_plateau_long_short"] = bestL
    (OUT / "B_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    (OUT / "B3_live_scan.json").write_text(json.dumps(scan, indent=2, default=float))
    print(json.dumps(scan, indent=1))


if __name__ == "__main__":
    main()
