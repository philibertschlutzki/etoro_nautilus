"""Issue #1078/#1226 (P1, Semantik-Bump, reward_semantics_version v25) — kalibrierte p50-Slippage
in den Selektionspfad.

Symptom (B-12): die gemessene Fill-Slippage bei TRAILING_STOP-Exits ist im Median 44,1 % des
Median-Stop-Verlusts und 7,85x c_rt — sie beeinflusste bislang WEDER Reward NOCH Eligibility-Gates
NOCH Deflation, sondern lebte ausschliesslich in der additiven ``full_realism``-Report-Stress-Stufe
(``_full_realism_expectancy``), die keinen Trial jemals rejected oder umrankt.

Root-Cause: die Round-Trip-PnL-Serie (``rt_pnls_with_ts``), die JEDER Konsument (Reward, Gates,
Deflation, Holdout-Metriken) liest, enthielt an der Quelle nie eine kalibrierte Slippage-Korrektur.

Fix: ``backtest_runner._apply_calibrated_slippage_deduction`` zieht die kalibrierte p50-Slippage
(je Asset-Klasse) AN DER QUELLE von JEDEM TRAILING_STOP-Round-Trip ab — unmittelbar NACH dem
#946/#1112-Dust-Boden, VOR jeder IS/OOS-/Fold-Aufteilung — nach demselben "Fix an der Quelle,
Konsumenten erben automatisch"-Muster wie ``_filter_dust_round_trips``. Gated über
``optimizer.json['apply_calibrated_slippage_in_selection']`` (Default ``true``); ``false`` ODER ein
leerer Kalibrierungs-Cache (``slippage_bps_p50<=0``) ⇒ bit-identisch zum Vorzustand
(``selection_cost_basis='round_trip_only'``, Akzeptanzkriterium 1, Zero-Regression). Die neue,
je Study gestempelte ``selection_cost_basis`` (``'round_trip_only'`` vs.
``'round_trip_plus_calibrated_slippage'``) macht sichtbar, ob der Abzug tatsächlich griff, und
propagiert ohne separate Verdrahtung über ``parsing.TournamentMetrics`` (Sweep-Trial-Ebene) und
``confirm._metrics_dict``/``report._study_record`` (promotierte Holdout-Ebene, Akzeptanzkriterium 4:
"selection_cost_basis in 154/154 Records", flacher Feldname ohne ``holdout_``-Präfix).

Hinweis: dieses Modul importiert ``automation.backtest_runner``, das ``nautilus_trader`` voraussetzt
— dieselbe Umgebungs-Einschränkung wie ``test_issue_946_1112_dust_round_trip_source_filter.py``. In
dieser Sandbox (Python 3.12, gepinnte ``nautilus_trader``-Version) ist das Modul importierbar.
"""
import inspect
import json

import pytest

from automation import backtest_runner
from automation.optimizer import confirm, invariants as inv, parsing, run_optimization as ro


def _rt(pnl, exit_ts_ns, holding_ns=3600, qty=1.0):
    return (pnl, exit_ts_ns, holding_ns, qty)


# --- backtest_runner._apply_calibrated_slippage_deduction ---------------------------------------

def test_noop_when_slippage_bps_p50_is_zero():
    rt_pnls = [_rt(-10.0, 0)]
    rt_notionals = [(1000.0, 0)]
    rt_meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, rt_meta, slippage_bps_p50=0.0)
    assert adjusted is rt_pnls
    assert n == 0


def test_noop_when_slippage_bps_p50_is_negative():
    rt_pnls = [_rt(-10.0, 0)]
    rt_notionals = [(1000.0, 0)]
    rt_meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, rt_meta, slippage_bps_p50=-5.0)
    assert adjusted is rt_pnls
    assert n == 0


