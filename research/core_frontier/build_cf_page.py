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
    # ---------- capital-efficiency layer (portfolio margin deck)
    mg = pd.read_csv(RES / "MG_grid.csv").set_index("key")
    fl = pd.DataFrame(json.loads((RES / "FL_summary.json").read_text())["rows"]).set_index("key")
    cl = pd.DataFrame(json.loads((RES / "CL_summary.json").read_text())["rows"]).set_index("key")
    mgs = json.loads((RES / "MG_summary.json").read_text())
    fls = json.loads((RES / "FL_summary.json").read_text())
    pick = lambda df, k, label, note="": {"label": label, "note": note, **{c: float(df.loc[k, c]) for c in
             ["test_cagr", "test_max_dd", "test_calmar", "test_worst_year", "full_cagr", "full_max_dd", "full_calmar", "full_worst_year"]}}
    rec = fls["chosen_by_design_calmar"]
    d["layers"] = [
        pick(mg, "const_1.0_spy0.0_vix0.0", "65/35, no extra leverage", "best return per unit of drawdown; margin over 70% on 24% of days"),
        pick(mg, "const_1.25_spy0.0_vix0.0", "Constant 1.25×", "margin over 70% most days"),
        pick(mg, "const_1.5_spy0.0_vix0.0", "Constant 1.5×", "margin over 70% most days"),
        pick(mg, "deck_vix_spy0.0_vix0.0", "Deck VIX-band leverage (2.0× … 0.75×)", "cuts leverage just before rebounds"),
        pick(mg, mgs["chosen"], "Deck bands ×1.25 + loss-budget throttle + SPY put hedge", "best on 2015–20, poor after"),
        pick(cl, "trading x1.5 | fade capped (VIX <= 10% of account)", "Lever trading sleeves 1.5× (VIX fade capped)", "little to lever once the VIX fade is capped"),
        pick(fl, rec, "Target 1.75× with a 70% margin cap, spot crypto (recommended)", "average leverage 1.17×"),
        pick(fl, fls["max_cagr_meeting_rule"], "Target 2.5× with 70% cap, crypto via futures", "highest return inside the −15% year rule"),
    ]
    pts = [{"x": -float(r.test_max_dd), "y": float(r.test_cagr), "label": k, "group": "Whole-portfolio leverage / hedges"} for k, r in mg.iterrows()]
    pts += [{"x": -float(r.test_max_dd), "y": float(r.test_cagr), "label": k, "group": "Margin-capped leverage"} for k, r in fl.iterrows()]
    pts += [{"x": -float(r.test_max_dd), "y": float(r.test_cagr), "label": k, "group": "Trading-sleeve leverage"} for k, r in cl.iterrows()]
    d["layer_points"] = pts
    d["layer_rec_key"] = rec
    d["hedges"] = mgs["hedges"]
    # recommended layer: daily leverage and margin use (recomputed for the recommended target)
    import importlib.util
    spec = importlib.util.spec_from_file_location("fl", HERE / "study_final_layer.py"); fl_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(fl_mod)
    Dd = pd.read_csv(RES / "CF_daily.csv", index_col=0, parse_dates=True)
    vol_ = pd.read_csv(LV / "data" / "vol_index_d.csv", index_col=0, parse_dates=True)["^VIX"].reindex(Dd.index).ffill() / 100
    rr, lev, util = fl_mod.run(Dd, vol_, float(fls["chosen_by_design_calmar"].split("target ")[1].split("x")[0]), "futures" in rec)
    mon = lambda x: (1 + x).resample("ME").prod() - 1
    eqs = pd.DataFrame({"65/35, no extra leverage": mon(Dd.portfolio), "Recommended: 1.75× target, 70% margin cap": mon(rr),
                        "Constant 1.5×": mon(Dd.rf + 1.5 * (Dd.portfolio - Dd.rf) - 0.5 * 0.005 / 252),
                        "QQQ": M["QQQ"]})
    d["layer_ec"] = pack(growth(eqs))
    gg2 = growth(eqs)
    d["layer_dd"] = pack(gg2 / gg2.cummax() - 1)
    d["layer_lev"] = pack(pd.DataFrame({"Leverage used": lev.resample("W-FRI").mean(), "Margin use": util.resample("W-FRI").mean()}), "%Y-%m-%d")
    yr2 = (1 + pd.DataFrame({"base": Dd.portfolio, "rec": rr})).groupby(Dd.index.year).prod() - 1
    d["layer_years"] = {"labels": [str(y) for y in yr2.index], "base": [round(float(v), 4) for v in yr2.base], "rec": [round(float(v), 4) for v in yr2.rec]}
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
