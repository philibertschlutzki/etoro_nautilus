# 📋 Abnahmeprotokoll: eToro Nautilus Autotuner V2 (Phase 0a - 1c)

Dieses Dokument dient als formelles Checklisten-Protokoll zur Abnahme der 5 Pull Requests (PRs) für den Autotuner V2 durch den Operator. Jeder PR darf **erst gemerged werden**, wenn alle hier gelisteten Fragen mit "Ja" beantwortet und verifiziert wurden. Dies stellt technische Korrektheit, strikte Rückwärtskompatibilität und eine lückenlose Dokumentation in `AGENTS.md` sicher.

---

## 🛠 Auftrag 1: Phase 0a (`--dry-run` zu `--no-deploy`)
**Ziel:** Beseitigung von irreführenden Dummy-Tournaments; Phase 1-4 läuft immer real.

### 1. Code & Logik
- [ ] Wurde `--dry-run` im `argparse` und im gesamten Code restlos entfernt?
- [ ] Wurde `--no-deploy` korrekt in `build_arg_parser()` integriert?
- [ ] Läuft `phase3_4_backtest_and_tournament` jetzt *immer* real durch, es sei denn, es liegt der echte No-Data-Fallback vor (`not TOURNAMENT_PATH.exists()`)?
- [ ] Fängt `phase5_live_deployment` das `--no-deploy` Flag korrekt ab, emittiert das Event `LIVE_DEPLOY_SKIPPED_NO_DEPLOY` und beendet sich mit Exit Code 0, *ohne* `subprocess.Popen` für den Bot aufzurufen?

### 2. Tests & CI
- [ ] Sind die Tests in `test_orchestrator_cli.py` vorhanden und deterministisch (keine echten Netzwerkanfragen/Subprozesse)?
- [ ] Ist der Test in `.github/workflows/pytest-gate.yml` unter **Tier 3** eingehängt?
- [ ] Läuft die GitHub Action (CI-Gate) für diesen PR grün durch?

### 3. Rückwärtskompatibilität (Nicht-Bruch des Bestands)
- [ ] **Kritisch:** Wenn das Skript *ohne* Argumente aufgerufen wird (wie im täglichen Cronjob), läuft dann alles exakt so weiter wie bisher (inklusive echtem Deploy)?

### 4. AGENTS.md Validierung
- [ ] Wurde in Kapitel 16 ein neuer Pitfall für das Entfernen von `--dry-run` und das Verhalten von `--no-deploy` angelegt?
- [ ] Enthält das Changelog (Kap. 19) den entsprechenden Eintrag für Phase 0a?

---

## 🛠 Auftrag 2: Phase 0b (Env-Isolation, Manifest-Contract, Fold-Sortinos)
**Ziel:** Entkopplung des Backtest-Runners von statischen Pfaden und Defaults.

### 1. Code & Logik
- [ ] Nutzen `daily_orchestrator.py` und `backtest_runner.py` nun `os.getenv("ETORO_CONFIG_DIR")` und `ETORO_LOGS_DIR` via Accessor-Funktionen, statt hartcodierte Konstanten beim Import zu binden?
- [ ] Implementiert `resolve_strategy_params` den strikten Contract? (Wenn `is_manifest=True`, dürfen **keine** Parameter aus den Defaults gemerged werden).
- [ ] Sammelt `collect_oos_fold_sortinos` die Metriken None-sicher ein und wird die Liste unter `aggregate_winner.oos_fold_sortinos` exportiert?

### 2. Tests & CI
- [ ] Sind die drei Testdateien (`test_runner_env_isolation.py`, `test_runner_manifest_contract.py`, `test_runner_fold_sortinos.py`) als reine Unit-Tests umgesetzt?
- [ ] Sind alle drei Tests in `.github/workflows/pytest-gate.yml` (**Tier 3**) eingehängt und grün?

### 3. Rückwärtskompatibilität (Nicht-Bruch des Bestands)
- [ ] **Kritisch:** Wenn der `backtest_runner.py` regulär vom Orchestrator gestartet wird (also *ohne* `manifest_version` in der Config), greift dann weiterhin der Legacy-Merge mit den Werten aus `strategy_defaults.json`?

### 4. AGENTS.md Validierung
- [ ] Ist der Block "Optimizer / backtest_runner.py — Config-Contract" in Kap. 16 exakt und wörtlich dokumentiert?
- [ ] Ist das Verhalten der `oos_fold_sortinos` in Kap. 10 dokumentiert?
- [ ] Ist Changelog 0b in Kap. 19 eingetragen?

---

## 🛠 Auftrag 3: Phase 1a (Config-Erweiterung & Optimizer-Grundgerüst)
**Ziel:** Neues Modul, Manifest-Building und absolutes Zero-Hardcoding.

### 1. Code & Logik
- [ ] Ist `holdout_days` (Wert 45) sauber im `"walk_forward"` Block in `backtest.json` integriert und im Schema dokumentiert?
- [ ] Existiert die neue `optimizer.json` mit exakt den geforderten Keys (inklusive Weights und Clips)?
- [ ] Wendet `trial_config.build_trial()` die Sonntags-Rollback-Logik absolut identisch zur Produktions-Logik im Orchestrator an?
- [ ] Enthält das `experiment_manifest.json` die korrekten Provenienz-Hashes?

