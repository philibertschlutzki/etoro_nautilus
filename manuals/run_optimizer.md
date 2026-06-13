# Bedienungshandbuch: Closed-Loop Autotuner (`run_optimizer.md`)

> **System:** eToro Nautilus v2.0 — Paket `automation/optimizer/`
> **Zielgruppe:** Einsteiger, die den automatisierten Parameter-Optimierer **bedienen** wollen (nicht weiterentwickeln).
> **Was dieses Handbuch leistet:** Es beschreibt jede Funktion des Optimierers, den vollständigen Bedienablauf, den Promotions-Prozess und das Error-Handling — und es macht **explizit auf Abweichungen zwischen Dokumentation und Code aufmerksam**, die du vor dem ersten Lauf kennen musst.

---

## 1. Was der Optimizer macht (in einem Satz)

Der Autotuner ersetzt den manuellen Regelkreis *„Parameter raten → Backtest → auswerten → wiederholen"* durch einen **Optuna-TPE-Sampler**: er schlägt Strategie-Parameter vor, fährt pro Vorschlag **einen** Walk-Forward-Backtest gegen das ganze Instrumenten-Universum, bewertet das Ergebnis mit einem Reward, verdichtet die Suche in erfolgreichen Regionen und prüft am Ende den besten Fund gegen ein **nie gesehenes Holdout-Fenster**. Nur wenn der Holdout besteht, entsteht ein Vorschlag (Proposal) für einen Pull Request — **deployt wird nie automatisch**.

### Datenfluss

```
            ┌───────────────────────────────────────────────┐
            │            Optuna TPESampler (1c)             │
            │  schlägt Parameter vor (keltner_period, …)    │
            └───────────────────────┬───────────────────────┘
                                    │ sample_params()  (spaces.py)
                                    ▼
            ┌───────────────────────────────────────────────┐
            │   Trial-Isolierung + Manifest  (trial_config) │
            │  data/optimizer/study_<S>/trial_0042/         │
            │  ├─ config/   (eingefrorene *.json kopiert)   │
            │  └─ experiment_manifest.json (1 Strategie)    │
            └───────────────────────┬───────────────────────┘
                                    │ run_backtest()  (runner.py)
                                    ▼
            ┌───────────────────────────────────────────────┐
            │   Subprozess: automation/backtest_runner.py   │
            │  4-Fold Walk-Forward, Tournament (Phase 3+4)  │
            │  → trial_0042/tournament_result.json          │
            └───────────────────────┬───────────────────────┘
                                    │ parse_tournament()  (parsing.py)
                                    ▼
            ┌───────────────────────────────────────────────┐
            │   Reward-Kalkulation  (reward.py)             │
            │  base − overfit − dd_excess + coverage        │
            └───────────────────────┬───────────────────────┘
                                    │  zurück an Optuna; N-mal iterieren
                                    ▼  (nach n_trials / Konvergenz)
            ┌───────────────────────────────────────────────┐
            │   Holdout-Bestätigung (confirm.py)            │
            │  bester Trial gegen ungesehene Tage prüfen    │
            │  → proposal_<Strategie>.json                  │
            │     status = READY_FOR_PR | REJECTED_ON_HOLDOUT│
            └───────────────────────────────────────────────┘
```

---

## 2. Sicherheits-Leitplanken (was der Optimizer **nie** tut)

Diese fünf Invarianten sind hart und nicht konfigurierbar. Verlasse dich darauf — und überschreibe sie nicht:

1. **Kein Live-Deploy.** Der Optimizer ruft `backtest_runner.py` direkt (nur Phase 3+4). Phase 5 (Live-Bot) wird **nie** betreten. `tournament.json` wird **nie** verändert.
2. **Risiko-Gates eingefroren.** `max_drawdown`, `min_win_rate`, `min_expectancy`, `min_total_return`, `oos_min_*` aus `tournament.json` werden 1:1 in jedes Trial kopiert und nie variiert. Optimiert werden **ausschließlich Strategie-Parameter**.
3. **Holdout unberührt.** Während der Suche bleiben die jüngsten `holdout_days` (Default 45) komplett ausgespart. Erst der finale Bestätigungslauf sieht sie.
4. **Human-in-the-Loop.** Promotion erfolgt nur über einen manuell freigegebenen Git-PR. Kein Auto-Commit.
5. **Reproduzierbarkeit.** Jedes Trial ist durch ein vollständig aufgelöstes `experiment_manifest.json` (inkl. Git-Hash und Katalog-Fingerprint) zu 100 % nachvollziehbar.

---

## 3. Voraussetzungen & Umgebung

