# Jules-Auftrag: Vollständige Verifikation Autotuner V2 (Phase 0a – 1c)

> **Auftragstyp:** **AUDIT / VERIFIKATION** — keine blinde Implementierung.
> **Ziel:** Beweise mit Datei-/Zeilen-Belegen, dass die Implementierung der fünf Aufträge (0a, 0b·A/B/C, 1a, 1b, 1c) **zu 100 %** den Spezifikationen entspricht, und dass `automation/AGENTS.md` die durchgeführten Änderungen **wasserdicht** abbildet (keine Drift zwischen Code und Doku).
> **Ergebnis:** Ein strukturierter Verifikations-Report (Markdown) mit Pass/Fail je Contract-Punkt + eine forensische Findings-Liste (P0–P3) im AGENTS.md-Issue-Stil. Erst nach freigegebenem Report → optionale chirurgische Remediation.

---

## 0. Quellen der Wahrheit (Recon — PFLICHT, vor jeder Aussage)

Lies vollständig und behandle in dieser Prioritätsordnung:

1. **`automation/strategies/closedloop_autotuner/claude.md`** — die **autoritativen Auftrags-Contracts** (Funktionssignaturen, Tests, CI-Tier, Nicht-Ziele). **Bei Konflikt gewinnt dieses Dokument** für Code-Verhalten.
2. **`automation/strategies/closedloop_autotuner/claude.md` → Anhang A** — der **Namens-Contract**. Jeder Funktions-/Datei-/Key-Name muss exakt so heißen. Abweichende Namen = automatisch **FAIL**.
3. **`konzept_automatisierte_strategie_optimierung_v2.md`** — Konzept/Begründung. Referenzcode darin ist illustrativ; wo er hartcodierte Literale zeigt (z. B. `RISK_DD_CAP = 0.30`, `-10.0`, `0.5`, `8.0` in `reward.py`), **gilt der claude.md-Zero-Hardcoding-Contract** (Werte aus JSON) — d. h. diese Literale dürfen im finalen Code **nicht** stehen.
4. **`abnahmeprotokoll.md`** — die formelle Operator-Checkliste. Jede Checkbox muss erfüllbar und belegt sein.
5. **`automation/AGENTS.md`** — Ist-Zustand der Doku (Changelog Kap. 19 zeigt, was angeblich erledigt wurde).

**Vor dem Audit zusätzlich lesen:**
- `automation/daily_orchestrator.py`, `automation/backtest_runner.py` (vollständig)
- Gesamtes Paket `automation/optimizer/` (alle vorhandenen `*.py`)
- `automation/config/backtest.json`, `optimizer.json`, `strategies.json`, `strategy_defaults.json`, `tournament.json`
- `automation/tests/test_orchestrator_cli.py`, `test_runner_env_isolation.py`, `test_runner_manifest_contract.py`, `test_runner_fold_sortinos.py`, `test_optimizer_manifest.py`, `test_optimizer_runner.py`, `test_optimizer_reward_parser.py`, `test_optimizer_loop.py`
- `.github/workflows/pytest-gate.yml`

**Wichtig:** Stelle pro Auftrag zuerst den **Existenz-/Fertigstellungsstatus** fest (`IMPLEMENTIERT` / `TEILWEISE` / `FEHLT`). Nimm **nicht** an, dass der Changelog korrekt ist — der Changelog selbst ist Prüfgegenstand. Existiert ein Artefakt nicht, ist das ein Finding, kein Grund zum Erstellen (in der Audit-Phase).

---

## 1. Globale Invarianten (müssen über ALLE Aufträge gelten)

Prüfe diese fünf Querschnitts-Invarianten separat und belege jede:

