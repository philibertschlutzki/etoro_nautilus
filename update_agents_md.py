import re

with open("automation/AGENTS.md", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add Pitfall 88
pitfall_88 = """
### 🟢 Pitfall #88 — Leakage in OOS Window durch Indikator-Lookback [BEHOBEN: GH-#466]
**Symptom:** Time-Series-Splits in `backtest_runner.py` generieren eine kontiguierliche OOS-Union, bei der der Start des OOS-Folds exakt auf das Ende des IS-Folds fällt, was zu Leakage durch Indikator-Lookbacks führt.
**Root Cause:** Fehlende Embargo-Periode (Purge/Embargo). Indikatoren, die im OOS-Fenster berechnet werden, beziehen IS-Daten mit ein.
**Fix/Regel:** `embargo_period_days` aus `walk_forward_dict` (Default 0) in die Berechnung des `split_oos_start_ns` integriert. OOS-Start = IS-Start + IS-Window + Embargo-Period.

### 🟢 Pitfall #89 — Semantics Guard Posterior-Korruption [BEHOBEN: GH-#468]
**Symptom:** Optuna mischt Trials aus alten und neuen Reward-Semantiken, was den TPE-Sampler korrumpiert und die Suche divergiert.
**Root Cause:** `_check_reward_semantics_version` hat bisher nur geloggt, den Lauf aber nicht abgebrochen.
**Fix/Regel:** Zwingender Fail-Loud (`ValueError`) bei `existing < current` mit bestehenden Trials. Jede Code-Änderung der Penalty-/Distanz-Logik erfordert den Bump der `reward_semantics_version` in `optimizer.json`.

"""

content = content.replace("### 🟢 Pitfall #87", pitfall_88 + "### 🟢 Pitfall #87")

# 2. Add Changelog Entry
changelog_entry = "| 2026-06-29 | **IMPLEMENTIERUNG Issues #466, #467, #468**: Embargo-Splits, OOS-Penalty Decoupling, Semantics Guard. Pitfalls #88 & #89 dokumentiert. | `automation/backtest_runner.py`, `automation/optimizer/reward.py`, `automation/optimizer/run_optimization.py`, `automation/config/optimizer.json`, `automation/config/tournament.json`, `automation/config/backtest.json`, `automation/AGENTS.md` |\n"

# find the first changelog entry and insert before it
content = re.sub(r'(\| \d{4}-\d{2}-\d{2} \| \*\*IMPLEMENTIERUNG GitHub-Issue #462)', changelog_entry + r'\1', content)

with open("automation/AGENTS.md", "w", encoding="utf-8") as f:
    f.write(content)
