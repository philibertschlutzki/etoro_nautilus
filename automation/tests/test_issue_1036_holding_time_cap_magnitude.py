"""Issue #1036 (Katalog #866) — ``check_holding_time_cap`` FAILte bislang nur ueber
``timebox_violating_trades_frac`` (Anteil zeitbox-verletzender Round-Trips). Eine einzelne,
ökonomisch untragbare Position (beobachtet: 3991 h bei einer 24-Bar-Zeitbox) verdünnt sich über
einen gross gepoolten Round-Trip-Nenner (128 347 Round-Trips im Referenzlauf) auf einen
unauffälligen Anteil (3 %) — "wie viele" und "wie schlimm" sind zwei verschiedene Fragen
(Pitfall #358). Der neue Magnituden-Ast FAILt unabhängig vom Anteil, sobald
``max_holding_time_s > hard_multiple * cap_s``.
"""
from automation.optimizer import invariants as inv

# cap_s = (6 + 3) * 3600 = 32400s (Default max_bars_in_trade_cap=6 seit Issue #1275/GH #1148,
# Katalog #1272-1297, P0 -- vormals 24 --, slack=3.0, bar_seconds=3600).
_CAP_S = 9 * 3600.0


def test_magnitude_branch_fails_on_extreme_single_position_despite_low_fraction():
    """Die #1036-Kernbeobachtung: SqueezeBreakout/TSLA mit max_holding_time_s=3991h besteht den
    Anteils-Ast (kein timebox_violating_trades_denominator hier), FAILt aber am Magnituden-Ast."""
    records = [{
        "strategy": "SqueezeBreakoutStrategy", "symbol": "TSLA.ETORO",
        "max_holding_time_s": 3991 * 3600.0,
    }]
    result = inv.check_holding_time_cap(records)
    assert result.passed is False
    assert "SqueezeBreakoutStrategy/TSLA.ETORO" in result.provenance["magnitude_offenders"]
    assert "Magnituden-Ast" in result.detail


def test_magnitude_branch_passes_within_hard_multiple():
    records = [{
        "strategy": "S", "symbol": "X",
        "max_holding_time_s": 2.0 * _CAP_S,  # unter dem Default-hard_multiple=3.0
    }]
    result = inv.check_holding_time_cap(records)
    assert result.passed is True


def test_magnitude_branch_respects_custom_hard_multiple():
    records = [{"strategy": "S", "symbol": "X", "max_holding_time_s": 4.0 * _CAP_S}]
    lenient = inv.check_holding_time_cap(records, hard_multiple=5.0)
    assert lenient.passed is True
    strict = inv.check_holding_time_cap(records, hard_multiple=3.0)
    assert strict.passed is False


def test_fraction_branch_still_fails_independently_of_magnitude():
    """Rueckwaertskompatibilitaet: eine Study mit 30 % leichten Ueberschreitungen (< 1.5x cap)
    FAILt weiterhin ueber den Anteils-Ast, auch ohne jede Magnituden-Verletzung."""
    records = [{
        "strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": 500,
        "timebox_violating_trades_numerator": 150,
        "timebox_violating_trades_frac": 0.30,
        "max_holding_time_s": 1.2 * _CAP_S,
    }]
    result = inv.check_holding_time_cap(records, study_tolerance=0.25)
    assert result.passed is False
    assert result.actual["S/X"] == 0.30
    assert "S/X" not in (result.provenance.get("magnitude_offenders") or {})


def test_both_branches_can_fail_on_the_same_study_independently():
    records = [{
        "strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": 500,
        "timebox_violating_trades_numerator": 150,
        "timebox_violating_trades_frac": 0.30,
        "max_holding_time_s": 4.0 * _CAP_S,
    }]
    result = inv.check_holding_time_cap(records, study_tolerance=0.25, hard_multiple=3.0)
    assert result.passed is False
    assert "S/X" in result.actual
    assert "S/X" in result.provenance["magnitude_offenders"]
    assert "Anteils-Ast" in result.detail and "Magnituden-Ast" in result.detail


def test_not_applicable_without_any_holding_time_telemetry():
    result = inv.check_holding_time_cap([{"strategy": "S", "symbol": "X"}])
    assert result.passed is True
    assert result.actual is None
