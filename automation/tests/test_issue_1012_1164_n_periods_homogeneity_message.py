"""Issue #1012/#1164 (Katalog #1170, P2) — ``check_n_periods_homogeneity`` behauptet eine
Konsequenz, die die Konfiguration ausschliesst.

Symptom: der Meldungstext lautet "die #865-Heterogenitäts-Suppression (deflation_max_n_periods_
ratio) greift vermutlich für praktisch jede Familie dieses Symbols". Gemessen wird die Spannweite
ZWISCHEN Studies eines Symbols. Die #865-Suppression arbeitet auf ``cohort_n_periods`` — den
eligiblen Trials INNERHALB einer Study (``confirm.py``). Unter ``promotion_family_scope =
'per_strategy'`` gibt es keinen Pfad, auf dem die studienübergreifende Spannweite eine
Suppression auslöst. Gegenprobe: ``deflation_skipped_reason`` war in 28/28 ``None``.

Root-Cause: der Text stammt aus der Ära der symbolweiten Familie (vor #826/#1131) und wurde beim
Scope-Wechsel nicht nachgezogen (dieselbe Klasse wie #1128 — verdrahtete Schlussfolgerung).

Fix: Meldungstext auf die tatsächlich gemessene Grösse reduziert; ein Verweis auf #865 nur, wenn
``promotion_family_scope != 'per_strategy'``.
"""
from automation.optimizer import invariants as inv


_OFFENDING_RECORDS = [
    {"symbol": "XOM.ETORO", "strategy": "OpeningRangeBreakout", "oos_n_periods_median": 72},
    {"symbol": "XOM.ETORO", "strategy": "AdxAtrMomentum", "oos_n_periods_median": 816},
]


def test_no_865_reference_under_the_default_per_strategy_scope():
    """Akzeptanzkriterium: ein Test prüft, dass der #865-Verweis unter 'per_strategy' nicht
    erscheint. 'Kein Verweis' heisst hier praezise: die Meldung behauptet NICHT mehr, dass die
    Suppression tatsaechlich greift (deflation_max_n_periods_ratio als aktive Konsequenz) -- ein
    erklaerender Gegenbeweis-Satz, der #865 nennt, um dessen NICHT-Anwendbarkeit zu begruenden,
    ist davon ausdruecklich unterschieden und bleibt erlaubt (siehe naechster Test)."""
    result = inv.check_n_periods_homogeneity(
        _OFFENDING_RECORDS, promotion_family_scope="per_strategy")
    assert result.passed is False
    assert "deflation_max_n_periods_ratio" not in result.detail
    assert "keinen Pfad von dieser Zwischen-Study-Spannweite zu einer #865-Suppression" in result.detail


def test_no_865_reference_when_scope_is_omitted_legacy_caller():
    """Ein Legacy-/Test-Aufrufer ohne promotion_family_scope kann die tatsächliche Konsequenz
    nicht behaupten -- der Verweis bleibt ebenfalls unterdrückt."""
    result = inv.check_n_periods_homogeneity(_OFFENDING_RECORDS)
    assert result.passed is False
    assert "deflation_max_n_periods_ratio" not in result.detail


def test_865_reference_appears_under_a_non_per_strategy_scope():
    result = inv.check_n_periods_homogeneity(
        _OFFENDING_RECORDS, promotion_family_scope="per_symbol_best")
    assert result.passed is False
    assert "#865" in result.detail
    assert "deflation_max_n_periods_ratio" in result.detail


def test_message_names_the_actually_measured_quantity():
    result = inv.check_n_periods_homogeneity(
        _OFFENDING_RECORDS, promotion_family_scope="per_strategy")
    assert "Kommensurabilität" in result.detail
    assert "cohort_n_periods" in result.detail


def test_check_is_wired_with_promotion_family_scope_in_the_report():
    from automation.optimizer import report as rpt
    source = open(rpt.__file__, encoding="utf-8").read()
    assert "check_n_periods_homogeneity(\n        studies_out,\n        promotion_family_scope=" in source


def test_passing_result_still_reports_ok_regardless_of_scope():
    records = [
        {"symbol": "XOM.ETORO", "strategy": "A", "oos_n_periods_median": 200},
        {"symbol": "XOM.ETORO", "strategy": "B", "oos_n_periods_median": 400},
    ]
    result = inv.check_n_periods_homogeneity(records, promotion_family_scope="per_symbol_best")
    assert result.passed is True
    assert result.detail == "OK"
