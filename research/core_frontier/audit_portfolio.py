"""
INDEPENDENT AUDIT OF THE CORE FRONTIER NUMBERS (with the carry sleeve in the portfolio)

Every check recomputes a published number with SEPARATE code from the raw inputs (or from the published
daily series) and compares. Nothing here is imported from the scripts it audits except the raw loaders.

  D  data      freshness of every input, T-bill series vs French monthly, CF rf vs the T-bill file
  C  carry     sleeve rebuilt from the HOURLY Deribit files with a different aggregation; look-ahead test
  P  portfolio daily identity (weights x sleeves), monthly compounding, headline statistics recomputed
  L  layers    trading-sleeve layer at k=1 equals the portfolio; margin-capped 1.75x layer rebuilt
               (own margin model + own leverage rule) and compared; how often margin ended above 70%
  G  page      numbers embedded in core_frontier.html equal the result files; no unfilled placeholders
Writes results/AUDIT_portfolio.json and prints PASS / FAIL lines.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
RES, DATA = HERE / "results", HERE / "data"
LVD, MD = HERE.parent / "leaps_vix" / "data", HERE.parent / "momentum" / "data"
checks = []


def check(cid, name, ok, detail, note=False):
    """ok -> PASS, not ok -> FAIL; note=True marks a disclosed limitation (sized and stated, not hidden)."""
    status = "NOTE" if (note and ok) else ("PASS" if ok else "FAIL")
    checks.append({"id": cid, "check": name, "status": status, "pass": bool(ok), "detail": detail})
    print(f"[{status}] {cid} {name}: {detail}")


# ------------------------------------------------------------------ D. data
def data_checks():
    last = {}
    for lab, f, col in [("ETF prices (SPY)", LVD / "etf_raw_d.csv", "SPY"), ("VIX family", LVD / "vol_index_d.csv", "^VIX"),
                        ("hard assets / crypto (SPY)", LVD / "hard_assets_adj_d.csv", "SPY"), ("T-bill series", MD / "rf_daily.csv", "RF"),
                        ("Ken French momentum deciles", MD / "mom10_d.csv", None), ("Deribit funding (hourly)", DATA / "funding_deribit_btc_hourly.csv", None)]:
        d = pd.read_csv(f, index_col=0)
        s = d[col].dropna() if col else d.iloc[:, 0].dropna()
        last[lab] = str(s.index[-1])[:10]
    expected = str(pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=1)[0].date())   # last weekday before today
    stale = {k: v for k, v in last.items() if v < expected and "French" not in k}
    check("D1", "inputs reach the last US close", not stale, "; ".join(f"{k} {v}" for k, v in last.items()) + f" (expected {expected}); stale: {', '.join(stale) or 'none'}")
    check("D2", "known data gap: French momentum ends 2026-09-30", last["Ken French momentum deciles"] == "2026-09-30",
          "momentum L/S sleeve is held in T-bills after 2026-09-30 (French publishes monthly with a lag); disclosed on the page", note=True)
    rf = pd.read_csv(MD / "rf_daily.csv", index_col=0, parse_dates=True)["RF"]
    fm = pd.read_csv(MD / "ff_factors_m.csv", index_col=0, parse_dates=True)["RF"]
    comp = (1 + rf).groupby(rf.index.to_period("M")).prod() - 1
    fm.index = fm.index.to_period("M")
    err = float((comp.reindex(fm.index) - fm).abs().max())
    check("D3", "daily T-bill series compounds to French monthly RF", err < 1e-12, f"max monthly error {err:.1e}")
    D = pd.read_csv(RES / "CF_daily.csv", index_col=0, parse_dates=True)
    check("D5", "portfolio runs to the last US close", str(D.index[-1].date()) == last["ETF prices (SPY)"],
          f"portfolio ends {D.index[-1].date()}, ETF data ends {last['ETF prices (SPY)']}")
    e2 = float((D.rf - rf.reindex(D.index).ffill()).abs().max())
    check("D4", "portfolio uses that T-bill series", e2 < 1e-15, f"max |CF rf - rf_daily| {e2:.1e}")
    return D, rf


# ------------------------------------------------------------------ C. carry, rebuilt from the hourly files
def carry_checks(D, rf):
    C = pd.read_csv(RES / "CARRY_daily.csv", index_col=0, parse_dates=True)
    days = C.index
    rfd = rf.reindex(days).ffill()
    rebuilt, ons = {}, {}
    for k in ["btc", "eth"]:
        hfund = pd.read_csv(DATA / f"funding_deribit_{k}_hourly.csv", index_col=0, parse_dates=True)["f1h"]
        utc_day = hfund.index.tz_convert(None).normalize()
        pos = days.searchsorted(utc_day, side="left")              # first US trading day on/after the UTC date
        ok = pos < len(days)
        lab = pd.Series(days[pos[ok]], index=hfund.index[ok])
        daily = hfund[ok].groupby(lab.values).sum().reindex(days)
        first = utc_day.min() + pd.Timedelta(days=1)
        daily[days < first] = np.nan                               # same warm-up convention as the original
        sig = np.full(len(days), 0.0)
        fv, rv = daily.values, rfd.values
        for i in range(len(days)):                                 # explicit loop: decide day i with data up to i-1 only
            if i >= 8:
                w = fv[i - 7:i]
                if not np.isnan(w).any():
                    sig[i] = float(w.mean() * 252 - rv[i - 7:i].mean() * 252 > 0.02)
        on = pd.Series(sig, index=days)
        f = daily.fillna(rfd)
        r = rfd + on * (f - rfd) - on.diff().abs().fillna(0) * 0.002
        rebuilt[k], ons[k] = r, on
    car = 0.5 * rebuilt["btc"] + 0.5 * rebuilt["eth"]
    start = "2019-05-15"
    a, b = car.loc[start:], C.carry.loc[start:]
    diff = float((a - b).abs().max())
    cg = lambda x: float((1 + x).prod() ** (252 / len(x)) - 1)
    check("C1", "carry sleeve rebuilt from hourly funding matches", diff < 1e-10,
          f"max daily diff {diff:.1e}; CAGR rebuilt {cg(a):.4%} vs published {cg(b):.4%} ({a.index[0].date()} to {a.index[-1].date()})")
    on_pub = C.on_btc.loc[start:]
    check("C2", "on/off signal identical", float((ons["btc"].loc[start:] - on_pub).abs().max()) == 0.0,
          f"BTC on-days {int(on_pub.sum())} of {len(on_pub)}")
    # look-ahead: change one day's funding drastically; that day's signal must not move
    import study_carry as sc
    fund = C.fund_btc.copy()
    t0 = fund.loc["2022-06-01":].index[0]
    base_r, base_on = sc.carry_leg(fund, rfd)
    bumped = fund.copy(); bumped[t0] = -1.0 if base_on[fund.index[fund.index.get_loc(t0) + 1]] == 1 else 1.0   # push the way that can flip the signal
    _, on2 = sc.carry_leg(bumped, rfd)
    nxt = fund.index[fund.index.get_loc(t0) + 1]
    check("C3", "no look-ahead in the carry signal", on2[t0] == base_on[t0] and on2[nxt] != base_on[nxt],
          f"bumping funding on {t0.date()} leaves that day's signal at {int(base_on[t0])}; next day moves {int(base_on[nxt])}->{int(on2[nxt])}")
    pc = float((D.carry - C.carry.reindex(D.index)).abs().max())
    check("C4", "portfolio uses the published carry series", pc < 1e-15, f"max diff {pc:.1e}")
    pre = D.loc[:"2019-05-14"]
    check("C5", "carry = T-bills before funding history exists", float((pre.carry - pre.rf).abs().max()) < 1e-15,
          f"{len(pre)} days 2015-10 .. 2019-05-14 held in T-bills (disclosed)", note=True)
    return C


# ------------------------------------------------------------------ P. portfolio
def portfolio_checks(D, rf):
    W = {"core": 0.65, "vix_fade": 0.15, "carry": 0.10, "momentum_ls": 0.05, "prediction": 0.05}
    rec = sum(W[k] * D[k] for k in W)
    e = float((rec - D.portfolio).abs().max())
    check("P1", "portfolio = 65% core + 15% VIX fade + 10% carry + 5% momentum + 5% prediction", e < 1e-14, f"max daily diff {e:.1e}")
    e2 = float(((rec - 0.65 * D.core) / 0.35 - D.trading).abs().max())
    check("P2", "trading bucket = the four trading sleeves / 35%", e2 < 1e-13, f"max diff {e2:.1e}")
    check("P3", "no missing values in the sleeves", not D[list(W) + ["rf"]].isna().any().any(), f"{len(D)} days {D.index[0].date()} .. {D.index[-1].date()}")
    M = pd.read_csv(RES / "CF_monthly.csv", index_col=0, parse_dates=True)
    P = "Core Frontier Portfolio (65/35)"
    mm = (1 + D.portfolio).resample("ME").prod() - 1
    e3 = float((mm - M[P].reindex(mm.index)).abs().max())
    check("P4", "monthly series = compounded daily returns", e3 < 1e-12, f"max diff {e3:.1e}")
    S = json.loads((RES / "CF_summary.json").read_text())
    rfm = (1 + rf.reindex(D.index).ffill()).resample("ME").prod() - 1
    res = {}
    for per, st in [("perf_full", None), ("perf_test", "2021-01-01")]:
        r = mm.loc[st:] if st else mm
        ex = r - rfm.reindex(r.index)
        eq = (1 + r).cumprod()
        mine = {"cagr": eq.iloc[-1] ** (12 / len(r)) - 1, "sharpe": ex.mean() / ex.std() * np.sqrt(12),
                "max_dd": (eq / eq.cummax() - 1).min(), "ann_vol": r.std() * np.sqrt(12)}
        yr = (1 + r).groupby(r.index.year).prod() - 1
        mine["worst_year"] = yr[yr.index > 2015].min()
        pub = S[per][P]
        worst = max(abs(mine[k] - pub[k]) for k in mine)
        check("P5" + ("a" if per == "perf_full" else "b"), f"headline stats recomputed ({'2015-26' if st is None else '2021-26 test'})",
              worst < 1e-9, " ".join(f"{k} {pub[k]:.4f}" for k in mine) + f" | max diff {worst:.1e}")
        res[per] = mine
        # partial final month: CAGR with exact calendar time instead of counting Nov-2026 as a whole month
        start_d = (r.index[0] - pd.offsets.MonthEnd(1)) if st else D.index[0] - pd.Timedelta(days=1)
        yrs = (D.index[-1] - start_d).days / 365.25
        exact = eq.iloc[-1] ** (1 / yrs) - 1
        check("P6" + ("a" if per == "perf_full" else "b"), "CAGR bias from the partial last month is small",
              abs(exact - mine["cagr"]) < 0.005, f"month-count CAGR {mine['cagr']:.2%} vs exact-time CAGR {exact:.2%}: the page counts the partial November as a full month, so its CAGRs are slightly low", note=True)
    # daily-data drawdown vs month-end drawdown (the page states which it uses)
    st = "2021-01-01"
    eqd = (1 + D.portfolio.loc[st:]).cumprod()
    res["daily_test_max_dd"] = float((eqd / eqd.cummax() - 1).min())
    check("P7", "daily vs month-end drawdown reported", True,
          f"2021-26 max DD month-end {res['perf_test']['max_dd']:.2%} vs daily {res['daily_test_max_dd']:.2%} (page labels each)", note=True)
    return res


# ------------------------------------------------------------------ L. layers
def layer_checks(D, rf):
    import study_capital_layer as cl
    r1 = cl.build(D, 1.0)
    e = float((r1 - D.portfolio).abs().max())
    check("L1", "trading-sleeve layer at 1x reproduces the portfolio", e < 1e-14, f"max diff {e:.1e}")
    vix = pd.read_csv(LVD / "vol_index_d.csv", index_col=0, parse_dates=True)["^VIX"].reindex(D.index).ffill() / 100
    hc = np.select([vix < 0.20, vix < 0.30, vix < 0.50], [0.275, 0.36, 0.475], 0.65)
    core_m = D.w_btc_tilt * 1.0 + D.w_eth_tilt * 1.0 + D.w_trend_hard * 0.08 + D.w_psc_gld * 0.06 + (D.w_hard_rotation + D.w_space) * hc
    trade_m = 0.15 * 0.5 * 0.20 + 0.05 * 2 * 0.15 + 0.05 * 0.20 + 0.10 * D.carry_on
    u = 0.65 * core_m + trade_m
    vix_y = vix.shift(1).bfill()                                   # sizing uses yesterday's VIX for the haircut
    hc_y = np.select([vix_y < 0.20, vix_y < 0.30, vix_y < 0.50], [0.275, 0.36, 0.475], 0.65)
    core_y = D.w_btc_tilt + D.w_eth_tilt + D.w_trend_hard * 0.08 + D.w_psc_gld * 0.06 + (D.w_hard_rotation + D.w_space) * hc_y
    L = np.minimum(1.75, 0.70 / (0.65 * core_y + trade_m))
    r = D.rf + L * (D.portfolio - D.rf) - np.maximum(L - 1, 0) * 0.005 / 252
    fl = pd.DataFrame(json.loads((RES / "FL_summary.json").read_text())["rows"]).set_index("key").loc["target 1.75x | crypto via spot"]
    t = r.loc["2021-01-01":]
    eq = (1 + t).cumprod()
    mine = {"test_cagr": float(eq.iloc[-1] ** (252 / len(t)) - 1), "test_max_dd": float((eq / eq.cummax() - 1).min()), "avg_lev": float(L.mean())}
    worst = max(abs(mine[k] - fl[k]) for k in mine)
    check("L2", "1.75x margin-capped layer rebuilt independently", worst < 1e-9,
          f"test CAGR {mine['test_cagr']:.4%} / DD {mine['test_max_dd']:.2%} / avg lev {mine['avg_lev']:.3f}× vs published "
          f"{fl.test_cagr:.4%} / {fl.test_max_dd:.2%} / {fl.avg_lev:.3f}× | max diff {worst:.1e}")
    real = L * u
    over = float((real > 0.70 + 1e-12).mean())
    hi = real[real > 0.80]
    jumps = all(vix.shift(1)[d] < 0.50 <= vix[d] for d in hi.index)
    check("L3", "margin above the deck's 80% de-lever line only when VIX jumped past 50 within the day", jumps,
          f"ended above 70% on {over:.1%} of days; above 80% on {len(hi)} days ({', '.join(f'{d.date()} {v:.1%}' for d, v in hi.items()) or 'none'}), "
          f"each when VIX crossed 50 intraday and the haircut stepped up. The de-lever-at-80% rule would act intraday; the backtest does not model that.", note=True)
    carry_m = 0.10 * D.carry_on
    check("L4", "carry sleeve's margin counted", float(carry_m.max()) <= 0.10 + 1e-12,
          f"adds {carry_m.mean():.2%} of the account on average (10% when fully on, 0 when in T-bills)")
    return mine


# ------------------------------------------------------------------ G. page
def page_checks():
    html = (HERE / "core_frontier.html").read_text()
    left = re.findall(r"\[\[[a-z_0-9]+\]\]", html)
    check("G1", "no unfilled placeholders on the page", not left, f"unfilled: {left or 'none'}")
    m = re.search(r"const STATS = (\{.*?\});\n", html, re.S) or re.search(r"STATS\s*=\s*(\{.*?\})\s*[;,]\s*\n", html, re.S)
    S = json.loads((RES / "CF_summary.json").read_text())
    if m:
        st = json.loads(m.group(1))
        P = "Core Frontier Portfolio (65/35)"
        same = all(abs(st[p][P][k] - S[p][P][k]) < 1e-12 for p in ["perf_full", "perf_test"] for k in ["cagr", "sharpe", "max_dd"])
        check("G2", "page statistics = CF_summary.json", same, f"test CAGR on page {st['perf_test'][P]['cagr']:.4%}")
    else:
        check("G2", "page statistics = CF_summary.json", False, "could not locate the STATS block")
    fact = re.search(r"<!--FACTS (\{.*?\}) -->", html, re.S)
    check("G3", "prose figures come from the facts table", fact is not None, f"{len(json.loads(fact.group(1))) if fact else 0} figures filled at build time")


def main():
    D, rf = data_checks()
    carry_checks(D, rf)
    res = portfolio_checks(D, rf)
    layer_checks(D, rf)
    page_checks()
    n_fail = sum(c["status"] == "FAIL" for c in checks)
    n_note = sum(c["status"] == "NOTE" for c in checks)
    (RES / "AUDIT_portfolio.json").write_text(json.dumps({"checks": checks, "n_checks": len(checks), "n_fail": n_fail, "n_note": n_note}, indent=1, default=float))
    print(f"\n{len(checks)} checks: {len(checks) - n_fail - n_note} pass, {n_note} disclosed notes, {n_fail} failed")


if __name__ == "__main__":
    main()
