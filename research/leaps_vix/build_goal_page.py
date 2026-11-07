"""Build goal25.html (the Goal-25 plan page) from ./results and ./data.

Run after study_goal25.py and study_goal25_wf.py:  python build_goal_page.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
RES, DATA = HERE / "results", HERE / "data"


def pack(df, fmt="%Y-%m"):
    df = df.dropna(how="all")
    return {"labels": [d.strftime(fmt) for d in df.index],
            "series": {c: [None if pd.isna(v) else round(float(v), 5) for v in df[c]] for c in df.columns}}


def growth(df):
    return (1 + df).cumprod().where(df.notna())


def main():
    p = pd.read_csv(RES / "Z_plan_monthly.csv", index_col=0, parse_dates=True)
    sl = pd.read_csv(RES / "Z_sleeves_monthly.csv", index_col=0, parse_dates=True)
    roll = pd.read_csv(RES / "Z_rolling36.csv", index_col=0, parse_dates=True)
    wf = json.loads((RES / "Z_wf_edge.json").read_text())
    zs = json.loads((RES / "Z_summary.json").read_text())
    d = {}
    main_cols = ["Goal-25 Safe", "No sleeve limits (unsafe)", "QQQ", "SPY", "Unlevered core (Sharpe ~1.0)"]
    g = growth(p[main_cols])
    d["ec"] = pack(g)
    gg = growth(p[["Goal-25 Safe", "QQQ", "SPY"]])
    d["dd"] = pack(gg / gg.cummax() - 1)
    w = p.loc["2014-01-01":, ["Goal-25 Safe", "Walk-forward (3-yr look-back)", "Walk-forward (5-yr look-back)", "QQQ"]]
    d["wf_ec"] = pack(growth(w))
    names = {"qqq_tilt": "QQQ with 50-day tilt", "psc_qqq": "QQQ put spread → calls", "spike_fade": "Spike fade",
             "putwrite": "Put-write index", "leaders_dual": "Leaders, dual momentum", "momentum_vs": "Momentum L/S (vol-scaled)",
             "psc_spy": "SPY put spread → calls", "trend": "Cross-asset trend"}
    d["sleeves"] = pack(growth(sl[list(names)].rename(columns=names)))
    d["roll_cagr"] = pack(roll[[c for c in roll.columns if c.endswith("CAGR")]].rename(columns=lambda c: c.replace(" CAGR", "")))
    d["roll_sharpe"] = pack(roll[[c for c in roll.columns if c.endswith("Sharpe")]].rename(columns=lambda c: c.replace(" Sharpe", "")))
    yr = (1 + p[["Goal-25 Safe", "QQQ", "SPY"]]).groupby(p.index.year).prod() - 1
    d["years"] = {"labels": [str(y) for y in yr.index], "series": {c: [round(float(v), 4) for v in yr[c]] for c in yr.columns}}
    d["boot"] = {"bins": wf["bootstrap_bins"], **{k: wf["bootstrap"][k]["hist"] for k in ["Goal-25 Safe", "QQQ", "SPY"]}}
    d["weights"] = {names.get(k, k): v for k, v in zs["detail_safe"]["latest_weights"].items()}
    d["avg_weights"] = {names.get(k, k): v for k, v in zs["detail_safe"]["avg_weight_x_nav"].items()}

    # live signals
    raw = pd.read_csv(DATA / "growth_etf_raw_d.csv", index_col=0, parse_dates=True)
    vol = pd.read_csv(DATA / "vol_index_d.csv", index_col=0, parse_dates=True)
    sig = pd.read_csv(RES / "V_signals_daily.csv", index_col=0, parse_dates=True)
    live = json.loads((RES / "G_live.json").read_text())
    sigs = {}
    for t in ["SPY", "QQQ"]:
        s = raw[t].dropna()
        sigs[t] = {"close": round(float(s.iloc[-1]), 2), "ma50": round(float(s.rolling(50).mean().iloc[-1]), 2)}
    v = vol[["^VIX", "^VIX3M"]].dropna()
    sigs["VIX"] = round(float(v["^VIX"].iloc[-1]), 2)
    sigs["VIX3M"] = round(float(v["^VIX3M"].iloc[-1]), 2)
    sigs["fade_on"] = bool(sig["V3_signal"].astype(bool).iloc[-1])
    sigs["leaders"] = live["leadership_top3_now"]
    sigs["as_of"] = str(raw.index[-1].date())
    d["signals"] = sigs

    html = (HERE / "goal_template.html").read_text()
    html = html.replace("/*__DATA__*/{}", json.dumps(d, separators=(",", ":")))
    html = html.replace("/*__STATS__*/{}", json.dumps(wf, separators=(",", ":"), default=float))
    out = HERE / "goal25.html"
    out.write_text(html)
    print(out, f"{out.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
