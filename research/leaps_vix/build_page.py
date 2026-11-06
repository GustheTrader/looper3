"""Build paper.html (interactive version of PAPER.md) from ./results.

Run after the studies:  python build_page.py
The page template is page_template.html; this script only injects the data.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
RES = HERE / "results"


def growth(r: pd.Series) -> pd.Series:
    return (1 + r.dropna()).cumprod()


def pack(df: pd.DataFrame, fmt="%Y-%m") -> dict:
    df = df.dropna(how="all")
    return {"labels": [d.strftime(fmt) for d in df.index],
            "series": {c: [None if pd.isna(v) else round(float(v), 5) for v in df[c]] for c in df.columns}}


def main():
    d = {}
    p = pd.read_csv(RES / "calibration_putwrite.csv", index_col=0, parse_dates=True).dropna()
    d["calib_put"] = pack(pd.DataFrame({"CBOE ^PUT (real)": growth(p.put_actual), "Model": growth(p.put_sim)}))
    v = pd.read_csv(RES / "calibration_vixy.csv", index_col=0, parse_dates=True).dropna()
    vw = pd.DataFrame({"VIXY ETF (real)": growth(v.vixy_real), "Model": growth(v.vix_model)}).resample("W-FRI").last()
    d["calib_vixy"] = pack(vw, "%Y-%m-%d")

    s = pd.read_csv(RES / "L_leaps_series.csv", index_col=0, parse_dates=True)
    for u, key in [("SPY", "leaps_spy"), ("MOMENTUM", "leaps_mom")]:
        d[key] = pack(pd.DataFrame({"Shares": growth(s[f"{u}|shares"]), "LEAPS only": growth(s[f"{u}|leaps"]),
                                    "LEAPS + short calls": growth(s[f"{u}|pmcc"])}))

    sig = pd.read_csv(RES / "V_signals_daily.csv", index_col=0, parse_dates=True)
    wk = pd.DataFrame({"VIX": sig.vix.resample("W-FRI").max() * 100,
                       "on": sig.V3_signal.astype(bool).resample("W-FRI").max().astype(float)})
    d["vix"] = pack(wk, "%Y-%m-%d")
    mon = pd.read_csv(RES / "V_monthly.csv", index_col=0, parse_dates=True)
    d["vix_sleeves"] = pack(pd.DataFrame({"Spike fade (short after peak)": growth(mon.V3_spike_fade_short),
                                          "Long vol when VIX ≥ VIX3M": growth(mon.V1_backwardation_long)}))

    g = pd.read_csv(RES / "A_allocation_grid.csv")
    summ = json.loads((RES / "A_allocation_summary.json").read_text())
    d["frontier"] = {
        "points": [{"key": r.key, "lam": r.lam, "tail": r.tail_budget_m, "fade": r.fade_w, "boost": bool(r.boost),
                    "cagr": round(r.cagr, 4), "dd": round(-r.max_dd, 4), "sharpe": round(r.sharpe, 3)}
                   for r in g.itertuples()],
        "bench": {k: {"cagr": round(summ["benchmarks"][k]["cagr"], 4), "dd": round(-summ["benchmarks"][k]["max_dd"], 4),
                      "sharpe": round(summ["benchmarks"][k]["sharpe"], 3)} for k in ["spy_shares", "spy_covered_call"]},
    }
    a = pd.read_csv(RES / "A_allocation_series.csv", index_col=0, parse_dates=True)
    core = pd.read_csv(RES / "A_core_series.csv", index_col=0, parse_dates=True).reindex(a.index)
    mixes = {"SPY shares": core.spy_shares, "Conservative": a["lam0.75_tail0.0_fade0.2_noboost"],
             "Balanced": a["lam1.0_tail0.0_fade0.2_boost"], "Aggressive": a["lam1.25_tail0.0_fade0.2_boost"]}
    gr = pd.DataFrame({k: growth(r) for k, r in mixes.items()})
    d["mixes"] = pack(gr)
    d["mixes_dd"] = pack(gr / gr.cummax() - 1)

    fr = pd.read_csv(RES / "LS_french_results.csv")
    order = [("french_neutral", "No tilt"), ("french_tilt130", "130/70"), ("french_tilt150", "150/50"),
             ("french_tilt150_band1", "150/50 + 1% band"), ("french_flip", "Flip 100/0"),
             ("french_flip_band1", "Flip + 1% band"), ("market_ma50_long_short", "Market timing only")]
    d["ls_eras"] = {"labels": [lab for _, lab in order],
                    "series": {era: [round(float(fr[(fr.period == era) & (fr.key == k)].sharpe.iloc[0]), 3) for k, _ in order]
                               for era in ["1927-1996", "1996-2026"]}}
    html = (HERE / "page_template.html").read_text().replace("/*__DATA__*/{}", json.dumps(d, separators=(",", ":")))
    out = HERE / "paper.html"
    out.write_text(html)
    print(out, f"{out.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
