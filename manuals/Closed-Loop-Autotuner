# Konzept v2: Automatisierte Strategie-Optimierung (Closed-Loop Autotuner)

> **System:** eToro Nautilus v2.0 (Standalone `automation/`)
> **Ziel:** Schritte 1–6 aus `manuals/strategie_optimierung.md` selbstständig ausführen. Pro Parameteränderung ein neuer Backtest gegen das **gesamte Instrumenten-Universum**. Erfolgreiche Parameter werden iterativ verstärkt. Ergebnis: die robustesten **Strategie-Parameter** — ohne Overfitting, ohne Übersteuern von Sicherheitsmechanismen.
> **Status der Klärungen (v2):** Config über vollständig aufgelöstes JSON-Manifest · volles Universum, hohe Bandbreite, parallele Trials · ausschließlich Strategie-Parameter · `--dry-run` wird entfernt, `--no-deploy` eingeführt.

---

## 0. Kurzfassung & Designinvarianten

Der manuelle Regelkreis des Handbuchs (*diagnostizieren → Parameter anpassen → validieren → auswerten → wiederholen*) wird durch einen **Optuna-Sampler (TPE)** automatisiert: er schlägt Parameter vor, fährt pro Vorschlag genau einen Backtest gegen alle Instrumente, bewertet das Ergebnis und verschiebt den Suchraum dichtebasiert Richtung Erfolg (= Verstärkung). Persistenz und Warm-Start sorgen für Verstärkung über mehrere Läufe hinweg.

Verbindliche Invarianten (durchgehend eingehalten):

- **Nur Strategie-Parameter werden optimiert.** Risiko-Gates in `tournament.json` bleiben eingefroren — sie werden nie variiert und nie übersteuert.
- **Kein Overfitting.** Multi-Fold Walk-Forward während der Suche; ein nie gesehenes **Holdout-Fenster** entscheidet allein über die Promotion; der Reward bestraft die IS↔OOS-Lücke.
- **Kein Live-Deploy aus dem Optimierer.** Der Optimierer ruft `backtest_runner.py` direkt (Phase 3+4); die Deploy-Phase wird gar nicht betreten.
- **Nachvollziehbarkeit (Best Practice).** Jeder Backtest ist durch **ein vollständig aufgelöstes JSON-Manifest** vollständig beschrieben und reproduzierbar (inkl. Provenienz/Hashes).
- **Promotion nur menschlich freigegeben** (Git-PR, Jules-Workflow).

---

## 1. Wahl des Optimierers

Backtests sind teuer; sample-effiziente, modellbasierte Verfahren sind Pflicht. **TPE** (Optuna-Default) ist die Primärwahl: modelliert `l(x)=P(x|gut)` vs. `g(x)=P(x|schlecht)` und sampelt `argmax l/g`, verdichtet also erfolgreiche Regionen mit jedem Durchgang. `multivariate=True, group=True` erfasst Parameter-Interaktionen. Persistenz in SQLite (Einzelmaschine) bzw. **RDB/Postgres** (parallele Worker, siehe Abschnitt 10). Alternative für rein kontinuierliche, evolutionäre „Amplifikation": `CmaEsSampler`.

---

## 2. ⚠️ Zwei fundamentale Fallen — und warum sie ausgeschlossen sind

**Falle 1 — Meta-Overfitting auf das OOS-Fenster.** Optimiert man *auf* dem OOS-Resultat, wird OOS zum zweiten Trainingsset. Gegenmaßnahme: 3-Wege-Split + Multi-Fold-WF (Abschnitt 3); der finale Holdout wird während der Suche nie gesehen.

**Falle 2 — Gate-Gaming.** Hinge der Reward am Bool `oos_eligible` oder an den Schwellen in `tournament.json`, würde der Optimierer die Schwellen trivial auf null drehen. Gegenmaßnahme (durch Scope-Entscheidung bestätigt): **Strategie-Parameter werden optimiert, Risiko-Gates bleiben eingefroren.** Der Reward basiert auf tatsächlicher risikoadjustierter OOS-Performance, nicht auf dem Gate-Flag.

> Damit sind **Szenario A und C** des Handbuchs (Lockern weicher Filter) **explizit außerhalb des Scopes** — Sicherheitsmechanismen werden nicht übersteuert. **Szenario B** (längeres OOS, geglättete Indikatoren) ist durch Multi-Fold-WF und die Periodensuche abgedeckt.

---

## 3. Validierungs-Design

Statt `splits=1` (zu rauschig, einladend für Overfitting):

```
|<-------- Optimierungs-Korridor (Sampler sieht nur dies) -------->|<-- HOLDOUT -->|
[ IS | OOS ][ IS | OOS ][ IS | OOS ][ IS | OOS ]                     [ IS | OOS ]
   Fold 1     Fold 2     Fold 3     Fold 4                         finale Bestätigung
```