| Voraussetzung | Detail |
|---|---|
| **Arbeitsverzeichnis** | **Starte den Optimizer immer aus dem Projekt-Stammverzeichnis** (`PROJECT_ROOT`). Sowohl `runner.py` (relativer Aufruf `automation/backtest_runner.py`, kein `cwd`) als auch `reward.py` (relativer Pfad `automation/config/optimizer.json`) verlassen sich auf das CWD. Aus einem anderen Verzeichnis startest → `FileNotFoundError`. |
| **Python** | `runner.py` ruft den Backtest mit dem bloßen Kommando `python` (nicht `python3`, nicht `sys.executable`) auf. Stelle sicher, dass `python` im `PATH` auf deinen Projekt-Interpreter (≥ 3.11) zeigt. Andernfalls: `python` auf `python3` symlinken oder den Aufruf in `runner.py` anpassen. |
| **Eingefrorener Katalog** | Vor dem ersten Lauf **einmal** Phase 1/2 regulär fahren (Universe + Backfill), danach den Katalog `data/nautilus/` **nicht mehr anfassen**. `catalog_fingerprint()` protokolliert den Zustand; ändert er sich zwischen Läufen, sind die Trials nicht mehr vergleichbar. |
| **Abhängigkeit** | `optuna` muss installiert sein (zusätzlich zu den `automation/requirements.txt`-Paketen). |

### Optionale Umgebungsvariablen

Du brauchst sie für den Normalbetrieb **nicht** zu setzen — der Runner injiziert pro Trial automatisch isolierte Pfade. Setze sie nur, wenn du die Quelle der eingefrorenen Config bewusst umlenken willst:

```bash
# Nur falls du eine ANDERE Config-Quelle als automation/config verwenden willst:
export ETORO_CONFIG_DIR="$(pwd)/automation/config"
# Wird vom Optimizer-Prozess von config_dir() gelesen; build_trial kopiert von hier.
```

> **Wichtig:** `ETORO_LOGS_DIR` musst du **nicht** global setzen. `runner.py` setzt es pro Subprozess auf `trial_dir/logs`. (Hinweis: Dieses `logs/`-Verzeichnis wird vom aktuellen `build_trial` **nicht** vorab angelegt — siehe Befund B5 in Abschnitt 10.)

---

## 4. Konfiguration

Der Optimizer liest aus **drei** Dateien in `automation/config/`. Du steuerst sein Verhalten praktisch vollständig über `optimizer.json`.

### 4.1 `optimizer.json` — Sampler & Reward-Gewichte

```json
{
  "n_trials": 100,
  "n_startup_trials": 16,
  "seed": 42,
  "sortino_clip_abs": 5.0,
  "penalty_overfit_weight": 0.5,
  "penalty_dd_weight": 8.0,
  "bonus_coverage_weight": 1.0,
  "penalty_unevaluable_oos": -10.0
}
```

| Key | Bedeutung | Gelesen von |
|---|---|---|
| `n_trials` | Anzahl Trials pro Study (Default, falls nicht per Argument überschrieben). | `optimize()` |
| `n_startup_trials` | Zufalls-Trials, bevor TPE das probabilistische Modell nutzt. | `optimize()` (Sampler) |
| `seed` | Fixer Seed für Reproduzierbarkeit (Sampler **und** Manifest). | `optimize()`, `make_objective()`, `confirm_on_holdout()` |
| `sortino_clip_abs` | Kappung des OOS-Sortino vor der Reward-Berechnung (`±5.0`). Verhindert, dass Ausreißer den Reward dominieren. | `reward.py` |
| `penalty_overfit_weight` | Strafgewicht für die IS↔OOS-Lücke (`is_sortino_median − base`). | `reward.py` |
| `penalty_dd_weight` | Strafgewicht für Drawdown **über** dem `max_drawdown`-Cap. | `reward.py` |
| `bonus_coverage_weight` | Bonus für Universe-Abdeckung (`win_count / universe_size`). | `reward.py` |
| `penalty_unevaluable_oos` | Konstanter Reward, wenn das OOS-Fenster nicht auswertbar ist (Default `-10.0`). **Kein** Fehler — ein Signal. | `reward.py` |

> **Alle diese Keys müssen vorhanden sein.** `reward.py` greift mit hartem Index (`weights["…"]`) zu — fehlt ein Key, gibt es einen `KeyError` (siehe Abschnitt 9).

### 4.2 `backtest.json` → Block `walk_forward` — Zeitfenster

`build_trial()` berechnet das Backtest-Fenster aus diesem Block:

| Key | Default (falls fehlend) | Wirkung |
|---|---|---|
| `holdout_days` | `45` | Tage, die vom Korridor ausgespart bleiben (während der Suche unsichtbar). |
| `splits` | `1` | Anzahl Walk-Forward-Folds **während der Suche**. Konzept empfiehlt `4`. |
| `is_window_days` | `120` | In-Sample-Tage pro Fenster. |
| `oos_window_days` | `30` | Out-of-Sample-Tage pro Fold. |