def test_deducts_only_trailing_stop_exits():
    rt_pnls = [_rt(-10.0, 0), _rt(20.0, 1), _rt(-30.0, 2)]
    rt_notionals = [(1000.0, 0), (1000.0, 1), (1000.0, 2)]
    rt_meta = [
        {"exit_reason": "TRAILING_STOP"},
        {"exit_reason": "TIME_BOX"},
        {"exit_reason": "TRAILING_STOP"},
    ]
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, rt_meta, slippage_bps_p50=10.0)
    assert n == 2
    assert adjusted[0][0] == pytest.approx(-10.0 - 0.001 * 1000.0)
    assert adjusted[1][0] == 20.0
    assert adjusted[2][0] == pytest.approx(-30.0 - 0.001 * 1000.0)
    # exit_ts/holding_ns/qty bleiben unveraendert (nur das PnL-Feld wird korrigiert).
    assert adjusted[0][1:] == rt_pnls[0][1:]
    assert adjusted[1] == rt_pnls[1]


def test_exact_deduction_value_matches_slippage_rate_times_notional():
    rt_pnls = [_rt(100.0, 0)]
    rt_notionals = [(5000.0, 0)]
    rt_meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, rt_meta, slippage_bps_p50=24.03)
    assert n == 1
    expected = 100.0 - (24.03 / 10000.0) * 5000.0
    assert adjusted[0][0] == pytest.approx(expected)


def test_missing_or_zero_notional_is_skipped_safely():
    rt_pnls = [_rt(-10.0, 0), _rt(-20.0, 1)]
    rt_notionals = [(0.0, 0), (None, 1)]
    rt_meta = [{"exit_reason": "TRAILING_STOP"}, {"exit_reason": "TRAILING_STOP"}]
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, rt_meta, slippage_bps_p50=10.0)
    assert n == 0
    assert adjusted[0][0] == -10.0
    assert adjusted[1][0] == -20.0


def test_missing_exit_meta_entry_is_treated_as_no_match_not_a_crash():
    """``rt_exit_meta`` kuerzer als ``rt_pnls_with_ts`` sollte strukturell nicht vorkommen (beide
    sind index-parallel), aber die Funktion darf nicht crashen — dieselbe Absicherung wie
    ``_filter_dust_round_trips`` gegen kuerzere Nebenlisten."""
    rt_pnls = [_rt(-10.0, 0)]
    rt_notionals = [(1000.0, 0)]
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, [], slippage_bps_p50=10.0)
    assert n == 0
    assert adjusted[0][0] == -10.0


def test_returns_new_list_when_adjustments_are_applied():
    """Der Rueckgabewert ist eine NEUE Liste (kein In-Place-Mutieren des Aufrufer-Arguments) —
    dasselbe Muster wie ``_filter_dust_round_trips``."""
    rt_pnls = [_rt(-10.0, 0)]
    rt_notionals = [(1000.0, 0)]
    rt_meta = [{"exit_reason": "TRAILING_STOP"}]
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, rt_meta, slippage_bps_p50=10.0)
    assert n == 1
    assert adjusted is not rt_pnls
    assert rt_pnls[0][0] == -10.0  # Original unveraendert


# --- backtest_runner._read_apply_calibrated_slippage_in_selection -------------------------------

def test_production_config_default_is_true(monkeypatch):
    """Akzeptanzkriterium: optimizer.json['apply_calibrated_slippage_in_selection'] ist auf true
    gesetzt (Fix-Vorgabe, siehe dortiger Schema-Eintrag)."""
    monkeypatch.setattr(backtest_runner, "_apply_calibrated_slippage_in_selection_cache", None)
    assert backtest_runner._read_apply_calibrated_slippage_in_selection() is True


def test_cache_short_circuits_the_config_read(monkeypatch):
    monkeypatch.setattr(backtest_runner, "_apply_calibrated_slippage_in_selection_cache", False)
    assert backtest_runner._read_apply_calibrated_slippage_in_selection() is False
    monkeypatch.setattr(backtest_runner, "_apply_calibrated_slippage_in_selection_cache", True)
    assert backtest_runner._read_apply_calibrated_slippage_in_selection() is True


