"""
TRADING-SLEEVE WEIGHTS -- chosen on 2015-10 .. 2020-12 ONLY, then reported on the untouched 2021-26 test.

The 65% core is fixed. Candidate splits of the 35% trading bucket between the VIX fade, funding carry,
FF calendars (deck replay, MASTER execution), momentum L/S and the prediction pilot (held in T-bills).
Rule: highest 2015-20 Calmar (monthly CAGR / max drawdown). Writes results/WEIGHTS_choice.json, which
study_portfolio.py reads. The decay sleeve is not a candidate (it failed its own out-of-sample test).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "results"
FF_KEY = "deck core: SPY QQQ IWM DIA | deck rules | master"
CANDIDATES = {
    "W0 current (no FF)":            {"vix_fade": 0.15, "carry": 0.10, "momentum_ls": 0.05, "prediction": 0.05, "ff_calendars": 0.00},
    "W1 FF 5% for prediction":       {"vix_fade": 0.15, "carry": 0.10, "momentum_ls": 0.05, "prediction": 0.00, "ff_calendars": 0.05},
    "W2 FF 5%, carry 5%":            {"vix_fade": 0.15, "carry": 0.05, "momentum_ls": 0.05, "prediction": 0.05, "ff_calendars": 0.05},
    "W3 FF 10%, carry 5%":           {"vix_fade": 0.15, "carry": 0.05, "momentum_ls": 0.05, "prediction": 0.00, "ff_calendars": 0.10},
    "W4 FF 10%, fade 10%":           {"vix_fade": 0.10, "carry": 0.10, "momentum_ls": 0.05, "prediction": 0.00, "ff_calendars": 0.10},
    "W5 FF 5%, fade 10%":            {"vix_fade": 0.10, "carry": 0.10, "momentum_ls": 0.05, "prediction": 0.05, "ff_calendars": 0.05},
}


def mstats(r, rfm):
    eq = (1 + r).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    cagr = float(eq.iloc[-1] ** (12 / len(r)) - 1)
    ex = r - rfm.reindex(r.index)
    yr = (1 + r).groupby(r.index.year).prod() - 1
    return {"cagr": cagr, "max_dd": dd, "calmar": cagr / -dd, "sharpe": float(ex.mean() / ex.std() * np.sqrt(12)), "worst_year": float(yr.min())}


def main():
    D = pd.read_csv(OUT / "CF_daily.csv", index_col=0, parse_dates=True)
    ff = pd.read_csv(OUT / "FFA_daily.csv", index_col=0, parse_dates=True)[FF_KEY].reindex(D.index)
    S = D[["core", "vix_fade", "carry", "momentum_ls", "prediction", "rf"]].copy()
    S["ff_calendars"] = ff.fillna(D.rf)
    rfm = (1 + S.rf).resample("ME").prod() - 1
    rows = []
    for name, w in CANDIDATES.items():
        assert abs(sum(w.values()) - 0.35) < 1e-12, name
        port = 0.65 * S.core + sum(v * S[k] for k, v in w.items())
        m = (1 + port).resample("ME").prod() - 1
        rows.append({"name": name, **w, **{f"design_{k}": v for k, v in mstats(m.loc[:"2020-12-31"], rfm).items()},
                     **{f"test_{k}": v for k, v in mstats(m.loc["2021-01-01":], rfm).items()}})
    g = pd.DataFrame(rows).set_index("name")
    pick = g.design_calmar.idxmax()
    w = {"core": 0.65, **{k: float(g.loc[pick, k]) for k in ["vix_fade", "carry", "ff_calendars", "momentum_ls", "prediction"]}}
    (OUT / "WEIGHTS_choice.json").write_text(json.dumps({"chosen": pick, "weights": w, "ff_series": FF_KEY,
                                                         "rule": "highest 2015-20 monthly Calmar", "candidates": g.reset_index().to_dict("records")}, indent=1))
    pd.set_option("display.width", 250, "display.float_format", "{:.3f}".format)
    print(g[["design_cagr", "design_max_dd", "design_calmar", "test_cagr", "test_max_dd", "test_calmar", "test_sharpe", "test_worst_year"]])
    print("chosen on 2015-20:", pick, w)


if __name__ == "__main__":
    main()
