"""Issue #971/#972 (Katalog B, P0 HEADLINE) — die Zeitbox-Messung, die den 143-Symbol-Lauf
``46cf5070`` nach 2 Symbolen abbrach, hat einen Bug im Nenner/Zähler, nicht in der Zeitbox selbst.

#971: ``check_holding_time_cap`` konsumierte die TRIAL-Ebene (``timebox_violation_fraction``) statt
der TRADE-(Round-Trip-)Ebene. Root-Cause: der #857-Fix stempelt ``oos_evaluated=False`` auf JEDEN
Trial mit mindestens einem zeitbox-verletzenden Round-Trip — TRIAL-weise zählt ein Trial mit 150
sauberen Trades und einem Ausreisser zu 100% "verletzend". Auf dem Referenzlauf reproduzierte die
TRIAL-Quote bit-genau ``(n_trials - evaluable_trials) / n_trials`` — eine Grösse ohne Bezug zur
Haltedauer (``hit_trade_cap``/``time_box_penalty`` waren im GESAMTEN Lauf 0, siehe #973).

Ein zweiter, eng verwandter Defekt: ``_classify_is_rejection_detail`` konnte einen zeitbox-
invalidierten Trial (der TATSÄCHLICH OOS gehandelt hat) nicht von einem echten IS-Gate-Drop
unterscheiden — beide erschienen als ``REJECT_OOS_DISCARDED_BY_IS_GATE``. Dieser Test-Modul deckt
die Korrektur ab: ein expliziter ``timebox_violated``-Parameter VOR der IS-Gate-Heuristik.

#972: der Zero-Eligible-Plateau-Zähler ("0/N Trials trafen die Haltedauer-/Trade-Cap-Grenze") lief
über die bereits gefilterten ÜBERLEBENDEN (``n_evaluated``) statt über ``n_trials`` — ein Zähler,
dessen Grundgesamtheit durch das zu messende Kriterium selbst vorgefiltert ist, liefert immer null
(Pitfall #304 in AGENTS.md). ``check_counter_partition_consistency`` ist der Regressionswächter.
"""
from automation.optimizer import invariants as inv
from automation.optimizer import run_optimization as ro


# ── _classify_is_rejection_detail / _classify_trial_rejection: timebox_violated take precedence ──
class _FakeMetrics:
    def __init__(self, *, oos_evaluated, oos_eligible, oos_covered, oos_total_trades,
                 oos_rejection_reasons=()):
        self.oos_evaluated = oos_evaluated
        self.oos_eligible = oos_eligible
        self.oos_covered = oos_covered
        self.oos_total_trades = oos_total_trades
        self.oos_rejection_reasons = oos_rejection_reasons


def test_classify_is_rejection_detail_distinguishes_timebox_from_is_gate_discard():
    """Issue #971 — VOR dem Fix waren ein echter IS-Gate-Drop und eine nachträgliche
    Zeitbox-Invalidierung (#857) beide ``REJECT_OOS_DISCARDED_BY_IS_GATE``, weil beide
    ``metrics.oos_evaluated=False`` UND ``oos_covered=True`` UND ``oos_total_trades > 0`` tragen."""
    metrics = _FakeMetrics(oos_evaluated=False, oos_eligible=False, oos_covered=True,
                            oos_total_trades=180)
    # Ohne den timebox_violated-Parameter (Legacy-Aufruf): weiterhin IS-Gate-Drop.
    assert ro._classify_is_rejection_detail(metrics) == "REJECT_OOS_DISCARDED_BY_IS_GATE"
    # Mit dem #971-Parameter: die tatsächliche Ursache gewinnt.
    assert ro._classify_is_rejection_detail(
        metrics, timebox_violated=True) == "REJECT_OOS_TIMEBOX_VIOLATION"


def test_classify_trial_rejection_timebox_violated_precedes_not_evaluated():
    metrics = _FakeMetrics(oos_evaluated=False, oos_eligible=False, oos_covered=True,
                            oos_total_trades=180)
    assert ro._classify_trial_rejection(metrics, timebox_violated=True) == "oos_timebox_violation"
    assert ro._classify_trial_rejection(metrics) == "oos_not_evaluated"