**Fensterformel** (in `build_trial`):
```
end   = Mitternacht(now);   wenn Sonntag → ein Tag zurück (Freitag-EOD-Konvention)
end  -= holdout_days
start = end − (is_window_days + n_folds · oos_window_days)
```

### 4.3 `tournament.json` — eingefrorene Risiko-Gates

Wird **nicht** verändert, nur gelesen: Der Reward holt sich `max_drawdown` als Drawdown-Cap (`risk_dd_cap`), und die Holdout-Bestätigung nutzt denselben Wert als Pass-Schwelle. Der SHA-256 dieser Datei landet zur Beweissicherung im Manifest (`frozen_tournament_sha256`).

---

## 5. Bedienung — Schritt für Schritt

### 5.1 Schnellstart (TL;DR)

```python
# Aus dem PROJEKT-STAMMVERZEICHNIS, in einer Python-Shell oder einem Skript:
from automation.optimizer.run_optimization import run
run("HourlyMeanReversionStrategy")
# -> optimiert sequ(n_jobs=1), bestätigt am Holdout, schreibt proposal_*.json
```

### 5.2 Voller Lauf über die Python-API (empfohlener Weg, da keine CLI existiert)

Die Funktion `run(strategy)` ist die einzige, die die **gesamte Pipeline** ausführt (Optimierung → Holdout → Proposal). Sie ist aber auf `n_jobs=1` (sequentiell) festgelegt:

```python
from automation.optimizer.run_optimization import run

run("HourlyMeanReversionStrategy")   # oder "SmaCrossoverStrategy"
```

Erlaubte Strategie-Namen (Stand Code): **`HourlyMeanReversionStrategy`**, **`SmaCrossoverStrategy`**.

### 5.3 Parallele Trials (manuelle Pipeline)

Parallelität (`n_jobs > 1`) bietet nur `optimize()`, **nicht** `run()`. Willst du parallel fahren, setzt du die Pipeline selbst zusammen:

```python
from automation.optimizer.run_optimization import optimize
from automation.optimizer.confirm import confirm_on_holdout, export_proposal

strat = "HourlyMeanReversionStrategy"

study   = optimize(strat, n_jobs=4)            # 4 parallele Trials (Threads)
holdout = confirm_on_holdout(study, strat)     # bester Fund vs. Holdout
path    = export_proposal(study, strat, holdout)
print("Proposal:", path)
```

> **SQLite + Parallelität:** Bei `n_jobs > 4` gegen die lokale SQLite-DB drohen `database is locked`-Fehler. Für echte Parallelität auf eine PostgreSQL-`STORAGE`-URL wechseln (Abschnitt 9, Eintrag *database is locked*).
> **Core-Budgetierung:** Jeder Trial startet selbst einen Backtest mit internem ProcessPool (bis `cpu//2` Worker). Halte `parallele_Trials × interne_Worker ≤ Kerne`, sonst überbuchst du die CPU.

---

## 6. Was während des Laufs entsteht

Alles landet unter `data/optimizer/` (Konstante `WORK`):

```
data/optimizer/
├── studies.db                                   # Optuna-SQLite-DB (alle Trials, Warm-Start)
├── study_HourlyMeanReversionStrategy/
│   ├── trial_0000/
│   │   ├── config/                              # Kopie der eingefrorenen *.json
│   │   ├── experiment_manifest.json             # 100 % reproduzierbarer Backtest-Vertrag
│   │   ├── logs/                                # Subprozess-Logs (ETORO_LOGS_DIR)
│   │   └── tournament_result.json               # Ergebnis dieses Trials
│   ├── trial_0001/ …
│   └── …
├── study_HourlyMeanReversionStrategy_holdout/   # finaler Bestätigungslauf
│   └── trial_<best>/ …
└── proposal_HourlyMeanReversionStrategy.json    # Endergebnis (siehe Abschnitt 7)
```

- **`studies.db`** persistiert alle `(Parameter, Reward)`-Paare. Beim nächsten Lauf mit gleichem `study_name` erbt der Sampler diese Historie (`load_if_exists=True`) → **Warm-Start = Verstärkung über Nächte hinweg**.
- **`experiment_manifest.json`** enthält Provenienz (`git_commit`, `data_snapshot_sha256`, `frozen_tournament_sha256`), `global_settings` (Zeitfenster, Seed) und **genau eine** aktivierte Strategie mit vollständig aufgelösten Parametern. Mit dieser Datei lässt sich jeder Backtest exakt nachstellen.

### Fortschritt beobachten

Optuna loggt jeden abgeschlossenen Trial mit Reward auf stdout. Zusätzlich kannst du die DB live abfragen:

