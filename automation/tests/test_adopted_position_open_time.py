"""Übernommene eToro-Positionen tragen den echten Einstiegszeitpunkt (Zeit-Exit trotz Neustarts)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from nautilus_trader.model.identifiers import AccountId

from automation.adapters import etoro_execution as ex
from automation.backtest_runner import create_mock_instrument

OPEN = "2026-10-11T08:00:00.463Z"
OPEN_NS = 1_791_705_600_463_000_000      # 2026-10-11T08:00:00.463Z


def _pnl(*items):
    return {"clientPortfolio": {"positions": list(items), "credit": 9000.0}}


def _pos(pid, open_time, units=0.002352, rate=84041.4):
    return {"positionID": pid, "instrumentID": 100000, "isBuy": True, "units": units, "openRate": rate,
            "openDateTime": open_time}


def test_open_time_parsing():
    assert ex._open_time_ns(OPEN) == OPEN_NS
    assert ex._open_time_ns("2026-10-11T08:00:00.463") == OPEN_NS      # ohne Zeitzone = UTC
    assert ex._open_time_ns(None) is None and ex._open_time_ns("kaputt") is None


def test_net_position_nimmt_den_fruehesten_einstieg():
    net = ex.net_open_positions(_pnl(_pos(1, "2026-10-12T10:00:00Z"), _pos(2, OPEN)))
    assert net["100000"]["opened_ns"] == OPEN_NS and net["100000"]["position_ids"] == ["1", "2"]


def test_ohne_lesbare_zeit_bleibt_der_eintrag_ohne_opened_ns():
    net = ex.net_open_positions(_pnl({"positionID": 1, "instrumentID": 100000, "isBuy": True, "units": 1, "openRate": 2}))
    assert "opened_ns" not in net["100000"]


def _report(pnl, now_ns):
    class _Resp:
        status = 200

        async def json(self):
            return pnl

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    btc = create_mock_instrument("BTC.ETORO", price_precision=2, size_precision=8)
    fake = SimpleNamespace(
        _dry_run=False, _session=SimpleNamespace(get=lambda *a, **k: _Resp()), _pnl_base="https://x/pnl",
        _make_headers=lambda: {}, _clock=SimpleNamespace(timestamp_ns=lambda: now_ns),
        _cache=SimpleNamespace(instrument=lambda iid: btc if str(iid) == "BTC.ETORO" else None),
        _log=MagicMock(), account_id=AccountId("ETORO-REAL-001"))
    reports = asyncio.run(ex.EToroExecutionClient.generate_position_status_reports(fake))
    assert len(reports) == 1
    return reports[0]


def test_report_traegt_den_echten_einstieg_statt_jetzt():
    now_ns = OPEN_NS + 10 * 3_600 * 1_000_000_000
    rep = _report(_pnl(_pos(7, OPEN)), now_ns)
    assert rep.ts_last == OPEN_NS and rep.ts_init == now_ns


def test_report_ohne_zeit_oder_mit_zukunftszeit_bleibt_bei_jetzt():
    now_ns = OPEN_NS - 1
    assert _report(_pnl(_pos(7, OPEN)), now_ns).ts_last == now_ns          # Einstieg "in der Zukunft": nie später als jetzt
    item = {"positionID": 8, "instrumentID": 100000, "isBuy": True, "units": 0.002, "openRate": 84000.0}
    assert _report(_pnl(item), 123).ts_last == 123


def test_reconcile_nutzt_bei_24h_instrumenten_die_wanduhr_wenn_der_bar_cache_luecken_hat():
    from automation.strategies.rsi2_reversion import Rsi2ReversionConfig, Rsi2ReversionStrategy
    from nautilus_trader.model.enums import PositionSide  # noqa: F401
    from unittest.mock import PropertyMock, patch

    strategy = Rsi2ReversionStrategy(config=Rsi2ReversionConfig(
        instrument_id="BTC.ETORO", bar_type="BTC.ETORO-1-HOUR-MID-INTERNAL"))
    hour = 3_600 * 1_000_000_000
    pos = SimpleNamespace(ts_opened=1_000 * hour, id="p", quantity=1)
    cache = SimpleNamespace(positions_open=lambda instrument_id=None: [pos])
    clock = SimpleNamespace(timestamp_ns=lambda: 1_020 * hour)           # 20 Stunden seit Einstieg
    strategy._max_bars_in_trade = 7
    strategy._count_bars_since = lambda ns: 3                              # Cache sieht nur 3 Bars (Lücke)
    strategy._close_position_base = MagicMock()
    with patch.object(type(strategy), "cache", new_callable=PropertyMock, return_value=cache), \
         patch.object(type(strategy), "clock", new_callable=PropertyMock, return_value=clock):
        strategy._reconcile_after_reconnect()
    strategy._close_position_base.assert_called_once()                     # 20 h >= 7 Bars: Zeit-Exit sofort
    assert strategy._bars_in_position_rehydrated == 7
