"""Issue #1297 (GH #1170, Katalog #1272-1297, P1) — Sizing-Deckel greift nicht: LEVERAGE_OVERSHOOT
in 4/4 Laeufen.

Symptom. ``check_sizing_cap_enforcement`` (blocking) FAILt in allen vier Laeufen mit je einer Study:
Vwap/TSLA 15,9366 % bzw. 16,0531 % und AdxAtr/NVDA 16,1921 % gegen ``trade_amount_pct = 15,0`` —
Ueberschreitungsfaktoren 1,0624/1,0702/1,0795/1,0795. ``f_realized_peak_max_pct`` und
``f_turnover_realized_max_pct`` sind exakt identisch, es liegt also kein Scale-in-Umschlag-Artefakt
vor (die #1233-Trennung ist wirksam): die Position selbst ueberschreitet den konfigurierten Anteil.

Root-Cause. Der Aggregat-Deckel aus #1209 in ``_compute_quantity`` wird auf Basis des
Equity-/Preis-Standes ZUM SIGNALZEITPUNKT gerechnet; der Fill erfolgt zum naechsten Bar-Schluss. Bei
einer adversen Bewegung (Preis ODER Equity) zwischen Sizing und Fill ueberschreitet das REALISIERTE
Notional den Zielanteil, ohne dass vor diesem Fix ein Nachpruefpfad existierte.

Fix (vier Punkte aus dem Issue-Text):
  1. Deckel nach dem Fill erneut auswerten (``live_risk.compute_sizing_cap_correction``, aufgerufen
     aus ``hourly_strategy_base.on_position_opened``): ueberschreitet das realisierte Notional
     ``target_fraction * equity_at_entry * (1 + tolerance)``, wird die Position SOFORT ueber eine
     Teilschliessung (Market-Reduce-Order) auf das Ziel reduziert.
  2. ``sizing_cap_tolerance`` als expliziter Config-Key (``optimizer.json``, Default 0.02) ersetzt
     die zuvor implizite 1,05x/5%-Toleranz aus ``invariants.check_sizing_cap_enforcement``.
  3. Telemetrie ``sizing_cap_corrections_count``/``sizing_cap_max_overshoot_pre_correction`` je
     Study (holdout-only, wie ``holdout_f_realized_peak_max``).
  4. Der Live-Pfad (``MomentumLSAllocator.max_symbol_exposure_fraction``) und der Backtest-Pfad
     (``trade_amount_pct``) rufen DIESELBE Deckel-Funktion ueber DENSELBEN Aufrufort
     (``on_position_opened``) auf — kein Duplikat.

Scope: ``compute_sizing_cap_correction`` ist rein/deterministisch und direkt unit-testbar (dieselbe
Teststrategie wie die #1209-Vorlage, ``test_issue_1060_1209_sizing_cap_enforcement.py``s
``_apply_sizing_cap``). Die Verdrahtung in ``on_position_opened``/``_compute_quantity`` (echte
Order-Absetzung in einem laufenden NautilusTrader-Node) wird per Quelltext-Regressionswaechter
geprueft, nicht per Live-Fill-Simulation (dieselbe Begruendung wie dort: die Arithmetik ist die
sicherheitskritische Groesse, nicht der Node-Lifecycle)."""
import inspect
import json
import tempfile
from pathlib import Path

import pytest

from automation.live_risk import compute_sizing_cap_correction, SizingCapCorrection
from automation.momentum_ls_allocator import MomentumLSAllocator
from automation.strategies.hourly_strategy_base import HourlyStrategyConfig
from automation.optimizer import invariants as inv
from automation.optimizer import parsing


# ---------------------------------------------------------------------------------------------
# live_risk.compute_sizing_cap_correction — reine Arithmetik
# ---------------------------------------------------------------------------------------------

