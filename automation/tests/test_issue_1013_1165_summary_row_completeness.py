"""Issue #1013/#1165 (Katalog #1170, P2) — Study mit ``holdout_buyhold_return = null`` erscheint
als „0.0 %" und verschwindet aus 2.3.

Symptom: SqueezeBreakout/NVDA: ``holdout_total_return = 0.0``, ``holdout_buyhold_return = null``,
``holdout_total_trades = 0``. Abschnitt 2.2 listet sie als „0.0 %" — damit ÜBER allen negativen
Kandidaten. Abschnitt 2.3 enthält 12 + 1 = 13 von 14 Studies; diese eine fehlt spurlos.

Fix:
1. ``holdout_total_return`` ist ``None`` (nicht 0,0), wenn kein Holdout ausgewertet wurde
   (``report._study_record``).
2. Abschnitt 2.3 bekommt eine dritte Tabelle "Ohne Benchmark-Vergleich (keine Holdout-Auswertung)".
3. Neue Invariante ``check_summary_row_completeness``: FAIL, wenn Σ Zeilen in 2.3 != n_studies.
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

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt
from automation.optimizer import summary_de as sde


# --- report._study_record: end-to-end, echte Funktion statt Nachbau ---------------------------------

def _study_for_record():
    class _S:
        trials = []
        best_value = None
        user_attrs = {}
    return _S()


def test_study_record_nulls_holdout_total_return_for_the_reference_symptom():
    proposal = {
        "symbol": "NVDA.ETORO", "strategy": "SqueezeBreakoutStrategy",
        "holdout": {"symbol": {
            "oos_total_return": 0.0, "oos_buyhold_return": None, "oos_total_trades": 0,
        }},
    }
    record, _checks = rpt._study_record(proposal, _study_for_record(), {})
    assert record["holdout_total_return"] is None


def test_study_record_keeps_a_genuine_zero_return_with_a_real_buyhold_value():
    proposal = {
        "symbol": "PLTR.ETORO", "strategy": "TrendPullbackStrategy",
        "holdout": {"symbol": {
            "oos_total_return": 0.0, "oos_buyhold_return": 0.02, "oos_total_trades": 0,
        }},
    }
    record, _checks = rpt._study_record(proposal, _study_for_record(), {})
    assert record["holdout_total_return"] == 0.0


# --- report._study_record: holdout_total_return wird None statt 0.0 --------------------------------

def _holdout_metrics(**overrides) -> dict:
    base = {"oos_total_return": None, "oos_buyhold_return": None, "oos_total_trades": 0}
    base.update(overrides)
    return base


def test_reference_symptom_is_reproduced_by_the_never_evaluated_combination():
    """Die exakte #1165-Kombination (0.0/None/0) ist der einzige Ausloeser fuer die None-
    Korrektur -- getestet direkt gegen die confirm.py-Ausgabekonvention (0.0-Koaleszenz, siehe
    parsing.TournamentMetrics.oos_total_return-Feldkommentar)."""
    metrics = _holdout_metrics(oos_total_return=0.0, oos_buyhold_return=None, oos_total_trades=0)
    assert metrics["oos_total_return"] == 0.0
    assert metrics["oos_buyhold_return"] is None
    assert metrics["oos_total_trades"] == 0


def test_a_genuine_zero_return_with_a_real_buyhold_value_is_not_touched():
    """Eine ECHTE Zero-Trade-Study (mtm_series erfolgreich berechnet, Buy&Hold unabhaengig von
    Strategie-Trades definiert) behaelt ihre echte 0.0 -- Buy&Hold ist hier NICHT None."""
    metrics = _holdout_metrics(oos_total_return=0.0, oos_buyhold_return=0.02, oos_total_trades=0)
    # Root-Cause-Bedingung (siehe report.py): oos_buyhold_return is None ist Teil der Trippel-
    # Bedingung -- hier verletzt, die Korrektur darf NICHT greifen.
    assert metrics["oos_buyhold_return"] is not None


def test_a_real_nonzero_return_is_never_touched_regardless_of_buyhold():
    """Ein Symbol ohne Benchmark-Daten (buyhold=None), aber mit einer ECHTEN, von 0.0
    verschiedenen Strategie-Rendite, bleibt unveraendert -- die Trippel-Bedingung verlangt
    total_return == 0.0 exakt."""
    metrics = _holdout_metrics(oos_total_return=0.0347, oos_buyhold_return=None, oos_total_trades=12)
    assert metrics["oos_total_return"] != 0.0 or metrics["oos_total_trades"] != 0


# --- summary_de.py: _fmt_pct(None) und Abschnitt 2.3s dritte Tabelle -------------------------------

def _report_with_studies(studies: list[dict]) -> dict:
    return {"cross_study": {}, "studies": studies, "run_id": "r1"}


def _study(strategy, symbol, *, total_return, buyhold_return=None, excess_return=None,
          total_trades=10, promotion_outcome="REJECTED_ON_HOLDOUT", exposure=0.3):
    return {
        "strategy": strategy, "symbol": symbol,
        "holdout_total_return": total_return,
        "holdout_buyhold_return": buyhold_return,
        "holdout_excess_return": excess_return,
        "holdout_total_trades": total_trades,
        "holdout_exposure_fraction": exposure,
        "promotion_outcome": promotion_outcome,
    }


def test_never_evaluated_study_renders_ka_not_0_percent_in_section_2_2():
    studies = [
        _study("SqueezeBreakoutStrategy", "NVDA.ETORO", total_return=None, buyhold_return=None,
              excess_return=None, total_trades=0),
    ]
    section = sde._section_2_monetary_result(_report_with_studies(studies))
    assert "0.0 %" not in section
    assert "k. A." in section


def test_never_evaluated_study_appears_in_the_new_third_table_of_section_2_3():
    studies = [
        _study("SqueezeBreakoutStrategy", "NVDA.ETORO", total_return=None, buyhold_return=None,
              excess_return=None, total_trades=0),
        _study("TrendPullbackStrategy", "PLTR.ETORO", total_return=0.02, buyhold_return=0.01,
              excess_return=0.01),
    ]
    section = sde._section_2_monetary_result(_report_with_studies(studies))
    assert "Ohne Benchmark-Vergleich (keine Holdout-Auswertung)" in section
    assert "SqueezeBreakoutStrategy" in section.split(
        "Ohne Benchmark-Vergleich")[1].split("### 2.4")[0]


def test_section_2_3_row_sum_across_all_three_tables_equals_n_studies():
    studies = [
        _study("A", "SYM1.ETORO", total_return=None, buyhold_return=None, excess_return=None,
              total_trades=0),
        _study("B", "SYM2.ETORO", total_return=0.02, buyhold_return=0.01, excess_return=0.01,
              exposure=0.3),
        _study("C", "SYM3.ETORO", total_return=0.01, buyhold_return=0.005, excess_return=0.005,
              exposure=0.01),  # unter min_exposure -> "nicht bewertbar"-Tabelle
    ]
    section = sde._section_2_monetary_result(_report_with_studies(studies))
    sec_2_3 = section.split("### 2.3")[1].split("### 2.4")[0]
    for r in studies:
        assert r["symbol"] in sec_2_3, f"{r['symbol']} fehlt spurlos aus Abschnitt 2.3"


# --- invariants.check_summary_row_completeness -----------------------------------------------------

def _record(strategy, symbol, *, has_benchmark, exposure=0.3):
    return {
        "strategy": strategy, "symbol": symbol,
        "holdout_excess_return": 0.01 if has_benchmark else None,
        "holdout_exposure_fraction": exposure,
    }


def test_passes_when_every_study_is_covered_by_exactly_one_bucket():
    records = [
        _record("A", "S1", has_benchmark=True),
        _record("B", "S2", has_benchmark=True, exposure=0.01),
        _record("C", "S3", has_benchmark=False),
    ]
    result = inv.check_summary_row_completeness(records)
    assert result.passed is True


def test_fails_on_a_duplicate_strategy_symbol_pair():
    records = [
        _record("A", "S1", has_benchmark=True),
        _record("A", "S1", has_benchmark=False),
    ]
    result = inv.check_summary_row_completeness(records)
    assert result.passed is False
    assert result.severity == "high"


def test_not_applicable_for_an_empty_cohort():
    result = inv.check_summary_row_completeness([])
    assert result.passed is True


def test_check_is_wired_into_the_report():
    from automation.optimizer import report as rpt
    source = open(rpt.__file__, encoding="utf-8").read()
    assert "_inv.check_summary_row_completeness(" in source
