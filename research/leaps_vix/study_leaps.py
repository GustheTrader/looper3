"""
LEAPS EDGE -- buy deep-in-the-money LEAPS calls, sell short-dated calls above
them for income (a "poor man's covered call" / call diagonal), versus owning
the shares.

Underlyings
    SPY                 1996-10 .. latest month
    MOMENTUM SECTORS    top-2 SPDR sector ETFs by Sector Power Strength Rating
                        (blend of 6/9/12-month windows, skip 1; keep a holding
                        while it stays in the top 4 = buffer), 2001-01 .. latest
Strategies (all monthly, all net of bid/ask + commissions)
    shares        buy & hold the ETF(s), dividends reinvested
    covered_call  shares + sell a 1-month call each month
    leaps         LEAPS only, sized to the SAME delta exposure as the shares
                  (exposure multiple lam), spare cash in T-bills
    pmcc          LEAPS + sell a 1-month call per LEAPS each month
    pmcc_pause    pmcc, but skip the call sale while volatility is spiking
                  (VIX > 30 or VIX > 1.3x its 3-month average): after spikes,
                  markets often rebound hard and short calls would cap that
"""
from __future__ import annotations

import itertools
import json
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import options_lib as ol

sys.path.insert(0, str(ol.HERE.parent / "momentum"))
import momentum_lib as ml  # noqa: E402

OUT = ol.HERE / "results"


@dataclass
class Cfg:
    lam: float = 1.0          # delta exposure / NAV (1.0 = same market exposure as owning the shares)
    leaps_delta: float = 0.80
    leaps_tenor: float = 1.5  # years at purchase
    roll_at: float = 0.5      # roll when this much time is left
    short_delta: float = 0.25
    sell_calls: bool = True
    pause_in_spike: bool = False
    use_leaps: bool = True    # False -> shares (+ optional covered calls)
    cost_key: str | None = None  # override the cost model (e.g. "SECTOR_TIGHT")
    drift_band: float = 1.33  # re-size LEAPS when exposure drifts beyond [1/band, band] x target


@dataclass
class Leaps:
    u: str
    n: float
    K: float
    expiry: pd.Timestamp