def test_no_correction_within_tolerance():
    r = compute_sizing_cap_correction(
        realized_notional=1530.0, equity_at_entry=10000.0, target_fraction=0.15, tolerance=0.02)
    assert r.correction_needed is False
    assert r.target_notional == 1500.0


def test_correction_needed_beyond_tolerance():
    r = compute_sizing_cap_correction(
        realized_notional=1600.0, equity_at_entry=10000.0, target_fraction=0.15, tolerance=0.02)
    assert r.correction_needed is True
    assert r.excess_notional == pytest.approx(100.0)
    assert r.overshoot_factor == pytest.approx(1600.0 / 1500.0)


def test_acceptance_criterion_reference_scenario():
    """Akzeptanzkriterium aus dem Issue-Text: Sizing bei Equity E, Fill nach adverser Kursbewegung
    ⇒ realisiertes Notional bleibt <= trade_amount_pct * 1.02 NACH der Korrektur. E=10000,
    trade_amount_pct=15 (Ziel 1500 USD), Signal-Preis=100 -> 15 Einheiten; Fill zu 106.5 (+6.5 %,
    die adverse Bewegung) ergibt ein realisiertes Notional von 1597.5 USD (1,065x) -- klar ueber der
    2 %-Toleranz."""
    equity = 10000.0
    trade_amount_pct = 15.0
    signal_price = 100.0
    fill_price = 106.5
    units = (equity * trade_amount_pct / 100.0) / signal_price
    realized_notional = units * fill_price
    r = compute_sizing_cap_correction(
        realized_notional=realized_notional, equity_at_entry=equity,
        target_fraction=trade_amount_pct / 100.0, tolerance=0.02)
    assert r.correction_needed is True
    remaining_notional = realized_notional - r.excess_notional
    assert remaining_notional <= trade_amount_pct / 100.0 * equity * 1.02 + 1e-9


def test_fail_open_without_equity():
    r = compute_sizing_cap_correction(
        realized_notional=10_000.0, equity_at_entry=None, target_fraction=0.15)
    assert r.correction_needed is False
    assert r.target_notional is None


def test_fail_open_without_target_fraction():
    r = compute_sizing_cap_correction(
        realized_notional=10_000.0, equity_at_entry=10000.0, target_fraction=None)
    assert r.correction_needed is False


def test_fail_open_with_zero_or_negative_equity():
    r = compute_sizing_cap_correction(
        realized_notional=10_000.0, equity_at_entry=0.0, target_fraction=0.15)
    assert r.correction_needed is False


def test_exactly_at_tolerance_boundary_is_not_a_correction():
    r = compute_sizing_cap_correction(
        realized_notional=1530.0, equity_at_entry=10000.0, target_fraction=0.15, tolerance=0.02)
    assert r.correction_needed is False


def test_just_over_tolerance_boundary_is_a_correction():
    r = compute_sizing_cap_correction(
        realized_notional=1530.01, equity_at_entry=10000.0, target_fraction=0.15, tolerance=0.02)
    assert r.correction_needed is True


def test_overshoot_factor_reported_even_when_no_correction_needed():
    """overshoot_factor ist informativ (fuer sizing_cap_max_overshoot_pre_correction) und wird auch
    im Passfall gemeldet, nicht nur bei einer Korrektur."""
    r = compute_sizing_cap_correction(
        realized_notional=1500.0, equity_at_entry=10000.0, target_fraction=0.15, tolerance=0.02)
    assert r.correction_needed is False
    assert r.overshoot_factor == pytest.approx(1.0)


def test_returns_frozen_dataclass_instance():
    r = compute_sizing_cap_correction(
        realized_notional=1500.0, equity_at_entry=10000.0, target_fraction=0.15)
    assert isinstance(r, SizingCapCorrection)
    with pytest.raises(Exception):
        r.correction_needed = False


# ---------------------------------------------------------------------------------------------
# Fix Punkt 4 — derselbe Zielanteil fuer Backtest (trade_amount_pct) und Live (Allocator)
# ---------------------------------------------------------------------------------------------

