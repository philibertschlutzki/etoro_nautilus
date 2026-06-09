# Jules-Aufträge — Autotuner V2 (überarbeitet, wasserdicht & isoliert testbar)

> **Quelle der Wahrheit:** `konzept_automatisierte_strategie_optimierung_v2.md`
> **Prinzip:** Jeder Auftrag ist **eigenständig abnehmbar** — seine Tests bestehen mit einem frischen Checkout, sobald die *vorherigen* Aufträge gemerged sind. Kein Artefakt wird in einem Auftrag erzeugt und erst in einem späteren getestet.
> **Schnitt:** 5 Aufträge (0a, 0b, 1a, 1b, 1c). Auftrag 0b ist intern in drei unabhängig testbare Teile (A/B/C) gegliedert.

---

## Globale Konventionen (gelten verbindlich für ALLE Aufträge)

1. **Standalone-Prinzip.** Keine Importe aus `archive/` oder `adapters/`. `automation/` ist autark.
2. **Sprache.** Log-Ausgaben und Code-Kommentare auf Deutsch; Bezeichner/Tests auf Englisch.
3. **Zero-Hardcoding.** Keine Magic Numbers im Code. Alle Tunables stammen aus JSON-Konfiguration (`backtest.json`, `tournament.json`, `optimizer.json`). Tests lesen dieselben Werte aus der JSON — **keine** duplizierten Literale im Assert.
4. **TDD & schnelle Unit-Tests.** Tests zuerst. **Kein** Test startet einen echten Backtest, kontaktiert das Netzwerk oder benötigt Secrets. Schwergewichtige Abhängigkeiten (Subprozesse) werden gemockt; I/O läuft gegen `tmp_path`/Fixtures.
5. **Dependency Injection für Testbarkeit.** Datums-, Pfad- und Subprozess-Abhängigkeiten werden als optionale Parameter injizierbar gemacht (`now=None`, `catalog=None`, `run_backtest=...`). Default `None` ⇒ Produktionsverhalten (Konfig/Clock lesen).
6. **CI-Gate grün.** Neue Tests in den im jeweiligen Auftrag genannten Tier von `.github/workflows/pytest-gate.yml` einhängen. Der gesamte Gate muss grün durchlaufen.
7. **AGENTS.md-Pflicht.** Jeder Auftrag aktualisiert die genannten Kapitel **und** das Changelog (Kapitel 19). Das ist Teil der Definition of Done — nicht optional.
8. **Pitfall-Nummerierung.** Neue Pitfalls unter Kapitel 16 mit der **nächsten freien fortlaufenden Nummer** anlegen; die hier vorgegebenen Titel/Texte exakt übernehmen.
9. **Chirurgische Commits.** Ein Anliegen pro Commit, deutsche Commit-Messages. Ein Auftrag = ein PR. PR-Beschreibung referenziert den Auftrag und listet die bestehenden Tests als Nachweis.
10. **Reversibilität.** Änderungen müssen einzeln revertierbar sein, ohne andere Aufträge zu brechen.
11. **Namens-Contract.** Alle Funktions-/Datei-/Key-Namen sind in **Anhang A** zentral festgelegt und über alle Aufträge hinweg identisch zu verwenden.

### Definition of Done (Vorlage je Auftrag)
- [ ] Alle gelisteten Artefakte erstellt/geändert, exakt nach den Funktions-Contracts.
- [ ] Alle gelisteten Tests grün; Tests sind deterministisch, ohne Netzwerk/Subprozess-Realbetrieb.
- [ ] CI-Gate grün (neuer Test im genannten Tier eingehängt).
- [ ] AGENTS.md: genannte Kapitel + Changelog aktualisiert.
- [ ] Nicht-Ziele eingehalten (keine fremden Dateien angefasst).
- [ ] PR eröffnet, Auftrag referenziert, Testnachweis in der Beschreibung.

---

## Abhängigkeits- & Test-Matrix (Nachweis: kein ungetestetes Artefakt)

| Auftrag | Erstellt / Ändert | Tests (im selben Auftrag) | CI-Tier | Hängt ab von |
|---|---|---|---|---|
| **1 — 0a** | `daily_orchestrator.py` (CLI: `--dry-run` raus, `--no-deploy` rein; Phase 3 immer real) | `test_orchestrator_cli.py` | Tier 3 | — |
| **2 — 0b** | `daily_orchestrator.py` + `backtest_runner.py` (Env-Isolation, Manifest-Contract, Pro-Fold-Sortinos) | `test_runner_env_isolation.py`, `test_runner_manifest_contract.py`, `test_runner_fold_sortinos.py` | Tier 3 | 1 |
| **3 — 1a** | `backtest.json`, `optimizer.json`, `optimizer/{manifest,resolve,trial_config}.py` | `test_optimizer_manifest.py` | Tier 10 | 2 |
| **4 — 1b** | `optimizer/{runner,parsing,reward}.py` | `test_optimizer_runner.py`, `test_optimizer_reward_parser.py` | Tier 10 | 3 |
| **5 — 1c** | `optimizer/{spaces,confirm,run_optimization}.py` | `test_optimizer_loop.py` | Tier 10 | 4 |

---
---

# Auftrag 1 — Phase 0a: `--dry-run` entfernen, `--no-deploy` einführen

### Abhängigkeiten
Keine.

### Kontext & Ziel
`--dry-run` übersprang im Bestand Phase 3 (echter Backtest) und schrieb ein **Dummy-Tournament** — das widerspricht Handbuch Kap. 5 und ist irreführend. Ziel: `--dry-run` **restlos** entfernen, `--no-deploy` einführen (Phase 1–4 vollständig, nur Phase 5 unterbunden), und sicherstellen, dass Phase 3 **immer real** läuft.

