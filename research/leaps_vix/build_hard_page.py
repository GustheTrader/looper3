"""Build frontier.html (the hard-asset / crypto / space plan page) from ./results and ./data.

Run after study_hard25.py:  python build_hard_page.py
Styling and chart helpers are shared with goal_template.html; the content lives in hard_main.html.
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
    p = pd.read_csv(RES / "H_plan_monthly.csv", index_col=0, parse_dates=True)
    sl = pd.read_csv(RES / "H_sleeves_monthly.csv", index_col=0, parse_dates=True)
    roll = pd.read_csv(RES / "H_rolling36.csv", index_col=0, parse_dates=True)
    h = json.loads((RES / "H_summary.json").read_text())
    d = {}
    d["ec"] = pack(growth(p[["Frontier Plan", "Unlevered core", "BTC", "QQQ", "GLD"]]))
    gg = growth(p[["Frontier Plan", "QQQ", "BTC"]])
    d["dd"] = pack(gg / gg.cummax() - 1)
    t = p.loc["2021-01-01":, ["Frontier Plan", "Walk-forward (3-yr look-back)", "QQQ", "GLD", "BTC"]]
    d["test_ec"] = pack(growth(t))
    names = {"btc_tilt": "BTC, EMA-ensemble exit", "eth_tilt": "ETH, EMA-ensemble exit", "space": "Space / SpaceX chain, EMA-scaled",
             "hard_rotation": "Hard-asset rotation", "psc_gld": "Gold put spread → calls", "trend_hard": "Hard-asset trend",
             "spike_fade": "VIX spike fade"}
    d["sleeves"] = pack(growth(sl[list(names)].rename(columns=names)))
    d["roll_cagr"] = pack(roll[[c for c in roll.columns if c.endswith("CAGR")]].rename(columns=lambda c: c.replace(" CAGR", "")))
    d["roll_sharpe"] = pack(roll[[c for c in roll.columns if c.endswith("Sharpe")]].rename(columns=lambda c: c.replace(" Sharpe", "")))
    yr = (1 + p[["Frontier Plan", "QQQ", "GLD"]]).groupby(p.index.year).prod() - 1
    d["years"] = {"labels": [str(y) for y in yr.index], "series": {c: [round(float(v), 4) for v in yr[c]] for c in yr.columns}}
    d["boot"] = {"bins": h["bootstrap_bins"], **{k: h["bootstrap"][k]["hist"] for k in ["Frontier Plan", "QQQ", "BTC"]}}
    d["names"] = names
    raw = pd.read_csv(DATA / "hard_assets_adj_d.csv", index_col=0, parse_dates=True)
    spcx = pd.read_csv(DATA / "spcx_d.csv", index_col=0, parse_dates=True)["SPCX"].dropna()
    d["spcx"] = pack(spcx.to_frame("SpaceX (SPCX)"), "%Y-%m-%d")
    sig = {}

    def votes(s):
        s = s.dropna()
        out = {n: bool(s.iloc[-1] > s.ewm(span=n, adjust=False).mean().iloc[-1]) for n in (20, 50, 100, 200) if len(s) >= n}
        return {"close": round(float(s.iloc[-1]), 2), "above": out}
    for t_, lab in [("BTC-USD", "BTC"), ("ETH-USD", "ETH"), ("GLD", "GLD")]:
        sig[lab] = votes(raw[t_])
    sig["SPCX"] = votes(spcx)
    sig["as_of"] = str(raw["SPY"].dropna().index[-1].date())
    d["signals"] = sig

    e = json.loads((RES / "E_summary.json").read_text())
    d["ema"] = {"report": e["report"], "heat_design": e["heat_design_cash"], "heat_test": e["heat_test_cash"],
                "frontier": {k: {p: {m: v[p][m] for m in ["cagr", "sharpe", "max_dd"]} for p in ["full", "design_2015_20", "test_2021_26"]}
                             for k, v in e["frontier"].items()}}
    d["rule_pick"] = {"key": h["rule_pick"], "test": h["rule_pick_perf_test"], "full": h["rule_pick_perf_full"]}
    g = (HERE / "goal_template.html").read_text()
    style = g[g.index("<style>"):g.index("</style>") + len("</style>")]
    helpers = g[g.index("<script src="):g.index("function tables()")]
    main_html = (HERE / "hard_main.html").read_text()
    html = ("<title>Frontier Plan</title>\n" + g[g.index('<meta name="claude:viewport'):g.index("<style>")] + style + "\n"
            + main_html.replace("/*__HELPERS__*/", helpers.replace("/*__DATA__*/{}", json.dumps(d, separators=(",", ":")))
                                .replace("/*__STATS__*/{}", json.dumps(h, separators=(",", ":"), default=float))))
    out = HERE / "frontier.html"
    out.write_text(html)
    print(out, f"{out.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
