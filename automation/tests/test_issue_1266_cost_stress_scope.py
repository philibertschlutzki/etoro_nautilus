"""Issue #1266 (GH #1136) — Kostenstress je Study statt symbolweiter Konstante.

Symptom. Δ(1,5×−1×) = Δ(2×−1×) = −0,0040700 und Δ(full−2×) = −0,0070182 bit-identisch in 13/13
Studies. slippage_p50_bps_calibrated war in 14/14 identisch, obwohl die gemessene Slippage je
Study zwischen 15,63 und 168,82 bps lag (Faktor 10,8).

Root-Cause. Die Stufengrösse war additiv und STRATEGIE-UNABHÄNGIG (ein symbolweiter/asset-class-
weiter Pool-Median) — ein additiver Term kann keine Strategie gegen eine andere stressen.

Fix.
1. ``sweep.calibrate_and_write_slippage_cache`` sammelt zusätzlich zur asset-class-weiten
   Kalibrierung je (Asset-Klasse, Symbol) und (Asset-Klasse, Strategie, Symbol).
2. ``backtest_runner.resolve_slippage_bps``/``resolve_slippage_calibration_scope`` lösen die
   feinste verfügbare Ebene mit ausreichender Stichprobe auf (Strategie+Symbol > Symbol >
   Asset-Klasse), mit ``slippage_calibration_scope``-Telemetrie.
3. Neuer Check ``invariants.check_cost_stress_discriminates`` (severity ``high``).
"""
from automation.backtest_runner import (
    calibrate_slippage_bps_by_asset_class,
    merge_calibrated_slippage_into_config_scoped,
    resolve_slippage_bps,
    resolve_slippage_calibration_scope,
)
from automation.optimizer import invariants as inv


# ── resolve_slippage_bps: rückwärtskompatible Erweiterung ───────────────────────────────────────
def test_resolve_slippage_bps_plain_scalar_is_bit_identical_to_pre_1266():
    assert resolve_slippage_bps("TSLA.ETORO", {"EQUITY": 78.4052}, "EQUITY") == 78.4052


def test_resolve_slippage_bps_missing_asset_class_is_zero():
    assert resolve_slippage_bps("TSLA.ETORO", {"EQUITY": 78.4052}, "COMMODITY") == 0.0


def test_resolve_slippage_bps_falls_back_to_asset_class_value_without_scoped_data():
    entry = {"EQUITY": {"value": 78.4052, "by_symbol": {}, "by_strategy_symbol": {}}}
    assert resolve_slippage_bps("TSLA.ETORO", entry, "EQUITY", strategy="Donchian") == 78.4052
    assert resolve_slippage_calibration_scope(
        "TSLA.ETORO", entry, "EQUITY", strategy="Donchian") == "asset_class"


def test_resolve_slippage_bps_prefers_symbol_over_asset_class():
    entry = {"EQUITY": {
        "value": 78.4052,
        "by_symbol": {"TSLA.ETORO": {"value": 56.81, "n_observations": 40}},
        "by_strategy_symbol": {},
    }}
    assert resolve_slippage_bps("TSLA.ETORO", entry, "EQUITY") == 56.81
    assert resolve_slippage_calibration_scope("TSLA.ETORO", entry, "EQUITY") == "symbol"


def test_resolve_slippage_bps_prefers_strategy_symbol_over_symbol():
    entry = {"EQUITY": {
        "value": 78.4052,
        "by_symbol": {"TSLA.ETORO": {"value": 56.81, "n_observations": 40}},
        "by_strategy_symbol": {"Donchian|TSLA.ETORO": {"value": 15.63, "n_observations": 35}},
    }}
    assert resolve_slippage_bps(
        "TSLA.ETORO", entry, "EQUITY", strategy="Donchian") == 15.63
    assert resolve_slippage_calibration_scope(
        "TSLA.ETORO", entry, "EQUITY", strategy="Donchian") == "strategy_symbol"


def test_resolve_slippage_bps_ignores_scoped_data_below_min_observations():
    entry = {"EQUITY": {
        "value": 78.4052,
        "by_symbol": {"TSLA.ETORO": {"value": 56.81, "n_observations": 5}},
        "by_strategy_symbol": {"Donchian|TSLA.ETORO": {"value": 15.63, "n_observations": 3}},
    }}
    assert resolve_slippage_bps(
        "TSLA.ETORO", entry, "EQUITY", strategy="Donchian", min_observations=30) == 78.4052
    assert resolve_slippage_calibration_scope(
        "TSLA.ETORO", entry, "EQUITY", strategy="Donchian", min_observations=30) == "asset_class"


def test_resolve_slippage_bps_different_strategies_get_different_values():
    """Kern-Akzeptanzkriterium: zwei Strategien auf demselben Symbol erhalten unterschiedliche
    Kostenbasen, sobald beide genug eigene Beobachtungen haben — der Stress kann diskriminieren."""
    entry = {"EQUITY": {
        "value": 78.4052,
        "by_symbol": {},
        "by_strategy_symbol": {
            "Donchian|TSLA.ETORO": {"value": 15.63, "n_observations": 40},
            "OpeningRange|TSLA.ETORO": {"value": 168.82, "n_observations": 40},
        },
    }}
    donchian = resolve_slippage_bps("TSLA.ETORO", entry, "EQUITY", strategy="Donchian")
    opening_range = resolve_slippage_bps("TSLA.ETORO", entry, "EQUITY", strategy="OpeningRange")
    assert donchian != opening_range
    assert donchian == 15.63
    assert opening_range == 168.82


