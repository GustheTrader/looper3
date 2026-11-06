"""
LONG/SHORT MOMENTUM WITH A 50-DAY MOVING-AVERAGE TILT  (daily, 1996-10 .. latest)

Stock selection (each month end, S&P 500 members, 6-1 momentum = 6-month
return skipping the latest month, the stock window chosen in research/momentum):
    plain         long the 10 strongest, short the 10 weakest
    concentrated  long the 10 strongest inside the 3 strongest GICS sectors,
                  short the 10 weakest inside the 3 weakest sectors
                  (sectors rated by the 9-month Sector Power Strength Rating)

Daily tilt from SPY's own trend: when SPY closed above its 50-day simple moving
average yesterday, run the "up" leg weights, otherwise the "down" weights:
    neutral   100/100 both regimes            (plain dollar-neutral L/S)
    tilt130   130/70 above,  70/130 below
    tilt150   150/50 above,  50/150 below     (the requested design)
    flip      100/0 above,    0/100 below     (all long or all short)
Each tilt is also run with a 1% band (switch only when SPY is >1% beyond the
average) to cut whipsaw trades.

Costs: 10 bps per unit of stock turnover (monthly re-selection + regime
switches), 0.5%/yr borrow fee on the short leg, T-bill interest only on
unused cash (no interest credited on short-sale proceeds).
"""
from __future__ import annotations

import itertools
import json
import sys

import numpy as np
import pandas as pd

import options_lib as ol

sys.path.insert(0, str(ol.HERE.parent / "momentum"))
import momentum_lib as ml  # noqa: E402

OUT = ol.HERE / "results"
TILTS = {"neutral": ((1.0, 1.0), (1.0, 1.0)), "tilt130": ((1.3, 0.7), (0.7, 1.3)),
         "tilt150": ((1.5, 0.5), (0.5, 1.5)), "flip": ((1.0, 0.0), (0.0, 1.0))}
N = 10
COST_BPS, BORROW = 10, 0.005


def load():
    px = pd.read_csv(ml.DATA / "sp500_close_d.csv", index_col=0, parse_dates=True)
    R = px.pct_change(fill_method=None)
    R = R.mask((R > 1.0) | (R < -0.9))                       # free-data glitches
    spy = pd.read_csv(ol.DATA / "etf_raw_d.csv", index_col=0, parse_dates=True)["SPY"]
    rf = pd.read_csv(ml.DATA / "ff_factors_d.csv", index_col=0, parse_dates=True)["RF"]
    idx = R.index.intersection(spy.dropna().index)
    cons = pd.read_csv(ml.DATA / "sp500_constituents.csv")
    c = cons.assign(tk=cons.Symbol.str.replace(".", "-", regex=False)).set_index("tk")
    sector = c["GICS Sector"].reindex(px.columns)
    added = pd.to_datetime(c["Date added"], errors="coerce").reindex(px.columns)
    return R.loc[idx], px.loc[idx], spy.loc[idx], rf.reindex(idx).ffill().fillna(0), sector, added


def memberships(px, sector, added=None):
    """Daily 0/1 membership of the long and short baskets, for both selection modes."""
    me = px.groupby([px.index.year, px.index.month]).tail(1).index      # last trading day of each month
    m = px.loc[me]
    mret = m.pct_change(fill_method=None)
    mom = (m.shift(1) / m.shift(7) - 1)                                   # 6-1 momentum at each month end
    sect_ret = mret.T.groupby(sector).mean().T
    rating = ml.spsr(sect_ret, mret.mean(axis=1), 9, 0)
    out = {}
    for mode in ["plain", "concentrated"]:
        L = pd.DataFrame(0.0, index=me, columns=px.columns)
        S = L.copy()
        for d in me:
            s = mom.loc[d].dropna()
            if added is not None:
                # point-in-time universe: only stocks already in the S&P 500 on this date
                s = s[added.reindex(s.index) <= d]
            if len(s) < 60:
                continue
            if mode == "concentrated":
                r = rating.loc[d].dropna()
                if len(r) < 8:
                    continue
                top, bot = r.nlargest(3).index, r.nsmallest(3).index
                longs = s[sector[s.index].isin(top)].nlargest(N).index
                shorts = s[sector[s.index].isin(bot)].nsmallest(N).index
            else:
                longs, shorts = s.nlargest(N).index, s.nsmallest(N).index
            L.loc[d, longs] = 1.0 / N
            S.loc[d, shorts] = 1.0 / N
        # formed at the close of month-end d -> held from the next trading day
        out[mode] = (L.reindex(px.index).ffill().shift(1).fillna(0.0),
                     S.reindex(px.index).ffill().shift(1).fillna(0.0), L, S)
    return out


