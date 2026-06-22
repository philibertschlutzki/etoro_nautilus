# Per-Symbol Micro-Tuning (Ansatz 4) — Jules-Umsetzungs-Issues

> **System:** eToro Nautilus v2.0 (Standalone `automation/`)
> **Quelle der Wahrheit (Verhalten):** dieses Dokument + bestehender Code unter `automation/optimizer/`.
> **Quelle der Wahrheit (Begründung):** `Kapitel 4: Ansatz 4 — Per-Symbol Micro-Tuning` und `konzept_automatisierte_strategie_optimierung_v2.md`.
> **Status:** Verfeinerung des `§4.10`-Auftragsschnitts in 11 einzeln abnehmbare Jules-PRs.

---

## 0. Wie dieses Dokument zu nutzen ist

- **Jedes `## A4.x`-Kapitel = genau ein GitHub-Issue = genau ein PR.** Copy-paste-fähig.
- Reihenfolge strikt nach der **Abhängigkeitsmatrix (§4)**. Ein Issue startet erst, wenn seine Abhängigkeiten gemerged sind.
- Jedes Issue ist **eigenständig testbar** (frischer Checkout + gemergte Vorgänger ⇒ grüne Tests).
- **Labels-Vorschlag:** `area:optimizer`, `ansatz-4`, `prio:P0..P3`, `ci:tier10`, `touches-prod` (nur A4.8).

---

## 1. Globale Konventionen & harte Invarianten

Es gelten **alle** Konventionen aus `automation/strategies/closedloop_autotuner/claude.md` (Standalone-Prinzip, deutsche Logs/Kommentare & englische Bezeichner/Tests, Zero-Hardcoding, TDD mit gemockten Subprozessen, Dependency Injection, CI-Gate grün, AGENTS.md-Pflicht, fortlaufende Pitfall-Nummern, chirurgische Commits, Reversibilität, Namens-Contract). Zusätzlich für **alle** Issues hier:

| # | Harte Invariante |
|---|---|
| **HI-1** | **`daily_orchestrator.py` wird NIE verändert.** Keine Zeile, keine Signatur, kein neuer Import. Verifiziere je PR: `git diff --stat` zeigt `automation/daily_orchestrator.py` **nicht**. (Ausnahme: keine.) |
| **HI-2** | **Rückwärtskompatibilität.** Fehlt `instrument_overrides` / `global_settings.instruments`, ist das Verhalten **bit-identisch** zum Ist-Zustand. Jeder PR liefert einen Regressionstest „ohne Override → Alt-Verhalten". |
| **HI-3** | **Kein Live-Deploy aus dem Optimierer.** Phase 5 wird nie betreten; der Sweep erzeugt ausschließlich Proposal-JSONs. Promotion nur per menschlich freigegebenem PR. |
| **HI-4** | **Risiko-Gates eingefroren.** `tournament.json` wird nie variiert/überschrieben. Nur Strategie-Parameter werden optimiert. |
| **HI-5** | **Holdout unberührt während der Suche.** Nur Gate 3 fasst den Holdout an — einmal pro promotetem Kandidaten, nie im Such-Korridor. |
| **HI-6** | **Zero-Hardcoding.** Jeder neue Tunable (Strafgewichte, Margen, Schwellen) lebt in `optimizer.json`/`backtest.json`/`tournament.json`. Tests lesen dieselben Werte aus der JSON — **keine** duplizierten Literale im Assert. |
| **HI-7** | **Test-Hygiene (Timing-Wächter).** Kein Test startet einen echten Backtest, kontaktiert Netz oder braucht Secrets. `subprocess.run`/`run_backtest`/Backtest-Entry werden gemockt; `WORK`/`STORAGE` per `monkeypatch` auf `tmp_path`. Läuft eine Optimizer-Testdatei **> wenige Sekunden** ⇒ undichter Mock ⇒ **P0**.

> **Recon-Hinweis (gilt global):** Die Dateien `automation/backtest_runner.py`, `automation/daily_orchestrator.py`, `automation/momentum_ls_run.py`, `automation/momentum_ls_allocator.py`, `automation/historical_fetcher.py`, `automation/universe_fetcher.py`, sämtliche `automation/config/*.json` und `automation/AGENTS.md` lagen bei Erstellung dieses Plans **nicht** vollständig vor. Funktions-/Seam-Namen für diese Dateien stammen aus `CODE_AUDIT_ZERO_HARDCODING.md` und `claude.md`. **Jeder PR, der diese Dateien berührt, beginnt mit verpflichtender Recon und meldet Abweichungen als Finding, statt zu raten.**

---

## 2. Offene Entscheidungspunkte (bitte vor Start klären)

> Defaults sind so gewählt, dass kein Issue blockiert ist. Bei abweichender Antwort sind nur die genannten Issues betroffen.

### EP-1 — Darf `backtest_runner.py` verändert werden?
Der Daily-Orchestrator ruft `backtest_runner.py` als Subprozess auf. Per-Symbol-Tuning braucht dort (a) einen `instruments`-Filter (A4.2) und (b) eine `instrument`-aware Param-Auflösung (A4.1/A4.8). Beide sind **strikt additiv und rückwärtskompatibel** (ohne Overrides → identisches Verhalten), berühren aber Code, der im Produktionspfad liegt.
**Default:** A4.1/A4.2 nehmen nur **reine, gemockt-testbare Hilfsfunktionen** in `backtest_runner.py` auf + binden sie an den vorhandenen Seam; A4.8 (das eigentliche Live-Wiring) ist als `touches-prod` markiert und **deferrbar**, bis erste Proposals existieren. Bestätige bitte, dass additive Änderungen an `backtest_runner.py` zulässig sind (HI-1 betrifft nur `daily_orchestrator.py`).

### EP-2 — SQLite-Leitplanke jetzt aufweichen?
Der Plan (§4.9) will Postgres für parallele Sweeps. Die bestehende Leitplanke verlangt „ausschließlich SQLite".
**Default:** Der Sweep (A4.6) nutzt **pro (Strategie, Symbol)-Study eine eigene SQLite-Datei** → keine Lock-Contention, reproduzierbar pro Study, **kein Postgres nötig**. Damit bleibt die Leitplanke für den MVP intakt. **A4.7 (Postgres) wird optional** und nur für echtes Multi-Maschinen-Scaling gebraucht; es weicht die Leitplanke explizit & dokumentiert auf — bitte separat freigeben.

### EP-3 — Pilot-Scope
6 Strategien × 70 Symbole × 100 Trials ≈ 420 CPU-h. Auf dem Mac Mini (~8 Kerne) ≈ 52h Wall-Clock.
**Default/Empfehlung:** Pilot mit **einer** Strategie (Vorschlag `SmaCrossoverStrategy` — kleinster Suchraum, schnellster Durchlauf) und **Tier A** (nur Symbole, die unter globalen Parametern bereits Tournament-Gewinner sind). Erst nach validiertem Pilot auf weitere Strategien/Tiers skalieren. Welche Strategie soll der Pilot sein?

### EP-4 — Quelle der Symbol-Liste & des „Deployable-Set" (Tier A)
Der Sweep braucht (1) das Symbol-Universum und (2) die Liste der aktuellen Tournament-Gewinner pro Strategie (für Tier A).
**Default:** Universum aus `data/universe/momentum_ls.json` (wie `run_optimization.py` es heute liest). Tier-A-Gewinner aus dem zuletzt geschriebenen `tournament_result.json` (`per_symbol_winners`). Bitte bestätigen, dass `per_symbol_winners[symbol].strategy` die maßgebliche Quelle für „diese Strategie gewinnt auf diesem Symbol" ist.

---

## 3. Empfehlung: MVP-Pfad & Pilot

**MVP (liefert nutzbare Per-Symbol-Proposals, ohne Produktionspfad):** A4.0 → A4.1 → A4.2 → A4.3 → A4.4 → A4.5a → A4.5b → A4.6.
**Optionale Erweiterungen danach:**
- **A4.7** nur, wenn Multi-Maschinen-Parallelität gebraucht wird (Postgres, Leitplanke-Aufweichung).
- **A4.8** schaltet Proposals live (Matrix + Momentum-LS). Erst sinnvoll, wenn gemergte `instrument_overrides` existieren. `touches-prod`.
- **A4.9** reine Performance (In-Process-Backtest). Optional, zuletzt.

**Pilot-Reihenfolge konkret:** Nach A4.6 zunächst `--strategies SmaCrossoverStrategy --tier deployable` gegen 3–5 reale Symbole laufen lassen, Proposals & Gate-3-Verdikte manuell prüfen, dann skalieren.

---

## 4. Abhängigkeits- & Reihenfolge-Matrix

