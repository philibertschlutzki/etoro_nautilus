"""Issue #554 — Telemetrie: aktionierbare Reject-Gründe (adaptive Präzision + Delta-Dict).

Der frühere fixe ``:.5f`` stellte bei mikroskopischen Schwellen (5e-05) den Ist-Wert und die
Schwelle identisch dar (``0.00005 < 0.00005``), sodass der echte Rest-Gap unsichtbar war. Der Fix
nutzt adaptive Präzision (``.6g``) plus ein numerisches Δ im String UND ein maschinenlesbares
``oos_gate_deltas``-Dict.
"""
import pytest

from automation.backtest_runner import _evaluate_oos_eligibility

CFG = {
    "oos_min_trades": 1,
    "oos_min_total_return": 0.0,
    "oos_min_expectancy": 5e-05,
    "oos_min_win_rate": 0.0,
    "max_drawdown": 1.0,
    "eligible_requires_all": ["min_expectancy"],
}


def _oos(expectancy):
    return {"total_trades": 20, "expectancy": expectancy, "total_return": 0.01,
            "win_rate": 0.5, "max_drawdown": 0.05, "median_position_notional": 1000.0,
            "sortino_ratio": 1.0, "profit_factor": 1.5}


def test_near_miss_distinguishable_from_gross_miss():
    """Akzeptanzkriterium: Δ=−1e-7 im Reject-String unterscheidbar von Δ=−1e-4."""
    near = _evaluate_oos_eligibility(_oos(5e-05 - 1e-7), CFG)
    gross = _evaluate_oos_eligibility(_oos(5e-05 - 1e-4), CFG)
    near_str = " ".join(near["oos_rejection_reasons"])
    gross_str = " ".join(gross["oos_rejection_reasons"])
    assert "oos_min_expectancy" in near_str
    assert "oos_min_expectancy" in gross_str
    # Vorher waren beide identisch ('0.00005 < 0.00005'); jetzt klar unterscheidbar.
    assert near_str != gross_str


def test_no_rounding_artifact_for_small_values():
    """Reject-Gründe zeigen für Werte < 1e-3 signifikante Stellen (kein 0.00000-Artefakt)."""
    ev = _evaluate_oos_eligibility(_oos(4.99e-05), CFG)  # knapp unter 5e-05
    s = " ".join(ev["oos_rejection_reasons"])
    assert "0.00000" not in s
    assert "4.99" in s  # signifikante Stellen sichtbar


def test_gate_deltas_are_numeric_dict():
    """JSON-Event-Feld oos_gate_deltas ist ein numerisches Dict (kein String-Parsing nötig)."""
    ev = _evaluate_oos_eligibility(_oos(4.9e-05), CFG)
    deltas = ev["oos_gate_deltas"]
    assert isinstance(deltas, dict)
    assert all(isinstance(v, float) for v in deltas.values())
    assert "oos_min_expectancy" in deltas
    # actual − threshold; negativ = Gate verfehlt.
    assert deltas["oos_min_expectancy"] == pytest.approx(4.9e-05 - 5e-05)
    assert deltas["oos_min_expectancy"] < 0.0


def test_drawdown_delta_sign_convention():
    """max_drawdown-Delta = cap − actual (einheitlich: negativ = Gate verfehlt)."""
    oos = {"total_trades": 20, "expectancy": 0.01, "total_return": 0.01, "win_rate": 0.5,
           "max_drawdown": 0.5, "sortino_ratio": 1.0, "profit_factor": 1.5,
           "median_position_notional": 1000.0}
    ev = _evaluate_oos_eligibility(oos, {"oos_min_trades": 1, "oos_max_drawdown": 0.3,
                                         "eligible_requires_all": ["max_drawdown"]})
    assert ev["oos_gate_deltas"]["oos_max_drawdown"] == pytest.approx(0.3 - 0.5)
    assert ev["oos_gate_deltas"]["oos_max_drawdown"] < 0.0


def test_not_evaluated_has_empty_gate_deltas():
    """Schemastabilität: der Not-Evaluated-Pfad liefert ein leeres numerisches Delta-Dict."""
    ev = _evaluate_oos_eligibility({"total_trades": 0}, CFG)
    assert ev["oos_gate_deltas"] == {}
