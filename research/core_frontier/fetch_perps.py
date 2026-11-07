"""
Fetch perpetual-futures FUNDING history (the cost of holding a perp) plus CBOE implied-vol indices.

  Deribit   BTC-PERPETUAL, ETH-PERPETUAL  hourly funding ("interest_1h"), 2019 ->   (public API, no key)
  Hyperliquid BTC, ETH                    hourly funding, 2023 ->                   (cross-check of a 2nd venue)
  CBOE      VXAPL VXAZN VXIBM VXGS (single stocks), VXN RVX VXEEM VXEWZ VXFXI VXSLV VXTLT (ETFs/indices),
            VIX9D VIX6M (extra SPX term points)                                      (public daily CSVs)

A long perp pays funding when the rate is positive (perp above spot) and receives it when negative.
Daily funding = sum of the 24 hourly rates.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"
(DATA / "cboe").mkdir(parents=True, exist_ok=True)
DAY_MS = 86_400_000


def get(url, body=None):
    for k in range(4):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                         headers={"Content-Type": "application/json"} if body else {})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            if k == 3:
                raise
            time.sleep(2 ** (k + 1))


def deribit(inst, start="2019-01-01"):
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    now = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    rows = []
    while t0 < now:
        t1 = min(t0 + 30 * DAY_MS, now)
        res = get(f"https://www.deribit.com/api/v2/public/get_funding_rate_history?instrument_name={inst}"
                  f"&start_timestamp={t0}&end_timestamp={t1}")["result"]
        rows += [(x["timestamp"], x["interest_1h"], x["index_price"]) for x in res]
        t0 = t1
    d = pd.DataFrame(rows, columns=["ts", "f1h", "index"]).drop_duplicates("ts")
    d.index = pd.to_datetime(d.ts, unit="ms", utc=True)
    return d[["f1h", "index"]].sort_index()


def hyperliquid(coin, start="2023-05-01"):
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    rows = []
    while True:
        res = get("https://api.hyperliquid.xyz/info", {"type": "fundingHistory", "coin": coin, "startTime": t0})
        if not res:
            break
        rows += [(x["time"], float(x["fundingRate"])) for x in res]
        nxt = res[-1]["time"] + 1
        if nxt <= t0 or len(res) < 10:
            break
        t0 = nxt
        time.sleep(0.2)
    d = pd.DataFrame(rows, columns=["ts", "f1h"]).drop_duplicates("ts")
    d.index = pd.to_datetime(d.ts, unit="ms", utc=True)
    return d[["f1h"]].sort_index()


def cboe(sym):
    url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as r:
        (DATA / "cboe" / f"{sym}.csv").write_bytes(r.read())


def main():
    daily = {}
    for inst, key in [("BTC-PERPETUAL", "deribit_btc"), ("ETH-PERPETUAL", "deribit_eth")]:
        h = deribit(inst)
        h.to_csv(DATA / f"funding_{key}_hourly.csv")
        daily[key] = h.f1h.groupby(h.index.floor("D")).sum()
        print(key, len(h), h.index[0], h.index[-1])
    for coin, key in [("BTC", "hl_btc"), ("ETH", "hl_eth")]:
        h = hyperliquid(coin)
        daily[key] = h.f1h.groupby(h.index.floor("D")).sum()
        print(key, len(h), h.index[0], h.index[-1])
    f = pd.DataFrame(daily)
    f.index = f.index.tz_localize(None)
    f.to_csv(DATA / "funding_daily.csv")
    for s in ["VXAPL", "VXAZN", "VXIBM", "VXGS", "VXN", "RVX", "VXEEM", "VXEWZ", "VXFXI", "VXSLV", "VXTLT", "VIX9D", "VIX6M"]:
        try:
            cboe(s)
        except Exception as e:  # noqa: BLE001
            print("cboe fail", s, e)
    print((f.mean() * 365).round(4).to_dict())


if __name__ == "__main__":
    main()
