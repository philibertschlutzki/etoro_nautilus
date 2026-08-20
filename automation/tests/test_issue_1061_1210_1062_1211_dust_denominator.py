"""Issue #1061/#1210 (Dust-Round-Trips ausschliessen) und #1062/#1211 (35%-Vorbedingung auf
bereinigtem Nenner) — Katalog #1196-1221.

Investigations-Ergebnis: ``backtest_runner._filter_dust_round_trips`` (seit #946/#1112) verwirft
Dust-Round-Trips bereits AN DER QUELLE (VOR jeder IS/OOS-/Fold-Aufteilung) — Win-Rate, Expectancy,
Exit-Reason-Histogramm, ``oos_total_trades_with_exit_telemetry`` und Haltedauer-Perzentile lesen
danach ausschliesslich die vier gefilterten Listen ueber den Namen (kein Index von vorher bleibt in
Gebrauch). Empirisch bestaetigt gegen die Referenzlaeufe: ``dust_round_trips_filtered`` traegt reale,
studyspezifische Werte (z. B. Rsi2ReversionStrategy/GOOGL.ETORO: 4913/37997 = 12,93 % — exakt der im
Issue zitierte Wert), UND ``check_trailing_stop_risk_calibration_acceptance``s 35%-Vorbedingung
konsumierte bereits VOR diesem Fix ``oos_total_trades_with_exit_telemetry`` — den BEREINIGTEN
(post-Dust-Filter) Nenner, nicht den kontaminierten. Der #1211-Fix macht diese Bereinigung im
Detailtext NACHVOLLZIEHBAR (Anteil vor/nach Bereinigung + ausgeschlossene Zahl), statt sie
stillschweigend vorauszusetzen."""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, *, k_atr_mult, atr_bps, loss_bps, n_ts_losses,
           trailing_stop_count, total_exits_clean, dust_excluded=0):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_trailing_multiplier_median": k_atr_mult, "atr_median_bps": atr_bps,
        "gross_loss_median_bps_trailing_stop": loss_bps,
        "oos_n_trailing_stop_losses": n_ts_losses,
        "exit_reason_histogram": {"TRAILING_STOP": trailing_stop_count},
        "oos_total_trades_with_exit_telemetry": total_exits_clean,
        "dust_round_trips_filtered": dust_excluded,
        # Issue #1056/#1205 — dieses Testmodul prueft NUR die Dust-Denominator-Mechanik von
        # Kriterium 3 (TRAILING_STOP-Anteil), nicht Kriterium 1 (Spearman); ein sauberer,
        # ausreichend grosser WITHIN-STUDY-Kalibrierungswert haelt Kriterium 1 hier neutral (PASS),
        # damit diese Tests wie vor #1056 die FINALE Verdikt-Verzweigung erreichen statt in die neue
        # (n < 8)-INCONCLUSIVE-Verzweigung zu fallen.
        "stop_calibration_spearman_within_study": 1.0,
        "stop_calibration_n_pairs_within_study": 10,
    }


def test_trailing_stop_share_uses_the_dust_cleaned_denominator():
    """oos_total_trades_with_exit_telemetry ist bereits der bereinigte Nenner -- der Anteil darf
    NICHT ueber (clean + dust) gebildet werden."""
    result = inv.check_trailing_stop_risk_calibration_acceptance([
        _study("A", "X.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=30, total_exits_clean=100, dust_excluded=50),
    ])
    # 30/100 = 0.30 (bereinigt), NICHT 30/150 = 0.20 (kontaminiert).
    assert result.actual["trailing_stop_exit_share"] == 0.3


def test_detail_shows_share_before_and_after_dust_cleanup_with_excluded_count():
    """Issue #1062/#1211 Akzeptanzkriterium — Detailtext enthaelt den Anteil VOR und NACH
    Bereinigung sowie die Zahl der ausgeschlossenen Round-Trips, wenn die 35%-Schwelle FAILt.
    Braucht >= 3 Studies mit hinreichender TRAILING_STOP-Evidenz, sonst INCONCLUSIVE."""
    result = inv.check_trailing_stop_risk_calibration_acceptance([
        _study("A", "X.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=40, total_exits_clean=100, dust_excluded=50),
        _study("B", "Y.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=0, total_exits_clean=1, dust_excluded=0),
        _study("C", "Z.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=0, total_exits_clean=1, dust_excluded=0),
    ])
    assert result.passed is False
    assert result.actual["trailing_stop_exit_share"] == round(40 / 102, 4)  # bereinigt
    assert result.actual["trailing_stop_exit_share_raw_before_dust_cleanup"] == round(40 / 152, 4)
    assert result.actual["n_dust_round_trips_excluded"] == 50
    assert "bereinigter Nenner" in result.detail
    assert "vor Dust-Bereinigung" in result.detail


def test_raw_share_is_none_when_no_studies_carry_exit_telemetry():
    result = inv.check_trailing_stop_risk_calibration_acceptance([])
    assert result.actual is None or result.actual.get(
        "trailing_stop_exit_share_raw_before_dust_cleanup") is None


def test_zero_dust_excluded_makes_raw_and_clean_shares_identical():
    result = inv.check_trailing_stop_risk_calibration_acceptance([
        _study("A", "X.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=10, total_exits_clean=100, dust_excluded=0),
        _study("B", "Y.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=0, total_exits_clean=1, dust_excluded=0),
        _study("C", "Z.ETORO", k_atr_mult=2.0, atr_bps=10.0, loss_bps=15.0, n_ts_losses=40,
               trailing_stop_count=0, total_exits_clean=1, dust_excluded=0),
    ])
    assert (result.actual["trailing_stop_exit_share"]
            == result.actual["trailing_stop_exit_share_raw_before_dust_cleanup"])
    assert result.actual["n_dust_round_trips_excluded"] == 0


# --- backtest_runner._filter_dust_round_trips: die Quelle der Bereinigung selbst -------------------
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

import automation.backtest_runner as br


def test_filter_dust_round_trips_excludes_below_floor_and_reports_discarded():
    pnls = [(-1.0, 100), (5.0, 200), (0.001, 300)]
    notionals = [(1000.0, 100), (1200.0, 200), (0.5, 300)]  # 0.5 << 5% of median(~1100)
    peaks = [1000.0, 1200.0, 0.5]
    meta = [{"exit_reason": "TRAILING_STOP"}, {"exit_reason": "TIME_BOX"},
            {"exit_reason": "UNKNOWN"}]
    kept_pnls, kept_notionals, kept_peaks, kept_meta, discarded, discarded_meta = (
        br._filter_dust_round_trips(pnls, notionals, peaks, meta))
    assert len(kept_pnls) == 2
    assert len(discarded) == 1
    assert discarded[0][0] == 0.5
    assert discarded_meta[0]["exit_reason"] == "UNKNOWN"
    # Alle vier gefilterten Listen bleiben index-parallel und gleich lang.
    assert len(kept_pnls) == len(kept_notionals) == len(kept_peaks) == len(kept_meta)