| # | Invariante | Prüfung (konkret) |
|---|---|---|
| G1 | **Standalone-Prinzip** | Kein `from archive`, `import archive`, `from adapters`, `import adapters` in irgendeiner Datei unter `automation/optimizer/`. AST-/grep-Beleg beifügen. |
| G2 | **Zero-Hardcoding** | Keine Magic Numbers im Code, die ein Tunable abbilden. Alle Tunables aus `backtest.json`/`tournament.json`/`optimizer.json`. **Tests lesen dieselben Werte aus JSON** (keine duplizierten Literale im Assert). Besonders streng bei `reward.py`. |
| G3 | **Namens-Contract (Anhang A)** | Jeder öffentliche Name (Funktion, Konstante, JSON-Key, Event) stimmt **zeichengenau**. Erstelle eine Abgleichtabelle „erwartet ↔ vorhanden". |
| G4 | **Sicherheits-Leitplanken** | (a) Optimizer betritt **nie** Phase 5; (b) `tournament.json` wird **nie** variiert/überschrieben; (c) Holdout wird während der Suche **nie** ausgewertet; (d) Storage **ausschließlich SQLite**; (e) Plausibilitäts-Wächter vorhanden. Jede einzeln belegen. |
| G5 | **Test-Hygiene** | **Kein** Test startet einen echten Backtest, kontaktiert Netzwerk oder braucht Secrets. `subprocess.run`/`subprocess.Popen`/`run_backtest` sind gemockt. I/O läuft gegen `tmp_path`. Belege je Testdatei, dass der Subprozess gemockt ist. |

---

## 2. Verifikations-Matrix pro Auftrag

Für **jeden** Auftrag prüfe vier Achsen: **(A) Code & Contracts**, **(B) Tests & CI**, **(C) Rückwärtskompatibilität**, **(D) AGENTS.md**. Jede Zeile bekommt `✅ PASS` / `❌ FAIL` / `⚠️ ABWEICHUNG` + Beleg (Datei:Zeile, Snippet, Testname/-output).

---

### Auftrag 1 — Phase 0a (`--dry-run` → `--no-deploy`)

**(A) Code & Contracts** — `automation/daily_orchestrator.py`
- [ ] `build_arg_parser() -> argparse.ArgumentParser` existiert; `main()` nutzt sie.
- [ ] `--dry-run` ist **restlos** entfernt (Code + jedes zugehörige Feld). Beleg: `grep -rn "dry_run\|dry-run" automation/daily_orchestrator.py` liefert **keine** Treffer außer ggf. historischen Kommentaren.
- [ ] `--no-deploy` als `action="store_true"` registriert.
- [ ] `phase3_4_backtest_and_tournament(log)` hat **keinen** `dry_run`-Parameter; der `if dry_run: _create_dummy_tournament(...); return`-Block ist gelöscht.
- [ ] `_create_dummy_tournament` wird **ausschließlich** im Zweig `if not TOURNAMENT_PATH.exists()` aufgerufen → Phase 3 läuft **immer real**.
- [ ] `phase5_live_deployment(log, universe_result, tournament_result, no_deploy: bool = False) -> int`: bei `no_deploy=True` → Log-Zeile + Event `LIVE_DEPLOY_SKIPPED_NO_DEPLOY` + `return 0`, **ohne** `subprocess.Popen`.
- [ ] `main()`-Verdrahtung: `ORCHESTRATOR_START`-Payload nutzt Key `"no_deploy"` (nicht `"dry_run"`); Banner-Zeile „NO-DEPLOY" statt „DRY-RUN".

**(B) Tests & CI** — `automation/tests/test_orchestrator_cli.py`
- [ ] `test_dry_run_flag_is_removed` (argparse → `SystemExit`), `test_no_deploy_flag_recognized`, `test_phase5_no_deploy_early_exit` vorhanden.
- [ ] `test_phase5_no_deploy_early_exit` patcht `orch.subprocess.Popen` so, dass ein Aufruf eine `AssertionError` wirft, und prüft `rc == 0` + `LIVE_DEPLOY_SKIPPED_NO_DEPLOY` im Log.
- [ ] Deterministisch, keine echten Netzwerk-/Subprozess-Calls.
- [ ] In `.github/workflows/pytest-gate.yml` unter **Tier 3** eingehängt; Gate grün.

**(C) Rückwärtskompatibilität** *(kritisch)*
- [ ] Aufruf **ohne** Argumente (täglicher Cron) → vollständige Pipeline inkl. **echtem** Deploy, exakt wie zuvor.

**(D) AGENTS.md**
- [ ] Kap. 16: neuer Pitfall „`--dry-run` entfernt / `--no-deploy` eingeführt" mit **nächster freier Nummer** (laut Changelog **#53** — verifiziere, dass die Nummer nicht kollidiert).
- [ ] Pitfall-Text inhaltlich identisch zum claude.md-Vorgabetext (Phase 3 immer real; `_create_dummy_tournament` nur No-Data-Fallback; Operator-Validierung `--no-deploy --skip-api-fetch`).
- [ ] Kap. 19: Changelog-Eintrag 0a vorhanden.

