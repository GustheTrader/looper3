"""
Calibrate the option model to REAL tradable track records before using it.

1. Term structure: fit the long-run level (theta) and mean-reversion speed (k)
   so that VIX (30d) maps to VIX3M (93d), 2006-2026 daily.
2. Level: fit A_VIX (ATM 1-month IV = A_VIX * VIX) so that a simulated monthly
   ATM S&P 500 put-write reproduces the CBOE PutWrite index (^PUT), 1996-2026.
   ^PUT is a real, published option-selling benchmark, so this pins the price
   level of the options we sell.
3. VIX futures: build a 30-day constant-maturity VIX-futures series from the
   model and compare it with the real VIXY ETF (2011-2026).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize

import options_lib as ol

OUT = ol.HERE / "results"
OUT.mkdir(exist_ok=True)


def fit_term(m):
    d = pd.concat([m["vix"], m["vix3m"]], axis=1, keys=["v", "v3"]).dropna()

    # Long-run level anchored to the average 3-month implied vol (a free fit of
    # theta on 30d->93d data extrapolates badly to 1-2 year LEAPS); fit k only.
    th = float(d.v3.mean())

    def loss(p):
        k = p[0]
        g = lambda t: (1 - np.exp(-k * t)) / (k * t)
        pred = th + (d.v - th) * g(93 / 365) / g(30 / 365)
        return ((pred - d.v3) ** 2).mean()

    res = minimize(loss, [3.0], bounds=[(0.2, 15)])
    k = float(res.x[0])
    g = lambda t: (1 - np.exp(-k * t)) / (k * t)
    pred = th + (d.v - th) * g(93 / 365) / g(30 / 365)
    return {"theta_vix": th, "k": k, "rmse_vol_pts": float(np.sqrt(res.fun) * 100),
            "r2": float(1 - ((pred - d.v3) ** 2).sum() / ((d.v3 - d.v3.mean()) ** 2).sum())}


def putwrite_sim(m, A, theta, k):
    ol.PARAMS.update({"A_VIX": A, "THETA": A * theta, "K_MR": k})
    me = m["spx"].dropna().resample("ME").last().index
    dates = [m["spx"].dropna().loc[:d].index[-1] for d in me]
    rets = {}
    for t0, t1 in zip(dates[:-1], dates[1:]):
        S0, S1 = m["spx"][t0], m["spx"][t1]
        r, q, v = m["rf"][t0], m["q"]["SPY"][t0], m["vix"][t0]
        T = (t1 - t0).days / 365
        P = ol.bs_price(S0, S0, T, r, q, ol.iv(S0, S0, T, r, q, A * v), call=False)
        rets[t1] = (P + S0 * (np.exp(r * T) - 1) - max(S0 - S1, 0)) / S0
    return pd.Series(rets)


def main():
    m = ol.load_market()
    term = fit_term(m)
    print("term structure:", term)

    put = m["put"].dropna()
    actual = put.resample("ME").last().pct_change().dropna().loc["1996-09":"2026-10"]
    rows = []
    for A in np.arange(0.80, 1.06, 0.02):
        sim = putwrite_sim(m, A, term["theta_vix"], term["k"]).reindex(actual.index)
        rows.append({"A_VIX": round(A, 2), "sim_ann": sim.mean() * 12, "act_ann": actual.mean() * 12,
                     "corr": sim.corr(actual)})
    grid = pd.DataFrame(rows)
    best = grid.iloc[(grid.sim_ann - grid.act_ann).abs().idxmin()]
    print(grid.round(4).to_string(index=False))
    A = float(best.A_VIX)
    sim = putwrite_sim(m, A, term["theta_vix"], term["k"]).reindex(actual.index)

    # ---- VIX futures model vs VIXY
    d = pd.concat([m["vix"], m["vix3m"], m["vvix"], m["vixy"]], axis=1,
                  keys=["v", "v3", "vv", "vixy"]).dropna().loc["2011-01-04":]
    real = d.vixy.pct_change().iloc[1:]

    def cm_model(kappa):
        ol.PARAMS["VIX_BASIS"] = kappa
        F30 = np.array([ol.vix_future(a, b, c, 30) for a, b, c in zip(d.v, d.v3, d.vv)])
        F29 = np.array([ol.vix_future(a, b, c, 29) for a, b, c in zip(d.v, d.v3, d.vv)])
        return pd.Series(F29[1:] / F30[:-1] - 1, index=d.index[1:]) - 0.0085 / 252, F30  # minus ETF fee

    target = np.log1p(real).mean() * 252
    kgrid = np.round(np.arange(0.2, 1.01, 0.05), 2)
    errs = [abs(np.log1p(cm_model(kk)[0]).mean() * 252 - target) for kk in kgrid]
    kappa = float(kgrid[int(np.argmin(errs))])
    model, F30 = cm_model(kappa)
    vix_check = {
        "daily_corr": float(model.corr(real)),
        "model_ann_log_return": float(np.log1p(model).mean() * 252),
        "vixy_ann_log_return": float(np.log1p(real).mean() * 252),
        "model_ann_vol": float(model.std() * np.sqrt(252)),
        "vixy_ann_vol": float(real.std() * np.sqrt(252)),
        "model_avg_basis_F30_over_vix": float(np.mean(F30 / d.v.values)),
    }
    print("VIX futures model vs VIXY:", vix_check)
    vix_check["VIX_BASIS_fitted"] = kappa

    out = {
        "params": {"A_VIX": A, "THETA": A * term["theta_vix"], "K_MR": term["k"], "VIX_BASIS": kappa},
        "term_structure_fit": term,
        "putwrite_fit": {"A_VIX": A, "sim_ann": float(sim.mean() * 12), "actual_ann": float(actual.mean() * 12),
                         "monthly_corr": float(sim.corr(actual)), "sim_vol": float(sim.std() * np.sqrt(12)),
                         "actual_vol": float(actual.std() * np.sqrt(12)), "grid": grid.to_dict("records")},
        "vix_futures_check": vix_check,
    }
    (OUT / "calibration.json").write_text(json.dumps(out, indent=2, default=float))
    pd.DataFrame({"put_actual": actual, "put_sim": sim}).to_csv(OUT / "calibration_putwrite.csv")
    pd.DataFrame({"vixy_real": real, "vix_model": model}).to_csv(OUT / "calibration_vixy.csv")


if __name__ == "__main__":
    main()
