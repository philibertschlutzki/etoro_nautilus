
Auftrag 1: Phase 0a - Cleanup --dry-run & Einführen von --no-deploy
# Kontext & Ziel
Du bearbeitest das eToro Nautilus Projekt. Das Ziel ist Phase 0a aus `konzept_automatisierte_strategie_optimierung_v2.md`: Die restlose Entfernung von `--dry-run` und die Einführung von `--no-deploy` in `daily_orchestrator.py`.

# Aufgaben
1. Entferne das `--dry-run` Argument aus `automation/daily_orchestrator.py` und allen Referenzen.
2. Füge das Argument `--no-deploy` hinzu.
3. Ändere die Logik in `phase3_4_backtest_and_tournament`, sodass der Backtest IMMER real läuft. Der `_create_dummy_tournament`-Fallback darf NUR noch ausgeführt werden, wenn nach dem echten Lauf `not TOURNAMENT_PATH.exists()` zutrifft (echter No-Data-Fallback).
4. Ändere `phase5_live_deployment`: Wenn `no_deploy=True`, logge "[Phase 5] --no-deploy: Live-Deploy unterbunden" und beende mit Exit-Code 0. Echte Deployments werden übersprungen. Emittiere das JSON Event "LIVE_DEPLOY_SKIPPED_NO_DEPLOY".

# Harte Abnahmekriterien & Tests (TDD)
- [ ] Passe `automation/tests/test_orchestrator_phase1.py` (oder ein neues `test_orchestrator_cli.py`) an:
  - Test 1: Aufruf mit `--no-deploy` ruft Phase 1-4 real auf, mockt den Subprocess für Phase 5 und prüft den Early-Exit (Exit Code 0).
  - Test 2: Aufruf mit `--dry-run` muss einen argparse Error (unrecognized arguments) werfen.
- [ ] Der CI Workflow `.github/workflows/pytest-gate.yml` muss fehlerfrei durchlaufen (Grün).

# AGENTS.md Dokumentation
- [ ] Ergänze in `AGENTS.md` unter "16. Bekannte Pitfalls" und im "Changelog", dass `--dry-run` restlos durch `--no-deploy` ersetzt wurde (wie in Pitfall-Draft 9.2 aus dem Konzept verlangt). Es muss zwingend vermerkt werden, dass Phase 3 nun immer real läuft.

Auftrag 2: Phase 0b - Config/Log Isolation & Manifest-Contract
Markdown
# Kontext & Ziel
Vorbereitung des Runners für den Optimierer. Der `backtest_runner.py` muss vollständig über ein JSON-Manifest gesteuert werden können und seine Config/Logs hermetisch aus Umgebungsvariablen beziehen.

# Aufgaben
1. Implementiere in `daily_orchestrator.py` und `backtest_runner.py` die Unterstützung für `ETORO_CONFIG_DIR` (Fallback: `automation/config`) und `ETORO_LOGS_DIR` (Fallback: `logs/`). Nutze `os.getenv`.
2. Implementiere im `backtest_runner.py` den Manifest-Contract: Wenn die über `--config` geladene JSON-Datei einen Key `"manifest_version"` enthält, darf der Runner KEIN Re-Merge mit `strategy_defaults.json` durchführen. Die `params` unter `strategies[]` sind in diesem Fall vollständig aufgelöst und autoritativ.
3. Stelle sicher, dass bei `walk_forward.splits > 1` die Pro-Fold-OOS-Sortinos in der Methode `extract_metrics` aggregiert und als Liste unter dem Key `oos_fold_sortinos` im JSON exportiert werden.

# Harte Abnahmekriterien & Tests (TDD)
- [ ] Schreibe `automation/tests/test_runner_manifest_contract.py`:
  - Test 1: Lade ein fiktives Manifest mit `"manifest_version": "1.0"`. Prüfe, dass `backtest_runner.py` exakt diese Parameter an die Strategie übergibt und NICHTs aus defaults überschreibt/merget.
  - Test 2: Prüfe, dass `ETORO_CONFIG_DIR` korrekt referenziert wird.
- [ ] CI Integration: Füge in `.github/workflows/pytest-gate.yml` in Tier 3 den Run für `test_runner_manifest_contract.py` hinzu.

