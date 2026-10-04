"""Hilfsprogramm für ``test_issue_1359_disaster_stop.py`` (kein Test): fährt EIN Gap-/Drift-Szenario
durch die echte NautilusTrader-``BacktestEngine`` und schreibt das Ergebnis als JSON.

Aufruf: ``python -m automation.tests._disaster_stop_engine_harness '<{"steps": [...]}>' <out.json>``.
Flache 100,00 bis ~3,3 h (Entry beim ersten Stundenschluss), danach die Preisfolge ``steps``."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def run(steps: list[float]) -> dict:
    from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.model.currencies import USD
    from nautilus_trader.model.data import BarType, QuoteTick
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, TimeInForce
    from nautilus_trader.model.identifiers import InstrumentId, Venue
    from nautilus_trader.model.objects import Money, Price, Quantity

    from automation.backtest_runner import _build_order_exit_meta, create_mock_instrument
    from automation.strategies.hourly_strategy_base import HourlyStrategyBase, HourlyStrategyConfig

    sym = "GAP.ETORO"
    inst_id = InstrumentId.from_str(sym)

    class _Cfg(HourlyStrategyConfig, kw_only=True, frozen=True):
        pass

    class _Probe(HourlyStrategyBase):
        def __init__(self, config):
            super().__init__(config)
            self.instrument_id = inst_id
            self.bar_type = BarType.from_str(config.bar_type)
            self.entered = False

        def on_start(self):
            super().on_start()
            self.subscribe_bars(self.bar_type)

        def on_bar(self, bar):
            if self._check_exits_and_update(bar):
                return
            if not self.entered:
                self.entered = True
                instr = self.cache.instrument(self.instrument_id)
                self.submit_order(self.order_factory.market(
                    instrument_id=self.instrument_id, order_side=OrderSide.BUY,
                    quantity=instr.make_qty(10), time_in_force=TimeInForce.GTC,
                    tags=self._entry_order_tags(bar)))

    engine = BacktestEngine(config=BacktestEngineConfig(trader_id="BT-GAP-001"))
    engine.add_venue(venue=Venue("ETORO"), oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                     base_currency=USD, starting_balances=[Money(100_000, USD)])
    engine.add_instrument(create_mock_instrument(sym, 2, size_precision=2))

    def tick(ts_min: int, mid: float) -> QuoteTick:
        ts = (1_700_000_000 + ts_min * 60) * 1_000_000_000
        return QuoteTick(inst_id, Price(mid - 0.01, 2), Price(mid + 0.01, 2),
                         Quantity(1000, 2), Quantity(1000, 2), ts, ts)

    ticks = [tick(m, 100.0) for m in range(0, 200, 10)]
    for i, px in enumerate(steps):
        ticks.append(tick(200 + 10 * i, px))
    ticks.append(tick(200 + 10 * len(steps) + 10, steps[-1]))
    engine.add_data(ticks)

    cfg = _Cfg(instrument_id=sym, bar_type=f"{sym}-1-HOUR-MID-INTERNAL",
               disaster_stop_mode="simulated_order", atr_trailing_multiplier=50.0, max_bars_in_trade=7)
    engine.add_strategy(_Probe(cfg))
    engine.run()
    try:
        meta = _build_order_exit_meta(engine)
        fills = engine.trader.generate_order_fills_report().reset_index()
        stops = []
        for o in engine.cache.orders():
            if "EXIT_REASON:DISASTER_STOP" not in (o.tags or []):
                continue
            cid = str(o.client_order_id)
            rows = fills[fills["client_order_id"] == cid]
            stops.append({
                "reduce_only": bool(o.is_reduce_only),
                "is_open": bool(o.is_open),
                "trigger_price": float(o.trigger_price),
                "exit_reason_meta": (meta.get(cid) or {}).get("exit_reason"),
                "fill_px": float(rows.iloc[0]["avg_px"]) if len(rows) else None,
            })
        return {"stops": stops, "positions_open": len(engine.cache.positions_open())}
    finally:
        engine.dispose()


if __name__ == "__main__":
    args = json.loads(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(run(args["steps"])), encoding="utf-8")
