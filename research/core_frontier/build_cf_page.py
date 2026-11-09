"""Build core_frontier.html (65% core / 35% trading sleeves) from ./results.

Run after study_ff.py, study_pm.py and study_portfolio.py:  python build_cf_page.py
Styling and chart helpers are shared with research/leaps_vix/goal_template.html.
"""
from __future__ import annotations

import json
import sys
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
    # buy-and-hold BTC / QQQ next to the same assets through the core's 4-EMA filter (cash below the EMAs)
    sys.path.insert(0, str(LV)); sys.path.insert(0, str(HERE.parent / "momentum"))
    import study_hard25 as _hf
    _px, _rf = _hf.load()
    for _tk, _nm in [("BTC-USD", "BTC (4-EMA filter)"), ("QQQ", "QQQ (4-EMA filter)")]:
        _e = _hf.ens_frac(_px[_tk]); _r = _px[_tk].pct_change(fill_method=None)
        _f = (_e * (_r - _rf) + _rf - _e.diff().abs().fillna(0) * 1e-3).loc[M.index[0].replace(day=1):]
        M[_nm] = ((1 + _f.fillna(0)).resample("ME").prod() - 1).reindex(M.index)
    d["ec"] = pack(growth(M[[P, C, T, "QQQ", "BTC", "QQQ (4-EMA filter)", "BTC (4-EMA filter)"]]))
    g = growth(M[[P, C, "QQQ"]])
    d["dd"] = pack(g / g.cummax() - 1)
    d["test_ec"] = pack(growth(M.loc["2021-01-01":, [P, C, T, "QQQ", "BTC", "QQQ (4-EMA filter)", "BTC (4-EMA filter)"]]))
    names = {"core": "Core (65%)", "vix_fade": "VIX spike fade", "ff_calendars": "FF calendars (master execution)", "carry": "Funding carry",
             "momentum_ls": "Momentum L/S"}
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
    rec = fls["chosen_by_design_calmar"]                  # the layer's own 2015-20 pick (highest Calmar inside the -15% rule)
    L_REC = float(rec.split("target ")[1].split("x")[0])
    import study_margin as _sm
    _Dd = pd.read_csv(RES / "CF_daily.csv", index_col=0, parse_dates=True)
    _vix = pd.read_csv(LV / "data" / "vol_index_d.csv", index_col=0, parse_dates=True)["^VIX"].reindex(_Dd.index).ffill() / 100
    UNLEV_OVER70 = float((_sm.utilisation(_Dd, _vix, 1.0, False) > 0.70).mean())
    d["layers"] = [
        pick(mg, "const_1.0_spy0.0_vix0.0", "65/35, no extra leverage", f"margin over 70% on {UNLEV_OVER70:.0%} of days"),
        pick(mg, "const_1.25_spy0.0_vix0.0", "Constant 1.25×", f"margin over 70% on {float((_sm.utilisation(_Dd, _vix, 1.25, False) > 0.70).mean()):.0%} of days"),
        pick(mg, "const_1.5_spy0.0_vix0.0", "Constant 1.5×", f"margin over 70% on {float((_sm.utilisation(_Dd, _vix, 1.5, False) > 0.70).mean()):.0%} of days"),
        pick(mg, "deck_vix_spy0.0_vix0.0", "Deck VIX-band leverage (2.0× … 0.75×)", "cuts leverage just before rebounds"),
        pick(mg, mgs["chosen"], "Deck bands ×1.25 + loss-budget throttle + SPY put hedge", "best on 2015–20, poor after"),
        pick(cl, "trading x1.5 | fade capped (VIX <= 10% of account)", "Lever trading sleeves 1.5× (VIX fade capped)", "no margin cap applied in this test"),
        pick(fl, rec, f"Target {L_REC:g}× with a 70% margin cap, spot crypto (recommended)", f"average leverage {float(fl.loc[rec, 'avg_lev']):.2f}×"),
        pick(fl, fls["max_cagr_meeting_rule"], "Target " + fls["max_cagr_meeting_rule"].split("target ")[1].split("x")[0] + "× with 70% cap, crypto via "
             + fls["max_cagr_meeting_rule"].split("via ")[1], "highest full-sample CAGR inside the −15% year rule"),
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
    eqs = pd.DataFrame({"65/35, no extra leverage": mon(Dd.portfolio), f"Recommended: {L_REC:g}× target, 70% margin cap": mon(rr),
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
    _lq = pp["liquidation"]
    def _liq(m_):
        line = 0.8 * m_
        b_, e_ = -_lq["BTC-USD"]["worst_1day_low"], -_lq["ETH-USD"]["worst_1day_low"]
        return (f"liquidation line ≈ {line:.0%} fall: BTC's worst day {b_:.0%} {'survives' if b_ < line else 'liquidated'}, "
                f"ETH's {e_:.0%} {'survives' if e_ < line else 'liquidated'} ({_lq['BTC-USD']['date_1day']})")
    _un = pp["unlevered"]
    _idle = {}
    _C = pd.read_csv(RES / "CARRY_daily.csv", index_col=0, parse_dates=True)
    import study_hard25 as _h0
    _pxh, _ = _h0.load()
    for _k, _tk in [("btc", "BTC-USD"), ("eth", "ETH-USD")]:
        _e = _h0.ens_frac(_pxh[_tk]).reindex(_C.index)
        _idle[_k] = float(((1 - _e) * _C[f"on_{_k}"]).loc["2019-05-15":].mean())
    d["perp_rows"] = [
        gp("spot (as published) | margin 100% | target 1.0x", "Spot crypto, no extra leverage"),
        gp(f"spot (as published) | margin 100% | target {L_REC}x", f"Spot crypto, {L_REC:g}× target, 70% cap (current recommendation)", "top Calmar on 2015–20"),
        gp("perps | margin 50% | target 1.0x", "Perps (50% collateral), no extra leverage", f"funding costs {(_un['spot (as published)']['test']['cagr'] - _un['perps']['test']['cagr']) * 100:.1f} points/yr of portfolio CAGR"),
        gp(f"perps | margin 50% | target {L_REC}x", f"Perps (50% collateral), {L_REC:g}× target", _liq(0.50)),
        gp(f"perps | margin 35% | target {L_REC}x", f"Perps (35% collateral), {L_REC:g}× target", _liq(0.35)),
        gp("perps | margin 25% | target 2.5x", "Perps (25% collateral), 2.5× target", _liq(0.25)),
        gp(f"perps + idle carry | margin 50% | target {L_REC}x", f"Perps + carry on idle crypto, {L_REC:g}×", f"idle crypto and carry both on only {_idle['btc']:.0%} (BTC) / {_idle['eth']:.0%} (ETH) of the time"),
        gp(f"perps, dearer venue each day | margin 35% | target {L_REC}x", f"Perps at the dearer venue each day, {L_REC:g}×", "funding stress test"),
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
        ["SPY vol inputs (0.82 × VIX / VIX3M)", f"Real chain today: 30d {spy['iv_30']:.1%}, 90d {spy['iv_90']:.1%}; proxy {spy['proxy_iv_30']:.1%} / {spy['proxy_iv_90']:.1%}. But FF {spy['real_ff']:.2f} real vs {spy['proxy_ff']:.2f} proxy.", "Partial: levels close, FF noisy"],
        ["Single-name / ETF back month (Kadan model)", (lambda R_: f"Modelled FF {min(r_['model_ff'] for r_ in R_.values()):.2f} to {max(r_['model_ff'] for r_ in R_.values()):.2f} across names; real FF {min(r_['real_ff'] for r_ in R_.values()):+.2f} to {max(r_['real_ff'] for r_ in R_.values()):+.2f}. The model only carries the market's term structure.")({k_: v_ for k_, v_ in C_["rows"].items() if k_ != "SPY" and "model_ff" in v_}), "Fail: not usable without real chains"],
        ["Bid/ask assumptions", f"Real at-the-money half-spread today: SPY {spy['half_spread_30']:.1%} (we assumed 0.3%, ×2–3 in panics); single stocks " + ", ".join(f"{k_} {C_['rows'][k_]['half_spread_30']:.1%}" for k_ in ["AAPL", "AMZN", "GS", "IBM"] if k_ in C_["rows"]) + " (we assumed 0.6–1.0%).", "Pass for SPY; optimistic for stocks"],
        ["Bug: cost on deep in/out-of-the-money legs", "Costs were charged as % of each leg's price, so after big moves an in-the-money leg cost too much and an out-of-the-money one too little. Now charged as % of the at-the-money price for that expiry.", "Bug fixed (all FF scripts)"],
        ["Bug: near-zero debits", "In deep inversions the debit can shrink to almost nothing, and 'size ÷ debit' then hides huge leverage. Now skipped when the debit is under 20% of the back option.", "Bug fixed"],
        ["My earlier test vs the deck", "It traded one SPY calendar a week, and the retest held to expiry. The deck runs 20–50 positions across index ETFs and exits at FF &lt; 0.15. Re-run as written below.", "Fixed: deck replay"],
    ]
    d["ffa"] = {"rows": rows_, "audit": audit, "live": C_}
    sel_ = {"deck core: SPY QQQ IWM DIA | deck rules | zero": "No trading costs", "deck core: SPY QQQ IWM DIA | deck rules | master": "Master execution (used)",
            "deck core: SPY QQQ IWM DIA | deck rules | realistic": "Quoted bid/ask",
            "deck core: SPY QQQ IWM DIA | deck rules | deck frictions (20-30% bid/ask at panic)": "Deck's panic frictions"}
    fe = pd.DataFrame({v: mon(FD[k].fillna(0)) for k, v in sel_.items()})
    fe["T-bills"] = mon(rfd)
    d["ffa_ec"] = pack(growth(fe))
    g_ = (LV / "goal_template.html").read_text()
    style = g_[g_.index("<style>"):g_.index("</style>") + len("</style>")]
    helpers = g_[g_.index("<script src="):g_.index("function tables()")]
    _old_cb = "maxTicksLimit: 8, maxRotation: 0, font: { size: 11 }, callback(v) { return this.getLabelForValue(v).slice(0, 4); }"
    assert _old_cb in helpers, "x-axis tick callback not found"
    helpers = helpers.replace(_old_cb, "autoSkip: false, maxRotation: 0, font: { size: 11 }, callback(v) { const L = this.chart.data.labels, "
                              "l = String(this.getLabelForValue(v)); if (!/^\\d{4}/.test(l)) return l; if (v > 0 && String(L[v - 1]).slice(0, 4) === l.slice(0, 4)) return null; "
                              "const n = +String(L[L.length - 1]).slice(0, 4) - +String(L[0]).slice(0, 4) + 1, st = Math.max(1, Math.ceil(n / 8)), y = +l.slice(0, 4); "
                              "if (v === 0 && +l.slice(5, 7) > 6) return null; return (v === 0 || y % st === 0) ? l.slice(0, 4) : null; }")
    # ---------- weights, VIX validation, decay sleeve, execution desk
    WCH = json.loads((RES / "WEIGHTS_choice.json").read_text())
    d["weights"], d["wcands"], d["wchosen"] = WCH["weights"], WCH["candidates"], WCH["chosen"]
    VV = json.loads((RES / "VV_summary.json").read_text())
    vt = pd.read_csv(RES / "VV_trades.csv")
    d["vv"] = {"v1": VV["V1_inputs"], "v2": {k: {x: y for x, y in v.items() if x != "yearly"} for k, v in VV["V2_futures_model"].items()},
               "v3": {k: v for k, v in VV["V3_fade_vs_SVXY"].items() if k != "yearly"}, "v4": VV["V4_trades"], "v5": VV["V5_lookahead"], "v6": VV["V6_costs"],
               "years": {"labels": [str(y) for y in VV["V3_fade_vs_SVXY"]["yearly"]],
                         "model": [v["model"] for v in VV["V3_fade_vs_SVXY"]["yearly"].values()],
                         "real": [v["real"] for v in VV["V3_fade_vs_SVXY"]["yearly"].values()]},
               "trades": vt.to_dict("records"), "corrections": pd.read_csv(LV / "data" / "cboe_corrections.csv").to_dict("records")}
    DS = json.loads((RES / "DECAY_summary.json").read_text())
    DD_ = pd.read_csv(RES / "DECAY_daily.csv", index_col=0, parse_dates=True)
    show = ["A deck as written (kill + 50% target), market fills", "A0 deck as written, at mid (upper bound)",
            "E deck entries, no kill switch or target, mid (upper bound)", "F deck entries, no kill/target, market fills",
            "G deck entries, no kill/target, MASTER fills", "G3 same, 3% sleeve risk per trade"]
    d["decay"] = {"rows": [{"name": k, **{p_: DS["results"][k][p_] for p_ in ["design", "test"]}, "trades": DS["results"][k]["trades"],
                            "win": DS["results"][k]["win"], "avg": DS["results"][k]["avg_trade"]}
                           for k in DS["results"]],
                  "facts": DS["facts"], "liquid": DS["liquid_universe"], "by_ticker": DS["results"]["G deck entries, no kill/target, MASTER fills"]["by_ticker"],
                  "ec": pack(growth(((1 + DD_[show]).resample("ME").prod() - 1)))}
    d["decay_diag"] = json.loads((RES / "DECAY_diagnostics.json").read_text())
    d["desk"] = json.loads((RES / "DESK_today.json").read_text())
    d["L_rec"] = L_REC

    # ---------- facts: every result-dependent figure in the prose, filled into [[placeholders]]
    def pc(x, d=0, sign=False):
        v = f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"
        return v.replace("-", "−")
    X, FU = s["perf_test"], s["perf_full"]
    stt = s["sleeve_test"]
    ppr = json.loads((RES / "PP_summary.json").read_text())
    flr = pd.DataFrame(json.loads((RES / "FL_summary.json").read_text())["rows"]).set_index("key")
    fld = pd.read_csv(RES / "FL_daily.csv", index_col=0, parse_dates=True)
    aud = json.loads((RES / "AUDIT_portfolio.json").read_text()) if (RES / "AUDIT_portfolio.json").exists() else {"n_checks": 0, "n_fail": 0}
    ffa_rows = {f"{r['universe']} | {r['exit']} | {r['cost']}": r for r in d["ffa"]["rows"]}
    f0 = ffa_rows["deck core: SPY QQQ IWM DIA | deck rules | zero"]; fr_ = ffa_rows["deck core: SPY QQQ IWM DIA | deck rules | realistic"]
    sh_ = json.loads((RES / "FFA_summary.json").read_text())["D_share_ff_gt_020"]
    rr_rec = fl_mod.run(Dd, vol_, L_REC, False)[0]
    rec_yr = (1 + rr_rec).groupby(rr_rec.index.year).prod() - 1
    unl = ppr["unlevered"]["spot (as published)"]["test"]
    rec_row = flr.loc[rec]
    cy, tby = ppr["carry_yearly"], ppr["tbill_yearly"]
    cr = s["crisis"]
    fy = ppr["funding"]["yearly"]
    cheap = [str(y) for y in sorted(fy) if fy[y]["deribit_btc"] is not None and fy[y]["tbill"] is not None and fy[y]["deribit_btc"] < fy[y]["tbill"]]
    sw = ppr["ff_swap"]
    perp_rows = pd.DataFrame(ppr["grid"])
    perp_rows = perp_rows[perp_rows.crypto.str.startswith("perps") & (perp_rows.full_worst_year >= -0.15)]
    ps = ppr["sleeves"]
    exp_ = [h_["expected_cagr"] for h_ in s["haircut"] if h_["haircut"] in (0.25, 0.5)]
    mom_end = str(pd.read_csv(HERE.parent / "momentum" / "data" / "mom10_d.csv", index_col=0, parse_dates=True).index[-1].date())
    _cl = pd.DataFrame(json.loads((RES / "CL_summary.json").read_text())["rows"])
    _clb = _cl[_cl.fade_capped].sort_values("test_calmar").iloc[-1]          # best capped variant (fade levered too is excluded: VIX cap)
    _ffs = pd.read_csv(RES / "FFA_daily.csv", index_col=0, parse_dates=True)["deck core: SPY QQQ IWM DIA | deck rules | master"].fillna(0)
    _ffy = (1 + _ffs).groupby(_ffs.index.year).prod() - 1
    _var = pd.read_csv(RES / "CF_core_target_variants.csv")
    _v4 = _var[(_var.core_vol_target == 0.4) & (_var.period == "test")].iloc[0]
    _v6 = _var[(_var.core_vol_target == 0.6) & (_var.period == "test")].iloc[0]
    FACTS = {
        "last_date": pd.Timestamp(Dd.index[-1]).strftime("%-d %B %Y"), "audit_n": str(aud["n_checks"]), "audit_fail": str(aud["n_fail"]), "audit_note": str(aud.get("n_note", 0)),
        "audit_pass": str(aud["n_checks"] - aud["n_fail"] - aud.get("n_note", 0)), "mom_end": mom_end,
        "fade_test_cagr": pc(stt["vix_fade"]["cagr"]), "fade_test_sharpe": f"{stt['vix_fade']['sharpe']:.2f}", "vix_double": pc(s["stress_vix_double_total"], 1),
        "mom_test_cagr": pc(stt["momentum_ls"]["cagr"]),
        "carry_full_cagr": pc(ppr["carry_sleeve"]["full"]["cagr"], 1), "carry_test_cagr": pc(ppr["carry_sleeve"]["test"]["cagr"], 1),
        "carry_test_dd": pc(ppr["carry_sleeve"]["test"]["max_dd"], 1), "carry_on_pct": pc(ppr["carry_on_pct"]), "carry_corr": f"{ppr['carry_corr_core']:.2f}",
        "carry_2025": pc(cy["2025"], 1), "carry_2026": pc(cy["2026"], 1) + " (to date)", "tb_2025": pc(tby["2025"], 1), "tb_2026": pc(tby["2026"], 1),
        "ff_share": pc(min(sh_["SPY"], sh_["QQQ"], sh_["IWM"], sh_["DIA"]), 1) + "–" + pc(max(sh_["SPY"], sh_["QQQ"], sh_["IWM"], sh_["DIA"]), 1),
        "ff0_mar20": pc(f0["mar20"], 0, True), "ff0_win": pc(f0["win"]), "ffr_mar20": pc(fr_["mar20"], 0, True), "ffr_full_ex": pc(fr_["full_ex"], 1, True),
        "trade_2022": pc(cr["2022 bear market"]["Trading sleeves only"], 0, True), "trade_covid": pc(cr["COVID crash"]["Trading sleeves only"], 0, True),
        "trade_metals": pc(cr["Jan 2026 metals crash"]["Trading sleeves only"], 0, True),
        "rec_avg_lev": f"{rec_row.avg_lev:.2f}×", "unlev_calmar": f"{unl['calmar']:.2f}", "rec_calmar": f"{rec_row.test_calmar:.2f}",
        "hedge_spy_covid": pc(mgs["hedges"]["spy_bs_2020_02_03"]), "hedge_spy_yr": pc(mgs["hedges"]["spy_put_backspread_ann"], 1, True),
        "hedge_vix_yr": pc(mgs["hedges"]["vix_backspread_ann_per_unit"], 0, True), "unlev_over70": pc(UNLEV_OVER70),
        "rec_test_cagr": pc(rec_row.test_cagr, 1), "unlev_test_cagr": pc(unl["cagr"], 1), "rec_test_dd": pc(rec_row.test_max_dd), "rec_worst_year": pc(rec_yr.min()),
        "cheap_years": ", ".join(cheap) if cheap else "none",
        "perp_cost_btc": pc(ps["btc_spot"]["test"]["cagr"] - ps["btc_perp"]["test"]["cagr"], 1), "perp_cost_eth": pc(ps["eth_spot"]["test"]["cagr"] - ps["eth_perp"]["test"]["cagr"], 1),
        "perp_max_cagr": pc(perp_rows.test_cagr.max()), "perp_max_dd": pc(perp_rows.test_max_dd.min()),
        "swap_carry_cagr": pc(sw[f"published mix | target {L_REC}x"]["test"]["cagr"], 1),
        "swap_ff_cagr": pc(sw[f"previous mix (carry 10%, no FF) | target {L_REC}x"]["test"]["cagr"], 1),
        "swap_carry_dd": pc(sw[f"published mix | target {L_REC}x"]["test"]["max_dd"]),
        "swap_ff_dd": pc(sw[f"previous mix (carry 10%, no FF) | target {L_REC}x"]["test"]["max_dd"]),
        "cf_test_cagr": pc(X[P]["cagr"], 1), "core_test_cagr": pc(X[C]["cagr"], 1), "cf_test_dd": pc(X[P]["max_dd"]), "core_test_dd": pc(X[C]["max_dd"]),
        "cf_test_wy": pc(X[P]["worst_year"]), "core_test_wy": pc(X[C]["worst_year"]), "exp_lo": pc(min(exp_)), "exp_hi": pc(max(exp_)),
        "layer_add": f"{(rec_row.test_cagr - unl['cagr']) * 100:.1f} points",
        "crypto_crash": pc(s["stress_crypto_crash_total"], 1), "L_rec": f"{L_REC:g}×",
        "w_fade": pc(WCH["weights"]["vix_fade"]), "w_ff": pc(WCH["weights"]["ff_calendars"]), "w_carry": pc(WCH["weights"]["carry"]),
        "w_mom": pc(WCH["weights"]["momentum_ls"]), "w_pred": pc(WCH["weights"]["prediction"]), "w_chosen": WCH["chosen"],
        "ffm_mar20": pc(ffa_rows["deck core: SPY QQQ IWM DIA | deck rules | master"]["mar20"], 0, True),
        "ffm_win": pc(ffa_rows["deck core: SPY QQQ IWM DIA | deck rules | master"]["win"]),
        "ffm_d_ex": pc(ffa_rows["deck core: SPY QQQ IWM DIA | deck rules | master"]["d_ex"], 1, True),
        "ffm_t_ex": pc(ffa_rows["deck core: SPY QQQ IWM DIA | deck rules | master"]["t_ex"], 1, True),
        "ffr_d_ex": pc(ffa_rows["deck core: SPY QQQ IWM DIA | deck rules | realistic"]["d_ex"], 1, True),
        "ffr_t_ex": pc(ffa_rows["deck core: SPY QQQ IWM DIA | deck rules | realistic"]["t_ex"], 1, True),
        "vv_model_cagr": pc(VV["V3_fade_vs_SVXY"]["model"]["cagr"], 1), "vv_real_cagr": pc(VV["V3_fade_vs_SVXY"]["real_svxy"]["cagr"], 1),
        "vv_model_dd": pc(VV["V3_fade_vs_SVXY"]["model"]["max_dd"]), "vv_real_dd": pc(VV["V3_fade_vs_SVXY"]["real_svxy"]["max_dd"]),
        "vv_trades": str(VV["V4_trades"]["n"]), "vv_corr": f"{VV['V4_trades']['corr']:.2f}", "vv_old": pc(VV["V3_fade_vs_SVXY"]["old_check_unscaled_svxy_cagr"], 1),
        "vv_vixy_corr": f"{VV['V2_futures_model']['VIXY']['corr']:.2f}", "vv_vixy_beta": f"{VV['V2_futures_model']['VIXY']['beta_real_on_model']:.2f}",
        "vv_vixy_m": pc(VV["V2_futures_model"]["VIXY"]["cagr_model"]), "vv_vixy_r": pc(VV["V2_futures_model"]["VIXY"]["cagr_real"]),
        "vv_ncorr": str(len(d["vv"]["corrections"])), "vv_cost": pc(list(VV["V6_costs"]["drag_per_year"].values())[0], 2),
        "dc_A0_avg": pc(DS["results"]["A0 deck as written, at mid (upper bound)"]["avg_trade"], 0, True),
        "dc_E_avg": pc(DS["results"]["E deck entries, no kill switch or target, mid (upper bound)"]["avg_trade"], 0, True),
        "dcg_2022": pc(DS["results"]["G deck entries, no kill/target, MASTER fills"]["yearly"]["2022"], 0),
        "dcd_corr": f"{d['decay_diag']['direction']['corr_trade_return_vs_etp_move']:.2f}",
        "dcd_down": pc(list(d['decay_diag']['direction']['by_move'].values())[0]['avg_return'], 0, True),
        "dcd_up": pc(list(d['decay_diag']['direction']['by_move'].values())[-1]['avg_return'], 0, True),
        "dcs_net": pc(d['decay_diag']['shares_short']['mean_after_borrow'], 1, True), "dcs_d": pc(d['decay_diag']['shares_short']['mean_2011_20'], 1, True),
        "dcs_t": pc(d['decay_diag']['shares_short']['mean_2021_26'], 1, True),
        "dcr_ratio": f"{d['decay_diag']['real_costs']['ratio_model_to_real']:.1f}×", "dcr_real": pc(d['decay_diag']['real_costs']['median_real_pct'], 0),
        "dcr_model": pc(d['decay_diag']['real_costs']['median_model_pct'], 0),
        "dcw_calmar_dec": f"{max(c['design_calmar'] for c in WCH['candidates'] if c['decay'] > 0):.2f}",
        "dcw_calmar_base": f"{max(c['design_calmar'] for c in WCH['candidates'] if c['decay'] == 0):.2f}",
        "dcw_add": f"{(max(c['test_cagr'] for c in WCH['candidates'] if c['decay'] > 0) - max(c['test_cagr'] for c in WCH['candidates'] if c['decay'] == 0)) * 100:.1f} points at most",
        "decay_liquid": ", ".join(DS["liquid_universe"]),
        "cl_best_calmar": f"{_clb.test_calmar:.2f}", "cl_best_k": f"{_clb.k:g}× ({'VIX fade capped' if _clb.fade_capped else 'VIX fade levered too'})",
        "cl_best_cagr": pc(_clb.test_cagr, 1),
        "ff_top_years": ", ".join(f"{y} ({v * 100:+.1f}%)".replace("-", "−") for y, v in _ffy.sort_values(ascending=False).head(2).items()),
        "var_add": f"{(_v6.cagr - _v4.cagr) * 100:.1f} points", "var_dd_lo": pc(_v4.max_dd), "var_dd_hi": pc(_v6.max_dd),
    }
    d["num_audit"] = aud.get("checks", [])
    import re as _re
    main_html = (HERE / "cf_main.html").read_text()
    keys = set(_re.findall(r"\[\[(\w+)\]\]", main_html))
    missing = keys - set(FACTS)
    assert not missing, f"placeholders without a value: {missing}"
    for k_, v_ in FACTS.items():
        main_html = main_html.replace(f"[[{k_}]]", v_)
    assert not _re.search(r"\[\[[a-z_0-9]+\]\]", main_html), "unfilled placeholder"
    main_html = main_html.replace("</main>", "<!--FACTS " + json.dumps(FACTS, ensure_ascii=False) + " -->\n</main>", 1)
    html = ("<title>Core Frontier Portfolio</title>\n" + g_[g_.index('<meta name="claude:viewport'):g_.index("<style>")] + style + "\n"
            + main_html.replace("/*__HELPERS__*/", helpers.replace("/*__DATA__*/{}", json.dumps(d, separators=(",", ":")))
                                .replace("/*__STATS__*/{}", json.dumps(s, separators=(",", ":"), default=float))))
    out = HERE / "core_frontier.html"
    out.write_text(html)
    print(out, f"{out.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
