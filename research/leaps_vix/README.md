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
python study_ls_ma.py              # long/short momentum with the 50-day tilt
python study_leadership.py         # QQQ / mid / small / sectors / Mag 7 + AI leadership
python study_goal25.py             # higher Sharpe + 25% goal: 8 sleeves, risk balancing, leverage, stress test
python make_charts.py
python study_goal25_wf.py          # walk-forward re-optimisation, rolling windows, edge, bootstrap
python build_goal_page.py          # goal25.html plan page
python study_hard25.py             # Frontier plan: crypto, hard assets, energy, space/SpaceX
python build_hard_page.py          # frontier.html plan page
python build_page.py              # interactive paper.html from results/
```

Finance hosts must be reachable (the `looper3` cloud environment allows them).
