"""Issue #1091/#1239 (P1, Katalog #1247+) — ``deflation_n_family_raw`` kollabiert auch bei
``deflation_n_family=None`` auf 0, muss aber genauso wie ein explizites 0 REJECT.

Root-Cause: der #1034/#1183-Guard (``confirm.py``) prüfte nur die EXTERN übergebene Größe
(``deflation_n_family is not None and int(deflation_n_family) <= 0``), nicht die INTERN bereits
aus demselben Parameter abgeleitete ``deflation_n_family_raw = int(deflation_n_family or 0)``. Ein
Aufrufer, der eine unauflösbare Familien-Multiplizität über ``deflation_n_family=None`` statt eines
expliziten 0 signalisiert (z. B. eine ``FAMILY_EXCLUDED_DEGENERATE``-Study, #981/#1135, deren
Aufrufer die Familien-Ermittlung selbst nicht durchführen konnte), rutschte am #1034-Hard-Stop
vorbei — obwohl ``deflation_n_effective = max(deflation_n, deflation_n_family_effective)`` weiter
oben in derselben Funktion den Rückfall auf das per-Study-N bereits lautlos maskiert hatte, OHNE
jedes Flag. Symptom (Katalog): SqueezeBreakout/TSLA, 180 Trials, ``deflation_n_family_raw=0``,
``deflation_n_family_frozen=None``, 20/48/51 eligible Trials über drei aufeinanderfolgende Läufe —
alle drei passierten die Promotion trotz strukturell fehlender Familien-Multiplizität.

Fix: ``confirm.py``'s ``family_n_unresolvable``-Bedingung um einen zusätzlichen OR-Arm
``deflation_n_family_raw in (None, 0)`` erweitert — der Hard-Stop greift jetzt unabhängig davon, ob
der Aufrufer explizit 0 oder gar nichts (``None``) übergeben hat. Produktionsverhalten
unverändert: ``sweep.py`` löst ``deflation_n_family`` immer über
``(n_family_stage1_map or {}).get((strategy, symbol), 0)`` auf und übergibt NIE ``None`` — nur
Legacy-/Unit-Test-Aufrufer, die den Parameter weglassen, sind von der Verschärfung betroffen (siehe
auch die aktualisierte Regression
``test_issue_1034_1183_family_excluded_degenerate_deflation.py::
test_none_deflation_n_family_now_rejects_via_1091_raw_collapse``).

``invariants.check_family_n_statistic_coverage`` blockte ``deflation_n_family_raw<=0`` bereits seit
#1034 unverändert (severity ``blocking``, siehe #822) — dieser Test bestätigt nur, dass #1091 daran
nichts ändert (keine Code-Änderung in ``invariants.py`` nötig).
"""
import json
from pathlib import Path

from automation.optimizer import confirm
from automation.optimizer import invariants as inv
from automation.tests.test_issue_1034_1183_family_excluded_degenerate_deflation import (
    _SENTINEL_OMIT,
    _build_study,
    _result_payload,
    _holdout_factory,
)


def _confirm_tsla_with_family_n(tmp_path, monkeypatch, *, deflation_n_family):
    """Nachbau des Katalog-Symptoms (SqueezeBreakout/TSLA): eine Study mit ausreichend eigener
    Kohorte (deflation_n=20 >= 2), die das Holdout-Gate passiert."""
    global_params = {"price_breakout_period": 20}
    cohort_periods = [0.02 + 0.001 * i for i in range(20)]
    study = _build_study(tmp_path, monkeypatch, n_trials=20, cohort_periods=cohort_periods)
    fixed_result = _result_payload(sortino_ratio=2.0, dd=0.05, sortino_period=0.03, n_periods=200)

    kwargs = {}
    if deflation_n_family is not _SENTINEL_OMIT:
        kwargs["deflation_n_family"] = deflation_n_family
    return confirm.confirm_per_symbol_promotion(
        study, "SqueezeBreakoutStrategy", "TSLA.ETORO", global_params=global_params,
        run_backtest=_holdout_factory(global_params, symbol_result=fixed_result,
                                      global_result=fixed_result),
        **kwargs,
    )


def test_family_excluded_degenerate_via_none_rejects_not_silently_promotes(tmp_path, monkeypatch):
    """Primärreproduktion des Katalog-Symptoms: ``deflation_n_family=None`` (der Aufrufer konnte
    keine Familien-N ermitteln, statt sie explizit als 0 zu melden) darf die Promotion nicht
    stillschweigend über das per-Study-N durchlassen."""
    res = _confirm_tsla_with_family_n(tmp_path, monkeypatch, deflation_n_family=_SENTINEL_OMIT)

    assert res["promote"] is False
    assert res["holdout_passed"] is False
    assert res["status"] == "REJECTED_ON_DEFLATION"
    assert res["is_rejection_detail_override"] == "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"
    # Die Deflationsstufe wurde tatsächlich erreicht (SR0 berechnet) — der Reject ist ein
    # bewusstes Veto, keine fehlende Berechnung (analog #1034-Akzeptanzkriterium 1).
    assert res["metrics_symbol"]["deflated_sr0"] is not None
    assert res["stage_results"]["deflation"] == {
        "passed": False, "detail": "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"}


def test_explicit_zero_still_rejects_unchanged(tmp_path, monkeypatch):
    """Der bereits von #1034 abgedeckte explizite ``deflation_n_family=0``-Pfad bleibt durch #1091
    unverändert (kein Doppel-Fix, keine Regression)."""
    res = _confirm_tsla_with_family_n(tmp_path, monkeypatch, deflation_n_family=0)

    assert res["is_rejection_detail_override"] == "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"


def test_positive_family_n_still_unaffected(tmp_path, monkeypatch):
    """Eine reale, positive Familien-Multiplizität löst den Hard-Stop weiterhin nicht aus."""
    res = _confirm_tsla_with_family_n(tmp_path, monkeypatch, deflation_n_family=496)

    assert res["is_rejection_detail_override"] != "REJECT_PROMOTION_FAMILY_UNRESOLVABLE"


def test_check_family_n_statistic_coverage_unaffected_by_1091(tmp_path, monkeypatch):
    """``check_family_n_statistic_coverage`` (invariants.py) blockte ``deflation_n_family_raw<=0``
    bereits seit #1034 — #1091 ändert an dieser Invariante nichts, nur an ``confirm.py``'s
    Hard-Stop-Bedingung."""
    result_zero = inv.check_family_n_statistic_coverage([], deflation_n_family_raw=0)
    assert result_zero.passed is False
    assert result_zero.severity == "blocking"

    result_not_reached = inv.check_family_n_statistic_coverage([], deflation_n_family_raw=None)
    assert result_not_reached.passed is True
