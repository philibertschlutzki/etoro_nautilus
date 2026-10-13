"""Woechentlicher Bericht (nur lesend): Forward-Shadow-Arme (results/H4/stage_b) und Stufe-A-Kontrolle von B+ mit gemessenen Spreads.
Ausgabe results/weekly/<Datum>.md. Aufruf nach spread_eval.py."""
import csv, datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.getcwd())
os.environ["EBC_MEASURED"] = "1"
import numpy as np
import h48, portfolio as pf

today = dt.datetime.now(dt.timezone.utc).date()
out = [f"# EBC Wochenbericht {today}\n"]
sm = json.load(open("results/H4/spread_measured.json")) if os.path.exists("results/H4/spread_measured.json") else None
if sm:
    out.append(f"## Spread-Messung\n\nStichproben {sm['samples_total']}, gültig {sm['samples_valid']}, Instrumente mit Messung {sm['n_instruments_measured']}.\n")
    out.append("| Klasse | Instrumente | Median p50 bps | Modell bps |\n|---|---|---|---|")
    for k, v in sorted(sm["by_class"].items()): out.append(f"| {k} | {v['n_instruments']} | {v['median_p50_bps']:.1f} | {v['model_fallback_bps']:.1f} |")
fw = "results/H4/stage_b/forward_daily_returns.csv"
if os.path.exists(fw):
    rd = list(csv.DictReader(open(fw))); n = len(rd)
    out.append(f"\n## Forward-Shadow (seit {rd[0]['date']}, {n} Handelstage)\n\n| Arm | Rendite | SR | MaxDD |\n|---|---|---|---|")
    for a in ("C30", "HLNP30", "Kombi6P"):
        r = np.array([float(x[f"{a}_r"]) for x in rd])
        out.append(f"| {a} | {100 * (np.prod(1 + r) - 1):.2f} % | {pf.sharpe(r) if n > 10 else float('nan'):.2f} | {100 * pf.max_dd(r):.1f} % |")
    if n < 30: out.append(f"\nErst {n} von 30 Handelstagen: kein Verdikt möglich.")
# Stufe A B+ mit gemessenen Kosten
rows, ct, series, cal = h48.setup(with_neu=False)
g0 = h48.g_start(cal)
tr, q = h48.build(rows, ct, series, cal, h48.gen_trades(rows, series), g0min=g0)
r, eq, tk = pf.simulate(tr, cal, slots=30, g_from=g0)
m = h48.summarize(r, tk, g0, "C30")
out.append(f"\n## Stufe A, B+ mit gemessenen Spreads ({'gemessen' if os.path.exists('data/ebc/spreads_valid.json') else 'Rückfall'})\n\nSR {m['sr']:.2f} (Rückfallkosten 2,02), MaxDD {100 * m['maxdd']:.1f} %, {m['n_trades']} Trades, Expectancy {m['expectancy_bps']:.0f} bps.")
os.makedirs("results/weekly", exist_ok=True)
open(f"results/weekly/{today}.md", "w").write("\n".join(out) + "\n"); print("\n".join(out))