def regime(spy: pd.Series, band: float) -> pd.Series:
    """True = 'up' regime. Decided at the close, applied to the next day's return.
    With a band, the regime only flips once SPY is more than `band` beyond its 50-day average."""
    ma = spy.rolling(50).mean()
    state, out = True, []
    for p, a in zip(spy.values, ma.values):
        if not np.isnan(a):
            if p > a * (1 + band):
                state = True
            elif p < a * (1 - band):
                state = False
        out.append(state)
    return pd.Series(out, index=spy.index).shift(1, fill_value=True)


def run(R, rf, Ld, Sd, reg, tilt):
    (lu, su), (ldn, sdn) = TILTS[tilt]
    lw = pd.Series(np.where(reg, lu, ldn), index=R.index)
    sw = pd.Series(np.where(reg, su, sdn), index=R.index)
    W = Ld.mul(lw, axis=0) - Sd.mul(sw, axis=0)
    Rz = R.fillna(0.0)
    gross = (W * Rz).sum(axis=1)
    turnover = W.diff().abs().sum(axis=1).fillna(0.0)
    cash = (1 - lw).clip(lower=0)
    net = gross - turnover * COST_BPS / 1e4 - sw * BORROW / 252 + cash * rf
    return net, turnover, reg


def main():
    R, px, spy, rf, sector, added = load()
    mem = memberships(px, sector, added)
    mem_biased = memberships(px, sector, None)      # today's members at every date (look-ahead), for contrast
    regs = {0.0: regime(spy, 0.0), 0.01: regime(spy, 0.01)}
    start, end = "1996-10-01", "2026-10-31"
    rows, daily = [], {}
    spy_tr = pd.read_csv(ml.DATA / "etf_close_d.csv", index_col=0, parse_dates=True)["SPY"].pct_change().reindex(R.index)
    for mode, tilt, band in itertools.product(["plain", "concentrated"], TILTS, [0.0, 0.01]):
        if tilt == "neutral" and band:
            continue
        Ld, Sd, _, _ = mem[mode]
        net, to, reg = run(R, rf, Ld, Sd, regs[band], tilt)
        net = net.loc[start:end]
        key = f"{mode}_{tilt}" + ("_band1" if band else "")
        daily[key] = net
        p = ol.perf(net, 252, rf.loc[start:end])
        yr = (1 + net).groupby(net.index.year).prod() - 1
        switches = int(reg.loc[start:end].astype(int).diff().abs().sum())
        rows.append({"key": key, "mode": mode, "tilt": tilt, "band": band, **p,
                     "turnover_per_yr": float(to.loc[start:end].sum() / (len(net) / 252)),
                     "regime_switches_per_yr": switches / (len(net) / 252),
                     "pct_days_up": float(reg.loc[start:end].mean()),
                     "corr_spy": float(net.corr(spy_tr.loc[start:end])),
                     "ret_2000_02": float((1 + net.loc["2000":"2002"]).prod() - 1),
                     "ret_2008": yr.get(2008), "ret_2020": yr.get(2020), "ret_2022": yr.get(2022),
                     "sharpe_1st15y": ol.perf(net.loc[:"2011-09"], 252)["sharpe"],
                     "sharpe_2nd15y": ol.perf(net.loc["2011-10":], 252)["sharpe"]})
    # long-only top-10 momentum and SPY for reference
    for mode in ["plain", "concentrated"]:
        Ld, _, _, _ = mem[mode]
        lo = ((Ld * R.fillna(0)).sum(axis=1) - Ld.diff().abs().sum(axis=1) * COST_BPS / 1e4).loc[start:end]
        daily[f"{mode}_long_only"] = lo
        rows.append({"key": f"{mode}_long_only", "mode": mode, "tilt": "long_only", "band": 0, **ol.perf(lo, 252, rf.loc[start:end])})
    # how much of the tilt is just market timing? SPY +1 above its 50-day average, -1 below
    for band in [0.0, 0.01]:
        pos = regs[band].map({True: 1.0, False: -1.0})
        tr = pos.diff().abs().fillna(0)
        r = (pos * spy_tr.fillna(0) + (pos < 0) * 2 * rf - tr * 2 / 1e4).loc[start:end]
        k = "spy_ma50_long_short" + ("_band1" if band else "")
        daily[k] = r
        rows.append({"key": k, "mode": "-", "tilt": "spy_timing", "band": band, **ol.perf(r, 252, rf.loc[start:end]),
                     "ret_2008": float((1 + r.loc["2008"]).prod() - 1), "ret_2022": float((1 + r.loc["2022"]).prod() - 1),
                     "ret_2000_02": float((1 + r.loc["2000":"2002"]).prod() - 1)})
    # the look-ahead version, to show the size of the bias
    for tilt in ["neutral", "tilt150"]:
        Ld, Sd, _, _ = mem_biased["plain"]
        net = run(R, rf, Ld, Sd, regs[0.0], tilt)[0].loc[start:end]
        rows.append({"key": f"LOOKAHEAD_plain_{tilt}", "mode": "lookahead", "tilt": tilt, "band": 0.0, **ol.perf(net, 252, rf.loc[start:end])})
    lo = ((mem_biased["plain"][0] * R.fillna(0)).sum(axis=1)).loc[start:end]
    rows.append({"key": "LOOKAHEAD_plain_long_only", "mode": "lookahead", "tilt": "long_only", "band": 0.0, **ol.perf(lo, 252, rf.loc[start:end])})
    s = spy_tr.loc[start:end]
    daily["spy"] = s
    rows.append({"key": "spy", "mode": "-", "tilt": "spy", "band": 0, **ol.perf(s, 252, rf.loc[start:end])})
    g = pd.DataFrame(rows)
    g.to_csv(OUT / "LS_results.csv", index=False)
    dd = pd.DataFrame(daily)
    dd.to_csv(OUT / "LS_daily.csv")
    mon = (1 + dd).groupby([dd.index.year, dd.index.month]).prod() - 1
    mon.index = [pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0) for y, m in mon.index]
    mon.to_csv(OUT / "LS_monthly.csv")
    # current baskets for the live scan
    live = {}
    for mode in ["plain", "concentrated"]:
        _, _, L, S = mem[mode]
        d = L.index[-1]
        live[mode] = {"formed": str(d.date()), "long": sorted(L.columns[L.loc[d] > 0]), "short": sorted(S.columns[S.loc[d] > 0])}
    ma = spy.rolling(50).mean()
    live["spy_close"], live["spy_ma50"] = round(float(spy.iloc[-1]), 2), round(float(ma.iloc[-1]), 2)
    live["regime"] = "up (overweight longs)" if spy.iloc[-1] > ma.iloc[-1] else "down (overweight shorts)"
    live["as_of"] = str(spy.index[-1].date())
    (OUT / "LS_live.json").write_text(json.dumps(live, indent=1))
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(g[["key", "cagr", "ann_vol", "sharpe", "max_dd", "calmar", "corr_spy", "ret_2000_02", "ret_2008", "ret_2022",
             "turnover_per_yr", "regime_switches_per_yr", "sharpe_1st15y", "sharpe_2nd15y"]].to_string(index=False))
    print(json.dumps(live, indent=1))


