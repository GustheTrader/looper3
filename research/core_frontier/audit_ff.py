"""
AUDIT & VALIDATION OF THE FF CALENDAR TRADE

Checks every link between the deck ("FF, Contango & Backwardation") and our backtests:

  A. Formula      FF = (front IV - forward IV) / forward IV, forward from total variance. Reproduce the
                  deck's own numbers (75% / 38% -> 0.97; COVID 30d 70-80% vs 90d 45-55%).
  B. Simulator    unit tests of the calendar pricer: flat world (theta only), front-IV crush, big move,
                  and the sign of every leg.
  C. Inputs       today's REAL option chains (Yahoo, mid quotes, our own Black-Scholes inversion):
                  - SPY 30d / 90d ATM IV vs our proxy 0.82 x VIX / 0.82 x VIX3M, and real FF vs proxy FF
                  - QQQ, IWM, DIA, GLD, TLT, AAPL, AMZN, GS, IBM: real 30d/90d IV vs the Kadan-model back month
                  - real at-the-money bid/ask half-spreads vs the cost table we used
  D. Deck replay  the deck as written: universe of liquid ETFs, FF > 0.20 to enter, VIX-band size 4/6/8/10%,
                  many concurrent positions (any day, one per ETF per day), 20% cash reserve, exit when
                  FF < 0.15 or 7 days before front expiry. Costs: zero / realistic / the deck's own crisis
                  frictions (20-30% bid-ask at peak panic). Compared with the deck's 2020 table.
  E. Skew         re-price the legs with a sticky-strike skew instead of flat ATM vol.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "leaps_vix"))
import options_lib as ol  # noqa: E402
import study_ff2 as f2  # noqa: E402

OUT = HERE / "results"
A = ol.PARAMS["A_VIX"]


# ------------------------------------------------------------------ A. formula
def check_formula():
    fwd = lambda i1, t1, i2, t2: np.sqrt((i2 ** 2 * t2 - i1 ** 2 * t1) / (t2 - t1))
    out = {"deck_example_75_38": float((0.75 - 0.38) / 0.38)}
    for front, back in [(0.70, 0.45), (0.75, 0.50), (0.80, 0.55)]:
        out[f"covid_{int(front*100)}_{int(back*100)}"] = float(f2.fwd_ff(front, 30, back, 90))
        out[f"covid_{int(front*100)}_{int(back*100)}_forward_iv"] = float(fwd(front, 30, back, 90))
    out["deck_contango_20_24"] = float(f2.fwd_ff(0.20, 30, 0.24, 90))
    out["FF>0 iff front>back (sample)"] = bool(all((f2.fwd_ff(a, 30, b, 90) > 0) == (a > b) for a, b in [(0.2, 0.19), (0.2, 0.21), (0.5, 0.3)]))
    return out


# ------------------------------------------------------------------ B. simulator unit tests
def check_simulator():
    days = pd.bdate_range("2020-01-01", periods=60)
    res = {}
    def run(path_S, iv_front, iv_back):
        S = pd.Series(path_S, index=days)
        rf = pd.Series(0.0, index=days)
        curves = {t: f2.term_iv([(30, iv_front[i]), (93, iv_back[i])]) for i, t in enumerate(days)}
        sig = pd.Series(False, index=days); sig.iloc[0] = True
        sig.attrs = {}
        r, tr = f2.simulate(S, rf, curves.get, sig, lambda t: 0.0, frac=1.0, cap=1.0)
        return float(tr.ret.iloc[0]) if len(tr) else None
    n = len(days)
    res["flat world (no move, IV 20/20)"] = run([100] * n, [0.2] * n, [0.2] * n)
    res["front IV crush 40->20, back 30->25"] = run([100] * n, list(np.linspace(0.4, 0.2, n)), list(np.linspace(0.3, 0.25, n)))
    res["back IV falls 30->20 (vega loss)"] = run([100] * n, [0.2] * n, list(np.linspace(0.3, 0.2, n)))
    res["10% drop on day 5, IV flat 20"] = run([100] * 5 + [90] * (n - 5), [0.2] * n, [0.2] * n)
    # analytic check of the flat-world number
    c1 = ol.bs_price(100, 100, 30 / 365, 0, 0, 0.2); c2 = ol.bs_price(100, 100, 90 / 365, 0, 0, 0.2)
    c1x = ol.bs_price(100, 100, 7 / 365, 0, 0, 0.2); c2x = ol.bs_price(100, 100, 67 / 365, 0, 0, 0.2)
    res["analytic flat world"] = float((c2x - c1x) / (c2 - c1) - 1)
    return res


# ------------------------------------------------------------------ C. live chains
def bs_iv(price, S, K, T, r, call):
    f = lambda s: ol.bs_price(S, K, T, r, 0.0, s, call) - price
    try:
        return brentq(f, 0.01, 3.0)
    except Exception:  # noqa: BLE001
        return np.nan


def chain_atm(tk, target_days, r=0.04):
    import yfinance as yf
    t = yf.Ticker(tk)
    S = float(t.history(period="5d")["Close"].iloc[-1])
    today = pd.Timestamp.today().normalize()
    exps = [(e, (pd.Timestamp(e) - today).days) for e in t.options]
    e, d = min(exps, key=lambda x: abs(x[1] - target_days))
    ch = t.option_chain(e)
    out = []
    for df, call in [(ch.calls, True), (ch.puts, False)]:
        df = df[(df.bid > 0) & (df.ask > 0)].copy()
        df["dist"] = (df.strike - S).abs()
        row = df.sort_values("dist").iloc[0]
        mid = (row.bid + row.ask) / 2
        out.append({"iv": bs_iv(mid, S, row.strike, d / 365, r, call), "half_spread": (row.ask - row.bid) / 2 / mid, "K": row.strike})
    return {"exp": e, "days": d, "S": S, "atm_iv": float(np.nanmean([o["iv"] for o in out])),
            "half_spread": float(np.mean([o["half_spread"] for o in out]))}


def check_live():
    m = ol.load_market()
    vix, v3 = float(m["vix"].dropna().iloc[-1]), float(m["vix3m"].dropna().iloc[-1])
    rows = {}
    for tk in ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "SLV", "EEM", "AAPL", "AMZN", "GS", "IBM"]:
        try:
            a, b = chain_atm(tk, 30), chain_atm(tk, 90)
            rows[tk] = {"iv_30": a["atm_iv"], "days_30": a["days"], "iv_90": b["atm_iv"], "days_90": b["days"],
                        "real_ff": float(f2.fwd_ff(a["atm_iv"], a["days"], b["atm_iv"], b["days"])),
                        "half_spread_30": a["half_spread"], "half_spread_90": b["half_spread"]}
        except Exception as e:  # noqa: BLE001
            rows[tk] = {"error": str(e)}
    rows["SPY"]["proxy_iv_30"] = A * vix
    rows["SPY"]["proxy_iv_90"] = float(f2.term_iv([(30, A * vix), (93, A * v3)])(rows["SPY"]["days_90"]))
    rows["SPY"]["proxy_ff"] = float(f2.fwd_ff(A * vix, 30, A * v3, 93))
    # Kadan-model back month for the names with a CBOE 30d index, using today's real 30d IV as the input
    R = pd.DataFrame({tk: __import__("yfinance").download(tk, period="2y", progress=False, auto_adjust=True)["Close"].squeeze()
                      for tk in ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "SLV", "EEM", "AAPL", "AMZN", "GS", "IBM"]}).pct_change()
    k_idio = json.loads((OUT / "FF2_summary.json").read_text())["kappa_idio"]
    for tk in ["QQQ", "IWM", "DIA", "GLD", "TLT", "SLV", "EEM", "AAPL", "AMZN", "GS", "IBM"]:
        x = R[[tk, "SPY"]].dropna().iloc[-250:]
        beta = float(x[tk].cov(x["SPY"]) / x["SPY"].var())
        i30 = rows[tk].get("iv_30")
        if i30 is None or np.isnan(i30):
            continue
        sys30 = beta ** 2 * (A * vix) ** 2
        idio = max(i30 ** 2 - sys30, 0.0025)
        theta = idio      # no trailing history of the chain here -> assume today's idio is its average (no mean-reversion term)
        T = rows[tk]["days_90"]
        spx = f2.term_iv([(30, A * vix), (93, A * v3)])(T) ** 2
        iv90m = np.sqrt(beta ** 2 * spx + theta)
        rows[tk] |= {"beta": beta, "model_iv_90": float(iv90m),
                     "model_ff": float(f2.fwd_ff(i30, rows[tk]["days_30"], iv90m, T)), "systematic_share": float(sys30 / i30 ** 2)}
    return {"as_of": str(pd.Timestamp.today().date()), "vix": vix, "vix3m": v3, "rows": rows}


# ------------------------------------------------------------------ D. deck-faithful replay on an ETF universe
MIN_DEBIT = 0.20     # skip calendars whose debit is under 20% of the back option (near-zero debits = hidden leverage)


def deck_replay(universes=None):
    m = ol.load_market()
    rf, vix, v3 = m["rf"], m["vix"], m["vix3m"]
    import yfinance as yf
    vols = {"QQQ": ("cboe", "VXN"), "IWM": ("cboe", "RVX"), "DIA": ("yahoo", "^VXD"), "GLD": ("yahoo", "^GVZ"),
            "USO": ("yahoo", "^OVX"), "EEM": ("cboe", "VXEEM"), "EWZ": ("cboe", "VXEWZ"), "FXI": ("cboe", "VXFXI"),
            "SLV": ("cboe", "VXSLV"), "TLT": ("cboe", "VXTLT")}
    ylist = ["SPY"] + list(vols) + [v[1] for v in vols.values() if v[0] == "yahoo"]
    raw = yf.download(ylist, start="2009-01-01", progress=False, auto_adjust=False)
    close, adj = raw["Close"], raw["Adj Close"]
    R = adj.pct_change(fill_method=None)
    iv30 = {k: (f2.cboe(s) if src == "cboe" else close[s].dropna() / 100) for k, (src, s) in vols.items()}
    k_idio = json.loads((OUT / "FF2_summary.json").read_text())["kappa_idio"]
    days = vix.dropna().index.intersection(v3.dropna().index)
    days = days[(days >= "2011-03-01")]
    curves, ffs = {}, {}
    # SPY: real term points
    spy_pts = (pd.DataFrame({"30": vix, "93": v3}).reindex(days).ffill() * A)
    curves["SPY"] = {t: f2.term_iv([(30, a), (93, b)]) for t, (a, b) in spy_pts.iterrows()}
    ffs["SPY"] = f2.fwd_ff(spy_pts["30"], 30, spy_pts["93"], 93)
    for k in vols:
        i30 = iv30[k].reindex(days).ffill(limit=3)
        x = pd.concat([R[k], R["SPY"]], axis=1).dropna()
        beta = (x[k].rolling(250, min_periods=120).cov(x["SPY"]) / x["SPY"].rolling(250, min_periods=120).var()).shift(1).reindex(days).ffill()
        vx, vx3 = vix.reindex(days).ffill(), v3.reindex(days).ffill()
        idio = (i30 ** 2 - beta ** 2 * vx ** 2).clip(lower=0.0025)
        th = idio.rolling(252, min_periods=126).mean()
        sp93 = vx3 ** 2
        iv30m = np.sqrt(beta ** 2 * vx ** 2 + idio) * f2.A_STK
        iv93m = np.sqrt(beta ** 2 * sp93 + th + (idio - th) * f2.g(93 / 365, k_idio) / f2.g(30 / 365, k_idio)) * f2.A_STK
        cv = pd.DataFrame({"a": iv30m, "b": iv93m}).dropna()
        curves[k] = {t: f2.term_iv([(30, a), (93, b)]) for t, (a, b) in cv.iterrows()}
        ffs[k] = f2.fwd_ff(cv.a, 30, cv.b, 93)
    FF = pd.DataFrame(ffs).reindex(days)
    PX = close[["SPY"] + list(vols)].reindex(days).ffill()
    # live-measured at-the-money half-spreads (mean of the 30d and 90d legs) from the chain check, where available
    live_hs = {}
    try:
        rows_ = json.loads((OUT / "FFA_summary.json").read_text())["C_live"]["rows"]
        live_hs = {k: 0.5 * (v["half_spread_30"] + v["half_spread_90"]) for k, v in rows_.items() if "half_spread_30" in v}
    except Exception:  # noqa: BLE001
        pass
    band = lambda v: 1 if v < 0.25 else 2 if v < 0.40 else 3
    old_tbl = lambda k, v: f2.half_spread("SPX" if k == "SPY" else k if k in f2.COSTS["realistic"] else "EEM", "realistic", v)
    meas = lambda k, v: live_hs[k] * band(v) if k in live_hs else old_tbl(k, v)
    costs = {"zero": lambda k, v: 0.0,
             "realistic": meas,                                   # live-measured spreads, x2 / x3 in stressed markets
             "master": lambda k, v: 0.3 * meas(k, v),             # each calendar worked as ONE combo limit order: 30% of the spread
             "earlier cost table": old_tbl,
             "deck frictions (20-30% bid/ask at panic)": lambda k, v: (0.005 if v < 0.25 else 0.02 if v < 0.40 else 0.125) * (1 if k in ("SPY", "QQQ", "IWM", "DIA") else 1.5)}
    size = lambda v: 0.04 if v < 0.20 else 0.06 if v < 0.25 else 0.08 if v < 0.30 else 0.10
    out, daily, trades_out, exposure = {}, {}, {}, {}
    universes = universes or {"deck core: SPY QQQ IWM DIA": ["SPY", "QQQ", "IWM", "DIA"],
                              "extended: + GLD SLV TLT EEM EWZ FXI": ["SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "EEM", "EWZ", "FXI"]}
    for (uname, uni), variant in [(u, v) for u in universes.items() for v in ["deck rules", "deck rules, hold to 7 days left (no FF exit)"]]:
        for cname, cfun in costs.items():
            maxpos = 0
            expo_ = {}
            cash, pos, curve, trades = 1.0, [], {}, []
            for t in days:
                v = float(vix.get(t, np.nan))
                r = float(rf.get(t, 0.0))
                keep = []
                for p in pos:
                    k = p["k"]
                    f = curves[k].get(t)
                    St = PX.at[t, k]
                    if f is None or St != St:
                        keep.append(p); continue
                    t1, t2 = (p["e1"] - t).days, (p["e2"] - t).days
                    c1 = ol.bs_price(St, p["K"], max(t1, 0) / 365, r, 0.0, f(max(t1, 1)))
                    c2 = ol.bs_price(St, p["K"], t2 / 365, r, 0.0, f(t2))
                    p["mark"] = p["n"] * (c2 - c1)
                    ffk = FF.at[t, k]
                    if t1 <= 7 or (variant == "deck rules" and ffk == ffk and ffk < 0.15):
                        h = cfun(k, v)
                        a1 = ol.bs_price(St, St, max(t1, 0) / 365, r, 0.0, f(max(t1, 1)))   # cost = h x ATM price per expiry
                        a2 = ol.bs_price(St, St, t2 / 365, r, 0.0, f(t2))
                        proc = p["n"] * (c2 - c1 - h * (a1 + a2))
                        cash += proc
                        trades.append({"k": k, "entry": p["t0"], "exit": t, "ret": proc / p["debit"] - 1, "ff": p["ff"], "pnl": proc - p["debit"]})
                    else:
                        keep.append(p)
                pos = keep
                eq = cash + sum(p["mark"] for p in pos)
                for k in uni:
                    ffk = FF.at[t, k]
                    f = curves[k].get(t)
                    St = PX.at[t, k]
                    if not (ffk == ffk and ffk > 0.20) or f is None or St != St:
                        continue
                    if any(p["k"] == k and p["t0"] == t for p in pos):
                        continue
                    open_d = sum(p["mark"] for p in pos)
                    budget = min(size(v) * eq, 0.80 * eq - open_d)
                    if budget <= 0:
                        break
                    h = cfun(k, v)
                    c1 = ol.bs_price(St, St, 30 / 365, r, 0.0, f(30))
                    c2 = ol.bs_price(St, St, 90 / 365, r, 0.0, f(90))
                    du = c2 * (1 + h) - c1 * (1 - h)
                    if du <= MIN_DEBIT * c2:
                        continue
                    cash -= budget
                    pos.append({"k": k, "t0": t, "e1": t + pd.Timedelta(days=30), "e2": t + pd.Timedelta(days=90), "K": St,
                                "n": budget / du, "debit": budget, "mark": budget / du * (c2 - c1), "ff": float(ffk)})
                cash *= np.exp(r / 252)
                maxpos = max(maxpos, len(pos))
                curve[t] = cash + sum(p["mark"] for p in pos)
                expo_[t] = sum(p["mark"] for p in pos) / curve[t] if curve[t] > 0 else 0.0
            e = pd.Series(curve)
            rr = e.pct_change().fillna(0)
            tr = pd.DataFrame(trades)
            key = f"{uname} | {variant} | {cname}"
            daily[key] = rr
            exposure[key] = pd.Series(expo_)
            mo = (1 + rr).resample("ME").prod() - 1
            yr = (1 + rr).groupby(rr.index.year).prod() - 1
            exr = rr - rf.reindex(rr.index).fillna(0) / 252
            t20 = tr[(tr.entry >= "2020-01-01") & (tr.entry <= "2020-12-31")] if len(tr) else tr
            out[key] = {"cagr": float(e.iloc[-1] ** (252 / len(e)) - 1), "sharpe": float(exr.mean() / exr.std() * np.sqrt(252)),
                        "max_dd": float((e / e.cummax() - 1).min()), "trades": int(len(tr)), "win": float((tr.ret > 0).mean()),
                        "avg_trade": float(tr.ret.mean()), "max_open_positions": maxpos,
                        "pnl_2020_by_etf": {k: float(g.pnl.sum()) for k, g in t20.groupby("k")} if len(t20) else {},
                        "mar_2020": float(mo.get(pd.Timestamp("2020-03-31"), np.nan)), "year_2020": float(yr.get(2020, np.nan)),
                        "trades_2020": int(len(t20)), "win_2020": float((t20.ret > 0).mean()) if len(t20) else None,
                        "months_2020": {str(k.date())[:7]: float(v) for k, v in mo.loc["2020"].items()},
                        "yearly": {int(k): float(v) for k, v in yr.items()},
                        "by_etf": {k: {"n": int(len(g)), "win": float((g.ret > 0).mean()), "avg": float(g.ret.mean()), "pnl": float(g.pnl.sum())}
                                   for k, g in tr.groupby("k")} if len(tr) else {},
                        "pct_days_invested": float((e.diff().abs() > 1e-12).mean())}
            trades_out[key] = tr
    share = {k: float((FF[k] > 0.20).mean()) for k in FF.columns}
    pd.DataFrame(daily).to_csv(OUT / "FFA_daily.csv")
    pd.DataFrame(exposure).to_csv(OUT / "FFA_exposure.csv")       # open calendar value / sleeve equity (for margin use)
    pd.concat([t.assign(key=k) for k, t in trades_out.items() if len(t)], ignore_index=True).to_csv(OUT / "FFA_trades.csv", index=False)
    return out, share


# ------------------------------------------------------------------ E. skew sensitivity on SPY (deck rules, realistic cost)
def skew_check():
    m = ol.load_market()
    # one representative position each: 2020-03-16 (FF peak) and 2008-10-24, priced flat-ATM vs sticky-strike skew
    res = {}
    for d0 in ["2020-03-09", "2020-03-16", "2008-10-10", "2022-03-07"]:
        d0 = pd.Timestamp(d0)
        S = m["raw"]["SPY"]; v, v3 = m["vix"], m["vix3m"]
        if d0 not in S.index:
            continue
        t1d = S.index[S.index.get_indexer([d0 + pd.Timedelta(days=23)], method="bfill")[0]]
        S0, S1 = S[d0], S[t1d]
        out = {}
        for mode in ["flat ATM", "sticky-strike skew"]:
            def price(St, K, T, vx, vv, tt):
                ivt = f2.term_iv([(30, A * vx), (93, A * vv)])(tt)
                if mode == "flat ATM":
                    return ol.bs_price(St, K, T, 0.0, 0.0, ivt)
                Tm = max(T, 1 / 365)
                skew = ol.PARAMS["BETA_SKEW"] * np.log(K / St) / np.sqrt(Tm)     # same smile slope as options_lib, around the ATM vol
                return ol.bs_price(St, K, T, 0.0, 0.0, max(0.04, ivt + skew))
            e = price(S0, S0, 90 / 365, v[d0], v3[d0], 90) - price(S0, S0, 30 / 365, v[d0], v3[d0], 30)
            x = price(S1, S0, 67 / 365, v[t1d], v3[t1d], 67) - price(S1, S0, 7 / 365, v[t1d], v3[t1d], 7)
            out[mode] = float(x / e - 1)
        res[str(d0.date())] = {**out, "spy_move": float(S1 / S0 - 1), "vix_in": float(v[d0]), "vix_out": float(v[t1d]),
                               "ff_in": float(f2.fwd_ff(A * v[d0], 30, A * v3[d0], 93))}
    return res


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "D":
        out = json.loads((OUT / "FFA_summary.json").read_text())
        rep, share = deck_replay()
        out["D_replay"], out["D_share_ff_gt_020"] = rep, share
        (OUT / "FFA_summary.json").write_text(json.dumps(out, indent=1, default=float))
        report(rep, share)
        return
    out = {"A_formula": check_formula(), "B_simulator": check_simulator()}
    print(json.dumps(out, indent=1))
    try:
        out["C_live"] = check_live()
        print(json.dumps(out["C_live"], indent=1, default=float))
    except Exception as e:  # noqa: BLE001
        out["C_live"] = {"error": str(e)}
        print("live failed", e)
    out["E_skew"] = skew_check()
    print(json.dumps(out["E_skew"], indent=1))
    rep, share = deck_replay()
    out["D_replay"], out["D_share_ff_gt_020"] = rep, share
    (OUT / "FFA_summary.json").write_text(json.dumps(out, indent=1, default=float))
    report(rep, share)


def report(rep, share):
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame({k: {x: v[x] for x in ["cagr", "sharpe", "max_dd", "trades", "win", "avg_trade", "mar_2020", "year_2020", "trades_2020", "win_2020", "max_open_positions"]}
                        for k, v in rep.items()}).T)
    for k, v in rep.items():
        print(k, {a: round(b, 3) for a, b in v["months_2020"].items()})
        print("  by ETF", {a: (b["n"], round(b["win"], 2), round(b["avg"], 3)) for a, b in v["by_etf"].items()})
    print("share FF>0.20", share)


if __name__ == "__main__":
    main()
