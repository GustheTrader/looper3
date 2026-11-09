"""
EXECUTION DESK -- today's signals turned into exact order tickets from real option chains (last-close quotes).

  FF calendars   real Forward Factor from the live chains (ATM IV of the ~30d and ~90d expiries, our own BS
                 inversion of the mid). FF > 0.20 -> ticket: sell the front ATM call, buy the back ATM call,
                 same strike, as ONE combo order. Size by the deck's VIX bands (4/6/8/10% of the FF sleeve).
  VIX fade       entry state (VIX >= 25, curve back in contango, VIX >= 15% off its 10-day high)
  Funding carry  per coin: trailing 7-day funding vs T-bills + 2%/yr
  Decay scanner  the decay deck's signals today (tested and NOT funded -- shown for paper tracking only)

Master execution (applies to every option ticket)
  1. one combo order for the whole spread -- never leg in
  2. start at mid; improve by 10% of the bid/ask width every 5 minutes; never pay more than mid + 30% of the width
  3. trade 10:00-15:30 New York time (avoid the open and the closing auction, when spreads are widest)
  4. no fill by 15:30 -> cancel, re-quote tomorrow if the signal still holds
  5. place the exit at once as a GTC limit (take-profit) and keep the kill-switch rule on the screen
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "leaps_vix"))
import live_chains as lc  # noqa: E402

OUT = HERE / "results"
PORTFOLIO = 100_000          # tickets are written per $100k of portfolio; scale linearly


def call_chain(tk, days):
    c = lc.chain(tk, days, kind="call")
    q = c["quotes"]
    atm = q.iloc[(q.strike - c["spot"]).abs().argsort()[:1]].iloc[0]
    return c, atm


def ff_tickets(vix, ff_weight):
    out = []
    size = 0.04 if vix < 0.20 else 0.06 if vix < 0.25 else 0.08 if vix < 0.30 else 0.10
    for tk in ["SPY", "QQQ", "IWM", "DIA"]:
        try:
            f, _ = call_chain(tk, 30)
            b, _ = call_chain(tk, 90)
            qf, qb = f["quotes"].set_index("strike"), b["quotes"].set_index("strike")
            common = qf.index.intersection(qb.index)                                       # a calendar needs ONE strike in both expiries
            K = common[np.argmin(np.abs(common - f["spot"]))]
            fa, ba = qf.loc[K].copy(), qb.loc[K].copy()
            fa["strike"] = ba["strike"] = K
            t1, t2 = f["days"], b["days"]
            ff = (fa.iv - np.sqrt(max((ba.iv ** 2 * t2 - fa.iv ** 2 * t1) / (t2 - t1), 1e-6))) / np.sqrt(max((ba.iv ** 2 * t2 - fa.iv ** 2 * t1) / (t2 - t1), 1e-6))
            mid = ba.mid - fa.mid
            natural = ba.ask - fa.bid
            width = natural - (ba.bid - fa.ask)
            limit = mid + 0.30 * (natural - mid)
            budget = size * ff_weight * PORTFOLIO
            n = int(budget // (limit * 100)) if limit > 0 else 0
            out.append({"ticker": tk, "spot": round(f["spot"], 2), "ff": round(float(ff), 3), "signal": bool(ff > 0.20),
                        "front": f["expiry"], "back": b["expiry"], "strike": float(fa.strike),
                        "front_iv": round(float(fa.iv), 4), "back_iv": round(float(ba.iv), 4),
                        "sell": f"{tk} {f['expiry']} {fa.strike:g} C  bid {fa.bid:.2f} / ask {fa.ask:.2f}",
                        "buy": f"{tk} {b['expiry']} {ba.strike:g} C  bid {ba.bid:.2f} / ask {ba.ask:.2f}",
                        "mid": round(float(mid), 2), "natural": round(float(natural), 2), "combo_width": round(float(width), 2),
                        "limit_max": round(float(limit), 2), "contracts_per_100k": n,
                        "min_portfolio_for_1": round(float(limit * 100 / (size * ff_weight)), -3) if ff_weight > 0 else None, "debit_per_100k": round(n * limit * 100, 0),
                        "size_rule": f"{size:.0%} of the FF sleeve (VIX {vix * 100:.1f})",
                        "exit": "close when FF < 0.15, or 7 days before the front expiry; GTC limit at 2x the debit"})
        except Exception as e:  # noqa: BLE001
            out.append({"ticker": tk, "error": str(e)[:80]})
    return out


def main():
    vol = pd.read_csv(HERE.parent / "leaps_vix" / "data" / "vol_index_d.csv", index_col=0, parse_dates=True)
    v, v3 = vol["^VIX"].dropna() / 100, vol["^VIX3M"].dropna() / 100
    vix, vix3m, hi10 = float(v.iloc[-1]), float(v3.iloc[-1]), float(v.tail(10).max())
    W = json.loads((OUT / "CF_summary.json").read_text())["weights"]
    desk = {"as_of": str(v.index[-1].date()), "quotes": "last close (Friday quotes on a weekend)", "weights": W}
    desk["ff"] = ff_tickets(vix, W.get("ff_calendars", 0.0))
    desk["vix_fade"] = {"vix": vix, "vix3m": vix3m, "vix_10d_high": hi10,
                        "conditions": {"VIX >= 25": vix >= 0.25, "contango (VIX < VIX3M)": vix < vix3m, "VIX <= 85% of 10-day high": vix <= 0.85 * hi10},
                        "entry_today": bool(vix >= 0.25 and vix < vix3m and vix <= 0.85 * hi10)}
    C = pd.read_csv(OUT / "CARRY_daily.csv", index_col=0, parse_dates=True)
    rf = C.rf
    carry = {}
    for k in ["btc", "eth"]:
        fd = C[f"fund_{k}"].dropna()
        trail = float(fd.tail(7).mean() * 252 - rf.tail(7).mean() * 252)
        carry[k.upper()] = {"trailing_7d_funding_minus_tbills": trail, "on_tomorrow": trail > 0.02}
    desk["carry"] = carry
    import study_decay as sd
    px = sd.load()
    S_, K_, _, _ = sd.signals(px, vol)
    last = S_.index[-1]
    desk["decay_paper"] = {"date": str(last.date()), "signals": [tk for tk in S_.columns if bool(S_.at[last, tk])]}
    ok = [r for r in desk["ff"] if "error" not in r]
    f = OUT / "DESK_today.json"
    if not ok and f.exists():                      # market closed / pre-open: Yahoo returns no bid/ask. Keep the last good tickets, flagged.
        old = json.loads(f.read_text())
        if any("error" not in r for r in old.get("ff", [])):
            old["stale_note"] = (f"No live quotes at {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} UTC (market closed or not yet open). "
                                 f"Showing the last good tickets, from the {old['as_of']} close. Re-run desk.py after 10:00 New York time.")
            f.write_text(json.dumps(old, indent=1, default=float))
            print(old["stale_note"])
            return
    f.write_text(json.dumps(desk, indent=1, default=float))
    print(json.dumps(desk, indent=1, default=float))


if __name__ == "__main__":
    main()
