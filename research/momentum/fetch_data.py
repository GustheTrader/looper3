"""
Download every dataset the momentum study needs, from free sources only.

Sources
-------
1. Kenneth R. French Data Library (Dartmouth)  -- free, academic, built from CRSP.
   * Covers *every* NYSE/AMEX/NASDAQ stock, including ones that later died,
     so it has NO survivorship bias. This is our "whole market" ground truth.
2. Yahoo Finance via the free `yfinance` Python library.
   * SPY + the SPDR sector ETFs (tradable sector proxies).
   * Today's S&P 500 members (stock-level scan). WARNING: this list only holds
     companies that survived to 2026, so stock-level results are biased upward.

Run:  python fetch_data.py          (re-running skips files already on disk)
Output goes to ./data  (git-ignored; re-download instead of committing data).
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)

FRENCH = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FRENCH_FILES = {
    # name on disk          : zip on the server
    "ff_factors_m": "F-F_Research_Data_Factors_CSV.zip",
    "ff_factors_d": "F-F_Research_Data_Factors_daily_CSV.zip",
    "ff_mom_m": "F-F_Momentum_Factor_CSV.zip",
    "mom10_m": "10_Portfolios_Prior_12_2_CSV.zip",
    "rev10_m": "10_Portfolios_Prior_1_0_CSV.zip",     # short-term (1-month) sort
    "ltr10_m": "10_Portfolios_Prior_60_13_CSV.zip",   # long-term (5-year) sort
    "ind49_m": "49_Industry_Portfolios_CSV.zip",
    "ind12_m": "12_Industry_Portfolios_CSV.zip",
    "ind49_d": "49_Industry_Portfolios_daily_CSV.zip",
}

SECTOR_ETFS = ["SPY", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLRE", "XLC"]


def _first_block(text: str) -> pd.DataFrame:
    """French CSVs hold several tables; the first one is value-weighted returns.

    A table starts with a header line beginning with ',' and ends at the first
    blank line. Dates are YYYYMM (monthly) or YYYYMMDD (daily). Values are in
    percent and -99.99 / -999 mean "missing".
    """
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(",") or l.lower().startswith(" ,"))
    rows = []
    for l in lines[start + 1:]:
        if not l.strip() or not re.match(r"^\s*\d{6,8}\s*,", l):
            break
        rows.append(l)
    header = [h.strip() for h in lines[start].split(",")]
    header[0] = "date"
    df = pd.read_csv(io.StringIO("\n".join(rows)), header=None, names=header)
    d = df["date"].astype(str).str.strip()
    if d.str.len().iloc[0] == 6:
        df["date"] = pd.to_datetime(d, format="%Y%m") + pd.offsets.MonthEnd(0)
    else:
        df["date"] = pd.to_datetime(d, format="%Y%m%d")
    df = df.set_index("date").apply(pd.to_numeric, errors="coerce")
    df = df.mask(df <= -99.99) / 100.0  # percent -> decimal
    return df


def fetch_french() -> None:
    for name, zname in FRENCH_FILES.items():
        out = DATA / f"{name}.csv"
        if out.exists():
            continue
        r = requests.get(FRENCH + zname, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            text = z.read(z.namelist()[0]).decode("latin-1")
        df = _first_block(text)
        df.to_csv(out)
        print(f"french {name:14s} {df.index[0].date()} -> {df.index[-1].date()}  cols={df.shape[1]}")


def _yf_download(tickers: list[str], out: Path, start="1993-01-01") -> None:
    import yfinance as yf

    if out.exists():
        return
    frames = []
    for i in range(0, len(tickers), 50):
        batch = tickers[i:i + 50]
        for attempt in range(4):
            try:
                px = yf.download(batch, start=start, auto_adjust=True, progress=False,
                                 threads=True)["Close"]
                break
            except Exception as e:  # network hiccup -> back off and retry
                print("retry", attempt, e)
                time.sleep(2 ** attempt)
        if isinstance(px, pd.Series):
            px = px.to_frame(batch[0])
        frames.append(px)
        print(f"yahoo {out.stem}: {i + len(batch)}/{len(tickers)}")
    pd.concat(frames, axis=1).sort_index().to_csv(out)


def fetch_yahoo() -> None:
    _yf_download(SECTOR_ETFS, DATA / "etf_close_d.csv")
    cons = pd.read_csv(DATA / "sp500_constituents.csv")
    # Yahoo spells class shares with '-' (BRK-B), Wikipedia with '.' (BRK.B)
    tickers = cons["Symbol"].str.replace(".", "-", regex=False).tolist()
    _yf_download(tickers, DATA / "sp500_close_d.csv")


if __name__ == "__main__":
    fetch_french()
    fetch_yahoo()