```python
import optuna
from automation.optimizer.run_optimization import STORAGE
study = optuna.load_study(study_name="study_HourlyMeanReversionStrategy", storage=STORAGE)
print("Best reward:", study.best_value)
print("Best params:", study.best_trial.user_attrs.get("sampled_params"))
print(study.trials_dataframe()[["number", "value", "state"]])
```

---

## 7. Der Promotions-Prozess (vom Fund zum PR)

Nach `n_trials` läuft die Bestätigung automatisch (wenn du `run()` bzw. die Pipeline aus 5.3 nutzt):

### Schritt 1 — Holdout-Bestätigung (`confirm_on_holdout`)

Der beste Trial wird mit `holdout_days=0, n_folds=1` erneut gefahren — dadurch fällt das OOS-Fenster dieses Laufs **in die zuvor reservierten Tage**, die der Sampler nie gesehen hat. **Bestanden** (`passed=True`) gilt nur, wenn **alle vier** Bedingungen erfüllt sind:

```
oos_evaluated == True
oos_eligible  == True
oos_sortino    > 0.0          (None wird als 0.0 behandelt → durchgefallen)
oos_max_drawdown <= risk_dd_cap   (= tournament.json max_drawdown, Fallback 0.30)
```

### Schritt 2 — Proposal-Export (`export_proposal`)

Ergebnis: `data/optimizer/proposal_<Strategie>.json`.

**Szenario A — bestanden (`READY_FOR_PR`):**
```json
{
  "strategy": "HourlyMeanReversionStrategy",
  "status": "READY_FOR_PR",
  "reward": 2.45,
  "proposed_params_override": {
    "keltner_period": 24,
    "keltner_atr_period": 14,
    "keltner_multiplier": 2.1,
    "cooldown_bars": 12,
    "atr_trailing_multiplier": 1.5,
    "max_bars_in_trade": 48
  },
  "holdout": {
    "passed": true,
    "metrics": { "oos_sortino": 1.25, "oos_max_drawdown": 0.14,
                 "oos_evaluated": true, "oos_eligible": true },
    "trial_dir": "data/optimizer/study_…_holdout/trial_…"
  }
}
```

**Szenario B — durchgefallen (`REJECTED_ON_HOLDOUT`):** identische Struktur, `status: "REJECTED_ON_HOLDOUT"`, `holdout.passed: false`. **Diese Parameter dürfen nicht in Produktion.** Ein Reject ist ein Erfolg des Systems — es hat Meta-Overfitting abgefangen.

### Schritt 3 — Einspielen (menschliches Review)

Nur bei `READY_FOR_PR`:

1. Werte aus `proposed_params_override` kopieren.
2. In `automation/config/strategies.json` den `params`-Block der Strategie überschreiben (`params` hat Vorrang vor `strategy_defaults.json`).
3. Branch anlegen (z. B. `feature/opt-hourly-mean-reversion`), PR mit angehängtem Holdout-Ergebnis + Overfit-Gap zur Review einreichen.
4. **Kein** Auto-Commit, **kein** Auto-Deploy.

---

## 8. Funktionsreferenz (vollständig)

Jede Funktion des Pakets `automation/optimizer/`. Reihenfolge entspricht dem Datenfluss.

### 8.1 `manifest.py` — Pfade & Provenienz

| Objekt | Signatur | Zweck | Fehler |
|---|---|---|---|
| `PROJECT_ROOT` | Konstante | Repo-Wurzel (drei Ebenen über `manifest.py`). | — |
| `WORK` | Konstante | `PROJECT_ROOT/data/optimizer` — Arbeitsverzeichnis (DB, Trials, Proposals). | — |
| `git_commit()` | `() -> str` | Git-Short-Hash für die Provenienz; `"unknown"` wenn kein Git verfügbar. | Fängt jede Exception → `"unknown"`. |
| `sha256_file(path)` | `(Path) -> str` | SHA-256 einer Datei (streamend, 8-KB-Chunks). Für `frozen_tournament_sha256`. | `FileNotFoundError`, falls Pfad fehlt. |
| `catalog_fingerprint(catalog=None)` | `(Path \| None) -> str` | Stabiler Fingerprint über alle `data.parquet` (`relpath:size:mtime`). Default-Katalog `PROJECT_ROOT/data/nautilus`. | Robuste Rückgaben: `"unknown_catalog_missing"` (Pfad fehlt), `"empty_catalog"` (keine Parquets). Keine Exception. |

### 8.2 `resolve.py` — Parameter-Mergen