| Issue | Inhalt | Berührt Prod-Pfad? | CI-Tier | Hängt ab von |
|---|---|---|---|---|
| **A4.0** | Deklarativer numerischer Suchraum-Bounds-Extractor (`bounds.py`) | nein | Tier 10 | — |
| **A4.1** | `instrument_overrides`-Schema + Resolution (`resolve.py` optimizer-seitig; reine `resolve_strategy_params(instrument=…)` in `backtest_runner.py`) | nein (nur reine Funktion) | Tier 10 | — |
| **A4.2** | `global_settings.instruments`-Filter: `build_trial(instruments=…)` + reiner Universum-Filter in `backtest_runner.py` | nein (nur reine Funktion) | Tier 3/10 | A4.1 |
| **A4.3** | Per-Symbol-Reward (`reward_mode`, `param_pen`, Coverage-Drop bei `universe_size==1`) | nein | Tier 10 | A4.0, A4.1 |
| **A4.4** | Gate 1 Daten-Suffizienz: reine Funktion `is_symbol_tunable(...)` (`gate.py`) | nein | Tier 10 | — |
| **A4.5a** | `optimize_symbol(...)` (Single-Symbol-Study, per-Study-SQLite, Warm-Start = Gate 2) | nein | Tier 10 | A4.1, A4.2, A4.3 |
| **A4.5b** | Gate 3: `confirm_per_symbol_promotion(...)` + `export_symbol_proposal(...)` (Marge vs. Global auf Holdout) | nein | Tier 10 | A4.5a |
| **A4.6** | `sweep.py` Meta-Orchestrator + CLI (Enumeration, Tiering, Dispatch, nie Phase 5) | nein | Tier 10 | A4.4, A4.5a, A4.5b |
| **A4.7** | *(optional)* Konfigurierbare Storage-URL (SQLite-Default, Postgres-Opt-in) | nein | Tier 10 | A4.6 |
| **A4.8** | *(optional, `touches-prod`)* Live-Integration: Matrix-Wiring + `momentum_ls_run.py` | **JA** | Tier 3/10 | A4.1 |
| **A4.9** | *(optional)* Overhead-Reduktion: importierbarer In-Process-Backtest-Entry + Config-Sharing pro Study | nein | Tier 10 | A4.5a |

---

# 5. Die Issues

---

## A4.0 — Deklarativer numerischer Suchraum-Bounds-Extractor

**Meta:** Prio P1 · Depends: — · CI: Tier 10 · Prod-Pfad: nein

### Kontext & Ziel
Die Per-Symbol-Reward-Regularisierung (`param_pen`, A4.3) normiert jeden Parameter über seine Suchraum-Grenzen auf `[0,1]`. Heute sind diese Grenzen **nur implizit** in den `trial.suggest_*`-Aufrufen in `spaces.py` codiert. Statt sie zu duplizieren (DRY-Verletzung), extrahieren wir sie **aus `spaces.sample_params` selbst** per Introspektion über ein aufzeichnendes Trial-Objekt — eine einzige Quelle der Wahrheit, verhaltensneutral.

### Vorbedingungen (Recon)
- `automation/optimizer/spaces.py` vollständig lesen. Beachte: abgeleitete Parameter (`macd_slow = fast + gap`) sind **keine** `suggest_*`-Aufrufe; kategoriale (`require_vwap_confirmation`) sind `suggest_categorical`.

### Zu liefernde Artefakte
- Neu: `automation/optimizer/bounds.py`
- Neu: `automation/tests/test_optimizer_bounds.py`
- Geändert: `.github/workflows/pytest-gate.yml` (Tier 10)
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/optimizer/bounds.py
from typing import Any

class _RecordingTrial:
    """Minimales Trial-Double: zeichnet (low, high) numerischer suggest-Aufrufe auf
       und liefert einen deterministischen Wert (= low) zurück, damit sample_params
       seiteneffektfrei bis zum Ende durchläuft. suggest_categorical wird verzeichnet,
       aber NICHT als numerische Bound geführt."""
    def __init__(self) -> None:
        self.numeric: dict[str, tuple[float, float]] = {}
        self.categorical: dict[str, list[Any]] = {}
    def suggest_int(self, name: str, low: int, high: int, *a, **k) -> int: ...
    def suggest_float(self, name: str, low: float, high: float, *a, **k) -> float: ...
    def suggest_categorical(self, name: str, choices: list, *a, **k): ...

def extract_numeric_bounds(strategy: str) -> dict[str, tuple[float, float]]:
    """Ruft spaces.sample_params(strategy, _RecordingTrial()) und gibt {param: (low, high)}
       für alle numerischen suggest-Parameter zurück. Abgeleitete/kategoriale Parameter
       sind NICHT enthalten. raise ValueError bei unbekannter Strategie (propagiert aus spaces)."""

def normalized_param_distance(sampled: dict, reference: dict,
                              bounds: dict[str, tuple[float, float]]) -> float:
    """Mittlere quadrierte, auf [0,1] normierte Abweichung über alle Keys,
       die in bounds UND sampled UND reference vorkommen.
       Pro Key: span=(hi-lo) or 1.0; a=(sampled[k]-lo)/span; b=(reference[k]-lo)/span; (a-b)**2.
       Rückgabe Mittelwert; 0.0 falls keine gemeinsamen Keys."""
```

### Nicht-Ziele
- **Keine** Änderung an `spaces.py` (Sampling-Verhalten bleibt bit-identisch).
- Kein Einsatz in `reward.py` (das ist A4.3).

### Tests (TDD — End-to-End über die öffentliche API)
```python
import pytest
from automation.optimizer import bounds

def test_bounds_sma_exact():
    b = bounds.extract_numeric_bounds("SmaCrossoverStrategy")
    assert b["sma_period"] == (5, 60)
    assert b["cooldown_bars"] == (2, 36)

def test_bounds_combo_excludes_categorical_and_derived():
    b = bounds.extract_numeric_bounds("ComboTrendVwapStrategy")
    assert b["sma_period"] == (20, 100)
    assert b["bb_std_dev"] == (1.0, 2.5)
    assert "require_vwap_confirmation" not in b   # kategorial
    assert "macd_slow" not in b                   # abgeleitet (fast+gap)

def test_bounds_unknown_raises():
    with pytest.raises(ValueError):
        bounds.extract_numeric_bounds("DoesNotExist")

def test_distance_zero_when_equal():
    b = {"x": (0.0, 10.0)}
    assert bounds.normalized_param_distance({"x": 5}, {"x": 5}, b) == 0.0

def test_distance_normalized():
    b = {"x": (0.0, 10.0), "y": (0.0, 4.0)}
    # x: (8-2)/10 = 0.6 → 0.36 ; y: (1-3)/4 = -0.5 → 0.25 ; mean = 0.305
    d = bounds.normalized_param_distance({"x": 8, "y": 1}, {"x": 2, "y": 3}, b)
    assert d == pytest.approx(0.305, rel=1e-9)
```
- **Operator-Smoke:** keiner nötig (reine Funktion, kein Backtest).
- CI: in **Tier 10** einhängen; Gate grün.

### Definition of Done (für GitHub)
- [ ] `bounds.py` + Tests erstellt, alle Tests grün
- [ ] `spaces.py` unverändert (`git diff` leer für diese Datei)
- [ ] Tier 10 eingehängt, Gate grün
- [ ] AGENTS.md Kap. 2 (`bounds.py` im Optimizer-Baum) + Kap. 19 (Changelog) aktualisiert

---

## A4.1 — `instrument_overrides`-Schema + Resolution

**Meta:** Prio P1 · Depends: — · CI: Tier 10 · Prod-Pfad: nein (nur reine Funktionen)

### Kontext & Ziel
Datenmodell für symbol-spezifische Parameter. `strategies.json` erhält ein optionales Feld `instrument_overrides`. Die Auflösung erfolgt **erweiternd** auf den bestehenden Resolvern — kein neuer paralleler Mechanismus.

> **Koordination Ansatz 3:** Falls Ansatz 3 (`§3.x`) zeitgleich `resolve.py` anfasst, ist die `instrument`-Erweiterung hier additiv und sollte konfliktfrei mergen; andernfalls Merge-Reihenfolge mit dem Operator abstimmen.

### Vorbedingungen (Recon)
- `automation/optimizer/resolve.py` (vorhanden) lesen.
- `automation/backtest_runner.py`: Funktion `resolve_strategy_params(strategy_entry, defaults, *, is_manifest)` lokalisieren (laut `claude.md` Anhang A vorhanden). **Existenz/Signatur verifizieren**; bei Abweichung Finding statt Annahme.
- `automation/config/strategies.json`: Struktur + evtl. vorhandenen `_schema`-Block prüfen.

### Zu liefernde Artefakte
- Geändert: `automation/optimizer/resolve.py`
- Geändert: `automation/backtest_runner.py` (nur Signatur-Erweiterung der reinen Funktion)
- Geändert: `automation/config/strategies.json` (`_schema`-Doku des optionalen Feldes; **keine** echten Overrides eintragen)
- Neu: `automation/tests/test_resolve_instrument_override.py`
- Geändert: `.github/workflows/pytest-gate.yml` (Tier 10)
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/optimizer/resolve.py — Such-Basis (Optimizer)
def resolve_params(strategy_class: str, sampled: dict, base_cfg: Path,
                   *, instrument: str | None = None) -> dict:
    """Reihenfolge: strategy_defaults.json < strategies.json[params]
       < instrument_overrides[instrument] (nur falls instrument!=None) < sampled (höchste Prio).
       instrument=None ⇒ exakt bisheriges Verhalten (rückwärtskompatibel)."""

# automation/backtest_runner.py — Legacy/Matrix-/Live-Pfad
def resolve_strategy_params(strategy_entry: dict, defaults: dict, *,
                            is_manifest: bool, instrument: str | None = None) -> dict:
    """is_manifest=True  ⇒ params verbatim (KEIN Merge, KEIN Override) — Pitfall #61 bleibt strikt.
       is_manifest=False ⇒ {**defaults, **params, **instrument_overrides.get(instrument, {})}
                           wenn instrument!=None, sonst {**defaults, **params} (unverändert)."""
```
**Schema-Doku in `strategies.json._schema`:** Feld `instrument_overrides` als optionales `{ "<SYMBOL.ETORO>": { "<param>": <value> } }` dokumentieren; pro Symbol nur die zu überschreibenden Keys.

### Nicht-Ziele
- **Kein** Aufruf der `instrument`-Auflösung an realen Call-Sites (das ist A4.8). Hier nur die **reinen Funktionen** + Tests.
- `is_manifest=True`-Semantik bleibt unverändert: **niemals** Override im Manifest-Pfad.

