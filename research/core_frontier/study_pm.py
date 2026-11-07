"""
PREDICTION-MODEL SLEEVE -- proxy test of the "15-min prediction market" framework.

The deck buys the "Up/Yes" side of 15-minute BTC up/down markets when price dips
to the lower Keltner Channel (KC 20, 2.0 x ATR) inside the 15-minute window, only
while the 1-hour trend is bullish (1H close above a rising 20-EMA, two hours in a
row), and only at a contract price of $0.30 or less. Polymarket's own price history
is not reachable from here, so this tests the SIGNAL on BTC itself:

  * the market is assumed to price "Up" fairly: p = P(close >= open) under a random
    walk with BTC's recent 5-minute volatility, given where price is and how long
    is left in the window;
  * an edge exists only if, after a signal, BTC finishes at or above the open MORE
    often than p says (the dip "snaps back");
  * trade P&L per contract = outcome (1 or 0) - p - 2% slippage on p (deck's cost).

Data: BTC 5-minute and 15-minute bars (last 60 days) and 1-hour bars (2 years), Yahoo.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

HERE = Path(__file__).parent
DATA, OUT = HERE / "data", HERE / "results"
OUT.mkdir(exist_ok=True)


def load(name):
    d = pd.read_csv(DATA / f"btc_{name}.csv", index_col=0, parse_dates=True)
    d.index = pd.to_datetime(d.index, utc=True)
    return d[["Open", "High", "Low", "Close"]].astype(float)


def main():
    b5, b15, h1 = load("5m"), load("15m"), load("1h")
    # Keltner channel on completed 15-minute bars, known at the next bar's open
    ema = b15.Close.ewm(span=20, adjust=False).mean()
    tr = pd.concat([b15.High - b15.Low, (b15.High - b15.Close.shift()).abs(), (b15.Low - b15.Close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=20, adjust=False).mean()
    lower = (ema - 2.0 * atr).shift(1)
    # 1-hour regime: close above a rising 20-EMA for two consecutive completed hours
    e1 = h1.Close.ewm(span=20, adjust=False).mean()
    bull = ((h1.Close > e1) & (e1.diff() > 0)).astype(int)
    bull2 = (bull.rolling(2).sum() == 2).shift(1)            # known at the start of the next hour
    # 5-minute volatility (per minute) from the previous 4 hours
    lr5 = np.log(b5.Close).diff()
    sig1m = (lr5.rolling(48, min_periods=24).std() / np.sqrt(5)).shift(1)

    rows = []
    for t0, bar in b15.iterrows():
        subs = b5.loc[t0:t0 + pd.Timedelta(minutes=10)]
        if len(subs) < 3 or pd.isna(lower.get(t0)) or pd.isna(sig1m.get(t0)):
            continue
        O, C = bar.Open, bar.Close
        win = float(C >= O)
        hour = t0.floor("h")
        htf = bool(bull2.get(hour, False)) if not pd.isna(bull2.get(hour, np.nan)) else False
        s = sig1m[t0]
        for k in range(2):                                     # signal must leave at least 5 minutes
            sb = subs.iloc[k]
            left = 15 - 5 * (k + 1)
            p = float(norm.cdf((sb.Close - O) / (O * s * np.sqrt(left)))) if s > 0 else np.nan
            touch = sb.Low <= lower[t0]
            rows.append({"t": t0, "k": k, "p": p, "win": win, "touch": bool(touch), "htf": htf, "below_open": sb.Close < O})
            if touch:
                break
    df = pd.DataFrame(rows).dropna(subset=["p"])
    df.to_csv(OUT / "PM_observations.csv", index=False)

    def score(x, label):
        x = x.drop_duplicates("t")                             # one trade per 15-minute market
        if not len(x):
            return {"label": label, "n": 0}
        pnl = x.win - x.p - 0.02 * x.p
        return {"label": label, "n": int(len(x)), "win_rate": float(x.win.mean()), "avg_price": float(x.p.mean()),
                "edge_per_contract": float(pnl.mean()), "edge_t": float(pnl.mean() / pnl.std() * np.sqrt(len(pnl))) if len(pnl) > 2 else np.nan,
                "return_per_dollar": float(pnl.sum() / x.p.sum())}

    res = [
        score(df[df.touch & df.htf & (df.p <= 0.30)], "KC dip + 1H filter + price <= $0.30 (deck rules)"),
        score(df[df.touch & (df.p <= 0.30)], "KC dip, no 1H filter, price <= $0.30"),
        score(df[df.touch & df.htf], "KC dip + 1H filter, any price"),
        score(df[df.below_open & (df.p <= 0.30) & ~df.touch], "Any dip to <= $0.30 without a KC touch (control)"),
    ]
    # calibration of the fair-price model across all observations
    cal = df.groupby(pd.cut(df.p, [0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0]), observed=True).agg(n=("win", "size"), avg_p=("p", "mean"), win=("win", "mean"))
    out = {"window": [str(b5.index[0]), str(b5.index[-1])], "results": res,
           "calibration": {str(k): v for k, v in cal.round(4).to_dict("index").items()},
           "n_windows": int(df.t.nunique())}
    (OUT / "PM_summary.json").write_text(json.dumps(out, indent=1, default=float))
    pd.set_option("display.width", 220, "display.float_format", "{:.3f}".format)
    print(pd.DataFrame(res).to_string(index=False))
    print(cal)
    print(out["window"], out["n_windows"])


if __name__ == "__main__":
    main()