### Vorbedingungen (Recon)
- Lies `automation/daily_orchestrator.py` vollständig, insbesondere `main()`, `phase3_4_backtest_and_tournament`, `phase5_live_deployment` und alle Stellen mit `dry_run`/`args.dry_run`.
- Lies vorhandene Orchestrator-Tests (`automation/tests/test_orchestrator_phase1.py`, falls vorhanden) und `.github/workflows/pytest-gate.yml` (Tier-Struktur).

### Zu liefernde Artefakte
- Geändert: `automation/daily_orchestrator.py`
- Neu: `automation/tests/test_orchestrator_cli.py`
- Geändert: `.github/workflows/pytest-gate.yml` (Test in Tier 3)
- Geändert: `AGENTS.md`

### Funktions-Contracts (exakt)
1. **Argument-Parser refaktorieren** in eine testbare Funktion:
   ```python
   def build_arg_parser() -> argparse.ArgumentParser: ...
   ```
   - `--dry-run` und jedes zugehörige Argument/Feld **entfernen**.
   - Neu: `parser.add_argument("--no-deploy", action="store_true", help="Führt Phase 1–4 vollständig aus (echter Backtest), unterbindet ausschließlich Phase 5 (Live-Deploy).")`
   - `main()` nutzt `build_arg_parser()`.
2. **`phase3_4_backtest_and_tournament(log: logging.Logger) -> dict`** — Parameter `dry_run` **entfernen**. Den `if dry_run: _create_dummy_tournament(...) ; return`-Block **löschen**. Der `_create_dummy_tournament`-Aufruf bleibt **ausschließlich** im echten No-Data-Fallback erhalten:
   ```python
   if not TOURNAMENT_PATH.exists():
       _create_dummy_tournament(log)
   else:
       ...  # Tournament lesen & loggen (unverändert)
   ```
3. **`phase5_live_deployment(log, universe_result, tournament_result, no_deploy: bool = False) -> int`** — `dry_run` → `no_deploy`. Unmittelbar vor dem `subprocess.Popen(...)` des Bots:
   ```python
   if no_deploy:
       log.info("[Phase 5] --no-deploy: Live-Deploy unterbunden (Phase 1–4 vollständig ausgeführt).")
       emit_json_event(log, "LIVE_DEPLOY_SKIPPED_NO_DEPLOY", {
           "strategy": agg.get("strategy"),
           "fully_eligible_pairs": fully_eligible_pairs,
           "winner_count": winner_count,
       })
       return 0
   ```
   Der Bot-Subprozess wird in diesem Fall **nicht** gestartet.
4. **`main()`-Verdrahtung:** `phase3_4_backtest_and_tournament(log)` (ohne `dry_run`); `phase5_live_deployment(..., no_deploy=args.no_deploy)`. Im `ORCHESTRATOR_START`-Event den Key `"dry_run"` durch `"no_deploy"` ersetzen; Banner-Zeile „DRY-RUN" → „NO-DEPLOY".

### Nicht-Ziele
- Keine Änderung an Phase 1/2/3-Logik außer der Entfernung des Dummy-Skips.
- Keine Änderung an `backtest_runner.py` (das ist Auftrag 2).

### Harte Abnahmekriterien & Tests (TDD)
Datei `automation/tests/test_orchestrator_cli.py`:
```python
import pytest
from automation import daily_orchestrator as orch

def test_dry_run_flag_is_removed():
    parser = orch.build_arg_parser()
    with pytest.raises(SystemExit):              # argparse: unrecognized argument
        parser.parse_args(["--dry-run"])

def test_no_deploy_flag_recognized():
    parser = orch.build_arg_parser()
    assert parser.parse_args(["--no-deploy"]).no_deploy is True
    assert parser.parse_args([]).no_deploy is False

def test_phase5_no_deploy_early_exit(tmp_path, monkeypatch, caplog):
    import json
    # Fixture-Tournament: mind. 1 Symbol besteht sein OOS-Gate (Whitelist nicht leer)
    tournament = {
        "fully_eligible_pairs": 1,
        "oos_not_evaluable_pairs": 0, "oos_failed_pairs": 0,
        "per_symbol_winners": {"AAA.ETORO": {
            "strategy": "SmaCrossoverStrategy", "oos_eligible": True, "oos_evaluated": True}},
        "aggregate_winner": {
            "strategy": "SmaCrossoverStrategy", "win_count": 1,
            "oos_evaluated": True, "oos_eligible": True,
            "oos_metrics": {"sortino_ratio": 1.0, "max_drawdown": 0.10}},
    }
    tfile = tmp_path / "tournament.json"
    tfile.write_text(json.dumps(tournament), encoding="utf-8")
    # State-/Whitelist-Pfad in tmp umleiten, damit kein Repo-Schreibzugriff nötig ist
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path, raising=False)
    (tmp_path / "data" / "state").mkdir(parents=True, exist_ok=True)
    # Bot-Start MUSS unterbleiben:
    def _fail_popen(*a, **k): raise AssertionError("Popen darf bei --no-deploy NICHT aufgerufen werden")
    monkeypatch.setattr(orch.subprocess, "Popen", _fail_popen)

    rc = orch.phase5_live_deployment(orch.logging.getLogger("t"),
                                     {"universe": []},
                                     {"tournament_path": str(tfile)},
                                     no_deploy=True)
    assert rc == 0
    assert "LIVE_DEPLOY_SKIPPED_NO_DEPLOY" in caplog.text
```
- [ ] CI: Test in **Tier 3** (Orchestrator) von `pytest-gate.yml` einhängen; Gate grün.

### AGENTS.md-Dokumentation
- **Kapitel 16 (Bekannte Pitfalls)** — neuen Eintrag mit nächster freier Nummer, Titel **„`--dry-run` entfernt / `--no-deploy` eingeführt"**:
  > `--dry-run` übersprang Phase 3 und schrieb ein Dummy-Tournament — irreführend, ersatzlos entfernt. Ersatz: `--no-deploy` führt Phase 1–4 vollständig aus und unterbindet ausschließlich Phase 5. **Phase 3 läuft ab sofort IMMER real**; `_create_dummy_tournament` ist nur noch No-Data-Fallback (`not TOURNAMENT_PATH.exists()`). Operator-Validierung: `python3 automation/daily_orchestrator.py --no-deploy --skip-api-fetch`.
