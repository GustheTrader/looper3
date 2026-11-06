"""
VIX SPIKE STRATEGIES -- daily, 2007-03 .. latest (needs VIX3M + VVIX).

Instruments (both modelled from free data, calibrated in calibrate.py)
    CM future  30-day constant-maturity VIX futures position (what VIXY holds);
               model bleed matched to the real VIXY ETF, 2011-2026
    VIX call   Black-76 on the modelled VIX future for that expiry, IV = VVIX
               with an upward smile; settles to spot VIX at expiry

Strategies (each is a SLEEVE, returns are per $ of sleeve capital or per $ of
premium budget, combined with the core book in study_allocation.py)
    V0 always_long      hold the CM future every day (the cost of permanent insurance)
    V1 backwardation    hold the CM future only while VIX >= VIX3M (term structure
                        inverted = stress is live), decided at the prior close
    V2 tail_calls       every month buy OTM VIX calls ~60 calendar days out, strike = m x future,
                        spend a fixed premium budget; sell the whole position as
                        soon as it is worth tp x what it cost ("monetise the spike"),
                        otherwise hold to expiry
    V3 spike_fade       SHORT the CM future after a spike has peaked: VIX >= 25,
                        term structure back in contango, VIX >= 15% off its 10-day
                        high; exit when VIX < 18, after 40 days, or if backwardation
                        returns (stop). Size 0.5x like SVXY.
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

import options_lib as ol

OUT = ol.HERE / "results"


def panel(m):
    d = pd.concat([m["vix"], m["vix3m"], m["vvix"], m["rf"]], axis=1, keys=["v", "v3", "vv", "rf"]).dropna()
    d = d.loc["2007-03-01":]
    d["F30"] = [ol.vix_future(a, b, c, 30) for a, b, c in zip(d.v, d.v3, d.vv)]
    d["F29"] = [ol.vix_future(a, b, c, 29) for a, b, c in zip(d.v, d.v3, d.vv)]
    d["cm_ret"] = (d.F29 / d.F30.shift(1) - 1).fillna(0)        # one day of a long CM VIX future
    return d


def v0_v1(d):
    always = d.cm_ret
    sig = (d.v >= d.v3).shift(1, fill_value=False)
    back = d.cm_ret.where(sig, 0.0)
    return always, back, sig


def v3_fade(d, size=0.5):
    on, days, pos = False, 0, []
    hi10 = d.v.rolling(10).max()
    for i in range(len(d)):
        pos.append(on)
        v, v3 = d.v.iloc[i], d.v3.iloc[i]
        if on:
            days += 1
            if v < 0.18 or days >= 40 or v >= v3:
                on = False
        elif v >= 0.25 and v < v3 and v <= 0.85 * hi10.iloc[i]:
            on, days = True, 0
    pos = pd.Series(pos, index=d.index).shift(1, fill_value=False)   # act next day
    return -size * d.cm_ret.where(pos, 0.0), pos


def v2_tail_calls(d, strike_mult=1.5, tp=4.0, tenor_days=60):
    """Returns monthly series of (premium spent, proceeds) per $1 of monthly budget."""
    cost = ol.COSTS["VIX"]
    months = d.groupby([d.index.year, d.index.month]).tail(1).index
    rec = []
    for t0 in months:
        if t0 >= d.index[-1] - pd.Timedelta(days=5):
            break
        row = d.loc[t0]
        F = ol.vix_future(row.v, row.v3, row.vv, tenor_days)
        K = strike_mult * F
        mid = ol.vix_call(F, K, tenor_days, row.rf, row.vv)
        ask = cost.buy(mid)
        n = 1.0 / ask                         # contracts (per 1 point) bought with $1 budget
        expiry = t0 + pd.Timedelta(days=tenor_days)              # calendar days
        path = d.loc[t0:expiry].iloc[1:]
        proceeds, exit_day, how = None, None, "expired"
        for t, r in path.iterrows():
            left = max((expiry - t).days, 1)
            Ft = ol.vix_future(r.v, r.v3, r.vv, left)
            val = cost.sell(ol.vix_call(Ft, K, left, r.rf, r.vv))
            if tp and n * val >= tp:
                proceeds, exit_day, how = n * val, t, "take_profit"
                break
        if proceeds is None:
            last = path.iloc[-1] if len(path) else row
            proceeds = n * max(last.v - K, 0.0)
            exit_day = path.index[-1] if len(path) else t0
        rec.append({"buy": t0, "exit": exit_day, "how": how, "K_over_vix": K / row.v, "proceeds": proceeds})
    return pd.DataFrame(rec)


def to_monthly(daily: pd.Series) -> pd.Series:
    return (1 + daily).groupby([daily.index.year, daily.index.month]).prod().pipe(
        lambda s: s.set_axis([pd.Timestamp(y, mo, 1) + pd.offsets.MonthEnd(0) for y, mo in s.index])) - 1


def main():
    m = ol.load_market()
    d = panel(m)
    always, back, sig = v0_v1(d)
    fade, fpos = v3_fade(d)
    res = {}
    for name, r in [("V0_always_long", always), ("V1_backwardation_long", back), ("V3_spike_fade_short", fade)]:
        res[name] = ol.perf(r, 252) | {"days_active": float((r != 0).mean())}
    # real-ETF cross-check of V1 with VIXY (2011+)
    vixy = m["vixy"].pct_change().reindex(d.index)
    v1_real = vixy.where(sig, 0.0).loc["2011-01-05":]
    res["V1_backwardation_long_REAL_VIXY_2011+"] = ol.perf(v1_real, 252)
    res["V0_always_long_REAL_VIXY_2011+"] = ol.perf(vixy.loc["2011-01-05":], 252)
    svxy = m["svxy"].pct_change().reindex(d.index)
    res["V3_spike_fade_REAL_SVXY_2011+"] = ol.perf(svxy.where(fpos, 0.0).loc["2011-10-05":], 252)

    # tail calls grid
    rows, tails = [], {}
    for sm, tp in itertools.product([1.3, 1.6, 2.0], [3.0, 5.0, None]):
        t = v2_tail_calls(d, sm, tp)
        mult = t.proceeds
        tails[f"K{sm}_tp{tp}"] = t
        rows.append({"strike_mult": sm, "take_profit": tp, "avg_payoff_multiple": mult.mean(),
                     "hit_rate": (mult > 1).mean(), "best_multiple": mult.max(),
                     "months": len(t), "n_take_profit": int((t.how == "take_profit").sum()),
                     "payoff_2008": t[t.buy.dt.year == 2008].proceeds.sum(),
                     "payoff_2020": t[t.buy.dt.year == 2020].proceeds.sum()})
    tg = pd.DataFrame(rows)
    print(tg.round(3).to_string(index=False))
    tg.to_csv(OUT / "V_tail_call_grid.csv", index=False)
    for k, t in tails.items():
        t.to_csv(OUT / f"V_tail_calls_{k}.csv", index=False)

    # monthly series for allocation
    mon = pd.DataFrame({"V0_always_long": to_monthly(always), "V1_backwardation_long": to_monthly(back),
                        "V3_spike_fade_short": to_monthly(fade)})
    mon.to_csv(OUT / "V_monthly.csv")
    pd.DataFrame({"V1_signal": sig, "V3_signal": fpos, "vix": d.v, "vix3m": d.v3}).to_csv(OUT / "V_signals_daily.csv")
    (OUT / "V_summary.json").write_text(json.dumps(res, indent=2, default=float))
    for k, v in res.items():
        print(f"{k:42s} cagr {v.get('cagr', np.nan):7.3f} vol {v.get('ann_vol', np.nan):.3f} "
              f"sharpe {v.get('sharpe', np.nan):6.2f} dd {v.get('max_dd', np.nan):.2f} active {v.get('days_active', np.nan):.2f}")


if __name__ == "__main__":
    main()
