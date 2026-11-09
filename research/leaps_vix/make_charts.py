"""Charts for PAPER.md (light theme, same style as research/momentum)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter, LogLocator, PercentFormatter

HERE = Path(__file__).parent
RES, CH = HERE / "results", HERE / "charts"
CH.mkdir(exist_ok=True)
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
})


def logy(ax):
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(LogLocator(subs=(1, 2, 5)))
    ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:g}"))


def curves(ax, series: dict, colors, end_labels=True):
    for (lab, r), c in zip(series.items(), colors):
        eq = (1 + r.dropna()).cumprod()
        ax.plot(eq.index, eq.values, color=c, lw=2, label=lab)
        if end_labels:
            ax.annotate(f"{lab}  ${eq.iloc[-1]:.1f}", (eq.index[-1], eq.iloc[-1]), xytext=(4, 0),
                        textcoords="offset points", va="center", fontsize=8, color=INK2)
    logy(ax)


def calibration():
    p = pd.read_csv(RES / "calibration_putwrite.csv", index_col=0, parse_dates=True).dropna()
    v = pd.read_csv(RES / "calibration_vixy.csv", index_col=0, parse_dates=True).dropna()
    c = json.loads((RES / "calibration.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    curves(axes[0], {"CBOE ^PUT (real)": p.put_actual, "Model": p.put_sim}, [S2, S1])
    axes[0].set_title(f"Option model vs real put-write index, 1996–2026 (monthly corr {c['putwrite_fit']['monthly_corr']:.2f})")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    curves(axes[1], {"VIXY ETF (real)": v.vixy_real, "Model": v.vix_model}, [S2, S1], end_labels=False)
    axes[1].set_title(f"VIX-futures model vs real VIXY, 2011–2026 (daily corr {c['vix_futures_check']['daily_corr']:.2f})")
    axes[1].legend(frameon=False, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(CH / "1_calibration.png", dpi=160, bbox_inches="tight")


def leaps_curves():
    s = pd.read_csv(RES / "L_leaps_series.csv", index_col=0, parse_dates=True)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
    curves(axes[0], {"Shares": s["SPY|shares"], "LEAPS only": s["SPY|leaps"],
                     "LEAPS + short calls": s["SPY|pmcc"]}, [S2, S3, S1], end_labels=False)
    axes[0].set_title("SPY, 1996–2026: same exposure, ~28% of the capital")
    ends = {k: (1 + s[f"SPY|{k}"].dropna()).prod() for k in ["shares", "leaps", "pmcc"]}
    axes[0].text(0.98, 0.04, f"Ending value of $1: shares ${ends['shares']:.1f} · LEAPS ${ends['leaps']:.1f} · "
                 f"LEAPS+calls ${ends['pmcc']:.1f}", transform=axes[0].transAxes, ha="right", fontsize=8, color=INK2)
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    curves(axes[1], {"Shares": s["MOMENTUM|shares"], "LEAPS only": s["MOMENTUM|leaps"],
                     "LEAPS + short calls": s["MOMENTUM|pmcc"]}, [S2, S3, S1])
    axes[1].set_title("Top-2 momentum sectors, 2001–2026: options lose the edge")
    axes[1].legend(frameon=False, fontsize=8, loc="upper left")
    for ax in axes:
        ax.set_ylabel("Growth of $1 (log)")
    fig.tight_layout()
    fig.savefig(CH / "2_leaps_equity_curves.png", dpi=160, bbox_inches="tight")


def vix_sleeves():
    sig = pd.read_csv(RES / "V_signals_daily.csv", index_col=0, parse_dates=True)
    mon = pd.read_csv(RES / "V_monthly.csv", index_col=0, parse_dates=True)
    fig, axes = plt.subplots(2, 1, figsize=(12, 6.4), sharex=True, gridspec_kw={"height_ratios": [1.1, 1]})
    ax = axes[0]
    ax.plot(sig.index, sig.vix * 100, color=INK2, lw=1, label="VIX")
    on = sig.V3_signal.astype(bool)
    ax.fill_between(sig.index, 0, 90, where=on, color=S3, alpha=0.35, lw=0, label="Spike-fade position on")
    ax.set_ylim(0, 90)
    ax.set_ylabel("VIX")
    ax.set_title("The spike-fade rule only shorts volatility AFTER a spike has peaked (green)")
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    curves(axes[1], {"Spike fade (short after peak)": mon.V3_spike_fade_short,
                     "Long vol when VIX > VIX3M": mon.V1_backwardation_long}, [S3, S2])
    axes[1].set_title("Fading the spike earns; chasing it bleeds (sleeve returns, net)")
    axes[1].legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(CH / "3_vix_spike_strategies.png", dpi=160, bbox_inches="tight")


def frontier():
    g = pd.read_csv(RES / "A_allocation_grid.csv")
    b = json.loads((RES / "A_allocation_summary.json").read_text())["benchmarks"]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    tail = g[g.tail_budget_m > 0]
    notail = g[g.tail_budget_m == 0]
    ax.scatter(-notail.max_dd, notail.cagr, s=22, color="#86b6ef", edgecolor="none", label="No tail hedge (24 mixes)")
    ax.scatter(-tail.max_dd, tail.cagr, s=22, color="#cfcdc6", edgecolor="none", label="With VIX-call tail hedge (72 mixes)")
    picks = {"Conservative": "lam0.75_tail0.0_fade0.2_noboost", "Balanced": "lam1.0_tail0.0_fade0.2_boost",
             "Aggressive": "lam1.25_tail0.0_fade0.2_boost"}
    for lab, k in picks.items():
        r = g[g.key == k].iloc[0]
        ax.scatter(-r.max_dd, r.cagr, s=70, color=S1, edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.annotate(lab, (-r.max_dd, r.cagr), xytext=(8, -2), textcoords="offset points", fontsize=9, color=INK)
    for lab, k in [("SPY shares", "spy_shares"), ("SPY covered call", "spy_covered_call")]:
        ax.scatter(-b[k]["max_dd"], b[k]["cagr"], s=70, color=S2, edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.annotate(lab, (-b[k]["max_dd"], b[k]["cagr"]), xytext=(8, -2), textcoords="offset points", fontsize=9)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    from matplotlib.ticker import MultipleLocator
    ax.yaxis.set_major_locator(MultipleLocator(0.02))
    ax.set_xlabel("Worst peak-to-trough loss (max drawdown)")
    ax.set_ylabel("Compound annual return")
    ax.set_title("Capital allocation, 2007–2026: up-and-left is better")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(CH / "4_allocation_frontier.png", dpi=160, bbox_inches="tight")


def recommended_curves():
    s = pd.read_csv(RES / "A_allocation_series.csv", index_col=0, parse_dates=True)
    core = pd.read_csv(RES / "A_core_series.csv", index_col=0, parse_dates=True).reindex(s.index)
    ser = {"SPY shares": core.spy_shares, "Conservative": s["lam0.75_tail0.0_fade0.2_noboost"],
           "Balanced": s["lam1.0_tail0.0_fade0.2_boost"], "Aggressive": s["lam1.25_tail0.0_fade0.2_boost"]}
    cols = [S2, S3, S1, S4]
    fig, axes = plt.subplots(2, 1, figsize=(12, 6.6), sharex=True, gridspec_kw={"height_ratios": [1.6, 1]})
    curves(axes[0], ser, cols)
    axes[0].set_title("Recommended mixes vs owning SPY, 2007–2026 (net of costs)")
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")
    for (lab, r), c in zip(ser.items(), cols):
        eq = (1 + r.dropna()).cumprod()
        axes[1].plot(eq.index, eq / eq.cummax() - 1, color=c, lw=1.6, label=lab)
    axes[1].yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    axes[1].set_title("Drawdown")
    fig.tight_layout()
    fig.savefig(CH / "5_recommended_mixes.png", dpi=160, bbox_inches="tight")


if __name__ == "__main__":
    calibration()
    leaps_curves()
    vix_sleeves()
    frontier()
    recommended_curves()
    print("charts ->", CH)
