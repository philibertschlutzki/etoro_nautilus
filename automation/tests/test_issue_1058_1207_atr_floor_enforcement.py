"""Issue #1058/#1207 (P1, Katalog #1196-1221) — ATR-Floor gebunden und Stop-Kosten-Verhältnis
2,31 widersprechen sich.

Symptom: ``SqueezeBreakout/ASML`` stand unter den ATR-Floor-gebundenen Studies (§5.3), aber
``check_stop_cost_ratio`` meldete fuer dieselbe Study 2,3105 < 3,0 — obwohl der kostengekoppelte
Floor (``backtest_runner.cost_coupled_atr_floor_bps``) konstruktiv >= 3 · c_rt garantiert, WENN er
tatsaechlich bindet.

Fix: neue Invariante ``invariants.check_atr_floor_enforcement`` — fuer jede floor-gebundene Study
(dieselbe Erkennung wie ``check_atr_scale_homogeneity``) muss
``effective_stop_distance_bps >= min_stop_to_cost_ratio · c_rt - 1e-6`` gelten; FAIL benennt die
Study mit allen vier Zahlen (``atr_floor_bps_derived``, ``k``, ``effective_stop_distance_bps``,
``c_rt``)."""
from automation.optimizer import invariants as inv


def _study(strategy, symbol, *, atr_raw, atr_median, floor, k, c_rt):
    # Issue #1081/#1229 — umbenannt von ``stop_distance_bps`` zu ``stop_distance_bps_modelled``.
    stop_distance_bps_modelled = atr_median * k
    return {
        "strategy": strategy, "symbol": symbol,
        "atr_raw_median_bps": atr_raw,
        "atr_median_bps": atr_median,
        "atr_floor_bps_derived": floor,
        "atr_trailing_multiplier_median": k,
        "stop_distance_bps_modelled": stop_distance_bps_modelled,
        "round_trip_cost_bps": c_rt,
    }


def test_inconclusive_without_any_floor_binding_study():
    # atr_raw >= floor => nicht floor-gebunden.
    # Issue #1309 (GH #1186, P1) — Tri-State-Praezisierung: "nicht auswertbar" ist KEIN PASS mehr.
    result = inv.check_atr_floor_enforcement(
        [_study("A", "X.ETORO", atr_raw=50.0, atr_median=50.0, floor=10.0, k=2.0, c_rt=3.0)])
    assert result.passed is None
    assert result.inconclusive is True
    assert result.evaluable is False


def test_reproduces_b6_contradiction_floor_binding_but_ratio_below_threshold():
    """Reproduziert die B-6-Signatur: floor-gebunden (atr_raw < floor), aber der SIMULIERTE
    effektive Stop (atr_median * k) liegt trotzdem unter 3x c_rt -- die Floor-Garantie wurde nicht
    wirksam durchgesetzt."""
    result = inv.check_atr_floor_enforcement([
        _study("SqueezeBreakout", "ASML.ETORO", atr_raw=5.0, atr_median=7.0, floor=10.0,
               k=1.0, c_rt=3.0),  # effective_stop_distance_bps = 7.0 < 3*3.0=9.0
    ])
    assert result.passed is False
    assert result.severity == "high"
    offender = result.actual["SqueezeBreakout/ASML.ETORO"]
    assert offender["atr_floor_bps_derived"] == 10.0
    assert offender["k"] == 1.0
    assert offender["effective_stop_distance_bps"] == 7.0
    assert offender["c_rt"] == 3.0


def test_passes_when_floor_binding_and_ratio_holds():
    result = inv.check_atr_floor_enforcement([
        _study("A", "X.ETORO", atr_raw=5.0, atr_median=10.0, floor=10.0, k=1.0, c_rt=3.0),
        # effective_stop_distance_bps = 10.0 >= 3*3.0=9.0
    ])
    assert result.passed is True
    assert result.evaluable is True
    assert result.evaluability["n_studies_measured"] == 1


def test_non_binding_study_is_not_checked_even_with_low_ratio():
    """Eine Study, die NICHT floor-gebunden ist, darf niemals als Offender erscheinen, selbst wenn
    ihr Verhaeltnis < 3.0 ist -- das ist die Zustaendigkeit von check_stop_cost_ratio, nicht dieses
    Checks."""
    # Issue #1309 (GH #1186, P1) — Tri-State-Praezisierung: "nicht auswertbar" ist KEIN PASS mehr.
    result = inv.check_atr_floor_enforcement([
        _study("A", "X.ETORO", atr_raw=50.0, atr_median=1.0, floor=10.0, k=1.0, c_rt=3.0),
    ])
    assert result.passed is None
    assert result.inconclusive is True


def test_legacy_equality_criterion_used_without_atr_raw_median_bps():
    study = _study("A", "X.ETORO", atr_raw=None, atr_median=10.0, floor=10.0, k=1.0, c_rt=4.0)
    del study["atr_raw_median_bps"]
    result = inv.check_atr_floor_enforcement([study])
    assert result.evaluable is True
    assert result.passed is False  # 10.0 < 3*4.0=12.0
