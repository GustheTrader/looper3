"""
FINAL CAPITAL-EFFICIENCY LAYER -- leverage with the deck's 70% margin guardrail enforced.

Two changes suggested by study_margin.py:
  1. Hold BTC/ETH through CME micro futures instead of spot: margin ~35% of notional instead
     of 100% (crypto is outside portfolio margin), at a cost: futures usually trade above spot,
     charged here as 6% a year on the crypto notional ("basis").
  2. Leverage target L on the whole 65/35 portfolio, automatically cut each day so that the
     previous day's margin use stays at or below 70% (the PM guardrail in the PTG and PM decks).
Rule: no calendar year worse than -15%. Chosen by Calmar on 2015-10 .. 2020-12; tested 2021+.
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
import study_margin as sm  # noqa: E402

OUT = HERE / "results"


def unit_util(D, vix, crypto_margin):
    fixed = {"w_btc_tilt": crypto_margin, "w_eth_tilt": crypto_margin, "w_trend_hard": 0.08, "w_psc_gld": 0.06}
    core = sum(D[c] * v for c, v in fixed.items())
    hc = pd.Series([sm.haircut(v, False) for v in vix.values], index=D.index)
    core = core + (D.w_hard_rotation + D.w_space) * hc
    return 0.65 * core + sm.TRADING_MARGIN


def run(D, vix, L_tgt, futures, cap=0.70):
    rf = D.rf
    u1 = unit_util(D, vix, 0.35 if futures else 1.0)
    L = np.minimum(L_tgt, cap / u1.shift(1).bfill())
    crypto_notional = 0.65 * (D.w_btc_tilt + D.w_eth_tilt)
    drag = (L * crypto_notional * 0.06 / 252) if futures else 0.0
    r = rf + L * (D.portfolio - rf) - (L - 1).clip(lower=0) * 0.005 / 252 - drag
    return r, L, L * u1


def main():
    D = pd.read_csv(OUT / "CF_daily.csv", index_col=0, parse_dates=True)
    m = ol.load_market()
    vix = m["vix"].reindex(D.index).ffill()
    rf = D.rf
    rows, keep = [], {}
    for futures in [False, True]:
        for L in [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]:
            r, lev, util = run(D, vix, L, futures)
            key = f"target {L}x | crypto via {'futures' if futures else 'spot'}"
            row = {"key": key, "target": L, "futures": futures, "avg_lev": float(lev.mean()), "min_lev": float(lev.min()),
                   "avg_util": float(util.mean()), "max_util": float(util.max())}
            for per, sl in {"design": slice(None, "2020-12-31"), "test": slice("2021-01-01", None), "full": slice(None, None)}.items():
                row |= {f"{per}_{k}": v for k, v in sm.metrics(r.loc[sl], rf.loc[sl]).items()}
            rows.append(row)
            keep[key] = (r, lev, util)
    g = pd.DataFrame(rows)
    ok = g[g.design_worst_year >= -0.15]
    chosen = ok.sort_values("design_calmar", ascending=False).iloc[0]["key"]
    # also the highest-CAGR configuration that meets the -15% rule in every year of the full sample
    okf = g[g.full_worst_year >= -0.15]
    max_cagr = okf.sort_values("full_cagr", ascending=False).iloc[0]["key"]
    r, lev, util = keep[max_cagr]
    yr = (1 + r).groupby(r.index.year).prod() - 1
    out = {"chosen_by_design_calmar": chosen, "max_cagr_meeting_rule": max_cagr, "rows": g.to_dict("records"),
           "yearly_max_cagr": {int(a): float(b) for a, b in yr.items()},
           "lev_by_year_max_cagr": {int(a): float(b) for a, b in lev.groupby(lev.index.year).mean().items()}}
    (OUT / "FL_summary.json").write_text(json.dumps(out, indent=1, default=float))
    pd.DataFrame({"base_65_35": D.portfolio, "max_cagr_layer": r, "leverage": lev, "margin_util": util,
                  "calmar_pick": keep[chosen][0]}).to_csv(OUT / "FL_daily.csv")
    pd.set_option("display.width", 260, "display.float_format", "{:.3f}".format)
    print(g[["key", "avg_lev", "avg_util", "max_util", "design_calmar", "design_worst_year", "test_cagr", "test_max_dd", "test_calmar",
             "test_worst_year", "full_cagr", "full_max_dd", "full_calmar", "full_worst_year", "full_sharpe"]].to_string(index=False))
    print("chosen by design Calmar:", chosen, "| max CAGR meeting -15% rule:", max_cagr)
    print({a: round(b, 3) for a, b in out["yearly_max_cagr"].items()})
    print({a: round(b, 2) for a, b in out["lev_by_year_max_cagr"].items()})


if __name__ == "__main__":
    main()
