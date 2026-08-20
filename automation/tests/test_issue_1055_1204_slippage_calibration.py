"""Issue #1055/#1204 (P0, Katalog #1196-1221) — Die gemessene Fill-Slippage ist weder im
Kostenmodell noch in der Kostenstress-Leiter.

Symptom: Median-Slippage 21,12-78,57 bps gegen c_rt 3-6 bps (4,7-19,6x). ``slippage_bps_by_asset_
class`` ist in 14/14 Laeufen fuer alle Asset-Klassen 0,0 -- die Kostenstress-Stufen 1,5x/2x
variieren nur um wenige bps, ``full_realism`` ist ein No-Op.

Fix:
1. ``backtest_runner.calibrate_slippage_bps_by_asset_class`` — p50/p90 aus der gemessenen
   ``stop_exit_slippage_bps``-Verteilung je Asset-Klasse.
2. ``sweep.write_calibrated_slippage_cache``/``read_calibrated_slippage_cache`` — Persistenz nach
   ``calibrated_slippage.json`` mit run_id + n_observations.
3. ``backtest_runner.merge_calibrated_slippage_into_config`` — 'full_realism' konsumiert die
   kalibrierte p90 statt der 0.0-Konstante (ausser der Operator hat explizit einen Wert != 0 gesetzt).
4. Die Kostenstress-Faktoren 1,5x/2x wirken auf ``c_rt + slippage_p50`` (nicht auf c_rt allein).
5. ``invariants.check_cost_stress_distinctness`` verschaerft: ein Mindestdelta-Kriterium neben der
   bestehenden Bit-Identitaets-Pruefung."""
import sys
import types
import unittest.mock as mock

import pytest

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
from automation.optimizer import invariants as inv
from automation.optimizer import sweep


# --- backtest_runner.calibrate_slippage_bps_by_asset_class ------------------------------------------

def test_calibrate_computes_p50_p90_per_asset_class():
    result = br.calibrate_slippage_bps_by_asset_class({
        "EQUITY": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0],
        "COMMODITY": [5.0],
    })
    assert result["EQUITY"]["n_observations"] == 10
    assert result["EQUITY"]["p50"] == pytest.approx(50.0, abs=15.0)
    assert result["EQUITY"]["p90"] > result["EQUITY"]["p50"]
    assert result["COMMODITY"]["p50"] == 5.0
    assert result["COMMODITY"]["p90"] == 5.0


def test_calibrate_skips_asset_class_without_observations():
    result = br.calibrate_slippage_bps_by_asset_class({"EQUITY": [], "COMMODITY": [1.0]})
    assert "EQUITY" not in result
    assert "COMMODITY" in result


def test_calibrate_ignores_none_values():
    result = br.calibrate_slippage_bps_by_asset_class({"EQUITY": [10.0, None, 20.0]})
    assert result["EQUITY"]["n_observations"] == 2


# --- backtest_runner.merge_calibrated_slippage_into_config -------------------------------------------

def test_merge_fills_in_calibrated_p90_for_zero_static_config():
    merged = br.merge_calibrated_slippage_into_config(
        {"EQUITY": 0.0, "COMMODITY": 0.0},
        {"EQUITY": {"p50": 25.0, "p90": 60.0, "n_observations": 100}},
    )
    assert merged["EQUITY"] == 60.0
    assert merged["COMMODITY"] == 0.0  # keine Kalibrierung fuer COMMODITY -> unveraendert


def test_merge_respects_explicit_nonzero_operator_config():
    """Ein Operator, der EXPLIZIT einen realen Satz eingetragen hat, darf nie von der Kalibrierung
    ueberschrieben werden."""
    merged = br.merge_calibrated_slippage_into_config(
        {"EQUITY": 3.5},
        {"EQUITY": {"p50": 25.0, "p90": 60.0, "n_observations": 100}},
    )
    assert merged["EQUITY"] == 3.5


def test_merge_uses_p50_when_requested():
    merged = br.merge_calibrated_slippage_into_config(
        {}, {"EQUITY": {"p50": 25.0, "p90": 60.0, "n_observations": 100}}, percentile="p50")
    assert merged["EQUITY"] == 25.0


def test_merge_without_calibration_is_a_noop():
    merged = br.merge_calibrated_slippage_into_config({"EQUITY": 0.0}, {})
    assert merged == {"EQUITY": 0.0}


# --- sweep.write_calibrated_slippage_cache / read_calibrated_slippage_cache -------------------------