| Funktion | Signatur | Zweck | Fehler |
|---|---|---|---|
| `resolve_params` | `(strategy_class: str, sampled: dict, base_cfg: Path) -> dict` | Merge-Reihenfolge **niedrig→hoch**: `strategy_defaults.json` < `strategies.json[params]` < `sampled`. Liefert die autoritativen Parameter fürs Manifest. | Fehlt eine Datei, wird sie still übersprungen (`.exists()`-Guard) — kein Fehler, ggf. leeres Dict. |

> Erwartet `strategy_defaults.json` mit Top-Level-Key = Strategie-Klassenname und `strategies.json` mit Liste `strategies[]`, Match über `strategy_class`.

### 8.3 `spaces.py` — Suchräume

| Funktion | Signatur | Zweck | Fehler |
|---|---|---|---|
| `sample_params` | `(strategy: str, trial) -> dict` | Schlägt pro Trial die Parameter vor. **Implementiert:** `HourlyMeanReversionStrategy` (`keltner_period` 6–40, `keltner_atr_period` 6–40, `keltner_multiplier` 1.0–3.5, `cooldown_bars` 2–36, `atr_trailing_multiplier` 0.3–2.5, `max_bars_in_trade` 12–96); `SmaCrossoverStrategy` (`sma_period` 5–60, `cooldown_bars` 2–36). | **Jede andere Strategie → `ValueError(f"Unknown strategy: {strategy}")`**. |

### 8.4 `trial_config.py` — Trial-Isolierung & Manifest

| Funktion | Signatur | Zweck | Fehler |
|---|---|---|---|
| `config_dir()` | `() -> Path` | `ETORO_CONFIG_DIR` (falls gesetzt), sonst `automation/config`. | — |
| `build_trial` | `(strategy_class, sampled, *, study_name, trial_number, seed, now=None, holdout_days=None, n_folds=None, base_cfg=None) -> (Path, Path)` | Legt isoliertes `trial_dir` an, kopiert alle `config/*.json`, berechnet das Zeitfenster (Holdout-Aussparung, Sonntag→Samstag-Rollback) und schreibt `experiment_manifest.json` mit **genau einer** aufgelösten Strategie. `now` ist für Tests injizierbar (deterministische Fenster). | Liest `backtest.json` → `FileNotFoundError`, falls die Config-Datei fehlt. |

### 8.5 `parsing.py` — Tournament-JSON → Metriken

| Objekt | Signatur | Zweck | Fehler |
|---|---|---|---|
| `TournamentMetrics` | dataclass | Felder: `oos_evaluated`, `oos_eligible`, `is_sortino_median`, `oos_sortino`, `oos_max_drawdown`, `oos_total_trades`, `win_count`, `fully_eligible_pairs`. | — |
| `parse_tournament` | `(path: Path) -> TournamentMetrics` | Liest `aggregate_winner`/`oos_metrics` **typsicher** (None-safe Casts). `oos_sortino` = Median von `oos_fold_sortinos` (falls Liste vorhanden), sonst `oos_metrics.sortino_ratio`. `is_sortino_median` = `median_is_sortino` ?? `median_sortino` ?? `0.0`. | `FileNotFoundError` (Datei fehlt), `json.JSONDecodeError` (kaputtes JSON). Fehlende Felder → Defaults. |

### 8.6 `reward.py` — Zielfunktion

| Funktion | Signatur | Zweck | Fehler |
|---|---|---|---|
| `compute_reward` | `(m: TournamentMetrics, universe_size: int, weights: dict\|None=None, risk_dd_cap: float\|None=None) -> float` | Berechnet den skalaren Reward. **Frühausstieg:** wenn `not m.oos_evaluated` **oder** `m.oos_sortino is None` → Rückgabe `penalty_unevaluable_oos` (Default `-10.0`). Sonst: `base = clip(oos_sortino, ±sortino_clip_abs)`; `reward = base − overfit_gap·penalty_overfit_weight − dd_excess·penalty_dd_weight + coverage·bonus_coverage_weight`, mit `overfit_gap = max(0, is_sortino_median − base)`, `dd_excess = max(0, oos_max_drawdown − risk_dd_cap)`, `coverage = win_count / max(1, universe_size)`. | `weights=None` → liest `automation/config/optimizer.json` **relativ zum CWD** (`FileNotFoundError` bei falschem CWD). `risk_dd_cap=None` → liest `tournament.json["max_drawdown"]` hart (`KeyError`). Fehlende Reward-Keys → `KeyError`. |

> Kein Gate-Flag im Reward → der Optimizer hat **keinen** Anreiz, Schwellen zu lockern (Anti-Gate-Gaming).

### 8.7 `runner.py` — Subprozess-Backtest

