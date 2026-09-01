"""Issue #1340 (GH #1234) — achsenbewusster Reachability-Preflight: kein Kandidat kann promoviert
werden, solange ``max_attainable_psr(T_holdout) < promotion_confidence`` gilt. Grösster
Ertragshebel des #1246-Katalogs.
"""
import pytest

from automation.optimizer.deflation import max_attainable_psr
from automation.optimizer.invariants import check_promotion_confidence_reachability
from automation.optimizer.sweep import compute_holdout_bar_count


# --- max_attainable_psr: reproduziert die im #1246-Preflight zitierten Zahlen -------------------

def test_max_attainable_psr_at_t_202_matches_issue_reference():
    assert max_attainable_psr(202) == pytest.approx(0.9463, abs=1e-3)


def test_max_attainable_psr_at_t_211_crosses_0_95():
    # T=211 ist die im Issue genannte Grenze ("T>=211 nötig") — bei float-Praezision liegt der
    # Wert auf 4 Nachkommastellen bei 0.9500, minimal (1.9e-5) unterhalb der reinen >= 0.95-Grenze.
    assert round(max_attainable_psr(211), 4) == 0.9500
    assert max_attainable_psr(210) < 0.95


def test_max_attainable_psr_at_t_258_comfortably_passes():
    assert max_attainable_psr(258) >= 0.95


def test_max_attainable_psr_none_below_two_periods():
    assert max_attainable_psr(1) is None


# --- check_promotion_confidence_reachability: T=202 FAIL, T=258 PASS gegen Konfidenz 0.95 -------

def test_t_202_fails_against_confidence_0_95():
    result = check_promotion_confidence_reachability(202, 0.95)
    assert result.passed is False
    assert result.severity == "blocking"


def test_t_258_passes_against_confidence_0_95():
    result = check_promotion_confidence_reachability(258, 0.95)
    assert result.passed is True


def test_missing_inputs_are_inconclusive_not_fail():
    result = check_promotion_confidence_reachability(None, 0.95)
    assert result.passed is None
    assert result.inconclusive is True

    result2 = check_promotion_confidence_reachability(300, None)
    assert result2.passed is None


def test_actual_field_carries_the_full_computation_for_provenance():
    result = check_promotion_confidence_reachability(258, 0.95)
    assert result.actual["t_holdout"] == 258
    assert result.actual["promotion_confidence"] == 0.95
    assert "max_attainable_psr" in result.actual


# --- compute_holdout_bar_count: T_holdout aus der tatsaechlichen Bar-Achse ----------------------

def test_compute_holdout_bar_count_equity_uses_seven_bins_per_trading_day():
    session = {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}
    t = compute_holdout_bar_count(60, session, "EQUITY")
    assert t == round(60 * (5.0 / 7.0) * 7)


def test_compute_holdout_bar_count_crypto_uses_24_bars_per_calendar_day():
    t = compute_holdout_bar_count(60, {"EQUITY": {"open_utc": "13:30", "close_utc": "20:00"}}, None)
    assert t == 60 * 24


def test_current_backtest_json_holdout_days_reaches_the_confidence_threshold():
    """Regressionsschutz: die #1340-Konfigentscheidung (holdout_days=60) muss tatsaechlich
    max_attainable_psr >= deflation_confidence liefern — sonst waere die Config-Aenderung
    wirkungslos."""
    import json
    from pathlib import Path

    backtest_cfg = json.loads(Path("automation/config/backtest.json").read_text("utf-8"))
    tournament_cfg = json.loads(Path("automation/config/tournament.json").read_text("utf-8"))
    holdout_days = backtest_cfg["walk_forward"]["holdout_days"]
    confidence = tournament_cfg["deflation_confidence"]
    session = backtest_cfg.get("session_hours_by_asset_class")

    t_holdout = compute_holdout_bar_count(holdout_days, session, "EQUITY")
    result = check_promotion_confidence_reachability(t_holdout, confidence)
    assert result.passed is True, (
        f"backtest.json['walk_forward']['holdout_days']={holdout_days} liefert T={t_holdout}, "
        f"max_attainable_psr < deflation_confidence={confidence} — die #1340-Resolution ist "
        f"nicht (mehr) wirksam.")
