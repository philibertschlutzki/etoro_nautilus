"""Issue #998/#1150 (Katalog #1170) — ``_atr_floor_bps_by_symbol`` liefert stumm ``{}``;
``atr_floor_bps_derived`` null in 28/28.

Symptom: ``report._atr_floor_bps_by_symbol`` fing JEDEN Fehler je Symbol (``except Exception:
continue``) spurlos ab — ein leeres Ergebnis-Dict war dadurch NICHT von "der Floor bindet nirgends"
(die tatsaechliche #1096-Abnahme) unterscheidbar, sondern bedeutete "der Floor ist unbekannt".

Fix: beide Resolver (``_atr_floor_bps_by_symbol``, ``_round_trip_cost_bps_by_symbol``) geben
zusaetzlich ``resolution_errors: dict[symbol, str]`` zurueck; der breite ``except Exception`` ist
auf die konkret erwarteten Fehlerklassen (``ValueError`` — deckt auch
``InstrumentMetadataIncompleteError`` ab, ``OSError``, ``KeyError``, ``TypeError``) eingegrenzt.
Neue Invariante ``check_cost_basis_resolution`` (severity ``high``) failt, wenn fuer ein Symbol mit
>= 1 Study WEDER der ATR-Floor NOCH c_rt aufloesbar sind.
"""
import unittest.mock as mock

from automation.optimizer import invariants as inv
from automation.optimizer import report as rpt


# --- resolvers return (values, errors) tuples, with narrowed exception handling -------------------

def test_atr_floor_by_symbol_returns_tuple_and_captures_value_error():
    def _resolve_asset_class(symbol, **kw):
        raise ValueError(f"REJECT_INSTRUMENT_METADATA_INCOMPLETE: {symbol}")

    fake_module = mock.MagicMock()
    fake_module.resolve_atr_floor_bps = mock.MagicMock(return_value=2.0)
    fake_module._resolve_asset_class_for_symbol = _resolve_asset_class

    with mock.patch.dict("sys.modules", {"automation.backtest_runner": fake_module}):
        with mock.patch.object(rpt, "_load_json", return_value={"atr_floor_bps_by_asset_class": {"EQUITY": 3.0}}):
            resolved, errors = rpt._atr_floor_bps_by_symbol(["BAD.ETORO"])
    assert resolved == {}
    assert "BAD.ETORO" in errors
    assert "ValueError" in errors["BAD.ETORO"]


def test_atr_floor_by_symbol_resolves_normally_without_errors():
    fake_module = mock.MagicMock()
    fake_module.resolve_atr_floor_bps = mock.MagicMock(return_value=2.5)
    fake_module._resolve_asset_class_for_symbol = mock.MagicMock(return_value="EQUITY")

    with mock.patch.dict("sys.modules", {"automation.backtest_runner": fake_module}):
        with mock.patch.object(rpt, "_load_json", return_value={}):
            resolved, errors = rpt._atr_floor_bps_by_symbol(["GOOD.ETORO"])
    assert resolved == {"GOOD.ETORO": 2.5}
    assert errors == {}


def test_atr_floor_by_symbol_propagates_unexpected_error_class():
    """Ein Programmfehler (nicht in der erwarteten Fehlerklassen-Menge) propagiert, statt als
    'nicht aufloesbar' missgedeutet zu werden (Fix Punkt 3)."""
    def _boom(symbol, **kw):
        raise AttributeError("something is broken")

    fake_module = mock.MagicMock()
    fake_module.resolve_atr_floor_bps = mock.MagicMock(return_value=2.0)
    fake_module._resolve_asset_class_for_symbol = _boom

    with mock.patch.dict("sys.modules", {"automation.backtest_runner": fake_module}):
        with mock.patch.object(rpt, "_load_json", return_value={"atr_floor_bps_by_asset_class": {"EQUITY": 3.0}}):
            try:
                rpt._atr_floor_bps_by_symbol(["X.ETORO"])
                assert False, "expected AttributeError to propagate"
            except AttributeError:
                pass


def test_round_trip_cost_by_symbol_returns_tuple_and_captures_value_error():
    def _boom(symbol):
        raise ValueError("REJECT_INSTRUMENT_METADATA_INCOMPLETE: unresolved asset class")

    fake_module = mock.MagicMock()
    fake_module._read_default_round_trip_cost_bps = _boom
    with mock.patch.dict("sys.modules", {"automation.backtest_runner": fake_module}):
        resolved, errors = rpt._round_trip_cost_bps_by_symbol(["BAD.ETORO"])
    assert resolved == {}
    assert "BAD.ETORO" in errors


# --- invariants.check_cost_basis_resolution --------------------------------------------------------

def test_cost_basis_resolution_fails_when_a_symbol_has_neither_basis():
    result = inv.check_cost_basis_resolution(
        [{"symbol": "X.ETORO"}, {"symbol": "Y.ETORO"}],
        atr_floor_bps_by_symbol={"X.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={},
        resolution_errors={"Y.ETORO": {"atr_floor": "ValueError: boom", "round_trip_cost": "ValueError: boom"}},
    )
    assert result.passed is False
    assert result.severity == "high"
    assert "Y.ETORO" in result.actual
    assert "X.ETORO" not in result.actual


def test_cost_basis_resolution_passes_when_at_least_one_basis_resolved_per_symbol():
    result = inv.check_cost_basis_resolution(
        [{"symbol": "X.ETORO"}, {"symbol": "Y.ETORO"}],
        atr_floor_bps_by_symbol={"X.ETORO": 2.0},
        round_trip_cost_bps_by_symbol={"Y.ETORO": 3.0},
    )
    assert result.passed is True


def test_cost_basis_resolution_no_studies_is_vacuous_pass():
    result = inv.check_cost_basis_resolution(
        [], atr_floor_bps_by_symbol={}, round_trip_cost_bps_by_symbol={})
    assert result.passed is True


def test_cost_basis_resolution_carries_the_concrete_error_message():
    result = inv.check_cost_basis_resolution(
        [{"symbol": "Y.ETORO"}],
        atr_floor_bps_by_symbol={},
        round_trip_cost_bps_by_symbol={},
        resolution_errors={"Y.ETORO": {"atr_floor": "ValueError: REJECT_INSTRUMENT_METADATA_INCOMPLETE"}},
    )
    assert result.actual["Y.ETORO"]["errors"]["atr_floor"] == "ValueError: REJECT_INSTRUMENT_METADATA_INCOMPLETE"