- **Kapitel 19 (Changelog):** „0a: `--dry-run` restlos entfernt; `--no-deploy` eingeführt; Phase 3 läuft immer real; Event `LIVE_DEPLOY_SKIPPED_NO_DEPLOY`."

---
---

# Auftrag 2 — Phase 0b: Env-Isolation, Manifest-Contract & Pro-Fold-Sortinos

### Abhängigkeiten
Auftrag 1 (gemerged).

### Kontext & Ziel
Den `backtest_runner.py` für den Optimierer vorbereiten: hermetische Config/Log-Steuerung über Env-Variablen, vollständige Steuerung über ein JSON-Manifest (kein Re-Merge), und Export der Pro-Fold-OOS-Sortinos.

> **Struktur:** Drei unabhängig testbare Teile (A, B, C). Jeder Teil hat eine eigene Testdatei und ist für sich abnehmbar; sie können als getrennte Commits innerhalb dieses Auftrags landen.

### Vorbedingungen (Recon) — PFLICHT
- Lies `automation/backtest_runner.py` vollständig. Identifiziere:
  - wo die `--config`-JSON geladen wird,
  - wo pro Strategie die Parameter aufgelöst/gemerged werden (Defaults aus `strategy_defaults.json`),
  - die Methode `extract_metrics` und wie Walk-Forward-Folds verarbeitet werden.
- Falls die Param-Auflösung bzw. die Fold-Sortino-Sammlung nicht bereits als **reine, seiteneffektfreie Funktionen** vorliegen, **lege solche Seams an** (Refactor) mit den unten festgelegten Namen. Tests zielen ausschließlich auf diese Namen.

### Zu liefernde Artefakte
- Geändert: `automation/daily_orchestrator.py`, `automation/backtest_runner.py`
- Neu: `automation/tests/test_runner_env_isolation.py`, `automation/tests/test_runner_manifest_contract.py`, `automation/tests/test_runner_fold_sortinos.py`
- Geändert: `.github/workflows/pytest-gate.yml` (Tier 3)
- Geändert: `AGENTS.md`

### Teil A — Env-Isolation
**Contract:** In `daily_orchestrator.py` **und** `backtest_runner.py` werden Config- und Log-Verzeichnis über Env aufgelöst, mit testbaren Accessoren:
```python
def config_dir() -> Path:
    return Path(os.getenv("ETORO_CONFIG_DIR", str(PROJECT_ROOT / "automation" / "config")))
def logs_dir() -> Path:
    return Path(os.getenv("ETORO_LOGS_DIR", str(PROJECT_ROOT / "logs")))
```
Alle bisherigen Zugriffe auf das jeweilige Config-/Log-Verzeichnis nutzen diese Accessoren (statt fester Modul-Konstanten, die bei Import eingefroren würden).

**Test** `test_runner_env_isolation.py`:
```python
import automation.backtest_runner as runner
import automation.daily_orchestrator as orch

def test_config_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ETORO_CONFIG_DIR", str(tmp_path / "cfg"))
    assert runner.config_dir() == tmp_path / "cfg"
    assert orch.config_dir() == tmp_path / "cfg"

def test_logs_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ETORO_LOGS_DIR", str(tmp_path / "logs"))
    assert runner.logs_dir() == tmp_path / "logs"
    assert orch.logs_dir() == tmp_path / "logs"

def test_defaults_without_env(monkeypatch):
    monkeypatch.delenv("ETORO_CONFIG_DIR", raising=False)
    assert runner.config_dir().name == "config"
```

### Teil B — Manifest-Contract (kein Re-Merge)
**Contract:** Reine Funktion in `backtest_runner.py`:
```python
def resolve_strategy_params(strategy_entry: dict, defaults: dict, *, is_manifest: bool) -> dict:
    """is_manifest=True  ⇒ params verbatim (KEIN Defaults-Merge).
       is_manifest=False ⇒ Legacy: {**defaults, **params}."""
    params = dict(strategy_entry.get("params") or {})
    return params if is_manifest else {**defaults, **params}
```
Der Config-Loader setzt `is_manifest = (loaded_config.get("manifest_version") is not None)` und ruft diese Funktion. Bei `manifest_version` werden Defaults **nie** gemerged.

**Test** `test_runner_manifest_contract.py`:
```python
from automation.backtest_runner import resolve_strategy_params

DEFAULTS = {"keltner_period": 99, "foo": 7}

def test_manifest_uses_params_verbatim():
    entry = {"params": {"keltner_period": 14, "bar": 1}}
    out = resolve_strategy_params(entry, DEFAULTS, is_manifest=True)
    assert out == {"keltner_period": 14, "bar": 1}     # kein 'foo' aus Defaults
    assert "foo" not in out

def test_legacy_merges_defaults():
    entry = {"params": {"keltner_period": 14, "bar": 1}}
    out = resolve_strategy_params(entry, DEFAULTS, is_manifest=False)
    assert out == {"keltner_period": 14, "foo": 7, "bar": 1}
```

### Teil C — Pro-Fold-OOS-Sortinos
**Contract:** Reine Funktion in `backtest_runner.py`:
```python
def collect_oos_fold_sortinos(per_fold_oos: list[dict]) -> list[float]:
    """Extrahiert je Fold den OOS-Sortino (Reihenfolge erhalten, None-sicher übersprungen)."""
    return [float(f["sortino_ratio"]) for f in per_fold_oos
            if f is not None and f.get("sortino_ratio") is not None]
```
`extract_metrics` ruft diese Funktion bei `walk_forward.splits > 1` und exportiert das Ergebnis als Liste unter `aggregate_winner.oos_fold_sortinos` im resultierenden Tournament-JSON. Bestehende Aggregat-Felder bleiben unverändert.

