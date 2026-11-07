"""
CAPITAL-EFFICIENCY LAYER -- where to spend the extra buying power from portfolio margin.

study_margin.py showed that adding leverage on top of the whole 65/35 portfolio lowers
the out-of-sample Calmar, because the core is already levered inside (40% volatility
target) and crypto must be fully funded. This script spends the extra capacity on the
low-volatility, low-correlation trading sleeves instead, with a hard cap on short-VIX
exposure (at most 10% of the account, so a day when VIX futures double costs <= 10%).

Variants (65% core / 35% trading kept; trading capital levered k times, borrowing at T-bills + 0.5%):
  k = 1, 1.5, 2, 2.5, 3 for momentum, FF calendars and the prediction pilot;
  VIX spike fade levered at most 4/3 (0.15 x 4/3 x 0.5 = 10% of the account short VIX futures).
Rule: no calendar year worse than -15%. Chosen by Calmar on 2015-10 .. 2020-12, tested on 2021+.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import study_margin as sm

HERE = Path(__file__).parent
OUT = HERE / "results"
W = {"vix_fade": 0.15, "ff_calendars": 0.10, "momentum_ls": 0.05, "prediction": 0.05}


def build(D, k, fade_cap=4 / 3):
    rf = D.rf
    lev = lambda x, L: rf + L * (D[x] - rf) - max(L - 1, 0) * 0.005 / 252
    trading = sum(W[s] * lev(s, min(k, fade_cap) if s == "vix_fade" else k) for s in W)
    return 0.65 * D.core + trading


def main():
    D = pd.read_csv(OUT / "CF_daily.csv", index_col=0, parse_dates=True)
    rf = D.rf
    rows, series = [], {}
    for k in [1.0, 1.5, 2.0, 2.5, 3.0]:
        for cap_lab, cap in [("fade capped (VIX <= 10% of account)", 4 / 3), ("fade levered too", 99)]:
            r = build(D, k, cap)
            key = f"trading x{k} | {cap_lab}"
            row = {"key": key, "k": k, "fade_capped": cap < 10,
                   "short_vix_notional": 0.15 * min(k, cap) * 0.5}
            for per, sl in {"design": slice(None, "2020-12-31"), "test": slice("2021-01-01", None), "full": slice(None, None)}.items():
                row |= {f"{per}_{m}": v for m, v in sm.metrics(r.loc[sl], rf.loc[sl]).items()}
            rows.append(row)
            series[key] = r
    g = pd.DataFrame(rows)
    ok = g[(g.design_worst_year >= -0.15) & (g.short_vix_notional <= 0.10 + 1e-9)]
    chosen = ok.sort_values("design_calmar", ascending=False).iloc[0]["key"]
    r = series[chosen]
    yr = (1 + r).groupby(r.index.year).prod() - 1
    base = series["trading x1.0 | fade capped (VIX <= 10% of account)"]
    out = {"chosen": chosen, "rows": g.to_dict("records"),
           "yearly_chosen": {int(a): float(b) for a, b in yr.items()},
           "yearly_base": {int(a): float(b) for a, b in ((1 + base).groupby(base.index.year).prod() - 1).items()}}
    (OUT / "CL_summary.json").write_text(json.dumps(out, indent=1, default=float))
    pd.DataFrame({"base_65_35": base, "capital_layer": r}).to_csv(OUT / "CL_daily.csv")
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(g[["key", "short_vix_notional", "design_cagr", "design_calmar", "test_cagr", "test_max_dd", "test_calmar", "test_worst_year",
             "full_cagr", "full_max_dd", "full_calmar", "full_worst_year", "full_sharpe"]].to_string(index=False))
    print("chosen:", chosen)
    print({a: round(b, 3) for a, b in out["yearly_chosen"].items()})


if __name__ == "__main__":
    main()
