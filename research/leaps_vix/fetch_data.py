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
    for f in DATA.glob("*.csv"):
        x = pd.read_csv(f, index_col=0, parse_dates=True)
        print(f.name, x.shape, x.index[0].date(), x.index[-1].date())


if __name__ == "__main__":
    main()