**Test** `test_runner_fold_sortinos.py`:
```python
from automation.backtest_runner import collect_oos_fold_sortinos

def test_collect_skips_none_and_preserves_order():
    folds = [{"sortino_ratio": 1.2}, {"sortino_ratio": None}, {"sortino_ratio": 0.8}]
    assert collect_oos_fold_sortinos(folds) == [1.2, 0.8]

def test_collect_empty():
    assert collect_oos_fold_sortinos([]) == []
```

### Nicht-Ziele
- Keine Änderung der Reward-/Optimizer-Logik (das ist Phase 1).
- Keine Veränderung bestehender Aggregat-Metrik-Felder oder der Gating-Schwellen.

### CI
- [ ] Alle drei Testdateien in **Tier 3** von `pytest-gate.yml` einhängen; Gate grün.

### AGENTS.md-Dokumentation
- **Kapitel 16 (Bekannte Pitfalls)** — neuer Block **„Optimizer / `backtest_runner.py` — Config-Contract"** (exakt einfügen):
  > - `backtest_runner.py` liest Strategien + Parameter **ausschließlich** aus der via `--config` übergebenen Manifest-Datei. `strategies[].params` sind vollständig aufgelöst und autoritativ; **kein** erneutes Mergen aus `strategy_defaults.json`, sobald `manifest_version` gesetzt ist.
  > - `backtest_runner.py` respektiert `ETORO_CONFIG_DIR` (Quelle der eingefrorenen `tournament.json`) und `ETORO_LOGS_DIR`; Ergebnisse ausschließlich nach `--output`.
  > - Bei `walk_forward.splits > 1` exportiert `aggregate_winner.oos_fold_sortinos` die Pro-Fold-OOS-Sortinos als Liste (Basis des robusten Median-Rewards).
  > - Param-Auflösung und Fold-Sortino-Sammlung MÜSSEN als reine, testbare Funktionen (`resolve_strategy_params`, `collect_oos_fold_sortinos`) vorliegen.
- **Kapitel 10 (Metrics):** Verhalten der `oos_fold_sortinos`-Liste dokumentieren (wann gesetzt, Reihenfolge, None-Handling).
- **Kapitel 19 (Changelog):** „0b: `ETORO_CONFIG_DIR`/`ETORO_LOGS_DIR`; Manifest-Contract (kein Re-Merge bei `manifest_version`); `oos_fold_sortinos`-Export."

---
---

# Auftrag 3 — Phase 1a: Config-Erweiterung & Optimizer-Grundgerüst

### Abhängigkeiten
Auftrag 2 (gemerged) — Optimizer nutzt `ETORO_CONFIG_DIR`-Konvention.

### Kontext & Ziel
Infrastruktur für den Optimierer. Holdout-Tage in `backtest.json`, neue `optimizer.json`, und das Paket `automation/optimizer/` mit Provenienz, Param-Resolver und Manifest-Builder. **Zero-Hardcoding**: keine Parameter im Code.

### Vorbedingungen (Recon)
- Lies `automation/config/backtest.json`, `strategy_defaults.json`, `strategies.json`, `tournament.json`.
- Lies die Window-Logik in `daily_orchestrator.phase3_4_backtest_and_tournament` (Sonntag-Rollback) — `build_trial` spiegelt sie **exakt** (nur Sonntag `weekday()==6` → −1 Tag; Samstag unverändert).

### Zu liefernde Artefakte
- Geändert: `automation/config/backtest.json`
- Neu: `automation/config/optimizer.json`
- Neu: `automation/optimizer/__init__.py`, `manifest.py`, `resolve.py`, `trial_config.py`
- Neu: `automation/tests/test_optimizer_manifest.py`
- Geändert: `.github/workflows/pytest-gate.yml` (neuer **Tier 10: Optimizer**)
- Geändert: `AGENTS.md`

### Contracts (exakt)
**`backtest.json`** — im Block `"walk_forward"` ergänzen: `"holdout_days": 45`. Schema-Feld unter `_schema.fields` dokumentieren.

**`optimizer.json`** (neu, mit `_schema`-Block auf Deutsch) — MUSS mindestens enthalten:
```json
{
  "n_trials": 100,
  "n_startup_trials": 16,
  "seed": 42,
  "penalty_overfit_weight": 0.5,
  "penalty_dd_weight": 8.0,
  "bonus_coverage_weight": 1.0,
  "penalty_unevaluable_oos": -10.0,
  "sortino_clip_abs": 5.0
}
```
> Hinweis: `penalty_unevaluable_oos` und `sortino_clip_abs` sind ergänzt, damit die Reward-Funktion (Auftrag 4) **keinerlei** Literale enthält (Zero-Hardcoding).

**`manifest.py`:**
```python
def git_commit() -> str: ...                       # Kurz-Hash oder "unknown" (kein Git/Fehler)
def sha256_file(path: Path) -> str: ...            # hashlib.sha256 über Dateiinhalt
def catalog_fingerprint(catalog: Path | None = None) -> str: ...
# None ⇒ Default-Katalog. MUSS stabil sein und bei fehlendem Verzeichnis nicht crashen
# (deterministischer Wert über vorhandene data.parquet: rel-Pfad + size + int(mtime)).
WORK = PROJECT_ROOT / "data" / "optimizer"
```

**`resolve.py`:**
```python
def resolve_params(strategy_class: str, sampled: dict, base_cfg: Path) -> dict:
    """Reihenfolge: strategy_defaults.json < strategies.json[params] < sampled (höchste Prio)."""
```