# ── merge_calibrated_slippage_into_config_scoped ─────────────────────────────────────────────────
def test_merge_scoped_builds_nested_structure_from_calibration_cache():
    calibration = {
        "EQUITY": {
            "p50": 78.4052, "p90": 151.5869, "n_observations": 400,
            "by_symbol": {"TSLA.ETORO": {"p50": 56.81, "p90": 120.0, "n_observations": 40}},
            "by_strategy_symbol": {
                "Donchian|TSLA.ETORO": {"p50": 15.63, "p90": 30.0, "n_observations": 35}},
        },
    }
    merged = merge_calibrated_slippage_into_config_scoped({}, calibration, percentile="p50")
    assert merged["EQUITY"]["value"] == 78.4052
    assert merged["EQUITY"]["by_symbol"]["TSLA.ETORO"]["value"] == 56.81
    assert merged["EQUITY"]["by_strategy_symbol"]["Donchian|TSLA.ETORO"]["value"] == 15.63
    assert merged["EQUITY"]["by_strategy_symbol"]["Donchian|TSLA.ETORO"]["n_observations"] == 35


def test_merge_scoped_operator_override_wins_and_drops_scoped_levels():
    """Ein explizit gesetzter, nicht-null statischer Wert übersteuert JEDE Kalibrierungsebene —
    identisch zur Vor-#1266-Regel (merge_calibrated_slippage_into_config)."""
    calibration = {"EQUITY": {"p50": 78.4052, "n_observations": 400}}
    merged = merge_calibrated_slippage_into_config_scoped(
        {"EQUITY": 2.0}, calibration, percentile="p50")
    assert merged["EQUITY"] == 2.0


# ── calibrate_and_write_slippage_cache: Beobachtungssammlung ─────────────────────────────────────
def test_calibrate_slippage_bps_by_asset_class_is_reused_for_any_flat_namespace():
    """calibrate_and_write_slippage_cache ruft dieselbe reine Funktion dreifach mit verschiedenen
    Namensraeumen auf ('||'-kodierte Keys) statt einer neuen Implementierung."""
    result = calibrate_slippage_bps_by_asset_class({
        "EQUITY||Donchian||TSLA.ETORO": [10.0, 20.0, 30.0],
    })
    assert result["EQUITY||Donchian||TSLA.ETORO"]["p50"] == 20.0
    assert result["EQUITY||Donchian||TSLA.ETORO"]["n_observations"] == 3


# ── invariants.check_cost_stress_discriminates ───────────────────────────────────────────────────
def _study(strategy, symbol, *, baseline, stress_2x):
    return {"strategy": strategy, "symbol": symbol,
           "holdout_expectancy_capital_weighted": baseline,
           "holdout_expectancy_cost_stress_2x": stress_2x}


def test_identical_delta_across_studies_of_a_symbol_fails():
    result = inv.check_cost_stress_discriminates([
        _study("A", "TSLA.ETORO", baseline=0.01, stress_2x=0.01 - 0.00407),
        _study("B", "TSLA.ETORO", baseline=0.02, stress_2x=0.02 - 0.00407),
        _study("C", "TSLA.ETORO", baseline=-0.01, stress_2x=-0.01 - 0.00407),
    ])
    assert result.passed is False
    assert "TSLA.ETORO" in result.actual


def test_varying_delta_across_studies_of_a_symbol_passes():
    result = inv.check_cost_stress_discriminates([
        _study("A", "TSLA.ETORO", baseline=0.01, stress_2x=0.01 - 0.0016),
        _study("B", "TSLA.ETORO", baseline=0.02, stress_2x=0.02 - 0.0169),
        _study("C", "TSLA.ETORO", baseline=-0.01, stress_2x=-0.01 - 0.0088),
    ])
    assert result.passed is True


def test_single_study_symbol_is_skipped_not_a_pass_or_fail_offender():
    result = inv.check_cost_stress_discriminates([
        _study("A", "TSLA.ETORO", baseline=0.01, stress_2x=0.01 - 0.00407),
        _study("A", "PLTR.ETORO", baseline=0.01, stress_2x=0.01 - 0.0016),
        _study("B", "PLTR.ETORO", baseline=0.02, stress_2x=0.02 - 0.0169),
    ])
    # PLTR.ETORO hat 2 Studies mit variierendem Delta -> PASS; TSLA.ETORO hat nur 1 -> uebersprungen.
    assert result.passed is True
    assert "TSLA.ETORO" not in (result.actual or {})


def test_no_measurable_symbol_is_inconclusive():
    result = inv.check_cost_stress_discriminates([{"strategy": "A", "symbol": "X.ETORO"}])
    assert result.evaluable is False
    assert result.inconclusive is True
