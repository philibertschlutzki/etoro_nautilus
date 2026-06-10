import re
import pathlib

file_path = pathlib.Path("automation/AGENTS.md")
content = file_path.read_text("utf-8")
lines = content.splitlines()

# 1. Config Contract
insert_idx_1 = -1
for i, line in enumerate(lines):
    if line.startswith("### 🟢 Pitfall #53"):
        insert_idx_1 = i
        break

config_contract = [
    "### Optimizer / `backtest_runner.py` — Config-Contract",
    "",
    "- `backtest_runner.py` liest Strategien + Parameter **ausschließlich** aus der via `--config` übergebenen Manifest-Datei. `strategies[].params` sind vollständig aufgelöst und autoritativ; **kein** erneutes Mergen aus `strategy_defaults.json`, sobald `manifest_version` gesetzt ist.",
    "- `backtest_runner.py` respektiert `ETORO_CONFIG_DIR` (Quelle der eingefrorenen `tournament.json`) und `ETORO_LOGS_DIR`; Ergebnisse ausschließlich nach `--output`.",
    "- Bei `walk_forward.splits > 1` exportiert `aggregate_winner.oos_fold_sortinos` die Pro-Fold-OOS-Sortinos als Liste (Basis des robusten Median-Rewards).",
    "- Param-Auflösung und Fold-Sortino-Sammlung MÜSSEN als reine, testbare Funktionen (`resolve_strategy_params`, `collect_oos_fold_sortinos`) vorliegen.",
    ""
]
if insert_idx_1 != -1:
    lines = lines[:insert_idx_1] + config_contract + lines[insert_idx_1:]

# 2. OOS Fold Sortinos documentation
insert_idx_2 = -1
for i, line in enumerate(lines):
    if line.startswith("## 11. Orchestrator"):
        insert_idx_2 = i - 1
        break
oos_doc = [
    "",
    "**`oos_fold_sortinos` (Pro-Fold-OOS-Sortinos):** Bei `walk_forward.splits > 1` sammelt `collect_oos_fold_sortinos()` je Walk-Forward-Fold den OOS-Sortino und exportiert sie als Liste unter `aggregate_winner.oos_fold_sortinos` im Tournament-JSON. Die Fold-Reihenfolge bleibt erhalten; Folds ohne auswertbaren OOS-Sortino (`None`) werden übersprungen (nicht als `0.0` geführt). Diese Liste ist die Basis des robusten Median-Rewards im Optimizer (`parsing.parse_tournament` bildet den Median über die Folds, mit Fallback auf das aggregierte `oos_metrics.sortino_ratio`). Bestehende Aggregat-Metrik-Felder bleiben unverändert.",
    ""
]
if insert_idx_2 != -1:
    lines = lines[:insert_idx_2] + oos_doc + lines[insert_idx_2:]

# 3. Changelog 1
insert_idx_3 = -1
for i, line in enumerate(lines):
    if line.startswith("| Datum | Änderung | Dateien |"):
        insert_idx_3 = i + 2
        break

changelog_1 = [
    "| 2026-06-10 | **Doku-Nachtrag 0b (Verifikations-Finding P1):** Config-Contract-Block in Kap. 16 wörtlich ergänzt; Verhalten von `oos_fold_sortinos` in Kap. 10 dokumentiert (wann gesetzt, Reihenfolge, None-Handling). | `automation/AGENTS.md` |"
]
if insert_idx_3 != -1:
    lines = lines[:insert_idx_3] + changelog_1 + lines[insert_idx_3:]

content = "\n".join(lines) + "\n"

# 4. Dedup Pitfalls
new_lines = []
lines = content.splitlines()

pitfall_50_count = 0
pitfall_37_count = 0
pitfall_41_count = 0
pitfall_43_count = 0
pitfall_45_count = 0

next_free_pitfall = 54

for line in lines:
    if line.startswith("* **Pitfall #50 — Alternation Lock & Cooldown Bypass Trap in Mean Reversion/Breakout:**"):
        pitfall_50_count += 1
        if pitfall_50_count == 1:
            new_lines.append(line)
        else:
            # Skip the duplicate
            pass
    elif line.startswith("* **Pitfall #50 (Zero-Trade Statistical Cascades / Micro-Sizing Illusion):**"):
        new_lines.append(line.replace("Pitfall #50", f"Pitfall #{next_free_pitfall}"))
        next_free_pitfall += 1
    elif line.startswith("* **Pitfall #50 (Gate-Scope vs. Deployment-Scope Mismatch):**"):
        new_lines.append(line.replace("Pitfall #50", f"Pitfall #{next_free_pitfall}"))
        next_free_pitfall += 1
    elif line.startswith("### 🟢 Pitfall #50 — Restriktive Strategiefrequenzen vs. OOS-Gating-Fenster (Issue #289)"):
        new_lines.append(line.replace("Pitfall #50", f"Pitfall #{next_free_pitfall}"))
        next_free_pitfall += 1
    elif line.startswith("* **Pitfall #37 (Orphaned Limit Orders"):
        new_lines.append(line.replace("Pitfall #37", f"Pitfall #{next_free_pitfall}"))
        next_free_pitfall += 1
    elif line.startswith("* **Pitfall #41 (State-Mutation vor"):
        new_lines.append(line.replace("Pitfall #41", f"Pitfall #{next_free_pitfall}"))
        next_free_pitfall += 1
    elif line.startswith("* **Pitfall #43 (Metrics Rendering und"):
        new_lines.append(line.replace("Pitfall #43", f"Pitfall #{next_free_pitfall}"))
        next_free_pitfall += 1
    elif line.startswith("### 🟢 #45 — Aggregate Metric Statistical Artifacts (Issue #255)"):
        new_lines.append(line.replace("#45", f"#{next_free_pitfall}"))
        next_free_pitfall += 1
    else:
        new_lines.append(line)

content = "\n".join(new_lines) + "\n"

lines = content.splitlines()

# 5. Changelog 2
insert_idx_4 = -1
for i, line in enumerate(lines):
    if line.startswith("| 2026-06-10 | **Doku-Nachtrag 0b (Verifikations-Finding P1):**"):
        insert_idx_4 = i + 1
        break

changelog_2 = [
    "| 2026-06-10 | **Pitfall-Nummern-Bereinigung (Verifikations-Finding P2):** Duplikate (insb. #50) entkoppelt — erste Instanz behält die Nummer, distincte Folge-Pitfalls auf #54+ umnummeriert, exakte Dubletten entfernt. Rein redaktionell, Pitfall-Inhalte unverändert; Querverweise verifiziert. | `automation/AGENTS.md` |"
]

if insert_idx_4 != -1:
    lines = lines[:insert_idx_4] + changelog_2 + lines[insert_idx_4:]

file_path.write_text("\n".join(lines) + "\n", "utf-8")