def test_optimizer_json_carries_the_switch_and_the_bumped_version():
    with open(backtest_runner.config_dir() / "optimizer.json", "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg.get("apply_calibrated_slippage_in_selection") is True
    # Issue #1248/#1250/#1257 (GH #1118/#1120/#1127) — reward_semantics_version seither weiter auf
    # 26 gebumpt (siehe test_issue_637_reward_semantics_bump.py); die #1078/#1226-Bump-Schwelle war
    # 25, dies bleibt eine strikte Untergrenze, keine exakte Pin-Stelle mehr.
    assert cfg.get("reward_semantics_version") >= 25


# --- Zero-Regression (Akzeptanzkriterium 1): extract_metrics gates the deduction ------------------

def test_extract_metrics_gates_the_deduction_call_behind_the_switch():
    """Strukturbeweis (analog test_issue_1075_1223s Quelltext-Assertions): der Aufruf von
    ``_apply_calibrated_slippage_deduction`` in ``extract_metrics`` steht HINTER
    ``if _read_apply_calibrated_slippage_in_selection():`` — bei ``false`` wird
    ``rt_pnls_with_ts`` nicht neu zugewiesen und ``selection_cost_basis`` bleibt beim Default
    ``'round_trip_only'`` (bit-identisch zum Vorzustand)."""
    source = inspect.getsource(backtest_runner.extract_metrics)
    assert 'selection_cost_basis = "round_trip_only"' in source
    idx_gate = source.index("if _read_apply_calibrated_slippage_in_selection():")
    idx_call = source.index(
        "rt_pnls_with_ts, n_slippage_adjusted_round_trips = _apply_calibrated_slippage_deduction(")
    assert idx_gate < idx_call, (
        "Der Aufruf von _apply_calibrated_slippage_deduction steht nicht (mehr) hinter dem "
        "apply_calibrated_slippage_in_selection-Gate — Zero-Regression (Akzeptanzkriterium 1) "
        "waere bei false nicht mehr garantiert."
    )


def test_extract_metrics_stamps_selection_cost_basis_on_both_levels():
    source = inspect.getsource(backtest_runner.extract_metrics)
    assert 'is_metrics["selection_cost_basis"] = selection_cost_basis' in source
    assert 'oos_metrics["selection_cost_basis"] = selection_cost_basis' in source


def test_deduction_call_happens_after_the_1112_dust_filter_not_before():
    """Reihenfolge-Kontrakt: der #1078-Abzug muss NACH ``_filter_dust_round_trips`` (#946/#1112)
    laufen, damit ein Dust-Leg nicht faelschlich mitgestresst wird, bevor es verworfen ist."""
    source = inspect.getsource(backtest_runner.extract_metrics)
    idx_dust = source.index("_filter_dust_round_trips(")
    idx_slippage = source.index("_apply_calibrated_slippage_deduction(")
    assert idx_dust < idx_slippage


# --- Median-Expectancy-Drop (semantic acceptance criterion) ---------------------------------------

def test_median_expectancy_drops_after_slippage_deduction_when_all_trailing_stop():
    """Akzeptanzkriterium 2: die kapitalgewichtete Expectancy EINER Study mit ausschliesslich
    TRAILING_STOP-Exits sinkt monoton, sobald der Abzug greift."""
    rt_pnls = [_rt(50.0, i) for i in range(10)]
    rt_notionals = [(1000.0, i) for i in range(10)]
    rt_meta = [{"exit_reason": "TRAILING_STOP"} for _ in range(10)]

    def _expectancy(pnls):
        return sum(p for p, *_ in pnls) / len(pnls)

    baseline = _expectancy(rt_pnls)
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, rt_meta, slippage_bps_p50=24.03)
    assert n == 10
    stressed = _expectancy(adjusted)
    assert stressed < baseline
    expected_drop_per_trip = (24.03 / 10000.0) * 1000.0
    assert baseline - stressed == pytest.approx(expected_drop_per_trip)


def test_expectancy_unchanged_when_no_trailing_stop_exits_present():
    """Ein Symbol/Study ohne einen einzigen TRAILING_STOP-Exit im Fenster bleibt bit-identisch
    (``selection_cost_basis`` faellt auf ``'round_trip_only'`` zurueck, kein Raten)."""
    rt_pnls = [_rt(50.0, i) for i in range(5)]
    rt_notionals = [(1000.0, i) for i in range(5)]
    rt_meta = [{"exit_reason": "TIME_BOX"} for _ in range(5)]
    adjusted, n = backtest_runner._apply_calibrated_slippage_deduction(
        rt_pnls, rt_notionals, rt_meta, slippage_bps_p50=24.03)
    assert n == 0
    assert adjusted == rt_pnls


# --- parsing.TournamentMetrics / oos_selection_cost_basis ----------------------------------------

