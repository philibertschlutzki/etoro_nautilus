"""Issue #1259 (GH #1129), Pitfall #442 in AGENTS.md — generischer Brücken-Test.

Symptom (Kern des Issues). ``bar_range_median_bps`` war in 14/14 Studies ``None``, obwohl
``HourlyStrategyBase`` die Bar-Spanne maß: bei 100 % Nullspannen-Bars war die um Nullspannen-Bars
bereinigte Liste leer und der Tag wurde nie emittiert (siehe
``hourly_strategy_base._bar_range_bps_tags``-Docstring). Fix: der Tag wird jetzt UNBEDINGT
emittiert (0.0 mit ``BAR_RANGE_POPULATION_N:0`` statt gar keinem Tag), und ein neuer
``bar_range_population_n``-Zähler macht "Median 0 über 0 Bars" (DEGENERATE_ZERO_RANGE) von
"nie gemessen" (POPULATION_UNAVAILABLE) unterscheidbar.

Dieser Test ist der generische Brücken-Wächter aus Fix Punkt 4: jeder Rückgabeschlüssel von
``backtest_runner._aggregate_exit_telemetry`` muss (a) ein passendes ``oos_*``-Feld in
``parsing.TournamentMetrics`` haben, (b) über ``trial.set_user_attr`` in ``run_optimization.py``
gestempelt werden, UND (c) in ``report._study_record`` gelesen werden — sonst existiert eine
berechnete Kennzahl für jede Invariante nicht (Pitfall #442). Ein Schlüssel ohne einen dieser drei
Schritte lässt diesen Test fehlschlagen, nicht erst die nächste Invariante, die ihn braucht.

Bei der Umsetzung dieses Tests wurden zwei vorbestehende, von diesem generischen Scan aufgedeckte
Bruecken-Luecken (Pitfall #442, unabhängig vom Bar-Spannen-Symptom) mitgeschlossen:
``n_trailing_stop_exits_with_lag_telemetry`` und ``stop_ratchet_between_trigger_and_submit_bps_
median``/``n_trailing_stop_exits_with_ratchet_telemetry`` waren in ``_aggregate_exit_telemetry``
berechnet, aber nie bis ``report._study_record`` durchgereicht.
"""
import inspect

from automation.backtest_runner import _aggregate_exit_telemetry
from automation.optimizer import parsing
from automation.optimizer import report
from automation.optimizer import run_optimization as ro

# Issue #1259 — Schlüssel, die BEWUSST keinen eigenen oos_<key>-Bruecken-Pfad haben, weil ihr Wert
# bereits über einen ANDEREN, bereits gebrückten Schlüssel erreichbar ist (kein Pitfall-#442-Fall).
_INTENTIONALLY_UNBRIDGED_RAW_KEYS = {
    "n_round_trips_data_end": (
        "identisch zu exit_reason_histogram['DATA_END'] (oos_exit_reason_histogram ist gebrückt, "
        "siehe report._study_record: n_round_trips_data_end wird dort AUS dem Histogramm "
        "abgeleitet, Issue #1037)."
    ),
}


def _raw_exit_telemetry_keys() -> set[str]:
    return set(_aggregate_exit_telemetry([]).keys())