### Tests (TDD)
```python
from automation.optimizer.resolve import resolve_params
from automation.backtest_runner import resolve_strategy_params

def test_optimizer_override_precedence(tmp_path):
    (tmp_path/"strategy_defaults.json").write_text('{"VwapExhaustionStrategy":{"vwap_period":24,"cooldown_bars":3}}',"utf-8")
    (tmp_path/"strategies.json").write_text(
      '{"strategies":[{"strategy_class":"VwapExhaustionStrategy","params":{"vwap_period":20},'
      '"instrument_overrides":{"TSLA.ETORO":{"vwap_period":32,"cooldown_bars":5}}}]}',"utf-8")
    out = resolve_params("VwapExhaustionStrategy", {}, tmp_path, instrument="TSLA.ETORO")
    assert out["vwap_period"] == 32      # override schlägt params/defaults
    assert out["cooldown_bars"] == 5
    # sampled bleibt höchste Prio:
    out2 = resolve_params("VwapExhaustionStrategy", {"vwap_period": 99}, tmp_path, instrument="TSLA.ETORO")
    assert out2["vwap_period"] == 99

def test_optimizer_no_instrument_is_legacy(tmp_path):
    (tmp_path/"strategy_defaults.json").write_text('{"X":{"a":1}}',"utf-8")
    (tmp_path/"strategies.json").write_text('{"strategies":[{"strategy_class":"X","params":{"a":2},"instrument_overrides":{"S":{"a":3}}}]}',"utf-8")
    assert resolve_params("X", {}, tmp_path)["a"] == 2   # ohne instrument: kein Override

def test_runner_manifest_never_overrides():
    e = {"params": {"a": 1}, "instrument_overrides": {"S": {"a": 9}}}
    assert resolve_strategy_params(e, {"a": 0}, is_manifest=True, instrument="S") == {"a": 1}

def test_runner_legacy_applies_override():
    e = {"params": {"a": 1}, "instrument_overrides": {"S": {"a": 9}}}
    assert resolve_strategy_params(e, {"b": 5}, is_manifest=False, instrument="S") == {"a": 9, "b": 5}
    assert resolve_strategy_params(e, {"b": 5}, is_manifest=False) == {"a": 1, "b": 5}  # kein Override ohne instrument
```
- **Operator-Smoke:** keiner nötig.
- CI: Tier 10; Gate grün.

### Definition of Done
- [ ] Beide Resolver erweitert, `instrument=None`-Pfad bit-identisch (Regressionstest grün)
- [ ] `strategies.json._schema` dokumentiert, **keine** realen Overrides committet
- [ ] AGENTS.md Kap. 7 (Schema `instrument_overrides`) + Resolutionsordnung dokumentiert; Kap. 19 Changelog
- [ ] HI-1 verifiziert (`daily_orchestrator.py` unverändert)

---

## A4.2 — `global_settings.instruments`-Filter (Single-Symbol-Restriktion)

**Meta:** Prio P1 · Depends: A4.1 · CI: Tier 3/10 · Prod-Pfad: nein (nur reine Funktion + Manifest-Feld)

### Kontext & Ziel
Ein Single-Symbol-Backtest erfordert, die Matrix auf ein Symbol zu begrenzen — manifest-getrieben (reproduzierbar, konform zur Manifest-Autorität). `build_trial` schreibt optional `global_settings.instruments`; `backtest_runner.py` filtert das Universum reproduzierbar darauf.

### Vorbedingungen (Recon) — PFLICHT
- `automation/optimizer/trial_config.py` (vorhanden): `build_trial`-Signatur + `global_settings`-Aufbau.
- `automation/backtest_runner.py`: **Universum-Lade-Stelle** lokalisieren (laut Audit/Konzept der Punkt, an dem die handelbare Symbol-Liste entsteht). Falls keine reine Funktion existiert, **eine anlegen** (`restrict_universe`) und am Seam aufrufen.

### Zu liefernde Artefakte
- Geändert: `automation/optimizer/trial_config.py` (neuer optionaler Param `instruments`)
- Geändert: `automation/backtest_runner.py` (reine Funktion `restrict_universe` + Aufruf am Seam)
- Neu: `automation/tests/test_runner_instrument_filter.py`, `automation/tests/test_trial_instruments_manifest.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/optimizer/trial_config.py
def build_trial(strategy_class, sampled, *, study_name, trial_number, seed,
                now=None, holdout_days=None, n_folds=None,
                oos_window_days_override=None, base_cfg=None,
                instruments: list[str] | None = None) -> tuple[Path, Path]:
    """instruments!=None ⇒ schreibt global_settings["instruments"] = list(instruments).
       instruments=None  ⇒ Schlüssel wird NICHT geschrieben (rückwärtskompatibel, volles Universum)."""

# automation/backtest_runner.py
def restrict_universe(universe: list[str], instruments: list[str] | None) -> list[str]:
    """instruments falsy ⇒ universe unverändert (Reihenfolge erhalten).
       sonst ⇒ [s for s in universe if s in set(instruments)] (Schnittmenge, Reihenfolge von universe)."""
```
Am Universum-Seam: `universe = restrict_universe(universe, global_settings.get("instruments"))`.

### Nicht-Ziele
- Kein `--instrument`-CLI-Flag (Manifest-Feld ist vorzuziehen — kein verstecktes CLI-Verhalten).
- Keine Änderung der Matrix-Bildung außer der Filterung.

### Tests (TDD)
```python
from automation.backtest_runner import restrict_universe

def test_restrict_none_is_identity():
    u = ["A.ETORO","B.ETORO","C.ETORO"]
    assert restrict_universe(u, None) == u
    assert restrict_universe(u, []) == u

def test_restrict_intersection_preserves_order():
    u = ["A.ETORO","B.ETORO","C.ETORO"]
    assert restrict_universe(u, ["C.ETORO","A.ETORO"]) == ["A.ETORO","C.ETORO"]

def test_restrict_unknown_symbol_dropped():
    assert restrict_universe(["A.ETORO"], ["Z.ETORO"]) == []
```
```python
import json, datetime as dt
from pathlib import Path
from automation.optimizer import trial_config
def test_build_trial_writes_instruments(tmp_path):
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc)
    _, mp = trial_config.build_trial("SmaCrossoverStrategy", {}, study_name="s",
        trial_number=0, seed=42, now=now, holdout_days=45, n_folds=4,
        instruments=["TSLA.ETORO"])
    gs = json.loads(Path(mp).read_text("utf-8"))["global_settings"]
    assert gs["instruments"] == ["TSLA.ETORO"]
def test_build_trial_without_instruments_omits_key(tmp_path):
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc)
    _, mp = trial_config.build_trial("SmaCrossoverStrategy", {}, study_name="s",
        trial_number=1, seed=42, now=now, holdout_days=45, n_folds=4)
    assert "instruments" not in json.loads(Path(mp).read_text("utf-8"))["global_settings"]
```
- **Operator-Smoke (PFLICHT):** Einmal `backtest_runner.py` manuell mit einem Manifest mit `instruments=["<echtes Symbol>"]` ausführen → genau **ein** Symbol im Tournament. (Außerhalb CI.)
- CI: `restrict_universe`-Test in **Tier 3** (nah am Runner), Manifest-Test in **Tier 10**; Gate grün.

### Definition of Done
- [ ] Filter + Manifest-Feld implementiert; `instruments=None` bit-identisch (Regressionstest)
- [ ] Operator-Smoke dokumentiert (ein Symbol)
- [ ] AGENTS.md Kap. 7 (`global_settings.instruments`) + Kap. 19 Changelog
- [ ] HI-1 verifiziert

---

## A4.3 — Per-Symbol-Reward (`reward_mode`, `param_pen`, Coverage-Drop)

**Meta:** Prio P1 · Depends: A4.0, A4.1 · CI: Tier 10 · Prod-Pfad: nein

### Kontext & Ziel
Bei `universe_size == 1` degeneriert der Coverage-Term (`win_count/universe_size`). Neuer Reward-Pfad: Coverage entfällt, **Shrinkage-Strafe** `param_pen` Richtung globalem Optimum kommt hinzu (Gate 2 im Reward). Die Floor-/Ordnungsinvariante (nicht-evaluierbare Trials nie als Sieger) bleibt strikt.

### Vorbedingungen (Recon)
- `automation/optimizer/reward.py` (vorhanden) vollständig lesen — insb. den `floor`-Clamp und die Unevaluable-Shaping-Logik.
- `automation/config/optimizer.json`: vorhandene Keys prüfen (`penalty_*`, `sortino_clip_abs`, `unevaluable_shaping_span`, `evaluable_floor_epsilon`).

