"""
STRUCTURAL-DECAY SLEEVE -- put spreads on decaying leveraged / futures-based ETPs (PTG "Structural Decay" deck).

Deck rules, implemented as written
  universe  Tier 1 VXX UVXY UVIX BOIL BITX | Tier 2 TECS SOXS LABD SPXU SQQQ | Tier 3 NVDL NVDS TSLL TSLS MSTU MSTX
  entry     Close < EMA20 < EMA50 (downtrend), ADX(10) > 20 (trend strength), RSI(2) > 70 (short the rip);
            VIX products only while the VIX curve is in contango (VIX < VIX3M)
  trade     debit put spread, ~7 months (6-9): long ATM put, short ~10-delta put
  size      max loss (the debit) = 1% of the portfolio = 10% of this 10% sleeve; one position per ticker
  exits     kill switch: Close > EMA20 and ADX < 18 | VIX products: curve inverts (VIX >= VIX3M)
            take profit at 50% of max profit (deck: 40-60%) | close 30 days before expiry (never hold to expiry)
  timing    signals on the close of day t, filled on the close of t+1 (no look-ahead)

Option prices (no free history of option quotes): Black-Scholes, with implied vol =
  k x (0.5 x EWMA realised vol + 0.5 x 1-year realised vol) of the ETP itself, and the 10-delta put at
  s x that vol. k and s are calibrated per ticker on TODAY's real chains (live_chains.py); tickers without a
  usable chain use the median. Sensitivity: implied vol x0.8 and x1.2.
Execution: bid/ask per leg = today's measured half-spread (% of each leg's at-the-money price, clipped), x1.5 when
  VIX > 30. "market" pays the full half-spread on every leg; "master" works each spread as ONE limit order near mid
  and pays 30% of it; "mid" pays nothing (upper bound).
Also: buy-and-hold decay facts (ETP vs leverage x underlying).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "leaps_vix"))
import options_lib as ol  # noqa: E402

OUT, DATA = HERE / "results", HERE / "data"
TIER = {"VXX": 1, "UVXY": 1, "UVIX": 1, "BOIL": 1, "BITX": 1, "TECS": 2, "SOXS": 2, "LABD": 2, "SPXU": 2, "SQQQ": 2,
        "NVDL": 3, "NVDS": 3, "TSLL": 3, "TSLS": 3, "MSTU": 3, "MSTX": 3}
VIXP = {"VXX", "UVXY", "UVIX"}
UNDER = {"TECS": ("XLK", -3), "SOXS": ("SOXX", -3), "LABD": ("XBI", -3), "SPXU": ("SPY", -3), "SQQQ": ("QQQ", -3),
         "BOIL": ("UNG", 2), "BITX": ("BTC-USD", 2), "NVDL": ("NVDA", 2), "NVDS": ("NVDA", -1.5), "TSLL": ("TSLA", 2),
         "TSLS": ("TSLA", -1), "MSTU": ("MSTR", 2), "MSTX": ("MSTR", 2), "UVXY": ("VIXY", 1.5), "UVIX": ("VIXY", 2)}
TENOR, EXIT_DTE, TP, RISK, VIX_WIDE = 213, 30, 0.50, 0.10, 0.30
COST_FRAC = {"market": 1.0, "master": 0.3, "mid": 0.0}


def load():
    f = DATA / "decay_ohlc.pkl"
    if not f.exists():
        import yfinance as yf
        tick = sorted(set(TIER) | {u for u, _ in UNDER.values()})
        d = yf.download(tick, start="2008-01-01", progress=False, auto_adjust=False)
        d[["Open", "High", "Low", "Close", "Adj Close"]].to_pickle(f)
    return pd.read_pickle(f)


def ema(x, n):
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(x, n):
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(h, l, c, n=10):
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr, n)
    pdi, ndi = 100 * wilder(pdm, n) / atr, 100 * wilder(ndm, n) / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return wilder(dx, n)


def rsi(c, n=2):
    d = c.diff()
    g, l_ = wilder(d.clip(lower=0), n), wilder((-d).clip(lower=0), n)
    return 100 - 100 / (1 + g / l_.replace(0, np.nan))


def put(S, K, T, r, s):
    if T <= 0:
        return max(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * s * s) * T) / (s * np.sqrt(T))
    return K * np.exp(-r * T) * norm.cdf(-(d1 - s * np.sqrt(T))) - S * norm.cdf(-d1)


def put_delta(S, K, T, r, s):
    if T <= 0:
        return -1.0 if S < K else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * s * s) * T) / (s * np.sqrt(T))
    return norm.cdf(d1) - 1


def spread_cost(S, K1, K2, T, r, s1, s2, c):
    """Half bid/ask in DOLLARS for the two legs: % half-spread moves from the ATM rate (|delta| >= 0.40) to the
    10-delta wing rate (|delta| <= 0.12) with the leg's delta, applied to that leg's own price, capped at the ATM price."""
    pa = put(S, S, T, r, s1)
    tot = 0.0
    for K, s in [(K1, s1), (K2, s2)]:
        p = put(S, K, T, r, s)
        a = abs(put_delta(S, K, T, r, s))
        w = float(np.clip((a - 0.12) / (0.40 - 0.12), 0, 1))
        hs = w * c["hs_atm"] + (1 - w) * c["hs_wing"]
        tot += hs * min(p, pa)
    return tot


def strike_for_delta(S, T, r, s, delta=-0.10):
    d1 = norm.ppf(1 + delta)                    # put delta = N(d1) - 1
    return S * np.exp(-(d1 * s * np.sqrt(T) - (r + 0.5 * s * s) * T))


def calibration(fname="decay_chain_calib.csv"):
    c = pd.read_csv(DATA / fname).dropna(subset=["atm_iv"])
    if "skew" not in c:
        c["skew"] = np.nan
    px = load()["Close"]
    rows = {}
    for _, r in c.iterrows():
        tk = r.tk
        if tk not in px:
            continue
        lr = np.log(px[tk]).diff().dropna()
        blend = 0.5 * np.sqrt((lr ** 2).ewm(alpha=0.06).mean().iloc[-1] * 252) + 0.5 * lr.tail(252).std() * np.sqrt(252)
        good = r.n >= 6 and abs(r.wing_delta + 0.10) <= 0.06          # enough strikes and a real 10-delta wing
        if "hs_atm" in r and r.hs_atm > 0.30:
            good = good and False
        rows[tk] = {"k": float(r.atm_iv / blend), "s": float(r["skew"]) if good else np.nan, "hs_atm": float(r.hs_atm),
                    "hs_wing": float(r.hs_wing) if good else np.nan, "usable": bool(good)}
    C = pd.DataFrame(rows).T
    C["usable"] = C.usable.astype(bool)
    for col in ["k", "s", "hs_atm", "hs_wing"]:
        C[col] = pd.to_numeric(C[col], errors="coerce")
    med = C[C.usable][["k", "s", "hs_atm", "hs_wing"]].median()
    out = {}
    for tk in TIER:
        x = C.loc[tk] if tk in C.index else None
        k = float(x.k) if x is not None and bool(x.usable) else float(med.k)
        s = float(x.s) if x is not None and bool(x.usable) else float(med.s)
        ha = float(np.clip(x.hs_atm if x is not None else med.hs_atm, 0.03, 0.30))
        hw = float(np.clip(x.hs_wing if x is not None and bool(x.usable) else med.hs_wing, 0.05, 0.60))
        out[tk] = {"k": float(np.clip(k, 0.6, 1.4)), "s": float(np.clip(s, 0.75, 1.05)), "hs_atm": ha, "hs_wing": hw,
                   "own_chain": bool(x is not None and bool(x.usable))}
    return out, {k: float(v) for k, v in med.items()}


def signals(px, vol):
    O, H, L_, C = px["Open"], px["High"], px["Low"], px["Close"]
    sig, kill, iv = {}, {}, {}
    vix, v3 = vol["^VIX"].reindex(C.index).ffill() / 100, vol["^VIX3M"].reindex(C.index).ffill() / 100
    for tk in TIER:
        c = C[tk].dropna()
        if len(c) < 300:
            continue
        h, l_ = H[tk].reindex(c.index), L_[tk].reindex(c.index)
        e20, e50, a10, r2 = ema(c, 20), ema(c, 50), adx(h, l_, c, 10), rsi(c, 2)
        s = (c < e20) & (e20 < e50) & (a10 > 20) & (r2 > 70)
        k = (c > e20) & (a10 < 18)
        if tk in VIXP:
            cont = (vix < v3).reindex(c.index).fillna(False)
            s, k = s & cont, k | ~cont
        lr = np.log(c).diff()
        blend = 0.5 * np.sqrt((lr ** 2).ewm(alpha=0.06).mean() * 252) + 0.5 * lr.rolling(252, min_periods=126).std() * np.sqrt(252)
        sig[tk], kill[tk], iv[tk] = s, k, blend
    return pd.DataFrame(sig), pd.DataFrame(kill), pd.DataFrame(iv), vix


def run(px, vol, rf, cal, mode="master", iv_mult=1.0, start="2011-01-01", tenor=TENOR, exit_dte=EXIT_DTE, universe=None):
    S_, K_, IV, vix = signals(px, vol)
    C = px["Close"]
    days = C.index[C.index >= start]
    f = COST_FRAC[mode]
    cash, pos, curve, trades = 1.0, {}, {}, []
    pending = {}
    for t in days:
        r = float(rf.get(t, 0.0)) * 252
        wide = 1.5 if vix.get(t, 0.2) > VIX_WIDE else 1.0
        # mark / exit
        for tk in list(pos):
            p = pos[tk]
            St, s0 = C.at[t, tk], IV.at[t, tk] if t in IV.index else np.nan
            if not (St == St and s0 == s0):
                continue
            T = (p["exp"] - t).days / 365
            s = s0 * cal[tk]["k"] * iv_mult
            v = put(St, p["K1"], T, r, s) - put(St, p["K2"], T, r, s * cal[tk]["s"])
            p["mark"] = p["n"] * v
            why = None
            if v >= p["debit_u"] + TP * (p["K1"] - p["K2"] - p["debit_u"]):
                why = "take profit"
            elif bool(K_.at[t, tk]) if t in K_.index and K_.at[t, tk] == K_.at[t, tk] else False:
                why = "kill switch"
            elif T * 365 <= exit_dte:
                why = "time"
            if why:
                cost = f * wide * spread_cost(St, p["K1"], p["K2"], T, r, s, s * cal[tk]["s"], cal[tk])
                proceeds = p["n"] * max(v - cost, 0.0)
                cash += proceeds
                trades.append({"tk": tk, "tier": TIER[tk], "entry": p["t0"], "exit": t, "why": why, "debit": p["debit"],
                               "pnl": proceeds - p["debit"], "ret": proceeds / p["debit"] - 1, "days": (t - p["t0"]).days})
                del pos[tk]
        eq = cash + sum(p["mark"] for p in pos.values())
        # fill yesterday's signals at today's close
        for tk in list(pending):
            del pending[tk]
            if tk in pos or tk not in C or not C.at[t, tk] == C.at[t, tk]:
                continue
            St, s0 = C.at[t, tk], IV.at[t, tk]
            if not s0 == s0:
                continue
            s = s0 * cal[tk]["k"] * iv_mult
            T = tenor / 365
            K1, K2 = St, strike_for_delta(St, T, r, s * cal[tk]["s"])
            v = put(St, K1, T, r, s) - put(St, K2, T, r, s * cal[tk]["s"])
            cost = f * wide * spread_cost(St, K1, K2, T, r, s, s * cal[tk]["s"], cal[tk])
            du = v + cost
            budget = min(RISK * eq, cash)
            if du <= 0 or budget <= 0 or du >= 0.9 * (K1 - K2):
                continue
            cash -= budget
            pos[tk] = {"t0": t, "exp": t + pd.Timedelta(days=tenor), "K1": K1, "K2": K2, "n": budget / du, "debit": budget,
                       "debit_u": du, "mark": budget / du * v}
        for tk in S_.columns:
            if universe is not None and tk not in universe:
                continue
            if t in S_.index and bool(S_.at[t, tk]) and tk not in pos:
                pending[tk] = True
        cash *= np.exp(r / 252)
        curve[t] = cash + sum(p["mark"] for p in pos.values())
    e = pd.Series(curve)
    return e.pct_change().fillna(0), pd.DataFrame(trades)


def decay_facts(px):
    A = px["Adj Close"]
    rows = []
    for tk, (u, L) in UNDER.items():
        x = pd.concat([A[tk], A[u]], axis=1, keys=["etp", "u"]).dropna()
        x = x[x.index.dayofweek < 5].loc["2015-01-01":] if len(x.loc["2015-01-01":]) > 250 else x[x.index.dayofweek < 5]
        if len(x) < 250:
            continue
        r = x.pct_change().dropna()
        yrs = len(r) / 252
        etp = (1 + r.etp).prod() ** (1 / yrs) - 1
        simple = (1 + L * r.u).prod() ** (1 / yrs) - 1          # leverage applied once a day, no fees (the "clean" version)
        hold = (1 + r.u).prod() ** (1 / yrs) - 1
        var = r.u.var() * 252
        rows.append({"etp": tk, "underlying": u, "L": L, "from": str(x.index[0].date()), "etp_cagr": etp, "underlying_cagr": hold,
                     "L_daily_no_fees_cagr": simple, "fee_and_financing_gap": etp - simple,
                     "variance_drag_formula": 0.5 * L * (L - 1) * var})
    return pd.DataFrame(rows)


def stats(r, rf, sl):
    x = r.loc[sl]
    ex = x - rf.reindex(x.index).fillna(0)
    eq = (1 + x).cumprod()
    return {"cagr": float(eq.iloc[-1] ** (252 / len(x)) - 1), "excess": float((1 + ex).prod() ** (252 / len(x)) - 1),
            "sharpe": float(ex.mean() / ex.std() * np.sqrt(252)) if ex.std() > 0 else 0.0, "max_dd": float((eq / eq.cummax() - 1).min())}


def main():
    px = load()
    vol = pd.read_csv(HERE.parent / "leaps_vix" / "data" / "vol_index_d.csv", index_col=0, parse_dates=True)
    rf = pd.read_csv(HERE.parent / "momentum" / "data" / "rf_daily.csv", index_col=0, parse_dates=True)["RF"]
    trading = vol.index[vol.index >= "2011-01-01"]
    px = px.reindex(trading)
    rf = rf.reindex(trading).ffill()
    cal, med = calibration("decay_chain_calib.csv")             # ~7-month chains (deck tenor)
    cal90, med90 = calibration("decay_chain_calib_75d.csv")     # ~2-3-month chains (master tenor)
    liquid = [tk for tk, c in cal90.items() if c["own_chain"] and c["hs_atm"] <= 0.10]
    configs = {
        "A deck as written, market fills": dict(cal=cal, mode="market"),
        "B deck as written, master fills": dict(cal=cal, mode="master"),
        "A0 deck as written, at mid (upper bound)": dict(cal=cal, mode="mid"),
        "C master protocol": dict(cal=cal90, mode="master", tenor=90, exit_dte=21, universe=liquid),
        "C master protocol, market fills": dict(cal=cal90, mode="market", tenor=90, exit_dte=21, universe=liquid),
        "C master protocol, IV x0.8": dict(cal=cal90, mode="master", tenor=90, exit_dte=21, universe=liquid, iv_mult=0.8),
        "C master protocol, IV x1.2": dict(cal=cal90, mode="master", tenor=90, exit_dte=21, universe=liquid, iv_mult=1.2),
        "C0 master protocol, at mid (upper bound)": dict(cal=cal90, mode="mid", tenor=90, exit_dte=21, universe=liquid),
    }
    res, daily, tr_all = {}, {}, {}
    for key, kw in configs.items():
        c_ = kw.pop("cal")
        r, tr = run(px, vol, rf, c_, **kw)
        daily[key], tr_all[key] = r, tr
        res[key] = {p: stats(r, rf, sl) for p, sl in {"design": slice(None, "2020-12-31"), "test": slice("2021-01-01", None),
                                                      "full": slice(None, None)}.items()}
        res[key]["trades"] = int(len(tr))
        res[key]["win"] = float((tr.ret > 0).mean()) if len(tr) else None
        res[key]["avg_trade"] = float(tr.ret.mean()) if len(tr) else None
        res[key]["exits"] = tr.why.value_counts().to_dict() if len(tr) else {}
        res[key]["by_tier"] = {int(k): {"n": int(len(g)), "win": float((g.ret > 0).mean()), "avg": float(g.ret.mean()),
                                        "pnl": float(g.pnl.sum())} for k, g in tr.groupby("tier")} if len(tr) else {}
        res[key]["by_ticker"] = {k: {"n": int(len(g)), "win": float((g.ret > 0).mean()), "avg": float(g.ret.mean())}
                                 for k, g in tr.groupby("tk")} if len(tr) else {}
        res[key]["yearly"] = {int(k): float(v) for k, v in ((1 + r).groupby(r.index.year).prod() - 1).items()}
    facts = decay_facts(load())
    pd.DataFrame(daily).to_csv(OUT / "DECAY_daily.csv")
    pd.concat([t.assign(key=k) for k, t in tr_all.items() if len(t)]).to_csv(OUT / "DECAY_trades.csv", index=False)
    facts.to_csv(OUT / "DECAY_facts.csv", index=False)
    (OUT / "DECAY_summary.json").write_text(json.dumps({"results": res, "calibration": cal, "calibration_90d": cal90, "liquid_universe": liquid,
                                                        "calibration_median": med,
                                                        "facts": facts.to_dict("records")}, indent=1, default=str))
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(facts.to_string(index=False))
    print(pd.DataFrame(cal90).T)
    for k, v in res.items():
        print(k, {p: {x: round(y, 3) for x, y in v[p].items()} for p in ["design", "test"]}, "trades", v["trades"], "win", v["win"] and round(v["win"], 2),
              "avg", v["avg_trade"] and round(v["avg_trade"], 3), v["exits"])
    print("liquid universe:", liquid)
    print("by ticker (C):", {k: (v["n"], round(v["win"], 2), round(v["avg"], 3)) for k, v in res["C master protocol"]["by_ticker"].items()})
    print("yearly (C):", {a: round(b, 3) for a, b in res["C master protocol"]["yearly"].items()})


if __name__ == "__main__":
    main()
