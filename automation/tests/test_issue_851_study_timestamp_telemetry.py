"""Issue #851 (P2) — Fehlende Zeitstempel: zwei Abschnitte melden "nicht rekonstruierbar".

Root-Cause: der #742-Report führte keine Wallclock-Zeit je einzelner Study (nur die
Sweep-Gesamtzeit); Abschnitt 3.2 ("Laufzeit je Symbol/Strategie") und 3.4 ("Barriere-Wartezeit")
mussten das selbst eingestehen. Die Zahlen waren nur über eine Log-Zeitstempel-Rekonstruktion
zugänglich — eine Notlösung, die bei einem stilleren Lauf nicht funktioniert.

Fix:
1. ``run_optimization._optimize_symbol_impl`` persistiert ``study_started_at_utc``/
   ``study_ended_at_utc``/``study_wallclock_s``/``worker_id`` als Study-User-Attrs (auch bei
   vorzeitigem Abbruch, im ``finally:``-Block, #833-Stil).
2. ``report.py`` leitet daraus ``wallclock_by_strategy`` (Median/p90 je Strategie),
   ``symbol_barrier_wait_s`` (Symbol-Wallclock minus schnellste Study) und ``worker_utilisation``
   (Σ Study-Wallclock / (n_jobs × Sweep-Wallclock)) ab.
3. ``summary_de.py`` Abschnitte 3.2/3.4 nutzen die neuen Felder; die "nicht rekonstruierbar"-Texte
   entfallen.

SCOPE-ENTSCHEIDUNG (dokumentiert, nicht stillschweigend): Punkt 3 des Issue-Texts ("echte
Longest-Trades" mit Entry-/Exit-Zeitstempel + ``exit_reason`` je Einzeltrade) wird NICHT
umgesetzt — dieselbe Scope-Grenze wie bereits in #832 dokumentiert (siehe
``summary_de``-Moduldocstring/``report._longest_holding_studies``): das würde eine neue
Einzel-Trade-State-Verfolgung (Entry-Zeitstempel, Richtung, exit_reason je Position) in der
FIFO-Match-Schleife von ``backtest_runner.extract_metrics`` voraussetzen, der höchstriskantesten
P&L-Aggregationsstelle des Systems, ohne einen realen Marktdaten-Lauf zur Regressionsverifikation
in diesem Environment. Die STUDY-Ebene (``longest_holding_studies``) bleibt die sichere Alternative.

Akzeptanzkriterien:
- AK-1: Abschnitt 3.2 gibt Median-Wallclock je Strategie aus; Abschnitt 3.4 nennt die
  Barriere-Wartezeit je Symbol.
- AK-4: ``worker_utilisation`` wird berechnet.
"""
from pathlib import Path
import json

from automation.optimizer import run_optimization as ro
from automation.optimizer import trial_config
from automation.optimizer.report import (
    _wallclock_by_strategy, _symbol_barrier_wait, _worker_utilisation,
)
from automation.optimizer import summary_de


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "WORK", tmp_path)
    monkeypatch.setattr(trial_config, "WORK", tmp_path)
    monkeypatch.setattr(ro, "config_dir", lambda: Path("automation/config"))
    monkeypatch.setattr(trial_config, "config_dir", lambda: Path("automation/config"))


def _fake_factory(captured):
    def _fake(trial_dir: Path, manifest_path: Path, **_kwargs) -> Path:
        m = json.loads(Path(manifest_path).read_text("utf-8"))
        captured.append(m)
        out = Path(trial_dir) / "tournament_result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"fully_eligible_pairs": 1, "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True, "win_count": 1,
            "median_is_sortino": 1.0, "oos_fold_sortinos": [1.2],
            "oos_metrics": {"sortino_ratio": 1.2, "max_drawdown": 0.05}}}), "utf-8")
        return out
    return _fake