| Funktion | Signatur | Zweck | Fehler |
|---|---|---|---|
| `run_backtest` | `(trial_dir: Path, manifest_path: Path) -> Path` | Liest `catalog_path` aus `manifest.global_settings`, startet `automation/backtest_runner.py` als Subprozess (`--momentum --catalog-path … --config <manifest> --output <trial>/tournament_result.json`), `timeout=10800`, `check=False`. Env: `ETORO_CONFIG_DIR=trial/config`, `ETORO_LOGS_DIR=trial/logs`, `PYTHONUNBUFFERED=1`. Gibt den Output-Pfad zurück. | **`ValueError("Missing catalog_path …")`** wenn das Manifest es nicht enthält. `subprocess.TimeoutExpired` bei > 3 h. **`FileNotFoundError`**, wenn `tournament_result.json` nach dem Lauf fehlt (Backtest-Crash; `check=False` schluckt den Exit-Code). |

### 8.8 `confirm.py` — Holdout & Proposal

| Funktion | Signatur | Zweck | Fehler |
|---|---|---|---|
| `confirm_on_holdout` | `(study, strategy, *, run_backtest=…, build_trial=…) -> dict` | Baut einen Holdout-Trial (`holdout_days=0, n_folds=1`), fährt ihn, wertet die vier Pass-Kriterien aus (Abschnitt 7). `run_backtest`/`build_trial` sind als Dependency-Injection-Hooks für Tests parametrierbar. Rückgabe: `{passed, metrics, trial_dir}`. | Reicht Fehler aus `build_trial`/`run_backtest`/`parse_tournament` durch (u. a. B1). |
| `export_proposal` | `(study, strategy, holdout) -> Path` | Schreibt `proposal_<strategy>.json` mit `status = READY_FOR_PR \| REJECTED_ON_HOLDOUT`, `reward = best_trial.value`, `proposed_params_override = best_trial.user_attrs["sampled_params"]`, plus dem `holdout`-Dict. | `IOError`, falls `WORK` nicht beschreibbar (wird via `mkdir(parents=True, exist_ok=True)` abgesichert). |

### 8.9 `run_optimization.py` — Orchestrierung

| Objekt | Signatur | Zweck | Fehler |
|---|---|---|---|
| `STORAGE` | Konstante | `sqlite:///…/studies.db`. Für echte Parallelität auf Postgres umstellen. | — |
| `make_objective` | `(strategy: str) -> objective` | Liefert Optunas Zielfunktion: `sample_params` → `build_trial` → `run_backtest` → `parse_tournament` → `universe_size` ermitteln → `compute_reward`. `universe_size` = Anzahl Einträge in `per_symbol_winners` (Fallback `fully_eligible_pairs`, dann `1`). | Jede Exception in `objective` **stoppt standardmäßig die ganze Study** (Optuna ohne `catch=`). Siehe Abschnitt 9 (Trial-Isolation). |
| `optimize` | `(strategy: str, n_trials: int\|None=None, n_jobs: int=1) -> study` | Erzeugt `TPESampler(multivariate=True, group=True, n_startup_trials, seed)`, legt Study mit `load_if_exists=True` an (Warm-Start), protokolliert `data_snapshot_sha256`, ruft `study.optimize(...)`. | `sqlite3.OperationalError: database is locked` bei `n_jobs>1` auf SQLite. |
| `run` | `(strategy: str) -> None` | **Vollpipeline:** `optimize` → `confirm_on_holdout` → `export_proposal`. ⚠️ festverdrahtet auf `n_jobs=1`. | Reicht alle obigen Fehler durch. |

---

## 9. Error-Handling — wie es funktioniert (und funktionieren soll)

### 9.1 Grundphilosophie: Fail-Closed + Reproduzierbarkeit

Der Optimizer ist bewusst **fail-closed** ausgelegt: Im Zweifel **kein** Vorschlag statt eines fragwürdigen. Es gibt zwei Klassen von „Fehlern":

- **Harte Fehler (Exceptions):** brechen den Trial — und mangels `catch=` aktuell die **ganze Study** — ab. Beispiele: fehlendes Manifest-Feld, Backtest-Crash, Timeout, kaputtes JSON.
- **Weiche Signale (kein Crash):** Ein Trial, dessen OOS nicht auswertbar ist, bekommt den Reward `penalty_unevaluable_oos` (`-10.0`). Das ist **kein** Fehler, sondern die korrekte Bestrafung einer uninformativen Region. Wenn **alle** Trials auf `-10.0` hängen, ist das ein Hinweis auf einen Konfigurations- oder Suchraum-Defekt — nicht auf einen Absturz.

### 9.2 Trial-Isolation: Soll-Verhalten bei vielen Trials

Damit ein einzelner kaputter Trial nicht die ganze Nacht-Optimierung killt, sollte `study.optimize` Trial-Fehler **einfangen** statt durchzureichen. Zwei saubere Optionen:

