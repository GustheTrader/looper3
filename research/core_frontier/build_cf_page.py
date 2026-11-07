"""Build core_frontier.html (65% core / 35% trading sleeves) from ./results.

Run after study_ff.py, study_pm.py and study_portfolio.py:  python build_cf_page.py
Styling and chart helpers are shared with research/leaps_vix/goal_template.html.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
RES = HERE / "results"
LV = HERE.parent / "leaps_vix"


def pack(df, fmt="%Y-%m"):
    df = df.dropna(how="all")
    return {"labels": [d.strftime(fmt) for d in df.index],
            "series": {c: [None if pd.isna(v) else round(float(v), 5) for v in df[c]] for c in df.columns}}


def growth(df):
    return (1 + df).cumprod().where(df.notna())


def main():
    M = pd.read_csv(RES / "CF_monthly.csv", index_col=0, parse_dates=True)
    SL = pd.read_csv(RES / "CF_sleeves_monthly.csv", index_col=0, parse_dates=True)
    roll = pd.read_csv(RES / "CF_rolling36.csv", index_col=0, parse_dates=True)
    s = json.loads((RES / "CF_summary.json").read_text())
    P, C, T = "Core Frontier Portfolio (65/35)", "Core only (100%)", "Trading sleeves only"
    d = {}
    d["ec"] = pack(growth(M[[P, C, T, "QQQ", "BTC"]]))
    g = growth(M[[P, C, "QQQ"]])
    d["dd"] = pack(g / g.cummax() - 1)
    d["test_ec"] = pack(growth(M.loc["2021-01-01":, [P, C, T, "QQQ", "BTC"]]))
    names = {"core": "Core (65%)", "vix_fade": "VIX spike fade", "ff_calendars": "FF calendars", "momentum_ls": "Momentum L/S",
             "prediction": "Prediction model (pilot, cash)"}
    d["sleeves"] = pack(growth(SL[list(names)[1:]].rename(columns=names)))
    yr = (1 + M[[P, C, "QQQ"]]).groupby(M.index.year).prod() - 1
    d["years"] = {"labels": [str(y) for y in yr.index], "series": {c: [round(float(v), 4) for v in yr[c]] for c in yr.columns}}
    d["roll_cagr"] = pack(roll[[c for c in roll.columns if c.endswith("CAGR")]].rename(columns=lambda c: c.replace(" CAGR", "")))
    d["roll_sharpe"] = pack(roll[[c for c in roll.columns if c.endswith("Sharpe")]].rename(columns=lambda c: c.replace(" Sharpe", "")))
    ff = pd.read_csv(RES / "FF_series.csv", index_col=0, parse_dates=True)["FF"]
    d["ff"] = pack(ff.resample("W-FRI").max().to_frame("Forward Factor (weekly max)"), "%Y-%m-%d")
    d["boot"] = {"bins": s["bootstrap_bins"], **{k: s["bootstrap"][k]["hist"] for k in [P, "QQQ"]}}
    d["variants"] = pd.read_csv(RES / "CF_core_target_variants.csv").to_dict("records")
    fr = json.loads((LV / "results" / "H_summary.json").read_text())
    d["spacex"] = fr["spacex"]
    raw = pd.read_csv(LV / "data" / "hard_assets_adj_d.csv", index_col=0, parse_dates=True)
    spcx = pd.read_csv(LV / "data" / "spcx_d.csv", index_col=0, parse_dates=True)["SPCX"]
    vol = pd.read_csv(LV / "data" / "vol_index_d.csv", index_col=0, parse_dates=True)

    def votes(x):
        x = x.dropna()
        a = {n: bool(x.iloc[-1] > x.ewm(span=n, adjust=False).mean().iloc[-1]) for n in (20, 50, 100, 200) if len(x) >= n}
        return {"close": round(float(x.iloc[-1]), 2), "above": a}
    d["signals"] = {"BTC": votes(raw["BTC-USD"]), "ETH": votes(raw["ETH-USD"]), "GLD": votes(raw["GLD"]), "SPCX": votes(spcx),
                    "VIX": round(float(vol["^VIX"].dropna().iloc[-1]), 2), "VIX3M": round(float(vol["^VIX3M"].dropna().iloc[-1]), 2),
                    "VVIX": round(float(vol["^VVIX"].dropna().iloc[-1]), 1), "FF": round(float(ff.iloc[-1]), 3),
                    "as_of": str(raw["SPY"].dropna().index[-1].date())}
    g_ = (LV / "goal_template.html").read_text()
    style = g_[g_.index("<style>"):g_.index("</style>") + len("</style>")]
    helpers = g_[g_.index("<script src="):g_.index("function tables()")]
    main_html = (HERE / "cf_main.html").read_text()
    html = ("<title>Core Frontier Portfolio</title>\n" + g_[g_.index('<meta name="claude:viewport'):g_.index("<style>")] + style + "\n"
            + main_html.replace("/*__HELPERS__*/", helpers.replace("/*__DATA__*/{}", json.dumps(d, separators=(",", ":")))
                                .replace("/*__STATS__*/{}", json.dumps(s, separators=(",", ":"), default=float))))
    out = HERE / "core_frontier.html"
    out.write_text(html)
    print(out, f"{out.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
