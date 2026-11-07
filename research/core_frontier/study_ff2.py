"""
FF RETEST ON THE INDEX AND ON SINGLE STOCKS -- with the Kadan-Liu-Tang (2017) volatility factor model.

Why the paper matters here
  Kadan, Liu & Tang show that if returns follow a factor model, implied VARIANCE does too:
        IV_i^2 = beta_i^2 * IV_market^2  +  idiosyncratic implied variance           (their eq. 3/5/8/9)
  and that the implied-minus-realised variance spread (split into systematic and idiosyncratic parts)
  predicts future JUMPS. Jumps are what kill a short-front calendar.

What we can do with free data
  * Index: SPX has four term points (VIX9D 9d, VIX 30d, VIX3M 93d, VIX6M 182d), so FF can be measured
    three ways: 9->30 (weekly calendars), 30->93 (the deck), 93->182 (slow calendars).
  * Stocks/ETFs: CBOE publishes ONLY a 30-day implied vol (VXAPL, VXAZN, VXGS, VXIBM; VXN for QQQ,
    RVX for IWM, VXEEM, VXEWZ, VXFXI, VXSLV, VXTLT). No back month -> no FF directly. We use the
    paper's decomposition to BUILD the back month:
        systematic part   beta^2 x SPX variance at that maturity (observed: VIX / VIX3M)
        idiosyncratic part  mean-reverts from today's level to its 1-year average (speed fitted on <= 2020)
    The same mean-reversion model is checked on the SPX itself, where the real VIX3M is known.
  * Kadan filter: skip entries when the implied-minus-realised variance spread is in its top third
    (the paper: high spread -> more jumps next month). Chosen on 2011-2020, judged on 2021+.

Trade (all underlyings): ATM call calendar, short 30d / long 90d (SPX 9->30 variant: short 9d / long 30d),
5% of sleeve equity per entry, at most one entry a week, at most 50% of equity in debits, exit when the
front has 7 days left (2 days for the 9d variant). Half bid/ask per leg: SPX 2%, ETFs 3%, single stocks 4%.
Signals: "always" (every week, the control), "deck" FF > 0.20, "top decile" FF above its own trailing
1-year 90th percentile, and each of those + the Kadan filter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "leaps_vix"))
import options_lib as ol  # noqa: E402

OUT, CB = HERE / "results", HERE / "data" / "cboe"
A_SPX = ol.PARAMS["A_VIX"]
A_STK = 0.92                      # ATM / model-free vol ratio for stocks & ETFs (flatter skew than the index)
NAMES = {"AAPL": ("VXAPL", "stock"), "AMZN": ("VXAZN", "stock"), "GS": ("VXGS", "stock"), "IBM": ("VXIBM", "stock"),
         "QQQ": ("VXN", "etf"), "IWM": ("RVX", "etf"), "EEM": ("VXEEM", "etf"), "EWZ": ("VXEWZ", "etf"),
         "FXI": ("VXFXI", "etf"), "SLV": ("VXSLV", "etf"), "TLT": ("VXTLT", "etf")}
# half bid/ask per option leg as a share of the option price, in calm markets (VIX < 25);
# x2 when VIX is 25-40 and x3 above 40. "realistic" = typical ATM 30-90d quotes; "deck" = the earlier, wider costs.
COSTS = {"zero": {}, "realistic": {"SPX": 0.003, "QQQ": 0.004, "IWM": 0.006, "AAPL": 0.006, "AMZN": 0.006, "EEM": 0.010,
                                   "TLT": 0.008, "SLV": 0.012, "FXI": 0.012, "EWZ": 0.012, "GS": 0.010, "IBM": 0.010},
         "deck": {"index": 0.02, "etf": 0.03, "stock": 0.04}}
KIND = {"SPX": "index", **{n: k for n, (_, k) in NAMES.items()}}


def half_spread(name, cost, vix):
    base = COSTS[cost].get(name, COSTS[cost].get(KIND[name], 0.0))
    return base * (1 if vix < 0.25 else 2 if vix < 0.40 else 3)
DESIGN_END = "2020-12-31"


def cboe(sym):
    d = pd.read_csv(CB / f"{sym}.csv")
    d.index = pd.to_datetime(d.iloc[:, 0], format="%m/%d/%Y")
    s = pd.to_numeric(d.iloc[:, -1], errors="coerce") / 100
    return s[s > 0].sort_index()


def g(T, k):                                           # average of an OU decay over T years
    return (1 - np.exp(-k * T)) / (k * T)


def fwd_ff(iv1, t1, iv2, t2):
    fv = (iv2 ** 2 * t2 - iv1 ** 2 * t1) / (t2 - t1)
    fwd = np.sqrt(np.maximum(fv, 1e-6))
    return (iv1 - fwd) / fwd


def term_iv(points):
    """points: list of (days, iv) at one date -> function days -> iv, linear in total variance."""
    ds = np.array([p[0] for p in points], float)
    w = np.array([p[1] ** 2 * p[0] for p in points])
    def f(d):
        d = max(d, 1.0)
        if d <= ds[0]:
            return points[0][1]
        if d >= ds[-1]:
            return np.sqrt((w[-1] + points[-1][1] ** 2 * (d - ds[-1])) / d)
        return np.sqrt(np.interp(d, ds, w) / d)
    return f


def simulate(S, rf, curve, signal, hfun, front=30, back=90, exit_left=7, frac=0.05, cap=0.5):
    """curve(t) -> function(days) -> ATM iv. Returns daily returns and trades."""
    eq_cash, pos, trades, curve_eq, last = 1.0, [], [], {}, None
    for t in S.index:
        St, r = S[t], rf[t]
        f = curve(t)
        h = hfun(t)
        if f is None:
            curve_eq[t] = eq_cash + sum(p["mark"] for p in pos)
            continue
        keep = []
        for p in pos:
            t1, t2 = (p["e1"] - t).days, (p["e2"] - t).days
            c1 = ol.bs_price(St, p["K"], max(t1, 0) / 365, r, 0.0, f(max(t1, 1)))
            c2 = ol.bs_price(St, p["K"], t2 / 365, r, 0.0, f(t2))
            p["mark"] = p["n"] * (c2 - c1)
            if t1 <= exit_left:
                a1 = ol.bs_price(St, St, max(t1, 0) / 365, r, 0.0, f(max(t1, 1)))       # cost = h x ATM price per expiry
                a2 = ol.bs_price(St, St, t2 / 365, r, 0.0, f(t2))
                proc = p["n"] * (c2 - c1 - h * (a1 + a2))
                eq_cash += proc
                trades.append({"entry": p["t0"], "ret": proc / p["debit"] - 1, "ff": p["ff"], "ks": p["ks"]})
            else:
                keep.append(p)
        pos = keep
        eq = eq_cash + sum(p["mark"] for p in pos)
        sig = signal.get(t, np.nan)
        if sig == sig and sig and (last is None or (t - last).days >= 7):
            open_d = sum(p["debit"] for p in pos)
            c1 = ol.bs_price(St, St, front / 365, r, 0.0, f(front))
            c2 = ol.bs_price(St, St, back / 365, r, 0.0, f(back))
            du = c2 * (1 + h) - c1 * (1 - h)
            budget = min(frac * eq, cap * eq - open_d)
            if du > 0 and budget > 0:
                eq_cash -= budget
                pos.append({"t0": t, "e1": t + pd.Timedelta(days=front), "e2": t + pd.Timedelta(days=back), "K": St,
                            "n": budget / du, "debit": budget, "mark": budget / du * (c2 - c1),
                            "ff": float(signal.attrs.get("ff", pd.Series(dtype=float)).get(t, np.nan)),
                            "ks": float(signal.attrs.get("ks", pd.Series(dtype=float)).get(t, np.nan))})
                last = t
        eq_cash *= np.exp(r / 252)
        curve_eq[t] = eq_cash + sum(p["mark"] for p in pos)
    e = pd.Series(curve_eq)
    return e.pct_change().fillna(0), pd.DataFrame(trades)


def summarise(r, tr, rf_d):
    out = {}
    for p, sl in {"design": slice(None, DESIGN_END), "test": slice("2021-01-01", None), "full": slice(None, None)}.items():
        x = r.loc[sl]
        ex = x - rf_d.reindex(x.index)
        eq = (1 + x).cumprod()
        t = tr[(tr.entry >= x.index[0]) & (tr.entry <= x.index[-1])] if len(tr) else tr
        out[p] = {"cagr": float(eq.iloc[-1] ** (252 / len(x)) - 1), "excess_cagr": float((1 + ex).prod() ** (252 / len(x)) - 1),
                  "sharpe": float(ex.mean() / ex.std() * np.sqrt(252)) if ex.std() > 0 else 0.0,
                  "max_dd": float((eq / eq.cummax() - 1).min()), "trades": int(len(t)),
                  "win": float((t.ret > 0).mean()) if len(t) else None, "avg_trade": float(t.ret.mean()) if len(t) else None}
    return out


def signals(ff, ks, ks_cut):
    pct90 = ff.rolling(252, min_periods=126).quantile(0.90).shift(1)
    base = {"always": pd.Series(True, index=ff.index), "deck FF>0.20": ff > 0.20, "top decile FF": (ff > pct90) & (ff > 0)}
    out = {}
    for k, s in base.items():
        out[k] = s
        if k != "always":
            out[k + " + Kadan filter"] = s & (ks < ks_cut)
    for s in out.values():
        s.attrs = {"ff": ff, "ks": ks}
    return out


def main():
    m = ol.load_market()
    rf = m["rf"]
    vix, v3 = m["vix"], m["vix3m"]
    v9, v6 = cboe("VIX9D"), cboe("VIX6M")
    import yfinance as yf
    tick = ["SPY"] + list(NAMES)
    raw = yf.download(tick, start="2003-01-01", progress=False, auto_adjust=False)
    close, adj = raw["Close"], raw["Adj Close"]
    R = adj.pct_change(fill_method=None)
    rf_d = rf / 252
    results, trades_all, rets = {}, {}, {}

    # ---------------- SPX: three FF definitions on real term points
    days = v9.index.intersection(v6.index).intersection(vix.dropna().index).intersection(v3.dropna().index)
    days = days[days >= "2011-02-01"]
    S = close["SPY"].reindex(days).ffill()
    pts = pd.DataFrame({"9": v9, "30": vix, "93": v3, "182": v6}).reindex(days).ffill() * A_SPX
    rv21 = R["SPY"].rolling(21).std().reindex(days) * np.sqrt(252)
    ks = (vix.reindex(days) ** 2 - rv21 ** 2)                       # implied - realised variance (market = systematic)
    curves = {t: term_iv([(9, a), (30, b), (93, c), (182, d)]) for t, (a, b, c, d) in pts.iterrows()}
    defs = {"SPX 30->93 (deck)": (fwd_ff(pts["30"], 30, pts["93"], 93), 30, 90, 7),
            "SPX 9->30 (weekly)": (fwd_ff(pts["9"], 9, pts["30"], 30), 9, 30, 2),
            "SPX 93->182 (slow)": (fwd_ff(pts["93"], 93, pts["182"], 182), 93, 182, 30)}
    ff_share = {}
    for name, (ff, fr, bk, ex) in defs.items():
        ks_cut = ks.loc[:DESIGN_END].quantile(2 / 3)
        ff_share[name] = {"pct_days_ff_gt_020": float((ff > 0.20).mean()), "pct_days_ff_gt_0": float((ff > 0).mean())}
        for sname, sig in signals(ff, ks, ks_cut).items():
            for cost in COSTS:
                hf = lambda t, c=cost: half_spread("SPX", c, vix.get(t, 0.2))
                r, tr = simulate(S, rf.reindex(days).ffill(), curves.get, sig, hf, fr, bk, ex)
                key = f"[{cost}] {name} | {sname}"
                results[key] = summarise(r, tr, rf_d)
                rets[key] = r
                trades_all[key] = tr

    # ---------------- Kadan model check on SPX: build "VIX3M" from VIX alone with the mean-reversion model
    v2 = vix ** 2
    theta = v2.rolling(252, min_periods=126).mean()
    dev, dev_f = (v2 - theta), (v2.shift(-21) - theta)
    xx = pd.concat([dev, dev_f], axis=1).dropna().loc[:DESIGN_END]
    rho_spx = float((xx.iloc[:, 0] * xx.iloc[:, 1]).sum() / (xx.iloc[:, 0] ** 2).sum())
    k_spx = -np.log(rho_spx) / (30 / 365)
    v3_model = np.sqrt(theta + (v2 - theta) * g(93 / 365, k_spx) / g(30 / 365, k_spx))
    ff_true, ff_mod = fwd_ff(vix, 30, v3, 93), fwd_ff(vix, 30, v3_model, 93)
    j = pd.concat([ff_true, ff_mod], axis=1).dropna()
    model_check = {"kappa": float(k_spx), "corr_ff_model_vs_true": float(j.corr().iloc[0, 1]),
                   "mae_ff": float((j.iloc[:, 0] - j.iloc[:, 1]).abs().mean()),
                   "hit_ff_gt_020": float(((j.iloc[:, 1] > 0.20) & (j.iloc[:, 0] > 0.20)).sum() / max((j.iloc[:, 1] > 0.20).sum(), 1)),
                   "corr_vix3m": float(pd.concat([v3, v3_model], axis=1).dropna().corr().iloc[0, 1])}

    # ---------------- stocks & ETFs: Kadan decomposition builds the back month
    iv30 = {n: cboe(c) for n, (c, _) in NAMES.items()}
    beta = {}
    for n in NAMES:
        x = pd.concat([R[n], R["SPY"]], axis=1).dropna()
        cov = x[n].rolling(250, min_periods=120).cov(x["SPY"])
        beta[n] = (cov / x["SPY"].rolling(250, min_periods=120).var()).shift(1)
    # idiosyncratic implied variance and its pooled mean-reversion speed (design years only)
    idio, th = {}, {}
    pairs = []
    for n in NAMES:
        i30 = iv30[n]
        b = beta[n].reindex(i30.index).ffill()
        sysv = b ** 2 * (vix.reindex(i30.index).ffill() ** 2)
        idio[n] = (i30 ** 2 - sysv).clip(lower=0.0025)
        th[n] = idio[n].rolling(252, min_periods=126).mean()
        d0, d1 = idio[n] - th[n], idio[n].shift(-21) - th[n]
        pairs.append(pd.concat([d0, d1], axis=1).dropna().loc[:DESIGN_END].values)
    P = np.vstack(pairs)
    rho_idio = float((P[:, 0] * P[:, 1]).sum() / (P[:, 0] ** 2).sum())
    k_idio = -np.log(rho_idio) / (30 / 365)
    decomp = {}
    stock_rets = {}
    for n, (c, kind) in NAMES.items():
        i30 = iv30[n]
        d = i30.index.intersection(close[n].dropna().index).intersection(v3.dropna().index)
        d = d[d >= "2011-02-01"]
        b = beta[n].reindex(d).ffill()
        vx, vx3 = vix.reindex(d).ffill(), v3.reindex(d).ffill()
        id30, thn = idio[n].reindex(d), th[n].reindex(d)
        def total_var(days_):
            T = days_ / 365
            spx = vx ** 2 if days_ <= 30 else (vx ** 2 * 30 + (vx3 ** 2 * 93 - vx ** 2 * 30) * min(days_ - 30, 63) / 63
                                                + vx3 ** 2 * max(days_ - 93, 0)) / days_
            return b ** 2 * spx + thn + (id30 - thn) * g(T, k_idio) / g(30 / 365, k_idio)
        iv30m = np.sqrt(total_var(30)) * A_STK
        iv93m = np.sqrt(total_var(93)) * A_STK
        ff = fwd_ff(iv30m, 30, iv93m, 93).dropna()
        sys_share = (b ** 2 * vx ** 2 / (i30.reindex(d) ** 2)).clip(0, 1)
        rv = R[n].rolling(21).std().reindex(d) * np.sqrt(252)
        rvs = R["SPY"].rolling(21).std().reindex(d) * np.sqrt(252)
        ks_s = b ** 2 * (vx ** 2 - rvs ** 2) + (id30 - (rv ** 2 - b ** 2 * rvs ** 2).clip(lower=0))   # sys + idio spread
        decomp[n] = {"avg_beta": float(b.mean()), "avg_systematic_share": float(sys_share.mean()),
                     "pct_days_ff_gt_020": float((ff > 0.20).mean()), "pct_days_ff_gt_0": float((ff > 0).mean())}
        cv = pd.DataFrame({"a": iv30m, "b": iv93m}).dropna()
        curves_s = {t: term_iv([(30, a), (93, bb)]) for t, (a, bb) in cv.iterrows()}
        S = close[n].reindex(d).ffill()
        ks_cut = ks_s.loc[:DESIGN_END].quantile(2 / 3)
        for sname, sig in signals(ff.reindex(d), ks_s, ks_cut).items():
            for cost in COSTS:
                hf = lambda t, c=cost, nn=n: half_spread(nn, c, vix.get(t, 0.2))
                r, tr = simulate(S, rf.reindex(d).ffill(), curves_s.get, sig, hf)
                key = f"[{cost}] {n} | {sname}"
                results[key] = summarise(r, tr, rf_d)
                stock_rets.setdefault((cost, sname), {})[n] = r
                trades_all[key] = tr

    # equal-weight baskets (single stocks, ETFs, all) per signal
    for (cost, sname), dct in stock_rets.items():
        df = pd.DataFrame(dct)
        for lab, cols in {"basket: 4 stocks": ["AAPL", "AMZN", "GS", "IBM"],
                          "basket: 7 ETFs": ["QQQ", "IWM", "EEM", "EWZ", "FXI", "SLV", "TLT"], "basket: all 11": list(NAMES)}.items():
            x = df[cols].dropna(how="all")
            r = x.mean(axis=1)
            tr = pd.concat([trades_all[f"[{cost}] {c} | {sname}"] for c in cols], ignore_index=True)
            key = f"[{cost}] {lab} | {sname}"
            results[key] = summarise(r, tr, rf_d)
            rets[key] = r

    # trade returns by Kadan-spread tercile (design-period cut points) -- does the paper's jump signal show up?
    by_ks = {}
    for grp, keys in {"SPX 30->93": ["[zero] SPX 30->93 (deck) | always"],
                      "stocks+ETFs": [f"[zero] {n} | always" for n in NAMES]}.items():
        t = pd.concat([trades_all[k] for k in keys], ignore_index=True).dropna(subset=["ks"])
        t["tercile"] = pd.qcut(t.ks.rank(method="first"), 3, labels=["low spread", "mid", "high spread (jump risk)"])
        by_ks[grp] = {str(k): {"n": int(len(v)), "win": float((v.ret > 0).mean()), "avg": float(v.ret.mean()),
                               "worst": float(v.ret.min())} for k, v in t.groupby("tercile", observed=True)}
        t["ffq"] = pd.qcut(t.ff.rank(method="first"), 4, labels=["FF q1 (low)", "q2", "q3", "FF q4 (high)"])
        by_ks[grp + " by FF quartile"] = {str(k): {"n": int(len(v)), "win": float((v.ret > 0).mean()), "avg": float(v.ret.mean())}
                                          for k, v in t.groupby("ffq", observed=True)}

    out = {"results": results, "ff_share_spx": ff_share, "decomposition": decomp, "kappa_idio": float(k_idio), "rho_idio": rho_idio,
           "spx_model_check": model_check, "by_kadan_spread": by_ks}
    (OUT / "FF2_summary.json").write_text(json.dumps(out, indent=1, default=float))
    pd.DataFrame(rets).to_csv(OUT / "FF2_daily.csv")
    rows = [{"key": k, **{f"{p}_{x}": v[p][x] for p in ["design", "test"] for x in ["excess_cagr", "sharpe", "max_dd", "trades", "win", "avg_trade"]}}
            for k, v in results.items()]
    pd.set_option("display.width", 260, "display.float_format", "{:.3f}".format, "display.max_rows", 200)
    print(pd.DataFrame(rows).to_string(index=False))
    print(json.dumps({"ff_share": ff_share, "decomp": decomp, "kappa_idio": k_idio, "model_check": model_check, "by_ks": by_ks}, indent=1, default=float))


if __name__ == "__main__":
    main()
