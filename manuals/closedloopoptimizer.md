> **Status (2026-08-23):** Diese Architektur wurde gegen den bestehenden Code-Stand geprüft; die vier Umsetzungs-Issues sind als GitHub-Issues #1104–#1107 angelegt (siehe `manuals/closedloop_issues.md`). Bekannte Abweichungen dieses Dokuments vom realen Code, die bei der Umsetzung zu beachten sind: (1) §5.1 zeigt einen `run_optimization.py`-CLI-Aufruf mit `--symbol/--config/--search-space-overrides/--workers/--cpcv-folds/--output-dir` — real existieren nur `--strategy`, `--n-trials`, `--n-jobs`; (2) §6.1 nennt feste Gate-Schwellen (Sharpe/Sortino/PBO/DSR/MaxDD) — real entscheidet `automation/optimizer/deployment_gate.py::evaluate_deployment_eligibility` mit elf Klauseln, u. a. ist `min_sortino` seit Issue #614 kein hartes Gate mehr; (3) §6.2 beschreibt einen automatisierten Git-Commit bei Promotion — das produktive `champions.py` hält Live-Deployment bewusst als menschliche Entscheidung (HI-3-Prinzip); der Orchestrator darf `strategies.json` nie selbst schreiben und muss in einem isolierten Git-Worktree laufen, getrennt vom Arbeitsverzeichnis von `daily_orchestrator.py`/`momentum_ls_run.py`.

### 1. Systemarchitektur & API-Orchestrierung

Das Subsystem zur autonomen Optimierung wird als orchestrierende Steuerungsschicht (`automation/ai_loop/`) implementiert, welche die bestehende lokale Backtesting-, CPCV- und Deployment-Gate-Infrastruktur kapselt.

```
+-----------------------------------------------------------------------------------+
|                            automation/ai_loop/                                    |
|                                                                                   |
|  +---------------------+      Prompt       +-----------------------------------+  |
|  | Performance Parser  | ----------------> | deepseek-reasoner (R1)            |  |
|  | (Logs, Metrics,     |                   | - Root Cause Analysis             |  |
|  |  Rejection Reasons) |                   | - Hypothesen & Pfadwahl (A/B)     |  |
|  +---------------------+                   +-----------------------------------+  |
|             ^                                                |                    |
|             |                                                v                    |
|             |                              +-----------------------------------+  |
|             |                              | deepseek-chat (V3)                |  |
|             |                              | - Code-/JSON-Synthese             |  |
|             |                              +-----------------------------------+  |
|             |                                                |                    |
|             |                                                v                    |
|             | Feedback-Schleife            +-----------------------------------+  |
|             | (Rejection context)          | Static Validation Pipeline        |  |
|             |                              | (ruff, mypy, pytest, AST Bias)    |  |
|             |                              +-----------------------------------+  |
|             |                                                |                    |
|             |                                                v (Valid)            |
|  +---------------------+   Report / Gate   +-----------------------------------+  |
|  | Deployment Gate     | <---------------- | Lokale Execution Engine           |  |
|  | & Champion Registry |                   | (CPCV, Slippage/Spread Stress)    |  |
|  +---------------------+                   +-----------------------------------+  |
+-----------------------------------------------------------------------------------+

```

#### 1.1 Modul- und Dateistruktur

* **`automation/ai_loop/`**:
* `__init__.py`: Package-Initialisierung.
* `client.py`: Async DeepSeek API Client (Handling für Token-Limits, Retries, Model Routing).
* `ingestion.py`: Extraktion und Normalisierung von Backtest-Reports und Gate-Telemetrie.
* `reasoning.py`: Prompt-Generierung und Interaktion mit `deepseek-reasoner` (R1).
* `synthesizer.py`: Code- und Suchraum-Synthese via `deepseek-chat` (V3).
* `validator.py`: Lokale AST-, Linter-, Typ- und Unit-Test-Prüfung.
* `orchestrator.py`: State Machine zur Ausführung der 5 Zyklus-Schritte.
* `memory.py`: Persistenz der Optimierungshistorie (`logs/ai_optimization_ledger.jsonl`).



#### 1.2 Konfiguration & Schnittstellen

