"""Schreibt results/H{4..7}/preregistration.json (Prompt-SHA, Parameter, Seeds, Abweichungen). Aufruf: h48_prereg.py H4 [retro]"""
import hashlib, json, subprocess, sys, datetime as dt
h = sys.argv[1]; retro = len(sys.argv) > 2
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
dev = ["Kostentabelle: nur Rueckfallwerte aus costs.py (keine Werktags-Spread-Messung, kein Stufe-C-Zugang); Spreads K1 3,0 bps, Slippage 1,5 bps je Seite.",
       "Stufe B (Forward-Shadow) und Stufe C nicht ausgefuehrt; Route-R-Gegenprobe nicht ausgefuehrt (nur Route V).",
       "Kein eigener Branch ebc/hx je Hypothese; ein gemeinsamer Experiment-Branch."]
if h == "H4": dev.append("U_neu: Seed 20261012, Schichten 300/100/60 wie Spec, aber aus den auf eToro verfuegbaren Kandidaten mit bestandener Qualitaetspruefung (Zahl je Schicht siehe universe_neu_draw.json).")
if retro: dev.append("Vorregistrierung wurde NACH einem ersten Entwicklungslauf der Skripte geschrieben (Schwellen aus dem Prompt unveraendert, nicht nach Ergebnissen angepasst).")
d = dict(hypothesis=h, written_at=dt.datetime.now(dt.timezone.utc).isoformat(), prompt=f"manuals/ebc/EBC_{h}.md", prompt_sha256=sha(f"manuals/ebc/EBC_{h}.md"),
         repo_head=subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(), seed=20261011, comparison_period=dict(start="2024-12-23", end="2026-10-12", candles_until="2026-10-10"),
         preregistered_before_evaluation=not retro, thresholds="siehe Prompt §2.2/§2.3 (unveraendert)", protocol_deviations=dev)
if h == "H4": d["universe_neu_sha256"] = sha("results/H4/universe_neu.csv"); d["seed_universum"] = 20261012
json.dump(d, open(f"results/{h}/preregistration.json", "w"), indent=1)