def _stamped_trial_user_attr_keys() -> set[str]:
    """Siehe test_issue_994_1146_metric_stamping_contract.py — dieselbe AST-Extraktion, hier
    dupliziert (bewusst kein Import aus dem anderen Testmodul: Testmodule bleiben unabhängig
    lauffähig, siehe Testkonventionen in AGENTS.md)."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(ro.__file__).read_text("utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "set_user_attr"
                and isinstance(func.value, ast.Name) and func.value.id == "trial"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    return keys


def _study_record_source() -> str:
    return inspect.getsource(report._study_record)


def test_every_raw_key_has_a_parsing_field_or_is_allowlisted():
    raw_keys = _raw_exit_telemetry_keys()
    fields = set(parsing.TournamentMetrics.__dataclass_fields__)
    missing = [
        k for k in sorted(raw_keys - set(_INTENTIONALLY_UNBRIDGED_RAW_KEYS))
        if f"oos_{k}" not in fields
    ]
    assert not missing, (
        f"_aggregate_exit_telemetry-Schlüssel ohne parsing.TournamentMetrics-Feld 'oos_<key>' und "
        f"ohne _INTENTIONALLY_UNBRIDGED_RAW_KEYS-Begründung: {missing}"
    )


def test_every_bridged_raw_key_is_stamped_as_trial_user_attr():
    """Felder, die run_optimization._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS bereits begründet
    ausnehmen (z. B. holdout-only Sizing-Felder, siehe confirm.py-Re-Evaluation), zählen nicht als
    Lücke — dieselbe Allowlist wie test_issue_994_1146_metric_stamping_contract.py."""
    raw_keys = _raw_exit_telemetry_keys() - set(_INTENTIONALLY_UNBRIDGED_RAW_KEYS)
    fields = set(parsing.TournamentMetrics.__dataclass_fields__)
    stamped = _stamped_trial_user_attr_keys()
    allowlisted = set(ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS)
    missing = [
        f"oos_{k}" for k in sorted(raw_keys)
        if f"oos_{k}" in fields and f"oos_{k}" not in stamped and f"oos_{k}" not in allowlisted
    ]
    assert not missing, (
        f"oos_*-Feld(er) ohne trial.set_user_attr-Stempelung und ohne _INTENTIONALLY_UNSTAMPED_"
        f"METRIC_FIELDS-Begründung in run_optimization.py: {missing}"
    )


def test_every_bridged_raw_key_arrives_in_study_record():
    raw_keys = _raw_exit_telemetry_keys() - set(_INTENTIONALLY_UNBRIDGED_RAW_KEYS)
    fields = set(parsing.TournamentMetrics.__dataclass_fields__)
    src = _study_record_source()
    missing = [
        f"oos_{k}" for k in sorted(raw_keys)
        if f"oos_{k}" in fields and f'"oos_{k}"' not in src
    ]
    assert not missing, (
        f"oos_*-Feld(er), die trial.user_attrs erreichen, aber in report._study_record nicht "
        f"gelesen werden: {missing}"
    )


# ── Die konkreten, im Issue benannten Felder (bar_range_*) ───────────────────────────────────────
def test_bar_range_population_n_is_a_new_full_bridge():
    assert "oos_bar_range_population_n" in set(parsing.TournamentMetrics.__dataclass_fields__)
    assert "oos_bar_range_population_n" in _stamped_trial_user_attr_keys()
    assert '"oos_bar_range_population_n"' in _study_record_source()


def test_bar_range_bps_tags_emitted_unconditionally_with_zero_population():
    from automation.strategies.hourly_strategy_base import HourlyStrategyBase

    class _Fake:
        _position_bar_count = 5
        _position_bar_range_bps_readings: list = []

    tags = HourlyStrategyBase._bar_range_bps_tags(_Fake())
    assert "BAR_RANGE_MEDIAN_BPS:0.0000" in tags
    assert "BAR_RANGE_P75_BPS:0.0000" in tags
    assert "BAR_RANGE_POPULATION_N:0" in tags


def test_bar_range_bps_tags_empty_without_any_bar_observed():
    from automation.strategies.hourly_strategy_base import HourlyStrategyBase

    class _Fake:
        _position_bar_count = 0
        _position_bar_range_bps_readings: list = []

    assert HourlyStrategyBase._bar_range_bps_tags(_Fake()) == []


def test_bar_range_bps_tags_regular_population():
    from automation.strategies.hourly_strategy_base import HourlyStrategyBase

    class _Fake:
        _position_bar_count = 3
        _position_bar_range_bps_readings = [10.0, 20.0, 30.0]

    tags = HourlyStrategyBase._bar_range_bps_tags(_Fake())
    assert "BAR_RANGE_MEDIAN_BPS:20.0000" in tags
    assert "BAR_RANGE_POPULATION_N:3" in tags


# ── check_stop_loss_vs_bar_range: DEGENERATE_ZERO_RANGE vs. POPULATION_UNAVAILABLE ──────────────
def test_confirmed_zero_population_is_degenerate_zero_range():
    from automation.optimizer import invariants as inv

    result = inv.check_stop_loss_vs_bar_range([
        {"strategy": "A", "symbol": "X.ETORO",
         "oos_gross_loss_mean_bps_trailing_stop_pooled": 10.0,
         "bar_range_median_bps": 0.0, "bar_range_population_n": 0,
         "realized_stop_loss_ratio": 6.0, "oos_n_trailing_stop_losses": 40},
    ])
    assert result.passed is False
    assert result.evaluable is True
    assert result.evaluability["inconclusive_reason"] == "DEGENERATE_ZERO_RANGE"


def test_unknown_population_falls_back_to_legacy_reason():
    """Regressionsschutz fuer #1057/#1206: fehlt bar_range_population_n (aeltere Emissionskette),
    bleibt das Alt-Verhalten (POPULATION_UNAVAILABLE_AFTER_FIX) bit-identisch erhalten."""
    from automation.optimizer import invariants as inv

    result = inv.check_stop_loss_vs_bar_range([
        {"strategy": "A", "symbol": "X.ETORO",
         "oos_gross_loss_mean_bps_trailing_stop_pooled": 10.0,
         "bar_range_median_bps": 0.0,
         "realized_stop_loss_ratio": 6.0, "oos_n_trailing_stop_losses": 40},
    ])
    assert result.evaluability["inconclusive_reason"] == "POPULATION_UNAVAILABLE_AFTER_FIX"
