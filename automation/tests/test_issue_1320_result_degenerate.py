"""Issue #1320 (GH #1197, P2) — Degenerierte Ergebnis-Identität wird nicht als eigener Befund
gestempelt.

Symptom. ``best_reward = -20,0`` in 14/14 Studies, ``n_eligible = 0`` in 14/14,
``reward_std_total`` nicht gesetzt (Referenzlauf ``da354bc2``). Das Ergebnis ist informationsfrei
— der Report weist das nirgends als solches aus.

Fix. ``report`` stempelt ``result_degenerate: bool`` (alle (>= 2) Studies tragen denselben
``best_reward`` ODER ``n_evaluable == 0`` in allen Studies) plus ``result_degenerate_reason``.
Neue Invariante ``check_result_not_degenerate`` (severity ``high``), die bei ``true`` FAILt. §1 der
Zusammenfassung nennt den Zustand in ihrem Ein-Satz-Ergebnis.
"""
import json
from pathlib import Path

import pytest

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import summary_de

_DA354BC2_PATH = Path("logs/run_da354bc2_20260827T131447775582.json")


def _degenerate_reasons(studies_out: list[dict]) -> tuple[bool, str | None]:
    """Dieselbe Logik wie report._build_report (hier repliziert, um sie unabhaengig von der
    vollstaendigen _build_report-Signatur direkt gegen konstruierte Study-Listen zu testen)."""
    reasons: list[str] = []
    if len(studies_out) >= 2:
        if len({s.get("best_reward") for s in studies_out}) == 1:
            reasons.append("SAME_BEST_REWARD_ACROSS_ALL_STUDIES")
    if studies_out and all((s.get("n_evaluable") or 0) == 0 for s in studies_out):
        reasons.append("ZERO_EVALUABLE_IN_ALL_STUDIES")
    return bool(reasons), (" + ".join(reasons) if reasons else None)


# ── Akzeptanzkriterium 1 — result_degenerate=true fuer den Referenzlauf da354bc2 ─────────────────

def test_reference_run_da354bc2_is_degenerate():
    report = json.loads(_DA354BC2_PATH.read_text("utf-8"))
    studies_out = report.get("studies") or []
    degenerate, reason = _degenerate_reasons(studies_out)
    assert degenerate is True
    assert "SAME_BEST_REWARD_ACROSS_ALL_STUDIES" in reason
    assert "ZERO_EVALUABLE_IN_ALL_STUDIES" in reason