* **Umgebungsvariablen (`.env`)**:
* `DEEPSEEK_API_KEY`: API-Schlüssel für DeepSeek.
* `AI_LOOP_MAX_ITERATIONS`: Maximale Anzahl an Schleifendurchläufen pro Lauf (z. B. `20`).
* `AI_LOOP_MAX_RETRIES`: Maximale Anzahl an Selbstkorrektur-Zyklen bei Syntaxfehlern (z. B. `3`).
* `AI_LOOP_STRATEGY_ALLOWLIST`: Komma-separierte Liste der zu mutierenden Strategien.



---

### 2. Phase 1 – Ingestion & Leistungsanalyse

Extraktion und Aggregation aller quantitativen Leistungsdaten vergangener Läufe als strukturierter Input für das Reasoning-Modell.

#### 2.1 Datenquellen & Parsing

* **JSON-Run-Reports (`logs/run_<id>.json`)**:
* Extraktion globaler Kennzahlen: Sharpe Ratio, Sortino Ratio, Max Drawdown (MTM & Realized), Calmar Ratio, Profit Factor, Expected Value pro Trade in CHF.
* Extraktion statistischer Overfitting-Metriken: Probability of Backtest Overfitting (PBO), Deflated Sharpe Ratio (DSR), Fold-Dispersion, Tail-Risk-Statistiken.


* **Aggregierte Markdown-Reports (`logs/zusammenfassung_<id>.md`)**:
* Auslesen der Symbol-spezifischen Performance-Divergenzen und Sizing-Effizienz.


* **Deployment-Gate-Logs (`logs/gate_eval_<id>.json`)**:
* Veto-Gründe: Collinearity Rejection, Minimum Trade Threshold Failures, Out-of-Sample Degeneration, Slippage/Spread Degradation.



#### 2.2 Payload-Strukturierung für `deepseek-reasoner`

* Serialisierung der letzten $N$ Läufe (Standard: $N=3$) zur Vermeidung von Parameter-Oszillation:

```json
{
  "strategy_name": "ComboTrendVWAP",
  "symbol": "EURUSD",
  "current_iteration": 4,
  "history": [
    {
      "iteration": 3,
      "metrics": {
        "sharpe": 1.12,
        "sortino": 1.45,
        "pbo": 0.42,
        "dsr": 0.58,
        "max_dd_chf": -420.50,
        "trade_count": 84
      },
      "rejection_reasons": ["PBO_ABOVE_THRESHOLD", "FOLD_CONSISTENCY_FAILED"],
      "parameters": { "vwap_window": 48, "deviation_mult": 1.8 }
    }
  ]
}

```

---

### 3. Phase 2 – Zweistufige KI-Anpassung (Hypothese & Codegen)

Trennung von quantitativer Ursachenanalyse und konkreter Code-/Konfigurationserzeugung durch Kaskadierung von R1 und V3.

```
+-------------------------------------------------------------------------------------+
| Ingestion Context (KPIs, Rejections, Code)                                          |
+-------------------------------------------------------------------------------------+
                                          |
                                          v
+-------------------------------------------------------------------------------------+
| deepseek-reasoner (R1)                                                              |
| Task:                                                                               |
| 1. Identifiziere primären Rejection-Treiber (z. B. False Breakouts bei hoher Vola). |
| 2. Wähle Optimierungspfad:                                                          |
|    - Pfad A: Parameter-Suchraum fehlerhaft (Hyperparameter-Shift nötig).             |
|    - Pfad B: Signallogik defizitär (Indikator-Modifikation, Filter-Hinzufügung).    |
| Output: Reasoning Trace + Strukturierter Aktionsplan (Hypothese).                   |
+-------------------------------------------------------------------------------------+
                                          |
                                          v
+-------------------------------------------------------------------------------------+
| deepseek-chat (V3)                                                                  |
| Task:                                                                               |
| - Pfad A: JSON-Generierung für 'automation/config/search_space_overrides.json'      |
| - Pfad B: Vollständige Code-Synthese für 'automation/strategies/<strategy>.py'      |
| Constraint: Streng typisiert, nur modifizierte Artefakte im Antwort-JSON.           |
+-------------------------------------------------------------------------------------+

```

#### 3.1 Tier 1: Reasoning & Hypothesenformulierung (`deepseek-reasoner`)

