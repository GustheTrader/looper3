"""
Free data for the LEAPS-income and VIX-spike study (Yahoo Finance via yfinance).

* ^VIX (1990+), ^VIX3M (2006+), ^VVIX (2007+): implied-vol inputs and spike signals
* ^PUT (1996+): CBOE S&P 500 PutWrite index -- a REAL option-selling track record,
  used to calibrate the option-pricing model
* VIXY, SVXY, UVXY (2011+): real tradable VIX-futures funds, used to check the
  VIX-futures model
* SPY + SPDR sector ETFs: raw closes (what option strikes refer to) AND
  dividend-adjusted closes (total return); the gap gives the dividend yield
Output -> ./data (git-ignored).
"""
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
INDEX = ["^VIX", "^VIX3M", "^VVIX", "^PUT", "^GSPC", "VIXY", "SVXY", "UVXY"]
ETFS = ["SPY", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLRE", "XLC"]


def main():
    if not (DATA / "vol_index_d.csv").exists():
        px = yf.download(INDEX, start="1990-01-01", auto_adjust=True, progress=False)["Close"]
        px.to_csv(DATA / "vol_index_d.csv")
    if not (DATA / "etf_raw_d.csv").exists():
        d = yf.download(ETFS, start="1993-01-01", auto_adjust=False, progress=False)
        d["Close"].to_csv(DATA / "etf_raw_d.csv")       # split-adjusted, NOT dividend-adjusted
        d["Adj Close"].to_csv(DATA / "etf_adj_d.csv")   # total-return series
    # growth leadership study: QQQ, mid/small caps, semis, software, Mag 7 + AI leaders
    groups = {"growth_etf": ["QQQ", "MDY", "IJH", "IJR", "IWM", "SMH", "SOXX", "IGV", "SPY"],
              "ai_stocks": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "AMD", "TSM", "ORCL",
                            "PLTR", "ANET", "MU", "SMCI", "ARM", "VRT", "CRWD", "NOW", "DELL"]}
    for name, tick in groups.items():
        if not (DATA / f"{name}_raw_d.csv").exists():
            d = yf.download(tick, start="1993-01-01", auto_adjust=False, progress=False)
            d["Close"].to_csv(DATA / f"{name}_raw_d.csv")
            d["Adj Close"].to_csv(DATA / f"{name}_adj_d.csv")
    if not (DATA / "cross_asset_adj_d.csv").exists():   # goal-25 trend sleeve
        x = yf.download(["TLT", "IEF", "GLD", "DBC", "UUP", "SHY", "HYG", "EFA", "EEM", "SPY", "QQQ"],
                        start="2000-01-01", auto_adjust=True, progress=False)["Close"]
        x.to_csv(DATA / "cross_asset_adj_d.csv")
    if not (DATA / "hard_assets_adj_d.csv").exists():   # Frontier plan: crypto, hard assets, energy, space
        h = yf.download(["BTC-USD", "ETH-USD", "GLD", "SLV", "GDX", "GDXJ", "XME", "COPX", "URA", "XLE", "XOP", "OIH", "USO",
                         "DBC", "DBB", "CPER", "PPLT", "ITA", "XAR", "UFO", "ROKT", "ARKX", "RKLB", "XOVR", "DXYZ", "GOOGL",
                         "MSTR", "COIN", "IBIT", "SPY", "QQQ", "^GVZ", "^OVX", "GBTC"], start="2004-01-01",
                        auto_adjust=True, progress=False)["Close"]
        h.to_csv(DATA / "hard_assets_adj_d.csv")
        s = yf.download("SPCX", start="2026-01-01", auto_adjust=True, progress=False)["Close"]   # SpaceX, listed 2026-06-12
        (s.iloc[:, 0] if hasattr(s, "columns") else s).rename("SPCX").to_frame().to_csv(DATA / "spcx_d.csv")
    apply_cboe_official()
    for f in DATA.glob("*.csv"):
        x = pd.read_csv(f, index_col=0, parse_dates=True)
        print(f.name, x.shape, x.index[0].date(), x.index[-1].date())


def apply_cboe_official():
    """Replace Yahoo's ^VIX / ^VIX3M / ^VVIX closes with CBOE's official daily closes wherever CBOE has the day.
    Yahoo had corrupted rows (e.g. 2026-02-06 ^VIX = 20.37, a copy of that day's VIX3M; CBOE close 17.76)."""
    import io
    import urllib.request
    f = DATA / "vol_index_d.csv"
    vol = pd.read_csv(f, index_col=0, parse_dates=True)
    log = []
    for col, sym in [("^VIX", "VIX"), ("^VIX3M", "VIX3M"), ("^VVIX", "VVIX")]:
        with urllib.request.urlopen(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv", timeout=60) as r:
            c = pd.read_csv(io.BytesIO(r.read()))
        c.index = pd.to_datetime(c.iloc[:, 0], format="%m/%d/%Y")
        c = pd.to_numeric(c.iloc[:, -1], errors="coerce").dropna()
        common = vol.index.intersection(c.index)
        diff = (vol.loc[common, col] - c.loc[common]).abs()
        for d in diff[diff > 0.005].index:
            log.append({"series": col, "date": str(d.date()), "yahoo": float(vol.at[d, col]), "cboe": float(c[d])})
        vol.loc[common, col] = c.loc[common]
    vol.to_csv(f)
    pd.DataFrame(log).to_csv(DATA / "cboe_corrections.csv", index=False)
    print("CBOE official closes applied;", len(log), "cells corrected")


if __name__ == "__main__":
    main()