def test_classify_is_rejection_detail_timebox_violated_overrides_even_a_normally_eligible_trial():
    """Ein Trial, der eigentlich eligible gewesen wäre, wird durch die Zeitbox-Invalidierung VOR
    ``compute_reward`` disqualifiziert (siehe run_optimization.py #857-Block) — die Klassifikation
    muss diesen Fall genauso als Zeitbox-Ursache erkennen, nicht als "none"/kein Drop."""
    metrics = _FakeMetrics(oos_evaluated=True, oos_eligible=True, oos_covered=True,
                            oos_total_trades=90)
    assert ro._classify_is_rejection_detail(
        metrics, timebox_violated=True) == "REJECT_OOS_TIMEBOX_VIOLATION"


# ── check_holding_time_cap: golden test aus der #971-Akzeptanzkriterien-Liste ────────────────────
def test_golden_unevaluable_trials_without_timebox_violations_do_not_trip_the_guard():
    """#971-Akzeptanzkriterium: eine synthetische Study mit 100 Trials, davon 30 nicht auswertbar
    UND 0 Zeitbox-Verletzern, liefert timebox_violating_trades_frac == 0.0."""
    result = inv.check_holding_time_cap([{
        "strategy": "S", "symbol": "X",
        # 70 evaluable Trials x ~5 Trades/Trial, 0 Verletzungen. Die 30 nicht auswertbaren Trials
        # tragen PER DEFINITION kein oos_max_holding_time_s (sie erreichten die OOS-Zeitbox-Messung
        # nie) und damit auch keine Round-Trips — sie duerfen den Nenner nicht kontaminieren.
        "timebox_violating_trades_denominator": 350,
        "timebox_violating_trades_numerator": 0,
        "timebox_violating_trades_frac": 0.0,
    }])
    assert result.passed is True


def test_golden_trades_over_the_box_trip_the_guard_when_material():
    """#971-Akzeptanzkriterium: 100 auswertbare Trials, davon 30 mit je einem Trade über der Box
    (bei angenommen 5 Trades/Trial macht das 30/500 = 0.06 — noch unter Toleranz) — sobald der
    Anteil über die Toleranz steigt (hier 150/500 = 0.30), FAILt der Check, mit einem Wert > 0."""
    below_tolerance = inv.check_holding_time_cap([{
        "strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": 500,
        "timebox_violating_trades_numerator": 30,
        "timebox_violating_trades_frac": round(30 / 500, 4),
    }], study_tolerance=0.25)
    assert below_tolerance.passed is True
    assert below_tolerance.actual is None

    above_tolerance = inv.check_holding_time_cap([{
        "strategy": "S", "symbol": "X",
        "timebox_violating_trades_denominator": 500,
        "timebox_violating_trades_numerator": 150,
        "timebox_violating_trades_frac": round(150 / 500, 4),
    }], study_tolerance=0.25)
    assert above_tolerance.passed is False
    assert above_tolerance.actual["S/X"] > 0.0


# ── check_counter_partition_consistency (#972) ────────────────────────────────────────────────────
def test_check_counter_partition_consistency_passes_when_breakdown_sums_to_n_trials():
    result = inv.check_counter_partition_consistency([{
        "strategy": "S", "symbol": "X", "n_trials": 67, "plateau_n_evaluated": 47,
        "plateau_counter_breakdown": {
            "invalidated_timebox": 20, "invalidated_trade_cap": 0, "discarded_is_gate": 0,
            "window_unreachable": 0, "not_evaluated": 0,
        },
    }])
    assert result.passed is True


def test_check_counter_partition_consistency_fails_when_survivors_and_breakdown_disagree():
    """Reproduziert exakt den #972-Befund: 47 Überlebende + 0-Zerlegung (der alte, tautologische
    Zähler) summiert sich NICHT auf n_trials=67 — der Widerspruch wird jetzt maschinell erkannt."""
    result = inv.check_counter_partition_consistency([{
        "strategy": "DynamicBreakoutStrategy", "symbol": "GSAT.ETORO", "n_trials": 67,
        "plateau_n_evaluated": 47,
        "plateau_counter_breakdown": {
            "invalidated_timebox": 0, "invalidated_trade_cap": 0, "discarded_is_gate": 0,
            "window_unreachable": 0, "not_evaluated": 0,
        },
    }])
    assert result.passed is False
    assert "DynamicBreakoutStrategy/GSAT.ETORO" in result.actual


def test_check_counter_partition_consistency_noop_without_plateau_telemetry():
    result = inv.check_counter_partition_consistency([{"strategy": "S", "symbol": "X", "n_trials": 10}])
    assert result.passed is True
