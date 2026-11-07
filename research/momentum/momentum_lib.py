"""
Small, dependency-light momentum backtest engine (monthly rebalancing).

Timing convention (important -- this is where most backtests cheat by accident):
    * Signal is computed with data up to the END of month t.
    * The portfolio formed at the end of month t earns the return of month t+1.
    * "skip" S drops the most recent S months from the lookback window
      (e.g. the classic 12-1 momentum is lookback L=12, skip S=1: it uses
      months t-11 .. t-1 and ignores month t, which tends to reverse).

Holding period H uses Jegadeesh-Titman overlapping portfolios: each month
1/H of the book is re-formed, so the live portfolio is the average of the
last H formation-month portfolios.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

DATA = Path(__file__).parent / "data"

# ----------------------------------------------------------------- loading


def load_french(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / f"{name}.csv", index_col=0, parse_dates=True)


def market_monthly() -> tuple[pd.Series, pd.Series]:
    """Whole-US-market total return and the risk-free rate (CRSP via French)."""
    ff = load_french("ff_factors_m")
    return (ff["Mkt-RF"] + ff["RF"]).rename("MKT"), ff["RF"]


def yahoo_monthly(file: str, max_abs_ret: float = 3.0) -> pd.DataFrame:
    """Adjusted daily closes -> month-end simple returns.

    Free Yahoo data has occasional bad ticks; a monthly move above +300% or
    below -95% in an S&P 500 stock is almost always an error, so we drop it.
    """
    px = pd.read_csv(DATA / file, index_col=0, parse_dates=True)
    m = px.resample("ME").last()
    r = m.pct_change(fill_method=None)
    r = r.mask((r > max_abs_ret) | (r < -0.95))
    # Drop the current, not-yet-finished month.
    last_daily = px.index.max()
    if last_daily < (last_daily + pd.offsets.MonthEnd(0)) - pd.Timedelta(days=3):
        r = r.iloc[:-1]
    return r


# ----------------------------------------------------------------- signals


def cum_return(ret: pd.DataFrame, L: int, S: int = 0) -> pd.DataFrame:
    """Compounded return over months t-S-L+1 .. t-S (NaN unless all present)."""
    lr = np.log1p(ret)
    s = lr.rolling(L, min_periods=L).sum().shift(S)
    return np.expm1(s)


def spsr(ret: pd.DataFrame, mkt: pd.Series, L: int, S: int = 0) -> pd.DataFrame:
    """Sector Power Strength Rating (0-100) -- a composite relative-strength score.

    Three ingredients, each measured over the same lookback L (skipping S):
      1. Relative strength: compounded return minus the market's.
      2. Risk-adjusted strength: mean monthly excess-vs-market return / its
         volatility (an "information ratio"; penalises lucky, jumpy sectors).
      3. Consistency: share of months the sector beat the market
         (smooth, steady leadership persists better than one big jump --
         the "frog-in-the-pan" effect of Da, Gurun & Warachka 2014).
    For short lookbacks (<6 months) the volatility and consistency pieces use a
    6-month window so they are not computed from 1-2 data points.
    Each ingredient is z-scored across sectors each month, averaged, then
    converted to a 0-100 percentile rank (100 = strongest sector).
    """
    ex = ret.sub(mkt.reindex(ret.index), axis=0)
    w = max(L, 6)
    rs = cum_return(ret, L, S).sub(cum_return(mkt.to_frame("m"), L, S)["m"].reindex(ret.index), axis=0)
    mu = ex.rolling(w, min_periods=w).mean().shift(S)
    sd = ex.rolling(w, min_periods=w).std().shift(S)
    ir = mu / sd
    cons = (ex > 0).astype(float).where(ex.notna()).rolling(w, min_periods=w).mean().shift(S)

    def z(x):
        return x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1), axis=0)

    score = pd.concat([z(rs), z(ir), z(cons)]).groupby(level=0).mean()
    score = score.where(rs.notna())
    return score.rank(axis=1, pct=True) * 100


# ----------------------------------------------------------------- portfolios


def rank_weights(signal: pd.DataFrame, n_long=None, n_short=None, frac=None,
                 long_only: bool = False, allowed: pd.DataFrame | None = None,
                 allowed_short: pd.DataFrame | None = None) -> pd.DataFrame:
    """Equal-weight long the top names, short the bottom names, each formation month.

    Pick either a fixed count (n_long / n_short) or a fraction of the eligible
    universe (frac=0.1 -> top & bottom decile). `allowed` / `allowed_short`
    are optional boolean masks restricting which names may enter each leg
    (used for the sector-concentrated version). Long leg sums to +1, short to -1.
    """
    sig = signal.to_numpy(dtype=float)
    al = None if allowed is None else allowed.reindex_like(signal).fillna(False).to_numpy(bool)
    ash = None if allowed_short is None else allowed_short.reindex_like(signal).fillna(False).to_numpy(bool)
    W = np.zeros_like(sig)
    for t in range(sig.shape[0]):
        row = sig[t]
        ok = ~np.isnan(row)
        n = ok.sum()
        if n < 4:
            continue
        kl = n_long if n_long else max(1, int(round(frac * n)))
        ks = n_short if n_short else max(1, int(round(frac * n)))
        order = np.argsort(np.where(ok, row, np.nan))  # NaNs sort last
        valid = order[:n]
        longs = valid[::-1]
        if al is not None:
            longs = longs[al[t, longs]]
        longs = longs[:kl]
        if len(longs):
            W[t, longs] = 1.0 / len(longs)
        if not long_only:
            shorts = valid
            if ash is not None:
                shorts = shorts[ash[t, shorts]]
            shorts = shorts[:ks]
            if len(shorts):
                W[t, shorts] -= 1.0 / len(shorts)
    return pd.DataFrame(W, index=signal.index, columns=signal.columns)


@dataclass
class BT:
    gross: pd.Series
    net: pd.Series
    turnover: pd.Series


def backtest(weights: pd.DataFrame, ret: pd.DataFrame, H: int = 1,
             cost_bps: float = 10.0) -> BT:
    """Weights formed at t earn returns at t+1. Overlapping JT portfolios for H>1."""
    w = weights.rolling(H, min_periods=1).mean() if H > 1 else weights
    fwd = ret.shift(-1).reindex(w.index).fillna(0.0)
    gross = (w * fwd).sum(axis=1).shift(1)  # label the return with the month it is earned
    turnover = w.diff().abs().sum(axis=1).shift(1)
    net = gross - turnover * cost_bps / 1e4
    active = w.abs().sum(axis=1).shift(1) > 0
    return BT(gross[active], net[active], turnover[active])


# ----------------------------------------------------------------- statistics


def perf(r: pd.Series, rf: pd.Series | None = None, periods: int = 12) -> dict:
    r = r.dropna()
    if len(r) < 12:
        return {}
    ex = r - (rf.reindex(r.index).fillna(0) if rf is not None else 0)
    mu, sd = ex.mean(), ex.std()
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    return {
        "start": r.index[0].strftime("%Y-%m"),
        "end": r.index[-1].strftime("%Y-%m"),
        "months": len(r),
        "cagr": eq.iloc[-1] ** (periods / len(r)) - 1,
        "ann_ret": r.mean() * periods,
        "ann_vol": r.std() * np.sqrt(periods),
        "sharpe": mu / sd * np.sqrt(periods) if sd > 0 else np.nan,
        "t_stat": mu / sd * np.sqrt(len(r)) if sd > 0 else np.nan,
        "max_dd": dd.min(),
        "worst_month": r.min(),
        "hit_rate": (r > 0).mean(),
        "skew": sps.skew(r),
    }


def deflated_sharpe(best: pd.Series, all_sr_monthly: np.ndarray) -> float:
    """Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio.

    Probability that the best of N tried strategies has a true Sharpe > 0
    after accounting for how many configurations were tested (selection bias)
    and for skew / fat tails. > 0.95 is the usual bar.
    """
    r = best.dropna()
    T = len(r)
    sr = r.mean() / r.std()
    N = len(all_sr_monthly)
    var_sr = np.nanvar(all_sr_monthly, ddof=1)
    g = 0.5772156649
    sr0 = np.sqrt(var_sr) * ((1 - g) * sps.norm.ppf(1 - 1 / N) + g * sps.norm.ppf(1 - 1 / (N * np.e)))
    sk, ku = sps.skew(r), sps.kurtosis(r, fisher=False)
    z = (sr - sr0) * np.sqrt(T - 1) / np.sqrt(1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    return float(sps.norm.cdf(z))


def walk_forward(candidates: dict, train: int = 120, min_train: int = 60,
                 every: int = 12, start: str | None = None) -> tuple[pd.Series, pd.Series]:
    """Honest window selection: every `every` months, pick the candidate with the
    best trailing Sharpe (over up to `train` months, using only past data), then
    trade it out-of-sample until the next re-selection.

    Returns (out-of-sample return series, chosen-candidate label series).
    """
    df = pd.DataFrame(candidates).sort_index()
    idx = df.index
    first = idx[0] + pd.DateOffset(months=min_train) if start is None else pd.Timestamp(start)
    out, pick = {}, {}
    choice = None
    for i, t in enumerate(idx):
        if t < first:
            continue
        if choice is None or (i % every == 0):
            hist = df.loc[:idx[i - 1]].iloc[-train:]
            sr = hist.mean() / hist.std()
            sr = sr[hist.count() >= min_train]
            if sr.notna().any():
                choice = sr.idxmax()
        if choice is not None:
            out[t] = df.at[t, choice]
            pick[t] = str(choice)
    return pd.Series(out), pd.Series(pick)