```python
# Option A: Optuna fängt die Exception, markiert den Trial FAILED, läuft weiter.
study.optimize(make_objective(strategy), n_trials=n_trials, n_jobs=n_jobs,
               catch=(Exception,))
```

```python
# Option B (granularer): in der objective recoverable Fehler abfangen
def objective(trial):
    try:
        ...
        return compute_reward(metrics, universe_size=universe_size, risk_dd_cap=risk_dd_cap)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        # uninformatives Ergebnis statt Study-Abbruch
        return float(weights["penalty_unevaluable_oos"])
```

> **Empfehlung:** Option A für den nächtlichen Batch (Robustheit), kombiniert mit Logging, damit FAILED-Trials hinterher sichtbar sind (`study.trials_dataframe()` → Spalte `state`). Harte Konfigurationsfehler (z. B. B1) **nicht** wegfangen — die sollen laut auffallen.

### 9.3 Fehlerkatalog

| Fehler / Symptom | Auslöser (Funktion) | Aktuelles Verhalten | Soll-Verhalten / Operator-Aktion |
|---|---|---|---|
| `ValueError: Missing catalog_path in manifest global_settings` | `run_backtest` | **Jeder Trial bricht ab**. | Sicherstellen, dass im Manifest catalog_path gesetzt wird. |
| `ValueError: Unknown strategy: <X>` | `sample_params` | Trial bricht ab. | Nur `HourlyMeanReversionStrategy`/`SmaCrossoverStrategy` nutzen oder Suchraum in `spaces.py` ergänzen. |
| `FileNotFoundError: Output file …/tournament_result.json not generated` | `run_backtest` | Trial bricht ab. | Der Backtest ist abgestürzt (`check=False` verbirgt den Exit-Code). **Trial-Logs prüfen:** `data/optimizer/<study>/trial_XXXX/logs/`. Häufig: defekter Katalog-Pfad, Precision-Mismatch, zu kurze Datenspanne. |
| Reward konstant `-10.0` über viele Trials | `compute_reward` (weiches Signal) | Kein Crash; Suche bleibt „blind". | Strategie generiert keine auswertbaren OOS-Trades. Suchräume in `spaces.py` auf plausible Grenzen prüfen; Datenspanne (`is_window_days + n_folds·oos_window_days`) muss durch den Katalog gedeckt sein; ggf. `oos_min_trades` vs. Signalfrequenz prüfen. |
| `sqlite3.OperationalError: database is locked` | `optimize` (n_jobs>1) | Trials kollidieren auf der SQLite-DB. | Bei > 4 Worker `STORAGE` auf PostgreSQL umstellen: `STORAGE = "postgresql://opt:opt@db/optuna"`. Worker-Prozesse statt Threads. |
| `subprocess.TimeoutExpired` (nach 10800 s) | `run_backtest` | Trial bricht ab. | Backtest > 3 h: Universe/`splits` reduzieren oder Timeout in `runner.py` erhöhen. |
| `KeyError: 'penalty_unevaluable_oos'` (o. ä.) | `compute_reward` | Trial bricht ab. | `optimizer.json` vervollständigen — **alle** Reward-Keys aus Abschnitt 4.1 müssen vorhanden sein. |
| `KeyError: 'max_drawdown'` | `compute_reward` (nur wenn `risk_dd_cap=None`) | Trial bricht ab. | `tournament.json` muss `max_drawdown` enthalten. (Im Normalpfad übergibt der Orchestrator `risk_dd_cap` mit `.get(..., 0.30)`-Fallback, daher latent.) |
| `FileNotFoundError: …/automation/config/optimizer.json` | `compute_reward` (weights=None, relativer Pfad) | Trial bricht ab. | **Optimizer aus dem Projekt-Stammverzeichnis starten** (Abschnitt 3). |
| `FileNotFoundError: …/backtest.json` | `build_trial` | Trial bricht ab. | Sicherstellen, dass `config_dir()` auf ein Verzeichnis mit vollständiger Config zeigt. |
| `json.JSONDecodeError` | `parse_tournament` / Config-Reads | Trial bricht ab. | Betroffene JSON-Datei validieren (oft halb geschriebene `tournament_result.json` nach hartem Subprozess-Abbruch — dann ist der eigentliche Fehler upstream im Backtest). |
| **Ganze Study stoppt beim ersten Fehler** | `study.optimize` ohne `catch=` | Optuna-Default. | Trial-Isolation aus 9.2 (`catch=(Exception,)` oder in-objective `try/except`). |
| `command not found: python` / läuft mit falschem Interpreter | `run_backtest` (bare `python`) | Subprozess startet nicht/falsch → leeres Output → `FileNotFoundError`. | `python` im `PATH` auf den Projekt-Interpreter zeigen lassen oder Aufruf in `runner.py` auf `sys.executable` umstellen. |

