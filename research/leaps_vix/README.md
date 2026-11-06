# LEAPS income + VIX spike research

A follow-on to `../momentum`. **Read [PAPER.md](PAPER.md).**

```bash
pip install -r ../momentum/requirements.txt
python ../momentum/fetch_data.py   # needs the Fama-French risk-free rate from there
python fetch_data.py               # VIX family, ^PUT, VIXY/SVXY, raw + adjusted ETF prices
python calibrate.py                # fit the option model to ^PUT and VIXY -> results/calibration.json
python study_leaps.py              # LEAPS / covered-call / PMCC on SPY and momentum sectors (~5 min)
python study_vix.py                # VIX spike sleeves, daily
python study_allocation.py         # capital allocation grid
python make_charts.py
python build_page.py              # interactive paper.html from results/
```

Finance hosts must be reachable (the `looper3` cloud environment allows them).
