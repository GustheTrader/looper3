"""
GROWTH LEADERSHIP -- concentrate in the leading parts of the market instead of
the whole market: QQQ, mid caps, small caps, the leading sectors, and the
mega-cap / AI leadership group; keep the 50-day moving-average tilt.

Part 1  Rule-based leadership rotation (no hindsight), daily, 2002-08 .. latest
        Menu: QQQ, MDY (mid), IJR (small), SMH (semis), IGV (software) + the SPDR
        sectors. Each month rank by Sector Power Strength Rating (6/9/12-month
        blend, skip 1); hold the top 3 (keep a holding while it ranks top 5).
        Variants with SPY's 50-day average:
          lead_long         100% in the top 3 always
          lead_long_ma      100% above the average, 50% below (rest in T-bills)
          lead_ls_neutral   long top 3 / short bottom 3, 100/100
          lead_ls_150       150/50 above, 50/150 below (+ 1% band version)
        Growth mix (fixed buckets): 40% QQQ, 15% MDY, 10% IJR, 35% leadership rotation
          growth_mix        always invested
          growth_mix_ma     100% above, 50% below the 50-day average
          growth_mix_ls     above: 100% long; below: 50% long + 50% short the bottom-3 menu ETFs
Part 2  Mag 7 + AI leaders basket, 2023-01 .. latest (the group was only
        identifiable from about then; any earlier test would pick winners with hindsight)
Part 3  LEAPS + monthly calls on QQQ instead of SPY (2000 .. latest)
Part 4  Mixes, 2007-04 .. 2026-09: the Balanced mix with QQQ in the core and/or a
        growth-leadership sleeve.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

import options_lib as ol
from study_leaps import Cfg, month_ends, simulate

sys.path.insert(0, str(ol.HERE.parent / "momentum"))
import momentum_lib as ml  # noqa: E402

OUT = ol.HERE / "results"
MENU = ["QQQ", "MDY", "IJR", "SMH", "IGV", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLRE", "XLC"]
MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]
AI = ["NVDA", "AVGO", "AMD", "TSM", "ORCL", "PLTR", "ANET", "MU", "SMCI", "ARM", "VRT", "CRWD", "NOW", "DELL"]
COST = 5e-4      # ETF trades, one-way
BORROW = 0.005


def load():
    g = pd.read_csv(ol.DATA / "growth_etf_adj_d.csv", index_col=0, parse_dates=True)
    e = pd.read_csv(ol.DATA / "etf_adj_d.csv", index_col=0, parse_dates=True)
    px = pd.concat([g, e.drop(columns=["SPY"])], axis=1).sort_index()
    raw_spy = pd.read_csv(ol.DATA / "etf_raw_d.csv", index_col=0, parse_dates=True)["SPY"]
    ff = pd.read_csv(ml.DATA / "ff_factors_d.csv", index_col=0, parse_dates=True)["RF"]
    idx = px.index[px.index >= "1999-01-01"]
    rf = ff.reindex(idx).ffill()
    return px.loc[idx], raw_spy.reindex(idx).ffill(), rf


def regime(price: pd.Series, band=0.0, n=50) -> pd.Series:
    ma = price.rolling(n).mean()
    state, out = True, []
    for p, a in zip(price.values, ma.values):
        if not np.isnan(a):
            state = True if p > a * (1 + band) else (False if p < a * (1 - band) else state)
        out.append(state)
    return pd.Series(out, index=price.index).shift(1, fill_value=True)   # act on the next day


def rotation_weights(px, k=3, buffer=5):
    """Daily weights (1/k each) for the top-k and bottom-k menu ETFs by blended SPSR."""
    mpx = px[MENU].resample("ME").last()
    mret = mpx.pct_change(fill_method=None)
    mkt = px["SPY"].resample("ME").last().pct_change()
    score = sum(ml.spsr(mret, mkt, L, 1) for L in (6, 9, 12)) / 3
    rank = score.rank(axis=1, ascending=False)
    me = px.groupby([px.index.year, px.index.month]).tail(1).index
    top = pd.DataFrame(0.0, index=me, columns=MENU)
    bot = top.copy()
    held = []
    for d in me:
        r = rank.loc[d + pd.offsets.MonthEnd(0)].dropna() if (d + pd.offsets.MonthEnd(0)) in rank.index else pd.Series(dtype=float)
        if len(r) < 8:
            continue
        held = [h for h in held if r.get(h, 99) <= buffer]
        for c in r.sort_values().index:
            if len(held) >= k:
                break
            if c not in held:
                held.append(c)
        top.loc[d, held] = 1.0 / k
        bot.loc[d, r.sort_values().index[-k:]] = 1.0 / k
    daily = lambda w: w.reindex(px.index).ffill().shift(1).fillna(0.0)
    return daily(top), daily(bot), top


def book(R, rf, long_w, short_w, lw, sw):
    """Daily return of a long/short book: long_w/short_w are basket weights (rows sum to 1),
    lw/sw the leg sizes (Series), costs on every weight change, borrow on shorts,
    T-bill interest on uninvested cash."""
    W = long_w.mul(lw, axis=0) - short_w.mul(sw, axis=0)
    gross = (W * R.reindex(columns=W.columns).fillna(0)).sum(axis=1)
    to = W.diff().abs().sum(axis=1).fillna(0)
    return gross - to * COST - sw * BORROW / 252 + (1 - lw).clip(lower=0) * rf


def part1(px, spy_raw, rf):
    R = px.pct_change(fill_method=None)
    top, bot, top_m = rotation_weights(px)
    res, series = [], {}
    regs = {"": regime(spy_raw), "_band1": regime(spy_raw, 0.01), "_qqqMA": regime(px["QQQ"])}
    one = pd.Series(1.0, index=px.index)
    up0 = regs[""]
    variants = {
        "lead_long": (top, bot, one, 0 * one),
        "lead_long_ma": (top, bot, up0.map({True: 1.0, False: 0.5}), 0 * one),
        "lead_long_ma_qqq": (top, bot, regs["_qqqMA"].map({True: 1.0, False: 0.5}), 0 * one),
        "lead_ls_neutral": (top, bot, one, one),
        "lead_ls_150": (top, bot, up0.map({True: 1.5, False: 0.5}), up0.map({True: 0.5, False: 1.5})),
        "lead_ls_150_band1": (top, bot, regs["_band1"].map({True: 1.5, False: 0.5}), regs["_band1"].map({True: 0.5, False: 1.5})),
    }
    for k, (lwts, swts, lw, sw) in variants.items():
        series[k] = book(R, rf, lwts, swts, lw, sw)
    # growth mix buckets
    fixed = pd.DataFrame(0.0, index=px.index, columns=MENU)
    fixed["QQQ"], fixed["MDY"], fixed["IJR"] = 0.40, 0.15, 0.10
    mixw = fixed + 0.35 * top
    series["growth_mix"] = book(R, rf, mixw, bot, one, 0 * one)
    series["growth_mix_ma"] = book(R, rf, mixw, bot, up0.map({True: 1.0, False: 0.5}), 0 * one)
    series["growth_mix_ls"] = book(R, rf, mixw, bot, up0.map({True: 1.0, False: 0.5}), up0.map({True: 0.0, False: 0.5}))
    for b in ["SPY", "QQQ", "MDY", "IJR", "SMH", "IGV"]:
        series[b] = R[b]
    start, end = "2002-08-01", "2026-10-31"
    for k, r in series.items():
        r = r.loc[start:end]
        yr = (1 + r).groupby(r.index.year).prod() - 1
        res.append({"key": k, **ol.perf(r, 252, rf.loc[start:end]),
                    "ret_2008": yr.get(2008), "ret_2020": yr.get(2020), "ret_2022": yr.get(2022),
                    "ret_2023_26": float((1 + r.loc["2023":]).prod() - 1),
                    "corr_spy": float(r.corr(R["SPY"].loc[start:end]))})
    held = top_m.apply(lambda row: [c for c in row.index if row[c] > 0], axis=1)
    counts = pd.Series([c for lst in held for c in lst]).value_counts()
    return pd.DataFrame(res), series, (counts / len([h for h in held if h])).round(3).to_dict(), held


def part2(rf, spy_raw):
    s = pd.read_csv(ol.DATA / "ai_stocks_adj_d.csv", index_col=0, parse_dates=True)
    g = pd.read_csv(ol.DATA / "growth_etf_adj_d.csv", index_col=0, parse_dates=True)
    R = s.pct_change(fill_method=None)
    start = "2023-01-03"
    rows, series = [], {}
    up = regime(spy_raw.reindex(R.index).ffill()).reindex(R.index)
    for name, names in [("mag7_ew", MAG7), ("ai_leaders_ew", AI), ("mag7_plus_ai_ew", sorted(set(MAG7 + AI)))]:
        r = R[names].loc[start:].mean(axis=1)     # equal weight, daily rebalanced, names join when listed
        series[name] = r
        expo = up.loc[start:].map({True: 1.0, False: 0.5})
        series[name + "_ma"] = expo * r + (1 - expo) * rf.reindex(r.index).fillna(0) - expo.diff().abs().fillna(0) * 0.5 * COST
    # top-5 momentum (6-1) inside the combined group, re-picked monthly
    allg = sorted(set(MAG7 + AI))
    m = s[allg].resample("ME").last()
    mom = m.shift(1) / m.shift(7) - 1
    me = s.groupby([s.index.year, s.index.month]).tail(1).index
    W = pd.DataFrame(0.0, index=me, columns=allg)
    for d in me:
        x = mom.loc[d + pd.offsets.MonthEnd(0)].dropna() if (d + pd.offsets.MonthEnd(0)) in mom.index else pd.Series(dtype=float)
        if len(x) >= 8:
            W.loc[d, x.nlargest(5).index] = 0.2
    Wd = W.reindex(R.index).ffill().shift(1).fillna(0)
    series["mag7_ai_top5_momentum"] = ((Wd * R[allg].fillna(0)).sum(axis=1) - Wd.diff().abs().sum(axis=1) * 1e-3).loc[start:]
    for b in ["QQQ", "SPY", "SMH"]:
        series[b] = g[b].pct_change().loc[start:]
    for k, r in series.items():
        r = r.dropna()
        yr = (1 + r).groupby(r.index.year).prod() - 1
        rows.append({"key": k, **ol.perf(r, 252, rf.reindex(r.index)), **{f"ret_{y}": yr.get(y) for y in [2023, 2024, 2025, 2026]}})
    return pd.DataFrame(rows), series


def part3_and_mixes(rf_d):
    m = ol.load_market()
    graw = pd.read_csv(ol.DATA / "growth_etf_raw_d.csv", index_col=0, parse_dates=True)
    gadj = pd.read_csv(ol.DATA / "growth_etf_adj_d.csv", index_col=0, parse_dates=True)
    idx = m["raw"].index
    m["raw"]["QQQ"], m["adj"]["QQQ"] = graw["QQQ"].reindex(idx), gadj["QQQ"].reindex(idx)
    tr = np.log(m["adj"]["QQQ"] / m["adj"]["QQQ"].shift(252)) - np.log(m["raw"]["QQQ"] / m["raw"]["QQQ"].shift(252))
    m["q"]["QQQ"] = tr.clip(0, 0.08).fillna(0.007)
    lr = np.log(m["adj"]).diff()
    for col, win, key in [("QQQ", 63, "rel"), ("QQQ", 504, "rel_long")]:
        rv = lr[col].rolling(win, min_periods=40).std() / lr["SPY"].rolling(win, min_periods=40).std()
        m[key]["QQQ"] = rv.clip(0.6, 2.5).fillna(1.25)
    dates = [d for d in month_ends(idx) if d >= pd.Timestamp("1999-12-01")]
    tg = {d: ["QQQ"] for d in dates}
    rows, ser = [], {}
    for name, cfg in [("qqq_shares", Cfg(use_leaps=False, sell_calls=False)), ("qqq_covered_call", Cfg(use_leaps=False)),
                      ("qqq_leaps_only", Cfg(sell_calls=False)), ("qqq_pmcc", Cfg()), ("qqq_pmcc_lam1.5", Cfg(lam=1.5)),
                      ("qqq_leaps_lam1.5", Cfg(lam=1.5, sell_calls=False))]:
        r, ex = simulate(m, tg, cfg, sector=False)
        r.index = r.index + pd.offsets.MonthEnd(0)
        ser[name] = r
        rows.append({"key": name, **ol.perf(r, 12), **ex})
    p3 = pd.DataFrame(rows)

    # ---------------- mixes 2007-04 .. 2026-09
    a = pd.read_csv(OUT / "A_allocation_series.csv", index_col=0, parse_dates=True)
    core = pd.read_csv(OUT / "A_core_series.csv", index_col=0, parse_dates=True)
    vm = pd.read_csv(OUT / "V_monthly.csv", index_col=0, parse_dates=True)
    sigd = pd.read_csv(OUT / "V_signals_daily.csv", index_col=0, parse_dates=True)
    boost = sigd.V3_signal.astype(bool).groupby(sigd.index + pd.offsets.MonthEnd(0)).last().shift(1)
    ix = a.loc["2007-04-30":"2026-09-30"].index
    boost = boost.reindex(ix).fillna(False).astype(bool)
    rf_m = (1 + rf_d).groupby([rf_d.index.year, rf_d.index.month]).prod() - 1
    rf_m.index = [pd.Timestamp(y, mo, 1) + pd.offsets.MonthEnd(0) for y, mo in rf_m.index]
    rf_m = rf_m.reindex(ix).fillna(0)
    fade = vm["V3_spike_fade_short"].reindex(ix).fillna(0)

    def monthly(d):
        x = (1 + d).groupby([d.index.year, d.index.month]).prod() - 1
        x.index = [pd.Timestamp(y, mo, 1) + pd.offsets.MonthEnd(0) for y, mo in x.index]
        return x.reindex(ix)

    return p3, ser, (a, core, ix, boost, rf_m, fade, monthly)


def main():
    px, spy_raw, rf = load()
    p1, s1, held_share, held = part1(px, spy_raw, rf)
    p2, s2 = part2(rf, spy_raw)
    p3, s3, ctx = part3_and_mixes(rf)
    a, core, ix, boost, rf_m, fade, monthly = ctx
    lsn = pd.read_csv(OUT / "LS_french_daily.csv", index_col=0, parse_dates=True)["french_neutral"]
    q1, q15 = s3["qqq_pmcc"].reindex(ix), s3["qqq_pmcc_lam1.5"].reindex(ix)
    spy_core = core["core_1.0"].reindex(ix)
    spy_core_hi = core["core_1.5"].reindex(ix)
    qqq_boosted = q1.where(~boost, q15)
    ql, ql15 = s3["qqq_leaps_only"].reindex(ix), s3["qqq_leaps_lam1.5"].reindex(ix)
    qqq_leaps_boosted = ql.where(~boost, ql15)
    spy_boosted = spy_core.where(~boost, spy_core_hi)
    sleeve = lambda name: monthly(s1[name]) - rf_m
    mixes = {
        "Balanced (SPY core) - reference": spy_boosted + 0.2 * fade,
        "Balanced + 20% momentum L/S (prior best)": spy_boosted + 0.2 * fade + 0.2 * (monthly(lsn) - rf_m),
        "QQQ core instead of SPY": qqq_boosted + 0.2 * fade,
        "QQQ LEAPS core, no calls": qqq_leaps_boosted + 0.2 * fade,
        "50/50 SPY + QQQ core": 0.5 * (spy_boosted + qqq_boosted) + 0.2 * fade,
        "50/50 SPY LEAPS+calls / QQQ LEAPS (no calls)": 0.5 * (spy_boosted + qqq_leaps_boosted) + 0.2 * fade,
        "Same + 20% growth mix (MA) + 20% momentum L/S": 0.5 * (spy_boosted + qqq_leaps_boosted) + 0.2 * fade
            + 0.2 * sleeve("growth_mix_ma") + 0.2 * (monthly(lsn) - rf_m),
        "Same + 20% leadership L/S 150/50 (band) + 20% momentum L/S": 0.5 * (spy_boosted + qqq_leaps_boosted) + 0.2 * fade
            + 0.2 * sleeve("lead_ls_150_band1") + 0.2 * (monthly(lsn) - rf_m),
        "50/50 core + 20% growth mix (MA tilt)": 0.5 * (spy_boosted + qqq_boosted) + 0.2 * fade + 0.2 * sleeve("growth_mix_ma"),
        "50/50 core + 20% leadership rotation (MA tilt)": 0.5 * (spy_boosted + qqq_boosted) + 0.2 * fade + 0.2 * sleeve("lead_long_ma"),
        "50/50 core + 20% leadership L/S 150/50 (band)": 0.5 * (spy_boosted + qqq_boosted) + 0.2 * fade + 0.2 * sleeve("lead_ls_150_band1"),
        "50/50 core + 20% growth mix (MA) + 20% momentum L/S": 0.5 * (spy_boosted + qqq_boosted) + 0.2 * fade
            + 0.2 * sleeve("growth_mix_ma") + 0.2 * (monthly(lsn) - rf_m),
        "SPY buy & hold": core["spy_shares"].reindex(ix),
        "QQQ buy & hold": s3["qqq_shares"].reindex(ix),
    }
    rows = []
    for k, r in mixes.items():
        r = r.dropna()
        yr = (1 + r).groupby(r.index.year).prod() - 1
        rows.append({"mix": k, **ol.perf(r, 12, rf_m.reindex(r.index)), "ret_2008": yr.get(2008),
                     "ret_2020": yr.get(2020), "ret_2022": yr.get(2022)})
    p4 = pd.DataFrame(rows)

    p1.to_csv(OUT / "G1_leadership.csv", index=False)
    p2.to_csv(OUT / "G2_mag7_ai.csv", index=False)
    p3.to_csv(OUT / "G3_qqq_leaps.csv", index=False)
    p4.to_csv(OUT / "G4_mixes.csv", index=False)
    pd.DataFrame(s1).to_csv(OUT / "G1_series_daily.csv")
    pd.DataFrame(s2).to_csv(OUT / "G2_series_daily.csv")
    pd.DataFrame({k: v for k, v in mixes.items()}).to_csv(OUT / "G4_mix_series.csv")
    last = held[held.map(len) > 0].iloc[-1] if (held.map(len) > 0).any() else []
    live = {"leadership_top3_now": list(last), "share_of_months_held": held_share,
            "spy_above_50d": bool(spy_raw.iloc[-1] > spy_raw.rolling(50).mean().iloc[-1]),
            "qqq_above_50d": bool(px["QQQ"].iloc[-1] > px["QQQ"].rolling(50).mean().iloc[-1]), "as_of": str(px.index[-1].date())}
    (OUT / "G_live.json").write_text(json.dumps(live, indent=1))
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    c = ["cagr", "ann_vol", "sharpe", "max_dd", "calmar"]
    print("== Part 1 (2002-08 .. 2026-10)\n", p1[["key"] + c + ["ret_2008", "ret_2022", "ret_2023_26", "corr_spy"]].to_string(index=False))
    print("== Part 2 (2023-01 .. )\n", p2[["key"] + c + ["ret_2023", "ret_2024", "ret_2025", "ret_2026"]].to_string(index=False))
    print("== Part 3 QQQ LEAPS\n", p3[["key"] + c + ["premium_yield", "call_payout_yield", "avg_capital_in_options"]].to_string(index=False))
    print("== Part 4 mixes (2007-04 .. 2026-09)\n", p4[["mix"] + c + ["ret_2008", "ret_2020", "ret_2022"]].to_string(index=False))
    print(json.dumps(live, indent=1))


if __name__ == "__main__":
    main()