---

### Auftrag 2 — Phase 0b (Env-Isolation · Manifest-Contract · Fold-Sortinos)

**Teil A — Env-Isolation** (`daily_orchestrator.py` **und** `backtest_runner.py`)
- [ ] `config_dir()` liest `ETORO_CONFIG_DIR`, Default `PROJECT_ROOT/automation/config`.
- [ ] `logs_dir()` liest `ETORO_LOGS_DIR`, Default `PROJECT_ROOT/logs`.
- [ ] **Alle** bisherigen Zugriffe nutzen die Accessoren — **keine** beim Import eingefrorenen Modul-Konstanten mehr. Beleg: grep nach alten Konstanten-Referenzen.
- [ ] `test_runner_env_isolation.py`: Override + Default für **beide** Module (`runner.config_dir()`, `orch.config_dir()`, `logs_dir()`, Default ohne Env).

**Teil B — Manifest-Contract (kein Re-Merge)** (`backtest_runner.py`)
- [ ] Reine Funktion `resolve_strategy_params(strategy_entry, defaults, *, is_manifest) -> dict`.
- [ ] `is_manifest=True` ⇒ **params verbatim**, **kein** Defaults-Merge (Beleg: Defaults-Key taucht im Output **nicht** auf).
- [ ] `is_manifest=False` ⇒ `{**defaults, **params}` (Legacy).
- [ ] Config-Loader setzt `is_manifest = (loaded_config.get("manifest_version") is not None)` und ruft die Funktion.
- [ ] `test_runner_manifest_contract.py`: `test_manifest_uses_params_verbatim` (kein `foo` aus Defaults) + `test_legacy_merges_defaults`.

**Teil C — Pro-Fold-OOS-Sortinos** (`backtest_runner.py`)
- [ ] Reine Funktion `collect_oos_fold_sortinos(per_fold_oos) -> list[float]` — Reihenfolge erhalten, `None`-sicher übersprungen.
- [ ] `extract_metrics` ruft sie bei `walk_forward.splits > 1` und exportiert das Ergebnis als Liste unter `aggregate_winner.oos_fold_sortinos`.
- [ ] `test_runner_fold_sortinos.py`: `test_collect_skips_none_and_preserves_order` (`[1.2, 0.8]`) + `test_collect_empty`.

**(B/CI)** Alle drei Testdateien in **Tier 3**; Gate grün.

**(C) Rückwärtskompatibilität** *(kritisch)*
- [ ] Regulärer Orchestrator-Start (ohne `manifest_version` in Config) ⇒ **weiterhin** Legacy-Merge mit `strategy_defaults.json`.
- [ ] Bestehende Aggregat-Metrik-Felder und Gating-Schwellen **unverändert**.

**(D) AGENTS.md**
- [ ] Kap. 16: Block „Optimizer / `backtest_runner.py` — Config-Contract" **wörtlich** vorhanden (4 Bulletpoints aus claude.md: Manifest-Autorität, `ETORO_CONFIG_DIR`/`ETORO_LOGS_DIR`, `oos_fold_sortinos` bei `splits>1`, reine testbare Funktionen).
- [ ] Kap. 10: Verhalten von `oos_fold_sortinos` (wann gesetzt, Reihenfolge, None-Handling) dokumentiert.
- [ ] Kap. 19: Changelog-Eintrag 0b vorhanden.

---

### Auftrag 3 — Phase 1a (Config-Erweiterung & Optimizer-Grundgerüst)

