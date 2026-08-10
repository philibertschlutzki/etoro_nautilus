"""Issue #999 (P0, HEADLINE, Katalog #993-#1002) — Kapitalallokation, Circuit-Breaker und
Live-Exposure-Budget an der Live-Grenze.

Symptom 1 — der Vor-#999-Allocator (``account_balance / pending_signals``, ``pending_signals`` =
Zahl der Universumssymbole OHNE offene Position) wies der LETZTEN eroeffneten Position das GESAMTE
verfuegbare Kapital zu (``a_n = B_{n-1}/1``): die Allokationsfolge war monoton STEIGEND in der Zahl
bereits eingegangener Risiken — das exakte Gegenteil einer Risikobudgetierung.

Symptom 2 — der Bot wurde nach dem Start als detached Subprozess NICHT mehr auf seinen
Equity-Verlauf ueberwacht; ``max_drawdown <= 0.3`` war ein reines Backtest-Gate ohne Live-Aequivalent.

Dieser Test deckt ab:
  (a) die neue Budget-Formel ist monoton FALLEND in der Zahl offener Positionen und respektiert
      max_total_exposure_fraction/max_symbol_exposure_fraction,
  (b) 142 von 143 offenen Positionen fuehren NICHT zu ``balance/1`` fuer das 143. Signal,
  (c) der Drawdown-Daempfer ψ(DD) skaliert neue Allokationen kontinuierlich herunter,
  (d) ein getrippter Circuit-Breaker setzt jede neue Allokation auf 0.0,
  (e) Ausloeser A (Drawdown) und B (Verteilung, erst ab n_live >= n_min) — automation/live_risk.py,
  (f) ``check_live_exposure_budget`` (invariants.py),
  (g) ``LiveCircuitBreakerWatchdog`` flattet alle Strategien und stoppt den Node bei einem Trip.
"""
import pytest
from nautilus_trader.model.identifiers import InstrumentId

from automation.live_risk import (
    CircuitBreakerDecision,
    LiveCircuitBreakerWatchdog,
    drawdown_damper,
    evaluate_circuit_breaker,
)
from automation.momentum_ls_allocator import MomentumLSAllocator
from automation.optimizer.invariants import check_live_exposure_budget


# --- Fakes (kein NautilusTrader-Runtime/Broker-Zugriff noetig) ---------------------------------

class _FakePosition:
    def __init__(self, quantity: float, avg_px_open: float):
        self.quantity = quantity
        self.avg_px_open = avg_px_open


class _FakeCache:
    def __init__(self, open_positions: dict[str, list] | None = None):
        self._open = open_positions or {}

    def positions_open(self, instrument_id=None):
        return self._open.get(str(instrument_id), [])

    def open(self, symbol: str, quantity: float, avg_px_open: float) -> None:
        self._open.setdefault(symbol, []).append(_FakePosition(quantity, avg_px_open))


def _allocator(n_symbols: int = 10, **kwargs) -> MomentumLSAllocator:
    universe = [f"SYM{i}.ETORO" for i in range(n_symbols)]
    return MomentumLSAllocator(universe, **kwargs)


# --- (a)/(b) Budget-Formel ------------------------------------------------------------------

def test_first_allocation_is_capped_at_symbol_fraction_not_full_balance():
    alloc = _allocator(max_total_exposure_fraction=0.60, max_symbol_exposure_fraction=0.10)
    cache = _FakeCache()
    a = alloc.get_allocation(InstrumentId.from_str("SYM0.ETORO"), cache, 10_000.0)
    assert a == pytest.approx(1_000.0)


def test_allocation_sequence_is_monotonically_non_increasing_and_budget_respecting():
    alloc = _allocator(n_symbols=10, max_total_exposure_fraction=0.60, max_symbol_exposure_fraction=0.10)
    cache = _FakeCache()
    balance = 10_000.0
    sequence = []
    for i in range(7):
        symbol = f"SYM{i}.ETORO"
        a = alloc.get_allocation(InstrumentId.from_str(symbol), cache, balance)
        sequence.append(a)
        # Position with entry notional == allocation (qty=1 simplifies the arithmetic).
        cache.open(symbol, 1.0, a if a > 0 else 0.0)
    assert all(sequence[i] >= sequence[i + 1] - 1e-9 for i in range(len(sequence) - 1)), sequence
    # Six 10%-slots already exhaust the 60% total budget -> the seventh signal gets nothing.
    assert sequence[6] == 0.0


