"""Render the report charts from ./results into ./charts (PNG, light theme)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

HERE = Path(__file__).parent
RES, CH = HERE / "results", HERE / "charts"
CH.mkdir(exist_ok=True)

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"
DIVERGE = LinearSegmentedColormap.from_list("bgr", ["#e34948", "#f0efec", "#2a78d6"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
})


def lookback_plateau():
    g = pd.read_csv(RES / "B1_sector_grid.csv")
    ls = g[g["mode"] == "long_short"].groupby(["universe", "L"])["score"].mean().unstack(0)
    names = {"ind12_m": "French 12 industries", "ind49_m": "French 49 industries", "spdr_etf": "SPDR sector ETFs"}
    fig, ax = plt.subplots(figsize=(8, 4.4))
    for (col, label), c in zip(names.items(), [S1, S2, S3]):
        y = ls[col]
        ax.plot(y.index, y.values, color=c, lw=2, marker="o", ms=5, label=label)
        ax.annotate(label, (y.index[-1], y.values[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", color=INK2, fontsize=9)
    ax.axvspan(6, 12, color=GRID, alpha=0.5, lw=0, zorder=0)
    ax.text(9, -0.06, "6–12 month plateau", ha="center", va="center", color=INK2, fontsize=9)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xticks(ls.index)
    ax.set_xlim(0.5, 30)
    ax.set_xlabel("Lookback window (months)")
    ax.set_ylabel("Average net Sharpe across all other settings")
    ax.set_title("Sector long/short momentum: every universe peaks near a 9-month window")
    ax.legend(frameon=False, loc="upper right", ncol=1, fontsize=9)
    fig.tight_layout()
    fig.savefig(CH / "1_sector_lookback_plateau.png", dpi=160)


def stock_heatmap():
    g = pd.read_csv(RES / "A3_stock_window_grid.csv")
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.6), sharey=True)
    for ax, S in zip(axes, [0, 1]):
        p = g[g.S == S].pivot(index="L", columns="H", values="sharpe_net")
        im = ax.imshow(p.values, cmap=DIVERGE, vmin=-0.4, vmax=0.4, aspect="auto")
        for i in range(p.shape[0]):
            for j in range(p.shape[1]):
                ax.text(j, i, f"{p.values[i, j]:.2f}", ha="center", va="center", fontsize=8, color=INK)
        ax.set_xticks(range(p.shape[1]), p.columns)
        ax.set_yticks(range(p.shape[0]), p.index)
        ax.set_xlabel("Holding period (months)")
        ax.set_title(f"Skip {S} month{'s' if S != 1 else ''}", fontsize=10)
        ax.grid(False)
    axes[0].set_ylabel("Lookback (months)")
    fig.suptitle("S&P 500 stock long/short (top vs bottom decile): net Sharpe by window, 1996–2026",
                 x=0.02, ha="left", fontweight="bold")
    cb = fig.colorbar(im, ax=axes, shrink=0.8)
    cb.outline.set_visible(False)
    cb.set_label("Net Sharpe")
    fig.savefig(CH / "2_stock_window_heatmap.png", dpi=160, bbox_inches="tight")


def selection_methods():
    t = pd.read_csv(RES / "C_selection_table.csv")
    fams = {
        "market_trend_long_cash": "Market trend (long / cash)",
        "stock_LS_sp500": "Stock long/short (S&P 500)",
        "concentrated_stock_LS": "Sector-concentrated stock L/S",
        "sector_ind49_long_short": "Sector L/S (49 industries)",
        "sector_ind12_long_only": "Top-2 sectors long-only (vs market)",
        "sector_spdr_long_only": "Top-2 SPDR ETFs long-only (vs SPY)",
    }
    order = ["in_sample_best", "wf_grid", "wf_lookback", "fixed_12", "ensemble", "ens_guarded"]
    labels = ["In-sample best (overfit)", "Walk-forward: full grid", "Walk-forward: lookback only",
              "Fixed 12 months", "Ensemble 6/9/12", "Ensemble + crash guards"]
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.6), sharex=True)
    for ax, (fam, title) in zip(axes.flat, fams.items()):
        d = t[t.family == fam].set_index("method").reindex(order)
        colors = [GRID if m == "in_sample_best" else (S1 if m == "ens_guarded" else "#9ec5f4") for m in order]
        ax.barh(range(len(order)), d["sharpe"], color=colors, height=0.7)
        for i, v in enumerate(d["sharpe"]):
            ax.text(v + (0.02 if v >= 0 else -0.02), i, f"{v:.2f}", va="center",
                    ha="left" if v >= 0 else "right", fontsize=8, color=INK2)
        ax.set_yticks(range(len(order)), labels if ax in axes[:, 0] else [""] * len(order), fontsize=8.5)
        ax.invert_yaxis()
        ax.axvline(0, color=INK2, lw=0.8)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="y", visible=False)
        ax.set_xlim(-0.35, 0.95)
    fig.suptitle("How you choose the window matters more than which window: out-of-sample Sharpe, 2001-10 → 2026",
                 x=0.01, ha="left", fontweight="bold")
    fig.supxlabel("Net Sharpe (long-only panels: information ratio vs market)", color=INK2, fontsize=9)
    fig.tight_layout()
    fig.savefig(CH / "3_window_selection_methods.png", dpi=160)


def equity_curves():
    c = pd.read_csv(RES / "C_selection_series.csv", index_col=0, parse_dates=True)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
    for ax, fam, title in [(axes[0], "sector_ind49_long_short", "Sector L/S, 49 industries"),
                           (axes[1], "concentrated_stock_LS", "Sector-concentrated stock L/S")]:
        for m, lab, col in [("wf_grid", "Walk-forward full grid", S2), ("ensemble", "Ensemble 6/9/12", S3),
                            ("ens_guarded", "Ensemble + crash guards", S1)]:
            s = c[f"{fam}|{m}"].dropna()
            eq = (1 + s).cumprod()
            ax.plot(eq.index, eq.values, lw=2, color=col, label=lab)
            ax.annotate(f"{lab}  {eq.iloc[-1]:.2f}x", (eq.index[-1], eq.iloc[-1]), xytext=(4, 0),
                        textcoords="offset points", va="center", fontsize=8, color=INK2)
        ax.set_yscale("log")
        from matplotlib.ticker import FuncFormatter, LogLocator
        ax.yaxis.set_major_locator(LogLocator(subs=(1, 2, 5)))
        ax.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:g}"))
        ax.set_title(title, fontsize=10)
        ax.set_ylabel("Growth of $1 (log scale)")
        ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.suptitle("Crash guards turn momentum from a −80% ride into a usable return stream (net of costs)",
                 x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(CH / "4_equity_curves.png", dpi=160, bbox_inches="tight")


def live_ratings():
    s = json.loads((RES / "B3_live_scan.json").read_text())
    d = pd.Series({k: v["spsr"] for k, v in s["spdr_etf_spsr"].items()}).sort_values()
    fig, ax = plt.subplots(figsize=(7, 4.2))
    cols = [S1 if v >= 75 else ("#e34948" if v <= 25 else "#b7d3f6") for v in d.values]
    ax.barh(d.index, d.values, color=cols, height=0.7)
    for i, v in enumerate(d.values):
        ax.text(v + 1.5, i, f"{v:.0f}", va="center", fontsize=8, color=INK2)
    ax.set_xlim(0, 110)
    ax.set_xlabel("Sector Power Strength Rating (0–100, 9-month window)")
    ax.set_title(f"Live sector scan, SPDR ETFs, as of {s['as_of']}")
    ax.grid(axis="y", visible=False)
    ax.set_title(f"Live sector scan, SPDR ETFs, as of {s['as_of']}\n", loc="left")
    ax.text(0, 1.02, "Blue = long candidates (top quartile) · red = short candidates (bottom quartile)",
            transform=ax.transAxes, fontsize=8, color=INK2)
    fig.tight_layout()
    fig.savefig(CH / "5_live_sector_ratings.png", dpi=160)


if __name__ == "__main__":
    lookback_plateau()
    stock_heatmap()
    selection_methods()
    equity_curves()
    live_ratings()
    print("charts written to", CH)


def two_eras():
    """Lookback plateau in the untouched 1931-1996 era vs the 1996-2026 study era."""
    new = pd.read_csv(RES / "B1_sector_grid.csv")
    old = pd.read_csv(RES / "D1_pre1996_sector_grid.csv")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, u, title in [(axes[0], "ind12_m", "French 12 industries"), (axes[1], "ind49_m", "French 49 industries")]:
        for df, lab, c in [(old, "1931–1996 (never used to pick the window)", S2), (new, "1996–2026 (study period)", S1)]:
            y = df[(df.universe == u) & (df["mode"] == "long_short")].groupby("L")["score"].mean()
            ax.plot(y.index, y.values, color=c, lw=2, marker="o", ms=5, label=lab)
        ax.axvspan(6, 12, color=GRID, alpha=0.5, lw=0, zorder=0)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.set_xticks(y.index)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Lookback window (months)")
    axes[0].set_ylabel("Average net Sharpe, sector long/short")
    axes[0].legend(frameon=False, fontsize=8.5, loc="lower center")
    fig.suptitle("Out-of-sample check: the 6–12 month sector window also wins over the prior 65 years",
                 x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(CH / "6_two_eras_plateau.png", dpi=160)


def weekly_lookbacks():
    g = pd.read_csv(RES / "D2_weekly_sector_grid.csv")
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for u, lab, c in [("ind49_weekly", "French 49 industries (1996–2026)", S2), ("spdr_weekly", "SPDR sector ETFs (2001–2026)", S3)]:
        y = g[g.universe == u].groupby("L_weeks")["sharpe"].mean()
        ax.plot(y.index, y.values, color=c, lw=2, marker="o", ms=5, label=lab)
        ax.annotate(lab.split(" (")[0], (y.index[-1], y.values[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=INK2)
    ax.axvspan(39, 52, color=GRID, alpha=0.5, lw=0, zorder=0)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 4, 8, 13, 26, 39, 52, 78, 104], ["1", "2", "4", "8", "13", "26", "39", "52", "78", "104"])
    ax.minorticks_off()
    ax.set_xlim(0.8, 220)
    ax.set_xlabel("Lookback window (weeks, log scale)")
    ax.set_ylabel("Average net Sharpe, weekly rebalance")
    ax.set_title("Weekly rebalancing: short windows lose, the best is again 39–52 weeks (9–12 months)")
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    fig.tight_layout()
    fig.savefig(CH / "7_weekly_lookbacks.png", dpi=160)


if __name__ == "__main__":
    two_eras()
    weekly_lookbacks()