def test_write_and_read_calibrated_slippage_cache_roundtrip(tmp_path):
    calibration = {"EQUITY": {"p50": 25.0, "p90": 60.0, "n_observations": 100}}
    sweep.write_calibrated_slippage_cache(tmp_path, calibration, run_id="run_abc")
    data = sweep.read_calibrated_slippage_cache(tmp_path)
    assert data["run_id"] == "run_abc"
    assert data["slippage_bps_by_asset_class"] == calibration
    assert "calibrated_at_utc" in data


def test_read_calibrated_slippage_cache_missing_file_returns_empty_dict(tmp_path):
    assert sweep.read_calibrated_slippage_cache(tmp_path) == {}


# --- sweep.calibrate_and_write_slippage_cache: sammelt store-weit (nicht run_id-gescoped) ----------

class _FakeTrial:
    def __init__(self, slippage_bps):
        self.user_attrs = {"oos_stop_exit_slippage_bps_median": slippage_bps}


class _FakeStudy:
    def __init__(self, trials):
        self.trials = trials


def test_calibrate_and_write_slippage_cache_writes_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        br, "_resolve_asset_class_for_symbol", lambda symbol, **_kw: "EQUITY", raising=False)
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    studies = [_FakeStudy([_FakeTrial(10.0), _FakeTrial(20.0), _FakeTrial(-5.0)])]

    result = sweep.calibrate_and_write_slippage_cache(
        pairs, studies, work_dir=tmp_path, run_id="run_xyz")

    assert "EQUITY" in result
    assert result["EQUITY"]["n_observations"] == 3
    cached = sweep.read_calibrated_slippage_cache(tmp_path)
    assert cached["slippage_bps_by_asset_class"] == result


def test_calibrate_and_write_slippage_cache_treats_negative_slippage_as_zero_adverse(tmp_path, monkeypatch):
    """Issue #1029/#1178 — ein negativer Wert (guenstigerer Fill als der Stop) ist keine
    Kostenposition; die Kalibrierung darf sie nicht als negative 'Kosten' mitteln."""
    monkeypatch.setattr(
        br, "_resolve_asset_class_for_symbol", lambda symbol, **_kw: "EQUITY", raising=False)
    pairs = [("StratA", "TSLA.ETORO", "OK")]
    studies = [_FakeStudy([_FakeTrial(-50.0)])]

    result = sweep.calibrate_and_write_slippage_cache(
        pairs, studies, work_dir=tmp_path, run_id="run_xyz")
    assert result["EQUITY"]["p50"] == 0.0


# --- invariants.check_cost_stress_distinctness: verschaerftes Mindestdelta-Kriterium ----------------

def _study(strategy, symbol, *, capital_weighted, full_realism, total_trades,
           slippage_p50=None, n_ts_exits=None):
    return {
        "strategy": strategy, "symbol": symbol,
        "holdout_expectancy_capital_weighted": capital_weighted,
        "holdout_expectancy_cost_stress_full_realism": full_realism,
        "holdout_total_trades": total_trades,
        "slippage_p50_bps_calibrated": slippage_p50,
        "oos_n_trailing_stop_losses": n_ts_exits,
    }


def test_passes_when_delta_meets_the_calibrated_minimum():
    # min_expected = 0.5 * (30/10000) * (50/100) = 0.00075; actual_delta = 0.001 (kapital - full_realism)
    result = inv.check_cost_stress_distinctness([
        _study("A", "X.ETORO", capital_weighted=0.02, full_realism=0.019, total_trades=100,
               slippage_p50=30.0, n_ts_exits=50),
    ])
    assert result.passed is True


def test_fails_when_delta_is_smaller_than_the_calibrated_minimum():
    """Reproduziert eine 'technisch aktive, aber oekonomisch bedeutungslose' full_realism-Stufe:
    bit-different von der Basis, aber weit unter dem erwarteten Kalibrierungs-Effekt."""
    result = inv.check_cost_stress_distinctness([
        _study("A", "X.ETORO", capital_weighted=0.02, full_realism=0.019999, total_trades=100,
               slippage_p50=30.0, n_ts_exits=50),
    ])
    assert result.passed is False
    assert "A/X.ETORO" in result.provenance["delta_offenders"]


def test_skips_delta_criterion_without_calibration():
    """Ohne Kalibrierung (frischer Store) bleibt nur das bestehende Bit-Identitaets-Kriterium
    aktiv -- keine erratene Mindestzahl."""
    result = inv.check_cost_stress_distinctness([
        _study("A", "X.ETORO", capital_weighted=0.02, full_realism=0.02, total_trades=100),
    ])
    # Bit-identisch, aber nur 1 Study (Anteil 1.0 > 0.9) -> FAILt ueber das BESTEHENDE Kriterium,
    # nicht ueber das neue Delta-Kriterium (das mangels Kalibrierung nicht greift).
    assert result.passed is False
    assert result.provenance.get("delta_offenders") is None