**(A) Config & Module**
- [ ] `backtest.json` → `walk_forward.holdout_days` **= 45** (Typ `int`), unter `_schema.fields` dokumentiert.
- [ ] `optimizer.json` existiert mit **exakt** diesen Keys: `n_trials`, `n_startup_trials`, `seed`, `penalty_overfit_weight`, `penalty_dd_weight`, `bonus_coverage_weight`, `penalty_unevaluable_oos`, `sortino_clip_abs` + `_schema`-Block (Deutsch).
- [ ] `automation/optimizer/__init__.py`, `manifest.py`, `resolve.py`, `trial_config.py` vorhanden.
- [ ] `manifest.py`: `git_commit()` (Kurz-Hash oder `"unknown"`, kein Crash); `sha256_file(path)`; `catalog_fingerprint(catalog=None)` — **stabil** (gleicher Input → gleicher Hash), **sensitiv** (Änderung → anderer Hash), **kein Crash** bei fehlendem Verzeichnis; Konstante `WORK = PROJECT_ROOT/data/optimizer`.
- [ ] `resolve.py`: `resolve_params(strategy_class, sampled, base_cfg)` — Reihenfolge `defaults < strategies.json[params] < sampled`.
- [ ] `trial_config.py`: `build_trial(strategy_class, sampled, *, study_name, trial_number, seed, now=None, holdout_days=None, n_folds=None, base_cfg=None) -> tuple[Path, Path]`.
  - [ ] `now=None` ⇒ `datetime.now(timezone.utc)`; `holdout_days=None` ⇒ aus `backtest.json`; `n_folds=None` ⇒ aus `backtest.json walk_forward.splits`; `base_cfg=None` ⇒ `config_dir()`.
  - [ ] Kopiert `config_dir()`-Inhalt nach `trial_dir/config` (eingefrorene `tournament.json`).
  - [ ] Schreibt `experiment_manifest.json` mit `manifest_version="1.0"`, Provenienz (`git_commit`, `data_snapshot_sha256` via `catalog_fingerprint()`, `frozen_tournament_sha256` via `sha256_file(base_cfg/"tournament.json")`), `global_settings`, **genau EINE** Strategie mit `resolve_params()`-Ergebnis.
  - [ ] **Window-Logik exakt:** `end = midnight(now)`; falls `end.weekday()==6` (Sonntag) → `end -= 1 Tag`; `end -= holdout_days`; `start = end - (is_window_days + n_folds*oos_window_days)`.

**(B) Tests & CI** — `automation/tests/test_optimizer_manifest.py`
- [ ] `test_resolve_params_precedence` (sampled gewinnt; `strategies.json` > defaults).
- [ ] `test_sha256_file_deterministic`, `test_catalog_fingerprint_stable_and_sensitive`, `test_catalog_fingerprint_missing_dir_no_crash`.
- [ ] `test_optimizer_json_parses_and_has_keys`, `test_backtest_json_has_holdout`.
- [ ] **Datums-Determinismus per injiziertem `now`:** `test_build_trial_end_time_weekday` → Mittwoch `2026-06-10` − 45 Tage = **`2026-04-26T00:00:00Z`**; `test_build_trial_sunday_rollback` → Sonntag `2026-06-07` → `2026-06-06` − 45 = **`2026-04-22T00:00:00Z`**.
- [ ] `test_no_forbidden_imports` (kein `archive`/`adapters`).
- [ ] Neue Sektion **Tier 10: Optimizer** in `pytest-gate.yml`; Gate grün.

**(C) Rückwärtskompatibilität**
- [ ] `automation/optimizer/` hält Standalone strikt ein; keine bestehende Funktionalität durch die neuen JSON-Dateien beeinträchtigt.

**(D) AGENTS.md**
- [ ] Kap. 2: Modul `automation/optimizer/` mit allen Submodulen beschrieben.
- [ ] Kap. 7: `optimizer.json` (alle Keys + Zweck) und `walk_forward.holdout_days` dokumentiert.
- [ ] Kap. 19: Changelog 1a vorhanden.

---

### Auftrag 4 — Phase 1b (Runner-Aufruf · Parser · konfigurierter Reward)

**(A) Contracts**
- [ ] `runner.py` → `run_backtest(trial_dir, manifest_path) -> Path`:
  - [ ] `subprocess.run(..., check=False, timeout=10800)`.
  - [ ] `catalog_path` wird **aus dem Manifest** (`global_settings.catalog_path`) gelesen — nicht hartcodiert.
  - [ ] Env: `ETORO_CONFIG_DIR=trial_dir/config`, `ETORO_LOGS_DIR=trial_dir/logs`, `PYTHONUNBUFFERED=1`.
  - [ ] `argv` exakt: `[python, automation/backtest_runner.py, --momentum, --catalog-path <cat>, --config <manifest_path>, --output <trial_dir/tournament_result.json>]`.
  - [ ] `raise FileNotFoundError`, falls Output fehlt.