def test_parse_tournament_reads_selection_cost_basis(tmp_path):
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True,
            "oos_eligible": True,
            "win_count": 1,
            "median_is_sortino": 0.4,
            "oos_metrics": {
                "sortino_ratio": 1.1,
                "total_trades": 5,
                "selection_cost_basis": "round_trip_plus_calibrated_slippage",
            },
        },
        "full_results": [],
    }
    path = tmp_path / "tournament_result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = parsing.parse_tournament(path)
    assert metrics.oos_selection_cost_basis == "round_trip_plus_calibrated_slippage"


def test_parse_tournament_missing_selection_cost_basis_is_none(tmp_path):
    """Legacy-JSON ohne das Feld (Pre-#1078) laedt fehlerfrei mit None."""
    payload = {
        "fully_eligible_pairs": 1,
        "aggregate_winner": {
            "oos_evaluated": True,
            "oos_eligible": True,
            "win_count": 1,
            "median_is_sortino": 0.4,
            "oos_metrics": {"sortino_ratio": 1.1, "total_trades": 5},
        },
        "full_results": [],
    }
    path = tmp_path / "tournament_result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    metrics = parsing.parse_tournament(path)
    assert metrics.oos_selection_cost_basis is None


# --- confirm._metrics_dict / report._study_record bridge -----------------------------------------

class _M:
    """Minimaler TournamentMetrics-Stand-in (nur die fuer confirm._metrics_dict benoetigten
    Attribute), analog test_issue_948_1114_fold_annualization_period_scale.py."""
    def __getattr__(self, name):
        return None


def test_metrics_dict_carries_the_oos_prefixed_key():
    m = _M()
    m.oos_selection_cost_basis = "round_trip_plus_calibrated_slippage"
    d = confirm._metrics_dict(m)
    assert d["oos_selection_cost_basis"] == "round_trip_plus_calibrated_slippage"


def test_allowlist_entry_reason_names_the_report_field():
    assert "oos_selection_cost_basis" in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
    reason = ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS["oos_selection_cost_basis"]
    assert "confirm.py-Re-Evaluation" in reason
    assert "selection_cost_basis" in reason


# --- invariants.check_selection_cost_basis_contract ------------------------------------------------

def _record(*, basis, holdout_slippage_bps=None, trades=10, strategy="S", symbol="SYM.ETORO"):
    r = {"strategy": strategy, "symbol": symbol, "holdout_total_trades": trades,
         "selection_cost_basis": basis}
    if holdout_slippage_bps is not None:
        r["holdout_stop_exit_slippage_bps"] = holdout_slippage_bps
    return r


def test_inconclusive_without_any_selection_cost_basis_field():
    result = inv.check_selection_cost_basis_contract([{"strategy": "S", "symbol": "X.ETORO"}])
    assert result.passed is True
    assert result.inconclusive is True


def test_passes_for_round_trip_only_without_measured_slippage():
    records = [_record(basis="round_trip_only")]
    result = inv.check_selection_cost_basis_contract(records)
    assert result.passed is True


def test_passes_for_adjusted_basis_with_measured_slippage():
    records = [_record(basis="round_trip_plus_calibrated_slippage", holdout_slippage_bps=24.03)]
    result = inv.check_selection_cost_basis_contract(records)
    assert result.passed is True


def test_fails_for_adjusted_basis_without_any_measured_slippage():
    """Widerspruch: die Study behauptet einen Abzug, aber die Holdout-Slippage-Telemetrie zeigt
    0/None — der Abzug kann strukturell nicht stattgefunden haben."""
    records = [_record(basis="round_trip_plus_calibrated_slippage", holdout_slippage_bps=None)]
    result = inv.check_selection_cost_basis_contract(records)
    assert result.passed is False
    assert "S/SYM.ETORO" in result.actual


def test_fails_for_unknown_basis_value():
    records = [_record(basis="something_else")]
    result = inv.check_selection_cost_basis_contract(records)
    assert result.passed is False


def test_not_applicable_without_holdout_trades():
    result = inv.check_selection_cost_basis_contract(
        [{"strategy": "S", "symbol": "X.ETORO", "selection_cost_basis": "round_trip_only"}])
    assert result.passed is True
    assert result.inconclusive is True