**`trial_config.py`:**
```python
def build_trial(strategy_class: str, sampled: dict, *, study_name: str, trial_number: int,
                seed: int, now: datetime | None = None, holdout_days: int | None = None,
                n_folds: int | None = None, base_cfg: Path | None = None
               ) -> tuple[Path, Path]:
    """Erzeugt isoliertes trial_dir; kopiert config_dir()-Inhalt nach trial_dir/config;
       schreibt experiment_manifest.json (manifest_version='1.0', Provenienz, global_settings,
       genau EINE Strategie mit resolve_params()-Ergebnis). Gibt (trial_dir, manifest_path) zurück.

       now=None        ⇒ datetime.now(timezone.utc)
       holdout_days=None ⇒ aus backtest.json walk_forward.holdout_days
       n_folds=None    ⇒ aus backtest.json walk_forward.splits
       base_cfg=None   ⇒ config_dir()
       Window: end = midnight(now); if end.weekday()==6: end -= 1 Tag; end -= holdout_days;
               start = end - (is_window_days + n_folds*oos_window_days) Tage."""
```
Das Manifest-Schema entspricht Abschnitt 8.1 des Konzepts (inkl. `provenance.data_snapshot_sha256` via `catalog_fingerprint()` und `provenance.frozen_tournament_sha256` via `sha256_file(base_cfg/"tournament.json")`).

### Nicht-Ziele
- Kein Aufruf von `backtest_runner.py`, kein Subprozess (das ist Auftrag 4).
- Keine Reward-/Loop-Logik.

### Harte Abnahmekriterien & Tests (TDD)
Datei `automation/tests/test_optimizer_manifest.py`:
```python
import json, hashlib, datetime as dt
from pathlib import Path
import pytest
from automation.optimizer import manifest, resolve, trial_config

UTC = dt.timezone.utc

# --- resolve_params -------------------------------------------------------
def test_resolve_params_precedence(tmp_path):
    (tmp_path / "strategy_defaults.json").write_text(json.dumps(
        {"SmaCrossoverStrategy": {"sma_period": 5, "cooldown_bars": 12}}), "utf-8")
    (tmp_path / "strategies.json").write_text(json.dumps(
        {"strategies": [{"strategy_class": "SmaCrossoverStrategy", "params": {"cooldown_bars": 20}}]}), "utf-8")
    out = resolve.resolve_params("SmaCrossoverStrategy", {"sma_period": 8}, tmp_path)
    assert out["sma_period"] == 8       # sampled gewinnt
    assert out["cooldown_bars"] == 20   # strategies.json > defaults

# --- manifest helpers -----------------------------------------------------
def test_sha256_file_deterministic(tmp_path):
    f = tmp_path / "x.bin"; f.write_bytes(b"hello")
    assert manifest.sha256_file(f) == hashlib.sha256(b"hello").hexdigest()

def test_catalog_fingerprint_stable_and_sensitive(tmp_path):
    (tmp_path / "AAA").mkdir(); (tmp_path / "AAA" / "data.parquet").write_bytes(b"a")
    fp1 = manifest.catalog_fingerprint(tmp_path)
    assert fp1 == manifest.catalog_fingerprint(tmp_path)          # stabil
    (tmp_path / "BBB").mkdir(); (tmp_path / "BBB" / "data.parquet").write_bytes(b"b")
    assert manifest.catalog_fingerprint(tmp_path) != fp1          # reagiert auf Änderung

def test_catalog_fingerprint_missing_dir_no_crash(tmp_path):
    assert isinstance(manifest.catalog_fingerprint(tmp_path / "nope"), str)

# --- optimizer.json -------------------------------------------------------
def test_optimizer_json_parses_and_has_keys():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    for k in ("n_trials","n_startup_trials","seed","penalty_overfit_weight",
              "penalty_dd_weight","bonus_coverage_weight","penalty_unevaluable_oos","sortino_clip_abs"):
        assert k in cfg

def test_backtest_json_has_holdout():
    cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    assert isinstance(cfg["walk_forward"]["holdout_days"], int)

# --- build_trial: deterministisches end_time via injiziertem now ----------
def test_build_trial_end_time_weekday(tmp_path):
    now = dt.datetime(2026, 6, 10, 15, 0, tzinfo=UTC)   # Mittwoch
    _, mpath = trial_config.build_trial(
        "SmaCrossoverStrategy", {"sma_period": 8},
        study_name="s", trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4)
    m = json.loads(Path(mpath).read_text("utf-8"))
    assert m["manifest_version"] == "1.0"
    assert m["global_settings"]["end_time"] == "2026-04-26T00:00:00Z"   # 2026-06-10 − 45 Tage
    assert m["strategies"][0]["params"]["sma_period"] == 8

def test_build_trial_sunday_rollback(tmp_path):
    now = dt.datetime(2026, 6, 7, 9, 0, tzinfo=UTC)     # Sonntag → −1 Tag (06.06.) − 45
    _, mpath = trial_config.build_trial(
        "SmaCrossoverStrategy", {}, study_name="s", trial_number=1, seed=42,
        now=now, holdout_days=45, n_folds=4)
    m = json.loads(Path(mpath).read_text("utf-8"))
    assert m["global_settings"]["end_time"] == "2026-04-22T00:00:00Z"   # 2026-06-06 − 45 Tage

# --- Standalone-Prinzip ---------------------------------------------------
def test_no_forbidden_imports():
    for p in Path("automation/optimizer").glob("*.py"):
        src = p.read_text("utf-8")
        assert "from archive" not in src and "import archive" not in src
        assert "from adapters" not in src and "import adapters" not in src
```
- [ ] CI: Neue Sektion **„Tier 10: Optimizer"** in `pytest-gate.yml`; `test_optimizer_manifest.py` läuft; Gate grün.

### AGENTS.md-Dokumentation
- **Kapitel 2 (Module/Architektur):** Neues Modul `automation/optimizer/` beschreiben (Zweck: Closed-Loop-Hyperparameter-Optimierung; Submodule manifest/resolve/trial_config/runner/parsing/reward/spaces/confirm/run_optimization).
- **Kapitel 7 (Konfigurationssystem):** `optimizer.json` (alle Keys + Zweck) und neues Feld `walk_forward.holdout_days` in `backtest.json` dokumentieren.
- **Kapitel 19 (Changelog):** „1a: `holdout_days` in `backtest.json`; `optimizer.json`; Optimizer-Paket (manifest/resolve/trial_config) mit injizierbarem `now` für deterministische Window-Berechnung."