- [ ] `parsing.py` → `@dataclass TournamentMetrics` mit **genau** den Feldern `oos_evaluated, oos_eligible, is_sortino_median, oos_sortino, oos_max_drawdown, oos_total_trades, win_count, fully_eligible_pairs`.
  - [ ] `parse_tournament(path)` None-safe; `oos_sortino = median(oos_fold_sortinos)` falls vorhanden, sonst `oos_metrics.sortino_ratio`; `is_sortino_median` toleriert `median_is_sortino` **und** `median_sortino`.
- [ ] `reward.py` → `compute_reward(m, universe_size, weights=None, risk_dd_cap=None) -> float`:
  - [ ] `weights=None` ⇒ aus `optimizer.json`; `risk_dd_cap=None` ⇒ aus `tournament.json` `max_drawdown`.
  - [ ] **Zero-Hardcoding-Härtetest:** Keine numerischen Literale (`-10.0`, `0.5`, `8.0`, `5.0`, `0.30`, `1.0` als Gewicht) im Python-Code. Beleg: grep + manuelle Sicht.
  - [ ] `not m.oos_evaluated or m.oos_sortino is None` ⇒ `return penalty_unevaluable_oos`.
  - [ ] Formel exakt: `base = clip(oos_sortino, ±sortino_clip_abs)`; `overfit_gap = max(0, is_sortino_median − base)`; `dd_excess = max(0, oos_max_drawdown − risk_dd_cap)`; `coverage = win_count / max(1, universe_size)`; `return base − overfit_gap·penalty_overfit_weight − dd_excess·penalty_dd_weight + coverage·bonus_coverage_weight`.

**(B) Tests & CI**
- [ ] `test_optimizer_runner.py`: `test_run_backtest_invocation_and_env` (mockt `runner.subprocess.run`, prüft argv/env/`timeout==10800`, simuliert Output-Datei) + `test_run_backtest_missing_output_raises` (mock ohne Datei ⇒ `FileNotFoundError`). **Kein** echter Backtest.
- [ ] `test_optimizer_reward_parser.py`: `test_parser_median_from_fold_sortinos` (Median, nicht Aggregat), `test_reward_uses_config_weights` (erwartete Werte aus Mock-Config gelesen, **keine** Literale im Assert), `test_reward_unevaluable_penalty`.
- [ ] Beide in **Tier 10**; Gate grün.

**(C) Rückwärtskompatibilität**
- [ ] Aufruf von `automation/backtest_runner.py` im Orchestrator unbeeinträchtigt.

**(D) AGENTS.md**
- [ ] Kap. 7: dynamische Reward-Gewichtung dokumentiert (Gewichte aus `optimizer.json`, DD-Cap aus `tournament.json`).
- [ ] Kap. 19: Changelog 1b vorhanden.

---

### Auftrag 5 — Phase 1c (Optuna-Loop · Holdout-Confirmation · PR-Export)

> **Hinweis:** Falls die 1c-Artefakte fehlen, lautet das Finding „Auftrag 1c nicht implementiert" (P0), obwohl `spaces`/`confirm`/`run_optimization` bereits in AGENTS.md Kap. 2 gelistet sind → das wäre eine Doku-Drift (siehe §3).

**(A) Contracts**
- [ ] `spaces.py` → `sample_params(strategy, trial) -> dict` für `HourlyMeanReversionStrategy` **und** `SmaCrossoverStrategy`:
  - [ ] Hourly-Ranges: `keltner_period` 6–40, `keltner_atr_period` 6–40, `keltner_multiplier` 1.0–3.5, `cooldown_bars` 2–36, `atr_trailing_multiplier` 0.3–2.5, `max_bars_in_trade` 12–96.
  - [ ] Sma-Ranges: `sma_period` 5–60, `cooldown_bars` 2–36.
  - [ ] `raise ValueError` bei unbekannter Strategie.
- [ ] `confirm.py` → `confirm_on_holdout(study, strategy, *, run_backtest=..., build_trial=...) -> dict`:
  - [ ] Trial mit `holdout_days=0`, `n_folds=1` (Holdout = reguläres OOS).
  - [ ] `risk_dd_cap` aus `tournament.json`.
  - [ ] `passed = oos_evaluated & oos_eligible & oos_sortino>0 & oos_max_drawdown<=cap`.
  - [ ] Rückgabe `{'passed', 'metrics', 'trial_dir'}`.