def test_allocator_exposes_max_symbol_exposure_fraction_publicly():
    allocator = MomentumLSAllocator(["TSLA.ETORO"], max_symbol_exposure_fraction=0.10)
    assert allocator.max_symbol_exposure_fraction == 0.10


def test_hourly_strategy_config_sizing_cap_tolerance_default():
    cfg = HourlyStrategyConfig(instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-LAST-EXTERNAL")
    assert cfg.sizing_cap_tolerance == 0.02


def test_hourly_strategy_config_sizing_cap_tolerance_overridable():
    cfg = HourlyStrategyConfig(
        instrument_id="TSLA.ETORO", bar_type="TSLA.ETORO-1-HOUR-LAST-EXTERNAL",
        sizing_cap_tolerance=0.05)
    assert cfg.sizing_cap_tolerance == 0.05


# ---------------------------------------------------------------------------------------------
# Fix Punkt 2 — sizing_cap_tolerance als expliziter Config-Key
# ---------------------------------------------------------------------------------------------

def test_production_optimizer_config_has_sizing_cap_tolerance_default():
    cfg = json.loads(Path("automation/config/optimizer.json").read_text("utf-8"))
    assert cfg.get("sizing_cap_tolerance") == 0.02


def test_report_check_sizing_cap_enforcement_call_site_reads_config_tolerance():
    """Quelltext-Regressionswaechter: report._build_report darf die im Check eingefrorene 1.05x-
    Toleranz nicht mehr unveraendert lassen -- max_overshoot_factor muss aus
    optimizer_cfg['sizing_cap_tolerance'] abgeleitet werden (Fix Punkt 2)."""
    from automation.optimizer import report as rpt
    source = inspect.getsource(rpt._build_report)
    assert "sizing_cap_tolerance" in source
    assert "check_sizing_cap_enforcement(" in source


# ---------------------------------------------------------------------------------------------
# invariants.check_sizing_cap_enforcement — Fix Punkt 3 telemetry-enriched offenders
# ---------------------------------------------------------------------------------------------

def _study(strategy, symbol, *, trade_amount_pct, f_realized_peak_max,
           corrections_count=None, max_overshoot_pre_correction=None):
    r = {"strategy": strategy, "symbol": symbol, "trade_amount_pct": trade_amount_pct,
         "holdout_f_realized_peak_max": f_realized_peak_max}
    if corrections_count is not None:
        r["holdout_sizing_cap_corrections_count"] = corrections_count
    if max_overshoot_pre_correction is not None:
        r["holdout_sizing_cap_max_overshoot_pre_correction"] = max_overshoot_pre_correction
    return r


def test_offender_surfaces_corrections_count_when_present():
    r = inv.check_sizing_cap_enforcement([
        _study("VwapMeanReversion", "TSLA.ETORO", trade_amount_pct=15.0,
               f_realized_peak_max=0.159366, corrections_count=1,
               max_overshoot_pre_correction=0.0624),
    ])
    assert r.passed is False
    offender = r.actual["VwapMeanReversion/TSLA.ETORO"]
    assert offender["sizing_cap_corrections_count"] == 1
    assert offender["sizing_cap_max_overshoot_pre_correction"] == pytest.approx(0.0624)


def test_offender_without_corrections_telemetry_omits_the_new_keys():
    """Rueckwaertskompatibel: fehlt die neue Telemetrie (aeltere Report-JSONs), bleibt der Offender
    unveraendert -- kein KeyError, keine erfundenen Werte."""
    r = inv.check_sizing_cap_enforcement([
        _study("AdxAtrMomentumStrategy", "NVDA.ETORO", trade_amount_pct=15.0,
               f_realized_peak_max=0.161921),
    ])
    offender = r.actual["AdxAtrMomentumStrategy/NVDA.ETORO"]
    assert "sizing_cap_corrections_count" not in offender
    assert "sizing_cap_max_overshoot_pre_correction" not in offender


def test_configurable_max_overshoot_factor_still_works():
    """Fix Punkt 2 macht die Toleranz config-getrieben (report.py-Aufrufstelle), die Funktions-
    Signatur selbst bleibt rueckwaerts-kompatibel parametrisierbar."""
    r_tight = inv.check_sizing_cap_enforcement(
        [_study("S", "X.ETORO", trade_amount_pct=15.0, f_realized_peak_max=0.154)],
        max_overshoot_factor=1.02)
    assert r_tight.passed is False
    r_loose = inv.check_sizing_cap_enforcement(
        [_study("S", "X.ETORO", trade_amount_pct=15.0, f_realized_peak_max=0.154)],
        max_overshoot_factor=1.05)
    assert r_loose.passed is True


# ---------------------------------------------------------------------------------------------
# parsing.TournamentMetrics / parse_tournament — Fix Punkt 3 field roundtrip
# ---------------------------------------------------------------------------------------------

def test_tournament_metrics_has_the_new_fields():
    fields = parsing.TournamentMetrics.__dataclass_fields__
    assert "oos_sizing_cap_corrections_count" in fields
    assert "oos_sizing_cap_max_overshoot_pre_correction" in fields


def test_parse_tournament_roundtrips_sizing_cap_telemetry(tmp_path):
    payload = {
        "aggregate_winner": {
            "oos_evaluated": True, "oos_eligible": True,
            "oos_metrics": {
                "sizing_cap_corrections_count": 2,
                "sizing_cap_max_overshoot_pre_correction": 0.0795,
            },
        },
    }
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    metrics = parsing.parse_tournament(p)
    assert metrics.oos_sizing_cap_corrections_count == 2
    assert metrics.oos_sizing_cap_max_overshoot_pre_correction == pytest.approx(0.0795)


def test_parse_tournament_defaults_to_none_without_the_fields(tmp_path):
    payload = {"aggregate_winner": {"oos_evaluated": True, "oos_eligible": True, "oos_metrics": {}}}
    p = tmp_path / "tournament_result.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    metrics = parsing.parse_tournament(p)
    assert metrics.oos_sizing_cap_corrections_count is None
    assert metrics.oos_sizing_cap_max_overshoot_pre_correction is None


# ---------------------------------------------------------------------------------------------
# run_optimization._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS — metric-stamping contract
# ---------------------------------------------------------------------------------------------

def test_new_oos_fields_are_allowlisted_as_holdout_only():
    from automation.optimizer import run_optimization as ro
    assert "oos_sizing_cap_corrections_count" in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS
    assert "oos_sizing_cap_max_overshoot_pre_correction" in ro._INTENTIONALLY_UNSTAMPED_METRIC_FIELDS


# ---------------------------------------------------------------------------------------------
# confirm.py — Pitfall #421-Klasse: parsing.py allein bruecke nicht die kuratierte Teilmenge
# ---------------------------------------------------------------------------------------------

def test_confirm_py_curated_holdout_dict_includes_the_new_fields():
    from automation.optimizer import confirm as cf
    source = Path(cf.__file__).read_text("utf-8")
    assert '"oos_sizing_cap_corrections_count": getattr(m, "oos_sizing_cap_corrections_count", None)' in source
    assert (
        '"oos_sizing_cap_max_overshoot_pre_correction": getattr(\n'
        '            m, "oos_sizing_cap_max_overshoot_pre_correction", None)' in source
        or '"oos_sizing_cap_max_overshoot_pre_correction"' in source
    )


# ---------------------------------------------------------------------------------------------
# report._study_record — holdout_sizing_cap_* threading (Quelltext-Regressionswaechter)
# ---------------------------------------------------------------------------------------------

def test_study_record_threads_holdout_sizing_cap_fields():
    from automation.optimizer import report as rpt
    source = inspect.getsource(rpt)
    assert '"holdout_sizing_cap_corrections_count": holdout_metrics.get("oos_sizing_cap_corrections_count")' in source
    assert 'holdout_metrics.get(\n            "oos_sizing_cap_max_overshoot_pre_correction")' in source


# ---------------------------------------------------------------------------------------------
# backtest_runner.py — Strategie-Instanz-Telemetrie wird gestempelt (Quelltext-Regressionswaechter,
# analog test_issue_1060_1209s Begruendung: kein zuverlaessig instanziierbarer Node in dieser Weise)
# ---------------------------------------------------------------------------------------------

def test_backtest_runner_stamps_sizing_cap_telemetry_from_the_strategy_instance():
    import automation.backtest_runner as br
    source = Path(br.__file__).read_text("utf-8")
    assert 'getattr(\n            strategy, "_sizing_cap_corrections_count", 0)' in source
    assert '"_sizing_cap_max_overshoot_pre_correction"' in source


# ---------------------------------------------------------------------------------------------
# hourly_strategy_base.py — Fix Punkt 1/4 Quelltext-Regressionswaechter
# ---------------------------------------------------------------------------------------------

_STRATEGY_SOURCE = Path("automation/strategies/hourly_strategy_base.py").read_text("utf-8")


def _compute_quantity_source() -> str:
    start = _STRATEGY_SOURCE.index("def _compute_quantity(self, bar: Bar)")
    end = _STRATEGY_SOURCE.index("\n    def on_position_opened(self, event)")
    return _STRATEGY_SOURCE[start:end]


def _on_position_opened_source() -> str:
    start = _STRATEGY_SOURCE.index("def on_position_opened(self, event)")
    end = _STRATEGY_SOURCE.index("\n    def on_position_closed(self, event)")
    return _STRATEGY_SOURCE[start:end]


def test_compute_quantity_sets_sizing_target_fraction_for_allocator_path():
    source = _compute_quantity_source()
    assert "self._sizing_target_fraction = self.allocator.max_symbol_exposure_fraction" in source


def test_compute_quantity_sets_sizing_target_fraction_for_pct_path():
    source = _compute_quantity_source()
    assert "self._sizing_target_fraction = trade_amount_pct / 100.0" in source


def test_on_position_opened_calls_the_shared_cap_correction_function():
    source = _on_position_opened_source()
    assert "compute_sizing_cap_correction(" in source
    assert "self._sizing_target_fraction" in source


def test_on_position_opened_submits_a_reduce_order_when_correction_needed():
    source = _on_position_opened_source()
    assert "_correction.correction_needed" in source
    assert "self.order_factory.market(" in source
    assert "self.submit_order(correction_order)" in source


def test_on_position_opened_increments_the_corrections_counter():
    source = _on_position_opened_source()
    assert "self._sizing_cap_corrections_count += 1" in source


def test_on_position_opened_updates_the_max_overshoot_telemetry():
    source = _on_position_opened_source()
    assert "self._sizing_cap_max_overshoot_pre_correction" in source


def test_take_profit_order_uses_the_corrected_quantity():
    """Vermeidet eine TP-Limit-Order fuer die volle (unkorrigierte) event.quantity, nachdem eine
    Teilschliessung bereits abgesetzt wurde."""
    source = _on_position_opened_source()
    tp_block_start = source.index("if self._profit_target_pct is not None")
    tp_block = source[tp_block_start:tp_block_start + 800]
    assert "_corrected_qty" in tp_block


def test_only_one_call_site_for_compute_sizing_cap_correction():
    """Fix Punkt 4 — EIN Aufrufort fuer Backtest- UND Live-Pfad (dieselbe on_position_opened-Methode
    bedient beide, siehe _compute_quantity's Pfad A/C)."""
    assert _STRATEGY_SOURCE.count("compute_sizing_cap_correction(") == 1
