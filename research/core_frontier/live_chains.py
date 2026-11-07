"""
Live option-chain reader (Yahoo, last-close quotes). Implied vols are computed HERE from the bid/ask mid with
Black-Scholes (Yahoo's own impliedVolatility field is not used). Used for:
  * calibrating the decay sleeve's vol model (ATM IV / realised vol, 10-delta put skew, bid/ask), and
  * the execution desk's order tickets.
Quotes taken on a weekend are Friday's closing quotes and can be stale or wide.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm


def bs(S, K, T, r, sig, call):
    if T <= 0 or sig <= 0:
        return max(S - K, 0.0) if call else max(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    d2 = d1 - sig * np.sqrt(T)
    return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2) if call else K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_delta(S, K, T, r, sig, call):
    d1 = (np.log(S / K) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    return norm.cdf(d1) if call else norm.cdf(d1) - 1


def implied(price, S, K, T, r, call):
    intrinsic = max(S - K * np.exp(-r * T), 0) if call else max(K * np.exp(-r * T) - S, 0)
    if price <= intrinsic + 1e-6:
        return np.nan
    try:
        return brentq(lambda s: bs(S, K, T, r, s, call) - price, 0.01, 8.0)
    except ValueError:
        return np.nan


def chain(tk, target_days, r=0.04, kind="put"):
    """Quotes for the expiry nearest target_days, with our own IVs and deltas."""
    import yfinance as yf
    t = yf.Ticker(tk)
    S = float(t.history(period="5d")["Close"].dropna().iloc[-1])
    today = pd.Timestamp.today().normalize()
    exps = [(e, (pd.Timestamp(e) - today).days) for e in t.options]
    exps = [x for x in exps if x[1] > 7]
    e, d = min(exps, key=lambda x: abs(x[1] - target_days))
    oc = t.option_chain(e)
    df = (oc.puts if kind == "put" else oc.calls)[["strike", "bid", "ask", "volume", "openInterest"]].copy()
    df = df[(df.bid > 0) & (df.ask > df.bid)]
    T = d / 365
    df["mid"] = (df.bid + df.ask) / 2
    df["half_spread"] = (df.ask - df.bid) / 2 / df.mid
    df["iv"] = [implied(m, S, k, T, r, kind == "call") for m, k in zip(df.mid, df.strike)]
    df = df.dropna(subset=["iv"])
    df["delta"] = [bs_delta(S, k, T, r, s, kind == "call") for k, s in zip(df.strike, df.iv)]
    return {"ticker": tk, "spot": S, "expiry": e, "days": d, "T": T, "quotes": df.reset_index(drop=True)}


def atm_and_wing(c, wing_delta=-0.10):
    q = c["quotes"]
    atm = q.iloc[(q.strike - c["spot"]).abs().argsort()[:1]].iloc[0]
    wing = q.iloc[(q.delta - wing_delta).abs().argsort()[:1]].iloc[0]
    return atm, wing
