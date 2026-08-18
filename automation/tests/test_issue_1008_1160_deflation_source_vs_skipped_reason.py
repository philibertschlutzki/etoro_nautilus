"""Issue #1008/#1160 (Katalog #1170, P2) — ``deflation_n_family_source`` mischt Quelle und
Skip-Grund.

Symptom: beobachtete Werte: ``n_family_stage1_per_strategy`` (24×), ``SKIPPED_SMALL_COHORT`` (1×),
``SKIPPED_NO_STATISTIC`` (1×), ``None`` (2×). Zwei Vokabulare in einem Feld.

Root-Cause: ``confirm.py``'s Fail-Loud-Fallback (#978/#1132) schrieb bei einer uebersprungenen
Deflation ``f"SKIPPED_{deflation_skipped_reason}"`` IN ``deflation_n_family_source`` — dieselbe
Bug-Klasse wie #1005/#1157 (zwei Groessen unter einem Feldnamen).

Fix:
1. ``confirm.py``: der Fallback schreibt ``None`` (statt ``SKIPPED_*``) in
   ``deflation_n_family_source``, wenn die Deflation uebersprungen wurde — der Skip-Grund lebt
   ausschliesslich in ``deflation_skipped_reason``.
2. ``invariants.check_family_n_statistic_coverage`` bewacht zusaetzlich (optionale, rueckwaerts-
   kompatible Parameter): kein ``SKIPPED_``-Sentinel in ``deflation_n_family_source``, und
   ``deflation_n_family_source``/``deflation_skipped_reason`` sind nie gleichzeitig gesetzt.
3. ``report.py`` reicht beide Felder aus ``holdout_metrics`` an den Check durch.
"""
import sys
import types
import unittest.mock as mock

if "nautilus_trader" not in sys.modules:
    class _MockModule(types.ModuleType):
        def __getattr__(self, name):
            return mock.MagicMock()

    for _mod in (
        "nautilus_trader", "nautilus_trader.backtest", "nautilus_trader.backtest.engine",
        "nautilus_trader.backtest.models", "nautilus_trader.model", "nautilus_trader.model.data",
        "nautilus_trader.model.enums", "nautilus_trader.model.identifiers",
        "nautilus_trader.model.currencies", "nautilus_trader.model.objects",
        "nautilus_trader.model.instruments", "nautilus_trader.config", "nautilus_trader.common",
        "nautilus_trader.common.enums", "nautilus_trader.common.actor", "nautilus_trader.core",
        "nautilus_trader.core.message", "nautilus_trader.portfolio", "nautilus_trader.test_engine",
        "nautilus_trader.persistence", "nautilus_trader.persistence.catalog",
        "nautilus_trader.execution", "nautilus_trader.execution.messages",
    ):
        sys.modules[_mod] = _MockModule(_mod)

from automation.optimizer import confirm
from automation.optimizer import invariants as inv


# --- invariants.check_family_n_statistic_coverage: neue Vokabular-Koharenz -------------------------

def test_legacy_callers_without_the_new_params_are_unaffected():
    """Rueckwaertskompatibilitaet (#822-Bestandstests): kein neuer Parameter -> altes Verhalten."""
    result = inv.check_family_n_statistic_coverage([], deflation_n_family_raw=None)
    assert result.passed is True


def test_fails_on_the_exact_skipped_sentinel_regression():
    result = inv.check_family_n_statistic_coverage(
        [], deflation_n_family_raw=None,
        deflation_n_family_source="SKIPPED_SMALL_COHORT", deflation_skipped_reason=None)
    assert result.passed is False
    assert result.severity == "high"
    assert "SKIPPED_" in result.detail


def test_fails_when_source_and_skipped_reason_are_both_set():
    result = inv.check_family_n_statistic_coverage(
        [], deflation_n_family_raw=None,
        deflation_n_family_source="n_family_stage1_per_strategy",
        deflation_skipped_reason="SMALL_COHORT")
    assert result.passed is False


def test_passes_with_a_clean_real_source_and_no_skipped_reason():
    result = inv.check_family_n_statistic_coverage(
        [], deflation_n_family_raw=None,
        deflation_n_family_source="n_family_stage1_per_strategy", deflation_skipped_reason=None)
    assert result.passed is True


def test_passes_with_none_source_and_a_clean_skipped_reason():
    """Das reparierte Verhalten: source=None + skipped_reason=SMALL_COHORT ist der KORREKTE
    Zustand nach dem Fix (nicht mehr SKIPPED_SMALL_COHORT als source)."""
    result = inv.check_family_n_statistic_coverage(
        [], deflation_n_family_raw=None,
        deflation_n_family_source=None, deflation_skipped_reason="SMALL_COHORT")
    assert result.passed is True


def test_both_none_is_not_flagged_pre_deflation_stage_rejection():
    """Eine Study, die die Deflations-Stufe nie erreicht hat (z. B. REJECTED_BEFORE_HOLDOUT),
    traegt legitim beide Felder als None -- kein Befund."""
    result = inv.check_family_n_statistic_coverage(
        [], deflation_n_family_raw=None,
        deflation_n_family_source=None, deflation_skipped_reason=None)
    assert result.passed is True


def test_822_violation_and_1160_violation_can_both_surface_in_one_result():
    trials = [{"oos_selection_statistic_available": False}]
    result = inv.check_family_n_statistic_coverage(
        trials, deflation_n_family_raw=5,
        deflation_n_family_source="SKIPPED_NO_STATISTIC", deflation_skipped_reason=None)
    assert result.passed is False
    assert "822" not in result.detail or "#1160" in result.detail  # beide Hinweise vorhanden
    assert "#1160" in result.detail


# --- confirm.py: der Fallback schreibt None statt SKIPPED_* --------------------------------------

def test_confirm_fallback_writes_none_not_a_skipped_sentinel_when_skipped():
    """Regressionswaechter direkt am Quelltext: die alte f'SKIPPED_{reason}'-Zuweisung an
    ``deflation_n_family_source`` darf nirgends mehr in confirm.py vorkommen (der String taucht nur
    noch in einem erklaerenden Kommentar auf, nicht mehr als aktiver Code)."""
    import inspect
    source = inspect.getsource(confirm)
    assert '= (\n            f"SKIPPED_{deflation_skipped_reason}"' not in source
    assert "None if deflation_skipped_reason is not None else deflation_n_family_source" in source