* **Prompt-Architektur**:
* Input: Leistungsmetriken, Fehlermuster, bestehende Logik, Rejection-Gründe.
* Anforderung: Formulierung einer mathematisch/ökonomisch begründeten Hypothese (z. B. *„Hohe Fold-Varianz resultiert aus mangelnder Volatilitätsanpassung der Stop-Distanz in Trendwenden; ATR-Filterung muss dynamisiert werden.“*).
* Pfad-Entscheidung: Pfad A (Parameter-Raum) vs. Pfad B (Logik-Mutation).



#### 3.2 Tier 2: Synthese & Modifikation (`deepseek-chat`)

* **Pfad A (Parameter-Suchraum)**:
* Generierung präziser Bounds und Schritte in `automation/config/search_space_overrides.json`.
* Anpassung von Prior-Verteilungen für den TPE/Bayesian-Sampler.


* **Pfad B (Logik-Evolution)**:
* Modifikation der Strategieklasse in `automation/strategies/<strategy_name>.py`.
* Synthese neuer Entry-/Exit-Klauseln unter Verwendung verifizierter NautilusTrader-Indikatoren (`nautilus_trader.indicators`).



---

### 4. Phase 3 – Lokale Vorab-Prüfung (Static Validation)

Vor der Übergabe an ressourcenintensive Backtests durchläuft der generierte Code ein automatisiertes Prüfgitter.

```
              +-----------------------------------------+
              | Synthetisierter Code / JSON Search Space|
              +-----------------------------------------+
                                   |
                                   v
             [ 1. Syntax & AST Lookahead-Bias Check ] -------- Fail --+
                                   | Pass                             |
                                   v                                  |
             [ 2. Linting & Types: ruff / mypy ] ------------- Fail --+
                                   | Pass                             |
                                   v                                  |
             [ 3. Unit Tests: pytest Strategy Execution ] ---- Fail --+
                                   | Pass                             |
                                   v                                  |
                   Bereit für lokalen Backtest                        |
                                                                      v
                                                    +-----------------------------------+
                                                    | Self-Correction Prompt            |
                                                    | (Fehlerprotokoll -> deepseek-chat)|
                                                    +-----------------------------------+

```

#### 4.1 Validierungsstufen

1. **AST & NautilusTrader-Sicherheit**:
* Prüfung via `ast`: Verbot von blockierenden Calls (`time.sleep`, synchrone Netzwerk-Sockets, ungecachte Disk-I/O im Bar-Loop).
* Lookahead-Bias-Check: Verbot des Zugriffs auf Bar-Attribute der aktuellen Periode vor deren Abschluss (z. B. unvollständige Bar-Close-Preise im Tick-Context).


2. **Linting & Type Integrity**:
* Ausführung: `ruff check automation/strategies/`
* Ausführung: `mypy --strict automation/strategies/<strategy_name>.py`


3. **Deterministische Unit-Tests**:
* Ausführung isolierter Testcases (`pytest automation/tests/test_strategy_execution.py`) mit synthetischen Mock-Bars zur Sicherstellung, dass Orders korrekt generiert werden.



#### 4.2 Feedback-Schleife bei Validierungsfehlern (Self-Healing)

* Bei Fehlern in Stufe 1–3: Rückgabe des `stderr`- und Test-Tracebacks an `deepseek-chat` mit dem Befehl zur minimalen Fehlerkorrektur.
* Abbruchbedingung: Nach $M$ Fehlversuchen (Standard: $M=3$) wird die Mutation verworfen, der vorherige Zustand via Git restauriert und ein Rejection-Log generiert.

---

### 5. Phase 4 – Lokale Backtest- & Sweep-Ausführung

Ausführung des rechenintensiven Kerns direkt auf der lokalen Hardware unter Nutzung der existierenden Multiprocessing-Pipelines.

#### 5.1 CLI-Steuerung

* Kapselung des Aufrufs via `subprocess` mit striktem Timeout und isolierten Run-IDs:

```bash
python -m automation.optimizer.run_optimization \
  --strategy ComboTrendVWAP \
  --symbol EURUSD \
  --config automation/config/optimizer.json \
  --search-space-overrides automation/config/search_space_overrides.json \
  --workers 16 \
  --cpcv-folds 6 \
  --output-dir logs/ai_loop_runs/

```

