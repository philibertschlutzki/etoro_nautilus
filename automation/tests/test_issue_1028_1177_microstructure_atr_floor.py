"""Issue #1028/#1177 (P1) — der ATR-Floor ist rein kostengekoppelt und ignoriert die Bar-Spanne.

Fix: eine zweite, unabhängige Untergrenze (``atr_floor_bps_microstructure = k_min_bar_range_
multiple · bar_range_median_bps / atr_trailing_multiplier``) wird zusätzlich zum bereits
bestehenden, kostengekoppelten Floor (``atr_floor_bps_derived``, #1096) berechnet und als
``atr_floor_bps_recommended``/``atr_floor_source`` je Study ausgewiesen — bewusst NUR diagnostisch
(``bar_range_median_bps`` ist ein retrospektiver Round-Trip-Aggregat, zum Zeitpunkt der Stop-
Platzierung eines neuen Trials nicht verfügbar; die Simulation selbst bleibt unverändert).
"""
from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


def test_k_min_bar_range_multiple_defaults_to_one(tmp_path):
    assert rpt._k_min_bar_range_multiple(tmp_path) == 1.0


def test_microstructure_floor_binds_when_bar_range_exceeds_the_cost_floor():
    studies_out = [{
        "symbol": "TSLA.ETORO", "strategy": "DynamicBreakoutStrategy",
        "atr_trailing_multiplier_median": 1.5,
        "bar_range_median_bps": 30.0,  # -> microstructure floor = 1.0 * 30.0 / 1.5 = 20.0
    }]
    rpt._stamp_atr_floor_bps_derived(
        studies_out,
        atr_floor_bps_by_symbol={"TSLA.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"TSLA.ETORO": 4.0},  # cost floor ~ 3*4/1.5 = 8.0
        min_stop_to_cost_ratio=3.0,
        k_min_bar_range_multiple=1.0,
    )
    r = studies_out[0]
    assert r["atr_floor_bps_derived"] == 8.0
    assert r["atr_floor_bps_microstructure"] == 20.0
    assert r["atr_floor_bps_recommended"] == 20.0
    assert r["atr_floor_source"] == "bar_range"


def test_cost_floor_binds_when_it_exceeds_the_microstructure_floor():
    studies_out = [{
        "symbol": "TSLA.ETORO", "strategy": "A",
        "atr_trailing_multiplier_median": 1.5,
        "bar_range_median_bps": 5.0,  # -> microstructure floor = 1.0 * 5.0 / 1.5 = 3.33
    }]
    rpt._stamp_atr_floor_bps_derived(
        studies_out,
        atr_floor_bps_by_symbol={"TSLA.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"TSLA.ETORO": 4.0},  # cost floor = 8.0
        min_stop_to_cost_ratio=3.0,
    )
    r = studies_out[0]
    assert r["atr_floor_bps_derived"] == 8.0
    assert r["atr_floor_bps_recommended"] == 8.0
    assert r["atr_floor_source"] == "cost"


def test_atr_floor_bps_derived_itself_is_unchanged_by_the_microstructure_extension():
    """atr_floor_bps_derived bleibt EXAKT der tatsaechlich simulierte Floor (kein stiller
    Bedeutungswechsel eines bestehenden Feldes)."""
    studies_out = [{
        "symbol": "TSLA.ETORO", "strategy": "A",
        "atr_trailing_multiplier_median": 1.5,
        "bar_range_median_bps": 500.0,  # microstructure floor dominiert klar
    }]
    rpt._stamp_atr_floor_bps_derived(
        studies_out,
        atr_floor_bps_by_symbol={"TSLA.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"TSLA.ETORO": 4.0},
        min_stop_to_cost_ratio=3.0,
    )
    assert studies_out[0]["atr_floor_bps_derived"] == 8.0  # unveraendert der Cost-Floor


def test_source_is_none_when_neither_floor_is_computable():
    studies_out = [{"symbol": "X.ETORO", "strategy": "A"}]
    rpt._stamp_atr_floor_bps_derived(
        studies_out, atr_floor_bps_by_symbol={}, round_trip_cost_bps_by_symbol={},
        min_stop_to_cost_ratio=3.0)
    assert studies_out[0]["atr_floor_source"] == "none"
    assert studies_out[0]["atr_floor_bps_recommended"] is None


def test_check_stop_distance_microstructure_floor_fails_when_stop_is_inside_bar_noise():
    r = {"strategy": "A", "symbol": "B.ETORO", "stop_distance_bps": 9.22,
        "bar_range_median_bps": 50.16}
    result = inv.check_stop_distance_microstructure_floor([r])
    assert result.passed is False
    assert result.severity == "high"
    assert result.actual["A/B.ETORO"]["stop_distance_bps"] == 9.22


def test_check_stop_distance_microstructure_floor_passes_when_stop_exceeds_bar_noise():
    r = {"strategy": "A", "symbol": "B.ETORO", "stop_distance_bps": 60.0,
        "bar_range_median_bps": 50.16}
    result = inv.check_stop_distance_microstructure_floor([r])
    assert result.passed is True


def test_check_stop_distance_microstructure_floor_skips_studies_missing_either_field():
    result = inv.check_stop_distance_microstructure_floor([{"strategy": "A", "symbol": "B.ETORO"}])
    assert result.passed is True
    assert result.actual is None
