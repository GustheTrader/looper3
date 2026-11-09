"""
PER-ASSET-CLASS EMA OPTIMISATION for the long / short / cash switch.

For each asset class, find the exponential moving average (EMA) length whose
"price above EMA -> long, below -> cash or short" rule works best, then use it
in the Frontier plan instead of the single 50-day simple average.

Grid: 8 EMA lengths (10, 20, 30, 50, 75, 100, 150, 200 days) x 3 rules for the
position below the EMA (cash 0, short half -0.5, short full -1), every
instrument in a class, net of switching costs (5 bps; crypto 10 bps).

Honest selection
  * Design period: up to 2016-12 (crypto: up to 2020-12). Test period: after that, untouched.
  * Each class's EMA is picked on the design period by the best AVERAGE Sharpe of
    each length and its two neighbours (a plateau), not the single best cell.
  * The test period compares: chosen EMA vs the old 50-day SMA vs buy & hold.
Then the Frontier plan is rebuilt with the chosen EMAs and compared with the original.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import options_lib as ol
import study_hard25 as h

OUT = ol.HERE / "results"
LENGTHS = [10, 20, 30, 50, 75, 100, 150, 200]
MODES = {"cash": 0.0, "short_half": -0.5, "short_full": -1.0}
CLASSES = {
    "crypto": ["BTC-USD", "ETH-USD"],
    "precious": ["GLD", "SLV", "PPLT"],
    "miners": ["GDX", "GDXJ"],
    "metals": ["XME", "COPX", "DBB", "URA"],
    "energy": ["XLE", "XOP", "OIH", "USO"],
    "space": ["ITA", "XAR", "UFO", "space_chain"],
    "equity": ["QQQ", "SPY", "SMH"],
}
SPLIT = {c: ("2020-12-31" if c == "crypto" else "2016-12-31") for c in CLASSES}
END = "2026-11-05"


def load():
    px, rf = h.load()
    g = pd.read_csv(ol.DATA / "growth_etf_adj_d.csv", index_col=0, parse_dates=True)
    px["SMH"] = g["SMH"].reindex(px.index)
    R = px.pct_change(fill_method=None)
    sp = R[h.SPACE].where(px[h.SPACE].shift(1).notna()).mean(axis=1, skipna=True)
    first = px[h.SPACE].apply(lambda s: s.first_valid_index()).min()
    sp = sp.where(sp.index > first)
    px["space_chain"] = (1 + sp.fillna(0)).cumprod().where(sp.notna())
    return px, rf


def rule_returns(price, rf, n, low, cost):
    p = price.dropna()
    r = p.pct_change()
    ema = p.ewm(span=n, adjust=False, min_periods=n).mean()
    pos = (p > ema).map({True: 1.0, False: low}).shift(1)
    pos = pos.where(ema.shift(1).notna())
    rr = rf.reindex(p.index).fillna(0)
    return (pos * (r - rr) - pos.diff().abs().fillna(0) * cost).dropna()


def ens_returns(price, rf, low, cost):
    p = price.dropna()
    r = p.pct_change()
    pos = low + (1 - low) * h.ens_frac(p)
    pos = pos.where(p.ewm(span=200, adjust=False, min_periods=200).mean().shift(1).notna())
    rr = rf.reindex(p.index).fillna(0)
    return (pos * (r - rr) - pos.diff().abs().fillna(0) * cost).dropna()


def sma50_returns(price, rf, cost):
    p = price.dropna()
    r = p.pct_change()
    pos = (p > p.rolling(50).mean()).astype(float).shift(1).where(p.rolling(50).mean().shift(1).notna())
    rr = rf.reindex(p.index).fillna(0)
    return (pos * (r - rr) - pos.diff().abs().fillna(0) * cost).dropna()


def sharpe(x):
    x = x.dropna()
    return float(x.mean() / x.std() * np.sqrt(252)) if len(x) > 120 and x.std() > 0 else np.nan


def main():
    px, rf = load()
    rows = []
    for cls, names in CLASSES.items():
        cost = 1e-3 if cls == "crypto" else 5e-4
        for name in names:
            price = px[name]
            bh = (price.pct_change() - rf).dropna()
            for per, (a, b) in {"design": (None, SPLIT[cls]), "test": (SPLIT[cls], END), "full": (None, END)}.items():
                sl = lambda x: x.loc[a:b] if a else x.loc[:b]
                rows.append({"class": cls, "asset": name, "period": per, "rule": "buy_hold", "L": 0, "sharpe": sharpe(sl(bh)),
                             "cagr": float((1 + sl(bh) + rf.reindex(sl(bh).index)).prod() ** (252 / max(len(sl(bh)), 1)) - 1)})
                s50 = sl(sma50_returns(price, rf, cost))
                rows.append({"class": cls, "asset": name, "period": per, "rule": "sma50_cash", "L": 50, "sharpe": sharpe(s50),
                             "cagr": float((1 + s50 + rf.reindex(s50.index)).prod() ** (252 / max(len(s50), 1)) - 1)})
                for mode, low in MODES.items():
                    x = sl(ens_returns(price, rf, low, cost))
                    rows.append({"class": cls, "asset": name, "period": per, "rule": "ens_" + mode, "L": -1, "sharpe": sharpe(x),
                                 "cagr": float((1 + x + rf.reindex(x.index)).prod() ** (252 / max(len(x), 1)) - 1)})
                for n in LENGTHS:
                    for mode, low in MODES.items():
                        x = sl(rule_returns(price, rf, n, low, cost))
                        rows.append({"class": cls, "asset": name, "period": per, "rule": mode, "L": n, "sharpe": sharpe(x),
                                     "cagr": float((1 + x + rf.reindex(x.index)).prod() ** (252 / max(len(x), 1)) - 1)})
    g = pd.DataFrame(rows)
    g.to_csv(OUT / "E_ema_grid.csv", index=False)

    cls_avg = g.groupby(["class", "period", "rule", "L"])[["sharpe", "cagr"]].mean().reset_index()
    choice, report = {}, []
    for cls in CLASSES:
        best = None
        for mode in MODES:
            d = cls_avg[(cls_avg["class"] == cls) & (cls_avg.period == "design") & (cls_avg.rule == mode)].set_index("L").reindex(LENGTHS)
            plateau = d.sharpe.rolling(3, center=True, min_periods=2).mean()
            n = int(plateau.idxmax())
            if best is None or plateau.max() > best[2]:
                best = (n, mode, float(plateau.max()))
        n, mode, ds = best
        choice[cls] = (n, MODES[mode])
        t = cls_avg[(cls_avg["class"] == cls) & (cls_avg.period == "test")]
        f = lambda rule, L: t[(t.rule == rule) & (t.L == L)].iloc[0]
        report.append({"class": cls, "chosen_ema": n, "below_ema": mode, "design_sharpe_plateau": ds,
                       "test_sharpe_chosen": float(f(mode, n).sharpe), "test_cagr_chosen": float(f(mode, n).cagr),
                       "test_sharpe_sma50": float(f("sma50_cash", 50).sharpe), "test_cagr_sma50": float(f("sma50_cash", 50).cagr),
                       "test_sharpe_buyhold": float(f("buy_hold", 0).sharpe), "test_cagr_buyhold": float(f("buy_hold", 0).cagr),
                       "test_best_L_cash": int(t[t.rule == "cash"].set_index("L").sharpe.idxmax()),
                       "test_sharpe_ens_cash": float(f("ens_cash", -1).sharpe), "test_cagr_ens_cash": float(f("ens_cash", -1).cagr),
                       "full_sharpe_ens_cash": float(cls_avg[(cls_avg["class"] == cls) & (cls_avg.period == "full") & (cls_avg.rule == "ens_cash")].sharpe.iloc[0]),
                       "full_sharpe_sma50": float(cls_avg[(cls_avg["class"] == cls) & (cls_avg.period == "full") & (cls_avg.rule == "sma50_cash")].sharpe.iloc[0]),
                       "split": SPLIT[cls]})
    rep = pd.DataFrame(report)

    # full-period heat map data: class x length, for each rule
    heat = {mode: cls_avg[(cls_avg.period == "full") & (cls_avg.rule == mode)].pivot(index="class", columns="L", values="sharpe")
            .reindex(list(CLASSES)).round(3) for mode in MODES}
    # test-period heat map (cash rule) to show stability
    heat_test = cls_avg[(cls_avg.period == "test") & (cls_avg.rule == "cash")].pivot(index="class", columns="L", values="sharpe").reindex(list(CLASSES)).round(3)
    heat_design = cls_avg[(cls_avg.period == "design") & (cls_avg.rule == "cash")].pivot(index="class", columns="L", values="sharpe").reindex(list(CLASSES)).round(3)

    # rebuild the Frontier plan with the chosen EMAs (rotation filter uses the length; tilts use length + rule)
    results = {}
    ens = {c: ("ens", None) for c in CLASSES}            # keep each sleeve's original below-signal position
    for label, sig in [("SMA-50 (original)", None), ("Per-class EMA", choice), ("EMA ensemble 20/50/100/200", ens)]:
        h.SIGNAL = sig
        S, rf_, px_, bench, _ = h.sleeves()
        r, _, _ = h.combine(S, rf_, 0.40, 6.0, h.CAPS, "cluster")
        m = h.monthly(r)
        rfm = h.monthly(rf_)
        res = {}
        for per, (a, b) in {"full": ("2015-10-01", "2026-11-30"), "design_2015_20": ("2015-10-01", "2020-12-31"),
                            "test_2021_26": ("2021-01-01", "2026-11-30")}.items():   # month-end labels
            x = m.loc[a:b]
            p = ol.perf(x, 12, rfm.reindex(x.index))
            yr = (1 + x).groupby(x.index.year).prod() - 1
            res[per] = {k: p[k] for k in ["cagr", "ann_vol", "sharpe", "max_dd", "calmar"]} | {"worst_year": float(yr.min())}
        res["sleeves_test"] = {c: ol.perf((S[c] + rf_).loc["2021-01-01":], 252, rf_.loc["2021-01-01":])["sharpe"] for c in S.columns}
        res["sleeves_full"] = {c: ol.perf(S[c] + rf_, 252, rf_)["sharpe"] for c in S.columns}
        results[label] = res
        results[label + "_monthly"] = m
    h.SIGNAL = None
    pd.DataFrame({k: v for k, v in results.items() if k.endswith("_monthly")}).rename(columns=lambda c: c.replace("_monthly", "")).to_csv(OUT / "E_frontier_monthly.csv")
    summ = {"choice": {k: {"ema": v[0], "below": v[1]} for k, v in choice.items()}, "report": rep.to_dict("records"),
            "heat_full": {k: v.to_dict("index") for k, v in heat.items()}, "heat_test_cash": heat_test.to_dict("index"),
            "heat_design_cash": heat_design.to_dict("index"),
            "frontier": {k: v for k, v in results.items() if not k.endswith("_monthly")}}
    (OUT / "E_summary.json").write_text(json.dumps(summ, indent=1, default=float))

    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(rep.to_string(index=False))
    print("design (cash rule) Sharpe by EMA length\n", heat_design)
    print("test (cash rule) Sharpe by EMA length\n", heat_test)
    for mode in MODES:
        print("full period,", mode, "\n", heat[mode])
    for k, v in summ["frontier"].items():
        print(k, json.dumps({p: v[p] for p in ["full", "design_2015_20", "test_2021_26"]}, default=float))
        print("  sleeves test Sharpe", {a: round(b, 2) for a, b in v["sleeves_test"].items()})


if __name__ == "__main__":
    main()
