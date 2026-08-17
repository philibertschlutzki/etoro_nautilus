"""Issue #1093 (Katalog #926) — ``check_trailing_stop_loss_share`` erkennt, wenn der Trailing-Stop
der haeufigste UND verlustreichste UND teuerste Ausgang einer Study ist (die Signatur des
#1092/#1094-Defekts), statt es unentdeckt zu lassen.
"""
from automation.optimizer import invariants as inv


def _record(n_ts_exits: int, n_ts_losses: int, mean_loss_ts: float | None = None,
           mean_loss_all: float | None = None) -> dict:
    return {
        "strategy": "Rsi2Reversion", "symbol": "TSLA.ETORO",
        "exit_reason_histogram": {"TRAILING_STOP": n_ts_exits, "TIME_BOX": 5},
        "oos_n_trailing_stop_losses": n_ts_losses,
        "oos_gross_loss_mean_bps_trailing_stop": mean_loss_ts,
        # Issue #972/#1126 — Bedingung 2 konsumiert seit diesem Fix die robuste Median-Variante
        # (Pitfall #405 in AGENTS.md); fuer diese Fixtures identisch zum Mittel oben.
        "gross_loss_median_bps_trailing_stop": mean_loss_ts,
        "oos_gross_loss_mean_bps": mean_loss_all,
    }


def test_healthy_trailing_stop_passes():
    r = _record(100, 50, mean_loss_ts=50.0, mean_loss_all=50.0)
    result = inv.check_trailing_stop_loss_share([r])
    assert result.passed is True


def test_high_loss_share_fails_matching_1092_reference_symptom():
    """Referenzlauf: Median-Verlustquote 83,6% ueber 54 Studies."""
    r = _record(100, 84, mean_loss_ts=50.0, mean_loss_all=50.0)
    result = inv.check_trailing_stop_loss_share([r])
    assert result.passed is False
    assert result.severity == "blocking"
    assert "Rsi2Reversion/TSLA.ETORO" in result.actual
    assert result.actual["Rsi2Reversion/TSLA.ETORO"]["loss_share"] == 0.84


def test_high_mean_loss_ratio_fails_matching_1092_reference_symptom():
    """Referenzlauf: mittlerer Verlust 2,26x des Durchschnitts aller Ausgaenge."""
    r = _record(100, 50, mean_loss_ts=113.0, mean_loss_all=50.0)
    result = inv.check_trailing_stop_loss_share([r])
    assert result.passed is False
    assert result.actual["Rsi2Reversion/TSLA.ETORO"]["mean_loss_ratio"] == 2.26


def test_custom_thresholds_from_optimizer_json():
    r = _record(100, 55, mean_loss_ts=50.0, mean_loss_all=50.0)
    assert inv.check_trailing_stop_loss_share([r]).passed is True
    assert inv.check_trailing_stop_loss_share([r], max_loss_share=0.50).passed is False


def test_no_trailing_stop_exits_is_not_applicable():
    r = {"strategy": "A", "symbol": "B", "exit_reason_histogram": {"TIME_BOX": 10},
         "oos_n_trailing_stop_losses": 0}
    assert inv.check_trailing_stop_loss_share([r]).passed is True


def test_missing_telemetry_is_fail_open():
    r = {"strategy": "A", "symbol": "B", "exit_reason_histogram": {"TRAILING_STOP": 10}}
    assert inv.check_trailing_stop_loss_share([r]).passed is True