def test_the_old_ruin_path_cannot_reoccur_142_of_143_symbols_already_open():
    """Regression: unter der ALTEN Formel haette das 143. Signal ``balance/1`` (das GESAMTE
    Restkapital) erhalten. Unter #999 ist jede Allokation numerisch <= max_symbol_exposure_fraction
    * balance, egal wie viele Positionen bereits offen sind."""
    n = 143
    alloc = _allocator(n_symbols=n, max_total_exposure_fraction=0.60, max_symbol_exposure_fraction=0.10)
    open_positions = {f"SYM{i}.ETORO": [_FakePosition(1.0, 100.0)] for i in range(n - 1)}
    cache = _FakeCache(open_positions)
    balance = 10_000.0
    a = alloc.get_allocation(InstrumentId.from_str(f"SYM{n - 1}.ETORO"), cache, balance)
    assert a <= 0.10 * balance + 1e-9
    assert a != balance  # the old bug: balance / 1 pending signal


def test_total_exposure_never_exceeds_budget_across_a_monte_carlo_style_opening_sequence():
    import random
    random.seed(1234)
    n = 60
    alloc = _allocator(n_symbols=n, max_total_exposure_fraction=0.60, max_symbol_exposure_fraction=0.10)
    cache = _FakeCache()
    balance = 25_000.0
    symbols = [f"SYM{i}.ETORO" for i in range(n)]
    random.shuffle(symbols)
    total_open_notional = 0.0
    for symbol in symbols:
        a = alloc.get_allocation(InstrumentId.from_str(symbol), cache, balance)
        if a > 0:
            cache.open(symbol, 1.0, a)
            total_open_notional += a
        assert total_open_notional / balance <= 0.60 + 1e-9


def test_when_open_position_notional_is_unresolvable_falls_back_to_conservative_slot_estimate():
    class _WeirdPosition:
        pass  # no .quantity/.avg_px_open -> _position_notional returns None

    alloc = _allocator(n_symbols=10, max_total_exposure_fraction=0.60, max_symbol_exposure_fraction=0.10)
    cache = _FakeCache({f"SYM{i}.ETORO": [_WeirdPosition()] for i in range(5)})
    a = alloc.get_allocation(InstrumentId.from_str("SYM9.ETORO"), cache, 10_000.0)
    # 5 unresolvable positions -> conservative estimate treats each as a full symbol-cap slot
    # (5 * 0.10 = 0.50 of the 0.60 budget already "spent") -> remaining 0.10 slot still fits exactly.
    assert a == pytest.approx(1_000.0)


def test_zero_or_negative_account_balance_never_allocates():
    alloc = _allocator()
    cache = _FakeCache()
    assert alloc.get_allocation(InstrumentId.from_str("SYM0.ETORO"), cache, 0.0) == 0.0
    assert alloc.get_allocation(InstrumentId.from_str("SYM0.ETORO"), cache, -100.0) == 0.0


# --- (c) Drawdown-Daempfer -------------------------------------------------------------------

def test_drawdown_damper_scales_allocation_down_continuously():
    alloc = _allocator(max_total_exposure_fraction=0.60, max_symbol_exposure_fraction=0.10,
                        dd_halt_fraction=0.10, psi_min=0.2)
    cache = _FakeCache()
    a_no_dd = alloc.get_allocation(InstrumentId.from_str("SYM0.ETORO"), cache, 10_000.0)

    alloc.update_risk_state(current_drawdown=0.05)  # halfway to the 10% halt threshold
    a_half_dd = alloc.get_allocation(InstrumentId.from_str("SYM1.ETORO"), cache, 10_000.0)

    assert a_half_dd == pytest.approx(a_no_dd * 0.5)


def test_drawdown_damper_never_goes_below_psi_min():
    assert drawdown_damper(0.50, dd_halt_fraction=0.10, psi_min=0.2) == 0.2


# --- (d) getrippter Breaker blockiert JEDE neue Allokation --------------------------------------

def test_tripped_breaker_blocks_all_new_allocations_regardless_of_budget():
    alloc = _allocator()
    cache = _FakeCache()
    alloc.update_risk_state(tripped=True)
    a = alloc.get_allocation(InstrumentId.from_str("SYM0.ETORO"), cache, 10_000.0)
    assert a == 0.0


# --- (e) evaluate_circuit_breaker (Ausloeser A/B) -----------------------------------------------

def test_trigger_a_drawdown_trips_at_the_configured_halt_fraction():
    d = evaluate_circuit_breaker(9_000.0, 10_000.0, dd_halt_fraction=0.10)
    assert d.tripped is True
    assert d.trigger == "drawdown"


def test_trigger_a_does_not_trip_below_the_halt_fraction():
    d = evaluate_circuit_breaker(9_500.0, 10_000.0, dd_halt_fraction=0.10)
    assert d.tripped is False


def test_trigger_b_is_none_and_does_not_trip_below_n_min_periods():
    catastrophic_returns = [-0.05] * 10
    d = evaluate_circuit_breaker(
        10_000.0, 10_000.0, catastrophic_returns,
        backtest_mu=0.001, backtest_sigma=0.01, z_halt=2.5, n_min_periods=30)
    assert d.z_live is None
    assert d.tripped is False