---
---

# Auftrag 4 — Phase 1b: Runner-Aufruf, Parser & konfigurierter Reward

### Abhängigkeiten
Auftrag 3 (gemerged).

### Kontext & Ziel
Den `backtest_runner.py` isoliert aufrufen, das `tournament_result.json` typsicher parsen und den Reward **vollständig aus Konfiguration** berechnen (kein Hardcoding).

### Zu liefernde Artefakte
- Neu: `automation/optimizer/runner.py`, `parsing.py`, `reward.py`
- Neu: `automation/tests/test_optimizer_runner.py`, `automation/tests/test_optimizer_reward_parser.py`
- Geändert: `.github/workflows/pytest-gate.yml` (Tier 10)
- Geändert: `AGENTS.md`

### Contracts (exakt)
**`runner.py`:**
```python
def run_backtest(trial_dir: Path, manifest_path: Path) -> Path:
    """Ruft backtest_runner.py als Subprozess (check=False, timeout=10800).
       catalog_path wird aus dem Manifest (global_settings.catalog_path) gelesen.
       Env: ETORO_CONFIG_DIR=trial_dir/config, ETORO_LOGS_DIR=trial_dir/logs, PYTHONUNBUFFERED=1.
       argv: [python, automation/backtest_runner.py, --momentum, --catalog-path <cat>,
              --config <manifest_path>, --output <trial_dir/tournament_result.json>]
       Gibt den Output-Pfad zurück; raise FileNotFoundError, falls Output fehlt."""
```

**`parsing.py`:** `@dataclass TournamentMetrics` (Felder: `oos_evaluated, oos_eligible, is_sortino_median, oos_sortino, oos_max_drawdown, oos_total_trades, win_count, fully_eligible_pairs`) und
```python
def parse_tournament(path: Path) -> TournamentMetrics:
    """Liest aggregate_winner/oos_metrics typsicher (None-safe).
       oos_sortino = Median von aggregate_winner.oos_fold_sortinos, falls vorhanden,
       sonst oos_metrics.sortino_ratio. is_sortino_median = median_is_sortino bzw. median_sortino."""
```

**`reward.py`:**
```python
def compute_reward(m: "TournamentMetrics", universe_size: int,
                   weights: dict | None = None, risk_dd_cap: float | None = None) -> float:
    """weights=None  ⇒ aus optimizer.json (penalty_overfit_weight, penalty_dd_weight,
                        bonus_coverage_weight, penalty_unevaluable_oos, sortino_clip_abs).
       risk_dd_cap=None ⇒ aus tournament.json (max_drawdown).
       Falls not m.oos_evaluated oder m.oos_sortino is None: return penalty_unevaluable_oos.
       base = clip(oos_sortino, -sortino_clip_abs, +sortino_clip_abs)
       overfit_gap = max(0, is_sortino_median - base); dd_excess = max(0, oos_max_drawdown - risk_dd_cap)
       coverage = win_count / max(1, universe_size)
       return base - overfit_gap*penalty_overfit_weight - dd_excess*penalty_dd_weight
              + coverage*bonus_coverage_weight"""
```

### Nicht-Ziele
- Keine Optuna-/Loop-Logik (Auftrag 5). Kein echter Backtest in Tests.

### Harte Abnahmekriterien & Tests (TDD)
Datei `automation/tests/test_optimizer_runner.py`:
```python
import json, types
from pathlib import Path
import pytest
from automation.optimizer import runner

def _make_trial(tmp_path):
    (tmp_path / "config").mkdir(); (tmp_path / "logs").mkdir()
    m = {"global_settings": {"catalog_path": "data/nautilus"}}
    mp = tmp_path / "experiment_manifest.json"; mp.write_text(json.dumps(m), "utf-8")
    return tmp_path, mp

def test_run_backtest_invocation_and_env(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)
    captured = {}
    def fake_run(argv, **kw):
        captured["argv"] = argv; captured["env"] = kw["env"]; captured["timeout"] = kw["timeout"]
        (trial_dir / "tournament_result.json").write_text("{}", "utf-8")   # Erfolg simulieren
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    out = runner.run_backtest(trial_dir, mp)
    assert out == trial_dir / "tournament_result.json"
    assert "backtest_runner.py" in " ".join(captured["argv"])
    assert "--config" in captured["argv"] and str(mp) in captured["argv"]
    assert captured["env"]["ETORO_CONFIG_DIR"] == str(trial_dir / "config")
    assert captured["env"]["ETORO_LOGS_DIR"] == str(trial_dir / "logs")
    assert captured["timeout"] == 10800

def test_run_backtest_missing_output_raises(tmp_path, monkeypatch):
    trial_dir, mp = _make_trial(tmp_path)
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1))  # erzeugt KEINE Datei
    with pytest.raises(FileNotFoundError):
        runner.run_backtest(trial_dir, mp)
```
Datei `automation/tests/test_optimizer_reward_parser.py`:
```python
import json, statistics
from pathlib import Path
from automation.optimizer import parsing, reward

def _write_tournament(tmp_path, **agg):
    data = {"fully_eligible_pairs": 1, "aggregate_winner": agg}
    p = tmp_path / "tournament_result.json"; p.write_text(json.dumps(data), "utf-8"); return p

def test_parser_median_from_fold_sortinos(tmp_path):
    p = _write_tournament(tmp_path, oos_evaluated=True, oos_eligible=True, win_count=3,
                          median_is_sortino=2.0, oos_fold_sortinos=[1.0, 3.0, 2.0],
                          oos_metrics={"sortino_ratio": 9.9, "max_drawdown": 0.1})
    m = parsing.parse_tournament(p)
    assert m.oos_sortino == statistics.median([1.0, 3.0, 2.0])   # 2.0, nicht 9.9

def test_reward_uses_config_weights(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    cap = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))["max_drawdown"]
    p = _write_tournament(tmp_path, oos_evaluated=True, oos_eligible=True, win_count=5,
                          median_is_sortino=3.0, oos_fold_sortinos=[1.0],
                          oos_metrics={"sortino_ratio": 1.0, "max_drawdown": cap + 0.1})
    m = parsing.parse_tournament(p)
    base = max(-cfg["sortino_clip_abs"], min(cfg["sortino_clip_abs"], 1.0))
    expected = (base
                - max(0.0, 3.0 - base) * cfg["penalty_overfit_weight"]
                - 0.1 * cfg["penalty_dd_weight"]
                + (5 / 100) * cfg["bonus_coverage_weight"])
    assert reward.compute_reward(m, universe_size=100) == \
        __import__("pytest").approx(expected, rel=1e-9)

def test_reward_unevaluable_penalty(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    p = _write_tournament(tmp_path, oos_evaluated=False, win_count=0)
    m = parsing.parse_tournament(p)
    assert reward.compute_reward(m, universe_size=100) == cfg["penalty_unevaluable_oos"]
```
- [ ] CI: Beide Tests in **Tier 10** ergänzen; Gate grün.