#### 5.2 Lokale Validierungs-Features

* **Combinatorial Purged Cross-Validation (CPCV)**: $N=6$ Folds mit Purging- und Embargo-Fenstern zur Vermeidung von Leakage.
* **Stresstests**: Simulation von variablem Slippage (1.0x bis 3.0x Median-Spread) und Execution-Delays.
* **Risk & Sizing Engine**: Validiere Performance unter Berücksichtigung von Fractional Kelly Sizing und minimalen CHF-Positionsgrössen.

---

### 6. Phase 5 – Gate-Bewertung & Feedback-Schleife

Evaluation der Backtest-Ergebnisse über das integrierte Deployment Gate und Einpflegen in die Champion Registry oder den Feedback-Speicher.

#### 6.1 Gate-Evaluation (`deployment_gate.py`)

* Kriterien für Status **PROMOTED**:
* Sharpe Ratio $\ge 1.30$, Sortino Ratio $\ge 1.60$ über alle Folds.
* PBO (Probability of Backtest Overfitting) $\le 0.25$.
* Deflated Sharpe Ratio (DSR) $\ge 0.95$.
* Max Intra-Trade Drawdown $\le 500\text{ CHF}$ (pro Standard-Tranche).
* Statistische Signifikanz: Mindestens 100 Fills im In-Sample- und Out-of-Sample-Fenster.



#### 6.2 Status-Verarbeitung & Registry

* **Erfolg (Gate Passed)**:
1. Commit des Champion-Status in `automation/optimizer/champions.py`.
2. Backup des Strategie-Codes und der Hyperparameter in `automation/champions_archive/<strategy>_<timestamp>.py`.
3. Git-Commit mit strukturierter Commit-Message (`feat(ai-loop): promote champion ComboTrendVWAP Sharpe=1.42 PBO=0.18`).


* **Misserfolg (Gate Rejected)**:
1. Extraktion der exakten Rejection-Vektoren aus dem Gate-Objekt (`GateEvaluationResult.rejection_reasons`).
2. Rollback des Strategie-Codes auf den letzten Champion-Stand via `git checkout`.
3. Anhängen des Veto-Kontexts an `logs/ai_optimization_ledger.jsonl`.
4. Nächster Schleifendurchlauf startet mit dem aktualisierten Rejection-Kontext als negativer Prompt-Filter für `deepseek-reasoner`.



---

### 7. Implementierungs-Roadmap

```
+-----------------------------------------------------------------------------+
| Phase 1: AI Engine Scaffold (Client, Parsing, Ledger)                       |
| - Implementierung async Client für DeepSeek R1/V3                           |
| - JSON/Markdown Parser für logs/                                            |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
| Phase 2: Static Verification & Self-Healing                                 |
| - AST Safety Checker (Lookahead / Block-Calls)                              |
| - Automatisierter ruff/mypy/pytest Subprocess Wrapper                       |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
| Phase 3: Orchestrator Loop & CLI Integration                                |
| - State Machine Implementierung (Ingest -> Reason -> Synth -> Run -> Gate)  |
| - Rollback- & Git-Commit-Mechanismus                                        |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
| Phase 4: E2E Testlauf & Härtung                                             |
| - Testlauf mit einer Baseline-Strategie (z. B. 'vwap_exhaustion')           |
| - Verifikation der Konvergenz gegen PBO-Schwellenwerte                      |
+-----------------------------------------------------------------------------+

```

#### Meilensteine

* **Meilenstein 1 (Core Scaffold)**: `automation/ai_loop/client.py` und `ingestion.py` sind funktionsfähig und extrahieren strukturierte Metriken aus existierenden `logs/`-Dateien.
* **Meilenstein 2 (Pre-Flight Engine)**: `validator.py` blockiert nachweislich fehlerhaften Code (Syntaktisch, Lookahead, Typen) und triggert den V3-Korrektur-Prompt.
* **Meilenstein 3 (Full Closed Loop)**: Vollautonomer 5-Schritte-Lauf über mindestens 5 Iterationen auf lokaler Hardware mit sauberem Rollback bei Gate-Veto und Champion-Persistenz bei Promotion.
