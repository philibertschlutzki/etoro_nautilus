"""Issue #1006/#1158 (Katalog #1170, P2) — ``n_family.frozen`` 1743 vs. ``observed`` 1744 (NVDA).

Symptom: Differenz exakt 1 — die Study SqueezeBreakout/NVDA trägt eingefroren 0, zur Berichtszeit
1. Für PLTR ist die Summe identisch (1839).

Root-Cause: ``family_membership == 'excluded_degenerate'`` (korrekt gesetzt nach
``oos_n_periods_median < min_oos_periods_for_family``, #981/#1135) wird beim Einfrieren
(``sweep.py``'s Symbol-Dispatch-Schleife) angewendet — die betroffene Study zählt dort explizit
mit 0. Beim Beobachten (``report._family_n_stages``) fehlt das Feld ``n_family_stage1`` für diese
Study jedoch (``deflation_n_family`` wird NIE gestempelt, weil ``confirm.py``'s Deflation für eine
so kurze Study übersprungen wird — der ``if deflation_sr0 is not None:``-Guard) — der #1080-
Fallback auf ``n_selection_statistic_available`` griff dann OHNE den Ausschluss zu prüfen und
zählte die Study mit ihrer rohen Trial-Statistik (1) mit.

Fix:
1. EINE geteilte Filterfunktion ``sweep._family_members(family_membership)`` — von BEIDEN Summen
   aufgerufen (dem eingefrorenen Pfad in ``sweep.py`` UND dem beobachteten Pfad in
   ``report._family_n_stages``, importiert von dort).
2. ``check_family_n_stability`` (``invariants.py``) failt bei JEDER Differenz != 0 statt erst bei
   einer 5%-relativen Lücke (die vorherige Toleranz UND ein ``frozen <= 0: continue``-Skip
   verdeckten das Ein-Study-Symptom strukturell).
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report
from automation.optimizer import sweep


# --- sweep._family_members: die geteilte Filterfunktion -------------------------------------------

def test_family_members_excludes_the_degenerate_label():
    assert sweep._family_members("excluded_degenerate") is False


def test_family_members_includes_none_and_any_other_label():
    assert sweep._family_members(None) is True
    assert sweep._family_members("some_other_future_label") is True


# --- report._family_n_stages consumes the same predicate as sweep.py ------------------------------

def test_degenerate_study_is_excluded_even_when_n_selection_statistic_available_is_positive():
    """Reproduziert das #1158-Referenzsymptom: die Study hat KEIN ``n_family_stage1`` (Deflation
    uebersprungen), aber ``n_selection_statistic_available=1`` (der alte #1080-Fallback-Wert) UND
    ``family_membership='excluded_degenerate'`` -- muss trotzdem mit N1=0 zaehlen, NICHT mit 1."""
    studies_out = [
        {"symbol": "NVDA.ETORO", "strategy": "SqueezeBreakoutStrategy",
         "n_family_stage1": None, "n_selection_statistic_available": 1,
         "family_membership": "excluded_degenerate"},
        {"symbol": "NVDA.ETORO", "strategy": "TrendPullbackStrategy", "n_family_stage1": 1742},
    ]
    stage1, stage2 = report._family_n_stages(studies_out)
    assert stage1["NVDA.ETORO"]["SqueezeBreakoutStrategy"] == 0
    assert stage1["NVDA.ETORO"]["TrendPullbackStrategy"] == 1742
    # Issue #1080-Fallback bleibt fuer NICHT-degenerierte Studies unveraendert wirksam.
    assert stage2["NVDA.ETORO"] == 1


def test_non_degenerate_study_still_uses_the_1080_fallback_unchanged():
    studies_out = [
        {"symbol": "PLTR.ETORO", "strategy": "TrendPullbackStrategy",
         "n_family_stage1": None, "n_selection_statistic_available": 111,
         "family_membership": None},
    ]
    stage1, _ = report._family_n_stages(studies_out)
    assert stage1["PLTR.ETORO"]["TrendPullbackStrategy"] == 111


def test_observed_now_matches_frozen_for_the_reference_scenario():
    """Reproduziert den vollen #1158-Vergleich: PLTR bleibt 1839==1839 (identisch, wie im Symptom
    beschrieben); NVDA war 1743 (frozen) vs. 1744 (observed) -- mit dem Fix sind beide 1743."""
    frozen_by_symbol = {"PLTR.ETORO": 1839, "NVDA.ETORO": 1743}
    studies_out = [
        {"symbol": "PLTR.ETORO", "strategy": "A", "n_family_stage1": 1839},
        {"symbol": "NVDA.ETORO", "strategy": "SqueezeBreakoutStrategy",
         "n_family_stage1": None, "n_selection_statistic_available": 1,
         "family_membership": "excluded_degenerate"},
        {"symbol": "NVDA.ETORO", "strategy": "TrendPullbackStrategy", "n_family_stage1": 1743},
    ]
    stage1, _ = report._family_n_stages(studies_out)
    observed_by_symbol = {
        symbol: sum(per_strategy.values()) for symbol, per_strategy in stage1.items()
    }
    assert observed_by_symbol == frozen_by_symbol
    result = inv.check_family_n_stability(frozen_by_symbol, observed_by_symbol)
    assert result.passed is True


# --- invariants.check_family_n_stability: exakte Gleichheit, keine Toleranzschwelle mehr ----------

def test_check_family_n_stability_fails_on_the_reference_symptom_without_the_fix():
    """Ohne den Fix (hier direkt simuliert: observed traegt weiterhin die rohe 1744) faellt der
    Check jetzt SOFORT auf -- vorher haette die 5%-Toleranz (|1743-1744|/1743 = 0.06%) diese echte
    Ein-Study-Abweichung stillschweigend durchgelassen."""
    result = inv.check_family_n_stability({"NVDA.ETORO": 1743}, {"NVDA.ETORO": 1744})
    assert result.passed is False
    assert result.severity == "blocking"
    assert result.actual["NVDA.ETORO"]["difference"] == 1


def test_check_family_n_stability_no_longer_skips_a_zero_frozen_value():
    """Vor dem Fix: ``frozen <= 0: continue`` liess ``frozen=0`` NIE auswerten -- exakt der Fall
    einer Symbol-Familie, die AUSSCHLIESSLICH aus einer degenerierten Study besteht."""
    result = inv.check_family_n_stability({"GHOST.ETORO": 0}, {"GHOST.ETORO": 3})
    assert result.passed is False
    assert "GHOST.ETORO" in result.actual
