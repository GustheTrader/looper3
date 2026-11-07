"""
Option pricing + market model for the LEAPS / VIX study.

Free historical option quotes do not exist, so options are priced with
Black-Scholes(-Merton) / Black-76 using an implied-volatility SURFACE built
from free index data. Every modelling choice is a named parameter so it can
be calibrated (see calibrate.py) and stress-tested:

    ATM 30-day IV of SPY      = A_VIX * VIX                      (A_VIX fit to CBOE ^PUT)
    ATM IV at maturity T      = theta + (iv30 - theta) * g(T)/g(1/12)   mean-reversion term structure
                                g(T) = (1 - exp(-k T)) / (k T)    (k fit to VIX3M)
    smile / skew              = iv_atm + BETA_SKEW * ln(K/F) / sqrt(T)
    sector ETF IV             = SPY surface * (sector 63-day realised vol / SPY 63-day realised vol)

VIX options/futures:
    VIX futures price F(tau)  from the VIX / VIX3M variance term structure
                              (forward variance over [tau, tau+30d], minus a convexity term)
    VIX option IV             = VVIX * (1 + VIX_SKEW * ln(K/F))   (VIX smile slopes UP)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

HERE = Path(__file__).parent
DATA = HERE / "data"
MOM_DATA = HERE.parent / "momentum" / "data"

# -------------------------------------------------- calibrated / assumed parameters
PARAMS = {
    "A_VIX": 0.90,        # overwritten by calibrate.py results if present
    "K_MR": 3.0,          # per year, overwritten by calibrate.py results if present
    "THETA": 0.19,        # long-run SPX implied vol (VIX long-run mean ~19.5)
    "BETA_SKEW": -0.15,   # equity skew (vol per unit ln-moneyness / sqrt(T))
    "VIX_SKEW": 0.40,     # VIX call smile slope
    "VIX_BASIS": 1.0,     # scales the futures premium over spot VIX (fit to VIXY)
}
_cal = HERE / "results" / "calibration.json"
if _cal.exists():
    import json
    PARAMS.update({k: v for k, v in json.loads(_cal.read_text()).get("params", {}).items()})

# -------------------------------------------------- Black-Scholes / Black-76


def bs_price(S, K, T, r, q, sig, call=True):
    T = max(T, 1e-6)
    sig = max(sig, 1e-4)
    d1 = (np.log(S / K) + (r - q + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    d2 = d1 - sig * np.sqrt(T)
    if call:
        return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)


def bs_delta(S, K, T, r, q, sig, call=True):
    T = max(T, 1e-6)
    d1 = (np.log(S / K) + (r - q + 0.5 * sig ** 2) * T) / (max(sig, 1e-4) * np.sqrt(T))
    return np.exp(-q * T) * (norm.cdf(d1) if call else norm.cdf(d1) - 1)


def black76_call(F, K, T, r, sig):
    T = max(T, 1e-6)
    sig = max(sig, 1e-4)
    d1 = (np.log(F / K) + 0.5 * sig ** 2 * T) / (sig * np.sqrt(T))
    return np.exp(-r * T) * (F * norm.cdf(d1) - K * norm.cdf(d1 - sig * np.sqrt(T)))


# -------------------------------------------------- equity vol surface


def term_factor(T, k=None):
    k = PARAMS["K_MR"] if k is None else k
    g = lambda t: (1 - np.exp(-k * t)) / (k * t)
    return g(max(T, 1 / 365)) / g(1 / 12)


def atm_iv(iv30: float, T: float, rel: float = 1.0) -> float:
    """ATM implied vol at maturity T, given the 30-day ATM vol of SPY and the
    underlying's vol relative to SPY."""
    th = PARAMS["THETA"]
    return rel * (th + (iv30 - th) * term_factor(T))


def eff_rel(rel, T):
    """rel may be a float or (short-term ratio, long-run ratio): long-dated options
    price the sector's vol ratio drifting back to its long-run level."""
    if isinstance(rel, tuple):
        rs, rl = rel
        return rl + (rs - rl) * term_factor(T)
    return rel


def iv(S, K, T, r, q, iv30, rel=1.0):
    rel = eff_rel(rel, T)
    F = S * np.exp((r - q) * T)
    a = atm_iv(iv30, T, rel)
    return max(0.04, a + PARAMS["BETA_SKEW"] * rel * np.log(K / F) / np.sqrt(max(T, 1 / 365)))


def call_price(S, K, T, r, q, iv30, rel=1.0):
    return bs_price(S, K, T, r, q, iv(S, K, T, r, q, iv30, rel), True)


def call_delta(S, K, T, r, q, iv30, rel=1.0):
    return bs_delta(S, K, T, r, q, iv(S, K, T, r, q, iv30, rel), True)


def strike_for_delta(S, T, r, q, iv30, target, rel=1.0):
    """Call strike whose (smile-consistent) delta equals `target`."""
    f = lambda K: call_delta(S, K, T, r, q, iv30, rel) - target
    return brentq(f, S * 0.05, S * 5.0)


# -------------------------------------------------- trading-cost model


