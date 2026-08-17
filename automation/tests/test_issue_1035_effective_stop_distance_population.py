"""Issue #1035 (Katalog #866) — ``check_effective_stop_distance`` mass bislang ``oos_gross_loss_
mean_bps`` (Mittel ueber ALLE Verlust-Trades) gegen den konfigurierten Stop-Abstand, obwohl bei
ueberwiegend UNKNOWN-/TIME_BOX-Exits (vor #1034) der Stop den grossen Teil der Trades nie beruehrt
hatte — die falsche Grundgesamtheit (bestaetigt Hypothese (a) aus #1008). Der Zaehler ist jetzt
``oos_gross_loss_mean_bps_trailing_stop`` (NUR nachweisliche TRAILING_STOP-Exits, #1034
Voraussetzung); mit weniger als 30 Stop-Exits ist das Urteil INCONCLUSIVE statt FAIL.
"""
from automation.optimizer import invariants as inv


def _record(**kw):
    base = {"strategy": "FlashCrashReversalStrategy", "symbol": "TSLA.ETORO"}
    base.update(kw)
    return base


def test_passes_when_trailing_stop_loss_matches_configured_distance():
    records = [_record(
        atr_median_bps=20.0, atr_trailing_multiplier_median=2.0,
        oos_gross_loss_mean_bps_trailing_stop=16.0, oos_n_trailing_stop_losses=50,
        oos_gross_loss_mean_bps_trailing_stop_pooled=16.0,
        # Issue #972/#1126 — check_effective_stop_distance konsumiert seither die robuste
        # Median-Variante als primaeren Zaehler (Pitfall #405 in AGENTS.md).
        gross_loss_median_bps_trailing_stop=16.0,
    )]
    result = inv.check_effective_stop_distance(records)
    assert result.passed is True
    assert result.inconclusive is False


def test_fails_when_trailing_stop_loss_is_far_below_configured_distance():
    """configured_distance = 2.0 * 20.0 = 40; ratio = 3.6/40 = 0.09 < 0.4 (die tatsaechliche
    #1008-Beobachtung, jetzt aber auf der RICHTIGEN Grundgesamtheit gemessen)."""
    records = [_record(
        atr_median_bps=20.0, atr_trailing_multiplier_median=2.0,
        oos_gross_loss_mean_bps_trailing_stop=3.6, oos_n_trailing_stop_losses=50,
        oos_gross_loss_mean_bps_trailing_stop_pooled=3.6,
        gross_loss_median_bps_trailing_stop=3.6,
    )]
    result = inv.check_effective_stop_distance(records)
    assert result.passed is False
    assert "FlashCrashReversalStrategy/TSLA.ETORO" in result.actual


def test_inconclusive_below_minimum_stop_exit_sample_size():
    """Weniger als 30 nachweisliche Stop-Exits — die Stichprobe ist zu klein fuer ein Urteil,
    selbst wenn das Verhaeltnis dem Anschein nach unter der Schwelle liegt."""
    records = [_record(
        atr_median_bps=20.0, atr_trailing_multiplier_median=2.0,
        oos_gross_loss_mean_bps_trailing_stop=3.6, oos_n_trailing_stop_losses=5,
        oos_gross_loss_mean_bps_trailing_stop_pooled=3.6,
        gross_loss_median_bps_trailing_stop=3.6,
    )]
    result = inv.check_effective_stop_distance(records)
    assert result.passed is True
    assert result.inconclusive is True


def test_inconclusive_when_no_trailing_stop_telemetry_at_all():
    """Legacy-Report ohne #1034/#1035-Telemetrie (nur das ungefilterte oos_gross_loss_mean_bps) —
    die Grundgesamtheit ist unbekannt, kein Urteil auf einer moeglicherweise falschen Zahl."""
    records = [_record(
        atr_median_bps=20.0, atr_trailing_multiplier_median=2.0,
        oos_gross_loss_mean_bps=3.6, oos_gross_loss_mean_bps_pooled=3.6,
    )]
    result = inv.check_effective_stop_distance(records)
    assert result.passed is True
    assert result.inconclusive is True


def test_not_applicable_without_any_exit_telemetry():
    result = inv.check_effective_stop_distance([_record()])
    assert result.passed is True
    assert result.actual is None
    assert result.inconclusive is False