### 9.4 Diagnose-Reihenfolge bei einem fehlgeschlagenen Trial

1. **Welche Exception?** Steht im stdout/Traceback des Optimizer-Prozesses.
2. **Bei `FileNotFoundError (tournament_result.json)`** → in das **Trial-Verzeichnis** wechseln und `logs/` lesen. Dort steht der echte Backtest-Fehler.
3. **Manifest prüfen:** `cat data/optimizer/<study>/trial_XXXX/experiment_manifest.json` — sind `catalog_path` , `start_time`/`end_time` und die Parameter plausibel?
4. **Reproduzieren:** Den Backtest mit genau diesem Manifest manuell starten, um den Fehler isoliert zu sehen:
   ```bash
   python automation/backtest_runner.py --momentum \
     --catalog-path data/nautilus \
     --config data/optimizer/<study>/trial_XXXX/experiment_manifest.json \
     --output /tmp/debug_result.json
   ```

---

## 10. Bekannte Diskrepanzen Code ↔ Dokumentation (Code-Review-Ergebnis)

Zusammenfassung der Befunde aus dem Abgleich von `docs/strategie_optimierung_guide.md`, dem Konzept (`konzept_automatisierte_strategie_optimierung_v2.md`) und dem tatsächlichen Code in `automation/optimizer/`:

| # | Befund | Datei | Schweregrad | Empfehlung |
|---|--------|-------|-------------|------------|
| **B1** | `catalog_path` fehlt im Manifest, wird vom Runner aber verlangt. | `trial_config.py` ↔ `runner.py` | ✅ Behoben | Erledigt. |
| **B2** | Kein `__main__`/argparse; dokumentierter CLI-Aufruf wirkungslos. | `run_optimization.py` | ✅ Behoben | Erledigt. |
| **B3** | Nur 2 von 6 konzipierten Strategien im Suchraum implementiert. | `spaces.py` | ✅ Behoben | Erledigt. |
| **B4** | `run()` (Vollpipeline) ist auf `n_jobs=1` festverdrahtet; Parallelität nur über `optimize()`, das aber Holdout/Proposal nicht ausführt. | `run_optimization.py` | ✅ Behoben | Erledigt. |
| **B5** | `build_trial` legt `trial_dir/logs` nicht an, der Runner setzt aber `ETORO_LOGS_DIR=trial/logs`. | `trial_config.py` | 🟢 minor | In `build_trial` ein `(trial_dir/"logs").mkdir(parents=True, exist_ok=True)` ergänzen. |
| **B6** | `reward.py` (weights=None) und `runner.py` (bare `python`, kein `cwd`) sind CWD-/PATH-abhängig. | `reward.py`, `runner.py` | 🟢 minor | Aus `PROJECT_ROOT` starten; optional auf `sys.executable` + `cwd` umstellen. |
| **B7** | Manifest enthält weniger Felder als im Konzept (kein `experiment`-Block, kein `start_capital`, kein `walk_forward`). | `trial_config.py` | 🟢 kosmetisch | Optional ergänzen, um das Manifest vollständig selbstbeschreibend zu halten. |

> **Reihenfolge der Behebung:** B5–B7 nach Bedarf. Halte die Commits chirurgisch (ein Anliegen je Commit, deutsche Log-Sprache, `pytest`-Gate grün) — gemäß `automation/AGENTS.md`.

---

## 11. Troubleshooting-Schnelltabelle (für Einsteiger)

| Du siehst … | Wahrscheinlich … | Tu das |
|---|---|---|
| Beim Start passiert „nichts" | Falscher Aufruf | Python-API (5.2) nutzen oder CLI verwenden. |
| `Missing catalog_path …` | Manifest defekt | Manifest prüfen. |
| `Unknown strategy: …` | Strategie nicht im Suchraum | Unterstützte Strategie wählen. |
| `Output file … not generated` | Backtest abgestürzt | `trial_XXXX/logs/` lesen, dann manuell reproduzieren (9.4 Schritt 4). |
| Reward immer `-10.0` | Keine OOS-Trades | Suchräume/Datenspanne/`oos_min_trades` prüfen. |
| `database is locked` | SQLite + viele Worker | Auf Postgres umstellen. |
| `FileNotFoundError: optimizer.json` | Falsches Arbeitsverzeichnis | Aus `PROJECT_ROOT` starten. |
| Lauf stirbt beim ersten Fehler | Kein `catch=` | Trial-Isolation (9.2). |

---

*Erstellt nach Abgleich von Handbuch, Konzept v2 und dem Code in `automation/optimizer/` (Stand des bereitgestellten Repos, 2026-06-10). Bei jeder Code-Änderung am Optimizer dieses Handbuch und den Befund-Status in Abschnitt 10 aktualisieren.*