@dataclass
class Cost:
    half_spread_pct: float      # of option mid
    min_half_spread: float      # $ per share
    commission: float = 0.0065  # $0.65 per contract = $0.0065 per share

    def buy(self, mid):
        return mid + max(self.half_spread_pct * mid, self.min_half_spread) + self.commission

    def sell(self, mid):
        return max(0.0, mid - max(self.half_spread_pct * mid, self.min_half_spread) - self.commission)


COSTS = {
    "SPY": {"short": Cost(0.02, 0.01), "leaps": Cost(0.01, 0.05)},
    "SECTOR": {"short": Cost(0.04, 0.02), "leaps": Cost(0.025, 0.10)},
    "SECTOR_TIGHT": {"short": Cost(0.025, 0.01), "leaps": Cost(0.012, 0.05)},  # patient limit orders
    "VIX": Cost(0.05, 0.03),
}

# -------------------------------------------------- market data


def load_market():
    """Daily panel: raw & total-return ETF prices, dividend yields, rates, VIX family."""
    raw = pd.read_csv(DATA / "etf_raw_d.csv", index_col=0, parse_dates=True)
    adj = pd.read_csv(DATA / "etf_adj_d.csv", index_col=0, parse_dates=True)
    vol = pd.read_csv(DATA / "vol_index_d.csv", index_col=0, parse_dates=True)
    ff = pd.read_csv(MOM_DATA / "ff_factors_d.csv", index_col=0, parse_dates=True)
    idx = raw.index
    rfd = pd.read_csv(MOM_DATA / "rf_daily.csv", index_col=0, parse_dates=True)["RF"]   # precise daily T-bill return
    rf = (rfd * 252).reindex(idx).ffill().bfill()                   # annualised T-bill rate
    tr = np.log(adj / adj.shift(252)) - np.log(raw / raw.shift(252))
    q = tr.clip(0, 0.08).fillna(0.02)                               # trailing dividend yield
    lr = np.log(adj).diff()
    rv63 = lr.rolling(63, min_periods=40).std() * np.sqrt(252)
    rel = rv63.div(rv63["SPY"], axis=0).clip(0.6, 2.5).fillna(1.0)  # vol relative to SPY
    rv504 = lr.rolling(504, min_periods=126).std() * np.sqrt(252)
    rel_long = rv504.div(rv504["SPY"], axis=0).clip(0.6, 2.5).fillna(rel)  # long-run ratio
    v = vol.reindex(idx).ffill()
    return dict(raw=raw, adj=adj, q=q, rf=rf, rel=rel, rel_long=rel_long, vix=v["^VIX"] / 100,
                vix3m=v["^VIX3M"] / 100, vvix=v["^VVIX"] / 100, put=v["^PUT"], spx=v["^GSPC"],
                vixy=v["VIXY"], svxy=v["SVXY"], vol=vol)


# -------------------------------------------------- VIX futures model


def vix_future(vix, vix3m, vvix, tau_days):
    """Model price of a VIX future expiring in tau_days.

    VIX at expiry measures expected vol over [tau, tau+30d]. Its fair value is
    the square root of the FORWARD variance over that window (interpolated
    linearly in total variance between the 30-day VIX and the 93-day VIX3M,
    flat beyond 93 days), minus a convexity correction because E[sqrt(V)] <
    sqrt(E[V]) (Jensen), which scales with vol-of-vol (VVIX) and time.
    """
    def w(d):  # total implied variance to d days
        d = np.asarray(d, float)
        w30, w93 = vix ** 2 * 30, vix3m ** 2 * 93
        slope = (w93 - w30) / 63
        return np.where(d <= 30, vix ** 2 * d,
                        np.where(d <= 93, w30 + slope * (d - 30), w93 + vix3m ** 2 * (d - 93)))
    t = max(tau_days, 0.0)
    fwd_var = (w(t + 30) - w(t)) / 30
    fwd = np.sqrt(np.maximum(fwd_var, 1e-6))
    F = fwd * np.exp(-0.125 * vvix ** 2 * t / 365)
    return float(vix + PARAMS["VIX_BASIS"] * (F - vix))


def vix_call(F, K, tau_days, r, vvix):
    s = vvix * (1 + PARAMS["VIX_SKEW"] * np.log(K / F))
    return black76_call(F, K, tau_days / 365, r, max(s, 0.2))


# -------------------------------------------------- statistics


def perf(r: pd.Series, periods=12, rf: pd.Series | None = None) -> dict:
    r = r.dropna()
    if len(r) < 6:
        return {}
    ex = r - (rf.reindex(r.index).fillna(0) if rf is not None else 0)
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    n = len(r)
    cagr = eq.iloc[-1] ** (periods / n) - 1
    var5 = r.quantile(0.05)
    return {
        "start": str(r.index[0].date()), "end": str(r.index[-1].date()),
        "cagr": cagr, "ann_vol": r.std() * np.sqrt(periods),
        "sharpe": ex.mean() / ex.std() * np.sqrt(periods) if ex.std() > 0 else np.nan,
        "sortino": ex.mean() / ex[ex < 0].std() * np.sqrt(periods) if (ex < 0).sum() > 2 else np.nan,
        "max_dd": dd.min(), "calmar": cagr / -dd.min() if dd.min() < 0 else np.nan,
        "cvar5": r[r <= var5].mean(), "worst": r.min(), "best": r.max(),
    }