# ---------------------------------------------------------------- survivorship-free test
def french_test():
    """Same tilt rules on Ken French's daily momentum deciles (every NYSE/AMEX/NASDAQ
    stock, dead ones included): long the top decile, short the bottom decile;
    regime from the whole-market index vs its moving average."""
    dec = pd.read_csv(ml.DATA / "mom10_d.csv", index_col=0, parse_dates=True)
    ff = pd.read_csv(ml.DATA / "ff_factors_d.csv", index_col=0, parse_dates=True)
    idx = dec.index.intersection(ff.index)
    hi, lo = dec.loc[idx, "Hi PRIOR"], dec.loc[idx, "Lo PRIOR"]
    mkt, rf = (ff["Mkt-RF"] + ff["RF"]).loc[idx], ff["RF"].loc[idx]
    level = (1 + mkt).cumprod()

    def reg_ma(n, band):
        ma = level.rolling(n).mean()
        state, out = True, []
        for p, a in zip(level.values, ma.values):
            if not np.isnan(a):
                state = True if p > a * (1 + band) else (False if p < a * (1 - band) else state)
            out.append(state)
        return pd.Series(out, index=idx).shift(1, fill_value=True)

    def sleeve(reg, tilt):
        (lu, su), (ldn, sdn) = TILTS[tilt]
        lw = pd.Series(np.where(reg, lu, ldn), index=idx)
        sw = pd.Series(np.where(reg, su, sdn), index=idx)
        switch = (lw.diff().abs() + sw.diff().abs()).fillna(0)
        # the deciles themselves re-form monthly: ~35% of each leg turns over per month at 10 bps
        rebal = (lw + sw) * 12 * 0.35 * COST_BPS / 1e4 / 252
        return (lw * hi - sw * lo - switch * COST_BPS / 1e4 - rebal - sw * BORROW / 252
                + (1 - lw).clip(lower=0) * rf)

    rows, keep = [], {}
    periods = {"1996-2026": ("1996-10-01", "2026-09-30"), "1927-1996": ("1927-07-01", "1996-09-30")}
    for (pname, (a, b)), tilt, band in itertools.product(periods.items(), TILTS, [0.0, 0.01]):
        if tilt == "neutral" and band:
            continue
        reg = reg_ma(50, band)
        r = sleeve(reg, tilt).loc[a:b]
        key = f"french_{tilt}" + ("_band1" if band else "")
        if pname == "1996-2026":
            keep[key] = r
        yr = (1 + r).groupby(r.index.year).prod() - 1
        rows.append({"period": pname, "key": key, **ol.perf(r, 252, rf.loc[a:b]),
                     "ret_2009": yr.get(2009), "ret_2008": yr.get(2008), "ret_1932": yr.get(1932),
                     "switches_per_yr": float(reg.loc[a:b].astype(int).diff().abs().sum() / (len(r) / 252))})
    for (pname, (a, b)) in periods.items():
        pos = reg_ma(50, 0.0).map({True: 1.0, False: -1.0})
        r = (pos * mkt + (pos < 0) * 2 * rf - pos.diff().abs().fillna(0) * 2 / 1e4).loc[a:b]
        rows.append({"period": pname, "key": "market_ma50_long_short", **ol.perf(r, 252, rf.loc[a:b])})
        rows.append({"period": pname, "key": "market_buy_hold", **ol.perf(mkt.loc[a:b], 252, rf.loc[a:b])})
    # moving-average length sensitivity, 150/50 tilt
    for (pname, (a, b)), n in itertools.product(periods.items(), [20, 50, 100, 200]):
        r = sleeve(reg_ma(n, 0.0), "tilt150").loc[a:b]
        rows.append({"period": pname, "key": f"french_tilt150_ma{n}", **ol.perf(r, 252, rf.loc[a:b])})
    g = pd.DataFrame(rows)
    g.to_csv(OUT / "LS_french_results.csv", index=False)
    pd.DataFrame(keep).to_csv(OUT / "LS_french_daily.csv")
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(g[["period", "key", "cagr", "ann_vol", "sharpe", "max_dd", "calmar", "ret_2008", "ret_2009", "switches_per_yr"]].to_string(index=False))
    return keep, rf


