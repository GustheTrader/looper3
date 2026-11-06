# Momentum research

Thirty-year study of (A) long/short momentum across the whole US market and
(B) sector concentration using a Sector Power Strength Rating, including which
rolling window to use. **Start with [REPORT.md](REPORT.md).**

## Run it yourself

```bash
pip install -r requirements.txt
python fetch_data.py        # free data -> data/ (needs the finance hosts allowed)
python study_market.py      # Concept A
python study_sectors.py     # Concept B + live scan
python study_selection.py   # which-window-selection-method comparison
python make_charts.py       # charts/
```

The whole pipeline runs in about a minute after the download.