def month_ends(idx: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(idx, index=idx)
    return [pd.Timestamp(x) for x in s.groupby([idx.year, idx.month]).last().values]


def momentum_targets(m, dates, k=2, buffer=4, every=1) -> dict:
    """Top-k SPDR sectors by blended SPSR at each month end, with a hold buffer."""
    adj = m["adj"].drop(columns=["SPY"])
    mret = adj.resample("ME").last().pct_change(fill_method=None)
    mk = m["adj"]["SPY"].resample("ME").last().pct_change()
    score = sum(ml.spsr(mret, mk, L, 1) for L in (6, 9, 12)) / 3
    rank = score.rank(axis=1, ascending=False)
    out, held = {}, []
    for i, d in enumerate(dates):
        me = d + pd.offsets.MonthEnd(0)
        if me not in rank.index or rank.loc[me].notna().sum() < 6:
            continue
        if held and i % every:
            out[d] = list(held)
            continue
        r = rank.loc[me]
        held = [h for h in held if r.get(h, 99) <= buffer]
        for c in r.sort_values().index:
            if len(held) >= k:
                break
            if c not in held:
                held.append(c)
        out[d] = list(held)
    return out


def spike(m, d) -> bool:
    v = m["vix"].loc[:d]
    return bool(v.iloc[-1] > 0.30 or v.iloc[-1] > 1.3 * v.iloc[-63:].mean())


def simulate(m, targets: dict, cfg: Cfg, sector: bool) -> tuple[pd.Series, dict]:
    raw, adj, q, rf, rel, rl, vix = m["raw"], m["adj"], m["q"], m["rf"], m["rel"], m["rel_long"], m["vix"]
    cost = ol.COSTS[cfg.cost_key or ("SECTOR" if sector else "SPY")]
    A = ol.PARAMS["A_VIX"]
    dates = sorted(targets)
    nav, cash = 1.0, 1.0
    shares: dict[str, float] = {}
    legs: list[Leaps] = []
    hist, stats = {}, {"premium": 0.0, "call_payout": 0.0, "spread_cost": 0.0, "leaps_capital": [],
                       "months_paused": 0, "rolls": 0}
    for d0, d1 in zip(dates[:-1], dates[1:]):
        tg = targets[d0]
        w = 1.0 / len(tg)
        r0, iv30 = rf[d0], A * vix[d0]
        dt = (d1 - d0).days / 365
        # ---------------- equity leg: LEAPS or shares
        if cfg.use_leaps:
            keep = []
            for L in legs:
                S = raw.at[d0, L.u]
                T = (L.expiry - d0).days / 365
                mid = ol.call_price(S, L.K, T, r0, q.at[d0, L.u], iv30, (rel.at[d0, L.u], rl.at[d0, L.u]))
                dl = ol.call_delta(S, L.K, T, r0, q.at[d0, L.u], iv30, (rel.at[d0, L.u], rl.at[d0, L.u]))
                expo = L.n * dl * S / (cfg.lam * w * nav)
                if L.u in tg and T > cfg.roll_at and 0.6 < dl < 0.97 and 1 / cfg.drift_band < expo < cfg.drift_band:
                    keep.append(L)
                else:
                    bid = cost["leaps"].sell(mid)
                    cash += L.n * bid
                    stats["spread_cost"] += L.n * (mid - bid) / nav
                    stats["rolls"] += 1
            legs = keep
            for u in tg:
                if any(L.u == u for L in legs):
                    continue
                S, qq, rr = raw.at[d0, u], q.at[d0, u], (rel.at[d0, u], rl.at[d0, u])
                K = ol.strike_for_delta(S, cfg.leaps_tenor, r0, qq, iv30, cfg.leaps_delta, rr)
                mid = ol.call_price(S, K, cfg.leaps_tenor, r0, qq, iv30, rr)
                dl = ol.call_delta(S, K, cfg.leaps_tenor, r0, qq, iv30, rr)
                n = cfg.lam * w * nav / (dl * S)
                ask = cost["leaps"].buy(mid)
                n = min(n, 0.95 * max(cash, 0) / ask)   # never borrow to buy LEAPS
                cash -= n * ask
                stats["spread_cost"] += n * (ask - mid) / nav
                legs.append(Leaps(u, n, K, d0 + pd.Timedelta(days=int(cfg.leaps_tenor * 365))))
            exposure = {L.u: L.n for L in legs}
        else:
            # shares: rebalance to target weights at month end
            value = {u: shares.get(u, 0.0) * adj.at[d0, u] for u in shares}
            for u in list(shares):
                if u not in tg:
                    cash += value[u] * (1 - 0.0005)
                    del shares[u]
            for u in tg:
                if u not in shares:
                    amt = w * nav
                    shares[u] = amt * (1 - 0.0005) / adj.at[d0, u]
                    cash -= amt
            exposure = {u: shares[u] * adj.at[d0, u] / raw.at[d0, u] for u in shares}  # in raw-share units
        stats["leaps_capital"].append(
            sum(L.n * ol.call_price(raw.at[d0, L.u], L.K, (L.expiry - d0).days / 365, r0, q.at[d0, L.u], iv30,
                                    (rel.at[d0, L.u], rl.at[d0, L.u])) for L in legs) / nav if cfg.use_leaps else 0)
        # ---------------- short calls
        shorts = []
        paused = cfg.pause_in_spike and spike(m, d0)
        stats["months_paused"] += int(cfg.sell_calls and paused)
        if cfg.sell_calls and not paused:
            for u, n in exposure.items():
                S, qq, rr = raw.at[d0, u], q.at[d0, u], (rel.at[d0, u], rl.at[d0, u])
                Ks = ol.strike_for_delta(S, dt, r0, qq, iv30, cfg.short_delta, rr)
                mid = ol.call_price(S, Ks, dt, r0, qq, iv30, rr)
                bid = cost["short"].sell(mid)
                cash += n * bid
                stats["premium"] += n * bid / nav
                stats["spread_cost"] += n * (mid - bid) / nav
                shorts.append((u, n, Ks))
        # ---------------- month passes
        cash *= np.exp(rf[d0] * dt)
        for u, n, Ks in shorts:
            pay = max(raw.at[d1, u] - Ks, 0.0)
            if pay > 0:
                pay += cost["short"].min_half_spread + cost["short"].commission
            cash -= n * pay
            stats["call_payout"] += n * pay / nav
        r1, iv1 = rf[d1], A * vix[d1]
        eq = sum(L.n * ol.call_price(raw.at[d1, L.u], L.K, (L.expiry - d1).days / 365, r1, q.at[d1, L.u], iv1,
                                     (rel.at[d1, L.u], rl.at[d1, L.u])) for L in legs) if cfg.use_leaps else \
            sum(shares[u] * adj.at[d1, u] for u in shares)
        new_nav = cash + eq
        hist[d1] = new_nav / nav - 1
        nav = new_nav
        if nav <= 0.01:
            break
    years = len(hist) / 12
    extra = {"premium_yield": stats["premium"] / years, "call_payout_yield": stats["call_payout"] / years,
             "avg_capital_in_options": float(np.mean(stats["leaps_capital"])), "rolls_per_year": stats["rolls"] / years, "spread_cost_yield": stats["spread_cost"] / years,
             "pct_months_paused": stats["months_paused"] / max(len(hist), 1)}
    return pd.Series(hist), extra


def run_universe(m, name, targets, sector):
    rows, series = [], {}
    base = dict(shares=Cfg(use_leaps=False, sell_calls=False),
                covered_call=Cfg(use_leaps=False),
                leaps=Cfg(sell_calls=False),
                pmcc=Cfg(),
                pmcc_pause=Cfg(pause_in_spike=True))
    grid = {}
    for ld, ten, sd, pz in itertools.product([0.70, 0.80, 0.90], [1.0, 1.5, 2.0], [0.15, 0.25, 0.35], [False, True]):
        grid[f"pmcc_ld{ld}_t{ten}_sd{sd}_{'pause' if pz else 'always'}"] = Cfg(
            leaps_delta=ld, leaps_tenor=ten, short_delta=sd, pause_in_spike=pz, roll_at=min(0.5, ten / 2))
    grid["pmcc_wideband"] = Cfg(drift_band=2.0)
    if sector:
        grid["leaps_tightcost"] = Cfg(sell_calls=False, cost_key="SECTOR_TIGHT", drift_band=2.0)
        grid["pmcc_tightcost"] = Cfg(cost_key="SECTOR_TIGHT", drift_band=2.0)
        grid["covered_call_tightcost"] = Cfg(use_leaps=False, cost_key="SECTOR_TIGHT")
    grid["leaps_wideband"] = Cfg(sell_calls=False, drift_band=2.0)
    for lam in [0.5, 1.5, 2.0]:
        grid[f"pmcc_pause_lam{lam}"] = Cfg(lam=lam, pause_in_spike=True)
        grid[f"leaps_lam{lam}"] = Cfg(lam=lam, sell_calls=False)
    for key, cfg in (base | grid).items():
        r, ex = simulate(m, targets, cfg, sector)
        p = ol.perf(r, 12, m["rf"].resample("ME").last().reindex(r.index) / 12)
        rows.append({"universe": name, "strategy": key, **p, **ex})
        series[f"{name}|{key}"] = r
        if key in base:
            print(f"{name:9s} {key:14s} cagr {p['cagr']:.3f} vol {p['ann_vol']:.3f} sharpe {p['sharpe']:.2f} "
                  f"dd {p['max_dd']:.2f} prem {ex['premium_yield']:.3f} payout {ex['call_payout_yield']:.3f} "
                  f"cap {ex['avg_capital_in_options']:.2f} rolls {ex['rolls_per_year']:.1f} spread {ex['spread_cost_yield']:.3f}")
    return rows, series


def main():
    m = ol.load_market()
    dates = [d for d in month_ends(m["raw"].index) if d >= pd.Timestamp("1996-09-01")]
    spy_t = {d: ["SPY"] for d in dates}
    md = [d for d in dates if d >= pd.Timestamp("2000-12-01")]
    mom_t = momentum_targets(m, md)
    mom_slow = momentum_targets(m, md, buffer=6, every=3)   # lower-turnover rotation for options
    rows, series = [], {}
    for name, tg, sec in [("SPY", spy_t, False), ("MOMENTUM", mom_t, True), ("MOMENTUM_SLOW", mom_slow, True)]:
        r, s = run_universe(m, name, tg, sec)
        rows += r
        series |= s
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "L_leaps_results.csv", index=False)
    pd.DataFrame(series).to_csv(OUT / "L_leaps_series.csv")
    pd.Series({str(k.date()): v for k, v in mom_t.items()}).to_json(OUT / "L_momentum_holdings.json", indent=1)


if __name__ == "__main__":
    main()