### AGENTS.md-Dokumentation
- **Kapitel 7 (Konfigurationssystem):** Dynamische Reward-Gewichtung dokumentieren (Zero-Hardcoding: Gewichte aus `optimizer.json`, DD-Cap aus `tournament.json`).
- **Kapitel 19 (Changelog):** „1b: `runner.py` (Subprozess-Aufruf, Env-Isolation, `timeout=10800`), `parsing.py` (Fold-Median, None-safe), `reward.py` (vollständig konfiguriert)."

---
---

# Auftrag 5 — Phase 1c: Optuna-Loop (SQLite), Holdout-Confirmation & PR-Export

### Abhängigkeiten
Auftrag 4 (gemerged).

### Kontext & Ziel
Zusammenführung zur Hauptschleife: TPE-Sampling, Bestätigung des besten Setups gegen den **ungesehenen Holdout**, Export eines PR-Proposals. Storage **exklusiv SQLite**.

### Zu liefernde Artefakte
- Neu: `automation/optimizer/spaces.py`, `confirm.py`, `run_optimization.py`
- Neu: `automation/tests/test_optimizer_loop.py`
- Geändert: `.github/workflows/pytest-gate.yml` (Tier 10)
- Geändert: `AGENTS.md`

### Contracts (exakt)
**`spaces.py`:** `sample_params(strategy: str, trial) -> dict` für `HourlyMeanReversionStrategy` und `SmaCrossoverStrategy` (Ranges gemäß Konzept Abschnitt 4: Hourly = `keltner_period` 6–40, `keltner_atr_period` 6–40, `keltner_multiplier` 1.0–3.5, `cooldown_bars` 2–36, `atr_trailing_multiplier` 0.3–2.5, `max_bars_in_trade` 12–96; Sma = `sma_period` 5–60, `cooldown_bars` 2–36). `raise ValueError` bei unbekannter Strategie.

**`confirm.py`:**
```python
def confirm_on_holdout(study, strategy: str, *, run_backtest=run_backtest,
                       build_trial=build_trial) -> dict:
    """Trial mit holdout_days=0, n_folds=1 (Holdout = reguläres OOS). Liest risk_dd_cap aus
       tournament.json. passed = oos_evaluated & oos_eligible & oos_sortino>0 & oos_max_drawdown<=cap.
       Rückgabe: {'passed': bool, 'metrics': dict, 'trial_dir': str}."""

def export_proposal(study, strategy: str, holdout: dict) -> Path:
    """Schreibt data/optimizer/proposal_<strategy>.json mit proposed_params_override
       (best_trial.user_attrs['sampled_params']), Reward, Holdout-Metriken,
       status = 'READY_FOR_PR' wenn holdout['passed'] sonst 'REJECTED_ON_HOLDOUT'."""
```
`run_backtest`/`build_trial` sind als Default-Argumente injizierbar (Testbarkeit).

**`run_optimization.py`:**
```python
from .runner import run_backtest            # Monkeypatch-Ziel: run_optimization.run_backtest
WORK = ...                                  # PROJECT_ROOT/data/optimizer
STORAGE = f"sqlite:///{WORK / 'studies.db'}"   # AUSSCHLIESSLICH SQLite

def make_objective(strategy: str): ...      # setzt trial.user_attr('sampled_params', sampled)
def optimize(strategy: str, n_trials: int | None = None, n_jobs: int = 1):
    # n_trials/n_startup_trials/seed aus optimizer.json (None ⇒ Konfig).
    # TPESampler(multivariate=True, group=True, n_startup_trials=..., seed=...),
    # create_study(..., storage=STORAGE, direction='maximize', load_if_exists=True).
    # study.set_user_attr('data_snapshot_sha256', catalog_fingerprint()); study.optimize(...).
def run(strategy: str):
    # optimize → confirm_on_holdout(best) → export_proposal.
```

### Nicht-Ziele
- Keine weiteren Strategien außer den beiden genannten.
- Kein Postgres/RDB in diesem Auftrag (nur SQLite).
- **Kein** echter Subprozess/Backtest in Tests (CI-Schutz).