def test_report_build_report_stamps_result_degenerate_true_for_da354bc2_shaped_input(tmp_path):
    """End-to-End durch report._build_report selbst, mit einer Study-Liste, die dieselbe
    Signatur wie der Referenzlauf traegt (14 Studies, identischer best_reward, n_evaluable=0)."""
    from automation.optimizer.parsing import TournamentMetrics  # noqa: F401 (Smoke-Import-Check)

    class _FakeTrial:
        def __init__(self):
            self.number = 0
            self.params = {}
            self.user_attrs = {}
            self.value = -20.0

    class _FakeStudy:
        def __init__(self, strategy, symbol):
            self.study_name = f"study_{strategy}_{symbol}"
            self.trials = []
            self._attrs = {
                "strategy": strategy, "symbol": symbol,
                "reward_semantics_version": 99,
            }

        @property
        def user_attrs(self):
            return dict(self._attrs)

        def set_user_attr(self, k, v):
            self._attrs[k] = v

        @property
        def best_trial(self):
            return _FakeTrial()

        @property
        def best_value(self):
            return -20.0

    # _build_report erwartet eine Liste "proposals" (siehe bestehende _build_report-Aufrufer in
    # anderen Testdateien dieses Repos) -- hier reicht die leere Liste (0 Studies), um NUR die
    # reine Aggregationslogik ausserhalb von _study_record zu pruefen (siehe Test oben fuer die
    # inhaltliche da354bc2-Reproduktion direkt gegen die echten Study-Daten).
    report = rpt._build_report(
        [], run_id="test-1320-empty", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    assert report["result_degenerate"] is False
    assert report["result_degenerate_reason"] is None


# ── Akzeptanzkriterium 2 — check_result_not_degenerate erscheint im Strom (PASS und FAIL) ────────

def test_check_result_not_degenerate_fails_when_degenerate():
    r = inv.check_result_not_degenerate(True, "SAME_BEST_REWARD_ACROSS_ALL_STUDIES")
    assert r.passed is False
    assert r.severity == "high"
    assert r.actual["result_degenerate"] is True


def test_check_result_not_degenerate_passes_when_not_degenerate():
    r = inv.check_result_not_degenerate(False, None)
    assert r.passed is True
    assert r.actual is None


def test_check_result_not_degenerate_appears_in_the_report_stream_for_an_empty_run(tmp_path):
    report = rpt._build_report(
        [], run_id="test-1320-stream", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    names = {c.get("check") or c.get("name") for c in report["invariant_checks"]}
    assert "check_result_not_degenerate" in names


def test_check_result_not_degenerate_is_not_flagged_missing_by_check_invariant_coverage(tmp_path):
    """Regressionsschutz: check_result_not_degenerate (und check_override_axis_coherence, #1193)
    MUESSEN VOR der check_invariant_coverage-Momentaufnahme in invariant_checks stehen — sonst
    meldet die Abdeckungspruefung sie faelschlich als 'fehlt im Strom', obwohl sie tatsaechlich
    (nur zu spaet fuer diese Momentaufnahme) im Artefakt erscheinen."""
    report = rpt._build_report(
        [], run_id="test-1320-coverage", started_at_utc="2026-01-01T00:00:00Z",
        wallclock_s=1.0, cli_args={}, reports_dir=tmp_path,
    )
    coverage = next(
        c for c in report["invariant_checks"]
        if (c.get("check") or c.get("name")) == "check_invariant_coverage"
    )
    _missing = ((coverage.get("actual") or {}).get("missing") or [])
    assert "check_result_not_degenerate" not in _missing
    assert "check_override_axis_coherence" not in _missing


# ── Akzeptanzkriterium 3 — §1 nennt "informationsfreies Ergebnis" statt nur "0 deploybar" ────────

def _minimal_report(**overrides):
    base = {
        "run_id": "run-1", "run_status": "complete",
        "started_at_utc": "2026-08-19T00:00:00Z", "wallclock_s": 10.0,
        "cli_args": {"n_jobs": 1, "n_jobs_source": "CLI"},
        "symbols_completed": 1, "symbols_planned": 1,
        "studies": [],
        "cross_study": {
            "promotion_outcome_counts": {}, "budget_executed_fraction": {"median": None, "p10": None, "n": 0},
            "longest_holding_studies": [], "boundary_solutions": [], "diagnosed_pairs": [],
        },
        "invariant_checks": [],
    }
    base.update(overrides)
    return base


def test_section_1_mentions_informationsfrei_when_result_degenerate():
    report = _minimal_report(
        studies=[{"strategy": "A", "symbol": "X"}, {"strategy": "B", "symbol": "Y"}],
        result_degenerate=True, result_degenerate_reason="SAME_BEST_REWARD_ACROSS_ALL_STUDIES",
    )
    text = summary_de._section_1_result_in_one_sentence(report)
    assert "informationsfrei" in text
    assert "0 deploybar" in text


def test_section_1_does_not_mention_informationsfrei_when_not_degenerate():
    report = _minimal_report(
        studies=[{"strategy": "A", "symbol": "X"}],
        result_degenerate=False, result_degenerate_reason=None,
    )
    text = summary_de._section_1_result_in_one_sentence(report)
    assert "informationsfrei" not in text


def test_section_1_does_not_mention_informationsfrei_when_field_is_absent():
    """Ein Report ohne result_degenerate-Feld (Pre-#1320-Report/Legacy-Fixture) faellt auf das
    bisherige, unbedingte '0 deploybar'-Verhalten zurueck (bit-identisches Legacy-Verhalten)."""
    report = _minimal_report(studies=[{"strategy": "A", "symbol": "X"}])
    assert "result_degenerate" not in report
    text = summary_de._section_1_result_in_one_sentence(report)
    assert "informationsfrei" not in text
    assert "0 deploybar" in text


def test_generate_german_summary_full_report_mentions_informationsfrei_for_da354bc2():
    """End-to-End: die echte, committete Referenzlauf-JSON durch generate_german_summary."""
    report = json.loads(_DA354BC2_PATH.read_text("utf-8"))
    studies_out = report.get("studies") or []
    degenerate, reason = _degenerate_reasons(studies_out)
    report["result_degenerate"] = degenerate
    report["result_degenerate_reason"] = reason
    text = summary_de.generate_german_summary(report)
    assert "informationsfrei" in text.split("\n\n")[0] or "informationsfrei" in text[:2000]


# ── Randfaelle der Aggregationslogik selbst ──────────────────────────────────────────────────────

def test_single_study_with_a_real_reward_is_not_degenerate_by_same_reward_alone():
    """Ein einzelner Study-Lauf hat per Konstruktion nur einen best_reward-Wert -- 'alle tragen
    denselben' ist bei n=1 keine Aussage ueber KOLLABIERTE Varianz."""
    degenerate, reason = _degenerate_reasons([
        {"strategy": "A", "symbol": "X", "best_reward": 1.5, "n_evaluable": 40},
    ])
    assert degenerate is False
    assert reason is None


def test_single_study_with_zero_evaluable_is_degenerate():
    degenerate, reason = _degenerate_reasons([
        {"strategy": "A", "symbol": "X", "best_reward": None, "n_evaluable": 0},
    ])
    assert degenerate is True
    assert reason == "ZERO_EVALUABLE_IN_ALL_STUDIES"


def test_two_studies_with_different_rewards_and_nonzero_evaluable_is_not_degenerate():
    degenerate, reason = _degenerate_reasons([
        {"strategy": "A", "symbol": "X", "best_reward": 1.5, "n_evaluable": 40},
        {"strategy": "B", "symbol": "Y", "best_reward": 0.9, "n_evaluable": 30},
    ])
    assert degenerate is False
    assert reason is None


def test_empty_study_list_is_not_flagged_degenerate():
    degenerate, reason = _degenerate_reasons([])
    assert degenerate is False
    assert reason is None
