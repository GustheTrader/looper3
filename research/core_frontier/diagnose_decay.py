"""
DIAGNOSTICS FOR THE DECAY SLEEVE -- is the put-spread backtest pointed the right way, and what drives its result?

  1  direction      correlation of each trade's return with the ETP's move over the trade (must be strongly negative
                    for a bearish put spread), and the average return by size of the move
  2  by-hand reprice  one trade repriced with independent Black-Scholes code from the raw price series
  3  shares short    the same deck signals traded by SHORTING THE SHARES (no options, no costs, 10%/yr borrow)
  4  always on       a put spread opened on every ETP every month, no signal, mid prices: is the decay capturable?
  5  exits           the deck's kill switch / 50% profit target vs holding to 30 days before expiry
  6  real costs      the model's bid/ask cost for today's real spreads vs the real quotes (live chains)
Writes results/DECAY_diagnostics.json
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
import study_decay as sd  # noqa: E402

OUT = HERE / "results"
BORROW = 0.10        # per year, assumed for hard-to-borrow ETPs (shares-short test only)


def main():
    px = sd.load()
    vol = pd.read_csv(HERE.parent / "leaps_vix" / "data" / "vol_index_d.csv", index_col=0, parse_dates=True)
    idx = vol.index[vol.index >= "2011-01-01"]
    px = px.reindex(idx)
    C = px["Close"]
    rf = pd.read_csv(HERE.parent / "momentum" / "data" / "rf_daily.csv", index_col=0, parse_dates=True)["RF"].reindex(idx).ffill()
    S_, K_, IV, _ = sd.signals(px, vol)
    IV = IV.reindex(idx)
    cal, _ = sd.calibration("decay_chain_calib.csv")
    out = {}

    # ---- 1 direction (deck as written, at mid)
    T = pd.read_csv(OUT / "DECAY_trades.csv", parse_dates=["entry", "exit"])
    x = T[T.key == "A0 deck as written, at mid (upper bound)"].copy()
    x["move"] = [C.at[b, k] / C.at[a, k] - 1 for a, b, k in zip(x.entry, x.exit, x.tk)]
    bins = pd.cut(x.move, [-1, -0.3, -0.1, 0, 0.1, 0.3, 5])
    out["direction"] = {"corr_trade_return_vs_etp_move": float(x[["ret", "move"]].corr().iloc[0, 1]), "n": int(len(x)),
                        "avg_etp_move_in_trade": float(x.move.mean()),
                        "by_move": {str(k): {"n": int(len(g)), "avg_return": float(g.ret.mean())} for k, g in x.groupby(bins, observed=True)}}

    # ---- 2 by-hand reprice of the first TECS trade
    from scipy.stats import norm
    tr = x[x.tk == "TECS"].iloc[0]
    r0 = float(rf.get(tr.entry, 0)) * 252
    s0 = float(IV.at[tr.entry, "TECS"]) * cal["TECS"]["k"]
    St0, St1 = float(C.at[tr.entry, "TECS"]), float(C.at[tr.exit, "TECS"])
    def bsput(S, K, T_, r, s):
        d1 = (np.log(S / K) + (r + s * s / 2) * T_) / (s * np.sqrt(T_)); d2 = d1 - s * np.sqrt(T_)
        return K * np.exp(-r * T_) * norm.cdf(-d2) - S * norm.cdf(-d1)
    sw = s0 * cal["TECS"]["s"]
    K2 = sd.strike_for_delta(St0, sd.TENOR / 365, r0, sw)
    d1_ = norm.ppf(1 - 0.10)
    K2_hand = St0 * np.exp(-(norm.ppf(0.90) * sw * np.sqrt(sd.TENOR / 365) - (r0 + sw * sw / 2) * sd.TENOR / 365))
    debit = bsput(St0, St0, sd.TENOR / 365, r0, s0) - bsput(St0, K2_hand, sd.TENOR / 365, r0, sw)
    T1 = (sd.TENOR - (tr.exit - tr.entry).days) / 365
    s1 = float(IV.at[tr.exit, "TECS"]) * cal["TECS"]["k"]
    val = bsput(St1, St0, T1, r0, s1) - bsput(St1, K2_hand, T1, r0, s1 * cal["TECS"]["s"])
    out["by_hand"] = {"trade": {"ticker": "TECS", "entry": str(tr.entry.date()), "exit": str(tr.exit.date())}, "short_strike_pct_of_spot": float(K2_hand / St0),
                      "strike_match": bool(abs(K2_hand - K2) / K2 < 1e-6), "return_by_hand": float(val / debit - 1), "return_in_backtest": float(tr.ret),
                      "etp_move": float(St1 / St0 - 1)}

    # ---- 3 shares short on the same signals
    rows = []
    for tk in S_.columns:
        s, k, c = S_[tk].fillna(False), K_[tk].fillna(False), C[tk]
        dates, pos = list(c.index), None
        for n, t in enumerate(dates[:-1]):
            if pos is None:
                if bool(s.get(t, False)):
                    pos = (n + 1, dates[n + 1])
            else:
                n0, t0 = pos
                if bool(k.get(t, False)) or n - n0 >= 60:
                    rows.append({"tk": tk, "tier": sd.TIER[tk], "entry": t0, "days": n - n0, "ret": -(c[t] / c[t0] - 1)})
                    pos = None
    Sh = pd.DataFrame(rows)
    Sh["net"] = Sh.ret - BORROW * Sh.days / 252
    out["shares_short"] = {"trades": int(len(Sh)), "mean": float(Sh.ret.mean()), "mean_after_borrow": float(Sh.net.mean()),
                           "win": float((Sh.net > 0).mean()), "avg_days": float(Sh.days.mean()),
                           "mean_2011_20": float(Sh[Sh.entry.dt.year <= 2020].net.mean()), "mean_2021_26": float(Sh[Sh.entry.dt.year >= 2021].net.mean()),
                           "worst": float(Sh.net.min()), "borrow_assumed_per_year": BORROW}

    # ---- 4 always-on put spread at mid
    def trade(tk, t0, k, tenor=90, exit_dte=21):
        c = C[tk].dropna(); i0 = c.index.get_loc(t0); j = i0 + tenor - exit_dte
        if j >= len(c):
            return None
        t1 = c.index[j]; r = float(rf.get(t0, 0)) * 252
        a, b = IV.at[t0, tk] * k, IV.at[t1, tk] * k
        if not (a == a and b == b):
            return None
        S0, S1 = c[t0], c[t1]; K2 = sd.strike_for_delta(S0, tenor / 365, r, a * 0.9)
        d0 = sd.put(S0, S0, tenor / 365, r, a) - sd.put(S0, K2, tenor / 365, r, a * 0.9)
        d1 = sd.put(S1, S0, exit_dte / 365, r, b) - sd.put(S1, K2, exit_dte / 365, r, b * 0.9)
        return d1 / d0 - 1, S1 / S0 - 1
    ao = {}
    for lab, kk in [("k=1.0", lambda t: 1.0), ("k=0.8", lambda t: 0.8), ("k=measured", lambda t: cal[t]["k"])]:
        res = []
        for tk in sd.TIER:
            c = C[tk].dropna()
            if len(c) < 400:
                continue
            for t0 in c.index[c.index >= "2012-01-01"][::21]:
                z = trade(tk, t0, kk(tk))
                if z:
                    res.append({"yr": t0.year, "ret": z[0], "move": z[1]})
        D = pd.DataFrame(res)
        ao[lab] = {"n": int(len(D)), "mean": float(D.ret.mean()), "median": float(D.ret.median()), "win": float((D.ret > 0).mean()),
                   "mean_2012_20": float(D[D.yr <= 2020].ret.mean()), "mean_2021_26": float(D[D.yr >= 2021].ret.mean()), "avg_etp_move": float(D.move.mean())}
    out["always_on_mid"] = ao

    # ---- 5 exits (read from the saved runs)
    S = json.loads((OUT / "DECAY_summary.json").read_text())["results"]
    out["exits"] = {k: {"avg_trade": v["avg_trade"], "win": v["win"], "test_cagr": v["test"]["cagr"], "design_cagr": v["design"]["cagr"]}
                    for k, v in S.items() if k[0] in "AEFG"}

    # ---- 6 real costs
    rows, _ = [], None
    cal90, _ = sd.calibration("decay_chain_calib_75d.csv")
    for tk in ["TECS", "SOXS", "SQQQ", "UVIX"]:
        for days, c_ in [(213, cal), (90, cal90)]:
            try:
                c = lc.chain(tk, days); q = c["quotes"]; Sx = c["spot"]
                atm = q.iloc[(q.strike - Sx).abs().argsort()[:1]].iloc[0]
                wing = q.iloc[(q.delta + 0.10).abs().argsort()[:1]].iloc[0]
                if abs(wing.delta + 0.10) > 0.06 or wing.strike >= atm.strike or atm.mid - wing.mid <= 0:
                    continue
                mid = atm.mid - wing.mid
                real = (atm.ask - wing.bid) - mid
                model = sd.spread_cost(Sx, atm.strike, wing.strike, c["days"] / 365, 0.04, atm.iv, wing.iv, c_[tk])
                rows.append({"tk": tk, "days": int(c["days"]), "debit_mid": float(mid), "real_over_mid": float(real), "real_pct": float(real / mid),
                             "model_pct": float(model / mid), "atm_oi": float(atm.openInterest), "wing_oi": float(wing.openInterest)})
            except Exception:  # noqa: BLE001
                pass
    R = pd.DataFrame(rows)
    out["real_costs"] = {"rows": rows, "median_real_pct": float(R.real_pct.median()), "median_model_pct": float(R.model_pct.median()),
                         "ratio_model_to_real": float(R.model_pct.median() / R.real_pct.median()), "cost_scale_applied": sd.COST_SCALE,
                         "note": "one side, % of the spread's mid price, strikes: ATM long / 10-delta short, last-close quotes"}
    (OUT / "DECAY_diagnostics.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: v for k, v in out.items() if k not in ("real_costs", "exits")}, indent=1, default=float))
    print(R.round(3).to_string(index=False), "\nmedian model/real", round(out["real_costs"]["ratio_model_to_real"], 2))


if __name__ == "__main__":
    main()