# AGENTS.md Dokumentation
- [ ] Füge den "Optimizer / backtest_runner.py — Config-Contract" aus Kapitel 9.2 des Konzepts exakt so in `AGENTS.md` ein. 
- [ ] Dokumentiere das neue Verhalten der Pro-Fold-OOS-Sortinos in Kapitel 10.
- [ ] Aktualisiere das Changelog (Kapitel 19).


Auftrag 3: Phase 1a - Zero-Hardcoding, backtest.json Update & Optimizer Manifest Building
# Kontext & Ziel
Erstellung der Infrastruktur für den Optimierer. Zero-Hardcoding-Regel: Keine Parameter dürfen im Python-Code hartcodiert sein. Wir integrieren die Holdout-Tage in `backtest.json` und erstellen eine neue `optimizer.json`.

# Aufgaben
1. Erweitere `automation/config/backtest.json` im Block `"walk_forward"` um den Key `"holdout_days": 45`.
2. Erstelle `automation/config/optimizer.json`. Diese muss folgende Keys enthalten: `"n_trials": 100`, `"n_startup_trials": 16`, `"seed": 42`, `"penalty_overfit_weight": 0.5`, `"penalty_dd_weight": 8.0`, `"bonus_coverage_weight": 1.0`.
3. Erstelle das Paket `automation/optimizer/` mit `__init__.py`.
4. Erstelle `automation/optimizer/manifest.py` mit Funktionen für `git_commit()`, `catalog_fingerprint()` und `sha256_file()`.
5. Erstelle `automation/optimizer/resolve.py` mit `resolve_params()`, welches Defaults, `strategies.json` und gesampelte Parameter korrekt (höchste Prio = gesampelt) merget.
6. Erstelle `automation/optimizer/trial_config.py` mit `build_trial()`. Erzeuge isolierte `trial_dir` Verzeichnisse, kopiere den `ETORO_CONFIG_DIR` Inhalt, lade `holdout_days` aus der `backtest.json` und errechne das `end_time` (Heute minus Holdout-Days, exakt auf UTC 00:00). Schreibe das fertige `experiment_manifest.json`.

# Harte Abnahmekriterien & Tests (TDD)
- [ ] Schreibe `automation/tests/test_optimizer_manifest.py`:
  - Test 1: `resolve_params` überschreibt Defaults korrekt.
  - Test 2: `build_trial` erzeugt ein valides Manifest. Das `end_time` muss dynamisch exakt auf (Heute - `holdout_days`) fallen.
  - Test 3: Prüfe, dass `optimizer.json` fehlerfrei geparst werden kann.
- [ ] Keine direkten Importe aus `archive/`! Das Standalone-Prinzip gilt strikt.
- [ ] CI: Füge in `pytest-gate.yml` eine neue Sektion "Tier 10: Optimizer" ein und lass `test_optimizer_manifest.py` laufen.

# AGENTS.md Dokumentation
- [ ] Dokumentiere `optimizer.json` und das neue `holdout_days` Feld in `backtest.json` in Kapitel 7 (Konfigurationssystem).
- [ ] Dokumentiere das neue Modul `automation/optimizer/` in Kapitel 2.
- [ ] Aktualisiere das Changelog.

Auftrag 4: Phase 1b - Ausführung, Parser & Parameter-gesteuerter Skalarer Reward
# Kontext & Ziel
Der Optimierer muss den `backtest_runner.py` isoliert aufrufen können, das `tournament_result.json` lesen und den Reward-Score rein auf Basis der Konfiguration (ohne Hardcoding) berechnen.