- **Multi-Fold Walk-Forward:** `splits = 4` (Rechenpower ist vorhanden). Der Reward nutzt die **Median-OOS-Performance über die Folds** → robust gegen Einzelfenster-Glück.
- **Holdout:** die jüngsten `HOLDOUT_DAYS` (Vorschlag 45) werden über `end_time = heute − HOLDOUT_DAYS` aus dem Korridor herausgehalten. Nur das beste Setup wird in **einem** Bestätigungslauf gegen den Holdout getestet (Holdout = OOS dieses Laufs). Nur bei Bestehen → PR.

---

## 4. Suchraum: optimiert vs. eingefroren

Da jeder Trial **genau eine** Strategie aktiviert (im Manifest), bleibt die Dimensionalität pro Study klein (≤ 8) und die Suchräume können bewusst **breit** gehalten werden („möglichst hohe Bandbreite"):

| Strategie | Parameter (breite Suchräume) |
|---|---|
| `SmaCrossoverStrategy` | `sma_period` 5–60, `cooldown_bars` 2–36 |
| `HourlyMeanReversionStrategy` | `keltner_period` 6–40, `keltner_atr_period` 6–40, `keltner_multiplier` 1.0–3.5, `cooldown_bars` 2–36 |
| `FlashCrashReversalStrategy` | `bb_period` 6–40, `bb_std_dev` 1.5–3.5, `rsi_period` 5–28, `rsi_oversold` 10–40, `rsi_overbought` 60–90, `atr_trailing_multiplier` 0.3–2.5, `max_bars_in_trade` 6–72 |
| `VolatilityBreakoutPumpStrategy` | `bb_period` 6–40, `bb_std_dev` 1.5–3.5 |
| `ComboTrendVwapStrategy` | `sma_period` 8–80, `macd_fast` 3–14, `macd_slow` (= fast + Gap 4–26), `macd_signal_period` 4–14, `bb_period` 6–40, `bb_std_dev` 1.5–3.5, `cooldown_bars` 4–36, `atr_multiplier` 0.2–1.8 |
| `VwapExhaustionStrategy` | `deviation_threshold` 0.002–0.025, `vwap_period` 10–60, `cooldown_bars` 1–18 |

**Gemeinsame Basis (`HourlyStrategyConfig`):** `atr_period` 6–28, `atr_trailing_multiplier` 0.3–2.5, `max_bars_in_trade` 12–96, `cooldown_bars` 2–36 — optimierbar.

**Eingefroren (nicht im Suchraum):** alle `tournament.json`-Gates (`max_drawdown`, `min_win_rate`, `min_expectancy`, `min_total_return`, `oos_min_*`), `trade_amount_pct`/Position-Sizing (Risikopolitik; Near-Zero-Sizing erzeugte zuvor implausible Metriken), `max_daily_trades`.

**Hinweise:** (1) **Constraints erzwingen** — `macd_slow > macd_fast` über „fast + Gap"; Keltner-Perioden sinnvoll koppeln. (2) **Tote Parameter ausschließen** — z. B. `ComboTrendVwapConfig.bb_entry_tolerance` ist deklariert, wird in `on_bar()` aber nicht referenziert (dort wirkt `atr_multiplier`); vor Aufnahme verifizieren. (3) **Inaktive Strategien optional** — da jeder Trial die Produktions-Config nicht anrührt und genau eine Strategie isoliert aktiviert, lassen sich auch produktiv deaktivierte Strategien (z. B. `MeanReversion`, `DynamicBreakout`) in einer eigenen Study optimieren; standardmäßig werden nur die sechs aktiven optimiert.

---

## 5. Zielfunktion / Reward (gaming-resistent)

```python
# automation/optimizer/reward.py
RISK_DD_CAP = 0.30  # identisch zu tournament.json max_drawdown — eingefroren

def _clip(x, lo, hi): return max(lo, min(hi, x))

def compute_reward(m, universe_size: int) -> float:
    # Region ohne auswertbares OOS → uninformativ, stark (aber endlich) abwerten.
    if not m.oos_evaluated or m.oos_sortino is None:
        return -10.0
    base = _clip(float(m.oos_sortino), -5.0, 5.0)               # Median über Folds (Abschnitt 8.6)
    is_s = float(m.is_sortino_median) if m.is_sortino_median is not None else base
    overfit_gap = max(0.0, is_s - base)                         # IS >> OOS ⇒ Overfitting
    dd_excess   = max(0.0, float(m.oos_max_drawdown or 0.0) - RISK_DD_CAP)
    coverage    = m.win_count / max(1, universe_size)           # Abdeckung (Symptom A)
    return base - 0.5 * overfit_gap - 8.0 * dd_excess + 1.0 * coverage
```

Kein Gate-Flag im Reward → kein Anreiz, Schwellen zu lockern. Overfit-Strafe und Coverage-Bonus adressieren Symptom B bzw. A, ohne Risiko-Gates anzutasten. Gewichte sind Startkalibrierung. (Ausbaustufe: Optuna Multi-Objective → Pareto-Front aus OOS-Sortino, Coverage, Overfit-Gap.)

---

## 6. Der Closed Loop — Schritte 1–6 automatisiert

1. **Daten-Freeze (einmalig):** Phase 1/2 einmal regulär fahren, danach Katalog einfrieren (`data_snapshot_sha256` festhalten). Während der Optimierung keine Datenänderung.
2. **Diagnose (← Kap. 2):** Baseline-Tournament parsen, Symptome A/B/C klassifizieren — rein informativ; steuert keine Schwellen-Lockerung.
3. **Parametrierung (← Kap. 3/4):** Sampler schlägt Parameter vor → vollständig aufgelöstes **Manifest** (Abschnitt 8.1), Risiko-Gates unverändert, `splits=4`.
4. **Backtest (← Kap. 5, korrigiert):** **`backtest_runner.py` direkt** (Phase 3+4) gegen das volle Universum. `--dry-run` wird nicht verwendet (es überspringt im Bestand den Backtest und schreibt ein Dummy-Tournament — wird entfernt, siehe Abschnitt 9). Deploy wird nie betreten.
5. **Auswertung (← Kap. 6):** Tournament-JSON parsen, Reward berechnen, zurückmelden.
6. **Wiederholen + Verstärken:** bis Budget/Konvergenz; bestes Setup gegen Holdout bestätigen; bei Bestehen Vorschlag exportieren (kein Auto-Commit).

---

## 7. Verstärkungs-Mechanismus

1. **Innerhalb einer Study (TPE):** nach `n_startup_trials` Zufallsziehungen verdichtet TPE die Abtastung in erfolgreichen Regionen — Verstärkung mit jedem Durchgang.
2. **Über Läufe hinweg (Warm-Start):** `load_if_exists=True` + persistente Storage erben alle (params, reward)-Paare; gute Regionen werden weiter verdichtet.
3. **Optional explizit:** `study.enqueue_trial(best_params)` intensiviert lokal um den bisherigen Sieger.

---

## 8. Implementierung

```
automation/optimizer/
├── __init__.py
├── manifest.py       # Pfade, Git-Commit, Katalog-Fingerprint, Datei-Hashes
├── resolve.py        # defaults ⊕ strategies.json-params ⊕ sampled  → aufgelöste Params
├── trial_config.py   # baut isoliertes Trial-Verzeichnis + vollständiges Manifest
├── runner.py         # ruft backtest_runner.py direkt (ETORO_CONFIG_DIR/LOGS_DIR, eigener Output)
├── parsing.py        # Tournament-JSON → TournamentMetrics (Pro-Fold-Median, Fallback)
├── reward.py         # compute_reward (Abschnitt 5)
├── spaces.py         # Suchräume pro Strategie
├── diagnose.py       # Symptom-Klassifikation (informativ)
├── confirm.py        # Holdout-Bestätigung
└── run_optimization.py  # Optuna-Hauptschleife + Promotion-Export
```

### 8.1 Best-Practice Config-Contract: das Experiment-Manifest

**Eine** selbstbeschreibende JSON pro Backtest — vollständig aufgelöst und reproduzierbar:

```json
{
  "manifest_version": "1.0",
  "experiment": {
    "study_name": "HourlyMeanReversionStrategy",
    "trial_number": 42,
    "created_at": "2026-06-09T18:00:00Z",
    "sampler": "TPESampler",
    "seed": 42
  },
  "provenance": {
    "git_commit": "a1b2c3d",
    "data_snapshot_sha256": "…",
    "frozen_tournament_sha256": "…",
    "base_config_dir": "automation/config"
  },
  "global_settings": {
    "catalog_path": "data/nautilus",
    "start_time": "2025-12-26T00:00:00Z",
    "end_time": "2026-04-25T00:00:00Z",
    "start_capital": 10000.0,
    "walk_forward": { "is_window_days": 120, "oos_window_days": 30, "splits": 4 }
  },
  "strategies": [
    {
      "strategy_module": "automation.strategies.hourly_mean_reversion",
      "strategy_class": "HourlyMeanReversionStrategy",
      "config_class": "HourlyMeanReversionConfig",
      "params": {
        "keltner_period": 14, "keltner_atr_period": 14, "keltner_multiplier": 2.1,
        "cooldown_bars": 8, "atr_trailing_multiplier": 1.3, "max_bars_in_trade": 36,
        "trade_amount_pct": 15.0
      }
    }
  ]
}
```

**Runner-Contract (verbindlich, in AGENTS.md verankert):** `backtest_runner.py` liest Strategien und Parameter **ausschließlich** aus dieser via `--config` übergebenen Datei. `strategies[].params` sind **vollständig aufgelöst und autoritativ** — kein erneutes Mergen aus `strategy_defaults.json`. Tournament-Kriterien kommen aus der (eingefrorenen) `tournament.json` unter `ETORO_CONFIG_DIR`; ihr Hash ist im Manifest dokumentiert.

### 8.2 Pfade & Provenienz

```python
# automation/optimizer/manifest.py
from __future__ import annotations
import hashlib, subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_CFG = PROJECT_ROOT / "automation" / "config"
CATALOG  = PROJECT_ROOT / "data" / "nautilus"
WORK     = PROJECT_ROOT / "data" / "optimizer"

def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT).decode().strip()
    except Exception:
        return "unknown"

def catalog_fingerprint() -> str:
    """Günstiger Fingerabdruck des eingefrorenen Katalog-Zustands (Drift-Erkennung)."""
    h = hashlib.sha256()
    for p in sorted(CATALOG.rglob("data.parquet")):
        st = p.stat()
        h.update(str(p.relative_to(CATALOG)).encode())
        h.update(str(st.st_size).encode()); h.update(str(int(st.st_mtime)).encode())
    return h.hexdigest()

def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()
```

### 8.3 Param-Resolver (explizites Mergen im Optimierer)

```python
# automation/optimizer/resolve.py
import json
from pathlib import Path

def resolve_params(strategy_class: str, sampled: dict, base_cfg: Path) -> dict:
    defaults = json.loads((base_cfg / "strategy_defaults.json").read_text("utf-8"))
    resolved = dict(defaults.get(strategy_class, {}))            # 1. Defaults
    sjson = json.loads((base_cfg / "strategies.json").read_text("utf-8"))
    for s in sjson["strategies"]:                                # 2. bestehende params
        if s.get("strategy_class") == strategy_class:
            resolved.update(s.get("params") or {}); break
    resolved.update(sampled)                                     # 3. Sampler-Overrides (höchste Prio)
    return resolved
```

### 8.4 Trial-Verzeichnis + Manifest

```python
# automation/optimizer/trial_config.py
import json, shutil, datetime, uuid
from pathlib import Path
from .manifest import (PROJECT_ROOT, BASE_CFG, CATALOG, WORK,
                       git_commit, catalog_fingerprint, sha256_file)
from .resolve import resolve_params

def _module_for(strategy_class: str) -> dict:
    sjson = json.loads((BASE_CFG / "strategies.json").read_text("utf-8"))
    for s in sjson["strategies"]:
        if s.get("strategy_class") == strategy_class:
            return {"strategy_module": s["strategy_module"],
                    "strategy_class": strategy_class,
                    "config_class": s["config_class"]}
    raise ValueError(strategy_class)

def build_trial(strategy_class: str, sampled: dict, *, study_name: str,
                trial_number: int, n_folds: int, holdout_days: int, seed: int):
    trial_dir = WORK / "trials" / f"{strategy_class}_t{trial_number}_{uuid.uuid4().hex[:8]}"
    (trial_dir / "logs").mkdir(parents=True, exist_ok=True)
    shutil.copytree(BASE_CFG, trial_dir / "config")   # eingefrorene tournament.json etc.

    end = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    if end.weekday() == 6:                              # Sonntag → Freitag EOD
        end -= datetime.timedelta(days=1)
    end -= datetime.timedelta(days=holdout_days)        # Holdout aussparen

    bt = json.loads((BASE_CFG / "backtest.json").read_text("utf-8"))
    wf = dict(bt.get("walk_forward") or {}); wf["splits"] = n_folds
    total_days = wf.get("is_window_days", 120) + wf["splits"] * wf.get("oos_window_days", 30)
    start = end - datetime.timedelta(days=total_days)

    manifest = {
        "manifest_version": "1.0",
        "experiment": {"study_name": study_name, "trial_number": trial_number,
                       "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "sampler": "TPESampler", "seed": seed},
        "provenance": {"git_commit": git_commit(),
                       "data_snapshot_sha256": catalog_fingerprint(),
                       "frozen_tournament_sha256": sha256_file(BASE_CFG / "tournament.json"),
                       "base_config_dir": str(BASE_CFG)},
        "global_settings": {"catalog_path": str(CATALOG),
                            "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "start_capital": bt.get("start_capital", 10000.0),
                            "walk_forward": wf},
        "strategies": [{**_module_for(strategy_class),
                        "params": resolve_params(strategy_class, sampled, BASE_CFG)}],
    }
    manifest_path = trial_dir / "experiment_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
    return trial_dir, manifest_path
```

### 8.5 Runner-Wrapper (direkt, isoliert, kollisionsfrei)

```python
# automation/optimizer/runner.py
import os, sys, subprocess
from pathlib import Path
from .manifest import PROJECT_ROOT, CATALOG

def run_backtest(trial_dir: Path, manifest_path: Path) -> Path:
    out = trial_dir / "tournament_result.json"          # pro Trial eigener Output
    env = {**os.environ,
           "ETORO_CONFIG_DIR": str(trial_dir / "config"),   # eingefrorene Gates
           "ETORO_LOGS_DIR": str(trial_dir / "logs"),       # Log-Isolation (Parallelität)
           "PYTHONUNBUFFERED": "1"}
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "automation" / "backtest_runner.py"),
         "--momentum", "--catalog-path", str(CATALOG),
         "--config", str(manifest_path), "--output", str(out)],
        cwd=str(PROJECT_ROOT), env=env, check=False, timeout=10800,
    )
    if not out.exists():
        raise FileNotFoundError(f"backtest_runner lieferte kein Ergebnis: {out}")
    return out
```

Der Katalog ist während der Optimierung **read-only** → parallele Trials teilen ihn gefahrlos. Isolation nur für Output und Logs (oben gelöst).

### 8.6 Parser (Pro-Fold-Median, Fallback Aggregat)

```python
# automation/optimizer/parsing.py
import json, statistics
from dataclasses import dataclass
from pathlib import Path

@dataclass
class TournamentMetrics:
    oos_evaluated: bool; oos_eligible: bool
    is_sortino_median: float | None; oos_sortino: float | None
    oos_max_drawdown: float | None; oos_total_trades: int
    win_count: int; fully_eligible_pairs: int

def parse_tournament(path: Path) -> TournamentMetrics:
    data = json.loads(Path(path).read_text("utf-8"))
    agg = data.get("aggregate_winner") or {}; oos = agg.get("oos_metrics") or {}
    # Handbuch nutzt 'median_sortino', Orchestrator 'median_is_sortino' — beide tolerieren.
    is_sortino = agg.get("median_is_sortino", agg.get("median_sortino"))
    per_fold = agg.get("oos_fold_sortinos")    # Runner-Contract (Abschnitt 9.2)
    oos_sortino = (statistics.median([float(x) for x in per_fold])
                   if per_fold else oos.get("sortino_ratio"))
    return TournamentMetrics(
        oos_evaluated=bool(agg.get("oos_evaluated", False)),
        oos_eligible=bool(agg.get("oos_eligible", False)),
        is_sortino_median=is_sortino, oos_sortino=oos_sortino,
        oos_max_drawdown=oos.get("max_drawdown"),
        oos_total_trades=int(oos.get("total_trades", 0) or 0),
        win_count=int(agg.get("win_count", 0) or 0),
        fully_eligible_pairs=int(data.get("fully_eligible_pairs", 0) or 0))
```

### 8.7 Suchräume

```python
# automation/optimizer/spaces.py
import optuna

def sample_params(strategy: str, trial: optuna.Trial) -> dict:
    if strategy == "HourlyMeanReversionStrategy":
        return dict(
            keltner_period=trial.suggest_int("keltner_period", 6, 40),
            keltner_atr_period=trial.suggest_int("keltner_atr_period", 6, 40),
            keltner_multiplier=trial.suggest_float("keltner_multiplier", 1.0, 3.5),
            cooldown_bars=trial.suggest_int("cooldown_bars", 2, 36),
            atr_trailing_multiplier=trial.suggest_float("atr_trailing_multiplier", 0.3, 2.5),
            max_bars_in_trade=trial.suggest_int("max_bars_in_trade", 12, 96))
    if strategy == "ComboTrendVwapStrategy":
        fast = trial.suggest_int("macd_fast", 3, 14)
        slow = fast + trial.suggest_int("macd_slow_gap", 4, 26)   # erzwingt slow > fast
        return dict(
            sma_period=trial.suggest_int("sma_period", 8, 80),
            macd_fast=fast, macd_slow=slow,
            macd_signal_period=trial.suggest_int("macd_signal_period", 4, 14),
            bb_period=trial.suggest_int("bb_period", 6, 40),
            bb_std_dev=trial.suggest_float("bb_std_dev", 1.5, 3.5),
            cooldown_bars=trial.suggest_int("cooldown_bars", 4, 36),
            atr_multiplier=trial.suggest_float("atr_multiplier", 0.2, 1.8))
    # … FlashCrashReversal, VwapExhaustion, VolatilityBreakout, SmaCrossover analog (Tabelle Abschnitt 4) …
    raise ValueError(f"Unbekannte Strategie: {strategy}")
```

### 8.8 Hauptschleife

```python
# automation/optimizer/run_optimization.py
import json, datetime
from pathlib import Path
import optuna
from optuna.samplers import TPESampler
from .spaces import sample_params
from .trial_config import build_trial, WORK
from .runner import run_backtest
from .parsing import parse_tournament
from .reward import compute_reward
from .manifest import catalog_fingerprint

N_FOLDS, HOLDOUT_DAYS, SEED = 4, 45, 42
STORAGE = f"sqlite:///{WORK / 'studies.db'}"
# Für echte Parallelität: STORAGE = "postgresql://opt:opt@db/optuna"

def _universe_size() -> int:
    try:
        return len(json.loads(Path("data/universe/momentum_ls.json")
                              .read_text("utf-8")).get("universe", []))
    except Exception:
        return 70
UNIVERSE_SIZE = _universe_size()

def make_objective(strategy: str):
    def objective(trial: optuna.Trial) -> float:
        sampled = sample_params(strategy, trial)
        trial.set_user_attr("sampled_params", sampled)   # materialisierte Overrides
        trial_dir, manifest = build_trial(
            strategy, sampled, study_name=strategy, trial_number=trial.number,
            n_folds=N_FOLDS, holdout_days=HOLDOUT_DAYS, seed=SEED)
        m = parse_tournament(run_backtest(trial_dir, manifest))
        for k in ("oos_eligible", "oos_evaluated", "win_count",
                  "oos_sortino", "oos_max_drawdown", "is_sortino_median"):
            trial.set_user_attr(k, getattr(m, k))
        trial.set_user_attr("trial_dir", str(trial_dir))
        return compute_reward(m, UNIVERSE_SIZE)
    return objective

def optimize(strategy: str, n_trials: int = 100, n_jobs: int = 1):
    study = optuna.create_study(
        study_name=strategy, storage=STORAGE, direction="maximize",
        sampler=TPESampler(multivariate=True, group=True, n_startup_trials=16, seed=SEED),
        load_if_exists=True)                              # Warm-Start = Verstärkung
    study.set_user_attr("data_snapshot_sha256", catalog_fingerprint())
    study.optimize(make_objective(strategy), n_trials=n_trials, n_jobs=n_jobs)
    return study
```

### 8.9 Holdout-Bestätigung + Promotion

```python
# automation/optimizer/confirm.py
from .trial_config import build_trial, WORK
from .runner import run_backtest
from .parsing import parse_tournament
import json, datetime

def confirm_on_holdout(study, strategy: str) -> dict:
    best = study.best_trial
    sampled = best.user_attrs["sampled_params"]
    trial_dir, manifest = build_trial(
        strategy, sampled, study_name=f"{strategy}_HOLDOUT",
        trial_number=best.number, n_folds=1, holdout_days=0, seed=42)  # Holdout = OOS
    m = parse_tournament(run_backtest(trial_dir, manifest))
    passed = bool(m.oos_evaluated and m.oos_eligible
                  and (m.oos_sortino or -9) > 0 and (m.oos_max_drawdown or 1) <= 0.30)
    return {"passed": passed, "metrics": m.__dict__, "trial_dir": str(trial_dir)}

def export_proposal(study, strategy: str, holdout: dict):
    best = study.best_trial
    proposal = {
        "strategy_class": strategy,
        "proposed_params_override": best.user_attrs["sampled_params"],  # → strategies.json params
        "search_reward": study.best_value,
        "search_oos_sortino": best.user_attrs.get("oos_sortino"),
        "holdout_passed": holdout["passed"], "holdout_metrics": holdout["metrics"],
        "data_snapshot_sha256": study.user_attrs.get("data_snapshot_sha256"),
        "n_trials": len(study.trials),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "READY_FOR_PR" if holdout["passed"] else "REJECTED_ON_HOLDOUT"}
    p = WORK / f"proposal_{strategy}.json"
    p.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), "utf-8")
    return p
```

Promotion = `strategies[strategy].params = proposed_params_override` per Git-PR (minimal, `params` hat Vorrang vor `strategy_defaults.json`), nach menschlichem Review. Kein Auto-Commit, kein Auto-Deploy.

---

## 9. Bereinigung des Bestandscodes: `--no-deploy` einführen, `--dry-run` entfernen

**Problem:** `--dry-run` überspringt im aktuellen `daily_orchestrator.py` den Backtest (Phase 3) und schreibt ein **Dummy-Tournament** — entgegen der Aussage in Handbuch-Kapitel 5. Die Angabe ist irreführend und wird **ersatzlos entfernt**. Stattdessen: `--no-deploy` (Phase 1–4 vollständig, nur Phase 5 unterbunden).

### 9.1 Konkrete Änderungen an `daily_orchestrator.py`

**(a) Pfade aus Env lenkbar machen (Config-/Log-Isolation):**
```python
AUTOMATION_CFG_DIR = Path(os.getenv("ETORO_CONFIG_DIR", str(PROJECT_ROOT / "automation" / "config")))
LOGS_DIR           = Path(os.getenv("ETORO_LOGS_DIR",  str(PROJECT_ROOT / "logs")))
```

**(b) Argument-Parser:** `--dry-run` löschen, `--no-deploy` hinzufügen:
```python
# ENTFERNEN:
# parser.add_argument("--dry-run", action="store_true", help="Kein echter Bot-Start.")
# NEU:
parser.add_argument("--no-deploy", action="store_true",
    help="Führt Phase 1–4 vollständig aus (echter Backtest), unterbindet ausschließlich Phase 5 (Live-Deploy).")
```

**(c) `phase3_4_backtest_and_tournament`:** `dry_run`-Parameter und Dummy-Skip-Block entfernen — Phase 3 läuft IMMER real. Der No-Data-Fallback (`if not TOURNAMENT_PATH.exists(): _create_dummy_tournament(...)`) bleibt erhalten:
```python
def phase3_4_backtest_and_tournament(log: logging.Logger) -> dict:
    ...
    # ENTFERNT:
    #   if dry_run:
    #       log.info("[Phase 3] DRY-RUN: Backtest übersprungen.")
    #       _create_dummy_tournament(log)
    #       return {"tournament_path": str(TOURNAMENT_PATH), "dry_run": True}
    ...
    with open(str(bt_log_path), "w", encoding="utf-8") as bt_log_f:
        proc = subprocess.run(cmd, stdout=bt_log_f, stderr=subprocess.STDOUT,
                              cwd=str(PROJECT_ROOT), timeout=3600, check=False)
    ...
    if not TOURNAMENT_PATH.exists():        # echter No-Data-Fallback bleibt
        _create_dummy_tournament(log)
    else:
        ...                                  # Tournament lesen & loggen
    return {"tournament_path": str(TOURNAMENT_PATH)}
```

**(d) `phase5_live_deployment`:** `dry_run` → `no_deploy`:
```python
def phase5_live_deployment(log, universe_result, tournament_result, no_deploy: bool = False) -> int:
    ...
    emit_json_event(log, "BOT_START_INITIATED", {... , "no_deploy": no_deploy, ...})
    if no_deploy:
        log.info("[Phase 5] --no-deploy: Live-Deploy unterbunden (Phase 1–4 vollständig ausgeführt).")
        emit_json_event(log, "LIVE_DEPLOY_SKIPPED_NO_DEPLOY",
                        {"aggregate_oos_sortino": aggregate_oos_sortino,
                         "fully_eligible_pairs": fully_eligible_pairs, "winner_count": winner_count})
        return 0
    # else: Bot via subprocess.Popen starten (unverändert)
```

**(e) `main()`-Verdrahtung:**
```python
tournament_result = phase3_4_backtest_and_tournament(log)
exit_code = phase5_live_deployment(log, universe_result, tournament_result, no_deploy=args.no_deploy)
# ORCHESTRATOR_START-Payload: "dry_run": args.dry_run  →  "no_deploy": args.no_deploy
# Banner-Zeile "DRY-RUN: ..."  →  "NO-DEPLOY: JA/NEIN"
```

**(f) Optionaler Refactor:** Zeitfenster-/`global_settings`-Aufbau aus `_build_backtest_config` in ein gemeinsames Modul (`automation/config_builder.py`) extrahieren, damit Orchestrator (Phase 3) und Optimierer dieselbe Logik nutzen; der Optimierer ergänzt Resolution + Provenienz darüber.

### 9.2 AGENTS.md — fertige Ergänzung (zum Einfügen)

```markdown
## Pitfall #<nächste freie Nummer> — `--dry-run` entfernt, `--no-deploy` eingeführt

**Kontext:** `--dry-run` übersprang Phase 3 und schrieb ein Dummy-Tournament. Das widersprach
Handbuch Kap. 5 ("Phasen 1–4 vollständig") und war irreführend.

**Regel:**
- `--dry-run` ist aus Code UND Doku ersatzlos entfernt. Keine neue Verwendung einführen.
- `--no-deploy` führt Phase 1–4 vollständig aus und unterbindet ausschließlich Phase 5.
  Operator-Validierung (vormals `--dry-run --skip-api-fetch`) lautet nun:
  `python3 automation/daily_orchestrator.py --no-deploy --skip-api-fetch`
- Phase 3 läuft immer real; der `_create_dummy_tournament`-Fallback bleibt ausschließlich
  für den echten No-Data-Fall (`TOURNAMENT_PATH` fehlt nach dem Lauf).

## Optimizer / backtest_runner.py — Config-Contract (verbindlich)

- `backtest_runner.py` liest Strategien + Parameter **ausschließlich** aus der via `--config`
  übergebenen Manifest-Datei. `strategies[].params` sind vollständig aufgelöst und autoritativ;
  **kein** erneutes Mergen aus `strategy_defaults.json`.
- `backtest_runner.py` respektiert `ETORO_CONFIG_DIR` (Quelle der eingefrorenen `tournament.json`)
  und `ETORO_LOGS_DIR` (Log-Verzeichnis); schreibt Ergebnisse ausschließlich nach `--output`.
- Bei `walk_forward.splits > 1` gibt `aggregate_winner.oos_fold_sortinos` die Pro-Fold-OOS-Sortinos
  als Liste aus (Basis des robusten Median-Rewards).
- Der Optimierer verändert `tournament.json` NIE und startet NIE Phase 5. Promotion nur per
  menschlich freigegebenem PR.

## Changelog-Pflicht
- CHANGELOG: "Entfernt `--dry-run`; `--no-deploy` ergänzt; `ETORO_CONFIG_DIR`/`ETORO_LOGS_DIR`
  unterstützt; Manifest-Config-Contract für backtest_runner."
- Commits chirurgisch (1 Anliegen/Commit), deutsche Log-Sprache, `pytest`-Gate grün.
```

### 9.3 Handbuch-Korrektur (`manuals/strategie_optimierung.md`, Kap. 5)

Befehl und Beschreibung ersetzen: `--dry-run` → `--no-deploy`; Text klarstellen, dass Phase 1–4 real ausgeführt werden und nur der Live-Deploy unterbleibt:
```bash
python3 automation/daily_orchestrator.py --no-deploy --skip-api-fetch
```

---

## 10. Compute, Performance & Parallelisierung

Da Rechenpower vorhanden ist und gegen das **volle Universum** getestet wird, entfällt jedes Subset-Screening. Die Rechenleistung fließt in Breite und Robustheit:

- **Volles Universum pro Trial**, breite Suchräume, `splits=4`.
- **Parallele Trials** statt sequenziell: mehrere Worker-Prozesse rufen `optimize(..., n_jobs=1)` gegen **dieselbe RDB-Study** (Postgres) — Optuna verteilt die Trials. Jeder Trial schreibt in sein eindeutiges `trial_dir` (Abschnitt 8.4) → kollisionsfrei; der Katalog ist read-only und wird geteilt.
- **Core-Budgetierung:** Pro Trial die internen `max_workers` (siehe `backtest.json`) so wählen, dass `parallele_Trials × max_workers ≤ verfügbare Kerne`. Prozess-Worker (nicht Threads) bevorzugen, da die Last subprozess-gebunden ist.
- **Budget:** ~80–150 Trials pro Strategie bei ≤ 8 Dimensionen. Bei 6 aktiven Strategien als nächtliche, parallele Batches gut machbar.

---

## 11. Betrieb: Cron, Persistenz, Reproduzierbarkeit

- **Scheduling:** als nächtlicher Job (analog zum bestehenden Freitag-Cron); Warm-Start sorgt für kontinuierliche Verbesserung über Nächte.
- **Reproduzierbarkeit:** `seed` fix; **`data_snapshot_sha256`** je Study protokollieren — bei Katalog-Drift Study-Historie kennzeichnen (sonst sind Nächte nicht vergleichbar).
- **Persistenz/Resume:** RDB-Storage; Abbruch/Neustart jederzeit möglich.
- **Audit-Trail:** pro Trial bleiben Manifest, `tournament_result.json`, Logs und Reward erhalten — passt zum forensischen Workflow und erlaubt vollständige Re-Analyse.

---

## 12. Sicherheits-Leitplanken (verbindlich)

1. **Kein Live-Deploy aus dem Optimierer** (Runner-Direktaufruf, Phase 5 nie betreten).
2. **Risiko-Gates eingefroren** (`tournament.json` 1:1 kopiert, nie variiert).
3. **Holdout unberührt** (keine Optimierungs-Auswertung sieht ihn).
4. **Human-in-the-Loop** (Promotion nur per PR; Holdout-Ergebnis + Overfit-Gap im Review).
5. **Plausibilitäts-Wächter** (Trials mit absurden Metriken — z. B. Sortino > 50, PF > 20, Near-Zero-Sizing — werden markiert statt als Sieger gewertet).

---

## 13. Verbleibende Annahmen (als Contract verankert, nicht offen)

- **Pro-Fold-Metriken:** Feld `aggregate_winner.oos_fold_sortinos` wird vom Runner geliefert (AGENTS.md 9.2). Solange nicht vorhanden, nutzt der Parser automatisch das aggregierte OOS-Sortino.
- **Env-Variablen:** `backtest_runner.py` und Orchestrator respektieren `ETORO_CONFIG_DIR`/`ETORO_LOGS_DIR` (AGENTS.md 9.2 / Abschnitt 9.1).
- **Manifest-Autorität:** Runner liest Params ausschließlich aus dem `--config`-Manifest (kein Re-Merge).

---

## 14. Umsetzungs-Roadmap

1. **Phase 0 — Enabling/Cleanup:** `--dry-run` entfernen, `--no-deploy` + Env-Variablen ergänzen, AGENTS.md + Handbuch Kap. 5 anpassen, optionaler `config_builder`-Refactor, Manifest-Contract im Runner verankern (inkl. `oos_fold_sortinos`).
2. **Phase 1 — Single-Strategy-MVP:** `optimizer/`-Modul, eine Strategie (z. B. `HourlyMeanReversionStrategy`), Manifest + Runner-Wrapper + Parser + skalarer Reward, sequentiell, SQLite. Ende-zu-Ende verifizieren.
3. **Phase 2 — Validierungs-Härtung:** `splits=4`, 3-Wege-Split inkl. Holdout-Bestätigung, Plausibilitäts-Wächter, Snapshot-Hashing.
4. **Phase 3 — Skalierung:** RDB-Storage, parallele Worker, Core-Budgetierung, Suchräume für alle aktiven Strategien, nächtlicher Cron mit Warm-Start.
5. **Phase 4 — Ausbau (optional):** Multi-Objective/Pareto, CMA-ES-Vergleich, optionale Studies für produktiv deaktivierte Strategien.

---

*Kernbotschaft (unverändert): Der Optimierer ist Standard — der Wert liegt in der Trennung von Strategie-Parametern (optimierbar) und Risiko-Gates (eingefroren), im nie gesehenen Holdout gegen Meta-Overfitting und in der lückenlos nachvollziehbaren Manifest-Config. Ohne diese drei findet der Loop nicht die besten Einstellungen, sondern die besten Wege, die eigene Metrik zu täuschen.*