# ── AK-1: Study-Zeitstempel-Telemetrie (optimize_symbol) ────────────────────────────────────────
def test_optimize_symbol_persists_study_timestamps(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(ro, "run_backtest", _fake_factory([]))
    study = ro.optimize_symbol("SmaCrossoverStrategy", "TSLA.ETORO", n_trials=2)
    attrs = study.user_attrs
    assert attrs.get("study_started_at_utc") is not None
    assert attrs.get("study_ended_at_utc") is not None
    assert attrs["study_ended_at_utc"] >= attrs["study_started_at_utc"]
    assert isinstance(attrs.get("study_wallclock_s"), (int, float))
    assert attrs["study_wallclock_s"] >= 0
    assert attrs.get("worker_id") is not None


# ── report._wallclock_by_strategy (reine Funktion) ───────────────────────────────────────────────
def test_wallclock_by_strategy_computes_median_and_p90():
    studies = [
        {"strategy": "S1", "study_wallclock_s": 10.0},
        {"strategy": "S1", "study_wallclock_s": 20.0},
        {"strategy": "S1", "study_wallclock_s": 30.0},
        {"strategy": "S2", "study_wallclock_s": 100.0},
    ]
    out = _wallclock_by_strategy(studies)
    assert out["S1"]["median"] == 20.0
    assert out["S1"]["n"] == 3
    assert out["S2"]["median"] == 100.0
    assert out["S2"]["n"] == 1


def test_wallclock_by_strategy_ignores_missing_data():
    studies = [{"strategy": "S1", "study_wallclock_s": None}, {"strategy": None, "study_wallclock_s": 5.0}]
    assert _wallclock_by_strategy(studies) == {}


# ── report._symbol_barrier_wait (reine Funktion) ─────────────────────────────────────────────────
def test_symbol_barrier_wait_is_max_minus_min_per_symbol():
    studies = [
        {"symbol": "A.ETORO", "study_wallclock_s": 10.0},
        {"symbol": "A.ETORO", "study_wallclock_s": 40.0},
        {"symbol": "B.ETORO", "study_wallclock_s": 15.0},  # nur 1 Study -> kein Warten messbar
    ]
    out = _symbol_barrier_wait(studies)
    assert out == {"A.ETORO": 30.0}


def test_symbol_barrier_wait_empty_without_multi_study_symbols():
    studies = [{"symbol": "A.ETORO", "study_wallclock_s": 10.0}]
    assert _symbol_barrier_wait(studies) == {}


# ── report._worker_utilisation (reine Funktion) ──────────────────────────────────────────────────
def test_worker_utilisation_serial_reference_run_near_one():
    """Ein serieller Referenzlauf (n_jobs=1): Summe der Study-Wallclock ≈ Sweep-Wallclock ⇒
    worker_utilisation ≈ 1.0 (Akzeptanzkriterium #851-AK-4)."""
    studies = [{"study_wallclock_s": 30.0}, {"study_wallclock_s": 20.0}, {"study_wallclock_s": 45.0}]
    out = _worker_utilisation(studies, n_jobs=1, sweep_wallclock_s=95.0)
    assert out == 1.0


def test_worker_utilisation_scales_with_n_jobs():
    studies = [{"study_wallclock_s": 100.0}]
    out = _worker_utilisation(studies, n_jobs=4, sweep_wallclock_s=50.0)
    assert out == 0.5  # 100 / (4*50)


def test_worker_utilisation_none_without_data():
    assert _worker_utilisation([], n_jobs=4, sweep_wallclock_s=50.0) is None
    assert _worker_utilisation([{"study_wallclock_s": 10.0}], n_jobs=None, sweep_wallclock_s=50.0) is None
    assert _worker_utilisation([{"study_wallclock_s": 10.0}], n_jobs=4, sweep_wallclock_s=None) is None


# ── summary_de.py Abschnitte 3.2/3.4 ──────────────────────────────────────────────────────────────
def _minimal_report(**overrides):
    base = {
        "run_id": "run-abc",
        "run_status": "complete",
        "started_at_utc": "2026-07-30T00:00:00Z",
        "wallclock_s": 7200.0,
        "cli_args": {"n_jobs": 8, "n_jobs_source": "CLI"},
        "symbols_completed": None,
        "symbols_planned": None,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {},
            "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [],
            "boundary_solutions": [],
            "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_section_3_2_lists_wallclock_by_strategy():
    report = _minimal_report()
    report["cross_study"]["wallclock_by_strategy"] = {
        "ComboTrendVwapStrategy": {"median": 1500.0, "p90": 2400.0, "n": 59},
    }
    text = summary_de.generate_german_summary(report)
    section_3_2 = text.split("### 3.2")[1].split("### 3.3")[0]
    assert "nicht ableitbar" not in section_3_2
    assert "ComboTrendVwapStrategy" in section_3_2


def test_section_3_4_lists_symbol_barrier_wait_and_worker_utilisation():
    report = _minimal_report()
    report["cross_study"]["symbol_barrier_wait_s"] = {"XOM.ETORO": 1170.0}
    report["cross_study"]["worker_utilisation"] = 0.42
    text = summary_de.generate_german_summary(report)
    section_3_4 = text.split("### 3.4")[1]
    assert "nicht rekonstruierbar" not in section_3_4
    assert "XOM.ETORO" in section_3_4
    assert "42.0 %" in section_3_4


def test_section_3_2_and_3_4_degrade_gracefully_without_telemetry():
    """Pre-#851-Report (keine wallclock_by_strategy/symbol_barrier_wait_s-Keys) darf nicht
    crashen -- fail-open mit einem erklärenden Satz statt eines KeyError."""
    report = _minimal_report()
    text = summary_de.generate_german_summary(report)
    assert "## 3. Zeitdauer" in text