### Zu liefernde Artefakte
- Geändert: `automation/optimizer/reward.py`
- Geändert: `automation/config/optimizer.json` (neue Keys + `_schema`)
- Neu: `automation/tests/test_reward_per_symbol.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/optimizer/reward.py
def compute_reward(m: "TournamentMetrics", universe_size: int,
                   weights: dict | None = None, risk_dd_cap: float | None = None,
                   *, sampled: dict | None = None, global_params: dict | None = None,
                   strategy: str | None = None) -> float:
    """universe_size > 1  ⇒ exakt bisheriges Verhalten (Coverage-Pfad, rückwärtskompatibel).
       universe_size == 1 (oder weights['reward_mode']=='per_symbol') ⇒ Per-Symbol-Pfad:
         - Unevaluable-Pfad identisch (penalty_unevaluable_oos + unevaluable_shaping_span*trade_progress).
         - base = clip(oos_sortino, ±sortino_clip_abs)
         - overfit_gap = max(0, is_sortino_median - base)
         - dd_excess   = max(0, oos_max_drawdown - risk_dd_cap)
         - param_pen   = lambda_reg * normalized_param_distance(sampled, global_params,
                                       bounds.extract_numeric_bounds(strategy))
                         falls (sampled and global_params and strategy) sonst 0.0
         - reward = base - overfit_gap*penalty_overfit_weight - dd_excess*penalty_dd_weight - param_pen
         - KEIN coverage-Term
         - floor = penalty_unevaluable_oos + unevaluable_shaping_span + evaluable_floor_epsilon
         - return max(reward, floor)   # Ordnungsinvariante: evaluable >= floor > unevaluable"""
```
**Neue `optimizer.json`-Keys:** `"lambda_reg": <float, z.B. 0.25>`, `"promotion_margin": <float, z.B. 0.10>` (Letzteres wird erst in A4.5b genutzt, hier mitanlegen), optional `"reward_mode": "auto"`.

### Nicht-Ziele
- Keine Änderung am Coverage-Pfad (`universe_size>1`).
- Kein Backtest-Aufruf; `param_pen` rein arithmetisch.

### Tests (TDD)
```python
import json, statistics
from pathlib import Path
from automation.optimizer import parsing, reward, bounds

def _tournament(tmp_path, **agg):
    p = tmp_path/"t.json"
    p.write_text(json.dumps({"fully_eligible_pairs":1,"aggregate_winner":agg}),"utf-8"); return p

def test_per_symbol_drops_coverage_and_applies_param_pen(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    cap = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))["max_drawdown"]
    p = _tournament(tmp_path, oos_evaluated=True, oos_eligible=True, win_count=1,
                    median_is_sortino=2.0, oos_fold_sortinos=[1.0],
                    oos_metrics={"sortino_ratio":1.0,"max_drawdown":cap})
    m = parsing.parse_tournament(p)
    sampled = {"sma_period": 60, "cooldown_bars": 36}
    glob    = {"sma_period": 20, "cooldown_bars": 12}
    b = bounds.extract_numeric_bounds("SmaCrossoverStrategy")
    base = max(-cfg["sortino_clip_abs"], min(cfg["sortino_clip_abs"], 1.0))
    pen  = cfg["lambda_reg"] * bounds.normalized_param_distance(sampled, glob, b)
    expected = base - max(0.0, 2.0-base)*cfg["penalty_overfit_weight"] - 0.0*cfg["penalty_dd_weight"] - pen
    got = reward.compute_reward(m, universe_size=1, sampled=sampled, global_params=glob,
                                strategy="SmaCrossoverStrategy")
    assert got == __import__("pytest").approx(max(expected, cfg["penalty_unevaluable_oos"]
                 + cfg["unevaluable_shaping_span"] + cfg["evaluable_floor_epsilon"]), rel=1e-9)

def test_per_symbol_no_param_pen_when_missing_inputs(tmp_path):
    p = _tournament(tmp_path, oos_evaluated=True, oos_eligible=True, win_count=1,
                    median_is_sortino=1.0, oos_fold_sortinos=[1.0],
                    oos_metrics={"sortino_ratio":1.0,"max_drawdown":0.0})
    m = parsing.parse_tournament(p)
    # ohne sampled/global → param_pen=0; Reward identisch zu base (minus 0)
    r = reward.compute_reward(m, universe_size=1)
    assert r > 0

def test_per_symbol_unevaluable_matches_global(tmp_path):
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    p = _tournament(tmp_path, oos_evaluated=False, win_count=0)
    m = parsing.parse_tournament(p)
    assert reward.compute_reward(m, universe_size=1) == reward.compute_reward(m, universe_size=70)

def test_universe_gt_1_is_unchanged_coverage_path(tmp_path):
    # Regression: Coverage-Pfad bleibt exakt erhalten
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    p = _tournament(tmp_path, oos_evaluated=True, oos_eligible=True, win_count=5,
                    median_is_sortino=1.0, oos_fold_sortinos=[1.0],
                    oos_metrics={"sortino_ratio":1.0,"max_drawdown":0.0})
    m = parsing.parse_tournament(p)
    r = reward.compute_reward(m, universe_size=100)
    assert r >= 1.0/100 * cfg["bonus_coverage_weight"]   # Coverage trägt bei
```
> **Kalibrierungs-Hinweis (in AGENTS.md vermerken):** Sehr große `param_pen` kollabieren auf den Floor (TPE unterscheidet geflorte Trials nicht); nahe dem Optimum bleibt der Gradient erhalten. `lambda_reg` konservativ wählen.

- **Operator-Smoke:** keiner nötig.
- CI: Tier 10; Gate grün.

### Definition of Done
- [ ] Per-Symbol-Pfad implementiert, Coverage-Pfad bit-identisch (Regressionstest)
- [ ] `optimizer.json` Keys + `_schema`; Tests lesen Werte aus JSON (HI-6)
- [ ] AGENTS.md Kap. 7 (neue Keys, Kalibrierungs-Hinweis) + Kap. 19 Changelog

---

## A4.4 — Gate 1: Daten-Suffizienz (`is_symbol_tunable`)

**Meta:** Prio P1 · Depends: — · CI: Tier 10 · Prod-Pfad: nein

### Kontext & Ziel
Die wichtigste strukturelle Bremse gegen „Chartbild-Auswendiglernen": Ein (Strategie, Symbol)-Study startet **nur**, wenn die Historie das gesamte Fenster (IS + Folds·OOS + Holdout + Puffer) abdeckt **und** eine Parameter-zu-Daten-Heuristik erfüllt ist. Reine Funktion, keine Seiteneffekte.

### Vorbedingungen (Recon)
- `automation/historical_fetcher.py`: nach `is_symbol_data_sufficient(min_bars)` und `inception_bounds.json`-Nutzung suchen (laut Konzept/Audit vorhanden). Falls vorhanden, **wiederverwenden** (per Injektion), nicht neu bauen.
- `automation/config/backtest.json` `walk_forward` + `optimizer.json` für Schwellen.

### Zu liefernde Artefakte
- Neu: `automation/optimizer/gate.py`
- Geändert: `automation/config/optimizer.json` (Schwellen + `_schema`)
- Neu: `automation/tests/test_symbol_tunable_gate.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/optimizer/gate.py
def required_bars(*, is_window_days, oos_window_days, splits, holdout_days,
                  buffer_days, bars_per_day=24) -> int:
    """Mindest-Bar-Zahl für das gesamte Fenster (1h-Bars): (is + splits*oos + holdout + buffer)*bars_per_day."""

def is_symbol_tunable(symbol: str, n_params: int, *, available_bars: int,
                      config: dict) -> tuple[bool, str]:
    """True nur wenn:
         (a) available_bars >= required_bars(... aus config['walk_forward'] + config['gate1_buffer_days'])
         (b) available_bars / max(1, n_params) >= config['min_bars_per_param']
         (c) (oos_window_days*bars_per_day) >= config['min_oos_bars_per_fold']
       Rückgabe (ok, reason). reason ∈ {'OK','INSUFFICIENT_HISTORY','PARAM_DATA_RATIO_TOO_LOW',
                                        'OOS_FOLD_TOO_SHORT'}.
       available_bars wird vom Aufrufer geliefert (Injektion); KEIN I/O in dieser Funktion."""
```
**Neue `optimizer.json`-Keys:** `"gate1_buffer_days": <int, z.B. 30>`, `"min_bars_per_param": <int, z.B. 200>`, `"min_oos_bars_per_fold": <int, z.B. 500>`.

### Nicht-Ziele
- Kein direkter Datei-/Katalog-Zugriff in `is_symbol_tunable` (Testbarkeit). Die Bar-Zählung liefert der Sweep (A4.6) per Adapter über `historical_fetcher`.

### Tests (TDD)
```python
import json
from pathlib import Path
from automation.optimizer import gate

def _cfg():
    bt = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    opt = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    return {"walk_forward": bt["walk_forward"], **{k: opt[k] for k in
            ("gate1_buffer_days","min_bars_per_param","min_oos_bars_per_fold")}}

def test_sufficient_passes():
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=6, available_bars=10_000, config=_cfg())
    assert ok and why == "OK"

def test_insufficient_history():
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=6, available_bars=100, config=_cfg())
    assert not ok and why == "INSUFFICIENT_HISTORY"

def test_param_data_ratio():
    cfg = _cfg(); cfg["min_bars_per_param"] = 100_000  # erzwinge Ratio-Fail
    ok, why = gate.is_symbol_tunable("A.ETORO", n_params=8, available_bars=10_000, config=cfg)
    assert not ok and why == "PARAM_DATA_RATIO_TOO_LOW"
```
- **Operator-Smoke:** keiner nötig.
- CI: Tier 10; Gate grün.

### Definition of Done
- [ ] `gate.py` rein & I/O-frei; Schwellen aus JSON (HI-6)
- [ ] Wenn `historical_fetcher.is_symbol_data_sufficient` existiert: Wiederverwendung dokumentiert
- [ ] AGENTS.md Kap. 7 (Gate-1-Schwellen) + Kap. 19 Changelog

---

## A4.5a — `optimize_symbol` (Single-Symbol-Study + Warm-Start = Gate 2)

**Meta:** Prio P1 · Depends: A4.1, A4.2, A4.3 · CI: Tier 10 · Prod-Pfad: nein

### Kontext & Ziel
Single-Symbol-Variante von `optimize`: eigene Study `study_{strategy}_{symbol}`, Manifest mit `instruments=[symbol]`, `universe_size=1`, Per-Symbol-Reward, **Warm-Start am globalen Optimum** (Gate 2: `study.enqueue_trial(global_best)`). Jede Study eine eigene SQLite-Datei (EP-2: keine Lock-Contention).