### 2. Tests & CI
- [ ] Funktionieren die Prioritäten in `resolve.resolve_params` korrekt (`sampled` > `strategies.json` > `defaults`)?
- [ ] Nutzt der Test `test_build_trial_end_time_weekday` Dependency Injection (z.B. injiziertes `now`), sodass er nicht um Mitternacht oder am Wochenende fehlschlägt?
- [ ] Ist der Test in **Tier 10: Optimizer** der CI eingehängt und grün?

### 3. Rückwärtskompatibilität (Nicht-Bruch des Bestands)
- [ ] Beachtet das neue `automation/optimizer/` Paket strikt das Standalone-Prinzip (keine Importe aus `archive` oder `adapters`)?
- [ ] Wurde bestätigt, dass *keine* existierende Funktionalität durch das Hinzufügen der neuen JSON-Dateien beeinträchtigt wird?

### 4. AGENTS.md Validierung
- [ ] Sind die Neuerungen in `backtest.json` und die `optimizer.json` im Konfigurationssystem (Kap. 7) exakt beschrieben?
- [ ] Wurde das Modul `automation/optimizer/` im Architektur-Baum (Kap. 2) ergänzt?
- [ ] Ist Changelog 1a dokumentiert?

---

## 🛠 Auftrag 4: Phase 1b (Runner-Aufruf, Parser & konfigurierter Reward)
**Ziel:** Subprozess-Start des Runners und hardcoding-freie Reward-Berechnung.

### 1. Code & Logik
- [ ] Ruft `runner.run_backtest` den Subprozess mit `check=False` auf und reicht `ETORO_CONFIG_DIR` sowie `ETORO_LOGS_DIR` sauber durch?
- [ ] Parst `parsing.parse_tournament` das Feld `oos_fold_sortinos` und berechnet sicher den Median?
- [ ] **Kritischer Zero-Hardcoding-Check:** Zieht `reward.compute_reward` *alle* Gewichte aus `optimizer.json` und das DD-Cap aus `tournament.json`? Es dürfen keine Magic Numbers im Python-Code stehen!

### 2. Tests & CI
- [ ] **CI-Schutz-Check:** Ist sichergestellt, dass `test_optimizer_runner.py` den `subprocess.run` mockt und *keinen* echten stundenlangen Backtest auslöst?
- [ ] Sind Magic Numbers in den Assertions der Tests vermieden (lesen die Tests die erwarteten Werte aus den Mock-Configs)?
- [ ] Sind beide Tests in **Tier 10** und laufen grün?

### 3. Rückwärtskompatibilität (Nicht-Bruch des Bestands)
- [ ] Wurde bestätigt, dass der Aufruf von `automation/backtest_runner.py` im Orchestrator nicht beeinträchtigt wurde?

### 4. AGENTS.md Validierung
- [ ] Ist die dynamische Reward-Gewichtung in Kap. 7 dokumentiert?
- [ ] Ist Changelog 1b dokumentiert?

---

## 🛠 Auftrag 5: Phase 1c (Optuna-Loop, Holdout-Confirmation & PR-Export)
**Ziel:** Die eigentliche Optimierungsschleife (TPE), OOS-Holdout und PR-File Generierung.

### 1. Code & Logik
- [ ] Laufen die `spaces.sample_params` exakt in den vertraglich zugesicherten Parameter-Ranges?
- [ ] Läuft die Optuna Storage-Engine *ausschließlich* auf SQLite (`sqlite:///.../studies.db`)? Keine PostgreSQL-Artefakte?
- [ ] Setzt `confirm.confirm_on_holdout` zwingend `holdout_days=0` und liest `risk_dd_cap` dynamisch?
- [ ] Schreibt `export_proposal` das korrekte Status-Feld (`READY_FOR_PR` vs `REJECTED_ON_HOLDOUT`)?

### 2. Tests & CI
- [ ] **CI-Schutz-Check:** Setzt der Test `test_optimize_creates_db_and_proposal` einen Mock für den Subprozess und zwingt Optuna durch den Parameter `n_trials=2` zu einem schnellen Durchlauf?
- [ ] Sind alle Abhängigkeiten (WORK-Dir, STORAGE) im Test sauber auf `tmp_path` gemockt, um das Repository nicht mit Datenbank-Dateien zu verschmutzen?
- [ ] Ist der Test in **Tier 10** und läuft grün?

### 3. Rückwärtskompatibilität (Nicht-Bruch des Bestands)
- [ ] **Kritisch:** Das Modul darf unter keinen Umständen aus sich heraus in den Live-Handel (Phase 5) eingreifen. Wurde dies sichergestellt?

### 4. AGENTS.md Validierung
- [ ] Wurden die "Sicherheits-Leitplanken" exakt wie gefordert in Kap. 12 übernommen?
- [ ] Sind die neuen Pitfalls (Ausschließlich SQLite / Keine Veränderung der `tournament.json` / Kein Start von Phase 5) in Kap. 16 dokumentiert?
- [ ] Wurde der Abschluss des Autotuners V2 im Changelog (Kap. 19) eingetragen?