def test_trigger_b_trips_once_n_min_periods_reached_with_significantly_worse_live_returns():
    bad_returns = [-0.02] * 30
    d = evaluate_circuit_breaker(
        10_000.0, 10_000.0, bad_returns,
        backtest_mu=0.001, backtest_sigma=0.01, z_halt=2.5, n_min_periods=30)
    assert d.tripped is True
    assert d.trigger == "distribution"


def test_both_triggers_simultaneously_reports_drawdown_first_deterministically():
    bad_returns = [-0.02] * 30
    d = evaluate_circuit_breaker(
        9_000.0, 10_000.0, bad_returns,
        dd_halt_fraction=0.10, backtest_mu=0.001, backtest_sigma=0.01, z_halt=2.5, n_min_periods=30)
    assert d.trigger == "drawdown"


def test_no_equity_peak_yields_no_drawdown_clause_but_does_not_crash():
    d = evaluate_circuit_breaker(10_000.0, None)
    assert d.dd_live is None
    assert d.tripped is False


# --- (f) check_live_exposure_budget ------------------------------------------------------------

def test_check_live_exposure_budget_passes_trivially_with_no_snapshots():
    result = check_live_exposure_budget([])
    assert result.passed is True


def test_check_live_exposure_budget_passes_within_budget():
    snapshots = [{"open_exposure_fraction": 0.10 * i} for i in range(6)]
    result = check_live_exposure_budget(snapshots, max_total_exposure_fraction=0.60)
    assert result.passed is True


def test_check_live_exposure_budget_fails_on_a_violating_snapshot():
    snapshots = [{"open_exposure_fraction": 0.30}, {"open_exposure_fraction": 0.75}]
    result = check_live_exposure_budget(snapshots, max_total_exposure_fraction=0.60)
    assert result.passed is False
    assert len(result.actual) == 1


def test_check_live_exposure_budget_tolerates_float_rounding_at_the_boundary():
    snapshots = [{"open_exposure_fraction": 0.6000000001}]
    result = check_live_exposure_budget(snapshots, max_total_exposure_fraction=0.60, tolerance=1e-9)
    assert result.passed is True


# --- (g) LiveCircuitBreakerWatchdog --------------------------------------------------------------

class _FakePortfolio:
    def __init__(self, equity_sequence):
        self._values = iter(equity_sequence)

    def equity(self, venue):
        return {"USD": next(self._values)}


class _FakeTrader:
    def __init__(self):
        self.strategy_ids = ["S1", "S2"]
        self.exited: list[str] = []

    def market_exit_strategy(self, strategy_id):
        self.exited.append(strategy_id)


class _FakeNode:
    def __init__(self, equity_sequence):
        self.portfolio = _FakePortfolio(equity_sequence)
        self.trader = _FakeTrader()
        self.stopped = False

    def stop(self):
        self.stopped = True
    # Deliberately no get_event_loop() — exercises the watchdog's direct-call fallback path.


def test_watchdog_flattens_all_strategies_and_stops_node_on_trip():
    node = _FakeNode([10_000.0, 9_800.0, 9_000.0])
    trips: list[CircuitBreakerDecision] = []
    updates: list[CircuitBreakerDecision] = []
    watchdog = LiveCircuitBreakerWatchdog(
        node, poll_interval_s=999.0, dd_halt_fraction=0.10,
        on_trip=lambda d: trips.append(d), on_update=lambda d: updates.append(d))
    watchdog.start()  # consumes the first equity reading (10_000.0) itself
    watchdog._tick()  # 9800 -> 2% DD, no trip
    watchdog._tick()  # 9000 -> 10% DD, trips
    watchdog.stop()

    assert watchdog.tripped_event.is_set()
    assert node.trader.exited == ["S1", "S2"]
    assert node.stopped is True
    assert len(trips) == 1
    assert len(updates) == 3  # on_update fires on every tick (incl. start()'s), tripped or not


def test_watchdog_does_not_trip_while_equity_stays_within_drawdown_bound():
    node = _FakeNode([10_000.0, 9_900.0, 9_950.0])
    watchdog = LiveCircuitBreakerWatchdog(node, poll_interval_s=999.0, dd_halt_fraction=0.10)
    watchdog.start()
    watchdog._tick()
    watchdog._tick()
    watchdog.stop()
    assert not watchdog.tripped_event.is_set()
    assert node.trader.exited == []
    assert node.stopped is False


def test_watchdog_equity_read_failure_is_swallowed_and_does_not_trip():
    class _ExplodingPortfolio:
        def equity(self, venue):
            raise RuntimeError("no data client connected yet")

    class _NodeWithBrokenPortfolio:
        def __init__(self):
            self.portfolio = _ExplodingPortfolio()
            self.trader = _FakeTrader()

    node = _NodeWithBrokenPortfolio()
    watchdog = LiveCircuitBreakerWatchdog(node, poll_interval_s=999.0)
    watchdog.start()
    watchdog._tick()
    watchdog.stop()
    assert not watchdog.tripped_event.is_set()