### Vorbedingungen (Recon)
- `automation/optimizer/run_optimization.py` (vorhanden): `make_objective`, `optimize`, `STORAGE`, Universe-Read.
- `automation/optimizer/confirm.py` (vorhanden): `export_proposal`-Muster (Quelle globaler Best-Params).
- Quelle des globalen Optimums festlegen: `proposal_{strategy}.json` (`proposed_params_override`) falls vorhanden, sonst `strategies.json[strategy].params`.

### Zu liefernde Artefakte
- Geändert: `automation/optimizer/run_optimization.py`
- Neu: `automation/tests/test_optimize_symbol.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/optimizer/run_optimization.py
def _sanitize(symbol: str) -> str:
    """'TSLA.ETORO' → 'TSLA_ETORO' (dateinamenstauglich)."""

def load_global_best(strategy: str, base_cfg: Path) -> dict:
    """proposal_{strategy}.json['proposed_params_override'] falls vorhanden & status READY_FOR_PR,
       sonst strategies.json[strategy].params, sonst {} (None-safe)."""

def make_symbol_objective(strategy: str, symbol: str, global_params: dict,
                          *, run_backtest=run_backtest, build_trial=build_trial):
    """Wie make_objective, aber: build_trial(instruments=[symbol]); compute_reward(...,
       universe_size=1, sampled=sampled, global_params=global_params, strategy=strategy)."""

def optimize_symbol(strategy: str, symbol: str, n_trials: int | None = None,
                    *, storage: str | None = None) -> "optuna.Study":
    """study_name=f'study_{strategy}_{_sanitize(symbol)}';
       storage default = f'sqlite:///{WORK}/sweep/study_{strategy}_{_sanitize(symbol)}.db' (Verzeichnis anlegen);
       TPESampler(multivariate=True, group=True, n_startup_trials=..., seed=...) aus optimizer.json;
       create_study(..., direction='maximize', load_if_exists=True);
       study.enqueue_trial(load_global_best(strategy, config_dir()))  # Warm-Start (Gate 2), falls nicht leer;
       study.set_user_attr('data_snapshot_sha256', catalog_fingerprint());
       study.optimize(make_symbol_objective(...), n_trials=n_trials, n_jobs=1)."""
```