# Aufgaben
1. Erstelle `automation/optimizer/runner.py` mit `run_backtest()`. Rufe `backtest_runner.py` als Subprocess auf (`timeout=10800`). Nutze die Trial-Umgebung (`ETORO_CONFIG_DIR` zeigt auf das Trial-Config-Verzeichnis, `ETORO_LOGS_DIR` auf das Trial-Log-Verzeichnis).
2. Erstelle `automation/optimizer/parsing.py`, welches das resultierende `tournament_result.json` liest und in eine `TournamentMetrics` Dataclass parst (mit sicherem Typ-Handling für `None`). Lies zwingend `oos_fold_sortinos` aus.
3. Erstelle `automation/optimizer/reward.py` mit `compute_reward()`. 
   - Lade zwingend die Penalty-Weights (`penalty_overfit_weight`, etc.) dynamisch aus der `optimizer.json`. Nichts hardcodieren!
   - Den Risk-Drawdown-Cap lädst du dynamisch aus `tournament.json` (`max_drawdown`).
   - Reward-Formel: `base - (overfit_gap * penalty_overfit_weight) - (dd_excess * penalty_dd_weight) + (coverage * bonus_coverage_weight)`.

# Harte Abnahmekriterien & Tests (TDD)
- [ ] Schreibe `automation/tests/test_optimizer_reward_parser.py`:
  - Test 1: Parser liest `oos_fold_sortinos` und berechnet korrekt den Median.
  - Test 2: `compute_reward` bestraft Overfitting exakt nach den Gewichten der `optimizer.json`. Keine Magic Numbers im Assert! Lese die Gewichte im Test ebenfalls aus der JSON.
  - Test 3: `compute_reward` wirft eine hohe Strafe (-10.0), wenn `oos_evaluated == False`.
- [ ] CI: Ergänze den Test in Tier 10 der `pytest-gate.yml`.

# AGENTS.md Dokumentation
- [ ] Dokumentiere die dynamische Gewichtung der Reward-Funktion im `AGENTS.md` (Zero-Hardcoding Regel).
- [ ] Aktualisiere das Changelog.

Auftrag 5: Phase 1c - Optuna-Loop (SQLite), Holdout-Confirmation & PR-Export
# Kontext & Ziel
Die Zusammenführung zur Hauptschleife. Optuna samplen lassen, das beste Setup gegen den ungesehenen Holdout testen und einen exportierbaren PR-Proposal generieren.

# Aufgaben
1. Erstelle `automation/optimizer/spaces.py` mit der Optuna-Sampling-Logik für `HourlyMeanReversionStrategy` und `SmaCrossoverStrategy`.
2. Erstelle `automation/optimizer/confirm.py` mit den Funktionen `confirm_on_holdout` und `export_proposal`. `confirm_on_holdout` generiert ein Trial mit `holdout_days=0` (sodass der Holdout nun das reguläre OOS ist).
3. Erstelle `automation/optimizer/run_optimization.py`. 
   - Lade `n_trials`, `n_startup_trials` und `seed` aus `optimizer.json`.
   - Setze die Storage Engine strikt auf SQLite: `f"sqlite:///{WORK / 'studies.db'}"`.
   - Implementiere `make_objective` und starte die `study.optimize` Schleife mit `TPESampler`.
   - Nach der Optimierung: Rufe `confirm_on_holdout` für den besten Trial auf und exportiere das JSON-Proposal.

# Harte Abnahmekriterien & Tests (TDD)
- [ ] Schreibe `automation/tests/test_optimizer_loop.py`:
  - Test 1: Mocke `run_backtest` via `unittest.mock`, sodass kein echter Subprocess startet, sondern ein gefaktes Metric-JSON geschrieben wird.
  - Test 2: Lass `optimize()` mit einem MOCK-Setup (`n_trials=2`) laufen. Prüfe, ob Optuna Parameter generiert, die SQLite `.db` Datei angelegt wird und am Ende ein `proposal_...json` auf der Platte liegt.
- [ ] CI: Der Mock-Test MUSS in `.github/workflows/pytest-gate.yml` laufen. Das stellt sicher, dass die Pipeline nicht durch echte stundenlange Backtests blockiert wird.

# AGENTS.md Dokumentation
- [ ] Füge Kapitel 12 "Sicherheits-Leitplanken (verbindlich)" aus dem Konzept exakt so in `AGENTS.md` ein (Risiko-Gates eingefroren, PR-Zwang, Holdout unberührt).
- [ ] Dokumentiere, dass die Optuna-Storage exklusiv auf SQLite läuft.
- [ ] Aktualisiere das Changelog (Finaler Schritt Autotuner V2).