- [ ] `confirm.py` → `export_proposal(study, strategy, holdout) -> Path`: schreibt `data/optimizer/proposal_<strategy>.json` mit `proposed_params_override = best_trial.user_attrs['sampled_params']`, Reward, Holdout-Metriken, `status = 'READY_FOR_PR'` bzw. `'REJECTED_ON_HOLDOUT'`.
- [ ] `run_optimization.py`:
  - [ ] `from .runner import run_backtest` (Monkeypatch-Ziel `run_optimization.run_backtest`).
  - [ ] Konstante `WORK` als Modul-Attribut exportiert.
  - [ ] `STORAGE = f"sqlite:///{WORK/'studies.db'}"` — **ausschließlich SQLite**, **keine** PostgreSQL-/RDB-Artefakte im Code.
  - [ ] `make_objective(strategy)` setzt `trial.set_user_attr('sampled_params', sampled)`.
  - [ ] `optimize(strategy, n_trials=None, n_jobs=1)`: `n_trials`/`n_startup_trials`/`seed` aus `optimizer.json` (None ⇒ Konfig); `TPESampler(multivariate=True, group=True, n_startup_trials=..., seed=...)`; `create_study(..., storage=STORAGE, direction='maximize', load_if_exists=True)`; `study.set_user_attr('data_snapshot_sha256', catalog_fingerprint())`.
  - [ ] `run(strategy)`: `optimize → confirm_on_holdout(best) → export_proposal`.

**(B) Tests & CI** — `automation/tests/test_optimizer_loop.py`
- [ ] `test_spaces_sma_keys` (`{"sma_period","cooldown_bars"}`).
- [ ] `test_optimize_creates_db_and_proposal`: `run_backtest` gemockt (`_fake_backtest_factory`), `n_trials=2`, prüft `len(study.trials)==2` + `(ro.WORK/'studies.db').exists()`.
- [ ] `test_holdout_pass_and_reject`: passing ⇒ `status=='READY_FOR_PR'`; failing ⇒ `status=='REJECTED_ON_HOLDOUT'`.
- [ ] **CI-Schutz-Check (kritisch):** Test darf **keinen** echten Subprozess/Backtest auslösen; `WORK`/`STORAGE` ggf. via `monkeypatch` auf `tmp_path`.
- [ ] In **Tier 10**; Gate grün.

**(C) Nicht-Ziele eingehalten**
- [ ] Nur die beiden genannten Strategien in `spaces.py`.
- [ ] Kein Postgres/RDB in 1c.
- [ ] Modul greift **nie** selbst in Phase 5 / Live-Handel ein.

**(D) AGENTS.md**
- [ ] Kap. 12: **Sicherheits-Leitplanken** — **alle 5 Punkte exakt** (kein Live-Deploy; Gates eingefroren; Holdout unberührt; Human-in-the-Loop/PR; Plausibilitäts-Wächter).
- [ ] Kap. 16: Einträge „Optimizer-Storage ausschließlich SQLite" + „Optimizer verändert `tournament.json` NIE und startet NIE Phase 5; Promotion nur per PR".
- [ ] Kap. 19: Changelog 1c „Autotuner V2 abgeschlossen".

---

## 3. AGENTS.md — Wasserdicht-Audit (eigenständige Sektion)

Über die per-Auftrag-Doku hinaus prüfe die **Integrität** der gesamten `automation/AGENTS.md`:

