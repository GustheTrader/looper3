"""
FF (FORWARD FACTOR) CALENDAR SLEEVE -- daily backtest, 2007-03 .. latest.

Forward Factor (from the "FF, Contango & Backwardation" deck):
    FF = (front IV - forward IV) / forward IV
    forward IV between T1 and T2 = sqrt((IV2^2 T2 - IV1^2 T1) / (T2 - T1))
Here front IV = 30-day (VIX), back IV = 93-day (VIX3M), both scaled to SPY
at-the-money volatility with the calibrated factor from research/leaps_vix.

Trade (deck rules)
    enter  an at-the-money SPY call calendar (short 30-day, long 90-day) when FF > 0.20,
           at most one new position per week
    size   debit = 4% / 6% / 8% / 10% of sleeve equity for VIX < 20 / 20-25 / 25-30 / > 30
    exit   when FF < 0.15 (normalised) or 7 days before the front option expires
    limits at most 80% of equity in open debits (20% cash reserve)
    costs  half bid/ask per option leg: 2% (VIX < 30), 5% (30-40), 10% (> 40, panic spreads)
Variants: the gated rule above; the same with a flat 4% size; and "always on"
(a 4% calendar every week regardless of FF) to show what contango does to calendars.
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

OUT = HERE / "results"
OUT.mkdir(exist_ok=True)
A = ol.PARAMS["A_VIX"]


def atm_iv(vix, vix3m, days):
    """ATM SPY implied vol for a maturity in days, from the VIX (30d) / VIX3M (93d) variance curve."""
    w30, w93 = vix ** 2 * 30, vix3m ** 2 * 93
    if days <= 30:
        w = vix ** 2 * days
    elif days <= 93:
        w = w30 + (w93 - w30) * (days - 30) / 63
    else:
        w = w93 + vix3m ** 2 * (days - 93)
    return A * np.sqrt(max(w, 1e-8) / max(days, 1e-6))


def forward_factor(vix, vix3m):
    iv1, iv2 = A * vix, A * vix3m
    fv = (iv2 ** 2 * 93 - iv1 ** 2 * 30) / 63
    fwd = np.sqrt(np.maximum(fv, 1e-6))
    return (iv1 - fwd) / fwd


def half_spread(vix):
    return 0.02 if vix < 0.30 else (0.05 if vix < 0.40 else 0.10)


def size_for(vix):
    return 0.04 if vix < 0.20 else 0.06 if vix < 0.25 else 0.08 if vix < 0.30 else 0.10


def run(m, mode="gated", flat_spread=False, ff_exit=True):
    S, q, rf = m["raw"]["SPY"], m["q"]["SPY"], m["rf"]
    vix, v3 = m["vix"], m["vix3m"]
    days = S.dropna().index.intersection(v3.dropna().index)
    days = days[days >= "2007-03-01"]
    ff = forward_factor(vix.reindex(days), v3.reindex(days))
    eq, cash = 1.0, 1.0
    pos, trades, curve = [], [], {}
    last_entry = None
    for t in days:
        St, r, qq, vx, vv = S[t], rf[t], q[t], vix[t], v3[t]
        # mark and exit
        keep = []
        for p in pos:
            t1 = (p["e1"] - t).days
            t2 = (p["e2"] - t).days
            c1 = ol.bs_price(St, p["K"], max(t1, 0) / 365, r, qq, atm_iv(vx, vv, max(t1, 1)))
            c2 = ol.bs_price(St, p["K"], t2 / 365, r, qq, atm_iv(vx, vv, t2))
            p["mark"] = p["n"] * (c2 - c1)
            exit_ff = ff_exit and mode != "always" and ff[t] < 0.15
            if t1 <= 7 or exit_ff:
                h = 0.02 if flat_spread else half_spread(vx)
                proceeds = p["n"] * (c2 * (1 - h) - c1 * (1 + h))
                cash += proceeds
                trades.append({"entry": str(p["t0"].date()), "exit": str(t.date()), "ff_entry": p["ff"], "vix_entry": p["vix"],
                               "debit": p["debit"], "proceeds": proceeds, "ret": proceeds / p["debit"] - 1,
                               "days": (t - p["t0"]).days, "why": "ff_normalised" if exit_ff and t1 > 7 else "front_expiry"})
            else:
                keep.append(p)
        pos = keep
        eq = cash + sum(p["mark"] for p in pos)
        # entries: at most one per week
        week_ok = last_entry is None or (t - last_entry).days >= 7
        want = (mode == "always") or (ff[t] > 0.20)
        open_debit = sum(p["debit"] for p in pos)
        if want and week_ok and open_debit < 0.8 * eq:
            frac = 0.04 if mode in ("always", "gated_flat") else size_for(vx)
            K = St
            c1 = ol.bs_price(St, K, 30 / 365, r, qq, atm_iv(vx, vv, 30))
            c2 = ol.bs_price(St, K, 90 / 365, r, qq, atm_iv(vx, vv, 90))
            h = 0.02 if flat_spread else half_spread(vx)
            debit_unit = c2 * (1 + h) - c1 * (1 - h)
            if debit_unit > 0:
                budget = min(frac * eq, 0.8 * eq - open_debit)
                n = budget / debit_unit
                cash -= budget
                pos.append({"t0": t, "e1": t + pd.Timedelta(days=30), "e2": t + pd.Timedelta(days=90), "K": K, "n": n,
                            "debit": budget, "ff": float(ff[t]), "vix": float(vx), "mark": n * (c2 - c1)})
                last_entry = t
        # cash earns T-bills
        cash *= np.exp(r / 252)
        eq = cash + sum(p["mark"] for p in pos)
        curve[t] = eq
    eqs = pd.Series(curve)
    return eqs.pct_change().fillna(0), pd.DataFrame(trades), ff


def main():
    m = ol.load_market()
    out, rets = {}, {}
    for mode in ["gated", "gated_flat", "always"]:
        r, tr, ff = run(m, mode)
        rets[mode] = r
        rf = m["rf"].reindex(r.index) / 252
        p = ol.perf(r, 252, rf)
        yr = (1 + r).groupby(r.index.year).prod() - 1
        out[mode] = {**{k: p[k] for k in ["cagr", "ann_vol", "sharpe", "max_dd"]}, "trades": len(tr),
                     "win_rate": float((tr.ret > 0).mean()) if len(tr) else None,
                     "avg_trade": float(tr.ret.mean()) if len(tr) else None,
                     "pct_time_invested": float((r != rf).mean()),
                     "yearly": {int(k): float(v) for k, v in yr.items()},
                     "ret_2020_03": float((1 + r.loc["2020-03"]).prod() - 1),
                     "ret_2008_q4": float((1 + r.loc["2008-10":"2008-12"]).prod() - 1)}
        if mode == "gated":
            tr.to_csv(OUT / "FF_trades.csv", index=False)
            ffs = ff
            # win rate by FF bucket
            b = pd.cut(tr.ff_entry, [0.2, 0.3, 0.4, 0.6, 3.0])
            out["by_ff_bucket"] = {str(k): {"n": int(len(g)), "win": float((g.ret > 0).mean()), "avg": float(g.ret.mean())}
                                   for k, g in tr.groupby(b, observed=True)}
        if mode == "always":
            b = pd.cut(tr.ff_entry, [-1, 0, 0.2, 3.0])
            out["always_by_ff"] = {str(k): {"n": int(len(g)), "win": float((g.ret > 0).mean()), "avg": float(g.ret.mean())}
                                   for k, g in tr.groupby(b, observed=True)}
    # sensitivity: are the losses from my cost / exit assumptions?
    sens = {}
    for lab, kw in [("flat 2% spreads", dict(flat_spread=True)), ("hold to front expiry", dict(ff_exit=False)),
                    ("flat spreads + hold to expiry", dict(flat_spread=True, ff_exit=False))]:
        r, tr, _ = run(m, "gated", **kw)
        p = ol.perf(r, 252, m["rf"].reindex(r.index) / 252)
        sens[lab] = {"cagr": p["cagr"], "sharpe": p["sharpe"], "max_dd": p["max_dd"], "win_rate": float((tr.ret > 0).mean()),
                     "avg_trade": float(tr.ret.mean()), "ret_2020_03": float((1 + r.loc["2020-03"]).prod() - 1)}
    out["sensitivity"] = sens
    # what moves a calendar: front vs back IV changes from entry to exit on the gated trades
    out["ff_share_above_020"] = float((ffs > 0.20).mean())
    out["ff_share_positive"] = float((ffs > 0).mean())
    out["ff_max"] = float(ffs.max())
    out["ff_max_date"] = str(ffs.idxmax().date())
    pd.DataFrame(rets).to_csv(OUT / "FF_daily_returns.csv")
    ffs.rename("FF").to_csv(OUT / "FF_series.csv")
    (OUT / "FF_summary.json").write_text(json.dumps(out, indent=1, default=float))
    for k, v in out.items():
        if isinstance(v, dict) and "cagr" in v:
            print(k, {x: (round(v[x], 3) if isinstance(v[x], float) else v[x]) for x in
                      ["cagr", "ann_vol", "sharpe", "max_dd", "trades", "win_rate", "avg_trade", "ret_2020_03", "ret_2008_q4"]})
    print("by FF bucket", out["by_ff_bucket"]); print("always by FF", out["always_by_ff"])
    print("FF>0.20 share", out["ff_share_above_020"], "FF>0", out["ff_share_positive"], "max", out["ff_max"], out["ff_max_date"])
    print("sensitivity", json.dumps(out["sensitivity"], indent=0, default=lambda x: round(float(x), 3)))
    print("gated yearly", {k: round(v, 3) for k, v in out["gated"]["yearly"].items()})


if __name__ == "__main__":
    main()
