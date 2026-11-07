"""Build core_frontier.html (65% core / 35% trading sleeves) from ./results.

Run after study_ff.py, study_pm.py and study_portfolio.py:  python build_cf_page.py
Styling and chart helpers are shared with research/leaps_vix/goal_template.html.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
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
    rec = "target 1.75x | crypto via spot"     # 1.75x and 2.5x targets are identical once the 70% margin cap binds (avg 1.17x vs 1.19x)
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
    # ---------- perps + FF retest (study_perps.py, study_ff2.py)
    pp = json.loads((RES / "PP_summary.json").read_text())
    f2 = json.loads((RES / "FF2_summary.json").read_text())
    fy = pp["funding"]["yearly"]
    yrs = sorted(fy)
    d["fund_years"] = {"labels": yrs, **{k: [None if fy[y][k] is None else round(fy[y][k], 4) for y in yrs]
                                          for k in ["deribit_btc", "deribit_eth", "hl_btc", "tbill"]}}
    fdd = pd.read_csv(HERE / "data" / "funding_daily.csv", index_col=0, parse_dates=True)
    fm = fdd[["deribit_btc", "hl_btc"]].resample("ME").mean() * 365
    rfm_ = (Dd.rf.resample("ME").mean() * 252).reindex(fm.index).ffill()
    d["fund_month"] = pack(pd.DataFrame({"BTC perp funding, Deribit": fm.deribit_btc.rolling(3).mean(),
                                         "BTC perp funding, Hyperliquid": fm.hl_btc.rolling(3).mean(), "T-bills": rfm_}))
    g = pd.DataFrame(pp["grid"]).set_index("key")
    gp = lambda k, lab, note="": {"label": lab, "note": note, **{c: float(g.loc[k, c]) for c in
            ["avg_lev", "test_cagr", "test_max_dd", "test_calmar", "full_worst_year", "full_cagr"]}}
    d["perp_rows"] = [
        gp("spot (as published) | margin 100% | target 1.0x", "Spot crypto, no extra leverage"),
        gp("spot (as published) | margin 100% | target 1.75x", "Spot crypto, 1.75× target, 70% cap (current recommendation)", "top Calmar on 2015–20 (any target ≥ 1.75× is the same once capped)"),
        gp("perps | margin 50% | target 1.0x", "Perps (50% collateral), no extra leverage", "funding costs ~1.5%/yr"),
        gp("perps | margin 50% | target 1.75x", "Perps (50% collateral), 1.75× target", "survives every 1-day crash since 2015 (BTC)"),
        gp("perps | margin 35% | target 1.75x", "Perps (35% collateral), 1.75× target", "would have been liquidated on 12 Mar 2020"),
        gp("perps | margin 25% | target 2.5x", "Perps (25% collateral), 2.5× target", "highest CAGR inside −15% rule; liquidation risk"),
        gp("perps + idle carry | margin 50% | target 1.75x", "Perps + carry on idle crypto, 1.75×", "carry is rarely on when crypto is idle"),
        gp("perps, dearer venue each day | margin 35% | target 1.75x", "Perps at the dearer venue each day, 1.75×", "funding stress test"),
    ]
    d["perp_sleeves"] = {k: pp["sleeves"][k]["test"] for k in ["btc_spot", "btc_perp", "btc_perp_stress", "eth_spot", "eth_perp", "eth_perp_stress"]}
    d["liq"] = pp["liquidation"]
    PPd = pd.read_csv(RES / "PP_daily.csv", index_col=0, parse_dates=True)
    cs = PPd.carry_sleeve.dropna()
    d["carry_ec"] = pack(growth(pd.DataFrame({"Perp carry sleeve (50/50 BTC/ETH)": mon(cs), "T-bills": mon(PPd.rf.loc[cs.index])})))
    d["carry"] = {"tab": pp["carry_sleeve"], "yearly": pp["carry_yearly"], "tbill": pp["tbill_yearly"], "hl": pp["carry_hl_2023"],
                  "der23": pp["carry_deribit_2023"], "corr": pp["carry_corr_core"], "swap": pp["ff_swap"], "on_pct": pp["carry_on_pct"]}
    # procyclical funding: funding minus T-bills by crypto trend exposure
    import sys as _s
    _s.path.insert(0, str(LV)); _s.path.insert(0, str(HERE.parent / "momentum"))
    import study_hard25 as _h
    import study_perps as _sp
    pxh, rfh = _h.load()
    Fh = _sp.funding_on_days(pxh.index, rfh)
    proc = {}
    for k, tk in [("BTC", "BTC-USD"), ("ETH", "ETH-USD")]:
        e = _h.ens_frac(pxh[tk])
        x = pd.DataFrame({"e": e, "x": (Fh[f"deribit_{k.lower()}"] - rfh) * 252}).dropna()
        proc[k] = {"full": float(x.x[x.e == 1].mean()), "part": float(x.x[(x.e > 0.25) & (x.e < 1)].mean()), "low": float(x.x[x.e <= 0.25].mean())}
    d["procyc"] = proc
    # FF retest
    R2 = f2["results"]
    sel = lambda k: {p: R2[k][p] for p in ["design", "test"]}
    d["ff2_spx"] = [{"def": dfn, "sig": sg, **sel(f"[realistic] {dfn} | {sg}"), "zero": R2[f"[zero] {dfn} | {sg}"]["test"]}
                    for dfn in ["SPX 30->93 (deck)", "SPX 9->30 (weekly)", "SPX 93->182 (slow)"]
                    for sg in ["always", "deck FF>0.20", "deck FF>0.20 + Kadan filter", "top decile FF", "top decile FF + Kadan filter"]]
    d["ff2_basket"] = [{"def": b, "sig": sg, **sel(f"[realistic] {b} | {sg}"), "zero": R2[f"[zero] {b} | {sg}"]["test"]}
                       for b in ["basket: 4 stocks", "basket: 7 ETFs"]
                       for sg in ["always", "deck FF>0.20", "deck FF>0.20 + Kadan filter", "top decile FF", "top decile FF + Kadan filter"]]
    d["ff2_q"] = {g_: f2["by_kadan_spread"][g_] for g_ in f2["by_kadan_spread"]}
    d["ff2_decomp"] = f2["decomposition"]
    d["ff2_check"] = f2["spx_model_check"]
    d["ff2_share"] = f2["ff_share_spx"]
    # ---------- FF audit (audit_ff.py)
    fa = json.loads((RES / "FFA_summary.json").read_text())
    FD = pd.read_csv(RES / "FFA_daily.csv", index_col=0, parse_dates=True)
    rfa = (Dd.rf.reindex(FD.index) if False else None)
    import options_lib as _ol
    rfd = (_ol.load_market()["rf"] / 252).reindex(FD.index).ffill().fillna(0)
    def _st(r, sl):
        x = r.loc[sl]; e = (1 + x).cumprod()
        if (e <= 0).any():
            return None, None, -1.0
        ex = x - rfd.loc[sl]
        return float((1 + ex).prod() ** (252 / len(x)) - 1), float(ex.mean() / ex.std() * np.sqrt(252)), float((e / e.cummax() - 1).min())
    rows_ = []
    for k in FD.columns:
        u, ex_, c = k.split(" | ")
        r = FD[k].fillna(0)
        dx, dsh, _ = _st(r, slice(None, "2020-12-31")); tx, tsh, _ = _st(r, slice("2021-01-01", None)); fx, _, fdd = _st(r, slice(None, None))
        rep = fa["D_replay"][k]
        rows_.append({"universe": u, "exit": ex_, "cost": c, "d_ex": dx if dx is not None else -1.0, "d_sh": dsh, "t_ex": tx, "t_sh": tsh,
                      "full_ex": fx if fx is not None else -1.0, "full_dd": fdd, "ruin": fx is None, "mar20": float((1 + r.loc["2020-03"]).prod() - 1),
                      "y2020": float((1 + r.loc["2020"]).prod() - 1), "trades": rep["trades"], "win": rep["win"], "maxpos": rep["max_open_positions"]})
    A_, B_, C_ = fa["A_formula"], fa["B_simulator"], fa["C_live"]
    spy = C_["rows"]["SPY"]
    audit = [
        ["FF formula", f"Deck example (front 75%, forward 38%) gives {A_['deck_example_75_38']:.2f}; deck says 0.97. FF &gt; 0 exactly when the curve is inverted.", "Pass"],
        ["Calendar pricer", f"Flat world (no move, IV unchanged): simulator {B_['flat world (no move, IV 20/20)']:+.1%} vs Black-Scholes by hand {B_['analytic flat world']:+.1%}. Front-IV crush {B_['front IV crush 40->20, back 30->25']:+.0%}; back-IV fall {B_['back IV falls 30->20 (vega loss)']:+.0%}; 10% gap {B_['10% drop on day 5, IV flat 20']:+.0%}. Every sign is right.", "Pass"],
        ["SPY vol inputs (0.82 × VIX / VIX3M)", f"Real chain today: 30d {spy['iv_30']:.1%}, 90d {spy['iv_90']:.1%}; proxy {spy['proxy_iv_30']:.1%} / {spy['proxy_iv_90']:.1%}. But FF {spy['real_ff']:.2f} real vs {spy['proxy_ff']:.2f} proxy.", "Partial: levels fine, FF noisy"],
        ["Single-name / ETF back month (Kadan model)", "Modelled FF ≈ 0.01–0.05 for every name; real FF ranges from −0.08 (IBM) to +0.33 (EEM). The model only carries the market's term structure.", "Fail: not usable without real chains"],
        ["Bid/ask assumptions", f"Real SPY ATM half-spread {spy['half_spread_30']:.1%} (we assumed 0.3%, ×2–3 in panics). Single stocks quoted far wider than assumed.", "Pass for SPY; optimistic for stocks"],
        ["Bug: cost on deep in/out-of-the-money legs", "Costs were charged as % of each leg's price, so after big moves an in-the-money leg cost too much and an out-of-the-money one too little. Now charged as % of the at-the-money price for that expiry.", "Bug fixed (all FF scripts)"],
        ["Bug: near-zero debits", "In deep inversions the debit can shrink to almost nothing, and 'size ÷ debit' then hides huge leverage. Now skipped when the debit is under 20% of the back option.", "Bug fixed"],
        ["My earlier test vs the deck", "It traded one SPY calendar a week, and the retest held to expiry. The deck runs 20–50 positions across index ETFs and exits at FF &lt; 0.15. Re-run as written below.", "Fixed: deck replay"],
    ]
    d["ffa"] = {"rows": rows_, "audit": audit, "live": C_}
    sel_ = {"deck core: SPY QQQ IWM DIA | deck rules | zero": "No trading costs", "deck core: SPY QQQ IWM DIA | deck rules | realistic": "Realistic bid/ask",
            "deck core: SPY QQQ IWM DIA | deck rules | deck frictions (20-30% bid/ask at panic)": "Deck's panic frictions"}
    fe = pd.DataFrame({v: mon(FD[k].fillna(0)) for k, v in sel_.items()})
    fe["T-bills"] = mon(rfd)
    d["ffa_ec"] = pack(growth(fe))
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