1. **Code↔Doku-Konsistenz (Drift):** Jede in Kap. 2 gelistete Optimizer-Datei existiert tatsächlich und umgekehrt (kein Submodul gelistet, das fehlt; kein vorhandenes Submodul ungelistet). Insbesondere: Sind `spaces`/`confirm`/`run_optimization` gelistet **und** vorhanden?
2. **Changelog-Vollständigkeit (Kap. 19):** Genau ein Eintrag je Auftrag (0a, 0b, 1a, 1b, 1c) mit Datum + Datei-Liste; die genannten Dateien existieren und wurden plausibel berührt.
3. **Pitfall-Nummern-Integrität:** Prüfe auf **Kollisionen/Duplikate**. *Hinweis:* Die Datei enthält aktuell **mehrere mit „#50" nummerierte Pitfalls** (Alternation-Lock, Zero-Trade-Cascades, Gate-Scope-Mismatch, Restriktive-Strategiefrequenzen u. a.). Liste alle Nummern-Duplikate als Finding (P2) und schlage eine eindeutige Neu-Nummerierung vor — **ohne** inhaltliche Änderung.
4. **Contract-Blöcke wörtlich:** Der „Config-Contract"-Block (0b) und die „Sicherheits-Leitplanken" (1c) müssen textlich der claude.md-/Konzept-Vorgabe entsprechen, nicht paraphrasiert sein.
5. **Kein „Hidden Gate":** Jede in den Tests/Code referenzierte Schwelle (z. B. `holdout_days`, `sortino_clip_abs`, `penalty_*`) ist in Kap. 7 dokumentiert. Keine undokumentierten Konfig-Parameter.
6. **Drei-Wege-Abgleich:** Wo `claude.md`, `konzept_v2.md` und `AGENTS.md` Werte/Namen nennen, dokumentiere jede Divergenz (z. B. Reward-Literale im Konzept vs. Zero-Hardcoding in claude.md/Code; `median_sortino` vs. `median_is_sortino`). Für Code gilt claude.md; für Zero-Hardcoding gelten die JSON-Werte.

---

## 4. Konkrete Verifikations-Kommandos (auszuführen & Output beifügen)

```bash
# --- Standalone / verbotene Importe (G1) ---
grep -rnE "from (archive|adapters)|import (archive|adapters)" automation/optimizer/ || echo "OK: keine verbotenen Importe"

# --- 0a: --dry-run restlos entfernt ---
grep -rni "dry.run\|dry_run" automation/daily_orchestrator.py

# --- Zero-Hardcoding im Reward (G2) — verdächtige Literale ---
grep -nE "(-?10\.0|0\.5|8\.0|5\.0|0\.30|1\.0)" automation/optimizer/reward.py

# --- SQLite-Exklusivität (G4d) ---
grep -rniE "postgres|psycopg|rdb|mysql" automation/optimizer/ || echo "OK: nur SQLite"
grep -n "sqlite:///" automation/optimizer/run_optimization.py

# --- Namens-Contract Stichproben (G3) ---
grep -rn "def build_arg_parser\|def config_dir\|def logs_dir\|def resolve_strategy_params\|def collect_oos_fold_sortinos" automation/
grep -rn "def resolve_params\|def catalog_fingerprint\|def build_trial\|def run_backtest\|def parse_tournament\|def compute_reward\|def sample_params\|def confirm_on_holdout\|def export_proposal\|def make_objective\|def optimize" automation/optimizer/
grep -rn "LIVE_DEPLOY_SKIPPED_NO_DEPLOY\|oos_fold_sortinos" automation/

# --- Config-Keys (1a) ---
python3 - <<'PY'
import json, pathlib
opt = json.loads(pathlib.Path("automation/config/optimizer.json").read_text("utf-8"))
need = {"n_trials","n_startup_trials","seed","penalty_overfit_weight","penalty_dd_weight",
        "bonus_coverage_weight","penalty_unevaluable_oos","sortino_clip_abs"}
print("optimizer.json fehlende Keys:", need - set(opt))
bt = json.loads(pathlib.Path("automation/config/backtest.json").read_text("utf-8"))
print("holdout_days:", bt["walk_forward"].get("holdout_days"), type(bt["walk_forward"].get("holdout_days")).__name__)
PY

# --- Vollständige Test-Suite der Aufträge (deterministisch, ohne echten Backtest) ---
pytest automation/tests/test_orchestrator_cli.py \
       automation/tests/test_runner_env_isolation.py \
       automation/tests/test_runner_manifest_contract.py \
       automation/tests/test_runner_fold_sortinos.py \
       automation/tests/test_optimizer_manifest.py \
       automation/tests/test_optimizer_runner.py \
       automation/tests/test_optimizer_reward_parser.py \
       automation/tests/test_optimizer_loop.py -v

# --- CI-Tier-Einhängung prüfen ---
grep -nE "tier3|Tier 3|tier10|Tier 10|test_optimizer_|test_orchestrator_cli|test_runner_" .github/workflows/pytest-gate.yml
```

