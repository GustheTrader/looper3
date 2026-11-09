"""
PERPS FOR CRYPTO -- replace spot BTC/ETH (and the CME-futures idea) in the Core Frontier with
perpetual futures, using REAL funding-rate history.

What changes with a perp
  * cost: a long perp pays the funding rate (Deribit hourly history 2019-04 ->, Hyperliquid 2023-05 ->)
    instead of the T-bill rate that fully-funded spot gives up. Funding > T-bills = perps cost MORE.
  * margin: a perp needs only collateral m of the notional (we test 25% / 35% / 50%), so less of the
    account is tied up and the 70% margin guardrail allows more leverage on everything else.
  * liquidation: if price falls more than ~80% of m before collateral is topped up, the position is
    closed. Checked against every 1-day and 3-day (unattended weekend) drop since 2015.
  * carry: when the crypto trend signal is partly in cash, the idle crypto allocation can run a
    cash-and-carry (long spot, short perp = no price risk) that EARNS funding while it beats T-bills.

Sections
  A. funding facts             B. crypto sleeve: spot vs perp (+ carry)
  C. standalone carry sleeve   D. full 65/35 portfolio + leverage layer with perp margin
Before 2019-04 (no funding history) the perp is charged T-bills + 5%/yr (2017-19 BitMEX-like levels).
Design 2015-10 .. 2020-12, test 2021-01 ->, as in every earlier layer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "leaps_vix"))
sys.path.insert(0, str(HERE.parent / "momentum"))
import options_lib as ol  # noqa: E402
import study_hard25 as h  # noqa: E402
import study_margin as sm  # noqa: E402

OUT, DATA = HERE / "results", HERE / "data"
from study_carry import CARRY_COST, CARRY_ON, PRE_PREMIUM, funding_on_days  # noqa: E402,F401  (one definition of the carry rules)
import study_carry as sc  # noqa: E402


def drops():
    import yfinance as yf
    d = yf.download(["BTC-USD", "ETH-USD"], start="2015-10-01", progress=False, auto_adjust=False)
    res = {}
    for c in ["BTC-USD", "ETH-USD"]:
        lo, cl = d["Low"][c].dropna(), d["Close"][c].dropna()
        one = (lo / cl.shift(1) - 1).min()
        three = (lo.rolling(3).min() / cl.shift(3) - 1).min()
        res[c] = {"worst_1day_low": float(one), "date_1day": str((lo / cl.shift(1) - 1).idxmin().date()),
                  "worst_3day_low": float(three), "date_3day": str((lo.rolling(3).min() / cl.shift(3) - 1).idxmin().date())}
    return res


def stats(r, rf, sl=slice(None, None)):
    return sm.metrics(r.loc[sl].dropna(), rf.loc[sl].reindex(r.loc[sl].dropna().index))


def main():
    h.SIGNAL = h.DEFAULT_SIGNAL
    px, rf = h.load()
    R = px.pct_change(fill_method=None)
    days = px.index
    F = funding_on_days(days, rf)
    D = pd.read_csv(OUT / "CF_daily.csv", index_col=0, parse_dates=True)
    idx = D.index
    rfD = D.rf

    # ---------- A. funding facts (annualised, on US trading days)
    fd = pd.read_csv(DATA / "funding_daily.csv", index_col=0, parse_dates=True)
    rf_y = (rf.groupby(rf.index.year).sum())
    yearly = (fd.groupby(fd.index.year).sum() * 365 / fd.groupby(fd.index.year).count()).round(4)
    yearly["tbill"] = rf_y.reindex(yearly.index)
    facts = {"yearly": {int(y): {k: (None if pd.isna(v) else float(v)) for k, v in row.items()} for y, row in yearly.iterrows()},
             "avg": {c: float(fd[c].mean() * 365) for c in fd.columns},
             "pct_negative_days": {c: float((fd[c].dropna() < 0).mean()) for c in fd.columns},
             "worst_week": {c: float(fd[c].rolling(7).sum().min()) for c in fd.columns},
             "best_week": {c: float(fd[c].rolling(7).sum().max()) for c in fd.columns}}

    # ---------- B. crypto sleeves: spot vs perp, with and without the carry on idle allocation
    sleeves, expo = {}, {}
    for k, tk in [("btc", "BTC-USD"), ("eth", "ETH-USD")]:
        e = h.ens_frac(px[tk]).where(px[tk].notna(), 0.0)
        cost = e.diff().abs().fillna(0) * 1e-3
        fund = F[f"perp_{k}"]
        trail = F[f"deribit_{k}"].rolling(7).mean().shift(1) * 252 - rf.rolling(7).mean().shift(1) * 252
        on = (trail > CARRY_ON).astype(float)
        carry_ex = (1 - e) * on * (F[f"deribit_{k}"].fillna(rf) - rf) - ((1 - e) * on).diff().abs().fillna(0) * CARRY_COST
        sleeves[f"{k}_spot"] = (e * (R[tk] - rf) - cost).where(px[tk].notna(), 0.0)
        sleeves[f"{k}_perp"] = (e * (R[tk] - fund) - cost).where(px[tk].notna(), 0.0)
        sleeves[f"{k}_perp_stress"] = (e * (R[tk] - F[f"stress_{k}"]) - cost).where(px[tk].notna(), 0.0)
        sleeves[f"{k}_perp_carry"] = sleeves[f"{k}_perp"] + carry_ex.where(px[tk].notna(), 0.0)
        expo[k] = e
        expo[f"{k}_carry_on"] = on
    S = pd.DataFrame(sleeves).loc[idx]
    rfI = rf.reindex(idx)
    sleeve_tab = {c: {p: stats(S[c] + rfI, rfI, sl) for p, sl in
                      {"design": slice(None, "2020-12-31"), "test": slice("2021-01-01", None), "full": slice(None, None)}.items()}
                  for c in S.columns}

    # ---------- C. standalone carry sleeve (50/50 BTC/ETH, long spot short perp, T-bills when switched off)
    CB = sc.build()
    carry_sleeve = CB.carry.loc[sc.START:]
    carry_tab = {p: stats(carry_sleeve, rf.reindex(carry_sleeve.index), sl) for p, sl in
                 {"2019-20": slice(None, "2020-12-31"), "test": slice("2021-01-01", None), "full": slice(None, None)}.items()}
    carry_year = (1 + carry_sleeve).groupby(carry_sleeve.index.year).prod() - 1
    tb_year = (1 + rf.loc[carry_sleeve.index]).groupby(carry_sleeve.index.year).prod() - 1
    carry_on_pct = float(CB.carry_on.loc[sc.START:].mean())

    # ---------- D. whole portfolio: swap spot crypto for perps inside the 65% core
    wb, we = D.w_btc_tilt, D.w_eth_tilt
    def swap(kind):
        return D.portfolio + 0.65 * (wb * (S[f"btc_{kind}"] - S.btc_spot) + we * (S[f"eth_{kind}"] - S.eth_spot))
    port = {"spot (as published)": D.portfolio, "perps": swap("perp"), "perps + idle carry": swap("perp_carry"),
            "perps, dearer venue each day": swap("perp_stress")}
    vix = ol.load_market()["vix"].reindex(idx).ffill()

    def run(r, L_tgt, m, cap=0.70):
        u1 = sm_unit(D, vix, m)
        L = np.minimum(L_tgt, cap / sm_unit(D, vix.shift(1).bfill(), m))      # sized on today's positions, yesterday's VIX
        out = rfD + L * (r - rfD) - (L - 1).clip(lower=0) * 0.005 / 252
        return out, L, L * u1

    def sm_unit(D, vix, crypto_margin):
        fixed = {"w_btc_tilt": crypto_margin, "w_eth_tilt": crypto_margin, "w_trend_hard": 0.08, "w_psc_gld": 0.06}
        core = sum(D[c] * v for c, v in fixed.items())
        hc = pd.Series([sm.haircut(v, False) for v in vix.values], index=D.index)
        return 0.65 * (core + (D.w_hard_rotation + D.w_space) * hc) + sm.trading_margin(D)

    rows, keep = [], {}
    configs = [("spot (as published)", 1.0), ("perps", 0.50), ("perps", 0.35), ("perps", 0.25),
               ("perps + idle carry", 0.50), ("perps + idle carry", 0.35), ("perps, dearer venue each day", 0.35)]
    for name, m in configs:
        for L in [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]:
            r, lev, util = run(port[name], L, m)
            key = f"{name} | margin {int(m * 100)}% | target {L}x"
            row = {"key": key, "crypto": name, "margin": m, "target": L, "avg_lev": float(lev.mean()), "avg_util": float(util.mean())}
            for p, sl in {"design": slice(None, "2020-12-31"), "test": slice("2021-01-01", None), "full": slice(None, None)}.items():
                row |= {f"{p}_{k}": v for k, v in sm.metrics(r.loc[sl], rfD.loc[sl]).items()}
            rows.append(row)
            keep[key] = (r, lev, util)
    g = pd.DataFrame(rows)
    ok = g[g.design_worst_year >= -0.15]
    pick = ok.sort_values("design_calmar", ascending=False).iloc[0]["key"]
    ok_full = g[g.full_worst_year >= -0.15]
    max_cagr = ok_full.sort_values("full_cagr", ascending=False).iloc[0]["key"]
    L_REC = float(json.loads((OUT / "FL_summary.json").read_text())["chosen_by_design_calmar"].split("target ")[1].split("x")[0])
    prev_pick = f"spot (as published) | margin 100% | target {L_REC}x"
    liq = drops()

    # ---------- E. the published trading mix vs the previous one (carry 10%, no FF, prediction 5%)
    carry_full = D.carry
    swap_port = 0.65 * D.core + 0.15 * D.vix_fade + 0.10 * D.carry + 0.05 * D.momentum_ls + 0.05 * D.prediction
    hl = {}
    for k in ["btc", "eth"]:
        f = F[f"hl_{k}"]
        tr_ = f.rolling(7).mean().shift(1) * 252 - rf.rolling(7).mean().shift(1) * 252
        on = (tr_ > CARRY_ON).astype(float)
        hl[k] = rf + on * (f.fillna(rf) - rf) - on.diff().abs().fillna(0) * CARRY_COST
    hl_sleeve = (0.5 * hl["btc"] + 0.5 * hl["eth"]).loc["2023-06-01":]       # same rule on Hyperliquid funding (comparison only)
    swaps = {}
    for name, r0 in {"previous mix (carry 10%, no FF)": swap_port, "published mix": D.portfolio}.items():
        for L in [1.0, L_REC]:
            r, lev, util = run(r0, L, 1.0)
            swaps[f"{name} | target {L}x"] = {p: sm.metrics(r.loc[sl], rfD.loc[sl]) for p, sl in
                                              {"design": slice(None, "2020-12-31"), "test": slice("2021-01-01", None), "full": slice(None, None)}.items()}
            if name == "published mix" and L == L_REC:
                keep["carry_swap_175"] = (r, lev, util)
    hl_tab = sm.metrics(hl_sleeve, rf.reindex(hl_sleeve.index))
    der_same = sm.metrics(carry_sleeve.loc["2023-06-01":], rf.reindex(carry_sleeve.loc["2023-06-01":].index))
    corr_carry = float(pd.concat([carry_full - rfD, D.core - rfD], axis=1).loc["2019-06-01":].corr().iloc[0, 1])

    r_pick = keep[pick][0]
    yr = lambda r: {int(a): float(b) for a, b in ((1 + r).groupby(r.index.year).prod() - 1).items()}
    out = {"funding": facts, "sleeves": sleeve_tab, "carry_sleeve": carry_tab, "carry_on_pct": carry_on_pct,
           "carry_yearly": {int(a): float(b) for a, b in carry_year.items()}, "tbill_yearly": {int(a): float(b) for a, b in tb_year.items()},
           "grid": g.to_dict("records"), "ff_swap": swaps, "L_rec": L_REC, "carry_hl_2023": hl_tab, "carry_deribit_2023": der_same,
           "carry_corr_core": corr_carry, "pick_by_design_calmar": pick, "max_cagr_meeting_rule": max_cagr, "previous_pick": prev_pick,
           "liquidation": liq, "yearly_pick": yr(r_pick), "yearly_prev": yr(keep[prev_pick][0]),
           "unlevered": {n: {p: sm.metrics(v.loc[sl], rfD.loc[sl]) for p, sl in {"test": slice("2021-01-01", None), "full": slice(None, None)}.items()}
                         for n, v in port.items()}}
    (OUT / "PP_summary.json").write_text(json.dumps(out, indent=1, default=float))
    pd.DataFrame({"prev_pick": keep[prev_pick][0], "perp_pick": r_pick, "perp_pick_lev": keep[pick][1], "perp_pick_util": keep[pick][2],
                  "carry_sleeve": carry_sleeve.reindex(idx), "carry_swap_175": keep["carry_swap_175"][0], "rf": rfD}).to_csv(OUT / "PP_daily.csv")
    print(json.dumps({"swap": {k: {p: {x: round(v[p][x], 3) for x in ["cagr", "max_dd", "calmar", "worst_year"]} for p in v} for k, v in swaps.items()},
                      "hl": hl_tab, "der": der_same, "corr": corr_carry}, indent=0, default=float))
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(yearly)
    print(pd.DataFrame({c: {k: v["test"][k] for k in ["cagr", "max_dd", "sharpe", "calmar"]} for c, v in sleeve_tab.items()}).T)
    print("carry", carry_tab, "on%", carry_on_pct)
    print({n: {k: round(v["test"][k], 3) for k in ["cagr", "max_dd", "calmar"]} for n, v in out["unlevered"].items()})
    print(g[["key", "avg_lev", "avg_util", "design_calmar", "design_worst_year", "test_cagr", "test_max_dd", "test_calmar",
             "test_worst_year", "full_cagr", "full_max_dd", "full_worst_year"]].to_string(index=False))
    print("pick:", pick, "| max CAGR meeting rule:", max_cagr)
    print(liq)


if __name__ == "__main__":
    main()
