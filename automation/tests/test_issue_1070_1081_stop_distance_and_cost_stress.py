"""Issue #1070/#1081 (Katalog #866-2, Kohorte C, Risikoschicht).

#1070 (Pitfall #369) — ``check_effective_stop_distance`` prüfte bislang NUR ``ratio < min_ratio``;
ein Verhältnis weit ÜBER dem konfigurierten Stop-Abstand (Beweis B-3 im #866-Katalog: bis 36,66)
blieb unsichtbar (``passed=True, actual=None``), obwohl der Check in
``optimizer.json['fail_fast_invariants']`` steht. Fix: eine zweite, symmetrische Schranke
(``max_ratio``) plus ``actual``, das IMMER alle gemessenen Verhältnisse trägt (nicht nur Offender).

#1081 — der Kostenstress (``backtest_runner._expectancy_cost_stress``) skalierte bislang nur die
Kommission, nicht die vollen Round-Trip-Kosten (Spread + Kommission). Fix (hier ohne
``nautilus_trader``-Abhängigkeit getestet): ``deployment_gate._clause_cost_stress`` und
``parsing.py`` bevorzugen den umbenannten Schlüssel ``expectancy_round_trip_cost_stress_2x``, mit
dem alten Namen als Fallback.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import deployment_gate as dg


# ── #1070: two-sided check_effective_stop_distance ───────────────────────────────────────────────
def _record(strategy, symbol, *, atr_bps, k, loss_bps, n_stop_exits=50):
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_median_bps": atr_bps, "atr_trailing_multiplier_median": k,
        "oos_gross_loss_mean_bps_trailing_stop": loss_bps,
        "oos_n_trailing_stop_losses": n_stop_exits,
        # Issue #1097 (Katalog #930) — der vormalige gepoolte Konsum; fuer diese Einzel-Trial-
        # Fixtures identisch zum medianbasierten Wert oben (keine echte Mehr-Trial-Poolung).
        "oos_gross_loss_mean_bps_trailing_stop_pooled": loss_bps,
        # Issue #972/#1126 — check_effective_stop_distance konsumiert seither die robuste Median-
        # Variante als primaeren Zaehler (Pitfall #405 in AGENTS.md).
        "gross_loss_median_bps_trailing_stop": loss_bps,
        # Issue #1081/#1229 (GitHub-Issue, nicht zu verwechseln mit der Katalog-Nummer im
        # Modul-Docstring) — der Legacy-Fallback konsumiert seither die GEMESSENE Distanz statt
        # k*ATR; fuer diese Fixtures bewusst identisch zu ``atr_bps * k`` gesetzt (bit-identische
        # Testerwartungen, kein Bedeutungswechsel dieser Testfaelle selbst).
        "stop_distance_bps_measured": atr_bps * k,
    }


def test_high_ratio_now_fails_and_is_visible_in_actual():
    # configured_distance = 2.0 * 2.0 = 4.0 bps; realized loss = 124.22 bps ⇒ ratio ≈ 31.06
    # (Beweis B-3 im #866-Katalog: DynamicBreakout, Verhältnis 36,66 auf denselben Grössenordnungen).
    records = [_record("DynamicBreakoutStrategy", "TSLA.ETORO", atr_bps=2.0, k=1.694, loss_bps=124.22)]
    result = inv.check_effective_stop_distance(records)
    assert result.passed is False
    assert result.actual is not None
    assert "DynamicBreakoutStrategy/TSLA.ETORO" in result.actual
    assert result.actual["DynamicBreakoutStrategy/TSLA.ETORO"]["ratio_median"] > 10.0


def test_actual_always_carries_all_measured_ratios_even_when_passing():
    records = [_record("S", "X", atr_bps=20.0, k=2.0, loss_bps=16.0)]  # ratio == 0.4 (min bound)
    result = inv.check_effective_stop_distance(records)
    assert result.passed is True
    # Issue #1042/#1191 — umbenannt von ``ratio_pooled_mean`` (Namenskollision mit report.py's
    # ``realized_stop_loss_ratio_mean_per_trial``, siehe dortiger Feldkommentar).
    assert result.actual == {
        "S/X": {"ratio_median": 0.4, "realized_stop_loss_ratio_mean_pooled": 0.4}}


def test_low_ratio_still_fails_as_before():
    records = [_record("S", "X", atr_bps=20.0, k=2.0, loss_bps=3.6)]  # ratio 0.09 < 0.4
    result = inv.check_effective_stop_distance(records)
    assert result.passed is False
    assert result.actual["S/X"]["ratio_median"] == 0.09


def test_custom_max_ratio_is_respected():
    records = [_record("S", "X", atr_bps=2.0, k=1.0, loss_bps=25.0)]  # ratio 12.5
    lenient = inv.check_effective_stop_distance(records, max_ratio=20.0)
    assert lenient.passed is True
    strict = inv.check_effective_stop_distance(records, max_ratio=10.0)
    assert strict.passed is False


def test_five_offenders_reference_scenario_from_the_866_catalog():
    """Beweis B-3: fuenf Studies mit atr_median_bps am/nahe dem Floor haben ein Verhaeltnis > 14;
    acht mit atr_median_bps >= 13.5 haben < 6 — perfekte Trennung am Floor."""
    high = [
        _record("MeanReversionStrategy", "TSLA.ETORO", atr_bps=6.115, k=1.067, loss_bps=94.02),
        _record("TrendPullbackStrategy", "TSLA.ETORO", atr_bps=4.189, k=1.122, loss_bps=92.73),
        _record("VwapExhaustionStrategy", "TSLA.ETORO", atr_bps=2.598, k=2.312, loss_bps=152.37),
        _record("HourlyMeanReversionStrategy", "TSLA.ETORO", atr_bps=2.361, k=1.470, loss_bps=99.70),
        _record("DynamicBreakoutStrategy", "TSLA.ETORO", atr_bps=2.000, k=1.694, loss_bps=124.22),
    ]
    low = [
        _record("AdxAtrMomentumStrategy", "TSLA.ETORO", atr_bps=13.502, k=1.163, loss_bps=89.11),
    ]
    result = inv.check_effective_stop_distance(high + low)
    assert result.passed is False
    offenders_high = {k: v for k, v in result.actual.items() if v["ratio_median"] > 10.0}
    assert len(offenders_high) == 5
    assert "AdxAtrMomentumStrategy/TSLA.ETORO" not in offenders_high


# ── #1081: deployment_gate._clause_cost_stress prefers the renamed key ──────────────────────────
def test_clause_cost_stress_prefers_renamed_key():
    record = {"expectancy_round_trip_cost_stress_2x": -0.5, "expectancy_cost_stress_2x": 0.5}
    assert dg._clause_cost_stress(record) is False


def test_clause_cost_stress_falls_back_to_legacy_key():
    record = {"expectancy_cost_stress_2x": 0.5}
    assert dg._clause_cost_stress(record) is True


def test_clause_cost_stress_none_when_neither_key_present():
    assert dg._clause_cost_stress({}) is None
    assert dg._clause_cost_stress(None) is None


# ── #1081: parsing.parse_tournament prefers the renamed oos_metrics key ─────────────────────────
def test_parse_tournament_prefers_renamed_round_trip_cost_stress_key(tmp_path):
    from automation.optimizer import parsing as prs
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_metrics": {
                "expectancy_round_trip_cost_stress_2x": -1.0,
                "expectancy_cost_stress_2x": 99.0,
            },
        },
    }
    path = tmp_path / "tournament_result.json"
    path.write_text(__import__("json").dumps(payload), "utf-8")
    metrics = prs.parse_tournament(path)
    assert metrics.oos_expectancy_cost_stress_2x == -1.0


def test_parse_tournament_falls_back_to_legacy_key_when_renamed_key_absent(tmp_path):
    from automation.optimizer import parsing as prs
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_metrics": {"expectancy_cost_stress_2x": 0.42},
        },
    }
    path = tmp_path / "tournament_result.json"
    path.write_text(__import__("json").dumps(payload), "utf-8")
    metrics = prs.parse_tournament(path)
    assert metrics.oos_expectancy_cost_stress_2x == 0.42