> **Timing-Wächter:** Wenn `pytest` für die Optimizer-Tests **deutlich länger als wenige Sekunden** läuft, ist sehr wahrscheinlich ein Subprozess-Mock undicht (echter Backtest) → **sofort als P0-Finding** markieren (verstößt gegen G5 und blockiert die CI-Pipeline).

---

## 5. Ausgabeformat — Verifikations-Report (Pflichtstruktur)

Liefere den Report als `automation/strategies/closedloop_autotuner/verifikation_report.md` mit **genau** dieser Struktur:

```markdown
# Verifikations-Report — Autotuner V2 (Phase 0a–1c)
Datum · Git-Commit · geprüfte Dateien

## 1. Executive Summary
Gesamtverdikt je Auftrag: 0a ✅/❌ · 0b ✅/❌ · 1a ✅/❌ · 1b ✅/❌ · 1c ✅/❌
Globale Invarianten: G1–G5 ✅/❌
Anzahl Findings: P0=… P1=… P2=… P3=…
Ein-Satz-Fazit: „100% spec-konform" / „N Abweichungen, M davon mergeblockierend".

## 2. Verifikations-Matrix
Pro Auftrag eine Tabelle: | Contract-Punkt | Status | Beleg (Datei:Zeile / Testname / Output) |
(Jede Checkbox aus §2 dieses Auftrags wird zu einer Zeile.)

## 3. Globale Invarianten G1–G5
Je Invariante: Status + Beleg (grep-/AST-Output, Test-Laufzeit).

## 4. AGENTS.md Wasserdicht-Audit
Punkte 1–6 aus §3: Status + Beleg. Pitfall-Nummern-Kollisionen explizit auflisten.

## 5. Findings (forensisch, AGENTS.md-Issue-Stil)
Je Finding:
### [P0|P1|P2|P3] <Kurztitel>
- **Datei/Stelle:** path:line
- **Soll (Spec):** <claude.md/Anhang-A-Referenz, exakter Contract>
- **Ist:** <was tatsächlich im Code/Doku steht>
- **Auswirkung:** <Funktional? Merge-Blocker? Doku-Drift?>
- **Fix-Vorschlag:** <minimal, chirurgisch — Code- oder Doku-Patch>

## 6. Go / No-Go
Merge-Empfehlung je Auftrag (gemäß abnahmeprotokoll.md): GO / NO-GO + Begründung.
```

**Severity-Leitfaden:** P0 = funktionaler Bug / Sicherheits-Leitplanke verletzt / undichter Subprozess-Mock / fehlendes Pflicht-Artefakt. P1 = Contract-Abweichung (falsche Signatur, falscher Wert, fehlender Test). P2 = AGENTS.md-Drift / Pitfall-Nummern-Kollision / fehlende Doku. P3 = Stil/Klarheit.

---

## 6. Nicht-Ziele (strikt)

- **Keine stillen Fixes in der Audit-Phase.** Erst Report, dann (nach Operator-Freigabe) Remediation.
- **Kein Scope-Creep:** keine neuen Features, keine Optimizer-Strategien über die zwei in 1c spezifizierten hinaus.
- **Risiko-Gates NIE anfassen:** `tournament.json` wird weder gelesen-zum-Variieren noch geschrieben.
- **Phase 5 / Live-Handel NIE auslösen** — auch nicht testweise.
- **Bestehende Aggregat-Metrik-Logik und Gating-Schwellen nicht „verschönern"** (insb. die bewusst hybride OOS-Aggregation und die `_oos_trade_records`-Pipeline sind geschützt).

---

## 7. Optionale Remediation (nur nach freigegebenem Report)

Falls Findings bestätigt werden:
- **Ein Anliegen pro Commit**, deutsche Commit-Message, je Auftrag ein PR (gemäß claude.md Globale Konventionen #9).
- Jeder Code-Fix bringt den Code **in Spec-Konformität** (Anhang A maßgeblich) — keine Spec-Umdeutung.
- Jeder Fix aktualisiert die betroffenen AGENTS.md-Kapitel **und** Kap. 19 (Changelog) im selben Commit.
- Pitfall-Nummern-Kollisionen werden in einem **separaten** Doku-only-Commit eindeutig neu nummeriert (rein redaktionell, keine Inhaltsänderung).
- Nach jeder Änderung: Pre-Flight-Checks + vollständiges `pytest`-Gate grün.