### Nicht-Ziele
- **Kein** Holdout/Promotion (das ist A4.5b).
- **Kein** `n_jobs>1` innerhalb einer Study (Reproduzierbarkeit, Pitfall #68).
- Bestehendes `optimize`/`run` (global) unverändert.

### Tests (TDD — End-to-End mit gemocktem Backtest)
```python
import json
from pathlib import Path
from automation.optimizer import run_optimization as ro

def _fake_factory(captured):
    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        m = json.loads(Path(manifest_path).read_text("utf-8"))
        captured.append(m)  # Manifest für Assertions festhalten
        out = Path(trial_dir)/"tournament_result.json"
        out.write_text(json.dumps({"fully_eligible_pairs":1,"aggregate_winner":{
            "oos_evaluated":True,"oos_eligible":True,"win_count":1,
            "median_is_sortino":1.0,"oos_fold_sortinos":[1.2],
            "oos_metrics":{"sortino_ratio":1.2,"max_drawdown":0.05}}}),"utf-8")
        return out
    return _fake

def test_optimize_symbol_manifest_and_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)                      # Isolation
    cap = []
    monkeypatch.setattr(ro, "run_backtest", _fake_factory(cap))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=2)
    assert study.study_name == "study_SmaCrossoverStrategy_TSLA_ETORO"
    assert (tmp_path/"sweep"/"study_SmaCrossoverStrategy_TSLA_ETORO.db").exists()
    assert all(m["global_settings"]["instruments"] == ["TSLA.ETORO"] for m in cap)

def test_optimize_symbol_warm_start_enqueues_global(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    # globales Proposal als Fixture
    (tmp_path).mkdir(exist_ok=True, parents=True)
    monkeypatch.setattr(ro, "run_backtest", _fake_factory([]))
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {"sma_period": 33, "cooldown_bars": 7})
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=1)
    # erster (enqueued) Trial trägt die globalen Best-Params
    assert study.trials[0].params.get("sma_period") == 33
```
- **Operator-Smoke (PFLICHT):** Einmal `optimize_symbol("<Pilot-Strategie>", "<echtes Symbol>", n_trials=5)` **echt** laufen lassen → eigene `.db`, Manifest mit einem Symbol, plausibler Reward.
- CI: Tier 10; Gate grün.

### Definition of Done
- [ ] `optimize_symbol` + Warm-Start + per-Study-SQLite; globales `optimize` unverändert
- [ ] E2E-Test (gemockt) + Operator-Smoke dokumentiert
- [ ] AGENTS.md Kap. 2/12 (Single-Symbol-Study, Gate 2) + Kap. 19 Changelog
- [ ] HI-1 verifiziert

---

## A4.5b — Gate 3: Promotion-Marge gegen Global (`confirm_per_symbol_promotion`)

**Meta:** Prio P0 (Kernverteidigung) · Depends: A4.5a · CI: Tier 10 · Prod-Pfad: nein

### Kontext & Ziel
Das **entscheidende** Gate: Ein `instrument_overrides[symbol]` wird nur promotet, wenn der symbol-getunte Vektor das globale Baseline auf dem **ungesehenen Holdout** um `promotion_margin` schlägt **und** der symbol-getunte Lauf selbst das Holdout-Gate besteht. Kosten: zwei zusätzliche Holdout-Backtests pro Symbol (vernachlässigbar gegenüber dem Such-Budget).

> **Design-Entscheidung (verbindlich):** Der Vergleichs-Score `holdout_reward` ist die **rohe** risikoadjustierte Performance (Per-Symbol-Pfad **ohne** `param_pen`). Begründung: `param_pen` ist ein Such-Regularisierer, kein Performance-Maß; im fairen Edge-Test verzerrt er den Vergleich. Implementierung: `compute_reward(..., universe_size=1)` **ohne** `sampled`/`global_params` ⇒ `param_pen=0`.

### Vorbedingungen (Recon)
- `automation/optimizer/confirm.py` (vorhanden): `confirm_on_holdout` als Vorlage (holdout_days=0, n_folds=1, `oos_window_days_override`, Lesen von `risk_dd_cap`).
- `WORK`-Layout für Proposals (`proposal_{strategy}.json` heute → `proposal_{strategy}_{symbol}.json`).

### Zu liefernde Artefakte
- Geändert: `automation/optimizer/confirm.py`
- Neu: `automation/tests/test_per_symbol_promotion.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/optimizer/confirm.py
def _holdout_metrics_for_params(strategy, symbol, params, *, run_backtest, build_trial):
    """build_trial(strategy, sampled=params, instruments=[symbol], holdout_days=0, n_folds=1,
       oos_window_days_override=<holdout_days aus backtest.json>) → run_backtest → parse_tournament."""

def confirm_per_symbol_promotion(study, strategy: str, symbol: str, global_params: dict,
                                 *, run_backtest=run_backtest, build_trial=build_trial) -> dict:
    """1. symbol-getunte Best-Params: study.best_trial.user_attrs['sampled_params'].
       2. m_symbol = _holdout_metrics_for_params(..., symbol_params, ...)
          m_global = _holdout_metrics_for_params(..., global_params, ...)
       3. R_symbol = compute_reward(m_symbol, universe_size=1)   # ohne param_pen
          R_global = compute_reward(m_global, universe_size=1)
       4. holdout_passed = m_symbol.oos_evaluated and m_symbol.oos_eligible
                           and (m_symbol.oos_sortino or -9) > 0
                           and (m_symbol.oos_max_drawdown or 1) <= risk_dd_cap (aus tournament.json)
       5. promote = holdout_passed and (R_symbol > R_global + promotion_margin)  # margin aus optimizer.json
       Rückgabe: {'promote': bool, 'status': <s>, 'R_symbol', 'R_global', 'promotion_margin',
                  'holdout_passed', 'metrics_symbol', 'metrics_global', 'symbol_params'}
       status ∈ {'READY_FOR_PR' (promote),
                 'REJECTED_NO_EDGE_OVER_GLOBAL' (holdout_passed aber Marge nicht erreicht),
                 'REJECTED_ON_HOLDOUT' (symbol-Lauf besteht Holdout-Gate nicht)}."""

def export_symbol_proposal(study, strategy: str, symbol: str, promotion: dict) -> Path:
    """Schreibt data/optimizer/proposal_{strategy}_{symbol}.json:
       {strategy, symbol, status, reward (study.best_value),
        proposed_instrument_override (= symbol_params), R_symbol, R_global, promotion_margin,
        holdout: {symbol: metrics_symbol, global: metrics_global}}."""
```

### Nicht-Ziele
- **Kein** Schreiben in `strategies.json` (Promotion ausschließlich per menschlichem PR).
- Kein Eingriff in den Live-Pfad.

### Tests (TDD — End-to-End mit params-abhängigem Fake)
```python
import json
from pathlib import Path
from automation.optimizer import run_optimization as ro, confirm

def _factory_by_params(tuned_keyvals, sortino_tuned, sortino_global, dd=0.05):
    """Liefert hohen Sortino, wenn die Manifest-Params die getunten Werte tragen, sonst niedrigen."""
    def _fake(trial_dir: Path, manifest_path: Path) -> Path:
        params = json.loads(Path(manifest_path).read_text("utf-8"))["strategies"][0]["params"]
        is_tuned = all(params.get(k) == v for k, v in tuned_keyvals.items())
        s = sortino_tuned if is_tuned else sortino_global
        out = Path(trial_dir)/"tournament_result.json"
        out.write_text(json.dumps({"fully_eligible_pairs":1,"aggregate_winner":{
            "oos_evaluated":True,"oos_eligible":True,"win_count":1,
            "median_is_sortino":1.0,"oos_fold_sortinos":[s],
            "oos_metrics":{"sortino_ratio":s,"max_drawdown":dd}}}),"utf-8")
        return out
    return _fake

def _study(tmp_path, monkeypatch, tuned):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _factory_by_params(tuned, 2.0, 0.5))
    monkeypatch.setattr(ro, "load_global_best", lambda *a, **k: {})
    # erzwinge best_trial mit bekannten Params über FixedTrial-Enqueue oder n_trials klein:
    study = ro.optimize_symbol("SmaCrossoverStrategy", "AAA.ETORO", n_trials=3)
    return study

def test_promotion_pass(tmp_path, monkeypatch):
    tuned = {"sma_period": 60}
    study = _study(tmp_path, monkeypatch, tuned)
    monkeypatch.setattr(confirm, "run_backtest", _factory_by_params(tuned, 2.0, 0.5))
    res = confirm.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "AAA.ETORO",
                                               global_params={"sma_period": 20})
    # Symbol-getunt (2.0) schlägt Global (0.5) klar
    assert res["promote"] is True and res["status"] == "READY_FOR_PR"
    p = confirm.export_symbol_proposal(study, "SmaCrossoverStrategy", "AAA.ETORO", res)
    assert json.loads(Path(p).read_text("utf-8"))["status"] == "READY_FOR_PR"

def test_promotion_no_edge(tmp_path, monkeypatch):
    tuned = {"sma_period": 60}
    study = _study(tmp_path, monkeypatch, tuned)
    # symbol und global gleich gut → keine Marge
    monkeypatch.setattr(confirm, "run_backtest", _factory_by_params(tuned, 1.0, 1.0))
    res = confirm.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "AAA.ETORO",
                                               global_params={"sma_period": 20})
    assert res["promote"] is False and res["status"] == "REJECTED_NO_EDGE_OVER_GLOBAL"

def test_promotion_rejected_on_holdout(tmp_path, monkeypatch):
    tuned = {"sma_period": 60}
    study = _study(tmp_path, monkeypatch, tuned)
    monkeypatch.setattr(confirm, "run_backtest", _factory_by_params(tuned, -0.5, -0.9, dd=0.9))
    res = confirm.confirm_per_symbol_promotion(study, "SmaCrossoverStrategy", "AAA.ETORO",
                                               global_params={"sma_period": 20})
    assert res["status"] == "REJECTED_ON_HOLDOUT"
```
- **Operator-Smoke (PFLICHT):** Auf dem Pilot-Symbol echte Promotion-Bestätigung fahren; alle drei Status mindestens einmal in der Praxis sehen (ggf. mit künstlich gutem/schlechtem Symbol).
- CI: Tier 10; Gate grün.

### Definition of Done
- [ ] Gate 3 implementiert; `param_pen` aus Vergleich ausgeschlossen (dokumentiert)
- [ ] Alle drei Status durch Tests abgedeckt
- [ ] `export_symbol_proposal` schreibt `proposal_{strategy}_{symbol}.json`, schreibt **nicht** in `strategies.json`
- [ ] AGENTS.md Kap. 12 (Gate 3, Status-Werte) + Kap. 19 Changelog
- [ ] HI-3/HI-5 verifiziert

---

## A4.6 — `sweep.py` Meta-Orchestrator + CLI

**Meta:** Prio P1 · Depends: A4.4, A4.5a, A4.5b · CI: Tier 10 · Prod-Pfad: nein

### Kontext & Ziel
Enumeriert tunebare (Strategie, Symbol)-Paare, filtert via Gate 1, wählt das Tier (deployable | refine | all), dispatcht `optimize_symbol` + `confirm_per_symbol_promotion`, schreibt Proposals. Betritt **nie** Phase 5. Parallelität über getrennte Studies (je eigene SQLite-Datei), nicht innerhalb einer Study.

### Vorbedingungen (Recon)
- Symbol-Universum: `data/universe/momentum_ls.json` (EP-4 bestätigen).
- Tier-A-Quelle: zuletzt geschriebenes `tournament_result.json` → `per_symbol_winners[symbol].strategy` (EP-4 bestätigen).
- `historical_fetcher`: Bar-Zählung pro Symbol für Gate 1 (Adapter; gemockt im Test).

### Zu liefernde Artefakte
- Neu: `automation/optimizer/sweep.py`
- Neu: `automation/tests/test_sweep_enumeration.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/optimizer/sweep.py
def load_symbol_universe(base_cfg: Path | None = None) -> list[str]: ...
def load_tier_a_winners(tournament_path: Path | None = None) -> dict[str, list[str]]:
    """{strategy: [symbols, die unter globalen Params Tournament-Gewinner sind]}."""

def n_params_for(strategy: str) -> int:
    """Anzahl numerischer Suchraum-Parameter via bounds.extract_numeric_bounds(strategy)."""

def enumerate_tunable_pairs(strategies: list[str], symbols: list[str] | None,
                            *, tier: str, available_bars: dict[str, int],
                            config: dict) -> list[tuple[str, str, str]]:
    """1. Symbol-Liste = symbols or load_symbol_universe().
       2. Tier-Auswahl:
          - 'deployable': nur (strategy, symbol) mit symbol in load_tier_a_winners()[strategy]
          - 'refine'    : Symbole, die knapp scheitern (Operator-Liste / Heuristik) — Platzhalter, P3-Ausbau
          - 'all'       : Kreuzprodukt strategies × Symbole
       3. Gate 1: is_symbol_tunable(symbol, n_params_for(strategy),
                  available_bars=available_bars[symbol], config=config) muss True sein.
       Rückgabe Liste (strategy, symbol, reason='OK'); ausgeschlossene Paare NICHT enthalten."""

def run_per_symbol_sweep(strategies: list[str], symbols: list[str] | None = None,
                         *, tier: str = "deployable", n_jobs: int = 1,
                         optimize_symbol=None, confirm=None) -> list[Path]:
    """Für jedes enumerierte Paar: optimize_symbol(strategy, symbol) → confirm_per_symbol_promotion
       → export_symbol_proposal. n_jobs steuert parallele Studies (je eigene SQLite-Datei).
       Betritt NIE Phase 5. Gibt Liste der Proposal-Pfade zurück.
       optimize_symbol/confirm injizierbar (Default: echte Implementierungen) — Testbarkeit."""

# CLI
# python -m automation.optimizer.sweep --strategies all --symbols all --tier deployable --n-jobs 4
```

### Nicht-Ziele
- **Kein** echter Backtest im Test (`optimize_symbol`/`confirm` injizieren/mocken).
- **Kein** Schreiben in `strategies.json`, **kein** Phase-5-Aufruf, **kein** `subprocess.Popen`.
- `tier='refine'` nur als Platzhalter-Pfad (eigentliche Refinement-Heuristik = späterer P3-Ausbau).

### Tests (TDD — Enumeration & Dispatch, vollständig gemockt)
```python
from automation.optimizer import sweep

def test_enumeration_deployable_and_gate1(monkeypatch):
    monkeypatch.setattr(sweep, "load_symbol_universe", lambda *a, **k: ["A.ETORO","B.ETORO","C.ETORO"])
    monkeypatch.setattr(sweep, "load_tier_a_winners", lambda *a, **k: {"SmaCrossoverStrategy": ["A.ETORO","C.ETORO"]})
    monkeypatch.setattr(sweep, "n_params_for", lambda s: 2)
    cfg = {"walk_forward": {"is_window_days":120,"oos_window_days":30,"splits":4,"holdout_days":45},
           "gate1_buffer_days":30,"min_bars_per_param":200,"min_oos_bars_per_fold":500}
    bars = {"A.ETORO": 10_000, "B.ETORO": 10_000, "C.ETORO": 50}  # C fällt durch Gate 1
    pairs = sweep.enumerate_tunable_pairs(["SmaCrossoverStrategy"], None,
              tier="deployable", available_bars=bars, config=cfg)
    assert pairs == [("SmaCrossoverStrategy","A.ETORO","OK")]   # B nicht deployable, C Gate-1-Fail

def test_sweep_dispatch_writes_proposals_no_backtest(monkeypatch, tmp_path):
    calls = []
    def fake_opt(strategy, symbol, **k): calls.append((strategy, symbol)); return object()
    def fake_confirm(study, strategy, symbol, global_params, **k):
        return {"promote": True, "status": "READY_FOR_PR", "symbol_params": {}}
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs",
        lambda *a, **k: [("SmaCrossoverStrategy","A.ETORO","OK")])
    monkeypatch.setattr(sweep, "export_symbol_proposal",
        lambda study, s, sym, prom: tmp_path/f"proposal_{s}_{sym}.json")
    monkeypatch.setattr(sweep, "load_global_best", lambda *a, **k: {})
    out = sweep.run_per_symbol_sweep(["SmaCrossoverStrategy"], ["A.ETORO"],
            tier="deployable", optimize_symbol=fake_opt, confirm=fake_confirm)
    assert calls == [("SmaCrossoverStrategy","A.ETORO")]
    assert out == [tmp_path/"proposal_SmaCrossoverStrategy_A.ETORO.json"]

def test_sweep_never_calls_popen(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Phase 5 verboten")))
    monkeypatch.setattr(sweep, "enumerate_tunable_pairs", lambda *a, **k: [])
    assert sweep.run_per_symbol_sweep(["SmaCrossoverStrategy"], [], tier="deployable") == []
```
- **Operator-Smoke (PFLICHT):** `python -m automation.optimizer.sweep --strategies <Pilot> --tier deployable --n-jobs 2` gegen das echte Repo → Proposals erscheinen unter `data/optimizer/`, keine Live-Aktion, getrennte `.db`-Dateien.
- CI: Tier 10; Gate grün (Timing-Wächter — alles gemockt).

### Definition of Done
- [ ] Enumeration (Tier + Gate 1) + Dispatch implementiert, alle Tests grün
- [ ] CLI vorhanden (`--strategies/--symbols/--tier/--n-jobs`); `all`-Auflösung dokumentiert
- [ ] Kein Phase-5-/`strategies.json`-Schreibzugriff (Tests beweisen es)
- [ ] AGENTS.md Kap. 2 (`sweep.py`) + Kap. 12 (Sweep betritt nie Phase 5) + Kap. 19 Changelog
- [ ] HI-1/HI-3 verifiziert

---

## A4.7 — *(optional)* Konfigurierbare Storage-URL (SQLite-Default, Postgres-Opt-in)

**Meta:** Prio P2 · Depends: A4.6 · CI: Tier 10 · Prod-Pfad: nein · **Leitplanke-Aufweichung — Sign-off nötig (EP-2)**

### Kontext & Ziel
Für echte Multi-Maschinen-Parallelität (mehrere Hosts gegen **eine** Study) ist eine RDB nötig. Diese Erweiterung macht die Storage-URL konfigurierbar; **SQLite bleibt Default** und der einzige Pfad, den die Tests ausführen. Postgres ist rein Opt-in und auf den Sweep begrenzt.

> **Wichtig:** Dieses Issue weicht die bestehende „ausschließlich SQLite"-Leitplanke (G4d / Pitfall) **bewusst, dokumentiert und begrenzt** auf. Erst nach Operator-Freigabe umsetzen. Ohne dieses Issue funktioniert der MVP vollständig (per-Study-SQLite).

### Vorbedingungen (Recon)
- `automation/optimizer/run_optimization.py`: `STORAGE`-Konstante + `optimize_symbol`-Storage-Default.
- AGENTS.md G4d / Pitfall #53 / #68 (SQLite-Determinismus-Warnung) lokalisieren.

### Zu liefernde Artefakte
- Geändert: `automation/optimizer/run_optimization.py`
- Geändert: `automation/config/optimizer.json` (`storage_url`-Key + `_schema`)
- Neu: `automation/tests/test_storage_url_resolution.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md` (G4d präzisieren)

### Funktions-Contracts (exakt)
```python
def resolve_storage(*, study_name: str, base_cfg: Path | None = None) -> str:
    """Priorität: ENV ETORO_OPTUNA_STORAGE > optimizer.json['storage_url'] (falls nicht null)
       > f'sqlite:///{WORK}/sweep/{study_name}.db' (Default).
       Bei nicht-sqlite-URL: WARNUNG loggen, dass Determinismus pro Study nur bei n_jobs=1 garantiert ist."""
```
`optimize_symbol` ruft `resolve_storage(...)` statt fester SQLite-Konstruktion.

### Nicht-Ziele
- **Keine** echte Postgres-Verbindung in Tests (nur URL-Auflösung).
- Globales `optimize` (Einzel-Strategie) bleibt auf der bestehenden `studies.db` (SQLite).

### Tests (TDD)
```python
import json
from pathlib import Path
from automation.optimizer import run_optimization as ro

def test_default_is_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.delenv("ETORO_OPTUNA_STORAGE", raising=False)
    url = ro.resolve_storage(study_name="study_X_A_ETORO")
    assert url.startswith("sqlite:///") and "study_X_A_ETORO.db" in url

def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setenv("ETORO_OPTUNA_STORAGE", "postgresql://u:p@db/opt")
    assert ro.resolve_storage(study_name="s") == "postgresql://u:p@db/opt"
```
- **Operator-Smoke:** nur falls Postgres tatsächlich eingesetzt wird (separat).
- CI: Tier 10; Gate grün.

### Definition of Done
- [ ] **Operator-Freigabe für Leitplanke-Aufweichung eingeholt** (im PR verlinkt)
- [ ] SQLite-Default; ENV/Config-Opt-in; Tests prüfen nur URL-Auflösung
- [ ] AGENTS.md G4d präzisiert („SQLite für Single-Node; Postgres nur für explizite parallele Sweeps"); Pitfall #68 bleibt für SQLite gültig; Kap. 19 Changelog

---

## A4.8 — *(optional, `touches-prod`)* Live-Integration: Matrix-Wiring + Momentum-LS

**Meta:** Prio P2 · Depends: A4.1 · CI: Tier 3/10 · **Prod-Pfad: JA** · **EP-1 Sign-off nötig**

### Kontext & Ziel
Nach Promotion von Overrides wirken zwei Pfade: (1) der Daily-Orchestrator-**Matrix-Backtest** (`backtest_runner.py`, Legacy-Pfad) löst pro (symbol, strategy) via `instrument`-aware Resolver auf; (2) `momentum_ls_run.py` instanziiert den per-Symbol-Tournament-Gewinner mit override-aufgelösten Parametern. **`daily_orchestrator.py` bleibt unverändert** — das Wiring sitzt ausschließlich in `backtest_runner.py` und `momentum_ls_run.py`. Gate-Scope-vs-Deployment-Scope (Pitfall #60) bleibt: ein per-Symbol-OOS-Verlierer bleibt auch mit Override ausgeschlossen.

> **Erst sinnvoll, wenn gemergte `instrument_overrides` in `strategies.json` existieren.** Bis dahin deferrbar.

### Vorbedingungen (Recon) — PFLICHT
- `automation/backtest_runner.py`: die **Matrix-Job-Call-Site**, an der pro (symbol, strategy) die Strategie-Parameter aufgelöst werden (Legacy-Pfad ohne `manifest_version`). Dort `resolve_strategy_params(..., instrument=symbol)` (A4.1) verdrahten.
- `automation/momentum_ls_run.py`: Registrierungspunkt des Tournament-Gewinners; dort Override-Auflösung einfügen. `STRATEGY_REGISTRY`/Allocator-Hook (Pitfall #22) bleiben.

### Zu liefernde Artefakte
- Geändert: `automation/backtest_runner.py` (Matrix-Call-Site)
- Geändert: `automation/momentum_ls_run.py` (Live-Registrierung)
- Neu: `automation/tests/test_live_instrument_override.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
- **Matrix-Pfad:** an der Auflösungs-Call-Site `is_manifest=False`-Zweig um `instrument=symbol` ergänzen (reine Funktion aus A4.1). Ohne `instrument_overrides[symbol]` ⇒ identische Params wie heute (HI-2).
- **Momentum-LS:** beim Erzeugen der Gewinner-Config die override-aufgelösten Parameter verwenden (gleiche reine Auflösung). Ohne Override ⇒ identisch.

### Nicht-Ziele
- **Keine** Änderung an `daily_orchestrator.py` (HI-1).
- Keine Aufweichung von OOS-Gating, Whitelist-Erzeugung (Pitfall #60), Fail-Closed-Interlock.

### Tests (TDD — gemockt, kein echter Backtest/kein Live)
```python
# Matrix-Pfad: reine Auflösung an der Call-Site verifizieren (Funktion aus A4.1 wird mit instrument aufgerufen)
def test_matrix_uses_instrument_override(monkeypatch):
    # Strategie-Entry mit Override; prüfe, dass die an die Strategie übergebenen Params das Override tragen,
    # wenn instrument gesetzt ist, und NICHT, wenn kein Override existiert.
    ...
# Momentum-LS: Registrierung instanziiert Config mit Override-Params (Allocator/Registry gemockt)
def test_momentum_ls_registers_with_override(monkeypatch):
    ...
def test_no_override_is_identical_behavior(monkeypatch):
    # Regression: ohne instrument_overrides verhält sich beides bit-identisch zum Ist-Zustand
    ...
```
- **Operator-Smoke (PFLICHT, hohe Sorgfalt):** Mit **einem** gemergten Test-Override einmal `daily_orchestrator.py --no-deploy --skip-api-fetch` fahren → die betroffene Strategie läuft auf dem Symbol mit override-Params; OOS-Gating/Whitelist unverändert; **kein** Live-Deploy. Anschließend Override wieder entfernen, falls nur Test.
- CI: Matrix-Test **Tier 3**, Momentum-LS-Test **Tier 10**; Gate grün.

### Definition of Done
- [ ] **EP-1-Sign-off im PR verlinkt**
- [ ] Wiring an beiden Call-Sites; `daily_orchestrator.py` unverändert (`git diff` leer)
- [ ] Regressionstest „ohne Override → identisch" grün
- [ ] Operator-Smoke mit `--no-deploy` dokumentiert
- [ ] AGENTS.md Kap. 6/12 (Live-Auflösung Overrides, Gate-Scope-vs-Deployment bleibt) + Kap. 19 Changelog
- [ ] HI-1/HI-2/HI-3 verifiziert

---

## A4.9 — *(optional)* Overhead-Reduktion: In-Process-Backtest-Entry + Config-Sharing

**Meta:** Prio P3 · Depends: A4.5a · CI: Tier 10 · Prod-Pfad: nein

### Kontext & Ziel
Reine Performance: Der Subprozess-pro-Trial-Overhead dominiert den naiven Sweep (~93h vs. ~6h warm). Ein **importierbarer In-Process-Backtest-Entry** eliminiert Spawn-/Import-Kosten; **Config-Sharing pro Study** spart 42 000 → 420 Config-Kopien. Trade-off: Fault-Isolation sinkt von Trial- auf Study-Ebene — daher Trial-Exceptions → `optuna.TrialPruned`, fundamentale Fehler (ImportError) crashen die Study hart (Fail-Fast).

### Vorbedingungen (Recon) — PFLICHT
- `automation/backtest_runner.py`: prüfen, ob `run_single_backtest_worker`/eine Backtest-Funktion **importierbar und seiteneffektfrei** aufrufbar ist (heute CLI). Falls nicht, eine importierbare Entry-Funktion **anlegen**, ohne das CLI-Verhalten zu ändern.
- `automation/optimizer/trial_config.py`: Config-Kopie pro Trial (heute `shutil.copy2` aller JSONs) → auf **eingefrorene** `config/` pro (Strategie, Symbol)-Study umstellen; pro Trial unterscheidet sich nur das `experiment_manifest.json`.

### Zu liefernde Artefakte
- Geändert: `automation/backtest_runner.py` (importierbarer Entry, additiv)
- Geändert: `automation/optimizer/runner.py` (In-Process-Aufruf-Variante, gateweise aktivierbar)
- Geändert: `automation/optimizer/trial_config.py` (Config-Sharing pro Study)
- Neu: `automation/tests/test_inprocess_backtest_entry.py`
- Geändert: `.github/workflows/pytest-gate.yml`
- Geändert: `automation/AGENTS.md`

### Funktions-Contracts (exakt)
```python
# automation/backtest_runner.py
def run_backtest_inprocess(manifest_path: Path, output_path: Path) -> Path:
    """Seiteneffektfreier In-Process-Lauf (kein Subprozess). Schreibt tournament_result.json nach output_path.
       Wirft fachliche Fehler als Exceptions (vom Aufrufer in TrialPruned zu wandeln)."""

# automation/optimizer/runner.py
def run_backtest(trial_dir, manifest_path, *, mode: str = "subprocess") -> Path:
    """mode='subprocess' (Default, unverändert) | mode='inprocess' (run_backtest_inprocess).
       Trial-Exceptions im inprocess-Modus → optuna.TrialPruned; ImportError o.ä. → Fail-Fast (re-raise)."""
```
Config-Sharing: `build_trial` erhält einen Modus, der eine bereits eingefrorene Study-`config/` referenziert, statt pro Trial neu zu kopieren (rückwärtskompatibel: Default bleibt Kopie pro Trial).

### Nicht-Ziele
- **Keine** Verhaltensänderung der Backtest-Ergebnisse (nur Performance-/Isolations-Mechanik).
- Subprozess-Modus bleibt Default und voll funktionsfähig.

### Tests (TDD — Fault-Isolation gemockt, KEIN echter Backtest)
```python
import optuna, pytest
from automation.optimizer import runner

def test_inprocess_trial_exception_becomes_pruned(monkeypatch, tmp_path):
    def boom(manifest_path, output_path): raise RuntimeError("trial-level")
    monkeypatch.setattr(runner, "run_backtest_inprocess", boom, raising=False)
    with pytest.raises(optuna.TrialPruned):
        runner.run_backtest(tmp_path, tmp_path/"m.json", mode="inprocess")

def test_inprocess_import_error_fails_fast(monkeypatch, tmp_path):
    def boom(manifest_path, output_path): raise ImportError("fundamental")
    monkeypatch.setattr(runner, "run_backtest_inprocess", boom, raising=False)
    with pytest.raises(ImportError):
        runner.run_backtest(tmp_path, tmp_path/"m.json", mode="inprocess")

def test_subprocess_mode_unchanged(monkeypatch, tmp_path):
    # Regression: Default-Pfad ruft weiterhin subprocess.run
    ...
```
- **Operator-Smoke (PFLICHT):** Kalibrierungslauf (5 Trials) im `inprocess`-Modus vs. `subprocess`-Modus → **identische** Reward-Werte, deutlich kürzere Laufzeit. Ergebnis-Identität ist Abnahmekriterium.
- CI: Tier 10; Gate grün.

### Definition of Done
- [ ] In-Process-Entry additiv; Subprozess-Default unverändert (Regressionstest)
- [ ] Fault-Isolation: TrialPruned vs. Fail-Fast getestet
- [ ] Operator-Smoke: identische Rewards inprocess vs. subprocess dokumentiert
- [ ] AGENTS.md Kap. 12/16 (Fault-Isolation-Trade-off, Mitigation) + Kap. 19 Changelog

---

# 6. Anhang A — Namens-Contract (Ergänzungen)

Verbindlich, auftragsübergreifend identisch zu verwenden:

**`automation/optimizer/bounds.py`** — `extract_numeric_bounds(strategy)`, `normalized_param_distance(sampled, reference, bounds)`.
**`automation/optimizer/resolve.py`** — `resolve_params(strategy_class, sampled, base_cfg, *, instrument=None)`.
**`automation/backtest_runner.py`** — `resolve_strategy_params(strategy_entry, defaults, *, is_manifest, instrument=None)`, `restrict_universe(universe, instruments)`, *(A4.9)* `run_backtest_inprocess(manifest_path, output_path)`.
**`automation/optimizer/trial_config.py`** — `build_trial(..., instruments=None)`.
**`automation/optimizer/reward.py`** — `compute_reward(m, universe_size, weights=None, risk_dd_cap=None, *, sampled=None, global_params=None, strategy=None)`.
**`automation/optimizer/gate.py`** — `required_bars(...)`, `is_symbol_tunable(symbol, n_params, *, available_bars, config)`.
**`automation/optimizer/run_optimization.py`** — `_sanitize(symbol)`, `load_global_best(strategy, base_cfg)`, `make_symbol_objective(...)`, `optimize_symbol(strategy, symbol, n_trials=None, *, storage=None)`, *(A4.7)* `resolve_storage(*, study_name, base_cfg=None)`.
**`automation/optimizer/confirm.py`** — `confirm_per_symbol_promotion(study, strategy, symbol, global_params, *, run_backtest=..., build_trial=...)`, `export_symbol_proposal(study, strategy, symbol, promotion)`.
**`automation/optimizer/sweep.py`** — `load_symbol_universe`, `load_tier_a_winners`, `n_params_for`, `enumerate_tunable_pairs`, `run_per_symbol_sweep`.

**Status-Werte (Proposals):** `READY_FOR_PR`, `REJECTED_NO_EDGE_OVER_GLOBAL`, `REJECTED_ON_HOLDOUT`.
**Proposal-Datei:** `data/optimizer/proposal_{strategy}_{symbol}.json`.
**Study-Name:** `study_{strategy}_{_sanitize(symbol)}`; per-Study-SQLite: `{WORK}/sweep/study_{strategy}_{_sanitize(symbol)}.db`.

---

# 7. Anhang B — Neue Config-Keys (Zero-Hardcoding)

**`automation/config/optimizer.json`** (Defaults sind Startkalibrierung, vom Operator zu justieren):
```jsonc
{
  "lambda_reg": 0.25,             // A4.3 — Stärke der Shrinkage-Strafe (Gate 2)
  "promotion_margin": 0.10,       // A4.5b — Marge, die symbol-getunt das Global auf Holdout schlagen muss (Gate 3)
  "reward_mode": "auto",          // A4.3 — 'auto' (Per-Symbol bei universe_size==1) | 'per_symbol'
  "gate1_buffer_days": 30,        // A4.4 — Puffer über IS+Folds*OOS+Holdout
  "min_bars_per_param": 200,      // A4.4 — N_bars / N_params Mindestverhältnis
  "min_oos_bars_per_fold": 500,   // A4.4 — Mindest-OOS-Bars je Fold
  "storage_url": null             // A4.7 (optional) — null ⇒ SQLite-Default
}
```
Alle Keys im `_schema`-Block (Deutsch) dokumentieren. **Tests lesen diese Werte aus der JSON** (HI-6), niemals als Literal.

---

# 8. Anhang C — Test-Hygiene & „End-to-End"-Definition

- **„100% end-to-end getestet" heißt hier:** Jeder Issue-Test treibt sein Feature über die **öffentliche Entry-Funktion** und prüft das **beobachtbare Artefakt** (Manifest-Inhalt, Proposal-JSON, Resolutionsergebnis, Study-DB-Existenz, Enumerationsliste). Der einzige gemockte Baustein ist der **schwergewichtige Backtest-Subprozess** (Pflicht — ein echter Backtest in CI ist ein **P0**-Verstoß gegen HI-7).
- **Zusätzlich pro Issue (wo markiert): ein manueller Operator-Smoke** mit **einem echten** Lauf gegen 1 Symbol/Strategie — außerhalb CI. Das schließt die Lücke zwischen „gemockt-vollständig" und „real bestätigt".
- **Timing-Wächter:** Optimizer-Testdatei > wenige Sekunden ⇒ undichter Mock ⇒ sofort P0.
- **Determinismus:** Per-Symbol-Studies laufen `n_jobs=1`; Parallelität ausschließlich über getrennte Studies (je eigene SQLite-Datei). Damit bleibt jede Study reproduzierbar (`seed` fix, `data_snapshot_sha256` protokolliert).

---

*Kernbotschaft: Der Wert von Ansatz 4 liegt nicht im Per-Symbol-Tuning selbst — das ist trivial — sondern in den fünf Gates, die verhindern, dass ein einzelnes Symbol auswendig gelernt wird. Gate 1 (Daten-Suffizienz) und Gate 3 (Marge gegen Global auf unberührtem Holdout) sind die beiden härtesten Bremsen und gehören zu den am sorgfältigsten zu testenden Issues (A4.4, A4.5b).*