### Harte Abnahmekriterien & Tests (TDD)
Datei `automation/tests/test_optimizer_loop.py`:
```python
import json
from pathlib import Path
import optuna
from automation.optimizer import run_optimization as ro
from automation.optimizer import spaces, confirm

def _fake_backtest_factory(sortino, dd, evaluated=True, eligible=True, win=3):
    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        out = Path(trial_dir) / "tournament_result.json"
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": evaluated, "oos_eligible": eligible, "win_count": win,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [sortino],
            "oos_metrics": {"sortino_ratio": sortino, "max_drawdown": dd}}}), "utf-8")
        return out
    return _fake

def test_spaces_sma_keys():
    t = optuna.trial.FixedTrial({"sma_period": 20, "cooldown_bars": 10})
    p = spaces.sample_params("SmaCrossoverStrategy", t)
    assert set(p) == {"sma_period", "cooldown_bars"}

def test_optimize_creates_db_and_proposal(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "run_backtest", _fake_backtest_factory(1.5, 0.1))
    study = ro.optimize("SmaCrossoverStrategy", n_trials=2)   # gemockt → kein Subprozess
    assert len(study.trials) == 2
    assert study.trials[0].params                              # Optuna hat Parameter erzeugt
    assert (ro.WORK / "studies.db").exists()                   # SQLite-Datei angelegt

def test_holdout_pass_and_reject(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm, "run_backtest", _fake_backtest_factory(1.2, 0.1))   # passing
    study = ro.optimize("SmaCrossoverStrategy", n_trials=2)
    res = confirm.confirm_on_holdout(study, "SmaCrossoverStrategy")
    assert res["passed"] is True
    p = confirm.export_proposal(study, "SmaCrossoverStrategy", res)
    assert json.loads(Path(p).read_text("utf-8"))["status"] == "READY_FOR_PR"

    monkeypatch.setattr(confirm, "run_backtest", _fake_backtest_factory(-0.5, 0.5))  # failing
    res2 = confirm.confirm_on_holdout(study, "SmaCrossoverStrategy")
    p2 = confirm.export_proposal(study, "SmaCrossoverStrategy", res2)
    assert json.loads(Path(p2).read_text("utf-8"))["status"] == "REJECTED_ON_HOLDOUT"
```
> Hinweis: `optimize` muss `n_trials` als Override akzeptieren (Default `None` ⇒ aus `optimizer.json`), damit der Mock-Test mit `n_trials=2` schnell bleibt. `WORK` muss als Modul-Attribut exportiert sein. Für Test-Isolation darf `WORK`/`STORAGE` per `monkeypatch` auf `tmp_path` zeigen, falls Parallelläufe das nötig machen.

- [ ] CI: Mock-Test in **Tier 10** ergänzen. Der Test **darf keinen** echten Backtest starten (sonst blockiert er die Pipeline stundenlang). Gate grün.

### AGENTS.md-Dokumentation
- **Kapitel 12 (Sicherheits-Leitplanken)** — exakt aus dem Konzept einfügen:
  > 1. Kein Live-Deploy aus dem Optimierer (Runner-Direktaufruf, Phase 5 nie betreten).
  > 2. Risiko-Gates eingefroren (`tournament.json` 1:1 kopiert, nie variiert).
  > 3. Holdout unberührt (keine Optimierungs-Auswertung sieht ihn).
  > 4. Human-in-the-Loop (Promotion nur per PR; Holdout-Ergebnis + Overfit-Gap im Review).
  > 5. Plausibilitäts-Wächter (Trials mit absurden Metriken werden markiert, nicht als Sieger gewertet).
- **Kapitel 16 (Bekannte Pitfalls):** Eintrag „Optimizer-Storage ausschließlich SQLite" + „Optimizer verändert `tournament.json` NIE und startet NIE Phase 5; Promotion nur per PR".
- **Kapitel 19 (Changelog):** „1c: Optuna-Loop (SQLite, TPE, Warm-Start), Holdout-Confirmation, PR-Proposal-Export. Autotuner V2 abgeschlossen."

---
---

# Anhang A — Namens-Contract (verbindlich, auftragsübergreifend)

**Orchestrator (`daily_orchestrator.py`):** `build_arg_parser()`, `config_dir()`, `logs_dir()`, `phase3_4_backtest_and_tournament(log)`, `phase5_live_deployment(log, universe_result, tournament_result, no_deploy=False)`. Event: `LIVE_DEPLOY_SKIPPED_NO_DEPLOY`.

**Runner (`backtest_runner.py`):** `config_dir()`, `logs_dir()`, `resolve_strategy_params(strategy_entry, defaults, *, is_manifest)`, `collect_oos_fold_sortinos(per_fold_oos)`. JSON-Key: `aggregate_winner.oos_fold_sortinos`.

**Optimizer-Paket (`automation/optimizer/`):**
- `manifest.py`: `git_commit()`, `sha256_file(path)`, `catalog_fingerprint(catalog=None)`, Konstante `WORK`.
- `resolve.py`: `resolve_params(strategy_class, sampled, base_cfg)`.
- `trial_config.py`: `build_trial(strategy_class, sampled, *, study_name, trial_number, seed, now=None, holdout_days=None, n_folds=None, base_cfg=None)`.
- `runner.py`: `run_backtest(trial_dir, manifest_path)`.
- `parsing.py`: `TournamentMetrics`, `parse_tournament(path)`.
- `reward.py`: `compute_reward(m, universe_size, weights=None, risk_dd_cap=None)`.
- `spaces.py`: `sample_params(strategy, trial)`.
- `confirm.py`: `confirm_on_holdout(study, strategy, *, run_backtest=..., build_trial=...)`, `export_proposal(study, strategy, holdout)`.
- `run_optimization.py`: `make_objective(strategy)`, `optimize(strategy, n_trials=None, n_jobs=1)`, `run(strategy)`, Konstanten `WORK`, `STORAGE`.

**Config-Dateien:** `automation/config/backtest.json` (+ `walk_forward.holdout_days`), `automation/config/optimizer.json`, `automation/config/tournament.json` (eingefroren), Manifest pro Trial: `experiment_manifest.json` (`manifest_version: "1.0"`).

# Anhang B — CI-Tier-Einhängung (Muster)
Jeder Auftrag hängt seine Tests in den genannten Tier. Falls **Tier 10: Optimizer** noch nicht existiert (ab Auftrag 3), als neuen Job/Step im Stil der bestehenden Tiers anlegen, z. B.:
```yaml
  tier10-optimizer:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: pytest automation/tests/test_optimizer_*.py -q
```
Bestehende Tier-Konventionen (Setup, Caching, Marker) sind zu übernehmen; keine Netzwerk-/Secret-abhängigen Tests.
