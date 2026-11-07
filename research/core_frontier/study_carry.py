"""
FUNDING-CARRY SLEEVE -- market-neutral crypto cash-and-carry, built from REAL perp funding history.

Position: buy spot BTC (or ETH) and short the same notional of the Deribit perpetual. Price moves cancel;
the position collects the funding rate that longs pay shorts (and pays it when funding is negative).

Rules (the same ones tested in study_perps.py; nothing re-tuned here)
  * per coin, ON while the trailing 7-day average funding beats T-bills by more than 2%/yr,
    judged on data up to the previous US close (no look-ahead); OFF = the sleeve sits in T-bills
  * 50% BTC / 50% ETH
  * 0.2% of the coin's capital each time it switches on or off (spot + perp fees and slippage)
  * funding is credited per US trading day as the sum of hourly rates since the previous trading day,
    so weekend funding lands on Monday
  * no Deribit history before 2019-04-30: the sleeve is T-bills before 2019-05-15 (after a 7-day warm-up)

Not modelled: exchange failure (cap any one venue), the small perp-vs-index premium at entry/exit,
fees on the daily collateral top-ups. Writes results/CARRY_daily.csv.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "leaps_vix"))
sys.path.insert(0, str(HERE.parent / "momentum"))
import study_hard25 as h  # noqa: E402

OUT, DATA = HERE / "results", HERE / "data"
PRE_PREMIUM = 0.05      # only used by study_perps for the perp-cost stress before 2019
CARRY_COST = 0.002
CARRY_ON = 0.02
START = "2019-05-15"


def funding_on_days(days, rf):
    """Funding per US trading day: hourly rates summed over calendar days (previous trading day, this day]."""
    f = pd.read_csv(DATA / "funding_daily.csv", index_col=0, parse_dates=True)
    out = {}
    for c in ["deribit_btc", "deribit_eth", "hl_btc", "hl_eth"]:
        cum = f[c].dropna().cumsum()
        cal = cum.reindex(pd.date_range(cum.index[0], days[-1], freq="D")).ffill()
        d = cal.reindex(days, method="ffill").diff()
        out[c] = d.where(days >= cum.index[0] + pd.Timedelta(days=1))
    F = pd.DataFrame(out, index=days)
    for k in ["btc", "eth"]:
        F[f"perp_{k}"] = F[f"deribit_{k}"].fillna(rf + PRE_PREMIUM / 252)
        F[f"stress_{k}"] = F[[f"deribit_{k}", f"hl_{k}"]].max(axis=1).fillna(rf + 2 * PRE_PREMIUM / 252)
    return F


def carry_leg(fund, rf, venue_cost=CARRY_COST, on_thr=CARRY_ON):
    """One coin: returns (daily total return, on-flag)."""
    trail = fund.rolling(7).mean().shift(1) * 252 - rf.rolling(7).mean().shift(1) * 252
    on = (trail > on_thr).astype(float)
    r = rf + on * (fund.fillna(rf) - rf) - on.diff().abs().fillna(0) * venue_cost
    return r, on


def build():
    px, rf = h.load()
    F = funding_on_days(px.index, rf)
    legs, ons = {}, {}
    for k in ["btc", "eth"]:
        legs[k], ons[k] = carry_leg(F[f"deribit_{k}"], rf)
    carry = (0.5 * legs["btc"] + 0.5 * legs["eth"])
    on = 0.5 * ons["btc"] + 0.5 * ons["eth"]
    pre = carry.index < START
    carry[pre], on[pre] = rf[pre], 0.0
    return pd.DataFrame({"carry": carry, "carry_on": on, "on_btc": ons["btc"].where(~pre, 0.0), "on_eth": ons["eth"].where(~pre, 0.0),
                         "fund_btc": F.deribit_btc, "fund_eth": F.deribit_eth, "rf": rf})


def main():
    C = build()
    C.to_csv(OUT / "CARRY_daily.csv")
    x = C.loc[START:]
    yr = (1 + x.carry).groupby(x.index.year).prod() - 1
    tb = (1 + x.rf).groupby(x.index.year).prod() - 1
    print(pd.DataFrame({"carry": yr, "tbills": tb, "extra": yr - tb, "pct_on": x.carry_on.groupby(x.index.year).mean()}).round(4))
    eq = (1 + x.carry).cumprod()
    print("CAGR", round(eq.iloc[-1] ** (252 / len(x)) - 1, 4), "maxDD", round((eq / eq.cummax() - 1).min(), 4), "last", x.index[-1].date())


if __name__ == "__main__":
    main()