def add_to_mix(keep, rf_d):
    """Add the honest (French) MA-tilt sleeve to the recommended mixes, 2007-2026.
    The sleeve is funded from the mix's T-bill cash, so only its excess over cash is added."""
    a = pd.read_csv(OUT / "A_allocation_series.csv", index_col=0, parse_dates=True)
    core = pd.read_csv(OUT / "A_core_series.csv", index_col=0, parse_dates=True)
    mixes = {"Conservative": a["lam0.75_tail0.0_fade0.2_noboost"], "Balanced": a["lam1.0_tail0.0_fade0.2_boost"],
             "Aggressive": a["lam1.25_tail0.0_fade0.2_boost"]}
    rf_m = (1 + rf_d).groupby([rf_d.index.year, rf_d.index.month]).prod() - 1
    rf_m.index = [pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0) for y, m in rf_m.index]
    rows, series = [], {}
    for sl in ["french_neutral", "french_tilt150", "french_tilt150_band1", "french_flip_band1"]:
        d = keep[sl]
        m = (1 + d).groupby([d.index.year, d.index.month]).prod() - 1
        m.index = [pd.Timestamp(y, mo, 1) + pd.offsets.MonthEnd(0) for y, mo in m.index]
        for name, mix in mixes.items():
            for w in [0.0, 0.1, 0.2, 0.3]:
                if w == 0 and sl != "french_neutral":
                    continue
                idx = mix.dropna().index.intersection(m.index)
                r = mix.reindex(idx) + w * (m.reindex(idx) - rf_m.reindex(idx).fillna(0))
                yr = (1 + r).groupby(r.index.year).prod() - 1
                key = f"{name}+{sl if w else 'none'}@{w}"
                series[key] = r
                rows.append({"mix": name, "sleeve": sl if w else "none", "weight": w,
                             **ol.perf(r, 12, rf_m.reindex(idx)), "ret_2008": yr.get(2008), "ret_2020": yr.get(2020),
                             "ret_2022": yr.get(2022)})
    g = pd.DataFrame(rows)
    g.to_csv(OUT / "LS_mix_results.csv", index=False)
    pd.DataFrame(series).to_csv(OUT / "LS_mix_series.csv")
    print(g[["mix", "sleeve", "weight", "cagr", "ann_vol", "sharpe", "max_dd", "calmar", "ret_2008", "ret_2022"]].to_string(index=False))


if __name__ == "__main__":
    main()
    keep, rf_d = french_test()
    add_to_mix(keep, rf_d)
